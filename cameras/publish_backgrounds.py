"""Publish the median BACKGROUNDS the tablet payload fetches, plus a live positive CONTROL.

The tablets are handed frame URLs; each needs the matching background for its camera and lighting
mode. Rather than ship a background per item, they are published once at a predictable URL
(<CAMERAS_SITE_URL>/bg/ch<N>_<mode>.jpg) and the payload derives which one it needs.

 WHY THIS WAS REWRITTEN: the first version built ch1/colour from FOUR frames and the
detector saturated -- 32 of 52 frames hit the 4-box cap, because anything that lingers in a
4-frame median becomes part of the "background" and everything else reads as change. A detector
that boxes everything is useless in exactly the way HOG was. More frames is the fix and it is free:
the NVR archive holds hundreds of events per day and retrospective extraction is proven.

Two engineering constraints this version respects that the first did not:
  * MEMORY. Loading 176 frames as full-size RGB is fine at 720p and is over a gigabyte now that
    recordings are 4K. Frames are streamed in one at a time, greyscaled and downscaled to a common
    working size before stacking.
  * SAMPLING. A median wants a representative spread, not merely a big pile. Frames are sampled
    EVENLY ACROSS TIME so a busy ten minutes cannot dominate the model for a whole day.

 THE CONTROL IS THE POINT. Every tablet order requires a known-positive, or "the detector found
nothing" and "the detector is broken" are the same result. The control is the real ch1 IR
background with one bright rectangle pasted in; `control_expect = "boxes=1"` therefore proves the
whole chain -- fetch, background fetch, diff, box -- not merely that the script ran.
 It must share its background with the thing it tests: an earlier control was derived from the
COLOUR background but measured as IR (backgrounds are greyscale, so saturation is ~0), so the
payload diffed it against a different image and it failed for a reason unrelated to the detector.
"""
import glob
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
BG = os.path.join(SITE, "bg")
FULL = os.path.join(SITE, "full")

SAT_CUTOFF = 8.0
WORK_W = 1280               # backgrounds are diffed after resize anyway; 720p is plenty and cheap
MIN_FRAMES = 24             # a median needs depth; MEASURED, see below
NEAREST = 60                # frames per background, taken NEAREST IN TIME to the bucket centre
BUCKET_H = 3                # hours per bucket -> 8 buckets/day
CONTROL_NAME = "CONTROL_ch1_ir_b0.jpg"
CONTROL_FROM = None         # chosen at runtime: the first background actually built

# WHY TIME BUCKETS, AND WHY "NEAREST" RATHER THAN "NARROWEST"
# A single all-day median matched no particular moment -- the 3am room and the 10am room differ
# (objects moved, table covered, curtains). Every labelled frame came back >50% changed and was
# correctly refused as `global-change`, which read as the detector going deaf.
# But locality alone is NOT the fix. Measured against frame 092723 on ch1/IR:
# all-day median (60 frames) hot_frac 0.543
# +/-1h window (20 frames) hot_frac 0.826 <-- WORSE than all-day
# +/-2h window (34 frames) hot_frac 0.670 <-- still worse
# +/-3h window (58 frames) hot_frac 0.214 <-- works
# At comparable depth (58 vs 60) locality wins hugely; too narrow to hold enough frames is worse
# than no locality at all. So: take the NEAREST N frames in time, and refuse below MIN_FRAMES --
# never "the narrowest window".


def classify(path):
    """(channel, mode) for a frame, or None. Mode is MEASURED, never inferred from the filename."""
    base = os.path.basename(path)
    ch = "1" if "_ch1_" in base else ("2" if "_ch2_" in base else None)
    if not ch:
        return None
    with Image.open(path) as im:
        small = im.convert("RGB").resize((160, 90))
        sat = float(np.asarray(small.convert("HSV"))[:, :, 1].mean())
    return ch, ("colour" if sat > SAT_CUTOFF else "ir")


def secs_of(path):
    """Seconds-since-midnight from the filename stamp (…HHMMSS_chN_…)."""
    stamp = os.path.basename(path).split("_")[0][-6:]
    return int(stamp[0:2]) * 3600 + int(stamp[2:4]) * 60 + int(stamp[4:6])


