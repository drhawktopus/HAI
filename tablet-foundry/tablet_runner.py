"""THE TABLET ORDER TAKER — consume orders from the queue, forever.

Design: the library's `Tablet-Fleet-Order-Taker-Design.md`.
Desk work is in `tablet_foundry.py`; line work is in `tablet_fulfil.py`. This is the loop.

Mirrors the GPU factory's `run_queue()` deliberately -- same stages, same vocabulary, same
singleton discipline -- with ONE structural difference: the GPU factory is a serial lane because it
owns one card; this is serial in ORDERS and parallel in ITEMS across five independent nodes.

SUBMITTING WORK, from any box: drop a `*.tjob.json` in the INBOX on the share
(the TABLET_INBOX_HOST share folder, seen in the container as TABLET_INBOX). This loop
accepts it, fans it, validates the control, DMs the outcome, copies the results to the OUTBOX and
clears the line. No session has to be driving.
"""
import contextlib
import hashlib
import json
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tablet_foundry import (ARCHIVE_DIR, CURRENT, CURRENT_RIGBY, DETECT_HEAD_PORT, LANE, QUEUE_DIR, RESULTS_DIR,
                            STATE_DIR, TABLET_CLASSES, TABLET_SKU, acquire_singleton, archive_stopped_spec, beat, lane_name,
                            collect_fleet_orders, detect_capabilities, detect_head_serve, fleet_running_names,
                            push_capacity_if_changed, mccoy_shadow,
                            drain_inbox, expand_triage_extract, fleet_health, lane_housekeeping, log, note_retired,
                            read_reservation, reservation_admits, RIGBY_RUNNING,
                            notify_desk, now, order_stem, process_slips, publish, queued,
                            report_fleet_result, resend_desk_reports, ship_to_outbox, spec_defects, try_repair)

import tablet_foundry                                   # its CAPACITY hook is set when the order taker starts
from scheduler import TABLETS as _SCHED_TABLETS
from tablet_fulfil import (_available_hosts, clear_stage_dirs, row_key, run_order, useful_nodes, validate_complete,
                           validate_control, validate_distinct_workers)

POLL = float(os.environ.get("TABLET_POLL", "30"))
STOP_POLL = float(os.environ.get("TABLET_STOP_POLL", "5"))
LANE_BUSY_RETRY_S = float(os.environ.get("TABLET_LANE_BUSY_RETRY", "30"))
# QUICK_POLL_S (default 1s) is the cadence for checking for new work between full passes (POLL). It must remain low to ensure immediate task pickup; do not increase it to the full POLL interval.
QUICK_POLL_S = float(os.environ.get("TABLET_QUICK_POLL", "1"))


def _idle_wait(poll, collect, sleep=None, clock=None):
    """Sleep up to `poll` seconds in QUICK_POLL_S steps, calling `collect()` after each; return early (True) the moment
    it reports work. A failing `collect` is the full pass's problem, not this wait's (the GPU takers' idle wait)."""
    sleep = sleep or time.sleep
    clock = clock or time.time
    t0 = clock()
    while True:
        left = poll - (clock() - t0)
        if left <= 0:
            return False
        sleep(min(QUICK_POLL_S, left))
        try:
            if collect():
                return True
        except Exception:                                                # noqa: BLE001
            pass


def open_to_start(pending):
    """What a lane's pick may look at: nothing while a deploy holds intake."""
    return [] if tablet_foundry.intake_held() else pending


def _work_waiting(lane, waiting, now=None):
    """A queued order of `lane` that is not waiting out a LaneBusy - the lane's reason to start a pass now."""
    now = time.time() if now is None else now
    if tablet_foundry.intake_held():
        return False                         # or the lane wakes every second for the whole hold
    try:
        return any(lane_of(p) == lane and waiting.get(p, 0) <= now for p in queued())
    except Exception:                                                    # noqa: BLE001
        return False


def _fleet_work_waiting(waiting, now=None):
    """A queued fleet order the dispatcher could START now: not running, not waiting out a LaneBusy, and not refused for
    busy tablets with nothing given back since (that one wakes on NODES_FREED). Running orders stay in the queue dir
    while they run, so _work_waiting alone would wake the loop every second for nothing."""
    now = time.time() if now is None else now
    if tablet_foundry.intake_held():
        return False
    try:
        for p in queued():
            if lane_of(p) != "fleet" or order_stem(p) in FLEET_RUNNING or waiting.get(p, 0) > now:
                continue
            b = BLOCKED.get(p)
            if b is not None and b["gen"] == NODES_GEN[0]:
                continue
            return True
    except Exception:                                                    # noqa: BLE001
        return False
    return False


class LaneBusy(RuntimeError):
    """Enough healthy nodes exist for the order, but not enough are FREE right now (some are still
    finishing a speculative item from the previous order). A property of the moment, not of the
    order: the order stays queued untouched, the taker moves on to the next one, and comes back."""


# One job per node. An order acquires only the nodes it needs and releases them via run_one's finally; the next order starts immediately on free nodes.
# Larger work (windows, cohorts) acquires multiple nodes. Reserved nodes (LAUNCH_RESERVED) must not be taken by subsequent orders.
NODES_LK = threading.Lock()
NODE_HOLDER = {}                 # tablet -> the running fleet order (stem) that holds it
NODES_GEN = [0]                  # bumped on every give-back: an order refused for busy tablets retries once it moves
NODES_FREED = threading.Event()  # set on every give-back - the dispatcher's wake (status, not a clock)
LAUNCH_RESERVED = {}             # stem -> tablets an order AHEAD of it is waiting for: this order may not take them
STARTED = {}                     # stem -> Event: the order took its tablets, or ended (the dispatcher waits on it)
BLOCKED = {}                     # path -> {"wants", "gen"}: refused for tablets held by other orders (NodesBusy)
FLEET_RUNNING = {}               # stem -> thread, every fleet-lane order running right now
TAKEN = {}                       # stem -> the tablets the order took (McCoy's shadow records them beside his advice)


def tolkiens_decision(spec, hosts):
    """What Tolkien decided for one piece, in the terms of McCoy's questions (worker, tries, escalate) - logged beside
    McCoy's advice so the questions can be measured (mccoy_shadow)."""
    workers = sorted({"rigby" if h == "rigby" else "tablet-%s" % (tablet_class(h) or "?") for h in hosts})
    return {"worker": workers, "hosts": sorted(hosts), "tries": spec.get("replicates") or 1, "escalate": "stay"}


class NodesBusy(LaneBusy):
    """The tablets this order can use are held by OTHER running fleet orders (or reserved for an order ahead of it).
    A property of the moment: the order stays queued untouched and is retried the moment an order gives tablets back -
    no retry clock. `wants` = every tablet it could use; orders queued BEHIND it may not take those, so a large order
    is never starved by a stream of small ones (it waits for the line to drain, as it always did)."""

    def __init__(self, msg, wants=()):
        super().__init__(msg)
        self.wants = set(wants)


def unavailable_to(key):
    """Tablets order `key` may not take now: held by another running order, or reserved for an order ahead of it."""
    with NODES_LK:
        held = {h for h, k in NODE_HOLDER.items() if k != key}
    return held | set(LAUNCH_RESERVED.get(key) or ())


def take_nodes(key, hosts):
    """Hold `hosts` for order `key`, all or none (another order took one meanwhile = NodesBusy). Tells the
    dispatcher the order has its tablets, so it can start the next one."""
    hosts = list(hosts)
    with NODES_LK:
        clash = sorted(h for h in hosts if NODE_HOLDER.get(h, key) != key)
        if not clash:
            for h in hosts:
                NODE_HOLDER[h] = key
    if clash:
        raise NodesBusy("%s taken by another order meanwhile" % ", ".join(clash), wants=hosts)
    TAKEN[key] = sorted(hosts)       # what the order got, for McCoy's shadow beside it (read once, then dropped)
    log("%s: holds %s (%d tablet%s)" % (key, ", ".join(sorted(hosts)) or "no tablet", len(hosts),
                                        "" if len(hosts) == 1 else "s"))
    ev = STARTED.get(key)
    if ev is not None:
        ev.set()


def give_back(key):
    """Release every tablet order `key` holds. Never raises (it runs in a finally)."""
    try:
        with NODES_LK:
            mine = [h for h, k in NODE_HOLDER.items() if k == key]
            for h in mine:
                del NODE_HOLDER[h]
            NODES_GEN[0] += 1
        NODES_FREED.set()
        if mine:
            log("%s: gave back %s" % (key, ", ".join(sorted(mine))))
    except Exception:                                                # noqa: BLE001
        pass


def _busy_or_short(path, need, free, busy_here, finishing, wants, roster, why):
    """Too few FREE tablets for this order: other orders hold some (NodesBusy - retried when they give back), an earlier
    order's speculative loser still finishes on some (LaneBusy), or the fleet is short of HEALTHY ones (_short_of_nodes:
    WAIT inside the roster, HOLD beyond it)."""
    if busy_here:
        raise NodesBusy("%s; %d free, %s held by other orders" % (why, len(free), ", ".join(sorted(busy_here))),
                        wants=wants)
    if finishing:
        raise LaneBusy("%s; %d free, %s still finishing an earlier item" % (why, len(free), ", ".join(sorted(finishing))))
    _short_of_nodes(path, need, len(free), roster, why)


RIGBY_LOCK = threading.Lock()       # rigby is ONE unit: its lane's order, or a Voltron order, never both
RIGBY_HOLDER = [""]
PICK_LK = threading.Lock()          # the rigby lane's pick and a rebind never interleave


def rigby_busy():
    """rigby cannot start a new piece now: an order (or a Voltron) holds it, or a team run has it reserved."""
    if RIGBY_LOCK.locked():
        return True
    try:
        return read_reservation() is not None
    except Exception:                                                    # noqa: BLE001
        return False


def _move_pays(spec, opt, ahead, weigh=None):
    """Returns (bool, reason) whether to route a queued piece to `opt` instead of waiting for the current rigby.

    `opt` is preferred if it falls within the requested quality band and offers a faster ETA than the current
    queue (ahead + running + self) multiplied by the current rigby's price time. If weighing fails or data is
    missing, defaults to False (stay with the current rigby)."""
    p = spec.get("placement") or {}
    try:
        if weigh is None:
            import scheduler
            weigh = scheduler.weigh_rows
        band, rejected = weigh(p.get("family"), p.get("tier"), interactive=p.get("lens") == "fastest")
    except Exception as e:                                               # noqa: BLE001
        return False, "no weighing (%s) - stays with rigby" % type(e).__name__

    def find(rows, row):
        return next((c for c in rows if c.get("box") == row.get("box")
                     and (c.get("entry") or {}).get("model") == row.get("model")), None)
    o = find(band, opt.get("row") or {})
    if o is None:
        return False, ("%s is below the request's quality band - the owner's overflow rule is similar quality"
                       % (opt.get("row") or {}).get("model"))
    r = find(band + rejected, spec.get("placement_row") or {})
    if r is None:
        return False, "rigby's row is not in the weighing - the wait cannot be priced"
    wait = (ahead + 2) * float(r["time_s"])
    if float(o["time_s"]) >= wait:
        return False, "waiting for rigby (~%.0f s, %d ahead) is not slower than %s (%.0f s)" % (
            wait, ahead, (opt.get("row") or {}).get("model"), float(o["time_s"]))
    return True, "rigby's wait ~%.0f s (%d ahead) vs %.0f s on %s, inside the quality band" % (
        wait, ahead, float(o["time_s"]), (opt.get("row") or {}).get("model"))


_MOVE_SAID = {}                  # path -> the last no-move reason logged (one line per reason, not one per second)


def fleet_capacity():
    """Tolkien's free-spot report (tablet_foundry.CAPACITY): how many tablets, how many no order holds, whether rigby
    is busy, and how many pieces wait in rigby's line (queued for the rigby lane, not the one it is running)."""
    with NODES_LK:
        held = set(NODE_HOLDER)
    tabs = list(_SCHED_TABLETS)
    line = 0
    for p in queued():
        if order_stem(p) != RIGBY_RUNNING[0] and lane_of(p) == "rigby":
            line += 1
    return {"nodes": len(tabs), "free_nodes": sum(1 for t in tabs if t not in held), "rigby_busy": bool(rigby_busy()),
            "rigby_line": line}


