"""Verified direct-employer activation and bounded refresh rotation.

The activation registry is evidence-backed configuration, not a discovery
surface. Every endpoint returned here has already passed the public ATS
probe contract recorded alongside the registry. All approved endpoints are
eligible for reuse on every portfolio run, while one 25-employer refresh
group is fetched live per ordinary run to keep network cost bounded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.config import PROJECT_ROOT
from src.matching.target_schema import load_unique_yaml

EndpointKey = tuple[str, str]


def endpoint_key(adapter: str, entry: Any) -> EndpointKey | None:
    if adapter == "workday":
        try:
            value = "/".join(str(entry[field]) for field in ("tenant", "host", "site"))
        except (TypeError, KeyError):
            return None
        return adapter, value
    if isinstance(entry, str):
        return adapter, entry
    return None


@dataclass(frozen=True)
class SupplyRefreshRotation:
    group_id: str
    group_index: int
    group_count: int
    approved_endpoint_count: int
    live_endpoint_keys: frozenset[EndpointKey]
    deferred_endpoint_keys: frozenset[EndpointKey]


def load_supply_refresh_rotation(
    acquisition_cycle: int,
    *,
    path: Path | None = None,
) -> SupplyRefreshRotation:
    """Choose one deterministic refresh group from the approved registry."""

    registry_path = path or PROJECT_ROOT / "config" / "employer_supply_activation.v1.yaml"
    data = load_unique_yaml(registry_path.read_text(encoding="utf-8"))
    waves = list(data.get("waves") or [])
    if not waves:
        return SupplyRefreshRotation(
            group_id="none",
            group_index=0,
            group_count=0,
            approved_endpoint_count=0,
            live_endpoint_keys=frozenset(),
            deferred_endpoint_keys=frozenset(),
        )

    all_keys: set[EndpointKey] = set()
    keys_by_wave: list[set[EndpointKey]] = []
    for wave in waves:
        wave_keys: set[EndpointKey] = set()
        for employer in wave.get("employers") or []:
            key = endpoint_key(str(employer.get("adapter") or ""), employer.get("endpoint"))
            if key is not None:
                wave_keys.add(key)
                all_keys.add(key)
        keys_by_wave.append(wave_keys)

    group_index = acquisition_cycle % len(waves)
    live_keys = keys_by_wave[group_index]
    return SupplyRefreshRotation(
        group_id=str(waves[group_index].get("id") or f"group-{group_index + 1}"),
        group_index=group_index,
        group_count=len(waves),
        approved_endpoint_count=len(all_keys),
        live_endpoint_keys=frozenset(live_keys),
        deferred_endpoint_keys=frozenset(all_keys - live_keys),
    )


def apply_supply_refresh_rotation(
    needs_fetch: dict[str, list[Any]],
    rotation: SupplyRefreshRotation,
) -> tuple[dict[str, list[Any]], set[EndpointKey]]:
    """Defer off-group approved endpoints to persisted-snapshot reuse.

    Endpoints outside the approved registry are untouched. An approved
    endpoint in the current group is also untouched, so freshness still
    decides whether it needs a live request.
    """

    rotated: dict[str, list[Any]] = {}
    deferred: set[EndpointKey] = set()
    for adapter, entries in needs_fetch.items():
        for entry in entries:
            key = endpoint_key(adapter, entry)
            if key in rotation.deferred_endpoint_keys:
                deferred.add(key)
                continue
            rotated.setdefault(adapter, []).append(entry)
    return rotated, deferred


__all__ = [
    "SupplyRefreshRotation",
    "apply_supply_refresh_rotation",
    "endpoint_key",
    "load_supply_refresh_rotation",
]
