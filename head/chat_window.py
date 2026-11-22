"""HAI model turns through a factory WINDOW order: Kirk's half. Model work is a factory order, chat included; the shape is [[34-CHAT-FACTORY-CONTRACT-AUDIT]] § "Shape review": ONE
window order per box + model + context, held warm and shared by every Kirk run, instead of a desk order per turn.
The factory half is helper_window.py --live; its docstring is the contract this module writes against.

  open The brain chooser has already picked host, model and context. The window is a desk order pinned to that host
          (`only`), so the desk's own admission decides (fit-before-load, a person on the card, Plex, a lost card).
          Refused or held = httpx.ConnectError: the caller's existing failover takes the next box. A held order is
          cancelled at the desk first - a chat turn does not wait in line for a box the chooser can route around.
  a turn One item {model, messages, tools, think, keep_alive, options, stream: true}. The window's <id>.stream.jsonl
          is read back as the engine's own lines, so every parser downstream is unchanged.
  status OWNER.json is rewritten every PERIOD_S while Kirk holds the window (the window ends itself when it falls
          silent past 3 periods - a dead Kirk never leaves a card held). IDLE exists while no run is using it; a run
          that is between tool calls is still using it (B9: no lending during tool waits). A window that ended at the
          desk or wrote CLOSE is forgotten, and the next turn opens another wherever the chooser sends it.
  restart A restart of Kirk does not end a window: the engine keeps its model and the conversation it has read, and a
          turn it is writing keeps going to its stream file. WINDOW.json says what the window is and OWNER.json which runs
          use it, so the new process takes it back (adopt_windows) before any run resumes, the run goes back to its own
          window (take_resumed) and an answer still being written is followed, not asked again (_claim_item). Nothing is
          restored, so nothing can differ.
          LLM-Project\\Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start.md.

Off unless config.CHAT_WINDOWS, and then only for the users it names (enabled_for). Two wires: ollama /api/chat, and
Strata's OpenAI chat/completions at a box's llama-swap base (config.SWAP_MODELS - Flash-Next, the agentic crown): that
window is placed `--strata auto` at the model's measured context, starts its own engine through strata_arm's lifecycle
(daily use, no frontier mode) after llama-swap lets go, and its chunks come back as `data:` lines ending `[DONE]`."""
import contextlib
import json
import os
import re
import shutil
import threading
import time
import uuid

import httpx

import cancellation
import config
import notes

PERIOD_S = 15.0           # = the factory's COMPUTE_BEAT_S: Kirk declares this cadence in every OWNER.json
# While runs are parked for a deploy, the heartbeat declares this cadence instead, so a window outlives the restart: a window
# ends after 3 silent periods, 3 minutes here. MEASUREDpark to the new container's start, 60 s. A restart that
# takes longer costs the window, and the run reads its conversation again: today's behaviour, never worse.
HOLD_PERIOD_S = 60.0
_HOLD = {"on": False}
WINDOW = "WINDOW.json"    # what this window is, for the next Kirk process (one writer: the process that opened it)
POLL_S = 0.1             # reading a local-share file for the next chunk; not a model request
PRIORITY = 100            # a service order a live user waits on: next after the current order (a1_delegate's rule)
ITEM, OUT, CLOSE, STREAM, OWNER, IDLE, CANCEL = (".item.json", ".out.json", "CLOSE", ".stream.jsonl", "OWNER.json",
                                                 "IDLE", ".cancel")
LEND, LENT = "LEND", "LENT"   # lending an idle window's card (helper_window.py § LENDING THE CARD is the contract)
CUT = ".cut"              # park the turn in flight mid-answer and KEEP its state (helper_window.py § CUT is the contract)
_LOCK = threading.RLock()
_WINDOWS = {}             # (host, model, num_ctx) -> Window
_BEAT = {"thread": None}


class WindowUnavailable(httpx.ConnectError):
    """The desk HELD or REFUSED this box's window: the box cannot take the turn now. Still a ConnectError, so every
    existing failover takes the next box; `unavailable` tells the chat loop not to retry the same box or shrink the
    window."""
    unavailable = True


class WindowFailedAtOpen(httpx.ReadError):
    """The window's factory order failed before relaying any output: the box cannot serve this model now. `unavailable`
    so the chat loop neither retries it nor shrinks it; the caller takes its next row."""
    unavailable = True


class TurnParked(httpx.ReadError):
    """The window PARKED this turn mid-answer and saved its state: a suspend asked for it, or the factory wanted the card. Not a failure. The chat loop asks the same turn
    again - at once, or after the run is resumed - and the engine goes on from the cut (lab patch #4), so the caller
    gets the whole answer. Still a ReadError, so a caller that does not know it treats it as a cut read and retries."""
    parked = True


class TurnLooped(httpx.ReadError):
    """The window ENDED this turn because the model's output had stopped making progress: the factories' loop rule
    (infer_ollama.looping / tolkien_llm.looping), and the row says done_reason "loop". The model's own answer, not a
    transport fault: the chat loop tells the model once and asks again, and ends the call when it keeps happening.
    Still a ReadError, so a caller that does not know it treats it as a cut read."""
    looped = True


class Window:
    def __init__(self, host, model, num_ctx, name, local_dir, desk_id):
        self.host, self.model, self.num_ctx, self.name = host, model, num_ctx, name
        self.dir, self.desk_id = local_dir, desk_id
        self.users = set()                  # run keys currently using this window
        self.inflight = {}                  # run key -> the item id it is reading now (park_turn)
        self.n = 0
        self.strata = False                 # a Strata window runs its own engine: it has no ollama slot to lend
        self.opened = time.time()           # _open sets it to before the desk intake: the window's first-output clock
        self.resumed = set()                # adopted after a restart: runs that go back to it once (take_resumed)
        self.to_claim = set()               # adopted: runs that may still claim an unfinished item (_claim_item)
        self.returning = 0                  # adopted: how many runs were coming back
        self.claimed = set()                # adopted: the items returning runs reconnected to (never cancelled)
        self.context_tokens = None          # the last turn's conversation, estimated from its characters: what a reload re-reads

    @property
    def key(self):
        return (self.host, self.model, self.num_ctx)

    def ended(self):
        """True once the window wrote CLOSE or its desk order is terminal. Unreadable desk = not ended (no verdict)."""
        if os.path.exists(os.path.join(self.dir, CLOSE)):
            return True
        try:
            import p20_queue
            job = p20_queue.get(self.desk_id) or {}
            return job.get("status") in p20_queue.TERMINAL
        except Exception:
            return False

    def desk_status(self):
        try:
            import p20_queue
            return (p20_queue.get(self.desk_id) or {}).get("status") or ""
        except Exception:
            return ""


def enabled_for(user):
    """config.CHAT_WINDOWS_USERS: "*" / "1" = everyone, else the named users only (the live proof widens by name)."""
    users = config.CHAT_WINDOWS_USERS
    return bool(users) and ("*" in users or "1" in users or (user or "") in users)


TOLKIEN_BOX = config.roster_role("tolkien")     # the box Tolkien runs on (its own GPU lane), or None when this head has none
_TOLKIEN_ORDER = threading.local()            # the Tolkien box row's own `order` fields for the window this thread is about to open


def tolkien_window_enabled_for(user):
    """The Tolkien box's chat window is on for `user`: <STATE_DIR>/<that box>-window.json {"users": [...]} names them
    ("*" = everyone), read per turn so it is turned on or widened without a redeploy (config's note). Absent /
    unreadable / no Tolkien box = off."""
    if not TOLKIEN_BOX:
        return False
    try:
        with open(os.path.join(config.STATE_DIR, "%s-window.json" % TOLKIEN_BOX), encoding="utf-8") as f:
            users = set(json.load(f).get("users") or [])
    except (OSError, ValueError, AttributeError):
        return False
    return enabled_for(user) and ("*" in users or (user or "") in users)


@contextlib.contextmanager
def tolkien_order(fields):
    """The weighed Tolkien box row's `order` fields (model, ctx, gpu, fit_target, build, temperature, reasoning, threads) for
    a window opened on this thread - the row that chose the Tolkien box chooses its settings, as on the GPU boxes."""
    prev = getattr(_TOLKIEN_ORDER, "fields", None)
    _TOLKIEN_ORDER.fields = dict(fields or {})
    try:
        yield
    finally:
        _TOLKIEN_ORDER.fields = prev


