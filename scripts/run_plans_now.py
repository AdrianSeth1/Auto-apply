"""Interactively practice any subset of the overnight automation plans.

2026-07-10: manual counterpart to the 2:30am Beat schedule. Loads the same
plans from config/automation_plans.yaml and uses the same validated task and
payload contract as Beat. Manual runs force dry_run=True: they search, score,
and report selection yield without creating review cards or materials.

Requires the stack to be running (worker + Redis); launched via
"Run Plans Now.bat".
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/run_plans_now.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.application.automation_plans import (  # noqa: E402
    load_automation_plans_data,
    schedule_entry_for_plan,
)
from src.application.schedule_control import dispatch_schedule_entry  # noqa: E402
from src.core.models import TENANT_DEFAULT  # noqa: E402


def main() -> int:
    plans = [
        p for p in load_automation_plans_data()["plans"] if p.get("enabled", True)
    ]
    if not plans:
        print("No enabled automation plans found in config/automation_plans.yaml.")
        return 1

    print("\nPractice plans (dry-run; no review cards, materials, or submissions):\n")
    for index, plan in enumerate(plans, 1):
        is_portfolio = plan.get("task") == "orchestration.portfolio_run"
        limit_label = (
            f"capacity: {plan.get('canary_capacity')}"
            if is_portfolio
            else f"top-N: {plan.get('top_n')}"
        )
        print(
            f"  {index}. {plan['id']:<26} "
            f"search: {plan.get('search_profile_id') or '(none)':<26} "
            f"resume: {plan.get('profile_id'):<22} {limit_label}"
        )

    choice = input(
        "\nRun which? (e.g. '1,3' or 'all', blank to cancel): "
    ).strip().lower()
    if not choice:
        print("Cancelled — nothing queued.")
        return 0
    if choice == "all":
        picked = plans
    else:
        try:
            picked = [
                plans[int(token) - 1]
                for token in choice.replace(" ", "").split(",")
                if token
            ]
        except (ValueError, IndexError):
            print("Didn't understand that — use numbers from the list, like '1,3'.")
            return 1

    override_raw = input(
        "Override selection limit for this practice run? (blank = keep plan setting): "
    ).strip()
    override = None
    if override_raw:
        try:
            override = max(1, min(int(override_raw), 25))
        except ValueError:
            print("Not a number — keeping each plan's own top-N.")

    for plan in picked:
        entry = schedule_entry_for_plan(plan, dry_run_override=True)
        if override is not None:
            if entry["task"] == "orchestration.portfolio_run":
                entry["kwargs"]["canary_capacity"] = min(override, 20)
            else:
                entry["kwargs"]["top_n"] = override
        result = dispatch_schedule_entry(entry, tenant_id=TENANT_DEFAULT)
        task_suffix = f"  (task {result['task_id']})" if result.get("task_id") else ""
        print(f"  queued {plan['id']} as {result['enqueued']}{task_suffix}")

    print(
        "\nDone. Practice plans run on the worker and record selection/yield "
        "results without creating review entries, materials, or submissions."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled — nothing queued.")
        raise SystemExit(0) from None
