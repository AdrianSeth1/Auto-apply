# Implementation briefs (hand one at a time to a code agent)

Ground rules for every brief: read CLAUDE.md first and obey its invariants
(esp. #2 ATS location filtering, #4 cache deep-copies, #9 `127.0.0.1` never
`localhost`). Use `uv run` for everything. Tests must not hit live LLM,
Ollama, or network (AUTOAPPLY_DISABLE_LLM=1 is set suite-wide in conftest;
mock httpx). Never run DB-backed test suites without Postgres up — they hang
(CLAUDE.md). Add a docs/CHANGELOG.md entry per task, matching existing style.
Commit when green; do NOT push force; do NOT touch data/profile/ (PII,
gitignored).

---

## Brief 1 — Adzuna intake adapter (priority: HIGHEST — LinkedIn replacement)

Why: LinkedIn scraping is dead (account restriction — do NOT rebuild it).
Adzuna is a legitimate free API that aggregates broadly AND returns redirect
URLs that feed our existing board-discovery (src/intake/board_discovery.py),
restoring the self-growing companies.yaml registry.

1. Register for a free Adzuna API key (app_id + app_key) — ask the user to
   do this at developer.adzuna.com and put both in config/settings.yaml under
   a new `adzuna:` block (`enabled`, `app_id`, `app_key_env` defaulting to
   AUTOAPPLY_ADZUNA_KEY, `country: us`, `results_per_query: 50`).
2. Create src/intake/adzuna.py modeled on src/intake/ashby.py:
   GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
   params: app_id, app_key, what=<keyword>, where=<location>, results_per_page.
   VERIFY the real response shape first (title, company.display_name,
   location.display_name, description, redirect_url, created, salary_min).
   Map to RawJob: source="adzuna", ats_type="unknown",
   application_url=redirect_url, raw_data=item; map `created` into raw_data
   so the scorer's ghost-age check works (add "created" to the candidates
   tuple in src/matching/scorer.py::_posting_age_days — ISO format).
3. Wire into src/application/jobs.py::search_jobs as a new source leg: when
   source in ("all",) and adzuna enabled, query once per keyword (cap total
   API calls per search at ~10 — free tier is 250/day and five overnight
   plans run daily; log a warning when capped). Adzuna results MUST pass the
   normal ATS-style location/keyword filters (invariant #2 — treat like ATS,
   not like LinkedIn).
4. After the merge, pass adzuna jobs through register_discovered_boards (it
   already extracts greenhouse/lever/ashby slugs from redirect URLs).
5. Note Adzuna descriptions are TRUNCATED — the existing JD-recovery fetcher
   (materials.generate pre-step) will fetch full text at generation time via
   redirect_url; confirm that path triggers for adzuna jobs (description
   under generation.min_jd_chars).
6. Tests: mocked httpx response fixture; mapping; call-cap; board-discovery
   handoff. Run: uv run pytest tests/test_intake_adzuna.py tests/test_improvements_2026_07.py tests/test_web.py -q

## Brief 2 — Workday intake adapter (priority: HIGH — big-enterprise class)

Each Workday tenant has a public JSON careers endpoint, e.g.
POST https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
with body {"searchText": "", "limit": 20, "offset": 0}. VERIFY shape against
a real tenant before coding (try salesforce / adobe careers pages, inspect
network tab semantics via the documented cxs pattern; if a tenant 403s,
skip it — self-pruning like other boards).

1. src/intake/workday.py: fetch_company_jobs(tenant_config) where
   companies.yaml gains a `workday:` section of entries like
   `- {tenant: salesforce, host: wd12, site: External_Career_Site}` (list
   format differs from other ATS strings — document it in the yaml header).
   Paginate to a cap (~200 jobs/board). Map jobPostedOn/postedOn into
   raw_data for ghost-age; bulletFields/location mapping per real shape.
