"""Publishing approved items.

Manual (ROADMAP 1.12, D-072): `upload_kit()` prints what to paste into YouTube Studio and TikTok;
`record_manual_post()` stores the live post in `posts` and moves the item to `published`. Nothing
there calls a platform, so it works with DRY_RUN on.

Scheduled YouTube (ROADMAP 2.3, D-115): `schedule_youtube()` uploads the render as private with a
`publishAt` time, so YouTube itself makes it public at 17:00 (the PC can be off), and stores the
post as `scheduled`. `check_scheduled()` asks YouTube which of those are public now and records
them as `published`. TikTok stays manual until task 2.6.

`connect_youtube()` signs the channel in (ROADMAP 2.2, D-102) and `test_upload()` proves the
upload path with the brand sting, always unlisted.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from factory.budgets import check_and_record
from factory.config import TikTokPlatform, YouTubePlatform
from factory.context import AppContext
from factory.db import set_status, upsert_platform_account
from factory.errors import FactoryError
from factory.integrations import youtube, youtube_auth
from factory.stages import metadata
from factory.stages.generate import windows_path
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.publish")

PLATFORMS = ("youtube", "tiktok")
# Only Gate 2-approved items may go out; `scheduled` and `published` items can still get their
# second platform.
PUBLISHABLE = ("approved", "scheduled", "published")
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
TIKTOK_PATH = re.compile(r"^/(@[\w.]+)/video/(\d+)/?$")


@dataclass(frozen=True)
class RecordedPost:
    post_id: int
    url: str
    created: bool  # False = the same post was already recorded (re-running is harmless)
    content_status: str


def parse_post_url(platform: str, url: str, handle: str) -> tuple[str, str]:
    """The video id and a clean link from what the owner pasted; refuses anything else."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if platform == "youtube":
        video_id = None
        if host == "youtu.be":
            video_id = parsed.path.strip("/")
        elif host in YOUTUBE_HOSTS and parsed.path.startswith("/shorts/"):
            video_id = parsed.path.removeprefix("/shorts/").strip("/")
        elif host in YOUTUBE_HOSTS and parsed.path == "/watch":
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if not video_id or not YOUTUBE_ID.match(video_id):
            raise FactoryError(
                f"not a YouTube video link: {url} (expected youtube.com/shorts/<id> or "
                "youtube.com/watch?v=<id>)"
            )
        return video_id, f"https://www.youtube.com/shorts/{video_id}"
    match = TIKTOK_PATH.match(parsed.path) if host in TIKTOK_HOSTS else None
    if match is None:
        raise FactoryError(
            f"not a full TikTok video link: {url} (expected tiktok.com/{handle}/video/<number>; "
            "short vm./vt. links can't be checked: open the video on tiktok.com and copy the "
            "address bar)"
        )
    if match.group(1).lower() != handle.lower():
        raise FactoryError(f"that video belongs to {match.group(1)}, not {handle}")
    return match.group(2), f"https://www.tiktok.com/{handle}/video/{match.group(2)}"


