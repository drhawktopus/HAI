"""Hard Bench — FLEET rungs for the tool and vlm pools, as factory orders (the coding rungs' pattern).

Question: the 1.5B tool model has no middle band (every task always or never right) and the vision model has a few
"sometimes" questions — does a BIGGER model fix what the tablets miss, and at what energy per solved task?

  tool  : 24 hard_tool_tasks, PREAMBLE prompt, answer constrained by an ollama JSON schema (the lab's structured-output
          rule) whose tool field is the payload's own tool list; graded by map_tool_swarm_hard.score_tool.
  vlm   : 24 hard_vlm_tasks, the rendered image inline (base64) + the same question; graded by the payload's
          _vlm_score on the whole answer.
  rungs : tool 9B (qwen3.5:9b) + 27B (qwen3.6:27b); vlm 8B (qwen3-vl:8b) + 27B (qwen3.6:27b, vision-capable).
  sampling matches the tablets: temperature 0.6, seed = 1 + round, 3 rounds.
Orders go to Starfleet's desk (requires order.compute + model.<name>), never a named host; infer_ollama.py meters
GPU energy per row. submit | watch | reduce.
"""
import base64
import collections
import csv
import datetime
import hashlib
import importlib.util
import json
import pathlib
import statistics
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_price as MP  # noqa: E402  delivered_out: the newest delivered generation of an order's out
import task_versions as TVER  # noqa: E402  which payload generation grades a row
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hard_tool_tasks as HT  # noqa: E402
import hard_vlm_tasks as HV  # noqa: E402
import menu_price as MP  # noqa: E402

import menu_paths as MPATH  # noqa: E402  every folder the menu reads
LS = MPATH.LS                 # the head's address and the share's UNC root are settings (HAI_URL, SSD_SHARED_UNC)

KIRK = LS.head_url()
USER = "ClaudeSupervisor"
OUT_DIR = pathlib.Path(MPATH.CAMPAIGNS) / "hard-bench-20260927" / "fleet-tv"
UNC_DIR = LS.setting("SSD_SHARED_UNC") + r"\tabfoundry-jobs\campaigns\hard-bench-20260927\fleet-tv"
PAY = pathlib.Path(MPATH.PAYLOADS)
IMG_DIR = pathlib.Path(MPATH.VLM_HARD_SET)
RUNGS = {"tool-9b": ("tool", "qwen3.5:9b", 6144), "tool-27b": ("tool", "qwen3.6:27b", 8192),
         "tool-27b-q38": ("tool", "qwen3.8:27b", 8192),                  # S7 model knob,
         "vlm-8b": ("vlm", "qwen3-vl:8b", 4096), "vlm-27b": ("vlm", "qwen3.6:27b", 8192),
         # qwen3.6:27b could not read h_num4827.png at temperature 0 ("47"); qwen3.8:27b could ("4827") -
         "vlm-27b-q38": ("vlm", "qwen3.8:27b", 8192),
         # the valid vision comparison: 2x images (hard_vlm_tasks.SERVE_SCALE) and thinking ON (vision_factor_probe.py)
         "vlm2x-8b": ("vlm", "qwen3-vl:8b", 8192), "vlm2x-27b-q36": ("vlm", "qwen3.6:27b", 8192),
         "vlm2x-27b-q38": ("vlm", "qwen3.8:27b", 8192),
         # the 8B on the 8 GB cards it is sized for; a single-image check needs ~1,700 tokens, so 4096 (not chat's 6144)
         "vlm2x-8b-bh4k": ("vlm", "qwen3-vl:8b", 4096), "vlm2x-8b-ss4k": ("vlm", "qwen3-vl:8b", 4096),
         # menu fill: the rest of what the factories hold, routed by the desk. qwen3:8b at the 8 GB boxes'
         # tested max (RUNBOOK "Fleet VRAM residency map"); Mothership's fit probe has not run, so its models take the
         # 8192 the 27B tool rungs used - reduce() counts every row that hit the context, so a binding cap is visible
         "tool-q3-8b": ("tool", "qwen3:8b", 17408), "tool-q3-14b": ("tool", "qwen3:14b", 8192),
         "tool-coder30b": ("tool", "qwen3-coder:30b", 8192), "tool-36moe": ("tool", "qwen3.6:35b-a3b", 8192),
         "tool-27b38mtp": ("tool", "qwen3.8:27b-mtp-q4_K_M", 8192),
         "tool-vl8b": ("tool", "qwen3-vl:8b", 5120),                    # 5,120 = its tested max on the 8 GB boxes
         # Softserve's own rows (3070 Ti speed/energy, not BigHonker's 3070) - pinned below
         "tool-q3-8b-ss": ("tool", "qwen3:8b", 17408), "tool-9b-ss": ("tool", "qwen3.5:9b", 7168),
         "vlm-8b-ss": ("vlm", "qwen3-vl:8b", 5120),
         # menu fill: qwen3.5:9b takes images (ollama capabilities: vision) and had no vision cell; 7,168 is
         # its tested max on both 8 GB boxes. vlm-* submit with HB_VLM_SCALE=1, vlm2x-* with the default 2.
         "vlm-9b-bh": ("vlm", "qwen3.5:9b", 7168), "vlm-9b-ss": ("vlm", "qwen3.5:9b", 7168),
         "vlm2x-9b-bh": ("vlm", "qwen3.5:9b", 7168), "vlm2x-9b-ss": ("vlm", "qwen3.5:9b", 7168),
         # the vision model on tool work per 8 GB box, and on 1x vision (thinking off) on Mothership
         "tool-vl8b-bh": ("tool", "qwen3-vl:8b", 5120), "tool-vl8b-ss": ("tool", "qwen3-vl:8b", 5120),
         "vlm-8b-ms": ("vlm", "qwen3-vl:8b", 8192)}
