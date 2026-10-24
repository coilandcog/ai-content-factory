from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory import cli
from factory.config import EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import FactoryError, StatusTransitionError
from factory.stages import metadata, publish

YT_URL = "https://youtube.com/shorts/AbCdEfGhI_1?si=share"
TT_URL = "https://www.tiktok.com/@coilandcog/video/7430000000000000001?lang=en"
METADATA = {
    "content_id": 0,
    "object": "zipper",
    "format": "short",
    "youtube": {
        "title": "Zipper in 40 seconds",
        "description": "How the slider locks the teeth.\n\nSources:\n- Z: https://example.org/z",
        "hashtags": ["#Engineering", "#HowItWorks", "#Zipper"],
        "render": "render_youtube.mp4",
    },
    "tiktok": {
        "caption": "The slider is a tiny wedge machine. #HowItWorks #Zipper #Engineering",
        "hashtags": ["#HowItWorks", "#Zipper", "#Engineering"],
        "render": "render_tiktok.mp4",
    },
}


def make_ctx(root: Path) -> AppContext:
    conn = connect(root / "data" / "factory.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    return AppContext(load_settings(), env, channel, conn, root)


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    context = make_ctx(tmp_path)
    yield context
    context.conn.close()


def item(ctx: AppContext, until: str = "approved") -> int:
    """An item walked through the real status path, with a metadata.json on disk."""
    content_id = add_content(ctx.conn, ctx.channel, object_name="zipper", fmt="short",
                             pillar="air_and_sea")  # fmt: skip
    path = ("scripted", "script_approved", "awaiting_clips", "generated", "assembled", "approved")
    for status in path[: path.index(until) + 1]:
        set_status(ctx.conn, content_id, status)
    metadata.asset_dir(ctx, content_id).mkdir(parents=True)
    metadata.write(ctx, content_id, {**METADATA, "content_id": content_id})
    return content_id


def status(ctx: AppContext, content_id: int) -> str:
    return ctx.conn.execute("SELECT status FROM contents WHERE id = ?", (content_id,)).fetchone()[0]


# --- links -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/shorts/AbCdEfGhI_1?si=share",
        "https://www.youtube.com/watch?v=AbCdEfGhI_1&t=3",
        "https://youtu.be/AbCdEfGhI_1",
        "https://m.youtube.com/shorts/AbCdEfGhI_1/",
    ],
)
def test_youtube_links_give_the_video_id(url: str) -> None:
    video_id, clean = publish.parse_post_url("youtube", url, "@coilandcog")

    assert video_id == "AbCdEfGhI_1"
    assert clean == "https://www.youtube.com/shorts/AbCdEfGhI_1"


def test_tiktok_link_gives_the_video_id() -> None:
    video_id, clean = publish.parse_post_url("tiktok", TT_URL, "@coilandcog")

    assert video_id == "7430000000000000001"
    assert clean == "https://www.tiktok.com/@coilandcog/video/7430000000000000001"


@pytest.mark.parametrize(
    ("platform", "url", "message"),
    [
        ("youtube", "https://youtube.com/@coilandcog", "not a YouTube video link"),
        ("youtube", "https://evil.example/shorts/AbCdEfGhI_1", "not a YouTube video link"),
        ("youtube", "https://youtube.com/shorts/short", "not a YouTube video link"),
        ("tiktok", "https://vm.tiktok.com/ZMabc123/", "short vm./vt. links"),
        ("tiktok", "https://www.tiktok.com/@someoneelse/video/123", "belongs to @someoneelse"),
    ],
)
def test_wrong_links_are_refused(platform: str, url: str, message: str) -> None:
    with pytest.raises(FactoryError, match=message):
        publish.parse_post_url(platform, url, "@coilandcog")


# --- recording posts ---------------------------------------------------------------------------


def test_first_post_records_it_and_publishes_the_item(ctx: AppContext) -> None:
    content_id = item(ctx)

    post = publish.record_manual_post(ctx, content_id, "youtube", YT_URL, ai_label_on=True)

    row = ctx.conn.execute("SELECT * FROM posts WHERE id = ?", (post.post_id,)).fetchone()
    assert post.created and status(ctx, content_id) == "published"
    assert row["external_id"] == "AbCdEfGhI_1" and row["status"] == "published"
    assert row["title"] == "Zipper in 40 seconds" and row["caption"].startswith("How the slider")
    assert row["hashtags"] == "#Engineering #HowItWorks #Zipper"
    assert (row["ai_disclosure"], row["privacy"], row["dry_run"]) == (1, "public", 0)
    assert row["published_at"]
    account = ctx.conn.execute(
        "SELECT * FROM platform_accounts WHERE id = ?", (row["platform_account_id"],)
    ).fetchone()
    assert (account["platform"], account["handle"]) == ("youtube", "@coilandcog")


