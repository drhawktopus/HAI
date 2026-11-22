"""Tablet control plane for Tolkien: reboot / usb-restore / status, over a token-gated
localhost HTTP surface.

Control is adb-root over WiFi (the container reaches each tablet on `<ip>:5555`); a rebooted
node's adbd comes up as uid 2000 and every gadget write is SELinux-refused until `adb root`, so
that step is load-bearing. The host-side bridge (new `enx` -> `br0`, ARP flush) is handled by
rigby's fellowship-bridge service, so this module never needs `br0`/NET_ADMIN. The TABLET-side TX
offload (gso/sg on rndis0) is this module's job: class A's gadget comes up with it on, and the frames
it sends then arrive with bad TCP checksums (usb_offload_off.py). It reuses
tablet_foundry's roster and adb helpers rather than defining a second source of truth.

Endpoints (bind is TOLKIEN_CTL_BIND, default 127.0.0.1; set 0.0.0.0 to serve the admin/backend
layer LAN + tailnet — the token is then the only gate; every request carries `X-Tolkien-Token`):
    GET  /fellowship/status[?target=all|<group>|<node,...>]
    POST /fellowship/reboot       {"target": "...", "restore": true}
    POST /fellowship/usb-restore  {"target": "..."}
    POST /fellowship/off          {"target": "..."}    # power off; no remote power-on after

Gadget rebuild follows Tablet-RNDIS-Setup §4c (A12 setprop) and §4b (configfs relink: always on A11,
on A12 whenever the config still links no rndis function). Never sets sys.usb.config to `none`.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tablet_foundry as tf

# Segment octet within the wire subnet (tf.WIRE_PREFIX, the TABLET_WIRE_PREFIX setting).
SEG_OCTET = tf.WIRE_OCTET        # one table: the address book owns the wire octets
# "all" is every tablet in the roster; "class-a" and so on are the tablets of each hardware class (TABLET_CLASSES), in
# that setting's order. No classes set = "all" only.
GROUPS = {"all": list(tf.TABLET_NET)}
for _name, _cls in tf.TABLET_CLASSES.items():
    GROUPS.setdefault("class-" + _cls.lower(), []).append(_name)
IP = "/system/bin/ip"          # `ip` is not on the default tablet PATH; the full path is (doc §4c).
OFFLOAD_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usb_offload_off.py")
OFFLOAD_REMOTE = "/data/local/tmp/usb_offload_off.py"
TERMUX_PY = "/data/data/com.termux/files/usr/bin/python3"   # the only python3 on the GSI builds


def _adb(ip, *args, timeout=45):
    return tf.sh_argv([tf.ADB, "-s", "%s:5555" % ip, *args], timeout=timeout)


def _adb_sh(ip, cmd, timeout=45):
    return _adb(ip, "shell", cmd, timeout=timeout)[1].strip()


def _adb_root_sh(ip, cmd, timeout=45):
    """`_adb_sh` as ROOT. Setsid processes are invisible to adb's shell uid 2000; only root can kill them."""
    if not _ensure_root(ip):
        return "NOROOT"
    return _adb_sh(ip, cmd, timeout=timeout)


def _adb_ip(name):
    """USB preferred over WiFi for standard commands.
    usb_restore and reboot use WiFi to avoid circular dependency on the USB link."""
    return tf.node_ip(name, 5555, refresh=True)[0]


def _connect(ip):
    tf.sh_argv([tf.ADB, "connect", "%s:5555" % ip], timeout=15)


def _uid(ip):
    out = _adb_sh(ip, "id -u", timeout=15)
    return out if out in ("0", "2000") else "?"


def _ensure_root(ip, wait=120):
    """Restart adbd as root, waiting out a node still finishing boot. Returns True at uid 0."""
    # `adb root` restarts adbd, dropping the WiFi adb connection; the first check after it often races the restart. Ask once, then keep reconnecting and re-checking until root or the budget runs out.
    t0 = time.time()
    asked = False
    while time.time() - t0 < wait:
        _connect(ip)
        u = _uid(ip)
        if u == "0":
            return True
        if u == "2000" and not asked:
            _adb(ip, "root", timeout=20)
            asked = True
        time.sleep(5)
    return False


