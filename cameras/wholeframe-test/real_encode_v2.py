"""The real-encode arm, read over a window of frames rather than one matched frame.

A crop with no cat in it says nothing about the detector: "no cat" there is absence of the cat, not
absence of detection. Template matching on the low-bitrate clip peaks weakly (0.768) and can land far
from the cat frame (1200, against the 4K clip's 840); the two pulls are near the same length (2277 vs
2262), so index 840 is the right neighbourhood.

So: take a WINDOW around 840, crop the known world-region from each frame, and write them all out to
be scored AND eyeballed. If the cat is not visibly in a crop, that crop's score is not evidence about
the detector.
"""
import os
import sys

import cv2

BASE = r"/ssdshared/Lab/cameras/clips"
OUT = "/tmp/cropq2"
os.makedirs(OUT, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

cap = cv2.VideoCapture(os.path.join(BASE, "cat0343_720.mp4"))
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
CAT = (1420, 880, 1600, 1040)                     # in 1920x1080 space
x0 = int(CAT[0] / 1920.0 * w); y0 = int(CAT[1] / 1080.0 * h)
x1 = int(CAT[2] / 1920.0 * w); y1 = int(CAT[3] / 1080.0 * h)
# widen a little: the cat may sit slightly differently in this pull
pad = 25
x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
x1 = min(w, x1 + pad); y1 = min(h, y1 + pad)
print("real 720 clip %dx%d, region x %d-%d y %d-%d" % (w, h, x0, x1, y0, y1))

want = list(range(820, 881, 10))
idx = 0
got = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    if idx in want:
        crop = frame[y0:y1, x0:x1]
        up = cv2.resize(crop, (320, 320), interpolation=cv2.INTER_LINEAR)
        cv2.imwrite(os.path.join(OUT, "r720_f%04d.jpg" % idx), up,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        got += 1
    idx += 1
cap.release()
print("wrote %d crops (%d frames in clip)" % (got, idx))

# contact sheet so one look settles whether the cat is even present
import glob  # noqa: E402
files = sorted(glob.glob(os.path.join(OUT, "r720_f*.jpg")))
if files:
    row = [cv2.imread(f) for f in files]
    sheet = cv2.hconcat(row)
    for i, f in enumerate(files):
        cv2.putText(sheet, f.split("_f")[1][:4], (i * 320 + 8, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.imwrite("/tmp/cropq2/SHEET.jpg", sheet)
    print("sheet: /tmp/cropq2/SHEET.jpg  (%d panels)" % len(files))
