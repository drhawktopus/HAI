"""Text embeddings via Ollama, for hybrid (semantic) RAG retrieval.

Batch-embeds via `/api/embed` on the box `scheduler.embed_placement` designates (or `config.EMBED_HOSTS` fallback) to distribute load; nomic-embed-text is asymmetric, so doc vs query get different task prefixes.
Returns None on total failure so retrieval falls back to BM25-only. A per-namespace disk cache keyed by sha1(raw text) ensures reloads only embed new/changed chunks."""
import hashlib
import json
import os
import threading
import time

import httpx

import config
import notes

_LOCK = threading.Lock()

# The menu's placement for the next embed, held for PLACEMENT_TTL_S: a turn's two or three embeds (retrieval, tool
# selection) land within seconds of each other, and every input to the weighing - claims, residency, the table - moves on
# the scale of a window's keep-alive (300 s); 30 s is a tenth of that. Each weighing asks every box's residency (a
# sleeping the brain box costs a connect timeout per ask), so per call it would tax every retrieval.
PLACEMENT_TTL_S = 30.0
_PLACED = {"at": None, "order": None, "source": None}   # order: [(url, model, candidate)] or None (no family on the menu)
_PLACED_LOCK = threading.Lock()
_ORDER_NOTED = [None]

# host -> monotonic deadline before which we don't dial it again.
#
# EMBED_HOSTS is [the brain box, the failover box] and the brain box SLEEPS after 30 min idle (UNATTENDSLEEP), which
# is normal and expected — it wakes on demand. But a sleeping host costs EMBED_CONNECT_TIMEOUT on
# EVERY embed call before the cascade reaches the always-on failover, and embeds happen per request.
# Remembering the failure for a short while turns "3 s tax on every retrieval while the primary naps"
# into "3 s once a minute". Deliberately short: a woken host must come back into service by itself.
_COOLDOWN = {}
_COOLDOWN_LOCK = threading.Lock()


def available():
    return config.EMBED_ENABLED and bool(config.EMBED_HOSTS)


def _weighed():
    """The menu's order for the next embed, [(url, model, candidate)] best first, or None when the table has no embed
    family (said once). Held PLACEMENT_TTL_S (see above). Hosts cooling down (just found unreachable) are excluded from
    the weighing so a sleeping box is not asked its residency. Any failure inside placement = None: the fixed list."""
    now = time.monotonic()
    with _PLACED_LOCK:
        if _PLACED["at"] is not None and now - _PLACED["at"] < PLACEMENT_TTL_S:
            return _PLACED["order"]
    try:
        import scheduler
        with _COOLDOWN_LOCK:
            cooling = [h for h, until in _COOLDOWN.items() if until > now]
        urls = {(c.get("url") or "").rstrip("/"): name for name, c in scheduler.CARDS.items()}
        exclude = tuple(urls[h.rstrip("/")] for h in cooling if h.rstrip("/") in urls)
        order = scheduler.embed_placement(exclude=exclude)
    except Exception as exc:                                       # noqa: BLE001 - placement never breaks retrieval
        notes.log("EMBED placement unavailable (%s: %s) - the fixed host list" % (type(exc).__name__, str(exc)[:120]))
        order = None
    with _PLACED_LOCK:
        _PLACED.update(at=now, order=order)
    return order


def _model_for(host):
    """The embed model the menu row that placed `host` names; config.EMBED_MODEL where no row placed it."""
    with _PLACED_LOCK:
        order = _PLACED["order"] or []
    for url, model, _c in order:
        if url.rstrip("/") == host.rstrip("/"):
            return model or config.EMBED_MODEL
    return config.EMBED_MODEL


def _note_order(source, hosts, order):
    """Say once per change where embeds go and why (the weighing's first row's notes), never per call."""
    key = (source, tuple(hosts))
    if _ORDER_NOTED[0] == key:
        return
    _ORDER_NOTED[0] = key
    why = ""
    if order:
        c = order[0][2]
        why = " (%s: %.0f ms, %s J; %s)" % (c.get("box"), 1000 * float(c.get("time_s") or 0),
                                           "%.1f" % c["j"] if c.get("j") is not None else "J unmetered",
                                           "; ".join(c.get("notes") or [])[:160])
    notes.log("EMBED placement by the %s: %s%s" % (source, " > ".join(hosts) or "nobody", why))


def _hosts_to_try():
    now = time.monotonic()
    order = _weighed()
    if order:
        base, source = [u for u, _m, _c in order], "menu"
    else:
        # no embed family on the menu yet (None), or one in which no box passed the rules right now ([]): the fixed list
        base, source = list(config.EMBED_HOSTS), "fixed list" if order is None else "fixed list (no box passed the rules)"
    with _COOLDOWN_LOCK:
        live = [h for h in base if _COOLDOWN.get(h, 0.0) <= now]
    # If EVERY host is cooling down, try them all anyway rather than returning None on a stale
    # marker — a wrong "everything is down" is worse than a slow call.
    live = live or list(base)
    if not order:
        # Deprioritize hosts configured to avoid transients, but never exclude them: if only avoided hosts are available, use them to prevent retrieval loss.
        avoid = {h.rstrip("/") for h in getattr(config, "EMBED_AVOID_HOSTS", [])}
        if avoid and len(live) > 1:
            preferred = [h for h in live if h.rstrip("/") not in avoid]
            deprioritized = [h for h in live if h.rstrip("/") in avoid]
            if preferred:
                live = preferred + deprioritized
    # OCCUPIED hosts are excluded entirely: a concurrent embed would evict a running job or interrupt a factory order.
    held = {h: c for h, c in ((h, _claim_on(h)) for h in live) if c}
    if held:
        _note_held(held)
        live = [h for h in live if h not in held]
    _note_order(source, live, order)
    return live


