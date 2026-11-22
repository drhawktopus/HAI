"""Push the L1 timer to the tablet and run it there. Completes the latency breakdown."""
import json
import statistics
import sys

sys.path.insert(0, "/app")
import tablet_foundry as tf  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: run_l1.py <tablet>  (its roster name)")
N = int(sys.argv[2]) if len(sys.argv) > 2 else 15
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

tf._scp(HOST, "/ssdshared/tabfoundry-bench/ctrl_cat.jpg", "lat.jpg")
tf._scp(HOST, "/tmp/lat_on_tablet.py", "lat_on_tablet.py")
rc, out = tf._ssh(HOST, "python3 ~/lat_on_tablet.py %d" % N, timeout=300)

ts = None
for line in (out or "").splitlines():
    line = line.strip()
    if line.startswith("["):
        try:
            ts = json.loads(line)
        except Exception:
            pass
if not ts:
    print("L1 STILL FAILED. raw:")
    print((out or "")[:600])
    sys.exit(1)

ts = sorted(ts)
print("L1 on-tablet localhost (no network), n=%d" % len(ts))
print("   median %6.1f ms   min %6.1f   max %6.1f" % (statistics.median(ts), ts[0], ts[-1]))
tf._ssh(HOST, "rm -f ~/lat.jpg ~/lat_on_tablet.py", timeout=30)
print("cleaned up")
