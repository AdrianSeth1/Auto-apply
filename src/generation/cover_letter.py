"""Cover letter generator — structure-constrained semi-generation.

Structure:
  1. Opening: role + reason for interest
  2. Middle: 2-3 best-matching evidence points from profile
  3. Company tie-in: why this specific company
  4. Close: availability / enthusiasm

All LLM output is bounded by a structural template to prevent
style drift and hallucination.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from src.documents.docx_engine import build_cover_letter_from_ir
from src.documents.file_manager import get_output_paths
from src.documents.latex_engine import build_cover_letter_tex_from_ir, compile_latex_to_pdf
from src.documents.pdf_converter import convert_to_pdf
from src.documents.templates import (
    TemplateManifest,
    default_manifest,
    ensure_template_package,
    load_template_package,
)
from src.generation.evidence import select_relevant_evidence
from src.generation.ir import CoverLetterDocument, CoverLetterParagraph
from src.generation.validator import (
    validate_cover_letter_artifacts,
    validate_cover_letter_document,
    validate_latex_artifacts,
)
from src.intake.schema import RawJob
from src.utils.llm import LLMError, generate_text

logger = logging.getLogger("autoapply.generation.cover_letter")

DEFAULT_OUTPUT_DIR = Path("data/output")

# How many LLM re-asks to take before falling back to a hard paragraph
# trim. Each round costs one LLM call (~30-60s), so the cap directly
# determines the worst-case generation latency. 1 retry keeps total
# wall time within the front-end's poll budget while still giving the
# LLM a second shot when the first draft missed the page target.
_MAX_LLM_LENGTH_REGENS = 1
# Absolute ceiling so a degenerate "LLM always returns junk" loop still
# terminates with *some* output. Higher than _MAX_LLM_LENGTH_REGENS so
# the deterministic paragraph-drop fallback still gets attempts.
_MAX_RENDER_PASSES = 4


def _render_cover_letter_to_target_pages(
    *,
    document,
    template_path: Path | None,
    template_manifest,
    docx_output: Path,
    pdf_output: Path,
    target_pages: int,
    job: RawJob | None = None,
    profile_data: dict[str, Any] | None = None,
    evidence_bullets: list[str] | None = None,
    use_llm: bool = True,
):
    """Render the cover letter, then iteratively ask the LLM for a longer
    or shorter draft until the produced PDF matches the target page count.

    The previous implementation chopped paragraphs to hit the page
    budget. That was the wrong call: cover letters are LLM-generated, so
    the LLM is the right place to lengthen or shorten the prose. The
    pipeline now feeds the rendered page count back into the prompt and
    re-generates, only falling back to a hard paragraph drop if the LLM
    refuses to converge (e.g. provider outage, rate limit).
    """
    from src.documents.page_count import get_pdf_page_count  # noqa: PLC0415

    current = document

    def _render_and_count(doc):
        docx = build_cover_letter_from_ir(
            doc,
            docx_output,
            template_path=template_path,
            manifest=template_manifest,
        )
        pdf: Path | None = None
        try:
            pdf = convert_to_pdf(docx, pdf_output)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cover letter PDF conversion failed: %s", exc)
        pages = get_pdf_page_count(pdf) if pdf else None
        return docx, pdf, pages

    docx_path, pdf_path, pages = _render_and_count(current)
    if target_pages <= 0 or pages is None:
        return current, docx_path, pdf_path

    llm_regens = 0
    for attempt in range(_MAX_RENDER_PASSES):
        if pages == target_pages:
            return current, docx_path, pdf_path

        if (
            use_llm
            and llm_regens < _MAX_LLM_LENGTH_REGENS
            and job is not None
            and profile_data is not None
            and evidence_bullets is not None
        ):
            feedback = _length_feedback_message(
                rendered_pages=pages,
                target_pages=target_pages,
                current_text="\n\n".join(p.text for p in current.paragraphs),
            )
            logger.info(
                "Cover letter rendered %d pages, target %d; asking LLM to %s "
                "(LLM regen %d/%d).",
                pages,
                target_pages,
                "shorten" if pages > target_pages else "lengthen",
                llm_regens + 1,
                _MAX_LLM_LENGTH_REGENS,
            )
            try:
                new_text = _generate_with_llm(
                    job,
                    profile_data,
                    evidence_bullets,
                    target_pages=target_pages,
                    length_feedback=feedback,
                    previous_attempt="\n\n".join(p.text for p in current.paragraphs),
                )
            except LLMError as exc:
                logger.info(
                    "LLM length regen failed (%s); falling back to paragraph trim.",
                    exc,
                )
                llm_regens = _MAX_LLM_LENGTH_REGENS  # stop trying the LLM
            else:
                current = current.model_copy(
                    update={
                        "paragraphs": _cover_paragraphs_from_text(
                            new_text, evidence_bullets
                        )
                    }
                )
                current = _fit_cover_letter_document(current, template_manifest)
                llm_regens += 1
                docx_path, pdf_path, pages = _render_and_count(current)
                continue

        # Bounded fallback: only used when LLM regen is unavailable or
        # has refused to converge. Adds or removes a single paragraph
        # rather than relying on the LLM, so the loop still terminates.
        adjusted = (
            _drop_weakest_paragraph(current)
            if pages > target_pages
            else None  # cannot fabricate paragraphs without the LLM
        )
        if adjusted is None:
            logger.info(
                "Cover letter cannot be adjusted further (rendered %d, target %d).",
                pages,
                target_pages,
            )
            return current, docx_path, pdf_path
        current = adjusted
        docx_path, pdf_path, pages = _render_and_count(current)

    return current, docx_path, pdf_path


def _length_feedback_message(
    *, rendered_pages: int, target_pages: int, current_text: str
) -> str:
    """Build the natural-language steering message for the LLM regen."""
    min_words, target_words, max_words = _length_window_for(target_pages)
    actual_words = len(current_text.split())
    if rendered_pages > target_pages:
        delta = rendered_pages - target_pages
        return (
            f"Your previous draft was {rendered_pages} pages "
            f"({actual_words} words) but the template only allows "
            f"{target_pages} page(s). Rewrite the body to be SHORTER "
            f"({min_words}-{max_words} words, aim for {target_words}). "
            f"Cut about {delta} page worth of prose -- tighten sentences, "
            "drop the least-impactful evidence, and remove repetition. "
            "Keep every grounded factual claim from the evidence list."
        )
    delta = target_pages - rendered_pages
    return (
        f"Your previous draft was only {rendered_pages} page(s) "
        f"({actual_words} words) but the template targets exactly "
        f"{target_pages} page(s). Rewrite the body to be LONGER "
        f"({min_words}-{max_words} words, aim for {target_words}). "
        f"Add about {delta} page worth of substantive content by going "
        "deeper into each evidence paragraph, expanding the company / role "
        "tie-in, and adding a separate paragraph that connects an "
        "additional applicant experience to a job requirement. Do NOT "
        "fabricate experiences -- only elaborate on what is grounded in "
        "the evidence list."
    )


def _drop_weakest_paragraph(document):
    """Return a copy with the highest-impact, lowest-cost paragraph removed.

    Preference order:
    1. Drop a ``company_fit`` paragraph if present.
    2. Else drop one ``experience_evidence`` paragraph (keeping the rest).
    3. Else give up -- we will not delete the opening or closing.
    """
    paragraphs = list(document.paragraphs)
    if not paragraphs:
        return None

    drop_order = ("company_fit", "experience_evidence")
    for kind in drop_order:
        for idx, paragraph in enumerate(paragraphs):
            if paragraph.type == kind:
                if kind == "experience_evidence":
                    remaining = sum(
                        1
                        for other in paragraphs
                        if other.type == "experience_evidence"
                    )
                    if remaining <= 1:
                        continue
                trimmed = document.model_copy(deep=True)
                trimmed.paragraphs.pop(idx)
                return trimmed
    return None


def generate_cover_letter(
    job: RawJob,
    profile_data: dict[str, Any],
    evidence_bullets: list[str] | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    use_llm: bool = True,
    template_id: str = "classic_v1",
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Generate a tailored cover letter for a specific job.

    Args:
        job: Target job posting.
        profile_data: Full applicant profile dict.
        evidence_bullets: Pre-selected evidence points. If None, auto-selected.
        output_dir: Directory for output files.
        use_llm: Whether to use LLM for generation (if False, returns template only).

    Returns:
        Dict with generated paths plus the CoverLetterDocument IR and validation result.
    """
    identity = profile_data.get("identity", {})
    template_manifest = _manifest_for_template_path(template_path)
    if template_path is None or not template_path.exists():
        package = ensure_template_package("cover_letter", template_id)
        template_path = package.template_path
        template_manifest = package.manifest
    elif template_manifest is None:
        template_manifest = default_manifest("cover_letter")

    # Select evidence points if not provided
    if evidence_bullets is None:
        evidence_bullets = _select_evidence(job, profile_data)

    target_pages = template_manifest.target_pages or template_manifest.capacity.max_pages

    if use_llm:
        try:
            text = _generate_with_llm(
                job, profile_data, evidence_bullets, target_pages=target_pages
            )
        except (LLMError, Exception) as e:
            logger.warning("LLM cover letter generation failed (%s), using template", e)
            text = _generate_template(job, identity, evidence_bullets)
    else:
        text = _generate_template(job, identity, evidence_bullets)

    paths = get_output_paths(
        company=job.company,
        role=job.title,
        output_dir=output_dir,
        pattern=template_manifest.filename_pattern,
        profile_name=profile_data.get("identity", {}).get("full_name", ""),
        custom_label=template_manifest.filename_custom_label,
        template_id=template_manifest.template_id,
    )

    document = build_cover_letter_document(
        job=job,
        profile_data=profile_data,
        body_text=text,
        evidence_bullets=evidence_bullets,
    )
    document = _fit_cover_letter_document(document, template_manifest)
    document, docx_path, pdf_path = _render_cover_letter_to_target_pages(
        document=document,
        template_path=template_path,
        template_manifest=template_manifest,
        docx_output=paths["cover_docx"],
        pdf_output=paths["cover_pdf"],
        target_pages=target_pages,
        job=job,
        profile_data=profile_data,
        evidence_bullets=evidence_bullets,
        use_llm=use_llm,
    )
    validation = validate_cover_letter_document(document, target_pages=target_pages)
    validation = validate_cover_letter_artifacts(
        validation,
        docx_path=docx_path,
        pdf_path=pdf_path,
        pdf_attempted=True,
        max_pages=template_manifest.capacity.max_pages,
        target_pages=target_pages,
    )

    logger.info("Generated cover letter for %s at %s", job.title, job.company)
    result: dict[str, Any] = {
        "text": text,
        "docx": docx_path,
        "ir": document,
        "validation": validation,
    }
    if pdf_path:
        result["pdf"] = pdf_path
    return result


