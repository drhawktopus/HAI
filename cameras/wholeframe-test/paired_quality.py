"""PAIRED test of the owner's claim: does higher source quality win MORE OFTEN THAN NOT?

I retracted "quality matters" off ONE matched pair at 100% fill -- the cell where both arms sit
at ceiling and no difference can appear. That is testing an intervention in the one regime where
it cannot show, immediately after proving that single cells do not generalise here.

 but "is it
better MORE OFTEN". That is a paired sign test, and pairing is what defeats the framing chaos --
each pair holds frame AND crop fixed, so the erratic response to framing cancels out and only
source quality varies within a pair.

Three arms per cell, so resolution and compression can be separated rather than lumped:
    A 4K native crop -- full quality (resolution + bitrate)
    B 4K crop downsampled to C's exact pixel dims -- pristine pixels, C's resolution
    C real 226 kb/s 720p crop -- real compression, same resolution as B

    A vs C = does overall source quality help? (the operational question: main vs sub)
    B vs C = does COMPRESSION alone matter? (resolution held equal)
    A vs B = does RESOLUTION alone matter? (compression held ~equal)

Swept over many frames and several crop paddings so the hard/marginal cells -- the ones that
actually decide whether a cat is caught -- are represented, not just the easy ceiling case.
"""
import json
import os
import sys
import urllib.request

import cv2

BASE = "/ssdshared/Lab/cameras/clips"
URL = "http://localhost:8802/v1/vision/detection"
CAT = (1420, 880, 1600, 1040)
FRAMES = list(range(820, 901, 10))          # the window where the cat is visible
PADS = [0, 45, 90, 180]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load(clip, wanted):
    cap = cv2.VideoCapture(os.path.join(BASE, clip))
    out, i = {}, 0
    need = set(wanted)
    while need:
        ok, fr = cap.read()
        if not ok:
            break
        if i in need:
            out[i] = fr
            need.discard(i)
        i += 1
    cap.release()
    return out


def crop(frame, pad):
    h, w = frame.shape[:2]
    x0 = max(0, int((CAT[0] - pad) / 1920.0 * w)); y0 = max(0, int((CAT[1] - pad) / 1080.0 * h))
    x1 = min(w, int((CAT[2] + pad) / 1920.0 * w)); y1 = min(h, int((CAT[3] + pad) / 1080.0 * h))
    return frame[y0:y1, x0:x1]


def score(img):
    ok, buf = cv2.imencode(".jpg", cv2.resize(img, (320, 320), interpolation=cv2.INTER_LINEAR),
                           [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        return 0.0
    data = buf.tobytes()
    b = "----p"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
             "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % b).encode()
            + data + ("\r\n--%s--\r\n" % b).encode())
    req = urllib.request.Request(
        URL, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % b})
    try:
        obj = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    cats = [p for p in (obj.get("predictions") or []) if p.get("label") == "cat"]
    return max((c["confidence"] for c in cats), default=0.0)


f4 = load("cat0343_4k.mp4", FRAMES)
f7 = load("cat0343_720.mp4", FRAMES)
common = sorted(set(f4) & set(f7))
print("frames usable in BOTH clips: %s" % common)
print()
print("%-6s %-5s | %-7s %-7s %-7s | A>C   B>C   A>B" % ("frame", "pad", "A_4k", "B_match", "C_real"))
print("-" * 66)

tally = {"A>C": [0, 0, 0], "B>C": [0, 0, 0], "A>B": [0, 0, 0]}   # win, loss, tie


def note(key, x, y):
    if x is None or y is None:
        return "?"
    if abs(x - y) < 1e-6:
        tally[key][2] += 1
        return "="
    if x > y:
        tally[key][0] += 1
        return "W"
    tally[key][1] += 1
    return "L"


for fr in common:
    for pad in PADS:
        c4, c7 = crop(f4[fr], pad), crop(f7[fr], pad)
        if c4.size == 0 or c7.size == 0:
            continue
        cb = cv2.resize(c4, (c7.shape[1], c7.shape[0]), interpolation=cv2.INTER_AREA)
        a, bb, c = score(c4), score(cb), score(c7)
        print("%-6d %-5d | %-7s %-7s %-7s |  %s     %s     %s"
              % (fr, pad,
                 "%.3f" % a if a is not None else "err",
                 "%.3f" % bb if bb is not None else "err",
                 "%.3f" % c if c is not None else "err",
                 note("A>C", a, c), note("B>C", bb, c), note("A>B", a, bb)))

print()
print("=== PAIRED TALLY (win / loss / tie) ===")
for k in ("A>C", "B>C", "A>B"):
    w, l, t = tally[k]
    n = w + l
    print("  %-5s  %2d W / %2d L / %2d tie   -> wins %s of %d decided pairs"
          % (k, w, l, t, w, n))
