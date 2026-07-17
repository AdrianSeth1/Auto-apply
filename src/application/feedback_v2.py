"""Structured review feedback and bounded, interpretable preference priors."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from src.core.models import ReviewFeedback, TENANT_DEFAULT

MODEL_VERSION = "feedback-v2.1"

Judgment = Literal[
    "apply_now", "worth_reviewing", "stretch_but_interesting", "not_worth_reviewing"
]
Action = Literal["applied", "saved", "skipped"]

ROLE_REASONS = {"wrong_role_family", "wrong_work_content", "domain_mismatch"}
CANDIDACY_REASONS = {
    "too_senior",
    "insufficient_direct_experience",
    "missing_required_capability",
    "missing_credential",
    "high_selectivity_low_odds",
}
PREFERENCE_REASONS = {
    "location_or_work_mode",
    "compensation",
    "employer_not_interested",
    "travel",
    "employment_type",
}
POSTING_REASONS = {
    "stale_or_ghost",
    "broken_apply_link",
    "misleading_posting",
    "unknown_employer",
}
PROCESS_REASONS = {
    "duplicate",
    "already_seen",
    "already_applied",
    "closed",
    "timing",
    "no_time_today",
    "materials_bad",
}
WEAK_REASONS = {"not_interested_unspecified"}
POSITIVE_REASONS = {"positive_unspecified"}
ALL_REASONS = (
    ROLE_REASONS
    | CANDIDACY_REASONS
    | PREFERENCE_REASONS
    | POSTING_REASONS
    | PROCESS_REASONS
    | WEAK_REASONS
    | POSITIVE_REASONS
)


class ReviewFeedbackInputV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: uuid.UUID
    target_id: str
    judgment: Judgment
    action: Action
    primary_reason: str | None = None
    secondary_reasons: list[str] = Field(default_factory=list)
    free_text: str | None = None

    @model_validator(mode="after")
    def validate_reason(self) -> "ReviewFeedbackInputV2":
        if self.primary_reason is not None and self.primary_reason not in ALL_REASONS:
            raise ValueError(f"Unknown feedback reason {self.primary_reason!r}")
        unknown = set(self.secondary_reasons) - ALL_REASONS
        if unknown:
            raise ValueError(f"Unknown secondary feedback reasons: {sorted(unknown)}")
        if self.judgment == "not_worth_reviewing" and not self.primary_reason:
            raise ValueError("Negative judgments require a primary reason")
        return self


class FeedbackObservationV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str
    judgment: Judgment
    primary_reason: str
    created_at: datetime
    employer_key: str | None = None
    title_family: str | None = None
    canonical_episode: str
    historical_binary_only: bool = False


class FeedbackPriorV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    key: str
    effective_sample_size: float
    base_rate: float
    posterior_rate: float
    point_contribution: float
    reason_mix: dict[str, float]


def derive_learnable(primary_reason: str) -> bool:
    return primary_reason not in PROCESS_REASONS


def persist_feedback(
    session: Session,
    payload: ReviewFeedbackInputV2,
    *,
    review_id: uuid.UUID | None = None,
    tenant_id: str = TENANT_DEFAULT,
) -> ReviewFeedback:
    row = ReviewFeedback(
        tenant_id=tenant_id,
        review_id=review_id,
        evaluation_id=payload.evaluation_id,
        target_id=payload.target_id,
        judgment=payload.judgment,
        action=payload.action,
        primary_reason=payload.primary_reason or "positive_unspecified",
        secondary_reasons=payload.secondary_reasons,
        free_text=payload.free_text,
        learnable=derive_learnable(payload.primary_reason or "positive_unspecified"),
        model_version=MODEL_VERSION,
    )
    session.add(row)
    session.flush()
    return row


def _observation_weight(observation: FeedbackObservationV2, *, now: datetime) -> float:
    if observation.primary_reason in PROCESS_REASONS:
        return 0.0
    base = 0.25 if observation.historical_binary_only else 1.0
    if observation.primary_reason in WEAK_REASONS:
        base *= 0.25
    age_days = max(0.0, (now - observation.created_at.astimezone(UTC)).total_seconds() / 86400)
    return base * math.pow(0.5, age_days / 180.0)


def compute_feedback_adjustment(
    observations: list[FeedbackObservationV2],
    *,
    target_id: str,
    employer_key: str | None,
    title_family: str | None,
    now: datetime | None = None,
) -> tuple[float, tuple[FeedbackPriorV2, ...]]:
    """Compute small target-specific priors with episode dedupe and shrinkage."""

    now = (now or datetime.now(UTC)).astimezone(UTC)
    target_rows = [row for row in observations if row.target_id == target_id]
    deduped: dict[str, FeedbackObservationV2] = {}
    for row in sorted(target_rows, key=lambda item: item.created_at):
        deduped[row.canonical_episode] = row
    rows = list(deduped.values())

    weighted_total = sum(_observation_weight(row, now=now) for row in rows)
    weighted_positive = sum(
        _observation_weight(row, now=now)
        for row in rows
        if row.judgment in {"apply_now", "worth_reviewing"}
    )
    base_rate = weighted_positive / weighted_total if weighted_total else 0.28
    priors: list[FeedbackPriorV2] = []

    def add_prior(kind: str, key: str, subset: list[FeedbackObservationV2], threshold: int) -> None:
        total = sum(_observation_weight(row, now=now) for row in subset)
        if total < threshold:
            return
        positive = sum(
            _observation_weight(row, now=now)
            for row in subset
            if row.judgment in {"apply_now", "worth_reviewing"}
        )
        posterior = (positive + 10 * base_rate) / (total + 10)
        points = max(-3.0, min(3.0, (posterior - base_rate) * 10))
        reasons: dict[str, float] = defaultdict(float)
        for row in subset:
            reasons[row.primary_reason] += _observation_weight(row, now=now)
        priors.append(
            FeedbackPriorV2(
                kind=kind,
                key=key,
                effective_sample_size=round(total, 4),
                base_rate=round(base_rate, 4),
                posterior_rate=round(posterior, 4),
                point_contribution=round(points, 4),
                reason_mix=dict(sorted(reasons.items())),
            )
        )

    if employer_key:
        employer_rows = [row for row in rows if row.employer_key == employer_key]
        add_prior("employer_target", employer_key, employer_rows, 8)
        add_prior("employer", employer_key, employer_rows, 5)
    if title_family:
        add_prior(
            "title_family",
            title_family,
            [row for row in rows if row.title_family == title_family],
            5,
        )
    adjustment = max(-5.0, min(5.0, sum(prior.point_contribution for prior in priors)))
    return round(adjustment, 4), tuple(priors)


__all__ = [
    "ALL_REASONS",
    "FeedbackObservationV2",
    "FeedbackPriorV2",
    "ReviewFeedbackInputV2",
    "compute_feedback_adjustment",
    "derive_learnable",
    "persist_feedback",
]