def record_manual_post(
    ctx: AppContext, content_id: int, platform: str, url: str, *, ai_label_on: bool
) -> RecordedPost:
    """Store a post the owner made by hand. The first platform moves the item to `published`."""
    item = _publishable_item(ctx, content_id)
    account = _account(ctx, platform)
    if not ai_label_on:
        raise FactoryError(
            f"switch on the AI label on {platform} first, then confirm with --ai-label-on"
        )
    external_id, clean_url = parse_post_url(platform, url, account.handle)
    existing = ctx.conn.execute(
        "SELECT id, external_id FROM posts WHERE content_id = ? AND platform = ?",
        (content_id, platform),
    ).fetchone()
    if existing is not None:
        if existing["external_id"] != external_id:
            raise FactoryError(
                f"#{content_id} already has a different {platform} post recorded "
                f"(video {existing['external_id']})"
            )
        return RecordedPost(existing["id"], clean_url, False, item["status"])
    other = ctx.conn.execute(
        "SELECT content_id FROM posts WHERE platform = ? AND external_id = ?",
        (platform, external_id),
    ).fetchone()
    if other is not None:
        raise FactoryError(f"that {platform} video is already recorded for #{other[0]}")
    data = metadata.load(ctx, content_id)
    if platform == "youtube":
        title, caption = data["youtube"]["title"], data["youtube"]["description"]
    else:
        title, caption = None, data["tiktok"]["caption"]
    account_id = upsert_platform_account(
        ctx.conn, item["channel_id"], platform, account.handle, account.publish_mode
    )
    with ctx.conn:
        post_id = ctx.conn.execute(
            """
            INSERT INTO posts (content_id, platform_account_id, platform, status, external_id,
                               url, title, caption, hashtags, ai_disclosure, privacy,
                               published_at, dry_run)
            VALUES (?, ?, ?, 'published', ?, ?, ?, ?, ?, 1, 'public',
                    strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 0)
            """,
            (content_id, account_id, platform, external_id, clean_url, title, caption,
             " ".join(data[platform]["hashtags"])),
        ).lastrowid  # fmt: skip
        if item["status"] in ("approved", "scheduled"):
            set_status(ctx.conn, content_id, "published")
    log.info("#%s recorded as posted on %s: %s", content_id, platform, clean_url,
             extra={"content_id": content_id, "event": "post_recorded"})  # fmt: skip
    return RecordedPost(post_id, clean_url, True, "published")


def upload_kit(ctx: AppContext, content_id: int) -> str:
    """Everything to copy into YouTube Studio and TikTok for one approved item, as plain text."""
    _publishable_item(ctx, content_id)
    data = metadata.load(ctx, content_id)
    folder = asset_dir(ctx, content_id)
    recorded = {
        row["platform"]: row["url"]
        for row in ctx.conn.execute(
            "SELECT platform, url FROM posts WHERE content_id = ?", (content_id,)
        )
    }
    yt, tt = data["youtube"], data["tiktok"]
    channel = ctx.channel
    account_handle = channel.platforms.tiktok.handle
    lines = [f"UPLOAD KIT for #{content_id} ({data['object']}, {data['format']})", ""]
    lines += ["FILES (open the folder from Windows):", f"  {windows_path(folder)}", ""]
    if "youtube" in recorded:
        lines += [f"YOUTUBE: already recorded: {recorded['youtube']}", ""]
    else:
        lines += [
            f"YOUTUBE (studio.youtube.com, signed in as {channel.display_name}):",
            f"  1. Create > Upload videos > {yt['render']}",
            "  2. Title:",
            f"     {yt['title']}",
            "  3. Description (copy everything between the lines):",
            "     " + "-" * 60,
            *(f"     {line}" for line in yt["description"].splitlines()),
            "     " + "-" * 60,
            "  4. Audience: No, it's not made for kids",
            "  5. Altered or synthetic content: Yes",
            "  6. Show more > Category: Science & Technology",
            "  7. Visibility: Public, then Publish (music is already in this render)",
            "  8. Copy the video link and record it:",
            f"     uv run factory publish --manual --id {content_id} --platform youtube "
            '--url "<link>" --ai-label-on',
            "",
        ]
    if "tiktok" in recorded:
        lines += [f"TIKTOK: already recorded: {recorded['tiktok']}"]
    else:
        lines += [
            f"TIKTOK (tiktok.com/upload or the app, signed in as {account_handle}):",
            f"  1. Upload {tt['render']}",
            "  2. Optional: add a TikTok sound at low volume (this render has no music)",
            "  3. Caption:",
            f"     {tt['caption']}",
            "  4. More options > AI-generated content: ON",
            "  5. Post, open the video on tiktok.com, copy the address bar and record it:",
            f"     uv run factory publish --manual --id {content_id} --platform tiktok "
            '--url "<link>" --ai-label-on',
        ]
    return "\n".join(lines)


