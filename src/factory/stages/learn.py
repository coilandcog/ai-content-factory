"""Learn stage (ROADMAP 2.5): numbers the owner copies from YouTube Studio and TikTok become
`metrics` rows (D-101), and `factory learn` pulls the YouTube ones from the Analytics API (D-117).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from factory.budgets import check_and_record
from factory.context import AppContext
from factory.errors import FactoryError
from factory.integrations import youtube, youtube_analytics, youtube_auth

log = logging.getLogger("factory.learn")

CHECKPOINTS = ("24h", "72h", "7d", "28d")
PLATFORMS = ("youtube", "tiktok")
WINDOWS = {"24h": timedelta(hours=24), "72h": timedelta(hours=72),
           "7d": timedelta(days=7), "28d": timedelta(days=28)}  # fmt: skip
ANALYTICS_ZONE = ZoneInfo("America/Los_Angeles")  # YouTube Analytics days are Pacific days
LAG_DAYS = 2  # Analytics numbers can arrive up to 2 days late: pull a checkpoint once it's final


@dataclass(frozen=True)
class Numbers:
    """What the platforms show. None = not entered (kept from an earlier entry)."""

    views: int | None = None
    stayed_to_watch_pct: float | None = None
    avg_view_pct: float | None = None
    avg_view_duration_s: float | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    subscribers_gained: int | None = None
    watch_time_min: float | None = None

    def problems(self) -> list[str]:
        out = []
        for name, value in asdict(self).items():
            if value is not None and value < 0 and name != "subscribers_gained":
                out.append(f"{name} can't be negative ({value})")
        if self.stayed_to_watch_pct is not None and self.stayed_to_watch_pct > 100:
            out.append("stayed-to-watch is a share of viewers: at most 100")
        if self.avg_view_pct is not None and self.avg_view_pct > 1000:
            out.append("average view percentage above 1000 is a typo")
        return out


def add_manual(
    ctx: AppContext, content_id: int, platform: str, checkpoint: str, numbers: Numbers
) -> sqlite3.Row:
    """Store (or correct) one checkpoint's numbers for a recorded post; returns the row.

    Entering the same checkpoint again only replaces the values given this time."""
    if platform not in PLATFORMS:
        raise FactoryError(f"--platform must be one of {', '.join(PLATFORMS)}")
    if checkpoint not in CHECKPOINTS:
        raise FactoryError(f"--checkpoint must be one of {', '.join(CHECKPOINTS)}")
    if problems := numbers.problems():
        raise FactoryError("; ".join(problems))
    values = {k: v for k, v in asdict(numbers).items() if v is not None}
    if not values:
        raise FactoryError("give at least one number, e.g. --views 1234")
    post = ctx.conn.execute(
        "SELECT id FROM posts WHERE content_id = ? AND platform = ?", (content_id, platform)
    ).fetchone()
    if post is None:
        raise FactoryError(
            f"#{content_id} has no {platform} post recorded: run `factory publish --manual "
            f"--id {content_id} --platform {platform} --url <link> --ai-label-on` first"
        )
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    updates = ", ".join(f"{k} = excluded.{k}" for k in values)
    with ctx.conn:
        ctx.conn.execute(
            f"""
            INSERT INTO metrics (post_id, checkpoint, source, {columns})
            VALUES (?, ?, 'manual', {marks})
            ON CONFLICT (post_id, checkpoint) DO UPDATE SET {updates}, source = 'manual',
                collected_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            """,
            (post[0], checkpoint, *values.values()),
        )
    return ctx.conn.execute(
        "SELECT * FROM metrics WHERE post_id = ? AND checkpoint = ?", (post[0], checkpoint)
    ).fetchone()


def show(ctx: AppContext, content_id: int) -> str:
    """Every stored checkpoint of an item, per platform, as a plain-text table."""
    row = ctx.conn.execute(
        "SELECT object_name FROM contents WHERE id = ?", (content_id,)
    ).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} does not exist")
    rows = ctx.conn.execute(
        """
        SELECT p.platform, m.* FROM metrics m JOIN posts p ON p.id = m.post_id
        WHERE p.content_id = ?
        ORDER BY p.platform, CASE m.checkpoint WHEN '24h' THEN 1 WHEN '72h' THEN 2
                                               WHEN '7d' THEN 3 ELSE 4 END
        """,
        (content_id,),
    ).fetchall()
    lines = [f"#{content_id} {row['object_name']}"]
    if not rows:
        lines.append("  no numbers yet: `factory metrics add` them from YouTube Studio / TikTok")
    for m in rows:
        parts = [
            f"{m['views']} views" if m["views"] is not None else None,
            _pct("stayed", m["stayed_to_watch_pct"]),
            _pct("avg viewed", m["avg_view_pct"]),
            _seconds(m["avg_view_duration_s"]),
            _count("likes", m["likes"]), _count("comments", m["comments"]),
            _count("shares", m["shares"]), _count("subs", m["subscribers_gained"]),
        ]  # fmt: skip
        shown = " | ".join(p for p in parts if p)
        lines.append(f"  {m['platform']:<8}{m['checkpoint']:<4} {shown}  ({m['source']})")
    return "\n".join(lines)


def _pct(label: str, value: float | None) -> str | None:
    return None if value is None else f"{label} {value:g}%"


def _seconds(value: float | None) -> str | None:
    return None if value is None else f"avg {value:.0f} s"


def _count(label: str, value: int | None) -> str | None:
    return None if value is None else f"{value} {label}"


# --- YouTube Analytics pull (D-117) -------------------------------------------------------


@dataclass(frozen=True)
class Pulled:
    """One checkpoint of one post: `saved` (numbers stored), `no_data` (final but YouTube has
    no row yet: tried again next run) or `waiting` (not final until `due`)."""

    content_id: int
    checkpoint: str
    state: str
    due: date
    views: int | None = None


def window(published: datetime, checkpoint: str) -> tuple[date, date]:
    """The Pacific days a checkpoint covers: the publish day to the day the window ends."""
    start = published.astimezone(ANALYTICS_ZONE)
    end = (published + WINDOWS[checkpoint]).astimezone(ANALYTICS_ZONE)
    return start.date(), end.date()


def due_day(published: datetime, checkpoint: str) -> date:
    """The first Pacific day on which a checkpoint's numbers count as final."""
    return window(published, checkpoint)[1] + timedelta(days=1 + LAG_DAYS)


