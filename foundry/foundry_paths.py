"""Centralized filesystem paths and per-box settings for the Foundry.\n\nAll owned resources reside under `FOUNDRY_ROOT` (derived from an env var), enabling\nblue/green deployment without code forks. External resources (model weights, caches)\nare pinned to fixed mount points for performance and are not relocated by this module.
Addresses come from setting(): environment first, then the etc folder's lab.env, then
the default."""
import json
import os

# --- what the foundry owns ------------------------------------------------------------------------
# The factory's root: FOUNDRY_ROOT, else the folder above this module's own `bin` (the installer lays the code down as
# <root>\bin). Exported to this process's environment, so every process the factory starts (a payload, ComfyUI, the
# render worker) is handed the same root rather than guessing one.
FOUNDRY_ROOT = os.environ.get("FOUNDRY_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FOUNDRY_ROOT", FOUNDRY_ROOT)

BIN = os.path.join(FOUNDRY_ROOT, "bin")            # the live modules, nothing else
ETC = os.path.join(FOUNDRY_ROOT, "etc")            # config + launchers

# --- the ONE settings reader for this workspace ----------------------------------------------------
# Precedence: environment, then the lab.env file beside ETC, then the caller's default.
# Nothing else reads lab.env anywhere; a second reader is a second source of truth.
LAB_ENV = os.path.join(ETC, "lab.env")


