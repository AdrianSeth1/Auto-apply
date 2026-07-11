"""Resume builder — JD-driven bullet selection, optional rewrite, and document generation.

Pipeline:
  1. Extract keywords/tags from JD requirements
  2. Select best-matching bullets from the bullet pool
  3. Optionally rewrite bullets with light lexical adjustment (keyword injection)
  4. Run fact-drift check to ensure no fabrication
  5. Assemble into docx via the document engine, convert to PDF

Design principle: block-based assembly, NOT full-text LLM rewrite.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from src.documents.docx_engine import build_resume_from_ir, create_default_template
from src.documents.file_manager import get_output_paths
from src.documents.latex_engine import build_resume_tex_from_ir, compile_latex_to_pdf
from src.documents.pdf_converter import convert_to_pdf
from src.documents.templates import (
    TemplateManifest,
    default_manifest,
    ensure_template_package,
    load_template_package,
)
from src.generation.evidence import (
    EvidenceBullet,
    collect_profile_evidence,
    evidence_by_entity,
    select_relevant_evidence,
)
from src.generation.fitting import fit_resume_document_to_template
from src.generation.ir import BulletRewriteResult, ResumeDocument, ResumeItem
from src.generation.validator import (
    validate_latex_artifacts,
    validate_resume_artifacts,
    validate_resume_document,
)
from src.intake.schema import RawJob

logger = logging.getLogger("autoapply.generation.resume_builder")

DEFAULT_TEMPLATE_DIR = Path("data/templates")
DEFAULT_OUTPUT_DIR = Path("data/output")

# Upper bound on render/trim attempts when squeezing into the page target.
# Each round drops one bullet (or item, once bullets run out), so 8 covers
# most cases even with very dense source data.
_MAX_TRIM_ATTEMPTS = 8


def generate_resume(
    job: RawJob,
    profile_data: dict[str, Any],
    selected_bullets: dict[str, list[str]] | None = None,
    template_path: Path | None = None,
    template_id: str = "ats_single_column_v1",
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    rewrite: bool = False,
    use_llm: bool = False,
    rewrite_mode: str = "balanced",
) -> dict[str, Any]:
    """Generate a tailored resume for a specific job.

    Args:
        job: The target job posting.
        profile_data: Full applicant profile dict.
        selected_bullets: Pre-selected bullets (if None, auto-selected from JD).
        template_path: Path to .docx template (creates default if None/missing).
        output_dir: Directory for output files.
        rewrite: Whether to do light lexical rewrite on bullets.
        use_llm: Whether to use LLM for rewrite (requires rewrite=True).

    Returns:
        Dict with generated paths plus the ResumeDocument IR and validation result.
    """
    # Resolve template package. Visual rules live in template.docx/manifest.json;
    # this pipeline only passes structured content and named style references.
    template_manifest = _manifest_for_template_path(template_path)
    if template_path is None or not template_path.exists():
        package = ensure_template_package("resume", template_id)
        template_path = package.template_path
        template_manifest = package.manifest
    elif template_manifest is None:
        template_manifest = default_manifest("resume")

    if not template_path.exists():
        template_path = DEFAULT_TEMPLATE_DIR / "default_resume.docx"
        if not template_path.exists():
            logger.info("No template found, creating default")
            create_default_template(template_path)

    resume_document = build_resume_document(
        job=job,
        profile_data=profile_data,
        selected_bullets=selected_bullets,
        rewrite=rewrite,
        use_llm=use_llm,
        template_id=template_manifest.template_id,
        template_manifest=template_manifest,
        rewrite_mode=rewrite_mode,
    )
    target_pages = template_manifest.target_pages or template_manifest.capacity.max_pages
    validation = validate_resume_document(
        resume_document,
        jd_tags=resume_document.metadata.get("jd_tags", []),
        max_bullet_words=template_manifest.capacity.max_words_per_bullet or 32,
        max_estimated_pages=target_pages,
    )
    if not validation.ok:
        logger.warning(
            "Resume IR validation found blocking issues for %s at %s: %s",
            job.title,
            job.company,
            [issue.type for issue in validation.issues if issue.severity == "error"],
        )

    # Get output paths
    paths = get_output_paths(
        company=job.company,
        role=job.title,
        output_dir=output_dir,
        pattern=template_manifest.filename_pattern,
        profile_name=profile_data.get("identity", {}).get("full_name", ""),
        custom_label=template_manifest.filename_custom_label,
        template_id=template_manifest.template_id,
    )

    # Render + iterate: if the rendered PDF is over the page target,
    # drop the weakest bullet and re-render. This is the only reliable
    # way to honour the user's "exactly N pages" setting because the
    # pre-render fitter only knows item caps -- font size, line spacing
    # and margin changes from Template Library edits can push content
    # over the line in ways the IR can't predict.
    resume_document, docx_path, pdf_path = _render_resume_to_target_pages(
        resume_document=resume_document,
        template_path=template_path,
        template_manifest=template_manifest,
        docx_output=paths["resume_docx"],
        pdf_output=paths["resume_pdf"],
        target_pages=target_pages,
    )

    validation = validate_resume_artifacts(
        validation,
        docx_path=docx_path,
        pdf_path=pdf_path,
        pdf_attempted=True,
        max_pages=template_manifest.capacity.max_pages,
        target_pages=target_pages,
    )

    result = {
        "docx": docx_path,
        "ir": resume_document,
        "validation": validation,
    }
    if pdf_path:
        result["pdf"] = pdf_path

    logger.info(
        "Generated resume for %s at %s: %s",
        job.title,
        job.company,
        list(result.keys()),
    )
    return result


def generate_resume_latex(
    job: RawJob,
    profile_data: dict[str, Any],
    selected_bullets: dict[str, list[str]] | None = None,
    *,
    template_id: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    rewrite: bool = False,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Generate a tailored resume as LaTeX, with optional PDF compilation."""
    package = load_template_package("resume", template_id)
    if package.manifest.renderer != "latex":
        raise ValueError("Selected resume template is not a LaTeX template.")

    resume_document = build_resume_document(
        job=job,
        profile_data=profile_data,
        selected_bullets=selected_bullets,
        rewrite=rewrite,
        use_llm=use_llm,
        template_id=package.template_id,
        template_manifest=package.manifest,
    )
    target_pages = package.manifest.target_pages or package.manifest.capacity.max_pages
    validation = validate_resume_document(
        resume_document,
        jd_tags=resume_document.metadata.get("jd_tags", []),
        max_bullet_words=package.manifest.capacity.max_words_per_bullet or 32,
        max_estimated_pages=target_pages,
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
    tex_path = build_resume_tex_from_ir(
        template_path=package.template_path,
        document=resume_document,
        output_path=paths["resume_tex"],
        manifest=package.manifest,
    )

    pdf_path = None
    try:
        pdf_path = compile_latex_to_pdf(tex_path, paths["resume_pdf"])
    except Exception as exc:
        logger.warning("LaTeX resume PDF compilation failed: %s", exc)

    validation = validate_latex_artifacts(
        validation,
        tex_path=tex_path,
        pdf_path=pdf_path,
        pdf_attempted=True,
        max_pages=package.manifest.capacity.max_pages,
        target_pages=target_pages,
    )

    result = {
        "tex": tex_path,
        "ir": resume_document,
        "validation": validation,
    }
    if pdf_path:
        result["pdf"] = pdf_path
    logger.info("Generated LaTeX resume for %s at %s", job.title, job.company)
    return result


def build_resume_document(
    job: RawJob,
    profile_data: dict[str, Any],
    selected_bullets: dict[str, list[str]] | None = None,
    *,
    rewrite: bool = False,
    use_llm: bool = False,
    template_id: str = "ats_single_column_v1",
    template_manifest: TemplateManifest | None = None,
    rewrite_mode: str = "balanced",
) -> ResumeDocument:
    """Plan a renderer-agnostic resume IR for a target job."""
    template_manifest = template_manifest or default_manifest("resume")
    jd_tags = extract_jd_tags(job)
    logger.info("JD tags for %s at %s: %s", job.title, job.company, jd_tags)

    db_session = _optional_generation_session()
    try:
        evidence = (
            _evidence_from_selected_bullets(profile_data, selected_bullets, jd_tags)
            if selected_bullets is not None
            else select_relevant_evidence(
                jd_tags,
                profile_data,
                max_bullets_per_entity=_max_bullets_per_entity(template_manifest),
                query_text=f"{job.title}\n{job.description or ''}",
                db_session=db_session,
                query_embedding=_job_query_embedding(job),
            )
        )
    finally:
        if db_session is not None:
            db_session.close()
    grouped = evidence_by_entity(evidence)

    if rewrite and use_llm:
        grouped = _rewrite_grouped_evidence(grouped, jd_tags, mode=rewrite_mode)

    document = ResumeDocument(
        template_id=template_id,
        target_role=job.title,
        company=job.company,
        header=profile_data.get("identity", {}),
        skills=_prioritize_skills(profile_data.get("skills", {}), jd_tags),
        education=profile_data.get("education", []),
        experiences=_build_experience_items(profile_data, grouped),
        projects=_build_project_items(profile_data, grouped),
        custom_sections=_build_custom_sections(profile_data),
        section_order=template_manifest.section_order or _plan_section_order(job, profile_data),
        metadata={
            "jd_tags": jd_tags,
            "selected_evidence_count": sum(len(items) for items in grouped.values()),
        },
    )
    return fit_resume_document_to_template(document, template_manifest)


def _build_custom_sections(profile_data: dict[str, Any]) -> list:
    """Load free-form sections (VOLUNTEER / AWARDS / AFFILIATIONS / ...)
    from the profile and project them onto the IR.

    Accepts either of two layouts (both produced by various profile
    sources we've shipped over the project's life):

      A) ``custom_sections: [{title, entries: [...]}, ...]`` -- the
         canonical schema the resume importer now emits.
      B) ``custom_sections: {Title1: [entries], Title2: [entries], ...}``
         -- a dict-of-sections shape we accept defensively so a
         hand-edited YAML keeps working.

    Returns an empty list when the field is missing or malformed. We do
    NOT raise: a bad custom-section payload should degrade gracefully
    rather than block the entire resume from rendering.
    """
    from src.generation.ir import CustomSection, CustomSectionEntry  # noqa: PLC0415

    raw = profile_data.get("custom_sections")
    if not raw:
        return []

    sections: list[CustomSection] = []

    def _coerce_entries(entries_raw) -> list[CustomSectionEntry]:
        entries: list[CustomSectionEntry] = []
        if not isinstance(entries_raw, list):
            return entries
        for entry in entries_raw:
            if not isinstance(entry, dict):
                # Plain string in the entry list (e.g. INTERESTS &
                # ACTIVITIES is often just a comma-separated line).
                text = str(entry).strip()
                if text:
                    entries.append(CustomSectionEntry(details=text))
                continue
            bullets_raw = entry.get("bullets") or []
            bullets: list[str] = []
            for bullet in bullets_raw:
                if isinstance(bullet, dict):
                    text = str(bullet.get("text") or "").strip()
                elif bullet is None:
                    text = ""
                else:
                    text = str(bullet).strip()
                if text:
                    bullets.append(text)
            entries.append(
                CustomSectionEntry(
                    title=str(entry.get("title") or "").strip(),
                    organization=str(entry.get("organization") or "").strip(),
                    location=str(entry.get("location") or "").strip(),
                    start_date=str(entry.get("start_date") or "").strip(),
                    end_date=str(entry.get("end_date") or "").strip(),
                    details=str(entry.get("details") or entry.get("description") or "").strip(),
                    bullets=bullets,
                )
            )
        return entries

    if isinstance(raw, list):
        for section in raw:
            if not isinstance(section, dict):
                continue
            title = str(section.get("title") or "").strip()
            if not title:
                continue
            sections.append(
                CustomSection(title=title, entries=_coerce_entries(section.get("entries")))
            )
    elif isinstance(raw, dict):
        for title, entries_raw in raw.items():
            title = str(title or "").strip()
            if not title:
                continue
            sections.append(CustomSection(title=title, entries=_coerce_entries(entries_raw)))

    # Strip empty sections so a heading with no entries doesn't leave
    # a dangling section title on the rendered resume.
    return [s for s in sections if s.entries]


def _evidence_from_selected_bullets(
    profile_data: dict[str, Any],
    selected_bullets: dict[str, list[str]],
    jd_tags: list[str],
) -> list[EvidenceBullet]:
    all_evidence = evidence_by_entity(collect_profile_evidence(profile_data))
    tag_set = {_normalize_tag(tag) for tag in jd_tags}
    selected: list[EvidenceBullet] = []

    for entity, bullet_texts in selected_bullets.items():
        candidates = all_evidence.get(entity, [])
        for index, text in enumerate(bullet_texts):
            match = next((item for item in candidates if item.text == text), None)
            if match is None:
                tags = [_normalize_tag(tag) for tag in _infer_tags_from_text(text, tag_set)]
                selected.append(
                    EvidenceBullet(
                        source_id=f"manual:{_slugify(entity)}:bullet:{index}",
                        source_type="manual",
                        source_entity=entity,
                        text=text,
                        tags=tags,
                        matched_keywords=tags,
                        score=float(len(tags)),
                        original_index=index,
                    )
                )
            else:
                matched = _matched_keywords(match.text, match.tags, tag_set)
                selected.append(
                    match.model_copy(
                        update={
                            "matched_keywords": matched,
                            "score": float(len(matched)),
                        }
                    )
                )

    return selected


def _optional_generation_session():
    try:
        from src.core.config import load_config
        from src.core.database import get_session_factory

        return get_session_factory(load_config())()
    except Exception:
        return None


def _job_query_embedding(job: RawJob) -> list[float] | None:
    for key in ("description_embedding", "embedding"):
        value = job.raw_data.get(key)
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, int | float) for item in value)
        ):
            return [float(item) for item in value]
    return None


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


