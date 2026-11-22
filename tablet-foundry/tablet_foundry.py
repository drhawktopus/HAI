"""TABLET FLEET ORDER TAKER - the Fellowship's factory layer.

Design: Reference/Tablet-Fleet-Order-Taker-Design.md. The SCHEDULER half is imported, never
reimplemented (scheduler.place(), chunk(), TABLETS, tablet_alive()).

SHAPE: serial in ORDERS, parallel in ITEMS - one order fanned across independent nodes; same
product-order stages as the GPU factory, own queue and own runner. A sibling factory, not a
second implementation."""
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from queue import Empty, Queue

# Fail closed on HEAD; tolerant on AGENT (Termux). Agent lacks scheduler;
# hard import would break agent and cause drift.
#
# On HEAD: if missing, record error in _SCHED_ERR. `_require_scheduler()`
# re-raises it to prevent silent failure (no placement, no noise).
# Only code that never places work may run without scheduler.
S = None
_SCHED_ERR = None
try:
    import scheduler as S
except Exception:                                   # runnable from Softserve without the container
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import scheduler as S
    except Exception as _e:                         # the agent's normal case -- not an error there
        _SCHED_ERR = _e


def _require_scheduler():
    """Head-side gate. Raises the ORIGINAL import error rather than a generic AttributeError."""
    if S is None:
        raise RuntimeError("scheduler unavailable on this host (%s: %s) -- head-side placement "
                           "cannot run here" % (type(_SCHED_ERR).__name__, _SCHED_ERR))
    return S

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.environ.get("TABLET_FOUNDRY_ROOT", "/tabfoundry")      # the container's local bind (the compose file sets it)
QUEUE_DIR = os.path.join(ROOT, "queue")
ARCHIVE_DIR = os.path.join(ROOT, "archive")
STATE_DIR = os.path.join(ROOT, "state")
RESULTS_DIR = os.path.join(ROOT, "results")
RUNNER_LOCK = os.path.join(STATE_DIR, "runner.lock")
HEARTBEAT = os.path.join(STATE_DIR, "runner.heartbeat")
CURRENT = os.path.join(STATE_DIR, "CURRENT_ORDER.json")
# The rigby lane's own claim: rigby is a unit like each tablet, so its order is claimed beside the fleet lane's.
CURRENT_RIGBY = os.path.join(STATE_DIR, "CURRENT_ORDER.rigby.json")
HAI_URL = os.environ.get("HAI_URL", "")       # the head this factory is linked to ('' = not linked)
# the suffix that makes a tablet's name resolvable on this LAN (".lan" with a Pi-hole, often ".local"); "" = bare names
TABLET_DNS_SUFFIX = os.environ.get("TABLET_DNS_SUFFIX", "")
LANE = threading.local()          # .name = "rigby" inside the rigby lane's thread; absent = the fleet lane


def lane_name():
    return getattr(LANE, "name", "fleet")


# A rigby chat window between turns: open, holding its model, nothing to answer. The runner states it in the lane claim's
# `phase` and the watchdog reads the same constant (one derivation): a window waiting for HAI's next turn is not stalled.
WINDOW_WAITING = "window: waiting for a turn"


def window_turn_in_flight(items_dir):
    """-> (in_flight, chunks) for a chat window's items folder: whether an item still waits for its answer (an
    `<id>.item.json` with no `<id>.out.json`), and how many lines its stream file holds so far. Returns (None, None)
    when the folder cannot be read, indicating the caller should state nothing new."""
    try:
        names = os.listdir(items_dir)
    except (OSError, TypeError):
        return None, None
    outs = {n[:-len(".out.json")] for n in names if n.endswith(".out.json")}
    pending = [n[:-len(".item.json")] for n in names if n.endswith(".item.json") and n[:-len(".item.json")] not in outs]
    if not pending:
        return False, 0
    chunks = 0
    for iid in pending:
        try:
            with open(os.path.join(items_dir, iid + ".stream.jsonl"), "rb") as f:
                chunks += sum(1 for _ in f)
        except OSError:
            pass                                    # not streamed (or not started yet): its row is the answer
    return True, chunks


class as_lane:
    """Run a block as `name`'s lane on THIS thread, restoring what was there. For callbacks a lane hands to code that
    calls them from its own threads (Inspect runs the agentic payload's model calls in worker threads), where the
    thread-local lane would otherwise read as the fleet lane and the rigby lane's claim would land in the fleet's."""

    def __init__(self, name):
        self.name, self.prev = name, None

    def __enter__(self):
        self.prev = getattr(LANE, "name", None)
        LANE.name = self.name
        return self

    def __exit__(self, *exc):
        if self.prev is None:
            if hasattr(LANE, "name"):
                del LANE.name
        else:
            LANE.name = self.prev
        return False

# INBOX/OUTBOX: Job files and results are COPIED across the CIFS boundary.
# Do not use os.replace (rename) between local disk and mount:
# it raises EXDEV, causing retire_order to fail after work completes.
INBOX = os.environ.get("TABLET_INBOX", "/ssdshared/tabfoundry-jobs/inbox")
OUTBOX = os.environ.get("TABLET_OUTBOX", "/ssdshared/tabfoundry-jobs/outbox")
FLEET_HOST = os.environ.get("TABLET_FLEET_HOST", "fellowship")
REMOTE_STAGE = "tfoundry_stage"
# library-triage extract (30b): the head reads docs from the mount this container already holds
# (read-only, same mount the NVR credential doc rides), and the rules-lane payload ships to the
# nodes from this image. Both are DETECTED preconditions of the capability, never configuration.
DOCS_ROOT = os.environ.get("TOLKIEN_DOCS_ROOT", "/docs")
TRIAGE_PAYLOAD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "triage_extract_payload.py")

# Batch must be >= the widest node's worker count or that node starves: gandalf/frodo/sam run up to
# 8 workers, and a 3-image batch left 5 of 8 cores idle (measured: 10.1 -> 15-17 s/img).
BATCH = int(os.environ.get("TABLET_BATCH", "8"))

# SKU-affinity lanes for matrix orders (opt-in via spec["sku_affinity"]). Same-hardware tablets share
# a lane and SPLIT it by dynamic pull; a cell tagged for a lane runs only on that lane's tablets. This
# is the EXPERIMENT's grouping, not a hardware fact (the hardware roster is scheduler.TABLETS), so it
# lives here in the head, not the shared placement brain. sam is frodo-class hardware (8c/1.76GB) but
# runs its OWN lane: its gflops trails its peers (15.16 vs ~17.2) and the point is to MEASURE whether
# that disparity persists, not assume it away by pooling. A test pins these keys to scheduler.TABLETS
# so a roster change cannot silently orphan a lane.
# Each tablet's hardware class (A, B or C, the Fellowship roster's `class`) is the TABLET_CLASSES setting, JSON
# {"name": "A"}, and a tablet that runs its OWN lane over its class is TABLET_SKU_LANES, JSON {"name": "lane"} (both in
# the tablet services' env_file, beside TABLET_NET). Unset or unreadable = no class known: no lanes, and a tablet with
# no class gets the largest model that fits.
def _tablet_table(name):
    try:
        table = json.loads(os.environ.get(name, "") or "{}")
        return {str(k): str(v) for k, v in table.items()}
    except (ValueError, TypeError, AttributeError):
        return {}


