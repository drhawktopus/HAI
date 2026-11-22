"""THE CONTROL I SHOULD HAVE RUN FIRST: do the five tablets agree with each other?

Every comparison I made today sent ONE image ONCE and let Tolkien pick whichever fellow was
free -- so each cell was served by a different, unrecorded node. If the nodes disagree, then
every "0.92 vs 0.83" I reported is measuring WHICH TABLET ANSWERED, not the thing I varied.
That would invalidate the lot, and I would have no way to tell from the results themselves.

Sends the SAME image many times and groups the scores by the node that served it. Two failure
modes to separate:
  * between-node spread  -> the fleet is not uniform (bad model push, different build)
  * within-node spread   -> the detector is nondeterministic (threading, uninitialised buffers)
Both are fatal to n=1 comparisons; they need different fixes, so do not collapse them.
"""
import collections
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
URL = "http://localhost:8802/v1/vision/detection"
IMG = "/tmp/fill/fill_4k_pad045_frac44.jpg"     # the suspicious 0.490 cell
N = 25


def score(path):
    with open(path, "rb") as f:
        img = f.read()
    b = "----n"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
             "filename=\"x.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % b).encode()
            + img + ("\r\n--%s--\r\n" % b).encode())
    req = urllib.request.Request(
        URL, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % b})
    obj = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace"))
    cats = [p for p in (obj.get("predictions") or []) if p.get("label") == "cat"]
    return obj.get("node", "?"), (max(c["confidence"] for c in cats) if cats else 0.0)


for img in (IMG, "/tmp/fill/fill_4k_pad000_frac100.jpg"):
    by_node = collections.defaultdict(list)
    print("\n=== %s  x%d ===" % (img.split("/")[-1], N))
    for _ in range(N):
        try:
            node, s = score(img)
        except Exception as e:  # noqa: BLE001
            print("   error %r" % e)
            continue
        by_node[node].append(s)
    allv = [v for vs in by_node.values() for v in vs]
    for node in sorted(by_node):
        vs = by_node[node]
        print("   %-9s n=%-3d min %.3f  max %.3f  mean %.3f%s"
              % (node, len(vs), min(vs), max(vs), sum(vs) / len(vs),
                 "   <-- ZEROS: %d" % vs.count(0.0) if 0.0 in vs else ""))
    if allv:
        print("   OVERALL   n=%-3d min %.3f  max %.3f  spread %.3f"
              % (len(allv), min(allv), max(allv), max(allv) - min(allv)))
