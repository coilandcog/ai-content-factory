"""Wikipedia reference text for scripts and pageviews for discover (INTEGRATIONS.md §3).
Official public APIs only.

Wikimedia requires a descriptive User-Agent with contact details (WIKIMEDIA_USER_AGENT).
The script reference is best effort: any failure returns None and the script is written without
a reference. Pageviews use the shared retry rules and raise on failure.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote

import httpx

from factory.integrations.http_json import get_json

log = logging.getLogger("factory.wikimedia")

SEARCH_URL = "https://en.wikipedia.org/w/api.php"
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"


@dataclass(frozen=True)
class WikiSummary:
    title: str
    extract: str  # the article's lead section as plain text
    url: str


def page_summary(
    http: httpx.Client, topic: str, *, before_call: Callable[[], None] | None = None
) -> WikiSummary | None:
    """Find the best-matching English Wikipedia article for `topic` and return its summary.

    Two calls: a title search (so "cvt gearbox" finds "Continuously variable transmission"),
    then the summary. Disambiguation pages count as not found.
    """
    check = before_call or (lambda: None)
    try:
        check()
        found = http.get(
            SEARCH_URL,
            params={"action": "opensearch", "search": topic, "limit": 1, "namespace": 0},
        )
        found.raise_for_status()
        titles = found.json()[1]
        if not titles:
            log.warning("no Wikipedia article found for %r", topic, extra={"event": "wiki_missing"})
            return None
        check()
        page = http.get(SUMMARY_URL.format(title=quote(titles[0].replace(" ", "_"), safe="")))
        page.raise_for_status()
        data = page.json()
    except (httpx.HTTPError, ValueError, IndexError, KeyError) as exc:
        log.warning("Wikipedia lookup for %r failed: %s", topic, exc, extra={"event": "wiki_error"})
        return None
    if data.get("type") != "standard" or not data.get("extract"):
        log.warning(
            "Wikipedia page for %r is not a plain article", topic, extra={"event": "wiki_skip"}
        )
        return None
    title = data.get("title") or titles[0]
    url = data.get("content_urls", {}).get("desktop", {}).get("page")
    return WikiSummary(
        title=title,
        extract=data["extract"],
        url=url or f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
    )


# --- pageviews (discover, ROADMAP 1.14) ------------------------------------------------------

PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
    "{title}/daily/{start}/{end}"
)


def article_title(
    http: httpx.Client,
    topic: str,
    *,
    override: str | None = None,
    before_call: Callable[[], None] | None = None,
    retry_wait_s: float = 2,
) -> str | None:
    """The English Wikipedia article for `topic`, or None. With `override` (from the channel
    YAML) that article is used; otherwise the best full-text search hit. Redirects are followed
    (pageviews only count the real article) and disambiguation pages are skipped."""
    params: dict[str, object] = {
        "action": "query", "prop": "pageprops", "ppprop": "disambiguation", "redirects": 1,
        "format": "json", "formatversion": 2,
    }  # fmt: skip
    if override:
        params["titles"] = override
    else:
        params |= {"generator": "search", "gsrsearch": topic, "gsrlimit": 5}
    data = get_json(http, SEARCH_URL, service="Wikipedia search", params=params,
                    before_call=before_call, retry_wait_s=retry_wait_s)  # fmt: skip
    pages = sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
    for page in pages:
        if page.get("missing") or "disambiguation" in page.get("pageprops", {}):
            continue
        return page["title"]
    return None


def daily_views(
    http: httpx.Client,
    title: str,
    start: date,
    end: date,
    *,
    before_call: Callable[[], None] | None = None,
    retry_wait_s: float = 2,
) -> list[int]:
    """Human (not bot) views of the article for every day from `start` to `end`, oldest first.
    Days the API leaves out had no views and count as 0."""
    data = get_json(
        http,
        PAGEVIEWS_URL.format(
            title=quote(title.replace(" ", "_"), safe=""),
            start=start.strftime("%Y%m%d"),
            end=end.strftime("%Y%m%d"),
        ),
        service="Wikimedia pageviews",
        before_call=before_call,
        retry_wait_s=retry_wait_s,
    )
    by_day = {item["timestamp"][:8]: int(item["views"]) for item in data.get("items", [])}
    days = (end - start).days + 1
    return [by_day.get((start + timedelta(days=i)).strftime("%Y%m%d"), 0) for i in range(days)]
