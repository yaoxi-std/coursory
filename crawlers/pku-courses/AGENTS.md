# AGENTS.md

This file defines PKU-specific crawler rules for `crawlers/pku-courses/`.

## Scope

- This crawler fetches public PKU course-search data for local analysis.
- PKU course search does not require login; it only gates searches with image captchas.
- Fetching is read-only.
- The public search page may require a user-entered image captcha.
- Do not bypass captchas; save the captcha image locally and ask the user to enter it.

## Crawl Workflow

- Use `uv run python crawlers/pku-courses/crawl_course_search.py --semester <slug>`.
- The crawler must use the public course-search form contract from `courseSearch.php`.
- Treat the web page's `Read More` / remaining-course button as repeated `courseSearch_do.php` POSTs with an increasing `startrow`.
- It should crawl course list rows first, then unique course-detail pages.
- Keep polite pacing and fail with actionable messages when the captcha or page shape changes.

## Forbidden Actions

- Do not add, drop, select, confirm, submit, or waitlist courses.
- Do not click buttons whose effect may change enrollment state.
- Do not bypass captchas, authorization, VPN controls, or browser safety warnings.
- Do not scrape beyond public or normally accessible pages.
- Do not send raw data to remote services.
- Do not commit `.local/`, `data/raw/`, cookies, tokens, captcha images, screenshots, or raw responses.

## Data Output Rules

- Canonical structured data is Parquet under `data/processed/pku-courses/<semester>/<run_id>/`.
- Raw JSON and HTML are only an ignored audit/debug cache under `data/raw/pku-courses/<semester>/<run_id>/`.
- Produce `sections.parquet`, `course_details.parquet`, `teacher_details.parquet`, `detail_links.parquet`, and `manifest.json`.
- Metadata must include source, semester, fetched time, URL, method, status code, content type, and notes.
- Metadata must not include cookies, session ids, captcha values, or sensitive request headers.

## Checks

From the repository root, run:

```bash
uv run ruff format --check crawlers/pku-courses
uv run ruff check crawlers/pku-courses
uv run ty check
uv run pytest
```

For crawler workflow changes, also smoke-test:

```bash
uv run python crawlers/pku-courses/crawl_course_search.py --semester 2026-spring --dry-run
```
