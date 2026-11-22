"""Phase 8 v2: ground the decomposition planner + critic in the orchestrator's OWN source.

The planner/critic otherwise reason from general knowledge, so a plan to change EXISTING code drifts
to plausible-but-wrong stacks (raw sockets, daemon threads, freezegun) instead of our reality
(FastAPI StreamingResponse, asyncio.to_thread, cancellation.py, unittest). This module retrieves the
most relevant slices of our own code for a commission's objective and formats them for injection into BOTH
the planner draft prompt AND the critic review prompt — so the critic's review catches "this leaf
contradicts the real code" automatically and the refine loop fixes it, with no hand-feeding.

It indexes *.py (chunked by top-level symbol) and ranks with the SAME hybrid BM25+embedding retrieval
rag.py uses (reusing `embeddings`), degrading to BM25 when embeddings are down. It AUTO-DISTINGUISHES
"change existing things" from "greenfield": a greenfield objective shares no symbols/terms with the
code, so retrieval returns nothing above threshold and grounding is simply empty (the planner proceeds
ungrounded, exactly as before). Lazy-built, signature-reloaded, fail-soft — never breaks planning.
"""
from __future__ import annotations

import os
import re
import time

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None
try:
    from rank_bm25 import BM25Okapi
except Exception:  # pragma: no cover
    BM25Okapi = None

import config
import embeddings


_STOP = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are", "be", "it",
         "this", "that", "with", "from", "as", "by", "add", "make", "use", "so", "we", "i"}


def _toks(text):
    return [w for w in re.findall(r"[a-z0-9_]+", (text or "").lower())
            if len(w) > 1 and w not in _STOP]


def _symbol_chunks(name: str, text: str):
    """Split a .py file into top-level symbol chunks (def/class/async def), each headed by
    `# <file> :: <symbol>` so the planner sees which file+symbol the slice came from, and the BM25
    index carries the file/symbol terms. A leading module preamble (imports/docstring) is one chunk."""
    out = []
    parts = re.split(r"(?=^(?:async def |def |class ))", text, flags=re.M)
    for part in parts:
        body = part.strip()
        if len(body) < 40:
            continue
        m = re.match(r"^(?:async def |def |class )(\w+)", body)
        sym = m.group(1) if m else "module"
        header = f"# {name} :: {sym}"
        out.append((name, header + "\n" + body[:config.GROUNDING_CHUNK_MAX_CHARS]))
    return out