def _android(ip):
    v = _adb_sh(ip, "getprop ro.build.version.release", timeout=15)
    try:
        return int(v.split(".")[0])
    except (ValueError, IndexError):
        return 0


RELINK = ("G=/config/usb_gadget/g1; U=$(cat $G/UDC); "
          "mkdir -p $G/functions/rndis.gs4; echo '' > $G/UDC; "
          "rm -f $G/configs/b.1/f2; ln -s $G/functions/rndis.gs4 $G/configs/b.1/f2; "
          "echo $U > $G/UDC")


def _gadget_slots(ip):
    """Function names linked into the gadget config (["ffs.adb", "rndis.gs4"]); None = cannot read."""
    out = _adb_sh(ip, "cd /config/usb_gadget/g1/configs/b.1 && echo SLOTS && "
                      "for f in f*; do [ -L $f ] && readlink $f; done", timeout=15)
    lines = out.splitlines()
    if "SLOTS" not in lines:
        return None
    return [l.strip().rsplit("/", 1)[-1] for l in lines[lines.index("SLOTS") + 1:] if l.strip()]


def _build_gadget(ip, android):
    """Bring an rndis function onto the gadget: A12 setprop first, then the §4b configfs relink on any
    version whose config still links no rndis function.

    The setprop alone cannot repair class A (mt6765, A12): its init rc only `symlink`s and only the
    `none` action removes links, so once f1 holds ffs.adb the rndis symlink fails "File exists"."""
    if android < 12:
        _adb_sh(ip, RELINK, timeout=25)
        time.sleep(3)
        return
    _adb_sh(ip, "setprop sys.usb.config rndis,adb", timeout=20)
    time.sleep(3)
    if "rndis" not in _adb_sh(ip, "getprop sys.usb.config", timeout=15):
        return                     # the property did not take; usb_restore reports it, no relink on top
    slots = _gadget_slots(ip)
    if slots is not None and not any(s.startswith("rndis") for s in slots):
        _adb_sh(ip, RELINK, timeout=25)
        time.sleep(3)


def _address(ip, octet):
    """Tablet-side RNDIS addressing + policy routing (§4c), idempotent. Needs a wire subnet (TABLET_WIRE_PREFIX)."""
    if not tf.WIRE_PREFIX:
        raise RuntimeError("no wire subnet is set (TABLET_WIRE_PREFIX): the tablet keeps its WiFi route only")
    _adb_sh(ip, "%(ip)s addr replace %(pfx)s%(n)d/24 dev rndis0; "
                "%(ip)s link set rndis0 up; "
                "%(ip)s route replace %(cidr)s dev rndis0 src %(pfx)s%(n)d table 55; "
                "%(ip)s rule del to %(cidr)s lookup 55 pref 500 2>/dev/null; "
                "%(ip)s rule add to %(cidr)s lookup 55 pref 500; "
                "%(ip)s rule del from %(cidr)s lookup 55 pref 501 2>/dev/null; "
                "%(ip)s rule add from %(cidr)s lookup 55 pref 501"
                % {"ip": IP, "n": octet, "pfx": tf.WIRE_PREFIX, "cidr": tf.WIRE_CIDR}, timeout=30)


def _offload(ip, fix=False):
    """rndis0 TX offload: "on" | "off" | "?" (cannot tell). With fix=True, turn gso/sg off first."""
    if _adb(ip, "push", OFFLOAD_LOCAL, OFFLOAD_REMOTE, timeout=30)[0] != 0:
        return "?"
    if fix:
        _adb_sh(ip, "%s %s rndis0" % (TERMUX_PY, OFFLOAD_REMOTE), timeout=25)
    out = _adb_sh(ip, "%s %s rndis0 --check" % (TERMUX_PY, OFFLOAD_REMOTE), timeout=25)
    for word in ("on", "off"):
        if out.startswith("offload: %s " % word):
            return word
    return "?"


