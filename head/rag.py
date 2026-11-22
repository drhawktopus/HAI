"""BM25 keyword retrieval over one or more named corpus *namespaces*.

A namespace is an isolated knowledge base (its own *.md dirs + BM25 index). This keeps each
audience's grounding separate: homelab docs never bleed into a nursing answer and vice-versa.

Namespaces come from config.RAG_NAMESPACES, e.g.:
  homelab : /docs/Reference, /docs/LLM-Project, /docs/Runbook   (curated homelab library)
  nursing : a nursing user's own corpus (a registry entry)      (isolated per user)

Each namespace globs '*.md' (not rglob), so Reference/Credentials/ stays EXCLUDED — secrets are
only reachable via the gated get_credential tool. search(namespace=None) returns [] (ungrounded).
"""
import json
import os
import re
import time
import pathlib
import datetime
from rank_bm25 import BM25Okapi
try:
    import numpy as np
except Exception:  # pragma: no cover - numpy guard; without it, semantic is disabled
    np = None
import threading

import config
import embeddings
import notes

DEFAULT_NAMESPACE = "homelab"


def _now_ct():
    return datetime.datetime.now(config.LAB_ZONE)


# Strip common words so general questions ("what is a black hole") don't spuriously match docs.
_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in", "on", "at",
    "for", "and", "or", "but", "if", "then", "so", "as", "by", "with", "from", "about", "into", "out",
    "what", "whats", "who", "whos", "when", "where", "why", "how", "which", "do", "does", "did", "done",
    "i", "you", "it", "we", "they", "he", "she", "my", "your", "our", "me", "us", "them", "this", "that",
    "these", "those", "can", "could", "would", "should", "will", "shall", "may", "might", "must",
    "have", "has", "had", "get", "got", "give", "tell", "show", "say", "one", "two", "sentence",
    "please", "thanks", "right", "now", "just", "really", "actually", "okay", "ok", "hey", "hi", "hello",
}


def _qtoks(query):
    return [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in _STOP and len(w) > 1]


