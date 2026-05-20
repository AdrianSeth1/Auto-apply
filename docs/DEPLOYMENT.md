# AutoApply Deployment And Usage Guide

This guide documents the real, working setup flow for the current project.
It covers local deployment, first-time initialization, CLI usage, and the Vue-based web GUI.

## 1. What You Are Deploying

AutoApply currently supports:

- ATS job intake from Greenhouse and Lever
- LinkedIn search with external ATS redirect discovery and durable Job Index records
- Materials workspace for tailored resume and cover letter generation per job or pasted JD
- Document library for trusted resumes/cover letters, generated artifact promotion, and material strategy defaults
- DOCX-first template packages with manifest-driven styles, capacity, preview, validation, and uploads
- QA template loading from `qa_bank`
- Browser automation for Greenhouse, Lever, and Ashby applications
- Multi-vendor LLM routing across REST providers, local Ollama, local CLIs, and user-defined OpenAI-compatible endpoints
- Application tracking, analytics, CSV export, background task execution, and the Vue web GUI

Direct apply support is currently implemented for:

- Greenhouse
- Lever
- Ashby

## 2. Prerequisites

Install these before starting:

- Python `3.12+`
- `uv`
- PostgreSQL `16+`
- PostgreSQL `pgvector` extension
- Redis `7+` (required from Phase 12 onward as the cache / distributed-lock / queue substrate; the current code does not yet require it, but every release from v0.12 will. Install now so the upgrade is seamless.)
- Chromium for Playwright
- At least one document-to-PDF path:
  - Microsoft Word + `docx2pdf`, or
  - LibreOffice
- At least one LLM provider: an API key for a hosted provider, a local Ollama server, Claude Code CLI, Codex CLI, or a user-defined OpenAI-compatible endpoint
- Node.js and npm only if you plan to rebuild the frontend assets locally

## 3. Clone And Install

```bash
git clone https://github.com/Liam-Frost/AutoApply.git
cd AutoApply
uv sync
uv run playwright install chromium
```

## 3.1 Frontend Build

The repository already includes built frontend assets under `src/web/static/spa`.
You only need this step when you modify files under `frontend/`.

```bash
cd frontend
npm install
npm run build
cd ..
```

## 3.2 Configure An LLM Provider

AutoApply uses its own provider registry rather than vendor SDKs. You can use hosted REST APIs, local Ollama, local CLIs, or a custom OpenAI-compatible endpoint.

Supported built-in providers include:

- OpenAI, Anthropic, Gemini, DeepSeek, Moonshot/Kimi, Qwen, xAI Grok, Groq, Mistral, and OpenRouter
- Ollama for local models
- Claude Code CLI and Codex CLI

If you use the local CLIs, install them on the same machine that runs AutoApply:

```bash
npm install -g @anthropic-ai/claude-code
npm install -g @openai/codex
```

Then complete each CLI's own local sign-in/auth flow before relying on LLM-backed parsing or generation.

Recommended setup:

- configure one primary provider
- optionally configure one or more fallback providers
- optionally configure `llm.small_provider` / `llm.small_model` for cheaper extraction tasks

After `uv sync`, the project exposes the CLI entrypoint. You can use either:

```bash
autoapply --help
```

or the more portable form:

```bash
uv run autoapply --help
```

## 4. Database And Cache Setup

You need PostgreSQL 16+ with the `pgvector` extension, and Redis 7+.
Pick one of the two paths below.

### 4.A Docker Compose (recommended for new installs)

The repo ships a `docker-compose.yml` that runs both services with
sensible defaults:

```bash
# Put a real password in .env first; compose will refuse to start
# without it.
echo "AUTOAPPLY_DB_PASSWORD=change-me" >> .env
docker compose up -d
docker compose ps   # both services should be `healthy`
```

The Postgres container uses the `pgvector/pgvector:pg16` image, so
the `vector` extension is already installed and the initial
migration's `CREATE EXTENSION` succeeds without extra steps. Data
lives in the named volumes `postgres-data` and `redis-data`; back
Postgres up with `pg_dump`, not by copying the raw files.

