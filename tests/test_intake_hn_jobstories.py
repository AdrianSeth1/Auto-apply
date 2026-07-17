"""Official Hacker News jobstories adapter tests."""

from __future__ import annotations

from src.intake.hn_jobstories import _parse_job_item, _split_title


def test_parse_hiring_title() -> None:
    assert _split_title("Acme is hiring an Applied AI Engineer") == (
        "Acme",
        "an Applied AI Engineer",
    )


def test_parse_jobstory() -> None:
    job = _parse_job_item(
        {
            "id": 123,
            "type": "job",
            "time": 1_700_000_000,
            "title": "Cora AI is hiring Applied AI Engineers",
            "text": "<p>Join our remote team building production agents.</p>",
            "url": "https://cora.example/jobs/ai",
        }
    )
    assert job is not None
    assert job.source == "hn"
    assert job.source_id == "jobstory-123"
    assert job.company == "Cora AI"
    assert job.title == "Applied AI Engineers"
    assert job.location == "Remote"
    assert job.application_url == "https://cora.example/jobs/ai"
    assert job.raw_data["hn_feed"] == "jobstories"


def test_dead_or_non_job_items_are_ignored() -> None:
    assert _parse_job_item({"id": 1, "type": "story", "title": "Nope"}) is None
    assert _parse_job_item({"id": 2, "type": "job", "title": "Nope", "dead": True}) is None


def test_prose_title_does_not_turn_entire_post_into_company_identity() -> None:
    raw = (
        "Sumble is the newco from the founders of Kaggle. We are hiring full "
        "stack engineers and growth roles. Apply on our careers page."
    )
    company, title = _split_title(raw)
    assert company == "Sumble"
    assert len(company) <= 200
    assert len(title) <= 300