def generate_cover_letter_latex(
    job: RawJob,
    profile_data: dict[str, Any],
    evidence_bullets: list[str] | None = None,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    use_llm: bool = True,
    template_id: str,
) -> dict[str, Any]:
    """Generate a tailored cover letter as LaTeX, with optional PDF compilation."""
    identity = profile_data.get("identity", {})
    package = load_template_package("cover_letter", template_id)
    if package.manifest.renderer != "latex":
        raise ValueError("Selected cover letter template is not a LaTeX template.")

    if evidence_bullets is None:
        evidence_bullets = _select_evidence(job, profile_data)

    target_pages = package.manifest.target_pages or package.manifest.capacity.max_pages

    if use_llm:
        try:
            text = _generate_with_llm(
                job, profile_data, evidence_bullets, target_pages=target_pages
            )
        except (LLMError, Exception) as exc:
            logger.warning("LLM cover letter generation failed (%s), using template", exc)
            text = _generate_template(job, identity, evidence_bullets)
    else:
        text = _generate_template(job, identity, evidence_bullets)

    paths = get_output_paths(
        company=job.company,
        role=job.title,
        output_dir=output_dir,
        pattern=package.manifest.filename_pattern,
        profile_name=profile_data.get("identity", {}).get("full_name", ""),
        custom_label=package.manifest.filename_custom_label,
        template_id=package.manifest.template_id,
    )
    document = build_cover_letter_document(
        job=job,
        profile_data=profile_data,
        body_text=text,
        evidence_bullets=evidence_bullets,
    )
    document = _fit_cover_letter_document(document, package.manifest)
    validation = validate_cover_letter_document(document, target_pages=target_pages)
    tex_path = build_cover_letter_tex_from_ir(
        package.template_path,
        document,
        paths["cover_tex"],
        manifest=package.manifest,
    )

    pdf_path = None
    try:
        pdf_path = compile_latex_to_pdf(tex_path, paths["cover_pdf"])
    except Exception as exc:
        logger.warning("LaTeX cover letter PDF compilation failed: %s", exc)

    validation = validate_latex_artifacts(
        validation,
        tex_path=tex_path,
        pdf_path=pdf_path,
        pdf_attempted=True,
        max_pages=package.manifest.capacity.max_pages,
        target_pages=target_pages,
    )

    result: dict[str, Any] = {
        "text": text,
        "tex": tex_path,
        "ir": document,
        "validation": validation,
    }
    if pdf_path:
        result["pdf"] = pdf_path
    logger.info("Generated LaTeX cover letter for %s at %s", job.title, job.company)
    return result