Compose only runs the data dependencies. The Python app (web GUI,
worker, beat) still runs natively on the host — Playwright and the
docx→PDF toolchain are easier to keep working that way.

### 4.B Native install (existing PostgreSQL / Redis on the host)

Create the database and user manually:

```sql
CREATE USER autoapply WITH PASSWORD 'change-me';
CREATE DATABASE autoapply OWNER autoapply;
\c autoapply
CREATE EXTENSION IF NOT EXISTS vector;
```

Install Redis 7+ from your distribution's package manager and make
sure it listens on `localhost:6379`.

## 5. Configure Environment

Copy the example environment file:

```bash
cp config/.env.example .env
```

Set the database values in `.env`:

```env
AUTOAPPLY_DB_HOST=localhost
AUTOAPPLY_DB_PORT=5432
AUTOAPPLY_DB_NAME=autoapply
AUTOAPPLY_DB_USER=autoapply
AUTOAPPLY_DB_PASSWORD=change-me
AUTOAPPLY_LOG_LEVEL=INFO
```

Notes:

- `config/settings.yaml` provides defaults
- `.env` overrides those defaults
- environment variables override both
- LLM provider priority is stored in `config/settings.yaml`

## 6. Run Migrations

```bash
uv run alembic upgrade head
```

You can also let `autoapply init` validate the database and run migrations for you.

## 7. First-Time Initialization

### Option A: interactive setup

```bash
uv run autoapply init
```

### Option A1: setup with explicit LLM priority

```bash
uv run autoapply init --llm-primary claude-cli --llm-fallback codex-cli
```

or:

```bash
uv run autoapply init --llm-primary codex-cli --llm-fallback claude-cli
```

### Option B: import an existing structured profile

```bash
uv run autoapply init --profile data/profile/profile.yaml
```

### Option C: parse a resume into a profile

```bash
uv run autoapply init --resume "/path/to/resume.pdf"
```

Useful flags:

```bash
uv run autoapply init --skip-db
uv run autoapply init --skip-llm
```

What `init` does:

- validates `config/settings.yaml` and `.env`
- tests database connectivity
- runs Alembic migrations
- imports or creates `data/profile/profile.yaml`
- checks configured LLM provider availability where possible
- stores preferred primary/fallback LLM settings when you pass `--llm-primary` / `--llm-fallback`

### 7.1 LLM provider priority

Current config lives in `config/settings.yaml`:

```yaml
llm:
  provider: codex-cli
  primary_provider: codex-cli
  fallback_provider: claude-cli
  allow_fallback: true
  fallback_providers:
    - claude-cli
  small_provider: claude-cli
  small_model: null
```

Meaning:

- `primary_provider`: provider tried first
- `fallback_provider`: legacy single fallback provider
- `fallback_providers`: ordered fallback list when more than one fallback is configured
- `allow_fallback`: whether AutoApply should fail over automatically
- `small_provider` / `small_model`: optional cheaper route for extraction-style work

The Settings UI exposes provider connection, health, and model selection. `GET /api/providers/{id}/models` merges curated model catalogs with live runtime catalogs where available, such as Ollama.

To use an OpenAI-compatible endpoint that is not built in, add an entry under `llm.custom_providers` in `config/settings.yaml`; built-in provider ids always take precedence on collision.

## 7.2 LLM fallback behavior

There are two levels of fallback:

### Provider-level fallback

- if `primary_provider` fails, times out, or is missing
- and `allow_fallback` is enabled
- AutoApply tries the configured fallback provider chain

This works across REST providers, local Ollama, and local CLI providers when credentials or local runtimes are configured.

### Feature-level fallback

Even after provider calls fail, several features still degrade gracefully:

- JD parsing -> regex heuristics fallback
- cover letter generation -> deterministic template fallback
- resume bullet rewrite -> keep original bullet
- QA generation -> template answers or manual review fallback
- unsupported / risky answers -> explicit human review

## 8. Profile And Config Files

