"""Weekly report (ROADMAP 2.9, D-119): last week's posts, the labels and the patterns behind
them, sent to the owner in Telegram. Patterns are counts per pillar, hook type and format,
never "copy this video" (CONTENT_STRATEGY §10); ideation starts using them in task 3.1.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from factory.context import AppContext
from factory.integrations.telegram_bot import MAX_TEXT, TelegramClient
from factory.stages import learn
from factory.stages.bot import split_text

log = logging.getLogger("factory.report")

PATTERN_FIELDS = (("pillar", "Pillar"), ("hook_type", "Hook"), ("format", "Format"))


def last_week(today: date) -> tuple[date, date]:
    """Monday to Sunday of the last full week before `today` (on a Monday: the week just ended)."""
    monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=6)


def weekly(ctx: AppContext, now: datetime | None = None) -> str:
    """The report text. Refreshes the labels first, so it shows the newest numbers."""
    zone = ZoneInfo(ctx.channel.timezone)
    now = now or datetime.now(UTC)
    start, end = last_week(now.astimezone(zone).date())
    labelled = learn.compute_labels(ctx)
    lines = [f"{ctx.channel.display_name}: weekly report {start:%a %d %b} – {end:%a %d %b %Y}"]

    lines += ["", "Posted last week:"]
    posted = _posted(ctx, zone, start, end)
    lines += posted or ["  nothing"]

    by_label = {name: [item for item in labelled if item.label == name] for name in learn.LABELS}
    lines += ["", f"Labels (7 days after posting, {len(labelled)} videos):"]
    for name in ("winner", "loser"):
        for item in by_label[name]:
            lines.append(f"  {name.upper()} #{item.content_id} {item.object_name}"
                         f"{' (yours)' if item.overridden else ''}: {_numbers(item)}")  # fmt: skip
    lines.append(f"  {len(by_label['winner'])} winner · {len(by_label['average'])} average · "
                 f"{len(by_label['loser'])} loser")  # fmt: skip

    if patterns := _patterns(ctx, labelled):
        lines += ["", "Patterns (median 7-day views; W/A/L = winners/average/losers):", *patterns]

    if todo := _todo(ctx, labelled):
        lines += ["", "To do:", *todo]
    return "\n".join(lines)


def send(ctx: AppContext, client: TelegramClient, chat_id: int, text: str) -> int:
    """Send the report (split at Telegram's message limit); returns the number of messages."""
    prefix = "[DRY RUN] " if ctx.env.dry_run else ""
    parts = split_text(prefix + text, MAX_TEXT)
    for part in parts:
        client.send_message(chat_id, part)
    log.info("weekly report sent in %d message(s)", len(parts), extra={"event": "report_sent"})
    return len(parts)


def _posted(ctx: AppContext, zone: ZoneInfo, start: date, end: date) -> list[str]:
    rows = ctx.conn.execute(
        """
        SELECT p.content_id, c.object_name, p.platform, p.published_at,
               (SELECT m.views FROM metrics m WHERE m.post_id = p.id AND m.views IS NOT NULL
                ORDER BY CASE m.checkpoint WHEN '28d' THEN 1 WHEN '7d' THEN 2 WHEN '72h' THEN 3
                                           ELSE 4 END LIMIT 1) AS views
        FROM posts p JOIN contents c ON c.id = p.content_id
        JOIN channels ch ON ch.id = c.channel_id
        WHERE ch.slug = ? AND p.status = 'published' AND p.dry_run = 0
              AND p.published_at IS NOT NULL
        ORDER BY p.published_at, p.platform
        """,
        (ctx.channel.slug,),
    ).fetchall()
    out = []
    for r in rows:
        day = _parse(r["published_at"]).astimezone(zone).date()
        if start <= day <= end:
            views = "no numbers yet" if r["views"] is None else f"{r['views']} views"
            out.append(f"  #{r['content_id']} {r['object_name']} on {r['platform']} "
                       f"({day:%a %d %b}): {views}")  # fmt: skip
    return out


def _numbers(item: learn.Label) -> str:
    b = item.basis or {}
    if not b:
        return "set by you, no 7-day numbers"
    parts = [f"{b['views']} views (median {b['median']:g} of {b['median_of']})"]
    if b.get("stayed_pct") is not None:
        parts.append(f"stayed {b['stayed_pct']:g}%")
    if b.get("avg_view_pct") is not None:
        parts.append(f"avg viewed {b['avg_view_pct']:g}%")
    return ", ".join(parts)


def _patterns(ctx: AppContext, labelled: list[learn.Label]) -> list[str]:
    if not labelled:
        return []
    ids = [item.content_id for item in labelled]
    rows = {r["id"]: r for r in ctx.conn.execute(
        f"SELECT id, pillar, hook_type, format FROM contents "
        f"WHERE id IN ({', '.join('?' for _ in ids)})", ids,
    ).fetchall()}  # fmt: skip
    out = []
    for field, title in PATTERN_FIELDS:
        groups: dict[str, list[learn.Label]] = defaultdict(list)
        for item in labelled:
            groups[rows[item.content_id][field] or "not set"].append(item)
        cells = []
        for value, items in sorted(groups.items()):
            counts = "/".join(str(sum(i.label == name for i in items)) for name in learn.LABELS)
            views = [i.basis["views"] for i in items if i.basis]
            median = f"{statistics.median(views):g}" if views else "-"
            cells.append(f"{value} {median} ({counts})")
        out.append(f"  {title}: " + " · ".join(cells))
    return out


def _todo(ctx: AppContext, labelled: list[learn.Label]) -> list[str]:
    """Stayed-to-watch shares to type in (a winner needs one) and videos still waiting."""
    out = []
    for item in labelled:
        if item.basis and item.basis.get("stayed_pct") is None:
            out.append(f"  #{item.content_id}: add stayed-to-watch from YouTube Studio: "
                       f"`factory metrics add {item.content_id} --platform youtube "
                       f"--checkpoint 7d --stayed <pct>`")  # fmt: skip
    done = {item.content_id for item in labelled if item.basis}
    rows = ctx.conn.execute(
        """
        SELECT p.content_id, c.object_name, p.published_at FROM posts p
        JOIN contents c ON c.id = p.content_id JOIN channels ch ON ch.id = c.channel_id
        WHERE ch.slug = ? AND p.platform = 'youtube' AND p.status = 'published'
              AND p.dry_run = 0 AND p.published_at IS NOT NULL
        ORDER BY p.content_id
        """,
        (ctx.channel.slug,),
    ).fetchall()
    for r in rows:
        if r["content_id"] not in done:
            due = learn.due_day(_parse(r["published_at"]), "7d")
            out.append(f"  #{r['content_id']} {r['object_name']}: 7-day numbers final from "
                       f"{due:%a %d %b} (then `factory learn`)")  # fmt: skip
    return out


def _parse(stamp: str) -> datetime:
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
