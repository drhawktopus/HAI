"""Phase 2 close-out: Flash-Next (Strata Q2_0, xhigh) on the LEAD-ROLE test, the champion's crowning protocol - a copy
of Nemotron's challenger plan with only the engine, model, context and sampling changed.
    python submit_fn_a1.py --decoy     one exercise (wordy) to prove the path
    python submit_fn_a1.py             the 15 exercises, early-stopped vs the 3.8's pooled baseline"""
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
FN = os.path.join(MP.FOUNDRY_MIRROR, "workspace", "flashnext", "plans", "strata-answers-ram64.20261017.json")
decoy = "--decoy" in sys.argv
src = json.load(open(os.path.join(LOCAL, "plans", "a1v22-py-agentic-hai-nemotron30-best-thon-spot.20261008-0755.json"),
                     encoding="utf-8"))
fn = json.load(open(FN, encoding="utf-8"))
plan = dict(src)
plan.update({
    "model": "qwen3.8-flash-next-strata-q2_0", "num_ctx": 131072, "think": "xhigh",
    "options": dict(fn["sampler"]),
    "engine": "strata",
    "strata": {k: fn[k] for k in ("strata", "gguf_dir", "data_dir", "model", "shards", "port")} |
              {"setup_args": ["--low-ram", "auto", "--context", "131072"]},
    "sampling": "Flash-Next's vendor sampling, the same as its 10-17 real-work rematch (%s); xhigh = the model "
                "template's own default, proven by prompt tokens 10-17" % json.dumps(fn["sampler"]),
    "note": "Phase 2 close-out (REOPENED 10-17): Flash-Next on Strata (64 GB, standard mode) on the champion's "
            "LEAD-ROLE test - Nemotron's challenger plan (a1v22-...-nemotron30-best-thon-spot.20261008-0755), same "
            "harness / control / exercises / stop rule; only engine, model, context (131,072, Strata --context) and "
            "sampling differ.",
})
if decoy:
    plan["exercises"] = ["wordy"]
    plan.pop("stop_rule", None)
    plan["note"] = "DECOY (proves the Strata path; not scored): " + plan["note"]
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
name = "a1v22-py-agentic-hai-flashnext-strata-%s.%s" % ("decoy" if decoy else "lead", stamp)
pf = name + ".json"
json.dump(plan, open(os.path.join(LOCAL, "plans", pf), "w", encoding="utf-8"), indent=1)
body = {"spec": {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                 "payload": "{FOUNDRY_ROOT}\\workspace\\a1\\a1_order.py",
                 "python_exe": "{FOUNDRY_ROOT}\\venv-inspect\\Scripts\\python.exe",
                 "args": ["{ORDER_OUT}", UNC + "\\plans\\" + pf], "deliver_to": UNC + "\\delivered",
                 "notify": "failures", "progress_total": len(plan["exercises"]),
                 "model_memory": {"engine": "strata", "model": plan["model"], "num_ctx": plan["num_ctx"]}},
        "requires": ["order.compute"], "only": ["mothership"], "user": "ClaudeSupervisor"}
req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as r:
    reply = json.loads(r.read().decode("utf-8", "replace"))
print(name, "->", "ok" if reply.get("ok") else "FAILED", (reply.get("steps") or [{}])[0].get("id"), reply.get("error") or "")