class _CodeIndex:
    # views of the ONE published index (rag._Namespace's twin): setting any of them publishes a whole new tuple
    chunks = property(lambda s: s._index[0], lambda s, v: setattr(s, "_index", (v, s._index[1], s._index[2])))
    bm25 = property(lambda s: s._index[1], lambda s, v: setattr(s, "_index", (s._index[0], v, s._index[2])))
    embeddings = property(lambda s: s._index[2], lambda s, v: setattr(s, "_index", (s._index[0], s._index[1], v)))

    def __init__(self, dirs):
        self.dirs = list(dirs or [])
        self._index = ([], None, None)    # (chunks, bm25, embeddings): published whole by load(), read whole by search()
        self.sig = None
        self.last_check = 0.0
        self.load()

    def _files(self):
        for base in self.dirs:
            if not os.path.isdir(base):
                continue
            for fn in sorted(os.listdir(base)):
                if fn.endswith(".py") and fn != "__init__.py":
                    yield os.path.join(base, fn)

    def _signature(self):
        count, newest = 0, 0.0
        for path in self._files():
            try:
                m = os.path.getmtime(path)
            except OSError:
                continue
            count += 1
            newest = max(newest, m)
        return (count, round(newest, 3))

    def load(self):
        chunks = []
        for path in self._files():
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            chunks.extend(_symbol_chunks(os.path.basename(path), text))
        # built aside, published in ONE assignment (rag._Namespace.load's twin: a search mid-reload read embeddings=None)
        bm25 = (BM25Okapi([_toks(c[1]) for c in chunks]) if (BM25Okapi and chunks) else None)
        emb = None
        if chunks and np is not None and embeddings.available():
            vecs = embeddings.embed_corpus(config.GROUNDING_NAMESPACE, [c[1] for c in chunks])
            if vecs is not None:
                try:
                    emb = np.asarray(vecs, dtype=np.float32)
                except Exception:
                    emb = None
        self._index = (chunks, bm25, emb)
        self.sig = self._signature()
        self.last_check = time.monotonic()
        return len(chunks)

    def maybe_reload(self):
        now = time.monotonic()
        if now - self.last_check < getattr(config, "RAG_RELOAD_CHECK_INTERVAL", 30):
            return False
        self.last_check = now
        try:
            if self._signature() != self.sig:
                self.load()
                return True
        except Exception:
            pass
        return False

    def search(self, query, k):
        """Retrieve relevant code chunks, best-first, as [{source, text, score, sim}].

        BM25 keyword overlap is the RECALL GATE: only chunks the query shares terms with are
        candidates, so a greenfield objective (no shared identifiers/terms with the code) yields []
        -- this is what auto-distinguishes "change existing things" from "new task", and it is robust
        to the embedding model's high similarity FLOOR (nomic-embed returns ~0.5 even for unrelated
        text, so a sim threshold cannot make this call). Embeddings, when available, only RE-RANK the
        keyword candidates (precision), never introduce new ones."""
        chunks, bm25, emb = self._index          # one snapshot: a reload in flight never shows a half-built index
        n = len(chunks)
        if n == 0:
            return []
        qtoks = _toks(query)
        bm = list(bm25.get_scores(qtoks)) if (bm25 and qtoks) else [0.0] * n
        cand = [i for i in range(n) if bm[i] > 0]
        if not cand:
            return []
        sims = None
        if emb is not None and np is not None and query:
            qv = embeddings.embed_query(query)
            if qv is not None:
                qv = np.asarray(qv, dtype=np.float32)
                denom = np.linalg.norm(emb, axis=1) * (float(np.linalg.norm(qv)) + 1e-9) + 1e-9
                sims = (emb @ qv) / denom
        rrf = {i: 0.0 for i in cand}
        for rank, i in enumerate(sorted(cand, key=lambda i: bm[i], reverse=True)):
            rrf[i] += 1.0 / (config.RRF_K + rank)
        if sims is not None:
            for rank, i in enumerate(sorted(cand, key=lambda i: sims[i], reverse=True)):
                rrf[i] += 1.0 / (config.RRF_K + rank)
        top = sorted(cand, key=lambda i: rrf[i], reverse=True)[:k]
        return [{"source": chunks[i][0], "text": chunks[i][1],
                 "score": round(float(bm[i]), 2),
                 "sim": round(float(sims[i]), 3) if sims is not None else None}
                for i in top]


_INDEX: _CodeIndex | None = None


def _index():
    global _INDEX
    if _INDEX is None:
        _INDEX = _CodeIndex([config.GROUNDING_SOURCE_DIR])
    else:
        _INDEX.maybe_reload()
    return _INDEX


def configure(dirs):
    """Build/replace the code index at explicit dirs (tests; or to point at a non-default source)."""
    global _INDEX
    _INDEX = _CodeIndex(dirs)
    return len(_INDEX.chunks)


def retrieve(query, k=None):
    """Best-first code chunks relevant to `query`, or [] (greenfield / nothing relevant)."""
    try:
        return _index().search(query, int(k or config.GROUNDING_TOPK))
    except Exception:
        return []


def context_block(query, *, max_chars=None) -> str:
    """A capped, prompt-ready 'relevant existing code' block to inject into planner + critic, or ''
    when nothing relevant was found (so a greenfield commission stays ungrounded)."""
    hits = retrieve(query)
    if not hits:
        return ""
    cap = int(max_chars or config.GROUNDING_MAX_CHARS)
    header = ("Relevant EXISTING code from this codebase — ground every leaf in THIS: match these "
              "real modules, APIs, frameworks, and test style. Do NOT invent mechanisms the code "
              "does not use (e.g. raw sockets, daemon threads, freezegun/pytest) when the code above "
              "shows what it actually uses.")
    parts, used = [header, ""], len(header) + 2
    for h in hits:
        block = h["text"].rstrip()
        if used + len(block) + 2 > cap:
            break
        parts.append(block)
        parts.append("")
        used += len(block) + 2
    return "\n".join(parts).rstrip()[:cap]   # hard cap: never overflow the plan ctx, even on the header


def sources(query) -> list[str]:
    """Distinct source files behind the grounding (for surfacing in the plan/critique trail)."""
    seen, out = set(), []
    for h in retrieve(query):
        s = h["source"]
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
