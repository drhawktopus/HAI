"""Hard Bench — calibration order per division (--lane coding | tool | vlm; default coding): submit, watch, reduce.

submit   compose 21 tasks x 5 replicates (seed = replicate + 1) = 105 single-tablet runs, shuffled
         deterministically, dealt as map.window orders of up to 10 items with distinct_workers = items (one
         tablet per run, whole Fellowship in parallel); payload map_swarm_code_hard.py (sha pinned in the
         manifest); control_add per window. Writes manifests/intent-<run_id>.json, then the inbox (atomic).
watch    status-based: exits DONE when every window has a result file, HELD when Tolkien has parked one,
         INCONCLUSIVE at the deadline. Never on a clock alone.
reduce   per-task pass rate over the 5 seeds (per-ARM usability, control-green only), hosts, wall; keeps
         the 0.3-0.7 band; asserts the row count against the manifest (an instrument that measured nothing
         must not read as a clean bench). Products -> tabfoundry-jobs\\campaigns\\hard-bench-<date>\\.
"""
import collections
import csv
import datetime
import hashlib
import json
import os
import pathlib
import random
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hard_tasks  # noqa: E402
import hard_tool_tasks  # noqa: E402
import hard_vlm_tasks  # noqa: E402

import menu_paths as MPATH  # noqa: E402  every folder, mirror and box name the menu reads
import task_versions as TVER  # noqa: E402  which payload generation a new order runs
INBOX = pathlib.Path(MPATH.INBOX)
OUTBOX = pathlib.Path(MPATH.OUTBOX)
LIVE_ROOT = pathlib.Path(MPATH.PAYLOADS)
TOLKIEN_ROOT = pathlib.Path(MPATH.TOLKIEN_MIRROR)
MANIFESTS = HERE / "manifests"
PAYLOAD_ROOT = MPATH.TABJOBS_SEEN + "/payloads"      # as Tolkien sees the share
PAYLOAD = "map_swarm_code_hard.py"
MODEL = "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M"
ARM = "code-hard"
REPLICATES = 5
# 8, not 10: with 10 healthy nodes a 10-distinct window has no slack, and Tolkien withholds a node that is still
# finishing an earlier item - one straggler voided hardbench-vlm-cal.20260927-193946.win.02.
WINDOW_ITEMS = 8
BAND = (0.3, 0.7)

# One row per division: its task pool, payload, champion model (spec: Coder-1.5B coding, Qwen2.5-1.5B tool,
# SmolVLM2-500M vlm), item syntax, control cell and the "kind" column the reduce reports.
LANES = {
    "coding": {"run": "hardbench-code-cal", "tasks": hard_tasks.TASKS, "payload": PAYLOAD, "model": MODEL,
               "item": lambda t, r: "arm=%s;task=%s;replicate=%d;seed=%d;model=%s" % (ARM, t, r, r + 1, MODEL),
               "control": "arm=control;task=control_add;replicate=0;seed=1;model=%s" % MODEL,
               "expect": "CODE_CONTROL_PASS", "kind": lambda t: hard_tasks.TASKS[t]["kind"]},
    "tool": {"run": "hardbench-tool-cal", "tasks": hard_tool_tasks.TASKS, "payload": "map_tool_swarm_hard.py",
             "model": "Qwen2.5-1.5B-Instruct-Q4_K_M",
             "item": lambda t, r: "arm=tool-hard;task=%s;replicate=%d;seed=%d;model=Qwen2.5-1.5B-Instruct-Q4_K_M" % (t, r, r + 1),
             "control": "arm=control;task=control_tool;replicate=0;seed=1;model=Qwen2.5-1.5B-Instruct-Q4_K_M",
             "expect": "TOOL_CONTROL_PASS", "kind": lambda t: hard_tool_tasks.TASKS[t]["gold_tool"]},
    "vlm": {"run": "hardbench-vlm-cal", "tasks": hard_vlm_tasks.TASKS, "payload": "map_vlm_swarm_hard.py",
            "model": "SmolVLM2-500M-Video-Instruct-Q8_0",
            "item": lambda t, r: "vlm-hard~SmolVLM2-500M-Video-Instruct-Q8_0~vlm~%s~%d~%d" % (t, r, r + 1),
            "control": "control~SmolVLM2-500M-Video-Instruct-Q8_0~vlm~control_vlm~0~1",
            "expect": "VLM_CONTROL_PASS", "kind": lambda t: hard_vlm_tasks.TASKS[t][4]},
}
# Family 1 on the Coder-0.5B: the payload the 0.5B matrix already ran (map_swarm_code_hard_05b.py, pinned to that
# model), as single-tablet tries - the S1 cell the menu is missing.
_M05 = "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M"
LANES["coding-05b"] = dict(LANES["coding"], run="hardbench-code-05b-cal", payload="map_swarm_code_hard_05b.py",
                           model=_M05,
                           item=lambda t, r: "arm=%s;task=%s;replicate=%d;seed=%d;model=%s" % (ARM, t, r, r + 1, _M05),
                           control="arm=control;task=control_add;replicate=0;seed=1;model=%s" % _M05)
