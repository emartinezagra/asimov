"""SearXNG metasearch — self-hosted, so the query never goes straight to a
third party. The LLM never touches the network itself: Python does the
request, and only a compact title/url/snippet per result comes back.
"""
import os
import logging
import requests

logger = logging.getLogger("asimov")

SEARXNG_URL = os.getenv("SEARXNG_URL", "").rstrip("/")
MAX_RESULTS = 5
SNIPPET_MAX_CHARS = 200


def search(query):
    if not SEARXNG_URL:
        return {"status": "not_configured"}
    try:
        resp = requests.get(f"{SEARXNG_URL}/search", params={
            "q": query, "format": "json"
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.error(f"SearXNG search failed for '{query}'", exc_info=True)
        return {"status": "service_error"}

    results = data.get("results", [])[:MAX_RESULTS]
    if not results:
        return {"status": "no_results"}

    compact = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("content") or "")[:SNIPPET_MAX_CHARS],
        }
        for r in results
    ]
    return {"status": "ok", "results": compact}
