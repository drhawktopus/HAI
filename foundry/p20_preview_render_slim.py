r"""[P20] SLIM checkpoint preview renderer — uses a baked conditioning cache, NO Mistral load.
(Render cross-cut #3: saves ~2 min + 15.6 GB of loading per preview vs p20_preview_render.py.)
Usage: p20_preview_render_slim.py <lora_filename|none> <output_prefix> <cond_cache_name>
       [seed] [width] [height] [lora_strength] [extra_loras_json] [steps]

  lora_filename     an adapter in the FOUNDRY_LORAS folder, or "none" for a base-only render
  lora_strength     strength for that adapter (default 1.0, clamped 0.1-1.5)
  extra_loras_json  '[{"name":"concept.safetensors","strength":0.7}]' — chained AFTER the primary,
                    each with its own strength (needed to stack a capability LoRA with a person one)

Exit: 0 rendered, 1 failed."""
import json, os, sys, time, urllib.request

BASE = "http://127.0.0.1:8189"
LORA = sys.argv[1]
PREFIX = sys.argv[2]
COND = sys.argv[3]
SEED = int(sys.argv[4]) if len(sys.argv) > 4 else 42   # 42: default for comparability; callers pass explicit seeds to avoid repeat draws
WIDTH = int(sys.argv[5]) if len(sys.argv) > 5 else 1024   # scene-aware aspect (worker picks): square folds
HEIGHT = int(sys.argv[6]) if len(sys.argv) > 6 else 1024

# --- adapter stack -----------------------------------------------------------------------------
# arg7  = strength for the primary adapter (was hardcoded 1.0). Standard ComfyUI
#         LoraLoaderModelOnly knob: how strongly the adapter steers generation.
# arg8  = JSON list of ADDITIONAL adapters, [{"name": "...", "strength": 0.7}, ...], chained after
#         the primary one. Needed to stack a base-capability LoRA with a person LoRA, where the
#         balance between "this person" and "this capability" is the whole tuning problem — each
#         needs its own strength, so a single global value cannot express it.
# A generic (no-identity) render passes "none" as arg1: the stack is simply empty and the guider is
# driven straight from the base UNet — same base, encoder and sampler settings as an identity
# render, which makes it the clean control for judging how much an adapter is steering a result.
# Clamped: 0 would drop the adapter entirely (use "none" for that), >1.5 degrades the image.
_NO_LORA = str(LORA).strip().lower() in ("", "-", "none")
STRENGTH = sys.argv[7] if len(sys.argv) > 7 else 1.0
EXTRA_LORAS = json.loads(sys.argv[8]) if len(sys.argv) > 8 else []
# Clamped 10-60: below ~10 is noise, above 60 adds cost with no evidence.
STEPS = max(10, min(int(sys.argv[9]), 60)) if len(sys.argv) > 9 else 20


def _clamp(s):
    try:
        return max(0.1, min(float(s), 1.5))
    except (TypeError, ValueError):
        return 1.0


STACK = ([] if _NO_LORA else [{"name": LORA, "strength": _clamp(STRENGTH)}])
STACK += [{"name": a["name"], "strength": _clamp(a.get("strength", 1.0))}
          for a in EXTRA_LORAS if isinstance(a, dict) and a.get("name")]

