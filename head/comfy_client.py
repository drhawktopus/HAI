"""ComfyUI client — talks to the headless ComfyUI on the lead.

  * SDXL ("fast") coexists with a resident LLM on the 24 GB card — no unload needed. Flux ("best")
    needs the card to itself, so callers unload the lead's LLMs first.
  * the lead sleeps; its keep-awake only watches Ollama, so `ensure_up` WoL-wakes and waits.
  * Generations poll /history, images fetch via /view, and /free releases VRAM so the card returns
    to its primary tenant (the 27B).

Quota state lives at State/images/quotas.json (per-user per-day; admins unlimited).
"""
import datetime
import json
import pathlib
import re
import time

import httpx

import config
import notes
import wol


class ComfyError(RuntimeError):
    pass


def _url(path):
    return config.COMFY_URL.rstrip("/") + path


def healthy(timeout=5):
    if not config.COMFY_URL:
        return False            # no renderer configured: nothing to be healthy
    try:
        r = httpx.get(_url("/system_stats"), timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def free_memory(unload_models=True, timeout=30):
    """Unload ComfyUI's resident models and free VRAM (POST /free). Required between a big Flux model
    (e.g. a Kontext reference pass) and an SDXL render in the same session: if Flux stays resident the
    next SDXL CheckpointLoaderSimple fails to load for want of VRAM. Fail-soft."""
    try:
        r = httpx.post(_url("/free"),
                       json={"unload_models": unload_models, "free_memory": True}, timeout=timeout)
        ok = r.status_code == 200
        notes.log(f"IMG_PIPE comfy free_memory: {'ok' if ok else r.status_code}")
        return ok
    except Exception as exc:
        notes.log(f"IMG_PIPE comfy free_memory failed (soft): {exc}")
        return False


def _magic_packet(mac=None):
    """Direct WoL broadcast fallback: wol.wake() delegates to the head sidecar, which is
    unreachable from mirror-side scripts on the failover box — where this direct broadcast is what works.
    Inside the bridge container it is a harmless no-op (bridges can't reach the LAN broadcast, which
    is why the sidecar exists). Both paths fire so callers never have to care which context they run in."""
    try:
        import socket
        mac = (mac or config.device_mac(config.LEAD_BOX)).replace(":", "").replace("-", "")
        if not mac:
            return                                  # no NIC configured: nothing to wake
        pkt = b"\xff" * 6 + bytes.fromhex(mac) * 16
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for dst in config.WOL_BROADCASTS:
            for _ in range(3):
                s.sendto(pkt, (dst, 9))
        s.close()
    except Exception:
        pass


def ensure_up(timeout=90):
    """True once the ComfyUI API answers — WoL-waking the lead AND starting ComfyUI if needed.

    ComfyUI is launched PER JOB by the P20 render worker, not a persistent service, so most of the
    time it is simply not running on an otherwise-awake box and a WOL packet alone cannot start it.
    Wake the host (harmless if already awake) and trigger the worker's own ComfyUI scheduled task —
    one launch path, no second lifecycle. Attempted twice at most: once immediately, once ~30s in,
    which covers a genuine cold wake where the first SSH lands before the box can accept it.
    """
    if not config.COMFY_URL:
        return False            # no renderer configured: nothing to wake or start
    if healthy():
        return True
    try:
        wol.wake()
    except Exception:
        pass
    _magic_packet()
    started = time.monotonic()
    deadline = started + timeout
    launches = 0
    while time.monotonic() < deadline:
        if healthy():
            return True
        elapsed = time.monotonic() - started
        if launches == 0 or (launches == 1 and elapsed > 30):
            try:
                import lead_ssh as _ms
                _ms.start_comfy()
            except Exception:
                pass
            launches += 1
        time.sleep(3)
    return False


# --- workflows -----------------------------------------------------------------------------------

# Each preset = (prompt suffix, negative additions, img2img strength). Strength stays 0.5 — higher
# denoise melts the subject; style comes from the suffix + grayscaling (prep_source), not more denoise.
STYLES = {
    "sketch": (", monochrome graphite pencil sketch, black and white drawing, hand-drawn linework,"
               " cross-hatching, pencil shading",
               "color, colored, colorful, saturated colors, photo, photorealistic", 0.5),
    "watercolor": (", soft watercolor painting, visible brush strokes, paper texture",
                   "photo, photorealistic, digital render", 0.5),
    "oil_painting": (", classical oil painting, rich impasto brushwork, canvas texture",
                     "photo, photorealistic, flat digital", 0.5),
    "anime": (", anime style, clean cel shading, expressive",
              "photorealistic, western cartoon", 0.5),
    "photo": (", photorealistic, natural lighting, high detail",
              "illustration, cartoon, painting, drawing", 0.5),
}

# Styles whose look is fundamentally colorless: their SOURCE image gets grayscaled before encoding,
# because at subject-preserving strengths (<=0.5) the sampler can't remove color the source still carries.
MONO_STYLES = {"sketch"}


def prep_source(data, style):
    """Pre-process source-image bytes for a style: monochrome styles get a grayscale pass.
    Fail-open — any problem returns the original bytes."""
    if str(style or "").strip().lower() not in MONO_STYLES:
        return data
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(data)).convert("L").convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return data


