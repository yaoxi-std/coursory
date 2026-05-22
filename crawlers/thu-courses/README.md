# THU Course Crawler

This backend fetches Tsinghua undergraduate course-opening data for local analysis. It is read-only: it must not submit course selections, add/drop courses, join waitlists, or perform any enrollment-changing action.

The normal workflow is:

1. Use Playwright to let the local user log in manually.
2. Reuse the saved Playwright storage state for read-only course-opening requests.
3. Write canonical structured data as Parquet.
4. Keep raw HTML only as an ignored audit/debug cache.

## Files

```text
crawlers/thu-courses/
  auth.py
  crawl_opening_info.py
  opening_links.py
  opening_parser.py
  parquet_store.py
  thu_common.py
  AGENTS.md
  README.md
```

Local state and outputs are ignored by git:

```text
.local/thu-courses/
  storage_state.json
  session.json

data/raw/thu-courses/<semester>/<run_id>/
  <raw HTML and metadata, audit/debug only>

data/processed/thu-courses/<semester>/<run_id>/
  sections.parquet
  course_details.parquet
  teacher_details.parquet
  experiment_details.parquet
  manifest.json
```

## Setup

```bash
uv sync
```

The default browser channel is the local Google Chrome installation (`--browser chrome`), so this workflow does not require downloading Playwright's bundled Chromium. Use `--browser chromium` only if you explicitly installed Playwright Chromium.

## Authentication

Start a headed Playwright-controlled local Chrome and log in manually through Tsinghua SSO:

```bash
uv run python crawlers/thu-courses/auth.py login
```

By default this opens the undergraduate course-selection bridge `https://zhjwxk.cic.tsinghua.edu.cn/xklogin.do`, which redirects to `id.tsinghua.edu.cn` for the `综合教务系统` SSO login. After SSO succeeds, the script probes the `2026-fall` opening-info page and saves the session only after that page is reachable. To use another semester as the verification target:

```bash
uv run python crawlers/thu-courses/auth.py login --semester 2026-spring
```

Check whether the saved session still reaches the course-opening page:

```bash
uv run python crawlers/thu-courses/auth.py status
```

Clear local session state:

```bash
uv run python crawlers/thu-courses/auth.py logout
```

Useful options:

```bash
uv run python crawlers/thu-courses/auth.py login --timeout 300
uv run python crawlers/thu-courses/auth.py login --browser chrome
uv run python crawlers/thu-courses/auth.py status --headed
```

The crawler stores only project-local Playwright session state under `.local/thu-courses/`. It does not read the user's daily Chrome profile cookies.

`auth.py login` uses a project-local persistent Chrome profile at `.local/thu-courses/browser-profile/` so the user can complete SSO in a normal headed browser without touching the daily Chrome profile. `auth.py logout` removes both the exported storage state and this local browser profile.

## Crawl To Parquet

Dry-run without network access:

```bash
uv run python crawlers/thu-courses/crawl_opening_info.py --semester 2026-fall --dry-run
```

Limited smoke crawl after login:

```bash
uv run python crawlers/thu-courses/crawl_opening_info.py \
  --semester 2026-fall \
  --max-pages 1 \
  --detail-limit 3
```

Full crawl:

```bash
uv run python crawlers/thu-courses/crawl_opening_info.py --semester 2026-fall
```

The crawler uses 8 concurrent requests for opening pages and 8 concurrent
requests for linked detail pages by default. Tune these if the system is slow or
unstable:

```bash
uv run python crawlers/thu-courses/crawl_opening_info.py \
  --semester 2026-fall \
  --page-concurrency 8 \
  --detail-concurrency 8
```

The crawler prints progress for opening pages and linked detail pages. Disable
progress output when writing logs or running in a quiet environment:

```bash
uv run python crawlers/thu-courses/crawl_opening_info.py \
  --semester 2026-fall \
  --no-progress
```

Skip linked detail pages and write only opening rows plus detail URLs:

```bash
uv run python crawlers/thu-courses/crawl_opening_info.py \
  --semester 2026-fall \
  --skip-details
```

