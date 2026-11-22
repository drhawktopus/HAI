"""Measures residency and speed at specified sequence lengths to validate the gpu_spill full-card rule.

Unlike fit_probe.py, which measures residency only, this tool also captures read/write rates to confirm that residency thresholds correlate with actual performance degradation. Evicts the model between windows to isolate the impact of length. Writes results to {ORDER_OUT}\\fitspeed.json.

Usage: fit_speed_probe.py <model> {ORDER_OUT} --windows 7168,14336,19968 [--predict 256] [--runs 3]"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.environ.get("FOUNDRY_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin"))
import gpu_spill  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
# A fixed passage long enough for a real read rate; the probe evicts between windows, so every window's first read starts cold.
PROMPT = " ".join("Record %04d: machine M-%05d serviced on day %d by technician T-%02d, part P-%03d replaced, meter at %d hours." % (
    i, (i * 7919) % 99991, 1 + i % 365, i % 37, (i * 13) % 997, 1000 + i * 3) for i in range(90)) + "\n\nWrite a short story about the technician who serviced the most machines."


def filled_prompt(ctx, share):
    """A prompt that fills `share` of the window, as a long chat does: a short prompt leaves most of the window's memory untouched, so its speed says little about a full one. About 45 tokens a line, measured."""
    lines = max(90, int(ctx * share / 45))
    return " ".join("Record %05d: machine M-%05d serviced on day %d by technician T-%02d, part P-%03d replaced, meter at %d hours." % (
        i, (i * 7919) % 99991, 1 + i % 365, i % 37, (i * 13) % 997, 1000 + i * 3) for i in range(lines)) + "\n\nWrite a short story about the technician who serviced the most machines."


def post(path, body, timeout=900):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def ps(model):
    with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=15) as r:
        for m in json.loads(r.read().decode()).get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return m
    return {}


def evict(model):
    post("/api/generate", {"model": model, "keep_alive": 0, "prompt": "", "stream": False})
    gpu_spill.wait_released()


def card():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,clocks.gr,power.draw,pstate", "--format=csv,noheader"],
                             capture_output=True, text=True, errors="replace", timeout=20).stdout.strip()
        return out or None
    except Exception as e:                                               # noqa: BLE001 - a missing tool is reported, it never stops the row
        return "unreadable: %s" % type(e).__name__


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("out")
    ap.add_argument("--windows", required=True)
    ap.add_argument("--predict", type=int, default=256)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--fill", type=float, default=0.0, help="fill this share of each window with the prompt, as a long chat does (0.9 = 90%%)")
    a, _rest = ap.parse_known_args(argv)                                 # the runner appends the order name: ignore it
    os.makedirs(a.out, exist_ok=True)
    rep = {"model": a.model, "windows": a.windows, "start": time.time(), "fit_target": os.environ.get("LLAMA_ARG_FIT_TARGET"),
           "rule": {"spill_min_mb": gpu_spill.SPILL_MIN_MB, "full_margin_mb": gpu_spill.FULL_MARGIN_MB}, "rows": []}
    path = os.path.join(a.out, "fitspeed.json")
    evict(a.model)
    rep["card_empty"] = {"snapshot": gpu_spill.snapshot(), "nvidia_smi": card()}
    for ctx in [int(x) for x in a.windows.split(",")]:
        evict(a.model)
        row = {"num_ctx": ctx, "runs": []}
        try:
            for i in range(a.runs):
                # the first run reads the whole prompt cold; later runs reuse it, so only their write rate is a measurement
                r = post("/api/generate", {"model": a.model, "prompt": filled_prompt(ctx, a.fill) if a.fill else PROMPT, "stream": False, "think": False, "keep_alive": "10m",
                                           "options": {"num_ctx": ctx, "num_predict": a.predict, "temperature": 0.6, "seed": 7}})
                run = {"prompt_tokens": r.get("prompt_eval_count"), "tokens": r.get("eval_count"), "done_reason": r.get("done_reason"),
                       "read_tok_s": round(r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9), 1) if r.get("prompt_eval_duration") and i == 0 else None,
                       "write_tok_s": round(r["eval_count"] / (r["eval_duration"] / 1e9), 2) if r.get("eval_duration") else None}
                row["runs"].append(run)
                if i == 0:
                    m = ps(a.model)
                    snap = gpu_spill.read()
                    row.update(size=m.get("size"), size_vram=m.get("size_vram"), resident=bool(m.get("size")) and m.get("size_vram", 0) >= m.get("size", 1),
                               snapshot=snap, rule_says_spilled=gpu_spill.spilled(snap), nvidia_smi=card())
        except Exception as e:                                           # noqa: BLE001 - the row says why, the next window still runs
            row["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        rep["rows"].append(row)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print("[fitspeed] %d resident %s rule_spilled %s runs %s %s" % (ctx, row.get("resident"), row.get("rule_says_spilled"), [x.get("write_tok_s") for x in row["runs"]], row.get("error", "")), flush=True)
    evict(a.model)
    rep["end"] = time.time()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
