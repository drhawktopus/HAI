"""Submit the engine HISTORY probe (workspace\\labengine\\engine_history_probe.py) to one GPU factory, pinned.

    python submit_history_probe.py <host> <model> <num_ctx> <label> [extra probe args...]

Does this box's engine repeat itself across its own history? No save, restore or resume in it. `label` names the one
thing this arm changed on the box (e.g. `baseline`, `coopmat-off`), so one name is one artifact; the launcher switch
itself is set by the operator before the order and recorded by the probe (`library_env`, the runner's command line).
Delivers {ORDER_OUT}\\history.json to campaigns\\enginehistory-<date>\\."""
import datetime
import json
import os
import re
import sys
import urllib.request
import engine_settings as E   # the head's URL and the share, from the lab's settings

KIRK = E.KIRK
USER = "ClaudeSupervisor"
DAY = datetime.datetime.now().strftime("%Y%m%d")
UNC_DIR = E.campaigns("enginehistory-%s" % DAY)

host, model, num_ctx, label = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
assert re.fullmatch(r"[a-z0-9-]+", label), "label: lower-case letters, digits and dashes"
rest = sys.argv[5:]
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
spec = {"name": "enginehistory-%s-%s-%s.%s" % (host[:2], model.replace(":", "-").replace(".", ""), label, stamp),
        "mode": "compute", "load": "gpu", "initiator": USER,
        "payload": "{FOUNDRY_ROOT}\\workspace\\labengine\\engine_history_probe.py",
        "args": [model, num_ctx, "{ORDER_OUT}"] + rest, "deliver_to": UNC_DIR, "notify": "failures"}
body = {"spec": spec, "requires": ["order.compute", "model." + model], "only": [host], "user": USER, "rush": True}
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=30) as r:
    out = json.loads(r.read().decode("utf-8", "replace") or "{}")
print(out.get("ok"), spec["name"], [s.get("id") for s in out.get("steps", [])], out.get("why"), out.get("error"))
