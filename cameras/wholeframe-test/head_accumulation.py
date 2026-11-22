"""Does the head get SLOWER the longer it runs? Order-preserved timing + thread/socket census.

The previous run sorted its samples and so destroyed the one thing that mattered: L3's median went
220 ms (n=15) to 279 ms (n=30) within minutes, while the direct-to-tablet path stayed flat at
~143 ms. A fixed protocol stall does not do that; accumulation does.

Hypothesis: the head sets `protocol_version = "HTTP/1.1"`, which means keep-alive by DEFAULT, but
urllib opens a NEW connection per request and never sends Connection: close. ThreadingHTTPServer
spawns a thread per connection, and each finished request leaves its thread parked in readline()
waiting for a follow-up request that never comes. Threads and sockets pile up, and the head slows.

If true this is a real defect, not a curiosity: Frigate drives ~6 detections/sec through this
door continuously, so anything that accumulates per request accumulates forever.

Prints latency IN ORDER plus the container's thread and socket count before/after, so the claim
is falsifiable rather than a story that fits.
"""
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, "/app")
import tablet_foundry as tf  # noqa: E402

N = 40
IMG = "/ssdshared/tabfoundry-bench/ctrl_cat.jpg"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
with open(IMG, "rb") as f:
    JPEG = f.read()


def census():
    """Threads in this process and sockets held, straight from /proc."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            th = [l.split()[1] for l in f if l.startswith("Threads:")][0]
    except Exception:
        th = "?"
    try:
        import os
        socks = sum(1 for fd in os.listdir("/proc/self/fd")
                    if (os.readlink("/proc/self/fd/" + fd) or "").startswith("socket:"))
    except Exception:
        socks = "?"
    return th, socks


def post(url, close_hdr=False):
    b = "----lat"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
             "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % b).encode()
            + JPEG + ("\r\n--%s--\r\n" % b).encode())
    h = {"Content-Type": "multipart/form-data; boundary=%s" % b}
    if close_hdr:
        h["Connection"] = "close"
    req = urllib.request.Request(url, data=body, headers=h)
    t = time.time()
    urllib.request.urlopen(req, timeout=60).read()
    return (time.time() - t) * 1000.0


URL = "http://127.0.0.1:8802/v1/vision/detection"
print("head process census BEFORE: threads=%s sockets=%s" % census())

for label, close_hdr in (("default (keep-alive implied)", False),
                         ("with Connection: close", True)):
    ts = []
    for _ in range(N):
        try:
            ts.append(post(URL, close_hdr))
        except Exception as e:  # noqa: BLE001
            print("  error %r" % e)
    if not ts:
        continue
    print("\n=== L3 %s ===" % label)
    print("   IN ORDER: %s" % " ".join("%.0f" % x for x in ts))
    first, last = ts[:10], ts[-10:]
    print("   first10 median %.1f   last10 median %.1f   drift %+.1f ms"
          % (statistics.median(first), statistics.median(last),
             statistics.median(last) - statistics.median(first)))
    print("   census now: threads=%s sockets=%s" % census())
