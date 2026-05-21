"""Fit Planner helpers for resume_builder. Imported by resume_builder.

Kept in a small dedicated module so resume_builder.py stays readable
even after the planner / outline / apply functions land. The module
is internal: external code should never import directly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("autoapply.generation.fit_planner")


def coerce_optional_int(value: Any) -> int | None:
    """Best-effort int coercion that tolerates LLM-shaped weirdness
    (string ints, None, floats, dict garbage). Returns None on failure
    so callers can decide what 'unspecified' means in context."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_section_outline(document) -> str:
    """Render the outline string fed into the planner prompt.

    Compact enough that the LLM can consider every section in one
    pass, but detailed enough to make a relevance call (bullet counts,
    matched tags, dates). Section ``id`` values are stable strings the
    planner echoes back so we can map decisions onto IR slots without
    ambiguity. This keeps the planner round-trip O(1) regardless of
    how many bullets the user has -- we never include full bullet text.
    """
    lines: list[str] = []

    def _exp_or_proj_summary(item) -> str:
        tags = sorted({tag for bullet in item.bullets for tag in (bullet.tags or [])})
        return (
            f"  title={item.title or item.name!r}; org={item.organization!r}; "
            f"dates={item.start_date}..{item.end_date}; "
            f"bullets={len(item.bullets)}; tags={tags[:8]}"
        )

    lines.append(f"- id=experiences  (canonical)  count={len(document.experiences)}")
    for idx, item in enumerate(document.experiences):
        lines.append(f"  experiences[{idx}]:")
        lines.append(_exp_or_proj_summary(item))

    lines.append(f"- id=projects  (canonical)  count={len(document.projects)}")
    for idx, item in enumerate(document.projects):
        lines.append(f"  projects[{idx}]:")
        lines.append(_exp_or_proj_summary(item))

    lines.append(f"- id=education  (canonical)  count={len(document.education)}")
    for idx, item in enumerate(document.education):
        if isinstance(item, dict):
            lines.append(
                f"  education[{idx}]: {item.get('institution', '')!r} "
                f"{item.get('degree', '')!r} {item.get('field', '')!r}"
            )

    skill_total = sum(len(values or []) for values in document.skills.values())
    lines.append(
        f"- id=skills  (canonical)  categories={len(document.skills)}  total={skill_total}"
    )

    for idx, custom in enumerate(getattr(document, "custom_sections", []) or []):
        section_id = f"custom:{idx}:{custom.title}"
        total_bullets = sum(len(e.bullets or []) for e in custom.entries)
        lines.append(
            f"- id={section_id!r}  custom  entries={len(custom.entries)}  "
            f"total_bullets={total_bullets}"
        )
        for eidx, entry in enumerate(custom.entries[:5]):
            lines.append(
                f"  custom[{idx}][{eidx}]: title={entry.title!r}; "
                f"org={entry.organization!r}; bullets={len(entry.bullets or [])}; "
                f"details={(entry.details or '')[:60]!r}"
            )
    return "\n".join(lines)


def summarise_plan(plan: dict[str, Any]) -> str:
    """One-line plan summary for logs + the planner's previous_plans block."""
    decisions: list[str] = []
    for section_id, decision in (plan.get("sections") or {}).items():
        if not decision.get("keep", True):
            decisions.append(f"DROP {section_id}")
        else:
            parts = [section_id]
            if decision.get("max_items") is not None:
                parts.append(f"max_items={decision['max_items']}")
            mode = decision.get("bullets_mode")
            if mode and mode != "keep":
                parts.append(f"bullets={mode}")
            decisions.append(":".join(parts))
    reasoning = plan.get("reasoning") or ""
    return f"{reasoning} | " + ", ".join(decisions) if decisions else reasoning


