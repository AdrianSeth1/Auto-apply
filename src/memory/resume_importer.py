"""Resume importer.

Parses existing Word (.docx) or PDF resumes into structured YAML format
using Claude CLI for intelligent extraction.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from src.utils.llm import generate_text

logger = logging.getLogger("autoapply.memory.resume_importer")

EXTRACTION_SYSTEM_PROMPT = """You are a resume parser.
Extract structured data from the resume text provided.
Return ONLY valid YAML (no markdown fences, no explanations) matching this exact schema:

identity:
  full_name: "..."
  email: "..."
  phone: "..."
  location: "..."
  linkedin_url: "..."
  github_url: "..."
  portfolio_url: "..."

education:
  - institution: "..."
    degree: "..."
    field: "..."
    location: "..."
    start_date: "YYYY-MM"
    end_date: "YYYY-MM"
    gpa: "..."
    relevant_courses:
      - name: "..."
        tags: ["..."]

work_experiences:
  - company: "..."
    title: "..."
    location: "..."
    start_date: "YYYY-MM"
    end_date: "YYYY-MM"
    bullets:
      - text: "exact bullet text from resume"
        tags: ["skill1", "skill2"]

projects:
  - name: "..."
    role: "..."
    description: "..."
    tech_stack: ["..."]
    links: ["https://github.com/me/project", "..."]
    bullets:
      - text: "exact bullet text from resume"
        tags: ["skill1", "skill2"]

skills:
  languages: ["..."]
  frameworks: ["..."]
  databases: ["..."]
  tools: ["..."]
  domains: ["..."]

Rules:
- Preserve original bullet text exactly — do not rephrase or embellish
- Tags should be lowercase single words or short phrases
  (e.g., "python", "distributed_systems", "api_design")
