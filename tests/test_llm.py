"""Tests for LLM provider selection and fallback behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils.llm import LLMError, generate_text, get_llm_settings


class TestLLMSettings:
    def test_legacy_provider_maps_to_primary(self):
        settings = get_llm_settings({"llm": {"provider": "claude-cli", "timeout": 30}})
        assert settings["primary_provider"] == "claude-cli"
        assert settings["fallback_provider"] is None
        assert settings["timeout"] == 30


class TestDirectCLIInvocation:
    def test_claude_uses_system_prompt_flag(self):
        from src.utils.llm import claude_generate

        completed = type("Completed", (), {"returncode": 0, "stdout": "OK", "stderr": ""})()

        with (
            patch("src.utils.llm._resolve_executable", return_value=r"C:\tools\claude.exe"),
            patch("src.utils.llm.subprocess.run", return_value=completed) as mock_run,
        ):
            result = claude_generate("hello", system="be terse")

        assert result == "OK"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == r"C:\tools\claude.exe"
        assert "--system-prompt" in cmd
        assert "--system" not in cmd

    def test_claude_isolates_from_project_context(self):
        """Regression: invoking ``claude`` from the AutoApply repo
        made the CLI auto-discover this project's CLAUDE.md and slip
        into its coding-agent persona, so the model asked "What would
        you like me to work on in ``C:\\Projects\\AutoApply``?"
        instead of parsing the resume. We isolate by running the
        subprocess in an empty scratch dir; ``--bare`` is NOT used
        because on Claude Code 2.1.x it forces ANTHROPIC_API_KEY
        auth (breaking subscription users) and silently drops the
        positional prompt.
        """
        from src.utils.llm import claude_generate

        completed = type(
            "Completed", (), {"returncode": 0, "stdout": "OK", "stderr": ""}
        )()

        with (
            patch("src.utils.llm._resolve_executable", return_value=r"C:\tools\claude.exe"),
            patch("src.utils.llm.subprocess.run", return_value=completed) as mock_run,
        ):
            claude_generate("parse this resume please", system="be terse")

        cmd = mock_run.call_args[0][0]
        assert "--print" in cmd
        # ``--bare`` must NOT be present: it breaks subscription auth
        # and drops positional prompts on the 2.1.x CLI line.
        assert "--bare" not in cmd
        # Prompt is passed as the documented positional argument.
        assert cmd[-1] == "parse this resume please"
        # And the subprocess must run in an isolated cwd so the CLI
        # cannot auto-discover the AutoApply project.
        kwargs = mock_run.call_args.kwargs
        assert kwargs.get("cwd"), "Claude CLI must be invoked with an explicit cwd"
        assert "AutoApply" not in str(kwargs["cwd"])

    def test_codex_isolates_from_project_context(self):
        """Codex CLI has the same project-auto-discovery problem
        (AGENTS.md) when invoked from inside a repo. Same cwd
        isolation."""
        from src.utils.llm import codex_generate

        completed = type(
            "Completed", (), {"returncode": 0, "stdout": "FINAL", "stderr": ""}
        )()

        with (
            patch(
                "src.utils.llm._resolve_executable",
                return_value=r"C:\tools\codex.cmd",
            ),
            patch("src.utils.llm.subprocess.run", return_value=completed) as mock_run,
        ):
            codex_generate("parse this resume please", system="be terse")

        kwargs = mock_run.call_args.kwargs
        assert kwargs.get("cwd"), "Codex CLI must be invoked with an explicit cwd"
        assert "AutoApply" not in str(kwargs["cwd"])

    def test_codex_uses_resolved_executable_path(self):
        from src.utils.llm import codex_generate

        completed = type("Completed", (), {"returncode": 0, "stdout": "OK", "stderr": ""})()

        with (
            patch(
                "src.utils.llm._resolve_executable",
                return_value=r"C:\Users\me\AppData\Roaming\npm\codex.cmd",
            ),
            patch("src.utils.llm.subprocess.run", return_value=completed) as mock_run,
        ):
            result = codex_generate("hello")

        assert result == "OK"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == r"C:\Users\me\AppData\Roaming\npm\codex.cmd"
        assert "--output-last-message" in cmd
        assert "--full-auto" not in cmd
        assert "--sandbox" in cmd
        assert "workspace-write" in cmd
        assert "--skip-git-repo-check" in cmd

    def test_codex_returns_last_message_file_when_available(self):
        from src.utils.llm import codex_generate

        completed = type("Completed", (), {"returncode": 0, "stdout": "TRANSCRIPT", "stderr": ""})()

        def fake_run(cmd, **kwargs):
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_path.write_text("FINAL ANSWER", encoding="utf-8")
            return completed

        with (
            patch("src.utils.llm._resolve_executable", return_value="codex.cmd"),
            patch("src.utils.llm.subprocess.run", side_effect=fake_run),
        ):
            result = codex_generate("hello")

        assert result == "FINAL ANSWER"


class TestLLMFallback:
    def test_falls_back_from_codex_to_claude(self):
        with (
            patch(
                "src.utils.llm.load_config",
                return_value={
                    "llm": {
                        "primary_provider": "codex-cli",
                        "fallback_provider": "claude-cli",
                        "allow_fallback": True,
                    }
                },
            ),
            patch("src.utils.llm.codex_generate", side_effect=LLMError("codex boom")),
            patch("src.utils.llm.claude_generate", return_value="claude ok"),
        ):
            assert generate_text("hello") == "claude ok"

    def test_falls_back_from_claude_to_codex(self):
        with (
            patch(
                "src.utils.llm.load_config",
                return_value={
                    "llm": {
                        "primary_provider": "claude-cli",
                        "fallback_provider": "codex-cli",
                        "allow_fallback": True,
                    }
                },
            ),
            patch("src.utils.llm.claude_generate", side_effect=LLMError("claude boom")),
            patch("src.utils.llm.codex_generate", return_value="codex ok"),
        ):
            assert generate_text("hello") == "codex ok"

    def test_raises_when_fallback_disabled(self):
        with (
            patch(
                "src.utils.llm.load_config",
                return_value={
                    "llm": {
                        "primary_provider": "claude-cli",
                        "fallback_provider": "codex-cli",
                        "allow_fallback": False,
                    }
                },
            ),
            patch("src.utils.llm.claude_generate", side_effect=LLMError("claude boom")),
        ):
            with pytest.raises(LLMError, match="All configured LLM providers failed"):
                generate_text("hello")
