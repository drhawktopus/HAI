#!/usr/bin/env python3
"""Fellowship tablet control: status / reboot / usb-restore for the tablets in the roster.

The control plane is adb-root over WiFi; the work/segment plane is ssh:8022 over br0.
They are separate: reboot and gadget rebuild REQUIRE adb as uid 0, which ssh (the Termux
uid) cannot provide. `adb root` first is load-bearing — a rebooted node's adbd comes up as
uid 2000, and every setprop/gadget write is then silently refused by SELinux (reads as a
"wedged controller" if you skip it). Segment addressing/bridge/offload are delegated to
usbnet_up.py --only, never reimplemented here.

Usage:
    python fellowship_ctl.py status [node|group|all]
    python fellowship_ctl.py reboot <node|group|all> [--restore]
    python fellowship_ctl.py usb-restore <node|group|all>
    python fellowship_ctl.py boot-recover <node|group|all>   # reboot + usb-restore
Groups: all, and class-a / class-b / class-c from each tablet's class in the roster
"""
import roster
import argparse, json, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADB = str(HERE / "platform-tools" / "adb.exe")
USBNET = str(HERE / "usbnet_up.py")
LOG = HERE / "fellowship_ctl.log"
RIGBY = roster.S.setting("TABLET_HEAD_NAME", "tablet-head")   # ssh name of the tablet head (br0 + the cluster key)
SEGMENT = roster.S.usb_segment()   # the USB segment (FELLOWSHIP_USB_SEGMENT)
SEG_KEY = "~/.ssh/tabcluster_ed25519"

ROSTER = {n: dict(wifi=t["address"], octet=t.get("usb_octet"), cls=t["class"])
          for n, t in roster.tablets().items() if t.get("class")}
GROUPS = {"all": list(ROSTER)}
for _n, _t in ROSTER.items():
    GROUPS.setdefault("class-" + _t["cls"].lower(), []).append(_n)


def audit(action, node, result, detail=""):
    rec = dict(ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               action=action, node=node, result=result, detail=detail)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def run(cmd, timeout=60):
    # A booting node makes `adb connect`/`adb shell` hang; a timeout must read as
    # "not reachable yet", never crash the caller.
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="timeout")


def adb(ip, *args, timeout=45):
    return run([ADB, "-s", f"{ip}:5555", *args], timeout=timeout)


def adb_sh(ip, cmd, timeout=45):
    return adb(ip, "shell", cmd, timeout=timeout).stdout.strip()


def resolve(target):
    if target in GROUPS:
        return GROUPS[target]
    names = [t.strip() for t in target.split(",") if t.strip()]
    bad = [n for n in names if n not in ROSTER]
    if bad:
        sys.exit(f"unknown node(s): {', '.join(bad)}  (known: {', '.join(ROSTER)})")
    return names


def ensure_connected(ip):
    run([ADB, "connect", f"{ip}:5555"], timeout=15)


def uid_of(ip):
    out = adb_sh(ip, "id -u", timeout=15)
    return out if out in ("0", "2000") else "?"


def ensure_root(ip, wait=120):
    """Restart adbd as root if needed, waiting up to `wait`s for adbd to answer
    (covers a node still finishing boot). Returns True once uid==0."""
    t0 = time.time()
    while time.time() - t0 < wait:
        ensure_connected(ip)
        u = uid_of(ip)
        if u == "0":
            return True
        if u == "2000":
            adb(ip, "root", timeout=20)
            run([ADB, "connect", f"{ip}:5555"], timeout=10)
            adb(ip, "wait-for-device", timeout=30)
            return uid_of(ip) == "0"
        time.sleep(5)
    return False


def android(ip):
    v = adb_sh(ip, "getprop ro.build.version.release", timeout=15)
    try:
        return int(v.split(".")[0])
    except (ValueError, IndexError):
        return 0


def seg_ping(octet):
    r = run(["ssh", RIGBY, f"ping -c1 -W2 {SEGMENT}.{octet} >/dev/null 2>&1 && echo UP || echo DOWN"], timeout=25)
    return "UP" if "UP" in r.stdout else "DOWN"


