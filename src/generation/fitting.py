"""Content fitting for template capacity constraints."""

from __future__ import annotations

import re

from src.documents.templates import TemplateManifest
from src.generation.ir import ResumeDocument, ResumeItem


def fit_resume_document_to_template(
    document: ResumeDocument,
    manifest: TemplateManifest,
) -> ResumeDocument:
    """Return a copy of the resume IR constrained by template capacity.

    Fitting removes or shortens content; it never invents new claims or changes
    template formatting. Visual details remain in template.docx/styles.

    Multi-page targets scale the per-section caps proportionally so a
    2-page template asks the bullet selector for roughly twice as much
    content as the default 1-page layout. The renderer + post-render
    page-count validator then enforce the actual page budget.
    """
    if manifest.document_type != "resume":
        return document

    page_scale = _page_scale(manifest)

    fitted = document.model_copy(deep=True)
    fitted.template_id = manifest.template_id
    if manifest.section_order:
        # Manifest section_order is the source of truth, but ``summary``
        # is filtered out unconditionally -- generated resumes never
        # render a Summary section.
        fitted.section_order = [s for s in manifest.section_order if s != "summary"]

    if not _section_enabled(manifest, "education"):
        fitted.education = []
    else:
        edu_limit = _section_limit(manifest, "education", "max_items", len(fitted.education))
        fitted.education = fitted.education[: max(edu_limit, edu_limit * page_scale)]

    fitted.experiences = _fit_items(
        fitted.experiences,
        manifest,
        section="experience",
        max_items=_scaled_limit(manifest.capacity.max_experience_items, page_scale),
        page_scale=page_scale,
    )
    fitted.projects = _fit_items(
        fitted.projects,
        manifest,
        section="projects",
        max_items=_scaled_limit(manifest.capacity.max_project_items, page_scale),
        page_scale=page_scale,
    )
    fitted.skills = _fit_skills(fitted.skills, manifest, page_scale=page_scale)
    _fit_total_bullets(
        fitted, _scaled_limit(manifest.capacity.max_bullets_total, page_scale)
    )
    fitted.metadata = {
        **fitted.metadata,
        "template_id": manifest.template_id,
        "template_capacity": manifest.capacity.model_dump(mode="json"),
        "target_pages": manifest.target_pages or manifest.capacity.max_pages,
    }
    return fitted


def _page_scale(manifest: TemplateManifest) -> int:
    target = manifest.target_pages or manifest.capacity.max_pages or 1
    return max(1, int(target))


def _scaled_limit(value: int | None, page_scale: int) -> int | None:
    if value is None:
        return None
    return value * page_scale


def _fit_items(
    items: list[ResumeItem],
    manifest: TemplateManifest,
    *,
    section: str,
    max_items: int | None,
    page_scale: int = 1,
) -> list[ResumeItem]:
    if not _section_enabled(manifest, section):
        return []

    base_item_cap = _section_limit(manifest, section, "max_items", max_items or len(items))
    max_item_count = base_item_cap * max(1, page_scale)
    max_bullets = _section_limit(manifest, section, "max_bullets_per_item", 4)
    max_words = _section_limit(
        manifest,
        section,
        "max_words_per_bullet",
        manifest.capacity.max_words_per_bullet or 24,
    )

    fitted_items = [item.model_copy(deep=True) for item in items[:max_item_count]]
    for item in fitted_items:
        item.bullets = sorted(item.bullets, key=lambda bullet: bullet.score, reverse=True)[
            :max_bullets
        ]
        for bullet in item.bullets:
            bullet.text = _trim_words(bullet.text, max_words)
    return fitted_items


def _fit_skills(
    skills: dict[str, list[str]],
    manifest: TemplateManifest,
    *,
    page_scale: int = 1,
) -> dict[str, list[str]]:
    if not _section_enabled(manifest, "skills"):
        return {}
    base_lines = _section_limit(
        manifest,
        "skills",
        "max_lines",
        manifest.capacity.max_skill_lines or len(skills),
    )
    max_lines = base_lines * max(1, page_scale)
    return {key: values for index, (key, values) in enumerate(skills.items()) if index < max_lines}


def _fit_total_bullets(document: ResumeDocument, max_total: int | None) -> None:
    if not max_total:
        return
    while _bullet_count(document) > max_total:
        weakest = None
        for item in [*document.projects, *document.experiences]:
            if not item.bullets:
                continue
            candidate = min(item.bullets, key=lambda bullet: bullet.score)
            if weakest is None or candidate.score < weakest[1].score:
                weakest = (item, candidate)
        if weakest is None:
            return
        weakest[0].bullets.remove(weakest[1])


def _bullet_count(document: ResumeDocument) -> int:
    return sum(len(item.bullets) for item in [*document.experiences, *document.projects])


# Clause boundaries where a trimmed bullet may end: sentence punctuation,
# semicolons, commas (followed by space), or a spaced em/en dash.
_CLAUSE_BOUNDARY_RE = re.compile(r"[.;!?](?=\s|$)|,(?=\s)|\s[—–](?=\s)")


def _trim_words(text: str, max_words: int | None) -> str:
    """Trim an over-long bullet WITHOUT cutting mid-phrase.

    The old behaviour sliced the word list at ``max_words`` and stripped
    trailing punctuation, which shipped resumes whose bullets ended
    mid-sentence ("... Piper TTS for spoken"). Now the bullet is only
    trimmed when a clause boundary falls inside the budget and keeps at
    least half of it; otherwise the text is returned untouched -- the
    post-render page-fit loop (LLM shorten + weakest-bullet drop) deals
    with genuine overflow using real page counts instead of a blind
    word cap.
    """
    if not max_words:
        return text
    words = re.findall(r"\S+", text)
    if len(words) <= max_words:
        return text

    prefix = " ".join(words[:max_words])
    best_cut: str | None = None
    for match in _CLAUSE_BOUNDARY_RE.finditer(prefix):
        candidate = prefix[: match.end()].strip().rstrip(",;—– ")
        if len(re.findall(r"\S+", candidate)) < max_words // 2:
            continue
        if candidate.count("**") % 2 or candidate.count("*") % 2:
            # Never orphan inline bold/italic markup mid-span.
            continue
        best_cut = candidate
    if best_cut is None:
        return text
    if not best_cut.endswith((".", "!", "?")):
        best_cut += "."
    return best_cut


def _section_enabled(manifest: TemplateManifest, section: str) -> bool:
    config = manifest.sections.get(section)
    return True if config is None else config.enabled


def _section_limit(
    manifest: TemplateManifest,
    section: str,
    field: str,
    default: int,
) -> int:
    config = manifest.sections.get(section)
    value = getattr(config, field, None) if config else None
    return int(value if value is not None else default)
