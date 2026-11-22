"""Tag WHERE the motion was, so a human eye goes straight to it.

 Correct, and it earns its keep three ways:
  1. a human sees the right corner of the frame instantly (I missed Lucy under the table; I also
     called a soot stain a cat -- both are attention failures, not reasoning failures);
  2. the litter-box question becomes "does the motion box intersect the tray region?";
  3. box geometry (size, aspect, how low it sits) is the cheap cat-vs-person signal.

Method: per-camera MEDIAN background from the corpus. The median of many frames keeps whatever is
static and rejects whatever moved through, so people and cats vanish and the room remains. Then
diff each frame against it, threshold, and box the surviving regions.

 Backgrounds are built PER LIGHTING MODE. Mixing an IR frame into a colour median produces a
background that matches neither, and every frame then looks like it changed everywhere. Colour
mode is measured (mean saturation), not assumed.

numpy + PIL only -- deliberately, so this runs unmodified on the Fellowship tablets.
"""
import glob
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SAT_CUTOFF = 8.0
DIFF_THRESH = 34      # per-pixel grey delta counted as "changed"
MIN_AREA_FRAC = 0.0006   # ignore specks; ~550 px at 1280x720
GRID = 24             # coarse cell grid -- cheap connected-region grouping without scipy


def is_colour(im):
    return float(np.asarray(im.convert("HSV"))[:, :, 1].mean()) > SAT_CUTOFF


def grey(im):
    return np.asarray(im.convert("L"), dtype=np.int16)


def apply_overlay_mask(d):
    """Blank the burned-in timestamp before looking for motion.

    The NVR burns a clock into the top-right corner and it changes every second, so a
    median-background diff reports it as "changed": correct, and useless. Masking excludes the
    region from the measurement rather than filtering it out of the results afterwards.

    Also blanks the bottom-left channel-name caption for the same reason (static, but it sits on a
    high-contrast edge and can flicker with exposure changes).
    """
    h, w = d.shape
    d[0:int(h * 0.075), int(w * 0.60):] = False        # timestamp, top-right
    d[int(h * 0.93):, 0:int(w * 0.22)] = False         # channel caption, bottom-left
    return d


def boxes_from_mask(mask, w, h):
    """Group changed cells on a coarse grid into rectangles (flood fill on the small grid)."""
    gh, gw = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    for gy in range(gh):
        for gx in range(gw):
            if not mask[gy, gx] or seen[gy, gx]:
                continue
            stack, cells = [(gy, gx)], []
            seen[gy, gx] = True
            while stack:
                y, x = stack.pop()
                cells.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < gh and 0 <= nx < gw and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            ys = [c[0] for c in cells]
            xs = [c[1] for c in cells]
            cw, ch_ = w / gw, h / gh
            box = (int(min(xs) * cw), int(min(ys) * ch_),
                   int((max(xs) + 1) * cw), int((max(ys) + 1) * ch_))
            if (box[2] - box[0]) * (box[3] - box[1]) >= MIN_AREA_FRAC * w * h:
                out.append((box, len(cells)))
    return out


def run(src_glob, tag):
    files = sorted(glob.glob(src_glob))
    if len(files) < 4:
        print("  %s: only %d frames, need >=4 for a median background" % (tag, len(files)))
        return
    ims = [Image.open(f).convert("RGB") for f in files]
    colour = [is_colour(im) for im in ims]
    for mode, want in (("colour", True), ("mono", False)):
        idx = [i for i, c in enumerate(colour) if c == want]
        if len(idx) < 4:
            if idx:
                print("  %s/%s: %d frames -- too few for a background, SKIPPED (not 'no motion')"
                      % (tag, mode, len(idx)))
            continue
        stack = np.stack([grey(ims[i]) for i in idx])
        bg = np.median(stack, axis=0)
        outdir = os.path.join(HERE, "annotated")
        os.makedirs(outdir, exist_ok=True)
        print("  %s/%s: background from %d frames" % (tag, mode, len(idx)))
        for i in idx:
            g = grey(ims[i])
            d = apply_overlay_mask(np.abs(g - bg) > DIFF_THRESH)
            h, w = d.shape
            cy, cx = h // GRID, w // GRID
            cells = d[:cy * GRID, :cx * GRID].reshape(GRID, cy, GRID, cx).mean(axis=(1, 3))
            found = boxes_from_mask(cells > 0.18, w, h)
            ann = ims[i].copy()
            dr = ImageDraw.Draw(ann)
            for (box, ncells) in sorted(found, key=lambda b: -b[1])[:4]:
                dr.rectangle(box, outline=(255, 60, 60), width=4)
                dr.text((box[0] + 4, box[1] + 4), "motion", fill=(255, 200, 200))
            name = os.path.basename(files[i])
            ann.save(os.path.join(outdir, "ann_" + name), quality=88)
        print("     -> %d annotated" % len(idx))


print("building motion-region annotations")
run(os.path.join(HERE, "corpus_recorded", "*_ch1_*.jpg"), "recorded ch1")
run(os.path.join(HERE, "corpus_recorded", "*_ch2_*.jpg"), "recorded ch2")
run(os.path.join(HERE, "corpus", "*ch1*.jpg"), "live ch1")
run(os.path.join(HERE, "corpus", "*ch2*.jpg"), "live ch2")