def build_cover_letter_document(
    *,
    job: RawJob,
    profile_data: dict[str, Any],
    body_text: str,
    evidence_bullets: list[str],
) -> CoverLetterDocument:
    """Create a structured cover letter IR from generated body text."""
    identity = profile_data.get("identity", {})
    return CoverLetterDocument(
        recipient={"company": job.company, "hiring_manager": None},
        applicant={
            "name": identity.get("full_name", ""),
            "email": identity.get("email", ""),
            "phone": identity.get("phone", ""),
        },
        paragraphs=_cover_paragraphs_from_text(body_text, evidence_bullets),
        metadata={"target_role": job.title, "company": job.company},
    )


def _fit_cover_letter_document(
    document: CoverLetterDocument,
    manifest: TemplateManifest,
) -> CoverLetterDocument:
    if manifest.document_type != "cover_letter":
        return document
    fitted = document.model_copy(deep=True)
    body_config = manifest.sections.get("body")
    if body_config and body_config.max_items:
        fitted.paragraphs = fitted.paragraphs[: body_config.max_items]
    fitted.metadata = {
        **fitted.metadata,
        "template_id": manifest.template_id,
        "template_capacity": manifest.capacity.model_dump(mode="json"),
    }
    return fitted


def _manifest_for_template_path(template_path: Path | None) -> TemplateManifest | None:
    if template_path is None:
        return None
    manifest_path = template_path.parent / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return TemplateManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Ignoring invalid template manifest %s: %s", manifest_path, exc)
        return None


