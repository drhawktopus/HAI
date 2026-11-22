"""Select tools for the current request via semantic retrieval and context signals.

This module avoids keyword matching to handle ambiguity. It combines four signals
to determine tool relevance:

1. SEMANTICS: Cosine similarity between the request and cached tool description
   embeddings (using `nomic-embed-text`).
2. CONVERSATION: Recent turn history, providing context that static schemas lack.
3. PERSONAL CORPUS: User-specific namespaces used to expand query terms and
   ground matching in user-defined meanings.
4. IN-FLIGHT STATE: Current operational state (e.g., open reviews), injected
   by the caller to keep this module pure.

Scoring uses relative LEAD_BOX over the median cosine, not absolute thresholds.
Embedding similarities are compressed (typically 0.36–0.76), making absolute
floors ineffective. The lead discriminates relevant tools from noise.

If the best lead is below `TOOL_SELECT_CONF_LEAD` or signals conflict,
the selector returns an uncertainty flag, prompting clarification rather
than silent failure."""
import json
import threading

try:
    import numpy as np
except Exception:            # pragma: no cover - numpy guard, mirrors rag.py
    np = None

import config
import embeddings
import notes

# Always offered, whatever the scores say. Two jobs: keep the assistant able to answer from the
# corpus at all, and keep the ESCALATION path reachable — a brain that cannot ask for help is the
# failure mode this whole module exists to end.
CORE_TOOL_NAMES = frozenset({
    "search_docs",
    "get_document",
    # "catch_up" must remain if index outages hide it; otherwise assistant can fetch docs it can no longer discover.
    "catch_up",
    # fleet_status must remain in base set: during TRAINING windows the supervisor model is a narrow-context tier (9B) variant that lacks robust fallbacks; removing this tool would leave the agent blind to system health.
    "fleet_status",
    "fleet_remedy",
    "remember",
    "web_search",
    # fetch_url is the read-half of web_search; both must be present to prevent answers derived only from search snippets.
    "fetch_url",
    "confirm_action",
    "escalate_to_mothership",
    "escalate_to_claude",
    "check_host",
    # A file is a hard fact about the turn; without this tool, the model cannot perceive the upload.
    "look_at_upload",
})

# The heavy tier cannot escalate to itself. Keep its existing, permission-filtered
# workspace entry reachable when present in the caller's pool, even when
# retrieval misses a coding request. A successful open loads the coding set.
CAPABILITY_ENTRY_NAMES = frozenset({"coding_workspace", "host_admin_list"})

# Persona-specific tools always offered regardless of relevance scoring. Nursing's second perspective must not depend on request keywords matching the tool name.
PERSONA_CORE = {"nursing": frozenset({"medical_review"})}

# PURPOSE_BUILT sets (e.g., coding/survey) bypass relevance narrowing. Named by owner, not by tool count, to avoid arbitrary size limits.
_PURPOSE_BUILT = []
# The GENERAL pools (HEAVY_TOOLS, BRAIN_TOOLS), registered by tools.py: only a list drawn from one of these is narrowed.
# A list from anywhere else (a caller's own hand-picked tools) passes untouched - failing toward every tool the caller
# chose, never toward a missing one.
_GENERAL = []


def _names_of(tools_list):
    return frozenset(n for n in (_tool_name(t) for t in tools_list) if n)


def register_set(tools_list):
    """Declare a purpose-built tool set: a caller passing (a subset of) it gets it whole, never narrowed."""
    names = _names_of(tools_list)
    if names and names not in _PURPOSE_BUILT:
        _PURPOSE_BUILT.append(names)
    return names


def register_pool(tools_list):
    """Declare a GENERAL pool: a list drawn from it is narrowed to the request's relevant tools."""
    names = _names_of(tools_list)
    if names and names not in _GENERAL:
        _GENERAL.append(names)
    return names


def _tool_name(tool):
    return ((tool.get("function") or {}).get("name") or "") if isinstance(tool, dict) else ""


_MATRIX_LOCK = threading.Lock()
_MATRIX = {"key": None, "names": (), "vecs": None}


def _approx_tokens(obj):
    """Schema size in tokens, the same ~4-chars-per-token estimate used everywhere else here."""
    try:
        return len(json.dumps(obj)) // 4
    except (TypeError, ValueError):
        return 0


