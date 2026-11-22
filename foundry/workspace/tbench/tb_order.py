"""Terminal-Bench arm as a FACTORY ORDER ([[34-Terminal-Bench-Lead-Level-Grid-Plan]] § "How it FITS the lab").

    payload: {FOUNDRY_ROOT}\\workspace\\tbench\\tb_order.py   args: {ORDER_OUT} <plan.json>
    plan = {"label": "...", "tasks": ["fix-git", ...], "port": 8765,
            "lead": {"helper": "bighonker", "helper_model": "qwen3:8b", "helper_ctx": 17408,
                     "helper_think": false, "helper_temperature": 0.7}}

Runs on Softserve (the lab's hub: it talks to Sundae, where Harbor and the task containers live, and to the lead's
box). 1) opens the lead's MODEL WINDOW order through the desk (a1_delegate.WindowDelegator, tied to THIS order's desk
record, so it closes when this order ends by any route); 2) snapshots its own deployed tb_run.sh + tb_relay.py into the
run dir on the share (Sundae executes exactly those bytes; the run dir records which code ran); 3) drives Sundae over
ssh and waits on that process (status, never a clock); 4) closes the window in a finally; 5) prices the run into
out.json: reward per task from Harbor's result.json, every model call's done_reason / tokens / joules from the window
rows, the relay's no-progress ends, and calls at the lead's tested context (13a: counted before any score is read).
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "a1"))           # workspace\a1: the window delegator
_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, _ROOT)
import foundry_paths as _P             # this workspace's ONE settings reader
SHARE_UNC = _P.setting("SSD_SHARED_UNC", "")      # the shared SSD staging root
SHARE_SUNDAE = _P.setting("SSD_SHARE_MOUNT", "")  # the same share as the head's container sees it
HEAD = _P.setting("HAI_BOX_NAME", "")              # the head's ssh name: the benchmark runs in its compose stack
WIN_UNC = SHARE_UNC + r"\mothership-build\workspace\a1"
RUNS_UNC = SHARE_UNC + r"\tabfoundry-jobs\campaigns\tbench"
NEAR_CTX = 0.95          # a call whose prompt filled >= 95 % of the lead's tested context is AT its ceiling


def sundae_path(unc):
    return unc.replace(SHARE_UNC, SHARE_SUNDAE).replace("\\", "/")


def own_desk_id(out):
    """This order's desk record id from its own spec in the runner's queue (a1_delegate.desk_id_for, one rule)."""
    import a1_delegate
    return a1_delegate.desk_id_for(out, os.path.dirname(os.path.dirname(HERE)))


def token():
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "bin"))
        import p20_foundry
        return p20_foundry._worker_token()
    except Exception:                                                       # noqa: BLE001
        return None


def price(run_dir_unc, items_dir_unc, ctx, window_out=None):
    """out.json body from what the run left on the share. Every count is stated; zero trials read is an error.
    An AMD window meters its rows only at close (telemetry log), so their joules come from the window's DELIVERED
    out.json (`window_out`), matched by item id."""
    results = sorted(glob.glob(os.path.join(run_dir_unc, "jobs", "*", "result.json")), key=os.path.getmtime)
    assert results, "no Harbor result.json in %s - the run did not reach grading" % run_dir_unc
    res = json.load(open(results[-1], encoding="utf-8"))
    tasks = {}
    for ev in (res.get("stats", {}).get("evals") or {}).values():
        for reward, trials in (ev.get("reward_stats", {}).get("reward") or {}).items():
            for t in trials:
                tasks[t.split("__")[0]] = {"reward": float(reward)}
        for exc, trials in (ev.get("exception_stats") or {}).items():
            for t in trials:
                tasks.setdefault(t.split("__")[0], {})["exception"] = exc
    # B8: a trial whose helpers fetched its own tests / solution - or whose fetch could not be audited - is VOID: never
    # solved, never graded (fetch_audit.py, run on Sundae beside the dataset)
    ap = os.path.join(run_dir_unc, "fetch_audit.json")
    if os.path.exists(ap):
        for trial, e in json.load(open(ap, encoding="utf-8")).items():
            if e.get("leak") or e.get("error"):
                tasks.setdefault(trial.split("__")[0], {})["void"] = ("fetched its own key %s" % e.get("urls")
                                                                       if e.get("leak") else e["error"])
    rows = []
    for f in sorted(glob.glob(os.path.join(items_dir_unc, "*.out.json"))):
        try:
            rows.append(json.load(open(f, encoding="utf-8")))
        except (OSError, ValueError):
            pass
    relay = []
    rl = os.path.join(run_dir_unc, "relay.jsonl")
    if os.path.exists(rl):
        relay = [json.loads(l) for l in open(rl, encoding="utf-8") if l.strip()]
    if window_out and any(r.get("gpu_j") is None for r in rows) and os.path.exists(window_out):
        closed = {str(r.get("id")): r.get("gpu_j") for r in json.load(open(window_out, encoding="utf-8")).get("rows") or []}
        rows = [dict(r, gpu_j=closed.get(str(r.get("id")))) if r.get("gpu_j") is None else r for r in rows]
    reasons = {}
    for r in rows:
        reasons[str(r.get("done_reason"))] = reasons.get(str(r.get("done_reason")), 0) + 1
    metered = [r["gpu_j"] for r in rows if r.get("gpu_j") is not None]
    return {"tasks": tasks, "solved": sum(1 for t in tasks.values() if t.get("reward") == 1.0 and "void" not in t),
            "graded": sum(1 for t in tasks.values() if "reward" in t and "void" not in t),
            "void": sum(1 for t in tasks.values() if "void" in t),
            "exceptions": sum(1 for t in tasks.values() if "exception" in t),
            "model_calls": len(rows), "done_reasons": reasons,
            "calls_at_ctx": sum(1 for r in rows if (r.get("prompt_eval_count") or 0) >= NEAR_CTX * ctx),
            # trials, not log lines: one end is logged again for Terminus's confirmation turn
            "no_progress_ends": sum(1 for i, r in enumerate(relay) if r.get("error") == "ended_no_progress"
                                    and not (i and relay[i - 1].get("error") == "ended_no_progress")),
            "window_closed_calls": sum(1 for r in relay if r.get("error") == "window_closed"),
            "answers_from_thinking": sum(1 for r in relay if r.get("answer_from_thinking")),
            "context_full_calls": sum(1 for r in relay if r.get("error") == "context_full"),
            "model_wall_s": round(sum(r.get("wall_s") or 0 for r in rows), 1),
            "gpu_j": round(sum(metered)) if metered and len(metered) == len(rows) else None,
            "gpu_j_rows": len(metered), "harbor_result": results[-1]}


def run_cmd(run_dir, items_dir, plan, lead, helper_items=None):
    """The Sundae command: tb_run.sh <items> <run dir> <port> <lead ctx> <tasks...> (the ctx becomes Terminus's limit);
    an ERRAND arm also passes its helper window's items dir, port and ctx (tb_run.sh then runs LabTerminus)."""
    env = ""
    if helper_items:
        env = "HELPER_ITEMS='%s' HELPER_PORT=%d HELPER_CTX=%d " % (
            sundae_path(helper_items), int(plan.get("port", 8765)) + 1, int(plan["helper"]["helper_ctx"]))
        if plan.get("errand_mode") == "commands":            # B8: RUN in a private copy + FETCH; unset = look-only
            env += "ERRAND_MODE=commands "
    return "%ssh '%s/tb_run.sh' '%s' '%s' %d %d %s" % (
        env, sundae_path(run_dir), sundae_path(items_dir), sundae_path(run_dir), int(plan.get("port", 8765)),
        int(lead["helper_ctx"]), " ".join(plan["tasks"]))


def errand_records(run_dir_unc):
    """Every errand the lead sent (LabTerminus writes <trial>/agent/errands.json), counted by status."""
    recs = []
    for f in glob.glob(os.path.join(run_dir_unc, "jobs", "*", "*__*", "agent", "errands.json")):
        try:
            recs += json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            pass
    by = {}
    for r in recs:
        by[r.get("status")] = by.get(r.get("status"), 0) + 1
    return {"errands": len(recs), "errand_status": by, "errand_looks": sum(r.get("looks") or 0 for r in recs),
            "errand_runs": sum(r.get("runs") or 0 for r in recs),
            "errand_fetches": sum(len(r.get("fetches") or []) for r in recs)}


def main(argv):
    out, plan = argv[0], json.load(open(argv[1], encoding="utf-8"))
    os.makedirs(out, exist_ok=True)
    import a1_delegate as D
    name = os.path.basename(out.rstrip("\\/"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(RUNS_UNC, "%s.%s" % (plan["label"], stamp))
    os.makedirs(run_dir, exist_ok=True)
    for f in ("tb_run.sh", "tb_relay.py", "lab_terminus.py", "lab_errands.py", "fetch_audit.py"):   # deployed bytes
        shutil.copy2(os.path.join(HERE, f), os.path.join(run_dir, f))
    lead = dict(plan["lead"], window=True)
    owner = own_desk_id(out)
    if not owner:
        # a window with no lead order cannot see this order end: a stop slip would leave it, the relay and Harbor
        # running (10-11, the 9B's first arm) - refuse rather than open one
        with open(os.path.join(out, "out.json"), "w", encoding="utf-8") as f:
            json.dump({"label": plan["label"], "error": "no desk id for %s in the queue - window not opened" % name}, f)
        print("[tb] REFUSED: no desk id for %s - a window without an owner would outlive a stop" % name, flush=True)
        return 1
    dlg = D.WindowDelegator(lead, name, WIN_UNC, token, lambda: None, lead_order_id=owner,
                            prefix="tbwin-", log=lambda m: print("[tb] " + m, flush=True))
    hdlg = None
    if plan.get("helper"):                       # ERRAND arm: the helper's own window, owned by this order too
        hdlg = D.WindowDelegator(dict(plan["helper"], window=True), name, WIN_UNC, token, lambda: None,
                                 lead_order_id=owner, prefix="tbhelp-", log=lambda m: print("[tb] " + m, flush=True))
    rc, body = 1, {"label": plan["label"], "lead": plan["lead"], "helper": plan.get("helper"),
                   "tasks_planned": plan["tasks"], "run_dir": run_dir}
    try:
        w = dlg.open()
        body["window"] = w
        if w.get("status") != "placed":
            body["error"] = "model window not placed: %s" % w.get("error")
            return 1
        hitems = None
        if hdlg is not None:
            hw = hdlg.open()
            body["helper_window"] = hw
            if hw.get("status") != "placed":
                body["error"] = "helper window not placed: %s" % hw.get("error")
                return 1
            hitems = hw["items_dir"]
        if not HEAD:
            body["error"] = "HAI_BOX_NAME unset: no head to run the benchmark on"
            return 1
        cmd = ["ssh", "-o", "ConnectTimeout=20", HEAD, run_cmd(run_dir, w["items_dir"], plan, lead, hitems)]
        print("[tb] %s on %s: %d task(s)" % (plan["label"], lead["helper"], len(plan["tasks"])), flush=True)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        for line in p.stdout:                                      # the head's own words, to the order's log
            print("[%s] %s" % (HEAD, line.rstrip()), flush=True)
        rc = p.wait()
        body["harbor_rc"] = rc
    finally:
        try:
            body["window_close"] = dlg.close()
        except Exception as e:                                     # noqa: BLE001 - cleanup must never raise
            body["window_close_error"] = repr(e)[:200]
        if hdlg is not None:
            try:
                body["helper_window_close"] = hdlg.close()
            except Exception as e:                                 # noqa: BLE001 - cleanup must never raise
                body["helper_window_close_error"] = repr(e)[:200]
            try:
                hrows = [json.load(open(f, encoding="utf-8")) for f in glob.glob(os.path.join(
                    (body.get("helper_window") or {}).get("items_dir", "-"), "*.out.json"))]
                hstep = (body.get("helper_window_close") or {}).get("step")
                hout = os.path.join(WIN_UNC, "delegated", hstep, "out.json") if hstep else None
                if hout and os.path.exists(hout):                 # AMD meters at close: the delivered rows carry joules
                    hrows = json.load(open(hout, encoding="utf-8")).get("rows") or hrows
                metered = [r["gpu_j"] for r in hrows if r.get("gpu_j") is not None]
                body["helper_price"] = {"calls": len(hrows), "wall_s": round(sum(r.get("wall_s") or 0 for r in hrows), 1),
                                        "gpu_j": round(sum(metered)) if metered and len(metered) == len(hrows) else None}
                body.update(errand_records(run_dir))
            except Exception as e:                                 # noqa: BLE001
                body["helper_price_error"] = repr(e)[:200]
        try:
            if body.get("window", {}).get("items_dir"):
                step = (body.get("window_close") or {}).get("step")
                wout = os.path.join(WIN_UNC, "delegated", step, "out.json") if step else None
                body.update(price(run_dir, body["window"]["items_dir"], int(lead["helper_ctx"]), wout))
                if body.get("gpu_j") is None:
                    body["gpu_j_from"] = wout             # not delivered yet: re-price from here, never read as 0
        except Exception as e:                                     # noqa: BLE001
            body["price_error"] = repr(e)[:300]
        with open(os.path.join(out, "out.json"), "w", encoding="utf-8") as f:
            json.dump(body, f, indent=1)
        print("[tb] out.json written: solved %s of %s graded, %s exception(s), calls at ctx %s, no-progress ends %s" % (
            body.get("solved"), body.get("graded"), body.get("exceptions"), body.get("calls_at_ctx"),
            body.get("no_progress_ends")), flush=True)
    return 0 if rc == 0 and "price_error" not in body else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
