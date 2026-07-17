from __future__ import annotations

from typing import Any

from scripts import run_plans_now


def test_manual_runner_dispatches_v2_plan_as_dry_run(monkeypatch) -> None:
    plan = {
        "id": "nightly-portfolio-v2",
        "name": "Job Pool V2 Canary",
        # Reproduce the inconsistent shape that caused the original bug.
        "task": "orchestration.plan_run",
        "pipeline_version": "v2",
        "target_ids": ["ai-implementation"],
        "profile_id": "canonical candidate",
        "search_profile_id": "global V2 acquisition",
        "canary_capacity": 5,
        "dry_run": False,
        "enabled": True,
    }
    monkeypatch.setattr(
        run_plans_now,
        "load_automation_plans_data",
        lambda: {"plans": [plan]},
    )
    answers = iter(["all", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    captured: dict[str, Any] = {}

    def _dispatch(entry: dict[str, Any], *, tenant_id: str) -> dict[str, str]:
        captured["entry"] = entry
        captured["tenant_id"] = tenant_id
        return {
            "enqueued": entry["task"],
            "queue": "search",
            "task_id": "practice-task",
        }

    monkeypatch.setattr(run_plans_now, "dispatch_schedule_entry", _dispatch)

    assert run_plans_now.main() == 0
    assert captured["entry"]["task"] == "orchestration.portfolio_run"
    assert captured["entry"]["kwargs"]["pipeline_version"] == "v2"
    assert captured["entry"]["kwargs"]["dry_run"] is True

