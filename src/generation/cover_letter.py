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

import json
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
from src.intake.html_utils import strip_html
from src.intake.schema import RawJob
from src.utils.llm import LLMError, generate_text

logger = logging.getLogger("autoapply.generation.cover_letter")

DEFAULT_OUTPUT_DIR = Path("data/output")

# How many LLM re-asks to take before falling back to a hard paragraph
# trim. Each round costs one LLM call (~30-60s), so the cap directly
# determines the worst-case generation latency. 1 retry keeps total
# wall time within the front-end's poll budget while still giving the
# LLM a second shot when the first draft missed the page target.
_MAX_LLM_LENGTH_REGENS = 0
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
    strategy: dict[str, Any] | None = None,
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
                    strategy=strategy,
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
            f"Cut about {delta} page worth of prose, tighten sentences, "
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
        "fabricate experiences, only elaborate on what is grounded in "
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

    strategy = _infer_cover_letter_strategy(job, evidence_bullets)

    text_from_llm = False
    letter_origin = "deterministic_baseline"
    origin_issues: list[str] = []
    if use_llm:
        try:
            text, letter_origin, origin_issues = _generate_high_quality_cover_letter_text(
                job,
                profile_data,
                evidence_bullets,
                target_pages=target_pages,
                strategy=strategy,
            )
            text_from_llm = letter_origin == "llm"
        except Exception as e:  # noqa: BLE001 -- surface quality failures explicitly
            if _cover_letter_fail_closed():
                logger.warning("LLM cover letter generation failed quality checks: %s", e)
                raise LLMError(
                    "Cover letter failed quality/grounding checks; no generic "
                    f"fallback was written. {e}"
                ) from e
            logger.warning("LLM cover letter generation failed (%s), using template", e)
            text = _generate_template(
                job, identity, evidence_bullets, profile_data, strategy=strategy
            )
    else:
        text = _generate_template(
            job, identity, evidence_bullets, profile_data, strategy=strategy
        )

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
        strategy=strategy,
        quality_issues=[] if text_from_llm else None,
        letter_origin=letter_origin,
        origin_issues=origin_issues,
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
        strategy=strategy,
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


def _generate_high_quality_cover_letter_text(
    job: RawJob,
    profile_data: dict[str, Any],
    evidence_bullets: list[str],
    *,
    target_pages: int,
    strategy: dict[str, Any],
    max_attempts: int = 2,
) -> tuple[str, str, list[str]]:
    """Generate at most two drafts and return the best usable version.

    Returns ``(text, origin, issues)`` where ``origin`` is ``"llm"`` or
    ``"deterministic_baseline"``. The origin MUST be propagated to the
    document metadata / review card: a baseline letter is a factual but
    templated draft that the operator should rewrite before approving,
    and shipping it unlabeled is how two template letters went to the
    review queue unnoticed on 2026-07-15.

    Empty/meta output still fails closed because it is not a letter. Structural,
    style, or grounding warnings make a candidate score worse and steer the
    second attempt, but no longer destroy every artifact. The user reviews the
    surfaced draft before submission.
    """
    feedback: str | None = None
    previous: str | None = None
    last_error: Exception | None = None
    baseline = _generate_template(
        job,
        profile_data.get("identity", {}),
        evidence_bullets,
        profile_data,
        strategy=strategy,
    )
    # A factual deterministic letter is always available. The LLM must beat
    # this small baseline penalty without grounding/style warnings.
    candidates: list[tuple[int, str, list[str], str]] = [
        (45, baseline, ["deterministic evidence baseline"], "deterministic_baseline")
    ]

    for attempt in range(max(1, max_attempts)):
        try:
            text = _generate_with_llm(
                job,
                profile_data,
                evidence_bullets,
                target_pages=target_pages,
                length_feedback=feedback,
                previous_attempt=previous,
                strategy=strategy,
                allow_quality_warnings=True,
            )
        except LLMError as exc:
            last_error = exc
            feedback = f"Attempt {attempt + 1} was unusable: {exc}. Return only a letter body."
            previous = None
            continue

        score, issues = _score_cover_letter_candidate(
            text,
            job=job,
            profile_data=profile_data,
            evidence_bullets=evidence_bullets,
            target_pages=target_pages,
        )
        candidates.append((score, text, issues, "llm"))
        if not issues:
            return text, "llm", []
        feedback = (
            f"Attempt {attempt + 1} had these problems: {'; '.join(issues)}. "
            "Repair only those problems. Never move a detail from the job description "
            "into the applicant's history."
        )
        previous = text

    if candidates:
        candidates.sort(key=lambda candidate: candidate[0])
        best_score, best_text, best_issues, best_origin = candidates[0]
        logger.warning(
            "Cover letter using best available draft (score=%d, origin=%s): %s",
            best_score,
            best_origin,
            best_issues,
        )
        return best_text, best_origin, best_issues
    raise LLMError(f"Cover letter produced no usable draft: {last_error}") from last_error


def _score_cover_letter_candidate(
    text: str,
    *,
    job: RawJob,
    profile_data: dict[str, Any],
    evidence_bullets: list[str],
    target_pages: int,
) -> tuple[int, list[str]]:
    """Lower is better. Warnings guide repair but do not suppress artifacts."""
    from src.generation.fact_drift import check_fact_drift

    issues: list[str] = []
    score = 0
    words = len(text.split())
    paragraphs = [part for part in text.split("\n\n") if part.strip()]
    min_words, _, max_words = _length_window_for(target_pages)
    if words < int(min_words * 0.7):
        issues.append(f"too short ({words} words)")
        score += 20
    if words > int(max_words * 1.5):
        issues.append(f"too long ({words} words)")
        score += 20
    if len(paragraphs) < 4:
        issues.append(f"only {len(paragraphs)} paragraphs")
        score += 15
    quality = _cover_letter_quality_issues(text, job_title=job.title)
    issues.extend(quality)
    score += 10 * len(quality)
    drift = check_fact_drift(
        text,
        evidence_texts=evidence_bullets,
        jd_snapshot_text=f"{job.company}\n{job.title}\n{job.description or ''}",
        profile_text=json.dumps(profile_data, ensure_ascii=False, default=str),
    )
    if drift.number_drift:
        issues.append("unsupported numbers: " + ", ".join(drift.number_drift))
        score += 100 * len(drift.number_drift)
    if drift.entity_drift:
        issues.append("unsupported entities: " + ", ".join(drift.entity_drift))
        score += 25 * len(drift.entity_drift)
    unsupported_claims = _unsupported_applicant_claims(text, evidence_bullets)
    if unsupported_claims:
        issues.extend(f"unsupported claim: {claim}" for claim in unsupported_claims)
        # Plausible connective detail is acceptable because the profile is not
        # exhaustive. Prefer the cleaner draft, but reserve severe penalties
        # for invented numbers/entities above.
        score += 10 * len(unsupported_claims)
    return score, issues


