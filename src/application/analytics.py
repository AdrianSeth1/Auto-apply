"""Outcome analytics — does the match score actually predict responses?

Aggregates submitted applications by score band and by resume profile so
the dashboard can show which score ranges and which resume variants
convert into OA/interview/offer. Pure read; recomputed per request (the
input is at most a few thousand rows).

Outcome semantics (``Application.outcome``):
  * ``pending`` / NULL — no reply yet
  * ``rejected``       — replied, negative
  * ``oa`` / ``interview`` / ``offer`` — replied, positive signal
"""

from __future__ import annotations

import logging

from src.core.config import load_config

logger = logging.getLogger("autoapply.application.analytics")

POSITIVE_OUTCOMES = ("oa", "interview", "offer")
SCORE_BANDS = (
    (0.8, "0.8 – 1.0"),
    (0.6, "0.6 – 0.8"),
    (0.4, "0.4 – 0.6"),
    (0.2, "0.2 – 0.4"),
    (0.0, "0.0 – 0.2"),
)


def load_outcome_analytics() -> dict:
    """Aggregate outcomes for all submitted, non-deleted applications."""
    from src.core.database import get_session_factory
    from src.core.models import Application, Job

    try:
        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            rows = (
                session.query(
                    Application.match_score,
                    Application.outcome,
                    Application.submitted_at,
                    Job.raw_data,
                    Job.title,
                )
                .join(Job, Job.id == Application.job_id)
                .filter(
                    Application.deleted_at.is_(None),
                    Application.submitted_at.isnot(None),
                )
                .all()
            )
    except Exception as exc:  # noqa: BLE001 -- analytics must never 500 the dashboard
        logger.warning("Outcome analytics query failed: %s", exc)
        return {
            "ok": False,
            "error": str(exc),
            "total_submitted": 0,
            "by_score_band": [],
            "by_profile": [],
            "overall": _bucket(),
        }

    overall = _bucket()
    by_band: dict[str, dict] = {label: _bucket() for _, label in SCORE_BANDS}
    by_band["unscored"] = _bucket()
    by_profile: dict[str, dict] = {}

    for match_score, outcome, _submitted_at, raw_data, _title in rows:
        _tally(overall, outcome)
        _tally(by_band[_band_label(match_score)], outcome)
        profile = (raw_data or {}).get("best_profile") or "unknown"
        _tally(by_profile.setdefault(profile, _bucket()), outcome)

    return {
        "ok": True,
        "error": None,
        "total_submitted": overall["total"],
        "overall": _finalize(overall),
        "by_score_band": [
            {"band": label, **_finalize(by_band[label])}
            for _, label in SCORE_BANDS
            if by_band[label]["total"]
        ]
        + (
            [{"band": "unscored", **_finalize(by_band["unscored"])}]
            if by_band["unscored"]["total"]
            else []
        ),
        "by_profile": sorted(
            (
                {"profile": profile, **_finalize(bucket)}
                for profile, bucket in by_profile.items()
                if bucket["total"]
            ),
            key=lambda item: -item["total"],
        ),
    }


def _bucket() -> dict:
    return {"total": 0, "pending": 0, "rejected": 0, "positive": 0}


def _tally(bucket: dict, outcome: str | None) -> None:
    bucket["total"] += 1
    if outcome in POSITIVE_OUTCOMES:
        bucket["positive"] += 1
    elif outcome == "rejected":
        bucket["rejected"] += 1
    else:
        bucket["pending"] += 1


def _finalize(bucket: dict) -> dict:
    responded = bucket["positive"] + bucket["rejected"]
    return {
        **bucket,
        "response_rate": round(responded / bucket["total"], 3) if bucket["total"] else 0.0,
        "positive_rate": round(bucket["positive"] / bucket["total"], 3)
        if bucket["total"]
        else 0.0,
    }


def _band_label(match_score: float | None) -> str:
    if match_score is None:
        return "unscored"
    for floor, label in SCORE_BANDS:
        if match_score >= floor:
            return label
    return SCORE_BANDS[-1][1]
