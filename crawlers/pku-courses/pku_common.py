from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

SOURCE = 'pku-courses'

CRAWLER_DIR = Path(__file__).resolve().parent
REPO_ROOT = CRAWLER_DIR.parents[1]
LOCAL_BASE_DIR = Path(os.environ.get('COURSORY_LOCAL_DIR', REPO_ROOT / '.local'))
LOCAL_DIR = LOCAL_BASE_DIR / SOURCE
DATA_RAW_ROOT = REPO_ROOT / 'data' / 'raw' / SOURCE
DATA_PROCESSED_ROOT = REPO_ROOT / 'data' / 'processed' / SOURCE
CAPTCHA_IMAGE_PATH = LOCAL_DIR / 'captcha.png'
SESSION_PATH = LOCAL_DIR / 'session.json'

BASE_URL = 'https://dean.pku.edu.cn/service/web/'
COURSE_SEARCH_URL = BASE_URL + 'courseSearch.php'
CAPTCHA_IMAGE_URL = BASE_URL + 'course_vercode.php'
CHECK_SEARCH_URL = BASE_URL + 'course_do.php?act=checkSearch'
CHECK_CAPTCHA_URL = BASE_URL + 'course_do.php?act=checkVercode'
COURSE_SEARCH_ENDPOINT = BASE_URL + 'courseSearch_do.php'
COURSE_DETAIL_URL = BASE_URL + 'courseDetail.php'


class CrawlerError(RuntimeError):
  pass


def utc_now() -> datetime:
  return datetime.now(UTC)


def timestamp_slug(dt: datetime | None = None) -> str:
  dt = dt or utc_now()
  return dt.astimezone(UTC).strftime('%Y-%m-%dT%H%M%SZ')


def ensure_local_dir() -> None:
  LOCAL_DIR.mkdir(parents=True, exist_ok=True)


def validate_semester(value: str) -> str:
  if not re.fullmatch(r'\d{4}-(spring|summer|fall|autumn)', value):
    raise CrawlerError(
      'Semester must look like 2026-spring, 2026-summer, 2026-fall, or 2026-autumn.'
    )
  return value


def semester_to_yearandseme(semester: str) -> str:
  year_text, term = semester.split('-', 1)
  year = int(year_text)
  if term in {'fall', 'autumn'}:
    return f'{year % 100:02d}-{(year + 1) % 100:02d}-1'
  if term == 'spring':
    return f'{(year - 1) % 100:02d}-{year % 100:02d}-2'
  if term == 'summer':
    return f'{(year - 1) % 100:02d}-{year % 100:02d}-3'
  raise CrawlerError(
    'Only spring, summer, fall, and autumn semester slugs can be mapped to PKU yearandseme.'
  )


def course_detail_url(plan_id: str) -> str:
  return COURSE_DETAIL_URL + '?' + urlencode({'flag': '1', 'zxjhbh': plan_id})


def write_json(path: Path, payload: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
  )
