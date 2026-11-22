"""Frigate CPU across several rounds, to find the NATURAL variance before trusting any delta.

The "+0.18 of a core for main-stream detect" figure came from ONE 60 s window at baseline and ONE
at treatment. Frigate's CPU tracks scene activity -- motion means detections mean work -- so the
baseline is not a constant and a single-window delta could be mostly time-of-day.

This measures the baseline REPEATEDLY on the current (sub-stream) config and reports the spread.
If the baseline itself swings by more than the delta I claimed, that delta is not established and
I should say so rather than defend it.

Also records dining's detect_fps and skipped_fps each round: CPU is only interpretable alongside
how much work Frigate was actually doing. A quiet house and a busy one are different regimes and
averaging them hides both.

Read-only. Touches no config.
"""
import json
import statistics
import subprocess
import sys
import time
import urllib.request

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
SAMPLES = int(sys.argv[2]) if len(sys.argv) > 2 else 5
GAP = int(sys.argv[3]) if len(sys.argv) > 3 else 30
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cpu_once():
    p = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", "frigate"],
        capture_output=True, text=True, timeout=60)
    txt = (p.stdout or "").strip().rstrip("%")
    try:
        return float(txt)
    except ValueError:
        return None


def frigate_work():
    try:
        with urllib.request.urlopen("http://127.0.0.1:5000/api/stats", timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        c = (d.get("cameras") or {}).get("dining_room") or {}
        return c.get("detection_fps"), c.get("skipped_fps")
    except Exception:
        return None, None


rows = []
for rnd in range(1, ROUNDS + 1):
    vals = []
    for _ in range(SAMPLES):
        v = cpu_once()
        if v is not None:
            vals.append(v)
        time.sleep(2)
    det, skip = frigate_work()
    med = statistics.median(vals) if vals else None
    rows.append((med, det, skip))
    print("round %d/%d: frigate cpu median %s%%  (samples %s)  dining detect_fps=%s skipped=%s"
          % (rnd, ROUNDS, ("%.1f" % med) if med else "-",
             " ".join("%.0f" % v for v in vals), det, skip), flush=True)
    if rnd < ROUNDS:
        time.sleep(GAP)

meds = [m for m, _, _ in rows if m]
if meds:
    print("\n=== BASELINE VARIANCE (sub-stream config, %d rounds) ===" % len(meds))
    print("  median of rounds : %.1f%% of ONE core   (Sundae has 6 -> %.1f%% of the box)"
          % (statistics.median(meds), statistics.median(meds) / 6.0))
    print("  round spread     : %.1f - %.1f  (range %.1f points of one core)"
          % (min(meds), max(meds), max(meds) - min(meds)))
    print("\n  ⚠ the single-window main-stream delta was +18 points of one core.")
    if max(meds) - min(meds) >= 18:
        print("  -> BASELINE SWING >= THAT DELTA. The +18 is NOT established; re-measure the")
        print("     treatment across rounds too, or withdraw the number.")
    else:
        print("  -> baseline swings less than the delta, so the delta survives this check")
        print("     (necessary, not sufficient: the treatment still needs its own rounds).")
