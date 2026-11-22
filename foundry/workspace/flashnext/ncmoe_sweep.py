"""Flash-Next with explicit MoE placement: llama-server run directly (the engine ollama bundles), all layers on the
GPU (-ngl 99) except the routed experts of the first N layers (--n-cpu-moe N), n-gram table pinned to the CPU.
Sweeps N; per arm: load time, GPU/CPU buffer sizes from the server's own log, 3 x 500-token generations (forced
length), 3 x prefill of a long prompt with prompt caching OFF. Frontier mode entered and always restored; ollama's
resident models are unloaded first so only one GPU context exists.

    python ncmoe_sweep.py <ORDER_OUT> <gguf> <N> [<N> ...] [--threads T] [order name, appended by the factory]
"""
import json, os, re, statistics, subprocess, sys, time, urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in (_ROOT, os.path.join(_ROOT, "bin")):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import foundry_paths as _P             # this workspace's ONE settings reader
# the ollama bundle whose lib\ollama holds the llama-server these scripts drive: FLASHNEXT_ENGINE_DIR (a setting);
# empty = every arm names its own "bin"
ENGINE_DIR = _P.setting("FLASHNEXT_ENGINE_DIR", "")
BIN = os.path.join(ENGINE_DIR, "lib", "ollama") if ENGINE_DIR else ""
FRONTIER = r"C:\ProgramData\HAI\frontier_mode.py"
OLLAMA = "http://127.0.0.1:11434"
PORT = 8093
GEN = ("Explain, in detail and with examples, how a mixture-of-experts language model routes tokens to experts, why "
       "that saves compute, and what trade-offs it creates for memory.\n\n")
LONG = " ".join(["The quarterly maintenance log lists each machine, its last service date, the technician, parts replaced, "
                 "and any follow-up needed. Summarise recurring failures and which parts are replaced most."] * 110)


def post(url, body, timeout=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def unload_ollama():
    try:
        for m in json.loads(urllib.request.urlopen(OLLAMA + "/api/ps", timeout=30).read()).get("models", []):
            post(OLLAMA + "/api/generate", {"model": m["name"], "keep_alive": 0}, 120)
            print("unloaded ollama model", m["name"], flush=True)
    except OSError as e:
        print("ollama not reachable (%s) - nothing to unload" % e, flush=True)


def wait_ready(proc):
    """Ready when /health says ok; failed if the process exits first. No clock: a load takes what it takes."""
    while proc.poll() is None:
        try:
            if json.loads(urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=5).read()).get("status") == "ok":
                return True
        except (OSError, ValueError):
            pass
        time.sleep(2)
    return False


def buffers(log):
    txt = open(log, encoding="utf-8", errors="replace").read()
    out = {}
    for dev, mib in re.findall(r"(\w[\w_]*) model buffer size =\s*([\d.]+) MiB", txt):
        out[dev] = round(out.get(dev, 0) + float(mib) / 1024, 2)
    for dev, mib in re.findall(r"(\w[\w_]*) compute buffer size =\s*([\d.]+) MiB", txt):
        out[dev + "_compute"] = round(out.get(dev + "_compute", 0) + float(mib) / 1024, 2)
    err = [l for l in txt.splitlines() if re.search(r"out of memory|failed to allocate|error", l, re.I)][:3]
    return out, err


TELEM = _P.GPU_TELEMETRY   # the factory's own telemetry file


def vram_used_gb():
    """VRAM in use now, from the box's own GPU telemetry (the server log does not print buffer sizes)."""
    lines = open(TELEM, encoding="utf-8", errors="replace").read().splitlines()
    head = lines[0].split(",")
    row = dict(zip(head, lines[-1].split(",")))
    return round(float(row["vram_used_mb"]) / 1024, 1)


def complete(prompt, n):
    d = post("http://127.0.0.1:%d/completion" % PORT, {"prompt": prompt, "n_predict": n, "temperature": 0,
                                                       "ignore_eos": True, "cache_prompt": False})
    t = d.get("timings", {})
    return t.get("predicted_per_second"), t.get("prompt_per_second"), t.get("prompt_n"), t.get("predicted_n")


