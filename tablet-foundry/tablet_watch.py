"""Out-of-process watchdog for the tablet order taker.

Monitors three metrics to detect hangs or stalls that in-process guards
cannot see:
1. Order taker heartbeat (written every ~30s). Stale implies hung/dead.
2. Current order progress (done/total). Frozen progress implies stalled.
3. Fleet reachability (node count responding).

Reporting only: does not take corrective action (e.g., kills/restarts)
to avoid the risk of false positives affecting healthy monitors.
Inconclusive states (read failures) are reported as such, not assumed
down. Does not police its own bookkeeping or the operator's STAND_DOWN
flag to prevent alert fatigue/muting."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tablet_foundry import (CURRENT, CURRENT_RIGBY, HEARTBEAT, HAI_URL, FLEET_HOST, ROOT, STATE_DIR, WINDOW_WAITING,
                            claim_orders, fleet_health, log, notify_desk, now)

POLL = float(os.environ.get("TABLET_WATCH_POLL", "60"))

# The runner beats every poll (~30 s). Five minutes is ten missed beats -- comfortably past any
# plausible scheduling hiccup, and short enough that a hung overnight order is caught the same hour.
HEARTBEAT_STALE_S = float(os.environ.get("TABLET_WATCH_HB_STALE", "300"))

# Calibrated to batch throughput: worst-case batch (~8 items) takes ~6.5 min.
# 20 min is safely past that; shorter stalls are normal during speculative-execution cutoffs.
NO_PROGRESS_S = float(os.environ.get("TABLET_WATCH_STALL", "1200"))

# A fleet-wide 0-reachable that clears on the next poll is a NETWORK ARTIFACT, not 7 tablets dying
# at once -- the twice-daily WiFi re-association drops every node's ssh for one poll (~40 s) and
# returns. DM only after the condition survives this many consecutive polls; a shorter blip is
# reported to the tile as transient but never DM'd. At POLL=60 the default is ~3 min of genuine,
# sustained unreachability -- well past the re-association, and harmless to delay since a real
# outage only HOLDS orders, it never fails them.
UNREACH_CONFIRM = int(os.environ.get("TABLET_WATCH_UNREACH_CONFIRM", "3"))

# WiFi-INDEPENDENT confirmation. sshd-over-WiFi (via the LAN's DNS names) is ONE path and it may have been
# superseded by the USB segment. Before declaring the fleet unreachable we ask rigby -- the USB hub,
# the only host that can see the wire subnet -- whether the tablets answer on the wire. rigby authorises
# our tablet key ONLY to run a forced USB-reach probe (least privilege).
RIGBY_USER = os.environ.get("TABLET_WATCH_RIGBY_USER", "")   # the ssh user there; unset = ssh's own default
RIGBY_HOST = os.environ.get("TABLET_WATCH_RIGBY", "")   # the USB hub's address; unset = no wire check (inconclusive)
RIGBY_KEY = os.environ.get("TABLET_WATCH_RIGBY_KEY", "/run/secrets/tabcluster_key")
USB_PROBE_TIMEOUT = float(os.environ.get("TABLET_WATCH_USB_TIMEOUT", "20"))

# The documented operator control. Touch this file and the watchdog stands by -- it says so, on
# every beat, so a stood-down guard can never be mistaken for a healthy one.
STAND_DOWN = os.path.join(ROOT, "WATCHDOG_STANDDOWN")


def usb_reach_via_rigby():
    """Ask rigby which tablets answer on the USB segment. Returns a set of reachable octets, or
    None when rigby ITSELF could not be reached -- an INCONCLUSIVE result the caller must not read
    as 'truly unreachable'. Empty set means rigby answered and NO tablet is on the wire.

    The forced command on rigby ignores the argv we send; 'probe' is a placeholder."""
    import subprocess
    if not RIGBY_HOST:
        return None                                    # no hub configured -> inconclusive, never "unreachable"
    cmd = ["ssh", "-i", RIGBY_KEY, "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=8", (RIGBY_USER + "@" if RIGBY_USER else "") + RIGBY_HOST, "probe"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=USB_PROBE_TIMEOUT)
    except Exception:
        return None                                    # could not even run ssh -> inconclusive
    for line in (p.stdout or "").splitlines():
        if line.startswith("USB_REACH"):
            return {int(t) for t in line.split()[1:] if t.isdigit()}
    return None                                        # reached, but no parseable answer

_alerted = {}          # key -> ts of last DM, so one condition does not DM every minute


def _read(path):
    """(record, error). Absence and unreadability are DIFFERENT and both are reported as themselves."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, None
    except Exception as e:
        return None, type(e).__name__


