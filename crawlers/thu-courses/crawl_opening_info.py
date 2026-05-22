from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

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


def import_playwright():
  try:
    from playwright.sync_api import sync_playwright
  except ModuleNotFoundError as exc:
    raise CrawlerError(
      'Playwright is not installed. Run `uv sync`, then '
      '`uv run python -m playwright install chromium`.'
    ) from exc
  return sync_playwright


def launch_browser(playwright, *, browser_channel: str | None):
  kwargs: dict[str, object] = {'headless': True}
  if browser_channel:
    kwargs['channel'] = browser_channel
  try:
    return playwright.chromium.launch(**kwargs)
  except Exception as exc:
    raise CrawlerError(
      'Could not launch Playwright Chromium. Try '
      '`uv run python -m playwright install chromium`.'
    ) from exc


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
    print(f'Details: {"disabled" if args.skip_details else "all unique links"}')
    print(
      f'Detail limit: {args.detail_limit if args.detail_limit is not None else "all"}'
    )
    return 0

  if not STORAGE_STATE_PATH.exists():
    raise CrawlerError(
      f'No saved session exists at {STORAGE_STATE_PATH}. '
      'Run `uv run python crawlers/thu-courses/auth.py login` first.'
    )

  raw_dir.mkdir(parents=True, exist_ok=True)
  processed_dir.mkdir(parents=True, exist_ok=True)

  browser_channel = None if args.browser == 'chromium' else args.browser
  sync_playwright = import_playwright()
  started_at = utc_now()
  sections: list[dict[str, object]] = []
  detail_links: list[DetailLink] = []
  course_details: list[dict[str, object]] = []
  teacher_details: list[dict[str, object]] = []
  experiment_details: list[dict[str, object]] = []
  requests: list[dict[str, object]] = []
  errors: list[dict[str, object]] = []

  with sync_playwright() as p:
    browser = launch_browser(p, browser_channel=browser_channel)
    context = browser.new_context(storage_state=str(STORAGE_STATE_PATH))
    api = context.request

    first = fetch_get(api, entry_url, timeout=args.timeout)
    ensure_authenticated(first.url, first.body)
    first_info = parse_page_info(first.body)
    total_pages = first_info.total_pages or 1
    max_pages = min(total_pages, args.max_pages) if args.max_pages else total_pages
    form_fields = parse_form_fields(first.body)
    if not form_fields.get('token'):
      browser.close()
      raise CrawlerError('Opening-info page did not contain the expected hidden token.')

    for page_number in range(1, max_pages + 1):
      if page_number == 1:
        result = first
        method = 'GET'
      else:
        form_fields['m'] = 'kkxxSearch'
        form_fields['page'] = str(page_number)
        result = fetch_post_form(
          api,
          OPENING_INFO_ENDPOINT,
          form_fields,
          timeout=args.timeout,
        )
        method = 'POST'
        ensure_authenticated(result.url, result.body)
        form_fields = parse_form_fields(result.body) or form_fields

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
      requests.append(
        {
          'kind': 'opening_info',
          'page': page_number,
          'url': result.url,
          'method': method,
          'status_code': result.status_code,
          'content_type': result.content_type,
          'raw_path': str(html_path),
          **info.to_dict(),
        }
      )
      sections.extend(
        parse_sections(
          result.body,
          base_url=result.url,
          semester=semester,
          xnxq=xnxq,
          page=page_number,
        )
      )
      detail_links.extend(collect_detail_links(result.body, result.url))
      time.sleep(args.delay)

    detail_links = filter_detail_links(detail_links, limit=args.detail_limit)
    if not args.skip_details:
      for index, link in enumerate(detail_links, start=1):
        try:
          result = fetch_get(api, link.url, timeout=args.timeout)
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
          time.sleep(args.delay)
        except Exception as exc:
          errors.append({'url': link.url, 'kind': link.kind, 'error': str(exc)})
          if not args.continue_on_error:
            browser.close()
            raise

    browser.close()

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
  write_parquet_table(processed_dir / 'sections.parquet', 'sections', sections)
  write_parquet_table(
    processed_dir / 'course_details.parquet', 'course_details', course_details
  )
  write_parquet_table(
    processed_dir / 'teacher_details.parquet', 'teacher_details', teacher_details
  )
  write_parquet_table(
    processed_dir / 'experiment_details.parquet',
    'experiment_details',
    experiment_details,
  )


def fetch_get(api, url: str, *, timeout: int) -> FetchResult:
  response = api.get(url, timeout=timeout * 1000)
  content_type = response.headers.get('content-type', '')
  return FetchResult(
    url=response.url,
    status_code=response.status,
    content_type=content_type,
    body=decode_response_body(response.body(), content_type),
  )


def fetch_post_form(
  api, url: str, fields: dict[str, str], *, timeout: int
) -> FetchResult:
  response = api.fetch(
    url,
    method='POST',
    form=fields,
    timeout=timeout * 1000,
    headers={
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    },
  )
  content_type = response.headers.get('content-type', '')
  return FetchResult(
    url=response.url,
    status_code=response.status,
    content_type=content_type,
    body=decode_response_body(response.body(), content_type),
  )


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


def filter_detail_links(
  links: list[DetailLink], *, limit: int | None
) -> list[DetailLink]:
  seen: set[tuple[str, str]] = set()
  deduped: list[DetailLink] = []
  for link in links:
    key = (link.kind, link.url)
    if key in seen:
      continue
    seen.add(key)
    deduped.append(link)
    if limit is not None and len(deduped) >= limit:
      break
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
    '--detail-limit',
    type=int,
    help='Fetch at most this many unique detail links; default is all details.',
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
    '--browser', choices=['chromium', 'chrome', 'msedge'], default='chrome'
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
