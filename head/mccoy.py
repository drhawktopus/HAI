"""McCoy triage advisor. The model proposes a tier/family/kind; code decides based on confidence.

Routing: Trusts the tier if confidence >= MCCOY_TIER_THRESHOLD (and family/kind if >= MCCOY_THRESHOLD);
otherwise it escalates (shadow mode only).

Constraint: The question set in `mccoy_questions.json` is pinned to the specific model version and prompt
format used to tune the thresholds. Rewording questions invalidates the thresholds."""
import datetime
import json
import os
import threading
import time

import config
import httpx
import ollama_client

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "mccoy_questions.json"), encoding="utf-8") as _f:
    QUESTIONS = json.load(_f)
TIERS = tuple(QUESTIONS["tier"]["criteria"])


class AdvisorUnplaced(RuntimeError):
    """No card can take the advisor right now (every one busy, lost, or holding a live HAI job's model)."""


def advisor_host(lend=False, only=()):
    """Resolve the host for an advisor call. Tries the home failover box first, then any available card.\n    `lend`: Allow borrowing from Kirk's idle chat window.\n    `only`: Restrict selection to specific pinned boxes (e.g., replays).\n    Returns (host, ollama_url, reason). Raises AdvisorUnplaced if no host is available."""
    import scheduler
    for host, _score, why in scheduler.place("advisor", lend=lend, only=tuple(only)):
        url = (scheduler.CARDS.get(host) or {}).get("url")
        if url:
            return host, url, why
    raise AdvisorUnplaced("no card can take the advisor right now")


def _borrowed(host, lend):
    """The card McCoy borrows (chat_window.lend: the window saves its conversation first) when Spock placed this call
    on Kirk's own idle window, else nothing to borrow. A window that cannot lend after all = unplaced."""
    import contextlib
    import chat_window
    w = chat_window.lendable(host) if lend else None
    if w is None:
        return contextlib.nullcontext()

    @contextlib.contextmanager
    def borrowed():
        try:
            with chat_window.lend(w, "mccoy", wait_s=config.MCCOY_TIMEOUT):
                yield
        except chat_window.CardNotLent as e:
            raise AdvisorUnplaced(str(e))
    return borrowed()


def _ask(text, questions, purpose, timeout=None, lend=False, only=()):
    """THE one McCoy call, whoever asks (Kirk's triage, Tolkien's questions): placed by Spock (kind `advisor`), kept
    loaded on a two-model box, registered in the in-flight table, the silence-watched transport. -> (the systemone
    response, host, where, t0). Raises on failure; callers that must never raise wrap it."""
    host, url, where = advisor_host(lend=lend, only=only)
    body = {"model": config.MCCOY_MODEL, "state": text, "questions": questions}
    if host in config.MCCOY_RESIDENT_HOSTS:
        # stays loaded on a two-model box (config.MCCOY_RESIDENT_HOSTS); 10-23: every call there had reloaded him
        body["keep_alive"] = -1
    t0 = time.time()
    # kind "advisor" prevents this call from marking the host LIVE (advisor yields to LIVE); required because this call originates from McCoy's own host
    with _borrowed(host, lend), \
            ollama_client.track(url, config.MCCOY_MODEL, purpose=purpose, user="system", kind="advisor"):
        r = ollama_client.post_supervised(url.rstrip("/") + "/v1/systemone",
                                          json=body, timeout=timeout or config.MCCOY_TIMEOUT)
        r.raise_for_status()
        return r.json(), host, where, t0


# Shadow mode: advice is logged for comparison but does not affect routing. Promote to active only after establishing a measured threshold (#35).
with open(os.path.join(_HERE, "tolkien_questions.json"), encoding="utf-8") as _f:
    TOLKIEN_QUESTIONS = json.load(_f)


def advise_tolkien(text, timeout=None, lend=False):
    """McCoy's advice on one piece of Tolkien's work: {question: {"choice", "confidence"}}, plus wall_s / host. Raises
    on failure (the door returns the error; Tolkien's shadow never waits on it)."""
    resp, host, where, t0 = _ask(text, TOLKIEN_QUESTIONS, "mccoy advises tolkien", timeout=timeout, lend=lend)
    ans = resp.get("answers") or {}
    out = {k: {"choice": (ans.get(k) or {}).get("choice"), "confidence": (ans.get(k) or {}).get("confidence")}
           for k in TOLKIEN_QUESTIONS}
    bad = [k for k, v in out.items() if v["choice"] not in TOLKIEN_QUESTIONS[k]["criteria"]]
    if bad:
        raise ValueError("systemone answered no valid choice for %s: %s" % (", ".join(bad), json.dumps(resp)[:200]))
    return {"advice": out, "wall_s": round(time.time() - t0, 3), "host": host, "placed": where,
            "model": config.MCCOY_MODEL, "mode": "shadow"}


