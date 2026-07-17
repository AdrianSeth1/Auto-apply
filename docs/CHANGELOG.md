# Changelog

All notable implementation changes to AutoApply are documented here, organized by phase. This is the detailed engineering log; keep product overview and quick-start content in the README, and keep current operating state in docs/HANDOFF.md.

## [Unreleased]

### Material quality hardening + operational cleanup (2026-07-16)

Audit of the first real 20-job delivery (2026-07-15) found: ~8 letters
addressed to ATS board slugs ("Localitymediallcdbafirstdue" instead of
"First Due"), 2 deterministic template letters shipped unlabeled, quality
warnings firing on 20/20 letters (no signal), one resume PDF recorded in the
ledger but absent on disk, one role consuming two portfolio slots via an
onsite+remote repost, headless LinkedIn probes on UI mounts despite the
automation ban, and the known dead 23:00 legacy Beat entry.

- **Company names**: Greenhouse adapter now reads the flat `company_name`
  payload field (sanitized of zero-width chars) before falling back to slug
  title-casing; `scripts/fix_company_display_names.py` repaired 6,030 legacy
  jobs, 342 postings, and 4 pending review cards (55 distinct renames).
  Audit confirmed no slug-named application had been submitted; 8 approved
  ones had materials regenerated before submission.
- **Pay extraction**: Greenhouse `metadata` currency/currency_range fields
  ("Budgeted Salary") now populate `raw_data.salary_min/salary_max` for the
  existing compensation fact extractor.
