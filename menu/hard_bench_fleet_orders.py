"""Hard Bench — FLEET rungs as real FACTORY ORDERS.

submit writes items.json (10 tasks + control_add, the tablet payload's prompt + SUFFIX + no-tools line) to the
        share and POSTs two `mode:"compute"` orders to Starfleet (Kirk's desk, never a host named):
          9b requires ["order.compute", "model.qwen3.5:9b"] -> the 8 GB tier (desk's choice)
          27b requires ["order.compute", "model.qwen3.6:27b"] -> Mothership
        payload {FOUNDRY_ROOT}\\bin\\infer_ollama.py, rounds=3 (production sampling: no temperature given),
        progress declared so the tile + watch.compute see N/M. Every step is claimed, visible, checkpointed.
watch deliverable-based: exits DONE when both out.json files exist, or STILL_RUNNING (re-arm) — never on a clock.
reduce same checker as the tablets (payload.extract_code -> payload.score); per (task, rung) pass rate over
        rounds, best-of-3, served model; verification runs OFFLINE here and is recorded as such."""
import collections
import csv
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import statistics
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_price as MP  # noqa: E402  delivered_out: the newest delivered generation of an order's out
import tempfile
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hard_tasks  # noqa: E402
import hard_bench_matrix as mx  # noqa: E402
import menu_price as MP  # noqa: E402

import menu_paths as MPATH  # noqa: E402  every folder the menu reads
LS = MPATH.LS                 # the head's address and the share's UNC root are settings (HAI_URL, SSD_SHARED_UNC)

KIRK = LS.head_url()
SHARE = LS.setting("SSD_SHARED_UNC")
USER = "ClaudeSupervisor"
OUT_DIR = pathlib.Path(MPATH.CAMPAIGNS) / "hard-bench-20260927" / "fleet"
UNC_DIR = SHARE + r"\tabfoundry-jobs\campaigns\hard-bench-20260927\fleet"
# --tag <t> [--temperature <x>] [--date YYYYMMDD]: a knob sweep gets its OWN product folder (fleet-<t>), so it can never
# overwrite the production-sampling run above; --temperature pins the knob and seeds (seed = 1 + round).
_arg = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
TAG, TEMPERATURE = _arg("--tag", ""), _arg("--temperature")
if TAG:
    _day = _arg("--date", datetime.datetime.now().strftime("%Y%m%d"))
    # w1ops lives in its own campaign: the menu readers class anything named "-w1"/"_w1" under hard-bench-* as W1
    # chat1 likewise: the menu filler reads an unrecognised hard-bench-* folder as family 1
    _camp = {"w1ops": "w1ops", "chat1": "chat1"}.get(_arg("--pool"), "hard-bench")
    OUT_DIR = pathlib.Path(MPATH.CAMPAIGNS) / ("%s-%s" % (_camp, _day)) / ("fleet-%s" % TAG)
    UNC_DIR = SHARE + r"\tabfoundry-jobs\campaigns\%s-%s\fleet-%s" % (_camp, _day, TAG)
# No box is excluded. Any rung may land on Softserve when the desk routes there. The 9B's old Softserve exclusion was Softserve's ollama config, not a fit limit: since
# the 9B fits Softserve fully up to 7,168 (RUNBOOK "Fleet VRAM residency map").
EXCLUDE = {}
# --pool f2: the second coding family (hard_tasks_f2.py) - every task runs (the fleet has no band to pick), graded by
# its own generated payload. Default: family 1's 10 matrix tasks, as before.
POOL_TASKS, POOL_INBAND = mx.TASKS, mx.IN_BAND
if _arg("--pool") == "f2":
    assert TAG, "--pool f2 needs a --tag (its own product folder)"
    import hard_tasks_f2 as hard_tasks  # noqa: E402,F811
    POOL_TASKS = POOL_INBAND = list(hard_tasks.TASKS)
