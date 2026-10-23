from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from factory.budgets import check_and_record, usage_date, usage_today
from factory.config import ChannelConfig, load_channel
from factory.db import connect, migrate, upsert_channel
from factory.errors import BudgetExceededError

NOON = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def channel() -> ChannelConfig:
    return load_channel("coil-and-cog")  # gemini 60, youtube_data 3000, flow 50 per day


@pytest.fixture
def conn(tmp_path: Path, channel: ChannelConfig) -> sqlite3.Connection:
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    upsert_channel(connection, channel)
    yield connection
    connection.close()


def test_usage_adds_up(conn: sqlite3.Connection, channel: ChannelConfig) -> None:
    check_and_record(conn, channel, "flow", 20, now=NOON)
    total = check_and_record(conn, channel, "flow", 20, now=NOON)

    assert total == 40
    assert usage_today(conn, channel, NOON)["flow"] == (40, 50)


def test_overrun_raises_and_records_nothing(
    conn: sqlite3.Connection, channel: ChannelConfig
) -> None:
    check_and_record(conn, channel, "flow", 40, now=NOON)

    with pytest.raises(BudgetExceededError, match="flow: 40 used \\+ 20 requested"):
        check_and_record(conn, channel, "flow", 20, now=NOON)  # 60 > 50

    assert usage_today(conn, channel, NOON)["flow"] == (40, 50)
    assert check_and_record(conn, channel, "flow", 10, now=NOON) == 50  # exactly the limit is ok


def test_services_without_a_limit_are_only_counted(
    conn: sqlite3.Connection, channel: ChannelConfig
) -> None:
    for _ in range(100):
        check_and_record(conn, channel, "wikimedia", now=NOON)

    assert usage_today(conn, channel, NOON)["wikimedia"] == (100, None)


def test_the_day_is_the_channels_local_date(channel: ChannelConfig) -> None:
    # 23:30 UTC on 23 Oct is already 24 Oct in Berlin (UTC+2 in summer time).
    late = datetime(2026, 10, 23, 23, 30, tzinfo=UTC)

    assert usage_date(channel, late) == "2026-10-24"


def test_unknown_service_is_refused(conn: sqlite3.Connection, channel: ChannelConfig) -> None:
    with pytest.raises(ValueError, match="unknown service"):
        check_and_record(conn, channel, "openai")
