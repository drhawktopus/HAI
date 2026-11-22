"""#34 B11b stage 1 probe, as a compute order payload: does a slot SAVED from ollama's own runner, then RESTORED into
the runner after an eviction, let the next turn read only its new tokens - through ollama, the way HAI calls it?

    slot_dump_probe.py <ORDER_OUT> <model> <num_ctx> [--doc-chars=N] [<job name, appended by the factory>]

Needs LLAMA_ARG_SLOT_SAVE_PATH in ollama's environment (the runner inherits it; ollama 0.40.5's bundled llama-server
reads it - B11b). Method, all through http://127.0.0.1:11434 except the slot calls, which go to the runner's own
127.0.0.1 port (read from its command line):
  1. turn 1: a long document + a question (/api/chat, stream off) -> answer A1
  2. SAVE slot 0 of the runner -> slot file
  3. EVICT (keep_alive 0) and wait until /api/ps is empty
  4. RESTORE arm: load the model again (an empty-prompt generate), restore the slot file into the new runner, then
     turn 2 = turn 1's messages + A1 + a new question -> prompt_eval_count
  5. CONTROL arm: evict again, load again, NO restore, the same turn 2 -> prompt_eval_count (the full re-read)
Lossless is not claimed (stock engine, no pending token): this measures what an eviction BETWEEN turns costs with and
without the dump. Deliverable: result.json. Cleanup: the slot file is deleted; the model is left loaded."""
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
SLOT_FILE = "b11b-probe.bin"


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def post(url, body, timeout=1800):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def runner_port():
    """The bundled llama-server's --port, from its own command line (exactly one runner: one model per card)."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
                          "ForEach-Object { $_.CommandLine }"], capture_output=True, text=True, errors="replace",
                         timeout=60).stdout
    ports = re.findall(r"--port (\d+)", out)
    if len(ports) != 1:
        raise RuntimeError("expected exactly one runner, found ports %r" % (ports,))
    return int(ports[0])


def evict(model):
    post(OLLAMA + "/api/generate", {"model": model, "keep_alive": 0}, timeout=120)
    for _ in range(120):
        if not (get(OLLAMA + "/api/ps").get("models") or []):
            return
        time.sleep(1)
    raise RuntimeError("model still listed in /api/ps 120 s after keep_alive 0")


def load(model, ctx):
    r = post(OLLAMA + "/api/generate", {"model": model, "prompt": "", "options": {"num_ctx": ctx}}, timeout=600)
    return round((r.get("load_duration") or 0) / 1e9, 2)


def chat(model, ctx, messages):
    t0 = time.time()
    r = post(OLLAMA + "/api/chat", {"model": model, "messages": messages, "stream": False,
                                    "options": {"num_ctx": ctx, "temperature": 0, "seed": 1, "num_predict": 256}})
    return r, {"prompt_eval_count": r.get("prompt_eval_count"),
               "prompt_eval_s": round((r.get("prompt_eval_duration") or 0) / 1e9, 3),
               "eval_count": r.get("eval_count"), "load_s": round((r.get("load_duration") or 0) / 1e9, 2),
               "wall_s": round(time.time() - t0, 2), "done_reason": r.get("done_reason")}


def main():
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    out, model, ctx = pos[0], pos[1], int(pos[2])           # a 4th positional is the job name the factory appends
    doc_chars = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--doc-chars=")), "24000"))
    os.makedirs(out, exist_ok=True)
    root = os.environ.get("FOUNDRY_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # The slot dir is resolved from the launcher's env, not the current process's:
    # the factory does not propagate this env var, and the path is host-specific.
    slots = os.environ.get("LLAMA_ARG_SLOT_SAVE_PATH")
    if not slots:
        sys.path.insert(0, os.path.join(root, "bin"))
        from helper_window import runner_slot_dir
        slots = runner_slot_dir()
    doc = open(os.path.join(root, "bin", "p20_foundry.py"), encoding="utf-8", errors="replace").read()[:doc_chars]
    m1 = [{"role": "user", "content": "Here is a source file:\n\n" + doc + "\n\nIn two sentences: what does it do?"}]
    res = {"model": model, "num_ctx": ctx, "doc_chars": len(doc), "start": now(), "slot_save_path_env_here": slots}

    evict(model)
    load(model, ctx)
    r1, res["turn1"] = chat(model, ctx, m1)
    a1 = (r1.get("message") or {}).get("content") or ""
    m2 = m1 + [{"role": "assistant", "content": a1},
               {"role": "user", "content": "Name one function in it and say what it is for, in one sentence."}]

    port = runner_port()
    sv = post("http://127.0.0.1:%d/slots/0?action=save" % port, {"filename": SLOT_FILE}, timeout=600)
    res["save"] = {"port": port, "n_saved": sv.get("n_saved"), "n_written": sv.get("n_written"),
                   "save_ms": (sv.get("timings") or {}).get("save_ms")}

    evict(model)
    res["restore_arm"] = {"reload_s": load(model, ctx)}
    port = runner_port()
    rs = post("http://127.0.0.1:%d/slots/0?action=restore" % port, {"filename": SLOT_FILE}, timeout=600)
    res["restore_arm"].update(port=port, n_restored=rs.get("n_restored"),
                              restore_ms=(rs.get("timings") or {}).get("restore_ms"))
    _, res["restore_arm"]["turn2"] = chat(model, ctx, m2)

    evict(model)
    res["control_arm"] = {"reload_s": load(model, ctx)}
    _, res["control_arm"]["turn2"] = chat(model, ctx, m2)

    # ollama's prompt_eval_count is the WHOLE prompt even when the cache served most of it (10-21 BigHonker: 6,300 in
    # both arms), so the read TIME is the measure: what turn 2 spent reading, with and without the restored slot
    a, c = res["restore_arm"]["turn2"]["prompt_eval_s"], res["control_arm"]["turn2"]["prompt_eval_s"]
    res["read_s_with_dump"], res["read_s_without_dump"] = a, c
    res["read_speedup"] = round(c / a, 1) if a else None
    res["finished"] = now()
    try:
        if slots:
            os.remove(os.path.join(slots, SLOT_FILE))
            res["cleanup"] = "slot file deleted"
    except OSError as e:
        res["cleanup"] = "slot file not deleted: %r" % (e,)
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print("[b11b] RESULT %s" % json.dumps(res), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
