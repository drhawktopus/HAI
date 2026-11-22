"""Read current Encode config for both channels from the NVR. Read-only."""
import re
import sys
import urllib.request

from nvr_settings import HOST, login
USER, PW = login()
mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
mgr.add_password(None, "http://%s/" % HOST, USER, PW)
opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

body = opener.open("http://%s/cgi-bin/configManager.cgi?action=getConfig&name=Encode"
                   % HOST, timeout=25).read().decode("utf-8", "replace")
for line in body.splitlines():
    if re.search(r"Encode\[[01]\]\.(MainFormat|ExtraFormat)\[0\]\.Video\."
                 r"(BitRate|BitRateControl|Compression|CustomResolutionName|FPS|Quality|"
                 r"resolution|Width|Height)", line):
        print(line.strip())