def _max_bullets_per_entity(manifest: TemplateManifest) -> int:
    limits = []
    for section in ("experience", "projects"):
        config = manifest.sections.get(section)
        if config and config.enabled and config.max_bullets_per_item:
            limits.append(config.max_bullets_per_item)
    return max(limits) if limits else 4


_BATCH_REWRITE_SYSTEM = """/no_think
You are a resume bullet editor. Rewrite each numbered bullet to naturally incorporate relevant job keywords while preserving every fact.

Return ONLY a JSON object:
{"rewritten_bullets": ["...", "...", ...]}

Rules:
- Return exactly as many bullets as given, in the same order
- If a bullet ALREADY contains a target keyword, return it unchanged, never
  paraphrase an existing keyword match away
- Preserve every number, metric, and named tool exactly as written; NEVER add
  a number that is not in the original bullet
- Do NOT invent claims not present in the original bullet
- Never swap plain words for fancier synonyms; only change wording to insert
  a target keyword truthfully
- Spread keywords across bullets; avoid repeating the same keyword in multiple bullets
- Keep professional tone throughout
- Do NOT use em dashes or en dashes (—, –). Use a comma, period, or
  semicolon instead
- Inline markup (optional, use sparingly): **bold** for one key skill or metric per bullet, *italics* for proper nouns"""

