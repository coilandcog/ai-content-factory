from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response
from typer.testing import CliRunner

from factory import cli
from factory.budgets import usage_today
from factory.config import EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import (
    add_content,
    connect,
    migrate,
    set_status,
    upsert_channel,
    upsert_platform_account,
)
from factory.errors import FactoryError, RetryableError, StatusTransitionError
from factory.integrations import youtube
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

    no_id = runner.invoke(cli.app, ["publish", "--config-dir", str(config_dir)])
    kit = runner.invoke(cli.app, base)
    refused = runner.invoke(cli.app, record)
    recorded = runner.invoke(cli.app, [*record, "--ai-label-on"])

    assert no_id.exit_code == 1 and "publish needs --id" in no_id.output
    assert kit.exit_code == 0 and "UPLOAD KIT" in kit.output
    assert refused.exit_code == 1 and "--ai-label-on" in refused.output
    assert recorded.exit_code == 0, recorded.output
    assert "recorded on youtube" in recorded.output and "(published)" in recorded.output


# --- scheduled YouTube publishing (ROADMAP 2.3, D-115) -----------------------------------------

# Saturday 31 Oct 2026, 13:00 in Berlin (CET, UTC+1): 17:00 local is 16:00 UTC.
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
TODAY_17 = datetime(2026, 10, 31, 16, 0, tzinfo=UTC)


class FakeRequest:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers

    def execute(self) -> Any:
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def next_chunk(self) -> Any:
        return None, self.execute()


class FakeYouTube:
    """Stands in for googleapiclient's YouTube client: channel check, uploads, status lookups."""

    def __init__(self, upload: list[Any] | None = None, privacy: dict[str, str] | None = None):
        self.inserted: list[dict[str, Any]] = []
        self.upload_answers = upload or [{"id": "VIDEO000001"}]
        self.privacy = privacy or {}
        self.status_lookups: list[str] = []

    def channels(self) -> FakeYouTube:
        return self

    def videos(self) -> FakeYouTube:
        return self

    def list(self, **kwargs: Any) -> FakeRequest:
        if kwargs.get("mine"):
            item = {"id": "UC123", "snippet": {"title": "Coil & Cog", "customUrl": "@coilandcog"}}
            return FakeRequest([{"items": [item]}])
        assert kwargs["part"] == "status"
        self.status_lookups.append(kwargs["id"])
        asked = kwargs["id"].split(",")
        items = [{"id": vid, "status": {"privacyStatus": privacy}}
                 for vid, privacy in self.privacy.items() if vid in asked]  # fmt: skip
        return FakeRequest([{"items": items}])

    def insert(self, **kwargs: Any) -> FakeRequest:
        self.inserted.append(kwargs)
        return FakeRequest(self.upload_answers)


def builder_for(api: FakeYouTube) -> Any:
    return lambda name, version, **kwargs: api


def real(ctx: AppContext) -> AppContext:
    """The same context with DRY_RUN off."""
    env = EnvSettings(dry_run=False, factory_channel="coil-and-cog")
    return AppContext(ctx.settings, env, ctx.channel, ctx.conn, ctx.root)


def approved(ctx: AppContext, *, decision: str = "approve") -> int:
    """An approved item with its Gate 2 review row and a render on disk."""
    content_id = item(ctx)
    ctx.conn.execute(
        "INSERT INTO reviews (content_id, gate, decision, reason, channel) "
        "VALUES (?, 'final', 'approve', NULL, 'cli')",
        (content_id,),
    )
    if decision != "approve":
        ctx.conn.execute(
            "INSERT INTO reviews (content_id, gate, decision, reason, channel) "
            "VALUES (?, 'final', ?, 'not good', 'cli')",
            (content_id, decision),
        )
    ctx.conn.commit()
    (metadata.asset_dir(ctx, content_id) / "render_youtube.mp4").write_bytes(b"fake mp4")
    return content_id


def schedule(ctx: AppContext, content_id: int, api: FakeYouTube, **kwargs: Any) -> Any:
    return publish.schedule_youtube(
        ctx, content_id, now=NOW, load=lambda path: object(), builder=builder_for(api), **kwargs
    )


