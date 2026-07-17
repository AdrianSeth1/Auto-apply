# Job Pool V2 — authoritative product and architecture design

Status: **Approved normative design; implemented as a live five-card V2 canary**  
Date: 2026-07-12  
Design authority: Sol Ultra / Codex design pass  
Implementation audience: smaller Codex/Claude agents working from bounded tickets

Implementation state and residual work are intentionally maintained separately
in `docs/JOB_POOL_V2_IMPLEMENTATION_STATUS.md`. Sonnet/Claude agents should begin
with `docs/SONNET_START_HERE.md` so this normative design is not confused with
legacy V1 code paths or with the original pre-implementation status line.

This document answers every required deliverable in
`docs/SOL_ULTRA_JOB_POOL_REDESIGN_BRIEF.md`. When it conflicts with current
provisional plan-selection behavior, this document is the intended V2 behavior.
Repository invariants in `AGENTS.md` remain binding.

Required-deliverable map:

| Brief deliverable | Normative section |
|---|---|
| Canonical target/profile schema | Section 2 |
| Staged funnel and stage contracts | Sections 4 and 10 |
| Fit/candidacy model and explanations | Section 5 |
| Apply/skip learning and rejection taxonomy | Section 6 |
| Source retention, expansion, health, and employer discovery | Section 7 |
| Source/employer/posting classification | Sections 7 and 8 |
| Unified configuration authority | Section 3 |
| Global portfolio policy | Section 9 |
| Replay, blind review, and release evaluation | Section 11 |
| Bounded implementation tasks | Section 12 |
| Decisions reserved for Arya | Section 13 |

## 1. Executive decisions

Job Pool V2 is a replacement pipeline, not a retuning of the current cosine
score.

The core decisions are:

1. **One candidate evidence bank, five target specifications.** Identity,
   experience, projects, bullets, stories, skills, and facts live once. A target
   specification contains role intent, candidacy boundaries, preferences,
   discovery aliases, evidence priorities, and selection policy. The target is
   data, never a YAML comment.
2. **One global acquisition run.** Every configured employer board is fetched
   once per refresh window. Persisted snapshots are routed and evaluated against
   all enabled targets. The system no longer performs five conceptually separate
   board sweeps and repairs overlap afterward.
3. **Recall first, then explicit staged decisions.** Acquisition and
   normalization do not decide fit. Eligibility, target routing, candidacy,
   evidence fit, employer/posting quality, deduplication, and portfolio selection
   are separate auditable stages.
4. **Tri-state gates.** A fact is `pass`, `fail`, or `unknown`. Unknown data does
   not silently pass as positive evidence and does not automatically fail unless
   the target explicitly declares that fact mandatory. Unknown lowers confidence
   and can cap the fit tier.
5. **Tiered candidacy, not a fake match percentage.** V2 outputs `A — strong`,
   `B — viable`, `C — stretch`, or `D — reject`, plus separate 0–100 **Story
   Fit**, **Candidacy**, and **Review Index** values and a separate confidence
   value. These are explainable ordering aids, not claimed interview
   probabilities, and the UI never adds a percent sign.
6. **Component floors prevent compensation.** High text similarity cannot rescue
   a role that is too senior, the wrong role family, or unsupported by candidate
   evidence. Role, level, and evidence components each have minimums for every
   tier.
7. **Feedback starts as bounded interpretable priors.** The 106 noisy historical
   decisions are not enough to train an unconstrained model. V2 first captures
   structured reasons and applies small, smoothed, target-specific adjustments.
   A learned model is deferred until enough clean labels exist and it wins a
   temporal holdout.
8. **Source transport is not employer quality.** Greenhouse, Adzuna, HN, etc.
   describe how a listing arrived. Separate metadata describes directness,
   employer type, selectivity, stage, posting health, and confidence.
9. **Portfolio selection is global.** A job gets one primary target and at most
   one review card. Core capacity is quality-limited, not quota-filled. Five
   startup jobs are a separate global bonus lane and never consume core slots.
10. **V1 remains available until V2 passes shadow evaluation.** All new tables
    and fields are additive. `matching.pipeline_version` controls `v1`,
    `v2_shadow`, or `v2`. Rollback is a configuration change, not a data restore.

### 1.1 Authoritative diagnosis

The system does not have one generic “not enough jobs” problem. The documented
evidence separates five failures, and V2 measures them independently:

| Failure class | Evidence and diagnosis | V2 response |
|---|---|---|
| Target strategy | Three profile bodies are identical after ignored comments; the fourth differs mainly in QA prose the matcher does not read. Equal five-plan budgets therefore rank nearly the same broad biography against different labels rather than expressing five candidacy theses. | One evidence bank, five validated target specs, materially different archetypes/exclusions/evidence weights, and global budget priorities. |
| Discovery / useful supply | Raw volume is high (roughly 610–970 records per plan), so there is no global raw-record shortage. Useful direct supply is nevertheless thin for analyst and implementation, Adzuna dominates several pools, employer coverage is not calibrated to attainability, some configured boards repeatedly fail, and query ordering starves later query variants. This is a shortage of target-affine, healthy, unique supply—not proof that acquisition is adequate. | Fetch once, retain transport provenance, rotate query arms, measure unique A/B yield, curate target/employer cohorts, add official adapters, and quarantine endpoints through a durable health state machine. |
| Filtering / fact quality | Only about 8–16% of raw records survive to seen candidates, but early exclusions have not had reconciled reason rows. Missing location can fail while other unknowns pass; duplicate YAML keys overwrite identity data; authorization compares free-form prose to enums; pre-normalization drops can disappear from audits. Attrition can therefore be healthy noise removal or a correctness defect, and the present reports cannot distinguish them. | Versioned `JobFactsV2`, tri-state gates, one terminal stage result plus reason rows for every pair, schema validation, and exact funnel reconciliation. |
| Ranking / candidacy | Historical 0.6–0.8 scores produced a 30% take rate versus 31% at 0.4–0.6. The current rule bonus is constant among qualified jobs, missing parsed skills receive a positive 0.5, broad biography text rewards generic vocabulary, short substrings such as `ai` can misfire, and level/evidence/selectivity are not properly modeled. The score is not calibrated and is not a realistic candidacy estimate. | Separate role, level, evidence, transfer, attainability, preference, and posting-trust components with floors, confidence, evidence citations, bounded feedback, and tier calibration. |
| Selection / reporting | Five independent Top-N plans, overlapping companies/jobs, process-local reservation, and a startup count target can manufacture apparent success even when qualified supply is weak. “Selected five” has historically described attempts more reliably than five useful new cards. | One transactional global portfolio, one primary target/card per canonical job, company caps, five additive startup slots, no filling below Tier B, and persisted portfolio decisions. |

This diagnosis means source expansion alone cannot fix ranking, and a better
scorer alone cannot create missing target-affine supply. Each release gate must
report both broad-funnel attrition and A/B supply by target before the system can
claim improvement.

## 2. Product target strategy

Keep five target families, but stop treating them as equal copies of one resume.
Rename TAM to its realistic center of gravity while preserving `tam` as a
temporary alias.

| Target ID | Display name | Default priority | Candidacy center |
|---|---|---:|---|
| `ai-implementation` | AI Implementation & Applied AI Solutions | 0.90 | Workflow discovery, customer-facing AI deployment, implementation, applied AI solutions |
| `saas-implementation` | SaaS Implementation & Solutions Consulting | 1.00 | Onboarding, configuration, professional services, deployment, project delivery |
| `revenue-operations-analyst` | Revenue, Business & Product Operations Analyst | 0.90 | RevOps, sales ops, operations/business analysis, pragmatic SQL/dashboard work |
| `associate-solutions-engineering` | Associate Solutions Engineering & Presales | 0.70 | Discovery, demos, proofs of concept, APIs/integrations, SMB or mid-market presales |
| `technical-customer-success` | Technical Customer Success & Enablement | 0.70 | Adoption, onboarding, enablement, customer health, technical support-to-success |

Priority influences portfolio balancing by at most four utility points. It
cannot make a lower-tier job outrank a higher-tier job. These defaults reflect
the documented evidence; Arya may change them in configuration without changing
the architecture.

### 2.1 One canonical evidence bank

Canonical file: `data/profile/candidate.yaml`.

It contains only facts and user-owned content:

- identity and work authorization;
- global job preferences and hard constraints;
- education;
- work experiences;
- projects;
- skills/capabilities;
- story bank;
- QA bank;
- evidence bullets.

Every evidence-bearing object receives a stable ID. IDs survive text edits so an
evaluation or generated bullet can cite its source.

Required stable ID prefixes:

