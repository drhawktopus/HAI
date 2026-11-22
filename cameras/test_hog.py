"""Give the 2005 pedestrian detector a fair shot at the real corpus.



Fair is the operative word. I argued from theory that HOG+SVM would fail on edge-of-frame partial
bodies; theory is not a measurement. So: OpenCV's stock Dalal-Triggs detector, run on this machine
(not a tablet) because the question here is "does the ALGORITHM work on this footage" -- porting
is only worth discussing if the answer is yes.

Scored against the owner's own labels, not mine (mine were wrong on 6 of 22).

Given a fair shot means:
  * a small SWEEP of scale/stride/padding rather than one arbitrary setting,
  * upscaling the frame, since these people are small in a 1280x720 wide-angle view and HOG's
    window is 64x128 -- a person occupying 90px of height is under-sized for it,
  * reporting the BEST configuration found, not the first one tried.
"""
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "labels_20260808.json"), encoding="utf-8") as f:
    L = json.load(f)["ch1_livingroom"]
PERSON, CAT = set(L["person"]), set(L["cat"])

files = sorted(glob.glob(os.path.join(HERE, "corpus_recorded", "*_ch1_*.jpg")))
assert len(files) == L["total"], "corpus/label mismatch: %d vs %d" % (len(files), L["total"])

hog = cv2.HOGDescriptor()
hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

# frames are grayscale IR; HOG works on gradients so colour is irrelevant, but SIZE matters a lot
CONFIGS = []
for upscale in (1.0, 1.5, 2.0):
    for scale in (1.03, 1.05, 1.10):
        for stride in (4, 8):
            CONFIGS.append({"upscale": upscale, "scale": scale, "stride": stride})

print("corpus: %d ch1 frames | ground truth: %d person, %d cat"
      % (len(files), len(PERSON), len(CAT)))
print("sweeping %d configs of the stock Dalal-Triggs detector ...\n" % len(CONFIGS))

results = []
for cfg in CONFIGS:
    tp = fp = 0
    t0 = time.time()
    for i, path in enumerate(files, start=1):
        im = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if cfg["upscale"] != 1.0:
            im = cv2.resize(im, None, fx=cfg["upscale"], fy=cfg["upscale"],
                            interpolation=cv2.INTER_LINEAR)
        rects, weights = hog.detectMultiScale(
            im, winStride=(cfg["stride"], cfg["stride"]), padding=(8, 8),
            scale=cfg["scale"])
        detected = len(rects) > 0
        if i in PERSON and detected:
            tp += 1
        elif i in CAT and detected:
            fp += 1
    dt = time.time() - t0
    recall = tp / len(PERSON)
    results.append({**cfg, "tp": tp, "fp": fp, "recall": recall,
                    "sec_per_frame": dt / len(files)})
    print("  upscale=%.1f scale=%.2f stride=%d -> found %2d/%d people, %d/%d cats flagged"
          "  (%.2f s/frame)"
          % (cfg["upscale"], cfg["scale"], cfg["stride"], tp, len(PERSON), fp, len(CAT),
             dt / len(files)))

best = max(results, key=lambda r: (r["recall"], -r["fp"]))
print()
print("=" * 64)
print("BEST CONFIG: upscale=%.1f scale=%.2f stride=%d"
      % (best["upscale"], best["scale"], best["stride"]))
print("  people found : %d of %d   (recall %.0f%%)" % (best["tp"], len(PERSON),
                                                       100 * best["recall"]))
print("  cats flagged : %d of %d" % (best["fp"], len(CAT)))
print("  cost         : %.2f s/frame on this machine" % best["sec_per_frame"])
with open(os.path.join(HERE, "hog_results.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, indent=1)