# Family 1 on the Qwen3.5 tablet models, thinking OFF (gen_hard_payload.py --model <m> --suffix _<tag> --reasoning off):
# the S1 cells the menu is missing. Thinking ON waits for a tested context + a silence-based cutoff.
def _f1_model_lane(tag, model):
    return dict(LANES["coding"], run="hardbench-code-%s-cal" % tag, payload="map_swarm_code_hard_%s.py" % tag,
                model=model,
                item=lambda t, r: "arm=%s;task=%s;replicate=%d;seed=%d;model=%s" % (ARM, t, r, r + 1, model),
                control="arm=control;task=control_add;replicate=0;seed=1;model=%s" % model)


LANES["coding-q352bnt"] = _f1_model_lane("q352bnt", "Qwen3.5-2B-Q4_K_M")
LANES["coding-q3508bnt"] = _f1_model_lane("q3508bnt", "Qwen3.5-0.8B-Q4_K_M")


# The tool pool on other tablet models (gen_tool_hard_payload.py --model <m> --suffix _<tag> [--reasoning off]).
def _tool_model_lane(tag, model):
    return dict(LANES["tool"], run="hardbench-tool-%s-cal" % tag, payload="map_tool_swarm_hard_%s.py" % tag,
                model=model,
                item=lambda t, r: "arm=tool-hard;task=%s;replicate=%d;seed=%d;model=%s" % (t, r, r + 1, model),
                control="arm=control;task=control_tool;replicate=0;seed=1;model=%s" % model)


LANES["tool-c05b"] = _tool_model_lane("c05b", "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M")
LANES["tool-c15b"] = _tool_model_lane("c15b", "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M")
LANES["tool-q3508bnt"] = _tool_model_lane("q3508bnt", "Qwen3.5-0.8B-Q4_K_M")
LANES["tool-q352bnt"] = _tool_model_lane("q352bnt", "Qwen3.5-2B-Q4_K_M")
# Same cells served at 4x (HB_VLM_SCALE=4 through hard_vlm_tasks.py + gen_vlm_hard_payload.py); only the payload differs.
LANES["vlm4x"] = dict(LANES["vlm"], run="hardbench-vlm4x-cal", payload="map_vlm_swarm_hard_4x.py")
# Second coding task family (debug + modify existing code): same payload contract, its own pool and payload file.
try:
    import hard_tasks_f2  # noqa: E402
    LANES["coding-f2"] = dict(LANES["coding"], run="hardbench-codef2-cal", tasks=hard_tasks_f2.TASKS,
                              payload="map_swarm_code_hard_f2.py", kind=lambda t: hard_tasks_f2.TASKS[t]["kind"])
    def _f2_model_lane(tag, model):
        """The family-2 pool on another tablet model; its payload (map_swarm_code_hard_f2_<tag>.py, from
        gen_hard_payload.py --pool f2 --model <model>) refuses any other model name in an item."""
        return dict(LANES["coding-f2"], run="hardbench-codef2-%s-cal" % tag,
                    payload="map_swarm_code_hard_f2_%s.py" % tag, model=model,
                    item=lambda t, r: "arm=%s;task=%s;replicate=%d;seed=%d;model=%s" % (ARM, t, r, r + 1, model),
                    control="arm=control;task=control_add;replicate=0;seed=1;model=%s" % model)
    LANES["coding-f2-05b"] = _f2_model_lane("05b", "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M")
    LANES["coding-f2-q352b"] = _f2_model_lane("q352b", "Qwen3.5-2B-Q4_K_M")
    LANES["coding-f2-q3508b"] = _f2_model_lane("q3508b", "Qwen3.5-0.8B-Q4_K_M")
    # the same models with thinking OFF (gen_hard_payload --reasoning off); thinking ON is the overnight row
    LANES["coding-f2-q352bnt"] = _f2_model_lane("q352bnt", "Qwen3.5-2B-Q4_K_M")
    LANES["coding-f2-q3508bnt"] = _f2_model_lane("q3508bnt", "Qwen3.5-0.8B-Q4_K_M")
    # thinking ON, the "low and slow" overnight row, re-made ctx 16384 (0.8B/2B cache 12 KiB/token, read
    # from the GGUF metadata) and generation ended only when llama-cli's CPU time stops (--idle-cutoff), replacing
    # the 2048 context + flat 400 s timeout of windows
    LANES["coding-f2-q352bt16k"] = _f2_model_lane("q352bt16k", "Qwen3.5-2B-Q4_K_M")
    LANES["coding-f2-q3508bt16k"] = _f2_model_lane("q3508bt16k", "Qwen3.5-0.8B-Q4_K_M")
