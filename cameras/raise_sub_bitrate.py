"""Raise NVR sub-stream (ExtraFormat) bitrates — the IR night-detect fix.

Night IR grain starves the sub encoder and the cat's pixels don't survive 1024 kbps.
Frigate detects on the sub stream.

ch1 (Encode[0], dining, 704x480): 512 -> 1536 kbps
ch2 (Encode[1], basement, 1280x720): 1024 -> 3072 kbps

setConfig with URL-ENCODED brackets (raw brackets silently no-op), then READ BACK —
an empty response is not success (ops doc). CBR kept: one variable.
"""
import re
import sys
import urllib.parse
import urllib.request

from nvr_settings import HOST, login
USER, PW = login()
mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
mgr.add_password(None, "http://%s/" % HOST, USER, PW)
opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cgi(path, timeout=25):
    return opener.open("http://%s%s" % (HOST, path), timeout=timeout).read().decode(
        "utf-8", "replace")


TARGETS = {0: 1536, 1: 3072}

for enc, kbps in TARGETS.items():
    key = "Encode[%d].ExtraFormat[0].Video.BitRate" % enc
    q = urllib.parse.urlencode({"action": "setConfig", key: kbps})
    resp = cgi("/cgi-bin/configManager.cgi?%s" % q).strip()
    print("set %s=%d -> %r" % (key, kbps, resp[:80]))

body = cgi("/cgi-bin/configManager.cgi?action=getConfig&name=Encode")
ok = True
for enc, kbps in TARGETS.items():
    m = re.search(r"table\.Encode\[%d\]\.ExtraFormat\[0\]\.Video\.BitRate=(\d+)" % enc, body)
    got = m.group(1) if m else "MISSING"
    good = (got == str(kbps))
    ok = ok and good
    print("readback Encode[%d] ExtraFormat BitRate=%s %s" % (enc, got, "OK" if good else "FAIL"))
print("RESULT:", "ALL VERIFIED" if ok else "VERIFICATION FAILED")
