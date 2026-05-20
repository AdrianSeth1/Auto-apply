# AutoApply

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

Core product development is complete through **Phase 17.9: LLM Provider Expansion** (layered on top of Phase 17.8: Material Strategy & Document Library).

The current application includes the Job Index, task queue, plan-run automation, review queue, document library, material strategy defaults, multi-vendor provider management with per-provider model catalogs, a Settings model picker, custom OpenAI-compatible provider support, and an optional cheap-model tier, plus LinkedIn session management and the modern Vue web console. The roadmap from here is **Phase 18** (Worker Activation, Reliability, Parallelism, and automatic artifact Cleanup with quarantine/audit) → **Phase 19** (Per-Posting Tag Cache & Filter Fast Path: every search re-fetches upstream, while snapshot tags and profile/scorer-version scores are cached) → **Phase 20** (Custom Job Sources / Connectors with URL safety, bounded multi-source search, and feature-gated template DSL) → **Phase 21** (Multi-tenancy & Auth Hardening, deferred until the personal-version product is feature-complete).

Latest local verification in this workspace:

- `uv run pytest -q`: 1597 passed, 1 skipped (2026-05-19, after Phase 17.9)
- `npm run build`: passed, with the existing Vite chunk-size warning
- Legacy batch-run naming cleanup: no remaining obsolete product strings in source docs or built SPA assets

For implementation-level history, see [Phase History](docs/PHASE_HISTORY.md) and [Changelog](docs/CHANGELOG.md).

## Quick Start

Bring up Postgres (with pgvector) and Redis via the bundled Compose
file, then launch the app on the host:

```powershell
# 1. Set AUTOAPPLY_DB_PASSWORD in .env (any non-empty value)
docker compose up -d

# 2. Install Python deps + Chromium and run migrations
uv sync
uv run playwright install chromium
uv run alembic upgrade head
uv run autoapply init
uv run autoapply web
```

Open the web console at `http://127.0.0.1:8000`.

The Compose file only runs the **data dependencies** (Postgres + Redis).
The Python app stays on the host — Playwright and the docx→PDF
toolchain run more reliably native than in a container. For a
single-server production install that runs the web GUI, Celery
worker, and Beat under one supervisor, see `supervisord.conf` at the
repo root and section 15 of [the deployment guide](docs/DEPLOYMENT.md).

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

# Web console
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
