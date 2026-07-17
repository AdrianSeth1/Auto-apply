"""Tests for the DB-independent helpers in src.application.resolve_snippets.

``resolve_pending_snippets`` itself needs a live Postgres session (it goes
through ``JobIndexStore``/``enrich_posting`` exactly like the real V2
portfolio run does) and is not exercised here -- see
the "SUP-07" notes in git history of the removed
``docs/JOB_POOL_V2_IMPLEMENTATION_STATUS.md`` (2026-07-16) for what was
and wasn't verifiable in this implementation session. These tests cover the
two pieces that don't need a database: loading/parsing
``config/source_policy.yaml`` and converting a legacy ``jobs`` row into a
``RawJob`` for ``resolve_full_jd``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.application.resolve_snippets import (
    _pending_snippets_query,
    _raw_job_from_row,
    load_source_policy,
    resolve_pending_snippets,
)
from src.intake.full_jd_resolver import ResolveOutcome


def test_load_source_policy_reads_the_real_repo_config():
    policy = load_source_policy()
    assert policy.get("adapters", {}).get("greenhouse", {}).get("enabled") is True
    assert policy.get("adapters", {}).get("smartrecruiters", {}).get("enabled") is False


def test_load_source_policy_missing_file_fails_closed(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"
    assert load_source_policy(missing) == {}


def test_load_source_policy_malformed_yaml_fails_closed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("adapters: [this is not a mapping", encoding="utf-8")
    assert load_source_policy(bad) == {}


def _fake_row(**overrides):
    fields = dict(
        tenant_id="default",
        source="adzuna",
        source_id="adz-42",
        company="Acme Corp",
        title="Solutions Engineer",
        location="Remote",
        employment_type="fulltime",
        seniority="mid",
        description="Short snippet...",
        requirements=None,
        application_url="https://example.com/redirect/42",
        ats_type="unknown",
        raw_data={"description_completeness": "snippet"},
        discovered_at=None,
        expires_at=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_raw_job_from_row_round_trips_core_fields():
    row = _fake_row()
    job = _raw_job_from_row(row)
    assert job.source == "adzuna"
    assert job.source_id == "adz-42"
    assert job.company == "Acme Corp"
    assert job.application_url == "https://example.com/redirect/42"
    assert job.raw_data["description_completeness"] == "snippet"


def test_raw_job_from_row_tolerates_malformed_requirements():
    row = _fake_row(requirements={"not_a_real_field": object()})
    job = _raw_job_from_row(row)
    # Falls back to an empty JobRequirements rather than raising.
    assert job.requirements.must_have_skills == []


def test_raw_job_from_row_defaults_missing_enums():
    row = _fake_row(employment_type=None, seniority=None, ats_type=None, raw_data=None)
    job = _raw_job_from_row(row)
    assert job.employment_type == "unknown"
    assert job.seniority == "unknown"
    assert job.ats_type == "unknown"
    assert job.raw_data == {}


def test_pending_query_requires_promising_immutable_evaluation():
    query = _pending_snippets_query(max_attempts=3, limit=50)
    sql = str(query)
    params = set(query.compile().params.values())
    assert "job_target_evaluations.review_index" in sql
    assert "job_target_evaluations.component_scores" in sql
    assert "job_evaluation_reasons.stage" in sql
    assert {"target_routing", "global_eligibility", "location"} <= params


def test_raw_job_from_row_normalizes_source_case():
    row = _fake_row(source="  AdZuNa  ")
    assert _raw_job_from_row(row).source == "adzuna"


def test_success_records_attempt_and_enriches_existing_posting_without_snapshot():
    row = _fake_row(
        source="AdZuNa",
        raw_data={
            "description_completeness": "snippet",
            "full_jd_recovery_attempts": 1,
            "full_jd_recovery_last_reason": "redirect_follow_failed",
        },
        discovered_at=datetime.now(UTC),
    )
    recovered = _raw_job_from_row(row).model_copy(
        update={
            "description": "A complete recovered description " * 20,
            "raw_data": {
                "description_completeness": "full",
                "full_jd_recovered": True,
            },
        }
    )
    posting = SimpleNamespace(latest_snapshot_id=None)
    first_result = MagicMock()
    first_result.scalars.return_value = [row]
    second_result = MagicMock()
    second_result.scalar_one_or_none.return_value = posting
    session = MagicMock()
    session.execute.side_effect = [first_result, second_result]
    store = SimpleNamespace(tenant_id="default")
    enrich_result = SimpleNamespace(content_changed=True, snapshot_id="snapshot-1")
    with (
        patch("src.application.resolve_snippets.JobIndexStore", return_value=store),
        patch(
            "src.application.resolve_snippets.resolve_full_jd",
            return_value=ResolveOutcome(resolved=True, job=recovered),
        ),
        patch(
            "src.application.resolve_snippets.enrich_posting",
            return_value=enrich_result,
        ) as enrich,
    ):
        summary = resolve_pending_snippets(session, source_policy={})

    assert summary.considered == 1
    assert summary.recovered == 1
    assert row.raw_data["full_jd_recovery_attempts"] == 2
    assert row.raw_data["full_jd_recovery_last_reason"] == "resolved"
    assert row.raw_data["full_jd_recovery_last_attempted_at"]
    enrich.assert_called_once()

    lookup_sql = str(session.execute.call_args_list[1].args[0])
    assert "lower(trim(job_postings.source))" in lookup_sql