class _Namespace:
    """One isolated BM25 corpus with its own change-detector and reload throttle."""

    # Setting any of these publishes a whole new tuple to ensure atomic snapshots for concurrent readers.
    chunks = property(lambda s: s._index[0], lambda s, v: setattr(s, "_index", (v, s._index[1], s._index[2])))
    bm25 = property(lambda s: s._index[1], lambda s, v: setattr(s, "_index", (s._index[0], v, s._index[2])))
    embeddings = property(lambda s: s._index[2], lambda s, v: setattr(s, "_index", (s._index[0], s._index[1], v)))

    def __init__(self, name, dirs):
        self.name = name
        self.dirs = [pathlib.Path(d) for d in dirs]
        self._index = ([], None, None)    # (chunks, bm25, embeddings): published whole by load(), read whole by search()
        self.loaded_at = None
        self.sig = None
        self.last_check = time.monotonic()
        self.load()

    def _load_chunks(self):
        chunks = []
        for base in self.dirs:
            if not base.is_dir():
                continue
            for p in sorted(base.glob("*.md")):          # glob, not rglob -> skips Credentials/
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                # Document title = first H1, else a cleaned filename. Prepended to EVERY chunk so the
                # title's terms are in the BM25 index for each chunk (ranking) AND the model sees which
                # document (which assignment / source) a chunk belongs to (disambiguation) — fixes
                # mid-document chunks losing their parent context when split on sub-headers.
                m = re.search(r"^#\s+(.+)", text, flags=re.M)
                title = m.group(1).strip() if m else p.stem.replace("-", " ").replace("_", " ")
                for part in re.split(r"(?=^#{1,3}\s)", text, flags=re.M):
                    part = part.strip()
                    if len(part) > 30:
                        # Don't double-print the title on the chunk that already is the H1 section.
                        chunk_text = part if part.startswith(f"# {title}") else f"[{title}]\n{part}"
                        chunks.append((p.name, chunk_text))
        return chunks

    def _signature(self):
        """Cheap change-detector: (file count, newest mtime) over this namespace's *.md files."""
        count, newest = 0, 0.0
        for base in self.dirs:
            if not base.is_dir():
                continue
            for p in base.glob("*.md"):
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                count += 1
                if m > newest:
                    newest = m
        return (count, round(newest, 3))

    def load(self):
        """Build the whole new index aside, then publish it in ONE assignment. A search reads the old index or the new one, never a partial state."""
        chunks = self._load_chunks()
        bm25 = BM25Okapi([c[1].lower().split() for c in chunks]) if chunks else None
        # Semantic layer: embed every chunk (cached). None -> BM25-only (embed host down / numpy missing).
        emb = None
        if chunks and np is not None:
            vecs = embeddings.embed_corpus(self.name, [c[1] for c in chunks])
            if vecs is not None:
                try:
                    emb = np.asarray(vecs, dtype=np.float32)
                except Exception:
                    emb = None
        self._index = (chunks, bm25, emb)
        self.loaded_at = _now_ct()
        self.sig = self._signature()
        return len(chunks)

    def maybe_reload(self, aside=False):
        """Reload when a file changed (throttled). `aside`: rebuild on a background thread and return at once - the
        caller's search reads the index as it was (load() publishes the new one whole), so a person's turn never
        waits on a rebuild somebody else's edit caused. One rebuild at a time per namespace. -> True when a reload
        happened (or, aside, was started)."""
        now = time.monotonic()
        if now - self.last_check < config.RAG_RELOAD_CHECK_INTERVAL:
            return False
        self.last_check = now
        try:
            if self._signature() != self.sig:
                if not aside:
                    self.load()
                    return True
                if getattr(self, "_rebuilding", False):
                    return False
                self._rebuilding = True
                threading.Thread(target=self._rebuild_aside, name="rag-rebuild-%s" % self.name, daemon=True).start()
                return True
        except Exception:
            pass
        return False

    def _rebuild_aside(self):
        t0 = time.monotonic()
        try:
            n = self.load()
            notes.log("LIBRARY INDEX %s rebuilt aside: %d chunks in %.1f s (a document changed; the turn that noticed "
                      "read the index as it was)" % (self.name, n, time.monotonic() - t0))
        except Exception as exc:                                         # noqa: BLE001 - the old index keeps serving
            notes.log("LIBRARY INDEX %s rebuild failed (%s: %s) - the previous index keeps serving" % (
                self.name, type(exc).__name__, str(exc)[:160]))
        finally:
            self._rebuilding = False

    def search(self, query, qtoks, k):
        """Hybrid retrieval: fuse the BM25 ranking with a cosine-similarity (embedding) ranking via
        Reciprocal Rank Fusion. Each result carries `score` (raw BM25, for the legacy relevance gate)
        AND `sim` (cosine, or None) so the caller can keep a chunk that's a strong SEMANTIC match even
        when its BM25 score is low — fixing keyword misses where wording differs. With embeddings
        unavailable this collapses to exactly the old BM25 top-k behavior (single-list RRF preserves order)."""
        chunks, bm25, emb = self._index          # one snapshot: a reload in flight never shows a half-built index
        n = len(chunks)
        if n == 0:
            return []
        bm = list(bm25.get_scores(qtoks)) if (bm25 and qtoks) else [0.0] * n

        sims = None
        if emb is not None and query and np is not None:
            qv = embeddings.embed_query(query)
            if qv is not None:
                qv = np.asarray(qv, dtype=np.float32)
                denom = np.linalg.norm(emb, axis=1) * (float(np.linalg.norm(qv)) + 1e-9) + 1e-9
                sims = (emb @ qv) / denom

        rrf = {}
        for rank, i in enumerate(sorted(range(n), key=lambda i: bm[i], reverse=True)):
            if bm[i] > 0:
                rrf[i] = rrf.get(i, 0.0) + 1.0 / (config.RRF_K + rank)
        if sims is not None:
            for rank, i in enumerate(sorted(range(n), key=lambda i: sims[i], reverse=True)):
                if sims[i] > 0:
                    rrf[i] = rrf.get(i, 0.0) + 1.0 / (config.RRF_K + rank)
        if not rrf:
            return []
        top = sorted(rrf, key=lambda i: rrf[i], reverse=True)[:k]
        return [
            {"source": chunks[i][0], "text": chunks[i][1],
             "score": round(float(bm[i]), 2),
             "sim": round(float(sims[i]), 3) if sims is not None else None}
            for i in top
        ]


_NAMESPACES: dict[str, _Namespace] = {}


