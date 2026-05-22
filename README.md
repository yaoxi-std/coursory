# Coursory

Local-first course data fetching and interactive schedule-planning workspace.

## THU Crawler

```bash
uv sync
uv run python crawlers/thu-courses/auth.py login
uv run python crawlers/thu-courses/auth.py status
uv run python crawlers/thu-courses/crawl_opening_info.py --semester 2026-fall --dry-run
```

See `crawlers/thu-courses/README.md` for the read-only authentication and Parquet crawl workflow.

## Course Planning Runtime

Start user-facing planning sessions from `course-planning/` so the runtime
agent rules are auto-loaded:

```bash
cd course-planning
```

See `course-planning/README.md` and `course-planning/AGENTS.md`.
