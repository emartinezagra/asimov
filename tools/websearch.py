"""SearXNG metasearch — self-hosted, so the query never goes straight to a
third party. The LLM never touches the network itself: Python does the
request, and only a compact title/url/snippet per result comes back.
"""
import os
import logging
import requests

logger = logging.getLogger("asimov")

MAX_RESULTS = 5
TITLE_MAX_CHARS = 120
SNIPPET_MAX_CHARS = 200
DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 1  # one small retry on failure, never unbounded
# Always include news engines alongside general ones: it helps queries about
# current events/headlines without hurting other queries (they just add more
# candidate results into the same ranking).
DEFAULT_CATEGORIES = "general,news"


class SearchError(Exception):
    """Raised for anything the caller should treat as "search didn't work":
    not configured, unreachable, timed out, or a malformed response."""


class SearchService:
    def __init__(self, base_url, max_results=MAX_RESULTS, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
        self.base_url = (base_url or "").rstrip("/")
        self.max_results = max_results
        self.timeout = timeout
        self.retries = retries

    def is_configured(self):
        return bool(self.base_url)

    def search(self, query, max_results=None):
        """Returns a normalized list of {"title", "url", "snippet"} dicts
        (possibly empty), or raises SearchError. Never returns raw SearXNG
        JSON — the caller (and eventually the LLM) only ever sees this."""
        if not self.is_configured():
            raise SearchError("not_configured")

        data = None
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                resp = requests.get(
                    f"{self.base_url}/search",
                    params={"q": query, "format": "json", "categories": DEFAULT_CATEGORIES},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                last_error = e
                logger.warning(f"SearXNG search attempt {attempt + 1} failed for {query!r}: {e}")
        else:
            logger.error(f"SearXNG search failed for {query!r} after {self.retries + 1} attempt(s)")
            raise SearchError("service_unavailable") from last_error

        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise SearchError("invalid_response")

        limit = max_results or self.max_results
        return [
            {
                "title": (r.get("title") or "")[:TITLE_MAX_CHARS],
                "url": r.get("url", ""),
                "snippet": (r.get("content") or "")[:SNIPPET_MAX_CHARS],
            }
            for r in data["results"][:limit]
        ]


SEARXNG_URL = os.getenv("SEARXNG_URL", "")
default_service = SearchService(SEARXNG_URL)


def search(query):
    """Kept for actions.py: same {"status": ..., "results": [...]} shape it
    already handles, backed by SearchService underneath."""
    try:
        results = default_service.search(query)
    except SearchError as e:
        if str(e) == "not_configured":
            return {"status": "not_configured"}
        return {"status": "service_error"}
    if not results:
        return {"status": "no_results"}
    return {"status": "ok", "results": results}