THINK_RUNGS = {"vlm2x-8b", "vlm2x-27b-q36", "vlm2x-27b-q38", "vlm2x-8b-bh4k", "vlm2x-8b-ss4k",
               "vlm2x-9b-bh", "vlm2x-9b-ss"}
ONLY = {"vlm2x-8b-bh4k": ["bighonker"], "vlm2x-8b-ss4k": ["softserve"],   # the owner: the medium test on the medium cards
        "tool-q3-8b-ss": ["softserve"], "tool-9b-ss": ["softserve"], "vlm-8b-ss": ["softserve"],
        "vlm-9b-bh": ["bighonker"], "vlm-9b-ss": ["softserve"], "vlm2x-9b-bh": ["bighonker"], "vlm2x-9b-ss": ["softserve"],
        "tool-vl8b-bh": ["bighonker"], "tool-vl8b-ss": ["softserve"], "vlm-8b-ms": ["mothership"]}
# the image scale a vision rung must be SUBMITTED at; submit() refuses a mismatch (the menu reads the scale from the name)
SCALE_OF = lambda rung: 2 if rung.startswith("vlm2x") else 1
# Grid cells (menu_fill.py, via `cell`): every model on every box at every knob, each its own rung, kept here so
# submit, reduce and build_menu all read the same knobs.
EXTRA = OUT_DIR / "rungs_extra.json"
KNOBS = {}                                           # rung -> {"temperature", "seed_base"}
THINK_LEVEL = {}                                     # rung -> reasoning effort sent as the level (infer_ollama --think-level)
EXTRA_OFFLOAD = {}                                   # rung -> True: model larger than the card (infer_ollama --offload-ok)
EXTRA_PRICE = {}                                     # rung -> the order's price line in seconds (infer_ollama --price-ceiling-s)
EXTRA_STRATA = {}                                    # rung -> Strata serving plan (infer_ollama --strata); pinned, no model.*
if EXTRA.exists():
    for _r, _c in json.loads(EXTRA.read_text(encoding="utf-8")).items():
        RUNGS[_r] = (_c["division"], _c["model"], _c["ctx"])
        ONLY[_r] = [_c["host"]]
        if _c.get("think"):
            THINK_RUNGS.add(_r)
        if _c.get("think_level"):
            THINK_LEVEL[_r] = _c["think_level"]
        KNOBS[_r] = {"temperature": _c["temperature"], "seed_base": _c["seed_base"]}
        EXTRA_OFFLOAD[_r] = bool(_c.get("offload_ok"))
        if _c.get("price_ceiling_s"):
            EXTRA_PRICE[_r] = float(_c["price_ceiling_s"])
        if _c.get("strata"):
            EXTRA_STRATA[_r] = _c["strata"]
# The desk has no VRAM field on an order: qwen3-vl:8b at num_ctx 8192 needs ~6.3 GB and an 8 GB card fits ~5.6
#, so the 8 GB factories are excluded for that rung.
# 8192 was never needed: ollama resizes this model's images to a fixed budget (~1,110 prompt tokens at 1x AND 2x).
EXCLUDE = {"vlm2x-8b": ["bighonker", "softserve"]}
ROUNDS = 3
TEMP = 0.6
# prompt_eval_count DOES include the image for the Qwen 27Bs: ~175 at 480x320, ~625 at 2x, ~2,425 at 4x (qwen3-vl:8b
# spends ~1,100 at 480x320). At 480x320 the 27Bs get too few image tokens to read digits - render the pool at 2x.


