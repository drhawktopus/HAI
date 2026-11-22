"""Measure `ollama serve` itself (not a hand-launched llama-server) under different backends / MTP draft depths.

    python ollama_arm.py <out_dir> <plan.json>

Each arm starts a PRIVATE `ollama serve` on its own port (the live service on 11434 is never touched), with the live
launcher's settings plus the arm's env overrides, loads the model, runs the prompts through /api/generate, and records
ollama's own eval timings, /api/ps residency and the server log. The backend is READ from the debug log, never assumed.
"""
import json, os, re, statistics, subprocess, sys, time, urllib.request

import ladder as L

OLLAMA_EXE = os.path.join(L.ENGINE_DIR, "ollama.exe") if L.ENGINE_DIR else ""   # the bundle the ladder drives (FLASHNEXT_ENGINE_DIR)
LIVE_ENV = {"OLLAMA_MODELS": r"E:\Ollama\Models", "OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0",
            "OLLAMA_NUM_PARALLEL": "2"}           # = C:\ProgramData\HAI\start-ollama.ps1 + Mothership's machine env
BACKEND_RE = re.compile(r"(library=\S+|ggml_vulkan: \d|Vulkan0|ROCm0|using device \S+|inference compute)", re.I)


def api(port, path, body=None, timeout=None):
    url = "http://127.0.0.1:%d%s" % (port, path)
    if body is None:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    return L.post(url, body, timeout)


def wait_up(proc, port, limit_s):
    t0 = time.time()
    while proc.poll() is None and time.time() - t0 < limit_s:
        try:
            api(port, "/api/version", timeout=3)
            return True
        except (OSError, ValueError):
            time.sleep(1)
    return False


def run_arm(arm, plan, out_dir):
    port = int(arm.get("port", 11435))
    env = dict(os.environ, **LIVE_ENV)
    env.update({"OLLAMA_HOST": "127.0.0.1:%d" % port, "OLLAMA_DEBUG": "1", "OLLAMA_KEEP_ALIVE": "30m"})
    env.update(arm.get("env") or {})
    log_path = os.path.join(out_dir, "serve_%s.log" % arm["name"])
    rec = {"name": arm["name"], "env": arm.get("env") or {}, "port": port}
    with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.Popen([OLLAMA_EXE, "serve"], stdout=lf, stderr=subprocess.STDOUT, env=env)
    try:
        if not wait_up(proc, port, 60):
            rec["result"] = "ERROR: ollama serve did not answer /api/version"
            return rec
        opts = {"temperature": plan.get("temperature", 0), "num_ctx": plan["num_ctx"], "seed": plan.get("seed", 1)}
        t0 = time.time()
        api(port, "/api/generate", {"model": plan["model"], "prompt": "Say OK.", "stream": False, "think": False,
                                     "options": dict(opts, num_predict=4)})
        rec["load_s"] = round(time.time() - t0, 1)
        ps = api(port, "/api/ps").get("models") or []
        rec["ps"] = [{"name": m.get("name"), "size_gb": round(m.get("size", 0) / 2**30, 2),
                      "size_vram_gb": round(m.get("size_vram", 0) / 2**30, 2), "context_length": m.get("context_length")}
                     for m in ps]
        rec["spill_gb"] = L.spill_gb()
        rec["vram_used_gb"] = L.vram_used_gb()
        gens = []
        for p in plan["prompts"]:
            d = api(port, "/api/generate", {"model": plan["model"], "prompt": p, "stream": False,
                                            "think": plan.get("think", False),
                                            "options": dict(opts, num_predict=plan.get("num_predict", 512))})
            ev, ed = d.get("eval_count") or 0, (d.get("eval_duration") or 0) / 1e9
            pe, pd = d.get("prompt_eval_count") or 0, (d.get("prompt_eval_duration") or 0) / 1e9
            gens.append({"eval_count": ev, "gen_tok_s": round(ev / ed, 1) if ed else None,
                         "prompt_tok_s": round(pe / pd, 1) if pd else None, "done_reason": d.get("done_reason")})
            print("[%s] %d/%d gen %s tok/s (%d tok)" % (arm["name"], len(gens), len(plan["prompts"]),
                                                       gens[-1]["gen_tok_s"], ev), flush=True)
        rec["gens"] = gens
        tps = [g["gen_tok_s"] for g in gens if g["gen_tok_s"]]
        rec["gen_tok_s_median"] = round(statistics.median(tps), 1) if tps else None
        rec["result"] = "ok"
        return rec
    except Exception as e:                                          # noqa: BLE001
        rec["result"] = "ERROR: %r" % (e,)
        return rec
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        time.sleep(3)
        try:
            text = open(log_path, encoding="utf-8", errors="replace").read()
            hits = BACKEND_RE.findall(text)
            rec["backend_lines"] = sorted(set(hits))[:12]
            rec["backend_lines_n"] = len(hits)
            rec["runner_cmd"] = [ln[:600] for ln in text.splitlines() if "llama-server" in ln and ("--model" in ln or "-m " in ln)][:2]
            rec["spec_lines"] = [ln[:300] for ln in text.splitlines() if re.search(r"draft|speculat|mtp|nextn", ln, re.I)][:8]
        except OSError as e:
            rec["backend_lines"] = ["log unreadable: %r" % (e,)]


def main():
    out_dir, plan_path = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    plan = json.load(open(plan_path, encoding="utf-8"))
    results = []
    for arm in plan["arms"]:
        r = run_arm(arm, plan, out_dir)
        results.append(r)
        print("ARM %s: %s | median gen %s tok/s | vram %s spill %s | backend lines %s" % (
            r["name"], r.get("result"), r.get("gen_tok_s_median"), r.get("vram_used_gb"), r.get("spill_gb"),
            r.get("backend_lines_n")), flush=True)
        json.dump({"plan": plan, "arms": results}, open(os.path.join(out_dir, "ollama_arms.json"), "w", encoding="utf-8"),
                  indent=1)
    print("LADDER: " + " | ".join("%s %s" % (r["name"], r.get("gen_tok_s_median")) for r in results), flush=True)


if __name__ == "__main__":
    main()
