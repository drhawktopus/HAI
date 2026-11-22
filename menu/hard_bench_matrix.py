"""Hard Bench — coding ensemble MATRIX: submit, watch, reduce.

Design:
  tasks = 8 in-band (pass rate 0.1-0.7 over 10 calibration seeds) + 2 anchors (always-pass, never-pass)
  N = {1, 3, 5, 9} repeats = 3 per (task, N), each repeat a FRESH seed block (seed = replicate + 1)
  strategies are SELECTORS applied at reduce time to the SAME rows (Phase 5 ran three identical physical arms
  per cell; here one arm per (task, N, repeat) feeds all three):
      independent_swarm = mean(ok) (per-tablet accuracy)
      best_of_n = any(ok) (real verifier: the hidden checks)
      majority_vote = verdict majority (ok votes > N/2) and canonical-answer majority (winner's ok)
  arms = 10 x 4 x 3 = 120; tablet runs = 10 x (1+3+5+9) x 3 = 540; windows pack arms to <= 10 items.
  seeds start at replicate 10 so no matrix seed repeats a calibration seed.
Same payload (map_swarm_code_hard.py, sha pinned), same control per window, same inbox front door."""
import collections
import csv
import datetime
import json
import os
import pathlib
import re
import statistics
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hard_tasks  # noqa: E402
import hard_bench_calibrate as cal  # noqa: E402  (sha, INBOX, OUTBOX, PAYLOAD..., watch helpers)
import menu_paths as MPATH  # noqa: E402

import task_versions as TVER  # noqa: E402  a calibrated task keeps its place under its current (versioned) id
IN_BAND = [TVER.current(t) for t in ("caesar", "valid_ipv4", "longest_common_prefix", "top_k_words",
                                     "flatten", "balanced_brackets", "eval_rpn", "rle_decode")]
ANCHORS = {"rotate_matrix": "always_pass", "group_anagrams": "never_pass"}
TASKS = IN_BAND + list(ANCHORS)
# --pool f2: the second coding family's band from its own calibration (hardbench-codef2-cal.20260928-115852), same
# 0.1-0.7 rule the owner set for family 1: 0.6 / 0.4 and four at 0.2; anchors = one always-pass, one never-pass.
if "--pool" in sys.argv and sys.argv[sys.argv.index("--pool") + 1] == "f2":
    IN_BAND = ["dbg_count_evens", "dbg_is_palindrome", "dbg_append_to_all", "dbg_running_total",
               "mod_greet", "mod_keep_positive"]
    ANCHORS = {"dbg_word_freq": "always_pass", "dbg_dedupe_sorted": "never_pass"}
    TASKS = IN_BAND + list(ANCHORS)
NS = (1, 3, 5, 9)
REPEATS = 3
SEED_BASE = 10          # calibration used replicates 0-9
WINDOW_CAP = 10


def compose(run_id, model=cal.MODEL, payload=cal.PAYLOAD):
    arms = []
    for t in TASKS:
        rep = SEED_BASE
        for n in NS:
            for r in range(1, REPEATS + 1):
                items = ["arm=%s;task=%s;replicate=%d;seed=%d;model=%s" % (cal.ARM, t, k, k + 1, model)
                         for k in range(rep, rep + n)]
                arms.append({"name": "%s.arm.%s.N%02d.r%d" % (run_id, t, n, r), "payload": "%s/%s" % (cal.PAYLOAD_ROOT, payload),
                             "batch": 1, "distinct_workers": n, "items": items,
                             "control": {"item": "arm=control;task=control_add;replicate=0;seed=1;model=%s" % model,
                                         "expect": "CODE_CONTROL_PASS"},
                             "hardbench": {"stage": "matrix", "division": "coding", "task": t, "n": n, "repeat": r,
                                           "replicate_start": rep, "role": ANCHORS.get(t, "in_band")}})
                rep += n
    # first-fit-decreasing into windows of <= WINDOW_CAP items; a window's distinct_workers = its item count
    windows = []
    for a in sorted(arms, key=lambda a: -len(a["items"])):
        for w in windows:
            if w["_items"] + len(a["items"]) <= WINDOW_CAP:
                w["arms"].append(a); w["_items"] += len(a["items"]); break
        else:
            windows.append({"arms": [a], "_items": len(a["items"])})
    specs = []
    for i, w in enumerate(windows, 1):
        name = "%s.win.%02d" % (run_id, i)
        specs.append({"kind": "map.window", "name": name, "user": "test", "require_arch": "aarch64", "arms": w["arms"]})
    return arms, specs


