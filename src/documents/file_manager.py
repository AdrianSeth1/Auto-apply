"""File naming, versioning, and output management.

Handles consistent naming of generated resume/cover letter files
and maintains a record of which file was used for each application.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger("autoapply.documents.file_manager")

DocumentType = Literal["resume", "cover"]

FILENAME_PATTERNS = ("company_role_date", "type_profile_seq", "type_custom_seq")
SEQ_STATE_FILENAME = ".template_seq.json"


def _slugify(value: str, *, max_len: int = 40) -> str:
    text = (value or "").lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:max_len].strip("_") or "untitled"


def make_filename(
    doc_type: DocumentType,
    company: str,
    role: str,
    date: datetime | None = None,
    ext: str = "docx",
) -> str:
    """Generate a standardized filename.

    Pattern: {type}_{company}_{role}_{date}.{ext}
    Special chars replaced with underscores, lowercased.

    Example: resume_stripe_backend_engineer_2026-04-02.docx
    """
    if date is None:
        date = datetime.now(UTC)
    return (
        f"{doc_type}_{_slugify(company)}_{_slugify(role)}_{date.strftime('%Y-%m-%d')}.{ext}"
    )


def next_template_sequence(
    output_dir: Path, template_id: str
) -> int:
    """Increment + return the monotonic sequence counter for a template.

    Persisted in ``data/output/.template_seq.json`` so re-runs do not
    reset the count. The file is rewritten atomically (write to a
    sibling tmp + replace) so a crash mid-write cannot truncate prior
    counters.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / SEQ_STATE_FILENAME
    state: dict[str, int] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Template seq state unreadable, resetting: %s", exc)
            state = {}
    next_value = int(state.get(template_id, 0)) + 1
    state[template_id] = next_value
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(state_path)
    return next_value


def make_filename_from_pattern(
    *,
    doc_type: DocumentType,
    ext: str,
    pattern: str,
    company: str,
    role: str,
    profile_name: str = "",
    custom_label: str = "",
    seq: int | None = None,
    date: datetime | None = None,
) -> str:
    """Render a filename for a configured template pattern.

    Falls back to the legacy ``company_role_date`` pattern when the
    pattern name is unknown or required inputs are missing -- the
    generator must always produce *some* filename, never crash.
    """
    if date is None:
        date = datetime.now(UTC)

    if pattern == "type_profile_seq":
        label = _slugify(profile_name) if profile_name else "profile"
        seq_text = f"{(seq or 1):03d}"
        return f"{doc_type}_{label}_{seq_text}.{ext}"
    if pattern == "type_custom_seq":
        label = _slugify(custom_label) if custom_label else _slugify(company) or "doc"
        seq_text = f"{(seq or 1):03d}"
        return f"{doc_type}_{label}_{seq_text}.{ext}"
    return make_filename(doc_type, company, role, date, ext)


def get_output_paths(
    output_dir: Path,
    company: str,
    role: str,
    date: datetime | None = None,
    *,
    pattern: str = "company_role_date",
    profile_name: str = "",
    custom_label: str = "",
    template_id: str | None = None,
) -> dict[str, Path]:
    """Return all output paths for a single application's documents.

    Accepts a configured ``pattern`` so per-template filename schemes
    take effect. When the pattern requires a sequence, the counter is
    consumed once per call (paired across the resume + cover-letter
    triplets, so all three extensions share a single number for a
    given generation).
    """
    if date is None:
        date = datetime.now(UTC)

    output_dir.mkdir(parents=True, exist_ok=True)

    needs_seq = pattern in {"type_profile_seq", "type_custom_seq"}
    resume_seq = (
        next_template_sequence(output_dir, f"resume:{template_id or pattern}")
        if needs_seq
        else None
    )
    cover_seq = (
        next_template_sequence(output_dir, f"cover:{template_id or pattern}")
        if needs_seq
        else None
    )

    def _path(doc_type: DocumentType, ext: str, seq: int | None) -> Path:
        return output_dir / make_filename_from_pattern(
            doc_type=doc_type,
            ext=ext,
            pattern=pattern,
            company=company,
            role=role,
            profile_name=profile_name,
            custom_label=custom_label,
            seq=seq,
            date=date,
        )

    return {
        "resume_docx": _path("resume", "docx", resume_seq),
        "resume_pdf": _path("resume", "pdf", resume_seq),
        "resume_tex": _path("resume", "tex", resume_seq),
        "cover_docx": _path("cover", "docx", cover_seq),
        "cover_pdf": _path("cover", "pdf", cover_seq),
        "cover_tex": _path("cover", "tex", cover_seq),
    }


def list_generated_files(output_dir: Path, pattern: str = "*.pdf") -> list[Path]:
    """List all generated files in the output directory."""
    if not output_dir.exists():
        return []
    return sorted(output_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
