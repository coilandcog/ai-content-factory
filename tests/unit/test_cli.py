from __future__ import annotations

from pathlib import Path

import typer
from typer.testing import CliRunner

from factory.cli import _make_stub, app

runner = CliRunner()

# The fixed stage names from AGENTS.md §1, written out so a rename breaks this test.
PIPELINE = ["discover", "ideate", "script", "generate", "assemble", "review", "publish", "learn"]


def test_help_lists_every_stage() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for stage in PIPELINE:
        assert stage in result.output


def test_stub_names_its_roadmap_task_and_fails(config_dir: Path) -> None:
    # Every pipeline stage is built (learn was the last, 2.5); the stub stays for new stages.
    runner.invoke(app, ["db", "init", "--config-dir", str(config_dir)])
    stub_app = typer.Typer()
    stub_app.command(name="later")(_make_stub("later", "9.9"))

    result = runner.invoke(stub_app, ["--config-dir", str(config_dir)])

    assert result.exit_code == 1
    assert "ROADMAP task 9.9" in result.output
