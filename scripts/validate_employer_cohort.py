"""Validate a cohort research artifact; release mode requires verified approval."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator

from src.core.config import PROJECT_ROOT

TARGETS = {
    "ai-implementation", "saas-implementation", "revenue-operations-analyst",
    "associate-solutions-engineering", "technical-customer-success",
}


class CohortEmployer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    careers_url: HttpUrl
    evidence_date: date
    rationale: str
    targets: list[str]
    enabled: bool = False
    verification_status: str

    @field_validator("targets")
    @classmethod
    def valid_targets(cls, values: list[str]) -> list[str]:
        unknown = set(values) - TARGETS
        if unknown:
            raise ValueError(f"unknown targets: {sorted(unknown)}")
        return values


def validate(path: Path, *, release: bool = False) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    employers = [CohortEmployer.model_validate(value) for value in payload["employers"]]
    names = [employer.name.casefold() for employer in employers]
    urls = [str(employer.careers_url).rstrip("/").casefold() for employer in employers]
    if len(names) != int(payload["required_net_new_employers"]):
        raise ValueError("cohort must contain exactly the configured net-new employer quota")
    if len(names) != len(set(names)) or len(urls) != len(set(urls)):
        raise ValueError("duplicate employer name or careers endpoint")
    links = Counter(target for employer in employers for target in employer.targets)
    if sum(links.values()) < int(payload["required_target_links"]):
        raise ValueError("target-link quota not met")
    missing = TARGETS - {target for target, count in links.items() if count >= 10}
    if missing:
        raise ValueError(f"targets below ten supported links: {sorted(missing)}")
    if release:
        not_verified = [employer.name for employer in employers if employer.verification_status != "verified_approved" or not employer.enabled]
        if not_verified:
            raise ValueError(f"release blocked; pending verification/approval: {not_verified}")
    elif any(employer.enabled for employer in employers if employer.verification_status != "verified_approved"):
        raise ValueError("unverified or exception employers must remain inactive")
    return {"employers": len(employers), "target_links": sum(links.values()), "by_target": dict(sorted(links.items())), "release_ready": all(employer.enabled and employer.verification_status == "verified_approved" for employer in employers)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=PROJECT_ROOT / "config" / "employer_cohort.v1.yaml")
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    print(validate(args.path, release=args.release))


if __name__ == "__main__":
    main()