2. Wire into the board-fetch loop in src/intake/search.py next to
   greenhouse/lever/ashby; preserve deep-copy cache semantics (invariant #4).
3. Job detail: list responses carry a short description; full JD lives at
   .../wday/cxs/{tenant}/{site}/job/{externalPath}. Fetch details only for
   jobs that survive keyword filtering (like LinkedIn detail fetches did),
   cap ~30 detail fetches per board per run.
4. Add 3-5 seed tenants relevant to the user's targets (verify each live):
   healthcare IT and enterprise SaaS preferred.
5. Tests: mocked httpx; pagination; detail-fetch gating; a bad tenant logs
   one error and continues.

## Brief 3 — Hacker News "Who's Hiring" adapter (priority: MEDIUM, tiny)

Monthly thread, startup-dense, ideal for the ai-solutions plan (which has
the user's highest take rate: 75%).

1. src/intake/hn_hiring.py: Algolia API —
   GET https://hn.algolia.com/api/v1/search_by_date?tags=story&query=%22Ask%20HN%3A%20Who%20is%20hiring%22
   to find the latest thread id, then
   GET https://hn.algolia.com/api/v1/items/{id} for all top-level comments.
   Each comment is one posting: parse company (text before first | or -),
   title-ish line, REMOTE/location tokens, and any URL. Map to RawJob with
   source="hn", description=comment text (HTML-stripped), application_url=
   first URL or the comment permalink. created_at → raw_data for ghost-age.
2. Wire as a source leg in search_jobs (keyword + location filters apply).
   Cache the parsed thread in the board cache (key ("hn", thread_id, False)).
3. STARTUP QUALITY GATE (user requirement: startups yes, "tiny ones that
   don't pay" no): for source="hn" ONLY, invert the pay filter's
   unknown-passes convention — a posting with NO stated compensation is
   DROPPED when the search profile sets pay_operator/pay_amount. Implement
   as a per-source flag (e.g. RawJob.raw_data["strict_pay"] = True set by
   the adapter, honored in src/application/jobs.py::_apply_search_filters
   next to the existing pay check). Do not change unknown-passes for any
   other source (it exists for good reasons — see the docstring there).
4. Tests: fixture with 3 fake comments; parsing edge cases (no URL, REMOTE);
   strict-pay drop vs a comment stating "$120k-$160k" passing.

## Brief 3b — Seed funded-startup boards (priority: MEDIUM, no code — config)

User wants well-paying startups in the funnel. Curate ~30 funded startups
(YC top companies, recent Series B+ in dev tools / AI / SaaS — the user's
target space) and check each for a live board:
  greenhouse: GET boards-api.greenhouse.io/v1/boards/{slug}/departments
  lever:      GET api.lever.co/v0/postings/{slug}?limit=1
  ashby:      GET api.ashbyhq.com/posting-api/job-board/{slug}
Add VERIFIED slugs to config/companies.yaml under the right key with a
"# funded-startup seed 2026-07" comment (preserve the file's comments —
append lines textually, do not YAML round-trip). Skip anything without a
live board. These boards' postings flow through the normal pay filter
($90k floor in the user's search profiles), which handles the quality bar.

## Brief 4 — Remotive remote-jobs adapter (priority: LOW, ~1 hour)

GET https://remotive.com/api/remote-jobs?search=<keyword> (free, keyless).
Map to RawJob (source="remotive", url field → application_url,
publication_date → raw_data for ghost-age, candidate_required_location →
location). Wire as a source leg like Brief 3. Only fetch when a search
includes remote in location_types. Tests: mocked response, mapping.

## Brief 5 — "Copy pack" button on review cards (priority: HIGH for daily use)

The user applies manually; cut per-application time.

1. Backend: GET /api/review/{entry_id}/copy-pack returns JSON:
   identity fields (name, email, phone, location, linkedin from the active
   profile), artifact absolute paths (reuse _entry_artifacts in
   src/web/routes/review.py), the posting URL (reuse _entry_application_url),
   and the top 5 qa_bank entries matching the job title/description
   (reuse the token-overlap matcher pattern from
   src/application/question_answers.py::_similar_saved_answers).
2. Frontend (ReviewQueueView card): "Copy pack" button → fetch → render a
   small modal with each field/answer and a per-item copy icon + "copy all"
   (navigator.clipboard). Rebuild SPA: cd frontend; npx vite build.
3. Tests: route test with mocked session (test_web.py style).

## Brief 6 — Resume-variant A/B stamping (priority: MEDIUM, analytics groundwork)

Goal: outcome analytics can answer "which resume profile converts".

1. materials.generate already knows profile_id; persist it: add nullable
   `profile_id` (String 100) to Application via a NEW alembic migration
   (alembic revision -m "application profile stamp"; upgrade head with
   Postgres UP). Write it wherever resume_version is written in
   src/tasks/tasks.py, and in the manual mark-submitted materialization in
   src/web/routes/review.py (from the entry's plan kwargs if available,
   else the active profile id).
2. src/application/analytics.py: prefer Application.profile_id over the
   job raw_data best_profile fallback in by_profile bucketing.
3. Tests: analytics bucketing prefers the stamp; migration up/down.

## Brief 7 — Baylor-alumni referral step in prep packs (priority: MEDIUM)

In src/application/prep.py, after building the markdown, append a
"Warm intro candidates" section: run a web search (use the existing
provider-free path: this feature is only for interactive use — call
DuckDuckGo html endpoint via httpx with a clear User-Agent, query
'site:linkedin.com/in "{company}" "Baylor"', parse top 5 result titles/URLs;
if the fetch fails, write "search manually: <query>" instead — NEVER let
this block prep generation). Include the query itself in the output so the
user can rerun it by hand. Tests: mocked httpx, failure path writes the
manual-query fallback.

## Brief 8 — Mothball LinkedIn cleanly (priority: LOW, hygiene)

LinkedIn scraping is permanently off (account restriction).
1. config/settings.yaml: add `linkedin: enabled: false` (keep existing keys).
2. In src/application/jobs.py::search_jobs, when linkedin disabled, skip the
   linkedin leg silently for source="all" and return a clear one-line error
   only for explicit source="linkedin" requests.
3. Remove `linkedin_cookie_refresh` from the Beat schedule (update
   tests/test_tasks_beat.py EXPECTED_ENTRIES and any digest-wiring test).
4. Frontend: hide the LinkedIn session card in the Jobs view behind the
   config flag (expose it via an existing settings/config endpoint).
5. Do NOT delete src/intake/linkedin.py (history + possible official-API
   future); just gate it.
