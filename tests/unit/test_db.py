from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory.cli import app
from factory.config import load_channel
from factory.db import connect, migrate, set_status, upsert_channel
from factory.errors import StatusTransitionError

ALL_TABLES = {
    "channels",
    "platform_accounts",
    "trend_signals",
    "contents",
    "content_signals",
    "scripts",
    "sources",
    "assets",
    "music_tracks",
    "reviews",
    "posts",
    "metrics",
    "reference_notes",  # 002 (D-100)
    "performance_labels",
    "runs",
    "api_usage",
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """A fresh, migrated database in a temp folder (never data/factory.db)."""
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def channel_id(conn: sqlite3.Connection) -> int:
    return upsert_channel(conn, load_channel("coil-and-cog"))


def add_content(conn: sqlite3.Connection, channel_id: int) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO contents (channel_id, format, object_name) "
            "VALUES (?, 'short', 'alternator')",
            (channel_id,),
        )
    return cur.lastrowid


def status_of(conn: sqlite3.Connection, content_id: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT status, status_before_failure, status_reason FROM contents WHERE id = ?",
        (content_id,),
    ).fetchone()


# --- migrations and channel rows -------------------------------------------------------


def test_migrate_creates_every_table_once(conn: sqlite3.Connection) -> None:
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert tables == ALL_TABLES
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert migrate(conn) == []  # second run: nothing left to apply


def test_upsert_channel_updates_instead_of_duplicating(conn: sqlite3.Connection) -> None:
    channel = load_channel("coil-and-cog")
    first = upsert_channel(conn, channel)
    second = upsert_channel(conn, channel.model_copy(update={"display_name": "Renamed"}))

    rows = conn.execute("SELECT id, display_name FROM channels").fetchall()
    assert first == second
    assert [(r["id"], r["display_name"]) for r in rows] == [(first, "Renamed")]


def test_foreign_keys_are_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        add_content(conn, channel_id=999)


# --- set_status -------------------------------------------------------------------------


def test_happy_path_reaches_analyzed(conn: sqlite3.Connection, channel_id: int) -> None:
    content_id = add_content(conn, channel_id)
    path = [
        "scripted",
        "scripted",  # Gate 1 redo
        "script_approved",
        "awaiting_clips",
        "generated",
        "assembled",
        "generated",  # Gate 2 redo
        "assembled",
        "approved",
        "scheduled",
        "published",
        "analyzed",
    ]
    for status in path:
        set_status(conn, content_id, status)

    assert status_of(conn, content_id)["status"] == "analyzed"


def test_manual_post_goes_from_approved_straight_to_published(
    conn: sqlite3.Connection, channel_id: int
) -> None:
    content_id = add_content(conn, channel_id)
    for status in ("scripted", "script_approved", "awaiting_clips", "generated", "assembled"):
        set_status(conn, content_id, status)
    set_status(conn, content_id, "approved")

    set_status(conn, content_id, "published")  # D-072: no "scheduled" step for manual posts

    assert status_of(conn, content_id)["status"] == "published"


@pytest.mark.parametrize("bad_target", ["approved", "published", "done", "idea"])
def test_illegal_move_is_refused(
    conn: sqlite3.Connection, channel_id: int, bad_target: str
) -> None:
    content_id = add_content(conn, channel_id)

    with pytest.raises(StatusTransitionError, match=f"'idea' -> '{bad_target}'"):
        set_status(conn, content_id, bad_target)
    assert status_of(conn, content_id)["status"] == "idea"


def test_failure_remembers_status_and_retry_restores_it(
    conn: sqlite3.Connection, channel_id: int
) -> None:
    content_id = add_content(conn, channel_id)
    set_status(conn, content_id, "scripted")
    set_status(conn, content_id, "failed", reason="Gemini 503 after 3 retries")

    row = status_of(conn, content_id)
    assert (row["status"], row["status_before_failure"]) == ("failed", "scripted")
    assert row["status_reason"] == "Gemini 503 after 3 retries"

    with pytest.raises(StatusTransitionError):
        set_status(conn, content_id, "idea")  # retry may only go back to 'scripted'

    set_status(conn, content_id, "scripted")
    row = status_of(conn, content_id)
    assert (row["status"], row["status_before_failure"]) == ("scripted", None)


