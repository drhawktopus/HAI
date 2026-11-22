"""Measure each tablet's RADIO LINK from the network side. Runs on the SUNDAE HOST (needs ping).

On-device WiFi stats are permission-gated on these GSI builds (/proc/net/wireless, dumpsys and
/proc/uptime all return Permission denied to an ordinary app), so asking the tablet is a dead end.
Measuring reachability from the network needs no Android permission at all and answers the actual
question: is frodo's link materially worse than its peers?

Packet loss and RTT jitter are what a marginal WiFi link looks like. A device that is simply
"flaky" for no reason does not exist; a device at the edge of coverage, or on a congested band,
loses packets and shows it here.

 Sequential, not parallel: five simultaneous ping floods would contend for the same air and
measure each other. One node at a time, same count, same interval.
 Reports loss AND jitter AND max separately -- a link can have 0% loss and still be unusable if
its RTT spikes to seconds, and averaging those together hides exactly that.
"""
import os
import re
import subprocess
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # the cameras folder
from cam_settings import tablets

COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 60
NODES = tablets()   # every network tablet in the Fellowship roster
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("ping %d packets per node, 0.3s apart, sequential\n" % COUNT)
rows = []
for host in sorted(NODES):
    ip = NODES[host]
    try:
        p = subprocess.run(["ping", "-c", str(COUNT), "-i", "0.3", "-W", "2", ip],
                           capture_output=True, text=True, timeout=COUNT * 2 + 60)
        out = p.stdout
    except Exception as e:  # noqa: BLE001
        print("  %-9s ping failed: %r" % (host, e))
        continue
    loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", out)
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
    loss_pct = float(loss.group(1)) if loss else None
    if rtt:
        mn, avg, mx, mdev = (float(rtt.group(i)) for i in range(1, 5))
    else:
        mn = avg = mx = mdev = None
    rows.append((host, loss_pct, mn, avg, mx, mdev))
    print("  %-9s loss %5s%%   rtt min/avg/max %s/%s/%s ms   jitter(mdev) %s ms"
          % (host,
             ("%.1f" % loss_pct) if loss_pct is not None else "?",
             ("%.1f" % mn) if mn else "?", ("%.1f" % avg) if avg else "?",
             ("%.0f" % mx) if mx else "?", ("%.1f" % mdev) if mdev else "?"))

good = [r for r in rows if r[1] is not None]
if len(good) >= 2:
    print("\n=== COMPARISON ===")
    worst_loss = max(good, key=lambda r: r[1])
    worst_jit = max((r for r in good if r[5] is not None), key=lambda r: r[5], default=None)
    print("  most packet loss : %s (%.1f%%)" % (worst_loss[0], worst_loss[1]))
    if worst_jit:
        print("  most jitter      : %s (%.1f ms mdev)" % (worst_jit[0], worst_jit[5]))
    lossy = [r[0] for r in good if r[1] and r[1] > 1.0]
    print("  nodes with >1%% loss: %s" % (", ".join(lossy) if lossy else "none"))
    print("\n  -> %s" % (
        "a node stands out; its link is the explanation, not 'flakiness'" if lossy else
        "all links look clean RIGHT NOW; the dropout is INTERMITTENT, so a single clean "
        "sweep does NOT clear it -- this needs a long soak to catch the event"))