- Dates in YYYY-MM format. Use "Present" for current positions
- If information is not in the resume, omit that field entirely
- For skills, categorize into the groups shown. If unsure of category, put in "tools"
- Hyperlinks in the input are rendered as ``"label (https://target)"``.
  Pull the URL out and assign it to the right structured field rather
  than leaving it inline:
    * GitHub link in the contact line → ``identity.github_url``
    * LinkedIn link in the contact line → ``identity.linkedin_url``
    * Personal site / portfolio in the contact line → ``identity.portfolio_url``
    * A URL attached to a project title (or anywhere in the project
      block that clearly points to the project itself) → add it to
      that project's ``links`` list. Multiple links are allowed.
  When you move a URL into a structured field, also remove the
  ``(https://…)`` suffix from the surrounding text so bullet/title
  strings stay clean.
"""


# Word's XML namespaces. We only need ``w`` (main document grammar)
# and ``r`` (relationship references) to find hyperlinks; declaring
# them here keeps the XPath expressions readable.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DOCX_NSMAP = {"w": _W_NS, "r": _R_NS}


def _render_paragraph_with_links(paragraph) -> str:
    """Walk a python-docx paragraph's XML children in document order
    and produce a plain-text rendering where hyperlinks are written
    as ``text (url)``.

    Why we bother: ``paragraph.text`` from python-docx concatenates
    every ``<w:t>`` element it can find, which yields just the
    *visible* text. Hyperlinks live inside ``<w:hyperlink>`` wrappers
    whose ``r:id`` attribute points into the document's relationships
    map -- the URL itself never appears inside the paragraph's runs,
    so the default ``.text`` accessor silently drops it. Resumes
    routinely hang the candidate's LinkedIn/GitHub off the contact
    line as hyperlinks, and project titles often link to a repo;
    losing those means the parsed profile has no way to recover them.

    Internal hyperlinks (anchors / bookmarks) and links whose
    relationship can't be resolved fall back to plain text rather
    than emitting a misleading ``()`` suffix.
    """
    rels = paragraph.part.rels
    pieces: list[str] = []

    for child in paragraph._element.iterchildren():
        tag = child.tag
        if tag == f"{{{_W_NS}}}r":
            # Plain run: just collect any <w:t> text under it.
            for t in child.iter(f"{{{_W_NS}}}t"):
                if t.text:
                    pieces.append(t.text)
        elif tag == f"{{{_W_NS}}}hyperlink":
            inner_text = "".join(
                (t.text or "") for t in child.iter(f"{{{_W_NS}}}t")
            )
            r_id = child.get(f"{{{_R_NS}}}id")
            url: str | None = None
            if r_id and r_id in rels:
                rel = rels[r_id]
                # ``is_external`` is False for anchor-only links
                # (e.g. ``<w:hyperlink w:anchor="..."/>``); those have
                # no URL we can preserve, so render the text only.
                if getattr(rel, "is_external", False):
                    url = rel.target_ref or None
            if inner_text and url and url not in inner_text:
                pieces.append(f"{inner_text} ({url})")
            elif inner_text:
                pieces.append(inner_text)
        # Other children (bookmarks, proofErr, etc.) carry no
        # user-visible text; ignore them.

    return "".join(pieces).strip()


def extract_text_from_docx(path: Path) -> str:
    """Extract plain text from a .docx file, preserving hyperlinks.

    Hyperlinks are rendered as ``"link text (https://target)"`` inline
    with the surrounding text so the downstream LLM has both the
    human-readable label and the URL when it builds the structured
    profile (see ``_render_paragraph_with_links`` for the rationale).
    """
    from docx import Document

    doc = Document(str(path))
    paragraphs: list[str] = []

    for para in doc.paragraphs:
        rendered = _render_paragraph_with_links(para)
        if rendered:
            paragraphs.append(rendered)

    # Also extract from tables (common in resume templates). We
    # iterate cell.paragraphs rather than cell.text so hyperlinks
    # inside tables get the same treatment.
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    rendered = _render_paragraph_with_links(para)
                    if rendered:
                        paragraphs.append(rendered)

    return "\n".join(paragraphs)


def extract_text_from_pdf(path: Path) -> str:
    """Extract plain text from a PDF file."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        return "\n".join(text_parts)
    except ImportError:
        raise ImportError(
            "PyMuPDF (fitz) is required for PDF parsing. Install with: uv add pymupdf"
        )


def _strip_markdown_fences(text: str) -> str:
    """Remove ```yaml ... ``` style markdown fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines)
    return cleaned.strip()


def _strip_chatty_preamble(text: str) -> str:
    """Drop common LLM preambles like 'Here is the YAML:' that sit
    before the first real YAML key. We look for the first line that
    looks like a top-level mapping key (``identity:``, ``education:``,
    etc.) and discard anything above it. If no such line exists, the
    text is returned unchanged.
    """
    expected_top_keys = (
        "identity:",
        "education:",
        "work_experiences:",
        "projects:",
        "skills:",
    )
    lines = text.split("\n")
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if any(stripped.startswith(key) for key in expected_top_keys):
            return "\n".join(lines[idx:])
    return text


def _parse_llm_yaml_response(response: str) -> dict:
    """Best-effort parser for the LLM's YAML reply.

    The LLM is instructed to return bare YAML, but in practice it
    sometimes wraps the output in markdown fences, prepends a chatty
    preamble (\"Here is the structured YAML:\"), or returns the whole
    payload double-encoded as a single string. We unwrap those cases
    before giving up so that a transient phrasing tic on the model's
    side doesn't surface as a hard parse failure to the user.

    Raises:
        ValueError: With a snippet of the actual response so the
            operator can see what the model returned.
    """
    cleaned = _strip_markdown_fences(response)
    cleaned = _strip_chatty_preamble(cleaned)

    try:
        parsed = yaml.safe_load(cleaned)
    except yaml.YAMLError as e:
        logger.error("Failed to parse LLM response as YAML: %s", e)
        logger.error("Raw LLM response (first 500 chars):\n%s", cleaned[:500])
        raise ValueError(
            f"LLM returned invalid YAML: {e}. Response started with: "
            f"{cleaned[:200]!r}"
        )

    # If the model wrapped the whole document in quotes, safe_load gives
    # us a string — try reparsing the inner content once.
    if isinstance(parsed, str):
        inner = _strip_markdown_fences(parsed)
        inner = _strip_chatty_preamble(inner)
        try:
            reparsed = yaml.safe_load(inner)
        except yaml.YAMLError:
            reparsed = None
        if isinstance(reparsed, dict):
            return reparsed

        logger.error(
            "LLM returned a scalar string instead of a YAML mapping. "
            "First 500 chars:\n%s",
            cleaned[:500],
        )
        raise ValueError(
            "LLM did not return a structured resume — got plain text. "
            f"First 200 chars: {cleaned[:200]!r}"
        )

    if not isinstance(parsed, dict):
        logger.error(
            "LLM response parsed to %s, expected dict. First 500 chars:\n%s",
            type(parsed).__name__,
            cleaned[:500],
        )
        raise ValueError(
            f"Expected a YAML mapping from the LLM, got {type(parsed).__name__}. "
            f"First 200 chars: {cleaned[:200]!r}"
        )

    return parsed


def import_resume(resume_path: Path, output_path: Path | None = None) -> dict:
    """Import a resume file and convert to structured YAML.

    Args:
        resume_path: Path to .docx or .pdf resume file.
        output_path: Optional path to save the generated YAML.

    Returns:
        Parsed profile data as a dict.
    """
    suffix = resume_path.suffix.lower()
    if suffix == ".docx":
        raw_text = extract_text_from_docx(resume_path)
    elif suffix == ".pdf":
        raw_text = extract_text_from_pdf(resume_path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Use .docx or .pdf")

    if not raw_text.strip():
        raise ValueError(f"No text extracted from {resume_path}")

    logger.info("Extracted %d chars from %s", len(raw_text), resume_path.name)

    # Resume import is pure extraction (raw resume text -> structured
    # YAML) so route it via the Phase 17.9.5 small tier when configured.
    # A user setting llm.small_provider / llm.small_model can shave
    # tokens here without affecting creative paths like cover-letter
    # generation. No small config? The dispatcher uses the primary chain.
    prompt = f"Parse this resume into structured YAML:\n\n{raw_text}"
    response = generate_text(
        prompt, system=EXTRACTION_SYSTEM_PROMPT, timeout=180, tier="small"
    )

    profile_data = _parse_llm_yaml_response(response)

    # Save to file if output path specified
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(
                profile_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False
            )
        logger.info("Saved structured profile to %s", output_path)

    return profile_data
