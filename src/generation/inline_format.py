"""Markdown-ish inline markup parser shared by every renderer.

The Fit Planner LLM (and the per-bullet rewriter) emit resume / cover-letter
text with a tiny set of inline markers so an Arial 9pt body can still
highlight a metric or a tool name without trying to inject Word XML
through the prompt. The supported markers are deliberately narrow:

* ``**bold text**`` -- emphasis for key skills, named technologies,
  quantified outcomes ("**1.5M+ requests/day**").
* ``*italic text*`` -- titles of papers / projects, foreign-language
  phrases, conventional italicisation. Single ``*`` only -- ``_`` is
  NOT a synonym so the parser cannot get confused by underscores
  inside identifiers (``model_v2``).
* ``---`` standing alone on its own paragraph -- a horizontal divider
  between sections. The IR ``ResumeDocument.dividers_after`` field is
  the structured way to ask for one; this inline form exists for
  cases where the LLM emits a divider mid-paragraph.

We intentionally do NOT support tables, links, headings, or arbitrary
HTML through the markers. Anything richer needs to land as a typed IR
field so two renderers do not drift, and so the validator can see what
the LLM is doing.

Public surface:

* :class:`InlineRun` -- one slice of text + bold/italic flags.
* :func:`parse_inline_markup` -- return ``list[InlineRun]`` for a
  string. Always returns at least one run (possibly empty text) so
  callers can blindly iterate.
* :func:`strip_inline_markup` -- raw text with markers removed. Used by
  validators and the bullet pool so a duplicate-detection check is not
  fooled by formatting differences.
* :data:`DIVIDER_PARAGRAPH_MARKER` -- the literal ``---`` that the
  renderers swap for an HR when it stands alone as a paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


DIVIDER_PARAGRAPH_MARKER = "---"


@dataclass(frozen=True)
class InlineRun:
    """One contiguous chunk of inline text + formatting flags.

    The renderers treat runs as immutable so a single rendered paragraph
    can be assembled by appending ``add_run(run.text)`` calls (for DOCX)
    or concatenating ``\\textbf{...}``/``\\textit{...}`` (for LaTeX)
    without the caller having to re-parse markers.
    """

    text: str
    bold: bool = False
    italic: bool = False


# Token pattern handles all three markup forms in a single pass. The
# parser walks the string with ``finditer`` rather than greedy regex
# replacement so overlapping cases (``***triple***``) degrade to the
# longer match (``**`` wins, leaving a ``*x*`` italic inside).
_TOKEN_RE = re.compile(
    r"""
    (?P<bold>\*\*(?P<bold_inner>.+?)\*\*)        # **bold**
    |
    (?P<italic>(?<![A-Za-z0-9*])\*               # opening * not preceded by word char
        (?P<italic_inner>[^*\n]+?)\*             # ...content...
        (?![A-Za-z0-9*]))                        # closing * not followed by word char
    """,
    re.VERBOSE,
)


def parse_inline_markup(text: str | None) -> list[InlineRun]:
    """Split ``text`` into bold/italic-aware runs.

    Always returns at least one :class:`InlineRun`. A ``None`` /
    ``""`` input yields a single empty run so the renderer's loop
    bodies do not need to special-case it.

    Behaviour notes
    ---------------
    * Bold beats italic on the same span (``**x**`` is bold, never
      italic-of-italic).
    * Unmatched ``**`` / ``*`` are left as literal text -- the parser
      never deletes characters it cannot interpret. This matters when
      a real bullet contains, e.g., ``C*`` or a 5-star rating.
    * Newlines inside a markup span end the span -- markers must close
      on the same line so a bad LLM output cannot eat the rest of the
      document.
    """
    raw = text or ""
    if not raw:
        return [InlineRun(text="")]

    runs: list[InlineRun] = []
    cursor = 0
    for match in _TOKEN_RE.finditer(raw):
        start, end = match.span()
        if start > cursor:
            runs.append(InlineRun(text=raw[cursor:start]))
        if match.group("bold") is not None:
            inner = match.group("bold_inner")
            # Allow nested *italic* inside **bold** so the LLM can do
            # ``**important *italicised* phrase**`` cleanly.
            for nested in parse_inline_markup(inner):
                runs.append(
                    InlineRun(
                        text=nested.text,
                        bold=True,
                        italic=nested.italic,
                    )
                )
        else:
            runs.append(InlineRun(text=match.group("italic_inner"), italic=True))
        cursor = end

    if cursor < len(raw):
        runs.append(InlineRun(text=raw[cursor:]))

    # Drop empty runs that fell out of overlapping spans. Keep at least
    # one run -- the renderer's "for run in runs" loop should not be
    # surprised by an empty list.
    cleaned = [run for run in runs if run.text]
    return cleaned or [InlineRun(text="")]


def strip_inline_markup(text: str | None) -> str:
    """Plain-text version of ``text`` with markers removed.

    Used by validators (duplicate detection, word-count budgets) so a
    bullet rewritten with ``**FastAPI**`` highlights is not counted as
    a different sentence from its unformatted twin.
    """
    return "".join(run.text for run in parse_inline_markup(text))


def is_divider_paragraph(text: str | None) -> bool:
    """True if a paragraph's text is the inline divider marker.

    Renderers use this to decide whether to skip the normal paragraph
    body and emit a horizontal rule instead. We accept any line that is
    only dashes (``--``, ``---``, ``----``) so the LLM can author the
    divider naturally without worrying about exact length.
    """
    stripped = (text or "").strip()
    return bool(stripped) and set(stripped) == {"-"} and len(stripped) >= 2


__all__ = [
    "DIVIDER_PARAGRAPH_MARKER",
    "InlineRun",
    "is_divider_paragraph",
    "parse_inline_markup",
    "strip_inline_markup",
]