def rebind_to_free_worker():
    """Rebinds pieces queued for a busy rigby to the first free tablet in their placement run.

    Presence is validated at collection (resolve_placed); this handles the busyness check at dispatch time.
    Each free tablet receives at most one piece, and the move is atomic."""
    import tolkien_llm
    moved = 0
    with PICK_LK:
        if not rigby_busy():
            return 0
        with NODES_LK:
            free_tabs = [h for h in _SCHED_TABLETS if h not in NODE_HOLDER]
        if not free_tabs:
            return 0
        ahead = 0
        q_now = queued()
        for p in list(_MOVE_SAID):
            if p not in q_now:
                del _MOVE_SAID[p]
        for p in q_now:
            if moved >= len(free_tabs):
                break
            if order_stem(p) == RIGBY_RUNNING[0] or lane_of(p) != "rigby":
                continue
            spec = _spec_or_none(p)
            opts = (spec or {}).get("placement_options") or []
            opt = next((o for o in opts if (o.get("order") or {}).get("arms") == ["warm"]), None)
            if opt is None:
                ahead += 1
                continue
            ok, why = _move_pays(spec, opt, ahead)
            if not ok:
                if _MOVE_SAID.get(p) != why:
                    _MOVE_SAID[p] = why
                    log("%s: rigby is busy, stays queued for it - %s" % (spec.get("name"), why))
                ahead += 1
                continue
            _MOVE_SAID.pop(p, None)
            new = tolkien_llm.rebound(spec, opt)
            tmp = p + ".rebind"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(new, f, indent=2)
                os.replace(tmp, p)
            except Exception as e:                                       # noqa: BLE001
                log("rebind of %s failed (%s) - it stays with rigby" % (os.path.basename(p), type(e).__name__))
                continue
            moved += 1
            log("%s: rigby is busy -> moved to %s %s (%s)"
                % (spec.get("name"), opt["row"].get("box"), opt["row"].get("model"), why))
    return moved


@contextlib.contextmanager
def rigby_claim(name):
    """Hold rigby for one order. Held by someone else = a property of the moment: LaneBusy, never a hold."""
    if not RIGBY_LOCK.acquire(blocking=False):
        raise LaneBusy("rigby is busy with %s" % (RIGBY_HOLDER[0] or "another order"))
    RIGBY_HOLDER[0] = name
    try:
        yield
    finally:
        RIGBY_HOLDER[0] = ""
        RIGBY_LOCK.release()


@contextlib.contextmanager
def rigby_claims(name, spec):
    """rigby's claim, plus the GT 730's when this order puts work on that card (a warm order may hold the 730 as one
    of its nodes while rigby's lane runs a 1060 order beside it). Busy either way = LaneBusy, never a hold."""
    import tolkien_llm
    with rigby_claim(name):
        if not tolkien_llm.spec_uses_gt730(spec):
            yield
            return
        if not tolkien_llm.gt730_try_acquire(name):
            raise LaneBusy("rigby's GT 730 is busy with %s" % (tolkien_llm.GT730_HOLDER[0] or "another order"))
        try:
            yield
        finally:
            tolkien_llm.gt730_release()


def lane_of(path):
    """Which lane takes a queued order: "rigby" (rigby alone) or "fleet". Unreadable = fleet, whose
    build_order HOLDs a broken spec with the reason named."""
    try:
        import tolkien_llm
        return tolkien_llm.lane_of_spec(load_spec(path))
    except Exception:                                                # noqa: BLE001
        return "fleet"


class StopWatch:
    """Routes INBOX slips every STOP_POLL seconds while an order runs, so a hold / release /
    priority on OTHER orders lands mid-run, and a stop for THIS order sets `event`.

    Every running order (one per lane) registers here, and every routing pass names ALL of them, so a slip routed
    from one lane never treats the other lane's running order as queued. Inbox intake runs in the same pass, so an
    order submitted while another runs is in the queue within seconds (and a cancel slip can find it).

    The line (tablet_fulfil.run_order) reads `event` before dealing each batch: set means deal
    nothing new; batches already on a tablet finish or time out under the normal batch contract.
    The rigby lane (tolkien_llm.complete) reads it on every streamed chunk and cuts the answer in flight.
    The stop slip stays in the inbox until the order has shipped; `finish()` then routes it with
    the running order included, so the customer gets ONE DM for one slip."""

    ACTIVE = {}                          # stem -> StopWatch, every order running right now (both lanes)
    _LK = threading.Lock()

    def __init__(self, stem, name=""):
        self.stem, self.name = stem, name
        self.event = threading.Event()
        self.slip = None
        self._halt = threading.Event()
        self._thread = None

    @classmethod
    def running(cls, exclude=None):
        with cls._LK:
            return [{"stem": w.stem, "name": w.name} for s, w in cls.ACTIVE.items() if s != exclude]

    def check(self):
        try:
            drain_inbox()
        except Exception as e:                                       # noqa: BLE001
            log("inbox drain error during %s (%s) - order continues" % (self.stem, type(e).__name__))
        try:
            hits = process_slips(running=StopWatch.running(exclude=self.stem) +
                                 [{"stem": self.stem, "name": self.name}])
        except Exception as e:                                       # noqa: BLE001
            log("slip routing error during %s (%s) - order continues" % (self.stem, type(e).__name__))
            return
        lane_housekeeping()                     # a rigby reservation must land while a long order runs
        with StopWatch._LK:
            targets = [(self if s == self.stem else StopWatch.ACTIVE.get(s), slip) for s, slip in hits.items()]
            targets = [(w, slip) for w, slip in targets if w is not None]
        for w, hit in targets:
            if not w.event.is_set():
                w.slip = hit
                w.event.set()
                log("STOP requested for RUNNING %s by %s (%s): no new work will be started; a rigby-lane "
                    "answer in flight is cut, tablet batches in flight finish" % (w.stem, hit["slip"], hit["why"]))

    def start(self):
        with StopWatch._LK:
            StopWatch.ACTIVE[self.stem] = self
        self.check()

        def loop():
            while not self._halt.wait(STOP_POLL):
                self.check()
        self._thread = threading.Thread(target=loop, daemon=True, name="stopwatch-%s" % self.stem)
        self._thread.start()
        return self

    def close(self):
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=STOP_POLL + 30)
        with StopWatch._LK:
            if StopWatch.ACTIVE.get(self.stem) is self:
                del StopWatch.ACTIVE[self.stem]

    def stopped_record(self):
        if not self.slip:
            return None
        return {"slip": self.slip["slip"], "why": self.slip["why"], "at": now(), "pause": bool(self.slip.get("pause"))}

    def finish(self, results, n_rows):
        """Route the stop slip now that the stopped order has shipped. Never raises."""
        if not self.slip:
            return
        try:
            process_slips(running=StopWatch.running(exclude=self.stem),
                          finished={"slip": self.slip["slip"], "stem": self.stem,
                                    "name": self.name, "results": os.path.basename(results or ""),
                                    "rows": n_rows})
        except Exception as e:                                       # noqa: BLE001
            log("stop-slip routing after %s failed (%s) - the next pass will retry"
                % (self.stem, type(e).__name__))


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def _apply_exclude_hosts(name, spec, hosts):
    """Drop the spec's `exclude_hosts` from an eligible host list. Returns (kept, excluded_that_were_present).

    Names that are not in the list are ignored here (spec_defects rejects unknown nodes at intake),
    so an excluded node that is already unhealthy costs nothing."""
    excluded = {str(h).strip() for h in (spec.get("exclude_hosts") or []) if str(h).strip()}
    if not excluded:
        return list(hosts), []
    kept = [h for h in hosts if h not in excluded]
    return kept, sorted(h for h in hosts if h in excluded)


# Class B and C hardware (<=1.82 GB) cannot run 2B models efficiently (0.2-0.3 tok/s); only Class A (2.79 GB) is suitable. Missing classes default to the max fitting model.
CLASS_MODEL_CEILING_B = {"B": 1.5, "C": 1.5}


def tablet_class(host):
    """A / B / C: its hardware class (TABLET_CLASSES), never its lane. A tablet that runs its own SKU lane for matrix
    orders (TABLET_SKU_LANES; the lab's sam) keeps its hardware class here. None = no class known."""
    return TABLET_CLASSES.get(host)


def model_size_b(model):
    """Parameter count in billions from a model's name ('Qwen3.5-2B' 2.0, 'SmolVLM2-500M' 0.5); None if unnamed."""
    import re
    m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)([BM])(?![a-z])", str(model or ""))
    if not m:
        return None
    return float(m.group(1)) / (1000.0 if m.group(2) == "M" else 1.0)


def order_model(spec):
    """The model an order runs: the spec's own `model`, else the payload's MODEL_NAME_FIXED declaration (map payloads
    name their tablet model there). None when neither says. A drafted rigby order (`draft_host`) runs only its
    draft_model on a tablet; the target stays on rigby."""
    if spec.get("draft_host") and spec.get("draft_model"):
        return spec["draft_model"]
    if spec.get("model"):
        return spec["model"]
    import re
    try:
        with open(spec.get("payload") or "", encoding="utf-8") as f:
            m = re.search(r"^MODEL_NAME_FIXED\s*=\s*['\"]([^'\"]+)['\"]", f.read(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def _apply_class_rules(name, spec, hosts):
    """Drop the nodes whose CLASS the order is not for. Returns (kept, [(host, why)])."""
    size = model_size_b(order_model(spec))
    kept, dropped = [], []
    for h in hosts:
        cls = tablet_class(h)
        ceiling = CLASS_MODEL_CEILING_B.get(cls)
        if ceiling is not None and size is not None and size > ceiling:
            dropped.append((h, "class %s takes models up to %gB, not %gB" % (cls, ceiling, size)))
        else:
            kept.append(h)
    return kept, dropped


def _order_roster(name, spec, health):
    """Every node this order could ever run on, healthy or not: the fleet after require_arch,
    exclude_hosts and the class rules, the same filters build_order applies to the healthy list."""
    nodes = sorted(health)
    arch = spec.get("require_arch")
    if arch:
        nodes = [h for h in nodes if _SCHED_TABLETS.get(h, {}).get("arch", "aarch64") == arch]
    return _apply_class_rules(name, spec, _apply_exclude_hosts(name, spec, nodes)[0])[0]


def _short_of_nodes(path, requested, healthy, roster, why):
    """Too few healthy nodes: WAIT (LaneBusy) when the roster could supply them, HOLD when it never can."""
    if requested <= len(roster):
        raise LaneBusy("%s; %d of the %d nodes it can use are healthy -- waiting for the fleet"
                       % (why, healthy, len(roster)))
    hold_order(path, "%s; only %d nodes can ever run it" % (why, len(roster)))


def _select_distinct_hosts(name, hosts, count):
    """Choose a deterministic, order-specific cohort without exposing host control to callers."""
    ranked = sorted(hosts, key=lambda host: hashlib.sha256(
        (str(name) + "\0" + str(host)).encode("utf-8")).hexdigest())
    return ranked[:count]


CAMERA_CRED_DOC = os.environ.get(
    "TOLKIEN_NVR_CRED_DOC", "/docs/Reference/Credentials/camera-credentials.md")


def _nvr_credentials():
    """Returns NVR credentials as (user, password, source).

    Sources:
      1. Environment variables TOLKIEN_NVR_USER / TOLKIEN_NVR_PASS (takes precedence).
      2. Credentials doc at CAMERA_CRED_DOC path.

    Returns (None, None, reason) if unavailable.
    """
    user = os.environ.get("TOLKIEN_NVR_USER")
    pw = os.environ.get("TOLKIEN_NVR_PASS")
    if user and pw:
        return user, pw, "environment (TOLKIEN_NVR_USER/PASS)"

    try:
        import io
        import re
        with io.open(CAMERA_CRED_DOC, encoding="utf-8") as fh:
            text = fh.read()
        u = re.search(r"\*\*Username\*\*\s*\|\s*`([^`]+)`", text)
        p = re.search(r"\*\*Password\*\*\s*\|\s*`([^`]+)`", text)
        if not u or not p:
            return None, None, ("%s is readable but the Username/Password fields did not parse - "
                                "the doc format may have changed" % CAMERA_CRED_DOC)
        return u.group(1), p.group(1), "credentials doc (%s)" % CAMERA_CRED_DOC
    except Exception as e:                                           # noqa: BLE001
        return None, None, "%s unreadable: %s: %s" % (CAMERA_CRED_DOC, type(e).__name__, e)


def camera_subscriber_start():
    """Start the NVR event subscriber on its own thread. Returns (started, why).

        Fails loudly and explicitly; does not start a silent-failing subscriber.
        Three distinct outcomes are returned separately to prevent masking errors:
          * module missing          -> started=False, why=import error details
          * credentials unavailable -> started=False, why=missing config details
                                     (does NOT guess passwords or start with 401s)
          * success                 -> started=True, why=None (liveness via thread beat)
    """
    try:
        import tolkien_cameras as tc
    except Exception as e:                                           # noqa: BLE001
        return False, "tolkien_cameras import failed: %s: %s" % (type(e).__name__, e)

    user, pw, src = _nvr_credentials()
    if not user or not pw:
        return False, ("no NVR credentials (%s) - the subscriber will not start without them" % src)
    log("camera subscriber credentials from: %s" % src)

    import threading
    import urllib.request

    def run():
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, "http://%s/" % tc.NVR_HOST, user, pw)
        opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))
        # Maintenance runs inside the subscriber loop via `on_tick`.
        # Placing it outside would result in execution only once at startup,
        # since `tc.subscribe(...)` is a blocking call that never returns.
        state = {"last_maint": 0.0}

        def maintenance(_counts):
            if time.time() - state["last_maint"] < 3600:
                return
            state["last_maint"] = time.time()
            rep = tc.prune()
            if any((rep["event_days"], rep["reject_days"], rep["snapshots_by_age"],
                    rep["snapshots_by_ceiling"])):
                log("camera prune: %s" % json.dumps(rep))
            if rep.get("ceiling_hit"):
                log("⚠ camera snapshot CEILING hit - pruning oldest-first regardless of age")
            bk = tc.backup()
            if bk.get("copied"):
                log("camera backup: %s (keeping %d)" % (", ".join(bk["copied"]), bk["kept"]))
            # A degradation that still "works" reports nothing on its own -- surface it.
            st = tc.store_status()
            if st.get("degraded"):
                log("⚠ camera store DEGRADED: %s" % st.get("degraded_detail"))

        while True:
            try:
                tc.subscribe(opener=opener, on_tick=maintenance)
            except Exception as e:                                   # noqa: BLE001
                log("camera subscriber error (%s: %s) - retrying in 30s"
                    % (type(e).__name__, str(e)[:160]))
                time.sleep(30)

    threading.Thread(target=run, name="camera-subscriber", daemon=True).start()
    return True, ""