def test_finished_items_cannot_fail(conn: sqlite3.Connection, channel_id: int) -> None:
    content_id = add_content(conn, channel_id)
    set_status(conn, content_id, "archived")

    with pytest.raises(StatusTransitionError):
        set_status(conn, content_id, "failed")


def test_unknown_content_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(StatusTransitionError, match="does not exist"):
        set_status(conn, 12345, "scripted")


# --- rules the database itself enforces --------------------------------------------------


def test_database_guards(conn: sqlite3.Connection, channel_id: int) -> None:
    content_id = add_content(conn, channel_id)
    with conn:
        account_id = conn.execute(
            "INSERT INTO platform_accounts (channel_id, platform) VALUES (?, 'youtube')",
            (channel_id,),
        ).lastrowid
        conn.execute("INSERT INTO scripts (content_id, version) VALUES (?, 1)", (content_id,))

    bad_statements = [
        # AI disclosure can't be switched off (D-039)
        (
            "INSERT INTO posts (content_id, platform_account_id, platform, ai_disclosure) "
            "VALUES (?, ?, 'youtube', 0)",
            (content_id, account_id),
        ),
        # affiliate links are disabled at launch (D-009)
        ("UPDATE contents SET affiliate_links_json = '[]' WHERE id = ?", (content_id,)),
        # reject needs a reason
        (
            "INSERT INTO reviews (content_id, gate, decision, channel) "
            "VALUES (?, 'script', 'reject', 'cli')",
            (content_id,),
        ),
        # only one current script version per content
        ("INSERT INTO scripts (content_id, version) VALUES (?, 2)", (content_id,)),
    ]
    for sql, params in bad_statements:
        with pytest.raises(sqlite3.IntegrityError), conn:
            conn.execute(sql, params)


# --- CLI ----------------------------------------------------------------------------------


def test_db_init_is_safe_to_rerun(config_dir: Path) -> None:
    runner = CliRunner()

    first = runner.invoke(app, ["db", "init", "--config-dir", str(config_dir)])
    second = runner.invoke(app, ["db", "init", "--config-dir", str(config_dir)])

    assert first.exit_code == 0, first.output
    assert "Migrations applied: 001_init.sql" in first.output
    assert second.exit_code == 0
    assert "Migrations: up to date" in second.output
    db = connect(config_dir.parent / "data" / "factory.db")
    assert db.execute("SELECT slug FROM channels").fetchall()[0]["slug"] == "coil-and-cog"
    db.close()


# --- factory drop (D-093) ------------------------------------------------------------------------

PRE_PUBLIC_PATH = ["scripted", "script_approved", "awaiting_clips", "generated", "assembled",
                   "approved"]  # fmt: skip


@pytest.mark.parametrize("stop", range(len(PRE_PUBLIC_PATH) + 1))
def test_anything_not_public_yet_can_be_archived(
    conn: sqlite3.Connection, channel_id: int, stop: int
) -> None:
    content_id = add_content(conn, channel_id)
    for status in PRE_PUBLIC_PATH[:stop]:
        set_status(conn, content_id, status)

    set_status(conn, content_id, "archived", reason="dropped: weak clips")

    assert status_of(conn, content_id)["status"] == "archived"


@pytest.mark.parametrize("public", ["scheduled", "published"])
def test_public_items_cannot_be_archived_even_after_failing(
    conn: sqlite3.Connection, channel_id: int, public: str
) -> None:
    content_id = add_content(conn, channel_id)
    for status in [*PRE_PUBLIC_PATH, public]:
        set_status(conn, content_id, status)

    with pytest.raises(StatusTransitionError):
        set_status(conn, content_id, "archived")
    set_status(conn, content_id, "failed")
    with pytest.raises(StatusTransitionError):
        set_status(conn, content_id, "archived")


def test_failed_item_can_be_dropped(conn: sqlite3.Connection, channel_id: int) -> None:
    content_id = add_content(conn, channel_id)
    set_status(conn, content_id, "scripted")
    set_status(conn, content_id, "failed")

    set_status(conn, content_id, "archived")

    assert status_of(conn, content_id)["status"] == "archived"