# --pool w1: the work-shaped family (hard_tasks_w1.py, charter step 2) - code, exact-answer reasoning, lab ops; each
# prompt carries its own answer format, so the payload adds no "return only the function" suffix
if _arg("--pool") == "w1":
    assert TAG, "--pool w1 needs a --tag (its own product folder)"
    import hard_tasks_w1 as hard_tasks  # noqa: E402,F811
    POOL_TASKS = POOL_INBAND = list(hard_tasks.TASKS)
# --pool c1: the chat/generalist family (hard_tasks_c1.py) - canonical ANSWER blocks like W1
if _arg("--pool") == "c1":
    assert TAG, "--pool c1 needs a --tag (its own product folder)"
    import hard_tasks_c1 as hard_tasks  # noqa: E402,F811
    POOL_TASKS = POOL_INBAND = list(hard_tasks.TASKS)
# --pool w1ops: W1's 8 ops tasks, letters shuffled, new ids (hard_tasks_w1ops.py)
if _arg("--pool") == "w1ops":
    assert TAG, "--pool w1ops needs a --tag (its own product folder)"
    import hard_tasks_w1ops as hard_tasks  # noqa: E402,F811
    POOL_TASKS = POOL_INBAND = list(hard_tasks.TASKS)
# --pool chat1: the small tier's one-line chat turn (hard_tasks_chat1.py, HAI-Agent-Surface-Design measurement 3) on
# the GPU boxes too, so every tier is priced on the SAME turns; the bare request, nothing appended
if _arg("--pool") == "chat1":
    assert TAG, "--pool chat1 needs a --tag (its own product folder)"
    import hard_tasks_chat1 as hard_tasks  # noqa: E402,F811
    POOL_TASKS = POOL_INBAND = list(hard_tasks.TASKS)
# the pool's own payload (its scorer + task table) - chosen AFTER the pool, never overwritten below it; a new order runs
# the newest generation (task_versions), whose task table carries the ids the prompts above are built under
import task_versions as TVER  # noqa: E402
PAYLOAD = pathlib.Path(MPATH.PAYLOADS) / TVER.newest_payload(
    {"f2": "map_swarm_code_hard_f2.py", "w1": "map_swarm_code_hard_w1.py", "chat1": "map_swarm_code_hard_chat1.py",
     "c1": "map_swarm_code_hard_c1.py", "w1ops": "map_swarm_code_hard_w1ops.py"}.get(_arg("--pool"),
                                                                                    "map_swarm_code_hard.py"))
assert PAYLOAD.exists(), "the pool's payload %s is not generated (gen_hard_payload.py)" % PAYLOAD.name
ALL_RUNGS = {"9b": "qwen3.5:9b", "27b": "qwen3.6:27b", "27b38": "qwen3.8:27b",
             # the rest of what the factories advertise: each routes to whoever holds it
             "coder30b": "qwen3-coder:30b", "36moe": "qwen3.6:35b-a3b", "q3-14b": "qwen3:14b", "q3-8b": "qwen3:8b",
             # the SAME weights as qwen3.8:27b (same ollama id); the tag only turns on multi-token-prediction drafting
             "27b38mtp": "qwen3.8:27b-mtp-q4_K_M",
             # the vision model on text work; tested max on the 8 GB boxes is 5,120
             "vl8b": "qwen3-vl:8b"}
# --rungs <list>: anything but the default is a knob run in its own --tag folder. The default is the production
# pair; its heavy rung is qwen3.8:27b - the
# rung KEY differs from 3.6's ("27b"), so no earlier 3.6 product is overwritten.
DEFAULT_RUNGS = ["9b", "27b38"]
if _arg("--model-name"):
    # any model a box holds (menu_fill.py's full grid): one rung, keyed by a slug of its name
    import re as _re
    RUNGS = {_re.sub(r"[^a-z0-9.]+", "-", _arg("--model-name").lower()).strip("-")[:48]: _arg("--model-name")}
