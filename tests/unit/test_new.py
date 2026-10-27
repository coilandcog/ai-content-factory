from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory.cli import app
from factory.config import load_channel
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import FactoryError

runner = CliRunner()


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    upsert_channel(connection, load_channel("coil-and-cog"))
    yield connection
    connection.close()


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM contents").fetchone()[0]


def test_add_content_stores_an_idea(conn: sqlite3.Connection) -> None:
    content_id = add_content(
        conn,
        load_channel("coil-and-cog"),
        object_name="  CVT  Gearbox ",
        fmt="short",
        pillar="vehicles",
    )

    row = conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
    assert (row["object_name"], row["format"], row["pillar"]) == (
        "cvt gearbox",
        "short",
        "vehicles",
    )
    assert row["status"] == "idea"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"fmt": "episode"}, "format 'episode' is not enabled"),
        ({"fmt": "reel"}, "format 'reel' is not enabled"),
        ({"pillar": "weapons"}, "pillar 'weapons' is not defined"),
        ({"object_name": "   "}, "--object must not be empty"),
    ],
)
def test_bad_input_is_refused_and_nothing_is_stored(
    conn: sqlite3.Connection, kwargs: dict[str, str], message: str
) -> None:
    args = {"object_name": "alternator", "fmt": "short", "pillar": "vehicles"} | kwargs

    with pytest.raises(FactoryError, match=message):
        add_content(conn, load_channel("coil-and-cog"), **args)
    assert count(conn) == 0


def test_new_and_status_commands(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    runner.invoke(app, ["db", "init", *opts])

    created = runner.invoke(
        app, ["new", "--object", "alternator", "--format", "short", "--pillar", "vehicles", *opts]
    )
    shown = runner.invoke(app, ["status", "1", *opts])
    missing = runner.invoke(app, ["status", "99", *opts])

    assert created.exit_code == 0, created.output
    assert "Created content #1: status idea" in created.output
    assert shown.exit_code == 0
    assert "object: alternator | format: short | pillar: vehicles" in shown.output
    assert "status: idea" in shown.output
    assert missing.exit_code == 1
    assert "content #99 does not exist" in missing.output


def test_status_shows_failure_details(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    runner.invoke(app, ["db", "init", *opts])
    runner.invoke(
        app,
        ["new", "--object", "jet engine", "--format", "short", "--pillar", "air_and_sea", *opts],
    )
    db = connect(config_dir.parent / "data" / "factory.db")
    set_status(db, 1, "failed", reason="Gemini 503 after 3 retries")
    db.close()

    shown = runner.invoke(app, ["status", "1", *opts])

    assert "status: failed" in shown.output
    assert "failed while: idea" in shown.output
    assert "reason: Gemini 503 after 3 retries" in shown.output


def test_drop_archives_an_item_with_the_reason(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    runner.invoke(app, ["db", "init", *opts])
    runner.invoke(
        app, ["new", "--object", "submarine", "--format", "short", "--pillar", "air_and_sea", *opts]
    )
    db = connect(config_dir.parent / "data" / "factory.db")
    for status in ("scripted", "script_approved", "awaiting_clips"):
        set_status(db, 1, status)

    dropped = runner.invoke(app, ["drop", "1", "--reason", "Veo draws warships", *opts])
    empty = runner.invoke(app, ["drop", "1", "--reason", "  ", *opts])

    assert dropped.exit_code == 0, dropped.output
    assert "#1 archived (dropped: Veo draws warships)" in dropped.output
    row = db.execute("SELECT status, status_reason FROM contents WHERE id = 1").fetchone()
    assert tuple(row) == ("archived", "dropped: Veo draws warships")
    assert empty.exit_code == 1 and "--reason must not be empty" in empty.output
    db.close()


def test_drop_refuses_a_published_item(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    runner.invoke(app, ["db", "init", *opts])
    runner.invoke(
        app, ["new", "--object", "alternator", "--format", "short", "--pillar", "vehicles", *opts]
    )
    db = connect(config_dir.parent / "data" / "factory.db")
    for status in ("scripted", "script_approved", "awaiting_clips", "generated", "assembled",
                   "approved", "published"):  # fmt: skip
        set_status(db, 1, status)
    db.close()

    result = runner.invoke(app, ["drop", "1", "--reason", "old", *opts])

    assert result.exit_code == 1
    assert "it is public, so delete or hide it on the platform" in result.output
