"""Build the ffmpeg batch that harvests one frame per motion event (>= a cutoff hour).



Writes a simple TSV task list (channel, start, out-name) plus the shell script that consumes it,
both destined for Sundae where the ffmpeg container lives. One container invocation loops the whole
list -- starting a container per frame would cost more than the extraction.

The frame is taken a couple of seconds INTO the clip: Dahua starts the recording slightly before
the trigger (pre-record buffer), so t=0 is often the empty scene the motion was measured against.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CUTOFF = sys.argv[1] if len(sys.argv) > 1 else "09:00:00"
SEEK_INTO = 2  # seconds into the clip to sample

with open(os.path.join(HERE, "motion_events.json"), encoding="utf-8") as f:
    events = json.load(f)

NAMES = {1: "livingroom", 2: "frontdoor"}
sel = [e for e in events if e["start"][11:] >= CUTOFF]

# mediaFileFind returns OVERLAPPING records, so the same event appears more than once. The first
# run submitted 76 tasks and produced 41 files -- "ok=76" read as success until the file count was
# checked. Dedupe on (channel, start) so the task count and the artifact count mean the same thing.
seen = set()
deduped = []
for e in sel:
    k = (e["channel"], e["start"])
    if k in seen:
        continue
    seen.add(k)
    deduped.append(e)
if len(deduped) != len(sel):
    print("deduped %d overlapping records -> %d unique events" % (len(sel) - len(deduped),
                                                                  len(deduped)))
sel = deduped

lines = []
for i, e in enumerate(sel):
    date, tod = e["start"].split(" ")
    y, mo, d = date.split("-")
    h, mi, s = tod.split(":")
    st = "%s_%s_%s_%s_%s_%s" % (y, mo, d, h, mi, s)
    # end a few seconds later; we only need one frame
    end_s = (int(s) + 6) % 60
    end_m = int(mi) + (1 if int(s) + 6 >= 60 else 0)
    end_h = int(h) + (1 if end_m >= 60 else 0)
    en = "%s_%s_%s_%02d_%02d_%02d" % (y, mo, d, end_h % 24, end_m % 60, end_s)
    out = "%s_ch%d_%s.jpg" % (tod.replace(":", ""), e["channel"], NAMES.get(e["channel"], "cam"))
    lines.append("%d\t%s\t%s\t%s" % (e["channel"], st, en, out))

with open(os.path.join(HERE, "extract_tasks.tsv"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines) + "\n")

by_ch = {}
for e in sel:
    by_ch[e["channel"]] = by_ch.get(e["channel"], 0) + 1
print("events at/after %s : %d  %s" % (CUTOFF, len(sel), by_ch))
print("-> extract_tasks.tsv")
