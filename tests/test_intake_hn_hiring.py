"""Tests for the HN "Who is hiring?" adapter (src/intake/hn_hiring.py),
its board-cache reuse, the search_jobs source leg, and the HN-only
strict-pay startup quality gate in src/application/jobs.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.intake.base import ScraperError
from src.intake.hn_hiring import (
    MIN_COMMENT_CHARS,
    THREAD_TITLE_RE,
    _parse_comment,
    _split_header,
    fetch_latest_hn_hiring_jobs,
    find_latest_thread_id,
)

# ---- fixture: 3 fake top-level comments (per brief) --------------------------

COMMENT_PIPE_WITH_URL = {
    "id": 1001,
    "author": "acme_hr",
    "created_at": "2026-07-01T15:05:00.000Z",
    "text": (
        "Acme Corp | Senior Backend Engineer | Remote (US) | Full-time"
        '<p>We build payment infrastructure for mid-market retailers. Looking for '
        "a backend engineer with Python and distributed-systems experience. "
        '<p><a href="https:&#x2F;&#x2F;acme.example&#x2F;careers&#x2F;backend" rel="nofollow">'
        "https:&#x2F;&#x2F;acme.example&#x2F;careers&#x2F;backend</a>"
    ),
}

COMMENT_DASH_NO_URL = {
    "id": 1002,
    "author": "beta_founder",
    "created_at": "2026-07-01T15:06:00.000Z",
    "text": (
        "Beta Startup (YC S25) — Founding Full-Stack Engineer — NYC / Hybrid"
        "<p>We're a two-person team building developer tooling for AI agents. "
        "Looking for a generalist engineer comfortable across the stack. Equity-heavy "
        "offer, no fixed salary posted yet -- we'll negotiate based on experience."
    ),
}

COMMENT_NO_DELIMITER_WITH_URL = {
    "id": 1003,
    "author": "gamma_dev",
    "created_at": "2026-07-01T15:07:00.000Z",
    "text": (
        "Gamma Analytics is hiring a remote data engineer to join our three person team"
        '<p><a href="https:&#x2F;&#x2F;gamma.example&#x2F;jobs" rel="nofollow">'
        "https:&#x2F;&#x2F;gamma.example&#x2F;jobs</a>"
    ),
}

COMMENT_NOISE = {
    "id": 1004,
    "author": "grump",
    "created_at": "2026-07-01T15:08:00.000Z",
    "text": "don't waste your time with these guys",
}

COMMENT_DELETED = {
    "id": 1005,
    "author": None,
    "created_at": "2026-07-01T15:09:00.000Z",
    "text": None,
}


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self._response

    def close(self) -> None:
        pass


# ---- comment parsing -----------------------------------------------------------


def test_parse_comment_pipe_delimited_with_url_and_remote() -> None:
    job = _parse_comment(COMMENT_PIPE_WITH_URL, "thread1")

    assert job is not None
    assert job.source == "hn"
    assert job.ats_type == "unknown"
    assert job.source_id == "1001"
    assert job.company == "Acme Corp"
    assert "Senior Backend Engineer" in job.title
    assert job.location == "Remote (US)"
    assert job.employment_type == "fulltime"
    assert job.application_url == "https://acme.example/careers/backend"
    assert "payment infrastructure" in job.description
    assert job.raw_data["strict_pay"] is True
    assert job.raw_data["hn_thread_id"] == "thread1"
    assert job.raw_data["created_at"] == "2026-07-01T15:05:00.000Z"


def test_parse_comment_dash_delimited_no_url_falls_back_to_permalink() -> None:
    job = _parse_comment(COMMENT_DASH_NO_URL, "thread1")

    assert job is not None
    assert job.company == "Beta Startup (YC S25)"
    assert "Founding Full-Stack Engineer" in job.title
    # No REMOTE token anywhere in the header -- location stays unknown
    # rather than guessing "NYC / Hybrid" is the location segment.
    assert job.location is None
    assert job.application_url == "https://news.ycombinator.com/item?id=1002"


def test_parse_comment_no_delimiter_uses_whole_header_as_company() -> None:
    job = _parse_comment(COMMENT_NO_DELIMITER_WITH_URL, "thread1")

    assert job is not None
    assert job.company == (
        "Gamma Analytics is hiring a remote data engineer to join our three person team"
    )
    assert job.location == job.company  # only segment, and it contains "remote"
    assert job.application_url == "https://gamma.example/jobs"


def test_parse_comment_skips_noise_under_min_chars() -> None:
    assert _parse_comment(COMMENT_NOISE, "thread1") is None
    assert len(COMMENT_NOISE["text"]) < MIN_COMMENT_CHARS


def test_parse_comment_skips_deleted_comment() -> None:
    assert _parse_comment(COMMENT_DELETED, "thread1") is None


def test_split_header_pipe_delimited() -> None:
    assert _split_header("A | B | C") == ["A", "B", "C"]


def test_split_header_em_dash_delimited() -> None:
    assert _split_header("A — B — C") == ["A", "B", "C"]


def test_split_header_does_not_split_compound_word_hyphen() -> None:
    # No surrounding whitespace on the hyphen in "Full-Stack" -- must not split.
    assert _split_header("Full-Stack Engineer") == ["Full-Stack Engineer"]


def test_split_header_hyphen_with_spaces_splits() -> None:
    assert _split_header("Company - Remote - Full-Stack Engineer") == [
        "Company",
        "Remote",
        "Full-Stack Engineer",
    ]


def test_split_header_no_delimiter_returns_whole_string() -> None:
    assert _split_header("Just one plain sentence with no delimiters at all") == [
        "Just one plain sentence with no delimiters at all"
    ]


# ---- thread title matching -----------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Ask HN: Who is hiring? (July 2026)", True),
        ("Ask HN: Who is hiring?  (August 2026)", True),
        ("Ask HN: Who wants to be hired? (July 2026)", False),
        ("Ask HN: Who is hiring freelance developers?", True),  # matches the prefix
        ("Show HN: My new startup", False),
    ],
)
def test_thread_title_re(title: str, expected: bool) -> None:
    assert bool(THREAD_TITLE_RE.match(title)) is expected


def test_find_latest_thread_id_skips_non_official_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "hits": [
            {"objectID": "999", "title": "Ask HN: Who wants to be hired? (July 2026)"},
            {"objectID": "888", "title": "Ask HN: Who is hiring? (July 2026)"},
            {"objectID": "777", "title": "Ask HN: Who is hiring? (June 2026)"},
        ]
    }
    fake_client = _FakeClient(_FakeResponse(payload=payload))
    monkeypatch.setattr("src.intake.hn_hiring.httpx.Client", lambda **_kwargs: fake_client)

    assert find_latest_thread_id() == "888"


def test_find_latest_thread_id_returns_none_when_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"hits": [{"objectID": "1", "title": "Ask HN: Who wants to be hired?"}]}
    fake_client = _FakeClient(_FakeResponse(payload=payload))
    monkeypatch.setattr("src.intake.hn_hiring.httpx.Client", lambda **_kwargs: fake_client)

    assert find_latest_thread_id() is None


def test_find_latest_thread_id_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeClient(_FakeResponse(status_code=500))
    monkeypatch.setattr("src.intake.hn_hiring.httpx.Client", lambda **_kwargs: fake_client)

    with pytest.raises(ScraperError):
        find_latest_thread_id()


def test_find_latest_thread_id_raises_on_bad_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeClient(_FakeResponse(payload={"unexpected": 1}))
    monkeypatch.setattr("src.intake.hn_hiring.httpx.Client", lambda **_kwargs: fake_client)

    with pytest.raises(ScraperError):
        find_latest_thread_id()


# ---- fetch_latest_hn_hiring_jobs: thread fetch + cache wiring ------------------


def test_fetch_latest_hn_hiring_jobs_parses_all_three_and_skips_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.intake.hn_hiring.find_latest_thread_id", lambda **_kwargs: "thread1"
    )
    thread_payload = {
        "children": [
            COMMENT_PIPE_WITH_URL,
            COMMENT_DASH_NO_URL,
            COMMENT_NO_DELIMITER_WITH_URL,
            COMMENT_NOISE,
            COMMENT_DELETED,
        ]
    }
    fake_client = _FakeClient(_FakeResponse(payload=thread_payload))
    monkeypatch.setattr("src.intake.hn_hiring.httpx.Client", lambda **_kwargs: fake_client)
    monkeypatch.setattr("src.intake.search._board_cache_get", lambda _key: None)
    put_calls = []
    monkeypatch.setattr(
        "src.intake.search._board_cache_put",
        lambda key, jobs: put_calls.append((key, jobs)),
    )

    jobs = fetch_latest_hn_hiring_jobs()

    assert len(jobs) == 3
    assert {j.source_id for j in jobs} == {"1001", "1002", "1003"}
    assert put_calls[0][0] == ("hn", "thread1", False)


def test_fetch_latest_hn_hiring_jobs_uses_board_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.intake.hn_hiring.find_latest_thread_id", lambda **_kwargs: "thread1"
    )
    from src.intake.schema import RawJob

    cached_job = RawJob(source="hn", source_id="cached-1", company="Cached Co", title="X")
    monkeypatch.setattr(
        "src.intake.search._board_cache_get",
        lambda key: [cached_job] if key == ("hn", "thread1", False) else None,
    )

    def _fail_if_called(**_kwargs):
        raise AssertionError("should not hit the network on a cache hit")

    monkeypatch.setattr("src.intake.hn_hiring.httpx.Client", _fail_if_called)

    jobs = fetch_latest_hn_hiring_jobs()
    assert jobs == [cached_job]


def test_fetch_latest_hn_hiring_jobs_raises_when_no_thread_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.intake.hn_hiring.find_latest_thread_id", lambda **_kwargs: None)
    with pytest.raises(ScraperError):
        fetch_latest_hn_hiring_jobs()


# ---- search_jobs source leg wiring ----------------------------------------------


def test_search_jobs_hn_leg_narrows_by_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from src.intake.schema import RawJob

    backend_job = RawJob(source="hn", source_id="1", company="Acme", title="Backend Engineer")
    frontend_job = RawJob(source="hn", source_id="2", company="Beta", title="Frontend Designer")

    with patch(
        "src.intake.hn_hiring.fetch_latest_hn_hiring_jobs",
        return_value=[backend_job, frontend_job],
    ):
        from src.application.jobs import search_jobs

        result = asyncio.run(
            search_jobs(profile=None, source="all", keyword="backend", score=False, no_parse=True)
        )

    hn_titles = [j["title"] for j in result["jobs"] if j["source"] == "hn"]
    assert hn_titles == ["Backend Engineer"]
    assert result["counts"]["hn"] == 1


def test_search_jobs_hn_leg_error_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    with patch(
        "src.intake.hn_hiring.fetch_latest_hn_hiring_jobs",
        side_effect=ScraperError("no thread found"),
    ):
        from src.application.jobs import search_jobs

        result = asyncio.run(
            search_jobs(profile=None, source="all", keyword="backend", score=False, no_parse=True)
        )

    assert any("HN" in err for err in result["errors"])


# ---- strict-pay startup quality gate --------------------------------------------


def _hn_job(pay_text: str, source_id: str = "1") -> Any:
    from src.intake.schema import RawJob

    return RawJob(
        source="hn",
        source_id=source_id,
        company="Acme",
        title="Backend Engineer",
        description=f"Acme | Backend Engineer | Remote | {pay_text}",
        application_url="https://example.com",
        raw_data={"strict_pay": True},
    )


def _non_hn_job(pay_text: str, source_id: str = "2") -> Any:
    from src.intake.schema import RawJob

    return RawJob(
        source="greenhouse",
        source_id=source_id,
        company="Acme",
        title="Backend Engineer",
        description=f"Acme | Backend Engineer | Remote | {pay_text}",
        ats_type="greenhouse",
        application_url="https://example.com",
        raw_data={},
    )


def _apply_pay_filter(jobs: list) -> list:
    from src.application.jobs import _apply_search_filters

    return _apply_search_filters(
        jobs,
        experience_levels=[],
        employment_types=[],
        location_types=[],
        locations=[],
        search_location=None,
        searched_linkedin_locations=[],
        pay_operator="gte",
        pay_amount=100_000,
        experience_operator=None,
        experience_years=None,
        education_levels=[],
        use_llm=False,
    )


def test_strict_pay_drops_hn_job_with_no_stated_pay() -> None:
    job = _hn_job("great benefits, unlimited PTO")
    assert _apply_pay_filter([job]) == []


def test_strict_pay_passes_hn_job_stating_pay_in_range() -> None:
    job = _hn_job("$120k-$160k + equity")
    assert _apply_pay_filter([job]) == [job]


def test_strict_pay_still_drops_hn_job_stating_pay_below_floor() -> None:
    # Stated but below the $100k floor -- normal numeric filter, not the
    # strict-pay gate, should exclude it (gate only fires on *missing* pay).
    job = _hn_job("$40k-$60k")
    assert _apply_pay_filter([job]) == []


def test_strict_pay_does_not_affect_non_hn_sources_missing_pay() -> None:
    # Unknown-passes convention must be untouched for every other source.
    job = _non_hn_job("great benefits, unlimited PTO")
    assert _apply_pay_filter([job]) == [job]


def test_strict_pay_no_filter_active_hn_job_with_no_pay_still_passes() -> None:
    # The gate only fires when a pay filter is actually set by the caller.
    from src.application.jobs import _apply_search_filters

    job = _hn_job("great benefits, unlimited PTO")
    result = _apply_search_filters(
        [job],
        experience_levels=[],
        employment_types=[],
        location_types=[],
        locations=[],
        search_location=None,
        searched_linkedin_locations=[],
        pay_operator=None,
        pay_amount=None,
        experience_operator=None,
        experience_years=None,
        education_levels=[],
        use_llm=False,
    )
    assert result == [job]