_PAST_CLAIM_RE = re.compile(
    r"\b(i|we|who)\s+(build|built|create|created|design|designed|develop|developed|"
    r"implement|implemented|lead|led|run|ran|manage|managed|"
    r"maintained|rewrote|transformed|helped|worked|directed|evaluated|identified|"
    r"outlined|diagnosed|lifted|reduced|increased|improved|contributed|presented|"
    r"operated|deployed|analyzed|validated|fixed|cut)\b|"
    r"\b(this|that|these|the guides?|the work|the system|this approach|this involved)\s+"
    r"(eliminated|prevented|became|kept|enabled|saved|ensured|reduced|increased|"
    r"improved|lifted|cut|drove|contributed|standardized)\b",
    re.IGNORECASE,
)
_CLAIM_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "i", "in", "into", "it",
    "my", "of", "on", "or", "our", "that", "the", "their", "this", "to", "we",
    "with", "while", "was", "were", "is", "are",
}


def _unsupported_applicant_claims(text: str, evidence_bullets: list[str]) -> list[str]:
    """Flag past-tense applicant claims with weak evidence-token overlap."""
    evidence_tokens = [_claim_tokens(item) for item in evidence_bullets if item]
    unsupported: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence = sentence.strip()
        if not sentence or not _PAST_CLAIM_RE.search(sentence):
            continue
        tokens = _claim_tokens(sentence)
        if not tokens:
            continue
        overlap = max(
            (len(tokens & source) / max(1, len(tokens)) for source in evidence_tokens),
            default=0.0,
        )
        if overlap < 0.20:
            unsupported.append(sentence[:140])
    return unsupported


def _claim_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2 and token not in _CLAIM_STOPWORDS
    }


def _cover_letter_fail_closed() -> bool:
    from src.core.config import load_config

    generation = load_config().get("generation", {}) or {}
    return bool(generation.get("cover_letter_fail_closed", True))


def _assert_cover_letter_grounded(
    text: str,
    *,
    job: RawJob,
    profile_data: dict[str, Any],
    evidence_bullets: list[str],
) -> None:
    """Reject invented numbers/entities on the active generation path."""
    from src.generation.fact_drift import check_fact_drift

    report = check_fact_drift(
        text,
        evidence_texts=evidence_bullets,
        jd_snapshot_text=f"{job.company}\n{job.title}\n{job.description or ''}",
        profile_text=json.dumps(profile_data, ensure_ascii=False, default=str),
    )
    if report.has_blocking_drift:
        details = []
        if report.number_drift:
            details.append("unsupported numbers: " + ", ".join(report.number_drift))
        if report.entity_drift:
            details.append("unsupported entities: " + ", ".join(report.entity_drift))
        raise LLMError("Cover letter fact drift detected (" + "; ".join(details) + ")")


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

    strategy = _infer_cover_letter_strategy(job, evidence_bullets)

    text_from_llm = False
    if use_llm:
        try:
            text = _generate_with_llm(
                job,
                profile_data,
                evidence_bullets,
                target_pages=target_pages,
                strategy=strategy,
            )
            text_from_llm = True
        except (LLMError, Exception) as exc:
            logger.warning("LLM cover letter generation failed (%s), using template", exc)
            text = _generate_template(
                job, identity, evidence_bullets, profile_data, strategy=strategy
            )
    else:
        text = _generate_template(
            job, identity, evidence_bullets, profile_data, strategy=strategy
        )

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
        strategy=strategy,
        quality_issues=[] if text_from_llm else None,
        letter_origin="llm" if text_from_llm else "deterministic_baseline",
        origin_issues=[],
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
    strategy: dict[str, Any] | None = None,
    quality_issues: list[str] | None = None,
    letter_origin: str = "llm",
    origin_issues: list[str] | None = None,
) -> CoverLetterDocument:
    """Create a structured cover letter IR from generated body text.

    ``letter_origin`` records whether the body came from the LLM or the
    deterministic evidence template; it flows into the version ledger and
    review card so a templated draft is never mistaken for a tailored one.
    """
    identity = profile_data.get("identity", {})
    if strategy is None:
        strategy = _infer_cover_letter_strategy(job, evidence_bullets)
    if quality_issues is None:
        quality_issues = _cover_letter_quality_issues(body_text, job_title=job.title)
    return CoverLetterDocument(
        recipient={
            "company": job.company,
            "hiring_manager": None,
            "location": _display_location(job.location),
        },
        applicant={
            "name": identity.get("full_name", ""),
            "email": identity.get("email", ""),
            "phone": identity.get("phone", ""),
            "location": identity.get("location", ""),
        },
        paragraphs=_cover_paragraphs_from_text(body_text, evidence_bullets),
        metadata={
            "target_role": job.title,
            "company": job.company,
            "role_type": strategy["role_type"],
            "capability_buckets": [
                bucket["name"] for bucket in strategy["capability_buckets"]
            ],
            "quality_issues": quality_issues,
            "letter_origin": letter_origin,
            "origin_issues": list(origin_issues or []),
            # Give the validator real bullets to compare against instead
            # of pattern-guessing what "raw evidence" looks like.
            "evidence_bullets": [b for b in evidence_bullets if b][:12],
        },
    )


_LOCATION_LOWER_WORDS = {"and", "or", "of", "the", "in", "at"}


