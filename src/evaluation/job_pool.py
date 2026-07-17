"""Deterministic historical replay and prospective blind-set construction.

The functions in this module are deliberately pure: they perform no network
calls and never write review, material, application, or outcome rows.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.matching.profile_v2 import load_candidate, load_targets, resolve_target
from src.matching.scorer_v2 import evaluate_job_target
from src.orchestration.portfolio_run import raw_job_from_payload

Judgment = Literal["apply_now", "worth_reviewing", "not_for_me", "unqualified"]
POSITIVE = {"apply_now", "worth_reviewing"}
JUDGMENT_GAIN = {"unqualified": 0.0, "not_for_me": 0.0, "worth_reviewing": 1.0, "apply_now": 2.0}


class FrozenReplayItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    observed_at: datetime
    canonical_group: str
    target_id: str
    job: dict[str, Any]
    judgment: Judgment | None = None
    v1_score: float | None = None
    v1_tier: str | None = None
    binding_confidence: float = Field(default=1.0, ge=0, le=1)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _rate_interval(successes: int, total: int, z: float = 1.96) -> dict[str, float | int | None]:
    """Wilson score interval for a binomial rate."""
    if total == 0:
        return {"successes": 0, "total": 0, "rate": None, "low": None, "high": None}
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return {
        "successes": successes,
        "total": total,
        "rate": round(p, 6),
        "low": round(max(0.0, center - half), 6),
        "high": round(min(1.0, center + half), 6),
    }


def temporal_group_split(items: Iterable[FrozenReplayItem], development_share: float = 0.7) -> dict[str, str]:
    """Assign whole canonical groups to chronological development/holdout arms."""
    rows = list(items)
    group_first: dict[str, datetime] = {}
    for item in rows:
        group_first[item.canonical_group] = min(
            item.observed_at, group_first.get(item.canonical_group, item.observed_at)
        )
    ordered = sorted(group_first, key=lambda group: (group_first[group], group))
    cut = math.floor(len(ordered) * development_share)
    if len(ordered) > 1:
        cut = max(1, min(len(ordered) - 1, cut))
    development = set(ordered[:cut])
    return {group: ("development" if group in development else "holdout") for group in ordered}


def _precision(rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    labeled = [row for row in rows[:limit] if row["judgment"] is not None]
    return _rate_interval(sum(row["judgment"] in POSITIVE for row in labeled), len(labeled))


def _ndcg(rows: list[dict[str, Any]]) -> float | None:
    labeled = [row for row in rows if row["judgment"] is not None]
    if not labeled:
        return None
    gains = [JUDGMENT_GAIN[row["judgment"]] for row in labeled]
    dcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sorted(gains, reverse=True)
    idcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(ideal))
    return round(dcg / idcg, 6) if idcg else 0.0


def _arm_metrics(rows: list[dict[str, Any]], score_key: str, capacity: int) -> dict[str, Any]:
    ranked = sorted(rows, key=lambda row: (-float(row.get(score_key) or 0), row["item_id"]))
    tier_rates = {}
    for tier in ("A", "B", "C", "D"):
        tier_rows = [row for row in rows if row.get("v2_tier") == tier and row["judgment"] is not None]
        tier_rates[tier] = _rate_interval(
            sum(row["judgment"] in POSITIVE for row in tier_rows), len(tier_rows)
        )
    applied = [row for row in rows if row["judgment"] == "apply_now"]
    recalled = sum(row in ranked[:capacity] for row in applied)
    return {
        "precision_at_5": _precision(ranked, 5),
        "precision_at_10": _precision(ranked, 10),
        "precision_at_capacity": _precision(ranked, capacity),
        "apply_recall_at_capacity": _rate_interval(recalled, len(applied)),
        "ndcg": _ndcg(ranked),
        "tier_positive_rates": tier_rates,
    }


def replay_frozen_items(
    raw_items: Iterable[dict[str, Any] | FrozenReplayItem], *, capacity: int = 20
) -> dict[str, Any]:
    """Evaluate identical frozen inputs with their pinned V1 values and current V2."""
    items = [item if isinstance(item, FrozenReplayItem) else FrozenReplayItem.model_validate(item) for item in raw_items]
    split = temporal_group_split(items)
    candidate = load_candidate()
    targets = load_targets()
    resolved = {target_id: resolve_target(candidate, targets[target_id]) for target_id in targets}
    rows: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: (value.observed_at, value.item_id)):
        if item.target_id not in resolved:
            raise ValueError(f"unknown target_id: {item.target_id}")
        evaluation = evaluate_job_target(raw_job_from_payload(item.job), resolved[item.target_id])
        rows.append(
            {
                "item_id": item.item_id,
                "canonical_group": item.canonical_group,
                "target_id": item.target_id,
                "split": split[item.canonical_group],
                "judgment": item.judgment,
                "binding_confidence": item.binding_confidence,
                "v1_score": item.v1_score,
                "v1_tier": item.v1_tier,
                "v2_score": evaluation.adjusted_review_index,
                "v2_tier": evaluation.tier,
                "confidence": evaluation.confidence,
                "gate_statuses": [gate.status for gate in evaluation.gate_results],
                "reason_codes": [
                    reason.reason_code for reason in (*evaluation.strengths, *evaluation.gaps)
                ],
                "source": item.job.get("source"),
                "company": item.job.get("company"),
            }
        )
    holdout = [row for row in rows if row["split"] == "holdout"]
    by_target: dict[str, Any] = {}
    for target_id in sorted({row["target_id"] for row in holdout}):
        target_rows = [row for row in holdout if row["target_id"] == target_id]
        by_target[target_id] = {
            "v1": _arm_metrics(target_rows, "v1_score", capacity),
            "v2": _arm_metrics(target_rows, "v2_score", capacity),
        }
    sources = Counter(str(row["source"] or "unknown") for row in holdout)
    companies = Counter(str(row["company"] or "unknown") for row in holdout)
    groups = Counter(row["canonical_group"] for row in holdout)
    result = {
        "schema_version": "job-pool-replay.v1",
        "input_count": len(rows),
        "development_count": sum(row["split"] == "development" for row in rows),
        "holdout_count": len(holdout),
        "canonical_group_count": len(split),
        "overall": {
            "v1": _arm_metrics(holdout, "v1_score", capacity),
            "v2": _arm_metrics(holdout, "v2_score", capacity),
            "sources": dict(sorted(sources.items())),
            "top_companies": companies.most_common(10),
            "duplicate_member_rate": round(sum(count > 1 for count in groups.values()) / max(1, len(groups)), 6),
            "unknown_gate_rate": round(
                sum("unknown" in row["gate_statuses"] for row in holdout) / max(1, len(holdout)), 6
            ),
        },
        "by_target": by_target,
        "rows": rows,
    }
    result["content_hash"] = hashlib.sha256(canonical_json(result).encode()).hexdigest()
    return result


def build_blind_set(
    replay: dict[str, Any], *, seed: str, minimum: int = 50, per_target_goal: int = 10
) -> dict[str, Any]:
    """Build a seeded set without exposing arm, score, tier, or selection fields."""
    eligible = [row for row in replay["rows"] if row["split"] == "holdout"]
    rng = random.Random(seed)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        buckets[row["target_id"]].append(row)
    chosen: list[dict[str, Any]] = []
    for target_id in sorted(buckets):
        values = buckets[target_id][:]
        rng.shuffle(values)
        chosen.extend(values[:per_target_goal])
    chosen_ids = {row["item_id"] for row in chosen}
    remainder = [row for row in eligible if row["item_id"] not in chosen_ids]
    rng.shuffle(remainder)
    chosen.extend(remainder[: max(0, minimum - len(chosen))])
    rng.shuffle(chosen)
    public_items = [
        {
            "item_id": row["item_id"],
            "target_id": row["target_id"],
            "presentation_order": index,
            "judgment": None,
            "primary_reason": None,
            "secondary_reasons": [],
        }
        for index, row in enumerate(chosen, start=1)
    ]
    return {
        "schema_version": "job-pool-blind.v1",
        "seed": seed,
        "count": len(public_items),
        "items": public_items,
    }


__all__ = [
    "FrozenReplayItem",
    "build_blind_set",
    "canonical_json",
    "replay_frozen_items",
    "temporal_group_split",
]
