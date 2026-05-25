from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import httpx
from course_parser import (
  DetailLink,
  detail_links_from_sections,
  parse_course_detail,
  parse_section_row,
  teacher_detail_rows,
)
from parquet_store import write_parquet_table
from pku_common import (
  CAPTCHA_IMAGE_PATH,
  CAPTCHA_IMAGE_URL,
  CHECK_CAPTCHA_URL,
  CHECK_SEARCH_URL,
  COURSE_SEARCH_ENDPOINT,
  COURSE_SEARCH_URL,
  DATA_PROCESSED_ROOT,
  DATA_RAW_ROOT,
  SESSION_PATH,
  SOURCE,
  CrawlerError,
  ensure_local_dir,
  semester_to_yearandseme,
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


class RetryableFetchError(CrawlerError):
  pass


TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class CaptchaProvider:
  def __init__(self, codes: list[str], *, image_path: Path) -> None:
    self.codes = codes
    self.image_path = image_path
    self.index = 0

  def next_code(self) -> str:
    if self.index < len(self.codes):
      code = self.codes[self.index]
      self.index += 1
      return code
    if not sys.stdin.isatty():
      raise CrawlerError(
        f'Captcha is required. Open {self.image_path} and re-run with '
        '`--resume-session --captcha-code <code>` if this session was prepared '
        'with `--prepare-captcha`, or run from an interactive terminal.'
      )
    try:
      return input(f'Enter captcha shown in {self.image_path}: ').strip()
    except EOFError as exc:
      raise CrawlerError(
        f'Captcha is required. Open {self.image_path} and re-run with '
        '`--resume-session --captcha-code <code>`.'
      ) from exc

  def has_supplied_code(self) -> bool:
    return self.index < len(self.codes)


def crawl(args: argparse.Namespace) -> int:
  semester = validate_semester(args.semester)
  yearandseme = args.yearandseme or semester_to_yearandseme(semester)
  run_id = args.run_id or timestamp_slug()
  raw_dir = DATA_RAW_ROOT / semester / run_id
  processed_dir = DATA_PROCESSED_ROOT / semester / run_id

  if args.dry_run:
    print('Dry run only; no network request will be made.')
    print(f'Search page: {COURSE_SEARCH_URL}')
    print(f'List endpoint: {COURSE_SEARCH_ENDPOINT}')
    print(f'Semester: {semester}')
    print(f'yearandseme: {yearandseme}')
    print(f'Raw audit cache: {raw_dir}')
    print(f'Processed Parquet: {processed_dir}')
    print(f'Max pages: {args.max_pages or "all"}')
    print(
      f'Detail limit: {args.detail_limit if args.detail_limit is not None else "all"}'
    )
    print(f'Detail concurrency: {args.detail_concurrency}')
    return 0

  if args.prepare_captcha:
    ensure_local_dir()
    with new_http_client(timeout=args.timeout) as client:
      bootstrap_public_session(client, timeout=args.timeout)
      save_captcha_image(client, timeout=args.timeout)
      save_session_cookies(client, SESSION_PATH)
    print(f'Captcha image saved: {CAPTCHA_IMAGE_PATH}')
    print(f'Session saved: {SESSION_PATH}')
    print('Re-run with `--resume-session --captcha-code <code>` to crawl.')
    return 0

  if args.detail_concurrency < 1:
    raise CrawlerError('Detail concurrency must be a positive integer.')
  if args.retries < 0:
    raise CrawlerError('Retries must be zero or a positive integer.')
  if args.retry_delay < 0:
    raise CrawlerError('Retry delay must be zero or a positive number.')

  ensure_local_dir()
  raw_dir.mkdir(parents=True, exist_ok=True)
  processed_dir.mkdir(parents=True, exist_ok=True)

  started_at = utc_now()
  sections: list[dict[str, object]] = []
  course_details: list[dict[str, object]] = []
  requests: list[dict[str, object]] = []
  errors: list[dict[str, object]] = []
  captcha_provider = CaptchaProvider(args.captcha_code, image_path=CAPTCHA_IMAGE_PATH)

  with new_http_client(timeout=args.timeout) as client:
    if args.resume_session:
      load_session_cookies(client, SESSION_PATH)
    else:
      bootstrap_public_session(client, timeout=args.timeout)
    if not args.resume_session or args.captcha_code:
      ensure_search_allowed(
        client,
        captcha_provider,
        timeout=args.timeout,
        prefer_existing_captcha=args.resume_session,
      )
    startrow = args.startrow
    page_number = 0
    total_count: int | None = None

    while True:
      if args.max_pages is not None and page_number >= args.max_pages:
        break
      if args.limit is not None and len(sections) >= args.limit:
        break

      result = fetch_search_page(
        client,
        yearandseme=yearandseme,
        course_name=args.course_name,
        teacher_name=args.teacher_name,
        course_type=args.course_type,
        department=args.department,
        startrow=startrow,
        timeout=args.timeout,
        retries=args.retries,
        retry_delay=args.retry_delay,
      )
      page_number += 1
      page_sections, page_total, request = record_search_page(
        result=result,
        raw_dir=raw_dir,
        run_id=run_id,
        semester=semester,
        yearandseme=yearandseme,
        startrow=startrow,
        page_number=page_number,
      )
      requests.append(request)
      if total_count is None:
        total_count = page_total
      if args.limit is not None:
        remaining = max(args.limit - len(sections), 0)
        page_sections = page_sections[:remaining]
      sections.extend(page_sections)
      safe_print(
        f'Fetched page {page_number}: startrow={startrow} '
        f'rows={len(page_sections)} total={total_count or 0}'
      )
      if not page_sections:
        break
      startrow += len(page_sections)
      if total_count is not None and startrow >= total_count:
        break
      sleep_after_request(args.delay)

    detail_links = detail_links_from_sections(sections)
    if args.detail_limit is not None:
      detail_links = detail_links[: args.detail_limit]
    if not args.skip_details:
      detail_errors = fetch_and_record_details(
        client=client,
        detail_links=detail_links,
        raw_dir=raw_dir,
        run_id=run_id,
        semester=semester,
        yearandseme=yearandseme,
        timeout=args.timeout,
        delay=args.delay,
        retries=args.retries,
        retry_delay=args.retry_delay,
        detail_concurrency=args.detail_concurrency,
        course_details=course_details,
        requests=requests,
      )
      errors.extend(detail_errors)

  teacher_details = teacher_detail_rows(sections)
  write_outputs(
    processed_dir=processed_dir,
    sections=sections,
    detail_links=detail_links,
    course_details=course_details,
    teacher_details=teacher_details,
  )
  write_json(
    processed_dir / 'manifest.json',
    {
      'source': SOURCE,
      'semester': semester,
      'yearandseme': yearandseme,
      'run_id': run_id,
      'started_at': started_at.isoformat(),
      'finished_at': utc_now().isoformat(),
      'entry_url': COURSE_SEARCH_URL,
      'list_endpoint': COURSE_SEARCH_ENDPOINT,
      'raw_dir': str(raw_dir),
      'processed_dir': str(processed_dir),
      'sections': len(sections),
      'detail_links': len(detail_links),
      'course_details': len(course_details),
      'teacher_details': len(teacher_details),
      'errors': errors,
      'outputs': {
        'sections': str(processed_dir / 'sections.parquet'),
        'detail_links': str(processed_dir / 'detail_links.parquet'),
        'course_details': str(processed_dir / 'course_details.parquet'),
        'teacher_details': str(processed_dir / 'teacher_details.parquet'),
      },
      'requests': requests,
      'notes': (
        'The observed public PKU search page exposes course-detail URLs but no '
        'teacher-profile detail URL; teacher_details contains normalized list text.'
      ),
    },
  )

  print(f'Wrote processed dataset: {processed_dir}')
  print(f'Wrote raw audit cache: {raw_dir}')
  print(f'Sections: {len(sections)}')
  print(f'Course details: {len(course_details)}')
  print(f'Teacher rows: {len(teacher_details)}')
  if errors:
    print(f'Errors: {len(errors)}')
  return 0


def bootstrap_public_session(client: httpx.Client, *, timeout: int) -> None:
  result = client.get(COURSE_SEARCH_URL, timeout=timeout)
  if result.status_code >= 400:
    raise CrawlerError(f'Course search page returned HTTP {result.status_code}.')


def response_payload(
  response: httpx.Response, *, context: str, retryable: bool = False
) -> dict[str, object]:
  if response.status_code >= 400:
    message = f'{context} returned HTTP {response.status_code}.'
    if retryable:
      raise RetryableFetchError(message)
    raise CrawlerError(message)
  try:
    payload = response.json()
  except ValueError as exc:
    message = f'{context} returned invalid JSON.'
    if retryable:
      raise RetryableFetchError(message) from exc
    raise CrawlerError(message) from exc
  if not isinstance(payload, dict):
    message = f'{context} returned unexpected JSON.'
    if retryable:
      raise RetryableFetchError(message)
    raise CrawlerError(message)
  return payload


def payload_code(payload: dict[str, object]) -> int:
  value = payload.get('code')
  if isinstance(value, int):
    return value
  if isinstance(value, str):
    with suppress(ValueError):
      return int(value)
  return 0


def ensure_search_allowed(
  client: httpx.Client,
  captcha_provider: CaptchaProvider,
  *,
  timeout: int,
  prefer_existing_captcha: bool,
) -> None:
  response = client.post(CHECK_SEARCH_URL, timeout=timeout)
  payload = response_payload(response, context='Search permission check')
  if payload_code(payload) == 1:
    return
  solve_captcha(
    client,
    captcha_provider,
    timeout=timeout,
    prefer_existing_captcha=prefer_existing_captcha,
  )


def solve_captcha(
  client: httpx.Client,
  captcha_provider: CaptchaProvider,
  *,
  timeout: int,
  prefer_existing_captcha: bool,
) -> None:
  for _attempt in range(1, 6):
    if (
      not prefer_existing_captcha
      or not CAPTCHA_IMAGE_PATH.exists()
      or not captcha_provider.has_supplied_code()
    ):
      save_captcha_image(client, timeout=timeout)
      save_session_cookies(client, SESSION_PATH)
      safe_print(f'Captcha image saved: {CAPTCHA_IMAGE_PATH}')
    code = captcha_provider.next_code()
    if not code:
      continue
    check = client.post(CHECK_CAPTCHA_URL, data={'code': code}, timeout=timeout)
    try:
      payload = response_payload(check, context='Captcha check', retryable=True)
    except RetryableFetchError as exc:
      safe_print(f'{exc} Retrying with a fresh captcha.')
      prefer_existing_captcha = False
      continue
    if payload_code(payload) == 1:
      safe_print('Captcha accepted.')
      return
    safe_print(f'Captcha rejected: {payload.get("msg") or "unknown error"}')
  raise CrawlerError('Captcha was rejected too many times.')


def save_captcha_image(client: httpx.Client, *, timeout: int) -> None:
  image_response = client.get(CAPTCHA_IMAGE_URL, timeout=timeout)
  if image_response.status_code >= 400:
    raise CrawlerError(f'Captcha image returned HTTP {image_response.status_code}.')
  CAPTCHA_IMAGE_PATH.write_bytes(image_response.content)


def save_session_cookies(client: httpx.Client, path: Path) -> None:
  cookies = []
  for cookie in client.cookies.jar:
    cookies.append(
      {
        'name': cookie.name,
        'value': cookie.value,
        'domain': cookie.domain,
        'path': cookie.path,
      }
    )
  write_json(
    path,
    {
      'source': SOURCE,
      'saved_at': utc_now().isoformat(),
      'cookies': cookies,
    },
  )


def load_session_cookies(client: httpx.Client, path: Path) -> None:
  if not path.exists():
    raise CrawlerError(
      f'No saved PKU session exists at {path}. '
      'Run with `--prepare-captcha` first or omit `--resume-session`.'
    )
  payload = json.loads(path.read_text(encoding='utf-8'))
  for cookie in payload.get('cookies', []):
    name = cookie.get('name')
    value = cookie.get('value')
    domain = cookie.get('domain')
    if not name or value is None or not domain:
      continue
    client.cookies.set(
      str(name),
      str(value),
      domain=str(domain),
      path=str(cookie.get('path') or '/'),
    )


def fetch_search_page(
  client: httpx.Client,
  *,
  yearandseme: str,
  course_name: str,
  teacher_name: str,
  course_type: str,
  department: str,
  startrow: int,
  timeout: int,
  retries: int,
  retry_delay: float,
) -> FetchResult:
  fields = {
    'coursename': course_name,
    'teachername': teacher_name,
    'yearandseme': yearandseme,
    'coursetype': course_type,
    'yuanxi': department,
    'startrow': str(startrow),
  }
  return fetch_with_retries(
    lambda: fetch_post_form_once(
      client, COURSE_SEARCH_ENDPOINT, fields, timeout=timeout
    ),
    label=f'POST {COURSE_SEARCH_ENDPOINT}',
    retries=retries,
    retry_delay=retry_delay,
    validate=ensure_search_payload,
  )


def fetch_detail_page(
  client: httpx.Client,
  link: DetailLink,
  *,
  timeout: int,
  retries: int,
  retry_delay: float,
) -> FetchResult:
  return fetch_with_retries(
    lambda: fetch_get_once(client, link.url, timeout=timeout),
    label=f'GET {link.url}',
    retries=retries,
    retry_delay=retry_delay,
  )


def fetch_with_retries(
  operation: Callable[[], FetchResult],
  *,
  label: str,
  retries: int,
  retry_delay: float,
  validate: Callable[[FetchResult], None] | None = None,
) -> FetchResult:
  attempts = max(retries + 1, 1)
  for attempt in range(1, attempts + 1):
    try:
      result = operation()
      if result.status_code in TRANSIENT_STATUS_CODES:
        raise RetryableFetchError(
          f'{label} returned transient HTTP {result.status_code}'
        )
      if result.status_code >= 400:
        raise CrawlerError(f'{label} returned HTTP {result.status_code}')
      if validate is not None:
        validate(result)
      return result
    except (httpx.TimeoutException, httpx.TransportError, RetryableFetchError) as exc:
      if attempt >= attempts:
        raise CrawlerError(f'{label} failed after {attempts} attempts: {exc}') from exc
      time.sleep(retry_delay * attempt)
  raise CrawlerError(f'{label} failed unexpectedly.')


def fetch_get_once(client: httpx.Client, url: str, *, timeout: int) -> FetchResult:
  response = client.get(url, timeout=timeout)
  return FetchResult(
    url=str(response.url),
    status_code=response.status_code,
    content_type=response.headers.get('content-type', ''),
    body=response.text,
  )


def fetch_post_form_once(
  client: httpx.Client, url: str, fields: dict[str, str], *, timeout: int
) -> FetchResult:
  response = client.post(url, data=fields, timeout=timeout)
  return FetchResult(
    url=str(response.url),
    status_code=response.status_code,
    content_type=response.headers.get('content-type', ''),
    body=response.text,
  )


def ensure_search_payload(result: FetchResult) -> None:
  try:
    payload = json.loads(result.body)
  except json.JSONDecodeError as exc:
    raise RetryableFetchError('Search response was not JSON.') from exc
  if payload.get('status') not in {'ok', 'no'}:
    raise RetryableFetchError(f'Search response had unexpected status: {payload}')


def record_search_page(
  *,
  result: FetchResult,
  raw_dir: Path,
  run_id: str,
  semester: str,
  yearandseme: str,
  startrow: int,
  page_number: int,
) -> tuple[list[dict[str, object]], int, dict[str, object]]:
  payload = json.loads(result.body)
  raw_path = raw_dir / f'{run_id}_search_{page_number:03d}.json'
  write_json(raw_path, payload)
  write_json(
    raw_path.with_suffix('.meta.json'),
    {
      'source': SOURCE,
      'semester': semester,
      'yearandseme': yearandseme,
      'fetched_at': utc_now().isoformat(),
      'startrow': startrow,
      'url': result.url,
      'method': 'POST',
      'status_code': result.status_code,
      'content_type': result.content_type,
      'notes': 'Raw PKU course-search JSON retained as ignored audit cache.',
    },
  )
  rows = payload.get('courselist') or []
  if not isinstance(rows, list):
    raise CrawlerError('Search JSON courselist was not a list.')
  sections = [
    parse_section_row(
      row,
      semester=semester,
      yearandseme=yearandseme,
      startrow=startrow,
      row_index=index,
    )
    for index, row in enumerate(rows)
    if isinstance(row, dict)
  ]
  total_count = int(payload.get('count') or 0)
  request: dict[str, object] = {
    'kind': 'course_search',
    'page': page_number,
    'startrow': startrow,
    'url': result.url,
    'method': 'POST',
    'status_code': result.status_code,
    'content_type': result.content_type,
    'raw_path': str(raw_path),
    'rows': len(sections),
    'total_count': total_count,
  }
  return sections, total_count, request


def fetch_and_record_details(
  *,
  client: httpx.Client,
  detail_links: list[DetailLink],
  raw_dir: Path,
  run_id: str,
  semester: str,
  yearandseme: str,
  timeout: int,
  delay: float,
  retries: int,
  retry_delay: float,
  detail_concurrency: int,
  course_details: list[dict[str, object]],
  requests: list[dict[str, object]],
) -> list[dict[str, object]]:
  errors: list[dict[str, object]] = []

  def submit(link: DetailLink) -> FetchResult:
    result = fetch_detail_page(
      client,
      link,
      timeout=timeout,
      retries=retries,
      retry_delay=retry_delay,
    )
    sleep_after_request(delay)
    return result

  def handle(item: tuple[int, DetailLink], future: Future[FetchResult]) -> None:
    index, link = item
    try:
      result = future.result()
      request = record_detail_page(
        link=link,
        result=result,
        raw_dir=raw_dir,
        run_id=run_id,
        index=index,
        semester=semester,
        yearandseme=yearandseme,
        course_details=course_details,
      )
      requests.append(request)
      safe_print(f'Fetched detail {index}/{len(detail_links)}: {link.text}')
    except Exception as exc:
      errors.append({'url': link.url, 'kind': link.kind, 'error': str(exc)})

  run_bounded_thread_pool(
    list(enumerate(detail_links, start=1)),
    max_workers=detail_concurrency,
    submit=lambda item: submit(item[1]),
    handle=handle,
    interrupt_message='Interrupted by user while fetching detail pages.',
  )
  return errors


def record_detail_page(
  *,
  link: DetailLink,
  result: FetchResult,
  raw_dir: Path,
  run_id: str,
  index: int,
  semester: str,
  yearandseme: str,
  course_details: list[dict[str, object]],
) -> dict[str, object]:
  html_path = raw_dir / f'{run_id}_detail_course_{index:03d}.html'
  html_path.write_text(result.body, encoding='utf-8')
  write_json(
    html_path.with_suffix('.meta.json'),
    {
      'source': SOURCE,
      'semester': semester,
      'yearandseme': yearandseme,
      'fetched_at': utc_now().isoformat(),
      'detail_kind': link.kind,
      'detail_text': link.text,
      'plan_id': link.plan_id,
      'course_id': link.course_id,
      'url': result.url,
      'method': 'GET',
      'status_code': result.status_code,
      'content_type': result.content_type,
      'notes': 'Raw PKU course-detail page retained as ignored audit cache.',
    },
  )
  course_details.append(parse_course_detail(result.body, url=result.url))
  return {
    'kind': 'detail_course',
    'detail_text': link.text,
    'url': result.url,
    'method': 'GET',
    'status_code': result.status_code,
    'content_type': result.content_type,
    'raw_path': str(html_path),
  }


def write_outputs(
  *,
  processed_dir: Path,
  sections: list[dict[str, object]],
  detail_links: list[DetailLink],
  course_details: list[dict[str, object]],
  teacher_details: list[dict[str, object]],
) -> None:
  write_parquet_table(processed_dir / 'sections.parquet', 'sections', sections)
  write_parquet_table(
    processed_dir / 'detail_links.parquet',
    'detail_links',
    detail_link_rows(detail_links),
  )
  write_parquet_table(
    processed_dir / 'course_details.parquet',
    'course_details',
    sorted(course_details, key=lambda row: str(row.get('url') or '')),
  )
  write_parquet_table(
    processed_dir / 'teacher_details.parquet',
    'teacher_details',
    teacher_details,
  )


def detail_link_rows(links: list[DetailLink]) -> list[dict[str, object]]:
  return [
    {
      **link.to_dict(),
      'stable_key': json.dumps(['course', link.plan_id], ensure_ascii=False),
    }
    for link in links
  ]


def run_bounded_thread_pool[T, R](
  items: Iterable[T],
  *,
  max_workers: int,
  submit: Callable[[T], R],
  handle: Callable[[T, Future[R]], None],
  interrupt_message: str,
) -> None:
  item_iterator = iter(items)
  futures: dict[Future[R], T] = {}
  executor = ThreadPoolExecutor(max_workers=max_workers)

  def submit_next() -> bool:
    try:
      item = next(item_iterator)
    except StopIteration:
      return False
    futures[executor.submit(submit, item)] = item
    return True

  try:
    for _ in range(max_workers):
      if not submit_next():
        break
    while futures:
      for future in as_completed(futures):
        item = futures.pop(future)
        handle(item, future)
        submit_next()
        break
  except KeyboardInterrupt as exc:
    for future in futures:
      future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)
    raise CrawlerError(interrupt_message) from exc
  except Exception:
    for future in futures:
      future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)
    raise
  else:
    executor.shutdown(wait=True)


