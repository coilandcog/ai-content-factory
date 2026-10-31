"""YouTube Analytics API v2 (INTEGRATIONS.md §5, ROADMAP 2.5, D-117): a video's totals between
two dates, read with the owner's sign-in (scope yt-analytics.readonly, D-102).

Dates are whole days in US Pacific time (how YouTube Analytics counts them) and the numbers can
arrive up to about 2 days late. There is no "stayed to watch" metric, so that stays manual.
Errors and retries follow the Data API client (youtube.py).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from googleapiclient.discovery import build

from factory.integrations.youtube import _execute, _with_retries

# Analytics metric name -> metrics column (DATABASE.md §3).
METRICS = {
    "views": "views",
    "estimatedMinutesWatched": "watch_time_min",
    "averageViewDuration": "avg_view_duration_s",
    "averageViewPercentage": "avg_view_pct",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
    "subscribersGained": "subscribers_gained",
}


def service(credentials: Any, *, builder: Callable[..., Any] = build) -> Any:
    """A YouTube Analytics client acting as the signed-in channel."""
    return builder("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)


def video_totals(
    api: Any,
    video_id: str,
    start: date,
    end: date,
    *,
    before_call: Callable[[], None],
    retry_wait_s: float = 2,
) -> dict[str, float] | None:
    """One video's totals from `start` to `end` (both included), keyed by metrics column; None
    when YouTube has no row for it yet."""

    def call() -> Any:
        before_call()
        return _execute(
            api.reports().query(
                ids="channel==MINE",
                startDate=start.isoformat(),
                endDate=end.isoformat(),
                metrics=",".join(METRICS),
                dimensions="video",
                filters=f"video=={video_id}",
            )
        )

    response = _with_retries(call, "YouTube Analytics reports.query", retry_wait_s)
    rows = response.get("rows") or []
    if not rows:
        return None
    names = [header["name"] for header in response.get("columnHeaders", [])]
    values = dict(zip(names, rows[0], strict=True))
    return {column: values[name] for name, column in METRICS.items() if name in values}
