"""Regression tests for _trim_words: bullets must never end mid-phrase.

Found 2026-07-02: generated resumes shipped with bullets like
"... Piper TTS for spoken" because the pre-render fitter sliced the word
list at the template's max_words_per_bullet and stripped punctuation.
"""

from __future__ import annotations

from src.generation.fitting import _trim_words


def test_total_bullet_cap_preserves_professional_evidence_floor():
    from src.generation.fitting import _fit_total_bullets
    from src.generation.ir import ResumeBullet, ResumeDocument, ResumeItem

    def item(source_id: str, source_type: str, scores: list[float]) -> ResumeItem:
        return ResumeItem(
            source_id=source_id,
            source_type=source_type,
            name=source_id,
            bullets=[
                ResumeBullet(
                    text=f"Evidence {source_id} {index}",
                    score=score,
                    source_id=f"{source_id}:{index}",
                    source_entity=source_id,
                )
                for index, score in enumerate(scores)
            ],
        )

    document = ResumeDocument(
        target_role="Analyst",
        company="Acme",
        experiences=[
            item("exp-strong", "experience", [10, 9, 8]),
            item("exp-second", "experience", [7, 6, 5]),
            item("exp-third", "experience", [4, 3]),
        ],
        projects=[item("project", "project", [20, 19, 18])],
    )

    _fit_total_bullets(document, 6)

    assert [len(entry.bullets) for entry in document.experiences] == [2, 2, 1]
    assert len(document.projects[0].bullets) == 1


def test_under_limit_is_untouched():
    text = "Built a small tool for the lab."
    assert _trim_words(text, 24) == text


def test_no_limit_is_untouched():
    text = "word " * 60
    assert _trim_words(text.strip(), None) == text.strip()


def test_never_ends_mid_phrase_smarsh_regression():
    # Real bullet from a shipped resume that was cut to
    # "... Piper TTS for spoken".
    text = (
        "Built full RAG pipeline — faster-whisper for live transcription, "
        "ChromaDB + local LLMs for Q&A over course materials, Piper TTS for "
        "spoken answers, all running locally with no cloud dependencies."
    )
    result = _trim_words(text, 22)
    assert result.endswith((".", "!", "?"))
    assert not result.endswith("for spoken.")
    # Cut must land on a clause boundary from the original text.
    assert result.rstrip(".") in text


def test_prefers_clause_boundary_within_budget():
    text = (
        "Standardized the onboarding flow across teams; documented every "
        "step in a shared runbook that new hires can follow without help "
        "from the platform group or any senior engineer on rotation."
    )
    result = _trim_words(text, 12)
    assert result == "Standardized the onboarding flow across teams."


def test_returns_full_text_when_no_boundary_in_budget():
    # 30 words, no internal punctuation: better to keep it whole and let
    # the page-fit loop shorten/drop it than to amputate mid-sentence.
    text = " ".join(f"word{i}" for i in range(30))
    assert _trim_words(text, 20) == text


def test_does_not_orphan_inline_bold_markup():
    text = (
        "Shipped the **FastAPI ingestion service and React dashboard** to "
        "production, cutting manual review time in half for the operations "
        "team across every region we support today."
    )
    result = _trim_words(text, 10)
    # Budget lands inside the bold span; there is no safe clause boundary
    # outside it, so the text must come back untouched.
    assert result == text
