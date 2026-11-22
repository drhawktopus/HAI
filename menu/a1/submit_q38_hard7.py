"""Phase 2 close-out: the champion (qwen3.8:27b, xhigh, Mothership) on the 7 HARD lead-role exercises on the CURRENT
harness (72403f73, compaction + lab provider), so all 15 compare to Flash-Next's 10-17 lead run on one harness - its
only hard-7 rows were on the 10-08 harness (cee5ef60). A copy of the 3.8's own 10-17 00:47 plan; only the exercises
change. No stop rule: this IS the baseline. The UPS Power Logger records the 3.8's whole-box watts alongside.
    python submit_q38_hard7.py"""
import datetime, json, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the menu folder
import menu_paths as MP  # noqa: E402
sys.path.insert(0, MP.FOUNDRY_MIRROR)
import foundry_paths as _P  # noqa: E402   the foundry's one settings reader
KIRK = _P.head_url()                    # '' = no head linked: there is nowhere to submit to
LOCAL = os.path.join(MP.FOUNDRY_MIRROR, "workspace", "a1")
UNC = _P.setting("SSD_SHARED_UNC", "") + "\\%s\\workspace\\a1" % os.path.basename(MP.FOUNDRY_MIRROR)
if not KIRK or not _P.setting("SSD_SHARED_UNC", ""):
    sys.exit("no head linked (HAI_URL) or no shared staging (SSD_SHARED_UNC): nothing to submit")
SRC = "a1v22-py-agentic-hai-q38-best-thon-spot-exhigh.20261017-0047.json"
HARD7 = ["dot-dsl", "forth", "pov", "tree-building", "scale-generator", "react", "sgf-parsing"]
plan = json.load(open(os.path.join(LOCAL, "plans", SRC), encoding="utf-8"))
plan["exercises"] = HARD7
plan.pop("stop_rule", None)
plan["note"] = ("Phase 2 close-out: the 3.8's hard-7 on the current harness for a one-harness comparison with "
                "Flash-Next's lead run (a1v22-...-flashnext-strata-lead.20261017-2248). Copy of " + SRC +
                "; only the exercises changed. Original note: " + (plan.get("note") or ""))
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
name = "a1v22-py-agentic-hai-q38-best-thon-spot-exhigh-hard7.%s" % stamp
pf = name + ".json"
json.dump(plan, open(os.path.join(LOCAL, "plans", pf), "w", encoding="utf-8"), indent=1)
body = {"spec": {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                 "payload": "{FOUNDRY_ROOT}\\workspace\\a1\\a1_order.py",
                 "python_exe": "{FOUNDRY_ROOT}\\venv-inspect\\Scripts\\python.exe",
                 "args": ["{ORDER_OUT}", UNC + "\\plans\\" + pf], "deliver_to": UNC + "\\delivered",
                 "notify": "failures", "progress_total": len(HARD7),
                 "model_memory": {"model": plan["model"], "num_ctx": plan["num_ctx"]}},
        "requires": ["order.compute"], "only": ["mothership"], "user": "ClaudeSupervisor"}
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as r:
    reply = json.loads(r.read().decode("utf-8", "replace"))
print(name, "->", "ok" if reply.get("ok") else "FAILED", (reply.get("steps") or [{}])[0].get("id"), reply.get("error") or "")