# Per-page word-count window. The cover letter renderer assumes 11pt
# Times New Roman with 0.85" margins, which fits ~340 words on a single
# page including the 5-paragraph frame. Multi-page targets multiply this.
_WORDS_PER_PAGE_TARGET = 340
_WORDS_PER_PAGE_MIN = 280
_WORDS_PER_PAGE_MAX = 400


def _length_window_for(target_pages: int) -> tuple[int, int, int]:
    """Return (min_words, target_words, max_words) for ``target_pages``.

    Used to drive both the LLM prompt (so it aims for the right length
    from the start) and the post-render validator (so a deliberately
    long 2-page letter is not flagged as too long).
    """
    pages = max(1, int(target_pages))
    return (
        _WORDS_PER_PAGE_MIN * pages,
        _WORDS_PER_PAGE_TARGET * pages,
        _WORDS_PER_PAGE_MAX * pages,
    )


def _cl_system_prompt(target_pages: int) -> str:
    pages = max(1, int(target_pages))
    min_words, target_words, max_words = _length_window_for(pages)
    if pages == 1:
        structure = (
            "1. OPENING (2-3 sentences): State the role and connect interest "
            "to the job's actual technical work.\n\n"
            "2. EVIDENCE PARAGRAPH 1 (3-4 sentences): Map one applicant "
            "experience to one job requirement.\n\n"
            "3. EVIDENCE PARAGRAPH 2 (3-4 sentences): Map a different "
            "experience to another requirement.\n\n"
            "4. COMPANY / ROLE TIE-IN (2-3 sentences): Explain why this role's "
            "domain and responsibilities fit.\n\n"
            "5. CLOSE (2 sentences): Express enthusiasm and availability. "
            "Keep it professional and brief."
        )
        paragraph_rule = "Use exactly 5 paragraphs separated by blank lines"
    else:
        extra_evidence = max(1, pages - 1)
        structure = (
            "1. OPENING (3-4 sentences): State the role and connect interest "
            "to the job's actual technical work.\n\n"
            "2. EVIDENCE PARAGRAPHS (multiple): Provide "
            f"{2 + extra_evidence} evidence paragraphs of 4-5 sentences each, "
            "each mapping a different applicant experience to a different "
            "job requirement. Go into more depth than a one-pager would.\n\n"
            "3. COMPANY / ROLE TIE-IN (3-4 sentences): Explain why this role's "
            "domain, team, and responsibilities fit your background and "
            "interests.\n\n"
            "4. CLOSE (2-3 sentences): Express enthusiasm and availability."
        )
        paragraph_count = 2 + extra_evidence + 2  # opening + evidence + tie-in + close
        paragraph_rule = (
            f"Use exactly {paragraph_count} paragraphs separated by blank lines"
        )
    return f"""You are a professional cover letter writer. Generate a compelling
cover letter body sized for a {pages}-page letter, following this EXACT structure:

{structure}

Rules:
- Total length: {min_words}-{max_words} words (aim for around {target_words}).
- {paragraph_rule}.
- Tone: confident but not arrogant, specific but not verbose.
- Do NOT use clichés like "I am writing to express my interest"
  or "I believe I would be a great fit".
- Do NOT fabricate experiences, skills, or achievements not in the provided profile.
- Do NOT include a greeting line (Dear Hiring Manager)
  or sign-off (Sincerely) -- those are added separately.
- Output ONLY the body text of the cover letter."""


