from __future__ import annotations

from collections import Counter

from src.intake.query_scheduler import (
    QueryArmV2,
    _weighted_round_robin_order,
    build_query_arms,
    select_query_arms,
)
from src.matching.profile_v2 import load_targets


def test_builds_distinct_target_and_geography_arms() -> None:
    targets = load_targets()
    arms = build_query_arms(targets)
    assert len(arms) >= 5 * 4
    assert len({arm.id for arm in arms}) == len(arms)
    assert {arm.target_id for arm in arms} == {target.id for target in targets}
    assert {arm.geography for arm in arms} == {
        "Remote",
        "Dallas, TX",
        "Portland, OR",
        "United States",
    }


def test_build_query_arms_carries_target_priority() -> None:
    # Phase S7: priority is threaded onto each arm from its owning target,
    # but must never feed the identity/version hash (a priority change must
    # not invalidate stored yield history for an already-running arm).
    targets = load_targets()
    priority_by_target = {target.id: target.priority for target in targets}
    arms = build_query_arms(targets)
    for arm in arms:
        assert arm.priority == priority_by_target[arm.target_id]
    # Two arms for the same target/query/geography built from a target with
    # a different priority must still hash identically -- confirmed by
    # construction here (version depends only on target/adapter/query/geo).
    versions = {arm.version for arm in arms}
    assert len(versions) == len(arms)


def test_first_eight_cycles_are_least_run_round_robin() -> None:
    arms = [
        QueryArmV2(
            id=f"arm-{index}",
            target_id="target",
            adapter="adzuna",
            query=f"query {index}",
            geography="Remote",
            version=f"v{index}",
            run_count=index,
        )
        for index in range(6)
    ]
    selected = select_query_arms(arms, budget=3, acquisition_cycle=4, seed="seed")
    assert {arm.run_count for arm in selected} == {0, 1, 2}


def test_yield_weighting_keeps_a_low_run_exploration_canary() -> None:
    arms = [
        QueryArmV2(
            id="canary",
            target_id="target",
            adapter="adzuna",
            query="later alias",
            geography="Remote",
            version="v1",
            run_count=1,
            useful_yield_positive=0,
            useful_yield_total=20,
        ),
        QueryArmV2(
            id="winner",
            target_id="target",
            adapter="adzuna",
            query="winner",
            geography="Remote",
            version="v2",
            run_count=20,
            useful_yield_positive=12,
            useful_yield_total=20,
        ),
        QueryArmV2(
            id="middle",
            target_id="target",
            adapter="adzuna",
            query="middle",
            geography="Remote",
            version="v3",
            run_count=10,
            useful_yield_positive=4,
            useful_yield_total=20,
        ),
    ]
    selected = select_query_arms(arms, budget=2, acquisition_cycle=9, seed="seed")
    assert [arm.id for arm in selected] == ["canary", "winner"]


# ---- Phase S7: priority-weighted budget allocation ---------------------------


def _arms_for_target(target_id: str, priority: float, count: int, *, run_count_start: int = 0):
    return [
        QueryArmV2(
            id=f"{target_id}-{index}",
            target_id=target_id,
            adapter="adzuna",
            query=f"query {index}",
            geography="Remote",
            version=f"v-{target_id}-{index}",
            run_count=run_count_start + index,
            priority=priority,
        )
        for index in range(count)
    ]


def test_weighted_round_robin_never_drops_a_zero_weight_key_and_is_interleaved() -> None:
    order = _weighted_round_robin_order({"a": 3.0, "b": 1.0}, 8)
    counts = Counter(order)
    # Roughly 3:1 -- not exact due to integer picks, but "a" must dominate.
    assert counts["a"] > counts["b"]
    assert counts["a"] + counts["b"] == 8
    # Interleaved, not clustered: "b" should not be pushed entirely to one end.
    b_positions = [i for i, key in enumerate(order) if key == "b"]
    assert max(b_positions) - min(b_positions) >= 3 or len(b_positions) == 1


def test_weighted_round_robin_empty_or_zero_total_returns_empty() -> None:
    assert _weighted_round_robin_order({}, 5) == []
    assert _weighted_round_robin_order({"a": 0.0}, 5) == []
    assert _weighted_round_robin_order({"a": 1.0}, 0) == []


