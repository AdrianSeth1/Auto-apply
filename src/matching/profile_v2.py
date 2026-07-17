"""Canonical candidate/target loading and compilation for Job Pool V2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.core.config import PROJECT_ROOT
from src.matching.target_schema import (
    CandidateProfileV2,
    ResolvedTargetProfile,
    TargetSpecV2,
    load_unique_yaml,
    normalize_phrase,
)

CANDIDATE_PATH = PROJECT_ROOT / "data" / "profile" / "candidate.yaml"
TARGETS_DIR = PROJECT_ROOT / "config" / "targets"
ROLE_TAXONOMY_PATH = PROJECT_ROOT / "config" / "taxonomies" / "roles.v1.yaml"
CAPABILITY_TAXONOMY_PATH = (
    PROJECT_ROOT / "config" / "taxonomies" / "capabilities.v1.yaml"
)


def canonical_hash(value: Any) -> str:
    """Hash canonical JSON so formatting-only YAML changes do not bust caches."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=False)
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return load_unique_yaml(path.read_text(encoding="utf-8"))


def load_candidate(path: Path | None = None) -> CandidateProfileV2:
    return CandidateProfileV2.model_validate(_read_yaml(path or CANDIDATE_PATH))


def load_target(target_id: str, targets_dir: Path | None = None) -> TargetSpecV2:
    directory = targets_dir or TARGETS_DIR
    candidates = [target_id, target_id.replace("_", "-")]
    for candidate in candidates:
        path = directory / f"{candidate}.yaml"
        if path.exists():
            target = TargetSpecV2.model_validate(_read_yaml(path))
            if target_id != target.id and target_id not in target.aliases:
                raise ValueError(
                    f"Target file {path} declares {target.id!r}, not {target_id!r}"
                )
            return target

    for path in sorted(directory.glob("*.yaml")):
        target = TargetSpecV2.model_validate(_read_yaml(path))
        if target_id == target.id or target_id in target.aliases:
            return target
    raise FileNotFoundError(f"Unknown V2 target {target_id!r}")


def load_targets(
    targets_dir: Path | None = None, *, enabled_only: bool = True
) -> list[TargetSpecV2]:
    directory = targets_dir or TARGETS_DIR
    targets = [
        TargetSpecV2.model_validate(_read_yaml(path))
        for path in sorted(directory.glob("*.yaml"))
    ]
    ids = [target.id for target in targets]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate V2 target IDs")
    aliases = [alias for target in targets for alias in target.aliases]
    if set(ids) & set(aliases) or len(aliases) != len(set(aliases)):
        raise ValueError("Target IDs and aliases must be globally unique")
    return [target for target in targets if target.enabled or not enabled_only]


def _taxonomy(path: Path) -> tuple[dict[str, Any], str]:
    value = _read_yaml(path)
    return value, canonical_hash(value)


def resolve_target(
    candidate: CandidateProfileV2,
    target: TargetSpecV2,
    *,
    role_taxonomy_path: Path | None = None,
    capability_taxonomy_path: Path | None = None,
) -> ResolvedTargetProfile:
    _, role_version = _taxonomy(role_taxonomy_path or ROLE_TAXONOMY_PATH)
    _, capability_version = _taxonomy(
        capability_taxonomy_path or CAPABILITY_TAXONOMY_PATH
    )

    evidence = {
        capability.id: tuple(capability.evidence_refs)
        for capability in candidate.capabilities
    }
    requested = {
        capability
        for group in target.candidacy.required_capability_groups
        for capability in group.any_of
    } | {item.id for item in target.candidacy.preferred_capabilities}
    missing = requested - set(evidence)
    if missing:
        raise ValueError(
            f"Target {target.id!r} references candidate capabilities not present in "
            f"the canonical evidence bank: {sorted(missing)}"
        )

    title_rules = {
        "core": tuple(normalize_phrase(item) for item in target.role.core_titles),
        "adjacent": tuple(normalize_phrase(item) for item in target.role.adjacent_titles),
        "stretch": tuple(normalize_phrase(item) for item in target.role.stretch_titles),
        "description_only": tuple(
            normalize_phrase(item) for item in target.role.description_only_titles
        ),
        "excluded": tuple(
            normalize_phrase(item) for item in target.role.excluded_title_terms
        ),
    }
    discovery_terms = target.discovery.query_terms or [
        *target.role.core_titles,
        *target.role.adjacent_titles,
    ]
    return ResolvedTargetProfile(
        candidate=candidate,
        target=target,
        candidate_version=canonical_hash(candidate),
        target_version=canonical_hash(target),
        role_taxonomy_version=role_version,
        capability_taxonomy_version=capability_version,
        normalized_title_rules=title_rules,
        target_capability_evidence={key: evidence[key] for key in sorted(requested)},
        resolved_constraints={
            **target.constraints.model_dump(mode="json"),
            "candidate_preferences": candidate.preferences.model_dump(mode="json"),
            "work_authorization": candidate.identity.work_authorization.model_dump(mode="json"),
        },
        discovery_terms=tuple(dict.fromkeys(normalize_phrase(item) for item in discovery_terms)),
        material_priorities=tuple(target.materials.section_priority),
    )


def load_resolved_target(target_id: str) -> ResolvedTargetProfile:
    return resolve_target(load_candidate(), load_target(target_id))


def to_legacy_profile(resolved: ResolvedTargetProfile) -> dict[str, Any]:
    """Read-only adapter for existing evidence-grounded generation code."""

    candidate = resolved.candidate
    auth = candidate.identity.work_authorization
    return {
        "identity": {
            **candidate.identity.model_dump(mode="json", exclude={"work_authorization"}),
            "work_authorization": auth.status,
            "visa_sponsorship_needed": auth.sponsorship_needed,
        },
        "preferences": candidate.preferences.model_dump(mode="json"),
        "education": [
            item.model_dump(mode="json", exclude={"id"}) for item in candidate.education
        ],
        "work_experiences": [
            {
                **item.model_dump(mode="json", exclude={"id", "bullets"}),
                "bullets": [
                    bullet.model_dump(
                        mode="json",
                        exclude={"id", "capabilities", "evidence_strength", "verification"},
                    )
                    for bullet in item.bullets
                ],
            }
            for item in candidate.experiences
        ],
        "projects": [
            {
                **item.model_dump(mode="json", exclude={"id", "bullets"}),
                "bullets": [
                    bullet.model_dump(
                        mode="json",
                        exclude={"id", "capabilities", "evidence_strength", "verification"},
                    )
                    for bullet in item.bullets
                ],
            }
            for item in candidate.projects
        ],
        "skills": candidate.skills,
        "story_bank": [
            item.model_dump(mode="json", exclude={"id", "evidence_refs"})
            for item in candidate.stories
        ],
        "qa_bank": [
            item.model_dump(mode="json", exclude={"id"}) for item in candidate.qa_bank
        ],
        "target": resolved.target.model_dump(mode="json"),
        "candidate_version": resolved.candidate_version,
        "target_version": resolved.target_version,
    }


__all__ = [
    "CANDIDATE_PATH",
    "TARGETS_DIR",
    "canonical_hash",
    "load_candidate",
    "load_resolved_target",
    "load_target",
    "load_targets",
    "resolve_target",
    "to_legacy_profile",
]
