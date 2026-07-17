"""Tests for the Phase S6 refresh-cadence split/reconstruct logic in
``src.jobs.source_freshness``.

Both functions under test issue exactly ``session.scalars(select(...)).all()``
calls -- no writes, no transactions -- so they're tested here against a
minimal duck-typed fake session that routes by mapped class (inspecting
``stmt.column_descriptions[0]["type"]``, pure SQLAlchemy Core/ORM
introspection that needs no DB connection) and returns a canned in-memory
row set, the same pattern already established in
``tests/test_source_endpoints_health.py`` for ``_update_endpoint_health``.

This validates the actual designed logic -- endpoint-key derivation
(must exactly mirror ``src.intake.search._endpoint_identity``), the
staleness-threshold branch, and the raw_data-tag-based posting-to-endpoint
matching used to rebuild ``RawJob``s from the Job Index. It does NOT
validate that the real ``select(...).where(...)`` filters behave correctly
against live Postgres (standard, low-risk ORM usage already used elsewhere
in this codebase, e.g. ``src/application/resolve_snippets.py``) -- that
part is unverified in this sandbox (no live Postgres), consistent with
every other DB-touching change this session.
"""

from __future__ import annotations

import datetime as _dt

_dt.UTC = _dt.timezone.utc  # noqa: E402  (src/core/models.py needs 3.12's datetime.UTC)

from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402

from src.core.models import JobPosting, JobSnapshot, SourceEndpoint  # noqa: E402
from src.jobs.source_freshness import (  # noqa: E402
    _endpoint_key_for_entry,
    reconstruct_fresh_endpoint_jobs,
    split_companies_by_freshness,
)

UTC = _dt.UTC
NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=UTC)


# ---- _endpoint_key_for_entry -------------------------------------------------


def test_endpoint_key_plain_slug():
    assert _endpoint_key_for_entry("greenhouse", "acme") == "acme"


def test_endpoint_key_workday_matches_search_py_identity():
    entry = {"tenant": "salesforce", "host": "wd12", "site": "External_Career_Site"}
    assert _endpoint_key_for_entry("workday", entry) == "salesforce/wd12/External_Career_Site"


def test_endpoint_key_malformed_workday_entry_returns_none():
    assert _endpoint_key_for_entry("workday", {"tenant": "acme"}) is None
    assert _endpoint_key_for_entry("workday", "not-a-dict") is None


