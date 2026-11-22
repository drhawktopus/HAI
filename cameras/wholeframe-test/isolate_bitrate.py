"""ADVERSARIAL RE-TEST of my own headline claim: "bitrate is the lever, resolution is not."

That claim rested on comparing a pristine-4K crop (cat 0.945) against a 226 kb/s 720p crop
(0.0-0.42). Those two arms differ in FOUR things at once -- bitrate, native resolution, crop
padding (0 vs 25 px) and frame index (840 vs a 820-880 sweep of a MOVING cat). That is not a
one-variable comparison, it is a story, and I published it as a measurement.

This isolates compression properly:
  * SAME frame index (840) from both clips
  * SAME world region, SAME zero padding
  * the 4K crop is DOWNSAMPLED to the 720 crop's native pixel dimensions, so both arms carry
    identical effective resolution and the ONLY remaining difference is encoder damage
  * then both upscaled identically to 320x320

If the two now score the same, "bitrate is the lever" is dead and the real explanation was crop
framing/pose. If 4K still wins by a wide margin, the claim survives a fair test.

Also emits a contact sheet: a crop is not evidence until I have looked at it.
"""
import os
import sys

import cv2

BASE = "/ssdshared/Lab/cameras/clips"
OUT = "/tmp/iso"
os.makedirs(OUT, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CAT = (1420, 880, 1600, 1040)          # in 1920x1080 space
FRAME = 840


def grab(path, index):
    cap = cv2.VideoCapture(path)
    i = 0
    out = None
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


def region(frame):
    h, w = frame.shape[:2]
    x0 = int(CAT[0] / 1920.0 * w); y0 = int(CAT[1] / 1080.0 * h)
    x1 = int(CAT[2] / 1920.0 * w); y1 = int(CAT[3] / 1080.0 * h)
    return frame[y0:y1, x0:x1]


f4k = grab(os.path.join(BASE, "cat0343_4k.mp4"), FRAME)
f720 = grab(os.path.join(BASE, "cat0343_720.mp4"), FRAME)
if f4k is None or f720 is None:
    print("FAIL: could not read frame %d from both clips" % FRAME)
    sys.exit(1)

c4k = region(f4k)
c720 = region(f720)
print("frame %d: 4K region %dx%d, 720 region %dx%d"
      % (FRAME, c4k.shape[1], c4k.shape[0], c720.shape[1], c720.shape[0]))

# THE control: knock the 4K crop down to the 720 crop's native size, so effective resolution
# matches and only encode damage differs.
tgt = (c720.shape[1], c720.shape[0])
c4k_matched = cv2.resize(c4k, tgt, interpolation=cv2.INTER_AREA)

arms = {
    "A_4k_native":     c4k,           # pristine, full detail  (upper bound)
    "B_4k_matchedres": c4k_matched,   # pristine pixels at 720's resolution  <-- the control
    "C_720_real":      c720,          # real 226 kb/s encode at the same resolution
}
for name, img in arms.items():
    up = cv2.resize(img, (320, 320), interpolation=cv2.INTER_LINEAR)
    cv2.imwrite(os.path.join(OUT, "iso_%s.jpg" % name), up, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("  %-18s native %dx%d -> 320x320" % (name, img.shape[1], img.shape[0]))

sheet = cv2.hconcat([cv2.imread(os.path.join(OUT, "iso_%s.jpg" % n)) for n in sorted(arms)])
for i, n in enumerate(sorted(arms)):
    cv2.putText(sheet, n, (i * 320 + 6, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
cv2.imwrite(os.path.join(OUT, "SHEET.jpg"), sheet)
print("sheet written")
