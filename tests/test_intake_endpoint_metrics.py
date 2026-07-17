"""SUP-01B: per-endpoint fetch instrumentation in src.intake.search.

Pure-Python -- no database, no network. Scraper classes are substituted with
fakes at the module-level names ``src.intake.search`` imports them under, so
``search_jobs``'s real board-fetch/cache/error-handling logic runs
unmodified; only the HTTP boundary inside each fake ``fetch_jobs`` is
scripted. ``persist_and_sync_ids`` is mocked out so a missing/unreachable
Postgres can never make this file hang (see AGENTS.md: DB-backed tests hang,
not fail, when Postgres is down -- this file must never be DB-backed).
"""

from __future__ import annotations

from unittest.mock import patch

from src.intake.base import ScraperError
from src.intake.schema import RawJob


def _make_fake_scraper_class(*, jobs=None, error=None, stats=None, call_log=None):
    """One-shot fake BaseScraper: either raises on fetch_jobs, or returns
    ``jobs`` and sets ``last_fetch_stats`` the way real scrapers do."""

    class _Fake:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            if call_log is not None:
                call_log.append(1)
            return self

        def __exit__(self, *exc_info):
            return False

        def fetch_jobs(self, slug):
            if error is not None:
                raise error
            self.last_fetch_stats = stats
            return list(jobs or [])

    return _Fake


def _job(source: str, source_id: str, company: str = "Some Co") -> RawJob:
    return RawJob(source=source, source_id=source_id, company=company, title="Engineer")


def test_success_tags_jobs_with_exact_endpoint_and_records_real_counts():
    job = _job("greenhouse", "1", "Acme")
    fake = _make_fake_scraper_class(
        jobs=[job],
        stats={"provider_records": 3, "normalized_records": 1, "malformed_records": 2},
    )
    with (
        patch("src.intake.search.GreenhouseScraper", fake),
        patch("src.intake.search.persist_and_sync_ids"),
    ):
        from src.intake.search import clear_board_cache, search_jobs

        clear_board_cache()
        metrics: list[dict] = []
        result = search_jobs(
            profile=None,
            companies={"greenhouse": ["acme"]},
            parse_jds=False,
            endpoint_metrics=metrics,
        )
        clear_board_cache()

    assert len(result) == 1
    # Exact, fetch-time attribution -- not a company-name guess (req #5).
    assert result[0].raw_data["source_endpoint_adapter"] == "greenhouse"
    assert result[0].raw_data["source_endpoint_key"] == "acme"

    assert len(metrics) == 1
    metric = metrics[0]
    assert metric["adapter"] == "greenhouse"
    assert metric["endpoint_key"] == "acme"
    assert metric["status"] == "success"
    assert metric["from_cache"] is False
    assert metric["provider_records"] == 3
    assert metric["normalized_records"] == 1
    assert metric["malformed_records"] == 2
    assert metric["duration_ms"] >= 0
    assert metric["started_at"] <= metric["finished_at"]


def test_empty_board_is_recorded_as_empty_not_success_or_error():
    fake = _make_fake_scraper_class(
        jobs=[], stats={"provider_records": 0, "normalized_records": 0, "malformed_records": 0}
    )
    with (
        patch("src.intake.search.LeverScraper", fake),
        patch("src.intake.search.persist_and_sync_ids"),
    ):
        from src.intake.search import clear_board_cache, search_jobs

        clear_board_cache()
        metrics: list[dict] = []
        result = search_jobs(
            profile=None,
            companies={"lever": ["ghost-co"]},
            parse_jds=False,
            endpoint_metrics=metrics,
        )
        clear_board_cache()

    assert result == []
    assert len(metrics) == 1
    assert metrics[0]["status"] == "empty"
    assert metrics[0]["provider_records"] == 0
    assert metrics[0]["error_code"] is None


