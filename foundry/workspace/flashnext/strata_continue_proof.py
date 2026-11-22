"""Proof that mid-token resume is lossless: restore a cut stream and verify the output matches the uncut baseline.

Usage: strata_continue_proof.py <plan.json> <num_ctx> <ORDER_OUT> [options]

Safety:
- Requires the installed server file to be the vanilla build (refuses otherwise).
- Swaps in the lab's patched server (strata_server_lab.py) for the run, then restores the vanilla server on all exit routes.
- Cleans up generated session files by name on all exit routes.
- Writes results to <ORDER_OUT>/continue.json.

Arms:
- whole_1/2, other_seed: Baselines for engine determinism and seed sensitivity.
- cut_same_engine: Resume within the same engine instance (must equal whole_1).
- no_seed_*: Resume when seed was server-generated (must equal corresponding whole run).
- whole_after_restart, cut_after_restart: Resume across an engine restart (must equal whole_1)."""
import argparse
import hashlib
import http.client
import json
import os
import shutil
import sys
import time
import types
import urllib.error
import urllib.parse
import urllib.request

VANILLA = "9eb13d12dc3ed0f6c94d"
LAB = "strata_server_lab.py"
CUT, CUT2 = "lab-continue-proof-a.bin", "lab-continue-proof-b.bin"
QUESTION = ("Write a careful account of the first thirty records in that log, in your own words: which machines came up, which "
            "technicians worked on them, and anything you notice about the pattern of parts replaced.")
SEED, OTHER = 4242, 777


