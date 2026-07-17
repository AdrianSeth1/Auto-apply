"""SUP-09 (Phase S6): refresh-cadence throttling for direct-ATS boards.

Phase S6: "Refresh direct ATS boards once daily and on `Run Plans Now` only
when their cached snapshot is older than six hours."

The naive version of this -- just skip the live fetch for a board that was
successfully fetched recently -- is wrong. Postings only get linked into a
discovery run's candidate pool if they're present in *that run's* live
fetch result (`src.orchestration.portfolio_run.run_portfolio_v2` builds
`jobs` straight from `search_result["jobs"]`, and only postings in `jobs`
get an evaluation linked to this run via `DiscoveryRunEvaluation`). Simply
skipping a "fresh" board's fetch would silently drop its postings from this
run's candidate pool entirely, not just save one HTTP call -- exactly the
kind of silent-supply-loss this codebase has been careful not to introduce
anywhere else (see the yield-demotion diagnostic in
`src.application.source_funnel`, which stops at "recommend" for the same
reason).

The correct version, implemented here: split configured companies into
"needs a live fetch" (never fetched, or last successful fetch older than
the staleness threshold) and "fresh enough" (fetched successfully within
the threshold). The caller fetches only the first group live, then calls
``reconstruct_fresh_endpoint_jobs`` to pull the second group's most
recently known postings back out of the Job Index and merge them into the
same job list -- so a "fresh" board's postings stay eligible for this run's
candidate pool without a redundant live HTTP call. Reused postings carry up
to ``staleness_threshold`` of latency versus the live board (bounded and
accepted by design, not a correctness gap: the whole point of a refresh
cadence is accepting bounded staleness in exchange for fewer live fetches).

``force_refresh=True`` bypasses this entirely (matches the existing
semantics of that flag elsewhere in the codebase: "bypass the cache,
re-fetch everything") -- the split functions are simply not called in that
case; the caller passes the full, unfiltered company list through as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import TENANT_DEFAULT, JobPosting, JobSnapshot, SourceEndpoint
from src.intake.schema import JobRequirements, RawJob

DEFAULT_STALENESS_THRESHOLD = timedelta(hours=6)


def _endpoint_key_for_entry(adapter: str, entry: Any) -> str | None:
    """Mirror ``src.intake.search._endpoint_identity`` exactly -- this must
    produce the identical key that module derives, or freshness lookups
    silently miss every row. Returns ``None`` for a malformed entry (same
    "skip, don't guess" handling ``search.py`` uses for a bad Workday dict)."""
    if adapter == "workday":
        try:
            return f"{entry['tenant']}/{entry['host']}/{entry['site']}"
        except (TypeError, KeyError):
            return None
    if isinstance(entry, str):
        return entry
    return None


@dataclass
class FreshnessSplit:
    needs_fetch: dict[str, list[Any]] = field(default_factory=dict)
    # (adapter, endpoint_key) -> the raw companies.yaml entry, kept so the
    # caller can log/report which configured boards were skipped this run.
    fresh_reuse: dict[tuple[str, str], Any] = field(default_factory=dict)


def split_companies_by_freshness(
    session: Session,
    *,
    tenant_id: str = TENANT_DEFAULT,
    companies: dict[str, list[Any]],
    staleness_threshold: timedelta = DEFAULT_STALENESS_THRESHOLD,
    now: datetime | None = None,
) -> FreshnessSplit:
    """Partition a companies.yaml-shaped dict into "needs a live fetch" vs
    "fetched successfully within the staleness threshold".

    An endpoint with no ``SourceEndpoint`` row yet (never attempted) always
    needs a fetch -- there's nothing to reuse. A malformed entry (can't
    derive a key) also always needs a fetch, the same fail-open-to-fetching
    behavior as skipping straight to a live attempt rather than guessing
    it's fine to skip.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    split = FreshnessSplit()

    all_keys: set[tuple[str, str]] = set()
    entry_by_key: dict[tuple[str, str], tuple[str, Any]] = {}
    for adapter, entries in companies.items():
        for entry in entries or []:
            key = _endpoint_key_for_entry(adapter, entry)
            if key is None:
                split.needs_fetch.setdefault(adapter, []).append(entry)
                continue
            all_keys.add((adapter, key))
            entry_by_key[(adapter, key)] = (adapter, entry)

    if not all_keys:
        return split

    adapters = {adapter for adapter, _ in all_keys}
    endpoint_rows = list(
        session.scalars(
            select(SourceEndpoint).where(
                SourceEndpoint.tenant_id == tenant_id,
                SourceEndpoint.adapter.in_(adapters),
            )
        ).all()
    )
    last_success_by_key: dict[tuple[str, str], datetime | None] = {
        (row.adapter, row.endpoint_key): row.last_success_at for row in endpoint_rows
    }

    for key in all_keys:
        adapter, entry = entry_by_key[key]
        last_success = last_success_by_key.get(key)
        if last_success is None or (now - last_success) >= staleness_threshold:
            split.needs_fetch.setdefault(adapter, []).append(entry)
        else:
            split.fresh_reuse[key] = entry

    return split


