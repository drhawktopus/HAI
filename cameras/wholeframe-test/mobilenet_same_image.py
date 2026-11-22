"""Score the SAME images through the LIVE production path (mobilenet on the Fellowship).

One variable. The yolo11 matrix and this must differ only in the model, so both read the same
files and both go to the same tablets. Comparing yolo11-on-my-bench against a mobilenet number
remembered from yesterday's different crop would be a story, not a result.

Goes through Tolkien's deepstack endpoint on :8802 -- the exact path Frigate uses -- rather
than invoking the binary myself, so what is measured is what runs in service.
"""
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
URL = "http://localhost:8802/v1/vision/detection"
STAGE = "/ssdshared/tabfoundry-bench"

IMAGES = [
    ("ctrl_cat (known IR cat crop, 320x320)", "/tmp/ctrl_cat.jpg"),
    ("src_d1  (704x480 - what detect sees today)", os.path.join(STAGE, "src_d1.jpg")),
    ("src_1080 (1920x1080 main)", os.path.join(STAGE, "src_1080.jpg")),
    ("src_4k  (3840x2160 main)", os.path.join(STAGE, "src_4k.jpg")),
]

for label, path in IMAGES:
    if not os.path.exists(path):
        print("%-46s MISSING %s" % (label, path))
        continue
    with open(path, "rb") as f:
        img = f.read()
    boundary = "----bench"
    body = (
        ("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
         "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % boundary).encode()
        + img + ("\r\n--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(
        URL, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        obj = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        print("%-46s ERROR %r" % (label, e))
        continue
    preds = obj.get("predictions") or []
    node = obj.get("node", "?")
    print("\n=== %s   (served by %s) ===" % (label, node))
    if not preds:
        print("   no predictions returned")
    for p in sorted(preds, key=lambda d: -d.get("confidence", 0))[:8]:
        print("   %-14s %.3f  [%s,%s,%s,%s]" % (
            p.get("label"), p.get("confidence", 0),
            p.get("x_min"), p.get("y_min"), p.get("x_max"), p.get("y_max")))
    cats = [p for p in preds if p.get("label") in ("cat", "dog")]
    print("   -> CAT/DOG: %s" % (
        ", ".join("%s %.3f" % (c["label"], c["confidence"]) for c in cats) or "NONE"))
