"""Submit A1 phase 1 (Aider-Polyglot Python, agentic mode) for the three leads to Mothership's desk, one compute order
per lead, the 3.8 first (the default lead). Each lead runs at its TESTED context on Mothership (menu_fill.TESTED_CTX;
), thinking on, temperature from the model's configured vendor settings. Plans and deliveries live on the
share under mothership-build\\workspace\\a1\\; the payload is {FOUNDRY_ROOT}\\workspace\\a1\\a1_order.py.
Usage: submit_a1.py [--box=mothership|bighonker|softserve] [--only=short,...] [--dry]"""
import datetime
import hashlib
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the menu folder
import menu_paths as MP  # noqa: E402
sys.path.insert(0, MP.FOUNDRY_MIRROR)
import foundry_paths as _P  # noqa: E402   the foundry's one settings reader
KIRK = _P.head_url()                    # '' = no head linked: there is nowhere to submit to
LOCAL = os.path.join(MP.FOUNDRY_MIRROR, "workspace", "a1")
UNC = _P.setting("SSD_SHARED_UNC", "") + "\\%s\\workspace\\a1" % os.path.basename(MP.FOUNDRY_MIRROR)
if not KIRK or not _P.setting("SSD_SHARED_UNC", ""):
    sys.exit("no head linked (HAI_URL) or no shared staging (SSD_SHARED_UNC): nothing to submit")
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M")
# Each box's leads at that box's tested context (menu_fill.TESTED_CTX), most promising first: the 8 GB boxes lead with
# the tool-tuned 4B (261k context); the 9B's tested context on 8 GB is 7168, so it runs last and its cap hits are counted.
BOX_LEADS = {
    "mothership": [("qwen3.8:27b", 122880, "q38"), ("ornith-1.5:35b-a3b-q4km", 262144, "ornith"),
                   ("nemotron-3-nano:30b-iq4xs", 1048576, "nemotron30")],
    "bighonker": [("nemotron-3-nano:4b", 261120, "nemotron4"), ("qwen3:8b", 17408, "q3-8b"),
                  ("qwen3.5:9b", 7168, "q35-9b")],
    "softserve": [("nemotron-3-nano:4b", 261120, "nemotron4"), ("qwen3:8b", 17408, "q3-8b"),
                  ("qwen3.5:9b", 7168, "q35-9b")],
}
# --settings=best = the lab's ONE resolver, hard-bench\best_settings.py (LAB-TOOL-GAPS #1): top quality AND speed for
# the task type, the better of vendor and measured, never a guess. Where the families nearest A1 split
# on thinking, A1 itself measures both arms: --think=on|off is then REQUIRED.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--settings=")), "tag")
assert SETTINGS in ("tag", "best"), SETTINGS
THINK_ARG = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--think=")), None)
assert THINK_ARG in (None, "on", "off"), THINK_ARG


def tag_temperature(model):
    req = urllib.request.Request("http://%s:11434/api/show" % _P.setting_json("FLEET_HOSTS", {})[BOX],
                                 data=json.dumps({"model": model}).encode(), headers={"Content-Type": "application/json"})
    params = json.load(urllib.request.urlopen(req, timeout=30)).get("parameters") or ""
    for ln in params.splitlines():
        if ln.split() and ln.split()[0] == "temperature":
            return float(ln.split()[1])
    return None


def best_settings(model):
    """(think, options, provenance) for --settings=best, from the lab's ONE resolver (best_settings.resolve, task
    "a1"). A split on thinking refuses without --think: A1 then measures both arms."""
    import best_settings as BS
    first = BS.resolve(model, "a1", box=BOX, carried=tag_temperature(model))
    if THINK_ARG is None:
        assert first["think"] != "split", ("%s: %s - pass --think=on|off" % (model, first["evidence"][:300]))
    think_s = THINK_ARG or first["think"] or "on"
    r = BS.resolve(model, "a1", box=BOX, think=think_s, carried=tag_temperature(model))
    why = ("thinking %s by --think (an A1 arm); " % THINK_ARG if THINK_ARG else "") + "%s [%s]; split: %s" % (
        r["evidence"], r["source"], first["evidence"] if first["think"] == "split" else "no")
    return think_s == "on", ({"temperature": r["temperature"]} if r["temperature"] is not None else {}), why
BOX = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--box=")), None) or \
    ((((MP.BOXES.get("tiers") or {}).get("homes") or {}).get("lead") or [None])[0])   # default: the lead tier's home (boxes.json)
if not BOX:
    raise SystemExit("submit_a1: --box=<box> is required (boxes.json names no lead tier home)")
LEADS = BOX_LEADS[BOX]
# --lead=<model>@<ctx>@<short>: one lead not in BOX_LEADS (e.g. a cross-box comparison arm); replaces the list
_lead = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--lead=")), None)
if _lead:
    _m, _c, _s = _lead.split("@")
    LEADS = [(_m, int(_c), _s)]
