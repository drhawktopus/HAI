"""Set BOTH cameras' main stream to 4K. The owner's decision.

3840x2160 @ 10 fps, 8192 kbps.

Choices, stated so they can be argued with later:
  * FPS stays at 10 (not raised). At a fixed bitrate, more frames means fewer bits per frame, and
    every goal here -- which cat, what is it doing, is it at the tray -- is a SPATIAL question.
    Spending the budget on pixels rather than on temporal smoothness is deliberate.
  * Substreams are left at 704x480. They are the cheap tier's input (motion regions, ROI tests);
    making them bigger costs storage and buys nothing.

 THIS DEVICE APPLIES ENCODER CHANGES ASYNCHRONOUSLY. A 4-second settle produced a read-back that
still showed the OLD resolution, which is how an earlier restore appeared to fail and how a "2K"
capture came out as 4K. So: generous settle, verify with retries, and finally confirm against the
H.264 SPS in the live stream -- the config table is a claim, the SPS is what is actually emitted.
"""
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from nvr_settings import HOST, login
W, H, FPS, BR = 3840, 2160, 10, 8192

USER, PW = login()
mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
mgr.add_password(None, "http://%s/" % HOST, USER, PW)
opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))


def cgi(p, timeout=35):
    return opener.open("http://%s%s" % (HOST, p), timeout=timeout).read().decode(
        "utf-8", "replace")


def state(idx):
    b = cgi("/cgi-bin/configManager.cgi?action=getConfig&name=Encode")
    out = {}
    for k in ("Width", "Height", "FPS", "BitRate"):
        m = re.search(r"table\.Encode\[%d\]\.MainFormat\[0\]\.Video\.%s=(\S+)" % (idx, k), b)
        if m:
            out[k] = m.group(1)
    return out


print("BEFORE")
for idx, name in ((0, "ch1 Living Room"), (1, "ch2 Front Door/bedroom")):
    print("  %-24s %s" % (name, state(idx)))

print()
for idx, name in ((0, "ch1 Living Room"), (1, "ch2 Front Door/bedroom")):
    print("setting %s -> %dx%d @ %d fps, %d kbps" % (name, W, H, FPS, BR))
    r = cgi("/cgi-bin/configManager.cgi?action=setConfig"
            "&Encode[%d].MainFormat[0].Video.Width=%d"
            "&Encode[%d].MainFormat[0].Video.Height=%d"
            "&Encode[%d].MainFormat[0].Video.FPS=%d"
            "&Encode[%d].MainFormat[0].Video.BitRate=%d"
            % (idx, W, idx, H, idx, FPS, idx, BR))
    print("  setConfig -> %s" % r.strip()[:40])

    ok = False
    for attempt in range(6):
        time.sleep(5)
        s = state(idx)
        if s.get("Width") == str(W) and s.get("Height") == str(H):
            print("  confirmed after %ds: %s" % (5 * (attempt + 1), s))
            ok = True
            break
        print("  ...not applied yet (%s)" % s)
    if not ok:
        print("  ⚠ FAILED to confirm %s — check manually" % name)

print()
print("=" * 58)
print("FINAL config-table state:")
for idx, name in ((0, "ch1"), (1, "ch2")):
    print("  %s %s" % (name, state(idx)))
print()
print("Now verify against the STREAM (run rtsp_describe.py) — the config table is a claim.")