Main files you will edit:

- `data/profile/profile.yaml`
- `data/profile/profiles/<profile_id>.yaml`
- `data/templates/<document_type>/<template_id>/manifest.json`
- `config/settings.yaml`
- `config/filters.yaml`
- `config/companies.yaml`

Guidance:

- put your identity, education, experiences, projects, skills, `story_bank`, and `qa_bank` in `profile.yaml`
- keep resume and cover letter templates as packages under `data/templates/`
- put ATS company slugs in `companies.yaml`
- define matching filters in `filters.yaml`

## 9. Job Search Workflow

### ATS search

```bash
uv run autoapply search --profile default --score
```

### ATS search for one company or ATS

```bash
uv run autoapply search --ats greenhouse --company stripe --score
```

### LinkedIn search

```bash
uv run autoapply search \
  --source linkedin \
  --keyword "software engineer intern" \
  --location "United States" \
  --time-filter week \
  --max-pages 3
```

### Combined search

```bash
uv run autoapply search --source all --keyword "backend intern" --score
```

Notes:

- first LinkedIn use may require interactive login
- LinkedIn results can be enriched with external ATS links
- LinkedIn search uses a local file cache under `data/cache/linkedin_search/`; clear it from Settings or delete cached JSON if you need a fresh scrape
- scoring requires a valid `data/profile/profile.yaml`

## 10. Application Workflow

### Apply to one ATS URL

```bash
uv run autoapply apply --url https://boards.greenhouse.io/company/jobs/123
```

### Dry run only

```bash
uv run autoapply apply --url https://jobs.lever.co/company/abc123/apply --dry-run
```

### Apply by tracked database job id

```bash
uv run autoapply apply --job-id <uuid>
```

### Batch apply to top matches

```bash
uv run autoapply apply --batch --top-n 5 --profile default
```

### Auto-submit instead of pausing for review

```bash
uv run autoapply apply --url <ats-url> --auto-submit
```

What happens during `apply` now:

- detects ATS type from the target URL
- loads job context from DB or ATS API when possible
- generates a job-specific resume and cover letter
- loads QA answers from `qa_bank`
- creates a tracked `Application` record
- fills the form with Playwright
- syncs screenshots and state back into tracking

## 11. Web Dashboard

Start the web UI:

```bash
uv run autoapply web --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Pages:

- `/` dashboard
- `/jobs` job search and apply actions
- `/materials` resume and cover letter generation workspace
- `/applications` tracking and outcome updates
- `/profile` profile inspection and resume import
- `/settings` LLM provider priority and fallback settings

Use `/settings` after deployment if you want to change the primary or fallback provider without editing YAML by hand.

### 11.1 Materials Workspace

Use `/materials` when you want to generate documents without immediately filling an application form.

Main flow:

1. Start from `/jobs` and click `Generate Apply Materials`, or open `/materials` directly.
2. Choose a search result or paste a complete JD.
3. Choose the saved applicant profile.
4. Choose Resume and/or Cover Letter, select DOCX/PDF formats, and select templates.
5. Generate materials, expand preview when needed, then download selected artifacts.

Notes:

- Resume outputs support DOCX and PDF.
- Cover Letter outputs support DOCX and PDF in the UI.
- Preview is collapsed by default so downloads and validation status stay easy to scan.
- Generated artifacts are downloaded through `/api/artifacts/download`, restricted to `data/output`.

### 11.2 Template Library

The Materials page includes a Template Library modal for low-frequency template management.

Template packages live under:

```text
data/templates/resume/<template_id>/
data/templates/cover_letter/<template_id>/
```

Each package contains:

- `template.docx` — Word document that owns visual styling
- `manifest.json` — template metadata, named Word styles, section order, blocks, and capacity limits
- `style.lock.json` — renderer-facing style/block contract
- `sample_resume.json` or `sample_cover_letter.json` — sample IR payload placeholder

Uploads accept `.docx` files up to 10 MiB. The server assigns a safe template ID, adds required styles and block markers if missing, validates the package, and refreshes the template list.

### 11.3 Materials API Surface

The Vue app uses these JSON endpoints:

- `POST /api/jobs/generate-material`
- `GET /api/templates`
- `POST /api/templates/upload`
- `GET /api/artifacts/download?path=...`

These endpoints validate template IDs, profile IDs, artifact paths, and upload size before touching files.

## 12. Tracking And Export

Show analytics in CLI:

```bash
uv run autoapply status
```

Export CSV:

```bash
uv run autoapply status --export-csv report.csv
```

Filter recent applications:

```bash
uv run autoapply status --company Stripe --status SUBMITTED --outcome interview
```

## 13. Recommended Deployment Modes

### Developer laptop / personal workstation

- use `uv run autoapply web --reload`
- keep PostgreSQL local
- keep Playwright and LibreOffice on the same machine

### Single Linux server or VM

- install Python, PostgreSQL, LibreOffice, and Chromium
- install and authenticate Claude Code CLI and/or Codex CLI on the same server
- run PostgreSQL locally or use a managed database
- launch the web GUI behind a reverse proxy
- run the CLI manually or from a scheduler

Example web GUI command on a server:

```bash
uv run autoapply web --host 0.0.0.0 --port 8000 --no-open
```

## 14. Production Deployment On Linux

This section describes a practical production-style deployment for the current Vue web GUI.

Suggested layout:

- app user: `autoapply`
- app path: `/opt/autoapply`
- service bind: `127.0.0.1:8000`
- public access: Nginx reverse proxy on `80/443`

### 14.1 Install system packages

Example for Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv postgresql libpq-dev libreoffice nginx curl
```

