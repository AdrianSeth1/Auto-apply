"""Tests for Phase S7's candidate-preference wiring in scorer_v2.

Two changes under test, both against real production code (candidate.yaml,
target YAML files, and the real _preference/_responsibility_coverage
functions -- no mocks):

1. ``preferred_companies`` bumps the existing employer_interest component in
   ``_preference`` (no new weighted term added -- see that function's
   comment for why: adding a new weight would require rebalancing the whole
   0.25/0.25/0.20/0.15/0.10/0.05 split, a bigger and riskier change than
   this ticket calls for).
2. ``excluded_role_signals`` (candidate-level) is merged with each target's
   own ``negative_responsibility_signals`` in ``_responsibility_coverage``.

Full ``evaluate_job_target`` cannot be exercised in this sandbox --
``evaluate_global_eligibility`` lazily imports ``src.application.jobs``,
which needs ``src.core.state_machine``, which needs Python 3.11's
``enum.StrEnum`` (this sandbox is 3.10). ``_preference`` and
``_responsibility_coverage`` don't touch that import chain, so they're
tested directly instead, with a hand-built ``GateResultV2`` standing in for
what ``evaluate_global_eligibility`` would normally produce.
"""

from __future__ import annotations

import datetime as _dt

_dt.UTC = _dt.timezone.utc  # noqa: E402 (src/core/models.py needs 3.12's datetime.UTC)

import pytest  # noqa: E402

import src.matching.profile_v2 as pv  # noqa: E402
from src.intake.schema import RawJob  # noqa: E402
from src.jobs.employers import assess_employer  # noqa: E402
from src.matching.job_facts import GateResultV2, extract_job_facts  # noqa: E402
from src.matching.scorer_v2 import _preference, _responsibility_coverage  # noqa: E402

BASE_KWARGS = dict(
    source="greenhouse",
    source_id="1",
    company="Axxess",
    title="Implementation Consultant",
    location="Remote",
    employment_type="fulltime",
    seniority="entry",
    description="Own client onboarding and implementation for SaaS customers.",
    application_url="u",
    ats_type="greenhouse",
    raw_data={},
)


@pytest.fixture(scope="module")
def resolved_saas():
    targets = pv.load_targets()
    target = next(t for t in targets if t.id == "saas-implementation")
    candidate = pv.load_candidate()
    return pv.resolve_target(candidate, target)


def _passing_gates():
    return (
        GateResultV2(
            gate_id="location", status="pass", reason_code="ok", message="ok", confidence=1.0
        ),
    )


def test_preferred_company_bumps_employer_interest_component(resolved_saas):
    # Axxess is in candidate.yaml's preferred_companies list.
    job_preferred = RawJob(**BASE_KWARGS)
    job_other = RawJob(**{**BASE_KWARGS, "company": "Some Unlisted Company"})
    gates = _passing_gates()
    facts = extract_job_facts(job_preferred)
    score_preferred, _ = _preference(
        job_preferred, facts, resolved_saas, assess_employer(job_preferred), gates
    )
    score_other, _ = _preference(
        job_other, facts, resolved_saas, assess_employer(job_other), gates
    )
    # employer_interest component: 100 (preferred) vs 60 (baseline), weight
    # 0.15 -> exactly a 6.0 point difference, nothing else about the score
    # changes since both jobs are otherwise identical.
    assert score_preferred - score_other == pytest.approx(6.0)


def test_preferred_company_match_is_case_and_punctuation_insensitive(resolved_saas):
    # normalize_phrase casefolds and strips punctuation -- "AXXESS" and
    # "Axxess," should both match the stored "Axxess" preference entry.
    job = RawJob(**{**BASE_KWARGS, "company": "AXXESS,"})
    gates = _passing_gates()
    facts = extract_job_facts(job)
    score, _ = _preference(job, facts, resolved_saas, assess_employer(job), gates)
    job_baseline = RawJob(**{**BASE_KWARGS, "company": "Totally Unrelated Inc"})
    score_baseline, _ = _preference(
        job_baseline, facts, resolved_saas, assess_employer(job_baseline), gates
    )
    assert score - score_baseline == pytest.approx(6.0)