except ImportError:
    pass
# Work family W1 (hard_tasks_w1.py, charter step 2) on the four tablet models, thinking off. Its answers carry a method
# and an ANSWER block, longer than f2's function-only replies, so every W1 payload is made at the f2 overnight lanes'
# measured setting: ctx 16384 and generation ended only when llama-cli stops working (gen_hard_payload.py --pool w1
# --model <m> --suffix _w1_<tag> --ctx 16384 --idle-cutoff [--reasoning off]).
try:
    import hard_tasks_w1  # noqa: E402

    def _w1_model_lane(tag, model):
        return dict(LANES["coding"], run="hardbench-w1-%s-cal" % tag, tasks=hard_tasks_w1.TASKS,
                    payload="map_swarm_code_hard_w1_%s.py" % tag, model=model,
                    kind=lambda t: hard_tasks_w1.TASKS[t]["kind"],
                    item=lambda t, r: "arm=%s;task=%s;replicate=%d;seed=%d;model=%s" % (ARM, t, r, r + 1, model),
                    control="arm=control;task=control_add;replicate=0;seed=1;model=%s" % model)
    LANES["w1-c15b"] = _w1_model_lane("c15b", "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M")
    LANES["w1-c05b"] = _w1_model_lane("c05b", "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M")
    LANES["w1-q352bnt"] = _w1_model_lane("q352bnt", "Qwen3.5-2B-Q4_K_M")
    LANES["w1-q3508bnt"] = _w1_model_lane("q3508bnt", "Qwen3.5-0.8B-Q4_K_M")
except ImportError:
    pass
# C1, the chat/generalist family (charter step 2): the same four tablet models at W1's measured setting
try:
    import hard_tasks_c1  # noqa: E402

    def _c1_model_lane(tag, model):
        return dict(LANES["coding"], run="hardbench-c1-%s-cal" % tag, tasks=hard_tasks_c1.TASKS,
                    payload="map_swarm_code_hard_c1_%s.py" % tag, model=model,
                    kind=lambda t: hard_tasks_c1.TASKS[t]["kind"],
                    item=lambda t, r: "arm=%s;task=%s;replicate=%d;seed=%d;model=%s" % (ARM, t, r, r + 1, model),
                    control="arm=control;task=control_add;replicate=0;seed=1;model=%s" % model)
    LANES["c1-c15b"] = _c1_model_lane("c15b", "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M")
    LANES["c1-c05b"] = _c1_model_lane("c05b", "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M")
    LANES["c1-q352bnt"] = _c1_model_lane("q352bnt", "Qwen3.5-2B-Q4_K_M")
    LANES["c1-q3508bnt"] = _c1_model_lane("q3508bnt", "Qwen3.5-0.8B-Q4_K_M")
except ImportError:
    pass


# CHAT1, the small tier answering a one-line chat turn:
# the same four tablet models at C1's setting, on HAI's own simple-chat requests (hard_tasks_chat1.py)
try:
    import hard_tasks_chat1  # noqa: E402

    def _chat1_model_lane(tag, model):
        return dict(LANES["coding"], run="hardbench-chat1-%s" % tag, tasks=hard_tasks_chat1.TASKS,
                    payload="map_swarm_code_hard_chat1_%s.py" % tag, model=model,
                    kind=lambda t: hard_tasks_chat1.TASKS[t]["kind"],
                    item=lambda t, r: "arm=%s;task=%s;replicate=%d;seed=%d;model=%s" % (ARM, t, r, r + 1, model),
                    control="arm=control;task=control_add;replicate=0;seed=1;model=%s" % model)
    LANES["chat1-c15b"] = _chat1_model_lane("c15b", "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M")
    LANES["chat1-c05b"] = _chat1_model_lane("c05b", "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M")
    LANES["chat1-q352bnt"] = _chat1_model_lane("q352bnt", "Qwen3.5-2B-Q4_K_M")
    LANES["chat1-q3508bnt"] = _chat1_model_lane("q3508bnt", "Qwen3.5-0.8B-Q4_K_M")
