from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.matching.pipeline import get_pipeline_version, writes_review_queue
from src.matching.profile_v2 import (
    canonical_hash,
    load_candidate,
    load_targets,
    resolve_target,
    to_legacy_profile,
)
from src.matching.target_schema import (
    CandidateProfileV2,
    DuplicateKeyError,
    load_unique_yaml,
    phrase_in_text,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CANDIDATE = ROOT / "data/profile/candidate.yaml.example"


def test_pipeline_mode_defaults_to_v1_and_shadow_never_writes_queue() -> None:
    assert get_pipeline_version({}) == "v1"
    assert get_pipeline_version({"matching": {"pipeline_version": "v2_shadow"}}) == (
        "v2_shadow"
    )
    assert not writes_review_queue("v2_shadow")
    assert writes_review_queue("v2")
    with pytest.raises(ValueError):
        get_pipeline_version({"matching": {"pipeline_version": "maybe"}})


def test_unique_yaml_loader_rejects_duplicate_identity_location() -> None:
    text = "identity:\n  location: Dallas, TX\n  location: Portland, OR\n"
    with pytest.raises(DuplicateKeyError, match="location"):
        load_unique_yaml(text)


def test_canonical_candidate_is_strict_and_references_are_valid() -> None:
    candidate = load_candidate(EXAMPLE_CANDIDATE)
    assert candidate.identity.location == "Chicago, IL"
    assert candidate.identity.work_authorization.status == "citizen"
    assert candidate.identity.work_authorization.sponsorship_needed is False
    assert "US Remote" in candidate.preferences.preferred_locations
    assert candidate.capabilities
    dumped = candidate.model_dump(mode="json")
    dumped["unexpected"] = True
    with pytest.raises(ValidationError):
        CandidateProfileV2.model_validate(dumped)


def test_five_targets_share_candidate_hash_but_have_distinct_intent() -> None:
    candidate = load_candidate(EXAMPLE_CANDIDATE)
    targets = load_targets()
    assert len(targets) == 5
    assert canonical_hash(candidate)
    assert len({canonical_hash(target) for target in targets}) == 5
    assert len({tuple(target.discovery.query_terms) for target in targets}) == 5
    assert len({tuple(target.role.core_titles) for target in targets}) == 5


def test_hash_is_format_independent() -> None:
    left = {"b": [2, 3], "a": 1}
    right = {"a": 1, "b": [2, 3]}
    assert canonical_hash(left) == canonical_hash(right)


def test_phrase_matching_never_matches_short_ai_inside_gainsight() -> None:
    assert phrase_in_text("AI", "AI implementation specialist")
    assert not phrase_in_text("AI", "Gainsight administrator")
    assert phrase_in_text("solutions engineer", "Associate Solutions Engineer")


def test_legacy_adapter_keeps_generation_evidence_grounded() -> None:
    candidate = load_candidate(EXAMPLE_CANDIDATE)
    target = load_targets()[0]
    target = target.model_copy(
        update={
            "candidacy": target.candidacy.model_copy(
                update={"required_capability_groups": [], "preferred_capabilities": []}
            )
        }
    )
    resolved = resolve_target(candidate, target)
    legacy = to_legacy_profile(resolved)
    assert legacy["work_experiences"]
    assert "projects" in legacy
    assert "story_bank" in legacy
    assert legacy["target"]["id"] == resolved.target.id
    first_bullet = legacy["work_experiences"][0]["bullets"][0]
    assert "text" in first_bullet
    assert "id" not in first_bullet


def test_canonical_files_are_inside_expected_authority_paths() -> None:
    assert EXAMPLE_CANDIDATE.exists()
    assert len(list((ROOT / "config/targets").glob("*.yaml"))) == 5
    assert (ROOT / "config/portfolio.yaml").exists()
    assert (ROOT / "config/source_policy.yaml").exists()
