from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from thu_common import (
  BROWSER_PROFILE_DIR,
  DEFAULT_LOGIN_ENTRY_URL,
  SESSION_SUMMARY_PATH,
  STORAGE_STATE_PATH,
  CrawlerError,
  decode_response_body,
  ensure_local_dir,
  is_opening_info_page,
  is_probably_login_page,
  opening_info_url_for_semester,
  utc_now,
  validate_semester,
  write_json,
)


def import_playwright():
  try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
  except ModuleNotFoundError as exc:
    raise CrawlerError(
      'Playwright is not installed. Install it locally, then run '
      '`uv run python -m playwright install chromium` if the browser binary is missing.'
    ) from exc
  return sync_playwright, PlaywrightTimeoutError


def launch_browser(playwright, *, headless: bool, browser_channel: str | None):
  kwargs: dict[str, object] = {'headless': headless}
  if browser_channel:
    kwargs['channel'] = browser_channel
  try:
    return playwright.chromium.launch(**kwargs)
  except Exception as exc:
    channel_hint = f' with channel={browser_channel!r}' if browser_channel else ''
    raise CrawlerError(
      f'Could not launch Playwright Chromium{channel_hint}. '
      'If this is the first run, try `uv run python -m playwright install chromium`.'
    ) from exc


def launch_login_context(playwright, *, browser_channel: str | None):
  kwargs: dict[str, object] = {'headless': False}
  if browser_channel:
    kwargs['channel'] = browser_channel
  try:
    return playwright.chromium.launch_persistent_context(
      str(BROWSER_PROFILE_DIR), **kwargs
    )
  except Exception as exc:
    channel_hint = f' with channel={browser_channel!r}' if browser_channel else ''
    raise CrawlerError(
      f'Could not launch Playwright Chrome persistent context{channel_hint}.'
    ) from exc


def page_text(page) -> str:
  try:
    return page.locator('body').inner_text(timeout=3000)
  except Exception:
    return ''


def page_content(page) -> str:
  try:
    return page.content()
  except Exception:
    return ''


def probe_opening_target(api, target_url: str, timeout: int) -> tuple[bool, str]:
  try:
    response = api.get(target_url, timeout=timeout * 1000)
    content_type = response.headers.get('content-type', '')
    body = decode_response_body(response.body(), content_type)
    return is_opening_info_page(body), response.url
  except Exception:
    return False, ''


def cmd_login(args: argparse.Namespace) -> int:
  sync_playwright, _ = import_playwright()
  ensure_local_dir()
  semester = validate_semester(args.semester)
  entry_url = args.entry_url
  probe_target = opening_info_url_for_semester(semester)
  browser_channel = None if args.browser == 'chromium' else args.browser

  with sync_playwright() as p:
    if args.fresh and BROWSER_PROFILE_DIR.exists():
      shutil.rmtree(BROWSER_PROFILE_DIR)
    context = launch_login_context(p, browser_channel=browser_channel)
    page = context.pages[0] if context.pages else context.new_page()

    print('Browser opened. Log in through the Tsinghua page in the browser window.')
    print(
      'The default entry is the undergraduate course-selection bridge, which '
      'redirects to Tsinghua SSO for the comprehensive academic system.'
    )
    print(f'Waiting until the course-opening page is reachable: {probe_target}')
    try:
      page.goto(entry_url, wait_until='domcontentloaded', timeout=15000)
    except Exception:
      print(f'Initial login page load did not finish quickly: {entry_url}')
      print('Continue manually in the browser window; session probing is still active.')

    deadline = time.monotonic() + args.timeout
    last_url = ''
    while time.monotonic() < deadline:
      current_url = page.url
      text = page_text(page)
      if current_url != last_url:
        print(f'Current page: {current_url}')
        last_url = current_url
      html = page_content(page)
      probe_ok, probe_url = probe_opening_target(
        context.request, probe_target, timeout=30
      )
      if is_opening_info_page(html) or probe_ok:
        context.storage_state(path=str(STORAGE_STATE_PATH))
        write_json(
          SESSION_SUMMARY_PATH,
          {
            'source': 'thu-courses',
            'saved_at': utc_now().isoformat(),
            'semester': semester,
            'entry_url': entry_url,
            'probe_target': probe_target,
            'probe_url': probe_url,
            'final_url': page.url,
            'storage_state': str(STORAGE_STATE_PATH),
          },
        )
        print(f'Saved Playwright storage state to {STORAGE_STATE_PATH}')
        context.close()
        return 0
      if is_probably_login_page(current_url, text):
        page.wait_for_timeout(2000)
        continue
      page.wait_for_timeout(2000)

    context.storage_state(path=str(STORAGE_STATE_PATH))
    context.close()
    raise CrawlerError(
      'Timed out before a clear post-login page was detected. '
      f'A provisional storage state was saved to {STORAGE_STATE_PATH}; '
      'run `auth.py status` to check it, or re-run `auth.py login --timeout 300`.'
    )


