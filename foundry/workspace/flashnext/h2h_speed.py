"""Flash-Next build head-to-head (speed): each build loaded fresh, placement read back, one discarded warm-up, then
3 x 500-token generations (temp 0, same prompt as the q2mix ladder) and 2 x long-prompt prefill measurements.
Frontier mode is entered first and ALWAYS restored (finally).

    python h2h_speed.py <ORDER_OUT> <tag> [<tag> ...] [--threads N] [order name, appended by the factory]
"""
import json, os, statistics, subprocess, sys, time, urllib.request

HOST = "http://127.0.0.1:11434"
FRONTIER = r"C:\ProgramData\HAI\frontier_mode.py"
GEN_PROMPT = ("Explain, in detail and with examples, how a mixture-of-experts language model routes tokens to experts, "
              "why that saves compute, and what trade-offs it creates for memory.")
LONG = " ".join(["The quarterly maintenance log lists each machine, its last service date, the technician, parts "
                 "replaced, and any follow-up needed. Summarise recurring failures and which parts are replaced most."] * 40)


def call(path, body, timeout=None):
    req = urllib.request.Request(HOST + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def chat(model, prompt, n, threads):
    opts = {"num_predict": n, "temperature": 0}
    if threads:
        opts["num_thread"] = threads
    t = time.time()
    d = call("/api/chat", {"model": model, "stream": False, "think": False, "keep_alive": "30m", "options": opts,
                           "messages": [{"role": "user", "content": prompt}]})
    d["_wall_s"] = round(time.time() - t, 1)
    return d


def rate(count, dur_ns):
    return round(count / (dur_ns / 1e9), 2) if count and dur_ns else None


def main():
    out = sys.argv[1]
    threads = int(sys.argv[sys.argv.index("--threads") + 1]) if "--threads" in sys.argv else None
    skip = {sys.argv.index("--threads"), sys.argv.index("--threads") + 1} if threads else set()
    tags = [a for i, a in enumerate(sys.argv[2:], 2) if i not in skip and not a.startswith("--") and "." not in a]
    assert tags, "no build tags given"
    os.makedirs(out, exist_ok=True)
    res = {"threads": threads or "default", "builds": {}, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    print("frontier enter:", subprocess.run([sys.executable, FRONTIER, "enter"], capture_output=True, text=True).stdout[-300:],
          flush=True)
    try:
        for tag in tags:
            model = "qwen3.8-flash-next:" + tag
            loaded = json.loads(urllib.request.urlopen(HOST + "/api/ps", timeout=30).read()).get("models", [])
            for m in loaded:                                     # unload whatever is resident
                call("/api/generate", {"model": m["name"], "keep_alive": 0}, 120)
            time.sleep(3)
            warm = chat(model, GEN_PROMPT, 500, threads)
            ps = [m for m in json.loads(urllib.request.urlopen(HOST + "/api/ps", timeout=30).read()).get("models", [])
                  if m["name"] == model]
            place = {"size_gb": round(ps[0]["size"] / 1e9, 1), "vram_gb": round(ps[0]["size_vram"] / 1e9, 1)} if ps else {}
            gens = [chat(model, GEN_PROMPT, 500, threads) for _ in range(3)]
            pres = [chat(model, LONG + " (run %d)" % i, 1, threads) for i in range(2)]
            g = [rate(x.get("eval_count"), x.get("eval_duration")) for x in gens]
            p = [rate(x.get("prompt_eval_count"), x.get("prompt_eval_duration")) for x in pres]
            b = {"load_s": round((warm.get("load_duration") or 0) / 1e9, 1), "placement": place,
                 "gen_tok_s": g, "gen_median": statistics.median([v for v in g if v]),
                 "gen_tokens": [x.get("eval_count") for x in gens],
                 "prefill_tok_s": p, "prefill_tokens": [x.get("prompt_eval_count") for x in pres],
                 "warmup_gen_tok_s": rate(warm.get("eval_count"), warm.get("eval_duration"))}
            res["builds"][tag] = b
            print("%-8s load %5.1fs | %s | gen %s (median %.2f) | prefill %s" % (tag, b["load_s"], place, g, b["gen_median"], p),
                  flush=True)
            json.dump(res, open(os.path.join(out, "h2h_speed.json"), "w"), indent=1)
    finally:
        print("frontier restore:", subprocess.run([sys.executable, FRONTIER, "restore"], capture_output=True,
                                                  text=True).stdout[-300:], flush=True)
    json.dump(res, open(os.path.join(out, "h2h_speed.json"), "w"), indent=1)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
