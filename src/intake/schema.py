"""Unified Job schema.

All ATS scrapers normalize their output to this schema before storage.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

EmploymentType = Literal["internship", "fulltime", "parttime", "contract", "coop", "unknown"]
SeniorityLevel = Literal["internship", "entry", "mid", "senior", "staff", "unknown"]
SourceType = Literal[
    "greenhouse",
    "lever",
    "ashby",
    "linkedin",
    "adzuna",
    "workday",
    "hn",
    "remotive",
    "company_site",
    "smartrecruiters",
    "workable",
    "recruitee",
    "manual_import",
    "unknown",
]
# Migration alias. Existing adapters and database columns keep the historical
# name while V2 uses ``SourceType`` in new contracts.
ATSType = SourceType

SourceChannel = Literal[
    "direct_ats", "aggregator", "community_board", "employer_site", "manual_import"
]
PublisherRelationship = Literal[
    "employer_verified",
    "employer_claimed",
    "third_party_aggregator",
    "recruiter_claimed",
    "unknown",
]
DescriptionCompleteness = Literal["full", "partial", "snippet", "missing"]
ApplicationTargetKind = Literal[
    "direct_ats",
    "employer_site",
    "aggregator_redirect",
    "recruiter_contact",
    "email",
    "missing",
    "unknown",
]


class ApplicationTargetV2(BaseModel):
    original_url: str | None = None
    resolved_url: str | None = None
    kind: ApplicationTargetKind = "unknown"
    resolution_status: str = "unresolved"
    verified_at: datetime | None = None


class JobProvenanceV2(BaseModel):
    adapter: SourceType
    channel: SourceChannel
    endpoint_id: str | None = None
    query_arm_ids: list[str] = Field(default_factory=list)
    source_record_url: str | None = None
    listing_url: str | None = None
    provider_published_at: datetime | None = None
    provider_updated_at: datetime | None = None
    publisher_relationship: PublisherRelationship = "unknown"
    description_completeness: DescriptionCompleteness = "missing"
    application_target: ApplicationTargetV2 = Field(default_factory=ApplicationTargetV2)
    parser_confidence: float = Field(default=0.0, ge=0, le=1)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobRequirements(BaseModel):
    """Structured requirements extracted from a JD."""

    must_have_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    seniority: str | None = None
    domain: str | None = None
    role_family: str | None = None
    education_level: str | None = None  # e.g. "Bachelor's", "Master's"
    experience_years_min: int | None = None
    experience_years_max: int | None = None
    visa_sponsorship: bool | None = None
    us_work_auth_required: bool | None = None  # True = requires US citizen/GC
    relocation_provided: bool | None = None
    remote_ok: bool | None = None


class RawJob(BaseModel):
    """Normalized job posting — output of every scraper."""

    id: UUID = Field(default_factory=uuid4)
    source: ATSType
    source_id: str  # ATS-native job ID
    company: str
    title: str
    location: str | None = None
    employment_type: EmploymentType = "unknown"
    seniority: SeniorityLevel = "unknown"
    description: str | None = None
    requirements: JobRequirements = Field(default_factory=JobRequirements)
    application_url: str | None = None
    ats_type: ATSType = "unknown"
    raw_data: dict = Field(default_factory=dict)
    provenance: JobProvenanceV2 | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None

    @field_validator("company", "title", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return " ".join(v.split()) if isinstance(v, str) else v

    @field_validator("company")
    @classmethod
    def bound_company_for_storage(cls, value: str) -> str:
        # Provider/community titles occasionally contain an entire posting in
        # the employer field. Preserve the original payload in raw_data, but
        # never let malformed transport text abort the whole discovery run.
        return value[:200].rstrip()

    @field_validator("title")
    @classmethod
    def bound_title_for_storage(cls, value: str) -> str:
        return value[:300].rstrip()

    @field_validator("source_id")
    @classmethod
    def bound_source_identity_for_storage(cls, value: str) -> str:
        value = value.strip()
        if len(value) <= 200:
            return value
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return f"{value[:174]}:{digest}"

    @field_validator("location")
    @classmethod
    def bound_location_for_storage(cls, value: str | None) -> str | None:
        return " ".join(value.split())[:200].rstrip() if isinstance(value, str) else value

    def dedup_key(self) -> str:
        """Stable source-global identity matching the database unique index."""
        return f"{self.source.strip().lower()}::{self.source_id.strip()}"


class SourceFetchResult(BaseModel):
    """Provider-neutral result; empty and failed fetches remain observable."""

    fetch_run_id: str
    adapter: SourceType
    endpoint_id: str | None = None
    query_arm_id: str | None = None
    started_at: datetime
    finished_at: datetime
    status: str
    http_status: int | None = None
    provider_records: int = 0
    normalized_records: int = 0
    malformed_records: int = 0
    records: list[RawJob] = Field(default_factory=list)
    error_code: str | None = None
    error_detail: str | None = None
    retry_after: int | None = None
    response_schema_version: str = "1"
    metadata: dict[str, Any] = Field(default_factory=dict)


def classify_employment_type(raw: str) -> EmploymentType:
    """Map free-form employment type strings to the canonical enum."""
    s = raw.lower()
    if any(w in s for w in ("co-op", "coop")):
        return "coop"
    if any(w in s for w in ("intern", "internship")):
        return "internship"
    if "part" in s:
        return "parttime"
    if any(w in s for w in ("contract", "contractor", "freelance")):
        return "contract"
    if "full" in s:
        return "fulltime"
    return "unknown"


def classify_seniority(title: str) -> SeniorityLevel:
    """Infer seniority from the job title."""
    t = title.lower()
    if any(w in t for w in ("intern", "internship", "co-op", "coop", "student")):
        return "internship"
    if any(w in t for w in ("staff", "principal", "distinguished")):
        return "staff"
    if any(w in t for w in ("senior", "sr.", " sr ", "lead")):
        return "senior"
    if any(w in t for w in ("junior", "jr.", " jr ", "associate", "entry", "new grad")):
        return "entry"
    if "mid" in t:
        return "mid"
    return "unknown"
