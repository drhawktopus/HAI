"""Submit one Terminal-Bench arm ([[34-Terminal-Bench-Lead-Level-Grid-Plan]]): a Softserve compute order running
tb_order.py with the lead's MODEL WINDOW on its own box. Settings from the lab's ONE resolver (best_settings, task
"a1" - the agentic family), context = the tested max for that model on that box (menu_fill.TESTED_CTX; 13a).

    python submit_tb.py --lead bighonker:qwen3:8b [--think on|off|xhigh|medium|low] [--tasks spot|<a,b,c>] [--label x] [--dry]
"""
import json
import os
import sys
import time
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import foundry_paths as _P             # this workspace's ONE settings reader
HB = _P.setting("HARD_BENCH_DIR", "")  # the menu folder (the hard bench)
if not HB:
    raise SystemExit("submit_tb: HARD_BENCH_DIR is not set (the menu folder, in the foundry's lab.env)")
sys.path.insert(0, HB)

KIRK = _P.head_url()
CAMP = os.path.join(os.path.dirname(_P.setting("TABLET_INBOX_HOST", "")), "campaigns", "tbench")   # the desk share's campaigns
UNC = _P.setting("SSD_SHARED_UNC", "") + r"\tabfoundry-jobs\campaigns\tbench"
# the SPOT SET, by the plan's rule: all 4 easy + the 5 shortest-expert medium from 5 categories + the shortest hard
SPOT = ["fix-git", "prove-plus-comm", "cobol-modernization", "overfull-hbox", "crack-7z-hash", "mteb-leaderboard",
        "raman-fitting", "constraints-scheduling", "kv-store-grpc", "configure-git-webserver"]


def arg(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--%s=" % name)), default)


def lead_block(spec, think_arg, ctx_arg=None):
    import best_settings as BS
    import menu_fill
    box, model = spec.split(":", 1)
    # Pinning context via --lead-ctx/--helper-ctx ensures re-runs match historical arms exactly,
    # overriding the live TESTED_CTX lookup which may have changed.
    ctx = int(ctx_arg) if ctx_arg else menu_fill.TESTED_CTX.get(box, {}).get(model)
    assert ctx, "%s on %s has no tested context - the lab does not run it there (13a)" % (model, box)
    first = BS.resolve(model, "a1", box=box)
    if think_arg is None:
        assert first["think"] != "split", "%s: %s - pass --think=on|off|xhigh|medium|low" % (model, first["evidence"][:200])
    mode = think_arg or first["think"] or "off"
    level = mode if mode in ("xhigh", "medium", "low") else None     # an effort level (qwen3.8); "on" = the bool
    think = mode != "off"
    r = BS.resolve(model, "a1", box=box, think=mode)
    return {"helper": box, "helper_model": model, "helper_ctx": int(ctx), "helper_think": think,
            "helper_think_level": level,
            "helper_temperature": r["temperature"] if r["temperature"] is not None else 0.6,
            "helper_settings": "%s [%s]" % (r["evidence"][:200], r["source"])}


def main():
    lead = lead_block(arg("lead"), arg("think"), arg("lead-ctx"))
    tasks = SPOT if arg("tasks", "spot") == "spot" else arg("tasks").split(",")
    label = arg("label") or "tb-%s-%s%s" % (lead["helper"][:2], lead["helper_model"].replace(":", "-"),
                                           "-th" if lead["helper_think"] else "")
    helper = (lead_block(arg("errands"), arg("helper-think"), arg("helper-ctx"))
              if arg("errands") else None)   # ERRAND arm
    mode = arg("errand-mode", "look")              # B8: "commands" = + RUN in a private copy + FETCH (one variable)
    assert mode in ("look", "commands"), "--errand-mode is look or commands"
    assert helper or mode == "look", "--errand-mode needs --errands (it is a property of the errand arm)"
    if helper:
        assert helper["helper"] != lead["helper"], "an errand helper on the lead's own box would evict the lead"
        label = arg("label") or label + "-err-%s-%s%s" % (helper["helper"][:2], helper["helper_model"].replace(":", "-"),
                                                          "-cmd" if mode == "commands" else "")
    stamp = time.strftime("%Y%m%d-%H%M")
    os.makedirs(os.path.join(CAMP, "plans"), exist_ok=True)
    plan = {"label": label, "tasks": tasks, "port": 8765, "lead": lead}
    if helper:
        plan["helper"] = helper
    if mode == "commands":
        plan["errand_mode"] = "commands"
    plan_name = "%s.%s.json" % (label, stamp)
    name = "%s.%s" % (label, stamp)
    body = {"spec": {"name": name, "mode": "compute", "load": "cpu", "initiator": "ClaudeSupervisor",
                     "payload": "{FOUNDRY_ROOT}\\workspace\\tbench\\tb_order.py",
                     "args": ["{ORDER_OUT}", UNC + "\\plans\\" + plan_name], "deliver_to": UNC + "\\delivered",
                     "notify": "failures", "priority": 1,
                     # coexists_with_plex is true because Softserve runs only an ssh client here (CPU-heavy work is isolated to Sundae), so it does not trigger Plex's CPU-contention blocker.
                     "coexists_with_plex": True},
            "requires": ["order.compute"], "only": ["softserve"], "user": "ClaudeSupervisor"}
    print(json.dumps(plan, indent=1))
    if "--dry" in sys.argv:
        print("DRY", name)
        return 0
    json.dump(plan, open(os.path.join(CAMP, "plans", plan_name), "w", encoding="utf-8"), indent=1)
    req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    reply = json.load(urllib.request.urlopen(req, timeout=60))
    print(name, "->", reply.get("ok"), reply.get("routed_to"), reply.get("error") or "")
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
