"""Interactively run any subset of the overnight automation plans right now.

2026-07-10: manual counterpart to the 2:30am Beat schedule. Loads the same
plans from config/automation_plans.yaml and enqueues orchestration.plan_run
with EXACTLY the kwargs Beat would send (via schedule_entry_for_plan), so a
manual run behaves identically to an overnight one: search -> score ->
top-N -> review queue -> materials.

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


def main() -> int:
    plans = [
        p for p in load_automation_plans_data()["plans"] if p.get("enabled", True)
    ]
    if not plans:
        print("No enabled automation plans found in config/automation_plans.yaml.")
        return 1

    print("\nOvernight plans (same ones Beat runs at 2:30-3:30am):\n")
    for index, plan in enumerate(plans, 1):
        print(
            f"  {index}. {plan['id']:<26} "
            f"search: {plan.get('search_profile_id') or '(none)':<26} "
            f"resume: {plan.get('profile_id'):<22} top-N: {plan.get('top_n')}"
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
        "Override top-N for this run? (blank = keep each plan's setting): "
    ).strip()
    override = None
    if override_raw:
        try:
            override = max(1, min(int(override_raw), 25))
        except ValueError:
            print("Not a number — keeping each plan's own top-N.")

    from src.tasks.app import celery_app  # noqa: PLC0415 -- needs Redis up

    for plan in picked:
        kwargs = dict(schedule_entry_for_plan(plan)["kwargs"])
        if override is not None:
            kwargs["top_n"] = override
        result = celery_app.send_task(
            "orchestration.plan_run", kwargs=kwargs, queue="search"
        )
        print(f"  queued {plan['id']}  (task {result.id})")

    print(
        "\nDone. Plans run one at a time on the worker (roughly 1-3 min of "
        "search each, then material generation in the background). Results "
        "land in Awaiting Review; reports under data/plan_runs/."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled — nothing queued.")
        raise SystemExit(0) from None