def status_one(name):
    ip, octet = ROSTER[name]["wifi"], ROSTER[name]["octet"]
    ensure_connected(ip)
    uid = uid_of(ip)
    if uid == "?":
        print(f"  {name:8s} cls-{ROSTER[name]['cls']}  adb=UNREACHABLE (wifi {ip})")
        return
    a = android(ip)
    cfg = adb_sh(ip, "getprop sys.usb.config", timeout=15) or "?"
    rndis = adb_sh(ip, "ip -o -4 addr show rndis0 2>/dev/null | awk '{print $4}'", timeout=15) or "-"
    models = adb_sh(ip, "ls /data/data/com.termux/files/home/llm/*.gguf 2>/dev/null | wc -l", timeout=15) or "0"
    seg = seg_ping(octet)
    root = "root" if uid == "0" else "shell"
    print(f"  {name:8s} cls-{ROSTER[name]['cls']} A{a}  adb={root:5s} usb.config={cfg:12s} rndis0={rndis:15s} seg({SEGMENT}.{octet})={seg}  models={models}")


def reboot_one(name, restore=False):
    ip = ROSTER[name]["wifi"]
    print(f"[reboot] {name} ({ip}) ...", flush=True)
    ensure_connected(ip)
    adb(ip, "reboot", timeout=20)
    t0 = time.time()
    # TCP adb: the connection drops on reboot; poll re-connect until adbd answers again.
    back = False
    while time.time() - t0 < 180:
        time.sleep(5)
        run([ADB, "connect", f"{ip}:5555"], timeout=10)
        if uid_of(ip) in ("0", "2000"):
            back = True
            break
    if not back:
        audit("reboot", name, "FAIL", "did not return within 180s")
        print(f"  {name}: did not come back within 180s")
        return False
    rooted = ensure_root(ip)
    boot = int(time.time() - t0)
    audit("reboot", name, "OK", f"boot={boot}s root={rooted}")
    print(f"  {name}: back in ~{boot}s, adb root={rooted}. USB segment is DOWN until usb-restore.")
    if restore:
        return usb_restore_one(name)
    return True


def usb_restore_one(name):
    ip, octet, cls = ROSTER[name]["wifi"], ROSTER[name]["octet"], ROSTER[name]["cls"]
    print(f"[usb-restore] {name} ({ip}) ...", flush=True)
    if not ensure_root(ip):
        audit("usb-restore", name, "FAIL", "could not get adb root")
        print(f"  {name}: FAILED to get adb root (control plane unavailable)")
        return False
    a = android(ip)
    cfg = adb_sh(ip, "getprop sys.usb.config", timeout=15)
    if "rndis" not in cfg:
        # Build the gadget. A12 = setprop method; A11 relink is handled by usbnet_up.
        # rndis,adb keeps adbd alive (never set 'none').
        adb_sh(ip, "setprop sys.usb.config rndis,adb", timeout=20)
        adb(ip, "wait-for-device", timeout=20)
        cfg = adb_sh(ip, "getprop sys.usb.config", timeout=15)
        if cfg == "none" or not cfg:
            audit("usb-restore", name, "FAIL", f"config stuck at '{cfg}' after setprop (A{a})")
            print(f"  {name}: gadget did not build (config='{cfg}', A{a}) - inspect /config/usb_gadget")
            return False
    # Segment plane: address + bridge + offload + verify, delegated to the sanctioned tool.
    seg = run([sys.executable, USBNET, "--apply", "--only", name], timeout=300)
    ok = seg_ping(octet) == "UP"
    tail = "\n".join(seg.stdout.strip().splitlines()[-3:])
    audit("usb-restore", name, "OK" if ok else "FAIL", f"seg_ping={'UP' if ok else 'DOWN'}")
    print(f"  {name}: usb.config={cfg}  segment {SEGMENT}.{octet}={'UP' if ok else 'DOWN'}")
    if not ok:
        print("  --- usbnet_up.py tail ---\n  " + tail.replace("\n", "\n  "))
    return ok


def main():
    ap = argparse.ArgumentParser(description="Fellowship tablet control")
    ap.add_argument("action", choices=["status", "reboot", "usb-restore", "boot-recover"])
    ap.add_argument("target", nargs="?", default="all", help="node | group | all")
    ap.add_argument("--restore", action="store_true", help="reboot: also usb-restore after boot")
    a = ap.parse_args()
    nodes = resolve(a.target)

    if a.action == "status":
        print(f"== Fellowship status ({len(nodes)} node{'s' if len(nodes)!=1 else ''}) ==")
        for n in nodes:
            status_one(n)
        return

    ok = True
    for n in nodes:
        if a.action == "reboot":
            ok &= reboot_one(n, restore=a.restore)
        elif a.action == "usb-restore":
            ok &= usb_restore_one(n)
        elif a.action == "boot-recover":
            ok &= reboot_one(n, restore=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