- `exp_` — experience;
- `expb_` — experience bullet;
- `proj_` — project;
- `projb_` — project bullet;
- `story_` — story;
- `cap_` — demonstrated capability;
- `qa_` — QA answer.

Example shape:

```yaml
schema_version: 2
candidate_id: arya
identity:
  location: Portland, OR
  work_authorization:
    country: US
    status: permanent_resident
    sponsorship_needed: false
  professional_experience_years: 2
preferences:
  preferred_locations: [Portland, OR, Dallas, TX, US Remote]
  willing_to_relocate: true
  compensation:
    currency: USD
    preferred_base_min: 90000
    hard_base_min: null
experiences:
  - id: exp_sds
    company: SDS
    title: Operations Analyst & Startup Generalist
    bullets:
      - id: expb_sds_onboarding_ttv
        text: Redesigned client onboarding ...
        capabilities: [cap_saas_implementation, cap_process_design]
capabilities:
  - id: cap_saas_implementation
    label: SaaS implementation and onboarding
    level: demonstrated
    evidence_refs: [expb_sds_onboarding_ttv, expb_sds_guides]
```

Rules:

- Work authorization is an enum/structured object, never a free-form string for
  rule comparison.
- There is exactly one canonical location field. The current duplicate Dallas
  and Portland YAML keys are invalid under schema V2.
- Capability level is one of `demonstrated`, `working`, or `exposure`.
- A capability without at least one evidence reference fails validation.
- Generated or plausible prose may describe existing evidence, but cannot create
  a new capability or raise its level.

### 2.2 Target specification schema

Canonical directory: `config/targets/*.yaml`. Each file validates against a
Pydantic `TargetSpecV2` and is hashed after canonical serialization. The hash is
persisted as `target_version` on every evaluation.

```yaml
schema_version: 2
id: saas-implementation
display_name: SaaS Implementation & Solutions Consulting
enabled: true
priority: 1.0
positioning: >-
  Early-career SaaS implementer who maps workflows, configures solutions,
  improves onboarding, and translates between customers and technical teams.

role:
  core_titles:
    - implementation specialist
    - implementation analyst
    - implementation consultant
    - onboarding specialist
    - deployment specialist
    - professional services consultant
  adjacent_titles:
    - solutions consultant
    - customer onboarding manager
    - implementation engineer
  stretch_titles:
    - technical consultant
    - solutions architect
  excluded_title_terms:
    - senior
    - principal
    - director
  responsibility_signals:
    - requirements gathering
    - configure
    - onboarding
    - implementation
    - training
    - go-live
  negative_responsibility_signals:
    - sap implementation
    - oracle implementation
    - active clinical license

candidacy:
  core_max_required_years: 3
  stretch_max_required_years: 4
  allowed_seniority: [entry, associate, junior, unknown]
  unsupported_specializations: [sap, oracle, workday hcm]
  required_capability_groups:
    - any_of: [cap_saas_implementation, cap_project_delivery]
      weight: 2.0
  preferred_capabilities:
    - id: cap_stakeholder_translation
      weight: 1.0

constraints:
  employment_types: [fulltime, contract]
  geography_policy: candidate_default
  compensation_policy: candidate_default
  active_clearance: reject

discovery:
  query_terms: []  # empty means derive from core + adjacent titles
  description_only_lane: false
  employer_cohorts: [b2b_saas, healthtech_saas]

selection:
  minimum_core_tier: B
  per_run_soft_cap: 5
  allow_stretch_in_core: false

materials:
  preferred_evidence_refs: [exp_sds, proj_prof_ai]
  section_priority: [experience, projects, skills, education]
```

Configuration uses normalized whole-phrase matching, not raw substrings. A title
term `ai` cannot match `Gainsight`. Implementations must use the same token
boundary helper everywhere.

### 2.3 Resolved target contract

`resolve_target(candidate, target_spec)` returns an immutable
`ResolvedTargetProfile` containing:

- `candidate_version` — SHA-256 of canonical evidence YAML;
- `target_version` — SHA-256 of canonical target YAML;
- normalized title rules;
- target capability map with evidence references;
- resolved global/target constraints;
- discovery terms;
- material priorities.

Scoring and material generation receive the same resolved object. Search does
not invent a second filter profile. The current five copied profile files remain
readable only through a migration adapter until cutover.

## 3. Unified configuration authority

V2 configuration ownership is:

| Concern | Authority |
|---|---|
| Candidate facts/evidence | `data/profile/candidate.yaml` |
| Role intent, routing, candidacy, target-specific material priorities | `config/targets/*.yaml` |
| Global portfolio capacity/diversity/startup policy | `config/portfolio.yaml` |
| Adapter enablement, rate limits, compliance policy | `config/source_policy.yaml` |
| Schedule and live/shadow mode | `config/automation_plans.yaml` |
| Dynamic employer endpoints and health | PostgreSQL, seeded once from `companies.yaml` |
| Temporary interactive UI overrides | search request/localStorage, never saved into target files implicitly |

`config/search_profiles.yaml` and `config/filters.yaml` become V1 compatibility
inputs. The V2 compiler derives discovery terms and deterministic gates from
`TargetSpecV2`. Hardcoded role maps and employer regexes leave
`src/orchestration/plan_run.py` after parity is proven.

`automation_plans.yaml` changes from five role runs to one portfolio run:

```yaml
plans:
  - id: nightly-portfolio-v2
    task: orchestration.portfolio_run
    enabled: true
    pipeline_version: v2_shadow
    target_ids:
      - ai-implementation
      - saas-implementation
      - revenue-operations-analyst
      - associate-solutions-engineering
      - technical-customer-success
    portfolio_id: default
    hour: 7
    minute: 30
```

The old five plans remain enabled only while V2 is shadowing. They are disabled,
not deleted, at cutover.

## 4. Staged funnel and data contracts

V2 uses one reconciled stage ledger. Every candidate job-target pair ends the
run with a stage decision and reason codes.

| Stage | Input | Responsibility | Output |
|---|---|---|---|
| 0. Endpoint health | source endpoint | Decide whether endpoint is due, active, or quarantined | endpoint check |
| 1. Acquire | healthy endpoint / approved external feed | Fetch public postings once | raw source records |
| 2. Normalize | raw record | Produce `RawJob`, provenance, stable source identity | persisted posting/snapshot |
| 3. Enrich facts | snapshot | Deterministically extract level, years, responsibilities, capabilities, domain, geography, pay, authorization | `JobFactsV2` + field confidence |
| 4. Global eligibility | job facts + candidate | Apply facts independent of target | tri-state gate results |
| 5. Target routing | job facts + target taxonomy | Route to zero or more targets | role route + route confidence |
| 6. Target candidacy | routed pair | Compute role, level, evidence, domain, preference, opportunity components | immutable evaluation |
| 7. History exclusion | evaluation + workflow history | Exclude submitted, active pending, or explicit snooze/block | reason-coded candidate |
| 8. Canonical grouping | candidates | Group possible duplicates without merging; choose preferred occurrence | cluster representative |
| 9. Cross-target ownership | evaluations for representative | Pick one primary target; retain secondary explanations | owned candidate |
| 10. Portfolio selection | owned A/B candidates | Apply global capacity and diversity policy | core/startup selections |
| 11. Materials/review | selected jobs | Generate grounded materials and create one review card | review entries |
| 12. Feedback/outcomes | human action | Capture structured reasons and downstream outcomes | feedback + business funnel events |

### 4.1 `JobFactsV2`

The current `JobRequirements` is retained and extended; existing fields remain
backward compatible.

Required additions:

```text
parser_version: str
full_description_available: bool
title_tokens: list[str]
country_codes: list[str]
workplace_type: remote | hybrid | onsite | unknown
remote_geographies: list[str]
compensation: {currency, min, max, period, confidence} | null
level_signals: list[{kind, value, excerpt, confidence}]
responsibility_signals: list[{normalized_id, excerpt, confidence}]
capability_requirements: list[{capability_id, importance, excerpt, confidence}]
domain_signals: list[{domain_id, excerpt, confidence}]
authorization_signals: list[{kind, value, excerpt, confidence}]
specialization_signals: list[{specialization_id, excerpt, confidence}]
```

Every extracted claim must retain an excerpt or structured source field. The
local LLM may extract into this schema only for a bounded shortlist; it cannot
assign fit or invent evidence.

### 4.2 Tri-state gate contract

```text
GateResultV2:
  gate_id: str
  status: pass | fail | unknown
  reason_code: str
  message: str
  job_evidence: list[str]
  confidence: float  # 0..1
```

Global hard fails:

- explicit active security-clearance requirement;
- explicit work authorization incompatible with normalized permanent-resident
  status;
- explicit non-US onsite/hybrid location when relocation policy does not allow
  it;
