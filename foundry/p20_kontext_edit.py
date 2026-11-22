import json
import os
import shutil
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8189"
import foundry_paths as P  # noqa: E402
COMFY_INPUT = P.COMFY_INPUT   # a setting: this box's ComfyUI input folder

SOURCE = sys.argv[1]
PREFIX = sys.argv[2]
INSTRUCTION = sys.argv[3]
SEED = int(sys.argv[4]) if len(sys.argv) > 4 else 42
STEPS = max(10, min(int(sys.argv[5]), 60)) if len(sys.argv) > 5 else 20
GUIDANCE = float(sys.argv[6]) if len(sys.argv) > 6 else 2.5
# arg7 = adapter stack: list of dicts like [{"name": "x.safetensors", "strength": 0.8}]
# Chained UNet -> lora1 -> lora2 -> ... -> guider; empty list leaves base UNet untouched.
LORAS = json.loads(sys.argv[7]) if len(sys.argv) > 7 else []

if not os.path.exists(SOURCE):
    print("FAIL source image does not exist: %s" % SOURCE)
    sys.exit(1)

# LoadImage resolves names against ComfyUI's input dir; stage the source there under the cell's
# own prefix so parallel cells can never collide and cleanup is greppable.
staged_name = "%s_src%s" % (PREFIX, os.path.splitext(SOURCE)[1] or ".png")
os.makedirs(COMFY_INPUT, exist_ok=True)
shutil.copyfile(SOURCE, os.path.join(COMFY_INPUT, staged_name))

# Latent canvas: match the source (snapped to /8) so the edit preserves composition instead of
# recomposing onto the #19 pipeline's fixed 1216x832 family canvas.
try:
    from PIL import Image
    with Image.open(SOURCE) as im:
        W = max(64, (im.width // 8) * 8)
        H = max(64, (im.height // 8) * 8)
except Exception:
    W = H = 1024

g = {
    "1": {"class_type": "UNETLoader",
          "inputs": {"unet_name": "flux1-dev-kontext_fp8_scaled.safetensors",
                     "weight_dtype": "default"}},
    "2": {"class_type": "DualCLIPLoader",
          "inputs": {"clip_name1": "clip_l.safetensors",
                     "clip_name2": "t5xxl_fp8_e4m3fn_scaled.safetensors",
                     "type": "flux"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
    "8": {"class_type": "LoadImage", "inputs": {"image": staged_name}},
    "9": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["8", 0]}},
    "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["3", 0]}},
    "4": {"class_type": "CLIPTextEncode",
          "inputs": {"clip": ["2", 0], "text": str(INSTRUCTION)[:2000]}},
    "11": {"class_type": "ReferenceLatent",
           "inputs": {"conditioning": ["4", 0], "latent": ["10", 0]}},
    "12": {"class_type": "FluxGuidance",
           "inputs": {"conditioning": ["11", 0], "guidance": GUIDANCE}},
    "5neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
    "13": {"class_type": "EmptySD3LatentImage",
           "inputs": {"width": W, "height": H, "batch_size": 1}},
    "5": {"class_type": "KSampler",
          "inputs": {"model": ["1", 0], "positive": ["12", 0], "negative": ["5neg", 0],
                     "latent_image": ["13", 0], "seed": SEED, "steps": STEPS, "cfg": 1.0,
                     "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
    "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["3", 0]}},
    "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": PREFIX}},
}

# Adapter chain (mirrors SLIM's): UNet -> lora1 -> ... -> sampler. Node ids from 20 to stay clear
# of the fixed graph. An empty stack leaves node "5" pointed at the bare UNet.
_model = ["1", 0]
for _i, _ad in enumerate(a for a in LORAS if isinstance(a, dict) and a.get("name")):
    _nid = str(20 + _i)
    g[_nid] = {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": _model, "lora_name": _ad["name"],
                          "strength_model": max(0.0, min(float(_ad.get("strength", 1.0)), 2.0))}}
    _model = [_nid, 0]
g["5"]["inputs"]["model"] = _model
if LORAS:
    print("KONTEXT-STACK " + json.dumps(LORAS), flush=True)


def post_json(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


resp = post_json("/prompt", {"client_id": "p20_kontext_edit", "prompt": g})
if resp.get("node_errors"):
    print("FAIL", resp["node_errors"])
    sys.exit(1)
pid = resp["prompt_id"]
print("QUEUED-KONTEXT", PREFIX, pid, flush=True)
t0 = time.time()
while True:
    time.sleep(20)
    h = get_json(f"/history/{pid}")
    if pid in h and h[pid].get("status", {}).get("completed"):
        print("DONE", h[pid]["outputs"]["7"]["images"][0]["filename"], f"({time.time()-t0:.0f}s)")
        sys.exit(0)
    if time.time() - t0 > 1500:
        print("TIMEOUT")
        sys.exit(1)
