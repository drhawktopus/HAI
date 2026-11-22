"""Enumerate TODAY's recorded motion events on the NVR (read-only).

 Rather than wait for new motion, ask the recorder what it already has.

Uses the Dahua mediaFileFind CGI: create a finder object, run a query, page the results, close it.
The finder object MUST be closed -- they are a limited resource on the device and leaking them
eventually makes searches fail for everyone, including the app.
"""
import re
import sys
import time
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


TODAY = time.strftime("%Y-%m-%d")
START = "%s%%2000:00:00" % TODAY
END = "%s%%2023:59:59" % TODAY

for ch in (1, 2):
    print("=" * 60)
    print("CHANNEL %d  (%s 00:00:00 -> 23:59:59)" % (ch, TODAY))
    obj = None
    try:
        r = cgi("/cgi-bin/mediaFileFind.cgi?action=factory.create")
        m = re.search(r"result=(\d+)", r)
        if not m:
            print("  could not create finder: %r" % r.strip()[:160])
            continue
        obj = m.group(1)

        q = ("/cgi-bin/mediaFileFind.cgi?action=findFile&object=%s"
             "&condition.Channel=%d&condition.StartTime=%s&condition.EndTime=%s"
             "&condition.Types[0]=dav" % (obj, ch, START, END))
        print("  findFile -> %s" % cgi(q).strip()[:60])

        total = 0
        events = {}
        while True:
            body = cgi("/cgi-bin/mediaFileFind.cgi?action=findNextFile&object=%s&count=100"
                       % obj)
            found = re.search(r"found=(\d+)", body)
            n = int(found.group(1)) if found else 0
            if n == 0:
                break
            total += n
            for mm in re.finditer(r"items\[\d+\]\.(\w+)=(.*)", body):
                k, v = mm.group(1), mm.group(2).strip()
                if k in ("StartTime", "EndTime", "Type", "Events\\[0\\]", "Length"):
                    pass
            for mm in re.finditer(r"items\[(\d+)\]\.Events\[0\]=(\w+)", body):
                events[mm.group(2)] = events.get(mm.group(2), 0) + 1
            starts = re.findall(r"items\[\d+\]\.StartTime=([\d\- :]+)", body)
            if starts and total <= 100:
                print("  first few start times: %s" % starts[:5])
            if n < 100:
                break
        print("  TOTAL recordings today: %d" % total)
        print("  event types seen: %s" % (events or "(none tagged)"))
    except Exception as e:
        print("  ERROR %r" % e)
    finally:
        if obj:
            try:
                cgi("/cgi-bin/mediaFileFind.cgi?action=close&object=%s" % obj)
                cgi("/cgi-bin/mediaFileFind.cgi?action=destroy&object=%s" % obj)
            except Exception:
                pass