- explicit employment type outside allowed types;
- no usable posting or application URL after provenance resolution;
- closed/expired posting.

Unknown location, compensation, years, or employment type does **not** equal
pass. It continues with an unknown gate, lowers confidence, and may cap the tier.

Pre-V2 correctness blockers to test before any replay:

- current work-authorization comparison treats a complete free-form string as
  an enum and can falsely reject a permanent resident;
- current matching-rule documentation implies location checking, but location
  is performed elsewhere rather than by `check_rules`;
- duplicate YAML location keys overwrite Dallas with Portland;
- fuzzy skill substring matching allows short tokens such as `ai` to match
  unrelated words;
- an absent job skill list currently grants positive 0.5 overlap;
- current parser role families are software-centric and do not represent the
  five intended targets.

These are parity blockers, not reasons to bypass the backend/frontend location
invariant.

## 5. Fit and candidacy model

### 5.1 Components

Every routed job-target pair receives seven 0–100 components:

- **R — role compatibility**: actual work archetypes, title, domain context,
  and explicitly excluded work;
- **L — level plausibility**: required years, seniority, ownership scope,
  quota/book/program signals;
- **E — evidence strength**: weighted job capabilities supported by candidate
  capability IDs and evidence references;
- **D — domain/tool transferability**: direct, adjacent, learnable, or
  unsupported specialization/tool gaps;
- **A — attainability**: bounded employer/role prior such as early-career
  openness, conventional-background demands, and founding scope;
- **P — preference fit**: role interest, geography/workplace, employment,
  compensation, employer interest, travel;
- **Q — posting trust**: completeness, application integrity, freshness,
  employer identity, and requisition specificity.

V2 deliberately produces two separate judgments and one review ordering index:

```text
StoryFit S       = 0.50R + 0.35E + 0.15D
Candidacy C      = 0.45L + 0.30E + 0.15D + 0.10A
ReviewIndex U    = 0.40S + 0.40C + 0.12P + 0.08Q
```

The bounded feedback adjustment is applied after `U` and may move it by at most
five points. Confidence does **not** multiply or subtract from the score; doing
so would systematically bury incomplete but potentially good postings.
Confidence controls enrichment and tier eligibility.

None of `S`, `C`, or `U` is an interview probability and none is displayed with
a percent sign.

### 5.2 Component rules

Role compatibility:

- `T` = normalized title-taxonomy match;
- `W` = target-weighted work-archetype/responsibility match;
- `X` = domain-context compatibility;
- `N` = share of evidence assigned to explicitly excluded archetypes.

```text
R = 100 * clamp(0.30T + 0.60W + 0.10X - 0.50N, 0, 1)
```

Core, adjacent, stretch, and description-only titles initialize `T` at 1.00,
0.80, 0.65, and 0.50 respectively. A dominant excluded archetype (≥0.50) caps
R at 35. Title-only assessment with no usable responsibilities caps R at 65.
Embedding similarity may supply at most ten auxiliary archetype points and may
not override explicit contradictory responsibilities.

Level plausibility uses the candidate's documented two recruiter-credited years
and the job's required-versus-preferred language. Experience score `Y` is 1.00
for 0–2 required years, 0.80 for 3, 0.55 for 4,
0.30 for 5, 0.10 for 6, 0 for 7+, and 0.50 with zero field confidence when
unknown. A preferred rather than required minimum blends halfway toward 1.00.

Title-level score `H` is 1.00 for entry/associate/junior/new-grad, 0.65 unknown,
0.55 early-mid, 0.35 manager, 0.20 senior/lead, and 0.05
staff/principal/director/head. `Manager` remains target-sensitive.

Scope score `O` is 1.00 for bounded individual execution, 0.80 for independent
project/workstream ownership, 0.40 for enterprise portfolio/renewal quota/major
account ownership, and 0.15 for organizational leadership or architecture
authority.

```text
L = 100 * (0.45Y + 0.30H + 0.25O)
```

Mandatory 6+ years caps L at 25; staff/principal/director scope caps it at 15;
founding roles ordinarily cap it at 35 after actual scope review. These are
candidacy caps, not global eligibility deletion, so the rejection remains
visible and auditable.

Evidence strength:

- parser output maps requirements/responsibilities to the same capability
  ontology used by candidate evidence;
- explicit mandatory capabilities weight 3, core responsibilities 2, preferred
  capabilities 1;
- evidence bases are 1.00 quantified professional result, 0.85 direct
  professional work, 0.80 adopted external-user project, 0.70 substantial
  production-like project, 0.60 adjacent professional evidence, 0.35
  coursework/certification, 0.20 plausible narrative, and 0 absent;
- verification multiplies evidence by documented 1.00, self-reported 0.90,
  plausible 0.65, or needs-review 0.25;
- for a candidate capability, use the strongest item plus 0.10 of the second
  strongest independent item, capped at 1.00, so copied bullets do not inflate
  support;
- taxonomy transfer is exact 1.00, close 0.75–0.90, weak adjacent 0.40–0.60,
  unrelated 0;
- `E = 100 * supported_weight / known_requirement_weight`;
- an unmatched critical non-credential capability caps E at 49; a missing
  mandatory license/credential is eligibility failure;
- no extracted capabilities does not award 0.5. It uses responsibility evidence
  and receives low confidence; it cannot reach Tier A without enrichment;
- every supported capability lists candidate evidence refs; every gap lists the
  job excerpt.

Domain/tool transfer uses domain scores exact 1.00, adjacent 0.75, broadly
transferable 0.55, specialized unfamiliar 0.20, incompatible 0; and required-
tool coverage exact 1.00, canonical alias 0.90, explicit adjacent 0.60,
learnable generic gap 0.35, specialist mandatory absent 0.

```text
D = 100 * (0.55 * domain_transfer + 0.45 * tool_transfer)
```

If one side is unknown, reweight to the known side and reduce confidence. If
both are unknown, D is null; ranking may use an explicitly marked 50 imputation,
but Tier A is prohibited.

Attainability starts at 65 and applies transparent bounded signals: explicit
early-career program +15, unconventional/project backgrounds welcomed +10,
repeated early-career hiring +5, high-selectivity employer −10, founding/first
technical hire −25, and conventional tenure/background strongly preferred −15.
Clamp A to 0–100. Brand prestige is never positive evidence.

Preference defaults:

```text
P = 0.25 role_interest + 0.25 geography_work_mode + 0.20 compensation
  + 0.15 employer_interest + 0.10 startup_interest + 0.05 travel_schedule
```

Posting trust:

```text
Q = 0.25 description_completeness + 0.20 application_integrity
  + 0.20 freshness + 0.20 employer_identity_confidence
  + 0.15 requisition_specificity
```

Explicit hard preference violations fail in tri-state eligibility. Unknown
preference fields use an explicit 50 ranking imputation with zero field
confidence, never a positive explanation. A direct ATS URL can improve Q but
source transport has no blanket fit modifier.

### 5.3 Confidence

Overall confidence `K` is:

| Evidence available | Weight |
|---|---:|
| Role/responsibility facts | 0.30 |
| Experience/level facts | 0.25 |
| Requirements/capabilities | 0.20 |
| Employer classification | 0.15 |
| Posting/provenance facts | 0.10 |

Field confidence conventions are explicit quoted fact 0.95, high-precision
deterministic inference 0.80, weaker inference 0.60, conflict 0.20, unknown 0.
Evidence verification confidence remains separate from extraction confidence.

### 5.4 Tiers

Tiers require all listed floors; weighted averages cannot compensate for a
failed floor.

| Tier | Required floors | Automatic eligibility |
|---|---|---|
| A — strong | eligible; K≥0.70; R≥70; E≥60; L≥60; S≥68; C≥60; U≥68; no cap | core and startup |
| B — viable | eligible; K≥0.55; R≥60; E≥50; L≥50; S≥60; C≥52; U≥58; no severe contradiction | core and startup |
| C — stretch | eligible; K≥0.45; R≥58; E≥40; L≥30; S≥55; C≥40; U≥50; risk displayed | manual explore only by default |
| D — marginal/reject | anything below C or any hard fail | never selected |
| Unresolved | critical eligibility/fact conflict | enrichment/manual resolution only |

Unknown years plus missing capabilities caps a job at C. Missing only one can
still reach B if all other floors and K pass. No usable application URL prevents
selection. Startup status changes neither components nor tier.

### 5.5 Explanation contract

`JobTargetEvaluationV2` persists:

```text
snapshot_id, target_id, candidate_version, target_version, pipeline_version
gate_results[]
component_scores: {role, level, evidence, domain, attainability, preference, posting_trust}
component_confidence: same keys
story_fit, candidacy_index, review_index, tier, confidence
strengths[]: {reason_code, message, job_excerpt, candidate_evidence_refs[]}
gaps[]: {reason_code, severity, message, job_excerpt, missing_capability_ids[]}
feedback_adjustment: {value, priors_used[]}
```