def publish_health(alive, detail, disabled=False):
    """Report health to the GPU rescue daemon endpoint.

    Omit `last_tflops` if None. Pydantic v2 rejects explicit nulls for bare floats
    with 422; since this push is fail-soft, such errors would be silent."""
    # DECLARE THE CADENCE. The orchestrator derives "is this daemon still alive" from the reporter's
    # OWN interval rather than a flat constant of its own -- a guard that speaks every 60 s and one
    # that speaks every 20 minutes cannot share a silence budget. At POLL=60 this makes us SILENT at
    # 2 min and DOWN at 3, instead of a blanket 5.
    body = {"host": FLEET_HOST, "alive": bool(alive), "pid": os.getpid(),
            "period": int(POLL), "disabled": bool(disabled), "last": detail[:400]}
    try:
        import urllib.request
        req = urllib.request.Request(HAI_URL.rstrip("/") + "/fleet/rescue-state",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            raw, status = r.read(), r.status
        try:
            ans = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            ans = {}
        if isinstance(ans, dict) and ans.get("ok") is False:
            log("[WATCH] health push REFUSED: %s" % ans.get("error"))
            return None
        return status
    except Exception as e:
        # Say so. A reporter wrapped in silence is how a tile reads DOWN for eleven hours while the
        # daemon is fine -- and how a DEAD one reads healthy.
        log("[WATCH] health push failed (%s)" % type(e).__name__)
        return None


def alert(key, message, every=1800):
    """DM at most once per `every` seconds per condition. A guard that repeats itself every minute
    gets muted, and then it is worse than not having one."""
    last = _alerted.get(key, 0)
    if time.time() - last < every:
        return False
    _alerted[key] = time.time()
    notify_desk("\U0001F6A8 FELLOWSHIP WATCHDOG\n%s" % message)
    return True


def clear(key):
    _alerted.pop(key, None)


def progress_key(done, work, unit):
    """What 'advancing' compares on the rigby lane: the item count plus the tokens the answer in flight has streamed
    (tokens are text reaching the answer). The tablet lane compares finished items only - its CPU beat is not progress."""
    return done if (done is None or work is None) else "%s (%s %s)" % (done, work, unit)


class Progress:
    """Stall detection based on the first observed value.

    The baseline must be an actual observed step, not a default/sentinel (e.g. 0).
    If no value has been observed yet, return 'inconclusive' rather than assuming
    the process is done or stalled.
    """

    def __init__(self):
        self.order = None
        self.done = None
        self.since = None

    def observe(self, order, done):
        """Returns (verdict, detail) where verdict is 'ok' | 'stalled' | 'inconclusive'."""
        if order != self.order:                       # new order -> new baseline, always
            self.order, self.done, self.since = order, done, time.time()
            return "ok", "new order %r, baseline %s" % (order, done)
        if done is None or self.done is None:
            self.since = self.since or time.time()
            return "inconclusive", "no readable progress counter"
        if done != self.done:
            self.done, self.since = done, time.time()
            return "ok", "advancing (%s)" % done
        idle = time.time() - (self.since or time.time())
        if idle > NO_PROGRESS_S:
            return "stalled", ("frozen at %s for %d min" % (done, int(idle // 60)))
        return "ok", "at %s for %d min" % (done, int(idle // 60))


class FleetReach:
    """Consecutive-poll confirmation for the fleet-unreachable DM.

    A single 0-reachable poll is INCONCLUSIVE -- a fleet-wide simultaneous drop is far likelier a
    probe/network artifact than every node failing at once. Only a run of UNREACH_CONFIRM polls all
    reading zero is treated as a real outage worth a DM. Any node answering resets the count."""

    def __init__(self):
        self.zero_streak = 0

    def observe(self, alive_count):
        if alive_count > 0:
            self.zero_streak = 0
        else:
            self.zero_streak += 1
        return self.zero_streak


class WorkMotion:
    """When the in-flight attempts' CPU beat (`work`) last changed, per order. observe() returns the seconds since it
    last moved, or None when no beat has been seen for this order."""

    def __init__(self):
        self.order = self.value = self.since = None

    def observe(self, order, work):
        if order != self.order:
            self.order, self.value, self.since = order, work, (time.time() if work is not None else None)
        elif work is not None and work != self.value:
            self.value, self.since = work, time.time()
        return None if self.since is None else time.time() - self.since


_MOTION = WorkMotion()

# A stage process doing real work uses several cores (a tablet stage ran ~720% computing a chunk); loading weights and
# waiting for the next chunk sit well under this. The telemetry store samples every stage process every 2 s.
BUSY_BEAT_PCT = 50.0
TELEMETRY_DB = os.path.join(STATE_DIR, "telemetry", "telemetry.sqlite")


def busy_beat(path=None):
    """The newest moment any stage process (rpc-server, llama-bench or llama-server, on rigby or a tablet) used real
    CPU, from the telemetry store: a value that MOVES while stages compute and holds still when they stop. It is the
    'work' beat for lanes whose claim carries none (a bench order). None when the store cannot be read - 'could not
    tell', never 'still'."""
    import sqlite3
    p = path or TELEMETRY_DB
    try:
        db = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        row = db.execute("SELECT MAX(ts) FROM samples WHERE proc IS NOT NULL AND proc_cpu_pct >= ?",
                         (BUSY_BEAT_PCT,)).fetchone()
        return None if not row or row[0] is None else row[0]
    except sqlite3.Error:
        return None
    finally:
        db.close()


_EXTRA = {}         # order name -> (Progress, WorkMotion) for every running fleet order after the oldest


def _watch_order(order, done, work, prog, motion, key):
    """One running fleet order: advancing, inconclusive, or stalled (DM under `key`). Returns its detail line."""
    if work is None:
        work = busy_beat()              # a bench order's claim carries no beat; the stages' own CPU is the evidence
    still = motion.observe(order, work)
    verdict, why = prog.observe(order, done)
    if verdict == "stalled":
        kind = ("worker CPU not measured" if still is None else
                "the attempts are still using CPU: an answer grinding far too slowly to finish"
                if still < NO_PROGRESS_S else "the attempts' CPU has stopped too: the work looks dead")
        alert(key, "order %r: no item has finished - %s (heartbeat healthy; %s)" % (order, why, kind))
        return "ALERT: order %r %s (%s)" % (order, why, kind)
    clear(key)
    if verdict == "inconclusive":
        return "watching %r but %s" % (order, why)
    return "watching %r: %s" % (order, why)


def patrol(prog, fleet, motion=None):
    if os.path.exists(STAND_DOWN):
        return True, "stood down by operator (WATCHDOG_STANDDOWN present)"

    hb, hb_err = _read(HEARTBEAT)
    cur, cur_err = _read(CURRENT)
    order = (cur or {}).get("name")
    done, total = (cur or {}).get("done"), (cur or {}).get("total")

    # --- 1. is the order taker beating? -------------------------------------------------------
    if hb_err:
        clear("hb")
        return True, "INCONCLUSIVE: heartbeat unreadable (%s)" % hb_err
    if hb is None:
        clear("hb")
        return True, "INCONCLUSIVE: no heartbeat file yet"
    try:
        hb_age = time.time() - float(hb.get("ts") or 0)
    except (TypeError, ValueError):
        clear("hb")
        return True, "INCONCLUSIVE: heartbeat timestamp unreadable"

    if hb_age > HEARTBEAT_STALE_S:
        detail = ("order taker SILENT for %d min (last phase %r)"
                  % (int(hb_age // 60), hb.get("phase") or "?"))
        if order:
            detail += " while claiming order %r at %s/%s" % (order, done, total)
        alert("hb", detail + "\nThe container may be hung. Nothing was killed -- this is a report.")
        return False, detail
    clear("hb")

    # Progress is measured by finished items, not CPU activity (high CPU can indicate a hang on low-memory hardware).
    # When multiple orders are active, monitor the oldest one (key "stall") and all others (key "stall-<name>").
    orders = claim_orders(cur)
    if orders:
        details, live = [], set()
        for i, r in enumerate(orders):
            o = r.get("name")
            live.add(o)
            if i == 0:
                p, m, key = prog, (motion or _MOTION), "stall"
            else:
                p, m = _EXTRA.setdefault(o, (Progress(), WorkMotion()))
                key = "stall-%s" % o
            details.append(_watch_order(o, r.get("done"), r.get("work"), p, m, key))
        for o in list(_EXTRA):
            if o not in live:
                _EXTRA.pop(o, None)
                clear("stall-%s" % o)
        return True, " | ".join(details)
    clear("stall")

    # --- 3. idle: is the fleet still THERE? ---------------------------------------------------
    # Only when idle. Health-probing five tablets mid-order would add ssh load to nodes that are
    # busy, and the runner already checks health at intake.
    try:
        h = fleet_health()
        up = [k for k, v in h.items() if v.get("healthy")]
        alive_n = [k for k, v in h.items() if v.get("alive")]
        streak = fleet.observe(len(alive_n))
        if not alive_n:
            # Two gates before a DM. (1) DEBOUNCE: a fleet-wide zero that clears next poll is the
            # re-association blip, not an outage. (2) SECOND PATH: WiFi is only one transport, so
            # confirm over the WiFi-independent USB segment before claiming the tablets are gone.
            mins = int(streak * POLL // 60)
            if streak < UNREACH_CONFIRM:
                return True, ("idle; 0/%d reachable on WiFi (transient? %d/%d polls -- not DMing yet)"
                              % (len(h), streak, UNREACH_CONFIRM))
            usb = usb_reach_via_rigby()
            if usb:                                    # non-empty: alive on the wire -> NOT gone
                clear("fleet")
                return True, ("idle; 0/%d on WiFi but %d reachable over USB %s -- WiFi/DNS path "
                              "down, nodes alive (not DMing)" % (len(h), len(usb), sorted(usb)))
            if usb is None:                            # could not reach rigby -> USB unverifiable
                alert("fleet", "Tablets unreachable over WiFi for %d polls (~%d min) AND rigby could "
                               "not be reached to check the USB segment -- status UNCONFIRMED. "
                               "Orders will be HELD, not failed." % (streak, mins))
                return True, "idle; 0/%d WiFi, USB INCONCLUSIVE (rigby unreachable)" % len(h)
            alert("fleet", "NO tablet answered on EITHER WiFi (%d polls, ~%d min) OR the USB segment "
                           "-- the fleet is truly unreachable. Orders will be HELD, not failed."
                           % (streak, mins))
            return True, "idle; 0/%d WiFi AND 0 over USB (truly unreachable)" % len(h)
        clear("fleet")
        return True, "idle; %d/%d nodes healthy (%s)" % (len(up), len(h), ", ".join(sorted(up)))
    except Exception as e:
        return True, "idle; INCONCLUSIVE fleet probe (%s)" % type(e).__name__


def patrol_rigby(prog):
    """The rigby lane's order, watched for progress the same way as the fleet lane's (one derivation).
    Returns a detail string, or "" when the rigby lane has no order."""
    cur, cur_err = _read(CURRENT_RIGBY)
    if cur_err:
        return "rigby lane: INCONCLUSIVE (claim unreadable: %s)" % cur_err
    order = (cur or {}).get("name")
    if not order:
        prog.observe(None, None)
        clear("stall-rigby")
        return ""
    if cur.get("phase") == WINDOW_WAITING:
        # WINDOW_WAITING is an intentional hold state, not a stall. Resetting the stall detector here ensures the next active turn is timed from its own start, preventing false positives.
        prog.observe(None, None)
        clear("stall-rigby")
        return "rigby lane: %r open, waiting for a turn (%s served)" % (order, cur.get("done"))
    done, work = cur.get("done"), cur.get("work")
    verdict, why = prog.observe(order, progress_key(done, work, "tokens"))   # a window's work: its stream's chunk lines
    if verdict == "stalled":
        alert("stall-rigby", "rigby lane order %r has not advanced: %s" % (order, why))
        return "rigby lane: ALERT %r %s" % (order, why)
    clear("stall-rigby")
    return "rigby lane: watching %r: %s" % (order, why)


INBOX_CONFIRM_S = 2 * POLL


def patrol_inbox():
    """"" while the order taker can read its inbox; otherwise a line for the tile, DM'd once it has lasted two polls.
    The runner owns the measurement (it mounts the share; this container does not) and states it on its heartbeat."""
    hb, err = _read(HEARTBEAT)
    since = (hb or {}).get("inbox_down_since")
    if err or not since:
        clear("inbox")
        return ""
    mins = int((time.time() - float(since)) // 60)
    line = "INBOX UNREACHABLE for %d min (%s): no order or slip can arrive" % (mins, hb.get("inbox_error") or "?")
    if time.time() - float(since) >= INBOX_CONFIRM_S:
        alert("inbox", "Tolkien cannot read its order inbox (%s) for %d min: no new order, stop slip or control slip "
                       "can reach it; queued work keeps running. Check rigby's share mounts (rigby-shares.service)."
                       % (hb.get("inbox_error") or "?", mins))
    return line


def main():
    import tablet_foundry
    tablet_foundry.install_stop()          # PID 1: exit on a stop instead of making docker wait its 10 s grace
    log("[WATCH] fellowship watchdog up; poll=%ds, hb_stale=%ds, stall=%ds"
        % (POLL, HEARTBEAT_STALE_S, NO_PROGRESS_S))
    prog = Progress()
    prog_rigby = Progress()
    fleet = FleetReach()
    while True:
        try:
            alive, detail = patrol(prog, fleet)
            rig = patrol_rigby(prog_rigby)
            if rig:
                detail = "%s | %s" % (detail, rig)
            inbox = patrol_inbox()
            if inbox:
                detail = "%s | %s" % (inbox, detail)
        except Exception as e:
            # A watchdog must never die of its own bug. It must also never go quiet about it --
            # and the handler must not be able to reproduce the failure it is reporting, which is
            # why this formats only a type name and never re-emits arbitrary payload text.
            alive, detail = True, "INCONCLUSIVE: patrol error (%s)" % type(e).__name__
            log("[WATCH] patrol error (%s)" % type(e).__name__)
        publish_health(alive, detail, disabled=os.path.exists(STAND_DOWN))
        log("[WATCH] %s" % detail)
        time.sleep(POLL)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        p = Progress()
        a, d = patrol(p, FleetReach())
        print("alive=%s  %s" % (a, d))
    else:
        main()
