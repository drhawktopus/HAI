"""WiFi link audit via `adb shell` (uid 2000), NOT via Termux (uid 10144) and NOT via root.

Why adb shell: the Termux app uid is denied /proc/net/wireless, dumpsys and even /proc/uptime on
these PHH GSIs, so every on-device probe came back UNAVAILABLE. The `shell` uid can read all of
them. Root is not required for ANY of this.

 DELIBERATELY NOT RUNNING `adb root`. The library records that `adb root` restarts Termux --
a node can lose its WAKE-LOCK that way and crawl at 8-12x, and sshd deaths coincide with
`adb root` calls (`Fellowship-Node-Provisioning.md`; history in the archived cluster handoff).
The nodes serve the Fellowship detector, so a restart takes detection down to prove a point that
uid 2000 can prove for free.

Read-only: every command here is a getter.
"""
import os
import re
import subprocess
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # the cameras folder
from cam_settings import ADB, tablets

NODES = tablets()   # every network tablet in the Fellowship roster
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def sh(ip, cmd, t=40):
    try:
        p = subprocess.run([ADB, "-s", "%s:5555" % ip, "shell", cmd],
                           capture_output=True, text=True, timeout=t)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return "ERR %r" % e


def band(freq):
    try:
        f = int(freq)
    except Exception:  # noqa: BLE001
        return "?"
    return "2.4GHz" if f < 3000 else ("5GHz" if f < 6000 else "6GHz")


print("%-9s %-6s %-8s %-8s %-9s %-19s %s" % (
    "node", "RSSI", "band", "freq", "link", "BSSID (which AP)", "uptime"))
print("-" * 92)
rows = []
for host in sorted(NODES):
    ip = NODES[host]
    info = sh(ip, "dumpsys wifi | grep -m1 -A0 'mWifiInfo'")
    if "mWifiInfo" not in info:
        info = sh(ip, "cmd wifi status")
    rssi = re.search(r"RSSI:\s*(-?\d+)", info)
    freq = re.search(r"Frequency:\s*(\d+)", info)
    link = re.search(r"Link speed:\s*(\d+)", info)
    bssid = re.search(r"BSSID:\s*([0-9a-fA-F:]{17}|<none>)", info)
    up = sh(ip, "cat /proc/uptime")
    try:
        hrs = "%.1f h" % (float(up.split()[0]) / 3600.0)
    except Exception:  # noqa: BLE001
        hrs = "?"
    r = (host, rssi.group(1) if rssi else "?", band(freq.group(1)) if freq else "?",
         freq.group(1) if freq else "?", link.group(1) if link else "?",
         bssid.group(1) if bssid else "?", hrs)
    rows.append(r)
    print("%-9s %-6s %-8s %-8s %-9s %-19s %s" % r)

print("\n=== power-save / sleep settings ===")
for host in sorted(NODES):
    ip = NODES[host]
    ps = sh(ip, "settings get global wifi_sleep_policy").strip()
    scan = sh(ip, "settings get global wifi_scan_always_enabled").strip()
    pson = sh(ip, "cmd wifi get-softap-supported-features >/dev/null 2>&1; "
                  "dumpsys wifi | grep -m1 -i 'power save\\|PowerSave'").strip()
    print("  %-9s wifi_sleep_policy=%-8s scan_always=%-6s %s"
          % (host, ps[:8] or "unset", scan[:6] or "?", pson[:60]))

print("\n=== do any two nodes share an AP? (BSSID groups) ===")
groups = {}
for host, _rssi, _b, _f, _l, bssid, _u in rows:
    groups.setdefault(bssid, []).append(host)
for bssid, hosts in groups.items():
    print("  %-19s %s" % (bssid, ", ".join(sorted(hosts))))
