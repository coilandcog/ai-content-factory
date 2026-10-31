"""`factory learn`: the YouTube Analytics pull (ROADMAP 2.5, D-117). YouTube is faked."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from factory.budgets import usage_today
from factory.config import EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel, upsert_platform_account
from factory.errors import BudgetExceededError, FactoryError
from factory.stages import learn
from factory.stages.learn import Numbers

# #5-like video: public Sat 24 Oct 2026 16:00 UTC = 09:00 Pacific (PDT, UTC-7).
WENT_PUBLIC = "2026-10-24T16:00:00Z"
PUBLISHED = datetime(2026, 10, 24, 16, 0, tzinfo=UTC)
# Tue 3 Nov 2026 13:00 Pacific: 24h, 72h and 7d are final, 28d is not.
NOV_3 = datetime(2026, 11, 3, 21, 0, tzinfo=UTC)
HEADERS = ["video", "views", "estimatedMinutesWatched", "averageViewDuration",
           "averageViewPercentage", "likes", "comments", "shares", "subscribersGained"]  # fmt: skip


class FakeRequest:
    def __init__(self, answer: Any) -> None:
        self.answer = answer

    def execute(self) -> Any:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FakeYouTube:
    """Data API videos.list(part=snippet) and Analytics reports.query in one object."""

    def __init__(self, public: dict[str, str] | None = None, rows: Any = None) -> None:
        self.public = {"VID5": WENT_PUBLIC} if public is None else public
        self.rows = rows  # None: one row of numbers; [] : no data; an exception: raised
        self.queries: list[dict[str, Any]] = []

    def videos(self) -> FakeYouTube:
        return self

    def reports(self) -> FakeYouTube:
        return self

    def list(self, **kwargs: Any) -> FakeRequest:
        assert kwargs["part"] == "snippet"
        items = [{"id": vid, "snippet": {"publishedAt": at}}
                 for vid, at in self.public.items() if vid in kwargs["id"].split(",")]  # fmt: skip
        return FakeRequest({"items": items})

    def query(self, **kwargs: Any) -> FakeRequest:
        self.queries.append(kwargs)
        if isinstance(self.rows, Exception):
            return FakeRequest(self.rows)
        rows = [["VID5", 1200, 640.5, 32, 133.4, 40, 3, 5, 2]] if self.rows is None else self.rows
        return FakeRequest({"columnHeaders": [{"name": n} for n in HEADERS], "rows": rows})


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def post(ctx: AppContext, *, platform: str = "youtube", dry_run: int = 0,
         status: str = "published", video: str = "VID5") -> int:  # fmt: skip
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="vehicles"
    )
    channel_id = ctx.conn.execute("SELECT id FROM channels").fetchone()[0]
    account = upsert_platform_account(ctx.conn, channel_id, platform, "@coilandcog", "manual")
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO posts (content_id, platform_account_id, platform, status, external_id, "
            "url, published_at, dry_run) VALUES (?, ?, ?, ?, ?, ?, '2026-10-24T22:08:16Z', ?)",
            (content_id, account, platform, status, video, f"https://youtu.be/{video}", dry_run),
        )
    return content_id


def pull(ctx: AppContext, api: FakeYouTube, now: datetime, **kwargs: Any) -> list[learn.Pulled]:
    return learn.pull(
        ctx, now=now, load=lambda path: object(), builder=lambda *a, **k: api, **kwargs
    )


def test_windows_are_pacific_days_and_final_two_days_after_they_end() -> None:
    assert learn.window(PUBLISHED, "24h") == (date(2026, 10, 24), date(2026, 10, 25))
    assert learn.window(PUBLISHED, "7d") == (date(2026, 10, 24), date(2026, 10, 31))
    assert learn.due_day(PUBLISHED, "24h") == date(2026, 10, 28)
    assert learn.due_day(PUBLISHED, "28d") == date(2026, 11, 24)
    # 23:30 UTC is still the same afternoon in California, not the next day
    late = datetime(2026, 10, 24, 23, 30, tzinfo=UTC)
    assert learn.window(late, "24h")[0] == date(2026, 10, 24)


def test_final_checkpoints_are_saved_and_the_rest_wait(ctx: AppContext) -> None:
    content_id = post(ctx)
    api = FakeYouTube()

    pulled = pull(ctx, api, NOV_3)

    states = {p.checkpoint: p.state for p in pulled}
    assert states == {"24h": "saved", "72h": "saved", "7d": "saved", "28d": "waiting"}
    assert [q["endDate"] for q in api.queries] == ["2026-10-25", "2026-10-27", "2026-10-31"]
    assert (
        api.queries[0]["filters"] == "video==VID5" and api.queries[0]["startDate"] == "2026-10-24"
    )
    row = ctx.conn.execute("SELECT * FROM metrics WHERE checkpoint = '7d'").fetchone()
    assert (row["source"], row["views"], row["avg_view_pct"]) == ("api", 1200, 133.4)
    assert (row["avg_view_duration_s"], row["watch_time_min"], row["subscribers_gained"]) == (
        32, 640.5, 2)  # fmt: skip
    assert row["stayed_to_watch_pct"] is None
    # the recorded time is corrected to the moment YouTube says it went public
    assert ctx.conn.execute("SELECT published_at FROM posts").fetchone()[0] == WENT_PUBLIC
    assert usage_today(ctx.conn, ctx.channel)["youtube_analytics"][0] == 3
    assert "1200 views" in learn.show(ctx, content_id)


def test_a_second_run_pulls_nothing_already_pulled(ctx: AppContext) -> None:
    post(ctx)
    pull(ctx, FakeYouTube(), NOV_3)
    api = FakeYouTube()

    pulled = pull(ctx, api, NOV_3)

    assert [p.state for p in pulled] == ["waiting"] and api.queries == []
    assert ctx.conn.execute("SELECT count(*) FROM metrics").fetchone()[0] == 3


def test_all_four_pulled_needs_no_sign_in(ctx: AppContext) -> None:
    post(ctx)
    pull(ctx, FakeYouTube(), datetime(2026, 12, 1, tzinfo=UTC))

    def no_load(path: Path) -> Any:
        raise AssertionError("must not sign in")

    assert learn.pull(ctx, now=datetime(2026, 12, 2, tzinfo=UTC), load=no_load) == []


def test_a_typed_stayed_share_survives_and_api_numbers_replace_typed_ones(ctx: AppContext) -> None:
    content_id = post(ctx)
    learn.add_manual(ctx, content_id, "youtube", "7d", Numbers(views=999, stayed_to_watch_pct=61))

    pull(ctx, FakeYouTube(), NOV_3)

    row = ctx.conn.execute("SELECT * FROM metrics WHERE checkpoint = '7d'").fetchone()
    assert (row["source"], row["views"], row["stayed_to_watch_pct"]) == ("api", 1200, 61)


def test_no_row_yet_is_reported_and_tried_again(ctx: AppContext) -> None:
    post(ctx)

    pulled = pull(ctx, FakeYouTube(rows=[]), datetime(2026, 10, 29, tzinfo=UTC))

    assert [(p.checkpoint, p.state) for p in pulled][0] == ("24h", "no_data")
    assert ctx.conn.execute("SELECT count(*) FROM metrics").fetchone()[0] == 0


def test_test_posts_tiktok_and_scheduled_posts_are_skipped(ctx: AppContext) -> None:
    post(ctx, dry_run=1, video="DRY")
    post(ctx, platform="tiktok", video="TT")
    post(ctx, status="scheduled", video="LATER")

    assert pull(ctx, FakeYouTube(), NOV_3) == []


def test_only_the_chosen_item_and_an_unknown_one_is_refused(ctx: AppContext) -> None:
    post(ctx)
    other = post(ctx, video="VID6")
    api = FakeYouTube(public={"VID5": WENT_PUBLIC, "VID6": WENT_PUBLIC})

    pulled = pull(ctx, api, NOV_3, content_id=other)

    assert {p.content_id for p in pulled} == {other}
    assert {q["filters"] for q in api.queries} == {"video==VID6"}
    with pytest.raises(FactoryError, match="no published YouTube post"):
        pull(ctx, api, NOV_3, content_id=999)


def test_a_deleted_video_is_skipped(ctx: AppContext) -> None:
    post(ctx)

    assert pull(ctx, FakeYouTube(public={}), NOV_3) == []


def test_quota_refusal_stops_the_run(ctx: AppContext) -> None:
    post(ctx)
    refused = HttpError(
        Response({"status": 403}),
        b'{"error": {"errors": [{"reason": "quotaExceeded"}], "message": "quota"}}',
    )

    with pytest.raises(BudgetExceededError, match="quotaExceeded"):
        pull(ctx, FakeYouTube(rows=refused), NOV_3)
