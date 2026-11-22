"""Submit the #34 B11b slot-dump probe (slot_dump_probe.py) to one GPU factory as a compute order.
Usage: submit_slotdump.py <host> <model> <num_ctx> [--dry]"""
import datetime
import json
import os
import sys
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import foundry_paths as _P             # this workspace's ONE settings reader

KIRK = _P.head_url()
UNC = _P.setting("SSD_SHARED_UNC", "") + r"\mothership-build\workspace\slotdump"
if not KIRK or not _P.setting("SSD_SHARED_UNC", ""):
    sys.exit("no head linked (HAI_URL) or no shared staging (SSD_SHARED_UNC): nothing to submit")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
host, model, ctx = args[0], args[1], args[2]
name = "b11b-slotdump-%s-%s.%s" % (host[:2], model.replace(":", "-").replace(".", ""),
                                   datetime.datetime.now().strftime("%Y%m%d-%H%M"))
body = {"spec": {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                 "payload": "{FOUNDRY_ROOT}\\workspace\\slotdump\\slot_dump_probe.py",
                 "args": ["{ORDER_OUT}", model, ctx], "deliver_to": UNC + "\\delivered", "notify": "failures"},
        "requires": ["order.compute"], "only": [host], "user": "ClaudeSupervisor"}
if "--dry" in sys.argv:
    print("DRY", json.dumps(body))
    sys.exit(0)
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as resp:
    reply = json.loads(resp.read().decode("utf-8", "replace"))
print("%-48s -> %s %s" % (name, "ok" if reply.get("ok") else "FAILED", reply.get("error") or ""))
