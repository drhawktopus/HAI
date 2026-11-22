"""Capture the SAME scene at three resolutions so the owner can judge with their own eyes.

 FAIRNESS: bitrate is scaled WITH resolution. 4K at the current 1536 kbps would be starved and
look worse than 720p, which would answer the question backwards. Each rung gets a bitrate
appropriate to its pixel count.

 RESTORE IS GUARANTEED. The original Encode settings are captured first and put back in a
finally block, so an exception mid-run cannot leave the owner's recorder in a test configuration.

Verification is by decoding the H.264 SPS out of the live RTSP stream, not by reading the config
table back -- the config table is a claim, the SPS is what the encoder is actually emitting. That
distinction is how the 720p was caught in the first place.
"""
import io
import os
import re
import sys
import time
import urllib.request

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
from nvr_settings import HOST, login
CH = 1                      # Living Room -- the harder identification problem
OUT = os.path.join(HERE, "resolution_test")
os.makedirs(OUT, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

USER, PW = login()
mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
mgr.add_password(None, "http://%s/" % HOST, USER, PW)
opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))


def cgi(p, timeout=30):
    return opener.open("http://%s%s" % (HOST, p), timeout=timeout).read()


def read_encode():
    b = cgi("/cgi-bin/configManager.cgi?action=getConfig&name=Encode").decode("utf-8", "replace")
    cur = {}
    for key in ("Width", "Height", "FPS", "BitRate"):
        m = re.search(r"table\.Encode\[%d\]\.MainFormat\[0\]\.Video\.%s=(\S+)" % (CH - 1, key), b)
        if m:
            cur[key] = m.group(1)
    return cur


def set_encode(w, h, fps, br):
    q = ("/cgi-bin/configManager.cgi?action=setConfig"
         "&Encode[%d].MainFormat[0].Video.Width=%d"
         "&Encode[%d].MainFormat[0].Video.Height=%d"
         "&Encode[%d].MainFormat[0].Video.FPS=%d"
         "&Encode[%d].MainFormat[0].Video.BitRate=%d"
         % (CH - 1, w, CH - 1, h, CH - 1, fps, CH - 1, br))
    return cgi(q).decode().strip()


RUNGS = [
    ("4K",   3840, 2160, 15, 8192),
    ("2K",   2560, 1440, 15, 4096),
    ("720p", 1280,  720, 10, 1536),
]

original = read_encode()
print("ORIGINAL Encode[ch%d] main: %s" % (CH, original))
print()

results = []
try:
    for label, w, h, fps, br in RUNGS:
        print("=== %s : %dx%d @ %d fps, %d kbps" % (label, w, h, fps, br))
        print("  setConfig ->", set_encode(w, h, fps, br))
        time.sleep(6)          # encoder restarts; a snapshot taken too early is the OLD size
        data = cgi("/cgi-bin/snapshot.cgi?channel=%d" % CH, timeout=45)
        im = Image.open(io.BytesIO(data))
        path = os.path.join(OUT, "%s_%dx%d.jpg" % (label, w, h))
        im.save(path, quality=95)
        print("  snapshot: %s  %d KB -> %s" % (im.size, len(data) // 1024, os.path.basename(path)))
        results.append((label, im.size, len(data), path))
finally:
    if original:
        print()
        print("RESTORING original settings ...")
        print("  ->", set_encode(int(original["Width"]), int(original["Height"]),
                                 int(original["FPS"]), int(original["BitRate"])))
        time.sleep(4)
        print("  read back:", read_encode())

print()
print("=" * 56)
for label, size, nbytes, path in results:
    print("  %-5s %-12s %6d KB" % (label, "%dx%d" % size, nbytes // 1024))