The review card headline becomes, for example:

`B — Viable | 66 review index | 76% fact confidence`

It shows StoryFit, Candidacy, three strongest reasons, and the largest gap. It
never says `71% match` or claims an interview probability.

### 5.6 Bounded LLM use

Deterministic extraction and scoring handle the broad pool. A serialized local
LLM enrichment task may run only when all are true:

- deterministic routing yields prospective Tier B/C;
- a missing critical fact could change tier;
- snapshot/parser-version cache has no valid enrichment;
- the run has used fewer than 20 LLM enrichments.

The LLM returns schema-validated facts with exact JD excerpts. Invalid or
unsupported output is discarded. The LLM never sees authority to approve,
reject, select, or create candidate evidence.

## 6. Structured feedback and bounded learning

V2 separates three learning questions:

1. **Worth-review/preference:** would Arya spend time reviewing or applying?
2. **Candidacy calibration:** do applications reach screens, interviews, and
   offers?
3. **Posting/source trust:** is the listing duplicate, stale, misleading,
   incomplete, or broken?

A skip does not automatically mean poor candidacy. A rejection reason is
required when the judgment is `not_worth_reviewing`.

### 6.1 Review feedback schema

```text
judgment:
  apply_now | worth_reviewing | stretch_but_interesting | not_worth_reviewing
action: applied | saved | skipped
evaluation_id: UUID
target_id: str
primary_reason: reason code
secondary_reasons: list[reason code]
free_text: optional str
learnable: bool  # derived, not client-controlled
created_at: timestamp
```

Reason taxonomy:

| Group | Codes | Learns into |
|---|---|---|
| Role | `wrong_role_family`, `wrong_work_content`, `domain_mismatch` | role taxonomy / role component |
| Candidacy | `too_senior`, `insufficient_direct_experience`, `missing_required_capability`, `missing_credential`, `high_selectivity_low_odds` | level/evidence/attainability calibration |
| Preference | `location_or_work_mode`, `compensation`, `employer_not_interested`, `travel`, `employment_type` | preference only |
| Posting | `stale_or_ghost`, `broken_apply_link`, `misleading_posting`, `unknown_employer` | posting/source quality |
| Process | `duplicate`, `already_seen`, `already_applied`, `closed`, `timing`, `no_time_today`, `materials_bad` | no fit learning |
| Weak signal | `not_interested_unspecified` | quarter-weight preference signal only |

The UI offers one-click common reasons and optional detail. `materials_bad` is
essential: poor generated materials must not teach the job matcher that a good
job is bad.

### 6.2 Initial learning algorithm

V2 launches with deterministic scoring and smoothed preference priors, not a
trained ranker.

- Historical binary labels without reasons have effective weight 0.25.
- Duplicate/closed/already-seen/timing/materials reasons have weight 0 for job
  fit.
- Feedback is target-specific; analyst feedback cannot change AI scoring.
- Canonical duplicates and regional copies contribute at most one label per
  decision episode.
- Apply a 180-day half-life.
- Use a Beta-style shrunk posterior:

```text
posterior_rate =
  (weighted_positive + 10 * relevant_target_base_rate)
  / (weighted_total + 10)
```

- Require effective sample size 5 for employer or title-family adjustment and
  8 for employer × target.
- Bound any one prior to ±3 review-index points and all feedback to ±5.
- Feedback cannot change hard eligibility, route ownership, component floors,
  or turn C into A/B.
- Source-level adjustment is disabled until there are at least 20 clean,
  target-specific decisions and source identity coverage is repaired.
- Keep a 10% exploration share among eligible Tier B candidates.
- Every adjustment records sample size, prior, posterior, reason mix, and point
  contribution. Arya can reset it globally, by target, or by employer.

Do not fit an interview-probability model until at least 100 submitted
applications and 20 screens exist with adequate negative follow-up. Even then,
train and evaluate that outcome model separately from preference ranking.

## 7. Source acquisition and health architecture

### 7.1 Decouple acquisition from target evaluation

One acquisition run refreshes due endpoints and query arms, persists normalized
postings/snapshots, and records provenance. All targets then retrieve from the
same acquisition snapshot. An interactive run may reuse an acquisition snapshot
up to six hours old unless `force_refresh` is requested.

Whole-board ATS fetches remain recall-oriented. Target retrieval still narrows
those boards before candidacy scoring, preserving the keyword-narrowing
invariant. The difference is that the complete normalized snapshot is persisted
once, making replay and a changed target possible without another network call.

### 7.2 Typed provenance

Rename the conceptual `ATSType` to `SourceType`; retain `ATSType` as a migration
alias. Add `smartrecruiters`, `workable`, and `recruitee` when those adapters
ship.

`JobProvenanceV2` fields:

```text
adapter
channel: direct_ats | aggregator | community_board | employer_site | manual_import
endpoint_id
query_arm_ids[]
source_record_url
listing_url
provider_published_at / provider_updated_at
publisher_relationship:
  employer_verified | employer_claimed | third_party_aggregator |
  recruiter_claimed | unknown
description_completeness: full | partial | snippet | missing
application_target:
  original_url
  resolved_url
  kind: direct_ats | employer_site | aggregator_redirect |
        recruiter_contact | email | missing | unknown
  resolution_status
  verified_at
parser_confidence
observed_at
```

Observed `source` and `source_id` remain immutable provider identity. Resolving
an Adzuna redirect to Greenhouse does not rewrite the observed source; it adds
provenance and possible-duplicate evidence.

### 7.3 Provider-neutral fetch result

Every adapter returns `SourceFetchResult`:

```text
fetch_run_id, endpoint_id or query_arm_id, adapter
started_at, finished_at, status, http_status
provider_records, normalized_records, malformed_records
records: list[RawJob]
error_code, error_detail, retry_after
response_schema_version
```

Provider records, normalized jobs, source-identity duplicates, persisted new,
persisted refreshed, and cross-source cluster members are different counters.
Reports never call all of them “raw jobs.”

### 7.4 Durable endpoint health

Dynamic endpoint state moves from mutable `companies.yaml` into PostgreSQL.
`companies.yaml` becomes an idempotent seed and V1 rollback artifact; runtime
discovery never edits it.

Endpoint states:

`candidate → active ↔ degraded → quarantined` and `active → dormant`;
`blocked` and `retired` require explicit policy/manual action.

Transitions:

- first schema-valid nonempty response: candidate → active;
- one transient network error: remain active;
- first 404, malformed response, or schema drift: active → degraded;
- three consecutive 404/malformed/schema failures spanning at least 24 hours:
  degraded → quarantined for seven days;
- valid empty response is success, not failure;
- seven consecutive daily empty responses or 14 days without nonempty output:
  active → dormant and probe weekly;
- 429 honors `Retry-After` and does not lower health;
- terms/robots/account restriction: blocked immediately;
- quarantined/dormant endpoints receive weekly canaries;
- one successful recovery: quarantined → degraded;
- two successful recovery probes: degraded → active or dormant.

No endpoint is automatically deleted. Sentry, Gong, and `p-1` import as degraded
based on known repeated 404 evidence, then follow the normal state machine.

### 7.5 Current-source decisions

| Source | Decision | V2 policy |
|---|---|---|
| Greenhouse | retain | Fetch once; endpoint health; no automatic fit bonus |
| Lever | retain | Same |
| Ashby | retain | Same; Ashby does not imply startup |
| Workday | retain and curate more tenants | Fetch list once; retrieve expensive details only after target retrieval |
| Adzuna | retain as primary query source | Rotate query arms; preserve query/rank; classify snippets/redirects honestly; shortlist full-JD recovery |
| HN monthly hiring | retain | community/employer-claimed; not automatically startup |
| HN `jobstories` | retain separately | high-confidence YC/startup evidence; normal candidacy floor |
| Remotive | dormant initially | weekly canary; reactivate after two materially fresh successful probes |
| LinkedIn automation | blocked | never part of `all`; manual URL import only |
| Wellfound automation | blocked | manual JD/direct URL or user-owned alert import only after compliance review |
| YC public job pages | manual/compliance-review lane | do not add automated harvesting without a documented terms/robots decision |
| `unknown` | data-quality state | repair bindings; exclude low-confidence inference from source learning |

Adzuna's small historical sample has a high take rate, so V2 does not impose a
blanket aggregator penalty. It removes silent pre-normalization staffing drops;
staffing becomes explicit classification and a reasoned target decision.

### 7.6 Query arms instead of YAML-order starvation

