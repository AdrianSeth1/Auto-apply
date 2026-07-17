"""One acquisition -> five evaluations -> one transactional V2 portfolio."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select

from src.application.review import CreateEntryArgs, create_entry
from src.core.config import PROJECT_ROOT, load_config
from src.core.database import get_session_factory
from src.core.models import (
    TENANT_DEFAULT,
    DiscoveryRun,
    JobPosting,
    JobSnapshot,
    PortfolioDecision,
    PortfolioRun,
    ReviewQueueEntry,
    SourceQueryArm,
    SourceQueryRun,
)
from src.intake.batch import load_company_list
from src.intake.query_scheduler import (
    QueryArmV2,
    build_query_arms,
    select_query_arms,
)
from src.intake.schema import JobRequirements, RawJob
from src.jobs.employers import assess_employer
from src.jobs.enrich import enrich_posting
from src.jobs.quality import assess_posting
from src.jobs.source_endpoints import record_endpoint_runs
from src.jobs.source_freshness import (
    reconstruct_endpoint_jobs,
    split_companies_by_freshness,
)
from src.jobs.store import JobIndexStore
from src.jobs.supply_activation import (
    SupplyRefreshRotation,
    apply_supply_refresh_rotation,
    load_supply_refresh_rotation,
)
from src.matching.evaluation_store import persist_evaluation
from src.matching.job_facts import extract_job_facts
from src.matching.pipeline import PipelineVersion, get_pipeline_version, writes_review_queue
from src.matching.profile_v2 import canonical_hash, load_candidate, load_targets, resolve_target
from src.matching.scorer_v2 import evaluate_job_target
from src.orchestration.portfolio import (
    PortfolioCandidateV2,
    load_portfolio_policy,
    select_portfolio,
)
from src.tasks.locks import advisory_lock

SearchFn = Callable[..., Awaitable[dict[str, Any]]]
EnqueueFn = Callable[[str, dict[str, Any]], str]


def _now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def _fail_discovery_on_exception(factory, run_id: uuid.UUID):
    """Keep the run ledger terminal even when normalization/persistence fails."""
    try:
        yield
    except Exception as exc:
        with factory() as failure_session, failure_session.begin():
            row = failure_session.get(DiscoveryRun, run_id)
            if row is not None:
                row.status = "failed"
                row.error = f"{type(exc).__name__}: {exc}"
                row.finished_at = _now()
        raise


def build_acquisition_request(
    resolved_targets: list,
    query_arms: list[QueryArmV2],
    *,
    force_refresh: bool,
    companies: dict[str, list] | None = None,
) -> dict[str, Any]:
    """One recall-oriented request; target gates happen after acquisition.

    ``companies`` (Phase S6 refresh cadence): when provided, overrides the
    default "load every configured board from companies.yaml" behavior with
    a caller-computed subset -- used to fetch only the ATS boards whose
    cached snapshot is stale (see ``_split_stale_and_fresh_companies`` /
    ``src.jobs.source_freshness``). Left ``None`` (the default) reproduces
    the pre-S6 behavior exactly: every configured board gets fetched.
    """

    keywords = list(
        dict.fromkeys(term for target in resolved_targets for term in target.discovery_terms)
    )
    return {
        "profile": None,
        "source": "all",
        "score": False,
        "keywords": keywords,
        "aggregator_keywords": [arm.query for arm in query_arms],
        "aggregator_query_arms": [
            {"query": arm.query, "geography": arm.geography} for arm in query_arms
        ],
        "use_llm": False,
        "include_views": True,
        "use_job_index": True,
        "include_remote_sources": True,
        "force_refresh": force_refresh,
        "companies": companies,
        "experience_levels": [],
        "employment_types": [],
        "location_types": [],
        "locations": [],
        "education_levels": [],
    }


def raw_job_from_payload(payload: RawJob | dict[str, Any]) -> RawJob:
    if isinstance(payload, RawJob):
        return payload.model_copy(deep=True)
    raw = dict(payload)
    requirements = raw.get("requirements") or (raw.get("raw_data") or {}).get("requirements") or {}
    raw["requirements"] = (
        requirements
        if isinstance(requirements, JobRequirements)
        else JobRequirements.model_validate(requirements)
    )
    raw.pop("match_score", None)
    raw.pop("best_profile", None)
    raw.pop("profile_scores", None)
    raw.pop("disqualified", None)
    for metadata_key in (
        "experience_level",
        "employment_category",
        "location_type",
        "education_level",
        "experience_years_min",
        "experience_years_max",
        "pay_min",
        "pay_max",
    ):
        raw.pop(metadata_key, None)
    return RawJob.model_validate(raw)


def _content(job: RawJob) -> dict[str, Any]:
    return {
        "title": job.title,
        "location": job.location,
        "employment_type": job.employment_type,
        "seniority": job.seniority,
        "description": job.description,
        "requirements": job.requirements.model_dump(mode="json"),
        "application_url": job.application_url,
        "raw_data": job.raw_data,
    }


def _default_enqueue(task_name: str, payload: dict[str, Any]) -> str:
    from src.tasks.app import celery_app

    return str(celery_app.send_task(task_name, kwargs=payload).id)


async def run_portfolio_v2(
    *,
    tenant_id: str = TENANT_DEFAULT,
    target_ids: list[str] | None = None,
    mode: PipelineVersion | None = None,
    force_refresh: bool = False,
    dry_run: bool = False,
    canary_capacity: int | None = None,
    search_fn: SearchFn | None = None,
    enqueue_fn: EnqueueFn | None = None,
    session_factory=None,
) -> dict[str, Any]:
    """Run V2. Shadow persists decisions but never creates cards/materials."""

    config = load_config()
    pipeline_version = mode or get_pipeline_version(config)
    if pipeline_version == "v1":
        return {
            "ok": False,
            "status": "disabled",
            "pipeline_version": "v1",
            "error": "V2 portfolio runner requires v2_shadow or v2",
        }
    all_targets = load_targets()
    wanted = set(target_ids or [target.id for target in all_targets])
    selected_targets = [target for target in all_targets if target.id in wanted or wanted.intersection(target.aliases)]
    if len(selected_targets) != len(wanted):
        resolved_names = {target.id for target in selected_targets} | {
            alias for target in selected_targets for alias in target.aliases
        }
        missing = wanted - resolved_names
        if missing:
            raise ValueError(f"Unknown V2 target IDs: {sorted(missing)}")
    candidate_profile = load_candidate()
    resolved_targets = [resolve_target(candidate_profile, target) for target in selected_targets]
    factory = session_factory or get_session_factory(config)
    acquisition_cycle = 0
    selected_arm_rows: list[SourceQueryArm] = []
    run_id = uuid.uuid4()
    seed = str(run_id)
    config_hash = canonical_hash(
        {
            "candidate": resolved_targets[0].candidate_version if resolved_targets else "",
            "targets": [target.target_version for target in resolved_targets],
            "pipeline": pipeline_version,
        }
    )

    generated_arms = build_query_arms(selected_targets)
    with factory() as session, session.begin():
        acquisition_cycle = int(
            session.scalar(
                select(func.count(DiscoveryRun.id)).where(
                    DiscoveryRun.tenant_id == tenant_id
                )
            )
            or 0
        )
        existing = {
            row.version: row
            for row in session.scalars(
                select(SourceQueryArm).where(SourceQueryArm.tenant_id == tenant_id)
            )
        }
        for arm in generated_arms:
            row = existing.get(arm.version)
            if row is None:
                row = SourceQueryArm(
                    tenant_id=tenant_id,
                    target_id=arm.target_id,
                    adapter=arm.adapter,
                    query=arm.query,
                    normalized_query=" ".join(arm.query.casefold().split()),
                    geography=arm.geography,
                    state="active",
                    version=arm.version,
                )
                session.add(row)
                session.flush()
                existing[arm.version] = row
        # Phase S7: priority per target, threaded onto each reconstructed arm
        # so select_query_arms can weight discovery budget toward the
        # targets Arya prioritized -- looked up from the same resolved
        # target list already loaded above, not a new source of truth.
        priority_by_target_id = {target.id: target.priority for target in selected_targets}
        schedulable = [
            QueryArmV2(
                id=str(row.id),
                target_id=row.target_id,
                adapter=row.adapter,
                query=row.query,
                geography=row.geography,
                version=row.version,
                run_count=row.run_count,
                useful_yield_positive=row.useful_yield_positive,
                useful_yield_total=row.useful_yield_total,
                active=row.state == "active",
                priority=priority_by_target_id.get(row.target_id, 1.0),
            )
            for row in existing.values()
            if row.target_id in {target.id for target in selected_targets}
        ]
        selected_arms = select_query_arms(
            schedulable,
            budget=15,
            acquisition_cycle=acquisition_cycle,
            seed=seed,
        )
        selected_ids = {uuid.UUID(arm.id) for arm in selected_arms}
        selected_arm_rows = [row for row in existing.values() if row.id in selected_ids]
        session.add(
            DiscoveryRun(
                id=run_id,
                tenant_id=tenant_id,
                mode="dry_run" if dry_run else pipeline_version,
                pipeline_version=pipeline_version,
                config_hash=config_hash,
                target_ids=[target.id for target in selected_targets],
                status="acquiring",
                started_at=_now(),
            )
        )

    if search_fn is None:
        from src.application.jobs import search_jobs

        search_fn = search_jobs
    # SUP-01B: caller-supplied out-parameter (see src.intake.search
    # docstring) -- populated in place by _fetch_board if search_fn honors
    # it, empty otherwise (e.g. test fakes that don't forward it). Never
    # fabricated when absent; record_endpoint_runs() below just writes
    # nothing for this run in that case.
    endpoint_metrics: list[dict[str, Any]] = []

    # Phase S6 refresh cadence: direct ATS boards refresh once daily / on
    # Run Plans Now only when their cached snapshot is >6h old. force_refresh
    # keeps its existing "bypass everything, re-fetch every board" meaning --
    # the split is simply skipped in that case, matching pre-S6 behavior.
    # See src.jobs.source_freshness module docstring for why this can't be a
    # plain "skip the fetch" -- fresh-but-skipped boards' postings still need
    # to be reconstructed from the Job Index below so they stay in this
    # run's candidate pool.
    fresh_reuse_keys: set[tuple[str, str]] = set()
    companies_override: dict[str, list] | None = None
    supply_rotation: SupplyRefreshRotation = load_supply_refresh_rotation(acquisition_cycle)
    if not force_refresh:
        all_companies = load_company_list(PROJECT_ROOT / "config" / "companies.yaml")
        if all_companies:
            with factory() as freshness_session:
                split = split_companies_by_freshness(
                    freshness_session,
                    tenant_id=tenant_id,
                    companies=all_companies,
                )
            companies_override = split.needs_fetch
            fresh_reuse_keys = set(split.fresh_reuse.keys())
            companies_override, deferred_keys = apply_supply_refresh_rotation(
                companies_override,
                supply_rotation,
            )
            # Off-group approved boards remain in the candidate pool through
            # their latest immutable Job Index snapshots. A never-fetched
            # endpoint contributes nothing until its group rotates in.
            fresh_reuse_keys.update(deferred_keys)

    request = build_acquisition_request(
        resolved_targets,
        [
            QueryArmV2(
                id=str(row.id),
                target_id=row.target_id,
                adapter=row.adapter,
                query=row.query,
                geography=row.geography,
                version=row.version,
                run_count=row.run_count,
                useful_yield_positive=row.useful_yield_positive,
                useful_yield_total=row.useful_yield_total,
            )
            for row in selected_arm_rows
        ],
        force_refresh=force_refresh,
        companies=companies_override,
    )
    request["endpoint_metrics"] = endpoint_metrics
    try:
        search_result = await search_fn(**request)
    except Exception as exc:
        with factory() as session, session.begin():
            row = session.get(DiscoveryRun, run_id)
            row.status = "failed"
            row.error = f"{type(exc).__name__}: {exc}"
            row.finished_at = _now()
        return {
            "ok": False,
            "status": "failed",
            "run_id": str(run_id),
            "pipeline_version": pipeline_version,
            "error": str(exc),
        }

    jobs = [raw_job_from_payload(payload) for payload in search_result.get("jobs", [])]

    # Phase S6 refresh cadence, part two: boards skipped above because their
    # snapshot was <=6h old get their most recent known postings pulled back
    # out of the Job Index and merged in here, so skipping the live fetch
    # never silently shrinks this run's candidate pool. Reused postings are
    # tagged raw_data["reused_from_job_index"]=True (auditable, not
    # indistinguishable from a live fetch) and deduped against anything
    # already present from the live fetch by (source, source_id).
    reused_job_count = 0
    if fresh_reuse_keys:
        with factory() as freshness_session:
            reused_jobs = reconstruct_endpoint_jobs(
                freshness_session,
                tenant_id=tenant_id,
                endpoint_keys=fresh_reuse_keys,
            )
        seen = {(job.source, job.source_id) for job in jobs}
        for reused in reused_jobs:
            key = (reused.source, reused.source_id)
            if key in seen:
                continue
            seen.add(key)
            jobs.append(reused)
            reused_job_count += 1

    selected_jobs: list[tuple[PortfolioCandidateV2, JobPosting, JobSnapshot]] = []
    selection = None
    portfolio_run_id = uuid.uuid4()
    review_rows: list[tuple[ReviewQueueEntry, str]] = []
    with _fail_discovery_on_exception(factory, run_id), factory() as session, session.begin():
        with advisory_lock(session, f"portfolio:{tenant_id}:default") as acquired:
            if not acquired:
                discovery = session.get(DiscoveryRun, run_id)
                discovery.status = "lock_busy"
                discovery.finished_at = _now()
                return {
                    "ok": False,
                    "status": "lock_busy",
                    "run_id": str(run_id),
                    "pipeline_version": pipeline_version,
                }

            store = JobIndexStore(session, tenant_id=tenant_id)

            # SUP-01B: real per-endpoint fetch telemetry, written once per
            # attempted board (success/empty/error/cache-hit) regardless of
            # whether anything from it survives into an evaluation below.
            endpoint_run_ids = record_endpoint_runs(
                session,
                tenant_id=tenant_id,
                discovery_run_id=run_id,
                endpoint_metrics=endpoint_metrics,
            )

            # Arm counts/SourceQueryRun rows are computed before enrichment
            # (not backfilled after) so real ids exist in time to be linked
            # onto the JobSnapshots created in the loop below.
            arm_counts: Counter[tuple[str, str]] = Counter()
            for job in jobs:
                query_key = (
                    str((job.raw_data or {}).get("source_query_term") or ""),
                    str((job.raw_data or {}).get("source_query_location") or ""),
                )
                if any(query_key):
                    arm_counts[query_key] += 1

            query_run_by_arm: dict[tuple[str, str], SourceQueryRun] = {}
            for row in selected_arm_rows:
                row.run_count += 1
                count = arm_counts[(row.query, row.geography)]
                query_run = SourceQueryRun(
                    tenant_id=tenant_id,
                    query_arm_id=row.id,
                    discovery_run_id=run_id,
                    status="nonempty" if count else "empty",
                    provider_records=count,
                    unique_postings=count,
                    routed_pairs=0,
                    viable_evaluations=0,
                    review_positives=0,
                    applications=0,
                    metrics={"attribution": "primary_query_term"},
                    started_at=_now(),
                    finished_at=_now(),
                )
                session.add(query_run)
                session.flush()
                query_run_by_arm[(row.query, row.geography)] = query_run

            posting_by_occurrence: dict[str, tuple[JobPosting, JobSnapshot, RawJob]] = {}
            for job in jobs:
                enriched = enrich_posting(
                    store=store,
                    source=job.source,
                    source_id=job.source_id,
                    company=job.company,
                    content=_content(job),
                )
                posting = session.get(JobPosting, enriched.posting_id)
                snapshot = session.get(JobSnapshot, enriched.snapshot_id)
                if job.provenance is not None:
                    snapshot.provenance = job.provenance.model_dump(mode="json")
                if enriched.content_changed:
                    # Snapshots are immutable once created -- only a
                    # brand-new snapshot gets today's fetch attribution; a
                    # reused snapshot (identical content_hash) keeps
                    # whichever fetch first produced that exact content.
                    raw = job.raw_data or {}
                    endpoint_ref = (
                        raw.get("source_endpoint_adapter"),
                        raw.get("source_endpoint_key"),
                    )
                    query_key = (
                        str(raw.get("source_query_term") or ""),
                        str(raw.get("source_query_location") or ""),
                    )
                    if endpoint_ref[0] and endpoint_ref[1] and endpoint_ref in endpoint_run_ids:
                        snapshot.source_endpoint_run_id = endpoint_run_ids[endpoint_ref]
                    elif any(query_key) and query_key in query_run_by_arm:
                        snapshot.source_query_run_id = query_run_by_arm[query_key].id
                    # Else: no reliable fetch attribution for this posting
                    # (SUP-01B req #5) -- both FK columns stay null rather
                    # than guessed; source_funnel_report buckets it as
                    # "attribution_unknown" instead of inferring a source.
                posting_by_occurrence[f"{job.source}::{job.source_id}"] = (
                    posting,
                    snapshot,
                    job,
                )

            v2_candidates: list[PortfolioCandidateV2] = []
            for occurrence_key, (posting, snapshot, job) in posting_by_occurrence.items():
                history_status = session.scalar(
                    select(ReviewQueueEntry.status)
                    .where(
                        ReviewQueueEntry.tenant_id == tenant_id,
                        ReviewQueueEntry.job_snapshot_id == snapshot.id,
                        ReviewQueueEntry.status.in_(("pending", "approved", "submitted")),
                    )
                    .limit(1)
                )
                prior_portfolio_selection = session.scalar(
                    select(PortfolioDecision.id)
                    .join(PortfolioRun, PortfolioRun.id == PortfolioDecision.portfolio_run_id)
                    .where(
                        PortfolioDecision.tenant_id == tenant_id,
                        PortfolioDecision.canonical_group == posting.canonical_fingerprint,
                        PortfolioDecision.selected.is_(True),
                        or_(
                            PortfolioDecision.review_id.is_not(None),
                            PortfolioRun.mode == "v2",
                        ),
                    )
                    .limit(1)
                )
                history_excluded = history_status is not None or prior_portfolio_selection is not None
                history_reason = (
                    f"review_{history_status}"
                    if history_status
                    else "previously_surfaced_portfolio"
                    if prior_portfolio_selection is not None
                    else None
                )
                # Facts, employer identity, and posting integrity are properties
                # of the occurrence, not the target. The old loop recomputed
                # all three five times per job (roughly 9k parses/classifiers
                # for a 1.8k-job run). Eligibility remains target-specific.
                facts = extract_job_facts(job)
                employer_assessment = assess_employer(job)
                posting_assessment = assess_posting(
                    job, employer_confidence=employer_assessment.confidence
                )
                for resolved in resolved_targets:
                    result = evaluate_job_target(
                        job,
                        resolved,
                        facts=facts,
                        employer=employer_assessment,
                        posting=posting_assessment,
                        pipeline_version=pipeline_version,
                    )
                    written = persist_evaluation(
                        session,
                        snapshot_id=snapshot.id,
                        facts=facts,
                        result=result,
                        discovery_run_id=run_id,
                        tenant_id=tenant_id,
                    )
                    v2_candidates.append(
                        PortfolioCandidateV2(
                            evaluation_id=str(written.evaluation.id),
                            snapshot_id=str(snapshot.id),
                            occurrence_key=occurrence_key,
                            source=job.source,
                            source_id=job.source_id,
                            company=job.company,
                            title=job.title,
                            location=job.location,
                            application_url=job.application_url,
                            canonical_group=posting.canonical_fingerprint,
                            target_priority=resolved.target.priority,
                            evaluation=result,
                            history_excluded=history_excluded,
                            history_reason=history_reason,
                        )
                    )

            # SUP-01B req #4: persist real routed_pairs / viable_evaluations
            # onto each arm's SourceQueryRun now that evaluation has run.
            # "Viable" follows the architecture's literal Tier B label
            # (JOB_POOL_V2_ARCHITECTURE.md Section 5.4: "B -- viable");
            # combined Tier A/B ("A/B evaluations") is a separate, broader
            # stage reported by source_funnel_report, not this column.
            for candidate in v2_candidates:
                job = posting_by_occurrence[candidate.occurrence_key][2]
                raw = job.raw_data or {}
                query_key = (
                    str(raw.get("source_query_term") or ""),
                    str(raw.get("source_query_location") or ""),
                )
                query_run = query_run_by_arm.get(query_key) if any(query_key) else None
                if query_run is None:
                    continue
                route_pass = (
                    candidate.evaluation.route_tier != "unmatched"
                    and candidate.evaluation.component_scores["role"] >= 35
                )
                if route_pass:
                    query_run.routed_pairs += 1
                if candidate.evaluation.tier == "B":
                    query_run.viable_evaluations += 1

            policy = load_portfolio_policy()
            if canary_capacity is not None:
                policy = policy.model_copy(
                    update={
                        "core_capacity": min(policy.core_capacity, max(0, canary_capacity)),
                        "startup_bonus": policy.startup_bonus.model_copy(update={"capacity": 0}),
                    }
                )
            selection = select_portfolio(v2_candidates, policy=policy, seed=seed)
            portfolio_run = PortfolioRun(
                id=portfolio_run_id,
                tenant_id=tenant_id,
                discovery_run_id=run_id,
                portfolio_id=policy.id,
                portfolio_version=selection.policy_version,
                config_hash=canonical_hash(policy),
                mode="dry_run" if dry_run else pipeline_version,
                seed=seed,
                status="selected",
                counts=selection.counts,
                started_at=_now(),
            )
            session.add(portfolio_run)
            session.flush()
            candidates_by_eval = {item.evaluation_id: item for item in v2_candidates}
            decision_rows: dict[str, PortfolioDecision] = {}
            for decision in selection.decisions:
                row = PortfolioDecision(
                    tenant_id=tenant_id,
                    portfolio_run_id=portfolio_run_id,
                    evaluation_id=uuid.UUID(decision.evaluation_id),
                    canonical_group=decision.canonical_group,
                    owned_target_id=decision.owned_target_id,
                    secondary_target_ids=list(decision.secondary_target_ids),
                    company_key=decision.company_key,
                    lane=decision.lane,
                    utility=decision.utility,
                    rank=decision.rank,
                    selected=decision.selected,
                    reason_codes=list(decision.reason_codes),
                )
                session.add(row)
                session.flush()
                decision_rows[decision.evaluation_id] = row

            # SUP-01B req #4 (continued): surfaced-card attribution back to
            # the arm that supplied the winning posting.
            for decision in selection.decisions:
                if not decision.selected:
                    continue
                candidate = candidates_by_eval.get(decision.evaluation_id)
                if candidate is None:
                    continue
                job = posting_by_occurrence[candidate.occurrence_key][2]
                raw = job.raw_data or {}
                query_key = (
                    str(raw.get("source_query_term") or ""),
                    str(raw.get("source_query_location") or ""),
                )
                query_run = query_run_by_arm.get(query_key) if any(query_key) else None
                if query_run is not None:
                    query_run.review_positives += 1

            if writes_review_queue(pipeline_version) and not dry_run:
                for selected in (*selection.selected_core, *selection.selected_startup_bonus):
                    posting, snapshot, job = posting_by_occurrence[selected.occurrence_key]
                    decision_row = decision_rows[selected.evaluation_id]
                    entry = create_entry(
                        session,
                        CreateEntryArgs(
                            tenant_id=tenant_id,
                            job_id=posting.id,
                            job_snapshot_id=snapshot.id,
                            materials_path=None,
                            score_breakdown=selected.evaluation.to_persisted_dict(),
                            company=job.company,
                            title=job.title,
                            run_id=str(run_id),
                            evaluation_id=selected.evaluation_id,
                            portfolio_decision_id=decision_row.id,
                        ),
                    )
                    decision_row.review_id = entry.id
                    entry.evaluation_id = uuid.UUID(selected.evaluation_id)
                    entry.portfolio_decision_id = decision_row.id
                    review_rows.append((entry, selected.evaluation.target_id))
                    selected_jobs.append((selected, posting, snapshot))

            portfolio_run.status = "complete"
            portfolio_run.finished_at = _now()
            discovery = session.get(DiscoveryRun, run_id)
            discovery.status = "complete"
            discovery.counts = {
                **(search_result.get("counts") or {}),
                **selection.counts,
                "evaluations": len(v2_candidates),
                "ats_live_endpoints": len(endpoint_metrics),
                "ats_reused_endpoints": len(fresh_reuse_keys),
                "ats_reused_jobs": reused_job_count,
            }
            discovery.finished_at = _now()

    task_ids: list[str] = []
    if review_rows:
        enqueue = enqueue_fn or _default_enqueue
        for entry, target_id in review_rows:
            task_ids.append(
                enqueue(
                    "materials.generate",
                    {
                        "job_id": str(entry.job_id),
                        "profile_id": target_id,
                        "document_types": ["resume", "cover_letter"],
                    },
                )
            )

    assert selection is not None
    return {
        "ok": True,
        "status": "complete",
        "run_id": str(run_id),
        "portfolio_run_id": str(portfolio_run_id),
        "pipeline_version": pipeline_version,
        "dry_run": dry_run,
        "search_counts": search_result.get("counts") or {},
        "supply_counts": {
            "approved_endpoints": (
                supply_rotation.approved_endpoint_count
            ),
            "live_refresh_group": supply_rotation.group_id if not force_refresh else "all",
            "live_endpoints": len(endpoint_metrics),
            "reused_endpoints": len(fresh_reuse_keys),
            "reused_jobs": reused_job_count,
        },
        "portfolio_counts": selection.counts,
        "review_entry_ids": [str(row.id) for row, _ in review_rows],
        "materials_task_ids": task_ids,
        "selected": [
            {
                "evaluation_id": item.evaluation_id,
                "target_id": item.evaluation.target_id,
                "tier": item.evaluation.tier,
                "review_index": item.evaluation.adjusted_review_index,
                "company": item.company,
                "title": item.title,
                "startup": item.startup,
                "lane": "core" if item in selection.selected_core else "startup_bonus",
            }
            for item in (*selection.selected_core, *selection.selected_startup_bonus)
        ],
        "errors": search_result.get("errors") or [],
    }


__all__ = ["build_acquisition_request", "raw_job_from_payload", "run_portfolio_v2"]
