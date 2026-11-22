"""How far back does the NVR actually hold footage? Probe by date.

The disk is FULL (UsedBytes == TotalBytes on every partition) -- normal ring-buffer behaviour --
so retention is set entirely by bitrate. Raising recording quality shortens history
proportionally, and that trade should be made with a real number rather than a guess.

Probes specific days rather than enumerating everything: a full-range findFile would page through
tens of thousands of records to answer a one-line question.
"""
import re
import sys
import time
import urllib.request

from nvr_settings import HOST, login
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

USER, PW = login()
mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
mgr.add_password(None, "http://%s/" % HOST, USER, PW)
opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))


def cgi(p, timeout=30):
    return opener.open("http://%s%s" % (HOST, p), timeout=timeout).read().decode(
        "utf-8", "replace")


def count_on(day, ch=1):
    """Recordings on a YYYY-MM-DD.

    Returns (count, error). A FAILED PROBE IS NOT AN EMPTY DAY -- the first version of this
    returned -1 for both and printed "nothing" beside it, which renders "could not tell" as a
    definite absence. That is the exact failure this lab has a doctrine row about, so the two
    outcomes are now separate values and the caller must handle them separately.

    Finder objects are a limited device resource and are closed on every path; the device also
    dislikes them being created back-to-back, hence the caller's delay.
    """
    obj = None
    try:
        r = cgi("/cgi-bin/mediaFileFind.cgi?action=factory.create")
        m = re.search(r"result=(\d+)", r)
        if not m:
            return None, "no finder object: %r" % r.strip()[:70]
        obj = m.group(1)
        cgi("/cgi-bin/mediaFileFind.cgi?action=findFile&object=%s&condition.Channel=%d"
            "&condition.StartTime=%s%%2000:00:00&condition.EndTime=%s%%2023:59:59"
            "&condition.Types[0]=dav" % (obj, ch, day, day))
        body = cgi("/cgi-bin/mediaFileFind.cgi?action=findNextFile&object=%s&count=1" % obj)
        f = re.search(r"found=(\d+)", body)
        if f:
            return int(f.group(1)), None
        if "Error" in body or "error" in body:
            return None, body.strip()[:70]
        return 0, None                      # genuinely queried, genuinely empty
    except Exception as e:
        return None, repr(e)[:70]
    finally:
        if obj:
            for a in ("close", "destroy"):
                try:
                    cgi("/cgi-bin/mediaFileFind.cgi?action=%s&object=%s" % (a, obj))
                except Exception:
                    pass


now = time.time()
print("offset | date       | result")
oldest = None
errors = 0
for back in (0, 1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90):
    day = time.strftime("%Y-%m-%d", time.localtime(now - back * 86400))
    n, err = count_on(day)
    if err is not None:
        errors += 1
        print("  -%-3d | %s | ⚠ COULD NOT MEASURE: %s" % (back, day, err))
    elif n > 0:
        oldest = (back, day)
        print("  -%-3d | %s | %d recording(s)" % (back, day, n))
    else:
        print("  -%-3d | %s | empty (measured)" % (back, day))
    time.sleep(1.5)     # the device rejects rapid finder churn

print()
if errors:
    print("⚠ %d of 13 probes FAILED TO MEASURE. Those days are UNKNOWN, not empty." % errors)
if oldest:
    print("Confirmed footage at least %d days back (%s)." % (oldest[0], oldest[1]))
    print("⚠ LOWER BOUND from sampled days — not the true retention edge.")
else:
    print("RETENTION: NOT ESTABLISHED.")
