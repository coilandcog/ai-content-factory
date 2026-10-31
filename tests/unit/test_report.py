from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fakes.fake_telegram import OWNER, FakeTelegram
from typer.testing import CliRunner

from factory.cli import app
from factory.config import EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel, upsert_platform_account
from factory.stages import learn, report
from factory.stages.learn import Numbers

NOW = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)  # Monday 06:00 in Berlin


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def post(ctx: AppContext, name: str, published_at: str, views: int | None = None,
         stayed: float | None = None, viewed: float | None = None,
         hook: str = "question") -> int:  # fmt: skip
    content_id = add_content(ctx.conn, ctx.channel, object_name=name, fmt="short",
                             pillar="vehicles")  # fmt: skip
    channel_id = ctx.conn.execute("SELECT id FROM channels").fetchone()[0]
    account = upsert_platform_account(ctx.conn, channel_id, "youtube", "@coilandcog", "manual")
    with ctx.conn:
        ctx.conn.execute("UPDATE contents SET hook_type = ? WHERE id = ?", (hook, content_id))
        ctx.conn.execute(
            "INSERT INTO posts (content_id, platform_account_id, platform, status, dry_run, "
            "published_at) VALUES (?, ?, 'youtube', 'published', 0, ?)",
            (content_id, account, published_at),
        )
    if views is not None:
        numbers = Numbers(views=views, stayed_to_watch_pct=stayed, avg_view_pct=viewed)
        learn.add_manual(ctx, content_id, "youtube", "7d", numbers)
    return content_id


def test_last_week_is_the_full_monday_to_sunday_before_today() -> None:
    assert report.last_week(date(2026, 11, 2)) == (date(2026, 10, 26), date(2026, 11, 1))
    assert report.last_week(date(2026, 11, 4)) == (date(2026, 10, 26), date(2026, 11, 1))


def test_weekly_report_lists_posts_labels_patterns_and_todo(ctx: AppContext) -> None:
    post(ctx, "alternator", "2026-10-20T16:00:00Z", 100, 60, 70)
    post(ctx, "turbocharger", "2026-10-21T16:00:00Z", 400, None, 70, hook="number")
    post(ctx, "jet engine", "2026-10-22T16:00:00Z", 1500, 72, 90)
    post(ctx, "cvt gearbox", "2026-11-01T16:00:00Z")  # posted last week, no numbers yet

    text = report.weekly(ctx, NOW)

    assert text.startswith("Coil & Cog: weekly report Mon 26 Oct – Sun 01 Nov 2026")
    assert "  #4 cvt gearbox on youtube (Sun 01 Nov): no numbers yet" in text
    assert "alternator on youtube" not in text  # posted the week before
    assert "  WINNER #3 jet engine: 1500 views (median 400 of 3), stayed 72%" in text
    assert "  LOSER #1 alternator: 100 views (median 400 of 3)" in text
    assert "  1 winner · 1 average · 1 loser" in text
    assert "  Hook: number 400 (0/1/0) · question 800 (1/0/1)" in text
    assert "#2: add stayed-to-watch from YouTube Studio" in text
    assert "#4 cvt gearbox: 7-day numbers final from" in text


def test_report_with_no_videos_still_reads_well(ctx: AppContext) -> None:
    text = report.weekly(ctx, NOW)

    assert "Posted last week:\n  nothing" in text
    assert "0 winner · 0 average · 0 loser" in text
    assert "Patterns" not in text


def test_send_marks_dry_run_and_splits_long_reports(ctx: AppContext) -> None:
    tg = FakeTelegram()

    sent = report.send(ctx, tg.client(), OWNER, "line\n" * 2000)

    assert sent == 3
    assert tg.texts[0].startswith("[DRY RUN] line")
    assert all(body["chat_id"] == OWNER for body in tg.sent())


def test_report_command_prints_and_needs_telegram_settings_to_send(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    opts = ["--config-dir", str(config_dir)]
    runner = CliRunner()
    runner.invoke(app, ["db", "init", *opts])

    printed = runner.invoke(app, ["report", "weekly", "--no-send", *opts])
    refused = runner.invoke(app, ["report", "weekly", *opts])

    assert printed.exit_code == 0, printed.output
    assert "weekly report" in printed.output and "Sent to Telegram" not in printed.output
    assert refused.exit_code == 1 and "TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_ID" in (
        refused.output
    )
