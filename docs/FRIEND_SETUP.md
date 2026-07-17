# Run AutoApply on a second Windows PC

This is the shortest supported setup for a new user. It keeps each person's
profile, credentials, database, and generated documents local; none of those
should be committed to GitHub.

## 1. Install the prerequisites

Install Git, Python 3.12 or newer, `uv`, Docker Desktop, Ollama, and Microsoft
Word or LibreOffice. Node.js is only needed when changing the web interface.
Start Docker Desktop and Ollama before continuing.

## 2. Clone and install

```powershell
git clone https://github.com/AdrianSeth1/Auto-apply.git
cd Auto-apply
uv sync
uv run playwright install chromium
Copy-Item config/.env.example .env
```

Open `.env`, give `AUTOAPPLY_DB_PASSWORD` a non-empty local password, and keep
the database host as `127.0.0.1`.

## 3. Install the 16 GB VRAM model

Use Qwen3 14B for both the primary and small-model routes:

```powershell
ollama pull qwen3:14b
ollama pull nomic-embed-text
```

The standard `qwen3:14b` Ollama build is about 9.3 GB, leaving more headroom
than a 14 GB model on a 16 GB card. `config/settings.yaml.example` already sets
Ollama, `qwen3:14b`, and concurrency 1 as safe fresh-install defaults. On first
run AutoApply copies that file to the private `config/settings.yaml`.

If `config/settings.yaml` already exists, set:

```yaml
llm:
  provider: ollama
  primary_provider: ollama
  fallback_provider: null
  allow_fallback: false
  fallback_providers: []
  small_provider: ollama
  small_model: qwen3:14b
parallelism:
  llm:
    max_concurrent_global: 1
  bullet_rewrites:
    max_concurrent_per_task: 1
  provider:
    ollama:
      max_concurrent: 1
```

In Settings, connect Ollama and select `qwen3:14b` as the primary model. Keep
only one model loaded (`OLLAMA_NUM_PARALLEL=1`) if VRAM is tight.

## 4. Create the personal candidate profile

```powershell
Copy-Item data/profile/candidate.yaml.example data/profile/candidate.yaml
```

Edit `data/profile/candidate.yaml`. Replace every example value and keep the
stable ID prefixes: `edu_`, `exp_`, `expb_`, `proj_`, `projb_`, `story_`,
`qa_`, and `cap_`. Every capability needs at least one `evidence_refs` entry,
and each referenced ID must exist. Use only facts you can defend; resume and
letter generation treats this file as its evidence bank.

Then customize the five role files in `config/targets/`. Disable an unwanted
target with `enabled: false`, or change its titles, signals, constraints, and
discovery terms. Any capability named in a target must also exist in the
candidate profile.

Validate the profile and targets without touching the database:

```powershell
uv run pytest tests/test_target_profiles_v2.py -q
```

## 5. Initialize and run

```powershell
uv run autoapply init
uv run alembic upgrade head
uv run autoapply start --check
uv run autoapply start
```

Open `http://127.0.0.1:8000`. The launcher starts PostgreSQL and Redis through
Docker, applies migrations, and starts the worker, scheduler, and web app.
Use `Stop AutoApply.bat` or stop the launcher when finished.

Never enable automatic submission. Review every queued application and its
generated materials before approving it.

## Troubleshooting

- Docker errors: confirm Docker Desktop is running and `.env` has a password.
- Ollama not listed: run `ollama list`, restart Ollama, then reconnect it in
  Settings.
- Out of memory: close other GPU apps, keep concurrency at 1, and reduce
  Ollama context length; if necessary use `qwen3:8b`.
- No PDF: install Microsoft Word or LibreOffice and restart AutoApply.
- Backend or worker code changed: restart both web and worker processes.
- Web UI changed: run `npm install` and `npm run build` inside `frontend/`.

Do not run the full test suite against the live database. Some DB-backed tests
use the configured database. Run focused tests, or create a disposable test
database first.