def apply_style(prompt, negative, style, strength=None):
    """Fold a preset into (prompt, negative, strength). Unknown/absent style = unchanged; an
    explicit strength always wins over the preset's."""
    preset = STYLES.get(str(style or "").strip().lower())
    if not preset:
        return prompt, negative, strength
    suffix, neg_add, preset_strength = preset
    prompt = str(prompt)[:1800] + suffix
    negative = (str(negative or "").strip().rstrip(",") + ", " + neg_add).strip(", ")
    return prompt, negative, (strength if strength is not None else preset_strength)


SIZES = {
    "square": (1024, 1024),
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "wide": (1344, 768),
    "wallpaper": (1344, 768),   # generated wide, then model-upscaled per UPSCALE_SIZES
    "ultrawide": (1536, 640),   # 21:9 ultrawide
}

UPSCALE_MODEL = "RealESRGAN_x4plus.pth"   # in the lead's ComfyUI models\upscale_models
UPSCALE_SIZES = {                         # sizes that get the 4x-then-lanczos upscale tail
    "wallpaper": (2560, 1440),
    "ultrawide": (3440, 1440),
}


def build_workflow(prompt, negative="", quality="fast", size="square", seed=None):
    """A ComfyUI /prompt graph for one txt2img generation. quality: fast=SDXL (11s warm, coexists
    with an LLM) · best=Flux dev fp8 (better prompt adherence + legible text; needs the card).
    size="wallpaper" appends a RealESRGAN model-upscale tail → 2560x1440 output."""
    w, h = SIZES.get(size, SIZES["square"])
    if seed is None:
        seed = int(time.time() * 1000) % (2 ** 31)
    flux = (quality == "best")
    ckpt = "flux1-dev-fp8.safetensors" if flux else config.IMG_SDXL_CHECKPOINT
    steps = 20 if flux else 25
    cfg = 1.0 if flux else 7.0
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": str(prompt)[:2000]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1],
                         "text": str(negative or "blurry, low quality, watermark")[:500]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "haigen"}},
    }
    if size in UPSCALE_SIZES:
        tw, th = UPSCALE_SIZES[size]
        graph["10"] = {"class_type": "UpscaleModelLoader",
                       "inputs": {"model_name": UPSCALE_MODEL}}
        graph["11"] = {"class_type": "ImageUpscaleWithModel",
                       "inputs": {"upscale_model": ["10", 0], "image": ["6", 0]}}
        graph["12"] = {"class_type": "ImageScale",
                       "inputs": {"image": ["11", 0], "width": tw, "height": th,
                                  "upscale_method": "lanczos", "crop": "center"}}
        graph["7"]["inputs"]["images"] = ["12", 0]
    return {"prompt": graph}


def upload_to_comfy(name, data):
    """Push an input image to ComfyUI so an img2img workflow can reference it by name."""
    r = httpx.post(_url("/upload/image"),
                   files={"image": (name, data, "image/png")},
                   data={"overwrite": "true"}, timeout=60)
    r.raise_for_status()
    return r.json().get("name", name)


