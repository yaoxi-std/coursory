from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx
from opening_links import DetailLink, collect_detail_links
from opening_parser import (
  parse_course_detail,
  parse_experiment_detail,
  parse_form_fields,
  parse_page_info,
  parse_sections,
  parse_teacher_detail,
)
from parquet_store import write_parquet_table
from thu_common import (
  DATA_PROCESSED_ROOT,
  DATA_RAW_ROOT,
  OPENING_INFO_ENDPOINT,
  SOURCE,
  STORAGE_STATE_PATH,
  CrawlerError,
  decode_response_body,
  is_probably_login_page,
  opening_info_url_for_semester,
  semester_to_xnxq,
  timestamp_slug,
  utc_now,
  validate_semester,
  write_json,
)


@dataclass(frozen=True)
class FetchResult:
  url: str
  status_code: int
  content_type: str
  body: str


class ProgressBar:
  def __init__(
    self,
    *,
    label: str,
    total: int,
    enabled: bool,
    stream=sys.stderr,
    width: int = 28,
  ) -> None:
    self.label = label
    self.total = max(total, 1)
    self.enabled = enabled
    self.stream = stream
    self.width = width
    self.current = 0
    self.last_line = ''
    self.is_tty = bool(getattr(stream, 'isatty', lambda: False)())

  def update(self, current: int, *, suffix: str = '') -> None:
    if not self.enabled:
      return
    self.current = min(max(current, 0), self.total)
    line = self._line(suffix=suffix)
    if self.is_tty:
      self.stream.write('\r' + line)
      self.stream.flush()
      self.last_line = line
    elif line != self.last_line:
      self.stream.write(line + '\n')
      self.stream.flush()
      self.last_line = line

  def advance(self, *, suffix: str = '') -> None:
    self.update(self.current + 1, suffix=suffix)

  def finish(self, *, suffix: str = 'done') -> None:
    if not self.enabled:
      return
    self.update(self.total, suffix=suffix)
    if self.is_tty:
      self.stream.write('\n')
      self.stream.flush()

  def _line(self, *, suffix: str) -> str:
    ratio = self.current / self.total
    filled = min(self.width, int(self.width * ratio))
    bar = '#' * filled + '-' * (self.width - filled)
    percent = int(ratio * 100)
    suffix_text = f' {suffix}' if suffix else ''
    return (
      f'{self.label} [{bar}] {self.current}/{self.total} {percent:3d}%{suffix_text}'
    )


