from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

TABLE_SCHEMAS: dict[str, dict[str, Any]] = {
  'sections': {
    'semester': pl.String,
    'yearandseme': pl.String,
    'startrow': pl.Int64,
    'row_index': pl.Int64,
    'ordinal': pl.Int64,
    'course_id': pl.String,
    'course_name': pl.String,
    'department': pl.String,
    'course_type': pl.String,
    'class_number': pl.String,
    'credits': pl.Float64,
    'weeks': pl.String,
    'schedule': pl.String,
    'teacher': pl.String,
    'remark': pl.String,
    'plan_id': pl.String,
    'course_detail_url': pl.String,
    'raw_json': pl.String,
  },
  'detail_links': {
    'kind': pl.String,
    'text': pl.String,
    'url': pl.String,
    'plan_id': pl.String,
    'course_id': pl.String,
    'stable_key': pl.String,
  },
  'course_details': {
    'url': pl.String,
    'plan_id': pl.String,
    'title_zh': pl.String,
    'title_en': pl.String,
    'course_id': pl.String,
    'credits': pl.Float64,
    'prerequisites': pl.String,
    'department': pl.String,
    'description_zh': pl.String,
    'description_en': pl.String,
    'raw_text': pl.String,
  },
  'teacher_details': {
    'teacher_key': pl.String,
    'name': pl.String,
    'profile': pl.String,
    'source_section_count': pl.Int64,
    'course_ids_json': pl.String,
    'course_names_json': pl.String,
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
