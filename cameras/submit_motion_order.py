"""Submit the motion-region work to THE FELLOWSHIP as a proper map order.

 Correct -- everything until now ran as ad-hoc Python on Softserve,
which is a parallel mechanism sitting next to the factory instead of using it.

Composes the spec by hand rather than through HAI's counter because this is a NEW payload; the
counter only accepts whitelisted payload names, and `map_motion.py` was placed in payloads/ as the
deliberate human act that whitelist is designed to require.

Spec shape copied from `tablet_orders.submit_map`, not invented:
    kind, name, payload (FOUNDRY-side path), items, control{item,expect}

 Dropped atomically (.tmp then os.replace). The foundry tolerates a half-written spec with a
120 s grace period, but there is no reason to make it exercise that.
"""
import glob
import sys
import json
import os
import time
from cam_settings import need

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SHARE = need("TABLET_JOBS_HOST", "the tablets' order share")
FOUNDRY_SHARE = need("TABLET_JOBS_SEEN", "the same share, as Tolkien sees it")
BASE_URL = need("CAMERAS_SITE_URL", "the cameras site") + "/full"
CONTROL = "%s/043000_ch1_ir_CONTROL.jpg" % BASE_URL

frames = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "site", "full", "*.jpg"))
                if 'CONTROL' not in os.path.basename(p))
items = ["%s/%s" % (BASE_URL, f) for f in frames]
assert items, "no frames to submit"
assert len(items) <= 500, "MAX_ITEMS is 500 — split the job"

order = "cam-motion.%s" % time.strftime("%Y%m%d-%H%M%S")
spec = {
    "kind": "map",
    "name": order,
    "payload": "%s/payloads/map_motion.py" % FOUNDRY_SHARE,
    "items": items,
    "control": {"item": CONTROL, "expect": "boxes=1"},
}

inbox = os.path.join(SHARE, "inbox")
os.makedirs(inbox, exist_ok=True)
tmp = os.path.join(inbox, ".%s.tjob.json.tmp" % order)
dst = os.path.join(inbox, "%s.tjob.json" % order)
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(spec, f, indent=1)
os.replace(tmp, dst)

print("SUBMITTED %s" % order)
print("  items   : %d frames" % len(items))
print("  payload : map_motion.py")
print("  control : %s  expect 'boxes=1'" % CONTROL)
print("  dropped : %s" % dst)
print()
print("Watch: homepage tile 'Fellowship - Tablets'. Results land in")
print("  %s\\outbox\\%s.results.json" % (SHARE, order))
print("⚠ Read BOTH verdicts: control_passed AND complete. usable = the AND of them.")
