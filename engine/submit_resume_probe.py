"""Submit the LOSSLESS-RESUME proof (engine_resume_probe.py) to one GPU factory, pinned (#34 B11b stage 2).

    python submit_resume_probe.py <host> <model> <num_ctx> [extra probe args...]

The box IS what is proven, so `only` pins it. Delivers {ORDER_OUT}\\resume.json to campaigns\\labengine-<date>\\."""
import datetime
import json
import os
import sys
import urllib.request
import engine_settings as E   # the head's URL and the share, from the lab's settings

KIRK = E.KIRK
USER = "ClaudeSupervisor"
DAY = datetime.datetime.now().strftime("%Y%m%d")
UNC_DIR = E.campaigns("labengine-%s" % DAY)

host, model, num_ctx = sys.argv[1], sys.argv[2], sys.argv[3]
rest = sys.argv[4:]
# --dist: the distribution proof (engine_resume_dist_probe.py) for an engine that does not reproduce its own tokens
dist = "--dist" in rest
rest = [x for x in rest if x != "--dist"]
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()    # two orders in one second must not share a name: one name = one artifact
spec = {"name": "resume%sprobe-%s-%s.%s" % ("dist" if dist else "", host[:2], model.replace(":", "-").replace(".", ""),
                                             stamp), "mode": "compute", "load": "gpu", "initiator": USER,
        "payload": "{FOUNDRY_ROOT}\\workspace\\labengine\\%s" % (
            "engine_resume_dist_probe.py" if dist else "engine_resume_probe.py"),
        "args": [model, num_ctx, "{ORDER_OUT}"] + rest, "deliver_to": UNC_DIR, "notify": "failures"}
body = {"spec": spec, "requires": ["order.compute", "model." + model], "only": [host], "user": USER, "rush": True}
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=30) as r:
    out = json.loads(r.read().decode("utf-8", "replace") or "{}")
print(out.get("ok"), out.get("order"), [s.get("id") for s in out.get("steps", [])], out.get("why"), out.get("error"))