def build_img2img_workflow(prompt, source_name, negative="", quality="fast", strength=0.6,
                           seed=None):
    """#19 P2: img2img — start from an uploaded image instead of noise. strength = how much the
    model reimagines (0.3 subtle restyle … 0.9 barely-related). Same quality tiers as txt2img."""
    if seed is None:
        seed = int(time.time() * 1000) % (2 ** 31)
    flux = (quality == "best")
    ckpt = "flux1-dev-fp8.safetensors" if flux else config.IMG_SDXL_CHECKPOINT
    steps = 20 if flux else 25
    cfg = 1.0 if flux else 7.0
    strength = min(0.95, max(0.2, float(strength)))
    return {"prompt": {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": str(prompt)[:2000]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1],
                         "text": str(negative or "blurry, low quality, watermark")[:500]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": source_name}},
        "9": {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": ["1", 2]}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["9", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": "euler", "scheduler": "normal", "denoise": strength}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "haigen"}},
    }}


# --- ControlNet structure-preserving restyle ------------------------------------------------------
# Canny edges lock the SUBJECT and composition while the sampler runs FULL denoise, so the style
# transforms completely without the identity loss that capped plain img2img at 0.5. SDXL-only: the
# union model is an SDXL controlnet, so Flux restyles fall back to img2img.
CONTROLNET_MODEL = "controlnet-union-sdxl-promax.safetensors"
CN_RESTYLE_STRENGTH = 0.85   # A/B: 0.85 cleaner + more confident linework than 0.6


def controlnet_available():
    """True when the union controlnet model is present on the ComfyUI side. Fail-closed."""
    try:
        r = httpx.get(_url("/models/controlnet"), timeout=10)
        return CONTROLNET_MODEL in (r.json() or [])
    except Exception:
        return False


def nearest_size(w, h):
    """The SIZES entry whose aspect ratio best matches w×h (for picking the output latent
    of a controlnet restyle from the source photo's shape)."""
    try:
        aspect = float(w) / float(h)
    except Exception:
        return SIZES["square"]
    return min((v for k, v in SIZES.items() if k not in UPSCALE_SIZES),
               key=lambda v: abs(v[0] / v[1] - aspect))


def build_controlnet_restyle_workflow(prompt, source_name, negative="", strength=None,
                                      width=1024, height=1024, seed=None):
    """Canny-guided txt2img: edges from the source constrain the whole diffusion; denoise stays
    1.0 so the style is total. width/height should come from nearest_size(source dims)."""
    if seed is None:
        seed = int(time.time() * 1000) % (2 ** 31)
    strength = CN_RESTYLE_STRENGTH if strength is None else min(1.0, max(0.2, float(strength)))
    return {"prompt": {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": config.IMG_SDXL_CHECKPOINT}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": str(prompt)[:2000]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1],
                         "text": str(negative or "blurry, low quality, watermark")[:500]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "LoadImage", "inputs": {"image": source_name}},
        "19": {"class_type": "ImageScale",
               "inputs": {"image": ["8", 0], "width": width, "height": height,
                          "upscale_method": "lanczos", "crop": "center"}},
        "20": {"class_type": "Canny",
               "inputs": {"image": ["19", 0], "low_threshold": 0.3, "high_threshold": 0.7}},
        "21": {"class_type": "ControlNetLoader",
               "inputs": {"control_net_name": CONTROLNET_MODEL}},
        "22": {"class_type": "SetUnionControlNetType",
               "inputs": {"control_net": ["21", 0],
                          "type": "canny/lineart/anime_lineart/mlsd"}},
        "23": {"class_type": "ControlNetApplyAdvanced",
               "inputs": {"positive": ["2", 0], "negative": ["3", 0], "control_net": ["22", 0],
                          "image": ["20", 0], "strength": strength,
                          "start_percent": 0.0, "end_percent": 1.0, "vae": ["1", 2]}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["23", 0], "negative": ["23", 1],
                         "latent_image": ["4", 0], "seed": seed, "steps": 25, "cfg": 7.0,
                         "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "haigen"}},
    }}


def image_dims(data):
    """(w, h) of image bytes via PIL; None if unreadable."""
    try:
        from io import BytesIO
        from PIL import Image
        return Image.open(BytesIO(data)).size
    except Exception:
        return None


