"""Job Pool V2 feature-mode helpers.

V1 remains authoritative until the prospective gates in the V2 architecture
pass. Keeping mode resolution in one module prevents routes, workers, and CLI
commands from interpreting the setting differently.
"""

from __future__ import annotations

from typing import Any, Literal

PipelineVersion = Literal["v1", "v2_shadow", "v2"]
VALID_PIPELINE_VERSIONS: frozenset[str] = frozenset({"v1", "v2_shadow", "v2"})


def get_pipeline_version(config: dict[str, Any] | None) -> PipelineVersion:
    """Return the validated matching pipeline mode, defaulting safely to V1."""

    raw: Any = ((config or {}).get("matching") or {}).get("pipeline_version", "v1")
    value = str(raw or "v1").strip().lower()
    if value not in VALID_PIPELINE_VERSIONS:
        raise ValueError(
            f"Invalid matching.pipeline_version={raw!r}; expected one of "
            f"{sorted(VALID_PIPELINE_VERSIONS)}"
        )
    return value  # type: ignore[return-value]


def writes_review_queue(version: PipelineVersion) -> bool:
    """Only full V2 may create cards; shadow mode is persistence-only."""

    return version == "v2"


__all__ = [
    "PipelineVersion",
    "VALID_PIPELINE_VERSIONS",
    "get_pipeline_version",
    "writes_review_queue",
]
