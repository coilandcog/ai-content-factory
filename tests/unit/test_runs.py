from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from factory.cli import app
from factory.db import connect

runner = CliRunner()


def invoke(config_dir: Path, *args: str) -> object:
    return runner.invoke(app, [*args, "--config-dir", str(config_dir)])


def runs_table(config_dir: Path) -> list[tuple[object, ...]]:
    conn = connect(config_dir.parent / "data" / "factory.db")
    rows = conn.execute("SELECT command, status, dry_run, error FROM runs ORDER BY id").fetchall()
    conn.close()
    return [tuple(row) for row in rows]


def test_every_command_writes_a_run_row_and_log_line(config_dir: Path) -> None:
    assert invoke(config_dir, "db", "init").exit_code == 0
    status = invoke(config_dir, "status")
    failed = invoke(config_dir, "learn", "--id", "999")

    assert status.exit_code == 0, status.output
    assert failed.exit_code == 1
    assert runs_table(config_dir) == [
        ("db init", "ok", 1, None),
        ("status", "ok", 1, None),
        ("learn", "failed", 1, "#999 has no published YouTube post to pull numbers for"),
    ]
    [log_file] = (config_dir.parent / "data" / "logs").glob("factory-*.jsonl")
    events = [json.loads(line)["event"] for line in log_file.read_text().splitlines()]
    assert events == ["db_initialised", "run_finished", "run_failed"]


def test_status_shows_mode_contents_budgets_and_runs(config_dir: Path) -> None:
    invoke(config_dir, "db", "init")

    result = invoke(config_dir, "status")

    assert "[DRY RUN]" in result.output
    assert "coil-and-cog (Coil & Cog) | mode: DRY RUN" in result.output
    assert "Contents: none yet" in result.output
    assert "gemini 0/60, youtube_data 0/3000, flow 0/50" in result.output
    assert "#1 db init: ok" in result.output


def test_commands_need_an_initialised_database(config_dir: Path) -> None:
    result = invoke(config_dir, "status")

    assert result.exit_code == 1
    assert "run `uv run factory db init` first" in result.output