Query sources use versioned `source_query_arms`. For each target, title-family
variants combine with US remote, Dallas, Portland, and broad-US scopes where the
provider supports them.

- Run round-robin coverage for the first eight acquisition cycles.
- Record cost, provider results, unique postings, role routes, A/B evaluations,
  review judgments, and applications per arm.
- Only after eight cycles may shrunk useful-yield weights affect frequency.
- No arm disappears; low-yield arms retain exploration canaries.
- Adzuna's call budget rotates across arms rather than always taking the first
  ten YAML keywords.
- Full-JD recovery and redirect resolution are reserved for roughly the top
  20–30 deterministic candidates per target.

### 7.7 Official adapter expansion

Implement an adapter only after identifying at least ten target-affine employers
that use it.

1. **SmartRecruiters** — public per-company listing/detail endpoints and useful
   structured level/location/function fields. Run an anonymous-access conformance
   spike before implementation because its documentation discusses multiple
   authentication contexts. Authority:
   `https://developers.smartrecruiters.com/docs/endpoints`.
2. **Workable** — documented public published-job endpoint
   `https://www.workable.com/api/accounts/{subdomain}?details=true`; verify stable
   IDs, pagination, description, and apply URL in the conformance spike.
   Authority:
   `https://help.workable.com/hc/en-us/articles/115012771647-Using-the-Workable-API-to-create-a-careers-page`.
3. **Recruitee Careers Site API** — public careers offers endpoint; capture both
   careers and apply URLs. Authority:
   `https://support.recruitee.com/en/articles/1066282-api-documentation`.
4. **More verified Workday tenants** — likely faster value than a fourth new
   adapter and especially relevant to implementation/analyst/customer-success.

Each adapter is separately feature-flagged and requires official-doc/terms
record, fixtures, pagination, stable identity, empty/malformed behavior, and one
opt-in live conformance test.

### 7.8 Employer cohort strategy

Before adapter expansion affects scheduling, curate a pilot of at least 35
net-new employers and 50 target/employer affinity links (at least ten links per
target):

- ≥60% standard-selectivity employers;
- ≤15% exceptional/high-selectivity employers;
- ≥25% startup/growth employers;
- at least three employers per AI/implementation/analyst/customer-success target
  in healthcare, education, workflow automation, RevOps/customer operations, or
  vertical SaaS where Arya's evidence creates an advantage;
- every link has an official endpoint, target rationale, dated lifecycle and
  selectivity evidence, and active health status.

Aggregator/HN jobs that Arya reviews or applies to can propose a new employer
endpoint. Recognized ATS URLs create `candidate` endpoints, never immediately
active ones.

## 8. Employer and posting assessment

Employer attributes are independent dimensions, not one enum:

```text
employment_relationship:
  direct_employer | staffing_intermediary | employer_of_record | unknown
business_model:
  product_company | consultancy_professional_services |
  government_contractor | nonprofit_education | public_sector | unknown
lifecycle:
  startup | growth | established | unknown
funding_stage:
  pre_seed | seed | series_a | series_b | series_c_plus |
  bootstrapped | public | unknown
selectivity_tier:
  exceptional | high | standard | unknown
confidence, evidence[], classifier_version, assessed_at
```

A consultancy can be a direct employer. A government contractor can be viable.
Startup and selectivity are independent.

Posting assessment fields:

- publisher relationship;
- application-target kind and resolution;
- description completeness;
- endpoint health;
- provider date and confidence;
- parser confidence;
- requisition specificity;
- evergreen risk: low/medium/high/unknown;
- integrity problems and evidence;
- classifier version.

High evergreen risk requires at least two signals, such as explicit talent-pool
language, >90 days unchanged, repeated close/reopen with identical content, or
generic multi-role text plus no requisition/apply target. Age alone never hard-
rejects a job.

Classification precedence is manual override, verified registry evidence,
recognized direct domain/ATS evidence, deterministic rules, then unknown. Broad
funnel classification does not use an LLM.

## 9. Global portfolio selection

### 9.1 Capacity and quality

Default `config/portfolio.yaml`:

```yaml
schema_version: 2
id: default
core_capacity: 20
minimum_core_tier: B
per_target_max: 5
company_max: 1
startup_bonus:
  capacity: 5
  minimum_tier: B
  company_max: 1
  per_target_max: 2
exploration:
  tier_b_share: 0.10
stretch:
  enabled: false
  capacity: 0
```

Capacities are ceilings, never fill requirements. If only seven A/B jobs exist,
the run selects seven core jobs. The run does not lower standards.

The five startup jobs are additional slots. Core startups do not reduce the
five-bonus capacity; bonus selection excludes already-selected jobs. If fewer
than five additional A/B startups exist, the report shows the actual number.

### 9.2 Duplicate and occurrence handling

- Preserve every observed source posting and every possible-duplicate member.
- Group for selection using current conservative canonical fingerprint plus
  exact requisition/direct URL/description evidence when available.
- Never auto-merge or delete fuzzy members.
- Choose a representative occurrence by: verified live direct apply target,
  full JD, freshest verified state, higher provenance confidence, then stable
  source/company/title tie-break.
- One canonical group can create one card per run.

### 9.3 Cross-target ownership

A job can have evaluations for multiple targets but only one primary owner.

Ordering:

1. higher tier;
2. higher review index;
3. higher role component;
4. higher target priority;
5. stable target ID.

If the top two review indices are within five points, show the secondary target
on the card, but do not generate a second card or materials set.

### 9.4 Deterministic selection utility

Within a tier, start with review index and apply bounded portfolio modifiers:

- underrepresented-target bonus: 0–4 points based on target priority and current
  selected share;
- company concentration: hard cap 1 by default;
- canonical duplicate: hard suppression after representative selection;
- exploration bonus: 2 points for eligible Tier B jobs chosen by the seeded
  exploration sampler;
- source transport: no modifier;
- startup: no core modifier and no fit-score multiplier.

Process all Tier A before Tier B. Portfolio modifiers cannot cross tiers.
Selection is seeded by run ID where randomness is needed, making replay exact.

### 9.5 Concurrency

Selection/reservation must use a PostgreSQL transaction and advisory lock keyed
by tenant + portfolio ID. The current module-level threading lock is insufficient
for future multiple worker processes. Insert portfolio decisions and pending
review reservations in the same transaction. Existing pending partial uniqueness
remains a final idempotency guard.

## 10. Persistence and analytics model

All changes are additive in the first migration series.

### 10.1 New tables

`source_endpoints`

- tenant, adapter, normalized endpoint key unique;
- employer link, careers URL, adapter config;
- discovery provenance;
- state/compliance/manual override;
- latest health timestamps and next probe.

`source_endpoint_runs`

- endpoint/fetch IDs;
- explicit status including nonempty, empty, 404, forbidden, rate limit,
  timeout, network, malformed, schema drift;
- HTTP status/counts/duration/signature/error/retry-after.

`source_query_arms` and `source_query_runs`

- target, adapter, query/geography, state, version, call cost;
- run metrics and SearchQuery linkage.

`employers` and `employer_assessments`

- normalized identity/aliases;
- versioned multi-dimensional classifications, evidence, confidence, override.

`job_quality_assessments`

- snapshot + classifier version unique;
- posting/provenance/evergreen/directness facts and explanations.

`discovery_runs`

- one acquisition snapshot boundary, mode/config hash, start/finish/status.

`job_target_evaluations`

- snapshot, target ID/version, candidate version, parser/taxonomy/model versions;
- stage status, component scores/confidence, indices, tier, explanation JSON;
- unique on snapshot + target + full version tuple.

`job_evaluation_reasons`

- normalized stage, decision, reason code, severity, evidence/details;
- indexed by run, target, reason, and evaluation.

`portfolio_runs` and `portfolio_decisions`

- portfolio version/config hash/mode;
- evaluation, canonical group, owned target, lane, utility, rank, selected flag,
  reason codes, reservation/review ID.

`review_feedback`

- review/evaluation link, judgment, action, target, primary/secondary reasons,
  learnable flag, free text, model version.

`evaluation_sets` and `evaluation_items`

- frozen replay/blind-set definitions, snapshot/evaluation links, hidden arm,
  presentation order, judgment, and timestamps.

### 10.2 Existing-table additions

- `review_queue.evaluation_id` and `portfolio_decision_id`;
- `applications.evaluation_id`;
- `funnel_events.evaluation_id` / journey key;
- provenance linkage from posting observations to endpoint/query runs.

Do not replace existing `(tenant, source, source_id)` identity or snapshot
immutability.

### 10.3 Operational versus business funnel

Operational attrition comes from evaluation/reason rows, not `funnel_events`.
Counts reconcile from provider record through selection.

