"""Phase 11.1 -- ordered fallback chain + error classification.

These tests cover the new behaviour layered on top of the existing
:class:`tests.test_llm.TestLLMFallback` cases:

* multi-element ``fallback_providers`` chains
* error classification (HTTP status + CLI text) and the transient gate
* attempt-chain bookkeeping on success, transient-only failure, and
  fatal abort
* :data:`src.utils.llm.last_attempt_chain` ContextVar plumbing
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.providers.base import (
    ProviderError,
    ProviderErrorKind,
    classify_cli_error,
    classify_http_status,
)
from src.utils.llm import LLMError, generate_text, get_llm_settings, last_attempt_chain


class TestWritersSyncListAndScalar:
    """Codex review P2: writers must keep both `fallback_providers` and
    `fallback_provider` in sync. After ``autoapply migrate`` creates the
    list form, every later writer (`update_llm_settings`, `use_provider_as_primary`,
    `use_cmd`) must update both shapes -- otherwise `generate_text` keeps
    reading a stale list."""

    def test_update_llm_settings_syncs_both_shapes(self, tmp_path):
        from src.core.config import update_llm_settings  # noqa: PLC0415

        config_path = tmp_path / "settings.yaml"
        config_path.write_text(
            "llm:\n  primary_provider: claude-cli\n  fallback_providers: [old-fallback]\n",
            encoding="utf-8",
        )
        update_llm_settings(
            "claude-cli",
            fallback_provider="codex-cli",
            allow_fallback=True,
            config_path=config_path,
        )
        import yaml  # noqa: PLC0415

        out = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert out["llm"]["fallback_provider"] == "codex-cli"
        assert out["llm"]["fallback_providers"] == ["codex-cli"]

    def test_update_llm_settings_clearing_clears_both(self, tmp_path):
        from src.core.config import update_llm_settings  # noqa: PLC0415

        config_path = tmp_path / "settings.yaml"
        config_path.write_text(
            "llm:\n"
            "  primary_provider: claude-cli\n"
            "  fallback_providers: [codex-cli]\n"
            "  fallback_provider: codex-cli\n",
            encoding="utf-8",
        )
        update_llm_settings(
            "claude-cli",
            fallback_provider=None,
            allow_fallback=False,
            config_path=config_path,
        )
        import yaml  # noqa: PLC0415

        out = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert out["llm"]["fallback_provider"] is None
        assert out["llm"]["fallback_providers"] == []


class TestUpdateLLMSettingsSmallTier:
    """Phase 17.9.9: `update_llm_settings` accepts an optional small-tier
    block via a three-state action (preserve / set / clear)."""

    def test_preserve_is_default(self, tmp_path):
        """Existing callers that don't pass small_tier_action keep
        whatever is already in the file."""
        from src.core.config import update_llm_settings  # noqa: PLC0415

        config_path = tmp_path / "settings.yaml"
        config_path.write_text(
            "llm:\n"
            "  primary_provider: claude-cli\n"
            "  small_provider: groq\n"
            "  small_model: llama-3.3-70b-versatile\n",
            encoding="utf-8",
        )
        update_llm_settings(
            "anthropic",
            fallback_provider=None,
            allow_fallback=False,
            config_path=config_path,
        )
        import yaml  # noqa: PLC0415

        out = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert out["llm"]["small_provider"] == "groq"
        assert out["llm"]["small_model"] == "llama-3.3-70b-versatile"

    def test_set_writes_both_keys(self, tmp_path):
        from src.core.config import update_llm_settings  # noqa: PLC0415

        config_path = tmp_path / "settings.yaml"
        config_path.write_text(
            "llm:\n  primary_provider: claude-cli\n",
            encoding="utf-8",
        )
        update_llm_settings(
            "claude-cli",
            fallback_provider=None,
            allow_fallback=False,
            small_provider="groq",
            small_model="llama-3.3-70b-versatile",
            small_tier_action="set",
            config_path=config_path,
        )
        import yaml  # noqa: PLC0415

        out = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert out["llm"]["small_provider"] == "groq"
        assert out["llm"]["small_model"] == "llama-3.3-70b-versatile"

    def test_set_with_empty_provider_writes_null(self, tmp_path):
        """Disabling the small tier via the action='set' path lands an
        explicit null so the YAML reflects writer intent."""
        from src.core.config import update_llm_settings  # noqa: PLC0415

        config_path = tmp_path / "settings.yaml"
        config_path.write_text(
            "llm:\n"
            "  primary_provider: claude-cli\n"
            "  small_provider: groq\n",
            encoding="utf-8",
        )
        update_llm_settings(
            "claude-cli",
            fallback_provider=None,
            allow_fallback=False,
            small_provider="",
            small_model="",
            small_tier_action="set",
            config_path=config_path,
        )
        import yaml  # noqa: PLC0415

        out = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert out["llm"]["small_provider"] is None
        assert out["llm"]["small_model"] is None

    def test_clear_removes_keys(self, tmp_path):
        from src.core.config import update_llm_settings  # noqa: PLC0415

        config_path = tmp_path / "settings.yaml"
        config_path.write_text(
            "llm:\n"
            "  primary_provider: claude-cli\n"
            "  small_provider: groq\n"
            "  small_model: x\n",
            encoding="utf-8",
        )
        update_llm_settings(
            "claude-cli",
            fallback_provider=None,
            allow_fallback=False,
            small_tier_action="clear",
            config_path=config_path,
        )
        import yaml  # noqa: PLC0415

        out = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert "small_provider" not in out["llm"]
        assert "small_model" not in out["llm"]

    def test_unknown_action_raises(self, tmp_path):
        from src.core.config import update_llm_settings  # noqa: PLC0415

        config_path = tmp_path / "settings.yaml"
        config_path.write_text(
            "llm:\n  primary_provider: claude-cli\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Unknown small_tier_action"):
            update_llm_settings(
                "claude-cli",
                fallback_provider=None,
                allow_fallback=False,
                small_tier_action="kaboom",
                config_path=config_path,
            )


class TestSettingsChain:
    def test_list_shape_is_honoured(self):
        settings = get_llm_settings(
            {
                "llm": {
                    "primary_provider": "claude-cli",
                    "fallback_providers": ["codex-cli", "openai"],
                    "allow_fallback": True,
                }
            }
        )
        assert settings["fallback_providers"] == ["codex-cli", "openai"]
        # fallback_provider mirrors the first entry for back-compat
        assert settings["fallback_provider"] == "codex-cli"

    def test_legacy_scalar_is_promoted_to_chain(self):
        settings = get_llm_settings(
            {
                "llm": {
                    "primary_provider": "claude-cli",
                    "fallback_provider": "codex-cli",
                    "allow_fallback": True,
                }
            }
        )
        assert settings["fallback_providers"] == ["codex-cli"]

    def test_primary_is_removed_from_chain(self):
        settings = get_llm_settings(
            {
                "llm": {
                    "primary_provider": "claude-cli",
                    "fallback_providers": ["claude-cli", "codex-cli"],
                    "allow_fallback": True,
                }
            }
        )
        # 'claude-cli' appears as primary so the chain skips the dup
        assert settings["fallback_providers"] == ["codex-cli"]

    def test_duplicates_in_chain_collapse(self):
        settings = get_llm_settings(
            {
                "llm": {
                    "primary_provider": "claude-cli",
                    "fallback_providers": ["codex-cli", "codex-cli", "openai"],
                    "allow_fallback": True,
                }
            }
        )
        assert settings["fallback_providers"] == ["codex-cli", "openai"]

    def test_comma_string_is_accepted(self):
        settings = get_llm_settings(
            {
                "llm": {
                    "primary_provider": "claude-cli",
                    "fallback_providers": "codex-cli, openai",
                    "allow_fallback": True,
                }
            }
        )
        assert settings["fallback_providers"] == ["codex-cli", "openai"]


class TestClassifiers:
    @pytest.mark.parametrize(
        ("status", "kind"),
        [
            (401, ProviderErrorKind.AUTH),
            (403, ProviderErrorKind.AUTH),
            (429, ProviderErrorKind.QUOTA),
            (400, ProviderErrorKind.BAD_REQUEST),
            (404, ProviderErrorKind.BAD_REQUEST),
            (500, ProviderErrorKind.SERVER),
            (503, ProviderErrorKind.SERVER),
            (200, ProviderErrorKind.UNKNOWN),
        ],
    )
    def test_classify_http_status(self, status, kind):
        assert classify_http_status(status) == kind

    @pytest.mark.parametrize(
        ("text", "kind"),
        [
            ("Claude CLI timed out after 60s", ProviderErrorKind.TIMEOUT),
            ("Please login first via `codex login`", ProviderErrorKind.AUTH),
            ("Rate limit exceeded for this minute", ProviderErrorKind.QUOTA),
            ("HTTP 429 from upstream", ProviderErrorKind.QUOTA),
            ("Codex CLI not found. Install with: npm install ...", ProviderErrorKind.AUTH),
            ("some weird thing happened", ProviderErrorKind.UNKNOWN),
        ],
    )
    def test_classify_cli_error(self, text, kind):
        assert classify_cli_error(text) == kind

    def test_transient_gate(self):
        assert ProviderErrorKind.NETWORK.is_transient is True
        assert ProviderErrorKind.QUOTA.is_transient is True
        assert ProviderErrorKind.AUTH.is_transient is True
        assert ProviderErrorKind.TIMEOUT.is_transient is True
        assert ProviderErrorKind.SERVER.is_transient is True
        assert ProviderErrorKind.UNKNOWN.is_transient is True
        # Non-transient: don't waste a fallback hop on a malformed prompt
        assert ProviderErrorKind.BAD_REQUEST.is_transient is False
        assert ProviderErrorKind.PARSE.is_transient is False


def _chain_config(*providers: str) -> dict:
    return {
        "llm": {
            "primary_provider": providers[0],
            "fallback_providers": list(providers[1:]),
            "allow_fallback": True,
        }
    }


class TestThreeProviderChain:
    """Three providers; both legacy CLI ids are exercised + the registry."""

    def test_succeeds_on_first(self):
        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_chain_config("claude-cli", "codex-cli"),
            ),
            patch("src.utils.llm._dispatch_via_registry_enabled", return_value=False),
            patch("src.utils.llm.claude_generate", return_value="primary win"),
            patch("src.utils.llm.codex_generate", side_effect=AssertionError("not reached")),
        ):
            assert generate_text("p") == "primary win"
            chain = last_attempt_chain.get()
            assert [a["provider"] for a in chain] == ["claude-cli"]
            assert chain[0]["ok"] is True

    def test_advances_past_transient_failures(self):
        # Two transient failures then a third success.
        primary_err = LLMError("claude rate limit hit")  # classify -> QUOTA
        secondary_err = LLMError("codex CLI timed out after 60s")  # classify -> TIMEOUT
        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_chain_config("claude-cli", "codex-cli"),
            ),
            patch("src.utils.llm._dispatch_via_registry_enabled", return_value=False),
            patch("src.utils.llm.claude_generate", side_effect=primary_err),
            patch("src.utils.llm.codex_generate", side_effect=secondary_err),
        ):
            with pytest.raises(LLMError) as excinfo:
                generate_text("p")

        attempts = excinfo.value.attempts
        assert [a["provider"] for a in attempts] == ["claude-cli", "codex-cli"]
        assert attempts[0]["kind"] == ProviderErrorKind.QUOTA.value
        assert attempts[1]["kind"] == ProviderErrorKind.TIMEOUT.value
        assert all(a["ok"] is False for a in attempts)

    def test_stops_on_non_transient(self):
        # First provider raises a ProviderError(BAD_REQUEST). The second
        # provider must NOT be called because retrying a malformed prompt
        # elsewhere just burns money on the same failure.
        bad = ProviderError("400 invalid prompt", kind=ProviderErrorKind.BAD_REQUEST)
        wrapped = LLMError("Provider 'claude-cli' failed: 400 invalid prompt")
        wrapped.__cause__ = bad

        codex_calls = {"n": 0}

        def codex_stub(*_args, **_kwargs):
            codex_calls["n"] += 1
            return "should not be reached"

        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_chain_config("claude-cli", "codex-cli"),
            ),
            patch("src.utils.llm._dispatch_via_registry_enabled", return_value=False),
            patch("src.utils.llm.claude_generate", side_effect=wrapped),
            patch("src.utils.llm.codex_generate", side_effect=codex_stub),
        ):
            with pytest.raises(LLMError, match="non-transient"):
                generate_text("p")

        assert codex_calls["n"] == 0


class TestAttemptChainSideChannel:
    def test_contextvar_is_reset_on_each_call(self):
        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_chain_config("claude-cli", "codex-cli"),
            ),
            patch("src.utils.llm._dispatch_via_registry_enabled", return_value=False),
            patch("src.utils.llm.claude_generate", side_effect=LLMError("first quota")),
            patch("src.utils.llm.codex_generate", return_value="ok"),
        ):
            assert generate_text("a") == "ok"
            first = list(last_attempt_chain.get())

        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_chain_config("claude-cli"),
            ),
            patch("src.utils.llm._dispatch_via_registry_enabled", return_value=False),
            patch("src.utils.llm.claude_generate", return_value="ok2"),
        ):
            assert generate_text("b") == "ok2"
            second = list(last_attempt_chain.get())

        assert len(first) == 2
        assert len(second) == 1  # ContextVar reset between calls


# ---------------------------------------------------------------------------
# Phase 17.9.5 -- small-tier dispatch
# ---------------------------------------------------------------------------


def _small_tier_config(
    *, primary: str = "claude-cli",
    small_provider: str | None = None,
    small_model: str | None = None,
    fallback: list[str] | None = None,
) -> dict:
    llm: dict = {
        "primary_provider": primary,
        "fallback_providers": list(fallback or []),
        "allow_fallback": bool(fallback),
    }
    if small_provider is not None:
        llm["small_provider"] = small_provider
    if small_model is not None:
        llm["small_model"] = small_model
    return {"llm": llm}


class TestSmallTier:
    def test_settings_normalize_small_provider_and_model(self):
        with patch(
            "src.utils.llm.load_config",
            return_value=_small_tier_config(
                primary="claude-cli",
                small_provider="codex-cli",
                small_model="o4-mini",
            ),
        ):
            settings = get_llm_settings()
        assert settings["small_provider"] == "codex-cli"
        assert settings["small_model"] == "o4-mini"

    def test_settings_default_when_unset(self):
        with patch(
            "src.utils.llm.load_config",
            return_value=_small_tier_config(primary="claude-cli"),
        ):
            settings = get_llm_settings()
        assert settings["small_provider"] is None
        assert settings["small_model"] is None

    def test_unknown_tier_raises(self):
        with patch(
            "src.utils.llm.load_config",
            return_value=_small_tier_config(primary="claude-cli"),
        ):
            with pytest.raises(LLMError, match="Unknown LLM tier"):
                generate_text("p", tier="huge")

    def test_small_tier_routes_to_small_provider(self):
        """When small_provider is set, tier='small' calls it first."""
        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_small_tier_config(
                    primary="claude-cli",
                    small_provider="codex-cli",
                ),
            ),
            patch("src.utils.llm._dispatch_via_registry_enabled", return_value=False),
            patch("src.utils.llm.claude_generate", side_effect=AssertionError("not reached")),
            patch("src.utils.llm.codex_generate", return_value="cheap answer") as cgen,
        ):
            assert generate_text("p", tier="small") == "cheap answer"
            assert cgen.call_count == 1

    def test_small_tier_falls_through_to_primary_when_unset(self):
        """No small_provider configured -> tier='small' uses the primary chain."""
        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_small_tier_config(primary="claude-cli"),
            ),
            patch("src.utils.llm._dispatch_via_registry_enabled", return_value=False),
            patch("src.utils.llm.claude_generate", return_value="primary answer"),
        ):
            assert generate_text("p", tier="small") == "primary answer"

    def test_small_model_threaded_through_dispatch(self):
        """When small_model is set, the override is passed to _call_provider."""
        recorded = {}

        def fake_dispatch(provider, prompt, *, system, timeout, output_format, model=None):
            recorded["provider"] = provider
            recorded["model"] = model
            return "ok"

        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_small_tier_config(
                    primary="claude-cli",
                    small_provider="codex-cli",
                    small_model="cheap-model-7b",
                ),
            ),
            patch("src.utils.llm._call_provider", side_effect=fake_dispatch),
        ):
            generate_text("p", tier="small")
        assert recorded["provider"] == "codex-cli"
        assert recorded["model"] == "cheap-model-7b"

    def test_primary_tier_does_not_thread_small_model(self):
        """tier='primary' (default) must NOT inject the small_model override."""
        recorded = {}

        def fake_dispatch(provider, prompt, *, system, timeout, output_format, model=None):
            recorded["provider"] = provider
            recorded["model"] = model
            return "ok"

        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_small_tier_config(
                    primary="claude-cli",
                    small_provider="codex-cli",
                    small_model="cheap-model-7b",
                ),
            ),
            patch("src.utils.llm._call_provider", side_effect=fake_dispatch),
        ):
            generate_text("p")  # default tier
        assert recorded["provider"] == "claude-cli"
        assert recorded["model"] is None

    def test_small_tier_chain_falls_back_to_primary_on_failure(self):
        """small_provider failure transiently advances into the primary chain."""
        with (
            patch(
                "src.utils.llm.load_config",
                return_value=_small_tier_config(
                    primary="claude-cli",
                    small_provider="codex-cli",
                ),
            ),
            patch("src.utils.llm._dispatch_via_registry_enabled", return_value=False),
            patch(
                "src.utils.llm.codex_generate",
                side_effect=LLMError("codex CLI timed out after 60s"),
            ),
            patch("src.utils.llm.claude_generate", return_value="primary rescue"),
        ):
            assert generate_text("p", tier="small") == "primary rescue"
            attempts = last_attempt_chain.get()
            assert [a["provider"] for a in attempts] == ["codex-cli", "claude-cli"]
