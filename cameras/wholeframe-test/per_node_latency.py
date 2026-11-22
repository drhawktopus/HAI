"""Per-node detector latency, measured DIRECTLY at each tablet's :8801.

Correcting my own confound: I measured L2 against gandalf ONLY, L3 through the head (which
round-robins all five), and attributed the 75 ms difference to "head overhead". The ordered L3
samples then showed a repeating fast/slow/medium cycle -- the signature of cycling through nodes
of DIFFERENT speeds, not of a per-request cost in the head.

So the question is not "what does the head add" but "how much do the fellows differ", and the
head's apparent cost is mostly the slow members of the pool. If the spread is large that is the
actual finding, and it has a different fix entirely (fix or retire the slow node) from anything
to do with routing.

Same image, same count, every node, sequential so they do not contend with each other.
"""
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, "/app")
import tablet_foundry as tf  # noqa: E402

N = 12
IMG = "/ssdshared/tabfoundry-bench/ctrl_cat.jpg"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
with open(IMG, "rb") as f:
    JPEG = f.read()


def post(url):
    b = "----lat"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
             "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % b).encode()
            + JPEG + ("\r\n--%s--\r\n" % b).encode())
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % b})
    t = time.time()
    urllib.request.urlopen(req, timeout=60).read()
    return (time.time() - t) * 1000.0


print("per-node, direct to :8801, n=%d each, sequential\n" % N)
meds = {}
for host in sorted(tf.TABLET_NET):
    ip = tf.TABLET_NET[host][0]
    ts = []
    for _ in range(N):
        try:
            ts.append(post("http://%s:8801/v1/vision/detection" % ip))
        except Exception as e:  # noqa: BLE001
            print("  %-8s error %r" % (host, e))
            break
    if not ts:
        continue
    s = sorted(ts)
    meds[host] = statistics.median(s)
    print("  %-8s median %6.1f  min %6.1f  max %6.1f   | %s"
          % (host, statistics.median(s), s[0], s[-1], " ".join("%.0f" % x for x in ts)))

if meds:
    lo, hi = min(meds.values()), max(meds.values())
    print("\n  fastest %.0f ms (%s)   slowest %.0f ms (%s)   spread %.0f ms  (%.1fx)"
          % (lo, min(meds, key=meds.get), hi, max(meds, key=meds.get), hi - lo, hi / lo))
    print("  pool mean of medians: %.0f ms  <- this is what routing through the head should cost"
          % statistics.mean(meds.values()))
