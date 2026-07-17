# AutoApply master handoff

**Status:** THE current-state document. Start here after `CLAUDE.md`/`AGENTS.md`.
**As of:** 2026-07-16 (post quality-hardening session)
**Supersedes:** `FABLE_CURRENT_SYSTEM_HANDOFF.md`, `SONNET_START_HERE.md`,
`JOB_POOL_V2_IMPLEMENTATION_STATUS.md`, `PROJECT_MANAGEMENT.md`,
`AUDIT_2026-07-11.md`, and the pre-V2 plan documents — all deleted 2026-07-16.
If a git-recovered copy of those files disagrees with this one, this one wins.

## Reading order and authority

1. `CLAUDE.md` / `AGENTS.md` — repository invariants and safety rules (binding).
2. This file — live state, policies, schedules, and open risks.
3. `docs/JOB_POOL_V2_ARCHITECTURE.md` — normative V2 design contracts (binding).
4. `docs/FUNNEL_IDENTITY_HANDOFF.md` — outcome-funnel and identity constraints (binding).
5. `docs/DECISIONS.md` — append-only decision log. New choices append; never rewrite.
6. `docs/CHANGELOG.md` — engineering history; `docs/PHASE_HISTORY.md` — phases 1–18.
7. `docs/INFRASTRUCTURE.md` — runtime topology. `docs/AGENT_ARCHITECTURE.md` — the
   bounded agent harness. `docs/DEPLOYMENT.md` — human setup/usage guide.

Precedence on conflict: invariants > V2 architecture > funnel/identity handoff >
this file > changelog history.

## 1. What AutoApply is

Local-first job discovery and application preparation for one user (Arya).
Acquires jobs from approved sources, stores immutable JD snapshots, evaluates
each job against five target roles, builds one quality-limited global
portfolio (up to 20 unseen Tier A/B jobs/day), and prepares evidence-grounded
materials. **Nothing auto-submits.** Every application passes an explicit
human review/approval transition. The user's real goal is 20–30 quality
applications per week — quality of what gets sent matters more than counters.

### Live product state

| Area | State |
|---|---|
| Matching pipeline | Job Pool V2 (`pipeline_version: v2`), five targets, one evidence bank |
| Core delivery | Up to 20 unseen Tier A/B jobs daily (07:15 UTC live run) |
| Reservoir | Target 40; measured 28 on 2026-07-15 (refill remains important) |
| Diversity | Max 2 jobs/company (hard); 5/target first pass (soft); +5 startup lane |
| Same-role reposts | Suppressed: one slot per (company, title-variant), reason `title_variant_duplicate` |
| Refill | Dry-run V2 every 6h (00:45/06:45/12:45/18:45 UTC), non-consuming |
| Sources | Greenhouse/Lever/Ashby/Workday boards + HN/Remotive + Adzuna (discovery-only); 100 verified employer endpoints in 4 rotating groups of 25 |
| LinkedIn | ALL automated access off (account was flagged). Status widget is passive (disk-only) by default; live probe only on explicit user refresh |
| Location | Explicit US onsite/hybrid or explicit US-remote eligible; SF/Dallas/Portland/NYC/LA preferred (small ranking signal only) |
| Materials | Pre-generated for all delivered cards; evidence-grounded; letter origin labeled |

## 2. Pipeline (stage summary)

```
approved sources → one recall-oriented acquisition (portfolio_run.py)
  → normalize/identity/immutable snapshots (jobs/enrich.py, store.py, identity.py)
  → JobFacts + tri-state global gates (matching/job_facts.py)
  → five target evaluations (matching/scorer_v2.py) → A/B/C/D tiers
  → one representative occurrence + one owning target
  → title-variant dedupe + portfolio selection (orchestration/portfolio.py)
  → review queue + materials tasks → human approval → submission prep
```

Key properties (details in `JOB_POOL_V2_ARCHITECTURE.md`):

- Acquisition, fit, candidacy, quality, identity, and selection are separate
  decisions. High text match never overrides a failed gate.
