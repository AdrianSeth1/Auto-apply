from datetime import UTC, datetime, timedelta

from src.evaluation.job_pool import build_blind_set, canonical_json, temporal_group_split
from src.evaluation.job_pool import FrozenReplayItem


def _item(index: int, group: str) -> FrozenReplayItem:
    return FrozenReplayItem(
        item_id=f"i-{index}",
        observed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index),
        canonical_group=group,
        target_id="ai-implementation",
        job={"source": "greenhouse"},
    )


def test_temporal_split_never_leaks_a_canonical_group():
    items = [_item(1, "same"), _item(9, "same"), _item(2, "b"), _item(3, "c")]
    split = temporal_group_split(items)
    assert set(split) == {"same", "b", "c"}
    assert split[items[0].canonical_group] == split[items[1].canonical_group]


def test_blind_set_is_seeded_and_hides_arm_tier_and_scores():
    replay = {
        "rows": [
            {
                "item_id": f"i-{i}",
                "target_id": "ai-implementation",
                "split": "holdout",
                "v1_score": i,
                "v2_score": 100 - i,
                "v2_tier": "A",
            }
            for i in range(12)
        ]
    }
    first = build_blind_set(replay, seed="fixed", minimum=10)
    second = build_blind_set(replay, seed="fixed", minimum=10)
    assert canonical_json(first) == canonical_json(second)
    forbidden = {"v1_score", "v2_score", "v2_tier", "hidden_arm", "selected"}
    assert all(not (forbidden & set(item)) for item in first["items"])
