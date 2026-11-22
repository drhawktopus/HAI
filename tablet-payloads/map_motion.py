"""map payload: motion — item is a frame URL; row is the motion region(s) found in it.

Contract (kind: "map"): invoked as `python map_motion.py items.txt out.json`. One item per line,
emit a JSON LIST of row dicts, every row carrying "item" verbatim -- see map_echo.py, the template.
Errors are ROWS, not crashes: a frame that could not be fetched is a MEASURED result ("this frame
did not answer"), never a missing item, or coverage cannot tell a dead URL from a skipped one.

WHAT IT DOES
  item  = <cameras site>/full/<frame>.jpg
  The payload measures whether the frame is colour or IR (mean HSV saturation -- IR frames are
  R==G==B so saturation collapses), fetches the matching median BACKGROUND for that camera and
  lighting mode from the same site's /bg/, diffs the two, and returns bounding boxes of what
  changed.

WHY A BACKGROUND RATHER THAN FRAME-TO-FRAME
  The median of many frames keeps whatever is static and rejects whatever moved through, so
  furniture and soot vanish and only real change survives. Frame-to-frame differencing would
  instead flag every lighting flicker.
  Its known limitation, stated so nobody over-trusts a row: anything present in MOST frames
  becomes part of the background. Something that stays still in view most of the time is
  invisible to this. It detects CHANGE, not objects.

The NVR burns a clock into the top-right corner that ticks every second, so it is masked out
before boxing -- otherwise every frame reports a spurious region there. The channel caption
bottom-left is masked for the same reason.

Dependencies: numpy + Pillow. Unlike the stdlib-only payloads this one needs them; if a node ever
lacks them the row carries the error rather than the batch dying.
"""
import io
import json
import os
import sys
import urllib.request

SAT_CUTOFF = 8.0
DIFF_THRESH = 34
GRID = 24
CELL_HOT = 0.18
MIN_AREA_FRAC = 0.0006
# above this fraction of hot cells the frame is dominated by a global change, not a local one
GLOBAL_CHANGE_FRAC = 0.40
BUCKET_H = 3            # hours per background bucket; MUST match publish_backgrounds.py
TIMEOUT = 25


def bg_base(url):
    """The backgrounds live on the same site as the frame: <site>/full/<frame>.jpg -> <site>/bg."""
    head, sep, _ = url.rpartition("/full/")
    return (head if sep else url.rsplit("/", 1)[0]) + "/bg"

_bg_cache = {}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "tabfleet-motion/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def boxes_from_cells(hot, w, h):
    """Group hot grid cells into rectangles. Flood fill on the small grid -- no scipy on tablets.

    `hot` is a plain list-of-lists (the caller does .tolist() so the rest of this function needs no
    numpy). Dimensions come from len(), NOT .shape -- asking a list for .shape is what made the
    first tablet run return an error row for every item.
    """
    gh, gw = len(hot), len(hot[0]) if hot else 0
    seen = [[False] * gw for _ in range(gh)]
    out = []
    for gy in range(gh):
        for gx in range(gw):
            if not hot[gy][gx] or seen[gy][gx]:
                continue
            stack, cells = [(gy, gx)], []
            seen[gy][gx] = True
            while stack:
                y, x = stack.pop()
                cells.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < gh and 0 <= nx < gw and hot[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((ny, nx))
            ys = [c[0] for c in cells]
            xs = [c[1] for c in cells]
            cw, ch = w / float(gw), h / float(gh)
            box = [int(min(xs) * cw), int(min(ys) * ch),
                   int((max(xs) + 1) * cw), int((max(ys) + 1) * ch)]
            if (box[2] - box[0]) * (box[3] - box[1]) >= MIN_AREA_FRAC * w * h:
                out.append({"box": box, "cells": len(cells)})
    out.sort(key=lambda b: -b["cells"])
    return out


def process(url):
    row = {"item": url, "boxes": [], "n_boxes": 0, "mode": "", "summary": "", "error": ""}
    try:
        import numpy as np
        from PIL import Image

        im = Image.open(io.BytesIO(fetch(url))).convert("RGB")
        sat = float(np.asarray(im.convert("HSV"))[:, :, 1].mean())
        mode = "colour" if sat > SAT_CUTOFF else "ir"
        row["mode"] = mode

        name = os.path.basename(url)
        ch = "1" if "_ch1_" in name else ("2" if "_ch2_" in name else "1")
        # TIME-BUCKETED background. One median for a whole day matched no particular moment --
        # the 3am room and the 10am room differ (objects moved, curtains) -- and every labelled
        # frame came back >50% changed. Buckets are 3h; the publisher builds each from the frames
        # NEAREST IN TIME, never merely the narrowest window (a 20-frame ±1h median measured WORSE
        # than an all-day one). Stamp is HHMMSS at the end of the first '_'-separated field.
        stamp = name.split("_")[0][-6:]
        bucket = int(stamp[0:2]) // BUCKET_H if stamp.isdigit() else 0
        bg_url = "%s/ch%s_%s_b%d.jpg" % (bg_base(url), ch, mode, bucket)
        if bg_url not in _bg_cache:
            _bg_cache[bg_url] = Image.open(io.BytesIO(fetch(bg_url))).convert("L")
        bg = _bg_cache[bg_url]

        g = np.asarray(im.convert("L"), dtype=np.int16)
        b = np.asarray(bg.resize(im.size), dtype=np.int16)

        # GLOBAL BRIGHTNESS NORMALISATION. Overnight frames drift in exposure (IR illuminator,
        # auto-gain), so EVERY pixel differs from the median and the diff reports the whole frame
        # as changed -- 48 whole-frame "regions" in the run, nearly all at 02:xx-03:xx.
        # Matching the medians first cancels a uniform shift and leaves only local change.
        # Median, not mean: a large moving object drags a mean and would normalise itself away.
        b = b + int(np.median(g) - np.median(b))
        d = np.abs(g - b) > DIFF_THRESH

        h, w = d.shape
        d[0:int(h * 0.075), int(w * 0.60):] = False      # burned-in clock, top-right
        d[int(h * 0.93):, 0:int(w * 0.22)] = False       # channel caption, bottom-left

        cy, cx = h // GRID, w // GRID
        cells = d[:cy * GRID, :cx * GRID].reshape(GRID, cy, GRID, cx).mean(axis=(1, 3))
        hot_arr = cells > CELL_HOT
        hot_frac = float(hot_arr.mean())

        # REFUSE to emit a region when nearly everything changed. A box around the whole frame is
        # not a detection -- it carries no location, and on the page it renders as a confident red
        # rectangle around the entire image, which is worse than reporting nothing. If normalisation
        # did not rescue the frame, say so honestly and emit ZERO boxes with a reason.
        if hot_frac > GLOBAL_CHANGE_FRAC:
            row["boxes"] = []
            row["n_boxes"] = 0
            row["global_change"] = True
            row["hot_frac"] = round(hot_frac, 3)
            row["summary"] = "global-change"
            return row

        hot = hot_arr.tolist()
        found = boxes_from_cells(hot, w, h)[:4]

        row["boxes"] = found
        row["n_boxes"] = len(found)
        row["global_change"] = False
        row["hot_frac"] = round(hot_frac, 3)
        row["summary"] = "boxes=%d" % len(found)
    except Exception as e:
        row["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        row["summary"] = "error"
    return row


def main(items_path, out_path):
    with open(items_path, encoding="utf-8") as f:
        items = [l.strip() for l in f if l.strip()]
    rows = [process(it) for it in items]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