def _display_location(location: str | None) -> str | None:
    """Render an ATS location string presentably in the letter header.

    Sources frequently normalize to lowercase ("san francisco, ca",
    "united states"); a letter header must not. Two-letter state/country
    codes are uppercased, other words title-cased.
    """
    if not location or not location.strip():
        return location
    words = []
    for raw_word in location.strip().split():
        # Preserve separators attached to the word (e.g. "ca," keeps the comma)
        core = raw_word.strip(",;")
        suffix = raw_word[len(core):]
        if len(core) == 2 and core.isalpha():
            words.append(core.upper() + suffix)
        elif core.lower() in _LOCATION_LOWER_WORDS:
            words.append(core.lower() + suffix)
        else:
            words.append(core[:1].upper() + core[1:] + suffix)
    result = " ".join(words)
    # First word always capitalized even if it's a lower-word ("The Hague")
    return result[:1].upper() + result[1:] if result else result


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
#
# 2026-07-16 recalibration: the previous window (min 220 / target 280)
# combined with the validator's min of 260 flagged 20/20 letters in the
# first real portfolio batch as "too short" — the local model reliably
# produces 190-236 word letters, and the well-written ones (CoreView,
# WalkMe) were in that band. A warning that fires 100% of the time
# carries no signal and lets genuinely bad drafts hide among good ones.
# 200-240 words is also simply a good cover-letter length; the old
# budget encouraged padding. Keep the validator (validator.py
# ``validate_cover_letter_document``) aligned with this window.
_WORDS_PER_PAGE_TARGET = 240
_WORDS_PER_PAGE_MIN = 180
_WORDS_PER_PAGE_MAX = 340


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


def _cl_system_prompt(
    target_pages: int,
    strategy: dict[str, Any] | None = None,
) -> str:
    pages = max(1, int(target_pages))
    min_words, target_words, max_words = _length_window_for(pages)
    strategy = strategy or {}
    role_type = strategy.get("role_type") or "software_development"
    capability_block = _format_capability_buckets_for_prompt(
        strategy.get("capability_buckets") or []
    )
    if pages == 1:
        structure = (
            "1. OPEN (2-3 sentences): Who the applicant is in ONE plain sentence, "
            "then connect their background to the single most important thing the "
            "job description says this person will DO (the top item under 'what "
            "you'll do' or equivalent). Never open with enthusiasm boilerplate or "
            "an identity that doesn't match the job title.\n\n"
            "2-3. TWO PROOF PARAGRAPHS (3-4 sentences each): Each one picks a "
            "different top task/pain point from the job description and tells ONE "
            "specific thing the applicant actually did about that kind of problem, "
            "with the outcome. Tell it plainly, like relaying it to a colleague: "
            "'At SDS, I redesigned onboarding and cut time-to-value about 30%.' "
            "Not a thesis. Not 'This experience demonstrates my ability to...'\n\n"
            "4. CLOSE (2-3 sentences): One specific observation about the role or "
            "what the company is building, drawn from the job description (real "
            "specificity, not flattery), then a simple direct close."
        )
        paragraph_rule = "Use exactly 4 paragraphs separated by blank lines"
    else:
        extra_evidence = max(1, pages - 1)
        paragraph_count = 2 + extra_evidence + 2
        structure = (
            "Same philosophy, more room: open plainly, then "
            f"{2 + extra_evidence} proof paragraphs (each = one of THEIR tasks + "
            "one specific thing the applicant did about it + outcome), then a "
            "role-specific close."
        )
        paragraph_rule = (
            f"Use exactly {paragraph_count} paragraphs separated by blank lines"
        )
    return f"""/no_think
You write cover letters that sound like a capable person talking,
not like an AI or a formal essay. A good cover letter is a clear, honest note
from the applicant to the hiring manager: here's what you need done, here's
proof I've done that kind of thing. Sized for a {pages}-page letter.

Primary objective:
Connect the applicant's real experience to THIS job's top 2-3 tasks. Do not
summarize the resume. Do not build an abstract argument.

Role type: {role_type} — the applicant must sound like someone applying for
THIS kind of role, in this role's vocabulary (use the job description's own
words for tools and tasks, e.g. if they say "CRM", say "CRM").

Capability buckets to draw evidence from:
{capability_block}

Follow this structure:

{structure}

Voice rules (the most important part):
- Read-aloud test: every sentence must sound natural spoken to a colleague.
  If it sounds like corporate writing, say it plainer.
- Use contractions where natural ("I've", "I'd", "that's"). Include one small,
  specific observation about why the work is interesting; do not make every
  paragraph follow the same sentence rhythm.
- It is fine to sound slightly conversational. Avoid sounding over-polished,
  ceremonious, or like a generated summary of the resume.
- Plain verbs. "built", "ran", "cut", "fixed", "led" — never "spearheaded",
  "leveraged", "utilized", "orchestrated", "delved", "fostered".
- Ban these words/phrases entirely: "passionate", "excited to apply",
  "strong candidate", "valuable addition", "perfect fit", "resonates",
  "aligns with my", "robust", "comprehensive", "cutting-edge", "seamless",
  "I believe my skills", "proven track record".
- Vary sentence length. Some short. It reads human.
- Confidence without adjectives: let the numbers and specifics carry it.
- Mention the exact job title at most once; afterwards say "this role" or
  "the team".

Hard rules:
- Total length: {min_words}-{max_words} words (aim for around {target_words}).
- {paragraph_rule}.
- Do NOT fabricate experiences, skills, or achievements not in the provided
  profile/evidence/stories.
- Treat the Job Description as COMPANY CONTEXT only. A tool, customer type,
  workflow, metric, industry detail, or responsibility appearing only in the
  JD must NEVER be written as something the applicant previously did.
- Use evidence at its original level of specificity. Do not turn "implementation
  guides" into security, clinical, EHR, infrastructure, or data-pipeline work
  unless that exact domain appears in the applicant evidence.
- Plausible connective detail is allowed: you may describe a reasonable
  workflow, motivation, or lesson that makes the evidence read like a human
  story. The profile is not an exhaustive diary.
- Do NOT invent hard facts: no new numbers, credentials, employers, named tools,
  regulated-domain experience, team size, revenue, scale, or major outcome.
- Do not claim direct ownership when the evidence says "contributed" or
  "helped." Modest interpretation is fine; material promotion is not.
- In each proof paragraph, anchor the applicant-history sentence closely to ONE
  supplied evidence bullet. You may simplify its wording, but do not append an
  invented hard result. If the evidence says "contributed," do not upgrade it
  to sole ownership with "built," "designed," or "led."
- Do NOT include a greeting line (Dear Hiring Manager)
  or sign-off (Sincerely), those are added separately.
- Do NOT use em dashes or en dashes. Prefer commas, periods, or semicolons.
- Do NOT list more than 3 technologies in one sentence.

Inline formatting (use sparingly, OPTIONAL):
- ``**text**`` -- bold for a small number of named technologies or
  quantified outcomes ("**1.5M+ requests/day**", "**FastAPI**") so the
  reader's eye lands on the strongest evidence. Aim for at most 2-3
  bolded spans per letter; one per paragraph is plenty.
- ``*text*`` -- italics for proper nouns / product / paper names where
  italicisation is conventional.
- Do NOT use any other Markdown -- no headings, lists, links, code
  fences, or ``_underscores_``.

- Output ONLY the body text of the cover letter."""


