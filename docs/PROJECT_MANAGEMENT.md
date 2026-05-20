# Project Management

This document is the live operating context for AutoApply. It should stay short, current, and actionable. Historical implementation detail belongs in [Phase History](PHASE_HISTORY.md) and [Changelog](CHANGELOG.md). Architecture rationale belongs in [Architecture Decisions](DECISIONS.md).

## Current State

AutoApply is complete through **Phase 17.9: LLM Provider Expansion** (2026-05-19), itself layered on top of Phase 17.8.

The product currently supports job discovery, job-index freshness, fit scoring and explanations, materials generation, document-library curation, automation plans, review queues, gated submission, application tracking, multi-vendor LLM routing (OpenAI / Anthropic / Gemini / DeepSeek / Moonshot / Qwen / xAI / Groq / Mistral / OpenRouter / Ollama / Claude+Codex CLI / user-defined custom providers), per-provider model catalogs surfaced in the Settings UI, an optional cheap-model tier for extraction-style work, and background task execution.

The next planned hardening area is **Phase 18: Worker Activation, Reliability, Parallelism, Cleanup** (re-ordered 2026-05-19). **Phase 19: Per-Posting Tag Cache & Filter Fast Path** then drops the search-result-set cache in favour of per-posting attribute tags + per-profile cached scores, so adding more sources doesn't re-score the same posting on every search. **Phase 20: Custom Job Sources (Connectors)** introduces a registry-driven framework for user-added company careers sites (Nvidia, Microsoft, Stripe, etc.) on top of the LinkedIn / ATS intake we ship today, plus an LLM-assisted scraper-template generator for the long tail. Multi-tenancy & auth hardening, originally Phase 18, now lands as **Phase 21** once the personal-version product is feature-complete.

## Verification Baseline

Last local verification in this workspace:

| Check | Result |
|---|---|
| `uv run pytest -q` | 1597 passed, 1 skipped |
| `npm run build` | Passed; existing Vite chunk-size warning remains |
| `python -m py_compile` on plan-run modules | Passed |
| Product-string cleanup | No remaining legacy batch-run naming in source docs or built SPA assets |

When schema changes are present, run `uv run alembic upgrade head` before launching the web app. The current head is `e7c3a5b91f48`, which creates the `user_documents` table for the Document Library.

## Active Roadmap

| Phase | Scope | Status |
|---|---|---|
| 17.8 | Material strategy defaults, user document library, plan-level material overrides, replace-materials review actions | Complete |
| 17.9 | LLM provider expansion (more vendors, per-provider model catalog + UI picker, small-model tier, user-defined custom providers) | Complete |
| 18 | Worker activation, reliability, parallelism, cleanup | Planned |
| 19 | Per-Posting Tag Cache & Filter Fast Path: drop search-result TTL cache; tag/score caches keyed by posting + profile_version | Planned |
| 20 | Custom Job Sources (Connectors): ATS auto-detection + multi-source search + LLM scraper templates | Planned |
| 21 | Multi-tenancy and auth hardening (deferred from Phase 18 → 19 → 20) | Future |

### Phase 18 Working Scope

| Area | Intended Outcome |
|---|---|
| Worker activation | Move from "Celery code present" to "workers actually run the queues end-to-end under supervisord, with health checks and shutdown semantics" |
| Reliability | Per-task retry policy, dead-letter routing, idempotency keys for resubmission-prone tasks, structured failure surfacing in the review UI |
| Parallelism | Concurrency tuning per queue (`search` / `materials` / `application` / `maintenance`), rate-limit coordination, lock hygiene |
| Cleanup | Retention policies for traces, plan-run reports, browser artifacts; bounded log rotation under supervisord |

### Phase 19 Working Scope

The current `search_results` TTL cache short-circuits whole result sets, which means a profile edit or a stale freshness state can hide jobs we already paid to fetch. Phase 19 moves the granularity from "result set" down to "single posting": searches always re-fetch (so we never miss a new posting), but the per-posting objective attributes are computed once and the per-profile score is cached and reused across searches.