def status(name):
    ip = _adb_ip(name)
    octet = SEG_OCTET.get(name)
    _connect(ip)
    u = _uid(ip)
    if u == "?":
        return {"node": name, "adb": "unreachable", "wifi": ip}
    rndis = _adb_sh(ip, "%s -o -4 addr show rndis0 2>/dev/null | awk '{print $4}'" % IP) or "-"
    state = _adb_sh(ip, "cat /sys/class/net/rndis0/operstate 2>/dev/null") or "-"
    # The ioctl needs root; as uid 2000 the answer is "?", never a guess.
    offload = _offload(ip) if (u == "0" and rndis != "-") else "?"
    return {"node": name, "adb": "root" if u == "0" else "shell",
            "android": _android(ip),
            "usb_config": _adb_sh(ip, "getprop sys.usb.config") or "?",
            "rndis0": rndis, "rndis0_state": state.strip().lower(), "octet": octet,
            "offload": offload, "udc_state": _udc_state(ip)}


def _udc_state(ip):
    """The tablet's USB device controller as the kernel sees it ("configured", "not attached", ...); "?" = cannot
    tell. World-readable, so it works as uid 2000 too."""
    return (_adb_sh(ip, "cat /sys/class/udc/*/state 2>/dev/null", timeout=15) or "?").strip().lower() or "?"


def _no_host(ip):
    """True if the gadget is bound to the UDC but the state is 'not attached' (ready, no host enumerating). None if indeterminate. Requires root; callers must ensure root via _ensure_root."""
    out = _adb_sh(ip, "echo BOUND=$(cat /config/usb_gadget/g1/UDC 2>/dev/null); "
                      "echo STATE=$(cat /sys/class/udc/*/state 2>/dev/null)", timeout=15)
    kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    if "BOUND" not in kv or "STATE" not in kv:
        return None
    return bool(kv["BOUND"].strip()) and kv["STATE"].strip().lower() == "not attached"


def usb_restore(name):
    ip = tf.TABLET_NET[name][0]
    octet = SEG_OCTET[name]
    if not _ensure_root(ip):
        return {"node": name, "ok": False, "why": "could not get adb root"}
    android = _android(ip)
    # Re-attach the rndis function only when the interface is actually down: on A11 the relink
    # unbinds the UDC, which would bounce a link that is already working (its usb.config prop never
    # says rndis, so the prop cannot be the test). Offload alone never rebuilds the gadget.
    if _iface_down(status(name)) is not False:
        _build_gadget(ip, android)
        cfg = _adb_sh(ip, "getprop sys.usb.config")
        if android >= 12 and (cfg == "none" or not cfg):
            return {"node": name, "ok": False, "why": "gadget did not build (config=%r, A%s)"
                    % (cfg, android)}
    _address(ip, octet)
    _offload(ip, fix=True)
    after = status(name)
    # Same derivation as the keeper's detector (one idea, one implementation).
    ok = usb_link_down(after) is False
    out = {"node": name, "ok": ok, "usb_config": after.get("usb_config"), "rndis0": after.get("rndis0", "-"),
           "rndis0_state": after.get("rndis0_state"), "offload": after.get("offload"),
           "android": android}
    if not ok:
        out["gadget_slots"] = _gadget_slots(ip)
        if _no_host(ip):
            out["no_host"] = True
            out["why"] = ("no USB host: the gadget is built and bound but the tablet's USB controller reads 'not "
                          "attached' - nothing on the cable is enumerating it. Not a tablet problem: check its cable "
                          "and rigby's port (rigby's kernel log names the port). A tablet-side restore cannot fix it.")
    return out


