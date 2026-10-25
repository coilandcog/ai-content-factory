from __future__ import annotations

import dataclasses
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from factory import cli
from factory.budgets import usage_today
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import connect, migrate, upsert_channel
from factory.integrations import wikimedia
from factory.stages import discover

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
KEY = "AIzaFAKE-youtube-key-1234567890"
TOPICS = ["alternator", "jet engine"]


class FakeWeb:
    """Wikipedia + YouTube answers. Every 7-day block of the 28 'before' days has 700 views and
    the last 7 days have 1400, so the trend ratio is 2.0."""

    def __init__(self, *, no_article: set[str] = frozenset(), broken: set[str] = frozenset(),
                 youtube_error: tuple[int, str] | None = None) -> None:  # fmt: skip
        self.no_article, self.broken, self.youtube_error = no_article, broken, youtube_error
        self.calls: list[str] = []
        self.searched: list[dict] = []
        self.views = (200, 100)  # per day: the last 7 days, the 28 before

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        params = request.url.params
        if "w/api.php" in url:
            self.searched.append(dict(params))
            topic = params.get("gsrsearch") or params["titles"]
            if topic in self.no_article:
                return httpx.Response(200, json={"batchcomplete": True})
            pages = [{"title": f"{topic.title()} (disambiguation)", "index": 1,
                      "pageprops": {"disambiguation": ""}},
                     {"title": topic.title(), "index": 2}]  # fmt: skip
            return httpx.Response(200, json={"query": {"pages": pages}})
        if "pageviews" in url:
            if any(t.title().replace(" ", "_") in url for t in self.broken):
                return httpx.Response(503)
            end = NOW.date() - timedelta(days=1)
            items = [
                {"timestamp": (end - timedelta(days=i)).strftime("%Y%m%d00"),
                 "views": self.views[0] if i < 7 else self.views[1]}
                for i in range(35)
            ]  # fmt: skip
            return httpx.Response(200, json={"items": items})
        if "youtube/v3/search" in url:
            if self.youtube_error:
                status, reason = self.youtube_error
                return httpx.Response(status, json={"error": {"errors": [{"reason": reason}]}})
            q = params["q"]
            items = [{"id": {"videoId": f"{q[:3]}{n}"}, "snippet": {"title": f"{q} video {n}"}}
                     for n in range(5)]  # fmt: skip
            return httpx.Response(200, json={"items": items})
        if "youtube/v3/videos" in url:
            ids = params["id"].split(",")
            items = [{"id": i, "statistics": {"viewCount": str(1000 * (n + 1))}}
                     for n, i in enumerate(ids)]  # fmt: skip
            return httpx.Response(200, json={"items": items})
        raise AssertionError(f"unexpected call {url}")


def make_ctx(db: Path, *, key: str | None = KEY, youtube_units: int = 3000) -> AppContext:
    conn = connect(db)
    migrate(conn)
    channel = load_channel("coil-and-cog")
    channel = channel.model_copy(update={
        "niche": channel.niche.model_copy(update={"seed_topics": TOPICS}),
        "budgets_per_day": channel.budgets_per_day.model_copy(
            update={"youtube_units": youtube_units}),
    })  # fmt: skip
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog",
                      wikimedia_user_agent="CoilAndCogTest/0.1 (https://example.org)",
                      youtube_api_key=key)  # fmt: skip
    return AppContext(load_settings(), env, channel, conn, REPO_ROOT)


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    context = make_ctx(tmp_path / "test.db")
    yield context
    context.conn.close()


def run(ctx: AppContext, web: FakeWeb, now: datetime = NOW) -> discover.DiscoverResult:
    http = httpx.Client(transport=httpx.MockTransport(web))
    return discover.run(ctx, http=http, now=now, retry_wait_s=0)


