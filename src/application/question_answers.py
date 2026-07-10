"""Collaborative application-question answering (Materials → Questions).

Flow: the user pastes an open-ended application question ("give us one
or two examples of exceptional performance"). We draft an answer
grounded in the profile + story bank + saved QA bank, and — where the
profile genuinely lacks the needed detail — return up to three
clarifying questions for the USER. The user answers those, we redraft
with their notes folded in, and the final answer can be saved to the
QA bank so the form-filler and future drafts reuse it.

Grounding contract: same as the rest of generation — the LLM may only
use facts from the profile, story bank, and the user's own clarifying
answers. No invented achievements.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.core.config import load_config

logger = logging.getLogger("autoapply.application.question_answers")

_MAX_CLARIFYING_QUESTIONS = 3

_SYSTEM = """You help a job applicant answer application-form questions.

Hard rules:
- Ground every claim in the applicant profile, story bank, or the
  applicant's own clarifying notes provided in the prompt. NEVER invent
  experiences, metrics, employers, or dates.
- Write in first person, confident but concrete. Prefer specific
  outcomes over adjectives.
- Length: match the question; default 80-160 words for open-ended
  questions, shorter for direct ones.
- If the profile lacks information that would make the answer stronger,
  ask the APPLICANT for it via clarifying_questions (max 3, specific,
  answerable in a sentence). Ask nothing if the material is sufficient.

