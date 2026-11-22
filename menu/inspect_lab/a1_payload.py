"""A1 as an AGENTIC payload for Tolkien's `llm` order kind (34-A1 spec, rigby build R3): rigby measures its OWN model,
on rigby, with the same harness, brief, tools, no-progress rule and hidden-test grade as the GPU factories' A1 orders.

    {"kind": "llm", "name": "a1-rigby-<model>-<mode>-<think>.<stamp>", "model": "<gguf in ~/models>",
     "payload": "/ssdshared/tabfoundry-jobs/payloads/a1/a1_payload.py", "tasks": ["affine-cipher", ...],
     "ctx": <tested context>, "reasoning": "on"|"off", "a1_mode": "agentic"|"agentic-hai", "arms": ["rigby"]}

One item = one exercise run as a one-sample Inspect eval (`a1_task.a1(ex_dir=...)`, model `lab/<gguf>` with the
`tolkien` backend), so the order loop keeps its per-item checkpoint, resume, stop slip, brake and tile, and every item
leaves a standard Inspect log. The deployed folder holds this file, a1_harness.py, a1_task.py, lab_provider.py,
control/control-add and polyglot-benchmark/python/exercises/practice (the archived task set).
"""
import hashlib
import json
import os
import shutil
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
A1_DIR = os.environ.setdefault("A1_DIR", HERE)          # deployed: everything beside this file; the repo sets ../a1
for p in (A1_DIR, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
# The rigby lane is one long-lived process and loads this payload once per order: drop the siblings an earlier order
# imported, so every order runs the files now in this folder (HARNESS_SHA256 below describes the module that runs).
for _m in ("a1_harness", "a1_task", "lab_provider", "a1_ollama", "a1_fellowship", "a1_validity", "a1_stop",
           "a1_branch"):
    sys.modules.pop(_m, None)
import a1_harness as H  # noqa: E402
from a1_validity import lead_alone, run_flags, verdict  # noqa: E402,F401  (one derivation with the GPU path; tolkien_llm reads them)
from a1_stop import decide as early_stop  # noqa: E402,F401  (the GPU arms' early stop; tolkien_llm supplies the baseline)

EXERCISES = os.path.join(A1_DIR, "polyglot-benchmark", "python", "exercises", "practice")
CONTROL = os.path.join(HERE, "control", "control-add")
TASKS = {n: {"ex_dir": os.path.join(EXERCISES, n)}
         for n in (sorted(os.listdir(EXERCISES)) if os.path.isdir(EXERCISES) else [])}
CONTROL_TASKS = {"control_add": {"ex_dir": CONTROL}}
MODES = ("agentic-hai", "agentic")
TASK_SET = "Aider-AI/polyglot-benchmark@7e0611e7 (python, 34)"
HARNESS_SHA256 = hashlib.sha256(open(H.__file__, "rb").read()).hexdigest()


def _example(ex_dir):
    cfg = json.load(open(os.path.join(ex_dir, ".meta", "config.json"), encoding="utf-8"))
    return os.path.join(ex_dir, cfg["files"]["example"][0]), cfg["files"]["solution"][0]


def self_test():
    """The grader on every exercise and the control, before any model time: the stub must fail its hidden tests and
    the reference solution must pass them. Returns "" when sound, else what broke."""
    problems, n = [], 0
    for ex in [CONTROL] + [t["ex_dir"] for t in TASKS.values()]:
        name = os.path.basename(ex)
        ws = H._setup_workspace(ex, None)
        try:
            p, t, _ = H.grade(ws, ex)
            if t and p == t:
                problems.append("%s: the stub passes" % name)
            example, solution = _example(ex)
            shutil.copy2(example, os.path.join(ws, solution))
            p, t, out = H.grade(ws, ex)
            if not t or p != t:
                problems.append("%s: reference %s/%s (%s)" % (name, p, t, out.strip().splitlines()[-1:] or ""))
            n += 1
        finally:
            shutil.rmtree(ws, ignore_errors=True)
    if n != len(TASKS) + 1:
        problems.append("graded %d of %d exercises" % (n, len(TASKS) + 1))
    return "; ".join(problems[:6])


class _Either:
    """Stop when the order's slip OR the item's price ceiling fires (tolkien_llm.chat only asks is_set())."""

    def __init__(self, *events):
        self.events = [e for e in events if e is not None]

    def is_set(self):
        return any(e.is_set() for e in self.events)


def _usage(log):
    ins = outs = 0
    for u in ((getattr(log.stats, "model_usage", None) or {}).values()):
        ins += u.input_tokens or 0
        outs += u.output_tokens or 0
    return ins, outs


# rigby's tested maximum A1 context (GT 730 runbook § context: 65,536 loads, 98,304 does not)
RIGBY_A1_TESTED_CTX = 65536


def at_ceiling(caps, ctx):
    """Cap hits that filled the model's WHOLE tested context (the order ran at rigby's tested max): the model ran away,
    scored by a1_validity, not void. Below the tested max the cap was a limit we chose, and stays void."""
    return caps if int(ctx or 0) >= RIGBY_A1_TESTED_CTX else 0


def _cap_hits(sample, turn_cap=0):
    """Model calls that ran to the server's context (stop_reason max_tokens): counted, never hidden (class 13a). A cut
    by the runaway guard's turn cap is not one: the harness counts those (tool_counts runaway_cut)."""
    import a1_task
    return sum(1 for ev in (getattr(sample, "events", None) or [])
               if getattr(ev, "event", None) == "model"
               and getattr(getattr(ev, "output", None), "stop_reason", None) == "max_tokens"
               and not a1_task.is_runaway(ev.output, turn_cap))


def run_agentic(task, tdef, spec, ctx, temperature, seed, stop_event=None, live=None, ceiling_s=None, log_dir=None):
    """One exercise through Inspect against this order's llama-server. Returns the row fields tolkien_llm.run_arm
    records: ok / checks_passed / checks_total / error / finish_reason / wall_s plus the harness row and the log."""
    from inspect_ai import eval as inspect_eval
    from inspect_ai.log import read_eval_log
    from inspect_ai.model import get_model
    import a1_task
    import lab_provider
    mode = spec.get("a1_mode", MODES[0])
    if mode not in MODES:
        raise ValueError("a1_mode must be one of %s" % (MODES,))
    turn_cap = int(spec.get("turn_cap") or 0)       # the runaway guard (a1_harness RUNAWAY GUARD); 0 = none
    if turn_cap >= int(ctx):
        raise ValueError("turn_cap %d leaves no room in ctx %s (compaction keeps one reply's room)" % (turn_cap, ctx))
    priced = threading.Event()
    timer = threading.Timer(ceiling_s, priced.set) if ceiling_s else None
    lab_provider.TOLKIEN_HOOKS.update(stop_event=_Either(stop_event, priced), live=live)
    dlg = None
    if spec.get("team"):           # A1-team on the Fellowship: the lead's delegate() deals tablet pieces (a1_fellowship)
        import a1_fellowship
        dlg = a1_fellowship.FellowshipDelegator(spec["team"], spec["name"], stop_event=_Either(stop_event, priced))
        dlg.exercise = task
    rev = None
    if spec.get("review"):         # A1-review on this layer: a TABLET reads the artifact at the lead's accepted finish
        import a1_fellowship       # (the same shape one layer down - the reviewer is a different, smaller model)
        rev = a1_fellowship.FellowshipDelegator(spec["review"], spec["name"], stop_event=_Either(stop_event, priced),
                                                prefix="a1review-")
        rev.exercise = task
    a1_task.DELEGATE["fn"] = dlg
    a1_task.DELEGATE["brief"] = (H.TEAM_BRIEFS.get(spec["team"].get("mode") or "organic") or "") if dlg else ""
    a1_task.DELEGATE["finish_gate"] = bool(spec.get("finish_gate"))
    a1_task.DELEGATE["review_fn"] = rev
    # per-turn checkpoint beside the order's Inspect logs (Tolkien's persistent results mount): a stopped exercise
    # resumes from its last turn on the requeue, exactly as the fleet's a1_order does
    a1_task.CHECKPOINT["path"] = (os.path.join(log_dir, "checkpoints", "%s.r%s.partial.json" % (task, seed))
                                  if log_dir else None)
    t0 = time.time()
    if timer:
        timer.start()
    try:
        # memoize=False: Inspect would otherwise hand a later order (same model, same args) the provider instance an
        # earlier order built, bound to that order's module and its stop/beat hooks
        model = get_model("lab/" + spec["model"], memoize=False, backend="tolkien", num_ctx=ctx,
                          think=spec["reasoning"])
        logs = inspect_eval(a1_task.a1(mode=mode, ex_dir=tdef["ex_dir"], turn_cap=turn_cap), model=model,
                            log_dir=log_dir, display="none", temperature=temperature, seed=seed)
    finally:
        if timer:
            timer.cancel()
        lab_provider.TOLKIEN_HOOKS.update(stop_event=None, live=None)
        a1_task.DELEGATE.update(fn=None, brief="", finish_gate=False, review_fn=None)
        ck_path, a1_task.CHECKPOINT["path"] = a1_task.CHECKPOINT["path"], None
    log = logs[0]
    if log.location and os.path.exists(log.location):
        log = read_eval_log(log.location)                    # the full log as written, samples and events included
    sample = (log.samples or [None])[0]
    row = {}
    if sample is not None and sample.scores:
        row = dict(next(iter(sample.scores.values())).metadata or {})
    caps = _cap_hits(sample, turn_cap)
    if row.get("ended") == "context_full":   # the request that no longer fit: the context ran out (at_ceiling decides)
        caps += 1
    if stop_event is not None and stop_event.is_set():
        finish = "stopped"
    elif priced.is_set():
        finish = "priced_out"
    elif caps:
        finish = "length"
    else:
        finish = "stop"
    err = None
    if log.status != "success":
        err = "inspect %s: %s" % (log.status, getattr(log.error, "message", "") if log.error else "")
    elif row.get("ended") == "error":
        err = (row.get("reason") or "loop error")[:400]
    if finish in ("stopped", "priced_out"):
        err = "%s after %ss" % (finish, round(time.time() - t0, 1))
    ins, outs = _usage(log)
    out = _row(row, finish, err, caps, ins, outs, mode, log, spec, dlg, rev, t0)
    out["length_at_ceiling"] = at_ceiling(caps, ctx)
    out["turn_cap"] = turn_cap
    out["runaway_cuts"] = int((row.get("tool_counts") or {}).get("runaway_cut") or 0)
    out["compactions"] = row.get("compactions") or []       # each {turn, trigger}: where the conversation was summarized
    out["resumed_turns"] = (sample.metadata or {}).get("resumed_turns") if sample is not None else None
    if finish != "stopped" and ck_path and os.path.exists(ck_path):
        os.remove(ck_path)                 # the exercise ended: its row supersedes the checkpoint (a stop keeps it)
    if finish not in ("stopped", "priced_out"):          # a stop is the operator's, not a void measurement
        out["void"] = verdict(out)
    return out


def _row(row, finish, err, caps, ins, outs, mode, log, spec, dlg, rev, t0):
    return {"ok": bool(row.get("passed")) and finish not in ("stopped", "priced_out"),
            "checks_passed": row.get("tests_passed", 0), "checks_total": row.get("tests_total", 0),
            "error": err, "finish_reason": finish, "wall_s": round(time.time() - t0, 1), "cap_hits": caps,
            "prompt_tokens": ins, "gen_tokens": outs, "a1_mode": mode, "turns": row.get("turns"),
            "ended": row.get("ended"), "graded": row.get("graded"), "tool_counts": row.get("tool_counts"),
            "visible_passed": row.get("visible_passed"), "visible_total": row.get("visible_total"),
            "hidden_passed": row.get("hidden_passed"), "hidden_total": row.get("hidden_total"),
            "failing": row.get("failing"), "inspect_log": log.location, "task_set": TASK_SET,
            "harness_sha256": HARNESS_SHA256, "team": spec.get("team"),
            "delegations": list(dlg.pieces) if dlg else None,
            "delegate_wait_s": round(sum((p.get("done") or p["submitted"]) - p["submitted"] for p in dlg.pieces), 1)
            if dlg else None,
            "finish_gate": bool(spec.get("finish_gate")),
            "review": row.get("review"), "review_spec": spec.get("review"),      # the verdict record; the reviewer
            "review_pieces": list(rev.pieces) if rev else None,
            "review_wait_s": round(sum((p.get("done") or p["submitted"]) - p["submitted"] for p in rev.pieces), 1)
            if rev else None,
            "silent": bool(err) and finish == "stop" and ("TimeoutError" in err or "timed out" in err)}