else:
    RUNGS = {k: ALL_RUNGS[k] for k in (_arg("--rungs") or ",".join(DEFAULT_RUNGS)).split(",")}
assert TAG or list(RUNGS) == DEFAULT_RUNGS, "--rungs other than the default needs a --tag (its own product folder)"
ROUNDS = 3
# 6144, not 8192: at 8192 the 9B loads 33/34 layers on BigHonker's 3070 (one on CPU) -- by
# the runner investigation -- so an 8192 run is not the 9B on the GPU. The 27B fits either way.
NUM_CTX = int(sys.argv[sys.argv.index("--num-ctx") + 1]) if "--num-ctx" in sys.argv else 6144
NO_TOOLS = ("" if _arg("--pool") == "chat1"          # a chat turn is the bare request (no tools are sent anyway)
            else "\nDo not use any tools and do not search." if _arg("--pool") in ("w1", "c1", "w1ops")
            else "\nDo not use any tools and do not search; write the function from the description alone.")


def _payload():
    spec = importlib.util.spec_from_file_location("hard_payload", PAYLOAD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def http(method, path, body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(KIRK + path, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def submit():
    mod = _payload()
    missing = [t for t in POOL_TASKS if t not in mod.TASKS]
    assert not missing, "payload %s lacks this pool's tasks %s - the on-box grader would grade nothing" % (PAYLOAD.name, missing[:5])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tasks = dict(hard_tasks.TASKS)
    tasks["control_add"] = mod.CONTROL_TASKS["control_add"]
    items = [{"id": t, "prompt": tasks[t]["prompt"] + mod.SUFFIX + NO_TOOLS} for t in POOL_TASKS + ["control_add"]]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    items_name = "items.%s.json" % stamp                  # inputs are per submission too: nothing is rewritten
    (OUT_DIR / items_name).write_text(json.dumps({"items": items}, indent=1), encoding="utf-8")
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else list(RUNGS)
    rec = {"submitted": stamp, "items_sha256": hashlib.sha256((OUT_DIR / items_name).read_bytes()).hexdigest()[:16], "orders": {}}
    # merge per rung, always: a submit replaces only the rungs it sends (a whole-record write would drop the other
    # rungs' live orders from this folder's record)
    if (OUT_DIR / "submission.json").exists():
        rec["orders"] = json.loads((OUT_DIR / "submission.json").read_text(encoding="utf-8")).get("orders", {})
    for rung, model in RUNGS.items():
        if rung not in only:
            continue
        # SHIPPING is the factory's: the payload writes into the order's own folder ({ORDER_OUT}) and the
        # factory delivers it to deliver_to\<order name> (RUNBOOK-Compute-and-Training-Order-Submission)
        # --seed-base N: a replicate run with fresh seeds (run-to-run variance), in its own --tag folder
        knob = (["--temperature", str(float(TEMPERATURE)), "--seed-base", _arg("--seed-base", "1")]
                if TEMPERATURE is not None else [])
        # --think: let thinking models think. Its own --tag folder, like the temperature knob.
        if "--think" in sys.argv:
            assert TAG, "--think needs its own --tag (its own product folder)"
            knob += ["--think"]
        # --think-level xhigh|medium|low: thinking ON at that reasoning effort (infer_ollama sends the level string;
        # qwen3.8 renders a bare --think as MEDIUM)
        if _arg("--think-level"):
            assert TAG, "--think-level needs its own --tag (its own product folder)"
            assert _arg("--think-level") in ("xhigh", "medium", "low"), _arg("--think-level")
            knob += ["--think-level", _arg("--think-level")]
        # --offload-ok: the model is larger than the card, so partial residency is the model being measured
        if "--offload-ok" in sys.argv:
            knob += ["--offload-ok"]
        # --price-ceiling-s <s>: the order's measured price line (menu_fill computes it per box per family): an answer
        # that costs more is cut and recorded priced_out (infer_ollama --price-ceiling-s); the control is exempt
        if _arg("--price-ceiling-s"):
            assert float(_arg("--price-ceiling-s")) > 0, "--price-ceiling-s must be positive seconds"
            knob += ["--price-ceiling-s", str(float(_arg("--price-ceiling-s")))]
        # --at-cap-brake <share>: an unceiled family's cannot-fit share, applied inside the order (infer_ollama)
        if _arg("--at-cap-brake"):
            assert 0 < float(_arg("--at-cap-brake")) <= 1, "--at-cap-brake must be a share in (0, 1]"
            knob += ["--at-cap-brake", str(float(_arg("--at-cap-brake")))]
        # --strata <plan>: the model is served by Strata, not ollama (infer_ollama --strata starts and stops it); the
        # box's ollama does not list it, so the order is pinned (--only-host) instead of routed by model.<name>
        strata = _arg("--strata")
        if strata:
            assert _arg("--only-host"), "--strata needs --only-host: the desk cannot route a model ollama does not list"
            knob += ["--strata", strata]
        # the order grades its own rows on the box with the tablets' scorer
        knob += ["--grade", SHARE + "\\tabfoundry-jobs\\payloads\\%s" % PAYLOAD.name]
        spec = {"name": "hardbench-fleet%s-%s.%s" % (("-" + TAG) if TAG else "", rung, stamp), "mode": "compute", "load": "gpu",
                "initiator": USER, "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py",
                "args": ["%s\\%s" % (UNC_DIR, items_name), "{ORDER_OUT}\\out.json", "--model", model,
                         "--rounds", str(ROUNDS), "--num-ctx", str(NUM_CTX)] + knob,
                "progress_glob": "{ORDER_OUT}\\out.progress\\*.done", "progress_total": len(items) * ROUNDS,
                "deliver_to": UNC_DIR, "notify": "failures"}
        body = {"spec": spec, "requires": ["order.compute"] + ([] if strata else ["model.%s" % model]), "user": USER}
        if rung in EXCLUDE:
            body["exclude"] = EXCLUDE[rung]
        # --only-host <h>: pin the order to one box when the BOX is what is measured (its own menu row); the desk
        # stamps pinned_to, so an unavailable card makes the order WAIT for that box instead of re-routing
        if _arg("--only-host"):
            assert TAG, "--only-host needs its own --tag (its own product folder)"
            body["only"] = _arg("--only-host").split(",")
        # --priority <n>: the factory's queue order (higher first); a short service order goes ahead of long arms
        if _arg("--priority"):
            spec["priority"] = float(_arg("--priority"))
        (OUT_DIR / ("order_%s.json" % rung)).write_text(json.dumps(body, indent=1), encoding="utf-8")
        out = http("POST", "/p20/order", body, timeout=30)
        print("%s -> %s" % (rung, json.dumps(out)[:300]))
        steps = [s["order"] for s in out.get("steps") or []]
        if not steps and out.get("held") and out.get("order") and len(body.get("only") or []) == 1:
            steps = ["%s_%s" % (out["order"], body["only"][0])]   # held + pinned: the step will be <order>_<host>
        assert len(steps) == 1, "one step per rung expected, the desk returned %r" % steps
        # the delivery path is STATED by the desk's step name (= the order name on the box), never guessed
        rec["orders"][rung] = {"id": out.get("id"), "reply": out, "out": str(OUT_DIR / steps[0] / "out.json")}
    (OUT_DIR / "submission.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")


def watch(budget_s=560, every_s=60):
    rec = json.loads((OUT_DIR / "submission.json").read_text(encoding="utf-8"))
    t_end = time.time() + budget_s
    while True:
        done = {r: bool(MP.delivered_out(v["out"])) for r, v in rec["orders"].items()}
        print("%s delivered=%s" % (time.strftime("%H:%M:%S"), done), flush=True)
        if all(done.values()):
            print("VERDICT: DONE"); return 0
        if time.time() > t_end:
            print("VERDICT: STILL_RUNNING (re-arm)"); return 4
        time.sleep(every_s)


def reduce():
    mod = _payload()
    work = tempfile.mkdtemp()
    rows, energy, on_box = [], {}, {}
    limit = collections.defaultdict(collections.Counter)   # rung -> rows priced out / void at the context limit
    orders =json.loads((OUT_DIR / "submission.json").read_text(encoding="utf-8")).get("orders", {})
    for rung in RUNGS:
        p = pathlib.Path(MP.delivered_out(orders[rung]["out"]) or orders[rung]["out"]) if rung in orders \
            else OUT_DIR / ("out_%s.json" % rung)
        if not p.exists():
            print("missing", p); continue
        d = json.loads(p.read_text(encoding="utf-8"))
        energy[rung] = (d.get("summary") or {}).get("energy") or {}
        for r in d["rows"]:
            task = r["id"]
            status = MP.row_status(r, d.get("num_ctx"))
            if status == "priced_out":                   # cut at the order's price line: a stated cost, never graded
                limit[rung]["priced_out"] += 1
                rows.append({"rung": rung, "host": d.get("host"), "task": task, "round": r["round"], "ok": False,
                             "checks_passed": 0, "checks_total": 0, "served_model": r.get("served_model"),
                             "wall_s": r.get("wall_s"), "gpu_j": r.get("gpu_j"), "status": status,
                             "error": "priced_out"})
                continue
            if status == "void":                         # hit the context limit: it measured the cap ()
                limit[rung]["void"] += 1
            if r.get("ok") is not None:                  # graded on the box by the order itself
                passed, total, err = r.get("checks_passed"), r.get("checks_total"), r.get("grade_error") or ""
                on_box[rung] = on_box.get(rung, 0) + 1
            else:                                        # older orders (no --grade): grade here, as before
                checks = (hard_tasks.TASKS.get(task) or mod.CONTROL_TASKS.get(task))["checks"]
                passed, total, err = mod.score(mod.extract_code(r.get("answer") or ""), checks, work)
            rows.append({"rung": rung, "host": d.get("host"), "task": task, "round": r["round"], "ok": bool(total) and passed == total,
                         "checks_passed": passed, "checks_total": total, "served_model": r.get("served_model"),
                         "wall_s": r.get("wall_s"), "gpu_j": r.get("gpu_j"), "status": status,
                         "error": (r.get("error") or err)[:60]})
    assert rows, "zero rows - the join is broken, not the bench"
    with open(OUT_DIR / "fleet_rows.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["rung"], r["task"])].append(r)
    md = ["# Hard Bench — fleet rungs on the same 10 coding tasks, as FACTORY ORDERS (%d rows)" % len(rows), "",
          ("temperature %s, seed = 1 + round" % TEMPERATURE if TEMPERATURE is not None else "production sampling (no temperature given)")
          + ", num_ctx %d, rounds %d" % (NUM_CTX, ROUNDS), "",
          "graded ON THE BOX by the order (infer_ollama --grade): %s of %d rows; the rest graded here with the same scorer"
          % (on_box or "none", len(rows)), "",
          "| rung | host | task | n | pass_rate | best_of_%d | served | wall_mean_s |" % ROUNDS, "|---|---|---|---|---|---|---|---|"]
    for (rung, task), rs in sorted(by.items()):
        ok = [r["ok"] for r in rs]
        md.append("| %s | %s | %s | %d | %.2f | %s | %s | %.1f |" % (rung, rs[0]["host"], task, len(rs), sum(ok) / len(ok), any(ok),
                                                                     sorted({str(r["served_model"]) for r in rs}), statistics.mean(float(r["wall_s"] or 0) for r in rs)))
    md += ["", "## Per rung across the %d in-band tasks" % len(POOL_INBAND), "", "| rung | per-attempt pass rate | best_of_%d (mean over tasks) | wall_mean_s |" % ROUNDS, "|---|---|---|---|"]
    for rung in RUNGS:
        rs = [r for r in rows if r["rung"] == rung and r["task"] in POOL_INBAND]
        if rs:
            anys = [any(x["ok"] for x in rs if x["task"] == t) for t in POOL_INBAND if any(x["task"] == t for x in rs)]
            md.append("| %s (%s) | %.3f | %.3f | %.1f |" % (rung, RUNGS[rung], sum(r["ok"] for r in rs) / len(rs), sum(anys) / len(anys),
                                                          statistics.mean(float(r["wall_s"] or 0) for r in rs)))
    if any(limit[r] for r in RUNGS):
        md += ["", "## Rows that did not measure the model as asked", "",
               "`priced out` = cut at the order's price line (infer_ollama --price-ceiling-s): a stated COST measurement, counted",
               "as not solved but never graded. `void` = ended at the context limit: it measured the cap, not the model",
               "(doctrine 13a) - re-run with room before believing the cell.", "",
               "| rung | rows | priced out | void (at the context limit) |", "|---|---|---|---|"]
        for rung in RUNGS:
            if limit[rung]:
                md.append("| %s | %d | %d | %d |" % (rung, sum(1 for r in rows if r["rung"] == rung),
                                                     limit[rung]["priced_out"], limit[rung]["void"]))
    md += ["", "## Energy per solved task (GPU board power, metered inside the order)", "",
           "Every attempt's energy counts, solved or not, divided by the attempts that solved the task. `above idle`",
           "subtracts the order's own idle baseline (resident model, nothing in flight) times each attempt's wall time.", "",
           "J per solved counts every attempt, so a few runaway answers dominate it (2026-09-28: one 103-s ramble cost 13,501 J);",
           "the MEDIAN per attempt is the typical cost - read the two together.", "",
           "| rung | meter | idle_w | J per attempt | J per attempt MEDIAN | J per attempt above idle | J per solved task | J per solved above idle | attempts metered |",
           "|---|---|---|---|---|---|---|---|---|"]
    for rung in RUNGS:
        rs = [r for r in rows if r["rung"] == rung and r["task"] in POOL_INBAND]
        e = energy.get(rung) or {}
        metered = [r for r in rs if r["gpu_j"] is not None]
        if not rs:
            continue
        if not metered or len(metered) != len(rs):
            md.append("| %s | %s | %s | UNMETERED (%d of %d attempts carry energy) | | | | | %d/%d |" % (
                rung, e.get("source"), e.get("idle_w"), len(metered), len(rs), len(metered), len(rs)))
            continue
        idle_w = e.get("idle_w")
        tot = sum(r["gpu_j"] for r in metered)
        above = sum(max(0.0, r["gpu_j"] - (idle_w or 0) * float(r["wall_s"] or 0)) for r in metered) if idle_w is not None else None
        solved = sum(r["ok"] for r in metered)
        md.append("| %s (%s) | %s | %s | %.0f | %.0f | %s | %s | %s | %d/%d |" % (
            rung, RUNGS[rung], e.get("source"), idle_w, tot / len(metered), statistics.median(r["gpu_j"] for r in metered),
            "%.0f" % (above / len(metered)) if above is not None else "-",
            "%.0f" % (tot / solved) if solved else "no solves",
            "%.0f" % (above / solved) if solved and above is not None else "-", len(metered), len(rs)))
    md += ["", "Tablet reference (1.5B coder, sampled, same tasks): per-attempt 0.400; best-of-3 0.708; best-of-9 0.917 (../matrix.md).", "Source: fleet_rows.csv."]
    (OUT_DIR / "fleet.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md)); print("wrote", OUT_DIR / "fleet.md")


if __name__ == "__main__":
    {"submit": submit, "watch": lambda: sys.exit(watch()), "reduce": reduce}[sys.argv[1]]()
