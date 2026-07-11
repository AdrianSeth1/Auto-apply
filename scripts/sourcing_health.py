"""Sourcing health report: is the pipeline surfacing jobs worth applying to?

Reads review-queue decisions (the user's actual approve/skip/apply behavior)
and joins them to jobs and plan-run reports. Run any time:

    uv run python scripts/sourcing_health.py

Baseline at first run (2026-07-10): 18 applied / 42 skipped = 30% take rate.
If a plan or source sits far below the overall take rate, its keywords or
boards are producing noise; far above, it deserves more top-N budget.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import load_config  # noqa: E402
from src.core.database import get_session_factory  # noqa: E402
from src.core.models import Job, ReviewQueueEntry  # noqa: E402


def _score_band(score) -> str:
    if score is None:
        return "unscored"
    for floor, label in ((0.8, "0.8+"), (0.6, "0.6-0.8"), (0.4, "0.4-0.6")):
        if score >= floor:
            return label
    return "<0.4"


def _plan_by_run_id() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for report_path in Path("data/plan_runs").glob("*.json"):
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            run_id = report.get("run_id")
            if run_id:
                mapping[str(run_id)] = (
                    report.get("search_profile_id")
                    or report.get("profile_id")
                    or "unknown"
                )
        except (ValueError, OSError):
            continue
    return mapping


def main() -> int:
    plan_names = _plan_by_run_id()
    session_factory = get_session_factory(load_config())
    with session_factory() as session:
        rows = (
            session.query(ReviewQueueEntry, Job)
            .outerjoin(Job, Job.id == ReviewQueueEntry.job_id)
            .all()
        )

    # An entry counts as ACTED ON when the user applied (submitted) or
    # skipped (rejected). Pending entries are excluded from take rates.
    buckets: dict[str, dict[str, list[int]]] = {
        "score band": defaultdict(lambda: [0, 0]),
        "source": defaultdict(lambda: [0, 0]),
        "plan": defaultdict(lambda: [0, 0]),
    }
    skipped_companies: dict[str, int] = defaultdict(int)
    applied = skipped = pending = 0

    for entry, job in rows:
        if entry.status == "submitted":
            outcome_idx = 0
            applied += 1
        elif entry.status == "rejected":
            outcome_idx = 1
            skipped += 1
            if job is not None:
                skipped_companies[job.company] += 1
        else:
            pending += 1
            continue

        score = None
        if isinstance(entry.score_breakdown, dict):
            score = entry.score_breakdown.get("final_score")
        buckets["score band"][_score_band(score)][outcome_idx] += 1
        buckets["source"][(job.source if job else "unknown") or "unknown"][
            outcome_idx
        ] += 1
        buckets["plan"][plan_names.get(str(entry.run_id), "manual/unknown")][
            outcome_idx
        ] += 1

    total_acted = applied + skipped
    print("\n=== Sourcing health ===")
    print(f"acted on: {total_acted}  (applied {applied} / skipped {skipped})"
          f"   pending: {pending}")
    if total_acted:
        print(f"overall take rate: {applied / total_acted:.0%}\n")
    for dimension, data in buckets.items():
        print(f"-- take rate by {dimension} --")
        for key, (a, s) in sorted(
            data.items(), key=lambda kv: -(kv[1][0] + kv[1][1])
        ):
            n = a + s
            print(f"  {key:<24} {a:>3}/{n:<3}  ({a / n:.0%})" if n else "")
        print()
    if skipped_companies:
        worst = sorted(skipped_companies.items(), key=lambda kv: -kv[1])[:8]
        print("-- most-skipped companies (candidates for keyword/board pruning) --")
        for company, count in worst:
            print(f"  {company:<28} skipped {count}x")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