def crawl(args: argparse.Namespace) -> int:
  semester = validate_semester(args.semester)
  xnxq = semester_to_xnxq(semester)
  entry_url = opening_info_url_for_semester(semester)
  run_id = args.run_id or timestamp_slug()
  raw_dir = DATA_RAW_ROOT / semester / run_id
  processed_dir = DATA_PROCESSED_ROOT / semester / run_id

  if args.dry_run:
    print('Dry run only; no network request will be made.')
    print(f'Session path: {STORAGE_STATE_PATH}')
    print(f'Opening URL: {entry_url}')
    print(f'Raw audit cache: {raw_dir}')
    print(f'Processed Parquet: {processed_dir}')
    print(f'Max pages: {args.max_pages or "all"}')
    print(f'Page concurrency: {args.page_concurrency}')
    print(f'Details: {"disabled" if args.skip_details else "all unique links"}')
    print(
      f'Detail limit: {args.detail_limit if args.detail_limit is not None else "all"}'
    )
    print(f'Detail concurrency: {args.detail_concurrency}')
    return 0

  if not STORAGE_STATE_PATH.exists():
    raise CrawlerError(
      f'No saved session exists at {STORAGE_STATE_PATH}. '
      'Run `uv run python crawlers/thu-courses/auth.py login` first.'
    )
  if args.page_concurrency < 1 or args.detail_concurrency < 1:
    raise CrawlerError('Concurrency values must be positive integers.')

  raw_dir.mkdir(parents=True, exist_ok=True)
  processed_dir.mkdir(parents=True, exist_ok=True)

  started_at = utc_now()
  sections: list[dict[str, object]] = []
  detail_links: list[DetailLink] = []
  course_details: list[dict[str, object]] = []
  teacher_details: list[dict[str, object]] = []
  experiment_details: list[dict[str, object]] = []
  requests: list[dict[str, object]] = []
  errors: list[dict[str, object]] = []

  first = fetch_get(entry_url, timeout=args.timeout)
  ensure_authenticated(first.url, first.body)
  first_info = parse_page_info(first.body)
  total_pages = first_info.total_pages or 1
  max_pages = min(total_pages, args.max_pages) if args.max_pages else total_pages
  form_fields = parse_form_fields(first.body)
  if not form_fields.get('token'):
    raise CrawlerError('Opening-info page did not contain the expected hidden token.')

  page_progress = ProgressBar(
    label='Opening pages',
    total=max_pages,
    enabled=not args.no_progress,
  )
  first_sections, first_links, first_request = record_opening_page(
    result=first,
    raw_dir=raw_dir,
    run_id=run_id,
    semester=semester,
    xnxq=xnxq,
    page_number=1,
    method='GET',
  )
  sections.extend(first_sections)
  detail_links.extend(first_links)
  requests.append(first_request)
  page_progress.update(
    1,
    suffix=f'sections={len(sections)} detail-links={len(detail_links)}',
  )

  page_completed = 1
  page_errors: list[dict[str, object]] = []
  page_numbers = range(2, max_pages + 1)
  with ThreadPoolExecutor(max_workers=args.page_concurrency) as executor:
    futures = {
      executor.submit(
        fetch_opening_page,
        page_number,
        form_fields,
        timeout=args.timeout,
        delay=args.delay,
      ): page_number
      for page_number in page_numbers
    }
    for future in as_completed(futures):
      page_number = futures[future]
      try:
        result = future.result()
        ensure_authenticated(result.url, result.body)
        page_sections, page_links, page_request = record_opening_page(
          result=result,
          raw_dir=raw_dir,
          run_id=run_id,
          semester=semester,
          xnxq=xnxq,
          page_number=page_number,
          method='POST',
        )
        sections.extend(page_sections)
        detail_links.extend(page_links)
        requests.append(page_request)
      except Exception as exc:
        page_errors.append({'page': page_number, 'error': str(exc)})
        if not args.continue_on_error:
          raise
      finally:
        page_completed += 1
        page_progress.update(
          page_completed,
          suffix=f'sections={len(sections)} detail-links={len(detail_links)}',
        )
  page_progress.finish(suffix=f'sections={len(sections)} errors={len(page_errors)}')
  errors.extend(page_errors)

  detail_links = filter_detail_links(detail_links)
  detail_links = sort_detail_links(detail_links)
  if args.detail_limit is not None:
    detail_links = detail_links[: args.detail_limit]
  if not args.skip_details:
    detail_progress = ProgressBar(
      label='Detail pages',
      total=len(detail_links),
      enabled=not args.no_progress,
    )
    detail_completed = 0
    with ThreadPoolExecutor(max_workers=args.detail_concurrency) as executor:
      futures = {
        executor.submit(
          fetch_detail_page,
          link,
          timeout=args.timeout,
          delay=args.delay,
        ): (index, link)
        for index, link in enumerate(detail_links, start=1)
      }
      for future in as_completed(futures):
        index, link = futures[future]
        try:
          result = future.result()
          ensure_authenticated(result.url, result.body)
          html_path = write_raw_html(
            raw_dir=raw_dir,
            run_id=run_id,
            prefix=f'detail_{link.kind}',
            index=index,
            method='GET',
            result=result,
            meta={
              'source': SOURCE,
              'semester': semester,
              'xnxq': xnxq,
              'fetched_at': utc_now().isoformat(),
              'detail_kind': link.kind,
              'detail_text': link.text,
              'row_index': link.row_index,
              'course_id': link.course_id,
              'section_id': link.section_id,
              'notes': f'Raw {link.kind} detail page retained as ignored audit cache.',
            },
          )
          parse_detail_row(
            link=link,
            result=result,
            course_details=course_details,
            teacher_details=teacher_details,
            experiment_details=experiment_details,
          )
          requests.append(
            {
              'kind': f'detail_{link.kind}',
              'detail_text': link.text,
              'url': result.url,
              'method': 'GET',
              'status_code': result.status_code,
              'content_type': result.content_type,
              'raw_path': str(html_path),
            }
          )
          detail_suffix = f'{link.kind}: {link.text}'
        except Exception as exc:
          errors.append({'url': link.url, 'kind': link.kind, 'error': str(exc)})
          detail_suffix = f'error {link.kind}: {link.text}'
          if not args.continue_on_error:
            raise
        finally:
          detail_completed += 1
          detail_progress.update(detail_completed, suffix=detail_suffix)
    detail_progress.finish(
      suffix=(
        f'course={len(course_details)} teacher={len(teacher_details)} '
        f'experiment={len(experiment_details)} errors={len(errors)}'
      )
    )

  write_outputs(
    processed_dir=processed_dir,
    sections=sections,
    course_details=course_details,
    teacher_details=teacher_details,
    experiment_details=experiment_details,
  )
  write_json(
    processed_dir / 'manifest.json',
    {
      'source': SOURCE,
      'semester': semester,
      'xnxq': xnxq,
      'run_id': run_id,
      'started_at': started_at.isoformat(),
      'finished_at': utc_now().isoformat(),
      'entry_url': entry_url,
      'raw_dir': str(raw_dir),
      'processed_dir': str(processed_dir),
      'total_pages_seen': total_pages,
      'pages_crawled': max_pages,
      'page_concurrency': args.page_concurrency,
      'detail_concurrency': args.detail_concurrency,
      'sections': len(sections),
      'detail_links': len(detail_links),
      'course_details': len(course_details),
      'teacher_details': len(teacher_details),
      'experiment_details': len(experiment_details),
      'errors': errors,
      'outputs': {
        'sections': str(processed_dir / 'sections.parquet'),
        'course_details': str(processed_dir / 'course_details.parquet'),
        'teacher_details': str(processed_dir / 'teacher_details.parquet'),
        'experiment_details': str(processed_dir / 'experiment_details.parquet'),
      },
      'requests': requests,
    },
  )

  print(f'Wrote processed dataset: {processed_dir}')
  print(f'Wrote raw audit cache: {raw_dir}')
  print(f'Sections: {len(sections)}')
  print(
    f'Detail pages: {len(course_details) + len(teacher_details) + len(experiment_details)}'
  )
  if errors:
    print(f'Errors: {len(errors)}')
  return 0


