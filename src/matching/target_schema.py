"""Strict schemas for the canonical candidate and Job Pool V2 targets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DuplicateKeyError(ValueError):
    """Raised when YAML contains a mapping key more than once."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            mark = key_node.start_mark
            raise DuplicateKeyError(
                f"Duplicate YAML key {key!r} at line {mark.line + 1}, "
                f"column {mark.column + 1}"
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_unique_yaml(text: str) -> dict[str, Any]:
    """Load one YAML mapping and reject duplicate keys at every depth."""

    value = yaml.load(text, Loader=_UniqueKeyLoader)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Expected a YAML mapping at the document root")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkAuthorizationV2(_StrictModel):
    country: str = "US"
    status: Literal[
        "citizen", "permanent_resident", "ead", "temporary_visa", "unknown"
    ] = "unknown"
    sponsorship_needed: bool = True


class CandidateIdentityV2(_StrictModel):
    full_name: str
    email: str = ""
    phone: str = ""
    location: str
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    citizenship: str = ""
    work_authorization: WorkAuthorizationV2
    willing_to_relocate: bool = False
    professional_experience_years: float = Field(ge=0)
    graduation_date: str | None = None


class CompensationPreferenceV2(_StrictModel):
    currency: str = "USD"
    preferred_base_min: int | None = Field(default=None, ge=0)
    hard_base_min: int | None = Field(default=None, ge=0)


class CandidatePreferencesV2(_StrictModel):
    preferred_locations: list[str] = Field(default_factory=list)
    remote_us_allowed: bool = True
    # When true, any posting with explicit US geography may pass the
    # onsite/hybrid location gate. ``preferred_locations`` remains the
    # ranking layer; this flag expands eligibility, not preference.
    onsite_hybrid_us_allowed: bool = False
    onsite_hybrid_locations: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=lambda: ["fulltime", "contract"])
    compensation: CompensationPreferenceV2 = Field(
        default_factory=CompensationPreferenceV2
    )
    travel_ceiling_percent: int | None = Field(default=None, ge=0, le=100)
    startup_interest: float = Field(default=0.8, ge=0, le=1)
    # Phase S7 (2026-07-13): companies the candidate is specifically excited
    # about. Matched case/punctuation-insensitively against job.company in
    # scorer_v2._preference -- see that function for the exact effect.
    preferred_companies: list[str] = Field(default_factory=list)
    # Candidate-level negative responsibility signals, applied across every
    # target (unlike TargetRoleV2.negative_responsibility_signals, which is
    # per-target). Use for dislikes that aren't specific to one role family --
    # e.g. "door to door" for a candidate open to B2B outside sales but not
    # door-to-door/canvassing roles. Merged with each target's own negative
    # signals in scorer_v2._responsibility_coverage.
    excluded_role_signals: list[str] = Field(default_factory=list)
    # Willingness flags collected in Phase S7. Stored for target definitions
    # to consume; not yet read by any scoring code, since none of the
    # current targets model quota-bearing or non-implementation-coding roles
    # -- see the "Phase S7" notes in git history of the removed
    # docs/JOB_POOL_V2_IMPLEMENTATION_STATUS.md (consolidated into HANDOFF.md
    # 2026-07-16) for the
    # explicit disclosure that these are recorded-but-not-yet-wired.
    quota_bearing_ok: bool = True
    light_coding_implementation_ok: bool = True


class EducationV2(_StrictModel):
    id: str
    institution: str
    degree: str
    field: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    gpa: str = ""
    honors: list[str] = Field(default_factory=list)
    relevant_courses: list[dict[str, Any]] = Field(default_factory=list)


class EvidenceBulletV2(_StrictModel):
    id: str
    text: str
    capabilities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    impact: str = ""
    evidence_strength: Literal[
        "quantified_professional",
        "direct_professional",
        "adopted_external_project",
        "production_like_project",
        "adjacent_professional",
        "coursework",
        "plausible_narrative",
    ] = "direct_professional"
    verification: Literal["documented", "self_reported", "plausible", "needs_review"] = (
        "self_reported"
    )