# ---- split_companies_by_freshness --------------------------------------------


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Routes .scalars(stmt) by the mapped class the statement selects from,
    ignoring WHERE-clause filters (the fake stands in for "the DB already
    scoped to the right tenant/adapters"; see module docstring)."""

    def __init__(self, *, endpoints=None, postings=None, snapshots=None):
        self.endpoints = endpoints or []
        self.postings = postings or []
        self.snapshots = snapshots or []

    def scalars(self, stmt):
        model = stmt.column_descriptions[0]["type"]
        if model is SourceEndpoint:
            return _FakeScalars(self.endpoints)
        if model is JobPosting:
            return _FakeScalars(self.postings)
        if model is JobSnapshot:
            return _FakeScalars(self.snapshots)
        raise AssertionError(f"unexpected model queried: {model}")


def _endpoint(adapter, endpoint_key, last_success_at):
    row = SourceEndpoint(adapter=adapter, endpoint_key=endpoint_key)
    row.last_success_at = last_success_at
    return row


def test_never_fetched_endpoint_needs_fetch():
    companies = {"greenhouse": ["acme"]}
    session = _FakeSession(endpoints=[])
    split = split_companies_by_freshness(session, companies=companies, now=NOW)
    assert split.needs_fetch == {"greenhouse": ["acme"]}
    assert split.fresh_reuse == {}


def test_stale_endpoint_needs_fetch():
    companies = {"greenhouse": ["acme"]}
    session = _FakeSession(
        endpoints=[_endpoint("greenhouse", "acme", NOW - timedelta(hours=7))]
    )
    split = split_companies_by_freshness(session, companies=companies, now=NOW)
    assert split.needs_fetch == {"greenhouse": ["acme"]}
    assert split.fresh_reuse == {}


def test_fresh_endpoint_reused_not_fetched():
    companies = {"greenhouse": ["acme"]}
    session = _FakeSession(
        endpoints=[_endpoint("greenhouse", "acme", NOW - timedelta(hours=1))]
    )
    split = split_companies_by_freshness(session, companies=companies, now=NOW)
    assert split.needs_fetch == {}
    assert split.fresh_reuse == {("greenhouse", "acme"): "acme"}


def test_exactly_at_threshold_needs_fetch():
    # >= threshold means "needs fetch" (boundary belongs to the safer side:
    # a board exactly 6h stale gets refreshed, not skipped).
    companies = {"greenhouse": ["acme"]}
    session = _FakeSession(
        endpoints=[_endpoint("greenhouse", "acme", NOW - timedelta(hours=6))]
    )
    split = split_companies_by_freshness(session, companies=companies, now=NOW)
    assert split.needs_fetch == {"greenhouse": ["acme"]}


def test_malformed_workday_entry_falls_open_to_needs_fetch():
    companies = {"workday": [{"tenant": "acme"}]}  # missing host/site
    session = _FakeSession(endpoints=[])
    split = split_companies_by_freshness(session, companies=companies, now=NOW)
    assert split.needs_fetch == {"workday": [{"tenant": "acme"}]}
    assert split.fresh_reuse == {}


def test_mixed_fresh_and_stale_across_adapters():
    companies = {
        "greenhouse": ["fresh-co", "stale-co"],
        "lever": ["never-fetched-co"],
    }
    session = _FakeSession(
        endpoints=[
            _endpoint("greenhouse", "fresh-co", NOW - timedelta(minutes=30)),
            _endpoint("greenhouse", "stale-co", NOW - timedelta(hours=10)),
        ]
    )
    split = split_companies_by_freshness(session, companies=companies, now=NOW)
    assert split.needs_fetch == {
        "greenhouse": ["stale-co"],
        "lever": ["never-fetched-co"],
    }
    assert split.fresh_reuse == {("greenhouse", "fresh-co"): "fresh-co"}


def test_empty_companies_returns_empty_split():
    session = _FakeSession(endpoints=[])
    split = split_companies_by_freshness(session, companies={}, now=NOW)
    assert split.needs_fetch == {}
    assert split.fresh_reuse == {}


# ---- reconstruct_fresh_endpoint_jobs -----------------------------------------


def _posting(**overrides):
    posting = JobPosting(
        tenant_id="default",
        source="greenhouse",
        source_id="123",
        company="Acme",
    )
    posting.latest_snapshot_id = overrides.pop("latest_snapshot_id")
    for key, value in overrides.items():
        setattr(posting, key, value)
    return posting


def _snapshot(id_, *, source_endpoint_adapter, source_endpoint_key, **overrides):
    snap = JobSnapshot(
        posting_id=id_,
        content_hash="deadbeef",
        title=overrides.pop("title", "Software Engineer"),
    )
    snap.id = id_
    snap.location = overrides.pop("location", "Remote")
    snap.employment_type = overrides.pop("employment_type", "fulltime")
    snap.seniority = overrides.pop("seniority", "mid")
    snap.description = overrides.pop("description", "Do engineering things.")
    snap.requirements = overrides.pop("requirements", {})
    snap.application_url = overrides.pop("application_url", "https://boards.greenhouse.io/acme/123")
    snap.raw_data = {
        "source_endpoint_adapter": source_endpoint_adapter,
        "source_endpoint_key": source_endpoint_key,
    }
    return snap


def test_reconstruct_matches_tagged_snapshot():
    import uuid

    snap_id = uuid.uuid4()
    posting = _posting(latest_snapshot_id=snap_id)
    snapshot = _snapshot(snap_id, source_endpoint_adapter="greenhouse", source_endpoint_key="acme")
    session = _FakeSession(postings=[posting], snapshots=[snapshot])

    jobs = reconstruct_fresh_endpoint_jobs(
        session, fresh_endpoint_keys={("greenhouse", "acme")}
    )
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "greenhouse"
    assert job.source_id == "123"
    assert job.company == "Acme"
    assert job.title == "Software Engineer"
    assert job.raw_data["reused_from_job_index"] is True
    assert job.raw_data["source_endpoint_key"] == "acme"


def test_reconstruct_skips_untagged_or_mismatched_snapshot():
    import uuid

    snap_id = uuid.uuid4()
    posting = _posting(latest_snapshot_id=snap_id)
    # Tagged for a DIFFERENT endpoint than what's being reconstructed.
    snapshot = _snapshot(snap_id, source_endpoint_adapter="greenhouse", source_endpoint_key="other-co")
    session = _FakeSession(postings=[posting], snapshots=[snapshot])

    jobs = reconstruct_fresh_endpoint_jobs(
        session, fresh_endpoint_keys={("greenhouse", "acme")}
    )
    assert jobs == []


def test_reconstruct_empty_keys_short_circuits_without_querying():
    session = _FakeSession()  # would raise on any .scalars() call it didn't expect
    jobs = reconstruct_fresh_endpoint_jobs(session, fresh_endpoint_keys=set())
    assert jobs == []


def test_reconstruct_posting_with_no_matching_snapshot_row_skipped():
    import uuid

    snap_id = uuid.uuid4()
    posting = _posting(latest_snapshot_id=snap_id)
    session = _FakeSession(postings=[posting], snapshots=[])  # snapshot row missing
    jobs = reconstruct_fresh_endpoint_jobs(
        session, fresh_endpoint_keys={("greenhouse", "acme")}
    )
    assert jobs == []


def test_reconstruct_skips_snapshot_that_no_longer_validates_instead_of_raising():
    # A stored employment_type value that no longer matches the current
    # RawJob literal (schema narrowed since the snapshot was written) must
    # not crash the whole discovery run -- the posting is simply left out
    # of the reused set.
    import uuid

    snap_id = uuid.uuid4()
    posting = _posting(latest_snapshot_id=snap_id)
    snapshot = _snapshot(
        snap_id,
        source_endpoint_adapter="greenhouse",
        source_endpoint_key="acme",
        employment_type="full_time",  # not a valid EmploymentType literal
    )
    session = _FakeSession(postings=[posting], snapshots=[snapshot])
    jobs = reconstruct_fresh_endpoint_jobs(
        session, fresh_endpoint_keys={("greenhouse", "acme")}
    )
    assert jobs == []
