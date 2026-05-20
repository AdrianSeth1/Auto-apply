# Project Management

This document is the live operating context for AutoApply. It should stay short, current, and actionable. Historical implementation detail belongs in [Phase History](PHASE_HISTORY.md) and [Changelog](CHANGELOG.md). Architecture rationale belongs in [Architecture Decisions](DECISIONS.md).

## Current State

AutoApply is complete through **Phase 17.9: LLM Provider Expansion** (2026-05-19), itself layered on top of Phase 17.8.

The product currently supports job discovery, job-index freshness, fit scoring and explanations, materials generation, document-library curation, automation plans, review queues, gated submission, application tracking, multi-vendor LLM routing (OpenAI / Anthropic / Gemini / DeepSeek / Moonshot / Qwen / xAI / Groq / Mistral / OpenRouter / Ollama / Claude+Codex CLI / user-defined custom providers), per-provider model catalogs surfaced in the Settings UI, an optional cheap-model tier for extraction-style work, and background task execution.

The next planned hardening area is **Phase 18: Worker Activation, Reliability, Parallelism, Cleanup** (re-ordered 2026-05-19; refined 2026-05-20 to require full worker-stub closeout, durable task results/DLQ, and automatic artifact cleanup with quarantine/audit). **Phase 19: Per-Posting Tag Cache & Filter Fast Path** then drops the search-result-set cache in favour of snapshot-level objective tags and profile/scorer-version score caches, so the same JD snapshot is not re-scored on every search. **Phase 20: Custom Job Sources (Connectors)** introduces URL-safe user-added company careers sites (Nvidia, Microsoft, Stripe, etc.) on top of the LinkedIn / ATS intake we ship today, with bounded multi-source search and a feature-gated LLM template DSL for the long tail. Multi-tenancy & auth hardening, originally Phase 18, now lands as **Phase 21** once the personal-version product is feature-complete.

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
| 19 | Per-Posting Tag Cache & Filter Fast Path: drop search-result TTL cache; tags keyed by snapshot, scores keyed by snapshot + profile_version + scorer_version | Planned |
| 20 | Custom Job Sources (Connectors): URL-safe user sources, ATS auto-detection, multi-source search, and feature-gated LLM template DSL | Planned |
| 21 | Multi-tenancy and auth hardening (deferred from Phase 18 → 19 → 20) | Future |

### Phase 18 Working Scope

| Area | Intended Outcome |
|---|---|
| Worker stub closeout | Every registered / scheduled task body stops fake-succeeding. `materials.generate`, `application.prepare/fill/submit`, `jobs.enrich`, and maintenance tasks either call real use cases or explicitly return `not_implemented` and are removed from Beat/UI scheduling. |
| Async API + task result | Long-running material routes enqueue and return `task_id`; `GET /api/tasks/{task_id}` exposes durable status plus `TaskRecord.result` so the UI can retrieve generated artifacts after completion. |
| Reliability + DLQ | Exercise ack-late worker-loss behavior with real broker tests; add a durable `dead_lettered` state (or equivalent DLQ table), `last_attempted_at`, `dlq_reason`, and manual retry/discard actions backed by Postgres rather than transient Redis state. |
| Automatic cleanup | Daily artifact cleanup is in scope and enabled through a safe pipeline: build a DB-derived protected-path set, classify candidates, move eligible orphan/tmp/failed artifacts into `data/quarantine/<run_id>/`, write cleanup reports, and permanently purge only after the quarantine window. Manual `scan`, `clean`, `restore`, and `purge-quarantine` commands share the same rules. |
| Parallelism + rate limits | Add targeted concurrency for bullet rewrites, dual document generation, and JD parsing, but route LLM calls through global/provider-level semaphores so multiple workers cannot multiply per-task concurrency into provider abuse. |
| Sync fallback retirement | `AUTOAPPLY_SYNC_MATERIALS=1` is a short soak/debug escape hatch only. The UI defaults to async, and the fallback is removed or marked dev-only once Phase 18 passes. |

Phase 18 cleanup is intentionally not just dry-run. The safety mechanism is reference-based protection plus quarantine and auditability: user-owned library/source/template files and any DB-referenced artifact are protected, while temp files, failed intermediate artifacts, old screenshots, orphan outputs, and soft-deleted application artifacts are automatically moved out of `data/output` under category-specific retention rules.

### Phase 19 Working Scope

The current `search_results` TTL cache short-circuits whole result sets, which means a profile edit or a stale freshness state can hide jobs we already paid to fetch. Phase 19 moves the granularity from "result set" down to "single JD snapshot / posting analysis": searches always re-fetch upstream (so we never miss a new posting), but objective attributes are computed once per snapshot and profile-dependent scores are cached by snapshot + profile version + scorer version.

**Data model (sub-phase 19.1):**