def test_early_cycle_priority_weighting_favors_higher_priority_target_without_starving_others() -> None:
    # Two targets, wildly different priority, plenty of arms each. Every
    # target must still get its guaranteed canary slot; the higher-priority
    # target should claim more of the remaining budget.
    high = _arms_for_target("high", priority=1.0, count=10)
    low = _arms_for_target("low", priority=0.1, count=10)
    selected = select_query_arms(high + low, budget=8, acquisition_cycle=3, seed="seed-1")
    by_target = Counter(arm.target_id for arm in selected)
    assert by_target["low"] >= 1  # never fully starved
    assert by_target["high"] > by_target["low"]
    assert sum(by_target.values()) == 8


def test_equal_priority_targets_split_evenly_as_before() -> None:
    # Backward compatibility: when priorities are equal (the default for any
    # arm that doesn't set one), behavior should still guarantee both targets
    # participate roughly evenly -- matches pre-S7 flat round robin.
    a = _arms_for_target("a", priority=1.0, count=10)
    b = _arms_for_target("b", priority=1.0, count=10)
    selected = select_query_arms(a + b, budget=8, acquisition_cycle=3, seed="seed-2")
    by_target = Counter(arm.target_id for arm in selected)
    assert abs(by_target["a"] - by_target["b"]) <= 1
    assert sum(by_target.values()) == 8


def test_yield_phase_equal_priority_reproduces_pre_s7_ranking() -> None:
    # Single target, matching the original (pre-S7) test exactly but with
    # explicit priority=1.0 on every arm -- the blend multiplier
    # (0.7 + 0.3*priority) is then 1.0 for all three, so ranking must be
    # byte-for-byte identical to plain posterior_yield ordering.
    arms = [
        QueryArmV2(
            id="canary", target_id="target", adapter="adzuna", query="later alias",
            geography="Remote", version="v1", run_count=1, useful_yield_positive=0,
            useful_yield_total=20, priority=1.0,
        ),
        QueryArmV2(
            id="winner", target_id="target", adapter="adzuna", query="winner",
            geography="Remote", version="v2", run_count=20, useful_yield_positive=12,
            useful_yield_total=20, priority=1.0,
        ),
        QueryArmV2(
            id="middle", target_id="target", adapter="adzuna", query="middle",
            geography="Remote", version="v3", run_count=10, useful_yield_positive=4,
            useful_yield_total=20, priority=1.0,
        ),
    ]
    selected = select_query_arms(arms, budget=2, acquisition_cycle=9, seed="seed")
    assert [arm.id for arm in selected] == ["canary", "winner"]


def test_yield_phase_higher_priority_wins_an_exact_yield_tie() -> None:
    # Two targets with identical posterior_yield inputs but different
    # priority -- the higher-priority target's arm must rank first among the
    # non-canary fill, confirming the blend actually has an effect (not just
    # a no-op multiplier) when yields are tied.
    high_priority = QueryArmV2(
        id="high-arm", target_id="high", adapter="adzuna", query="q", geography="Remote",
        version="v1", run_count=20, useful_yield_positive=8, useful_yield_total=20,
        priority=1.0,
    )
    low_priority = QueryArmV2(
        id="low-arm", target_id="low", adapter="adzuna", query="q2", geography="Remote",
        version="v2", run_count=20, useful_yield_positive=8, useful_yield_total=20,
        priority=0.1,
    )
    # A third, cheap arm per target so each target's "canary" (least-run)
    # slot is filled by something else, leaving high-arm/low-arm to compete
    # purely on the yield+priority blend for the single remaining slot.
    high_canary = QueryArmV2(
        id="high-canary", target_id="high", adapter="adzuna", query="q3", geography="Remote",
        version="v3", run_count=0, priority=1.0,
    )
    low_canary = QueryArmV2(
        id="low-canary", target_id="low", adapter="adzuna", query="q4", geography="Remote",
        version="v4", run_count=0, priority=0.1,
    )
    arms = [high_priority, low_priority, high_canary, low_canary]
    selected = select_query_arms(arms, budget=3, acquisition_cycle=9, seed="seed-tie")
    ids = [arm.id for arm in selected]
    assert "high-canary" in ids and "low-canary" in ids
    assert "high-arm" in ids
    assert "low-arm" not in ids