def parse_detail_row(
  *,
  link: DetailLink,
  result: FetchResult,
  course_details: list[dict[str, object]],
  teacher_details: list[dict[str, object]],
  experiment_details: list[dict[str, object]],
) -> None:
  if link.kind == 'course':
    course_details.append(parse_course_detail(result.body, url=result.url))
  elif link.kind == 'teacher':
    teacher_details.append(parse_teacher_detail(result.body, url=result.url))
  elif link.kind == 'experiment':
    experiment_details.append(parse_experiment_detail(result.body, url=result.url))


def write_outputs(
  *,
  processed_dir: Path,
  sections: list[dict[str, object]],
  course_details: list[dict[str, object]],
  teacher_details: list[dict[str, object]],
  experiment_details: list[dict[str, object]],
) -> None:
  write_parquet_table(
    processed_dir / 'sections.parquet',
    'sections',
    sorted_sections(sections),
  )
  write_parquet_table(
    processed_dir / 'course_details.parquet',
    'course_details',
    sorted_rows(course_details),
  )
  write_parquet_table(
    processed_dir / 'teacher_details.parquet',
    'teacher_details',
    sorted_rows(teacher_details),
  )
  write_parquet_table(
    processed_dir / 'experiment_details.parquet',
    'experiment_details',
    sorted_rows(experiment_details),
  )


def record_opening_page(
  *,
  result: FetchResult,
  raw_dir: Path,
  run_id: str,
  semester: str,
  xnxq: str,
  page_number: int,
  method: str,
) -> tuple[list[dict[str, object]], list[DetailLink], dict[str, object]]:
  html_path = write_raw_html(
    raw_dir=raw_dir,
    run_id=run_id,
    prefix='opening_info',
    index=page_number,
    method=method,
    result=result,
    meta={
      'source': SOURCE,
      'semester': semester,
      'xnxq': xnxq,
      'fetched_at': utc_now().isoformat(),
      'page': page_number,
      'notes': 'Raw opening-info page retained as ignored audit cache.',
    },
  )
  info = parse_page_info(result.body)
  sections = parse_sections(
    result.body,
    base_url=result.url,
    semester=semester,
    xnxq=xnxq,
    page=page_number,
  )
  links = collect_detail_links(result.body, result.url)
  request: dict[str, object] = {
    'kind': 'opening_info',
    'page': page_number,
    'url': result.url,
    'method': method,
    'status_code': result.status_code,
    'content_type': result.content_type,
    'raw_path': str(html_path),
    **info.to_dict(),
  }
  return sections, links, request


def fetch_opening_page(
  page_number: int,
  form_fields: dict[str, str],
  *,
  timeout: int,
  delay: float,
) -> FetchResult:
  fields = dict(form_fields)
  fields['m'] = 'kkxxSearch'
  fields['page'] = str(page_number)
  result = fetch_post_form(OPENING_INFO_ENDPOINT, fields, timeout=timeout)
  sleep_after_request(delay)
  return result


def fetch_detail_page(link: DetailLink, *, timeout: int, delay: float) -> FetchResult:
  result = fetch_get(link.url, timeout=timeout)
  sleep_after_request(delay)
  return result


def fetch_get(url: str, *, timeout: int) -> FetchResult:
  with new_http_client(timeout=timeout) as client:
    response = client.get(url)
  content_type = response.headers.get('content-type', '')
  return FetchResult(
    url=str(response.url),
    status_code=response.status_code,
    content_type=content_type,
    body=decode_response_body(response.content, content_type),
  )


