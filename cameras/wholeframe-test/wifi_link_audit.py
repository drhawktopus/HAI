"""Why does frodo drop off? Compare the WiFi LINK of every tablet against its peers.

 Agreed, and the lab's own doctrine says the same -- when a story
drifts toward "the hardware is unreliable" the fault has been mine every time.

What is already RULED OUT: doze whitelist and wake-lock are ON for all five (node_health), and
frodo self-recovered, so it is not a permanent misconfiguration and not a dead device.

What is NOT yet measured: the radio link. If frodo sits at a materially worse RSSI, or on a
different band/AP from its peers, then "flaky" is really "marginal signal" -- an environment and
settings problem with an actual fix (move it, or steer it to the other band), not a device fault.

Reports per node: RSSI, band/frequency, BSSID (which AP), link speed, and device uptime. Uptime
matters because a REBOOT and a WiFi BLIP look identical from the network side and have completely
different causes.

 Several of these sources are permission-gated for an ordinary app. Each is tried and its
failure is REPORTED, never silently skipped -- an unmeasured field must not render as a good one.
"""
import json
import sys

sys.path.insert(0, "/app")
import tablet_foundry as tf  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROBES = [
    ("termux-api", "termux-wifi-connectioninfo 2>/dev/null"),
    ("proc-wireless", "cat /proc/net/wireless 2>/dev/null"),
    ("dumpsys", "dumpsys wifi 2>/dev/null | grep -i -m4 'rssi\\|frequency\\|bssid'"),
    ("uptime", "cat /proc/uptime 2>/dev/null"),
    ("ip-addr", "ip -4 addr show 2>/dev/null | grep -m2 'inet '"),
]

for host in sorted(tf.TABLET_NET):
    print("\n=== %s (%s) ===" % (host, tf.TABLET_NET[host][0]))
    for label, cmd in PROBES:
        rc, out = tf._ssh(host, cmd, timeout=45)
        out = (out or "").strip()
        if rc != 0 or not out:
            print("   %-14s UNAVAILABLE (rc=%s)" % (label, rc))
            continue
        if label == "termux-api":
            try:
                d = json.loads(out)
                print("   %-14s rssi=%s dBm  freq=%s MHz  link=%s Mbps  bssid=%s  ssid=%s"
                      % (label, d.get("rssi"), d.get("frequency_mhz") or d.get("frequency"),
                         d.get("link_speed_mbps") or d.get("link_speed"),
                         d.get("bssid"), d.get("ssid")))
                continue
            except Exception:
                pass
        if label == "uptime":
            try:
                secs = float(out.split()[0])
                print("   %-14s %.1f hours since boot" % (label, secs / 3600.0))
                continue
            except Exception:
                pass
        for line in out.splitlines()[:4]:
            print("   %-14s %s" % (label, line.strip()[:110]))