# The leading "/no_think" is Qwen3's soft switch disabling thinking mode for
# the turn — a 16-bullet structured-JSON task on a thinking model was
# producing 300s timeouts and malformed outputs (2026-07-09 logs). Other
# model families ignore the token harmlessly.

# Bullets per LLM call. One 16-bullet JSON was fragile (wrong-count /
# non-list responses discarded ALL tailoring); smaller chunks fail
# independently and parse reliably.
_BATCH_REWRITE_CHUNK_SIZE = 8


def _rewrite_grouped_evidence(
    grouped: dict[str, list[EvidenceBullet]],
    jd_tags: list[str],
    *,
    mode: str = "balanced",
) -> dict[str, list[EvidenceBullet]]:
    """Rewrite all bullets in a single LLM call (batch mode).

    Sends every bullet at once with the JD keywords and gets back a
    rewritten list in the same order. Falls back to originals if the
    call fails or the model returns the wrong number of bullets.
    """
    from src.utils.llm import generate_json  # noqa: PLC0415

    if not jd_tags:
        return grouped

    # Flatten all bullets, tracking position so we can reconstruct later
    flat: list[str] = []
    index_map: list[tuple[str, int]] = []
    for entity, items in grouped.items():
        for i, item in enumerate(items):
            flat.append(item.text)
            index_map.append((entity, i))

    if not flat:
        return grouped

    keywords_str = ", ".join(jd_tags[:15])

    mode_hint = {
        "conservative": "Make the smallest possible change — swap at most 1-2 words per bullet.",
        "aggressive": "Rewrite freely while keeping every fact and number grounded.",
    }.get(mode, "Sensible rewrites — adjust word choice, keep meaning and structure.")

    # 2026-07-09: chunked + guarded. The previous single 16-bullet call
    # had two failure modes seen in production logs the same day:
    # (1) malformed / wrong-count responses discarded ALL tailoring for
    # a resume; (2) the batch path skipped _rewrite_regression_guard,
    # so an invented number reached a rendered resume (caught only as a
    # post-hoc validation WARNING). Chunks fail independently, and every
    # accepted rewrite now passes the same deterministic guard as the
    # per-bullet path.
    validated: list[str] = list(flat)  # default: originals
    call_count = 0
    for chunk_start in range(0, len(flat), _BATCH_REWRITE_CHUNK_SIZE):
        chunk = flat[chunk_start : chunk_start + _BATCH_REWRITE_CHUNK_SIZE]
        bullets_block = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(chunk))
        prompt = (
            f"Target keywords: {keywords_str}\n\n"
            f"Mode: {mode_hint}\n\n"
            f"Bullets to rewrite:\n{bullets_block}\n\n"
            f"Return a JSON object with key 'rewritten_bullets' — "
            f"a list of exactly {len(chunk)} rewritten bullets in the same order."
        )
        try:
            result = generate_json(prompt, system=_BATCH_REWRITE_SYSTEM, timeout=300)
            call_count += 1
            rewrites = (
                (result or {}).get("rewritten_bullets") if isinstance(result, dict) else None
            )
            if not (isinstance(rewrites, list) and len(rewrites) == len(chunk)):
                logger.warning(
                    "Batch rewrite chunk returned %s bullets, expected %d — "
                    "keeping originals for this chunk",
                    len(rewrites) if isinstance(rewrites, list) else "non-list",
                    len(chunk),
                )
                continue
            for offset, (orig, rw) in enumerate(zip(chunk, rewrites)):
                rw_text = str(rw).strip() if rw else ""
                if not rw_text or len(rw_text) > len(orig) * 2.5 or len(rw_text) < len(orig) * 0.25:
                    continue  # keep original
                rw_text = _normalize_prose_dashes(rw_text)
                validated[chunk_start + offset] = _rewrite_regression_guard(
                    orig, rw_text, keywords_str
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Batch rewrite chunk failed (%s), keeping originals for this chunk", exc
            )

    rewritten: dict[str, list[EvidenceBullet]] = {
        e: list(items) for e, items in grouped.items()
    }
    for idx, (entity, bullet_idx) in enumerate(index_map):
        item = rewritten[entity][bullet_idx]
        rewritten[entity][bullet_idx] = item.model_copy(
            update={"render_text": validated[idx]}
        )
    changed = sum(1 for orig, final in zip(flat, validated) if orig != final)
    logger.info(
        "Batch bullet rewrite: %d bullets, %d LLM call(s), %d accepted rewrites",
        len(flat),
        call_count,
        changed,
    )
    return rewritten


def _build_experience_items(
    profile_data: dict[str, Any], grouped: dict[str, list[EvidenceBullet]]
) -> list[ResumeItem]:
    items: list[ResumeItem] = []
    for index, exp in enumerate(profile_data.get("work_experiences", [])):
        if not isinstance(exp, dict):
            continue
        company = str(exp.get("company") or "Unknown")
        title = str(exp.get("title") or "")
        entity = f"{company} - {title}".strip(" -")
        evidence_items = grouped.get(entity, [])
        if not evidence_items:
            continue
        items.append(
            ResumeItem(
                source_id=f"experience:{_slugify(entity) or index}",
                source_type="experience",
                name=company,
                title=title,
                organization=company,
                location=str(exp.get("location") or ""),
                start_date=str(exp.get("start_date") or ""),
                end_date=str(exp.get("end_date") or ""),
                meta=str(exp.get("description") or ""),
                bullets=[item.to_resume_bullet() for item in evidence_items],
            )
        )
    return items


def _build_project_items(
    profile_data: dict[str, Any], grouped: dict[str, list[EvidenceBullet]]
) -> list[ResumeItem]:
    items: list[ResumeItem] = []
    for index, project in enumerate(profile_data.get("projects", [])):
        if not isinstance(project, dict):
            continue
        name = str(project.get("name") or "Unknown")
        evidence_items = grouped.get(name, [])
        if not evidence_items:
            continue
        items.append(
            ResumeItem(
                source_id=f"project:{_slugify(name) or index}",
                source_type="project",
                name=name,
                title=str(project.get("role") or ""),
                meta=str(project.get("description") or ""),
                start_date=str(project.get("start_date") or ""),
                end_date=str(project.get("end_date") or ""),
                tech_stack=[str(value) for value in project.get("tech_stack", [])],
                bullets=[item.to_resume_bullet() for item in evidence_items],
            )
        )
    return items