def load_spec(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_CARDS_DOWN = set()     # (gpu, build) drops already DM'd: one DM per drop, cleared when that card is seen again


def _wait_for_cards(name, spec):
    """Queues the order if required cards are unavailable; the order is never consumed (LaneBusy), and the operator receives a single DM per drop event."""
    import tolkien_llm
    checks = tolkien_llm.card_checks(spec)
    back = {k for k, why in checks.items() if why is None}
    _CARDS_DOWN.difference_update({d for d in _CARDS_DOWN if d[0] in back})
    bad = {k: why for k, why in checks.items() if why}
    if not bad:
        if checks:
            _card_marker(None)                  # every card this order needs is back: nothing is waiting on one
        return
    _card_marker(name, sorted({g for g, _b in bad}), list(bad.values()))
    keeper = _read_json(os.path.join(STATE_DIR, CARD_KEEPER))
    now = time.time()
    phase = keeper_phase(keeper, now)
    new = [k for k in bad if (k, phase) not in _CARDS_DOWN]
    if new and phase != "rebooting":            # mid-reboot the keeper's own verdict is one run away: say nothing yet
        _CARDS_DOWN.update((k, phase) for k in new)
        what = "; ".join(bad[k] for k in new)
        notify_desk(("RIGBY CARD STILL MISSING - %s waits queued: %s. The card keeper gave up: %s. A human is needed "
                     "(the 1060's riser?)." % (name, what, keeper.get("gave_up_why") or "no reason recorded"))
                    if phase == "gave_up" else
                    "RIGBY CARD NOT VISIBLE - %s waits queued (not run, not voided): %s. %s"
                    % (name, what, keeper_line(keeper, now)))
    raise LaneBusy("card not visible: %s" % "; ".join(bad.values()))


# Card keeper: Tolkien cannot reboot its own host, so it writes CARD_WANTED to signal a root timer on rigby
# (host/rigby_card_keeper.py) to reboot rigby if an order is waiting on an invisible card. The marker and
# CARD_KEEPER both carry heartbeats (based on LaneBusy retry cadence) to distinguish active waiting from
# stale markers and to verify the keeper's presence.
CARD_WANTED = "card_wanted.json"
CARD_KEEPER = "card_keeper.json"


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                                # noqa: BLE001
        return {}


def _keeper_alive(keeper, now):
    """(alive, age_s) from the keeper's own heartbeat: alive within 3x the period it declares; unreadable = not alive."""
    try:
        age = now - float(keeper["checked"])
        return age <= 3 * float(keeper["period_s"]), age
    except (KeyError, TypeError, ValueError):
        return False, None


def keeper_phase(keeper, now):
    """The keeper's state for this drop, from its own record: "gave_up" | "rebooting" (it asked for a reboot and has
    not yet judged the result - only while its heartbeat is alive, so a keeper that died mid-reboot never silences the
    DM) | "drop". One DM per drop per phase; none while "rebooting"."""
    if keeper.get("gave_up"):
        return "gave_up"
    return "rebooting" if keeper.get("rebooted_at") and _keeper_alive(keeper, now)[0] else "drop"


def keeper_line(keeper, now):
    """What the DM may say about the keeper - only what its own heartbeat shows: alive within 3x its declared period,
    else SILENT (a dead keeper cannot announce itself, so this is the only place it gets noticed)."""
    alive, age = _keeper_alive(keeper, now)
    if alive:
        return ("The card keeper (checked %.0f s ago) reboots rigby once both lanes are idle, once per drop; waiting "
                "orders then run by themselves." % age)
    return ("The card keeper is SILENT (%s) - nothing will reboot rigby: reboot it by hand "
            "(RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04 § Xid 79)." % (
                "never checked in" if age is None else "last check %.0f min ago" % (age / 60)))


def _card_marker(order, cards=None, why=None):
    """Write (an order is waiting on these cards) or remove (order None) the keeper's marker. Never raises."""
    p = os.path.join(STATE_DIR, CARD_WANTED)
    try:
        if order is None:
            if os.path.exists(p):
                os.remove(p)
            return
        prev = _read_json(p)
        now = time.time()
        body = {"order": order, "cards": cards, "why": why, "period_s": LANE_BUSY_RETRY_S,
                "since": prev.get("since") if prev.get("cards") == cards else now, "updated": now}
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f)
        os.replace(tmp, p)
    except Exception as e:                                           # noqa: BLE001 - a marker never fails the lane
        log("card marker not written (%s)" % type(e).__name__)


def hold_order(path, why):
    """An order this fleet cannot build is HELD and NAMED, never silently worked around.

    Renamed rather than deleted so the customer can see it and fix the spec."""
    dst = path + ".HELD"
    try:
        os.replace(path, dst)
    except Exception:
        pass
    log("HELD %s: %s" % (os.path.basename(path), why))
    # A desk-collected order that gets HELD is reported FAILED, truthfully: its chained
    # successors must hold rather than run on a predecessor that never built. Best-effort read
    # -- a local inbox order has no fleet_order_id and the report is a no-op.
    try:
        spec = load_spec(dst if os.path.exists(dst) else path)
        report_fleet_result(spec, False, why)
    except Exception:
        pass


_CLAIM_LK = threading.Lock()
_FLEET_CLAIMS = {}               # order name -> its claim record, every running fleet order in start order
_FLEET_KEYS = {}                 # order name -> its stem (the node pool's key), set when the order starts


def write_current(spec=None, phase="", done=0, total=0, work=None, name=None):
    """The lane claim. STATED, never inferred -- readers must not glob for 'the newest thing'.
    Each lane has its own claim file; the calling thread's lane picks it. `work` is the in-flight item's own
    progress (rigby: tokens streamed, which count as progress) or beat (tablets: the attempts' CPU ticks, which do
    not - they only tell grinding from dead).
    The fleet lane runs several orders at once (the node pool), so ITS claim lists every running order under
    `orders` (oldest first; `tablet_foundry.claim_orders` reads it) and repeats the oldest at the top level; an order
    ends its own entry with write_current(None, name=<its name>). The file goes when the last one ends."""
    if lane_name() == "rigby":
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            if spec is None:
                if os.path.exists(CURRENT_RIGBY):
                    os.remove(CURRENT_RIGBY)
                return
            with open(CURRENT_RIGBY, "w", encoding="utf-8") as f:
                json.dump({"name": spec.get("name"), "phase": phase, "done": done, "total": total, "work": work,
                           "started": now(), "pid": os.getpid()}, f)
        except Exception:
            pass
        return
    with _CLAIM_LK:
        try:
            if spec is None:
                if name is None and len(_FLEET_CLAIMS) > 1:
                    log("write_current(None) with no order name while %d fleet orders run - left as is"
                        % len(_FLEET_CLAIMS))
                    return
                if name is None:
                    _FLEET_CLAIMS.clear()
                else:
                    _FLEET_CLAIMS.pop(name, None)
            else:
                n = spec.get("name")
                prev = _FLEET_CLAIMS.get(n) or {}
                _FLEET_CLAIMS[n] = {"name": n, "phase": phase, "done": done, "total": total, "work": work,
                                    "started": prev.get("started") or now(), "pid": os.getpid(),
                                    "hosts": sorted(h for h, k in list(NODE_HOLDER.items())
                                                    if k == _FLEET_KEYS.get(n))}
            os.makedirs(STATE_DIR, exist_ok=True)
            if not _FLEET_CLAIMS:
                if os.path.exists(CURRENT):
                    os.remove(CURRENT)
                return
            rows = list(_FLEET_CLAIMS.values())
            tmp = CURRENT + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(rows[0], orders=rows), f)
            os.replace(tmp, CURRENT)
        except Exception:
            pass


def _archive_run_spec(path, name, stopped=None, results=""):
    """Archive the spec of an order that ran. Never raises."""
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        if stopped:
            archive_stopped_spec(path, order_stem(path), dict(stopped, state="running"))
        else:
            os.replace(path, os.path.join(ARCHIVE_DIR, "%s.tjob.json.%s" % (name, _stamp())))
    except Exception as e:                                           # noqa: BLE001
        log("archive failed (%s) - spec left in queue" % type(e).__name__)
    note_retired(order_stem(path), results)


