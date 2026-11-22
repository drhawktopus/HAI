"""Web search via self-hosted SearXNG."""
import threading
import time

import httpx
import config

# Minimum seconds between outbound searches, process-wide.
# Prevents rate-limit/CAPTCHA suspension of upstream engines (brave, duckduckgo,
# startpage, google-cse) by throttling burst traffic. Tests set this to 0.
MIN_INTERVAL_S = 2.5

_pace_lock = threading.Lock()
_last_search_at = 0.0


def _pace() -> None:
    """Block until at least MIN_INTERVAL_S has passed since the previous search (process-wide)."""
    global _last_search_at
    with _pace_lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_search_at)
        if wait > 0:
            time.sleep(wait)
        _last_search_at = time.monotonic()


def search(query: str, max_results: int = 5) -> str:
    """Query SearXNG and return a compact summary of the top results."""
    try:
        _pace()
        r = httpx.get(
            f"{config.SEARXNG_URL}/search",
            params={"q": query, "format": "json"},
            timeout=8.0,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])[:max_results]
        if not results:
            # Empty + suspended engines is a THROTTLE, not an answer. Reporting it as "no results"
            # converts a fetch failure into false negative evidence (the bulk-API-pull trap) — a
            # research pass would conclude "nothing exists" about topics the engines never searched.
            unresponsive = data.get("unresponsive_engines") or []
            if unresponsive:
                engines = "; ".join(
                    f"{e[0]}: {e[1]}" if isinstance(e, (list, tuple)) and len(e) > 1 else str(e)
                    for e in unresponsive
                )
                return (
                    f"Web search backend is THROTTLED right now (engines unavailable — {engines}). "
                    f"Results for '{query}' are UNAVAILABLE, not absent: do NOT treat this as "
                    "evidence that nothing exists. Retry in a few minutes."
                )
            return f"Web search found no results for '{query}'."
        lines = []
        for i, res in enumerate(results, 1):
            title = res.get("title", "(no title)")
            url = res.get("url", "(no url)")
            snippet = (res.get("content") or "")[:200]
            lines.append(f"{i}. {title}\n   URL: {url}")
            if snippet:
                lines.append(f"   {snippet}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Web search failed: {exc}"