def trim_items_with_plan(items: list, decision: dict | None) -> list:
    """Trim experiences / projects to plan's ``max_items``. Always
    keeps >= 1 item so an out-of-spec plan can't blank the section."""
    if not decision:
        return items
    cap = decision.get("max_items")
    if cap is None or cap < 0:
        return items
    return items[: max(1, cap)]


def resize_section_bullets_in_place(
    items: list,
    mode: str,
    rewriter,
) -> None:
    """Batch-rewrite every bullet in ``items`` via one concurrent
    ``asyncio.gather``. ``mode`` is ``"shorter"`` (~12 words) or
    ``"longer"`` (~28 words). ``rewriter`` is the per-bullet callable
    injected by the caller (kept out of this module to avoid an import
    cycle with resume_builder).

    Per-bullet failures keep the original text so a flaky provider
    can't blank a whole section.
    """
    bullets = [
        (item, idx, bullet)
        for item in items
        for idx, bullet in enumerate(item.bullets)
    ]
    if not bullets:
        return

    from src.utils.llm import LLMError  # noqa: PLC0415
    from src.utils.parallelism import bullet_rewrite_cap  # noqa: PLC0415

    target_words = 12 if mode == "shorter" else 28
    cap = max(1, bullet_rewrite_cap())

    async def _runner() -> None:
        sem = asyncio.Semaphore(cap)

        async def _one(triple) -> None:
            item, idx, bullet = triple
            async with sem:
                try:
                    new_text = await asyncio.to_thread(
                        rewriter,
                        bullet.text,
                        direction=mode,
                        target_words=target_words,
                    )
                except (LLMError, Exception) as exc:  # noqa: BLE001
                    logger.debug(
                        "Bullet length rewrite kept original (%s): %s",
                        mode,
                        exc,
                    )
                    return
                item.bullets[idx].text = new_text

        await asyncio.gather(*(_one(triple) for triple in bullets))

    asyncio.run(_runner())


def apply_fit_plan(document, plan: dict[str, Any], *, bullet_rewriter):
    """Deterministically apply a fit plan returned by the LLM.

    Steps:
    1. Drop custom sections whose ``keep`` is False.
    2. Trim experiences / projects / custom entries to ``max_items``.
    3. If the plan asked for ``shorter`` / ``longer`` bullets in
       experiences / projects, run ONE concurrent batch rewrite per
       section (not per bullet) so the LLM bill stays bounded.

    ``bullet_rewriter`` is the callable that rewrites a single bullet
    for length; passed in by the caller so this module does not import
    from ``resume_builder`` (which would be a circular import).
    """
    decisions = plan.get("sections") or {}
    new_doc = document.model_copy(deep=True)

    new_doc.experiences = trim_items_with_plan(
        new_doc.experiences, decisions.get("experiences")
    )
    new_doc.projects = trim_items_with_plan(
        new_doc.projects, decisions.get("projects")
    )
    edu_decision = decisions.get("education") or {}
    if edu_decision.get("keep", True) is False:
        new_doc.education = []
    elif edu_decision.get("max_items") is not None:
        new_doc.education = new_doc.education[: edu_decision["max_items"]]

    kept_custom: list = []
    for idx, custom in enumerate(new_doc.custom_sections or []):
        section_id = f"custom:{idx}:{custom.title}"
        decision = decisions.get(section_id) or decisions.get(f"custom:{idx}") or {}
        if decision.get("keep", True) is False:
            logger.info("Fit plan: dropping custom section %r", custom.title)
            continue
        if decision.get("max_items") is not None:
            custom.entries = custom.entries[: decision["max_items"]]
        kept_custom.append(custom)
    new_doc.custom_sections = kept_custom

    for section_key, items in (
        ("experiences", new_doc.experiences),
        ("projects", new_doc.projects),
    ):
        mode = (decisions.get(section_key) or {}).get("bullets_mode")
        if mode not in {"shorter", "longer"}:
            continue
        resize_section_bullets_in_place(items, mode, bullet_rewriter)

    return new_doc
