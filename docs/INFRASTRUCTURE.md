# Infrastructure & Data Flow

Written 2026-07-01. Complements `CLAUDE.md` (agent quick-reference) and
`PHASE_HISTORY.md` (how we got here). This is the "how does it actually
run and where does data live" document.

## Runtime topology

Five long-running processes on one Windows machine:

| Process | Start command | Purpose |
|---|---|---|
| PostgreSQL 16 | Docker Compose (`autoapply start`) or Windows service | All persistent state (port 5432) |
| Redis | Docker Compose | Celery broker + result backend + distributed locks + LLM cache |
| Celery worker | `uv run autoapply worker --concurrency 2 --pool threads` | Runs `materials.generate` and maintenance tasks |
| Web (FastAPI) | `uv run autoapply web` | JSON API + serves the built SPA at :8000 |
| Ollama | `ollama serve` | Local LLM. Main: `qwen3.6:35b-a3b`; small tier: `gpt-oss:20b` |

Celery Beat (started by `autoapply start`) drives scheduled maintenance
(cache eviction, artifact cleanup, JD health checks) and automation plans.

Job Pool V2 currently runs as an additive shadow pipeline. One acquisition
snapshot is evaluated against five target specs, then written to immutable
evaluation and portfolio ledgers. In `v2_shadow` it creates no review entries,
materials, applications, or submissions. `/jobs/quality` reads that ledger for
operator audit. `matching.pipeline_version: v1` remains the production
authority until the replay, seven-cycle shadow, blinded-review, canary, and
two-week rollback gates pass.

The SPA is **not** served by a dev server in normal use: `npx vite build`
writes to `src/web/static/spa/` and FastAPI serves those files. No process
hot-reloads — restart web/worker after code changes.

## The three job stores (and why there are three)

1. **`jobs` (legacy table)** — every ATS job that passes a filter profile is
   persisted here by `src/intake/storage.py::persist_and_sync_ids`, which
   also rewrites the in-memory `RawJob.id` to the stable DB primary key.
   This is what the Job Database view (`/jobs-db`) browses.
2. **Job Index (`job_postings`, `job_snapshots`, `search_queries`,
   `search_results`)** — Phase 13 per-search cache with freshness tracking.
   `src/jobs/search.py::cached_search` is the only entry point: cache-hit
   returns persisted postings; miss takes a Redis distributed lock, runs the
   scrape, re-links results, and prunes links not seen in the latest run.
   Snapshots keep the JD text the materials were generated against.
3. **`applications` + `review_queue`** — workflow state. `review_queue`
   rows are created by plan runs (or the Job Database batch-generate) and
   carry denormalized company/title plus `materials_path`, so the kanban
   survives retention sweeps of the job rows.

## Cache layers (fastest to slowest)

| Layer | Where | TTL / scope | Bypass |
|---|---|---|---|
| Frontend fetch-signature reuse | `JobsView.vue` | until search params change | "Refresh results" button |
| ATS board cache | `src/intake/search.py` in-process dict | 15 min per (ats, slug, parse_jds); deep-copied both directions | `force_refresh=True` (wired from the Refresh button) |
| Job Index search cache | Postgres via `cached_search` | `search_cache.ttl_hours` (24h) per normalized query; LinkedIn only today | `force_refresh` |
| LLM response cache | Redis (`src/cache`) | provider-level | n/a |

Greenhouse/Lever board APIs have **no server-side filtering** — a fetch is
always the whole board (`?content=true`, includes full JDs). Region
narrowing is therefore always client-side; see the location-matching
invariant in `CLAUDE.md`.

## Live search request flow (Jobs tab)

```
POST /api/jobs/search
  └─ application.jobs.search_jobs()
       ├─ ATS: intake.search.search_jobs — board cache → parallel board
       │        fetches (ThreadPool ≤8) → enrich_requirements (regex JD parse)
       │        → filter profile (if named) → persist_and_sync_ids
       ├─ LinkedIn: per-location cached_search(Job Index) → Playwright scrape
       ├─ cross-source dedupe on (company, title, location) — ATS copy wins
       ├─ _prepare_jobs_for_search_filters (classify level/type/pay/education)
       ├─ _score_jobs against active applicant profile (match_score,
       │        disqualified, score_breakdown into raw_data)
       ├─ _apply_search_filters (locations use whole-word matcher;
       │        LinkedIn-sourced jobs skip location when geo-searched)
       └─ sort via _job_sort_key → serialize → {jobs, views, counts, errors}
```

