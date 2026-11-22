"""Submit the Vulkan op-by-op diagnostic (workspace\\labengine\\vulkan_checklog_run.py) to Mothership, pinned.

    python submit_checklog.py <model> <num_ctx> [label]

It builds the diagnostic Vulkan backend from lab-engine\\diag\\llama.cpp-620d222-r4-checklog (make it first with
diag\\make_checklog_tree.py), runs it beside the lab's engine tree and delivers checklog.json + logs to
campaigns\\enginehistory-<date>\\. It installs nothing."""
import datetime
import json
import os
import sys
import urllib.request
import engine_settings as E   # the head's URL and the share, from the lab's settings

KIRK = E.KIRK
USER = "ClaudeSupervisor"
DAY = datetime.datetime.now().strftime("%Y%m%d")
UNC_DIR = E.campaigns("enginehistory-%s" % DAY)
SRC = E.engine_unc("diag", "llama.cpp-620d222-r4-checklog")
assert os.path.isdir(os.path.join(E.HERE, "diag", "llama.cpp-620d222-r4-checklog")), "run diag\\make_checklog_tree.py first"

model, num_ctx = sys.argv[1], sys.argv[2]
label = sys.argv[3] if len(sys.argv) > 3 else "run"
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
spec = {"name": "vkchecklog-%s-%s.%s" % (model.replace(":", "-").replace(".", ""), label, stamp), "mode": "compute",
        "load": "gpu", "initiator": USER, "payload": "{FOUNDRY_ROOT}\\workspace\\labengine\\vulkan_checklog_run.py",
        "args": [SRC, model, num_ctx, "{ORDER_OUT}"], "deliver_to": UNC_DIR, "notify": "failures"}
body = {"spec": spec, "requires": ["order.compute", "model." + model], "only": ["mothership"], "user": USER, "rush": True}
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=30) as r:
    out = json.loads(r.read().decode("utf-8", "replace") or "{}")
print(out.get("ok"), spec["name"], [s.get("id") for s in out.get("steps", [])], out.get("why"), out.get("error"))
