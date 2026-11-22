r"""Project 34 menu - the big table (layer 1): one row per measured cell, from every product the lab has shipped.

the owner "I *want* the huge thing." The Fellowship Gauntlet's 688-cell table is the precedent; this is its big brother across every machine.

Writes Documentation\Datasets\Project34-Menu\:
  menu-cells.csv one row per cell: shape, box, host, model, family, task, seed, knobs, verdict, time, energy
  menu-answers.jsonl cell_id -> the raw answer, so a fixed grader RE-GRADES instead of re-running
  build-manifest.json every source file with bytes + sha256 + rows taken, and per-source row counts

Coding rows (fleet, tablets, rigby) are RE-GRADED here from their saved answers with ONE instrument: the family's
payload scorer + the fence-tolerant extractor. Tool/vlm rows keep their reducer's verdict; gauntlet rows keep the gauntlet's rubric. `grader` says which.

usage: python build_menu.py rebuild everything (grades new answers one at a time)
       python build_menu.py --workers 8 grade the new answers on 8 processes first (0 = every core)
       python build_menu.py --jobs-out JOBS.json write the new answers for a Mothership grading order, then stop
       python build_menu.py --import-grades G.jsonl merge that order's delivered grades into the cache, then rebuild
Grades are cached in grade_cache.jsonl (keyed by payload sha + grader fingerprint + task + answer sha)."""
import ast
import collections
import csv
import glob
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_price as MP  # noqa: E402
import menu_void as MV  # noqa: E402
import menu_fill as MF  # noqa: E402  the placement epochs (RIGBY_PLACEMENT `since`) - one derivation with the filler
import menu_wall as MW  # noqa: E402  whole-box wall joules per answer window
VOIDS = MV.load()                  # instrument-fault deliveries: rows kept on the menu, marked unusable with the reason
import menu_paths as MPATH  # noqa: E402  every folder, mirror and box table the menu reads
sys.path.insert(0, MPATH.FOUNDRY_MIRROR)
import grade_jobs as GJ  # noqa: E402  the one copy of the grading logic (also the Mothership grading order's payload)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task_versions as TVER  # noqa: E402  versioned task ids and the payload generation that defines each

CAMP = MPATH.CAMPAIGNS
OUTBOX = MPATH.OUTBOX
PAYLOADS = MPATH.PAYLOADS
HERE = os.path.dirname(os.path.abspath(__file__))
MANIFESTS = os.path.join(HERE, "manifests")
GAUNTLET = MPATH.GAUNTLET
OUT = MPATH.MENU

COLS = ["cell_id", "source_kind", "campaign", "shape", "box", "host", "model", "family", "task", "seed", "replicate",
        "temperature", "think", "draft_model", "decoding", "n", "ok", "checks_passed", "checks_total", "wall_s",
        "energy_j", "card_j", "wall_extra_j", "wall_src", "grader", "error", "usable", "priced_out", "void", "source_file"]
# `energy_j` = the WHOLE BOX at the wall over the answer's window (menu_wall: measured from the box's wall log, else its
# fitted line - `wall_src` says which); `wall_extra_j` = that minus the box's idle; `card_j` = the card meter (the old
# energy_j). The menu prices the total draw, with the extra and the card shown beside it.
# `priced_out` = the answer was cut at the order's price line: a stated COST measurement (ok is False, the answer is
# never graded, the row stays usable). `void` = "cap": the answer ended at the context limit, so the row measured the
# limit and not the model: usable is False and menu_coverage.py leaves it out.
BOX_SHAPE = MPATH.box_shape()

rows, answers, sources = [], {}, {}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


PROG = {"label": "", "i": 0, "n": 0, "item": "", "t0": 0.0}
TTY = sys.stdout.isatty()
GREEN, RESET = "\033[32m", "\033[0m"


def _dur(s):
    s = int(s)
    return "%dm%02ds" % divmod(s, 60) if s >= 60 else "%ds" % s


def status():
    """Overall first (grade x of y, bar, elapsed, ETA for the whole build), then where it is (step, file x of y)."""
    g = globals().get("G")
    hits, new = (g.hits, g.misses) if g else (0, 0)
    done, total = hits + new, (g.planned if g else 0)
    width = 30
    fill = int(width * done / total) if total else 0
    bar = "#" * fill + "-" * (width - fill)
    el = time.time() - (g.t0 if g and g.t0 else PROG["t0"])
    # cache hits are free, so the ETA is the NEW grades still to score x the measured seconds per new grade
    left_new = max(0, g.planned_new - new) if g else 0
    eta = _dur(left_new * g.new_s / new) if g and new else "?"
    return "[%s] grade %d/%d (%d cache, %d new of ~%d) | %s in, ETA %s | %s %d/%d | %s" % (
        (GREEN + bar + RESET) if TTY else bar, done, total, hits, new, g.planned_new if g else 0, _dur(el), eta,
        PROG["label"], PROG["i"], PROG["n"], PROG["item"][-60:])