| Change | Detail |
|---|---|
| `job_snapshots` tag columns | `tags JSONB DEFAULT '{}'` (A1 objective attributes), `tagger_version INT DEFAULT 0`, `tags_status TEXT DEFAULT 'pending'` (`pending` / `computing` / `ready` / `failed`), `tags_computed_at TIMESTAMPTZ`. `job_postings` may keep denormalized latest tags for UI speed, but snapshot tags are the source of truth. |
| New `job_posting_scores` table (A2) | FK `posting_id` + FK `snapshot_id` + `profile_id` + `profile_version TEXT` + `scorer_version TEXT` + optional `agent_version` / `model_id` + `score_breakdown JSONB` + `verdict TEXT` + `computed_at TIMESTAMPTZ`; `UNIQUE (tenant_id, snapshot_id, profile_id, profile_version, scorer_version)` so snapshot/profile/scorer changes naturally invalidate. |
| Indexing / retention | Hot indexes cover `(tenant_id, snapshot_id, profile_id, profile_version, scorer_version)`, `(tenant_id, profile_id, computed_at)`, and `(tenant_id, verdict, computed_at)`. Old score versions remain auditable but can be archived out of the hot table. |
| `tenant_id` | New columns/tables carry `tenant_id` from day one so Phase 21 doesn't have to retrofit it (D026 pattern). |

**Sub-phases 19.2 – 19.8:**

| Sub-phase | Scope |
|---|---|
| 19.2 `src/jobs/tagger.py` | Pure-function rule set over JD snapshots only: `work_mode` / `level` / `sponsorship_signal` / `intern_eligible` / `posting_age_bucket` / `clearance_required` / `usa_only`. A1 tags are profile-independent objective attributes; subjective labels belong to A2 score. |
| 19.3 `posting.tag` + `posting.tag_backfill` Celery tasks | `posting.tag` writes snapshot tags idempotently on content-hash change. `posting.tag_backfill` pages through stale `tagger_version` rows in batches and shows a UI banner while draining. |
| 19.4 `job_posting_scores` write-through | Filter Agent in `src/agent/` writes computed score keyed by `(tenant_id, snapshot_id, profile_id, profile_version, scorer_version)`. Read path checks only current profile/scorer versions before invoking the agent. |
| 19.5 `cached_search` refactor | `src/jobs/search.py` drops the TTL short-circuit; `search_results` rows stay (for "removed since" diffs and pagination) and the existing distributed lock stays (still want to prevent concurrent same-source scrapes) |
| 19.6 Filter fast-path | New `src/filter/fast_path.py`: A1 hard rules reject only when snapshot tags are `ready`; pending/computing tags show `Tagging...` and fall back to slow scoring; failed tags use ordinary scoring/manual retag, never default reject. A2 cache hits reuse current-version scores; misses enqueue the real Filter Agent. |
| 19.7 Frontend | JobsView shows tag chips on each posting (`Remote` / `Senior` / `7 days` / `Sponsorship needed`); spinner + "Tagging…" while `tags_status='pending'`; manual `POST /api/jobs/postings/{id}/retag`. ReviewQueueView shows `(cached score · profile vXYZ · scorer sABC)` so the user knows when and why a verdict came from cache. |
| 19.8 Docs sweep | README / PROJECT_MANAGEMENT / CHANGELOG; DECISIONS entry for snapshot-level A1 tags, A2 score keys, and the Phase 19 cross-source dedupe boundary. |

**Profile-version derivation:** `hashlib.sha256(canonical_json(filter_profile)).hexdigest()[:12]` — a profile edit changes the version. `scorer_version` is a separate constant bumped on hard-rule / prompt / agent behavior changes. Old `job_posting_scores` rows remain queryable for historical "why was this scored that way" audits, but hot reads use only the current profile/scorer versions.

**Scope notes / risks:**

- **`TAGGER_VERSION` bumps are expensive on a large index.** The retag enqueues are paginated background work — UI shows a banner while the index drains, and fast-path falls back to slow scoring rather than mis-rejecting.
- **Cache poisoning is bounded** because write-through is idempotent and the unique key includes tenant, snapshot, profile, profile version, and scorer version. Concurrent computes race-write the same value.
- **Search behavior intentionally changes:** searches no longer short-circuit on TTL — every search hits the upstream. This is a deliberate product choice for now; if real LinkedIn rate-limit/cookie-failure evidence appears later, source-aware degradation can be added in a separate phase.
- **Cross-source dedupe is not promised by Phase 19.** Phase 19 avoids repeated scoring for the same snapshot + profile/scorer version. LinkedIn + ATS + company-site versions of the same real-world job may still produce separate snapshots until a future canonical dedupe phase.

### Phase 20 Working Scope

Two layers, sequenced, with URL safety in front. Tier 1 (URL guard + ATS detection + connector registry + multi-source) is the required baseline; Tier 2 (LLM templates) handles the long tail and lands behind `custom_sources.llm_templates.enabled=false` by default so Tier 1 can ship without waiting on Tier 2 stability.

**Source URL Safety (sub-phase 20.0):**