def _format_capability_buckets_for_prompt(buckets: list[dict[str, Any]]) -> str:
    if not buckets:
        return "- software development and maintainability"
    lines = []
    for index, bucket in enumerate(buckets[:3], start=1):
        evidence = bucket.get("candidate_evidence") or []
        evidence_text = "; ".join(str(item) for item in evidence[:2]) or "selected profile evidence"
        signals = ", ".join(bucket.get("jd_signals") or []) or bucket.get("name", "")
        lines.append(
            f"{index}. {bucket.get('name')} | JD signals: {signals} | "
            f"candidate evidence: {evidence_text}"
        )
    return "\n".join(lines)


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

_FORBIDDEN_COVER_LETTER_PHRASES = (
    "i am passionate about",
    "i believe my skills",
    "i believe i would",
    "strong candidate",
    "valuable addition",
    "perfect fit",
)

_TECH_TERMS = (
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "next.js",
    "fastapi",
    "flask",
    "postgresql",
    "sqlite",
    "redis",
    "docker",
    "kubernetes",
    "aws",
    "nginx",
    "https",
    "cloudflare",
    "systemd",
    "kafka",
    "graphql",
    "rest",
    "api",
    "apis",
)


_TECH_TERM_RE = re.compile(
    r"(?<![\w.+-])(" + "|".join(re.escape(term) for term in _TECH_TERMS) + r")(?![\w.+-])"
)


def _infer_cover_letter_strategy(
    job: RawJob,
    evidence_bullets: list[str],
) -> dict[str, Any]:
    role_type = _classify_cover_letter_role(job)
    job_text = _job_signal_text(job).lower()
    boosts = _role_bucket_boosts(role_type)
    buckets = []
    for bucket in _CAPABILITY_BUCKETS:
        score = sum(2 for signal in bucket["signals"] if signal in job_text)
        if bucket["name"] in boosts:
            score += boosts[bucket["name"]]
        matched_evidence = _match_bucket_evidence(bucket, evidence_bullets)
        if matched_evidence:
            score += 1
        buckets.append(
            {
                **bucket,
                "score": score,
                "candidate_evidence": matched_evidence or evidence_bullets[:1],
            }
        )

    buckets.sort(key=lambda bucket: bucket["score"], reverse=True)
    selected = buckets[:3]
    if len(selected) < 3:
        selected.extend(bucket for bucket in buckets if bucket not in selected)
    return {
        "role_type": role_type,
        "capability_buckets": selected[:3],
        "quality_focus": _quality_focus_for_role(role_type),
    }


def _classify_cover_letter_role(job: RawJob) -> str:
    text = _job_signal_text(job).lower()
    title = (job.title or "").lower()
    if any(
        term in title
        for term in (
            "professional services",
            "implementation",
            "onboarding",
            "solutions consultant",
            "customer success",
            "technical account",
        )
    ):
        return "customer_implementation"
    if any(term in text for term in ("embedded", "firmware", "microcontroller", "rtos")):
        return "embedded_firmware"
    if any(term in text for term in ("test", "testing", "verification", "qa", "quality")):
        return "software_development_test"
    if re.search(r"\b(ai|agent|automation|workflow)\b", text):
        return "ai_automation"
    if any(term in text for term in ("security", "defense", "mission", "secure")):
        return "security_mission_systems"
    if any(term in text for term in ("backend", "api", "database", "service")):
        return "backend"
    return "software_development"


def _job_signal_text(job: RawJob) -> str:
    requirements = getattr(job, "requirements", None)
    parts = [job.title or "", job.company or "", job.description or ""]
    if requirements is not None:
        for attr in (
            "must_have_skills",
            "preferred_skills",
            "responsibilities",
            "soft_skills",
            "keywords",
        ):
            values = getattr(requirements, attr, []) or []
            parts.extend(str(value) for value in values if value)
        parts.extend(
            str(value)
            for value in (
                requirements.domain,
                requirements.role_family,
                requirements.seniority,
            )
            if value
        )
    return "\n".join(parts)


_CAPABILITY_BUCKETS: list[dict[str, Any]] = [
        {
            "name": "software development and maintainability",
            "signals": [
                "software development",
                "implementation",
                "maintainable",
                "api",
                "backend",
                "data model",
            ],
            "evidence_terms": ["api", "backend", "data", "model", "validation"],
            "claim": "building maintainable software systems",
            "relevance": (
                "it depends on clear interfaces, validation, and implementation "
                "choices that remain understandable after the first version works"
            ),
        },
        {
            "name": "testing, debugging, and reliability",
            "signals": [
                "test",
                "testing",
                "debug",
                "verification",
                "reliability",
                "quality",
                "edge case",
            ],
            "evidence_terms": ["test", "debug", "reliab", "edge", "coverage", "trace"],
            "claim": "testing, debugging, and reliability-focused implementation",
            "relevance": (
                "the work rewards engineers who verify assumptions, trace failures "
                "carefully, and treat correctness as part of implementation"
            ),
        },
        {
            "name": "systems integration and collaboration",
            "signals": [
                "integration",
                "collaboration",
                "team",
                "service",
                "workflow",
                "cross-functional",
            ],
            "evidence_terms": [
                "integrat",
                "service",
                "workflow",
                "frontend",
                "backend",
                "team",
            ],
            "claim": "working across system boundaries",
            "relevance": (
                "many engineering failures appear between components, so integration "
                "work requires communication, careful debugging, and attention to "
                "end-to-end behavior"
            ),
        },
        {
            "name": "security, correctness, and controlled change",
            "signals": [
                "security",
                "secure",
                "defense",
                "mission",
                "correctness",
                "documentation",
                "risk",
            ],
            "evidence_terms": ["security", "auth", "document", "reliab", "correct", "risk"],
            "claim": "reliability and careful engineering judgment",
            "relevance": (
                "secure or mission-sensitive systems need controlled changes, "
                "documentation, and a habit of checking assumptions before relying "
                "on software behavior"
            ),
        },
        {
            "name": "workflow automation and data quality",
            "signals": ["automation", "ai", "agent", "workflow", "data quality", "review"],
            "evidence_terms": ["automation", "agent", "workflow", "data", "review"],
            "claim": "designing automation around data quality and review boundaries",
            "relevance": (
                "useful automation depends on reliable inputs, explicit failure "
                "handling, and human review where software should not overreach"
            ),
        },
        {
            "name": "embedded interfaces and low-level debugging",
            "signals": ["embedded", "firmware", "hardware", "interface", "real-time", "rtos"],
            "evidence_terms": ["embedded", "hardware", "interface", "control", "system"],
            "claim": "reasoning about software at interface boundaries",
            "relevance": (
                "embedded work requires careful debugging where software behavior is "
                "constrained by hardware, timing, and resource limits"
            ),
        },
    ]