- **Labeled baseline letters** (invariant #10 amended by user decision):
  `_generate_high_quality_cover_letter_text` returns `(text, origin, issues)`;
  origin flows into document metadata, the version ledger, a
  `score_breakdown.materials_quality` digest on the review entry, and a
  "Template letter" / "N draft warnings" badge on the review card.
- **Threshold recalibration**: cover-letter window now min 180 / target 240 /
  max 340 (was 220/280/340 with a separate 260 validator minimum);
  `raw_evidence_dump` now fires only on near-verbatim bullet copies compared
  against `metadata.evidence_bullets`, not on the "At X, I…" style the prompt
  instructs; deterministic template no longer splices raw JD clauses
  ungrammatically and letter-header locations are title-cased.
- **PDF integrity**: `convert_to_pdf` verifies the output file exists and is
  non-empty before returning (docx2pdf/Word COM can fail silently).
- **Title-variant dedupe**: portfolio selection keeps one owner per
  (company, end-qualifier-stripped title); suppressed variants persist with
  reason `title_variant_duplicate`. Comma-suffixed specializations never merge.
- **LinkedIn**: session status is passive (disk-only) by default; a real
  headless probe requires explicit refresh.
- **Schedules**: removed the always-failing static 23:00 UTC `plan_run` Beat
  entry; added daily `maintenance.ledger_retention` (03:30 UTC) pruning
  dry-run portfolio decisions/discovery links older than
  `retention.dry_run_ledger_days` (14) — live-run rows kept forever.
- **Single-instance guard**: `autoapply start` probes `/api/instance` when
  the web port is busy and reuses a running AutoApply instead of starting a
  duplicate stack on a random port.
- **Incident during this session**: running the full pytest suite against
  the live database deleted all default-tenant `review_queue` rows (32
  delivered cards, 8 approvals). Recovered by rebuilding entries from
  `portfolio_decisions` + evaluations + the artifact version ledger
  (`tmp/rebuild_review_20260716.py`); approvals reset to pending because
  their letters were being regenerated anyway. Warning added to
  CLAUDE.md/AGENTS.md and HANDOFF.md: never run the full suite against the
  live DB until tests target a disposable database.
- **Docs consolidated**: new master `docs/HANDOFF.md` supersedes and replaces
  FABLE_CURRENT_SYSTEM_HANDOFF, SONNET_START_HERE,
  JOB_POOL_V2_IMPLEMENTATION_STATUS, JOB_SUPPLY_EXPANSION_PLAN,
  SOL_ULTRA_JOB_POOL_REDESIGN_BRIEF, AUDIT_2026-07-11, BRIEFS_FOR_CODE_AGENTS,
  PROJECT_MANAGEMENT, plan_en/plan_zh, and DEPLOYMENT_zh (all deleted;
  recoverable from git). CLAUDE.md/AGENTS.md re-synced and invariants 12–16
  added.

### Overnight review materials restored (2026-07-15)

- Moved the live V2 portfolio run to 08:00 UTC (3:00am Central during
  daylight-saving time) and capped it at 20 jobs. The live run creates pending
  review cards and immediately enqueues resume and cover-letter generation
  before any approval.
- Made material write-back accept both pending and approved review cards. This
  closes a race where approving a card while its overnight worker was finishing
  left generated files on disk but no quick links on the review card. A
  cover-letter-only partial success is now linked too.
- Updated the overnight launcher documentation and added a scheduler regression
  test. Submission still requires the explicit review transition; only material
  preparation is automatic.

### Location policy: nationwide US eligibility with preferred metros (2026-07-14)

- Expanded V2 onsite/hybrid eligibility from a four-city allowlist to any
  posting with explicit US geography. US-remote roles remain eligible only
  when the posting explicitly permits US candidates; bare or foreign remote
  labels keep their existing unknown/fail behavior.
- Added `onsite_hybrid_us_allowed` as an explicit candidate preference so the
  expanded hard eligibility policy is auditable and independently reversible.
- Kept location ranking separate from eligibility. San Francisco, Dallas,
  Portland, New York City, and Los Angeles receive the full geography
  preference score; other US cities remain eligible at a lower preference.
- A live non-consuming refill measurement increased unseen A/B inventory
  from 21 to 28 while still selecting 20 with zero delivery shortfall. It
  created no review cards or materials; eight additional A/B jobs remained
  in the reservoir after the 20-job simulated delivery.

### Supply yield: 20-job A/B reservoir and verified 100-board rotation (2026-07-14)

- Practice/shadow portfolio decisions no longer consume job history. A job is
  treated as previously surfaced only when it reached a real V2 run/review
  card; immutable dry-run decisions remain available for later practice and
  live delivery.
- Activated all 100 direct-employer endpoints that already passed the public
  ATS evidence contract. Ordinary runs refresh one deterministic 25-employer
  group and reconstruct the other groups from their latest immutable Job
  Index snapshots; `force_refresh` retains its explicit fetch-all behavior.
- Replaced the five-job canary with a 20-job core delivery target and a
  40-job unseen A/B reservoir target. Target diversity is now a first-pass
  soft cap, so an underrepresented target gets protected slots but excess
  A/B jobs can fill otherwise empty capacity. The company cap increased from
  one to two strong roles; Tier C is never used as filler.
- Added a separate six-hour dry-run refill plan. It rotates verified board
  refreshes, persists immutable postings/evaluations, and never consumes
  history or creates review/material records; the daily live plan remains
  the only process that delivers jobs to the review queue.
- Added explicit reservoir and supply telemetry: delivery shortfall,
  available/remaining A/B inventory, refill need, live endpoint group,
  live/reused endpoint counts, and reused job counts. This replaces the
  misleading interpretation that a cached ATS run with `ats: 0` had no ATS
  supply.
- Improved responsibility recall with the existing normalized JobFacts
  taxonomy (for example, discovery calls and needs analysis) while retaining
  the same A/B score thresholds, title lanes, experience caps, selectivity
  cap, full-description rule, and direct-employer gates.
- Tightened V2 remote geography: ATS roles must explicitly permit US remote
  work; a bare `Remote` is unknown, and a foreign remote label fails even if
  generic description prose mentions the United States.
- Tightened the broad operations-analyst route after the first full-pool
  practice surfaced a supply-chain procurement role as RevOps. Supply chain,
  procurement, logistics, legal operations, billing operations, and IT
  operations now require a different target instead of inheriting generic
  business-operations evidence.
- Live non-consuming validation progressed from 2 selected A/B jobs before
  the fix to 15 after groups 1–2, 17 after group 3, and 20 after group 4.
  The final precision-checked run retained 21 unseen A/B jobs, selected 20,
  reported zero delivery shortfall, created no review/material records, and
  showed 1,674 reused direct-ATS jobs even though the legacy `ats` live-fetch
  count was zero.
- Full test suite: 2,187 passed and 2 skipped. The sole failure is the known
  pre-existing `test_filters.py::test_loads_real_config` expectation for a
  `default` filter profile that is not present in the user's configuration.
  A final 73-test focused regression set passed after the RevOps precision
  exclusion.

### Maintenance: safe V2 practice-run dispatch and truthful task status (2026-07-14)

- Fixed `scripts/run_plans_now.py`, which previously built V2 portfolio kwargs
  and then hard-coded the legacy `orchestration.plan_run` task name. Manual
  runs now dispatch the task selected by the normalized plan through the same
  validated schedule path as the web app and Beat.
- Manual launcher and Plans-screen actions are now explicitly dry-run practice
  runs. Scheduled executions retain the plan's persisted `dry_run` setting.
- V2 pipeline/task normalization, publish-time contract checks, and strict
  worker payload validation prevent a V2 payload from silently defaulting to
  the legacy `default` profile and scanning the full stored job pool.
- Structured orchestration results with terminal error statuses now mark the
  durable task ledger row `failed` while preserving the diagnostic result.
  V2 portfolio runs are also included in plan-run history.
- Rebuilt the SPA, restarted web/worker/Beat, verified both orchestration tasks
  in the live worker, and passed 98 focused regression tests.

### Supply priorities: reviewed activation waves and selective Adzuna recovery (2026-07-14)

- Expanded direct-employer research to 162 net-new candidates after excluding
  every endpoint already present in `companies.yaml`. A production-adapter
  probe verified 101 current boards against non-empty samples, employer
  identity, unique IDs, direct ATS apply URLs, locations, and full
  descriptions. Selected the strongest 100 and retained every failure in the
  immutable audit instead of enabling guessed or empty endpoints.
- Added `config/employer_supply_activation.v1.yaml` as the reviewed four-wave
  activation registry (25 employers per wave). Wave 1 is now live in
  `config/companies.yaml`; waves 2–4 are verified and approved but remain out
  of the live registry until the preceding wave completes two canary runs.
  Promotion is judged by net-new complete direct-apply A/B jobs, never raw
  posting count. A configuration test prevents inactive waves from leaking
  into the live board list.
- Reviewed the real 35-employer probe artifact. Sixteen employers passed all
  six evidence checks with eight-job samples; none was silently promoted to
  approved/enabled. Added a review-only two-wave proposal in
  `data/audits/employer_cohort_activation_waves.review.yaml`, prioritizing AI
  implementation/solutions, SaaS implementation/TCS, and RevOps overlap.
- Adzuna recovery now selects only promising snippets backed by the latest
  immutable evaluation: target routing and location must pass, no known
  eligibility gate may fail, and the existing Tier-B review/role floors must
  be met. This prevents broad provider/ATS follow-ups for records that cannot
  become A/B jobs. The live ledger currently has 197 pending snippets and
  zero that clear this conservative recovery bar.
- Successful recovery now retains attempt audit metadata, uses the legacy
  job's tenant, finds Job Index postings case-insensitively, and enriches an
  existing posting even when it has no prior snapshot. Job Index upserts now
  normalize and compare source names case-insensitively, preventing a
  mixed-case recovery from creating a duplicate posting.
- Fixed endpoint exploration-state handling so a successful first canary can
  promote `candidate` to `active`; the three-run budget suppresses only real
  demotions and never fabricates activation for an endpoint that has not
  succeeded.
- Verification: focused lint passed; 101 cohort, resolver, live-Postgres
  snapshot, endpoint-health, and source-funnel tests passed. A live resolver
  query with `--limit 0` completed without writes.

### Maintenance: accurate source-funnel attrition label (2026-07-14)

- The Search Quality source table no longer calls the difference between raw
  provider records and V2-evaluated postings “Dupes.” That number also
  includes intentional keyword/location narrowing, normalization rejects,
  and conservative identity reconciliation, so it was a misleading measure
  of duplication. It is now labelled “Before V2” with an in-product
  explanation. The actual duplicate cluster is intentionally not inferred
  from this aggregate count.

### Phase S7: user input that materially improves yield (2026-07-13)

- Collected real structured input from Arya (via `AskUserQuestion` for
  discrete choices, chat for open lists) instead of one-off prompt text, per
  the plan's explicit S7 deliverable. Researched existing schema first --
  location prefs and a soft salary floor already existed; a hard floor field
  existed but was unset; nothing existed yet for company preferences,
  apply/no-apply examples, or quota/travel/coding willingness.
- `src/matching/target_schema.py::CandidatePreferencesV2`: added
  `preferred_companies: list[str]`, `excluded_role_signals: list[str]`
  (candidate-level, applies across every target), `quota_bearing_ok: bool`,
  `light_coding_implementation_ok: bool` -- all with safe defaults for
  backward compatibility with the `extra="forbid"` strict model.
- `data/profile/candidate.yaml` populated: `hard_base_min: 70000`,
  `travel_ceiling_percent: 25`, 50 `preferred_companies`, `excluded_role_signals:
  [door to door, door-to-door, canvassing, residential sales route]` (scoped
  to Arya's actual stated dealbreaker -- door-to-door specifically, not a
  blanket outside-sales exclusion, since she said B2B outside sales is
  tolerable), `quota_bearing_ok`/`light_coding_implementation_ok: true`.
- `src/matching/scorer_v2.py`: `preferred_companies` bumps the existing
  `employer_interest` component in `_preference` (100 vs. 60 baseline, same
  0.15 weight -- no new weighted term, no rebalancing of the score's other
  five components, consistent with CLAUDE.md's "preserve existing
  calibration" invariant). `excluded_role_signals` merged with each target's
  own `negative_responsibility_signals` in `_responsibility_coverage`.
- `src/intake/query_scheduler.py`: added `QueryArmV2.priority` (not part of
  the arm identity/version hash -- a priority change must never invalidate
  stored yield history). `select_query_arms`'s round-robin phase now uses a
  smooth weighted round robin (`_weighted_round_robin_order`) for slots
  beyond each target's guaranteed canary, so higher-priority targets claim
  more of the extra budget without ever starving anyone; the yield phase
  gets a gentle multiplicative priority blend (`posterior_yield * (0.7 + 0.3
  * priority)`) that's a no-op at priority 1.0 and discounts up to 30% at
  priority 0. `config/targets/*.yaml` priorities updated to reflect Arya's
  chosen top 3 (AI Implementation, SaaS Implementation, Technical Customer
  Success): technical-customer-success 0.7 -> 0.85, revenue-operations-analyst
  0.9 -> 0.6, associate-solutions-engineering 0.7 -> 0.6.
  `src/orchestration/portfolio_run.py`'s two `QueryArmV2` construction sites
  updated to thread priority through.
- Tests: `tests/test_scorer_v2_preferences.py` (7 cases, 1 skipped by
  design) and 7 new cases in `tests/test_query_scheduler_v2.py` (3
  pre-existing tests unmodified, still passing) -- all run for real under
  pytest against production code (real `candidate.yaml`, real target YAML
  files, no mocks beyond the established fake-session pattern where needed).
- Not verified: full `run_portfolio_v2` integration and live-Postgres
  `SourceQueryArm` round-trips (Python 3.12/DB requirements this sandbox
  can't meet). Hit a persistent phantom syntax error from this sandbox's
  bash tool specifically on `src/orchestration/portfolio_run.py` that does
  not reproduce via the Read tool even after a full-file rewrite -- flagged
  for a manual `py_compile` check on the real machine.
- Deferred: apply/no-apply examples (Arya had none ready yet);
  `travel_ceiling_percent` is stored but still not read by any scoring code
  (pre-existing gap, not something to rush a fix for here);
  `quota_bearing_ok`/`light_coding_implementation_ok` have nothing to
  consume them yet since no current target models either dimension.

### SUP-09: adaptive source-endpoint scheduling, Phase S6 (2026-07-13)

- Closed a gap flagged (and left unactioned) in both the SUP-01B and SUP-02
  writeups: `src/jobs/source_endpoints.py::_update_endpoint_health` now
  actually drives `SourceEndpoint.state` through the real, pre-existing
  state machine in `src/intake/source_health.py::transition_health`
  (candidate/active/degraded/quarantined/dormant/blocked/retired) instead
  of only accumulating counters nothing consumed. Added
  `_classify_fetch_status`, which maps `_fetch_board`'s four coarse
  statuses down to the finer vocabulary `transition_health` needs by
  parsing the HTTP status code every scraper's own `ScraperError` message
  already contains (`f"HTTP {status} from {url}"`, confirmed against
  `src/intake/base.py`, not guessed).
- Added a 3-run exploration budget (Phase S6): a new endpoint's first three
  runs update every counter truthfully but can't trigger a state demotion,
  computed from a real `SourceEndpointRun` count rather than a new column
  -- no migration needed. `blocked` transitions (403/compliance) are exempt
  from the budget on purpose.
- **Caught and fixed before shipping**: the exploration budget silently
  broke an existing test's assumption
  (`test_endpoint_health_tracks_failures_and_recovery_across_runs` expected
  `recovery_successes == 1` after fail/fail/success on a brand-new
  endpoint). Traced by hand: under the new budget-aware code the 2nd
  failure never actually reaches `"degraded"` (suppressed), so there's
  nothing to recover from and `recovery_successes` correctly stays 0 --
  intentional, more correct behavior, not a bug, but it changes what a
  brand-new endpoint's test needs to set up. Fixed by adding three warm-up
  successful runs before the sequence under test so it runs past the
  budget window, and added a new dedicated test for the budget-suppressed
  case itself.
- Added `src/application/source_funnel.py::compute_yield_demotion_candidates`,
  a read-only diagnostic for Phase S6's "demote after seven non-empty runs
  with zero routed candidates" -- deliberately **not** wired to an automatic
  state change, since "routes nothing" depends on downstream target-
  matching/scoring logic that could itself be buggy; auto-demoting on that
  signal risks silently starving a target while masking the real defect.
  Reuses the same `target_routing` reason signal `source_funnel_report`'s
  existing `low_yield` flag already computes, aggregated across a rolling
  window of runs via `JobSnapshot.source_endpoint_run_id` instead of within
  one run.
- Scoped out, not attempted: freshness override for previously-excluded
  snapshots (touches `src/orchestration/portfolio.py`'s durable exclusion
  logic, explicitly flagged in CLAUDE.md as not to reopen casually) -- a
  real, separate, larger change documented as a follow-up rather than
  rushed. (Refresh-cadence throttling, scoped out alongside it initially,
  was picked back up and closed the same day -- see below.)
- Tests: `tests/test_source_endpoints_health.py` (18 cases, pure Python +
  a minimal fake session for the one `COUNT(*)` call `_update_endpoint_health`
  makes -- run for real under pytest via the `datetime.UTC` shim, same
  technique as SUP-07). `tests/test_source_endpoints_persistence.py` gained
  a new exploration-budget test and warm-up runs in the existing
  recovery-tracking test (DB-backed, not run this session -- no reachable
  Postgres, consistent with every DB-backed ticket this session).

### Phase S6 refresh cadence: direct ATS boards, closing the gap left open above (2026-07-13)

- Per Arya's explicit direction, picked back up the refresh-cadence item
  scoped out of SUP-09 above and built the "full correct version": direct
  ATS boards fetched successfully within the last 6h are skipped on the
  live HTTP call on the next `Run Plans Now`/scheduled run, rather than
  refetched every time. Flagged and got explicit sign-off on the actual
  design risk first: a naive version of this would silently shrink the
  candidate pool, since `run_portfolio_v2` only links postings present in
  *that run's* live fetch result -- skipping a board's fetch would drop its
  postings from the run entirely, not just save an HTTP call. Arya chose
  the harder-but-correct option (reconstruct from the Job Index) over two
  smaller/safer alternatives offered.
- New module `src/jobs/source_freshness.py`:
  `split_companies_by_freshness` partitions `config/companies.yaml` into
  "needs a live fetch" vs "fetched within 6h, reuse" using
  `SourceEndpoint.last_success_at`, reproducing
  `src.intake.search._endpoint_identity`'s exact key derivation (confirmed
  against the real function, not re-derived from memory).
  `reconstruct_fresh_endpoint_jobs` rebuilds `RawJob`s for skipped endpoints
  from the Job Index's most recent postings, matching only snapshots
  carrying the exact `source_endpoint_adapter`/`source_endpoint_key` tags
  `_tag_endpoint` (`src/intake/search.py`) already writes -- confirmed real
  and already persisted, not invented for this. Reused postings are tagged
  `raw_data["reused_from_job_index"] = True`, auditable and distinguishable
  from a live fetch.
- `src/application/jobs.py::search_jobs` gained a `companies` passthrough
  (default `None`, used instead of `_build_companies_filter(...)` only when
  explicitly provided -- every other caller unaffected).
  `src/orchestration/portfolio_run.py::run_portfolio_v2` computes the
  freshness split (skipped entirely when `force_refresh=True`, preserving
  its existing "bypass everything" meaning), passes the stale subset into
  the acquisition request, and merges reconstructed jobs for the fresh
  subset back into the candidate pool after the live fetch returns, deduped
  by `(source, source_id)`, before any enrichment/evaluation/selection runs.
  Deliberately does not fabricate `SourceEndpointRun` telemetry for skipped
  endpoints -- their last real fetch's row stands as history.
- **Caught and fixed before shipping**: a stored snapshot whose field no
  longer validates against the current `RawJob` schema (e.g. a narrowed
  enum literal) would have propagated an uncaught `pydantic.ValidationError`
  out of `reconstruct_fresh_endpoint_jobs` and failed the whole discovery
  run. Wrapped the `RawJob(...)` construction in its own
  `try/except (TypeError, ValueError)` that skips just that one posting;
  caught by a test built specifically to probe this case, then fixed.
- **Sandbox-tooling note for future sessions**: hit a stronger variant of
  the bash-mount staleness issue noted under SUP-07/SUP-09 while verifying
  this -- `pytest` ran bytecode for `src/jobs/source_freshness.py` that was
  missing an edit the Read tool and direct `cat`/`grep` both showed as
  present, confirmed via `dis.dis`, and it survived one `__pycache__` clear.
  Resolved by writing the file's known-correct content through a bash
  heredoc directly (bypassing the Windows-mount round-trip) plus another
  `__pycache__` clear. See the implementation status doc for the full
  account.
- Tests: `tests/test_source_freshness.py` (15 cases, pure Python, the same
  `datetime.UTC` shim + duck-typed fake-session pattern as
  `test_source_endpoints_health.py`, run for real under pytest against
  production code). Not verified: the real SQL queries against live
  Postgres, and the full `run_portfolio_v2` integration path (needs Python
  3.12 for `enum.StrEnum`, unavailable in this sandbox).

### SUP-08: YC Work at a Startup terms review -- blocked, Phase S5 (2026-07-13)

- Phase S5 priority 1 requires a terms review before touching YC's Work at
  a Startup board. Fetched Y Combinator's actual Terms of Use
  (`ycombinator.com/legal`) directly rather than relying on third-party
  summaries or scraper-vendor marketing claims. Found an explicit
  prohibition: "you will not engage in or use any data mining, robots,
  scraping or similar data gathering or extraction methods," under a
  section that defines "the Site" as covering "all subdomains" and whose
  Privacy Policy names Work at a Startup as one of the Site's core
  Services. `workatastartup.com/robots.txt` is fully permissive, but that
  governs crawler indexing, not the separate contractual scraping ban, and
  doesn't override it. No official YC jobs API was found; third-party
  scraper products (Apify, Browse.ai) market "public API" access, but that
  describes their own reverse-engineered use of internal endpoints, not
  anything Y Combinator has authorized.
- Decision: same treatment as Indeed/Wellfound -- blocked, no adapter, no
  scraping. Updated `docs/JOB_SUPPLY_EXPANSION_PLAN.md`'s "Decision"
  section to cover YC alongside Indeed/Wellfound, with the exact clause and
  evidence. No code was written; this is a research/compliance ticket by
  design. Manual paste/import remains available for individual YC
  postings, same as Wellfound today.

### SUP-07: Adzuna full-JD resolver, Phase S4 (2026-07-13)

- Added `src/intake/full_jd_resolver.py::resolve_full_jd`: recovers a full
  description for a snippet-only Adzuna posting by following *only* the
  provider-supplied `application_url`, matching the resolved URL against
  the same direct-ATS host shapes each scraper's own URL construction
  already uses, and reusing that adapter's real scraper class --
  `GreenhouseScraper.fetch_job` / `LeverScraper.fetch_job` (both pre-
  existing) or `AshbyScraper.fetch_jobs` + find-by-id -- but only when the
  adapter is `enabled: true` in `config/source_policy.yaml` (previously
  read by nothing in `src/` at all; this is the first runtime enforcement
  of it). Workday is deliberately excluded (its own docstring documents
  its careers pages as client-rendered SPAs a static GET can't recover);
  SmartRecruiters/Workable/Recruitee are recognized by URL shape but
  rejected at the policy-gate step since they're still `enabled: false`
  pending their own Phase S3 conformance tickets (SUP-04/05/06). Never
  raises -- every non-recovery path returns a `reason` string instead.
- Added `src/application/resolve_snippets.py::resolve_pending_snippets`:
  batch driver over the legacy `jobs` table (updates description/raw_data
  in place there) that also writes a new immutable JobSnapshot via the
  existing `src.jobs.enrich.enrich_posting` facade -- the same call
  `src.orchestration.portfolio_run.run_portfolio_v2` already makes for
  every source -- when a Job Index posting already exists for that job.
  Rescoring is intentionally not triggered here: `JobTargetEvaluation` is
  keyed by `snapshot_id`, so a new snapshot is automatically unscored and
  picked up fresh by the next normal portfolio run. Added
  `scripts/resolve_adzuna_snippets.py`, a manual-run CLI matching SUP-02's
  shipped pattern.
- **Caught and fixed before shipping**: the first version checked for an
  existing Job Index posting via `store.upsert_posting(...)`, which
  *creates* a bare posting row if none exists -- silently indexing a job
  into the Job Index a run early via a side effect, even though the
  surrounding code correctly skipped calling `enrich_posting` afterward.
  Fixed by replacing it with a plain read-only `SELECT` on `JobPosting`.
- Tests: `tests/test_full_jd_resolver.py` (16 cases: guard clauses,
  redirect-follow failure, unrecognized/Workday-shaped URLs, the
  "recognized-but-not-refetchable" vs "recognized-and-policy-disabled"
  distinction, successful recovery through each of the three reusable
  adapters with the full/partial length threshold, Ashby job-not-found,
  scraper errors, empty recovered description).
  `tests/test_resolve_snippets.py` (6 cases, DB-independent helpers only).
- **Verified more thoroughly than SUP-01B/SUP-02 could be**: this sandbox's
  Python 3.10 can't normally import this project's code (`datetime.UTC`
  needs 3.11+), but patching `datetime.UTC = datetime.timezone.utc` before
  import -- an exact equivalence, not a behavior change -- let the *real*
  modules run under `pytest` here. All 22 tests pass for real, not via a
  hand-copied replica, after `pip install --break-system-packages pytest
  sqlalchemy pgvector`. Worth reusing this trick in future sessions hitting
  the same version wall.
- **Not verified**: the database-integration path in
  `resolve_pending_snippets` (needs live Postgres, unavailable here). The
  `upsert_posting` -> `SELECT` fix above was confirmed via the Read tool
  (authoritative) plus an isolated harness, not `uv run pytest` -- bash's
  mounted view of this file went stale/frozen mid-session, matching the
  same symptom documented in "SUP-02 follow-up" entries below. Run
  `uv run pytest tests/test_full_jd_resolver.py tests/test_resolve_snippets.py -q`
  then `uv run python scripts/resolve_adzuna_snippets.py` against live data
  before treating this as verified end-to-end.

### SUP-02 plateau accepted at 16/35; Phase S4 (aggregator full-JD resolver) started (2026-07-13)

- Run 3 (after the network-request-capture fix) held at 16/35 verified, same
  15 employers stuck at `adapter_not_detected` with `render_used: true,
  render_error: null` -- the browser successfully rendered every one of
  them and still matched nothing. Ground-truthed two by hand instead of
  guessing more regex patterns: **Retool**'s "Apply" button resolves to
  `jobs.gem.com/retool/{id}` (Gem, an unsupported ATS -- confirmed live via
  Claude in Chrome network inspection); **Rippling** runs its own
  first-party `ats.rippling.com/{slug}/jobs` product (confirmed via web
  search). Both are real careers systems outside the seven adapters this
  pipeline supports, not detection bugs. The remaining 13 stuck employers
  were not individually confirmed this session (the browser connection
  dropped mid-investigation) and are documented as "probable, unconfirmed"
  rather than verified.
- Decision: accept 16/35 as the plateau rather than keep patching detection
  heuristics against employers with nothing in-adapter to detect. This means
  Phase S2's "at least 25 verified" acceptance bar may not be met -- treated
  as a legitimate evidence-based outcome, not a bug. Adding a Gem adapter (or
  others) to close the gap for real is separate, unscoped feature work.
  Full writeup in `docs/JOB_POOL_V2_IMPLEMENTATION_STATUS.md` under "SUP-02"
  / "Plateau confirmed at 16/35".

### SUP-02 follow-up 2: network-request-based detection (2026-07-13)

- Second real run (5 -> 16/35 verified with the domcontentloaded fix)
  showed 12 employers where the JS-render fallback genuinely rendered the
  page and still found nothing (`render_used: true, render_error: null,
  status: adapter_not_detected`). Root cause: some careers pages fetch the
  ATS's JSON API directly via client-side JS and never render a plain
  `<a href>` link at all -- the slug never appears in `page.content()`,
  only in the network request URL for the API call itself.
- Fixed: `BrowserRenderer.render` now also captures every network request
  URL made while the page loads (`page.on("request", ...)`), returned as
  `RenderResult.network_urls`. `probe_employer` scans the rendered DOM
  *and* these captured URLs together. Added `_API_PATTERNS` -- each
  adapter's real JSON API URL shape, confirmed against each scraper's own
  fetch URL in `src/intake/*.py` (not guessed): `boards-api.greenhouse.io/
  v1/boards/{slug}`, `api.lever.co/v0/postings/{slug}`, `api.ashbyhq.com/
  posting-api/job-board/{slug}`, `api.smartrecruiters.com/v1/companies/
  {slug}`, `apply.workable.com/api/v3/accounts/{slug}`. Also added
  `_WORKDAY_CXS_RE` for Workday's real `.../wday/cxs/{tenant}/{site}/jobs`
  API shape, since the generic page-link pattern's optional site segment
  would otherwise misread the literal `"wday"` path component as a site
  name -- `_NOT_SLUGS` now excludes `"wday"`, `"cxs"`, `"api"`,
  `"accounts"`, and version segments (`"v0"`-`"v3"`) for the same reason
  across all adapters.
- **Caught and fixed before shipping**: the first version of this change
  scanned patterns via `{**_ADAPTER_PATTERNS, **_API_PATTERNS}`. Since both
  dicts share adapter-name keys (both have `"greenhouse"` etc.), that dict
  merge silently *overwrites* rather than combines -- it would have dropped
  the original public-link pattern for 5 of 6 adapters, breaking every
  already-working static detection (Writer, Hebbia, Ironclad, Benchling,
  Modern Health, ServiceTitan, Uniphore). Confirmed with a throwaway
  `{**a, **b}` REPL check before rewriting it as two separate scan loops.
  Regression test added: `test_static_public_link_detection_is_not_shadowed_by_api_patterns`.
- `run_probe`'s console summary now includes `render_error` per failed
  employer, not just `render_used` -- the previous run needed a full read
  of `data/audits/employer_cohort_probe.json` to find that five failures
  were `networkidle` timeouts from a stale-but-still-running earlier
  process, because the summary dropped that field.
- Tests added: one API-pattern-detection test per adapter, the Workable
  API-vs-public-link disambiguation, the Workday CXS-vs-generic-pattern
  disambiguation, and an end-to-end `probe_employer` test where the
  rendered DOM has nothing but a captured network request resolves the
  endpoint.

### SUP-02 hotfix: silent-run + networkidle-hang risk in the JS-render fallback (2026-07-13)

- Arya's first run with the JS-render fallback sat with zero output for
  several minutes -- looked identical to a hang, and the wait strategy made
  it likely to actually behave like one. Two fixes to
  `scripts/probe_employer_cohort.py`:
  - `BrowserRenderer.render` no longer waits for Playwright's
    `"networkidle"` -- modern marketing sites routinely keep a connection
    open forever (chat widgets, analytics beacons, websockets), so
    networkidle reliably burns the full per-employer timeout on exactly
    the pages this fallback targets. Switched to `"domcontentloaded"` plus
    a fixed 2.5s settle pause (`_JS_SETTLE_MS`), which is far faster and
    still gives client-side JS time to inject the ATS widget.
  - `run_probe` now prints `[i/n] probing <name>...` and the outcome to
    stderr as each employer is processed, flushed immediately, instead of
    staying silent until the final JSON summary. A multi-minute run with
    no progress output is indistinguishable from a hang; this fixes that.

### SUP-02 follow-up: Workday detail-fetch + JS-render fallback (2026-07-13)

- First real run of `scripts/probe_employer_cohort.py` against the live 35
  employers: 5/35 verified. Root-caused the 30 failures instead of taking
  the count at face value -- 25 were `adapter_not_detected` (static HTML
  had no ATS links at all, consistent with JS-rendered careers pages), 2
  were correctly-detected Workday endpoints failing only on missing
  descriptions, 1 was a rebrand-driven identity-check near-miss (Tray.io /
  Tray.ai), 1 was a stale/wrong Greenhouse slug found on the page (Aisera).
- Fixed: `fetch_sample` now calls `scraper.fetch_job_detail(job)` for
  Workday samples before verification -- Workday's list endpoint carries
  no description field at all, so without this every Workday candidate
  failed `has_full_description` regardless of endpoint quality.
- Added, per Arya's explicit choice given the 25/35 static-detection miss
  rate: `BrowserRenderer`, a lazy, run-scoped headless Chromium fallback
  (`playwright.sync_api`) used only when static HTML detection finds
  nothing. One instance is reused for the whole cohort run; it never
  launches for employers whose static HTML already resolves. Recovered
  candidates are tagged `detection_method: "js_render"`; a missing/broken
  Playwright install degrades to a recorded `render_error` on the
  employers that needed it, never a crash. New CLI flags:
  `--no-js-render`, `--render-timeout-ms`.
- Tests added to `tests/test_probe_employer_cohort.py`: Workday
  detail-fetch invocation and non-Workday exemption, `BrowserRenderer`
  laziness and graceful degradation, the static-hit-skips-render /
  static-miss-triggers-render / render-finds-nothing-vs-render-unavailable
  distinctions in `probe_employer`, and an end-to-end `run_probe` test
  confirming the renderer is constructed, wired through, and closed.
  Existing `run_probe` tests were updated to pass `use_js_render=False`
  explicitly so they stay pure-Python/no-network.
- Full breakdown of the first run's results and what's still a manual-review
  case (Tray.io, Aisera) vs. what's fixed is in
  `docs/JOB_POOL_V2_IMPLEMENTATION_STATUS.md` under "SUP-02" / "First real
  run and two follow-up fixes".

### SUP-02: read-only cohort endpoint detector and audit artifact (2026-07-12)

- Added `scripts/probe_employer_cohort.py`, per
  `docs/JOB_SUPPLY_EXPANSION_PLAN.md` Phase S2 / ticket SUP-02. For each of
  the 35 employers in `config/employer_cohort.v1.yaml`: fetches the
  official `careers_url` (redirects followed), detects a Greenhouse/Lever/
  Ashby/Workday/SmartRecruiters/Workable/Recruitee endpoint from the
  resolved URL + page HTML, samples through the real production scraper
  class for that adapter (no duplicate fetch logic), and runs five
  verification checks (identity, unique non-empty IDs, direct application
  URLs, location, full description). Writes an audit (every employer, pass
  and fail) plus two proposed YAML patches under `data/audits/` --
  `config/companies.yaml` and `config/employer_cohort.v1.yaml` are never
  written to directly, and nothing produced by this script can set
  `verification_status: verified_approved` or `enabled: true` on its own
  (`scripts/validate_employer_cohort.py`'s release gate still requires an
  explicit separate human step for that).
- Tests: `tests/test_probe_employer_cohort.py`, pure Python, HTTP faked at
  the `httpx.Client` boundary and scraper fetches faked via
  `_SCRAPER_CLASSES` substitution -- covers adapter detection (including
  the Workday "site segment missing" incomplete-match case), all five
  verification checks with an explicit fail-closed-on-empty-sample case,
  scraper failure isolation, and `run_probe`'s end-to-end artifact writing
  including the never-`enabled: true` invariant.
- Full contract, what was and wasn't verifiable in the implementation
  sandbox, and the exact next commands to run are in
  `docs/JOB_POOL_V2_IMPLEMENTATION_STATUS.md` under "SUP-02".
- Also flagged, not yet fixed: `src/jobs/source_endpoints.py::_update_endpoint_health`
  (from SUP-01B) tracks failure/recovery counters correctly but doesn't
  drive `SourceEndpoint.state` transitions the way the canonical
  `src/intake/source_health.py::transition_health` state machine would --
  noted as a follow-up in the implementation status doc rather than folded
  into this ticket's scope.

### SUP-01B: real acquisition instrumentation for the supply funnel (2026-07-12)

- Replaced SUP-01's disclosed estimate-only fallback with real per-endpoint
  fetch telemetry, per Arya's explicit instruction not to fabricate endpoint
  or query-arm metrics:
  - `src/intake/search.py::_fetch_board` now times every attempted
    direct-ATS board and classifies the outcome (`success`/`empty`/
    `error`/`cache_hit`), reported via a new `endpoint_metrics`
    out-parameter on `search_jobs` (threaded through
    `src.application.jobs.search_jobs` into
    `src.orchestration.portfolio_run.run_portfolio_v2` -- the smallest
    interface that avoids duplicating the fetch pipeline, per the
    instruction to identify one before coding). One broken employer board
    still cannot fail the whole discovery run; the existing per-future
    try/except isolation in the ThreadPoolExecutor loop is unchanged, only
    now also records the failed attempt before re-raising.
  - All seven ATS scrapers (`greenhouse`, `lever`, `ashby`, `workday`,
    `smartrecruiters`, `workable`, `recruitee`) set
    `self.last_fetch_stats = {provider_records, normalized_records,
    malformed_records}` before returning, so provider-vs-parsed counts are
    observed, not assumed equal.
  - New `src/jobs/source_endpoints.py` (`upsert_source_endpoint`,
    `record_endpoint_runs`) turns that telemetry into real `SourceEndpoint`
    / `SourceEndpointRun` rows inside `run_portfolio_v2`'s existing
    transaction. It only ever writes passive health fields
    (`last_checked_at`, `last_success_at`, `last_nonempty_at`,
    `consecutive_failures`, `consecutive_empty`, `recovery_successes`,
    `first_failure_at`); it never touches `state`/`compliance_status`, so a
    passing fetch can never masquerade as a SUP-02-verified, activated
    source.
  - Postings are tagged with their exact origin at fetch time
    (`raw_data.source_endpoint_adapter`/`source_endpoint_key` for direct
    ATS, unchanged `source_query_term`/`source_query_location` for Adzuna)
    and `JobSnapshot.source_endpoint_run_id`/`source_query_run_id` (schema
    columns that previously had no writer) are now actually set -- only on
    a brand-new snapshot, never a reused one, respecting snapshot
    immutability. A posting with neither tag is bucketed by
    `source_funnel_report` as `endpoint_kind: "attribution_unknown"` rather
    than guessed from company name (explicit requirement: attribute
    conservatively, mark unknown rather than guess).
  - `SourceQueryRun.routed_pairs`/`viable_evaluations` are now computed
    after evaluation and written back for real. `viable_evaluations` uses
    the architecture's literal Tier B label ("B -- viable",
    `JOB_POOL_V2_ARCHITECTURE.md` Section 5.4) per the instruction to defer
    to an explicit architecture definition when one exists -- it is
    deliberately narrower than `source_funnel_report`'s own combined Tier
    A/B "A/B evaluations" stage, which is reported separately and remains
    unchanged. `review_positives` reflects real surfaced-card attribution.
  - `source_funnel_report`'s `instrumented: false` fallback (fetched count
    estimated from evaluated postings, no duration/last-success) is
    preserved exactly as SUP-01 shipped it, for any source/endpoint lacking
    a real `SourceEndpointRun`/`SourceQueryRun` row in that specific run --
    Remotive, HN, and LinkedIn (LinkedIn stays excluded from `source="all"`
    entirely) remain permanently in this category by design, not as a bug.
- Tests added: `tests/test_intake_endpoint_metrics.py` (pure Python, no DB
  -- success/empty/failure-isolation/cache-hit at the fetch boundary, via
  scraper classes patched at their `src.intake.search` import names, with
  `persist_and_sync_ids` mocked out so this file can never hang waiting on
  Postgres). `tests/test_source_endpoints_persistence.py` (DB-backed --
  one row per attempted endpoint with real counts, upsert never touches
  activation state, consecutive-failure/recovery tracking across runs,
  empty-vs-nonempty distinction). `tests/test_source_funnel_report.py`
  gained an `attribution_unknown` case and a multi-target
  no-double-counting reconciliation case (one posting evaluated against
  three targets must still count once at fetched/unique), and its existing
  employer-board case now uses the real fetch-time tag instead of the old
  company-name-derived grouping.
- **Not run this session**: no reachable Postgres and no Python 3.12
  interpreter were available in the implementation sandbox, so none of the
  above tests have actually been executed, and the SPA was not rebuilt.
  Full detail and the exact command to run first is in
  `docs/JOB_POOL_V2_IMPLEMENTATION_STATUS.md` under "SUP-01 / SUP-01B" and
  in `docs/SONNET_START_HERE.md`'s "Next task" section. Treat this as
  reviewed-but-unverified, not shipped, until that run is green.

### SUP-01: per-source/endpoint supply funnel report (2026-07-12)

- Added `src/application/source_funnel.py::source_funnel_report`, extending
  the Search Quality screen with one row per source (and, where the data
  distinguishes it, per endpoint/query arm) showing
  `fetched -> unique -> in-policy geography -> target-routed -> full-JD -> A/B
  -> surfaced`, plus duplicates, description-completeness breakdown, and
  rolling 7-/30-day unique A/B yield. New read-only route:
  `GET /api/job-pool-v2/source-funnel` (optional `run_id`). Wired into
  `frontend/src/views/JobPoolQualityView.vue` as a new table below the
  existing tier-supply card.
- Every row is built from the immutable ledger (`job_target_evaluations`,
  `job_evaluation_reasons`, `discovery_run_evaluations`, `portfolio_decisions`)
  so counts reconcile to a specific discovery run and every stage carries its
  source snapshot ids. Rows with several fetched jobs but zero routed or zero
  full-JD jobs are labeled `low_yield` per the SUP-01 acceptance criteria.
  Adzuna postings are grouped by query arm (`source_query_term` /
  `source_query_location`); direct-ATS postings by `(source, company)` since
  individual `SourceEndpoint` rows don't exist yet for those boards
  (that's SUP-02); everything else by source alone.
- Known, disclosed gap: `SourceEndpointRun` (per-endpoint fetch duration/http
  status/provider-record telemetry) is a real table but nothing writes to it
  yet -- direct-ATS adapters fetch through one shared `search_jobs(source=
  "all")` call rather than per endpoint. `SourceQueryRun.routed_pairs` /
  `viable_evaluations` / `review_positives` are also still hardcoded to 0 at
  write time in `portfolio_run.py`. Until that instrumentation lands, the
  report's `fetch.instrumented` flag is `False` and duration/last-success are
  `None` rather than fabricated; "fetched" falls back to the count of
  postings actually evaluated in the run. Wiring real per-fetch telemetry
  into the intake adapters is a larger, separate change and is not part of
  this ticket.
- Tests: `tests/test_source_funnel_report.py` (DB-backed; needs Postgres up).
  Builds one healthy `greenhouse`/"Acme Corp" endpoint (3 postings, one fails
  geography, one surfaced) and one low-yield `adzuna` query arm (6 postings,
  zero routed, zero full-JD) inside a single discovery run and asserts counts
  reconcile, snapshot ids trace back correctly, and the aggregator arm is
  flagged `low_yield` while the healthy endpoint is not. Two smaller tests
  cover "no run yet" and "run with no linked evaluations".

### Job Pool V2 supply, repeat-run, and live-canary hardening (2026-07-12)

- Reduced portfolio CPU work by computing job facts and employer/posting
  assessments once per posting rather than once for every target. The measured
  live run fell from about 5m39s to 3m36s.
- Balanced early query-arm scheduling across targets, raised the V2 acquisition
  budget and Adzuna recall cap to 15, expanded RevOps/TCS terms, and enabled the
  already-verified Remotive feed for V2 searches.
- Added an exploration-only Tier C near-miss view that cannot reserve review
  cards or generate materials and excludes hard gate failures.
- Added `discovery_run_evaluations` so immutable evaluations reused across runs
  remain visible in each run's reports and replays. Durable selected portfolio
  decisions now prevent cleared queue rows from immediately resurfacing.
- Corrected loss reporting to count distinct failed/deferred evaluations.
- Marked Adzuna descriptions as snippets and capped snippet-only jobs below
  Tier B until a full employer JD is recovered. The live Orion Health result
  exposed the defect; its review entry was marked stale.
- Restarted the live web/worker processes after the final scorer/history fixes.
  Focused regression verification: 37 tests passed; the production SPA build
  and full 8,440-pair read-only v2.4 replay passed.
- Added `docs/JOB_SUPPLY_EXPANSION_PLAN.md`: a bounded direct-employer cohort,
  official ATS adapter, full-JD recovery, compliant board, and adaptive-yield
  roadmap. Indeed/Wellfound scraping remains prohibited.

### Job Pool V2 five-card canary activation (2026-07-12)

- Activated the user-requested V2 trial with a hard global capacity of five
  review cards. The five independent V1 plans are disabled so `Run Plans Now`
  cannot mix legacy and V2 candidates in the same review queue.
- The canary still admits only Tier A/B candidates and may create fewer than
  five cards. Materials are generated only for successfully reserved review
  cards; application submission remains explicitly human-approved.
- Rollback remains one edit: restore `matching.pipeline_version: v1`, disable
  `nightly-portfolio-v2`, and re-enable the five legacy plans.

### Job Pool V2 additive implementation and shadow gate (2026-07-12)

- Implemented the canonical candidate evidence bank, five strict target specs,
  duplicate-key-safe loaders, role/capability taxonomies, and a read-only V1
  generation adapter. The live authority remains `matching.pipeline_version:
  v1`; an enabled global `v2_shadow` plan writes audit data only.
- Added deterministic JobFactsV2, tri-state eligibility, employer/posting
  assessments, separate Story Fit/Candidacy/Review Index components, hard A/B
  tier floors, confidence, evidence-linked explanations, and bounded structured
  feedback priors. Broad-funnel evaluation makes no LLM calls.
- Added the immutable V2 ledger migration for endpoints, discovery/query runs,
  job-target evaluations and reasons, portfolio runs/decisions, structured
  review feedback, and frozen evaluation sets. The migration was upgraded on
  the local PostgreSQL database and retains every V1 table/path.
- Added one-refresh/five-target orchestration and a transactionally reserved
  global portfolio: 20 quality-limited core slots plus five additive startup
  slots, A before B, no C filler, and canonical/company/target ownership caps.
  Shadow mode creates neither review cards nor material tasks; no path submits
  applications automatically.
- Added structured negative feedback in the review queue and surfaced-week
  evaluation journeys for business conversion. Legacy events remain readable
  but are explicitly excluded where their evaluation binding is uncertain.
- Added deterministic offline replay, canonical-group temporal isolation,
  V1/V2 comparison metrics, Wilson intervals, and seeded blinded-set export
  that hides model arm, tier, score, and selection fields.
- Added a nontechnical Search Quality screen with target tier supply, attrition
  reasons, endpoint health, proposed-versus-created cards, and missing-data
  reporting. Rebuilt the production SPA.
- Added disabled-by-default SmartRecruiters, Workable, and Recruitee public
  adapters plus normalization fixtures. Activation still requires live
  conformance and useful unique A/B yield.
- Added a validated 35-employer/73-target-link research cohort. Every candidate
  is inactive and pending endpoint/evidence verification; release validation
  intentionally fails until verification and Arya approval are recorded.
- Cutover is intentionally not claimed: the required seven shadow cycles,
  50-item blinded review with at least 60% A/B precision, canary, and two-week
  rollback soak are real-world gates and have not yet elapsed.
- Live shadow rehearsal retrieved 1,699 unique jobs and evaluated 8,495
  job-target pairs without creating cards or materials. That rehearsal exposed
  and fixed an overlong HN employer field, `Sr.` seniority leakage, company-age
  years misread as required experience, boilerplate remote text overriding an
  explicit office requirement, unsupported-specialization leakage, and missing
  AI deployment/startup signals. A read-only full-snapshot v2.3 replay retained
  only two A/B candidates (Nooks AI Deployment Strategist and Vantage Solutions
  Engineer), demonstrating intentional empty slots rather than quota filler.

### Job Pool V2 authoritative design (2026-07-12)

- Added `docs/JOB_POOL_V2_ARCHITECTURE.md`, the approved implementation contract
  for replacing copied profiles, opaque similarity ranking, split search
  configuration, independent plan quotas, and unaudited source attrition.
- The design defines one canonical evidence bank, five target specifications,
  a reconciled staged funnel, tri-state eligibility, separate Story Fit and
  Candidacy judgments, deterministic tier floors, structured feedback, source
  health and employer/posting assessments, and a transactional global portfolio
  with five additive startup slots.
- Added bounded V2-00 through V2-15 tickets with exact file boundaries, tests,
  dependencies, migrations, rollback behavior, and completion criteria for
  smaller implementation models. The additive implementation above now follows
  this contract; V1 remains the live authority until the prospective gates pass.
- Recorded the controlling architecture decision as D031.

### Sol Ultra job-pool redesign brief (2026-07-12)

- Added `docs/SOL_ULTRA_JOB_POOL_REDESIGN_BRIEF.md`, a design-authority handoff
  documenting measured pool, profile, scoring, source, filter, employer,
  feedback, and selection failures. It includes exact evidence, implicated
  files, invariants, required design deliverables, and acceptance criteria for
  smaller implementation models.
- Confirmed the core matching defect: analyst, implementation, and TAM profile
  bodies are identical after ignored comment headers; sales differs mainly in
  QA prose that matching does not consume. Historical score bands are also
  non-predictive of apply/skip behavior (30% take at 0.6-0.8 versus 31% at
  0.4-0.6).
- An unfinished target-fit scorer experiment was removed before the handoff;
  existing matching behavior remains intact and 34 matching tests pass.

### Automation plan quality and queue diversity (2026-07-12)

- Added a plan-level role-family gate so analyst, TAM, sales-engineering,
  implementation, and AI-implementation plans cannot select unrelated software
  engineering roles merely because their descriptions share technical terms.
- Added a direct-employer quality gate for identifiable staffing/recruiter
  listings, wrong-domain sales-title exclusions, a hard 0.50 fit floor, and a
  conservative selection-time company/title collapse. No database records are
  fuzzy-merged or deleted.
- Plans now exclude jobs already pending review before Top-N selection, so
  concurrent plans backfill distinct cards instead of reporting the same five
  jobs repeatedly. The current single-process threaded worker serializes this
  selection/reservation step.
- Expanded TAM discovery with entry-friendly technical customer-success terms.
  AI implementation keeps a broader, role-gated startup discovery lane so up
  to five relevant startup bonuses remain available without displacing Top-N.
- Reports now distinguish role-compatible jobs, weak employers, jobs below the
  score floor, exact duplicates, previously applied jobs, already-pending jobs,
  newly created review cards, total startups, raw/source counts, search-filter
  attrition, and the actual selected job list.
- Added `scripts/audit_plan_quality.py`, a no-enqueue/no-materials rehearsal
  that writes `data/audits/latest_plan_quality.json` for Claude/Codex review.
- Reduced local embedding input to 4,000 characters after live rehearsals
  proved 20,000 and 8,000 still caused repeated Ollama context-length failures.
- Final rehearsal: 26 jobs selected (21 core + 5 startup bonuses). Four role
  families produced five core matches; TAM intentionally stopped at three
  rather than admit two candidates scoring 0.39 and 0.37.
- Follow-up raw-pool audit corrected the initial diagnosis: discovery volume is
  high (610–970 raw records per plan), but direct-source quality is uneven.
  Analyst had only 7 ATS keyword matches versus 384 Adzuna results;
  implementation had 19 versus 355; TAM retained only 21 role-compatible jobs,
  of which 17 were weak-employer, sub-0.50, or already applied. The remaining
  source-quality bottleneck is therefore real even though raw volume is ample.

### Startup bonus lane (2026-07-11)

- Every scored `source=all` search now keeps a separate HN startup candidate
  lane. Exact keyword matches remain normal ranked results; non-keyword startup
  candidates must still pass location, experience, compensation, hard-rule,
  and minimum-fit gates.
- Interactive searches add only enough highest-scoring bonus candidates to
  reach five startup results. Normal best matches are never displaced.
- Scheduled plans and `Run Plans Now.bat` select their configured `top_n` plus
  enough qualified, not-previously-applied startup jobs to reach five. Reports
  expose `startup_bonus_selected` for auditability.

### Cover-letter repair loop and official HN startup jobs (2026-07-12)

- Added a three-attempt cover-letter repair loop. Rejected short/drifted drafts
  feed their exact failure reason into the next attempt; final failure remains
  fail-closed with no boilerplate artifact.
- Declined Wellfound browser scraping because its current terms prohibit
  scraping/harvesting. Existing pasted-JD materials remain the safe Wellfound
  intake path.
- Added the official Hacker News Firebase `jobstories` feed as a cached,
  startup-native source alongside the monthly HN hiring thread.

### Cover-letter and job-quality recalibration (2026-07-11)

- Diagnosed recent generic letters as silent deterministic fallbacks: the
  generator emitted an artifact even while validation warned it was too short,
  underfilled, under-structured, and a raw evidence dump.
- Cover-letter generation now fails closed and applies fact-drift checks on the
  active generator path, preventing both boilerplate and invented metrics.
- Recalibrated job quality with a 0.50 display floor, shared clearance and
  staffing gates, robust `Sr` recognition, restored HN unknown-pay startup
  posts, startup/entry signals, and high-selectivity employer penalties.

### Funnel analytics, duplicate clusters, and legacy uniqueness (2026-07-11)

- Added append-only, idempotent funnel milestones from discovery through offer,
  weekly conversion APIs/views, source/profile/material dimensions, and elapsed
  application time capture.
- Added conservative canonical fingerprints and non-destructive `Possible
  duplicate` clusters in the Job Database.
- Audited the live legacy table (zero normalized duplicate groups), aligned
  Python deduplication with source-global identity, and added a transactional
  normalized unique index/backfill migration.
- Added `docs/FUNNEL_IDENTITY_HANDOFF.md` with practice-run evidence and explicit
  contracts for Claude and future maintainers.

### Repository audit and conservative cleanup (2026-07-11)

- Added `docs/AUDIT_2026-07-11.md` with a current architecture map, risk assessment, prioritized
  engineering roadmap, product avenues, and a hiring-focused operating strategy.
- Aligned package metadata with the existing `0.18.9` product version shown in the README.
- Removed unused imports from the document engines, question-answer service, and task tests;
  removed a dataclass-helper shadow in plan-run search-profile mapping; wrapped one lint overflow.
- Verification: 135 focused backend tests passed; a full run reached 495 passes before PostgreSQL
  was closed; the frontend production build passed with the existing large-chunk warning.

### Fix: local embeddings circuit breaker tripped by long JDs, not just Ollama outages (2026-07-11)

- **User report**: a practice Jobs-tab search logged `Ollama embeddings returned 500: {"error":"the input length exceeds the context length"}` once, then every subsequent job (across all 4 saved applicant profiles' scoring passes on the same 693-job set — see below) silently fell back to keyword-overlap for the full 120s backoff window, even though Ollama itself was healthy the whole time.
- **Root cause, two layers** (`src/matching/semantic.py`): (1) `embed_text_local`'s truncation cap (`_MAX_EMBED_INPUT_CHARS = 32_000`) was shared with the OpenAI path and sized for OpenAI's ~4-chars/token assumption; `nomic-embed-text`'s tokenizer runs denser on bullet/punctuation-heavy JD text, so a 32k-char description can still exceed its 8192-token window after truncation. (2) More importantly, the circuit breaker didn't distinguish *why* the request failed — a context-length 500 (a property of that one job's text) tripped the exact same 120s "assume Ollama is down" backoff as a connection failure or a genuinely-unpulled model, degrading every OTHER job in the batch even though the service was fine.
- **Fix**: added a separate, tighter `_MAX_LOCAL_EMBED_INPUT_CHARS = 20_000` cap for the local/Ollama path only (OpenAI path untouched). More importantly, `embed_text_local` now inspects the error body for `"context length"` — that case logs once and returns `None` for just that job **without** tripping the breaker, so the next job's text still gets a real embedding attempt; every other failure mode (non-200 status, connection error, timeout) still trips the breaker exactly as before.
- Also confirmed **not a bug** while investigating the same log: the repeated `Scored 693 jobs: 487 qualified, 206 disqualified` line (4x in ~8s) is `src.application.jobs._score_jobs` scoring the same job set once per saved applicant profile in `data/profile/profiles/` (a 2026-07-07 multi-profile-scoring feature, so a job matching a non-active resume profile better still surfaces correctly) — identical qualify/disqualify counts across passes are expected since that split comes from profile-independent hard rules (visa/experience/employment type), not the per-profile fit score.
- Tests: `tests/test_improvements_2026_07.py::TestLocalEmbedCircuitBreaker` (a context-length error hits the network on the very next call instead of short-circuiting; a generic 500 and a connection error both still trip the breaker; the local cap is actually applied to the outgoing request).

### "Copy pack" button on review cards (2026-07-11)

- **`GET /api/review/{entry_id}/copy-pack`** (`src/web/routes/review.py`): bundles everything a user applying manually needs for one posting into a single fetch — active-profile identity fields (name, email, phone, location, LinkedIn), generated-artifact absolute paths, the posting URL, and the top 5 saved QA-bank answers most relevant to the job. Reuses existing helpers rather than reinventing them: `_entry_artifacts` and `_entry_application_url` (already used by the kanban card's "View posting"/materials links) for artifacts and the URL.
- **New helpers**: `_entry_job_text` (title + description for QA matching — legacy `Job` row first, falls back to the bound `JobSnapshot` for Job Index entries); `_qa_bank_matches_for_job` (the same token-overlap technique as `question_answers._similar_saved_answers`, `>=3` shared tokens, but matched against the job's title+description instead of a live user question, since the copy pack has no question in hand); `_active_profile_identity` (best-effort profile load, returns `{}` rather than erroring when no profile is configured, so the rest of the pack still comes back).
- **Frontend** (`ReviewQueueView.vue`): a "Copy pack" button on both the `pending` and `approved` card action rows opens a modal (shadcn-vue `Dialog`) listing identity fields, the posting link, artifact paths, and matching QA answers, each with its own copy icon (`navigator.clipboard.writeText`, mirroring the existing pattern in `MaterialsQuestionsView.vue`) plus a "Copy all" button that concatenates everything (excluding artifact paths, which aren't useful pasted into a form field) into one paste-ready block. A brief checkmark swap gives per-item copy feedback instead of a global toast, since several items can live in the modal at once. SPA rebuilt (`cd frontend; npx vite build`).
- Tests: `tests/test_web_review_routes.py` — `TestCopyPackRoute` (happy path, 404 for a missing entry, 404 for cross-tenant access, and a missing-profile case that still returns the rest of the pack), all fully mocked at the `src.web.routes.review` import boundary (no live Postgres needed, unlike this file's existing DB-backed classes) per the brief's "mocked session, test_web.py style" ask; plus `TestQaBankMatchesForJob` and `TestActiveProfileIdentity` unit tests for the two new helpers' actual logic (overlap threshold, 5-result cap, real YAML parsing for identity extraction).

### Reduce em-dash / AI-sounding text in resume bullets + Materials-tab answers (2026-07-11)

- **User report**: resume bullet rewrites and drafted application-question answers (Materials → Questions tab) overused em/en dashes and read a bit AI-generated. Cover letters already had this fixed (`src/generation/cover_letter.py` has explicit "sound human", banned-word, and no-dash rules plus a `_normalize_cover_letter_dashes` post-processing pass) — resume bullets and question answers had no equivalent.
- **Calibrated, not copy-pasted from cover letters**: cover letters ban a list of words ("spearheaded", "leveraged", "utilized", "orchestrated", "delved", "fostered") because they read as filler in flowing prose. Those same words are normal, expected resume-bullet action verbs, so none of that banned-word list or the "read-aloud test" / sentence-length-variation instructions were copied into `resume_builder.py` — only the narrow, uncontroversial part of the fix (no em/en dashes) was added there, to avoid overcorrecting into stilted, hedge-everything bullets.
- **`src/generation/resume_builder.py`**: added a "Do NOT use em dashes or en dashes" rule (with an explicit compound-word-hyphen carve-out, e.g. "time-to-value" / "full-time" stay untouched) to all three bullet-rewrite prompts (`_BATCH_REWRITE_SYSTEM`, `_REWRITE_SYSTEM_BASE`, `_LENGTH_REWRITE_SYSTEM`). Added a new `_normalize_prose_dashes()` helper (mirrors `cover_letter._normalize_cover_letter_dashes`, regex-targets only the Unicode em dash `—` / en dash `–`, never the ASCII hyphen) as a backstop, wired into the shared `_clean_llm_bullet_rewrite_output()` choke point (covers the single-bullet and length-rewrite paths) and into the batch-rewrite loop in `_rewrite_grouped_evidence()`.
- **`src/application/question_answers.py`**: added the same em/en-dash rule plus one light tone line ("write the way the applicant would actually type it: plain sentences, not corporate or essay-like phrasing") to `_SYSTEM` — deliberately just one line, not the cover letter's full voice-rule block, since these are shorter first-person answers where over-instructing risks producing text that reads like it's *trying* to sound casual (the overcorrection failure mode). Added `_normalize_answer_dashes()`, applied in `_parse_response()` on both the JSON-parse success path and the fallback-to-raw-text path, so every answer returned by `draft_question_answer()` is covered regardless of how the model responded.
- Tests: `tests/test_improvements_2026_07.py::TestProseDashNormalization` (9 tests — em/en dash to comma on both new helpers, compound-word hyphens explicitly proven untouched on both sides, the shared bullet-cleanup choke point, the batch-rewrite path end-to-end, and both `_parse_response` code paths for question answers).

### Remotive intake adapter (2026-07-11)

- **`src/intake/remotive.py`**: new `RemotiveScraper` for Remotive's free, keyless remote-jobs API (`GET remotive.com/api/remote-jobs?search=<keyword>`). Response shape verified live: `{"job-count", "total-job-count", "jobs": [...]}`, each item `id`, `url` (the Remotive listing page → `application_url`), `title`, `company_name`, `job_type`, `publication_date` (naive ISO datetime, no tz suffix), `candidate_required_location` (free text: "Worldwide", "USA, CST (UTC-6)", etc. → `location`), `salary`, `description` (full HTML, not truncated).
- **Live-verified caveat worth flagging**: Remotive's own docs (github.com/remotive-io/remote-jobs-api) document `search`/`category`/`limit` query params, but the live endpoint is currently served through a Cloudflare cache (`Cf-Cache-Status: HIT`, `Age: ~69000s`) that appears to key on the path only — every parameter combination tested, including a cache-busting nonce, returned an identical ~19-hour-old 30-job snapshot. Correctness doesn't depend on server-side filtering actually working: the adapter still sends `search` as documented (harmless, and self-correcting if the cache issue is temporary), and `src.application.jobs` re-applies keyword filtering client-side the same way it already does for every other source.
- **Ghost-age**: `raw_data = item` already carries `publication_date` through verbatim; `src/matching/scorer.py::_posting_age_days` gained it as a candidate — no reformatting needed, `_parse_posting_datetime`'s naive-datetime fallback (assume UTC) already handles the missing tz suffix.
- **Wired as a `source in ("all",)` leg, gated on remote-only intent**: unlike Adzuna/HN (which always run under "all"), Remotive is a remote-jobs-only board, so `src/application/jobs.py` only calls it when `"remote"` is in the caller's `location_types` — a search for hybrid/onsite roles skips it entirely rather than wastefully fetching (and then filtering out) an all-remote result set. Only the first keyword is sent server-side (`_search_remotive` — Remotive's `search` is a single free-text param, not per-keyword like Adzuna's `what`); keyword narrowing against every keyword still happens client-side afterward, same as the other legs.
- Tests: `tests/test_intake_remotive.py` (field mapping incl. HTML-description stripping and internship classification, empty-keyword omits the `search` param, malformed/bad-shape/HTTP-error handling, ghost-age scorer integration, and the `search_jobs` wiring: remote-gating on/off, keyword narrowing, and isolated error handling).

### Funded-startup board seed (2026-07-11)

- **`config/companies.yaml`**: added 35 verified boards (14 greenhouse, 3 lever, 18 ashby) curated from YC top companies and recent Series B+ dev tools / AI / SaaS startups — the user's target space. Verification was live, not slug-guessing: every entry was confirmed via `boards-api.greenhouse.io/v1/boards/{slug}/departments`, `api.lever.co/v0/postings/{slug}?limit=1`, or `api.ashbyhq.com/posting-api/job-board/{slug}` returning a real response before being added; candidates that 404'd or came back empty-shaped were dropped rather than guessed at.
- **Slug-collision check, not just "does a board exist"**: several plausible slug guesses returned a *live but wrong* board — e.g. `finch` on Ashby is a consumer app (Product Designer / Senior Animator postings, not the HR/payroll API company), while `finch` on Lever has the real match (Developer Success Engineer, Retirement product); `merge`/`alloy` similarly had a same-slug collision on one ATS and the real company on the other. Resolved by reading actual job titles/departments from each live response to confirm company identity before picking which ATS entry to keep, not just taking the first 200 response.
- Two entries turned up direct keyword hits for the user's own target roles while verifying: Modal (`ashby/modal`) has an open "Forward Deployed Engineer - ML" req, and Alloy (`greenhouse/alloy`) has "Founding Forward Deployed Engineer" — both literal matches to the `ai-solutions` search profile's keyword list.
- No pay-quality curation logic added here — per the brief, these boards flow through the existing $90k pay floor already configured in every search profile (`config/search_profiles.yaml`), which is what actually enforces the "well-paying" bar; this task was sourcing, not filtering.
- `config/companies.yaml` is gitignored (local, user-editable) so this change has no corresponding diff in version control — verification was done via live API calls and confirmed by fetching jobs through the actual `GreenhouseScraper`/`LeverScraper`/`AshbyScraper` classes (not just raw curl) before considering the task done.

### HN "Who is hiring?" intake adapter + "all" permanently excludes LinkedIn (2026-07-11)

- **`src/intake/hn_hiring.py`**: new adapter for the monthly HN "Who is hiring?" thread via the keyless Algolia API. Two-step: `search_by_date?tags=story,author_whoishiring&query=Who%20is%20hiring` finds the latest thread (client-side title regex required — verified live that the raw query alone also matches the sibling "Who wants to be hired?" thread via Algolia's stemming), then `items/{id}` pulls all top-level comments (each comment's own nested `children` are replies, not postings, and aren't recursed into). Comment parsing was calibrated against a real live thread (262 top-level comments) rather than assumed: the header line (text before the first `<p>`) splits on `|` or on a hyphen/en-dash/em-dash **with mandatory surrounding whitespace** (so "Full-Stack" doesn't get mis-split); a segment containing "remote" becomes `location` (no guessing beyond that — unknown stays unknown); the first `href="..."` anywhere in the comment becomes `application_url` (HN auto-linkifies plain URLs server-side, confirmed live), falling back to the comment's own permalink (`news.ycombinator.com/item?id=...`) for the ~16% of postings with no link. Comments under `MIN_COMMENT_CHARS` (50) are skipped as noise — calibrated against real data to sit between an actual one-line non-posting remark ("don't waste your time with these guys", 37 chars) and the shortest genuine posting observed live (72 chars).
- **No new pay-parsing code needed**: reused the existing `_extract_pay_range`/`PAY_RANGE_RE` pipeline in `src/application/jobs.py` (already regex-scans `job.description`) — HN comment text flows through the same metadata classification every other source uses, and it correctly extracts pay from the brief's own `"$120k-$160k"` example with no changes.
- **Ghost-age**: comments already carry `created_at` (ISO string) verbatim into `raw_data` since `raw_data = dict(comment)`; `src/matching/scorer.py::_posting_age_days` gained `raw.get("created_at")` as a candidate — no reformatting needed, the existing ISO parser already handles it.
- **Board-cache reuse**: `fetch_latest_hn_hiring_jobs` looks up the thread id first (cheap), then checks/writes the SAME in-process board cache the ATS scrapers use (`src.intake.search._board_cache_get`/`_board_cache_put`), keyed `("hn", thread_id, False)` exactly as specified — a repeated search within the TTL window doesn't re-fetch and re-parse all ~250+ comments.
- **Startup quality gate** (`src/application/jobs.py::_apply_search_filters`): the adapter sets `raw_data["strict_pay"] = True` on every HN job. A new check — placed immediately before the existing pay filter, touching neither `_matches_numeric_filter` nor any other source — drops a job when a pay filter is active, `strict_pay` is set, AND no compensation was extracted at all. Every other source's "unknown passes" convention (documented in the surrounding docstring) is completely untouched; this only ever fires for `source="hn"` since only the HN adapter sets the flag.
- **Wired as a `source in ("all",)` leg**, matching the Adzuna/Workday pattern: keyword narrowing applied explicitly (HN's own "search" is really just the whole thread); location narrowing is generic and needed no special-casing since HN isn't LinkedIn (the `should_skip_location_filter` check in `_apply_search_filters` only ever exempts `source == "linkedin"`).
- **"all" now permanently excludes LinkedIn** (`src/application/jobs.py`): while wiring this leg in, live-testing `search_jobs(source="all", ...)` unexpectedly launched a real LinkedIn Playwright browser — the 2026-07-10 safety fix (`b336084`) had only pinned the 5 saved search profiles to `source: ats` to route around this, which as a side effect also excluded Adzuna/HN from the overnight automation (they only ever ran under `"all"`). Root-caused and fixed properly: the LinkedIn leg's condition changed from `source in ("linkedin", "all")` to `source == "linkedin"` only — LinkedIn now requires an explicit, standalone request and can never be swept in by `"all"` again, for either the overnight automation (`src/orchestration/plan_run.py`, which defaults to `source: "all"`) or a manual "search everything" call. `_resolve_linkedin_search_locations` updated to match. `config/search_profiles.yaml`'s 5 profiles reverted from `source: ats` back to `source: all` — now safe, and restores Adzuna/Workday/HN coverage for the overnight plans that the blunt `ats`-only fix had accidentally suppressed. Frontend: `JobsView.vue`'s `sourceUsesLinkedIn` computed flag (gates the LinkedIn auth banner, session pre-loading, and several LinkedIn-only controls) narrowed from `source === "linkedin" || source === "all"` to `source === "linkedin"` only, so selecting "All" no longer shows a misleading "connect your LinkedIn account" prompt; SPA rebuilt.
- Tests: `tests/test_intake_hn_hiring.py` (comment parsing incl. the 3-comment fixture from the brief — pipe-delimited-with-URL, dash-delimited-no-URL, no-delimiter-with-URL — plus noise/deleted-comment skip, `_split_header` edge cases incl. the compound-hyphen guard, thread-title matching incl. the "wants to be hired" exclusion, board-cache hit/miss, `search_jobs` leg wiring incl. keyword narrowing and isolated error handling, and the strict-pay gate: drops with no stated pay, passes on `"$120k-$160k"`, still drops when stated pay is below the floor via the normal numeric filter, and confirms non-HN sources are completely unaffected). Also repurposed `tests/test_web.py`'s `test_jobs_search_all_keeps_ats_results_when_linkedin_fails` (LinkedIn can no longer fail during an "all" search since it no longer runs) into `test_jobs_search_all_keeps_ats_results_when_hn_fails`, which additionally asserts LinkedIn is never invoked.

### Workday intake adapter — big-enterprise class (2026-07-11)

- **`src/intake/workday.py`**: new adapter for Workday's public CXS (Candidate Experience Site) API. Response shape verified live (not just from the brief's assumed pattern) against five real tenants — salesforce (wd12/External_Career_Site), adobe (wd5/external_experienced), workday itself (wd5/Workday), athenahealth (wd1/External), healthcatalyst (wd5/healthcatalystcareers). Two corrections to the brief's assumptions, both confirmed live: (1) the list endpoint (`POST .../wday/cxs/{tenant}/{site}/jobs`) carries **no description field at all** (not even a short snippet) — every Workday job is title/location-only until detail-fetched, making the detail-fetch step load-bearing rather than an optimization; (2) `limit` has a **hard server-side cap of 20** (21+ returns `HTTP_400`) and `total` is unreliable past the first page (observed returning `0` on page 2 of a 1487-job board), so pagination stops on a short page rather than trusting `total`.
- **Pagination**: capped at `MAX_JOBS_PER_BOARD = 200` (page size fixed at the server's 20-item ceiling); verified live against Salesforce's 1487-job board — stopped at exactly 200 with no duplicate `source_id`s and no 11th request issued.
- **Job mapping**: `WorkdayScraper.fetch_jobs(tenant_config)` — doesn't literally implement `BaseScraper.fetch_jobs(company_slug: str)` since a bare tenant string can't determine host/site; takes the `{tenant, host, site}` dict instead (still inherits `BaseScraper` for the shared httpx client). `application_url` is built as the human-facing career-site URL (`{base}/{site}{externalPath}`, confirmed to exactly match the detail response's own `externalUrl` field); a separate `raw_data["workday_detail_url"]` holds the internal CXS API URL used only for detail fetching, kept apart so it can never leak out as something a human would click. Employment type is classified from `timeType` + `bulletFields` + title combined (not OR'd) — `bulletFields` content varies by tenant (some carry only the req id, others prepend a generic value like "Regular" that would otherwise mask a real "intern" signal in the title), so `classify_employment_type`'s ordered scan needs all the text together to pick the specific signal over the generic one (same pattern used for the Adzuna adapter).
- **Ghost-age signal**: Workday's `postedOn` is a RELATIVE human string ("Posted Today" / "Posted Yesterday" / "Posted N Days Ago" / "Posted 30+ Days Ago"), not an ISO date, so `_parse_relative_posted` converts it to an approximate ISO date stashed as `raw_data["workday_posted_date"]` at list-fetch time. When a job gets detail-fetched, the detail response's `startDate` field (an actual ISO date, observed live to equal the true posting date) overwrites it with a more precise value. `src/matching/scorer.py::_posting_age_days` gained `raw.get("workday_posted_date")` as a new candidate.
- **Detail fetch, keyword-gated and capped** (`src/application/jobs.py::_enrich_workday_job_details`): runs after the existing ATS keyword-narrowing filter, groups surviving Workday jobs by tenant, and fetches full JDs for up to `DETAIL_FETCH_CAP = 30` per tenant per run via a small `ThreadPoolExecutor` (sequential fetching at ~1-2s/request would take minutes across multiple tenants — unacceptable for a live web search). A tenant with more keyword-surviving matches than the cap logs one warning naming how many were left without a full JD. Verified live: 9/9 jobs on a small tenant went from 0 to full descriptions (~7KB HTML→text each) in well under a second wall-clock thanks to the thread pool.
- **Board-fetch wiring** (`src/intake/search.py`): `WorkdayScraper` added to `scraper_map` next to greenhouse/lever/ashby. Workday's `companies.yaml` entries are `{tenant, host, site}` dicts, not bare slug strings — unlike every other ATS here — and dicts aren't hashable, which the existing board-cache/board-results plumbing depends on (`dict[tuple[str, str, bool], ...]` keys). Fixed by converting each Workday entry to a `(tenant, host, site)` tuple at board-task construction time and reconstructing the dict only at the `scraper.fetch_jobs()` call site; a new `_slug_label()` helper keeps log lines readable (`[workday/salesforce]` instead of the raw tuple). Deep-copy cache semantics (invariant #4) are unchanged — `_board_cache_get`/`_board_cache_put` already copy on read/write generically, regardless of the key's shape. A malformed `companies.yaml` workday entry (missing `tenant`/`host`/`site`) logs one warning and is skipped, matching the self-pruning convention; a tenant/site that's live but wrong (bad slug) 200s with a Workday-native `errorCode` body rather than failing the HTTP request, so `_post_json` treats that the same as a transport error — one `ScraperError`, one log line, board loop continues.
- **`config/companies.yaml`**: added `workday:` section — salesforce, adobe, workday (enterprise SaaS) + athenahealth, healthcatalyst (healthcare IT) — all 5 verified live against their CXS API 2026-07-11. Header comment documents the differing dict-list format since it isn't derivable from the tenant name the way a Greenhouse/Lever/Ashby slug is (host shard and career-site slug are both arbitrary per tenant).
- No `RawJob.source`/`ATSType` schema change needed — `"workday"` was already a reserved literal value from before this adapter existed.
- Tests: `tests/test_intake_workday.py` (29 tests — field mapping incl. the combined employment-type classification and multi-location placeholder, pagination incl. the max-cap boundary and the unreliable-`total` case, malformed-item skip, transport-error and Workday-native-error-body `ScraperError` paths, detail-fetch success/failure/no-url, relative-date parsing incl. the "30+" form, ghost-age scorer integration, the `search.py` board-loop wiring incl. a malformed-entry skip and a bad-tenant-continues case, and `_enrich_workday_job_details`'s per-tenant cap independence).

### Adzuna intake adapter — LinkedIn replacement (2026-07-10)

- **`src/intake/adzuna.py`**: new adapter for Adzuna's free Job Search API (`GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}`, `app_id`/`app_key` query auth). Unlike the board scrapers this is a keyword-search API, not a per-company board, so `AdzunaScraper` doesn't implement `BaseScraper.fetch_jobs(company_slug)` — it exposes `search(keyword, location, page, results_per_page)` instead, reusing `BaseScraper`'s default timeout/headers/`ScraperError`. Response shape verified against a real live call (not just the docs): top-level `{"results": [...], "count": N}`, each job `id`, `title`, `company.display_name`, `location.display_name`/`.area`, `description` (a TRUNCATED snippet), `redirect_url`, `created` (ISO), `salary_min`/`salary_max`, `contract_time`/`contract_type`, `category`. Employment type is classified from `contract_time`+`contract_type`+`title` combined (not OR'd) — `classify_employment_type`'s ordered scan checks "intern" before "full", so a real-world case where Adzuna tags an internship posting `contract_time: full_time` still resolves correctly once the title text is in the mix.
- **LinkedIn replacement**: LinkedIn scraping stays permanently off (account restriction, see "safety: stop all automated LinkedIn access" below) — Adzuna is the new broad-aggregation source. `source="adzuna"` and `RawJob.source`'s `ATSType` literal both updated (`src/intake/schema.py`, `src/application/jobs.py::ATS_TYPES`).
- **Wired into `search_jobs`** (`src/application/jobs.py`): new leg fires only when `source == "all"` and `adzuna.enabled` is true. Queries once per resolved keyword (same `linkedin_keywords` list the ATS leg already narrows on), capped at `ADZUNA_MAX_CALLS_PER_SEARCH = 10` calls — free tier is 250 calls/day and five overnight automation plans run daily, so an unbounded per-keyword fan-out could burn the whole day's quota in one search; a warning logs when the cap truncates the keyword list. Zero keywords still makes one broad (unfiltered `what`) call rather than skipping. Adzuna results pass through the **normal ATS-style location/keyword filters** (invariant #2 in CLAUDE.md) — `_apply_search_filters`'s LinkedIn-only skip checks `job.source == "linkedin"`, so `source="adzuna"` was never eligible for the skip and needed no filter-logic change, only the wiring.
- **Config** (`config/settings.yaml`, `.example`): new `adzuna:` block — `enabled`, `app_id` (not secret-sensitive per Adzuna's own convention, stored in the gitignored `settings.yaml`), `app_key_env` (default `AUTOAPPLY_ADZUNA_KEY`, resolved from the environment — never written to YAML), `country`, `results_per_query`.
- **Board discovery** (`src/intake/board_discovery.py` via `register_discovered_boards`): Adzuna jobs are merged into the same discovery call as LinkedIn jobs. In practice this is currently a near-total no-op: Adzuna's `redirect_url` is always its own tracking landing page (`www.adzuna.com/land/ad/...`), which 403s on every unauthenticated fetch attempt (verified live with both `curl` and `httpx`, browser-like headers included — looks like Cloudflare bot management, not a header/UA issue), so it never resolves to the real company/ATS URL server-side and never matches the greenhouse/lever/ashby regexes. Left wired in anyway (harmless, and `discover_board_slugs` doesn't care about `source`) in case Adzuna ever returns a direct board URL or the tracking wall changes.
- **Ghost-age signal** (`src/matching/scorer.py::_posting_age_days`): added `raw.get("created")` (Adzuna, ISO string) to the candidate tuple.
- **JD recovery**: no code change needed — `materials.generate`'s thin-JD recovery step (`src/tasks/tasks.py`) is purely `description`/`application_url`-driven with no source allowlist, so it already triggers for Adzuna jobs when the truncated snippet is under `generation.min_jd_chars`. In practice the same tracking-URL 403 wall above means recovery will usually fail silently for Adzuna postings (best-effort, already tolerated) — most Adzuna snippets observed live were long enough (>300 chars) to clear the gate anyway, just truncated mid-sentence.
- Tests: `tests/test_intake_adzuna.py` (field mapping incl. the combined employment-type classification and `created` passthrough, malformed/bad-shape/HTTP-error handling, missing-credentials guard, board-discovery handoff for both the real-world tracking-URL no-op and the hypothetical direct-board-URL case, and `src.application.jobs._search_adzuna`'s disabled/missing-key/call-cap/no-keyword-broad-call behavior via a stub scraper). Also updated `tests/test_web.py::test_jobs_search_all_keeps_ats_results_when_linkedin_fails` to mock the new Adzuna leg — with real credentials configured locally this test was making a live Adzuna call and flaking on result ordering; it wasn't hermetic against a third `source="all"` leg before.

### Cover letter self-critique pass (2026-07-09)

- **One critique → revise cycle** (`src/generation/cover_letter.py::_generate_with_llm`): after the initial LLM draft, a second small-tier call (`generate_json`, `tier="small"`, 120s timeout) checks the draft against a strict checklist — (a) references the company's actual product/domain, not just its name; (b) has at least one concrete accomplishment with a stated outcome; (c) has no cover-letter filler ("I am excited to apply...", "I believe my skills..."); (d) makes no claims absent from the provided evidence/stories. A passing draft (or a critique call that errors) returns unchanged — critique is an enhancement, never a blocker, never raises. A failing draft gets exactly one revision call (`generate_text`, same original prompt + a `<critique>` block listing the problems and the original draft, instructed to fix only those problems while preserving every grounded claim and roughly the same length) — bounding the added latency to one extra round trip.
- **Skipped during page-fit iteration**: the existing iterative renderer re-calls `_generate_with_llm` with `previous_attempt`/`length_feedback` set to hit a page-count target; critiquing every one of those re-calls would multiply LLM calls for a concern (length) unrelated to content quality, so the critique cycle only runs on the first draft.
- **Config knob**: `generation.cover_letter_critique` (default `true`, `config/settings.yaml`) — adds ~1-2 min per letter on local models; the downstream cleaning/validation/fact-drift path is unchanged, it just sees the (possibly revised) draft in place of the original first pass.
- Tests in `tests/test_improvements_2026_07.py::TestCoverLetterCritique` (pass verdict returns original, fail verdict returns the revision with the critique problems reaching the revision prompt, a critique exception returns the original, `previous_attempt`/`length_feedback` skip the critique entirely, and the config knob disables it).

### Ashby intake adapter (2026-07-09)

- **`src/intake/ashby.py`**: new scraper for Ashby's public, keyless Job Board Posting API (`GET https://api.ashbyhq.com/posting-api/job-board/{slug}`), mirroring `greenhouse.py`'s structure. Response shape verified live against the "notion" board before coding to it: `{"jobs": [...]}`, each with `id`, `title`, `employmentType`, `location` (plain string), `secondaryLocations`, `isRemote`, `workplaceType`, `publishedAt`, `jobUrl`, `applyUrl`, `descriptionPlain`/`descriptionHtml`. Maps `employmentType` via the existing `classify_employment_type` (already produces the exact FullTime→fulltime/PartTime→parttime/Intern→internship/Contract→contract/unknown→unknown mapping, case-insensitive — no new mapping needed). Location is the primary `location` string with a `", Remote"` suffix appended when `isRemote` is true and the text doesn't already say so. Description prefers `descriptionPlain`, falling back to `strip_html(descriptionHtml)`. `"ashby"` was already present in `ATS_TYPES` (`src/application/jobs.py`) and `RawJob.source`'s `ATSType` literal — this closes the gap where the type was declared but nothing produced it.
- **Wired into search** (`src/intake/search.py`): `AshbyScraper` added to the board-fetch `scraper_map` next to greenhouse/lever — the existing TTL cache (keyed by `(ats, slug, parse_jds)`) and deep-copy-on-read/write behavior apply unchanged since both are already generic over `ats`. Also added to `_build_companies_filter`'s bare-`company` fallback in `src/application/jobs.py` so a company-only search checks Ashby too. `src/intake/batch.py::load_company_list` already passed any top-level YAML list key through (no ATS whitelist there) — the `ashby:` key needed no fix.
- **`config/companies.yaml`**: added `ashby:` section (notion, linear, vercel, ramp, gainsight, miro, zapier, mercury, confluent, loom) — all 10 slugs verified live against the posting API 2026-07-09 (vercel/mercury/loom currently return 0 open jobs but the board itself is real, not a dead slug, so they were kept). Removed the now-stale header note claiming Ashby companies aren't scrapable and must be applied to manually.
- **Board discovery** (`src/intake/board_discovery.py`): added `_ASHBY_RE` (`jobs.ashbyhq.com/{slug}`) alongside the existing Greenhouse/Lever patterns; `discover_board_slugs`/`register_discovered_boards` now carry a third `"ashby"` key, so LinkedIn-search-driven auto-discovery grows the Ashby board list the same way it already grows Greenhouse/Lever. Tests updated in `tests/test_improvements_2026_07.py::TestBoardDiscovery`.
- **Ghost-age signal** (`src/matching/scorer.py::_posting_age_days`): added `raw.get("publishedAt")` (Ashby, ISO string) to the age-detection candidate tuple — `_parse_posting_datetime` already handles ISO strings, so Ashby postings now participate in the existing age-penalty multiplier instead of always reading as unknown-age.
- Tests: `tests/test_intake_ashby.py` (job-field mapping incl. `employmentType`/remote-location handling, malformed-item skip, bad-shape `ScraperError`, board-discovery slug extraction).

### JD recovery + thin-JD gate (2026-07-09)

- **External JD recovery (`src/intake/jd_recovery.py`)**: jobs whose posting redirects off-platform (company career site, Workday, an ATS we don't scrape) kept stub-or-empty descriptions, so `parse_requirements` found nothing and generated materials were generic. `recover_job_description(url)` fetches the posting page (httpx, realistic UA, redirects followed), strips script/style/nav/header/footer/form, and returns the largest contiguous remaining text block (readability-lite heuristic, no new heavy deps — stdlib `html.parser` + regex, matching the existing `src/intake/html_utils.py` convention rather than adding BeautifulSoup). Never raises — non-200, non-HTML, and <300-char extractions all return `None`. Successes are cached (`jd_recovery` namespace, keyed by sha256 of the URL, 24h TTL added to `NAMESPACE_TTLS`) so retries/regenerates don't refetch. Wired into `materials.generate` (`src/tasks/tasks.py`) immediately before generation: if the job's description is missing/short and it has an `application_url`, recovery runs once and, on success, `parse_requirements` re-runs against the recovered text so requirements/keywords are populated too. One INFO line either way.
- **Thin-JD gate**: even after recovery, `materials.generate` no longer burns an LLM pass on a near-empty description. If the description is still under `generation.min_jd_chars` (new config knob, default 300, `config/settings.yaml`) after the recovery attempt, generation is skipped and the task returns a structured `{"status": "thin_jd", "detail": ..., "description_chars": N}` result (mirrors the existing `not_implemented` honest-result pattern used elsewhere in `tasks.py`). The matching pending `ReviewQueueEntry.reason` is set to `"Materials skipped: job description too thin (Xch) — open the posting and retry"` so the review kanban card explains itself instead of silently having no materials. Tests in `tests/test_jd_recovery.py`.

### Follow-ups (2026-07-07, later still)

- **Materials → Questions tab (collaborative application answers)**: new `src/application/question_answers.py` + `/api/qa/draft|save|bank` routes + `MaterialsQuestionsView.vue`. Paste an application question ("examples of exceptional performance?") → LLM drafts an answer grounded in profile + story bank + previously saved answers, and returns up to 3 clarifying questions for the USER when the profile lacks the detail; the user answers, refines, and saves to the existing `qa_bank` table — which the form-filler's confidence cascade already checks first, so saved answers are reused verbatim on future application forms. Robust JSON parsing (fenced/prose-wrapped output tolerated; falls back to raw text as the draft). Drafting runs via `asyncio.to_thread` so long local-LLM calls don't stall the event loop.
- **Batch bullet-rewrite hardening (2026-07-09, from production logs)**: the batch refactor bypassed `_rewrite_regression_guard` — an invented metric reached a rendered resume (`added_unverified_number`, post-hoc warning only), and one 16-bullet JSON call on a thinking model produced a 300s timeout plus two malformed responses that silently discarded ALL tailoring for those resumes. Fixes: bullets now rewritten in chunks of 8 (`_BATCH_REWRITE_CHUNK_SIZE`) that fail independently; every accepted rewrite passes the regression guard; the guard additionally reverts rewrites that ADD numbers absent from the original; `/no_think` (Qwen3 soft switch, harmless elsewhere) prepended to the batch system prompt to stop thinking-mode timeout spirals; log line now reports accepted-rewrite counts.
- **Fallback cover letters no longer fabricate (2026-07-09 user report, Epic letter)**: when the LLM path failed, `_generate_template` stitched hardcoded engineering "capability bucket" claims around whatever evidence existed — a therapy-technician bullet was presented twice as proof of "building maintainable software systems", the raw entity key ("Encompass Health - Therapy Technician") appeared mid-sentence, and the result shipped with no flag. Rewritten to UNDER-claim: honest opening (who the applicant is), each real evidence bullet quoted once (deduped, entity titles stripped from prose), plain close — deliberately short; the ≥260-word test expectation that pressured the old fabrication was updated. Also `/no_think` prepended to the cover-letter and critique system prompts (matching the batch rewriter) so qwen thinking-mode timeouts stop triggering the fallback in the first place.
- **Document look-and-feel overhaul (2026-07-09)**: (1) human dates everywhere — `_humanize_date` ("2022-08" → "Aug. 2022") applied in `_format_date_range` for the DOCX IR path, the legacy block path (was inline f-strings), and the LaTeX engine; (2) `ats_single_column_v1` restyled to the classic professional format (per user-provided examples): Times New Roman throughout, 20pt bold name, 11pt bold ALL-CAPS section headings with a bottom rule (direct XML for caps/border + `update_docx_template_styles` for fonts/sizes so the style lock stays valid), italic subtitles; `classic_v1` cover-letter template moved to the same serif; (3) cover-letter voice rewrite — replaced the rigid 5-paragraph engineering-framed "Claim → Evidence → Relevance" system prompt with a plain-spoken structure (open with THEIR top task, two proof paragraphs telling one real thing each, role-specific close), read-aloud voice rules, plain-verb requirements, and an expanded banned-phrase list.
- **Incident note**: `docx_engine.py` was truncated mid-function during this session by a round-trip through a file-mount with a capped read size; recovered from `git show HEAD` + the legible working diff. Reminder that the repo's week of work is UNCOMMITTED — commit early.
- **Cover-letter critique: role-type framing check (2026-07-09)**: added check (e) to `_CRITIQUE_SYSTEM_PROMPT` — the letter's self-description must match the job title's role type (a Solutions Consultant application must not open "as an engineer", the exact failure observed on the regenerated Figma letter) — and the critique call now receives `<job_title>` alongside the description so the check is decidable. Complements the prompt-side role-framing anchor added to `_generate_with_llm` the same day.
- **Bullet rewriter was destroying JD keyword matches (2026-07-08 user report, Figma resume)**: the JD said "time to value" and "demos"; the profile bullets contained both verbatim; the local-model rewrite replaced them with "initial value realization" (semantically garbled) and "trial-to-pipeline progression" — paraphrasing AWAY the exact matches the rewrite exists to create, plus general thesaurus inflation ("Overhauled the customer integration lifecycle"). Two-layer fix in `resume_builder.py`: (1) `_REWRITE_SYSTEM_BASE` now declares matched keywords sacred, bans synonym swaps, and requires verbatim pass-through when no keyword fits; (2) `_rewrite_regression_guard` deterministically reverts to the original bullet when a rewrite drops a matched keyword (word-boundary, singular/plural tolerant), loses a number, or inflates length >35%. A rewrite can now never make a bullet worse than the profile original. Tests built from the actual Figma failure cases.
- **Self-growing board registry (`src/intake/board_discovery.py`)**: Greenhouse/Lever have no global search API — companies.yaml IS the ATS universe. New: after every LinkedIn search, postings that resolve to `boards.greenhouse.io/{slug}` / `job-boards.greenhouse.io/{slug}` / `jobs.lever.co/{slug}` URLs are harvested and textually appended to companies.yaml (comment-preserving insertion under the ats key; pyyaml round-trip would destroy the file's comments). Best-effort, thread-locked writes, dedup against existing slugs; a wrong slug costs one error line on the next fetch. Also expanded companies.yaml: +8 verified Greenhouse boards (anthropic, scaleai, datadog, mongodb, elastic, cloudflare, gitlab, samsara — checked live against boards-api), +10 high-confidence unverified, +2 verified Lever (palantir, mistral; plaid checked and empty, excluded).
- **plan_run now applies saved-search filters (2026-07-08 — why overnight results included London/ANZ/senior/AE roles)**: `run_plan` passed only `profile=` (the intake filter-profile name) to `search_jobs`; every field in `config/search_profiles.yaml` — keywords, locations, experience levels, employment/location types, pay floor, max_pages — was silently ignored, so overnight runs fetched entire company boards and top-N'd whatever scored best worldwide. New `_saved_search_profile_kwargs` mirrors the `POST /api/jobs/search` field mapping so a plan run applies exactly the filters the identical saved profile applies in the Jobs tab; missing profiles log a warning and fall back to legacy behavior.
- **Dead filter tokens repaired**: saved profiles said `experience_levels: [entry_level, associate]` and `location_types: [..., onsite]`, but the classifiers emit `entry` / `in_person` and the LinkedIn mapper wants `entry` — so an entry-level filter EXCLUDED jobs explicitly titled entry-level (known-value mismatch) while passing every unclassifiable title, and an "include onsite" selection excluded explicitly on-site jobs. Added `_normalize_experience_levels` / `_normalize_location_types` alias maps at the search_jobs boundary (entry_level/associate/junior→entry, mid_senior→senior, onsite/on-site/office→in_person) and corrected the user's five saved profiles to the canonical tokens.
- **Review kanban cards are now actionable for manual applying (2026-07-08 user report)**: plan-run entries rendered only company + title — no posting link, no materials — leaving the operator dead-ended ("I can't submit manually because I don't have the job link, nor the resume or cover letter"). `GET /api/review` entries now carry `application_url` (legacy Job first, `JobPosting.canonical_url` fallback) and `artifacts` (existing files probed from `materials_path` across resume_/cover_/cover_letter_ prefixes and .pdf/.docx), rendered as links on every card. Additionally, the review-entry `mark-submitted` route now MATERIALIZES an `Application` row (SUBMITTED + `submitted_at` + score from the entry's breakdown + resume path) when none exists — the overnight plan run creates review entries without Application rows, so manual submissions previously had nothing to track against. Follow-up candidate: create Application rows in `plan_run` itself, mirroring the 2026-07-02 Job Database rework.
- **"I applied manually" action (review queue + paused applications)**: Phase 18 correctly refuses to auto-mark rows SUBMITTED (no external click-submit worker), but left no path for the user's own by-hand submissions — the only way to clear the pile was Discard, which kept those applications out of outcome tracking entirely (email ingestion, follow-ups, and analytics all key off `submitted_at`). New: `POST /api/review/{entry_id}/mark-submitted` (entry pending/approved → submitted + matching Application → SUBMITTED with `submitted_at` + `USER_CONFIRMED_MANUAL_SUBMISSION` state-history breadcrumb, atomic) and `POST /api/applications/{id}/mark-submitted` (`tracking.mark_application_submitted_manually`, also clears matching review entries). "I applied manually" buttons on review kanban cards (pending + approved) and paused-application cards.
- **"Failed to fetch" during material generation (worker succeeded, UI gave up)**: user report — generate-materials showed "Resume: Failed to fetch; Cover Letter: Failed to fetch" while the worker completed and wrote both artifacts. Root causes: (1) `pollTask` in `api.js` had zero fault tolerance — one dropped `GET /api/tasks/{id}` (web event loop momentarily blocked, e.g. by a provider health probe whose `verified_at` timestamp matched the failure second exactly) aborted the whole UI flow; now tolerates up to 12 consecutive poll failures (~90s, growing backoff) before surfacing an error. (2) `OllamaProvider.default_base_url` used `localhost:11434`, violating repo invariant #9 — every probe/generation call paid an IPv6-first connect attempt against the IPv4-only Ollama listener; pinned to `127.0.0.1` (+ test updated).
- **Apply-button stall fix (`/api/jobs/apply`)**: the apply pipeline ran inside the request on the shared event loop; its blocking sections (LLM, DOCX/PDF rendering, Playwright startup) delayed even *finished* jobs' HTTP responses, so applying to several jobs showed every button stuck on "Applying…" until the last one completed. The route now runs each pipeline in its own thread + event loop (`asyncio.to_thread(asyncio.run, ...)`); LLM concurrency stays bounded because the Phase 18.5 gates are `threading.Semaphore`, shared across threads/loops.

- **Experience-years fix (`src/matching/rules.py::_merged_experience_years`)**: `load_applicant_context` summed each work experience's year-span independently — overlapping jobs double-counted (two concurrent 2022–2023 roles = 2 years... counted twice), and year-subtraction miscounted spans (22 months = "1 year", 11 months = "0"). Now parses `YYYY-MM`, merges overlapping intervals, and floors merged months to whole years. On the user's real profile: old 2 → correct 3 YOE. Tests in `test_improvements_2026_07.py::TestMergedExperienceYears`.
- **Profile content**: added AI-adoption discovery / AI implementation-blueprint bullets (tagged `ai`, `solution-design`, `consulting`, ...) and description to the SDS entry across all four profiles — the user's real client-facing AI consulting work was entirely absent from the bullet pool. Verified they rank #1–2 in the SDS entry for AI-role JDs. Deliberately did NOT retitle SDS as a SWE role (user didn't write production code there; unverifiable title = background-check risk).
- **New `ai-solutions` search profile + automation plan** (08:30 UTC, materials via solutions-consultant profile): forward-deployed engineer / applied AI / GenAI solutions keywords. Healthcare implementation keywords (EHR, clinical informatics) added to implementation-consultant; RevOps keywords added to analyst.

### Overnight automation (2026-07-07, later)

Phase 19's "saved-search registry" turned out to already exist as automation plans (`config/automation_plans.yaml` → `automation_plan_schedule_entries()` → Beat). Plan runs verified working (June 30 report: 52 jobs → top 5 → review entries + materials in 82s). What was missing was the stack actually running overnight, plus timing. Changes:

- **Retimed the four automation plans** to 07:30 / 07:45 / 08:00 / 08:15 UTC (2:30–3:15am Central), staggered 15 min apart (inside the 60-min board-cache TTL so all four share one board download). Early start budgets ~3h for serialized local-LLM material generation (worst case 4×5 jobs ≈ 1.5–2.5h at 2–4 min per cover letter through the global LLM concurrency gate). `morning_digest` moved 08:00 → 12:00 UTC (7am Central, after materials finish); `linkedin_cookie_refresh` moved 03:00 → 07:15 UTC so the LinkedIn session is warm just before the plans that need it (at the old 10pm-Central slot the stack was usually down).
- **`Overnight AutoApply.bat`** + Windows Task Scheduler entry "AutoApply Overnight" (2:00 AM daily, `WakeToRun=true`, registered via `Register-ScheduledTask`): wakes the PC, starts Ollama + the full stack headless (`--no-open`), holds the machine awake 4.5h (2:00→6:30am, sized for worst-case material generation) via `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)`, then releases. Skips stack startup if port 8000 already responds.
- **`Stop AutoApply.bat`**: kills the project's `.venv` python processes (web/worker/beat, matched by executable path so nothing else is touched), `docker compose down`, stops Docker Desktop + `wsl --shutdown` (frees the vmmem gigabytes), kills Ollama (frees VRAM).
- **Efficiency**: both launchers now pass `--worker-pool threads --worker-concurrency 2` (the runbook flow) instead of cmd_start's Windows default `solo`, so material generation doesn't serialize behind search/maintenance tasks. ATS board cache TTL is config-driven (`search_cache.board_ttl_minutes`, default 60, was hardcoded 15 min) so the staggered plans share one board download; the Jobs-tab Refresh button (`force_refresh`) remains the fresh-pull escape hatch.
- Beat static-entry contract test updated for `email_ingest` (`tests/test_tasks_beat.py`).

### Feature batch (2026-07-07, continued)

Six job-search-effectiveness features, same session as the scoring fix below.

- **Multi-profile scoring (`src/application/jobs.py::_score_jobs`)**: every search now scores each job against EVERY profile in `data/profile/profiles/`, not just the active one. The best profile wins `match_score` and is recorded in `raw_data.best_profile` / `raw_data.profile_scores` (also top-level in `serialize_job`). Ties prefer the active profile; a profile that fails to load is a warning, not a search failure. JobsView shows a "Best fit: <profile>" chip (hover = all per-profile scores) and passes `best_profile` as the generation `profile_id` so the matching resume variant is used automatically. NOTE: the user's four profiles currently flatten to identical scoring text (same bullets/skills), so scores tie until the variants are differentiated.
- **Embedding similarity (`src/matching/semantic.py`)**: JD↔profile text similarity now uses local Ollama embeddings (`nomic-embed-text`, 768-dim) with cosine rescaled from [0.35, 0.85] to [0,1] (`calibrate_embedding_cosine`), replacing TF term overlap when available. Cached in the `embedding` namespace keyed (ollama, model, base_url, text) — disjoint from the OpenAI `embed_text` keys. Circuit breaker: first failure disables the HTTP path for 120s so a down Ollama costs one timeout, not one per job. `ScoringContext.applicant_vector` embeds the profile once per batch; JD vectors are content-cached so multi-profile scoring embeds each JD once. Config: `matching.embeddings` in settings.yaml; falls back to TF overlap automatically.
- **Ghost-posting age penalty (`src/matching/scorer.py::_compute_quality_multiplier`)**: postings age-penalized ×0.9 >30d, ×0.75 >60d, ×0.6 >90d. Age from `raw_data.first_seen_at` (stashed from `JobPosting.first_seen_at` on both the Job Index cache-hit and fresh-scrape paths in `application/jobs.py`), Greenhouse `first_published`/`updated_at`, or Lever `createdAt` (epoch ms). Unknown age never penalizes.
- **Interview prep packs (`src/generation/prep_pack.py` + `src/application/prep.py`)**: deterministic (no-LLM) one-page markdown mapping the profile's `story_bank` onto a JD — must-have/preferred skills with ✓ marks for ones on the profile, top stories ranked by JD token overlap + `applicable_to` tag boost, likely behavioral questions per theme, questions to ask. Written to `data/output/prep/`. `POST /api/applications/{id}/prep-pack` + per-row button in ApplicationsView; auto-generated (best-effort) when an outcome flips to `interview`.
- **Outcome analytics (`src/application/analytics.py`, `GET /api/analytics/outcomes`)**: response/positive rates for submitted applications bucketed by match-score band and by `best_profile` — answers "does 0.75 predict replies?" and "which resume variant converts?". Dashboard card renders when there's data.
- **Gmail reply ingestion (`src/intake/email_ingest.py`)**: read-only IMAP (app password via `AUTOAPPLY_GMAIL_APP_PASSWORD` env var; `email:` block in settings.yaml), conservative keyword classifier (offer > rejection > interview > OA precedence, since rejections mention "the interview"), company-name matching to submitted applications (names <4 chars must match the sender, not the body), and escalate-only outcome updates (pending→oa→interview→offer; rejected always allowed, never downgrades). Ambiguous messages are reported, not applied. Also computes "no reply in 10+ days" follow-up nudges. Beat: `maintenance.email_ingest` every 6h (no-ops safely when unconfigured); manual: `POST /api/email/ingest` (supports `dry_run`), `GET /api/email/followups`, "Check email replies" button + follow-up banner in ApplicationsView. Interview outcomes from email also trigger prep-pack generation.
- Tests in `tests/test_improvements_2026_07.py` (hermetic — no Postgres/Redis/Ollama/IMAP).

### Maintenance session (2026-07-07)

- **Scoring fix: fulltime jobs no longer auto-disqualified (`src/matching/rules.py`)**: `ApplicantContext.preferred_employment_types` defaulted to `["internship", "coop"]` and `load_applicant_context` never populated it from the profile, so the `employment_type` hard rule failed every `fulltime` job and web search returned `match_score = 0.0` / `disqualified` for all of them — search results appeared unrated. The dataclass default is now `[]` (no preference → rule passes), and `load_applicant_context` reads `preferences.employment_types` from the profile YAML (lowercased; values match `RawJob.employment_type`: internship/fulltime/parttime/contract/coop) for anyone who wants the hard filter back. Verified: fulltime Solutions Consultant JD vs the active profile now scores 0.75 instead of 0.0/disqualified.
- **One-click launcher (`Start AutoApply.bat`)**: starts Ollama if not running (skips with a warning if not on PATH), then delegates to `uv run autoapply start`, which already handles Docker Desktop, Postgres+Redis compose, migrations, Celery worker + beat, the web server, and opening the browser.

### Maintenance session (2026-07-02, continued)

- **Job Database → manual-apply flow rework**: batch generate now creates an `Application` per selected job (materializing a legacy `jobs` row from the Job Index posting+snapshot when needed — `_resolve_legacy_job`) and enqueues `materials.generate` with `application_id`. The worker writes `resume_version`/`cover_letter_version` onto the application and advances early-state applications to `REVIEW_REQUIRED`, so selected jobs land in the Awaiting Review "ready" section with the job's apply link and material downloads — the surface used for applying manually. The previous flow created bare review-queue kanban entries, which carry no application URL and only the resume path (a dead end for manual applying).
- **Duplicate-generation guard**: jobs whose application already has materials are skipped with an "Already prepared" notice (regenerate from the application card instead), and the Job Database view shows a "Prepared" chip via a new `has_application` flag.
- **Cover-letter LLM timeout fix (`src/generation/cover_letter.py`)**: removed the hardcoded `timeout=90`, which was shorter than a local model's cold-load + long-prose generation — nearly every cover letter in worker logs failed with "timed out after 90s" and silently fell back to the generic template. Now uses the configured `llm.timeout` (300s).
- **Mid-sentence bullet truncation fix (`src/generation/fitting.py::_trim_words`)**: the pre-render fitter sliced bullets at the template's `max_words_per_bullet` and stripped punctuation, shipping resumes with bullets ending mid-phrase ("… Piper TTS for spoken"). Trimming now only happens at a clause boundary (sentence end / semicolon / comma / spaced dash) that preserves ≥ half the budget and never orphans inline `**bold**`/`*italic*` markup; when no safe boundary exists the bullet is left whole and the post-render page-fit loop (LLM shorten + weakest-bullet drop, driven by real page counts) handles overflow. Regression tests in `tests/test_fitting_trim.py`.

### Maintenance session (2026-07-01)

Search correctness, speed, and a stored-jobs surface. No schema changes.

- **Region-leak fix (frontend)**: `JobsView.vue` skipped the location filter for *all* jobs whenever the source included LinkedIn, letting unfiltered global ATS-board jobs into "shown". Only LinkedIn-sourced jobs (already geo-filtered server-side) may skip it now.
- **Whole-word location matching**: replaced raw substring matching ("ny" matched "Germany", "us" matched "Australia") with whole-word/phrase matching plus aliases — us/usa/united states/america equivalence, uk expansion, remote synonyms, and a full US state name↔postal-code table. Two-letter state codes match only in the `", XX"` form. Country candidates also match city/state-only locations ("Dallas, TX" matches "united states"). Implemented in `src/application/jobs.py::_matches_locations` and mirrored in `JobsView.vue::matchesLocations`; the two must stay in sync.
- **Deterministic sorting**: `_job_sort_key` (score desc → company → title) replaces `raw_data.get("match_score", 0.0)` sorts that crashed on stored `None` and left unscored ties in scrape order. `/api/jobs/search` now passes `warn_on_missing_profile=True` so "results not scored" surfaces instead of silently rendering unsorted. The Jobs tab gained a user-facing sort control (Best match / Newest / Company / Title) persisted in `jobs-state.js`.
- **ATS scrape speed**: board fetches run concurrently (ThreadPool ≤8) in `src/intake/search.py`, and results are cached in-process for 15 minutes per (ats, slug, parse_jds) with deep copies on write/read (downstream mutates RawJob). `force_refresh` (Jobs-tab Refresh button) bypasses the cache and is now threaded through to the ATS path.
- **Cross-source dedupe**: with source "all", the same posting arriving from both an ATS board and LinkedIn is deduped on (company, title, location); the ATS copy wins (full JD, direct apply URL).
- **Job Database view (`/jobs-db`)**: new `src/application/job_database.py` + `GET /api/jobs/db` (filter persisted `jobs` rows by location/employment type/seniority/source/company/free text, with facets and pagination) and `POST /api/jobs/db/generate-materials` (per selected job: create pending review-queue entry + enqueue `materials.generate`, ≤50 per batch — results surface on the existing Materials kanban and Tasks list). New `JobDatabaseView.vue`, nav entry, `api.js` helpers, and route tests in `tests/test_web_job_database.py`.
- **Docs**: added root `CLAUDE.md` (agent guide: environment, invariants, conventions) and `docs/INFRASTRUCTURE.md` (runtime topology, job stores, cache layers, request/generation flows, operational gotchas).

Follow-up fixes, same session:

- **Duplicate-explosion fix (`src/intake/storage.py`)**: `persist_and_sync_ids` / `upsert_jobs` compared lowercased company names against the DB's original casing (`Job.company.in_({"stripe"})` vs stored "Stripe"), so the existing-row check matched nothing and every search re-inserted the entire board. Fixed with `func.lower(Job.company)`; a one-time cleanup removed 21,676 duplicate rows (24,935 → 3,259), keeping application/review-referenced rows. Follow-up candidate: a unique index on `(source, source_id, lower(company))` via Alembic to enforce this at the schema level.
- **Keywords now narrow ATS results** in `application.jobs.search_jobs`: any keyword phrase must appear in title or description. Previously keywords only fed the LinkedIn scraper and ATS sources returned entire scored boards regardless of the keyword box. The Jobs-tab Keywords field now shows for all sources, is no longer cleared when switching to ATS, and is always part of the fetch signature.
- **Job Database reads both stores**: `list_db_jobs` is now a SQL `UNION ALL` over legacy `jobs` and the Job Index (`job_postings` + latest `job_snapshots`, minus rows already in legacy), which surfaced ~800 previously invisible LinkedIn jobs. `generate_materials_for_db_jobs` resolves ids against either store and binds `job_snapshot_id` on review entries for index postings. Facets gained a company dropdown (top 40 by count).
- **IPv4 pinning (`config/settings.yaml`, `.env`)**: `localhost` resolves IPv6-first on Windows and Docker Desktop's published ports blackhole (never refuse) `::1` connections, so DB/Redis clients without connect timeouts hung forever — this looked like "the test suite hangs". Hosts are pinned to `127.0.0.1`.
- **Test isolation fix (`tests/test_generation.py`)**: `test_resume_trims_bullets_when_rendered_pdf_overflows_page_target` didn't patch `_rewrite_bullet_for_length`, so with Ollama running it made real LLM calls (minutes each) and only exercised its intended fallback path when Ollama was down. Now patched like its sibling tests.
- **UI guidance**: Candidate Locations hint documents coarse board labels ("US", "Ireland Locations") and recommends combining a city chip with "united states"/"remote" to keep country-wide roles.
- **"Unknown passes" for Advanced filters** (backend `_apply_search_filters`/`_matches_numeric_filter` + `JobsView.vue` mirrors): pay, experience-years, employment type, location type, education, and experience level filters now only exclude jobs whose *known* value violates the filter. Previously a job with unparseable pay or an unclassifiable employment type was excluded outright, so a saved profile stacking pay ≥ 90k + full-time + hybrid/remote filtered 361 fetched jobs down to 0 shown. This matches the long-standing convention in `intake/filters.py` (`unknown` passes).
- **"City, ST" location chips**: comma-separated candidates now require every part to match, so "Portland, OR" matches "Portland, Oregon Metropolitan Area" and "Portland, OR (Remote)" but not "Portland, Maine" (applies to Jobs tab and Job Database location filters, both stacks).
- **Working-tree repair**: all 14 `migrations/versions/*.py` files had been deleted (uncommitted) by an earlier session; restored via `git checkout -- migrations/versions/`. Alembic head unchanged (`b8d2f9e15c33`).
- **Beat schedule tests** now ignore `automation:<plan-id>` entries injected from the user's `config/automation_plans.yaml`, asserting only the static contract. Full suite: 1755 passed / 1 environment-dependent failure (`test_filters.py::test_loads_real_config`, user config has no `default` profile).

### Phase 18 — Worker Activation, Reliability, Parallelism, Cleanup (2026-05-20)

Phase 18 converted the Phase 14 task scaffold from audit-visible placeholders into the default execution surface for long-running materials work, and added the operational guardrails needed for local-first automation.

- **18.1 Worker stub closeout**: `materials.generate` now calls the material-generation usecase and writes artifact paths back to review/application records when possible. `jobs.enrich`, `application.prepare`, `maintenance.jd_health_check`, `maintenance.gate_expire_sweep`, `maintenance.linkedin_cookie_refresh`, and `maintenance.cache_eviction` now run real code paths. Unsupported paths (`application.fill`, `maintenance.status_sync`, saved-search fanout/refresh, and final browser click-submit) return explicit `status="not_implemented"` instead of fake success.
- **18.2 Async material APIs + task result**: `POST /api/jobs/generate-material` and `POST /api/applications/{id}/regenerate-material` default to enqueueing `materials.generate` and returning `{task_id, poll_url}`. `tasks.result` JSONB is persisted from Celery postrun and surfaced by `/api/tasks/{id}`; the SPA gained `getTask` / `pollTask` helpers and wraps material-generation calls through them.
- **18.3 DLQ + retry/discard**: added `tasks.last_attempted_at`, `tasks.dead_lettered_at`, `tasks.dlq_reason`, a partial DLQ index, `dead_lettered` task status, retry support for dead-lettered rows, and a discard action for the Tasks UI's "Stuck / failed" tab.
- **18.4 Automatic artifact cleanup**: added `src/maintenance/artifacts.py`, `src/maintenance/atomic.py`, `cleanup_runs`, `cleanup_items`, `applications.deleted_at`, `autoapply cleanup scan/clean/restore/purge-quarantine`, and scheduled cleanup through `maintenance.cache_eviction`. Cleanup builds a DB-derived protected path set, quarantines eligible files under `data/quarantine/<run_id>/`, audits every decision, supports restore, and only purges after the quarantine window.
- **18.5 Strategic parallelism + provider limits**: bullet rewrites, dual-document material generation, and JD requirement parsing now fan out with bounded concurrency. `src/utils/parallelism.py` provides global and per-provider LLM semaphores used by `generate_text`; LinkedIn detail scraping remains serial by design.
- **18.6 Sync fallback retirement**: material-generation endpoints default to async. `AUTOAPPLY_SYNC_MATERIALS=1` remains a dev-only escape hatch and emits a warning when used; the SPA only uses the async path.
- **18.7 Material workflow + branding hardening**: added the AutoApply logo/favicon to the SPA and README; expanded material strategy/template/profile UI handling; fixed generated TXT cover-letter fallback for CLI material generation; made Celery task audit results JSONB-safe before commit; and prevented old `TaskRecord.result` artifact paths from re-protecting application files after soft-delete retention expires.
- **Review follow-up**: fixed idempotency replay to return `TaskRecord.result` instead of the original task payload. Legacy submit entrypoints no longer mark records submitted before a real external ATS submission exists. Codex review follow-ups now cover JSONB-safe task results and cleanup retention semantics. Remaining known follow-ups are final browser click-submit, saved-search registry fanout, and outcome status sync.

### Documentation Sync (2026-05-20)

- Refined the Phase 18 roadmap across project docs: all registered worker
  stubs must be closed out, async material APIs need durable task results,
  DLQ state must be Postgres-backed, parallel LLM work needs global/provider
  limits, sync material fallback is short-lived only, and cleanup is now an
  automatic quarantine/audit system rather than dry-run-only.
- Refined the Phase 19 roadmap: searches still hit upstream every time, but
  A1 tags now bind to `job_snapshots`, A2 score cache keys include
  `profile_version` and `scorer_version`, tag backfill is paginated, pending /
  failed tags fall back to slow scoring, and cross-source canonical dedupe is
  explicitly out of scope.
- Refined the Phase 20 roadmap: user-added sources now start with URL/SSRF
  guards, connectors are separated from source instances, source state is
  explicit, multi-source search is bounded with partial failures, source
  sessions are isolated, and LLM scraper templates are feature-gated constrained
  DSL rather than arbitrary Playwright code.

### Documentation Sync (2026-05-19)

- Refreshed README-adjacent project docs for the Phase 17.9 baseline:
  project-management verification, long-form English/Chinese plans,
  phase history, and deployment provider guidance now reflect the
  expanded LLM provider surface and Phase 18/19 ordering.

### Phase 17.9 — LLM Provider Expansion (2026-05-19)

LLM hardening between Phase 17.8 (Document Library) and Phase 18
(Worker Activation). Adds first-class support for more upstream
providers, gives each provider a curated model catalog, lets users
pick a model per provider from the UI, and introduces an optional
cheap-model tier for extraction-style work.

**17.9.1** — Extract `OpenAICompatibleProvider` from
`src/providers/openai.py` so any vendor speaking `Bearer` + `/v1/chat/completions`
is a ~10-line subclass. Add `ModelInfo` dataclass and
`KNOWN_MODELS: tuple[ModelInfo, ...]` on `LLMProvider`. OpenAI,
Anthropic, and Gemini ship curated catalogs for May 2026 (Anthropic
and Gemini keep their bespoke `generate()` paths -- Messages API and
v1beta REST aren't OpenAI-shaped).

**17.9.2** — Add seven new OpenAI-compatible providers: DeepSeek,
Moonshot/Kimi, Qwen (DashScope OpenAI-compat endpoint), xAI Grok,
Groq, Mistral, and OpenRouter. Each ships a curated `KNOWN_MODELS`.
OpenRouter seeds the 10 most popular routes; the rest are discoverable
via the model catalog API in 17.9.4.

**17.9.3** — `OllamaProvider` for local self-hosted models. Probes
`/api/tags` (native Ollama API) and exposes `list_local_models()` for
the 17.9.4 catalog. Adds an `allow_empty_key` class flag on
`ApiKeyProvider`: when set, `connect()` / `get_api_key()` / the
application + CLI layers all accept an empty secret. Credential row
is still persisted (carries the custom `base_url` and the `verified_at`
breadcrumb).

**17.9.4** — Model catalog API + Connect dialog picker.
`GET /api/providers/{id}/models` returns `KNOWN_MODELS` merged with
the live runtime catalog where available (Ollama today). Connect
dialog replaces the freeform model `<Input>` with a native `<select>`
over the catalog, with a `Custom...` sentinel that swaps in a free-
text input for ids shipping after our curated snapshot. Base URL
moves into a collapsible "Advanced" panel so the default view stays
a one-glance form. `allow_empty_key` is surfaced in `public_view()`
so the dialog labels the key field "(optional)" and skips its own
"key required" guard for Ollama-style providers.

**17.9.5** — Small-model tier. `generate_text` / `generate_json` gain
a `tier: "primary" | "small"` parameter. With `llm.small_provider` and
/ or `llm.small_model` configured in settings.yaml, `tier="small"`
routes via that cheaper path; the primary chain still serves as the
safety net on transient failure. `LLMProvider.generate()` picks up an
optional `model: str | None = None` keyword threaded through
`_call_provider` -> `_dispatch_via_registry`. Cache fingerprint folds
in the effective model so primary and small-tier requests don't
collide on cached entries. Applied at two pure-extraction call sites:
`src/intake/jd_parser.py` and `src/memory/resume_importer.py`.
Creative paths (cover letter, resume rewrite, QA responder)
intentionally stay on the primary tier.

**17.9.6** — User-defined custom providers. `config/settings.yaml`
`llm.custom_providers: [...]` synthesises an `OpenAICompatibleProvider`
subclass per entry at registry-init time. Lets users wire in third-
party OpenAI-compat proxies, private vLLM / LM Studio endpoints, or
not-yet-bundled upstreams without code changes. Validation skips
malformed entries with a `logger.warning` rather than crashing
startup; builtin ids always win on collision.

### Roadmap Update: Task Queue + Materials Generation v2

- Re-planned Phase 14 from a scheduler-only phase into **Task Queue +
  Scheduled Work**: Redis is queue transport, Postgres is durable task
  state, workers own ack/nack/retry/heartbeat/concurrency, and agents
  run inside one bounded task with structured outcomes.
- Re-planned Phase 15 from cover-letter-only work into **Resume & Cover
  Letter Generation v2**: original editable resumes get a patch mode;
  newly generated resumes become LaTeX-first template packages with
  manifests/adapters; cover letters stay snapshot-bound and
  fact-checked.
- Added architecture decisions D023 and D024 to pin the queue/agent
  boundary and the materials-generation template contract.

### Roadmap v3.1 calibration (2026-05-14)

- **Phase 14 is now Celery 5.x-based** (D025). The originally-planned
  self-built task model + queue transport + worker runtime is dropped
  in favor of Celery 5.x + Redis broker + Celery Beat. AutoApply
  layers a thin AutoApplyTask base class on top for agent boundary +
  HITL + trace + tenant context. APScheduler is retired (D021 is
  superseded by D025).
- **Phase 13.9** (D026) lands a one-shot `tenant_id` retrofit
  migration on every legacy table before Phase 14 begins so D020's
  discipline becomes schema-enforced rather than just review-enforced.
- **HITL gate backend** moves from the single-process file backend in
  `src/agent/gate/queue.py` to a Postgres `gate_queue` table folded
  into Phase 14, so Phase 17's review queue does not have to reinvent
  it.
- **Phase 15.3 LaTeX scope** clarified — `src/documents/latex_engine.py`
  already exists, so Phase 15 is "template package spec + manifest +
  adapter conventions on top of an existing engine," not "build LaTeX
  from scratch."
- **Then-current multi-tenancy plan call-out**: the auth middleware,
  Redis namespace refactor, and credential store work (now Phase 21 after
  the later roadmap reorder) are honestly net-new construction rather than
  "activate existing work."

### Phase 13.9: `tenant_id` retrofit migration

Per D026: lands one alembic migration giving every legacy table from
Phase 11 and earlier a `tenant_id TEXT NOT NULL DEFAULT 'default'`
column with backfill, so D020's discipline ("every new table from
Phase 12 onward carries `tenant_id`") becomes a schema-level guarantee
before Phase 14 begins.

- **Migration `d8a5c2f1e9b3`** adds `tenant_id` to `jobs`,
  `applications`, `applicant_profile`, `bullet_pool`, `qa_bank` and
  backfills existing rows. ORM models in `src/core/models.py` add the
  field; `TENANT_DEFAULT` is moved to module-top for reuse. Existing
  query paths are intentionally not forced to filter — they keep
  today's global-read behavior — but every new Phase 14+ code path
  must thread an explicit tenant context. The "default tenant"
  fallback persists until Phase 19 auth middleware + RLS take over.
- 7 new tests in `test_phase_13_9_tenant_id_retrofit.py`: per-table
  invariants + a `Base.metadata` catch-all that fails if any future
  table lands without the column + migration-file metadata check.
- Verification: 1022 passed, 1 skipped on dev (`pytest -q`); `ruff
  check` clean; `codex review --uncommitted` returned no findings;
  `alembic upgrade head` moved dev DB to revision `d8a5c2f1e9b3`.
  (commits `ae46a39` + roadmap docs `0c8ec95`)

### Phase 14: Task Queue + Scheduled Work (Celery 5.x)

Production-grade task execution substrate built on Celery 5.x.
AutoApply layers an "agent boundary + HITL + trace + tenant context"
wrapper on top; D023's principle ("queue owns execution reliability;
agents own bounded decisions") is preserved.

- **14.1 Celery wiring + skeleton** -- `celery_app = Celery(
  "autoapply", broker=REDIS_URL, backend=REDIS_URL)` with the D025
  reliability commitments: `task_acks_late=True`,
  `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`
  (long-task model — do not prefetch). Four queues (`search`,
  `materials`, `application`, `maintenance`) with a router that maps
  task-name prefix → queue; unknown prefixes fall back to
  `maintenance`. JSON-only serialization. `redbeat_redis_url` +
  namespace pre-configured for 14.5/14.10. +15 tests. (commit
  `83de0db`)

- **14.2 Durable `tasks` audit table** -- migration `e1b4f72c8a05`.
  Postgres is the source of truth; Celery's result backend is
  transient. Lifecycle signals (`task_prerun` / `task_postrun` /
  `task_failure` / `task_retry` / `task_revoked`) plumb state into
  this table; handlers tolerate missing rows and swallow exceptions
  so an audit bug never poisons a worker. +11 tests. (commit
  `259c892`)

- **14.3 `AutoApplyTask` base class** + tenant ContextVar in
  `src/tasks/context.py`. `call_agent(fn, *args, **kwargs)` runs a
  bounded agent and normalises any return shape into `AgentDispatch`
  with one of five outcomes (`success` / `failed_retryable` /
  `failed_terminal` / `needs_human` / `needs_followup_task`).
  `short_circuit_if_already_succeeded` handles idempotency-key
  replay. Static `enqueue(celery_task, session, EnqueueSpec)`
  atomically writes the audit row and dispatches. +17 tests. (commit
  `8b5fb4c`)

- **14.4 HITL gate moved to Postgres `gate_queue`** -- migration
  `f2c5d83a91b6`. Replaces the single-process file backend in
  `src/agent/gate/queue.py` (kept as compat for one release per
  D026). `open_request` flips the linked task to `waiting_human` and
  the worker is released immediately — no thread parking.
  Double-approve returns the existing decision (UI double-click is
  not a 409); approve-then-reject is a real conflict. +10 tests.
  (commit `880887a`)

- **14.5 Celery Beat schedule** retires APScheduler. Six entries in
  `src/tasks/beat.py`: `daily_search` (02:00 UTC) → `search` queue;
  `jd_health_check` (hourly), `application_status_sync` (every 6h @
  :15), `linkedin_cookie_refresh` (03:00 UTC daily), `cache_eviction`
  (hourly @ :30), `gate_expire_sweep` (every 15min) → `maintenance`.
  `install(celery_app)` wires `RedBeatScheduler` for multi-instance
  leader election. +7 tests. (commit `12e3b21`)

- **14.6 12 concrete task kinds** in `src/tasks/tasks.py`. Each is an
  `AutoApplyTask` wrapper with a Pydantic payload model; malformed
  payload raises `TypeError` which Celery treats as terminal (no
  retry storm). Worker-driven: `search.refresh`, `jobs.enrich`,
  `materials.generate`, `application.{prepare,fill,submit}`.
  Beat-driven: `search.daily_fanout`, `maintenance.{status_sync,
  jd_health_check,linkedin_cookie_refresh,cache_eviction,
  gate_expire_sweep}`. Bodies log-and-return-stub today; Phase 15 /
  17 swap bodies without changing task name or payload contract.
  +32 tests under Celery eager mode. (commit `f01cab5`)

- **14.7 CLI command groups** -- `autoapply worker -Q queues -c N`
  validates `-Q` against the four-queue allowlist (a typo errors
  immediately); `--check` prints the resolved Celery invocation
  without starting anything. `autoapply beat` selects
  `RedBeatScheduler`. `autoapply tasks list/inspect/retry/cancel/
  kinds` reads the 14.2 audit table; retry refuses non-failed/
  cancelled rows; cancel refuses non-queued rows. `autoapply
  schedule list/run-now` reads/dispatches Beat entries. +10 tests.
  (commit `ef59570`)

- **14.8 Web JSON API + minimal SPA view** -- `src/web/routes/
  tasks.py` adds `GET/POST /api/tasks`, `GET /api/tasks/{id}`,
  `POST /api/tasks/{id}/{cancel,retry}`, `GET /api/schedule`, `POST
  /api/schedule/{name}/run-now`, `GET /api/gate?status=...`, `GET
  /api/gate/{id}`, `POST /api/gate/{id}/{approve,reject}`. Every
  route scoped by `x-autoapply-tenant`; cross-tenant queries return
  404. `frontend/src/views/TasksView.vue` adds the `/tasks` page
  with three sections (gate awaiting human, recent tasks, Beat
  schedule). Frontend rebuilt clean (vite 5.73s, 125kB gzip). +14
  tests. (commit `edb8661`)

- **14.9 Task-shape trace integration** -- `src/tasks/trace.py`
  hooks `task_prerun` / `task_postrun` / `task_failure` /
  `task_retry` to write a `TraceRecord` per attempt and stamp the
  `trace_id` onto the audit row at prerun. Child tasks dispatched
  with `x-autoapply-parent-trace` inherit `parent_trace_id` for the
  viewer chain. Persistence is best-effort: a failed `save()` logs
  and swallows. +8 tests. (commit `b7ecb57`)

- **14.10 Postgres advisory-lock backstop** -- `src/tasks/locks.py`
  adds `with advisory_lock(session, key)`, a non-blocking
  `pg_try_advisory_xact_lock` wrapper for deployment-wide critical
  sections. Auto-releases on commit/rollback/connection drop. Key
  is a SHA256-derived 63-bit signed int. +4 tests. (commit `707d94e`)

- **codex review fixes** (two rounds, P1 + P2):
  - **P1 cancel-revoke**: cancel routes (web + CLI) now call
    `celery_app.control.revoke(celery_task_id, terminate=False)`
    before flipping the row, so a worker cannot still claim the
    queued message.
  - **P2 universal audit**: a new `before_task_publish_handler` in
    `src/tasks/audit.py` writes a `queued` row for every Celery
    dispatch (Beat ticks, raw `send_task`, retries) so they all show
    up in `/api/tasks` and `autoapply tasks list`. `AutoApplyTask.
    enqueue` opts out via the `AUDIT_OK_HEADER` because it has
    already written the row with `idempotency_key` +
    `parent_task_id`.
  - **P2 cancelled-terminal**: all four lifecycle handlers
    (`prerun` / `postrun` / `failure` / `retry`) respect `cancelled`
    as terminal so the audit row preserves operator intent even in
    the revoke-vs-claim race.
  - 11 new tests in `test_tasks_codex_review_fixes.py`. (commit
    `3de7084`)

Verification: 1161 passed, 1 skipped on `feat/phase-14`; `ruff check`
clean; frontend build clean; migrations `e1b4f72c8a05` +
`f2c5d83a91b6` applied; `codex review` returned no findings on second
pass.

### Phase 15: Resume & Cover Letter Generation v2

Rebuilds materials generation around two explicit resume modes
(D024): patching the user's original source when possible, and
LaTeX-first generation when creating a new resume from a template.
Cover-letter generation moves through a bounded agent with a
fact-drift post-guard and a five-tier deterministic fallback ladder.
HITL gates only fire for persistent grounding mutations -- one-shot
generation never blocks.

- **15.1 Source-resume table** -- migration `a3b9d52e7c41`;
  `source_resumes` (tenant_id required per D026, source_type CHECK
  IN docx/latex/pdf, editable BOOL, checksum SHA256 unique per
  tenant, storage_path project-relative, extracted_structure JSONB,
  size_bytes, notes). `src/generation/source_resume.py` ingests files
  under `data/source_resumes/<tenant>/<checksum><ext>`, dedupes,
  extracts shallow structure per type (DOCX paragraphs, LaTeX
  sections, PDF headings via pymupdf), rejects traversal in
  resolve_storage_path. +18 tests. (commit `4e95e98`)

- **15.2 DOCX patch mode** -- `src/generation/docx_patch.py` mutates
  runs in place to preserve font/size/bold/italic + named styles.
  Operations: summary, skills, bullet (List Bullet swap with surplus
  appended via XML clone + deficit blanked), section_drop (only
  blanks IR-empty sections; populated IR sections always kept).
  Top-level vs sub-heading distinction so bullets nested under
  Heading 2 job entries are reachable. PatchFallback exception
  signals route-to-template per D024. +9 tests. (commit `697bd3d`)

- **15.3 LaTeX template package spec** -- TemplateManifest grows an
  optional `latex: LatexConfig` sub-model. LatexFieldMapping declares
  IR-field -> LaTeX command + arity. `src/documents/latex_manifest.py`
  exposes the shared `escape_latex` / `resolve_field` /
  `render_command` / `validate_assets` / `validate_field_coverage`
  helpers. +37 tests. (commit `ef81afe`)

- **15.4 Manifest-adapter LaTeX renderer** --
  `src/documents/latex_renderer.py` renders user-uploaded LaTeX
  with custom commands via the field_mappings table. Templates
  declare `{{resume.commands}}`; missing fields skip their command
  (no `\cmd{}` rendering visibly empty). `compile_via_manifest`
  honors `compile_engine` and preserves asset subdirectories.
  +13 tests. (commit `57de801`)

- **15.5 Materials router** -- `src/generation/materials_router.py`
  dispatches `patch_existing` vs `generate_from_template`. Every
  outcome carries MaterialsBindings (job_snapshot_id,
  source_resume_id, template_package_id, profile_version, trace_id,
  tenant_id) for Phase 17 review queue + trace viewer audit binding.
  SourceResumeView keeps the router decoupled from SQLAlchemy.
  +15 tests. (commit `e86f15d`)

- **15.6 `jd_lookup` agent tool** -- `src/agent/tools/jd.py`
  read-only dotted-path access to a bound JobSnapshot. Empty path
  returns a section index; nested paths drill into JSONB with
  `_count`/keys summaries; missing paths report available keys.
  +18 tests. (commit `95c8efb`)

- **15.7 AgentCoverLetter + fact-drift post-guard** --
  `src/generation/fact_drift.py` (number drift blocking with
  10k<->10000 normalization; entity drift warning; length sanity)
  and `src/generation/agent_cover_letter.py` orchestrator with
  five-tier fallback ladder (deterministic_only / agent_ok /
  agent_drift_fallback / agent_error_fallback). Bounded-agent loop
  itself is wrapped by AutoApplyTask.call_agent in the materials
  task body. +21 tests. (commit `983a5a5`)

- **15.8 Template adapter assistant** -- `src/documents/template_adapter.py`
  scans `\newcommand` declarations + `\foo{...}` usages, matches
  against curated conventional name table, warns on unmatched,
  errors on missing `{{resume.commands}}`, runs sample render.
  finalize_proposal gates persistence on sample_render_ok + no
  error notes + assets validating. +15 tests. (commit `98eacad`)

- **15.9 Eval suites** -- three new suites
  (materials_docx_patch / materials_latex_template / cover_letter)
  with JSON fixtures + runners in `src/agent/eval/runner.py`. Two
  new scorer types (json_field_equals, json_field_contains) walk
  dotted paths into parsed envelopes. 7 fixtures + 7 verification
  tests. (commit `488b23d`)

- **15.10 HITL gate triggers** -- `src/generation/gate_triggers.py`.
  is_gateworthy(kind) returns True only for persistent grounding
  mutations (bullet_pool_mutation / story_bank_mutation /
  template_manifest_persist); one-shot generation never gates.
  propose_* helpers open Phase 14.4 gate_queue rows with
  operator-friendly summaries; find_pending_for_task lists blocking
  gates. +14 tests. (commit `439d2d7`)

- **codex review fixes** (one round, P2): legacy LaTeX templates
  without a Phase 15.3 latex block now fall back to the existing
  placeholder renderer in `latex_engine` instead of returning
  decision='unsupported'; PDF source-resume ingest captures
  `len(doc)` inside the pymupdf `with` block so page_count survives
  close; `compile_via_manifest` preserves manifest asset
  subdirectories (`images/logo.png` -> `workdir/images/logo.png`,
  not flattened). 5 new tests; existing 'unsupported' assertion
  updated. (commit `9b813a3`)

Verification: 1332 passed, 1 skipped on `feat/phase-15`; `ruff
check` clean; alembic upgraded dev DB to revision `a3b9d52e7c41`;
`codex review` returned no findings on second pass.

### Phase 16: Filter Agent + Explainability

Not a replacement for the deterministic scorer -- an explainability
layer on top, plus a borderline-band edge-case agent. The hard rules
stay binary pass/fail (visa, US auth, experience, education,
employment type, spam). The agent only fires when the deterministic
score lands in the genuinely-ambiguous middle, and even then it can
only flag the job for human review -- it never overrides hard rules
and it never auto-submits.

- **16.1 `RuleVerdict` schema evolution** -- `RuleResult` grows
  `rule_id` (machine-readable, defaults to `rule_name` when omitted
  so old call sites still emit something useful), `verdict`
  (`"pass"` / `"fail"` / `"warn"` -- hard rules only emit pass/fail;
  `"warn"` reserved for 16.2 agent commentary), and `evidence_excerpt`
  (a bounded JD snippet, ~200 chars with ~80 chars of context on each
  side of the trigger phrase, whitespace collapsed, ellipsis on
  truncation, or a structured marker like `"employment_type=fulltime"`
  when the rule fires on a struct field rather than text, or `None`
  when no JD evidence applies). Each rule has curated regex patterns
  that prefer specific phrases (`"no visa sponsorship"`, `"5+ years
  of experience"`, `"PhD"`) and fall back to broader matches.
  `ScoreBreakdown` gains `job_snapshot_id` (Phase 13 binding) and
  `disqualify_results: list[RuleResult]` alongside the legacy
  `disqualify_reasons: list[str]`. `score_job(job, ctx,
  job_snapshot_id=...)` + `score_jobs(snapshot_ids={...})`. Both
  `RuleResult` and `ScoreBreakdown` gain `to_dict()` so the trace
  store + 16.3 popover payload + Phase 14 task audit row share one
  wire shape. The aggregate `RuleVerdict.fail_reasons: list[str]`
  is preserved unchanged so existing tests pass without modification.
  +26 tests. (commit `203becb`)

- **16.2 Edge-case agent + `score_breakdown` tool** --
  `src/agent/tools/score_breakdown.py` is bound to one
  `ScoreBreakdown` at construction time (audit binding matches
  `jd_lookup` from 15.6); paths `""` (summary with `rule_ids` +
  `fail_rule_ids` + `n_fail` counters), scalar (`final_score` /
  `skill_overlap` / ...), `rules` (list), `rules.<rule_id>` (one).
  Unknown paths return helpful observations with `is_error=False` so
  the agent self-corrects. `src/matching/edge_case_agent.py` ships
  `EdgeCaseAgent` -- fires only when `0.4 <= final_score <= 0.6`
  AND the job isn't hard-rule disqualified. Returns
  `EdgeCaseDecision` with one of four `kind` values: `not_invoked`
  (out-of-band / disqualified / `use_agent=False` / no `llm_fn`),
  `agent_ok` (parsed JSON), `agent_error` (`llm_fn` raised),
  `agent_malformed` (JSON missing / wrong shape / verdict not in
  `{surface, reject, abstain}`). Confidence clamped to `[0, 1]`.
  Trailing-JSON-after-thinking-text parsing supported. Hard rules
  NEVER overridden -- the agent's scope is score-band ambiguity
  only. D023: orchestrator is sync; Phase 17's `plan_run` task
  body will wrap `run()` inside `AutoApplyTask.call_agent`. +27
  tests. (commit `bbc13b9`)

- **16.3 "Why was this filtered?" UI** --
  `src/application/matching.py` adds `explain_job()` that strips
  `serialize_job()` flat fields before coercing into `RawJob`,
  threads `raw_data.job_snapshot_id` into the breakdown when
  present, returns `{ok, score_breakdown, warnings}`.
  `POST /api/matching/explain` route is the thin wrapper.
  `_score_jobs` + `_select_batch_jobs` now stash
  `ScoreBreakdown.to_dict()` into `job.raw_data['score_breakdown']`
  so the popover renders inline without a round-trip.
  `frontend/src/views/JobsView.vue` adds an Info chip-button next
  to the existing "Review" badge on disqualified job cards; the
  Dialog popover renders `final_score` chip, `job_snapshot_id` chip
  (truncated to 8 chars), per-rule cards with `rule_name` / verdict
  chip / reason / `evidence_excerpt` block; falls back to
  `api.matchingExplain(job)` when the inline breakdown is absent
  (legacy cached results). `frontend/src/lib/api.js` gains
  `matchingExplain(job)`. +10 tests including a UI-contract pin
  that asserts every key the Vue template reads is present in
  `ScoreBreakdown.to_dict()`. (commit `c57d108`)

- **16.4 `filter_borderline` eval suite** -- 10 JSON fixtures
  covering the full decision matrix: high-skill + low-keyword
  surface (false negative driven by short JD); borderline-but-
  wrong-role reject (Marketing Analyst with incidental shared
  keyword); quality-multiplier-drag surface (decent components
  dragged into band by 0.5 multiplier); hard-rule disqualified
  short-circuit (verifies agent never overrides hard rules even
  when the stubbed `llm_output` says "surface"); below-band
  short-circuit; above-band short-circuit as surface; malformed
  output fall-back; `llm_fn` raises fall-back; abstain on thin
  signal; invalid verdict literal fall-back. `_filter_borderline_runner`
  in `src/agent/eval/runner.py` builds a `ScoreBreakdown` from the
  fixture's `breakdown` dict (with optional `rules` list of
  `RuleResult`-shaped entries), instantiates `EdgeCaseAgent` with a
  stub `llm_fn` (returns fixture's `llm_output` verbatim; raises on
  `__raise__` sentinel; stays `None` when omitted), emits the
  decision as a JSON envelope so the existing `json_field_equals` /
  `json_field_contains` scorers can assert against it. The plan's
  "agent decision matches human >= 70%" bar is a Phase 17 concern
  (real LLM, real cost budget); this suite is the deterministic
  offline harness it will measure against. +4 tests including a
  coverage assertion that the 10 fixtures collectively touch every
  `EdgeCaseDecisionKind` AND every `EdgeCaseVerdict`. (commit
  `9198a3b`)

- **codex review fixes** (one round, P2): removed 33 committed
  runtime trace artifacts under `data/agent_traces/` that the
  existing `TraceStore` + `/api/agent/traces` viewer was serving as
  if they were real local history on fresh checkouts. Path now in
  `.gitignore`. (commit `5702da7`)

Verification: 1398 passed, 1 skipped on `feat/phase-16`;
`ruff check` clean; frontend build clean (vite 6.00s, 126kB gzip JS).

### Phase 17.8: Material Strategy & Document Library

User-curated document library + per-doc-type generation strategy +
plan-level overrides + paused-review "Replace materials" surface.
Closes the gap where the user could neither see what resumes/cover
letters AutoApply had on file nor steer how new drafts get made.

Implementation highlights:

- **17.8.1 user_documents table** — `migrations/.../e7c3a5b91f48_phase_17_8_user_documents.py`,
  `src/core/models.py:UserDocument`. Per-tenant, deduped by
  `(tenant_id, document_type, checksum)`. Storage at
  `data/user_documents/<tenant>/<document_type>/<checksum><suffix>`.
  Origin enum: `uploaded` / `profile_import` / `generated_promoted`
  with optional `source_application_id` provenance. `src/documents/
  user_documents.py` owns ingest/list/delete/path resolution + a
  `to_source_resume_view()` adapter so the existing Phase 15.5
  materials router doesn't need to learn a new type.
- **17.8.1 documents API** — `/api/documents` (GET list, POST upload,
  PATCH rename, DELETE, GET `/{id}/download`), `/api/documents/promote`
  (copy a generated artifact into the library with provenance back to
  the application that produced it).
- **17.8.1 profile-creation hook** — `import_resume_file()` now stashes
  the original bytes in `user_documents` as `origin='profile_import'`
  before parsing. New `import_resume_from_library()` +
  `POST /api/profile/from-library` let a user seed a profile from a
  doc already in the library without re-uploading.
- **17.8.2 material_defaults.yaml** — `src/application/material_defaults.py`
  + `GET/PUT /api/settings/material-defaults`. Per-doc-type
  `{strategy, default_template_id, default_document_id}` with a
  `resolve_material_choice()` cascade (override → saved default →
  system default). `/api/jobs/generate-material` accepts per-call
  `strategy` + `source_document_id`. When `patch_existing` lands on a
  DOCX library doc, `patch_resume_docx` runs after IR generation and
  swaps the artifact path; failure modes downgrade to regenerate and
  surface `strategy_notes` back to the UI.
- **17.8.3 AutomationPlan overrides** —
  `src/application/automation_plans.py:_normalize_plan` gains
  `resume_strategy / resume_template_id / resume_source_document_id`
  and the same for cover letters. Empty strings inherit the user's
  Settings default. `run_plan` forwards these to the
  `materials.generate` payload; `MaterialsGeneratePayload` widened to
  keep them through Celery boundary.
- **17.8.4 Paused-review actions** —
  `src/application/regenerate_materials.py` +
  `POST /api/applications/{id}/regenerate-material`. The kanban's
  paused card grows a Replace materials dialog (material × strategy
  × template-or-library-doc) plus Save-to-library buttons on every
  downloadable artifact (`/review` and `/applications`). Promotion
  re-enters the same `user_documents` ingest path with
  `origin='generated_promoted'` so the artifact remains in its
  Application home AND in the library.

UI: `/materials` got a tab strip (`Generate` / `Library` / `Templates`)
with a new `MaterialsLibraryView`; Settings got the
"Default material strategy" card; Plans form got a collapsible
"Materials (override Settings defaults)" section; Profile create
gained a third mode "Pick From Library".

Decision: generated outputs do NOT auto-populate the library
(Option C). The library stays user-curated; every generation
artifact ships with a single-click "Save to library" affordance so
nothing is lost. Rationale: the library's value is a short list of
intentional bases (3-10 entries), not a flood of one-off variants.

Verification: `vite build` clean; `uv run python` smoke-tests of the
new routes pass; alembic migration head moves from `c9e1f3a7b8d4` to
`e7c3a5b91f48`.

### Phase 17: Plan Run Loop + Review Queue

Integration phase for scheduled and user-defined application batches.
Threads Phase 14 (task queue + scheduler) + Phase 13 (job index +
freshness) + Phase 12 (cache) + Phase 15/16 (agents) into the
end-to-end review flow. Submit actions remain explicitly gated.

Implementation highlights:

- **17.1 plan_run orchestrator** -- `src/orchestration/plan_run.py`.
  Async `run_plan(...)` dependency-injected for testability. Flow:
  search → score → top-N → persist review_queue rows + enqueue
  materials.generate + application.prepare. Pause sentinel
  short-circuits BEFORE search. Returns a JSON-serializable
  `PlanRunReport` with run_id, status, per-stage counts,
  borderline counter, materials/application/review entry id lists,
  errors, cost. Celery task wrapper `orchestration.plan_run` +
  Beat entry for scheduled plan runs. CLI: `autoapply plan-runs run/enqueue/status`.
  +33 tests. (commits `771b6da`, `2d694e9`, `fe11907`)

- **17.2 review_queue model + state machine** -- migration
  `b7d9a1e4f3c2` + `c9e1f3a7b8d4` (partial unique on pending).
  `review_queue` table with denormalised company/title (kanban
  renders without joining jobs), `job_id` / `job_snapshot_id`
  intentionally NOT FK so rows survive retention sweeps, JSONB
  `score_breakdown` snapshot for the popover. State machine:
  `pending → approved → submitted | rejected | stale`;
  `approved → rejected | stale`; `stale → pending | rejected`;
  `submitted`/`rejected` terminal. `src/application/review.py` use
  cases: `create_entry` (idempotent on pending), `approve`,
  `reject`, `mark_submitted`, `mark_stale`, `refresh_stale`,
  `list_entries`, `bulk_approve`, `bulk_reject`,
  `bulk_reject_by_filter`. +30 tests. (commits `1fe3960`, `62c4314`)

- **17.3 + 17.4 /review kanban + bulk ops** -- `src/web/routes/review.py`
  exposes GET (list + detail) + single-item POST (approve / reject
  / refresh / submit) + bulk POST (approve / reject /
  reject-by-filter). Tenant-isolated (cross-tenant → 404).
  `InvalidTransitionError → 409`, `LookupError → 404`. Bulk
  envelope `{succeeded, failed}` so the UI renders "8 of 12
  approved -- 4 failed". `frontend/src/views/ReviewQueueView.vue`
  four-column kanban; stale rows live in the Pending column with
  Refresh (Approve hidden for stale -- codex round-2 P2 fix);
  Approved column has Submit + Reject; multi-select + bulk action
  card + by-filter card. +16 route tests. (commit `46e8834`)

- **17.5 Pre-submit hard gate** -- `src/review/pre_submit_gate.py`.
  Runs `should_refresh(posting, "before_submit")` (6h budget) AND
  compares `entry.job_snapshot_id` vs `posting.latest_snapshot_id`
  (codex round-3 P1 catches the case where the posting was
  re-scraped after materials were generated). Four
  `PreSubmitAction` values: `allow`, `refresh` (stale snapshot
  or snapshot mismatch -- flips to stale), `expired` (posting
  expired/archived -- flips to rejected), `missing_binding` (not
  approved / no job_id / posting purged). Route `POST
  /api/review/{id}/submit` wraps the flow. +18 tests. (commits
  `4956f5c`, `62c4314`)

- **17.6 Morning digest at 08:00** -- `src/orchestration/digest.py`.
  Aggregates per-run JSON reports under `data/plan_runs/<ts>-
  <run_id>.json` (gitignored; filename-prefix windowing) +
  `count(*)` over review_queue grouped by status. Returns
  `DigestPayload` with headline + per-status review queue chips +
  windowed totals + errors/paused-runs counters. Beat task
  `notifications.morning_digest` at 08:00 UTC. `GET /api/digest`.
  Dashboard banner renders inline above the KPI cards. +17 tests.
  (commit `b005fcb`)

- **17.7 Kill switch** -- `autoapply pause-plan-runs
  [--clear-pending]` + `autoapply resume-plan-runs`. Pause sentinel
  `data/plan_runs_paused` is checked by `run_plan` BEFORE search.
  `--clear-pending` bulk-rejects pending review_queue rows with
  reason="paused for vacation"; approved/submitted/rejected/stale
  rows survive. +2 tests. (commit `208db10`)

- **codex review fixes** (three rounds, all P1/P2 folded in):
  * Round 1: dict-to-RawJob coercion in scoring path
    (production search returned serialised dicts but scorer
    needed RawJob); orchestrator persists review_queue rows
    directly (the `application.prepare` stub never would have);
    `top_n <= 0` now selects none, not all. (commit `2d694e9`)
  * Round 2: review entries were bound to `RawJob.id` not
    `JobPosting.id` so pre-submit gate always returned
    `missing_binding` at submit time -- added DB lookup +
    in-place breakdown patching via
    `_resolve_and_patch_posting_ids`; stale-row Approve button
    hidden (always 409'd). (commit `fe11907`)
  * Round 3: pre-submit gate compares snapshot ids;
    `/api/review/{id}/refresh` now enqueues `jobs.enrich` +
    `materials.generate` (was only flipping status); LinkedIn
    description-only matches now get the redirect-enrichment
    pass too (apply URL was never resolved); `review_queue`
    UNIQUE narrowed to a PostgreSQL partial unique index on
    `status='pending'` so the same snapshot can re-pass through
    the lifecycle in later plan runs. (commit `62c4314`)

Verification: 1530 passed, 1 skipped on `feat/phase-17`;
`ruff check` clean; frontend build clean (vite 6.29s, 129kB gzip
JS); alembic head at `c9e1f3a7b8d4`.

### Phase 13: Job Index & Freshness Engine

Replaces the file-backed ``src/intake/search_cache.py`` with a proper
**Job Intelligence Database**: typed entities, content-hashed
snapshots, search-query cache, freshness state machine, and audit
binding from generated materials back to the exact JD snapshot they
were produced from. Foundation for Phase 14 / 15 / 17.

- **13.1 Job Index schema** -- alembic migration ``c7d3a91b4e2f``
  adds ``job_postings``, ``job_snapshots`` (unique by
  `(posting_id, content_hash)`), ``search_queries`` (unique by
  `(tenant_id, source, normalized_key)`), ``search_results``
  (CASCADE on parent delete), ``refresh_tasks`` (composite index on
  `(tenant_id, status, priority, scheduled_for)`).
  ``applications.job_snapshot_id`` FK + index added for audit
  binding. ``latest_snapshot_id`` on the posting created with
  ``use_alter`` to break the bootstrap cycle. Every new table carries
  ``tenant_id`` with server_default 'default' per D020. ORM models
  in ``src/core/models.py`` mirror the migration's uniques + indexes
  via ``__table_args__``; smoke tests in
  ``tests/test_job_index_models.py`` guard the invariants. +9 tests.
  (commit ``c0f4ea4``)

- **13.2 Search-key + content-hash normalization** -- pure-function
  module ``src/jobs/normalize.py``. ``normalize_search_key()`` strips
  LinkedIn / generic tracking params (``currentJobId``, ``origin``,
  ``trk*``, ``lipi``, ``lici``, ``utm_*``, ``gclid``, ``fbclid``);
  strings are stripped + collapsed + lowercased; lists are sorted and
  de-duplicated (including lists of dicts via stable JSON form);
  empty / None / [] are dropped. Keys preserved verbatim so
  LinkedIn's camelCase ``geoId`` / ``sortBy`` survive. The blacklist
  is authoritative, not the whitelist, so new ATS-specific filters
  work without code changes. ``search_query_fingerprint()`` SHA256s
  the normalized dict for the ``normalized_key`` column.
  ``normalize_job_content()`` + ``content_hash()`` exclude
  ``UNSTABLE_CONTENT_FIELDS`` (``applicant_count``, ``promoted``,
  ``view_count``, scrape timestamps, ``current_job_id``). +15 tests.
  (commit ``3f55c45``)

- **13.3 Freshness state machine** -- ``src/jobs/state.py``
  centralizes the ``new → active → stale → unknown → expired →
  archived`` lifecycle. ``next_state(current, event)`` is the
  caller-driven transition table; illegal transitions raise
  ``IllegalTransitionError``. ``project_by_time()`` is the pure
  time-decay projection used by the Phase 14 ``jd_health_check``
  job (``active→stale @24h``, ``stale→unknown @72h``,
  ``unknown→expired @7d``; ``new`` / ``expired`` / ``archived`` are
  excluded from decay; missing ``last_checked_at`` is a no-op).
  ``is_safe_to_apply(state)`` is the single Phase 17 pre-submit gate
  -- only ``active`` qualifies. +18 tests. (commit ``86b8b2e``)

- **13.4 Cache-first search flow with distributed lock** --
  ``src/jobs/store.py`` (``JobIndexStore``) is the persistence facade
  over the ORM models; methods take a live ``Session`` and never
  commit. ``src/jobs/search.py`` (``cached_search()``) is the
  orchestrator: normalize params → upsert ``SearchQuery`` → cache hit
  if ``status="fresh"`` and within the freshness window → otherwise
  acquire a Phase 12 distributed lock keyed
  ``jobs:search:{source}:{fingerprint}`` → re-check inside the lock
  → call ``fetch_fn`` (sync or async) → persist as ``search_results``
  rows → mark query ``fresh``. Lock contention returns the cached
  rows with ``stale=True``. On scrape failure the old cache is
  preserved, the query flips to ``stale`` with ``last_error``, and
  ``outcome.refresh_failed=True`` so the UI can flag the degraded
  read. +10 tests including real Redis (fakeredis) lock contention.
  (commit ``ce6bac9``)

- **13.5 Snapshot-versioned enrichment** -- ``src/jobs/enrich.py``
  implements ``scrape → normalize → content_hash → if hash matches
  latest_snapshot: no-op, else insert new immutable JobSnapshot row``.
  Existing snapshots are NEVER mutated; that immutability is what
  makes the ``applications.job_snapshot_id`` audit binding
  load-bearing. ``on_content_changed`` is the decorator-style listener
  hook (Phase 14 will queue follow-up scrapes when must-haves shift;
  Phase 17 will flag related applications for review). Listener
  exceptions are logged and swallowed so a buggy subscriber can't
  break enrichment. ``mark_refresh_failed`` / ``mark_source_404`` are
  the documented transient / 404 transitions. +9 tests covering
  expired→active recovery and listener fault-tolerance.
  (commit ``8f2d0f9``)

- **13.6 Context-aware freshness predicate** --
  ``should_refresh(posting, context, now=)`` in
  ``src/jobs/freshness.py`` returns a
  ``FreshnessVerdict(should_refresh, reason, age_hours, budget_hours)``
  so callers can both gate behaviour and surface "why" to the UI or
  trace store. Three documented contexts: ``search_display=72h``,
  ``generate_materials=24h``, ``before_submit=6h``. ``new`` (no
  snapshot) and ``unknown`` / ``expired`` / ``archived`` (degraded /
  terminal) always refresh. State governs lifecycle; this predicate
  judges time; the two axes compose. +18 tests across each context's
  window boundary and the state overrides. (commit ``6aaf3a4``)

- **13.7 Web UI freshness banner** --
  ``POST /api/jobs/index/freshness`` returns
  ``{known, status, last_run_at, last_success_at, last_error,
  result_count, age_hours, ...}`` (falls back to ``{known:false}``
  when the migration hasn't been applied);
  ``POST /api/jobs/index/refresh`` enqueues a high-priority
  ``kind="search.refresh"`` task that the Phase 14 scheduler will
  consume; ``GET /api/jobs/index/posting/{id}?context=...`` for the
  per-posting verdict. Frontend
  ``frontend/src/components/JobIndexBanner.vue`` renders
  "Last updated 18h ago · N indexed" plus a [Refresh] button.
  ``JobsView.forceRefreshSearch()`` clears ``lastFetchSignature`` so
  the next search() bypasses the canReuseFetchedResults shortcut.
  Application module ``src/application/job_index.py`` owns the
  session lifecycle and degrades gracefully on ``ProgrammingError``.
  +6 FastAPI tests. (commit ``eac302d``)

- **13.8 Legacy file-cache import + removal** --
  ``src/jobs/legacy.import_legacy_file_cache(store, legacy_dir,
  delete_after_import)`` walks ``data/cache/linkedin_search/*.json``
  and replays each file as one ``SearchQuery`` row (status='stale' so
  the next real search re-scrapes -- we don't trust historical disk
  data) plus one ``SearchResult`` link per contained job; idempotent
  across re-runs. ``clear_indexed_searches()`` replaces the legacy
  ``clear_linkedin_search_cache()`` (cascades ``search_results`` via
  the FK). New CLI: ``autoapply jobs import-legacy-cache
  --legacy-dir --delete``. ``src/intake/search_cache.py`` is deleted;
  ``src/intake/search.py`` removes the file-cache short-circuit. The
  ``TestLinkedInSearchCache`` class in ``tests/test_linkedin.py`` is
  removed; equivalent coverage lives in ``test_jobs_normalize.py`` +
  ``test_jobs_search.py``. End-to-end wiring of ``search_linkedin``
  into ``cached_search`` is deferred to Phase 17's plan-run loop
  per the in-code comment. +6 tests. (commit ``99e2dea``)

- **fix (codex P2)**:
  ``JobIndexStore.prune_results_not_seen_since(query_id, threshold)``
  deletes ``search_results`` rows whose ``last_seen_at < threshold``.
  ``cached_search()`` captures ``run_started_at`` before ``fetch_fn``
  and prunes immediately after persisting the fresh links, so
  postings missing from the new scrape lose their link instead of
  replaying on the next cache hit. ``outcome.counts`` carries
  ``"removed"`` alongside ``"scraped"`` / ``"new"`` so the UI banner
  can surface "N new · M removed · K updated". +1 regression test.
  (commit ``aacde6d``)

**Phase 13 close**: 1004 passed, 1 skipped on ``feat/phase-13`` after
the codex fix; ``ruff check`` clean; frontend builds clean.

### Phase 12: Cache Infrastructure (Redis)

First introduction of Redis as the L2 cache substrate. Builds the
generic cache + lock + queue primitives that Phases 13, 14, 17, and
18 will consume. Scope is deliberately narrow: LLM and embedding
responses only -- JD content caching ships in Phase 13 because it
needs content versioning, not TTL eviction.

- **12.1+12.2 Cache infrastructure + Redis L2 backend** -- `src/cache/`
  module (``CacheBackend`` ABC, ``LRUBackend`` for L1, ``RedisBackend``
  for L2, ``Cache`` orchestrator with namespace TTLs `llm:7d`,
  `embedding:30d`, `response:5m`, version-stamped keys), Redis
  connection management with graceful degrade (malformed URLs, DNS
  failures, transport errors all degrade to L1-only with a logged
  warning), ``autoapply redis ping/info/flush`` CLI (all
  ``--json``-friendly, exit-non-zero on failure, destructive ops
  gated on ``--yes``, namespace validated against glob injection),
  and a ``docker-compose.yml`` with Redis 7.2 + AOF + maxmemory cap.
  The cache singleton retries L2 attachment every 30s so a
  Redis-down-at-boot deployment recovers without a process restart.
  +109 tests (836 total).

- **12.3 Distributed lock primitive** -- ``cache.lock(key, ttl=600,
  blocking=False)`` in ``src/cache/lock.py`` on Redis ``SET NX PX``
  with WATCH/MULTI/EXEC compare-and-delete release (chosen over
  Lua ``EVAL`` because fakeredis EVAL is unreliable across
  platforms). Tokens are uuid4 + ``secrets.token_hex`` so a stale
  process cannot release a successor's lock. Lock keys live in
  their own ``lock:`` prefix to avoid colliding with cache value
  keys. Process-local ``threading.Lock`` fallback when L2 is
  unavailable OR when Redis raises mid-acquisition. +15 tests.

- **12.4 Opt-in LLM response caching** -- ``generate_text(cache=True)``
  wraps the call with an L1+L2 lookup. Cache key is SHA256 over
  ``provider + model + base_url + system + prompt + output_format``;
  the model + base URL come from the registered provider so an
  API-key model swap or endpoint change invalidates the cache
  automatically. Only the primary provider's successful responses
  are cached -- caching a fallback would keep replaying the
  fallback's answer after the primary recovered. Cache failures
  swallow at debug level so the LLM call never breaks on a Redis
  blip. +12 tests.

- **12.5 Cache-wrapped OpenAI embeddings** -- ``embed_text(text)``
  in ``src/matching/semantic.py``. Default ``cache=True`` (embeddings
  are deterministic given ``model + base_url + text``); 30-day TTL.
  ``ApiKeyProvider.get_api_key`` resolves the API key (credentials
  > ``OPENAI_API_KEY`` env). Graceful degrade to ``None`` on any
  failure (provider missing, HTTP error, malformed JSON shape) so
  callers fall back to keyword matching. +20 tests.

- **12.6 Cache inspector UI** -- New page at ``/settings/cache``
  rendering Redis health, per-namespace entry counts, hit-rate,
  $-saved estimate, and one-click namespace clear behind a modal
  confirmation. New endpoints ``GET /api/cache`` (snapshot) and
  ``DELETE /api/cache/{namespace}`` (requires ``{confirm: true}``).
  Both run their SCAN+DEL paths off the FastAPI event loop via
  ``asyncio.to_thread``. The clear path drives its own SCAN+DEL
  rather than ``RedisBackend.clear_namespace`` so transport
  failures surface as ``clear_failed`` instead of looking like a
  successful 0-key clear. Frontend ``request()`` helper now
  attaches the parsed body to thrown errors so structured FastAPI
  ``detail`` objects don't render as ``[object Object]``. +19 tests.

- **12.7 Cost dashboard upgrade** -- ``AgentStep.cached`` derived
  from ``llm_attempts[0]``. ``AgentResult`` exposes
  ``cached_step_count`` / ``fresh_step_count`` /
  ``total_cost_usd_fresh`` / ``total_cost_saved_usd`` so the trace
  viewer can render "N fresh + M cached" with a ``saved $X.XXXX``
  pill. Legacy traces (pre-Phase-12.7) fold ``step_count`` /
  ``total_cost_usd`` into the fresh totals so the partition
  invariant (cached + fresh = step_count) holds. +12 tests.

**Total**: +187 tests across the phase (727 -> 927, +200 including
trace serialisation coverage). ruff clean. Frontend builds clean.
Codex review ran on every sub-phase; final pass on each was clean.

### Phase 11: Reliability & Cleanup

Tightens the Phase 10 provider layer and ships the upgrade migration
tool. Sub-phases landed independently on `feat/phase-11`; merged to
`dev` as one Phase.

- **11.1 Provider fallback chain** -- `generate_text()` now accepts an
  ordered chain (`fallback_providers: [a, b, c]` in `config/settings.yaml`
  in addition to the legacy scalar `fallback_provider`). Errors are
  classified into a `ProviderErrorKind` enum (`auth`, `quota`, `network`,
  `timeout`, `server`, `bad_request`, `parse`, `unknown`) and only
  transient kinds advance to the next provider -- retrying a malformed
  prompt on a second provider just burns money on the same failure. The
  per-call attempt list (`{provider, ok, kind, error, latency_ms}`) is
  exposed via the `src.utils.llm.last_attempt_chain` ContextVar and
  carried onto each `AgentStep.llm_attempts` so the trace viewer can
  show which provider actually answered. `config/settings.yaml` flips
  `allow_fallback: true` now that the chain actually classifies failures.
  +25 tests (705 total).
- **11.2 `autoapply migrate` CLI** -- one-shot upgrade tool for legacy
  credential and settings artifacts. Detects stale
  `managed_by: codex-cli` breadcrumbs, subprocess providers carrying
  dead stored secrets, credential rows for ids the registry no longer
  knows, and the legacy `llm.provider` / scalar `llm.fallback_provider`
  keys. Default is dry-run; `--apply` performs fixes and writes
  `.bak.YYYYMMDDTHHMMSSZ` snapshots beside the originals. `--json` emits
  a stable envelope for automation. +13 tests (718 total).
- **11.3 Docs sync** -- this changelog entry, plus
  `docs/PROJECT_MANAGEMENT.md` and `docs/AGENT_ARCHITECTURE.md` updated
  to reflect the 11.1 ContextVar plumbing on `AgentStep`. The Phase
  11-18 v2 roadmap was already in the docs after commit `68421bc`;
  no further roadmap edits needed in 11.3.
- **11.4 Provider health monitor** -- background poller calls
  `test_connection()` on every configured provider every 5 minutes and
  caches results in memory (`src/providers/health.py`,
  `ProviderHealthMonitor`). Probes run in a worker thread
  (`asyncio.to_thread`) so subprocess providers' `--version` checks
  don't block the FastAPI event loop. Lifecycle is managed by a
  `@asynccontextmanager` lifespan on the app, with
  `AUTOAPPLY_DISABLE_HEALTH_MONITOR=1` opt-out for TestClient usage.
  New endpoints `GET /api/providers/health` (cached snapshot) and
  `POST /api/providers/health/refresh` (force probe + return fresh
  snapshot). The Settings page's "Last verified ..." line now reflects
  live telemetry rather than the last manual-test breadcrumb, and
  surfaces `health.detail` in the destructive variant when a probe
  failed. +7 tests (725 total).
- **11.5 Writer sync for list+scalar fallback shapes** -- Phase 11.1
  made `fallback_providers` (list) authoritative; `get_llm_settings`
  ignores the legacy `fallback_provider` scalar when both exist. Before
  this sub-phase the writers that mutate `settings.yaml` only updated
  the scalar, so users who had already migrated to the list form never
  saw their fallback selections take effect. Fixed across four codex
  review rounds in `src/core/config.py` (`update_llm_settings`),
  `src/cli/cmd_provider.py` (`use_cmd`), `src/cli/cmd_migrate.py`
  (`detect_settings_issues` / `apply_settings_fixes` now promote the
  orphan `llm.provider` key for pre-Phase-10 configs), and
  `src/application/providers.py` (`disconnect_provider`,
  `use_provider_as_primary`). The new `_coerce_chain` helper accepts
  list / comma-separated string / missing -- the same three shapes
  `get_llm_settings` reads -- and the chain logic now: (a) preserves
  list-only configs through disconnect, (b) keeps deeper fallbacks when
  one chain entry is removed, (c) ignores a stale scalar when the list
  is present, (d) mirrors the new list head onto the scalar after
  pruning, and (e) preserves `allow_fallback: false` through both
  disconnect cleanup and self-heal. +8 tests (727 total). ruff clean.

### Licensing: PolyForm Noncommercial 1.0.0 adopted

The project was previously unlicensed ("Private -- not yet
determined" in the README). Adopted **PolyForm Noncommercial 1.0.0**
as the source-available license.

- Added `LICENSE` at the repo root with the full PolyForm
  Noncommercial 1.0.0 text plus a `Required Notice` and a
  `Commercial Use` section directing commercial users to
  `frostnova986@gmail.com` for a separate license.
- Updated `README.md` License section with a permitted-vs-
  commercial table and the commercial-licensing contact path.
- Updated `pyproject.toml`: `license = { file = "LICENSE" }`,
  `authors`, and a `License :: Other/Proprietary License`
  classifier (PolyForm Noncommercial is not on the OSI/SPDX
  standard list, so we use the "Other" classifier; the canonical
  license name lives in `LICENSE` itself).

**What is permitted without contacting the author**: personal use
to apply for your own jobs, academic / coursework / thesis use,
nonprofit / public-research / educational-institution use, and
noncommercial open-source forks.

**What requires a commercial license**: running AutoApply as a
paid service, bundling it into a commercial product, using it
inside a for-profit company's recruiting workflow, or selling
support / hosting / modifications.

### Phase 10: LLM Provider Abstraction

The "LLM" layer was previously hard-coded to two subprocess
providers (`claude -p` and `codex exec`). Phase 10 breaks that open:
all five call paths now go through a `ProviderRegistry` so users
can plug in any of OpenAI / Anthropic / Gemini (REST) or Claude CLI
/ Codex CLI (subprocess).

- **10.1 Provider abstraction + credential store**
  (`src/providers/base.py`, `src/providers/store.py`):
  `LLMProvider` ABC, `ProviderKind`, `AuthType` (`API_KEY`,
  `SUBPROCESS`), `ProviderTestResult`. Credentials live in
  `data/providers/credentials.json` (mode 0600) with OS-keyring
  fallback when available. Never written to git, never logged.
- **10.2 REST adapters** (`src/providers/openai.py`,
  `src/providers/anthropic.py`, `src/providers/gemini.py`):
  one adapter per vendor using `httpx`. Each implements
  `generate(prompt, system, model)`, `list_models()`, and a deep
  `test_connection()` that does an auth-validating round-trip
  (not just a token presence check).
- **10.3** Originally a "Codex OAuth wrapper" -- removed in 10.7.
  The wrapper conflated "drive Codex CLI as a subprocess" with
  "implement a native OAuth client", and the OAuth half was never
  real (generation always went through `codex exec`). Kept for one
  revision under a back-compat alias before 10.7 cleaned it up.
- **10.4 Claude CLI subprocess provider**
  (`src/providers/claude_cli.py`): `auth_type=SUBPROCESS`. The CLI
  owns its own login -- AutoApply doesn't store a token, doesn't
  manage refresh, doesn't run an OAuth dance. `test_connection`
  is a deep probe (`claude --version` + status check).
- **10.5 Registry bridge into `generate_text`**
  (`src/providers/registry.py`, `src/utils/llm.py`): old call sites
  in `generation/`, `matching/`, `agent/` are unchanged -- the
  dispatch picks the configured primary provider transparently.
  Fallback dispatch (when primary errors) lands in Phase 11.1.
- **10.6 `autoapply provider` CLI subcommands**
  (`src/cli/cmd_provider.py`): `list`, `set-key`, `test`,
  `set-primary`, `set-fallback`, `disconnect`. The
  `provider login` subcommand introduced in 10.3 was removed in
  10.7 -- subprocess providers manage their own auth; users run
  `codex login` / `claude login` directly.
- **10.7 Settings page UI**
  (`frontend/src/views/SettingsView.vue`): connect / disconnect /
  test / set-primary / set-fallback for every provider in one
  place. Distinguishes "API-key provider, configured" from
  "subprocess provider, CLI installed and authenticated" from
  "subprocess provider, CLI installed but NOT logged in" --
  the last case is reported via `codex login status` so the user
  doesn't dispatch generations that will crash at runtime.
  Disconnect button stays visible for subprocess providers when a
  stale credential breadcrumb exists from earlier revisions
  (labelled "Clear stored record" in that mode).

**Architectural pivot recorded here**: this Phase 10 was originally
planned as "cover-letter agent". It was reordered after Phase 9
because the LLM-provider abstraction unblocks every subsequent
agent phase (no point writing a second agent against a hard-coded
`subprocess.run(['claude', ...])`). The roadmap was re-planned again
on 2026-05-12 (v2) around Redis + the commercial path, then tightened
on 2026-05-14 (v3) so Phase 14 explicitly owns task queue / worker
execution and Phase 15 owns resume + cover-letter generation v2. Four
new cross-cutting / infrastructure phases are inserted ahead of agent
work: Phase 11 (reliability), Phase 12 (cache infrastructure with
Redis), Phase 13 (Job Index & Freshness Engine), Phase 14 (task queue
+ scheduled work). Phase 18 (multi-tenancy & auth hardening) closes
the v1 commercial-ready core. See `docs/PROJECT_MANAGEMENT.md` for
the full sub-phase breakdown and `docs/DECISIONS.md` D018-D024 for the
rationale.

Test baseline at Phase 10 close: 669 passed, 1 skipped.
`ruff check src/ tests/` clean. Frontend rebuilds clean.

### Agent Phase 9: Form-Filler Agent (with HITL gate, eval suite, cost telemetry)

The first real business node converted to agent-mode. The deterministic
`form_filler.py` is still the default; `agent_form_filler.py` is the
new path, gated on confidence + the existing approval queue.

- **9.1 Browser tool layer** (`src/agent/tools/browser.py`,
  `src/agent/tools/browser_models.py`): four sync, side-effect-free
  tools the agent can call -- `browser_inspect_page`,
  `browser_find_field`, `browser_propose_fill`, `browser_screenshot`.
  Operate on a `PageSnapshot` extracted by the orchestrator; the agent
  never holds a Playwright handle. Stdlib HTML snapshot builder for
  fixtures + an async `build_snapshot_from_page` for live runs. 38 tests.
- **9.2 Orchestrator** (`src/execution/agent_form_filler.py`): wires
  snapshot → agent loop → proposal review → approval gate →
  deterministic `fill_fields`. Two gate kinds, `form_fill_review`
  (soft, optional based on confidence threshold) and `submit_form`
  (hard, always required). `submit()` raises `PermissionError` if
  the gate hasn't approved -- there is no force flag. Falls back to
  rule-based filling when the agent crashes or proposes nothing.
  Adds `profile_lookup` tool so PII is never pasted into the prompt.
  21 + 15 tests across orchestrator and profile tool.
- **9.3 Eval suite** (`tests/agent_evals/fixtures/form_filler/`):
  five fixtures (basic / Workday-like / Greenhouse-like /
  Lever-like w/ recovery / Ashby-like long select). Two new scorers:
  `field_mapping_match` and `no_proposal_for_label`. Runner emits a
  JSON envelope; CLI gate is `autoapply eval --suite form_filler
  --min-pass-rate 0.85`. Baseline JSON locked in
  `tests/agent_evals/baselines/`. 14 tests.
- **9.4 Cost / latency telemetry**: `AgentStep` now carries
  `prompt_tokens`, `output_tokens`, `cost_usd`; `AgentResult` and
  `TraceRecord` aggregate. Estimated via chars/4 heuristic with rates
  configurable by env var. Surfaces in `autoapply eval`, the web trace
  viewer, and persisted trace JSON. 13 tests.
- **9.5 Docs**: new `docs/AGENT_ARCHITECTURE.md` describing the
  three-layer orchestrator/loop/tool split and the HITL contract;
  README updated with agent-mode notes; this changelog entry.

Verification baseline: 553 passed, 1 skipped. `ruff check` clean.
`autoapply eval --suite form_filler --min-pass-rate 0.85` exits 0
with 5/5 passing at ~$0.23 estimated under default rates.

### Agent Phase 8: Agent Harness (foundational layer)

Foundational primitives that Phase 9 sits on top of. Shipped in
commits ed75568..e6a06ee on `feat/phase-8`.

- **8.1 Tool abstraction layer** (`src/agent/tools/base.py`): `Tool`
  ABC, `ToolSpec`, `ToolRegistry` with allow-list views, `ToolResult`
  with structured payload + error channel. Built-in `fs_read`,
  `text_stats`, `finish`. Hardened `fs_read` truncation to handle
  multi-byte UTF-8 boundary cleanly.
- **8.2 Bounded ReAct agent loop** (`src/agent/core/loop.py`): manual
  `{thought, action}` JSON protocol so both `claude` and `codex` CLIs
  work without provider-native tool-use. Hard `max_steps`,
  `step_timeout`, `allow_tool_errors` controls; `finish` sentinel; tool
  errors surface as observations rather than aborting.
- **8.3 Trace store + viewer** (`src/agent/trace/`, `src/web/routes/agent.py`):
  per-session JSON document under `data/agent_traces/`; FastAPI viewer
  at `/api/agent/viewer` lists recent runs and replays steps.
- **8.4 Fixture-driven eval harness** (`src/agent/eval/`,
  `tests/agent_evals/fixtures/agent_smoke/`): JSON fixtures specify
  `goal`, allowed tools, scripted LLM responses, and scorer
  expectations. New `autoapply eval` CLI command with `--suite`,
  `--list`, `--json`, `--min-pass-rate`.
- **8.5 HITL approval gate** (`src/agent/gate/queue.py`,
  `/api/agent/gate/...`): file-backed approval queue with `propose`,
  `approve`, `reject`, lazy expiry, and a viewer UI for pending
  requests.

### UI Overhaul -- Phase A: Design System
- Generated the AutoApply design system spec via the `ui-ux-pro-max` agent — color palette, typography scale, spacing rhythm, and component inventory

### UI Overhaul -- Phase B: Tailwind + shadcn-vue Foundation
- Installed Tailwind v3 and `tailwindcss-animate`, configured `darkMode: ["class", '[data-theme="dark"]']`, and aliased every theme color (`background`, `foreground`, `card`, `primary`, `secondary`, `muted`, `accent`, `destructive`, `success`, `warning`, `popover`, `border`, `input`, `ring`) to HSL CSS variables
- Added the HSL token sets in `frontend/src/tokens.css` for both light and dark themes
- Added shadcn-style base components under `frontend/src/components/ui/`: `Button`, `Input`, `Label`, `Card` (+ `CardHeader` / `CardTitle` / `CardContent` / `CardFooter` / `CardDescription`), `Badge`, `Dialog` (+ `DialogContent` / `DialogHeader` / `DialogTitle` / `DialogDescription` / `DialogFooter` / `DialogClose`), `Skeleton`, and `EmptyState`

### UI Overhaul -- Phase C: View Rebuilds
- Rebased `styles.css` onto the Phase B HSL tokens; tightened core controls (button / input / chip / banner / page header) and tightened layout (workspace 1400px, denser tables, hoverable rows)
- Rebuilt every view shell with shadcn `Card` + Lucide icons: Dashboard, Applications, Settings, Materials, Profile, Jobs
- Replaced empty states with the shared `EmptyState`, switched primary actions to shadcn `Button` (default / ghost / destructive / icon variants), and added `tabular-nums` to numeric columns

### UI Overhaul -- Phase D: Primitives + a11y Polish
- Added shadcn `Alert` (+ `AlertTitle` / `AlertDescription`) with destructive / success / warning / default variants and migrated every `.banner is-*` div across all views to the new primitive
- Migrated the JobsView "Apply Materials" modal and the MaterialsView Template Library modal to reka-ui `Dialog` (portal, overlay, scroll-lock, focus-trap, built-in close button)
- Rebuilt `AppSelect.vue` as a wrapper around reka-ui Select primitives (portal, scroll buttons, animated open/close); preserved the existing `{ value, label }` API by mapping empty-string sentinels to an internal token
- Rebuilt `TagInput.vue` with shadcn-style chip pills (rounded-full, `bg-secondary`) and a flush inline `Input`; preserved the keyboard / paste / commit-on-blur behavior
- Replaced `AppIcon.vue` and `DockIcon.vue` (hand-rolled SVG dictionaries) with direct lucide-vue-next components everywhere, then deleted both files
- Migrated the dock navigation, theme toggle, and the ProfileView / JobsView / PaginationBar accordion + pagination icon-buttons to shadcn `Button` (`variant="ghost"`, `size="icon"`); destructive variants pick up `text-destructive` + `hover:bg-destructive/10`
- Added `aria-expanded` bindings to every accordion-head and editor-item-head button across ProfileView and JobsView
- Pruned dead CSS for the migrated banner / modal patterns; bundle CSS dropped from 53 kB to 52 kB

### Materials Workspace
- Added a dedicated Vue Materials page at `/materials` for job/JD selection, applicant profile selection, resume/cover letter options, template selection, preview, and artifact downloads
- Job search results now route `Generate Apply Materials` into `/materials?jobId=...` so the selected search result carries into the generation workflow
- Added Preview tabs for Resume and Cover Letter, collapsed-by-default review, validation chips, version metadata, and selected-format download links
- Moved template upload into a Template Library modal so low-frequency template management does not interrupt the core generation flow
- Removed TXT as a product-facing Cover Letter output option; DOCX/PDF are the supported UI formats

### DOCX-First Template Packages
- Added first-class template package assets under `data/templates/<document_type>/<template_id>/`
- Template packages now include `template.docx`, `manifest.json`, `style.lock.json`, and sample JSON assets
- Added default packages: `resume/ats_single_column_v1` and `cover_letter/classic_v1`
- Added template APIs for listing packages and uploading DOCX templates
- Uploaded templates are validated, assigned safe IDs, given required named styles/markers, and serialized without leaking absolute filesystem paths
- Template package writes are stable on Windows with LF newlines and trailing final newline

### Generation Pipeline
- Added structured Resume/Cover Letter IR models, evidence retrieval, template-aware fitting, artifact validation, page counting, and local generation version persistence
- DOCX rendering now prefers manifest block markers such as `{{resume.sections}}` and `{{cover_letter.body}}`
- Renderers use named Word styles from template manifests instead of ad hoc formatting overrides
- Resume fitting applies template capacity limits for sections, items, bullets, bullet length, and skill lines
- Cover letter generation now follows the same DOCX-first artifact path with validation and version metadata

### Web/API Hardening
- Added `/api/jobs/generate-material`, `/api/templates`, `/api/templates/upload`, and `/api/artifacts/download`
- Added artifact download path restrictions to `data/output`
- Added template ID validation to prevent path traversal outside `data/templates`
- Added template upload size limits before parsing DOCX content
- Restored strict search-profile ID validation at the service layer and mapped invalid DELETE requests to HTTP 400
- Added profile-aware material generation from saved applicant profiles

### Search, Intake, And ATS Fixes
- Added Ashby ATS adapter support and Ashby application URL normalization
- Hardened LinkedIn pagination, page-state probing, job-card scroll detection, primary Apply-button selection, popup cancellation, and description extraction
- Avoided double-enriching LinkedIn description-filter matches
- Restored duplicate collapse for no-keyword LinkedIn searches
- Normalized LinkedIn cache keys so keyword/filter order does not cause duplicate cache entries
- Fixed JD parser false positives for `ml`, `api`, and `data` substring matches

### Review And Verification
- Ran Claude Code CLI review, fixed all actionable findings, and rechecked the final cache-key/partition coverage fixes
- Current verification baseline: `uv run python -m pytest` -> 340 passed, 1 skipped
- Current lint/build baseline: `uv run ruff check .` and `npm run build` pass

### Packaging + Runtime Fixes
- Added package build metadata so `uv sync` installs the `autoapply` CLI entrypoint
- Added missing `itsdangerous` dependency required by FastAPI session middleware

### Apply Pipeline Fixes
- `autoapply apply` now loads a real job context from DB or ATS APIs instead of reusing the newest files in `data/output`
- Per-job resume and cover letter generation now runs inside the apply flow
- QA templates are loaded from `qa_bank` and persisted with tracked applications
- Application tracking is now created and synced during the apply flow
- Batch apply now uses the scoring layer correctly

### Web Fixes
- Dashboard and applications routes now pass template-safe stats structures
- Fixed HTMX outcome editing to call the real tracker update function
- Job search page now shows match scores when a profile is available
- Job search page now exposes an Apply action that triggers the existing pipeline

### Earlier Verification Snapshot
- `uv run autoapply --help`
- `uv run pytest -q` -> 244 passing at the packaging/runtime-fix checkpoint

## [0.7.0] -- 2026-04-03 -- Phase 7: Web GUI

### Phase 7.1: FastAPI Backend
- FastAPI app factory with Jinja2 templates, static files, session middleware
- 4 route modules: dashboard, jobs, applications, profile
- `autoapply web` CLI command with --host, --port, --reload, --no-open options
- Dependencies: fastapi, uvicorn, jinja2, python-multipart

### Phase 7.2: Dashboard Page
- Pipeline stats cards (total, pending, submitted, response rate)
- Pipeline breakdown with colored status badges
- Quick action buttons (search jobs, view applications, manage profile)
- DB connection warning when database is unavailable

### Phase 7.3: Job Search Page
- Search form with source selector (ATS/LinkedIn/All), keywords, location
- ATS and time posted filter controls
- HTMX-powered live search (partial page updates without full reload)
- Results list with ATS type badges, company, location, employment type
- "View" links to external application URLs

### Phase 7.4: Applications + Profile Pages
- Applications: pipeline stats grid, outcome breakdown, filterable table
  with inline HTMX-powered outcome editing (pending/rejected/oa/interview/offer)
- Profile: identity card, skills cloud, education/experience/projects sections
- Resume upload form for automatic profile generation via Claude CLI

### Phase 7.5: Tests
- 21 new test cases: app factory, all 4 pages, navigation, CLI integration
- Fixed Jinja2 template caching issue with TemplateResponse API
- Total test count: 228 (177 existing + 30 LinkedIn + 21 Web)

---

## [0.6.0] -- 2026-04-03 -- Phase 6: LinkedIn Integration

### Phase 6.1: LinkedIn Session Manager
- LinkedInSession: Playwright persistent context with cookie reuse for authenticated sessions
- Auto-detects login state; opens browser for manual login on first run
- Cookie persistence in `data/.linkedin_session/` avoids repeated logins

### Phase 6.2-6.3: LinkedIn Job Scraper + ATS Redirect Detection
- LinkedInScraper: search URL builder with all LinkedIn filter parameters (time, experience level, job type)
- Pagination through search results, job card extraction from DOM
- Job detail page enrichment: full description extraction
- ATS redirect detection: clicks "Apply" button to discover external Greenhouse/Lever URLs
- URL cleaning to remove tracking parameters
- Updated ATSType schema to include "linkedin"

### Phase 6.4: Pipeline Integration
- search_linkedin() async function + search_linkedin_sync() wrapper
- CLI: `autoapply search --source linkedin --keyword "swe intern" --location "US"`
- New CLI options: --source (ats/linkedin/all), --keyword, --location, --time-filter, --max-pages, --no-enrich, --headless
- Combined ATS + LinkedIn results in "all" mode
- LinkedIn URL detection in apply command with helpful redirect message

### Phase 6.5: Tests
- 30 new test cases covering URL utilities, search URL builder, schema integration, CLI integration, mocked Playwright parsing, filter constants
- Total test count: 207 (177 existing + 30 new)

---

## [0.5.0] -- 2026-04-03 -- Phase 5: CLI + Tracking + Full Pipeline

### Phase 5.1: CLI Framework + Init Wizard
- Click command group with 4 commands: `autoapply init`, `search`, `apply`, `status`
- `autoapply init` wizard: config validation, DB connection + migration, profile import (YAML / resume parse / template), LLM CLI availability check
- `autoapply search` wraps intake layer with Click interface, adds --score for profile-based ranking
- Entry point: `[project.scripts] autoapply = src.cli.main:main`
- ASCII-safe output for Windows console compatibility

### Phase 5.2: Application Tracking
- tracker/database.py: Application CRUD, state machine sync to DB, outcome updates, filtered queries, joined queries
- tracker/analytics.py: Pipeline stats, outcome breakdown (response/positive rate), per-company stats, per-platform stats, daily activity timeline
- tracker/export.py: CSV export (error_log excluded by default), formatted text status report
- Application model extended: state_history, fields_filled/total, files_uploaded, updated_at, outcome_updated_at

### Phase 5.3: Apply + Status Commands
- `autoapply apply --url` / `--job-id` / `--batch`: single or batch application pipeline
- Batch mode: search -> score -> rate-limited apply with proper result tracking
- `autoapply apply --dry-run`: generate materials without browser
- `autoapply status`: analytics dashboard with pipeline/outcome/platform/company stats
- `autoapply status --export-csv`: export to CSV

### Post-Review Fixes (Codex review: 3 P1, 6 P2, 2 P3)
- **P1**: Alembic migration failure now correctly returns error
- **P1**: _execute_application returns ApplicationResult; batch only records submitted apps
- **P1**: UUID validation before DB job lookup
- **P2**: Sanitized DB connection errors in CLI output
- **P2**: tracker uses flush() not commit() for caller-owned transactions
- **P2**: submitted_at preserved on re-sync (only set when None)
- **P2**: CSV export excludes error_log by default
- **P2**: Resume/cover selection by most recent mtime

### Tests
- 21 CLI/tracker tests (command structure, init wizard, ATS detection, tracker CRUD, analytics, export)
- Total: 177 tests passing

---

## [0.4.0] — 2026-04-02 — Phase 4: Browser Automation + Form Filling

### Phase 4.1: Core Infrastructure
- Application state machine (FSM) with 11 states, validated transitions, audit trail
- Playwright browser manager: async context manager, anti-detection (configurable sandbox), random delays, screenshot capture

### Phase 4.2: Form Detection & Filing
- Form field detector: text, email, tel, select, checkbox, radio (grouped by name), textarea, file inputs
- Scoped detection: form_selector parameter constrains scanning to ATS form container
- Profile-to-field mapper: label keyword matching for identity, education, links, with QA fallback
- Multi-strategy file uploader: direct selector → auto-detect by label → file chooser dialog
- File extension allowlist validation (pdf, docx, doc, rtf, txt)

### Phase 4.3: ATS Adapters
- Abstract BaseATSAdapter with full apply() workflow: open → fill → upload → answer → review/submit
- GreenhouseAdapter: #application_form scoped, resume/cover selectors, custom questions, #submit_app
- LeverAdapter: .application-form scoped, .resume-upload, custom questions, submit with postings-btn
- Submit verification: wait for networkidle + check for error indicators before advancing FSM

### Phase 4.4: Rate Limiting & Anti-Detection
- RateLimiter: random action delays, error cooldowns, hourly application caps
- Concurrency-safe (asyncio.Lock) for all state mutations
- Configurable via settings.yaml (min_delay, max_delay, cooldown_on_error, max_applications_per_hour)

### Post-Review Fixes (Codex review: 3 P1, 7 P2, 2 P3)
- **P1**: Fully qualified CSS selector paths to prevent wrong-field targeting
- **P1**: File upload extension allowlist to prevent arbitrary file exfiltration
- **P1**: Submit verification — check for error indicators before marking as SUBMITTED
- **P2**: Added checkbox and radio button detection to form scanner
- **P2**: Scoped form detection to ATS form container (Greenhouse & Lever)
- **P2**: CSS attribute escaping in label lookup and selector generation
- **P2**: asyncio.Lock for rate limiter concurrency safety
- **P2**: Removed --no-sandbox default from browser launch args

### Tests
- 43 execution tests (state machine transitions, form mapping, rate limiter, ATS adapter workflows)
- Total: 156 tests passing

---

## [0.3.0] — 2026-04-02 — Phase 3: Resume/CL Tailoring + QA

### Phase 3.1: Resume Builder
- JD tag extraction from requirements and title keywords
- Bullet selection by tag overlap (ranked, configurable max per entity)
- Optional LLM-powered light lexical rewrite with keyword injection
- Fact-drift guard: rejects rewrites that change length >2x or <0.3x
- Full pipeline: extract → select → rewrite → docx assembly → PDF conversion

### Phase 3.2: Cover Letter Generator
- Structure-constrained generation: opening → evidence → company tie-in → close
- LLM generation bounded by system prompt (250-400 words, no fabrication)
- Template fallback when LLM unavailable
- Auto-selects best evidence bullets from profile by JD tag overlap

### Phase 3.3: QA Auto-Responder
- Question classifier for 10 types (authorization, sponsorship, salary, start_date, why_company, why_role, strengths, weaknesses, experience_years, custom)
- Confidence cascade: QA bank match → template → LLM → flag for review
- Geography and role-type variant selection from QA bank
- High-risk types (salary, authorization, sponsorship) always flagged for review
- LLM-generated answers always flagged for review

### Post-Review Fixes (Codex review)
- **P1**: Removed auto-generated authorization/sponsorship template answers — jurisdiction-sensitive, must use QA bank with explicit variants or flag for review
- **P2**: Experience year calculation now uses month-level precision with interval merging to avoid double-counting and calendar-year inflation

### Tests
- 43 generation tests (JD tag extraction, bullet selection/ranking, evidence selection, cover letter template, question classification, QA bank matching, variant selection, template answers, experience calculation, answer pipeline)

---

## [0.2.0] — 2026-04-02 — Phase 2: Job Intake + Smart Filtering

### Phase 2.1: Job Intake
- Unified Job schema (Pydantic): RawJob, JobRequirements, employment type/seniority classifiers
- Base scraper with httpx client, context manager, retry/timeout support
- Greenhouse ATS scraper (boards-api.greenhouse.io/v1)
- Lever ATS scraper (api.lever.co/v0/postings)
- LLM-assisted JD parser with regex fallback (skills, education, experience, visa, remote)
- Job storage with deduplication (source + company + source_id)
- Batch intake orchestrator with YAML company config
- Generic filter engine: YAML-driven profiles with location/work mode rules, title keywords, employment type, seniority, description regex exclusions, experience cap
- Batch search CLI: `python -m src.intake.search --profile default`
- Default filter profile: Vancouver/Toronto all modes, US remote-only, software intern roles, excludes Canadian PR/citizenship

### Phase 2.2: Smart Filtering & Scoring
- Hard rule matching: work authorization, experience (1-year grace), education level, employment type, spam/ghost job detection
- ApplicantContext loader from profile YAML
- Skill overlap scoring with normalization and fuzzy matching (JS→javascript, K8s→kubernetes)
- TF-based keyword similarity as embedding fallback
- Cosine similarity utility for future embedding support
- Composite scorer: weighted skill overlap (must-have 70% / preferred 30%) + keyword similarity + rule bonus + quality multiplier
- Quality multiplier penalizes sparse JDs and missing apply URLs
- Ranked output with `print_ranking()` CLI helper

### Post-Review Fixes
- **P1**: Added `source_id` column to Job model for proper indexed deduplication
- **P1**: Fixed dedup query to filter by company and use source_id directly
- **P1**: Added per-job IntegrityError handling in upsert_jobs
- **P1**: Separated `coop` vs `internship` in employment type classifier
- **P1**: Replaced hardcoded year with `datetime.now().year` in experience calculation
- **P1**: Wrapped JD text in XML tags to mitigate prompt injection
- **P1**: Used Pydantic `model_validate` for LLM output validation
- **P2**: Extracted shared HTML stripping, applied to Lever descriptions
- **P2**: Fixed Greenhouse office type check for non-dict entries
- **P2**: Normalized US work auth comparison to case-insensitive
- **P2**: Weighted must-have skills higher than preferred in scorer
- **P2**: Default IGNORECASE for filter regex patterns

### Tests
- 26 filter tests (work mode inference, title/location/description/experience matching)
- 34 matching tests (rules, semantic overlap, keyword similarity, scorer ranking)

---

## [0.1.0] — 2026-04-02 — Phase 1: Infrastructure + Memory + Documents

### Phase 1.1: Project Initialization
- uv project with pyproject.toml (sqlalchemy, psycopg, pgvector, alembic, python-docx, pymupdf, etc.)
- PostgreSQL 14 + pgvector 0.7.4 installed and configured
- Alembic migrations with 5 tables: jobs, applications, applicant_profile, bullet_pool, qa_bank
- SQLAlchemy 2.0 ORM models with vector columns and FK constraints
- Config loader (YAML + .env + env var overrides, credential URL encoding)
- LLM CLI wrapper (claude -p and codex exec via subprocess, with error handling)
- Logging setup (file + console with configurable level)

### Phase 1.2: Applicant Memory Layer
- Profile YAML schema definition (`data/profile/schema.yaml`)
- Profile loader: YAML → DB ingestion with tag extraction per section
- Bullet pool: extract tagged bullets from experiences/projects, query by tag overlap
- Story bank: STAR-format stories with theme/context filtering
- QA bank: structured Q&A with canonical answers, geography/role variants, confidence, review flag
- Resume importer: .docx/.pdf → Claude CLI → structured YAML

### Phase 1.3: Document Processing Layer
- Block-based docx engine: `{{PLACEHOLDER}}` substitution + section block rebuilding
- Section rebuilders clear stale template content before inserting new data
- PDF converter: docx2pdf with LibreOffice CLI fallback
- File manager: standardized naming (`type_company_role_date.ext`) + output path management
- Template registry with auto-discovery from template directory

### Post-Review Fixes (Codex review)
- **P1**: Migration now enables pgvector extension before creating VECTOR columns
- **P1**: Fixed table creation order (jobs before applications) for FK constraint
- **P2**: Added FK constraint `applications.job_id → jobs.id` in migration and ORM
- **P2**: Percent-encode DB credentials in connection URL (handles special characters)
- **P2**: Declared `pymupdf` as explicit dependency in pyproject.toml
- **P2**: `find_answer()` now ranks by pattern overlap score, not first-of-type

---

## [0.0.0] — 2026-04-02 — Project Setup

### Added
- Initial project skeleton with directory structure
- README with architecture overview and tech stack
- Implementation plan in English (`docs/plan_en.md`) and Chinese (`docs/plan_zh.md`)
- `.gitignore` and `config/.env.example`
- Project management documentation (`docs/PROJECT_MANAGEMENT.md`)
