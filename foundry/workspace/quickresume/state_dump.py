"""Measures the cost of saving and restoring KV caches vs. re-reading conversations.

llama-server supports `--slot-save-path` (POST /slots/0?action=save|restore) to persist KV state to disk,
preventing full context re-reads after eviction. Ollama lacks this endpoint.

Usage:
    python state_dump.py <ORDER_OUT> <plan.json>

plan.json keys: gguf, bin, ctx, depths, rounds, turn_tokens, extra.
Outputs results.json with per-round timing and KV state sizes."""
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "flashnext"))
try:
    import ladder as L  # noqa: E402  Mothership: the lab's tested launcher (ROCm env, telemetry spill, frontier mode)
except ImportError:
    L = None


class _Portable:
    """Portable fallback for environments lacking flashnext or frontier mode. Uses nvidia-smi for residency checks."""
    PORT = 8093
    OLLAMA = "http://127.0.0.1:11434"
    FRONTIER = None

    @staticmethod
    def post(url, body, timeout=None):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    @staticmethod
    def wait_ready(proc):
        while proc.poll() is None:
            try:
                if json.loads(urllib.request.urlopen("http://127.0.0.1:%d/health" % _Portable.PORT,
                                                     timeout=5).read()).get("status") == "ok":
                    return True
            except (OSError, ValueError):
                pass
            time.sleep(2)
        return False

    @staticmethod
    def engine_env(bin_dir):
        """PATH + GGML_BACKEND_PATH for an engine folder whose GPU backend sits in a subfolder (ollama's bundled server:
        lib\\ollama\\cuda_v12\\ggml-cuda.dll). Without it the server lists no device and would run CPU-only."""
        dlls = sorted(os.path.join(d, f) for d, _, fs in os.walk(bin_dir) for f in fs
                      if f.lower() in ("ggml-cuda.dll", "ggml-hip.dll", "ggml-vulkan.dll"))
        env = dict(os.environ, PATH=";".join([bin_dir] + sorted({os.path.dirname(x) for x in dlls})
                                             + [os.environ.get("PATH", "")]))
        if dlls:
            env["GGML_BACKEND_PATH"] = dlls[0]
        return env

    @staticmethod
    def vram_used_gb():
        return None

    @staticmethod
    def spill_gb():
        return None


if L is None:
    L = _Portable
FRONTIER = getattr(L, "FRONTIER", None)
FRONTIER = FRONTIER if FRONTIER and os.path.isfile(FRONTIER) else None