def _load(name):
    """The grading payload: the generation named when it is here, else its newest generation (a published tree
    carries only the newest; a generation that renames no task grades every row the same, task_versions)."""
    if not (PAY / name).exists():
        name = TVER.newest_payload(name)
    spec = importlib.util.spec_from_file_location(name[:-3], PAY / name)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def items_for(division):
    if division == "tool":
        schema = {"type": "object", "properties": {"tool": {"type": "string", "enum": HT.TOOL_NAMES},
                                                   "arguments": {"type": "object"}},
                  "required": ["tool", "arguments"]}
        return [{"id": t, "prompt": HT.PREAMBLE.format(req=d["req"]), "format": schema} for t, d in HT.TASKS.items()]
    out = []
    for t, (img, q, _gold, _match, _cat) in HV.TASKS.items():
        out.append({"id": t, "prompt": q, "images": [base64.b64encode((IMG_DIR / img).read_bytes()).decode("ascii")]})
    return out


def items_name(division):
    """One name per generation: vision items carry the image scale they were built at."""
    return "items-vlm-%dx.json" % HV.SERVE_SCALE if division == "vlm" else "items-%s.json" % division


def http(method, path, body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(KIRK + path, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def cell():
    """Register ONE grid cell as its own rung and submit it:
    cell --rung R --division tool|vlm --model M --ctx N --host H --temperature T --seed-base S [--think] [--offload-ok]
         [--price-ceiling-s SECONDS]"""
    a = lambda k: sys.argv[sys.argv.index(k) + 1]
    rung, division = a("--rung"), a("--division")
    assert division in ("tool", "vlm"), division
    assert rung not in RUNGS or rung in KNOBS, "%s is a hand-defined rung" % rung
    extra = json.loads(EXTRA.read_text(encoding="utf-8")) if EXTRA.exists() else {}
    extra[rung] = {"division": division, "model": a("--model"), "ctx": int(a("--ctx")), "host": a("--host"),
                   "temperature": float(a("--temperature")), "seed_base": int(a("--seed-base")),
                   "think": "--think" in sys.argv or "--think-level" in sys.argv, "offload_ok": "--offload-ok" in sys.argv}
    if "--strata" in sys.argv:                       # served by Strata (infer_ollama --strata), pinned by --host
        extra[rung]["strata"] = a("--strata")
    if "--think-level" in sys.argv:
        assert a("--think-level") in ("xhigh", "medium", "low"), a("--think-level")
        extra[rung]["think_level"] = a("--think-level")
    if "--price-ceiling-s" in sys.argv:
        assert float(a("--price-ceiling-s")) > 0, "--price-ceiling-s must be positive seconds"
        extra[rung]["price_ceiling_s"] = float(a("--price-ceiling-s"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    EXTRA.write_text(json.dumps(extra, indent=1), encoding="utf-8")
    c = extra[rung]
    RUNGS[rung] = (division, c["model"], c["ctx"])
    ONLY[rung] = [c["host"]]
    if c["think"]:
        THINK_RUNGS.add(rung)
    if c.get("think_level"):
        THINK_LEVEL[rung] = c["think_level"]
    KNOBS[rung] ={"temperature": c["temperature"], "seed_base": c["seed_base"]}
    EXTRA_OFFLOAD[rung] = c["offload_ok"]
    if c.get("price_ceiling_s"):
        EXTRA_PRICE[rung] = c["price_ceiling_s"]
    if c.get("strata"):
        EXTRA_STRATA[rung] = c["strata"]
    submit([rung])


def submit(only=None):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if only is None:
        only = sys.argv[2].split(",") if len(sys.argv) > 2 else list(RUNGS)
    rec = {"submitted": stamp, "orders": {}}
    if (OUT_DIR / "submission.json").exists():        # a partial submit keeps the other rungs' records
        rec["orders"] = json.loads((OUT_DIR / "submission.json").read_text(encoding="utf-8")).get("orders", {})
    for division in ("tool", "vlm"):
        items = items_for(division)
        assert len(items) == 24, (division, len(items))
        p = OUT_DIR / items_name(division)
        p.write_text(json.dumps({"items": items}), encoding="utf-8")
        rec["items_%s_sha256" % division] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    for rung, (division, model, ctx) in RUNGS.items():
        if rung not in only:
            continue
        assert division != "vlm" or SCALE_OF(rung) == HV.SERVE_SCALE, (
            "%s must be submitted at %dx images (HB_VLM_SCALE=%d), not %dx" % (rung, SCALE_OF(rung), SCALE_OF(rung),
                                                                          HV.SERVE_SCALE))
        # SHIPPING is the factory's: the payload writes into the order's own folder ({ORDER_OUT}) and the factory
        # delivers it to deliver_to\<order name> (RUNBOOK-Compute-and-Training-Order-Submission). Each run lands in
        # its own folder, so a re-run never touches an earlier run's products.
        spec = {"name": "hardbench-fleet-%s.%s" % (rung, stamp), "mode": "compute", "load": "gpu", "initiator": USER,
                "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py",
                "args": ["%s\\%s" % (UNC_DIR, items_name(division)), "{ORDER_OUT}\\out.json", "--model", model,
                         "--rounds", str(ROUNDS), "--num-ctx", str(ctx),
                         "--temperature", str(KNOBS.get(rung, {}).get("temperature", TEMP)),
                         "--seed-base", str(KNOBS.get(rung, {}).get("seed_base", 1))]
                        + (["--think-level", THINK_LEVEL[rung]] if rung in THINK_LEVEL
                           else ["--think"] if rung in THINK_RUNGS else [])
                        + (["--offload-ok"] if EXTRA_OFFLOAD.get(rung) else [])
                        + (["--price-ceiling-s", str(EXTRA_PRICE[rung])] if EXTRA_PRICE.get(rung) else [])
                        + (["--strata", EXTRA_STRATA[rung]] if EXTRA_STRATA.get(rung) else []),
                "progress_glob": "{ORDER_OUT}\\out.progress\\*.done", "progress_total": 24 * ROUNDS,
                "deliver_to": UNC_DIR, "notify": "failures"}
        body = {"spec": spec, "user": USER,
                "requires": ["order.compute"] + ([] if EXTRA_STRATA.get(rung) else ["model.%s" % model])}
        assert not EXTRA_STRATA.get(rung) or rung in ONLY, "a Strata rung must be pinned: ollama does not list it"
        if rung in EXCLUDE:
            body["exclude"] = EXCLUDE[rung]
        if rung in ONLY:
            body["only"] = ONLY[rung]
        (OUT_DIR / ("order_%s.%s.json" % (rung, stamp))).write_text(json.dumps(body, indent=1), encoding="utf-8")
        out = http("POST", "/p20/order", body, timeout=30)
        print("%-14s -> %s" % (rung, json.dumps(out)[:300]))
        steps = [s["order"] for s in out.get("steps") or []]
        if not steps and out.get("held") and out.get("order") and len(body.get("only") or []) == 1:
            steps = ["%s_%s" % (out["order"], body["only"][0])]   # held + pinned: the step will be <order>_<host>
        assert len(steps) == 1, "one step per rung expected, the desk returned %r" % steps
        # the delivery path is STATED by the desk's step name (= the order name on the box), never guessed
        rec["orders"][rung] = {"id": out.get("id"), "reply": out, "out": str(OUT_DIR / steps[0] / "out.json"),
                               "model": model, "num_ctx": ctx, "host": (ONLY.get(rung) or [None])[0],
                               "temperature": KNOBS.get(rung, {}).get("temperature", TEMP),
                               "seed_base": KNOBS.get(rung, {}).get("seed_base", 1),
                               "think": rung in THINK_RUNGS, "think_level": THINK_LEVEL.get(rung), "scale": SCALE_OF(rung) if division == "vlm" else None}
    (OUT_DIR / ("submission-%s.json" % stamp)).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    (OUT_DIR / "submission.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")


def watch(budget_s=3000, every_s=60):
    rec = json.loads((OUT_DIR / "submission.json").read_text(encoding="utf-8"))
    t_end = time.time() + budget_s
    while True:
        done = {r: bool(MP.delivered_out(v["out"])) for r, v in rec["orders"].items()}
        if all(done.values()):
            print("VERDICT: DONE", done); return 0
        if time.time() > t_end:
            print("VERDICT: STILL_RUNNING", done); return 4
        time.sleep(every_s)


def reduce():
    tool_pay, vlm_pay = _load("map_tool_swarm_hard.py"), _load("map_vlm_swarm_hard.py")
    rows = []
    energy = {}
    capped = collections.Counter()                  # rows that ended at the context limit: void ()
    priced = collections.Counter()                  # rows cut at the order's price line: a stated cost measurement
    sub = json.loads((OUT_DIR / "submission.json").read_text(encoding="utf-8")).get("orders", {})
    for rung, (division, model, ctx) in RUNGS.items():
        # the desk-stated delivery path; earlier runs wrote OUT_DIR\out_<rung>.json
        p = pathlib.Path(MP.delivered_out(sub[rung]["out"]) or sub[rung]["out"]) if rung in sub \
            else OUT_DIR / ("out_%s.json" % rung)
        if not p.exists():
            p = OUT_DIR / ("out_%s.json" % rung)
        if not p.exists():
            print("missing", rung, p); continue
        d = json.loads(p.read_text(encoding="utf-8"))
        energy[rung] = (d.get("summary") or {}).get("energy") or {}
        for r in d["rows"]:
            # one classifier (menu_price.row_status): priced_out is a stated cost measurement and is never graded;
            # void = a row that hit the context limit measured the limit, not the model
            status = MP.row_status(r, ctx)
            if status == "void":
                capped[rung] += 1
            elif status == "priced_out":
                priced[rung] += 1
            ans = r.get("answer") or ""
            field = "content"
            if not ans.strip() and (r.get("thinking") or "").strip():
                ans, field = r["thinking"], "thinking"     # a thinking model can file its answer there
            if status == "priced_out":
                ok, err = False, "priced_out"              # the partial text of a cut answer is never graded
            elif division == "tool":
                t = HT.TASKS[r["id"]]
                passed, total, err = tool_pay.score_tool(ans, t["gold_tool"], t["gold_args"])
                ok = total > 0 and passed == total
            else:
                _img, _q, gold, match, _cat = HV.TASKS[r["id"]]
                ok = bool(ans.strip()) and vlm_pay._vlm_score(ans, gold, match)
                err = "" if ok else "wrong"
            rows.append({"rung": rung, "division": division, "model": r.get("served_model") or model, "host": d.get("host"),
                         "temperature": KNOBS.get(rung, {}).get("temperature", TEMP),
                         "think": THINK_LEVEL.get(rung) or ("on" if rung in THINK_RUNGS else "off"),
                         "seed_base": KNOBS.get(rung, {}).get("seed_base", 1),
                         "task": r["id"], "round": r["round"], "ok": bool(ok), "answer_field": field,
                         "answer": ans[:200], "wall_s": r.get("wall_s"), "gpu_j": r.get("gpu_j"),
                         "status": status, "error": (r.get("error") or err or "")[:80],
                         "t0": r.get("t0"), "t1": r.get("t1")})     # the answer's window: menu_wall prices it at the wall
    assert rows, "zero rows - the join is broken"
    with open(OUT_DIR / "fleet_tv_rows.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
    md = ["# Hard Bench — fleet rungs on the tool and vlm pools (%d rows)" % len(rows), "",
          "temperature %.1f, seed = 1 + round, %d rounds; graded with the tablet payloads' own scorers" % (TEMP, ROUNDS), "",
          "`rows at a limit` = VOID (ended at the context limit: it measured the cap). `priced out` = cut at the order's price "
          "line - a stated cost measurement, counted as not right but never graded.", "",
          "| rung | model | host | per-attempt right | tasks always right | never right | sometimes | J per solved | meter | rows at a limit | priced out |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for rung in RUNGS:
        rs = [r for r in rows if r["rung"] == rung]
        if not rs:
            continue
        by = collections.defaultdict(list)
        for r in rs:
            by[r["task"]].append(r["ok"])
        always = sum(all(v) for v in by.values())
        never = sum(not any(v) for v in by.values())
        solved = sum(r["ok"] for r in rs)
        js = [r["gpu_j"] for r in rs if r["gpu_j"] is not None]
        jps = ("%.0f" % (sum(js) / solved)) if solved and len(js) == len(rs) else "-"
        md.append("| %s | %s | %s | %.2f | %d | %d | %d | %s | %s | %d | %d |" % (
            rung, rs[0]["model"], rs[0]["host"], solved / len(rs), always, never, len(by) - always - never, jps,
            energy.get(rung, {}).get("source"), capped[rung], priced[rung]))
    md += ["", "Tablet reference (same pools, sampled 0.6): tool 1.5B — 18 tasks always right, 6 never; vlm SmolVLM2-500M — "
               "see calibration-vlm.md (1x images), calibration-vlm-2x.md (2x).", "Source: fleet_tv_rows.csv."]
    (OUT_DIR / "fleet_tv.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    {"submit": submit, "cell": cell, "watch": lambda: sys.exit(watch()), "reduce": reduce}[sys.argv[1]]()
