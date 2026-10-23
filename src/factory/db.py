"""SQLite access: connections, migrations, channel rows and guarded status changes (DATABASE.md)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from factory.config import REPO_ROOT, ChannelConfig
from factory.errors import FactoryError, StatusTransitionError

MIGRATIONS_DIR = REPO_ROOT / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
_NOW_UTC = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"

# Allowed status moves from DATABASE.md §4. Moves into and out of 'failed' are handled
# separately in _is_allowed(), because they depend on the status before the failure.
TRANSITIONS: dict[str, frozenset[str]] = {
    "idea": frozenset({"scripted", "archived"}),
    "scripted": frozenset({"script_approved", "scripted", "rejected"}),  # scripted = Gate 1 redo
    "script_approved": frozenset({"awaiting_clips"}),
    "awaiting_clips": frozenset({"generated"}),
    "generated": frozenset({"assembled"}),
    "assembled": frozenset({"approved", "generated", "rejected"}),  # generated = Gate 2 redo
    "approved": frozenset({"scheduled"}),
    "scheduled": frozenset({"published"}),
    "published": frozenset({"analyzed"}),
    "analyzed": frozenset(),
    "rejected": frozenset({"archived"}),
    "archived": frozenset(),
    "failed": frozenset(),
}

# Statuses some stage works on. Only these can fail; finished or parked items can't.
CAN_FAIL = frozenset(
    {
        "idea",
        "scripted",
        "script_approved",
        "awaiting_clips",
        "generated",
        "assembled",
        "approved",
        "scheduled",
        "published",
    }
)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database with the settings every connection needs (DATABASE.md §1)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # rows can be read by column name: row["status"]
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite ignores REFERENCES unless this is on
    conn.execute("PRAGMA journal_mode = WAL")  # bot and stages can read while another writes
    return conn


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations in number order and return the names of the files applied.

    The number of the last applied file is stored in SQLite's built-in `user_version`.
    """
    current = schema_version(conn)
    applied: list[str] = []
    for number, path in _migration_files(migrations_dir):
        if number <= current:
            continue
        # One transaction per file: if any statement fails, none of the file is applied.
        script = f"BEGIN;\n{path.read_text(encoding='utf-8')}\nPRAGMA user_version = {number};\n"
        try:
            conn.executescript(script + "COMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise
        applied.append(path.name)
    return applied


def _migration_files(migrations_dir: Path) -> list[tuple[int, Path]]:
    files: dict[int, Path] = {}
    for path in migrations_dir.glob("*.sql"):
        match = _MIGRATION_NAME.match(path.name)
        if not match:
            raise FactoryError(f"migration '{path.name}' must be named NNN_description.sql")
        number = int(match.group(1))
        if number in files:
            raise FactoryError(f"two migrations share number {number:03d}")
        files[number] = path
    return sorted(files.items())


def schema_version(conn: sqlite3.Connection) -> int:
    """Number of the last applied migration; 0 means `factory db init` never ran."""
    return conn.execute("PRAGMA user_version").fetchone()[0]


def start_run(
    conn: sqlite3.Connection, command: str, channel_slug: str | None, dry_run: bool
) -> int:
    """Insert a 'running' row in runs and return its id (also the run_id in log lines)."""
    with conn:
        cur = conn.execute(
            "INSERT INTO runs (command, channel_slug, dry_run) VALUES (?, ?, ?)",
            (command, channel_slug, int(dry_run)),
        )
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    error: str | None = None,
    items_processed: int = 0,
    items_failed: int = 0,
) -> None:
    """Close a run row with its final status: ok, partial or failed."""
    with conn:
        conn.execute(
            f"""
            UPDATE runs SET status = ?, error = ?, items_processed = ?, items_failed = ?,
                            finished_at = {_NOW_UTC}
            WHERE id = ?
            """,
            (status, error, items_processed, items_failed, run_id),
        )


def upsert_channel(conn: sqlite3.Connection, channel: ChannelConfig) -> int:
    """Insert or update the channel row from its YAML and return its id.

    The YAML is the source of truth; the row only exists so foreign keys work.
    """
    with conn:  # commits on success, rolls back on error
        rows = conn.execute(
            """
            INSERT INTO channels (slug, display_name, niche, status) VALUES (?, ?, ?, ?)
            ON CONFLICT (slug) DO UPDATE SET
                display_name = excluded.display_name,
                niche = excluded.niche,
                status = excluded.status
            RETURNING id
            """,
            (channel.slug, channel.display_name, channel.niche.name, channel.status),
        ).fetchall()
    return rows[0]["id"]


def set_status(
    conn: sqlite3.Connection, content_id: int, new_status: str, reason: str | None = None
) -> None:
    """Move one content item to a new status, refusing any move DATABASE.md §4 doesn't allow.

    This is the only function that may change contents.status (CONVENTIONS.md §4).
    """
    row = conn.execute(
        "SELECT status, status_before_failure FROM contents WHERE id = ?", (content_id,)
    ).fetchone()
    if row is None:
        raise StatusTransitionError(f"content {content_id} does not exist")
    current = row["status"]
    if not _is_allowed(current, new_status, row["status_before_failure"]):
        raise StatusTransitionError(
            f"content {content_id}: '{current}' -> '{new_status}' is not an allowed move"
        )
    before_failure = current if new_status == "failed" else None
    with conn:
        # "AND status = ?" makes the update a no-op if another process changed it meanwhile.
        updated = conn.execute(
            f"""
            UPDATE contents
            SET status = ?, status_before_failure = ?, status_reason = ?, updated_at = {_NOW_UTC}
            WHERE id = ? AND status = ?
            """,
            (new_status, before_failure, reason, content_id, current),
        ).rowcount
    if updated != 1:
        raise StatusTransitionError(f"content {content_id} changed status while updating")


def _is_allowed(current: str, new: str, status_before_failure: str | None) -> bool:
    if new == "failed":
        return current in CAN_FAIL
    if current == "failed":
        # `factory retry` may only return the item to where it failed.
        return new == status_before_failure
    return new in TRANSITIONS.get(current, frozenset())
