"""Submit the Strata arm and its same-day llama.cpp control to Mothership's desk.

Orders are submitted back-to-back (FIFO) to ensure the control runs immediately
after the Strata arm on the same box state.

--strata-only: Re-run the Strata arm alone.
--plateau: Run Strata on 10 varied prompts, followed by the best varied llama.cpp cook."""
import datetime, json, os, sys, urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import foundry_paths as _P             # this workspace's ONE settings reader

KIRK = _P.head_url()
SSD = _P.setting("SSD_SHARED_UNC", "")
UNC = SSD + r"\mothership-build\workspace\flashnext"
if not KIRK or not SSD:
    sys.exit("no head linked (HAI_URL) or no shared staging (SSD_SHARED_UNC): nothing to submit")
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M")
ORDERS = [("flashnext-strata-q2_0.%s" % STAMP, "strata_arm.py", "strata-q2_0.20261004.json", None),
          ("flashnext-strata-control-q2k.%s" % STAMP, "ladder.py", "strata-control-q2k.20261004.json", None)]
if "--plateau" in sys.argv:                     # Strata to its plateau, then the documented best varied llama.cpp cook
    ORDERS = [("flashnext-strata-plateau.%s" % STAMP, "strata_arm.py", "strata-plateau.20261004.json", None),
              ("flashnext-strata-plateau-control.%s" % STAMP, "ladder.py", "strata-plateau-control.20261004.json", None)]
if "--vision" in sys.argv:
    ORDERS = [("flashnext-strata-vision-control.%s" % STAMP, "strata_arm.py", "strata-vision-control.20261105.json", None),
              ("flashnext-strata-vision-on.%s" % STAMP, "strata_arm.py", "strata-vision-on.20261105.json", None)]
if "--vision-built" in sys.argv:                # the serving plan + the vision section (the encoder built on this box)
    ORDERS = [("flashnext-strata-vision-on-built.%s" % STAMP, "strata_arm.py", "strata-vision-on-built.20261105.json", None)]
if "--strata-only" in sys.argv:
    ORDERS = ORDERS[:1]
if "--build-vision" in sys.argv:                # build + install + prove Strata's image encoder on this Windows AMD box
    MMPROJ = SSD + r"\tabfoundry-jobs\staging\strata-vision\mmproj-F16.gguf"
    body = {"spec": {"name": "flashnext-strata-vision-build.%s" % STAMP, "mode": "compute", "load": "cpu",
                     "initiator": "ClaudeSupervisor",
                     "payload": "{FOUNDRY_ROOT}\\workspace\\flashnext\\build_strata_vision.py",
                     "args": ["{ORDER_OUT}", UNC + "\\plans\\strata-serve-q2_0-v045.20261018.json", MMPROJ],
                     "deliver_to": UNC + "\\delivered", "notify": "failures"},
            "requires": ["order.compute"], "only": ["mothership"], "user": "ClaudeSupervisor"}
    req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        reply = json.loads(r.read().decode("utf-8", "replace"))
    print("%-48s -> %s %s %s" % (body["spec"]["name"], "ok" if reply.get("ok") else "FAILED", reply.get("error") or "",
                                 str(reply.get("placed") or reply.get("why") or "")[:160]))
    sys.exit(0)
for name, script, plan, prio in ORDERS:
    body = {"spec": {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                     "payload": "{FOUNDRY_ROOT}\\workspace\\flashnext\\%s" % script,
                     "args": ["{ORDER_OUT}", UNC + "\\plans\\" + plan], "deliver_to": UNC + "\\delivered",
                     "notify": "failures", **({"priority": prio} if prio is not None else {})},
            "requires": ["order.compute"], "only": ["mothership"], "user": "ClaudeSupervisor"}
    req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        reply = json.loads(r.read().decode("utf-8", "replace"))
    print("%-48s priority %s -> %s %s" % (name, prio, "ok" if reply.get("ok") else "FAILED", reply.get("error") or ""))
