"""Deterministic global Job Pool V2 portfolio selection."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.config import PROJECT_ROOT
from src.jobs.identity import canonical_fingerprint, normalize_identity_text
from src.matching.profile_v2 import canonical_hash
from src.matching.scorer_v2 import JobTargetEvaluationV2
from src.matching.target_schema import load_unique_yaml


class StartupBonusPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    capacity: int = Field(default=5, ge=0)
    minimum_tier: Literal["A", "B"] = "B"
    company_max: int = Field(default=1, ge=1)
    per_target_max: int = Field(default=2, ge=1)


class ExplorationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tier_b_share: float = Field(default=0.10, ge=0, le=1)


class StretchPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = False
    capacity: int = Field(default=0, ge=0)


class PortfolioPolicyV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[2] = 2
    id: str = "default"
    core_capacity: int = Field(default=20, ge=0)
    reservoir_target: int = Field(default=40, ge=0)
    minimum_core_tier: Literal["A", "B"] = "B"
    per_target_max: int = Field(default=5, ge=1)
    company_max: int = Field(default=1, ge=1)
    startup_bonus: StartupBonusPolicy = Field(default_factory=StartupBonusPolicy)
    exploration: ExplorationPolicy = Field(default_factory=ExplorationPolicy)
    stretch: StretchPolicy = Field(default_factory=StretchPolicy)


class PortfolioCandidateV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_id: str
    snapshot_id: str
    occurrence_key: str
    source: str
    source_id: str
    company: str
    title: str
    location: str | None = None
    application_url: str | None = None
    canonical_group: str | None = None
    target_priority: float = Field(ge=0, le=1)
    evaluation: JobTargetEvaluationV2
    history_excluded: bool = False
    history_reason: str | None = None

    @property
    def group_key(self) -> str:
        return self.canonical_group or canonical_fingerprint(
            company=self.company,
            title=self.title,
            location=self.location,
            application_url=self.application_url,
        ) or f"occurrence:{self.occurrence_key}"

    @property
    def company_key(self) -> str:
        return normalize_identity_text(self.company)

    @property
    def startup(self) -> bool:
        return self.evaluation.employer_assessment.lifecycle == "startup"


class PortfolioDecisionV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_id: str
    canonical_group: str
    owned_target_id: str
    secondary_target_ids: tuple[str, ...] = ()
    company_key: str
    lane: Literal["core", "startup_bonus", "none"] = "none"
    utility: float
    rank: int | None = None
    selected: bool = False
    reason_codes: tuple[str, ...] = ()


class PortfolioSelectionV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str
    seed: str
    selected_core: tuple[PortfolioCandidateV2, ...]
    selected_startup_bonus: tuple[PortfolioCandidateV2, ...]
    decisions: tuple[PortfolioDecisionV2, ...]
    counts: dict[str, int]


def load_portfolio_policy(path: Path | None = None) -> PortfolioPolicyV2:
    path = path or PROJECT_ROOT / "config" / "portfolio.yaml"
    return PortfolioPolicyV2.model_validate(
        load_unique_yaml(path.read_text(encoding="utf-8"))
    )


def _tier_rank(tier: str) -> int:
    return {"A": 0, "B": 1, "C": 2, "D": 3, "unresolved": 4}.get(tier, 5)


def _occurrence_rank(candidate: PortfolioCandidateV2) -> tuple:
    posting = candidate.evaluation.posting_assessment
    kind = {
        "direct_ats": 0,
        "employer_site": 1,
        "aggregator_redirect": 2,
        "recruiter_contact": 3,
        "email": 4,
        "unknown": 5,
        "missing": 6,
    }[posting.application_target_kind]
    completeness = {"full": 0, "partial": 1, "snippet": 2, "missing": 3}[
        posting.description_completeness
    ]
    return (
        kind,
        completeness,
        -posting.freshness_score,
        -candidate.evaluation.confidence,
        candidate.source,
        candidate.company.casefold(),
        candidate.title.casefold(),
        candidate.occurrence_key,
    )


def _owner_rank(candidate: PortfolioCandidateV2) -> tuple:
    evaluation = candidate.evaluation
    return (
        _tier_rank(evaluation.tier),
        -evaluation.adjusted_review_index,
        -evaluation.component_scores["role"],
        -candidate.target_priority,
        evaluation.target_id,
    )


def _exploration_bonus(candidate: PortfolioCandidateV2, seed: str, share: float) -> float:
    if candidate.evaluation.tier != "B" or share <= 0:
        return 0.0
    digest = hashlib.sha256(f"{seed}:{candidate.evaluation_id}".encode()).digest()
    draw = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    return 2.0 if draw < share else 0.0


def _utility(
    candidate: PortfolioCandidateV2,
    *,
    target_count: int,
    policy: PortfolioPolicyV2,
    seed: str,
) -> float:
    underrepresented = 4.0 * candidate.target_priority * max(
        0.0, 1.0 - target_count / policy.per_target_max
    )
    exploration = _exploration_bonus(candidate, seed, policy.exploration.tier_b_share)
    return candidate.evaluation.adjusted_review_index + underrepresented + exploration


# 2026-07-16: workplace/region qualifiers employers append to duplicate
# postings of the SAME role ("Forward Deployed Engineer" vs "Forward
# Deployed Engineer - Remote"). Only these tokens are stripped, only from
# the END of the normalized title, so specialization suffixes that carry
# real meaning ("…, Fire Prevention" vs "…, Emergency Ops") never merge.
_TITLE_VARIANT_QUALIFIERS = frozenset(
    {
        "remote", "hybrid", "onsite", "site",
        "us", "usa", "u", "s", "united", "states",
        "north", "america", "americas", "amer", "ame",
        "emea", "apac", "global", "worldwide",
    }
)


def _title_variant_key(candidate: PortfolioCandidateV2) -> str:
    """Company-scoped key that treats remote/region reposts as one role."""
    tokens = normalize_identity_text(candidate.title).split()
    while tokens and tokens[-1] in _TITLE_VARIANT_QUALIFIERS:
        tokens.pop()
    return " ".join(tokens)


def _representatives_and_owners(
    candidates: list[PortfolioCandidateV2],
) -> tuple[list[PortfolioCandidateV2], dict[str, tuple[str, ...]], dict[str, str]]:
    grouped: dict[str, list[PortfolioCandidateV2]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.group_key].append(candidate)

    owners: list[PortfolioCandidateV2] = []
    secondary: dict[str, tuple[str, ...]] = {}
    suppressed: dict[str, str] = {}
    for group_key, group in sorted(grouped.items()):
        occurrence_keys = sorted({item.occurrence_key for item in group})
        best_occurrence = min(
            occurrence_keys,
            key=lambda key: min(_occurrence_rank(item) for item in group if item.occurrence_key == key),
        )
        occurrence_candidates = [item for item in group if item.occurrence_key == best_occurrence]
        owner = min(occurrence_candidates, key=_owner_rank)
        owners.append(owner)
        close_targets = tuple(
            item.evaluation.target_id
            for item in sorted(occurrence_candidates, key=_owner_rank)
            if item.evaluation_id != owner.evaluation_id
            and abs(item.evaluation.adjusted_review_index - owner.evaluation.adjusted_review_index) <= 5
        )
        secondary[owner.evaluation_id] = close_targets
        for item in group:
            if item.evaluation_id != owner.evaluation_id:
                suppressed[item.evaluation_id] = (
                    "non_representative_occurrence"
                    if item.occurrence_key != best_occurrence
                    else "secondary_target"
                )

    # 2026-07-16 second pass: employers repost the same role as separate
    # ATS postings that differ only by a workplace qualifier (observed:
    # Doppel "Forward Deployed Engineer" + "… - Remote" consumed two of
    # twenty core slots for one role). Different postings mean different
    # snapshots, locations, and canonical groups, so the fingerprint
    # correctly keeps them apart — but the PORTFOLIO should not spend two
    # slots on one role. Keep the best owner per (company, variant-title)
    # and suppress the rest with an explicit auditable reason. This is a
    # selection decision, not an identity merge: nothing is deleted or
    # rewritten, and the suppressed posting stays fully evaluated.
    variant_groups: dict[tuple[str, str], list[PortfolioCandidateV2]] = defaultdict(list)
    for owner in owners:
        variant_key = _title_variant_key(owner)
        if variant_key:
            variant_groups[(owner.company_key, variant_key)].append(owner)
    deduped_owners: list[PortfolioCandidateV2] = []
    for owner in owners:
        variant_key = _title_variant_key(owner)
        group = variant_groups.get((owner.company_key, variant_key), []) if variant_key else []
        if len(group) > 1:
            best = min(group, key=lambda item: (_owner_rank(item), _occurrence_rank(item)))
            if owner.evaluation_id != best.evaluation_id:
                suppressed[owner.evaluation_id] = "title_variant_duplicate"
                continue
        deduped_owners.append(owner)
    return deduped_owners, secondary, suppressed


def select_portfolio(
    candidates: list[PortfolioCandidateV2],
    *,
    policy: PortfolioPolicyV2 | None = None,
    seed: str,
) -> PortfolioSelectionV2:
    """Select A/B core and five additional A/B startup slots without filler."""

    policy = policy or load_portfolio_policy()
    owners, secondary, suppressed = _representatives_and_owners(candidates)
    eligible = [
        item
        for item in owners
        if item.evaluation.tier in {"A", "B"} and not item.history_excluded
    ]
    selected_core: list[PortfolioCandidateV2] = []
    selected_bonus: list[PortfolioCandidateV2] = []
    target_counts: dict[str, int] = defaultdict(int)
    company_counts: dict[str, int] = defaultdict(int)

    relaxed_target_cap_ids: set[str] = set()
    for tier in ("A", "B"):
        tier_pool = [item for item in eligible if item.evaluation.tier == tier]
        while tier_pool and len(selected_core) < policy.core_capacity:
            ranked = sorted(
                tier_pool,
                key=lambda item: (
                    -_utility(
                        item,
                        target_count=target_counts[item.evaluation.target_id],
                        policy=policy,
                        seed=seed,
                    ),
                    item.company_key,
                    item.title.casefold(),
                    item.evaluation_id,
                ),
            )
            chosen = next(
                (
                    item
                    for item in ranked
                    if target_counts[item.evaluation.target_id] < policy.per_target_max
                    and company_counts[item.company_key] < policy.company_max
                ),
                None,
            )
            if chosen is None:
                break
            selected_core.append(chosen)
            target_counts[chosen.evaluation.target_id] += 1
            company_counts[chosen.company_key] += 1
            tier_pool.remove(chosen)

        # Target diversity is a preference, not a reason to return fewer
        # qualified jobs. Once every target has had its protected share,
        # use any remaining core slots for the best unseen A/B jobs while
        # preserving the company cap and every upstream quality gate.
        while tier_pool and len(selected_core) < policy.core_capacity:
            ranked = sorted(
                tier_pool,
                key=lambda item: (
                    -_utility(
                        item,
                        target_count=target_counts[item.evaluation.target_id],
                        policy=policy,
                        seed=seed,
                    ),
                    item.company_key,
                    item.title.casefold(),
                    item.evaluation_id,
                ),
            )
            chosen = next(
                (
                    item
                    for item in ranked
                    if company_counts[item.company_key] < policy.company_max
                ),
                None,
            )
            if chosen is None:
                break
            selected_core.append(chosen)
            relaxed_target_cap_ids.add(chosen.evaluation_id)
            target_counts[chosen.evaluation.target_id] += 1
            company_counts[chosen.company_key] += 1
            tier_pool.remove(chosen)

    bonus_target_counts: dict[str, int] = defaultdict(int)
    bonus_pool = [
        item
        for item in eligible
        if item.startup and item not in selected_core
    ]
    for item in sorted(
        bonus_pool,
        key=lambda value: (
            _tier_rank(value.evaluation.tier),
            -value.evaluation.adjusted_review_index,
            value.company_key,
            value.evaluation_id,
        ),
    ):
        if len(selected_bonus) >= policy.startup_bonus.capacity:
            break
        if company_counts[item.company_key] >= policy.startup_bonus.company_max:
            continue
        if bonus_target_counts[item.evaluation.target_id] >= policy.startup_bonus.per_target_max:
            continue
        selected_bonus.append(item)
        company_counts[item.company_key] += 1
        bonus_target_counts[item.evaluation.target_id] += 1

    core_ids = {item.evaluation_id for item in selected_core}
    bonus_ids = {item.evaluation_id for item in selected_bonus}
    core_rank = {item.evaluation_id: index for index, item in enumerate(selected_core, start=1)}
    bonus_rank = {item.evaluation_id: index for index, item in enumerate(selected_bonus, start=1)}
    owners_by_id = {item.evaluation_id: item for item in owners}
    decisions: list[PortfolioDecisionV2] = []
    for item in sorted(candidates, key=lambda value: value.evaluation_id):
        owner = owners_by_id.get(item.evaluation_id)
        if item.evaluation_id in core_ids:
            lane: Literal["core", "startup_bonus", "none"] = "core"
            selected = True
            rank = core_rank[item.evaluation_id]
            reasons = (
                ("selected_core", "target_soft_cap_relaxed")
                if item.evaluation_id in relaxed_target_cap_ids
                else ("selected_core",)
            )
        elif item.evaluation_id in bonus_ids:
            lane = "startup_bonus"
            selected = True
            rank = bonus_rank[item.evaluation_id]
            reasons = ("selected_startup_bonus",)
        else:
            lane = "none"
            selected = False
            rank = None
            if item.evaluation_id in suppressed:
                reasons = (suppressed[item.evaluation_id],)
            elif item.history_excluded:
                reasons = (item.history_reason or "history_excluded",)
            elif item.evaluation.tier not in {"A", "B"}:
                reasons = ("below_minimum_tier",)
            elif company_counts[item.company_key] >= policy.company_max:
                reasons = ("company_cap",)
            else:
                reasons = ("capacity_or_target_cap",)
        owner_for_decision = owner or item
        decisions.append(
            PortfolioDecisionV2(
                evaluation_id=item.evaluation_id,
                canonical_group=item.group_key,
                owned_target_id=owner_for_decision.evaluation.target_id,
                secondary_target_ids=secondary.get(owner_for_decision.evaluation_id, ()),
                company_key=item.company_key,
                lane=lane,
                utility=round(
                    _utility(
                        owner_for_decision,
                        target_count=0,
                        policy=policy,
                        seed=seed,
                    ),
                    4,
                ),
                rank=rank,
                selected=selected,
                reason_codes=reasons,
            )
        )
    return PortfolioSelectionV2(
        policy_version=canonical_hash(policy),
        seed=seed,
        selected_core=tuple(selected_core),
        selected_startup_bonus=tuple(selected_bonus),
        decisions=tuple(decisions),
        counts={
            "input": len(candidates),
            "owned": len(owners),
            "eligible_ab": len(eligible),
            "reservoir_available_ab": len(eligible),
            "core_selected": len(selected_core),
            "startup_bonus_selected": len(selected_bonus),
            "selected_total": len(selected_core) + len(selected_bonus),
            "delivery_shortfall": max(0, policy.core_capacity - len(selected_core)),
            "reservoir_remaining_after_delivery": max(
                0, len(eligible) - len(selected_core) - len(selected_bonus)
            ),
            "reservoir_refill_needed": max(0, policy.reservoir_target - len(eligible)),
        },
    )


__all__ = [
    "PortfolioCandidateV2",
    "PortfolioDecisionV2",
    "PortfolioPolicyV2",
    "PortfolioSelectionV2",
    "load_portfolio_policy",
    "select_portfolio",
]
