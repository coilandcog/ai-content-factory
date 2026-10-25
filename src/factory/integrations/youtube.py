"""YouTube Data API v3, read-only with the API key (INTEGRATIONS.md §4). Discover uses it now;
uploads (OAuth) come in Phase 2.

Quota: search.list costs 100 units, videos.list 1 unit per call of up to 50 ids. Callers pass a
`before_call(units)` that reserves the units in api_usage first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx

from factory.integrations.http_json import get_json

API = "https://www.googleapis.com/youtube/v3"
SEARCH_UNITS = 100
VIDEOS_UNITS = 1


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    views: int


def recent_popular_videos(
    http: httpx.Client,
    api_key: str,
    query: str,
    published_after: datetime,
    *,
    before_call: Callable[[int], None],
    max_results: int = 25,
    retry_wait_s: float = 2,
) -> list[Video]:
    """The most-viewed English videos about `query` published after `published_after`, with
    their view counts, most viewed first (search.list, then videos.list for the counts)."""
    found = get_json(
        http,
        f"{API}/search",
        service="YouTube search.list",
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": "viewCount",
            "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "relevanceLanguage": "en",
            "maxResults": max_results,
            "key": api_key,
        },
        before_call=lambda: before_call(SEARCH_UNITS),
        retry_wait_s=retry_wait_s,
        reason_of=_reason,
    )
    titles = {
        item["id"]["videoId"]: item["snippet"]["title"]
        for item in found.get("items", [])
        if item.get("id", {}).get("videoId")
    }
    if not titles:
        return []
    stats = get_json(
        http,
        f"{API}/videos",
        service="YouTube videos.list",
        params={"part": "statistics", "id": ",".join(titles), "key": api_key},
        before_call=lambda: before_call(VIDEOS_UNITS),
        retry_wait_s=retry_wait_s,
        reason_of=_reason,
    )
    videos = [
        Video(item["id"], titles[item["id"]], int(item["statistics"].get("viewCount", 0)))
        for item in stats.get("items", [])
        if item.get("id") in titles
    ]
    return sorted(videos, key=lambda v: v.views, reverse=True)


def _reason(response: httpx.Response) -> str:
    """Google's short error reason (e.g. quotaExceeded, keyInvalid), never the URL."""
    try:
        return response.json()["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