def _tool_doc(tool):
    """The text that REPRESENTS a tool for retrieval: its name, description and parameter names.

    Enum VALUES are deliberately excluded. `_ground_dataset_sets` injects the live dataset names into
    the `sets` enum per request, so including them would change this text on every call and bust the
    embedding cache — paying a round-trip to re-embed a tool whose meaning has not changed.
    """
    fn = (tool.get("function") or {}) if isinstance(tool, dict) else {}
    name = fn.get("name") or ""
    desc = (fn.get("description") or "")[:600]
    props = ((fn.get("parameters") or {}).get("properties") or {})
    params = ", ".join(list(props.keys())[:12])
    spoken = name.replace("_", " ")
    return "%s. %s Parameters: %s" % (spoken, desc, params) if params else "%s. %s" % (spoken, desc)


def _matrix(tools_list):
    """(names, vectors) for the tool corpus, or None when embeddings are unavailable.

    Cached in memory on the exact doc set, so a steady toolbox costs zero embed calls per request;
    `embed_corpus` also keeps a disk cache keyed by sha1 of each doc, so a changed DESCRIPTION
    re-embeds one tool rather than all 86.
    """
    pairs = [(t["function"]["name"], _tool_doc(t)) for t in tools_list
             if isinstance(t, dict) and (t.get("function") or {}).get("name")]
    if not pairs:
        return None
    key = hash(tuple(d for _, d in pairs))
    with _MATRIX_LOCK:
        if _MATRIX["key"] == key and _MATRIX["vecs"] is not None:
            return _MATRIX["names"], _MATRIX["vecs"]
    vecs = embeddings.embed_corpus("tools", [d for _, d in pairs])
    if vecs is None:
        return None
    names = tuple(n for n, _ in pairs)
    if np is not None:
        try:
            vecs = np.asarray(vecs, dtype=np.float32)
        except Exception:
            pass
    with _MATRIX_LOCK:
        _MATRIX.update(key=key, names=names, vecs=vecs)
    return names, vecs


def _cohesion(vecs, indices):
    """
    Mean pairwise cosine among the chosen tools.

    Serves as an uncertainty measure: low cohesion indicates disagreement among
    selected tools (doubt), whereas high cohesion indicates agreement even if
    individual tool scores are low.
    """
    if len(indices) < 2:
        return 1.0                    # a single clear pick is not a disagreement
    rows = [vecs[i] for i in indices]
    total, pairs = 0.0, 0
    for a in range(len(rows)):
        for b in range(a + 1, len(rows)):
            ra, rb = rows[a], rows[b]
            dot = float(sum(x * y for x, y in zip(ra, rb)))
            na = float(sum(x * x for x in ra)) ** 0.5 + 1e-9
            nb = float(sum(x * x for x in rb)) ** 0.5 + 1e-9
            total += dot / (na * nb)
            pairs += 1
    return total / pairs if pairs else 1.0


def _cosines(qvec, vecs):
    """Cosine of one query against every tool vector. numpy when present, plain Python otherwise."""
    if np is not None and isinstance(vecs, np.ndarray):
        q = np.asarray(qvec, dtype=np.float32)
        denom = np.linalg.norm(vecs, axis=1) * (float(np.linalg.norm(q)) + 1e-9) + 1e-9
        return list((vecs @ q) / denom)
    out = []
    qn = sum(x * x for x in qvec) ** 0.5 + 1e-9
    for row in vecs:
        dot = sum(a * b for a, b in zip(row, qvec))
        out.append(dot / ((sum(x * x for x in row) ** 0.5 + 1e-9) * qn))
    return out


def corpus_context(user, text, persona=None):
    """(excerpt, pull) from the ASKER's own corpus — signal 2.

    `pull` is the best cosine similarity any of their chunks had with the request, i.e. how strongly
    this question is grounded in that person's documents. Never raises: retrieval problems must
    degrade tool selection, never break a chat.
    """
    try:
        import rag
        namespaces = config.grounding_namespaces(config.namespace_for_user(user), persona, user)
    except Exception:
        return "", 0.0
    excerpts, pull = [], 0.0
    for ns in namespaces or []:
        try:
            hits = rag.search(text, k=config.TOOL_SELECT_CORPUS_K, namespace=ns)
        except Exception:
            continue
        for hit in hits or []:
            sim = hit.get("sim")
            if sim is not None:
                pull = max(pull, float(sim))
            excerpts.append(hit.get("text") or "")
    joined = " ".join(excerpts)[:config.TOOL_SELECT_CORPUS_CHARS]
    return joined, pull