Business funnel stages become:

`surfaced → reviewed → applied → screen → interview → offer`

Discovery/eligible volumes remain operational metrics because one job can be
evaluated against several targets. Every business event is bound to the same
`evaluation_id` journey. Weekly conversion uses the **surfaced-week cohort** and
asks what later outcomes that cohort reached; it does not divide independent
event counts that happened in the same calendar week. Existing events remain
readable but are labeled legacy/non-cohort unless they can be bound confidently.

### 10.4 Exact reconciliation metrics

Per acquisition endpoint/query arm:

`provider → normalized → malformed → source-identity duplicate → persisted new /
refreshed → possible duplicate member`

Per target:

`retrieved → global eligibility fail/unknown/pass → route fail/pass → evaluated
→ Tier A/B/C/D → history exclusion → duplicate representative → owned →
portfolio eligible → selected`

Every delta is backed by reason rows. Reports derive totals; they never maintain
independent counters that can drift.

## 11. Replay, prospective evaluation, and release gates

### 11.1 Historical replay

`scripts/replay_job_pool_v2.py` evaluates frozen snapshots with V1 and V2
without network calls, material generation, review writes, or outcome mutation.

Dataset construction:

- bind review decisions to the pinned snapshot and target/evaluation where
  possible;
- group canonical/possible duplicates and regional copies;
- order chronologically and split earliest 70% development / newest 30% holdout;
- never allow versions of one posting or one decision episode across the split;
- label unverifiable historical source/target bindings and exclude them from the
  affected metric rather than guessing;
- retain all rows for data-quality reporting.

Metrics overall and per target:

- worth-review/apply precision at 5, 10, and portfolio capacity;
- Tier A/B positive rate and A→B→C monotonicity;
- recall of historically applied jobs;
- normalized discounted cumulative gain from structured judgments;
- false-negative sample and reason distribution;
- hard/unknown gate rates;
- source/employer/provenance coverage;
- company and target concentration;
- duplicate rate;
- component/confidence missingness;
- score/tier stability when only source occurrence changes.

Historical binary labels diagnose regressions but do not by themselves approve
cutover.

### 11.2 Prospective blinded review

- Freeze target, taxonomy, parser, and scorer versions first.
- Collect at least 50 unique candidates, aiming for ten per target when actual
  Tier A/B supply permits.
- Hide model arm, source preference, Story Fit, Candidacy, Review Index, tier,
  and portfolio selection.
- Randomize order within target with a stored seed.
- Ask for the four-valued judgment and structured reasons.
- Include a stratified false-negative sample from V2 C/D and V1-only choices.
- Report Wilson or beta-binomial intervals, not raw percentages alone.

Primary product gate: at least 60% of V2 surfaced A/B jobs are judged
`apply_now` or `worth_reviewing` across the frozen set. Secondary gate: top tier
beats the marginal tier by at least 20 percentage points when both samples are
adequate. A target with insufficient A/B supply fails a supply diagnostic; its
threshold is not lowered automatically.

### 11.3 Shadow and canary sequence

1. **Offline:** schema fixtures and historical replay.
2. **`v2_shadow`:** at least seven acquisition cycles. V2 persists evaluations
   and portfolio decisions but creates no review rows and no materials.
3. **Blinded prospective review:** user labels frozen V1/V2 samples.
4. **Canary:** V2 creates review cards for one target or at most five global
   candidates; V1 remains operational for the rest.
5. **Full V2:** one global portfolio run creates cards/materials.
6. **V1 retirement:** only after two weeks without rollback and acceptance
   metrics remain healthy.

Rollback at any point sets `matching.pipeline_version: v1`. New evaluation and
source data remain for diagnosis; no destructive down migration is required.

## 12. Phased implementation plan for smaller models

Each ticket is intentionally bounded. An implementation agent must read
`AGENTS.md`, this document, and the files named in its ticket. It may not reopen
the product decisions above.

### V2-00 — Baseline freeze, feature flags, and correctness fixtures

Objective: make the current behavior reproducible and capture known blockers
before new architecture changes it.

Files:

- `config/settings.yaml.example` and user config compatibility loader;
- new `tests/fixtures/job_pool_v2/`;
- `tests/test_matching.py`;
- `tests/test_quality_recalibration.py`;
- `scripts/audit_plan_quality.py`;
- documentation only in `docs/DECISIONS.md`.

Work:

- add `matching.pipeline_version: v1|v2_shadow|v2`, default `v1`;
- freeze representative fixtures for every target, missing data, work auth,
  location, duplicates, source types, and historical selected failures;
- snapshot current replay/audit output;
- add tests demonstrating, without fixing yet, the free-form permanent-resident
  comparison, duplicate YAML key, short-token fuzzy match, missing-skill 0.5,
  and software-only role-family limitations.

Tests: fixture determinism; current audit snapshot reproducibility; one
expected-failure fixture per named defect; existing V1 matching/plan tests.  
Dependencies: none.  
Migration: none.  
Rollback: remove/ignore feature flag; V1 remains default.  
Complete when: fixtures are deterministic and the known-defect tests are
explicitly marked expected-failure with issue/ticket references.

### V2-01 — Canonical candidate, taxonomies, and target compiler

Objective: create one evidence authority and five validated target specs.

Files:

- new `src/matching/profile_v2.py` and `src/matching/target_schema.py`;
- new `data/profile/candidate.yaml`;
- new `config/targets/*.yaml`;
- new `config/taxonomies/roles.v1.yaml` and `capabilities.v1.yaml`;
- `src/application/profile.py` compatibility adapter;
- new `scripts/migrate_profiles_v2.py`;
- new `tests/test_target_profiles_v2.py`.

Work:

- implement strict Pydantic schemas and duplicate-key-safe YAML loading;
- add stable evidence IDs and validate references/allowed uses;
- encode the five target decisions and initial capability weights;
- compile a `ResolvedTargetProfile` with candidate/target/taxonomy hashes;
- keep legacy profile files unchanged; provide read-only compatibility output.

Tests: strict/duplicate-key schema rejection, stable/dangling evidence IDs,
canonical hash stability, five materially different compiled targets, and V1
compatibility output.  
Dependencies: V2-00.  
Migration: file migration only; script is dry-run by default and writes a new
candidate file only with explicit flag.  
Rollback: V1 continues to read existing profiles.  
Complete when: all five targets resolve from one candidate hash, produce
materially different target hashes/features, no dangling references exist, and
legacy generation tests remain green.

### V2-02 — Job facts V2 and tri-state eligibility

Objective: replace positive missing-data defaults and expand the role/capability
ontology without changing V1 behavior.

Files:

- `src/intake/schema.py`;
- `src/intake/jd_parser.py`;
- new `src/matching/job_facts.py`;
- `src/matching/rules.py` behind V2 path;
- `src/application/jobs.py` integration;
- parser/rule tests plus new `tests/test_job_facts_v2.py`.

Work:

- implement typed fact/evidence/confidence schema;
- add customer-facing role families and responsibility/capability extraction;
- normalize authorization enums and fix permanent-resident behavior in V2;
- make missing skill/years/location unknown, never positive 0.5;
- use token-boundary/taxonomy aliases rather than substring fuzzy matching;
- implement tri-state eligibility with reason codes;
- retain backend/frontend whole-word location behavior and LinkedIn exception.

Tests: parser fixtures for all five role families; pass/fail/unknown gate matrix;
permanent-resident, duplicate-location, short-token, missing-fact, location
alias, and LinkedIn-exception regressions; V1 compatibility tests.  
Dependencies: V2-01.  
Migration: facts initially stored in evaluation JSON; no destructive change.  
Rollback: feature flag routes to V1 parser/rules.  
Complete when: all correctness blockers have passing V2 tests, every fact cites
an excerpt/source, and V1 tests still pass.

### V2-03 — Evaluation and reason ledger

Objective: persist reproducible target evaluations and reconcile operational
attrition.

Files:

- `src/core/models.py`;
- new Alembic migration;
- new `src/matching/evaluation_store.py`;
- task/application serialization;
- new `tests/test_job_evaluation_models.py`.

Work:

- add `discovery_runs`, `job_target_evaluations`, and
  `job_evaluation_reasons`;
- enforce full version-tuple uniqueness;
- make writes idempotent and immutable for the same version tuple;
- expose plain-dict use cases; do not couple route handlers to ORM.

Tests: migration upgrade, uniqueness race, idempotent replay, reason-code schema,
stage terminality, and exact run/pair/reason reconciliation.  
Dependencies: V2-01 and V2-02 schema contracts.  
Migration: additive tables/indexes only.  
Rollback: stop writes; tables remain.  
Complete when: rerunning identical evaluation creates no duplicate and every
stage delta reconciles from reason rows.

