from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from opening_links import collect_detail_links

SECTION_COLUMNS = [
  'department',
  'course_id',
  'section_id',
  'course_name',
  'credits',
  'teacher',
  'undergrad_capacity',
  'undergrad_remaining',
  'grad_capacity',
  'grad_remaining',
  'schedule',
  'notes',
  'course_features',
  'grade',
  'has_second_level',
  'experiment',
  'retake_allowed',
  'selectable',
  'gen_ed_group',
]


@dataclass(frozen=True)
class PageInfo:
  current_page: int | None
  total_pages: int | None
  total_records: int | None

  def to_dict(self) -> dict[str, int | None]:
    return asdict(self)


def parse_page_info(html: str) -> PageInfo:
  text = BeautifulSoup(html, 'lxml').get_text(' ', strip=True)
  match = re.search(
    r'第\s*(\d+)\s*页\s*/\s*共\s*(\d+)\s*页\uff08共\s*([\d,]+)\s*条记录\uff09',
    text,
  )
  if match is None:
    return PageInfo(current_page=None, total_pages=None, total_records=None)
  return PageInfo(
    current_page=int(match.group(1)),
    total_pages=int(match.group(2)),
    total_records=int(match.group(3).replace(',', '')),
  )


def parse_form_fields(html: str) -> dict[str, str]:
  soup = BeautifulSoup(html, 'lxml')
  form = soup.find('form')
  if form is None:
    return {}
  fields: dict[str, str] = {}
  for control in form.find_all(['input', 'select', 'textarea']):
    name = control.get('name')
    if not name:
      continue
    fields[str(name)] = _control_value(control)
  return fields


def parse_sections(
  html: str,
  *,
  base_url: str,
  semester: str,
  xnxq: str,
  page: int | None,
) -> list[dict[str, object]]:
  soup = BeautifulSoup(html, 'lxml')
  sections: list[dict[str, object]] = []
  for row_index, row in enumerate(soup.select('tr')):
    cells = [_clean_text(cell.get_text(' ', strip=True)) for cell in row.find_all('td')]
    if not _looks_like_section_row(cells):
      continue
    values = dict(zip(SECTION_COLUMNS, cells, strict=False))
    links = collect_detail_links(str(row), base_url)
    course_link = next((link.url for link in links if link.kind == 'course'), None)
    teacher_link = next((link.url for link in links if link.kind == 'teacher'), None)
    experiment_link = next(
      (link.url for link in links if link.kind == 'experiment'), None
    )
    sections.append(
      {
        'semester': semester,
        'xnxq': xnxq,
        'page': page,
        'row_index': row_index,
        **_typed_section_values(values),
        'course_detail_url': course_link,
        'teacher_detail_url': teacher_link,
        'experiment_detail_url': experiment_link,
      }
    )
  return sections


def parse_course_detail(html: str, *, url: str) -> dict[str, object]:
  fields = _label_value_fields(html)
  parsed = urlparse(url)
  query = parse_qs(parsed.query)
  p_id = query.get('p_id', [''])[0]
  teacher_id, course_id = _split_p_id(p_id)
  return {
    'url': url,
    'teacher_id': teacher_id,
    'course_id': course_id,
    'title': _title(html),
    'course_number': fields.get('课程编号'),
    'course_name': fields.get('课程名'),
    'hours': _to_int(fields.get('总学时')),
    'credits': _to_float(fields.get('总学分')),
    'description': fields.get('课程内容简介'),
    'course_description_en': fields.get('Course Description'),
    'progress': fields.get('进度安排'),
    'assessment': fields.get('考核方式'),
    'textbook': fields.get('教材及参考书'),
    'main_textbook': fields.get('主教材'),
    'references': fields.get('参考书'),
    'co_teachers': fields.get('合开教师'),
    'guidance': fields.get('选课指导'),
    'prerequisites': fields.get('先修要求'),
    'teaching_features': fields.get('教师教学特色'),
    'office_hour': fields.get('Office Hour'),
    'grading_policy': fields.get('成绩评定标准'),
    'calendar_text': fields.get('教学日历'),
    'raw_text': _body_text(html),
  }


def parse_teacher_detail(html: str, *, url: str) -> dict[str, object]:
  fields = _label_value_fields(html)
  teacher_id = parse_qs(urlparse(url).query).get('p_jsh', [''])[0] or None
  return {
    'url': url,
    'teacher_id': teacher_id,
    'title': _title(html),
    'teacher_number': fields.get('教师号'),
    'name': fields.get('姓名'),
    'gender': fields.get('性别'),
    'academic_title': fields.get('职称'),
    'unit': fields.get('单位'),
    'phone': fields.get('电话'),
    'email': fields.get('E-Mail'),
    'profile': fields.get('个人简介'),
    'research_fields': fields.get('主要研究方向'),
    'research_profile': fields.get('研究方向简介'),
    'raw_text': _body_text(html),
  }