g = {
    # Quant is per-GPU (e.g., Q3_K_M for 24GB, smaller for 8GB). Default ensures byte-identical Mothership; override via FOUNDRY_RENDER_UNET env var.
    "1": {"class_type": "UnetLoaderGGUF",
          "inputs": {"unet_name": os.environ.get("FOUNDRY_RENDER_UNET", "flux2-dev-Q3_K_M.gguf")}},
    "4": {"class_type": "P20LoadConditioning", "inputs": {"name": COND}},
    "5": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": 2.5}},
    "6": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
    "7": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": WIDTH, "height": HEIGHT, "batch_size": 1}},
    "8": {"class_type": "Flux2Scheduler", "inputs": {"steps": STEPS, "width": WIDTH, "height": HEIGHT}},
    "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
    "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": SEED}},
    "11": {"class_type": "BasicGuider",
           "inputs": {"model": ["1", 0], "conditioning": ["5", 0]}},   # rewired below if STACK
    "12": {"class_type": "SamplerCustomAdvanced",
           "inputs": {"noise": ["10", 0], "guider": ["11", 0], "sampler": ["9", 0],
                      "sigmas": ["8", 0], "latent_image": ["7", 0]}},
    "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["6", 0]}},
    "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": PREFIX}},
}
# Chain the adapters: UNet -> lora1 -> lora2 -> ... -> guider. An empty STACK leaves node 11 on the
# base UNet, so the no-adapter case needs no special handling. Node ids start at 20 to stay clear of
# the fixed graph above.
_model = ["1", 0]
for _i, _ad in enumerate(STACK):
    _nid = str(20 + _i)
    g[_nid] = {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": _model, "lora_name": _ad["name"],
                          "strength_model": _ad["strength"]}}
    _model = [_nid, 0]
g["11"]["inputs"]["model"] = _model
print("STACK " + json.dumps(STACK), flush=True)   # so the worker log shows what actually loaded

# Optional negative guidance branch (CFG), OFF BY DEFAULT.
#
# Flux models are distilled to consume an internal guidance embedding, not external CFG.
# Using real CFG costs a second forward pass per step (~2x latency) and risks over-saturation.
# Enable only for controlled A/B testing with baked conditions (no CLIP in this graph).
#
# Env vars:
#   P20_NEG_COND  Name of a pre-baked .cond file (empty/unset = feature off)
#   P20_NEG_CFG   CFG scale, default 2.0, clamped 1.0-6.0
NEG_COND = (os.environ.get("P20_NEG_COND") or "").strip()
if NEG_COND:
    try:
        NEG_CFG = max(1.0, min(float(os.environ.get("P20_NEG_CFG", "2.0")), 6.0))
    except (TypeError, ValueError):
        NEG_CFG = 2.0
    # FluxGuidance is applied to the POSITIVE only. It is the distilled guidance embedding, not a
    # CFG term, and putting it on both branches would double-count the steering.
    g["4n"] = {"class_type": "P20LoadConditioning", "inputs": {"name": NEG_COND}}
    g["11"] = {"class_type": "CFGGuider",
               "inputs": {"model": _model, "positive": ["5", 0], "negative": ["4n", 0],
                          "cfg": NEG_CFG}}
    print("NEGATIVE cond=%s cfg=%.2f  (~2x sampling time - CFG runs a second forward pass)"
          % (NEG_COND, NEG_CFG), flush=True)

def post_json(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)

# Hardcode node inputs (model, positive, negative, cfg) here, then verify against the live
# /object_info/CFGGuider contract. A mismatch is silent until model load, wasting a full
# GPU cycle; refusing early avoids this cost.
if NEG_COND:
    try:
        info = get_json("/object_info/CFGGuider")
        req = (info.get("CFGGuider", {}).get("input", {}) or {}).get("required", {}) or {}
        missing = [k for k in ("model", "positive", "negative", "cfg") if k not in req]
        if missing:
            print("FAIL CFGGuider does not take %s -- it takes %s. Negative branch not wired for "
                  "this ComfyUI build; refusing rather than burning a load cycle."
                  % (missing, sorted(req)))
            sys.exit(1)
    except Exception as e:
        print("FAIL could not read /object_info/CFGGuider (%s) -- refusing to guess the contract"
              % type(e).__name__)
        sys.exit(1)

resp = post_json("/prompt", {"client_id": "p20_preview_slim", "prompt": g})
if resp.get("node_errors"):
    print("FAIL", resp["node_errors"]); sys.exit(1)
pid = resp["prompt_id"]
print("QUEUED-SLIM", PREFIX, pid, flush=True)
t0 = time.time()
while True:
    time.sleep(20)
    h = get_json(f"/history/{pid}")
    if pid in h and h[pid].get("status", {}).get("completed"):
        print("DONE", h[pid]["outputs"]["14"]["images"][0]["filename"], f"({time.time()-t0:.0f}s)")
        sys.exit(0)
    if time.time() - t0 > 1500:
        print("TIMEOUT"); sys.exit(1)
