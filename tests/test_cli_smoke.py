from __future__ import annotations

import os
import subprocess
import sys
from importlib import util
from io import StringIO
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
CRAWLER_ROOT = REPO_ROOT / 'crawlers' / 'thu-courses'
sys.path.insert(0, str(CRAWLER_ROOT))


def load_crawler_module(name: str):
  spec = util.spec_from_file_location(name, CRAWLER_ROOT / f'{name}.py')
  if spec is None or spec.loader is None:
    raise RuntimeError(f'Could not load {name}.py')
  module = util.module_from_spec(spec)
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


thu_common = load_crawler_module('thu_common')
opening_links = load_crawler_module('opening_links')
opening_parser = load_crawler_module('opening_parser')
parquet_store = load_crawler_module('parquet_store')
crawl_opening_info = load_crawler_module('crawl_opening_info')


def run_script(
  *args: str, local_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
  env = None
  if local_dir is not None:
    env = {**os.environ, 'COURSORY_LOCAL_DIR': str(local_dir)}
  return subprocess.run(
    [sys.executable, *args],
    cwd=REPO_ROOT,
    env=env,
    text=True,
    capture_output=True,
    check=False,
  )


def test_auth_help() -> None:
  result = run_script('crawlers/thu-courses/auth.py', '--help')

  assert result.returncode == 0
  assert 'login' in result.stdout
  assert 'status' in result.stdout


def test_fetch_dry_run() -> None:
  result = run_script(
    'crawlers/thu-courses/crawl_opening_info.py',
    '--semester',
    '2026-fall',
    '--dry-run',
  )

  assert result.returncode == 0
  assert 'Dry run only' in result.stdout
  assert 'data/processed/thu-courses/2026-fall' in result.stdout
  assert 'p_xnxq=2026-2027-1' in result.stdout


def test_fetch_help_mentions_progress_option() -> None:
  result = run_script('crawlers/thu-courses/crawl_opening_info.py', '--help')

  assert result.returncode == 0
  assert '--no-progress' in result.stdout
  assert '--page-concurrency' in result.stdout
  assert '--detail-concurrency' in result.stdout


def test_progress_bar_writes_readable_status() -> None:
  stream = StringIO()
  progress = crawl_opening_info.ProgressBar(
    label='Opening pages',
    total=2,
    enabled=True,
    stream=stream,
    width=4,
  )

  progress.update(1, suffix='sections=20')
  progress.finish()

  output = stream.getvalue()
  assert 'Opening pages' in output
  assert '1/2' in output
  assert '2/2' in output


def test_status_without_session_is_actionable(tmp_path: Path) -> None:
  result = run_script('crawlers/thu-courses/auth.py', 'status', local_dir=tmp_path)

  assert result.returncode == 2
  assert 'auth.py login' in result.stderr


def test_semester_to_xnxq() -> None:
  assert thu_common.semester_to_xnxq('2026-fall') == '2026-2027-1'
  assert thu_common.semester_to_xnxq('2026-spring') == '2025-2026-2'


def test_collect_detail_links() -> None:
  links = opening_links.collect_detail_links(
    section_html(),
    'https://zhjwxk.cic.tsinghua.edu.cn/xkBks.vxkBksJxjhBs.do',
  )

  assert [link.kind for link in links] == ['course', 'teacher', 'experiment']
  assert links[0].course_id == '00040302'
  assert links[0].section_id == '90'
  assert links[0].url.startswith('https://zhjwxk.cic.tsinghua.edu.cn/')


def test_parse_sections() -> None:
  rows = opening_parser.parse_sections(
    f'<table>{section_html()}</table>',
    base_url='https://zhjwxk.cic.tsinghua.edu.cn/xkBks.vxkBksJxjhBs.do',
    semester='2026-fall',
    xnxq='2026-2027-1',
    page=1,
  )

  assert len(rows) == 1
  row = rows[0]
  assert row['course_id'] == '00040302'
  assert row['course_name'] == '工程域人工智能'
  assert row['credits'] == 2.0
  assert row['undergrad_remaining'] == 30
  assert row['has_second_level'] is False
  assert row['course_detail_url']


def test_parse_detail_pages() -> None:
  course = opening_parser.parse_course_detail(
    """
    <html><head><title>教师网上录入课堂信息</title></head><body>
      课程编号 00040302 课程名: 工程域人工智能
      总学时: 36 总学分: 2
      课程内容简介: 面向工程域人工智能。
      选课指导: 零基础起点。
      先修要求: 无。
      成绩评定标准: 作业与项目。
      技术支持
    </body></html>
    """,
    url='https://zhjwxk.cic.tsinghua.edu.cn/js.vjsKcbBs.do?m=showToXs&p_id=2003990024;00040302&kcfldm=001',
  )
  teacher = opening_parser.parse_teacher_detail(
    """
    <html><head><title>教师个人信息</title></head><body>
      教师号: 2003990024 姓名: 张嘎 性别: 男 职称: 教授
      单位: 水利水电工程系 电话: 62795679 E-Mail: zhangga@tsinghua.edu.cn
      个人简介: 简介文本。主要研究方向: 工程智能。技术支持
    </body></html>
    """,
    url='https://zhjwxk.cic.tsinghua.edu.cn/xkBks.vxkBksJxjhBs.do?m=showJsDetail&p_jsh=2003990024',
  )

  assert course['course_id'] == '00040302'
  assert course['course_name'] == '工程域人工智能'
  assert course['credits'] == 2.0
  assert teacher['teacher_id'] == '2003990024'
  assert teacher['name'] == '张嘎'


def test_parquet_write_smoke(tmp_path: Path) -> None:
  output = tmp_path / 'sections.parquet'
  parquet_store.write_parquet_table(
    output,
    'sections',
    [
      {
        'semester': '2026-fall',
        'xnxq': '2026-2027-1',
        'page': 1,
        'row_index': 1,
        'course_id': '00040302',
        'section_id': '90',
        'course_name': '工程域人工智能',
      }
    ],
  )

  frame = pl.read_parquet(output)
  assert frame.height == 1
  assert frame['course_id'][0] == '00040302'

  experiment_output = tmp_path / 'experiment_details.parquet'
  parquet_store.write_parquet_table(
    experiment_output,
    'experiment_details',
    [
      {
        'url': 'https://zhjwxk.cic.tsinghua.edu.cn/xk.xk_syrwb.do',
        'xnxq': '2026-2027-1',
        'course_id': '00040302',
        'section_id': '90',
        'fields_json': '{"实验": "是"}',
      }
    ],
  )
  experiment_frame = pl.read_parquet(experiment_output)
  assert experiment_frame.height == 1
  assert experiment_frame['fields_json'][0] == '{"实验": "是"}'


def section_html() -> str:
  return """
  <tr>
    <td>水利系</td><td>00040302</td><td>90</td>
    <td><a href="js.vjsKcbBs.do?m=showToXs&p_id=2003990024;00040302&kcfldm=001">工程域人工智能</a></td>
    <td>2</td>
    <td><a href="xkBks.vxkBksJxjhBs.do?m=showJsDetail&p_jsh=2003990024">张嘎</a></td>
    <td>30</td><td>30</td><td>0</td><td>0</td><td>1-3(全周)</td>
    <td>限:2026</td><td>通识选修课</td><td>2026</td><td>否</td>
    <td><a href="xk.xk_syrwb.do?m=show&p_xnxq=2026-2027-1&p_kch=00040302&p_kxh=90">实验...</a></td>
    <td>否</td><td>是</td><td>科学课组</td>
  </tr>
  """