def tick(what=""):
    """Every grade: a console redraws the bar in place; a log file gets one line per grade."""
    if TTY:
        sys.stdout.write("\r\033[K" + status())
        sys.stdout.flush()
    else:
        print(status() + ("  " + what if what else ""), flush=True)


def progress(items, label):
    """Iterate `items` with a live bar: x of y, the item in hand, the grades so far, and an ETA for this step."""
    items = list(items)
    PROG.update(label=label, i=0, n=len(items), t0=time.time())
    quiet = bool(globals().get("G") and G.counting)
    for i, it in enumerate(items, 1):
        PROG["i"], PROG["item"] = i, os.path.basename(str(it))
        if quiet:
            pass
        elif TTY:
            tick()
        else:
            print(status(), flush=True)
        yield it
    if TTY and not quiet:
        sys.stdout.write("\n")


def take(path):
    """Record a source file (bytes + sha) the first time it is read."""
    if path not in sources:
        sources[path] = {"bytes": os.path.getsize(path), "sha256": sha(path), "rows": 0}
    return path


GRADE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grade_cache.jsonl")


class Grader:
    """One coding instrument per family: the payload's own checks and score(), the fence-tolerant extractor.

    A grade is cached under (payload sha, this grader's own code, task, answer sha): a saved answer is graded once per
    instrument, and any change to the payload or to grade_jobs.score_answer/FENCE changes every key, so nothing stale is
    ever reused."""

    def __init__(self):
        self.mods, self.work = {}, tempfile.mkdtemp(prefix="menu_grade_")
        self.fp = GJ.fingerprint()
        self.cache, self.hits, self.misses = {}, 0, 0
        self.counting, self.planned, self.t0 = False, 0, 0.0
        self.planned_new, self.planned_keys, self.new_s = 0, set(), 0.0   # new_s: seconds spent scoring new answers
        self.jobs, self.controls = [], []   # counting pass: the new answers to grade, and known grades to prove a box
        if os.path.exists(GRADE_CACHE):
            with open(GRADE_CACHE, encoding="utf-8") as f:
                for ln in f:
                    try:
                        k, v = json.loads(ln)
                        self.cache[k] = v
                    except ValueError:
                        pass
        self.out = None                      # opened at the first grade written: loading the module creates no file

    def mod(self, payload):
        if payload not in self.mods:
            self.mods[payload] = GJ.load_payload(os.path.join(PAYLOADS, payload))
        return self.mods[payload]

    def remember(self, key, g):
        self.cache[key] = g
        if self.out is None:
            self.out = open(GRADE_CACHE, "a", encoding="utf-8")
        self.out.write(json.dumps([key, g]) + "\n")
        self.out.flush()

    def grade(self, payload, task, answer):
        # the generation that defines this id grades it (task_versions): an old id stays graded by the file that ran it
        named = payload
        for payload in TVER.generations(named):
            if payload != named and not os.path.exists(os.path.join(PAYLOADS, payload)):
                continue                     # a later generation not generated yet; the named file loads as it always did
            m, psha = self.mod(payload)
            if GJ.task_def(m, task) is not None:
                break
        else:
            return None
        key = GJ.cache_key(psha, self.fp, task, answer)
        if self.counting:                    # the counting pass: how many grades the real pass will do, and how many new
            self.planned += 1
            if key not in self.cache and key not in self.planned_keys:
                self.planned_new += 1
                self.jobs.append({"key": key, "payload": payload, "task": task, "answer": answer or ""})
            elif key in self.cache and len(self.controls) < 8 and \
                    sum(bool(c["expect"]) == bool(self.cache[key]["ok"]) for c in self.controls) < 4:
                self.controls.append({"key": key, "payload": payload, "task": task, "answer": answer or "",
                                      "expect": bool(self.cache[key]["ok"])})
            self.planned_keys.add(key)
            return {"ok": False, "checks_passed": 0, "checks_total": 0, "grader": "count", "error": ""}
        if key in self.cache:
            self.hits += 1
            tick("%s cached %s" % (task, "PASS" if self.cache[key].get("ok") else "fail"))
            return format_failure(payload, m, answer, dict(self.cache[key]))
        t = time.time()
        g = GJ.grade_one(m, psha, task, answer, self.work)
        self.new_s += time.time() - t
        self.remember(key, g)
        self.misses += 1
        tick("%s graded %s (%s/%s checks)" % (task, "PASS" if g["ok"] else "fail", g["checks_passed"], g["checks_total"]))
        return format_failure(payload, m, answer, dict(g))