Output STRICT JSON, nothing else:
{"answer": "<the drafted answer>",
 "clarifying_questions": ["<question for the applicant>", ...]}"""


def draft_question_answer(
    *,
    question: str,
    company: str = "",
    title: str = "",
    profile_id: str | None = None,
    clarifications: list[dict[str, str]] | None = None,
) -> dict:
    """Draft (or refine) an answer for one application question.

    ``clarifications`` is a list of ``{"question", "answer"}`` pairs the
    user already answered — present on the second round.
    """
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "Question is empty.", "error_code": "empty_question"}

    profile = _load_profile(profile_id)
    if profile is None:
        return {"ok": False, "error": "No applicant profile available.", "error_code": "profile_missing"}

    from src.generation.qa_responder import classify_question

    prompt = _build_prompt(
        question=question,
        company=company,
        title=title,
        profile=profile,
        clarifications=clarifications or [],
        similar_saved=_similar_saved_answers(question),
    )

    try:
        from src.utils.llm import generate_text

        raw = generate_text(prompt, system=_SYSTEM, timeout=120)
    except Exception as exc:  # noqa: BLE001 -- provider down -> structured error
        logger.exception("Question drafting failed")
        return {"ok": False, "error": f"LLM generation failed: {exc}", "error_code": "llm_failed"}

    answer, clarifying = _parse_response(raw)
    if not answer:
        return {
            "ok": False,
            "error": "The model returned an unusable response. Try again.",
            "error_code": "unparseable_response",
        }

    return {
        "ok": True,
        "error": None,
        "question": question,
        "question_type": classify_question(question),
        "answer": answer,
        "clarifying_questions": clarifying[:_MAX_CLARIFYING_QUESTIONS],
        "final": not clarifying,
    }


def save_question_answer(*, question: str, answer: str) -> dict:
    """Persist a finished answer to the QA bank for reuse.

    The form-filler's confidence cascade checks the QA bank first, so a
    saved answer is used verbatim next time a similar question appears.
    """
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not answer:
        return {"ok": False, "error": "Question and answer are required.", "error_code": "invalid_input"}

    from src.core.database import get_session_factory
    from src.core.models import QABank
    from src.generation.qa_responder import classify_question

    try:
        session_factory = get_session_factory(load_config())
        with session_factory() as session, session.begin():
            existing = (
                session.query(QABank)
                .filter(QABank.question_pattern == question)
                .one_or_none()
            )
            if existing is not None:
                existing.canonical_answer = answer
                existing.confidence = "high"
                existing.needs_review = False
                entry_id = existing.id
                status = "updated"
            else:
                entry = QABank(
                    question_pattern=question,
                    question_type=classify_question(question),
                    canonical_answer=answer,
                    confidence="high",
                    needs_review=False,
                )
                session.add(entry)
                session.flush()
                entry_id = entry.id
                status = "created"
    except Exception as exc:  # noqa: BLE001
        logger.exception("QA bank save failed")
        return {"ok": False, "error": str(exc), "error_code": "db_save_failed"}

    return {"ok": True, "error": None, "status": status, "id": str(entry_id), **list_saved_answers()}


def list_saved_answers() -> dict:
    """All QA bank entries, newest-ish first, for the Questions tab."""
    from src.core.database import get_session_factory
    from src.core.models import QABank

    try:
        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            rows = session.query(QABank).all()
            entries = [
                {
                    "id": str(row.id),
                    "question": row.question_pattern,
                    "question_type": row.question_type,
                    "answer": row.canonical_answer,
                    "confidence": row.confidence,
                    "needs_review": row.needs_review,
                }
                for row in reversed(rows)
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("QA bank list failed: %s", exc)
        return {"entries": [], "list_error": str(exc)}
    return {"entries": entries, "list_error": None}


def delete_saved_answer(*, entry_id: str) -> dict:
    from uuid import UUID

    from src.core.database import get_session_factory
    from src.core.models import QABank

    try:
        entry_uuid = UUID(entry_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid entry id.", "error_code": "invalid_id"}
    try:
        session_factory = get_session_factory(load_config())
        with session_factory() as session, session.begin():
            row = session.get(QABank, entry_uuid)
            if row is None:
                return {"ok": False, "error": "Entry not found.", "error_code": "not_found"}
            session.delete(row)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "error_code": "db_delete_failed"}
    return {"ok": True, "error": None, **list_saved_answers()}


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _load_profile(profile_id: str | None) -> dict | None:
    from src.application.profile import get_active_profile_path, get_profile_path
    from src.memory.profile import load_profile_yaml

    candidates = []
    if profile_id:
        candidates.append(get_profile_path(profile_id))
    active = get_active_profile_path()
    if active is not None:
        candidates.append(active)
    for path in candidates:
        if path and path.exists():
            try:
                return load_profile_yaml(path)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to load profile %s", path)
    return None


def _similar_saved_answers(question: str, limit: int = 3) -> list[dict]:
    """Cheap token-overlap lookup into the saved QA bank for context."""
    saved = list_saved_answers().get("entries", [])
    if not saved:
        return []
    q_tokens = set(re.findall(r"[a-z0-9]+", question.lower()))
    scored = []
    for entry in saved:
        text = (entry.get("question") or "").lower()
        overlap = len(q_tokens & set(re.findall(r"[a-z0-9]+", text)))
        if overlap >= 3:
            scored.append((overlap, entry))
    scored.sort(key=lambda pair: -pair[0])
    return [entry for _, entry in scored[:limit]]


def _build_prompt(
    *,
    question: str,
    company: str,
    title: str,
    profile: dict,
    clarifications: list[dict[str, str]],
    similar_saved: list[dict],
) -> str:
    parts = [f"<question>{question}</question>"]
    if company or title:
        parts.append(f"<target_role>{title or 'unknown role'} at {company or 'unknown company'}</target_role>")

    parts.append(f"<applicant_profile>\n{json.dumps(_profile_digest(profile), indent=1)}\n</applicant_profile>")

    stories = profile.get("story_bank") or []
    if stories:
        parts.append(f"<story_bank>\n{json.dumps(stories, indent=1)}\n</story_bank>")

    if similar_saved:
        parts.append(
            "<previously_saved_answers note='answers the applicant already approved for similar questions'>\n"
            + json.dumps(
                [{"question": s["question"], "answer": s["answer"]} for s in similar_saved],
                indent=1,
            )
            + "\n</previously_saved_answers>"
        )

    if clarifications:
        parts.append(
            "<applicant_clarifications note='the applicant answered your earlier questions; "
            "treat these as ground truth and fold them into the answer'>\n"
            + json.dumps(clarifications, indent=1)
            + "\n</applicant_clarifications>"
        )
        parts.append(
            "The applicant has already answered your clarifying questions. Produce the best "
            "final answer; only ask again if something CRITICAL is still missing."
        )

    return "\n\n".join(parts)


def _profile_digest(profile: dict) -> dict:
    """Trim the profile to what answer-writing needs (keeps prompts small)."""
    return {
        "identity": {
            key: value
            for key, value in (profile.get("identity") or {}).items()
            if key in ("full_name", "location", "work_authorization", "willing_to_relocate")
        },
        "education": profile.get("education"),
        "work_experiences": profile.get("work_experiences"),
        "projects": profile.get("projects"),
        "skills": profile.get("skills"),
    }


def _parse_response(raw: str) -> tuple[str, list[str]]:
    """Extract {answer, clarifying_questions} from model output.

    Tolerates markdown fences and prose around the JSON. Falls back to
    treating the whole output as the answer when JSON parsing fails —
    a usable draft beats an error.
    """
    text = (raw or "").strip()
    if not text:
        return "", []

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group(0))
            answer = str(payload.get("answer") or "").strip()
            questions = [
                str(q).strip()
                for q in (payload.get("clarifying_questions") or [])
                if str(q).strip()
            ]
            if answer:
                return answer, questions
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

    # Fallback: strip any fence markers and use the raw text.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    return text, []
