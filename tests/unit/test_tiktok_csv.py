from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory.cli import app
from factory.config import EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel, upsert_platform_account
from factory.errors import IntegrationError
from factory.stages import learn, tiktok_csv
from factory.stages.learn import Numbers

FIXTURES = Path(__file__).parent.parent / "fixtures" / "tiktok"
JET, ALTERNATOR = "7702707468513594646", "7700352799296556310"
DOWNLOADED = datetime.now(UTC) - timedelta(hours=1)  # a file can't be newer than now


def ago(days: float) -> str:
    """A publish time this many days before the download."""
    return (DOWNLOADED - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def tiktok_post(ctx: AppContext, name: str, video_id: str, published_at: str) -> int:
    content_id = add_content(ctx.conn, ctx.channel, object_name=name, fmt="short",
                             pillar="vehicles")  # fmt: skip
    channel_id = ctx.conn.execute("SELECT id FROM channels").fetchone()[0]
    account = upsert_platform_account(ctx.conn, channel_id, "tiktok", "@coilandcog", "manual")
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO posts (content_id, platform_account_id, platform, status, external_id, "
            "published_at, dry_run) VALUES (?, ?, 'tiktok', 'published', ?, ?, 0)",
            (content_id, account, video_id, published_at),
        )
    return content_id


def in_inbox(ctx: AppContext, fixture: str, when: datetime = DOWNLOADED) -> Path:
    folder = tiktok_csv.inbox(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "Content.csv"
    shutil.copy(FIXTURES / fixture, path)
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def rows(ctx: AppContext) -> list[tuple]:
    return [tuple(r) for r in ctx.conn.execute(
        "SELECT p.content_id, m.checkpoint, m.source, m.views, m.likes, m.comments, m.shares "
        "FROM metrics m JOIN posts p ON p.id = m.post_id ORDER BY p.content_id"
    ).fetchall()]  # fmt: skip


@pytest.mark.parametrize(
    ("cell", "value"),
    [("1,234", 1234), ("1.2K", 1200), ("3M", 3_000_000), (" 7 ", 7), ("", None), ("--", None)],
)
def test_numbers_as_tiktok_writes_them(cell: str, value: int | None) -> None:
    assert tiktok_csv.number(cell) == value


@pytest.mark.parametrize(
    ("hours", "checkpoint"),
    [(23, None), (24, "24h"), (71, "24h"), (72, "72h"), (9 * 24, "7d"), (40 * 24, "28d")],
)
def test_a_row_counts_as_the_latest_checkpoint_reached(hours: int, checkpoint: str) -> None:
    assert tiktok_csv.checkpoint_for(hours * 3600) == checkpoint


def test_the_real_header_only_export_reads_as_no_rows() -> None:
    text = (FIXTURES / "content_header_only.csv").read_bytes().decode("utf-8-sig")

    assert tiktok_csv.parse(text) == []


def test_import_stores_matched_rows_once_and_moves_the_file(ctx: AppContext) -> None:
    jet = tiktok_post(ctx, "jet engine", JET, ago(8))
    alternator = tiktok_post(ctx, "alternator", ALTERNATOR, ago(14))
    path = in_inbox(ctx, "content_rows.csv")

    result = tiktok_csv.import_files(ctx)

    assert rows(ctx) == [(jet, "7d", "csv", 1200, 1204, 35, 12),
                         (alternator, "7d", "csv", 0, 0, 0, 0)]  # fmt: skip
    assert result.unmatched == ["Someone else posted this"]
    assert not path.exists() and (path.parent / "imported" / "Content.csv").exists()

    in_inbox(ctx, "content_rows.csv")  # the same download again: nothing changes
    again = tiktok_csv.import_files(ctx)
    assert (again.stored, again.kept) == ([], 2)
    assert len(rows(ctx)) == 2


def test_typed_numbers_are_never_replaced(ctx: AppContext) -> None:
    jet = tiktok_post(ctx, "jet engine", JET, ago(8))
    learn.add_manual(ctx, jet, "tiktok", "7d", Numbers(views=200))
    in_inbox(ctx, "content_rows.csv")

    tiktok_csv.import_files(ctx)

    assert rows(ctx) == [(jet, "7d", "manual", 200, None, None, None)]


def test_a_video_under_24_hours_old_is_not_stored(ctx: AppContext) -> None:
    jet = tiktok_post(ctx, "jet engine", JET, ago(0.5))
    in_inbox(ctx, "content_rows.csv")

    result = tiktok_csv.import_files(ctx)

    assert result.too_new == [f"#{jet}"] and rows(ctx) == []


def test_a_broken_file_is_named_and_stays(ctx: AppContext, tmp_path: Path) -> None:
    folder = tiktok_csv.inbox(ctx)
    folder.mkdir(parents=True)
    (folder / "Overview.csv").write_text('"Date","Video views"\n"Nov 1","3"\n')
    (folder / "bad.csv").write_text('"Video link","Total views"\n"x","lots"\n')

    result = tiktok_csv.import_files(ctx)

    assert result.files == 0
    assert result.broken == [
        "Overview.csv: no 'Video link' and 'Total views' columns (not a Content export?)",
        "bad.csv: 'lots' is not a number",
    ]
    assert (folder / "Overview.csv").exists() and (folder / "bad.csv").exists()
    with pytest.raises(IntegrationError, match="empty"):
        tiktok_csv.parse("")


def test_import_tiktok_command(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    runner = CliRunner()
    runner.invoke(app, ["db", "init", *opts])
    db = connect(config_dir.parent / "data" / "factory.db")
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    ctx = AppContext(load_settings(config_dir), env,
                     load_channel("coil-and-cog", config_dir), db, config_dir.parent)  # fmt: skip
    jet = tiktok_post(ctx, "jet engine", JET, ago(8))
    path = in_inbox(ctx, "content_rows.csv")
    db.close()

    empty = runner.invoke(app, ["metrics", "import-tiktok", str(FIXTURES / "nothing.csv"), *opts])
    done = runner.invoke(app, ["metrics", "import-tiktok", str(path), *opts])

    assert "nothing.csv" in empty.output
    assert done.exit_code == 0, done.output
    assert f"#{jet} 7d: 1200 views" in done.output
    assert "no TikTok post recorded for 'Alternator in 40 seconds'" in done.output
    assert path.exists()  # a file you name yourself is not moved