The frontend re-filters `views.fetched` client-side (`matchesLocalFilters`)
so filter tweaks don't refetch; that logic mirrors the backend and must be
kept in sync.

## Materials generation flow

```
Plan run (orchestration/plan_run.py)          Job Database view (/jobs-db)
  search → score → top-N                        user selects stored jobs
        └────────────┬───────────────────────────────┘
                     ▼
        review.create_entry (pending row, idempotent per job+snapshot)
                     ▼
        celery send_task("materials.generate", {job_id, document_types})
                     ▼
        tasks.py materials_generate:
          load JobPosting → snapshot (or legacy Job fallback) →
          generate_material_for_job() per document type
            resume:  JD tag extraction → evidence/bullet selection from
                     bullet pool → optional per-bullet LLM keyword rewrite
                     (bounded concurrency, fact-drift + length guards) →
                     docx/LaTeX render → trim loop to page target → validate
            cover:   structure-constrained LLM draft (template fallback) →
                     length re-ask (≤1) → deterministic paragraph drop →
                     render + validate
          artifact paths → TaskRecord.result + pending review entry
                     ▼
        /materials kanban previews artifacts; approve/reject; submissions
        stay gated behind the pre-submit freshness check
```

Files land in `data/output/` named
`{type}_{company}_{role}_{date}` per `documents.naming_pattern`.

## LLM policy

`config/settings.yaml`: primary provider `ollama`, no fallback, 300s
timeout, global concurrency 1 (`parallelism.llm.max_concurrent_global`) —
one GPU, one request at a time. The provider registry
(Settings → Providers) supports OpenAI/Anthropic/DeepSeek/etc. per-provider
models; `small_model` is the cheap tier for classification-ish calls.
If a cloud provider is enabled for generation, raise the parallelism caps —
they exist to protect the local GPU, not the API.

## Operational gotchas

- **`localhost` hangs; `127.0.0.1` works.** Windows resolves `localhost`
  to IPv6 `::1` first, and Docker Desktop's port proxy accepts-then-stalls
  those connections instead of refusing them. psycopg has no default
  connect timeout, so any component configured with `localhost` blocks
  forever. `config/settings.yaml` and `.env` pin `127.0.0.1`; keep it
  that way (and pass `connect_args={"connect_timeout": 5}` in ad-hoc
  scripts).
- Postgres down ⇒ DB-backed tests and API calls **hang** (no fast failure).
  `Test-NetConnection 127.0.0.1 -Port 5432` before debugging "slow" anything.
- Ollama running changes test behavior: anything that reaches the real
  LLM does slow inference instead of failing fast to a fallback path.
  Tests must patch `generate_json`/`generate_text` (or the specific
  rewriter); `test_generation.py` had one that didn't and looked like a
  suite hang.
- Board location labels are coarse: Stripe's Greenhouse says "US",
  "Ireland Locations", "Japan Locations" — city-level filters cannot
  match them. Combine a city chip with "united states"/"remote".
- Two pytest runs sharing the DB deadlock each other; run one at a time.
- Celery tasks import code at worker start; a stale worker silently runs old
  logic after you edit generation code.
- `.venv` on Windows is managed by uv — never `pip install` into it directly.
- Alembic head as of 2026-07-01: `b8d2f9e15c33` (Phase 18.3 DLQ columns).
- The `jobs-db` batch-generate cap is 50 per request; with local Ollama at
  concurrency 1 each job costs ~2–5 min, so a full batch is hours — that is
  a GPU limit, not a bug.

## 2026-07-01 maintenance session (summary)

Fixed: region leak (frontend skipped the location filter for ATS jobs when
LinkedIn was in the source mix), substring location matching ("ny" ⊂
"Germany"), fragile/unsorted result ordering, silent scoring failures.
Added: whole-word + alias location matcher (both stacks), deterministic
sort + UI sort control, parallel board fetches, 15-min board TTL cache,
cross-source dedupe, warn-on-missing-profile in the search route, and the
Job Database view (`/jobs-db`: filter stored jobs by location/type/
seniority/source, select, batch-generate materials into the review flow).
Full detail: `docs/CHANGELOG.md` "Maintenance session (2026-07-01)".
