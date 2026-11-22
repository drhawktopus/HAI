r"""[P20] Krea 2 renderer - text-to-image on Krea 2 Turbo through the foundry's ComfyUI (:8189).
Usage: p20_krea_render.py <lora_filename|none> <output_prefix> <prompt> [seed] [width] [height] [lora_strength] [second_adapter_strength]

  lora_filename  an adapter in the FOUNDRY_LORAS folder trained on KREA 2 (a FLUX.2 adapter does not load
                 into this network), or "none" for a base-only render
  prompt         the prompt text itself: Krea encodes in-graph with its Qwen3-VL encoder, so there is no .cond bake

The graph is ComfyUI's own Krea 2 Turbo template (comfyui_workflow_templates `image_krea2_turbo_t2i`): 8 steps,
CFG 1, euler/simple, the positive zeroed for the negative. The template's prompt-enhancer branch is left out - the
user's prompt goes in as written.

Exit: 0 rendered, 1 failed."""
import hashlib, json, math, os, shutil, sys, time, urllib.error, urllib.request

# These filenames are the single source of truth: p20_foundry's render.krea2 detection imports them, so the capability a box declares and the files this graph loads cannot disagree. Use the fp8_scaled variant (13.1 GB) instead of the bf16 (26.3 GB); the latter exceeds the 24 GB VRAM limit of the 7900 XTX and causes memory-bound stalls.
UNET = os.environ.get("FOUNDRY_KREA2_UNET", "krea2_turbo_fp8_scaled.safetensors")
CLIP = os.environ.get("FOUNDRY_KREA2_CLIP", "qwen3vl_4b_bf16.safetensors")
VAE = os.environ.get("FOUNDRY_KREA2_VAE", "qwen_image_vae.safetensors")
BASE = os.environ.get("FOUNDRY_COMFY", "http://127.0.0.1:8189")
STEPS = 8          # the Turbo distill's own step count (template)
EDIT_ADAPTER = "krea2_identity_edit_v2.safetensors"
EDIT_SHA256 = "c53843f9451963a6cb3c22dc4975205056b93a6575fb9347dd2bc6e055ef9877"


# The optional second adapter: a file in the loras folder named by the KREA2_SECOND_ADAPTER setting, loaded only when
# its bytes match KREA2_SECOND_ADAPTER_SHA256. Unset means no render loads one.
try:
    import foundry_paths as _P
    _setting = _P.setting
except ImportError:                  # noqa: BLE001 - run outside the foundry tree: the environment alone
    _P = None
    def _setting(name, default=""):
        return os.environ.get(name, default)


def _loras_dir():
    """The loras folder at call time: FOUNDRY_LORAS (environment, then lab.env), else the one derived from COMFY_ROOT."""
    return _setting("FOUNDRY_LORAS", "") or (_P.COMFY_LORAS if _P else "")


def _comfy_input_dir():
    return _setting("FOUNDRY_COMFY_INPUT", "") or (_P.COMFY_INPUT if _P else "")
SECOND_ADAPTER = _setting("KREA2_SECOND_ADAPTER", "")
SECOND_ADAPTER_SHA256 = _setting("KREA2_SECOND_ADAPTER_SHA256", "")


def adapter_strength(value):
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 5:
        raise ValueError("Krea adapter strength must be a finite number between 0 and 5")
    return value


def verify_second_adapter():
    if not (SECOND_ADAPTER and SECOND_ADAPTER_SHA256):
        raise ValueError("no second adapter is configured (KREA2_SECOND_ADAPTER and KREA2_SECOND_ADAPTER_SHA256)")
    path = os.path.join(_loras_dir(), SECOND_ADAPTER)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != SECOND_ADAPTER_SHA256:
        raise ValueError("Krea second adapter SHA256 mismatch; refusing an unverified adapter")


