"""Extract every WiFi disconnect/reconnect from each tablet's own WiFi state machine history.

frodo's log showed a NETWORK_DISCONNECTION_EVENT at 08-10 19:23:29 (reason=1, rssi=-62, screen
off) and a reconnect at 19:53:28 -- a 30-minute outage. The previous CONNECTION event was
08-10 07:23:28, exactly TWELVE HOURS before the disconnect. That is a timer, not bad luck, and
timers have causes.

 The comparison across nodes is what makes this diagnostic rather than anecdotal: if every node
deauths on the same cadence and only frodo is slow to come back, the fault is Android's
screen-off reconnect backoff. If only frodo deauths, it is the client or the AP's view of it.
One node's log cannot tell those apart, which is why this reads all five.

Read-only via adb shell (uid 2000). No root, no `adb root` -- see wifi_adb_audit.py for why.
"""
import os
import re
import subprocess
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # the cameras folder
from cam_settings import ADB, tablets

NODES = tablets()   # every network tablet in the Fellowship roster
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

for host in sorted(NODES):
    ip = NODES[host]
    try:
        p = subprocess.run(
            [ADB, "-s", "%s:5555" % ip, "shell",
             "dumpsys wifi | grep -E 'NETWORK_DISCONNECTION_EVENT|NETWORK_CONNECTION_EVENT'"],
            capture_output=True, text=True, timeout=90)
        out = p.stdout or ""
    except Exception as e:  # noqa: BLE001
        print("%-9s ERR %r" % (host, e))
        continue

    evs = []
    for line in out.splitlines():
        m = re.search(r"time=(\d\d-\d\d \d\d:\d\d:\d\d)", line)
        if not m:
            continue
        kind = "DISCONNECT" if "DISCONNECTION" in line else "connect"
        rs = re.search(r"reason=(\d+)", line)
        rssi = re.search(r"rssi=(-?\d+)", line)
        evs.append((m.group(1), kind, rs.group(1) if rs else "",
                    rssi.group(1) if rssi else ""))
    evs.sort()
    print("\n=== %s === (%d events in the ring buffer)" % (host, len(evs)))
    prev_conn = None
    for t, kind, reason, rssi in evs:
        extra = ""
        if kind == "DISCONNECT":
            extra = "  reason=%s rssi=%s" % (reason or "?", rssi or "?")
            if prev_conn:
                extra += "   (was up since %s)" % prev_conn
        else:
            prev_conn = t
        print("   %s  %-10s%s" % (t, kind, extra))

    # Outage durations: a DISCONNECT followed by the next connect.
    outs = []
    for i, (t, kind, _r, _s) in enumerate(evs):
        if kind != "DISCONNECT":
            continue
        nxt = next((e for e in evs[i + 1:] if e[1] == "connect"), None)
        if nxt:
            outs.append((t, nxt[0]))
    if outs:
        print("   -> outages:")
        for a, b in outs:
            print("        %s -> %s" % (a, b))
