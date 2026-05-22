from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

TABLE_SCHEMAS: dict[str, dict[str, Any]] = {
  'sections': {
    'semester': pl.String,
    'xnxq': pl.String,
    'page': pl.Int64,
    'row_index': pl.Int64,
    'department': pl.String,
    'course_id': pl.String,
    'section_id': pl.String,
    'course_name': pl.String,
    'credits': pl.Float64,
    'teacher': pl.String,
    'undergrad_capacity': pl.Int64,
    'undergrad_remaining': pl.Int64,
    'grad_capacity': pl.Int64,
    'grad_remaining': pl.Int64,
    'schedule': pl.String,
    'notes': pl.String,
    'course_features': pl.String,
    'grade': pl.String,
    'has_second_level': pl.Boolean,
    'experiment': pl.String,
    'retake_allowed': pl.Boolean,
    'selectable': pl.Boolean,
    'gen_ed_group': pl.String,
    'course_detail_url': pl.String,
    'teacher_detail_url': pl.String,
    'experiment_detail_url': pl.String,
  },
  'course_details': {
    'url': pl.String,
    'teacher_id': pl.String,
    'course_id': pl.String,
    'title': pl.String,
    'course_number': pl.String,
    'course_name': pl.String,
    'hours': pl.Int64,
    'credits': pl.Float64,
    'description': pl.String,
    'course_description_en': pl.String,
    'progress': pl.String,
    'assessment': pl.String,
    'textbook': pl.String,
    'main_textbook': pl.String,
    'references': pl.String,
    'co_teachers': pl.String,
    'guidance': pl.String,
    'prerequisites': pl.String,
    'teaching_features': pl.String,
    'office_hour': pl.String,
    'grading_policy': pl.String,
    'calendar_text': pl.String,
    'raw_text': pl.String,
  },
  'teacher_details': {
    'url': pl.String,
    'teacher_id': pl.String,
    'title': pl.String,
    'teacher_number': pl.String,
    'name': pl.String,
    'gender': pl.String,
    'academic_title': pl.String,
    'unit': pl.String,
    'phone': pl.String,
    'email': pl.String,
    'profile': pl.String,
    'research_fields': pl.String,
    'research_profile': pl.String,
    'raw_text': pl.String,
  },
  'experiment_details': {
    'url': pl.String,
    'xnxq': pl.String,
    'course_id': pl.String,
    'section_id': pl.String,
    'title': pl.String,
    'fields_json': pl.String,
    'raw_text': pl.String,
  },
}


def write_parquet_table(
  path: Path, table_name: str, rows: list[dict[str, Any]]
) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  schema = TABLE_SCHEMAS[table_name]
  normalized_rows = [_normalize_row(row, schema) for row in rows]
  frame = (
    pl.DataFrame(normalized_rows, schema=schema)
    if rows
    else pl.DataFrame(schema=schema)
  )
  frame.write_parquet(path)


def _normalize_row(row: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
  return {key: row.get(key) for key in schema}
