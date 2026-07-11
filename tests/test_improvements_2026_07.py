"""Tests for the 2026-07-07 improvement batch.

Covers: multi-profile scoring, embedding similarity fallback + calibration,
ghost-posting age penalty, prep pack builder, outcome analytics helpers,
and the email reply classifier/matcher.

All tests are hermetic: no Postgres, Redis, Ollama, or IMAP required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from src.intake.schema import JobRequirements, RawJob

# ===========================================================================
# Helpers
# ===========================================================================


def _make_job(**overrides) -> RawJob:
    defaults = {
        "id": uuid.uuid4(),
        "source": "greenhouse",
        "source_id": f"j-{uuid.uuid4().hex[:8]}",
        "company": "TestCo",
        "title": "Solutions Consultant",
        "location": "Dallas, TX",
        "employment_type": "fulltime",
        "seniority": "entry",
        "description": (
            "We need a solutions consultant with SQL, Python, client "
            "onboarding, stakeholder management and SaaS implementation "
            "experience. You will run demos, configure solutions, and "
            "manage project delivery for enterprise clients. " * 3
        ),
        "requirements": JobRequirements(
            must_have_skills=["SQL", "Python"], preferred_skills=["HubSpot"]
        ),
        "ats_type": "greenhouse",
        "application_url": "https://example.com/apply",
        "raw_data": {},
    }
    defaults.update(overrides)
    return RawJob(**defaults)


_SC_PROFILE = {
    "identity": {"visa_sponsorship_needed": False},
    "education": [{"degree": "Bachelor of Science", "field": "Neuroscience"}],
    "work_experiences": [
        {
            "title": "Implementation Specialist",
            "start_date": "2024-01",
            "end_date": "2026-01",
            "bullets": [
                {"text": "Client onboarding, SQL dashboards, SaaS implementation"}
            ],
        }
    ],
    "projects": [],
    "skills": {"languages": ["Python", "SQL"], "tools": ["HubSpot"]},
}

_FIRMWARE_PROFILE = {
    "identity": {"visa_sponsorship_needed": False},
    "education": [{"degree": "Bachelor of Science", "field": "Neuroscience"}],
    "work_experiences": [
        {
            "title": "Firmware Intern",
            "start_date": "2024-01",
            "end_date": "2026-01",
            "bullets": [{"text": "Embedded C++ kernel drivers and RTOS work"}],
        }
    ],
    "projects": [],
    "skills": {"languages": ["C++"], "tools": ["JTAG"]},
}


# ===========================================================================
# Multi-profile scoring
# ===========================================================================


class TestMultiProfileScoring:
    @patch("src.matching.semantic.embed_text_local", return_value=None)
    def test_best_profile_wins(self, _embed):
        from src.application.jobs import _score_jobs

        profiles = [
            {"id": "solutions", "path": "/fake/solutions.yaml", "is_active": False},
            {"id": "firmware", "path": "/fake/firmware.yaml", "is_active": True},
        ]
        profile_data = {
            "/fake/solutions.yaml": _SC_PROFILE,
            "/fake/firmware.yaml": _FIRMWARE_PROFILE,
        }
        job = _make_job()

        with (
            patch("src.application.profile.list_profiles", return_value=profiles),
            patch(
                "src.application.profile.get_active_profile_id",
                return_value="firmware",
            ),
            patch(
                "src.memory.profile.load_profile_yaml",
                side_effect=lambda path: profile_data[str(path).replace("\\", "/")],
            ),
        ):
            scored, errors = _score_jobs([job], warn_on_missing_profile=True)

        assert scored is True
        assert errors == []
        assert job.raw_data["best_profile"] == "solutions"
        assert set(job.raw_data["profile_scores"]) == {"solutions", "firmware"}
        assert (
            job.raw_data["profile_scores"]["solutions"]
            > job.raw_data["profile_scores"]["firmware"]
        )
        assert job.raw_data["match_score"] == job.raw_data["profile_scores"]["solutions"]

    @patch("src.matching.semantic.embed_text_local", return_value=None)
    def test_bad_profile_skipped_with_warning(self, _embed):
        from src.application.jobs import _score_jobs

        profiles = [
            {"id": "good", "path": "/fake/good.yaml", "is_active": True},
            {"id": "broken", "path": "/fake/broken.yaml", "is_active": False},
        ]

        def load(path):
            if "broken" in str(path):
                raise ValueError("corrupt yaml")
            return _SC_PROFILE

        job = _make_job()
        with (
            patch("src.application.profile.list_profiles", return_value=profiles),
            patch("src.application.profile.get_active_profile_id", return_value="good"),
            patch("src.memory.profile.load_profile_yaml", side_effect=load),
        ):
            scored, errors = _score_jobs([job], warn_on_missing_profile=True)

        assert scored is True
        assert len(errors) == 1 and "broken" in errors[0]
        assert job.raw_data["best_profile"] == "good"

    def test_no_profiles_warns(self):
        from src.application.jobs import _score_jobs

        with patch("src.application.profile.list_profiles", return_value=[]):
            scored, errors = _score_jobs([_make_job()], warn_on_missing_profile=True)
        assert scored is False
        assert errors


# ===========================================================================
# Experience years — overlap merging
# ===========================================================================


class TestMergedExperienceYears:
    def _years(self, experiences):
        from src.matching.rules import _merged_experience_years

        return _merged_experience_years(experiences)

    def test_concurrent_jobs_count_once(self):
        # Two jobs held over the same two years = 2 years, not 4.
        experiences = [
            {"start_date": "2023-01", "end_date": "2025-01"},
            {"start_date": "2023-06", "end_date": "2025-01"},
        ]
        assert self._years(experiences) == 2

    def test_sequential_jobs_sum(self):
        experiences = [
            {"start_date": "2020-01", "end_date": "2021-01"},
            {"start_date": "2022-01", "end_date": "2023-01"},
        ]
        assert self._years(experiences) == 2

    def test_month_granularity(self):
        # Dec 2023 - Jan 2024 is one month, not "1 year" (old year-diff bug).
        assert self._years([{"start_date": "2023-12", "end_date": "2024-01"}]) == 0

    def test_present_and_missing_end(self):
        years_present = self._years([{"start_date": "2023-01", "end_date": "Present"}])
        years_missing = self._years([{"start_date": "2023-01", "end_date": ""}])
        assert years_present == years_missing >= 2

    def test_garbage_dates_skipped(self):
        assert self._years([{"start_date": "soon", "end_date": "later"}, "not-a-dict"]) == 0

    def test_declared_professional_years_overrides_calendar(self):
        from src.matching.rules import load_applicant_context

        profile = {
            "identity": {"professional_experience_years": 2},
            "education": [],
            "work_experiences": [
                {"start_date": "2020-01", "end_date": "2026-01"},  # 6 calendar yrs
            ],
        }
        assert load_applicant_context(profile).years_of_experience == 2
        # Absent/invalid -> calendar math stands.
        profile["identity"] = {"professional_experience_years": "lots"}
        assert load_applicant_context(profile).years_of_experience == 6


# ===========================================================================
# Embedding similarity
# ===========================================================================


class TestEmbeddingSimilarity:
    def test_calibration_clamps(self):
        from src.matching.semantic import calibrate_embedding_cosine

        assert calibrate_embedding_cosine(0.2) == 0.0
        assert calibrate_embedding_cosine(0.85) == 1.0
        assert 0.0 < calibrate_embedding_cosine(0.6) < 1.0

    @patch("src.matching.semantic.embed_text_local", return_value=None)
    def test_falls_back_to_tf_when_embeddings_unavailable(self, _embed):
        from src.matching.semantic import (
            compute_keyword_similarity,
            compute_text_similarity,
        )

        jd = "python sql onboarding"
        applicant = "python sql onboarding dashboards"
        assert compute_text_similarity(jd, applicant) == compute_keyword_similarity(
            jd, applicant
        )

    def test_uses_applicant_vector_when_given(self):
        from src.matching import semantic

        jd_vec = [1.0, 0.0]
        with patch.object(semantic, "embed_text_local", return_value=jd_vec) as mock:
            score = semantic.compute_text_similarity(
                "some jd", "some applicant", applicant_vector=[1.0, 0.0]
            )
        # Only the JD needed embedding; the applicant vector was reused.
        assert mock.call_count == 1
        assert score == semantic.calibrate_embedding_cosine(1.0)

    def test_disabled_via_config(self):
        from src.matching.semantic import local_embedding_settings

        with patch(
            "src.core.config.load_config",
            return_value={"matching": {"embeddings": {"enabled": False}}},
        ):
            assert local_embedding_settings() is None


# ===========================================================================
# Ghost-posting age penalty
# ===========================================================================


class TestGhostPenalty:
    def test_old_posting_penalized(self):
        from src.matching.scorer import _compute_quality_multiplier

        fresh = _make_job()
        old = _make_job(
            raw_data={
                "first_seen_at": (datetime.now(UTC) - timedelta(days=120)).isoformat()
            }
        )
        assert _compute_quality_multiplier(old) < _compute_quality_multiplier(fresh)

    def test_unknown_age_not_penalized(self):
        from src.matching.scorer import _compute_quality_multiplier, _posting_age_days

        job = _make_job()
        assert _posting_age_days(job) is None
        assert _compute_quality_multiplier(job) == 1.0

    def test_lever_epoch_millis_parsed(self):
        from src.matching.scorer import _posting_age_days

        created_ms = int((datetime.now(UTC) - timedelta(days=45)).timestamp() * 1000)
        job = _make_job(raw_data={"createdAt": created_ms})
        age = _posting_age_days(job)
        assert age is not None and 44 <= age <= 46

    def test_tiers(self):
        from src.matching.scorer import _compute_quality_multiplier

        def job_aged(days):
            return _make_job(
                raw_data={
                    "first_seen_at": (
                        datetime.now(UTC) - timedelta(days=days)
                    ).isoformat()
                }
            )

        assert _compute_quality_multiplier(job_aged(10)) == 1.0
        assert _compute_quality_multiplier(job_aged(45)) == 0.9
        assert _compute_quality_multiplier(job_aged(75)) == 0.75
        assert _compute_quality_multiplier(job_aged(120)) == 0.6


# ===========================================================================
# Prep packs
# ===========================================================================


class TestPrepPack:
    _PROFILE = {
        "skills": {"languages": ["Python", "SQL"], "tools": ["HubSpot"]},
        "story_bank": [
            {
                "theme": "stakeholder_translation",
                "context": "Engineering and clients misaligned on SaaS onboarding.",
                "action": "Rewrote docs, built implementation guides.",
                "result": "25% lift in demo conversion.",
                "applicable_to": ["solutions_consulting"],
            },
            {
                "theme": "technical_challenge",
                "context": "Professor needed a local AI lecture assistant.",
                "action": "Built RAG pipeline with whisper and Ollama.",
                "result": "Adopted in a real classroom.",
                "applicable_to": ["ai_roles"],
            },
        ],
    }

    _JOB = {
        "title": "Solutions Consultant",
        "company": "Acme",
        "location": "Dallas, TX",
        "description": (
            "Own client onboarding and SaaS implementation. Translate "
            "between engineering and enterprise stakeholders. Run demos."
        ),
        "requirements": {
            "must_have_skills": ["SQL", "Salesforce"],
            "preferred_skills": ["HubSpot"],
        },
        "application_url": "https://example.com/apply",
        "match_score": 0.75,
    }

    def test_builds_markdown_with_stories_and_skills(self):
        from src.generation.prep_pack import build_prep_pack

        markdown = build_prep_pack(job=self._JOB, profile=self._PROFILE)
        assert "# Interview Prep — Solutions Consultant @ Acme" in markdown
        assert "SQL ✓" in markdown  # has it
        assert "Salesforce" in markdown and "Salesforce ✓" not in markdown  # doesn't
        assert "Stakeholder Translation" in markdown
        assert "Likely asked as:" in markdown

    def test_relevant_story_ranked_first(self):
        from src.generation.prep_pack import rank_stories

        ranked = rank_stories(
            self._PROFILE["story_bank"],
            title=self._JOB["title"],
            description=self._JOB["description"],
        )
        assert ranked[0][0]["theme"] == "stakeholder_translation"

    def test_empty_profile_degrades(self):
        from src.generation.prep_pack import build_prep_pack

        markdown = build_prep_pack(job={"title": "X", "company": "Y"}, profile={})
        assert "# Interview Prep — X @ Y" in markdown


# ===========================================================================
# Outcome analytics helpers
# ===========================================================================


class TestOutcomeAnalytics:
    def test_band_labels(self):
        from src.application.analytics import _band_label

        assert _band_label(None) == "unscored"
        assert _band_label(0.95) == "0.8 – 1.0"
        assert _band_label(0.05) == "0.0 – 0.2"

    def test_rates(self):
        from src.application.analytics import _bucket, _finalize, _tally

        bucket = _bucket()
        for outcome in ("interview", "rejected", None, "pending"):
            _tally(bucket, outcome)
        final = _finalize(bucket)
        assert final["total"] == 4
        assert final["positive"] == 1
        assert final["rejected"] == 1
        assert final["pending"] == 2
        assert final["response_rate"] == 0.5
        assert final["positive_rate"] == 0.25


# ===========================================================================
# Bullet-rewrite regression guard (the Figma resume incident)
# ===========================================================================


class TestRewriteRegressionGuard:
    """Cases taken verbatim from the 2026-07-08 Figma resume failure."""

    def _guard(self, original, rewritten, keywords):
        from src.generation.resume_builder import _rewrite_regression_guard

        return _rewrite_regression_guard(original, rewritten, keywords)

    def test_reverts_when_matched_keyword_paraphrased_away(self):
        original = (
            "Redesigned client onboarding end to end, cutting time-to-value by "
            "~30% and standardizing the path from signup to activation."
        )
        rewritten = (
            "Overhauled the customer integration lifecycle from start to finish, "
            "decreasing initial value realization by ~30%."
        )
        assert (
            self._guard(original, rewritten, "time to value, onboarding, demos")
            == original
        )

    def test_reverts_when_demo_removed(self):
        original = "Lifted qualified demo conversion by ~25% through acquisition funnel redesign."
        rewritten = (
            "Optimized lead qualification pathways to increase trial-to-pipeline "
            "progression by ~25%."
        )
        assert self._guard(original, rewritten, "demos, presales, discovery") == original

    def test_reverts_on_lost_number(self):
        original = "Cut escalations by 40% across the client base."
        rewritten = "Substantially cut escalations across the client base."
        assert self._guard(original, rewritten, "escalations") == original

    def test_reverts_on_added_number(self):
        # 2026-07-09: "added_unverified_number" reached a rendered resume.
        original = "Cut escalations across the client base through process fixes."
        rewritten = "Cut escalations by 40% across the client base through process fixes."
        assert self._guard(original, rewritten, "escalations") == original

    def test_batch_rewrite_applies_guard_per_chunk(self):
        from src.generation import resume_builder as rb
        from src.generation.evidence import EvidenceBullet

        def _bullet(source_id, text):
            return EvidenceBullet(
                source_id=source_id,
                source_type="experience",
                source_entity="SDS",
                text=text,
            )

        grouped = {
            "SDS": [
                _bullet("e1", "Lifted qualified demo conversion by ~25% through funnel redesign."),
                _bullet("e2", "Built dashboards tracking onboarding health."),
            ]
        }
        # Model destroys the demo keyword in bullet 1 (guard must revert)
        # and makes a clean keyword injection in bullet 2 (must accept).
        fake_response = {
            "rewritten_bullets": [
                "Optimized lead pathways to lift trial progression by ~25%.",
                "Built dashboards tracking onboarding health and time-to-value.",
            ]
        }
        with patch("src.utils.llm.generate_json", return_value=fake_response):
            result = rb._rewrite_grouped_evidence(
                grouped, ["demos", "time to value"], mode="balanced"
            )
        texts = [b.render_text or b.text for b in result["SDS"]]
        assert texts[0] == grouped["SDS"][0].text  # reverted
        assert "time-to-value" in texts[1]  # accepted injection

    def test_reverts_on_inflation(self):
        original = "Built dashboards tracking onboarding health."
        rewritten = (
            "Architected and operationalized a comprehensive suite of analytical "
            "dashboards meticulously tracking the holistic health of the customer "
            "onboarding lifecycle across the organization."
        )
        assert self._guard(original, rewritten, "dashboards") == original

    def test_accepts_good_keyword_injection(self):
        original = "Built dashboards tracking onboarding health and engagement."
        rewritten = "Built Looker dashboards tracking onboarding health, engagement, and time-to-value."
        assert (
            self._guard(original, rewritten, "time to value, dashboards")
            == rewritten
        )

    def test_hyphen_and_markup_normalization(self):
        original = "Cut **time-to-value** by ~30% for new clients."
        rewritten = "Cut time to value by ~30% for new clients during onboarding."
        assert self._guard(original, rewritten, "time to value, onboarding") == rewritten

    def test_empty_rewrite_reverts(self):
        assert self._guard("Real bullet.", "   ", "anything") == "Real bullet."


# ===========================================================================
# Em/en dash normalization (resume bullets + Materials-tab answers reading
# as AI-generated -- 2026-07-11 user report)
# ===========================================================================


class TestProseDashNormalization:
    def test_resume_bullet_em_dash_becomes_comma(self):
        from src.generation.resume_builder import _normalize_prose_dashes

        assert (
            _normalize_prose_dashes("Led migration — reduced latency by 40%")
            == "Led migration, reduced latency by 40%"
        )

    def test_resume_bullet_en_dash_becomes_comma(self):
        from src.generation.resume_builder import _normalize_prose_dashes

        assert (
            _normalize_prose_dashes("Owned rollout – cut onboarding time in half")
            == "Owned rollout, cut onboarding time in half"
        )

    def test_resume_bullet_compound_word_hyphens_untouched(self):
        # Only the em/en dash Unicode characters are targeted -- ordinary
        # ASCII hyphens inside compound words like "time-to-value" or
        # "full-time" must survive unchanged.
        from src.generation.resume_builder import _normalize_prose_dashes

        text = "Cut time-to-value for full-time customers by 30%"
        assert _normalize_prose_dashes(text) == text

    def test_clean_llm_bullet_rewrite_output_strips_em_dash(self):
        from src.generation.resume_builder import _clean_llm_bullet_rewrite_output

        cleaned = _clean_llm_bullet_rewrite_output(
            "Led the migration — reduced latency by 40%",
            "Led the migration, reduced latency by 40%",
        )
        assert "—" not in cleaned
        assert cleaned == "Led the migration, reduced latency by 40%"

    def test_batch_rewrite_strips_em_dash_from_output(self):
        from src.generation import resume_builder as rb
        from src.generation.evidence import EvidenceBullet

        grouped = {
            "SDS": [
                EvidenceBullet(
                    source_id="e1",
                    source_type="experience",
                    source_entity="SDS",
                    text="Built dashboards tracking onboarding health.",
                )
            ]
        }
        fake_response = {
            "rewritten_bullets": [
                "Built dashboards — tracking onboarding health and time-to-value."
            ]
        }
        with patch("src.utils.llm.generate_json", return_value=fake_response):
            result = rb._rewrite_grouped_evidence(grouped, ["time to value"], mode="balanced")
        text = result["SDS"][0].render_text
        assert "—" not in text
        assert "time-to-value" in text  # compound hyphen survives

    def test_question_answer_em_dash_becomes_comma(self):
        from src.application.question_answers import _normalize_answer_dashes

        assert (
            _normalize_answer_dashes("I led the migration — it cut latency significantly.")
            == "I led the migration, it cut latency significantly."
        )

    def test_question_answer_compound_word_hyphens_untouched(self):
        from src.application.question_answers import _normalize_answer_dashes

        text = "Worked on a state-of-the-art system for full-time staff."
        assert _normalize_answer_dashes(text) == text

    def test_parse_response_strips_em_dash_from_json_answer(self):
        from src.application.question_answers import _parse_response

        answer, _ = _parse_response(
            '{"answer": "I led the project — it shipped early.", "clarifying_questions": []}'
        )
        assert answer == "I led the project, it shipped early."

    def test_parse_response_strips_em_dash_from_fallback_text(self):
        from src.application.question_answers import _parse_response

        answer, _ = _parse_response("Just a plain paragraph — no JSON here.")
        assert answer == "Just a plain paragraph, no JSON here."


# ===========================================================================
# Self-growing board registry
# ===========================================================================


class TestBoardDiscovery:
    def _job(self, url, **raw):
        return _make_job(application_url=url, raw_data=raw)

    def test_extracts_greenhouse_and_lever_slugs(self):
        from src.intake.board_discovery import discover_board_slugs

        jobs = [
            self._job("https://boards.greenhouse.io/vercel/jobs/123"),
            self._job("https://job-boards.greenhouse.io/retool/jobs/456"),
            self._job("https://boards.greenhouse.io/embed/job_app?for=ramp&token=1"),
            self._job("https://jobs.lever.co/kraken/abc-def"),
            self._job("https://example.com/careers/apply"),  # non-ATS: ignored
            self._job(None, manual_apply_url="https://jobs.lever.co/attentive/x"),
        ]
        found = discover_board_slugs(jobs)
        assert found["greenhouse"] == {"vercel", "retool", "ramp"}
        assert found["lever"] == {"kraken", "attentive"}
        assert found["ashby"] == set()

    def test_extracts_ashby_slugs(self):
        from src.intake.board_discovery import discover_board_slugs

        jobs = [
            self._job("https://jobs.ashbyhq.com/notion/03143d98-a561-44c6-96a5"),
            self._job(
                "https://jobs.ashbyhq.com/linear/abc-def/application"
            ),
            self._job("https://example.com/careers/apply"),  # non-ATS: ignored
            self._job(None, external_url="https://jobs.ashbyhq.com/ramp/xyz"),
        ]
        found = discover_board_slugs(jobs)
        assert found["ashby"] == {"notion", "linear", "ramp"}
        assert found["greenhouse"] == set()
        assert found["lever"] == set()

    def test_register_appends_only_new_slugs(self, tmp_path):
        from src.intake.board_discovery import register_discovered_boards

        config = tmp_path / "companies.yaml"
        config.write_text(
            "# comment survives\ngreenhouse:\n  - stripe\n\nlever:\n  - outreach\n"
            "\nashby:\n  - notion\n",
            encoding="utf-8",
        )
        jobs = [
            self._job("https://boards.greenhouse.io/stripe/jobs/1"),  # known
            self._job("https://boards.greenhouse.io/vercel/jobs/2"),  # new
            self._job("https://jobs.lever.co/mistral/x"),  # new
            self._job("https://jobs.ashbyhq.com/notion/1"),  # known
            self._job("https://jobs.ashbyhq.com/linear/2"),  # new
        ]
        added = register_discovered_boards(jobs, tmp_path)
        assert added == 3
        import yaml as _yaml

        data = _yaml.safe_load(config.read_text(encoding="utf-8"))
        assert data["greenhouse"] == ["vercel", "stripe"]
        assert data["lever"] == ["mistral", "outreach"]
        assert data["ashby"] == ["linear", "notion"]
        assert "# comment survives" in config.read_text(encoding="utf-8")
        # Second pass is a no-op.
        assert register_discovered_boards(jobs, tmp_path) == 0

    def test_missing_config_is_noop(self, tmp_path):
        from src.intake.board_discovery import register_discovered_boards

        assert register_discovered_boards([self._job("https://jobs.lever.co/x/1")], tmp_path / "nope") == 0


# ===========================================================================
# Saved-search filters actually reaching plan_run + dead-token repair
# ===========================================================================


class TestPlanRunSearchFilters:
    def test_saved_profile_kwargs_mapping(self):
        from src.orchestration.plan_run import _saved_search_profile_kwargs

        saved = {
            "id": "tam",
            "source": "all",
            "keywords": ["technical account manager"],
            "locations": ["Dallas, TX", "United States"],
            "experience_levels": ["entry"],
            "employment_types": ["full_time"],
            "location_types": ["remote"],
            "education_levels": [],
            "pay_operator": ">=",
            "pay_amount": 90000,
            "experience_operator": "",
            "experience_years": None,
            "time_filter": "week",
            "ats": "",
            "company": "",
            "max_pages": 5,
        }
        with patch(
            "src.application.search_profiles.load_search_profiles_data",
            return_value={"profiles": [saved]},
        ):
            kwargs = _saved_search_profile_kwargs("tam")
        assert kwargs["keywords"] == ["technical account manager"]
        assert kwargs["locations"] == ["Dallas, TX", "United States"]
        assert kwargs["experience_levels"] == ["entry"]
        assert kwargs["pay_operator"] == ">=" and kwargs["pay_amount"] == 90000
        assert "ats" not in kwargs  # empty string means "no restriction"
        assert "experience_years" not in kwargs

    def test_missing_profile_returns_empty(self):
        from src.orchestration.plan_run import _saved_search_profile_kwargs

        with patch(
            "src.application.search_profiles.load_search_profiles_data",
            return_value={"profiles": []},
        ):
            assert _saved_search_profile_kwargs("nope") == {}
        assert _saved_search_profile_kwargs(None) == {}


class TestFilterTokenNormalization:
    def test_experience_aliases(self):
        from src.application.jobs import _normalize_experience_levels

        assert _normalize_experience_levels(["entry_level", "associate"]) == ["entry"]
        assert _normalize_experience_levels(["mid_senior"]) == ["senior"]
        assert _normalize_experience_levels(["entry", "senior"]) == ["entry", "senior"]

    def test_location_type_aliases(self):
        from src.application.jobs import _normalize_location_types

        assert _normalize_location_types(["remote", "hybrid", "onsite"]) == [
            "remote",
            "hybrid",
            "in_person",
        ]


# ===========================================================================
# Application-question drafting (Materials → Questions)
# ===========================================================================


class TestQuestionAnswers:
    def test_parse_clean_json(self):
        from src.application.question_answers import _parse_response

        answer, questions = _parse_response(
            '{"answer": "I led X.", "clarifying_questions": ["What was the metric?"]}'
        )
        assert answer == "I led X."
        assert questions == ["What was the metric?"]

    def test_parse_fenced_json_with_prose(self):
        from src.application.question_answers import _parse_response

        raw = 'Sure! Here you go:\n```json\n{"answer": "Done.", "clarifying_questions": []}\n```'
        answer, questions = _parse_response(raw)
        assert answer == "Done."
        assert questions == []

    def test_parse_garbage_falls_back_to_text(self):
        from src.application.question_answers import _parse_response

        answer, questions = _parse_response("Just a plain paragraph answer.")
        assert answer == "Just a plain paragraph answer."
        assert questions == []

    def test_empty_question_rejected(self):
        from src.application.question_answers import draft_question_answer

        result = draft_question_answer(question="   ")
        assert result["ok"] is False
        assert result["error_code"] == "empty_question"

    def test_draft_round_trip_with_mocked_llm(self):
        from src.application import question_answers as qa

        profile = {"identity": {"full_name": "A"}, "skills": {}, "story_bank": []}
        with (
            patch.object(qa, "_load_profile", return_value=profile),
            patch.object(qa, "_similar_saved_answers", return_value=[]),
            patch(
                "src.utils.llm.generate_text",
                return_value='{"answer": "My work at SDS...", "clarifying_questions": ["Which client?"]}',
            ),
        ):
            result = qa.draft_question_answer(
                question="Examples of exceptional performance?"
            )
        assert result["ok"] is True
        assert result["final"] is False
        assert result["clarifying_questions"] == ["Which client?"]

        # Second round with the clarification folded in -> final.
        with (
            patch.object(qa, "_load_profile", return_value=profile),
            patch.object(qa, "_similar_saved_answers", return_value=[]),
            patch(
                "src.utils.llm.generate_text",
                return_value='{"answer": "Final answer.", "clarifying_questions": []}',
            ) as mock_llm,
        ):
            result = qa.draft_question_answer(
                question="Examples of exceptional performance?",
                clarifications=[{"question": "Which client?", "answer": "Acme Corp"}],
            )
        assert result["ok"] is True and result["final"] is True
        # The user's clarification must reach the prompt verbatim.
        assert "Acme Corp" in mock_llm.call_args[0][0]


# ===========================================================================
# Email ingestion
# ===========================================================================


class TestEmailClassifier:
    def test_rejection(self):
        from src.intake.email_ingest import classify_message

        assert (
            classify_message(
                "Your application to Stripe",
                "Unfortunately we have decided to move forward with other candidates.",
            )
            == "rejected"
        )

    def test_rejection_outranks_interview_mention(self):
        from src.intake.email_ingest import classify_message

        assert (
            classify_message(
                "Update on your interview",
                "Thank you for taking the time to interview with us. "
                "Unfortunately, we will not be progressing.",
            )
            == "rejected"
        )

    def test_interview(self):
        from src.intake.email_ingest import classify_message

        assert (
            classify_message(
                "Next steps", "We'd like to schedule an interview — what is your availability?"
            )
            == "interview"
        )

    def test_oa_and_offer(self):
        from src.intake.email_ingest import classify_message

        assert classify_message("Stripe", "Please complete this HackerRank test") == "oa"
        assert classify_message("Congrats", "We are pleased to offer you the role") == "offer"

    def test_marketing_noise_ignored(self):
        from src.intake.email_ingest import classify_message

        assert classify_message("Weekly digest", "Here are 20 new jobs for you") is None


class TestEmailMatching:
    _APPS = [
        {"id": "1", "company": "Stripe", "title": "SC", "outcome": None},
        {"id": "2", "company": "Box", "title": "TAM", "outcome": None},
    ]

    def _msg(self, sender="", subject="", body=""):
        return {"from": sender, "subject": subject, "body": body, "date": ""}

    def test_matches_by_sender_domain(self):
        from src.intake.email_ingest import match_applications

        matches = match_applications(
            self._msg(sender="no-reply@stripe.com", subject="Application update"),
            self._APPS,
        )
        assert [a["id"] for a in matches] == ["1"]

    def test_short_company_requires_sender_match(self):
        from src.intake.email_ingest import match_applications

        # "Box" appearing only in a body must NOT match…
        no_match = match_applications(
            self._msg(sender="hr@example.com", body="check the box below"), self._APPS
        )
        assert not no_match
        # …but in the sender it does.
        matches = match_applications(
            self._msg(sender="recruiting@box.com", subject="Interview"), self._APPS
        )
        assert [a["id"] for a in matches] == ["2"]

    def test_escalation_rules(self):
        from src.intake.email_ingest import _should_escalate

        assert _should_escalate(None, "interview")
        assert _should_escalate("oa", "interview")
        assert not _should_escalate("interview", "oa")  # never downgrade
        assert _should_escalate("interview", "rejected")  # rejection is real info
        assert not _should_escalate("rejected", "rejected")

    def test_not_configured_is_structured(self):
        from src.intake.email_ingest import ingest_replies

        with patch("src.intake.email_ingest.email_settings", return_value=None):
            result = ingest_replies()
        assert result["ok"] is False
        assert result["error_code"] == "email_not_configured"


# ===========================================================================
# Cover letter self-critique pass
# ===========================================================================


_CL_DRAFT_A = (
    "As a software engineer with hands on experience building backend systems, "
    "I am applying for this role at TestCo. What draws me to this role is the "
    "emphasis on reliable, well tested systems that ship on a predictable "
    "cadence for real customers.\n\n"
    "In my most recent internship I owned a data pipeline that reduced nightly "
    "processing time from six hours to forty five minutes by rewriting the "
    "batch job to stream records instead of loading them all into memory at "
    "once. That change let the team ship reports a full business day earlier "
    "and cut infrastructure costs by nearly a third.\n\n"
    "I also spent a semester pairing with two teammates to redesign an "
    "internal review tool, moving validation logic out of the frontend and "
    "into a shared service so every client stayed consistent. The rework "
    "removed an entire class of bugs that had been reported for months by "
    "support staff.\n\n"
    "TestCo's focus on giving small teams direct ownership of the systems "
    "they build matches how I like to work: close to the problem, with clear "
    "accountability for outcomes rather than just output.\n\n"
    "I would welcome the chance to talk about how this experience could "
    "support the team's goals this year. Thank you for your time and "
    "consideration."
)

_CL_DRAFT_B = (
    "As a software engineer with hands on experience building backend systems, "
    "I am applying for this role at TestCo, whose SaaS onboarding product I "
    "have used firsthand and admired for how quickly new customers reach "
    "value.\n\n"
    "In my most recent internship I owned a data pipeline that cut nightly "
    "processing time from six hours to forty five minutes by rewriting the "
    "batch job to stream records instead of loading them all into memory at "
    "once, which let the team ship reports a full business day earlier.\n\n"
    "I also spent a semester pairing with two teammates to redesign an "
    "internal review tool, moving validation logic out of the frontend and "
    "into a shared service so every client stayed consistent, removing an "
    "entire class of bugs that had been reported for months.\n\n"
    "TestCo's focus on giving small teams direct ownership of the onboarding "
    "flows they build matches how I like to work: close to the problem, with "
    "clear accountability for outcomes rather than just output on a roadmap "
    "slide.\n\n"
    "I would welcome the chance to talk about how this experience could "
    "support the team's goals this year, and to learn more about the "
    "onboarding roadmap directly from the people building it. Thank you for "
    "your time and consideration."
)


class TestCoverLetterCritique:
    def _job(self):
        return _make_job()

    def _profile(self):
        return {"identity": {"full_name": "Test User"}, "skills": {}}

    def _evidence(self):
        return ["Built and shipped a data pipeline that cut processing time significantly."]

    def _call(self, **kwargs):
        from src.generation.cover_letter import _generate_with_llm

        return _generate_with_llm(
            self._job(), self._profile(), self._evidence(), **kwargs
        )

    def test_pass_verdict_returns_original(self):
        with (
            patch(
                "src.generation.cover_letter.generate_text", return_value=_CL_DRAFT_A
            ),
            patch(
                "src.utils.llm.generate_json",
                return_value={"pass": True, "problems": []},
            ) as mock_critique,
            patch("src.utils.llm.generate_text") as mock_revision,
        ):
            result = self._call()
        mock_critique.assert_called_once()
        mock_revision.assert_not_called()
        assert result == _CL_DRAFT_A

    def test_fail_verdict_returns_revision(self):
        with (
            patch(
                "src.generation.cover_letter.generate_text", return_value=_CL_DRAFT_A
            ),
            patch(
                "src.utils.llm.generate_json",
                return_value={
                    "pass": False,
                    "problems": ["opening never mentions what the company builds"],
                },
            ),
            patch("src.utils.llm.generate_text", return_value=_CL_DRAFT_B) as mock_revision,
        ):
            result = self._call()
        mock_revision.assert_called_once()
        # The critique problem list should have reached the revision prompt.
        revision_prompt = mock_revision.call_args.args[0]
        assert "opening never mentions what the company builds" in revision_prompt
        assert result == _CL_DRAFT_B

    def test_critique_exception_returns_original(self):
        with (
            patch(
                "src.generation.cover_letter.generate_text", return_value=_CL_DRAFT_A
            ),
            patch(
                "src.utils.llm.generate_json", side_effect=RuntimeError("boom")
            ),
            patch("src.utils.llm.generate_text") as mock_revision,
        ):
            result = self._call()
        mock_revision.assert_not_called()
        assert result == _CL_DRAFT_A

    def test_previous_attempt_skips_critique(self):
        with (
            patch(
                "src.generation.cover_letter.generate_text", return_value=_CL_DRAFT_A
            ),
            patch("src.utils.llm.generate_json") as mock_critique,
        ):
            result = self._call(previous_attempt="some earlier draft text")
        mock_critique.assert_not_called()
        assert result == _CL_DRAFT_A

    def test_length_feedback_skips_critique(self):
        with (
            patch(
                "src.generation.cover_letter.generate_text", return_value=_CL_DRAFT_A
            ),
            patch("src.utils.llm.generate_json") as mock_critique,
        ):
            result = self._call(length_feedback="please shorten this")
        mock_critique.assert_not_called()
        assert result == _CL_DRAFT_A

    def test_knob_false_skips_critique(self):
        with (
            patch(
                "src.generation.cover_letter.generate_text", return_value=_CL_DRAFT_A
            ),
            patch(
                "src.core.config.load_config",
                return_value={"generation": {"cover_letter_critique": False}},
            ),
            patch("src.utils.llm.generate_json") as mock_critique,
        ):
            result = self._call()
        mock_critique.assert_not_called()
        assert result == _CL_DRAFT_A