def test_one_broken_endpoint_does_not_fail_the_whole_search():
    """SUP-01B req #3: failure isolation. A 500 from one employer board must
    not prevent jobs from a healthy board in the same search from coming
    back, and the broken board's attempt must still be recorded."""

    healthy_job = _job("greenhouse", "ok-1", "Good Co")
    healthy = _make_fake_scraper_class(
        jobs=[healthy_job],
        stats={"provider_records": 1, "normalized_records": 1, "malformed_records": 0},
    )
    broken = _make_fake_scraper_class(error=ScraperError("HTTP 500 from lever"))
    with (
        patch("src.intake.search.GreenhouseScraper", healthy),
        patch("src.intake.search.LeverScraper", broken),
        patch("src.intake.search.persist_and_sync_ids"),
    ):
        from src.intake.search import clear_board_cache, search_jobs

        clear_board_cache()
        metrics: list[dict] = []
        result = search_jobs(
            profile=None,
            companies={"greenhouse": ["good-co"], "lever": ["broken-co"]},
            parse_jds=False,
            endpoint_metrics=metrics,
        )
        clear_board_cache()

    # The whole call succeeded and returned the healthy board's jobs --
    # one broken endpoint didn't take down the discovery run.
    assert len(result) == 1
    assert result[0].source_id == "ok-1"

    by_adapter = {m["adapter"]: m for m in metrics}
    assert by_adapter["greenhouse"]["status"] == "success"
    assert by_adapter["lever"]["status"] == "error"
    assert by_adapter["lever"]["error_code"] == "ScraperError"
    assert by_adapter["lever"]["normalized_records"] == 0
    assert "HTTP 500" in by_adapter["lever"]["error_detail"]


def test_cache_hit_on_second_call_does_not_refetch_or_fabricate_duration():
    job = _job("greenhouse", "c-1", "Cache Co")
    call_log: list[int] = []
    fake = _make_fake_scraper_class(
        jobs=[job],
        stats={"provider_records": 1, "normalized_records": 1, "malformed_records": 0},
        call_log=call_log,
    )
    with (
        patch("src.intake.search.GreenhouseScraper", fake),
        patch("src.intake.search.persist_and_sync_ids"),
    ):
        from src.intake.search import clear_board_cache, search_jobs

        clear_board_cache()
        metrics_first: list[dict] = []
        search_jobs(
            profile=None,
            companies={"greenhouse": ["cache-co"]},
            parse_jds=False,
            endpoint_metrics=metrics_first,
        )
        metrics_second: list[dict] = []
        result_second = search_jobs(
            profile=None,
            companies={"greenhouse": ["cache-co"]},
            parse_jds=False,
            endpoint_metrics=metrics_second,
        )
        clear_board_cache()

    # The scraper's __enter__ (i.e. an actual fetch attempt) only ran once --
    # the second call was served entirely from the board cache.
    assert len(call_log) == 1
    assert metrics_first[0]["status"] == "success"
    assert metrics_second[0]["status"] == "cache_hit"
    assert metrics_second[0]["from_cache"] is True
    assert metrics_second[0]["duration_ms"] == 0
    assert len(result_second) == 1


def test_endpoint_metrics_is_untouched_when_caller_does_not_pass_one():
    """Backward compatibility: omitting endpoint_metrics must not change
    fetch behavior at all -- it's a pure out-parameter."""

    job = _job("greenhouse", "no-metrics-1", "Silent Co")
    fake = _make_fake_scraper_class(
        jobs=[job],
        stats={"provider_records": 1, "normalized_records": 1, "malformed_records": 0},
    )
    with (
        patch("src.intake.search.GreenhouseScraper", fake),
        patch("src.intake.search.persist_and_sync_ids"),
    ):
        from src.intake.search import clear_board_cache, search_jobs

        clear_board_cache()
        result = search_jobs(profile=None, companies={"greenhouse": ["silent-co"]}, parse_jds=False)
        clear_board_cache()

    assert len(result) == 1
    # Still tagged even without a metrics collector -- tagging happens
    # unconditionally, only the metrics dict itself is optional.
    assert result[0].raw_data["source_endpoint_key"] == "silent-co"
