"""Score a motion order against the four criteria, straight from the RESULTS file.

 WHY IT READS THE RESULTS AND NOT site/boxes.json: an earlier version scored the merged file,
but `merge_results.py` REFUSES to merge a VOID order -- so when run 3 went VOID the scorer happily
re-read run 2's boxes and printed them as run 3's numbers. Stale data presented as a fresh
measurement is worse than no measurement. There is now no intermediary to go stale.

The four criteria are fixed and stated in advance so a result cannot be rationalised afterwards:
  1. CONTROL passes -- the planted rectangle is found; without it, absence proves nothing
  2. RECALL holds on labels -- all 22 of the owner's labelled frames still boxed (17 person, 5 cat)
  3. DEGENERATE boxes ~ 0 -- no whole-frame rectangles, which carry no location
  4. median box area stays small -- the useful boxes must not inflate to win criterion 3

 Criterion 2 is the one that catches a FAKE win. Run 2 scored perfectly on 1, 3 and 4 while
detecting nothing at all; only the labelled frames exposed it.
"""
import collections
import glob
import json
import os
import sys
from cam_settings import need

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(need("TABLET_JOBS_HOST", "the tablets' order share"), "outbox")

files = sorted(glob.glob(os.path.join(OUTBOX, "cam-motion.*.results.json")))
path = sys.argv[1] if len(sys.argv) > 1 else (files[-1] if files else None)
if not path:
    raise SystemExit("no results files")
with open(path, encoding="utf-8") as f:
    d = json.load(f)

rows = {r["item"].rsplit("/", 1)[-1]: r for r in d.get("rows", [])}
boxes = {n: [b["box"] for b in r.get("boxes", [])]
         for n, r in rows.items() if "CONTROL" not in n and not r.get("error")}

with open(os.path.join(HERE, "labels_20260808.json"), encoding="utf-8") as f:
    lab = json.load(f)["ch1_livingroom"]
person, cat = set(lab["person"]), set(lab["cat"])
labelled = sorted(f for f in os.listdir(os.path.join(HERE, "site", "full"))
                  if "_ch1_" in f and "CONTROL" not in f
                  and len(f.split("_")[0]) == 6 and f.split("_")[0] >= "090000")

print("scoring: %s" % os.path.basename(path))
print()
print("1) CONTROL      : control_passed=%s  complete=%s  usable=%s  VOID=%s"
      % (d.get("control_passed"), d.get("complete"), d.get("usable"), d.get("VOID")))
if not d.get("usable"):
    print("   ⚠ ORDER IS NOT USABLE — the numbers below describe a run that failed its own gate.")

assert len(labelled) == lab["total"], "label/corpus mismatch: %d vs %d" % (len(labelled),
                                                                          lab["total"])
hp = sum(1 for i, f in enumerate(labelled, 1) if i in person and boxes.get(f))
hc = sum(1 for i, f in enumerate(labelled, 1) if i in cat and boxes.get(f))
missed = [(i, f, rows.get(f, {}).get("summary", "?"), rows.get(f, {}).get("hot_frac"))
          for i, f in enumerate(labelled, 1) if not boxes.get(f)]
print("2) RECALL       : people %d/%d   cats %d/%d   (of %d labelled frames)"
      % (hp, len(person), hc, len(cat), len(labelled)))
for i, f, s, hf in missed[:6]:
    print("     MISSED #%-3d %-34s %s hot_frac=%s" % (i, f[:34], s, hf))

tot = deg = 0
areas = []
for bs in boxes.values():
    for x1, y1, x2, y2 in bs:
        tot += 1
        a = ((x2 - x1) * (y2 - y1)) / (1280.0 * 720.0)
        areas.append(a)
        if a > 0.5:
            deg += 1
areas.sort()
print("3) DEGENERATE   : %d of %d boxes (%.1f%%)" % (deg, tot, 100.0 * deg / max(tot, 1)))
if areas:
    print("4) median area  : %.1f%%   p90=%.1f%%"
          % (100 * areas[len(areas) // 2], 100 * areas[int(len(areas) * .9)]))

gc = sum(1 for r in rows.values() if r.get("global_change"))
er = sum(1 for r in rows.values() if r.get("error"))
print()
print("   frames with boxes %d | global-change %d | errors %d"
      % (len(boxes), gc, er))
print("   spread: %s" % dict(sorted(collections.Counter(len(v) for v in boxes.values()).items())))
if er:
    seen = collections.Counter(r["error"].split(":")[0] for r in rows.values() if r.get("error"))
    print("   error kinds: %s" % dict(seen))