def test_second_platform_only_adds_its_post(ctx: AppContext) -> None:
    content_id = item(ctx)
    publish.record_manual_post(ctx, content_id, "youtube", YT_URL, ai_label_on=True)

    post = publish.record_manual_post(ctx, content_id, "tiktok", TT_URL, ai_label_on=True)

    row = ctx.conn.execute("SELECT * FROM posts WHERE id = ?", (post.post_id,)).fetchone()
    assert row["title"] is None and row["caption"].startswith("The slider is a tiny wedge")
    assert status(ctx, content_id) == "published"
    assert ctx.conn.execute("SELECT count(*) FROM posts").fetchone()[0] == 2
    assert ctx.conn.execute("SELECT count(*) FROM platform_accounts").fetchone()[0] == 2


def test_same_link_twice_is_harmless_but_a_different_one_is_refused(ctx: AppContext) -> None:
    content_id = item(ctx)
    first = publish.record_manual_post(ctx, content_id, "youtube", YT_URL, ai_label_on=True)

    again = publish.record_manual_post(
        ctx, content_id, "youtube", "https://youtu.be/AbCdEfGhI_1", ai_label_on=True
    )
    with pytest.raises(FactoryError, match="already has a different youtube post"):
        publish.record_manual_post(
            ctx, content_id, "youtube", "https://youtu.be/ZZZZZZZZZZZ", ai_label_on=True
        )

    assert not again.created and again.post_id == first.post_id
    assert ctx.conn.execute("SELECT count(*) FROM posts").fetchone()[0] == 1


def test_one_video_cannot_be_recorded_for_two_items(ctx: AppContext) -> None:
    first, second = item(ctx), item(ctx)
    publish.record_manual_post(ctx, first, "youtube", YT_URL, ai_label_on=True)

    with pytest.raises(FactoryError, match=f"already recorded for #{first}"):
        publish.record_manual_post(ctx, second, "youtube", YT_URL, ai_label_on=True)

    assert status(ctx, second) == "approved"


def test_the_ai_label_must_be_confirmed(ctx: AppContext) -> None:
    content_id = item(ctx)

    with pytest.raises(FactoryError, match="AI label"):
        publish.record_manual_post(ctx, content_id, "tiktok", TT_URL, ai_label_on=False)

    assert status(ctx, content_id) == "approved"
    assert ctx.conn.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


@pytest.mark.parametrize("until", ["generated", "assembled"])
def test_only_gate_2_approved_items_can_be_published(ctx: AppContext, until: str) -> None:
    content_id = item(ctx, until=until)

    with pytest.raises(FactoryError, match="only Gate 2-approved items"):
        publish.record_manual_post(ctx, content_id, "youtube", YT_URL, ai_label_on=True)
    with pytest.raises(FactoryError, match="only Gate 2-approved items"):
        publish.upload_kit(ctx, content_id)


def test_unknown_platform_is_refused(ctx: AppContext) -> None:
    content_id = item(ctx)

    with pytest.raises(FactoryError, match="youtube or tiktok"):
        publish.record_manual_post(ctx, content_id, "instagram", YT_URL, ai_label_on=True)


def test_database_still_refuses_skipping_gate_2(ctx: AppContext) -> None:
    content_id = item(ctx, until="assembled")

    with pytest.raises(StatusTransitionError):
        set_status(ctx.conn, content_id, "published")


# --- upload kit and CLI ------------------------------------------------------------------------


def test_upload_kit_has_what_to_paste_and_drops_recorded_platforms(ctx: AppContext) -> None:
    content_id = item(ctx)

    kit = publish.upload_kit(ctx, content_id)
    publish.record_manual_post(ctx, content_id, "youtube", YT_URL, ai_label_on=True)
    after = publish.upload_kit(ctx, content_id)

    assert "Zipper in 40 seconds" in kit and "https://example.org/z" in kit
    assert "Altered or synthetic content: Yes" in kit and "AI-generated content: ON" in kit
    assert "The slider is a tiny wedge machine. #HowItWorks" in kit
    assert "\\\\wsl$\\" in kit  # folder path Windows Explorer can open
    assert "already recorded: https://www.youtube.com/shorts/AbCdEfGhI_1" in after
    assert "Altered or synthetic content" not in after and "AI-generated content: ON" in after


def test_cli_publish_manual(config_dir: Path) -> None:
    context = make_ctx(config_dir.parent)
    content_id = item(context)
    context.conn.close()
    runner = CliRunner()
    base = ["publish", "--manual", "--id", str(content_id), "--config-dir", str(config_dir)]
    record = [*base, "--platform", "youtube", "--url", YT_URL]

    not_manual = runner.invoke(cli.app, ["publish", "--config-dir", str(config_dir)])
    kit = runner.invoke(cli.app, base)
    refused = runner.invoke(cli.app, record)
    recorded = runner.invoke(cli.app, [*record, "--ai-label-on"])

    assert not_manual.exit_code == 1 and "ROADMAP task 2.3" in not_manual.output
    assert kit.exit_code == 0 and "UPLOAD KIT" in kit.output
    assert refused.exit_code == 1 and "--ai-label-on" in refused.output
    assert recorded.exit_code == 0, recorded.output
    assert "recorded on youtube" in recorded.output and "(published)" in recorded.output
