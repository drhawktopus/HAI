r"""FLUX2-Native Masked Inpainting (SLIM path).

Runs the same mask machinery as p20_inpaint_edit.py but uses SLIM's Flux.2 pipeline
(GGUF UNet, baked P20 conditioning, BasicGuider, SamplerCustomAdvanced). The face under
the mask is never regenerated, preserving identity regardless of LoRA strength inside the mask.

Mechanism:
  Partial denoise is achieved by truncating the sigma schedule via SplitSigmasDenoise,
  not a direct `denoise` knob. SamplerCustomAdvanced noises the VAE-encoded source to the
  first remaining sigma, scoped to the mask only by SetLatentNoiseMask.

Usage:
  p20_inpaint_slim.py <source_image> <output_prefix> <cond_cache_name> <mask_json>
                      [seed] [steps] [guidance] [denoise] [lora|none] [strength] [extra_loras_json]

Args:
  mask_json    JSON dict with 'box' [x0,y0,x1,y1] normalized 0-1 and 'feather',
               or {'file': '<abs path>'}. Deterministic generation matches p20_inpaint_edit.py.
  denoise      0.05-1.0, default 0.60. Note: FLUX2-native capability requires
               higher denoise than community bases (typically >0.3).
  lora/strength/extra_loras_json  SLIM adapter stack args. See p20_preview_render_slim.py.
                                  'none' = base-only inside the mask.

Exit: 0 on success, 1 on failure."""
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
COND = sys.argv[3]
MASK = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
SEED = int(sys.argv[5]) if len(sys.argv) > 5 else 42
STEPS = max(10, min(int(sys.argv[6]), 60)) if len(sys.argv) > 6 else 20
GUIDANCE = float(sys.argv[7]) if len(sys.argv) > 7 else 2.5
DENOISE = max(0.05, min(float(sys.argv[8]), 1.0)) if len(sys.argv) > 8 else 0.60
LORA = sys.argv[9] if len(sys.argv) > 9 else "none"
STRENGTH = sys.argv[10] if len(sys.argv) > 10 else 1.0
EXTRA_LORAS = json.loads(sys.argv[11]) if len(sys.argv) > 11 else []

if not os.path.exists(SOURCE):
    print("FAIL source image does not exist: %s" % SOURCE)
    sys.exit(1)


def _clamp(s):
    try:
        return max(0.1, min(float(s), 1.5))
    except (TypeError, ValueError):
        return 1.0


_NO_LORA = str(LORA).strip().lower() in ("", "-", "none")
STACK = ([] if _NO_LORA else [{"name": LORA, "strength": _clamp(STRENGTH)}])
STACK += [{"name": a["name"], "strength": _clamp(a.get("strength", 1.0))}
          for a in EXTRA_LORAS if isinstance(a, dict) and a.get("name")]

os.makedirs(COMFY_INPUT, exist_ok=True)
staged_name = "%s_src%s" % (PREFIX, os.path.splitext(SOURCE)[1] or ".png")
shutil.copyfile(SOURCE, os.path.join(COMFY_INPUT, staged_name))

# ---- deterministic mask (same code shape as p20_inpaint_edit.py: byte-identical per spec) -------
from PIL import Image, ImageDraw, ImageFilter

with Image.open(SOURCE) as _im:
    W, H = _im.width, _im.height
# Flux.2 VAE latents are /16; snap the working canvas so encode/decode round-trips cleanly.
W16, H16 = max(64, (W // 16) * 16), max(64, (H // 16) * 16)

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
    "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux2-dev-Q3_K_M.gguf"}},
    "4": {"class_type": "P20LoadConditioning", "inputs": {"name": COND}},
    "5": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": GUIDANCE}},
    "6": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
    "8": {"class_type": "LoadImage", "inputs": {"image": staged_name}},
    "9": {"class_type": "ImageScale",
          "inputs": {"image": ["8", 0], "width": W16, "height": H16,
                     "upscale_method": "lanczos", "crop": "disabled"}},
    "14": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
    "15": {"class_type": "ImageScale",
           "inputs": {"image": ["14", 0], "width": W16, "height": H16,
                      "upscale_method": "lanczos", "crop": "disabled"}},
    "16": {"class_type": "ImageToMask", "inputs": {"image": ["15", 0], "channel": "red"}},
    "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["6", 0]}},
    "17": {"class_type": "SetLatentNoiseMask",
           "inputs": {"samples": ["10", 0], "mask": ["16", 0]}},
    "18": {"class_type": "Flux2Scheduler",
           "inputs": {"steps": STEPS, "width": W16, "height": H16}},
    # Partial denoise on the custom-sampler path: keep only the LOW tail of the schedule.
    "19": {"class_type": "SplitSigmasDenoise",
           "inputs": {"sigmas": ["18", 0], "denoise": DENOISE}},
    "9k": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
    "10n": {"class_type": "RandomNoise", "inputs": {"noise_seed": SEED}},
    "11": {"class_type": "BasicGuider",
           "inputs": {"model": ["1", 0], "conditioning": ["5", 0]}},   # rewired below if STACK
    "12": {"class_type": "SamplerCustomAdvanced",
           "inputs": {"noise": ["10n", 0], "guider": ["11", 0], "sampler": ["9k", 0],
                      "sigmas": ["19", 1], "latent_image": ["17", 0]}},
    "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["6", 0]}},
    "sv": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": PREFIX}},
}
_model = ["1", 0]
for _i, _ad in enumerate(STACK):
    _nid = str(20 + _i)
    g[_nid] = {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": _model, "lora_name": _ad["name"],
                          "strength_model": _ad["strength"]}}
    _model = [_nid, 0]
g["11"]["inputs"]["model"] = _model
print("INPAINT-SLIM denoise=%.2f steps=%d guidance=%.1f stack=%s cond=%s"
      % (DENOISE, STEPS, GUIDANCE, json.dumps(STACK), COND), flush=True)


def post_json(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


resp = post_json("/prompt", {"client_id": "p20_inpaint_slim", "prompt": g})
if resp.get("node_errors"):
    print("FAIL", resp["node_errors"])
    sys.exit(1)
pid = resp["prompt_id"]
print("QUEUED-INPAINT-SLIM", PREFIX, pid, flush=True)
t0 = time.time()
while True:
    time.sleep(20)
    h = get_json(f"/history/{pid}")
    if pid in h and h[pid].get("status", {}).get("completed"):
        print("DONE", h[pid]["outputs"]["sv"]["images"][0]["filename"], f"({time.time()-t0:.0f}s)")
        sys.exit(0)
    if time.time() - t0 > 1500:
        print("TIMEOUT")
        sys.exit(1)
