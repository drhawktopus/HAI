"""Check if the local llama-server can rerank a Qwen3-Reranker GGUF model correctly.

Usage:
    python rerank_check.py <model.gguf> <out_dir> [--engine <llama-server.exe>]

Output: <out_dir>/rerank.json containing engine, model, load_s, cases (name, expected, top, ok, scores, ms), passed, total, and verdict.

Constraints:
- Must run on CPU (-ngl 0) to avoid contending with GPU resources used by other services (e.g., Plex).
- Health check relies on process status/exits rather than timeouts; process exit indicates failure.
- The first test case acts as a positive control; if it fails, subsequent results are invalid."""
import argparse
import glob
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

CASES = [  # (name, query, documents, index of the right one) - the first is the positive control
    ("control-capital", "What is the capital of France?",
     ["Bananas are rich in potassium.", "Berlin is the capital of Germany.",
      "Paris is the capital and largest city of France.", "The Eiffel Tower is made of wrought iron."], 2),
    ("lab-deploy", "I edited app.py for the orchestrator - how do I get the change running?",
     ["Pi-hole v6 reloads its DNS lists with `pihole reloaddns`; restartdns only prints help.",
      "Watchtower updates every container at 4 am except claudediscordbot.",
      "The orchestrator's code is baked into its image: docker compose build, then up -d. A restart alone runs the "
      "OLD code.",
      "Caddy reloads its config with `caddy reload --config /etc/caddy/Caddyfile`."], 2),
    ("lab-tablets", "Which tablets does Tolkien hand a one-batch order?",
     ["Each fleet order takes only the tablets it needs - one per batch it deals, the fastest free ones first - and "
      "the next order runs on the free tablets.",
      "Plex is always top priority on Softserve; side projects starve first.",
      "BigHonker's 3070 is clock-locked at 1500 MHz as a mitigation.",
      "Mothership has a 7900 XTX with 24 GB of VRAM."], 0),
    ("lab-mccoy", "What does McCoy do?",
     ["Frigate was removed in August and replaced by bespoke code in Tolkien.",
      "McCoy is Starfleet's small advisor model on Softserve: he triages each Auto turn and advises; deterministic "
      "code decides.",
      "The DO Pi-hole syncs nightly at 3 am.",
      "Caddy serves every .lan name over plain HTTP."], 1),
]


def engine():
    roots = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama-*lab*", "lib", "ollama"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "AMD", "AI_Bundle", "Ollama-*lab*", "lib", "ollama")]
    found = sorted((p for r in roots for p in glob.glob(os.path.join(r, "llama-server.exe"))), key=os.path.getmtime)
    return found[-1] if found else None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http(url, body=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "null")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("out_dir")
    ap.add_argument("--engine", default=None)
    # the foundry appends the order's name to every compute argv (patrol-exempt by name-in-cmdline): accept it
    ap.add_argument("order_name", nargs="?", default=None)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    out = {"engine": a.engine or engine(), "model": a.model, "cases": [], "verdict": "",
           "box": socket.gethostname()}
    path = os.path.join(a.out_dir, "rerank.json")
    if not out["engine"] or not os.path.exists(out["engine"]):
        out["verdict"] = "NO ENGINE - no llama-server.exe found under the lab engine trees"
    elif not os.path.exists(a.model):
        out["verdict"] = "NO MODEL - %s not found" % a.model
    if out["verdict"]:
        json.dump(out, open(path, "w", encoding="utf-8"), indent=2)
        print(out["verdict"])
        return 2
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    log = open(os.path.join(a.out_dir, "llama-server.log"), "w", encoding="utf-8", errors="replace")
    t0 = time.time()
    proc = subprocess.Popen([out["engine"], "-m", a.model, "--rerank", "--host", "127.0.0.1", "--port", str(port),
                             "-ngl", "0", "-c", "8192"],
                            cwd=os.path.dirname(out["engine"]), stdout=log, stderr=subprocess.STDOUT)
    try:
        while True:                                      # ready by STATUS; the process exiting is the failure
            if proc.poll() is not None:
                out["verdict"] = "ENGINE EXITED rc %s before /health answered (see llama-server.log)" % proc.returncode
                break
            try:
                if (http(base + "/health", timeout=5) or {}).get("status") == "ok":
                    break
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.5)
        out["load_s"] = round(time.time() - t0, 1)
        if not out["verdict"]:
            for name, query, docs, right in CASES:
                t1 = time.time()
                try:
                    res = http(base + "/v1/rerank", {"model": "rerank", "query": query, "documents": docs})
                except Exception as exc:                 # noqa: BLE001 - recorded, never dropped
                    out["cases"].append({"name": name, "expected": right, "error": "%s: %s" % (type(exc).__name__,
                                                                                             str(exc)[:200])})
                    continue
                scores = {int(r["index"]): float(r["relevance_score"]) for r in (res or {}).get("results") or []}
                top = max(scores, key=scores.get) if scores else None
                out["cases"].append({"name": name, "expected": right, "top": top, "ok": top == right,
                                     "scores": [round(scores.get(i, float("nan")), 4) for i in range(len(docs))],
                                     "ms": round(1000 * (time.time() - t1))})
            ok = [c for c in out["cases"] if c.get("ok")]
            out["passed"], out["total"] = len(ok), len(CASES)
            control = out["cases"][0] if out["cases"] else {}
            out["verdict"] = ("CONTROL FAILED - the instrument is broken, every case is void" if not control.get("ok")
                              else "%d of %d right" % (len(ok), len(CASES)))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()
        json.dump(out, open(path, "w", encoding="utf-8"), indent=2)
    print(out["verdict"])
    return 0 if out["verdict"].endswith("right") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