_ROLE_BUCKET_BOOSTS_DEFAULT: dict[str, int] = {"software development and maintainability": 3}
_ROLE_BUCKET_BOOSTS: dict[str, dict[str, int]] = {
    "customer_implementation": {
        "systems integration and collaboration": 5,
        "workflow automation and data quality": 3,
        "software development and maintainability": 1,
    },
    "software_development_test": {
        "testing, debugging, and reliability": 5,
        "software development and maintainability": 3,
        "systems integration and collaboration": 2,
    },
    "backend": {
        "software development and maintainability": 5,
        "systems integration and collaboration": 3,
        "testing, debugging, and reliability": 2,
    },
    "security_mission_systems": {
        "security, correctness, and controlled change": 5,
        "testing, debugging, and reliability": 3,
        "software development and maintainability": 2,
    },
    "ai_automation": {
        "workflow automation and data quality": 5,
        "testing, debugging, and reliability": 2,
        "systems integration and collaboration": 2,
    },
    "embedded_firmware": {
        "embedded interfaces and low-level debugging": 5,
        "testing, debugging, and reliability": 3,
        "software development and maintainability": 1,
    },
}


def _role_bucket_boosts(role_type: str) -> dict[str, int]:
    return _ROLE_BUCKET_BOOSTS.get(role_type, _ROLE_BUCKET_BOOSTS_DEFAULT)


def _match_bucket_evidence(bucket: dict[str, Any], evidence_bullets: list[str]) -> list[str]:
    terms = [str(term).lower() for term in bucket.get("evidence_terms", [])]
    matches = [
        evidence
        for evidence in evidence_bullets
        if any(term in evidence.lower() for term in terms)
    ]
    return matches[:2]


def _quality_focus_for_role(role_type: str) -> list[str]:
    focus = {
        "customer_implementation": [
            "customer discovery",
            "hands-on implementation",
            "workflow design",
            "adoption",
        ],
        "software_development_test": [
            "testability",
            "verification",
            "edge cases",
            "debugging discipline",
            "reliability",
        ],
        "backend": ["API design", "data modeling", "service boundaries", "maintainability"],
        "security_mission_systems": [
            "reliability",
            "documentation",
            "controlled change",
            "correctness",
            "secure systems",
        ],
        "ai_automation": [
            "workflow design",
            "data quality",
            "human review",
            "error handling",
        ],
        "embedded_firmware": [
            "hardware/software boundaries",
            "low-level debugging",
            "interfaces",
            "resource constraints",
        ],
    }
    return focus.get(role_type, ["maintainability", "debugging", "collaboration"])


def _format_cover_strategy_for_prompt(strategy: dict[str, Any]) -> str:
    focus = ", ".join(strategy.get("quality_focus") or [])
    return f"Quality focus: {focus}"


def _format_role_references_for_prompt(role_refs: dict[str, str]) -> str:
    return (
        f"opening={role_refs['opening']}; body={role_refs['body']}; "
        f"context={role_refs['context']}; closing={role_refs['closing']}"
    )


_CRITIQUE_SYSTEM_PROMPT = """/no_think
You are a strict editor reviewing a cover letter draft \
against a checklist. Return ONLY JSON: {"pass": bool, "problems": ["..."]}.

A draft PASSES only if ALL of the following hold:
(a) It references the company's actual product, mission, or domain -- not just the
    company name dropped into a generic sentence.
(b) It includes at least one concrete, specific accomplishment with a stated outcome
    (a number, a result, a concrete deliverable) -- not just a listed responsibility.
(c) It contains no filler sentences that could appear in any cover letter for any
    company (e.g. "I am excited to apply...", "I believe my skills...", and similar
    boilerplate).
(d) It makes no claims that are absent from the evidence/stories provided with the draft.
(e) The letter's self-description matches the ROLE TYPE in the job title. An
    application for a consultant / solutions / presales / account / customer-success
    title must NOT describe the applicant "as an engineer" or center engineering
    infrastructure work; an engineering title must not frame them as a salesperson.
    The opening sentence especially must read as the role being applied for.

If any check fails, set "pass": false and list each failing check as a short, specific
problem in "problems" (e.g. "opening never mentions what the company builds", "no
accomplishment has a stated outcome", "letter describes an engineer but the title is
Solutions Consultant"). If all checks pass, return
{"pass": true, "problems": []}."""


def _cover_letter_critique_enabled() -> bool:
    try:
        from src.core.config import load_config  # noqa: PLC0415

        raw = load_config().get("generation", {})
    except Exception:  # noqa: BLE001 -- config trouble must not disable the enhancement path oddly
        return True
    if not isinstance(raw, dict):
        return True
    return bool(raw.get("cover_letter_critique", True))


