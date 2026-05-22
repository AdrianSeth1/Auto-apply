"""Helpers shared by the DOCX and LaTeX renderers.

Kept in a dedicated module so cover-letter address-block rules,
divider matching, and field-cleaning live in exactly one place. Both
``docx_engine`` and ``latex_engine`` import from here.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def clean_field(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a", "na", "nan"}:
        return ""
    return text


def normalise_divider_set(raw: Any) -> set[str]:
    if not raw:
        return set()
    out: set[str] = set()
    for value in raw:
        token = str(value or "").strip().lower()
        if not token:
            continue
        out.add(token)
        if token.startswith("custom:"):
            out.add(token.split(":", 1)[1].strip())
    return out


def section_wants_divider(section: str, dividers_after: set[str]) -> bool:
    if not dividers_after:
        return False
    section_lower = (section or "").strip().lower()
    if section_lower in dividers_after:
        return True
    if section_lower.startswith("custom:"):
        title = section_lower.split(":", 1)[1].strip()
        if title in dividers_after:
            return True
    return False


def cover_letter_date() -> str:
    today = datetime.now()
    return f"{today:%B} {today.day}, {today:%Y}"


def clean_cover_letter_location(value: Any) -> str:
    text = clean_field(value)
    if not text:
        return ""
    text = re.sub(r"\s*\([^)]*\)", "", text)
    text = re.sub(
        r"\s*(?:[-–—|,]|\b)\s*(?:hybrid|remote|on-?site|in-person)\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip(" ,-|–—")


def cover_letter_contact_lines(applicant: dict[str, Any]) -> list[str]:
    return [
        line
        for line in (
            clean_cover_letter_location(applicant.get("location")),
            clean_field(applicant.get("phone")),
            clean_field(applicant.get("email")),
        )
        if line
    ]


def cover_letter_recipient_lines(recipient: dict[str, Any]) -> list[str]:
    hiring_manager = clean_field(recipient.get("hiring_manager"))
    company = clean_field(recipient.get("company"))
    location = clean_cover_letter_location(recipient.get("location"))
    lines: list[str] = []
    if hiring_manager:
        lines.append(hiring_manager)
    elif company:
        lines.append("Hiring Team")
    if company:
        lines.append(company)
    if location:
        lines.append(location)
    return lines
