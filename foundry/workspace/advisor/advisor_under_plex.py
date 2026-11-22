"""Advisor candidates' footprint on the GPU, measured at a request-sized context.

Each candidate runs the 48 requests on the card AS IT IS (no synthetic load). nvidia-smi (VRAM, encoder, decoder)
and Tautulli's transcode count are sampled every 2 s; the reduce checks the footprint against Plex's MEASURED band
(median, p90, max of transcode seconds). A real transcode that happens to overlap is recorded, never required.

Passes: `idle` (default) runs whenever no transcode is in flight; `plex` (validation only) requires
--min-transcodes in flight for the whole run.

Residency refusal (rc 2) is RECORDED as "does not fit" and never propagated (rc 2 to the foundry means
hold-and-retry).

Deliverables: <out_dir>/<pass>/<model>/out.json, <out_dir>/summary.json (fit, rc, transcodes, encoder/decoder,
VRAM), progress markers in <out_dir>/progress/."""
import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
INFER = os.path.join(os.path.dirname(os.path.dirname(HERE)), "bin", "infer_ollama.py")   # {FOUNDRY_ROOT}\bin
_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, _ROOT)
import foundry_paths as _P             # this workspace's ONE settings reader
KIRK = _P.head_url()
POLL_S = 10.0
SAMPLE_S = 2.0
REFUSED_RC = 2


def plex_transcodes(kirk):
    """Tautulli's live transcode count via Kirk, or None when it could not be asked (never read as idle)."""
    try:
        with urllib.request.urlopen(kirk + "/plex", timeout=10) as r:
            a = json.load(r)
        if not isinstance(a, dict) or "transcodes" not in a:
            return None
        return int(a.get("transcodes") or 0)
    except Exception:                                                   # noqa: BLE001
        return None


def gpu_sample():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.encoder,utilization.decoder,memory.used",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        enc, dec, mem = [float(x.strip()) for x in out.strip().splitlines()[0].split(",")]
        return {"enc": enc, "dec": dec, "vram_mb": mem}
    except Exception:                                                   # noqa: BLE001
        return None


class Sampler(threading.Thread):
    def __init__(self, kirk, plex_fn=plex_transcodes, gpu_fn=gpu_sample, every=SAMPLE_S):
        super().__init__(daemon=True)
        self.kirk, self.plex_fn, self.gpu_fn, self.every = kirk, plex_fn, gpu_fn, every
        self.stop, self.transcodes, self.gpu = threading.Event(), [], []

    def run(self):
        while not self.stop.is_set():
            t = self.plex_fn(self.kirk)
            if t is not None:
                self.transcodes.append(t)
            g = self.gpu_fn()
            if g:
                self.gpu.append(g)
            self.stop.wait(self.every)

    def summary(self):
        def stat(key):
            v = [g[key] for g in self.gpu]
            return {"max": max(v), "median": statistics.median(v)} if v else None
        return {"transcodes": {"min": min(self.transcodes), "max": max(self.transcodes), "samples": len(self.transcodes)}
                if self.transcodes else None,
                "enc": stat("enc"), "dec": stat("dec"), "vram_mb": stat("vram_mb")}


def run_candidate(items, out_dir, model, num_ctx, kirk):
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "out.json")
    s = Sampler(kirk)                   # looked up at call time (module attribute), so a test can replace it
    s.start()
    t0 = time.time()
    r = subprocess.run([sys.executable, "-u", INFER, items, out, "--model", model, "--rounds", "1", "--num-ctx",
                        str(num_ctx), "--temperature", "0.0", "--seed-base", "1"], capture_output=True, text=True)
    s.stop.set()
    s.join(5)
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-3:]
    cell = {"model": model, "num_ctx": num_ctx, "rc": r.returncode, "fit": r.returncode != REFUSED_RC,
            "ok": r.returncode == 0, "wall_s": round(time.time() - t0, 1), "out": out if r.returncode == 0 else None,
            "tail": tail, "during": s.summary()}
    print("[advisor] %s rc=%d fit=%s wall=%.0fs during=%s" % (model, r.returncode, cell["fit"], cell["wall_s"],
                                                              json.dumps(cell["during"])[:160]), flush=True)
    return cell


def condition(pass_name, transcodes, min_t):
    if transcodes is None:
        return False
    return transcodes == 0 if pass_name == "idle" else transcodes >= min_t


def clean(pass_name, cell, min_t):
    """The cell's window held for the whole run: idle saw no transcode; plex never dropped below the minimum."""
    d = (cell.get("during") or {}).get("transcodes")
    if not d:
        return False
    return d["max"] == 0 if pass_name == "idle" else d["min"] >= min_t


def parse(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("items")
    ap.add_argument("out_dir")
    ap.add_argument("--models", required=True)
    ap.add_argument("--num-ctx", type=int, required=True)
    ap.add_argument("--kirk", default=KIRK)
    ap.add_argument("--min-transcodes", type=int, default=1)
    ap.add_argument("--passes", default="idle", help="comma list of idle|plex; default idle only (no waiting on Plex)")
    # the foundry appends the ORDER NAME after the spec's args to every compute payload (p20_foundry compute spawn);
    # argparse's own exit code for an unknown argument is 2 = the foundry's REFUSED (hold-and-retry) code
    ap.add_argument("order_name", nargs="?", default="")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    models = [m for m in a.models.split(",") if m]
    passes = [p for p in a.passes.split(",") if p]
    assert passes and all(p in ("idle", "plex") for p in passes), passes
    prog = os.path.join(a.out_dir, "progress")
    os.makedirs(prog, exist_ok=True)
    cells = {p: {} for p in passes}
    waited = {p: 0.0 for p in passes}
    print("[advisor] %d candidates x %s at num_ctx %d; Plex signal %s/plex" % (len(models), passes, a.num_ctx, a.kirk),
          flush=True)
    while True:
        todo = {p: [m for m in models if not (m in cells[p] and clean(p, cells[p][m], a.min_transcodes))]
                for p in cells}
        if not any(todo.values()):
            break
        t = plex_transcodes(a.kirk)
        ready = [p for p in passes if todo[p] and condition(p, t, a.min_transcodes)]
        if not ready:
            for p in passes:
                if todo[p]:
                    waited[p] += POLL_S
            if int(sum(waited.values())) % 60 == 0:
                print("[advisor] waiting: transcodes=%s; left %s" % (t, {p: v for p, v in todo.items() if v}), flush=True)
            time.sleep(POLL_S)
            continue
        p = ready[0]
        m = todo[p][0]
        cell = run_candidate(a.items, os.path.join(a.out_dir, p, m.replace(":", "-").replace("/", "_")), m, a.num_ctx,
                             a.kirk)
        cell["window_held"] = clean(p, cell, a.min_transcodes)
        prior = cells[p].get(m)
        cells[p][m] = cell if (prior is None or cell["window_held"]) else prior
        if cell["window_held"]:
            open(os.path.join(prog, "%s-%s.done" % (p, m.replace(":", "-").replace("/", "_"))), "w").close()
        else:
            print("[advisor] %s/%s: the window did not hold (transcodes %s) - will re-run when it is back"
                  % (p, m, (cell.get("during") or {}).get("transcodes")), flush=True)
        with open(os.path.join(a.out_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump({"num_ctx": a.num_ctx, "min_transcodes": a.min_transcodes, "waited_s": waited, "cells": cells}, f,
                      indent=1)
    print("[advisor] complete: %s; waited %s" % ({p: len(c) for p, c in cells.items()}, waited), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