def uploads_dir(user):
    return _images_dir() / _safe(user) / "uploads"


def latest_upload(user):
    """The user's most recent uploaded image name, or None."""
    d = uploads_dir(user)
    if not d.exists():
        return None
    files = sorted((f for f in d.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg")),
                   key=lambda f: f.name, reverse=True)
    return files[0].name if files else None


def generate(workflow, timeout=None, want_nodes=None):
    """Submit, poll to completion, return {"filename", "seconds"}. Raises ComfyError on failure.
    want_nodes=[node_id,...] instead returns {"images": {node_id: filename}, "seconds"} so a
    multi-SaveImage graph (e.g. the identity-guarded finisher's post-swap + post-fuse) can pull
    each output; missing nodes are simply absent from the map.
    Self-heals a sleeping host ONCE: an idle gap (a long upstream phase, a quiet stretch before a
    user's ask) can let the lead reach its unattended-sleep window, so a connect failure triggers
    ensure_up (WoL + wait) and one resubmit before giving up."""
    timeout = timeout or config.IMG_GEN_TIMEOUT
    started = time.monotonic()
    pid = None
    for attempt in (1, 2):
        try:
            r = httpx.post(_url("/prompt"), json=workflow, timeout=30)
            r.raise_for_status()
            pid = r.json()["prompt_id"]
            break
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            if attempt == 2 or not ensure_up(timeout=180):
                raise ComfyError(f"submit failed (host unreachable): {exc}") from exc
            notes.log("IMG_GEN comfy host was asleep -> woken, resubmitting")
        except Exception as exc:
            raise ComfyError(f"submit failed: {exc}") from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            h = httpx.get(_url(f"/history/{pid}"), timeout=10).json()
        except Exception:
            continue
        entry = h.get(pid)
        if not entry:
            continue
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            raise ComfyError("generation errored: "
                             + str(status.get("messages"))[:300])
        if status.get("completed"):
            outs = entry.get("outputs") or {}
            if want_nodes is not None:
                picked = {nid: (outs.get(nid) or {}).get("images", [{}])[0].get("filename")
                          for nid in want_nodes
                          if (outs.get(nid) or {}).get("images")}
                if picked:
                    return {"images": picked,
                            "seconds": round(time.monotonic() - started, 1)}
                raise ComfyError("completed but wanted nodes produced no image output")
            for out in outs.values():
                for img in out.get("images") or []:
                    return {"filename": img["filename"],
                            "seconds": round(time.monotonic() - started, 1)}
            raise ComfyError("completed but produced no image output")
    raise ComfyError(f"timed out after {timeout}s")


def fetch(filename):
    r = httpx.get(_url("/view"), params={"filename": filename, "type": "output"}, timeout=60)
    r.raise_for_status()
    return r.content


# Keep connect timeout low: probes run on every scheduler placement. A slow timeout (e.g. 3s when SYN is dropped) causes significant latency. 250ms is sufficient for local LAN connections.
RENDER_PROBE_CONNECT_S = 0.25


def render_busy():
    """True while ComfyUI is actively rendering (its queue has a running job). Used by the brain
    router: when the lead's card is mid-render, chat should wait out the brain box's WoL wake (seconds)
    instead of loading a brain onto the contested GPU. Fail-soft False — an unreachable ComfyUI is
    not a busy one."""
    try:
        q = httpx.get(_url("/queue"), timeout=httpx.Timeout(3.0, connect=RENDER_PROBE_CONNECT_S)).json()
        if bool(q.get("queue_running")):
            return True
    except Exception:
        pass
    # The identity pipeline renders on :8189 and also TRAINS for hours — both own the lead's card
    # as hard as a plain render, so both must be visible here or the router will load a brain onto a
    # card mid-QLoRA. The render worker heartbeats what the card is doing; trust it.
    #
    # STALENESS IS NOT FREEDOM: a stale heartbeat that last said BUSY still counts as busy — a
    # multi-hour training run does not end because the process reporting on it died, and a model
    # loaded onto that card mid-QLoRA poisons the context (the collision class). A stale heartbeat
    # that last said FREE stays fail-soft, so a dead worker cannot block the lead forever.
    BUSY = ("training", "training-pause", "rendering", "finishing")
    try:
        import p20_queue
        st, age = p20_queue.gpu_status_any()
        if st and st.get("state") in BUSY:
            # A STALE busy claim is only trustworthy while the reporter might plausibly just be
            # lagging. Beyond a bounded window, cross-check the authoritative pointer: the run the
            # factory DECLARES via POST /fleet/run-state (scheduler.stated_run) — which keeps a killed
            # worker from marking the card busy for hours while it idles.
            if age is not None and age <= 900:
                return True
            if age is not None and age <= 86400:
                try:
                    if _training_pointer_live():
                        return True
                except Exception:
                    return True      # cannot verify -> assume the run is live (fail SAFE for GPUs)
    except Exception:
        pass
    return False


def _training_pointer_live():
    """Is a run actually live, per the run the factory DECLARES (fleet-run-state, read by
    scheduler.stated_run)? Used to sanity-check a STALE busy heartbeat. Fail-safe: unreadable state ->
    assume a run IS live (do not free a card on a failed probe)."""
    try:
        import scheduler
        rec = scheduler.stated_run()
        return bool(rec) and (rec.get("state") or "").strip().lower() in ("training", "warming", "rendering")
    except Exception:
        return True


def free_vram():
    """Release ComfyUI's models so the card goes back to the LLMs. Fail-soft."""
    try:
        httpx.post(_url("/free"), json={"unload_models": True, "free_memory": True}, timeout=15)
    except Exception:
        pass


def unload_lead_llms():
    """Ask Ollama on the lead to drop resident models (the Flux prelude). Fail-soft — worst
    case ComfyUI's own memory manager offloads more slowly."""
    base = config.HEAVY_HOST.rstrip("/")
    try:
        import ollama_client
        ps = httpx.get(base + "/api/ps", timeout=10).json()
        for m in ps.get("models") or []:
            with ollama_client.track(base, m["name"], purpose="unload before render", user="system", kind="unload"):
                httpx.post(base + "/api/generate",
                           json={"model": m["name"], "keep_alive": 0}, timeout=30)
    except Exception:
        pass


# --- storage + quotas ----------------------------------------------------------------------------

def _images_dir():
    return pathlib.Path(config.STATE_DIR) / "images"


def _safe(part):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(part or ""))[:60]