def graph(lora, prefix, prompt, seed=42, width=1024, height=1024, strength=1.0, second_strength=0.0,
          reference_image=None):
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "krea2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "seed": seed, "steps": STEPS, "cfg": 1.0, "sampler_name": "euler",
                         "scheduler": "simple", "positive": ["4", 0], "negative": ["5", 0],
                         "latent_image": ["6", 0], "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }
    second_strength = adapter_strength(second_strength)
    if second_strength:
        g["21"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": SECOND_ADAPTER,
            "strength_model": second_strength}}
        g["7"]["inputs"]["model"] = ["21", 0]
    if str(lora).strip().lower() not in ("", "-", "none"):
        g["20"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": g["7"]["inputs"]["model"], "lora_name": lora,
                              "strength_model": adapter_strength(strength)}}
        g["7"]["inputs"]["model"] = ["20", 0]
    if reference_image:
        if "20" in g:
            raise ValueError("Choose reference identity or trained identity for this graph")
        g["2"]["inputs"]["device"] = "default"  # normal GPU/offload placement; CPU FP16 was prohibitively slow
        g["6"]["class_type"] = "EmptySD3LatentImage"
        g["30"] = {"class_type": "LoadImage", "inputs": {"image": reference_image}}
        g["31"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["30", 0], "vae": ["3", 0]}}
        g["32"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": g["7"]["inputs"]["model"], "lora_name": EDIT_ADAPTER, "strength_model": 1.0}}
        g["33"] = {"class_type": "Krea2EditModelPatch", "inputs": {
            "model": ["34", 0], "source_latent": ["34", 3], "source_image": ["30", 0],
            "vae": ["3", 0], "target_latent": ["6", 0], "fit_mode": "fit",
            "ref_boost": 2.0, "ref_boost_a": 1.0,
            "ref_position": "beside (right of target)", "ref_position_a": "aligned (trained)"}}
        g["4"] = {"class_type": "Krea2EditGroundedEncode", "inputs": {
            "clip": ["2", 0], "prompt": prompt, "image": ["30", 0], "grounding_px": 768}}
        g["5"] = {"class_type": "Krea2EditGroundedEncode", "inputs": {
            "clip": ["2", 0], "prompt": "", "image": ["30", 0], "grounding_px": 768}}
        # Explicit dependencies ensure BOTH image-grounded encodes finish before
        # freeing their 8+ GB GPU weights and preparing the diffusion sampler.
        g["34"] = {"class_type": "P20ReferenceMemoryBarrier", "inputs": {
            "model": ["32", 0], "positive": ["4", 0], "negative": ["5", 0],
            "source_latent": ["31", 0]}}
        g["7"]["inputs"]["positive"] = ["34", 1]
        g["7"]["inputs"]["negative"] = ["34", 2]
        g["7"]["inputs"]["model"] = ["33", 0]
    else:
        # Retain the reference route's memory barrier to prevent encoder weight spills.
        # Empty canvas passes through unchanged; no reference edit is applied.
        g["34"] = {"class_type": "P20ReferenceMemoryBarrier", "inputs": {
            "model": g["7"]["inputs"]["model"], "positive": ["4", 0],
            "negative": ["5", 0], "source_latent": ["6", 0]}}
        g["7"]["inputs"].update(model=["34", 0], positive=["34", 1],
                                  negative=["34", 2], latent_image=["34", 3])
    return g


def stage_reference(reference, prefix):
    """Check the exact catalog source bytes, then stage under this order's own input name."""
    source = reference['path']
    with open(source, 'rb') as f:
        data = f.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != reference['sha256']:
        raise ValueError('Approved reference changed since catalog registration; refresh the catalog')
    if os.path.basename(prefix) != prefix or not prefix.replace('_', '').isalnum():
        raise ValueError('Reference staging needs a simple output prefix')
    adapter = os.path.join(_loras_dir(), EDIT_ADAPTER)
    h = hashlib.sha256()
    with open(adapter, 'rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    if h.hexdigest() != EDIT_SHA256:
        raise ValueError('Identity Edit V2 SHA256 mismatch')
    folder = _comfy_input_dir()
    os.makedirs(folder, exist_ok=True)
    name = prefix + ('_reference.png' if reference.get('kind') == 'generated_character' else '_reference.jpg')
    with open(os.path.join(folder, name), 'wb') as f:
        f.write(data)
    return name


def _post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


GONE_AFTER = 6     # consecutive REFUSED connections (one per poll) before ComfyUI counts as gone


def check(pid, get):
    """One status poll -> ("done", filename) | ("fail", why) | ("wait", None) | ("gone", why).
    A slow reply is "could not tell", never a verdict. Only a refused connection (nothing listening)
    can mean the server is gone; the caller requires several consecutive refusals to confirm."""
    try:
        h = get("/history/%s" % pid).get(pid)
        if h:
            st = h.get("status") or {}
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages") or [] if m and m[0] == "execution_error"]
                return "fail", json.dumps(msgs)[:600]
            if st.get("completed"):
                return "done", h["outputs"]["9"]["images"][0]["filename"]
        q = get("/queue")
        live = [e[1] for e in (q.get("queue_running") or []) + (q.get("queue_pending") or [])]
        if pid not in live and not get("/history/%s" % pid).get(pid):
            return "fail", "prompt %s left the queue with no history" % pid
        return "wait", None
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ConnectionRefusedError):
            return "gone", "ComfyUI refused the connection"
        return "wait", None
    except (TimeoutError, OSError):
        return "wait", None


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 1
    lora, prefix, prompt = argv[1], argv[2], argv[3]
    seed = int(argv[4]) if len(argv) > 4 else 42
    width = int(argv[5]) if len(argv) > 5 else 1024
    height = int(argv[6]) if len(argv) > 6 else 1024
    strength = float(argv[7]) if len(argv) > 7 else 1.0
    second_strength = float(argv[8]) if len(argv) > 8 else 0.0
    reference = json.loads(argv[9]) if len(argv) > 9 else None
    if second_strength:
        verify_second_adapter()
    reference_image = stage_reference(reference, prefix) if reference else None
    g = graph(lora, prefix, prompt, seed, width, height, strength, second_strength, reference_image)
    print("KREA2 identity_strength=%g second_adapter_strength=%g" % (strength, second_strength), flush=True)
    print("KREA2 unet=%s lora=%s %dx%d seed=%d" % (UNET, lora, width, height, seed), flush=True)
    # The slim renderer's client id: the render worker's progress reporter follows renders by it.
    resp = _post("/prompt", {"client_id": "p20_preview_slim", "prompt": g})
    if resp.get("node_errors"):
        print("FAIL", resp["node_errors"])
        return 1
    pid = resp["prompt_id"]
    print("QUEUED-KREA2", prefix, pid, flush=True)
    t0 = time.time()
    # Supervised by STATUS, not a clock: the prompt is done, failed, or still in ComfyUI's queue. A prompt that is
    # none of those has been dropped, and only then is it a failure. (The worker's subprocess cap is the backstop.)
    gone = 0
    while True:
        time.sleep(10)
        verdict, detail = check(pid, _get)
        gone = gone + 1 if verdict == "gone" else 0
        if verdict == "done":
            print("DONE", detail, "(%.0fs)" % (time.time() - t0))
            return 0
        if verdict == "fail" or gone >= GONE_AFTER:
            print("FAIL", detail)
            return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
