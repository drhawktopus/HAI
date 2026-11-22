"""Fit probe: the largest context each model in this box's ollama store holds FULLY on the card.

    python fit_probe.py <out.json> [--models a,b] [--start N] [--host URL]

Starts at the model's tested context (from triage_local.json), then doubles until it spills (VRAM limit or
shared memory) and bisects back to the largest resident context; halves if the start already spills.
Stop at the first spill - no artificial cap. Clears the card of resident models before each probe to match
GPU run state."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gpu_spill  # noqa: E402

HOST = "http://127.0.0.1:11434"
RESOLUTION = 1024          # the bisection stops when fit and spill are this close; also the smallest context tried
BIN = os.path.dirname(os.path.abspath(__file__))


def _post(host, path, body, timeout=1800):
    req = urllib.request.Request(host + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def _get(host, path, timeout=60):
    with urllib.request.urlopen(host + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def resident(host):
    return _get(host, "/api/ps").get("models") or []


def unload_all(host):
    for m in resident(host):
        _post(host, "/api/generate", {"model": m["name"], "keep_alive": 0})
    for _ in range(30):
        if not resident(host):
            return gpu_spill.wait_released()
        time.sleep(1)
    return False


def native_max(host, model):
    info = _post(host, "/api/show", {"model": model}, timeout=60).get("model_info") or {}
    vals = [v for k, v in info.items() if k.endswith(".context_length") and isinstance(v, int)]
    return max(vals) if vals else None


def load_at(host, model, ctx):
    """Load `model` at `ctx` alone on the card; return (size, size_vram, load_s)."""
    unload_all(host)
    t0 = time.time()
    _post(host, "/api/generate", {"model": model, "prompt": "ok", "stream": False, "keep_alive": "5m",
                                  "options": {"num_ctx": int(ctx), "num_predict": 1}})
    load_s = round(time.time() - t0, 1)
    for m in resident(host):
        if m.get("name") == model or m.get("model") == model:
            return int(m.get("size") or 0), int(m.get("size_vram") or 0), load_s
    return 0, 0, load_s


def fits(size, vram, spill=None):
    """Fully on the card: ollama's size_vram AND no shared-memory spill (gpu_spill - ollama counts shared system
    memory as resident on Vulkan, so size_vram alone passed gemma3:27b at a context that ran at a third of its speed)."""
    return size > 0 and vram >= size and not gpu_spill.spilled(spill)


def search(probe, start, top=None, resolution=RESOLUTION, clamped=lambda: False):
    """Largest context for which probe(ctx) fits, starting at `start`. probe(ctx) -> bool.
    Upward, the stop is measured, never chosen: the first spill, the model's native maximum (`top`), or
    `clamped()` - the footprint stopped growing, i.e. the server capped the context itself.
    Returns (best_fit or 0, first_spill or None, contexts tried in order)."""
    tried = []

    def p(c):
        tried.append(c)
        return probe(c)

    lo, hi = 0, None                     # lo = largest known fit, hi = smallest known spill
    c = max(resolution, min(start, top) if top else start)
    if p(c):
        lo = c
        while not top or lo < top:
            c = min(lo * 2, top) if top else lo * 2
            if p(c):
                lo = c
                if clamped():
                    break
            else:
                hi = c
                break
    else:
        hi = c
        while c > resolution:
            c = max(resolution, c // 2)
            if p(c):
                lo = c
                break
            hi = c
        if not lo:
            return 0, hi, tried
    while hi is not None and hi - lo > resolution:
        mid = (lo + hi) // 2 // resolution * resolution
        if mid <= lo:
            break
        if p(mid):
            lo = mid
        else:
            hi = mid
    return lo, hi, tried


def box_start():
    # the foundry's own FLEET_HOST derivation, so the payload and the factory agree on which row is this box
    name = os.environ.get("FOUNDRY_FLEET_HOST") or socket.gethostname().split(".")[0].lower()
    try:
        with open(os.path.join(BIN, "triage_local.json"), encoding="utf-8") as f:
            row = json.load(f).get(name) or {}
    except Exception:
        row = {}
    return name, int(row.get("num_ctx") or 0)


def card_memory():
    """NVIDIA only: what the card holds before we load anything (display / Plex / other tenants)."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        used, total = [int(x) for x in out.split(",")[:2]]
        return {"used_mb": used, "total_mb": total}
    except Exception:
        return None


def main(argv):
    out_path = argv[1]
    opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    host = opt("--host", HOST)
    box, start = box_start()
    start = int(opt("--start", start) or 0)
    if not start:
        raise SystemExit("no start context: this box has no triage_local.json row and no --start was given")
    models = [m for m in (opt("--models") or "").split(",") if m] or \
        sorted(m["name"] for m in (_get(host, "/api/tags").get("models") or []))
    assert models, "no models in this box's store - nothing to probe"
    print("[fit] box=%s start=%d models=%d" % (box, start, len(models)), flush=True)
    rows, summary = [], {}
    for i, model in enumerate(models, 1):
        top = native_max(host, model)
        before = None
        sizes = []                       # (ctx, size) of fitting loads, to see the server clamp the context

        def probe(ctx, _m=model):
            nonlocal before
            unload_all(host)
            if before is None:
                before = card_memory()
            size, vram, load_s = load_at(host, _m, ctx)
            spill = gpu_spill.read()
            ok = fits(size, vram, spill)
            if ok:
                sizes.append((ctx, size))
            rows.append({"model": _m, "num_ctx": ctx, "size": size, "size_vram": vram, "fits": ok, "load_s": load_s,
                         "spill": spill if spill is not None else "could not tell"})
            print("[fit] %s @ %d: %.2f/%.2f GB on card, shared %s MB %s (%.0f s)"
                  % (_m, ctx, vram / 1e9, size / 1e9, spill["shared_mb"] if spill else "?",
                     "FITS" if ok else "SPILLS", load_s), flush=True)
            return ok

        def clamped():
            return len(sizes) >= 2 and sizes[-1][0] > sizes[-2][0] and sizes[-1][1] == sizes[-2][1]

        try:
            best, spill, tried = search(probe, start, top, clamped=clamped)
        except Exception as e:
            summary[model] = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
            print("[fit] %s ERROR %s" % (model, summary[model]["error"]), flush=True)
            continue
        summary[model] = {"max_fit_ctx": best, "first_spill_ctx": spill, "native_max": top, "start": start,
                          "tried": tried, "card_before_load": before}
        print("[fit] %d/%d models done" % (i, len(models)), flush=True)   # the foundry's COMPUTE_PROG_RE shape
        print("[fit] %s -> max fully-resident context %s (first spill %s, native max %s)"
              % (model, best or "NONE", spill, top), flush=True)
    unload_all(host)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"box": box, "start": start, "resolution": RESOLUTION, "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "summary": summary, "rows": rows}, f, indent=1)
    print("[fit] wrote %s" % out_path, flush=True)


if __name__ == "__main__":
    main(sys.argv)      # the factory appends the order name; only argv[1] and named options are read
