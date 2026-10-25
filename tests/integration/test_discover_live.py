"""Real Wikipedia pageviews and one real YouTube search. Off by default; run with:
uv run pytest -m integration -k discover

Uses 101 of the 10,000 daily YouTube quota units (not counted in the factory's api_usage).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from factory.config import load_env
from factory.integrations import wikimedia, youtube

pytestmark = pytest.mark.integration


def test_real_pageviews_and_youtube_search() -> None:
    env = load_env()
    if env.wikimedia_user_agent is None or env.youtube_api_key is None:
        pytest.skip("WIKIMEDIA_USER_AGENT or YOUTUBE_API_KEY is not set in .env")
    now = datetime.now(UTC)
    with httpx.Client(headers={"User-Agent": env.wikimedia_user_agent}, timeout=30) as http:
        title = wikimedia.article_title(http, "jet engine")
        end = now.date() - timedelta(days=1)
        views = wikimedia.daily_views(http, title, end - timedelta(days=34), end)
        videos = youtube.recent_popular_videos(
            http, env.youtube_api_key.get_secret_value(), "jet engine", now - timedelta(days=30),
            before_call=lambda units: None,
        )  # fmt: skip

    assert title == "Jet engine"
    assert len(views) == 35 and sum(views) > 1000
    assert videos and videos[0].views >= videos[-1].views
