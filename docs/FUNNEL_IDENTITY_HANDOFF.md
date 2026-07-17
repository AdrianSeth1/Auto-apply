# Funnel analytics and canonical identity handoff

This note is written for Arya, Claude, Codex, and future maintainers. It records
the behavioral contract, not just the files changed.

## Plan selection quality contract (2026-07-12)

Automation selection is quality-first and queue-aware. Before Top-N and startup
bonus selection, orchestration now applies these non-destructive gates in order:

1. hard-rule qualification from the scorer;
2. exact title-family compatibility for the active saved-search profile;
3. identifiable direct-employer quality (known staffing/recruiter listings and
   email-address company names are skipped);
4. minimum final score of 0.50;
5. one selection per normalized company/title pair;
6. previously applied and already-pending review jobs are removed.

The company/title collapse only protects selection slots. It does not merge or
delete stored postings and is intentionally separate from canonical possible-
duplicate clusters. The report exposes a count for every gate plus
`selected_jobs` (company, title, score, startup status, source, and URL), so a
future maintainer can audit claims about quality rather than infer them from
`selected=5`.

Run `uv run python scripts/audit_plan_quality.py` for a read-only rehearsal. It
does not create review entries or materials and writes the complete result to
`data/audits/latest_plan_quality.json`.

Verified 2026-07-12 rehearsal: AI implementation selected eight jobs, including
five startup bonuses; analyst, implementation, and sales engineering each
selected five; TAM selected three and rejected the remaining nine candidates
below the 0.50 floor. Total: 26 jobs, 21 core plus five bonus startups.

Raw volume must not be confused with pool quality. The same rehearsal fetched
610–970 records per plan but rejected 84–92% in saved-search filters. Analyst
and implementation were overwhelmingly Adzuna-supplied (only 7 and 19 ATS
keyword matches respectively), while TAM had just 21 title-family candidates
and only four direct-employer candidates at or above 0.50 before prior-history
removal. Future source work should optimize direct relevant postings, not raw
record count. `raw_jobs_fetched`, `search_filtered_out`, and `source_counts` are
now persisted on every plan report so this distinction stays visible.

## Startup result guarantee (2026-07-11)

Startup discovery is a bonus lane, not a quota inside the main Top-N. Search
keeps HN employer-posted roles available even when they lack an exact saved
search phrase, but all ordinary eligibility and fit gates still apply. Scored
interactive results retain the complete normal lane and add only enough
startup-only candidates to reach five. Automation selects `top_n` normal jobs,
then appends startups until the selected set contains five. Previously applied
jobs are removed first so lower-ranked eligible startups can backfill. If fewer
than five qualified startup jobs exist, the report shows the actual count
instead of admitting disqualified filler.

## Practice-run evidence (2026-07-11)

Profile: AI solutions / forward-deployed roles, United States, entry-level,
full-time, minimum requested compensation $90,000.

- 686 raw unique jobs were fetched; 107 survived filters.
- Raw source counts: 473 ATS, 247 Adzuna, 15 HN, 0 Remotive (sources overlap
  before cross-source deduplication).
- Shown source mix: 42 Adzuna, 25 Greenhouse, 21 Ashby, 19 Lever.
- Score bands: 23 at 0.60+, 15 at 0.50–0.59, 9 at 0.40–0.49, 60 below 0.40.
- The strongest results were credible adjacent-fit roles: Commure Forward
  Deployed Engineer (0.672), Reveald AI Solutions Engineer (0.662), Palantir
  Defense Applications (0.657), SAIC AI Solutions Engineer (0.657), Pendo GTM
  AI Engineer (0.649), and Doppel Forward Deployed Engineer (0.648).

Assessment: ranking direction is good, but the threshold is too permissive.
The top 20–25 jobs are worth human review; the bottom 60 should not compete for
attention. Direct ATS employers are generally higher quality than the Adzuna
tail. Staffing/recruiting firms, senior roles, and clearance-heavy defense work
remain false positives. Employer type should become an explicit ranking signal,
not a hard rejection, because consultancies and government contractors can be
legitimate targets for this profile.

## Funnel contract

`funnel_events` is append-only and idempotent on
`(tenant_id, entity_type, entity_id, stage)`. Stages are:

