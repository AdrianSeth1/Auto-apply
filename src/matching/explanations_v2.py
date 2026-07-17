"""Human-readable, evidence-linked V2 evaluation explanations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class EvaluationReasonV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason_code: str
    message: str
    job_excerpt: str = ""
    candidate_evidence_refs: tuple[str, ...] = ()
    missing_capability_ids: tuple[str, ...] = ()
    severity: Literal["info", "warning", "blocking"] = "info"


def component_strengths(
    scores: dict[str, float],
    *,
    title: str,
    evidence_refs: tuple[str, ...],
) -> tuple[EvaluationReasonV2, ...]:
    labels = {
        "role": "Work content aligns with the target role",
        "level": "Required level is plausible for the candidate",
        "evidence": "Canonical evidence supports the job's work",
        "domain": "Domain and tools are transferable",
        "attainability": "Employer and role scope are attainable",
        "preference": "Role and working conditions fit stated preferences",
        "posting_trust": "The posting is sufficiently complete and actionable",
    }
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        EvaluationReasonV2(
            reason_code=f"strong_{name}",
            message=f"{labels[name]} ({value:.0f}/100)",
            job_excerpt=title if name in {"role", "level"} else "",
            candidate_evidence_refs=evidence_refs if name == "evidence" else (),
        )
        for name, value in ranked[:3]
        if value >= 55
    )


def component_gaps(
    scores: dict[str, float],
    *,
    missing_capabilities: tuple[str, ...] = (),
    gate_failures: tuple[tuple[str, str], ...] = (),
) -> tuple[EvaluationReasonV2, ...]:
    gaps = [
        EvaluationReasonV2(
            reason_code=code,
            message=message,
            severity="blocking",
        )
        for code, message in gate_failures
    ]
    floor_labels = {
        "role": (60, "Role/work compatibility is below the viable floor"),
        "level": (50, "Required seniority or ownership is above the viable floor"),
        "evidence": (50, "Direct evidence for the required work is incomplete"),
        "domain": (50, "Domain or required-tool transfer is weak"),
        "attainability": (45, "Employer/role attainability is weak"),
        "posting_trust": (45, "Posting provenance or completeness is weak"),
    }
    for name, (floor, message) in floor_labels.items():
        value = scores[name]
        if value < floor:
            gaps.append(
                EvaluationReasonV2(
                    reason_code=f"low_{name}",
                    message=f"{message} ({value:.0f}/100)",
                    missing_capability_ids=(
                        missing_capabilities if name == "evidence" else ()
                    ),
                    severity="warning",
                )
            )
    return tuple(gaps)


__all__ = ["EvaluationReasonV2", "component_gaps", "component_strengths"]
