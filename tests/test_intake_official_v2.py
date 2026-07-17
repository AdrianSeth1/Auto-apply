from src.intake.recruitee import RecruiteeScraper
from src.intake.smartrecruiters import SmartRecruitersScraper
from src.intake.workable import WorkablePublicScraper


def test_smartrecruiters_normalizes_stable_id_detail_and_apply_url():
    scraper = object.__new__(SmartRecruitersScraper)
    job = scraper._parse_job("acme", {
        "id": "sr-1", "name": "Implementation Specialist", "company": {"name": "Acme"},
        "location": {"city": "Chicago", "region": "IL", "country": "US"},
        "typeOfEmployment": "Full-time", "applyUrl": "https://apply.example/sr-1",
        "jobAd": {"sections": {"jobDescription": {"text": "<p>Lead customer implementation.</p>"}}},
    })
    assert (job.source, job.source_id, job.company) == ("smartrecruiters", "sr-1", "Acme")
    assert job.location == "Chicago, IL, US"
    assert job.application_url == "https://apply.example/sr-1"
    assert job.provenance.application_target.kind == "direct_ats"


def test_workable_normalizes_public_detail():
    scraper = object.__new__(WorkablePublicScraper)
    job = scraper._parse_job("acme", {
        "shortcode": "ABC123", "title": "AI Implementation Consultant",
        "location": {"city": "New York", "region": "NY", "country": "US"},
        "description": "<p>Configure and deploy AI workflows for customers.</p>",
        "application_url": "https://apply.workable.com/acme/j/ABC123/",
    })
    assert job.source_id == "ABC123"
    assert job.description == "Configure and deploy AI workflows for customers."
    assert job.application_url.endswith("/ABC123/")


def test_recruitee_normalizes_offer_and_urls():
    scraper = object.__new__(RecruiteeScraper)
    job = scraper._parse_job("acme", {
        "id": 42, "slug": "solutions-engineer", "title": "Associate Solutions Engineer",
        "location": {"city": "Austin", "state": "TX", "country": "US"},
        "description": "<p>Run discovery, demos, and technical onboarding.</p>",
    })
    assert (job.source, job.source_id) == ("recruitee", "42")
    assert job.application_url == "https://acme.recruitee.com/o/solutions-engineer/c/new"
    assert job.provenance.listing_url == "https://acme.recruitee.com/o/solutions-engineer"