TEMP_ARG = next((float(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--temperature=")), None)
dry = "--dry" in sys.argv
MODE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--mode=")), "agentic")
assert MODE in ("agentic", "agentic-hai", "aider"), MODE
only = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")]
# --exercises=a,b,c: a SPOT test on a subset; the
# name carries -spot so a subset's rows are never read as a full run
SPOT = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--exercises=")), None)
if only:
    LEADS = [l for l in LEADS if l[2] in only[0].split(",")]
    assert LEADS, "--only matched no lead on %s (have %s)" % (BOX, [l[2] for l in BOX_LEADS[BOX]])
os.makedirs(os.path.join(LOCAL, "plans"), exist_ok=True)
os.makedirs(os.path.join(LOCAL, "delivered"), exist_ok=True)
# The protocol a plan runs is NAMED: the boxes keep v2.1 as a1_harness.py (5274e5d2, frozen while v2.1
# orders are queued) and v2.2 (continue message + delegate) as a1_harness_v22.py, deployed byte-identical from this
# folder's a1_harness.py. --harness=a1_harness_v22 selects it; the label carries the deployed file's hash.
HARNESS_MOD = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--harness=")), None)
assert HARNESS_MOD in (None, "a1_harness_v22"), HARNESS_MOD
PROTOCOL = "v2.2" if HARNESS_MOD else "v2.1"
HARNESS_SHA = (hashlib.sha256(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "a1_harness.py"),
                                   "rb").read()).hexdigest()[:8] if HARNESS_MOD else "5274e5d2")
# --team=<box>:<model>: A1-team, the lead's delegate() goes to that helper as desk orders (a1_delegate). The helper's
# context is its tested max on that box and its thinking/temperature come from the resolver's coding families.
TEAM_ARG = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--team=")), None)
# --team-mode=directed: the lead is TOLD to use the helper (a1_harness.DIRECTED_BRIEF); default = the organic arm (the
# tool is merely available). the 3.8 never delegated organically, so the pricing arm is directed.
TEAM_MODE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--team-mode=")), "organic")
assert TEAM_MODE in ("organic", "directed", "mandatory"), TEAM_MODE
# --team-window: the helper runs as ONE window order for the whole run (bin\helper_window.py; a1_delegate
# WindowDelegator) instead of one desk order per piece - the per-piece fixed cost paid once. Name suffix -win.
TEAM_WINDOW = "--team-window" in sys.argv
# --team-errands: ERRAND-team (Phase 7 "Two shapes of delegation"): the lead may send look-only helpers on errands in its
# own workspace while it keeps working; ONE placed window per run (policy.json family `tool` - an errand is tool use -,
# tier 8gb, lens fastest: the report is wanted before the lead needs it); no `delegate`. Name suffix -team-err.
TEAM_ERRANDS = "--team-errands" in sys.argv
# With --team=<box>:<model> the errand helper is PINNED (a window on that box, settings from the resolver's `tool`
# family) - for an arm whose question is "do errands help the lead", a helper measured to fit keeps it one variable
# (10-10: the placed tool row on BigHonker, qwen3-vl:8b, did not fit next to the signed-in desktop session).
# --priority=N: the foundry's own queue order (p20_foundry.order_priority reads `priority` from the spec; higher runs
# first, default 0) - stated at submission instead of a ctl slip raced against the runner's pull.
PRIORITY = next((float(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--priority=")), None)
# --finish-gate: finish is refused until the lead's own tests pass on the current code (a1_harness FINISH_GATE_BRIEF;
# agentic-hai only). Its own arm, name suffix -gate.
FINISH_GATE = "--finish-gate" in sys.argv
# --behaviour=recover|correct|plan: a BEHAVIOUR arm (A1 spec "Work behaviour, scored by code") - one injected event,
# scored deterministically and apart from the solve rate. Name suffix -b<arm>.
BEHAVIOUR = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--behaviour=")), None)
assert BEHAVIOUR in (None, "recover", "correct", "plan"), BEHAVIOUR
# --effort=xhigh|medium|low: the lead thinks at this reasoning effort, sent as ollama's think level string. qwen3.8
# renders think=true as MEDIUM; its vendor default is xhigh (ollama model/renderers/qwen38_test.go).
EFFORT = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--effort=")), None)
# --turn-cap=N: the A1 runaway guard's per-call output cap (a1_harness RUNAWAY GUARD). A value comes from a turn-length
# measurement for that model (; rigby: 20,480 from 1,749 calls); compaction keeps that much room.
TURN_CAP = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--turn-cap=")), None)
assert EFFORT in (None, "xhigh", "medium", "low"), EFFORT
# --branch-from=<parent order name> --branch-point=finish|first_test: start each exercise from the parent's saved run
# up to this arm's own difference. The parent must
# be on the same model, context, effort, mode AND harness file; the order waits (rc 2) until the parent has delivered.
BRANCH_FROM = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--branch-from=")), None)
BRANCH_POINT = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--branch-point=")), None)
assert (BRANCH_FROM is None) == (BRANCH_POINT is None), "--branch-from and --branch-point go together"
assert BRANCH_POINT in (None, "finish", "first_test"), BRANCH_POINT
# --stop-vs=<baseline order>: end this arm once it cannot come within run-to-run noise of that baseline on solves
# (a1_stop; margin 2 = the measured spread of four 3.8 runs on the spot set). Not for behaviour arms or replicates.
STOP_VS = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--stop-vs=")), None)


def stop_rule(baseline):
    import a1_stop
    import reduce_a1
    return a1_stop.make_rule(baseline, UNC, reduce_a1.NON_DISCRIMINATING)


# --review=<box>:<model>: A1-review - a DIFFERENT model on another box reads the artifact once when the lead finishes;
# the lead gets one repair round (a1_harness REVIEW_BRIEF). Its own arm, name suffix -rev-<box>.
REVIEW_ARG = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--review=")), None)
REVIEW_THINK = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--review-think=")), None)
assert REVIEW_THINK in (None, "on", "off"), REVIEW_THINK
assert REVIEW_THINK is None or REVIEW_ARG, "--review-think needs --review"


def team_block(arg=None, family="coding-f2"):
    import best_settings as BS
    import menu_fill
    hbox, hmodel = (arg or TEAM_ARG).split(":", 1)
    if hbox == "rigby":           # rigby as the helper (spec decision 3a): its own decided settings, not the GPU resolver
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inspect_lab"))
        from submit_speed_probe import GPU_ATTN_NOOFF        # the speed probe's winner, ONE definition
        assert hmodel == "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf", "rigby's helper is its shortlist MoE (10-04); got %s" % hmodel
        blk = {"helper": "rigby", "helper_model": hmodel, "helper_ctx": 32768, "helper_think": False,
               "helper_temperature": 0.6, "mode": TEAM_MODE,
               "helper_settings": "rigby shortlist MoE, thinking off (10-04); ctx 32,768 (rigby's A1 orders); GT 730 "
                                  "attention + --no-op-offload, no speculation (speed probe 2026-10-07)"}
        blk.update(GPU_ATTN_NOOFF)
        return blk
    r = BS.resolve(hmodel, family, box=hbox)
    assert r["think"] in ("on", "off"), "helper %s on %s: thinking unresolved (%s)" % (hmodel, hbox, r["evidence"][:200])
    blk = {"helper": hbox, "helper_model": hmodel, "helper_ctx": menu_fill.TESTED_CTX[hbox][hmodel],
           "helper_think": r["think"] == "on", "helper_temperature": r["temperature"] if r["temperature"] is not None
           else 0.6, "helper_settings": "%s [%s]" % (r["evidence"][:200], r["source"]), "mode": TEAM_MODE}
    if TEAM_WINDOW and arg is None:
        blk["window"] = True
    return blk
for model, ctx, short in LEADS:
    if SETTINGS == "best":
        think, options, sampling = best_settings(model)
    else:
        assert THINK_ARG is None, "--think applies to --settings=best"
        think, options, sampling = True, {}, "the model's tag parameters (vendor thinking-mode values)"
    if EFFORT:
        assert think, "--effort needs thinking on"
        think = EFFORT
        sampling = "reasoning effort %s; %s" % (EFFORT, sampling)
    if TEMP_ARG is not None:          # a comparison arm pins the other arm's temperature (one variable)
        options = dict(options, temperature=TEMP_ARG)
        sampling = "temperature %s pinned by --temperature; %s" % (TEMP_ARG, sampling)
    team = team_block() if TEAM_ARG and not TEAM_ERRANDS else None
    if TEAM_ERRANDS and TEAM_ARG:
        team = dict(team_block(family="tool"), window=True, errands=True, mode="organic")
    elif TEAM_ERRANDS:
        team = {"placed": True, "window": True, "errands": True, "family": "tool", "tier": "8gb", "lens": "fastest",
                "mode": "organic", "helper": "placed",
                "helper_settings": "placed by policy.json tool/8gb/fastest; the desk fills model, effort, temperature "
                                   "and tested context from the row that placed the box; the lead's box excluded"}
    name = "a1%s-py-%s-%s%s%s%s%s%s.%s" % (PROTOCOL.replace(".", ""), MODE,
                                           "" if BOX == "mothership" else BOX[:2] + "-", short,
                                           "-best" if SETTINGS == "best" else "",
                                           "-th" + THINK_ARG if THINK_ARG else "", "-spot" if SPOT else "",
                                           (("-team-" + team["helper"][:2] + {"directed": "-dir", "mandatory": "-must"}.get(TEAM_MODE, "")
                                             + ("-win" if TEAM_WINDOW else "") + ("-err" if TEAM_ERRANDS else "")) if team else "") + ("-gate" if FINISH_GATE else "")
                                           + ("-rev-" + REVIEW_ARG.split(":", 1)[0][:2] if REVIEW_ARG else "")
                                           + ("-rth" + REVIEW_THINK if REVIEW_THINK else "")
                                           + ("-b" + BEHAVIOUR if BEHAVIOUR else "")
                                           + ("-e" + EFFORT if EFFORT else "")
                                           + ("-tc%d" % TURN_CAP if TURN_CAP else "")
                                           + ("-br" if BRANCH_FROM else ""), STAMP)
    plan = {"model": model, "num_ctx": ctx, "think": think, "mode": MODE, "exercises": SPOT or "all",
            "options": options, "sampling": sampling,
            "control": "affine-cipher",
            "protocol": "%s: Aider's brief + prompts, visible/hidden split, nudge before the loop cut (a1_harness %s)"
                        % (PROTOCOL, HARNESS_SHA),
            "note": "the owner 2026-10-04: A1 phase 1, the leads solo on Aider-Polyglot Python (34), agentic tool loop; "
                    "thinking on, vendor temperature, tested context on the box"}
    if HARNESS_MOD:
        plan["harness"] = HARNESS_MOD
    if TURN_CAP:
        assert 0 < TURN_CAP < ctx, "--turn-cap must leave room in %s's tested context %d" % (model, ctx)
        plan["turn_cap"] = TURN_CAP
    if team:
        plan["team"] = team
    if FINISH_GATE:
        assert MODE == "agentic-hai", "--finish-gate needs --mode=agentic-hai (coding_test runs the lead's own tests)"
        plan["finish_gate"] = True
    if BEHAVIOUR:
        assert MODE != "aider", "a behaviour arm needs an agentic mode"
        plan["behaviour"] = BEHAVIOUR
    if REVIEW_ARG:
        rb = team_block(REVIEW_ARG)
        assert rb["helper_model"] != model, "the reviewer must be a DIFFERENT model from the lead (decorrelated)"
        if REVIEW_THINK:   # the resolver's coding-f2 settings made the 8B a 5-token rubber stamp
            rb["helper_think"] = REVIEW_THINK == "on"
            rb["helper_settings"] = "thinking %s by --review-think (a review is not a one-shot coding answer); %s" % (
                REVIEW_THINK, rb["helper_settings"])
        plan["review"] = dict(rb, mode="review")
    if BRANCH_FROM:
        assert (BRANCH_POINT == "finish") == bool(REVIEW_ARG) or BEHAVIOUR in ("recover", "correct"), \
            "finish branches a REVIEW arm; first_test branches b-recover / b-correct (their event is after it)"
        plan["branch"] = {"from": BRANCH_FROM, "point": BRANCH_POINT,
                          "from_dir": UNC + "\\delivered\\" + BRANCH_FROM}
    if STOP_VS:
        assert not BEHAVIOUR, "a behaviour arm is not judged on solves vs a baseline"
        plan["stop_rule"] = stop_rule(STOP_VS.split(",") if "," in STOP_VS else STOP_VS)   # a,b = pooled baselines
    plan_file = "%s.json" % name
    body = {"spec": {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                     "payload": "{FOUNDRY_ROOT}\\workspace\\a1\\a1_order.py",
                     # the one A1 call path needs Inspect (setup_inspect_venv.py, rigby's pins); explicit, so the desk's
                     # host torch venv (HOST_COMPUTE_PY) does not apply
                     "python_exe": "{FOUNDRY_ROOT}\\venv-inspect\\Scripts\\python.exe",
                     "args": ["{ORDER_OUT}", UNC + "\\plans\\" + plan_file], "deliver_to": UNC + "\\delivered",
                     "notify": "failures", "progress_total": len(SPOT) if SPOT else 34,
                     "model_memory": {"model": model, "num_ctx": ctx}},
            "requires": ["order.compute"], "only": [BOX], "user": "ClaudeSupervisor"}
    if PRIORITY is not None:
        body["spec"]["priority"] = PRIORITY
    if dry:
        print("DRY", name, model, ctx, "think", think, options, "|", sampling[:110])
        continue
    json.dump(plan, open(os.path.join(LOCAL, "plans", plan_file), "w", encoding="utf-8"), indent=1)
    req =urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        reply = json.loads(r.read().decode("utf-8", "replace"))
    print("%-44s %-26s ctx %-8d -> %s %s" % (name, model, ctx, "ok" if reply.get("ok") else "FAILED",
                                              reply.get("error") or ""))
