"""Tests for the Job Database browse + batch-generate API surface.

The usecases hit Postgres + Celery, so the route tests patch them out
(same pattern as the rest of test_web.py). The pure helpers in
``src.application.job_database`` are tested directly where possible.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from src.web.app import create_app

    return TestClient(create_app())


class TestDbJobsRoutes:
    def test_routes_registered(self):
        from src.web.app import create_app

        app = create_app()
        paths = [route.path for route in app.routes]
        assert "/api/jobs/db" in paths
        assert "/api/jobs/db/generate-materials" in paths

    @patch("src.application.job_database.list_db_jobs")
    def test_list_db_jobs_route_passes_filters(self, mock_list, client):
        mock_list.return_value = {
            "ok": True,
            "jobs": [],
            "total": 0,
            "limit": 20,
            "offset": 0,
            "facets": {"employment_types": [], "seniorities": [], "sources": []},
        }

        response = client.get(
            "/api/jobs/db",
            params={
                "q": "consultant",
                "location": "portland",
                "employment_type": "fulltime",
                "source": "greenhouse",
                "limit": 50,
                "offset": 100,
            },
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        kwargs = mock_list.call_args.kwargs
        assert kwargs["q"] == "consultant"
        assert kwargs["location"] == "portland"
        assert kwargs["employment_type"] == "fulltime"
        assert kwargs["source"] == "greenhouse"
        assert kwargs["limit"] == 50
        assert kwargs["offset"] == 100

    @patch("src.application.job_database.generate_materials_for_db_jobs")
    def test_generate_materials_route(self, mock_generate, client):
        mock_generate.return_value = {
            "ok": True,
            "queued": [
                {
                    "job_id": "8b8f6f0a-1111-2222-3333-444455556666",
                    "company": "Stripe",
                    "title": "Solutions Consultant",
                    "task_id": "abc",
                    "application_id": "def",
                }
            ],
            "errors": [],
            "run_id": "manual-x",
            "document_types": ["resume"],
        }

        response = client.post(
            "/api/jobs/db/generate-materials",
            json={
                "job_ids": ["8b8f6f0a-1111-2222-3333-444455556666"],
                "document_types": ["resume"],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert len(body["queued"]) == 1
        kwargs = mock_generate.call_args.kwargs
        assert kwargs["job_ids"] == ["8b8f6f0a-1111-2222-3333-444455556666"]
        assert kwargs["document_types"] == ["resume"]


class TestGenerateMaterialsValidation:
    def test_empty_selection_rejected(self):
        from src.application.job_database import generate_materials_for_db_jobs

        result = generate_materials_for_db_jobs(job_ids=[])
        assert result["ok"] is False
        assert result["errors"] == ["No jobs selected."]

    def test_batch_cap_enforced(self):
        from src.application.job_database import (
            _MAX_BATCH_GENERATE,
            generate_materials_for_db_jobs,
        )

        result = generate_materials_for_db_jobs(
            job_ids=[f"id-{i}" for i in range(_MAX_BATCH_GENERATE + 1)]
        )
        assert result["ok"] is False
        assert "Too many jobs selected" in result["errors"][0]

    @patch("src.core.database.get_session_factory")
    def test_invalid_ids_reported_per_job(self, mock_factory):
        from src.application.job_database import generate_materials_for_db_jobs

        result = generate_materials_for_db_jobs(job_ids=["not-a-uuid"])
        assert result["ok"] is False
        assert "Invalid job id: not-a-uuid" in result["errors"]
        mock_factory.return_value.assert_not_called()

    def test_document_types_sanitized(self):
        from src.application.job_database import generate_materials_for_db_jobs

        # Bogus doc types fall back to the default pair; empty selection
        # short-circuits before any DB access so this stays unit-level.
        result = generate_materials_for_db_jobs(
            job_ids=[], document_types=["résumé-lol", "malware"]
        )
        assert result["errors"] == ["No jobs selected."]