### V2-04 — Typed provenance, endpoint registry, and health

Objective: decouple source observation from employer/posting quality and replace
mutable runtime board YAML.

Files:

- `src/intake/schema.py` and all current adapters;
- `src/core/models.py` + additive migration;
- new `src/intake/source_registry.py` and `source_health.py`;
- new `scripts/import_companies_registry.py`;
- adapter contract, registry, and health tests.

Work:

- add `SourceType`, provenance/application-target/completeness contracts;
- populate provenance in every existing adapter;
- add endpoint and endpoint-run tables/state machine;
- idempotently import all `companies.yaml` entries;
- seed known recurring 404s as degraded, not deleted;
- distinguish valid empty, 404, malformed, 429, timeout, and recovery.

Tests: shared contract fixtures for every retained adapter; registry import
idempotency; all health transitions/timers; `Retry-After`; valid empty; recovery;
and proof that configuration is never silently deleted or rewritten.  
Dependencies: V2-00; can run parallel with V2-01/02 if schema coordination is
agreed first.  
Migration: additive; YAML stays intact.  
Rollback: `source_registry.enabled=false` restores YAML reads.  
Complete when: shared contract tests pass for every adapter, state transitions
match Section 7, and no runtime path writes `companies.yaml`.

### V2-05 — Single acquisition refresh and target retrieval

Objective: fetch once and evaluate all targets against one indexed snapshot.

Files:

- `src/application/jobs.py`;
- `src/intake/search.py`;
- `src/jobs/search.py` and `store.py`;
- `src/tasks/tasks.py`;
- new `src/orchestration/portfolio_run.py`;
- automation scheduling and acquisition tests.

Work:

- create one acquisition run boundary;
- refresh due endpoints/query arms once;
- persist complete normalized board snapshots;
- compile target retrieval predicates from target specs;
- preserve search-result pruning, distributed lock, source identity, keyword
  narrowing, and deep-copy cache invariants;
- no broad-funnel LLM calls.

Tests: one fetch per due endpoint, one immutable acquisition snapshot across all
targets, query/link attribution, keyword/location invariants, cache deep-copy
isolation, partial source failure, and reconciled five-target counts.  
Dependencies: V2-01, V2-02, V2-03, V2-04.  
Migration: additive endpoint/query links to existing posting/search records.  
Rollback: flag routes plans to current per-plan acquisition.  
Complete when: five-target rehearsal performs one board refresh, evaluates one
acquisition snapshot, and all count boundaries reconcile.

### V2-06 — Employer and posting quality assessments

Objective: replace source assumptions and regex blocklists with versioned,
evidence-backed dimensions.

Files:

- new `src/jobs/employers.py` and `src/jobs/quality.py`;
- `src/core/models.py` + additive migration;
- `src/application/jobs.py`;
- assessment/backfill tests.

Work:

- add employers, aliases, employer assessments, and job quality assessments;
- implement deterministic classification precedence/evidence/confidence;
- implement bounded attainability and posting trust inputs;
- retain provisional blocklists behind flag until parity review;
- backfill only evidence-supported historical source/employer facts.

Tests: classification precedence and evidence citations; independent transport,
employer, startup, selectivity, directness, and posting-trust dimensions;
confidence/unknown behavior; and dry-run/idempotent backfill.  
Dependencies: V2-02, V2-04.  
Migration: additive versioned assessment tables.  
Rollback: stop V2 classifier and retain provisional gates.  
Complete when: dimensions are independent, no global source bonus exists, and
≥80% of rehearsal cards have known publisher/application/employer/posting
metadata.

### V2-07 — Deterministic evaluator and explanations

Objective: implement the component formulas, tiers, confidence, and optional
bounded enrichment exactly as documented.

Files:

- new `src/matching/scorer_v2.py` and `explanations_v2.py`;
- `src/matching/semantic.py` only for bounded auxiliary similarity;
- task cache integration;
- comprehensive `tests/test_scorer_v2.py` and fixture matrix.

Work:

- calculate R/L/E/D/A/P/Q, Story Fit, Candidacy, Review Index, feedback
  adjustment, floors, caps, tiers, confidence, strengths, and gaps exactly as
  defined in Section 5;
- cache by snapshot + full version tuple;
- reserve local LLM for at most 20 schema extraction adjudications;
- never let embeddings or LLM override explicit contradiction/evidence;
- make formula contributions exactly reproducible.

Tests: exact arithmetic and rounding fixtures; component floors/caps; missing
data/confidence/tier caps; explanation evidence references; target-different
ranking; bounded feedback; cache invalidation by every version field; and
serialized 20-call enrichment budget.  
Dependencies: V2-01, V2-02, V2-03, V2-06.  
Migration: none beyond evaluation tables.  
Rollback: V1 scorer untouched; change flag.  
Complete when: controlled fixtures produce target-different tiers, startup does
not change candidacy, missing data cannot inflate scores, and every total
recomputes from persisted components.

### V2-08 — Global portfolio selector and transactional reservation

Objective: replace five independent Top-N runs with one quality-limited global
portfolio.

Files:

- new `src/orchestration/portfolio.py`;
- `src/orchestration/portfolio_run.py`;
- `src/core/models.py` + additive migration;
- `src/application/review.py` reservation integration;
- portfolio/idempotency/concurrency tests.

Work:

- add portfolio runs/decisions;
- choose canonical representative and one target owner;
- implement A-before-B, capacity, target balancing, company cap, exploration,
  and separate five-startup bonus;
- use PostgreSQL advisory lock and one transaction for decisions/reservations;
- leave slots empty below Tier B.

Tests: deterministic seeded selection; A-before-B; target/company/canonical
caps; possible-duplicate suppression without merge; insufficient supply;
five-additional-startup semantics; history exclusions; idempotency; and a real
PostgreSQL concurrent-reservation test.  
Dependencies: V2-03, V2-05, V2-07.  
Migration: additive tables and nullable review links.  
Rollback: V1 plan runner unchanged.  
Complete when: replay is deterministic, concurrent runs create no duplicate
cards, no company/cluster exceeds cap, and startup bonuses never displace core.

### V2-09 — Structured review feedback and journey binding

Objective: collect clean learnable judgments and repair cohort identity.

Files:

- `src/core/models.py` + additive migration;
- `src/application/review.py`, `tracking.py`, and `funnel.py`;
- `src/web/routes/review.py`;
- `frontend/src/views/ReviewQueueView.vue`;
- API/frontend/funnel tests and SPA rebuild.

Work:

- add review feedback and evaluation links to review/application/funnel;
- implement one-click reason taxonomy and material-specific reason;
- derive learnable flag server-side;
- compute surfaced-week cohorts by evaluation journey;
- retain legacy analytics labeled non-cohort where binding is uncertain.

Tests: API validation for every reason; required negative primary reason;
fit-versus-process learnability; material-failure isolation; evaluation journey
binding; cohort week boundaries; legacy payload compatibility; and frontend
interaction tests.  
Dependencies: V2-03, V2-08.  
Migration: additive nullable columns/table; no historical invention.  
Rollback: reason UI optional, old approve/reject payload remains accepted.  
Complete when: every new skip has a primary reason, process/material reasons do
not train fit, and cohort counts follow one evaluation journey.

### V2-10 — Replay and blinded evaluation harness

Objective: prove V2 quality before queue cutover.

Files:

- new `src/evaluation/job_pool.py`;
- new `scripts/replay_job_pool_v2.py` and `scripts/build_blind_job_set.py`;
- evaluation models/migration if not in V2-03;
- evaluation tests and fixtures.

Work:

- build duplicate-grouped temporal replay;
- compute all Section 11 metrics and V1 comparison;
- create seeded blinded sets and record judgments;
- prevent leakage across posting versions/duplicates.

Tests: temporal and canonical-group split isolation; fixed-seed byte-for-byte
replay; hidden model/tier/score fields; confidence intervals; tier monotonicity;
and V1/V2 comparison on identical snapshots.  
Dependencies: V2-07 and preferably V2-09 for prospective labels.  
Migration: additive evaluation-set tables.  
Rollback: read-only tooling.  
Complete when: replay is byte-for-byte reproducible from frozen inputs and the
prospective set hides model identity/tier/score.

### V2-11 — Shadow reports and operator UI

Objective: make V2 auditable without changing live queue behavior.

Files:

- plan/report API use cases and routes;
- `scripts/audit_plan_quality.py` compatibility;
- Plans/Jobs frontend views;
- report and UI tests; SPA build.

Work:

- add operational Sankey/count table, source endpoint health, target tier
  supply, component explanations, and V1/V2 comparison;
- clearly label shadow decisions;
- show selected attempts versus successful reservations/cards;
- surface unresolved candidates and missing data.

