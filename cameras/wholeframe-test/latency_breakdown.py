"""WHERE DO THE 200 ms GO? Decompose the detection round-trip. Runs inside tablet-foundry.


Fair -- documented raw inference on these tablets is ~93 ms but Frigate sees 220-380 ms, so
130-280 ms has never been accounted for and I have been quoting it as if it were normal.

The same detector is reachable at three levels, so the difference between them IS the breakdown:

  L1 on the tablet, to 127.0.0.1:8801 -> inference + the fellow's own HTTP/multipart handling,
                                            NO network at all
  L2 from this container, to tablet:8801 -> L1 + one WiFi hop
  L3 from this container, to head:8802 -> L2 + Tolkien's health-cache lookup, node pick and
                                            blocking forward

  L2 - L1 = the network. L3 - L2 = the head. L1 = the node's own cost.

 ONE IMAGE, byte-identical at every level, or this measures JPEG size instead of transport.
 Run only while the fleet is IDLE. Timing a tablet that is also serving Frigate measures
contention, and a disrupted run is not a measurement.
 Report median AND min: min approximates the uncontended floor, median what is really paid.
"""
import json
import statistics
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "/app")
import tablet_foundry as tf  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: latency_breakdown.py <tablet>  (its roster name)")
N = int(sys.argv[2]) if len(sys.argv) > 2 else 15
IMG = "/ssdshared/tabfoundry-bench/ctrl_cat.jpg"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open(IMG, "rb") as f:
    JPEG = f.read()
print("image: %s (%d bytes)  host: %s  n=%d per level" % (IMG, len(JPEG), HOST, N))
ip = tf.TABLET_NET[HOST][0]

# Put the SAME bytes on the tablet for L1.
tf._scp(HOST, IMG, "lat.jpg")
rc, out = tf._ssh(HOST, "stat -c%s ~/lat.jpg", timeout=30)
print("pushed to tablet: %s bytes (want %d)" % ((out or "").strip(), len(JPEG)))


def post(url, jpeg):
    b = "----lat"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
             "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % b).encode()
            + jpeg + ("\r\n--%s--\r\n" % b).encode())
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % b})
    t0 = time.time()
    r = urllib.request.urlopen(req, timeout=60)
    obj = json.loads(r.read().decode("utf-8", "replace"))
    return (time.time() - t0) * 1000.0, obj


def summarise(label, ms):
    if not ms:
        print("  %-38s (no samples)" % label)
        return None
    ms = sorted(ms)
    print("  %-38s median %6.1f ms   min %6.1f   max %6.1f"
          % (label, statistics.median(ms), ms[0], ms[-1]))
    return statistics.median(ms)


# ── L1: on the tablet itself, no network ────────────────────────────────────────────────────────
# Timed BY THE TABLET so the ssh round-trip is not counted. Uses its own python.
L1_SCRIPT = (
    "python3 -c \""
    "import json,time,urllib.request;"
    "d=open('/data/data/com.termux/files/home/lat.jpg','rb').read();"
    "b='----lat';"
    "body=('--%s\\r\\nContent-Disposition: form-data; name=\\\"image\\\"; "
    "filename=\\\"x.jpg\\\"\\r\\nContent-Type: image/jpeg\\r\\n\\r\\n'%%b).encode()+d+"
    "('\\r\\n--%s--\\r\\n'%%b).encode();"
    "ts=[];"
    "\nfor i in range(" + str(N) + "):\n"
    "    r=urllib.request.Request('http://127.0.0.1:8801/v1/vision/detection',data=body,"
    "headers={'Content-Type':'multipart/form-data; boundary='+b});"
    "t=time.time();urllib.request.urlopen(r,timeout=60).read();ts.append((time.time()-t)*1000)\n"
    "print(json.dumps(ts))\"")
rc, out = tf._ssh(HOST, L1_SCRIPT, timeout=300)
l1 = []
for line in (out or "").splitlines():
    line = line.strip()
    if line.startswith("["):
        try:
            l1 = json.loads(line)
        except Exception:
            pass
if not l1:
    print("L1 FAILED -- raw output follows (an unmeasured level must not be silently skipped):")
    print("   " + (out or "")[:400].replace("\n", "\n   "))

# ── L2 / L3: from this container ────────────────────────────────────────────────────────────────
l2, l3 = [], []
for _ in range(N):
    try:
        t, _o = post("http://%s:8801/v1/vision/detection" % ip, JPEG)
        l2.append(t)
    except Exception as e:  # noqa: BLE001
        print("  L2 error %r" % e)
for _ in range(N):
    try:
        t, _o = post("http://127.0.0.1:8802/v1/vision/detection", JPEG)
        l3.append(t)
    except Exception as e:  # noqa: BLE001
        print("  L3 error %r" % e)

# ── network floor, for reference ────────────────────────────────────────────────────────────────
try:
    p = subprocess.run(["ping", "-c", "10", "-q", ip], capture_output=True, text=True, timeout=60)
    rtt = [l for l in p.stdout.splitlines() if "rtt" in l or "round-trip" in l]
except Exception as e:  # noqa: BLE001
    rtt = ["ping unavailable: %r" % e]

print()
print("=== BREAKDOWN (%s) ===" % HOST)
m1 = summarise("L1 on-tablet localhost (no network)", l1)
m2 = summarise("L2 container -> tablet:8801 (+WiFi)", l2)
m3 = summarise("L3 container -> head:8802 (+Tolkien)", l3)
print()
for line in rtt:
    print("  icmp %s" % line.strip())
print()
if m1 and m2 and m3:
    print("  node's own cost          %6.1f ms" % m1)
    print("  network adds             %6.1f ms   (L2-L1)" % (m2 - m1))
    print("  Tolkien head adds        %6.1f ms   (L3-L2)" % (m3 - m2))
    print("  ------------------------------------")
    print("  total through the head   %6.1f ms" % m3)

tf._ssh(HOST, "rm -f ~/lat.jpg", timeout=30)
print("\ncleaned up ~/lat.jpg")