# Back-compat alias for any callers that imported the constant directly.
_CL_SYSTEM = _cl_system_prompt(1)

_INVALID_LLM_OUTPUT_PATTERNS = (
    "please paste the system instructions",
    "paste the system instructions",
    "system instructions you want me to follow",
    "if you want me to inspect or modify",
    "point me to the relevant file",
    "openai codex",
    "tokens used",
    "reading additional input from stdin",
)


def _generate_with_llm(
    job: RawJob,
    profile_data: dict[str, Any],
    evidence_bullets: list[str],
    *,
    target_pages: int = 1,
    length_feedback: str | None = None,
    previous_attempt: str | None = None,
) -> str:
    """Generate cover letter body using the configured LLM.

    ``length_feedback`` is an optional natural-language steering message
    appended when the previous render's page count did not match the
    template target -- used by the iterative renderer below to ask for
    a longer or shorter draft instead of crudely chopping paragraphs.
    """
    identity = profile_data.get("identity", {})
    skills = profile_data.get("skills", {})
    system_prompt = _cl_system_prompt(target_pages)

    # Build context for the LLM
    evidence_text = "\n".join(f"- {b}" for b in evidence_bullets)

    skill_summary = []
    for category, items in skills.items():
        if isinstance(items, list) and items:
            skill_summary.append(f"{category}: {', '.join(items[:8])}")
    skills_text = "\n".join(skill_summary)

    feedback_block = ""
    if length_feedback:
        feedback_block = (
            "\n<length_feedback>\n"
            f"{length_feedback}\n"
            "Rewrite the cover letter body to match the requested length while "
            "preserving every grounded claim from the evidence above.\n"
            "</length_feedback>\n"
        )
    if previous_attempt:
        feedback_block += (
            "\n<previous_attempt>\n"
            f"{previous_attempt}\n"
            "</previous_attempt>\n"
        )

    prompt = f"""Write a cover letter body for this application.

<cover_letter_instructions>
{system_prompt}
</cover_letter_instructions>

<job>
Company: {job.company}
Role: {job.title}
Location: {job.location or "Not specified"}

Job Description:
{(job.description or "")[:3000]}
</job>

<applicant>
Name: {identity.get("full_name", "")}
Education: {_format_education_brief(profile_data.get("education", []))}

Key evidence points from my experience:
{evidence_text}

Skills:
{skills_text}
</applicant>
{feedback_block}
Generate the cover letter body following the instructions above."""

    raw = generate_text(prompt, system=system_prompt, timeout=90)
    return _clean_llm_cover_letter_output(raw, target_pages=target_pages)