def triage(text, timeout=None, lend=False, only=(), questions=None):
    """One systemone call: {tier, family, kind, depth, confidence{...}, decided, tier_if_used, wall_s}.
    Raises on failure; shadow() is the caller that must never raise.

    `lend=True`: borrows Kirk's idle chat window card if no card is free (conversation is saved and restored).
    Shadow turns never lend: they are already in flight and cannot afford the reload cost."""
    # questions: A custom prompt for the triage measurement. If provided, it is used as the `text` argument to _ask instead of the standard QUESTIONS constant. Note that this path also registers in the in-flight table for GPU accounting, just like standard live turns.
    resp, host, where, t0 = _ask(text, questions or QUESTIONS, "mccoy triage", timeout=timeout, lend=lend, only=only)
    ans = resp.get("answers") or {}
    pick = {k: (ans.get(k) or {}).get("choice") for k in ("tier", "family", "kind", "depth")}
    conf = {k: (ans.get(k) or {}).get("confidence") for k in ("tier", "family", "kind", "depth")}
    if pick["tier"] not in TIERS:
        raise ValueError("systemone answered no valid tier: %s" % json.dumps(resp)[:200])
    decided = conf["tier"] is not None and conf["tier"] >= config.MCCOY_TIER_THRESHOLD   # its own line (Phase 8 tuning)
    return dict(pick, confidence=conf, decided=decided, tier_if_used=pick["tier"] if decided else "lead",
                wall_s=round(time.time() - t0, 3), host=host, placed=where)


def _log(row):
    try:
        os.makedirs(config.MCCOY_LOG_DIR, exist_ok=True)
    except Exception:                                                  # noqa: BLE001 - fail-soft, never create on read-only FS
        return
    path = os.path.join(config.MCCOY_LOG_DIR, datetime.date.today().strftime("%Y-%m") + ".jsonl")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                                                  # noqa: BLE001
        pass