def _publishable_item(ctx: AppContext, content_id: int) -> sqlite3.Row:
    row = ctx.conn.execute(
        """
        SELECT c.* FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE c.id = ? AND ch.slug = ?
        """,
        (content_id, ctx.channel.slug),
    ).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} does not exist for {ctx.channel.slug}")
    if row["status"] not in PUBLISHABLE:
        raise FactoryError(
            f"content #{content_id} is '{row['status']}'; only Gate 2-approved items can be "
            "published"
        )
    return row


def _account(ctx: AppContext, platform: str) -> YouTubePlatform | TikTokPlatform:
    if platform not in PLATFORMS:
        raise FactoryError(f"--platform must be youtube or tiktok, not '{platform}'")
    account = getattr(ctx.channel.platforms, platform)
    if not account.enabled:
        raise FactoryError(f"{platform} is disabled in the channel YAML")
    return account


# --- scheduled YouTube publishing (ROADMAP 2.3, D-115) ------------------------------------------

SCHEDULE_PRIVACY = "private"  # YouTube only accepts publishAt on a private video
MIN_LEAD = timedelta(minutes=15)  # time for YouTube to process the upload before it goes public
LOCAL_FORMAT = "%Y-%m-%d %H:%M"


@dataclass(frozen=True)
class ScheduledPost:
    content_id: int
    publish_at: datetime  # UTC
    title: str
    render: Path
    dry_run: bool  # True = nothing was uploaded or recorded
    post_id: int | None = None
    url: str | None = None


@dataclass(frozen=True)
class CheckedPost:
    content_id: int
    url: str
    scheduled_for: str
    state: str  # 'published' (just went public) | 'waiting' | 'missing' (deleted on YouTube)


def local_time(ctx: AppContext, moment: datetime) -> str:
    """A UTC moment as the channel's local clock time, e.g. '2026-11-01 17:00 CET'."""
    return moment.astimezone(ZoneInfo(ctx.channel.timezone)).strftime(f"{LOCAL_FORMAT} %Z")


def parse_local_time(ctx: AppContext, text: str) -> datetime:
    """'2026-11-02 17:00' in the channel's time zone, as UTC."""
    try:
        naive = datetime.strptime(text.strip(), LOCAL_FORMAT)
    except ValueError:
        raise FactoryError(
            f"--at must look like 2026-11-02 17:00 ({ctx.channel.timezone} time), not '{text}'"
        ) from None
    return naive.replace(tzinfo=ZoneInfo(ctx.channel.timezone)).astimezone(UTC)


def pick_publish_time(
    ctx: AppContext, content_id: int, *, at: datetime | None = None, now: datetime | None = None
) -> datetime:
    """When the video goes public (UTC). One YouTube post per local day (SECURITY_COMPLIANCE §2):
    `at` if given, else the item's planned slot if it is still ahead and its day is free, else the
    next free day at `cadence.publish_time`."""
    now = now or datetime.now(UTC)
    zone = ZoneInfo(ctx.channel.timezone)
    earliest = now + MIN_LEAD
    taken = _youtube_days(ctx, zone)
    if at is not None:
        if at < earliest:
            raise FactoryError(
                f"--at {local_time(ctx, at)} is too soon: choose a time at least "
                f"{int(MIN_LEAD.total_seconds() // 60)} minutes ahead"
            )
        if at.astimezone(zone).date() in taken:
            raise FactoryError(
                f"{at.astimezone(zone).date()} already has a YouTube post: one per day"
            )
        return at
    planned = ctx.conn.execute(
        "SELECT planned_publish_at FROM contents WHERE id = ?", (content_id,)
    ).fetchone()[0]
    if planned:
        slot = datetime.fromisoformat(planned.replace("Z", "+00:00"))
        if slot >= earliest and slot.astimezone(zone).date() not in taken:
            return slot
    hour, minute = (int(part) for part in ctx.channel.cadence.publish_time.split(":"))
    day = now.astimezone(zone).date()
    for _ in range(366):
        slot = datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC)
        if slot >= earliest and day not in taken:
            return slot
        day += timedelta(days=1)
    raise FactoryError("no free publishing day in the next year")


