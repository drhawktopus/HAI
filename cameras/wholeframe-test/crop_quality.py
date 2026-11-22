"""THE decisive test: does a better SOURCE make Frigate's CROP better?

Everything measured so far says whole-frame detection fails at every resolution, and a tight
crop succeeds (cat 0.932). But Frigate never runs whole-frame -- it finds a motion region,
crops it, and sends the crop. So the question that actually decides whether main-stream detect
is worth +20% of a core is not "does 1080p help the frame", it is:

    does the SAME motion region, cropped from 1080p instead of from 704x480, score higher?

One variable: identical region in world coordinates, identical model, identical tablets. Only
the pixels behind the crop differ. If d1 and 1080 score the same, main-stream detect is a waste
and I should say so; if 1080 wins, the +20% buys the cat.

 src_d1 is itself a downscale of the 4K frame, so it is OPTIMISTIC about the real sub stream
(which also carries D1 encoder mush at 1536 kb/s). A win for 1080 here is a LOWER bound.
"""
import os
import sys

import cv2

OUT = "/tmp/cropq"
os.makedirs(OUT, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
STAGE = "/ssdshared/tabfoundry-bench"

# The cat's box in 1920x1080 coordinates, read off the marked frame by eye.
CAT = (1420, 880, 1600, 1040)


def norm(box, w, h):
    x0, y0, x1, y1 = box
    return x0 / 1920.0 * w, y0 / 1080.0 * h, x1 / 1920.0 * w, y1 / 1080.0 * h


for src in ("src_d1", "src_1080", "src_4k"):
    img = cv2.imread(os.path.join(STAGE, src + ".jpg"))
    if img is None:
        print("missing", src)
        continue
    h, w = img.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in norm(CAT, w, h)]
    crop = img[y0:y1, x0:x1]
    # Frigate scales the region to the model input; do the same so the only difference
    # between arms is how much real detail was there to begin with.
    up = cv2.resize(crop, (320, 320), interpolation=cv2.INTER_LINEAR)
    p = os.path.join(OUT, "crop_%s.jpg" % src)
    cv2.imwrite(p, up, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print("%-9s source %dx%d -> region %dx%d px -> %s" % (
        src, w, h, crop.shape[1], crop.shape[0], p))
