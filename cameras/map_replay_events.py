"""Map each REPLAY event back to the wall-clock moment of the night it came from.

The concat plays the [M] clips back to back in time order at 1x, so an event at offset T into the
stream sits inside whichever clip covers cumulative-duration T. That gives every replay detection a
source timestamp, which answers the question: live produced ZERO events between 01:00 and 06:00
(true IR); replaying THE SAME windows at main-stream quality, does anything fire?

APPROXIMATE BY DESIGN, and bounded so it cannot mislead: the transcoded clip durations are used for
the cumulative walk (they are what actually plays), only the first pass is counted, and any event
past the end of the concat is reported as OUT-OF-RANGE rather than silently clamped. Frigate
restarts ffmpeg when a file ends, so a second pass would double count; events beyond the concat
duration are dropped and reported, not folded in.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta

from cam_settings import head

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPLAY_START = 1786299534
if len(sys.argv) != 2:
    sys.exit("usage: map_replay_events.py <night_clips.tsv>")
TSV = sys.argv[1]
CAM = {"replay_dining": "1", "replay_basement": "2"}


def sh(cmd):
    return subprocess.run(["ssh", head(), cmd], capture_output=True, text=True,
                          timeout=180).stdout


# real durations of the transcoded pieces, in the order the concat lists them
durs = {}
for ch in ("1", "2"):
    raw = sh("docker exec frigate sh -c \"cat /tmp/replay/list_ch%s.txt\"" % ch)
    names = [l.split("'")[1] for l in raw.splitlines() if "'" in l]
    out = sh("docker exec frigate sh -c \"for f in %s; do "
             "/usr/lib/ffmpeg/5.0/bin/ffprobe -v error -show_entries format=duration "
             "-of csv=p=0 \\$f; done\"" % " ".join(names))
    d = [float(x) for x in out.split() if x.strip()]
    durs[ch] = list(zip(names, d))
    print("ch%s: %d pieces, total %.1f min" % (ch, len(d), sum(d) / 60))

# clip start times, keyed by the basename the concat used (tx_ch<N>_HHMMSS.mp4)
starts = {}
for line in open(TSV, encoding="utf-8"):
    p = line.strip().split("\t")
    if len(p) != 4:
        continue
    t0 = datetime.strptime(p[1], "%Y-%m-%d %H:%M:%S")
    starts["tx_ch%s_%s.mp4" % (p[0], t0.strftime("%H%M%S"))] = t0

rows = json.loads(sh("docker exec frigate curl -s "
                     "'http://127.0.0.1:5000/api/events?limit=2000&after=%d'" % REPLAY_START))
rows = [r for r in rows if str(r.get("camera", "")).startswith("replay_")]
print("\n%d replay events fetched" % len(rows))

for cam, ch in CAM.items():
    pieces = durs[ch]
    total = sum(d for _n, d in pieces)
    evs = sorted((r for r in rows if r["camera"] == cam), key=lambda r: r["start_time"])
    print("\n=== %s (concat %.1f min) ===" % (cam, total / 60))
    late = 0
    oor = 0
    for r in evs:
        off = r["start_time"] - REPLAY_START
        if off > total:
            oor += 1
            continue
        acc = 0.0
        src = None
        for name, d in pieces:
            if acc + d >= off:
                base = starts.get(name.split("/")[-1])
                if base:
                    src = base + timedelta(seconds=off - acc)
                break
            acc += d
        score = (r.get("data") or {}).get("top_score") or 0
        when = src.strftime("%H:%M:%S") if src else "??"
        flag = ""
        if src and (1 <= src.hour < 6):
            late += 1
            flag = "  <-- DEEP-IR WINDOW (live had ZERO here)"
        print("   src~%s  %-7s %.3f%s" % (when, r["label"], float(score), flag))
    print("   -> %d event(s) in the 01:00-06:00 deep-IR window; %d after first pass (ignored)"
          % (late, oor))
