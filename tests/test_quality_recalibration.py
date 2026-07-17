"""Regression tests for the July candidacy-quality recalibration."""

from unittest.mock import patch

import pytest

from src.application.jobs import _classify_experience_level
from src.generation.cover_letter import (
    _assert_cover_letter_grounded,
    _generate_high_quality_cover_letter_text,
    _unsupported_applicant_claims,
)
from src.intake.schema import RawJob
from src.matching.rules import ApplicantContext, check_rules
from src.utils.llm import LLMError


def _job(**overrides) -> RawJob:
    values = {
        "source": "greenhouse",
        "source_id": "quality-1",
        "company": "Acme",
        "title": "Solutions Engineer",
        "description": "Work with customers to deploy production software.",
    }
    values.update(overrides)
    return RawJob(**values)


def test_plain_sr_prefix_is_senior() -> None:
    assert _classify_experience_level("Sr Systems Analyst - GenAI") == "senior"


def test_active_clearance_requirement_is_disqualifying() -> None:
    job = _job(description="Candidates must possess an active Secret clearance.")
    verdict = check_rules(job, ApplicantContext(years_of_experience=2))
    assert not verdict.passed
    assert any(result.rule_id == "security_clearance" for result in verdict.fail_results)


def test_known_staffing_intermediary_is_disqualifying() -> None:
    job = _job(company="Motion Recruitment Partners, LLC")
    verdict = check_rules(job, ApplicantContext(years_of_experience=2))
    assert not verdict.passed
    assert any(result.rule_id == "staffing_employer" for result in verdict.fail_results)


def test_active_cover_letter_path_rejects_invented_number() -> None:
    job = _job()
    with pytest.raises(LLMError, match="fact drift"):
        _assert_cover_letter_grounded(
            "I improved Acme's deployment conversion by 73%.",
            job=job,
            profile_data={"identity": {"full_name": "Arya"}},
            evidence_bullets=["Built a customer deployment workflow."],
        )


def test_cover_letter_repairs_rejected_short_first_attempt() -> None:
    job = _job()
    good = (
        "I am interested in Acme because the role combines customer discovery and deployment.\n\n"
        "I built a customer deployment workflow and documented each handoff for its users.\n\n"
        "That work required translating technical constraints into practical operating steps.\n\n"
        "I would bring the same grounded, customer-facing approach to this role at Acme."
    )
    with patch(
        "src.generation.cover_letter._generate_with_llm",
        side_effect=[LLMError("too short"), good],
    ) as generate:
        text, origin, _issues = _generate_high_quality_cover_letter_text(
            job,
            {"identity": {"full_name": "Arya"}},
            ["Built a customer deployment workflow."],
            target_pages=1,
            strategy={},
        )
    assert text == good
    assert origin == "llm"
    assert generate.call_count == 2
    assert "too short" in generate.call_args_list[1].kwargs["length_feedback"]


def test_cover_letter_returns_best_available_after_two_drafts() -> None:
    job = _job()
    first = "One short paragraph with an unsupported 73% result."
    second = (
        "I am interested in Acme's customer deployment work.\n\n"
        "I built a customer deployment workflow and documented each handoff.\n\n"
        "That required translating technical constraints into operating steps.\n\n"
        "I would bring that same practical approach to this role."
    )
    with patch(
        "src.generation.cover_letter._generate_with_llm",
        side_effect=[first, second],
    ) as generate:
        text, origin, _issues = _generate_high_quality_cover_letter_text(
            job,
            {"identity": {"full_name": "Arya"}},
            ["Built a customer deployment workflow."],
            target_pages=1,
            strategy={},
        )

    assert text == second
    assert origin == "llm"
    assert generate.call_count == 2


def test_cover_letter_flags_invented_causal_result() -> None:
    claims = _unsupported_applicant_claims(
        (
            "At SDS, I wrote implementation guides for client workflows. "
            "This eliminated confusion and became the company standard."
        ),
        ["Built implementation guides tailored to client workflows."],
    )

    assert any("eliminated confusion" in claim for claim in claims)