def save_image(user, prompt, data):
    """Store the PNG under State/images/<user>/ and return its user-relative name."""
    d = _images_dir() / _safe(user)
    d.mkdir(parents=True, exist_ok=True)
    slug = _safe(re.sub(r"\s+", "-", str(prompt))[:40]).strip("-_") or "image"
    name = f"{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}-{slug}.png"
    (d / name).write_bytes(data)
    return name


def quota_state():
    f = _images_dir() / "quotas.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


BEST_QUOTA_COST = 3


def quota_remaining(user):
    """Generation UNITS left today (fast=1, best=3): admins and IMG_UNLIMITED_USERS are unlimited
    (None), everyone else gets IMG_GEN_DAILY_QUOTA units. The ghost `test` user is exempt — it runs
    automated probes, never a real person. IMG_UNLIMITED_USERS is quota exemption WITHOUT admin powers."""
    if user in config.ADMIN_USERS or user in config.IMG_UNLIMITED_USERS or user == "test":
        return None
    today = f"{datetime.date.today():%Y-%m-%d}"
    used = quota_state().get(today, {}).get(str(user), 0)
    return max(0, config.IMG_GEN_DAILY_QUOTA - used)


def record_use(user, units=1):
    today = f"{datetime.date.today():%Y-%m-%d}"
    state = {today: quota_state().get(today, {})}   # rolls old days off naturally
    state[today][str(user)] = state[today].get(str(user), 0) + int(units)
    f = _images_dir() / "quotas.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=1), encoding="utf-8")


def refund_use(user, units):
    """Give quota units back after a render failure that was not the user's fault. Floor at 0 — a
    refund can never mint extra daily quota."""
    today = f"{datetime.date.today():%Y-%m-%d}"
    state = {today: quota_state().get(today, {})}
    state[today][str(user)] = max(0, state[today].get(str(user), 0) - int(units))
    f = _images_dir() / "quotas.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=1), encoding="utf-8")
