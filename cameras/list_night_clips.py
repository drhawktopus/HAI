"""List all [M] motion clips for the night the window below names, both channels, deduped (main/sub
duplicates collapse on identical span). Read-only.
Emits a TSV: channel <TAB> start <TAB> end (times NVR-local) to stdout, plus a summary.
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


def window(ch, day, s, e, spans):
    for attempt in (1, 2):
        obj = None
        try:
            r = cgi("/cgi-bin/mediaFileFind.cgi?action=factory.create")
            m = re.search(r"result=(\d+)", r)
            if not m:
                raise RuntimeError("no finder")
            obj = m.group(1)
            q = ("/cgi-bin/mediaFileFind.cgi?action=findFile&object=%s"
                 "&condition.Channel=%d&condition.StartTime=%s%%20%s"
                 "&condition.EndTime=%s%%20%s" % (obj, ch, day, s, day, e))
            if "OK" not in cgi(q):
                return
            while True:
                body = cgi("/cgi-bin/mediaFileFind.cgi?action=findNextFile&object=%s"
                           "&count=100" % obj)
                found = re.search(r"found=(\d+)", body)
                n = int(found.group(1)) if found else 0
                if n == 0:
                    break
                items = {}
                for mm in re.finditer(r"items\[(\d+)\]\.(\w+)=(.*)", body):
                    items.setdefault(int(mm.group(1)), {})[mm.group(2)] = mm.group(3).strip()
                for it in items.values():
                    if "[M]" in it.get("FilePath", ""):
                        spans.add((it.get("StartTime"), it.get("EndTime")))
                if n < 100:
                    break
            return
        except Exception as exc:
            if attempt == 2:
                print("WINDOW ERR ch%d %s %s: %r" % (ch, day, s, exc), file=sys.stderr)
            else:
                time.sleep(2)
        finally:
            if obj:
                try:
                    cgi("/cgi-bin/mediaFileFind.cgi?action=close&object=%s" % obj)
                    cgi("/cgi-bin/mediaFileFind.cgi?action=destroy&object=%s" % obj)
                except Exception:
                    pass


from datetime import datetime

for ch in (1, 2):
    spans = set()
    window(ch, "2026-08-08", "22:00:00", "23:59:59", spans)
    window(ch, "2026-08-09", "00:00:00", "06:00:00", spans)
    total = 0.0
    for st, en in sorted(spans):
        try:
            dur = (datetime.strptime(en, "%Y-%m-%d %H:%M:%S")
                   - datetime.strptime(st, "%Y-%m-%d %H:%M:%S")).total_seconds()
        except Exception:
            dur = -1
        total += max(dur, 0)
        print("%d\t%s\t%s\t%.0f" % (ch, st, en, dur))
    print("# ch%d: %d clips, %.1f min" % (ch, len(spans), total / 60), file=sys.stderr)
