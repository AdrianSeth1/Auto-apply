from __future__ import annotations

from src.intake.schema import JobRequirements, RawJob
from src.matching.profile_v2 import load_resolved_target
from src.matching.scorer_v2 import JobTargetEvaluationV2, evaluate_job_target
from src.orchestration.portfolio import (
    PortfolioCandidateV2,
    PortfolioPolicyV2,
    select_portfolio,
)


def _base_evaluation(*, startup: bool = False) -> JobTargetEvaluationV2:
    job = RawJob(
        source="greenhouse",
        source_id="base",
        company="Base Co",
        title="Implementation Specialist",
        location="Remote, United States",
        employment_type="fulltime",
        description=(
            "Partner with customers on requirements gathering, configure implementation "
            "workflows, lead onboarding and go-live, provide training and documentation. "
            "Candidates need 2+ years of experience. B2B SaaS."
        ),
        requirements=JobRequirements(
            must_have_skills=["implementation", "requirements gathering"],
            experience_years_min=2,
            remote_ok=True,
        ),
        application_url="https://boards.greenhouse.io/base/jobs/1",
        raw_data={"is_startup": startup},
    )
    return evaluate_job_target(job, load_resolved_target("saas-implementation"))


def _candidate(
    index: int,
    *,
    company: str | None = None,
    target: str = "saas-implementation",
    tier: str = "B",
    startup: bool = False,
    group: str | None = None,
    occurrence: str | None = None,
    review_index: float = 70,
    history_excluded: bool = False,
    title: str = "Implementation Specialist",
) -> PortfolioCandidateV2:
    evaluation = _base_evaluation(startup=startup).model_copy(
        update={
            "target_id": target,
            "tier": tier,
            "review_index": review_index,
            "adjusted_review_index": review_index,
        }
    )
    return PortfolioCandidateV2(
        evaluation_id=f"eval-{index}",
        snapshot_id=f"snapshot-{index}",
        occurrence_key=occurrence or f"occurrence-{index}",
        source="greenhouse",
        source_id=str(index),
        company=company or f"Company {index}",
        title=title,
        location="Remote, United States",
        application_url=f"https://boards.greenhouse.io/company/jobs/{index}",
        canonical_group=group,
        target_priority=1.0,
        evaluation=evaluation,
        history_excluded=history_excluded,
        history_reason="already_applied" if history_excluded else None,
    )


def test_never_fills_with_c_or_history_excluded_jobs() -> None:
    candidates = [
        _candidate(1, tier="C"),
        _candidate(2, history_excluded=True),
        _candidate(3, tier="B"),
    ]
    result = select_portfolio(candidates, seed="run-1")
    assert [item.evaluation_id for item in result.selected_core] == ["eval-3"]
    assert result.counts["core_selected"] == 1
    assert result.counts["delivery_shortfall"] == 19
    assert result.counts["reservoir_refill_needed"] == 39


def test_company_cap_allows_two_strong_roles_but_not_three() -> None:
    # 2026-07-16: distinct roles at one company must have distinct titles
    # now that same-title postings collapse (see title-variant test below).
    policy = PortfolioPolicyV2(company_max=2, startup_bonus={"capacity": 0})
    titles = ["Implementation Specialist", "Solutions Engineer", "Customer Success Engineer"]
    candidates = [
        _candidate(
            index,
            company="Same Co",
            group=f"role-{index}",
            review_index=90 - index,
            title=titles[index - 1],
        )
        for index in range(1, 4)
    ]
    result = select_portfolio(candidates, policy=policy, seed="company-two")
    assert len(result.selected_core) == 2
    suppressed = next(
        decision for decision in result.decisions if not decision.selected
    )
    assert suppressed.reason_codes == ("company_cap",)


def test_same_role_remote_variant_consumes_one_slot() -> None:
    """Doppel case (2026-07-15): "Forward Deployed Engineer" and
    "Forward Deployed Engineer - Remote" are the same role posted twice;
    the portfolio must spend one slot, suppressing the variant with an
    explicit reason. Distinct specializations (comma suffixes that are
    not workplace qualifiers) must NOT merge."""
    policy = PortfolioPolicyV2(company_max=2, startup_bonus={"capacity": 0})
    candidates = [
        _candidate(
            1,
            company="Doppel",
            group="role-onsite",
            review_index=90,
            title="Forward Deployed Engineer",
        ),
        _candidate(
            2,
            company="Doppel",
            group="role-remote",
            review_index=88,
            title="Forward Deployed Engineer - Remote",
        ),
        # Control: real specialization variants stay distinct.
        _candidate(
            3,
            company="First Due",
            group="role-fire",
            review_index=80,
            title="Implementation Consultant, Fire Prevention",
        ),
        _candidate(
            4,
            company="First Due",
            group="role-emergency",
            review_index=79,
            title="Implementation Consultant, Emergency Operations",
        ),
    ]
    result = select_portfolio(candidates, policy=policy, seed="variant-dedupe")
    selected_ids = [item.evaluation_id for item in result.selected_core]
    assert "eval-1" in selected_ids
    assert "eval-2" not in selected_ids
    assert "eval-3" in selected_ids
    assert "eval-4" in selected_ids
    suppressed = next(
        decision for decision in result.decisions if decision.evaluation_id == "eval-2"
    )
    assert suppressed.reason_codes == ("title_variant_duplicate",)


