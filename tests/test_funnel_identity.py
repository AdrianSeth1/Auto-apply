"""Regression tests for conservative job identity and funnel contracts."""

from __future__ import annotations

from src.application.funnel import FUNNEL_STAGES
from src.core.models import FunnelEvent, Job
from src.jobs.identity import (
    canonical_fingerprint,
    canonicalize_application_url,
    normalize_identity_text,
)


def test_identity_normalization_removes_legal_suffix_only() -> None:
    assert normalize_identity_text("Acme Technologies, Inc.") == "acme technologies"
    assert normalize_identity_text("  Forward-Deployed Engineer ") == "forward deployed engineer"


def test_application_url_drops_tracking_but_preserves_destination_query() -> None:
    value = "https://Jobs.Example.com/roles/123/?utm_source=board&department=ai#apply"
    assert canonicalize_application_url(value) == (
        "https://jobs.example.com/roles/123?department=ai"
    )


def test_fingerprint_clusters_cross_source_urls_when_location_matches() -> None:
    direct = canonical_fingerprint(
        company="Acme, Inc.",
        title="AI Solutions Engineer",
        location="Dallas, TX",
        application_url="https://acme.example/jobs/123",
    )
    aggregator = canonical_fingerprint(
        company="ACME",
        title="AI Solutions Engineer",
        location="Dallas TX",
        application_url="https://aggregator.example/ad/999",
    )
    assert direct == aggregator


def test_fingerprint_refuses_thin_identity() -> None:
    assert (
        canonical_fingerprint(
            company="Acme", title="Engineer", location=None, application_url=None
        )
        is None
    )


def test_funnel_stage_order_is_business_facing() -> None:
    assert FUNNEL_STAGES == (
        "discovered",
        "qualified",
        "reviewed",
        "applied",
        "screen",
        "interview",
        "offer",
    )


def test_model_contracts_include_identity_and_idempotent_funnel_constraint() -> None:
    assert "canonical_fingerprint" in Job.__table__.columns
    constraints = {constraint.name for constraint in FunnelEvent.__table__.constraints}
    assert "uq_funnel_event_stage" in constraints

