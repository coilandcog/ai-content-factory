"""Daily usage budgets per external service (ARCHITECTURE.md §6, INTEGRATIONS.md §0).

Call check_and_record() *before* every external call: it either reserves the units or raises
BudgetExceededError without recording anything, so a stage stops before going over.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from factory.config import ChannelConfig
from factory.errors import BudgetExceededError, FactoryError

# Allowed values of api_usage.service (DATABASE.md §3).
SERVICES = (
    "gemini",
    "youtube_data",
    "youtube_analytics",
    "tiktok",
    "flow",
    "wikimedia",
    "telegram",
)


def daily_limits(channel: ChannelConfig) -> dict[str, int]:
    """Services with a limit in budgets_per_day. Other services are counted but not limited."""
    budgets = channel.budgets_per_day
    return {
        "gemini": budgets.gemini_calls,
        "youtube_data": budgets.youtube_units,
        "flow": budgets.flow_credits,
    }


def usage_date(channel: ChannelConfig, now: datetime | None = None) -> str:
    """Today's date in the channel's time zone (api_usage.usage_date is a local date)."""
    moment = now or datetime.now(UTC)
    return moment.astimezone(ZoneInfo(channel.timezone)).date().isoformat()


def check_and_record(
    conn: sqlite3.Connection,
    channel: ChannelConfig,
    service: str,
    units: int = 1,
    *,
    now: datetime | None = None,
) -> int:
    """Reserve `units` of today's budget for `service` and return today's new total.

    Raises BudgetExceededError, recording nothing, if that would go over the daily limit.
    """
    if service not in SERVICES:
        raise ValueError(f"unknown service '{service}'")
    if units < 1:
        raise ValueError("units must be at least 1")
    day = usage_date(channel, now)
    limit = daily_limits(channel).get(service)

    # BEGIN IMMEDIATE takes the write lock now, so two processes can't both pass the check.
    conn.execute("BEGIN IMMEDIATE")
    try:
        channel_id = _channel_id(conn, channel.slug)
        used = _used(conn, channel_id, service, day)
        if limit is not None and used + units > limit:
            raise BudgetExceededError(
                f"{service}: {used} used + {units} requested would exceed today's limit "
                f"of {limit} ({day}, {channel.slug})"
            )
        conn.execute(
            """
            INSERT INTO api_usage (channel_id, service, usage_date, units) VALUES (?, ?, ?, ?)
            ON CONFLICT (channel_id, service, usage_date) DO UPDATE
            SET units = units + excluded.units
            """,
            (channel_id, service, day, units),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return used + units


def usage_today(
    conn: sqlite3.Connection, channel: ChannelConfig, now: datetime | None = None
) -> dict[str, tuple[int, int | None]]:
    """Service -> (units used today, limit or None) for every service with a limit or usage."""
    day = usage_date(channel, now)
    channel_id = _channel_id(conn, channel.slug)
    rows = conn.execute(
        "SELECT service, units FROM api_usage WHERE channel_id = ? AND usage_date = ?",
        (channel_id, day),
    ).fetchall()
    used = {row["service"]: row["units"] for row in rows}
    limits = daily_limits(channel)
    return {s: (used.get(s, 0), limits.get(s)) for s in SERVICES if s in limits or s in used}


def _channel_id(conn: sqlite3.Connection, slug: str) -> int:
    row = conn.execute("SELECT id FROM channels WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise FactoryError(f"channel '{slug}' is not in the database: run `factory db init`")
    return row["id"]


def _used(conn: sqlite3.Connection, channel_id: int, service: str, day: str) -> int:
    row = conn.execute(
        "SELECT units FROM api_usage WHERE channel_id = ? AND service = ? AND usage_date = ?",
        (channel_id, service, day),
    ).fetchone()
    return row["units"] if row else 0