def reboot(name, restore=True):
    ip = tf.TABLET_NET[name][0]
    _connect(ip)
    _adb(ip, "reboot", timeout=20)
    t0 = time.time()
    back = False
    while time.time() - t0 < 180:
        time.sleep(5)
        _connect(ip)
        if _uid(ip) in ("0", "2000"):
            back = True
            break
    if not back:
        return {"node": name, "ok": False, "why": "did not return within 180s"}
    _ensure_root(ip)
    out = {"node": name, "ok": True, "boot_s": int(time.time() - t0)}
    if restore:
        out["restore"] = usb_restore(name)
        out["ok"] = bool(out["restore"].get("ok"))
    return out


def off(name):
    """Power the node off. It will NOT return remotely -- there is no WoL over adb/USB, so a
    physical power button (or a charge-triggered boot) is needed to bring it back."""
    ip = _adb_ip(name)
    if not _ensure_root(ip):
        return {"node": name, "ok": False, "why": "could not get adb root"}
    _adb(ip, "shell", "reboot -p", timeout=15)
    return {"node": name, "ok": True, "off": True,
            "note": "powered off; needs physical power-on to return"}


# --- USB link keeper -----------------------------------------------------------------------------
# A node whose USB gadget comes up without rndis (any reboot that did not go through reboot(), e.g.
# the Softserve Termux re-armer's raw `adb reboot`) is off the wire until someone runs usb_restore.
# Presence cannot come from USB enumeration: with sys.usb.config=none the gadget is unbound and the
# node disappears from rigby's USB bus, so the evidence is the node's own status over WiFi adb.
USB_KEEP_EVERY_S = float(os.environ.get("TOLKIEN_USB_KEEP_EVERY", "300"))
USB_KEEP_CONFIRM = int(os.environ.get("TOLKIEN_USB_KEEP_CONFIRM", "2"))
USB_KEEP_COOLDOWN_S = float(os.environ.get("TOLKIEN_USB_KEEP_COOLDOWN", "1800"))
RUNNER_IDLE = threading.Event()      # set by the order taker only while it has nothing to run
_KEEP = {"down": {}, "acted": {}, "no_host": {}}   # no_host: node -> the udc_state a failed restore saw
_KEEP_LOCK = threading.Lock()


def _iface_down(st):
    """True if the RNDIS interface is down or missing the node's segment address. Note: Do not rely on sys.usb.config for interface status; on the A11 configfs path it remains "adb" even when the interface is active. An address present on a DOWN interface is not valid."""
    if st.get("adb") not in ("root", "shell"):
        return None
    if st.get("rndis0_state") != "up":
        return True
    octet = st.get("octet")
    if octet is not None and not str(st.get("rndis0") or "").startswith("%s%d/" % (tf.WIRE_PREFIX, octet)):
        return True
    return False


def usb_link_down(st):
    """One status() reading -> True (link down), False (up), None (cannot tell: no adb).

    Down = the interface is down, OR its TX offload is on: an up interface with gso/sg on pings and
    handshakes but corrupts every large frame the tablet sends. Offload "?" is not evidence either
    way, so it leaves the interface verdict standing."""
    down = _iface_down(st)
    if down is False and st.get("offload") == "on":
        return True
    return down