def _prioritize_skills(skills: dict[str, Any], jd_tags: list[str]) -> dict[str, list[str]]:
    tag_set = {_normalize_tag(tag) for tag in jd_tags}
    prioritized: dict[str, list[str]] = {}
    for category, values in skills.items():
        if not isinstance(values, list):
            continue
        # 2026-07-10: dict-shaped entries (certifications: {name, issuer,
        # date}) were stringified to their repr — render their name.
        clean_values = [
            str(value.get("name") if isinstance(value, dict) else value)
            for value in values
            if str(value.get("name") if isinstance(value, dict) else value).strip()
        ]
        prioritized[category] = sorted(
            clean_values,
            key=lambda value: _normalize_tag(value) in tag_set,
            reverse=True,
        )
    return prioritized


def _plan_section_order(job: RawJob, profile_data: dict[str, Any]) -> list[str]:
    # Summary is intentionally absent in every branch -- see ResumeDocument
    # in src/generation/ir.py.
    title = job.title.lower()
    seniority = (job.seniority or "").lower()
    is_student = bool(profile_data.get("education")) and any(
        token in f"{title} {seniority}" for token in ("intern", "student", "coop", "co-op")
    )
    if is_student:
        return ["header", "education", "skills", "projects", "experience"]
    return ["header", "skills", "experience", "projects", "education"]


def _matched_keywords(text: str, tags: list[str], tag_set: set[str]) -> list[str]:
    text_tokens = set(_infer_tags_from_text(text, tag_set))
    tag_matches = {_normalize_tag(tag) for tag in tags} & tag_set
    return sorted(tag_matches | text_tokens)


def _infer_tags_from_text(text: str, tag_set: set[str]) -> list[str]:
    tokens = {_normalize_tag(token) for token in text.split()}
    return sorted(tokens & tag_set)


def _normalize_tag(value: str) -> str:
    return value.lower().strip().replace(" ", "_")


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:80]


def extract_jd_tags(job: RawJob) -> list[str]:
    """Extract searchable tags from a job's requirements and description.

    Combines must-have skills, preferred skills, and inferred keywords
    from the title into a flat tag list for bullet pool querying.
    """
    tags = []

    # From structured requirements
    tags.extend(s.lower() for s in job.requirements.must_have_skills)
    tags.extend(s.lower() for s in job.requirements.preferred_skills)
    tags.extend(s.lower() for s in job.requirements.keywords)
    tags.extend(s.lower() for s in job.requirements.soft_skills)
    for value in (
        job.requirements.domain,
        job.requirements.role_family,
        job.requirements.seniority,
    ):
        if value:
            tags.append(value.lower())

    # From title and JD text -- extract meaningful technical keywords
    searchable_text = f"{job.title} {job.description or ''}".lower()
    searchable_tokens = set(re.findall(r"[a-z][a-z0-9+#.]+", searchable_text))
    tech_keywords = {
        "python",
        "java",
        "javascript",
        "typescript",
        "go",
        "rust",
        "c++",
        "react",
        "vue",
        "angular",
        "node",
        "django",
        "flask",
        "fastapi",
        "spring",
        "kubernetes",
        "docker",
        "aws",
        "gcp",
        "azure",
        "sql",
        "postgresql",
        "mongodb",
        "redis",
        "graphql",
        "ml",
        "ai",
        "machine learning",
        "deep learning",
        "nlp",
        "backend",
        "frontend",
        "fullstack",
        "devops",
        "sre",
        "data",
        "analytics",
        "infrastructure",
        "platform",
        "security",
    }
    for keyword in tech_keywords:
        if " " in keyword:
            if keyword in searchable_text:
                tags.append(keyword)
        elif keyword in searchable_tokens:
            tags.append(keyword)

    # 2026-07-08: augment with small-tier LLM extraction. The hardcoded
    # tech vocabulary above is SWE-only — for solutions-consultant / TAM /
    # CSM / analyst JDs it fires on almost nothing, which starved bullet
    # selection, rewrite targeting, and skill prioritization for the
    # user's actual target roles. The LLM sees the real JD language
    # ("technical discovery", "time to value", "demos", "design systems")
    # and returns it. Cached by prompt, heuristic-only on any failure.
    # Only generation paths call extract_jd_tags, so search speed is
    # unaffected.
    tags.extend(_llm_jd_keywords(job))

    # Deduplicate preserving order
    seen = set()
    unique = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique.append(tag)

    return unique


_JD_KEYWORDS_SYSTEM = """You extract resume-targeting keywords from job descriptions.

Return ONLY a JSON array of 10-15 lowercase strings. Each entry must be:
- a skill, tool, methodology, deliverable, or role-specific competency the
  employer screens for (e.g. "technical discovery", "demos", "time to value",
  "sql", "stakeholder management", "design systems", "presales")
- 1-4 words, exactly as a resume should mirror it
- concrete — never generic filler like "communication" alone, "fast-paced
  environment", "team player", or benefits/EEO language

No prose, no objects, no explanations — just the JSON array."""


def _llm_jd_keywords(job: RawJob) -> list[str]:
    """Small-tier keyword extraction with strict output validation."""
    description = (job.description or "").strip()
    if len(description) < 200:
        return []
    from src.utils.llm import generate_json  # noqa: PLC0415

    try:
        result = generate_json(
            f"Job title: {job.title}\n\nJob description:\n{description[:6000]}",
            system=_JD_KEYWORDS_SYSTEM,
            timeout=120,
            cache=True,
            tier="small",
        )
    except Exception:  # noqa: BLE001 -- extraction is an enhancement, never a blocker
        logger.debug("LLM JD keyword extraction skipped", exc_info=True)
        return []

    if not isinstance(result, list):
        return []
    keywords = []
    for item in result[:20]:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().lower()
        # Reject mega-tags (whole requirement sentences) and junk.
        if 2 <= len(cleaned) <= 40 and len(cleaned.split()) <= 4:
            keywords.append(cleaned)
    return keywords


def select_bullets_for_jd(
    jd_tags: list[str],
    profile_data: dict[str, Any],
    max_bullets_per_entity: int = 4,
) -> dict[str, list[str]]:
    """Select the best-matching bullets from profile based on JD tags.

    For each experience/project entity, selects bullets whose tags have
    the highest overlap with the JD tags. Falls back to all bullets if
    no tag overlap found.

    Returns:
        {entity_name: [bullet_text, ...]}
    """
    selected: dict[str, list[str]] = {}
    evidence = select_relevant_evidence(
        jd_tags,
        profile_data,
        max_bullets_per_entity=max_bullets_per_entity,
    )
    for entity, items in evidence_by_entity(evidence).items():
        selected[entity] = [item.text for item in items]

    total = sum(len(v) for v in selected.values())
    logger.info("Selected %d bullets across %d entities", total, len(selected))
    return selected


def _rank_and_select(
    bullets: list[dict],
    tag_set: set[str],
    max_count: int,
) -> list[str]:
    """Rank bullets by tag overlap and return top N texts."""
    scored = []
    for bullet in bullets:
        if not isinstance(bullet, dict) or not bullet.get("text"):
            continue
        bullet_tags = set(t.lower() for t in bullet.get("tags", []))
        overlap = len(bullet_tags & tag_set)
        scored.append((overlap, bullet["text"]))

    # Sort by overlap descending, then by original order for ties
    scored.sort(key=lambda x: x[0], reverse=True)

    # If no overlap at all, return all bullets (better than nothing)
    if scored and scored[0][0] == 0:
        return [text for _, text in scored[:max_count]]

    return [text for _, text in scored[:max_count]]