def submit(model=cal.MODEL, payload=cal.PAYLOAD, tag=""):
    """tag/model/payload: size-ladder rungs (e.g. --tag -05b --model Qwen2.5-Coder-0.5B-Instruct-Q4_K_M
    --payload map_swarm_code_hard_05b.py). The payload's item contract refuses a mismatched model, so a wrong
    pairing fails loud in the first window rather than silently measuring the wrong rung."""
    assert (cal.LIVE_ROOT / payload).exists(), "payload not on the share: %s" % payload
    ptxt = (cal.LIVE_ROOT / payload).read_text(encoding="utf-8")
    assert 'MODEL_NAME_FIXED = "%s"' % model in ptxt, "payload %s is not pinned to %s" % (payload, model)
    run_id = "hardbench-code-matrix%s.%s" % (tag, time.strftime("%Y%m%d-%H%M%S"))
    arms, specs = compose(run_id, model, payload)
    n_items = sum(len(a["items"]) for a in arms)
    assert len(arms) == len(TASKS) * len(NS) * REPEATS and n_items == len(TASKS) * sum(NS) * REPEATS
    assert all(sum(len(a["items"]) for a in s["arms"]) <= WINDOW_CAP for s in specs)
    intent = {"schema": "hardbench-matrix-v1", "run_id": run_id, "division": "coding", "tasks": TASKS, "anchors": ANCHORS,
              "model": model, "payload": payload,
              "ns": NS, "repeats": REPEATS, "seed_base": SEED_BASE, "arms": len(arms), "items": n_items,
              "payload_sha256": {payload: cal.sha(cal.LIVE_ROOT / payload)},
              "tolkien_source_sha256": {n: cal.sha(cal.TOLKIEN_ROOT / n) for n in ("tablet_foundry.py", "tablet_fulfil.py", "tablet_runner.py")},
              "windows": specs}
    cal.MANIFESTS.mkdir(exist_ok=True)
    manifest = cal.MANIFESTS / ("intent-%s.json" % run_id)
    assert not manifest.exists()
    dests = [cal.INBOX / (s["name"] + ".tjob.json") for s in specs]
    assert not any(d.exists() for d in dests), "order collision"
    manifest.write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
    for s, dst in zip(specs, dests):
        tmp = cal.INBOX / ("." + dst.name + ".tmp")
        tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
        os.replace(tmp, dst)
    print("submitted run_id=%s windows=%d arms=%d items=%d manifest=%s" % (run_id, len(specs), len(arms), n_items, manifest.name))


def watch(manifest, budget_s=560, every_s=60):
    """Bounded to the supervising shell's cap: exits DONE / HELD / STILL_RUNNING (re-arm) / never on a clock as truth."""
    intent = json.loads(pathlib.Path(manifest).read_text(encoding="utf-8"))
    names = [w["name"] for w in intent["windows"]]
    t_end = time.time() + budget_s
    last = -1
    while True:
        done = [n for n in names if cal._results_for(n)]
        if len(done) != last:
            print("%s results %d/%d" % (time.strftime("%H:%M:%S"), len(done), len(names)), flush=True)
            last = len(done)
        if len(done) == len(names):
            print("VERDICT: DONE"); return 0
        held = [h for h in (cal._held_names() or []) if intent["run_id"] in h]
        if held:
            print("VERDICT: HELD %s" % held); return 2
        if time.time() > t_end:
            print("VERDICT: STILL_RUNNING %d/%d (re-arm)" % (len(done), len(names))); return 4
        time.sleep(every_s)


_WS = re.compile(r"\s+")


def canon(code):
    """Canonical form of a code answer for the answer-majority: strip comments/blank lines/whitespace runs."""
    lines = [l.split("#", 1)[0].rstrip() for l in (code or "").splitlines()]
    return _WS.sub(" ", "\n".join(l for l in lines if l.strip())).strip()


