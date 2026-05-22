# Coursory

English / [简体中文](README.zh.md)

Local-first course data fetching and interactive schedule-planning workspace.

Coursory is meant to be used with a local coding agent. The crawler scripts and
Parquet files are implementation details for the agent; as a user, you normally
start a conversation and let the agent follow the project rules.

## Start Here

1. Clone this repository.
2. Install the local toolchain, including `uv` and Python 3.14.
3. Open Codex, or another local coding agent configured to read `AGENTS.md`.
   Good fits include Codex Desktop, Codex CLI, or any comparable agent where you
   can point it at this repository and have it load repository instructions.
4. In the agent session, `cd` to the repository root.
5. Start with a plain-language request, for example:

```text
I am a Tsinghua University undergraduate student in the <department> department,
class of <year>. Please use this repository to help me plan my course schedule.
```

The agent should first confirm that you are planning with Tsinghua University
course data, confirm the semester, and check whether local course-opening data
already exists. If data is missing or stale, it will follow the project rules to
open the Tsinghua login flow, ask you to log in manually, and then fetch the
read-only course-opening data.

The first crawl may take around 10 minutes. It writes local Parquet datasets
under `data/processed/` and raw ignored audit cache under `data/raw/`.

## What The Agent Will Do

After the data is available, continue planning by telling the agent your
constraints and preferences. A useful order is:

1. Required courses: major requirements, politics courses, PE, English, labs,
   graduation requirements, or anything your department expects you to take.
2. Hard constraints: time blocks you cannot use, campus/location preferences,
   credit limits, workload limits, exam conflicts, or instructors you prefer.
3. Interests: topics you want to explore, general education areas, seminar
   styles, project-heavy or exam-light preferences, and backup options.

The agent can write local Python scripts that use Polars to read the Parquet
course data, filter candidates, compare sections, and check schedule conflicts.
It can then use its language-model reasoning to help with the softer planning
work: explaining tradeoffs, narrowing choices, drafting alternative schedules,
and keeping track of what you have already decided.

Confirmed preferences and selected/planned courses should be persisted in local
ignored files under `.local/course-planning/` so future sessions can continue
from the same context without committing personal planning data.

## Important Boundary

**Coursory is only for course data fetching, analysis, and schedule planning.**

**It must not submit course selections, add or drop courses, join waitlists,
confirm enrollment, or perform any other state-changing action in Tsinghua
systems.** You remain responsible for all official course-selection operations
in the official university system.

## Manual Commands

Most users should let the agent run these when needed, but the main THU crawler
commands are:

```bash
uv sync
uv run python crawlers/thu-courses/auth.py login
uv run python crawlers/thu-courses/auth.py status
uv run python crawlers/thu-courses/crawl_opening_info.py --semester 2026-fall
```

See `crawlers/thu-courses/README.md` for the read-only authentication and
Parquet crawl workflow.

## Runtime Rules

For end-user course planning, the agent should use the runtime rules in:

```bash
cd course-planning
```

See `course-planning/README.md` and `course-planning/AGENTS.md`. Repository
development rules live in the root `AGENTS.md`; those are for changing the code,
not for ordinary course-planning conversations.

## Acknowledgements

Thanks to Codex. This project was co-authored by the maintainer and Codex in one
half-afternoon working session.