def new_http_client(*, timeout: int) -> httpx.Client:
  return httpx.Client(
    follow_redirects=True,
    trust_env=False,
    timeout=httpx.Timeout(timeout),
    headers={
      'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome Safari/537.36'
      ),
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
      'Origin': 'https://dean.pku.edu.cn',
      'Referer': COURSE_SEARCH_URL,
      'X-Requested-With': 'XMLHttpRequest',
    },
  )


def sleep_after_request(delay: float) -> None:
  if delay > 0:
    time.sleep(delay)


def safe_print(*objects: object) -> None:
  with suppress(OSError):
    print(*objects)


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description='Crawl PKU public course-search information into Parquet datasets.'
  )
  parser.add_argument(
    '--semester', required=True, help='Semester slug, for example 2026-spring.'
  )
  parser.add_argument(
    '--yearandseme',
    help='PKU raw yearandseme value, for example 25-26-2. Defaults from --semester.',
  )
  parser.add_argument('--run-id', help='Stable run id; defaults to UTC timestamp.')
  parser.add_argument('--course-name', default='', help='Course name or id filter.')
  parser.add_argument('--teacher-name', default='', help='Teacher name filter.')
  parser.add_argument(
    '--course-type', default='0', help='PKU coursetype filter; default is all.'
  )
  parser.add_argument(
    '--department', default='0', help='PKU yuanxi department filter; default is all.'
  )
  parser.add_argument('--startrow', type=int, default=0)
  parser.add_argument(
    '--max-pages',
    type=int,
    help='Fetch at most this many list pages; default is all pages.',
  )
  parser.add_argument('--limit', type=int, help='Fetch at most this many sections.')
  parser.add_argument(
    '--detail-limit',
    type=int,
    help='Fetch at most this many unique course details; default is all details.',
  )
  parser.add_argument(
    '--detail-concurrency',
    type=int,
    default=4,
    help='Concurrent detail-page requests; default is 4.',
  )
  parser.add_argument(
    '--skip-details',
    action='store_true',
    help='Only crawl list rows and detail URLs; do not fetch detail pages.',
  )
  parser.add_argument(
    '--captcha-code',
    action='append',
    default=[],
    help='Captcha code to use non-interactively. Repeat for multiple captcha prompts.',
  )
  parser.add_argument(
    '--prepare-captcha',
    action='store_true',
    help='Save a captcha image and reusable public session, then exit.',
  )
  parser.add_argument(
    '--resume-session',
    action='store_true',
    help='Reuse the anonymous public captcha session saved by --prepare-captcha.',
  )
  parser.add_argument(
    '--dry-run',
    action='store_true',
    help='Validate args and print planned paths without network access.',
  )
  parser.add_argument('--timeout', type=int, default=60)
  parser.add_argument(
    '--retries',
    type=int,
    default=3,
    help='Retry transient request failures this many times; default is 3.',
  )
  parser.add_argument(
    '--retry-delay',
    type=float,
    default=1.0,
    help='Base delay in seconds between retries; default is 1.0.',
  )
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
