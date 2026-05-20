# AutoApply — Full Project Plan

This document is the long-form planning reference for AutoApply. It preserves
strategy, historical roadmap context, and phase rationale. It is not the best
place for quick-start instructions or current verification status.

To reduce duplication, use the following source-of-truth split:

| Topic | Authoritative source |
|---|---|
| Current status, next roadmap, verification | `docs/PROJECT_MANAGEMENT.md` |
| Shipped phase archive | `docs/PHASE_HISTORY.md` |
| Why we chose / rejected each design | `docs/DECISIONS.md` |
| Agent harness internals | `docs/AGENT_ARCHITECTURE.md` |
| User-facing setup | `docs/DEPLOYMENT.md` |
| This file | Strategy, historical roadmap context, long-form planning notes |

Last refreshed: **2026-05-19 (Phase 17.9 documentation sync)**. Current operating status, verification, and next roadmap live in `docs/PROJECT_MANAGEMENT.md`. This plan keeps the long-form rationale. v3.1 calibrated the roadmap in four places:
(a) Phase 14 task queue switches to Celery (the original "self-built task model +
queue transport + worker runtime" plan is dropped; see D025). APScheduler is
retired in favor of Celery Beat for cron triggers.
(b) A new Phase 13.9 sub-phase lands before Phase 14 starts: one-shot
`tenant_id` retrofit migration for every legacy table from Phase 11 and earlier,
turning D020's discipline into a schema-level guarantee (see D026).
(c) The HITL gate backend moves from single-process file JSON to a
Postgres-backed table that lives alongside the Celery task state (folded into
Phase 14), so Phase 14 multi-worker and Phase 17 review queue don't each
reinvent it (see D026).
(d) Phase 15.3 LaTeX scope is clarified: `src/documents/latex_engine.py` already
exists, so Phase 15 is not "build LaTeX from scratch" — it is "add the template
package spec + manifest + adapter convention on top of an existing engine."

The 2026-05-19 refresh records Phase 17.9 as complete: the provider layer now covers OpenAI, Anthropic, Gemini, DeepSeek, Moonshot/Kimi, Qwen, xAI Grok, Groq, Mistral, OpenRouter, Ollama, Claude CLI, Codex CLI, and user-defined OpenAI-compatible providers. The same refresh inserted **Phase 19** (Per-Posting Tag Cache & Filter Fast Path — reinstating the 2026-05-16 cache plan) and **Phase 20** (Custom Job Sources / Connectors) between Phase 18 (worker activation / reliability / parallelism / cleanup) and the multi-tenancy work, which now lands as **Phase 21**.

---

## 1. Goal

Build an end-to-end job-application automation system covering seven
capability layers: job intake & filtering, applicant memory, resume &
cover-letter tailoring, quick-question response, document processing,
form-filling automation, and tracking / analytics.

Commercial ambition has been preserved since the 2026-05-12 v2 re-plan
and sharpened in the 2026-05-14 v3 update: multi-tenancy, Redis-backed
cache/queue transport, distributed locks, per-tenant quotas, Postgres
RLS, and a background worker model are all in the roadmap, even though
no SaaS business layer is on the table yet.

## 2. Design Principles

1. **State machine-driven.** Every application is a state machine —
   interruptible, resumable, auditable.
2. **Evidence-grounded materials.** No full-text LLM rewrite. Agents select
   from profile facts, story bank, and tagged bullet pools, optionally apply
   light lexical rewrite, and pass through fact-drift guards.
3. **Two resume paths.** Patch the user's original editable source when the
   goal is to preserve their existing style; use LaTeX-first template
   packages when generating a new resume from scratch. In both paths, LLMs
   produce structured IR or adapter proposals; deterministic renderers own
   final files.
4. **Human-in-the-loop on every submit.** Default pauses before submit;
   `--auto-submit` is an opt-in escape hatch that still passes through the
   gate queue.
5. **Full audit trail.** Screenshots, DOM snapshots, file versions, QA
   responses are all persisted. Phase 13 extends this with content-hashed
   JD snapshots so we know forever which JD version a given letter / resume
   was generated against.
6. **Provider-agnostic LLM.** No subprocess- or REST-specific code outside
   `src/providers/`. Every call site uses `generate_text()`.
7. **Queue-managed automation.** Background tasks own scheduling, retry,
   idempotency, and worker lifecycle. Agents run inside one bounded task and
   return structured outcomes; they do not own queue ack/nack or global
   orchestration.

## 3. Tech Stack

| Layer | Technology | Rationale |
|---|---|---|
| Language / runtime | Python 3.12+, `uv` | Standard async + typing baseline |
| Backend | FastAPI + Click CLI (`autoapply`) | Single codebase serves web + CLI |
| Frontend | Vue 3 + Vue Router + Vite + Tailwind v3 + shadcn-vue + reka-ui | See D015 |
| Browser automation | Playwright (Python, async) | Full DOM access + persistent context for LinkedIn login |
| LLM providers | OpenAI / Anthropic / Gemini plus DeepSeek, Moonshot/Kimi, Qwen, xAI Grok, Groq, Mistral, OpenRouter, Ollama, Claude Code CLI, Codex CLI, and user-defined OpenAI-compatible providers — all behind `ProviderRegistry` | See D016 and Phase 17.9 |
| Agent harness | In-house ReAct loop in `src/agent/` — bounded steps, allow-listed `ToolRegistry`, Postgres-backed HITL for task flows, JSON-on-disk trace store, fixture-driven eval | See D017 (no LangChain / LangGraph) |
| Database (source of truth) | PostgreSQL + pgvector + alembic | Vector search for matching; alembic for schema migrations |
| Cache / lock / queue (Phase 12+) | Redis 7+ | L2 cache, distributed lock primitive (`SET NX PX`), task queue substrate; see D018 |
| Task queue / scheduler (Phase 14+) | Celery 5.x + Redis broker + Redis result backend + Celery Beat (cron trigger) | See D025 (replaces the original "self-built queue + APScheduler" plan); D023's agent/queue boundary principle is retained |
| Document processing | python-docx + LaTeX toolchain + docx2pdf / LibreOffice | DOCX patching for original resumes; LaTeX-first for newly generated resumes; PDF as derived output |
| Config | YAML (`config/settings.yaml`, `config/filters.yaml`, `config/companies.yaml`) + `.env` overrides | Defaults → file → env, with credential URL encoding |
| Target ATS platforms | Greenhouse / Lever / Ashby; LinkedIn for discovery | Direct-apply for the first three; LinkedIn auth via Playwright persistent context |

## 4. Code Layout (actual, not aspirational)

```
src/
├── core/                # Config loader, DB session, ORM models, state machine
├── agent/               # In-house agent harness
│   ├── tools/           #   tool ABC + builtin / browser / profile tools
│   ├── core/            #   bounded ReAct loop + cost telemetry
│   ├── gate/            #   legacy local gate helpers; task flows use Postgres gate_queue
│   ├── trace/           #   JSON-on-disk trace store
│   └── eval/            #   fixture-driven eval runner + scorers
├── providers/           # LLM provider abstraction
│   ├── base.py          #   LLMProvider ABC + ProviderKind + AuthType
│   ├── openai.py / anthropic.py / gemini.py   # first-party REST adapters
│   ├── deepseek.py / moonshot.py / qwen.py / xai.py / groq.py / mistral.py / openrouter.py / ollama.py
│   ├── claude_cli.py / codex.py               # subprocess adapters
│   ├── api_base.py      #   shared REST helpers
│   ├── store.py         #   credential storage (0600 file + OS keyring fallback)
│   └── registry.py      #   primary / fallback dispatch into generate_text
├── intake/              # Job scraping & schema
│   ├── greenhouse.py / lever.py / linkedin.py # Adapters
│   ├── schema.py        #   RawJob / JobRequirements / employment-type classifiers
│   ├── jd_parser.py     #   LLM-assisted parsing + regex fallback
│   ├── batch.py / search.py / storage.py
│   ├── filters.py       #   YAML-driven filter profiles
│   └── search_cache.py  #   File-backed JSON cache (slated for Phase 13.8 removal)
├── matching/            # Filtering & scoring
│   ├── rules.py         #   Hard rules (authorization, experience, education, ...)
│   ├── semantic.py      #   Embedding + TF-similarity scoring
│   └── scorer.py        #   Composite scorer + quality multiplier
├── memory/              # Applicant memory
│   ├── profile.py       #   Identity / education / skills / experiences / projects
│   ├── bullet_pool.py   #   Tagged bullets with usage counters
│   ├── story_bank.py    #   STAR stories with theme tags
│   ├── qa_bank.py       #   Question patterns + canonical answers + variants
│   └── resume_importer.py # PDF/DOCX → Claude CLI → structured YAML
├── generation/          # Resume + cover letter + QA
│   ├── ir.py            #   Resume / cover letter IR
│   ├── resume_builder.py
│   ├── cover_letter.py
│   ├── fitting.py       #   Template capacity-aware fitting
│   ├── validator.py     #   Artifact validation (page count, length)
│   └── qa_responder.py  #   Classifier + cascading answer generator
├── execution/           # Browser automation + form fill + submit
│   ├── browser.py       #   Playwright wrapper
│   ├── form_filler.py   #   Deterministic filler (default path)
│   ├── agent_form_filler.py # Phase 9 agent orchestrator
│   ├── file_uploader.py
│   └── ats/             #   Per-ATS adapters (greenhouse / lever / ashby / generic / base)
├── documents/           # DOCX + PDF + page-count + templates
├── tracker/             # CRM: applications table + analytics + CSV export
├── application/         # Shared application-layer services used by CLI + Web
├── cli/                 # Click command tree (autoapply, init, search, apply, status, provider, web, eval, ...)
├── web/                 # FastAPI app factory + JSON API routes + SPA static mount
└── utils/               # llm.generate_text bridge, rate limiter, logger
```

Five top-level skeleton folders from the original plan still exist
empty (`src/applicant/`, `src/cover_letter/`, `src/filter/`,
`src/resume/`, `src/scraper/`). They are historical placeholders and
should be deleted on the next housekeeping pass.

## 5. Data Model (current)

Current Postgres schema lives in `migrations/versions/` (alembic). The
core tables are:

```sql
jobs (
  id UUID PRIMARY KEY,
  source TEXT,                       -- greenhouse / lever / ashby / linkedin
  source_id TEXT,                    -- per-source job id; (source, company, source_id) is the dedup key
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  location TEXT,
  employment_type TEXT,              -- intern / fulltime / coop
  seniority TEXT,
  description TEXT,
  description_embedding vector(1536),
  requirements JSONB,
  visa_sponsorship BOOLEAN,
  ats_type TEXT,
  application_url TEXT,
  raw_data JSONB,
  discovered_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ
);

applications (
  id UUID PRIMARY KEY,
  job_id UUID REFERENCES jobs(id),
  status TEXT NOT NULL DEFAULT 'DISCOVERED',
  match_score FLOAT,
  resume_version TEXT,
  cover_letter_version TEXT,
  qa_responses JSONB,
  screenshot_paths JSONB,
  error_log TEXT,
  state_history JSONB,
  fields_filled INT, fields_total INT,
  files_uploaded JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  submitted_at TIMESTAMPTZ,
  outcome TEXT,                      -- pending / rejected / oa / interview / offer
  outcome_updated_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
);

applicant_profile (
  id UUID PRIMARY KEY,
  section TEXT NOT NULL,             -- identity / education / skills / experience / projects
  content JSONB NOT NULL,
  content_embedding vector(1536),
  tags TEXT[],
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

bullet_pool (
  id UUID PRIMARY KEY,
  category TEXT,
  source_entity TEXT,
  text TEXT NOT NULL,
  text_embedding vector(1536),
  tags TEXT[],
  used_count INT DEFAULT 0
);

qa_bank (
  id UUID PRIMARY KEY,
  question_pattern TEXT,
  question_type TEXT,
  canonical_answer TEXT,
  variants JSONB,
  confidence TEXT DEFAULT 'high',
  needs_review BOOLEAN DEFAULT FALSE
);
```

The application state machine has 11 states:

```
DISCOVERED → QUALIFIED → MATERIALS_READY → FORM_OPENED
→ FIELDS_MAPPED → FILES_UPLOADED → QUESTIONS_ANSWERED
→ REVIEW_REQUIRED → SUBMITTED → FAILED → NEEDS_RETRY
```

Phase 13 added a separate cluster of tables for the **Job Index &
Freshness Engine**:

```sql
job_postings        -- entity identity (UNIQUE(source, source_job_id))
job_snapshots       -- content version, content_hash, immutable
search_queries      -- normalized search condition + freshness state
search_results      -- search → posting many-to-many (per scrape)
refresh_tasks       -- priority queue of pending scrapes
```

Plus a new `applications.job_snapshot_id` FK that pins every
generated artifact to the exact JD content it was produced against.
Every Phase-12+ table carries a `tenant_id` column (default `"default"`
until Phase 21 — originally 18, deferred to 19, 20, then 21 as new
product work landed ahead). Phase 13.9 backfills the same column onto
every legacy table (`jobs`, `applications`, `applicant_profile`,
`bullet_pool`, `story_bank`, `qa_bank`, etc.) — see D020 / D026.

## 6. Layered Architecture

### Layer 1: Job Intake
Greenhouse / Lever / Ashby / LinkedIn adapters; unified `RawJob` schema;
LLM-assisted JD parsing with regex fallback; deduplication on `(source,
company, source_id)`. Phase 13 replaced the file-backed search cache
with the Job Index & Freshness Engine.

### Layer 2: Matching & Filtering
Three-tier scoring:
1. **Hard rules** (work authorization, experience-year cap with 1-year
   grace, education, employment type, spam / ghost-job detection)
2. **Semantic** (embedding overlap on description / responsibilities /
   requirements + TF-similarity fallback when embeddings unavailable)
3. **Risk** (visa, ghost reposting, sparse JDs, missing apply URL)

Composite scorer: weighted must-have (70%) / preferred (30%) skill
overlap + keyword similarity + rule bonus × quality multiplier.

Phase 16 adds a reason chain and an edge-case agent for borderline
scores `[0.4, 0.6]`.

### Layer 3: Applicant Memory
Profile YAML → DB ingestion with per-section embeddings and tagged
bullets. `qa_bank` includes geography + role-type variants and a
`needs_review` flag for high-risk question types (authorization,
sponsorship, salary, start date). Resume importer turns DOCX / PDF
resumes into structured YAML via Claude CLI.

### Layer 4: Resume / Cover Letter Generation
Structured IR + block-based assembly. Bullets are selected from the
pool by tag overlap, then optionally lexically rewritten under a
fact-drift guard (rejects rewrites whose length ratio falls outside
`[0.3, 2.0]`). Cover-letter generation is constrained to four
sections (opening, evidence, company tie-in, close), 250-400 words,
no fabrication. Quick-question answering cascades QA-bank → template →
LLM → flag for review.

Phase 15 promotes cover-letter generation to an agent (binding to a
specific `job_snapshot_id`).

### Layer 5: Form Filling & Submission
Each application is an 11-state state machine. The deterministic
`form_filler.py` is still the default; `agent_form_filler.py` (Phase
9) is the agent path, gated on confidence and the HITL approval
queue. ATS adapters live in `src/execution/ats/` (Greenhouse / Lever
/ Ashby / generic). Rate limiter enforces random delays, hourly caps,
and per-error cooldowns.

### Layer 6: File Pipeline
Template packages under `data/templates/<document_type>/<template_id>/`
contain `template.docx`, `manifest.json`, `style.lock.json`, and a
sample IR payload. DOCX rendering uses named Word styles from the
manifest plus block markers (`{{resume.sections}}`,
`{{cover_letter.body}}`). PDF export prefers Word + `docx2pdf` and
falls back to LibreOffice. File naming is
`{type}_{company}_{role}_{date}.{ext}`; every artifact is versioned.

### Layer 7: Analytics / CRM
Tracking table records source, company, role, date, platform, resume
version, match score, status, outcome, and outcome timestamp. Analytics
dashboard surfaces pipeline / outcome / platform / company breakdowns.
CSV export excludes `error_log` by default.

## 7. Phased Implementation — what has shipped

Test counts are snapshots at the close of each phase. Current
baseline (post-Phase-10): **680 passed, 1 skipped** (`pytest -q`),
`ruff check src/ tests/` clean, `npm run build` clean.

| Phase | Scope | Status | Test snapshot |
|---|---|---|---|
| 1 | Infrastructure + Applicant Memory + Document Pipeline | Complete | — |
| 2 | Job Intake + Smart Filtering | Complete | 156 |
| 3 | Resume / CL Tailoring + QA | Complete | — |
| 4 | Browser Automation + Form Filling | Complete | 156 |
| 5 | CLI + Tracking + Full Pipeline | Complete | 177 |
| 6 | LinkedIn Integration | Complete | 207 |
| 7 | Web GUI (FastAPI + Vue SPA) | Complete | 228 |
| 8 | Materials Workspace + DOCX Template Packages + Hardening | Complete | 340 |
| Agent 8 | Agent Harness (tools / loop / trace / eval / HITL gate) | Complete | — |
| Agent 9 | Form-Filler Agent + cost telemetry + 5-fixture eval | Complete | 553 |
| 10 | LLM Provider Abstraction (REST + subprocess + credential store + Settings UI) | Complete | 669 |

See `docs/CHANGELOG.md` for the per-sub-phase shipping log.

## 8. Roadmap (Phase 11 → 21) — v3.3, refreshed 2026-05-19

v3 corrected four issues from v1/v2 (preserved below); v3.1 further calibrates
v3 in four places (see the top-of-file version note). v3.2 recorded Phase 17.9
and the Phase 18/19 reorder. v3.3 inserted the per-posting cache and custom-
sources phases between Phase 18 and the multi-tenancy work; multi-tenancy is
now Phase 21.

The v2/v3 re-plan reflects four corrections to the v1 draft:

1. **PostgreSQL is the source of truth**, not SQLite. The v1 draft
   said "L2 SQLite cache" and "APScheduler + SQLite jobstore"; both
   were errors. The app has always run on Postgres + pgvector +
   alembic. (See D021.)
2. **Redis is adopted from Phase 12** as the cache / lock / queue
   substrate, preserving a commercial deployment path. (See D018.)
3. **Task queues are required for automated runs.** Phase 14 now makes the
   Redis queue + Postgres task-state + worker boundary explicit instead of
   treating background work as a scheduler detail. (See D023.)
4. **Materials generation needs two resume modes.** Phase 15 now covers
   original-resume patching plus LaTeX-first generation rather than only the
   cover-letter agent. (See D024.)

The earlier "JD scrape caching" sub-phase has been promoted to a full
phase (**Phase 13: Job Index & Freshness Engine**) because the
problem is content versioning + freshness state machines + audit
binding, not key-value eviction. (See D019.)

Multi-tenancy & auth hardening still closes the commercial-ready core, but it is now **Phase 21** after a three-step deferral: original Phase 18 → 19 → 20 → 21. **Phase 18** is worker activation, reliability, parallelism, and cleanup so the personal-version product is solid before any further product work. **Phase 19** then reinstates the per-posting tag cache & filter fast-path that was scoped on 2026-05-16 and displaced twice. **Phase 20** opens the system to user-added company careers sites (ATS-first, LLM-templates for the long tail). Every Phase 12+ table — including the new Phase 19-20 tables — is still built with `tenant_id` from day one. (See D020 / D026.)

### Phase 11: Reliability & Cleanup (~1 week)
Tighten the provider layer; ship the migration tool needed for users
upgrading from earlier revisions.
- **11.1** Provider fallback chain in `generate_text` (primary +
  ordered fallbacks; quota / network / auth failure auto-failover;
  attempt chain recorded in trace).
- **11.2** `autoapply migrate` CLI: cleans stale credential
  breadcrumbs, renames legacy settings keys, detects stale credentials.
- **11.3** Docs sync — push everything up to Phase 10 complete state.
- **11.4** Provider health monitor: `/api/providers/health` background
  probe every 5 minutes; Settings page "Last verified" surfaces real
  telemetry.

### Phase 12: Cache Infrastructure (~1.5 weeks)
**First introduction of Redis.** Scope is deliberately narrow — LLM
and embedding responses only. JD / job content caching moves to
Phase 13.
- **12.1** `src/cache/` module — L1 in-process LRU + L2 Redis;
  namespace TTL (`llm:7d`, `embedding:30d`, `response:5m`); unified
  `get/set/invalidate` API; version-stamped keys.
- **12.2** Redis infrastructure — connection pool, health check,
  `REDIS_URL` env var, `docker-compose.yml` service, AOF persistence,
  `autoapply redis ping/flush/info` CLI.
- **12.3** Distributed lock primitive — `with cache.lock(key, ttl)`
  built on Redis `SET NX PX`. Phase 13 force-refresh consumes it.
- **12.4** LLM response caching — `generate_text(cache=True)`;
  agent loops default `cache=False`, deterministic retrieval defaults
  `cache=True`. Cost-saved counter on hit.
- **12.5** Embedding cache — `embed_text(cache=True)` for
  `src/matching/semantic.py` with 30-day TTL.
- **12.6** Cache inspector UI at `/settings/cache`.
- **12.7** Cost dashboard upgrade — Phase 9.4 aggregates split into
  "cached vs fresh" with a $-saved line.

### Phase 13: Job Index & Freshness Engine (~2 weeks)
Replaces the file-backed `src/intake/search_cache.py` with a proper
Job Intelligence Database.
- **13.1** Schema (alembic) — `job_postings`, `job_snapshots`,
  `search_queries`, `search_results`, `refresh_tasks`; add
  `application_records.job_snapshot_id` FK; every new table carries
  `tenant_id`.
- **13.2** Normalization layer — `normalize_search_key()`,
  `normalize_job_content()`, `content_hash()` excluding unstable
  fields.
- **13.3** Freshness state machine in `src/jobs/state.py` —
  `new → active → stale → unknown → expired → archived`.
- **13.4** Search flow — cache-first by default; force-refresh wraps
  scrape in a Phase 12 distributed lock; old cache preserved on
  failure.
- **13.5** Detail enrichment with content versioning — scrape →
  normalize → hash → new `job_snapshot` if `content_hash` changed;
  emit `job.content_changed` event.
- **13.6** Context-aware freshness — `should_refresh(job, context)`
  where context ∈ {`search_display: 72h`, `generate_materials: 24h`,
  `before_submit: 6h`}.
- **13.7** Web UI — "Last updated 18h ago · Refresh"; refresh-success
  banner reports `N new / N expired / N updated`.
- **13.8** Migrate legacy `data/cache/linkedin_search/*.json` into
  `search_queries` + `search_results`; remove the file-cache module.
- **13.9** **tenant_id retrofit migration** (must land before Phase 14 starts;
  see D026). One alembic migration adds
  `tenant_id TEXT NOT NULL DEFAULT 'default'` to every legacy table from Phase
  11 and earlier (`jobs`, `applications`, `applicant_profile`, `bullet_pool`,
  `story_bank`, `qa_bank`, `templates` / `template_packages`, etc.) and
  backfills existing rows; ORM models add the field. Existing query paths
  are not forced to filter (preserving today's global-read behavior), but
  every new Phase-14 code path must thread an explicit tenant context. The
  "default tenant" fallback is replaced by the eventual Phase 21 auth
  middleware + RLS (originally Phase 19, deferred twice through
  19→20→21).

### Phase 14: Task Queue + Scheduled Work (~2.5 weeks, Celery-based) — **Complete**

All 10 sub-phases shipped on `feat/phase-14` (commits `83de0db` →
`707d94e`) + two rounds of codex-review fixes folded in (`3de7084`).
Verification baseline: 1161 passed, 1 skipped; `ruff check` clean;
frontend build clean; migrations `e1b4f72c8a05` (tasks audit table) +
`f2c5d83a91b6` (gate_queue) applied to dev DB.

Switches to Celery 5.x (see D025). The originally-planned self-built task
model + queue transport + worker runtime is dropped — Celery owns those.
AutoApply layers a thin "agent boundary + HITL + trace + tenant context"
wrapper on top. D023's principle ("queue owns execution reliability; agents
own bounded decisions") is preserved.

- **14.1** **Celery wiring + project skeleton.** `celery_app = Celery(
  "autoapply", broker=REDIS_URL, backend=REDIS_URL)`,
  `task_acks_late=True`, `task_reject_on_worker_lost=True`,
  `worker_prefetch_multiplier=1` (long-task model). Four queues:
  `search.*` / `materials.*` / `application.*` / `maintenance.*`.
- **14.2** **Durable audit table** (Postgres, source of truth). Celery's
  result backend is transient; AutoApply maintains its own `tasks` table
  (`id, celery_task_id, tenant_id, kind, payload, idempotency_key, status,
  attempts, parent_task_id, trace_id, created_at, finished_at`). Celery
  signals (`task_prerun` / `task_postrun` / `task_failure` / `task_retry`)
  keep it in sync.
- **14.3** **Custom `AutoApplyTask` base class** (subclass of Celery
  `Task`) — provides: (a) reads `tenant_id` from task headers and injects
  it into DB session + Redis namespace; (b) idempotency key check on
  entry (return early if already succeeded); (c) `self.call_agent(...)`
  wrapper that runs one bounded agent per task and dispatches the
  structured return (`success` / `failed_retryable` / `failed_terminal` /
  `needs_human` / `needs_followup_task`) to `raise self.retry()`, gate
  enqueue, or child-task enqueue; (d) writes trace records.
- **14.4** **HITL gate moved to the DB** (replaces single-process file
  JSON; see D026). New table `gate_queue(id, tenant_id, task_id, kind,
  payload, status, requested_at, decided_at, decision, reason)` with
  `pending → approved → rejected`. When a Celery task returns
  `needs_human`, the task row transitions to `waiting_human` and the
  worker is *released immediately* (no blocking). User approval calls
  `/api/gate/{id}/approve` which enqueues a `resume` task under the same
  idempotency key. The old file-backed `src/agent/gate/queue.py` stays as
  a compat layer for one release and is then removed.
- **14.5** **Celery Beat for cron triggers** (replaces APScheduler
  entirely). Beat schedule in `src/tasks/beat.py`: `daily_search`,
  `jd_health_check` (drives 13.3 freshness time decay),
  `application_status_sync`, `linkedin_cookie_refresh`, `cache_eviction`.
  Beat only enqueues; business logic never runs in the Beat process.
  Multi-instance Beat uses `celery-redbeat` or a Postgres advisory lock to
  prevent double-fire.
- **14.6** **Task kinds**: `search.refresh`, `jobs.enrich`,
  `materials.generate`, `application.prepare`, `application.fill`,
  `application.submit`, `status.sync`, each a Celery task built on the
  14.3 base. Payload schemas validated via Pydantic models.
- **14.7** **CLI**: `autoapply worker --queues search,materials,apply
  --concurrency 4` (wraps `celery -A src.tasks worker ...`);
  `autoapply beat`; `autoapply tasks list/retry/cancel/inspect` (reads the
  14.2 audit table); `autoapply schedule list/pause/run-now` (reads Beat
  schedule + enqueues a one-shot task).
- **14.8** **Web UI** `/schedule` + `/tasks` + `/gate`: reads the audit
  table for queue depth, live workers via Celery inspect API, failure
  reasons, manual retry/cancel. `/gate` replaces the legacy agent gate
  viewer.
- **14.9** **Trace integration**: `AutoApplyTask.on_success/on_failure/
  on_retry` auto-write trace records; child task headers carry
  `parent_trace_id` so the trace viewer can walk the parent/child chain.
- **14.10** **Multi-instance safety**: Celery itself guarantees a single
  worker picks up each task; Beat multi-instance uses redbeat leader
  election; Postgres advisory lock as a defense-in-depth backstop (D021's
  principle preserved).

### Phase 15: Resume & Cover Letter Generation v2 (~3 weeks) — **Complete**

All 10 sub-phases shipped on `feat/phase-15` (commits `4e95e98` →
`439d2d7`) + one codex-review P2 fix round (`9b813a3`). Verification
baseline: 1332 passed / 1 skipped; `ruff check` clean; migration
`a3b9d52e7c41` (`source_resumes`) applied to dev DB.

Implementation highlights:

* `src/generation/source_resume.py` -- upload ingest (DOCX / LaTeX / PDF)
* `src/generation/docx_patch.py` -- DOCX patch with named-style preservation
* `src/documents/latex_manifest.py` + `latex_renderer.py` -- manifest-adapter
  rendering on top of the existing `latex_engine.py` (not from scratch)
* `src/generation/materials_router.py` -- `patch_existing` vs
  `generate_from_template` dispatch with provenance bindings
* `src/agent/tools/jd.py` -- the `jd_lookup` agent tool
* `src/generation/agent_cover_letter.py` + `fact_drift.py` -- five-tier
  fallback ladder + numeric-drift blocking
* `src/documents/template_adapter.py` -- propose manifests for
  arbitrary user-uploaded LaTeX templates
* Three eval suites + 7 fixtures
* `src/generation/gate_triggers.py` -- HITL gate only for persistent
  grounding mutations
Benefits from Phase 12 (LLM caching), Phase 13 (snapshot binding), and
Phase 14 (background material tasks).
- **15.1** Source-resume model: uploaded originals stored with type, checksum,
  extracted structure, and editability flag. PDF import feeds fact extraction
  only; no format-preserving PDF edit promise.
- **15.2** DOCX patch mode: localized edits to summary, bullets, skills order,
  and section inclusion while preserving existing styles and layout structures
  where DOCX permits. **Fallback**: when patching fails (missing style, IR
  field unmappable, page overflow after edit), automatically degrade to
  `generate_from_template` and tell the user why in the UI / task result —
  do not let users assume DOCX patch is 100% fidelity-preserving.
- **15.3** LaTeX template package spec. Note that
  `src/documents/latex_engine.py` already provides the compile/render
  primitives (built alongside DOCX template packages in Phase 8). This
  sub-phase *normalizes the template package structure*: `template.tex`,
  assets, `template.manifest.yaml`, sample IR, compile engine choice
  (`pdflatex` / `xelatex` / `lualatex`), capacity / page rules, command /
  field mappings, escape allowlist. The focus is defining manifest schema
  + adapter conventions, not writing a renderer.
- **15.4** LaTeX-first resume generator: agent emits structured resume IR;
  the deterministic renderer (reusing existing `latex_engine.py`) escapes,
  maps via the manifest, compiles, and validates page/capacity. Refactors
  the LaTeX branch in `resume_builder.py` from "custom IR direct dispatch"
  to "manifest-adapter dispatch."
- **15.5** Materials router: `patch_existing` vs `generate_from_template`,
  both running as `materials.generate` tasks and binding outputs to
  `job_snapshot_id`, source/template ID, profile version, and trace ID.
- **15.6** Shared `jd_lookup` tool for resume and cover-letter agents.
- **15.7** `AgentCoverLetter` orchestrator emits cover-letter IR with evidence
  citations; existing fact-drift checker as post-guard; deterministic fallback
  on agent failure.
- **15.8** Template adapter assistant: agent may propose a manifest for a new
  arbitrary LaTeX template, but persistence requires sample compile + user
  confirmation.
- **15.9** Eval suite covers DOCX patch fixtures, LaTeX template fixtures, and
  cover-letter fixtures.
- **15.10** HITL gate fires on bullet/story-bank mutation or persistent
  template-adapter creation, not on ordinary generation.

### Phase 16: Filter Agent + Explainability (~1.5 weeks) — **Complete**

All 4 sub-phases shipped on `feat/phase-16` (commits `203becb` →
`9198a3b`) + one codex-review P2 fix (`5702da7`). Verification
baseline: 1398 passed / 1 skipped; `ruff check` clean; frontend
build clean.

Implementation highlights:

* `src/matching/rules.py` -- structured `RuleResult` fields with
  bounded JD excerpt extraction per hard rule
* `src/matching/scorer.py` -- `ScoreBreakdown.job_snapshot_id` +
  `disqualify_results` + `to_dict()`
* `src/agent/tools/score_breakdown.py` -- read-only dotted-path
  tool bound to one breakdown
* `src/matching/edge_case_agent.py` -- fires only on
  `0.4 <= score <= 0.6` with fail-closed fallback ladder; never
  overrides hard rules
* `src/application/matching.py` + `POST /api/matching/explain` --
  on-demand re-score endpoint for the popover
* `frontend/src/views/JobsView.vue` -- "Why was this filtered?"
  Dialog popover on every disqualified job card
* `tests/agent_evals/fixtures/filter_borderline/` -- 10 fixtures
  covering the full decision matrix

(Original plan retained below for the design rationale.)
Not a replacement for the deterministic filter — an explainability
layer + agent invocation for borderline jobs only.
- **16.1** **`RuleVerdict` schema evolution** (this is a schema change, not
  just "add a layer"). Today, `src/matching/scorer.py`'s
  `ScoreBreakdown.disqualify_reasons` is only a `list[str]` and
  `RuleVerdict` carries no `evidence_excerpt` or `rule_id` structure.
  This sub-phase: (a) restructures `RuleVerdict` into `{rule_id,
  rule_name, verdict, reason, evidence_excerpt}`; (b) each rule
  implementation in `src/matching/rules.py` actively extracts the
  relevant JD snippet as `evidence_excerpt`; (c) adds `job_snapshot_id`
  to `ScoreBreakdown` so the whole result can be pinned to a specific
  JD version. 16.3's UI consumes this structure directly.
- **16.2** Edge-case agent — invoked only for scores ∈ [0.4, 0.6];
  uses Phase 8 harness + new `score_breakdown` tool.
- **16.3** Web UI "Why was this filtered?" affordance.
- **16.4** Eval suite — 10 human-annotated borderline jobs; agent
  decision matches human ≥ 70%.

### Phase 17: Plan Run Loop + Review Queue (~2 weeks) — **Complete**

All 7 sub-phases shipped on `feat/phase-17` (commits `771b6da` →
`208db10`) + three rounds of codex-review fixes (`2d694e9`,
`fe11907`, `62c4314`; 3 P1 + 6 P2 across the rounds).
Verification baseline: 1530 passed / 1 skipped; `ruff check` clean;
frontend build clean; alembic head at `c9e1f3a7b8d4`.

Implementation highlights:

* `src/orchestration/plan_run.py` -- async `run_plan(...)`
  orchestrator, dependency-injected for testability. Flow: search
  (cache-first via Phase 13.4) -> score (Phase 16-aware structured
  breakdowns) -> top-N qualified -> persist review_queue rows +
  enqueue materials.generate + application.prepare. **Never
  enqueues application.submit.** Pause sentinel short-circuits
  BEFORE search.
* Migrations `b7d9a1e4f3c2` + `c9e1f3a7b8d4` -- `review_queue` table
  + five-state machine + pending-only partial unique index (the
  same snapshot can re-pass through the lifecycle in later runs).
* `src/application/review.py` -- single-item + bulk ops + state
  machine guards.
* `src/web/routes/review.py` -- `/api/review` routes, tenant-
  isolated, error mapping (409 / 404).
* `frontend/src/views/ReviewQueueView.vue` -- 4-column kanban; stale
  rows in Pending column with Refresh affordance (Approve hidden);
  Approved column has Submit + Reject; multi-select + bulk actions
  + by-filter rejection.
* `src/review/pre_submit_gate.py` -- 6h freshness budget +
  snapshot-id mismatch detection + lifecycle state check; auto-flips
  to stale / rejected.
* `src/orchestration/digest.py` -- 08:00 morning digest aggregating
  `data/plan_runs/*.json` + live review_queue counts;
  dashboard banner renders the headline + per-status chips.
* `autoapply pause-plan-runs [--clear-pending]` -- sentinel + the
  vacation affordance for bulk-clearing pending entries.

(Original plan retained below for design rationale.)
Integration phase. Threads Phase 14 (task queue + scheduler) + Phase 13
(job-index / freshness) + Phase 12 (cache) + Phase 9 / 15 (agents)
into customizable application batch runs with review before submit.
- **17.1** `plan_run` orchestrator — search (cache-first, refresh
  stale) → filter (with 16's explainability) → top-N → enqueue
  `materials.generate` and `application.prepare`; workers run agents under
  task-level retry/timeout policy. **Never auto-submits.**
- **17.2** Review queue model — `review_queue(id, tenant_id, job_id,
  job_snapshot_id, materials_path, status, ...)`; state machine
  `pending → approved → submitted` or `pending → rejected`.
- **17.3** `/review` kanban UI.
- **17.4** Bulk operations — multi-select approve, bulk-reject by
  company / keyword.
- **17.5** Pre-submit hard gate — re-run
  `should_refresh(job, "before_submit")`; refresh if > 6h stale;
  block on expired jobs entirely.
- **17.6** Morning digest at 08:00.
- **17.7** `autoapply pause-plan-runs` kill switch.

### Phase 17.8: Material Strategy & Document Library (~1 week) — **Complete**
Closes the loop on "AutoApply made me a draft I don't love." Three
gaps before this phase:

1. The user had no first-class way to see / curate the resumes and
   cover letters they'd given the system (the Phase 15.1
   `source_resumes` table existed but was internal-only).
2. There was no per-document-type strategy setting — every
   generation defaulted to "regenerate from system template."
3. Once a paused review entry sat in the kanban, the only verbs
   were Approve & Submit or Discard; the user couldn't say
   "regenerate with a different template" or "use my real resume
   as the base."

Sub-phases:

- **17.8.1** New `user_documents` table (per-tenant, deduped, structural
  index). Distinct from `source_resumes` (which stays an internal
  Phase 15.1 artifact). REST: `GET/POST/PATCH/DELETE /api/documents`,
  `GET /api/documents/{id}/download`, `POST /api/documents/promote`.
  Profile upload (`POST /api/profile/upload-resume`) now also stashes
  the original file in the library; new `POST /api/profile/from-library`
  lets a user seed a profile from an already-uploaded library doc.
- **17.8.2** `config/material_defaults.yaml` + `GET/PUT
  /api/settings/material-defaults`. Per-doc-type defaults:
  `{strategy: regenerate|patch_existing, default_template_id,
  default_document_id}`. `resolve_material_choice()` does the
  override → saved-default → system-default cascade.
  `/api/jobs/generate-material` accepts per-call `strategy` and
  `source_document_id` overrides; when patch_existing fires on a
  DOCX library doc, `patch_resume_docx` runs after IR generation
  and swaps the artifact path.
- **17.8.3** `AutomationPlan` schema gains per-plan overrides:
  `resume_strategy`, `resume_template_id`, `resume_source_document_id`,
  and the same trio for cover letters. `plan_run` carries these
  through to the `materials.generate` payload; `MaterialsGeneratePayload`
  was widened to keep them.
- **17.8.4** Paused-review card in `/review` grows a Replace materials
  dialog (pick material × strategy × template-or-library-doc) backed
  by `POST /api/applications/{id}/regenerate-material`, plus a
  Save-to-library button on every downloadable resume / cover letter
  in `/review` and `/applications` that calls
  `POST /api/documents/promote`.

UI: `/materials` got a Library / Templates / Generate tab strip;
Settings got a Default material strategy card; Plans form got a
collapsible Materials override section; Profile create gained a
"Pick From Library" mode.

Open questions deferred: cover-letter patching (we currently fall
back to regenerate with a warning), LaTeX source patching (same).
Picked up by Phase 18+ when the materials generation worker body
actually consumes `MaterialsGeneratePayload`.

### Phase 17.9: LLM Provider Expansion (~0.5 week) — **Complete**

Hardens the Phase 10 provider abstraction before worker activation. The goal was to make provider choice an operator setting instead of a code change.

- **17.9.1** Extracted `OpenAICompatibleProvider`, added `ModelInfo`, and gave first-party providers curated model catalogs.
- **17.9.2** Added DeepSeek, Moonshot/Kimi, Qwen, xAI Grok, Groq, Mistral, and OpenRouter.
- **17.9.3** Added local Ollama support with empty-key credential handling and live `/api/tags` catalog probing.
- **17.9.4** Added `GET /api/providers/{id}/models` and a Settings model picker with a custom-model escape hatch.
- **17.9.5** Added the optional `llm.small_provider` / `llm.small_model` tier for extraction workloads such as JD parsing and resume import.
- **17.9.6** Added `llm.custom_providers` so users can register OpenAI-compatible proxies, private vLLM / LM Studio endpoints, or newer upstreams without code changes.

### Phase 18: Worker Activation, Reliability, Parallelism, Cleanup (~2.5–3 weeks)

> **Re-ordering (2026-05-19, refreshed)**: this used to be Phase 19,
> after Multi-Tenancy. We moved it forward because:
> (a) the personal-version product is the active priority; multi-
>     tenancy/commercialization is paused until the single-user
>     product is rock-solid;
> (b) garbage is accumulating in `data/output/` today and the cleanup
>     debt is hurting day-to-day use right now;
> (c) the worker activation work in 18.1 is a prerequisite for
>     reliability / parallelism / scalability across all subsequent
>     phases, multi-tenancy included.
> Two further product phases now sit between Phase 18 and the multi-
> tenancy work: **Phase 19** (Per-Posting Tag Cache & Filter Fast Path)
> and **Phase 20** (Custom Job Sources / Connectors). Multi-tenancy &
> auth hardening is now **Phase 21**, deferred until the personal
> version is feature-complete.

A **fix-focused phase**, not a feature phase. Phase 14 shipped the
Celery scaffold (queues, base task, audit table, reliability config,
Beat schedule); Phase 17 shipped the per-plan strategy + review loop
on top of it; the project memory accurately summarised the state in
mid-May 2026 as "the bones of MQ are there, the body isn't." This
phase fills the body in, and pays down the cleanup debt that has
been accumulating since Phase 15.

Four pillars, mapped one-to-one to the failure modes surfaced during
the late-Phase-17 / Phase-18-prep sweep:

1. **The work isn't on the queue.** `materials.generate`,
   `application.prepare/fill/submit`, `maintenance.cache_eviction`,
   `maintenance.gate_expire_sweep` — every task body is a stub that
   logs "queued" and returns `"scheduled"`. The actual generation
   runs synchronously inside the FastAPI request handler, so closing
   a browser tab mid-LLM-call loses the work and worker horizontal
   scaling is impossible.
2. **MQ reliability is configured but unexercised.** `task_acks_late=
   True`, `task_reject_on_worker_lost=True`, `worker_prefetch_
   multiplier=1`, idempotency-key short-circuit, `TaskRecord` audit
   row state machine — all of it sits unverified because (1).
3. **Parallelism is left on the table.** Bullet rewrites run serially
   inside `rewrite_bullets` (one LLM call per bullet); resume +
   cover-letter generation are sequential inside one request; JD
   parsing for N search results runs one-LLM-at-a-time. LinkedIn
   detail-page enrichment is **correctly** serial (anti-bot) and
   stays that way.
4. **No garbage collection.** `data/output/` only grows; failed
   patches leave half-written `patched_resume_<uuid>.docx`; screenshot
   directories accumulate every form-fill attempt; `TaskRecord` has
   no retention policy; `delete_document` is the only path that
   removes a file from disk.

**Honest scope**: 18.1 is net-new code (real task bodies, async API
contract). 18.2 is "exercise + add DLQ + manual-retry UI" on top of
infrastructure that already exists. 18.3 is mostly `asyncio.gather`
+ rate-limit threading. 18.4 is net-new (no cleanup logic exists
today outside `delete_document` + the profile-import `_upload_*`
tmpfile unlink). The four are bundled into one phase because they
share a single audience (the worker + operator), but they're
internally sequential: 18.4 (cleanup) is independent and ships
first to stop the bleed; 18.1 (activation) unblocks 18.2 and 18.3.

Sub-phases:

- **18.1 Worker activation** — fill the stub task bodies with the
  real call chain. Concretely:
  - `materials.generate` invokes `generate_material_for_job` end-to-end
    using the `MaterialsGeneratePayload` already shaped in Phase 17.8.
    Writes the resulting artifact paths back onto the `Application`
    row via the same code path `regenerate_application_material`
    uses today, so the audit `state_history` event is unchanged.
  - `application.prepare` / `application.fill` / `application.submit`
    bodies — `application.submit` keeps the pre-submit gate (Phase
    17) wiring; HITL transitions still use the `waiting_human` audit
    state (no `time.sleep` in workers).
  - Async REST surface: `POST /api/jobs/generate-material` and
    `POST /api/applications/{id}/regenerate-material` switch to
    "enqueue + return `task_id`", with `GET /api/tasks/{task_id}`
    polling endpoint backed by `TaskRecord`. SPA gets a generic
    "long-running operation" hook so existing views can swap in
    progress polling without per-view boilerplate.
  - The existing synchronous endpoints are retained behind a feature
    flag (`AUTOAPPLY_SYNC_MATERIALS=1`) for a one-week soak; default
    is async.
  - **Tests**: end-to-end test that fires `materials.generate` via
    `apply_async` against an in-process Celery worker (`task_always_
    eager=True` is **not** used — we need the real broker contract).

- **18.2 Resilience exercise + DLQ + manual retry** —
  - Add a `tests/test_worker_resilience.py` suite that kills a
    worker mid-task (`os.kill(pid, SIGTERM)` on a subprocess Celery
    worker) and asserts the task is requeued exactly once with the
    same `idempotency_key`. Same for poison-message handling.
  - Dead-letter queue: tasks that exhaust `max_retries=3` move to
    a per-kind DLQ (`materials.generate.dlq`, etc.) instead of being
    silently absorbed by the audit row's `failed` state. DLQ entries
    surface in the Tasks UI with a "Retry from DLQ" button that
    creates a fresh task with the same payload + a new idempotency
    key (so the original failure stays auditable).
  - `TaskRecord` lifecycle hooks already exist; this phase verifies
    them end-to-end and adds the missing `last_attempted_at` /
    `dlq_reason` fields if the audit rows don't already capture them.
  - SPA `/tasks` view grows a "Stuck / failed" tab that lists DLQ
    entries with payload preview + retry / discard actions.

- **18.3 Strategic parallelism** —
  - `rewrite_bullets` rewritten as `asyncio.gather` of
    `_rewrite_single_bullet`, capped at 5 concurrent LLM calls
    (provider-rate-limit dependent). Expected: ~30s → ~6s for a
    10-bullet resume.
  - `_generate_selected_material` runs `generate_resume` and
    `generate_cover_letter` for one job in parallel via
    `asyncio.to_thread` (both are sync today; this preserves their
    bodies). Expected: ~75s → ~45s for the dual-doc case.
  - `intake.jd_parser.parse_requirements_batch()`: new helper that
    accepts N descriptions and runs them concurrently with the same
    rate-limit ceiling. Called from search post-processing when
    `use_llm=True`. Expected: 25 jobs × 3s/parse = 75s → ~15s.
  - **Out of scope (intentionally)**: parallelising LinkedIn detail-
    page fetches. The current serial + random-delay loop in
    `enrich_with_details` is the anti-bot contract and must not
    change inside this phase.
  - Each parallel hot-spot lands behind a config flag
    (`parallelism.bullet_rewrites.max_concurrent=5`) so an operator
    can dial it down if a provider rate-limits.

- **18.4 Cleanup policy + scheduled garbage collection** —
  - `docs/DECISIONS.md` gets a new entry (likely D026):
    "data/output/ is a cache, not a vault" — explicit retention
    rules per artifact category. Reviewed before writing code.
  - Atomic-write helper: a `with atomic_write(target_path) as tmp`
    context manager that writes to `target_path.with_suffix(
    target_path.suffix + ".tmp")` and renames on success, unlinks
    on exception. Applied to every `generate_*` / `patch_*` /
    `_copy_library_document_to_output` call site so crashes can't
    leave half-written DOCX/PDF on disk.
  - `maintenance.cache_eviction` task body — actually walks
    `data/output/` once a day, deletes files older than
    `cleanup.output_retention_days=30` that are NOT referenced by
    any `Application.resume_version` / `cover_letter_version` /
    `user_documents.storage_path`. Dry-run mode flag lands a
    `cleanup_report` audit row before deletion is enabled.
  - Screenshot rotation: per-application directory keeps only the
    latest 5 screenshots; older ones go in a `data/output/screenshots/
    archive/` tarball nightly.
  - `TaskRecord` retention: succeeded rows older than 30 days collapse
    into a `tasks_archive` summary table (per-tenant, per-kind,
    per-day counts + last error sample). Failed rows kept for 90.
    HITL `waiting_human` rows never expire.
  - `Application` delete API + UI — `DELETE /api/applications/{id}`
    with a `cascade=true` option to unlink the on-disk artifacts.
    Soft-delete (sets `Application.deleted_at`) by default;
    cascade removal cleans the files only after the row's audit
    history is summarised into the archive table.
  - Orphan scanner CLI: `autoapply cleanup scan` prints what
    `cache_eviction` would delete; `--apply` actually deletes. Lets
    operators audit before the scheduled task runs.

Sequencing rationale: 18.4 ships first (the orphans are accumulating
today, independent of MQ status). 18.1 next (unblocks 18.2, 18.3 and
fixes the lost-work-on-tab-close problem). 18.2 and 18.3 then ship
in parallel because they touch disjoint files.

Open questions deferred to later phases:
- Persistent task progress UI (real-time SSE streaming, not poll-based).
  Phase 18 only does poll.
- Cross-tenant DLQ surfacing for the future ops dashboard.
- Anti-bot session pooling — would let LinkedIn detail-page parallelism
  become safe by routing through N independent sessions. Out of scope
  here (overlaps with Phase 20 Tier 2 risks).

### Phase 19: Per-Posting Tag Cache & Filter Fast Path (~2 weeks)

> **History**: this was previously planned as Phase 19 in May 2026
> ("Per-Posting Tag Cache & Filter Fast Path"), then displaced first
> by the 17.9 LLM-provider expansion and then by the worker/multi-
> tenancy reshuffle. It is now re-instated as Phase 19 ahead of the
> Custom Sources work because the cache value compounds once multiple
> sources start surfacing the same posting.

Moves the search cache granularity from "result set" down to "single
posting". Today's `search_results` TTL short-circuit keeps a whole
result set alive for an hour; that means a profile edit during the
TTL window silently hides postings we already paid to fetch, and a
new posting from upstream is invisible until the TTL expires. Phase
19 inverts the model: searches always re-fetch, but each posting's
**objective attributes** are computed once (A1) and each posting's
**per-profile score** is cached and reused across searches (A2).

**Sub-phases:**

- **19.1** Schema migration. `job_postings` gains `tags JSONB`,
  `tagger_version INT`, `tags_status TEXT` (`pending` / `computing`
  / `ready` / `failed`), `tags_computed_at TIMESTAMPTZ`. New
  `job_posting_scores` table (FK `posting_id` + FK `snapshot_id` +
  `profile_id` + `profile_version` + `score_breakdown JSONB` +
  `verdict TEXT` + `computed_at`; `UNIQUE (snapshot_id,
  profile_version)`). Both new columns/table carry `tenant_id` from
  day one (D026).
- **19.2** `src/jobs/tagger.py` — pure-function rules over
  `work_mode` / `level` / `sponsorship_signal` / `intern_eligible` /
  `posting_age_bucket` / `clearance_required` / `usa_only`. Module-
  level `TAGGER_VERSION` constant; bumping it queues a full retag.
- **19.3** `posting.tag` Celery task kind + `enrich.on_content_changed`
  listener auto-enqueue on snapshot content-hash change.
- **19.4** `job_posting_scores` write-through: the Filter Agent
  (Phase 16) writes its computed verdict back keyed by
  `(snapshot_id, profile_version)`; read path checks the table
  before invoking the agent.
- **19.5** `cached_search` refactor: drop TTL short-circuit; keep
  `search_results` rows (for "removed since" diff + pagination);
  keep the distributed lock (still want to prevent concurrent
  same-source scrapes).
- **19.6** Filter fast-path (`src/filter/fast_path.py`): A1 hard
  rules reject up-front, A2 cached score reused on hit, otherwise
  enqueue real Filter Agent. Plan-run picker + Jobs view both route
  through it.
- **19.7** Frontend: tag chips on each posting in JobsView, spinner
  while `tags_status='pending'`, manual `POST /api/jobs/postings/{id}/retag`
  button. ReviewQueueView surfaces `(cached score · profile vXYZ)`
  so users can tell when a verdict came from cache.
- **19.8** Docs sweep: README / PROJECT_MANAGEMENT / CHANGELOG;
  a new Decision entry capturing the A1+A2 split and the
  `profile_version = sha256(canonical_json(profile))[:12]` derivation.

**Behavior change to call out**: searches no longer short-circuit on
TTL — every search hits the upstream. Justified because the cost was
masking new postings; the per-posting cache keeps the *analysis* hot
path cheap rather than the *fetch* hot path.

**`TAGGER_VERSION` bumps are expensive** on a large index. Retag
enqueues are paginated background work; the UI shows a "tagging in
progress" banner during the drain. Acceptable as long as it stays a
rare event.

### Phase 20: Custom Job Sources (Connectors) (~3 weeks)

> **History**: surfaced as an explicit roadmap item on 2026-05-19
> after the Phase 19 cache plan was reinstated. The two layers
> (ATS-first, LLM-templates second) were settled by an architecture
> review the same day.

Lets users add company careers sites (Nvidia, Microsoft, Stripe, etc.)
on top of the LinkedIn / built-in ATS intake the product ships with
today. Two tiers, sequenced so Tier 1 can ship to users without
waiting on Tier 2 stability.

**Tier 1 — ATS connector framework + multi-source search (~1.5 weeks):**

- **20.1** Source data model: new `job_sources` table (id,
  display_name, kind, url, ats_type, owner_tenant_id, status,
  last_health, ...) + alembic migration. `Connector` ABC with
  `fetch_jobs(source_config) -> list[RawJob]` in
  `src/intake/connectors/`. `tenant_id` from day one (D026).
- **20.2** ATS fingerprint detector (`src/intake/ats_detect.py`):
  follow redirects + DOM-sniff to identify which ATS backs a careers
  URL. Initial coverage: Greenhouse, Lever, Workday, Ashby, iCIMS,
  Smartrecruiters, Eightfold. Unknown → connector parks in `draft`
  until Tier 2 infers a template.
- **20.3** Existing LinkedIn / Greenhouse / Lever / Workday adapters
  rewrap as registered Connectors. Search dispatch goes through the
  new registry rather than hard-coded `source` strings.
- **20.4** Add-source UX: `POST /api/sources` runs the detector +
  one verification fetch, persists on success. New "Sources" page
  in the Web UI (same shape as the Phase 17.9 provider list:
  Connected vs Available, health badges, manual probe / disconnect).
- **20.5** Multi-source search: `SearchPayload.sources: list[str]`,
  Celery group fan-out per source, merge + dedupe by
  `(source_id, source_source_id)`. Plan-run form gains a source
  multi-select; plans persist `source_ids` so Beat reads it each
  tick. The Phase 19 per-posting cache means repeated postings
  across sources skip re-scoring entirely.

**Tier 2 — LLM-assisted scraper templates for the long tail (~1.5 weeks):**

- **20.6** Template schema + executor: `scraper_templates` table
  (`selector_recipe: jsonb`, `playwright_steps: jsonb`, `health: jsonb`).
  Playwright-driven Connector that executes a template against a
  URL and yields `RawJob`. Steps cover login walls, pagination,
  infinite scroll.
- **20.7** LLM template inference. When ATS detection fails, fetch
  the page via Playwright (HTML + screenshot), prompt the LLM via
  `generate_json(tier="small")` to emit a candidate recipe. Cached
  on the template row; user reviews + edits in a JSON editor with
  a "Test on this page" preview before activation.
- **20.8** Template self-heal: per-source health probe (extends the
  Phase 11.4 `src/providers/health.py` pattern) watches consecutive
  failure counts. Threshold breach queues an LLM re-inference; if
  the new recipe materially diverges, the source flips to
  `needs_review` rather than auto-applying.

**Risks to plan around:**

- **Anti-bot** (Cloudflare / Akamai JS challenges). Tier 1 only
  commits to ATS-backed sites which don't gate on bot challenges;
  Tier 2 documents the limitation rather than promising universal
  coverage. Residential-proxy escape hatch may follow in a 20.x
  point release.
- **Login walls** — out of scope for Tier 2 v1. Reuse the LinkedIn
  session pattern (per-source `storage_state`) when needed.
- **LLM cost** — HTML inputs run 20k+ tokens. Aggressive caching +
  default `tier="small"` routing + per-source token budgets to cap
  runaway templates.
- **Maintenance burden** — scraper templates rot. The self-heal
  loop is essential; without it Tier 2 becomes a graveyard.
- **Legal / ToS** — users are responsible for ToS compliance with
  each careers site they add. AutoApply does not bundle a default
  list of company URLs; users opt in by adding their own.

### Phase 21: Multi-Tenancy & Auth Hardening (~2.5 weeks, deferred)

> **Re-ordering history**: originally Phase 18, deferred to Phase 19
> on 2026-05-19 (worker / cleanup ate Phase 18); deferred again to
> Phase 20 when the Per-Posting Tag Cache plan was reinstated as the
> new Phase 19; deferred a third time to Phase 21 when Custom Job
> Sources slotted in as the new Phase 20. The schema-level
> `tenant_id` groundwork from Phase 13.9 plus the same discipline
> applied to all new Phase 19-20 tables (`job_posting_scores`,
> `job_sources`, `scraper_templates`) keeps activation cheap.

Activates the commercial-readiness work seeded across 12-17. SaaS
business layer (billing, sign-up flow, marketing site) is NOT in
scope — this phase only makes the existing system safe to host for
multiple isolated users.

**Honest scope**: Phase 13.9 already lands the schema-level `tenant_id`
column on every table, and Phases 19-20 keep the discipline (D026)
on every new table they add (`job_posting_scores`, `job_sources`,
`scraper_templates`). So the "add column + backfill" portion is
genuinely "activate existing work." The following sub-phases are
**net-new construction**, not retrofit: 21.2 auth middleware
(`src/web/` has no auth layer today), 21.4 Redis namespace refactor
(keys today are `{version}:{namespace}:{key}` with no tenant prefix
— every wrapper needs to change), and 21.7 credential store
(`src/providers/store.py` is a single global JSON file today; needs
per-tenant directory split + keyring entry renaming). 21.1 / 21.3 /
21.5 / 21.6 are the only true "activations."

- **21.1** `tenants` + `users` tables; bind the `tenant_id='default'`
  rows that 13.9 left behind to real tenants.
- **21.2** **Build from scratch** the FastAPI auth middleware —
  session/token parsing, `current_tenant_id` injected into a
  `ContextVar`; ORM sessions auto-filter via SQLAlchemy events; Celery
  task headers carry tenant context (14.3 reserves the hook).
- **21.3** Postgres Row-Level Security policies — DB-level backstop
  that catches any ORM bypass.
- **21.4** **Refactor** Redis key naming — every namespace now prefixed
  `tenant:{id}:`; `src/cache/base.py` key construction requires an
  explicit tenant context (raises rather than falling back to default).
- **21.5** Per-tenant quotas (LLM tokens, scrape rate, storage).
  Exceeding returns 429.
- **21.6** Audit log table — `audit_events` (submission, settings
  change, credential operation, manual schedule trigger). Append-only.
- **21.7** **Refactor** credential store — `src/providers/store.py`
  moves from one global JSON file to
  `data/tenants/{id}/credentials/`; keyring entries get tenant prefixes;
  migrate existing `data/providers/credentials.json` into the `default`
  tenant.

### Timeline summary

Status as of 2026-05-19: Phases 1-17.9 are shipped on `dev`. Phase 18
is the next milestone after the worker-system audit re-prioritised
the roadmap.

| Phase | Scope | Est. | Status |
|-------|-------|------|------------|
| 11 | Reliability & Cleanup | 1w | Done |
| 12 | Cache Infrastructure (Redis) | 1.5w | Done |
| 13 | Job Index & Freshness Engine | 2w | Done |
| 13.9 | tenant_id retrofit migration | 0.3w | Done |
| 14 | Task Queue + Scheduled Work (Celery) | 2.5w | Done (bodies stubbed — activated in 18.1) |
| 15 | Resume & Cover Letter Generation v2 | 3w | Done |
| 16 | Filter Agent + Explainability | 1.5w | Done |
| 17 | Plan Run Loop + Review Queue | 2w | Done |
| 17.8 | Material Strategy & Document Library | 1w | Done |
| 17.9 | LLM Provider Expansion | 0.5w | Done |
| **18** | **Worker Activation, Reliability, Parallelism, Cleanup** | **2.5–3w** | **Next** |
| 19 | Per-Posting Tag Cache & Filter Fast Path | 2w | Planned |
| 20 | Custom Job Sources (Connectors) — ATS detection + LLM templates | 3w | Planned |
| 21 | Multi-Tenancy & Auth Hardening | 2.5w | Deferred (post personal-version maturity) |

The personal-version product (single user, local-first, no auth) is
feature-complete through Phase 17.9. Phase 18 hardens it (real
workers, retention, parallelism); Phase 19 swaps the search-cache
model so per-posting work doesn't repeat across searches; Phase 20
opens the system to user-added company careers sites; Phase 21
finally activates the multi-tenancy plumbing that Phases 12-20 have
been carrying dormant. Phase 18 was scoped after a Phase-18-prep
audit found the task bodies were stubs, no cleanup policy existed,
and parallelism opportunities were unexplored. Phase 19 reinstates
the 2026-05-16 cache plan that was displaced twice during the
17.9/18/19 reshuffles. Phase 20 captures the "let me add Nvidia"
class of user request behind a tiered architecture. Phase 21 has
been deferred four times now (18→19→20→21) — each deferral leaves
the schema-level `tenant_id` discipline in place so activation cost
stays bounded.

## 9. Cross-cutting Quality Bars

Enforced from Phase 11 onward:

- **Tests** — no PR can drop the suite below the current 680 passing.
- **Lint** — `ruff check src/ tests/` stays clean.
- **Codex review per sub-phase** — `codex review --uncommitted` pass
  before commit; P1 findings block merge.
- **Cost ceiling** — any eval suite that pushes total cost above
  $1.00 / 100 cases needs explicit justification.
- **Docs sync** — `docs/PROJECT_MANAGEMENT.md` + `docs/CHANGELOG.md`
  updated at the end of every Phase, not in a batch later.
- **Multi-tenancy hygiene** (Phase 12+) — every new table carries
  `tenant_id`; every new Redis key is prefixed; every new background
  task accepts a tenant context. No exceptions, or the eventual
  multi-tenancy phase (Phase 21) turns into a rewrite.

## 10. Verification Checklist (per-phase smoke)

| Phase | Smoke command / observable |
|---|---|
| 1 | Load profile YAML → ingest to DB → generate one tailored Word resume + PDF |
| 2 | Scrape jobs from Greenhouse → score & rank → output top-N |
| 3 | Given a JD → auto-select bullets → tailored resume + CL + answer quick questions |
| 4 | For a Greenhouse job → auto-fill form → upload files → screenshot (no submit) |
| 5 | Run pipeline on 10 jobs → view tracking dashboard → analytics report |
| 6 | LinkedIn search → external ATS link resolution → existing apply / material pipeline |
| 7 | `autoapply web` → Vue SPA search / tracking / settings workflow |
| 8 | `/jobs` → `/materials?jobId=...` → DOCX/PDF generation, preview, validation, download |
| Agent 8 | `autoapply eval --suite agent_smoke` → all cases pass |
| Agent 9 | `autoapply eval --suite form_filler --min-pass-rate 0.85` → 5/5 pass, est. cost ≤ $0.25 |
| 10 | Settings page → connect / test / disconnect each provider; `autoapply provider test <name>` reports auth state accurately |
| 11 | Revoke primary provider mid-run → fallback chain kicks in → eval still passes; `autoapply migrate` cleans legacy state |
| 12 | Re-run same batch → LLM cache hit-rate > 80%, wall time < 20%, cost < 5%; Redis restart preserves L2 entries |
| 13 | Second visit to same search condition < 2s (no HTTP); job content change produces a new `job_snapshot`; revoke LinkedIn cookie → cached results still served |
| 13.9 | `alembic upgrade` → every legacy table carries `tenant_id='default'`; existing query paths unchanged (no regression) |
| 14 | `autoapply worker -Q materials` starts a Celery worker; 100 mixed tasks enqueue → routed by queue; kill a worker → `task_acks_late + task_reject_on_worker_lost` auto-requeues once; Celery Beat fires `daily_search` and only enqueues; agent returning `needs_human` transitions the task to `waiting_human` and the worker immediately picks up the next task |
| 15 | DOCX patch preserves named styles; three LaTeX templates compile from the same IR; cover-letter eval 5/5 pass; artifacts bind snapshot/source/template/trace IDs |
| 16 | Any rejected job in JobsView surfaces a reason chain in < 5s; agent cost < $0.50 per 100 jobs |
| 17 | Schedule custom batch tasks that produce N pre-tailored applications in review queue, each approvable in < 30s |
| 17.8 | Upload a trusted resume to the document library → set it as a default material source → regenerate a paused review entry with that source → promote the output back to the library |
| 17.9 | Connect/test every built-in provider class that has credentials available; Settings model picker lists curated/live catalogs; `tier="small"` routes extraction calls through the configured small provider |
| 18 | Enqueue real `materials.generate` and `application.*` tasks through Celery workers; worker loss requeues safely; DLQ/manual retry works; cleanup dry-run reports orphaned artifacts before deletion |
| 19 | Two tenants seeded with overlapping email / LinkedIn cookies → cannot read each other's jobs / snapshots / applications / credentials / Redis keys (verified by direct SQL and direct Redis CLI); quota exhaustion returns 429 |

## 11. Risk & Open Questions

- **LinkedIn rate-limiting / detection.** Mitigated by persistent
  context cookies, randomized delays, controlled concurrency, and
  the Phase 13 distributed-lock-gated force-refresh. Still a real
  risk for any aggressive custom schedule.
- **LLM cost drift.** Mitigated by the Phase 12 cache, the Phase 11
  fallback chain, the Phase 17.9 small-model tier, and the eval
  $1 / 100 ceiling. Cost telemetry is the early-warning.
- **Worker bodies still need activation.** Phase 14 landed the Celery
  skeleton, but Phase 18 is where long-running search, generation, and
  apply work move fully into real worker tasks with retry and DLQ behavior.
- **Arbitrary LaTeX is not zero-config.** Phase 15 accepts arbitrary
  templates only after a manifest/adapter exists and sample compile passes.
  A fully automatic import may still need user correction.
- **Single-instance assumption today.** Phase 14 + D018/D023 plant the
  multi-instance work. Phase 18 makes it real. Until then, do not
  run two `autoapply web` processes against the same Postgres /
  Redis — the data layer permits it but the absence of advisory
  locks invites double-submission races.
- **Auto-submit safety.** `--auto-submit` exists in `apply`, but
  still routes through the HITL gate. We have not yet seen the eval
  data that would justify removing the gate even per-vendor.
- **No SaaS business layer.** Phase 21 is multi-tenant hosting
  infra, not billing / signup / marketing. That work is out of
  scope until / unless a commercial license customer signs.
