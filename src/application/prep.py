"""Interview prep pack use case.

Loads an application + its job from the DB, picks the profile whose resume
was (or would be) used, renders the markdown prep pack, and writes it under
``data/output/prep/``. Triggered from the Applications view button and
automatically (best-effort) when an application's outcome flips to
``interview``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from uuid import UUID

from src.core.config import PROJECT_ROOT, load_config

logger = logging.getLogger("autoapply.application.prep")

PREP_DIR = PROJECT_ROOT / "data" / "output" / "prep"


def generate_prep_pack(*, application_id: UUID, profile_id: str | None = None) -> dict:
    """Generate (or regenerate) the prep pack for one application."""
    from src.core.database import get_session_factory
    from src.core.models import Application, Job

    try:
        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            application = session.get(Application, application_id)
            if application is None or application.deleted_at is not None:
                return {
                    "ok": False,
                    "error": "Application not found.",
                    "error_code": "application_not_found",
                }
            job = session.get(Job, application.job_id)
            if job is None:
                return {
                    "ok": False,
                    "error": "Job for this application no longer exists.",
                    "error_code": "job_not_found",
                }
            job_payload = {
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "description": job.description,
                "requirements": job.requirements or {},
                "application_url": job.application_url,
                "match_score": application.match_score,
                "best_profile": (job.raw_data or {}).get("best_profile"),
            }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Prep pack DB load failed")
        return {"ok": False, "error": str(exc), "error_code": "db_load_failed"}

    profile = _load_profile_for_prep(profile_id or job_payload.get("best_profile"))
    if profile is None:
        return {
            "ok": False,
            "error": "No applicant profile available.",
            "error_code": "profile_missing",
        }

    from src.generation.prep_pack import build_prep_pack

    markdown = build_prep_pack(job=job_payload, profile=profile)

    PREP_DIR.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(f"prep_{job_payload['company']}_{job_payload['title']}") + ".md"
    path = PREP_DIR / filename
    path.write_text(markdown, encoding="utf-8")

    return {
        "ok": True,
        "path": str(path),
        "filename": filename,
        "markdown": markdown,
        "error": None,
        "error_code": None,
    }


def maybe_generate_prep_pack_on_interview(*, application_id: UUID, outcome: str) -> None:
    """Best-effort auto-generation when an outcome flips to ``interview``.

    Never raises: prep packs are a convenience and must not fail the
    outcome update that triggered them.
    """
    if outcome != "interview":
        return
    try:
        result = generate_prep_pack(application_id=application_id)
        if result.get("ok"):
            logger.info("Auto-generated interview prep pack: %s", result["path"])
        else:
            logger.warning("Prep pack auto-generation skipped: %s", result.get("error"))
    except Exception:  # noqa: BLE001
        logger.exception("Prep pack auto-generation failed")


def _load_profile_for_prep(profile_id: str | None) -> dict | None:
    """Load the requested profile, falling back to the active one."""
    from src.application.profile import get_active_profile_path, get_profile_path
    from src.memory.profile import load_profile_yaml

    candidates: list[Path] = []
    if profile_id:
        candidates.append(get_profile_path(profile_id))
    active = get_active_profile_path()
    if active is not None:
        candidates.append(active)
    for path in candidates:
        if path and path.exists():
            try:
                return load_profile_yaml(path)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to load profile %s for prep pack", path)
    return None


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return cleaned[:120] or "prep"