def nearest_in_time(files, centre, n):
    """The n frames closest in time to `centre`. Adaptive: a sparse period simply reaches wider
    rather than producing a thin median, which measured WORSE than no locality at all."""
    return sorted(sorted(files, key=lambda p: abs(secs_of(p) - centre))[:n], key=secs_of)


os.makedirs(BG, exist_ok=True)
paths = []
for src in (os.path.join(HERE, "corpus_recorded", "*.jpg"),
            os.path.join(HERE, "corpus", "*.jpg")):
    paths.extend(sorted(glob.glob(src)))

groups = {}
for p in paths:
    c = classify(p)
    if c:
        groups.setdefault(c, []).append(p)

print("frames available per group:")
for k in sorted(groups):
    print("  ch%s/%-6s %3d" % (k[0], k[1], len(groups[k])))
print()

made = {}
n_buckets = 24 // BUCKET_H
for (ch, mode), files in sorted(groups.items()):
    for b in range(n_buckets):
        centre = (b * BUCKET_H + BUCKET_H / 2.0) * 3600
        # only frames whose OWN bucket is this one need a background for it
        in_bucket = [p for p in files if int(secs_of(p) // 3600) // BUCKET_H == b]
        if not in_bucket:
            continue
        use = nearest_in_time(files, centre, NEAREST)
        if len(use) < MIN_FRAMES:
            print("  ch%s/%-6s b%d  %3d frames within reach -- BELOW MIN_FRAMES=%d, SKIPPED "
                  "(%d frame(s) in this bucket will error honestly rather than be diffed "
                  "against a guess)" % (ch, mode, b, len(use), MIN_FRAMES, len(in_bucket)))
            continue
        ref = None
        stack = []
        for p in use:
            with Image.open(p) as im:
                g = im.convert("L")
                if ref is None:
                    ref = (WORK_W, int(g.height * WORK_W / g.width))
                stack.append(np.asarray(g.resize(ref), dtype=np.uint8))
        med = np.median(np.stack(stack), axis=0).astype(np.uint8)
        out = os.path.join(BG, "ch%s_%s_b%d.jpg" % (ch, mode, b))
        Image.fromarray(med).save(out, quality=92)
        made[(ch, mode, b)] = out
        span = (secs_of(use[0]) // 3600, secs_of(use[-1]) // 3600)
        print("  ch%s/%-6s b%d (%02d:00-%02d:00)  %2d frames from %02dh-%02dh -> %s"
              % (ch, mode, b, b * BUCKET_H, (b + 1) * BUCKET_H, len(use),
                 span[0], span[1], os.path.basename(out)))

# ---- the control ---------------------------------------------------------------------------
print()
# The control must share its background with the thing it tests, so it is built from whichever
# background actually exists -- named to match, so the payload resolves to that exact file.
ctrl_key = sorted(made)[0] if made else None
if ctrl_key:
    # The control MUST parse like a real frame: the payload derives its background bucket from
    # the HHMMSS stamp in the filename. A name of "CONTROL_ch1_ir_b1.jpg" parsed to bucket 0,
    # which does not exist for ch1/IR -> 404 -> control FAILED and the whole order went VOID for a
    # reason that had nothing to do with the detector. Stamp it at the bucket centre.
    _ch, _mode, _b = ctrl_key
    CONTROL_NAME = "%02d3000_ch%s_%s_CONTROL.jpg" % (_b * BUCKET_H + BUCKET_H // 2, _ch, _mode)
    ctrl = Image.open(made[ctrl_key]).convert("RGB")
    d = ImageDraw.Draw(ctrl)
    w, h = ctrl.size
    # centre-ish, comfortably above MIN_AREA_FRAC, clear of both masked overlay corners
    d.rectangle([int(w * 0.40), int(h * 0.40), int(w * 0.60), int(h * 0.66)], fill=(255, 255, 255))
    os.makedirs(FULL, exist_ok=True)
    ctrl.save(os.path.join(FULL, CONTROL_NAME), quality=92)
    print("  CONTROL -> full/%s (one planted rectangle; expect boxes=1)" % CONTROL_NAME)
else:
    print("  ⚠ NO CONTROL BUILT — no background was produced at all. DO NOT submit an order "
          "without a control: a broken payload and a genuinely empty result are "
          "indistinguishable.")
