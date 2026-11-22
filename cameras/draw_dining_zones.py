"""Overlay MY READING of the owner's hand-drawn dining zones onto the live frame, for confirmation.

The owner drew on a screenshot (2622x1206, letterboxed: camera content spans x 185-1962 of the 2000px
displayed width, full height). These are those strokes transferred to normalised coordinates.

 SHOW BEFORE APPLYING. He drew the intent; the vertex numbers are my interpretation of his line,
and an interpretation is exactly the kind of thing that looks right to its author. The basement
zones went through the same loop and his correction ("align the boxes with the actual perception
lines") was load-bearing, because Frigate tests an object's BOTTOM-CENTRE against the polygon and
slop converts directly into false positives.

 THE TWO ZONES MUST NOT OVERLAP. Red = floor/hearth (where a squat is the event). Blue = litter
box (where a squat is NORMAL). If they overlapped, a cat correctly using the tray would sit in
both and fire the alert - the opposite of the point. The floor polygon is therefore cut around
the tray rather than drawn over it.
"""
import cv2
import numpy as np

SRC = "dining_current.jpg"
OUT = "dining_zones_proposed.jpg"

# --- the owner's strokes, transferred: x=(px-185)/1777, y=py/920 -------------------------------------
FLOOR = [  # red: wooden floor + hearth
    (0.166, 0.777), (0.239, 0.774), (0.240, 0.832), (0.300, 0.836),
    (0.300, 0.878),                                  # step around the litter tray
    (0.400, 0.884),
    (0.400, 0.840), (0.445, 0.837), (0.549, 0.774), (0.569, 0.777),
    (0.588, 0.864), (0.571, 0.924), (0.515, 0.998), (0.163, 0.998),
]
LITTER = [  # blue: litter box inside the fireplace
    (0.301, 0.766), (0.397, 0.761), (0.397, 0.836), (0.309, 0.872),
]

img = cv2.imread(SRC)
h, w = img.shape[:2]
ov = img.copy()


def poly(pts):
    return np.array([[int(x * w), int(y * h)] for x, y in pts], np.int32)


cv2.fillPoly(ov, [poly(FLOOR)], (0, 0, 200))
cv2.fillPoly(ov, [poly(LITTER)], (200, 120, 0))
img = cv2.addWeighted(ov, 0.35, img, 0.65, 0)
cv2.polylines(img, [poly(FLOOR)], True, (0, 0, 255), 2)
cv2.polylines(img, [poly(LITTER)], True, (255, 150, 0), 2)
cv2.putText(img, "floor_hearth", (int(0.17 * w), int(0.965 * h)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
cv2.putText(img, "litter_tray", (int(0.30 * w), int(0.755 * h)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 150, 0), 1)

big = cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)
cv2.imwrite(OUT, big)
print("wrote %s  (%dx%d)" % (OUT, big.shape[1], big.shape[0]))
print("\nfloor_hearth coordinates: " + ",".join("%.3f,%.3f" % p for p in FLOOR))
print("\nlitter_tray  coordinates: " + ",".join("%.3f,%.3f" % p for p in LITTER))