def keep_usb_links(names=None, now_t=None, status_fn=None, restore_fn=None, idle=None):
    """One keeper pass. Acts only on a node seen down on USB_KEEP_CONFIRM consecutive passes, at
    most once per USB_KEEP_COOLDOWN_S per node, and only while `idle()` is true at the moment of
    acting. "Cannot tell" neither counts as down nor clears a count. Returns [(node, action, detail)]."""
    now_t = time.time() if now_t is None else now_t
    names = list(names or GROUPS["all"])
    status_fn = status_fn or status
    restore_fn = restore_fn or usb_restore
    idle = idle or RUNNER_IDLE.is_set
    out = []
    with _KEEP_LOCK:
        for st in _fanout(names, status_fn):
            n = st.get("node")
            down = usb_link_down(st)
            if down is None:
                continue
            if not down:
                _KEEP["down"].pop(n, None)
                _KEEP["no_host"].pop(n, None)
                continue
            seen = _KEEP["down"].get(n, 0) + 1
            _KEEP["down"][n] = seen
            detail = "usb_config=%s rndis0=%s offload=%s" % (st.get("usb_config"), st.get("rndis0"),
                                                             st.get("offload", "?"))
            # A restore already proved there is no USB host: while the controller still reads the state it read
            # then, another restore cannot help - wait quietly for the cable/port to change it.
            if n in _KEEP["no_host"]:
                if st.get("udc_state") == _KEEP["no_host"][n]:
                    out.append((n, "no-host-waiting", detail + " udc=%s" % st.get("udc_state")))
                    continue
                _KEEP["no_host"].pop(n, None)              # the host side changed: back to the normal path
            if seen < USB_KEEP_CONFIRM:
                out.append((n, "seen", detail))
                continue
            last = _KEEP["acted"].get(n)
            if last is not None and now_t - last < USB_KEEP_COOLDOWN_S:
                out.append((n, "cooldown", detail))
                continue
            if not idle():
                out.append((n, "deferred-busy", detail))
                continue
            _KEEP["acted"][n] = now_t
            _KEEP["down"][n] = 0
            r = restore_fn(n)
            if not r.get("ok") and r.get("no_host"):
                _KEEP["no_host"][n] = st.get("udc_state")
                out.append((n, "no-usb-host", r))
                continue
            out.append((n, "restored" if r.get("ok") else "restore-failed", r))
    return out


# A node stuck at "Charging" without battery gain is considered stalled after a 2-hour window.
# Normal charging rates are 7-11%/h on 500mA ports, so 2h with no gain is anomalous and indicates a failure.
# "Charging" status implies the node is active and expected to gain charge; a dead node would not report "Charging".
CHARGE_STALL_S = float(os.environ.get("TOLKIEN_CHARGE_STALL_S", "7200"))
CHARGE_COOLDOWN_S = float(os.environ.get("TOLKIEN_CHARGE_COOLDOWN", "86400"))
CHARGE_FRESH_S = 600.0     # telemetry idle period is 10 s; a window must reach this close to both of its ends
_CHARGE = {"acted": {}, "reported": {}}


def charge_stalled(rows, now_t, window_s=None):
    """rows = [(ts, run, battery_pct, charging)] for ONE node, any order. True = stalled (every reading "Charging",
    below 100 %, no gain over the window), False = not stalled, None = cannot tell (window not covered, no readings, or
    an order ran in it - a node under load may lose charge honestly)."""
    window_s = CHARGE_STALL_S if window_s is None else window_s
    rows = sorted(r for r in rows if r[0] >= now_t - window_s and r[2] is not None)
    if not rows or rows[0][0] > now_t - window_s + CHARGE_FRESH_S or rows[-1][0] < now_t - CHARGE_FRESH_S:
        return None
    if any(r[1] for r in rows):
        return None
    if any(r[3] != "Charging" for r in rows):
        return False
    pct = [r[2] for r in rows]
    return pct[0] < 100 and max(pct) <= pct[0]


def charge_rows(names, since):
    """{node: [(ts, run, battery_pct, charging)]} from Tolkien's telemetry store (read-only)."""
    import sqlite3
    import tolkien_telemetry as tt
    db = sqlite3.connect("file:%s?mode=ro" % tt.DB, uri=True, timeout=30)
    try:
        out = {n: [] for n in names}
        for node, ts, run, pct, ch in db.execute(
                "SELECT node, ts, run, battery_pct, charging FROM samples WHERE ts > ? AND proc IS NULL "
                "AND node IN (%s)" % ",".join("?" * len(names)), [since] + list(names)):
            out[node].append((ts, run, pct, ch))
        return out
    finally:
        db.close()


