"""Ollama host helpers + tool-calling chat loop.

chat() returns (answer, escalation) where escalation is one of:
  None                   -> normal answer
  ("lead", reason)       -> brain wants the heavyweight on the lead (orchestrator auto-runs it)
  ("claude", reason)     -> recommend Claude (orchestrator OFFERS it; never auto)
"""
import contextlib
import datetime
import functools
import hashlib
import httpx
import itertools
import json
import os
import re
import socket
import threading
import uuid
import time
import cancellation
import config
import notes
import run_habits
import tool_selection
import tools


# Kirk-side measurement capture. This is deliberately thread-local and opt-in: an
# eval order opens the collector around its internal query, while ordinary chat pays
# no persistence or response-shape cost. The facts are recorded where the Ollama
# call actually runs, not reconstructed later from app.py's route labels.
_EXECUTION_EVIDENCE = threading.local()

# Registration happens inside the request layer, not by callers, to ensure every Ollama call is visible to the registry regardless of origin (eval, embeddings, etc.).
_INFLIGHT = {}                                   # call_id -> row (host, model, purpose, user, started, thread)
_INFLIGHT_LOCK = threading.Lock()
_CALL_CONTEXT = threading.local()                # purpose/user for the current thread's calls (opt-in labels)
_CALL_SEQ = [0]


def call_context(purpose=None, user=None, loop_deadline=None, job=None, budget_label=None, think_pin=None,
                 placed_think=None, exact_model=None, loop_budget=None):
    """Label this thread's subsequent Ollama calls (e.g. "eval_step coding-01", "absurd abr-…"). Labels
    are for humans on the tile; registration itself never depends on them. `loop_deadline` (a
    time.monotonic() value) bounds every coding tool loop inside the block; a nested deadline can only
    tighten it. Everything set here, including a later tighten_loop_deadline(), is restored on exit, so
    a pooled thread never inherits a finished job's deadline."""
    class _Ctx:
        def __enter__(self_inner):
            self_inner.prev = (getattr(_CALL_CONTEXT, "purpose", None), getattr(_CALL_CONTEXT, "user", None),
                               getattr(_CALL_CONTEXT, "loop_deadline", None),
                               getattr(_CALL_CONTEXT, "scope_depth", 0))
            self_inner.prev_job = getattr(_CALL_CONTEXT, "job", False)
            self_inner.prev_budget = getattr(_CALL_CONTEXT, "budget_label", None)
            self_inner.prev_pin = getattr(_CALL_CONTEXT, "think_pin", None)
            self_inner.prev_placed = getattr(_CALL_CONTEXT, "placed_think", None)
            self_inner.prev_exact = getattr(_CALL_CONTEXT, "exact_model", False)
            self_inner.prev_loop_budget = getattr(_CALL_CONTEXT, "loop_budget", None)
            if loop_budget is not None:
                _CALL_CONTEXT.loop_budget = loop_budget      # the job's whole budget in seconds, for its clock (_clock_note)
            if exact_model is not None:
                _CALL_CONTEXT.exact_model = bool(exact_model)   # a user's exact model-and-box pin (lead_model keeps it)
            if placed_think is not None:
                _CALL_CONTEXT.placed_think = placed_think    # the priced menu row Spock placed this turn on (placed_effort)
            if job is not None:
                _CALL_CONTEXT.job = bool(job)
            if think_pin is not None:
                _CALL_CONTEXT.think_pin = think_pin    # the user's own effort pin (pinned_think)
            if budget_label is not None:
                _CALL_CONTEXT.budget_label = budget_label   # the user's own budget, named when the loop stops on it
            if purpose is not None:
                _CALL_CONTEXT.purpose = purpose
            if user is not None:
                _CALL_CONTEXT.user = user
            if loop_deadline is not None:
                cur = self_inner.prev[2]
                _CALL_CONTEXT.loop_deadline = loop_deadline if cur is None else min(cur, loop_deadline)
            _CALL_CONTEXT.scope_depth = self_inner.prev[3] + 1
            return self_inner

        def __exit__(self_inner, *exc):
            (_CALL_CONTEXT.purpose, _CALL_CONTEXT.user, _CALL_CONTEXT.loop_deadline,
             _CALL_CONTEXT.scope_depth) = self_inner.prev
            _CALL_CONTEXT.job = self_inner.prev_job
            _CALL_CONTEXT.budget_label = self_inner.prev_budget
            _CALL_CONTEXT.think_pin = self_inner.prev_pin
            _CALL_CONTEXT.placed_think = self_inner.prev_placed
            _CALL_CONTEXT.exact_model = self_inner.prev_exact
            _CALL_CONTEXT.loop_budget = self_inner.prev_loop_budget
            return False
    return _Ctx()


# OLLAMA_THINK maps internal labels to ollama API values. "xhigh" is the only label that triggers extended thinking
# on current ollama; "high" maps to medium effort. Plain chat calls keep thinking disabled to preserve latency.
OLLAMA_THINK = {"xhigh": "xhigh", "medium": "medium", "low": "low"}
_CROWN = {"mtime": None, "crown": None}


def _crown():
    """roles.crown from the placement table, re-read when the file changes; None when it cannot be read (then
    nothing changes: think stays off, the same fail-open as the table's other reader)."""
    try:
        st = os.stat(config.POLICY_TABLE)
        if _CROWN["mtime"] != st.st_mtime_ns:
            _CROWN["crown"] = (config.load_policy(config.POLICY_TABLE).get("roles") or {}).get("crown")
            _CROWN["mtime"] = st.st_mtime_ns
    except Exception:
        _CROWN["crown"], _CROWN["mtime"] = None, None
    return _CROWN["crown"]


def _crown_box_url(crown):
    return getattr(config, "HOSTS_BY_NAME", {}).get(crown.get("box")) or config.roster_url(crown.get("box"))


def lead_model(host, model, job):
    """Resolve the target model for a call.

    Applies the crown's model if the call is job-shaped, targets the crown's box, and matches that box's
    standing model. The crown is shared by job family for agentic/long-context work; plain chat uses the
    box's standing model. Explicit user effort pins or exact model pins keep the call on its original
    model. One source of truth: roles.crown."""
    if not job or getattr(_CALL_CONTEXT, "think_pin", None) is not None or getattr(_CALL_CONTEXT, "exact_model", False):
        return model
    crown = _crown() or {}
    box_url = _crown_box_url(crown)
    standing = {config.HEAVY_HOST: config.HEAVY_MODEL}.get((host or "").rstrip("/"))
    if not box_url or not crown.get("model") or (host or "").rstrip("/") != box_url.rstrip("/") or model != standing:
        return model
    return crown["model"]


_CAN_THINK = {}


def can_think(host, model):
    """True when `model` on `host` declares the "thinking" capability (/api/show), False when it declares it does not,
    None when the box could not be asked. Known answers are cached per (host, model); "could not ask" is not cached.
    ollama refuses a `think` flag for a model without the capability, so no flag is sent unless this is True."""
    key = ((host or "").rstrip("/"), model)
    if config.swap_base(host, model):
        return True     # llama-swap: True = the engine template's own default (swap_think_kwargs), not an ollama flag
    if key in _CAN_THINK:
        return _CAN_THINK[key]
    if not config.THINK_CAPS_PROBE:
        return None
    try:
        r = httpx.post(f"{key[0]}/api/show", json={"model": model}, timeout=config.REACH_TIMEOUT)
        r.raise_for_status()
        caps = r.json().get("capabilities")
    except Exception as e:
        notes.log("THINK CAPABILITY UNKNOWN host=%s model=%s (%s) - this call goes without a think flag" % (
            host, model, type(e).__name__))
        return None
    if not isinstance(caps, list):
        return None
    _CAN_THINK[key] = "thinking" in caps
    return _CAN_THINK[key]


_CAN_SEE = {}


def can_see(host, model):
    """True when a picture in a message reaches `model` on `host`: an ollama model that declares the "vision"
    capability (/api/show), or a Strata model whose box declares it is served with its encoder. False when it declares
    none. The Tolkien box's window sees when Tolkien declares it, as a Strata box does. None when an ollama box could not be asked; that is not cached."""
    key = ((host or "").rstrip("/"), model)
    if key[0] == config.TOLKIEN_WINDOW_URL.rstrip("/"):
        import scheduler
        return ("sees.%s" % model) in scheduler.factory_capabilities(scheduler.FELLOWSHIP_HOST)
    if config.swap_base(host, model):
        # A Strata model sees when its OWN box declares it (`sees.<model>`, read by the factory off the serving plan
        # its launcher runs: p20_foundry._plan_vision). Never assumed: a server started without its encoder refuses
        # a picture, so the declaration and the server must be the same fact.
        import scheduler
        box = next((h for h, card in scheduler.CARDS.items() if (card.get("url") or "").rstrip("/") == key[0]), None)
        return bool(box) and ("sees.%s" % model) in scheduler.factory_capabilities(box)
    if _CAN_SEE.get(key) or time.time() < _NO_UNTIL.get(("see",) + key, 0):
        return _CAN_SEE[key]
    try:
        r = httpx.post(f"{key[0]}/api/show", json={"model": model}, timeout=config.REACH_TIMEOUT)
        r.raise_for_status()
        caps = r.json().get("capabilities")
    except Exception as e:
        notes.log("VISION CAPABILITY UNKNOWN host=%s model=%s (%s)" % (host, model, type(e).__name__))
        return None
    if not isinstance(caps, list):
        return None
    _CAN_SEE[key] = "vision" in caps
    if not _CAN_SEE[key]:
        _NO_UNTIL[("see",) + key] = time.time() + CAPABILITY_NO_LIFE_S
    return _CAN_SEE[key]


# "No" capability responses expire after 1 minute so a model re-created with new features is picked up; "yes" responses persist for the process lifetime. This TTL bounds the retry frequency for failed capability checks.
CAPABILITY_NO_LIFE_S = 60
_NO_UNTIL = {}


# Only Qwen engines parse tool calls from mid-conversation descriptions;
# other engines require tools defined at request start.
SEASON_PARSERS = ("qwen3.5", "qwen3-coder")
_CAN_SEASON = {}
_SEASON_QUIET = {}


def can_season(host, model):
    """True when a call to a tool described only in the conversation comes back from this engine as a parsed call.
    Proven by the short-list test (campaigns\\toolshape-20261105, shape B, the definitions at the end of the user's turn):
    the 27B (ollama, PARSER qwen3.5) called such a tool by name 10-11 times a run, Flash-Next on Strata 16-17, the 9B 21-24,
    every call parsed, B right as often as today's shape on both job models. ollama's Qwen parser uses the request's list
    only for argument types (model/parsers/qwen3coder.go parseToolCall), so those arguments arrive as strings and Kirk types
    them (_typed_args). ollama's other parsers match listed names only (tools/tools.go findTool), and the Tolkien box is unproven:
    those keep the old growth, one re-read. Unknown is False (the old way still works) and is not cached."""
    key = ((host or "").rstrip("/"), model)
    if key[0] == config.TOLKIEN_WINDOW_URL.rstrip("/"):
        return False
    if config.swap_base(host, model):
        return "flash-next" in (model or "")
    if _CAN_SEASON.get(key) or time.time() < _NO_UNTIL.get(("season",) + key, 0):
        return _CAN_SEASON[key]
    import scheduler
    if key[0] not in {(c.get("url") or "").rstrip("/") for c in scheduler.CARDS.values() if c.get("url")}:
        return False                   # an engine the fleet does not list is unproven: the old growth, nothing asked
    if time.time() < _SEASON_QUIET.get(key, 0):
        return False                   # asked within the minute and could not tell: one wait a minute, not one a turn
    try:
        r = httpx.post(f"{key[0]}/api/show", json={"model": model}, timeout=config.REACH_TIMEOUT)
        r.raise_for_status()
        modelfile = r.json().get("modelfile") or ""
    except Exception as e:
        _SEASON_QUIET[key] = time.time() + CAPABILITY_NO_LIFE_S
        notes.log("SEASON CAPABILITY UNKNOWN host=%s model=%s (%s): the block grows instead" % (host, model, type(e).__name__))
        return False
    parsers = [ln.split(None, 1)[1].strip() for ln in modelfile.splitlines() if ln.startswith("PARSER ") and " " in ln]
    _CAN_SEASON[key] = bool(parsers) and parsers[0] in SEASON_PARSERS
    if not _CAN_SEASON[key]:
        _NO_UNTIL[("season",) + key] = time.time() + CAPABILITY_NO_LIFE_S
    return _CAN_SEASON[key]


def _typed_args(tool, args):
    """A seasoned tool's call, typed by its own schema: the engine had no schema for it, so a number, flag, list or object
    arrives as its text. A value that does not parse as its declared type is passed on unchanged (the tool says why)."""
    props = (((tool or {}).get("function") or {}).get("parameters") or {}).get("properties") or {}
    if not isinstance(args, dict) or not props:
        return args
    out = dict(args)
    for k, v in args.items():
        want = (props.get(k) or {}).get("type")
        if not isinstance(v, str) or want in (None, "string") or isinstance(want, list):
            continue
        try:
            if want == "boolean" and v.strip().lower() in ("true", "false"):
                out[k] = v.strip().lower() == "true"
            elif want == "integer":
                out[k] = int(v.strip())
            elif want == "number":
                f = float(v.strip())
                out[k] = int(f) if f.is_integer() and "." not in v else f
            elif want in ("array", "object"):
                parsed = json.loads(v)
                if isinstance(parsed, list if want == "array" else dict):
                    out[k] = parsed
        except (ValueError, TypeError):
            pass
    return out


# offer_picture attaches a screenshot to the turn's tool results as a separate, marked message so it is never read as the user's request. Newer screenshots of the same file replace older ones to prevent payload bloat in iterative rendering jobs.
_PICTURE_NOTE = "[Picture from "


def serving():
    """(host, model) of the chat call whose tool is running on this thread, or None outside one."""
    return getattr(_CALL_CONTEXT, "serving", None)


# REPLAYABLE_TOOLS contains read-only tools that are safe to re-run after a restart.
# Tools that write, send, or place work must NOT be included here; they rely on note_tool_handle and tools.can_resume to avoid duplicate side effects.
REPLAYABLE_TOOLS = frozenset({"search_docs", "get_document", "web_search", "fetch_url", "coding_read", "coding_list",
                              "coding_search", "coding_git", "coding_look",
                              "coding_test"})   # a test run's verdict: running it again is a fresh, real verdict on the same code


def note_tool_handle(handle):
    """From a tool waiting on work elsewhere: what a run back after a restart needs to wait for the same work."""
    sink = getattr(_CALL_CONTEXT, "tool_handle", None)
    if sink is not None:
        try:
            sink(handle)
        except Exception:
            pass


def offer_picture(label, image_b64):
    """From a tool: hand this picture to the model serving the call. -> True when that model can see it and it will
    follow the tool's result; False when it cannot (the tool then says what it can in words)."""
    who, held = serving(), getattr(_CALL_CONTEXT, "pictures", None)
    if not who or held is None or can_see(*who) is not True:
        return False
    held.append((str(label), image_b64))
    return True


def _add_pictures(messages, pictures):
    for label, b64 in pictures:
        tag = "%s%s]" % (_PICTURE_NOTE, label)
        # never touch a message already sent: the engine reuses only an exact start of the prompt.
        again = any(str(m.get("content") or "").startswith(tag) for m in messages)
        messages.append({"role": "user", "content": tag + (" (the newest picture of this file; any earlier one further up is older)" if again else ""),
                         "images": [b64]})


def _repair_rejected_tool_media(messages, media, step):
    """Withhold only the exact unaccepted tool-image batch; preserve accepted prefix and rejected bytes."""
    import copy
    pending = media.get("pending") or []
    if not pending:
        return False
    for offered in pending:
        index = offered.get("index")
        if (not isinstance(index, int) or index < 0 or index >= len(messages)
                or messages[index] != offered.get("message")
                or not messages[index].get("images")
                or not str(messages[index].get("content") or "").startswith(_PICTURE_NOTE)):
            return False  # compaction or another change made attribution uncertain; never guess
    media.setdefault("rejected", []).append({"step": step, "error_code": "media_input_rejected",
                                              "offered": copy.deepcopy(pending)})
    for offered in pending:
        messages[offered["index"]] = {"role": "user", "content": (
            "[Tool image delivery error: the model server rejected this newly offered image. "
            "It was NOT seen. Convert it to a supported image or choose another reference. "
            "Original files and previously accepted images are unchanged. Source: %s]"
            % offered["message"].get("content", ""))}
    media["pending"] = []
    return True


def lead_think(host, model, job):
    """ollama's `think` for this call. Thinking is on by default: disabling it degrades model quality for minimal speed gain.
    Job-shaped calls to the crown model on the crown box use the crown's configured effort level; other calls to
    thinking-capable models use the default "on"; models without capability (or unknown) get no flag."""
    crown = _crown() or {}
    box_url = _crown_box_url(crown)
    if job and box_url and (host or "").rstrip("/") == box_url.rstrip("/") and model == crown.get("model"):
        level = crown.get("think")
        if level:
            return OLLAMA_THINK.get(level, bool(level))
    return bool(can_think(host, model))


# User effort pin overrides the job rule for every call in the turn. Pin levels are
# model-specific: only the qwen3.8 family has distinct granularities; for all other
# models, any non-'off' level maps to the model's maximum effort.
CUT_CALL_KEY = "_cut_off_arguments_chars"   # a tool call whose arguments were cut before they were valid JSON: tools.run_tool answers it, nothing runs
EFFORT_PIN_LEVELS = ("off", "low", "medium", "max")
_LEVELLED_FAMILIES = ("qwen3.8",)


def pinned_think(model, level):
    """ollama's `think` for the user's effort pin on `model`, or None when there is no pin (Auto: HAI decides)."""
    if level not in EFFORT_PIN_LEVELS:
        return None
    if level == "off":
        return False
    if any((model or "").startswith(f) for f in _LEVELLED_FAMILIES):
        return OLLAMA_THINK["xhigh" if level == "max" else level]
    return True


def placed_effort(host, model, setting):
    """Return the `think` flag for the priced menu row a turn was placed on, or None if not placed from the menu. Preserves the row's 'off' setting to avoid running configurations voided by OOM constraints (e.g., qwen3.5:9b on 8GB). Other settings default to 'on' via `lead_think`."""
    if setting is None:
        return None
    if str(setting).lower() == "off":
        return False
    return lead_think(host, model, False)


def tighten_loop_deadline(deadline):
    """Tighten the current call_context's coding-loop deadline (time.monotonic()). Outside any
    call_context there is nothing to restore it, so it is refused rather than left on the thread."""
    if getattr(_CALL_CONTEXT, "scope_depth", 0) <= 0:
        notes.log("loop deadline NOT set: tighten_loop_deadline called outside a call_context")
        return None
    cur = getattr(_CALL_CONTEXT, "loop_deadline", None)
    _CALL_CONTEXT.loop_deadline = deadline if cur is None else min(cur, deadline)
    return _CALL_CONTEXT.loop_deadline


def delta_sink(fn):
    """Route this thread's streaming deltas (#37 step 1: model text, tool calls and tool results as they
    happen) to `fn`, one dict per event, while the block runs; the previous sink is restored on exit.
    Per-thread, so only the worker that set it streams; a call made from another thread does not."""
    class _Ctx:
        def __enter__(self_inner):
            self_inner.prev = getattr(_CALL_CONTEXT, "delta_sink", None)
            _CALL_CONTEXT.delta_sink = fn
            return self_inner

        def __exit__(self_inner, *exc):
            _CALL_CONTEXT.delta_sink = self_inner.prev
            return False
    return _Ctx()


