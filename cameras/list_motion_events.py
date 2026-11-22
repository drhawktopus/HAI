"""List today's VideoMotion recordings with their start times -> motion_events.json

Feeds the frame-extraction step. Read-only. Closes every finder object it creates (they are a
limited device resource; leaking them makes searches fail for the phone app too).
"""
import json
import os
import re
import sys
import time
import urllib.request

from nvr_settings import HOST, login
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "motion_events.json")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
USER, PW = login()
mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
mgr.add_password(None, "http://%s/" % HOST, USER, PW)
opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))


def cgi(path, timeout=30):
    return opener.open("http://%s%s" % (HOST, path), timeout=timeout).read().decode(
        "utf-8", "replace")


TODAY = time.strftime("%Y-%m-%d")
events = []

for ch in (1, 2):
    obj = None
    try:
        m = re.search(r"result=(\d+)", cgi("/cgi-bin/mediaFileFind.cgi?action=factory.create"))
        obj = m.group(1)
        cgi("/cgi-bin/mediaFileFind.cgi?action=findFile&object=%s&condition.Channel=%d"
            "&condition.StartTime=%s%%2000:00:00&condition.EndTime=%s%%2023:59:59"
            "&condition.Types[0]=dav" % (obj, ch, TODAY, TODAY))
        while True:
            body = cgi("/cgi-bin/mediaFileFind.cgi?action=findNextFile&object=%s&count=100" % obj)
            found = re.search(r"found=(\d+)", body)
            n = int(found.group(1)) if found else 0
            if n == 0:
                break
            # group all fields per item index so StartTime and Events stay associated
            items = {}
            for mm in re.finditer(r"items\[(\d+)\]\.([\w\[\]]+)=(.*)", body):
                items.setdefault(int(mm.group(1)), {})[mm.group(2)] = mm.group(3).strip()
            for _, it in sorted(items.items()):
                if it.get("Events[0]") != "VideoMotion":
                    continue
                st = it.get("StartTime", "")
                if not st:
                    continue
                events.append({"channel": ch, "start": st,
                               "end": it.get("EndTime", ""),
                               "file": it.get("FilePath", "")})
            if n < 100:
                break
    except Exception as e:
        print("ch%d ERROR %r" % (ch, e))
    finally:
        if obj:
            for a in ("close", "destroy"):
                try:
                    cgi("/cgi-bin/mediaFileFind.cgi?action=%s&object=%s" % (a, obj))
                except Exception:
                    pass

events.sort(key=lambda e: (e["start"], e["channel"]))
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(events, f, indent=1)

by_ch = {}
for e in events:
    by_ch[e["channel"]] = by_ch.get(e["channel"], 0) + 1
print("VideoMotion recordings today: %d  %s" % (len(events), by_ch))
if events:
    print("first: %s (ch%d)" % (events[0]["start"], events[0]["channel"]))
    print("last : %s (ch%d)" % (events[-1]["start"], events[-1]["channel"]))
print("-> %s" % OUT)