def main():
    out, gguf = sys.argv[1], sys.argv[2]
    rest, threads = sys.argv[3:], 8
    if "--threads" in rest:
        i = rest.index("--threads")
        threads = int(rest[i + 1])
        rest = rest[:i] + rest[i + 2:]
    ns = [int(a) for a in rest if a.isdigit()]                 # the factory's trailing order name is not a number
    assert os.path.isfile(gguf), gguf
    assert ns, "no N values"
    os.makedirs(out, exist_ok=True)
    rocm = os.path.join(BIN, "rocm_v7_1")
    # the ROCm backend lives in a subfolder: without GGML_BACKEND_PATH llama-server silently runs CPU-only
    env = dict(os.environ, PATH=BIN + ";" + rocm + ";" + os.environ.get("PATH", ""),
               GGML_BACKEND_PATH=os.path.join(rocm, "ggml-hip.dll"))
    devs = subprocess.run([os.path.join(BIN, "llama-server.exe"), "--list-devices"], capture_output=True, text=True,
                          cwd=BIN, env=env).stdout
    print("devices:", " | ".join(l.strip() for l in devs.splitlines() if l.strip()), flush=True)
    assert "ROCm0" in devs, "llama-server sees no ROCm GPU - refusing to run a CPU-only sweep"
    res = {"gguf": gguf, "threads": threads, "arms": [], "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    unload_ollama()
    print("frontier enter:", subprocess.run([sys.executable, FRONTIER, "enter"], capture_output=True, text=True).stdout[-200:],
          flush=True)
    try:
        for n in ns:
            log = os.path.join(out, "server_ncmoe%d.log" % n)
            argv = [os.path.join(BIN, "llama-server.exe"), "-m", gguf, "-ngl", "99", "--n-cpu-moe", str(n),
                    "-ot", "per_layer_token_embd=CPU", "--no-mmap", "-t", str(threads), "-c", "16384",
                    "--host", "127.0.0.1", "--port", str(PORT)]
            t0 = time.time()
            with open(log, "w", encoding="utf-8") as lf:
                proc = subprocess.Popen(argv, stdout=lf, stderr=subprocess.STDOUT, cwd=BIN, env=env)
            arm = {"n_cpu_moe": n, "argv": argv}
            try:
                ok = wait_ready(proc)
                arm["load_s"] = round(time.time() - t0, 1)
                arm["buffers_gb"], arm["log_errors"] = buffers(log)
                if not ok:
                    arm["result"] = "FAILED TO LOAD (rc %s)" % proc.returncode
                    print("N=%d: %s | %s | %s" % (n, arm["result"], arm["buffers_gb"], arm["log_errors"]), flush=True)
                    res["arms"].append(arm)
                    continue
                time.sleep(12)                                       # let the 1 s telemetry catch up
                arm["vram_used_gb"] = vram_used_gb()
                if arm["vram_used_gb"] < 2:
                    arm["result"] = "INVALID: CPU-only (%.1f GB VRAM in use)" % arm["vram_used_gb"]
                    print("N=%d: %s - stopping the sweep" % (n, arm["result"]), flush=True)
                    res["arms"].append(arm)
                    break
                complete(GEN, 64)                                    # warm-up, discarded
                gens = [complete(GEN, 500) for _ in range(3)]
                pres = [complete(LONG + " (%d)" % i, 1) for i in range(3)]
                arm.update({"gen_tok_s": [round(g[0], 2) for g in gens], "gen_tokens": [g[3] for g in gens],
                            "prefill_tok_s": [round(p[1], 1) for p in pres], "prefill_tokens": [p[2] for p in pres],
                            "result": "ok"})
                arm["gen_median"] = statistics.median(arm["gen_tok_s"])
                print("N=%2d: load %5.0fs | VRAM %.1f GB | gen %s (median %.2f) | prefill %s on %s tokens"
                      % (n, arm["load_s"], arm["vram_used_gb"], arm["gen_tok_s"], arm["gen_median"], arm["prefill_tok_s"],
                         arm["prefill_tokens"][0]), flush=True)
            except Exception as e:                                   # noqa: BLE001
                arm["result"] = "ERROR %s: %s" % (type(e).__name__, e)
                print("N=%d: %s" % (n, arm["result"]), flush=True)
            finally:
                if proc.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                    proc.wait()
                time.sleep(5)
            res["arms"].append(arm)
            json.dump(res, open(os.path.join(out, "ncmoe_sweep.json"), "w"), indent=1)
    finally:
        print("frontier restore:", subprocess.run([sys.executable, FRONTIER, "restore"], capture_output=True,
                                                  text=True).stdout[-200:], flush=True)
    json.dump(res, open(os.path.join(out, "ncmoe_sweep.json"), "w"), indent=1)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
