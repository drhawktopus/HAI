"""Score the three matched crops through the live Fellowship path."""
import glob
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
URL = "http://localhost:8802/v1/vision/detection"

for path in sorted(glob.glob("/tmp/iso/iso_*.jpg")):
    with open(path, "rb") as f:
        img = f.read()
    b = "----c"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
             "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % b).encode()
            + img + ("\r\n--%s--\r\n" % b).encode())
    req = urllib.request.Request(
        URL, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % b})
    try:
        obj = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        print("%-24s ERROR %r" % (path, e))
        continue
    preds = obj.get("predictions") or []
    top = sorted(preds, key=lambda d: -d.get("confidence", 0))[:4]
    cats = [p for p in preds if p.get("label") in ("cat", "dog")]
    print("%-26s node=%-8s CAT: %-14s   all: %s" % (
        path.split("/")[-1], obj.get("node", "?"),
        (", ".join("%s %.3f" % (c["label"], c["confidence"]) for c in cats) or "NONE"),
        ", ".join("%s %.2f" % (p["label"], p["confidence"]) for p in top) or "-"))