| Area | Intended Outcome |
|---|---|
| URL guard | `POST /api/sources` validates before any fetch or Playwright navigation: only http/https, no localhost/private/metadata IPs, no `file://` / `ftp://` / `data:`, redirect revalidation on every hop, max redirect count, timeout, response-size, and content-type limits. |
| Playwright domain lock | Template execution may only visit the same registered domain or recognized ATS domains. Arbitrary cross-domain navigation, download, file upload, form submit, and arbitrary JS evaluation are forbidden. |

**Tier 1 — ATS connector framework + multi-source search (sub-phases 20.1 – 20.5):**

| Area | Intended Outcome |
|---|---|
| Source data model | `job_sources` stores user-configured source instances: id, `tenant_id`, display_name, url, connector_kind, ats_type, status, health_status, last_probe_at, last_error, created_by, created_at, updated_at. `Connector` is the capability definition; `JobSource` is the user's configured instance. No ambiguous `owner_tenant_id`. |
| `Connector` ABC | Uniform `fetch_jobs(source_config) -> list[RawJob]` interface in `src/intake/connectors/`. Existing LinkedIn / Greenhouse / Lever / Workday / Ashby / iCIMS adapters rewrap as connectors; registry pattern borrowed from `src/providers/registry.py`; fixture tests cover detect/fetch/normalize/dedupe. |
| ATS fingerprint detector | `src/intake/ats_detect.py` runs after URL guard, follows safe redirects, and DOM-sniffs careers URLs. Initial coverage target: Greenhouse, Lever, Workday, Ashby, iCIMS, Smartrecruiters, Eightfold. Unknown stays in `draft` until Tier 2 inference is explicitly enabled. |
| Source state machine + UX | Sources page supports `draft`, `probing`, `active`, `degraded`, `needs_review`, `disabled`, `deleted`. Only `active` participates in normal search; `degraded` gets low-frequency retry; `needs_review` does not auto-run; `disabled/deleted` never run. UI includes probe, disable/disconnect, clear session, and health details. |
| Multi-source search | `SearchPayload.sources: list[str]`; fan-out is bounded by `sources.max_concurrent_per_search`, per-source min interval, timeout, max pages, and max jobs. A failing source returns partial results and updates health instead of failing the whole search. |
| Minimal dedupe boundary | Phase 20 adds `canonical_fingerprint` (`normalized_company` + `normalized_title` + `normalized_location` + `canonical_application_url`) to mark `possible duplicate` in UI. Full `canonical_job_id` merge remains future work; scoring still follows Phase 19 snapshot cache. |
| Session isolation | Login state, when needed, lives at `data/sources/{tenant_id}/{source_id}/storage_state.json`; cookies do not live in `job_sources` JSONB. UI can clear sessions; source deletion quarantines/removes session state. |

**Tier 2 — LLM-assisted scraper templates for the long tail (sub-phases 20.6 – 20.8):**

| Area | Intended Outcome |
|---|---|
| Template schema + executor | `scraper_templates` stores constrained DSL, not arbitrary Playwright code: selectors for job card/title/company/location/application URL/next page plus max_pages. Allowed steps are `goto`, `wait_for_selector`, `click_next`, `scroll`, `extract`; arbitrary JS, form submit, file upload, download, and cross-domain navigation are forbidden. |
| LLM template inference | Default feature flag off. When ATS detection fails, Playwright fetches HTML + screenshot and `generate_json(tier="small")` emits a DSL candidate only. Activation requires preview of the first 5-10 extracted jobs, source links, and field selectors; user must explicitly Activate or the source remains `needs_review`. |
| Template self-heal | Per-source health probe (extends `src/providers/health.py` pattern) watches consecutive failure counts. Threshold breach queues an LLM re-inference; if the new recipe materially diverges from the current one the source flips to `needs_review` and surfaces in the review queue rather than auto-applying |

**Scope notes / risks to plan around:**

- **Anti-bot:** Cloudflare / Akamai JS challenges break on a fresh Playwright session. We'll likely need a residential-proxy escape hatch — Tier 1 only commits to ATS-backed sites (none of which gate on bot challenges); Tier 2 documents the limitation rather than promising universal coverage.
- **Login walls:** Some companies require an account. Tier 2 v1 does not automate login; users can manually authenticate and AutoApply persists per-source `storage_state` with a UI clear-session action.
- **LLM cost:** Template inference can run 20k+ token prompts (HTML is verbose). Cache aggressively, route via `tier="small"` by default, expose per-source token budgets so a misbehaving template can't burn the bill.
- **Maintenance burden:** Scraper templates rot. The self-heal loop is essential — without it Tier 2 becomes a graveyard of broken sources.
- **Security:** User-provided URLs, redirects, Playwright navigation, and LLM-generated selectors are constrained by 20.0 URL guard + template DSL. Any fetch path that bypasses URL guard is a P1 blocker.
- **Legal / ToS:** Document clearly that the user is responsible for ToS compliance with each careers site they add. Don't bundle a default list of company URLs — users opt in by adding their own.

**Testing requirements:** Connector fixture tests cover ATS detect, `fetch_jobs`, RawJob normalization, dedupe keys, source health, partial failure, URL/redirect guard, template DSL execution, and bad-selector preview. CI must not depend on live company websites.

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
