"""Phase 18.4: smoke tests for the ``autoapply cleanup`` CLI group.

The point isn't to exercise the cleanup engine end-to-end (the unit
tests cover the engine; that needs a live DB) -- it's to make sure
the command group is wired up and the help text is reachable so a
broken import or a missing decorator surfaces immediately.
"""

from __future__ import annotations

from click.testing import CliRunner

from src.cli.main import cli


def test_cleanup_group_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["cleanup", "--help"])
    assert result.exit_code == 0, result.output
    assert "scan" in result.output
    assert "clean" in result.output
    assert "restore" in result.output
    assert "purge-quarantine" in result.output


def test_cleanup_scan_help_works() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["cleanup", "scan", "--help"])
    assert result.exit_code == 0, result.output
    assert "--tenant" in result.output