def _host_for(url):
    import scheduler
    if url.split("/v1/")[0].rstrip("/") == config.TOLKIEN_WINDOW_URL:
        return TOLKIEN_BOX
    base = url.split("/api/")[0].split("/v1/")[0].rstrip("/")
    for model, row in getattr(config, "SWAP_MODELS", {}).items():
        if row.get("base", "").rstrip("/") == base:
            base = row["host"].rstrip("/")                # llama-swap's port on the box -> the box's card
    for host, card in scheduler.CARDS.items():
        if str(card.get("url") or "").rstrip("/") == base:
            return host
    return None


def is_swap_url(url):
    """A Strata (Flash-Next) turn: OpenAI chat/completions at a box's llama-swap base (config.SWAP_MODELS)."""
    base = url.split("/v1/")[0].rstrip("/")
    return url.rstrip("/").endswith("/v1/chat/completions") and (base == config.TOLKIEN_WINDOW_URL or any(
        row.get("base", "").rstrip("/") == base for row in getattr(config, "SWAP_MODELS", {}).values()))


def _write_json(path, body):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    os.replace(tmp, path)


# Where each chat's quick-resume state lives (The factory's window saves a chat's state per (model, window size, chat)
# when the window goes idle or switches chats (helper_window.ConvSlots) and keeps each user's five most recent; Kirk
# records the (box, model, size) of the last window turn each chat finished, merged per chat, newest kept.
HOMES_KEPT = 5000
_HOMES_LOCK = threading.Lock()


def _homes_path():
    return os.path.join(config.STATE_DIR, "chat-state-homes.json")