def card_used_mb():
    """Returns (dedicated_vram_used_mib, total_mib) or None.
    Windows (WDDM) reports per-process usage as [N/A], so residency checks rely on card-wide deltas.

    Note: The docstring mentions returning a single value or None, but the implementation returns a tuple."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
        used, total = (int(x) for x in out[0].split(","))
        return used, total
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def resident_ok(gguf, baseline):
    """(ok, detail) - the model must be fully in VRAM or every timing is void. Mothership: the box telemetry's spill
    (ladder). NVIDIA: the card's VRAM in use must have grown by at least the GGUF's size since the pre-launch baseline
    (ollama's models unloaded first) and the card must not be full (a full card means Windows spills to shared memory).
    Neither available = could not tell, which is NOT ok."""
    spill = L.spill_gb()
    if spill is not None:
        return spill <= 0.5, "spill %.2f GB (box telemetry)" % spill
    now = card_used_mb()
    if now is None or baseline is None:
        return False, "could not tell (no telemetry, no nvidia-smi reading)"
    (used, total), need = now, os.path.getsize(gguf) / 2 ** 20
    grew = used - baseline[0]
    ok = grew >= need and used <= total - 64
    return ok, "card VRAM %d/%d MiB, grew %d MiB since launch; the GGUF is %d MiB" % (used, total, grew, need)


BASE = "http://127.0.0.1:%d" % L.PORT
TOPICS = ["the orchestrator's deploy gate", "a factory order's lifecycle", "the tablet fleet's control items",
          "Mothership's GPU telemetry", "the placement table's crown", "a coding job's checkpoint ledger",
          "the homepage factory tiles", "the prompt cache and its eviction", "the stall rescue daemon",
          "the run ledger's resume path", "an A1 exercise and its hidden tests", "the desk's control slips"]


def tokens(text):
    return len(L.post(BASE + "/tokenize", {"content": text}, 600).get("tokens") or [])


def paragraph(i):
    t = TOPICS[i % len(TOPICS)]
    return ("Note %d on %s. The operator reviewed %s on day %d, recorded what changed, which check proved it, and "
            "what would make it fail again. Step %d of the review listed the inputs, the method, the deliverables "
            "and the cleanup, and named the one variable that differed from the previous run.\n"
            % (i, t, t, 1 + i % 28, 1 + i % 9))


def text_of(n_tokens, start=0):
    """A conversation of at least n_tokens, built from varied numbered paragraphs (the timing does not depend on
    the content, but a repeated block would let the engine's prompt cache hide work)."""
    out, i = [], start
    step = 40 if n_tokens > 5000 else 1   # ~78 tokens a paragraph: a 40-block made a "500-token" turn ~3,100 (live 10-16)
    while True:
        out.extend(paragraph(i + k) for k in range(step))
        i += step
        s = "".join(out)
        if tokens(s) >= n_tokens:
            return s, i


def ids_of(text):
    return list(L.post(BASE + "/tokenize", {"content": text}, 600).get("tokens") or [])


def read(prompt_ids):
    """One request on slot 0 with the prompt cache ON (the production path), prompt as TOKEN IDS. Returns the engine's
    own timings and the ids it generated. The next turn's prompt must be this prompt + these generated ids + the new
    text: a hybrid model's recurrent state cannot be rewound, so a prompt that diverges from the cache even by the
    model's own reply forces a FULL re-read and would make the dump look worthless (#36 handoff_body: the writer's
    prompt EXTENDS the read one)."""
    d = L.post(BASE + "/completion", {"prompt": prompt_ids, "n_predict": 8, "temperature": 0, "cache_prompt": True,
                                      "id_slot": 0, "return_tokens": True}, None)
    t = d.get("timings") or {}
    return ({"prompt_n": t.get("prompt_n"), "prompt_ms": round(t.get("prompt_ms") or 0, 1)},
            list(d.get("tokens") or []))


def slot(action, filename):
    t0 = time.time()
    d = L.post(BASE + "/slots/0?action=%s" % action, {"filename": filename}, None)
    return {"wall_ms": round((time.time() - t0) * 1000, 1), "reply": d}


def main():
    out, plan = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
    os.makedirs(out, exist_ok=True)
    slots = os.path.join(out, "slots")
    os.makedirs(slots, exist_ok=True)
    b = plan["bin"]
    env = L.engine_env(b)
    devs = subprocess.run([os.path.join(b, "llama-server.exe"), "--list-devices"], capture_output=True, text=True,
                          cwd=b, env=env).stdout
    print("devices:", " | ".join(x.strip() for x in devs.splitlines() if x.strip()), flush=True)
    assert any(k in devs for k in ("Vulkan0", "ROCm0", "CUDA0")), "llama-server sees no GPU - refusing a CPU-only run"
    probe = subprocess.run([os.path.join(b, "llama-server.exe"), "-m", "nonexistent.gguf", "-v", "--port",
                            str(L.PORT + 1)], capture_output=True, text=True, cwd=b, env=env, timeout=60)
    sysinfo = next((x for x in (probe.stdout + probe.stderr).splitlines() if "system_info" in x), "")
    assert "AVX2 = 1" in sysinfo, "CPU backend has no AVX2 - every number would be void"
    res = {"plan": plan, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": [], "notes": []}
    try:
        for m in json.loads(urllib.request.urlopen(L.OLLAMA + "/api/ps", timeout=30).read()).get("models", []):
            L.post(L.OLLAMA + "/api/generate", {"model": m["name"], "keep_alive": 0}, 120)
            res["notes"].append("unloaded ollama model %s" % m["name"])
    except OSError:
        pass
    if FRONTIER:   # Mothership's frontier mode (ladder.py's own verbs); boxes without it skip this
        print("frontier enter:", subprocess.run([sys.executable, FRONTIER, "enter"], capture_output=True,
                                                text=True).stdout[-120:], flush=True)
    proc = None
    try:
        # Launch flags come from the PLAN, copied from the model's documented serving recipe - never written fresh here
        # (Flash-Next doc own-goal 10-01: "copy that path's real launch flags, never write them fresh").
        argv = [os.path.join(b, "llama-server.exe"), "-m", plan["gguf"], "-ngl", "99", "-c", str(plan.get("ctx", 122880)),
                "--slot-save-path", slots, "--host", "127.0.0.1", "--port", str(L.PORT)] + list(plan.get("extra", []))
        res["argv"] = argv
        log = os.path.join(out, "server.log")
        time.sleep(5)                                   # ollama's unload (keep_alive 0) frees the card asynchronously
        baseline = card_used_mb()
        res["vram_baseline_mb"] = baseline
        t0 = time.time()
        with open(log, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(argv, stdout=lf, stderr=subprocess.STDOUT, cwd=b, env=env)
        if not L.wait_ready(proc):
            tail = open(log, encoding="utf-8", errors="replace").read().splitlines()[-4:]
            raise RuntimeError("llama-server failed to load: %s" % " | ".join(tail)[-300:])
        res["load_s"] = round(time.time() - t0, 1)
        time.sleep(10)
        res["vram_used_gb"], res["spill_gb"] = L.vram_used_gb(), L.spill_gb()
        ok, res["residency"] = resident_ok(plan["gguf"], baseline)
        print("residency:", res["residency"], flush=True)
        if not ok:
            raise RuntimeError("INVALID - not fully resident: %s" % res["residency"])
        turn_n = int(plan.get("turn_tokens", 1000))
        quick_text, _ = text_of(turn_n, start=900000)               # an unrelated request: evicts slot 0
        quick = ids_of(quick_text)
        for depth in plan.get("depths", [30000, 60000]):
            base, nxt = text_of(depth)
            for rnd in range(int(plan.get("rounds", 2))):
                # Prefix must be unique per row to avoid cache collisions; duplicate prefixes cause cold reads to return stale data.
                conv = ids_of("Conversation %d-%d-%d.\n" % (depth, rnd + 1, time.time_ns()) + base)
                row = {"depth": depth, "round": rnd + 1, "conv_tokens": len(conv)}
                t1, nxt = text_of(turn_n, start=nxt + 1000 * (rnd + 1))
                t2, nxt = text_of(turn_n, start=nxt)
                t3, nxt = text_of(turn_n, start=nxt)
                row["cold"], g0 = read(conv)                      # the whole conversation, nothing cached
                turn1 = conv + g0 + ids_of(t1)
                row["cached_turn"], g1 = read(turn1)              # the normal next turn: new tokens only
                kept = turn1 + g1                                 # what slot 0 holds now = what gets saved
                fname = "d%d_r%d.bin" % (depth, rnd + 1)
                row["save"] = slot("save", fname)
                fpath = os.path.join(slots, fname)
                row["file_gb"] = round(os.path.getsize(fpath) / 1e9, 3) if os.path.isfile(fpath) else None
                row["evict_1"], _ = read(quick)
                row["reread_turn"], _ = read(kept + ids_of(t2))   # TODAY: the evicted job re-reads everything
                row["evict_2"], _ = read(quick)
                row["restore"] = slot("restore", fname)
                row["after_restore_turn"], _ = read(kept + ids_of(t3))  # THE DUMP: restored, new tokens only
                if os.path.isfile(fpath):
                    os.remove(fpath)
                res["rows"].append(row)
                print("depth %d r%d: re-read %s ms (%s tok) vs restore %s ms + %s ms (%s tok); save %s ms, %s GB" % (
                    depth, rnd + 1, row["reread_turn"]["prompt_ms"], row["reread_turn"]["prompt_n"],
                    row["restore"]["wall_ms"], row["after_restore_turn"]["prompt_ms"],
                    row["after_restore_turn"]["prompt_n"], row["save"]["wall_ms"], row["file_gb"]), flush=True)
                json.dump(res, open(os.path.join(out, "results.json"), "w", encoding="utf-8"), indent=1)
    except Exception as exc:
        res["error"] = "%s: %s" % (type(exc).__name__, exc)   # a refused run says WHY in its own deliverable
        raise
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(60)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(slots, ignore_errors=True)                    # byproduct: multi-GB slot files never linger
        for f in glob.glob(os.path.join(out, "*.bin")):
            os.remove(f)
        if FRONTIER:
            print("frontier restore:", subprocess.run([sys.executable, FRONTIER, "restore"], capture_output=True,
                                                      text=True).stdout[-120:], flush=True)   # ladder.py's own verb
        json.dump(res, open(os.path.join(out, "results.json"), "w", encoding="utf-8"), indent=1)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