def retire_order(path, spec, rows, errors, wall, control_ok, control_why,
                 ctl_ok=None, ctl_why="", complete=True, complete_why="",
                 expected=None, unclaimed=None, cohort=None, stopped=None):
    """Ship deliverables, archive spec, and clear the lane.

    Specs are timestamped to prevent stale archives from shadowing newer ones.
    Cleanup is centralized here (not in the happy path) to run on ANY exit route,
    ensuring exceptions do not skip cleanup.

    `stopped` ({"slip", "why", "at"}) marks an order a customer slip stopped mid-run: the results
    carry `stopped: true` and the spec retires as `.stopped-<stamp>`."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    name = spec.get("name", "order")
    out = {"name": name, "finished": now(), "wall_s": round(wall, 1),
           "n_measured": len(rows), "n_expected": expected, "errors": errors,
           # Two independent verdicts, recorded separately so a reader can tell WHICH one failed.
           # Collapsing them into one boolean loses the distinction between "the detector is broken"
           # and "the detector never saw these items" -- opposite problems with opposite fixes.
           "control_passed": (control_ok if ctl_ok is None else ctl_ok), "control": ctl_why or control_why,
           "complete": complete, "coverage": complete_why,
           "never_measured": unclaimed or [],
           "usable": bool(control_ok) and bool(complete),
           "VOID": (not control_ok) or (not complete),
           "rows": rows}
    if cohort is not None:
        out["cohort"] = cohort
    for k in ("placement", "placement_row"):     # a PLACED order: the key Spock sent and the row Tolkien picked
        if spec.get(k) is not None:
            out[k] = spec[k]
    out["stopped"] = bool(stopped)
    if stopped:
        out["stopped_by"], out["stop_cause"] = stopped.get("slip"), stopped.get("why")
    res = os.path.join(RESULTS_DIR, "%s.%s.results.json" % (name, _stamp()))
    with open(res, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    _archive_run_spec(path, name, stopped, res)
    clear_stage_dirs(STATE_DIR, name)            # this order's staging only: other orders may be mid-run
    write_current(None, name=spec.get("name"))
    # Deliverables leave the building. Without this the results sit on Sundae's local disk where
    # the customer cannot reach them, and every order would still need a session to collect it.
    for d in ship_to_outbox(res):
        log("shipped -> %s" % d)
    # A chained SUCCESSOR must never glob for "the newest results file" (the documented trap):
    # the order may STATE an exact delivery path and the successor's spec references that same
    # literal. The slot is overwritten on a re-run by design -- it is a handoff, not an archive;
    # the timestamped local + outbox copies remain the record. Fail-soft like the outbox.
    if spec.get("deliver_to"):
        try:
            dst = str(spec["deliver_to"])
            if os.path.dirname(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(res, dst)
            log("delivered -> %s" % dst)
        except Exception as e:
            log("deliver_to failed (%s) - outbox copy stands" % type(e).__name__)
    # Report the verdict only at FINISH, not at collection: done releases successors, but void/paused must hold them. A pause is a requeue, not a failure; the desk hears the final verdict of the resumed run.
    if stopped and stopped.get("pause"):
        log("paused %s (%s): the desk hears its verdict when the requeued order finishes" % (name, stopped.get("why")))
    else:
        report_fleet_result(spec, bool(out["usable"]),
                            "" if out["usable"] else
                            (out["control"] if not out["control_passed"] else out["coverage"]))
    log("retired %s -> %s" % (name, os.path.basename(res)))
    return res


def window_hosts(spec, hosts):
    """The tablets a window's cohorts take from `hosts` - execute_window's own selection, arm by arm, so the node pool
    holds exactly those and nothing more (the same derivation; it re-selects the same cohorts from this set)."""
    remaining = list(hosts)
    chosen = []
    for child in spec["arms"]:
        picked = _select_distinct_hosts(child["name"], remaining, int(child["distinct_workers"]))
        remaining = [h for h in remaining if h not in picked]
        chosen += list(picked)
    return chosen


def execute_window(spec, hosts, on_rows=None, stop_event=None, on_work=None):
    """Run map arms concurrently on deterministic, non-overlapping host cohorts. `on_work(index, ticks)` relays each
    arm's in-flight CPU beat (run_order's `on_work`)."""
    line_kw = {"stop_event": stop_event} if stop_event is not None else {}
    remaining = list(hosts)
    planned = []
    for child in spec["arms"]:
        arm = dict(child)
        arm["kind"] = "map"
        requested = int(arm["distinct_workers"])
        if requested > len(remaining):
            raise ValueError("arm %s needs %d workers; %d remain"
                             % (arm["name"], requested, len(remaining)))
        selected = _select_distinct_hosts(arm["name"], remaining, requested)
        remaining = [host for host in remaining if host not in selected]
        cohort = {"requested_distinct_workers": requested,
                  "window_eligible_hosts": sorted(hosts),
                  "selected_hosts": list(selected)}
        planned.append((arm, selected, cohort))

    results = [None] * len(planned)

    def run_one(index, arm, selected, cohort):
        try:
            callback = None
            if on_rows:
                callback = lambda done, total: on_rows(index, done, total)
            arm_kw = dict(line_kw)
            if on_work:
                arm_kw["on_work"] = lambda ticks: on_work(index, ticks)
            rows, errors, wall, expected, unclaimed = run_order(
                arm, selected, on_rows=callback, **arm_kw)
            control_ok, control_why = validate_control(arm, rows)
            complete, complete_why = validate_complete(
                rows, expected, unclaimed, key=row_key(arm),
                stopped=bool(stop_event is not None and stop_event.is_set()))
            distinct_ok, distinct_why, actual_hosts = validate_distinct_workers(
                arm, rows, selected)
            cohort["actual_hosts"] = actual_hosts
            cohort["passed"] = distinct_ok
            cohort["detail"] = distinct_why
            complete = complete and distinct_ok
            complete_why = "%s; %s" % (complete_why, distinct_why)
            usable = bool(control_ok and complete)
            results[index] = {"name": arm["name"], "wall_s": round(wall, 1),
                              "n_measured": len(rows), "n_expected": expected,
                              "errors": errors, "control_passed": control_ok,
                              "control": control_why, "complete": complete,
                              "coverage": complete_why, "never_measured": unclaimed,
                              "usable": usable, "VOID": not usable, "cohort": cohort,
                              "rows": rows}
        except Exception as exc:                                     # noqa: BLE001
            results[index] = {"name": arm.get("name"), "usable": False, "VOID": True,
                              "complete": False, "control_passed": False, "rows": [],
                              "errors": {"window": "%s: %s" % (type(exc).__name__, exc)},
                              "cohort": cohort}

    threads = [threading.Thread(target=run_one, args=(index, arm, selected, cohort),
                                daemon=True, name="window-%s" % arm["name"])
               for index, (arm, selected, cohort) in enumerate(planned)]
    started = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, time.time() - started


TPS_WINDOW = 10
LIVE_TOKS_WINDOW_S = 60      # the GPU foundries' rule (p20_foundry.live_toks): tokens per second over the last minute
LIVE_TOKS_FRESH_S = 90       # no tick this recent: between generations, so the recent-answer median shows instead
LIVE_TOKS_MAX = 5000.0
LIVE_PUBLISH_S = 15          # a streaming tick re-publishes the live rate at most this often


def live_toks(beats, now):
    """Tokens per second from (time, cumulative tokens) ticks of ONE answer over the last LIVE_TOKS_WINDOW_S, or
    None when stale or fewer than two recent ticks. Same rule as p20_foundry.live_toks: an implausibly fast delta is
    skipped, a count that goes down is a new baseline."""
    if not beats or now - beats[-1][0] > LIVE_TOKS_FRESH_S:
        return None
    recent = [b for b in beats if now - b[0] <= LIVE_TOKS_WINDOW_S]
    toks = secs = 0.0
    good = recent[0] if recent else None
    for t1, n1 in recent[1:]:
        dt, dn = t1 - good[0], n1 - good[1]
        if dn < 0:
            good = (t1, n1)
            continue
        if dt <= 0 or dn / dt > LIVE_TOKS_MAX:
            continue
        toks += dn
        secs += dt
        good = (t1, n1)
    return toks / secs if secs > 0 and toks > 0 else None


def live_rate(streams, now):
    """The order's live tok/s: the sum over the answers streaming right now (the warm arm runs one per node)."""
    rates = [r for r in (live_toks(b, now) for b in streams.values()) if r]
    return sum(rates) if rates else None


def tps_phase(phase, tps, live=None):
    """`phase · N tok/s`, the same text every factory publishes and the desk's tile parses: the LIVE rate while
    answers stream, else the median of the recent answers' generation rate, else the phase unchanged."""
    w = write_rate(tps, live)
    return "%s · %.1f tok/s" % (phase, w) if w else phase


def write_rate(tps, live=None):
    """The rate the phase carries and the tile's write row shows: the live rate, else the recent answers' median."""
    import tolkien_llm as tv
    return live or tv.median(tps)


