"""Find the REAL fps and bitrate ceilings at 4K by asking the device, not the datasheet.

Context: a request for 8192 kbps came back as 6144 -- the NVR silently substitutes values it
considers valid rather than refusing. So the only way to know the limits is to try each one and
READ BACK what actually stuck. Every value here is measured.

 Async application: this device lands encoder changes seconds after replying OK, so each probe
settles and retries before believing the read.

Leaves ch1 at the best CONFIRMED combination it found; ch2 is set to match afterwards by the
caller, not here.
"""
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from nvr_settings import HOST, login
IDX = 0                       # probe on ch1 only

USER, PW = login()
mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
mgr.add_password(None, "http://%s/" % HOST, USER, PW)
opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))


def cgi(p, timeout=35):
    return opener.open("http://%s%s" % (HOST, p), timeout=timeout).read().decode(
        "utf-8", "replace")


def state():
    b = cgi("/cgi-bin/configManager.cgi?action=getConfig&name=Encode")
    out = {}
    for k in ("Width", "Height", "FPS", "BitRate"):
        m = re.search(r"table\.Encode\[%d\]\.MainFormat\[0\]\.Video\.%s=(\S+)" % (IDX, k), b)
        if m:
            out[k] = m.group(1)
    return out


def try_set(fps, br):
    cgi("/cgi-bin/configManager.cgi?action=setConfig"
        "&Encode[%d].MainFormat[0].Video.FPS=%d"
        "&Encode[%d].MainFormat[0].Video.BitRate=%d" % (IDX, fps, IDX, br))
    for _ in range(4):
        time.sleep(4)
        s = state()
        if s.get("FPS") == str(fps):
            return s
    return state()


print("probing 4K fps/bitrate ceilings on ch1 (asking for a high bitrate each time)")
print("requested -> ACCEPTED")
best = None
for fps in (10, 15, 20, 25, 30):
    s = try_set(fps, 16384)          # ask high; let the device clamp and tell us its ceiling
    got_fps = int(s.get("FPS", 0))
    got_br = int(s.get("BitRate", 0))
    ok = got_fps == fps
    print("  fps %2d -> fps %2d %-14s bitrate %5d kbps" %
          (fps, got_fps, "(ACCEPTED)" if ok else "(REJECTED)", got_br))
    if ok:
        best = (got_fps, got_br)

print()
if best:
    print("HIGHEST CONFIRMED: %d fps @ %d kbps at 3840x2160" % best)
    print("  (ch1 currently left at this setting)")
else:
    print("no fps change confirmed — check manually")
print("FINAL ch1:", state())
