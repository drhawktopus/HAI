"""Price the local ollama query embed cost (nomic-embed-text) to feed menu policy placement.

Usage: python embed_check.py <out.json> [--model nomic-embed-text] [--host http://127.0.0.1:11434] [--rounds 3] [--min-loop-s 30] [order_name]

Method:
1. Evict model, perform one cold call. Use ollama's load_duration for residency cost.
2. Assert model fits in VRAM (infer_ollama._placed). Exit 2 (REFUSED) if it spills or misses.
3. Run warm probes for >= MIN_LOOP_S (default 30s) to allow power meter sampling. 
   30s is a floor (3 x slowest telemetry cadence), not a cap, to resolve J/call.
4. Output vectors to allow reducer verification that all boxes return identical embeddings.
   Disagreement indicates instrument fault, not cheaper pricing.

Output: out.json containing placement metrics, timing rows, vectors, and summary.
Progress markers written per row."""
import argparse
import json
import os
import socket
import statistics
import sys
import time
import urllib.request

# This payload lives in <FOUNDRY_ROOT>\workspace\embed; the measurement helpers are the foundry's own, in <root>\bin.
_ROOT = os.environ.get("FOUNDRY_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "bin"))
import infer_ollama as IO  # noqa: E402  PowerMeter, _placed, _post

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUERY_PREFIX = "search_query: "      # nomic-embed-text is asymmetric; HAI's queries carry this (config.EMBED_QUERY_PREFIX)
# What HAI actually embeds per turn: the user's words (library search) and the same words for tool selection, 5-40
# tokens. Two docs-length texts sit at the end so the price covers a long question too.
PROBES = [
    ("q01", "how do I restart the orchestrator after editing app.py"),
    ("q02", "what is McCoy"),
    ("q03", "which tablets are in the Fellowship and what class is sam"),
    ("q04", "why did Mothership freeze this afternoon"),
    ("q05", "show me the energy price right now"),
    ("q06", "weather this week"),
    ("q07", "remind me how the busy pin queue tells a user their place and ETA"),
    ("q08", "set the living room lights to 40 percent"),
    ("q09", "what did we decide about keeping models loaded"),
    ("q10", "plex is buffering on the tv in the bedroom, what do I check first"),
    ("q11", "I edited the Caddyfile to add a new .lan name for the onboarding app and reloaded caddy but the name still "
            "does not resolve from my phone on wifi - which pi-hole list do I update and how do I reload it"),
    ("q12", "write me a short summary of what project 34 built this week for the status page, in plain language, "
            "covering the queue notice, the coding rows, the new window price and the embed family"),
]
MARK = "{stem}.progress"


def embed(host, model, text, keep_alive=None, timeout=300):
    body = {"model": model, "input": text}
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    req = urllib.request.Request(host.rstrip("/") + "/api/embed", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return d, t0, time.time()


def marker(out, item_id, rnd):
    d = MARK.format(stem=out[:-5] if out.lower().endswith(".json") else out)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "%s.r%d.done" % (item_id, rnd)), "w").close()


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--model", default="nomic-embed-text")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--min-loop-s", type=float, default=30.0)
    ap.add_argument("order_name", nargs="?", default=None)      # appended by the foundry; not used
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    box = socket.gethostname().lower()
    out = {"host": box, "model": a.model, "served": a.host, "placement": None, "dims": None, "rows": [],
           "vectors": {}, "summary": {}, "probes": [{"id": i, "text": QUERY_PREFIX + t} for i, t in PROBES]}

    # 1. cold load, measured: evict (a call with keep_alive 0 loads if needed and unloads after), then one clean call
    print("evicting %s for a cold-load measurement" % a.model, flush=True)
    try:
        embed(a.host, a.model, QUERY_PREFIX + "warm", keep_alive=0)
    except Exception as exc:                                        # noqa: BLE001 - the cold call below is the test
        print("evict call failed (%s) - continuing to the cold call" % exc, flush=True)
    time.sleep(3)
    d, t0, t1 = embed(a.host, a.model, QUERY_PREFIX + PROBES[0][1])
    ld = d.get("load_duration")
    load_s = round(ld / 1e9, 2) if isinstance(ld, (int, float)) and ld > 0 else None
    print("cold call %.2f s, load_duration %s s" % (t1 - t0, load_s), flush=True)

    # 2. on the card? (size_vram == size and the runner not in shared memory) - else refuse, never measure a crawl.
    # /api/ps names the model by its full tag ("nomic-embed-text:latest"); _placed matches the name exactly, so an
    # untagged model name is asked by its tag (the 17:42 orders refused on exactly this: None/None bytes).
    tag = a.model if ":" in a.model else a.model + ":latest"
    ok, vram, size, sp = IO._placed(a.host, tag)
    out["placement"] = {"size_vram": vram, "size": size, "offload": False, "cold": True, "load_s": load_s,
                        "spill": sp if sp is not None else "could not tell"}
    if not ok:
        out["summary"]["verdict"] = ("REFUSED: %s is not fully on the card (%s/%s bytes) after a clean load"
                                     % (a.model, vram, size))
        json.dump(out, open(a.out, "w", encoding="utf-8"), indent=1)
        print(out["summary"]["verdict"], flush=True)
        return 2

    # 3. the warm loop under the meter
    meter = IO.PowerMeter()
    time.sleep(1.0)                                   # the meter's first samples, so the loop's start is covered
    loop_t0 = time.time()
    rnd, calls = 0, 0
    while rnd < a.rounds or (time.time() - loop_t0) < a.min_loop_s:
        rnd += 1
        for item_id, text in PROBES:
            d, c0, c1 = embed(a.host, a.model, QUERY_PREFIX + text)
            vecs = d.get("embeddings") or []
            if not vecs:
                out["rows"].append({"id": item_id, "round": rnd, "error": "no embeddings in the reply"})
                continue
            out["rows"].append({"id": item_id, "round": rnd, "wall_s": round(c1 - c0, 4),
                                "total_ms": round(float(d.get("total_duration") or 0) / 1e6, 1),
                                "prompt_eval_count": d.get("prompt_eval_count")})
            if item_id not in out["vectors"]:
                out["vectors"][item_id] = vecs[0]
                out["dims"] = len(vecs[0])
            calls += 1
            if rnd <= a.rounds:
                marker(a.out, item_id, rnd)
        print("round %d done: %d calls, loop %.1f s" % (rnd, calls, time.time() - loop_t0), flush=True)
    loop_t1 = time.time()
    time.sleep(1.0)
    meter.stop()
    loop_j = meter.joules(loop_t0, loop_t1) if meter.source else None
    walls = [r["wall_s"] for r in out["rows"] if "wall_s" in r]
    out["summary"] = {"calls": calls, "rounds": rnd, "loop_s": round(loop_t1 - loop_t0, 2), "loop_j": loop_j,
                      "j_per_call": round(loop_j / calls, 2) if (loop_j is not None and calls) else None,
                      "s_per_call_median": round(statistics.median(walls), 4) if walls else None,
                      "s_per_call_mean": round(sum(walls) / len(walls), 4) if walls else None,
                      "meter": meter.source, "errors": sum(1 for r in out["rows"] if "error" in r),
                      "verdict": "%d calls, median %.1f ms, %s J per call (%s)" % (
                          calls, 1000 * statistics.median(walls) if walls else float("nan"),
                          "%.1f" % (loop_j / calls) if (loop_j is not None and calls) else "unmetered",
                          meter.source or "no meter")}
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=1)
    print(out["summary"]["verdict"], flush=True)
    return 0 if calls and not out["summary"]["errors"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
