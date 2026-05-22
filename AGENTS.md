# AGENTS.md

This file is the project rule SSOT for coding agents and contributors.

## Repository Boundary

- The repository root is a local-first Python workspace for fetching course data and interactively planning schedules.
- `crawlers/` contains independent school-specific crawler backends.
- `crawlers/thu-courses/` is specific to Tsinghua University course data.
- Do not turn this repository into a full course-selection app.
- Do not submit course selections, add/drop courses, join waitlists, confirm enrollment, or perform any state-changing action in school systems.
- Do not create future school backends, parser layers, UI apps, databases, or automation services before they are needed.

## Documentation Boundary

- `README.md`, if present, is only for a short project description and local commands.
- Root `AGENTS.md` is only for contributor and coding-agent rules shared by the whole repository.
- School-specific crawler operating rules belong in that crawler's own `AGENTS.md`.
- `docs/`, if introduced later, stores product, architecture, data-model, and phase design.
- Keep documentation strict, concise, English-only, and split by topic.

## Current Scope

- Current stage: first local crawler backend for Tsinghua course-opening data.
- Allowed scope: browser-assisted authentication, session-state reuse, read-only endpoint inspection, raw audit caching, structured Parquet writing, and parser/normalization work.
- Authenticated raw and processed data are local-only and must stay ignored by git.
- Do not implement enrollment automation, schedule submission, remote sync of authenticated data, hosted services, or credential collection.

## Tooling

- Use `uv` for Python dependencies and `uv.lock`.
- Use Python 3.14.
- Use Ruff for Python linting and formatting.
- Use ty for Python type checking.
- Use pytest for tests.
- Do not introduce poetry, pipenv, black, isort, mypy, pyright, Docker, databases, browser-cookie scraping libraries, background schedulers, or remote telemetry dependencies without an explicit task.

## Style

- Use English for comments, docstrings, documentation, logs, and CLI output.
- Keep comments minimal and about current invariants or boundaries.
- Use 2-space indentation for Python, JSON, YAML, Markdown, and future frontend files.
- Do not auto-wrap or reflow Markdown documents unless the task is documentation cleanup.
- Keep Python formatting under Ruff; `ruff.toml` is the style SSOT.
- Use `py314` as the Python lint target.
- Prefer strict typing where practical.
- Prefer dataclasses and small functions over framework-shaped abstractions until real behavior needs isolation.
- Do not add placeholder abstractions before there is real behavior to isolate.
- Avoid emojis in code, documentation, logs, and UI.
- Prefer clear, boring defaults.
- Keep configuration in the narrowest SSOT file.
- Store examples as example files; store real local secrets and sessions only in ignored local files.

## Data And Auth Safety

- Do not store usernames or passwords in the repository.
- Do not commit cookies, tokens, storage state, session files, HAR files, raw authenticated HTML, raw authenticated JSON, or personal data.
- Only fetch pages and API responses that the logged-in user can normally access.
- Prefer Playwright `storage_state` for browser-assisted local session reuse.
- Session files belong under `.local/`, not beside tracked source files.
- Raw audit snapshots belong under `data/raw/`, not tracked source files.
- Canonical structured datasets belong under `data/processed/`, not tracked source files.
- Metadata files must not include cookies, authorization headers, CSRF values, or sensitive request headers.

## Required Checks

Run these before handing off changes when the relevant dependencies are available:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```

For narrow script-only changes, also run direct smoke checks for the touched CLIs.