def rewrite_bullets(
    selected_bullets: dict[str, list[str]],
    jd_tags: list[str],
    *,
    mode: str = "balanced",
) -> dict[str, list[str]]:
    """Light lexical rewrite of bullets to inject JD keywords.

    ``mode`` selects which rules block the LLM gets:
      - ``conservative``: change at most a word or two; mostly pass-through
      - ``balanced``: default; sensible rewriting while preserving claims
      - ``aggressive``: rewrite freely while keeping all facts grounded

    Mode is plumbed in from ``material_defaults.patch_aggressiveness``
    for the patch_existing strategy. For regenerate the caller may
    leave ``mode`` at the default; aggressiveness is meaningful mainly
    when there's an original document the user wants to "preserve".

    Phase 18.5: bullets within one ``rewrite_bullets`` call run
    concurrently via :mod:`asyncio` capped by
    ``parallelism.bullet_rewrites.max_concurrent_per_task``. Provider
    abuse is bounded by the global / per-provider semaphores in
    :mod:`src.utils.parallelism` so increasing the per-task cap does
    NOT translate into unbounded LLM concurrency across workers.
    """
    from src.utils.parallelism import run_coroutine_safely  # noqa: PLC0415

    return run_coroutine_safely(
        _rewrite_bullets_async(selected_bullets, jd_tags, mode=mode)
    )


async def _rewrite_bullets_async(
    selected_bullets: dict[str, list[str]],
    jd_tags: list[str],
    *,
    mode: str = "balanced",
) -> dict[str, list[str]]:
    """Phase 18.5 implementation behind :func:`rewrite_bullets`."""
    import asyncio  # noqa: PLC0415

    from src.utils.llm import LLMError  # noqa: PLC0415
    from src.utils.parallelism import bullet_rewrite_cap  # noqa: PLC0415

    keywords_str = ", ".join(jd_tags[:15])
    cap = max(1, bullet_rewrite_cap())
    sem = asyncio.Semaphore(cap)

    async def _one(bullet: str) -> str:
        async with sem:
            try:
                rewrite = await asyncio.to_thread(
                    _rewrite_single_bullet, bullet, keywords_str, mode=mode
                )
                new_text = rewrite.rewritten_bullet
                if (
                    len(new_text) > len(bullet) * 2.5
                    or len(new_text) < len(bullet) * 0.25
                ):
                    logger.warning("Rewrite drift detected for bullet, keeping original")
                    return bullet
                return new_text
            except (LLMError, Exception) as exc:  # noqa: BLE001
                logger.debug("Rewrite failed for bullet: %s", exc)
                return bullet

    rewritten: dict[str, list[str]] = {}
    for entity, bullets in selected_bullets.items():
        results = await asyncio.gather(*[_one(b) for b in bullets])
        rewritten[entity] = list(results)
    return rewritten


_REWRITE_SYSTEM_BASE = """You are a resume bullet point editor. Your job is to adjust
a resume bullet to better match target job keywords while preserving facts.

CRITICAL constraints (apply in every mode):
- If the original bullet ALREADY contains a target keyword, that exact
  wording is sacred, never paraphrase it away. ("cutting time-to-value"
  must stay "time-to-value" when time-to-value is a target keyword;
  "demo conversion" must keep the word "demo" when demos are targeted.)
- NEVER swap plain words for fancier synonyms. "Redesigned onboarding"
  must not become "Overhauled the customer integration lifecycle".
  Recruiters and screening software both prefer the plain term.
- The ONLY permitted change is inserting or substituting toward TARGET
  KEYWORDS where truthful. If no keyword fits naturally, return the
  original bullet verbatim.
- Never make the bullet longer than the original by more than a few words.
- Do NOT use em dashes or en dashes (—, –) as punctuation inside the
  bullet. Use a comma, period, or semicolon instead. (This does not
  apply to ordinary hyphens in compound words like "time-to-value" or
  "full-time", which stay exactly as they are.)

Return ONLY a JSON object with exactly this shape:
{
  "rewritten_bullet": "...",
  "used_skills": ["Python"],
  "source_ids": [],
  "confidence": "high" | "medium" | "low",
  "changed_claims": []
}

Inline markup you MAY use sparingly inside ``rewritten_bullet``:
- ``**text**`` -- bold for ONE or TWO key skills, named technologies,
  or quantified outcomes per bullet (e.g. "**FastAPI**", "**1.5M+
  requests/day**"). Never bold a whole sentence.
- ``*text*`` -- italics for proper nouns, paper / product / project
  names. Never italicise ordinary technical terms.
- Do NOT use any other Markdown -- no headings, no links, no code
  fences, no ``_underscores_`` (so ``model_v2`` stays literal).
- Markup is OPTIONAL. Leave bullets plain when no term clearly merits
  emphasis.
"""

_REWRITE_RULES_BY_MODE: dict[str, str] = {
    "conservative": (
        "Rules (CONSERVATIVE — make the smallest possible change):\n"
        "- Preserve sentence structure, length, and verbs as much as humanly possible\n"
        "- Swap at most 1-2 words to incorporate ONE highly-relevant keyword if it "
        "lands naturally; otherwise return the bullet unchanged\n"
        "- Preserve all numbers, metrics, and quantified results EXACTLY\n"
        "- Do NOT add new skills, technologies, or achievements that weren't in the original\n"
        "- Do NOT change the tone or claims\n"
        "- changed_claims must list any claim that might not be grounded in the original bullet"
    ),
    "balanced": (
        "Rules (BALANCED — sensible rewriting):\n"
        "- Keep the same meaning, structure, and claims\n"
        "- Preserve all numbers, metrics, and quantified results EXACTLY\n"
        "- Adjust word choice to incorporate relevant keywords where natural\n"
        "- Do NOT add new skills, technologies, or achievements that weren't in the original\n"
        "- Do NOT change the tone from professional to casual or vice versa\n"
        "- changed_claims must list any claim that might not be grounded in the original bullet"
    ),
    "aggressive": (
        "Rules (AGGRESSIVE — rewrite freely while keeping facts):\n"
        "- You may rewrite the sentence structure and verbs as needed to surface "
        "relevant keywords for the target role\n"
        "- Preserve all numbers, metrics, and quantified results EXACTLY\n"
        "- You may emphasise transferable skills implied by the original bullet, "
        "but do NOT invent skills, technologies, or achievements that aren't grounded\n"
        "- Keep the tone professional\n"
        "- changed_claims must list any claim whose grounding in the original bullet "
        "is even slightly uncertain"
    ),
}


def _rewrite_system_for(mode: str) -> str:
    rules = _REWRITE_RULES_BY_MODE.get(mode, _REWRITE_RULES_BY_MODE["balanced"])
    return _REWRITE_SYSTEM_BASE + "\n" + rules


# Kept for back-compat with any external imports of the legacy name.
_REWRITE_SYSTEM = _rewrite_system_for("balanced")


_INVALID_BULLET_REWRITE_PATTERNS = (
    "please paste the system instructions",
    "paste the system instructions",
    "system instructions you want me to follow",
    "if you want me to inspect or modify",
    "point me to the relevant file",
    "openai codex",
    "tokens used",
    "reading additional input from stdin",
    "i cannot rewrite",
    "i can't rewrite",
    "i am unable to",
    "i'm unable to",
    "as an ai language model",
    "as a language model",
)