def signals(ctx: AppContext) -> list[dict]:
    rows = ctx.conn.execute("SELECT * FROM trend_signals ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# --- the stored signals --------------------------------------------------------------------


def test_wikipedia_and_youtube_signals_are_stored(ctx: AppContext) -> None:
    result = run(ctx, FakeWeb())

    assert (result.processed, result.failed, result.youtube_searched) == (2, 0, 2)
    rows = {(r["query"], r["metric_name"]): r for r in signals(ctx)}
    assert len(rows) == 6  # 2 topics x (views, trend ratio, YouTube median)
    assert rows[("alternator", "pageviews_7d")]["metric_value"] == 1400
    assert rows[("alternator", "pageviews_trend_ratio")]["metric_value"] == 2.0
    yt = rows[("jet engine", "video_views_median_30d")]
    assert yt["metric_value"] == 3000  # median of 1000..5000
    top = json.loads(yt["raw_json"])["top"]
    assert top[0] == {"title": "jet engine video 4", "views": 5000, "id": "jet4"}
    assert rows[("alternator", "pageviews_7d")]["title"] == "Alternator"
    assert rows[("alternator", "pageviews_7d")]["observed_at"] == "2026-10-25T10:00:00Z"
    usage = usage_today(ctx.conn, ctx.channel)  # budgets count by the real date
    assert usage["youtube_data"][0] == 2 * 101  # search 100 + videos 1, per topic
    assert usage["wikimedia"][0] == 4


def test_the_api_key_is_never_stored_or_logged(
    ctx: AppContext, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="factory")

    run(ctx, FakeWeb(youtube_error=(400, "badRequest")))

    assert KEY not in caplog.text
    assert all(KEY not in json.dumps(r) for r in signals(ctx))
    assert "YouTube search.list HTTP 400 badRequest" in caplog.text


# --- freshness: re-runs and the weekly YouTube rhythm --------------------------------------


def test_a_rerun_adds_nothing_and_calls_nothing(ctx: AppContext) -> None:
    run(ctx, FakeWeb())
    web = FakeWeb()

    result = run(ctx, web, NOW + timedelta(hours=1))

    assert (result.processed, web.calls) == (0, [])
    assert len(signals(ctx)) == 6


def test_wikipedia_daily_youtube_weekly(ctx: AppContext) -> None:
    run(ctx, FakeWeb())

    next_day = run(ctx, FakeWeb(), NOW + timedelta(days=1))
    assert (next_day.processed, next_day.youtube_searched) == (2, 0)
    next_week = run(ctx, FakeWeb(), NOW + timedelta(days=7))
    assert next_week.youtube_searched == 2


# --- limits and failures -------------------------------------------------------------------


def test_youtube_stops_cleanly_at_the_daily_budget(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path / "b.db", youtube_units=150)  # room for one search (101)

    result = run(ctx, FakeWeb())

    assert (result.youtube_searched, result.youtube_waiting) == (1, 1)
    assert "daily budget reached" in result.youtube_note
    assert result.processed == 2  # Wikipedia still covered both topics


def test_googles_quota_error_stops_youtube_for_the_day(ctx: AppContext) -> None:
    result = run(ctx, FakeWeb(youtube_error=(403, "quotaExceeded")))

    assert result.youtube_waiting == 2
    assert "quota is used up" in result.youtube_note
    assert result.failed == 0


def test_missing_article_is_skipped_and_a_broken_topic_does_not_stop_others(
    ctx: AppContext,
) -> None:
    result = run(ctx, FakeWeb(no_article={"alternator"}, broken={"jet engine"}))

    metrics = {(r["query"], r["metric_name"]) for r in signals(ctx)}
    assert ("alternator", "pageviews_7d") not in metrics  # no article: skipped
    assert ("jet engine", "pageviews_7d") not in metrics  # 503 four times: failed
    assert ("jet engine", "video_views_median_30d") in metrics  # YouTube still ran
    assert (result.processed, result.failed) == (2, 0)  # both got YouTube signals


def test_missing_youtube_key_skips_youtube_only(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path / "k.db", key=None)

    result = run(ctx, FakeWeb())

    assert result.processed == 2 and result.youtube_searched == 0
    assert "YOUTUBE_API_KEY is missing" in result.youtube_note


def test_days_without_views_count_as_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"timestamp": "2026102200", "views": 5}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    views = wikimedia.daily_views(http, "Alternator", date(2026, 10, 21), date(2026, 10, 23))

    assert views == [0, 5, 0]


# --- factory status --signals --------------------------------------------------------------


def test_signals_report_ranks_by_trend(ctx: AppContext) -> None:
    run(ctx, FakeWeb())
    ctx.conn.execute(
        "UPDATE trend_signals SET metric_value = 0.8 WHERE query = 'alternator' "
        "AND metric_name = 'pageviews_trend_ratio'"
    )

    report = cli._signals_report(ctx.conn, ctx.channel)

    lines = report.splitlines()
    assert "Top 2 of 2 seed topics" in lines[0]
    assert lines[2].split()[:2] == ["1", "jet"] and "2.00x" in lines[2]
    assert "0.80x" in lines[3] and "3,000" in lines[3]


def test_signals_report_without_data(ctx: AppContext) -> None:
    assert "Run: uv run factory discover" in cli._signals_report(ctx.conn, ctx.channel)


# --- choosing the article ------------------------------------------------------------------


def test_disambiguation_pages_are_skipped(ctx: AppContext) -> None:
    run(ctx, FakeWeb())

    titles = {r["title"] for r in signals(ctx) if r["source"] == "wikimedia_pageviews"}
    assert titles == {"Alternator", "Jet Engine"}


def test_yaml_article_overrides_the_search(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path / "o.db")
    niche = ctx.channel.niche.model_copy(update={"wikipedia_articles": {"jet engine": "Turbofan"}})
    ctx = dataclasses.replace(ctx, channel=ctx.channel.model_copy(update={"niche": niche}))
    web = FakeWeb()

    run(ctx, web)

    assert {"titles": "Turbofan"}.items() <= web.searched[1].items()
    assert "gsrsearch" in web.searched[0]  # alternator still uses the search


def test_tiny_pages_get_views_but_no_trend(ctx: AppContext) -> None:
    web = FakeWeb()
    web.views = (2, 1)  # 28 views in the 28 days before: below the minimum

    run(ctx, web)

    metrics = {r["metric_name"] for r in signals(ctx) if r["query"] == "alternator"}
    assert metrics == {"pageviews_7d", "video_views_median_30d"}


def test_wikipedia_articles_must_name_seed_topics() -> None:
    from pydantic import ValidationError

    channel = load_channel("coil-and-cog")
    data = channel.niche.model_dump() | {"wikipedia_articles": {"submarine": "Submarine"}}
    with pytest.raises(ValidationError, match="not seed_topics: \\['submarine'\\]"):
        type(channel.niche).model_validate(data)
