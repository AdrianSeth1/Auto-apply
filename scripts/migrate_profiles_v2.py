"""Create the canonical V2 candidate from the legacy evidence-rich profile.

Dry-run is the default. ``--write`` creates ``data/profile/candidate.yaml``;
``--force`` is required to replace an existing canonical file.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from src.core.config import PROJECT_ROOT
from src.matching.target_schema import CandidateProfileV2

DEFAULT_SOURCE = PROJECT_ROOT / "data" / "profile" / "profiles" / "analyst.yaml"
DEFAULT_DESTINATION = PROJECT_ROOT / "data" / "profile" / "candidate.yaml"


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return text or "item"


def _unique(base: str, seen: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in seen:
        candidate = f"{base}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _bullet(
    raw: dict[str, Any], *, bullet_id: str, project: bool = False
) -> dict[str, Any]:
    tags = [str(tag).strip() for tag in raw.get("tags", []) if str(tag).strip()]
    impact = str(raw.get("impact") or "")
    quantified = bool(re.search(r"(?:\d|%|~)", impact))
    adoption = any(tag in {"adoption", "real-world", "deployment"} for tag in tags)
    if project and adoption:
        strength = "adopted_external_project"
    elif project:
        strength = "production_like_project"
    elif quantified:
        strength = "quantified_professional"
    else:
        strength = "direct_professional"
    return {
        "id": bullet_id,
        "text": str(raw.get("text") or "").strip(),
        "capabilities": [f"cap_{_slug(tag)}" for tag in tags],
        "tags": tags,
        "impact": impact,
        "evidence_strength": strength,
        "verification": "self_reported",
    }


def migrate(source: Path) -> dict[str, Any]:
    # The legacy file is intentionally loaded permissively: it contains the
    # known duplicate ``identity.location`` key this migration retires. V2
    # loaders reject the same defect.
    legacy = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    identity = dict(legacy.get("identity") or {})
    education_rows = list(legacy.get("education") or [])
    graduation = str(education_rows[0].get("end_date") or "") if education_rows else None

    seen_ids: set[str] = set()
    evidence_by_capability: dict[str, list[str]] = defaultdict(list)
    experiences: list[dict[str, Any]] = []
    for exp in legacy.get("work_experiences") or []:
        exp_id = _unique(f"exp_{_slug(exp.get('company'))}", seen_ids)
        bullets = []
        for index, raw_bullet in enumerate(exp.get("bullets") or [], start=1):
            bullet_id = _unique(f"expb_{_slug(exp.get('company'))}_{index}", seen_ids)
            value = _bullet(raw_bullet, bullet_id=bullet_id)
            bullets.append(value)
            for capability in value["capabilities"]:
                evidence_by_capability[capability].append(bullet_id)
        experiences.append(
            {
                "id": exp_id,
                "company": exp.get("company", ""),
                "title": exp.get("title", ""),
                "location": exp.get("location", ""),
                "start_date": str(exp.get("start_date") or ""),
                "end_date": str(exp.get("end_date") or ""),
                "description": exp.get("description", ""),
                "bullets": bullets,
            }
        )

    projects: list[dict[str, Any]] = []
    for project in legacy.get("projects") or []:
        project_id = _unique(f"proj_{_slug(project.get('name'))}", seen_ids)
        bullets = []
        for index, raw_bullet in enumerate(project.get("bullets") or [], start=1):
            bullet_id = _unique(f"projb_{_slug(project.get('name'))}_{index}", seen_ids)
            value = _bullet(raw_bullet, bullet_id=bullet_id, project=True)
            bullets.append(value)
            for capability in value["capabilities"]:
                evidence_by_capability[capability].append(bullet_id)
        projects.append(
            {
                "id": project_id,
                "name": project.get("name", ""),
                "role": project.get("role", ""),
                "description": project.get("description", ""),
                "tech_stack": project.get("tech_stack") or [],
                "start_date": str(project.get("start_date") or ""),
                "end_date": str(project.get("end_date") or ""),
                "url": project.get("url", ""),
                "bullets": bullets,
            }
        )

    education: list[dict[str, Any]] = []
    for row in education_rows:
        education.append(
            {
                "id": _unique(f"edu_{_slug(row.get('institution'))}", seen_ids),
                **row,
            }
        )

    stories = []
    for row in legacy.get("story_bank") or []:
        stories.append(
            {
                "id": _unique(f"story_{_slug(row.get('theme'))}", seen_ids),
                **row,
                "evidence_refs": [],
            }
        )

    qa_bank = []
    for row in legacy.get("qa_bank") or []:
        qa_bank.append(
            {
                "id": _unique(f"qa_{_slug(row.get('question_type'))}", seen_ids),
                "question": row.get("question_pattern", ""),
                "answer": row.get("canonical_answer", ""),
                "category": row.get("question_type", ""),
                "tags": [],
                "variants": row.get("variants") or {},
                "confidence": row.get("confidence", "high"),
                "needs_review": bool(row.get("needs_review", False)),
            }
        )

    capabilities = [
        {
            "id": capability,
            "label": capability.removeprefix("cap_").replace("_", " ").title(),
            "level": "demonstrated",
            "evidence_refs": list(dict.fromkeys(refs)),
        }
        for capability, refs in sorted(evidence_by_capability.items())
    ]

    canonical = {
        "schema_version": 2,
        "candidate_id": "arya",
        "identity": {
            "full_name": identity.get("full_name", ""),
            "email": identity.get("email", ""),
            "phone": identity.get("phone", ""),
            "location": "Portland, OR",
            "linkedin_url": identity.get("linkedin_url", ""),
            "github_url": identity.get("github_url", ""),
            "portfolio_url": identity.get("portfolio_url", ""),
            "citizenship": identity.get("citizenship", ""),
            "work_authorization": {
                "country": "US",
                "status": "permanent_resident",
                "sponsorship_needed": False,
            },
            "willing_to_relocate": bool(identity.get("willing_to_relocate", True)),
            "professional_experience_years": float(
                identity.get("professional_experience_years", 2)
            ),
            "graduation_date": graduation or None,
        },
        "preferences": {
            "preferred_locations": ["Portland, OR", "Dallas, TX", "US Remote"],
            "remote_us_allowed": True,
            "onsite_hybrid_locations": ["Portland, OR", "Dallas, TX"],
            "employment_types": ["fulltime", "contract"],
            "compensation": {
                "currency": "USD",
                "preferred_base_min": 90000,
                "hard_base_min": None,
            },
            "travel_ceiling_percent": None,
            "startup_interest": 0.8,
        },
        "education": education,
        "experiences": experiences,
        "projects": projects,
        "skills": legacy.get("skills") or {},
        "stories": stories,
        "qa_bank": qa_bank,
        "capabilities": capabilities,
    }
    return CandidateProfileV2.model_validate(canonical).model_dump(
        mode="json", exclude_none=False
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    candidate = migrate(args.source)
    rendered = yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True, width=100)
    if not args.write:
        print(
            f"Validated candidate migration: {len(candidate['experiences'])} experiences, "
            f"{len(candidate['projects'])} projects, "
            f"{len(candidate['capabilities'])} capabilities. Use --write to persist."
        )
        return 0
    if args.destination.exists() and not args.force:
        raise FileExistsError(
            f"{args.destination} already exists; pass --force to replace it"
        )
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(rendered, encoding="utf-8")
    print(f"Wrote canonical candidate to {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