def _normalize_prose_dashes(text: str) -> str:
    """Replace em/en dash punctuation with a comma so rewrites don't read
    AI-generated (mirrors ``cover_letter._normalize_cover_letter_dashes``).

    Only targets the Unicode em dash (—) and en dash (–), never the plain
    ASCII hyphen, so compound words like "time-to-value" or "full-time"
    are untouched -- this is a punctuation cleanup, not a word filter.
    """
    cleaned = re.sub(r"\s*[—–]\s*", ", ", text)
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    return re.sub(r" {2,}", " ", cleaned).strip()


def _clean_llm_bullet_rewrite_output(
    rewritten: str,
    original: str,
) -> str:
    """Reject CLI/meta responses and obvious garbage so bad rewrites fall back.

    Mirrors ``cover_letter._clean_llm_cover_letter_output``: surface an
    :class:`LLMError` whenever the model returns a meta-response, fenced
    code-block dump, or a payload so short/long it cannot be a real
    resume bullet. The async wrapper above catches ``LLMError`` and keeps
    the original bullet, which is the correct fallback.
    """
    from src.utils.llm import LLMError  # noqa: PLC0415

    text = (rewritten or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    text = text.strip("•-– ").strip()
    text = _normalize_prose_dashes(text)

    if not text:
        raise LLMError("LLM returned an empty bullet rewrite.")

    lower = text.lower()
    if any(pattern in lower for pattern in _INVALID_BULLET_REWRITE_PATTERNS):
        raise LLMError("LLM returned a meta-response instead of a bullet rewrite.")

    word_count = len(text.split())
    if word_count < 4:
        raise LLMError("LLM returned a bullet rewrite that is too short.")
    if word_count > 80:
        raise LLMError("LLM returned a bullet rewrite that is too long.")
    if "\n\n" in text:
        raise LLMError("LLM returned multiple paragraphs instead of one bullet.")

    original_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\+?\b", original))
    new_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\+?\b", text))
    if new_numbers - original_numbers:
        raise LLMError("LLM rewrite introduced numbers not present in the original bullet.")

    return text


def _rewrite_single_bullet(
    bullet: str,
    keywords: str,
    *,
    mode: str = "balanced",
) -> BulletRewriteResult:
    """Rewrite a single bullet using structured LLM output.

    ``mode`` is one of ``conservative`` / ``balanced`` / ``aggressive``
    and chooses the rewriting rules section in the system prompt.
    """
    from src.utils.llm import generate_json

    prompt = (
        f"Target keywords: {keywords}\n\n"
        f"Original bullet: {bullet}\n\n"
        f"Rewrite the bullet to naturally incorporate relevant keywords."
    )
    result = generate_json(prompt, system=_rewrite_system_for(mode), timeout=300)
    if isinstance(result, str):
        cleaned = _clean_llm_bullet_rewrite_output(result, bullet)
        cleaned = _rewrite_regression_guard(bullet, cleaned, keywords)
        return BulletRewriteResult(rewritten_bullet=cleaned)

    parsed = BulletRewriteResult.model_validate(result)
    cleaned = _clean_llm_bullet_rewrite_output(parsed.rewritten_bullet, bullet)
    cleaned = _rewrite_regression_guard(bullet, cleaned, keywords)
    return parsed.model_copy(update={"rewritten_bullet": cleaned})


def _rewrite_regression_guard(original: str, rewritten: str, keywords: str) -> str:
    """Return ``original`` when the rewrite made the bullet WORSE.

    2026-07-08 (user report, Figma resume): the local-model rewriter was
    paraphrasing exact JD matches out of bullets — the JD said "time to
    value" and "demos", the original bullets contained both, and the
    rewrite replaced them with "initial value realization" and
    "trial-to-pipeline progression". A rewrite exists to ADD keyword
    alignment; any rewrite that reduces it is strictly worse than a
    no-op, so we detect three regressions deterministically and keep the
    original bullet:

      1. a target keyword present in the original is missing from the
         rewrite (keyword destruction — the Figma case);
      2. a number/metric in the original is missing (fact loss);
      3. the rewrite inflates the word count noticeably (thesaurus prose).
    """
    if not rewritten or not rewritten.strip():
        return original

    def _plain(text: str) -> str:
        # Strip the permitted **bold** / *italic* markup and normalize
        # separators so "time-to-value" == "time to value".
        text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
        return re.sub(r"[\s\-–—/]+", " ", text.lower())

    original_plain = _plain(original)
    rewritten_plain = _plain(rewritten)

    # 1. Keyword destruction. Match on word boundaries and accept simple
    # singular/plural variants ("demos" keyword must recognise "demo
    # conversion" in the original — the exact Figma failure).
    def _contains(text: str, phrase: str) -> bool:
        variants = {phrase}
        variants.add(phrase[:-1] if phrase.endswith("s") else phrase + "s")
        return any(
            re.search(rf"\b{re.escape(variant)}\b", text) for variant in variants
        )

    for raw_keyword in (keywords or "").split(","):
        phrase = _plain(raw_keyword.strip())
        if len(phrase) < 3:
            continue
        if _contains(original_plain, phrase) and not _contains(rewritten_plain, phrase):
            logger.info(
                "rewrite guard: keeping original bullet (rewrite dropped "
                "matched keyword %r)",
                raw_keyword.strip(),
            )
            return original
    # 2. Fact loss: every number in the original must survive.
    for number in re.findall(r"\d+(?:[.,]\d+)?%?", original_plain):
        if number not in rewritten_plain:
            logger.info(
                "rewrite guard: keeping original bullet (rewrite dropped %r)",
                number,
            )
            return original
    # 2b. Fact invention: the rewrite must not ADD numbers either — an
    # invented metric reached a rendered resume on 2026-07-09
    # ("added_unverified_number" validation warning, Foundation
    # Medicine). Rewritten numbers must be a subset of the original's.
    original_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", original_plain))
    for number in re.findall(r"\d+(?:[.,]\d+)?", rewritten_plain):
        if number not in original_numbers:
            logger.info(
                "rewrite guard: keeping original bullet (rewrite ADDED %r)",
                number,
            )
            return original
    # 3. Thesaurus inflation.
    if len(rewritten_plain.split()) > len(original_plain.split()) * 1.35 + 3:
        logger.info("rewrite guard: keeping original bullet (rewrite inflated length)")
        return original

    return rewritten


_LENGTH_REWRITE_SYSTEM = """You are a resume bullet editor adjusting bullet
length while preserving every grounded fact.

Return ONLY a JSON object with exactly this shape:
{
  "rewritten_bullet": "...",
  "used_skills": [],
  "source_ids": [],
  "confidence": "high" | "medium" | "low",
  "changed_claims": []
}

Rules:
- Preserve every claim, number, metric, and named technology from the original.
- Do NOT invent skills, technologies, accomplishments, or projects.
- Stay professional and concise; one bullet, one sentence (or two short ones).
- changed_claims must list any phrasing whose grounding is even slightly uncertain.
- Do NOT use em dashes or en dashes (—, –). Use a comma, period, or
  semicolon instead; ordinary hyphens in compound words are fine.

Inline markup you MAY use sparingly inside ``rewritten_bullet``:
- ``**text**`` -- bold for ONE or TWO key skills, named technologies,
  or quantified outcomes per bullet (e.g. "**FastAPI**", "**1.5M+
  requests/day**"). Never bold a whole sentence.
- ``*text*`` -- italics for proper nouns, paper / product / project
  names (e.g. "*Operating Systems: Three Easy Pieces*"). Never italicise
  ordinary technical terms.
- Do NOT use any other Markdown -- no headings, no links, no code
  fences, no ``_underscores_``. Underscores stay literal so identifiers
  like ``model_v2`` render correctly.
- Markup is OPTIONAL. If a bullet does not have an obvious key term to
  highlight, leave it plain.
"""


def _rewrite_bullet_for_length(
    bullet: str,
    *,
    direction: str,
    target_words: int,
) -> str:
    """Ask the LLM to make a bullet roughly ``target_words`` long.

    ``direction`` is ``"shorter"`` or ``"longer"`` -- chosen by the
    caller from the rendered page-count delta so the prompt explicitly
    asks for the right adjustment instead of letting the model guess.
    Raises :class:`LLMError` on bad output so the caller can fall back
    to the original bullet for that round.
    """
    from src.utils.llm import generate_json  # noqa: PLC0415

    instruction = (
        f"Make this bullet noticeably {direction}, around {target_words} words. "
        "Keep every grounded fact, number, and named tool. Do not invent any "
        "new claim."
    )
    prompt = (
        f"Original bullet:\n{bullet}\n\n"
        f"{instruction}\n\n"
        "Return the JSON object specified by the system instructions."
    )
    result = generate_json(prompt, system=_LENGTH_REWRITE_SYSTEM, timeout=300)
    if isinstance(result, str):
        return _clean_llm_bullet_rewrite_output(result, bullet)
    parsed = BulletRewriteResult.model_validate(result)
    return _clean_llm_bullet_rewrite_output(parsed.rewritten_bullet, bullet)


def _resize_document_bullets(
    document: ResumeDocument,
    *,
    direction: str,
    target_words: int,
) -> ResumeDocument:
    """Return a copy of ``document`` whose experience/project bullets have
    been rewritten for length via the LLM.

    Failures on individual bullets (LLMError) silently keep the original
    text for that bullet so a single bad rewrite does not abort the
    whole convergence loop.
    """
    from src.utils.llm import LLMError  # noqa: PLC0415

    updated = document.model_copy(deep=True)
    for section in (updated.experiences, updated.projects):
        for item in section:
            for bullet in item.bullets:
                if not bullet.text:
                    continue
                try:
                    bullet.text = _rewrite_bullet_for_length(
                        bullet.text,
                        direction=direction,
                        target_words=target_words,
                    )
                except (LLMError, Exception) as exc:  # noqa: BLE001
                    logger.debug("Length rewrite kept original bullet (%s): %s", direction, exc)
    return updated


# Upper bound on Fit-Planner rounds. Each round = at most 1 LLM call +
# 1 render + 1 PDF convert. Two rounds keep the total wall time within
# the front-end's poll budget even on slow LLM providers.
_MAX_FIT_PLAN_ROUNDS = 1


def _render_resume_to_target_pages(
    *,
    resume_document: ResumeDocument,
    template_path: Path,
    template_manifest: TemplateManifest,
    docx_output: Path,
    pdf_output: Path,
    target_pages: int,
    use_llm: bool = True,
    jd_tags: list[str] | None = None,
) -> tuple[ResumeDocument, Path, Path | None]:
    """Render the resume, then drive it to exactly ``target_pages`` using
    a single-shot **Fit Planner** LLM call per attempt.

    Why this design (replacing the per-bullet rewrite loop)
    --------------------------------------------------------
    The previous loop rewrote every selected bullet via the LLM up to
    two rounds. With ~10 bullets and a 60 s per-call timeout, the worst
    case blew past the front-end's 2-minute poll budget and the
    operator saw "no worker completed it" even though the task was
    still grinding.

    More importantly, per-bullet rewriting is the *wrong* abstraction.
    The smart move when a resume runs over (or under) the page target
    is to decide at the **section level** what stays, what shrinks,
    and what gets dropped -- e.g. cut "INTERESTS & ACTIVITIES" for a
    backend-engineer role rather than trim sentences of real evidence.
    The LLM has the relevance judgement to make that call when it sees
    the full outline.

    Convergence loop
    ----------------
    1. Render initial draft, check PDF page count.
    2. If wrong, send ONE LLM call with a compact section outline
       (canonical sections + custom_sections + bullet counts + JD tags)
       and ask for a JSON fit plan: per-section ``keep`` / ``max_items``
       / ``bullets_mode`` decisions plus the LLM's reasoning.
    3. Apply the plan deterministically -- drop sections marked drop,
       trim each section to its ``max_items``, batch-rewrite bullets
       only when the plan asked for it.
    4. Re-render. Repeat up to :data:`_MAX_FIT_PLAN_ROUNDS` rounds.
    5. Final deterministic fallback for stubborn overflow: drop the
       weakest bullet until pages fit or the resume can't shrink more.

    Returns (final_document, docx_path, pdf_path | None).
    """
    from src.documents.page_count import get_pdf_page_count  # noqa: PLC0415

    def _render(doc):
        path = build_resume_from_ir(
            template_path=template_path,
            document=doc,
            output_path=docx_output,
            manifest=template_manifest,
        )
        pdf: Path | None = None
        try:
            pdf = convert_to_pdf(path, pdf_output)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF conversion failed: %s", exc)
        pages = get_pdf_page_count(pdf) if pdf else None
        return path, pdf, pages

    current = resume_document
    docx_path, pdf_path, pages = _render(current)
    if target_pages <= 0 or pages is None:
        return current, docx_path, pdf_path

    jd_tags = jd_tags or list(current.metadata.get("jd_tags", []) or [])
    plan_history: list[str] = []

    for round_idx in range(_MAX_FIT_PLAN_ROUNDS if use_llm else 0):
        if pages == target_pages:
            return current, docx_path, pdf_path

        try:
            plan = _generate_resume_fit_plan(
                document=current,
                target_pages=target_pages,
                current_pages=pages,
                jd_tags=jd_tags,
                previous_plans=plan_history,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Resume fit-planner round %d failed (%s); falling back to deterministic trim.",
                round_idx + 1,
                exc,
            )
            break

        plan_history.append(_summarise_plan(plan))
        logger.info(
            "Resume fit-plan round %d/%d (pages %d -> target %d): %s",
            round_idx + 1,
            _MAX_FIT_PLAN_ROUNDS,
            pages,
            target_pages,
            plan_history[-1],
        )
        current = _apply_fit_plan(current, plan)
        docx_path, pdf_path, pages = _render(current)

    # Deterministic structural fallback: only used when the LLM plan
    # didn't converge. Overflow -> drop weakest bullet; underflow is
    # left to the validator since we will not fabricate content.
    for attempt in range(_MAX_TRIM_ATTEMPTS):
        if pages == target_pages:
            break
        if pages < target_pages:
            logger.info(
                "Resume under target pages (%d < %d) after fit planner; "
                "cannot fabricate content -- validator will flag.",
                pages,
                target_pages,
            )
            break
        trimmed = _drop_weakest_bullet(current)
        if trimmed is None:
            logger.info(
                "Resume already minimal; cannot trim further to hit %d pages (currently %d).",
                target_pages,
                pages,
            )
            break
        logger.info(
            "Resume rendered %d pages > target %d; deterministic bullet drop %d/%d.",
            pages,
            target_pages,
            attempt + 1,
            _MAX_TRIM_ATTEMPTS,
        )
        current = trimmed
        docx_path, pdf_path, pages = _render(current)

    return current, docx_path, pdf_path


def _drop_weakest_bullet(document: ResumeDocument) -> ResumeDocument | None:
    """Return a copy of ``document`` with one bullet (or empty item) removed.

    Picks the lowest-scoring bullet across projects + experiences so the
    most relevant evidence stays on the page. When a section item has
    no bullets left after a previous trim, the whole item is removed
    instead. Returns None when there is no more content to drop.
    """
    trimmed = document.model_copy(deep=True)

    # 1. Drop any item that has been fully emptied by prior trims.
    for section_name in ("projects", "experiences"):
        section = getattr(trimmed, section_name)
        empty_idx = next((idx for idx, item in enumerate(section) if not item.bullets), None)
        if empty_idx is not None:
            section.pop(empty_idx)
            return trimmed

    # 2. Otherwise drop the single weakest bullet across both sections.
    weakest: tuple[list, int, float] | None = None
    for section in (trimmed.projects, trimmed.experiences):
        for item in section:
            for idx, bullet in enumerate(item.bullets):
                score = float(getattr(bullet, "score", 0.0) or 0.0)
                if weakest is None or score < weakest[2]:
                    weakest = (item.bullets, idx, score)
    if weakest is None:
        return None
    bullets, idx, _ = weakest
    bullets.pop(idx)
    return trimmed


# ---------------------------------------------------------------------------
# Fit Planner: single-shot section-level decisions
# ---------------------------------------------------------------------------

_FIT_PLAN_SYSTEM = """You are a resume editor making a SINGLE structural
decision to fit a resume to an exact page target. You see every section
in the candidate's resume (canonical: experiences / projects / education
/ skills, plus any custom sections such as VOLUNTEER, AWARDS,
AFFILIATIONS, INTERESTS, CERTIFICATIONS, PUBLICATIONS). For each section,
decide whether to keep it, how many items to keep, whether to ask for
shorter bullets, and whether to place a horizontal divider after it for
visual grouping.

Return ONLY a JSON object of this exact shape:
{
  "reasoning": "1-2 sentence summary of your decision",
  "sections": [
    {
      "id": "<the id from the outline>",
      "keep": true | false,
      "max_items": <int or null>,
      "bullets_mode": "shorter" | "keep" | "longer",
      "divider_after": true | false
    }
  ]
}

Decision rules:
- The candidate's JD-relevant evidence (experiences + projects with
  high-overlap tags) is the LAST thing you should cut.
- Drop low-relevance custom sections FIRST when over budget. For a
  backend engineer role, INTERESTS or PROFESSIONAL AFFILIATIONS are
  usually cheaper to lose than a Volunteer Engineering experience.
- Never set keep=false on experiences or skills -- they are required.
  Education and Projects are required for student / new-grad resumes;
  mark keep=false only if the candidate has plenty of full-time work.
- max_items=null means keep all items in that section.
- bullets_mode applies to experiences / projects / volunteer-like
  sections that carry bullet text. Use shorter when over-budget,
  longer when under-budget, keep when length is fine.
- divider_after=true inserts a thin horizontal rule after the section.
  Use SPARINGLY -- at most one or two per resume, only where the visual
  break clearly helps (e.g. between the experience block and a list of
  awards). Default to false. Skipped automatically for the very last
  rendered section.
- Include every section id from the outline in your response.
"""


def _generate_resume_fit_plan(
    *,
    document: ResumeDocument,
    target_pages: int,
    current_pages: int,
    jd_tags: list[str],
    previous_plans: list[str],
) -> dict[str, Any]:
    """Ask the LLM for a section-level plan to hit the page target.

    Single LLM round-trip. Returns ``{"reasoning": str, "sections":
    {<id>: {keep, max_items, bullets_mode}}}``. Raises if the LLM is
    unavailable or returns malformed JSON so the caller can fall back
    to deterministic trimming.
    """
    from src.generation._fit_planner_helpers import (  # noqa: PLC0415
        build_section_outline,
        coerce_optional_int,
    )
    from src.utils.llm import generate_json  # noqa: PLC0415

    outline = build_section_outline(document)
    over = current_pages > target_pages
    delta_word = (
        f"OVER target by {current_pages - target_pages} page(s)"
        if over
        else f"UNDER target by {target_pages - current_pages} page(s)"
    )
    history_block = ""
    if previous_plans:
        bullets = "\n".join(f"- {entry}" for entry in previous_plans)
        history_block = (
            "\n<previous_attempts>\n" + bullets + "\n</previous_attempts>\n"
            "Adjust more aggressively than last time -- the previous "
            "plan did not converge.\n"
        )

    jd_block = ", ".join(jd_tags[:25]) if jd_tags else "(none provided)"
    prompt = (
        "<target>\n"
        f"Target pages: {target_pages}\n"
        f"Current rendered pages: {current_pages} ({delta_word})\n"
        "</target>\n\n"
        "<jd_keywords>\n"
        f"{jd_block}\n"
        "</jd_keywords>\n\n"
        "<resume_outline>\n"
        f"{outline}\n"
        "</resume_outline>\n"
        f"{history_block}"
        "\nReturn the JSON object specified by the system instructions.\n"
    )

    result = generate_json(prompt, system=_FIT_PLAN_SYSTEM, timeout=300)
    if not isinstance(result, dict):
        raise ValueError(f"Fit plan response is not a JSON object: {type(result).__name__}")
    sections_raw = result.get("sections")
    if not isinstance(sections_raw, list) or not sections_raw:
        raise ValueError("Fit plan missing 'sections' list")

    by_id: dict[str, dict] = {}
    for entry in sections_raw:
        if not isinstance(entry, dict):
            continue
        section_id = str(entry.get("id") or "").strip()
        if not section_id:
            continue
        by_id[section_id] = {
            "keep": bool(entry.get("keep", True)),
            "max_items": coerce_optional_int(entry.get("max_items")),
            "bullets_mode": str(entry.get("bullets_mode") or "keep").lower(),
            "divider_after": bool(entry.get("divider_after", False)),
        }

    return {
        "reasoning": str(result.get("reasoning") or "").strip(),
        "sections": by_id,
    }


def _summarise_plan(plan: dict[str, Any]) -> str:
    """One-line plan summary for logs + the planner's previous_plans block."""
    from src.generation._fit_planner_helpers import summarise_plan  # noqa: PLC0415

    return summarise_plan(plan)


def _apply_fit_plan(document: ResumeDocument, plan: dict[str, Any]) -> ResumeDocument:
    """Deterministically apply a fit plan returned by the LLM.

    Delegates to the helper module so the planner/apply logic stays
    importable without pulling all of ``resume_builder`` (which has a
    heavy import surface). The per-bullet rewriter callable is injected
    here so the helper module never imports from this file.
    """
    from src.generation._fit_planner_helpers import apply_fit_plan  # noqa: PLC0415

    return apply_fit_plan(
        document,
        plan,
        bullet_rewriter=_rewrite_bullet_for_length,
    )