def is_c1(name):
    """C1 by its exact tokens: "c1" alone also matches the W1 tablet lane c15b."""
    return any(t in name for t in ("_c1.", "_c1_", "-c1-"))


def format_failure(payload, m, answer, g):
    """C1 keeps a FORMAT failure apart from a wrong answer (34-Hard-Bench-Order-Spec, C1): a python block with no
    `ANSWER = ...` scores 0/n with no error, exactly like a wrong value. Read the block's syntax tree - never run it -
    and name that case in the error field. Verdicts are unchanged; only the label of a silent zero is added."""
    if not is_c1(payload) or g.get("ok") or g.get("checks_passed") or g.get("error"):
        return g
    code = m.extract_code(answer or "")
    try:
        tree = ast.parse(code)
    except SyntaxError:
        g["error"] = "format: python block does not parse"
        return g
    if not any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ANSWER" for t in n.targets)
               for n in ast.walk(tree)):
        g["error"] = "format: no ANSWER assignment"
    return g


G = Grader()
FAMILY_PAYLOAD = {"coding-f1": "map_swarm_code_hard.py", "coding-f2": "map_swarm_code_hard_f2.py",
                  "work-w1": "map_swarm_code_hard_w1.py",      # W1 is graded like coding: its payload's own checks
                  "chat-c1": "map_swarm_code_hard_c1.py"}      # and so is C1 (canonical ANSWER blocks)


METHOD_VOIDED = collections.Counter()
# A delivery the desk says is DONE but this build cannot find is a reader fault, never "nothing to add". Listed in build-manifest.json and logged by menu_refresh; it REPORTS, it does not block
# the menu (one lost delivery must not freeze every other price).
DESK_DIR = os.path.join(MPATH.DOCS_STATE_DIR, "p20-render-queue")
UNREAD = []
_DESK_DONE = {}