`discovered → qualified → reviewed → applied → screen → interview → offer`

- Search completion records `discovered` and non-disqualified `qualified`.
- A human review decision records `reviewed`.
- Confirmed submission records `applied`; queued or prepared work does not.
- OA, interview, and offer outcome changes record the last three stages.
- Events carry source, profile variant, material variant, time spent, and JSON
  metadata where known. Missing historical dimensions use `unknown`; do not
  invent them.
- Analytics are available at `GET /api/analytics/funnel?weeks=12` and displayed
  on Applications. Conversion denominators are the immediately prior stage.

## Canonical identity contract

`src/jobs/identity.py` produces a suggestion fingerprint. It never merges rows.
Exact normalized company and title are mandatory. Exact normalized location is
the preferred corroborator because aggregator and direct ATS URLs differ; a
canonicalized URL is used only when location is absent. The Job Database shows
`Possible duplicate ×N` only when multiple stored records share the key.

Known limitation: employers often publish several genuinely distinct openings
with identical company/title/location. These remain a cluster for human review,
not an automatic merge. A future confidence model may add exact application URL,
description hash, requisition number, and first-seen proximity as evidence.

## Legacy uniqueness contract

The legacy `jobs` table now has a partial normalized unique index:

`(tenant_id, lower(trim(source)), trim(source_id))`

Rows with blank/null source identity remain allowed. Before migration, the live
database audit found zero duplicate groups. The migration still contains a
transactional keeper/repoint/delete backfill for other installations. The oldest
row wins; application and review references are repointed before deletion. The
newer `job_postings` table already had `(tenant_id, source, source_id)` uniqueness.

`RawJob.dedup_key` and `src/intake/storage.py` now use the same source-global key.
Do not add company back to this identity: company display-name drift was the
reason duplicate rows previously escaped Python deduplication.

## Safe extension points

1. Add employer classification (`direct`, `staffing`, `consulting`, `defense`,
   `unknown`) as explainable metadata and a configurable score adjustment.
2. Default the Jobs UI to a review threshold near 0.50–0.55 while retaining a
   way to inspect lower-ranked results.
3. Add description similarity only as extra duplicate evidence; never use an
   embedding-only match to merge or delete records.
4. Add a small user control for time spent if automatic elapsed time is not a
   fair measure of active work.
5. Add material-variant conversion display once enough applications exist to
   avoid misleading tiny-sample conclusions.

## Quality recalibration (2026-07-11 evening)

The first live practice run exposed that keyword similarity was being treated
as candidacy probability. It is not. The system now:

- hides scored results below 0.50 by default;
- allows HN startup postings with unknown pay to survive (explicitly low pay
  still fails the numeric filter);
- rejects active/security-clearance requirements in shared rules;
- rejects known staffing/recruiting intermediaries by company name;
- recognizes `Sr` anywhere in a title, including a trailing `Engineer Sr`;
- gives a small startup/explicit-entry signal boost; and
- down-ranks unmarked roles at OpenAI, Anthropic, and Palantir because their
  lexical match materially overstates realistic interview probability.

The second practice run fell from 107 to 34 results and restored startup
representation (Commure, Doppel, Cora AI via HN, Attio, Alloy). This is still a
heuristic. Outcome-funnel data should replace hand-tuned employer priors once
there is enough application volume to estimate conversion honestly.

Cover letters now fail closed: an LLM failure, weak structure, style rejection,
or fact-drift finding produces a visible generation failure and no generic DOCX.
The active generator now runs the same number/entity drift guard that previously
protected only the unused agent-cover-letter path.

### Follow-up: repair retries and startup sourcing

Fail-closed initially exposed that the local model often returns a short first
draft or invents a metric. The generator now makes at most three attempts. Each
retry receives the rejected draft (when available) and the exact structure or
fact-drift reason. It still fails closed after the bounded repair loop.

Do not implement automated Wellfound scraping. Wellfound's current General
Terms explicitly prohibit scraping/harvesting and constrain automated access.
Users can paste a Wellfound JD into Materials for manual import. For automated
startup discovery, `src/intake/hn_jobstories.py` consumes the official Hacker
News Firebase `/v0/jobstories` endpoint (up to 200 current startup jobs) and is
combined with the existing monthly "Who is hiring?" adapter.
