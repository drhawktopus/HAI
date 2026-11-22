r"""Bake conditioning for a preview prompt.

Usage: p20_bake_cond.py <cache_name> <prompt text...>
Env:   P20_BAKE_ENCODER = flux2 (default) | klein

Encoder compatibility:
- .cond files are encoder-specific (flux2 uses Mistral-Small; klein uses Qwen3-4B).
- Mixing encoders causes category errors, not just degraded renders.
- Filenames include encoder suffix (<name>.{encoder}.cond) to prevent silent mismatches.

Architecture:
- Qwen3 encoder (~8 GB) is baked on Mothership (24 GB VRAM) to avoid VRAM pressure on Bighonker.
- Bighonker renders with klein (4.0 GB) + VAE (0.34 GB) using the baked .cond.
"""
import json, os, sys, time, urllib.request

# SIDECAR: where the .cond lands (P20SaveConditioning writes it server-side).
import foundry_paths as P  # noqa: E402
COND_DIR = P.COND_DIR

BASE = "http://127.0.0.1:8189"
NAME = sys.argv[1]
PROMPT = " ".join(sys.argv[2:])

ENCODER = (os.environ.get("P20_BAKE_ENCODER") or "flux2").strip().lower()
if ENCODER not in ("flux2", "klein"):
    print("FAIL unknown P20_BAKE_ENCODER %r - expected flux2 or klein" % ENCODER)
    sys.exit(1)

# klein's conditioning is NOT interchangeable with flux2's, so it gets its own cache name.
if ENCODER == "klein":
    NAME = NAME if NAME.endswith(".klein") else NAME + ".klein"
    # CLIPLoader (not ...GGUF): the Comfy-Org repackaged encoder is a .safetensors.
    _loader = {"class_type": "CLIPLoader",
               "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "flux2"}}
else:
    _loader = {"class_type": "CLIPLoaderGGUF",
               "inputs": {"clip_name": "Mistral-Small-3.2-24B-Instruct-2506-Q5_K_M.gguf",
                          "type": "flux2"}}

g = {
    "3": _loader,
    "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": PROMPT}},
    "5": {"class_type": "P20SaveConditioning", "inputs": {"conditioning": ["4", 0], "name": NAME}},
}

def post_json(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)

# Query /object_info to verify the node contract. A wrong 'type' enum value fails after
# loading the encoder (~8 GB), causing a slow, misleading bake failure.
_cls = g["3"]["class_type"]
try:
    _info = get_json("/object_info/" + _cls)
    _req = (_info.get(_cls, {}).get("input", {}) or {}).get("required", {}) or {}
    if "clip_name" not in _req or "type" not in _req:
        print("FAIL %s takes %s - not (clip_name, type). Refusing to guess." % (_cls, sorted(_req)))
        sys.exit(1)
    _names, _types = _req.get("clip_name"), _req.get("type")
    _names = _names[0] if isinstance(_names, list) and _names else []
    _types = _types[0] if isinstance(_types, list) and _types else []
    want_clip, want_type = g["3"]["inputs"]["clip_name"], g["3"]["inputs"]["type"]
    if isinstance(_names, list) and want_clip not in _names:
        print("FAIL %s is not on this server. Available: %s" % (want_clip, _names))
        sys.exit(1)
    if isinstance(_types, list) and want_type not in _types:
        print("FAIL type %r not in this build's enum %s" % (want_type, _types))
        sys.exit(1)
    print("contract ok: %s(clip_name=%s, type=%s)" % (_cls, want_clip, want_type))
except SystemExit:
    raise
except Exception as _e:
    print("FAIL could not read /object_info/%s (%s) - refusing to guess" % (_cls, type(_e).__name__))
    sys.exit(1)

resp = post_json("/prompt", {"client_id": "p20_bake", "prompt": g})
if resp.get("node_errors"):
    print("FAIL", resp["node_errors"]); sys.exit(1)
pid = resp["prompt_id"]
print("BAKE QUEUED", NAME, pid, flush=True)
t0 = time.time()
while True:
    time.sleep(10)
    h = get_json(f"/history/{pid}")
    if pid in h and h[pid].get("status", {}).get("completed"):
        # Write sidecar on success only; missing sidecar implies stale/bad bake.
        try:
            with open(os.path.join(COND_DIR, NAME + ".txt"), "w", encoding="utf-8") as _f:
                _f.write(PROMPT)
        except OSError as _e:
            print("WARNING: sidecar write failed", _e, "- this will re-bake next run")
        print("BAKED", NAME, f"({time.time()-t0:.0f}s)")
        sys.exit(0)
    if time.time() - t0 > 900:
        print("TIMEOUT"); sys.exit(1)
