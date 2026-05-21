<p align="center">
  <img src="docs/logo/AutoApply_logo.svg" alt="AutoApply Logo" width="400"/>
</p>

<p align="center">
  <a href="#product">Product</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#documentation">Docs</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#license">License</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.18.9-blue.svg" alt="Version"/>
  <img src="https://img.shields.io/badge/license-PolyForm_Noncommercial-green.svg" alt="License"/>
  <img src="https://img.shields.io/badge/python-3.12%2B-3776AB.svg" alt="Python"/>
  <img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/Vue-3.5-42B883.svg" alt="Vue"/>
  <img src="https://img.shields.io/badge/PostgreSQL-16%2B-4169E1.svg" alt="PostgreSQL"/>
</p>

---

AutoApply is a local-first job application automation workspace. It helps a job seeker discover roles, score fit, generate tailored application materials, prepare applications, and track outcomes while keeping every submit behind an explicit human decision.

The product combines a Vue/FastAPI operator console, PostgreSQL-backed job and application records, Redis/Celery background work, auditable agent traces, and a provider-agnostic LLM layer supporting OpenAI, Anthropic, Gemini, DeepSeek, Moonshot/Kimi, Qwen, xAI Grok, Groq, Mistral, OpenRouter, Ollama (local), the Claude and Codex CLIs, and user-defined custom OpenAI-compatible providers.