_HELD_NOTED = {}


def _claim_on(host):
    """Return scheduler.occupied_for(host) to identify the specific claim holding this host,
    or None if the host is not listed by the scheduler (fail-safe: treat unlisted as free).
    This is the single source of truth for availability checks on Kirk-side calls."""
    try:
        import scheduler
        return scheduler.occupied_for(host, _model_for(host))
    except Exception:
        return None


def _note_held(held):
    """Say once per order which host embeds are kept off (not on every request)."""
    for host, c in held.items():
        holder = (c or {}).get("holder") or "?"
        if _HELD_NOTED.get(host) != holder:
            _HELD_NOTED[host] = holder
            notes.log("EMBED skips %s: its card is held by %s" % (host, holder))


def _mark(host, ok):
    with _COOLDOWN_LOCK:
        if ok:
            _COOLDOWN.pop(host, None)
        else:
            _COOLDOWN[host] = time.monotonic() + config.EMBED_HOST_COOLDOWN


# Limit input size to 65,536 characters (4x the model's 2,048 token limit) to prevent tokenizer errors.
EMBED_MAX_CHARS = int(os.getenv("EMBED_MAX_CHARS", "65536"))


def _embed(texts):
    """Embed already-prefixed texts via the first reachable host. Returns list[list[float]] or None."""
    if not texts:
        return []
    cut = sum(1 for t in texts if len(t) > EMBED_MAX_CHARS)
    if cut:
        notes.log("EMBED: %d of %d texts are longer than the model reads (longest %d chars) - sent cut to %d" % (
            cut, len(texts), max(len(t) for t in texts), EMBED_MAX_CHARS))
        texts = [t[:EMBED_MAX_CHARS] for t in texts]
    try:
        timeout = httpx.Timeout(config.EMBED_TIMEOUT, connect=config.EMBED_CONNECT_TIMEOUT)
    except Exception:                # old/stubbed httpx without Timeout -> a plain numeric timeout
        timeout = config.EMBED_TIMEOUT
    import ollama_client                     # lazy: ollama_client -> tools -> ... may import this module
    for host in _hosts_to_try():
        model = _model_for(host)             # the row that placed the box names its model (one derivation)
        payload = {"model": model, "input": texts}
        try:
            with ollama_client.track(host, model, purpose="embed %d texts" % len(texts),
                                     user="system", kind="embed"):
                r = httpx.post(f"{host}/api/embed", json=payload, timeout=timeout)
            r.raise_for_status()
            embs = r.json().get("embeddings")
            if embs and len(embs) == len(texts):
                _mark(host, True)
                return embs
        except Exception as exc:     # A 400 is a request-level rejection, not a node outage: do not mark the host down or other callers lose availability.
            resp = getattr(exc, "response", None)
            bad_request = getattr(resp, "status_code", None) == 400
            if not bad_request:
                _mark(host, False)
            try:
                body = (resp.text or "")[:300] if resp is not None else ""
            except Exception:                                   # noqa: BLE001 - the log line never breaks retrieval
                body = ""
            notes.log("embed host %s %s: %s | model=%s texts=%d longest=%d chars%s" % (
                host, "refused this request (the box stays in use)" if bad_request else "unavailable",
                " ".join(str(exc).split()), model,          # one line: httpx's message carries a line break
                len(texts), max((len(t) for t in texts), default=0), (" | server said: " + body) if body else ""))
    return None


def _key(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _cache_path(namespace):
    return os.path.join(config.EMBED_CACHE_DIR, f"{namespace}.json")


def _load_cache(namespace):
    try:
        with open(_cache_path(namespace), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_cache(namespace, cache):
    try:
        os.makedirs(config.EMBED_CACHE_DIR, exist_ok=True)
        tmp = _cache_path(namespace) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, _cache_path(namespace))
    except OSError as exc:
        notes.log(f"embed cache save failed for {namespace}: {exc}")


def embed_corpus(namespace, texts):
    """Vectors aligned with `texts` (using + updating the namespace's disk cache), or None if embeddings
    are unavailable / incomplete (caller then uses BM25-only)."""
    if not available() or not texts:
        return None
    with _LOCK:
        cache = _load_cache(namespace)
        keys = [_key(t) for t in texts]
        missing = [(i, t) for i, (t, k) in enumerate(zip(texts, keys)) if k not in cache]
        if missing:
            new = _embed([config.EMBED_DOC_PREFIX + t for _, t in missing])
            if new is None:
                return None  # can't complete the corpus -> BM25-only
            for (i, _), vec in zip(missing, new):
                cache[keys[i]] = vec
            _save_cache(namespace, cache)
        vecs = [cache.get(k) for k in keys]
        return None if any(v is None for v in vecs) else vecs


def embed_queries(texts):
    """Vectors for SEVERAL queries in one round-trip, or None.

    Tool selection scores a request twice (as asked, and expanded with the asker's own corpus), and
    two sequential calls would double the latency the caller pays before the model even starts.
    Same prefix and host cascade as embed_query — this is the batch form, not a different path.
    """
    texts = [t for t in (texts or []) if t]
    if not available() or not texts:
        return None
    return _embed([config.EMBED_QUERY_PREFIX + t for t in texts])


def embed_query(text):
    """A single query vector, or None."""
    out = embed_queries([text]) if text else None
    return out[0] if out else None