class ExperienceV2(_StrictModel):
    id: str
    company: str
    title: str
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""
    bullets: list[EvidenceBulletV2] = Field(default_factory=list)


class ProjectV2(_StrictModel):
    id: str
    name: str
    role: str = ""
    description: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    url: str = ""
    bullets: list[EvidenceBulletV2] = Field(default_factory=list)


class StoryV2(_StrictModel):
    id: str
    theme: str
    context: str = ""
    action: str = ""
    result: str = ""
    applicable_to: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class QAEntryV2(_StrictModel):
    id: str
    question: str = ""
    answer: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    variants: dict[str, Any] = Field(default_factory=dict)
    confidence: str = "high"
    needs_review: bool = False


class CapabilityV2(_StrictModel):
    id: str
    label: str
    level: Literal["demonstrated", "working", "exposure"] = "demonstrated"
    evidence_refs: list[str] = Field(min_length=1)


class CandidateProfileV2(_StrictModel):
    schema_version: Literal[2] = 2
    candidate_id: str
    identity: CandidateIdentityV2
    preferences: CandidatePreferencesV2
    education: list[EducationV2] = Field(default_factory=list)
    experiences: list[ExperienceV2] = Field(default_factory=list)
    projects: list[ProjectV2] = Field(default_factory=list)
    skills: dict[str, list[Any]] = Field(default_factory=dict)
    stories: list[StoryV2] = Field(default_factory=list)
    qa_bank: list[QAEntryV2] = Field(default_factory=list)
    capabilities: list[CapabilityV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stable_ids_and_references(self) -> "CandidateProfileV2":
        seen: set[str] = set()

        def add(value: str, prefix: str) -> None:
            if not value.startswith(prefix):
                raise ValueError(f"Stable ID {value!r} must start with {prefix!r}")
            if value in seen:
                raise ValueError(f"Duplicate stable ID {value!r}")
            seen.add(value)

        for education in self.education:
            add(education.id, "edu_")
        for experience in self.experiences:
            add(experience.id, "exp_")
            for bullet in experience.bullets:
                add(bullet.id, "expb_")
        for project in self.projects:
            add(project.id, "proj_")
            for bullet in project.bullets:
                add(bullet.id, "projb_")
        for story in self.stories:
            add(story.id, "story_")
        for item in self.qa_bank:
            add(item.id, "qa_")
        for capability in self.capabilities:
            add(capability.id, "cap_")

        capability_ids = {item.id for item in self.capabilities}
        evidence_ids = {
            item.id for exp in self.experiences for item in exp.bullets
        } | {item.id for project in self.projects for item in project.bullets}
        evidence_ids |= {exp.id for exp in self.experiences}
        evidence_ids |= {project.id for project in self.projects}

        bullets = [item for exp in self.experiences for item in exp.bullets]
        bullets.extend(item for project in self.projects for item in project.bullets)
        for bullet in bullets:
            missing = set(bullet.capabilities) - capability_ids
            if missing:
                raise ValueError(
                    f"Bullet {bullet.id!r} references unknown capabilities {sorted(missing)}"
                )
        for capability in self.capabilities:
            missing = set(capability.evidence_refs) - evidence_ids
            if missing:
                raise ValueError(
                    f"Capability {capability.id!r} references unknown evidence {sorted(missing)}"
                )
        for story in self.stories:
            missing = set(story.evidence_refs) - evidence_ids
            if missing:
                raise ValueError(
                    f"Story {story.id!r} references unknown evidence {sorted(missing)}"
                )
        return self


class CapabilityGroupV2(_StrictModel):
    any_of: list[str] = Field(min_length=1)
    weight: float = Field(default=1.0, gt=0)


class WeightedCapabilityV2(_StrictModel):
    id: str
    weight: float = Field(default=1.0, gt=0)


class TargetRoleV2(_StrictModel):
    core_titles: list[str] = Field(default_factory=list)
    adjacent_titles: list[str] = Field(default_factory=list)
    stretch_titles: list[str] = Field(default_factory=list)
    description_only_titles: list[str] = Field(default_factory=list)
    excluded_title_terms: list[str] = Field(default_factory=list)
    responsibility_signals: list[str] = Field(default_factory=list)
    negative_responsibility_signals: list[str] = Field(default_factory=list)
    domain_signals: list[str] = Field(default_factory=list)


class TargetCandidacyV2(_StrictModel):
    core_max_required_years: int = Field(default=3, ge=0)
    stretch_max_required_years: int = Field(default=4, ge=0)
    allowed_seniority: list[str] = Field(default_factory=list)
    unsupported_specializations: list[str] = Field(default_factory=list)
    required_capability_groups: list[CapabilityGroupV2] = Field(default_factory=list)
    preferred_capabilities: list[WeightedCapabilityV2] = Field(default_factory=list)


class TargetConstraintsV2(_StrictModel):
    employment_types: list[str] = Field(default_factory=lambda: ["fulltime", "contract"])
    geography_policy: str = "candidate_default"
    compensation_policy: str = "candidate_default"
    active_clearance: Literal["reject", "allow", "unknown"] = "reject"
    staffing_intermediary: Literal["reject_auto", "allow", "manual_only"] = "reject_auto"


class TargetDiscoveryV2(_StrictModel):
    query_terms: list[str] = Field(default_factory=list)
    description_only_lane: bool = False
    employer_cohorts: list[str] = Field(default_factory=list)


class TargetSelectionV2(_StrictModel):
    minimum_core_tier: Literal["A", "B"] = "B"
    per_run_soft_cap: int = Field(default=5, ge=0)
    allow_stretch_in_core: bool = False


class TargetMaterialsV2(_StrictModel):
    preferred_evidence_refs: list[str] = Field(default_factory=list)
    section_priority: list[str] = Field(
        default_factory=lambda: ["experience", "projects", "skills", "education"]
    )


class TargetSpecV2(_StrictModel):
    schema_version: Literal[2] = 2
    id: str
    aliases: list[str] = Field(default_factory=list)
    display_name: str
    enabled: bool = True
    priority: float = Field(default=1.0, ge=0, le=1)
    positioning: str
    role: TargetRoleV2
    candidacy: TargetCandidacyV2
    constraints: TargetConstraintsV2 = Field(default_factory=TargetConstraintsV2)
    discovery: TargetDiscoveryV2 = Field(default_factory=TargetDiscoveryV2)
    selection: TargetSelectionV2 = Field(default_factory=TargetSelectionV2)
    materials: TargetMaterialsV2 = Field(default_factory=TargetMaterialsV2)

    @model_validator(mode="after")
    def validate_target(self) -> "TargetSpecV2":
        if not self.role.core_titles:
            raise ValueError("Target must define at least one core title")
        title_sets = (
            self.role.core_titles,
            self.role.adjacent_titles,
            self.role.stretch_titles,
            self.role.description_only_titles,
        )
        normalized: list[str] = []
        for values in title_sets:
            normalized.extend(normalize_phrase(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("A title phrase may appear in only one target title tier")
        return self


class ResolvedTargetProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: CandidateProfileV2
    target: TargetSpecV2
    candidate_version: str
    target_version: str
    role_taxonomy_version: str
    capability_taxonomy_version: str
    normalized_title_rules: dict[str, tuple[str, ...]]
    target_capability_evidence: dict[str, tuple[str, ...]]
    resolved_constraints: dict[str, Any]
    discovery_terms: tuple[str, ...]
    material_priorities: tuple[str, ...]


def normalize_phrase(value: str) -> str:
    """Normalize a phrase without enabling substring matches."""

    value = value.casefold().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9+#.]+", value))


def phrase_in_text(phrase: str, text: str) -> bool:
    """Whole-token phrase match shared by routing and scoring."""

    needle = normalize_phrase(phrase)
    haystack = f" {normalize_phrase(text)} "
    return bool(needle) and f" {needle} " in haystack


def ensure_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


__all__ = [
    "CandidateProfileV2",
    "DuplicateKeyError",
    "ResolvedTargetProfile",
    "TargetSpecV2",
    "load_unique_yaml",
    "normalize_phrase",
    "phrase_in_text",
]