def window_eta(elapsed_s, done, total, parallel=1, done_times=()):
    """'<s> s/item · ~<m>m left' in the spelling the deck parses; '' until the first item lands.

    A window runs its items `parallel` at a time, all started together, so elapsed/done x remaining counts parallel
    items as serial (the 4x vlm window read "~9m left" 2 min before it finished). With the elapsed time at each
    completion, the k-th completion ends round ceil(k/parallel): per-item time = that elapsed / its round, and the
    window ends after ceil(total/parallel) rounds."""
    if done <= 0 or elapsed_s <= 0:
        return ""
    parallel = max(1, int(parallel or 1))
    times = list(done_times)[:done]
    if parallel > 1 and len(times) == done:
        per = sum(t / (((k - 1) // parallel) + 1) for k, t in enumerate(times, 1)) / done
        left_s = per * (-(-total // parallel)) - elapsed_s
    else:
        per = elapsed_s / done
        left_s = per * max(0, total - done)
    left = int(round(max(0.0, left_s) / 60.0))
    return "%.1f s/item · %s" % (per, ("~%dm left" % left) if left > 0 else "finishing")


def median_unit_s(done_at):
    """Median seconds per item from each fresh completion's elapsed time (seconds since the run started). The MEDIAN:
    one marathon item (a 3-hour sgf-parsing among 5-46 min ones) must not set the pace of every item after it."""
    gaps = sorted(b - a for a, b in zip([0.0] + list(done_at), done_at))
    if not gaps:
        return None
    mid = len(gaps) // 2
    return gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2


def llm_eta(elapsed_s, done, total, resumed=0, done_at=(), controls=0):
    """Compute ETA from fresh answers in this run only. Resumed orders must be excluded from the rate calculation because their timestamps reflect the previous session, not current speed. Use the median interval (`done_at`) rather than the mean to avoid distortion from outliers. `controls` are excluded from the rate denominator because they represent non-measured items and do not contribute to the target workload."""
    fresh, left_n = done - resumed - controls, total - resumed - controls
    if fresh <= 0:
        return ""
    per = median_unit_s(done_at) if len(done_at) == fresh else None
    if per is None:
        return window_eta(elapsed_s, fresh, left_n)
    left = int(round(max(0.0, per * max(0, left_n - fresh)) / 60.0))
    return "%.1f s/item · %s" % (per, ("~%dm left" % left) if left > 0 else "finishing")


def build_bench_order(path, spec, watch=None):
    """llama-bench runs on rigby and/or tablets, and tablet link tests (#36): numbers out, nothing left running."""
    import tolkien_llm as tv
    name = spec["name"]
    t0 = time.time()
    stop_event = watch.event if watch is not None else None
    runs = spec["runs"]
    total = len(runs)
    write_current(spec, phase="bench", done=0, total=total)
    publish(name=name, state="running", phase="bench starting", step=0, total=total)

    done = [0]
    seen = {}            # rid -> {"t": elapsed text, "busy": stage-activity text}: the tile shows both, live

    def progress(i, n, rid):
        done[0] = i
        beat("bench %s %d/%d" % (name, i, n))
        write_current(spec, phase="bench", done=i, total=n)
        publish(name=name, state="running", phase="bench %s" % rid, step=i, total=n)
        log("bench %d/%d: %s" % (i, n, rid))

    def live(rid, text):
        """A run in flight: llama-bench prints nothing until it ends, so the run's own clock and (stage runs) how many
        stages are computing right now are what the deck shows - and the beat is what the watchdog reads."""
        cur = seen.setdefault(rid, {})
        cur["busy" if text.startswith("busy now") else "t"] = text
        phase = "bench %s (run %d/%d): %s" % (rid, done[0] + 1, total,
                                              " | ".join(v for v in (cur.get("t"), cur.get("busy")) if v))
        beat("bench %s: %s" % (name, phase))
        write_current(spec, phase="bench", done=done[0], total=total)
        publish(name=name, state="running", phase=phase, step=done[0], total=total)
        if text.startswith("busy now"):
            log("bench %s: %s" % (rid, text))

    rows, errors = tv.run_bench(spec, log, progress, stop_event,
                                tick=lambda what: beat("bench %s: %s" % (name, what)), live=live)
    done_ids = {r["run"] for r in rows}
    unrun = [r["id"] for r in runs if r["id"] not in done_ids]
    control_ok = not errors and bool(rows)
    stopped = watch.stopped_record() if (watch is not None and stop_event.is_set()) else None
    res = retire_order(path, spec, rows, errors, time.time() - t0, control_ok,
                       "every run returned the expected number of results" if control_ok
                       else "runs failed: %s" % ", ".join(sorted(errors)),
                       complete=not unrun, complete_why="%d/%d runs" % (total - len(unrun), total),
                       expected=total, unclaimed=unrun, stopped=stopped)
    if not unrun and not stopped:
        tv.retire_checkpoint(name)
    if stopped:
        watch.finish(res, len(rows))
    elif control_ok and not unrun and dm_quiet(spec):
        log("%s: shipped; notify policy failures-only, no DM" % name)
    else:
        notify_desk("BENCH %s: %d/%d runs, %d results%s in %.1f min -> %s"
                    % (name, total - len(unrun), total, len(rows),
                       (", FAILED: %s" % ", ".join(sorted(errors))) if errors else "",
                       (time.time() - t0) / 60.0, os.path.basename(res)))
    return res


# How often a chat window restates rigby's lane claim: the runner's own beat (POLL, ~30 s), faster than the watchdog
# reads it (tablet_watch POLL), so the claim it reads is never more than one beat old.
WINDOW_CLAIM_S = POLL


def build_chat_window_order(path, spec, watch=None):
    """Holds rigby's lane while the window is open; the lane is released only by the window's idle rule or an explicit CLOSE."""
    import tolkien_llm as tv
    name = spec["name"]
    t0 = time.time()
    stop_event = watch.event if watch is not None else None
    write_current(spec, phase="window", done=0, total=0)
    publish(name=name, state="running", phase="chat window starting", step=0, total=0)

    def tick(what):
        beat("window %s: %s" % (name, what))
        publish(name=name, state="running", phase="chat window: %s" % what, step=0, total=0)

    # The lane claim is what the watchdog judges (tablet_watch.patrol_rigby). It must stay current with the live state:
    # update it when turns are served, when a turn is in flight (one turn can outlast the watchdog's budget), or when
    # waiting between turns. Written from the keeper thread (as_lane).
    turns = [0]
    stop_claim = threading.Event()

    def claim_now():
        in_flight, chunks = tablet_foundry.window_turn_in_flight(spec.get("items_dir"))
        if in_flight is None:
            return                                  # the folder could not be read: state nothing new
        write_current(spec, phase="window" if in_flight else tablet_foundry.WINDOW_WAITING, done=turns[0], total=0,
                      work=chunks if in_flight else None)

    def on_turn(n):
        turns[0] = n
        claim_now()

    def keep_claim():
        while not stop_claim.wait(WINDOW_CLAIM_S):
            try:
                with tablet_foundry.as_lane("rigby"):
                    claim_now()
            except Exception:                       # the keeper must never cost the window
                pass

    keeper = threading.Thread(target=keep_claim, name="window-claim-%s" % name, daemon=True)
    keeper.start()
    try:
        summary, err = tv.run_window(spec, log, stop_event, tick=tick, on_turn=on_turn)
    finally:
        stop_claim.set()
        keeper.join(timeout=WINDOW_CLAIM_S)         # no keeper write may land after the order retires its claim
    rows = [{"served": summary.get("served", 0), "load_s": summary.get("load_s"), "why_ended": summary.get("why_ended")}]
    ok = not err
    stopped = watch.stopped_record() if (watch is not None and stop_event is not None and stop_event.is_set()) else None
    res = retire_order(path, spec, rows, {"window": err} if err else {}, time.time() - t0, ok,
                       ("served %d turn(s), ended: %s" % (summary.get("served", 0), summary.get("why_ended")))
                       if ok else ("chat window failed: %s" % err),
                       complete=ok, complete_why="window ended (%s)" % summary.get("why_ended"), stopped=stopped)
    if stopped:
        watch.finish(res, summary.get("served", 0))
    elif not ok:
        notify_desk("CHAT WINDOW %s on rigby FAILED: %s" % (name, err))
    log("%s: chat window served %d turn(s) in %.1f min - %s" % (name, summary.get("served", 0),
                                                               (time.time() - t0) / 60.0, summary.get("why_ended") or err))
    return res


def build_page_order(path, spec, watch=None):
    """One page on the GTX 1060 (tolkien_page, gpu_render.py on Kirk), or the 12-launch proof that lets rigby offer it."""
    import tolkien_page
    name = spec["name"]
    t0 = time.time()
    write_current(spec, phase="page", done=0, total=1)
    publish(name=name, state="running", phase="page render", step=0, total=1)
    result, err = tolkien_page.run_page(spec, log)
    ok = not err
    rows = [{"webgl": result.get("webgl"), "opened": result.get("opened")}]
    res = retire_order(path, spec, rows, {"page": err} if err else {}, time.time() - t0, ok, "page rendered" if ok else ("page render failed: %s" % err),
                       complete=ok, complete_why="page rendered" if ok else err)
    log("%s: page order %s in %.1f s" % (name, "done" if ok else ("FAILED: %s" % err), time.time() - t0))
    return res


def build_embed_order(path, spec, watch=None):
    """Price embedding cost using tolkien_llm.run_embed.

    Output is written to spec['deliver_to'] in the GPU payload out.json shape (read exclusively by menu_policy.embed_deliveries)."""
    import tolkien_llm as tv
    name = spec["name"]
    t0 = time.time()
    stop_event = watch.event if watch is not None else None
    total = len(spec["texts"]) * int(spec.get("rounds", 3))
    write_current(spec, phase="embed", done=0, total=total)
    publish(name=name, state="running", phase="embed starting", step=0, total=total)

    def progress(i, n, what):
        beat("embed %s %d/%d" % (name, i, n))
        write_current(spec, phase="embed", done=i, total=n)
        publish(name=name, state="running", phase="embed %s" % what, step=i, total=n)

    out, err = tv.run_embed(spec, log, progress, stop_event, tick=lambda what: beat("embed %s: %s" % (name, what)))
    rows = [r for r in (out.get("rows") or []) if "wall_s" in r]
    delivered = None
    if not err and rows:
        # beside Tolkien's own results record (retire_order ships THAT to deliver_to): the first run's out.json was
        # overwritten by the record and the vectors were lost - one name, one artifact
        dest = os.path.join(os.path.dirname(spec["deliver_to"]), "out.json")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        os.replace(tmp, dest)
        delivered = dest
        log("embed %s: %s -> %s" % (name, (out.get("summary") or {}).get("verdict"), dest))
    ok = not err and bool(rows)
    stopped = watch.stopped_record() if (watch is not None and stop_event is not None and stop_event.is_set()) else None
    res = retire_order(path, spec, rows, {"embed": err} if err else {}, time.time() - t0, ok,
                       ("%d calls measured, delivered to %s" % (len(rows), delivered)) if ok else ("embed failed: %s" % err),
                       complete=ok, complete_why="%d/%d marked calls" % (min(len(rows), total), total), stopped=stopped)
    if stopped:
        watch.finish(res, len(rows))
    elif ok and dm_quiet(spec):
        log("%s: shipped; notify policy failures-only, no DM" % name)
    else:
        notify_desk("EMBED %s: %d calls%s in %.1f min -> %s" % (name, len(rows), (", FAILED: %s" % err) if err else "",
                                                               (time.time() - t0) / 60.0, os.path.basename(res)))
    return res


def build_llm_order(path, spec, hosts, watch=None, adb_sh=None):
    """One model served by Tolkien: on rigby alone and/or as Voltron (rigby + the wire tablets as one machine)."""
    import tolkien_llm as tv
    if adb_sh is None:
        from tablet_control import _adb_root_sh as adb_sh      # the kills need ROOT (a setsid'd server is root-only)
    spec = tv.normalize(spec)
    if spec.get("dump_probe") is not None:
        return build_dump_probe_order(path, spec, watch)
    name = spec["name"]
    t0 = time.time()
    tv.meter_reset()                   # the tile's call count and context are this order's
    stop_event = watch.event if watch is not None else None
    mod, work = tv.load_scorer(spec["payload"])
    unknown = [t for t in spec["tasks"] if t not in mod.TASKS]
    if unknown:
        hold_order(path, "tasks not in %s: %s" % (os.path.basename(spec["payload"]), ", ".join(unknown[:5])))
        return None
    arms = spec["arms"]
    per_arm = 1 + len(tv.items_for(spec))
    total = per_arm * len(arms)
    done = [0]
    resumed = [0]      # answers a resume kept: they cost no time in THIS run, so they never enter the rate
    controls = [0]     # fresh control answers: counted done, never an item of the rate
    done_at = []       # elapsed seconds at each fresh answer (the median rate)
    by_arm = {}
    recent_tps = []
    streams = {}       # (arm, task, rep) -> [(time, tokens)] for each answer in flight: the live rate
    last_eta = [""]
    last_live_pub = [0.0]
    ctl_name = tv.control_of(mod)[0]
    write_current(spec, phase="llm-%s" % arms[0], done=0, total=total)
    publish(name=name, state="running", phase="llm-%s starting" % arms[0], step=0, total=total)

    shown = [total]    # the deck's x/y: an arm's own count - warm answers a control PER NODE, so its n is larger
    arm_n = {}         # than 1 + items and grows as nodes join (10-15: the deck read "44 / 36"); `total` stays the
                       # count of distinct answers the completeness check below needs

    def progress(arm, i, n, row):
        by_arm[arm] = i                                  # i counts answers a resume kept from the checkpoint
        done[0] = sum(by_arm.values())
        arm_n[arm] = max(n, arm_n.get(arm, 0))
        shown[0] = sum(arm_n.get(a, per_arm) for a in arms)
        if row.get("resumed"):
            resumed[0] += 1
        elif row.get("task") == ctl_name:
            controls[0] += 1
        else:
            done_at.append(time.time() - t0)
        beat("llm %s %s %d/%d" % (arm, name, i, n))      # the order's own progress is the liveness signal
        write_current(spec, phase="llm-%s" % arm, done=done[0], total=shown[0])
        if row.get("gen_tps"):
            recent_tps[:] = recent_tps[-(TPS_WINDOW - 1):] + [float(row["gen_tps"])]
        streams.pop((arm, row.get("task"), row.get("replicate")), None)
        last_eta[0] = llm_eta(time.time() - t0, done[0], shown[0], resumed[0], done_at, controls[0])
        live = live_rate(streams, time.time())
        publish(name=name, state="running", step=done[0], total=shown[0], eta=last_eta[0],
                phase=tps_phase("llm-%s" % arm, recent_tps, live=live),
                gen=tv.meter_view(write=write_rate(recent_tps, live)))
        verdict =("STOPPED" if row.get("stopped") else "PRICED OUT" if row.get("priced_out")
                   else "ok" if row.get("ok") else "FAIL")
        log("llm %s %d/%d: %s r%d %s (%ss, %s tok/s)"
            % (arm, i, n, row["task"], row["replicate"], verdict,
               row.get("wall_s"), round(row.get("gen_tps") or 0, 2)))

    def streaming(arm, task, rep, n):
        beat("llm %s %s: %s r%d %d tokens" % (arm, name, task, rep, n))
        write_current(spec, phase="llm-%s" % arm, done=done[0], total=shown[0], work=n)
        now = time.time()
        ticks = streams.setdefault((arm, task, rep), [])
        ticks[:] = [b for b in ticks if now - b[0] <= LIVE_TOKS_FRESH_S] + [(now, n)]
        if now - last_live_pub[0] >= LIVE_PUBLISH_S:
            last_live_pub[0] = now
            live = live_rate(streams, now)
            publish(name=name, state="running", step=done[0], total=shown[0], eta=last_eta[0],
                    phase=tps_phase("llm-%s" % arm, recent_tps, live=live),
                    gen=tv.meter_view(write=write_rate(recent_tps, live)))

    rows, metas = [], []
    for arm in arms:
        r, m = tv.run_arm(arm, spec, mod, work, hosts, log, progress, stop_event, adb_sh=adb_sh,
                          tick=lambda what, a=arm: beat("llm %s %s: %s" % (a, name, what)), streaming=streaming)
        rows += r
        metas.append(m)
        if stop_event is not None and stop_event.is_set():
            break
    ctl = {}
    for r in rows:
        if r["task"] == ctl_name:   # warm answers one control PER NODE; a node that failed its own took no work
            ctl[r["arm"]] = ctl.get(r["arm"], False) or bool(r.get("ok"))
    control_ok = len(ctl) == len(arms) and all(ctl.values())
    answered = {(r.get("arm"), r.get("task"), r.get("replicate")) for r in rows if tv.answered(r)}
    measured = len(answered)
    unmeasured = [[a, t, rep] for a in arms for t, rep in [(ctl_name, 1)] + tv.items_for(spec)
                  if (a, t, rep) not in answered]
    stopped = watch.stopped_record() if (watch is not None and stop_event.is_set()) else None
    errors = {m["arm"]: m["error"] for m in metas if m.get("error")}
    res = retire_order(path, spec, rows, errors, time.time() - t0, control_ok, "%s per arm: %s" % (ctl_name, ctl),
                       complete=measured == total, complete_why="%d/%d answers" % (measured, total),
                       expected=total, unclaimed=unmeasured, cohort={"arms": metas}, stopped=stopped)
    if measured == total and not stopped:
        tv.retire_checkpoint(name)
    summary = ", ".join("%s %d/%d" % (a, sum(1 for r in rows if r["arm"] == a and r["task"] != ctl_name
                                                and r.get("ok")), per_arm - 1) for a in arms)
    if stopped:
        watch.finish(res, len(rows))
    elif control_ok and measured == total and dm_quiet(spec):
        log("%s: shipped; notify policy failures-only, no DM" % name)
    else:
        # "VOLTRON" only names work that pooled the tablets; rigby alone is RIGBY
        label = " vs ".join(a.upper() for a in arms)
        notify_desk("%s %s %s: %s in %.1f min -> %s" % (label, name, spec["model"], summary,
                                                        (time.time() - t0) / 60.0, os.path.basename(res)))
    return res


def build_dump_probe_order(path, spec, watch=None):
    """#34 B11's measurement (tolkien_llm.run_dump_probe): one row of metrics, shipped like any order. Its CONTROL is the
    probe's own instrument check - the erased-slot control must re-read the whole question, or a cache served it and
    the comparison is void; COMPLETE = all five steps ran."""
    import tolkien_llm as tv
    name = spec["name"]
    p = spec["dump_probe"]["p"]
    t0 = time.time()
    stop_event = watch.event if watch is not None else None
    write_current(spec, phase="dump-probe", done=0, total=5)
    publish(name=name, state="running", phase="dump-probe", step=0, total=5)
    errors = {}
    try:
        m = tv.run_dump_probe(spec, log, stop_event=stop_event, tick=lambda what: beat("dump %s: %s" % (name, what)))
        if m.get("error"):                       # failed mid-probe: the steps it finished ship beside the error
            errors["rigby"] = m["error"]
    except Exception as e:                                                   # noqa: BLE001 - shipped as the error
        m = {"steps_done": 0}
        errors["rigby"] = "%s: %s" % (type(e).__name__, str(e)[:300])
    row = dict(m, arm="rigby", task="dump_probe", replicate=1)
    full = isinstance(m.get("control_read_tokens"), int) and m["control_read_tokens"] >= p - 1
    stopped = watch.stopped_record() if (watch is not None and stop_event is not None and stop_event.is_set()) else None
    res = retire_order(path, spec, [row], errors, time.time() - t0, full,
                       "control re-read %s of %d question tokens (a cache-served control voids the comparison)"
                       % (m.get("control_read_tokens"), p),
                       complete=m.get("steps_done") == 5, complete_why="%s/5 steps" % m.get("steps_done"),
                       expected=1, stopped=stopped)
    if stopped:
        watch.finish(res, 1)
    elif m.get("mode") == "chat":
        notify_desk("RIGBY chat quick-resume probe %s (temp %s): render matches the saved state %s, lossless %s (first "
                    "diff %s), continue re-read %s tok; + the pending token: lossless %s (re-read %s); lab resume "
                    "(engine #3): armed %s, lossless %s (first diff %s, read %s); server ran %s past the client; "
                    "without the dump %s -> %s"
                    % (name, m.get("temperature"), m.get("render_match"), m.get("lossless"), m.get("first_diff"),
                       m.get("continue_reread_tokens"), m.get("oracle_lossless"), m.get("oracle_reread_tokens"),
                       m.get("resume_armed"), m.get("resume_lossless"), m.get("resume_first_diff"),
                       m.get("resume_reread_tokens"), m.get("ran_past_client"), m.get("control_lossless"),
                       os.path.basename(res)))
    elif m.get("mode") == "continue":
        notify_desk("RIGBY quick-resume probe %s (temp %s): lossless %s (first diff at %s of %s), fresh runs agree %s, "
                    "continue re-read %s tok; without the dump %s -> %s"
                    % (name, m.get("temperature"), m.get("lossless"), m.get("first_diff"), m.get("ref_written"),
                       m.get("prefix_identical"), m.get("continue_reread_tokens"), m.get("control_lossless"),
                       os.path.basename(res)))
    else:
        notify_desk("RIGBY dump probe %s: with the dump %s ms (stop re-read %s tok), without %s ms; resumed turn "
                    "re-read %s tok -> %s" % (name, m.get("with_dump_ms"), m.get("stop_reread_tokens"),
                                              m.get("without_dump_ms"), m.get("resume_reread_tokens"),
                                              os.path.basename(res)))
    return res


def dm_quiet(spec):
    """notify: "failures" | "quiet" - the GPU factories' policy (p20_foundry): an order opts out of its routine
    shipped DM; a VOID, HELD or stopped order still DMs. A1-team pieces use it so one lead run is one DM, not one
    per delegated brief."""
    return str((spec or {}).get("notify") or "").strip().lower() in ("failures", "quiet")


def build_window_order(path, spec, hosts, caps, watch=None):
    """Execute and ship one physical window containing independently graded logical arms."""
    name = spec["name"]
    stop_kw = {"stop_event": watch.event} if watch is not None else {}
    total = sum(len(arm.get("items") or []) + 1 for arm in spec["arms"])
    progress = [0] * len(spec["arms"])
    progress_lock = threading.Lock()
    done_times = []            # elapsed seconds at each item completion, for the parallel-aware ETA
    # each arm runs on exactly distinct_workers selected tablets (execute_window), not on every eligible host
    parallel = max(1, min(total, sum(int(a.get("distinct_workers") or 0) for a in spec["arms"]) or len(hosts or [])))
    t_start = time.time()
    publish(name=name, state="running", phase="window", step=0, total=total, caps=caps)
    write_current(spec, "window", 0, total)

    def on_rows(index, done, _arm_total):
        with progress_lock:
            progress[index] = done
            completed = sum(progress)
            now_s = time.time() - t_start
            while len(done_times) < completed:
                done_times.append(now_s)
            times = list(done_times)
        beat("running window %s %d/%d" % (name, completed, total))
        write_current(spec, "window", completed, total)
        # The deck's Progress / Rate / ETA rows come from this push (they stayed blank while only the local
        # state file knew the count).
        publish(name=name, state="running", phase="window", step=completed, total=total, caps=caps,
                eta=window_eta(now_s, completed, total, parallel=parallel, done_times=times))

    arm_ticks = {}

    def on_work(index, ticks):
        with progress_lock:
            arm_ticks[index] = ticks
            completed, work = sum(progress), sum(arm_ticks.values())
        write_current(spec, "window", completed, total, work=work)

    try:
        arms, wall = execute_window(spec, hosts, on_rows=on_rows, on_work=on_work, **stop_kw)
    except ValueError as exc:
        hold_order(path, str(exc))
        write_current(None, name=name)
        return
    stopped = watch.stopped_record() if watch is not None else None
    usable = all(arm.get("usable") for arm in arms)
    out = {"kind": "map.window", "name": name, "finished": now(),
           "wall_s": round(wall, 1), "n_arms": len(arms),
           "n_usable_arms": sum(1 for arm in arms if arm.get("usable")),
           "control_passed": all(arm.get("control_passed") for arm in arms),
           "complete": all(arm.get("complete") for arm in arms),
           "coverage": "%d/%d arms usable" %
                       (sum(1 for arm in arms if arm.get("usable")), len(arms)),
           "usable": usable, "VOID": not usable, "stopped": bool(stopped), "arms": arms}
    if stopped:
        out["stopped_by"], out["stop_cause"] = stopped["slip"], stopped["why"]
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    res = os.path.join(RESULTS_DIR, "%s.%s.results.json" % (name, _stamp()))
    with open(res, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    _archive_run_spec(path, name, stopped, res)
    clear_stage_dirs(STATE_DIR, name)            # this window's staging only: other orders may be mid-run
    write_current(None, name=name)
    for destination in ship_to_outbox(res):
        log("shipped -> %s" % destination)
    report_fleet_result(spec, usable, "" if usable else out["coverage"])
    log("retired window %s -> %s%s" % (name, os.path.basename(res),
                                       " (STOPPED by %s)" % stopped["slip"] if stopped else ""))
    if stopped:
        watch.finish(res, sum(len(arm.get("rows") or []) for arm in arms))
    elif usable and dm_quiet(spec):
        log("%s: shipped; notify policy failures-only, no DM" % name)
    else:
        notify_desk("WINDOW %s: %d/%d arms usable in %.1f min -> %s"
                    % ("SHIPPED" if usable else "VOID", out["n_usable_arms"], len(arms),
                       wall / 60.0, os.path.basename(res)))
    return res


def build_order(path, watch=None):
    """Run one order to its end. Every fleet-lane route ends with its tablets given back - shipped, held, refused or
    crashed (run_one gives back too; a second give-back is a no-op)."""
    try:
        return _build_order(path, watch)
    finally:
        if lane_name() != "rigby":
            give_back(order_stem(path))


def _build_order(path, watch=None):
    spec = load_spec(path)
    name = spec.get("name", os.path.basename(path))
    kind = (spec.get("kind") or "sweep").strip().lower()
    if watch is not None:
        watch.name = name
    if lane_name() != "rigby":
        _FLEET_KEYS[name] = order_stem(path)        # its claim records the tablets the pool gives it
    stop_kw = {"stop_event": watch.event} if watch is not None else {}

    defects = spec_defects(spec)
    if defects:
        hold_order(path, "; ".join(defects))
        return

    if kind == "triage.extract":
        # The lab's own splitting step, inside the order: docs -> per-section map items at the
        # head, then the unchanged map machinery. An unreadable doc or a zero-section split HOLDs
        # the order with the reason named -- a broken input, never a silent short run.
        try:
            spec = expand_triage_extract(spec)
        except ValueError as e:
            hold_order(path, str(e))
            return

    if kind in ("llm", "voltron"):
        import tolkien_llm
        if not tolkien_llm.uses_tablets(spec):
            _wait_for_cards(name, spec)                                     # a card off the bus: WAIT, never a VOID
            with rigby_claims(name, spec):                                  # rigby alone: no tablet is involved
                return build_llm_order(path, spec, [], watch=watch)

    if kind == "bench":
        import tolkien_llm
        if not tolkien_llm.bench_tablets(spec):
            _wait_for_cards(name, spec)
            with rigby_claims(name, spec):
                return build_bench_order(path, spec, watch=watch)

    if kind == "embed":                                                      # rigby alone (embed_defects says so)
        _wait_for_cards(name, spec)
        with rigby_claims(name, spec):
            return build_embed_order(path, spec, watch=watch)

    if kind == "page.render":                                                # a job's page on the 1060: rigby alone
        with rigby_claims(name, spec):
            return build_page_order(path, spec, watch=watch)

    if kind == "chat.window":                                                # HAI's warm chat window: rigby alone
        _wait_for_cards(name, spec)
        with rigby_claims(name, spec):
            return build_chat_window_order(path, spec, watch=watch)

    # A tablet another running order holds is not probed: that order is its evidence of life, and an ssh probe would
    # only add load to a node mid-work (the watchdog's own rule). It counts as healthy and busy.
    with NODES_LK:
        held_by = {h: k for h, k in NODE_HOLDER.items() if k != order_stem(path)}
    if held_by:
        health = fleet_health([h for h in _SCHED_TABLETS if h not in held_by])
        for h, k in held_by.items():
            health[h] = {"healthy": True, "alive": True, "whitelisted": True, "wakelock": True,
                         "why": "busy with %s" % k}
    else:
        health = fleet_health()
    # Try the ONE suppression a non-root ssh can fix, then re-check. Repairing beats refusing, but
    # only where the fix is real -- the doze whitelist needs adb and is reported, not faked.
    for h, v in list(health.items()):
        if v["alive"] and not v["healthy"] and v.get("wakelock") is False:
            log("%s: %s -> attempting wake-lock re-acquire" % (h, v["why"]))
            health[h] = try_repair(h)
    usable = [h for h, v in health.items() if v["healthy"]]
    for h, v in sorted(health.items()):
        log("  %-9s %s  (%s)" % (h, "OK" if v["healthy"] else "UNUSABLE", v["why"]))
    # The order's roster ignores health: what the fleet could give it once every node is back. Short of
    # healthy nodes but inside the roster is a property of the moment (WAIT); beyond the roster it is a
    # property of the order (HOLD for the customer).
    roster = _order_roster(name, spec, health)
    if not usable:
        _short_of_nodes(path, 1, 0, roster, "no healthy node in the fleet")
        return

    # Internal placement is delegated to the head node; work-stealing ensures load balancing.
    hosts = sorted(usable)
    log("%s: placing on %s" % (name, ", ".join(hosts)))

    # require_arch (opt-in): filter healthy pool to tablets whose scheduler.TABLETS arch matches.
    # Fleet convention: scheduler.TABLETS states arch ONLY for exceptions (smeagol=armv7l);
    # every other tablet is aarch64 by default. This filter honours that convention -- an
    # absent "arch" key resolves to the fleet default so the derivation matches the authoring.
    # If a future non-aarch64 tablet joins, it MUST declare its arch in TABLETS (same rule
    # smeagol proves) and this filter will exclude it correctly.
    require_arch = spec.get("require_arch")
    if require_arch:
        before = list(hosts)
        hosts = [h for h in hosts
                 if _SCHED_TABLETS.get(h, {}).get("arch", "aarch64") == require_arch]
        excluded = [h for h in before if h not in hosts]
        if excluded:
            log("%s: require_arch=%s excluded %s" % (name, require_arch, ", ".join(excluded)))
        if not hosts:
            _short_of_nodes(path, 1, 0, roster, "no healthy node matches require_arch=%s" % require_arch)
            return

    # exclude_hosts (opt-in): the customer names nodes this order must NOT run on. Tolkien has no
    # memory across orders, so a multi-stage team (solver order, then critic order) can only keep
    # its roles on different tablets if the second order says which one the first used.
    hosts, excluded = _apply_exclude_hosts(name, spec, hosts)
    if excluded:
        log("%s: exclude_hosts removed %s" % (name, ", ".join(excluded)))
    if not hosts:
        _short_of_nodes(path, 1, 0, roster, "exclude_hosts leaves no eligible healthy node")
        return
    hosts, wrong_class = _apply_class_rules(name, spec, hosts)
    for h, why in wrong_class:
        log("%s: not dealt to %s - %s" % (name, h, why))
    if model_size_b(order_model(spec)) is None:
        log("%s: model size not stated (%r) - class ceilings not applied" % (name, order_model(spec)))
    if not hosts:
        _short_of_nodes(path, 1, 0, roster, "no healthy node of a class this order is for")
        return

    # Fewer HEALTHY nodes than the order needs is a property of the fleet the customer must see
    # (HOLD). Enough healthy nodes but some still finishing the last order's speculative item is a
    # property of the moment (LaneBusy: wait, never consume the order).
    free, finishing = _available_hosts(hosts)
    # THE NODE POOL: tablets other running orders hold (or an order ahead is waiting for) are not this order's
    key = order_stem(path)
    others = unavailable_to(key)
    busy_here = [h for h in free if h in others]
    free = [h for h in free if h not in others]

    if kind == "bench":
        # a bench order holds rigby AND every tablet: nothing else shares the CPUs or the USB link it measures
        import tolkien_llm as _tv
        tabs = _tv.bench_tablets(spec)
        down = [h for h in tabs if h not in usable]
        if down:
            _short_of_nodes(path, len(tabs), len(tabs) - len(down), roster,
                            "bench tablet(s) %s not healthy" % ", ".join(down))
            return
        if busy_here:
            raise NodesBusy("bench holds the fleet; %s held by other orders" % ", ".join(sorted(busy_here)),
                            wants=hosts)
        if finishing:
            raise LaneBusy("bench holds the fleet; %s still finishing an earlier item" % ", ".join(sorted(finishing)))
        take_nodes(key, free)
        with rigby_claims(name, spec):
            return build_bench_order(path, spec, watch=watch)

    if kind in ("llm", "voltron"):
        # Voltron: the rpc-server build is arm64; the whole healthy aarch64 fleet backs one model
        pool = [h for h in free if _SCHED_TABLETS.get(h, {}).get("arch", "aarch64") == "aarch64"]
        import tolkien_llm as _tv
        n = _tv.warm_nodes(spec)
        aarch = [h for h in hosts if _SCHED_TABLETS.get(h, {}).get("arch", "aarch64") == "aarch64"]
        busy_arm = [h for h in busy_here if h in aarch]
        if n:                       # a placed piece: n warm nodes, the fastest measured first - not the whole fleet
            picked = _tv.pick_warm_nodes(pool, n)
            if len(picked) < n:
                _busy_or_short(path, n, picked, busy_arm, finishing, aarch, roster,
                               "warm_nodes %d: %d free" % (n, len(picked)))
                return
            log("%s: warm on %s (fastest of %d free)" % (name, ", ".join(picked), len(pool)))
            take_nodes(key, picked)
            return build_llm_order(path, spec, picked, watch=watch)
        if _tv.warm_only(spec):                                      # warm runs on the tablets alone (every free
            if not pool:                                             # one): rigby's own lane carries on beside it
                _busy_or_short(path, 1, pool, busy_arm, finishing, aarch, roster, "warm: no free tablet")
                return
            take_nodes(key, pool)
            return build_llm_order(path, spec, pool, watch=watch)
        if spec.get("draft_host"):                                   # one named drafter; the rest stay idle so
            dh = spec["draft_host"]                                  # nothing else shares the tablet link
            if dh not in pool:
                _busy_or_short(path, 1, [], [dh] if dh in busy_here else [],
                               [dh] if dh in finishing else [], [dh], roster,
                               "draft_host %s is not healthy and free" % dh)
                return
            take_nodes(key, [dh])
            with rigby_claims(name, spec):
                return build_llm_order(path, spec, [dh], watch=watch)
        if busy_arm:                                                 # Voltron backs one model with the whole fleet
            raise NodesBusy("voltron takes the whole fleet; %s held by other orders" % ", ".join(sorted(busy_arm)),
                            wants=aarch)
        if finishing:
            raise LaneBusy("voltron takes the whole fleet; %s still finishing an earlier item"
                           % ", ".join(sorted(finishing)))
        take_nodes(key, pool)
        with rigby_claims(name, spec):                                      # Voltron uses rigby too
            return build_llm_order(path, spec, pool, watch=watch)

    if kind == "map.window":
        requested = sum(int(arm["distinct_workers"]) for arm in spec["arms"])
        if requested > len(hosts):
            _short_of_nodes(path, requested, len(hosts), roster,
                            "window needs %d eligible workers; only %d are healthy" % (requested, len(hosts)))
            return
        if requested > len(free):
            _busy_or_short(path, requested, free, busy_here, finishing, hosts, roster,
                           "window needs %d distinct workers" % requested)
            return
        chosen = window_hosts(spec, free)            # exactly the cohorts execute_window will pick, nothing more
        caps, _ = detect_capabilities(health)
        take_nodes(key, chosen)
        return build_window_order(path, spec, chosen, caps, watch=watch)

    cohort = None
    if spec.get("distinct_workers") is not None:
        requested = int(spec["distinct_workers"])
        eligible = list(hosts)
        if requested > len(eligible):
            _short_of_nodes(path, requested, len(eligible), roster,
                            "distinct_workers=%d but only %d eligible healthy nodes" % (requested, len(eligible)))
            return
        if requested > len(free):
            _busy_or_short(path, requested, free, busy_here, finishing, eligible, roster,
                           "distinct_workers=%d" % requested)
            return
        eligible = [h for h in eligible if h in free]
        hosts = _select_distinct_hosts(name, eligible, requested)
        cohort = {"requested_distinct_workers": requested,
                  "eligible_hosts": sorted(eligible),
                  "selected_hosts": list(hosts)}
        log("%s: distinct-worker cohort %d/%d selected: %s"
            % (name, requested, len(eligible), ", ".join(hosts)))
    else:
        # an elastic order takes the free tablets it can use - one per batch it deals, never more, the fastest first
        # (SKU-affinity orders take every free one: each lane's cells need that lane's tablets)
        if not free:
            _busy_or_short(path, 1, free, busy_here, finishing, hosts, roster, "no free tablet")
            return
        need = None if spec.get("sku_affinity") else useful_nodes(spec)
        import tolkien_llm as _tv
        hosts = list(free) if need is None else _tv.pick_warm_nodes(free, max(1, need))
        if len(hosts) < len(free):
            log("%s: %d batch%s - takes %d of %d free tablets, the rest stay free for other orders"
                % (name, need, "" if need == 1 else "es", len(hosts), len(free)))
    take_nodes(key, hosts)

    # SKU-affinity (opt-in): TOLKIEN owns which tablet is which lane; the tablets never decide. When
    # the order asks for it, hand run_order the {host: sku} map for the healthy hosts and each lane's
    # cells run only on that lane's tablets. A lane whose only host is unhealthy surfaces as unclaimed
    # (VOID coverage), which is the honest outcome -- not a silently short matrix.
    host_sku = None
    if spec.get("sku_affinity"):
        host_sku = {h: TABLET_SKU[h] for h in hosts if h in TABLET_SKU}
        lanes = sorted(set(host_sku.values()))
        log("%s: SKU-affinity ON -- lanes %s across %s"
            % (name, ", ".join(lanes), ", ".join("%s=%s" % (h, s) for h, s in sorted(host_sku.items()))))

    caps, _ = detect_capabilities(health)
    publish(name=name, state="running", phase="fulfilment", caps=caps)
    write_current(spec, "fulfilment", 0, 0)

    count = {"done": 0, "total": 0}

    def progress(done, total):
        count.update(done=done, total=total)
        beat("running %s %d/%d" % (name, done, total))
        write_current(spec, "fulfilment", done, total)
        publish(name=name, state="running", phase="fulfilment", step=done, total=total, caps=caps)

    def work_beat(ticks):
        # the in-flight attempts' CPU ticks: tells the watchdog whether a stalled order is grinding or dead
        write_current(spec, "fulfilment", count["done"], count["total"], work=ticks)

    rows, errors, wall, expected, unclaimed = run_order(spec, hosts, on_rows=progress,
                                                        host_sku=host_sku, on_work=work_beat, **stop_kw)
    stopped = watch.stopped_record() if watch is not None else None
    ctl_ok, ctl_why = validate_control(spec, rows)
    if not ctl_ok:
        # A failing control makes the run VOID. Without this, a broken detector and a genuinely
        # clean input produce the SAME output and nobody can tell them apart.
        log("*** CONTROL FAILED (%s) -- RESULTS ARE VOID, not a clean corpus ***" % ctl_why)
    else:
        log("control PASS (%s)" % ctl_why)
    # Separate verdict, separate failure mode: a working detector that only saw half the corpus is
    # the more dangerous result, because every other signal reads green.
    full, full_why = validate_complete(rows, expected, unclaimed, key=row_key(spec),
                                       stopped=bool(stopped))
    if cohort is not None:
        distinct_ok, distinct_why, actual_hosts = validate_distinct_workers(spec, rows, hosts)
        cohort["actual_hosts"] = actual_hosts
        cohort["passed"] = distinct_ok
        cohort["detail"] = distinct_why
        log(distinct_why)
        full_why = "%s; %s" % (full_why, distinct_why)
        full = full and distinct_ok
    if not full:
        log("*** SWEEP INCOMPLETE (%s) -- absence here does NOT mean absence ***" % full_why)
    else:
        log("coverage PASS (%s)" % full_why)
    ok = ctl_ok and full
    why = ctl_why if not ctl_ok else full_why
    log("%s: %d measured in %.0fs, %d node errors" % (name, len(rows), wall, len(errors)))
    res = retire_order(path, spec, rows, errors, wall, ok, why,
                       ctl_ok=ctl_ok, ctl_why=ctl_why, complete=full, complete_why=full_why,
                       expected=expected, unclaimed=unclaimed, cohort=cohort, stopped=stopped)
    if stopped:
        watch.finish(res, len(rows))
        return

    hits = sum(1 for r in rows if r.get("found"))
    errored = sum(1 for r in rows if r.get("error"))
    # Denominator is expected item count, not row count.
    # len(rows) would force 100% coverage by construction,
    # masking incomplete sweeps.
    if ok and dm_quiet(spec):
        log("%s: shipped; notify policy failures-only, no DM" % name)
        return
    if ok:
        head = "ORDER SHIPPED: %s" % name
    elif not ctl_ok:
        head = "ORDER VOID - CONTROL FAILED: %s\nThe detector did not read a known-positive. " \
               "Absence in these results proves nothing." % name
    else:
        head = "ORDER VOID - SWEEP INCOMPLETE: %s\nThe detector worked but did not see every " \
               "item. Absence in these results proves nothing." % name
    notify_desk(
        "%s\n%d/%d measured on %d nodes in %.0f min%s%s\n%s\n-> %s"
        % (head, len(rows), expected, len(hosts), wall / 60.0,
           ("  ·  %d with readable text" % hits) if hits else "",
           ("  ·  %d errored" % errored) if errored else "",
           ("node errors: " + "; ".join("%s=%s" % (h, e[:60]) for h, e in errors.items()))
           if errors else "no node errors",
           os.path.basename(res or "?")))


def next_runnable(pending, waiting, now_t=None):
    """First pending order (queue order: priority, then name) that is not waiting out a LaneBusy.
    Expired or vanished waits are dropped, so a waiting order is retried as soon as its time is up
    and before anything queued behind it."""
    now_t = time.time() if now_t is None else now_t
    for p in list(waiting):
        if waiting[p] <= now_t or p not in pending:
            del waiting[p]
    return next((p for p in pending if p not in waiting), None)


def clear_inherited_claims():
    """Called once the singleton lock is ours: no order of the previous instance can still run (its process and its
    llama-server went with it), so any lane claim on disk is a dead instance's. Each is logged, then removed - a claim
    nothing will ever clear blocks deploys and makes the watchdog alert on an order that is gone."""
    for path in (CURRENT, CURRENT_RIGBY):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    held = f.read().strip()[:300]
                os.remove(path)
                log("cleared a lane claim left by the previous instance: %s = %s" % (os.path.basename(path), held))
        except Exception as e:                                           # noqa: BLE001
            log("could not clear %s (%s: %s)" % (os.path.basename(path), type(e).__name__, e))


def run_queue(poll=POLL):
    for d in (QUEUE_DIR, ARCHIVE_DIR, STATE_DIR, RESULTS_DIR):
        os.makedirs(d, exist_ok=True)
    # a clean stop (a deploy's recreate) hands the lock over and exits at once - the next taker does not wait 90 s
    tablet_foundry.install_stop(on_stop=tablet_foundry.release_singleton)
    # Wait for lock instead of exiting: avoiding crash loops caused by docker `restart: unless-stopped`
    # and redeploy races with the predecessor's heartbeat. Waiting ensures exactly one runner is active.
    waited = 0.0
    while not acquire_singleton():
        time.sleep(10.0)
        waited += 10.0
        if waited % 60 == 0:
            log("still waiting for the runner lock (%.0fs)" % waited)
    log("tablet order taker up; watching %s" % QUEUE_DIR)
    clear_inherited_claims()

    # Live detection shim runs in-process to inherit supervision.
    # Bind failure is non-fatal; batch order processing continues.
    started, why = detect_head_serve()
    if started:
        log("tolkien detect shim serving on :%d" % DETECT_HEAD_PORT)
    else:
        log("⚠ tolkien detect shim did NOT start (%s) -- batch orders unaffected, but live "
            "detection cannot be served by the head until this is fixed" % why)

    # Tablet control plane (reboot / usb-restore / status), localhost-bound + token-gated.
    # Fail-closed: without TOLKIEN_CTL_TOKEN it does not serve. Non-fatal to the order desk.
    ctl = None
    try:
        import tablet_control
        ctl = tablet_control
        started, why = tablet_control.control_serve()
        log("%s%s" % ("tolkien " if started else "⚠ tolkien tablet control NOT started: ", why))
        log("tolkien %s" % tablet_control.keeper_start(log, notify_desk))
    except Exception as e:                                               # noqa: BLE001
        log("⚠ tolkien tablet control crashed on startup (%s: %s) -- orders continue"
            % (type(e).__name__, e))

    # Camera event subscriber. Runs in-process (not a separate container) to inherit
    # supervision, restart policy, and singleton guarantees.
    #
    # Failure here is non-fatal: the order desk remains available; only motion
    # recording/pruning stops until fixed.
    try:
        started, why = camera_subscriber_start()
        if started:
            log("tolkien camera subscriber watching the NVR event stream")
        else:
            # Reported, not fatal, and NOT dressed up as healthy.
            log("⚠ tolkien camera subscriber NOT started (%s) -- orders unaffected; camera events "
                "are NOT being recorded until this is fixed" % why)
    except Exception as e:
        log("⚠ tolkien camera subscriber crashed on startup (%s: %s) -- orders continue"
            % (type(e).__name__, e))

    tablet_foundry.CAPACITY[0] = fleet_capacity          # every run-state push now says what Tolkien has free
    rigby_lane_start(poll)
    log("tolkien rigby lane up: orders that need rigby alone run beside the tablet work")

    last_pub = 0.0
    fleet_busy = False        # an order held tablets at the last pass (its end republishes the idle tile at once)
    waiting = {}              # path -> epoch before which a LaneBusy order is not retried
    while True:
        beat("waiting")
        try:
            if time.time() - last_pub > 120:
                caps, ok = detect_capabilities()
                publish(name="", state="idle", caps=caps)
                last_pub = time.time()
                log("idle; %d/%d nodes healthy; caps=%s" % (len(ok), len(__import__("scheduler").TABLETS), caps))
        except Exception as e:
            log("capability publish error (%s) - orders continue" % type(e).__name__)

        try:
            drain_inbox()
        except Exception as e:
            log("inbox drain error (%s) - local queue still served" % type(e).__name__)

        try:
            resend_desk_reports()        # a receipt the desk missed (it was down) goes before new work is taken
        except Exception as e:
            log("desk receipt resend error (%s) - orders continue" % type(e).__name__)
        try:
            collect_fleet_orders()
        except Exception as e:
            log("fleet desk collect error (%s) - local queue still served" % type(e).__name__)

        # After intake, so a slip can name an order submitted in the same pass. Every running order is named,
        # or a slip routed while the rigby lane runs would treat its running order as queued.
        try:
            process_slips(running=StopWatch.running())
        except Exception as e:
            log("slip routing error (%s) - local queue still served" % type(e).__name__)
        FIRST_SLIP_PASS.set()
        lane_housekeeping()

        try:
            rebind_to_free_worker()                # a piece waiting on a busy rigby takes a free tablet instead
        except Exception as e:                                       # noqa: BLE001
            log("rebind error (%s) - pieces stay where they are" % type(e).__name__)
        try:
            pending = open_to_start([p for p in queued() if lane_of(p) == "fleet"])
        except Exception as e:
            log("queue read error (%s)" % type(e).__name__)
            pending = []

        # THE NODE POOL: start every order that fits on the free tablets; the running ones carry on beside them
        NODES_FREED.clear()
        dispatch_fleet(pending, waiting, ctl)
        if FLEET_RUNNING:
            fleet_busy = True
            last_pub = time.time()             # the running orders publish the tile; no idle push over them
        else:
            if ctl is not None:
                ctl.RUNNER_IDLE.set()          # the USB keeper may act only now: no order holds a tablet
            if fleet_busy:
                last_pub = 0.0                 # republish idle promptly after the last order ends
            fleet_busy = False
        _idle_wait(min(poll, LANE_BUSY_RETRY_S) if (pending or FLEET_RUNNING) else poll,
                   lambda: _fleet_quick_check(waiting))


def _fleet_quick_check(waiting):
    """Check for immediate work between full passes.
    Returns True if a new pass should start now due to desk orders, inbox arrivals, freed nodes,
    rebinds, or timed-out queued orders."""
    push_capacity_if_changed()
    return bool(NODES_FREED.is_set() or collect_fleet_orders() > 0 or drain_inbox() > 0
                or rebind_to_free_worker() > 0 or _fleet_work_waiting(waiting))


def run_one(path, waiting):
    """Run one order in the calling thread's lane. Never raises: a busy resource leaves the order queued
    (WAIT), anything else HOLDs it named, and the stop watch is closed on every route."""
    log("%spicking up %s" % ("rigby lane: " if lane_name() == "rigby" else "", os.path.basename(path)))
    key = order_stem(path)
    fleet = lane_name() != "rigby"
    watch = StopWatch(key)
    try:
        watch.start()
        build_order(path, watch)
        BLOCKED.pop(path, None)
    except NodesBusy as e:
        BLOCKED[path] = {"wants": set(e.wants), "gen": LAUNCH_GEN.get(key, NODES_GEN[0]), "at": time.time()}
        log("WAIT %s: %s -- left queued untouched; it starts the moment an order gives tablets back"
            % (os.path.basename(path), e))
    except LaneBusy as e:
        waiting[path] = time.time() + LANE_BUSY_RETRY_S
        log("WAIT %s: %s -- left queued untouched; next order first, back in %ds"
            % (os.path.basename(path), e, LANE_BUSY_RETRY_S))
    except Exception as e:                                           # noqa: BLE001
        # An order that blows up must not block every order behind it -- but it also must not
        # vanish. HELD and named, so the customer can see why.
        hold_order(path, "unhandled %s: %s" % (type(e).__name__, str(e)[:200]))
        write_current(None, name=watch.name or None)
    finally:
        try:
            watch.close()
        except Exception:                                            # noqa: BLE001
            pass
        if fleet:                         # the CLEANUP stage, by any route: its tablets, its claim, its tile entry
            give_back(key)
            _end_fleet_entry(watch.name)


def _end_fleet_entry(name):
    """Drop an ended fleet order's claim and tile entry if it still has them. Never raises."""
    if not name:
        return
    try:
        if name in _FLEET_CLAIMS:
            write_current(None, name=name)
        if name in fleet_running_names():
            publish(name=name, state="idle")
        _FLEET_KEYS.pop(name, None)
    except Exception:                                                # noqa: BLE001
        pass


LAUNCH_GEN = {}                  # stem -> NODES_GEN when it was launched (its NodesBusy retries once that moves)


def launch_fleet_order(path, waiting, reserved=()):
    """Start one fleet-lane order on its own thread and return once it HOLDS ITS TABLETS (then it runs beside the
    others) or has ended (shipped, refused, held). Choosing and taking tablets stays one order at a time, so two orders
    never health-probe and pick at once; the running stays parallel."""
    key = order_stem(path)
    ev = threading.Event()
    STARTED[key] = ev
    LAUNCH_RESERVED[key] = set(reserved)
    LAUNCH_GEN[key] = NODES_GEN[0]

    def body():
        try:
            run_one(path, waiting)
        finally:
            FLEET_RUNNING.pop(key, None)
            LAUNCH_RESERVED.pop(key, None)
            LAUNCH_GEN.pop(key, None)
            STARTED.pop(key, None)
            ev.set()

    t = threading.Thread(target=body, daemon=True, name="fleet-%s" % key)
    FLEET_RUNNING[key] = t
    t.start()
    ev.wait()
    hosts = TAKEN.pop(key, None)
    if hosts is not None:                    # it got its tablets: McCoy's advice on the same piece, in the background
        mccoy_shadow(_spec_or_none(path), tolkiens_decision(_spec_or_none(path) or {}, hosts))
    return key in FLEET_RUNNING


def dispatch_fleet(pending, waiting, ctl=None, launch=None, now_t=None, retry_s=None):
    """Start every queued fleet order that fits on the free tablets, in queue order (priority, then name). An order refused for tablets other orders hold waits for a give-back (or the full pass, `retry_s`), and the tablets it could use are RESERVED against every order behind it - so a large order is never starved by small ones. Returns how many it started."""
    launch = launch or launch_fleet_order
    now_t = time.time() if now_t is None else now_t
    retry_s = POLL if retry_s is None else retry_s
    for q in list(waiting):
        if waiting[q] <= now_t or q not in pending:
            del waiting[q]
    for q in list(BLOCKED):
        if q not in pending:
            del BLOCKED[q]
    reserved, started = set(), 0
    for p in pending:
        if order_stem(p) in FLEET_RUNNING or p in waiting:
            continue
        b = BLOCKED.get(p)
        if b is not None and b["gen"] == NODES_GEN[0] and now_t - b["at"] < retry_s:
            reserved |= b["wants"]                  # nothing given back since it was refused: ask again later
            continue
        if ctl is not None:
            ctl.RUNNER_IDLE.clear()
        if launch(p, waiting, set(reserved)):
            started += 1
        b = BLOCKED.get(p)
        if b is not None:
            reserved |= b["wants"]
    return started


def _spec_or_none(path):
    try:
        return load_spec(path)
    except Exception:                                                # noqa: BLE001
        return None


FIRST_SLIP_PASS = threading.Event()     # set by the fleet loop once it has routed the slips staged before it started
FIRST_SLIP_WAIT_S = 600


def rigby_lane(poll=POLL):
    """Rigby-only orders run here to avoid conflicting with Voltron, which holds Rigby in the fleet lane.

    The first pick must wait for the fleet loop's initial slip pass; without this wait, a requeued order may be skipped because the rigby lane picks a new order before the slip is routed."""
    LANE.name = "rigby"
    waiting = {}
    import tolkien_llm
    if not FIRST_SLIP_PASS.wait(FIRST_SLIP_WAIT_S):
        log("rigby lane: no slip pass from the fleet loop in %ds - picking without it" % FIRST_SLIP_WAIT_S)
    while True:
        tolkien_llm.publish_models()
        try:
            with PICK_LK:                          # a rebind moving a queued piece to a tablet never races this pick
                pending = open_to_start([p for p in queued() if lane_of(p) == "rigby"])
                res = read_reservation()
                if res is not None:                # reserved: only the owner's pieces start; the rest stay queued
                    pending = [p for p in pending if reservation_admits(_spec_or_none(p), res)]
                path = next_runnable(pending, waiting)
                if path is not None:
                    RIGBY_RUNNING[0] = order_stem(path)
            if path is not None:
                spec = _spec_or_none(path)
                mccoy_shadow(spec, tolkiens_decision(spec or {}, ["rigby"]))
            if path is None:
                if res is not None and not pending:
                    publish(name=res["by"], state="reserved", phase="rigby held for this team run")
                _idle_wait(min(poll, LANE_BUSY_RETRY_S) if pending else poll,
                           lambda: _work_waiting("rigby", waiting))
                continue
            try:
                run_one(path, waiting)
            finally:
                RIGBY_RUNNING[0] = ""
            publish(state="idle")                                    # clears rigby's part of the tile
        except Exception as e:                                       # noqa: BLE001
            log("rigby lane error (%s: %s) - lane continues" % (type(e).__name__, e))
            time.sleep(poll)


def rigby_lane_start(poll=POLL):
    t = threading.Thread(target=rigby_lane, args=(poll,), daemon=True, name="rigby-lane")
    t.start()
    return t


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "health":
        h = fleet_health()
        for k, v in sorted(h.items()):
            print("%-9s healthy=%-5s alive=%-5s whitelist=%-5s wakelock=%-5s  %s"
                  % (k, v["healthy"], v["alive"], v["whitelisted"], v["wakelock"], v["why"]))
        caps, ok = detect_capabilities(h)
        print("\ncapabilities: %s   (%d usable nodes)" % (caps, len(ok)))
    else:
        run_queue()