def test_target_cap_is_relaxed_instead_of_leaving_core_slots_empty() -> None:
    policy = PortfolioPolicyV2(
        core_capacity=4,
        per_target_max=1,
        company_max=2,
        startup_bonus={"capacity": 0},
    )
    candidates = [_candidate(index, review_index=90 - index) for index in range(1, 5)]
    result = select_portfolio(candidates, policy=policy, seed="soft-target-cap")
    assert len(result.selected_core) == 4
    relaxed = [
        decision
        for decision in result.decisions
        if "target_soft_cap_relaxed" in decision.reason_codes
    ]
    assert len(relaxed) == 3


def test_company_and_canonical_groups_consume_one_core_slot() -> None:
    candidates = [
        _candidate(1, company="Same Co", group="same-role", review_index=75),
        _candidate(2, company="Same Co", group="other-role", review_index=74),
        _candidate(3, company="Different Co", group="same-role", review_index=73),
        _candidate(4, company="Third Co", group="third-role", review_index=72),
    ]
    result = select_portfolio(candidates, seed="run-2")
    selected = result.selected_core
    assert len({item.company_key for item in selected}) == len(selected)
    assert len({item.group_key for item in selected}) == len(selected)
    # Representative occurrence choice is based on provenance quality and a
    # stable tie-break, not fit. The shared canonical group therefore keeps
    # eval-3; the other Same Co role and Third Co remain independently useful.
    assert {item.evaluation_id for item in selected} == {"eval-2", "eval-3", "eval-4"}


def test_cross_target_ownership_prefers_tier_then_review_then_priority() -> None:
    same_occurrence = "one-occurrence"
    candidates = [
        _candidate(
            1,
            target="revenue-operations-analyst",
            group="shared",
            occurrence=same_occurrence,
            review_index=80,
            tier="B",
        ),
        _candidate(
            2,
            target="saas-implementation",
            group="shared",
            occurrence=same_occurrence,
            review_index=69,
            tier="A",
        ),
    ]
    result = select_portfolio(candidates, seed="run-3")
    assert [item.evaluation_id for item in result.selected_core] == ["eval-2"]
    secondary = next(item for item in result.decisions if item.evaluation_id == "eval-1")
    assert "secondary_target" in secondary.reason_codes


def test_five_startup_slots_are_additional_and_core_startup_does_not_reduce_them() -> None:
    policy = PortfolioPolicyV2(
        core_capacity=2,
        per_target_max=5,
        company_max=1,
        startup_bonus={"capacity": 5, "minimum_tier": "B", "company_max": 1, "per_target_max": 2},
    )
    targets = [
        "ai-implementation",
        "saas-implementation",
        "revenue-operations-analyst",
        "associate-solutions-engineering",
        "technical-customer-success",
    ]
    candidates = [_candidate(1, company="Core Startup", startup=True, review_index=90)]
    candidates.append(_candidate(2, company="Core Established", review_index=89))
    for index in range(3, 10):
        candidates.append(
            _candidate(
                index,
                company=f"Bonus Startup {index}",
                target=targets[(index - 3) % len(targets)],
                startup=True,
                review_index=80 - index,
            )
        )
    result = select_portfolio(candidates, policy=policy, seed="run-4")
    assert len(result.selected_core) == 2
    assert len(result.selected_startup_bonus) == 5
    assert len(result.selected_core) + len(result.selected_startup_bonus) == 7
    assert all(item not in result.selected_core for item in result.selected_startup_bonus)


def test_selection_is_exactly_replayable() -> None:
    candidates = [_candidate(index) for index in range(1, 9)]
    left = select_portfolio(candidates, seed="fixed-seed")
    right = select_portfolio(list(reversed(candidates)), seed="fixed-seed")
    assert left.model_dump(mode="json") == right.model_dump(mode="json")
