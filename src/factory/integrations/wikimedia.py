"""Wikipedia reference text for scripts (INTEGRATIONS.md §3). Official public APIs only.

Wikimedia requires a descriptive User-Agent with contact details (WIKIMEDIA_USER_AGENT).
Lookups are best effort: any failure returns None and the script is written without a reference.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx

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