def reduce(manifest):
    intent = json.loads(pathlib.Path(manifest).read_text(encoding="utf-8"))
    out_dir = pathlib.Path(MPATH.CAMPAIGNS) / ("hard-bench-%s" % intent["run_id"].split(".")[1][:8])
    out_dir.mkdir(parents=True, exist_ok=True)
    # one product name per rung: the run id's tag (-05b, -3b; none for the 1.5B) names the files, and a product
    # holding another run is never overwritten (the 0.5B reduce once replaced the 1.5B matrix.md)
    sfx = intent["run_id"].split(".")[0][len("hardbench-code-matrix"):]
    md_path, cells_path = out_dir / ("matrix%s.md" % sfx), out_dir / ("matrix_cells%s.csv" % sfx)
    if md_path.exists():
        head = md_path.read_text(encoding="utf-8").splitlines()[0]
        assert head.endswith("(%s)" % intent["run_id"]), "%s holds another run: %s" % (md_path.name, head)
    cells, missing = [], []
    for w in intent["windows"]:
        files = cal._results_for(w["name"])
        loaded = [json.loads(f.read_text(encoding="utf-8")) for f in files]
        for spec in w["arms"]:
            chosen = None
            for d in loaded:
                a = next((x for x in d["arms"] if x.get("name") == spec["name"]), None)
                if a and a.get("usable") and a.get("control_passed") and a.get("complete"):
                    chosen = a
            hb = spec["hardbench"]
            if not chosen:
                missing.append(spec["name"]); continue
            rows = [r for r in chosen["rows"] if r.get("arm") != "control"]
            oks = [bool(r.get("ok")) for r in rows]
            n = len(oks)
            groups = collections.defaultdict(list)
            for r in rows:
                groups[canon(r.get("answer"))].append(bool(r.get("ok")))
            ranked = sorted(groups.values(), key=len, reverse=True)
            tie = len(ranked) > 1 and len(ranked[0]) == len(ranked[1])
            cells.append({"task": hb["task"], "role": hb["role"], "n": hb["n"], "repeat": hb["repeat"], "tablets": n,
                          "swarm_mean": round(sum(oks) / n, 3) if n else None,
                          "best_of_n": any(oks),
                          "majority_verdict": sum(oks) * 2 > n,
                          "majority_answer": (not tie) and bool(ranked[0][0]) if ranked else False,
                          "majority_tie": tie,
                          "wall_max_s": round(max(float(r.get("wall_s") or 0) for r in rows), 1) if rows else None,
                          "hosts": ",".join(sorted({r.get("_host", "") for r in rows}))})
    print("cells:", len(cells), "of", intent["arms"], "| missing/unusable arms:", len(missing))
    assert cells, "zero cells reduced - the join is broken, not the bench"
    with open(cells_path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(cells[0])); wr.writeheader(); wr.writerows(cells)
    # per (task, N): mean over repeats + spread; per N across in-band tasks
    md = ["# Hard Bench — coding ensemble matrix (%s)" % intent["run_id"], "",
          "cells %d of %d; missing/unusable %s; strategies are selectors over the same rows" % (len(cells), intent["arms"], missing), "",
          "## Per task x N (mean over %d repeats; spread = min..max of per-repeat values)" % intent["repeats"], "",
          "| task | role | N | swarm_mean | best_of_n | majority_verdict | majority_answer | ties | wall_max_s |", "|---|---|---|---|---|---|---|---|---|"]
    by = collections.defaultdict(list)
    for c in cells:
        by[(c["task"], c["role"], c["n"])].append(c)
    def m(cs, k):
        v = [float(c[k]) for c in cs]
        return "%.2f (%.2f..%.2f)" % (statistics.mean(v), min(v), max(v))
    for (t, role, n), cs in sorted(by.items(), key=lambda kv: (kv[0][1] != "in_band", kv[0][0], kv[0][2])):
        md.append("| %s | %s | %d | %s | %s | %s | %s | %d | %.1f |" % (
            t, role, n, m(cs, "swarm_mean"), m(cs, "best_of_n"), m(cs, "majority_verdict"), m(cs, "majority_answer"),
            sum(c["majority_tie"] for c in cs), statistics.mean(c["wall_max_s"] for c in cs)))
    md += ["", "## Per N across the %d in-band tasks (mean of task means)" % sum(1 for t in intent["tasks"] if t not in intent["anchors"]), "",
           "| N | swarm_mean | best_of_n | majority_verdict | majority_answer | wall_max_s |", "|---|---|---|---|---|---|"]
    for n in intent["ns"]:
        cs = [c for c in cells if c["role"] == "in_band" and c["n"] == n]
        if cs:
            md.append("| %d | %.3f | %.3f | %.3f | %.3f | %.1f |" % (
                n, statistics.mean(c["swarm_mean"] for c in cs), statistics.mean(c["best_of_n"] for c in cs),
                statistics.mean(c["majority_verdict"] for c in cs), statistics.mean(c["majority_answer"] for c in cs),
                statistics.mean(c["wall_max_s"] for c in cs)))
    md += ["", "Anchors: %s were labelled always/never-pass from calibration seeds; their 54 matrix samples each give the"
           % " / ".join("%s (%s)" % (t, r) for t, r in ANCHORS.items()),
           "true per-tablet rate (reported above). Their N=1 rows must still agree with calibration; a drift of the pooled rate is the calibration's small n, not the grader.",
           "Source: %s (one row per arm: task, N, repeat, selector outcomes, hosts)." % cells_path.name]
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md)); print("wrote", out_dir)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "submit":
        argv = sys.argv[2:]
        opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
        submit(model=opt("--model", cal.MODEL), payload=opt("--payload", cal.PAYLOAD), tag=opt("--tag", ""))
    elif cmd == "watch":
        sys.exit(watch(sys.argv[2]))
    elif cmd == "reduce":
        reduce(sys.argv[2])
    elif cmd == "plan":
        arms, specs = compose("plan")
        print("arms", len(arms), "items", sum(len(a["items"]) for a in arms), "windows", len(specs),
              "sizes", collections.Counter(sum(len(a["items"]) for a in s["arms"]) for s in specs))
