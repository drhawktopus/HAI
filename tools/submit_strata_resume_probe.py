"""Order the Flash-Next quick-resume proof (workspace\\flashnext\\strata_resume_probe.py) from Mothership, the box that serves it.

    python submit_strata_resume_probe.py [probe args...]

The plan is the one this box's strata_served.json names for the model: what a chat window would start. Delivers
{ORDER_OUT}\\resume.json to campaigns\\quickresume-<date>\\.

The desk is HAI_URL and the staging share is SSD_SHARED_UNC; without them there is nothing to
submit, and the script exits saying so."""
import datetime
import json
import sys
import urllib.request

import lab_settings as S

KIRK = S.head_url()
SHARE = S.setting("SSD_SHARED_UNC", "")
if not KIRK or not SHARE:
    sys.exit("no desk linked (HAI_URL) or no staging share (SSD_SHARED_UNC): nothing to submit")
USER = "ClaudeSupervisor"
HOST = "mothership"
MODEL = "qwen3.8-flash-next-strata-q2_0"
PLAN = "{FOUNDRY_ROOT}\\workspace\\flashnext\\plans\\strata-serve-q2_0-v045.20261018.json"
DAY = datetime.datetime.now().strftime("%Y%m%d")
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
spec = {"name": "strata-resume-probe.%s" % stamp, "mode": "compute", "load": "gpu", "initiator": USER,
        "payload": "{FOUNDRY_ROOT}\\workspace\\flashnext\\strata_resume_probe.py",
        "args": [PLAN, "262144", "{ORDER_OUT}"] + sys.argv[1:],
        "deliver_to": SHARE + r"\tabfoundry-jobs\campaigns\quickresume-%s" % DAY, "notify": "failures"}
body = {"spec": spec, "requires": ["order.compute", "model." + MODEL], "only": [HOST], "user": USER, "rush": True}
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=30) as r:
    out = json.loads(r.read().decode("utf-8", "replace") or "{}")
print(out.get("ok"), out.get("order"), [s.get("id") for s in out.get("steps", [])], out.get("why"), out.get("error"))
