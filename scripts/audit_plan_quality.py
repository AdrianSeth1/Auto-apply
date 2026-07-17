"""Rehearse enabled automation plans and print their actual selections.

This runs the same search and scoring path as ``Run Plans Now.bat`` but forces
``dry_run=True``. It never creates review cards or generates application
materials. Existing pending review cards are excluded, matching the behavior
of the next real run.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow ``uv run python scripts/audit_plan_quality.py`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.application.automation_plans import (  # noqa: E402
    load_automation_plans_data,
    schedule_entry_for_plan,
)
from src.orchestration.plan_run import run_plan  # noqa: E402


async def main() -> int:
    requested = set(sys.argv[1:])
    plans = [
        plan
        for plan in load_automation_plans_data()["plans"]
        if plan.get("enabled", True)
        and (not requested or plan.get("id") in requested)
    ]
    if requested and not plans:
        print(f"No enabled plans matched: {', '.join(sorted(requested))}")
        return 2
    reports: list[dict] = []
    for plan in plans:
        kwargs = dict(schedule_entry_for_plan(plan)["kwargs"])
        kwargs.pop("automation_plan_id", None)
        kwargs.pop("automation_plan_name", None)
        kwargs["tenant_id"] = "default"
        kwargs["dry_run"] = True
        report = await run_plan(**kwargs)
        payload = report.to_dict()
        payload["automation_plan_id"] = plan["id"]
        reports.append(payload)

        print(
            f"\n{plan['id']}: raw={report.raw_jobs_fetched}, "
            f"search-filtered={report.search_filtered_out}, "
            f"seen={report.total_jobs_seen}, "
            f"role-compatible={report.role_compatible}, "
            f"weak-employers={report.employer_quality_rejected}, "
            f"below-score-floor={report.low_score_rejected}, "
            f"exact-duplicates={report.exact_duplicates_removed}, "
            f"already-applied={report.previously_applied_removed}, "
            f"already-pending={report.pending_deduplicated}, "
            f"selected={report.selected}, startups={report.startup_selected_total}"
        )
        print(f"  sources: {report.source_counts}")
        for job in report.selected_jobs:
            marker = "startup" if job["is_startup"] else job["employer_type"] or "employer"
            print(
                f"  {job['score']:.3f} | {job['company']} | {job['title']} "
                f"| {marker} | {job['source']}"
            )

    output = Path("data/audits/latest_plan_quality.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"\nFull machine-readable audit: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
