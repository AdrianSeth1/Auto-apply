"""Gmail IMAP reply ingestion — turn recruiter emails into outcome updates.

Fetches recent inbox mail over IMAP (app password), classifies each
message with conservative keyword rules (rejection / interview / online
assessment), matches it to a submitted application by company name, and
proposes outcome updates. Also computes "no reply in N days" follow-up
nudges.

Config (config/settings.yaml)::

    email:
      enabled: true
      imap_host: imap.gmail.com
      username: you@gmail.com
      # Prefer the env var; the yaml key exists for convenience only.
      password_env: AUTOAPPLY_GMAIL_APP_PASSWORD
      app_password: ''
      lookback_days: 14
      followup_after_days: 10
      max_messages: 200

Safety properties:
  * Read-only IMAP (no deletes, no flags beyond what fetch implies).
  * Outcomes only ever ESCALATE (pending -> oa -> interview -> offer);
    ``rejected`` is always allowed. An interview never silently
    downgrades because a later marketing email matched.
  * Ambiguous messages (no classification or no unique application
    match) are reported, not applied.
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.config import load_config

logger = logging.getLogger("autoapply.intake.email_ingest")

# Outcome escalation order. An update is applied only when the new
# outcome ranks higher than the stored one, except "rejected" which is
# always applied (a rejection after an interview is real information).
_OUTCOME_RANK = {"pending": 0, "oa": 1, "interview": 2, "offer": 3}

_REJECTION_PATTERNS = (
    "unfortunately",
    "not moving forward",
    "not to move forward",
    "decided to move forward with other",
    "move forward with other candidates",
    "pursue other candidates",
    "other applicants",
    "no longer under consideration",
    "not selected",
    "position has been filled",
    "will not be progressing",
    "unable to offer you",
)
_OFFER_PATTERNS = (
    "pleased to offer",
    "excited to offer",
    "offer letter",
    "extend an offer",
)
_INTERVIEW_PATTERNS = (
    "schedule an interview",
    "schedule a call",
    "schedule some time",
    "phone screen",
    "phone interview",
    "video interview",
    "interview with",
    "invite you to interview",
    "like to interview",
    "book a time",
    "your availability",
)
_OA_PATTERNS = (
    "online assessment",
    "coding challenge",
    "coding assessment",
    "take-home",
    "take home assignment",
    "hackerrank",
    "codility",
    "codesignal",
    "plum assessment",
    "criteria assessment",
)
# Presence of any of these marks a message as application-related even
# before company matching (used to cut noise, not to classify).
_ATS_SENDER_HINTS = (
    "greenhouse",
    "lever.co",
    "ashbyhq",
    "myworkday",
    "smartrecruiters",
    "icims",
    "no-reply",
    "noreply",
    "talent",
    "recruiting",
    "careers",
)


def email_settings() -> dict[str, Any] | None:
    """Return resolved email config or ``None`` when disabled/unconfigured."""
    raw = load_config().get("email", {})
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return None
    username = str(raw.get("username") or "").strip()
    password_env = str(raw.get("password_env") or "AUTOAPPLY_GMAIL_APP_PASSWORD")
    password = os.environ.get(password_env) or str(raw.get("app_password") or "")
    if not username or not password:
        logger.warning(
            "Email ingestion enabled but username/app password missing "
            "(set email.username and the %s env var).",
            password_env,
        )
        return None
    return {
        "imap_host": str(raw.get("imap_host") or "imap.gmail.com"),
        "username": username,
        "password": password.replace(" ", ""),  # Google renders app passwords with spaces
        "lookback_days": int(raw.get("lookback_days") or 14),
        "followup_after_days": int(raw.get("followup_after_days") or 10),
        "max_messages": int(raw.get("max_messages") or 200),
    }


def ingest_replies(*, dry_run: bool = False) -> dict:
    """Fetch, classify, match, and (unless ``dry_run``) apply outcomes."""
    settings = email_settings()
    if settings is None:
        return {
            "ok": False,
            "error": (
                "Email ingestion is not configured. Set email.enabled, "
                "email.username in settings.yaml and the app password env var."
            ),
            "error_code": "email_not_configured",
            "processed": 0,
            "updates": [],
            "ambiguous": [],
            "followups": [],
        }

    try:
        messages = _fetch_recent_messages(settings)
    except Exception as exc:  # noqa: BLE001 -- IMAP failure -> structured error
        logger.exception("IMAP fetch failed")
        return {
            "ok": False,
            "error": f"IMAP fetch failed: {exc}",
            "error_code": "imap_fetch_failed",
            "processed": 0,
            "updates": [],
            "ambiguous": [],
            "followups": [],
        }

    try:
        applications = _load_open_applications()
    except Exception as exc:  # noqa: BLE001 -- DB down -> structured error, not a 500
        logger.warning("Email ingest: application load failed: %s", exc)
        return {
            "ok": False,
            "error": (
                "Could not load applications from the database (is Postgres "
                f"running?): {exc}"
            ),
            "error_code": "db_unavailable",
            "processed": len(messages),
            "updates": [],
            "ambiguous": [],
            "followups": [],
        }

    updates: list[dict] = []
    ambiguous: list[dict] = []

    for message in messages:
        outcome = classify_message(message["subject"], message["body"])
        if outcome is None:
            continue
        matches = match_applications(message, applications)
        summary = {
            "subject": message["subject"][:140],
            "from": message["from"][:140],
            "date": message["date"],
            "outcome": outcome,
        }
        if len(matches) != 1:
            ambiguous.append(
                {
                    **summary,
                    "reason": "no matching application"
                    if not matches
                    else f"matched {len(matches)} applications",
                    "candidates": [
                        f"{app['company']} — {app['title']}" for app in matches[:5]
                    ],
                }
            )
            continue
        app = matches[0]
        if not _should_escalate(app["outcome"], outcome):
            continue
        updates.append(
            {
                **summary,
                "application_id": app["id"],
                "company": app["company"],
                "title": app["title"],
                "previous_outcome": app["outcome"] or "pending",
            }
        )

    applied = 0
    if not dry_run:
        from uuid import UUID

        from src.application.tracking import update_application_outcome

        for update in updates:
            try:
                result = update_application_outcome(
                    application_id=UUID(update["application_id"]),
                    outcome=update["outcome"],
                )
                update["applied"] = bool(result.get("ok"))
                applied += 1 if update["applied"] else 0
                if update["applied"] and update["outcome"] == "interview":
                    from src.application.prep import (
                        maybe_generate_prep_pack_on_interview,
                    )

                    maybe_generate_prep_pack_on_interview(
                        application_id=UUID(update["application_id"]),
                        outcome="interview",
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Outcome update failed for %s", update["company"])
                update["applied"] = False
                update["error"] = str(exc)

    return {
        "ok": True,
        "error": None,
        "processed": len(messages),
        "classified": len(updates) + len(ambiguous),
        "applied": applied,
        "dry_run": dry_run,
        "updates": updates,
        "ambiguous": ambiguous,
        "followups": list_followup_candidates(
            after_days=settings["followup_after_days"]
        ),
    }


def classify_message(subject: str, body: str) -> str | None:
    """Classify one message. Conservative: None unless a pattern hits.

    Precedence: offer > rejection > interview > oa. Rejection outranks
    interview because rejection emails often mention "the interview".
    """
    text = f"{subject}\n{body}".lower()
    if any(p in text for p in _OFFER_PATTERNS):
        return "offer"
    if any(p in text for p in _REJECTION_PATTERNS):
        return "rejected"
    if any(p in text for p in _INTERVIEW_PATTERNS):
        return "interview"
    if any(p in text for p in _OA_PATTERNS):
        return "oa"
    return None


def match_applications(message: dict, applications: list[dict]) -> list[dict]:
    """Match a message to applications by company name.

    Company names shorter than 4 characters must appear in the sender
    address/domain (word-boundary substring matching in a whole email
    body would false-positive constantly for names like "Box").
    """
    sender = message["from"].lower()
    subject = message["subject"].lower()
    body = message["body"][:4000].lower()
    matched = []
    for app in applications:
        company = (app["company"] or "").lower().strip()
        if not company:
            continue
        pattern = r"\b" + re.escape(company) + r"\b"
        in_sender = re.search(pattern, sender) or company.replace(" ", "") in sender
        if len(company) < 4:
            if in_sender:
                matched.append(app)
            continue
        if in_sender or re.search(pattern, subject) or re.search(pattern, body):
            matched.append(app)
    return matched


def list_followup_candidates(*, after_days: int = 10) -> list[dict]:
    """Submitted applications with no reply in ``after_days`` days."""
    from src.core.database import get_session_factory
    from src.core.models import Application, Job

    cutoff = datetime.now(UTC) - timedelta(days=after_days)
    try:
        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            rows = (
                session.query(Application, Job)
                .join(Job, Job.id == Application.job_id)
                .filter(
                    Application.deleted_at.is_(None),
                    Application.submitted_at.isnot(None),
                    Application.submitted_at < cutoff,
                    (Application.outcome.is_(None)) | (Application.outcome == "pending"),
                )
                .order_by(Application.submitted_at.asc())
                .limit(50)
                .all()
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Follow-up query failed: %s", exc)
        return []

    now = datetime.now(UTC)
    followups = []
    for app, job in rows:
        submitted = app.submitted_at
        if submitted is not None and submitted.tzinfo is None:
            submitted = submitted.replace(tzinfo=UTC)
        followups.append(
            {
                "application_id": str(app.id),
                "company": job.company,
                "title": job.title,
                "submitted_at": submitted.isoformat() if submitted else None,
                "days_waiting": int((now - submitted).days) if submitted else None,
                "application_url": job.application_url,
            }
        )
    return followups


def _load_open_applications() -> list[dict]:
    """Submitted applications that could still receive a reply."""
    from src.core.database import get_session_factory
    from src.core.models import Application, Job

    session_factory = get_session_factory(load_config())
    with session_factory() as session:
        rows = (
            session.query(Application, Job)
            .join(Job, Job.id == Application.job_id)
            .filter(
                Application.deleted_at.is_(None),
                Application.submitted_at.isnot(None),
            )
            .all()
        )
        return [
            {
                "id": str(app.id),
                "company": job.company,
                "title": job.title,
                "outcome": app.outcome,
            }
            for app, job in rows
        ]


def _should_escalate(current: str | None, proposed: str) -> bool:
    if proposed == "rejected":
        return current != "rejected"
    return _OUTCOME_RANK.get(proposed, 0) > _OUTCOME_RANK.get(current or "pending", 0)


def _fetch_recent_messages(settings: dict) -> list[dict]:
    """Fetch recent inbox messages (headers + text body), newest first."""
    since = (datetime.now(UTC) - timedelta(days=settings["lookback_days"])).strftime(
        "%d-%b-%Y"
    )
    client = imaplib.IMAP4_SSL(settings["imap_host"])
    try:
        client.login(settings["username"], settings["password"])
        client.select("INBOX", readonly=True)
        status, data = client.search(None, f"(SINCE {since})")
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        ids = data[0].split()
        ids = ids[-settings["max_messages"] :]
        messages = []
        for msg_id in reversed(ids):
            status, payload = client.fetch(msg_id, "(RFC822)")
            if status != "OK" or not payload or payload[0] is None:
                continue
            try:
                parsed = email.message_from_bytes(payload[0][1])
                messages.append(
                    {
                        "subject": _decode_header(parsed.get("Subject", "")),
                        "from": _decode_header(parsed.get("From", "")),
                        "date": parsed.get("Date", ""),
                        "body": _extract_text(parsed),
                    }
                )
            except Exception:  # noqa: BLE001 -- one bad message never aborts the run
                logger.debug("Skipping unparseable message", exc_info=True)
        return messages
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass


def _decode_header(value: str) -> str:
    try:
        parts = email.header.decode_header(value)
        return "".join(
            part.decode(encoding or "utf-8", errors="replace")
            if isinstance(part, bytes)
            else part
            for part, encoding in parts
        )
    except Exception:  # noqa: BLE001
        return value


def _extract_text(message: email.message.Message) -> str:
    """Prefer text/plain parts; fall back to crudely de-tagged HTML."""
    chunks: list[str] = []
    html_chunks: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
        except Exception:  # noqa: BLE001
            continue
        if content_type == "text/plain":
            chunks.append(text)
        else:
            html_chunks.append(re.sub(r"<[^>]+>", " ", text))
    return "\n".join(chunks or html_chunks)[:20_000]