def keep_charge(names=None, now_t=None, rows_fn=None, reboot_fn=None, idle=None):
    """One charge-keeper pass. A stalled node is rebooted (USB link restored by reboot()) only while `idle()` is true, at
    most once per CHARGE_COOLDOWN_S; still stalled inside the cooldown -> reported once ("not-cured"), never rebooted
    again. Returns [(node, action, detail)]."""
    now_t = time.time() if now_t is None else now_t
    names = list(names or GROUPS["all"])
    rows_fn = rows_fn or charge_rows
    reboot_fn = reboot_fn or reboot
    idle = idle or RUNNER_IDLE.is_set
    rows = rows_fn(names, now_t - CHARGE_STALL_S)
    out = []
    for n in names:
        r = [x for x in rows.get(n, []) if x[0] >= now_t - CHARGE_STALL_S and x[2] is not None]
        if charge_stalled(r, now_t) is not True:
            continue
        detail = "Charging at %s%% with no gain for %d min" % (min(x[2] for x in r), CHARGE_STALL_S // 60)
        last = _CHARGE["acted"].get(n)
        if last is not None and now_t - last < CHARGE_COOLDOWN_S:
            if _CHARGE["reported"].get(n) != last:
                _CHARGE["reported"][n] = last
                out.append((n, "not-cured", detail))
            continue
        if not idle():
            out.append((n, "deferred-busy", detail))
            continue
        _CHARGE["acted"][n] = now_t
        res = reboot_fn(n)
        out.append((n, "rebooted" if res.get("ok") else "reboot-failed", dict(res, why_reboot=detail)))
    return out


def keeper_loop(log, notify, every=None):
    """Background thread body: one pass every `every` seconds; never raises out."""
    every = USB_KEEP_EVERY_S if every is None else every
    while True:
        time.sleep(every)
        if not RUNNER_IDLE.is_set():
            continue
        try:
            for node, action, detail in keep_usb_links():
                if action == "seen":
                    log("USB keeper: %s link down (%s) - confirming on the next pass" % (node, detail))
                elif action in ("cooldown", "deferred-busy", "no-host-waiting"):
                    log("USB keeper: %s link still down (%s) - %s" % (node, detail, action))
                elif action == "no-usb-host":
                    msg = "USB keeper: %s has NO USB HOST - %s (restores paused until its controller state changes)" % (
                        node, detail.get("why", ""))
                    log(msg)
                    notify(msg)
                else:
                    msg = "USB keeper: %s %s - %s" % (node, action, json.dumps(detail)[:300])
                    log(msg)
                    notify(msg)
        except Exception as e:                                           # noqa: BLE001
            log("USB keeper pass error (%s: %s) - orders unaffected" % (type(e).__name__, e))
        try:
            for node, action, detail in keep_charge():
                if action == "deferred-busy":
                    log("charge keeper: %s %s - deferred, an order is running" % (node, detail))
                    continue
                if action == "not-cured":
                    msg = ("charge keeper: %s still not charging after a reboot (%s) - needs its cable/port checked "
                           "at the tablet" % (node, detail))
                else:
                    msg = "charge keeper: %s %s - %s" % (node, action, json.dumps(detail)[:300])
                log(msg)
                notify(msg)
        except Exception as e:                                           # noqa: BLE001
            log("charge keeper pass error (%s: %s) - orders unaffected" % (type(e).__name__, e))


def keeper_start(log, notify):
    threading.Thread(target=keeper_loop, args=(log, notify), daemon=True).start()
    return ("USB keeper every %ds (confirm %d passes, cooldown %ds, idle only); charge keeper: reboot a node "
            "'Charging' with no gain for %ds (once per %ds, idle only)" % (
                USB_KEEP_EVERY_S, USB_KEEP_CONFIRM, USB_KEEP_COOLDOWN_S, CHARGE_STALL_S, CHARGE_COOLDOWN_S))


def _resolve(target):
    if target in GROUPS:
        return GROUPS[target]
    return [t.strip() for t in (target or "").split(",") if t.strip()]


def _fanout(nodes, fn):
    """Run fn per node concurrently; bounds wall time to the slowest single node."""
    out = {}
    threads = []
    for n in nodes:
        t = threading.Thread(target=lambda nn: out.__setitem__(nn, fn(nn)), args=(n,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=600)
    return [out.get(n, {"node": n, "ok": False, "why": "timeout"}) for n in nodes]


def control_app(method, path, body, token):
    """Token-gated dispatch. Returns (http_status, obj). Fail-closed if no token configured."""
    expected = os.environ.get("TOLKIEN_CTL_TOKEN", "")
    if not expected:
        return 503, {"ok": False, "error": "control token not configured"}
    if not token or token != expected:
        return 401, {"ok": False, "error": "unauthorized"}
    raw_path = path or ""
    path, _, query = raw_path.partition("?")
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            body = ""
    if method == "GET" and path == "/fellowship/status":
        from urllib.parse import parse_qs
        target = (parse_qs(query).get("target") or ["all"])[0]
        nodes = _resolve(target)
        bad = [n for n in nodes if n not in tf.TABLET_NET]
        if not nodes or bad:
            return 400, {"ok": False, "error": "unknown or empty target: %r" % target}
        return 200, {"ok": True, "nodes": _fanout(nodes, status)}
    if method == "POST" and path in ("/fellowship/reboot", "/fellowship/usb-restore", "/fellowship/off"):
        try:
            req = json.loads(body or "{}")
        except (ValueError, TypeError):
            req = {}
        target = req.get("target") or req.get("node") or "all"
        nodes = _resolve(target)
        bad = [n for n in nodes if n not in tf.TABLET_NET]
        if not nodes or bad:
            return 400, {"ok": False, "error": "unknown or empty target: %r" % target}
        if path.endswith("reboot"):
            restore = bool(req.get("restore", True))
            results = _fanout(nodes, lambda n: reboot(n, restore=restore))
        elif path.endswith("/off"):
            results = _fanout(nodes, off)
        else:
            results = _fanout(nodes, usb_restore)
        return 200, {"ok": all(r.get("ok", False) for r in results), "results": results}
    return 404, {"ok": False, "error": "no such control endpoint"}


def control_serve(port=None, token=None):
    """Start the control server on 127.0.0.1 (localhost only). Fail-closed without a token.

    Returns (started, why). Started by tablet_runner to inherit its supervision."""
    port = port or int(os.environ.get("TOLKIEN_CTL_PORT", "8740"))
    token = token if token is not None else os.environ.get("TOLKIEN_CTL_TOKEN", "")
    if not token:
        return False, "TOLKIEN_CTL_TOKEN not set - refusing to serve tablet control unauthenticated"

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _reply(self, status, obj):
            raw = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _tok(self):
            return self.headers.get("X-Tolkien-Token", "")

        def do_GET(self):
            try:
                s, o = control_app("GET", self.path, b"", self._tok())
            except Exception as e:                                       # noqa: BLE001
                s, o = 503, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            self._reply(s, o)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n) if n else b""
                s, o = control_app("POST", self.path, data, self._tok())
            except Exception as e:                                       # noqa: BLE001
                s, o = 503, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            self._reply(s, o)

    # Bind is env-configurable (no rebuild to change reach). Default 127.0.0.1 is the safe fallback;
    # TOLKIEN_CTL_BIND=0.0.0.0 lets any admin/backend-layer device call it with the shared token
    # (LAN + tailnet; no WAN — rigby has no port-forwards). The token is the only gate.
    bind = os.environ.get("TOLKIEN_CTL_BIND", "127.0.0.1")
    try:
        srv = ThreadingHTTPServer((bind, port), H)
    except Exception as e:                                               # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return True, "tablet control on %s:%d" % (bind, port)
