"""map payload: DETECT — item is an image URL; the row is what the resident detector sees in it.

Why this payload exists: scoring a corpus of frames against the detector had been
done a dozen times as ad-hoc scripts driving the fleet from outside. Scoring images IS a job with inputs, a method, deliverables and cleanup, so it is an
ORDER, and this is the missing payload that lets the factory take it.

Reuses the node's RESIDENT detector on localhost:8801 rather than loading a model per item — the
model load dwarfs the ~250 ms inference, which is the whole reason the resident worker exists
(PROJECT #32). This payload therefore adds no model state of its own and cannot drift from what
Frigate sees: it asks the exact same endpoint.

Contract: `python map_detect.py items.txt out.json`; every row carries "item" (the URL verbatim)
— see map_echo.py, the template. Errors are ROWS, not crashes: a URL that did not answer, or a
detector that returned 503, is a MEASURED result, never a missing item. Coverage validation
counts distinct "item" values, so a dropped row would read as an unexplained gap rather than a
silent pass.

 A 200 with an empty prediction list means "the detector looked and found nothing" and is a
real answer. A transport failure means "we could not ask" and is NOT the same thing — the two are
kept in different fields (`n`/`top` vs `error`) so a caller can never render one as the other.

Row fields:
    item the URL, verbatim (required by the coverage validator)
    top highest-confidence label, or "" when nothing was found
    best "<label>=<conf>" for the top hit, "" when none — this is what the control matches on
    n number of predictions
    dets every prediction: label, conf, box (full list; a floor belongs to the ANALYSIS,
           not here — a payload that pre-filters makes its own negatives unfalsifiable)
    error transport/detector failure, else ""
"""
import json
import sys
import urllib.request

DETECT_URL = "http://127.0.0.1:8801/v1/vision/detection"
FETCH_TIMEOUT = 20
DETECT_TIMEOUT = 60          # a loaded tablet also serves Frigate; be patient, not tight


def _post_image(jpeg):
    boundary = "----tabfleetdetect"
    body = (
        ("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
         "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % boundary).encode()
        + jpeg + ("\r\n--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(
        DETECT_URL, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with urllib.request.urlopen(req, timeout=DETECT_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def detect_one(url):
    row = {"item": url, "top": "", "best": "", "n": 0, "dets": [], "error": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tabfleet-detect/1.0"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            jpeg = r.read(20_000_000)
        if not jpeg:
            row["error"] = "empty body"
            return row
    except Exception as e:
        row["error"] = "fetch %s: %s" % (type(e).__name__, str(e)[:160])
        return row
    try:
        obj = _post_image(jpeg)
    except Exception as e:
        row["error"] = "detect %s: %s" % (type(e).__name__, str(e)[:160])
        return row
    if not obj.get("success", True):
        row["error"] = "detector: %s" % str(obj.get("error"))[:160]
        return row
    preds = obj.get("predictions") or []
    row["n"] = len(preds)
    row["dets"] = [{"label": p.get("label"),
                    "conf": round(float(p.get("confidence") or 0.0), 4),
                    "box": [p.get("x_min"), p.get("y_min"), p.get("x_max"), p.get("y_max")]}
                   for p in preds]
    if row["dets"]:
        best = max(row["dets"], key=lambda d: d["conf"])
        row["top"] = best["label"] or ""
        row["best"] = "%s=%.3f" % (best["label"], best["conf"])
    return row


def main(items_path, out_path):
    with open(items_path, encoding="utf-8") as f:
        items = [l.strip() for l in f if l.strip()]
    rows = [detect_one(u) for u in items]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
