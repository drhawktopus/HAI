"""WHEN did last night's live detections happen, and how many fall inside the [M] windows?

Two questions the raw totals cannot answer:
  1. Is night detection actually working, or were all the hits in the lit evening? -> hour histogram.
  2. Is the replay comparison fair? Live ran 8 h continuously; the replay covers only the 77 min of
     NVR motion clips. Only live events INSIDE those windows are an apples-to-apples denominator.
  3. Did anything fire during the 03:43 cat window at all?
"""
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime

from cam_settings import head

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if len(sys.argv) != 2:
    sys.exit("usage: live_distribution.py <night_clips.tsv>")
TSV = sys.argv[1]

LIVE_FROM = datetime(2026, 8, 8, 21, 54, 0).timestamp()
LIVE_TO = datetime(2026, 8, 9, 6, 0, 0).timestamp()

out = subprocess.run(
    ["ssh", head(),
     "docker exec frigate curl -s 'http://127.0.0.1:5000/api/events?limit=2000&after=%d&before=%d'"
     % (LIVE_FROM, LIVE_TO)], capture_output=True, text=True, timeout=120)
rows = [r for r in json.loads(out.stdout) if r.get("camera") in ("dining_room", "basement")]

# clip windows per channel: ch1 -> dining_room, ch2 -> basement
wins = {"dining_room": [], "basement": []}
for line in open(TSV, encoding="utf-8"):
    p = line.strip().split("\t")
    if len(p) != 4:
        continue
    cam = "dining_room" if p[0] == "1" else "basement"
    wins[cam].append((datetime.strptime(p[1], "%Y-%m-%d %H:%M:%S").timestamp(),
                      datetime.strptime(p[2], "%Y-%m-%d %H:%M:%S").timestamp()))

print("HOUR HISTOGRAM of live events (label letters: c=cat d=dog p=person)")
hist = {}
for r in rows:
    t = datetime.fromtimestamp(r["start_time"])
    key = (r["camera"], t.hour)
    hist.setdefault(key, Counter())[r["label"][0]] += 1
for cam in ("basement", "dining_room"):
    line = []
    for h in list(range(21, 24)) + list(range(0, 7)):
        c = hist.get((cam, h))
        line.append("%02d:%s" % (h, "".join("%s%d" % (k, v) for k, v in sorted(c.items()))
                                 if c else "-"))
    print("  %-12s %s" % (cam, "  ".join(line)))

print("\nINSIDE the [M] clip windows (the apples-to-apples denominator for the replay)")
for cam in ("basement", "dining_room"):
    inside = Counter()
    outside = Counter()
    for r in rows:
        if r["camera"] != cam:
            continue
        t = r["start_time"]
        hit = any(a <= t <= b for a, b in wins[cam])
        (inside if hit else outside)[r["label"]] += 1
    print("  %-12s inside=%s   outside=%s" % (cam, dict(inside), dict(outside)))

print("\nTHE 03:43 BASEMENT CAT WINDOW (03:43:01 to 03:44:36) — live events:")
a = datetime(2026, 8, 9, 3, 43, 1).timestamp()
b = datetime(2026, 8, 9, 3, 44, 36).timestamp()
hits = [r for r in rows if r["camera"] == "basement" and a <= r["start_time"] <= b]
print("   %d event(s)%s" % (len(hits), "" if hits else "  <- the miss that started this thread"))

print("\nDEEP NIGHT ONLY (00:00-06:00), animals:")
for cam in ("basement", "dining_room"):
    n = sum(1 for r in rows if r["camera"] == cam and r["label"] in ("cat", "dog")
            and 0 <= datetime.fromtimestamp(r["start_time"]).hour < 6)
    print("   %-12s %d" % (cam, n))