TABLET_CLASSES = _tablet_table("TABLET_CLASSES")
TABLET_SKU = dict(TABLET_CLASSES, **_tablet_table("TABLET_SKU_LANES"))


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(m):
    # Include the date in log lines: the log spans months, so time-only entries are ambiguous.
    print("%s  [TFOUNDRY] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), m), flush=True)


def sh(cmd, timeout=300):
    """Returns (rc, output). Never raises; all exceptions map to non-zero rc."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %ss" % timeout
    except Exception as e:
        return 125, "%s: %s" % (type(e).__name__, str(e)[:160])



ADB = os.environ.get("TABLET_ADB", "adb")


# SSH via explicit IP and key (not aliases, which fail in containers). The cluster key's path is the TABLET_SSH_KEY
# setting (the tablet services' env_file); empty = ssh's own default key. Use the existing fleet key; never mint one.
TAB_KEY = os.environ.get("TABLET_SSH_KEY", "")

# The tablet roster: friendly name -> (address, serial). Serials are the ADB/cluster identity. It is the TABLET_NET
# setting, JSON {"name": ["address", "serial"]}, from the environment (the tablet services' env_file); unset or
# unreadable means no tablets are linked. Every consumer reads this one table.
def _tablet_net(raw=None):
    raw = os.environ.get("TABLET_NET", "") if raw is None else raw
    try:
        table = json.loads(raw) if raw else {}
        out = {}
        for k, v in table.items():
            if not (isinstance(v, (list, tuple)) and len(v) == 2):
                return {}                      # one malformed row means the table is not trusted at all
            out[str(k)] = (str(v[0]), str(v[1]))
        return out
    except (ValueError, TypeError, AttributeError):
        return {}


TABLET_NET = _tablet_net()


def _key_path():
    """The cluster key's path from the TABLET_SSH_KEY setting, or '' when none is set (ssh uses its own default key)."""
    return TAB_KEY


# Segment octets are the roster's wire addresses (WIRE_PREFIX + octet, on the hub's USB bridge): the
# TABLET_WIRE_OCTETS setting, JSON {"name": octet} (the tablet services' env_file), beside TABLET_NET. Unset or
# unreadable = no node has a wire address, and each is reached by WiFi.
# adb stays on WiFi on purpose: it is the control path that repairs a node's USB link.
def _wire_octets(raw=None):
    raw = os.environ.get("TABLET_WIRE_OCTETS", "") if raw is None else raw
    try:
        table = json.loads(raw) if raw else {}
        return {str(k): int(v) for k, v in table.items()}
    except (ValueError, TypeError, AttributeError):
        return {}


WIRE_OCTET = _wire_octets()
# The wire subnet is the TABLET_WIRE_PREFIX setting (the tablet services' env_file), e.g. "10.0.0.": a node's wire
# address is the prefix plus its octet. Empty = this head has no wire; every node is reached by its WiFi address.
WIRE_PREFIX = os.environ.get("TABLET_WIRE_PREFIX", "")
WIRE_CIDR = (WIRE_PREFIX + "0/24") if WIRE_PREFIX else ""


def wire_ip(host):
    """The node's wire address, or None when no wire subnet is set or the node has no octet."""
    octet = WIRE_OCTET.get(host)
    return "%s%d" % (WIRE_PREFIX, octet) if (WIRE_PREFIX and octet is not None) else None


def on_wire(ip):
    """Whether an address is on the wire subnet (False when none is set)."""
    return bool(WIRE_PREFIX) and str(ip or "").startswith(WIRE_PREFIX)


ROUTE_TTL_S = float(os.environ.get("TABLET_ROUTE_TTL", "60"))
# "0" = never probe the wire, always WiFi. The test gate sets it at the container boundary, so no test
# can reach a real wire address (on rigby the wire exists and the chosen address would depend on the box).
ROUTE_PROBE = os.environ.get("TABLET_ROUTE_PROBE", "1") != "0"
_ROUTE = {}                       # (host, port) -> (ip, via, expires)
_ROUTE_LK = threading.Lock()
_CONNECT_FAILED = ("Connection refused", "Connection timed out", "No route to host",
                   "Network is unreachable", "timed out during banner exchange")


def _wifi_ip(host):
    ip = TABLET_NET.get(host, (host + TABLET_DNS_SUFFIX, ""))[0]
    return ip


def node_ip(host, port, refresh=False):
    """(ip, via) for talking to `host` on `port`: its wire address when that port answers there (a
    1.5 s connect, remembered ROUTE_TTL_S), else its WiFi address. via is "wire" or "wifi"."""
    now_t = time.time()
    with _ROUTE_LK:
        hit = _ROUTE.get((host, port))
        if hit and not refresh and hit[2] > now_t:
            return hit[0], hit[1]
    wire = wire_ip(host)
    ip, via = _wifi_ip(host), "wifi"
    if wire and ROUTE_PROBE:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        try:
            if s.connect_ex((wire, int(port))) == 0:
                ip, via = wire, "wire"
        except OSError:
            pass
        finally:
            s.close()
    with _ROUTE_LK:
        _ROUTE[(host, port)] = (ip, via, now_t + ROUTE_TTL_S)
    return ip, via


def _route_failed(host, port):
    """The wire was chosen but could not be connected: pin WiFi for this host+port for ROUTE_TTL_S."""
    with _ROUTE_LK:
        _ROUTE[(host, port)] = (_wifi_ip(host), "wifi", time.time() + ROUTE_TTL_S)


def _ssh_base(host):
    ip, _via = node_ip(host, 8022)
    key = _key_path()
    return ((['-i', key] if key else []) + ['-p', '8022', '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
            '-o', 'ConnectTimeout=20'], ip)


SSH_KEEPALIVE = ['-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=4']   # a dead link ends in ~2 min


def _ssh(host, cmd, timeout=120):
    """timeout=None: no clock on the work; keepalives catch dead links. Do not add a timeout to long-running payloads."""
    keepalive = timeout is None
    opts, ip = _ssh_base(host)
    if keepalive:
        opts = opts + SSH_KEEPALIVE
    rc, out = sh_argv(["ssh"] + opts + [ip, cmd], timeout=timeout)
    # Fall back to WiFi ONLY when the wire connection was never made (nothing ran on the node), so a
    # command that started and then failed is never run a second time.
    if rc == 255 and on_wire(ip) and any(m in out for m in _CONNECT_FAILED):
        _route_failed(host, 8022)
        opts, ip = _ssh_base(host)
        if keepalive:
            opts = opts + SSH_KEEPALIVE
        rc, out = sh_argv(["ssh"] + opts + [ip, cmd], timeout=timeout)
    return rc, out


def _scp(host, local, remote, timeout=600):
    opts, ip = _ssh_base(host)
    opts = ["-P" if o == "-p" else o for o in opts]        # scp uses uppercase -P
    return sh_argv(["scp"] + opts + [local, "%s:%s" % (ip, remote)], timeout=timeout)


def sh_argv(argv, timeout=300):
    """Never raises; argv LIST, so nothing is re-parsed by a shell (paths here contain spaces)."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %ss" % timeout
    except Exception as e:
        return 125, "%s: %s" % (type(e).__name__, str(e)[:160])


_ADB_MAP = {}          # host -> adb target, cached; adb serials are opaque and must be discovered
_ADB_MAP_TS = 0.0


def _adb_targets(ttl=120.0):
    """host -> whatever adb target actually works, discovered rather than assumed.

    TWO transports coexist and a node can be on either:
      * `<ip>:5555`  once `persist.adb.tcp.port=5555` has taken effect, which happens AT BOOT
      * an mDNS/TLS serial (`adb-HA10F5DZ-..._adb-tls-connect._tcp`) from Android 12 wireless
        debugging, which uses a RANDOM port and an opaque serial
    Assuming `<ip>:5555` marked four of five nodes UNKNOWN purely because they had not rebooted
    since the property was set. Serials are opaque, so each connected device is asked for its OWN
    wlan0 address and mapped back by IP -- never by the order adb happens to list them in."""
    global _ADB_MAP, _ADB_MAP_TS
    if _ADB_MAP and time.time() - _ADB_MAP_TS < ttl:
        return _ADB_MAP
    ip2host = {}
    for h in _require_scheduler().TABLETS:
        rc, out = sh("ping -n 1 -w 1000 %s%s" % (h, TABLET_DNS_SUFFIX) if os.name == "nt"
                     else "getent hosts %s%s" % (h, TABLET_DNS_SUFFIX),
                     timeout=20)
        for tok in out.replace("[", " ").replace("]", " ").split():
            if tok.count(".") == 3 and tok.replace(".", "").isdigit():
                ip2host[tok] = h
                break
    m = {}
    rc, out = sh("%s devices" % ADB, timeout=45)
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 2 and p[1] == "device" and not line.startswith("List"):
            rc2, ipout = sh('%s -s %s shell "ip -4 addr show wlan0"' % (ADB, p[0]), timeout=45)
            mm = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", ipout)
            if mm and mm.group(1) in ip2host:
                m[ip2host[mm.group(1)]] = p[0]
    # Fixed port works post-reboot. Try wired ADB first; fall back to WiFi if the wire fails.
    for h in _require_scheduler().TABLETS:
        if h not in m:
            tgts = ([wire_ip(h) + ":5555"] if wire_ip(h) else []) + ["%s%s:5555" % (h, TABLET_DNS_SUFFIX)]
            for tgt in tgts:
                rc, out = sh("%s connect %s" % (ADB, tgt), timeout=25)
                if "connected" in out.lower() and "cannot" not in out.lower() and "failed" not in out.lower():
                    m[h] = tgt
                    break
    _ADB_MAP, _ADB_MAP_TS = m, time.time()
    return m


def _adb_target(host):
    return _adb_targets().get(host)


def node_health(host, timeout=25):
    """Returns health status: {alive, whitelisted, wakelock, healthy, why}.

        Uses ssh only; does not require adb.

        Wake-lock is enforced (acquired), not inspected, because `dumpsys power`
        returns empty results for ordinary uids over ssh. Acquisition is idempotent.
    """
    h = {"host": host, "alive": False, "whitelisted": None, "wakelock": None,
         "agent_sha": None, "healthy": False, "why": ""}
    # alive by the route the work takes (node_ip: USB wire first), else the scheduler's WiFi check - a node whose WiFi
    # is down but whose wire answers is NOT unreachable (legolas, 10-08..10-10: benched two days on the wire)
    if node_ip(host, 8022, refresh=True)[1] != "wire" and not _require_scheduler().tablet_alive(host):
        h["why"] = "unreachable on the wire and WiFi (fail-closed: not offered)"
        return h
    h["alive"] = True
    # sha: prefix prevents all-digit hex hashes from being parsed as whitelist count
    rc, out = _ssh(host, "termux-wake-lock; cmd deviceidle whitelist 2>/dev/null "
                         "| tr ',' '\\n' | grep -c termux; "
                         "echo sha:$(sha256sum tablet_foundry.py 2>/dev/null | cut -c1-16)",
                   timeout=timeout + 20)
    nums = [l.strip() for l in out.splitlines() if l.strip().isdigit()]
    if nums:
        h["whitelisted"] = int(nums[-1]) > 0
    shas = [l.strip()[4:] for l in out.splitlines() if l.strip().startswith("sha:")]
    h["agent_sha"] = (shas[-1] or None) if shas else None
    h["wakelock"] = bool(rc == 0)      # acquired, not inspected

    if h["whitelisted"] is None:
        h["why"] = "could not read doze whitelist - treated as NOT healthy"
        return h
    if not h["wakelock"]:
        h["why"] = "could not acquire wake-lock over ssh"
        return h
    if not h["whitelisted"]:
        h["why"] = "NOT doze-whitelisted (will be frozen; needs adb: cmd deviceidle whitelist +com.termux)"
        return h
    if not h["wakelock"]:
        h["why"] = "wake-lock not held (sustained CPU throttled; run termux-wake-lock)"
        return h
    h["healthy"] = True
    h["why"] = "ok"
    return h


DRAM_PROBE_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tablet_dram_probe.py")
DRAM_MIN_MGATHER = float(os.environ.get("TABLET_DRAM_MIN", "1.5"))


def dram_health(host, timeout=180):
    """Return DRAM throughput (M gathers/s). Returns None if measurement failed.

    Measures random-access throughput using a 48 MB working set to bypass
    cache/prefetcher masking of degraded memory states. Sequential or
    small benchmarks may report healthy speeds on degraded nodes.

    Thresholds:
    - Healthy: ~2.0-2.9 M gathers/s
    - Degraded: <1.5 M gathers/s (requires reboot to fix)
    """
    # the same transport every other tablet call uses (wire first); a bare host alias is not resolvable in the container
    rc, out = _scp(host, DRAM_PROBE_SRC, "_dram_probe.py")
    if rc != 0:
        return None
    rc, out = _ssh(host, "python _dram_probe.py", timeout=timeout)
    if rc != 0:
        return None
    for line in (out or "").splitlines():
        try:
            return float(line.strip())
        except ValueError:
            continue
    return None


def fleet_health(hosts=None):
    hosts = list(hosts or _require_scheduler().TABLETS)
    out, threads = {}, []
    lk = threading.Lock()

    def one(x):
        r = node_health(x)
        with lk:
            out[x] = r
    for x in hosts:
        t = threading.Thread(target=one, args=(x,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return out


def try_repair(host):
    """Re-acquire the wake-lock -- the one suppression a non-root ssh CAN fix.

    The doze whitelist needs DEVICE_POWER (adb only) and is deliberately NOT attempted here: a
    factory should report what it cannot fix, not silently half-fix it."""
    sh('ssh -o ConnectTimeout=20 %s "termux-wake-lock"' % host, timeout=60)
    return node_health(host)


# Omit optional fields rather than sending nulls (validation rejects explicit nulls).
# Publish when IDLE to ensure the factory remains routable; omitting idle pushes results in zero reported capabilities.

_PUB = {"fleet": None, "rigby": None}     # the fleet lane's RUNNING orders {name: args} (None = idle), rigby's order
_PUB_LK = threading.Lock()


def claim_orders(claim):
    """Return the list of orders for a lane claim, oldest first. Fleet claims may contain multiple concurrent orders, while rigby claims contain a single one. All readers must use this derivation to ensure consistent state."""
    if not isinstance(claim, dict):
        return []
    rows = claim.get("orders")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict) and r.get("name")]
    return [claim] if claim.get("name") else []


def fleet_running_names():
    """The fleet-lane orders the tile shows as running right now."""
    with _PUB_LK:
        return list((_PUB["fleet"] or {}).keys())


def _fleet_view(runs, caps, ttl):
    """The tile's fleet line from every running fleet order: one order shows as itself; several show as ONE line naming
    each (the tile has one Order row) with their steps summed, and the first-started order's ETA and model rows."""
    if not runs:
        return {"name": "", "state": "idle", "phase": "", "step": 0, "total": 0, "caps": caps, "ttl": ttl, "eta": "",
                "gen": None}
    order = list(runs.values())
    if len(order) == 1:
        return dict(order[0], caps=caps)
    first = order[0]
    return {"name": "%d orders: %s" % (len(order), " + ".join(a["name"] for a in order)), "state": "running",
            "phase": " · ".join("%s %d/%d" % (a["name"], int(a["step"] or 0), int(a["total"] or 0)) for a in order),
            "step": sum(int(a["step"] or 0) for a in order), "total": sum(int(a["total"] or 0) for a in order),
            "caps": caps, "ttl": max(int(a["ttl"] or 0) for a in order), "eta": first["eta"], "gen": first["gen"]}


def publish(name="", state="idle", phase="", step=0, total=0, caps=None, ttl=180, eta="", gen=None):
    """Push the Fellowship's run-state. Each lane records its own view; the push always carries both, so rigby's
    order shows on the tile beside the tablet work instead of overwriting it. `gen` (the order's model work, for the
    tile's Speed / Step / Context rows) goes with the order the tile names: rigby's rows are not the tablets' order's.
    The fleet lane keeps one entry per RUNNING order: a named order publishing `idle` has ended (its entry goes), an
    unnamed idle means the lane runs nothing."""
    args = {"name": name, "state": state, "phase": phase, "step": step, "total": total, "caps": caps,
            "ttl": ttl, "eta": eta, "gen": gen}
    with _PUB_LK:
        lane = lane_name()
        if lane == "rigby":
            _PUB["rigby"] = args if state != "idle" else None
        else:
            if caps is not None:
                _PUB["fleet_caps"] = caps
            runs = dict(_PUB["fleet"] or {})
            if state == "idle" and not name:
                runs = {}
            elif state == "idle":
                runs.pop(name, None)
            else:
                runs[name] = args
            _PUB["fleet"] = runs or None
    return _push_view(ttl)


def _push_view(ttl=180):
    """Post the Fellowship's current view: every running fleet order, rigby's order beside them, and the capacity."""
    with _PUB_LK:
        fleet = _fleet_view(_PUB["fleet"], _PUB.get("fleet_caps"), ttl)
        rig = _PUB["rigby"]
    if rig:
        note = "rigby: %s %d/%d" % (rig["name"], rig["step"], rig["total"])
        if fleet["state"] == "idle":
            fleet.update(name="rigby: %s" % rig["name"], state="running", phase=rig["phase"],
                         step=rig["step"], total=rig["total"], eta=rig["eta"], gen=rig.get("gen"))
        else:
            fleet["phase"] = ("%s · %s" % (fleet["phase"], note)) if fleet["phase"] else note
    return _post_run_state(**fleet)


# CAPACITY[0] holds the current free-node count or None (unavailable).
# The runner owns this value and must update it directly; do not derive it from local state.
CAPACITY = [None]
_CAP_LAST = [None]


def capacity():
    """What Tolkien has free right now, or None when it cannot say (never a made-up 'all free')."""
    fn = CAPACITY[0]
    if fn is None:
        return None
    try:
        return fn()
    except Exception:                                                # noqa: BLE001
        return None


def push_capacity_if_changed():
    """Re-post the current view the moment the free-spot report changes (an order took or gave back tablets, a piece
    joined rigby's line), so Spock never weighs a minute-old spot. Returns True when it pushed. Never raises."""
    try:
        cap = capacity()
        if cap is None or cap == _CAP_LAST[0]:
            return False
        _push_view()
        return True
    except Exception:                                                # noqa: BLE001
        return False


def _post_run_state(name="", state="idle", phase="", step=0, total=0, caps=None, ttl=180, eta="", gen=None):
    body = {"host": FLEET_HOST, "name": name, "state": state, "ttl": ttl}
    cap = capacity()
    if cap is not None:
        body["capacity"] = cap
        _CAP_LAST[0] = cap
    if gen:
        body["gen"] = gen           # the desk keeps only the fields its tile formatter reads
    if phase:
        body["phase"] = phase
    if step or total:
        body["step"], body["total_steps"] = int(step), int(total)
    if eta:
        body["eta"] = eta           # the deck reads "<n> s/item" and "~<m>m left" out of this string
    if caps is not None:
        body["capabilities"] = list(caps)
    try:
        import urllib.request
        req = urllib.request.Request(HAI_URL.rstrip("/") + "/fleet/run-state",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            status = r.status
        # Endpoint returns HTTP 200 for both success and logical errors.
        # Check the body's 'ok' field, not just the status code.
        try:
            ans = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            ans = {}
        if isinstance(ans, dict) and ans.get("ok") is False:
            log("capability publish REFUSED by the orchestrator: %s" % ans.get("error"))
            return None
        return status
    except Exception as e:
        # Telemetry must never break the factory -- but say so, because a silent reporter is how a
        # tile read DOWN for eleven hours while the daemon was perfectly healthy.
        log("capability publish failed (%s) - orders continue" % type(e).__name__)
        return None


P20_TOKEN = os.environ.get("P20_WORKER_TOKEN", "")
_FLEET_DESK_QUIET = [False]
_PLACED_WAITING = {}            # desk id -> why a placed order is still waiting (logged once per reason)


def _desk_call(path, method="GET"):
    import urllib.request
    req = urllib.request.Request(HAI_URL.rstrip("/") + path, method=method,
                                 headers={"X-P20-Token": P20_TOKEN})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def _desk_post(path, payload, timeout=15):
    """POST a JSON body to Kirk with the worker token -> the JSON answer (the desk call's transport, with a body)."""
    import urllib.request
    req = urllib.request.Request(HAI_URL.rstrip("/") + path, method="POST", data=json.dumps(payload).encode("utf-8"),
                                 headers={"X-P20-Token": P20_TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


# SHADOW: Tolkien consults McCoy on a background thread and logs advice BESIDE its own decision.
# Nothing waits on or acts on the advice; the log is data for later threshold tuning.
TOLKIEN_MCCOY = os.environ.get("TOLKIEN_MCCOY", "shadow").strip().lower()      # "off" stops asking
MCCOY_SHADOW_LOG = os.path.join(STATE_DIR, "mccoy_shadow.jsonl")
MCCOY_STATE_CHARS = 1500


def mccoy_state(spec):
    """What McCoy is shown about one piece: the work itself, never Tolkien's decision (the comparison stays blind)."""
    p = spec.get("placement") if isinstance(spec.get("placement"), dict) else {}
    lines = ["kind: %s" % (spec.get("kind") or "sweep")]
    if p:
        lines.append("placed for family %s, tier %s, lens %s" % (p.get("family"), p.get("tier"), p.get("lens")))
    if spec.get("payload"):
        lines.append("payload: %s" % os.path.basename(str(spec["payload"])))
    if spec.get("model"):
        lines.append("model named: %s" % spec["model"])
    items = spec.get("items") if isinstance(spec.get("items"), list) else None
    tasks = spec.get("tasks") if isinstance(spec.get("tasks"), list) else None
    if items is not None:
        lines.append("items: %d" % len(items))
    elif tasks is not None:
        lines.append("tasks: %d x %s replicates" % (len(tasks), spec.get("replicates", 1)))
    req = spec.get("request") or (str(items[0]) if items else "")
    if req:
        lines.append("request: %s" % str(req)[:MCCOY_STATE_CHARS])
    return "\n".join(lines)


def _mccoy_shadow_worker(spec, decided):
    row = {"at": now(), "order": spec.get("name"), "mode": "shadow", "decided": decided}
    try:
        if not P20_TOKEN:
            row["error"] = "no worker token - Tolkien cannot reach Kirk's McCoy door"
        else:
            row.update(_desk_post("/mccoy/tolkien", {"text": mccoy_state(spec)}))
    except Exception as e:                                           # noqa: BLE001 - a shadow never surfaces
        row["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    try:
        os.makedirs(os.path.dirname(MCCOY_SHADOW_LOG), exist_ok=True)
        with open(MCCOY_SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                                                # noqa: BLE001
        pass


def mccoy_shadow(spec, decided):
    """Ask McCoy about one piece in the background; returns the thread (or None), never raises, changes nothing."""
    try:
        if TOLKIEN_MCCOY != "shadow" or not isinstance(spec, dict):
            return None
        t = threading.Thread(target=_mccoy_shadow_worker, args=(spec, decided), name="tolkien-mccoy", daemon=True)
        t.start()
        return t
    except Exception:                                                # noqa: BLE001
        return None


def collect_fleet_orders():
    """Collect orders the FLEET DESK routed to this factory, into the queue it already drains.

    Fail-soft throughout: the desk being unreachable must never stop the factory working the
    orders it already holds. Returns the number of orders collected."""
    if not P20_TOKEN:
        if not _FLEET_DESK_QUIET[0]:
            _FLEET_DESK_QUIET[0] = True
            log("fleet desk: no P20_WORKER_TOKEN in the environment - inbox/local queue only")
        return 0
    try:
        body = _desk_call("/p20/jobs?host=%s" % FLEET_HOST)
    except Exception as e:                                           # noqa: BLE001
        log("fleet desk unreachable (%s) - working local queue" % type(e).__name__)
        return 0
    orders = [j for j in (body.get("jobs") or []) if j.get("kind") == "factory_order"]
    got = 0
    for j in orders:
        jid = j.get("id") or ""
        spec = j.get("spec") or {}
        name = str(j.get("order_name") or spec.get("name") or "").strip()
        if not jid or not name or not spec:
            log("fleet desk: skipping malformed order record %r" % (jid or "?",))
            continue
        # A PLACED order (#34 Phase 7 design § D) names no worker: pick it from the placement table BEFORE claiming,
        # so the queue only ever holds a resolved spec (its lane - rigby or the tablets - follows from the worker).
        # No worker of the Fellowship's run ready now = HAND IT BACK: the desk re-places it on the table's next
        # destination (never consumed, never parked here behind a dropped card).
        no_row = None
        if str(spec.get("kind") or "").strip().lower() == "llm" and isinstance(spec.get("placement"), dict) \
                and not spec.get("arms"):
            import tolkien_llm
            try:
                resolved, why = tolkien_llm.resolve_placed(spec, placed_caps())
            except tolkien_llm.NoFellowshipRow as e:
                resolved, why, no_row = None, str(e), str(e)
            except Exception as e:                                   # noqa: BLE001
                resolved, why = None, "placement could not be resolved (%s)" % type(e).__name__
            if resolved is None and not no_row:
                if handback_fleet_order(jid, why):
                    _PLACED_WAITING.pop(jid, None)
                    log("fleet desk: %s HANDED BACK - %s" % (name, why))
                elif _PLACED_WAITING.get(jid) != why:     # the desk did not take it back: asked again next pass
                    _PLACED_WAITING[jid] = why
                    log("fleet desk: %s WAITS (hand-back refused) - %s" % (name, why))
                continue
            if resolved is not None:
                _PLACED_WAITING.pop(jid, None)
                spec = resolved
                log("fleet desk: placed %s -> %s %s (%s)" % (name, spec["placement_row"]["box"], spec["model"],
                                                             ", ".join(spec["arms"])))
        # CLAIM BEFORE WRITE, never after: a failed write leaves the order claimed and stopped,
        # the reverse order puts one spec on two factories.
        try:
            claimed = _desk_call("/p20/jobs/%s/claim?host=%s" % (jid, FLEET_HOST), method="POST")
        except Exception as e:                                       # noqa: BLE001
            log("fleet desk: claim failed for %s (%s)" % (name, type(e).__name__))
            continue
        if not claimed.get("ok"):
            continue                 # another taker got it first: normal, not an error
        if no_row:                   # a property of the order against the table: fail it with the reason
            report_fleet_result(dict(spec, fleet_order_id=jid), False, no_row)
            log("fleet desk: %s FAILED at intake - %s" % (name, no_row))
            continue
        # ONE NAME = ONE ARTIFACT: never clobber a spec already queued; disambiguate with the
        # desk's own id so "which generation?" stays a visible question.
        dest = os.path.join(QUEUE_DIR, "%s.tjob.json" % name)
        if os.path.exists(dest):
            dest = os.path.join(QUEUE_DIR, "%s-%s.tjob.json" % (name, jid[:6]))
        tmp = dest + ".incoming"     # not .tjob.json until os.replace makes it whole in one step
        try:
            spec = dict(spec)
            spec.setdefault("fleet_order_id", jid)   # provenance: which desk order became this
            os.makedirs(QUEUE_DIR, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(spec, f, indent=2)
            os.replace(tmp, dest)
        except Exception as e:                                       # noqa: BLE001
            log("fleet desk: could not stage %s (%r)" % (name, e))
            try:
                os.remove(tmp)
            except Exception:                                        # noqa: BLE001
                pass
            continue
        got += 1
        log("fleet desk: collected order %r -> %s" % (name, os.path.basename(dest)))
    return got


def handback_fleet_order(jid, why):
    """Return an order this Fellowship cannot serve now to the desk (POST /p20/jobs/<id>/handback - the GPU foundry's
    own door): the desk re-places it on another capable factory or HOLDS it; it is never failed. True when the desk
    took it back. Fail-soft: an unreachable desk leaves the order where it is, asked again next pass."""
    from urllib.parse import urlencode
    try:
        ans = _desk_call("/p20/jobs/%s/handback?%s" % (jid, urlencode({"host": FLEET_HOST, "why": (why or "")[:300]})),
                         method="POST")
    except Exception as e:                                           # noqa: BLE001
        log("fleet desk: hand-back of %s failed (%s)" % (jid, type(e).__name__))
        return False
    return bool(isinstance(ans, dict) and ans.get("ok"))


def report_fleet_result(spec, ok, why=""):
    """Tell the FLEET DESK a collected order finished, so `after_ids` chains release truthfully:
    done releases successors; failed holds them (fail-closed). No-op for orders that did not come
    from the desk. Fail-soft -- a desk outage must never take down the retire path -- but it LOGS,
    because a swallowed receipt is how chains silently stall."""
    jid = str((spec or {}).get("fleet_order_id") or "").strip()
    if not jid:
        return
    if not P20_TOKEN:
        log("WARNING: fleet order %s finished but no worker token - successors will HOLD" % jid)
        return
    from urllib.parse import quote
    path = "/p20/jobs/%s/result?status=%s" % (jid, "done" if ok else "failed")
    if not ok:
        path += "&error=" + quote((why or "see the tablet foundry log")[:200])
    try:
        _desk_call(path, method="POST")
    except Exception as e:                                           # noqa: BLE001
        kept = _keep_desk_report(jid, path, (spec or {}).get("name"), e)
        log("fleet result receipt failed for %s (%s) - %s" % (
            jid, type(e).__name__, "kept; resent once the desk answers" if kept
            else "NOT kept - the desk record stays open and successors HOLD"))


# Undelivered completion receipts, one <jid>.json per desk: {jid, path, name, error, at}.
# The runner must resend these before collecting new orders to prevent the desk record from staying claimed indefinitely.
DESK_REPORTS = os.path.join(STATE_DIR, "desk_reports")


def _keep_desk_report(jid, path, name, err):
    """Keep an undelivered receipt for resend_desk_reports. True when kept. Never raises."""
    try:
        os.makedirs(DESK_REPORTS, exist_ok=True)
        p = os.path.join(DESK_REPORTS, "%s.json" % jid)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "path": path, "name": name, "error": repr(err)[:200],
                       "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, indent=1)
        os.replace(p + ".tmp", p)
        return True
    except Exception:                                                # noqa: BLE001
        return False


def resend_desk_reports():
    """Deliver every kept receipt once the desk answers. Desk unreachable = WAIT: the pass stops at the first
    connection failure, quietly. The desk ANSWERING with a refusal (HTTP error) is about that one receipt: set aside
    as `.refused` for a human, the rest still go. Returns the desk ids reported."""
    import glob
    import urllib.error
    done = []
    for mp in sorted(glob.glob(os.path.join(DESK_REPORTS, "*.json"))):
        try:
            with open(mp, encoding="utf-8") as f:
                m = json.load(f)
            jid, path = str(m["jid"]), str(m["path"])
        except Exception as e:                                       # noqa: BLE001
            log("desk receipt marker %s unreadable (%r) - left for a human" % (mp, e))
            continue
        try:
            _desk_call(path, method="POST")
        except urllib.error.HTTPError as e:
            try:
                os.replace(mp, mp + ".refused")
            except OSError:
                pass
            log("desk REFUSED the kept receipt for %s (%s): HTTP %s - set aside as .refused for a human"
                % (jid, m.get("name"), e.code))
            continue
        except Exception:                                            # noqa: BLE001
            break                                                    # the desk is still down: wait, quietly
        try:
            os.remove(mp)
        except OSError:
            pass
        log("RESENT desk receipt for %s (%s; the first try failed at %s: %s)"
            % (jid, m.get("name"), m.get("at"), m.get("error")))
        done.append(jid)
    return done


# ── REPORTING ────────────────────────────────────────────────────────────────────────────────────
# Reuses the lab's EXISTING relay (hawkadminbot on arrnet) rather than a second notification path.
# The GPU factory speaks the same way -- `notify_desk()` in p20_foundry.py -- so both factories
# arrive in the same DM with the same shape.
DM_URL = os.environ.get("HAWKADMIN_DM_URL", "http://hawkadminbot:8477/dm")
DM_TOKEN = os.environ.get("HAWKADMIN_DM_TOKEN", "")
DM_USER = os.environ.get("ADMIN_DISCORD_ID", "")


def notify_desk(message):
    """Send a status message to the admin desk. Fails soft (never blocks factory ops) but logs errors.

    Note: The endpoint may return 200 with an error payload, so inspect the response body."""
    log("[DESK] %s" % message.replace("\n", " | "))
    if not (DM_TOKEN and DM_USER):
        return None
    try:
        import urllib.request
        req = urllib.request.Request(
            DM_URL,
            data=json.dumps({"user_id": DM_USER,
                             "message": "[fellowship:desk] %s" % message}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-DM-Token": DM_TOKEN})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            status = r.status
        try:
            ans = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            ans = {}
        if isinstance(ans, dict) and ans.get("ok") is False:
            log("DM refused: %s" % ans.get("error"))
            return None
        return status
    except Exception as e:
        log("DM failed (%s) - orders continue" % type(e).__name__)
        return None


def detect_capabilities(health=None):
    """What this fleet can BUILD, by DETECTION -- never a configured list."""
    health = health or fleet_health()
    ok = sorted(h for h, v in health.items() if v["healthy"])
    caps = []
    try:                                  # rigby's own lane does not need a healthy tablet (lazy: tolkien_llm imports us)
        import tolkien_llm
        caps += tolkien_llm.rigby_capabilities()
        caps += tolkien_llm.placed_capabilities()     # placed orders: Tolkien picks rigby or a tablet from the table
    except Exception as e:                # noqa: BLE001 - a probe that cannot run claims nothing
        log("rigby capability probe failed (%s) - rigby.llm not declared" % type(e).__name__)
    if not ok:
        _LAST_CAPS[0] = set(caps)
        return caps, ok
    caps.append("tablet_cpu")
    # Use _ssh (IP+key) to probe capability. Probe multiple nodes to tolerate flaky ssh.
    for host in ok[:2]:
        rc, out = _ssh(host, "command -v tesseract >/dev/null && echo yes", timeout=60)
        if "yes" in out:
            caps.append("tablet_ocr")
            break
    caps.append("tablet_nodes_%d" % len(ok))
    # library-triage extract (30b): detected, never configured -- the head must hold the docs
    # mount AND the payload must exist in this image, or the fleet cannot build the order and
    # must not claim it can (declared-but-absent is the failure mode this line refuses).
    if os.path.isdir(DOCS_ROOT) and os.path.exists(TRIAGE_PAYLOAD):
        caps.append("triage.extract.v1")
    _LAST_CAPS[0] = set(caps)
    return caps, ok


_LAST_CAPS = [set()]    # the last detection (the main loop re-detects every 120 s): what a placed order may resolve to


def placed_caps():
    """Capabilities a placed order resolves against: rigby's own, read live (cheap file checks), plus the last fleet
    detection for the tablets (an ssh probe per node is not repeated per collected order)."""
    import tolkien_llm
    return set(_LAST_CAPS[0]) | set(tolkien_llm.rigby_capabilities())


def expand_triage_extract(spec):
    """ONE triage.extract order (docs only) in, a standard MAP order out.

    The head does the expansion because it owns the /docs mount; the rules lane then fans across
    the nodes on the unchanged map machinery, inheriting batching, the positive control,
    coverage accounting and shipping. Items are BASE64 of per-section JSON -- map items are
    newline-delimited opaque strings and section text carries newlines and pipes, so encoding
    sidesteps every framing question (and the spec_defects pipe guard) at once.

    The injected control is a SYNTHETIC credential section (never a real secret): the payload
    must settle it as `credential` or the whole run is VOID -- a rules lane that cannot read a
    known positive proves nothing by finding nothing.

    Raises ValueError on an unreadable doc or a zero-section split; build_order HOLDs the order
    with the reason named. A zero-section split is a broken splitter, not an empty doc."""
    import base64
    from triage_extract_payload import split_sections
    items = []
    for rel in (spec.get("docs") or []):
        p = os.path.join(DOCS_ROOT, str(rel).replace("\\", "/").lstrip("/"))
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except Exception as e:                                       # noqa: BLE001
            raise ValueError("unreadable doc %s (%s: %s)" % (rel, type(e).__name__, e))
        secs = split_sections(text)
        if not secs:
            raise ValueError("zero sections from %s - empty doc or broken splitter" % rel)
        for s in secs:
            items.append(base64.b64encode(json.dumps(
                {"doc": rel, "heading": s["heading"],
                 "lines": "%d-%d" % (s["start"], s["end"]), "text": s["text"]},
                ensure_ascii=False).encode("utf-8")).decode("ascii"))
    ctl = base64.b64encode(json.dumps(
        {"doc": "(control)", "heading": "(positive control)", "lines": "0-0",
         "text": "The admin password is `control-swordfish-000` for the decoy panel."}
    ).encode("utf-8")).decode("ascii")
    out = dict(spec)
    out.update({"kind": "map", "items": items, "payload": TRIAGE_PAYLOAD,
                "control": {"item": ctl, "expect": "credential"},
                "batch": int(spec.get("batch") or 40)})
    log("triage.extract %s: %d docs -> %d sections (+1 control)"
        % (spec.get("name"), len(spec.get("docs") or []), len(items)))
    return out


# ── INTAKE ───────────────────────────────────────────────────────────────────────────────────────

def spec_defects(spec):
    """Validate spec fields to catch silent failures early.

    Supports two kinds:
      - "sweep" (default): items are FILES staged from src_host; control is a known-positive FILE.
      - "map": items are opaque STRINGS passed one line at a time; no staging; control is a known-answer ITEM.

    Both share the same payload contract (`python payload.py items.txt out.json`), shipping, and validators.
    A valid control item is required for both kinds to distinguish broken payloads from empty results."""
    d = []
    kind = (spec.get("kind") or "sweep").strip().lower()
    if kind not in ("sweep", "map", "map.window", "triage.extract", "llm", "voltron", "bench", "embed", "chat.window", "page.render"):
        d.append("unknown kind: %r (sweep|map|map.window|triage.extract|llm|bench|embed|chat.window|page.render)" % kind)
        return d
    if kind == "page.render":
        import tolkien_page
        return tolkien_page.page_defects(spec)
    if kind == "chat.window":
        import tolkien_llm
        return tolkien_llm.window_defects(spec)
    if kind in ("llm", "voltron"):                   # "voltron" is a legacy alias for kind "llm"
        import tolkien_llm
        return tolkien_llm.llm_defects(spec)
    if kind == "bench":
        import tolkien_llm
        return tolkien_llm.bench_defects(spec)
    if kind == "embed":                              # #34: price this worker's embed for the menu (rigby for now)
        import tolkien_llm
        return tolkien_llm.embed_defects(spec)
    if kind == "map.window":
        if not spec.get("name"):
            d.append("missing required field: name")
        arms = spec.get("arms")
        if not isinstance(arms, list) or not arms:
            d.append("map.window needs a non-empty arms list")
            return d
        names = [arm.get("name") for arm in arms if isinstance(arm, dict)]
        if len(names) != len(arms) or any(not name for name in names):
            d.append("every map.window arm needs a name")
        elif len(names) != len(set(names)):
            d.append("map.window arm names must be unique")
        total = 0
        for index, arm in enumerate(arms):
            if not isinstance(arm, dict):
                d.append("arm %d is not an object" % index)
                continue
            child = dict(arm)
            child["kind"] = "map"
            if arm.get("exclude_hosts") is not None:
                # The runner applies exclude_hosts to the WINDOW's host pool before arms are placed;
                # a per-arm copy would be validated and then silently ignored, which is worse than
                # a refusal.
                d.append("arm %s: exclude_hosts belongs on the window, not on an arm"
                         % arm.get("name", index))
            child_defects = spec_defects(child)
            d.extend("arm %s: %s" % (arm.get("name", index), defect)
                     for defect in child_defects)
            dw = arm.get("distinct_workers")
            if isinstance(dw, bool) or not isinstance(dw, int) or dw < 1:
                # execute_window places each arm on exactly this many tablets; a pull order is kind "map"
                d.append("arm %s: a map.window arm needs distinct_workers (a positive integer) - for a "
                         "pull order with no cohort, send kind \"map\"" % arm.get("name", index))
            else:
                total += dw
        if total > 10:
            d.append("map.window requests %d workers; maximum aarch64 window is 10" % total)
        return d
    if kind == "triage.extract":
        if not spec.get("name"):
            d.append("missing required field: name")
        docs = spec.get("docs")
        if not isinstance(docs, list) or not docs or not all(str(x).strip() for x in docs):
            d.append("triage.extract needs docs: a non-empty list of library-relative paths")
        return d
    if kind == "sweep":
        for k in ("name", "items_list", "payload", "src_host", "src_dir"):
            if not spec.get(k):
                d.append("missing required field: %s" % k)
    else:
        for k in ("name", "payload"):
            if not spec.get(k):
                d.append("missing required field: %s" % k)
        items = spec.get("items")
        if items is None and not spec.get("items_list"):
            d.append("map order needs items (inline list of strings) or items_list (file of lines)")
        if items is not None:
            if not isinstance(items, list) or not items:
                d.append("items must be a non-empty list of strings")
            else:
                bad = [x for x in items if not str(x).strip() or "|" in str(x) or "\n" in str(x)]
                if bad:
                    # "|" is the batch-key separator and newlines are the items-file separator;
                    # an item containing either would silently split into two different items.
                    d.append("%d item(s) empty or containing '|' or newline" % len(bad))
    ctl = spec.get("control") or {}
    if not ctl:
        d.append("NO POSITIVE CONTROL: without one, a broken detector and a genuinely clean input "
                 "produce the SAME output and the run cannot be told apart from a real negative")
    else:
        need = ("item", "expect") if kind == "map" else ("file", "src", "expect")
        for k in need:
            if not ctl.get(k):
                d.append("control missing %s" % k)
    if kind == "map" and isinstance(spec.get("items"), list):
        # Coverage counts results by item string, so a repeated item or a control equal to an item collapses
        # two rows into one and the order ships VOID with "1 unaccounted for" (card-proof.20261016-150444).
        clean = [str(x).strip() for x in spec["items"] if str(x).strip()]
        dupes = sorted({x for x in clean if clean.count(x) > 1})
        if dupes:
            d.append("items must be unique (coverage counts by item): repeated %s" % ", ".join(dupes[:5]))
        if ctl.get("item") and str(ctl["item"]).strip() in clean:
            d.append("the control item must be distinct from experiment items (%r is both)"
                     % str(ctl["item"]).strip())
    if spec.get("batch") and int(spec["batch"]) < 1:
        d.append("batch must be >= 1")
    support = spec.get("support_files")
    if support is not None:
        # files staged beside the payload on the node (the GPU factories' payloads import siblings from bin)
        if not isinstance(support, list) or not all(isinstance(x, str) and x.strip() for x in support):
            d.append("support_files must be a list of file paths")
        else:
            names = [os.path.basename(x) for x in support] + [os.path.basename(spec.get("payload") or "")]
            if len(set(names)) != len(names):
                d.append("support_files and the payload need distinct file names: %s" % names)
            missing = [x for x in support if not os.path.isfile(x)]
            if missing:
                d.append("support_files not found: %s" % missing)
    distinct = spec.get("distinct_workers")
    if distinct is not None:
        if kind != "map":
            d.append("distinct_workers is supported only for map orders")
        if isinstance(distinct, bool) or not isinstance(distinct, int) or distinct < 1:
            d.append("distinct_workers must be a positive integer")
        else:
            items = spec.get("items")
            if not isinstance(items, list):
                d.append("distinct_workers requires inline items")
            else:
                clean = [str(x).strip() for x in items if str(x).strip()]
                if len(clean) != distinct:
                    d.append("distinct_workers=%d requires exactly %d experiment items"
                             % (distinct, distinct))
                # unique items and a distinct control are checked for EVERY map order above
        if int(spec.get("batch") or BATCH) != 1:
            d.append("distinct_workers requires batch=1")
        if spec.get("sku_affinity"):
            d.append("distinct_workers and sku_affinity cannot be combined")
    excluded = spec.get("exclude_hosts")
    if excluded is not None:
        if (not isinstance(excluded, list)
                or not all(isinstance(x, str) and x.strip() for x in excluded)):
            d.append("exclude_hosts must be a list of node names")
        else:
            unknown = sorted(set(x.strip() for x in excluded) - set(TABLET_NET))
            if unknown:
                d.append("exclude_hosts names unknown node(s): %s" % ", ".join(unknown))
    return d


def order_priority(path):
    """An order's `priority` (higher runs first; default 0) -- the GPU factory's field and meaning.
    Fail-soft: unreadable = 0, so a bad field can never reorder the queue."""
    try:
        with open(path, encoding="utf-8") as f:
            return float(json.load(f).get("priority") or 0)
    except Exception:
        return 0.0


def queued():
    """Runnable orders, highest `priority` first, then by file name (the pre-priority order)."""
    try:
        paths = [os.path.join(QUEUE_DIR, f) for f in os.listdir(QUEUE_DIR)
                 if f.endswith(".tjob.json")]
    except Exception:
        return []
    return sorted(paths, key=lambda p: (-order_priority(p), p))


# A deploy's hold on new work (fleet_deploy_tolkien.hold_intake), from before its stop slips until after the swap: an order
# started in that stretch would die in the swap. A running order is not touched, and slips are still routed. The age limit
# outlasts the deploy's own stop wait (30 min), so a deploy that died cannot stop the factory for good.
DEPLOY_HOLD = os.path.join(INBOX, "DEPLOY_HOLD")
DEPLOY_HOLD_MAX_S = 3600


def intake_held(now=None):
    """True while a deploy holds intake. Could not ask = not held: the factory keeps working, as before this rule."""
    try:
        return (time.time() if now is None else now) - os.path.getmtime(DEPLOY_HOLD) <= DEPLOY_HOLD_MAX_S
    except OSError:
        return False


_INBOX_SEEN = {}


_INTAKE_LK = threading.Lock()
_INBOX_DOWN = {"since": None, "why": None}


def inbox_listing():
    """The inbox's file names, or None when the share cannot be read: an unreadable inbox is never an empty one.
    Logged once each way, and carried on the heartbeat so the watchdog (which does not mount the share) reports it."""
    try:
        names = os.listdir(INBOX)
    except Exception as e:                                           # noqa: BLE001
        if _INBOX_DOWN["since"] is None:
            _INBOX_DOWN.update(since=time.time(), why=type(e).__name__)
            log("INBOX UNREACHABLE (%s: %s) - no order or slip can arrive until the share is back"
                % (INBOX, type(e).__name__))
        return None
    if _INBOX_DOWN["since"] is not None:
        log("inbox reachable again after %ds" % (time.time() - _INBOX_DOWN["since"]))
        _INBOX_DOWN.update(since=None, why=None)
    return names


def drain_inbox():
    """Intake is single-writer: both lanes' stop watches and the fleet loop call this, one at a time."""
    with _INTAKE_LK:
        return _drain_inbox()


def _drain_inbox():
    """Move customer-dropped orders from the shared letter slot into the local queue.

    A file on a network share can be observed HALF-WRITTEN -- the writer is on another machine and
    nothing coordinates with it. So a spec that will not parse is not condemned immediately: it is
    given a grace period and retried, and only held once it has been unreadable long enough that
    "still being written" is no longer a credible explanation. Refusing a good order because we
    looked at it mid-copy would be a transient condition consuming work.

    Returns the number of orders accepted."""
    took = 0
    listing = inbox_listing()
    if listing is None:
        return 0
    names = [f for f in listing if f.endswith(".tjob.json")]
    for f in sorted(names):
        src = os.path.join(INBOX, f)
        try:
            with open(src, encoding="utf-8") as fh:
                json.load(fh)
        except Exception as e:
            first = _INBOX_SEEN.setdefault(f, time.time())
            if time.time() - first > 120:
                try:
                    os.replace(src, src + ".HELD")   # same directory, so this rename is safe
                    log("INBOX HELD %s: unreadable after 120s (%s)" % (f, type(e).__name__))
                except Exception:
                    pass
                _INBOX_SEEN.pop(f, None)
            continue
        try:
            os.makedirs(QUEUE_DIR, exist_ok=True)
            shutil.copy2(src, os.path.join(QUEUE_DIR, f))   # copy, never rename: EXDEV across CIFS
            os.remove(src)
            _INBOX_SEEN.pop(f, None)
            log("accepted %s from the inbox" % f)
            took += 1
        except Exception as e:
            log("inbox intake failed for %s (%s) - will retry" % (f, type(e).__name__))
    return took


def ship_to_outbox(*paths):
    """SHIPPING. Deliverables leave the building onto shared storage.

    Fail-soft: the authoritative copy is already written to the local results dir, so a share that
    is momentarily unavailable must not turn a finished order into a failed one."""
    shipped = []
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        try:
            os.makedirs(OUTBOX, exist_ok=True)
            dst = os.path.join(OUTBOX, os.path.basename(p))
            shutil.copy2(p, dst)
            shipped.append(dst)
        except Exception as e:
            log("outbox delivery failed for %s (%s) - local copy stands"
                % (os.path.basename(p), type(e).__name__))
    return shipped


# ── CONTROL SLIPS ────────────────────────────────────────────────────────────────────────────────
# The GPU factory's customer vocabulary (p20_foundry.py `<order>.stop.json` + `*.ctl.json`: fields
# action / order / why / value), for a parallel farm. Slips come through the INBOX, the customer's
# only door, and never enter the queue: drain_inbox() and queued() match `.tjob.json` only.
#
#   <order>.stop.json                         stop that order (queued / held / RUNNING)
#   <order>.ctl.json {"action": ...}          hold | release | cancel (= stop) | priority | requeue
#   either, with {"prefix": "<run-id>"}       every order whose queue name starts with it (>= 8 chars)
#                                             (not requeue: it names exactly one archived order)
#   optional: "why" (cause, quoted in the DM), "value" (priority), "dry_run": true
#
# A slip that names the RUNNING order is not consumed by the router: the runner's StopWatch sees it,
# stops dealing, ships what finished as `stopped: true`, and then routes it with `finished=`.
STOP_SUFFIX = ".stop.json"
CTL_SUFFIX = ".ctl.json"
ORDER_SUFFIX = ".tjob.json"
HELD_SUFFIX = ".tjob.json.HELD"
CTL_ACTIONS = ("hold", "release", "cancel", "priority", "requeue")
MIN_PREFIX = 8
SLIP_GRACE_S = 120.0
_SLIP_SEEN = {}
_SLIP_LOCK = threading.RLock()
_RECENT_RETIRED = {}


def _stamp_now():
    return time.strftime("%Y%m%d-%H%M%S")


def order_stem(path_or_name):
    """An order's queue identity: the file name minus `.tjob.json` / `.tjob.json.HELD`."""
    b = os.path.basename(path_or_name)
    for suf in (HELD_SUFFIX, ORDER_SUFFIX):
        if b.endswith(suf):
            return b[:-len(suf)]
    return b


def note_retired(stem, results=""):
    """Remember recent retirements so a stop that arrives just too late says so, not 'unknown'."""
    _RECENT_RETIRED[stem] = (now(), os.path.basename(results or ""))
    if len(_RECENT_RETIRED) > 200:
        for k in list(_RECENT_RETIRED)[:-200]:
            _RECENT_RETIRED.pop(k, None)


def parse_slip(fname, body):
    """(slip, refusal). The ONE reading of what a slip asks for, shared by the router and the
    running order's StopWatch so the two cannot disagree about which order a slip names."""
    if not isinstance(body, dict):
        return None, "slip body is not a JSON object"
    if fname.endswith(STOP_SUFFIX):
        action, stem = "stop", fname[:-len(STOP_SUFFIX)]
    elif fname.endswith(CTL_SUFFIX):
        action, stem = str(body.get("action") or "").strip().lower(), fname[:-len(CTL_SUFFIX)]
        if action not in CTL_ACTIONS:
            return None, "unknown action %r (%s)" % (action, "|".join(CTL_ACTIONS))
    else:
        return None, "not a slip file"
    prefix = target = None
    if "prefix" in body and action == "requeue":
        return None, "requeue names exactly one order (file name or \"order\"), never a prefix"
    if "prefix" in body:
        prefix = str(body.get("prefix") or "").strip()
        if len(prefix) < MIN_PREFIX:
            return None, ("prefix %r is empty or shorter than %d characters - refusing an "
                          "over-broad match" % (prefix, MIN_PREFIX))
    else:
        target = str(body.get("order") or body.get("target") or stem).strip()
        if not target:
            return None, "no order named (file name, \"order\", or \"prefix\")"
    value = body.get("value")
    if action == "priority" and value is not None:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None, "priority value %r is not a number" % (value,)
    why = str(body.get("why") or body.get("cause") or "").strip()[:300] or "no cause given"
    return {"slip": fname, "action": action, "stops": action in ("stop", "cancel"),
            "target": target, "prefix": prefix, "why": why, "value": value,
            "dry_run": bool(body.get("dry_run")),
            # a PAUSE: whoever stopped it requeues it (a deploy, rigby's reservation), so its stop is not its finish
            "pause": bool(body.get("pause"))}, ""


def slip_matches(slip, stem, name=""):
    names = [n for n in (stem, name) if n]
    if slip["prefix"] is not None:
        return any(n.startswith(slip["prefix"]) for n in names)
    return slip["target"] in names


def _inbox_slips():
    listing = inbox_listing()
    if listing is None:
        return []
    names = [f for f in listing if f.endswith((STOP_SUFFIX, CTL_SUFFIX))]

    def mtime(f):
        try:
            return os.path.getmtime(os.path.join(INBOX, f))
        except OSError:
            return 0.0
    return sorted(names, key=lambda f: (mtime(f), f))


def _read_slip(fname):
    """(body, error). Never raises."""
    try:
        with open(os.path.join(INBOX, fname), encoding="utf-8") as fh:
            return json.load(fh), ""
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:120])


def find_stop_slip(stem, name=""):
    """The first readable, non-dry-run slip in the INBOX that asks to stop THIS order, or None.
    Read-only. An unreadable slip is skipped: it must not stop the wrong run."""
    for fname in _inbox_slips():
        body, err = _read_slip(fname)
        if err:
            continue
        slip, refusal = parse_slip(fname, body)
        if refusal or not slip["stops"] or slip["dry_run"]:
            continue
        if slip_matches(slip, stem, name):
            return slip
    return None


def _archive_slip(fname, body, outcome, record):
    """Retire a routed slip into ARCHIVE_DIR as `<slip>.<outcome>-<stamp>`. Copy + remove: the
    inbox is a CIFS mount and the archive is local disk (EXDEV)."""
    src = os.path.join(INBOX, fname)
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        dst = os.path.join(ARCHIVE_DIR, "%s.%s-%s" % (fname, outcome, _stamp_now()))
        n = 1
        while os.path.exists(dst):
            n += 1
            dst = os.path.join(ARCHIVE_DIR, "%s.%s-%s-%d" % (fname, outcome, _stamp_now(), n))
        if isinstance(body, dict):
            out = dict(body)
            out["_routed"] = record
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=1)
        else:
            shutil.copy2(src, dst)
        os.remove(src)
        return dst
    except Exception as e:
        log("slip archive failed for %s (%s) - removing it so it cannot fire twice"
            % (fname, type(e).__name__))
        try:
            os.remove(src)
        except Exception:
            pass
        return None


# Rigby lane reservation: a run with helper 'rigby' reserves the lane via `<any>.lane.json` instead of manual pausing.
# Reserved: current rigby order is stopped (checkpoint kept); new orders blocked unless spec has "reservation": "<owner order>".
# Released by owner's slip or automatically when owner's desk record ends (done/failed/cancelled/expired). Stopped orders requeue.
# Fail-safe: if desk status is unknown, reservation persists.
LANE_SUFFIX = ".lane.json"
RESERVATION = os.path.join(STATE_DIR, "rigby_reservation.json")
RIGBY_RUNNING = [""]                     # the rigby lane's running order stem (set by tablet_runner.rigby_lane)
DESK_ENDED = ("done", "failed", "cancelled", "expired")
_RES_LOCK = threading.RLock()
_RES_DESK_WARNED = [0.0]


def read_reservation():
    """The active reservation, or None. Unreadable is reported and treated as NONE only after logging: a corrupt file
    must not freeze rigby forever, and saying so is how a human notices."""
    try:
        with open(RESERVATION, encoding="utf-8") as f:
            r = json.load(f)
        return r if isinstance(r, dict) and r.get("by") else None
    except FileNotFoundError:
        return None
    except Exception as e:                                           # noqa: BLE001
        log("rigby reservation unreadable (%s) - rigby lane runs unreserved" % type(e).__name__)
        return None


def _write_reservation(r):
    tmp = RESERVATION + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, indent=1)
    os.replace(tmp, RESERVATION)


def reservation_admits(spec, res=None):
    """True when a rigby-lane order may start: no reservation, or the order belongs to it."""
    res = read_reservation() if res is None else res
    return res is None or (isinstance(spec, dict) and spec.get("reservation") == res["by"])


def _queued_spec(stem):
    """The queued spec for an order stem, or None (unreadable = unknown, which admits nothing)."""
    try:
        with open(os.path.join(QUEUE_DIR, stem + ".tjob.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                                  # noqa: BLE001
        return None


def _drop_slip(name, body):
    tmp = os.path.join(INBOX, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f)
    os.replace(tmp, os.path.join(INBOX, name))


REQUEUE_PENDING = os.path.join(STATE_DIR, "rigby_requeue_pending.json")


def _pending():
    try:
        with open(REQUEUE_PENDING, encoding="utf-8") as f:
            p = json.load(f)
        return p if isinstance(p, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:                                           # noqa: BLE001
        log("rigby requeue list unreadable (%s) - pending requeues need a hand" % type(e).__name__)
        return {}


def _write_pending(p):
    if not p:
        try:
            os.remove(REQUEUE_PENDING)
        except FileNotFoundError:
            pass
        return
    tmp = REQUEUE_PENDING + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=1)
    os.replace(tmp, REQUEUE_PENDING)


def flush_pending_requeues():
    """Requeue pending orders only when their reservation has retired; requeueing a live reservation is refused. Never raises."""
    with _RES_LOCK:
        pend = _pending()
        for stem, why in list(pend.items()):
            if RIGBY_RUNNING[0] == stem:
                continue
            if os.path.exists(os.path.join(QUEUE_DIR, stem + ORDER_SUFFIX)):
                pend.pop(stem)
                log("rigby reservation: %s is queued again - nothing to requeue" % stem)
                continue
            try:
                retired = any(f.startswith(stem + ORDER_SUFFIX + ".stopped-") for f in os.listdir(ARCHIVE_DIR))
            except OSError:
                retired = False
            if retired:
                try:
                    _drop_slip(stem + CTL_SUFFIX, {"action": "requeue", "why": why})
                    pend.pop(stem)
                    log("rigby reservation: requeue sent for %s (retired)" % stem)
                except Exception as e:                               # noqa: BLE001
                    log("rigby reservation: requeue slip for %s not written (%s) - retried" % (stem, e))
        _write_pending(pend)


def release_reservation(why):
    """Lift the reservation; every order it stopped is requeued once it has retired (flush_pending_requeues)."""
    with _RES_LOCK:
        res = read_reservation()
        if res is None:
            return False
        pend = _pending()
        for stem in res.get("stopped") or []:
            pend[stem] = "rigby reservation by %s ended: %s" % (res["by"], why)
        try:
            _write_pending(pend)
            os.remove(RESERVATION)
        except FileNotFoundError:
            pass
        except Exception as e:                                       # noqa: BLE001
            log("rigby reservation: could not clear (%s)" % type(e).__name__)
            return False
    log("RIGBY RESERVATION RELEASED (%s): %s; to requeue once retired: %s"
        % (res["by"], why, res.get("stopped") or "none"))
    notify_desk("rigby reservation by %s released: %s" % (res["by"], why))
    flush_pending_requeues()
    return True


def process_lane_slips():
    """Route every `*.lane.json` in the INBOX. Never raises."""
    listing = inbox_listing() or []
    for fname in sorted(f for f in listing if f.endswith(LANE_SUFFIX)):
        body, err = _read_slip(fname)
        if err:
            continue                                         # being written: read next pass
        action, lane, by = (str((body or {}).get(k) or "") for k in ("action", "lane", "by"))
        if lane != "rigby" or not by or action not in ("reserve", "release"):
            _archive_slip(fname, body, "refused", {"at": now(), "why": "needs lane=rigby, by, reserve|release"})
            continue
        with _RES_LOCK:
            res = read_reservation()
            if action == "reserve":
                if res and res["by"] != by:
                    _archive_slip(fname, body, "refused", {"at": now(), "why": "rigby already reserved by " + res["by"]})
                    notify_desk("REFUSED rigby reservation for %s: already reserved by %s" % (by, res["by"]))
                    continue
                res = res or {"by": by, "owner_id": body.get("owner_id"), "why": body.get("why"), "at": now(),
                              "stopped": []}
                running = RIGBY_RUNNING[0]
                # the owner's own running piece is admitted, never stopped:
                # a respawned arm re-sending its reservation must not cancel the job it is waiting on.
                if running and running not in res["stopped"] and running != by \
                        and not reservation_admits(_queued_spec(running), res):
                    # a pause: release_reservation requeues it. Not a window: Kirk reopens a parked window himself
                    _drop_slip(running + STOP_SUFFIX, {"why": "rigby reserved for %s" % by,
                                                       "pause": not running.startswith("hai-win-")})
                    res["stopped"].append(running)
                _write_reservation(res)
                _archive_slip(fname, body, "applied", {"at": now(), "stopped": res["stopped"]})
                log("RIGBY RESERVED for %s (desk %s): stopped %s; only its pieces start until it ends"
                    % (by, res.get("owner_id"), res["stopped"] or "nothing"))
                notify_desk("rigby reserved for %s; stopped %s" % (by, res["stopped"] or "nothing"))
            else:
                _archive_slip(fname, body, "applied", {"at": now()})
                if res and res["by"] == by:
                    release_reservation(str(body.get("why") or "released by its owner"))


def check_reservation_owner(desk_call=None):
    """Release the reservation only if the owner's status is ENDED. On query failure, retain the reservation and log a warning (throttled to 10 min). Never raises."""
    res = read_reservation()
    if res is None or not res.get("owner_id"):
        return None
    try:
        status = str(((desk_call or _desk_call)("/p20/order/%s" % res["owner_id"]) or {}).get("status") or "")
    except Exception as e:                                           # noqa: BLE001
        if time.time() - _RES_DESK_WARNED[0] > 600:
            _RES_DESK_WARNED[0] = time.time()
            log("rigby reservation by %s: could not ask the desk (%s) - KEPT" % (res["by"], type(e).__name__))
        return None
    if status in DESK_ENDED:
        # Desk records are claims about the past (e.g., 'failed'); live factory state is the current truth.
        # If a factory reports a run for this owner, it must not be marked as ended, even if the desk record says otherwise.
        try:
            live = (desk_call or _desk_call)("/p20/factories") or {}
            hosts = [h for h, v in (live.get("factories") or {}).items()
                     if str(((v or {}).get("run") or {}).get("name") or "") == res["by"]]
        except Exception as e:                                       # noqa: BLE001
            if time.time() - _RES_DESK_WARNED[0] > 600:
                _RES_DESK_WARNED[0] = time.time()
                log("rigby reservation by %s: record says %s but the run-state could not be read (%s) - KEPT"
                    % (res["by"], status, type(e).__name__))
            return None
        if hosts:
            if time.time() - _RES_DESK_WARNED[0] > 600:
                _RES_DESK_WARNED[0] = time.time()
                log("rigby reservation by %s: desk record says %s but %s names it RUNNING - KEPT (a requeued "
                    "order keeps its old record)" % (res["by"], status, ", ".join(hosts)))
            return None
        release_reservation("its owner %s ended (%s)" % (res["by"], status))
        return status
    return None


def lane_housekeeping():
    """Reservation slips + owner check, called on every routing pass of both lanes. Never raises."""
    for fn in (process_lane_slips, check_reservation_owner, flush_pending_requeues):
        try:
            fn()
        except Exception as e:                                       # noqa: BLE001
            log("rigby reservation housekeeping error (%s: %s)" % (type(e).__name__, e))


def archive_stopped_spec(path, stem, record):
    """Retire a spec that a slip stopped: ARCHIVE_DIR/<stem>.tjob.json.stopped-<stamp>, with the
    stop recorded inside it as `_stopped`. Returns (archived_path, spec_or_None)."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    dst = os.path.join(ARCHIVE_DIR, "%s.tjob.json.stopped-%s" % (stem, _stamp_now()))
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = os.path.join(ARCHIVE_DIR, "%s.tjob.json.stopped-%s-%d" % (stem, _stamp_now(), n))
    spec = None
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except Exception:
        spec = None
    if isinstance(spec, dict):
        rec = dict(spec)
        rec["_stopped"] = record
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1)
        os.remove(path)
    else:
        os.replace(path, dst)
    return dst, (spec if isinstance(spec, dict) else None)


def _queue_entries():
    """[(stem, state, path)] for every queued (`.tjob.json`) and held (`.tjob.json.HELD`) order."""
    out = []
    try:
        names = os.listdir(QUEUE_DIR)
    except Exception:
        return out
    for f in sorted(names):
        if f.endswith(HELD_SUFFIX):
            out.append((order_stem(f), "held", os.path.join(QUEUE_DIR, f)))
        elif f.endswith(ORDER_SUFFIX):
            out.append((order_stem(f), "queued", os.path.join(QUEUE_DIR, f)))
    return out


def _apply_slip(slip, stem, state, path):
    """Apply one slip to one queued/held order. Returns (did_something, note)."""
    a = slip["action"]
    if slip["stops"]:
        dst, spec = archive_stopped_spec(path, stem, {
            "at": now(), "state": state, "cause": slip["why"], "slip": slip["slip"]})
        if spec is not None:
            report_fleet_result(spec, False, "stopped by the customer: %s" % slip["why"])
        log("STOP %s (%s) -> %s" % (stem, state, os.path.basename(dst)))
        return True, "stopped"
    if a == "hold":
        if state == "held":
            return False, "already held"
        dst = path + ".HELD"
        if os.path.exists(dst):
            return False, "a held order with this name already exists"
        os.replace(path, dst)
        log("HOLD %s: parked as %s (%s)" % (stem, os.path.basename(dst), slip["why"]))
        return True, "held"
    if a == "release":
        if state == "queued":
            return False, "already queued"
        dst = os.path.join(QUEUE_DIR, stem + ORDER_SUFFIX)
        if os.path.exists(dst):
            return False, "a queued order with this name already exists"
        os.replace(path, dst)
        log("RELEASE %s: re-admitted to the queue (%s)" % (stem, slip["why"]))
        return True, "released"
    if a == "priority":
        if state == "held":
            return False, "held (release it first)"
        with open(path, encoding="utf-8") as f:
            body = json.load(f)
        if slip["value"] is not None:
            val = slip["value"]
        else:
            others = [order_priority(p) for p in queued() if p != path]
            val = (max(others) if others else 0.0) + 1.0
        body["priority"] = val
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f, indent=1)
        os.replace(tmp, path)
        log("PRIORITY %s -> %s" % (stem, val))
        return True, "priority %g" % val
    return False, "unknown action"


_VERB = {"stop": "STOPPED", "cancel": "STOPPED", "hold": "HELD", "release": "RELEASED",
         "priority": "PRIORITY", "requeue": "REQUEUED"}


def newest_archived_spec(stem):
    """The newest archived generation of an order's spec (`<stem>.tjob.json.<stamp>` or `.stopped-<stamp>`), by
    mtime, or None. Every generation keeps its own name; the reader picks the newest explicitly."""
    try:
        names = [f for f in os.listdir(ARCHIVE_DIR) if f.startswith(stem + ORDER_SUFFIX + ".")]
    except OSError:
        return None
    paths = [os.path.join(ARCHIVE_DIR, f) for f in names]
    return max(paths, key=os.path.getmtime) if paths else None


def requeue_order(slip, busy_stems):
    """Put an archived order back in the queue under its own name, so it runs again and resumes from its checkpoint
    (an `llm` order skips every answer already there). Returns (did, note). The archived spec stays where it is."""
    stem = slip["target"]
    if stem in busy_stems:
        return False, "it is running now"
    if any(s == stem for s, _st, _p in _queue_entries()):
        return False, "it is already queued or held"
    src = newest_archived_spec(stem)
    if src is None:
        return False, "no archived spec named %s%s.*" % (stem, ORDER_SUFFIX)
    with open(src, encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, dict):
        return False, "archived spec %s is not a JSON object" % os.path.basename(src)
    spec.pop("_stopped", None)
    spec["_requeued"] = {"at": now(), "from": os.path.basename(src), "why": slip["why"], "slip": slip["slip"]}
    dst = os.path.join(QUEUE_DIR, stem + ORDER_SUFFIX)
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1)
    os.replace(tmp, dst)
    ckpt = os.path.join(STATE_DIR, "%s.checkpoint.jsonl" % spec.get("name", stem))
    rows = 0
    try:
        with open(ckpt, encoding="utf-8") as f:
            rows = sum(1 for line in f if line.strip())
    except OSError:
        pass
    log("REQUEUE %s from %s (%s); checkpoint rows: %d" % (stem, os.path.basename(src), slip["why"], rows))
    return True, "from %s, checkpoint %d row(s)" % (os.path.basename(src), rows)


def _slip_message(slip, done, skipped, finished=None):
    """ONE DM per slip. done/skipped: [(stem, state, note)]."""
    verb = _VERB[slip["action"]]
    sel = ("prefix %s" % slip["prefix"]) if slip["prefix"] is not None else slip["target"]
    tail = ""
    if finished:
        tail = (" -- shipped %d finished row(s) flagged stopped:true -> %s"
                % (finished.get("rows", 0), finished.get("results") or "?"))
    if slip["prefix"] is None and len(done) == 1 and not skipped:
        stem, state, note = done[0]
        if slip["action"] == "priority":
            return "%s %s -> %s (front of the queue): %s" % (verb, stem, note.split()[-1],
                                                              slip["why"])
        return "%s %s (%s): %s%s" % (verb, stem, state, slip["why"], tail)
    counts = {}
    for _stem, state, _note in done:
        counts[state] = counts.get(state, 0) + 1
    parts = ", ".join("%d %s" % (counts[s], s) for s in ("running", "queued", "held")
                      if counts.get(s))
    msg = "%s %s: %d order(s)%s: %s%s" % (verb, sel, len(done),
                                         (" (%s)" % parts) if parts else "", slip["why"], tail)
    if skipped:
        notes = {}
        for stem, _state, note in skipped:
            notes.setdefault(note, []).append(stem)
        msg += " | not changed: " + "; ".join(
            "%d %s (%s%s)" % (len(v), k, ", ".join(v[:3]), ", ..." if len(v) > 3 else "")
            for k, v in notes.items())
    return msg


def process_slips(running=None, finished=None):
    """Route every stop / control slip in the INBOX. Returns the slip that asks to stop `running`
    (left in the inbox for the runner), or None.

    running:  {"stem", "name"} of the order the runner is working right now, or None when idle -- or a LIST of
              them, one per lane: then the return is {stem: stop slip} for every running order a slip stops,
              and no running order is ever treated as queued by a slip routed from the other lane.
    finished: {"slip", "stem", "name", "results", "rows"} -- a running order that slip stopped,
              now retired; that slip is routed now and its DM covers the running order too."""
    as_list = isinstance(running, list)
    runs = [r for r in (running if as_list else [running]) if r]
    hits = {}
    with _SLIP_LOCK:
        for fname in _inbox_slips():
            body, err = _read_slip(fname)
            if err:
                first = _SLIP_SEEN.setdefault(fname, time.time())
                if time.time() - first > SLIP_GRACE_S:
                    _SLIP_SEEN.pop(fname, None)
                    log("SLIP %s unreadable after %.0fs (%s) - archived, nothing changed"
                        % (fname, SLIP_GRACE_S, err))
                    _archive_slip(fname, None, "unreadable", {"at": now(), "error": err})
                    notify_desk("REFUSED %s: unreadable after %.0fs (%s) - nothing changed"
                                % (fname, SLIP_GRACE_S, err))
                continue
            _SLIP_SEEN.pop(fname, None)
            slip, refusal = parse_slip(fname, body)
            if refusal:
                log("SLIP %s REFUSED: %s" % (fname, refusal))
                _archive_slip(fname, body, "refused", {"at": now(), "refused": refusal})
                notify_desk("REFUSED %s: %s - nothing changed" % (fname, refusal))
                continue
            if slip["action"] == "requeue":
                busy = {r.get("stem") for r in runs} | {r.get("name") for r in runs}
                if slip["dry_run"]:
                    src = newest_archived_spec(slip["target"])
                    note = ("would requeue from %s" % os.path.basename(src)) if src else "no archived spec"
                    _archive_slip(fname, body, "dryrun", {"at": now(), "note": note})
                    notify_desk("DRY-RUN requeue %s: %s - nothing changed" % (slip["target"], note))
                    continue
                try:
                    did, note = requeue_order(slip, busy)
                except Exception as e:                                   # noqa: BLE001
                    did, note = False, "failed: %s" % type(e).__name__
                    log("SLIP %s requeue FAILED (%r)" % (fname, e))
                _archive_slip(fname, body, "done" if did else "refused", {"at": now(), "requeued": did, "note": note})
                notify_desk(("REQUEUED %s (%s): %s" % (slip["target"], note, slip["why"])) if did else
                            ("REQUEUE %s: %s - nothing changed" % (slip["target"], note)))
                continue
            fin = finished if (finished and finished.get("slip") == fname) else None
            run_hits = [r for r in runs if slip_matches(slip, r.get("stem", ""), r.get("name", ""))]
            run_hit = bool(run_hits)
            if run_hit and slip["stops"] and not slip["dry_run"]:
                for r in run_hits:
                    hits.setdefault(r["stem"], slip)
                continue
            running_stems = {r.get("stem") for r in runs}
            entries = [(s, st, p) for s, st, p in _queue_entries()
                       if s not in running_stems and slip_matches(slip, s)]
            done, skipped = [], []
            if fin:
                done.append((fin.get("stem") or "?", "running", "stopped"))
            for r in run_hits:
                skipped.append((r["stem"], "running",
                                "running - a %s applies to queued orders; stop it with %s%s"
                                % (slip["action"], r["stem"], STOP_SUFFIX)))
            sel = ("prefix %s" % slip["prefix"]) if slip["prefix"] is not None else slip["target"]
            if not entries and not fin and not run_hit:
                recent = (_RECENT_RETIRED.get(slip["target"])
                          if slip["target"] is not None else None)
                why = ("already finished (retired %s -> %s)" % recent if recent
                       else "no queued, running or held order matches")
                log("SLIP %s: %s %s - %s; slip archived" % (fname, slip["action"], sel, why))
                _archive_slip(fname, body, "unknown", {"at": now(), "outcome": why})
                notify_desk("%s %s: %s - nothing changed" % (slip["action"].upper(), sel, why))
                continue
            if slip["dry_run"]:
                n = {}
                for _s, st, _p in entries:
                    n[st] = n.get(st, 0) + 1
                desc = ", ".join("%d %s" % (v, k) for k, v in sorted(n.items())) or "none"
                names = [s for s, _st, _p in entries]
                log("SLIP %s DRY-RUN %s %s: would affect %d order(s) (%s)%s: %s"
                    % (fname, slip["action"], sel, len(entries), desc,
                       " + the RUNNING order" if run_hit else "", ", ".join(names)))
                _archive_slip(fname, body, "dryrun", {"at": now(), "would_affect": names,
                                                      "running": bool(run_hit)})
                notify_desk("DRY-RUN %s %s: would affect %d order(s) (%s)%s - nothing changed"
                            % (slip["action"], sel, len(entries), desc,
                               " + the running order" if run_hit else ""))
                continue
            for stem, state, path in entries:
                try:
                    did, note = _apply_slip(slip, stem, state, path)
                except Exception as e:
                    did, note = False, "failed: %s" % type(e).__name__
                    log("SLIP %s on %s FAILED (%r)" % (fname, stem, e))
                (done if did else skipped).append((stem, state, note))
            _archive_slip(fname, body, "done", {
                "at": now(), "done": [[s, st, n] for s, st, n in done],
                "skipped": [[s, st, n] for s, st, n in skipped], "finished": fin})
            if not done:
                msg = "%s %s: nothing changed | %s" % (
                    _VERB[slip["action"]], sel,
                    "; ".join("%s (%s)" % (s, n) for s, _st, n in skipped[:5]))
            else:
                msg = _slip_message(slip, done, skipped, fin)
            notify_desk(msg)
    if as_list:
        return hits
    return next(iter(hits.values()), None)


INSTANCE = "%s-%d-%d" % (socket.gethostname(), os.getpid(), int(time.time()))


def acquire_singleton(stale_after=90.0):
    """Single-instance guard for order takers.

    Identity is derived from the `INSTANCE` string (host+pid+timestamp),
    not PID alone, because PID is unreliable in containers (always 1).
    Liveness is determined by a recent heartbeat written by the running
    process. A lock with a stale heartbeat is considered dead, even if
    the recorded instance ID matches an old container."""
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        if os.path.exists(RUNNER_LOCK):
            with open(RUNNER_LOCK, encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("instance") != INSTANCE:
                beat_ts = 0.0
                try:
                    with open(HEARTBEAT, encoding="utf-8") as f:
                        beat_ts = float(json.load(f).get("ts", 0))
                except Exception:
                    pass
                # the LIVE signal is the heartbeat; fall back to the lock's own stamp if absent
                fresh = max(beat_ts, float(rec.get("ts", 0)))
                age = time.time() - fresh
                if rec.get("released") and beat_ts <= float(rec["released"]) + 1.0:
                    # If a beat arrived after release, the holder is still running; the staleness check below prevents taking over.
                    log("previous instance %s released the lock %.0fs ago on a clean stop - taking over"
                        % (rec.get("instance"), time.time() - float(rec["released"])))
                elif age < stale_after:
                    log("another order taker is live (heartbeat %.0fs ago, instance %s) - waiting "
                        "for the lock; will take over if it goes quiet for %.0fs"
                        % (age, rec.get("instance"), stale_after))
                    return False
                else:
                    log("previous instance %s went quiet %.0fs ago - taking over"
                        % (rec.get("instance"), age))
        with open(RUNNER_LOCK, "w", encoding="utf-8") as f:
            json.dump({"instance": INSTANCE, "pid": os.getpid(), "ts": time.time(),
                       "host": socket.gethostname()}, f)
        return True
    except Exception as e:
        log("could not acquire singleton (%s)" % type(e).__name__)
        return False


def release_singleton():
    """On a clean stop: if the lock is THIS instance's, mark it released, so the next order taker takes over at once
    instead of waiting out `stale_after` of heartbeat silence. Only the holder releases, and only on its way out
    (install_stop exits straight after). Never raises - it runs in the stop path. -> True when it released."""
    try:
        with open(RUNNER_LOCK, encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("instance") != INSTANCE:
            return False
        rec["released"] = time.time()
        tmp = RUNNER_LOCK + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        os.replace(tmp, RUNNER_LOCK)
        return True
    except Exception:                                                # noqa: BLE001 - a stop path never raises
        return False


def install_stop(on_stop=None, exit_fn=None):
    """SIGTERM -> `on_stop()` (never raising) -> exit at once. Every Tolkien container runs its Python as PID 1, which
    IGNORES SIGTERM without a handler, so `docker compose` stop waited its full 10 s grace and then SIGKILLed - on every
    deploy, for every container. os._exit, not sys.exit: a non-daemon thread must not keep a stopped order taker alive
    after it has released its lock (that would be two order takers)."""
    import signal

    def _stop(signum, frame):
        if on_stop:
            try:
                on_stop()
            except Exception:                                        # noqa: BLE001
                pass
        try:
            sys.stdout.flush()
        except Exception:                                            # noqa: BLE001
            pass
        (exit_fn or os._exit)(0)
    signal.signal(signal.SIGTERM, _stop)
    return _stop


def beat(phase=""):
    try:
        with open(HEARTBEAT, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "iso": now(), "pid": os.getpid(), "phase": phase,
                       "inbox_down_since": _INBOX_DOWN["since"], "inbox_error": _INBOX_DOWN["why"]}, f)
    except Exception:
        pass


# `tablet_foundry.py` acts as both the head (orchestrator) and the fellow (agent)
# via `--agent`. Shared code prevents registry/logging drift between roles.
#
# Resident agent avoids per-batch SSH handshakes and Python startup/model reload costs.
#
# Health is pulled over HTTP (~2ms), not pushed. Push requires a defined
# `/fleet/run-state` contract to avoid payload mismatches.

import importlib.util                                                            # noqa: E402
import hmac                                                                      # noqa: E402
import io                                                                        # noqa: E402
import urllib.error                                                              # noqa: E402
import urllib.request                                                            # noqa: E402

FELLOW_PORT = int(os.environ.get("TABLET_FELLOW_PORT", "8730"))
FELLOW_TIMEOUT = float(os.environ.get("TABLET_FELLOW_TIMEOUT", "30"))
FELLOW_TOKEN_PATH = os.environ.get(
    "TABLET_FELLOW_TOKEN_FILE",
    os.path.join(os.path.expanduser("~"), ".fellow_token"))
FELLOW_STARTED = time.time()

_FELLOW_STATS = {"served": 0, "failed": 0, "busy": 0, "last_error": "", "last_task": ""}
_FELLOW_LOCK = threading.Lock()      # one unit of work at a time; see fellow_serve() docstring
_FELLOW_HANDLERS = {}


def fellow_token():
    """Return the shared secret token.

    Env var ``TABLET_FELLOW_TOKEN`` takes precedence over ``FELLOW_TOKEN_PATH``.
    Returns empty string if no token is found. An empty token prevents
    ``fellow_serve()`` from starting to avoid unauthenticated remote execution."""
    t = os.environ.get("TABLET_FELLOW_TOKEN", "").strip()
    if t:
        return t
    try:
        with open(FELLOW_TOKEN_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def fellow_lan_ip():
    """Return this device's LAN address.

    Avoid `getaddrinfo(gethostname())` which returns localhost on Android.
    Uses a dummy UDP connect to determine the source IP from the routing table;
    no packet is sent. The target is the LAN_ROUTE_PROBE setting (an address on the LAN, such as its router);
    unset means this device does not look its address up and returns "".
    """
    target = os.environ.get("LAN_ROUTE_PROBE", "")
    if not target:
        return ""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.connect((target, 9))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def fellow_name():
    """Which member of the Fellowship am I? Explicit env, then the registry, then hostname.

    The launcher sets TABLET_FELLOW_NAME, so the registry path is a fallback -- but it must still
    work, because a fellow started by hand with no env is exactly when a wrong name is confusing."""
    n = os.environ.get("TABLET_FELLOW_NAME", "").strip()
    if n:
        return n
    ip = fellow_lan_ip()
    if ip:
        for name, (known_ip, _serial) in TABLET_NET.items():
            if known_ip == ip:
                return name
    return socket.gethostname()


def fellow_handler(name):
    """Register a task. The set of registered names IS the agent's advertised capability list."""
    def deco(fn):
        _FELLOW_HANDLERS[name] = fn
        return fn
    return deco


# ── AGENT SIDE: tasks ────────────────────────────────────────────────────────────────────────────

@fellow_handler("ping")
def _fellow_ping(req):
    """Round-trip probe. Echoes back so a caller can measure transport cost with no work in it."""
    return {"pong": req.get("echo", ""), "t": time.time()}


_PAYLOAD_CACHE = {"key": None, "mod": None}


def _fellow_load_payload(path):
    """Import the payload module once and cache it.

    The cache is keyed on a fingerprint (absolute path, mtime, size) rather than
    path alone to ensure redeployed files with updated content are re-imported,
    avoiding stale module state from previous deployments."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    if _PAYLOAD_CACHE["key"] == key:
        return _PAYLOAD_CACHE["mod"], False
    spec = importlib.util.spec_from_file_location("fellow_payload", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _PAYLOAD_CACHE["key"], _PAYLOAD_CACHE["mod"] = key, mod
    return mod, True


@fellow_handler("payload")
def _fellow_payload(req):
    """Run a staged payload against items, IN PROCESS.

    Deliberately honours the EXISTING contract -- `main(items_path, out_path)`, the same one
    `tablet_fulfil` invokes as `python payload items.txt out.json`. Payloads therefore need no
    change to work here, and there is no second payload interface to keep in step."""
    path = req.get("payload") or ""
    items = req.get("items") or []
    if not os.path.exists(path):
        raise RuntimeError("payload not staged on this node: %s" % path)
    mod, reloaded = _fellow_load_payload(path)
    if not hasattr(mod, "main"):
        raise RuntimeError("payload %s has no main(items_path, out_path)" % os.path.basename(path))

    stage = os.path.dirname(os.path.abspath(path)) or "."
    tag = "%d_%d" % (os.getpid(), int(time.time() * 1000) % 10000000)
    ipath = os.path.join(stage, "fellow_items_%s.txt" % tag)
    opath = os.path.join(stage, "fellow_out_%s.json" % tag)
    try:
        with open(ipath, "w", encoding="utf-8") as f:
            f.write("\n".join(str(x) for x in items) + "\n")
        t0 = time.time()
        mod.main(ipath, opath)
        dt = time.time() - t0
        with open(opath, encoding="utf-8") as f:
            out = json.load(f)
        return {"out": out, "secs": round(dt, 3), "reloaded": reloaded, "n": len(items)}
    finally:
        # CLEANUP BELONGS TO THE CALLER THAT SEES THE END BY ANY ROUTE -- a payload that raises
        # must not leave its scratch files behind on a device with 3 GB of storage.
        for p in (ipath, opath):
            try:
                os.remove(p)
            except Exception:
                pass


# Frigate offloads detection to tablets via DeepStack protocol. No auth required by default
# (DeepStack has no bearer support; optional key honored if TABLET_DETECT_KEY is set).
# CPU inference used instead of Vulkan: Mali GPU is ~18x slower on this hardware for these models.

DETECT_PORT = int(os.environ.get("TABLET_DETECT_PORT", "8801"))
DETECT_HOME = os.path.expanduser("~")
DETECT_DIR = os.environ.get("TABLET_DETECT_DIR", os.path.join(DETECT_HOME, "det"))
DETECT_BIN = os.environ.get("TABLET_DETECT_BIN", os.path.join(DETECT_HOME, "ncnn_detect"))
DETECT_PARAM = os.path.join(DETECT_DIR, "mobilenet_ssd_voc_ncnn.param")
DETECT_WEIGHTS = os.path.join(DETECT_DIR, "mobilenet_ssd_voc_ncnn.bin")
DETECT_CONTROL = os.path.join(DETECT_DIR, "control.jpg")
# Label must travel in a sidecar (control.expect) with the image. Decoupling them (e.g. via env vars) risks
# the selftest asserting against a label that no longer matches the swapped control frame.
DETECT_CONTROL_EXPECT_FILE = os.path.join(DETECT_DIR, "control.expect")
DETECT_W = int(os.environ.get("TABLET_DETECT_W", "300"))
DETECT_H = int(os.environ.get("TABLET_DETECT_H", "300"))
DETECT_THREADS = int(os.environ.get("TABLET_DETECT_THREADS", "4"))
DETECT_KEY = os.environ.get("TABLET_DETECT_KEY", "")

# VOC-to-COCO label mapping is load-bearing: unmapped labels cause deepstack to abort
# processing, discarding all subsequent predictions in the sequence.
VOC_TO_COCO = {
    "aeroplane": "airplane",
    "diningtable": "dining table",
    "motorbike": "motorcycle",
    "pottedplant": "potted plant",
    "sofa": "couch",
    "tvmonitor": "tv",
}
DETECT_LABELS = set(
    x.strip() for x in os.environ.get("TABLET_DETECT_LABELS", "person,cat,dog").split(",")
    if x.strip()
)

_DETECT_LOCK = threading.Lock()
_DETECT = {"proc": None, "frames": 0, "errors": 0, "restarts": 0, "last_ms": None,
           "started": None, "ready": None}


DETECT_PAUSE = os.environ.get("TABLET_DETECT_PAUSE", os.path.join(DETECT_HOME, "detect.paused"))


def detect_available():
    """Availability is determined by the presence of detection artifacts, not a configured flag, with one exception: the pause marker file. The pause must persist across agent respawns and reboots. Downstream components (capabilities, routing, listener) treat the node as "not available" during a pause. A node must not advertise capabilities it cannot currently fulfill (e.g., starting a listener that returns 503s)."""
    missing = ["%s (%s)" % (what, path) for path, what in ((DETECT_BIN, "detector binary"),
                                                           (DETECT_PARAM, "model graph"),
                                                           (DETECT_WEIGHTS, "model weights"))
               if not os.path.exists(path)]
    if os.path.exists(DETECT_PAUSE):
        # Pause must not mask missing artifacts; report both to avoid silent delays
        return False, "paused by Tolkien (%s); %s" % (
            DETECT_PAUSE, "ALSO missing %s - --install-detect before resuming" % ", ".join(missing)
            if missing else "ready to resume")
    if missing:
        return False, "missing %s" % missing[0]
    return True, "ok"


def _detect_spawn():
    """Start the resident detector and block until it reports the weights are LOADED.

    Waiting for the ready line is what keeps the first real frame honest: without it, frame 1
    silently absorbs the model load and the detector looks mysteriously slow exactly once, which
    is the hardest kind of slow to diagnose after the fact."""
    ok, why = detect_available()
    if not ok:
        raise RuntimeError(why)
    p = subprocess.Popen(
        [DETECT_BIN, DETECT_PARAM, DETECT_WEIGHTS, str(DETECT_W), str(DETECT_H),
         str(DETECT_THREADS)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    line = p.stdout.readline()
    if not line:
        raise RuntimeError("detector exited before reporting ready (rc=%s)" % p.poll())
    hello = json.loads(line.decode("utf-8"))
    if not hello.get("ready"):
        raise RuntimeError("detector said %r" % hello)
    _DETECT["proc"] = p
    _DETECT["started"] = time.time()
    _DETECT["ready"] = hello
    log("detector resident: %s" % hello)
    return p


def _detect_infer(rgb_bytes):
    """One fixed-size frame in, one prediction dict out. Caller holds _DETECT_LOCK."""
    p = _DETECT["proc"]
    if p is None or p.poll() is not None:
        # Do not return empty results for a dead worker; it must raise or restart so Frigate
        # distinguishes a broken detector from an empty room (no motion).
        _DETECT["restarts"] += 1
        log("detector gone (rc=%s) - restarting" % (p.poll() if p else "never started"))
        p = _detect_spawn()
    p.stdin.write(rgb_bytes)
    p.stdin.flush()
    line = p.stdout.readline()
    if not line:
        raise RuntimeError("detector produced no output")
    return json.loads(line.decode("utf-8"))


def _detect_predict(jpeg_bytes):
    """JPEG -> Frigate-shaped predictions, filtered and ordered for deepstack.py's `break`."""
    from PIL import Image                     # imported here so a head with no Pillow still loads

    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    src_w, src_h = img.size
    if img.size != (DETECT_W, DETECT_H):
        # Frigate ignores per-detector size config; frames arrive at native resolution (e.g., 320x320).
        # We must measure actual input dims and scale coordinates back from the net's native 300.
        img = img.resize((DETECT_W, DETECT_H))
    raw = img.tobytes()

    t0 = time.time()
    with _DETECT_LOCK:
        out = _detect_infer(raw)
    _DETECT["last_ms"] = round((time.time() - t0) * 1000, 1)
    _DETECT["frames"] += 1

    # Coordinates must be in submitted frame pixels (not network input size).
    # deepstack.py normalizes boxes by its own image dimensions; returning
    # mismatched coords causes silent geometric drift.
    sx = src_w / float(DETECT_W)
    sy = src_h / float(DETECT_H)

    preds = []
    for d in out.get("predictions", []):
        label = VOC_TO_COCO.get(d["label"], d["label"])
        if label not in DETECT_LABELS:
            continue
        d = dict(d)
        d["label"] = label
        d["x_min"] = round(d["x_min"] * sx, 1)
        d["x_max"] = round(d["x_max"] * sx, 1)
        d["y_min"] = round(d["y_min"] * sy, 1)
        d["y_max"] = round(d["y_max"] * sy, 1)
        preds.append(d)
    return preds


def _detect_multipart_image(body, content_type):
    """Pull the `image` part out of a multipart/form-data body.

    Hand-rolled because `cgi` was removed in Python 3.13 and these tablets run newer than that."""
    marker = "boundary="
    if marker not in (content_type or ""):
        return None
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    for part in body.split(b"--" + boundary.encode()):
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep or b'name="image"' not in head:
            continue
        return data[:-2] if data.endswith(b"\r\n") else data
    return None


def detect_selftest():
    """Verify detector health using a known-positive control frame.

    An empty prediction list is ambiguous: it can indicate a clean room or a broken model/
    dead subprocess. A known-positive input disambiguates these states, ensuring the
    detector is actively processing frames."""
    if not os.path.exists(DETECT_CONTROL):
        return {"ok": False, "error": "no control frame at %s" % DETECT_CONTROL}
    try:
        with open(DETECT_CONTROL_EXPECT_FILE, encoding="utf-8") as fh:
            expect = fh.read().strip()
    except Exception:
        # No sidecar means we do not know what this frame contains, and a decoy that cannot say
        # what it expects proves nothing. Refuse rather than invent an expectation.
        return {"ok": False, "error": "no expectation sidecar at %s" % DETECT_CONTROL_EXPECT_FILE}
    with open(DETECT_CONTROL, "rb") as fh:
        preds = _detect_predict(fh.read())
    found = [p["label"] for p in preds]
    return {"ok": expect in found, "expected": expect,
            "found": found, "predictions": preds, "ms": _DETECT["last_ms"]}


def detect_status():
    p = _DETECT["proc"]
    ok, why = detect_available()
    return {"available": ok, "why": why,
            "alive": bool(p is not None and p.poll() is None),
            "port": DETECT_PORT, "size": [DETECT_W, DETECT_H], "threads": DETECT_THREADS,
            "labels": sorted(DETECT_LABELS),
            "frames": _DETECT["frames"], "errors": _DETECT["errors"],
            "restarts": _DETECT["restarts"], "last_ms": _DETECT["last_ms"],
            "uptime_s": round(time.time() - _DETECT["started"]) if _DETECT["started"] else None}


@fellow_handler("detect")
def _fellow_detect_task(req):
    """Tolkien-facing view of the detector: status, or run the live decoy.

    Exposed on the AUTHENTICATED task port so the head can verify a node without anyone opening the
    unauthenticated detection port to do it."""
    action = (req.get("action") or "status").strip().lower()
    if action == "status":
        return detect_status()
    if action == "selftest":
        return detect_selftest()
    raise RuntimeError("unknown detect action %r (status|selftest)" % action)


def _detect_app(method, path, body, content_type, api_key=""):
    """Pure request -> (status, obj). Split from HTTP so the protocol is testable without a socket.

    This is the seam that a unit test cannot see if it only tests the halves: the emitter and the
    parser have to AGREE, and this lab has already shipped a patrol that was completely blind
    because a producer and a consumer disagreed about one tab character."""
    path = (path or "").split("?", 1)[0]

    if method == "GET" and path == "/health":
        return 200, detect_status()
    if method == "GET" and path == "/selftest":
        try:
            return 200, detect_selftest()
        except Exception as e:
            return 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    if method == "POST" and path == "/v1/vision/detection":
        if DETECT_KEY and not hmac.compare_digest(api_key or "", DETECT_KEY):
            return 401, {"success": False, "error": "bad api_key"}
        try:
            jpeg = _detect_multipart_image(body, content_type)
            if not jpeg:
                return 400, {"success": False, "error": "no image part"}
            preds = _detect_predict(jpeg)
        except Exception as e:
            _DETECT["errors"] += 1
            log("detect request failed: %s: %s" % (type(e).__name__, e))
            return 503, {"success": False, "error": "%s: %s" % (type(e).__name__, e)}
        return 200, {"success": True, "predictions": preds}

    return 404, {"success": False, "error": "no such endpoint"}


def detect_serve(port=None):
    """Start the DeepStack listener on its own thread. Returns (started, why)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    ok, why = detect_available()
    if not ok:
        log("detector NOT started: %s" % why)
        return False, why
    try:
        _detect_spawn()
    except Exception as e:
        log("detector failed to start: %s: %s" % (type(e).__name__, e))
        return False, "%s: %s" % (type(e).__name__, e)

    class D(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, code, obj):
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._reply(*_detect_app("GET", self.path, None, None))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n)
            ctype = self.headers.get("Content-Type") or ""
            key = ""
            if DETECT_KEY:
                # api_key arrives as an ordinary form field alongside the image part.
                head, sep, rest = body.partition(b'name="api_key"\r\n\r\n')
                if sep:
                    key = rest.split(b"\r\n", 1)[0].decode("utf-8", "replace")
            self._reply(*_detect_app("POST", self.path, body, ctype, key))

        def log_message(self, *a):
            pass                    # several requests a second, forever -- the access log is noise

    srv = ThreadingHTTPServer(("0.0.0.0", port or DETECT_PORT), D)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _DETECT["listener"] = True
    log("detector listening on :%d  labels=%s" % (port or DETECT_PORT,
                                                  ",".join(sorted(DETECT_LABELS))))
    return True, "ok"


def _detect_stop():
    p = _DETECT["proc"]
    _DETECT["proc"] = None
    if p is None or p.poll() is not None:
        return False
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(5)
    return True


def detect_pause(why=""):
    """Write the pause marker, then stop the resident detector. Returns the node's detector state."""
    with open(DETECT_PAUSE, "w", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), why or "paused by Tolkien"))
    with _DETECT_LOCK:
        stopped = _detect_stop()
    log("detector PAUSED by Tolkien (%s)%s" % (why or "-", " - stopped the resident detector" if stopped else ""))
    return dict(detect_status(), stopped=stopped)


def detect_resume():
    """Remove the pause marker and start the detector again (the listener too, if this agent booted paused)."""
    try:
        os.remove(DETECT_PAUSE)
    except FileNotFoundError:
        pass
    if not _DETECT.get("listener"):
        started, why = detect_serve()
    else:
        with _DETECT_LOCK:
            if _DETECT["proc"] is None or _DETECT["proc"].poll() is not None:
                try:
                    _detect_spawn()
                    started, why = True, "ok"
                except Exception as e:                                       # noqa: BLE001
                    started, why = False, "%s: %s" % (type(e).__name__, e)
            else:
                started, why = True, "already running"
    log("detector RESUMED by Tolkien: %s" % why)
    return dict(detect_status(), started=started, why_start=why)


# hwdec is managed by the agent (respawn, health surface) because standalone guards
# lack restart policies and expiry reporting.
# Hardware decodes H.264/H.265 on MT8768, serving raw yuv420p over TCP for Frigate.
# Assignment is config-driven (`hwdec.json`); absent file means no decode.
HWDEC_BIN = os.environ.get("TABLET_HWDEC_BIN", os.path.join(DETECT_HOME, "hwdec"))
HWDEC_SPEC = os.environ.get("TABLET_HWDEC_SPEC", os.path.join(DETECT_HOME, "hwdec.json"))
HWDEC_BEAT = os.environ.get("TABLET_HWDEC_BEAT", os.path.join(DETECT_HOME, "hwdec.beat"))
HWDEC_LOG = os.path.join(DETECT_HOME, "hwdec.log")

_HWDEC = {"proc": None, "restarts": 0, "started": None, "spec": None}


def hwdec_available():
    """(ok, why) -- BY DETECTION. Binary present AND an assignment for THIS node."""
    if not os.path.exists(HWDEC_BIN):
        return False, "no hwdec binary (%s)" % HWDEC_BIN
    if not os.path.exists(HWDEC_SPEC):
        return False, "no assignment (%s) - this node decodes nothing" % HWDEC_SPEC
    try:
        with open(HWDEC_SPEC) as fh:
            spec = json.load(fh)
    except Exception as e:
        return False, "unreadable assignment: %s: %s" % (type(e).__name__, e)
    if not spec.get("input_cmd"):
        return False, "assignment has no input_cmd"
    return True, "ok"


def hwdec_beat_read():
    """The decoder's own heartbeat, or None.

    Liveness is determined by file freshness AND frame counter updates. A live process with a static frame count indicates a wedged worker."""
    try:
        with open(HWDEC_BEAT) as fh:
            b = json.load(fh)
        b["age"] = max(0.0, time.time() - float(b.get("ts") or 0))
        return b
    except Exception:
        return None


def _hwdec_spawn():
    ok, why = hwdec_available()
    if not ok:
        raise RuntimeError(why)
    with open(HWDEC_SPEC) as fh:
        spec = json.load(fh)
    argv = [HWDEC_BIN,
            "--mime", spec.get("mime", "video/hevc"),
            "--width", str(spec.get("width", 1920)),
            "--height", str(spec.get("height", 1080)),
            "--serve", str(spec.get("port", 8803)),
            "--heartbeat", HWDEC_BEAT,
            "--input-cmd", spec["input_cmd"]]
    if spec.get("scale"):
        argv += ["--scale", spec["scale"]]
    if spec.get("fps"):
        argv += ["--fps", str(spec["fps"])]
    # Its own log file: the decoder is chatty (a diag line every 3 s) and interleaving that into the
    # agent's log would bury everything else the fellow says.
    logf = open(HWDEC_LOG, "ab", buffering=0)
    p = subprocess.Popen(argv, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL)
    _HWDEC.update({"proc": p, "started": time.time(), "spec": spec})
    log("hwdec started pid=%s port=%s scale=%s fps=%s"
        % (p.pid, spec.get("port", 8803), spec.get("scale"), spec.get("fps")))
    return p


def hwdec_supervise():
    """Start the decoder and keep it alive. Runs on its own thread inside the agent.

    Restarts are bounded by backoff to prevent infinite respawn loops that flood logs or overload
    the device. The binary itself refuses to restart for known fatal errors; this loop handles
    transient failures."""
    ok, why = hwdec_available()
    if not ok:
        log("hwdec NOT started: %s" % why)
        return
    backoff = 5
    while True:
        clean = False
        try:
            p = _hwdec_spawn()
            rc = p.wait()
            _HWDEC["proc"] = None
            _HWDEC["restarts"] += 1
            clean = (rc == 0)
            log("hwdec exited rc=%s (%s) - restarting in %ss (restart #%d)"
                % (rc, "clean session end" if clean else "FAILURE",
                   1 if clean else backoff, _HWDEC["restarts"]))
        except Exception as e:
            log("hwdec spawn failed: %s: %s - retrying in %ss" % (type(e).__name__, e, backoff))
        if clean:
            # Straight back to listening, and the failure ladder resets -- a good session is
            # evidence the decoder works, which is exactly what the escalation was doubting.
            backoff = 5
            time.sleep(1)
        else:
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)     # 5s -> 5min ceiling, failures only
        # A node whose assignment was REMOVED should stop trying, not retry forever.
        ok, why = hwdec_available()
        if not ok:
            log("hwdec assignment gone (%s) - supervisor exiting" % why)
            return


# Decode is bursty, not continuous: NVR triggers hardware decode of clip segments.
# Same binary supports two modes via flags:
#   --serve PORT          : live path
#   --out FILE --frames N : burst playback path (via --input-cmd)
# Do not write a second decoder for the second mode.
#
# Validation relies on frames delivered, not process start state,
# because hardware decoders may configure/start but emit no frames.

BURST_DIR = os.environ.get("TABLET_BURST_DIR", os.path.join(DETECT_HOME, "burst"))


def _burst_rtsp_time(local_str):
    """Convert datetime string to underscore-separated format required by NVR playback URLs.

    The device silently rejects dashes/colons, causing ffmpeg timeouts.
    Format: 'YYYY_MM_DD_HH_MM_SS'.
    """
    return local_str.strip().replace("-", "_").replace(" ", "_").replace(":", "_")


_CRED_IN_URL = re.compile(r"(rtsp://)[^/@\s]*@")


def _redact(text):
    """Redact credentials from RTSP URLs in text.

    ffmpeg may print input URLs (including credentials) to stderr.
    This redaction prevents leaks if stderr is logged, stored, or indexed."""
    return _CRED_IN_URL.sub(r"\1<redacted>@", text or "")


def _burst_probe_geometry(channel, start_local, end_local, user, pw, host, timeout=45,
                          substream=False):
    """Probe the clip source for its native resolution and frame rate.

    Hardware decoders (e.g., MediaCodec) fail silently or corrupt output if
    fed with assumed dimensions that differ from the actual stream headers.
    Historical footage may have different geometry than current defaults;
    never assume format based on camera model alone.

    Returns {} if the probe fails or returns no data. The caller is responsible
    for falling back to a configuration hint; do not return guessed values."""
    import urllib.parse
    if substream in ("sub", True, 1):
        url = ("rtsp://%s:%s@%s:554/cam/realmonitor?channel=%d&subtype=1"
               % (urllib.parse.quote(user, safe=""), urllib.parse.quote(pw, safe=""), host, channel))
    elif substream == "main":
        url = ("rtsp://%s:%s@%s:554/cam/realmonitor?channel=%d&subtype=0"
               % (urllib.parse.quote(user, safe=""), urllib.parse.quote(pw, safe=""), host, channel))
    else:
        url = ("rtsp://%s:%s@%s:554/cam/playback?channel=%d&starttime=%s&endtime=%s"
               % (urllib.parse.quote(user, safe=""), urllib.parse.quote(pw, safe=""), host, channel,
                  _burst_rtsp_time(start_local), _burst_rtsp_time(end_local)))
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v",
             "-show_entries", "stream=width,height,codec_name", "-of", "default=nw=1", url],
            capture_output=True, timeout=timeout)
    except Exception as e:                                           # noqa: BLE001
        log("burst geometry probe failed: %s: %s" % (type(e).__name__, e))
        return {}
    out = {}
    for line in (p.stdout or b"").decode("utf-8", "replace").splitlines():
        k, _, v = line.strip().partition("=")
        if k in ("width", "height"):
            try:
                out[k] = int(v)
            except ValueError:
                pass
        elif k == "codec_name":
            out["codec"] = v
    return out


def _burst_playback_argv(channel, start_local, end_local, user, pw, host, substream=False):
    """Return an ffmpeg argv list (not a shell string) for streaming one recorded segment.

        Args:
            substream: If True, use the live realmonitor sub-stream (subtype=1),
                       which is hard-capped at 704x480. Recorded playback always uses
                       the main stream.
    """
    import urllib.parse
    if substream in ("sub", True, 1):
        url = ("rtsp://%s:%s@%s:554/cam/realmonitor?channel=%d&subtype=1"
               % (urllib.parse.quote(user, safe=""), urllib.parse.quote(pw, safe=""), host, channel))
    elif substream == "main":
        # LIVE main. Needed for the sub-vs-main comparison the 08-10 journal named and nobody ran:
        # "matched crops from the live sub and live main AT THE SAME INSTANT". Archive playback
        # cannot serve that arm -- it is a different moment, and the scene moves.
        url = ("rtsp://%s:%s@%s:554/cam/realmonitor?channel=%d&subtype=0"
               % (urllib.parse.quote(user, safe=""), urllib.parse.quote(pw, safe=""), host, channel))
    else:
        url = ("rtsp://%s:%s@%s:554/cam/playback?channel=%d&starttime=%s&endtime=%s"
               % (urllib.parse.quote(user, safe=""), urllib.parse.quote(pw, safe=""), host, channel,
                  _burst_rtsp_time(start_local), _burst_rtsp_time(end_local)))
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
            "-i", url, "-an", "-c:v", "copy", "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", "-"]


def _burst_playback_cmd(channel, start_local, end_local, user, pw, host):
    """Return ffmpeg command for recording playback via RTSP.

    Note: `cam/playback` endpoint, not `cam/realmonitor`.
    Times are NVR local wall-clock; no offset applied."""
    import urllib.parse
    url = ("rtsp://%s:%s@%s:554/cam/playback?channel=%d&starttime=%s&endtime=%s"
           % (urllib.parse.quote(user, safe=""), urllib.parse.quote(pw, safe=""), host, channel,
              _burst_rtsp_time(start_local), _burst_rtsp_time(end_local)))
    return ("ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -i '%s' "
            "-an -c:v copy -bsf:v hevc_mp4toannexb -f hevc -" % url)


@fellow_handler("burst")
def _fellow_burst(req):
    """Hardware-decode clip segment and run RESIDENT detector.

    Returns per-frame detections plus counts. Note: `decoded == 0` is a failure
    even if processes exit cleanly."""
    ch = int(req.get("channel") or 1)
    start_local = req.get("start")            # 'YYYY-MM-DD HH:MM:SS', NVR local
    end_local = req.get("end")
    if not start_local or not end_local:
        return {"ok": False, "error": "burst needs 'start' and 'end' (NVR local wall-clock)"}

    scale = req.get("scale") or "704x480"
    max_frames = int(req.get("frames") or 150)
    user, pw = req.get("user"), req.get("pw")
    host = req.get("nvr") or os.environ.get("TOLKIEN_NVR_HOST", "")
    if not user or not pw:
        return {"ok": False, "error": "burst needs NVR credentials; it does not hold any of its own"}
    if not host:
        return {"ok": False, "error": "burst needs the NVR's address (the order's nvr, or TOLKIEN_NVR_HOST)"}

    ok, why = hwdec_available()
    if not os.path.exists(HWDEC_BIN):
        return {"ok": False, "error": "no hwdec binary (%s)" % HWDEC_BIN}

    # Archive clips have heterogeneous geometries (e.g., pre-2026 resolutions differ from current defaults). MediaCodec fails silently or emits garbage if configured with incorrect dimensions. Therefore, we probe the clip for actual geometry rather than trusting caller hints or global defaults; hints serve only as fallbacks, and mismatches are logged.
    hint_w = int(req.get("width") or 0)
    hint_h = int(req.get("height") or 0)
    substream = req.get("substream")          # "sub" | "main" (both LIVE) | falsy = archive playback
    probed = _burst_probe_geometry(ch, start_local, end_local, user, pw, host,
                                   substream=substream)
    width = probed.get("width") or hint_w or 2688
    height = probed.get("height") or hint_h or 1520
    if probed.get("width") and hint_w and probed["width"] != hint_w:
        log("burst: clip is %dx%d but caller hinted %dx%d - using the CLIP"
            % (probed["width"], probed["height"], hint_w, hint_h))

    os.makedirs(BURST_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    raw = os.path.join(BURST_DIR, "burst-%s.yuv" % stamp)
    sw, sh = [int(x) for x in scale.lower().split("x")]

    t0 = time.time()
    # `hwdec` ignores `--input-cmd` unless `--serve` is active; it reads from stdin.
    # Feed ffmpeg stdout into hwdec stdin to utilize the documented 'stdin' path.
    ff_argv = _burst_playback_argv(ch, start_local, end_local, user, pw, host,
                                   substream=substream)
    burst_timeout = int(req.get("timeout") or 300)
    # Backstop timeout: child deadline is longer than parent's `communicate` timeout.
    # This handles cases where the agent dies mid-burst but the decoder remains wedged,
    # which PDEATHSIG does not cover in this path.
    hw_argv = [HWDEC_BIN, "--mime", req.get("mime") or "video/hevc",
               "--width", str(width), "--height", str(height),
               "--scale", scale, "--frames", str(max_frames), "--out", raw,
               "--max-seconds", str(burst_timeout + 15)]
    # Require explicit --codec so burst metrics measure capability for THIS decoder/geometry, not just which one the try-chain picked.
    if req.get("codec"):
        hw_argv += ["--codec", str(req["codec"])]
    ff = hw = None
    try:
        ff = subprocess.Popen(ff_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        hw = subprocess.Popen(hw_argv, stdin=ff.stdout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ff.stdout.close()          # the parent must drop its handle or hwdec never sees EOF
        _hw_out, _hw_err = hw.communicate(timeout=int(req.get("timeout") or 300))
        _hw_full = _redact((_hw_err or b"").decode("utf-8", "replace"))
        dec_err = _hw_full[-600:]
        dec_rc = hw.returncode
        # `HEVC_TRY` falls back silently from hardware to software.
        # Without this, a "hardware" arm may have run software,
        # invalidating throughput comparisons. The landed decoder name
        # is captured in the result for provenance on both success/failure.
        _m = re.search(r"decoder started:\s*(\S+)", _hw_full or "")
        dec_name = _m.group(1) if _m else None
        # Search `_hw_full` (not truncated `decoder_stderr`) for the try-chain.
        # Startup verdicts fall out of the 600-char tail on long chains,
        # making distinct failures indistinguishable.
        _chain = [ln.strip() for ln in (_hw_full or "").splitlines()
                  if ("not present on this node" in ln or "cannot configure" in ln
                      or "would not start" in ln or "decoder started:" in ln
                      or "walking past it" in ln or "no named decoder took" in ln)]
        try:
            ff.kill()
        except Exception:
            pass
        p = None
    except subprocess.TimeoutExpired as te:      # Must reap after kill: sending SIGKILL does not guarantee termination or cleanup.
# An unreaped child becomes a zombie; without wait(), we cannot distinguish
# 'already dead' from 'resisted', leading to undetected orphaned processes.
        _survived = []
        for _name, _proc in (("hwdec", hw), ("ffmpeg", ff)):
            if _proc is None:
                continue
            try:
                _proc.kill()
            except Exception:                                        # noqa: BLE001
                pass
            try:
                _proc.wait(timeout=5)
            except Exception:                                        # noqa: BLE001
                _survived.append("%s(pid=%s)" % (_name, getattr(_proc, "pid", "?")))
        if _survived:
            log("burst timeout: child(ren) SURVIVED kill+wait: %s -- self-deadline should collect "
                "them, but this is a real defect if it repeats" % ", ".join(_survived))
        # Timeout must carry evidence (stderr + partial .yuv size) to distinguish
        # "decoder never started" from "slow stream", which are distinct failure modes.
        partial = os.path.getsize(raw) if os.path.exists(raw) else 0
        fsz_guess = (sw * sh * 3 // 2) if sw and sh else 0
        got = (partial // fsz_guess) if fsz_guess else 0
        err = _redact(((te.stderr or b"") if isinstance(te.stderr, bytes)
                       else (te.stderr or "").encode()).decode("utf-8", "replace"))[-600:]
        _burst_cleanup(raw)
        return {"ok": False,
                "error": "hwdec timed out after %ss with %d frame(s) written (%d bytes)"
                         % (req.get("timeout") or 300, got, partial),
                "decoded": got, "partial_bytes": partial, "decoder_stderr": err,
                "geometry": "%dx%d" % (width, height), "scale": scale}
    decode_s = time.time() - t0

    fsz = sw * sh * 3 // 2                      # yuv420p
    have = os.path.getsize(raw) if os.path.exists(raw) else 0
    decoded = have // fsz if fsz else 0

    if decoded == 0:
        _burst_cleanup(raw)
        return {"ok": False, "error": "decoder produced ZERO frames (rc=%s)" % dec_rc,
                "decoded": 0, "decoder": dec_name, "engine": _engine_class(dec_name),
                "try_chain": _chain[:12],
                "decoder_stderr": dec_err, "decode_s": round(decode_s, 1)}

    stride = max(1, int(req.get("stride") or 1))
    frames, sent, errors = [], 0, 0
    for i in range(0, decoded, stride):
        try:
            jpeg = _burst_frame_jpeg(raw, i, sw, sh, fsz)
            preds = _burst_detect(jpeg)
            sent += 1
            if preds:
                frames.append({"frame": i, "predictions": preds})
        except Exception as e:                                       # noqa: BLE001
            errors += 1
            if errors <= 3:
                log("burst frame %d failed: %s: %s" % (i, type(e).__name__, e))

    _burst_cleanup(raw)
    return {"ok": True, "channel": ch, "start": start_local, "end": end_local,
            # "engine" and "decoder" must be recorded for every result (even CPU fallbacks)
            # so cross-node comparisons remain meaningful. `None` means unseen (unknown, not guessed).
            # Omission makes performance metrics uninterpretable since fallback is silent.
            "decoder": dec_name, "engine": _engine_class(dec_name), "decoder_stderr": dec_err,
            # Every candidate's verdict, in order, so "why this engine and not that one" is
            # answerable from the RESULT instead of requiring a second run to find out.
            "try_chain": _chain[:12],
            "decoded": decoded, "analysed": sent, "frame_errors": errors,
            "frames_with_detections": len(frames), "detections": frames,
            "decode_s": round(decode_s, 1), "total_s": round(time.time() - t0, 1),
            "scale": scale, "decoder_rc": dec_rc}


def _engine_class(decoder_name):
    """
    Map a decoder name to the underlying engine class.

    Returns 'dedicated' for hardware video blocks (vendor-prefixed e.g. mtk/qcom),
    'cpu' for AOSP software decoders (c2.android.* / omx.google.*), or None
    if the decoder is unknown.

    Note: There is no 'gpu' class because these decode paths use ByteBuffers,
    not GPU surfaces; the distinction is strictly between dedicated hardware
    and CPU fallback.
    """
    if not decoder_name:
        return None
    n = str(decoder_name).lower()
    if n.startswith("c2.android.") or n.startswith("omx.google."):
        return "cpu"
    for vendor in ("mtk", "qcom", "qti"):
        if vendor in n:
            return "dedicated"
    return None


def _burst_frame_jpeg(raw_path, index, w, h, fsz):
    """One yuv420p frame -> JPEG bytes, via ffmpeg (present on every node, already load-bearing)."""
    with open(raw_path, "rb") as fh:
        fh.seek(index * fsz)
        buf = fh.read(fsz)
    if len(buf) < fsz:
        raise IOError("short frame %d (%d/%d bytes)" % (index, len(buf), fsz))
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
         "-s", "%dx%d" % (w, h), "-i", "-", "-frames:v", "1", "-f", "mjpeg", "-"],
        input=buf, capture_output=True, timeout=30)
    if not p.stdout:
        raise IOError("ffmpeg produced no JPEG: %s" % (p.stderr or b"")[-200:])
    return p.stdout


def _burst_detect(jpeg):
    """POST one JPEG to THIS node's resident detector. Returns predictions (possibly empty).

        Raises 503 on failure, never an empty list: empty results must not be conflated with
    detector failures."""
    import urllib.request
    boundary = "----burst%d" % int(time.time() * 1000)
    body = (b"--" + boundary.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="image"; filename="f.jpg"\r\n'
            b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            b"--" + boundary.encode() + b"--\r\n")
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/vision/detection" % DETECT_PORT, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with urllib.request.urlopen(req, timeout=30) as r:
        obj = json.loads(r.read().decode("utf-8"))
    if not obj.get("success"):
        raise RuntimeError("detector said %s" % str(obj)[:160])
    return obj.get("predictions") or []


def _burst_cleanup(raw):
    """The raw YUV is a BYPRODUCT and is deleted in the same pass that created it.

    A 704x480 burst is ~0.5 MB/frame; 150 frames is ~75 MB. Leaving those to accumulate on a tablet
    is the staged-adapter mistake in miniature -- and unlike a GPU box, these nodes have no room to
    absorb it."""
    try:
        if os.path.exists(raw):
            os.remove(raw)
    except OSError:
        pass


def hwdec_status():
    """What this node's decoder is doing. Reported by the fellow, read by Tolkien."""
    ok, why = hwdec_available()
    beat = hwdec_beat_read()
    p = _HWDEC["proc"]
    return {"assigned": ok, "why": why,
            "pid": (p.pid if p and p.poll() is None else None),
            "restarts": _HWDEC["restarts"],
            "beat": beat,
            # Two separate claims, kept separate on purpose: a process can be alive while the
            # decode is wedged, and the beat is what tells them apart.
            "process_alive": bool(p and p.poll() is None),
            "beat_fresh": bool(beat and beat.get("age", 1e9) < 30)}


# ── HEAD SIDE: provisioning the detector ─────────────────────────────────────────────────────────

def detect_install(hosts=None, src=None):
    """Push the detector binary, model and control frame to each node. Tolkien's job, not a hand-run
    scp -- if the factory lacks a mechanism the answer is to BUILD it, never to walk the floor.

    Idempotent: a node whose files already match by size is skipped, so this is safe to re-run and
    cheap enough to be part of a deploy."""
    # Staging on shared mount (/ssdshared/tabfoundry-detect), not in docker context
    # to avoid bloating images with binaries/models.
    src = src or os.environ.get("TABLET_DETECT_STAGE", "/ssdshared/tabfoundry-detect")
    files = [("ncnn_detect", "ncnn_detect", True),
             ("mobilenet_ssd_voc_ncnn.param", "det/mobilenet_ssd_voc_ncnn.param", False),
             ("mobilenet_ssd_voc_ncnn.bin", "det/mobilenet_ssd_voc_ncnn.bin", False),
             ("control.jpg", "det/control.jpg", False),
             ("control.expect", "det/control.expect", False),
             # The hardware decoder ships with the detector -- same fleet, same staging dir, same
             # write-beside-then-rename (it is a RUNNING executable, so a direct scp gets ETXTBSY
             # on every upgrade after the first). Its per-node ASSIGNMENT is deliberately NOT
             # shipped here: the binary is fleet-wide, what a node decodes is not.
             ("hwdec", "hwdec", True)]
    missing = [f for f, _, _ in files if not os.path.exists(os.path.join(src, f))]
    if missing:
        return {"ok": False, "error": "staging incomplete in %s: %s" % (src, ", ".join(missing))}

    out = {}
    for host in (hosts or list(TABLET_NET)):
        steps = []
        rc, _ = _ssh(host, "mkdir -p ~/det", timeout=30)
        if rc != 0:
            out[host] = {"ok": False, "error": "cannot mkdir ~/det"}
            continue
        for local_name, remote_rel, executable in files:
            local = os.path.join(src, local_name)
            want = os.path.getsize(local)
            rc, have = _ssh(host, "stat -c%%s ~/%s 2>/dev/null || echo 0" % remote_rel, timeout=30)
            if rc == 0 and (have or "").strip().isdigit() and int(have.strip()) == want:
                steps.append("%s: already current" % remote_rel)
                continue
            # Write to .tmp then rename to avoid ETXTBSY when the binary is running.
            rc, err = _scp(host, local, remote_rel + ".new")
            if rc != 0:
                out[host] = {"ok": False, "error": "scp %s failed: %s" % (remote_rel, err)}
                break
            if executable:
                _ssh(host, "chmod +x ~/%s.new" % remote_rel, timeout=30)
            rc, err = _ssh(host, "mv -f ~/%s.new ~/%s" % (remote_rel, remote_rel), timeout=30)
            if rc != 0:
                out[host] = {"ok": False, "error": "install %s failed: %s" % (remote_rel, err)}
                break
            steps.append("%s: pushed %d bytes" % (remote_rel, want))
        else:
            out[host] = {"ok": True, "steps": steps}
    return out


def detect_fleet_status(hosts=None, selftest=False):
    """Query detector status for all nodes.

    Use ``selftest=True`` to run live decoy probes. Without this,
    the result only confirms network reachability, not functional detection."""
    out = {}
    for host in (hosts or list(TABLET_NET)):
        # fellow_call returns (ok, obj) -- transport success, then the node's own verdict inside.
        # Those are two different questions and collapsing them is how "could not ask" gets
        # rendered as a definite state.
        reached, obj = fellow_call(
            host, "/work", {"task": "detect", "action": "selftest" if selftest else "status"},
            timeout=90)
        if not reached:
            out[host] = {"ok": False, "reached": False, "error": (obj or {}).get("error")}
        elif not obj.get("ok"):
            out[host] = {"ok": False, "reached": True, "error": obj.get("error")}
        else:
            out[host] = dict(obj.get("result") or {}, reached=True)
    return out


# Head-side WiFi watchdog for tablet nodes.
#
# Constraint: Nodes deauth ~12h after association. A local on-node script (`wifi_watchdog.sh`)
# cycles the radio if unassociated >5m; external recovery is impossible during outage
# (network drop kills adb/ssh). The re-arm logic protects only the NEXT outage cycle.
#
# Architecture split:
# - STATUS reporting runs here via SSH (reads local log).
# - LIVE RE-ARMING runs on Softserve via ADB (requires uid 2000 access, pre-auth'd key,
#   and `persist.adb.tcp.port=5555`). This module does NOT have ADB installed; the
#   `wifi_watchdog_rearm()` function is inert/fallback-only. Reflashed nodes must
#   reset `persist.adb.tcp.port` or re-arm will fail.

WIFIWD_LOG = "/data/local/tmp/wifiwd.log"
WIFIWD_SCRIPT = "/data/local/tmp/wifi_watchdog.sh"
WIFIWD_STANDDOWN = "/data/local/tmp/wifiwd.OFF"
WIFIWD_ACTS = "/data/local/tmp/wifiwd.acts"
# Fallback cadence when no heartbeat has been seen yet; `why` will note this.
WIFIWD_BEAT_FALLBACK = 1800.0
WIFIWD_MAX_REARM = 3


def wifi_watchdog_status(hosts=None):
    """Check per-node WiFi watchdog liveness.

        Returns one of three states: fresh, stale, or unknown. An unreachable
        node is 'unknown', never 'down', to prevent status fatigue.

        Liveness is determined by heartbeat file age (computed on-device to avoid
        timezone discrepancies between tablet local time and container UTC), not
        process existence.
    """
    out = {}
    for host in (hosts or list(TABLET_NET)):
        rec = {"host": host, "state": "unknown", "age": None, "acts_today": None,
               "standdown": None, "cadence": None, "why": ""}
        cmd = ("now=$(date +%s); "
               "if [ ! -e {log} ]; then m=missing; "
               "elif [ ! -r {log} ]; then m=denied; "
               "else m=$(stat -c %Y {log} 2>/dev/null || echo denied); fi; "
               "b=$(grep HEARTBEAT {log} 2>/dev/null | tail -1 | tr '|' ' '); "
               # !! NO `|| echo 0` HERE. `grep -c` PRINTS "0" and ALSO EXITS 1 when it matches
               # nothing, so the fallback fires too and the variable becomes "0\n0" -- an embedded
               # NEWLINE that splits this echo into two lines. The parser takes the last line, gets
               # too few fields, and reports "unparseable probe output - UNKNOWN". It only shows up
               # on a node whose acts file EXISTS but has no ACT lines, i.e. the one node that had
               # actually done something (frodo, after its WOULD ACT). `head -1` collapses it.
               "a=$(grep -c \"^$(date '+%Y-%m-%d') ACT\" {acts} 2>/dev/null | head -1); "
               "[ -n \"$a\" ] || a=0; "
               "s=$([ -f {off} ] && echo yes || echo no); "
               "echo \"$now|$m|$a|$s|$b\"").format(
                   log=WIFIWD_LOG, acts=WIFIWD_ACTS, off=WIFIWD_STANDDOWN)
        rc, raw = _ssh(host, cmd, timeout=45)
        if rc != 0 or not (raw or "").strip():
            rec["why"] = "could not reach the node - state is UNKNOWN, not down"
            out[host] = rec
            continue
        parts = (raw or "").strip().splitlines()[-1].split("|")
        if len(parts) < 4:
            rec["why"] = "unparseable probe output - UNKNOWN"
            out[host] = rec
            continue
        now_s, mtime_s, acts_s, off_s = parts[:4]
        beat_line = parts[4] if len(parts) > 4 else ""
        rec["standdown"] = (off_s.strip() == "yes")
        # true  = monitoring ON  => the detector chain is at risk on Android 12
        # false = disabled       => the state we require
        # unset/other           => report it verbatim rather than guessing which it means
        try:
            rec["acts_today"] = int(acts_s.strip())
        except ValueError:
            rec["acts_today"] = None

        # The cadence is DECLARED by the reporter, not assumed by the reader.
        beat = None
        m = re.search(r"cadence=(\d+(?:\.\d+)?)", beat_line or "")
        if m:
            try:
                beat = float(m.group(1))
            except ValueError:
                beat = None
        rec["cadence"] = beat
        fallback = beat is None
        if fallback:
            beat = WIFIWD_BEAT_FALLBACK

        if mtime_s.strip() == "missing":
            rec["state"] = "absent"
            rec["why"] = "no watchdog log on this node - never installed, or wiped"
            out[host] = rec
            continue
        if mtime_s.strip() == "denied":
            # NOT actionable. "I cannot read it" is not "it is not there", and re-arming on this
            # would restart watchdogs that are already running.
            rec["state"] = "denied"
            rec["why"] = ("log exists but this uid cannot read it - UNKNOWN, not absent; "
                          "do NOT re-arm on this")
            out[host] = rec
            continue
        try:
            rec["age"] = float(now_s.strip()) - float(mtime_s.strip())
        except ValueError:
            rec["why"] = "unreadable timestamps - UNKNOWN"
            out[host] = rec
            continue
        note = " (cadence not declared yet - using %.0fs fallback)" % beat if fallback else ""
        if rec["age"] <= beat * 2:
            rec["state"] = "fresh"
            if fallback:
                rec["why"] = note.strip()
        elif rec["age"] <= beat * 3:
            rec["state"] = "silent"
            rec["why"] = "no heartbeat for %.0fs (cadence %.0fs)%s" % (rec["age"], beat, note)
        else:
            rec["state"] = "down"
            rec["why"] = "no heartbeat for %.0fs (>3x cadence %.0fs)%s" % (rec["age"], beat, note)
        out[host] = rec
    return out


def _adb():
    """Path to an adb client, or None. Absence is a REPORTED state, never a silent no-op."""
    for cand in ("adb", "/usr/bin/adb", "/usr/local/bin/adb"):
        try:
            if subprocess.run([cand, "version"], capture_output=True,
                              timeout=20).returncode == 0:
                return cand
        except Exception:      # noqa: BLE001 - probing; any failure just means "not this one"
            continue
    return None


def wifi_watchdog_rearm(hosts=None, mode="report", attempts=WIFIWD_MAX_REARM):
    """Restart the on-device watchdog on nodes whose heartbeat is gone. Bounded, idempotent.

    Only acts on `absent` or `down` states. It ignores `unknown` (unmeasurable) and `silent`
    (within cadence) to avoid false-positive triggers.

    Never overrides `WIFIWD_STANDDOWN`; the operator flag is respected per-call.

    Bounded by `attempts`: stops after limit rather than retrying forever. Failures do not
    propagate, ensuring the order desk continues running.
    """
    adb = _adb()
    res = {}
    if not adb:
        # Unwired, and it says so. Silently doing nothing here would be indistinguishable from
        # "all nodes healthy" to every caller.
        return {"ok": False, "error": "no adb client in this container - the re-arm path is "
                                      "INERT until the image ships one (plus the authorized "
                                      "adbkey). Status reporting is unaffected.",
                "nodes": {}}
    status = wifi_watchdog_status(hosts)
    for host, rec in status.items():
        if rec["state"] not in ("absent", "down"):
            res[host] = {"rearmed": False, "why": "state=%s - nothing to do" % rec["state"]}
            continue
        ip = TABLET_NET.get(host, (host, ""))[0]
        started, why = False, ""
        for n in range(1, max(1, attempts) + 1):
            try:
                subprocess.run([adb, "connect", "%s:5555" % ip],
                               capture_output=True, timeout=30)
                p = subprocess.run(
                    [adb, "-s", "%s:5555" % ip, "shell",
                     "setsid sh %s %s </dev/null >/dev/null 2>&1 &" % (WIFIWD_SCRIPT, mode)],
                    capture_output=True, timeout=45)
                if p.returncode == 0:
                    started, why = True, "restarted on attempt %d" % n
                    break
                why = "adb shell rc=%s" % p.returncode
            except Exception as e:      # noqa: BLE001 - a re-arm failure must not take the desk down
                why = "%s: %s" % (type(e).__name__, str(e)[:80])
        res[host] = {"rearmed": started, "why": why, "was": rec["state"]}
        log("wifi watchdog re-arm %s: %s (%s)" % (host, "OK" if started else "FAILED", why))
    return {"ok": True, "nodes": res}


# Frigate detection frames are routed here to the fleet (via `scheduler.place`) rather than
# being load-balanced directly by Frigate against a static IP list. This ensures:
# 1. Shared placement awareness with other consumers (e.g., map orders) to prevent collision.
# 2. Health awareness: dead nodes are excluded automatically via the fleet's live view.
# 3. Centralized node management: adding/retiring tablets is a fleet fact, not config edits.
#
# DESIGN CONSTRAINTS:
# - `scheduler.place()` selects the fleet; this code distributes work across live nodes.
#   Do NOT add host ranking logic here; it must only spread load over known-live peers.
# - Do NOT call `fellow_dispatch` per frame: it probes health on every call (slow).
#   Use the cached live view (`_DETECT_ROUTE`) for low-latency forwarding at 5+ fps.

DETECT_ROUTE_TTL = float(os.environ.get("TABLET_DETECT_ROUTE_TTL", "20"))
DETECT_COOLDOWN = float(os.environ.get("TABLET_DETECT_COOLDOWN", "30"))
DETECT_FWD_TIMEOUT = float(os.environ.get("TABLET_DETECT_FWD_TIMEOUT", "1.2"))

_DETECT_ROUTE = {"live": [], "ts": 0.0, "rr": 0, "cool": {}, "sent": {}, "fail": {}}
_DETECT_ROUTE_LK = threading.Lock()


def _detect_probe_one(host, out, lk, timeout=1.5):
    ip, _via = node_ip(host, DETECT_PORT)
    try:
        with urllib.request.urlopen("http://%s:%d/health" % (ip, DETECT_PORT),
                                    timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
        ok = bool(obj.get("running") or obj.get("ok") or obj.get("port"))
    except Exception:
        ok = False
    with lk:
        out[host] = ok


def detect_live_nodes(force=False):
    """Hosts whose DETECTOR answered recently. Cached; refreshed at most every DETECT_ROUTE_TTL.

    Checks the detector health endpoint (:8801/health), not the agent's /health,
    because Frigate requires the detector to be actively detecting, not just up."""
    now_t = time.time()
    with _DETECT_ROUTE_LK:
        fresh = (now_t - _DETECT_ROUTE["ts"]) < DETECT_ROUTE_TTL
        if fresh and not force:
            return list(_DETECT_ROUTE["live"])
        _DETECT_ROUTE["ts"] = now_t          # claim the refresh so callers do not stampede
    out, lk, threads = {}, threading.Lock(), []
    for h in TABLET_NET:
        t = threading.Thread(target=_detect_probe_one, args=(h, out, lk), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=2.5)
    live = sorted(h for h, ok in out.items() if ok)
    with _DETECT_ROUTE_LK:
        _DETECT_ROUTE["live"] = live
    return list(live)


def _detect_next_host():
    """Round-robin over live, non-cooling nodes. Returns None when the fleet has nothing to offer."""
    live = detect_live_nodes()
    now_t = time.time()
    with _DETECT_ROUTE_LK:
        cool = _DETECT_ROUTE["cool"]
        avail = [h for h in live if cool.get(h, 0) <= now_t]
        if not avail:
            return None
        _DETECT_ROUTE["rr"] = (_DETECT_ROUTE["rr"] + 1) % len(avail)
        return avail[_DETECT_ROUTE["rr"]]


def _detect_mark(host, ok):
    with _DETECT_ROUTE_LK:
        d = _DETECT_ROUTE["sent" if ok else "fail"]
        d[host] = d.get(host, 0) + 1
        if not ok:
            _DETECT_ROUTE["cool"][host] = time.time() + DETECT_COOLDOWN
        else:
            _DETECT_ROUTE["cool"].pop(host, None)


def detect_route(body, content_type, attempts=2):
    """Forward one DeepStack detection request into the fleet. Returns (status, obj).

    Return 503 on failure; never return 200 with an empty list, as Frigate
    cannot distinguish an empty prediction list from a lack of predictions.

    Forward body VERBATIM (bytes + Content-Type) to avoid multipart re-encoding
    mismatches between producer and consumer."""
    if DETECT_KEY:
        # The proxy would have to inject the api_key part into a body it deliberately does not
        # parse. Saying so is honest; silently 401-ing every frame is not.
        return 503, {"success": False,
                     "error": "TABLET_DETECT_KEY is set; the head proxy cannot inject it. "
                              "Point Frigate at the nodes or unset the key."}
    tried = []
    for _ in range(max(1, attempts)):
        host = _detect_next_host()
        if host is None:
            break
        tried.append(host)
        ip, _via = node_ip(host, DETECT_PORT)
        req = urllib.request.Request(
            "http://%s:%d/v1/vision/detection" % (ip, DETECT_PORT),
            data=body, headers={"Content-Type": content_type}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=DETECT_FWD_TIMEOUT) as r:
                obj = json.loads(r.read().decode("utf-8", "replace"))
            _detect_mark(host, True)
            obj["node"] = host          # so a bad prediction can be traced to a node
            return 200, obj
        except Exception as e:
            _detect_mark(host, False)
            last = "%s: %s" % (type(e).__name__, e)
            log("detect_route %s failed (%s)" % (host, last))
    live = detect_live_nodes()
    now_t = time.time()
    with _DETECT_ROUTE_LK:
        cooling = [h for h in live if _DETECT_ROUTE["cool"].get(h, 0) > now_t]
    if not live:
        why = "no fellow has a live detector (:%d/health did not answer)" % DETECT_PORT
    elif tried:
        why = "the %d fellow(s) tried all failed to serve" % len(tried)
    elif cooling:
        why = ("all %d live fellow(s) are cooling off after recent failures" % len(cooling))
    else:
        why = "no fellow was available to try"
    return 503, {"success": False, "error": why, "tried": tried,
                 "live": live, "cooling": cooling}


DETECT_HEAD_PORT = int(os.environ.get("TABLET_DETECT_HEAD_PORT", "8802"))


def _detect_head_app(method, path, body, content_type):
    """Handle synchronous detection requests.

        Uses `ThreadingHTTPServer` to avoid blocking the orchestrator's event loop,
        since detection is a high-throughput, low-latency traffic class (~10/s).

        Does not use the order desk (async submit-now-collect-later) because the
        caller waits synchronously for boxes. Does not use `scheduler.place()`
        because that targets the whole fellowship; route selection per frame is
        handled by `detect_route`.
    """
    path = (path or "").split("?", 1)[0]

    if method == "POST" and path == "/v1/vision/detection":
        return detect_route(body, content_type)

    if method == "GET" and path in ("/health", "/status"):
        st = detect_route_status()
        st["ok"] = True
        st["role"] = "tolkien-detect-shim"
        st["port"] = DETECT_HEAD_PORT
        return 200, st

    if method == "GET" and path == "/bench":
        # Live concurrency decoy. The serialisation bug that made this shim necessary is invisible
        # to unit tests -- mocking the forward removes the very blocking that causes it -- so the
        # only honest check is wall time under concurrency. Pings node health, not the detector,
        # so it costs the fleet nothing and never pollutes the request tally.
        t0 = time.time()
        ths = [threading.Thread(target=detect_live_nodes, args=(True,), daemon=True)
               for _ in range(10)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=10)
        wall = round(time.time() - t0, 3)
        return 200, {"n": 10, "wall_seconds": wall,
                     "verdict": "concurrent" if wall < 1.5 else "SERIALISED"}

    return 404, {"success": False, "error": "no such endpoint"}


def detect_head_serve(port=None):
    """Start the DeepStack detection server on its own thread.

    Returns (started, why).

    Frigate connects via `http://tablet-foundry:8802/v1/vision/detection`.
    No host port publishing needed; containers share a Docker network.

    Must be started by `tablet_runner` to inherit existing supervision,
    avoiding duplicate daemon lifecycle management."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass          # one line per frame at 10 fps would bury every other log line

        def _reply(self, status, obj):
            raw = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            try:
                status, obj = _detect_head_app("GET", self.path, b"", "")
            except Exception as e:                                   # noqa: BLE001
                status, obj = 503, {"success": False,
                                    "error": "%s: %s" % (type(e).__name__, e)}
            self._reply(status, obj)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                status, obj = _detect_head_app("POST", self.path, body,
                                               self.headers.get("Content-Type") or "")
            except Exception as e:                                   # Return 503 on error, not 200 with empty list:
# Frigate distinguishes "empty prediction" from "no response".
# A silent failure would appear as a permanently empty room.
                status, obj = 503, {"success": False,
                                    "error": "%s: %s" % (type(e).__name__, e)}
            self._reply(status, obj)

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port or DETECT_HEAD_PORT), H)
    except Exception as e:                                           # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("tolkien detect shim listening on :%d" % (port or DETECT_HEAD_PORT))
    return True, ""


def detect_route_status():
    """Return router status for display/post-mortem.

    This is a passive cache read; it never probes the fleet. The return value
    must distinguish three states to avoid misleading 'fleet down' errors:
      * Never probed (fresh start): probed=False, age=None, live=[]
      * Probed recently: probed=True
      * Stale probe: probed=True, fresh=False, live reflects last known state
    """
    now_t = time.time()
    with _DETECT_ROUTE_LK:
        ts = _DETECT_ROUTE["ts"]
        probed = ts > 0.0
        age = round(now_t - ts, 1) if probed else None
        cool = {h: round(t - now_t, 1) for h, t in _DETECT_ROUTE["cool"].items() if t > now_t}
        return {"live": list(_DETECT_ROUTE["live"]),
                "probed": probed,
                "age": age,
                "fresh": (age is not None and age <= DETECT_ROUTE_TTL),
                "note": None if probed else
                        "no detection request has been served yet; 'live' is unknown, not empty",
                "cooling": cool,
                "sent": dict(_DETECT_ROUTE["sent"]),
                "failed": dict(_DETECT_ROUTE["fail"]),
                "ttl": DETECT_ROUTE_TTL, "cooldown": DETECT_COOLDOWN,
                "fwd_timeout": DETECT_FWD_TIMEOUT}


# ── AGENT SIDE: the listener ─────────────────────────────────────────────────────────────────────

def _fellow_task_capabilities():
    """Tasks this member can execute now, plus explicit unavailable reasons.

    Registered handlers are code paths in the shared agent; they do not prove native/runtime
    prerequisites exist on this member.  An armv7 tablet therefore remains a normal fellow without
    advertising arm64 detector/decoder work.
    """
    tasks = set(_FELLOW_HANDLERS)
    unavailable = {}
    det_ok, det_why = detect_available()
    if not det_ok:
        tasks.discard("detect")
        unavailable["detect"] = det_why
    burst_missing = []
    if not det_ok:
        burst_missing.append(det_why)
    if not os.path.isfile(HWDEC_BIN) or not os.access(HWDEC_BIN, os.X_OK):
        burst_missing.append("missing executable hwdec (%s)" % HWDEC_BIN)
    if not shutil.which("ffmpeg"):
        burst_missing.append("ffmpeg not installed")
    if burst_missing:
        tasks.discard("burst")
        unavailable["burst"] = "; ".join(burst_missing)
    return sorted(tasks), unavailable


def _fellow_app(method, path, body, auth):
    """Pure request -> (status, obj). Split out from HTTP so it is testable without a socket."""
    tok = fellow_token()
    if not tok or not hmac.compare_digest(auth or "", tok):
        return 401, {"ok": False, "error": "bad or missing token"}

    if method == "GET" and path == "/health":
        tasks, unavailable = _fellow_task_capabilities()
        machine = platform.machine()
        bits = 64 if sys.maxsize > 2 ** 32 else 32
        held, why = _fellow_wakelock_state()
        return 200, {"ok": True, "host": fellow_name(), "role": "fellow",
                     "started": FELLOW_STARTED, "uptime": round(time.time() - FELLOW_STARTED, 1),
                     "pid": os.getpid(), "python": sys.version.split()[0],
                     "tasks": tasks,
                     "capabilities": (["task:%s" % task for task in tasks] +
                                      ["arch:%s" % machine, "runtime:%dbit" % bits]),
                     "runtime": {"machine": machine, "bits": bits},
                     "unavailable_tasks": unavailable,
                     "busy": _FELLOW_LOCK.locked(),
                     "wakelock": held, "wakelock_why": why,
                     "payload_loaded": os.path.basename((_PAYLOAD_CACHE["key"] or ("",))[0]),
                     "detector": detect_status(),
                     # Report `assigned: false` rather than omitting the key;
                     # omission and explicit unassignment must remain distinguishable.
                     "hwdec": hwdec_status(),
                     "stats": dict(_FELLOW_STATS)}

    if method == "POST" and path == "/detect/pause":
        return 200, {"ok": True, "host": fellow_name(), "detector": detect_pause((body or {}).get("why", ""))}

    if method == "POST" and path == "/detect/resume":
        return 200, {"ok": True, "host": fellow_name(), "detector": detect_resume()}

    if method == "POST" and path == "/work":
        task = (body or {}).get("task", "")
        tasks, unavailable = _fellow_task_capabilities()
        fn = _FELLOW_HANDLERS.get(task)
        if not fn or task not in tasks:
            return 400, {"ok": False, "error": "unavailable task %r" % task,
                         "tasks": tasks, "why": unavailable.get(task, "unknown task")}
        # Return 503 if busy; do NOT queue. Scheduling is centralized in HEAD,
        # so silent queuing here causes unbounded latency and hides contention
        # from the router.
        if not _FELLOW_LOCK.acquire(blocking=False):
            _FELLOW_STATS["busy"] += 1
            return 503, {"ok": False, "error": "busy", "host": fellow_name()}
        try:
            t0 = time.time()
            res = fn(body)
            _FELLOW_STATS["served"] += 1
            _FELLOW_STATS["last_task"] = task
            return 200, {"ok": True, "host": fellow_name(), "task": task,
                         "secs": round(time.time() - t0, 3), "result": res}
        except Exception as e:
            _FELLOW_STATS["failed"] += 1
            _FELLOW_STATS["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            # A payload that raises on ONE unit is a property of that unit, not of this node.
            # Reported as a unit failure (200 + ok:false) so the head does not blacklist a
            # perfectly healthy fellow for someone else's bad input.
            return 200, {"ok": False, "host": fellow_name(), "task": task,
                         "error": _FELLOW_STATS["last_error"]}
        finally:
            _FELLOW_LOCK.release()

    return 404, {"ok": False, "error": "no such endpoint"}


def _fellow_wakelock_state():
    """Returns (bool|None, str).

    Returns None if state cannot be determined (e.g., due to permissions,
    empty output), never False which would imply a measured absence."""
    rc, out = sh("termux-wake-lock", timeout=10)
    if rc == 0:
        return True, "termux-wake-lock re-acquired ok"
    return None, "could not confirm (rc=%s) %s" % (rc, out.strip()[:80])


def fellow_serve(port=None, token=None):
    """Run as a FELLOW. Blocks forever.

    ONE UNIT AT A TIME, deliberately. A tablet has 4-8 small cores and the payloads already use
    them all; two concurrent units would not add throughput, they would halve each other and blow
    the latency budget that is the whole point of this path."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    tok = token or fellow_token()
    if not tok:
        log("FELLOW REFUSING TO START: no token (%s missing and TABLET_FELLOW_TOKEN unset). "
            "The 'payload' task runs code, so an open listener is a remote-execution hole."
            % FELLOW_TOKEN_PATH)
        return 2
    os.environ["TABLET_FELLOW_TOKEN"] = tok

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"        # keep-alive: the point is to stop paying setup costs

        def _reply(self, code, obj):
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _auth(self):
            a = self.headers.get("Authorization", "")
            return a[7:].strip() if a.lower().startswith("bearer ") else ""

        def do_GET(self):
            code, obj = _fellow_app("GET", self.path.split("?")[0], None, self._auth())
            self._reply(code, obj)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except Exception as e:
                return self._reply(400, {"ok": False, "error": "bad json: %s" % e})
            code, obj = _fellow_app("POST", self.path.split("?")[0], body, self._auth())
            self._reply(code, obj)

        def log_message(self, *a):
            pass                              # the default logger writes to stderr on every request

    me = fellow_name()
    srv = ThreadingHTTPServer(("0.0.0.0", port or FELLOW_PORT), H)

    # BY DETECTION, never a flag: a node that has the binary and the model serves Frigate, one that
    # has not been provisioned yet says so and still serves ordinary work. An unprovisioned tablet
    # must not open a listener that 503s every frame.
    detect_serve()

    # Same by-detection rule as the detector: a node with the binary AND an assignment decodes; one
    # without either says so in the log and serves ordinary work. Daemon thread, so a wedged
    # supervisor can never hold the agent's shutdown.
    threading.Thread(target=hwdec_supervise, daemon=True).start()

    def _keepawake():
        # The agent is long-lived, so it must defeat doze for its WHOLE life, not just at start.
        # Both suppression mechanisms are documented above in FLEET HEALTH.
        while True:
            _fellow_wakelock_state()
            time.sleep(300)
    threading.Thread(target=_keepawake, daemon=True).start()

    log("fellow@%s listening on :%d  tasks=%s" % (me, port or FELLOW_PORT,
                                                  ",".join(sorted(_FELLOW_HANDLERS))))
    srv.serve_forever()
    return 0


# ── HEAD SIDE: talking to fellows ────────────────────────────────────────────────────────────────

def fellow_url(host, path):
    ip, _via = node_ip(host, FELLOW_PORT)
    return "http://%s:%d%s" % (ip, FELLOW_PORT, path)


def _fellow_request(host, path, body, tok):
    req = urllib.request.Request(fellow_url(host, path), method="POST" if body is not None else "GET")
    req.add_header("Authorization", "Bearer %s" % tok)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    return req, data


def fellow_call(host, path, body=None, timeout=None, token=None):
    """(ok, obj). NEVER raises -- a transport failure is data, like any other node condition."""
    tok = token or fellow_token()
    req, data = _fellow_request(host, path, body, tok)
    try:
        try:
            with urllib.request.urlopen(req, data=data, timeout=timeout or FELLOW_TIMEOUT) as r:
                return True, json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as e:
            # Connect-phase failure on the wire (nothing was sent): pin WiFi and retry once. A
            # response error (HTTPError) or a read timeout is not retried - the node may have acted.
            if isinstance(e, urllib.error.HTTPError) or not (WIRE_PREFIX and "://" + WIRE_PREFIX in req.full_url):
                raise
            _route_failed(host, FELLOW_PORT)
            req, data = _fellow_request(host, path, body, tok)
            with urllib.request.urlopen(req, data=data, timeout=timeout or FELLOW_TIMEOUT) as r:
                return True, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read().decode("utf-8"))
        except Exception:
            return False, {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return False, {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def detect_switch(hosts=None, pause=True, why=""):
    """Tolkien's switch for the camera detector on each fellow: {host: (ok, reply)}. Pause writes the agent's marker
    and stops its resident detector; resume removes it and starts the detector. Run from the head:
    `python tablet_foundry.py --detect-pause [--hosts=a,b] [--why=...]` / `--detect-resume`."""
    hosts = list(hosts or TABLET_NET)
    out, lk, threads = {}, threading.Lock(), []

    def one(h):
        r = fellow_call(h, "/detect/pause" if pause else "/detect/resume", {"why": why}, timeout=60)
        with lk:
            out[h] = r
    for h in hosts:
        t = threading.Thread(target=one, args=(h,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(90)
    return out


def fellow_health(hosts=None, timeout=4.0):
    hosts = list(hosts or TABLET_NET)
    out, lk, threads = {}, threading.Lock(), []

    def one(h):
        ok, obj = fellow_call(h, "/health", timeout=timeout)
        rec = {}
        rec.update(obj if isinstance(obj, dict) else {})
        claimed = rec.get("host")
        # Enforce registry name. `h` is the authoritative key; ignoring `claimed` prevents
        # masking DHCP shuffles where a device answers with an unexpected hostname.
        # (See below: mismatches are logged, not silently ignored.)
        rec["host"] = h
        rec["alive"] = bool(ok and rec.get("ok"))
        if claimed and claimed != h and claimed != "localhost":
            rec["identity_mismatch"] = "registry says %s, device says %s" % (h, claimed)
            log("⚠ IDENTITY MISMATCH at %s: device calls itself %r" % (fellow_url(h, ""), claimed))
        with lk:
            out[h] = rec

    for h in hosts:
        t = threading.Thread(target=one, args=(h,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout + 2)
    for h in hosts:
        out.setdefault(h, {"host": h, "alive": False, "error": "probe thread did not return"})
    return out


def fellow_dispatch(units, task="payload", hosts=None, extra=None, timeout=None,
                    attempts=2, on_result=None):
    """Fan `units` across live fellows and return results ALIGNED TO INPUT ORDER.

    WORK-STEALING, NOT A PRE-SPLIT. Each worker pulls the next unit when it finishes one, so a slow
    or throttled node simply takes fewer. A fixed split hands every node a fixed band, and the lab
    has already been bitten by that shape -- a stable-hash split gave the fleet's FASTEST box zero
    work because the remainder all hashed outside its band, silently.

    THREE FATES, kept separate (a transient condition must never consume the work):
      * node busy / unreachable  -> the unit goes BACK on the queue for someone else
      * unit rejected by payload -> recorded as that unit's failure; not retried forever
      * every node down          -> returns with an explicit error per unit, does not hang
    """
    units = list(units)
    results = [None] * len(units)
    extra = extra or {}

    required = set(extra.get("requires") or [])
    health = fellow_health(hosts)
    live = []
    for host, rec in health.items():
        caps = set(rec.get("capabilities") or [])
        if (rec.get("alive") and task in (rec.get("tasks") or [])
                and required.issubset(caps)):
            live.append(host)
    if not live:
        err = {"ok": False,
               "error": "no fellow reachable or advertises task %r with requirements %s"
                        % (task, sorted(required))}
        return [dict(err, index=i) for i in range(len(units))]

    q = Queue()
    for i, u in enumerate(units):
        q.put((i, u, 0))
    down = set()
    lk = threading.Lock()
    pending = {"n": len(units)}

    def worker(host):
        # Don't exit when queue is empty but pending > 0.
        # A dying worker requeues its unit; if healthy workers
        # have already drained, that unit gets stranded and
        # reported as FAILURE while nodes sit idle.

        # Invariant: a unit is in exactly one of:
        #   - the queue
        #   - a worker's hand
        #   - a results slot
        #
        # Waiting for pending to hit 0 guarantees termination.
        # If all workers die, threads end and the backstop below handles remainder.
        while True:
            with lk:
                if pending["n"] <= 0:
                    return
            try:
                i, unit, tries = q.get(timeout=0.1)
            except Empty:
                continue
            body = dict(extra)
            body["task"] = task
            body.update(unit if isinstance(unit, dict) else {"unit": unit})
            ok, obj = fellow_call(host, "/work", body, timeout=timeout)

            transient = (not ok) or obj.get("error") == "busy"
            if transient and tries + 1 < attempts * max(1, len(live)):
                if not ok:
                    with lk:
                        down.add(host)          # transport failed: stop feeding this one
                q.put((i, unit, tries + 1))
                q.task_done()
                if not ok:
                    return
                time.sleep(0.05)
                continue

            rec = dict(obj if isinstance(obj, dict) else {"ok": False, "error": "non-dict reply"})
            rec["index"], rec["host"] = i, host
            with lk:
                results[i] = rec
                pending["n"] -= 1
            if on_result:
                try:
                    on_result(rec)
                except Exception:
                    pass
            q.task_done()

    threads = [threading.Thread(target=worker, args=(h,), daemon=True) for h in live]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Anything still unassigned means every worker died before draining the queue. Say so per unit
    # rather than returning a short list -- a missing result is indistinguishable from a good one
    # that was never asked for.
    for i, r in enumerate(results):
        if r is None:
            results[i] = {"ok": False, "index": i, "host": None,
                          "error": "no fellow completed this unit (down: %s)"
                                   % (",".join(sorted(down)) or "unknown")}
    return results


def fellow_ensure_token(path=None):
    """Head-side: mint the shared secret once and keep it. Returns the token."""
    import secrets
    p = path or os.path.join(STATE_DIR, "fellow.token")
    try:
        with open(p, encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            return t
    except Exception:
        pass
    os.makedirs(os.path.dirname(p), exist_ok=True)
    t = secrets.token_hex(24)
    with open(p, "w", encoding="utf-8") as f:
        f.write(t)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    return t


def _cli_deploy(hosts=None):
    """Deploy tablet_foundry.py to fellows and restart agents.

    The supervisor in ~/.termux/boot/start-fellow.sh respawns the agent 5s after exit.
    Deploy sequence: copy file, request agent shutdown, let supervisor respawn.

    Note: A file copy alone is insufficient because the agent imports this module once at start.
    Restarting is mandatory to load new code; verify step below confirms success."""
    src = os.path.abspath(__file__)
    out = {}
    for host in (hosts or list(TABLET_NET)):
        rc, err = _scp(host, src, "tablet_foundry.py")
        if rc != 0:
            out[host] = {"ok": False, "error": "scp failed: %s" % err}
            continue
        # kill by NAME, and exclude the observer: a `-f` pattern matches the querying command's own
        # command line, which has bitten this lab at least four times.
        _ssh(host, "pkill -f '[t]ablet_foundry.py --agent'", timeout=30)
        out[host] = {"ok": True, "restarted": True}
    return out


if __name__ == "__main__":
    if "--agent" in sys.argv:
        sys.exit(fellow_serve())

    args = sys.argv[1:]
    hosts = None
    for a in args:
        if a.startswith("--hosts="):
            hosts = [h.strip() for h in a.split("=", 1)[1].split(",") if h.strip()]

    if "--deploy" in args:
        print(json.dumps(_cli_deploy(hosts), indent=2))
    elif "--install-detect" in args:
        print(json.dumps(detect_install(hosts), indent=2))
    elif "--detect-status" in args:
        print(json.dumps(detect_fleet_status(hosts), indent=2))
    elif "--detect-selftest" in args:
        print(json.dumps(detect_fleet_status(hosts, selftest=True), indent=2))
    elif "--detect-pause" in args or "--detect-resume" in args:
        why = next((a.split("=", 1)[1] for a in args if a.startswith("--why=")), "")
        print(json.dumps(detect_switch(hosts, pause="--detect-pause" in args, why=why), indent=2))
    elif "--health" in args:
        print(json.dumps(fellow_health(hosts), indent=2))
    else:
        print(__doc__)
