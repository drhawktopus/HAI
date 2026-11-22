"""Find the single Vulkan engine switch that restores repeatable outputs on this box.

engine_switch_search.py <model> <num_ctx> {ORDER_OUT} [--n 160] [--switches A,B,...] [--extra NAME=VALUE ...]

Starts a dedicated `ollama serve` instance on a spare port using the lab's launcher configuration to test GGML_VK_*
switches in isolation. It verifies repeatability by issuing three identical requests to the same loaded engine and
confirming the responses match (A2 == A1 and A3 == A1). The search bisects the active switch set to identify the
minimal configuration required for stability, recording latency for each arm. The lab's production ollama instance
is not restarted or modified. Results are written to {ORDER_OUT}\\switch_search.json."""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_resume_probe as E  # noqa: E402
import engine_history_probe as H  # noqa: E402

LAUNCHER = r"C:\ProgramData\HAI\start-ollama.ps1"
PORT = 11500
SWITCHES = ["GGML_VK_FA_SPARSE_DISABLE", "GGML_VK_DISABLE_FUSION", "GGML_VK_DISABLE_GRAPH_OPTIMIZE",
            "GGML_VK_DISABLE_DESCRIPTOR_REUSE", "GGML_VK_DISABLE_COOPMAT", "GGML_VK_DISABLE_COOPMAT2",
            "GGML_VK_DISABLE_MULTI_ADD", "GGML_VK_DISABLE_ASYNC", "GGML_VK_SERIALIZE_SUBMISSIONS"]
EXTRAS = [{"OLLAMA_KV_CACHE_TYPE": "f16"}, {"OLLAMA_FLASH_ATTENTION": "0"}]
LAB_OLLAMA = "http://127.0.0.1:11434"


def launcher_settings(text):
    """(exe, {env}) from the lab's launcher: every `$env:NAME = 'value'` line and the serve exe it starts."""
    env = dict(re.findall(r"(?m)^\s*\$env:([A-Z0-9_]+)\s*=\s*'([^']*)'", text))
    exe = re.search(r"-FilePath\s+'([^']+ollama\.exe)'", text)
    if not exe:
        raise SystemExit("the launcher names no ollama.exe: %s" % LAUNCHER)
    return exe.group(1), env


def serve(exe, base_env, arm_env, log_path):
    env = dict(os.environ, **base_env)
    env.update(arm_env)
    env["OLLAMA_HOST"] = "127.0.0.1:%d" % PORT
    log = open(log_path, "wb")
    p = subprocess.Popen([exe, "serve"], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/version" % PORT, timeout=3).read()
            return p, log
        except Exception:                                                          # noqa: BLE001
            if p.poll() is not None:
                break
            time.sleep(1)
    stop(p, log)
    raise SystemExit("this order's own ollama serve did not answer on %d (see %s)" % (PORT, log_path))


def stop(p, log):
    """End THIS order's serve and the runner it started, by the pid this order spawned. Never the lab's."""
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=60)
    try:
        p.wait(timeout=30)
    except Exception:                                                              # noqa: BLE001
        pass
    log.close()