except ImportError:
    pass


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def submit(tasks=None, rep_start=0, tag="", lane="coding", window_items=None, payload=None, pull=False):
    """tasks: subset of hard_tasks.TASKS (default all); rep_start: first replicate index, so a second pass
    on the same tasks uses NEW seeds (seed = replicate + 1) instead of repeating pass 1's. payload: a knob variant
    of the lane's payload (e.g. map_swarm_code_hard_t03.py for temperature 0.3); default: the newest generation of the
    lane's payload (task_versions), as every new order runs. Pinned by sha in the manifest."""
    L = dict(LANES[lane], payload=payload or TVER.newest_payload(LANES[lane]["payload"]))
    tasks = list(tasks or L["tasks"])
    unknown = set(tasks) - set(L["tasks"])
    assert not unknown, "unknown tasks %s" % unknown
    run_id = "%s%s.%s" % (L["run"], tag, time.strftime("%Y%m%d-%H%M%S"))
    pairs = [(t, r) for t in tasks for r in range(rep_start, rep_start + REPLICATES)]
    random.Random(20260926 + rep_start).shuffle(pairs)
    windows = []
    if pull:
        # independent single-tablet runs: ONE plain map order every free tablet pulls from (see redeal)
        wname = "%s.win.01" % run_id
        arm = {"name": "%s.arm" % wname, "payload": "%s/%s" % (PAYLOAD_ROOT, L["payload"]), "batch": 1,
               "items": [L["item"](t, r) for t, r in pairs], "control": {"item": L["control"], "expect": L["expect"]},
               "hardbench": {"stage": "calibration", "division": lane, "pairs": pairs}}
        order = dict(arm, kind="map", name=wname, user="test", require_arch="aarch64")
        defects = tolkien_defects(order)
        assert not defects, "Tolkien would refuse %s: %s" % (wname, defects)
        windows.append({"kind": "map", "name": wname, "arms": [arm], "_order": order})
    wi = int(window_items or WINDOW_ITEMS)
    for i in range(0, len(pairs) if not pull else 0, wi):
        chunk = pairs[i:i + wi]
        items = [L["item"](t, r) for t, r in chunk]
        wname = "%s.win.%02d" % (run_id, len(windows) + 1)
        arm = {"name": "%s.arm" % wname, "payload": "%s/%s" % (PAYLOAD_ROOT, L["payload"]), "batch": 1,
               "distinct_workers": len(items), "items": items,
               "control": {"item": L["control"], "expect": L["expect"]},
               "hardbench": {"stage": "calibration", "division": lane, "pairs": chunk}}
        windows.append({"kind": "map.window", "name": wname, "user": "test", "require_arch": "aarch64", "arms": [arm]})
    intent = {"schema": "hardbench-calibration-v1", "run_id": run_id, "division": lane, "model": L["model"],
              "tasks": sorted(tasks), "replicates": REPLICATES, "replicate_start": rep_start, "items": len(pairs),
              "payload_sha256": {L["payload"]: sha(LIVE_ROOT / L["payload"])},
              "tolkien_source_sha256": {n: sha(TOLKIEN_ROOT / n) for n in ("tablet_foundry.py", "tablet_fulfil.py", "tablet_runner.py")},
              "windows": windows}
    MANIFESTS.mkdir(exist_ok=True)
    manifest = MANIFESTS / ("intent-%s.json" % run_id)
    assert not manifest.exists()
    dests = [INBOX / (w["name"] + ".tjob.json") for w in windows]
    assert not any(d.exists() for d in dests), "order collision"
    assert sum(len(w["arms"][0]["items"]) for w in windows) == len(pairs) == len(tasks) * REPLICATES
    orders = [w.pop("_order", None) or w for w in windows]      # a pull order goes to the inbox flat; the manifest
    manifest.write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")       # keeps the readers' shape
    for o, dst in zip(orders, dests):
        tmp = INBOX / ("." + dst.name + ".tmp")
        tmp.write_text(json.dumps(o, indent=1), encoding="utf-8")
        os.replace(tmp, dst)
    print("submitted run_id=%s windows=%d items=%d manifest=%s" % (run_id, len(windows), len(pairs), manifest.name))
    return manifest


