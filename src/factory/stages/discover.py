"""discover: demand signals for the channel's seed topics (ROADMAP 1.14, D-078).

Two official sources, stored in trend_signals:
- Wikipedia pageviews (free, every day): views of the last 7 days and the trend ratio, i.e. the
  last 7 days divided by the average 7 days of the 28 before (above 1.0 = interest is rising).
- YouTube (100 quota units per search, so about once a week per topic): the median views of the
  25 most-viewed English videos of the last 30 days, with the top 5 titles as inspiration.

Re-running is safe: a topic whose signal is still fresh is skipped, so no duplicate rows.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from factory.budgets import check_and_record
from factory.context import AppContext, StageResult
from factory.errors import BudgetExceededError, IntegrationError
from factory.integrations import wikimedia, youtube
from factory.integrations.http_json import HttpStatusError
from factory.stages.script import http_client

log = logging.getLogger("factory.discover")

WIKI_SOURCE = "wikimedia_pageviews"
YOUTUBE_SOURCE = "youtube_search"
WIKI_FRESH = timedelta(hours=20)  # about once a day
YOUTUBE_FRESH = timedelta(days=6, hours=20)  # about once a week
YOUTUBE_WINDOW = timedelta(days=30)
RECENT_DAYS, BEFORE_DAYS = 7, 28
MIN_VIEWS_FOR_TREND = 280  # in the 28 days before: about 10 views a day
TOP_TITLES = 5


@dataclass
class DiscoverResult(StageResult):
    """StageResult (processed = topics with new signals) plus what the owner should know."""

    youtube_searched: int = 0
    youtube_waiting: int = 0  # topics left for another day (budget or quota reached)
    youtube_note: str | None = None


def run(
    ctx: AppContext,
    *,
    http: httpx.Client | None = None,
    now: datetime | None = None,
    retry_wait_s: float = 2,
) -> DiscoverResult:
    """Collect fresh signals for every seed topic. One failing topic never stops the others."""
    now = now or datetime.now(UTC)
    channel_id = ctx.conn.execute(
        "SELECT id FROM channels WHERE slug = ?", (ctx.channel.slug,)
    ).fetchone()[0]
    topics = list(dict.fromkeys(ctx.channel.niche.seed_topics))
    result = DiscoverResult()
    own_http = http is None
    http = http or http_client(ctx)  # the Wikimedia User-Agent rules apply here too
    try:
        updated: set[str] = set()
        failed: set[str] = set()
        for topic in topics:
            if _fresh(ctx.conn, channel_id, WIKI_SOURCE, topic, now - WIKI_FRESH):
                continue
            try:
                if _wikipedia(ctx, http, channel_id, topic, now, retry_wait_s):
                    updated.add(topic)
            except IntegrationError as exc:
                log.warning("Wikipedia signals for %r failed: %s", topic, exc,
                            extra={"event": "discover_wiki_failed"})  # fmt: skip
                failed.add(topic)
        _youtube_all(ctx, http, channel_id, topics, now, retry_wait_s, result, updated, failed)
    finally:
        if own_http:
            http.close()
    result.processed, result.failed = len(updated), len(failed - updated)
    return result


# --- Wikipedia ---------------------------------------------------------------------------------


def _wikipedia(
    ctx: AppContext,
    http: httpx.Client,
    channel_id: int,
    topic: str,
    now: datetime,
    retry_wait_s: float,
) -> bool:
    """Store pageviews_7d and pageviews_trend_ratio for one topic. False = no article."""
    budget = lambda: check_and_record(ctx.conn, ctx.channel, "wikimedia")  # noqa: E731
    override = ctx.channel.niche.wikipedia_articles.get(topic)
    title = wikimedia.article_title(http, topic, override=override, before_call=budget,
                                    retry_wait_s=retry_wait_s)  # fmt: skip
    if title is None:
        log.warning("no Wikipedia article for %r", topic, extra={"event": "discover_no_article"})
        return False
    end = now.date() - timedelta(days=1)  # today's numbers are not complete yet
    start = end - timedelta(days=RECENT_DAYS + BEFORE_DAYS - 1)
    try:
        views = wikimedia.daily_views(http, title, start, end, before_call=budget,
                                      retry_wait_s=retry_wait_s)  # fmt: skip
    except HttpStatusError as exc:
        if exc.status != 404:
            raise
        log.warning("no pageviews for %r (%s)", topic, title, extra={"event": "discover_no_views"})
        return False
    recent, before = sum(views[-RECENT_DAYS:]), sum(views[:BEFORE_DAYS])
    url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
    raw = {"article": title, "from": start.isoformat(), "to": end.isoformat(),
           "views_recent_7d": recent, "views_before_28d": before}  # fmt: skip
    rows = [("pageviews_7d", float(recent))]
    if before >= MIN_VIEWS_FOR_TREND:  # tiny pages swing wildly: 1 view -> 4 views is not a trend
        rows.append(("pageviews_trend_ratio", round(recent / (before / 4), 3)))
    with ctx.conn:
        for metric, value in rows:
            _insert(ctx.conn, channel_id, WIKI_SOURCE, topic, title, url, metric, value, now, raw)
    return True


# --- YouTube -----------------------------------------------------------------------------------


def _youtube_all(
    ctx: AppContext,
    http: httpx.Client,
    channel_id: int,
    topics: list[str],
    now: datetime,
    retry_wait_s: float,
    result: DiscoverResult,
    updated: set[str],
    failed: set[str],
) -> None:
    """Search the topics whose YouTube signal is oldest (or missing) until the budget is used."""
    key = ctx.env.youtube_api_key
    if key is None:
        result.youtube_note = "YOUTUBE_API_KEY is missing in .env: YouTube signals skipped"
        log.warning(result.youtube_note, extra={"event": "discover_no_youtube_key"})
        return
    due = [t for t in topics if not _fresh(ctx.conn, channel_id, YOUTUBE_SOURCE, t,
                                           now - YOUTUBE_FRESH)]  # fmt: skip
    due.sort(key=lambda t: _last_seen(ctx.conn, channel_id, YOUTUBE_SOURCE, t) or "")
    for index, topic in enumerate(due):
        try:
            videos = youtube.recent_popular_videos(
                http, key.get_secret_value(), topic, now - YOUTUBE_WINDOW,
                before_call=lambda units: check_and_record(ctx.conn, ctx.channel,
                                                           "youtube_data", units),
                retry_wait_s=retry_wait_s,
            )  # fmt: skip
        except BudgetExceededError as exc:
            _stop_youtube(result, len(due) - index, f"daily budget reached ({exc})")
            return
        except HttpStatusError as exc:
            if "quotaExceeded" in str(exc):
                _stop_youtube(result, len(due) - index, "Google's daily quota is used up")
                return
            log.warning("YouTube signals for %r failed: %s", topic, exc,
                        extra={"event": "discover_youtube_failed"})  # fmt: skip
            failed.add(topic)
            continue
        except IntegrationError as exc:
            log.warning("YouTube signals for %r failed: %s", topic, exc,
                        extra={"event": "discover_youtube_failed"})  # fmt: skip
            failed.add(topic)
            continue
        result.youtube_searched += 1
        median = float(statistics.median(v.views for v in videos)) if videos else 0.0
        top = [{"title": v.title, "views": v.views, "id": v.video_id} for v in videos[:TOP_TITLES]]
        with ctx.conn:
            _insert(ctx.conn, channel_id, YOUTUBE_SOURCE, topic,
                    top[0]["title"] if top else None,
                    f"https://youtu.be/{top[0]['id']}" if top else None,
                    "video_views_median_30d", median, now,
                    {"videos": len(videos), "top": top})  # fmt: skip
        updated.add(topic)


def _stop_youtube(result: DiscoverResult, waiting: int, why: str) -> None:
    result.youtube_waiting = waiting
    result.youtube_note = f"YouTube: {why}; {waiting} topic(s) wait for the next run"
    log.info(result.youtube_note, extra={"event": "discover_youtube_stopped"})


# --- database ----------------------------------------------------------------------------------


def _fresh(
    conn: sqlite3.Connection, channel_id: int, source: str, topic: str, since: datetime
) -> bool:
    last = _last_seen(conn, channel_id, source, topic)
    return last is not None and last >= _iso(since)


def _last_seen(conn: sqlite3.Connection, channel_id: int, source: str, topic: str) -> str | None:
    return conn.execute(
        """
        SELECT MAX(observed_at) FROM trend_signals
        WHERE channel_id = ? AND source = ? AND query = ?
        """,
        (channel_id, source, topic),
    ).fetchone()[0]


def _insert(
    conn: sqlite3.Connection,
    channel_id: int,
    source: str,
    topic: str,
    title: str | None,
    url: str | None,
    metric: str,
    value: float,
    now: datetime,
    raw: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO trend_signals (channel_id, source, query, title, url, metric_name,
            metric_value, observed_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (channel_id, source, topic, title, url, metric, value, _iso(now),
         json.dumps(raw, ensure_ascii=False)),
    )  # fmt: skip


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- report (factory status --signals) ---------------------------------------------------------


@dataclass(frozen=True)
class TopicSignals:
    topic: str
    article: str | None
    views_7d: float | None
    trend_ratio: float | None
    youtube_median: float | None
    youtube_top: str | None


def latest_signals(conn: sqlite3.Connection, channel_id: int) -> list[TopicSignals]:
    """The newest value of each metric per topic, highest trend ratio first."""
    rows = conn.execute(
        """
        SELECT t.query, t.metric_name, t.metric_value, t.title FROM trend_signals t
        JOIN (SELECT query, metric_name, MAX(observed_at) AS last FROM trend_signals
              WHERE channel_id = ? GROUP BY query, metric_name) n
          ON n.query = t.query AND n.metric_name = t.metric_name AND n.last = t.observed_at
        WHERE t.channel_id = ?
        """,
        (channel_id, channel_id),
    ).fetchall()
    data: dict[str, dict[str, object]] = {}
    for row in rows:
        topic = data.setdefault(row["query"], {})
        topic[row["metric_name"]] = row["metric_value"]
        topic[f"{row['metric_name']}_title"] = row["title"]
    signals = [
        TopicSignals(
            topic=name,
            article=values.get("pageviews_7d_title"),  # type: ignore[arg-type]
            views_7d=values.get("pageviews_7d"),  # type: ignore[arg-type]
            trend_ratio=values.get("pageviews_trend_ratio"),  # type: ignore[arg-type]
            youtube_median=values.get("video_views_median_30d"),  # type: ignore[arg-type]
            youtube_top=values.get("video_views_median_30d_title"),  # type: ignore[arg-type]
        )
        for name, values in data.items()
    ]
    return sorted(signals, key=lambda s: (s.trend_ratio is None, -(s.trend_ratio or 0)))