**Data model (sub-phase 19.1):**

| Change | Detail |
|---|---|
| `job_postings` new columns | `tags JSONB DEFAULT '{}'` (A1 objective attributes), `tagger_version INT DEFAULT 0` (global re-tag hook), `tags_status TEXT DEFAULT 'pending'` (`pending` / `computing` / `ready` / `failed`), `tags_computed_at TIMESTAMPTZ` |
| New `job_posting_scores` table (A2) | FK `posting_id` + FK `snapshot_id` + `profile_id` + `profile_version TEXT` + `score_breakdown JSONB` + `verdict TEXT` + `computed_at TIMESTAMPTZ`; `UNIQUE (snapshot_id, profile_version)` so a snapshot change or a profile edit naturally invalidates |
| `tenant_id` | Both new columns/table carry `tenant_id` from day one so Phase 21 doesn't have to retrofit it (Codex D026 pattern) |

**Sub-phases 19.2 – 19.8:**

| Sub-phase | Scope |
|---|---|
| 19.2 `src/jobs/tagger.py` | Pure-function rule set: `work_mode` / `level` / `sponsorship_signal` / `intern_eligible` / `posting_age_bucket` / `clearance_required` / `usa_only`. Module-level `TAGGER_VERSION` constant — bumping it on a rule change forces a full re-tag, recorded on the row |
| 19.3 `posting.tag` Celery task | New task kind in `src/tasks/tasks.py`; idempotent, writes back `tags` + `tags_status='ready'`. `src/jobs/enrich.py:on_content_changed` listener enqueues it whenever a snapshot's content hash changes |
| 19.4 `job_posting_scores` write-through | Filter Agent in `src/agent/` writes its computed score back into the new table keyed by `(snapshot_id, profile_version)`. Read path checks the table before invoking the agent |
| 19.5 `cached_search` refactor | `src/jobs/search.py` drops the TTL short-circuit; `search_results` rows stay (for "removed since" diffs and pagination) and the existing distributed lock stays (still want to prevent concurrent same-source scrapes) |
| 19.6 Filter fast-path | New `src/filter/fast_path.py`: A1 hard rules reject up front; A2 cached score reused when `(snapshot_id, profile_version)` hits; otherwise enqueue the real Filter Agent. Plan-run picker and Jobs view both route through it |
| 19.7 Frontend | JobsView shows tag chips on each posting (`Remote` / `Senior` / `7 days` / `Sponsorship needed`); spinner + "Tagging…" while `tags_status='pending'`; manual `POST /api/jobs/postings/{id}/retag`. ReviewQueueView shows `(cached score · profile vXYZ)` so the user knows when a verdict came from cache |
| 19.8 Docs sweep | README / PROJECT_MANAGEMENT / CHANGELOG; DECISIONS entry for the A1 + A2 split |

**Profile-version derivation:** `hashlib.sha256(canonical_json(filter_profile)).hexdigest()[:12]` — a profile edit changes the version, old `job_posting_scores` rows naturally age out without an explicit cache bust. Acceptable cold-storage cost; we don't delete them so historical "why was this scored that way" stays queryable.

**Scope notes / risks:**

- **`TAGGER_VERSION` bumps are expensive on a large index.** The retag enqueues are paginated background work — UI shows a banner while the index drains, ranking falls back to "untagged" filtering in the meantime.
- **Cache poisoning isn't a concern** because the write-through is idempotent and the unique key is content-derived. Concurrent computes race-write the same value.
- **Search behavior intentionally changes:** searches no longer short-circuit on TTL — every search hits the upstream. Justified because the cost was masking new postings; the per-posting cache keeps the hot path cheap on the analysis side, not the fetch side.

### Phase 20 Working Scope

Two layers, sequenced. Tier 1 (ATS detection + multi-source) is the high-value baseline; Tier 2 (LLM templates) handles the long tail and lands behind feature gating so we can ship Tier 1 to users without waiting on Tier 2 stability.

**Tier 1 — ATS connector framework + multi-source search (sub-phases 20.1 – 20.5):**