def schedule_youtube(
    ctx: AppContext,
    content_id: int,
    *,
    at: datetime | None = None,
    now: datetime | None = None,
    load: Callable[[Path], Any] = youtube_auth.load_credentials,
    builder: Callable[..., Any] = youtube.build,
) -> ScheduledPost:
    """Upload a Gate 2-approved item as private with a publishAt time and store it as
    `scheduled`. With DRY_RUN on, only works out what would happen."""
    item = _publishable_item(ctx, content_id)
    account = _account(ctx, "youtube")
    if account.publish_mode != "api_scheduled":
        raise FactoryError(
            f"YouTube publish_mode is '{account.publish_mode}' in the channel YAML, not "
            "'api_scheduled': use `factory publish --manual`"
        )
    _require_gate_2(ctx, content_id)
    existing = ctx.conn.execute(
        "SELECT status, url FROM posts WHERE content_id = ? AND platform = 'youtube'",
        (content_id,),
    ).fetchone()
    if existing is not None:
        raise FactoryError(
            f"#{content_id} already has a YouTube post ({existing['status']}): {existing['url']}"
        )
    data = metadata.load(ctx, content_id)
    yt = data["youtube"]
    render = asset_dir(ctx, content_id) / yt["render"]
    if not render.is_file():
        raise FactoryError(f"{render} is missing: run `uv run factory assemble`")
    publish_at = pick_publish_time(ctx, content_id, at=at, now=now)
    if ctx.env.dry_run:
        return ScheduledPost(content_id, publish_at, yt["title"], render, dry_run=True)

    secrets = ctx.root / ctx.settings.paths.secrets_dir
    credentials = load(youtube_auth.token_path(secrets, ctx.channel.slug))
    api = youtube.service(credentials, builder=builder)
    _signed_in_channel(ctx, credentials, builder, api=api)
    video_id = youtube.upload(
        api, render,
        title=yt["title"],
        description=yt["description"],
        category_id=account.category_id,
        privacy=SCHEDULE_PRIVACY,
        publish_at=publish_at,
        tags=[tag.lstrip("#") for tag in yt["hashtags"]],
        before_call=lambda units: check_and_record(ctx.conn, ctx.channel, "youtube_data", units),
    )  # fmt: skip
    # Logged before the database write, so the video can be found if that write ever fails.
    log.info("#%s uploaded as %s, public at %s", content_id, video_id, publish_at.isoformat(),
             extra={"content_id": content_id, "event": "youtube_uploaded"})  # fmt: skip
    url = f"https://youtu.be/{video_id}"
    account_id = upsert_platform_account(
        ctx.conn, item["channel_id"], "youtube", account.handle, account.publish_mode
    )
    with ctx.conn:
        post_id = ctx.conn.execute(
            """
            INSERT INTO posts (content_id, platform_account_id, platform, status, external_id,
                               url, title, caption, hashtags, ai_disclosure, privacy,
                               scheduled_for, dry_run)
            VALUES (?, ?, 'youtube', 'scheduled', ?, ?, ?, ?, ?, 1, ?, ?, 0)
            """,
            (content_id, account_id, video_id, url, yt["title"], yt["description"],
             " ".join(yt["hashtags"]), SCHEDULE_PRIVACY,
             publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ).lastrowid  # fmt: skip
        if item["status"] == "approved":
            set_status(ctx.conn, content_id, "scheduled")
    log.info("#%s scheduled on YouTube for %s: %s", content_id, publish_at.isoformat(), url,
             extra={"content_id": content_id, "event": "youtube_scheduled"})  # fmt: skip
    return ScheduledPost(content_id, publish_at, yt["title"], render, False, post_id, url)


def check_scheduled(
    ctx: AppContext,
    *,
    now: datetime | None = None,
    load: Callable[[Path], Any] = youtube_auth.load_credentials,
    builder: Callable[..., Any] = youtube.build,
) -> list[CheckedPost]:
    """Ask YouTube which scheduled posts are public now; record those as `published`. Read-only
    on YouTube, so it also runs with DRY_RUN on."""
    rows = ctx.conn.execute(
        """
        SELECT p.id, p.content_id, p.external_id, p.url, p.scheduled_for, c.status
        FROM posts p JOIN contents c ON c.id = p.content_id
        JOIN channels ch ON ch.id = c.channel_id
        WHERE p.platform = 'youtube' AND p.status = 'scheduled' AND ch.slug = ?
        ORDER BY p.scheduled_for
        """,
        (ctx.channel.slug,),
    ).fetchall()
    if not rows:
        return []
    now = now or datetime.now(UTC)
    secrets = ctx.root / ctx.settings.paths.secrets_dir
    api = youtube.service(load(youtube_auth.token_path(secrets, ctx.channel.slug)), builder=builder)
    privacy = youtube.video_privacy(
        api, [row["external_id"] for row in rows],
        before_call=lambda units: check_and_record(ctx.conn, ctx.channel, "youtube_data", units),
    )  # fmt: skip
    checked = []
    for row in rows:
        found = privacy.get(row["external_id"])
        if found is None:
            state = "missing"
        elif found == "public":
            state = "published"
            # It went public at its scheduled time, unless someone published it earlier by hand.
            went_public = min(row["scheduled_for"], now.strftime("%Y-%m-%dT%H:%M:%SZ"))
            with ctx.conn:
                ctx.conn.execute(
                    "UPDATE posts SET status = 'published', privacy = 'public', published_at = ? "
                    "WHERE id = ?",
                    (went_public, row["id"]),
                )
                if row["status"] == "scheduled":
                    set_status(ctx.conn, row["content_id"], "published")
            log.info(
                "#%s is public on YouTube: %s", row["content_id"], row["url"],
                extra={"content_id": row["content_id"], "event": "youtube_published"},
            )  # fmt: skip
        else:
            state = "waiting"
        checked.append(CheckedPost(row["content_id"], row["url"], row["scheduled_for"], state))
    return checked


def _youtube_days(ctx: AppContext, zone: ZoneInfo) -> set[date]:
    """Local days that already have a YouTube post (scheduled or live) on this channel."""
    rows = ctx.conn.execute(
        """
        SELECT coalesce(p.scheduled_for, p.published_at) FROM posts p
        JOIN contents c ON c.id = p.content_id JOIN channels ch ON ch.id = c.channel_id
        WHERE p.platform = 'youtube' AND p.status IN ('scheduled', 'published') AND ch.slug = ?
        """,
        (ctx.channel.slug,),
    )
    return {
        datetime.fromisoformat(moment.replace("Z", "+00:00")).astimezone(zone).date()
        for (moment,) in rows
        if moment
    }


def _require_gate_2(ctx: AppContext, content_id: int) -> None:
    """The newest Gate 2 decision must be an approval (CLAUDE.md rule 5)."""
    row = ctx.conn.execute(
        "SELECT decision FROM reviews WHERE content_id = ? AND gate = 'final' "
        "ORDER BY decided_at DESC, id DESC LIMIT 1",
        (content_id,),
    ).fetchone()
    if row is None or row["decision"] != "approve":
        raise FactoryError(
            f"#{content_id} has no Gate 2 approval: `uv run factory review --gate final`"
        )


# --- YouTube sign-in and test upload (ROADMAP 2.2, D-102) ---------------------------------------

# A test upload can never be public (CLAUDE.md rule 5): the privacy is fixed here, not passed in.
TEST_PRIVACY = "unlisted"
TEST_TITLE = "Coil & Cog API test - delete me"
TEST_VIDEO = "logo/sting_1080x1920.mp4"
# A scheduled test is private with a publishAt far enough ahead that it is deleted long before.
TEST_SCHEDULE_AHEAD = timedelta(days=30)


def connect_youtube(
    ctx: AppContext,
    *,
    sign_in: Callable[[Path], Any] = youtube_auth.sign_in,
    builder: Callable[..., Any] = youtube.build,
) -> youtube.Channel:
    """Sign in, check that the account is the channel's own, then save the token. A token for
    another channel (a personal account picked by mistake) is never saved."""
    secrets = ctx.root / ctx.settings.paths.secrets_dir
    credentials = sign_in(youtube_auth.client_path(secrets, ctx.channel.slug))
    channel = _signed_in_channel(ctx, credentials, builder)
    youtube_auth.save_token(credentials, youtube_auth.token_path(secrets, ctx.channel.slug))
    log.info("YouTube signed in as %s", channel.handle, extra={"event": "youtube_signed_in"})
    return channel


def test_upload(
    ctx: AppContext,
    *,
    scheduled: bool = False,
    now: datetime | None = None,
    load: Callable[[Path], Any] = youtube_auth.load_credentials,
    builder: Callable[..., Any] = youtube.build,
) -> str:
    """Upload the 1 s brand sting as an unlisted, AI-labelled test video; returns its link.
    `scheduled` makes it private with a publishAt 30 days ahead instead, to prove scheduling
    (D-115). Needs DRY_RUN=false: it really changes the channel (the owner deletes the video
    after)."""
    if ctx.env.dry_run:
        raise FactoryError(
            "DRY_RUN is on, so nothing is uploaded. For this one test run: "
            "DRY_RUN=false uv run factory auth test-upload"
        )
    secrets = ctx.root / ctx.settings.paths.secrets_dir
    credentials = load(youtube_auth.token_path(secrets, ctx.channel.slug))
    api = youtube.service(credentials, builder=builder)
    _signed_in_channel(ctx, credentials, builder, api=api)
    video = ctx.root / ctx.settings.paths.assets_static_dir / TEST_VIDEO
    if not video.is_file():
        raise FactoryError(f"{video} is missing: run `uv run factory brand build`")
    privacy, publish_at = TEST_PRIVACY, None
    if scheduled:
        privacy, publish_at = SCHEDULE_PRIVACY, (now or datetime.now(UTC)) + TEST_SCHEDULE_AHEAD
    video_id = youtube.upload(
        api, video,
        title=TEST_TITLE,
        description="Automatic upload test of the Coil & Cog factory. Made with AI.",
        category_id=ctx.channel.platforms.youtube.category_id,
        privacy=privacy,
        publish_at=publish_at,
        before_call=lambda units: check_and_record(ctx.conn, ctx.channel, "youtube_data", units),
    )  # fmt: skip
    log.info("test upload %s (%s)", video_id, privacy, extra={"event": "youtube_test_upload"})
    return f"https://youtu.be/{video_id}"


def _signed_in_channel(
    ctx: AppContext, credentials: Any, builder: Callable[..., Any], *, api: Any = None
) -> youtube.Channel:
    api = api or youtube.service(credentials, builder=builder)
    channel = youtube.my_channel(
        api, before_call=lambda u: check_and_record(ctx.conn, ctx.channel, "youtube_data", u)
    )
    expected = ctx.channel.platforms.youtube.handle.lower()
    if channel.handle != expected:
        raise FactoryError(
            f"signed in as {channel.handle or channel.title!r}, not {expected}: the token was "
            "not saved. Run `uv run factory auth youtube` again and choose the Coil & Cog channel"
        )
    return channel
