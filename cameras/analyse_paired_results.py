"""Analyse the paired source-quality order's DELIVERABLE from the outbox.

Reads the factory's shipped *.results.json rather than re-running anything: the order already
did the work, the control already passed, and coverage was already validated. Re-scoring here
would be a second mechanism beside the factory.

 REFUSES to analyse a VOID run. `usable` is the factory's own verdict (control passed AND
coverage complete); a failed control means the numbers are not evidence, and quietly analysing
them anyway is how a broken detector's output becomes a published finding.

Pairs are (frame, pad) triplets of arms A4k / Bmatch / Creal, so each comparison holds frame and
crop fixed and only source quality varies -- the framing chaos cancels within the pair.
"""
import json
import os
import re
import sys
from cam_settings import need

OUTBOX = os.path.join(need("TABLET_JOBS_HOST", "the tablets' order share"), "outbox")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

name = sys.argv[1] if len(sys.argv) > 1 else None
cands = sorted(f for f in os.listdir(OUTBOX)
               if f.endswith(".results.json") and (name or "cam-paired-quality") in f)
if not cands:
    print("no matching results in %s" % OUTBOX)
    sys.exit(1)
path = os.path.join(OUTBOX, cands[-1])          # newest explicitly; one name = one artifact
print("results: %s" % cands[-1])

with open(path, encoding="utf-8") as f:
    doc = json.load(f)

print("usable=%s  VOID=%s  control=%s  measured=%s/%s"
      % (doc.get("usable"), doc.get("VOID"), doc.get("control"),
         doc.get("n_measured"), doc.get("n_expected")))
if not doc.get("usable"):
    print("\nREFUSING to analyse: the factory marked this run unusable. Fix and re-run.")
    sys.exit(2)

rows = doc.get("rows") or []
cells = {}
for r in rows:
    m = re.search(r"f(\d+)_p(\d+)_(A4k|Bmatch|Creal)\.jpg$", r.get("item", ""))
    if not m:
        continue
    key = (int(m.group(1)), int(m.group(2)))
    best = 0.0
    for d in r.get("dets") or []:
        if d.get("label") == "cat":
            best = max(best, float(d.get("conf") or 0))
    cells.setdefault(key, {})[m.group(3)] = best

print("\n%-6s %-5s | %-7s %-7s %-7s | A>C B>C A>B" % ("frame", "pad", "A_4k", "B_match", "C_real"))
print("-" * 62)
tally = {"A>C": [0, 0, 0], "B>C": [0, 0, 0], "A>B": [0, 0, 0]}


def note(k, x, y):
    if abs(x - y) < 1e-9:
        tally[k][2] += 1
        return "="
    if x > y:
        tally[k][0] += 1
        return "W"
    tally[k][1] += 1
    return "L"


for key in sorted(cells):
    c = cells[key]
    if len(c) != 3:
        print("%-6d %-5d | INCOMPLETE TRIPLET (%s) - excluded" % (key[0], key[1], ",".join(c)))
        continue
    a, b, cc = c["A4k"], c["Bmatch"], c["Creal"]
    print("%-6d %-5d | %-7.3f %-7.3f %-7.3f |  %s   %s   %s"
          % (key[0], key[1], a, b, cc, note("A>C", a, cc), note("B>C", b, cc),
             note("A>B", a, b)))

print("\n=== PAIRED TALLY ===")
for k, what in (("A>C", "overall source quality"), ("B>C", "COMPRESSION alone"),
                ("A>B", "RESOLUTION alone")):
    w, l, t = tally[k]
    n = w + l
    pct = (100.0 * w / n) if n else 0
    print("  %-5s %-24s %2d W / %2d L / %2d tie   -> %.0f%% of %d decided"
          % (k, what, w, l, t, pct, n))
