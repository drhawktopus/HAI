"""Verifies bit-identical token resume of a Flash-Next conversation across engine restarts.

Usage: strata_resume_probe.py <plan.json> <num_ctx> {ORDER_OUT} [--lines 2400] [--n 200] [--rounds 2]

Protocol:
1. Turn 1: Load long log, save state.
2. Turn 2 (Live): Run immediately on same engine (Reference A).
3. Turn 2 (Restored): Stop engine, start engine, restore state, run at temp 0 and serving temp.
4. Turn 2 (No Restore): Stop engine, start engine, run WITHOUT restore (control for state loss).

Note: The 'Live' reference is essential to distinguish restart-specific failures from general engine non-determinism. Writes {ORDER_OUT}\\resume.json and cleans up the session file."""
import argparse
import json
import os
import sys
import time
import types
import urllib.error
import urllib.request

SLOT = "strata-resume-probe.bin"
QUESTION_1 = "What is the vault code for the east wing? Answer with the code only."
QUESTION_2 = ("Now write a careful account of the first thirty records in that log, in your own words: which machines came up, "
              "which technicians worked on them, and anything you notice about the pattern of parts replaced.")
STEPS_PER_ROUND = 2


def post(url, body, timeout=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def ask(host, messages, n, temperature, seed):
    """One turn, thinking off (a fixed answer to compare, not a reasoning test). No request clock (13a)."""
    body = {"model": "strata", "stream": False, "max_tokens": n, "temperature": temperature, "seed": seed,
            "messages": messages, "chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.time()
    d = post(host + "/v1/chat/completions", body)
    t = d.get("timings") or {}
    choice = (d.get("choices") or [{}])[0]
    return {"text": (choice.get("message") or {}).get("content") or "", "finish": choice.get("finish_reason"),
            "wall_s": round(time.time() - t0, 2), "read_n": t.get("prompt_n"), "read_tok_s": t.get("prompt_per_second"),
            "read_ms": t.get("prompt_ms"), "write_n": t.get("predicted_n"), "write_tok_s": t.get("predicted_per_second"),
            "prompt_tokens": (d.get("usage") or {}).get("prompt_tokens")}


def slot(host, action):
    """-> the engine's answer + wall time, or {"refused": why}: a refusal is a result here, never a crash."""
    t0 = time.time()
    try:
        r = post(host + "/slots/0?action=%s" % action, {"filename": SLOT})
        r["wall_s"] = round(time.time() - t0, 2)
        return r
    except urllib.error.HTTPError as e:
        return {"refused": "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:400]), "wall_s": round(time.time() - t0, 2)}
    except Exception as e:                                               # noqa: BLE001 - reported, the next arm still runs
        return {"refused": "%s: %s" % (type(e).__name__, str(e)[:300]), "wall_s": round(time.time() - t0, 2)}


def first_diff(a, b):
    """-> None when equal, else the index of the first differing character."""
    for i, (p, q) in enumerate(zip(a, b)):
        if p != q:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def verdict(res):
    """What was actually measured, in words. Never a stronger claim than the comparison made."""
    refs, rounds = res.get("references") or {}, res.get("rounds") or []
    out = {}
    if not refs or not rounds:
        return {"state": "INCOMPLETE"}
    usable = [r for r in rounds if not (r.get("restore") or {}).get("refused") and "t0" in r and "t6" in r]   # finished rounds
    out["restores_refused"] = sum(1 for r in rounds if (r.get("restore") or {}).get("refused"))
    if not usable:
        out["state"] = ("REFUSED: the engine would not restore its own session file after a restart" if out["restores_refused"]
                        else "INCOMPLETE")
        return out
    out["read_after_restore"] = [r["t0"].get("read_n") for r in usable]
    out["read_with_no_restore"] = ((res.get("no_restore") or {}).get("t0") or {}).get("read_n")
    out["same_engine_repeats_itself"] = first_diff(res["live"]["text"], refs["t0"]["text"]) is None
    for name in ("t0", "t6"):
        out["across_restart_" + name] = [first_diff(refs[name]["text"], r[name]["text"]) for r in usable]
    same = all(d is None for name in ("t0", "t6") for d in out["across_restart_" + name])
    if same:
        out["state"] = "IDENTICAL: every restored answer equals its reference at both temperatures"
    elif not out["same_engine_repeats_itself"]:
        out["state"] = "VOID for token equality: the engine did not repeat itself even with no restart"
    else:
        out["state"] = "DIFFERS across a restart (first differing character in across_restart_*); it repeats itself without one"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("num_ctx", type=int)
    ap.add_argument("out")
    ap.add_argument("--lines", type=int, default=2400)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--rounds", type=int, default=2)
    args, _extra = ap.parse_known_args()                 # the runner appends the order name; ignore it
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(root, "bin"))
    import helper_window as HW
    import infer_ollama as IO
    os.makedirs(args.out, exist_ok=True)
    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    slot_dir = plan.get("slot_save_path")
    if not slot_dir:
        raise SystemExit("REFUSED: this plan has no slot_save_path, so the engine has no session files to test")
    path = os.path.join(args.out, "resume.json")
    a = types.SimpleNamespace(strata=args.plan, num_ctx=args.num_ctx, out=os.path.join(args.out, "engine.json"), host=None)
    res = {"plan": os.path.basename(args.plan), "lines": args.lines, "n": args.n, "rounds_asked": args.rounds, "start": time.time()}
    t_serving, seed = float((plan.get("sampler") or {}).get("temperature", 0.6)), int((plan.get("sampler") or {}).get("seed", 1234))
    total, done = 4 + STEPS_PER_ROUND * args.rounds + 2, [0]

    def save():
        try:
            res["verdict"] = verdict(res)
        except Exception as e:                                           # noqa: BLE001 - a report never costs the measurement
            res["verdict"] = {"state": "the report could not be worked out: %r" % (e,)}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    def step(what):
        done[0] += 1
        print("[probe] %d/%d %s" % (done[0], total, what), flush=True)
        save()

    def engine():
        HW.free_swap()                 # what a chat window does before its own engine starts (helper_window.main)
        server, placement = IO._strata_up(a, frontier=False)
        res.setdefault("placements", []).append(placement)
        return server

    S = IO.strata_module()
    salt = "Run %d." % int(res["start"])
    t1 = [{"role": "user", "content": salt + "\n" + S.deep_text(args.lines) + "\n\n" + QUESTION_1}]
    server = None
    try:
        server = engine()
        res["t1"] = ask(a.host, t1, 64, 0.0, seed)
        step("turn 1 read %s tokens" % res["t1"].get("read_n"))
        t2 = t1 + [{"role": "assistant", "content": res["t1"]["text"]}, {"role": "user", "content": QUESTION_2}]
        res["save"] = slot(a.host, "save")
        step("saved %s tokens, %s bytes" % (res["save"].get("n_saved"), res["save"].get("n_written")))
        if res["save"].get("refused"):
            raise SystemExit("the engine would not save: %s" % res["save"]["refused"])
        res["live"] = ask(a.host, t2, args.n, 0.0, seed)
        step("turn 2 straight on")
        refs = {}
        res["restore_same_engine"] = slot(a.host, "restore")
        refs["t0"] = ask(a.host, t2, args.n, 0.0, seed)
        slot(a.host, "restore")
        refs["t6"] = ask(a.host, t2, args.n, t_serving, seed)
        res["references"] = refs
        step("references from a restore in the same engine")
        server[0].stop(server[1], server[2])
        server = None
        res["rounds"] = []
        for i in range(args.rounds):
            server = engine()
            row = {"restore": slot(a.host, "restore")}
            res["rounds"].append(row)
            row["t0"] = ask(a.host, t2, args.n, 0.0, seed)
            step("round %d restored, read %s tokens" % (i + 1, row["t0"].get("read_n")))
            row["restore6"] = slot(a.host, "restore")
            row["t6"] = ask(a.host, t2, args.n, t_serving, seed)
            step("round %d at the serving temperature" % (i + 1))
            server[0].stop(server[1], server[2])
            server = None
        server = engine()
        nr = {"t0": ask(a.host, t2, args.n, 0.0, seed)}
        res["no_restore"] = nr
        step("no restore: read %s tokens" % nr["t0"].get("read_n"))
        nr["t6"] = ask(a.host, t2, args.n, t_serving, seed)
        step("no restore at the serving temperature")
    finally:
        if server is not None:
            server[0].stop(server[1], server[2])
        try:
            os.remove(os.path.join(slot_dir, SLOT))
        except OSError:
            pass
        res["end"] = time.time()
        save()
    print("[probe] verdict: %s" % json.dumps(res["verdict"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
