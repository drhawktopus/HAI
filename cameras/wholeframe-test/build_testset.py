"""Build a one-variable test set from the 03:43 IR cat clip.

The point of the experiment: separate SOURCE RESOLUTION from MODEL INPUT SIZE. Those two have
been tangled together in every previous conclusion -- "the sub stream is too small" and "the
model squashes the frame" predict the same failure, and I have been unable to say which binds.

Same instant, same cat, same encoder, three source sizes:
   src_4k.jpg 3840x2160 -- the main stream as it exists today
   src_1080.jpg 1920x1080 -- the main stream if dining stays at 1080p30
   src_d1.jpg 704x480 -- what the DETECT stream actually feeds Frigate right now
 src_d1 is a DOWNSCALE of the 4K frame, not a native D1 encode, so it is OPTIMISTIC: the real
sub stream also carries D1 encoder mush. Any advantage the big sources show here is therefore a
LOWER bound on the real advantage. Said out loud so the number is not read as exact.

Locates the cat frame by template-matching the known-good crop rather than guessing an index.
"""
import os
import sys

import cv2

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")   # the cameras folder
if len(sys.argv) != 2:
    sys.exit("usage: build_testset.py <output folder>")
OUT = sys.argv[1]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.makedirs(OUT, exist_ok=True)

tpl = cv2.imread(os.path.join(BASE, "clips", "night_crop_0343_hq.jpg"))
if tpl is None:
    print("FAIL: no template")
    sys.exit(1)
print("template %dx%d" % (tpl.shape[1], tpl.shape[0]))

cap = cv2.VideoCapture(os.path.join(BASE, "clips", "cat0343_4k.mp4"))
n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print("clip %dx%d, %d frames" % (w, h, n))

# The template was cut at some scale; match against a downscaled frame to stay cheap, then
# keep the FULL-RES frame for the winner.
best = (-1.0, None, -1)
idx = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    if idx % 5 == 0:                      # every 5th frame is plenty at 15 fps
        small = cv2.resize(frame, (1280, 720))
        for scale in (1.0, 0.75, 1.25):
            t = tpl if scale == 1.0 else cv2.resize(
                tpl, (int(tpl.shape[1] * scale), int(tpl.shape[0] * scale)))
            if t.shape[0] >= small.shape[0] or t.shape[1] >= small.shape[1]:
                continue
            r = cv2.matchTemplate(small, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(r)
            if mx > best[0]:
                best = (mx, frame.copy(), idx)
    idx += 1
cap.release()

score, frame, at = best
print("best template match %.3f at frame %d" % (score, at))
if frame is None:
    print("FAIL: no frame")
    sys.exit(1)

for name, size in [("src_4k", None), ("src_1080", (1920, 1080)), ("src_d1", (704, 480))]:
    img = frame if size is None else cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    p = os.path.join(OUT, name + ".jpg")
    cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print("wrote %-10s %dx%d  %d bytes" % (name, img.shape[1], img.shape[0],
                                           os.path.getsize(p)))

# A DAYLIGHT frame too. Night IR and lit daylight are different regimes and a model chosen on
# one can be wrong for the other -- the same "calibrate in the regime you will use it in" rule
# that put a stall threshold inside the stall band.
day = cv2.imread(os.path.join(BASE, "clips", "event_snap_0343_cat94.jpg"))
if day is not None:
    p = os.path.join(OUT, "src_eventsnap.jpg")
    cv2.imwrite(p, day, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print("wrote %-10s %dx%d" % ("src_eventsnap", day.shape[1], day.shape[0]))
