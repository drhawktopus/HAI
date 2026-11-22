#!/usr/bin/env python3
"""Bring the Fellowship USB segment (10.55.0.0/24) up, and PROVE it is up.

    python usbnet_up.py            # measure only, change nothing  (DEFAULT)
    python usbnet_up.py --apply    # configure, then verify
    python usbnet_up.py --apply --only tablet-1,tablet-2

The segment is documented in the library's Reference/Tablet-USB-Networking-RNDIS.md; the section
numbers below are that doc's.

THE ADDRESS/ROUTE/BRIDGE DO NOT SURVIVE A REBOOT (doc section 4f) -- not the tablet's, not the head's.
That is expected, not a fault. Re-run this after any reboot. The one thing that now DOES survive is
the gadget config: `--apply` sets `persist.sys.usb.config=rndis,adb`, so a rebooted node comes back
advertising rndis and this tool can re-address it over USB instead of needing a physical re-plug.

THE TRAPS, ENCODED RATHER THAN COMMENTED
----------------------------------------
1. NEVER set `sys.usb.config` to `none`. init STOPS adbd rather than restarting it, severing adb
   INCLUDING OVER WIFI, and these builds have no su and no Magisk -- recovery is a PHYSICAL power
   cycle. Guarded in `set_rndis()`.
2. Configure USB over WIFI adb, never over USB adb, so reconfiguring the bus cannot cut the channel
   doing the reconfiguring.
3. EXECUTION ORDER IS THE REVERSE OF THE DOC'S SECTION ORDER: setprop -> wait for gadget -> (Android
   11 only) relink f2 -> wait for rndis0 -> address. Section 4b is a REPAIR OF WHAT 4c PRODUCES; run
   it first on a freshly booted device and the symlink target does not exist yet.
4. IDENTIFY BY SERIAL, NEVER BY MODEL. Every tablet reports `ro.product.model = Phh-Treble vanilla`,
   so software cannot otherwise tell them apart. An unknown serial is REPORTED AND SKIPPED, never
   guessed at.
5. `enx*` NAMES ARE REGENERATED FROM A RANDOM MAC AT EVERY BOOT. Never hard-code one. Bridge by glob
   and ASSERT THE COUNT -- `br0` happily keeps its old members, so a returning node sits outside a
   bridge that looks healthy, with a correct address and a correct route, and is still unreachable.
6. VERIFY THE ROUTE, NOT THE ADDRESS. An address can exist while traffic still leaves over WiFi --
   that is the silent failure mode of section 4f, and it makes a WiFi measurement look like a wired
   one.
7. ENUMERATE OVER ADB, NOT FROM TERMUX. `/sys/class/udc` is Permission denied to the Termux uid, and
   a glob over an unreadable directory expands to nothing -- so the check answers "No such file or
   directory" and reads as "this device has no UDC".
8. THE RNDIS GADGET LIES ABOUT OFFLOAD. It advertises gso/sg the host cannot honour, so frames reach
   the head's br0 with wrong TCP checksums and are dropped -- the node pings (ICMP is unsegmented) but
   every TCP connection over USB fails. This looks exactly like a bad cable. `harden_tablet()` disables it via usb_offload_off.py; it is applied every --apply.
   Autosuspend on the UDC is disabled in the same step so an idle gadget cannot re-enumerate mid-run.

Instruments on this question return confident, well-formed, wrong answers without erroring. Hence:
every negative here is preceded by a
positive control, and a partial segment is REFUSED rather than measured.
"""

import roster
import argparse
import os
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ADB = roster.S.setting("ADB_EXE", "adb")   # the adb binary (the ADB_EXE setting)
RIGBY = roster.S.setting("TABLET_HEAD_NAME", "tablet-head")   # ssh name of the tablet head
SEGMENT = roster.S.usb_segment()   # the USB segment (FELLOWSHIP_USB_SEGMENT)
BRIDGE_IP = f"{SEGMENT}.1/24"

# serial -> (name, USB-segment octet, address): serials are the only reliable identity on the USB segment
ROSTER = {t["serial"]: (n, t["usb_octet"], t["address"]) for n, t in roster.tablets().items() if t.get("usb_octet")}


