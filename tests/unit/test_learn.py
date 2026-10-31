from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory.cli import app
from factory.config import EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel, upsert_platform_account
from factory.errors import FactoryError
from factory.stages import learn
from factory.stages.learn import Numbers


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def published(ctx: AppContext, platforms: tuple[str, ...] = ("youtube",)) -> int:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="vehicles"
    )
    channel_id = ctx.conn.execute("SELECT id FROM channels").fetchone()[0]
    for platform in platforms:  # a post row is all metrics need (publishing has its own tests)
        account = upsert_platform_account(ctx.conn, channel_id, platform, "@coilandcog", "manual")
        with ctx.conn:
            ctx.conn.execute(
                "INSERT INTO posts (content_id, platform_account_id, platform, status) "
                "VALUES (?, ?, ?, 'published')",
                (content_id, account, platform),
            )
    return content_id


def metrics(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM metrics").fetchall()


def test_numbers_are_stored_and_a_rewatch_share_above_100_is_allowed(ctx: AppContext) -> None:
    content_id = published(ctx)

    learn.add_manual(ctx, content_id, "youtube", "7d",
                     Numbers(views=1234, stayed_to_watch_pct=56.79, avg_view_pct=133))  # fmt: skip

    row = metrics(ctx.conn)[0]
    assert (row["checkpoint"], row["source"], row["views"]) == ("7d", "manual", 1234)
    assert (row["stayed_to_watch_pct"], row["avg_view_pct"]) == (56.79, 133)  # D-101


def test_entering_a_checkpoint_again_replaces_only_the_given_numbers(ctx: AppContext) -> None:
    content_id = published(ctx)
    learn.add_manual(ctx, content_id, "youtube", "7d", Numbers(views=100, likes=4))

    learn.add_manual(ctx, content_id, "youtube", "7d", Numbers(views=150))

    rows = metrics(ctx.conn)
    assert len(rows) == 1 and (rows[0]["views"], rows[0]["likes"]) == (150, 4)


@pytest.mark.parametrize(
    ("numbers", "problem"),
    [
        (Numbers(views=-1), "views can't be negative"),
        (Numbers(stayed_to_watch_pct=120), "at most 100"),
        (Numbers(avg_view_pct=1500), "is a typo"),
        (Numbers(), "give at least one number"),
    ],
)
def test_impossible_numbers_are_refused(ctx: AppContext, numbers: Numbers, problem: str) -> None:
    content_id = published(ctx)

    with pytest.raises(FactoryError, match=problem):
        learn.add_manual(ctx, content_id, "youtube", "7d", numbers)
    assert metrics(ctx.conn) == []


def test_platform_without_a_recorded_post_or_bad_options_are_refused(ctx: AppContext) -> None:
    content_id = published(ctx)

    with pytest.raises(FactoryError, match="has no tiktok post recorded"):
        learn.add_manual(ctx, content_id, "tiktok", "7d", Numbers(views=1))
    with pytest.raises(FactoryError, match="--checkpoint must be one of"):
        learn.add_manual(ctx, content_id, "youtube", "1w", Numbers(views=1))
    with pytest.raises(FactoryError, match="--platform must be one of"):
        learn.add_manual(ctx, content_id, "instagram", "7d", Numbers(views=1))


def test_show_lists_platforms_and_checkpoints_in_order(ctx: AppContext) -> None:
    content_id = published(ctx, ("youtube", "tiktok"))
    learn.add_manual(ctx, content_id, "youtube", "28d", Numbers(views=900))
    learn.add_manual(ctx, content_id, "youtube", "24h", Numbers(views=300, avg_view_duration_s=41))
    learn.add_manual(ctx, content_id, "tiktok", "7d", Numbers(views=50, shares=2))

    text = learn.show(ctx, content_id)

    assert text.splitlines() == [
        "#1 alternator",
        "  tiktok  7d   50 views | 2 shares  (manual)",
        "  youtube 24h  300 views | avg 41 s  (manual)",
        "  youtube 28d  900 views  (manual)",
    ]


def test_metrics_commands(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    runner = CliRunner()
    runner.invoke(app, ["db", "init", *opts])
    db = connect(config_dir.parent / "data" / "factory.db")
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    ctx = AppContext(load_settings(config_dir), env,
                     load_channel("coil-and-cog", config_dir), db, config_dir.parent)  # fmt: skip
    content_id = published(ctx)
    db.close()

    added = runner.invoke(
        app,
        ["metrics", "add", str(content_id), "--platform", "youtube", "--checkpoint", "7d",
         "--views", "1234", "--stayed", "56.8", "--avd-pct", "133", *opts],
    )  # fmt: skip
    empty = runner.invoke(app, ["metrics", "show", "99", *opts])

    assert added.exit_code == 0, added.output
    assert "youtube 7d   1234 views | stayed 56.8% | avg viewed 133%  (manual)" in added.output
    assert empty.exit_code == 1 and "content #99 does not exist" in empty.output


# --- labels (ROADMAP 2.9, D-119) ----------------------------------------------------------


def video(ctx: AppContext, name: str, views: int, stayed: float | None = None,
          viewed: float | None = None, fmt: str = "short", dry_run: int = 0) -> int:  # fmt: skip
    """A real, published YouTube video with 7-day numbers."""
    content_id = add_content(ctx.conn, ctx.channel, object_name=name, fmt=fmt, pillar="vehicles")
    channel_id = ctx.conn.execute("SELECT id FROM channels").fetchone()[0]
    account = upsert_platform_account(ctx.conn, channel_id, "youtube", "@coilandcog", "manual")
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO posts (content_id, platform_account_id, platform, status, dry_run) "
            "VALUES (?, ?, 'youtube', 'published', ?)",
            (content_id, account, dry_run),
        )
    numbers = Numbers(views=views, stayed_to_watch_pct=stayed, avg_view_pct=viewed)
    learn.add_manual(ctx, content_id, "youtube", "7d", numbers)
    return content_id


@pytest.mark.parametrize(
    ("views", "stayed", "viewed", "fmt", "label"),
    [
        (300, 75, 85, "short", "winner"),  # 3x median, stayed and viewed above the bar
        (300, None, 85, "short", "average"),  # no stayed-to-watch yet: can't prove a winner
        (300, 75, 79, "short", "average"),  # Shorts need 80% viewed
        (300, 75, 45, "deep_dive", "winner"),  # over 60 s: 40% is enough
        (50, 90, 120, "short", "loser"),  # half the median or less
        (200, 60, 39, "short", "loser"),  # viewed below half of 80%
        (200, 60, 70, "short", "average"),
    ],
)
def test_judge_follows_the_strategy_rules(
    views: int, stayed: float | None, viewed: float | None, fmt: str, label: str
) -> None:
    assert learn.judge(views, 100, stayed, viewed, fmt)[0] == label


def test_labels_use_the_channel_median_and_skip_dry_runs(ctx: AppContext) -> None:
    low = video(ctx, "alternator", 100, 60, 70)
    mid = video(ctx, "turbocharger", 400, 60, 70)
    high = video(ctx, "jet engine", 1500, 72, 90)  # median of 100/400/1500 = 400
    video(ctx, "test", 99999, 90, 90, dry_run=1)  # a dry-run post never counts

    got = {item.content_id: item for item in learn.compute_labels(ctx)}

    assert {k: v.label for k, v in got.items()} == {low: "loser", mid: "average", high: "winner"}
    assert (got[high].basis["median"], got[high].basis["median_of"]) == (400, 3)


def test_owner_label_survives_recompute_and_auto_hands_it_back(ctx: AppContext) -> None:
    video(ctx, "alternator", 100, 60, 70)
    content_id = video(ctx, "turbocharger", 400, 60, 70)
    video(ctx, "jet engine", 1500, 72, 90)
    learn.compute_labels(ctx)

    learn.set_label(ctx, content_id, "winner", "great comments")
    after = {i.content_id: i for i in learn.compute_labels(ctx)}[content_id]
    assert (after.label, after.overridden, after.notes) == ("winner", True, "great comments")
    assert "rule says average" in learn.labels_text([after])

    learn.set_label(ctx, content_id, "auto")
    back = {i.content_id: i for i in learn.labels(ctx)}[content_id]
    assert (back.label, back.overridden) == ("average", False)


def test_set_label_refuses_unknown_labels_and_items(ctx: AppContext) -> None:
    with pytest.raises(FactoryError, match="label must be one of"):
        learn.set_label(ctx, 1, "great")
    with pytest.raises(FactoryError, match="does not exist"):
        learn.set_label(ctx, 99, "winner")


def test_labels_commands(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    runner = CliRunner()
    runner.invoke(app, ["db", "init", *opts])
    db = connect(config_dir.parent / "data" / "factory.db")
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    ctx = AppContext(load_settings(config_dir), env,
                     load_channel("coil-and-cog", config_dir), db, config_dir.parent)  # fmt: skip
    content_id = video(ctx, "alternator", 300, 75, 85)
    db.close()

    shown = runner.invoke(app, ["labels", "show", *opts])
    changed = runner.invoke(app, ["labels", "set", str(content_id), "loser", "--note", "dull",
                                  *opts])  # fmt: skip

    assert shown.exit_code == 0, shown.output
    assert f"#{content_id} alternator: average" in shown.output  # alone: 300 = 1x its own median
    assert changed.exit_code == 0, changed.output
    assert f"#{content_id} alternator: loser (yours)  rule says average  note: dull" in (
        changed.output
    )