def trial(a, exe, base_env, arm_env, tag, rep, plen=None, draft=None):
    """`plen`: the request's prompt cut or stretched to exactly that many tokens (the coopmat paths work in tiles of 16,
    so a batch that does not fill its last tile is its own case). `draft`: 0 turns the runner's drafting off for this
    arm, so every generated token is a batch of one and only the PROMPT is processed as a batch."""
    log_path = os.path.join(a.out, "serve-%s.log" % tag)
    p, log = serve(exe, base_env, arm_env, log_path)
    row = {"arm": tag, "env": arm_env, "prompt_len": plen, "draft": draft}
    try:
        t0 = time.time()
        port, lines = E.load(a.model, a.num_ctx, draft)
        row["load_s"] = round(time.time() - t0, 1)
        row["runner"] = (lines[0] if lines else "")[-900:]
        row["drafting_flags"] = "--spec-type" in (lines[0] if lines else "")
        P = E.post("http://127.0.0.1:%d/tokenize" % port, {"content": H.P_TEXT, "add_special": True})["tokens"]
        Q = E.post("http://127.0.0.1:%d/tokenize" % port, {"content": H.Q_TEXT, "add_special": True})["tokens"]
        if plen:
            more = E.post("http://127.0.0.1:%d/tokenize" % port, {"content": " Describe the sea, the lamp room, the keeper's "
                          "dog, the storm that week, the handwriting, the stamp, the answer he wrote and what the tide "
                          "brought back the next spring, in that order.", "add_special": False})["tokens"]
            P = (P + more * 4)[:plen]
            assert len(P) == plen, "the prompt could not be made %d tokens long" % plen
        A1 = H.ask(port, P, a)
        A2 = H.ask(port, P, a)
        H.ask(port, Q, a)
        A3 = H.ask(port, P, a)
        row["repeat_right_away"] = H.versus(A1, A2)
        row["after_a_different_answer"] = H.versus(A1, A3)
        row["repeats"] = bool(row["repeat_right_away"]["same"] and row["after_a_different_answer"]["same"])
        row["tokens"] = len(A1["tokens"])
    finally:
        stop(p, log)
    try:                       # is the arm on the card, and how fast: read from this serve's own log, never assumed
        text = open(log_path, encoding="utf-8", errors="replace").read()
        row["library"] = sorted(set(re.findall(r"library=(\w+)", text)))
        row["offloaded"] = (re.findall(r"offloaded (\d+/\d+) layers", text) or [None])[-1]
        speeds = [float(x) for x in re.findall(r"eval time =.*?([0-9.]+) tokens per second", text)]
        row["tok_s"] = round(sorted(speeds)[len(speeds) // 2], 1) if speeds else None
    except OSError:
        pass
    rep["arms"].append(row)
    with open(os.path.join(a.out, "switch_search.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)          # written after every arm: a search that dies keeps what it measured
    print(tag, "REPEATS" if row["repeats"] else "does not repeat",
          {k: row[k]["first_different_token"] for k in ("repeat_right_away", "after_a_different_answer")},
          "tok/s", row.get("tok_s"), "library", row.get("library"), "offloaded", row.get("offloaded"), flush=True)
    return row["repeats"]


def long_read(a, port):
    """How fast this engine takes in a long prompt and then writes: the price of a switch on real work. A fixed prose
    of numbered, different sentences (about 20,000 tokens), read whole with the prompt cache off."""
    text = "\n".join("Entry %d: the survey party logged %d samples at station %d and moved %d metres north before dusk."
                     % (i, (i * 37) % 911, (i * 13) % 97, (i * 53) % 409) for i in range(1, 801))
    ids = E.post("http://127.0.0.1:%d/tokenize" % port, {"content": text + "\n\nSummarise the pattern in two sentences.",
                                                        "add_special": True})["tokens"]
    E.slot(port, "erase")
    r = E.post("http://127.0.0.1:%d/completion" % port,
               {"prompt": ids, "n_predict": 96, "temperature": a.temperature, "seed": a.seed, "id_slot": 0,
                "cache_prompt": False, "return_tokens": True, "stream": False})
    t = r.get("timings") or {}
    return {"prompt_tokens": len(ids), "read_tok_s": t.get("prompt_per_second"), "write_tok_s": t.get("predicted_per_second"),
            "prompt_n": t.get("prompt_n"), "cache_n": t.get("cache_n")}


def verify(a, exe, base_env, switch, rep):
    """The switch against the REAL thing: the lab's whole resume proof (every arm, the continue after a different answer
    included) and a long read, once without the switch and once with it. Same engine, same order, one variable."""
    rep["verify"] = switch
    for tag, env in (("without", {}), ("with", {switch: "1"})):
        log_path = os.path.join(a.out, "serve-verify-%s.log" % tag)
        p, log = serve(exe, base_env, env, log_path)
        row = {"arm": tag, "env": env}
        try:
            out = os.path.join(a.out, "resume-" + tag)
            try:
                E.main([a.model, str(a.num_ctx), out, "--continue", "--hang-up", "--fresh-load"])
            except SystemExit as exc:
                row["resume_exit"] = str(exc.code)[:200]
            try:
                r = json.load(open(os.path.join(out, "resume.json"), encoding="utf-8"))
                row["resume_verdict"], row["raw_resume_verdict"] = r.get("verdict"), r.get("raw_resume_verdict")
                row["lab_continue"] = {k: (v.get("same") if isinstance(v, dict) and "same" in v else
                                           (v.get("first_diff") if isinstance(v, dict) else v))
                                       for k, v in (r.get("lab_continue") or {}).items()}
            except (OSError, ValueError) as exc:
                row["resume_error"] = repr(exc)[:200]
            port, _lines = E.load(a.model, a.num_ctx)
            row["long_read"] = [long_read(a, port), long_read(a, port)]     # twice: the second is the warm one
        finally:
            stop(p, log)
        try:
            text = open(log_path, encoding="utf-8", errors="replace").read()
            row["library"], row["offloaded"] = sorted(set(re.findall(r"library=(\w+)", text))), (re.findall(r"offloaded (\d+/\d+) layers", text) or [None])[-1]
        except OSError:
            pass
        rep["arms"].append(row)
        with open(os.path.join(a.out, "switch_search.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print(tag, row.get("resume_verdict"), row.get("lab_continue"), row.get("long_read"), flush=True)
    w, wo = rep["arms"][1], rep["arms"][0]
    rep["verdict"] = "VERIFY %s: resume proof WITH = %s, WITHOUT = %s" % (switch, w.get("resume_verdict"), wo.get("resume_verdict"))
    with open(os.path.join(a.out, "switch_search.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    print("VERDICT:", rep["verdict"])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("num_ctx", type=int)
    ap.add_argument("out")
    ap.add_argument("--n", type=int, default=160)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--probs", type=int, default=8)
    ap.add_argument("--switches", default=",".join(SWITCHES))
    ap.add_argument("--verify", default=None, help="one switch: run the lab's whole resume proof and a long read WITHOUT it and WITH it")
    ap.add_argument("--arms", default=None, help='named arms, one fresh serve each: "NAME=VALUE,NAME=VALUE|NAME=VALUE|"')
    ap.add_argument("--lengths", default=None, help="prompt lengths in tokens, e.g. 29,31,32,48,16,33: one fresh serve each, no switch, drafting off")
    a, _extra = ap.parse_known_args(argv)        # the foundry appends the order name
    os.makedirs(a.out, exist_ok=True)
    exe, base_env = launcher_settings(open(LAUNCHER, encoding="utf-8", errors="replace").read())
    rep = {"box": socket.gethostname(), "model": a.model, "num_ctx": a.num_ctx, "exe": exe, "launcher_env": base_env,
           "arms": [], "verdict": None}
    E.OLLAMA = LAB_OLLAMA
    E.evict(a.model)                              # the lab's serve lets the model go: the card is this order's
    E.OLLAMA = "http://127.0.0.1:%d" % PORT       # from here every load and evict is this order's own serve
    if a.verify:
        return verify(a, exe, base_env, a.verify, rep)
    if a.arms:
        # Named arms, one fresh serve each: "NAME=VALUE,NAME=VALUE|NAME=VALUE|..." (an empty arm is the baseline)
        for i, spec in enumerate(a.arms.split("|")):
            env = dict(x.split("=", 1) for x in spec.split(",") if x)
            trial(a, exe, base_env, env, "arm%d" % i, rep)
        rep["verdict"] = "ARMS: " + "; ".join("%s -> %s" % (json.dumps(r["env"]), "REPEATS" if r["repeats"] else "does not repeat")
                                              for r in rep["arms"])
        with open(os.path.join(a.out, "switch_search.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print("VERDICT:", rep["verdict"])
        return 0
    if a.lengths:
        # Is it the batch's last, unfilled tile? No switch in any arm; drafting off, so only the prompt is a batch.
        for L in [int(x) for x in a.lengths.split(",")]:
            trial(a, exe, base_env, {}, "len%d" % L, rep, plen=L, draft=0)
        full = [r for r in rep["arms"] if r["prompt_len"] % 16 == 0]
        part = [r for r in rep["arms"] if r["prompt_len"] % 16 != 0]
        rep["verdict"] = "LENGTHS: full tiles repeat %d of %d; unfilled tiles repeat %d of %d" % (
            sum(r["repeats"] for r in full), len(full), sum(r["repeats"] for r in part), len(part))
        with open(os.path.join(a.out, "switch_search.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print("VERDICT:", rep["verdict"])
        return 0
    cand = [s for s in a.switches.split(",") if s]
    on = lambda names: {n: "1" for n in names}  # noqa: E731

    if trial(a, exe, base_env, {}, "none", rep):
        rep["verdict"] = "VOID - with no switch the engine repeated itself here, so this harness is not measuring what production does"
    elif not trial(a, exe, base_env, on(cand), "all", rep):
        found = [json.dumps(x) for i, x in enumerate(EXTRAS) if trial(a, exe, base_env, x, "extra%d" % i, rep)]
        rep["verdict"] = ("NONE of the %d switches; repeats with: %s" % (len(cand), ", ".join(found)) if found else
                          "NONE of the %d switches, nor the KV cache type, nor flash attention" % len(cand))
    else:
        n = 0
        while len(cand) > 1:
            n += 1
            half, rest = cand[:len(cand) // 2], cand[len(cand) // 2:]
            if trial(a, exe, base_env, on(half), "half%d-a" % n, rep):
                cand = half
            elif trial(a, exe, base_env, on(rest), "half%d-b" % n, rep):
                cand = rest
            else:
                rep["verdict"] = "needs MORE THAN ONE of: %s (neither half alone repeats)" % ", ".join(cand)
                break
        if rep["verdict"] is None:
            alone = next((r for r in rep["arms"] if set(r["env"]) == set(cand)), None) or {}
            base = rep["arms"][0]
            rep["verdict"] = "REPEATS ITSELF with %s alone: %s tok/s against %s without it" % (cand[0], alone.get("tok_s"), base.get("tok_s"))
            rep["switch"] = cand[0]
    with open(os.path.join(a.out, "switch_search.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    print("VERDICT:", rep["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
