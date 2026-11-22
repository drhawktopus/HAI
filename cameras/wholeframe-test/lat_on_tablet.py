"""L1: time the detector from ON the tablet, so no network is in the number.

Written as a FILE and copied over, not built as an inline `python3 -c` string: the first attempt
did the latter and the '%%' and quoting were rewritten somewhere in the ssh -> shell chain,
producing a SyntaxError. That is the documented "text through shells corrupts silently" trap and
the documented fix is exactly this.

Prints one JSON list of per-request milliseconds so the caller cannot mistake a failure for a
fast run.
"""
import json
import os
import sys
import time
import urllib.request

IMG = os.path.expanduser("~/lat.jpg")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 15
URL = "http://127.0.0.1:8801/v1/vision/detection"

with open(IMG, "rb") as f:
    jpeg = f.read()

b = "----lat"
body = (("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
         "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % b).encode()
        + jpeg + ("\r\n--%s--\r\n" % b).encode())

ts = []
for _ in range(N):
    req = urllib.request.Request(
        URL, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % b})
    t = time.time()
    urllib.request.urlopen(req, timeout=60).read()
    ts.append((time.time() - t) * 1000.0)

print(json.dumps([round(x, 2) for x in ts]))
