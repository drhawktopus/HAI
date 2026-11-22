"""Build + publish the paired source-quality corpus, then print the order to submit.

INPUTS stage of a proper Fellowship order. The customer prepares inputs and submits; the FACTORY
does the work. Generating crops is customer-side because the fleet cannot write to the web root,
and `map` items are URLs the payload fetches (the convention `submit_motion_order.py` established).

THE EXPERIMENT — three arms per cell so resolution and compression separate:
    A 4K native crop -- full quality
    B that crop downsampled to C's exact pixel dims -- pristine pixels at C's resolution
    C the real 226 kb/s 720p crop -- real compression, same resolution as B
  A vs C = overall source quality B vs C = COMPRESSION alone A vs B = RESOLUTION alone

 PAIRING is the whole design. This detector's response to crop framing is chaotic (0.49 vs 0.98
on the same cat, 20 px apart) but EXACTLY deterministic (same image x25 across five tablets =
spread 0.000). So repeats are worthless and unpaired cells are misleading; holding frame AND
padding fixed inside each triplet cancels the framing term and leaves only source quality.

 And sweep the MARGINAL cells, not just the easy one. A control run at 100% crop fill once
"disproved" a real effect because both arms sit at ceiling there and no difference can appear.
"""
import hashlib
import os
import shutil
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(HERE, "clips")
CAT = (1420, 880, 1600, 1040)              # cat box in 1920x1080 space
FRAMES = list(range(820, 901, 10))
PADS = [0, 45, 90, 180]
RUN = "paired-%s" % time.strftime("%Y%m%d-%H%M%S")
OUT = os.path.join(HERE, "corpus_paired", RUN)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load(clip, wanted):
    cap = cv2.VideoCapture(os.path.join(CLIPS, clip))
    got, i, need = {}, 0, set(wanted)
    while need:
        ok, fr = cap.read()
        if not ok:
            break
        if i in need:
            got[i] = fr
            need.discard(i)
        i += 1
    cap.release()
    return got


def crop(frame, pad):
    h, w = frame.shape[:2]
    x0 = max(0, int((CAT[0] - pad) / 1920.0 * w)); y0 = max(0, int((CAT[1] - pad) / 1080.0 * h))
    x1 = min(w, int((CAT[2] + pad) / 1920.0 * w)); y1 = min(h, int((CAT[3] + pad) / 1080.0 * h))
    return frame[y0:y1, x0:x1]


def emit(img, name):
    """Write at 320x320 -- the size the detector's model consumes -- so the ONLY difference
    between arms is the pixels behind the upscale, never the geometry handed to the net."""
    p = os.path.join(OUT, name)
    cv2.imwrite(p, cv2.resize(img, (320, 320), interpolation=cv2.INTER_LINEAR),
                [cv2.IMWRITE_JPEG_QUALITY, 95])
    return p


if os.path.isdir(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT)

f4 = load("cat0343_4k.mp4", FRAMES)
f7 = load("cat0343_720.mp4", FRAMES)
common = sorted(set(f4) & set(f7))
print("frames present in BOTH clips: %s" % common)

names = []
for fr in common:
    for pad in PADS:
        c4, c7 = crop(f4[fr], pad), crop(f7[fr], pad)
        if c4.size == 0 or c7.size == 0:
            print("  skip f%d pad%d - empty crop" % (fr, pad))
            continue
        cb = cv2.resize(c4, (c7.shape[1], c7.shape[0]), interpolation=cv2.INTER_AREA)
        for arm, img in (("A4k", c4), ("Bmatch", cb), ("Creal", c7)):
            n = "f%04d_p%03d_%s.jpg" % (fr, pad, arm)
            emit(img, n)
            names.append(n)

# The POSITIVE CONTROL is a copy of a cell whose correct answer is already known and exactly
# reproducible: frame 840, pad 0, arm A scores cat=0.953, verified 25x across all five nodes.
# It is published under its OWN name so it is a distinct item the validator can find, rather than
# quietly reusing a corpus row (a control that is also a measurement cannot fail independently).
ctrl_src = os.path.join(OUT, "f0840_p000_A4k.jpg")
CTRL = "CONTROL_known_cat_0953.jpg"
if not os.path.exists(ctrl_src):
    print("FAIL: control source cell missing - refusing to publish a corpus with no control")
    sys.exit(1)
shutil.copy2(ctrl_src, os.path.join(OUT, CTRL))
names.append(CTRL)

digest = hashlib.sha256()
for n in sorted(names):
    with open(os.path.join(OUT, n), "rb") as fh:
        digest.update(fh.read())
print()
print("run id      : %s" % RUN)
print("images      : %d  (%d cells x 3 arms + 1 control)" % (len(names), (len(names) - 1) // 3))
print("local dir   : %s" % OUT)
print("corpus sha  : %s" % digest.hexdigest()[:16])
print("control     : %s   expect 'cat=0.9'" % CTRL)
print()
print("next: publish to the cameras site (CAMERAS_SITE_URL) and submit -- see submit_paired_order.py")
