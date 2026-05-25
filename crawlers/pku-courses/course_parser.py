from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from pku_common import course_detail_url


@dataclass(frozen=True)
class DetailLink:
  kind: str
  text: str
  url: str
  plan_id: str
  course_id: str | None = None

  def to_dict(self) -> dict[str, str | None]:
    return asdict(self)


@dataclass
class TeacherAccum:
  teacher_key: str
  name: str
  profile: str | None
  source_section_count: int
  course_ids: set[str]
  course_names: set[str]
  raw_text: str


def parse_section_row(
  row: dict[str, object],
  *,
  semester: str,
  yearandseme: str,
  startrow: int,
  row_index: int,
) -> dict[str, object]:
  plan_id = _text(row.get('zxjhbh'))
  return {
    'semester': semester,
    'yearandseme': yearandseme,
    'startrow': startrow,
    'row_index': row_index,
    'ordinal': _to_int(row.get('xh')),
    'course_id': _text(row.get('kch')),
    'course_name': _text(row.get('kcmc')),
    'department': _text(row.get('kkxsmc')),
    'course_type': _text(row.get('kctxm')),
    'class_number': _text(row.get('jxbh')),
    'credits': _to_float(row.get('xf')),
    'weeks': _text(row.get('qzz')),
    'schedule': _html_text(row.get('sksj')),
    'teacher': _html_text(row.get('teacher')),
    'remark': _text(row.get('bz')),
    'plan_id': plan_id,
    'course_detail_url': course_detail_url(plan_id) if plan_id else None,
    'raw_json': json.dumps(row, ensure_ascii=False, sort_keys=True),
  }


def detail_links_from_sections(sections: list[dict[str, object]]) -> list[DetailLink]:
  seen: set[str] = set()
  links: list[DetailLink] = []
  for section in sections:
    plan_id = str(section.get('plan_id') or '')
    url = str(section.get('course_detail_url') or '')
    if not plan_id or not url or plan_id in seen:
      continue
    seen.add(plan_id)
    links.append(
      DetailLink(
        kind='course',
        text=str(section.get('course_name') or ''),
        url=url,
        plan_id=plan_id,
        course_id=str(section.get('course_id') or '') or None,
      )
    )
  return links


def parse_course_detail(html: str, *, url: str) -> dict[str, object]:
  soup = BeautifulSoup(html, 'lxml')
  fields = _detail_fields(soup)
  title_zh, title_en = _detail_title(soup)
  plan_id = parse_qs(urlparse(url).query).get('zxjhbh', [''])[0] or None
  return {
    'url': url,
    'plan_id': plan_id,
    'title_zh': title_zh,
    'title_en': title_en,
    'course_id': fields.get('课程号'),
    'credits': _to_float(fields.get('学分')),
    'prerequisites': fields.get('先修课程'),
    'department': fields.get('开课院系'),
    'description_zh': fields.get('中文简介'),
    'description_en': fields.get('英文简介'),
    'raw_text': _body_text(html),
  }


def teacher_detail_rows(sections: list[dict[str, object]]) -> list[dict[str, object]]:
  grouped: dict[str, TeacherAccum] = {}
  for section in sections:
    teacher_text = str(section.get('teacher') or '').strip()
    if not teacher_text:
      continue
    for name in _split_teacher_names(teacher_text):
      entry = grouped.setdefault(
        name,
        TeacherAccum(
          teacher_key=name,
          name=name,
          profile=None,
          source_section_count=0,
          course_ids=set(),
          course_names=set(),
          raw_text=name,
        ),
      )
      entry.source_section_count += 1
      if section.get('course_id'):
        entry.course_ids.add(str(section['course_id']))
      if section.get('course_name'):
        entry.course_names.add(str(section['course_name']))

  rows: list[dict[str, object]] = []
  for entry in grouped.values():
    rows.append(
      {
        'teacher_key': entry.teacher_key,
        'name': entry.name,
        'profile': entry.profile,
        'source_section_count': entry.source_section_count,
        'course_ids_json': json.dumps(sorted(entry.course_ids), ensure_ascii=False),
        'course_names_json': json.dumps(sorted(entry.course_names), ensure_ascii=False),
        'raw_text': entry.raw_text,
      }
    )
  return sorted(rows, key=lambda row: str(row.get('teacher_key') or ''))


def _detail_fields(soup: BeautifulSoup) -> dict[str, str]:
  fields: dict[str, str] = {}
  for item in soup.select('.detailsList'):
    spans = item.find_all('span')
    if len(spans) < 2:
      continue
    label = _clean_text(spans[0].get_text(' ', strip=True)).rstrip('\uff1a:')
    value = _clean_text(spans[1].get_text(' ', strip=True))
    if label:
      fields[label] = value
  return fields


def _detail_title(soup: BeautifulSoup) -> tuple[str | None, str | None]:
  title = soup.select_one('.detailTit')
  if title is None:
    return None, None
  title_span = title.find('span')
  title_en = None
  if title_span is not None:
    title_en = _clean_text(title_span.get_text(' ', strip=True)) or None
    title_span.extract()
  title_zh = _clean_text(title.get_text(' ', strip=True)) or None
  return title_zh, title_en


def _split_teacher_names(value: str) -> list[str]:
  parts = re.split('[,\uff0c;\uff1b\u3001\n]+', value)
  return [part.strip() for part in parts if part.strip()]


def _html_text(value: object) -> str | None:
  text = _text(value)
  if text is None:
    return None
  return _clean_text(BeautifulSoup(text, 'lxml').get_text(' ', strip=True)) or None


def _body_text(html: str) -> str:
  return _clean_text(BeautifulSoup(html, 'lxml').get_text(' ', strip=True))


def _text(value: object) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _to_int(value: object) -> int | None:
  text = _text(value)
  if text is None:
    return None
  text = text.replace(',', '')
  if not re.fullmatch(r'-?\d+', text):
    return None
  return int(text)


def _to_float(value: object) -> float | None:
  text = _text(value)
  if text is None:
    return None
  try:
    return float(text)
  except ValueError:
    return None


def _clean_text(value: str) -> str:
  return re.sub(r'\s+', ' ', value).strip()