def resubmit(manifest, window_names, per_window=5):
    """Re-issue the exact (task, replicate) pairs of named windows (e.g. one Tolkien voided for too few distinct
    workers) as smaller windows under a new run id; reduce the original and this manifest together."""
    src = json.loads(pathlib.Path(manifest).read_text(encoding="utf-8"))
    pinned = list(src["payload_sha256"])
    assert len(pinned) == 1, "the manifest pins %d payloads" % len(pinned)
    L = dict(LANES[src.get("division", "coding")], payload=pinned[0])     # exactly the payload the run used
    pairs = [tuple(p) for w in src["windows"] if w["name"] in window_names for p in w["arms"][0]["hardbench"]["pairs"]]
    assert pairs, "no pairs matched %s" % window_names
    run_id = "%s-re.%s" % (src["run_id"].split(".")[0], time.strftime("%Y%m%d-%H%M%S"))
    windows = []
    for i in range(0, len(pairs), per_window):
        chunk = pairs[i:i + per_window]
        wname = "%s.win.%02d" % (run_id, len(windows) + 1)
        arm = {"name": "%s.arm" % wname, "payload": "%s/%s" % (PAYLOAD_ROOT, L["payload"]), "batch": 1,
               "distinct_workers": len(chunk), "items": [L["item"](t, r) for t, r in chunk],
               "control": {"item": L["control"], "expect": L["expect"]},
               "hardbench": {"stage": "calibration", "division": src["division"], "pairs": chunk, "reissue_of": window_names}}
        windows.append({"kind": "map.window", "name": wname, "user": "test", "require_arch": "aarch64", "arms": [arm]})
    sha_now = sha(LIVE_ROOT / L["payload"])
    assert sha_now == src["payload_sha256"][L["payload"]], "payload changed since the original run - not a like-for-like re-issue"
    intent = dict(src, run_id=run_id, items=len(pairs), windows=windows, reissue_of={"manifest": pathlib.Path(manifest).name,
                                                                                         "windows": window_names})
    out = MANIFESTS / ("intent-%s.json" % run_id)
    assert not out.exists()
    out.write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
    for w in windows:
        dst = INBOX / (w["name"] + ".tjob.json")
        tmp = INBOX / ("." + dst.name + ".tmp")
        tmp.write_text(json.dumps(w, indent=1), encoding="utf-8")
        os.replace(tmp, dst)
    print("re-issued %d pairs from %s as run_id=%s windows=%d manifest=%s" % (len(pairs), window_names, run_id, len(windows), out.name))
    return out


def tolkien_defects(spec):
    """Tolkien's own intake check (tablet_foundry.spec_defects), run here before anything reaches the inbox."""
    for p in (str(TOLKIEN_ROOT), MPATH.ORCH_MIRROR):
        if p not in sys.path:
            sys.path.insert(0, p)
    import tablet_foundry
    return tablet_foundry.spec_defects(spec)


def result_arms(res, arm_name):
    """The arms of a delivered result: a map.window result lists them; a plain map result IS its one arm."""
    if "arms" in res:
        return res["arms"]
    return [dict(res, name=arm_name)] if "rows" in res else []


