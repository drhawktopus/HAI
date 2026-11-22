r"""Ollama-API-compatible proxy that delegates placement to the scheduler.

Frigate connects here via a static `base_url`, believing it talks to a single
ollama server. This module forwards requests to `scheduler.place("vision")`,
which selects the best available GPU (the failover box, the lead, etc.).

Constraints:
1. Placement logic lives ONLY in the scheduler. This module must not rank hosts,
   maintain health tables, or implement fallback logic.
2. Fail closed: if no host fits, return an error. Never silently downgrade to
   an undersized card (e.g., running a 32B model on an 8 GB card).

Reuses the existing `vision` resource kind (6 GB, non-interactive)."""
import json
import time
import urllib.error
import urllib.request

try:
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse
except Exception:                                    # importable without FastAPI for unit tests
    APIRouter = None

import config
import notes
import scheduler

TIMEOUT = 180          # a VLM describing an image is slow; this is not a chat turn


# `scheduler.place()` is slow (~11s) due to live host probing.
# Cache results briefly to avoid per-request latency penalties,
# with a TTL short enough to detect host state changes promptly.
_PLACE_TTL = float(__import__("os").environ.get("OLLAMA_PROXY_PLACE_TTL", "30"))
_place_cache = {"at": 0.0, "host": None, "why": ""}
_place_lock = __import__("threading").Lock()


def choose_host(exclude=(), force_fresh=False):
    """Return (host, why) for the next VLM request, or (None, reason).

    Delegates to scheduler.place. Results are cached for `_PLACE_TTL` seconds
    and never re-ranked within that window."""
    now = time.time()
    if not force_fresh and not exclude:
        with _place_lock:
            c = _place_cache
            if c["host"] and (now - c["at"]) < _PLACE_TTL:
                return c["host"], c["why"] + " [cached %.0fs]" % (now - c["at"])
    try:
        ranked = scheduler.place("vision", exclude=tuple(exclude))
    except Exception as e:
        return None, "scheduler.place failed (%s: %s)" % (type(e).__name__, str(e)[:120])
    if not ranked:
        return None, "no host currently fits a vision job (all busy, asleep, or too small)"
    host, score, why = ranked[0]
    why = "%s (score %.2f) %s" % (host, score, why)
    if not exclude:
        with _place_lock:
            _place_cache.update({"at": time.time(), "host": host, "why": why})
    return host, why


def _base_url(host):
    """Map scheduler host key to its Ollama base URL.

    Uses the same config values as `app._brain_option` (BRAIN_HOST / HEAVY_HOST /
    FAILOVER_HOST) to ensure a single source of truth; do not introduce separate
    address mappings or fallbacks that may drift from config."""
    if host == scheduler.BRAIN:
        return config.BRAIN_HOST
    if host == scheduler.LEAD_BOX:
        return config.HEAVY_HOST
    if host == scheduler.FAILOVER:
        return config.FAILOVER_HOST
    return None


def forward(path, body, method="POST", exclude=()):
    """Place, then forward verbatim. Returns (status_code, payload_dict).

    The body is passed through UNTOUCHED -- Frigate's images, prompts and options are its business,
    and rewriting them here would make this a second client rather than a router."""
    host, why = choose_host(exclude=exclude)
    if not host:
        notes.log("OLLAMA-PROXY refused %s: %s" % (path, why))
        return 503, {"error": "could not place a vision request: %s" % why}

    base = _base_url(host)
    if not base:
        return 503, {"error": "no ollama base URL configured for placed host %r" % host}
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")

    t0 = time.time()
    import ollama_client
    try:
        with ollama_client.track(base, (body or {}).get("model") or "?", purpose="ollama proxy " + path,
                                 user="system", kind="vlm"), \
                urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        notes.log("OLLAMA-PROXY %s -> %s HTTP %s %s" % (path, host, e.code, body_txt[:120]))
        return e.code, {"error": "upstream %s returned HTTP %s" % (host, e.code), "body": body_txt}
    except Exception as e:
        # Do NOT silently retry on another host; opaque failover obscures root cause.
        notes.log("OLLAMA-PROXY %s -> %s FAILED (%s)" % (path, host, type(e).__name__))
        return 502, {"error": "could not reach %s (%s: %s)" % (host, type(e).__name__, str(e)[:120]),
                     "placed_on": host}

    ms = int((time.time() - t0) * 1000)
    notes.log("OLLAMA-PROXY %s -> %s %sms (%s)" % (path, host, ms, why))
    try:
        out = json.loads(raw.decode("utf-8"))
    except Exception:
        return code, {"error": "upstream %s returned non-JSON" % host}
    if isinstance(out, dict):
        # so a human reading a Frigate description can tell WHICH box produced it
        out.setdefault("_placed_on", host)
        out.setdefault("_placed_ms", ms)
    return code, out


if APIRouter is not None:
    router = APIRouter()

    @router.get("/ollama/api/tags")
    async def _tags():
        code, out = forward("/api/tags", None, method="GET")
        return JSONResponse(status_code=code, content=out)

    @router.post("/ollama/api/show")
    async def _show(request: Request):
        """Frigate calls this at init to probe model capabilities.

        If this endpoint fails or is missing, Frigate disables generative AI
        entirely, even if other endpoints like /api/chat are functional."""
        body = await request.json()
        code, out = forward("/api/show", body)
        return JSONResponse(status_code=code, content=out)

    @router.post("/ollama/api/chat")
    async def _chat(request: Request):
        body = await request.json()
        code, out = forward("/api/chat", body)
        return JSONResponse(status_code=code, content=out)

    @router.post("/ollama/api/generate")
    async def _generate(request: Request):
        body = await request.json()
        code, out = forward("/api/generate", body)
        return JSONResponse(status_code=code, content=out)

    @router.get("/ollama/where")
    async def _where():
        """Diagnostic: which host WOULD take the next vision request, and why."""
        host, why = choose_host()
        return {"host": host, "why": why}
