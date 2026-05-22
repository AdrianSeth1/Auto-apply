"""LinkedIn Easy-Apply-only guard.

Regression coverage for the Fitzrovia bug: the LinkedIn scraper used
to mis-detect a same-page link as the external apply URL when the
real apply path was Easy Apply, and the apply pipeline would happily
form-fill the company's marketing homepage. The fix has two layers:

1. The scraper now records ``linkedin_easy_apply_only`` whenever the
   only apply option on the page is Easy Apply.
2. ``apply_to_url`` / ``apply_to_job_id`` refuse to run when either
   the flag is set or the resolved URL is still on the LinkedIn
   domain.

Both layers are tested here without a real browser: we drive
``_resolve_apply_target`` with stubbed Playwright element handles and
exercise the apply guards via the real Python code paths.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.jobs import (
    _is_linkedin_url,
    _job_is_easy_apply_only,
    _linkedin_easy_apply_message,
    apply_to_url,
)
from src.intake.linkedin import LinkedInScraper
from src.intake.schema import JobRequirements, RawJob


def _make_element(
    *,
    text: str = "",
    aria_label: str | None = None,
    href: str | None = None,
    class_name: str | None = None,
):
    """Build a fake Playwright element handle the resolver can interrogate."""
    element = MagicMock()
    element.is_visible = AsyncMock(return_value=True)
    element.inner_text = AsyncMock(return_value=text)

    async def _get_attribute(name: str):
        if name == "aria-label":
            return aria_label
        if name == "href":
            return href
        if name == "class":
            return class_name
        return None

    element.get_attribute = AsyncMock(side_effect=_get_attribute)
    return element


def test_easy_apply_plus_company_homepage_is_still_easy_apply_only() -> None:
    """Fitzrovia regression: LinkedIn may show Easy Apply plus a
    wrapped company-homepage link. The homepage is not an Apply path,
    so AutoApply must refuse instead of form-filling that site."""
    scraper = LinkedInScraper.__new__(LinkedInScraper)
    elements = [
        _make_element(text="Easy Apply", aria_label="Easy Apply on LinkedIn"),
        _make_element(
            text="Visit company website",
            aria_label="Visit Fitzrovia website",
            href="https://www.linkedin.com/safety/go/?url=https%3A%2F%2Ffitzrovia.ca",
        ),
    ]

    async def _run():
        page = MagicMock()
        page.query_selector_all = AsyncMock(return_value=elements)
        return await scraper._resolve_apply_target(
            page,
            fallback_url="https://www.linkedin.com/jobs/view/42",
        )

    result = asyncio.run(_run())
    assert result["has_easy_apply"] is True
    assert result["easy_apply_only"] is True
    assert result["external_apply_url"] is None
    assert result["manual_apply_url"] == "https://www.linkedin.com/jobs/view/42"


def test_easy_apply_only_page_flags_the_job_correctly() -> None:
    """LinkedIn page with one "Easy Apply" button and nothing else
    must set ``easy_apply_only=True`` and surface no external URL."""
    scraper = LinkedInScraper.__new__(LinkedInScraper)
    elements = [_make_element(text="Easy Apply", aria_label="Easy Apply on LinkedIn")]

    async def _run():
        with patch.object(
            LinkedInScraper, "_find_all_apply_buttons", AsyncMock(return_value=elements)
        ):
            return await scraper._resolve_apply_target(
                MagicMock(),
                fallback_url="https://www.linkedin.com/jobs/view/42",
            )

    result = asyncio.run(_run())
    assert result["has_easy_apply"] is True
    assert result["easy_apply_only"] is True
    assert result["external_apply_url"] is None
    assert result["ats_url"] is None


def test_dual_apply_page_keeps_external_url_and_marks_easy_apply() -> None:
    """LinkedIn page that exposes BOTH Easy Apply and an external
    company link must record the external URL as the apply target
    while still flagging ``has_easy_apply=True`` so a future "let the
    user pick" UI has the data."""
    scraper = LinkedInScraper.__new__(LinkedInScraper)
    elements = [
        _make_element(text="Easy Apply", aria_label="Easy Apply on LinkedIn"),
        _make_element(
            text="Apply",
            aria_label="Apply on company website",
            href="https://boards.greenhouse.io/acme/jobs/123",
        ),
    ]

    async def _run():
        with patch.object(
            LinkedInScraper, "_find_all_apply_buttons", AsyncMock(return_value=elements)
        ):
            return await scraper._resolve_apply_target(
                MagicMock(),
                fallback_url="https://www.linkedin.com/jobs/view/42",
            )

    result = asyncio.run(_run())
    assert result["has_easy_apply"] is True
    assert result["easy_apply_only"] is False
    assert "greenhouse.io/acme/jobs/123" in result["external_apply_url"]
    assert result["ats_url"] is not None


def test_apply_to_url_refuses_linkedin_only_target() -> None:
    """``apply_to_url`` against a LinkedIn URL must refuse with the
    canonical Easy-Apply message instead of letting the pipeline run."""

    async def _resolver(url):
        # Stand-in for the LinkedIn URL resolver: it didn't find any
        # external target, mirroring the Easy-Apply-only case.
        return {"url": url, "ats_url": None}

    with patch("src.application.jobs.resolve_manual_apply_url", side_effect=_resolver):
        result = asyncio.run(
            apply_to_url(url="https://www.linkedin.com/jobs/view/42", dry_run=True)
        )

    assert result["ok"] is False
    assert result["error_code"] == "linkedin_easy_apply_only"
    assert "Easy Apply" in result["error"]


def test_linkedin_easy_apply_message_is_actionable() -> None:
    msg = _linkedin_easy_apply_message()
    # Surface the workaround so the operator knows what to do next.
    assert "Easy Apply" in msg
    assert "manual" in msg.lower() or "open" in msg.lower()


def test_job_is_easy_apply_only_reads_raw_data_flag() -> None:
    job = RawJob(
        source="linkedin",
        source_id="42",
        title="Backend Eng",
        company="Acme",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/42",
        description="...",
        requirements=JobRequirements(),
        raw_data={"linkedin_easy_apply_only": True},
    )
    assert _job_is_easy_apply_only(job) is True

    job.raw_data = {"linkedin_easy_apply_only": False}
    assert _job_is_easy_apply_only(job) is False

    job.raw_data = {}
    assert _job_is_easy_apply_only(job) is False


def test_is_linkedin_url_recognises_common_shapes() -> None:
    assert _is_linkedin_url("https://www.linkedin.com/jobs/view/42")
    assert _is_linkedin_url("https://linkedin.com/jobs/view/42")
    assert not _is_linkedin_url("https://boards.greenhouse.io/acme/jobs/123")
    assert not _is_linkedin_url("https://fitzrovia.io/careers")