def redeal(manifests):
    """Re-deal every not-yet-delivered pair of each run as ONE order per run with no distinct_workers: every tablet pulls
    the next pair the moment it is free and a straggler holds only its own pair. For runs whose pairs are independent single-tablet runs; each row still records
    its host. Stop the original windows with a prefix slip; reduce the original manifest and this one together."""
    out = []
    for manifest in manifests:
        src = json.loads(pathlib.Path(manifest).read_text(encoding="utf-8"))
        delivered = [w["name"] for w in src["windows"] if _results_for(w["name"])]
        pending = [w for w in src["windows"] if w["name"] not in delivered]
        pairs = [tuple(p) for w in pending for p in w["arms"][0]["hardbench"]["pairs"]]
        assert pairs, "%s: every window delivered - nothing to re-deal" % src["run_id"]
        arm0 = pending[0]["arms"][0]
        lane = src.get("division", "coding")
        pay = arm0["payload"].rsplit("/", 1)[-1]
        assert all(w["arms"][0]["payload"] == arm0["payload"] for w in pending), "one payload per run"
        items = [it for w in pending for it in w["arms"][0]["items"]]
        assert len(items) == len(pairs), "items and pairs disagree"
        base = src["run_id"].split(".")[0]
        run_id = "%s.%s" % (base if base.endswith("-pull") else base + "-pull", time.strftime("%Y%m%d-%H%M%S"))
        wname = "%s.win.01" % run_id
        arm = {"name": "%s.arm" % wname, "payload": arm0["payload"], "batch": 1, "items": items,
               "control": arm0["control"],
               "hardbench": {"stage": "calibration", "division": lane, "pairs": pairs, "redeal_of": [w["name"] for w in pending]}}
        # a plain map order: Tolkien deals it to every free tablet (map.window means pinned distinct-worker cohorts)
        order = dict(arm, kind="map", name=wname, user="test", require_arch="aarch64")
        defects = tolkien_defects(order)
        assert not defects, "Tolkien would refuse %s: %s" % (wname, defects)
        window = {"kind": "map", "name": wname, "arms": [arm]}           # the manifest keeps the readers' shape
        intent = dict(src, run_id=run_id, items=len(pairs), windows=[window],
                      payload_sha256={pay: sha(LIVE_ROOT / pay)},
                      redeal_of={"manifest": pathlib.Path(manifest).name, "windows": [w["name"] for w in pending],
                                 "delivered_before": delivered})
        dst = MANIFESTS / ("intent-%s.json" % run_id)
        assert not dst.exists()
        dst.write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
        inbox = INBOX / (wname + ".tjob.json")
        tmp = INBOX / ("." + inbox.name + ".tmp")
        tmp.write_text(json.dumps(order, indent=1), encoding="utf-8")
        os.replace(tmp, inbox)
        print("re-dealt %d pairs of %d undelivered windows of %s as ONE pull order %s" % (
            len(pairs), len(pending), src["run_id"], wname))
        out.append(dst)
    return out


def redeal_instrument_failures(manifests):
    """Re-deal, as ONE pull order per run, every pair whose delivered rows are ALL instrument failures
    (`answer_boundary:...` - the payload could not read the output: a node or instrument fault, never the item's) or
    that never delivered. A pair with any real row stands."""
    out = []
    for manifest in manifests:
        src = json.loads(pathlib.Path(manifest).read_text(encoding="utf-8"))
        w0 = src["windows"][0]
        arm0 = w0["arms"][0]
        want = {tuple(p): it for w in src["windows"] for p, it in zip(w["arms"][0]["hardbench"]["pairs"], w["arms"][0]["items"])}
        real = set()
        for w in src["windows"]:
            for f in _results_for(w["name"]):
                d = json.loads(f.read_text(encoding="utf-8"))
                for a in result_arms(d, w["arms"][0]["name"]):
                    for r in a.get("rows", []):
                        if r.get("arm") == "control" or str(r.get("error", "")).startswith("answer_boundary:"):
                            continue
                        real.add((r.get("task"), int(r.get("replicate", -1))))
        redo = [p for p in want if (p[0], int(p[1])) not in real]
        if not redo:
            print("%s: every pair has a real row - nothing to re-deal" % src["run_id"])
            continue
        base = src["run_id"].split(".")[0]
        run_id = "%s.%s" % (base if base.endswith("-pull") else base + "-pull", time.strftime("%Y%m%d-%H%M%S"))
        wname = "%s.win.01" % run_id
        arm = {"name": "%s.arm" % wname, "payload": arm0["payload"], "batch": 1, "items": [want[p] for p in redo],
               "control": arm0["control"], "hardbench": {"stage": "calibration", "division": src.get("division"),
                                                         "pairs": redo, "redeal_of": [w["name"] for w in src["windows"]]}}
        order = dict(arm, kind="map", name=wname, user="test", require_arch="aarch64")
        defects = tolkien_defects(order)
        assert not defects, "Tolkien would refuse %s: %s" % (wname, defects)
        intent = dict(src, run_id=run_id, items=len(redo), windows=[{"kind": "map", "name": wname, "arms": [arm]}],
                      redeal_of={"manifest": pathlib.Path(manifest).name, "why": "instrument failures / undelivered"})
        dst = MANIFESTS / ("intent-%s.json" % run_id)
        assert not dst.exists()
        dst.write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
        inbox = INBOX / (wname + ".tjob.json")
        tmp = INBOX / ("." + inbox.name + ".tmp")
        tmp.write_text(json.dumps(order, indent=1), encoding="utf-8")
        os.replace(tmp, inbox)
        print("re-dealt %d of %d pairs of %s (instrument failures / undelivered) as %s" % (len(redo), len(want),
                                                                                         src["run_id"], wname))
        out.append(dst)
    return out