def pull(
    ctx: AppContext,
    content_id: int | None = None,
    *,
    now: datetime | None = None,
    load: Callable[[Path], Any] = youtube_auth.load_credentials,
    builder: Callable[..., Any] = youtube.build,
) -> list[Pulled]:
    """Store every final checkpoint of the channel's real, published YouTube posts (or one
    item's) that has no API numbers yet. Read-only on YouTube, so it also runs in DRY_RUN.

    API numbers replace typed ones for the same checkpoint (they are exact), but a typed
    stayed-to-watch share is kept: the API doesn't have it."""
    rows = ctx.conn.execute(
        """
        SELECT p.id, p.content_id, p.external_id, p.url, p.published_at,
               (SELECT group_concat(m.checkpoint) FROM metrics m
                WHERE m.post_id = p.id AND m.source = 'api') AS pulled
        FROM posts p JOIN contents c ON c.id = p.content_id
        JOIN channels ch ON ch.id = c.channel_id
        WHERE p.platform = 'youtube' AND p.status = 'published' AND p.dry_run = 0
              AND p.external_id IS NOT NULL AND ch.slug = ? AND (? IS NULL OR p.content_id = ?)
        ORDER BY p.content_id
        """,
        (ctx.channel.slug, content_id, content_id),
    ).fetchall()
    if content_id is not None and not rows:
        raise FactoryError(f"#{content_id} has no published YouTube post to pull numbers for")
    todo = [r for r in rows if set(CHECKPOINTS) - set((r["pulled"] or "").split(","))]
    if not todo:
        return []
    now = now or datetime.now(UTC)
    today = now.astimezone(ANALYTICS_ZONE).date()
    secrets = ctx.root / ctx.settings.paths.secrets_dir
    credentials = load(youtube_auth.token_path(secrets, ctx.channel.slug))
    # The real moment each video went public: a manual post's published_at is when it was recorded.
    went_public = youtube.published_times(
        youtube.service(credentials, builder=builder), [r["external_id"] for r in todo],
        before_call=lambda units: check_and_record(ctx.conn, ctx.channel, "youtube_data", units),
    )  # fmt: skip
    analytics = youtube_analytics.service(credentials, builder=builder)
    results = []
    for row in todo:
        published = went_public.get(row["external_id"])
        if published is None:
            log.warning("#%s: %s not found on YouTube (deleted in Studio?)", row["content_id"],
                        row["url"], extra={"content_id": row["content_id"],
                                           "event": "learn_missing"})  # fmt: skip
            continue
        stamp = published.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if stamp != row["published_at"]:
            with ctx.conn:
                ctx.conn.execute(
                    "UPDATE posts SET published_at = ? WHERE id = ?", (stamp, row["id"])
                )
        done = set((row["pulled"] or "").split(","))
        for checkpoint in CHECKPOINTS:
            if checkpoint in done:
                continue
            due = due_day(published, checkpoint)
            if today < due:
                results.append(Pulled(row["content_id"], checkpoint, "waiting", due))
                continue
            start, end = window(published, checkpoint)
            numbers = youtube_analytics.video_totals(
                analytics, row["external_id"], start, end,
                before_call=lambda: check_and_record(ctx.conn, ctx.channel, "youtube_analytics"),
            )  # fmt: skip
            if numbers is None:
                results.append(Pulled(row["content_id"], checkpoint, "no_data", due))
                continue
            _store_api(ctx, row["id"], checkpoint, numbers)
            results.append(Pulled(row["content_id"], checkpoint, "saved", due,
                                  int(numbers.get("views", 0))))  # fmt: skip
    return results


def _store_api(ctx: AppContext, post_id: int, checkpoint: str, numbers: dict[str, float]) -> None:
    ints = {"views", "likes", "comments", "shares", "subscribers_gained"}
    values = {k: (int(v) if k in ints else float(v)) for k, v in numbers.items()}
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    updates = ", ".join(f"{k} = excluded.{k}" for k in values)
    with ctx.conn:
        ctx.conn.execute(
            f"""
            INSERT INTO metrics (post_id, checkpoint, source, {columns})
            VALUES (?, ?, 'api', {marks})
            ON CONFLICT (post_id, checkpoint) DO UPDATE SET {updates}, source = 'api',
                collected_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            """,
            (post_id, checkpoint, *values.values()),
        )
