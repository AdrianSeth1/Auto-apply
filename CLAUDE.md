# CLAUDE.md — AutoApply agent guide

Read this before touching anything. It exists so an agent with zero context
doesn't re-derive (or re-break) the things below.

## What this is

Local-first job-application automation for a single user (Arya). Vue 3 SPA +
FastAPI backend + PostgreSQL + Redis/Celery workers + local Ollama LLM.
Searches job sources, scores fit against applicant profiles, generates
tailored resumes/cover letters, and gates every actual submission behind
human review. **Never auto-submit anything without an explicit approved
review-queue transition.**

## Environment (this machine)

- Windows; project at `C:\Users\aryam\AutoApply`. Python **3.12+ required**
  (`datetime.UTC` etc.) — run everything through `uv run`, never bare
  `python`/`pip`.
- Services (see `AutoApply_Runbook.xlsx` for the daily startup sequence):
  PostgreSQL + Redis (Docker Compose or Windows services), Celery worker
  (`uv run autoapply worker --concurrency 2 --pool threads`), web
  (`uv run autoapply web` → http://localhost:8000), Ollama
  (`ollama serve`, main model `qwen3.6:35b-a3b`, small tier `gpt-oss:20b`).
- **The web server does not hot-reload.** After backend changes restart it;
  after frontend changes run `npx vite build` in `frontend/` (output goes to
  `src/web/static/spa/`, which FastAPI serves).
- Celery workers only pick up code at start — restart the worker too.

## Commands

```powershell
uv run pytest -q                      # full suite — needs Postgres+Redis UP
uv run pytest tests/test_web.py -q    # route tests — no live services needed
cd frontend; npx vite build           # rebuild SPA (npm install --include=dev first time)
uv run autoapply start --check        # print startup plan without starting
uv run alembic upgrade head           # migrations (alembic.ini at repo root)
```

Known pre-existing failure: `test_filters.py::test_loads_real_config`
expects a `default` profile in `config/filters.yaml`; the user's config has
only their custom profiles. Not a regression — leave it or add a `default`.
DB-backed tests (gate/tasks/review suites) **hang, not fail**, when Postgres
is down; check the port before blaming your diff.

## Architecture map

```
src/intake/       scrapers: greenhouse.py, lever.py (public board APIs — they
                  return the ENTIRE board, no server-side filtering exists),
                  linkedin.py (Playwright), filters.py (YAML filter profiles),
                  search.py (board fetch + 15-min TTL board cache, ThreadPool)
src/jobs/         Job Index: search.py cached_search (per-query cache w/
                  distributed lock), store.py (JobPosting/JobSnapshot/SearchQuery)
src/application/  use cases shared by CLI + web:
                  jobs.py (live search/filter/score — the big one),
                  job_database.py (browse persisted jobs + batch generate),
                  review.py (review-queue CRUD + state machine)
src/matching/     scorer.py — rule + keyword scoring, ScoreBreakdown explainability
src/generation/   resume_builder.py (block assembly from bullet pool, NOT
                  full-text LLM), cover_letter.py (structure-constrained),
                  validator.py + fact_drift.py (anti-hallucination checks)
src/tasks/        Celery: tasks.py `materials.generate` is the generation
                  entrypoint; writes artifact paths onto pending review entries
src/orchestration/plan_run.py — automation plans: search→score→top-N→
                  create review entries→enqueue materials.generate
src/web/routes/   api.py (main JSON API), review.py, tasks.py, agent.py
frontend/src/     Vue 3 SPA; views/JobsView.vue (live search),
                  views/JobDatabaseView.vue (stored jobs), lib/api.js
config/           settings.yaml, companies.yaml (board slugs),
                  filters.yaml (filter profiles), profiles/*.yaml (matching)
data/output/      generated .docx/.pdf artifacts
```

Job data lives in TWO tables: legacy `jobs` (ATS batch persists here via
`persist_and_sync_ids`) and `job_postings`/`job_snapshots` (Job Index).
`materials.generate` accepts either id and falls back legacy→index.

## Invariants — do not break these

1. **Location matching is whole-word + alias, in two places that MUST stay
   in sync**: `_matches_locations` in `src/application/jobs.py` and
   `matchesLocations` in `frontend/src/views/JobsView.vue` (also imported by
   `job_database.py`). It replaced raw substring matching, which matched
   "ny" inside "Germany" and "us" inside "Australia" and flooded results
   with wrong-region jobs. Two-letter US state codes only match in the
   `", XX"` form. If you change one side, change the other.
2. **Only LinkedIn-sourced jobs may skip the location filter** (LinkedIn
   geo-filters server-side). ATS jobs must ALWAYS pass the location check —
   the boards return every job worldwide. This was regressed once by
   skipping the filter for all jobs whenever the source included LinkedIn.
3. **Result ordering** comes from `_job_sort_key` (score desc, then
   company/title tie-break). The frontend additionally has a user-facing
   sort (`sortMode` in `jobs-state.js`). Don't reintroduce
   `raw_data.get("match_score", 0.0)` sorts — stored `None` crashes them.
4. **Caches hold deep copies.** The board cache in `src/intake/search.py`
   copies on write AND read because downstream mutates RawJob in place
   (scores in `raw_data`, DB id rewrites). Returning shared objects causes
   cross-search state leaks.
5. Generation is **evidence-grounded**: bullets come from the user's bullet
   pool and get at most keyword-injection rewrites with fact-drift checks.
   Never replace this with free-form LLM resume writing.
6. `search_results` pruning, review-queue idempotency (partial unique index
   on pending), and the pre-submit freshness gate are load-bearing —
   read the docstrings in those modules before "simplifying".
7. **String comparisons against DB columns must be case-insensitive**
   (`func.lower(...)`) when the Python side normalizes to lowercase. A
   case-sensitive company check in `src/intake/storage.py` silently
   matched nothing and duplicated the entire jobs table 13× before it
   was caught. There is no unique index on (source, source_id) yet.
8. **Keywords narrow ATS results too** (title/description contains any
   keyword, in `search_jobs`). The boards have no server-side search, so
   removing this re-creates "every Stripe role worldwide" results.
9. **Use `127.0.0.1`, never `localhost`, for DB/Redis hosts** on this
   machine — IPv6-first resolution + Docker Desktop port proxying makes
   `localhost` connections hang forever instead of failing fast.

## Conventions

- Use cases live in `src/application/`, take/return plain dicts, and are
  imported lazily inside route handlers (patch-friendly for tests).
- Tests patch use cases at their module path
  (`@patch("src.application.x.y")`) with `fastapi.testclient`.
- `_normalize_list` lowercases filter inputs at the API boundary.
- Frontend state that should survive reloads goes through
  `lib/jobs-state.js` → localStorage, key `autoapply.jobs.state`.
- Docs: engineering log in `docs/CHANGELOG.md`, decisions in
  `docs/DECISIONS.md`, infra topology in `docs/INFRASTRUCTURE.md`.

## History / context

Phases 1–18 complete (see `docs/PHASE_HISTORY.md`). 2026-07-01 session:
fixed region-leak + substring location bugs, unsorted results, added board
TTL cache, cross-source dedupe, sort control, and the Job Database view
(`/jobs-db`) — details in `docs/CHANGELOG.md` under "Maintenance session".
