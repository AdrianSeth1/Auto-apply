"""Phase 17.3 + 17.4 -- /api/review route wire-up tests.

Use-case behaviour is covered by ``test_phase_17_2_review_queue``;
this file is about the FastAPI wiring: shapes, status codes, tenant
isolation, and the single-item + bulk surfaces.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session, sessionmaker

from src.application.review import CreateEntryArgs, create_entry
from src.core.config import get_db_url, load_config
from src.core.models import ReviewQueueEntry

_TENANT_PREFIX = "test-rqr-"
# The route helper falls back to ``"default"`` when the tenant
# ContextVar is unset; in CI we don't run with auth, so every row
# we create has to use tenant_id="default" for the routes to see it.
ROUTE_TENANT = "default"


@pytest.fixture(scope="module")
def engine():
    return create_engine(get_db_url(load_config()))


@pytest.fixture
def db_session(engine) -> Session:
    s = sessionmaker(bind=engine)()
    yield s
    s.execute(
        sa_delete(ReviewQueueEntry).where(
            ReviewQueueEntry.tenant_id.in_([ROUTE_TENANT, f"{_TENANT_PREFIX}other"])
        )
    )
    s.commit()
    s.close()


@pytest.fixture
def client() -> TestClient:
    from src.web.app import create_app

    return TestClient(create_app())


def _seed(session: Session, *, tenant: str = ROUTE_TENANT, **overrides) -> ReviewQueueEntry:
    args = CreateEntryArgs(
        tenant_id=tenant,
        job_id=overrides.get("job_id", uuid.uuid4()),
        job_snapshot_id=overrides.get("snapshot_id", uuid.uuid4()),
        materials_path=overrides.get("materials_path"),
        score_breakdown=overrides.get("score_breakdown", {"final_score": 0.55}),
        company=overrides.get("company", "Acme"),
        title=overrides.get("title", "SWE Intern"),
        run_id=overrides.get("run_id"),
    )
    entry = create_entry(session, args)
    session.commit()
    return entry


# --------------------------------------------------------------------------- #
# Read routes                                                                 #
# --------------------------------------------------------------------------- #


class TestReadRoutes:
    def test_list_returns_entries(self, db_session: Session, client: TestClient):
        _seed(db_session)
        _seed(db_session)
        response = client.get("/api/review")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert isinstance(body["entries"], list)
        assert len(body["entries"]) >= 2
        for entry in body["entries"]:
            assert "id" in entry
            assert "status" in entry

    def test_list_filters_by_status(self, db_session: Session, client: TestClient):
        _seed(db_session)  # pending
        # Mark one approved at the DB level so route filtering picks it up.
        approved = _seed(db_session)
        approved.status = "approved"
        db_session.commit()

        response = client.get("/api/review?status=approved")
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert all(e["status"] == "approved" for e in entries)

    def test_get_entry_404_for_missing(self, client: TestClient):
        response = client.get(f"/api/review/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_get_entry_404_for_other_tenant(
        self, db_session: Session, client: TestClient
    ):
        entry = _seed(db_session, tenant=f"{_TENANT_PREFIX}other")
        response = client.get(f"/api/review/{entry.id}")
        # Cross-tenant access returns 404, not 403, so we don't leak
        # whether the id exists.
        assert response.status_code == 404

    def test_get_entry_happy_path(self, db_session: Session, client: TestClient):
        entry = _seed(db_session)
        response = client.get(f"/api/review/{entry.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["entry"]["id"] == str(entry.id)


# --------------------------------------------------------------------------- #
# Single-item transitions                                                     #
# --------------------------------------------------------------------------- #


class TestSingleItemTransitions:
    def test_approve_route(self, db_session: Session, client: TestClient):
        entry = _seed(db_session)
        response = client.post(
            f"/api/review/{entry.id}/approve",
            json={"reviewer": "alice", "reason": "looks good"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["entry"]["status"] == "approved"
        assert body["entry"]["reviewer"] == "alice"

    def test_reject_route(self, db_session: Session, client: TestClient):
        entry = _seed(db_session)
        response = client.post(
            f"/api/review/{entry.id}/reject",
            json={"reviewer": "bob", "reason": "wrong stack"},
        )
        assert response.status_code == 200
        assert response.json()["entry"]["status"] == "rejected"

    def test_invalid_transition_returns_409(
        self, db_session: Session, client: TestClient
    ):
        """Approving a rejected entry is a forbidden state-machine
        transition; the route maps that to 409 with the source/dst in
        the message."""
        entry = _seed(db_session)
        entry.status = "rejected"
        db_session.commit()
        response = client.post(
            f"/api/review/{entry.id}/approve", json={"reviewer": "x"}
        )
        assert response.status_code == 409
        assert "rejected" in response.json().get("detail", "")

    def test_refresh_route_stale_to_pending(
        self, db_session: Session, client: TestClient, monkeypatch
    ):
        # Codex round-3 P2: refresh must also enqueue the upstream
        # tasks that actually re-scrape + regenerate. Patch celery to
        # avoid hitting Redis in unit tests.
        class _StubResult:
            def __init__(self, name):
                self.id = f"stub-{name}"

        captured = []

        class _StubCelery:
            @staticmethod
            def send_task(name, **kwargs):
                captured.append((name, kwargs))
                return _StubResult(name)

        import src.tasks.app as celery_mod

        monkeypatch.setattr(celery_mod, "celery_app", _StubCelery())

        entry = _seed(db_session)
        entry.status = "stale"
        db_session.commit()
        response = client.post(
            f"/api/review/{entry.id}/refresh", json={"reviewer": "alice"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["entry"]["status"] == "pending"
        # The route enqueued both the re-scrape and the regeneration.
        assert body["enrich_task_id"] == "stub-jobs.enrich"
        assert body["materials_task_id"] == "stub-materials.generate"
        names = {c[0] for c in captured}
        assert names == {"jobs.enrich", "materials.generate"}

    def test_missing_entry_returns_404(self, client: TestClient):
        response = client.post(
            f"/api/review/{uuid.uuid4()}/approve", json={"reviewer": "x"}
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Bulk routes (17.4)                                                          #
# --------------------------------------------------------------------------- #


class TestBulkRoutes:
    def test_bulk_approve_envelope(self, db_session: Session, client: TestClient):
        a = _seed(db_session)
        b = _seed(db_session)
        response = client.post(
            "/api/review/bulk/approve",
            json={"entry_ids": [str(a.id), str(b.id)], "reviewer": "alice"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert set(body["succeeded"]) == {str(a.id), str(b.id)}
        assert body["failed"] == []

    def test_bulk_reject_aggregates_failures(
        self, db_session: Session, client: TestClient
    ):
        a = _seed(db_session)
        b = _seed(db_session)
        # b is already approved AND submitted -- now terminal; reject
        # should fail for it but succeed for a.
        b.status = "submitted"
        db_session.commit()
        response = client.post(
            "/api/review/bulk/reject",
            json={"entry_ids": [str(a.id), str(b.id)]},
        )
        assert response.status_code == 200
        body = response.json()
        assert str(a.id) in body["succeeded"]
        assert any(f["id"] == str(b.id) for f in body["failed"])

    def test_bulk_approve_requires_entry_ids(self, client: TestClient):
        response = client.post("/api/review/bulk/approve", json={"entry_ids": []})
        assert response.status_code == 400

    def test_bulk_skips_other_tenants(
        self, db_session: Session, client: TestClient
    ):
        """An attacker passing a known cross-tenant id should not be
        able to flip it -- the route filters out non-owned ids
        silently (they don't show up in succeeded OR failed)."""
        mine = _seed(db_session)
        theirs = _seed(db_session, tenant=f"{_TENANT_PREFIX}other")
        response = client.post(
            "/api/review/bulk/approve",
            json={"entry_ids": [str(mine.id), str(theirs.id)]},
        )
        body = response.json()
        assert str(mine.id) in body["succeeded"]
        # theirs filtered out -- not in succeeded OR failed.
        all_ids = set(body["succeeded"]) | {f["id"] for f in body["failed"]}
        assert str(theirs.id) not in all_ids

    def test_bulk_reject_by_filter_company(
        self, db_session: Session, client: TestClient
    ):
        a = _seed(db_session, company="BlocklistedCo")
        b = _seed(db_session, company="OtherCo")
        response = client.post(
            "/api/review/bulk/reject-by-filter",
            json={"company": "blocklisted", "reason": "hard-no"},
        )
        assert response.status_code == 200
        body = response.json()
        assert str(a.id) in body["succeeded"]
        assert str(b.id) not in body["succeeded"]

    def test_bulk_reject_by_filter_requires_predicate(self, client: TestClient):
        response = client.post(
            "/api/review/bulk/reject-by-filter", json={}
        )
        assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Copy pack (2026-07-11) -- mocked session, no live Postgres needed           #
# (test_web.py style: patch every DB-touching call at its import path in     #
# src.web.routes.review, rather than exercising the real ORM).               #
# --------------------------------------------------------------------------- #


class _FakeSessionContext:
    """Stands in for ``factory()`` -- the route only ever passes the
    yielded session through to functions we mock out separately, so it
    never needs to behave like a real SQLAlchemy Session."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc_info):
        return False


def _fake_entry(**overrides):
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": ROUTE_TENANT,
        "job_id": uuid.uuid4(),
        "job_snapshot_id": uuid.uuid4(),
        "company": "Acme",
        "title": "SWE Intern",
        "materials_path": "/data/output/resume_Acme_SWE-Intern_2026-07-11.pdf",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCopyPackRoute:
    def _patch_session_factory(self):
        return patch(
            "src.web.routes.review.get_session_factory",
            return_value=lambda: _FakeSessionContext(MagicMock()),
        )

    def test_copy_pack_happy_path(self, client: TestClient):
        entry = _fake_entry()
        with (
            self._patch_session_factory(),
            patch("src.web.routes.review.get_entry_db", return_value=entry),
            patch(
                "src.web.routes.review._entry_job_text",
                return_value=("SWE Intern", "Build things with Python."),
            ),
            patch(
                "src.web.routes.review._active_profile_identity",
                return_value={
                    "full_name": "Arya Seth",
                    "email": "arya@example.com",
                    "phone": "555-1234",
                    "location": "Portland, OR",
                    "linkedin_url": "https://linkedin.com/in/aryaseth",
                },
            ),
            patch(
                "src.web.routes.review._entry_artifacts",
                return_value=[{"label": "Resume PDF", "path": "/data/output/resume.pdf"}],
            ),
            patch(
                "src.web.routes.review._entry_application_url",
                return_value="https://acme.example/careers/123",
            ),
            patch(
                "src.web.routes.review._qa_bank_matches_for_job",
                return_value=[
                    {
                        "id": "qa-1",
                        "question": "Why do you want to work here?",
                        "answer": "Because Python.",
                        "question_type": "motivation",
                        "confidence": 0.9,
                        "needs_review": False,
                    }
                ],
            ),
        ):
            response = client.get(f"/api/review/{entry.id}/copy-pack")

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["entry_id"] == str(entry.id)
        assert body["company"] == "Acme"
        assert body["title"] == "SWE Intern"
        assert body["identity"]["full_name"] == "Arya Seth"
        assert body["identity"]["email"] == "arya@example.com"
        assert body["artifacts"] == [{"label": "Resume PDF", "path": "/data/output/resume.pdf"}]
        assert body["application_url"] == "https://acme.example/careers/123"
        assert len(body["qa_matches"]) == 1
        assert body["qa_matches"][0]["question"] == "Why do you want to work here?"

    def test_copy_pack_404_for_missing_entry(self, client: TestClient):
        with (
            self._patch_session_factory(),
            patch("src.web.routes.review.get_entry_db", return_value=None),
        ):
            response = client.get(f"/api/review/{uuid.uuid4()}/copy-pack")
        assert response.status_code == 404

    def test_copy_pack_404_for_other_tenant(self, client: TestClient):
        entry = _fake_entry(tenant_id="someone-elses-tenant")
        with (
            self._patch_session_factory(),
            patch("src.web.routes.review.get_entry_db", return_value=entry),
        ):
            response = client.get(f"/api/review/{entry.id}/copy-pack")
        # Cross-tenant access returns 404, not 403, matching every other
        # single-item route in this file.
        assert response.status_code == 404

    def test_copy_pack_missing_profile_returns_empty_identity(self, client: TestClient):
        """No active profile configured -- the pack still returns the
        other fields instead of erroring out."""
        entry = _fake_entry()
        with (
            self._patch_session_factory(),
            patch("src.web.routes.review.get_entry_db", return_value=entry),
            patch("src.web.routes.review._entry_job_text", return_value=("SWE Intern", "")),
            patch("src.web.routes.review._active_profile_identity", return_value={}),
            patch("src.web.routes.review._entry_artifacts", return_value=[]),
            patch("src.web.routes.review._entry_application_url", return_value=None),
            patch("src.web.routes.review._qa_bank_matches_for_job", return_value=[]),
        ):
            response = client.get(f"/api/review/{entry.id}/copy-pack")
        assert response.status_code == 200
        body = response.json()
        assert body["identity"] == {}
        assert body["artifacts"] == []
        assert body["qa_matches"] == []


class TestQaBankMatchesForJob:
    """Unit tests for the token-overlap matcher itself (no HTTP layer)."""

    def _saved(self, entries):
        return patch(
            "src.application.question_answers.list_saved_answers",
            return_value={"entries": entries},
        )

    def test_matches_on_token_overlap_with_job_text(self):
        from src.web.routes.review import _qa_bank_matches_for_job

        saved = [
            {
                "id": "1",
                "question": "Describe your experience with payments infrastructure engineering.",
            },
            {"id": "2", "question": "Describe your favorite vacation."},
        ]
        with self._saved(saved):
            matches = _qa_bank_matches_for_job(
                "Payments Infrastructure Engineer",
                "We build payments infrastructure and engineering tools.",
            )
        assert [m["id"] for m in matches] == ["1"]

    def test_requires_minimum_overlap(self):
        from src.web.routes.review import _qa_bank_matches_for_job

        saved = [{"id": "1", "question": "One word overlap only: payments."}]
        with self._saved(saved):
            matches = _qa_bank_matches_for_job("Engineer", "We build software.")
        assert matches == []

    def test_returns_at_most_five_sorted_by_overlap(self):
        from src.web.routes.review import _qa_bank_matches_for_job

        saved = [
            {"id": str(i), "question": "python backend engineer distributed systems api"}
            for i in range(8)
        ]
        with self._saved(saved):
            matches = _qa_bank_matches_for_job(
                "Backend Engineer", "python backend engineer distributed systems api"
            )
        assert len(matches) == 5

    def test_no_saved_answers_returns_empty(self):
        from src.web.routes.review import _qa_bank_matches_for_job

        with self._saved([]):
            assert _qa_bank_matches_for_job("Engineer", "Some description.") == []


class TestActiveProfileIdentity:
    def test_no_active_profile_returns_empty_dict(self):
        from src.web.routes.review import _active_profile_identity

        with patch("src.application.profile.get_active_profile_path", return_value=None):
            assert _active_profile_identity() == {}

    def test_extracts_expected_identity_fields(self, tmp_path):
        from src.web.routes.review import _active_profile_identity

        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text(
            "identity:\n"
            "  full_name: Arya Seth\n"
            "  email: arya@example.com\n"
            "  phone: '555-1234'\n"
            "  location: Portland, OR\n"
            "  linkedin_url: https://linkedin.com/in/aryaseth\n"
            "  github_url: https://github.com/arya\n",
            encoding="utf-8",
        )
        with patch(
            "src.application.profile.get_active_profile_path", return_value=profile_path
        ):
            identity = _active_profile_identity()
        assert identity == {
            "full_name": "Arya Seth",
            "email": "arya@example.com",
            "phone": "555-1234",
            "location": "Portland, OR",
            "linkedin_url": "https://linkedin.com/in/aryaseth",
        }
