"""Order the residency-and-speed probe on one factory, pinned, because the box is what is measured.

    python submit_fit_speed.py <host> <model> <window,window,...> [--predict N] [--runs N]

Delivers {ORDER_OUT}\\fitspeed.json to campaigns\\fitspeed-<date>\\. No --model/--num-ctx flags on the order on purpose: this probe exists to measure windows the fit check would refuse, as fit_probe does.

The desk is HAI_URL and the staging share is SSD_SHARED_UNC: with neither set there is nothing
to submit, and the script exits saying so."""
import datetime
import json
import os
import sys
import urllib.request

import lab_settings as S

KIRK = S.head_url()
SHARE = S.setting("SSD_SHARED_UNC", "")
if not KIRK or not SHARE:
    sys.exit("no desk linked (HAI_URL) or no staging share (SSD_SHARED_UNC): nothing to submit")
host, model, windows = sys.argv[1], sys.argv[2], sys.argv[3]
day = datetime.datetime.now().strftime("%Y%m%d")
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
unc = SHARE + r"\tabfoundry-jobs\campaigns\fitspeed-%s" % day
spec = {"name": "fitspeed-%s-%s.%s" % (host[:2], model.replace(":", "-").replace(".", "").replace("/", "-"), stamp), "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
        "payload": "{FOUNDRY_ROOT}\\workspace\\fitspeed\\fit_speed_probe.py", "args": [model, "{ORDER_OUT}", "--windows", windows] + sys.argv[4:], "deliver_to": unc, "notify": "failures"}
body = {"spec": spec, "requires": ["order.compute", "model." + model], "only": [host], "user": "ClaudeSupervisor", "rush": True}
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=30) as r:
    out = json.loads(r.read().decode("utf-8", "replace") or "{}")
print(out.get("ok"), out.get("order"), [s.get("id") for s in out.get("steps", [])], out.get("why"), out.get("error"))