- Gates are `pass`/`fail`/`unknown`; unknown is never positive evidence.
- Snapshots are immutable; changed content = new snapshot, rescored.
- Tier B floors: conf 0.55 / role 60 / evidence 50 / level 50 / SF 60 / Cand 52
  / RI 58. Tier A: conf 0.70 / role 70 / evidence 60 / level 60 / SF 68 /
  Cand 60 / RI 68 + known domain. Tier C never fills A/B capacity.
- Practice/dry runs persist evidence but never consume history or create
  cards/materials. Only real V2 selections count as surfaced history.
- `Review Index = 0.40·StoryFit + 0.40·Candidacy + 0.12·preference + 0.08·trust`.
  Match score is NOT candidacy probability — preserve the documented
  adjustments (see `FUNNEL_IDENTITY_HANDOFF.md`) until outcome data says otherwise.

## 3. Materials policy (updated 2026-07-16)

- Resumes: block assembly from the evidence bank (`data/profile/candidate.yaml`),
  keyword-bounded rewrites, fact-drift validation. Never free-form LLM resumes.
- Cover letters: LLM draft (2 attempts, scored) vs a deterministic evidence
  template that always exists as a score-45 baseline candidate. **Whichever
  ships is labeled**: `letter_origin` (`llm` | `deterministic_baseline`)
  travels through document metadata → version ledger → review-card badge
  ("Template letter"). A baseline letter is a factual draft the user must
  rewrite before approving; it must never look like a tailored letter.
  (History: on 2026-07-15 two baseline letters shipped unlabeled — that is
  the failure mode this exists to prevent. The old "fail closed, destroy the
  artifact" rule was replaced by "ship labeled" per user decision 2026-07-16.)
- Word budget: min 180 / target 240 / max 340 per page — `_length_window_for`
  in `generation/cover_letter.py` and the validator defaults in
  `generation/validator.py` MUST stay in sync. (The old 260-word minimum
  flagged 20/20 letters in the first real batch; a 100%-firing warning
  carries no information.)
- `raw_evidence_dump` only fires when a resume bullet appears near-verbatim,
  compared against `metadata.evidence_bullets` — not on the "At SDS, I…"
  style the prompt itself demands.
- Company display names come from the ATS payload (`company_name` for
  Greenhouse), sanitized of zero-width characters, falling back to the board
  slug ONLY when the payload has no name. 6,372 stored rows were repaired on
  2026-07-16 (`scripts/fix_company_display_names.py`).
- Greenhouse salary metadata ("Budgeted Salary", currency/currency_range) is
  promoted to `raw_data.salary_min/salary_max` for pay-aware scoring.
- PDF conversion is verified on disk before an artifact path is recorded
  (`documents/pdf_converter.py`); a recorded-but-missing PDF is a bug.
- Each `materials.generate` writes a compact quality digest
  (`score_breakdown.materials_quality` on the review entry): letter origin,
  validation issue types, word count. The review UI renders it as badges.

## 4. Schedules

| Schedule | Cron (UTC) | Mode | Effect |
|---|---:|---|---|
| `nightly-portfolio-v2` | 07:15 daily | Live V2 | Delivers ≤20 A/B jobs + queues materials |
| `portfolio-reservoir-refill-v2` | 45 */6 | Dry V2 | Refresh/persist/score, non-consuming |
| `daily_search` | 02:00 | Legacy | Saved-search refresh into Job Index |
| `email_ingest` | 15 */6 | Maintenance | Recruiter-reply → outcome escalation (read-only IMAP) |
| `ledger_retention` | 03:30 daily | Maintenance | Prunes dry-run decision/link rows older than `retention.dry_run_ledger_days` (14) |
| `jd_health_check` / `cache_eviction` / `gate_expire_sweep` | hourly//15min | Maintenance | Freshness decay, artifact cleanup, gate TTLs |

The static 23:00 UTC `plan_run` Beat entry was REMOVED 2026-07-16 (it always
failed on the missing legacy `default` profile). Do not restore it, and do not
fabricate a `default` filter profile to appease `test_filters.py` — that test
failure is known and accepted.

`Run Plans Now` / `scripts/run_plans_now.py` is practice-only and forces
`dry_run=True`.

## 5. Operational safeguards (added 2026-07-16)

- **Single-instance guard:** `autoapply start` probes `/api/instance` when the
  web port is taken. If another AutoApply answers, it opens that one and exits
  instead of starting a duplicate stack on a random port (the 2026-07-15 P0:
  a full duplicate web+worker+Beat was found on :60416).
- **Ledger retention:** dry-run `portfolio_decisions` (unselected, no
  review_id) and dry-run `discovery_run_evaluations` older than 14 days are
  pruned daily. Run aggregates and ALL live-run rows are kept forever. Never
  extend this to review/application evidence, snapshots, or evaluations.
- **LinkedIn passive status:** the web UI's session widget no longer launches
  a headless browser against linkedin.com on mount. Passive disk check by
  default; live probe only on explicit refresh. Keep it that way — the
  account has already been flagged once.

## 6. Invariants (delta view — full list in CLAUDE.md/AGENTS.md)

All CLAUDE.md invariants stand, with one amendment made 2026-07-16:

> **#10 (amended):** Cover letters must never ship a deterministic/template
> letter *silently*. The deterministic evidence baseline MAY ship when LLM
> drafts fail quality scoring, but only labeled `deterministic_baseline`
> end-to-end (ledger + review card badge). Restoring silent unlabeled
> fallback OR stripping the label is a regression. A missing artifact with a
> visible retryable error remains acceptable; a generic fabricated letter is not.

New invariants from this session:

13. Portfolio selection spends at most one slot per (company, title-variant):
    workplace/region qualifiers are stripped from the END of the normalized
    title only. Never strip comma-suffixed specializations ("…, Fire
    Prevention"). Suppression reason `title_variant_duplicate` must persist.
14. Never record an artifact path that is not verified on disk.
15. `cover_letter.py::_length_window_for` and
    `validator.py::validate_cover_letter_document` thresholds move together.
16. No code path may launch a browser against LinkedIn without an explicit
    user action in that session.

## 7. Measured state and open risks

### 2026-07-15 first real 20-job batch (measured)

- 20/20 cards created, 40 artifacts, ~23 min generation wall-clock.
- 18/20 letters LLM-written; 2 shipped as (then-unlabeled) baseline.
- ~8 letters had slug company names (fixed: adapter + data repair + regen).
- One resume PDF was recorded but never written (fixed: conversion verify).
- Doppel's onsite+remote FDE reposts consumed two slots (fixed: variant dedupe).
- No slug-named application had reached `submitted` at audit time; 8 were
  `approved` — their materials were regenerated before submission.

### Open risks / next work

- **P1 — reservoir below target:** 28 vs 40. Back-to-back full 20-job
  deliveries are not guaranteed. Expansion criterion stays *net-new complete
  direct-apply Tier A/B jobs*, never raw posting volume.
- **P1 — outcome loop is half-wired:** email ingest escalates outcomes and
  `review_feedback` feeds bounded scorer priors, but nobody yet joins
  outcomes back to targets/tiers/letter-origin to learn which of the five
  targets converts. Before tuning scorer weights again, build that join.
- **P2 — Ollama batch profile:** ~23 min for 20 jobs is fine; measure RAM/VRAM
  and retry behavior before raising material concurrency above 2.
- **P2 — remaining stale-name sources:** Ashby/Lever boards have no payload
  company name; their slugs are usually clean but watch for exceptions.

## 8. Environment quick reference

- Windows, repo at `C:\Users\aryam\AutoApply`, Python 3.12+ via `uv run` only.
- Postgres/Redis via Docker Compose on `127.0.0.1` (never `localhost`).
- Ollama local: main `qwen3.6:35b-a3b`, small tier `gpt-oss:20b`;
  `OLLAMA_NUM_PARALLEL=1`, one loaded model.
- Web (`uv run autoapply web`, :8000) does not hot-reload; rebuild SPA with
  `cd frontend && npx vite build`; restart worker after backend changes.
- **Never run the full pytest suite against the live DB.** DB-backed suites
  use the live connection, and a 2026-07-16 full-suite run deleted all
  default-tenant `review_queue` rows (recovered via
  `tmp/rebuild_review_20260716.py` from portfolio_decisions + the version
  ledger). Run focused suites, or point tests at a disposable database
  first. DB-backed tests HANG (not fail) when Postgres is down. Known
  accepted failure: `test_filters.py::test_loads_real_config`.
- Daily startup: `Start AutoApply.bat` (now duplicate-safe).
