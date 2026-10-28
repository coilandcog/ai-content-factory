"""Learn stage, part 1 (ROADMAP 2.5, C1, D-101): numbers the owner copies from YouTube Studio and
TikTok become `metrics` rows, so learning from results starts before the Analytics API (2.2/2.5).
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass

from factory.context import AppContext
from factory.errors import FactoryError

CHECKPOINTS = ("24h", "72h", "7d", "28d")
PLATFORMS = ("youtube", "tiktok")


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
