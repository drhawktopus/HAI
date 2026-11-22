"""Merge a Fellowship motion order's results into site/boxes.json.

Reads the NEWEST cam-motion results file from the outbox, checks BOTH verdicts before trusting
anything in it, and writes {frame filename: [[x1,y1,x2,y2], ...]} for build_site.py to overlay.

 REFUSES a VOID order. `usable` is control_passed AND complete, and they fail independently:
a working detector that only saw half the corpus reads green everywhere else. Merging a VOID
result would silently publish boxes for the frames that ran and nothing for the rest, which looks
exactly like "those frames had no motion".
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
if not files:
    raise SystemExit("no cam-motion results in the outbox")
latest = files[-1]
with open(latest, encoding="utf-8") as f:
    d = json.load(f)

print("results: %s" % os.path.basename(latest))
print("  control_passed=%s  complete=%s  usable=%s  VOID=%s  wall=%ss"
      % (d.get("control_passed"), d.get("complete"), d.get("usable"), d.get("VOID"),
         d.get("wall_s")))
print("  %s" % d.get("coverage"))

if not d.get("usable") or d.get("VOID"):
    raise SystemExit("REFUSING TO MERGE: order is not usable. Fix the order, do not publish "
                     "partial boxes -- missing boxes are indistinguishable from 'no motion'.")

boxes = {}
errors = []
counts = collections.Counter()
for r in d.get("rows", []):
    item = r.get("item", "")
    name = item.rsplit("/", 1)[-1]
    if "CONTROL" in name:
        continue
    if r.get("error"):
        errors.append((name, r["error"]))
        continue
    bs = [b["box"] for b in r.get("boxes", [])]
    counts[len(bs)] += 1
    if bs:
        boxes[name] = bs

site = os.path.join(HERE, "site")
os.makedirs(site, exist_ok=True)
with open(os.path.join(site, "boxes.json"), "w", encoding="utf-8") as f:
    json.dump(boxes, f)

print()
print("  frames with boxes : %d" % len(boxes))
print("  box-count spread  : %s" % dict(sorted(counts.items())))
print("  errored rows      : %d" % len(errors))
for n, e in errors[:6]:
    print("     %-42s %s" % (n[:42], e[:70]))
print("-> site/boxes.json")