Install `uv` if it is not already installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 14.2 Create a dedicated service user

```bash
sudo useradd --system --create-home --home-dir /opt/autoapply --shell /bin/bash autoapply
```

### 14.3 Deploy the code

```bash
sudo -u autoapply git clone https://github.com/Liam-Frost/AutoApply.git /opt/autoapply
cd /opt/autoapply
sudo -u autoapply /opt/autoapply/.local/bin/uv sync
sudo -u autoapply /opt/autoapply/.local/bin/uv run playwright install chromium
```

If you change the Vue frontend on the server, rebuild it before starting the app:

```bash
cd /opt/autoapply/frontend
sudo -u autoapply npm install
sudo -u autoapply npm run build
```

If `/opt/autoapply/.local/bin/uv` is different on your server, replace it with the actual `uv` path.

### 14.4 Configure environment and database

```bash
cd /opt/autoapply
sudo -u autoapply cp config/.env.example .env
sudo -u autoapply editor .env
sudo -u autoapply /opt/autoapply/.local/bin/uv run alembic upgrade head
```

At minimum, set the database fields in `.env`.

### 14.5 Run the web app manually once

```bash
sudo -u autoapply /opt/autoapply/.local/bin/uv run autoapply web --host 127.0.0.1 --port 8000 --no-open
```

Confirm that `http://127.0.0.1:8000` responds locally before adding `systemd` or Nginx.

## 15. Process Supervision

AutoApply has three long-lived processes in production:

| Process | Purpose | Command |
|---|---|---|
| `autoapply-web` | FastAPI + Vue console | `autoapply web --no-open --host 127.0.0.1` |
| `autoapply-worker` | Celery worker for all four queues | `autoapply worker` |
| `autoapply-beat` | Celery Beat with redbeat (cron driver) | `autoapply beat` |

You only need **one** Beat instance across the deployment; redbeat
provides leader election if you accidentally run more.

Pick **one** of the two supervisor options below.

### 15.A supervisord (recommended — runs all three)

The repo ships a ready-to-use `supervisord.conf` at the project root
that supervises all three processes with auto-restart, log rotation,
and group control (`supervisorctl restart autoapply:`).

```bash
sudo apt install -y supervisor
sudo cp /opt/autoapply/supervisord.conf /etc/supervisor/conf.d/autoapply.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status
```