Tests: API count reconciliation, reason drill-down, shadow write isolation,
selected-versus-reserved distinction, unresolved rendering, nontechnical labels,
and production SPA build.  
Dependencies: V2-05, V2-07, V2-08, V2-10.  
Migration: none.  
Rollback: hide V2 routes/UI.  
Complete when: a nontechnical operator can answer where every job was lost and
why each proposed card was selected.

### V2-12 — Query-arm scheduler and source-history repair

Objective: remove keyword-order starvation and make source learning attributable.

Files:

- new query scheduler module;
- `src/application/data_backfill.py`;
- `scripts/sourcing_health.py`;
- source/rotation/backfill tests.

Work:

- add query-arm/run tables if not already created;
- implement eight-cycle round-robin then shrunk useful-yield weighting;
- attribute outcomes through evaluation and query links;
- repair historical source only with evidence and confidence;
- exclude low-confidence inferred rows from priors.

Tests: eight-cycle round-robin, later-arm non-starvation, deterministic shrunk
weighting, exploration canaries, outcome attribution, dry-run/idempotent
backfill, and exclusion of low-confidence historical repairs.  
Dependencies: V2-04, V2-05, V2-09.  
Migration: additive links/backfill; idempotent and dry-run report first.  
Rollback: fixed query order feature flag.  
Complete when: later aliases cannot starve and source reports exclude
unattributable labels.

### V2-13A/B/C — Official source adapters

Three independent tickets in this order: SmartRecruiters, Workable, Recruitee.

Files per ticket:

- one `src/intake/<adapter>.py`;
- schema registration/config flag;
- fixture tests and optional live conformance test;
- endpoint seed entries only after curation.

Tests per ticket: recorded official-response fixtures, pagination, stable ID,
normalization, detail/apply URLs, empty/malformed/429/timeout states, health
integration, and one opt-in anonymous live conformance probe.  
Dependencies: V2-04/05 and ten verified target-affine employers for that adapter.
Migration: additive connector registration and endpoint seeds only; no source
enum rewrite or destructive job backfill.  
Rollback: disable only that adapter; retain observations and endpoint health.  
Complete when: official access is verified; pagination, stable ID, full detail,
dates, location, apply URL, empty/malformed states, and health reporting pass;
one dry run demonstrates useful unique A/B supply, not just raw volume.

### V2-14 — Employer cohort curation

Objective: correct the prestigious-infrastructure bias in endpoint coverage.

Files:

- versioned employer seed artifact and validation script;
- no scraper behavior changes.

Work:

- curate 35 net-new employers and 50 target links under Section 7 constraints;
- store rationale and dated selectivity/lifecycle evidence;
- verify official endpoint and health;
- obtain Arya approval for cohort exceptions.

Tests: schema validation, exact cohort/target quotas, duplicate endpoint and
alias rejection, dated evidence requirements, and inactive-by-default behavior
for unapproved exceptions.  
Dependencies: V2-01 target schema and V2-04 registry.  
Migration: additive versioned seed import only; no existing endpoint mutation.  
Rollback: deactivate seed cohort; never delete endpoints.  
Complete when: constraints validate and each target has at least ten supported
links.

### V2-15 — Canary, cutover, and V1 authority retirement

Objective: activate V2 only after acceptance gates pass.

Files:

- settings and automation plans;
- `docs/DECISIONS.md`, `CHANGELOG.md`, `INFRASTRUCTURE.md`, and handoff docs;
- compatibility loaders and deprecation warnings.

Work:

- run shadow/blind gates;
- canary at most five cards;
- switch one global plan to `v2`;
- disable old five plans;
- after two stable weeks, stop treating legacy search/filter/profile role data
  as authorities, but keep migration readers for rollback window;
- restart web/worker only after tests and production build pass.

Tests: full targeted V2 suite, existing V1 regression suite, migration upgrade,
frontend production build, `v2_shadow` no-queue/no-materials assertion, canary
capacity assertion, V1 rollback smoke test, and dry global rehearsal.  
Dependencies: all core V2 tickets; source adapters are optional for initial
cutover if existing-source supply passes.  
Migration: none destructive.  
Rollback: set `pipeline_version: v1`, re-enable old plans.  
Complete when: acceptance criteria pass, no auto-submit invariant is unchanged,
and operating docs name only the V2 authorities.

## 13. Decisions requiring Arya's judgment

These are product preferences, not safe engineering inferences. Defaults below
allow implementation to proceed, but the UI/config must make them editable.

| Decision | V2 default pending confirmation |
|---|---|
| Onsite/hybrid geography outside Portland/Dallas | Reject; US-wide is remote-only despite `willing_to_relocate` |
| Compensation | $90k preferred, soft; unknown allowed; no hard floor until Arya sets one |
| Contract roles | Allowed but preference 0.70 versus full-time 1.00 |
| Availability before August 2026 graduation | Mark unresolved; do not use as a gate until corrected |
| Travel ceiling | Unknown; lower confidence when a role requires material travel |
| Founding/high-selectivity jobs | Tier C explore only unless all A/B floors and explicit early-career evidence pass |
| Staffing intermediaries | Visible but excluded from automatic portfolio by default; manual search still shows them |
| Consultancies/professional services | Allowed and assessed normally |
| Government contractors | Allowed only without citizenship/active-clearance conflict; risk explained |
| Core cards per run | Ceiling 20, never filled below Tier B |
| Startup bonus | Five additional A/B jobs, one company each, max two per target |
| Company cap | One per run; manual override available |
| Target priorities | implementation 1.0, AI/analyst 0.9, presales/customer-success 0.7 |
| Rename `tam` | Display as Technical Customer Success & Enablement; keep alias during migration |
| Rejection interaction | One required reason for negative judgment; optional secondary/free text |
| Wellfound/YC discovery | Manual URL/JD or user-owned compliant alerts only unless a future terms review approves automation |

Changing one of these values creates a new target/portfolio/preference version;
it does not require a new scoring architecture.

## 14. Final acceptance criteria

V2 may cut over only when all are true:

1. One canonical candidate hash feeds five materially different target specs and
   rankings.
2. Duplicate YAML keys, free-form authorization comparison, short-token fuzzy
   matches, positive missing-skill defaults, and software-only target taxonomy
   are corrected on the V2 path.
3. One acquisition refresh supplies all targets; source/search/cache invariants
   remain intact.
4. Every normalized job-target pair has a terminal stage and reason rows;
   aggregate counts reconcile exactly.
5. Every selected job is Tier A/B. Core and startup slots remain empty when
   supply is insufficient.
6. Startup bonuses are five additional slots and do not alter fit components.
7. One canonical group, one company, and one primary target consume at most one
   default card per run.
8. At least 90% of A/B cards have role, level, evidence, risk, provenance, and
   confidence explanations; at least 80% of all cards meet the broader metadata
   coverage requirement.
9. Endpoint health distinguishes empty, dead, degraded, rate-limited, dormant,
   and recovered without automatic deletion.
10. Historical replay is reproducible and leakage-safe.
11. A frozen blinded set of at least 50 yields ≥60% worth-review precision among
    V2 A/B jobs, with uncertainty reported.
12. Top and marginal tiers differ by at least 20 percentage points when samples
    are adequate; otherwise the result remains inconclusive, not passed.
13. Business conversion uses surfaced-week evaluation cohorts.
14. Broad-funnel evaluation uses no LLM. Shortlist enrichment is serialized,
    cached, capped at 20, and schema/evidence constrained.
15. V1 remains a one-setting rollback until two stable V2 weeks pass.
16. Resume generation remains evidence-grounded and every application remains
   explicitly human-approved.
17. The targeted V2 and existing V1 regression suites, migration upgrade,
    production SPA build, V1 five-plan dry rehearsal, and V2 global shadow
    rehearsal all pass before web or worker processes are restarted for cutover.

## 15. Superseded provisional behavior

After V2 cutover:

- hardcoded plan title regexes are replaced by versioned target taxonomy;
- the arbitrary 0.50 floor is replaced by component floors and tiers;
- regex employer blocklists are replaced by versioned assessment and target
  policy;
- exact company/title collapse is replaced by canonical-group portfolio
  suppression without destructive merging;
- the module-level plan lock is replaced by a PostgreSQL portfolio lock;
- five independent plans are replaced by one acquisition/portfolio run;
- current raw/source counters are replaced by reconciled stage ledgers;
- current startup score multiplier and “core startups count toward five” behavior
  are removed;
- `search_profiles.yaml`, `filters.yaml`, and copied role profiles cease being
  product authorities.

Until cutover, those guardrails stay in place behind V1. Smaller implementation
agents must not remove them early.