| Area | Intended Outcome |
|---|---|
| Source data model | New `job_sources` table (id, display_name, kind, url, ats_type, owner_tenant_id, status, last_health) + alembic migration; carries `tenant_id` from day one so Phase 21 doesn't have to retrofit it |
| `Connector` ABC | Uniform `fetch_jobs(source_config) -> list[RawJob]` interface in `src/intake/connectors/`. Existing LinkedIn / Greenhouse / Lever / Workday / Ashby / iCIMS adapters rewrap as connectors; registry pattern borrowed from `src/providers/registry.py` |
| ATS fingerprint detector | `src/intake/ats_detect.py` follows redirects and DOM-sniffs to identify which ATS backs a careers URL. Initial coverage target: Greenhouse, Lever, Workday, Ashby, iCIMS, Smartrecruiters, Eightfold. Unknown → connector stays in `draft` until Tier 2 inference runs |
| Add-source UX | `POST /api/sources` runs the detector + a verification fetch, persists on success. New "Sources" page in the Web UI mirrors the Settings provider list shape: connected vs available, health badges, manual probe / disconnect actions |
| Multi-source search | `SearchPayload.sources: list[str]`; Celery group fan-out runs each source in parallel, results merged + deduped by `(source_id, source_source_id)`. Job-search form and plan-run form both grow a source multi-select. Plans persist `source_ids` so Beat reuses the same allowlist each tick. Per-posting cache from Phase 19 means repeated postings across sources skip re-scoring entirely |

**Tier 2 — LLM-assisted scraper templates for the long tail (sub-phases 20.6 – 20.8):**

| Area | Intended Outcome |
|---|---|
| Template schema + executor | `scraper_templates` table (`selector_recipe: jsonb`, `playwright_steps: jsonb`, `health: jsonb`) + a Playwright-driven connector that executes a template against a URL and yields `RawJob`. Steps cover login walls, pagination, infinite scroll |
| LLM template inference | When ATS detection fails, fetch the page through Playwright (HTML + screenshot), feed a structured prompt via `generate_json(tier="small")` to emit a candidate recipe. Result is cached on the template row; user reviews + edits in a JSON editor with a "Test on this page" preview button before activation |
| Template self-heal | Per-source health probe (extends `src/providers/health.py` pattern) watches consecutive failure counts. Threshold breach queues an LLM re-inference; if the new recipe materially diverges from the current one the source flips to `needs_review` and surfaces in the review queue rather than auto-applying |

**Scope notes / risks to plan around:**

- **Anti-bot:** Cloudflare / Akamai JS challenges break on a fresh Playwright session. We'll likely need a residential-proxy escape hatch — Tier 1 only commits to ATS-backed sites (none of which gate on bot challenges); Tier 2 documents the limitation rather than promising universal coverage.
- **Login walls:** Some companies require an account. Reuse the LinkedIn session pattern (per-source storage_state) — explicitly out of scope for Tier 2 v1; users can manually authenticate and AutoApply persists the session.
- **LLM cost:** Template inference can run 20k+ token prompts (HTML is verbose). Cache aggressively, route via `tier="small"` by default, expose per-source token budgets so a misbehaving template can't burn the bill.
- **Maintenance burden:** Scraper templates rot. The self-heal loop is essential — without it Tier 2 becomes a graveyard of broken sources.
- **Legal / ToS:** Document clearly that the user is responsible for ToS compliance with each careers site they add. Don't bundle a default list of company URLs — users opt in by adding their own.

### Phase 21 Working Scope (deferred — kept here as a planning placeholder)

| Area | Intended Outcome |
|---|---|
| Tenant and user model | First-class `tenants` and `users` tables replacing implicit single-user assumptions |
| Auth middleware | FastAPI request identity and tenant context instead of ambient `default` fallback |
| PostgreSQL isolation | Row-level security or equivalent tenant filtering discipline for user data |
| Redis namespace hardening | Per-tenant keys for cache, locks, task metadata, and rate limits |
| Credential isolation | Provider credentials scoped by tenant/user rather than global project state |
| Audit and quotas | Durable audit events and usage limits suitable for a future hosted deployment |

