"""Multi-round detector latency. Runs inside tablet-foundry.

 Correct -- every latency number I quoted was a single round
on Android tablets, where thermal state, doze and background scheduling all drift. A single round
cannot tell a stable property ("sam is slow") from a bad moment ("sam was busy just then"), and I
reported the first as if it were established.

TWO questions, and rounds answer both:
  1. Are the per-node medians STABLE, or did I catch one bad round? -> between-round spread, and
     whether the RANK ORDER of nodes holds. Rank stability is the stronger test: absolute times
     can all drift together while the ordering stays meaningful.
  2. What does the head actually add? -> my first answer (75 ms) compared gandalf-only against a
     five-node round-robin, which is not one variable. Fixed here: the head's response carries
     `node`, so head samples are GROUPED BY the tablet that served them and each node is compared
     AGAINST ITSELF. Same node, same image, with and without the head.

Rounds are separated in time so slow drift is visible rather than averaged away.
"""
import json
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, "/app")
import tablet_foundry as tf  # noqa: E402

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
N_DIRECT = int(sys.argv[2]) if len(sys.argv) > 2 else 10
N_HEAD = int(sys.argv[3]) if len(sys.argv) > 3 else 50
GAP = int(sys.argv[4]) if len(sys.argv) > 4 else 45
IMG = "/ssdshared/tabfoundry-bench/ctrl_cat.jpg"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
with open(IMG, "rb") as f:
    JPEG = f.read()
HOSTS = sorted(tf.TABLET_NET)


def post(url):
    b = "----lat"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
             "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % b).encode()
            + JPEG + ("\r\n--%s--\r\n" % b).encode())
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % b})
    t = time.time()
    raw = urllib.request.urlopen(req, timeout=60).read()
    dt = (time.time() - t) * 1000.0
    try:
        return dt, json.loads(raw.decode("utf-8", "replace")).get("node")
    except Exception:
        return dt, None


direct = {h: [] for h in HOSTS}          # round -> median, per host
head = {h: [] for h in HOSTS}

for rnd in range(1, ROUNDS + 1):
    print("--- round %d/%d ---" % (rnd, ROUNDS), flush=True)
    for h in HOSTS:
        ip = tf.TABLET_NET[h][0]
        ts = []
        for _ in range(N_DIRECT):
            try:
                dt, _ = post("http://%s:8801/v1/vision/detection" % ip)
                ts.append(dt)
            except Exception as e:  # noqa: BLE001
                print("   %-8s direct error %r" % (h, e))
        direct[h].append(statistics.median(ts) if ts else None)
    # head samples, attributed to whichever node served them
    by_node = {h: [] for h in HOSTS}
    for _ in range(N_HEAD):
        try:
            dt, node = post("http://127.0.0.1:8802/v1/vision/detection")
            if node in by_node:
                by_node[node].append(dt)
        except Exception as e:  # noqa: BLE001
            print("   head error %r" % e)
    for h in HOSTS:
        head[h].append(statistics.median(by_node[h]) if by_node[h] else None)
    print("   direct: %s" % "  ".join(
        "%s %s" % (h, ("%.0f" % direct[h][-1]) if direct[h][-1] else "-") for h in HOSTS),
        flush=True)
    print("   head  : %s" % "  ".join(
        "%s %s" % (h, ("%.0f" % head[h][-1]) if head[h][-1] else "-") for h in HOSTS),
        flush=True)
    if rnd < ROUNDS:
        time.sleep(GAP)

print("\n=== PER-NODE, ACROSS %d ROUNDS (median of each round) ===" % ROUNDS)
print("%-9s | %-34s | %s" % ("node", "direct :8801 per round", "min-max   spread"))
print("-" * 78)
stable = {}
for h in HOSTS:
    vals = [v for v in direct[h] if v]
    if not vals:
        continue
    stable[h] = statistics.median(vals)
    print("%-9s | %-34s | %3.0f-%3.0f   %+.0f ms"
          % (h, " ".join("%4.0f" % v for v in vals), min(vals), max(vals),
             max(vals) - min(vals)))

print("\n=== HEAD OVERHEAD, SAME NODE vs ITSELF (head median - direct median, per round) ===")
for h in HOSTS:
    deltas = [hd - dr for hd, dr in zip(head[h], direct[h]) if hd and dr]
    if not deltas:
        print("  %-9s (no paired rounds)" % h)
        continue
    print("  %-9s %s   median %+.0f ms"
          % (h, " ".join("%+5.0f" % d for d in deltas), statistics.median(deltas)))

print("\n=== IS THE RANK ORDER STABLE? ===")
ranks = []
for i in range(ROUNDS):
    row = [(direct[h][i], h) for h in HOSTS if i < len(direct[h]) and direct[h][i]]
    if row:
        ranks.append([h for _, h in sorted(row)])
        print("  round %d fastest->slowest: %s" % (i + 1, " ".join(ranks[-1])))
if ranks:
    same_slowest = len({r[-1] for r in ranks}) == 1
    same_fastest = len({r[0] for r in ranks}) == 1
    print("  slowest node identical every round: %s" % same_slowest)
    print("  fastest node identical every round: %s" % same_fastest)
    print("  -> %s" % ("rank order is STABLE; per-node differences are a real property"
                       if same_slowest else
                       "rank order MOVES between rounds; a single round cannot name a slow node"))