def _critique_cover_letter(draft: str, *, job: RawJob) -> list[str] | None:
    """Run the self-critique checklist against ``draft``.

    Returns a non-empty list of problems when the draft fails the
    checklist, or ``None`` when it passes or the critique call itself
    fails -- critique is an enhancement, never a blocker.
    """
    try:
        from src.utils.llm import generate_json  # noqa: PLC0415

        result = generate_json(
            "<draft_cover_letter>\n"
            f"{draft}\n"
            "</draft_cover_letter>\n\n"
            f"<job_title>{job.title}</job_title>\n"
            "<job_description>\n"
            f"{(job.description or '')[:2000]}\n"
            "</job_description>",
            system=_CRITIQUE_SYSTEM_PROMPT,
            timeout=120,
            tier="small",
        )
    except Exception:  # noqa: BLE001 -- critique is an enhancement, never a blocker
        logger.debug("Cover letter critique call failed", exc_info=True)
        return None

    if not isinstance(result, dict) or result.get("pass"):
        return None
    problems = result.get("problems")
    if not isinstance(problems, list):
        return None
    cleaned = [str(p).strip() for p in problems if str(p).strip()]
    return cleaned or None


def _apply_critique_revision(
    draft: str,
    *,
    job: RawJob,
    prompt: str,
    system_prompt: str,
) -> str:
    """One critique -> revise cycle. Returns ``draft`` unchanged unless
    the critique found problems AND the revision call succeeds."""
    problems = _critique_cover_letter(draft, job=job)
    if not problems:
        return draft

    critique_block = (
        "\n<critique>\n"
        "Your previous draft below has the following specific problems. "
        "Rewrite it to fix ONLY these problems -- keep every grounded "
        "claim and roughly the same length as the previous draft. Do not "
        "introduce new problems.\n\n"
        "Problems:\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\n\nPrevious draft:\n"
        + draft
        + "\n</critique>\n\nOutput ONLY the revised cover letter body."
    )
    try:
        from src.utils.llm import generate_text as _generate_text_revision  # noqa: PLC0415

        revised = _generate_text_revision(f"{prompt}\n{critique_block}", system=system_prompt)
    except Exception:  # noqa: BLE001 -- revision is an enhancement, never a blocker
        logger.debug("Cover letter critique revision call failed", exc_info=True)
        return draft
    revised = (revised or "").strip()
    return revised or draft


def _generate_with_llm(
    job: RawJob,
    profile_data: dict[str, Any],
    evidence_bullets: list[str],
    *,
    target_pages: int = 1,
    length_feedback: str | None = None,
    previous_attempt: str | None = None,
    strategy: dict[str, Any] | None = None,
    allow_quality_warnings: bool = False,
) -> str:
    """Generate cover letter body using the configured LLM.

    ``length_feedback`` is an optional natural-language steering message
    appended when the previous render's page count did not match the
    template target -- used by the iterative renderer below to ask for
    a longer or shorter draft instead of crudely chopping paragraphs.
    """
    identity = profile_data.get("identity", {})
    skills = profile_data.get("skills", {})
    if strategy is None:
        strategy = _infer_cover_letter_strategy(job, evidence_bullets)
    system_prompt = _cl_system_prompt(target_pages, strategy)
    role_refs = _role_references(job, strategy)

    # Build context for the LLM
    evidence_text = "\n".join(f"- {b}" for b in evidence_bullets)

    skill_summary = []
    for category, items in skills.items():
        if isinstance(items, list) and items:
            str_items = [x for x in items if isinstance(x, str)]
            if str_items:
                skill_summary.append(f"{category}: {', '.join(str_items[:8])}")
    skills_text = "\n".join(skill_summary)

    # 2026-07-08: the letter generator never saw the profile's story
    # bank — its richest material (situation → action → result stories
    # with real outcomes). Rank stories against THIS JD and hand the
    # top two to the prompt with instructions to concretely weave ONE
    # in. Letters built only from bullet fragments read like skill
    # lists; a story is what makes one memorable.
    stories_text = ""
    story_bank = profile_data.get("story_bank") or []
    if story_bank:
        try:
            from src.generation.prep_pack import rank_stories  # noqa: PLC0415

            top_story = rank_stories(
                story_bank, title=job.title, description=job.description or ""
            )[:1]
            if top_story:
                story, _score = top_story[0]
                stories_text = (
                    "\nOptional story context (use naturally; reasonable connective "
                    "detail is allowed, but do not invent hard facts):\n"
                    f"- Situation: {story.get('context', '')} "
                    f"Action: {story.get('action', '')} "
                    f"Result: {story.get('result', '')}\n"
                )
        except Exception:  # noqa: BLE001 -- evidence bullets remain sufficient
            logger.debug("story ranking for cover letter skipped", exc_info=True)

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

CRITICAL ROLE FRAMING: the applicant is applying to be a "{job.title}".
Describe them in terms of THAT role's work (for consultant/presales/account
roles: customer discovery, demos, translating between technical and business
stakeholders — NOT "as an engineer"). Only frame them as an engineer when
the job title itself is an engineering title. Never invent a team name;
refer to the team only by the job title or "your team".

<cover_letter_instructions>
{system_prompt}
</cover_letter_instructions>

<job>
Company: {job.company}
Role: {job.title}
Natural role references: {_format_role_references_for_prompt(role_refs)}
Location: {job.location or "Not specified"}

Job Description:
{(job.description or "")[:3000]}
</job>

<applicant>
Name: {identity.get("full_name", "")}
Education: {_format_education_brief(profile_data.get("education", []))}

Role strategy:
{_format_cover_strategy_for_prompt(strategy)}

