# PKU Course Crawler

This backend fetches public PKU undergraduate course-search data for local analysis. It does not log in; the public search page only requires image captchas before searches. It is read-only and must not perform any enrollment-changing action.

The normal workflow is:

1. Open a public, anonymous course-search session.
2. If the site asks for an image captcha, save it under `.local/pku-courses/` and ask the local user to enter the code.
3. Crawl list pages from `courseSearch_do.php`.
4. Crawl linked `courseDetail.php` pages.
5. Write canonical structured data as Parquet.

## Files

```text
crawlers/pku-courses/
  crawl_course_search.py
  course_parser.py
  parquet_store.py
  pku_common.py
  AGENTS.md
  README.md
```

Local state and outputs are ignored by git:

```text
.local/pku-courses/
  captcha.png

data/raw/pku-courses/<semester>/<run_id>/
  <raw JSON, raw HTML, and metadata, audit/debug only>

data/processed/pku-courses/<semester>/<run_id>/
  sections.parquet
  detail_links.parquet
  course_details.parquet
  teacher_details.parquet
  manifest.json
```

The course-search page displays only the first chunk of matching rows, then uses a `[剩余 ... 条数据未显示] Read More` button. The crawler does not click that button in a browser; it performs the same read-only operation directly by repeating `courseSearch_do.php` with `startrow` advanced by the number of rows already returned.

## Crawl To Parquet

Dry-run without network access:

```bash
uv run python crawlers/pku-courses/crawl_course_search.py --semester 2026-spring --dry-run
```

Limited smoke crawl:

```bash
uv run python crawlers/pku-courses/crawl_course_search.py \
  --semester 2026-spring \
  --max-pages 1 \
  --detail-limit 3
```

Full crawl:

```bash
uv run python crawlers/pku-courses/crawl_course_search.py --semester 2026-spring
```

When a captcha is required, the script prints the local image path and prompts for the code. If running through an agent or non-interactive shell, prepare the captcha first:

```bash
uv run python crawlers/pku-courses/crawl_course_search.py \
  --semester 2026-spring \
  --prepare-captcha
```

Then open `.local/pku-courses/captcha.png`, read the code, and continue the same saved anonymous public session:

```bash
uv run python crawlers/pku-courses/crawl_course_search.py \
  --semester 2026-spring \
  --resume-session \
  --captcha-code abcd
```

The site may ask for a new captcha after enough visible `Read More` checks in the browser. The crawler only performs the captcha check before the first search request, then continues pagination directly with `courseSearch_do.php` and increasing `startrow`.

Useful filters:

```bash
uv run python crawlers/pku-courses/crawl_course_search.py \
  --semester 2026-spring \
  --department 00001 \
  --course-type 0
```

Semester slug mapping:

- `2025-fall` -> `yearandseme=25-26-1`
- `2026-spring` -> `yearandseme=25-26-2`
- `2026-summer` -> `yearandseme=25-26-3`
- `2026-fall` -> `yearandseme=26-27-1`

## Structured Output

- `sections.parquet`: one row per course-search row, including course id/name, department, course type, class number, credits, teaching weeks, schedule text, teacher text, remarks, PKU `zxjhbh`, and linked course-detail URL.
- `detail_links.parquet`: one row per unique course-detail URL selected for crawling.
- `course_details.parquet`: one row per unique course-detail URL, including course id/name, credits, prerequisites, department, Chinese introduction, English introduction, and raw text.
- `teacher_details.parquet`: one row per distinct teacher display name from the public list. The observed public course-search page does not expose a teacher-profile detail URL; this table keeps the normalized teacher text, source course ids, and a nullable profile field for future endpoint findings.
- `manifest.json`: run metadata, output paths, request summary, and non-sensitive errors.

## Known PKU Flow

These findings came from a public, read-only browser/source inspection on May 25, 2026.

- Course-search page: `https://dean.pku.edu.cn/service/web/courseSearch.php`.
- Captcha image endpoint: `course_vercode.php`.
- Captcha status endpoint: `course_do.php?act=checkSearch`.
- Captcha verification endpoint: `course_do.php?act=checkVercode`, with POST field `code`.
- List endpoint: `courseSearch_do.php`, with POST fields `coursename`, `teachername`, `yearandseme`, `coursetype`, `yuanxi`, and `startrow`.
- The page's `Read More` button first calls `course_do.php?act=checkSearch`, then calls the same list endpoint with `startrow` equal to the current number of displayed rows. The crawler performs the initial captcha check, then continues the read-only `courseSearch_do.php` pagination directly. Observed list pages return 10 rows per request.
- List responses are JSON. Observed rows include `xh`, `kch`, `kcmc`, `kctxm`, `kkxsmc`, `jxbh`, `xf`, `zxjhbh`, `qzz`, `sksj`, `teacher`, and `bz`.
- Course detail pages are linked as `courseDetail.php?flag=1&zxjhbh=<zxjhbh>`.
- Observed public course detail fields include course title, course id, credits, prerequisites, department, Chinese introduction, and English introduction.
- The observed public list and detail pages did not expose teacher-profile detail links.

## Checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```