> **License**: [PolyForm Noncommercial 1.0.0](LICENSE). Personal, academic, and nonprofit use is free. Commercial use requires a separate license; see [Commercial Use](#commercial-use).

## Product

AutoApply is designed for users who want automation without losing control over sensitive application actions.

- **Job discovery**: Search LinkedIn-discovered roles and supported ATS platforms, normalize postings, and keep a durable Job Index.
- **Fit scoring**: Combine hard rules, semantic matching, freshness checks, and explainability for rejected or borderline jobs.
- **Applicant memory**: Maintain structured profile data, story bank entries, bullet pools, and reusable Q&A material.
- **Materials workspace**: Generate resumes and cover letters from a job or pasted JD, using templates, source documents, and evidence-grounded IR.
- **Document library**: Curate trusted resumes and cover letters, reuse them as generation bases, and promote generated artifacts when they are worth keeping.
- **Automation plans**: Create user-defined recurring tasks that search, scrape, score, prepare materials, and optionally auto-apply eligible jobs.
- **Review and submission**: Review prepared applications, replace materials, approve submissions, and keep submit actions gated.
- **Tracking and analytics**: Track application status, generated artifacts, outcomes, task history, and provider/agent cost telemetry.

## Current Status

Core product development is complete through **Phase 18: Worker Activation, Reliability, Parallelism, and automatic artifact Cleanup with quarantine/audit** (layered on top of Phase 17.9: LLM Provider Expansion).

The current application includes the Job Index, task queue with closed-out worker bodies (no fake `"scheduled"` / `"stubbed"` success returns), async materials generation that returns `task_id` + structured `TaskRecord.result`, a `dead_lettered` state + "Stuck / failed" tab for retries/discards, an automatic artifact-cleanup pipeline with quarantine + restore + purge, atomic-write protection on every materials writer, process-wide LLM rate-limit gates (global + per-provider), and the existing Phase 17.x surface (review queue, document library, material strategy defaults, multi-vendor provider management with per-provider model catalogs, Settings model picker, custom OpenAI-compatible providers, optional cheap-model tier, LinkedIn session management, modern Vue web console). Browser form-fill / click-submit, saved-search registry fanout, and outcome status sync are still explicit `not_implemented` paths rather than silent fake successes; legacy submit entrypoints now keep entries approved/queued instead of marking them submitted. The roadmap from here is **Phase 19** (Per-Posting Tag Cache & Filter Fast Path plus saved-search registry fanout) → **Phase 20** (Custom Job Sources / Connectors with URL safety, bounded multi-source search, and feature-gated template DSL) → **Phase 21** (Multi-tenancy & Auth Hardening) → future ATS-first application status sync.

Latest local verification in this workspace:

- `uv run pytest -q`: 1719 passed, 1 skipped (2026-05-21, after Phase 18)
- `npm run build`: passed, with the existing Vite chunk-size warning
- Alembic head: `b8d2f9e15c33` (Phase 18.3 DLQ columns); the Phase 18 schema chain is `e7c3a5b91f48` → `f4e8c1d2a907` → `a1c7b3e54f08` → `b8d2f9e15c33`

For implementation-level history, see [Phase History](docs/PHASE_HISTORY.md) and [Changelog](docs/CHANGELOG.md).

## Quick Start

Install dependencies once, then use the unified local launcher:

```powershell
# 1. Set AUTOAPPLY_DB_PASSWORD in .env (any non-empty value), then install deps
uv sync
uv run playwright install chromium
uv run autoapply init

# 2. Start Postgres + Redis, migrations, Celery worker/Beat, and the web UI
uv run autoapply start
```

Open the web console at `http://127.0.0.1:8000`.

`autoapply start` runs Docker Compose for **data dependencies**
(Postgres + Redis), applies Alembic migrations, starts Celery worker
and Beat, then launches the Python web app on the host. Use
`uv run autoapply start --check` to print the exact startup plan without
starting anything. If the default host ports are unavailable, the launcher
chooses alternate ports and passes them to Compose, Alembic, Celery, and
the web process for that run. It is cross-platform; Docker Desktop can be
auto-launched on Windows/macOS when installed, while Linux expects the
Docker daemon to already be available. For a single-server production install that runs the
same processes under one supervisor, see `supervisord.conf` and section
15 of [the deployment guide](docs/DEPLOYMENT.md).

If you modify frontend files:

```powershell
cd frontend
npm install
npm run build
```

The repository includes built SPA assets under `src/web/static/spa`, so a frontend rebuild is only required after editing `frontend/`.

## Requirements

| Area | Requirement |
|---|---|
| Python | Python 3.12+ with `uv` |
| Frontend | Node.js and npm for local SPA rebuilds |
| Database | PostgreSQL 16+ with pgvector |
| Browser | Playwright Chromium |
| Cache and queue | Redis for cache, locks, Celery broker, and Beat metadata |
| LLM provider | At least one of OpenAI, Anthropic, Gemini, DeepSeek, Moonshot, Qwen, xAI, Groq, Mistral, OpenRouter, a local Ollama server, the Claude / Codex CLIs, or a user-defined custom OpenAI-compatible endpoint |

## Common Commands

```powershell
# Database schema
uv run alembic upgrade head

# All-in-one local runtime
uv run autoapply start

# Web console only
uv run autoapply web --host 127.0.0.1 --port 8000

# Provider setup
uv run autoapply provider list
uv run autoapply provider set-key openai sk-...
uv run autoapply provider set-primary openai
uv run autoapply provider test openai

# Search and tracking
uv run autoapply search --source linkedin --keyword "software engineer" --location "Canada" --max-pages 3
uv run autoapply status

# Background workers
uv run autoapply worker --queues search,materials,application,maintenance
uv run autoapply beat

# Automation plan runs
uv run autoapply plan-runs run --profile default --top-n 10
uv run autoapply pause-plan-runs --clear-pending
uv run autoapply resume-plan-runs
```

Use the deployment guides for complete setup and production notes.

## Architecture

| Layer | Responsibility | Key Modules |
|---|---|---|
| Web console | Human-facing operator UI | `frontend/`, `src/web/` |
| Application services | Use cases shared by CLI and Web | `src/application/` |
| Job intelligence | Search, normalized postings, snapshots, freshness | `src/jobs/`, `src/intake/` |
| Matching | Rules, semantic scoring, explainability | `src/matching/` |
| Materials | Resume/cover letter IR, rendering, document library | `src/generation/`, `src/documents/` |
| Automation | Plan runs, review queue, digest | `src/orchestration/`, `src/review/` |
| Task execution | Celery tasks, queues, schedule, audit | `src/tasks/` |
| Agent harness | Bounded tools, traces, evals, HITL contracts | `src/agent/` |
| Persistence | SQLAlchemy models and Alembic migrations | `src/core/`, `migrations/` |
| Providers | LLM provider registry and credentials | `src/providers/`, `src/utils/llm.py` |

## Documentation

| Document | Purpose |
|---|---|
| [Deployment Guide](docs/DEPLOYMENT.md) | Installation, database, Redis, workers, and deployment operations |
| [部署与使用教程](docs/DEPLOYMENT_zh.md) | Chinese deployment and usage guide |
| [Project Management](docs/PROJECT_MANAGEMENT.md) | Current project state, next roadmap, verification baseline, and doc ownership |
| [Phase History](docs/PHASE_HISTORY.md) | Compact shipped-phase archive without README-level noise |
| [Changelog](docs/CHANGELOG.md) | Implementation-level change log and verification notes |
| [Architecture Decisions](docs/DECISIONS.md) | Accepted design decisions and rationale |
| [Agent Architecture](docs/AGENT_ARCHITECTURE.md) | Agent harness, tool boundary, HITL, trace, and eval contracts |
| [Implementation Plan](docs/plan_en.md) | Long-form planning reference in English |
| [实施计划](docs/plan_zh.md) | Long-form planning reference in Chinese |

## Project Layout

```text
frontend/             Vue SPA source
migrations/           Alembic schema migrations
src/application/      CLI/Web use cases
src/agent/            Agent harness, tools, traces, evals
src/cli/              autoapply command groups
src/core/             Models, configuration, database wiring
src/documents/        Template engines and user document storage
src/execution/        Browser automation and form filling
src/generation/       Resume and cover-letter generation
src/intake/           ATS and LinkedIn intake
src/jobs/             Job Index, snapshots, freshness, search cache
src/matching/         Rules, scoring, explainability
src/orchestration/    Plan runs and digest logic
src/providers/        LLM provider abstraction
src/tasks/            Celery app, tasks, Beat schedule, audit
src/web/              FastAPI routes and built SPA assets
tests/                Backend, route, eval, and integration tests
```

## Safety Model

AutoApply is not intended to be an unchecked autonomous submitter.

- Submit actions are gated by review flows and explicit approval.
- Agents run inside bounded tool registries; they do not receive arbitrary filesystem, database, browser, or network authority.
- Generated materials are evidence-grounded and traceable to profile facts, job snapshots, templates, and source documents.
- PostgreSQL stores durable application, task, review, and job-index state; Redis is treated as derived cache/queue state.
- The UI exposes review queues, task history, provider health, and downloadable artifacts for auditability.

## License

AutoApply is released under the **[PolyForm Noncommercial License 1.0.0](LICENSE)**.

### What this means

| | Allowed | Requires Commercial License |
|---|---|---|
| Run AutoApply to apply for your own jobs | Yes | |
| Personal experimentation, learning, hobby use | Yes | |
| Academic research, coursework, thesis projects | Yes | |
| Use by a registered nonprofit, public-research organization, or educational institution | Yes | |
| Read, modify, and redistribute the source code for noncommercial use | Yes | |
| Run AutoApply as a paid service for other job seekers | | Yes |
| Bundle AutoApply into a commercial product | | Yes |
| Use AutoApply inside a for-profit company's workflow | | Yes |
| Sell support, hosting, or modifications based on AutoApply | | Yes |

### Commercial Use

Commercial use is not granted under the default license. If your use case requires a commercial license, contact the author:

- **Email**: <frostnova986@gmail.com>
- **GitHub**: <https://github.com/Liam-Frost/AutoApply>

Please include your organization, intended use case, and expected scale.

### Required Notice

> Required Notice: Copyright (c) 2026 Liam Frost (frostnova986@gmail.com)

This notice must be preserved in any redistribution of the software, in source or binary form.

### Warranty Disclaimer

The software is provided as-is, without warranty. See the [full LICENSE text](LICENSE) for the legally binding terms.