def _read_homes():
    try:
        with open(_homes_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record_home(conv, host, model, num_ctx):
    """This chat's state now lives on (host, model, num_ctx). Fail-soft: a record that cannot be written never fails a
    turn."""
    try:
        with _HOMES_LOCK:
            d = _read_homes()
            d[str(conv)] = {"host": host, "model": model, "num_ctx": int(num_ctx or 0), "t": time.time()}
            if len(d) > HOMES_KEPT:
                for k in sorted(d, key=lambda k: d[k].get("t", 0))[:len(d) - HOMES_KEPT]:
                    d.pop(k, None)
            os.makedirs(os.path.dirname(_homes_path()), exist_ok=True)
            _write_json(_homes_path(), d)
    except Exception:                                                # noqa: BLE001
        pass


def conv_home(conv):
    """(host, model, num_ctx) of the window that last served this chat, or None."""
    if not conv:
        return None
    h = _read_homes().get(str(conv))
    return (h.get("host"), h.get("model"), int(h.get("num_ctx") or 0)) if isinstance(h, dict) else None


FETCH = ".fetch.json"        # = helper_window.FETCH: a prefetch ask the window consumes while no turn is pending


def prefetch(conv):
    """The page's first keystroke in a chat:
    if the box holding this chat's state has its window for that model and size open, ask it to bring the state back
    from the array now (helper_window.ConvSlots.prefetch_asked), so the turn restores instead of copying. No open
    window -> nothing to ask; the turn's own fetch still runs. Never raises: a prefetch is a hint, the turn decides."""
    try:
        home = conv_home(conv)
        if not home:
            return {"ok": True, "asked": False, "why": "no box holds this chat's state"}
        with _LOCK:
            w = _WINDOWS.get(home)
        if w is None:
            return {"ok": True, "asked": False, "why": "no open %s window on %s" % (home[1], home[0])}
        body = {"conv": conv}
        import scheduler
        rate = scheduler.read_rate(home[0], home[1])
        if rate:
            body["read_tok_s"] = rate
        _write_json(os.path.join(w.dir, "f%s%s" % (uuid.uuid4().hex[:12], FETCH)), body)
        return {"ok": True, "asked": True, "host": home[0]}
    except Exception as e:                                           # noqa: BLE001
        return {"ok": False, "asked": False, "why": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _beat_owner(w):
    _write_json(os.path.join(w.dir, OWNER), {"run": "kirk:" + w.name, "period": HOLD_PERIOD_S if _HOLD["on"] else PERIOD_S,
                                             "t": time.time(), "users": sorted(w.users)})


def _write_identity(w):
    """What this window is, for the next Kirk process (adopt_windows). Written once, after the desk placed it."""
    _write_json(os.path.join(w.dir, WINDOW), {"host": w.host, "model": w.model, "num_ctx": int(w.num_ctx), "name": w.name,
                                              "desk_id": w.desk_id, "strata": bool(w.strata), "opened": w.opened})


def going_down():
    """Kirk is stopping, for whatever reason (a deploy's recreate, a host reboot): every open window's last beat declares
    the restart period, so the window waits the restart out and the next process picks it back up. only
    a deploy that PARKED first did this (deploy_hold through /runs/park); a plain recreate left the 15 s period in the
    last beat, and a Kirk that was silent 46 s came back one second past the windows' limit - the Flash-Next job on it
    lost its window and then its run (run-q). The restart announces itself; no deploy script has to remember. Never raises."""
    try:
        deploy_hold(True)
        with _LOCK:
            names = sorted(w.name for w in _WINDOWS.values())
        if names:
            notes.log("CHAT WINDOW restart announced to %d open window(s): each waits %d s for the next process (%s)"
                      % (len(names), int(3 * HOLD_PERIOD_S), ", ".join(str(n) for n in names)[:300]))
    except Exception as exc:                                         # noqa: BLE001 - a shutdown must never fail on it
        notes.log("CHAT WINDOW could not announce the restart (%r)" % (exc,))


def deploy_hold(on):
    """A deploy parked the runs (or called it off): tell every open window at once how long a silence to allow, so the
    last beat before the restart already carries it. Never raises: a window that misses it ends as it always did."""
    _HOLD["on"] = bool(on)
    with _LOCK:
        for w in list(_WINDOWS.values()):
            try:
                _beat_owner(w)
            except OSError as exc:
                notes.log("CHAT WINDOW %s: the deploy hold was not written (%r)" % (w.name, exc))


def adopt_windows(resuming, clock=time.time):
    """Take back the windows the previous Kirk process left running -> their names. Called at startup BEFORE any run
    resumes, so what a resumed run finds held is its own. `resuming`: the run keys that are really coming back; a window's
    other users are gone, and a window nobody is coming back to goes IDLE and ends by its own idle rule. Left alone: a
    folder with no WINDOW.json (a window opened before this rule, an abandoned one), a closed one, one whose owner has
    already been silent past the window's own limit (it ended itself), one finished at the desk. Never raises."""
    resuming = set(resuming or ())
    try:
        names = sorted((n for n in os.listdir(config.CHAT_WINDOWS_DIR) if n.startswith("hai-win-")), reverse=True)
    except OSError as exc:
        notes.log("CHAT WINDOW pickup: the window folders could not be read (%r)" % (exc,))
        return []
    adopted = []
    for name in names:                                 # newest first: the stamp is in the name, and the newest wins a key
        d = os.path.join(config.CHAT_WINDOWS_DIR, name)
        try:
            if os.path.exists(os.path.join(d, CLOSE)):
                continue
            with open(os.path.join(d, WINDOW), encoding="utf-8") as f:
                who = json.load(f)
            with open(os.path.join(d, OWNER), encoding="utf-8") as f:
                owner = json.load(f)
            silent = clock() - float(owner["t"])
            if silent > 3 * float(owner["period"]):
                notes.log("CHAT WINDOW %s not picked up: its owner was silent %.0f s, past its limit" % (name, silent))
                continue
            w = Window(who["host"], who["model"], int(who["num_ctx"]), name, d, who["desk_id"])
            w.strata, w.opened = bool(who.get("strata")), float(who.get("opened") or clock())
            if w.ended():
                continue
            files = os.listdir(d)
            with _LOCK:
                if w.key in _WINDOWS:
                    continue
                w.users = {u for u in (owner.get("users") or []) if u in resuming}
                w.resumed, w.to_claim, w.returning = set(w.users), set(w.users), len(w.users)
                w.claimed = set()
                w.n = sum(1 for f in files if f.endswith(ITEM))
                _WINDOWS[w.key] = w
            if not w.users:
                _cancel_unfinished(w, files)       # nobody is coming back for what it was writing
            _beat_owner(w)
            _set_idle(w)
            adopted.append(name)
            notes.log("CHAT WINDOW picked up %s desk=%s (%s, %d item(s), owner silent %.0f s, runs %s)" % (
                name, w.desk_id, w.desk_status() or "desk status unknown", w.n, silent, sorted(w.users) or "none"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if not isinstance(exc, FileNotFoundError):
                notes.log("CHAT WINDOW %s not picked up (%r)" % (name, exc))
    if adopted:
        _ensure_beat()
    return adopted


def take_resumed(run_key):
    """The window a run resumed after a restart still holds, once, or None: the run goes straight back to it. Its box and
    model were already decided, and re-asking is how a job got sent to a smaller window."""
    if not run_key:
        return None
    with _LOCK:
        for w in _WINDOWS.values():
            if run_key in w.resumed:
                w.resumed.discard(run_key)
                return None if w.ended() else w
    return None


def shared_tool_count(tools):
    """How many leading tools of this list every chat carries: the selection's always-offered set comes first, in the pool's
    order (tool_selection.select), and the list renders before the conversation, so a saved start stops where it ends.
    26 of 27 real first turns had different lists, all sharing these."""
    import tool_selection
    n = 0
    for t in tools:
        if ((t.get("function") or {}).get("name") if isinstance(t, dict) else None) not in tool_selection.CORE_TOOL_NAMES:
            break
        n += 1
    return n


def _cancel_unfinished(w, files=None, keep=()):
    """Unfinished items nobody will read end their generation (the window's CANCEL marker), so the card is not left
    writing an answer for no one."""
    files = os.listdir(w.dir) if files is None else files
    done = {f[:-len(OUT)] for f in files if f.endswith(OUT)}
    for f in files:
        if f.endswith(ITEM) and f[:-len(ITEM)] not in done and f[:-len(ITEM)] not in keep:
            try:
                open(os.path.join(w.dir, f[:-len(ITEM)] + CANCEL), "w").close()
                notes.log("CHAT WINDOW %s: item %s cancelled, nobody is coming back for it" % (w.name, f[:-len(ITEM)]))
            except OSError:
                pass


def _claim_item(w, body, run_key):
    """A run back after a restart asks its turn again: when the window already has that exact turn - still being written, or
    finished while Kirk was away - follow it instead of asking twice -> the item's id or None. The generation never
    stopped, so the answer is the same bytes. Candidates: every unanswered item, and the newest answered ones, one per
    returning run (each run's last turn may have finished while Kirk was away). What is left unanswered when the last
    returning run has looked is cancelled."""
    # all of it under the window lock: two runs coming back at once must not race, and the LAST one's clean-up keeps every
    # item ANY of them reconnected to
    with _LOCK:
        if run_key not in w.to_claim:
            return None
        w.to_claim.discard(run_key)
        last = not w.to_claim
        claimed = getattr(w, "claimed", None)
        if claimed is None:
            claimed = w.claimed = set()
        try:
            files = sorted(os.listdir(w.dir))
        except OSError:
            return None
        items = [f[:-len(ITEM)] for f in files if f.endswith(ITEM)]
        done = {f[:-len(OUT)] for f in files if f.endswith(OUT)}
        open_items = [i for i in items if i not in done]
        # a turn the window PARKED has a row too, and it is not an answer: it is asked again, never reconnected to
        answered = [i for i in items if i in done and not (_read_row(os.path.join(w.dir, i + OUT)) or {}).get("cut")]
        candidates = [i for i in open_items + answered[-max(1, w.returning):] if i not in claimed]
        want = json.loads(json.dumps(body.get("messages") or [], ensure_ascii=False))
        found = None
        for iid in candidates:
            try:
                with open(os.path.join(w.dir, iid + ITEM), encoding="utf-8") as f:
                    item = json.load(f)
            except (OSError, ValueError):
                continue
            if item.get("model") == body.get("model") and item.get("messages") == want:
                found = iid
                break
        if found:
            claimed.add(found)
        if last:
            _cancel_unfinished(w, files, keep=tuple(claimed))
    if found:
        notes.log("CHAT WINDOW %s: turn %s reconnected after the restart (%s), not asked again" % (
            w.name, found, "already answered" if found in done else "still being written"))
    return found


def _set_idle(w):
    path = os.path.join(w.dir, IDLE)
    try:
        if w.users:
            if os.path.exists(path):
                os.remove(path)
        elif not os.path.exists(path):
            open(path, "w").close()
    except OSError:
        pass


def _retire(name, why):
    """Remove one ENDED window's folder, by its own name. Its items hold whole conversations (each turn's messages) on
    the shared jobs share; the conversation's record is HAI's history and run ledger, so these are transport copies.
    Cleanup belongs to the caller that sees the end by any route - Kirk."""
    path = os.path.join(config.CHAT_WINDOWS_DIR, name)
    if not name.startswith("hai-win-") or os.path.dirname(os.path.abspath(path)) != os.path.abspath(
            config.CHAT_WINDOWS_DIR):
        return
    try:
        shutil.rmtree(path)
        notes.log("CHAT WINDOW folder retired %s (%s)" % (name, why))
    except FileNotFoundError:
        pass
    except OSError as exc:
        notes.log("CHAT WINDOW folder %s not retired yet (%r) - the next sweep tries again" % (name, exc))


def sweep_ended(clock=time.time):
    """Retire folders of windows this process does not hold that have ended: CLOSE written, or an owner heartbeat past
    the DOWN rule (3 x its declared period) - a previous Kirk's windows, and ones abandoned before they started."""
    try:
        names = [n for n in os.listdir(config.CHAT_WINDOWS_DIR) if n.startswith("hai-win-")]
    except OSError:
        return
    with _LOCK:
        held = {w.name for w in _WINDOWS.values()}
    for name in names:
        if name in held:
            continue
        d = os.path.join(config.CHAT_WINDOWS_DIR, name)
        if os.path.exists(os.path.join(d, CLOSE)):
            _retire(name, "closed")
            continue
        try:
            with open(os.path.join(d, OWNER), encoding="utf-8") as f:
                o = json.load(f)
            if clock() - float(o["t"]) > 3 * float(o["period"]):
                _retire(name, "owner silent")
        except (OSError, ValueError, KeyError, TypeError):
            pass                                  # could not tell: leave it for a later sweep


def _beat_loop():
    sweep_ended()
    while True:
        time.sleep(PERIOD_S)
        with _LOCK:
            for key, w in list(_WINDOWS.items()):
                if w.ended():
                    _WINDOWS.pop(key, None)
                    notes.log("CHAT WINDOW ended %s (%s)" % (w.name, w.desk_status() or "closed"))
                    continue
                try:
                    _beat_owner(w)
                except OSError:
                    pass            # the share hiccuped: the next beat is inside the window's 3-period allowance
        sweep_ended()               # ended windows (ours, dropped above, and any a previous Kirk left) lose their folders


def _ensure_beat():
    if _BEAT["thread"] is None or not _BEAT["thread"].is_alive():
        _BEAT["thread"] = threading.Thread(target=_beat_loop, name="chat-window-beat", daemon=True)
        _BEAT["thread"].start()


def _intake(spec, requires, host):
    from app import FactoryOrderReq, p20_order        # the same delivery door every order uses
# rush: a live user waits on this turn, so it never waits for the cheap-power window - the quick-preview
# precedent (p20_queue.enqueue_quick_preview: work that expires "should bypass energy-saving wait windows"), and
# chat's direct path was never energy-gated. Without it the desk held a 07:56 CT window as deferrable.
    return p20_order(FactoryOrderReq(spec=spec, requires=requires, only=[host], rush=True))


def _cancel(desk_id, why):
    try:
        from app import OrderCancelReq, p20_order_cancel
        reply = p20_order_cancel(OrderCancelReq(id=desk_id, why=why, source="hai-chat-window"))
    except Exception as exc:
        reply = {"ok": False, "error": repr(exc)}
    if not (reply or {}).get("ok"):
        notes.log("CHAT WINDOW could not cancel %s: %s - its OWNER beat is not refreshed, so if it ever starts it "
                  "ends itself" % (desk_id, (reply or {}).get("error")))


def keep_alive_seconds(value):
    """ollama's keep_alive as seconds ("5m", "120s", "1h", a bare number) or None when it never unloads (negative) - the
    idle rule Kirk hands an ollama window (`--idle-ttl`): a window ends when ollama would have unloaded its model, so it
    never holds a cold card as busy (#34 design s 4 a). A keep_alive of 0 (unload at once) ends the window after one
    owner beat: the card is already free, nothing is lost."""
    s = str(value if value is not None else "").strip().lower()
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*([smh]?)", s)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    if n < 0:
        return None
    n *= {"": 1, "s": 1, "m": 60, "h": 3600}[unit]
    return int(n) if n > 0 else int(PERIOD_S)


def _idle_ttl_for(host):
    """The box's keep_alive as the window's idle rule - the same value every turn on it sends (KEEP_ALIVE_OVERRIDES by
    the box's url, else KEEP_ALIVE): one derivation, so the window and the engine let go together."""
    return keep_alive_seconds(config.KEEP_ALIVE_OVERRIDES.get(_url_for(host) or "", config.KEEP_ALIVE))


def _url_for(host):
    try:
        import scheduler
        return (scheduler.CARDS.get(host) or {}).get("url")
    except Exception:                                                # noqa: BLE001
        return None


FIRST_KEEP = 20


def _first_path():
    return os.path.join(config.STATE_DIR, "chat-window-first.json")


def record_first(w, t_first, done):
    """A NEW ollama window's first turn, measured by the engine and by Kirk (#34 checklist B1 + the 10-23 overflow
    finding - 37.9 s against a 12.0 s price): seconds from the window's open (before the desk intake) to the first
    output Kirk read, the engine's own load and prompt-read time and the prompt it read. Spock charges the median of
    (first - load) on a box with no window for the model (the load is residency_cost's). Never raises."""
    try:
        rec = {"t": time.time(), "model": w.model, "first_s": round(t_first - w.opened, 2),
               "load_s": round((done.get("load_duration") or 0) / 1e9, 2),
               "prompt_s": round((done.get("prompt_eval_duration") or 0) / 1e9, 2),
               "prompt_n": done.get("prompt_eval_count"),
               # what was ALREADY in the engine (a saved start, the chat's own state): the engine's own count
               "cached_n": done.get("prompt_eval_cached_count")}
        with _LOCK:
            try:
                data = json.load(open(_first_path(), encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            data[w.host] = ((data.get(w.host) or []) + [rec])[-FIRST_KEEP:]
            _write_json(_first_path(), data)
        # print what was READ, not the prompt's SIZE: a turn whose engine restored a saved start reads far fewer
        # tokens than the prompt holds, and the size would make a working saved start look unused
        n, cached = rec["prompt_n"], rec["cached_n"]
        read = ("read %s of the prompt's %s tokens in %.1f s, %s already in the engine" % (n - cached, n, rec["prompt_s"], cached)
                if isinstance(n, int) and isinstance(cached, int) and cached > 0
                else "read %s tokens in %.1f s" % (n, rec["prompt_s"]))
        notes.log("CHAT WINDOW %s first output %.1f s after open (load %.1f s, %s)" % (w.name, rec["first_s"], rec["load_s"], read))
    except Exception as exc:                                         # noqa: BLE001
        notes.log("CHAT WINDOW first-output record failed (%s)" % type(exc).__name__)


def first_output_s(host):
    """(seconds, n): the median time a NEW window on `host` took to its first output, its model's load left out - or
    (None, 0) when the lab has not seen one there."""
    try:
        rows = json.load(open(_first_path(), encoding="utf-8")).get(host) or []
    except (OSError, ValueError):
        rows = []
    xs = sorted(max(0.0, float(r["first_s"]) - float(r.get("load_s") or 0)) for r in rows if r.get("first_s") is not None)
    return (xs[len(xs) // 2], len(xs)) if xs else (None, 0)


TURN_KEEP = 50


def _turns_path():
    return os.path.join(config.STATE_DIR, "chat-window-turns.json")


def record_turn(w, t_item, t_first, done, first_turn):
    """EVERY ollama window turn, not only a new window's first (speed is the time to the FIRST VISIBLE OUTPUT as well
    as to the answer): seconds from the turn's item written to the first output Kirk read, with the engine's own load /
    prompt read. The menu stamps the per box x model median of the non-first turns on its GPU rows (`first_out_s`) and
    the weighing shows it beside the total; the ordering stays on the total until the admin rules on which half a user
    minds. Never raises; last TURN_KEEP per host."""
    try:
        rec = {"t": time.time(), "model": w.model, "first_s": round(t_first - t_item, 2), "first_turn": bool(first_turn),
               "load_s": round((done.get("load_duration") or 0) / 1e9, 2),
               "prompt_s": round((done.get("prompt_eval_duration") or 0) / 1e9, 2),
               "prompt_n": done.get("prompt_eval_count")}
        with _LOCK:
            try:
                data = json.load(open(_turns_path(), encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            data[w.host] = ((data.get(w.host) or []) + [rec])[-TURN_KEEP:]
            _write_json(_turns_path(), data)
    except Exception as exc:                                         # noqa: BLE001
        notes.log("CHAT WINDOW turn record failed (%s)" % type(exc).__name__)


def turn_first_output_s(host, model):
    """(seconds, n): the median time a turn on an EXISTING window for `model` on `host` took to its first visible output
    (new-window first turns left out - they pay the open, priced separately), or (None, 0) when none seen."""
    try:
        rows = json.load(open(_turns_path(), encoding="utf-8")).get(host) or []
    except (OSError, ValueError):
        rows = []
    xs = sorted(float(r["first_s"]) for r in rows
                if r.get("model") == model and not r.get("first_turn") and r.get("first_s") is not None)
    return (xs[len(xs) // 2], len(xs)) if xs else (None, 0)


WAITING = ("pending", "held")       # the desk has the window's order and no factory has taken it yet


def _served(w):
    """True once a factory has taken this window's desk order: the card is then this process's. A window still WAITING
    stands in the factory's line behind whatever runs there; it holds nothing. An unreadable desk is not WAITING."""
    return w.desk_status() not in WAITING


def has_window(host, model):
    """A live window for `model` on `host` (any context) that a factory is serving: a turn there pays no window open.
    A window whose order still waits at the desk is not open."""
    with _LOCK:
        return any(w.host == host and w.model == model and not w.ended() and _served(w) for w in list(_WINDOWS.values()))


def window_in_use(host, model):
    """A served window for `model` on `host` that a run is using right now: it keeps its card whatever waits behind it."""
    with _LOCK:
        return any(w.host == host and w.model == model and not w.ended() and _served(w) and w.users
                   for w in list(_WINDOWS.values()))


def _offload_ok(host, model):
    try:
        import scheduler
        return scheduler.offload_ok(host, model)
    except Exception:                                               # noqa: BLE001 - undeclared: the desk decides as before
        return False


def _open(host, model, num_ctx, intake=None, strata=False):
    t_open = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # one name = one window: the stamp is per SECOND and carries no context, so two windows for one host/model opened
    # in the same second shared a folder - and each answered the other's items
    name = "hai-win-%s-%s-%s-%s" % (host, re.sub(r"[^A-Za-z0-9.]+", "-", model).strip("-"), stamp,
                                    uuid.uuid4().hex[:4])
    local = os.path.join(config.CHAT_WINDOWS_DIR, name)
    unc = config.CHAT_WINDOWS_UNC + "\\" + name
    os.makedirs(local, exist_ok=False)          # a collision is an error, never a shared folder
# the heartbeat exists BEFORE the order does: a window Kirk gives up on (held, refused, cancel failed) can still be
# placed later, and then it finds a beat nobody refreshes and ends itself
    _write_json(os.path.join(local, OWNER), {"run": "kirk:" + name, "period": PERIOD_S, "t": time.time()})
    if host == TOLKIEN_BOX:
        return _open_tolkien_window(name, local, model, num_ctx, intake, t_open)
    spec = {"name": name, "mode": "compute", "load": "gpu", "initiator": "hai",
            "payload": "{FOUNDRY_ROOT}\\bin\\helper_window.py",
            # --strata auto: the box serves this model on Strata (its strata_served.json row); model_fit leaves a
            # Strata order to Strata's own placement, as for the menu's infer_ollama --strata orders
            "args": [unc, "{ORDER_OUT}\\out.json", "--model", model, "--num-ctx", str(int(num_ctx)), "--live"]
                    + (["--strata", "auto"] if strata else
                       # an ollama window ends at the box's own keep_alive, so it never holds a cold card (design s 4 a)
                       (["--idle-ttl", str(_idle_ttl_for(host))] if _idle_ttl_for(host) else [])
                       # a planned split (the menu's offload list, is declared as the
                       # menu cells declared it - the desk's fit check and the window's residency check honour it
                       + (["--offload-ok"] if _offload_ok(host, model) else [])),
            "progress_glob": "{ORDER_OUT}\\out.progress\\*.done", "notify": "failures", "priority": PRIORITY}
    clock = (getattr(config, "WINDOW_GPU_CLOCK", None) or {}).get(host)
    if clock:
        # the factory applies it while the window serves and returns the card to the box's own state when it ends
        # (p20_foundry.apply_order_clock / restore_box_clock); config.WINDOW_GPU_CLOCK has the why
        spec["gpu_clock"] = clock
    reply = (intake or _intake)(spec, ["order.compute", "model." + model], host)
    steps = [s for s in (reply.get("steps") or []) if isinstance(s, dict)]
    if reply.get("held"):
        _cancel(reply.get("id"), "a chat turn routes around a held box")
        raise WindowUnavailable("%s is not available for %s now (held: %s)" % (
            host, model, "; ".join(reply.get("why") or []) or "no reason given"))
    if not reply.get("ok") or len(steps) != 1 or not steps[0].get("id"):
        raise WindowUnavailable("the desk refused a %s window on %s: %s" % (
            model, host, reply.get("error") or "; ".join(reply.get("why") or []) or "no step placed"))
    w = Window(host, model, num_ctx, name, local, steps[0]["id"])
    w.strata = bool(strata)
    w.opened = t_open
    _write_identity(w)
    _beat_owner(w)
    _set_idle(w)
    notes.log("CHAT WINDOW opened %s desk=%s" % (name, w.desk_id))
    return w


TOLKIEN_ORDER_FIELDS = ("gpu", "fit_target", "build", "threads", "reasoning", "temperature")


def _intake_tolkien(tspec, family="", tier=""):
    """The Tolkien box's window through the desk as a Fellowship order (fellowship_turn.submit's form): Tolkien's spec under
    `fellowship`, pinned to the Fellowship, needing the Tolkien box's own lane (the Tolkien box.llm`), beside the GPU template the desk
    requires of every order. The desk ranks a Fellowship form by the weighed family and tier, so they ride along."""
    import scheduler
    from app import FactoryOrderReq, p20_order
    gpu = {"name": tspec["name"], "mode": "compute", "load": "gpu", "initiator": "hai",
           "payload": "{FOUNDRY_ROOT}\\bin\\helper_window.py", "args": [], "notify": "failures", "priority": PRIORITY}
    return p20_order(FactoryOrderReq(spec=gpu, requires=["order.compute"], only=[scheduler.FELLOWSHIP_HOST],
                                     family=family or "", tier=tier or "", lens="fastest",
                                     fellowship={"spec": tspec, "requires": ["rigby.llm"]}, rush=True))


def _open_tolkien_window(name, local, model, num_ctx, intake, t_open):
    """A `chat.window` order to Tolkien for the Tolkien box (tolkien_llm.run_window): the same window folder on the jobs share as
    Tolkien mounts it, the weighed row's own settings, the box's keep_alive as its idle rule."""
    import scheduler
    fields = getattr(_TOLKIEN_ORDER, "fields", None) or {}
    tspec = {"kind": "chat.window", "name": name, "model": model,
             "items_dir": config.CHAT_WINDOWS_TOLKIEN.rstrip("/") + "/" + name, "ctx": int(num_ctx),
             "idle_ttl": keep_alive_seconds(config.KEEP_ALIVE) or int(PERIOD_S), "priority": PRIORITY,
             "notify": "failures", "initiator": "hai"}
    tspec.update({k: fields[k] for k in TOLKIEN_ORDER_FIELDS if fields.get(k) is not None})
    if tspec.get("temperature") is not None:
        tspec["temperature"] = float(tspec["temperature"])
    reply = intake(tspec) if intake else _intake_tolkien(tspec, fields.get("family"), fields.get("tier"))
    steps = [s for s in (reply.get("steps") or []) if isinstance(s, dict)]
    if reply.get("held"):
        _cancel(reply.get("id"), "a chat turn routes around a held %s" % TOLKIEN_BOX)
        raise WindowUnavailable("%s is not available for %s now (held: %s)" % (
            TOLKIEN_BOX, model, "; ".join(reply.get("why") or []) or "no reason given"))
    if not reply.get("ok") or len(steps) != 1 or not steps[0].get("id") \
            or steps[0].get("host") != scheduler.FELLOWSHIP_HOST:
        raise WindowUnavailable("the desk refused a %s window on %s: %s" % (
            model, TOLKIEN_BOX, reply.get("error") or "; ".join(reply.get("why") or []) or "no step placed"))
    w = Window(TOLKIEN_BOX, model, num_ctx, name, local, steps[0]["id"])
    w.strata = True                     # an OpenAI-wire engine of its own: no ollama slot to lend
    w.opened = t_open
    _write_identity(w)
    _beat_owner(w)
    _set_idle(w)
    notes.log("CHAT WINDOW opened %s on %s desk=%s" % (name, TOLKIEN_BOX, w.desk_id))
    return w


def window_for(host, model, num_ctx, intake=None, strata=False):
    with _LOCK:
        w = _WINDOWS.get((host, model, num_ctx))
        if w is not None and w.ended():
            _WINDOWS.pop(w.key, None)
            w = None
        if w is None:
            w = _open(host, model, num_ctx, intake=intake, strata=strata)
            _WINDOWS[w.key] = w
            _ensure_beat()
        return w


def _approx_tokens(item):
    """An item's messages and tools in tokens, at the lab's measured characters per token (ollama_client's own figure, one
    derivation). None when it cannot be counted."""
    try:
        import ollama_client
        chars = len(json.dumps(item.get("messages") or [], default=str)) + len(json.dumps(item.get("tools") or [], default=str))
        return int(chars / ollama_client.COMPACT_CHARS_PER_TOKEN)
    except Exception:                                               # noqa: BLE001
        return None


def card_windows(host, exclude_run=None):
    """This process's live windows on `host` -> [{"model", "in_use" (another run than `exclude_run` holds it), "context_tokens"}]:
    what placing a model there would displace (scheduler._displacement_cost)."""
    with _LOCK:
        return [{"model": w.model, "in_use": bool(w.users - {exclude_run}), "context_tokens": w.context_tokens}
                for w in list(_WINDOWS.values()) if w.host == host and not w.ended()]


def held_on(host):
    """The model this process's open window on `host` holds, or None. Its desk claim and the factory's run-state are
    THIS Kirk's turns, not another job's (scheduler.own_window: a turn for that model joins the window). A window whose
    order still waits at the desk holds no card: the box is then judged by what runs there, like any other."""
    with _LOCK:
        for w in list(_WINDOWS.values()):
            if w.host == host and not w.ended() and _served(w):
                return w.model
    return None


def is_own_window(holder):
    """True when a desk claim's `holder` is one of THIS process's live windows (its run-state name starts with the folder name)."""
    h = str(holder or "")
    if not h.startswith("hai-win-"):
        return False
    with _LOCK:
        return any(h.startswith(w.name) and not w.ended() for w in _WINDOWS.values())


def orphan_window(holder):
    """The name of a window a PREVIOUS Kirk process left, when it is what a desk claim's `holder` names (the window
    order's run-state name, which starts with the folder name), or None. After a restart this process holds none of the
    old windows (held_on), so their claims read busy like any order until they end. That is right for a stranger's order
    and wrong for our own leftover: a job resumed at 17:14, found the lead "held" by its own pre-restart
    Flash-Next window (which ended 17:16) and was sent to an 8 GB box."""
    h = str(holder or "")
    if not h.startswith("hai-win-"):
        return None
    with _LOCK:
        if any(h.startswith(w.name) for w in _WINDOWS.values()):
            return None                        # this process's own live window: never an orphan
    try:
        names = [n for n in os.listdir(config.CHAT_WINDOWS_DIR) if n.startswith("hai-win-")]
    except OSError:
        names = []
    for n in sorted(names, key=len, reverse=True):
        if h.startswith(n):
            return n
    return h                                   # its folder already retired; the order is finishing - still ours


def close_orphan(name):
    """End a previous Kirk's window the documented way (CLOSE in its folder). -> True when CLOSE is written. A missing
    folder is not an error: the window is already ending."""
    d = os.path.join(config.CHAT_WINDOWS_DIR, str(name or ""))
    if not str(name or "").startswith("hai-win-") or os.path.dirname(os.path.abspath(d)) != os.path.abspath(
            config.CHAT_WINDOWS_DIR):
        return False
    try:
        open(os.path.join(d, CLOSE), "w").close()
        return True
    except OSError:
        return False


def idle_cache(host, model):
    """This process's window on `host` holding ANOTHER model while no run uses it, or None. VRAM is a cache: an idle
    window is not work, so a turn for a different model closes it and takes the card (close_idle) instead of reading the
    card as held. smoke test: after a Hard turn the idle Flash-Next window held lead for its 5-min idle
    rule - the 27B pin was refused as "a game" and a Nursing turn found no box at all."""
    with _LOCK:
        for w in list(_WINDOWS.values()):
            if w.host == host and w.model != model and not w.users and not w.ended():
                return w
    return None


UNSTARTED = ("pending", "held", "claimed")


def _cancel_unstarted(w, why):
    """A closed window whose order has not started is cancelled at the desk too. CLOSE ends a RUNNING window (it reads
    the file); an order still in a factory's queue reads nothing, so it stayed queued for good. an idle
    qwen3-coder window closed at 21:10 had been queued on the lead since 10-28, did not fit beside the desktop's
    1.4 GB, retried every ~36 s at the head of the queue, and both Flash-Next research runs behind it never started.
    A running order is left to CLOSE: the desk's cancel cannot stop one."""
    if w.desk_status() in UNSTARTED:
        _cancel(w.desk_id, why)


def close_idle(host, model):
    """End every idle window of this process on `host` that holds another model (CLOSE, the documented way the lead ends
    a window): the card goes to the turn that needs it now. A window any run is using is never touched. -> names closed."""
    with _LOCK:
        idle = [w for w in list(_WINDOWS.values())
                if w.host == host and w.model != model and not w.users and not w.ended()]
    closed = []
    for w in idle:
        try:
            open(os.path.join(w.dir, CLOSE), "w").close()
            closed.append(w.name)
        except OSError:
            continue
        _cancel_unstarted(w, "closed idle before it started: the card goes to %s" % model)
    if closed:
        try:
            notes.log("CHAT WINDOW closed idle %s on %s: the card goes to %s (an idle window is a cache)"
                      % (", ".join(closed), host, model))
        except Exception:                                            # noqa: BLE001
            pass
    return closed


def await_card_freed(host, names, watch=None, sleep=time.sleep, clock=time.time):
    """After close_idle: wait - on STATUS, not a clock - until the closed windows have ENDED and the card has pushed a
    load report taken after that, so the new window is sized against what the card holds now. re-test: the
    door sized the 27B before the idle Flash-Next engine let go, its ~22 GB counted as a tenant, and every model on
    the lead was refused. The only bound is the reporter's own freshness budget (scheduler.GPU_LOAD_FRESH_S) x3 with
    no newer report - then the caller sizes on what it has, which may refuse: could-not-tell is never "free".
    -> True when a report after the end was seen."""
    import scheduler
    with _LOCK:
        ws = [w for w in _WINDOWS.values() if w.name in set(names)]
    budget = 3 * float(getattr(scheduler, "GPU_LOAD_FRESH_S", 180))
    ended_at, t0 = None, clock()
    while True:
        cancellation.check()
        if watch is not None:
            watch.beat()                       # waiting for the card is a status, not a silent model
        if ended_at is None and all(w.ended() for w in ws):
            ended_at = clock()
        if ended_at is not None:
            rec = scheduler.gpu_load(host)
            age = scheduler._report_age_s(rec) if rec else None
            if age is not None and clock() - age > ended_at:
                return True
        if clock() - (ended_at or t0) > budget:
            notes.log("CHAT WINDOW %s: no card report after the idle window ended (%s) - sizing on the last one"
                      % (host, ", ".join(names)))
            return False
        sleep(POLL_S * 10)


def serving_others(host, run_key=None):
    """True when ANOTHER run holds this process's window on `host` - a new turn there would wait in line behind that
    run's work (B4b: an Auto request that would wait behind another user goes to the next box). A run holds its window
    from its first turn to its end, tools included: 's first version counted only a turn GENERATING, and the
    live proof's second chat arrived while the first ran six tools between turns, went to the lead, and waited ~5.5
    min behind the first chat's long final turn. The asking run never counts."""
    with _LOCK:
        return any(w.host == host and (w.users - {run_key}) and not w.ended() for w in list(_WINDOWS.values()))


class CardNotLent(RuntimeError):
    """The window could not lend its card now (a run took it, it refused, or it did not answer)."""


def lendable(host):
    """This process's window on `host` that can lend its card now - nobody using it, not ended, not Strata - or None."""
    with _LOCK:
        for w in list(_WINDOWS.values()):
            if w.host == host and not w.users and not w.strata and not w.ended():
                return w
    return None


@contextlib.contextmanager
def lend(w, holder, wait_s, clock=time.time, sleep=time.sleep):
    """Borrow idle window `w`'s card for `holder` (McCoy): LEND, then the window's LENT (it saved its conversation),
    then the caller's own model call. LEND is withdrawn by any route; the window keeps LENT until its
    next item takes the card back. Raises CardNotLent when a run takes the window, it refuses, or it does not answer
    within `wait_s` - the caller's own request clock (it polls its folder each second; a save is 0.3-3 s, RUNBOOK
    § SLOT SAVE). A window that ENDED meanwhile freed the card, which is all the caller needed."""
    lend_p, lent_p = os.path.join(w.dir, LEND), os.path.join(w.dir, LENT)
    _write_json(lend_p, {"holder": holder, "t": time.time()})
    refused = False
    try:
        t0, n = clock(), 0
        while not os.path.exists(lent_p):
            if w.users:
                raise CardNotLent("a run took %s's window first" % w.host)
            n += 1
            if n % 10 == 0 and w.ended():
                break
            if clock() - t0 > wait_s:
                raise CardNotLent("%s's window did not lend its card within %.0f s" % (w.host, wait_s))
            sleep(POLL_S)
        else:
            try:
                with open(lent_p, encoding="utf-8") as f:
                    ans = json.load(f)
            except (OSError, ValueError):
                ans = {}
            if ans.get("refused"):
                refused = True
                raise CardNotLent("%s's window refused: %s" % (w.host, ans["refused"]))
            notes.log("CHAT WINDOW %s lent its card to %s (slot %s)" % (
                w.name, holder, "saved" if ans.get("saved") else "not saved: %s" % (ans.get("why") or "?")))
        yield w
    finally:
        for p in (lend_p,) + ((lent_p,) if refused else ()):
            try:
                os.remove(p)
            except OSError:
                pass


def use(w, run_key):
    with _LOCK:
        new = run_key not in w.users
        w.users.add(run_key)
        _set_idle(w)
        if new:
            try:
                _beat_owner(w)     # OWNER.json names the runs using it: the next Kirk process reads that (adopt_windows)
            except OSError:
                pass               # the regular beat says it within a period


def release(run_key):
    """A run is over (or a ledger-less call ended): windows it held go IDLE when nobody else is using them."""
    with _LOCK:
        for w in _WINDOWS.values():
            if run_key in w.users:
                w.users.discard(run_key)
                _set_idle(w)


def _read_from(path, offset):
    """Bytes of `path` from `offset` to the server's EOF, through no cached page; b"" when not there yet. A tail the
    client knows exists but has not received reads as NUL bytes: it is cut back to the last whole line."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))   # Windows os.open is TEXT mode by default
    except FileNotFoundError:
        return b""
    try:
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        os.lseek(fd, offset, os.SEEK_SET)
        parts = []
        while True:
            part = os.read(fd, 1 << 20)
            if not part:
                break
            parts.append(part)
    finally:
        os.close(fd)
    got = b"".join(parts)
    if b"\x00" in got:
        got = got[:got.index(b"\x00")]
        got = got[:got.rfind(b"\n") + 1]
    return got


def _read_row(path):
    """The window's row for a turn, or None while it is absent or not yet whole on this client."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def _box_reporting(host):
    """True while `host`'s own load report is fresh. Never raises: could-not-tell is False, and the clock applies."""
    try:
        import ollama_client
        import scheduler
        rec = scheduler.gpu_load(host)
        age = ollama_client._report_age(rec, time.time()) if rec and not rec.get("stale") else None
        return age is not None and age <= scheduler.GPU_LOAD_FRESH_S
    except Exception:                                                       # noqa: BLE001
        return False


class _Stream(httpx.SyncByteStream):
    """The window's stream file for one item, as the engine's own NDJSON lines."""

    def __init__(self, w, iid, watch, sleep=time.sleep, sse=False, first=False, t_item=None):
        self.w, self.iid, self.watch, self.sleep = w, iid, watch, sleep
        self.sse = sse                            # an OpenAI (Strata) turn: replay as `data:` lines, end with [DONE]
        self.closed = False
        self.finished = False                     # the row arrived and was read to its end
        self.parked = False                       # the window parked this turn mid-answer and kept its state (TurnParked)
        self.withdrawn = False                    # a park asked before the window started it: taken back on the spot
        self.first = first and not sse            # the first turn of a new ollama window: its first output is measured
        self.measure = not sse                    # every ollama turn: first visible output, for the menu (record_turn)
        self.first_turn = bool(first)
        self.t_item = t_item or time.time()       # when the turn's item was written: the turn's own clock
        self.t_first = None

    def _line(self, obj):
        body = json.dumps(obj, ensure_ascii=False)
        return (("data: " + body) if self.sse else body).encode("utf-8") + b"\n"

    def _guard(self):
        cancellation.check()
        if self.watch is not None and self.watch.verdict is not None:
            raise httpx.ReadError(self.watch.detail or "the window's turn was ended by supervision")

    def __iter__(self):
        """The stream file is written on a FACTORY and read here over the CIFS mount (cache=strict, actimeo=1), whose
        cached size and pages trail the server: thelive proof got 0 lines on every turn, and a
        cross-machine repro read 0/5 turns whole - NUL-filled tails, sizes that went backwards, rows seen before the
        last lines. So each poll opens the file, drops this client's pages for it (posix_fadvise DONTNEED) and reads
        to EOF from the server; a cached size is never trusted; a tail with NUL bytes is not written yet; and a turn
        is over at the ENGINE'S done chunk, or - for one the window ended without it (a loop cut, a Stop) - when the
        row's `stream_chunks` have all been read. Same repro with this rule: 20/20 whole."""
        spath = os.path.join(self.w.dir, self.iid + STREAM)
        opath = os.path.join(self.w.dir, self.iid + OUT)
        offset, attempt, buf, polls, chunks = 0, None, b"", 0, 0
        while not self.closed:
            self._guard()
            row = _read_row(opath)                 # read BEFORE the stream: a row means every line is on the server
            got = _read_from(spath, offset)
            if (row is None and attempt is None and offset == 0 and not got
                    and os.path.exists(os.path.join(self.w.dir, self.iid + CUT))):
                # A park was asked for a turn the window has NOT started: its order waits for the card. There is nothing to
                # cut, so the turn is taken back at once; open_stream marks it cancelled, which the window answers
                # without the engine, the slot or the chat's state when it does reach it (helper_window CANCEL).
                self.parked = self.withdrawn = True
                raise TurnParked("window %s had not started this turn (its order waits for the card); the turn is "
                                 "withdrawn and asked again when the run goes on" % self.w.name)
            offset += len(got)
            buf += got
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                if not raw.strip():
                    continue
                obj = json.loads(raw.decode("utf-8"))
                if "attempt" in obj and set(obj) <= {"attempt", "id"}:
                    if attempt is not None:
                        raise httpx.ReadError("the window started this turn over")
                    attempt = obj["attempt"]
                    continue
                chunks += 1
                if self.watch is not None:
                    self.watch.beat()
                if self.measure and self.t_first is None:
                    self.t_first = time.time()     # the first output Kirk read: what the user waits for (B1)
                if obj.get("done"):
                    self.finished = True
                    if self.first:
                        record_first(self.w, self.t_first, obj)
                    if self.measure:
                        record_turn(self.w, self.t_item, self.t_first, obj, self.first_turn)
                yield self._line(obj)
                if self.finished:
                    return
            if row is not None:
                if row.get("cut"):
                    self.parked = True
                    raise TurnParked("window %s parked this turn mid-answer (%s); %s" % (
                        self.w.name, str(row["cut"])[:120],
                        "its state is saved and the answer continues" if row.get("cut_saved") and row.get("cut_mid_answer")
                        else "its answer so far is lost: asked again, it starts over"))
                if row.get("error"):
                    self.finished = True
                    from model_input_error import MediaInputRejected, window_media_rejection
                    if window_media_rejection(row):
                        raise MediaInputRejected("The model server rejected an image before accepting the request")
                    raise httpx.ReadError("window %s: %s" % (self.w.name, row["error"][:300]))
                if chunks >= int(row.get("stream_chunks") or 0):
                    # every chunk the window relayed is read: an OpenAI turn ends the wire's way ([DONE]; its
                    # finish_reason came in the chunks); an ollama turn without a done chunk is a cut turn to its caller
                    self.finished = True
                    if row.get("done_reason") == "loop":
                        raise TurnLooped("window %s ended this turn: the model's output was repeating itself" % self.w.name)
                    if self.sse:
                        yield b"data: [DONE]\n"
                    return
            if os.path.exists(os.path.join(self.w.dir, CLOSE)):
                raise httpx.ReadError("the window %s ended before answering" % self.w.name)
            polls += 1
            if polls % int(PERIOD_S / POLL_S) == 0:
                st = self.w.desk_status()
                if st == "pending" and self.watch is not None:
                    self.watch.beat()             # waiting in the desk's line is a status, not a silent model
                elif st in ("claimed", "running") and not chunks and self.watch is not None and _box_reporting(self.w.host):
                    # the factory has the order and is opening the window (its gates, the engine's load): a status too, with
                    # the box itself still talking. Five clocked turns one ending run-f. A wedged open is the
                    # factory's to end (the order then reads failed, below); a dead box stops reporting and the clock applies.
                    self.watch.beat()
                elif st == "failed" and not chunks:
                    # the window's order FAILED before any output: the box could not open this model - asking again changes nothing
                    raise WindowFailedAtOpen("the window %s is failed before any output" % self.w.name)
                elif st in ("done", "failed", "cancelled"):
                    raise httpx.ReadError("the window %s is %s" % (self.w.name, st))
            self.sleep(POLL_S)

    def close(self):
        self.closed = True


def sized_ctx(host, model, requested):
    """The window an ollama turn opens on `host`: the requested (full, measured) window when it fits beside what the card
    holds now, else the largest smaller MEASURED point that does (scheduler.window_ctx - the desk's own arithmetic on the
    card's live report). Sized
    HERE because every chat turn crosses this door whichever path chose the box (the weighed row, the cascade, the
    recovery list) - one place, not one per caller. An OPEN window for the same model on this box at a size between
    the two is joined rather than reopened (a model reload per turn would follow the desktop up and down). No measured
    point fits -> WindowUnavailable: the box cannot take the turn now, the next row / box serves it. Could-not-size
    keeps the request (the desk's check at open still decides)."""
    try:
        import scheduler
        n, why = scheduler.window_ctx(host, model, requested)
    except Exception as exc:                                        # noqa: BLE001 - the desk still checks at open
        notes.log("WINDOW SIZE %s %s: sizing unavailable (%r) - the desk decides at open" % (host, model, exc))
        return requested
    if n is None:
        # A window Kirk itself has OPEN for this model on this box is the model in place: what the card holds beside it
        # is that window's own use. bh-d: the window had just evicted and reloaded its declared split,
        # the box's minute-old load report still said "not loaded", and the job's next turn was refused its own model
        # ("needs 10710 MiB of RAM: 9009 free") and ended unanswered. Kirk's own record is the fresher fact.
        with _LOCK:
            open_sizes = [w.num_ctx for w in _WINDOWS.values() if w.host == host and w.model == model
                          and w.num_ctx <= requested and not getattr(w, "strata", False)
                          and not os.path.exists(os.path.join(w.dir, CLOSE))]
        if open_sizes:
            notes.log("WINDOW SIZE %s %s: the card reads full (%s), but this model's own window is open there - joined "
                      "at %d" % (host, model, why[:120], max(open_sizes)))
            return max(open_sizes)
        notes.log("WINDOW SIZE %s %s: no measured window fits beside the card's current use (%s)" % (host, model, why[:160]))
        raise WindowUnavailable("no measured %s window fits on %s beside the card's current use: %s" % (model, host, why))
    if n < requested:
        with _LOCK:
            open_sizes = [w.num_ctx for w in _WINDOWS.values() if w.host == host and w.model == model
                          and n <= w.num_ctx <= requested and not getattr(w, "strata", False)]
        if open_sizes:
            n = max(open_sizes)
        notes.log("WINDOW SIZE %s %s: %d of %d (sized to the card: %s)" % (host, model, n, requested, why[:120]))
    return n


def park_turn(run_key):
    """Ask the window to PARK the turn this run is reading now: cut it mid-answer and keep its state (CUT) -> the
    window's name, or None when the run has no turn in flight (it is in a tool, or between turns: it stops at its next
    natural point as before). a Strata window continues a cut too (strata_continue_patch.py, proven on a
    short conversation). Never raises: a park that cannot be asked costs time, never the run."""
    with _LOCK:
        hit = next(((w, w.inflight[run_key]) for w in _WINDOWS.values() if run_key in w.inflight), None)
    if hit is None:
        return None
    w, iid = hit
    try:
        open(os.path.join(w.dir, iid + CUT), "w").close()
    except OSError as exc:
        notes.log("CHAT WINDOW %s: the park for turn %s was not written (%r)" % (w.name, iid, exc))
        return None
    notes.log("CHAT WINDOW %s: turn %s asked to park mid-answer (run %s)" % (w.name, iid, run_key))
    return w.name


@contextlib.contextmanager
def open_stream(url, body, watch, run_key, intake=None, conv=None, cut_ok=False, shared_tools=0):
    host = _host_for(url)
    if host is None:
        raise httpx.ConnectError("no factory card serves %s" % url)
    strata = is_swap_url(url)
    if strata and host == TOLKIEN_BOX:
        # The Tolkien box serves the context its weighed row was priced at (the row's `order` fields, tolkien_order)
        num_ctx = int((getattr(_TOLKIEN_ORDER, "fields", None) or {}).get("ctx") or 0)
    elif strata:
        # Strata serves its tested context (the plan's, = config.MODEL_NUM_CTX): the request carries none
        import scheduler
        num_ctx = int(config.ctx_for(scheduler.CARDS[host]["url"], body["model"], None) or 0)
    else:
        num_ctx = int((body.get("options") or {}).get("num_ctx") or 0)
    if not num_ctx:
        raise httpx.ConnectError("a window turn needs its measured context")
    with _LOCK:
        # one model per card: a run moving to another model on this box lets go of its window there first, or the new
        # window would wait in the lane behind a window this same run holds (a deadlock, not a queue)
        for other in _WINDOWS.values():
            if other.host == host and other.model != body["model"] and run_key in other.users:
                other.users.discard(run_key)
                _set_idle(other)
    # an idle window holding another model is a cache: this turn takes the card - and is SIZED only after that window
    # has let go of it (await_card_freed), never against the engine it is replacing
    closed = close_idle(host, body["model"])
    if closed:
        await_card_freed(host, closed, watch)
    if not strata:
        num_ctx = sized_ctx(host, body["model"], num_ctx)
    w = window_for(host, body["model"], num_ctx, intake=intake, strata=strata)
    use(w, run_key)
    iid = _claim_item(w, body, run_key)      # back after a restart: the window may already have this very turn
    first = False
    t_item = time.time()
    if iid is None:
        with _LOCK:
            w.n += 1
            first = w.n == 1
            iid = "t%05d-%s" % (w.n, uuid.uuid4().hex[:8])
        item = {"id": iid, "model": body["model"], "messages": body.get("messages") or [], "stream": True}
        if strata:
            # the effort rides as Kirk sent it (swap_think_kwargs); the window reads it back into its own think value
            item["chat_template_kwargs"] = dict(body.get("chat_template_kwargs") or {})
        else:
            # the window's own (possibly sized) context, never the request's: ollama reloads a model asked at another num_ctx
            item.update(think=body.get("think", False), options=dict(body.get("options") or {}, num_ctx=w.num_ctx))
        if body.get("tools"):
            item["tools"] = body["tools"]
            # how much of the list every chat carries (the hot start). A MASTER block (ollama_client.sticky_tools) is
            # shared whole - Kirk says so, since its tools are the selector's full always-offered set (capability entries
            # and a persona's own included), which the leading-core count below would stop short of
            item["hot_tools"] = (len(body["tools"]) if shared_tools and shared_tools == len(body["tools"])
                                 else shared_tool_count(body["tools"]))
        if conv:
            item["conv"] = conv  # the chat this turn belongs to: the window keeps that chat's state (helper_window.ConvSlots)
            import scheduler
            rate = scheduler.read_rate(host, body["model"])
            if rate:
                item["read_tok_s"] = rate      # restore OR re-read: the window takes whichever is faster
            if conv_home(conv) == (host, body["model"], int(w.num_ctx)):
                # this box saved this chat's state: if retention has moved it to the array, the window brings it back when
                # that beats re-reading it at the menu's measured rate (helper_window.fetch_from_array)
                item["state_here"] = True
        if body.get("keep_alive"):
            item["keep_alive"] = body["keep_alive"]
        if cut_ok and conv:
            # the caller asks a parked turn again (the chat loop does): the factory may park this one for the card, not
            # only on Kirk's own <id>.cut. Every window continues a cut Strata (Flash-Next) included
            # (strata_continue_patch, installed on the lead): until then a factory's YIELD made a Flash-Next turn finish
            # first, while Kirk's own park already cut it - one lossless pause for every caller
            item["cut_ok"] = True
        _write_json(os.path.join(w.dir, iid + ITEM), item)
        w.context_tokens = _approx_tokens(item)
    stream = _Stream(w, iid, watch, sse=strata, first=first, t_item=t_item)
    resp = httpx.Response(200, request=httpx.Request("POST", url), stream=stream,
                          extensions={"factory_host": host, "factory_order_id": w.desk_id})
    with _LOCK:
        w.inflight[run_key] = iid
    try:
        yield resp
    finally:
        resp.close()
        with _LOCK:
            if w.inflight.get(run_key) == iid:
                w.inflight.pop(run_key, None)
        if stream.parked and not stream.withdrawn:
            if conv:
                record_home(conv, host, w.model, w.num_ctx)  # the parked turn's state is in that window's chat file
        elif stream.withdrawn or not stream.finished:
            # Stop, a supervision verdict or an error ended the read: the window ends that generation too, so the
            # card is not left writing an answer nobody reads (helper_window CANCEL)
            try:
                open(os.path.join(w.dir, iid + CANCEL), "w").close()
            except OSError:
                pass
        elif conv:
            record_home(conv, host, w.model, w.num_ctx)      # this chat's state now lives in that window (ConvSlots)