def _clean_llm_cover_letter_output(raw: str, *, target_pages: int = 1) -> str:
    """Reject CLI/meta responses so they fall back to deterministic templates.

    The min/max word window scales with ``target_pages`` so the same
    validator works for 1-page and 2-page letters without lying about
    the budget.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    lower = text.lower()
    if not text:
        raise LLMError("LLM returned an empty cover letter.")
    if any(pattern in lower for pattern in _INVALID_LLM_OUTPUT_PATTERNS):
        raise LLMError("LLM returned a meta-response instead of a cover letter.")
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    min_words, _, max_words = _length_window_for(target_pages)
    # Allow a wider tolerance than the prompt target because the
    # iterative feedback loop is what will tighten the actual length.
    if len(text.split()) < int(min_words * 0.7):
        raise LLMError("LLM returned a cover letter that is too short.")
    if len(text.split()) > int(max_words * 1.5):
        raise LLMError("LLM returned a cover letter that is too long.")
    min_paragraphs = 4 if target_pages == 1 else 4 + (target_pages - 1)
    if len(paragraphs) < min_paragraphs:
        raise LLMError("LLM returned a cover letter without enough paragraph structure.")
    return text


def _generate_template(
    job: RawJob,
    identity: dict[str, Any],
    evidence_bullets: list[str],
) -> str:
    """Generate a deterministic fallback body when the LLM is unavailable.

    This is intentionally more than a bare placeholder: users see this
    when the LLM returns a bad/short answer, so it must still read like
    a complete professional cover-letter body. The DOCX/PDF renderer
    adds the salutation and sign-off around these paragraphs.
    """
    del identity  # fallback body stays first-person; renderer owns the signature.
    focus = _job_focus_phrase(job)
    role_label = _role_label(job)
    opening = (
        f"I am excited to apply for the {job.title} position at {job.company} because "
        f"the role centers on {focus}. I am looking for a co-op environment where I "
        f"can contribute to production software, learn from experienced engineers, "
        f"and take ownership of implementation details that affect reliability and user experience."
    )

    evidence_parts = [_clean_evidence_sentence(bullet) for bullet in evidence_bullets[:3]]
    if not evidence_parts:
        evidence_parts = [
            "My project work has required me to break ambiguous requirements into testable "
            "software components.",
            "I have built and debugged full-stack systems where backend reliability and "
            "frontend usability both mattered.",
            "I have practiced communicating tradeoffs clearly while continuing to learn new "
            "tools quickly.",
        ]

    first_evidence = evidence_parts[0]
    second_evidence = evidence_parts[1] if len(evidence_parts) > 1 else evidence_parts[0]
    third_evidence = evidence_parts[2] if len(evidence_parts) > 2 else evidence_parts[-1]
    evidence_one = (
        f"A strong part of my preparation for this role is hands-on engineering work. "
        f"{first_evidence} That experience is relevant to {role_label} because it required "
        f"careful API design, debugging discipline, and attention to maintainability rather "
        f"than simply making features work once."
    )

    evidence_two = (
        f"I have also worked across system boundaries where correctness depends on how "
        f"services, data, and user workflows fit together. {second_evidence} {third_evidence} "
        f"Those projects strengthened my ability to trace problems end to end, document "
        f"technical decisions, and keep implementation choices grounded in user needs."
    )

    company_tie = (
        "The responsibilities in this posting suggest a team that values practical "
        "software development, testing, and collaboration. I would bring a careful "
        "engineering mindset, willingness to ask precise questions, and enough full-stack "
        "context to contribute while continuing to grow. I am especially interested in "
        "work where quality, security, and long-term maintainability matter, because those "
        "constraints reward engineers who test assumptions and communicate clearly."
    )

    close = (
        f"I would welcome the opportunity to discuss how my background can support "
        f"{job.company}'s work in this position. Thank you for your time and consideration."
    )

    return f"{opening}\n\n{evidence_one}\n\n{evidence_two}\n\n{company_tie}\n\n{close}"


def _clean_evidence_sentence(text: str) -> str:
    cleaned = " ".join((text or "").strip().strip("•-– ").split())
    if not cleaned:
        return ""
    at_match = re.match(r"^At\s+([^,]+),\s+(.+)$", cleaned)
    if at_match:
        entity = at_match.group(1).strip()
        body = _lowercase_initial(at_match.group(2).strip())
        if body.lower().startswith("i "):
            cleaned = f"In my work with {entity}, {body}"
        else:
            cleaned = f"In my work with {entity}, I {body}"
    if not cleaned.endswith(('.', '!', '?')):
        cleaned += "."
    return cleaned


def _lowercase_initial(text: str) -> str:
    if not text:
        return text
    return text[0].lower() + text[1:]


def _job_focus_phrase(job: RawJob) -> str:
    requirements = getattr(job, "requirements", None)
    terms: list[str] = []
    for attr in ("must_have_skills", "preferred_skills", "keywords"):
        values = getattr(requirements, attr, []) if requirements is not None else []
        terms.extend(str(value) for value in values if value)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = term.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(term.strip())
        if len(deduped) >= 4:
            break
    if deduped:
        return ", ".join(deduped[:-1]) + f", and {deduped[-1]}" if len(deduped) > 1 else deduped[0]
    return "software development, testing, and practical problem solving"


def _role_label(job: RawJob) -> str:
    title = (job.title or "this role").strip()
    return title if title.lower().startswith("the ") else f"the {title} role"


def _select_evidence(
    job: RawJob,
    profile_data: dict[str, Any],
    max_points: int = 3,
) -> list[str]:
    """Select the strongest evidence points from profile for this job.

    Picks bullets with highest tag overlap with JD requirements.
    """
    from src.generation.resume_builder import extract_jd_tags

    jd_tags = extract_jd_tags(job)
    evidence = select_relevant_evidence(jd_tags, profile_data, max_total=max_points)
    return [
        f"At {item.source_entity}, {item.text}" if item.source_entity else item.text
        for item in evidence[:max_points]
    ]


def _cover_paragraphs_from_text(
    body_text: str, evidence_bullets: list[str]
) -> list[CoverLetterParagraph]:
    raw_paragraphs = [part.strip() for part in body_text.split("\n\n") if part.strip()]
    if not raw_paragraphs:
        return []

    paragraph_types = ["opening", "experience_evidence", "experience_evidence", "company_fit"]
    paragraphs: list[CoverLetterParagraph] = []
    for index, text in enumerate(raw_paragraphs):
        paragraph_type = paragraph_types[index] if index < len(paragraph_types) else "closing"
        source_ids = []
        if paragraph_type == "experience_evidence":
            source_ids = [str(i) for i, evidence in enumerate(evidence_bullets) if evidence in text]
        paragraphs.append(
            CoverLetterParagraph(
                type=paragraph_type,  # type: ignore[arg-type]
                text=text,
                source_ids=source_ids,
            )
        )
    if paragraphs:
        paragraphs[-1].type = "closing"
    return paragraphs


def _format_education_brief(education: list[dict]) -> str:
    """One-line education summary for LLM context."""
    parts = []
    for edu in education:
        if isinstance(edu, dict):
            degree = edu.get("degree", "")
            field = edu.get("field", "")
            institution = edu.get("institution", "")
            if degree or institution:
                parts.append(f"{degree} in {field}, {institution}".strip(", "))
    return "; ".join(parts) if parts else "Not specified"
