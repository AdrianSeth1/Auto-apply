from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.application.feedback_v2 import (
    FeedbackObservationV2,
    ReviewFeedbackInputV2,
    compute_feedback_adjustment,
    derive_learnable,
)


NOW = datetime(2026, 7, 12, tzinfo=UTC)


def test_process_and_material_reasons_never_train_fit() -> None:
    assert not derive_learnable("duplicate")
    assert not derive_learnable("materials_bad")
    assert derive_learnable("wrong_role_family")
    assert derive_learnable("too_senior")


def test_negative_feedback_requires_known_structured_reason() -> None:
    payload = ReviewFeedbackInputV2(
        evaluation_id="00000000-0000-0000-0000-000000000001",
        target_id="saas-implementation",
        judgment="not_worth_reviewing",
        action="skipped",
        primary_reason="too_senior",
    )
    assert payload.primary_reason == "too_senior"


def test_feedback_priors_are_target_specific_smoothed_and_bounded() -> None:
    rows = []
    for index in range(10):
        rows.append(
            FeedbackObservationV2(
                target_id="saas-implementation",
                judgment="not_worth_reviewing" if index < 9 else "worth_reviewing",
                primary_reason="employer_not_interested",
                created_at=NOW - timedelta(days=index),
                employer_key="stripe",
                title_family="implementation",
                canonical_episode=f"episode-{index}",
            )
        )
    # A positive target baseline at other employers makes the repeated Stripe
    # skips informative without turning company identity into a hard block.
    rows.extend(
        FeedbackObservationV2(
            target_id="saas-implementation",
            judgment="worth_reviewing",
            primary_reason="not_interested_unspecified",
            created_at=NOW - timedelta(days=index),
            employer_key="acme",
            title_family="implementation",
            canonical_episode=f"acme-{index}",
        )
        for index in range(10)
    )
    # Other-target labels cannot leak into this adjustment.
    rows.extend(
        FeedbackObservationV2(
            target_id="ai-implementation",
            judgment="apply_now",
            primary_reason="not_interested_unspecified",
            created_at=NOW,
            employer_key="stripe",
            title_family="implementation",
            canonical_episode=f"ai-{index}",
        )
        for index in range(20)
    )
    adjustment, priors = compute_feedback_adjustment(
        rows,
        target_id="saas-implementation",
        employer_key="stripe",
        title_family="implementation",
        now=NOW,
    )
    assert -5 <= adjustment < 0
    assert priors
    assert any(
        prior.kind.startswith("employer") and prior.effective_sample_size <= 10
        for prior in priors
    )
    assert all(prior.effective_sample_size <= 20 for prior in priors)
    assert all(-3 <= prior.point_contribution <= 3 for prior in priors)


def test_duplicate_episode_contributes_only_one_label() -> None:
    rows = [
        FeedbackObservationV2(
            target_id="saas-implementation",
            judgment="apply_now",
            primary_reason="not_interested_unspecified",
            created_at=NOW - timedelta(hours=1),
            employer_key="acme",
            title_family="implementation",
            canonical_episode="same",
        ),
        FeedbackObservationV2(
            target_id="saas-implementation",
            judgment="not_worth_reviewing",
            primary_reason="wrong_role_family",
            created_at=NOW,
            employer_key="acme",
            title_family="implementation",
            canonical_episode="same",
        ),
    ]
    adjustment, priors = compute_feedback_adjustment(
        rows,
        target_id="saas-implementation",
        employer_key="acme",
        title_family="implementation",
        now=NOW,
    )
    assert adjustment == 0
    assert not priors  # one effective label is below every safeguard threshold
