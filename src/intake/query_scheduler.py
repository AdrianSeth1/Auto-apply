"""Deterministic query-arm generation and non-starving scheduling."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.matching.profile_v2 import canonical_hash
from src.matching.target_schema import TargetSpecV2, normalize_phrase


class QueryArmV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    target_id: str
    adapter: str
    query: str
    geography: str
    version: str
    run_count: int = Field(default=0, ge=0)
    useful_yield_positive: float = Field(default=0.0, ge=0)
    useful_yield_total: float = Field(default=0.0, ge=0)
    active: bool = True
    # Phase S7 (2026-07-13): the owning target's TargetSpecV2.priority (0-1),
    # threaded through here so select_query_arms can weight discovery budget
    # toward higher-priority targets. Deliberately NOT part of the arm's
    # identity/version hash (computed in build_query_arms before priority is
    # attached) -- a priority change must never invalidate an arm's stored
    # yield history. Defaults to 1.0 (no reweighting) for any caller that
    # doesn't set it, e.g. tests and the schedulable arms.
    priority: float = Field(default=1.0, ge=0, le=1)

    @property
    def posterior_yield(self) -> float:
        target_base = 0.28
        return (self.useful_yield_positive + 10 * target_base) / (
            self.useful_yield_total + 10
        )


def build_query_arms(
    targets: list[TargetSpecV2],
    *,
    adapter: str = "adzuna",
    geographies: tuple[str, ...] = ("Remote", "Dallas, TX", "Portland, OR", "United States"),
) -> list[QueryArmV2]:
    arms: list[QueryArmV2] = []
    for target in targets:
        queries = target.discovery.query_terms or [
            *target.role.core_titles,
            *target.role.adjacent_titles,
        ]
        for query in queries:
            for geography in geographies:
                identity = {
                    "target": target.id,
                    "adapter": adapter,
                    "query": normalize_phrase(query),
                    "geography": normalize_phrase(geography),
                }
                version = canonical_hash(identity)
                arms.append(
                    QueryArmV2(
                        id=version[:20],
                        target_id=target.id,
                        adapter=adapter,
                        query=query,
                        geography=geography,
                        version=version,
                        priority=target.priority,
                    )
                )
    return arms


def _weighted_round_robin_order(weights: dict[str, float], n: int) -> list[str]:
    """Nginx-style smooth weighted round robin: n picks from `weights`'
    keys, evenly interleaved (not clustered) and proportional to weight.

    Every key with a positive total weight appears in the output; a key
    with weight 0 never does (matches "reweight, don't starve" -- weight 0
    can only happen if a target's priority were 0, in which case it truly
    shouldn't be scheduled at all).
    """
    if not weights or n <= 0:
        return []
    current = dict.fromkeys(weights, 0.0)
    total = sum(weights.values())
    if total <= 0:
        return []
    keys_sorted = sorted(weights)
    order: list[str] = []
    for _ in range(n):
        for key in keys_sorted:
            current[key] += weights[key]
        pick = max(keys_sorted, key=lambda key: (current[key], key))
        order.append(pick)
        current[pick] -= total
    return order


def select_query_arms(
    arms: list[QueryArmV2],
    *,
    budget: int,
    acquisition_cycle: int,
    seed: str,
) -> list[QueryArmV2]:
    """Round-robin first eight cycles, then shrunk yield plus canaries."""

    active = [arm for arm in arms if arm.active]
    if budget <= 0 or not active:
        return []

    def stable_draw(arm: QueryArmV2) -> float:
        digest = hashlib.sha256(f"{seed}:{arm.id}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64 - 1)

    by_target: dict[str, list[QueryArmV2]] = {}
    for arm in active:
        by_target.setdefault(arm.target_id, []).append(arm)
    target_canaries = [
        min(values, key=lambda arm: (arm.run_count, stable_draw(arm), arm.id))
        for _, values in sorted(by_target.items())
    ]
    target_canaries = sorted(
        target_canaries, key=lambda arm: (arm.run_count, stable_draw(arm), arm.id)
    )

    # Phase S7: one priority per target (arms for the same target should all
    # carry the same value; take whichever is seen first if they somehow
    # differ rather than erroring over a scheduling-weight mismatch).
    priority_by_target = {arm.target_id: arm.priority for arm in reversed(active)}

    if acquisition_cycle < 8:
        selected = target_canaries[:budget]
        selected_ids = {arm.id for arm in selected}
        # Fill additional slots round-robin by target. The previous global
        # least-run sort could give four of ten calls to one target and only
        # one to another, which starved RevOps/TCS despite nominal canaries.
        # Phase S7: the fill order is now priority-weighted (smooth weighted
        # round robin) instead of a flat pass -- every target still gets its
        # canary slot above regardless of priority, so nothing is starved;
        # higher-priority targets just claim a larger share of the EXTRA
        # slots beyond that guaranteed minimum.
        remaining_by_target = {
            target_id: sorted(
                (arm for arm in values if arm.id not in selected_ids),
                key=lambda arm: (arm.run_count, stable_draw(arm), arm.id),
            )
            for target_id, values in sorted(by_target.items())
        }
        fill_needed = budget - len(selected)
        if fill_needed > 0 and remaining_by_target:
            weights = {
                target_id: max(priority_by_target.get(target_id, 1.0), 0.01)
                for target_id in remaining_by_target
            }
            # Ask for more picks than needed so exhausted targets can be
            # skipped without under-filling the budget.
            order = _weighted_round_robin_order(weights, fill_needed * 4)
            for target_id in order:
                if len(selected) >= budget:
                    break
                values = remaining_by_target.get(target_id)
                if values:
                    arm = values.pop(0)
                    selected.append(arm)
                    selected_ids.add(arm.id)
            # Fallback sweep: the weighted order can theoretically run out
            # (all requested picks landed on targets that were already
            # exhausted) while arms remain on a target that just wasn't
            # picked enough times. Plain round robin over whatever's left
            # guarantees the budget still gets filled when arms exist for it.
            while len(selected) < budget:
                added = False
                for target_id in sorted(remaining_by_target):
                    values = remaining_by_target[target_id]
                    if values and len(selected) < budget:
                        arm = values.pop(0)
                        selected.append(arm)
                        selected_ids.add(arm.id)
                        added = True
                if not added:
                    break
        return selected[:budget]

    # Reserve at least one slot for the least-run arm so poor historical yield
    # can never permanently starve an alias/geography.
    canaries = target_canaries[:budget]
    canary_ids = {arm.id for arm in canaries}
    # Phase S7: a gentle priority blend on top of posterior_yield -- a
    # priority-1.0 target's arms rank exactly as before (multiplier 1.0);
    # lower-priority targets get a modest discount (as low as x0.7 at
    # priority 0.0) so real yield performance still dominates the ranking
    # rather than being overridden by a static preference.
    def _priority_weighted_yield(arm: QueryArmV2) -> float:
        return arm.posterior_yield * (0.7 + 0.3 * arm.priority)

    ranked = sorted(
        (arm for arm in active if arm.id not in canary_ids),
        key=lambda arm: (-_priority_weighted_yield(arm), arm.run_count, stable_draw(arm), arm.id),
    )
    selected = list(canaries)
    selected.extend(ranked[: max(0, budget - len(selected))])
    return selected[:budget]


__all__ = ["QueryArmV2", "build_query_arms", "select_query_arms"]
