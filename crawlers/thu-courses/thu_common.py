from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

SOURCE = 'thu-courses'

CRAWLER_DIR = Path(__file__).resolve().parent
REPO_ROOT = CRAWLER_DIR.parents[1]
LOCAL_BASE_DIR = Path(os.environ.get('COURSORY_LOCAL_DIR', REPO_ROOT / '.local'))
LOCAL_DIR = LOCAL_BASE_DIR / SOURCE
DATA_RAW_ROOT = REPO_ROOT / 'data' / 'raw' / SOURCE
DATA_PROCESSED_ROOT = REPO_ROOT / 'data' / 'processed' / SOURCE
BROWSER_PROFILE_DIR = LOCAL_DIR / 'browser-profile'
STORAGE_STATE_PATH = LOCAL_DIR / 'storage_state.json'
SESSION_SUMMARY_PATH = LOCAL_DIR / 'session.json'

OPENING_INFO_ENDPOINT = 'https://zhjwxk.cic.tsinghua.edu.cn/xkBks.vxkBksJxjhBs.do'
COURSE_SYSTEM_LOGIN_BRIDGE_URL = 'https://zhjwxk.cic.tsinghua.edu.cn/xklogin.do'
DEFAULT_LOGIN_ENTRY_URL = COURSE_SYSTEM_LOGIN_BRIDGE_URL


class CrawlerError(RuntimeError):
  pass


def utc_now() -> datetime:
  return datetime.now(UTC)


def timestamp_slug(dt: datetime | None = None) -> str:
  dt = dt or utc_now()
  return dt.astimezone(UTC).strftime('%Y-%m-%dT%H%M%SZ')


def ensure_local_dir() -> None:
  LOCAL_DIR.mkdir(parents=True, exist_ok=True)


def is_probably_login_page(url: str, text: str = '') -> bool:
  parsed = urlparse(url)
  host = parsed.netloc.lower()
  path = parsed.path.lower()
  if 'id.tsinghua.edu.cn' in host and ('login' in path or '/auth/' in path):
    return True
  return '用户密码登录' in text and '清华大学用户电子身份服务系统' in text


def is_opening_info_page(html: str) -> bool:
  return (
    '选课开课信息查询' in html
    and 'kkxxSearch' in html
    and re.search(r'name=["\']token["\']', html) is not None
  )


def decode_response_body(body: bytes, content_type: str) -> str:
  charset_match = re.search(r'charset=([^;\s]+)', content_type, flags=re.I)
  charsets = [charset_match.group(1)] if charset_match else []
  charsets.extend(['utf-8', 'gbk', 'gb18030'])
  for charset in charsets:
    try:
      return body.decode(charset)
    except LookupError, UnicodeDecodeError:
      continue
  return body.decode('utf-8', errors='replace')


def validate_semester(value: str) -> str:
  if not re.fullmatch(r'\d{4}-(spring|summer|fall|autumn)', value):
    raise CrawlerError(
      'Semester must look like 2026-spring, 2026-summer, or 2026-fall.'
    )
  return value


def semester_to_xnxq(semester: str) -> str:
  year_text, term = semester.split('-', 1)
  year = int(year_text)
  if term in {'fall', 'autumn'}:
    return f'{year}-{year + 1}-1'
  if term == 'spring':
    return f'{year - 1}-{year}-2'
  if term == 'summer':
    return f'{year - 1}-{year}-3'
  raise CrawlerError(
    'Only spring, summer, fall, and autumn semester slugs can be mapped to THU xnxq.'
  )


def opening_info_url_for_semester(semester: str) -> str:
  query = urlencode(
    {
      'm': 'kkxxSearch',
      'p_xnxq': semester_to_xnxq(semester),
      'pathContent': '一级课开课信息',
    }
  )
  return f'{OPENING_INFO_ENDPOINT}?{query}'


def write_json(path: Path, payload: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
  )