def _results_for(wname):
    return sorted(OUTBOX.glob(wname + ".*.results.json"))


def _held_names():
    if not (MPATH.TABLET_HEAD and MPATH.TABLET_HEAD_STACK):
        return []                                  # no tablet head on this lab: nothing of its can be held
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", MPATH.TABLET_HEAD,
                              "ls %s/tablet-foundry/queue 2>/dev/null | grep HELD" % MPATH.TABLET_HEAD_STACK],
                             capture_output=True, text=True, timeout=30).stdout
        return [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        return None


def watch(manifest, deadline_s=3 * 3600, every_s=60):
    intent = json.loads(pathlib.Path(manifest).read_text(encoding="utf-8"))
    names = [w["name"] for w in intent["windows"]]
    t_end = time.time() + deadline_s
    last = -1
    while True:
        done = [n for n in names if _results_for(n)]
        if len(done) != last:
            print("%s results %d/%d" % (time.strftime("%H:%M:%S"), len(done), len(names)), flush=True)
            last = len(done)
        if len(done) == len(names):
            print("VERDICT: DONE"); return 0
        held = _held_names()
        mine = [h for h in (held or []) if intent["run_id"] in h]
        if mine:
            print("VERDICT: HELD %s" % mine); return 2
        if time.time() > t_end:
            print("VERDICT: INCONCLUSIVE_AT_DEADLINE %d/%d" % (len(done), len(names))); return 3
        time.sleep(every_s)


def reduce(*manifests, tag=""):
    """One or more manifests (pass 1 + pass 2 ...): rows pool per task, so pass rates are over ALL seeds.
    tag: appended to the product names (calibration-vlm-<tag>.*) when one lane has more than one run family."""
    intents = [json.loads(pathlib.Path(m).read_text(encoding="utf-8")) for m in manifests]
    lanes = {i.get("division", "coding") for i in intents}
    assert len(lanes) == 1, "one division per reduce, got %s" % lanes
    lane = lanes.pop()
    L = LANES[lane]
    sfx = ("" if lane == "coding" else "-" + lane) + ("-" + tag if tag else "")
    # a window re-issued by a later manifest is replaced by it: drop it from the windows AND the expected count
    reissued = {n for i in intents for n in (i.get("reissue_of") or {}).get("windows", [])}
    windows = [w for i in intents for w in i["windows"] if w["name"] not in reissued]
    intent = {"run_id": " + ".join(i["run_id"] for i in intents),
              "items": sum(len(w["arms"][0]["items"]) for w in windows), "windows": windows}
    if reissued:
        print("re-issued windows replaced:", sorted(reissued))
    out_dir = pathlib.Path(MPATH.CAMPAIGNS) / ("hard-bench-%s" % intents[0]["run_id"].split(".")[1][:8])
    out_dir.mkdir(parents=True, exist_ok=True)
    prior = out_dir / ("calibration%s.md" % sfx)
    if prior.exists():
        head = prior.read_text(encoding="utf-8").splitlines()[0]
        assert head.endswith("(%s)" % intent["run_id"]), \
            "%s holds another run (%s) - reduce with --tag instead of overwriting it" % (prior.name, head)
    rows, arm_status, unusable, instrument = [], collections.Counter(), [], []
    for w in intent["windows"]:
        files = _results_for(w["name"])
        chosen = None
        for f in files:
            d = json.loads(f.read_text(encoding="utf-8"))
            a = next((x for x in result_arms(d, w["arms"][0]["name"]) if x.get("name") == w["arms"][0]["name"]), None)
            if a and a.get("usable") and a.get("control_passed") and a.get("complete"):
                chosen = (f, a)
        if not chosen:
            arm_status["missing" if not files else "present_but_unusable"] += 1
            unusable.append(w["name"])
            continue
        arm_status["ok"] += 1
        for r in chosen[1]["rows"]:
            if r.get("arm") == "control":
                continue
            err = r.get("error") or ("" if r.get("ran", True) else r.get("reason", "not-run"))
            rec = {"window": w["name"], "task": r["task"], "replicate": r["replicate"], "seed": r["seed"],
                   "host": r.get("_host", ""), "ok": bool(r.get("ok")), "checks_passed": r.get("checks_passed"),
                   "checks_total": r.get("checks_total"), "wall_s": r.get("wall_s"), "error": err,
                   "answer": str(r.get("answer", "")), "file": chosen[0].name}
            # an unreadable output is the INSTRUMENT failing, not the model: kept out of every pass rate, counted
            (instrument if str(err).startswith("answer_boundary:") else rows).append(rec)
    expected = intent["items"]
    print("arms:", dict(arm_status), "| rows:", len(rows), "of", expected, "| unusable windows:", unusable,
          "| instrument failures (answer boundary):", len(instrument))
    assert rows, "zero rows reduced - the join is broken, not the bench"
    by_task = collections.defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r)
    table = []
    for t in sorted(L["tasks"]):
        rs = by_task.get(t, [])
        n = len(rs)
        p = sum(r["ok"] for r in rs) / n if n else None
        walls = [float(r["wall_s"] or 0) for r in rs]
        table.append({"task": t, "kind": L["kind"](t), "n": n, "pass_rate": None if p is None else round(p, 2),
                      "checks_mean": round(sum((r["checks_passed"] or 0) / (r["checks_total"] or 1) for r in rs) / n, 2)
                                     if n and any(r["checks_total"] for r in rs) else None,
                      "wall_mean_s": round(sum(walls) / n, 1) if n else None, "hosts": ",".join(sorted({r["host"] for r in rs})),
                      "errors": ";".join(sorted({r["error"] for r in rs if r["error"]}))[:80],
                      "band": (p is not None and BAND[0] <= p <= BAND[1])})
    with open(out_dir / ("calibration_rows%s.csv" % sfx), "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader(); wr.writerows(rows)
    with open(out_dir / ("calibration%s.csv" % sfx), "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(table[0]))
        wr.writeheader(); wr.writerows(table)
    kept = [t for t in table if t["band"]]
    md = ["# Hard Bench — %s calibration (%s)" % (lane, intent["run_id"]), "",
          "rows %d of %d expected; arms %s; unusable windows %s; instrument failures (answer boundary, excluded) %d; band %s-%s"
          % (len(rows), expected, dict(arm_status), unusable, len(instrument), BAND[0], BAND[1]), "",
          "| task | kind | n | pass_rate | checks_mean | wall_mean_s | band | errors |", "|---|---|---|---|---|---|---|---|"]
    for t in table:
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (t["task"], t["kind"], t["n"], t["pass_rate"], t["checks_mean"], t["wall_mean_s"], "KEEP" if t["band"] else "", t["errors"]))
    md += ["", "kept (in band): %d -> %s" % (len(kept), [t["task"] for t in kept]),
           "all-pass: %s" % [t["task"] for t in table if t["pass_rate"] == 1.0],
           "all-fail: %s" % [t["task"] for t in table if t["pass_rate"] == 0.0]]
    (out_dir / ("calibration%s.md" % sfx)).write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print("wrote", out_dir)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "submit":
        # submit [--lane coding|tool|vlm] [--tasks a,b,c] [--rep-start N] [--tag -p2]
        argv = sys.argv[2:]
        opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
        submit(tasks=opt("--tasks").split(",") if opt("--tasks") else None,
               rep_start=int(opt("--rep-start", 0)), tag=opt("--tag", ""), lane=opt("--lane", "coding"),
               window_items=opt("--window"), payload=opt("--payload"), pull="--pull" in argv)
    elif cmd == "resubmit":
        # resubmit <manifest> <window-name>[,<window-name>...]
        resubmit(sys.argv[2], sys.argv[3].split(","))
    elif cmd == "redeal":
        # redeal <manifest> [<manifest>...]   (stop the original windows with a prefix slip first)
        redeal(sys.argv[2:])
    elif cmd == "redeal-failures":
        # redeal-failures <manifest> [<manifest>...]   (after the run has delivered)
        redeal_instrument_failures(sys.argv[2:])
    elif cmd == "watch":
        sys.exit(watch(sys.argv[2]))
    elif cmd == "reduce":
        # reduce [--tag 2x] <manifest> [<manifest>...]
        argv = sys.argv[2:]
        tag = ""
        if "--tag" in argv:
            i = argv.index("--tag")
            tag = argv[i + 1]
            del argv[i:i + 2]
        reduce(*argv, tag=tag)
