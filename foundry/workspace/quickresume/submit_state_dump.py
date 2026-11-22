"""Submit #37's state-dump measurement to a factory's desk (shape copied from flashnext/submit_strata.py: payload in
{FOUNDRY_ROOT}\\workspace\\quickresume, plan on the share, delivered to workspace\\quickresume\\delivered). Default
priority: FIFO behind whatever is queued.

    python submit_state_dump.py [box] [plan file name]      (default: mothership state-dump.20261016.json)"""
import datetime
import json
import os
import sys
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import foundry_paths as _P             # this workspace's ONE settings reader

KIRK = _P.head_url()
UNC = _P.setting("SSD_SHARED_UNC", "") + r"\mothership-build\workspace\quickresume"
box = sys.argv[1] if len(sys.argv) > 1 else "mothership"
plan = sys.argv[2] if len(sys.argv) > 2 else "state-dump.20261016.json"
name = "quickresume-state-dump.%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M")
body = {"spec": {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                 "payload": "{FOUNDRY_ROOT}\\workspace\\quickresume\\state_dump.py",
                 "args": ["{ORDER_OUT}", UNC + "\\plans\\" + plan],
                 "deliver_to": UNC + "\\delivered", "notify": "failures"},
        "requires": ["order.compute"], "only": [box], "user": "ClaudeSupervisor"}
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as r:
    reply = json.loads(r.read().decode("utf-8", "replace"))
print(name, "->", "ok" if reply.get("ok") else "FAILED", json.dumps(reply)[:300])