def run_ledger(ledger):
    """Checkpoint every chat() call this thread makes into `ledger` (run_ledger.RunLedger) while the block
    runs, and replay what it already recorded: Kirk's own runs survive an orchestrator restart (#37).
    Per-thread, like delta_sink; the previous ledger is restored on exit."""
    class _Ctx:
        def __enter__(self_inner):
            self_inner.prev = (getattr(_CALL_CONTEXT, "run_ledger", None), getattr(_CALL_CONTEXT, "ckpt_slot", None),
                               getattr(_CALL_CONTEXT, "window_run", None))
            _CALL_CONTEXT.run_ledger, _CALL_CONTEXT.ckpt_slot = ledger, None
            # the whole run holds any chat window it uses, across its tool calls (B9); released when the run ends
            _CALL_CONTEXT.window_run = "run:" + (ledger.run_id if ledger is not None else uuid.uuid4().hex)
            return self_inner

        def __exit__(self_inner, *exc):
            if config.CHAT_WINDOWS:
                import chat_window
                chat_window.release(_CALL_CONTEXT.window_run)
            _CALL_CONTEXT.run_ledger, _CALL_CONTEXT.ckpt_slot, _CALL_CONTEXT.window_run = self_inner.prev
            return False
    return _Ctx()


# Checkpoints align with natural turn boundaries (message/tool call completion) to ensure text integrity.
# Auto: saves before every model turn.
# Manual (Park): run completes current task, saves, and holds until resumed.
_PARK = threading.Event()
_PARKED = set()                  # thread names of runs holding at a checkpoint


def park_runs(on=True):
    """Ask every checkpointed run to hold at its next checkpoint (on=False releases them)."""
    if on:
        _PARK.set()
    else:
        _PARK.clear()


def parking():
    return _PARK.is_set()


def parked_count():
    with _INFLIGHT_LOCK:
        return len(_PARKED)


def _park_here():
    """Hold this run at the checkpoint just written until released (or the process ends, which is the point).
    A Stop from the user still lands: cancellation is checked while waiting."""
    me = "%s-%d" % (threading.current_thread().name, threading.get_ident())
    with _INFLIGHT_LOCK:
        _PARKED.add(me)
    try:
        while _PARK.is_set():
            cancellation.check()
            _PARK_WAKE.wait(1.0)
    finally:
        with _INFLIGHT_LOCK:
            _PARKED.discard(me)


# SUSPEND ONE RUN (#37 Quick Resume, the design doc's "suspend one run to storage on demand, free its slot"). Unlike
# park (every run holds IN MEMORY for a deploy), a suspended run stops at its next natural point and its thread
# unwinds: the lead slot and the worker are freed, and the run lives on only as its saved ledger + feed until
# app's POST /runs/resume starts it again from there. Only a top-level call stops; a call made inside a tool runs on
# to its parent's next natural point, so no tool is ever cut by a suspend.
_SUSPEND = set()                 # run ids asked to suspend


class RunSuspended(BaseException):
    """Raised at a natural point in a run asked to suspend. A BaseException, like SystemExit: no `except Exception`
    on the way out may turn it into an error answer and end the run."""


def suspend_run(run_id, on=True):
    with _INFLIGHT_LOCK:
        (_SUSPEND.add if on else _SUSPEND.discard)(str(run_id))


def _suspend_here(slot):
    if slot is None or "." in slot.key:
        return
    rid = slot.ledger.run_id
    with _INFLIGHT_LOCK:
        if rid not in _SUSPEND:
            return
        _SUSPEND.discard(rid)
    raise RunSuspended(rid)


# TALK TO IT WHILE IT WORKS (#37, requirement 2: "like Claude Code"). A message sent to a running run waits here and is
# delivered at the run's next natural point (the start of a model turn, or instead of ending when the model has just
# answered), as a user message the model must take into account. Delivered messages are kept per run so app can save
# them with the answer. Only the top-level loop of a run takes them (a sub-call's slot key carries a ".").
_INBOX = {}                      # run id -> [{"from", "text", "at"}] waiting for the next natural point
_INBOX_NOTE = "[New message from "   # how a delivered message opens: never the run's own request (_current_brief)
_DELIVERED = {}                  # run id -> the same, once handed to the model