def fetch_post_form(url: str, fields: dict[str, str], *, timeout: int) -> FetchResult:
  with new_http_client(timeout=timeout) as client:
    response = client.post(url, data=fields)
  content_type = response.headers.get('content-type', '')
  return FetchResult(
    url=str(response.url),
    status_code=response.status_code,
    content_type=content_type,
    body=decode_response_body(response.content, content_type),
  )


def new_http_client(*, timeout: int) -> httpx.Client:
  return httpx.Client(
    cookies=load_storage_cookies(),
    follow_redirects=True,
    trust_env=False,
    timeout=httpx.Timeout(timeout),
    headers={
      'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari/537.36'
      ),
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    },
  )


def load_storage_cookies() -> httpx.Cookies:
  state = json.loads(STORAGE_STATE_PATH.read_text(encoding='utf-8'))
  cookies = httpx.Cookies()
  for cookie in state.get('cookies', []):
    name = cookie.get('name')
    value = cookie.get('value')
    domain = cookie.get('domain')
    if not name or value is None or not domain:
      continue
    cookies.set(
      str(name),
      str(value),
      domain=str(domain),
      path=str(cookie.get('path') or '/'),
    )
  return cookies


def sleep_after_request(delay: float) -> None:
  if delay > 0:
    time.sleep(delay)


def ensure_authenticated(url: str, body: str) -> None:
  if is_probably_login_page(url, body):
    raise CrawlerError(
      'Saved session was redirected to the Tsinghua login page. '
      'Re-run `uv run python crawlers/thu-courses/auth.py login`.'
    )


def write_raw_html(
  *,
  raw_dir: Path,
  run_id: str,
  prefix: str,
  index: int,
  method: str,
  result: FetchResult,
  meta: dict[str, object],
) -> Path:
  html_path = raw_dir / f'{run_id}_{prefix}_{index:03d}.html'
  html_path.write_text(result.body, encoding='utf-8')
  write_json(
    html_path.with_suffix('.meta.json'),
    {
      **meta,
      'url': result.url,
      'method': method,
      'status_code': result.status_code,
      'content_type': result.content_type,
    },
  )
  return html_path


def sorted_sections(sections: list[dict[str, object]]) -> list[dict[str, object]]:
  return sorted(
    sections,
    key=lambda row: (
      int(row.get('page') or 0),
      int(row.get('row_index') or 0),
      str(row.get('course_id') or ''),
      str(row.get('section_id') or ''),
    ),
  )


def sorted_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
  return sorted(rows, key=lambda row: str(row.get('url') or ''))


def sort_detail_links(links: list[DetailLink]) -> list[DetailLink]:
  return sorted(
    links,
    key=lambda link: (
      link.course_id or '',
      link.section_id or '',
      link.kind,
      link.url,
    ),
  )


def filter_detail_links(links: list[DetailLink]) -> list[DetailLink]:
  seen: set[tuple[str, str]] = set()
  deduped: list[DetailLink] = []
  for link in links:
    key = (link.kind, link.url)
    if key in seen:
      continue
    seen.add(key)
    deduped.append(link)
  return deduped


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description='Crawl THU course-opening information into Parquet datasets.'
  )
  parser.add_argument(
    '--semester', required=True, help='Semester slug, for example 2026-fall.'
  )
  parser.add_argument('--run-id', help='Stable run id; defaults to UTC timestamp.')
  parser.add_argument(
    '--max-pages',
    type=int,
    help='Fetch at most this many opening-info pages; default is all pages.',
  )
  parser.add_argument(
    '--page-concurrency',
    type=int,
    default=8,
    help='Concurrent opening-info page requests; default is 8.',
  )
  parser.add_argument(
    '--detail-limit',
    type=int,
    help='Fetch at most this many unique detail links; default is all details.',
  )
  parser.add_argument(
    '--detail-concurrency',
    type=int,
    default=8,
    help='Concurrent detail-page requests; default is 8.',
  )
  parser.add_argument(
    '--skip-details',
    action='store_true',
    help='Only crawl opening-info rows and detail URLs; do not fetch detail pages.',
  )
  parser.add_argument(
    '--continue-on-error',
    action='store_true',
    help='Record detail-page errors and continue instead of failing immediately.',
  )
  parser.add_argument(
    '--dry-run',
    action='store_true',
    help='Validate args and print planned paths without network access.',
  )
  parser.add_argument(
    '--no-progress',
    action='store_true',
    help='Disable crawl progress output.',
  )
  parser.add_argument('--timeout', type=int, default=60)
  parser.add_argument(
    '--delay', type=float, default=0.2, help='Delay between requests.'
  )
  return parser


def main(argv: list[str] | None = None) -> int:
  try:
    args = build_parser().parse_args(argv)
    return crawl(args)
  except CrawlerError as exc:
    print(f'ERROR: {exc}', file=sys.stderr)
    return 2


if __name__ == '__main__':
  raise SystemExit(main())
