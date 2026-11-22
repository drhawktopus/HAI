"""Answer-quality runs: every arm answers the same prompts via llama-server (thinking on) for blind pairwise grading.

    python answers.py <ORDER_OUT> <plan.json>

plan: {"prompts": <prompts json>, "sampler": {...}, "arms": [{"name", "bin", "gguf", "args": [...], "env": {...}}, ...]}
An arm's optional "env" overrides the engine's (e.g. GGML_BACKEND_PATH to run ollama's own engine on Vulkan).
Uses ladder.py helpers (engine_env, wait_ready, post, frontier mode) for server handling.
No max_tokens or request timeout; answers with finish_reason == "length" are marked VOID (context hit) and never graded.
Prompt 'key' fields are never sent."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder as L

OUT, PLAN = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
plan = json.load(open(PLAN, encoding="utf-8"))
prompts = json.load(open(plan["prompts"], encoding="utf-8"))["prompts"]
if plan.get("only_prompts"):                     # a make-up run for prompts an earlier order did not reach
    prompts = [p for p in prompts if p["id"] in plan["only_prompts"]]
    assert len(prompts) == len(plan["only_prompts"]), "only_prompts names an id the prompt file lacks"
sampler = plan.get("sampler", {})
print("prompts: %d | sampler %s" % (len(prompts), sampler), flush=True)
print("frontier enter:", subprocess.run([sys.executable, L.FRONTIER, "enter"], capture_output=True, text=True).stdout[-120:],
      flush=True)
for m in json.loads(L.urllib.request.urlopen(L.OLLAMA + "/api/ps", timeout=30).read()).get("models", []):
    L.post(L.OLLAMA + "/api/generate", {"model": m["name"], "keep_alive": 0}, 120)   # the previous order's model
    print("unloaded ollama model", m["name"], flush=True)                               # still holds the card
loaded = set()   # an arm with "fallback_for": X runs only if arm X failed to load
groups = set()   # arms sharing a "group" are tried in order until one loads cleanly; the rest are skipped
try:
    for a in plan["arms"]:
        if a.get("fallback_for") in loaded:
            print("%s: skipped - %s loaded, this is its fallback" % (a["name"], a["fallback_for"]), flush=True)
            continue
        if a.get("group") in groups:
            print("%s: skipped - group %s already has a clean load" % (a["name"], a["group"]), flush=True)
            continue
        b = a["bin"]
        log = os.path.join(OUT, "server_%s.log" % a["name"])
        argv = [os.path.join(b, "llama-server.exe"), "-m", a["gguf"]] + list(a.get("args", [])) + [
            "--jinja", "--reasoning-format", "deepseek", "--host", "127.0.0.1", "--port", str(L.PORT)]
        res = {"arm": a["name"], "gguf": a["gguf"], "argv": argv, "sampler": sampler, "answers": []}
        t0 = time.time()
        with open(log, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(argv, stdout=lf, stderr=subprocess.STDOUT, cwd=b,
                                    env=dict(L.engine_env(b), **a.get("env", {})))
        try:
            if not L.wait_ready(proc):
                tail = open(log, encoding="utf-8", errors="replace").read().splitlines()[-4:]
                print("%s: FAILED TO LOAD (rc %s): %s" % (a["name"], proc.returncode, " | ".join(tail)[-300:]), flush=True)
                continue
            # a spilled load runs, just slowly through system RAM - the worst kind of failure for a speed test.
            # The KV cache and compute buffers are allocated at load, so spill now is spill for the whole run.
            if a.get("max_spill_gb") is not None:
                time.sleep(15)                                   # let the box logger write a post-load row
                sp = L.spill_gb()
                if sp is None or sp > a["max_spill_gb"]:
                    print("%s: SPILLED %s GB at load (limit %s) - next size in its group"
                          % (a["name"], sp, a["max_spill_gb"]), flush=True)
                    continue
                print("%s: spill %.2f GB at load - clean" % (a["name"], sp), flush=True)
            loaded.add(a["name"])
            if a.get("group"):
                groups.add(a["group"])
            print("%s: loaded in %.0f s" % (a["name"], time.time() - t0), flush=True)
            for p in prompts:
                body = dict(sampler, messages=[{"role": "user", "content": p["prompt"]}],
                            chat_template_kwargs={"enable_thinking": True})
                t1 = time.time()
                d = L.post("http://127.0.0.1:%d/v1/chat/completions" % L.PORT, body)
                ch = d["choices"][0]
                msg = ch.get("message", {})
                ans = {"id": p["id"], "kind": p["kind"], "content": msg.get("content") or "",
                       "reasoning": msg.get("reasoning_content") or "", "finish_reason": ch.get("finish_reason"),
                       "completion_tokens": (d.get("usage") or {}).get("completion_tokens"),
                       "seconds": round(time.time() - t1, 1), "timings": d.get("timings")}
                tm = d.get("timings") or {}
                if tm.get("predicted_per_second"):
                    print("%s: %s gen %.1f tok/s | prompt %s tok | draft %s/%s accepted"
                          % (a["name"], p["id"], tm["predicted_per_second"], tm.get("prompt_n"),
                             tm.get("draft_n_accepted"), tm.get("draft_n")), flush=True)
                ans["void"] = ans["finish_reason"] == "length" or not ans["content"].strip()
                res["answers"].append(ans)
                print("%s: %s %s | %s tokens | %.0f s | answer %d chars%s"
                      % (a["name"], p["id"], ans["finish_reason"], ans["completion_tokens"], ans["seconds"],
                         len(ans["content"]), " | VOID" if ans["void"] else ""), flush=True)
                json.dump(res, open(os.path.join(OUT, "answers_%s.json" % a["name"]), "w", encoding="utf-8"), indent=1)
        except Exception as e:                                       # noqa: BLE001
            print("%s: ERROR %s: %s" % (a["name"], type(e).__name__, e), flush=True)
        finally:
            if proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                proc.wait()
            time.sleep(5)
finally:
    print("frontier restore:", subprocess.run([sys.executable, L.FRONTIER, "restore"], capture_output=True,
                                              text=True).stdout[-120:], flush=True)
print("DONE", flush=True)
