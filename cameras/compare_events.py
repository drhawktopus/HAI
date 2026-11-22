"""THE A/B: what Frigate detected LIVE (starved sub stream) vs what it detects replaying the SAME
footage at main-stream quality.

Same camera views, same detectors, same Frigate, same motion config -- the encode is the only
thing that differs, which is what makes this a comparison.

Live window: the span the pulled [M] clips cover (America/Chicago). Replay window: everything after
the replay restart epoch.
"""
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime

from cam_settings import head

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPLAY_START = 1786299534          # frigate restart that began the replay
LIVE_FROM = datetime(2026, 8, 8, 21, 54, 0).timestamp()
LIVE_TO = datetime(2026, 8, 9, 6, 0, 0).timestamp()


def api(path):
    out = subprocess.run(
        ["ssh", head(), "docker exec frigate curl -s 'http://127.0.0.1:5000%s'" % path],
        capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout)
    except Exception:
        print("API PARSE FAIL for %s: %r" % (path, out.stdout[:200]))
        return []


def summarise(rows, tag):
    c = Counter()
    scores = {}
    for r in rows:
        key = (r.get("camera"), r.get("label"))
        c[key] += 1
        s = (r.get("data") or {}).get("top_score") or r.get("top_score") or 0
        scores.setdefault(key, []).append(round(float(s or 0), 3))
    print("\n%s" % tag)
    if not c:
        print("   (no events)")
    for key in sorted(c):
        ss = sorted(scores[key], reverse=True)
        print("   %-18s %-7s n=%-3d  best=%.3f  median=%.3f"
              % (key[0], key[1], c[key], ss[0], ss[len(ss) // 2]))
    return c


live = [r for r in api("/api/events?limit=2000&after=%d&before=%d" % (LIVE_FROM, LIVE_TO))
        if r.get("camera") in ("dining_room", "basement")]
rep = [r for r in api("/api/events?limit=2000&after=%d" % REPLAY_START)
       if str(r.get("camera", "")).startswith("replay_")]

lc = summarise(live, "LIVE overnight (sub stream: dining 704x480@512k, basement 720p@1024k)")
rc = summarise(rep, "REPLAY same footage (main stream transcoded: both 720p@~4000k)")

print("\nPER-CAMERA TOTALS (animals = cat+dog, the label pair Sam flickers between)")
for live_cam, rep_cam in (("basement", "replay_basement"), ("dining_room", "replay_dining")):
    la = sum(v for k, v in lc.items() if k[0] == live_cam and k[1] in ("cat", "dog"))
    ra = sum(v for k, v in rc.items() if k[0] == rep_cam and k[1] in ("cat", "dog"))
    lp = sum(v for k, v in lc.items() if k[0] == live_cam and k[1] == "person")
    rp = sum(v for k, v in rc.items() if k[0] == rep_cam and k[1] == "person")
    print("   %-11s animals %3d -> %-3d      person %3d -> %d" % (live_cam, la, ra, lp, rp))

print("\nreplay events so far: %d   live events in window: %d" % (len(rep), len(live)))
print("⚠ replay may still be running; treat these as running totals, not a final count.")