def post_to_run(run_id, text, who, internal=False):
    """`internal`: back-end work reported to the model. These messages are never shown to the user or saved with the chat history."""
    with _INFLIGHT_LOCK:
        _INBOX.setdefault(str(run_id), []).append(
            {"from": str(who or ""), "text": str(text), "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "internal": bool(internal)})


def take_run_messages(run_id):
    """(delivered, undelivered) for a finished run, and forget both. Undelivered = sent after its last natural point. Only what a
    person sent: an internal report is not theirs to see."""
    rid = str(run_id or "")
    with _INFLIGHT_LOCK:
        return _DELIVERED.pop(rid, []), [m for m in _INBOX.pop(rid, []) if not m.get("internal")]


def _drain_inbox(slot, messages):
    """Hand this run's waiting messages to the model. Returns how many."""
    if slot is None or "." in slot.key:
        return 0
    rid = slot.ledger.run_id
    with _INFLIGHT_LOCK:
        waiting = _INBOX.pop(rid, [])
        said = [m for m in waiting if not m.get("internal")]
        if said:
            _DELIVERED.setdefault(rid, []).extend(said)
    for m in waiting:
        messages.append({"role": "user", "content": (
            _INBOX_NOTE + "%s while you were working - take it into account now, then carry on or change "
            "course as it asks:]\n%s" % (m["from"] or "the user", m["text"]))})
        if not m.get("internal"):
            _delta({"kind": "user_message", "from": m["from"], "text": m["text"]})
        notes.log("RUN MESSAGE delivered run=%s from=%s chars=%d%s" % (rid, m["from"], len(m["text"]), " internal" if m.get("internal") else ""))
    return len(waiting)


_PARK_WAKE = threading.Event()   # never set: a 1 s wait that a test can shorten by patching
YIELD_HOOK = None                # app sets it: a job holding the lead yields to a quick request between turns
UNVERIFIED_HOOK = None           # app sets it: (task ids, why) -> label tail; keeps unverified work as a marked checkpoint
OPEN_ERRANDS_HOOK = None         # medical_review sets it: (run id) waits until that run's open errands have reported


def _call_sig(messages, tools_list):
    """A short fingerprint of a top-level chat() call: stable across a restart, different between kinds of call.

    It is the first line of the leading system prompt ("" when there is none) plus the SORTED tool names ([]
    when there are no tools). Host, model, num_ctx and the rest of the messages are deliberately left out: a
    resume may land on another box, and the conversation grows. Calls of the same kind share a sig and resume
    positionally between themselves, as they always did."""
    first = ""
    for m in (messages or []):
        if isinstance(m, dict) and m.get("role") == "system" and m.get("content"):
            first = str(m["content"]).splitlines()[0]
            break
    names = sorted({(t.get("function") or {}).get("name") or t.get("name") or ""
                    for t in (tools_list or []) if isinstance(t, dict)})
    return hashlib.sha256(("%s\n%s" % (first, ",".join(names))).encode()).hexdigest()[:16]


def _checkpointed(fn):
    """chat() under a run ledger: a call the ledger already finished returns its recorded answer without a model
    call; otherwise the call runs (its loop saves before every turn and resumes from the last save) and its
    answer is recorded. No ledger on the thread = the plain call."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ledger = getattr(_CALL_CONTEXT, "run_ledger", None)
        if ledger is None:
            return fn(*args, **kwargs)
        parent = getattr(_CALL_CONTEXT, "ckpt_slot", None)
        sig = None
        if parent is None:
            # chat(host, model, messages, tools_list, ...): messages and tools_list are the third and fourth
            # positional parameters; a keyword call works just as well.
            messages = args[2] if len(args) > 2 else kwargs.get("messages")
            tools_list = args[3] if len(args) > 3 else kwargs.get("tools_list")
            sig = _call_sig(messages, tools_list)
        slot = ledger.open_call(parent, sig)
        rec = slot.recorded()
        if rec is not None:
            return rec
        _CALL_CONTEXT.ckpt_slot = slot
        try:
            out = fn(*args, **kwargs)
        finally:
            _CALL_CONTEXT.ckpt_slot = parent
        slot.done(out)
        return out
    return wrapper


def _delta(payload):
    """Hand one delta to this thread's sink, if any. Streaming is for watching: a sink that raises is
    ignored and never reaches the model call."""
    sink = getattr(_CALL_CONTEXT, "delta_sink", None)
    if sink is not None:
        try:
            sink(payload)
        except Exception:
            pass


_TURN_SEQ = itertools.count(1)

# Tracks generation speed per box independently of orders (chat is not an order).
# Rate: median of last GEN_WINDOW completions (min GEN_MIN_TOKENS); live rate while streaming.
GEN_MIN_TOKENS = 20       # p20_foundry.ENGINE_MIN_TOKENS
GEN_WINDOW = 10           # p20_foundry.TOKS_WINDOW
_GEN = {}                 # host url -> {"live": (tokens, first, last) | None, "recent": [tok/s, ...]}


_TRAIL_FULL_FIELDS = ("args_full", "result_full")


def compact_trail(steps):
    """The tool trail as it LEAVES the process (the page, chat history): the short display fields only. The full
    arguments and results stay in memory for the critics (deliberation.evidence_block)."""
    return [{k: v for k, v in s.items() if k not in _TRAIL_FULL_FIELDS} if isinstance(s, dict) else s
            for s in (steps or [])]


def _gen_note(host, tokens=None, first=None, eval_count=None, eval_duration=None, prompt_count=None,
              prompt_duration=None, num_ctx=None, prompt_cached=None):
    key = (host or "").rstrip("/")
    now = time.time()
    with _INFLIGHT_LOCK:
        g = _GEN.setdefault(key, {"live": None, "recent": []})
        if tokens is not None:
            g["live"] = (tokens, first, now)
            return
        g["live"] = None
        if eval_count and eval_duration and eval_count >= GEN_MIN_TOKENS:
            g["recent"] = (g["recent"] + [eval_count / (eval_duration / 1e9)])[-GEN_WINDOW:]
        # Prompt cache accounting: prompt_eval_count is the full prompt, but prompt_eval_duration only measures the new tokens (the tail). Divide duration by the uncached tokens (count - cached) to get true prefill speed; dividing by total count underestimates the rate.
        read_n = (prompt_count or 0) - (prompt_cached or 0)
        if read_n and prompt_duration and read_n >= GEN_MIN_TOKENS:
            g["read"] = (g.get("read", []) + [read_n / (prompt_duration / 1e9)])[-GEN_WINDOW:]
        # reload measures total prompt throughput including cached tokens, distinguishing it from the pure read rate above
        if prompt_cached and prompt_count and prompt_duration and prompt_count >= GEN_MIN_TOKENS:
            g["reload"] = (g.get("reload", []) + [prompt_count / (prompt_duration / 1e9)])[-GEN_WINDOW:]
        if num_ctx and (prompt_count or eval_count):
            g["ctx"] = ((prompt_count or 0) + (eval_count or 0), num_ctx)


def _gen_step(host, turn=None, tool=None, tool_ms=None):
    """The agent loop's position on this box for the tile: the turn it is on and the tool it is running (tool_ms
    None = still running) or ran last."""
    key = (host or "").rstrip("/")
    with _INFLIGHT_LOCK:
        g = _GEN.setdefault(key, {"live": None, "recent": []})
        if turn is not None:
            g["turn"] = turn
        if tool is not None:
            g["tool"] = {"name": tool, "started": time.time(), "ms": tool_ms}


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def generation_view(host):
    """Everything the tile shows about Kirk's work on `host`: write and read tok/s, the loop's turn and tool, and
    how full the context is. Missing values are None - the tile prints nothing rather than a guess."""
    key = (host or "").rstrip("/")
    with _INFLIGHT_LOCK:
        g = dict(_GEN.get(key) or {})
    write, how = generation_rate(host)
    tool = g.get("tool") or {}
    tool_s = None
    if tool:
        tool_s = (tool["ms"] / 1000.0) if tool.get("ms") is not None else (time.time() - tool["started"])
    return {"write": write, "write_how": how, "read": _median(g.get("read") or []),
            "reload": _median(g.get("reload") or []), "turn": g.get("turn"),
            "tool": tool.get("name"), "tool_s": tool_s, "tool_running": bool(tool) and tool.get("ms") is None,
            "ctx": g.get("ctx")}


def generation_rate(host):
    """(tok/s, "live" | "recent") for Kirk's generation on `host`, or (None, "") when it has none. The
    caller decides whether Kirk is working there (in-flight or within the accountant's grace)."""
    key = (host or "").rstrip("/")
    with _INFLIGHT_LOCK:
        g = _GEN.get(key)
        if not g:
            return None, ""
        live, recent = g["live"], list(g["recent"])
    if live:
        tokens, first, last = live
        if tokens >= GEN_MIN_TOKENS and last > first:
            return tokens / (last - first), "live"
    if recent:
        recent.sort()
        mid = len(recent) // 2
        return (recent[mid] if len(recent) % 2 else (recent[mid - 1] + recent[mid]) / 2), "recent"
    return None, ""
# How much of each tool result a live viewer is sent: a phone shows a preview line, not a 100 KB file
# read. Display only - the model and every grader still get the full result.
_LIVE_RESULT_PREVIEW = 2000


# Coding tools that change the workspace when they succeed. After one, re-reading or re-testing returns
# something new; before one, repeating a call returns what the model already has.
_WORKSPACE_CHANGING = {
    "coding_patch": None, "coding_create": None, "coding_file": None, "coding_fetch": None,
    "coding_checkpoint": {"create", "restore"},
    "coding_task": {"start", "finish", "resume", "abandon"},
    "coding_project_driver": {"start", "advance", "resume"},
}


# A trip of the idle rule (CODING_IDLE_TURNS repeat-only turns in a row) first EXPLAINS and restarts the count; only a
# trip after IDLE_RESETS of those ends the loop.
IDLE_RESETS = 1


def _idle_nudge(repeated, idle, bound, resets_left):
    """Explains to the model that repeated calls are blocked because they yield no new information.
    Specifically warns that `coding_test` runs only the profile's test files, not user scripts."""
    names = ", ".join(repeated) if repeated else "every call"
    tripped = idle >= bound
    text = ("[NO NEW WORK: %s repeated %s with the same arguments on an unchanged workspace, so it returned nothing "
            "new. Make the edit, run a different check, or give your answer." % (
                ("%d turns in a row" % idle) if tripped else "that turn", names))
    if "coding_test" in repeated:
        text += (" coding_test runs the test files of its profile and nothing else, so running it again cannot run a "
                 "script you wrote. To run a script, call it from a test file (test_*.py) in your folder, then run "
                 "the profile.")
    if tripped:
        text += (" The count starts again now; %d more such turns in a row end the loop.]" % bound)
    else:
        text += (" %d more such turns in a row and the loop %s.]" % (
            bound - idle, "stops you here to reset the count" if resets_left > 0 else "ends here"))
    return text


# Model-behaviour stops (deadline, idle, stall, looped, runaway, etc.) are handled here: the model is asked for a handoff and the run is marked stopped. Machinery faults (transport, media rejection) must still raise; do not conflate them.
_STOP_WHY = {
    "deadline": lambda: "the job's time budget ran out",
    "idle": lambda: "no new work for %d turns in a row" % config.CODING_IDLE_TURNS,
    "stall": lambda: ("no progress for %d turns in a row: the code kept returning to versions already tried"
                      % config.CODING_STALL_TURNS),
    "looped": lambda: ("the answer went into a loop %d times in a row (one passage written over and over)"
                       % config.CODING_IDLE_TURNS),
    "runaway": lambda: ("it thought past %d tokens without calling a tool, turn after turn"
                        % config.TOLKIEN_RUNAWAY_THINK),
    "out_of_room": lambda: "it filled its window turn after turn without calling a tool",
    "text_calls": lambda: "it kept writing tool calls as text instead of making them",
    "empty": lambda: "it gave an empty answer",
}


def _stall_nudge(stall):
    """What the loop tells a model whose edits keep landing on code it has already had (the STALL rule's trip)."""
    return ("[NO PROGRESS: for %d turns your edits only brought the code back to versions it has already been, and every "
            "check you ran had already been run on them. Going back to code you already tried cannot get further. Try "
            "an approach you have not tried yet, read something you have not read, or give your answer. The count starts "
            "again now; %d more such turns end the loop.]" % (stall, stall))


def _result_dict(result):
    """A tool's result as a dict when it is one (or a JSON text starting with one), else {}."""
    if isinstance(result, dict):
        return result
    try:
        obj, _ = json.JSONDecoder().raw_decode(str(result).lstrip())
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _short_hash(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


class _LoopProgress:
    """Detects coding loop idleness via two rules:

    REPEAT: A call is NEW unless the same tool with the same arguments already ran on the same workspace version;
    a turn where all calls are repeats is IDLE.

    STALL: A turn makes PROGRESS only if the workspace reaches a new state or the model makes a new observation.
    Rewriting back to tried code is not progress. The state is defined by file hashes (path -> sha256) reported by
    edit tools; edits without hashes count as new states to avoid false positives. Turns without tool calls are
    excluded from this detection."""

    def __init__(self):
        self.version = 0
        self.seen = set()
        self.idle_turns = 0
        self.resets = 0         # trips already spent on a reset (IDLE_RESETS), shared by both rules as in A1
        self.repeated = []      # this turn's repeated tool names, for the warning (not checkpointed: one turn's worth)
        self.files = {}         # STALL: path -> sha256 as the edit tools reported it
        self.states = {self._fp({})}
        self.looked = set()     # STALL: hashes of (state, tool, args) observations already made
        self.stall_turns = 0
        self.unhashed = 0
        self._new_look = False

    @staticmethod
    def _fp(files):
        return _short_hash(sorted(files.items()))

    def call(self, fn, args, ok, result=None):
        try:
            key = (fn, json.dumps(args, sort_keys=True, default=str), self.version)
        except Exception:
            key = (fn, repr(args), self.version)
        new = key not in self.seen
        self.seen.add(key)
        if not new and fn not in self.repeated:
            self.repeated.append(fn)
        actions = _WORKSPACE_CHANGING.get(fn, ())
        changes = fn in _WORKSPACE_CHANGING and (actions is None or (args or {}).get("action") in actions)
        if ok and changes:
            self.version += 1
            self._edited(fn, args or {}, _result_dict(result))
        elif not changes:
            look = _short_hash(self._fp(self.files), fn, key[1])
            if look not in self.looked:
                self.looked.add(look)
                self._new_look = True
        return new

    def _edited(self, fn, args, res):
        path = args.get("path")
        if fn == "coding_create" and res.get("sha256"):
            self.files[path] = res["sha256"]
        elif fn == "coding_patch" and res.get("new_sha256"):
            self.files[path] = res["new_sha256"]
        elif fn == "coding_file" and args.get("action") == "delete":
            self.files.pop(path, None)
        elif fn == "coding_file" and args.get("action") == "move" and args.get("destination"):
            self.files[args["destination"]] = self.files.pop(path, "moved:%s" % path)
        else:                                   # no hash to go on: a new state, the generous reading
            self.unhashed += 1
            self.files["\0unhashed:%d" % self.unhashed] = str(self.unhashed)

    def end_turn(self, any_new):
        self.idle_turns = 0 if any_new else self.idle_turns + 1
        after = self._fp(self.files)
        progress = after not in self.states or self._new_look
        self.states.add(after)
        self._new_look = False
        self.stall_turns = 0 if progress else self.stall_turns + 1
        return self.idle_turns

    def take_repeated(self):
        out, self.repeated = self.repeated, []
        return out

    def state(self):
        """What a checkpoint carries, as JSON (resumed jobs must not lose their repeat state)."""
        return {"version": self.version, "seen": sorted(list(k) for k in self.seen), "idle_turns": self.idle_turns,
                "resets": self.resets, "files": dict(self.files), "states": sorted(self.states),
                "looked": sorted(self.looked), "stall_turns": self.stall_turns, "unhashed": self.unhashed}

    @classmethod
    def restore(cls, state):
        p = cls()
        p.version = int(state.get("version") or 0)
        p.seen = {tuple(k) for k in state.get("seen") or ()}
        p.idle_turns = int(state.get("idle_turns") or 0)
        p.resets = int(state.get("resets") or 0)
        p.files = dict(state.get("files") or {})
        p.states = set(state.get("states") or ()) | {p._fp(p.files)}
        p.looked = set(state.get("looked") or ())
        p.stall_turns = int(state.get("stall_turns") or 0)
        p.unhashed = int(state.get("unhashed") or 0)
        return p

    def forget(self):
        """Clears session context after compaction. The model no longer retains previous returns, so re-reading is treated as new work. Historical state (e.g., tried code) is preserved to prevent regression."""
        self.seen.clear()
        self.looked.clear()
        self.idle_turns = 0
        self.stall_turns = 0


def _residency_s(host):
    """How long `host` keeps a model resident after a call: its configured keep_alive in seconds ("5m" -> 300), or
    None when it keeps it indefinitely or the value cannot be read."""
    ka = str(config.KEEP_ALIVE_OVERRIDES.get(host, config.KEEP_ALIVE) or "").strip().lower()
    try:
        if ka.endswith(("s", "m", "h")):
            return float(ka[:-1]) * {"s": 1, "m": 60, "h": 3600}[ka[-1]]
        return float(ka) if float(ka) >= 0 else None
    except ValueError:
        return None


def _mark_live(host, model, job=True):
    """Mark the scheduler's box as LIVE. \n    The claim TTL is determined by the caller type: jobs use a fixed default, while chat uses the model's resident time (keep_alive) to prevent embedding tasks from evicting active chat models. Fail-soft: write failures are ignored so they do not break the call."""
    try:
        import scheduler
        name = next((h for h, c in scheduler.CARDS.items()
                     if (c.get("url") or "").rstrip("/") == (host or "").rstrip("/")), None)
        resident_s = None if job else _residency_s(host)
        ttl = scheduler.CLAIM_TTL_DEFAULT if resident_s is None else resident_s   # keep_alive 0: nothing stays, no claim
        if name and ttl > 0:
            scheduler.claim(name, scheduler.LIVE, "hai-%s:%s" % ("job" if job else "chat",
                                                                  getattr(_CALL_CONTEXT, "user", None) or "?"),
                            ttl=ttl, note=model or "")
            marks = getattr(_CALL_CONTEXT, "live_job_marks", None)
            if job and marks is not None:
                marks.add((name, model or ""))           # the run lets go of it when it ends (live_job_scope)
    except Exception:                                                # noqa: BLE001
        pass


def track(host, model, purpose=None, user=None, kind="chat"):
    """Context manager: register an Ollama request on `host` for its duration. Used by _chat_once and by
    the side doors (embeddings, VLM probes, the unload trick) so ALL load Kirk puts on a box is visible.
    Every chat call also marks the box LIVE for placement (_mark_live; a job's claim outlasts a plain chat's)."""
    live = kind == "chat"
    job = bool(getattr(_CALL_CONTEXT, "job", False))

    class _Track:
        def __enter__(self_inner):
            if live:
                _mark_live(host, model, job=job)
            with _INFLIGHT_LOCK:
                _CALL_SEQ[0] += 1
                self_inner.cid = "%d-%d" % (int(time.time()), _CALL_SEQ[0])
                _INFLIGHT[self_inner.cid] = {
                    "id": self_inner.cid, "host": host, "model": model, "kind": kind,
                    "purpose": purpose or getattr(_CALL_CONTEXT, "purpose", None) or kind,
                    "user": user or getattr(_CALL_CONTEXT, "user", None) or "",
                    "started": time.time(), "thread": threading.current_thread().name}
            return self_inner

        def __exit__(self_inner, *exc):
            with _INFLIGHT_LOCK:
                _INFLIGHT.pop(self_inner.cid, None)
                _LAST_DONE[(host or "").rstrip("/")] = time.time()
            if live:
                if getattr(exc[1] if len(exc) > 1 else None, "unavailable", False):
                    _unmark_live(host, model)        # the box never served this model: no claim may say it does
                else:
                    _mark_live(host, model, job=job)
            return False
    return _Track()


@contextlib.contextmanager
def live_job_scope():
    """Live job claims are tied to the execution scope and must be released when the run ends to prevent TTL extensions into subsequent turns. The outermost scope on the thread owns the claims; nested runs release claims owned by their parent. Only claims matching the current user and model are released; concurrent calls on other threads retain their rolling TTLs. Plain chat claims are unaffected and retain their standard keep_alive."""
    if getattr(_CALL_CONTEXT, "live_job_marks", None) is not None:
        yield
        return
    _CALL_CONTEXT.live_job_marks = set()
    try:
        yield
    finally:
        marks, _CALL_CONTEXT.live_job_marks = _CALL_CONTEXT.live_job_marks, None
        try:
            import scheduler
            who = getattr(_CALL_CONTEXT, "user", None) or "?"
            for name, model in marks:
                c = scheduler.active_claim(name, scheduler.LIVE)
                if c and c.get("holder") == "hai-job:" + who and (c.get("note") or "") == model:
                    scheduler.release(name, scheduler.LIVE)
                    notes.log("LIVE claim released on %s: the run of %s ended (%s)" % (name, who, model))
        except Exception:                                            # noqa: BLE001 - cleanup must never raise
            pass


def _unmark_live(host, model):
    """Release the live claim set by the current call if it is still owned by this user for this model, rather than refreshing it. Fail-soft: release errors must not raise."""
    release_live(host, model, getattr(_CALL_CONTEXT, "user", None) or "?", "never served %s" % model)


def release_live(host, model, who, why):
    """Release `who`'s live claim for `model` on `host` if held, returning True on success.

    Used for failed calls (_unmark_live) and suspended runs to immediately free the slot,
    preventing other models from waiting out the TTL.

    Fail-soft: release errors are ignored to avoid blocking the caller."""
    try:
        import scheduler
        name = next((h for h, c in scheduler.CARDS.items()
                     if (c.get("url") or "").rstrip("/") == (host or "").rstrip("/")), None)
        c = scheduler.active_claim(name, scheduler.LIVE) if name else None
        if c and (c.get("note") or "") == (model or "") and str(c.get("holder") or "").endswith(":" + str(who)):
            scheduler.release(name, scheduler.LIVE)
            notes.log("LIVE claim released on %s: %s %s" % (name, c.get("holder"), why))
            return True
    except Exception:                                                # noqa: BLE001
        pass
    return False


_LAST_DONE = {}                                  # host url -> when Kirk's last request there ended


def last_call_end(host_url):
    """When Kirk's most recent request to `host_url` ended (epoch seconds), or None. The load accountant
    grants a short grace after a call: model unload / KV teardown keeps the card busy for a few seconds."""
    with _INFLIGHT_LOCK:
        return _LAST_DONE.get((host_url or "").rstrip("/"))


def inflight_snapshot():
    """Every Ollama request in flight right now, oldest first, with elapsed seconds and per-host counts."""
    now = time.time()
    with _INFLIGHT_LOCK:
        rows = [dict(r, elapsed_s=int(now - r["started"])) for r in _INFLIGHT.values()]
    rows.sort(key=lambda r: r["started"])
    by_host = {}
    for r in rows:
        by_host.setdefault(r["host"], {"calls": 0, "models": set()})
        by_host[r["host"]]["calls"] += 1
        by_host[r["host"]]["models"].add(r["model"])
    return {"rows": rows, "by_host": {h: {"calls": v["calls"], "models": sorted(v["models"])} for h, v in by_host.items()}}


def begin_execution_evidence():
    """Start collecting executor facts on this thread. Returns the live row list."""
    rows = []
    _EXECUTION_EVIDENCE.rows = rows
    return rows


def end_execution_evidence():
    """Stop and return this thread's executor facts (always safe in finally)."""
    rows = list(getattr(_EXECUTION_EVIDENCE, "rows", []) or [])
    try:
        del _EXECUTION_EVIDENCE.rows
    except AttributeError:
        pass
    return rows


def _record_execution_evidence(row):
    rows = getattr(_EXECUTION_EVIDENCE, "rows", None)
    if rows is not None:
        rows.append(dict(row))


# What a model is told the first time the factory's loop rule ends its answer (the turn loop's `looped` branch). Same
# shape as the idle-turn and final-turns notes: what happened, what to do instead, when the run ends.
LOOP_NUDGE = ("[YOUR LAST ANSWER WAS CUT OFF: it had stopped making progress - the same passage was being written over "
              "and over. Do not work the whole job out in one go. Take the next concrete step now: make one tool call, "
              "or give your answer. The run ends after %d such answers in a row.]")


class ChatError(RuntimeError):
    def __init__(self, host, model, detail, unavailable=False):
        super().__init__(detail)
        self.host = host
        self.model = model
        self.detail = detail
        self.unavailable = unavailable   # the box could not TAKE the turn (window held/refused): route elsewhere


# Only memory/context errors justify a smaller window. Any other failure must retry at the same num_ctx: changing it reloads the model, drops the prompt cache, and risks truncating the conversation, which invalidates the response.
_CTX_SHRINK_ERROR = re.compile(r"memory|alloc|\bOOM\b|out of (device )?memory|context (length|size|window)",
                               re.IGNORECASE)

# The finish gate's view of a coding loop (see chat): which tools change the code, and whether a coding_test passed.
_GATE_EDIT_TOOLS = frozenset({"coding_patch", "coding_create", "coding_file"})
_GATE_REFUSAL = ("[FINISH REFUSED: you changed the code and the tests have not passed on it since. Run coding_test, "
                 "fix what fails, and answer when it passes. If a test cannot be written or run, say exactly why.]")
_GATE_HOLLOW = ("[FINISH REFUSED: you changed code but added or changed no test for it, so a passing run proves "
                "nothing about your change. Write or update a test (tests/test_*.py) that covers it, run coding_test, "
                "and answer when it passes. If the change cannot be tested, say exactly why.]")
_GATE_UNSEEN = ("[FINISH REFUSED: %s. Render it once more with coding_render and look at the result (the sheet, or any "
                "frame with coding_look), then answer when what you see is what you mean to hand in. If it cannot be "
                "rendered, say exactly why.]")
_RETURNCODE = re.compile(r'"returncode"\s*:\s*(-?\d+)')


def _is_test_path(path):
    """A test file by the suites' own naming (unittest discover `test*.py`, or anything under a tests/ folder)."""
    p = str(path or "").replace("\\", "/")
    return p.endswith(".py") and ("/tests/" in "/" + p or p.rsplit("/", 1)[-1].startswith("test"))


# Compact when the conversation reaches 0.9 of the context window.
COMPACT_FRACTION = 0.9
COMPACT_INSTRUCTIONS = "Focus on preserving code snippets, variable names, and technical decisions."
# Summaries are built on the previous summary plus new turns. Carry forward all valid context from the prior summary to prevent loss of earlier work (e.g. tried approaches, requirements status). Length is not capped: summaries should be as detailed as the record requires.
COMPACT_RUNNING_RECORD = (
    "The conversation may open with an EARLIER SUMMARY of this same job: carry forward everything in it that is still "
    "true, so this summary never knows less about the earlier work than that one did. Keep a running record under these "
    "headings: REQUIREMENTS - each thing the original request asks for and where it stands (done, partly, not started, "
    "failing and why); TRIED - every approach taken so far and what came of it, including the ones ruled out, so none "
    "is repeated blind; FILES - each file as it is now (its path, what it holds, the functions or sections that "
    "matter); LAST CHECK - what was last seen when the work was checked (a test's result, a render, a picture) and "
    "what it showed; NEXT - the next step and why.")
_COMPACT_SYSTEM = (
    "You are compacting an agent's working conversation so it can keep going inside its context window. Write a "
    "summary of everything between the original request and the latest step: what was asked, what was done (tools "
    "called and what they returned that still matters), files changed, decisions made and why, what failed, and what "
    "remains. " + COMPACT_INSTRUCTIONS + " " + COMPACT_RUNNING_RECORD +
    " Do not invent anything; do not address the user; output the summary only.")


def _render_for_summary(msgs):
    out = []
    for m in msgs:
        role = m.get("role")
        if m.get("tool_calls"):
            calls = ", ".join("%s(%s)" % (c.get("function", {}).get("name"),
                                         json.dumps(c.get("function", {}).get("arguments", {}), default=str)[:400])
                              for c in m["tool_calls"])
            out.append("ASSISTANT called: %s" % calls)
        if m.get("content"):
            out.append("%s: %s" % (str(role).upper(), m["content"]))
    return "\n\n".join(out)


_COMPACT_NOTE = "[CONTEXT COMPACTED"


_CLOCK_LINE = re.compile(r"\[Clock: [^\]]*\]\s*$")
# A tool call that arrived as text: the engine did not parse it, so it never ran.
_TEXT_CALL = re.compile(r"<tool_call>|<function=[A-Za-z_]")
TEXT_CALL_NOTE = ("[Your last reply wrote a tool call as plain text, so it did not run and nothing was saved. Make the call "
                  "again as a tool call.]")
# SAVE_TOOLS: allow a stopped loop's final handover to execute these once, preventing loss of finished work.
SAVE_TOOLS = ("coding_create", "coding_patch", "coding_write")


def _clock_note(messages, left_s, budget_s):
    """Injects a clock notice into the newest tool result. Must only target the un-sent message so the engine's history remains immutable."""
    m = next((x for x in reversed(messages[-4:]) if x.get("role") == "tool" and isinstance(x.get("content"), str)), None)
    if m is None or _CLOCK_LINE.search(m["content"]):
        return
    left = max(0, int(left_s + 30) // 60)
    total = int(budget_s + 30) // 60
    m["content"] += "\n[Clock: %d min used of your %d min budget; %d min left.]" % (max(0, total - left), total, left)
    return True


def _compact_line(host, window):
    """Compute the token limit before compaction: 90% of the context window.

    Using a high threshold (0.9) ensures that context is preserved as much as
    possible while leaving enough room for the model's response and internal
    processing. Do not lower this value arbitrarily; a tighter limit triggers
    unnecessary compactions which degrade context quality and continuity."""
    return COMPACT_FRACTION * window


def _current_brief(messages):
    """Identify the active request by finding the last user message that is not a compaction or inbox note.

    First user messages in a reused thread may be stale context; messages sent while the run is active are status notes, not the primary task.

    This logic prevents re-executing old jobs or treating status checks as new requests."""
    return next((m for m in reversed(messages) if m.get("role") == "user"
                 and not str(m.get("content") or "").startswith((_COMPACT_NOTE, _INBOX_NOTE, _PICTURE_NOTE))), None)


# Empirical chars/token ratio (3.15) for code-heavy prompts. Used only for pre-flight estimation when the engine's exact token count is unavailable (e.g., for new windows).
COMPACT_CHARS_PER_TOKEN = 3.15
_CONTEXT_REFUSED = re.compile(r"exceeds the available context|exceeds? .*context|context (size|length)", re.I)


def _prompt_chars(messages, tools_list=None):
    """This prompt's characters: every message with its tool calls, plus the tool schemas."""
    chars = len(json.dumps(tools_list, default=str)) if tools_list else 0
    for m in messages:
        c = m.get("content")
        chars += len(c) if isinstance(c, str) else len(json.dumps(c or "", default=str))
        if m.get("tool_calls"):
            chars += len(json.dumps(m["tool_calls"], default=str))
    return chars


def _estimated_tokens(messages, tools_list=None):
    """What this prompt will weigh, from its characters."""
    return int(_prompt_chars(messages, tools_list) / COMPACT_CHARS_PER_TOKEN)


def _split_text(text, limit):
    """`text` in order, in pieces of at most `limit` characters, cut at a paragraph where one falls in the piece."""
    pieces = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit + 2)         # a break that starts exactly at the limit still counts
        cut = cut if cut > 0 else limit
        pieces.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return pieces + ([text] if text else [])


def _summarise_to_fit(host, model, text, num_ctx, keep_alive, timeout):
    """Summarises `text` into a response fitting the remaining window.
    Text exceeding the limit is split; if the joined summaries still exceed it, they are summarised again.
    Returns the summary, or "" if the model returned nothing."""
    limit = max(1, int(float(num_ctx) * COMPACT_CHARS_PER_TOKEN / 2))
    if len(text) > limit:
        parts = [_summarise_to_fit(host, model, p, num_ctx, keep_alive, timeout) for p in _split_text(text, limit)]
        joined = "\n\n".join(p for p in parts if p)
        if len(joined) > limit and len(joined) < len(text):
            return _summarise_to_fit(host, model, joined, num_ctx, keep_alive, timeout)
        return joined
    # Resume from the in-flight piece (checkpointed summaries are skipped) so the client can reconnect to the active session.
    memo = getattr(_CALL_CONTEXT, "compact_pieces", None)
    key = hashlib.sha256(("%s\n%s" % (model, text)).encode("utf-8")).hexdigest()
    if memo is not None and key in memo:
        return memo[key]
    try:
        reply = _chat_once(host, model, [{"role": "system", "content": _COMPACT_SYSTEM},
                                         {"role": "user", "content": text}],
                           [], num_ctx, keep_alive, timeout)
    except cancellation.Cancelled:
        raise
    except Exception as exc:                                         # noqa: BLE001 - only a refused size is split
        if len(text) < 2 or not _CONTEXT_REFUSED.search(str(getattr(exc, "detail", "") or exc)):
            raise
        half = _split_text(text, max(1, len(text) // 2))
        return "\n\n".join(p for p in (_summarise_to_fit(host, model, h, num_ctx, keep_alive, timeout) for h in half) if p)
    out = ((reply or {}).get("message") or {}).get("content", "").strip()
    if memo is not None:
        memo[key] = out
        saver = getattr(_CALL_CONTEXT, "compact_pieces_save", None)
        if saver is not None:
            saver()
    return out


def _compact_messages(host, model, messages, num_ctx, keep_alive, timeout, brief=None, cut=None):
    """(messages, compacted?) - the system prompt, the request and the latest step kept verbatim, everything between
    summarised. `brief` is the current request (by identity); without it, the first user message after the system
    prompt stands in for it. `cut` is the text of a turn that filled the window before finishing: it is summarised with
    the rest, last, so its reasoning and plan carry on into the turn asked again."""
    sys_n = 0
    while sys_n < len(messages) and messages[sys_n].get("role") == "system":
        sys_n += 1
    b = next((i for i, m in enumerate(messages) if m is brief), None) if brief is not None else None
    if b is None or b < sys_n:
        b = sys_n if sys_n < len(messages) and messages[sys_n].get("role") == "user" else None
    last = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=None)
    floor = (b + 1) if b is not None else sys_n
    if last is None or last <= floor:
        if not cut:
            return messages, False                  # nothing after the brief before the latest step to summarise
        last = floor                                # only the cut turn to summarise; what follows the brief stays
    middle = [m for i, m in enumerate(messages[sys_n:last], start=sys_n) if i != b]
    if cut:
        middle.append({"role": "assistant", "content": "[The turn in progress when the window ran out, unfinished: keep its "
                                                       "reasoning, its findings and the plan it was reaching]\n" + cut})
    if not middle:
        return messages, False
    summary = _summarise_to_fit(host, model, _render_for_summary(middle), num_ctx, keep_alive, timeout)
    if not summary:
        return messages, False
    note = {"role": "user", "content": _COMPACT_NOTE + " - a summary of the work so far (%d messages); the full "
                                       "transcript is kept on the server:]\n%s" % (len(middle), summary)
                                       + ("\n[Your last turn was ended while it was still thinking, before it acted: it had "
                                          "filled this window, or thought far longer than a turn that acts ever has. Act now: "
                                          "put your work in files with your tools, in pieces, instead of drafting it in your "
                                          "thinking.]" if cut else "")}
    kept = [messages[b]] if b is not None else []
    return messages[:sys_n] + kept + [note] + messages[last:], True


def _gate_task(gate, fn, args, result_text):
    """Note the coding tasks THIS run works on (a task id it passed, or the id a coding_task start returned), so its
    unverified work can be kept on its own task - never on a task some other run owns."""
    tid = (args or {}).get("task_id") if isinstance(args, dict) else None
    if tid:
        gate["tasks"].add(str(tid))
    if fn == "coding_task" and isinstance(args, dict) and args.get("action") == "start":
        try:
            data = json.loads(result_text or "")
            new = data.get("id") or (data.get("task") or {}).get("id") if isinstance(data, dict) else None
        except ValueError:
            new = None
        if new:
            gate["tasks"].add(str(new))


def _test_passed(result_text):
    """True only when a coding_test result reports returncode 0. Reads the runner's JSON reply; a reply that is not
    JSON (clipped) falls back to its top-level returncode fields. No returncode at all = not a pass."""
    try:
        data = json.loads(result_text or "")
    except ValueError:
        data = None
    if isinstance(data, dict) and "returncode" in data:
        return data.get("returncode") == 0
    codes = _RETURNCODE.findall(result_text or "")
    return bool(codes) and all(int(c) == 0 for c in codes)


def _last_user(messages):
    return next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"
                 and not str(m.get("content") or "").startswith(_PICTURE_NOTE)), "")


# Prompt prefix must remain stable to preserve KV-cache hits; the engine invalidates the entire cache if any earlier token differs.
# Therefore, tools must be appended-only (never reordered or removed) and selection notes must appear at the end of the message.
_STICKY_TOOLS = {}               # conversation key -> {"tools": [the schemas as SENT, in order], "announced": {name: hash}}
_STICKY_MAX = 512
_STICKY_LOCK = threading.Lock()
# Held tool blocks are persisted to disk with their exact text and resent verbatim to maintain prompt cache stability across restarts.
# Wording changes are announced at the end of the turn, not by reshuffling the block.
TOOL_BLOCK_DIR = os.path.join(config.STATE_DIR, "tool-blocks")
TOOL_UPDATE_NOTE = ("[Tool update: since this conversation started, these tools were changed. The definitions below "
                    "replace the earlier ones for these names; call them as defined here.]\n")
TOOL_ADDED_NOTE = ("[Tools added for this request: besides the tools listed at the start of this conversation, these are "
                   "available now. Call them by their exact name, like any other tool.]\n")
TOOL_OUTAGE_NOTE = ("[TOOL SELECTION] The tool index is unavailable for this turn, so no tools were added to the ones this "
                    "conversation already has. If this request needs a capability you were not given, say so or escalate "
                    "- do not claim it is impossible.")


def _tool_hash(tool):
    return hashlib.sha256(json.dumps(tool, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _held_get(key):
    """This conversation's held block {"tools", "announced"}: memory, else its file (a restart), else None."""
    with _STICKY_LOCK:
        rec = _STICKY_TOOLS.get(key)
    if rec is not None:
        return rec
    try:
        with open(os.path.join(TOOL_BLOCK_DIR, key + ".json"), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("tools"), list):
        return None
    with _STICKY_LOCK:
        _STICKY_TOOLS[key] = rec
    return rec


def _held_put(key, rec):
    """Keep a conversation's held block in memory and on disk. Disk write failures are logged but never raised; the block remains in memory."""
    with _STICKY_LOCK:
        _STICKY_TOOLS.pop(key, None)
        _STICKY_TOOLS[key] = rec
        while len(_STICKY_TOOLS) > _STICKY_MAX:
            _STICKY_TOOLS.pop(next(iter(_STICKY_TOOLS)))
    try:
        os.makedirs(TOOL_BLOCK_DIR, exist_ok=True)
        path = os.path.join(TOOL_BLOCK_DIR, key + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, path)
        names = [n for n in os.listdir(TOOL_BLOCK_DIR) if n.endswith(".json")]
        if len(names) > _STICKY_MAX + 64:            # retire the oldest: a held block is a cache, re-derived on demand
            paths = sorted((os.path.join(TOOL_BLOCK_DIR, n) for n in names), key=os.path.getmtime)
            for p in paths[:len(paths) - _STICKY_MAX]:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except OSError as exc:
        notes.log("TOOL BLOCK not kept on disk (%s: %s) - held in memory only" % (type(exc).__name__, exc))


def _held_forget(key):
    with _STICKY_LOCK:
        _STICKY_TOOLS.pop(key, None)
    try:
        os.remove(os.path.join(TOOL_BLOCK_DIR, key + ".json"))
    except OSError:
        pass


def held_tool_names(key):
    """The tool names a conversation holds, in order (for logs and tests)."""
    rec = _held_get(key) or {}
    return [_tool_fn_name(t) for t in rec.get("tools") or [] if _tool_fn_name(t)]
CONVERSATION_KEY_CHARS = 2000    # how much of the first user message names the conversation (its notes ride at the end)


def _tool_fn_name(tool):
    return ((tool.get("function") or {}).get("name") or "") if isinstance(tool, dict) else ""


def _opening_key(user, messages):
    """Hash the start of the first user message (not the full content) to keep the key stable.
    The content is hashed before the system note is appended on turn 1, but the history
    replays it with the note on subsequent turns; hashing the full text would cause a mismatch."""
    import hashlib
    first_sys = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
    first_user = next((m.get("content") or "" for m in messages if m.get("role") == "user"), "")[:CONVERSATION_KEY_CHARS]
    return hashlib.sha256(("%s\0%s\0%s" % (user or "", first_sys, first_user)).encode("utf-8", "replace")).hexdigest()


def conversation_key(user, messages):
    """Distinguish a chat from helper calls within it by combining the chat ID (`window_conv`) with the message opening.

    Without the chat ID, a helper call (e.g., a summary) would share the same key as the main chat turn,
    causing cache collisions or incorrect context retrieval. The opening text alone is insufficient to
    separate a chat from its internal sub-tasks."""
    import hashlib
    opening = _opening_key(user, messages)
    conv = getattr(_CALL_CONTEXT, "window_conv", None)
    if not conv:
        return opening
    return hashlib.sha256(("chat\0%s\0%s" % (conv, opening)).encode("utf-8", "replace")).hexdigest()


def _held_for(user, messages, first_turn_inherits=True):
    """This conversation's held record, or None. A chat keyed by id that has no record yet but is already under way
    (an answer in it) takes the record its opening was held under before chats were keyed by id, so no open chat re-reads
    for this change. `first_turn_inherits=False` (sticky_tools): a conversation Kirk knows only by its opening, on its
    FIRST turn, starts its own - a script or an eval that repeats one prompt is not continuing the last one."""
    under_way = any(m.get("role") == "assistant" for m in messages)
    conv = getattr(_CALL_CONTEXT, "window_conv", None)
    rec = _held_get(conversation_key(user, messages))
    if rec is None and conv and under_way:
        rec = _held_get(_opening_key(user, messages))
    if rec is not None and not conv and not under_way and not first_turn_inherits:
        return None
    return rec


MASTER_KEEPS_IN_BLOCK = ("render_person",)


def sticky_tools(user, messages, picked, available, budget, info=None, season=False, master=False):
    """Preserve the existing tool set order and append new tools that fit the token budget. Avoid reordering or reshuffling, as changes to the tool block force a full conversation re-read. If a new tool does not fit the budget, exclude it rather than evicting existing tools, unless the entire current set no longer fits (e.g., due to a smaller window), in which case start fresh from the current selection. If the `season` flag is enabled and the engine does not support listing unlisted tools in calls, append new tools to the 'seasoned' metadata instead of the tool block to prevent duplication and loss on subsequent turns."""
    by_name = {_tool_fn_name(t): t for t in available if _tool_fn_name(t)}
    key = conversation_key(user, messages)
    rec = _held_for(user, messages, first_turn_inherits=False) or {}
    held = {_tool_fn_name(t): t for t in rec.get("tools") or [] if _tool_fn_name(t)}
    # THAW: the held tools, still permitted, in the order and WITH THE TEXT this conversation was sent
    prev = [n for n in (_tool_fn_name(t) for t in rec.get("tools") or []) if n in by_name]
    seasoned_prev = {n: h for n, h in (rec.get("seasoned") or {}).items() if n in by_name and n not in prev} if prev else {}
    if prev and tool_selection._approx_tokens([held[n] for n in prev] + [by_name[n] for n in seasoned_prev]) > budget:
        prev, seasoned_prev = [], {}
    if prev and info and info.get("unsure"):
        picked = [by_name[info["raw_top"]]] if info.get("raw_sure") and info.get("raw_top") in by_name else []
    if prev and info and info.get("outage"):
        # the selector could not judge relevance (embeddings out): its list is the whole pool or the core, not a pick.
        # A held conversation keeps exactly what it has this turn - the outage is a minute, a grown block or 90 seasoned
        # definitions are for the rest of the chat - and the model is told so at the end of the turn.
        picked = []
        info["outage_note"] = TOOL_OUTAGE_NOTE

    def _text(n):
        return held[n] if n in prev_set else by_name[n]
    prev_set = set(prev)
    names = list(prev)
    for n in (_tool_fn_name(t) for t in picked):
        if not n or n in names or n in seasoned_prev:
            continue
        if prev and tool_selection._approx_tokens([_text(x) for x in names] + [by_name[x] for x in seasoned_prev]
                                                  + [by_name[n]]) > budget:
            continue                   # a growing set may not outgrow the window's share; a fresh pick is select()'s
        names.append(n)
    # Core tools are always offered, regardless of scores or budget limits; they are appended after standard growth.
    for n in (info or {}).get("core") or ():
        if n in by_name and n not in names and n not in seasoned_prev:
            names.append(n)
    seasoned = dict(seasoned_prev)
    added = [n for n in names if n not in prev_set] if prev else []

    def _engine_seasons():             # asked only when it matters (a held block gaining, or holding seasoned tools)
        try:
            return bool(season() if callable(season) else season)
        except Exception as exc:                                          # noqa: BLE001 - unknown is the old growth
            notes.log("SEASON CAPABILITY unread (%s): the block grows instead" % type(exc).__name__)
            return False
    _seasons = _engine_seasons() if (added or seasoned) else False
    if prev and _seasons:
        # SEASON the growth: the block stays exactly as sent; the new tools ride at the end of this turn
        names = list(prev)
        if added and info is not None:
            info["season_note"] = TOOL_ADDED_NOTE + "\n".join(
                json.dumps(by_name[n].get("function") or by_name[n], ensure_ascii=False) for n in added)
            for n in added:
                seasoned[n] = _tool_hash(by_name[n])
    elif seasoned:
        # this engine cannot call a tool it was not listed: the conversation's seasoned tools join its block
        names += [n for n in seasoned if n not in names]
        seasoned = {}
    elif (not prev and master and info is not None and not info.get("outage")
          and [n for n in info.get("core") or () if n in by_name] and _engine_seasons()):
        # The master block is the canonical core for new conversations; it must be shared across all first-turn requests to ensure the window's saved start covers the full prompt prefix for caching efficiency.
        # No seed is set here or by the window, so each request still rolls its own sampling.
        # A picked person renderer stays in the block to prevent regression to the default generate_image tool on small models where the consent path relies on render_person.
        core_names = [n for n in info["core"] if n in by_name]
        # the one regression that test found: on the 9B a real person's picture went to generate_image 5 of 5 where
        # today's shape chose render_person 3 of 5 - the consent path. A picked person renderer stays IN the block
        # (that chat reads cold, as it does today).
        block = core_names + [n for n in names if n in MASTER_KEEPS_IN_BLOCK and n not in core_names]
        extra = [n for n in names if n not in block]
        names = block
        if extra:
            info["season_note"] = TOOL_ADDED_NOTE + "\n".join(
                json.dumps(by_name[n].get("function") or by_name[n], ensure_ascii=False) for n in extra)
            for n in extra:
                seasoned[n] = _tool_hash(by_name[n])
        info["master_tools"] = len(core_names) if block == core_names else 0
    out = [_text(n) for n in names]
    # SEASON a rewording too: a held tool whose current wording differs is announced ONCE, at the end of this turn
    # (info["update_note"], added by chat()), never by rewriting the block above the conversation
    announced = {n: h for n, h in (rec.get("announced") or {}).items() if n in prev_set} if prev else {}
    changed = [n for n in prev if _tool_hash(held[n]) != _tool_hash(by_name[n])
               and announced.get(n) != _tool_hash(by_name[n])]
    changed += [n for n in seasoned_prev if n in seasoned and seasoned_prev[n] != _tool_hash(by_name[n])]
    if changed and info is not None:
        info["update_note"] = TOOL_UPDATE_NOTE + "\n".join(
            json.dumps(by_name[n].get("function") or by_name[n], ensure_ascii=False) for n in changed)
        for n in changed:
            if n in seasoned:
                seasoned[n] = _tool_hash(by_name[n])
            else:
                announced[n] = _tool_hash(by_name[n])
    _held_put(key, {"tools": out, "announced": announced, "seasoned": seasoned})
    return out


def seasoned_tool_names(user, messages):
    """The tools this conversation was described at the end of a turn rather than sent in its block. Never raises: it only
    ever adds names, so a failure here must not cost the turn (test_turn_stall's import explosion)."""
    try:
        return list(((_held_for(user, messages) or {}).get("seasoned") or {}))
    except Exception as exc:                                              # noqa: BLE001
        notes.log("seasoned tools unread (%s): the block alone" % type(exc).__name__)
        return []


def _coding_tools_after_open(fn, args, result, picked, available, budget, job=False, called=()):
    """A workspace open selects the permitted coding set, discarding the previous selection.

    Only schemas the caller already permitted are eligible. Never exceed the
    selector's context budget. Read-only survey callers remain read-only because
    their available pool contains no editing tools. When `job` is true, the tool
    block becomes a strict set: the permitted coding toolkit, always-offered tools,
    and already-called tools. Non-coding tools previously pulled in are dropped
    because the block is already being updated to include the coding tools."""
    if fn == "host_admin_list" and isinstance(result, str) and result.startswith("Registered admin hosts:"):
        wanted = tools._INFRA_HOSTADMIN_TOOLS
    elif fn == "coding_workspace" and args.get("action") == "open" and args.get("workspace"):
        try:
            data = json.loads(result) if isinstance(result, str) else result
        except (ValueError, TypeError):
            return picked, None
        if not isinstance(data, dict) or data.get("error") or data.get("id") != args.get("workspace") or data.get("read") is not True:
            return picked, None
        wanted = tools.ENGINEERING_TOOLS
    else:
        return picked, None
    coding_names = {_tool_fn_name(t) for t in wanted}
    held = picked
    if job and fn == "coding_workspace":
        keep = (coding_names | tool_selection.CORE_TOOL_NAMES | tool_selection.CAPABILITY_ENTRY_NAMES
                | {str(n) for n in called or ()})
        picked = [t for t in picked if _tool_fn_name(t) in keep]
    have = {_tool_fn_name(t) for t in picked}
    expanded = list(picked) + [t for t in available if _tool_fn_name(t) in coding_names - have]
    if tool_selection._approx_tokens(expanded) > budget:
        return held, "[Coding toolkit could not fit this model's tool budget; use a larger-context coding tier.]"
    return expanded, None


def note_at_end(messages, note):
    """This turn's note appended to its own (last) user message; a conversation with no user message keeps the old
    placement (a system message after the first)."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            messages[i] = dict(messages[i], content=(messages[i].get("content") or "") + "\n\n" + note)
            return messages
    at = 1 if (messages and messages[0].get("role") == "system") else 0
    messages.insert(at, {"role": "system", "content": note})
    return messages


_PROBE_QUIET = {}                  # base URL -> monotonic time until which a quick probe answers "could not ask" at once
_PROBE_QUIET_LOCK = threading.Lock()


def _base(url):
    m = re.match(r"^(https?://[^/]+)", str(url or ""))
    return m.group(1) if m else str(url or "")


def probe_quiet(url):
    """True while `url`'s box is in a quiet window following a recent probe timeout. This allows callers to return 'could not ask' immediately without re-attempting a known-hung host."""
    with _PROBE_QUIET_LOCK:
        return _PROBE_QUIET.get(_base(url), 0.0) > time.monotonic()


def probe_result(url, exc=None):
    """Record a quick probe's outcome: a timeout opens the quiet window, any answer closes it."""
    with _PROBE_QUIET_LOCK:
        if exc is None:
            _PROBE_QUIET.pop(_base(url), None)
        elif isinstance(exc, httpx.TimeoutException):
            _PROBE_QUIET[_base(url)] = time.monotonic() + float(getattr(config, "PROBE_QUIET_S", 60.0))


def _quick_get(url):
    """One quick probe through the quiet window. Raises like httpx.get (TimeoutException while quiet)."""
    if probe_quiet(url):
        raise httpx.ReadTimeout("quiet: %s timed out within the last %.0f s" % (_base(url), config.PROBE_QUIET_S))
    try:
        r = httpx.get(url, timeout=config.REACH_TIMEOUT)
    except Exception as exc:
        probe_result(url, exc)
        raise
    probe_result(url)
    return r


def reachable(host):
    try:
        _quick_get(f"{host}/api/version")
        return True
    except Exception:
        return False


def reachable_direct(host):
    """Bypasses the quiet window to ensure a live probe is sent (required for the lead's wake path).
    A timeout here re-arms the window for other callers."""
    try:
        r = httpx.get(f"{host}/api/version", timeout=config.REACH_TIMEOUT)
    except Exception as exc:
        probe_result(host, exc)
        return False
    probe_result(host)
    return r.status_code < 500


def port_open(host, timeout=2.0):
    """True when the box ACCEPTS a TCP connection on the model port - awake, whatever HTTP says. Asleep/off is a connect
    failure. The pair (port open, HTTP silent) is the lead's documented stall ("looks alive and is not",
    RUNBOOK-GPU-Host-Power-and-Glances § sleep-crash) - never a dead ollama to restart over ssh."""
    import socket
    from urllib.parse import urlsplit
    u = urlsplit(host if "://" in str(host) else f"http://{host}")
    try:
        with socket.create_connection((u.hostname, u.port or 11434), timeout=timeout):
            return True
    except OSError:
        return False


def models_loaded(host):
    """Names of models currently resident on a host (GET /api/ps)."""
    try:
        r = _quick_get(f"{host}/api/ps")
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


def models_resident(host):
    """models_loaded, but None when the host could not be asked: a placement COST must not read "could not tell" as
    "nothing resident" and charge a cold load that may not happen."""
    try:
        r = _quick_get(f"{host}/api/ps")
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return None


def model_state(host):
    """For the dashboard: (model_name, state) for a single Ollama host.
    state = hot (resident in GPU VRAM) / warm (loaded in RAM or partial CPU offload) /
    cold (reachable, nothing loaded) / offline (unreachable, e.g. The lead asleep)."""
    try:
        r = _quick_get(f"{host}/api/ps")
        models = r.json().get("models", [])
    except Exception:
        return ("—", "offline")
    if not models:
        return ("—", "cold")
    m = models[0]
    name = m.get("name") or "—"
    size = m.get("size") or 0
    vram = m.get("size_vram") or 0
    if vram > 0 and vram >= size:
        state = "hot"
    else:
        state = "warm"          # loaded but CPU-only or partial GPU offload
    return (name, state)


def gpu_stats(glances_base):
    """Returns (mem_pct, util_pct) from Glances GPU, or (None, None) on error."""
    try:
        r = httpx.get(f"{glances_base}/gpu", timeout=config.REACH_TIMEOUT)
        g = r.json()
        if isinstance(g, list) and g:
            return float(g[0].get("mem", 100)), float(g[0].get("proc", 100))
    except Exception:
        pass
    return None, None


def _explain(messages, host, model, num_ctx, why="too many tool iterations", run=None, tools_list=None):
    """Ask a model to explain, in plain language, why the tool loop didn't finish. `why` is the loop's actual stop reason. If `run` and save tools are provided, the model may make ONE save call before explaining to prevent losing uncommitted work."""
    saves = [t for t in (tools_list or []) if (t.get("function") or {}).get("name") in SAVE_TOOLS] if run else []
    saved = []
    if saves:
        ask = list(messages) + [{"role": "user", "content": (
            "The tool loop has stopped (reason: %s). If work you made is not saved yet, save it now with ONE tool call; "
            "otherwise say what you were doing, what you found and why you could not finish." % why)}]
        try:
            with track(host, model, purpose="tool-loop handoff save", user=getattr(_CALL_CONTEXT, "chat_user", None)):
                _m = _chat_once_raw(host, model, ask, saves, num_ctx,
                                    config.KEEP_ALIVE_OVERRIDES.get(host, config.KEEP_ALIVE))["message"]
            done = []
            for c in _m.get("tool_calls") or []:
                fn = (c.get("function") or {}).get("name")
                args = (c.get("function") or {}).get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                if fn in SAVE_TOOLS and isinstance(args, dict):
                    res = str(run(fn, args))
                    saved.append("%s %s" % (fn, args.get("path") or ""))
                    done.append({"role": "tool", "content": res})
            if done:
                messages = list(messages) + [dict(_m, content=_m.get("content") or "")] + done
                notes.log("TOOL-LOOP HANDOFF saved before stopping host=%s model=%s: %s" % (host, model, "; ".join(saved)))
        except Exception as exc:                                               # noqa: BLE001 - the explanation still comes
            notes.log("TOOL-LOOP HANDOFF save failed host=%s model=%s: %s" % (host, model, str(exc)[:200]))
    msgs = list(messages) + [{
        "role": "user",
        "content": (
            "The tool loop has stopped without a final answer (reason: %s). " % why +
            "Briefly explain in plain language what you were trying to do, what you "
            "found out so far (if anything), and why you weren't able to finish. Then "
            "give your best answer with whatever information you do have."
        ),
    }]
    try:
        # the same supervised call as every turn (it reads the whole long conversation, so it must not be clocked)
        with track(host, model, purpose="tool-loop handoff", user=getattr(_CALL_CONTEXT, "chat_user", None)):
            _m = _chat_once_raw(host, model, msgs, [], num_ctx,
                                config.KEEP_ALIVE_OVERRIDES.get(host, config.KEEP_ALIVE))["message"]
        # Fallback to `thinking` field; some models (e.g., qwen3-vl) ignore "think": False
        # and place content there even in error paths.
        _c, _t, _ = split_leaked_thinking(_m.get("content") or "", _m.get("thinking") or "")
        answer = ((_c or "").strip() or (_t or "").strip())
        if len(answer) >= 2:
            return answer + ("\n\n_Saved before stopping: %s._" % "; ".join(saved) if saved else "")
        notes.log("TOOL-LOOP HANDOFF empty host=%s model=%s (%d chars)" % (host, model, len(answer)))
    except Exception as exc:   # a failed handoff must say why: the run's findings are lost if it returns None
        notes.log("TOOL-LOOP HANDOFF failed host=%s model=%s: %s: %s" % (host, model, type(exc).__name__,
                                                                         str(exc)[:300]))
    return None


def _chat_once(host, model, messages, tools_list, num_ctx, keep_alive, timeout=None, think=False):
    """Registered wrapper: every /api/chat turn Kirk makes appears in the in-flight registry for exactly as
    long as the request is open, whatever called it. See _INFLIGHT above."""
    with track(host, model, kind="chat", user=getattr(_CALL_CONTEXT, "chat_user", None)):
        return _chat_once_raw(host, model, messages, tools_list, num_ctx, keep_alive, timeout, think=think)


def split_leaked_thinking(content, thinking=""):
    """Returns (content, thinking, leaked). Some models emit reasoning into `content` even with think=False;
    everything before the last `</think>` is leaked thinking."""
    if "</think>" not in (content or ""):
        return content, thinking, False
    head, _, tail = content.rpartition("</think>")
    head = head.strip()
    if head.startswith("<think>"):
        head = head[len("<think>"):].strip()
    return tail.strip(), "\n".join(x for x in ((thinking or "").strip(), head) if x), True


# Supervise silent calls by box load, not a timeout: Ollama sends no data while loading large models, so a clock kills valid work.
# Wait if the GPU is busy; kill if our model is resident but idle. Fall back to the clock only if load data is missing.
SILENCE_POLL_S = 10.0       # how often a silent call looks at its box's report: a check cadence, never a limit


class ModelStalled(httpx.ReadTimeout):
    """A silent call ended on the box's own evidence: our model resident, the card idle, no output."""


def _card_for(host):
    """The scheduler's name for the box at `host` (a URL), or None."""
    try:
        import scheduler
        h = (host or "").rstrip("/")
        for name, card in scheduler.CARDS.items():
            if (card.get("url") or "").rstrip("/") == h:
                return name
    except Exception:
        pass
    return None


def _load_report(card):
    """The box's last pushed load record (scheduler.gpu_load), or None when there is none to ask."""
    if not card:
        return None
    try:
        import scheduler
        return scheduler.gpu_load(card)
    except Exception:
        return None


def _report_age(rec, now):
    try:
        return now - datetime.datetime.fromisoformat(str(rec.get("ts"))).timestamp()
    except Exception:
        return None


def _resident(model, loaded):
    want = (model or "").removesuffix(":latest")
    return any((m or "").removesuffix(":latest") == want for m in (loaded or []))


def _swap_state(host, model):
    """llama-swap's state for `model` on `host` ("starting", "ready", ... as its /running says; "absent" when not
    running; "unknown" when it cannot be asked), or None when the model is not served through llama-swap."""
    base = config.swap_base(host, model)
    if not base:
        return None
    try:
        for r in (httpx.get(base + "/running", timeout=5).json() or {}).get("running") or []:
            if r.get("model") == model:
                return str(r.get("state") or "unknown")
        return "absent"
    except Exception:                                                # noqa: BLE001
        return "unknown"


def silence_verdict(silent_since, now, rec, model, idle_seen, clock_s, busy_pct, fresh_s, streak, swap_state=None):
    """Decide the status of a silent model call. PURE.

    Returns:
    - "starting" is treated as active loading, not idle.
    - None: Keep waiting (still loading or not enough data yet).
    - "idle": Model is resident and GPU has been idle for `streak` consecutive reports.
    - "blind": No valid report to vouch for the call, and the timeout clock has run out.

    `idle_seen` tracks consecutive idle reports; any busy report resets this count to zero.
    `swap_state` (from llama-swap) indicates if the model is being served by the swap engine.
    A model served by swap is NOT in the ollama list, so its residency is determined by `swap_state` status,
    not by ollama's presence or timing."""
    if swap_state == "starting":
        return None                  # the engine says it is loading: keep waiting, however long the load takes
    age = None
    if rec and not rec.get("stale") and rec.get("util_pct") is not None:
        age = _report_age(rec, now)
    if age is None or age > fresh_s:
        return "blind" if now - silent_since >= clock_s else None
    if now - age < silent_since:
        return None                  # taken before the silence began: it says nothing about it; the next push will
    if rec["util_pct"] >= busy_pct:
        idle_seen.clear()
        return None
    if swap_state is None:
        resident = _resident(model, rec.get("ollama_loaded"))
    else:
        resident = swap_state == "ready"
    if not resident:
        # not loaded (yet): a cold load reads idle on the card, so the report cannot vouch for the call either way
        return "blind" if now - silent_since >= clock_s else None
    idle_seen.add(str(rec.get("ts")))
    return "idle" if len(idle_seen) >= streak else None


class _SilenceWatch:
    """Watches one streamed call from a side thread and ends it when its request is cancelled (a Stop now works during
    a silent read: cancellation.check() only runs between chunks) or when silence_verdict() says so. Ending = shutting
    the socket down, the one thing that wakes a read blocked before ollama has sent a single header."""

    def __init__(self, host, model, clock_s):
        self.host, self.model, self.clock_s = host, model, float(clock_s)
        self.card = _card_for(host)
        self.event = cancellation.current_event()
        self.last = time.time()
        self.longest = 0.0          # longest silence a byte ended - carried by status where the clock would have cut
        self.verdict, self.detail = None, ""
        self._sock = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="silence-watch", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._done.set()

    def beat(self):
        now = time.time()
        self.longest = max(self.longest, now - self.last)
        self.last = now

    def socket(self, sock):
        with self._lock:
            self._sock = sock
            ended = self.verdict is not None
        if ended:
            self._shutdown()

    def _shutdown(self):
        with self._lock:
            sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _end(self, verdict, detail):
        with self._lock:
            if self.verdict is not None:
                return
            self.verdict, self.detail = verdict, detail
        self._shutdown()

    def _run(self):
        try:
            import scheduler
            busy, fresh, streak = scheduler.GPU_BUSY_UTIL_PCT, scheduler.GPU_LOAD_FRESH_S, scheduler.GPU_LOAD_STREAK
        except Exception:
            busy, fresh, streak = 20.0, 180.0, 2
        idle_seen, since = set(), self.last
        while not self._done.wait(SILENCE_POLL_S):
            try:
                if self.event is not None and self.event.is_set():
                    self._end("cancelled", "the request was cancelled")
                    return
                if self.last != since:
                    since = self.last
                    idle_seen.clear()
                now = time.time()
                if now - since < SILENCE_POLL_S:
                    continue
                rec = _load_report(self.card)
                v = silence_verdict(since, now, rec, self.model, idle_seen, self.clock_s, busy, fresh, streak,
                                    swap_state=_swap_state(self.host, self.model))
                if v == "idle":
                    self._end(v, "no output for %.0f s while %s sat loaded on %s's idle card (%d load reports, "
                                 "util %s%%)" % (now - since, self.model, self.card, len(idle_seen),
                                                 (rec or {}).get("util_pct")))
                    return
                if v == "blind":
                    self._end(v, "no output for %.0f s and no load report from %s that could vouch for the call "
                                 "- the %.0f s clock applies" % (now - since, self.card or self.host, self.clock_s))
                    return
            except Exception:
                pass                 # a watch that cannot judge keeps waiting; it never takes the call down itself


def _socket_tracer(watch):
    """httpcore `trace` hook that hands the connection's socket to the silence watch at connect (top-level
    httpx.stream / httpx.post cannot take request extensions, hence a client of our own)."""
    def trace(event, info):
        if watch is None or event != "connection.connect_tcp.complete":
            return
        try:
            stream = info.get("return_value")
            sock = stream.get_extra_info("socket") if stream is not None else None
            if sock is not None:
                watch.socket(sock)
        except Exception:
            pass
    return trace


def _raise_for_watch(watch, host, model, exc):
    """If the watch ended the call, raise WHY, in the type the caller already handles (a Stop is a Stop; a stall or
    the fallback clock reads as a timeout to the retry logic). Otherwise return, and the caller re-raises."""
    if watch.verdict == "cancelled":
        raise cancellation.Cancelled() from exc
    if watch.verdict in ("idle", "blind"):
        try:
            notes.log("MODEL CALL %s host=%s model=%s: %s" % ("STALLED" if watch.verdict == "idle" else "CLOCKED",
                                                             host, model, watch.detail))
        except Exception:
            pass
        raise (ModelStalled if watch.verdict == "idle" else httpx.ReadTimeout)(watch.detail) from exc


def _note_long_read(watch, host, model, silent_s):
    if silent_s >= watch.clock_s:
        try:
            notes.log("LONG READ host=%s model=%s: %.0f s with no output while the box reported work - the "
                      "old %.0f s clock would have ended this call" % (host, model, silent_s, watch.clock_s))
        except Exception:
            pass


def _chat_window_on(user):
    import chat_window
    return chat_window.enabled_for(user)


def _window_protocol(url):
    """A turn a chat window can carry: ollama /api/chat, or Strata's OpenAI wire at a box's llama-swap base."""
    import chat_window
    return url.rstrip("/").endswith("/api/chat") or chat_window.is_swap_url(url)


@contextlib.contextmanager
def _open_chat_stream(url, body, clock_s, watch=None):
    """POST a streamed /api/chat on a client of our own, so the silence watch can reach the connection's socket. No
    read clock: the watch decides when a silent call is over. connect / write / pool keep the clock.
    With config.CHAT_WINDOWS the turn runs in the box's factory WINDOW order instead (chat_window.py) and the same
    lines come back from its stream file; a call outside any run holds the window for this call only."""
    who = getattr(_CALL_CONTEXT, "chat_user", None) or getattr(_CALL_CONTEXT, "user", None)
    if config.CHAT_WINDOWS and _chat_window_on(who) and _window_protocol(url):
        import chat_window
        run = getattr(_CALL_CONTEXT, "window_run", None)
        key = run or "call:" + uuid.uuid4().hex
        try:
            with chat_window.open_stream(url, body, watch, key, conv=getattr(_CALL_CONTEXT, "window_conv", None),
                                         cut_ok=bool(getattr(_CALL_CONTEXT, "reask_parked", False)),
                                         shared_tools=getattr(_CALL_CONTEXT, "master_tools", 0)) as resp:
                yield resp
        finally:
            if run is None:
                chat_window.release(key)
        return
    with httpx.Client(timeout=httpx.Timeout(clock_s, read=None)) as client:
        with client.stream("POST", url, json=body, extensions={"trace": _socket_tracer(watch)}) as resp:
            yield resp


def post_supervised(url, *, json=None, timeout=None):
    """Non-streamed POST monitored by the same silence logic as streamed requests.
    The response is received only at the end, so the call is treated as stalled if no fresh load report arrives while the model is active."""
    body = json
    host = url.split("/api/", 1)[0]
    model = (body or {}).get("model")
    watch = _SilenceWatch(host, model, timeout if timeout is not None else config.CHAT_TIMEOUT).start()
    try:
        with httpx.Client(timeout=httpx.Timeout(watch.clock_s, read=None)) as client:
            resp = client.post(url, json=body, extensions={"trace": _socket_tracer(watch)})
        _note_long_read(watch, host, model, time.time() - watch.last)
        return resp
    except Exception as exc:
        _raise_for_watch(watch, host, model, exc)
        raise
    finally:
        watch.stop()


def _free_swap(host):
    """Before an ollama call on a box whose card llama-swap may hold (Strata: card + RAM to itself), free it: one cheap
    GET /running, and an unload only when something is running. Fail-open with a note - a llama-swap that cannot be
    asked must not stop the box's ollama models (the card then shows as UNACCOUNTED load on the deck, a report)."""
    base = config.swap_box_base(host)
    if not base:
        return
    try:
        running = (httpx.get(base + "/running", timeout=5).json() or {}).get("running") or []
        if running:
            httpx.post(base + "/api/models/unload", timeout=180)
            notes.log("SWAP FREED %s for an ollama call (was: %s)" % (base, ", ".join(r.get("model", "?") for r in running)))
    except Exception as exc:                                         # noqa: BLE001
        notes.log("SWAP FREE could not ask %s (%r) - the ollama call goes ahead" % (base, exc))


_PICTURE_MIME = (("iVBOR", "image/png"), ("/9j/", "image/jpeg"), ("R0lG", "image/gif"), ("UklG", "image/webp"))


def _picture_part(image_b64):
    """One base64 picture as an OpenAI `image_url` part, its type read from the data's own first bytes."""
    mime = next((m for head, m in _PICTURE_MIME if image_b64.startswith(head)), "image/png")
    return {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, image_b64)}}


def _call_arguments(raw, final=None):
    """Returns the arguments as a dict. If the raw string is invalid JSON (e.g. truncated due to max_tokens), it is treated as a cut-off call and must not be executed or sent back to the engine."""
    try:
        a = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {CUT_CALL_KEY: len(raw), "ended_at_the_window": final == "length"}
    return a if isinstance(a, dict) else {CUT_CALL_KEY: len(raw), "ended_at_the_window": final == "length"}


def _openai_messages(messages, pictures=False):
    """ollama chat messages -> OpenAI chat/completions messages: an assistant's tool_calls get ids and JSON-string
    arguments, each following `tool` message takes the next id (ollama keeps them in call order). Thinking is not
    sent (earlier thinking is not part of the prompt on ollama either). A message's `images` go as `image_url` parts
    only with `pictures` - the caller's word that this model is SERVED with its vision encoder (can_see): Strata reads
    pictures that way (on AMD through its CPU encoder), and a server started without the encoder refuses them."""
    out, pending = [], []
    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for j, tc in enumerate(m["tool_calls"]):
                fn = (tc or {}).get("function") or {}
                args = fn.get("arguments")
                cid = "call_%d_%d" % (i, j)
                calls.append({"id": cid, "type": "function", "function": {
                    "name": fn.get("name") or "", "arguments": json.dumps(_call_arguments(args) if isinstance(args, str) else (args or {}))}})
            pending = [c["id"] for c in calls]
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            out.append({"role": "tool", "content": _tool_result_content(m.get("content")),
                        "tool_call_id": pending.pop(0) if pending else "call_orphan_%d" % i})
        elif pictures and m.get("images"):
            out.append({"role": role, "content": [_picture_part(b) for b in m["images"]]
                        + [{"type": "text", "text": m.get("content") or ""}]})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def swap_think_kwargs(think):
    """HAI's `think` -> Strata's chat_template_kwargs (the factory's infer_ollama.template_kwargs, measured as served):
    False = thinking off; a level = that reasoning_effort; True = the template's own default (Flash-Next: xhigh)."""
    if think is False or think is None:
        return {"enable_thinking": False}
    if isinstance(think, str):
        return {"enable_thinking": True, "reasoning_effort": think}
    return {}


def _chat_once_swap(base, host, model, messages, tools_list, num_ctx, timeout, think):
    """_chat_once_raw for a model served through llama-swap (config.SWAP_MODELS): the same turn - live deltas, silence
    watch, cancellation, the in-flight registry (the caller's track wrapper) - over OpenAI chat/completions, returned in
    ollama's shape. No num_ctx (the engine serves its tested context) and no keep_alive (llama-swap's ttl)."""
    content, thinking, calls, final, usage, tm = [], [], {}, None, {}, {}
    turn = next(_TURN_SEQ)
    _delta({"kind": "turn", "turn": turn, "model": model, "host": host,
            "purpose": getattr(_CALL_CONTEXT, "purpose", None) or ""})
    finished = False
    gen_tokens, gen_first = 0, None
    clock_s = timeout if timeout is not None else config.CHAT_TIMEOUT
    watch = _SilenceWatch(host, model, clock_s).start()
    body = {"model": model, "messages": _openai_messages(messages, pictures=can_see(host, model) is True), "stream": True,
            "stream_options": {"include_usage": True}}
    if tools_list:
        body["tools"] = tools_list
    kw = swap_think_kwargs(think)
    if kw:
        body["chat_template_kwargs"] = kw
    try:
        with _open_chat_stream(base + "/v1/chat/completions", body, clock_s, watch) as resp:
            if resp.status_code >= 400:
                resp.read()
                resp.raise_for_status()
            for line in resp.iter_lines():
                watch.beat()
                cancellation.check()
                line = (line or "").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                if obj.get("error"):
                    raise ValueError("llama-swap/strata: %s" % ((obj["error"] or {}).get("message") or obj["error"]))
                for ch in obj.get("choices") or []:
                    d = ch.get("delta") or {}
                    if d.get("content") or d.get("reasoning_content") or d.get("tool_calls"):
                        gen_tokens += 1
                        gen_first = gen_first or time.time()
                        _gen_note(host, tokens=gen_tokens, first=gen_first)
                    if d.get("content"):
                        content.append(d["content"])
                        _delta({"kind": "content", "turn": turn, "text": d["content"]})
                    if d.get("reasoning_content"):
                        thinking.append(d["reasoning_content"])
                        _delta({"kind": "thinking", "turn": turn, "text": d["reasoning_content"]})
                    for tc in d.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", len(calls)), {"name": "", "arguments": ""})
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
                    final = ch.get("finish_reason") or final
                tm = obj.get("timings") or tm
                usage = obj.get("usage") or usage
        if final is None:
            raise ValueError("stream ended without a finish_reason (content_len=%d) - transient, retryable"
                             % len("".join(content)))
        finished = True
    except Exception as exc:
        _raise_for_watch(watch, host, model, exc)
        raise
    finally:
        watch.stop()
        if finished:
            _note_long_read(watch, host, model, watch.longest)
        ms = lambda k: int(float(tm[k]) * 1e6) if tm.get(k) is not None else None   # prompt_n is only non-cached reads; cache_n holds the prefix. prompt_eval_count is the sum, which compaction must weigh against.
        read_n, cache_n = tm.get("prompt_n"), tm.get("cache_n")
        prompt_total = (read_n + cache_n if read_n is not None and cache_n is not None
                        else usage.get("prompt_tokens", read_n))
        _gen_note(host, eval_count=tm.get("predicted_n", usage.get("completion_tokens")) if finished else None,
                  eval_duration=ms("predicted_ms") if finished else None,
                  prompt_count=prompt_total if finished else None,
                  prompt_duration=ms("prompt_ms") if finished else None,
                  num_ctx=num_ctx if finished else None,
                  prompt_cached=cache_n if finished else None)
        _delta({"kind": "turn_end" if finished else "discard", "turn": turn,
                "done_reason": final if finished else None})
    tool_calls = []
    for i in sorted(calls):
        a = _call_arguments(calls[i]["arguments"], final)
        tool_calls.append({"function": {"name": calls[i]["name"], "arguments": a}})
        _delta({"kind": "tool_call", "turn": turn, "name": calls[i]["name"], "args": _tool_result_content(a)})
    text, thought, leaked = split_leaked_thinking("".join(content), "".join(thinking))
    message = {"role": "assistant", "content": text}
    if thought:
        message["thinking"] = thought
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"message": message, "served_model": model,
            "done_reason": {"tool_calls": "stop", "length": "length"}.get(final, final),
            "prompt_eval_count": prompt_total,
            "prompt_eval_cached_count": cache_n,
            "eval_count": tm.get("predicted_n", usage.get("completion_tokens")),
            "think_leaked": leaked}


RUNAWAY = "runaway"      # a turn's done_reason when the runaway guard stopped it (config.TOLKIEN_RUNAWAY_THINK)


def _runaway_cap_chars(host, tools_list):
    """Characters of THINKING after which a turn with no tool call and no answer text is a runaway, or 0 for no guard.
    A job on the Tolkien box that can act (it holds tools); nothing else. Counted in characters because a window relays what the
    engine drafted, which is not always one token a line; the lab's own 3.15 characters a token turns the measured
    token figure into it."""
    if (tools_list and getattr(_CALL_CONTEXT, "job", False)
            and str(host or "").rstrip("/") == config.TOLKIEN_WINDOW_URL and config.TOLKIEN_RUNAWAY_THINK):
        return int(config.TOLKIEN_RUNAWAY_THINK * COMPACT_CHARS_PER_TOKEN)
    return 0


def _chat_once_raw(host, model, messages, tools_list, num_ctx, keep_alive, timeout=None, think=False):
    """One /api/chat turn, read as a STREAM so a cancel can abort mid-generation (closing the stream makes
    Ollama stop and frees the lead slot). Accumulates the streamed chunks back into the same response
    shape the caller expects from the old stream=False call: {"message": {...}, "done_reason", counts}.
    cancellation.check() between chunks raises Cancelled, which exits the `with` block and closes the socket.
    A SILENT call (no byte yet, or none for a while) is judged by its box's status, not a clock - see
    _SilenceWatch above. timeout overrides the global CHAT_TIMEOUT, which is now only the fallback clock for a
    box with no fresh load report."""
    swap = config.swap_base(host, model)
    if swap:
        return _chat_once_swap(swap, host, model, messages, tools_list, num_ctx, timeout, think)
    _free_swap(host)
    content, thinking, tool_calls, final = [], [], [], {}
    # Live viewers (#37) get every chunk as it arrives. A turn that fails is DISCARDED for them, because
    # the caller retries it as a new turn and the partial text never becomes part of the answer.
    turn = next(_TURN_SEQ)
    _delta({"kind": "turn", "turn": turn, "model": model, "host": host,
            "purpose": getattr(_CALL_CONTEXT, "purpose", None) or ""})
    finished = False
    gen_tokens, gen_first = 0, None
    runaway_at, thought_chars, ran_away = _runaway_cap_chars(host, tools_list), 0, False
    clock_s = timeout if timeout is not None else config.CHAT_TIMEOUT
    watch = _SilenceWatch(host, model, clock_s).start()
    try:
        with _open_chat_stream(f"{host}/api/chat", {
                "model": model, "messages": messages, "tools": tools_list,
                "stream": True, "think": think, "keep_alive": keep_alive,   # lead_think(): on unless the model cannot think
                "options": {"num_ctx": num_ctx},
        }, clock_s, watch) as resp:
            if resp.status_code >= 400:
                resp.read()
                resp.raise_for_status()
            for line in resp.iter_lines():
                watch.beat()
                cancellation.check()
                if not line:
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message") or {}
                if msg.get("content") or msg.get("thinking") or msg.get("tool_calls"):
                    gen_tokens += 1        # ollama streams one token per chunk
                    gen_first = gen_first or time.time()
                    _gen_note(host, tokens=gen_tokens, first=gen_first)
                if msg.get("content"):
                    content.append(msg["content"])
                    _delta({"kind": "content", "turn": turn, "text": msg["content"]})
                if msg.get("thinking"):
                    thinking.append(msg["thinking"])
                    thought_chars += len(msg["thinking"])
                    _delta({"kind": "thinking", "turn": turn, "text": msg["thinking"]})
                if msg.get("tool_calls"):
                    tool_calls.extend(msg["tool_calls"])
                    for tc in msg["tool_calls"]:
                        fn = (tc or {}).get("function") or {}
                        _delta({"kind": "tool_call", "turn": turn, "name": str(fn.get("name") or ""),
                                "args": _tool_result_content(fn.get("arguments") or {})})
                if chunk.get("done"):
                    final = chunk
                elif runaway_at and thought_chars >= runaway_at and not content and not tool_calls:
                    # the runaway guard: leaving this block closes the stream, which ends the generation on the box
                    # (the window's CANCEL), and the caller summarises the thought and asks the turn again
                    ran_away = True
                    final = {"done_reason": RUNAWAY, "model": model, "eval_count": gen_tokens}
                    break
        # If the stream ends without a 'done' chunk, it is a cut generation (server/proxy closed early).
        # Raising ValueError triggers the caller's retry logic to regenerate the turn,
        # preventing silent partial-state shipping. A downgraded-but-complete turn on retry
        # is preferable to a mid-plan termination.
        if not final:
            raise ValueError(f"stream ended without a done chunk (content_len={len(''.join(content))}, "
                             f"tool_calls={len(tool_calls)}) - transient, retryable")
        finished = True
    except Exception as exc:
        _raise_for_watch(watch, host, model, exc)      # the watch shut the socket: say WHY
        raise
    finally:
        watch.stop()
        if finished:
            _note_long_read(watch, host, model, watch.longest)
        _gen_note(host, eval_count=final.get("eval_count") if finished else None,
                  eval_duration=final.get("eval_duration") if finished else None,
                  prompt_count=final.get("prompt_eval_count") if finished else None,
                  prompt_duration=final.get("prompt_eval_duration") if finished else None,
                  prompt_cached=final.get("prompt_eval_cached_count") if finished else None,
                  num_ctx=num_ctx if finished else None)
        _delta({"kind": "turn_end" if finished else "discard", "turn": turn,
                "done_reason": final.get("done_reason") if finished else None})
    text, thought, leaked = split_leaked_thinking("".join(content), "".join(thinking))
    message = {"role": "assistant", "content": text}
    if thought:
        message["thinking"] = thought
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "message": message,
        # Ollama emits the model identity on the terminal stream chunk. Preserve
        # it so an executor can record what answered rather than echoing config.
        "served_model": final.get("model"),
        "done_reason": final.get("done_reason"),
        "prompt_eval_count": final.get("prompt_eval_count"),
        "eval_count": final.get("eval_count"),
        "think_leaked": leaked,           # the model thought despite think=False; the split above cleaned it
    }