def _shadow_worker(text, user, routing, chat_id):
    row = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "user": user, "routing": routing,
           "chat_id": chat_id, "request": text[:config.MCCOY_EXCERPT], "mode": "shadow",
           "model": config.MCCOY_MODEL, "threshold": config.MCCOY_TIER_THRESHOLD}
    try:
        row.update(triage(text))
    except AdvisorUnplaced as e:
        # not an error: the row keeps the request, so a replay order can triage it later
        row["deferred"] = str(e)
    except Exception as e:                                          # noqa: BLE001 - a shadow must never surface
        row["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    try:
        _log(row)
    except Exception:                                               # noqa: BLE001
        pass


def shadow(text, user, routing="auto", chat_id=None):
    """Tag one Auto turn in the background; returns at once, never raises, changes nothing about the turn."""
    try:
        if config.MCCOY_MODE != "shadow" or routing != "auto" or config.is_test_user(user) or not (text or "").strip():
            return None
        t = threading.Thread(target=_shadow_worker, args=(text, user, routing, chat_id), name="mccoy-shadow",
                             daemon=True)
        t.start()
        return t
    except Exception:                                               # noqa: BLE001
        return None


# --- ACTIVE: McCoy's verdict serves the turn (HAI-Agent-Surface-Design build step 4) -------------------------------
def active_for(user):
    """McCoy decides Auto turns for this user. Uses config.MCCOY_ACTIVE_USERS; "*" enables for all, excluding ghost test users."""
    users = config.MCCOY_ACTIVE_USERS
    return bool(user) and ("*" in users or user in users) and not config.is_test_user(user)


BELOW_LEAD = ("8gb", "fellowship", "tool")


def route(q):
    """The lane string of verdict(q) - "lead", "below", or None (see verdict)."""
    v = verdict(q)
    return v["lane"] if v else None


def verdict(q):
    """Route decision for an ACTIVE user's Auto turn.

    Returns a dict with keys: lane ("below" or "lead"), tier, decided, kind, confidence.
    - lane="below": High confidence (>= MCCOY_TIER_THRESHOLD) that the request is strictly below Lead (e.g., 8GB, fellowship, tool).
    - lane="lead": Default for all other cases (Lead requests, uncertain, no card, errors).
    - None: Not McCoy's turn (inactive user, special route, or empty text). Caller retains current route.

    Behavior:
    - Logs one row (mode="active").
    - Never raises."""
    try:
        user = getattr(q, "user", "") or ""
        text = (getattr(q, "text", "") or "").strip()
        if config.MCCOY_MODE == "off" or not active_for(user) or not text or special_route(text, q):
            return None
        row = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "user": user,
               "routing": getattr(q, "routing", "auto"), "chat_id": getattr(q, "chat_id", None),
               "request": text[:config.MCCOY_EXCERPT], "mode": "active", "model": config.MCCOY_MODEL,
               "threshold": config.MCCOY_TIER_THRESHOLD}
        try:
            row.update(triage(text, lend=True))
        except AdvisorUnplaced as e:
            row["deferred"] = str(e)
        except Exception as e:                                      # noqa: BLE001
            row["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        row["lane"] = "below" if (row.get("decided") and row.get("tier") in BELOW_LEAD) else "lead"
        _log(row)
        return {"lane": row["lane"], "tier": row.get("tier"), "decided": bool(row.get("decided")),
                "kind": row.get("kind"), "family": row.get("family"), "depth": row.get("depth"),
                "confidence": row.get("confidence") or {}}
    except Exception:                                               # noqa: BLE001 - a bug here keeps today's route
        return None


# --- The front-door wire: at most one shadow decision per request (run id), observational only ------
# A run resumed from its ledger (an orchestrator restart, or a suspend and resume) re-enters the front
# door under the SAME run id, so the wire must fire once per run - not once per entry. The tag is
# written BEFORE the work: a crash between tag and row can cost the row, never a duplicate. Run ids come
# from q.cancel_id (the /query/stream cancel id; the run ledger resumes under the same id). A q with no
# run id (one-shot machine leaves, tests) is tagged in memory only - it cannot be resumed, so it cannot
# double.
_SEEN = set()
_SEEN_LOCK = threading.Lock()
_TAG_NAME = "tagged.jsonl"


def _tag_key(q):
    rid = getattr(q, "cancel_id", None)
    return str(rid) if rid else None


def _already_tagged(key):
    if not key:
        return False
    with _SEEN_LOCK:
        if key in _SEEN:
            return True
    try:
        with open(os.path.join(config.MCCOY_LOG_DIR, _TAG_NAME), encoding="utf-8") as f:
            for line in f:
                try:
                    if (json.loads(line) or {}).get("run") == key:
                        with _SEEN_LOCK:
                            _SEEN.add(key)
                        return True
                except Exception:                                                  # noqa: BLE001
                    continue
    except Exception:                                                              # noqa: BLE001
        return False
    return False


def _tag(key):
    if not key:
        return
    with _SEEN_LOCK:
        _SEEN.add(key)
    try:
        os.makedirs(config.MCCOY_LOG_DIR, exist_ok=True)
        with open(os.path.join(config.MCCOY_LOG_DIR, _TAG_NAME), "a", encoding="utf-8") as f:
            f.write(json.dumps({"run": key, "at": datetime.datetime.now().isoformat(timespec="seconds")}) + "\n")
    except Exception:                                                              # noqa: BLE001
        pass


def observe(q):
    """The front-door wire: app._run_query_core (the wrapper every lane's entry goes through) calls this on
    every entry.
    At most one shadow decision per request: MCCOY_MODE "shadow", a non-test user, non-empty text - then a
    special route logs a 'skipped' row naming it, and anything else is triaged in the background. 'Once
    per request' holds across a resume: a run that comes back under the same run id (q.cancel_id) after a
    restart finds its tag and does nothing. Never raises, changes nothing about the turn, writes nothing
    but the shadow log."""
    try:
        if config.MCCOY_MODE != "shadow":
            return None
        user = getattr(q, "user", "") or ""
        if config.is_test_user(user) or active_for(user):
            return None                          # an ACTIVE user's turn is triaged by route(), once, on the turn
        text = (getattr(q, "text", "") or "").strip()
        if not text:
            return None
        key = _tag_key(q)
        if _already_tagged(key):
            return None
        skip = special_route(text, q)
        _tag(key)
        if skip:
            log_skip(user, skip, getattr(q, "chat_id", None))
            return None
        return shadow(text, user, routing=getattr(q, "routing", "auto"), chat_id=getattr(q, "chat_id", None))
    except Exception:                                                             # noqa: BLE001
        return None


# Special routes are decided upstream; never triage or modify them here.
# Markers must use existing Q fields and app.py predicates; do not add new heuristics.
_P20_CREATE_INTENT = None    # lazy import; app.p20_intent is a module-level import in app.py
_P20_STATUS_RE = None        # built once, reused


def _p20_create_intent(text):
    import p20_intent
    return p20_intent.wants_create(text)


def _p20_status_re():
    global _P20_STATUS_RE
    if _P20_STATUS_RE is None:
        import re
        # "what's the status of order X" / "is order X done" / "how's the run going" - the P20
        # status asks whose right route is a deck read (the advisor's own tier-tool label).
        _P20_STATUS_RE = re.compile(
            r"\b(?:status|progress|how'?[s\u2019]s|done|finished|running|stalled|stuck)\b"
            r"[^.!?]{0,60}\b(?:order|run|job|training|deploy)\b",
            re.IGNORECASE)
    return _P20_STATUS_RE


def log_skip(user, route, chat_id=None):
    """Write a 'skipped' row to the shadow log for a turn that took a special route.

    This is the log-only side of the special-route guard: the triage model is never called, and
    the routing is never changed. The row carries the route name so the Phase 8 review can see
    how often each special route short-circuits the triage."""
    try:
        row = {"at": datetime.datetime.now().isoformat(timespec="seconds"),
               "user": user, "routing": "auto", "chat_id": chat_id,
               "request": "", "mode": "shadow", "skipped": route,
               "model": config.MCCOY_MODEL, "threshold": config.MCCOY_TIER_THRESHOLD}
        _log(row)
    except Exception:                                                # noqa: BLE001
        pass


def special_route(text, q):
    """Return the name of a special route if `q` already takes one, else None.

    `q` is the app.py Q dataclass (or a duck-typed object with the same fields). The check is
    purely read-only: it never mutates `q`, never raises, and returns a short lowercase label
    that the caller writes into the shadow log row: self-engineering, absurd, P20 create, P20 status,
    external agents, machine users, an exact pin.
    """
    try:
        user = getattr(q, "user", "") or ""
        routing = getattr(q, "routing", "auto")
        effort = getattr(q, "effort_mode", "normal")
        text = text or ""
        # Persona accounts are briefs, not routing categories; they bypass the admin self-engineering gate.
        # Self-engineering relies on the single source of truth: app.SELF_ENGINEERING_INTENT.
        # We import it lazily here because this module is often imported before app in tests/isolated contexts.
        if user in config.ADMIN_USERS:
            import sys
            _app = sys.modules.get("app")
            _intent = getattr(_app, "SELF_ENGINEERING_INTENT", None) if _app else None
            if _intent is None:
                import app as _app  # noqa: F811  (lazy; only reached in test isolation)
                _intent = _app.SELF_ENGINEERING_INTENT
            if _intent.search(text):
                return "self-engineering"
        # 3. Absurd - the absurd async run marker (set by /absurd/start).
        if getattr(q, "absurd_max_seconds", None):
            return "absurd"
        # 4-5 read the text of an ASK; a supervised job packet describes work in prose ("invisible to a running job") and
        # is triaged like any other request, so these two rules do not apply to it (app.SUPERVISED_JOB, one pattern).
        import sys
        _app = sys.modules.get("app")
        _job = getattr(_app, "SUPERVISED_JOB", None) if _app else None
        if _job is None:
            import app as _app  # noqa: F811  (lazy; only reached in test isolation)
            _job = _app.SUPERVISED_JOB
        is_packet = bool(_job.search(text))
        # 4. P20 create - the render-queue intent predicate (app.py's own p20_intent.wants_create).
        if not is_packet and _p20_create_intent(text):
            return "p20-create"
        # 5. P20 status - the "what's the status of order/run X" ask (the advisor's tier-tool label).
        if not is_packet and _p20_status_re().search(text):
            return "p20-status"
        # 6. External agents - the Q.external_agent / external_use fields (the user named one).
        if getattr(q, "external_agent", None) or getattr(q, "external_use", None):
            return "external-agent"
        # 7. Machine users - ClaudeSupervisor et al. are not triaged (the fleet's own packets) - unless named in
        # MCCOY_ACTIVE_USERS, an explicit opt-in (the live proof of McCoy active runs as ClaudeSupervisor).
        if user in config.MACHINE_USERS and user not in config.MCCOY_ACTIVE_USERS:
            return "machine"
        # 8. An EXACT model pin (force_27b) is not triaged - the caller's pin already decided it. Quick is a general
        # mode, a WEIGHT inside the same weighing, so it is triaged like Auto.
        if routing not in ("auto", "quick_8b"):
            return "pinned-routing"
        return None
    except Exception:                                                # noqa: BLE001
        return None
