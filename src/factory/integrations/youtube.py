"""YouTube Data API v3 (INTEGRATIONS.md §4). Discover reads public data with the API key (plain
httpx); the channel check and uploads use the owner's sign-in (OAuth, Google's official client,
ROADMAP 2.2, D-102).

Quota: search.list costs 100 units, videos.list and channels.list 1 unit per call. Callers pass a
`before_call(units)` that reserves the units in api_usage first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from factory.errors import BudgetExceededError, IntegrationError
from factory.integrations.http_json import get_json

API = "https://www.googleapis.com/youtube/v3"
SEARCH_UNITS = 100
VIDEOS_UNITS = 1
CHANNELS_UNITS = 1
UPLOAD_UNITS = 1  # videos.insert counts in its own "Video Uploads" quota (INTEGRATIONS.md §4)
PRIVACY = ("private", "unlisted", "public")


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


# --- signed-in calls (OAuth) ---------------------------------------------------------------


@dataclass(frozen=True)
class Channel:
    channel_id: str
    title: str
    handle: str  # "@coilandcog", lowercase


def service(credentials: Any, *, builder: Callable[..., Any] = build) -> Any:
    """A YouTube Data API client acting as the signed-in channel."""
    return builder("youtube", "v3", credentials=credentials, cache_discovery=False)


def my_channel(api: Any, *, before_call: Callable[[int], None]) -> Channel:
    """The channel the sign-in belongs to (channels.list mine=true)."""
    before_call(CHANNELS_UNITS)
    response = _execute(api.channels().list(part="snippet", mine=True))
    items = response.get("items") or []
    if not items:
        raise IntegrationError("this Google account has no YouTube channel")
    snippet = items[0]["snippet"]
    return Channel(items[0]["id"], snippet.get("title", ""), snippet.get("customUrl", "").lower())


def upload(
    api: Any,
    path: Any,
    *,
    title: str,
    description: str,
    category_id: int,
    privacy: str,
    before_call: Callable[[int], None],
) -> str:
    """Upload one video, always labelled as AI-made (containsSyntheticMedia, D-039); returns its
    id. The caller decides the privacy; tests and the test upload pass 'unlisted'."""
    if privacy not in PRIVACY:
        raise ValueError(f"privacy must be one of {PRIVACY}")
    body = {
        "snippet": {"title": title, "description": description, "categoryId": str(category_id),
                    "defaultLanguage": "en"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},
    }  # fmt: skip
    before_call(UPLOAD_UNITS)
    request = api.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(path), mimetype="video/mp4", resumable=True, chunksize=-1),
    )
    response = None
    while response is None:  # resumable upload: one chunk (the whole small file), then the answer
        _, response = _execute(request, chunk=True)
    return response["id"]


def _execute(request: Any, *, chunk: bool = False) -> Any:
    try:
        return request.next_chunk() if chunk else request.execute()
    except HttpError as exc:
        reason = _http_reason(exc)
        if reason in {"quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded"}:
            raise BudgetExceededError(f"YouTube refused: {reason}; try again tomorrow") from exc
        raise IntegrationError(f"YouTube HTTP {exc.resp.status}: {reason}") from exc


def _http_reason(exc: HttpError) -> str:
    details = getattr(exc, "error_details", None) or []
    for detail in details:
        if isinstance(detail, dict) and detail.get("reason"):
            return str(detail["reason"])
    return str(getattr(exc, "reason", "") or "error")
