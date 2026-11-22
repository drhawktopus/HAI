"""Submit the #34 chat prompt-cache probe (chat_prefix_probe.py) to one GPU factory as a compute order.
Usage: submit_chatprefix.py <host> [--item=<item.json name in this workspace dir>] [--dry]"""
import datetime
import json
import os
import sys
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import foundry_paths as _P             # this workspace's ONE settings reader

KIRK = _P.head_url()
UNC = _P.setting("SSD_SHARED_UNC", "") + r"\mothership-build\workspace\chatprefix"
if not KIRK or not _P.setting("SSD_SHARED_UNC", ""):
    sys.exit("no head linked (HAI_URL) or no shared staging (SSD_SHARED_UNC): nothing to submit")
host = [a for a in sys.argv[1:] if not a.startswith("--")][0]
item = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--item=")), None)
name = "chatprefix-%s.%s" % (host[:2], datetime.datetime.now().strftime("%Y%m%d-%H%M"))
args = ["{ORDER_OUT}"] + (["--item={FOUNDRY_ROOT}\\workspace\\chatprefix\\" + item] if item else [])
body = {"spec": {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                 "payload": "{FOUNDRY_ROOT}\\workspace\\chatprefix\\chat_prefix_probe.py", "args": args,
                 "deliver_to": UNC + "\\delivered", "notify": "failures"},
        "requires": ["order.compute"], "only": [host], "user": "ClaudeSupervisor"}
if "--dry" in sys.argv:
    print("DRY", json.dumps(body))
    sys.exit(0)
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as resp:
    reply = json.loads(resp.read().decode("utf-8", "replace"))
print("%-36s -> %s %s" % (name, "ok" if reply.get("ok") else "FAILED", reply.get("error") or ""))
