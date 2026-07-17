"""Phase 17.1 -- plan_run orchestrator tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.orchestration.plan_run import (
    PLAN_RUN_PAUSE_SENTINEL_NAME,
    PlanRunError,
    PlanRunReport,
    _role_compatible,
    plan_run_pause_sentinel_path,
    plan_runs_paused,
    run_plan,
)


def _async(coro):
    return asyncio.run(coro)


def _clock():
    current = datetime(2026, 5, 16, 9, 0, tzinfo=UTC)

    def now():
        nonlocal current
        out = current
        current = current + timedelta(seconds=1)
        return out

    return now


async def _search_with_jobs(**_kwargs):
    return {
        "jobs": [
            {"id": "job-1", "company": "Acme", "title": "Engineer"},
            {"id": "job-2", "company": "Beta", "title": "Developer"},
        ]
    }


class _Breakdown:
    def __init__(self, job_id: str, score: float, *, is_startup: bool = False):
        self.job_id = job_id
        self.company = f"Company {job_id}"
        self.title = "Engineer"
        self.final_score = score
        self.disqualified = False
        self.job_snapshot_id = None
        self.is_startup = is_startup

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "company": self.company,
            "title": self.title,
            "final_score": self.final_score,
            "disqualified": self.disqualified,
        }


def _score(_jobs, _profile_id):
    return [_Breakdown("job-1", 0.9), _Breakdown("job-2", 0.8)]


def test_pause_sentinel_path(tmp_path: Path):
    assert plan_run_pause_sentinel_path(tmp_path) == (
        tmp_path / "data" / PLAN_RUN_PAUSE_SENTINEL_NAME
    )


def test_pause_state(tmp_path: Path):
    path = tmp_path / "data" / PLAN_RUN_PAUSE_SENTINEL_NAME
    path.parent.mkdir(parents=True)
    path.write_text("paused")
    assert plan_runs_paused(tmp_path)
    path.unlink()
    assert not plan_runs_paused(tmp_path)


def test_run_plan_requires_tenant():
    with pytest.raises(PlanRunError):
        _async(run_plan(tenant_id=""))


def test_run_plan_dry_run_selects_jobs(tmp_path: Path):
    report = _async(
        run_plan(
            tenant_id="default",
            profile_id="default",
            top_n=1,
            dry_run=True,
            search_fn=_search_with_jobs,
            score_fn=_score,
            pause_root=tmp_path,
            now=_clock(),
        )
    )

    assert isinstance(report, PlanRunReport)
    assert report.status == "ok"
    assert report.total_jobs_seen == 2
    assert report.qualified == 2
    assert report.selected == 1
    assert report.materials_task_ids == []


def test_startup_bonus_does_not_displace_top_n(tmp_path: Path):
    async def search(**_kwargs):
        return {
            "jobs": [
                {"id": f"job-{index}", "company": "Company", "title": "Role"}
                for index in range(1, 9)
            ]
        }

    def score(_jobs, _profile_id):
        return [
            _Breakdown(f"job-{index}", 1 - index / 100, is_startup=index >= 4)
            for index in range(1, 9)
        ]

    report = _async(
        run_plan(
            tenant_id="default",
            profile_id="default",
            top_n=3,
            dry_run=True,
            search_fn=search,
            score_fn=score,
            pause_root=tmp_path,
            now=_clock(),
        )
    )

    assert report.selected == 8
    assert report.startup_bonus_selected == 5
    assert len(report.selected_jobs) == 8
    assert report.selected_jobs[0]["job_id"] == "job-1"
    assert sum(job["is_startup"] for job in report.selected_jobs) == 5


def test_exact_company_title_duplicates_use_one_selection_slot(tmp_path: Path):
    async def search(**_kwargs):
        return {"jobs": [{"id": f"job-{index}"} for index in range(1, 4)]}

    def score(_jobs, _profile_id):
        first = _Breakdown("job-1", 0.9)
        second = _Breakdown("job-2", 0.8)
        third = _Breakdown("job-3", 0.7)
        first.company = second.company = "Same Company"
        first.title = second.title = "Revenue Operations Analyst"
        third.company = "Different Company"
        third.title = "Business Analyst"
        return [first, second, third]

    report = _async(
        run_plan(
            tenant_id="default",
            profile_id="default",
            top_n=3,
            dry_run=True,
            search_fn=search,
            score_fn=score,
            pause_root=tmp_path,
            now=_clock(),
        )
    )

    assert report.selected == 2
    assert report.exact_duplicates_removed == 1
    assert [job["job_id"] for job in report.selected_jobs] == ["job-1", "job-3"]


@pytest.mark.parametrize(
    ("profile", "title", "expected"),
    [
        ("analyst", "Revenue Operations Analyst", True),
        ("analyst", "Full Stack Engineer", False),
        ("analyst", "Software Engineers, Data Engineers, Data Scientists", False),
        ("ai-solutions", "Forward Deployed Engineer", True),
        ("ai-solutions", "Backend Engineer (Rust, MySQL)", False),
        ("implementation-consultant", "Clinical Deployment Specialist", True),
        ("sales-engineer", "Associate Solutions Engineer", True),
        ("sales-engineer", "Technical Sales Engineer - Custom HVAC Equipment", False),
        ("sales-engineer", "Associate Territory Sales Engineer", False),
        ("tam", "Technical Account Manager", True),
        ("tam", "Customer Success Specialist", True),
    ],
)
def test_role_family_compatibility(profile: str, title: str, expected: bool) -> None:
    breakdown = _Breakdown("job", 0.8)
    breakdown.title = title
    assert _role_compatible(breakdown, profile) is expected


def test_run_plan_enqueues_materials_and_prepare(tmp_path: Path):
    enqueued = []

    def enqueue(name, payload):
        enqueued.append((name, payload))
        return f"task-{len(enqueued)}"

    report = _async(
        run_plan(
            tenant_id="default",
            profile_id="default",
            top_n=1,
            search_fn=_search_with_jobs,
            score_fn=_score,
            enqueue_fn=enqueue,
            pause_root=tmp_path,
            now=_clock(),
        )
    )

    assert report.status in {"ok", "error"}
    assert (
        "materials.generate",
        {
            "job_id": "job-1",
            "profile_id": "default",
            "document_types": ["resume", "cover_letter"],
        },
    ) in enqueued
    assert ("application.prepare", {"application_id": "job-1"}) in enqueued


def test_run_plan_forwards_material_overrides(tmp_path: Path):
    enqueued = []

    def enqueue(name, payload):
        enqueued.append((name, payload))
        return f"task-{len(enqueued)}"

    _async(
        run_plan(
            tenant_id="default",
            profile_id="default",
            top_n=1,
            search_fn=_search_with_jobs,
            score_fn=_score,
            enqueue_fn=enqueue,
            pause_root=tmp_path,
            now=_clock(),
            resume_strategy="patch_existing",
            resume_template_id="resume-template",
            resume_source_document_id="resume-doc",
            cover_letter_strategy="regenerate",
            cover_letter_template_id="cover-template",
            cover_letter_source_document_id="cover-doc",
        )
    )

    materials_payload = next(payload for name, payload in enqueued if name == "materials.generate")
    assert materials_payload["resume_strategy"] == "patch_existing"
    assert materials_payload["resume_template_id"] == "resume-template"
    assert materials_payload["resume_source_document_id"] == "resume-doc"
    assert materials_payload["cover_letter_strategy"] == "regenerate"
    assert materials_payload["cover_letter_template_id"] == "cover-template"
    assert materials_payload["cover_letter_source_document_id"] == "cover-doc"


def test_plan_run_task_wrapper_dispatches():
    from src.tasks.tasks import orchestration_plan_run

    with patch("src.orchestration.plan_run.run_plan") as mocked:
        mocked.return_value = PlanRunReport(
            run_id="r1",
            tenant_id="default",
            profile_id="primary",
            search_profile_id=None,
            status="ok",
            started_at=datetime.now(UTC).isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            duration_seconds=0.1,
            top_n=3,
            dry_run=False,
        )
        out = orchestration_plan_run.run(
            profile_id="primary",
            top_n=3,
            resume_strategy="patch_existing",
            resume_source_document_id="resume-doc",
            cover_letter_template_id="cover-template",
        )

    assert out["profile_id"] == "primary"
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["resume_strategy"] == "patch_existing"
    assert mocked.call_args.kwargs["resume_source_document_id"] == "resume-doc"
    assert mocked.call_args.kwargs["cover_letter_template_id"] == "cover-template"


def test_plan_run_task_registered_but_not_statically_scheduled():
    """2026-07-16: the static payload-less Beat entry was removed (it always
    failed on the missing legacy `default` profile). The task itself stays
    registered for automation plans and manual dispatch."""
    from src.tasks.beat import get_schedule
    from src.tasks.tasks import KNOWN_TASK_NAMES

    schedule = get_schedule()
    assert "plan_run" not in schedule
    assert "orchestration.plan_run" in KNOWN_TASK_NAMES
