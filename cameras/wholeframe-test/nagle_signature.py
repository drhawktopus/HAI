"""Is the head's 75 ms a Nagle / delayed-ACK stall? Look at the DISTRIBUTION, not the median.

Hypothesis: neither HTTP server (the head on :8802, the fellow's detector on :8801) sets
TCP_NODELAY -- BaseHTTPRequestHandler.disable_nagle_algorithm defaults to False -- and `_reply`
writes headers and body as two separate socket writes. Nagle then holds the second write until
the first is ACKed, and the peer's delayed-ACK timer fires on a ~40 ms quantum.

The tell is BIMODALITY, not a raised average: some requests coalesce and cost nothing, others pay
a fixed ~40 ms. A median alone cannot distinguish "uniformly slower" (a compute cost) from
"sometimes stalls by a fixed quantum" (a protocol artefact), and those have completely different
fixes. L3's min was 147.7 ms against a 220 ms median, which already smells like this.

Prints every sample so the shape is visible and my conclusion is falsifiable.
"""
import json
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, "/app")
import tablet_foundry as tf  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: nagle_signature.py <tablet>  (its roster name)")
N = 30
IMG = "/ssdshared/tabfoundry-bench/ctrl_cat.jpg"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
with open(IMG, "rb") as f:
    JPEG = f.read()
ip = tf.TABLET_NET[HOST][0]


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


for label, url in (("L2 direct to tablet:8801", "http://%s:8801/v1/vision/detection" % ip),
                   ("L3 through head  :8802", "http://127.0.0.1:8802/v1/vision/detection")):
    ts = []
    for _ in range(N):
        try:
            ts.append(post(url))
        except Exception as e:  # noqa: BLE001
            print("   error %r" % e)
    if not ts:
        continue
    s = sorted(ts)
    print("\n=== %s   n=%d ===" % (label, len(s)))
    print("   median %.1f  min %.1f  max %.1f" % (statistics.median(s), s[0], s[-1]))
    print("   all: %s" % " ".join("%.0f" % x for x in s))
    # 40 ms buckets make a delayed-ACK quantum visible if it is there
    buckets = {}
    for x in s:
        buckets[int(x // 40) * 40] = buckets.get(int(x // 40) * 40, 0) + 1
    print("   40ms buckets: %s" % ", ".join(
        "%d-%d:%d" % (k, k + 40, v) for k, v in sorted(buckets.items())))
    fast = [x for x in s if x < statistics.median(s) - 20]
    print("   %d/%d samples are >20ms FASTER than the median%s"
          % (len(fast), len(s),
             "  <-- bimodal: consistent with a fixed stall, not a compute cost" if
             len(fast) >= len(s) * 0.2 else ""))