If `uv` is not at `/opt/autoapply/.local/bin/uv`, edit the
`command=` and `environment=PATH=` lines in `supervisord.conf` once.

Common operations:

```bash
sudo supervisorctl tail -f autoapply-web stderr
sudo supervisorctl restart autoapply:           # restart all three
sudo supervisorctl restart autoapply-worker     # restart just the worker
```

### 15.B systemd (web only)

If you only want the web GUI under a supervisor and intend to run
the worker / beat by hand or from cron, a single systemd unit is the
lightest option. Create `/etc/systemd/system/autoapply-web.service`:

```ini
[Unit]
Description=AutoApply Web Dashboard
After=network.target

[Service]
Type=simple
User=autoapply
Group=autoapply
WorkingDirectory=/opt/autoapply
Environment=HOME=/opt/autoapply
ExecStart=/opt/autoapply/.local/bin/uv run autoapply web --host 127.0.0.1 --port 8000 --no-open
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

If `uv` is installed somewhere else, replace the `ExecStart` path.

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable autoapply-web
sudo systemctl start autoapply-web
sudo systemctl status autoapply-web
```

View logs:

```bash
sudo journalctl -u autoapply-web -f
```

## 16. Nginx Reverse Proxy

Create `/etc/nginx/sites-available/autoapply`:

```nginx
server {
    listen 80;
    server_name autoapply.example.com;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/autoapply /etc/nginx/sites-enabled/autoapply
sudo nginx -t
sudo systemctl reload nginx
```

### 16.1 Enable HTTPS

If the server is public, add TLS with Certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d autoapply.example.com
```

## 17. Production Notes

- bind the app to `127.0.0.1` and expose it through Nginx
- keep PostgreSQL credentials only in `.env` or environment variables
- run the service under a dedicated non-root user
- keep `logs/`, `data/output/`, and `data/.linkedin_session/` writable by the service user
- keep `data/templates/` writable only if users need to upload templates from the Web UI
- keep any configured local LLM CLI binaries installed and authenticated for the same service user
- if you use LinkedIn search on a server, the first login may still require an interactive browser session
- Playwright-based apply jobs are better suited to trusted internal use than a public multi-user SaaS deployment

## 18. Operational Notes

- `apply` automation is currently for Greenhouse, Lever, and Ashby
- LinkedIn is primarily for search and ATS link discovery
- PDF conversion depends on Word/docx2pdf or LibreOffice
- LLM-dependent features degrade gracefully when providers are unavailable, but some parsing/generation quality will drop
- the default workflow is human-in-the-loop; auto-submit is optional

## 19. Troubleshooting

### `autoapply` command not found

Use:

```bash
uv run autoapply --help
```

If needed, rerun:

```bash
uv sync
```

### Database connection failed

Check:

- PostgreSQL is running
- `.env` values are correct
- the database and user exist
- `pgvector` can be created

### Web app fails on startup

Run:

```bash
uv sync
uv run autoapply web
```

### Browser automation fails

Run:

```bash
uv run playwright install chromium
```

Then try non-headless mode:

```bash
uv run autoapply apply --url <ats-url> --no-headless
```

### No PDF output generated

Install one of:

- Microsoft Word with `docx2pdf`
- LibreOffice

### Template upload fails

- only `.docx` uploads are supported
- upload size must be 10 MiB or smaller
- invalid templates are rejected before being listed
- missing required styles or block markers are added automatically when possible

### LinkedIn login problems

- run LinkedIn search with `--no-headless`
- complete login manually on the first run
- reuse the saved session under `data/.linkedin_session`

## 20. Validation Checklist

Use this after deployment:

```bash
uv run ruff check .
uv run pytest -q
uv run autoapply --help
uv run autoapply status
uv run autoapply web --no-open
```

Expected current baseline:

- `uv run pytest -q` passes with 680 tests and 1 skipped LinkedIn smoke test
- `uv run ruff check .` passes
- `npm run build` passes when frontend dependencies are installed
- CLI loads
- dashboard starts
- database-backed tracking is available after initialization