def _status(callback, message):
    if callback:
        try:
            callback(message)
        except Exception:
            pass


def _tool_snag(result):
    text = str(result)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        if payload.get("error"):
            return text[:220].replace("\n", " ")
        if payload.get("timed_out") is True or payload.get("returncode") not in (None, 0):
            return text[:220].replace("\n", " ")
        return None
    if isinstance(payload, list):
        # A list is DATA (search_docs hits, rows): words like "failed"/"error" inside a retrieved document are its
        # content, not the tool failing (10-16: every runbook search showed "Tool snag" on the live status line).
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in (
        " error", "failed", "rejected", "unprocessable", "not found", "conflict",
    )):
        return text[:220].replace("\n", " ")
    return None


def _tool_result_content(result):
    """Coerce a tool result to a string for the ollama `tool` message. `/api/chat` rejects a
    non-string `content` (object -> 'cannot unmarshal ... of type string'); register-based tools
    return a payload dict, so JSON-encode it — the model still sees the preview and token."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        return str(result)


@_checkpointed
def chat(host, model, messages, tools_list, num_ctx=None, status_callback=None, user=None, keep_alive=None,
         timeout=None, persona=None, telemetry=None, heartbeat=None, transcript=None,
         thoughts=None):
    # keep_alive override: callers loading a transient/auxiliary model (e.g. the MedGemma critique pass)
    # pass keep_alive="0" so it unloads immediately and never lingers in VRAM as a stray resident model.
    # timeout override: the fallback clock for a silent call whose box has no load report (None = global
    # CHAT_TIMEOUT); a box that reports work is waited on, whatever this says (_SilenceWatch).
    effective_keep_alive = keep_alive if keep_alive is not None else config.KEEP_ALIVE_OVERRIDES.get(host, config.KEEP_ALIVE)
    # Label this thread's in-flight rows with who asked (the tile shows it). Overwritten on every chat()
    # entry so a reused worker thread never carries a previous caller's name; a purpose set via
    # call_context() is kept, otherwise the registry falls back to the request kind.
    _CALL_CONTEXT.chat_user = user or ""
    _CALL_CONTEXT.master_tools = 0           # set below for a new chat's master block; a reused thread never inherits it
    _pre = [("enter", time.monotonic())]     # where a turn's seconds go BEFORE its request is sent (PRE-TURN, below)
    _lead = lead_model(host, model, getattr(_CALL_CONTEXT, "job", False))
    if _lead != model:
        notes.log("LEAD MODEL host=%s %s -> %s (policy crown, job-shaped call)" % (host, model, _lead))
        try:
            # the crown is a job-family preference, not a hard pin: on failure, the call falls back to the box's standing model
            return chat.__wrapped__(host, _lead, messages, tools_list, config.ctx_for(host, _lead, num_ctx),
                                    status_callback=status_callback, user=user, keep_alive=keep_alive,
                                    timeout=timeout, persona=persona, telemetry=telemetry, heartbeat=heartbeat,
                                    transcript=transcript, thoughts=thoughts)
        except ChatError as exc:
            if not getattr(exc, "unavailable", False):
                raise
            notes.log("LEAD MODEL host=%s %s could not serve (%s) -> the box's standing model %s"
                      % (host, _lead, str(getattr(exc, "detail", exc))[:160], model))
            if status_callback:
                status_callback("The lead's job model could not serve — answering on %s" % model)
    # Dispatch-time collision gate: concurrent generation on a second GPU context
    # during rendering poisons the context. Calls must wait until the GPU is idle;
    # distinguish short renders (wait) from long training jobs (refuse immediately)
    # to avoid stale-state stalls or proceeding while busy.
    _gate_busy = None
    if host == config.HEAVY_HOST:
        try:
            import comfy_client as _cc
            _long = ()
            try:
                import p20_queue as _pq
                # Use gpu_status_any() to tolerate stale heartbeats during multi-hour
                # training runs; treat status as valid if <24h old (matches comfy_client).
                _raw, _age = _pq.gpu_status_any()
                _st = (_raw or {}).get("state") if (_age is not None and _age <= 86400) else None
                _long = (_st,) if _st in ("training", "training-pause", "finishing") else ()
            except Exception:
                _st = None
            if _long:
                # Hours, not minutes. Fail fast so the caller can downgrade to the brain box instead of
                # queueing behind a run that will still be going long after any sane timeout.
                _gate_busy = "%s's GPU is %s — not waiting" % (config.LEAD_BOX_LABEL, _st)
            else:
                _gate_t0 = time.time()
                _noted = False
                while _cc.render_busy() and time.time() - _gate_t0 < config.COLLISION_GATE_WAIT:
                    if not _noted:
                        _noted = True
                        if status_callback:
                            status_callback("⏳ %s's GPU is mid-render — heavyweight call "
                                            "queued behind it (collision guard)" % config.LEAD_BOX_LABEL)
                    time.sleep(10)
                if _noted:
                    notes.log("collision gate: held %s dispatch %.0fs (user=%r model=%s)"
                              % (config.LEAD_BOX_LABEL, time.time() - _gate_t0, user, model))
                if _cc.render_busy():
                    _gate_busy = "%s's GPU is still busy after %ds" % (config.LEAD_BOX_LABEL,
                                                                      config.COLLISION_GATE_WAIT)
        except Exception as _gate_exc:
            # Fail-open: a broken gate must not fail silently (swallowing the exception).
            # If logging fails, we still let the call through rather than hard-failing.
            _gate_busy = None
            try:
                notes.log("COLLISION GATE BROKEN (failing open): %r" % (_gate_exc,))
            except Exception:
                pass
    if _gate_busy:
        notes.log("collision gate REFUSED: %s (user=%r model=%s)" % (_gate_busy, user, model))
        raise ChatError(host, model, _gate_busy)
    _pre.append(("gate", time.monotonic()))
    messages = list(messages)
    user_text = _last_user(messages)
    tools_list = tools.tools_for(tools_list, user)   # prune tools to fit context window; fail-safe: keep full list if pruning fails or is unnecessary
    _pre.append(("permit", time.monotonic()))
    _available = tools_list
    _held_before = None
    try:
        _select_info = {}
        # the block as it stood BEFORE this turn: sticky_tools writes this turn's, and a restart's held text must come
        # from what the conversation was sent, never from a record written a moment ago by this same turn
        _held_before = _held_for(user, messages)
        _state_tools = tools.live_state_tools(user)
        _pre.append(("state", time.monotonic()))
        tools_list, _select_note = tool_selection.select(
            tools_list, user, user_text, num_ctx or config.NUM_CTX,
            persona=persona, state_tools=_state_tools,
            history=tool_selection.recent_context(messages), info=_select_info)
        _pre.append(("select", time.monotonic()))
        # append-stable chat: the conversation keeps the tools it was sent, and the note goes at the end (above)
        # master: a NEW chat starts from the block every new chat shares. Not a job: a job's block becomes its coding set
        # when it opens its workspace (11-12), and its first message would carry every seasoned definition for good.
        tools_list = sticky_tools(user, messages, tools_list, _available,
                                  int((num_ctx or config.NUM_CTX) * config.TOOL_SELECT_BUDGET_FRAC), info=_select_info,
                                  season=lambda: can_season(host, model),
                                  master=not bool(getattr(_CALL_CONTEXT, "job", False)))
        _CALL_CONTEXT.master_tools = int(_select_info.get("master_tools") or 0)
        if _CALL_CONTEXT.master_tools:
            notes.log("MASTER START: a new chat's block is the %d always-offered tools every new chat shares; %d picked "
                      "for this message ride at its end host=%s model=%s user=%r" % (
                          _CALL_CONTEXT.master_tools, (_select_info.get("season_note") or "").count("\n"), host, model, user))
        _pre.append(("block", time.monotonic()))
        if _select_info.get("update_note"):
            # changed tool wording rides at the END of this turn's message (append-stable), so the block stays thawed
            messages = note_at_end(messages, _select_info["update_note"])
            notes.log("TOOL UPDATE announced at the end of the turn (block kept as sent) user=%r" % (user,))
        if _select_info.get("season_note"):
            # the tools this turn adds ride at the END of its message: the block above the conversation never grows
            messages = note_at_end(messages, _select_info["season_note"])
            notes.log("TOOLS SEASONED at the end of the turn (block kept as sent, %d tools) host=%s model=%s user=%r" % (
                _select_info["season_note"].count("\n"), host, model, user))
        if _select_info.get("outage_note"):
            # a held conversation in an index outage: it kept its own block, so the selector's "only core tools" (or no
            # note at all, for the whole pool) would describe a list it was not sent
            _select_note = _select_info["outage_note"]
            notes.log("TOOL INDEX OUT: the conversation keeps its held block this turn (%d tools) user=%r" % (
                len(tools_list), user))
        if _select_note:
            messages = note_at_end(messages, _select_note)
    except Exception as exc:
        notes.log("tool selection skipped (%s): %s" % (type(exc).__name__, exc))
    if _pre[-1][1] - _pre[0][1] >= 1.0:
        # Log phase breakdowns only if the total pre-turn overhead exceeds 1 second.
        notes.log("PRE-TURN host=%s model=%s user=%r %s" % (host, model, user, " ".join(
            "%s=%.1fs" % (name, t - _pre[i][1]) for i, (name, t) in enumerate(_pre[1:]))))
    # A call back after a restart sends the very list its conversation was sent.
    _ckpt = getattr(_CALL_CONTEXT, "ckpt_slot", None)
    _saved_tools = _ckpt.saved_tool_names() if _ckpt is not None else None
    if _saved_tools:
        _by_name = {_tool_fn_name(t): t for t in _available if _tool_fn_name(t)}
        # prefer held tool text over live definitions to preserve the exact prompt wording used in the original context
        _key = conversation_key(user, messages)
        _held_rec = _held_get(_key) or {}
        _held_by = {_tool_fn_name(t): t for t in (_held_before or {}).get("tools") or [] if _tool_fn_name(t)}
        _kept = [_held_by.get(n) or _by_name[n] for n in _saved_tools if n in _by_name]
        if _kept:
            tools_list = _kept
            _held_put(_key, {"tools": _kept, "announced": _held_rec.get("announced") or {}})
            notes.log("TOOL LIST kept across the restart: %d of %d tools (%d with their held text)" % (
                len(_kept), len(_saved_tools), sum(1 for n in _saved_tools if n in _held_by and n in _by_name)))
    # what this turn's own message finally said (notes included), for the history to keep verbatim (app.py)
    _CALL_CONTEXT.sent_user = _last_user(messages)
    tool_names = {
        item.get("function", {}).get("name")
        for item in tools_list
        if isinstance(item, dict)
    } | set(seasoned_tool_names(user, messages))     # a seasoned coding tool makes a coding loop as a listed one does
    _is_coding = any(name and name.startswith("coding_") and name != "coding_workspace" for name in tool_names)
    # JOBs (app._is_job) get the lead's priced effort; everything else stays off. Do not switch to `_is_coding`: it misclassifies admin chat (which has coding tools) as a job, breaking effort selection.
    _pin = getattr(_CALL_CONTEXT, "think_pin", None)
    _think = pinned_think(model, _pin)
    if _think is not None:          # the user's own effort pin wins over the job rule (#37, decided 10-07)
        if _think and not can_think(host, model):
            _think = False          # ollama refuses a think flag for a model without the capability
        notes.log("PINNED EFFORT host=%s model=%s think=%r (the user's pin: %s)" % (host, model, _think, _pin))
    elif placed_effort(host, model, getattr(_CALL_CONTEXT, "placed_think", None)) is not None:
        _think = placed_effort(host, model, _CALL_CONTEXT.placed_think)
        notes.log("PLACED EFFORT host=%s model=%s think=%r (the menu row: %s)" % (
            host, model, _think, _CALL_CONTEXT.placed_think))
    else:
        _think = lead_think(host, model, getattr(_CALL_CONTEXT, "job", False))
        notes.log("LEAD EFFORT host=%s model=%s think=%r" % (host, model, _think))
    # Open-ended loops (coding jobs, general jobs) have no fixed step cap. They terminate via idle detection (no new work for CODING_IDLE_TURNS), the job deadline, or when the model stops. Do not apply a static step limit here.
    _open_loop = _is_coding or bool(getattr(_CALL_CONTEXT, "job", False))
    loop_max = None if _open_loop else config.TOOL_LOOP_MAX
    _progress = _LoopProgress() if _open_loop else None
    _deadline = getattr(_CALL_CONTEXT, "loop_deadline", None) if _open_loop else None
    _turn_s = []
    _ended = "turns"
    # FINISH GATE (#37 work loop; the same rule A1 measures, a1_harness FINISH_GATE): on a JOB that changed code, the
    # model's final answer is refused until coding_test has passed on the code as it now stands — the tests decide,
    # not the model. A model that keeps answering without a passing run is doing no new work: after
    # CODING_IDLE_TURNS refusals in a row its answer ships, labelled as unverified.
    _conv_tokens = 0   # the conversation's size after the last model call (its prompt + its reply), for compaction
    _cuts_in_a_row = 0       # turns that filled the window with no tool call since the last one that made a call
    _text_calls_in_a_row = 0  # turns that wrote a tool call as text, since the last real call
    _cut_turn = None         # that turn's text, for the summary that makes the room
    _compact_pending = False  # a restart cut a compaction: redo it first (its checkpoint says so)
    _force_compact = False    # a stopped runaway: summarise its thought with the conversation before the turn is asked again
    _pieces = {}              # that compaction's finished piece summaries, by the piece's hash
    _counted_chars = 0     # how many characters that count covered: what was added since is estimated
    _gate = {"on": bool(_is_coding and getattr(_CALL_CONTEXT, "job", False)), "edited": False, "green": False,
             "refused": 0, "tasks": set()}
    # How this run is working (run_habits): counted for every run; said back and asked by the gate only where switched on.
    _habits = run_habits.Habits()
    # Teletrack turns, infer/tool time, and tool distribution for loops.
    _tele = {"turns": 0, "infer_s": 0.0, "tool_s": 0.0, "tools": {}, "host": host, "model": model}   # The tool trail records full arguments and results (args_full/result_full) because deliberation critics
# judge the answer against this evidence; truncated trails would leave them blind.
# args/result are short display copies; compact_trail() strips the full fields before the trail leaves the process.
    _TR_ARGS, _TR_RESULT = 180, 220

    def _tr(tool_name, args, result_text, ms):
        if transcript is None:
            return
        try:
            args_s = json.dumps(args, ensure_ascii=True, default=str)
        except Exception:
            args_s = str(args)
        full = str(result_text or "")
        transcript.append({"step": _tele["turns"], "tool": str(tool_name),
                           "args": args_s[:_TR_ARGS], "result": full[:_TR_RESULT],
                           "args_full": args_s, "result_full": full, "ms": int(ms)})

    _TH_CAP, _TH_LEN = 40, 1200

    def _th(msg_obj):
        if thoughts is None or len(thoughts) >= _TH_CAP:
            return
        t = (msg_obj.get("thinking") or "").strip()
        if t:
            thoughts.append({"step": _tele["turns"], "thinking": t[:_TH_LEN]})

    def _drive_tele():
        if telemetry is not None:
            telemetry.update(_tele)
        if _is_coding and _tele["turns"]:
            try:
                notes.log("DRIVE_TELE host=%s model=%s turns=%d infer=%.1fs tool=%.1fs tools=%s" % (
                    host, model, _tele["turns"], _tele["infer_s"], _tele["tool_s"], _tele["tools"]))
                if _habits.line():
                    notes.log("HABITS host=%s model=%s %s" % (host, model, _habits.line()))
            except Exception:
                pass

    step = 0
    # Run ledger (#37): resume from the last natural point - before a model turn, or between two of a turn's tools.
    # Recorded tool results are kept and never re-run (the workspace and orders they touched survived the restart).
    _slot = getattr(_CALL_CONTEXT, "ckpt_slot", None)
    _resume = _slot.resume_point() if _slot is not None else None
    if _slot is not None:
        _slot.tool_names = [_tool_fn_name(t) for t in tools_list if _tool_fn_name(t)]
        _slot.served = {"host": host, "model": model, "num_ctx": num_ctx}     # a resumed run goes back to this row

    _media = {"pending": [], "rejected": []}

    def _loop_state():
        """State persisted with every checkpoint so a resume restores the finish gate and other invariants that are not visible in the conversation log."""
        g = dict(_gate)
        g["tasks"] = sorted(g.get("tasks") or ())
        return {"gate": g, "habits": _habits.state(), "cuts": _cuts_in_a_row, "text_calls": _text_calls_in_a_row, "media": _media,
                "progress": _progress.state() if _progress is not None else None,
                "counts": {k: _tele[k] for k in ("compactions", "out_of_room", "runaways", "text_calls", "finish_refused", "nudges_deadline")
                           if k in _tele}}

    _reattach, _reattach_at = {}, [None]   # a tool a restart cut, to be finished: {index: handle or None} in turn _reattach_at[0]
    _NOT = object()

    def _run_calls(calls, step, first=0):
        """Run a turn's tool calls from index `first`; returns (escalation or None, any new work). Before each tool
        the conversation is checkpointed (a natural point: a deploy may park here) and the tool marked started."""
        nonlocal tools_list, _is_coding, loop_max, _progress, _deadline
        any_new = False
        _pictures = []          # what this turn's tools offered the model to look at; added after the last result
        for i, c in enumerate(calls[first:], first):
            fn = c["function"]["name"]
            args = c["function"].get("arguments", {}) or {}
            if fn not in {_tool_fn_name(t) for t in tools_list}:
                # a seasoned tool (described in the conversation, not in the block): the engine had no schema to type it
                args = _typed_args(next((t for t in _available if _tool_fn_name(t) == fn), None), args)
            # the live status the chat page and the HAI-Thinking tile show; once a run has rendered or written a file it
            # also says how the run is working (run_habits), in the short form
            _how = _habits.short()
            _status(status_callback, (f"Tool step {step}/{loop_max}: {fn}" if loop_max is not None
                                      else f"Tool step {step}: {fn}") + ((" · " + _how) if _how else ""))
            if fn == "escalate_to_mothership":
                return ("lead", args.get("reason", "escalation requested")), any_new
            if fn == "escalate_to_claude":
                return ("claude", args.get("reason", "escalation requested")), any_new
            if _slot is not None:
                _slot.save(messages, step, tools_done=i, loop=_loop_state())
                if _PARK.is_set():
                    _park_here()
                _suspend_here(_slot)
                _slot.tool_started(step, i)
            cancellation.check()
            _tt = time.monotonic()
            _gen_step(host, tool=fn)
            _CALL_CONTEXT.serving, _CALL_CONTEXT.pictures = (host, model), []
            _attach = _reattach.pop(i, _NOT) if _reattach_at[0] == step else _NOT
            if _slot is not None:
                _CALL_CONTEXT.tool_handle = (lambda h, _s=step, _i=i: _slot.tool_handle(_s, _i, h))
            try:
                result = (tools.resume_tool(fn, args, _attach) if _attach is not _NOT
                          else tools.run_tool(fn, args, user_text, user=user, persona=persona))
            finally:
                _CALL_CONTEXT.tool_handle = None
                _pictures.extend(getattr(_CALL_CONTEXT, "pictures", None) or [])
                _CALL_CONTEXT.serving, _CALL_CONTEXT.pictures = None, None
            _elapsed_tool = time.monotonic() - _tt
            _gen_step(host, tool=fn, tool_ms=int(_elapsed_tool * 1000))
            _tele["tool_s"] += _elapsed_tool
            _tele["tools"][fn] = _tele["tools"].get(fn, 0) + 1
            _tr(fn, args, result, _elapsed_tool * 1000)
            snag = _tool_snag(result)
            _rtext = _tool_result_content(result)
            _held_names = [_tool_fn_name(t) for t in tools_list]
            _expanded, _coding_note = _coding_tools_after_open(fn, args, result, tools_list, _available,
                int((num_ctx or config.NUM_CTX) * config.TOOL_SELECT_BUDGET_FRAC),
                job=bool(getattr(_CALL_CONTEXT, "job", False)), called=set(_tele["tools"]))
            _now_names = [_tool_fn_name(t) for t in _expanded]
            if _now_names != _held_names:
                _let_go = [n for n in _held_names if n not in set(_now_names)]
                if _let_go:
                    # the block is REPLACED here (a job's set), so the conversation's held list starts over from it;
                    # sticky_tools would otherwise put every held name back in front
                    _held_forget(conversation_key(user, messages))
                _mid_info = {}
                tools_list = sticky_tools(user, messages, _expanded, _available,
                    int((num_ctx or config.NUM_CTX) * config.TOOL_SELECT_BUDGET_FRAC), info=_mid_info,
                    season=(lambda: can_season(host, model)) if not _let_go else False)
                _seasoned_mid = _mid_info.get("season_note")
                if fn == "coding_workspace":
                    _is_coding, loop_max = True, None
                    _deadline = getattr(_CALL_CONTEXT, "loop_deadline", None)
                    if _progress is None:
                        _progress = _LoopProgress()
                    _gate["on"] = bool(getattr(_CALL_CONTEXT, "job", False))
                if _slot is not None:
                    _slot.tool_names = [_tool_fn_name(t) for t in tools_list if _tool_fn_name(t)]
                _now_callable = [_tool_fn_name(t) for t in tools_list] + [
                    n for n in seasoned_tool_names(user, messages) if n not in {_tool_fn_name(t) for t in tools_list}]
                _coding_note = ("[Available tools now: " + ", ".join(_now_callable) +
                    ". These tools are callable in this conversation now. Use the actual tool response to identify a limitation; do not report a listed tool as unavailable. Workspace boundaries and the existing human approval gates remain enforced.]")
                if _seasoned_mid:
                    # mid-turn growth seasoned too: the definitions ride on this tool result, the block stays as sent
                    _coding_note += "\n" + _seasoned_mid
                if _let_go:
                    _coding_note += ("\n[This is a coding job now, so these were taken off your list (you had not called them): "
                                     + ", ".join(_let_go) + ".]")
                notes.log("CODING TOOLSET loaded after workspace open: %d tools%s user=%r (~%d tok%s)" % (
                    len(_now_callable), (" (%d seasoned, block kept)" % (len(_now_callable) - len(tools_list)))
                    if _seasoned_mid else "", user, tool_selection._approx_tokens(tools_list),
                    ("; let go %d: %s" % (len(_let_go), ", ".join(_let_go))) if _let_go else ""))
            if _coding_note:
                _rtext += "\n" + _coding_note
            _said = _habits.note(fn, args, _rtext, snag=bool(snag))
            if _said and tools.habit_on("meter"):
                _rtext += "\n" + _said
            if _slot is not None and _habits.line():
                run_habits.LIVE[_slot.ledger.run_id] = _habits.line()
            _delta({"kind": "tool_result", "name": fn, "ms": int(_elapsed_tool * 1000), "snag": bool(snag),
                    "result": _rtext[:_LIVE_RESULT_PREVIEW], "chars": len(_rtext)})
            if snag:
                _status(status_callback, f"Tool snag at {fn}; model is trying to recover: {snag}")
            if _progress is not None:
                any_new = _progress.call(fn, args, ok=not snag, result=result) or any_new
            if _gate["on"]:
                _gate_task(_gate, fn, args, _rtext)
                if fn in _GATE_EDIT_TOOLS and not snag:
                    _gate["edited"], _gate["green"] = True, False
                    _p = str(args.get("path") or args.get("destination") or "")
                    if _is_test_path(_p):
                        _gate["tests"] = True
                    elif _p.endswith(".py"):
                        _gate["code"] = True
                elif fn == "coding_test":
                    _gate["green"] = _test_passed(_rtext)
            messages.append({"role": "tool", "content": _rtext})
        if _pictures:
            import copy
            first_picture = len(messages)
            _add_pictures(messages, _pictures)
            _media["pending"] = [{"index": i, "message": copy.deepcopy(messages[i])}
                                  for i in range(first_picture, len(messages))]
            notes.log("PICTURES host=%s model=%s step=%s: %s" % (host, model, step, ", ".join(p[0] for p in _pictures)))
        return None, any_new

    if _resume:
        messages = list(_resume["messages"])
        step = max(0, _resume["step"] - 1)
        _compact_pending, _cut_turn = bool(_resume.get("compact")), _resume.get("cut")
        _pieces = dict(_resume.get("pieces") or {}) if _compact_pending else {}
        _saved_loop = _resume.get("loop") or {}
        _media.update(_saved_loop.get("media") or {})
        if _saved_loop.get("gate"):
            _gate.update(_saved_loop["gate"])
            _gate["tasks"] = set(_gate.get("tasks") or ())
        if _saved_loop.get("habits"):
            _habits.s.update(run_habits.Habits(_saved_loop["habits"]).s)   # the same object the loop's closures hold
        _cuts_in_a_row = int(_saved_loop.get("cuts") or 0)
        _text_calls_in_a_row = int(_saved_loop.get("text_calls") or 0)
        if _progress is not None and _saved_loop.get("progress"):
            _progress = _LoopProgress.restore(_saved_loop["progress"])
        _tele.update(_saved_loop.get("counts") or {})
        _asked = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
        if _resume.get("tools_done") is not None and _asked and _asked.get("tool_calls"):
            # Saved between two tools of turn `step`: the model already chose them - run the rest, do not re-ask.
            step = _resume["step"]
            _calls, _first = _asked["tool_calls"], int(_resume["tools_done"])
            if _resume.get("in_tool") and _first < len(_calls):
                _cut = _calls[_first]["function"]["name"]
                _handle = _resume.get("handle")
                if tools.can_resume(_cut) and (_handle is not None or _cut in tools.CHECKED_WRITES):
                    # it waited on an order that went on without us (its handle), or its effect can be read back from
                    # the workspace: finish it exactly once instead of writing it off
                    _reattach[_first], _reattach_at[0] = _handle, step
                    notes.log("TOOL %s cut by a restart at step %d: %s" % (
                        _cut, step, "waiting for the same work" if _handle is not None else "checking what it did"))
                elif _cut in REPLAYABLE_TOOLS:
                    notes.log("TOOL %s cut by a restart at step %d: it only reads, so it runs again" % (_cut, step))
                else:
                    messages.append({"role": "tool", "content": (
                        "[%s was interrupted by an orchestrator restart before it returned. Its result was lost and it "
                        "was NOT run again: whatever it changes may or may not have happened. Check before repeating "
                        "it.]" % _cut)})
                    _delta({"kind": "tool_result", "name": _cut, "ms": 0, "snag": True,
                            "result": "interrupted by a restart; not re-run", "chars": 0})
                    _first += 1
            _esc, _ = _run_calls(_calls, step, _first)
            if _esc:
                return None, _esc
    _brief = _current_brief(messages)   # the request this run answers; compaction keeps it, not the thread's first
    while True:
        step += 1
        if loop_max is not None and step > loop_max:
            break
        _window = num_ctx or config.NUM_CTX
        # Ollama silently drops oldest messages if the prompt exceeds the window, risking loss of critical context (e.g., briefs).
        # Weigh the prompt before sending: always for jobs, and for chat once a baseline count exists.
        # Reuse the engine's token count when available to avoid the cost of a full prompt re-read.
        if _conv_tokens or getattr(_CALL_CONTEXT, "job", False):
            _chars_now = _prompt_chars(messages, tools_list)
            _weighed_tokens = (_conv_tokens + int((_chars_now - _counted_chars) / COMPACT_CHARS_PER_TOKEN)
                               if _conv_tokens else int(_chars_now / COMPACT_CHARS_PER_TOKEN))
            if _weighed_tokens > _conv_tokens and _weighed_tokens >= _compact_line(host, _window):
                notes.log("CONTEXT heavy before sending host=%s model=%s: ~%d tokens for a %d window"
                          % (host, model, _weighed_tokens, _window))
                _conv_tokens = _weighed_tokens
        if _compact_pending or _force_compact or (_conv_tokens and _conv_tokens >= _compact_line(host, _window)):
            # Checkpoint before summarizing: ensures a restart mid-compaction redoes the summary and preserves prior context.
            if _slot is not None:
                _slot.save(messages, step, compact=True, cut=_cut_turn, loop=_loop_state(), pieces=_pieces)
                _CALL_CONTEXT.compact_pieces = _pieces
                _CALL_CONTEXT.compact_pieces_save = lambda: _slot.save(messages, step, compact=True, cut=_cut_turn,
                                                                       loop=_loop_state(), pieces=_pieces)
            if _compact_pending:
                notes.log("CONTEXT COMPACTION redone after a restart host=%s model=%s step=%d" % (host, model, step))
            else:   # the start, so an audit can see a compaction a restart landed inside
                notes.log("CONTEXT COMPACTING host=%s model=%s step=%d at %d of %d tokens%s" % (
                    host, model, step, _conv_tokens, _window, ", with a cut turn" if _cut_turn else ""))
            try:
                messages, _did = _compact_messages(host, model, messages, _window, effective_keep_alive, timeout,
                                                   brief=_brief, cut=_cut_turn)
            except cancellation.Cancelled:
                raise
            except Exception as exc:   # fail-soft: the turn runs uncompacted; an overflow then errors as before
                _did = False
                notes.log("CONTEXT COMPACTION failed (%s): %s" % (type(exc).__name__, exc))
            finally:
                _CALL_CONTEXT.compact_pieces = _CALL_CONTEXT.compact_pieces_save = None
            if _did:
                notes.log("CONTEXT COMPACTED host=%s model=%s step=%d at %d of %d tokens (%.0f%%)"
                          % (host, model, step, _conv_tokens, _window, 100.0 * _conv_tokens / _window))
                _delta({"kind": "tool_result", "name": "context_compacted", "ms": 0, "snag": False,
                        "result": "conversation summarised at %d of %d tokens to keep working" % (_conv_tokens, _window),
                        "chars": 0})
                _tele["compactions"] = _tele.get("compactions", 0) + 1
                if _progress is not None:
                    _progress.forget()
            _conv_tokens = 0
            _cut_turn = None
            _compact_pending = _force_compact = False
            _pieces = {}
        if _slot is not None:
            _drain_inbox(_slot, messages)
            _slot.save(messages, step, loop=_loop_state())
            if _PARK.is_set():
                _park_here()
            _suspend_here(_slot)
        if YIELD_HOOK is not None:
            # A natural point between model turns: a job holding the lead lets a waiting quick request go first
            # (app._LeadSlot.yield_to_quick; a no-op for anything else).
            try:
                YIELD_HOOK(status_callback)
            except cancellation.Cancelled:
                raise
            except Exception as exc:
                notes.log("yield hook failed (ignored): %r" % (exc,))
        if _deadline is not None and time.monotonic() >= _deadline:
            _ended = "deadline"
            break
        _turn_t0 = time.monotonic()
        _gen_step(host, turn=step)
        _tele["turns"] = step
        if heartbeat is not None and config.CODING_HEARTBEAT_TURNS \
                and step % config.CODING_HEARTBEAT_TURNS == 0:
            try:
                heartbeat(step, dict(_tele["tools"]))
            except Exception:
                pass
        # One warning when the job's deadline is closer than three of this run's own average turns.
        _noted = False
        if _deadline is not None and _turn_s and not _tele.get("nudges_deadline") and \
                _deadline - time.monotonic() < 3 * (sum(_turn_s) / len(_turn_s)):
            _tele["nudges_deadline"] = 1
            messages.append({"role": "tool", "content": (
                "[FINAL TURNS: this job's time budget ends in about three of your turns. Make the "
                "workspace SAFE now: complete the current edit, run compile + the unit profile, and "
                "CHECKPOINT with an honest note of anything unfinished.]")})
            _noted = True
        if _deadline is not None and getattr(_CALL_CONTEXT, "loop_budget", None):
            _noted = bool(_clock_note(messages, _deadline - time.monotonic(), _CALL_CONTEXT.loop_budget)) or _noted
        if _noted and _slot is not None:
            # Checkpoint includes the notes to ensure the restart sends the same bytes and reconnects the active turn
            _slot.save(messages, step, loop=_loop_state())
        cancellation.check()   # stop before starting another model turn if the user cancelled
        if loop_max is not None and step == max(1, int(loop_max * 0.8)):
            _status(status_callback, f"Still working: used {step - 1}/{loop_max} tool steps")
        parsed = None
        last_error = None
        requested_ctx = num_ctx or config.NUM_CTX
        context_attempts = [
            requested_ctx,
            max(8192, requested_ctx // 2),
            8192,
        ]
        _ti = time.monotonic()
        _ctx_used = None
        _shrink = 0   # how far down the ladder: moves ONLY on a memory error (a different num_ctx reloads the model)
        _looped = 0   # answers to THIS turn that the factory's loop rule ended, in a row
        attempt = -1
        while attempt < config.CHAT_RETRIES:
            attempt += 1
            try:
                attempt_ctx = context_attempts[min(_shrink, len(context_attempts) - 1)]
                if attempt_ctx < requested_ctx:
                    notes.log(
                        f"CTX DOWNGRADE host={host} model={model} step={step} attempt={attempt + 1} "
                        f"requested={requested_ctx} using={attempt_ctx} "
                        f"({attempt_ctx * 100 // max(1, requested_ctx)}% of the requested window) "
                        f"after={last_error!r:.160} — the answer below came from a SMALLER context "
                        f"than configured; treat any measurement taken from it as VOID"
                    )
                _ctx_used = attempt_ctx
                # this loop asks a PARKED turn again (below), so the factory may park this turn for the card
                _CALL_CONTEXT.reask_parked = True
                try:
                    if _think:   # a think flag only goes out when there is one; a no-flag call (and every test fake) is unchanged
                        parsed = _chat_once(host, model, messages, tools_list, attempt_ctx, effective_keep_alive,
                                            timeout, think=_think)
                    else:
                        parsed = _chat_once(host, model, messages, tools_list, attempt_ctx, effective_keep_alive,
                                            timeout)
                finally:
                    _CALL_CONTEXT.reask_parked = False
                _media["pending"] = []  # accepted by the model; never remove these images on a later error
                break
            except cancellation.Cancelled:
                raise   # a cancel must never be retried — propagate so the request unwinds
            except (httpx.HTTPError, ValueError) as exc:
                detail = str(exc)
                rejected = bool(getattr(exc, "media_rejected", False))
                if rejected and _repair_rejected_tool_media(messages, _media, step):
                    if _slot is not None:
                        _slot.save(messages, step, loop=_loop_state())
                    _delta({"kind": "tool_result", "name": "image_delivery", "snag": True, "ms": 0,
                            "result": "media_input_rejected: new tool image was not seen; convert or choose another", "chars": 0})
                    notes.log("MEDIA INPUT REJECTED host=%s model=%s step=%s: new tool batch withheld; accepted prefix kept" % (host, model, step))
                    _status(status_callback, "The image was rejected before being seen; the model can choose or convert another")
                    attempt -= 1  # changed input, normal agent repair; the same rejected batch cannot retry again
                    continue
                if rejected:
                    raise ChatError(host, model, "media input was rejected but cannot be safely attributed to a new tool image; accepted history kept") from exc
                if getattr(exc, "parked", False):
                    # Turn parked mid-answer (not a failure): suspend requested or card yield. State is saved; run resumes later. No retry consumed.
                    notes.log("TURN PARKED host=%s model=%s step=%s: %s" % (host, model, step, detail[:200]))
                    _suspend_here(_slot)
                    # the person it was parked for goes first (app._LeadSlot._ask_holder_to_park): asked again at
                    # once, this turn would take the card straight back and they would wait out the whole answer
                    if YIELD_HOOK is not None:
                        try:
                            YIELD_HOOK(status_callback)
                        except cancellation.Cancelled:
                            raise
                        except Exception as hook_exc:               # noqa: BLE001 - a hand-over never ends the job's turn
                            notes.log("yield after a parked turn failed (%r)" % (hook_exc,))
                    _status(status_callback, "Picking this turn back up where it was parked")
                    attempt -= 1
                    continue
                if getattr(exc, "looped", False):
                    # The factory's loop rule ended this answer (chat_window.TurnLooped): the model was writing the same
                    # passage over and over. Not a transport fault, so it spends no retry, and asking the very same thing
                    # again changes nothing - the model is told once what happened, then asked again. CODING_IDLE_TURNS
                    # such answers in a row end the call with the reason said (the idle-turn rule's own count).
                    _looped += 1
                    _tele["nudges_loop"] = _tele.get("nudges_loop", 0) + 1
                    notes.log("TURN LOOPED host=%s model=%s step=%s (%d in a row): %s" % (
                        host, model, step, _looped, detail[:200]))
                    if _looped >= config.CODING_IDLE_TURNS:
                        if getattr(_CALL_CONTEXT, "job", False):
                            # Job context: break with 'looped' status (work preserved/reported) instead of raising.
                            _ended = "looped"
                            break
                        raise ChatError(host, model, "the model's answer went into a loop %d times in a row at step %d "
                                        "(one passage written over and over, ended by the loop rule each time); "
                                        "this is the model's own output, not a network fault" % (_looped, step)) from exc
                    if _looped == 1:
                        messages.append({"role": "tool" if messages and messages[-1].get("role") == "tool" else "user",
                                         "content": LOOP_NUDGE % config.CODING_IDLE_TURNS})
                    _status(status_callback, "The model was repeating itself; it has been told, and is being asked again")
                    attempt -= 1
                    continue
                if getattr(exc, "unavailable", False):
                    # the desk held/refused this box's window: asking it again (or with a smaller window) cannot help
                    raise ChatError(host, model, detail, unavailable=True) from exc
                if getattr(exc, "response", None) is not None:
                    try:
                        detail = exc.response.text[:500]
                    except Exception:
                        pass
                last_error = detail
                parsed = None
                if _CTX_SHRINK_ERROR.search(detail or ""):
                    _shrink += 1
                if attempt < config.CHAT_RETRIES:
                    time.sleep(3 * (attempt + 1))
        if _ended == "looped":
            break
        if parsed is None:
            raise ChatError(host, model, last_error or "Ollama request failed")
        _ctx_fact = {
            "host": host,
            "requested_model": model,
            "served_model": parsed.get("served_model") or "",
            "step": step,
            "attempt": attempt + 1,
            "requested_num_ctx": requested_ctx,
            "effective_num_ctx": _ctx_used,
            "downgraded": bool(_ctx_used is not None and _ctx_used < requested_ctx),
            "think": _think,   # the effort actually sent (False, or the crown's API value) - read back, never assumed
        }
        _record_execution_evidence(_ctx_fact)
        _tele["requested_num_ctx"] = requested_ctx
        _tele["effective_num_ctx"] = min(
            int(_tele.get("effective_num_ctx") or _ctx_used), int(_ctx_used))
        _tele["ctx_downgraded"] = bool(
            _tele.get("ctx_downgraded") or _ctx_fact["downgraded"])
        _tele["infer_s"] += time.monotonic() - _ti
        if parsed.get("done_reason") != RUNAWAY:   # a stopped runaway carries no prompt count: the last one stands
            _conv_tokens = int(parsed.get("prompt_eval_count") or 0) + int(parsed.get("eval_count") or 0)
        msg = parsed["message"]
        messages.append(msg)
        _counted_chars = _prompt_chars(messages, tools_list)
        _th(msg)
        calls = msg.get("tool_calls")
        if calls:
            _cuts_in_a_row = _text_calls_in_a_row = 0
        if not calls:
            # A turn that filled its window (RUNAWAY) or exceeded the rigid thinking limit is not a finished answer.
            # We must compact and re-ask: a stop with room left is a deliberate cap, and the model needs to be told to act.
            # The prompt size cannot be the test here (the prompt can grow during compaction), so we rely on these
            # explicit stop reasons. This recovers the state by summarizing the partial thought and retrying the turn.
            if parsed.get("done_reason") == RUNAWAY:
                if _cuts_in_a_row < config.CODING_IDLE_TURNS:
                    _cuts_in_a_row += 1
                    _cut_turn = "\n\n".join(t for t in (msg.get("thinking"), msg.get("content")) if t) or None
                    messages.pop()
                    _counted_chars = _prompt_chars(messages, tools_list)
                    _force_compact = True
                    notes.log("TURN RUNAWAY host=%s model=%s step=%d: thought for about %d tokens without calling a tool "
                              "(the guard is %d), summarising it and asking again"
                              % (host, model, step, int(len(_cut_turn or "") / COMPACT_CHARS_PER_TOKEN),
                                 config.TOLKIEN_RUNAWAY_THINK))
                    _delta({"kind": "tool_result", "name": "runaway", "ms": 0, "snag": True,
                            "result": "the turn thought past %d tokens without acting; its thinking is summarised and the "
                                      "turn asked again" % config.TOLKIEN_RUNAWAY_THINK, "chars": 0})
                    _tele["runaways"] = _tele.get("runaways", 0) + 1
                    continue
                if getattr(_CALL_CONTEXT, "job", False):
                    _ended = "runaway"          # mark as 'runaway' (not failed) to distinguish intentional handoff from error
                    break
            if parsed.get("done_reason") == "length" and _conv_tokens >= COMPACT_FRACTION * _window:
                if _cuts_in_a_row < config.CODING_IDLE_TURNS:
                    _cuts_in_a_row += 1
                    _cut_turn = "\n\n".join(t for t in (msg.get("thinking"), msg.get("content")) if t) or None
                    messages.pop()
                    _counted_chars = _prompt_chars(messages, tools_list)
                    notes.log("TURN OUT OF ROOM host=%s model=%s step=%d: %d of %d tokens, compacting and asking again"
                              % (host, model, step, _conv_tokens, _window))
                    _delta({"kind": "tool_result", "name": "out_of_room", "ms": 0, "snag": True,
                            "result": "the turn filled its %d-token window before finishing; compacting and asking again"
                                      % _window, "chars": 0})
                    _tele["out_of_room"] = _tele.get("out_of_room", 0) + 1
                    continue
                if getattr(_CALL_CONTEXT, "job", False):
                    _ended = "out_of_room"      # stopped with a handoff, not failed
                    break
            if getattr(_CALL_CONTEXT, "job", False) and _TEXT_CALL.search(msg.get("content") or ""):
                if _text_calls_in_a_row < config.CODING_IDLE_TURNS:
                    _text_calls_in_a_row += 1
                    notes.log("TOOL CALL AS TEXT host=%s model=%s step=%d: asked to make it again" % (host, model, step))
                    messages.append({"role": "user", "content": TEXT_CALL_NOTE})
                    _tele["text_calls"] = _tele.get("text_calls", 0) + 1
                    continue
                # If the retry budget is exhausted, exit immediately; falling through here would use the malformed text as the final answer.
                _ended = "text_calls"
                break
            answer = (msg.get("content") or msg.get("thinking") or "").strip()
            # Observability: log every final message to catch degenerate stubs (no tool calls)
            # or context overflows. Short answers may indicate truncation ('length') vs
            # model stopping ('stop'); step number helps distinguish early failure from
            # brief summaries after work.
            notes.log(
                f"TURNMETA host={host} model={model} step={step} done_reason={parsed.get('done_reason')!r} "
                f"prompt_tokens={parsed.get('prompt_eval_count')} eval_tokens={parsed.get('eval_count')} "
                f"tools_used={len(transcript or [])} answer_len={len(answer)}"
            )
            # Fail-safe: bare except + lazy import ensure this detector never breaks chat.
            # Verdict is local; nudge actuator is wired elsewhere.
            try:
                _ts = __import__("turn_stall")
                _supervised = any(
                    "SUPERVISED LOCAL CODING JOB" in (m.get("content") or "")
                    for m in messages if m.get("role") == "user"
                )
                _tr_tool_names = [
                    e.get("tool") for e in (transcript or [])
                    if isinstance(e, dict) and (e.get("tool") or "").startswith("coding_")
                ]
                _verdict = _ts.observe(
                    answer=answer, tool_names=_tr_tool_names, step=step,
                    done_reason=parsed.get("done_reason"),
                    eval_tokens=parsed.get("eval_count"),
                    supervised=_supervised,
                )
                if _verdict.get("verdict") == "stalled":
                    notes.log(f"TURNSTALL {_verdict.get('why')}")
            except Exception:
                pass
            if len(answer) < config.SHORTANS_CHARS:
                _full = parsed
                notes.log(
                    f"SHORTANS host={host} model={model} step={step} done_reason={_full.get('done_reason')!r} "
                    f"prompt_tokens={_full.get('prompt_eval_count')} eval_tokens={_full.get('eval_count')} "
                    f"thinking_len={len(msg.get('thinking') or '')} tools_used={len(transcript or [])} "
                    f"content={answer!r}"
                )
            if len(answer) < 2:
                if getattr(_CALL_CONTEXT, "job", False):
                    _ended = "empty"            # job runs retain empty responses instead of raising to allow handoff
                    break
                raise ChatError(host, model, f"model returned a truncated answer: {answer!r}")
            if _slot is not None and OPEN_ERRANDS_HOOK is not None:
                OPEN_ERRANDS_HOOK(_slot.ledger.run_id)   # a helper it sent out reports before it ends
            if _slot is not None and _drain_inbox(_slot, messages):
                continue   # the user wrote while it was answering: the run goes on with their message, not ends
            _hollow = _gate.get("code") and not _gate.get("tests")
            if _gate["on"] and _gate["edited"] and (not _gate["green"] or _hollow):
                if _gate["refused"] < config.CODING_IDLE_TURNS:
                    _gate["refused"] += 1
                    _tele["finish_refused"] = _gate["refused"]
                    _why = "no test added or changed for the code change" if _gate["green"] else \
                        "no passing coding_test since the code changed"
                    notes.log("FINISH GATE refused #%d host=%s step=%d (%s)" % (_gate["refused"], host, step, _why))
                    _delta({"kind": "tool_result", "name": "finish_gate", "ms": 0, "snag": True,
                            "result": "finish refused: " + _why, "chars": 0})
                    messages.append({"role": "user", "content": _GATE_HOLLOW if _gate["green"] else _GATE_REFUSAL})
                    continue
                _nv_why = "no test covers the change" if _gate["green"] else "the tests never passed on it"
                _kept = ""
                if UNVERIFIED_HOOK is not None:
                    try:
                        _kept = UNVERIFIED_HOOK(sorted(_gate.get("tasks") or ()), _nv_why) or ""
                    except Exception as exc:   # keeping the work must never cost the answer
                        notes.log("UNVERIFIED keep failed: %r" % (exc,))
                answer = answer.rstrip() + ("\n\n_Not verified: the code changed and %s (the finish gate refused "
                                            "%d times).%s_" % (_nv_why, _gate["refused"],
                                                               (" " + _kept) if _kept else ""))
            # Refusal count must mirror the test threshold to ensure a non-responsive model terminates with a labeled state.
            _unseen = _habits.finish_reasons() if (_gate["on"] and tools.habit_on("finish_look")) else []
            if _unseen:
                if _gate["refused"] < config.CODING_IDLE_TURNS:
                    _gate["refused"] += 1
                    _tele["finish_refused"] = _gate["refused"]
                    notes.log("FINISH GATE refused #%d host=%s step=%d (%s)" % (_gate["refused"], host, step, _unseen[0]))
                    _delta({"kind": "tool_result", "name": "finish_gate", "ms": 0, "snag": True,
                            "result": "finish refused: " + _unseen[0], "chars": 0})
                    messages.append({"role": "user", "content": _GATE_UNSEEN % _unseen[0]})
                    continue
                answer = answer.rstrip() + ("\n\n_Not looked at: %s (the finish gate refused %d times)._"
                                            % (_unseen[0], _gate["refused"]))
            _drive_tele()
            return answer, None
        _esc, _any_new = _run_calls(calls, step)
        if _esc:
            return None, _esc
        _turn_s.append(time.monotonic() - _turn_t0)
        if _progress is not None:
            idle = _progress.end_turn(_any_new)
            _repeated = _progress.take_repeated()
            stall = _progress.stall_turns
            _idle_trip = idle >= config.CODING_IDLE_TURNS
            _stall_trip = stall >= config.CODING_STALL_TURNS
            if (_idle_trip or _stall_trip) and _progress.resets >= IDLE_RESETS:
                _ended = "idle" if _idle_trip else "stall"
                break
            if idle >= 1 or _stall_trip:
                # The nudge must name the specific repeated unit; generic prompts were found to cause further repetition loops.
                # A trip that does not end the session (because resets remain) explains the cause and resets both idle and stall counters.
                _tele["nudges_idle"] = _tele.get("nudges_idle", 0) + 1
                messages.append({"role": "tool", "content": (
                    _idle_nudge(_repeated, idle, config.CODING_IDLE_TURNS, IDLE_RESETS - _progress.resets)
                    if idle >= 1 else _stall_nudge(stall))})
                if _idle_trip or _stall_trip:
                    _progress.resets += 1
                    _progress.idle_turns = _progress.stall_turns = 0
                    _tele["idle_resets"] = _tele.get("idle_resets", 0) + 1
                    notes.log("IDLE RESET host=%s model=%s step=%s: %s; told why, counts restarted" % (
                        host, model, step, ("%d repeat-only turns (%s)" % (idle, ", ".join(_repeated) or "?"))
                        if _idle_trip else "%d turns back on code already tried (stall)" % stall))
                    _delta({"kind": "idle_reset", "rule": "repeat" if _idle_trip else "stall", "repeated": _repeated})
    if _ended == "turns":
        _why = "too many tool iterations"
        _status(status_callback, f"Tool budget reached ({loop_max}/{loop_max}); asking the model for a concise handoff")
    else:
        _why = _STOP_WHY[_ended]()
        _tele["ended"] = _ended
        if _slot is not None and _ended != "deadline":
            # This is a guard stop, not a finish: the save must remain resumable (run_ledger.done/resumable), not finalized.
            _slot.stopped = _ended
        _status(status_callback, f"Stopped: {_why}; asking the model for a concise handoff")
    answer = _explain(messages, host, model, num_ctx or config.NUM_CTX, why=_why,
                      run=lambda fn, args: tools.run_tool(fn, args, user_text, user=user, persona=persona),
                      tools_list=tools_list)
    if answer is None and (host, model) != (config.HEAVY_HOST, config.HEAVY_MODEL):
        answer = _explain(messages, config.HEAVY_HOST, config.HEAVY_MODEL, config.HEAVY_NUM_CTX, why=_why)
    _drive_tele()
    answer = answer or ("(stopped: %s)" % _why)
    _label = getattr(_CALL_CONTEXT, "budget_label", None)
    if _label and (_ended in _STOP_WHY or _ended == "turns"):
        # A run the USER gave a time budget (#37 Background) must never read as a finish when something other than the
        # model's own answer ended it: the clock, the tool-step cap, no new work, or any other stop the loop made.
        why = ("at your %s budget" % _label if _ended == "deadline"
               else "at the tool-step limit (%s steps), inside your %s budget" % (loop_max, _label) if _ended == "turns"
               else "%s, inside your %s budget" % (_STOP_WHY[_ended](), _label))
        answer = answer.rstrip() + "\n\n_Stopped %s: this is where the work stood, not a finished answer._" % why
    return answer, None
