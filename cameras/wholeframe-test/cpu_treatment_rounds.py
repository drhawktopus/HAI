"""Measure the MAIN-STREAM detect CPU cost across several rounds, then always revert.

The baseline now has 6 rounds (27.7-35.4% of one core, median 34.1). The treatment still had
exactly one window, so the "+18 points" delta is half-measured. This completes it.

 THE REVERT IS IN A `finally`. This script changes production config; if it dies mid-run the
config must still go back. Cleanup belongs to the path that sees the end by ANY route, not to the
happy path -- and nothing in that finally may itself raise, or it replaces the real exception with
its own and destroys the diagnosis.

Reverts by restoring the backup it takes at the start, then verifies the restored file matches
byte-for-byte. "Restarted" is not "reverted" unless the bytes agree.
"""
import hashlib
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # the cameras folder
from cam_settings import need

STACK = need("HEAD_STACK_DIR", "the head's compose stack folder")
CFG = STACK + "/frigate/config/config.yml"
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
SAMPLES = int(sys.argv[2]) if len(sys.argv) > 2 else 5
GAP = int(sys.argv[3]) if len(sys.argv) > 3 else 25
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def sh(*a, **kw):
    return subprocess.run(a, capture_output=True, text=True, timeout=kw.get("t", 180))


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def cpu_once():
    p = sh("docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", "frigate")
    try:
        return float((p.stdout or "").strip().rstrip("%"))
    except ValueError:
        return None


bak = CFG + ".bak-cpurounds-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(CFG, bak)
before = sha(CFG)
print("backup %s  sha %s" % (bak, before[:16]))

try:
    with open(CFG, encoding="utf-8") as f:
        text = orig = f.read()
    if "rtsp://127.0.0.1:8554/dining_room_sub" not in text:
        print("FAIL: dining sub path absent - config shape changed, doing nothing")
        sys.exit(1)
    text = text.replace("- path: rtsp://127.0.0.1:8554/dining_room_sub\n",
                        "- path: rtsp://127.0.0.1:8554/dining_room\n", 1)
    s = text.index("dining_room:", text.index("cameras:"))
    e = text.index("\n  basement:", s)
    blk = re.sub(r"(\n      )width: 704\n(      )height: 480\n",
                 r"\1width: 1920\n\2height: 1080\n", text[s:e], count=1)
    if blk == text[s:e]:
        print("FAIL: dining detect 704x480 not found")
        sys.exit(1)
    text = text[:s] + blk + text[e:]
    tmp = CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, CFG)
    print("applied main-stream detect for dining; restarting frigate")
    sh("docker", "compose", "-f", STACK + "/docker-compose.yml", "restart", "frigate", t=240)
    time.sleep(75)                       # settle; a restart transient is not a measurement

    meds = []
    for rnd in range(1, ROUNDS + 1):
        vals = []
        for _ in range(SAMPLES):
            v = cpu_once()
            if v is not None:
                vals.append(v)
            time.sleep(2)
        if vals:
            meds.append(statistics.median(vals))
            print("round %d/%d: %.1f%%  (%s)" % (rnd, ROUNDS, meds[-1],
                                                 " ".join("%.0f" % v for v in vals)), flush=True)
        if rnd < ROUNDS:
            time.sleep(GAP)

    if meds:
        BASE_MED, BASE_LO, BASE_HI = 34.1, 27.7, 35.4          # measured, 6 rounds
        t_med = statistics.median(meds)
        print("\n=== TREATMENT (dining detect on 1080p main), %d rounds ===" % len(meds))
        print("  median of rounds : %.1f%% of one core   spread %.1f - %.1f"
              % (t_med, min(meds), max(meds)))
        print("  baseline (6 rnds): %.1f%%                spread %.1f - %.1f"
              % (BASE_MED, BASE_LO, BASE_HI))
        print("  DELTA            : %+.1f points of one core  (= %+.2f of a core, %+.1f%% of the box)"
              % (t_med - BASE_MED, (t_med - BASE_MED) / 100.0, (t_med - BASE_MED) / 6.0))
        overlap = min(BASE_HI, max(meds)) - max(BASE_LO, min(meds))
        print("  ranges overlap by: %.1f points -> %s"
              % (max(0.0, overlap),
                 "SEPARATED, the delta is real" if overlap < 0 else
                 "OVERLAPPING - the delta is NOT cleanly established"))
finally:
    # nothing here may raise: this runs on the exception path too.
    try:
        shutil.copy2(bak, CFG)
        sh("docker", "compose", "-f", STACK + "/docker-compose.yml", "restart", "frigate",
           t=240)
        same = sha(CFG) == before
        print("\nREVERTED: config sha matches original = %s" % same)
        if not same:
            print("  *** CONFIG DID NOT RESTORE CLEANLY - restore %s BY HAND ***" % bak)
    except Exception as e:      # noqa: BLE001
        print("\n*** REVERT FAILED (%s) - restore %s BY HAND ***" % (type(e).__name__, bak))
