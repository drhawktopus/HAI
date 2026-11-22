r"""MASKED INPAINT renderer — regenerate only a masked region of an existing image.

Usage: p20_inpaint_edit.py <source_image> <output_prefix> <prompt> <mask_json>
                           [seed] [steps] [guidance] [denoise] [loras_json] [unet_name]

  mask_json   {"box": [x0, y0, x1, y1], "feather": 24}  — box in NORMALIZED coords (0-1) of the
              region to REGENERATE, feather in pixels. Normalized so one spec fits any source size.
              Supports file path via {"file": "<abs path>"}.
  denoise     0.0-1.0, default 0.35. How much the masked region is rewritten; outside the mask the
              pixels are mathematically untouched.
  unet_name   Default Kontext UNet. Pass model name (e.g. flux1-dev-fp8) to use a different one.

Exit: 0 rendered, 1 failed."""
import json
import os
import shutil
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8189"
import foundry_paths as P  # noqa: E402
COMFY_INPUT = P.COMFY_INPUT   # a setting: this box's ComfyUI input folder
DEFAULT_UNET = "flux1-dev-kontext_fp8_scaled.safetensors"

SOURCE = sys.argv[1]
PREFIX = sys.argv[2]
PROMPT = sys.argv[3]
MASK = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
SEED = int(sys.argv[5]) if len(sys.argv) > 5 else 42
STEPS = max(10, min(int(sys.argv[6]), 60)) if len(sys.argv) > 6 else 20
GUIDANCE = float(sys.argv[7]) if len(sys.argv) > 7 else 2.5
DENOISE = max(0.05, min(float(sys.argv[8]), 1.0)) if len(sys.argv) > 8 else 0.35
LORAS = json.loads(sys.argv[9]) if len(sys.argv) > 9 else []
UNET = sys.argv[10] if len(sys.argv) > 10 else DEFAULT_UNET

if not os.path.exists(SOURCE):
    print("FAIL source image does not exist: %s" % SOURCE)
    sys.exit(1)

os.makedirs(COMFY_INPUT, exist_ok=True)
staged_name = "%s_src%s" % (PREFIX, os.path.splitext(SOURCE)[1] or ".png")
shutil.copyfile(SOURCE, os.path.join(COMFY_INPUT, staged_name))

# ---- build the mask as an image ComfyUI can load -------------------------------------------------
# White = regenerate, black = keep. Generated here rather than in-graph so the SAME spec produces a
# byte-identical mask every run (determinism: our whole comparison method rests on it).
from PIL import Image, ImageDraw, ImageFilter

with Image.open(SOURCE) as _im:
    W, H = _im.width, _im.height
W8, H8 = max(64, (W // 8) * 8), max(64, (H // 8) * 8)

mask_name = "%s_mask.png" % PREFIX
if MASK.get("file"):
    if not os.path.exists(MASK["file"]):
        print("FAIL mask file does not exist: %s" % MASK["file"])
        sys.exit(1)
    shutil.copyfile(MASK["file"], os.path.join(COMFY_INPUT, mask_name))
else:
    box = MASK.get("box") or [0.0, 0.0, 1.0, 1.0]
    if len(box) != 4:
        print("FAIL mask box needs 4 normalized values, got: %r" % (box,))
        sys.exit(1)
    px = [int(round(box[0] * W)), int(round(box[1] * H)),
          int(round(box[2] * W)), int(round(box[3] * H))]
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rectangle(px, fill=255)
    feather = int(MASK.get("feather", 24))
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    m.save(os.path.join(COMFY_INPUT, mask_name))
    print("MASK box_norm=%s box_px=%s feather=%d of %dx%d" % (box, px, feather, W, H), flush=True)

g = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
    "2": {"class_type": "DualCLIPLoader",
          "inputs": {"clip_name1": "clip_l.safetensors",
                     "clip_name2": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "flux"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
    "8": {"class_type": "LoadImage", "inputs": {"image": staged_name}},
    # snap to /8 so the VAE is happy regardless of the source's native size
    "9": {"class_type": "ImageScale",
          "inputs": {"image": ["8", 0], "width": W8, "height": H8,
                     "upscale_method": "lanczos", "crop": "disabled"}},
    "14": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
    "15": {"class_type": "ImageScale",
           "inputs": {"image": ["14", 0], "width": W8, "height": H8,
                      "upscale_method": "lanczos", "crop": "disabled"}},
    "16": {"class_type": "ImageToMask", "inputs": {"image": ["15", 0], "channel": "red"}},
    "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["3", 0]}},
    # THE inpaint step: noise is applied ONLY inside the mask, so everything outside is preserved
    # exactly. This is what makes identity structural rather than a strength-tuning problem.
    "17": {"class_type": "SetLatentNoiseMask",
           "inputs": {"samples": ["10", 0], "mask": ["16", 0]}},
    "4": {"class_type": "CLIPTextEncode",
          "inputs": {"clip": ["2", 0], "text": str(PROMPT)[:2000]}},
    "12": {"class_type": "FluxGuidance",
           "inputs": {"conditioning": ["4", 0], "guidance": GUIDANCE}},
    "5neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
    "5": {"class_type": "KSampler",
          "inputs": {"model": ["1", 0], "positive": ["12", 0], "negative": ["5neg", 0],
                     "latent_image": ["17", 0], "seed": SEED, "steps": STEPS, "cfg": 1.0,
                     "sampler_name": "euler", "scheduler": "simple", "denoise": DENOISE}},
    "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["3", 0]}},
    "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": PREFIX}},
}

_model = ["1", 0]
for _i, _ad in enumerate(a for a in LORAS if isinstance(a, dict) and a.get("name")):
    _nid = str(20 + _i)
    g[_nid] = {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": _model, "lora_name": _ad["name"],
                          "strength_model": max(0.0, min(float(_ad.get("strength", 1.0)), 2.0))}}
    _model = [_nid, 0]
g["5"]["inputs"]["model"] = _model
print("INPAINT unet=%s denoise=%.2f steps=%d guidance=%.1f stack=%s"
      % (UNET, DENOISE, STEPS, GUIDANCE, json.dumps(LORAS)), flush=True)


def post_json(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


resp = post_json("/prompt", {"client_id": "p20_inpaint_edit", "prompt": g})
if resp.get("node_errors"):
    print("FAIL", resp["node_errors"])
    sys.exit(1)
pid = resp["prompt_id"]
print("QUEUED-INPAINT", PREFIX, pid, flush=True)
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
