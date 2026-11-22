"""Build a numbered contact sheet from the corpus so the owner can label it by eye.

Numbered, because the whole point is that the owner supplies ground truth ("3,7,12 have people") and I
score the filter against it. A filter graded against my own guesses measures nothing -- and my
quality calls run optimistic, which is exactly the bias that would make a bad gate look fine.

Pillow only. One sheet per channel, since the two cameras have completely different scenes and
mixing them makes the grid harder to read.
"""
import glob
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "corpus_recorded")
COLS = 5
THUMB_W = 320

for ch, label in ((1, "livingroom"), (2, "frontdoor")):
    files = sorted(glob.glob(os.path.join(SRC, "*_ch%d_*.jpg" % ch)))
    if not files:
        continue
    thumbs = []
    for f in files:
        im = Image.open(f).convert("RGB")
        h = int(im.height * THUMB_W / im.width)
        thumbs.append((os.path.basename(f), im.resize((THUMB_W, h))))

    tw, th = THUMB_W, thumbs[0][1].height
    rows = (len(thumbs) + COLS - 1) // COLS
    pad, bar = 6, 22
    sheet = Image.new("RGB", (COLS * (tw + pad) + pad, rows * (th + bar + pad) + pad),
                      (24, 26, 30))
    d = ImageDraw.Draw(sheet)

    for i, (name, im) in enumerate(thumbs):
        r, c = divmod(i, COLS)
        x = pad + c * (tw + pad)
        y = pad + r * (th + bar + pad)
        sheet.paste(im, (x, y + bar))
        # index is what the owner quotes back; timestamp is for tracing it to the event
        d.text((x + 3, y + 5), "#%d  %s" % (i + 1, name.split("_")[0]), fill=(235, 235, 235))
        d.rectangle([x, y + bar, x + tw - 1, y + bar + th - 1], outline=(70, 74, 82))

    out = os.path.join(HERE, "contact_ch%d_%s.jpg" % (ch, label))
    sheet.save(out, quality=86)
    print("ch%d %-11s %2d frames -> %s (%d x %d)"
          % (ch, label, len(thumbs), os.path.basename(out), sheet.width, sheet.height))
