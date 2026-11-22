"""The variable that actually moved: HOW MUCH OF THE CROP THE ANIMAL FILLS.

Same clip, same frame 840, same cat, same model. With no padding the crop scored 0.928; with
25 px of padding it scored NOTHING. Everything else was identical. That is a far bigger effect
than resolution (0.915 vs 0.953 across 9x the pixels) or bitrate (0.928 real encode vs 0.915
pristine at matched resolution), both of which I had been chasing.

So measure the curve directly instead of inferring it: one frame, one source, expand the crop
box in steps, score each. Padding is expressed in 1080-space and applied proportionally, so the
arms differ ONLY in how much context surrounds the cat.

Run against BOTH the pristine 4K and the real 226 kb/s encode, so the shape of the curve can be
compared across source quality rather than assumed to be the same.
"""
import os
import sys

import cv2

BASE = "/ssdshared/Lab/cameras/clips"
OUT = "/tmp/fill"
os.makedirs(OUT, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CAT = (1420, 880, 1600, 1040)          # cat box in 1920x1080 space (180 x 160)
FRAME = 840
PADS = [0, 20, 45, 90, 180, 360]       # in 1080-space pixels, per side


def grab(path, index):
    cap = cv2.VideoCapture(path)
    i, out = 0, None
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i == index:
            out = fr
            break
        i += 1
    cap.release()
    return out


for tag, clip in (("4k", "cat0343_4k.mp4"), ("real720", "cat0343_720.mp4")):
    frame = grab(os.path.join(BASE, clip), FRAME)
    if frame is None:
        print("FAIL: no frame %d in %s" % (FRAME, clip))
        continue
    h, w = frame.shape[:2]
    for pad in PADS:
        x0 = int((CAT[0] - pad) / 1920.0 * w); y0 = int((CAT[1] - pad) / 1080.0 * h)
        x1 = int((CAT[2] + pad) / 1920.0 * w); y1 = int((CAT[3] + pad) / 1080.0 * h)
        x0 = max(0, x0); y0 = max(0, y0); x1 = min(w, x1); y1 = min(h, y1)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        # fraction of the crop's AREA that the cat box occupies
        cat_w = CAT[2] - CAT[0]; cat_h = CAT[3] - CAT[1]
        crop_w = (x1 - x0) / (w / 1920.0); crop_h = (y1 - y0) / (h / 1080.0)
        frac = (cat_w * cat_h) / max(1.0, crop_w * crop_h)
        up = cv2.resize(crop, (320, 320), interpolation=cv2.INTER_LINEAR)
        name = "fill_%s_pad%03d_frac%02d.jpg" % (tag, pad, round(frac * 100))
        cv2.imwrite(os.path.join(OUT, name), up, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print("%-8s pad %3d -> crop %4dx%-4d native, cat fills %4.1f%% of area"
              % (tag, pad, crop.shape[1], crop.shape[0], frac * 100))
