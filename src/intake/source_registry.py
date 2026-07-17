"""Durable V2 endpoint registry seeded idempotently from companies.yaml."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.config import PROJECT_ROOT
from src.core.models import SourceEndpoint, TENANT_DEFAULT
from src.intake.source_health import EndpointHealthV2, FetchStatus, transition_health
from src.matching.target_schema import normalize_phrase


class EndpointSeedV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter: str
    endpoint_key: str
    adapter_config: dict[str, Any]
    state: str = "candidate"
    compliance_status: str = "approved_public_api"
    discovery_provenance: dict[str, Any]


_KNOWN_DEGRADED = {
    ("greenhouse", "sentry"),
    ("greenhouse", "gong"),
    ("ashby", "p 1"),
}


def _endpoint_key(adapter: str, value: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(value, dict):
        config = {str(key): item for key, item in value.items()}
        if adapter == "workday":
            required = [str(config.get(key) or "").strip() for key in ("tenant", "host", "site")]
            if not all(required):
                raise ValueError(f"Malformed Workday endpoint: {value!r}")
            return "|".join(part.casefold() for part in required), config
        key = str(config.get("slug") or config.get("company") or config.get("id") or "").strip()
        if not key:
            raise ValueError(f"Endpoint mapping has no stable key: {value!r}")
        return normalize_phrase(key), config
    key = normalize_phrase(str(value))
    if not key:
        raise ValueError("Endpoint key may not be empty")
    return key, {"slug": str(value).strip()}


def load_endpoint_seeds(path: Path | None = None) -> list[EndpointSeedV2]:
    path = path or PROJECT_ROOT / "config" / "companies.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    seeds: list[EndpointSeedV2] = []
    try:
        provenance_path = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        provenance_path = str(path)
    for adapter, entries in payload.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            key, config = _endpoint_key(str(adapter), entry)
            state = "degraded" if (str(adapter), key) in _KNOWN_DEGRADED else "candidate"
            seeds.append(
                EndpointSeedV2(
                    adapter=str(adapter),
                    endpoint_key=key,
                    adapter_config=config,
                    state=state,
                    discovery_provenance={
                        "kind": "companies_yaml_seed",
                        "path": provenance_path,
                    },
                )
            )
    return seeds


def import_endpoint_seeds(
    session: Session,
    *,
    path: Path | None = None,
    tenant_id: str = TENANT_DEFAULT,
    dry_run: bool = False,
) -> dict[str, int]:
    """Insert missing seeds only; never overwrite runtime health or YAML."""

    counts = {"seen": 0, "created": 0, "existing": 0}
    for seed in load_endpoint_seeds(path):
        counts["seen"] += 1
        existing = session.scalar(
            select(SourceEndpoint).where(
                SourceEndpoint.tenant_id == tenant_id,
                func.lower(SourceEndpoint.adapter) == seed.adapter.casefold(),
                func.lower(SourceEndpoint.endpoint_key) == seed.endpoint_key.casefold(),
            )
        )
        if existing is not None:
            counts["existing"] += 1
            continue
        counts["created"] += 1
        if dry_run:
            continue
        session.add(
            SourceEndpoint(
                tenant_id=tenant_id,
                adapter=seed.adapter,
                endpoint_key=seed.endpoint_key,
                adapter_config=seed.adapter_config,
                discovery_provenance=seed.discovery_provenance,
                state=seed.state,
                compliance_status=seed.compliance_status,
                consecutive_failures=1 if seed.state == "degraded" else 0,
                first_failure_at=datetime.now(UTC) if seed.state == "degraded" else None,
            )
        )
    if not dry_run:
        session.flush()
    return counts


def endpoint_health(endpoint: SourceEndpoint) -> EndpointHealthV2:
    return EndpointHealthV2(
        state=endpoint.state,
        consecutive_failures=endpoint.consecutive_failures,
        consecutive_empty=endpoint.consecutive_empty,
        recovery_successes=endpoint.recovery_successes,
        first_failure_at=endpoint.first_failure_at,
        last_checked_at=endpoint.last_checked_at,
        last_success_at=endpoint.last_success_at,
        last_nonempty_at=endpoint.last_nonempty_at,
        next_probe_at=endpoint.next_probe_at,
    )


def update_endpoint_health(
    endpoint: SourceEndpoint,
    status: FetchStatus,
    *,
    now: datetime | None = None,
    retry_after_seconds: int | None = None,
) -> EndpointHealthV2:
    updated = transition_health(
        endpoint_health(endpoint),
        status,
        now=now,
        retry_after_seconds=retry_after_seconds,
    )
    for key, value in updated.model_dump().items():
        setattr(endpoint, key, value)
    return updated


__all__ = [
    "EndpointSeedV2",
    "endpoint_health",
    "import_endpoint_seeds",
    "load_endpoint_seeds",
    "update_endpoint_health",
]