def desk_done(order_name):
    """True when the desk records this factory order as delivered (status done)."""
    if not _DESK_DONE:
        for p in glob.glob(os.path.join(DESK_DIR, "*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            if d.get("kind") == "factory_order" and d.get("order_name"):
                _DESK_DONE[d["order_name"]] = d.get("status") == "done"
        _DESK_DONE.setdefault("", False)          # read once even when the desk holds no records
    return _DESK_DONE.get(order_name, False)
CODING_ONLY = collections.Counter()        # rigby rows of a non-coding family (kept their own verdict)


def add(row, answer=None):
    row = {c: row.get(c, "") for c in COLS}
    rows.append(row)
    if answer is not None:
        answers[row["cell_id"]] = answer
    sources[row["source_file"]]["rows"] += 1


def wall(host, t0, t1, card_j, wall_s):
    """The energy columns of one answer: the whole box at the wall (menu_wall), its extra over idle, and the card meter."""
    p = MW.price(host, t0, t1, card_j, wall_s)
    r1 = lambda x: round(x, 1) if x is not None else None                # noqa: E731
    return {"energy_j": r1(p["wall_j"]), "card_j": card_j, "wall_extra_j": r1(p["wall_extra_j"]),
            "wall_src": p["wall_src"]}


def arg(args, flag, default=""):
    return args[args.index(flag) + 1] if flag in args else default


# --- 1. fleet orders (GPU boxes): coding families, every rung, every knob -----------------------------------------
def fleet_orders():
    # w1ops (the 8 ops tasks, letters shuffled, ids <id>_sh) lives in its own campaign so the filler never reads it as W1
    for folder in progress(sorted(glob.glob(os.path.join(CAMP, "hard-bench-*", "fleet*")) +
                                  glob.glob(os.path.join(CAMP, "w1ops-*", "fleet*"))), "fleet_orders: folder"):
        if os.path.basename(folder) == "fleet-tv":
            continue
        sub = os.path.join(folder, "submission.json")
        if not os.path.exists(sub):
            continue
        with open(sub, encoding="utf-8") as f:
            orders = json.load(f).get("orders", {})
        for rung, rec in orders.items():
            out = MP.delivered_out(rec.get("out"))     # the newest delivered generation (a requeue re-delivers)
            if not out:
                name = os.path.basename(os.path.dirname(rec.get("out") or ""))
                if name and desk_done(name):
                    UNREAD.append(name)
                continue
            void_why = (VOIDS.get(MV._key(out)) or {}).get("why") if MV.is_voided(out, VOIDS) else None
            order_file = os.path.join(folder, "order_%s.json" % rung)
            args = []
            if os.path.exists(order_file):
                with open(order_file, encoding="utf-8") as f:
                    args = json.load(f)["spec"].get("args", [])
            grade_pay = os.path.basename(arg(args, "--grade").replace("\\", "/")) or None
            family = ("chat-c1" if is_c1(grade_pay or "") or is_c1(os.path.basename(folder) + "-") else
                      "work-w1" if (grade_pay and "_w1" in grade_pay) or "-w1" in folder else
                      "coding-f2" if (grade_pay and "_f2" in grade_pay) or "-f2" in folder else "coding-f1")
            payload = grade_pay if grade_pay and "_w1ops" in grade_pay else FAMILY_PAYLOAD[family]
            with open(take(out), encoding="utf-8") as f:
                d = json.load(f)
            host = d.get("host", "")
            for r in d["rows"]:
                task = r.get("id")
                status = MP.row_status(r, d.get("num_ctx"))
                if status == "priced_out":      # cut at the price line: a stated cost, never graded
                    g = {"ok": False, "checks_passed": 0, "checks_total": 0,
                         "grader": "priced_out (cut at the price line, not graded)", "error": "priced_out"}
                else:
                    g = G.grade(payload, task, r.get("answer") or "")
                if g is None:
                    continue
                cid = "fleet|%s|%s|%s|r%s|s%s" % (os.path.basename(folder), rung, task, r.get("round"), r.get("seed"))
                add({"cell_id": cid, "source_kind": "fleet-order", "campaign": os.path.basename(os.path.dirname(folder)),
                     "shape": "control" if task.startswith("control") else BOX_SHAPE.get(host, "S6"), "box": host,
                     "host": host, "model": r.get("served_model") or d.get("model"), "family": family, "task": task,
                     "seed": r.get("seed"), "replicate": r.get("round"),
                     "temperature": d.get("temperature") if d.get("temperature") is not None else arg(args, "--temperature"),
                     "think": __import__("menu_fill").think_label(args),
                     "decoding": "mtp" if "mtp" in (d.get("model") or "") else "standard", "n": 1,
                     "wall_s": r.get("wall_s"), **wall(host, r.get("t0"), r.get("t1"), r.get("gpu_j"), r.get("wall_s")),
                     "source_file": out,
                     "usable": "False" if (status == "void" or void_why) else "True",
                     "priced_out": status == "priced_out",
                     "void": ("instrument: %s" % void_why) if void_why else "cap" if status == "void" else "", **g},
                    r.get("answer"))


# --- 2. fleet tool / vision (reduced rows keep the reducer's verdict) ---------------------------------------------
# 1x vision is void BY METHOD for the Qwen 3.5/3.6/3.8 family: their 1x prompts read ~175-183 tokens, the image never registers. qwen3-vl reads ~1,110 and keeps its 1x cells.
VLM1X_METHOD_VOID = re.compile(r"^qwen3\.[568][:\-]")


def method_void(division, rung, model):
    return division == "vlm" and not rung.startswith("vlm2x") and bool(VLM1X_METHOD_VOID.match(model or ""))


def fleet_tv():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hard_bench_fleet_tv import THINK_RUNGS                     # the driver that passed --think owns the fact
    for p in sorted(glob.glob(os.path.join(CAMP, "hard-bench-*", "fleet-tv", "fleet_tv_rows.csv"))):
        with open(take(p), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rung = r["rung"]
                cid = "fleet-tv|%s|%s|r%s" % (rung, r["task"], r["round"])
                # `status` is the reducer's own classification (menu_price.row_status): re-run hard_bench_fleet_tv.py reduce
                # before building, or a capped row would be listed as a measurement
                assert "status" in r, "%s has no status column: re-run hard_bench_fleet_tv.py reduce first" % p
                mv = method_void(r["division"], rung, r["model"])
                METHOD_VOIDED[r["model"]] += mv
                add({"cell_id": cid, "source_kind": "fleet-tv", "campaign": os.path.basename(os.path.dirname(os.path.dirname(p))),
                     "shape": BOX_SHAPE.get(r["host"], "S6"), "box": r["host"], "host": r["host"], "model": r["model"],
                     "family": r["division"] + ("-2x" if rung.startswith("vlm2x") else ""), "task": r["task"],
                     "replicate": r["round"], "temperature": float(r.get("temperature") or 0.6),
                     "think": r.get("think") or ("on" if rung in THINK_RUNGS else "off"),
                     "decoding": "standard", "n": 1, "ok": r["ok"], "wall_s": r["wall_s"],
                     **wall(r["host"], r.get("t0"), r.get("t1"), r["gpu_j"], r["wall_s"]),
                     "grader": "fleet_tv reduce", "error": r.get("error", ""), "source_file": p,
                     "usable": "False" if (r["status"] == "void" or mv) else "True",
                     "priced_out": r["status"] == "priced_out",
                     "void": "method" if mv else ("cap" if r["status"] == "void" else "")},
                    r.get("answer"))


# --- 3. tablet single tries (calibrations; every lane, every knob) ------------------------------------------------
def row_verdict(r, source):
    """A tool/vision row keeps the verdict of the scorer that ran it; only coding is re-graded here."""
    if not r.get("ran") and not r.get("answer"):
        return {"ok": False, "checks_passed": 0, "checks_total": 0, "grader": "not-run", "error": r.get("error", "")}
    return {"ok": r.get("ok"), "checks_passed": r.get("checks_passed", ""), "checks_total": r.get("checks_total", ""),
            "grader": source, "error": r.get("error", "")}


def tablet_calibrations():
    for man in progress(sorted(glob.glob(os.path.join(MANIFESTS, "intent-hardbench-code*-cal*.json")) +
                               glob.glob(os.path.join(MANIFESTS, "intent-hardbench-tool*-cal*.json")) +
                               glob.glob(os.path.join(MANIFESTS, "intent-hardbench-w1-*-cal*.json")) +
                               glob.glob(os.path.join(MANIFESTS, "intent-hardbench-c1-*-cal*.json"))),
                        "tablet_calibrations: manifest"):
        with open(man, encoding="utf-8") as f:
            intent = json.load(f)
        payload = list(intent["payload_sha256"])[0]
        family = ("tool" if payload.startswith("map_tool") else "chat-c1" if is_c1(payload) else
                  "work-w1" if "_w1" in payload else "coding-f2" if "_f2" in payload else "coding-f1")
        temp = {"_t00": 0.0, "_t03": 0.3, "_t09": 0.9}.get(next((s for s in ("_t00", "_t03", "_t09") if s in payload), ""), 0.6)
        think = "off" if payload.endswith("nt.py") else ("on" if "q35" in payload else "n/a")
        run = intent["run_id"]
        for w in intent["windows"]:
            hits = sorted(glob.glob(os.path.join(OUTBOX, "%s.*.results.json" % w["name"])))
            if not hits:
                continue
            with open(take(hits[-1]), encoding="utf-8") as f:
                res = json.load(f)
            for arm in res["arms"] if "arms" in res else ([res] if "rows" in res else []):   # a plain map result is one arm
                for r in arm.get("rows", []):
                    # the payload could not find the model's reply (answer_boundary:*) - an instrument failure, never the
                    # model's answer; the pair was re-dealt, so it is left out like a cap row
                    instrument = str(r.get("error") or "").startswith("answer_boundary")
                    if instrument:
                        g = {"ok": False, "checks_passed": 0, "checks_total": 0, "grader": "not-run (instrument)",
                             "error": r.get("error", "")}
                    elif family == "tool":
                        g = row_verdict(r, "tablet tool scorer:%s" % payload)
                    elif not r.get("ran") and not r.get("answer"):
                        g = {"ok": False, "checks_passed": 0, "checks_total": 0, "grader": "not-run", "error": r.get("error", "")}
                    else:
                        g = G.grade(payload, r.get("task"), r.get("answer") or "") or {}
                    # the control rides in every window, so the window is part of the cell's identity
                    cid = "tablet|%s|%s|%s|%s" % (run, w["name"][len(run) + 1:], r.get("task"), r.get("seed"))
                    add({"cell_id": cid, "source_kind": "tablet-calibration", "campaign": run,
                         "usable": "False" if instrument else arm.get("usable"),
                         "void": "instrument" if instrument else "",
                         "shape": "control" if str(r.get("task", "")).startswith("control") else "S1",
                         "box": "fellowship", "host": r.get("_host", ""), "model": r.get("model") or intent["model"],
                         "family": family, "task": r.get("task"), "seed": r.get("seed"), "replicate": r.get("replicate"),
                         "temperature": r.get("temperature", temp), "think": think, "decoding": "standard", "n": 1,
                         "wall_s": r.get("wall_s"), "energy_j": MW.tablet_j(r.get("wall_s")),
                         "wall_src": "spec ceiling (USB port %g W)" % MW.TABLET_PORT_W,
                         "source_file": hits[-1], **g}, r.get("answer"))


# --- 4. tablet ensembles (matrix arms: shape S2, n = tablets) ------------------------------------------------------
def tablet_matrices():
    for p in sorted(glob.glob(os.path.join(CAMP, "hard-bench-*", "matrix_cells*.csv"))):
        tag = os.path.basename(p)[len("matrix_cells"):-4]
        if "vlm" in tag:
            family, model = "vlm", "SmolVLM2-500M (1x images)"
            grader = "matrix reduce (exact/contains)"
        else:
            family = "coding-f2" if "f2" in tag else "coding-f1"
            model = "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M" if "05b" in tag else "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M"
            grader = "matrix reduce (hidden checks)"
        with open(take(p), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        sels = [s for s in ("best_of_n", "majority_verdict") if rows and s in rows[0]]
        assert sels, "%s: no selector column" % p
        for r in rows:
            for sel in sels:
                cid = "matrix|%s|%s|N%s|rep%s|%s" % (tag or "f1", r["task"], r["n"], r["repeat"], sel)
                add({"cell_id": cid, "source_kind": "tablet-matrix", "campaign": os.path.basename(os.path.dirname(p)),
                     "shape": "S2:" + sel, "box": "fellowship", "host": r.get("hosts", ""), "model": model,
                     "family": family, "task": r["task"], "replicate": r["repeat"], "temperature": 0.6,
                     "think": "n/a", "decoding": "standard", "n": r["n"], "ok": r[sel], "wall_s": r.get("wall_max_s", ""),
                     "energy_j": MW.tablet_j(r.get("wall_max_s"), r["n"]),
                     "wall_src": "spec ceiling (USB port %g W x %s tablets)" % (MW.TABLET_PORT_W, r["n"]),
                     "grader": grader, "source_file": p})


# --- 5. rigby alone and Voltron (Tolkien kind llm) -----------------------------------------------------------------
def llm_rows_by_order(paths):
    """{order name: [(row, result file)]}. An order stopped and requeued delivers a result file per generation, and a
    later one need not hold every earlier row, so rows merge
    per (task, arm, round) with the NEWEST file's copy winning - nothing dropped, nothing counted twice."""
    out = collections.defaultdict(dict)
    for p in paths:                                  # sorted: a file name ends with its delivery stamp, oldest first
        with open(take(p), encoding="utf-8") as f:
            res = json.load(f)
        for r in res["rows"]:
            out[res["name"]][(r["task"], r.get("arm"), r["replicate"])] = (r, p)
    return {n: list(v.values()) for n, v in out.items()}


def llm_orders():
    paths = progress(sorted(glob.glob(os.path.join(OUTBOX, "hardbench-rigby-*.results.json")) +
                            glob.glob(os.path.join(OUTBOX, "hardbench-voltron-*.results.json"))),
                     "llm_orders: result file")
    for name, pairs in llm_rows_by_order(paths).items():
        family = ("vlm-2x" if "-vlm2x-" in name else "vlm" if "-vlm-" in name else "tool" if "-tool-" in name
                  else "chat-c1" if "-c1-" in name else "work-w1" if "-w1-" in name
                  else "coding-f2" if "-f2" in name else "coding-f1")
        for r, p in pairs:
            if family not in FAMILY_PAYLOAD:
                g = row_verdict(r, "tolkien scorer (row verdict)")
                CODING_ONLY[family] += 1
            else:
                # a finished answer is graded even when Tolkien's own grader could not run it; stopped / priced rows are not answers
                finished = r.get("ran") or (r.get("answer") and r.get("finish_reason") not in ("stopped", "priced_out"))
                g = G.grade(FAMILY_PAYLOAD[family], r["task"], r.get("answer") or "") if finished else None
                g = g or {"ok": False, "grader": "not-run", "error": r.get("error", "")}
            arm = r.get("arm")
            arm = {"local": "rigby", "pooled": "voltron"}.get(arm, arm)
            cid = "llm|%s|%s|%s|r%s" % (name, arm, r["task"], r["replicate"])
            add({"cell_id": cid, "source_kind": "tolkien-llm", "campaign": name,
                 "shape": "control" if r["task"].startswith("control") else ("S5" if arm == "voltron" else "S5r"),
                 "box": "rigby" if arm == "rigby" else "rigby+fellowship", "host": arm, "model": r.get("model"),
                 "family": family, "task": r["task"], "seed": r.get("seed"), "replicate": r["replicate"],
                 "temperature": r.get("temperature"), "think": r.get("reasoning") or "default",
                 # before the --spec-type fix a draft was loaded but never used: those rows are standard decoding
                 "draft_model": r.get("draft_model") or "",
                 "decoding": r.get("spec_type") or ("standard (draft loaded, spec-type none)" if r.get("draft_model")
                                                    else "standard"),
                 "n": 1, "wall_s": r.get("wall_s"), "source_file": p,
                 "usable": "False" if MP.row_status(r) == "void" else "True", "priced_out": bool(r.get("priced_out")),
                 "void": "cap" if MP.row_status(r) == "void" else "", **g}, r.get("answer"))


# --- 6. the Fellowship Gauntlet -------------------------------------------------------
def gauntlet():
    if not os.path.exists(GAUNTLET):
        return
    with open(take(GAUNTLET), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            add({"cell_id": "gauntlet|" + r["item"], "source_kind": "gauntlet", "campaign": "fellowship-gauntlet-20260831",
                 "shape": "S1", "box": "fellowship", "host": r["host"], "model": r["model"],
                 "family": "gauntlet-" + r["division"], "task": r["task"], "n": 1, "ok": r["rubric_ok"],
                 "wall_s": r["wall_s"], "grader": "gauntlet v2 rubric (state=%s)" % r["state"], "error": r["reason"],
                 "think": "n/a", "decoding": "standard", "source_file": GAUNTLET}, r.get("answer"))


STEPS = None
LS = MPATH.LS                 # the share's UNC root is a setting (SSD_SHARED_UNC)
PAYLOADS_UNC = LS.setting("SSD_SHARED_UNC") + r"\tabfoundry-jobs\payloads"
WORKERS, JOBS_OUT, IMPORT_GRADES = 1, None, None


def import_grades(path):
    """Merge delivered grades into the cache; only keys of THIS grader count. Two shapes: a GPU order's grades.jsonl
    ([key, grade] per line) or a Tolkien map order's results.json (rows with key/grade/control/error). A Tolkien file
    with any control row graded WRONG or any node whose self-test failed is refused whole."""
    pairs = []
    if path.endswith(".results.json"):
        with open(path, encoding="utf-8") as f:
            res = json.load(f)
        rows = res.get("rows") or []
        wrong = [r.get("_host") for r in rows if r.get("control") == "GRADE_CONTROL_WRONG"]
        broken = sorted({r.get("_host") for r in rows if "SELFTEST_FAILED" in (r.get("error") or "")})
        if wrong or broken or not res.get("control_passed", True):
            raise SystemExit("REFUSED: %s - controls graded wrong on %s, self-test failed on %s, control_passed=%s"
                             % (path, wrong, broken, res.get("control_passed")))
        pairs = [(r["key"], r["grade"]) for r in rows if r.get("key") and r.get("grade")]
        print("tolkien results: %d rows, %d graded, %d errors" % (
            len(rows), len(pairs), sum(1 for r in rows if r.get("error"))), flush=True)
    else:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    pairs.append(tuple(json.loads(ln)))
                except ValueError:
                    continue
    mine = 0
    for key, g in pairs:
        parts = str(key).split("|")
        if len(parts) == 4 and parts[1] == G.fp and key not in G.cache:
            G.remember(key, g)
            mine += 1
    print("imported %d new grades of %d from %s" % (mine, len(pairs), path), flush=True)
    assert pairs, "zero grades read - the file is wrong, not empty of news"


def prefill(workers):
    """Grade every new answer in parallel first; the build pass then reads them all from the cache."""
    n = len(G.jobs)
    PROG.update(label="parallel grading (%s workers)" % (workers or os.cpu_count()), i=0, n=n, t0=time.time())
    t0 = time.time()

    def on_grade(i, n, job, g):
        if g is not None:
            G.remember(job["key"], g)
        PROG["i"], PROG["item"] = i, "%s %s" % (job["task"], "PASS" if g and g["ok"] else "fail")
        el = time.time() - t0
        line = "[%s] new grade %d/%d | %s in, ETA %s | %s" % (
            "#" * int(30 * i / n) + "-" * (30 - int(30 * i / n)), i, n, _dur(el), _dur(el / i * (n - i)), PROG["item"])
        if TTY:
            sys.stdout.write("\r\033[K" + line)
            sys.stdout.flush()
        else:
            print(line, flush=True)
    GJ.run(G.jobs, PAYLOADS, workers, on_grade)
    if TTY:
        sys.stdout.write("\n")
    print("parallel grading: %d answers in %s" % (n, _dur(time.time() - t0)), flush=True)


def supersede_letter_biased_ops(rows):
    """The original 8 W1 ops tasks put the right cause at B in all 8 and the right fix at B in 7: their rows, from every source, are not usable; W1 ops is read from the shuffled `_sh`
    rows (hard_tasks_w1ops.py). Returns how many rows it marked."""
    import hard_tasks_w1ops
    old = set(hard_tasks_w1ops.TARGET)
    old |= {p for t in list(old) for p in TVER.predecessors(t)}    # a versioned task's original id is the same task
    n = 0
    for r in rows:
        if r["family"] == "work-w1" and r["task"] in old:
            r["usable"], r["void"] = "False", "letter-biased (superseded by the w1ops _sh rows)"
            n += 1
    return n


def main():
    global STEPS, WORKERS, JOBS_OUT, IMPORT_GRADES
    a = sys.argv[1:]
    WORKERS = int(a[a.index("--workers") + 1]) if "--workers" in a else 1
    JOBS_OUT = a[a.index("--jobs-out") + 1] if "--jobs-out" in a else None
    IMPORT_GRADES = a[a.index("--import-grades") + 1] if "--import-grades" in a else None
    if IMPORT_GRADES:
        import_grades(IMPORT_GRADES)
    MW.sync(log=lambda m: print(m, flush=True))     # each box's new wall-log bytes; a failure keeps the cache
    STEPS = (fleet_orders, fleet_tv, tablet_calibrations, tablet_matrices, llm_orders, gauntlet)
    # counting pass: read every source exactly as the real pass will, run no tests, keep nothing but the total
    G.counting = True
    for step in STEPS:
        step()
    rows.clear()
    answers.clear()
    METHOD_VOIDED.clear()
    CODING_ONLY.clear()
    G.counting = False
    print("counted %d coding grades to do: %d new to score, the rest cached" % (G.planned, G.planned_new), flush=True)
    if JOBS_OUT:
        with open(JOBS_OUT, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": G.fp, "payload_dir": PAYLOADS_UNC, "controls": G.controls, "jobs": G.jobs}, f)
        print("wrote %d jobs + %d controls -> %s (submit with grade_order.py; nothing built)"
              % (len(G.jobs), len(G.controls), JOBS_OUT))
        return 0
    G.t0 = time.time()
    if G.jobs and WORKERS != 1:
        prefill(WORKERS)
    for step in STEPS:
        before = len(rows)
        step()
        print("%-20s %6d rows" % (step.__name__, len(rows) - before), flush=True)
    assert rows, "no rows collected - every source reader found nothing"
    biased = supersede_letter_biased_ops(rows)
    print("W1 ops rows superseded as letter-biased (read from the w1ops _sh rows instead): %d" % biased)
    assert biased, "the letter-biased ops void matched nothing - the task ids changed, not the ruling"
    # zero is legitimate here (no setting re-measured yet since an epoch), so it is reported, not asserted
    print("rigby rows superseded by a newer placement epoch (menu_fill.RIGBY_PLACEMENT since): %d"
          % MF.supersede_pre_placement(rows))
    if G.out is not None:
        G.out.close()
    print("coding grades: %d from cache, %d graded now (cache %s)" % (G.hits, G.misses, GRADE_CACHE))
    voided ={m: n for m, n in METHOD_VOIDED.items() if n}
    print("1x vision void by method: %d rows %s" % (sum(voided.values()), voided))
    assert voided, "the 1x-vision method void matched nothing - the model names changed, not the ruling"
    print("rigby non-coding rows kept with their own verdict: %s" % dict(CODING_ONLY))
    fam = collections.Counter((r["source_kind"], r["family"]) for r in rows)
    print("rows by source x family: %s" % dict(sorted(fam.items())))
    ids = [r["cell_id"] for r in rows]
    dup = len(ids) - len(set(ids))
    if dup:
        seen = collections.Counter(ids)
        raise SystemExit("REFUSED: %d duplicate cell ids (answers would overwrite each other), e.g. %s"
                         % (dup, [k for k, v in seen.items() if v > 1][:3]))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "menu-cells.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, "menu-answers.jsonl"), "w", encoding="utf-8") as f:
        for cid, ans in answers.items():
            f.write(json.dumps({"cell_id": cid, "answer": ans}, ensure_ascii=False) + "\n")
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "menu_unread_ack.json"), encoding="utf-8") as f:
            ack = json.load(f)
    except (OSError, ValueError):
        ack = {}                  # no acknowledgements file = every gap is flagged
    unread, known = MP.split_acknowledged(UNREAD, ack)
    with open(os.path.join(OUT, "build-manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"rows": len(rows), "duplicate_cell_ids": dup, "answers": len(answers),
                   "unread_deliveries": unread, "unread_acknowledged": known, "sources": sources}, f, indent=1)
    print("total %d rows (%d duplicate ids), %d answers, %d source files -> %s"
          % (len(rows), dup, len(answers), len(sources), OUT))
    print("UNREAD DELIVERIES: %d new%s (%d acknowledged in menu_unread_ack.json)" % (
        len(unread), (" - the desk says done, no out.json found: " + ", ".join(unread)) if unread else "", len(known)))


if __name__ == "__main__":
    sys.exit(main())
