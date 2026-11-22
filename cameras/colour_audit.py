"""Is the frame COLOUR or IR-monochrome? Measure it, per frame, don't eyeball it.

Why it matters: cat identity leans heavily on colour. Zag (orange) and Stella (calico) are only
separable when the camera is in colour mode; Lucy (solid black, largest), Binks (black/white) and
Zeke (long-hair silhouette) survive monochrome. So "was this frame in colour" is a hard input to
how much the identification can be trusted -- it belongs in the pipeline, not in my head.

Method: mean saturation in HSV. An IR frame is R==G==B, so saturation collapses to ~0. This is a
measurement with an unambiguous physical basis, not a threshold I invented -- but the CUTOFF is
still a judgement, so it is named and the raw numbers are printed alongside every verdict.
"""
import glob
import os
import sys
import urllib.request

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
from nvr_settings import HOST, login
SAT_CUTOFF = 8.0   # mean HSV saturation (0-255). IR frames land ~0-2; colour lands well above.


def saturation(img):
    hsv = img.convert("HSV")
    return float(np.asarray(hsv)[:, :, 1].mean())


def verdict(s):
    return "COLOUR" if s > SAT_CUTOFF else "mono/IR"


# --- live check: what are the cameras doing right now? ---
USER, PW = login()
mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
mgr.add_password(None, "http://%s/" % HOST, USER, PW)
opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))

print("=== LIVE, right now")
import io
for ch, name in ((1, "livingroom"), (2, "frontdoor")):
    try:
        data = opener.open("http://%s/cgi-bin/snapshot.cgi?channel=%d" % (HOST, ch),
                           timeout=20).read()
        im = Image.open(io.BytesIO(data)).convert("RGB")
        s = saturation(im)
        print("  ch%d %-11s saturation %6.2f  -> %s" % (ch, name, s, verdict(s)))
        im.save(os.path.join(HERE, "live_ch%d.jpg" % ch), quality=88)
    except Exception as e:
        print("  ch%d ERROR %r" % (ch, e))

# --- corpus audit: how much of this morning was usable for colour-based cat ID? ---
print()
print("=== MORNING CORPUS (09:00-11:05)")
for ch, name in ((1, "livingroom"), (2, "frontdoor")):
    files = sorted(glob.glob(os.path.join(HERE, "corpus_recorded", "*_ch%d_*.jpg" % ch)))
    if not files:
        continue
    rows = []
    for fpath in files:
        s = saturation(Image.open(fpath).convert("RGB"))
        rows.append((os.path.basename(fpath).split("_")[0], s))
    col = [r for r in rows if r[1] > SAT_CUTOFF]
    print("  ch%d %-11s %2d frames | COLOUR %2d (%3.0f%%) | mono %2d"
          % (ch, name, len(rows), len(col), 100 * len(col) / len(rows), len(rows) - len(col)))
    if col:
        print("       first colour frame at %s (sat %.1f)" % (col[0][0], col[0][1]))
    mono = [r for r in rows if r[1] <= SAT_CUTOFF]
    if mono:
        print("       last mono frame at    %s (sat %.1f)" % (mono[-1][0], mono[-1][1]))