def _etc_env(path=None):
    """KEY=VALUE lines from the lab.env beside ETC, read the same way dm_relay.env is read.
    The path is resolved when called (so a caller can point it elsewhere); missing/unreadable = {}.
    Read as UTF-8 with replacement so an odd byte in a value can never stop the reader."""
    out = {}
    try:
        with open(path or LAB_ENV, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                if "=" in ln:
                    k, v = ln.strip().split("=", 1)
                    if k:
                        out[k] = v
    except OSError:
        pass
    return out


def setting(name, default=""):
    """One value, one place. The environment wins; lab.env is next; `default` is the last resort.

    An empty default means "not set": a factory with no head address keeps its own queue,
    pushes nothing, and says so. A setting that carries a table is JSON."""
    raw = os.environ.get(name)
    if raw is None:
        raw = _etc_env().get(name)
    if raw is None or raw == "":
        return default
    return raw


def setting_json(name, default=None):
    """A table as a setting: JSON in the environment or in lab.env. Unset or unparseable -> default."""
    raw = setting(name, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def head_url():
    """The head this factory is linked to ('' = not linked). Every head endpoint derives from
    this one value; nothing else states the head's address."""
    return setting("HAI_URL", "").rstrip("/")


def fleet_hosts():
    """name -> address for every box this factory may address. One table per workspace: every
    consumer calls this and no file keeps its own copy. Empty means no fleet."""
    return setting_json("FLEET_HOSTS", {})


def this_box():
    """This factory's own name, as the head and the fleet know it: FOUNDRY_FLEET_HOST (the environment, then
    etc\\lab.env), else the machine's own hostname, lowercased. Never a fixed name: a box that fell back to a
    valid-looking one would impersonate another factory (its runs, its claims, its placement)."""
    return setting("FOUNDRY_FLEET_HOST", "") or __import__("socket").gethostname().split(".")[0].lower()


VAR = os.path.join(FOUNDRY_ROOT, "var")
LOG_DIR = os.path.join(VAR, "log")                 # every *.log
STATE_DIR = os.path.join(VAR, "state")             # CURRENT_RUN.json, telemetry, heartbeats
CACHE_DIR = os.path.join(VAR, "cache")             # conditioning cache and friends
BACKUP_DIR = os.path.join(FOUNDRY_ROOT, "backup")  # dated subfolders, never the working tree

JOBS = os.path.join(FOUNDRY_ROOT, "jobs")
QUEUE = os.path.join(JOBS, "queue")                # submission = drop a spec here
ACTIVE = os.path.join(JOBS, "active")              # the one being fulfilled; the card is one lane
JOB_ARCHIVE = os.path.join(JOBS, "archive")        # completed AND failed orders
JOB_STATE = os.path.join(JOBS, "state")            # *.job.state.json
JOB_LOG = os.path.join(JOBS, "log")                # *.job.foundry.log

# Products, organised by modality so a text model has an obvious home the day it exists.
OUTPUT = os.path.join(FOUNDRY_ROOT, "output")
IMAGE_LORA = os.path.join(OUTPUT, "image-lora")
TEXT_LORA = os.path.join(OUTPUT, "text-lora")
RENDERS = os.path.join(OUTPUT, "renders")          # per-order render folders

# Runtime state files (named here so nothing reconstructs them from string fragments)
CURRENT_RUN = os.path.join(STATE_DIR, "CURRENT_RUN.json")
TELEMETRY_DIR = os.path.join(STATE_DIR, "telemetry")
RESCUE_LOG = os.path.join(TELEMETRY_DIR, "stall_rescue.log")
GPU_TELEMETRY = os.path.join(TELEMETRY_DIR, "gpu_telemetry.csv")


def gpu_telemetry_live():
    """The telemetry CSV this box's logger is writing NOW. The NVIDIA logger (v3+) writes one file per day,
    `gpu_telemetry-YYYYMMDD.csv` (BigHonker, Softserve); Mothership's AMD logger writes GPU_TELEMETRY. Newest dated file
    first (set-aside `_prev-schema` copies excluded), else GPU_TELEMETRY."""
    import glob
    dated = sorted(p for p in glob.glob(os.path.join(TELEMETRY_DIR, "gpu_telemetry-????????.csv")))
    return dated[-1] if dated else GPU_TELEMETRY
COND_DIR = os.path.join(CACHE_DIR, "cond_cache")
QUEUE_LOG = os.path.join(LOG_DIR, "foundry_queue.log")
DM_ENV = os.path.join(ETC, "dm_relay.env")

# --- EXTERNAL: the engines and caches the foundry points at, as settings -------------------------
# Model weights, caches, ComfyUI and ai-toolkit own their own trees; the foundry points at them and does not manage
# them. Each is a setting in etc\lab.env (or the environment): empty = this box has none, and every feature that
# needs it stays off and says so (a render worker without a ComfyUI launcher renders nothing; a trainer without
# ai-toolkit trains nothing). Nothing here names a drive.
AI_TOOLKIT = setting("FOUNDRY_AI_TOOLKIT", "")
COMFY_ROOT = setting("FOUNDRY_COMFY_ROOT", "")
COMFY_LORAS = setting("FOUNDRY_LORAS", os.path.join(COMFY_ROOT, "models", "loras") if COMFY_ROOT else "")
COMFY_OUTPUT = setting("FOUNDRY_COMFY_OUTPUT", os.path.join(COMFY_ROOT, "output") if COMFY_ROOT else "")
COMFY_INPUT = setting("FOUNDRY_COMFY_INPUT", os.path.join(COMFY_ROOT, "input") if COMFY_ROOT else "")
COMFY_MODELS = setting("FOUNDRY_COMFY_MODELS", "")        # a models tree outside ComfyUI's own (architecture detection)
MODEL_CACHE_DIR = setting("FOUNDRY_MODEL_CACHE", "")      # the text-encoder / uint4 cache the trainer reads
VENV_PY = setting("FOUNDRY_PY", os.path.join(AI_TOOLKIT, "venv", "python.exe") if AI_TOOLKIT else "")
# The Krea 2 trainer: a current upstream ai-toolkit installed BESIDE the FLUX.2 one (that tree carries the FIX-01..09
# resume patches on an older ai-toolkit, and upgrading it in place would put the FLUX.2 runs at risk). Its own venv.
AI_TOOLKIT_KREA2 = setting("FOUNDRY_AITK_KREA2", "")
KREA2_TRAIN_DIR = setting("FOUNDRY_KREA2_TRAIN", "")
COMFY_LAUNCH = setting("FOUNDRY_COMFY_LAUNCH", "")       # this box's ComfyUI launcher; empty = this box renders nothing
RESCUE_BURST = setting("FOUNDRY_BURST", os.path.join(AI_TOOLKIT, "p20_rescue_burst.py") if AI_TOOLKIT else "")
DATASET_ROOT = setting("FOUNDRY_DATASET_ROOT", "")       # where dataset jobs and sets live (p20_dataset)
DATASET_SCRATCH = setting("FOUNDRY_DATASET_SCRATCH", "") # fast scratch for a dataset job's working files
RENDER_SLOW_TIER = setting("FOUNDRY_RENDER_SLOW_TIER", "")  # where finished renders move to; empty = they stay put
CORRIDORKEY_DIR = setting("CORRIDORKEY_DIR", "")         # the CorridorKey tree (corridorkey_*.py); empty = none here
FFMPEG_DIR = setting("FFMPEG_DIR", "")                   # the folder holding ffmpeg.exe / ffprobe.exe (CorridorKey)

# Cross-machine hold files. NOT foundry-owned -- the watchdog, the render worker and CorridorKey all
# read these, so they stay at their well-known location. Listed here so a reader can see every path
# the foundry touches in ONE file, which is the entire point of this module.
HOLD_DIR = os.path.join(os.environ.get("ProgramData") or os.path.join("C:", os.sep, "ProgramData"), "HAI")
DOWNTIME_HOLD = os.path.join(HOLD_DIR, "planned-downtime.hold")
KEEPAWAKE_HOLD = os.path.join(HOLD_DIR, "keep-awake.hold")

# Control flags the foundry owns (a trainer reads STOP_NOW; it is our signal, so it moves with us)
STOP_NOW = os.path.join(STATE_DIR, "STOP_NOW")
RESCUE_PAUSE = os.path.join(STATE_DIR, "RESCUE_PAUSE")

# Executables the foundry ships and invokes
BAKE = os.path.join(BIN, "p20_bake_cond.py")
SLIM = os.path.join(BIN, "p20_preview_render_slim.py")

OWNED = (BIN, ETC, VAR, LOG_DIR, STATE_DIR, CACHE_DIR, BACKUP_DIR, JOBS, QUEUE, ACTIVE,
         JOB_ARCHIVE, JOB_STATE, JOB_LOG, OUTPUT, IMAGE_LORA, TEXT_LORA, RENDERS, TELEMETRY_DIR,
         COND_DIR)


def ensure_tree():
    """Create every directory the foundry owns. Idempotent; safe to call on every start."""
    for d in OWNED:
        os.makedirs(d, exist_ok=True)
    return FOUNDRY_ROOT


def describe():
    """One-line-per-path dump, for verifying a deployment without guessing."""
    rows = [("FOUNDRY_ROOT", FOUNDRY_ROOT)]
    rows += [(n, v) for n, v in sorted(globals().items())
             if n.isupper() and isinstance(v, str) and n != "FOUNDRY_ROOT"]
    return "\n".join("%-16s %s" % (n, v) for n, v in rows)


if __name__ == "__main__":
    print(describe())
