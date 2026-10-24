"""Manual publishing (ROADMAP 1.12, D-072): the owner uploads by hand, the factory records it.

`upload_kit()` prints what to paste into YouTube Studio and TikTok. `record_manual_post()` stores
the live post in `posts` and moves the item to `published`. Nothing here calls a platform, so it
works with DRY_RUN on; API publishing comes in tasks 2.3 and 2.6.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from factory.config import TikTokPlatform, YouTubePlatform
from factory.context import AppContext
from factory.db import set_status, upsert_platform_account
from factory.errors import FactoryError
from factory.stages import metadata
from factory.stages.generate import windows_path
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.publish")

PLATFORMS = ("youtube", "tiktok")
# Only Gate 2-approved items may go out; `published` items can still get their second platform.
PUBLISHABLE = ("approved", "published")
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
        if item["status"] == "approved":
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
