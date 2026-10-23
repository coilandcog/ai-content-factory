from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from factory.cli import app

runner = CliRunner()

# The fixed stage names from AGENTS.md §1, written out so a rename breaks this test.
PIPELINE = ["discover", "ideate", "script", "generate", "assemble", "review", "publish", "learn"]


def test_help_lists_every_stage() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for stage in PIPELINE:
        assert stage in result.output


def test_stub_names_its_roadmap_task_and_fails(config_dir: Path) -> None:
    runner.invoke(app, ["db", "init", "--config-dir", str(config_dir)])

    result = runner.invoke(app, ["script", "--config-dir", str(config_dir)])

    assert result.exit_code == 1
    assert "ROADMAP task 1.3" in result.output