def _ns(namespace):
    """Return the loaded namespace, building it from config.RAG_NAMESPACES on first use."""
    ns = _NAMESPACES.get(namespace)
    if ns is None:
        dirs = config.RAG_NAMESPACES.get(namespace)
        if dirs is None:
            return None
        ns = _Namespace(namespace, dirs)
        _NAMESPACES[namespace] = ns
    return ns


# Eager-load every configured namespace at import so the first query is warm (as before).
for _name in config.RAG_NAMESPACES:
    try:
        _ns(_name)
    except Exception:
        pass


HIDE_OVERFETCH = 40      # what a search reads past k while a hide list is on, so hidden hits do not leave it short


def hide_file():
    return os.path.join(config.STATE_DIR, "rag-hide.json")


def hidden_words():
    """Reads word-level hide flags from <STATE_DIR>/rag-hide.json.
    The file is read per call to allow runtime toggling without deployment.
    Missing or invalid file = no words hidden.
    The hide list is separate from the library to prevent benchmarking artifacts from mutating source truth."""
    try:
        with open(hide_file(), encoding="utf-8") as f:
            return [w.lower() for w in (json.load(f).get("words") or []) if w]
    except (OSError, ValueError, AttributeError):
        return []


def hidden_tasks():
    """Coding task ids out of sight for now: the same file's {"tasks": [...]}. See coding_client.task."""
    try:
        with open(hide_file(), encoding="utf-8") as f:
            return set(json.load(f).get("tasks") or [])
    except (OSError, ValueError, AttributeError, TypeError):
        return set()


def is_hidden(text, words=None):
    words = hidden_words() if words is None else words
    low = (text or "").lower()
    return any(w in low for w in words)


def without_hidden(text, words=None):
    """A whole document with its hidden paragraphs left out, for a full-text read."""
    words = hidden_words() if words is None else words
    if not words:
        return text
    return "\n\n".join(p for p in re.split(r"\n\s*\n", text or "") if not is_hidden(p, words))


def search(query, k=4, namespace=DEFAULT_NAMESPACE):
    """Retrieve up to k chunks from one namespace. namespace=None/unknown -> [] (ungrounded)."""
    if not namespace:
        return []
    ns = _ns(namespace)
    if not ns:
        return []
    words = hidden_words()
    if not words:
        return ns.search(query, _qtoks(query), k)
    hits = ns.search(query, _qtoks(query), k + HIDE_OVERFETCH)
    return [h for h in hits if not is_hidden(h.get("text"), words) and not is_hidden(h.get("source"), words)][:k]


def configure(namespace, dirs):
    """Create or replace a namespace pointed at explicit dirs, load it, return chunk count.
    Used by tests; also lets callers register a namespace not in config."""
    ns = _Namespace(namespace, dirs)
    _NAMESPACES[namespace] = ns
    return len(ns.chunks)


def reload(namespace=None):
    """Reload one namespace, or all known namespaces (namespace=None). Returns chunk count
    (for that namespace, or the total across all)."""
    if namespace is not None:
        ns = _ns(namespace)
        return ns.load() if ns else 0
    # refresh known namespaces + pick up any newly-configured ones
    for name in config.RAG_NAMESPACES:
        _ns(name)
    return sum(ns.load() for ns in _NAMESPACES.values())


def maybe_reload(namespace=None, aside=False):
    """Auto-reload changed corpora (throttled, fail-soft). One namespace or all. Returns True
    if any reload happened — so editors never have to POST /reload by hand. `aside`: the rebuild runs
    on a background thread (the turn path), True then means one was started."""
    if namespace is not None:
        ns = _ns(namespace)
        return ns.maybe_reload(aside=aside) if ns else False
    return any(ns.maybe_reload(aside=aside) for ns in list(_NAMESPACES.values()))


def count(namespace=DEFAULT_NAMESPACE):
    ns = _NAMESPACES.get(namespace)
    return len(ns.chunks) if ns else 0


def chunks(namespace=DEFAULT_NAMESPACE):
    """(source, text) tuples for a namespace — used by diagnostics and tests."""
    ns = _NAMESPACES.get(namespace)
    return list(ns.chunks) if ns else []


def loaded_at(namespace=DEFAULT_NAMESPACE):
    ns = _NAMESPACES.get(namespace)
    return ns.loaded_at.isoformat() if ns and ns.loaded_at else None