Key evidence points from my experience:
{evidence_text}
{stories_text}
Skills:
{skills_text}
</applicant>
{feedback_block}
Generate the cover letter body following the instructions above."""

    # No hardcoded timeout: fall through to the configured ``llm.timeout``
    # (300s). The previous ``timeout=90`` was shorter than a local model's
    # cold-load + long-prose generation, so nearly every cover letter
    # silently fell back to the generic template.
    raw = generate_text(prompt, system=system_prompt)

    # Self-critique -> revise, once. Skipped for the iterative page-fit
    # renderer's re-calls (``previous_attempt``/``length_feedback`` set)
    # -- those already re-ask the LLM for a reason unrelated to quality,
    # and critiquing every iteration would multiply LLM calls.
    if (
        previous_attempt is None
        and length_feedback is None
        and not allow_quality_warnings
        and _cover_letter_critique_enabled()
    ):
        raw = _apply_critique_revision(
            raw, job=job, prompt=prompt, system_prompt=system_prompt
        )

    return _clean_llm_cover_letter_output(
        raw,
        target_pages=target_pages,
        job_title=job.title,
        allow_quality_warnings=allow_quality_warnings,
    )


def _clean_llm_cover_letter_output(
    raw: str,
    *,
    target_pages: int = 1,
    job_title: str | None = None,
    allow_quality_warnings: bool = False,
) -> str:
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
    if _contains_html_markup(text):
        raise LLMError("LLM returned raw HTML markup instead of cover-letter prose.")
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    min_words, _, max_words = _length_window_for(target_pages)
    # Allow a wider tolerance than the prompt target because the
    # iterative feedback loop is what will tighten the actual length.
    if not allow_quality_warnings and len(text.split()) < int(min_words * 0.7):
        raise LLMError("LLM returned a cover letter that is too short.")
    if not allow_quality_warnings and len(text.split()) > int(max_words * 1.5):
        raise LLMError("LLM returned a cover letter that is too long.")
    min_paragraphs = 4 if target_pages == 1 else 4 + (target_pages - 1)
    if not allow_quality_warnings and len(paragraphs) < min_paragraphs:
        raise LLMError("LLM returned a cover letter without enough paragraph structure.")
    text = _normalize_cover_letter_dashes(text)
    quality_issues = _cover_letter_quality_issues(text, job_title=job_title)
    if quality_issues and not allow_quality_warnings:
        raise LLMError(
            "LLM returned a cover letter that failed quality checks: "
            + "; ".join(quality_issues)
        )
    return text


def _normalize_cover_letter_dashes(text: str) -> str:
    """Remove dash punctuation that makes cover letters read AI-generated."""
    cleaned = re.sub(r"\s*[—–]\s*", ", ", text)
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    return re.sub(r" {2,}", " ", cleaned).strip()


def _cover_letter_quality_issues(text: str, *, job_title: str | None = None) -> list[str]:
    issues: list[str] = []
    lower = text.lower()
    if _contains_html_markup(text):
        issues.append("raw_html_markup")
    for phrase in _FORBIDDEN_COVER_LETTER_PHRASES:
        if phrase in lower:
            issues.append(f"generic_phrase:{phrase}")
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        terms = _technology_terms_in_sentence(sentence)
        if len(terms) > 4:
            issues.append(f"technology_dumping:{', '.join(sorted(terms))}")
    title_repetitions = _raw_role_title_repetitions(text, job_title)
    if title_repetitions > 1:
        issues.append(f"repeated_raw_job_title:{title_repetitions}")
    return issues


def _raw_role_title_repetitions(text: str, job_title: str | None) -> int:
    title = _normalize_role_title_for_repetition(job_title)
    if not title or len(title.split()) < 3:
        return 0
    normalized_text = _normalize_role_title_for_repetition(text)
    return len(re.findall(rf"(?<!\w){re.escape(title)}(?!\w)", normalized_text))


def _normalize_role_title_for_repetition(value: str | None) -> str:
    text = (value or "").lower()
    text = re.sub(r"[–—]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _technology_terms_in_sentence(sentence: str) -> set[str]:
    return set(_TECH_TERM_RE.findall(sentence.lower()))


def _generate_template(
    job: RawJob,
    identity: dict[str, Any],
    evidence_bullets: list[str],
    profile_data: dict[str, Any] | None = None,
    *,
    strategy: dict[str, Any] | None = None,
) -> str:
    """Generate a deterministic fallback body when the LLM is unavailable.

    This is intentionally more than a bare placeholder: users see this
    when the LLM returns a bad/short answer, so it must still read like
    a complete professional cover-letter body. The DOCX/PDF renderer
    adds the salutation and sign-off around these paragraphs.
    """
    # 2026-07-09 rewrite (user report, Epic letter): the previous fallback
    # stitched hardcoded engineering "capability bucket" claims around
    # whatever evidence existed — a therapy-technician bullet was presented
    # TWICE as proof of "building maintainable software systems", with the
    # raw entity key interpolated mid-sentence, and the result shipped with
    # no flag. A fallback that runs precisely when the LLM CAN'T help must
    # UNDER-claim: say who the applicant is, quote their real bullets once
    # each, close plainly. Short and honest beats long and fabricated.
    if strategy is None:
        strategy = _infer_cover_letter_strategy(job, evidence_bullets)
    role_refs = _role_references(job, strategy)
    focus = _cover_letter_job_focus(job, strategy)

    # The focus is a verb-phrase lifted from the JD ("partner with the
    # assigned Implementation Manager…"). Splicing it bare into "stands
    # out is <clause>" produced ungrammatical letters; quote it instead
    # so any clause shape reads correctly, and never repeat it verbatim
    # in the close.
    opening = (
        f"I am applying for {role_refs['opening']} at {job.company}. "
        f'The responsibility that stands out to me is "{focus}"; the examples below are the '
        "closest evidence from my background."
    )

    seen: set[str] = set()
    proofs: list[str] = []
    for bullet in evidence_bullets:
        cleaned = _clean_evidence_sentence(bullet)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            proofs.append(cleaned)
        if len(proofs) == 3:
            break
    proof_paragraphs = proofs[:2] or ["My resume contains the relevant evidence."]

    close = (
        f"I would welcome the chance to discuss how this experience could support "
        f"that part of the role at {job.company}. "
        "Thank you for your time."
    )

    return _normalize_cover_letter_dashes(
        "\n\n".join([opening, *proof_paragraphs, close])
    )


def _cover_letter_job_focus(job: RawJob, strategy: dict[str, Any]) -> str:
    requirements = getattr(job, "requirements", None)
    responsibilities = getattr(requirements, "responsibilities", []) if requirements else []
    for responsibility in responsibilities:
        focus = _plain_cover_letter_text(str(responsibility))
        # Requirements parsers sometimes promote a branded heading such as
        # "You're a builder, not a maintainer" to a responsibility. It is not
        # a concrete duty and must never be quoted in a letter.
        if not _usable_job_focus(focus):
            continue
        words = focus.split()
        if len(words) > 22:
            focus = " ".join(words[:22]).rstrip(",;:")
        return focus[0].lower() + focus[1:]
    return _role_context_sentence(job, strategy)


_HTML_MARKUP_RE = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)


def _contains_html_markup(text: str) -> bool:
    return bool(_HTML_MARKUP_RE.search(text or ""))


def _plain_cover_letter_text(value: str) -> str:
    """Convert upstream ATS/parser fragments to safe one-line prose."""
    return re.sub(r"\s+", " ", strip_html(value or "")).strip(" .")


def _usable_job_focus(value: str) -> bool:
    words = value.split()
    if len(words) < 5:
        return False
    lower = value.lower()
    # Marketing/second-person headings are context, not a job duty.
    if re.search(r"\b(you|your|you're|you’re|we|our)\b", lower):
        return False
    return bool(
        re.search(
            r"\b(build|develop|design|implement|deliver|configure|integrate|"
            r"collaborate|manage|lead|analyze|support|drive|translate|onboard)\b",
            lower,
        )
    )


def _candidate_background(
    identity: dict[str, Any],
    profile_data: dict[str, Any] | None,
) -> str:
    headline = str(identity.get("headline") or "").strip()
    if headline:
        return headline
    for education in (profile_data or {}).get("education", []) or []:
        if not isinstance(education, dict):
            continue
        field = str(education.get("field") or "").strip()
        degree = str(education.get("degree") or "").strip()
        if field:
            if "computer" in field.lower() or "software" in field.lower():
                return f"{field} student"
            return f"student in {field}"
        if degree:
            return f"{degree} student"
    return "software engineering candidate"


def _role_context_sentence(job: RawJob, strategy: dict[str, Any]) -> str:
    role_type = strategy.get("role_type")
    company_text = (job.company or "").lower()
    job_text = _job_signal_text(job).lower()
    if role_type == "software_development_test":
        return (
            "the combination of implementation, verification, and "
            "reliability-focused engineering"
        )
    if role_type == "security_mission_systems" or any(
        term in company_text or term in job_text
        for term in ("mission", "defense", "secure", "security")
    ):
        return (
            "the unusual weight the work places on reliability, clear "
            "documentation, and careful verification"
        )
    if role_type == "ai_automation":
        return (
            "the need for automation that is useful, bounded, and reliable "
            "under real workflow constraints"
        )
    if role_type == "customer_implementation":
        return (
            "the mix of customer discovery, hands-on implementation, and "
            "making new workflows stick after launch"
        )
    if role_type == "embedded_firmware":
        return (
            "the connection between software correctness, interfaces, timing, "
            "and system-level constraints"
        )
    if role_type == "backend":
        return (
            "the emphasis on maintainable services, data flow, and debugging "
            "across system boundaries"
        )
    return (
        "the focus on practical software development, maintainability, and "
        "clear engineering judgment"
    )


def _role_references(job: RawJob, strategy: dict[str, Any]) -> dict[str, str]:
    concise = _cover_letter_role_title(job.title)
    role_family = _role_family_phrase(strategy.get("role_type"), concise)
    opening = f"the {concise} position" if concise else "this position"
    return {
        "opening": opening,
        "body": "this role",
        "alternate": "the position",
        "context": f"{role_family} work",
        "closing": f"{role_family} team",
    }


def _cover_letter_role_title(title: str) -> str:
    text = re.sub(r"\s+", " ", (title or "").strip())
    if not text:
        return "software engineering"
    parts = [part.strip() for part in re.split(r"\s+[-–—|]\s+", text) if part.strip()]
    if len(parts) > 1 and any(_looks_like_term_label(part) for part in parts[1:]):
        text = parts[0]
    text = re.sub(r"\b(20\d{2}|19\d{2})\b", "", text)
    text = re.sub(r"\b(spring|summer|fall|autumn|winter)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -/,")
    text = re.sub(r"\bIntern\s*/\s*Co-?op\b", "internship/co-op", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCo-?op\s*/\s*Intern\b", "internship/co-op", text, flags=re.IGNORECASE)
    text = re.sub(r"\bIntern\b", "internship", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCo-?op\b", "co-op", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -/,")
    return text.lower() if text else "software engineering"


def _looks_like_term_label(text: str) -> bool:
    lower = text.lower()
    return bool(re.search(r"\b(20\d{2}|19\d{2}|spring|summer|fall|autumn|winter)\b", lower))


def _role_family_phrase(role_type: str | None, concise_title: str) -> str:
    if role_type == "customer_implementation":
        return "professional services and implementation"
    if role_type == "software_development_test":
        return "software development and test"
    if role_type == "backend":
        return "backend engineering"
    if role_type == "security_mission_systems":
        return "reliability-focused engineering"
    if role_type == "ai_automation":
        return "automation engineering"
    if role_type == "embedded_firmware":
        return "embedded software"
    if "software" in concise_title:
        return "software engineering"
    return "engineering"


def _evidence_for_bucket(
    bucket: dict[str, Any],
    evidence_parts: list[str],
    fallback_index: int,
) -> str:
    for evidence in bucket.get("candidate_evidence") or []:
        cleaned = _clean_evidence_sentence(evidence)
        if cleaned:
            return cleaned
    if not evidence_parts:
        return (
            "My project work has required me to turn ambiguous requirements "
            "into testable software components."
        )
    return evidence_parts[min(fallback_index, len(evidence_parts) - 1)]


def _fallback_capability_paragraph(
    *,
    bucket: dict[str, Any],
    evidence: str,
    role_label: str,
    company: str,
    ordinal: str,
) -> str:
    claim = bucket.get("claim") or bucket.get("name") or "practical engineering work"
    relevance = bucket.get("relevance") or "the role depends on careful technical judgment"
    return (
        f"{ordinal} for {role_label} is {claim}. {evidence} I treated that work "
        "as more than feature delivery, focusing on the engineering constraints "
        "behind the implementation. That is relevant to "
        f"{company} because {relevance}."
    )


def _join_natural(values: list[str], *, conjunction: str = "and") -> str:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return "software development"
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f" {conjunction} ".join(cleaned)
    return f"{', '.join(cleaned[:-1])}, {conjunction} {cleaned[-1]}"


def _clean_evidence_sentence(text: str) -> str:
    cleaned = " ".join((text or "").strip().strip("•-– ").split())
    if not cleaned:
        return ""
    at_match = re.match(r"^At\s+([^,]+),\s+(.+)$", cleaned)
    if at_match:
        # Entity keys look like "Encompass Health - Therapy Technician";
        # only the company belongs in prose (2026-07-09 user report:
        # the full key was interpolated mid-sentence).
        entity = at_match.group(1).strip().split(" - ")[0].strip()
        body = _lowercase_initial(at_match.group(2).strip())
        if body.lower().startswith("i "):
            cleaned = f"At {entity}, {body}"
        else:
            cleaned = f"At {entity}, I {body}"
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