def parse_experiment_detail(html: str, *, url: str) -> dict[str, object]:
  query = parse_qs(urlparse(url).query)
  fields = _label_value_fields(html)
  return {
    'url': url,
    'xnxq': query.get('p_xnxq', [''])[0] or None,
    'course_id': query.get('p_kch', [''])[0] or None,
    'section_id': query.get('p_kxh', [''])[0] or None,
    'title': _title(html),
    'fields_json': json.dumps(fields, ensure_ascii=False, sort_keys=True),
    'raw_text': _body_text(html),
  }


def _control_value(control) -> str:
  if control.name == 'select':
    selected = control.find('option', selected=True)
    if selected is not None:
      return str(selected.get('value', ''))
    first = control.find('option')
    return str(first.get('value', '')) if first is not None else ''
  return str(control.get('value', ''))


def _looks_like_section_row(cells: list[str]) -> bool:
  if len(cells) < len(SECTION_COLUMNS):
    return False
  return bool(re.fullmatch(r'\d{8}', cells[1]) and cells[2])


def _typed_section_values(values: dict[str, str]) -> dict[str, object]:
  return {
    'department': values.get('department') or None,
    'course_id': values.get('course_id') or None,
    'section_id': values.get('section_id') or None,
    'course_name': values.get('course_name') or None,
    'credits': _to_float(values.get('credits')),
    'teacher': values.get('teacher') or None,
    'undergrad_capacity': _to_int(values.get('undergrad_capacity')),
    'undergrad_remaining': _to_int(values.get('undergrad_remaining')),
    'grad_capacity': _to_int(values.get('grad_capacity')),
    'grad_remaining': _to_int(values.get('grad_remaining')),
    'schedule': values.get('schedule') or None,
    'notes': values.get('notes') or None,
    'course_features': values.get('course_features') or None,
    'grade': values.get('grade') or None,
    'has_second_level': _to_bool(values.get('has_second_level')),
    'experiment': values.get('experiment') or None,
    'retake_allowed': _to_bool(values.get('retake_allowed')),
    'selectable': _to_bool(values.get('selectable')),
    'gen_ed_group': values.get('gen_ed_group') or None,
  }


def _label_value_fields(html: str) -> dict[str, str]:
  soup = BeautifulSoup(html, 'lxml')
  text = _body_text(str(soup))
  labels = [
    '课程编号',
    '课程名',
    '总学时',
    '总学分',
    '课程内容简介',
    'Course Description',
    '进度安排',
    '考核方式',
    '教材及参考书',
    '主教材',
    '参考书',
    '合开教师',
    '选课指导',
    '先修要求',
    '教师教学特色',
    'Office Hour',
    '成绩评定标准',
    '教学日历',
    '教师号',
    '姓名',
    '性别',
    '职称',
    '单位',
    '电话',
    'E-Mail',
    '个人简介',
    '主要研究方向',
    '研究方向简介',
  ]
  fields: dict[str, str] = {}
  for index, label in enumerate(labels):
    pattern = rf'{re.escape(label)}[\uff1a:]?\s*(.*?)'
    following = labels[index + 1 :]
    if following:
      pattern += (
        r'(?='
        + '|'.join(re.escape(item) + r'[\uff1a:]?' for item in following)
        + r'|技术支持|$)'
      )
    else:
      pattern += r'(?=技术支持|$)'
    match = re.search(pattern, text, flags=re.S)
    if match is not None:
      fields[label] = _clean_text(match.group(1))
  return fields


def _body_text(html: str) -> str:
  return _clean_text(BeautifulSoup(html, 'lxml').get_text(' ', strip=True))


def _title(html: str) -> str | None:
  title = BeautifulSoup(html, 'lxml').title
  if title is None:
    return None
  return _clean_text(title.get_text(' ', strip=True)) or None


def _split_p_id(p_id: str) -> tuple[str | None, str | None]:
  if not p_id:
    return None, None
  left, _, right = p_id.partition(';')
  return left or None, right or None


def _to_int(value: str | None) -> int | None:
  if value is None:
    return None
  value = value.strip().replace(',', '')
  if not value or not re.fullmatch(r'-?\d+', value):
    return None
  return int(value)


def _to_float(value: str | None) -> float | None:
  if value is None:
    return None
  value = value.strip()
  if not value:
    return None
  try:
    return float(value)
  except ValueError:
    return None


def _to_bool(value: str | None) -> bool | None:
  if value == '是':
    return True
  if value == '否':
    return False
  return None


def _clean_text(value: str) -> str:
  return re.sub(r'\s+', ' ', value).strip()