def cmd_status(args: argparse.Namespace) -> int:
  if not STORAGE_STATE_PATH.exists():
    raise CrawlerError(
      f'No saved session exists at {STORAGE_STATE_PATH}. Run `uv run python crawlers/thu-courses/auth.py login`.'
    )

  sync_playwright, _ = import_playwright()
  semester = validate_semester(args.semester)
  status_url = opening_info_url_for_semester(semester)
  browser_channel = None if args.browser == 'chromium' else args.browser
  with sync_playwright() as p:
    browser = launch_browser(
      p, headless=not args.headed, browser_channel=browser_channel
    )
    context = browser.new_context(storage_state=str(STORAGE_STATE_PATH))
    ok, final_url = probe_opening_target(context.request, status_url, args.timeout)
    if args.headed:
      page = context.new_page()
      page.goto(status_url, wait_until='domcontentloaded', timeout=args.timeout * 1000)
      page.wait_for_timeout(1000)
    browser.close()

  if not ok:
    raise CrawlerError(
      'Saved session reached a page, but it was not the course-opening form. '
      'Re-run `uv run python crawlers/thu-courses/auth.py login --fresh`.'
    )
  print('Saved session appears usable.')
  print(f'Semester: {semester}')
  print(f'Checked URL: {status_url}')
  print(f'Final URL: {final_url}')
  return 0


def cmd_logout(_: argparse.Namespace) -> int:
  removed: list[Path] = []
  for path in (STORAGE_STATE_PATH, SESSION_SUMMARY_PATH):
    if path.exists():
      path.unlink()
      removed.append(path)
  if BROWSER_PROFILE_DIR.exists():
    shutil.rmtree(BROWSER_PROFILE_DIR)
    removed.append(BROWSER_PROFILE_DIR)
  if removed:
    print('Removed local session files:')
    for path in removed:
      print(f'  {path}')
  else:
    print('No local session files were present.')
  return 0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description='Tsinghua course crawler authentication helper.'
  )
  sub = parser.add_subparsers(dest='command', required=True)

  login = sub.add_parser(
    'login', help='Start browser-assisted login and save storage state.'
  )
  login.add_argument(
    '--semester',
    default='2026-fall',
    help='Semester opening page to use as the post-login target.',
  )
  login.add_argument(
    '--entry-url',
    default=DEFAULT_LOGIN_ENTRY_URL,
    help='Initial visible page for manual login.',
  )
  login.add_argument(
    '--timeout', type=int, default=180, help='Seconds to wait for manual login.'
  )
  login.add_argument(
    '--browser', choices=['chromium', 'chrome', 'msedge'], default='chrome'
  )
  login.add_argument(
    '--fresh', action='store_true', help='Ignore existing storage_state during login.'
  )
  login.set_defaults(func=cmd_login)

  status = sub.add_parser('status', help='Check whether saved session still works.')
  status.add_argument(
    '--semester',
    default='2026-fall',
    help='Semester opening page to probe with the saved session.',
  )
  status.add_argument('--timeout', type=int, default=30)
  status.add_argument(
    '--headed', action='store_true', help='Show browser while checking.'
  )
  status.add_argument(
    '--browser', choices=['chromium', 'chrome', 'msedge'], default='chrome'
  )
  status.set_defaults(func=cmd_status)

  logout = sub.add_parser('logout', help='Clear saved local session state.')
  logout.set_defaults(func=cmd_logout)
  return parser


def main(argv: list[str] | None = None) -> int:
  try:
    args = build_parser().parse_args(argv)
    return args.func(args)
  except CrawlerError as exc:
    print(f'ERROR: {exc}', file=sys.stderr)
    return 2


if __name__ == '__main__':
  raise SystemExit(main())