Semester slug mapping:

- `2026-fall` -> `p_xnxq=2026-2027-1`
- `2026-spring` -> `p_xnxq=2025-2026-2`
- `2026-summer` -> `p_xnxq=2025-2026-3`

## Structured Output

Parquet is the canonical data interface. CSV is intentionally not produced.

- `sections.parquet`: one row per opening table row, including course id, section id, course name, teacher text, credits, department, capacities, remaining seats, schedule text, restrictions/notes, course features, grade, flags, general-education group, and linked detail URLs.
- `course_details.parquet`: one row per unique course detail URL, including course id/name when parseable, credits, description, guidance, prerequisites, teaching features, grading policy, and raw text.
- `teacher_details.parquet`: one row per unique teacher detail URL, including teacher id, name, title, unit, phone, email, profile, research fields, and raw text.
- `experiment_details.parquet`: one row per unique experiment detail URL, including semester/course/section identifiers when parseable and raw text.
- `manifest.json`: run metadata, output paths, request summary, and non-sensitive errors.

Raw HTML snapshots are retained under `data/raw/` only to make parser debugging reproducible. Metadata files include source, semester, fetch time, URL, method, status code, content type, and notes. They must not contain cookies, authorization headers, CSRF values, or sensitive headers.

## Known THU Flow

These findings came from an authenticated, user-operated browser session on May 22, 2026.

- Course-selection login bridge: `https://zhjwxk.cic.tsinghua.edu.cn/xklogin.do`.
- The bridge redirects to `id.tsinghua.edu.cn` for the `综合教务系统` SSO login.
- Best post-login landing page: `https://zhjwxk.cic.tsinghua.edu.cn/xkBks.vxkBksXkbBs.do?m=main`, titled `本科生选课`.
- The main page is an old frameset with `top`, `tree`, and `right` frames.
- The left menu lists semesters, including observed values such as `2026-2027学年-秋` and `2025-2026学年-春`.
- The read-only course-opening page is `开课信息` -> `一级课开课信息`.
- The entry request is a GET:

```text
https://zhjwxk.cic.tsinghua.edu.cn/xkBks.vxkBksJxjhBs.do?m=kkxxSearch&p_xnxq=<xnxq>&pathContent=一级课开课信息
```

- The response is GBK HTML, not JSON.
- Pagination and queries POST to:

```text
https://zhjwxk.cic.tsinghua.edu.cn/xkBks.vxkBksJxjhBs.do
```

- Required form fields include `m=kkxxSearch`, `page=<n>`, `p_xnxq=<xnxq>`, `pathContent=一级课开课信息`, sorting fields, and a dynamic hidden `token`.
- The crawler extracts the current hidden `token` from the page and does not store token values in tracked files.
- Observed first page for `2026-2027-1`: page 1 of 294, 5,864 records.
- Course names link to course detail pages like `js.vjsKcbBs.do?m=showToXs&p_id=<teacher_id>;<course_id>&kcfldm=001`.
- Teacher names link to teacher detail pages like `xkBks.vxkBksJxjhBs.do?m=showJsDetail&p_jsh=<teacher_id>`.
- Some rows link to experiment detail pages like `xk.xk_syrwb.do?m=show&p_xnxq=<xnxq>&p_kch=<course_id>&p_kxh=<section_id>`.

Observed opening table columns:

- 开课院系
- 课程号
- 课序号
- 课程名
- 学分
- 主讲教师
- 本科生课容量
- 本科生课余量
- 研究生课容量
- 研究生课余量
- 上课时间
- 选课文字说明
- 课程特色
- 年级
- 是否二级课
- 实验信息
- 重修是否允许
- 是否选课
- 通识选修课组

## Remaining Uncertainties

- Whether the Playwright storage state stays valid across days without re-login.
- Whether the hidden `token` is page-scoped, session-scoped, or expires during long crawls.
- Whether every linked detail page has the same HTML shape as sampled pages.
- Whether classroom details require a separate endpoint.
- Whether graduate or second-level course-opening pages use the same form contract.

## Checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```