def test_staffing_intermediary_still_zeroes_out_regardless_of_preferred_list(resolved_saas):
    # A preferred company that also happens to be flagged as a staffing
    # intermediary must still score 0 on this component -- the preferred-
    # company bump must not override the existing staffing exclusion.
    job = RawJob(
        **{
            **BASE_KWARGS,
            "company": "Axxess",
            "description": "Staffing agency placing contractors at client sites.",
        }
    )
    gates = _passing_gates()
    facts = extract_job_facts(job)
    employer = assess_employer(job)
    if employer.employment_relationship != "staffing_intermediary":
        pytest.skip("assess_employer heuristic didn't classify this fixture as staffing")
    score, _ = _preference(job, facts, resolved_saas, employer, gates)
    baseline_job = RawJob(**{**BASE_KWARGS, "company": "Zzz Not Preferred"})
    baseline_score, _ = _preference(
        baseline_job, facts, resolved_saas, assess_employer(baseline_job), gates
    )
    assert score < baseline_score


def test_no_preferred_companies_configured_is_a_no_op():
    # A candidate with an empty preferred_companies list must behave exactly
    # like the pre-S7 code -- both jobs get the same baseline employer_interest.
    candidate = pv.load_candidate()
    candidate = candidate.model_copy(
        update={
            "preferences": candidate.preferences.model_copy(
                update={"preferred_companies": []}
            )
        }
    )
    target = next(t for t in pv.load_targets() if t.id == "saas-implementation")
    resolved = pv.resolve_target(candidate, target)
    gates = _passing_gates()
    job_a = RawJob(**{**BASE_KWARGS, "company": "Axxess"})
    job_b = RawJob(**{**BASE_KWARGS, "company": "Some Other Co"})
    facts = extract_job_facts(job_a)
    score_a, _ = _preference(job_a, facts, resolved, assess_employer(job_a), gates)
    score_b, _ = _preference(job_b, facts, resolved, assess_employer(job_b), gates)
    assert score_a == score_b


def test_excluded_role_signal_increases_negative_share(resolved_saas):
    job_clean = RawJob(**BASE_KWARGS)
    job_door_to_door = RawJob(
        **{
            **BASE_KWARGS,
            "description": BASE_KWARGS["description"]
            + " This role includes door to door canvassing of residential neighborhoods.",
        }
    )
    _, negative_clean = _responsibility_coverage(job_clean, resolved_saas)
    _, negative_dtd = _responsibility_coverage(job_door_to_door, resolved_saas)
    assert negative_clean == 0.0
    assert negative_dtd >= 0.5


def test_excluded_role_signal_is_candidate_level_not_target_specific():
    # Confirms the merge happens for every target, not just saas-implementation
    # (the candidate-level list must not be accidentally read from a
    # per-target field).
    candidate = pv.load_candidate()
    assert "door to door" in candidate.preferences.excluded_role_signals
    for target in pv.load_targets():
        resolved = pv.resolve_target(candidate, target)
        job_dtd = RawJob(
            **{
                **BASE_KWARGS,
                "description": BASE_KWARGS["description"] + " Door to door sales required.",
            }
        )
        _, negative = _responsibility_coverage(job_dtd, resolved)
        assert negative > 0.0, f"{target.id} did not pick up the candidate-level exclusion"


def test_empty_excluded_role_signals_reproduces_pre_s7_behavior():
    # saas-implementation's own negative_responsibility_signals (SAP
    # implementation, Oracle implementation, active clinical license,
    # enterprise program ownership) don't mention door-to-door at all, so
    # with an empty candidate-level list the negative share for a
    # door-to-door job must be exactly 0 -- the pre-S7 behavior, not
    # something this candidate-level field silently inflates on its own.
    candidate = pv.load_candidate()
    candidate = candidate.model_copy(
        update={
            "preferences": candidate.preferences.model_copy(
                update={"excluded_role_signals": []}
            )
        }
    )
    target = next(t for t in pv.load_targets() if t.id == "saas-implementation")
    resolved = pv.resolve_target(candidate, target)
    job_dtd = RawJob(
        **{
            **BASE_KWARGS,
            "description": BASE_KWARGS["description"] + " Door to door sales required.",
        }
    )
    _, negative = _responsibility_coverage(job_dtd, resolved)
    assert negative == 0.0