def post(host, path, body):
    req = urllib.request.Request(host + path, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return {"refused": "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:400])}


def ask(host, messages, n, sampler, seed=None, hang_after=None):
    """One streamed turn -> what the client read. `hang_after`: close the socket after that many pieces, as a real stop does."""
    body = {"model": "strata", "stream": True, "max_tokens": n, "messages": messages, "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "xhigh"}}
    body.update(sampler)
    if seed:
        body["seed"] = seed
    u = urllib.parse.urlsplit(host)
    c = http.client.HTTPConnection(u.hostname, u.port)
    t0, first = time.time(), None
    c.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
    r = c.getresponse()
    out = {"status": r.status, "think": "", "text": "", "pieces": 0, "finish": None, "timings": None, "usage": None, "hung_up": False}
    try:
        while True:
            line = r.readline()
            if not line:
                break
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            d = json.loads(data)
            out["timings"] = d.get("timings") or out["timings"]
            out["usage"] = d.get("usage") or out["usage"]
            for ch in d.get("choices") or []:
                delta = ch.get("delta") or {}
                piece = (delta.get("reasoning_content") or "", delta.get("content") or "")
                if piece[0] or piece[1]:
                    first = first or time.time()
                    out["think"] += piece[0]
                    out["text"] += piece[1]
                    out["pieces"] += 1
                out["finish"] = ch.get("finish_reason") or out["finish"]
            if hang_after and out["pieces"] >= hang_after:
                out["hung_up"] = True
                break
    finally:
        try:
            r.close()
        finally:
            c.close()
    out["wall_s"] = round(time.time() - t0, 2)
    out["first_s"] = round(first - t0, 2) if first else None
    return out


def whole(a):
    return a["think"] + "\x00" + a["text"]


def first_diff(a, b):
    for i, (p, q) in enumerate(zip(a, b)):
        if p != q:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def read_n(a):
    return ((a or {}).get("timings") or {}).get("prompt_n")


def slot(host, action, name, **extra):
    t0 = time.time()
    r = post(host, "/slots/0?action=%s" % action, dict({"filename": name}, **extra))
    r["wall_s"] = round(time.time() - t0, 2)
    return r


def verdict(res):
    """Never a stronger claim than the comparison made. A blank arm is not an equal one."""
    v = {}
    w1, w2, oth = (res.get(k) for k in ("whole_1", "whole_2", "other_seed"))
    if not (w1 and w2 and oth):
        return {"state": "INCOMPLETE"}
    if not whole(w1).strip("\x00"):
        return {"state": "VOID: the reference is blank"}
    v["chars_compared"] = len(whole(w1))
    v["reference_finish"] = w1.get("finish")
    v["engine_repeats_itself"] = first_diff(whole(w1), whole(w2)) is None
    v["a_different_seed_differs"] = first_diff(whole(w1), whole(oth)) is not None
    for name, ref in (("cut_same_engine", "whole_1"), ("no_seed_continued", "no_seed_whole"), ("whole_after_restart", "whole_1"),
                      ("cut_after_restart", "whole_1")):
        if res.get(name) and res.get(ref):
            v[name + "_first_diff"] = first_diff(whole(res[ref]), whole(res[name]))
            v[name + "_chars"] = len(whole(res[name]))
            v[name + "_read"] = read_n(res[name])
    v["armed"] = {k: (res.get(k) or {}).get("lab_continue_armed") for k in ("restore_cut", "restore_no_seed", "restore_after_restart")}
    v["hung_up_at_chars"] = {k: len(whole(res[k])) for k in ("cut_1", "no_seed_cut") if res.get(k)}
    arms = [k for k in ("cut_same_engine", "no_seed_continued", "cut_after_restart") if k + "_first_diff" in v]
    if not v["a_different_seed_differs"]:
        v["state"] = "VOID: a different seed gave the same answer, so this comparison cannot tell answers apart"
    elif not v["engine_repeats_itself"]:
        v["state"] = "VOID for token equality: the engine did not repeat itself with no resume in it"
    elif not all(v["armed"].get(k) for k in v["armed"]) or len(arms) < 3:
        v["state"] = "NOT ARMED or incomplete: see `armed`"
    elif all(v[k + "_first_diff"] is None for k in arms):
        v["state"] = "IDENTICAL: every cut and continued answer equals its uninterrupted reference"
    else:
        v["state"] = "DIFFERS: see *_first_diff (characters into the answer, with the thinking first)"
    return v


def openai_messages(messages):
    """A saved conversation -> the wire the window sends: tool calls with ids and JSON arguments (ollama_client._openai_messages), thinking left out."""
    out, pending = [], []
    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for j, tc in enumerate(m["tool_calls"]):
                fn = (tc or {}).get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {"_cut_off_arguments_chars": len(args)}
                calls.append({"id": "call_%d_%d" % (i, j), "type": "function", "function": {"name": fn.get("name") or "", "arguments": json.dumps(args or {})}})
            pending = [c["id"] for c in calls]
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            c = m.get("content")
            out.append({"role": "tool", "content": c if isinstance(c, str) else json.dumps(c), "tool_call_id": pending.pop(0) if pending else "call_orphan_%d" % i})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def run_arms(host, restart, slot_dir, sampler, msgs, n, cut, save):
    """Every arm. `restart()` stops the engine and starts it. `save(res)` after each. -> res"""
    res = {}

    def step(name, value):
        res[name] = value
        print("[proof] %s: pieces %s, finish %s, read %s, %s s" % (name, value.get("pieces"), value.get("finish"), read_n(value), value.get("wall_s")), flush=True)
        save(res)

    step("whole_1", ask(host, msgs, n, sampler, SEED))
    step("whole_2", ask(host, msgs, n, sampler, SEED))
    step("other_seed", ask(host, msgs, n, sampler, OTHER))

    step("cut_1", ask(host, msgs, n, sampler, SEED, hang_after=cut))
    res["save_cut"] = slot(host, "save", CUT)
    try:
        res["save_cut"]["file_bytes"] = os.path.getsize(os.path.join(slot_dir, CUT))
    except OSError:
        pass
    res["restore_cut"] = slot(host, "restore", CUT, lab_continue=True)
    print("[proof] save %s | restore %s" % (res["save_cut"].get("n_saved") or res["save_cut"], res["restore_cut"].get("lab_continue_armed")), flush=True)
    step("cut_same_engine", ask(host, msgs, n, sampler, SEED))

    step("no_seed_cut", ask(host, msgs, n, sampler, None, hang_after=cut))
    res["save_no_seed"] = slot(host, "save", CUT2)
    try:
        with open(os.path.join(slot_dir, CUT2 + ".labturn"), encoding="utf-8") as f:
            chosen = json.load(f)["seed"]
    except (OSError, ValueError, KeyError) as e:
        chosen = None
        res["no_seed_sidecar"] = repr(e)
    res["chosen_seed"] = chosen
    res["restore_no_seed"] = slot(host, "restore", CUT2, lab_continue=True)
    step("no_seed_continued", ask(host, msgs, n, sampler, None))
    if chosen:
        step("no_seed_whole", ask(host, msgs, n, sampler, chosen))

    restart()
    step("whole_after_restart", ask(host, msgs, n, sampler, SEED))
    restart()
    res["restore_after_restart"] = slot(host, "restore", CUT, lab_continue=True)
    print("[proof] after a restart: restored %s, armed %s" % (res["restore_after_restart"].get("n_restored"), res["restore_after_restart"].get("lab_continue_armed")), flush=True)
    step("cut_after_restart", ask(host, msgs, n, sampler, SEED))
    return res


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("num_ctx", type=int)
    ap.add_argument("out")
    ap.add_argument("--lines", type=int, default=200)
    ap.add_argument("--n", type=int, default=700)
    ap.add_argument("--cut", type=int, default=300)
    ap.add_argument("--messages", default=None)
    args, _extra = ap.parse_known_args()                 # the runner appends the order name; ignore it
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    sys.path.insert(0, os.path.join(root, "bin"))
    import helper_window as HW
    import infer_ollama as IO
    os.makedirs(args.out, exist_ok=True)
    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    slot_dir = plan.get("slot_save_path")
    if not slot_dir:
        raise SystemExit("REFUSED: this plan has no slot_save_path")
    src = os.path.join(plan["strata"], "Strata-0.1.45")
    live = os.path.join(src, "serve", "server.py")
    got = sha(live)
    swap = got.startswith(VANILLA)
    if not swap and got != sha(os.path.join(here, LAB)):
        raise SystemExit("REFUSED: the installed server.py is %s, neither the vanilla %s nor the lab's: nothing was changed" % (got[:20], VANILLA))
    keep = live + ".vanilla-during-proof"
    if swap:
        shutil.copy2(live, keep)
    path = os.path.join(args.out, "continue.json")
    a = types.SimpleNamespace(strata=args.plan, num_ctx=args.num_ctx, out=os.path.join(args.out, "engine.json"), host=None)
    res_box = {"res": {}, "start": time.time()}

    def save(res):
        res_box["res"] = res
        res = dict(res, start=res_box["start"], plan=os.path.basename(args.plan), conversation=res_box.get("conversation"))
        try:
            res["verdict"] = verdict(res)
        except Exception as e:                                           # noqa: BLE001 - a report never costs the measurement
            res["verdict"] = {"state": "the report could not be worked out: %r" % (e,)}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return res

    server = [None]

    def engine():
        HW.free_swap()
        server[0], placement = IO._strata_up(a, frontier=False)
        print("[proof] engine up, load %s s" % placement.get("load_s"), flush=True)

    def restart():
        server[0][0].stop(server[0][1], server[0][2])
        server[0] = None
        engine()

    try:
        if swap:
            shutil.copyfile(os.path.join(here, LAB), live)
        print("[proof] the lab's server is in place, %s" % sha(live)[:20], flush=True)
        engine()
        S = IO.strata_module()
        if args.messages:
            with open(args.messages if os.path.isabs(args.messages) else os.path.join(here, args.messages), encoding="utf-8") as f:
                msgs = openai_messages(json.load(f))
            res_box["conversation"] = {"messages": len(msgs), "chars": sum(len(json.dumps(m)) for m in msgs)}
            print("[proof] a real conversation: %s" % res_box["conversation"], flush=True)
        else:
            msgs = [{"role": "user", "content": "Run %d.\n%s\n\n%s" % (int(res_box["start"]), S.deep_text(args.lines), QUESTION)}]
        sampler = {k: v for k, v in (plan.get("sampler") or {}).items() if k != "seed"}
        run_arms(a.host, restart, slot_dir, sampler, msgs, args.n, args.cut, save)
    finally:
        if server[0] is not None:
            server[0][0].stop(server[0][1], server[0][2])
        if swap:
            shutil.copyfile(keep, live)
            back = sha(live)
            print("[proof] the vanilla server is back: %s %s" % (back[:20], "OK" if back.startswith(VANILLA) else "MISMATCH"), flush=True)
            if back.startswith(VANILLA):
                os.remove(keep)
        for name in (CUT, CUT2, CUT + ".labturn", CUT2 + ".labturn"):
            try:
                os.remove(os.path.join(slot_dir, name))
            except OSError:
                pass
        res = save(res_box["res"])
    print("[proof] verdict: %s" % json.dumps(res["verdict"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