def reconstruct_endpoint_jobs(
    session: Session,
    *,
    tenant_id: str = TENANT_DEFAULT,
    endpoint_keys: set[tuple[str, str]],
) -> list[RawJob]:
    """Pull the latest known postings for selected endpoints from the index.

    The caller may be reusing a fresh endpoint or deferring an approved
    endpoint to another refresh group. Every returned ``RawJob`` carries
    ``raw_data["reused_from_job_index"] = True`` alongside its original
    ``source_endpoint_adapter``/
    ``source_endpoint_key`` tags (already present on the stored snapshot,
    from whichever earlier run actually fetched it) so this is auditable,
    not indistinguishable from a real fresh fetch.

    Deliberately conservative: only postings whose latest snapshot's
    ``raw_data`` carries the exact ``(source_endpoint_adapter,
    source_endpoint_key)`` tag are matched -- the same exact-attribution
    requirement ``src.application.source_funnel`` already relies on. A
    posting predating that instrumentation (no tag) is never guessed into a
    bucket and is simply not reused; it stays out of this run's pool until
    an endpoint that also covers it gets a real live fetch again.
    """
    if not endpoint_keys:
        return []

    adapters = {adapter for adapter, _ in endpoint_keys}
    postings = list(
        session.scalars(
            select(JobPosting).where(
                JobPosting.tenant_id == tenant_id,
                JobPosting.source.in_(adapters),
                JobPosting.latest_snapshot_id.is_not(None),
            )
        ).all()
    )
    if not postings:
        return []

    snapshot_ids = {p.latest_snapshot_id for p in postings if p.latest_snapshot_id}
    snapshots = {
        s.id: s
        for s in session.scalars(select(JobSnapshot).where(JobSnapshot.id.in_(snapshot_ids))).all()
    }

    reused: list[RawJob] = []
    for posting in postings:
        snapshot = snapshots.get(posting.latest_snapshot_id)
        if snapshot is None:
            continue
        raw_data = snapshot.raw_data or {}
        key = (
            str(raw_data.get("source_endpoint_adapter") or ""),
            str(raw_data.get("source_endpoint_key") or ""),
        )
        if key not in endpoint_keys:
            continue
        requirements = snapshot.requirements or {}
        try:
            parsed_requirements = JobRequirements.model_validate(requirements)
        except (TypeError, ValueError):
            parsed_requirements = JobRequirements()
        try:
            reused_job = RawJob(
                source=posting.source,  # type: ignore[arg-type]
                source_id=posting.source_id,
                company=posting.company,
                title=snapshot.title,
                location=snapshot.location,
                employment_type=snapshot.employment_type or "unknown",  # type: ignore[arg-type]
                seniority=snapshot.seniority or "unknown",  # type: ignore[arg-type]
                description=snapshot.description,
                requirements=parsed_requirements,
                application_url=snapshot.application_url,
                ats_type=posting.source,  # type: ignore[arg-type]
                raw_data={**raw_data, "reused_from_job_index": True},
            )
        except (TypeError, ValueError):
            # A stored snapshot that no longer validates against the current
            # RawJob schema (e.g. an enum literal narrowed since it was
            # written) can't be safely reused. Skip it rather than crash the
            # whole discovery run over one reused posting -- it simply stays
            # out of this run's pool, same as any other unmatched posting.
            continue
        reused.append(reused_job)
    return reused


def reconstruct_fresh_endpoint_jobs(
    session: Session,
    *,
    tenant_id: str = TENANT_DEFAULT,
    fresh_endpoint_keys: set[tuple[str, str]],
) -> list[RawJob]:
    """Backward-compatible fresh-only wrapper."""

    return reconstruct_endpoint_jobs(
        session,
        tenant_id=tenant_id,
        endpoint_keys=fresh_endpoint_keys,
    )


__all__ = [
    "DEFAULT_STALENESS_THRESHOLD",
    "FreshnessSplit",
    "reconstruct_endpoint_jobs",
    "reconstruct_fresh_endpoint_jobs",
    "split_companies_by_freshness",
]