def run(argv, timeout=45):
    """Run argv as a LIST -- never a shell string. Windows ssh/cmd quoting mangles inline
    one-liners into silent no-ops that exit 0."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def adb(ip, *args, timeout=45):
    return run([ADB, "-s", f"{ip}:5555", *args], timeout=timeout)


def adb_sh(ip, cmd, timeout=45):
    return adb(ip, "shell", cmd, timeout=timeout)


def rigby_sh(cmd, timeout=45):
    return run(["ssh", RIGBY, cmd], timeout=timeout)


# ---------------------------------------------------------------- discovery

def discover():
    """Return [(serial, name, octet, ip)] for roster tablets that adb can actually reach.

    Positive control: adb must list at least one device. If it lists none, that is an INSTRUMENT
    failure (adb server down, no route) and is reported as such -- not as 'no tablets exist'."""
    rc, out, err = run([ADB, "devices"])
    lines = [l for l in out.splitlines()[1:] if "\tdevice" in l]
    if not lines:
        print("!! adb lists ZERO devices. This is the INSTRUMENT failing, not an empty fleet.")
        print(f"   rc={rc} err={err or '<none>'}")
        print("   Try:  adb kill-server && adb connect <ip>:5555")
        return None

    found = []
    for line in lines:
        target = line.split("\t")[0]
        ip = target.split(":")[0]
        rc, ser, _ = adb(ip, "shell", "getprop ro.serialno", timeout=15)
        ser = ser.strip()
        if not ser:
            print(f"   .. {ip}: no serial returned, skipping (could not identify -> will not guess)")
            continue
        if ser not in ROSTER:
            print(f"   .. {ip}: serial {ser} is not on the USB roster -- skipped, never guessed at")
            continue
        nm, octet, _ = ROSTER[ser]
        found.append((ser, nm, octet, ip))
    return sorted(found, key=lambda r: r[2])


# ---------------------------------------------------------------- per-tablet state

def tablet_state(ip):
    """Read, never assume. Returns dict of the four things that decide what to do."""
    _, cfg, _ = adb_sh(ip, "getprop sys.usb.config", timeout=15)
    _, rel, _ = adb_sh(ip, "getprop ro.build.version.release", timeout=15)
    _, nets, _ = adb_sh(ip, "ls /sys/class/net/", timeout=15)
    _, udc, _ = adb_sh(ip, "cat /sys/class/udc/*/state", timeout=15)
    _, addr, _ = adb_sh(ip, "ip -br addr show rndis0", timeout=15)
    _, f2, _ = adb_sh(ip, "readlink /config/usb_gadget/g1/configs/b.1/f2", timeout=15)
    return {
        "cfg": cfg.strip(),
        "android": rel.strip(),
        "has_rndis0": "rndis0" in nets.split(),
        "udc": udc.strip(),
        "addr": addr.strip(),
        # Trap 2: init can link ffs.adb into f2 instead of rndis.gs4. Empty means no f2 at all.
        "f2": f2.strip(),
    }


def route_ok(ip, octet):
    """THE decisive check (trap 6): does traffic for the segment actually LEAVE via rndis0?
    An address without this is the silent failure -- it looks configured and runs over WiFi."""
    _, out, _ = adb_sh(ip, f"ip route get {SEGMENT}.1", timeout=15)
    return "rndis0" in out, out.strip()


# ---------------------------------------------------------------- actions

def set_rndis(ip, cfg_now):
    """Trap 1 lives here. We only ever ADD rndis; we never write a bare 'none'.

    Also sets the PERSIST prop so the gadget config (rndis) survives a reboot. This does NOT make
    the whole segment reboot-proof -- address, route, and the rigby bridge are still runtime-only
    (doc section 4f) -- but it means a node that reboots comes back advertising rndis instead of
    adb-only, so this tool can re-address it over USB rather than needing a physical re-plug. The
    persist target is only ever 'rndis,adb', never a bare 'none' (trap 1 applies to both props)."""
    if "none" == cfg_now.strip():
        print("      !! REFUSING: sys.usb.config is exactly 'none'. Setting it again risks adbd.")
        return False
    # persist first: it is the durable half and safe to (re)assert every apply.
    adb_sh(ip, "setprop persist.sys.usb.config rndis,adb", timeout=20)
    if "rndis" in cfg_now:
        return True
    rc, _, err = adb_sh(ip, "setprop sys.usb.config rndis,adb", timeout=20)
    if rc != 0:
        print(f"      !! setprop failed: {err}")
        return False
    return True


def wait_for(ip, predicate, what, tries=10, delay=2):
    for _ in range(tries):
        if predicate():
            return True
        time.sleep(delay)
    print(f"      !! timed out waiting for {what}")
    return False


def gadget_needs_relink(st):
    """Trap 2: init accepts the property, then links a SECOND adb into f2 instead of rndis.

    Keyed on the symptom, not the Android version -- both 11 and 12 nodes do this.
    """
    return not st["has_rndis0"] and "rndis.gs4" not in st["f2"]


def relink_gadget(ip):
    """Point f2 at rndis.gs4. MUST run detached: unbinding the UDC drops USB and can kill the
    shell that launched it, leaving no gadget at all until reboot."""
    script = (
        "G=/config/usb_gadget/g1; UDC=$(cat $G/UDC); echo '' > $G/UDC; "
        "rm -f $G/configs/b.1/f2; ln -s $G/functions/rndis.gs4 $G/configs/b.1/f2; "
        f"echo \"$UDC\" > $G/UDC; echo RESULT=$? > /data/local/tmp/gadget_fix.log"
    )
    adb_sh(ip, f"setsid sh -c \"{script}\" >/dev/null 2>&1 &", timeout=20)
    time.sleep(6)


def address_tablet(ip, octet):
    a = f"{SEGMENT}.{octet}"
    cmds = [
        f"ip addr replace {a}/24 dev rndis0",
        "ip link set rndis0 up",
        f"ip route replace {SEGMENT}.0/24 dev rndis0 src {a} table 55",
        f"ip rule add to {SEGMENT}.0/24 lookup 55 pref 500",
        f"ip rule add from {SEGMENT}.0/24 lookup 55 pref 501",
    ]
    for c in cmds:
        adb_sh(ip, c, timeout=20)  # rule add is idempotent-ish; duplicates are harmless


OFFLOAD_SCRIPT = os.path.join((roster.S.setting_json("CODE_MIRRORS", {}) or {}).get("tolkien", ""),
                              "usb_offload_off.py")   # one copy: Tolkien's, from its source folder
# Termux ships the only python3 with ctypes/fcntl on these GSI builds; there is no `ethtool` binary.
TERMUX_PY = "/data/data/com.termux/files/usr/bin/python3"


def harden_tablet(ip):
    """Two runtime hardenings that keep the wire correct, applied every --apply:

    1. USB autosuspend OFF on the UDC. The mt6765 controllers otherwise autosuspend the gadget under
       idle, forcing a re-enumeration that drops rndis0 mid-run. Nodes whose UDC does not expose
       power/control (the msm8937 tablets) silently no-op -- not applicable, not a failure.
    2. TX offload (gso/sg) OFF on rndis0. Without it the gadget emits wrong TCP checksums and every
       TCP connection over USB fails while ICMP still passes -- see usb_offload_off.py. It turns a
       node that is 'pingable but no ssh' into a working one; it looks exactly like a bad cable.
    """
    adb_sh(ip, "for f in /sys/class/udc/*/device/power/control; do echo on > $f 2>/dev/null; done",
           timeout=20)
    if not os.path.isfile(OFFLOAD_SCRIPT):
        print(f"      !! offload script missing: {OFFLOAD_SCRIPT} -- gso/sg NOT disabled")
        return
    rc, _, err = adb(ip, "push", OFFLOAD_SCRIPT, "/data/local/tmp/usb_offload_off.py", timeout=30)
    if rc != 0:
        print(f"      !! could not push offload script: {err} -- gso/sg NOT disabled")
        return
    rc, out, err = adb_sh(ip, f"{TERMUX_PY} /data/local/tmp/usb_offload_off.py rndis0", timeout=25)
    if rc != 0 or "after:" not in out:
        print(f"      !! offload-off did not confirm (rc={rc}): {(out or err).splitlines()[-1:]}")


# ---------------------------------------------------------------- rigby bridge

def bridge_state():
    _, present, _ = rigby_sh("ls /sys/class/net/ | grep -c '^enx' || true")
    _, members, _ = rigby_sh("ls /sys/class/net/br0/brif/ 2>/dev/null | wc -l")
    _, br0, _ = rigby_sh("ip -br addr show br0 2>/dev/null || true")
    return int(present.strip() or 0), int(members.strip() or 0), br0.strip()


def bridge_up():
    """Run the root half via run_remote.py, which passes the credential on sudo's stdin.

    rigby has no NOPASSWD rule and no root ssh key, so `ssh rigby "sudo ..."` hangs on an invisible
    password prompt and returns without doing anything -- a silent no-op that reads as success.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fellowship_bridge.sh")
    runner = os.path.join(roster.LAB_BIN, "run_remote.py")   # the lab's tool folder, as roster.py found it
    if not os.path.isfile(script) or not os.path.isfile(runner):
        print(f"      !! cannot run the root half: missing {script if not os.path.isfile(script) else runner}")
        return False
    # Pass the roster's octets so the bridge script verifies exactly the nodes this tool knows
    # about. A hardcoded list on either side is how "6 of 6 OK" gets printed on a 7-node segment.
    octets = ",".join(str(o) for _, o, _ in sorted(ROSTER.values(), key=lambda r: r[1]))
    print(f"      .. root half: {os.path.basename(runner)} {RIGBY} "
          f"{os.path.basename(script)} (octets {octets})")
    p = subprocess.run([sys.executable, runner, RIGBY, script, "180", "--args", SEGMENT + ":" + octets],
                       capture_output=True, text=True, timeout=300)
    for line in (p.stdout or "").splitlines():
        print(f"         {line}")
    if p.returncode != 0:
        print(f"      !! root half exited {p.returncode} "
              f"({'INCONCLUSIVE - no DONE marker' if p.returncode == 3 else 'failed'})")
    return p.returncode == 0


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="configure; default is measure-only")
    ap.add_argument("--only", default="", help="comma-separated tablet names")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    mode = "APPLY" if args.apply else "MEASURE-ONLY (nothing will be changed)"
    print(f"=== Fellowship USB segment -- {mode} ===\n")

    tablets = discover()
    if tablets is None:
        return 2
    if only:
        tablets = [t for t in tablets if t[1] in only]
    if not tablets:
        print("!! no roster tablets matched. Nothing to do.")
        return 2

    print(f"roster tablets reachable over WiFi adb: {len(tablets)} of {len(ROSTER)}\n")

    results = {}
    for ser, name, octet, ip in tablets:
        print(f"--- {name} ({ser}) at {ip} -> {SEGMENT}.{octet}")
        st = tablet_state(ip)
        print(f"      cfg={st['cfg']!r} android={st['android']} rndis0={st['has_rndis0']} "
              f"udc={st['udc']!r}")
        print(f"      addr: {st['addr'] or '<none>'}")

        if args.apply:
            if not set_rndis(ip, st["cfg"]):
                results[name] = False
                continue
            if not st["has_rndis0"]:
                # gadget must exist before the A11 repair can target it (trap 3)
                wait_for(ip, lambda: tablet_state(ip)["has_rndis0"], "gadget rebuild", tries=6)
                st = tablet_state(ip)
                if gadget_needs_relink(st):
                    print(f"      .. no rndis0 and f2 -> {st['f2'] or '<absent>'}; relinking gadget")
                    relink_gadget(ip)
                    wait_for(ip, lambda: tablet_state(ip)["has_rndis0"], "rndis0 after relink")
            address_tablet(ip, octet)
            harden_tablet(ip)
            time.sleep(1)

        ok, detail = route_ok(ip, octet)
        # ON THE WIRE needs the LINK, not just the route table: a tablet physically off the head's bus
        # (udc 'not attached', rndis0 DOWN, serial absent on the head) reads "ok" if only the route is checked
        st = tablet_state(ip)
        link_up = (st["addr"] or "").split()[1:2] == ["UP"]
        attached = st["udc"] not in ("not attached", "", None)
        results[name] = ok and link_up and attached
        print(f"      route -> {'rndis0 OK' if ok else 'NOT via rndis0'}  [{detail}]")
        print(f"      link {'UP' if link_up else 'DOWN'}, udc {st['udc']!r} -> "
              f"{'ON THE WIRE' if results[name] else 'NOT on the wire'}\n")

    present, members, br0 = bridge_state()
    print(f"--- rigby bridge: enx present={present} br0 members={members} br0={br0 or '<absent>'}")
    if args.apply:
        bridge_up()
        present, members, br0 = bridge_state()
        print(f"      after: enx present={present} br0 members={members} br0={br0 or '<absent>'}")

    bridge_ok = present > 0 and present == members
    if not bridge_ok:
        print(f"      !! BRIDGE MISMATCH: {present} enx interfaces, {members} enslaved. "
              "A node outside br0 has a correct address, a correct route, and is unreachable.")

    for ser, (nm, _o, _ip) in ROSTER.items():     # a roster tablet adb could not reach is NOT verified - never left out
        results.setdefault(nm, False)
    good = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n=== {good} of {total} tablets verified ON THE WIRE; bridge "
          f"{'OK' if bridge_ok else 'MISMATCH'} ===")
    for n, v in sorted(results.items()):
        print(f"    {'ok ' if v else '!! '} {n}")

    if good != total or not bridge_ok:
        print("\n!! PARTIAL SEGMENT. Do not take comparative measurements on it -- a partial "
              "segment is not a comparable test (doc section 4f).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
