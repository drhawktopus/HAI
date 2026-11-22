"""Sample Frigate's per-camera pipeline cost, for the before/after of the decode offload.

WHY A SAMPLER AND NOT A GLANCE: the doctrine row that cost this project a retraction is "conclude
anything from ONE measurement". Two of three single-round claims died on the second round.
So this takes N rounds, reports the MEDIAN and the RANGE for each arm, and states
explicitly whether the two arms' ranges OVERLAP -- a delta between two single samples is a story,
not a result.

 `docker stats` reports 100% = ONE core and Sundae has SIX. A reading of "35%" is a third of one
core, about 6% of the box. Sundae is a guest on Softserve, so never count both.

Usage: python sample_cost.py <label> <rounds> [seconds_between]
Writes: cost_<label>.json (and prints a summary)
"""
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # the cameras folder
from cam_settings import head, need

SUNDAE = urllib.parse.urlparse(need("HAI_URL", "the head's address")).hostname
CAMS = ("dining_room", "basement")


def sh(cmd, timeout=60):
    r = subprocess.run(["ssh", head(), cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.stdout.strip()


def sample():
    """One observation: Frigate's container CPU plus each camera's own fps counters."""
    cpu = sh("docker stats --no-stream --format '{{.CPUPerc}}' frigate")
    cpu_f = float(cpu.replace("%", "").strip()) if cpu.strip().endswith("%") else None

    with urllib.request.urlopen("http://%s:5000/api/stats" % SUNDAE, timeout=30) as r:
        st = json.load(r)

    row = {"t": time.time(), "frigate_cpu_pct_of_one_core": cpu_f, "cams": {}}
    for c in CAMS:
        v = st.get("cameras", {}).get(c, {})
        row["cams"][c] = {k: v.get(k) for k in
                          ("camera_fps", "detection_fps", "process_fps", "skipped_fps")}
    row["detectors"] = {k: v.get("inference_speed")
                        for k, v in st.get("detectors", {}).items()}
    return row


def main():
    label = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    gap = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0

    rows = []
    for i in range(rounds):
        try:
            r = sample()
            rows.append(r)
            print("round %d/%d  cpu=%s%%  %s" % (
                i + 1, rounds, r["frigate_cpu_pct_of_one_core"],
                {c: r["cams"][c]["skipped_fps"] for c in CAMS}))
        except Exception as e:
            # An unreadable round is recorded as unreadable, never silently dropped -- a sampler
            # that quietly discards failures reports a cleaner world than the one it measured.
            print("round %d/%d  FAILED %s: %s" % (i + 1, rounds, type(e).__name__, e))
            rows.append({"t": time.time(), "error": "%s: %s" % (type(e).__name__, e)})
        if i + 1 < rounds:
            time.sleep(gap)

    good = [r for r in rows if "error" not in r]
    out = {"label": label, "rounds": rounds, "gap_s": gap, "ok": len(good), "rows": rows}

    def summarise(vals):
        vals = [v for v in vals if isinstance(v, (int, float))]
        if not vals:
            return None
        return {"median": round(statistics.median(vals), 2),
                "min": round(min(vals), 2), "max": round(max(vals), 2), "n": len(vals)}

    out["summary"] = {
        "frigate_cpu_pct_of_one_core": summarise([r["frigate_cpu_pct_of_one_core"] for r in good]),
        "cams": {c: {k: summarise([r["cams"][c][k] for r in good])
                     for k in ("camera_fps", "detection_fps", "process_fps", "skipped_fps")}
                 for c in CAMS},
    }

    path = "cost_%s.json" % label
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print("\n=== %s (%d/%d rounds usable) ===" % (label, len(good), rounds))
    print(json.dumps(out["summary"], indent=2))
    print("wrote %s" % path)


if __name__ == "__main__":
    main()
