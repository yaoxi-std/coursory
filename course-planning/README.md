# Course Planning Runtime

This directory is the user-facing Codex workspace for interactive course
planning. Start Codex here so `course-planning/AGENTS.md` is auto-loaded.

The runtime flow is:

1. Confirm the user wants Tsinghua course planning.
2. Confirm the semester, for example `2026-fall`.
3. Check for processed Parquet data under `data/processed/thu-courses/`.
4. If data is missing, run the THU auth and crawl workflow.
5. Load local preferences and term planning state from `.local/course-planning/`.
6. Help the user shortlist, select, reject, and revise courses.
7. Persist confirmed preferences and planning decisions locally.

Runtime state is local-only and ignored by git:

```text
.local/course-planning/
  profile.json
  terms/
    <semester>/
      plan.json
      notes.md
```

This workspace is not an enrollment tool. It must not submit, add, drop,
waitlist, or confirm courses.