def recent_context(messages, limit=None, chars=None):
    """The conversation so far, as retrieval context — everything but the latest user message.

    A person does not disambiguate "I'm done with those" from a state file; they disambiguate it from
    what was just being discussed. The MODEL is handed that history and the selector was not, which
    is an asymmetry with no justification: the same words mean different things in different
    conversations, and history covers domains that have no in-flight state to consult at all.

    Tool results are excluded deliberately — they are frequently large, are DATA rather than intent,
    and a 4,000-character JSON blob would swamp the sentence that actually carries the meaning.
    """
    limit = config.TOOL_SELECT_HISTORY_TURNS if limit is None else limit
    chars = config.TOOL_SELECT_HISTORY_CHARS if chars is None else chars
    turns, seen_last_user = [], False
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if m.get("role") == "user" and not seen_last_user:
            seen_last_user = True     # the latest user message is the query itself, not its context
            continue
        turns.append(content.strip())
        if len(turns) >= limit:
            break
    return " ".join(reversed(turns))[-chars:] if turns else ""


def select(tools_list, user, text, num_ctx, persona=None, state_tools=None, corpus=None,
           history="", info=None):
    """Pick the tools this request could plausibly need. Returns (tools, note). `info` (optional) receives the selector's verdict: {"unsure": picks are weak/bunched, "barely": nothing scored confidently, "ranked": non-CORE picks strongest first, "raw_top"/"raw_sure": strongest tool on this turn alone and whether it clears the confidence bar}.

    `note` is None when nothing needs saying; otherwise it is a short system message telling the model the toolbox was narrowed (so it does not claim it cannot do something it was simply not handed) and asking it to clarify when signals are weak.

    Registered purpose-built sets that fit the budget pass through unchanged. General pools are always narrowed: the model sees sets, never the full raw tool list. There is no hard tool-count cap; the only ceiling is the context budget."""
    if not config.TOOL_SELECT_ENABLED or not tools_list:
        return tools_list, None

    budget = int((num_ctx or config.NUM_CTX) * config.TOOL_SELECT_BUDGET_FRAC)
    total = _approx_tokens(tools_list)
    names_in = _names_of(tools_list)
    general = any(names_in <= s for s in _GENERAL) and not any(names_in <= s for s in _PURPOSE_BUILT)
    if total <= budget and not general:
        return tools_list, None      # a purpose-built set or a caller's own list that fits: every tool it holds

    def _name(tool):
        return ((tool.get("function") or {}).get("name") or "") if isinstance(tool, dict) else ""

    core_names = CORE_TOOL_NAMES | CAPABILITY_ENTRY_NAMES | frozenset(PERSONA_CORE.get(persona, ()))
    core = [t for t in tools_list if _name(t) in core_names]
    state_tools = set(state_tools or ())

    matrix = _matrix(tools_list)
    qvecs = None
    excerpt, pull = ("", 0.0)
    if matrix is not None and text:
        if corpus is None:
            excerpt, pull = corpus_context(user, text, persona)
        else:
            excerpt, pull = corpus
        # Three readings of the same request, embedded in ONE round-trip: what was asked, what the
        # conversation is about, and what these words mean in this person's own documents.
        sources = [("raw", text)]
        if history:
            sources.append(("history", history + " " + text))
        if excerpt:
            sources.append(("corpus", text + " " + excerpt))
        qvecs = embeddings.embed_queries([q for _, q in sources])

    if matrix is None or not qvecs:
        # Embeddings unreachable: relevance cannot be judged. Where the whole pool FITS the window (the 3.8),
        # hand over everything - an outage must never strip a capable model of a necessary tool. Where it
        # does not fit (the small brains, whose window it would saturate), fall back to CORE - which still
        # contains escalate_to_mothership: a degraded brain that can hand off beats a saturated one.
        if info is not None:
            # Flag the outage to the caller: this list is not a relevance-ranked pick. Sticky sessions must respect this flag to prevent tool bloat.
            info["outage"] = True
        if total <= budget:
            notes.log("tool selection: embeddings unavailable, the whole pool fits - all %d tools" % len(tools_list))
            return tools_list, None
        notes.log("tool selection: embeddings unavailable, falling back to CORE (%d of %d tools)"
                  % (len(core), len(tools_list)))
        return (core or tools_list), (
            "[TOOL SELECTION] Only core tools are loaded (the tool index is unavailable). If this "
            "request needs a capability you were not given, say so or escalate — do not claim it is "
            "impossible.")

    names, vecs = matrix
    # Score on the LEAD_BOX over each query's OWN median, never on raw cosine. nomic-embed-text is
    # compressed — measured across 14 real requests, all 86 tools scored between 0.36 and 0.76 with
    # the median near 0.50 — so an absolute floor either admits everything or rejects correct tools.
    # The lead is scale-free, and it is per-query so the raw and corpus-expanded readings compare.
    leads = []
    for qv in qvecs:
        sims = [float(s) for s in _cosines(qv, vecs)]
        mid = sorted(sims)[len(sims) // 2]
        leads.append([s - mid for s in sims])
    # BLEND the readings, do not take the max. A max lets context only ADD tools, never redirect away
    # from them — which fails the case these signals exist for: "review the candidates" from someone
    # whose corpus is full of hiring notes is about their DOCUMENTS, not the image review. The corpus
    # weight rises with how strongly their library actually answered, so a weak retrieval nudges and
    # a strong one steers.
    weights = []
    for kind, _ in sources:
        if kind == "raw":
            weights.append(1.0)
        elif kind == "history":
            weights.append(config.TOOL_SELECT_HISTORY_WEIGHT)
        else:
            weights.append(config.TOOL_SELECT_CORPUS_WEIGHT
                           if pull >= config.EMBED_MIN_SIM
                           else config.TOOL_SELECT_CORPUS_WEIGHT / 2.0)
    total_w = sum(weights) or 1.0
    scores = {}
    for i, name in enumerate(names):
        score = sum(lead[i] * w for lead, w in zip(leads, weights)) / total_w
        if name in state_tools:
            # State is a FACT, so it earns a boost — but an additive one, so it cannot override a
            # confident read elsewhere ("restart the containers" is not hijacked by an open review).
            score += config.TOOL_SELECT_STATE_BOOST
        scores[name] = score

    by_name = {_name(t): t for t in tools_list if _name(t)}
    picked, used = list(core), _approx_tokens(core)
    ranked = sorted((n for n in scores if n not in core_names),
                    key=lambda n: scores[n], reverse=True)
    best = max((scores[n] for n in ranked), default=0.0)
    # Two floors, both required: an absolute lead (is anything actually relevant?) and a share of the
    # TOP lead (a confident request gets a tight box rather than everything above the waterline).
    floor = max(config.TOOL_SELECT_MIN_LEAD, best * config.TOOL_SELECT_TOP_FRAC)
    kept = []
    for name in ranked:                # every tool that clears the relevance floors - no count cap
        if scores[name] < floor:
            break                      # ranked desc: the first miss ends it
        size = _approx_tokens(by_name[name])
        if used + size > budget:
            continue                   # a big schema is skipped, a smaller one may still fit
        picked.append(by_name[name])
        kept.append(name)
        used += size
    note = ("[TOOL SELECTION] You were given the tools relevant to this request, not the full set. "
            "If you need a capability you were not handed, say so or escalate rather than claiming "
            "it cannot be done.")
    # Unsure in either of two ways: nothing scored confidently, or the person's own documents AND a
    # live in-flight review both lay claim to the same words. Say so; a coin flip is the wrong answer.
    index = {n: i for i, n in enumerate(names)}
    cohesion = _cohesion(vecs, [index[n] for n in kept[:5] if n in index])
    # Two ways to be unsure: the picks disagree with each other, or the person's own documents AND a
    # live in-flight review both lay claim to the same words. Either way, say so — a coin flip is the
    # wrong answer, and INVARIANTS is explicit that ambiguity resolves to asking a human.
    conflicted = bool(state_tools) and pull >= config.EMBED_MIN_SIM
    # A lone pick has nothing to disagree with, so cohesion reads 1.0 — "confident" — even when that
    # pick barely cleared the floor. The lead still has one job: catching that degenerate case.
    barely = best < config.TOOL_SELECT_MIN_LEAD * 2
    unsure = bool(kept and (cohesion < config.TOOL_SELECT_COHESION or barely or conflicted))
    if info is not None:
        # the RAW reading alone (what this turn asked, before the conversation and the corpus blend in): a confident raw
        # pick under a weak blend is a turn that changed the subject
        raw = {n: leads[0][i] for i, n in enumerate(names) if n not in core_names and n in by_name}
        raw_top = max(raw, key=raw.get) if raw else None
        info.update(unsure=unsure, barely=bool(kept and barely), ranked=list(kept), raw_top=raw_top,
                    raw_sure=bool(raw_top and raw[raw_top] >= config.TOOL_SELECT_MIN_LEAD * 2),
                    # the ALWAYS-OFFERED set, stated so ollama_client.sticky_tools keeps it in a held block (one
                    # derivation: this is the set picked above, never a second list kept there)
                    core=[_name(t) for t in core])
    if unsure:
        note += (" The best matches disagree with each other (%s), so this may not be what the user "
                 "means. If the conversation does not make it clear, ASK before calling a tool."
                 % ", ".join(kept[:3]))
    notes.log("tool selection: %d/%d tools, ~%d/%d tok (lead=%.2f cohesion=%.2f corpus_pull=%.2f "
              "state=%s) for %r" % (len(picked), len(tools_list), used, budget, best, cohesion,
                                    pull, sorted(state_tools) or "-", (text or "")[:60]))
    return picked, note