## Architecture Snapshot

| Area | Source of Truth | Notes |
|---|---|---|
| Web UI | `frontend/` | Vue 3, Vue Router, Vite, Tailwind, shadcn-style components |
| API | `src/web/` | FastAPI JSON routes plus built SPA serving |
| Use cases | `src/application/` | Shared by CLI and Web; should own session lifecycle where possible |
| Persistence | `src/core/models.py`, `migrations/` | PostgreSQL + pgvector via SQLAlchemy/Alembic |
| Cache and queue | Redis, `src/cache/`, `src/tasks/` | Redis is derived state; PostgreSQL remains durable state |
| Background work | Celery, `src/tasks/beat.py` | Queues: `search`, `materials`, `application`, `maintenance` |
| Job intelligence | `src/jobs/`, `src/intake/` | Normalized search queries, immutable snapshots, freshness state |
| Materials | `src/generation/`, `src/documents/` | Template rendering, source patching, document library, material defaults |
| Automation | `src/orchestration/`, `src/application/review.py` | Plan runs, review queue, digest, gated submission |
| Agents | `src/agent/` | Bounded tool registry, trace store, eval suites, HITL contracts |
| Providers | `src/providers/`, `src/utils/llm.py` | Registry-driven multi-vendor LLM routing: OpenAI, Anthropic, Gemini, DeepSeek, Moonshot/Kimi, Qwen, xAI Grok, Groq, Mistral, OpenRouter, Ollama (local), Claude CLI, Codex CLI, plus user-defined custom providers via `llm.custom_providers`. Optional cheap-model tier through `llm.small_provider` / `llm.small_model`. |

## Operator Commands

| Task | Command |
|---|---|
| Upgrade database | `uv run alembic upgrade head` |
| Start web console | `uv run autoapply web --host 127.0.0.1 --port 8000` |
| Start all-purpose worker | `uv run autoapply worker --queues search,materials,application,maintenance` |
| Start scheduler | `uv run autoapply beat` |
| Build frontend | `cd frontend; npm run build` |
| Run backend tests | `uv run pytest -q` |
| Run targeted task tests | `uv run pytest tests/test_web_routes_tasks.py tests/test_tasks_beat.py tests/test_tasks_kinds.py -q` |

## Documentation Ownership

| Document | Role | Keep Out |
|---|---|---|
| `README.md` | Product overview, quick start, architecture map, doc index, license | Long phase logs, sub-phase implementation detail, internal review notes |
| `docs/PROJECT_MANAGEMENT.md` | Current project state, next roadmap, verification baseline, operating context | Historical phase narratives and duplicated changelog entries |
| `docs/PHASE_HISTORY.md` | Compact shipped-phase archive | Per-commit implementation bullets |
| `docs/CHANGELOG.md` | Detailed implementation changes, migrations, tests, review fixes | Product marketing copy and future planning prose |
| `docs/DECISIONS.md` | Design decisions with rationale and trade-offs | Status tracking and implementation logs |
| `docs/AGENT_ARCHITECTURE.md` | Agent/tool/HITL/trace/eval contracts | General product roadmap |
| `docs/DEPLOYMENT.md` and `docs/DEPLOYMENT_zh.md` | Installation, operations, service commands, troubleshooting | Architecture debate and phase history |
| `docs/plan_en.md` and `docs/plan_zh.md` | Long-form planning reference | Current status claims that need frequent updates |

## Maintenance Rules

- Keep README product-facing and concise.
- Update this file when the active phase, verification baseline, or next roadmap changes.
- Update CHANGELOG when a feature, migration, route, CLI command, or behavior change lands.
- Update DECISIONS only for architectural choices that should be stable and citeable.
- Do not duplicate long implementation bullets across README, PROJECT_MANAGEMENT, and CHANGELOG.
- Prefer current product names: `plan_run`, plan runs, automation plans, review queue, document library.