def scheduled_post(ctx: AppContext, content_id: int, video_id: str, when: datetime) -> None:
    """A scheduled YouTube post written straight into the database."""
    channel_id = ctx.conn.execute("SELECT id FROM channels").fetchone()[0]
    account = upsert_platform_account(ctx.conn, channel_id, "youtube", "@coilandcog",
                                      "api_scheduled")  # fmt: skip
    ctx.conn.execute(
        "INSERT INTO posts (content_id, platform_account_id, platform, status, external_id, url, "
        "privacy, scheduled_for, dry_run) VALUES (?, ?, 'youtube', 'scheduled', ?, ?, 'private', "
        "?, 0)",
        (content_id, account, video_id, f"https://youtu.be/{video_id}",
         when.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )  # fmt: skip
    ctx.conn.commit()


def test_default_slot_is_today_at_the_publish_time(ctx: AppContext) -> None:
    content_id = approved(ctx)

    assert publish.pick_publish_time(ctx, content_id, now=NOW) == TODAY_17


def test_too_late_today_moves_to_tomorrow(ctx: AppContext) -> None:
    content_id = approved(ctx)
    late = TODAY_17 - timedelta(minutes=10)  # under the 15-minute lead

    assert publish.pick_publish_time(ctx, content_id, now=late) == TODAY_17 + timedelta(days=1)


def test_a_day_with_a_youtube_post_is_skipped(ctx: AppContext) -> None:
    content_id, other = approved(ctx), approved(ctx)
    scheduled_post(ctx, other, "OTHER000001", TODAY_17)

    assert publish.pick_publish_time(ctx, content_id, now=NOW) == TODAY_17 + timedelta(days=1)


def test_a_future_planned_slot_is_used_and_a_past_one_ignored(ctx: AppContext) -> None:
    content_id = approved(ctx)
    planned = TODAY_17 + timedelta(days=3)
    ctx.conn.execute("UPDATE contents SET planned_publish_at = ? WHERE id = ?",
                     (planned.strftime("%Y-%m-%dT%H:%M:%SZ"), content_id))  # fmt: skip

    assert publish.pick_publish_time(ctx, content_id, now=NOW) == planned
    ctx.conn.execute("UPDATE contents SET planned_publish_at = '2026-10-28T16:00:00Z'")
    assert publish.pick_publish_time(ctx, content_id, now=NOW) == TODAY_17


def test_chosen_time_must_be_ahead_and_on_a_free_day(ctx: AppContext) -> None:
    content_id, other = approved(ctx), approved(ctx)
    scheduled_post(ctx, other, "OTHER000001", TODAY_17 + timedelta(days=1))
    soon = NOW + timedelta(minutes=5)
    taken = TODAY_17 + timedelta(days=1, hours=2)

    with pytest.raises(FactoryError, match="too soon"):
        publish.pick_publish_time(ctx, content_id, at=soon, now=NOW)
    with pytest.raises(FactoryError, match="already has a YouTube post"):
        publish.pick_publish_time(ctx, content_id, at=taken, now=NOW)


def test_local_time_is_read_in_the_channel_time_zone(ctx: AppContext) -> None:
    assert publish.parse_local_time(ctx, "2026-11-02 17:00") == datetime(2026, 11, 2, 16, 0,
                                                                         tzinfo=UTC)  # fmt: skip
    assert publish.local_time(ctx, TODAY_17) == "2026-10-31 17:00 CET"
    with pytest.raises(FactoryError, match="must look like"):
        publish.parse_local_time(ctx, "tomorrow 5pm")


def test_dry_run_uploads_and_records_nothing(ctx: AppContext) -> None:
    content_id, api = approved(ctx), FakeYouTube()

    post = schedule(ctx, content_id, api)

    assert post.dry_run and post.publish_at == TODAY_17 and post.title == "Zipper in 40 seconds"
    assert api.inserted == [] and status(ctx, content_id) == "approved"
    assert ctx.conn.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


def test_schedule_uploads_private_with_publish_at_and_records_it(ctx: AppContext) -> None:
    content_id, api = approved(ctx), FakeYouTube()

    post = schedule(real(ctx), content_id, api)

    sent = api.inserted[0]["body"]
    assert sent["status"] == {
        "privacyStatus": "private", "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True, "publishAt": "2026-10-31T16:00:00Z",
    }  # fmt: skip
    assert sent["snippet"]["tags"] == ["Engineering", "HowItWorks", "Zipper"]
    assert sent["snippet"]["title"] == "Zipper in 40 seconds"
    row = ctx.conn.execute("SELECT * FROM posts WHERE id = ?", (post.post_id,)).fetchone()
    assert (row["status"], row["privacy"], row["external_id"]) == (
        "scheduled", "private", "VIDEO000001")  # fmt: skip
    assert row["url"] == post.url == "https://youtu.be/VIDEO000001"
    assert row["scheduled_for"] == "2026-10-31T16:00:00Z" and row["published_at"] is None
    assert (row["ai_disclosure"], row["dry_run"]) == (1, 0)
    assert status(ctx, content_id) == "scheduled"
    assert usage_today(ctx.conn, ctx.channel)["youtube_data"][0] == 2  # channel check + upload


@pytest.mark.parametrize("decision", ["none", "redo"])
def test_no_upload_without_a_standing_gate_2_approval(ctx: AppContext, decision: str) -> None:
    api = FakeYouTube()
    if decision == "none":
        content_id = item(ctx)
        (metadata.asset_dir(ctx, content_id) / "render_youtube.mp4").write_bytes(b"x")
    else:
        content_id = approved(ctx, decision=decision)

    with pytest.raises(FactoryError, match="no Gate 2 approval"):
        schedule(real(ctx), content_id, api)
    assert api.inserted == []


def test_a_second_youtube_post_is_refused(ctx: AppContext) -> None:
    content_id = approved(ctx)
    schedule(real(ctx), content_id, FakeYouTube())
    api = FakeYouTube()

    with pytest.raises(FactoryError, match="already has a YouTube post"):
        schedule(real(ctx), content_id, api)
    assert api.inserted == []


def test_check_records_public_videos_and_reports_the_rest(ctx: AppContext) -> None:
    live, waiting, gone = approved(ctx), approved(ctx), approved(ctx)
    for content_id, video, days in ((live, "LIVE0000001", 0), (waiting, "WAIT0000001", 1),
                                    (gone, "GONE0000001", 2)):  # fmt: skip
        set_status(ctx.conn, content_id, "scheduled")
        scheduled_post(ctx, content_id, video, TODAY_17 + timedelta(days=days))
    api = FakeYouTube(privacy={"LIVE0000001": "public", "WAIT0000001": "private"})

    checked = publish.check_scheduled(
        ctx, now=TODAY_17 + timedelta(hours=1), load=lambda p: object(), builder=builder_for(api)
    )

    assert [(c.content_id, c.state) for c in checked] == [
        (live, "published"), (waiting, "waiting"), (gone, "missing")]  # fmt: skip
    row = ctx.conn.execute("SELECT * FROM posts WHERE content_id = ?", (live,)).fetchone()
    assert (row["status"], row["privacy"], row["published_at"]) == (
        "published", "public", "2026-10-31T16:00:00Z")  # fmt: skip
    assert [status(ctx, c) for c in (live, waiting, gone)] == ["published", "scheduled",
                                                               "scheduled"]  # fmt: skip
    assert len(api.status_lookups) == 1  # one videos.list call for all of them


def test_check_without_scheduled_posts_needs_no_sign_in(ctx: AppContext) -> None:
    def no_load(path: Path) -> Any:
        raise AssertionError("must not sign in")

    assert publish.check_scheduled(ctx, load=no_load) == []


def test_tiktok_can_be_recorded_while_youtube_is_scheduled(ctx: AppContext) -> None:
    content_id = approved(ctx)
    schedule(real(ctx), content_id, FakeYouTube())

    publish.record_manual_post(ctx, content_id, "tiktok", TT_URL, ai_label_on=True)

    assert status(ctx, content_id) == "published"


def test_a_server_error_during_upload_is_retried() -> None:
    error = HttpError(Response({"status": 503}), json.dumps({"error": {"errors": [
        {"reason": "backendError"}]}}).encode())  # fmt: skip
    api = FakeYouTube(upload=[error, {"id": "VIDEO000002"}])
    counted: list[int] = []

    video_id = youtube.upload(api, __file__, title="t", description="d", category_id=28,
                              privacy="private", before_call=counted.append,
                              retry_wait_s=0)  # fmt: skip

    assert video_id == "VIDEO000002" and counted == [1]  # one upload counted, not two


def test_a_server_error_that_never_ends_gives_up_after_4_attempts() -> None:
    error = HttpError(Response({"status": 500}), b"{}")
    api = FakeYouTube(upload=[error])

    with pytest.raises(RetryableError, match="HTTP 500"):
        youtube.upload(api, __file__, title="t", description="d", category_id=28,
                       privacy="private", before_call=lambda u: None, retry_wait_s=0)  # fmt: skip


def test_publish_at_needs_a_private_video() -> None:
    with pytest.raises(ValueError, match="only works with privacy 'private'"):
        youtube.upload(FakeYouTube(), __file__, title="t", description="d", category_id=28,
                       privacy="unlisted", publish_at=NOW, before_call=lambda u: None)  # fmt: skip


def test_scheduled_test_upload_is_private_30_days_ahead(ctx: AppContext) -> None:
    sting = ctx.root / "assets_static" / publish.TEST_VIDEO
    sting.parent.mkdir(parents=True)
    sting.write_bytes(b"fake mp4")
    api = FakeYouTube()

    publish.test_upload(real(ctx), scheduled=True, now=NOW, load=lambda p: object(),
                        builder=builder_for(api))  # fmt: skip

    sent = api.inserted[0]["body"]["status"]
    assert sent["privacyStatus"] == "private" and sent["publishAt"] == "2026-11-30T12:00:00Z"
    assert sent["containsSyntheticMedia"] is True


def test_cli_publish_dry_run_says_when_it_would_go_public(config_dir: Path) -> None:
    context = make_ctx(config_dir.parent)
    content_id = approved(context)
    context.conn.close()

    result = CliRunner().invoke(
        cli.app, ["publish", "--id", str(content_id), "--at", "2099-01-05 17:00",
                  "--config-dir", str(config_dir)],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] would upload render_youtube.mp4" in result.output
    assert "public at 2099-01-05 17:00 CET" in result.output
    assert "DRY_RUN=false uv run factory publish --id" in result.output
