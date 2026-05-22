# AGENTS.md

This file defines Tsinghua-specific crawler rules for `crawlers/thu-courses/`.

## Scope

- This crawler fetches Tsinghua course-opening data for local analysis.
- Authentication is browser-assisted and user-operated.
- Fetching is read-only.
- Authentication, crawling, and parsing are separate modules.

## Login Workflow

- Start with `uv run python crawlers/thu-courses/auth.py login`.
- When the browser reaches a credential, captcha, QR, OTP, or identity-verification step, stop and ask the local user to operate it.
- After the user completes login, continue from the authenticated browser/session.
- Save only the minimum reusable local session state required for later read-only fetches.
- Prefer `.local/thu-courses/storage_state.json` for Playwright session reuse.
- If inspecting session state is necessary for debugging, inspect only local generated session files and never print raw cookie values in final answers.

## Crawl Workflow

- Use `uv run python crawlers/thu-courses/crawl_opening_info.py --semester <slug>`.
- The crawler must use the known read-only opening-info form contract and extract the current hidden `token` from the page before POST pagination.
- It should crawl opening rows first, then unique course, teacher, and experiment detail links.
- Keep polite pacing and fail with actionable messages when authentication or page shape changes.
- If live behavior differs, debug in an authenticated browser session and update README findings before changing parser assumptions.

## Forbidden Actions

- Do not add, drop, select, confirm, submit, or waitlist courses.
- Do not click buttons whose effect may change enrollment state.
- Do not bypass authentication, captchas, authorization, VPN controls, or browser safety warnings.
- Do not scrape beyond what the logged-in user can access normally.
- Do not send authenticated raw data to remote services.
- Do not commit `.local/`, `data/raw/`, cookies, tokens, storage states, HAR files, screenshots of personal pages, or raw authenticated responses.

## Data Output Rules

- Canonical structured data is Parquet under `data/processed/thu-courses/<semester>/<run_id>/`.
- Raw HTML is only an ignored audit/debug cache under `data/raw/thu-courses/<semester>/<run_id>/`.
- Produce `sections.parquet`, `course_details.parquet`, `teacher_details.parquet`, `experiment_details.parquet`, and `manifest.json`.
- Metadata must include source, semester, fetched time, URL, method, status code, content type, and notes.
- Metadata must not include cookies, authorization headers, CSRF values, or sensitive headers.

## Checks

From the repository root, run:

```bash
uv run ruff format --check crawlers/thu-courses
uv run ruff check crawlers/thu-courses
uv run ty check
uv run pytest
```

For crawler workflow changes, also smoke-test:

```bash
uv run python crawlers/thu-courses/auth.py --help
uv run python crawlers/thu-courses/crawl_opening_info.py --semester 2026-spring --dry-run
```
