r"""P20 Foundry Runner. Session-independent training-run orchestrator.

End-to-end lifecycle: launch -> watch -> checkpoint pauses (STOP_NOW) -> resume ->
finish -> ladder renders -> register -> restore -> report. State is persisted after
every transition, allowing crash/reboot recovery without losing choreography.

Critical Constraints:
- GPU Handshake: `launch_trainer()` requires `wait_card_quiet()` (HIP context teardown)
  to prevent SetDevice/memcpy_and_sync crashes on resume.
- Checkpoint Safety: The LADDER trigger is guarded by `near_end` to prevent rendering
  against undertrained weights when emergency saves write both numbered and final files.
- Process Isolation: All child processes (trainer, ComfyUI) use SCHEDULED TASKS, not Popen.
  This prevents transitive job-object teardown in session-hosted contexts.
- State Management: Pauses are signaled via STOP_NOW flag only; never kill the trainer
  directly. The stall-rescue daemon is a separate scheduled task and must not be managed
  by this runner.
- Monitoring: Watch checkpoint FILES, not log lines. ETAs are derived from measured rates.
"""
import json
import os
import re              # module level: pick_canvas compiles its patterns at import time
import shutil
import socket
import subprocess
import sys
import time
import glob
import datetime
import urllib.request
import foundry_paths as P   # THE path source of truth; never derive one path from another
import foundry_lock         # singleton-lock liveness, shared with the worker and the daemon
import p20_status as _p20_status
import hashlib          # render_index: content key for "has this exact cell already been rendered?"
import threading        # compute orders: heartbeat on a timer thread, never inside the read loop
import types

try:
    import yaml  # ai-toolkit venv has PyYAML
except ImportError:
    yaml = None

# Reconfigure to UTF-8 with replacement errors.
# Logging must not crash the process on encoding failures (e.g., cp1252 console).
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass                     # older interpreter or a non-reconfigurable stream: not fatal

COMFY = "http://127.0.0.1:8189"
COMFY_LAUNCH = P.COMFY_LAUNCH
STOP_NOW = os.path.join(P.STATE_DIR, "STOP_NOW")
PAUSE_FLAG = os.path.join(P.STATE_DIR, "STOP_AFTER_RENDER")    # operator pause -> PauseRequested
RUNNER_LOCK = os.path.join(P.STATE_DIR, "runner.lock")          # runner singleton (daemon pattern)
RUNNER_HEARTBEAT = os.path.join(P.STATE_DIR, "runner.heartbeat")
BAKE = os.path.join(P.BIN, "p20_bake_cond.py")
SLIM = os.path.join(P.BIN, "p20_preview_render_slim.py")
KONTEXT_SLIM = os.path.join(P.BIN, "p20_kontext_edit.py")
INPAINT_SLIM = os.path.join(P.BIN, "p20_inpaint_edit.py")   # masked regeneration
INPAINT_FLUX2 = os.path.join(P.BIN, "p20_inpaint_slim.py")
KREA_RENDER = os.path.join(P.BIN, "p20_krea_render.py")      # Krea 2 base: prompt encoded in-graph, no .cond
PIXEL_HASH = os.path.join(P.BIN, "p20_pixel_hash.py")
COND_DIR = os.path.join(P.CACHE_DIR, "cond_cache")
LORAS = P.COMFY_LORAS
PY = P.VENV_PY

# Framing requires torch, which the runner process does not carry: it runs out of process through p20_framing.py on
# an interpreter found here. FOUNDRY_FRAMING_PY (a setting) names it; the runner's own interpreter is the fallback.
FRAMING_PY_CANDIDATES = [P.setting("FOUNDRY_FRAMING_PY", ""), sys.executable]


def _find_framing_py():
    """Return the first interpreter in FRAMING_PY_CANDIDATES that has BOTH
    torch and torchvision installed, or None.

    Checks site-packages on disk (not by importing). Spawning the interpreter
    to verify imports would add tens of seconds at import time, causing this
    code to be disabled and the registry to lie again.

    Both packages are required because a system with torch but no torchvision
    (known in dev environments) causes silent runtime failures."""
    for exe in FRAMING_PY_CANDIDATES:
        if not exe or not os.path.exists(exe):
            continue
        base = os.path.dirname(exe)
        for sp in (os.path.join(base, "Lib", "site-packages"),           # venv layout
                   os.path.join(os.path.dirname(base), "Lib", "site-packages")):
            if all(os.path.exists(os.path.join(sp, pkg, "__init__.py"))
                   for pkg in ("torch", "torchvision")):
                return exe
    return None


FRAMING_PY = _find_framing_py()
POLL = 20.0
# 40 minutes covers worst-case render chains (e.g., 7-checkpoint ladder ~45 min).
# Sized to prevent lapse during slow/retried renders, but bounded so crashed renders
# eventually free the card.
RENDER_CLAIM_TTL = 2400

# This is an idle watchdog, not a total-duration timeout. The supervisor kills
# only on demonstrated lack of progress (no PROGRESS event), allowing slow
# passes to complete naturally. The generous window accommodates process startup,
# torch import, and large weight downloads, which occur before the first progress
# signal.
FRAMING_IDLE_S = 600.0

# Seed must remain fixed here to preserve cache keys and ladder comparisons.
# To vary the seed (for A/B testing or replication), override it per-order/spec,
# never by changing this constant.
DEFAULT_SEED = 42
# The adapter strength renders default to. NOT 1.0 -- that is SLIM's default and it is documented
# as OVER-DRIVEN (PROJECT-20-ROADMAP finding 2; v8's ck1500 renders an oil painting at 1.0 and a
# clean figure study at 0.6 from the same weights). 0.6 is the measured operating point and the
# strength the adapter actually ships at, so it is what a preview or probe should show unless an
# order deliberately asks for something else.
SHIP_STRENGTH = 0.6
DM_ENV = os.path.join(P.ETC, "dm_relay.env")

# --- render index -----------------------------------------------------------------------------
# Renders are pure functions of (prompt, seed, checkpoint, adapter strength).
# Re-rendering existing cells wastes GPU time on the project's only bottleneck.
#
# The index keys on inputs, not filenames: identical cells may have different names
# across probes, so name-based matching would miss duplicates.
RENDER_INDEX = os.path.join(os.path.dirname(COND_DIR), "jobs", "render_index.json")


# The base UNet model is part of the render cache key; differing models produce different pixels.
# p20_preview_render_slim.py is a standalone script that reads this env var but cannot import
# this constant. Its default literal must match this one to prevent drift; see integration tests.
RENDER_UNET_DEFAULT = "flux2-dev-Q3_K_M.gguf"

# Default path for ComfyUI outputs. Per-host config prevents probe failures
# when render workers live on different machines than the foundry.
COMFY_OUTPUT_DIR = P.COMFY_OUTPUT.strip()


def render_unet():
    """The base UNet this factory renders with. One reader, so the key and the render agree."""
    return (os.environ.get("FOUNDRY_RENDER_UNET") or RENDER_UNET_DEFAULT).strip()


def render_key(text, seed, ckpt_path, strength, base_only, extras=None, steps=None,
               kontext_source=None, guidance=None, edit_spec=None, unet=None):
    """Content key for one render cell. Anything that changes the pixels must be in here.

    extras: The stacked-adapter list (SLIM arg8). Must be included because stacked cells produce different pixels than unstacked siblings with identical other inputs; omitting it causes cache collisions serving wrong-comparison results. Appended only when non-empty to maintain byte-identical keys for pre-existing entries."""
    arm = "base" if base_only else "%.2f" % (1.0 if strength is None else float(strength))
    raw = "v1|%s|%s|%s|%s" % ((text or "").strip(), seed, os.path.basename(ckpt_path or ""), arm)
    if extras:
        raw += "|extras:" + json.dumps(
            [{"name": a["name"], "strength": round(float(a.get("strength", 1.0)), 2)}
             for a in extras], sort_keys=True)
    if steps and int(steps) != 20:
        # 20 is SLIM's historical default; omitting steps=20 keeps keys byte-identical.
        raw += "|steps:%d" % int(steps)
    if kontext_source:
        # Kontext depends on the source image identity. Use basename (not path) so
        # tiering moves from F: to D: without breaking cache keys.
        raw += "|kontext:%s" % os.path.basename(kontext_source)
    if edit_spec:
        raw += "|edit:" + json.dumps(edit_spec, sort_keys=True)
    if guidance is not None and abs(float(guidance) - 2.5) > 1e-9:
        # Include non-default guidance in cache key to prevent hash collisions; 2.5 is the historical default.
        raw += "|guid:%.2f" % float(guidance)
    if unet and unet.strip() and unet.strip() != RENDER_UNET_DEFAULT:
        # Absent-or-historical-default keeps EVERY pre-existing key byte-identical, so the whole
        # index built before this term survives -- the same compatibility rule as steps/guidance.
        # A klein render on BigHonker keys differently from a flux2 render of the same cell, which
        # is the point: they are different pictures.
        raw += "|unet:%s" % unet.strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def render_index_load():
    """Fail-soft: an unreadable or corrupt index means 'nothing known', never a crash. A missing
    index must not be able to stop the factory rendering."""
    try:
        with open(RENDER_INDEX, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def render_index_put(key, order, arm, png, text):
    d = render_index_load()
    d[key] = {"order": order, "arm": arm, "file": png, "text": (text or "").strip(),
              "when": datetime.datetime.now().isoformat(timespec="seconds")}
    try:
        os.makedirs(os.path.dirname(RENDER_INDEX), exist_ok=True)
        tmp = RENDER_INDEX + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, RENDER_INDEX)
    except Exception:
        pass


def render_index_lookup(key):
    """Return a prior render for this cell, but only if its file still exists on disk. A recorded
    path whose PNG has been deleted or archived is stale -- unverifiable is treated as absent, the
    same rule as cond_needs_bake and retire_stale_embeds."""
    hit = render_index_load().get(key)
    if hit and hit.get("file") and os.path.exists(hit["file"]):
        return hit
    return None

# The head's DM relay comes from the settings reader. Empty means no relay: this factory
# sends no DMs (already fail-soft). The relay's Host header is part of its identity, so it
# is a setting too, not a literal.
DM_URL = P.setting("OVERNIGHT_DM_URL", "")
DM_FILE_URL = DM_URL.replace("/dm", "/dm-file") if DM_URL else ""
DM_HOST = P.setting("OVERNIGHT_DM_HOST", "")
RESCUE_LOG = os.path.join(P.TELEMETRY_DIR, "stall_rescue.log")
DOWNTIME_HOLD = P.DOWNTIME_HOLD   # watchdog stands by while unexpired
KEEPAWAKE_HOLD = P.KEEPAWAKE_HOLD        # box stays awake while unexpired
HOLD_HOURS = 2.0    # rolling: refreshed every poll, so a crashed run auto-expires both within 2h


def write_holds():
    """Declare 'full-card job live' to the box: planned-downtime (watchdog stands by) + keep-awake.
    Rolling expiry, refreshed from the watch loops — crash-safe by construction (auto-expiring,
    keep-awake.hold convention; see RUNBOOK + ollama-watchdog.ps1)."""
    expiry = (datetime.datetime.utcnow() + datetime.timedelta(hours=HOLD_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for p in (DOWNTIME_HOLD, KEEPAWAKE_HOLD):
        try:
            with open(p, "w", encoding="ascii") as f:
                f.write(expiry)
        except Exception:
            pass


class Job:
    def __init__(self, spec_path):
        self.spec_path = os.path.abspath(spec_path)
        with open(spec_path, "r", encoding="utf-8") as f:
            self.spec = yaml.safe_load(f) if yaml else json.load(f)
        self.name = self.spec["name"]                      # Optional because probe orders (checkpoint-only) lack launcher/log fields.
# Training orders still fail loudly at launch if launcher is missing.
        self.launcher = self.spec.get("launcher", "")      # Resolved base: `spec.base`, else `spec.register.base`, else "flux2-dev".
# Drives render path selection and gates FLUX.2-specific machinery (Mistral bake, text-embeds cache).
        self.base = str(self.spec.get("base") or (self.spec.get("register") or {}).get("base")
                        or "flux2-dev").strip().lower()
        self.log_path = self.spec.get("log", "")
        self.output_dir = self.spec.get("output_dir", "")
        self.total_steps = int(self.spec.get("total_steps", 2000))
        # preview_every must align with trainer save_every to ensure previews
        # render from existing checkpoints; mismatched cadences yield zero output.
        # Set once here so all consumers share the same resolved value.
        self._spec_preview_every = int(self.spec.get("preview_every", 500))
        self.preview_every = self._spec_preview_every
        self.preview_prompts = self.spec.get("preview_prompts", [])   # [{name, text}] name = cond cache key
        self.finish_prompts = self.spec.get("finish_prompts", [])
        self.rotate_prompts = self.spec.get("rotate_prompts", [])
        # A/B prompts rendered WITH and WITHOUT the adapter at milestones `ab_at`.
        # For base-CAPABILITY LoRA, this is the only test that matters; see render(base_only=...).
        # PROBE measures a finished checkpoint (no training): same factory/render/cleanup as TRAIN.
        self.mode = (self.spec.get("mode") or "train").strip().lower()
        # Alias: "cpu" is mapped to "compute" for backward compatibility.
        # New specs use kind="compute" with load="gpu"|"mixed"|"cpu" (default mixed).
        if self.mode == "cpu":
            self.mode = "compute"
        self.load = (self.spec.get("load") or "mixed").strip().lower()
        # yield_at: checkpoints where the lane yields to priority orders (status-based,
        # never a timer). Consumed marks live in state["yielded"] to ensure each
        # milestone fires at most once across resumes.
        self.yield_at = sorted(int(x) for x in (self.spec.get("yield_at") or []))
        self.probe_lora = self.spec.get("probe_lora") or ""
        self.probe_step = int(self.spec.get("probe_step") or 0)
        self.probe_prompts = self.spec.get("probe_prompts", [])
        self.probe_strengths = [float(x) for x in (self.spec.get("probe_strengths") or [])]
        # probe_seeds: REPLICATION axis (symmetric with probe_strengths).
        # Empty/missing implies [DEFAULT_SEED] for backward compatibility.
        # Use multiple seeds to determine if variance is an adapter property
        # or sample-specific noise.
        self.probe_seeds = [int(x) for x in (self.spec.get("probe_seeds") or [])]
        self.sweep_names = self.spec.get("sweep_names") or []
        self.ab_prompts = self.spec.get("ab_prompts", [])
        self.ab_at = [int(s) for s in (self.spec.get("ab_at") or [])]
        self.resident_server = bool(self.spec.get("resident_server", False))
        # previews_mode: 'live' or 'deferred'.
        # Deferred previews never pause the trainer; full history renders at finish
        # from saved checkpoints. Live is default.
        # NOTE: Live previews add significant latency (~8-11 min per cycle on healthy hardware),
        # but are necessary for accurate visual feedback during training.
        self.previews_mode = self.spec.get("previews", "live")
        # preview_strength: defaults to 1.0 if None, but 1.0 over-drives the adapter (smeared faces);
        # use ~0.6 to match the actual shipped operating point.
        self.preview_strength = self.spec.get("preview_strength")
        self.extra_previews = [int(x) for x in (self.spec.get("extra_previews") or [])]
        self.initiator = self.spec.get("initiator", "direct")
        self.restore = self.spec.get("restore", {})        # {enable_tasks:[], start_tasks:[], delete_files:[]}
        self.register = self.spec.get("register", {})      # proposed person-record model-entry fields
        self.state_path = os.path.splitext(self.spec_path)[0] + ".state.json"
        self.flog = os.path.splitext(self.spec_path)[0] + ".foundry.log"
        self.state = self._load_state()
        self.tie_preview_to_save_every()

    def tie_preview_to_save_every(self):
        """Sanity-check preview cadence.\n\n    Constraint: Frequent previews add significant pause overhead (approx 8 min per cycle).\n    Avoid very low `preview_every` values relative to total steps to prevent excessive training time."""
        save_every, sample_every = trainer_knobs(self)
        want = self._spec_preview_every
        if want <= 0:
            self.preview_every = save_every or want or 100
            self.log("preview_every <= 0 -> %d" % self.preview_every)
            return
        self.preview_every = want
        if save_every and want % save_every:
            extra = max(0, self.total_steps // want)
            self.log("preview_every %d is off the save_every %d grid - each preview will force an "
                     "extra lossless checkpoint (~%d extra saves). This is supported, not a problem."
                     % (want, save_every, extra))
        elif save_every:
            self.log("preview_every %d = %dx save_every %d" % (want, want // save_every, save_every))
        if sample_every and save_every and sample_every != save_every:
            self.log("note: sample_every=%d differs from save_every=%d (ai-toolkit's own sampler "
                     "cannot run in this pipeline; the foundry renders previews)"
                     % (sample_every, save_every))
        pauses = self.total_steps // want
        if pauses > 12 and self.previews_mode == "live":
            self.log("WARNING: preview_every %d on a %d-step run = %d live pauses. At the measured "
                     "~8 min per 2-prompt cycle (2026-07-26, healthy card) that is ~%d min of "
                     "pausing. Consider a larger interval or previews: deferred."
                     % (want, self.total_steps, pauses, pauses * 8))
        if self.mode == "train" and not self.spec.get("expect_s_it"):
            self.log("WARNING: order declares no expect_s_it - the stall daemon's declared-rate "
                     "arm is OFF for this run (self-baseline and documented-band arms still "
                     "apply). Add expect_s_it (healthy s/it for this config) to the spec.")

    def _load_state(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"phase": "PREFLIGHT", "previews_done": [], "ladder_done": [], "started": None}

    def save(self):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)

    def log(self, msg):
        line = "%s  [%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), self.state.get("phase"), msg)
        print(line, flush=True)
        try:
            with open(self.flog, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ── notifications ───────────────────────────────────────────────────────────────────────

def _etc_env(path=DM_ENV):
    """Read one KEY=VALUE env file beside ETC. The reader lives in foundry_paths so there is
    exactly one parser for this format in the workspace."""
    return P._etc_env(path)


def _dm_creds():
    creds = _etc_env()
    return creds.get("OVERNIGHT_DM_TOKEN"), creds.get("OVERNIGHT_DM_USER_ID")


def notify_file(job, message, path):
    """DM file path via bot. Fail-soft; text NOTIFY sent separately."""
    job.log("NOTIFY-FILE: %s (%s)" % (message, os.path.basename(path)))
    if job.initiator != "direct":
        return
    token, user_id = _dm_creds()
    if not (token and user_id) or not (DM_URL and DM_HOST):
        return
    try:
        import uuid as _uuid
        import io as _io
        with open(path, "rb") as f:
            data = f.read()
        b = _uuid.uuid4().hex
        body = _io.BytesIO()
        # Order name goes LAST for image DMs. Leading with "[foundry:probe_concept_v7_ck1000]" spent
        # the first and most-read line on a machine identifier, pushing the arm and the prompt below
        # it -- on a phone that is most of what is visible. Provenance still travels with every
        # image, it just stops being the headline. Text notifies (notify()) keep the prefix: there
        # the order name IS the subject.
        for name, val in (("user_id", user_id), ("message", "%s\n%s" % (message, job.name))):
            body.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                        % (b, name, val)).encode())
        body.write(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                    "Content-Type: image/png\r\n\r\n" % (b, os.path.basename(path))).encode())
        body.write(data)
        body.write(("\r\n--%s--\r\n" % b).encode())
        req = urllib.request.Request(
            DM_URL.replace("/dm", "/dm-file"), data=body.getvalue(),
            headers={"Content-Type": "multipart/form-data; boundary=%s" % b,
                     "X-DM-Token": token, "Host": DM_HOST})
        # HTTP 200 does not imply success; /dm-file returns {"ok": false} on failures
        # (e.g., closed DMs, outages). Retry transient errors 3 times.
        ok, raw = False, ""
        for _attempt in (1, 2, 3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read(4096).decode("utf-8", "replace")
                ok = json.loads(raw).get("ok") is True
            except Exception as _e:
                ok, raw = False, repr(_e)
            if ok:
                break
            if _attempt < 3:
                job.log("NOTIFY-FILE attempt %d failed (%s) - retrying" % (_attempt, raw[:90]))
                time.sleep(5 * _attempt)
        job.log("NOTIFY-FILE %s: %s (%d bytes)%s"
                % ("DELIVERED" if ok else "**REJECTED after 3 attempts**",
                   os.path.basename(path), len(data),
                   "" if ok else " -> relay said: " + raw[:200]))
    except Exception as e:
        job.log("WARNING: DM attachment failed (%r) — text notify already carried the path" % (e,))


def _host_tag():
    """Return a host tag like `[mothership]` to attribute alerts.

    Two factories DM the same channel; unattributed alerts are unactionable.
    The tag is prepended so it survives phone truncation and remains greppable.
    A single helper ensures consistent formatting across all DM types."""
    return "[%s]" % FLEET_HOST


def notify(job, message):
    """Fail-soft Discord DM via hawkadminbot. Only for direct jobs; HAI-initiated runs are narrated by HAI. Failures must never affect the run."""
    job.log("NOTIFY: %s" % message.replace("\n", " | "))
    if job.initiator != "direct":
        return
    token, user_id = _dm_creds()
    if not (token and user_id) or not (DM_URL and DM_HOST):
        return
    try:
        req = urllib.request.Request(
            DM_URL,
            # Message format must match BigHonker to remain unambiguous on shared DM channel.
            data=json.dumps({"user_id": user_id,
                             "message": "%s [foundry:%s] %s"
                                        % (_host_tag(), job.name, message)}).encode(),
            headers={"Content-Type": "application/json", "X-DM-Token": token, "Host": DM_HOST},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        job.log("WARNING: DM send failed (%r) — continuing" % (e,))


def notify_desk(message):
    """A DM from the DESK rather than from a job -- the receptionist speaking, not the line.

    `notify()` needs a Job because everything that spoke before now belonged to one. Capability
    gaps and trespass alerts are properties of the FACTORY, not of any order, and inventing a
    fake job to carry them would put the wrong name on the message. Fail-soft like notify()."""
    print("%s  [DESK] %s" % (datetime.datetime.now().strftime("%H:%M:%S"),
                             message.replace("\n", " | ")), flush=True)
    token, user_id = _dm_creds()
    if not (token and user_id) or not (DM_URL and DM_HOST):
        return
    try:
        req = urllib.request.Request(
            DM_URL,
            data=json.dumps({"user_id": user_id,
                             "message": "%s [foundry:desk] %s"
                                        % (_host_tag(), message)}).encode(),
            headers={"Content-Type": "application/json", "X-DM-Token": token, "Host": DM_HOST},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


# ── primitives ──────────────────────────────────────────────────────────────────────────

def trainer_running(job):
    """v0.3: two-strike read — a single CIM query can transiently miss a live process (false 'not
    running' WARNING seen 01:41 on a healthy loading trainer). Only report False on two misses.
    v0.3.3: literal Contains() filter, not -match (the worker's -match variant false-negatived;
    02:15 post-mortem). 'run.py' + '<jobname>' matches ONLY the trainer ("venv\\python.exe -u run.py
    config\\<job>.yaml" — cwd-relative, never full paths) and can never match the foundry itself."""
    def _once():
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { "
                 "$null -ne $_.CommandLine -and $_.CommandLine.Contains('run.py') -and "
                 "$_.CommandLine.Contains('%s') } | Measure-Object).Count" % trainer_name(job)],
                capture_output=True, text=True, timeout=30).stdout.strip()
            return out not in ("", "0")
        except Exception:
            return False
    if _once():
        return True
    time.sleep(3)
    return _once()


def checkpoints(job):
    """{step:int -> path} for numbered saves in the output dir."""
    out = {}
    for p in glob.glob(os.path.join(job.output_dir, "%s_*.safetensors" % trainer_name(job))):
        tail = os.path.basename(p).rsplit("_", 1)[-1].replace(".safetensors", "")
        if tail.isdigit():
            out[int(tail)] = p
    return out


_TRAINER_TOTAL = {}


def trainer_total_steps(job):
    """Return the step count from the trainer's YAML config.

    Distinct from `job.total_steps` (the order limit). The trainer's progress bar
    uses its own config total; using the order total for parsing causes silent
    miscalculations. Falls back to `job.total_steps` if config is missing."""
    if job.name not in _TRAINER_TOTAL:
        total = job.total_steps
        try:
            with open(launcher_config(job) or os.path.join(P.AI_TOOLKIT, "config", job.name + ".yaml"),
                      "r", encoding="utf-8", errors="ignore") as f:
                m = re.search(r"^\s*steps\s*:\s*(\d+)", f.read(), re.M)
            if m:
                total = int(m.group(1))
        except OSError:
            pass
        _TRAINER_TOTAL[job.name] = total
    return _TRAINER_TOTAL[job.name]


def current_step(job):
    """Best-effort step from the trainer log's progress bar (display only — decisions use FILES)."""
    try:
        with open(job.log_path, "rb") as f:
            f.seek(max(0, os.path.getsize(job.log_path) - 4096))
            tail = f.read().decode("utf-8", "ignore")
        import re
        # Cut to the newest segment (the log APPENDS across resumes now): immediately after a
        # relaunch the 4 KB tail is still the PREVIOUS segment's progress bar, which would report a
        # step the freshly-booting trainer has not reached yet.
        seg = _current_segment(tail)
        # Match trainer step count, not order limit. They differ; using order total breaks regex parsing when steps diverge (e.g., stopping early).
        m = re.findall(r"(\d+)/%d" % trainer_total_steps(job), seg)
        return int(m[-1]) if m else None
    except Exception:
        return None


BOOT_MARKERS = [                      # ordered: later entries indicate further progress through boot
    ("Caching latents", "caching latents"),
    ("Caching text embeddings", "caching text embeddings"),
    ("UNLOADING TEXT ENCODER", "text encoder unloaded"),
    ("Loading pre-quantized", "loading quantized weights"),
    ("[P20-DSP]", "device placement"),
    ("[P20-FWD]", "first forward pass"),
]


def reconcile_cadence(save_every, preview_every, total_steps, keep=None, max_saves=30):
    """Reconcile save and preview cadences so previews always align with checkpoints.

    Since previews render from saved checkpoints, a preview step without a corresponding checkpoint
    is ineffective. To preserve the requested `preview_every` interval while ensuring alignment,
    `save_every` is adjusted to gcd(save_every, preview_every). This may introduce intermediate
    save points (e.g., 100/250 -> saves every 50) but avoids silent failures or forced snapping.

    To prevent checkpoint explosion from non-divisible inputs (e.g., 100/172 -> 4), the total
    checkpoint count is bounded by `max_saves`. If exceeded, `preview_every` is snapped up to the
    next valid multiple rather than lowering the save interval floor. The retention limit `keep`
    is increased if necessary to ensure checkpoints required for previews are not pruned.
    """
    notes = []
    try:
        save_every = int(save_every)
        preview_every = int(preview_every)
    except (TypeError, ValueError):
        return save_every, preview_every, keep, ["unreadable cadences; left alone"]
    if save_every <= 0:
        return save_every, preview_every, keep, ["save_every <= 0; left alone"]
    if preview_every <= 0:
        preview_every = save_every
        notes.append("preview_every <= 0 -> one save interval (%d)" % save_every)

    if preview_every % save_every == 0:
        notes.append("preview_every %d = %dx save_every %d" % (preview_every, preview_every // save_every, save_every))
    else:
        import math
        g = math.gcd(save_every, preview_every)
        # Bound the CHECKPOINT COUNT, not the step interval. The first version floored the interval
        # (g >= 25), which let 100/175 -> gcd 25 through: a legal-looking interval that means FORTY
        # checkpoints on a 1000-step run. Disk and prune pressure scale with the count, so that is
        # what gets a limit.
        would_save = total_steps // max(1, g)
        if would_save > max_saves:
            mult = max(1, -(-preview_every // save_every))
            notes.append("preview_every %d is not a multiple of save_every %d; matching it exactly "
                         "would need saves every %d = %d checkpoints (cap %d) -- snapping preview to "
                         "%d instead" % (preview_every, save_every, g, would_save, max_saves,
                                         mult * save_every))
            preview_every = mult * save_every
        else:
            notes.append("save_every %d -> %d so previews can land exactly on %d (extra save points "
                         "added; preview cadence preserved)" % (save_every, g, preview_every))
            save_every = g

    need = total_steps // save_every + 2          # +2: the step-0/final and one for slack
    if keep is not None and keep < need:
        notes.append("max_step_saves_to_keep %d -> %d so the run cannot prune checkpoints it will "
                     "render from" % (keep, need))
        keep = need
    return save_every, preview_every, keep, notes


def launcher_config(job):
    """The trainer config the order's launcher actually runs (first *.yaml token in the .bat), or "" if none exists.
    Deliberately NO regex for the path: an earlier version was written through a heredoc and its escaped
    drive-letter pattern collapsed into one that only matched by accident. Token scanning needs no escapes, so it
    cannot be corrupted the same way."""
    launcher = getattr(job, "launcher", "") or ""
    try:
        bat = open(launcher, encoding="utf-8", errors="ignore").read()
    except (OSError, TypeError):
        return ""
    for tok in bat.replace('"', " ").split():
        if tok.lower().endswith((".yaml", ".yml")):
            cfg = tok if os.path.isabs(tok) else os.path.join(os.path.dirname(launcher), tok)
            return cfg if os.path.exists(cfg) else ""
    return ""


def trainer_name(job):
    """Return the trainer's execution name from its config, falling back to the order name.

    The order name may differ (e.g., suffixed by host); use the config name to match processes and checkpoints."""
    cfg = launcher_config(job)
    if cfg:
        try:
            for line in open(cfg, encoding="utf-8", errors="ignore"):
                head = line.split("#", 1)[0].strip()
                if head.startswith("name:"):
                    val = head.split(":", 1)[1].strip().strip("'\"")
                    if val and not val.startswith("["):        # meta: name: "[name]" is ai-toolkit's placeholder
                        return val
        except OSError:
            pass
    return job.name


def trainer_knobs(job):
    """Reads save_every and sample_every from the launcher config.

        Note: The native ai-toolkit sampler is disabled in this pipeline because
        the text encoder is unloaded to fit 32B models in 24 GB VRAM. However, we
        reuse the `sample_every` config value to control preview cadence via ComfyUI,
        ensuring the knob actually affects output as expected.
    """
    try:
        cfg = launcher_config(job)
        if not cfg:
            return None, None

        def grab(key):
            for line in open(cfg, encoding="utf-8", errors="ignore"):
                head = line.split("#", 1)[0].strip()
                if head.startswith(key + ":"):
                    val = head.split(":", 1)[1].strip()
                    return int(val) if val.isdigit() else None
            return None
        return grab("save_every"), grab("sample_every")
    except Exception:
        return None, None


SEGMENT_MARK = "[P20] trainer segment start"
# The launcher appends logs rather than truncating. Since tail readers see the entire file history, any scan for a segment marker must ignore data from previous runs.


def _current_segment(tail):
    """Only the text since the last trainer launch. Without this, boot_phase would happily report a
    marker left over from the segment before and describe a boot that already finished."""
    i = tail.rfind(SEGMENT_MARK)
    return tail[i:] if i != -1 else tail


def boot_phase(job):
    """Latest boot-sequence event in the trainer log tail — so 'not stepping yet' is reported as the
    PHASE it's in, not silence or a false crash warning (the 01:41/01:54 confusion, taught)."""
    try:
        with open(job.log_path, "rb") as f:
            f.seek(max(0, os.path.getsize(job.log_path) - 16384))
            tail = _current_segment(f.read().decode("utf-8", "ignore"))
        phase = None
        for marker, label in BOOT_MARKERS:
            if marker in tail:
                phase = label
        return phase
    except Exception:
        return None


BOOT_PROFILE = os.path.join(os.path.dirname(RESCUE_LOG), "boot_profile.json")


def _boot_waypoints(job):
    """(elapsed_s, [codes]) for the CURRENT trainer segment, in log order."""
    try:
        with open(job.log_path, "rb") as f:
            f.seek(max(0, os.path.getsize(job.log_path) - 262144))
            tail = _current_segment(f.read().decode("utf-8", "ignore"))
    except OSError:
        return 0, []
    hits = re.findall(r"\[P20-TIMING\] (\d\d):(\d\d):(\d\d)\.\d+ (WP\S+)", tail)
    if not hits:
        return 0, []
    def _s(h, m, sec):
        return int(h) * 3600 + int(m) * 60 + int(sec)
    t0 = _s(*hits[0][:3])
    el = _s(*hits[-1][:3]) - t0
    return (el if el >= 0 else 0), [h[3] for h in hits]


def _boot_kind(job):
    """A resume boot skips the cache builds and part of the resident move -- ~3 min vs ~27. Predicting
    one from the other's profile would be worse than no bar at all."""
    try:
        with open(job.log_path, "rb") as f:
            f.seek(max(0, os.path.getsize(job.log_path) - 262144))
            tail = _current_segment(f.read().decode("utf-8", "ignore"))
        return "resume" if "P20_RESUME_MODE=1" in tail else "cold"
    except OSError:
        return "cold"


def record_boot_profile(job):
    """Record this boot's waypoint timings to enable accurate progress bars for subsequent boots.

    Waypoints are emitted by the trainer and vary with recipes, so they cannot be hardcoded.
    Results are keyed by boot kind (cold vs resume) because their durations differ significantly."""
    el, codes = _boot_waypoints(job)
    if len(codes) < 5:
        return
    try:
        prof = {}
        if os.path.exists(BOOT_PROFILE):
            with open(BOOT_PROFILE, "r", encoding="utf-8") as f:
                prof = json.load(f)
        prof[_boot_kind(job)] = {"codes": codes, "seconds": el, "at": datetime.datetime.now().isoformat()}
        os.makedirs(os.path.dirname(BOOT_PROFILE), exist_ok=True)
        with open(BOOT_PROFILE, "w", encoding="utf-8") as f:
            json.dump(prof, f, indent=1)
        job.log("recorded %s boot profile: %d waypoints, %ds" % (_boot_kind(job), len(codes), el))
    except Exception as e:
        job.log("WARNING: could not record boot profile (%r)" % (e,))


def boot_progress(job):
    """'loading quantized weights - 18/33 (55%) ~4m left', or the coarse phase if unprofiled yet."""
    label = boot_phase(job) or "starting up"
    el, codes = _boot_waypoints(job)
    if not codes:
        return label
    total, secs = 0, 0
    try:
        with open(BOOT_PROFILE, "r", encoding="utf-8") as f:
            ref = json.load(f).get(_boot_kind(job)) or {}
        total, secs = len(ref.get("codes") or []), int(ref.get("seconds") or 0)
    except Exception:
        pass
    done = len(codes)
    if total < done:
        total = done          # this boot is longer than the reference; never show >100%
    if not total:
        return "%s - step %d of boot" % (label, done)
    pct = int(100.0 * done / total)
    left = ""
    if secs and done < total:
        rem = int(secs * (total - done) / float(total)) - 0
        if rem > 0:
            left = " ~%dm left" % max(1, rem // 60) if rem >= 60 else " ~%ds left" % rem
    return "%s - %d/%d (%d%%)%s" % (label, done, total, pct, left)


def eta_text(job):
    """Estimate remaining time by multiplying remaining steps by the measured seconds-per-iteration
    from the trainer log, adding 20 minutes for each remaining live pause.

    Returns '' if no rate can be measured."""
    try:
        with open(job.log_path, "rb") as f:
            f.seek(max(0, os.path.getsize(job.log_path) - 4096))
            tail = f.read().decode("utf-8", "ignore")
        import re
        # Same segment cut as the others: a rate carried over from before a resume is not this
        # segment's rate, and the median below cannot rescue an ETA built from the wrong run.
        rates = re.findall(r"([\d.]+)s/it", _current_segment(tail))
        step = current_step(job)
        if not rates or not step:
            return ""
        # Use median of recent samples to avoid ETA spikes from resume/cache reloads or jitter.
        recent = [float(x) for x in rates[-5:]]
        recent.sort()
        rate = recent[len(recent) // 2]
        pauses_left = 0
        if job.previews_mode == "live" and job.preview_prompts:
            ms = sorted(set(list(range(job.preview_every, job.total_steps, job.preview_every))
                            + job.extra_previews))
            pauses_left = len([s for s in ms if s > step and s not in job.state["previews_done"]])
        mins = ((job.total_steps - step) * rate + pauses_left * 1200) / 60.0
        done = datetime.datetime.now() + datetime.timedelta(minutes=mins)
        return "ETA: finish ~%s (%d steps @ %.1fs/it + %d pause%s)" % (
            done.strftime("%H:%M"), job.total_steps - step, rate, pauses_left,
            "" if pauses_left == 1 else "s")
    except Exception:
        return ""


def launch_detached(job, task_name, command):
    """Launch a child via Task Scheduler so it survives the runner's session dying.
    Popen/start survives the runner process dying but not its session teardown."""
    # /ru SYSTEM: prevents 'No mapping between account names and security IDs' when overwriting
    # user-owned tasks. /sd 01/01/2020: uses past date to disable timer firing (Next Run: N/A)
    # while allowing immediate start via /run. Required because schtasks mandates a schedule.
    rc = subprocess.run(["schtasks", "/create", "/f", "/tn", task_name, "/tr", command,
                         "/sc", "once", "/st", "00:00", "/sd", "01/01/2020", "/ru", "SYSTEM"],
                        capture_output=True, text=True, timeout=30)
    if rc.returncode != 0:
        raise RuntimeError("schtasks /create %s failed (STALE definition would run!): %s"
                           % (task_name, (rc.stderr or rc.stdout).strip()))
    q = subprocess.run(["schtasks", "/query", "/tn", task_name, "/v", "/fo", "list"],
                       capture_output=True, text=True, timeout=30)
    if command.lower() not in q.stdout.lower():
        raise RuntimeError("schtasks task %s does NOT point at %r after create — refusing to run "
                           "a stale definition. Query says:\n%s" % (task_name, command, q.stdout[-400:]))
    r = subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("schtasks /run %s failed: %s" % (task_name, (r.stderr or r.stdout).strip()))
    job.log("launched via schtasks task %r: %s" % (task_name, command))


def comfy_up():
    try:
        with urllib.request.urlopen(COMFY + "/system_stats", timeout=3):
            return True
    except Exception:
        return False


# When the CURRENT ComfyUI process last came up. A render issued against a freshly-started server
# pays the 32B model load before it samples a single step; one issued into a warm server does not.
# Those are different budgets and conflating them is what broke v8's previews (see RENDER_TIMEOUT_*).
_COMFY_UP_AT = 0.0

# RENDER_TIMEOUT_WARM: Max observed warm render ~261s; 480s provides margin.
# RENDER_TIMEOUT_COLD: Includes 32B model load (~260s+). 900s prevents false timeouts during first render.
RENDER_TIMEOUT_WARM = 480
RENDER_TIMEOUT_COLD = 900

RENDER_STALL_SECS = 300      # ComfyUI idle this long with no PNG => genuinely wedged, not slow
RENDER_GRACE_SECS = 150      # SLIM has to start and submit before "idle" means anything
RENDER_HARD_CAP = 3600       # backstop only: something failed in a way neither signal describes


def comfy_busy():
    """Return True if ComfyUI is executing/queued, False if idle, None if unknown.

    Treat None as a failed probe, NOT as idle. Treating 'unknown' as 'free'
    brings down healthy work (fail-closed invariant)."""
    try:
        with urllib.request.urlopen(COMFY + "/queue", timeout=5) as r:
            q = json.loads(r.read().decode("utf-8", "replace"))
        return bool(q.get("queue_running")) or bool(q.get("queue_pending"))
    except Exception:
        return None


def supervise_render(job, cmd, png):
    """Run one render, supervised by ComfyUI's STATE rather than by elapsed time.

    Returns (last_line, reason). Kills the child only when ComfyUI has been demonstrably idle with no
    output for RENDER_STALL_SECS -- which is what "wedged" actually means, and is exactly the case
    the old 480s ceiling could not distinguish from "still loading the model".
    """
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t0 = time.time()
    last_progress = t0
    while True:
        rc = p.poll()
        if rc is not None:
            out = (p.stdout.read() or "").strip() if p.stdout else ""
            return ((out.splitlines()[-1] if out else str(rc)), "exited")
        if os.path.exists(png):
            try:
                p.wait(timeout=60)
            except Exception:
                p.kill()
            return ("PNG present", "done")
        busy = comfy_busy()
        if busy or busy is None:          # working, or cannot tell -> not evidence of a stall
            last_progress = time.time()
        idle = time.time() - last_progress
        elapsed = time.time() - t0
        if elapsed > RENDER_GRACE_SECS and idle > RENDER_STALL_SECS:
            p.kill()
            return ("STALLED: ComfyUI idle %.0fs with no PNG after %.0fs" % (idle, elapsed), "stalled")
        if elapsed > RENDER_HARD_CAP:
            p.kill()
            return ("HARD CAP %ds hit (backstop; status said busy the whole time)"
                    % RENDER_HARD_CAP, "capped")
        time.sleep(5)


def comfy_is_cold(within=180.0):
    """True if the server is young enough that the next render still pays the model load."""
    return (time.time() - _COMFY_UP_AT) < within


def ensure_comfy(job):
    global _COMFY_UP_AT
    if comfy_up():
        return
    for attempt in (1, 2):
        job.log("launching ComfyUI (schtasks, attempt %d)" % attempt)
        launch_detached(job, "P20 Foundry ComfyUI", COMFY_LAUNCH)
        t0 = time.time()
        # Timeout 300s: cold boot ~240s + 25% headroom. Poll every 5s to detect warm boot (~52s) promptly.
        while not comfy_up():
            if time.time() - t0 > 300:
                break
            time.sleep(5)
        if comfy_up():
            _COMFY_UP_AT = time.time()
            job.log("ComfyUI up (%.0fs) - next render is COLD (pays the 32B model load)"
                    % (time.time() - t0))
            return
        # Self-heal: a boot that never answers /system_stats usually left a zombie owning :8189 —
        # kill it and try once more before escalating (the manual fix, taught).
        job.log("ComfyUI not answering after 7 min — killing any :8189 owner and %s"
                % ("retrying once" if attempt == 1 else "giving up"))
        stop_comfy(job)
        time.sleep(10)
    raise RuntimeError("ComfyUI failed to come up (2 attempts, zombie-kill between)")


def free_comfy(job):
    """Unload models from the resident server to prevent VRAM starvation during training."""
    try:
        req = urllib.request.Request(COMFY + "/free",
                                     data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15):
            pass
        job.log("resident server: models FREED (process stays for boot savings)")
    except Exception as e:
        job.log("WARNING: /free failed (%r) — falling back to full stop" % (e,))
        stop_comfy(job)


def stop_comfy(job):
    """Stop this factory's ComfyUI. A slow PowerShell is a transient condition, never a property of the order: when
    the call times out, the port decides - free means stopped, held (or unknown) means the order WAITS (LaneBusy)."""
    if _port_free(8189):
        job.log("ComfyUI not running (port 8189 free) - nothing to stop")
        return
    _note_card_busy()            # a ComfyUI is about to be stopped: its context teardown starts the settle window
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "$p = (Get-NetTCPConnection -LocalPort 8189 -ea 0 | Where-Object { $_.State -eq 'Listen' -or $_.RemoteAddress -in '0.0.0.0','::' } | Select -First 1).OwningProcess; "
                        "if ($p) { Stop-Process -Id $p -Force }"], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        if not _port_free(8189):
            raise LaneBusy("ComfyUI stop: PowerShell did not answer in 30 s and port 8189 is still held (or could "
                           "not be read) - the order waits")
        job.log("ComfyUI stop: PowerShell did not answer in 30 s; port 8189 is free - stopped")
        return
    job.log("ComfyUI stopped")


def _port_free(port):
    """True when `port` is free. Uses `netstat` plus a bind check; `netstat` misses some wildcard listeners (Windows 11 build 26300+), while a connect-probe fails due to SYN retries. Fail-closed: any error or unreadable state returns False (busy)."""
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=15)
    except Exception:
        return False
    rows = [l.split() for l in (r.stdout or "").splitlines()]
    rows = [c for c in rows if len(c) >= 4 and c[0].upper() == "TCP"]
    if r.returncode != 0 or not rows:
        return False
    if any(c[1].rsplit(":", 1)[-1] == str(int(port)) and c[3].upper() == "LISTENING" for c in rows):
        return False
    for addr in ("0.0.0.0", "127.0.0.1"):
        s = socket.socket()
        try:
            s.bind((addr, int(port)))
        except OSError:
            return False
        finally:
            s.close()
    return True


def _comfy_procs_alive():
    """Count python.exe processes for THIS factory's ComfyUI server.

    Non-zero return after `stop_comfy` indicates the HIP context is still draining.

    Uses `_is_our_comfy()` to identify target processes, shared with GPU patrol to
    prevent drift in ownership definition. Filters out self-PID and handles
    PowerShell JSON output (including bare objects for single matches)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
                            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
                           capture_output=True, text=True, timeout=15)
        rows = json.loads((r.stdout or "").strip() or "[]")
        if isinstance(rows, dict):
            rows = [rows]           # ConvertTo-Json emits a bare object for a single match
        me = os.getpid()
        return sum(1 for p in rows
                   if p.get("ProcessId") != me
                   and _is_our_comfy((p.get("CommandLine") or "").lower()))
    except Exception:
        return -1


CAPTION_CONF_FLOOR = 0.7

CAPTION_SCHEMA_MINIMAL = {
    "type": "object",
    "properties": {"caption": {"type": "string"}},
    "required": ["caption"],
}


# No string is shared with the `view` vocabulary -- that collision is the whole point of the split.
CROP_FULL, CROP_KNEE = "full figure", "knee up"
CROP_WAIST, CROP_TORSO = "waist up", "torso only"


def derive_crop(f):
    """Derive crop term via arithmetic over visible landmarks, not taxonomy judgement.

    Fail-closed: unrecognized inputs return None."""
    head = (f.get("head_in_frame") or "").strip().lower()
    feet = (f.get("feet_in_frame") or "").strip().lower()
    knees = (f.get("knees_in_frame") or "").strip().lower()
    if head not in ("yes", "partly", "no"):
        return None
    if head == "no":
        return CROP_TORSO
    if feet in ("both", "one"):
        return CROP_FULL
    if feet != "neither":
        return None                       # unreadable -> record nothing
    if knees in ("both", "one"):
        return CROP_KNEE
    if knees == "neither":
        return CROP_WAIST
    return None


def run_framing_watched(job, src, n_expected):
    """Run keypoint pass supervised by PROGRESS tokens, not a clock.

    Waits for `PROGRESS i/n <file>` lines from p20_framing.py. Only kills if progress
    demonstrably stops; allows slow runs to complete. A timeout cannot distinguish a slow
    worker from a wedged one.

    Returns (stdout, stderr_tail, images_seen, returncode)."""
    import threading

    # Must explicitly set encoding="utf-8" because `text=True` defaults to the locale
    # (cp1252 on these boxes). Without it, non-ASCII filenames cause silent thread death
    # and obscure JSONDecodeErrors.
    proc = subprocess.Popen(
        [FRAMING_PY, os.path.join(P.BIN, "p20_framing.py"), src],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", bufsize=1)

    seen = [0]
    last_at = [time.time()]
    err_tail = []
    out_buf = []
    pump_err = []          # a reader thread that dies must SAY SO, not just return nothing

    def _pump_err():
        try:
            for ln in proc.stderr:
                ln = ln.rstrip()
                if ln.startswith("PROGRESS "):
                    last_at[0] = time.time()
                    part = ln.split()[1]
                    if "/" in part and part.split("/")[0].isdigit():
                        seen[0] = int(part.split("/")[0])
                else:
                    err_tail.append(ln)
                    del err_tail[:-40]
        except Exception as e:
            pump_err.append("stderr reader died: %r" % (e,))

    def _pump_out():
        # Daemon thread: exceptions are silent; capture them so we know
        # if the reader died (otherwise stdout stays empty and blame shifts).
        try:
            out_buf.append(proc.stdout.read())
        except Exception as e:
            pump_err.append("stdout reader died: %r" % (e,))

    te = threading.Thread(target=_pump_err, daemon=True)
    to = threading.Thread(target=_pump_out, daemon=True)
    te.start(); to.start()

    said = [0]
    while proc.poll() is None:
        time.sleep(2.0)
        idle = time.time() - last_at[0]
        if idle > FRAMING_IDLE_S:
            # DEMONSTRATED idleness, not elapsed time: the worker stopped saying anything for
            # longer than a weights download takes, having reported progress up to `seen`.
            proc.kill()
            te.join(timeout=5); to.join(timeout=5)
            return ("", "framing made no progress for %.0fs; last PROGRESS was %d/%d. %s"
                    % (idle, seen[0], n_expected, " | ".join(err_tail[-6:])),
                    seen[0], -1)
        # Check for stop order so the framing loop can be cancelled mid-run.
        # We kill the subprocess because partial framing is useless (all-or-nothing contract),
        # and return rc -3 to distinguish operator-cancelled runs from detector failures.
        if stop_order(job) is not None:
            proc.kill()
            te.join(timeout=5); to.join(timeout=5)
            job.log("STOP ORDER honored during the framing pass at %d/%d - ending here"
                    % (seen[0], n_expected))
            return ("", "stopped by operator during framing at %d/%d" % (seen[0], n_expected),
                    seen[0], -3)
        if seen[0] and seen[0] != said[0]:
            said[0] = seen[0]
            if seen[0] % 25 == 0:
                publish_current_run(job, state="captioning", step=0,
                                    phase="framing %d/%d (keypoint pass)" % (seen[0], n_expected),
                                    ttl=RENDER_CLAIM_TTL)
    te.join(timeout=10); to.join(timeout=10)
    # A dead reader is reported as a FAILURE of this call, not as a clean run with no output --
    # otherwise the caller sees returncode 0 plus empty stdout and blames the child.
    if pump_err:
        return ("", "; ".join(pump_err) + " | " + "\n".join(err_tail[-6:]), seen[0], -2)
    return ("".join(out_buf), "\n".join(err_tail[-10:]), seen[0], proc.returncode)


def framing_clause(fr):
    """Append measured framing info to the caption prompt.

    Frame boundaries are unreliable for the VLM; we provide them as facts,
    not queries, derived from keypoint arithmetic. The instruction is
    conditional on measurement: unconditional limb descriptions cause the
    model to invent limbs on cropped images (e.g., torso-only shots).

    Returns "" if no confident person is detected."""
    if not fr:
        return ""
    bits = []
    out = fr.get("limbs_out") or []
    inn = fr.get("limbs_in") or []
    if out:
        bits.append("The subject's %s are NOT in this photograph; state that plainly in the "
                    "paragraph and do not describe them." % " and ".join(out))
    if inn:
        bits.append("The subject's %s are in the photograph; say where they are and what they "
                    "are doing." % " and ".join(inn))
    # head_crop arrives as a bare noun phrase ("head partly cropped"), which needs a verb before it
    # can be a sentence.
    _HEAD_SAY = {"head partly cropped": "The subject's head is partly cut off by the top of the "
                                        "frame.",
                 "head out of frame": "The subject's head is entirely outside the frame; do not "
                                      "describe their face or hair."}
    if fr.get("head_crop"):
        bits.append(_HEAD_SAY.get(fr["head_crop"], "The subject's %s." % fr["head_crop"]))
    if fr.get("inverted"):
        bits.append("This photograph is stored UPSIDE DOWN: the subject's head is at the bottom of "
                    "the image. Describe the subject as they really are, not as the inverted "
                    "image appears, and mention that the photograph is inverted.")
    if not bits:
        return ""
    return (" These facts about this specific photograph were MEASURED, not guessed. Treat them as "
            "true even if the picture seems to disagree, and never contradict them. " +
            " ".join(bits))


CAPTION_SCHEMA_FRAMING_ONLY = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "framing": {"type": "string",
                    "enum": ["full figure", "three-quarter length", "half length",
                             "waist up", "torso only", "close-up"]},
        "out_of_frame": {"type": "string"},
    },
    "required": ["caption", "framing", "out_of_frame"],
}

CAPTION_PROMPT_FRAMING_ONLY = (
    "Answer ONE question about this photograph: how much of the person's body is inside the frame? "
    "Ignore everything else -- do not describe the subject, the pose, the clothing, the setting or "
    "the lighting. "
    # Ask it to LOOK at the edges before naming a term. The composite call answered "full figure"
    # 7/10 including on images with the head cropped and the legs cut at the knee, which reads
    # like a default rather than a judgement.
    "Work through it in this order. First, check each edge of the picture in turn and note which "
    "parts of the body the edge cuts through. Second, state which of these are FULLY visible: the "
    "head and face, the torso, the hips, the knees, the feet. Third, only then choose the term. "
    "Choose 'full figure' ONLY when the head AND the feet are both inside the frame -- if either "
    "one is cut off, it is not a full figure. "
    "caption: one sentence giving your answer. framing: the single term. out_of_frame: ONE "
    "complete sentence naming every part of the body cut off by the frame edge, for example 'Her "
    "head and lower legs are out of frame.' -- write exactly 'Nothing is out of frame.' only when "
    "the head, hands and feet are ALL visible. "
    "When you quote any text, wrap it in single quotes, never double quotes."
)


# A caption shorter than this is a STUB, not a caption. The prompt asks for 55-90 words; 45 leaves
# slack for a legitimately terse image without accepting the failure mode that destroyed the v2
# corpus (median 9 words across 1838 images, 86% under 20). One 16-word stub still slipped through
# the end-to-end probe at 1/12 -- ~150 of them across a full run -- because it ended in a period
# and cleared the 40-CHARACTER bar. Characters were never the right unit.
# A caption order's prompt and schema, by the kind its caption_schema names (caption_kinds). These are the foundry's
# own kinds; an extension beside this file adds kinds and can set the default.
CAPTION_PROMPT_PLAIN = (
    "Describe this image plainly for an image-training caption. Report only what you can SEE: the subject, what it "
    "is doing, the setting, the lighting and the camera angle, in one flowing paragraph of 40-120 words. Return "
    'STRICT JSON with exactly one key: {"caption": ""}')
_CAPTION_KINDS_OWN = {
    "minimal": {"prompt": CAPTION_PROMPT_PLAIN, "schema": CAPTION_SCHEMA_MINIMAL},
    "framing_only": {"prompt": CAPTION_PROMPT_FRAMING_ONLY, "schema": CAPTION_SCHEMA_FRAMING_ONLY},
}


def _foundry_extensions():
    """The modules beside this file named p20_foundry_ext_<name>.py, in name order."""
    import importlib
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    return [importlib.import_module(os.path.splitext(os.path.basename(p))[0])
            for p in sorted(glob.glob(os.path.join(here, "p20_foundry_ext_*.py")))]


def caption_kinds():
    """{kind: {"prompt", "schema"}}: the foundry's own kinds with every extension's CAPTION_KINDS merged on (a key an
    extension leaves out keeps the foundry's value). Only complete kinds are offered."""
    kinds = {k: dict(v) for k, v in _CAPTION_KINDS_OWN.items()}
    for ext in _foundry_extensions():
        for k, v in getattr(ext, "CAPTION_KINDS", {}).items():
            kinds.setdefault(k, {}).update(v)
    return {k: v for k, v in kinds.items() if v.get("prompt") and v.get("schema")}


def caption_default():
    """The kind an order that names no caption_schema gets: the first extension's CAPTION_DEFAULT, else minimal."""
    for ext in _foundry_extensions():
        if getattr(ext, "CAPTION_DEFAULT", None):
            return ext.CAPTION_DEFAULT
    return "minimal"


CAPTION_MIN_WORDS = 45


def caption_looks_truncated(text, min_words=0):
    """True if caption ends abruptly (mid-sentence/punctuation missing)."""
    t = (text or "").strip()
    if len(t) < 40:
        return True
    if min_words and len(t.split()) < min_words:
        return True          # a STUB: complete-looking, correctly punctuated, and not the product
    return t[-1] not in ".!?"


def compose_caption(out, emit_framing=False):
    """Sidecar text from the v2 schema: the prose paragraph plus ONLY the gated fields that
    clear the confidence floor. A field below the floor simply does not appear -- omitted is
    honest, invented is poison (the recaption() rule, applied to fresh captions)."""
    cap = (out.get("caption") or "").strip()
    if not cap:
        raise ValueError("empty caption")
    # Fold structured fields into prose, not key-value tags. Training data should
    # contain natural language captions; tag-like metadata teaches the model to expect
    # schema artifacts. Check for `view`, `out_of_frame`, or `head_in_frame` to detect
    # legacy schemas that lack the lean rich schema's safety guards.
    if "out_of_frame" in out or "head_in_frame" in out or "view" in out:
        parts = [cap if cap.endswith((".", "!", "?")) else cap + "."]
        # Framing judgments are excluded by default until a reliable source exists.
        # The model produces inconsistent/incorrect framing labels (e.g., flips on
        # out_of_frame) but records them in the manifest for analysis, not training.
        if emit_framing:
            fr = (out.get("framing") or "").strip()
            if fr:
                parts.append("The framing is %s." % fr)
        # Skip near-constant negatives (e.g., "no watermark") to avoid baking unsteerable tokens;
        # keep positives so the adapter learns artifacts are distinct from concepts.
        # out_of_frame is dropped due to low reproducibility; watermark removed per same rule.
        for field in (("out_of_frame", "watermark") if emit_framing else ()):
            s = (out.get(field) or "").strip()
            if not s:
                continue
            low = s.lower().rstrip(". ")
            if low in ("nothing is out of frame", "there is no watermark", "none", "nothing",
                       "no watermark", "n/a", "nothing is cut off"):
                continue
            parts.append(s if s.endswith((".", "!", "?")) else s + ".")
        return " ".join(parts)
    tags = []
    for field in ("subject_state", "framing", "marks"):
        val = (out.get(field) or "").strip()
        try:
            conf = float(out.get(field + "_conf") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if val and conf >= CAPTION_CONF_FLOOR:
            tags.append("%s: %s" % (field, val))
    return cap + ("\n" + " | ".join(tags) if tags else "")


def stage_caption_set(job, src_root, dest, count=0, stratify=True, include=(),
                      exts=(".jpg", ".jpeg", ".png", ".webp")):
    """Materialise an order's INPUT SET. The factory stages its own images.

    Inputs are part of the spec (`caption_stage_from` etc.) so the order is
    self-contained and audit-friendly.

    IDEMPOTENT for resume: if `dest` already contains expected images, it is
    left alone to avoid disturbing sidecars or re-triggering change detectors.

    STRATIFIED by immediate subfolder when `stratify=True`, ensuring samples
    span collections rather than biasing toward the first N files."""
    if not os.path.isdir(src_root):
        raise RuntimeError("caption_stage_from does not exist: %s" % src_root)
    os.makedirs(dest, exist_ok=True)
    have = [f for f in os.listdir(dest) if os.path.splitext(f)[1].lower() in exts]
    if have:
        # Resume must match the order's expected file count. If the existing
        # destination file count differs from `count` (or `include`), fail hard.
        # Silently accepting a different count risks processing unintended data.
        want = None
        if include:
            pats = [include] if isinstance(include, str) else list(include)
            want = len(pats)
        elif count:
            want = int(count)
        if want and len(have) != want:
            raise RuntimeError(
                "caption_src %s already holds %d image(s) but this order names %d. Refusing to "
                "resume: staging is skipped when the destination is populated, so continuing "
                "would silently caption the folder's contents instead of the order's inputs. "
                "Point caption_src at a fresh folder (one order, one staging directory), or "
                "clear it if this really is a resume of the same order."
                % (dest, len(have), want))
        job.log("stage: %s already holds %d image(s) matching this order - leaving it alone "
                "(idempotent resume)" % (dest, len(have)))
        return len(have)
    pool = sorted(p for p in glob.glob(os.path.join(src_root, "**", "*.*"), recursive=True)
                  if os.path.splitext(p)[1].lower() in exts and "_stale" not in p)
    if not pool:
        raise RuntimeError("caption_stage_from has no images: %s" % src_root)
    # Patterns match basename (fnmatch). Must hit at least one file; silent omission is a hard error.
    forced = []
    if include:
        import fnmatch
        pats = [include] if isinstance(include, str) else list(include)
        for pat in pats:
            hits = [p for p in pool if fnmatch.fnmatch(os.path.basename(p), pat)]
            if not hits:
                raise RuntimeError(
                    "caption_stage_include pattern matched no image in %s: %r - the order names an "
                    "input this factory does not have" % (src_root, pat))
            forced += hits
        seen_f = set()
        forced = [p for p in forced if not (p in seen_f or seen_f.add(p))]
        job.log("stage: %d image(s) named explicitly by caption_stage_include" % len(forced))
    if count and count < len(pool):
        if stratify:
            groups = {}
            for p in pool:
                groups.setdefault(os.path.basename(os.path.dirname(p)), []).append(p)
            picked, gi = [], sorted(groups)
            per = max(1, count // max(1, len(gi)))
            for g in gi:
                fs = groups[g]
                step = max(1, len(fs) // per)
                picked += [fs[i * step] for i in range(per) if i * step < len(fs)]
            pool = (picked + [p for p in pool if p not in picked])[:count]
        else:
            pool = pool[:count]
        # Named images survive the sample, and are counted within it rather than added on top, so
        # `count` still means what the order says it means.
        if forced:
            keep = [p for p in pool if p not in set(forced)][:max(0, count - len(forced))]
            pool = forced + keep
    elif forced:
        pool = forced + [p for p in pool if p not in set(forced)]
    for p in pool:
        # Flatten with the parent folder in the name so provenance survives the copy and two
        # collections cannot collide on a basename.
        flat = os.path.basename(os.path.dirname(p)) + "__" + os.path.basename(p)
        shutil.copy2(p, os.path.join(dest, flat))
    job.log("stage: %d image(s) from %s -> %s (stratify=%s)"
            % (len(pool), src_root, dest, stratify))
    return len(pool)


# Values that may stand alone because they are globally unique (tested): the draped state, the
# rotation, and the detector's crop / head tags.
STANDALONE_FIELDS = ("state", "view", "crop", "head_crop")


def merge_manifest_fields(prior_fields, new_fields):
    """Merge new_fields into prior_fields per-key, preserving existing keys.

    Updates are additive: new values overwrite existing ones for matching keys,
    but keys present in prior_fields but absent from new_fields are preserved.

    Pinned by test_foundry.py::test_manifest_merge_never_amputates."""
    merged = dict(prior_fields or {})
    for k, rec in (new_fields or {}).items():
        base = dict(merged.get(k) or {})
        base.update(rec or {})
        merged[k] = base
    return merged


def caption_targets(src, exts=(".jpg", ".jpeg", ".png", ".webp"), overwrite=False,
                    newer_than=None):
    """Return (to_caption, skipped_existing) for src, recursively and deterministically.

        A file is DONE if a .txt sidecar exists, unless `overwrite=True`. The sidecar acts as the
        resume key, making captioning idempotent for pause/resume workflows.

        If `overwrite=True` but `newer_than=None`, all existing sidecars are ignored (resetting progress).
        If both are set, only sidecars older than `newer_than` are overwritten; newer ones (written in
        this run) are kept. This prevents pausing from silently redoing work.
    """
    todo, skipped = [], 0
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for fn in sorted(files):
            if fn.lower().endswith(tuple(exts)):
                p = os.path.join(root, fn)
                sidecar = os.path.splitext(p)[0] + ".txt"
                done = False
                if os.path.exists(sidecar):
                    if not overwrite:
                        done = True
                    elif newer_than is not None:
                        try:
                            done = os.path.getmtime(sidecar) >= float(newer_than)
                        except OSError:
                            done = False
                if done:
                    skipped += 1
                else:
                    todo.append(p)
    return todo, skipped


def _vlm_ensure_resident(job, host, model, num_ctx=4096):
    """Load the model and verify full GPU residency.

    Raises if the model cannot fit entirely in VRAM. This prevents silent CPU offload,
    which degrades performance ~20x with no explicit error.
    """
    # num_ctx here sets the KV allocation size at load time. It must match the context
    # length used in subsequent inference calls; otherwise, long inputs truncate silently.
    # Ollama's default is low and will cause massive VRAM over-allocation if not set.
    req = urllib.request.Request(
        host.rstrip("/") + "/api/chat",
        data=json.dumps({"model": model, "stream": False, "keep_alive": "30m",
                         "options": {"num_ctx": int(num_ctx)},
                         "messages": [{"role": "user", "content": "ok"}]}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        r.read()
    with urllib.request.urlopen(host.rstrip("/") + "/api/ps", timeout=30) as r:
        ps = json.load(r)
    for m in ps.get("models", []):
        if m.get("name") == model or m.get("model") == model:
            if m.get("size_vram", 0) >= m.get("size", 1):
                job.log("caption VLM resident: %s (%.1f GB fully in VRAM, num_ctx=%d)"
                        % (model, m.get("size_vram", 0) / 1e9, int(num_ctx)))
                return
            raise RuntimeError("caption VLM %s PARTIALLY resident (%.1f/%.1f GB) - refusing to "
                               "caption at CPU speed" % (model, m.get("size_vram", 0) / 1e9,
                                                         m.get("size", 0) / 1e9))
    raise RuntimeError("caption VLM %s not reported by /api/ps after warmup" % model)


def _vlm_caption_one(host, model, prompt, path, max_side, schema, timeout=240, num_ctx=4096):
    """Caption an image using the shared VLM logic.

    Delegates to p20_dataset._score_image to centralize model-specific handling
    (downscaling, structured-output grammar) and ensure failures raise rather than
    silently return None, which is critical for accurate result counting in bulk pulls."""
    import p20_dataset as _pd
    import requests as _rq

    class _Pool(object):
        def get(self):
            return _rq

    return _pd._score_image(_Pool(), host, model, prompt, path, max_side,
                            timeout=timeout, schema=schema, num_ctx=num_ctx)


def ship_captions(job, src):
    r"""Shipping stage for a caption order.

    Siblings: `ship_render`, `drain_order_renders`, `notify_file`.
    Unlike render draining (fail-soft), this fails loud: silently missing captions leads to stale data assumptions.
    Missing `deliver_to` on the order is not an error; it implies nothing to ship.

    Reachability constraint: The runner executes as the box's console user (interactive). It can reach Softserve shares
    (e.g., the shared drive's UNC form) but CANNOT access drives local to the share host.
    Destinations MUST be declared via UNC paths, not mapped drive letters (which are per-session and invalid here)."""
    dest = (job.spec.get("deliver_to") or "").strip()
    if not dest:
        return
    # Filter by `caption_started_at`: ship only files from this run, ignoring stale artifacts.
    cutoff = job.state.get("caption_started_at") or 0
    skipped_foreign = 0
    n = 0
    for root, _dirs, files in os.walk(src):
        for fn in files:
            if not fn.lower().endswith(".txt") and fn != "_caption_manifest.json":
                continue
            if fn.lower().endswith(".txt") and cutoff:
                try:
                    if os.path.getmtime(os.path.join(root, fn)) < cutoff - 1:
                        skipped_foreign += 1
                        continue
                except OSError:
                    continue
            rel = os.path.relpath(os.path.join(root, fn), src)
            # Manifest names are made unique per-job to prevent concurrent fleet arms from overwriting
            # each other's provenance records in a shared destination directory.
            if fn == "_caption_manifest.json":
                rel = os.path.join(os.path.dirname(rel),
                                   "_caption_manifest_%s.json" % job.name)
            out = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(os.path.join(root, fn), out)
            n += 1
    # Ship source images for verification orders so findings are self-contained.
    # Opt-in (`caption_ship_images`) to avoid duplicating large corpus inputs.
    imgs = 0
    if job.spec.get("caption_ship_images"):
        exts = (".jpg", ".jpeg", ".png", ".webp")
        picked = set(job.spec.get("caption_ship_only") or ())
        for root, _dirs, files in os.walk(src):
            for fn in files:
                if os.path.splitext(fn)[1].lower() not in exts:
                    continue
                if picked and fn not in picked:
                    continue
                out = os.path.join(dest, os.path.relpath(os.path.join(root, fn), src))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copy2(os.path.join(root, fn), out)
                imgs += 1
    extra = (" (%d pre-existing sidecar(s) from an earlier run left behind - not this order's "
             "product)" % skipped_foreign) if skipped_foreign else ""
    also = (" + %d source image(s)" % imgs) if imgs else ""
    job.log("SHIPPED %d caption file(s)%s -> %s%s" % (n, also, dest, extra))
    notify(job, "SHIPPED %d caption file(s)%s to %s%s" % (n, also, dest, extra))


def _vlm_unload(job, host, model):
    """CLEANUP-stage: release the VRAM the order was using. Fail-soft."""
    try:
        req = urllib.request.Request(host.rstrip("/") + "/api/generate",
                                     data=json.dumps({"model": model, "keep_alive": 0}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        job.log("caption VLM unloaded (keep_alive 0)")
    except Exception as e:
        job.log("WARNING: caption VLM unload failed (%r) - keep_alive will expire it" % (e,))


def _ollama_resident():
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=3) as r:
            return bool(json.load(r).get("models"))
    except Exception:
        return False


# When the card was last SEEN busy by this process (a ComfyUI proc/port, or a stop_comfy that found the port held).
# The settle window the gate exists for is measured from that event, not from "now".
_CARD_BUSY_SEEN = [0.0]


def _note_card_busy():
    _CARD_BUSY_SEEN[0] = time.time()


def wait_card_quiet(job, max_wait=180, ollama_busy=True):
    """Wait for the GPU to be idle before launching a trainer.

    Prevents race conditions where previous workloads (e.g., ComfyUI) have not finished tearing down their HIP context, which causes SetDevice/memory faults in the next job's backward pass.

    Args:
        job: The job context for logging and notifications.
        max_wait: Maximum seconds to wait. On timeout, fails open (logs warning and proceeds).
        ollama_busy: If True (default), resident Ollama models count as 'busy' because trainers require the whole GPU. Pass False if the upcoming job uses the scheduler's Ollama instance, where a warm model is an asset, not an occupant.

    Trainers require two consecutive quiet reads to ensure HIP context teardown is complete. Compute orders (ollama_busy=False) settle by status: a card is considered quiet on a single read if it has not been touched within INTERVAL seconds (15s), as the two-read requirement only applies to prevent race conditions with teardown."""
    QUIET_NEEDED = 2
    INTERVAL = 15
    t0 = time.time()
    quiet = 0
    while quiet < QUIET_NEEDED:
        if time.time() - t0 > max_wait:
            job.log("WARNING: wait_card_quiet TIMED OUT at %ds — proceeding anyway (watch for crash)" % max_wait)
            notify(job, "WARNING: card_quiet gate timed out at %ds before launch — proceeding anyway. Watch for HIP crash." % max_wait)
            return
        busy = []
        if not _port_free(8189):
            busy.append(":8189 in use")
        n = _comfy_procs_alive()
        if n > 0:
            busy.append("%d ComfyUI proc(s) alive" % n)
        elif n < 0:
            # -1 means process table unreadable; treat as busy to avoid false quiet.
            busy.append("could not read the process table (assuming NOT quiet)")
        if ollama_busy and _ollama_resident():
            busy.append("Ollama has resident model(s)")
        if not busy:
            quiet += 1
            since_busy = time.time() - _CARD_BUSY_SEEN[0]
            if not ollama_busy and quiet == 1 and since_busy >= INTERVAL:
                job.log("card_quiet: quiet 1/1 - nothing busy on the card for %s, settled" % (
                    "%.0fs" % since_busy if _CARD_BUSY_SEEN[0] else "this runner's lifetime"))
                break
            job.log("card_quiet: quiet %d/%d" % (quiet, QUIET_NEEDED))
        else:
            quiet = 0
            _note_card_busy()
            job.log("card_quiet: busy (%s) — waiting" % ", ".join(busy))
        if quiet < QUIET_NEEDED:
            time.sleep(INTERVAL)
    job.log("card_quiet: card is quiet after %.0fs — safe to launch" % (time.time() - t0))


def cond_needs_bake(name, text):
    """Return why this conditioning must be (re)baked, or "" if the cache is trustworthy.

    The cache is global, keyed by name, and never expires. It relies on a .txt sidecar
    to verify that the cached .cond matches the current prompt text. If the sidecar is
    missing or mismatched, the entry is unverifiable and therefore treated as stale."""
    cond = os.path.join(COND_DIR, name + ".cond")
    if not os.path.exists(cond):
        return "absent"
    try:
        with open(os.path.join(COND_DIR, name + ".txt"), "r", encoding="utf-8") as f:
            baked = f.read()
    except OSError:
        return "no text record (pre-2026-07-26) - cannot verify"
    return "" if baked == (text or "") else "TEXT CHANGED since bake"


EMBEDS_CACHE = os.path.join(COND_DIR, "p20_embeds_default.pt")


def _caption_fingerprint(folder):
    """Hash of the caption SET: every filename and its bytes, in a stable order."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(glob.glob(os.path.join(folder, "*.txt"))):
        h.update(os.path.basename(p).encode("utf-8"))
        with open(p, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def trainer_dataset_folder(job):
    """The folder_path the trainer will actually read, from its own yaml. "" if unreadable."""
    cfg = launcher_config(job) or os.path.join(P.AI_TOOLKIT, "config", job.name + ".yaml")
    try:
        with open(cfg, "r", encoding="utf-8", errors="ignore") as f:
            m = re.search(r"folder_path\s*:\s*[\"']?([^\"'\n]+)", f.read())
        return (m.group(1).strip() if m else "")
    except OSError:
        return ""


def embeds_needs_rebuild(job):
    """
    Why the trainer's text-embeds cache must be discarded, or "" if trustworthy.

    The ai-toolkit resume gate keys this cache on the trigger word (defaulting to
    "default"), resulting in a single shared file regardless of corpus. Validation
    relies only on os.path.exists(), so changed captions cause silent stale-state
    failures at runtime. A .captions sidecar records the fingerprint of the caption
    set; if missing, the cache is unverifiable and considered stale.
    """
    if not os.path.exists(EMBEDS_CACHE):
        return ""                                  # nothing to invalidate; the trainer will build it
    folder = trainer_dataset_folder(job)
    if not folder or not os.path.isdir(folder):
        return "cannot locate the dataset folder - unverifiable"
    try:
        with open(EMBEDS_CACHE + ".captions", "r", encoding="utf-8") as f:
            recorded = f.read().strip()
    except OSError:
        return "no caption record - cannot verify"
    return "" if recorded == _caption_fingerprint(folder) else "CAPTIONS CHANGED since the cache was built"


def batch_dir():
    """Returns the batch directory relative to `CURRENT_RUN`.

    This function (instead of a module-level constant) avoids import-time
    dependencies on constants defined later in the file, preventing NameError
    during module initialization."""
    return os.path.join(os.path.dirname(CURRENT_RUN), "batches")


def batch_fingerprint(job):
    """The invariants that make two PROBE arms comparable. Everything here MUST match across arms;
    what is deliberately EXCLUDED is the variable under test (`probe_lora`) and anything cosmetic.

    Ordering is preserved, not sorted: a different seed ORDER is still the same comparison, but a
    different prompt-to-name binding is NOT, so prompts are keyed by name.
    """
    def _ptuple(p):
        t = (str(p.get("name")), (p.get("text") or "").strip(), bool(p.get("base", True)))
        # extra_loras is part of the comparison design: two arms whose "same" prompt differs only
        # in its stack are NOT like-for-like. Appended ONLY when present so every fingerprint
        # recorded before the field existed stays byte-identical (same rule as render_key).
        ex = [a for a in (p.get("extra_loras") or []) if isinstance(a, dict) and a.get("name")]
        if ex:
            t = t + (json.dumps(
                [{"name": a["name"], "strength": round(float(a.get("strength", 1.0)), 2)}
                 for a in ex], sort_keys=True),)
        # steps is comparison-relevant for the same reason as the stack (only-when-present rule).
        if p.get("steps") and int(p["steps"]) != 20:
            t = t + ("steps=%d" % int(p["steps"]),)
        return t
    prompts = sorted(_ptuple(p) for p in (job.probe_prompts or []))
    payload = {
        "prompts": prompts,
        "seeds": sorted(job.probe_seeds or []),
        "strengths": sorted(job.probe_strengths or []),
        "sweep_names": sorted(job.sweep_names or []),
        "probe_step": job.probe_step,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], payload


def batch_guard(job):
    """Refuse arms whose comparison invariants disagree with siblings.

    The first arm writes the fingerprint; subsequent arms verify against it.
    This function checks technical consistency, not compositional quality (e.g.,
    crop fit or pose occlusion).
    """
    if job.mode != "probe":
        return                      # training batches exist to VARY config; a check would be wrong
    name = (job.spec.get("batch") or "").strip()
    if not name or int(job.spec.get("batch_size") or 1) < 2:
        return                      # not a multi-arm comparison
    fp, payload = batch_fingerprint(job)
    _bd = batch_dir()
    os.makedirs(_bd, exist_ok=True)
    path = os.path.join(_bd, re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"batch": name, "fingerprint": fp, "first_arm": job.name,
                       "invariants": payload}, f, indent=1)
        job.log("batch '%s': first arm - comparison invariants recorded (%s)" % (name, fp))
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            prior = json.load(f)
    except Exception as e:
        # Unreadable registry: SELF-HEAL but say so LOUDLY. Archiving a legitimate order over a
        # corrupt sidecar would be disproportionate (this is a quality gate, not a safety gate), but
        # silently re-recording would leave earlier arms unverified while LOOKING verified -- the
        # "unverifiable treated as verified" shape this whole guard exists to prevent. Move the bad
        # file aside rather than overwrite it, per the standing rule: retire, do not delete.
        try:
            os.replace(path, path + ".corrupt-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        except OSError:
            pass
        job.log("WARNING: batch '%s' registry was UNREADABLE (%r). Moved aside and re-recorded from "
                "THIS arm - which means this arm's comparability to any EARLIER arm is UNVERIFIED. "
                "Treat any cross-arm result from this batch as suspect unless the earlier arms are "
                "re-run." % (name, e))
        return
    if prior.get("fingerprint") == fp:
        job.log("batch '%s': invariants MATCH arm '%s' (%s) - comparison is like-for-like"
                % (name, prior.get("first_arm"), fp))
        return
    diff = []
    for k, v in payload.items():
        pv = (prior.get("invariants") or {}).get(k)
        if pv != v:
            diff.append("  %s:\n    this arm : %r\n    arm '%s': %r"
                        % (k, v, prior.get("first_arm"), pv))
    raise RuntimeError(
        "BATCH INVARIANT MISMATCH - this arm is NOT comparable to its siblings, so any result from "
        "it would be a confounded comparison.\n"
        "  batch      : %s\n  this arm   : %s (%s)\n  first arm  : %s (%s)\n%s\n"
        "Fix the spec so the invariants match, or use a DIFFERENT batch name if this is meant to be "
        "a separate comparison. To deliberately restart a batch, delete %s"
        % (name, job.name, fp, prior.get("first_arm"), prior.get("fingerprint"),
           "\n".join(diff) or "  (no field-level diff available)", path))


def is_benchmark_order(spec):
    """Return True if the spec declares weights as a byproduct (e.g., BENCHMARK).

    Checks the TYPE TOKEN in `kind` against known non-product types. Returns
    False for empty, unknown, or product/training kinds, ensuring normal
    registration behavior."""
    k = (spec.get("kind") or "").strip().lower()
    return k.startswith(("benchmark", "measurement", "smoke test", "verification"))


def retire_stale_embeds(job):
    """Archive text-embeds cache if stale, recording new fingerprint. Skip for non-training jobs.

        Archive (not delete) to allow next run to verify integrity rather than guessing. Sidecar is
        (re)written so subsequent runs can check consistency.

        PROBE orders lack a corpus folder, causing embeds_needs_rebuild() to report unverifiable status.
        Without this guard, every probe triggers a cache rebuild for the next training run. We block
        here (not at PREFLIGHT) to cover crash-retry call sites with the same invariant.
    """
    if getattr(job, "mode", "train") != "train":
        return
    if getattr(job, "base", "flux2-dev") != "flux2-dev":
        return          # the cache belongs to the FLUX.2 trainer; another base's trainer keeps its own
    why = embeds_needs_rebuild(job)
    if why:
        dst = EMBEDS_CACHE + ".stale-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            shutil.move(EMBEDS_CACHE, dst)
            job.log("embeds cache RETIRED (%s) -> %s; trainer will rebuild it" % (why, os.path.basename(dst)))
        except OSError as e:
            job.log("WARNING: could not retire stale embeds cache (%r) - the run may crash on the stub encoder" % (e,))
            return
    folder = trainer_dataset_folder(job)
    if folder and os.path.isdir(folder):
        try:
            with open(EMBEDS_CACHE + ".captions", "w", encoding="utf-8") as f:
                f.write(_caption_fingerprint(folder))
        except OSError:
            pass


def bake_missing(job, prompts):
    """Bake conditioning for prompts where the .cond file is missing,
    or where its stored prompt text differs from the current request.

    Uses a .txt sidecar to verify prompt integrity; treats missing
    sidecars (legacy entries) as stale and re-bakes them."""
    if getattr(job, "base", "flux2-dev") == "krea2":
        return          # Krea encodes in-graph (p20_krea_render); a Mistral .cond would be the wrong encoder
    # Kontext EDIT prompts run their own clip/t5 in-graph, so P20 conditions are never loaded; skip them.
    # EXCEPT FLUX2-engine inpaint: it uses the baked-cond path (P20LoadConditioning),
    # so its text MUST be baked here to avoid missing .cond errors at render time.
    prompts = [p for p in prompts if (p.get("inpaint") or {}).get("engine") == "flux2"
               or not ((p.get("kontext") or {}).get("source")
                       or (p.get("inpaint") or {}).get("source"))]
    # Encoder-specific .cond files are not interchangeable (flux2 uses Mistral,
    # klein uses Qwen3). Serving the wrong one is a silent category error,
    # so each encoder gets its own suffix (e.g., `<prompt>.klein`) to enforce
    # strict cache isolation.
    enc = (job.spec.get("bake_encoder") or "").strip().lower()
    suffix = ".klein" if enc == "klein" else ""
    env = dict(os.environ)
    if enc:
        env["P20_BAKE_ENCODER"] = enc

    todo = [(p, why) for p in prompts
            for why in [cond_needs_bake(p["name"] + suffix, p.get("text") or "")] if why]
    for p, why in todo:
        cname = p["name"] + suffix
        job.log("baking conditioning: %s (%s)%s" % (cname, why, " [encoder=%s]" % enc if enc else ""))
        r = subprocess.run([PY, BAKE, p["name"], p["text"]], capture_output=True, text=True,
                           timeout=900, env=env)
        job.log("bake %s -> %s" % (cname, (r.stdout or "").strip().splitlines()[-1] if r.stdout else r.returncode))
        # Sidecar written only AFTER a successful-looking bake, so a failed bake stays "unverifiable"
        # and gets retried next time rather than being blessed by a stale .cond.
        if os.path.exists(os.path.join(COND_DIR, cname + ".cond")):
            try:
                with open(os.path.join(COND_DIR, cname + ".txt"), "w", encoding="utf-8") as f:
                    f.write(p.get("text") or "")
            except OSError as e:
                job.log("WARNING: could not record bake text for %s (%r) - it will re-bake next run"
                        % (cname, e))


_HORIZONTAL_POSE = re.compile(r"reclin|lying|lie |laid|lounge|lounging|sunbath|supine|prone|"
                              r"sprawl|recumbent|odalisque", re.I)
_VERTICAL_POSE = re.compile(r"standing|full[- ]body|full length|kneeling|seated|sitting|"
                             # contrapposto/raised arms imply upright, so bias full-body framing via aspect ratio (framing is constant/un-captioned in corpus)
                             r"contrapposto|arms raised|arm raised", re.I)


def pick_canvas(text):
    """Pick aspect ratio based on pose to avoid distortion.

    Square (1024x1024) distorts horizontal/vertical poses (e.g. extra limbs).
    Non-square canvases (~1MP) preserve VRAM/pace parity."""
    if _HORIZONTAL_POSE.search(text or ""):
        return 1216, 832
    if _VERTICAL_POSE.search(text or ""):
        return 832, 1216
    return 1024, 1024


def milestone_prompts(job, milestone):
    """The prompt set for ONE preview milestone: every fixed control, plus one rotating pose.

    The controls are deliberately unchanging (see render()'s seed comment -- same text and same seed
    at every checkpoint is what makes the ladder evidence rather than a slideshow). The rotating slot
    is additive, so nothing about the comparison is lost; it just stops the ladder from being two
    identical photos all night.

    Rotation advances one pose per milestone (ordinal), NOT by arithmetic on the step number. The
    obvious `milestone // 100 % len` collapses neighbours -- with this run's cadence, 200 and 250 both
    land on the same pose, as do 700 and 750 -- which defeats the point. Milestones are irregular
    (extra_previews 100..900 alongside every-250), so position in the planned list is the only thing
    that steps evenly.

    Determinism: for a given cadence this is stable, so re-rendering ck400 reproduces the pose already
    sitting in Discord. Editing the cadence mid-run WILL reshuffle the rotating slot -- acceptable
    precisely because this slot is not the comparability anchor; the fixed controls above it never
    move, and they are what the ladder is read against.
    """
    if not job.rotate_prompts:
        return job.preview_prompts
    planned = sorted(set(list(job.spec.get("extra_previews") or [])
                         + list(range(job.preview_every, job.total_steps + 1, job.preview_every))))
    m = int(milestone)
    pos = planned.index(m) if m in planned else sum(1 for x in planned if x < m)
    return job.preview_prompts + [job.rotate_prompts[pos % len(job.rotate_prompts)]]


def ship_render(job, png, src=None):
    """Copy each render to the order's own folder.

        COPY, not move: ComfyUI owns the source file; the render-index and idempotent-resume checks
        depend on its presence. Moving it would break history tracking and trigger re-renders.

        Non-fatal by design: a failed copy must never destroy a successfully rendered file.
    """
    try:
        d = os.path.join(P.RENDERS, job.name)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, os.path.basename(png))
        # Copy under THIS order's name; provenance is tracked via log/DM, not filename.
        if not os.path.exists(dst):
            shutil.copy2(src or png, dst)
        return dst
    except OSError as e:
        job.log("WARNING: could not ship a copy of %s to the order folder (%r)"
                % (os.path.basename(png), e))
        return ""


def render(job, ckpt_path, step, prompts, dm_each=False, base_only=False, tag="", strength=None,
           seed=None):
    """Render images with optional streaming and base-model comparison.

    Args:
        dm_each (bool): If True, stream each finished PNG to Discord immediately
            instead of batching at cycle end. Reduces initial delivery latency.
        base_only (bool): If True, render using only the base model (no adapter).
            Used to measure base capability for LoRAs that do not carry identity;
            compares against a prompt that explicitly names the subject.
    """
    # Non-default seed MUST be in the filename: renders are disk-keyed by prefix.
    # Without it, two seeds sharing a prefix cause the second to silently reuse
    # the first image (wrong comparison, not an error). Embedding here prevents
    # callers from forgetting, which causes silent failures.
    _seed = DEFAULT_SEED if seed is None else int(seed)
    if _seed != DEFAULT_SEED:
        tag = "%s_sd%d" % (tag, _seed)
    # Default strength to SHIP_STRENGTH (not SLIM's 1.0) here, before render_key().
    # The index keys cache entries on strength; if applied later, the key would remain
    # None while pixels use 0.6, causing cached 1.0 renders to be served for 0.6 requests.
    # Resolving early keeps old (None-keyed) and new (0.6-keyed) cells distinguishable.
    if strength is None and not base_only:
        strength = SHIP_STRENGTH
    lora = "%s_ck%d.safetensors" % (job.name, step)
    if base_only:
        lora = "none"
    else:
        subprocess.run(["cmd", "/c", "copy", "/Y", ckpt_path, os.path.join(LORAS, lora)], capture_output=True)
    # Returns (produced_count, failed_count).
    # Caller decides if zero produced images is fatal:
    # probes fail fast; training previews tolerate misses.
    _produced, _failed = 0, 0
    for p in prompts:
        # Pause honored at render boundaries only; current render completes (work never wasted).
        if pause_requested():
            raise PauseRequested("operator STOP_AFTER_RENDER honored before %s" % p.get("name"))
        if order_cancel_requested(job.name):
            raise OrderCancelled("order %s cancelled at the desk (before %s)"
                                 % (job.name, p.get("name")))
        # Refresh claim per prompt: prevents TTL lapse during long sets (>2400s) which causes
        # orchestrator to offer card as free mid-render (collision). Also keeps dashboard live.
        try:
            publish_current_run(job, state="rendering", step=step,
                                phase="rendering %s%s" % (p["name"], tag), ttl=RENDER_CLAIM_TTL)
        except Exception:
            pass
        publish_runner_card("working", "rendering %s%s" % (p["name"], tag))
        # `tag` is EMPTY for the normal ladder: these filenames are the resume key (an existing PNG
        # means "already rendered, skip"), so renaming them would re-render the whole history.
        prefix = "%s_ck%d_%s%s" % (job.name, step, p["name"], tag)
        # Use env var (FOUNDRY_COMFY_OUTPUT) instead of hardcoded path to support per-host output dirs.
        png = os.path.join(COMFY_OUTPUT_DIR, "%s_00001_.png" % prefix)
        if os.path.exists(png):
            job.log("render %s -> already exists, skipping (idempotent resume)" % prefix)
            _produced += 1          # a resumed cell IS a delivered render, not a failure
            continue
        _text = (p.get("text") or p["name"]).strip()
        # extra_loras: pass-through to SLIM for stacked LoRA configs (e.g., person + capability). 
        # Entries are dicts with 'path' (staged under job _ck prefix for cleanup) or 'name'. 
        # Note: applies even to base_only cells (primary "none" + extras = extras alone).
        _extras_cmd, _extras_key = [], []
        for _xi, _a in enumerate(a for a in (p.get("extra_loras") or [])
                                 if isinstance(a, dict) and (a.get("path") or a.get("name"))):
            _xs = round(float(_a.get("strength", 1.0)), 2)
            if _a.get("path"):
                _staged = "%s_ck%d_x%d.safetensors" % (job.name, step, _xi)
                subprocess.run(["cmd", "/c", "copy", "/Y", _a["path"],
                                os.path.join(LORAS, _staged)], capture_output=True)
                _extras_cmd.append({"name": _staged, "strength": _xs})
                _extras_key.append({"name": os.path.basename(_a["path"]), "strength": _xs})
            else:
                _extras_cmd.append({"name": _a["name"], "strength": _xs})
                _extras_key.append({"name": _a["name"], "strength": _xs})
        _arm = ("BASE - no adapter" if base_only
                else "ADAPTER %.2f" % (1.0 if strength is None else float(strength)))
        if _extras_key:
            _arm += " + " + ",".join("%s@%.2f" % (a["name"], a["strength"]) for a in _extras_key)
        _steps = p.get("steps")
        _steps = int(_steps) if _steps else None
        if _steps and _steps != 20:
            _arm += " @ %d steps" % _steps
        # If kontext or inpaint has a source, route to edit path.
        # These cells run their own conditioning (skip P20 Mistral bake
        # and primary/extras staging). Prompt text serves as the instruction.
        _ink = p.get("inpaint") or {}
        _kxb = p.get("kontext") or {}
        _kx = _kxb.get("source") or _ink.get("source")
        _guid = _kxb.get("guidance") if _kxb.get("guidance") is not None else _ink.get("guidance")
        _guid = float(_guid) if _guid is not None else None
        _edit = None
        _ink_flux2 = bool(_ink.get("source")) and _ink.get("engine") == "flux2"
        if _ink_flux2:
            # FLUX2 uses distinct mode string to prevent cache/index collision with Flux1 for identical params.
            _edit = {"mode": "inpaint_flux2",
                     "mask": _ink.get("mask") or {},
                     "denoise": round(float(_ink.get("denoise", 0.60)), 3)}
            _arm = "INPAINT-FLUX2 of %s (denoise %.2f) | %s" % (
                os.path.basename(_kx), _edit["denoise"], _arm)
        elif _ink.get("source"):
            _edit = {"mode": "inpaint",
                     "mask": _ink.get("mask") or {},
                     "denoise": round(float(_ink.get("denoise", 0.35)), 3),
                     "loras": [{"name": a["name"], "strength": round(float(a.get("strength", 1.0)), 2)}
                               for a in (_ink.get("loras") or []) if a.get("name")],
                     "unet": _ink.get("unet") or "default"}
            _arm = "INPAINT of %s (denoise %.2f)" % (os.path.basename(_kx), _edit["denoise"])
            if _edit["loras"]:
                _arm += " + " + ",".join("%s@%.2f" % (a["name"], a["strength"]) for a in _edit["loras"])
        elif _kx:
            _arm = "KONTEXT EDIT of %s" % os.path.basename(_kx)
            _kloras = [{"name": a["name"], "strength": round(float(a.get("strength", 1.0)), 2)}
                       for a in (_kxb.get("loras") or []) if a.get("name")]
            if _kloras:
                _edit = {"mode": "kontext", "loras": _kloras}
                _arm += " + " + ",".join("%s@%.2f" % (a["name"], a["strength"]) for a in _kloras)
        if _kx and _guid is not None and abs(_guid - 2.5) > 1e-9:
            _arm += " @ guidance %.1f" % _guid
        # Has this exact cell already been rendered, by ANY order? Renders are deterministic, so a
        # hit means the pixels are already on disk and the card would produce them again byte for
        # byte. Deliver the existing image, say where it came from, and keep the GPU for work that
        # is actually new. See RENDER_INDEX for what this cost before it existed.
        _key = render_key(_text, _seed, ckpt_path, strength, base_only, extras=_extras_key,
                          steps=_steps, kontext_source=_kx, guidance=_guid, edit_spec=_edit,
                          unet=render_unet())
        _prior = render_index_lookup(_key)
        # force_render: bypass cache to ensure fresh generation for controlled experiments where
        # session identity is the variable under test. Default behavior (no flag) is REUSE.
        if _prior and job.spec.get("force_render"):
            job.log("render %s -> index HIT from %s/%s but force_render is set - rendering fresh"
                    % (prefix, _prior["order"], _prior["arm"]))
            _prior = None
        if _prior and _prior["file"] != png:
            job.log("render %s -> REUSED from %s/%s (identical cell, card not spent)"
                    % (prefix, _prior["order"], _prior["arm"]))
            # Copy the prior file to create a local resume key so reuse is idempotent;
            # otherwise, missing artifacts trigger redundant delivery notifications on each resume.
            try:
                shutil.copyfile(_prior["file"], png)
            except OSError as e:
                job.log("WARNING: could not materialize reuse resume-key for %s (%r) - this cell "
                        "will re-deliver on the next resume" % (prefix, e))
            if dm_each:
                notify_file(job, "%s | ck%d\n%s\n(already rendered by %s - identical cell, reused)"
                            % (_arm, step, _text, _prior["order"]), _prior["file"])
            ship_render(job, png, src=_prior["file"])
            _produced += 1          # a REUSED cell is a delivered render, not a failure
            continue
        # fresh_context_per_render: if declared by order, restart ComfyUI for every
        # SPENT render. Placed AFTER reuse/skip checks so index-served cells never bounce.
        if job.spec.get("fresh_context_per_render") and not comfy_is_cold():
            job.log("fresh_context_per_render (order policy) -> bouncing ComfyUI before %s" % prefix)
            stop_comfy(job)
            time.sleep(5)
            ensure_comfy(job)
        # On timeout or missing output, retry once with fresh ComfyUI.
        # Timeout is 900s (empirically covers stalls up to 860s; >1800s context is usually wedged).
        for attempt in (1, 2, 3):
            job.log("render %s (attempt %d)" % (prefix, attempt))
            try:
                w, h = pick_canvas(p.get("text", ""))
                # Seed must be explicit to allow positional width/height args (index > 4).
                # Constant seed ensures comparability across ladder checkpoints.
                # Timeout: 480 s (~1.8x observed max). Re-derive from logs if render path changes.
                # arg7 = adapter strength (SLIM clamps 0.1-1.5). Omitted for normal ladder;
                # passed only for A/B strength sweep.
                if _ink_flux2:
                    # source, prefix, cond(name), mask, seed, steps, guidance, denoise, then the
                    # SLIM stack args (lora/strength/extras) with identical semantics to arg1/7/8.
                    _cmd = [PY, INPAINT_FLUX2, _kx, prefix, p["name"],
                            json.dumps(_ink.get("mask") or {}), str(_seed), str(_steps or 20),
                            str(2.5 if _guid is None else _guid), str(_edit["denoise"]),
                            (lora or "none"), str(1.0 if strength is None else strength),
                            json.dumps(_extras_cmd)]
                elif _ink.get("source"):
                    _cmd = [PY, INPAINT_SLIM, _kx, prefix, _text,
                            json.dumps(_ink.get("mask") or {}), str(_seed), str(_steps or 20),
                            str(2.5 if _guid is None else _guid), str(_edit["denoise"]),
                            json.dumps(_edit["loras"])]
                    if _ink.get("unet"):
                        _cmd.append(_ink["unet"])
                elif _kx:
                    _cmd = [PY, KONTEXT_SLIM, _kx, prefix, _text, str(_seed),
                            str(_steps or 20), str(2.5 if _guid is None else _guid)]
                    if (_edit or {}).get("loras"):
                        _cmd.append(json.dumps(_edit["loras"]))
                elif getattr(job, "base", "flux2-dev") == "krea2":
                    # A Krea checkpoint only loads into Krea: its own renderer, the prompt TEXT (no .cond)
                    _cmd = [PY, KREA_RENDER, lora, prefix, _text, str(_seed), str(w), str(h),
                            str(1.0 if strength is None else strength)]
                else:
                    _cmd = [PY, SLIM, lora, prefix, p["name"], str(_seed), str(w), str(h)]
                    # arg8 (extra_loras) and arg9 (steps) are positional AFTER strength, so any
                    # later arg forces the earlier ones to be stated explicitly at their defaults.
                    if strength is not None or _extras_cmd or _steps:
                        _cmd.append(str(1.0 if strength is None else strength))
                    if _extras_cmd or _steps:
                        _cmd.append(json.dumps(_extras_cmd))
                    if _steps:
                        _cmd.append(str(_steps))
                job.log("  supervising by ComfyUI status (%s server)"
                        % ("COLD" if comfy_is_cold() else "warm"))
                last, _why = supervise_render(job, _cmd, png)
            except Exception as e:
                last = "supervisor error %r" % (e,)
            job.log("render %s -> %s" % (prefix, last))
            if os.path.exists(png):
                break
            # Retry WARM in the same session first: bouncing makes the retry cold, reproducing a cold-start
            # overrun. Only a failed WARM attempt supports "the context is wedged" -- then bounce. Exception:
            # connection-refused means the server is gone; skip the warm retry and bounce immediately.
            _gone = any(k in last for k in ("URLError", "10061", "No connection could be made",
                                            "actively refused", "ConnectionRefused"))
            if _gone:
                job.log("ComfyUI is GONE, not slow (%s) -> bouncing NOW; a warm retry cannot reach a "
                        "dead server and would waste an attempt" % last[:70])
                stop_comfy(job)
                time.sleep(5)
                ensure_comfy(job)
            elif attempt == 1:
                job.log("no PNG -> retrying in the SAME session (it is warm now; a bounce would "
                        "make the retry cold again, which is what broke v8's previews)")
                time.sleep(2)
            elif attempt == 2:
                job.log("no PNG after a WARM attempt -> now the context really is suspect: "
                        "kill-and-requeue, bouncing ComfyUI")
                stop_comfy(job)
                time.sleep(5)
                ensure_comfy(job)
        if os.path.exists(png):
            # Compare pixels, not bytes (PNG metadata differs by order).
            # If the reference is missing or unverifiable, report UNVERIFIABLE (never DIFFER).
            # A broken verifier must not fail the render.
            _verdict = ""
            _ref = p.get("verify_against")
            # Skip verification on the structural _LORA pass (uses DEFAULT strength/seed, mismatching
            # the specific sweep cell's reference). This prevents spurious DIFFER reports.
            # Residual limitation: verify_against fires for every seed/strength in a sweep;
            # users should limit verifying orders to exactly one seed and one strength.
            if _ref and tag == "_LORA":
                job.log("  verify %s: SKIPPED on the structural _LORA pass (default strength/seed "
                        "- not the cell this reference describes)" % prefix)
                _ref = None
            if _ref:
                try:
                    _vr = subprocess.run([PY, PIXEL_HASH, png, _ref],
                                         capture_output=True, text=True, timeout=120)
                    for _ln in (_vr.stdout or "").strip().splitlines():
                        job.log("  verify %s: %s" % (prefix, _ln))
                        if _ln.startswith(("VERDICT", "UNVERIFIABLE")):
                            _verdict = _ln
                except Exception as e:
                    job.log("  verify %s: FAILED to run (%r) - render itself is unaffected"
                            % (prefix, e))
            if dm_each:
                # Caption must include `_arm` to distinguish A/B test variants.
                # Without it, identical prompts from different arms are indistinguishable
                # in notifications/renders that lack the filename tag.
                notify_file(job, "%s | ck%d\n%s%s"
                            % (_arm, step, _text, ("\n" + _verdict) if _verdict else ""), png)
            render_index_put(_key, job.name, "%s%s" % (p["name"], tag), png, _text)
            ship_render(job, png)
            _produced += 1
        else:
            _failed += 1
            notify(job, "render %s FAILED 3x (cold, warm, and a fresh-ComfyUI retry) — skipping it; "
                        "the rest of the set continues." % prefix)
    return _produced, _failed


CURRENT_RUN = P.CURRENT_RUN
# Identify this node to the scheduler. Do NOT use a static default fallback (e.g., "mothership"): if the
# env var is missing, falling back to a valid-looking sentinel causes this box to silently impersonate
# another, corrupting fleet state and scheduling tasks onto incompatible GPUs.
FLEET_HOST = P.this_box()
# Every endpoint of the head derives from HAI_URL, the factory's link to the head. An empty
# HAI_URL means this factory is not linked: it works from its own queue and pushes nothing.
ORCH_BASE = P.head_url()
FLEET_STATE_URL = ORCH_BASE + "/fleet/run-state" if ORCH_BASE else ""
_UNLINKED_NOTED = [False]


def _unlinked_note():
    """A factory with no head address pushes nothing. Say it once, plainly, rather than
    failing silently on every poll."""
    if not ORCH_BASE and not _UNLINKED_NOTED[0]:
        _UNLINKED_NOTED[0] = True
        line = "fleet: no HAI_URL set - not linked to a head; local queue only, nothing pushed"
        print("%s  [QUEUE] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), line), flush=True)
        try:
            with open(P.QUEUE_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

# Quick previews use a different, smaller base model for speed; they indicate steering/concept binding
# but are not representative of final ship quality. Tagged 'APPROX' to prevent accidental promotion.
QUICK_TAG = "APPROX"
RESCUE_PAUSE = os.path.join(P.STATE_DIR, "RESCUE_PAUSE")   # daemon-requested lossless recycle (v0.5 handshake)


def publish_current_run(job, state="training", step=None, eta="", phase="", ttl=None, work_beat=None, gen=None):
    """Publish the authoritative record of the current run and GPU ownership status.

        Replaces fragile heuristics (e.g., globbing for newest files) with a single
        source of truth so consumers can reliably distinguish active training from
        paused/stopped runs without inferring state from stale artifacts.

        `state` indicates whether the GPU is busy:
          training : a trainer process is alive and owns the GPU.
          paused   : this run is current but nothing is on the card (STOP_NOW,
                     rescue recycle, or crash). The scheduler treats the card as FREE.

        State is derived from `trainer_running()` at publish time rather than intent,
        because clean exits and crashes look identical post-facto. Whether to RESUME
        is a human decision; whether the card is BUSY is an observable fact.

        `host` identifies which physical card this run speaks for, enabling
        fleet-wide consumers to map runs to specific GPUs.
    """
    # Embed queue depth in `phase` string so homepage consumes it verbatim.
    # "behind" = pending orders minus current one. Fail-soft: never break publish on glob error.
    try:
        _behind = max(0, len(queued_orders()) - 1)
        if phase and _behind:
            phase = "%s · +%d queued" % (phase, _behind)
    except Exception:
        pass
    payload = None
    try:
        payload = {"name": job.name, "log": job.log_path, "output_dir": job.output_dir,
                       "total_steps": job.total_steps,
                   "state_file": job.state_path, "spec_file": job.spec_path,
                   "state": state, "host": FLEET_HOST,
                   "step": int(step or 0), "eta": eta or "", "phase": phase or "",
                   "started": job.state.get("started"),
                   # the payload's own liveness (`[beat] N` lines) - the stall demon reads a moving beat as work
                   "work_beat": work_beat,
                   # the order's model work for the deck's Speed / Step / Context rows (EngineRates.view)
                   "gen": gen,
                   "updated": datetime.datetime.now().isoformat()}
        with open(CURRENT_RUN, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as e:
        job.log("WARNING: could not publish CURRENT_RUN (%r) - consumers fall back to globbing" % (e,))
    # Also declare to FLEET for rolling-TTL liveness: crashed runs stop refreshing and auto-free.
    # Fail-soft: telemetry must never break a training run.
    try:
        if payload and ORCH_BASE:
            body = json.dumps({"host": FLEET_HOST, "name": job.name, "state": state,
                               "total_steps": job.total_steps,
                               "step": int(payload.get("step") or 0),
                               "eta": payload.get("eta") or "",
                               "phase": payload.get("phase") or "",
                               "started": payload.get("started") or "",
                               # WHAT THIS FACTORY CAN BUILD, on the push that already exists.
                               # The scheduler knew hardware and not what is installed, and
                               # "free" + "cannot" is the worst pair to route on.
                               "capabilities": declared_capabilities(),
                               "gen": gen,
                               "ttl": int(ttl or POLL * 6)}).encode("utf-8")
            rq = urllib.request.Request(FLEET_STATE_URL, data=body,
                                        headers={"Content-Type": "application/json"})
            urllib.request.urlopen(rq, timeout=5).read()
    except Exception as e:
        job.log("WARNING: could not declare run state to the fleet (%r)" % (e,))


def retire_staged_loras(job):
    """Archive or delete staged LoRA copies scoped to this run.\n\nFiles matching ``<job_name>_ck*.safetensors`` are processed:\n- If a copy's sha256 matches a canonical source (the run's output or its ``probe_lora``),\n  it is DELETED as a redundant duplicate.\n- Otherwise, it is ARCHIVED to avoid data loss if no other copy exists.\n\nOperations are scoped by the job name prefix to prevent interference with\nother runs. Moves are same-volume renames: reversible and atomic."""
    try:
        pat = os.path.join(LORAS, "%s_ck*.safetensors" % job.name)
        mine = sorted(glob.glob(pat))
        if not mine:
            return
        # canonical candidates this run could have staged from
        cands = []
        try:
            if os.path.isdir(job.output_dir):
                cands += [os.path.join(job.output_dir, f) for f in os.listdir(job.output_dir)
                          if f.endswith(".safetensors")]
        except OSError:
            pass
        if getattr(job, "probe_lora", "") and os.path.exists(job.probe_lora):
            cands.append(job.probe_lora)
        # extra_loras paths must be in cands; otherwise verified copies are archived
        # as "unverifiable" and deleted.
        for _p in (getattr(job, "probe_prompts", None) or []):
            for _a in (_p.get("extra_loras") or []):
                if isinstance(_a, dict) and _a.get("path") and os.path.exists(_a["path"]):
                    cands.append(_a["path"])
        canon = {}
        for c in cands:
            try:
                canon.setdefault(os.path.getsize(c), []).append(c)
            except OSError:
                pass
        deleted = 0
        unverified = []
        for p in mine:
            try:
                sz = os.path.getsize(p)
                hs = {_file_sha256(c) for c in canon.get(sz, [])}
                if hs and _file_sha256(p) in hs:
                    os.remove(p)
                    deleted += 1
                else:
                    unverified.append(p)
            except OSError:
                unverified.append(p)
        if deleted:
            job.log("retired %d staged LoRA cop%s by DELETION (content-verified duplicates of "
                    "canonical)" % (deleted, "y" if deleted == 1 else "ies"))
        if not unverified:
            return
        job.log("WARNING: %d staged cop%s could NOT be verified against a canonical - archiving "
                "instead of deleting (may be an only-surviving copy)"
                % (len(unverified), "y" if len(unverified) == 1 else "ies"))
        mine = unverified
        dest = os.path.join(os.path.dirname(RESCUE_LOG), "..", "archive",
                            datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), "staged-loras")
        dest = os.path.normpath(dest)
        os.makedirs(dest, exist_ok=True)
        moved, mb = 0, 0.0
        for src in mine:
            try:
                mb += os.path.getsize(src) / 1048576.0
                shutil.move(src, os.path.join(dest, os.path.basename(src)))
                moved += 1
            except Exception as e:
                job.log("WARNING: could not retire %s (%r)" % (os.path.basename(src), e))
        job.log("retired %d staged LoRA copies (%.0f MB) -> %s" % (moved, mb, dest))
    except Exception as e:
        job.log("WARNING: staged-LoRA retirement failed (%r)" % (e,))


def clear_current_run(job):
    """A finished run must stop claiming to be current -- locally AND to the fleet.

    The fleet release is separate from the local delete and separately guarded: the old version did
    both inside one try/except that swallowed everything, and its second half referenced an undefined
    ARRAY_STATE, so the local delete could succeed while the fleet half raised and vanished. Silence
    on the release path is the expensive direction -- a stale claim pins a healthy card.
    """
    retire_staged_loras(job)
    try:
        if os.path.exists(CURRENT_RUN):
            os.remove(CURRENT_RUN)
            job.log("cleared CURRENT_RUN (run finished)")
    except Exception as e:
        job.log("WARNING: could not clear CURRENT_RUN (%r)" % (e,))
    try:
        # Capabilities ride the RELEASE too: the scheduler most needs to know what a box can do
        # at the moment it becomes free, and a factory that only states them while busy would be
        # unroutable exactly when it is available.
        if not ORCH_BASE:
            _unlinked_note()
            raise RuntimeError("no head address")
        body = json.dumps({"host": FLEET_HOST, "name": job.name, "state": "done",
                           "capabilities": declared_capabilities()}).encode("utf-8")
        rq = urllib.request.Request(FLEET_STATE_URL, data=body,
                                    headers={"Content-Type": "application/json"})
        urllib.request.urlopen(rq, timeout=5).read()
        job.log("released the fleet claim on %s (run finished)" % FLEET_HOST)
    except Exception as e:
        # The rolling TTL is the backstop: an unreleased claim expires on its own within ~2 min.
        job.log("WARNING: could not release the fleet claim (%r) - TTL will expire it" % (e,))


STOP_MAX_EXTRA_STEPS = 6      # the trainer tests STOP_NOW once per step; 1-2 extra is normal
STOP_HARD_CAP = 900           # backstop only, for a failure neither signal describes
STOP_MOVING_S = 300           # a step change this recent means it is still training at the backstop


def _log_tail_has(job, text, nbytes=20000):
    """Whether the trainer's own log tail contains `text`. Unreadable = False (no claim made from it)."""
    try:
        with open(job.log_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - nbytes))
            return text in f.read().decode("utf-8", "replace")
    except Exception:                                                  # noqa: BLE001
        return False


def stop_verdict(honoured, stepped, still_moving):
    """Distinguish between a trainer that is actively ignoring the stop flag (still advancing steps) and one that honored it but is stuck in teardown or saving."""
    if honoured:
        return ("The trainer logged 'STOP_NOW honored' and saved, so the flag WAS read; the process is stuck after "
                "that (exit/teardown), not training.")
    if stepped and still_moving:
        return "It is still advancing steps, so the flag is not being read."
    if stepped:
        return ("It moved within the allowance and then stopped stepping, so it is saving or stuck rather than "
                "training.")
    return "The step count never moved, so it is saving or wedged rather than training."


def pause_trainer(job):
    """Ask the trainer to stop; judge success by step progress, not elapsed time.

        This guard prevents false-positives when STOP_NOW is consumed as a no-op
        at a resume boundary (trainer keeps training). The flag is also ignored
        while checkpoints are being written (step count is static).

        Logic:
          * Steps advance > STOP_MAX_EXTRA_STEPS -> flag ignored -> FATAL.
          * Steps static, process alive          -> saving checkpoint -> wait.

        Elapsed time (hard cap) is only a backstop; log growth is not usable
        as it increases in both cases.
    """
    job.log("dropping STOP_NOW (lossless pause)")
    open(STOP_NOW, "w").close()
    t0 = time.time()
    # Baseline must be the first observable step, never a falsy sentinel.
    # Using `or 0` causes failure on resume boundaries where initial reads return None,
    # disabling the step-stasis detector until the timeout backstop triggers.
    step0 = current_step(job)
    last_seen = step0
    stepped = False
    moved_at = t0
    while trainer_running(job):
        s = current_step(job)
        if s is not None and step0 is None:
            step0, last_seen = s, s
            job.log("post-flag baseline established at step %s (unreadable when the flag dropped "
                    "- the trainer was still booting)" % s)
        elif s is not None and s != last_seen:
            stepped, last_seen, moved_at = True, s, time.time()
            job.log("post-flag step %s (allowance %d from %s) - still stepping, not yet exiting"
                    % (s, STOP_MAX_EXTRA_STEPS, step0))
        if step0 is not None and last_seen > step0 + STOP_MAX_EXTRA_STEPS:
            raise TrainerUnstoppable(
                "STOP_NOW was IGNORED: trainer advanced from step %s to %s (allowance %d) after the "
                "flag. That is the resume-boundary no-op this guard exists for -- a human should look, "
                "because a longer wait cannot fix a flag the trainer is not reading."
                % (step0, last_seen, STOP_MAX_EXTRA_STEPS))
        if time.time() - t0 > STOP_HARD_CAP:
            # Report what was OBSERVED. The old text asserted "static" as a hardcoded narrative
            # because it was written assuming the branch above had already caught the stepping
            # case; when that branch was disabled, the backstop confidently described the opposite
            # of what the log showed. A guard may not narrate a state it did not measure.
            raise TrainerUnstoppable(
                "trainer still alive %ds after STOP_NOW. Observed: baseline step %s, last step %s, "
                "advanced=%s, step last moved %ds ago. %s"
                % (STOP_HARD_CAP, step0, last_seen, stepped, time.time() - moved_at,
                   stop_verdict(honoured=_log_tail_has(job, "STOP_NOW honored"), stepped=stepped,
                                still_moving=time.time() - moved_at < STOP_MOVING_S)))
        time.sleep(5)
    job.log("trainer exited cleanly (%.0fs, %d extra step(s) after the flag)"
            % (time.time() - t0, max(0, (last_seen or 0) - (step0 or 0))))


VIRGIN_TFLOPS = 55.0
BURST = P.RESCUE_BURST


def wait_card_healthy(job, attempts=6):
    """Launch only if card is fast, not just quiet.

    wait_card_quiet ensures no other process holds the card, but does not guarantee performance.
    A trainer spawning into a "poisoned" (degraded) context stays degraded for its lifetime.

    Health check benchmarks:
        - Virgin: ~76 TFLOP/s
        - Dud: 7-12 TFLOP/s (indicates contention/poisoning)

    Retries up to `attempts` times. If threshold not met, launches anyway with warning;
    slow execution is preferred over blocking indefinitely."""
    best = -1.0
    for i in range(1, attempts + 1):
        try:
            r = subprocess.run([PY, BURST, "6144", "25"], cwd=os.path.dirname(BURST),
                               capture_output=True, text=True, timeout=180)
            out = (r.stdout or "") + (r.stderr or "")
            m = re.search(r"([\d.]+)\s*TFLOP/s", out)
            tf = float(m.group(1)) if m else -1.0
        except Exception as e:
            job.log("card_healthy: probe %d failed (%r)" % (i, e))
            return
        best = max(best, tf)
        job.log("card_healthy: probe %d -> %.1f TFLOP/s (virgin ~%.0f)" % (i, tf, VIRGIN_TFLOPS))
        if tf >= VIRGIN_TFLOPS:
            job.log("card_healthy: card is CLEAN — safe to launch")
            return
        time.sleep(4)
    job.log("WARNING: card never measured clean (best %.1f of ~%.0f) after %d probes — launching "
            "anyway; the trainer may be born degraded and the daemon will have to rescue it."
            % (best, VIRGIN_TFLOPS, attempts))
    notify(job, "card_healthy gate: best %.1f TFLOP/s vs ~%.0f virgin before launch — starting "
                "anyway, but this run may need a rescue." % (best, VIRGIN_TFLOPS))


class PauseRequested(Exception):
    """Raised when an external pause request is received after the current unit of work.

    Deliberately NOT a subclass of LaneBusy or RuntimeError: handlers must distinguish
    operator-initiated pauses from resource contention or defects."""


class LaneYield(Exception):
    """Raised when the RUNNING ORDER yields the lane to waiting priority orders.

    Treated like a pause: releases the lane, keeps the order queued for lossless resume,
    but does NOT consume the operator flag or exit the queue loop. The priority sort
    runs waiting orders, and FIFO restores this order immediately. If no priority
    orders are waiting at the milestone, no yield occurs."""


class LaneBusy(RuntimeError):
    """Raised when a training lane is currently occupied.

    This is a transient condition. The order itself is valid and should remain
    in the queue to be retried, rather than being archived as failed."""


COMPUTE_BEAT_S = 15.0          # a compute order's heartbeat / stop-slip poll
CARD_CHECK_EVERY_BEATS = 4     # ~60 s: the box's reporter pushes its card verdict once a minute


class HandedBack(RuntimeError):
    """Raised when this box's card cannot run the order (e.g., capability mismatch or hung engine).
    This is a re-routing signal, not a failure or a wait: the desk re-places the order elsewhere.
    The local spec is already retired as handed-back before this is raised."""


def desk_availability(order_spec=None):
    """The desk's verdict on THIS box's card (GET /fleet/availability), or None when it could not be
    asked. One derivation: the desk's gpu_health and Plex gate, fed by this box's own reporter."""
    token = _worker_token()
    if not token or not ORCH_BASE:
        return None
    try:
        if order_spec is None:
            req = urllib.request.Request(ORCH_BASE + "/fleet/availability?host=%s" % FLEET_HOST,
                                         headers={"X-P20-Token": token})
        else:
            body = {"host": FLEET_HOST, "spec": order_spec}
            if order_uses_gpu(order_spec):
                body["gpu_memory"] = start_memory_snapshot()
            req = urllib.request.Request(ORCH_BASE + "/fleet/availability",
                                         data=json.dumps(body).encode("utf-8"),
                                         headers={"X-P20-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        return d if d.get("ok") else None
    except Exception:
        return None


def start_memory_snapshot():
    """The same allocation reader used by the reporter, sampled again at the last check BEFORE a load."""
    try:
        import gpu_spill
        return gpu_spill.snapshot()
    except Exception:
        return None


def coexists_with_plex(spec):
    """True if the spec requests coexisting with Plex. In this mode, the Plex activity rule is lifted, but the GPU card availability rule and the payload residency check still apply."""
    return bool((spec or {}).get("coexists_with_plex"))


def host_unavailable_reason(avail, running=False, gpu=True, plex_ok=False):
    """Why this box must not run an order now, or "". Could-not-tell (None) is never a reason to act:
    handing back needs a positive signal, and the payload's residency check stays the backstop.
    Plex TRANSCODING (the desk sends `plex_active` for Softserve only) stops every order, CPU included,
    because the measured harm is CPU contention. The card rules apply to GPU orders only: before launch
    the full placement gate (`start_ok`); mid-run only a lost card, because the utilisation gate would
    read our own order's load as someone else's."""
    if not avail:
        return ""
    if gpu and avail.get("gpu_ok") is False:
        return avail.get("gpu_why") or "GPU lost"
    if gpu and not running and (avail.get("model_fit") or {}).get("ok") is False:
        return avail["model_fit"]["why"]
    if plex_ok:
        return ""            # a Plex-coexistence measurement: the start gate's Plex/utilisation verdict is its subject
    if avail.get("plex_active") is True:
        return "Plex needs Softserve: %s" % (avail.get("plex_why") or "transcoding")
    if running or not gpu:
        return ""
    if avail.get("start_ok") is False:
        return avail.get("start_why") or "card unavailable"
    return ""


def order_uses_gpu(spec):
    mode = str(spec.get("mode") or "").strip().lower()
    if mode == "cpu":
        return False
    if mode == "compute":
        return str(spec.get("load") or "mixed").strip().lower() != "cpu"
    return True          # caption, probe, render and train all put a model on the card


def hand_back_to_desk(spec, why, log):
    """POST /p20/jobs/<fleet_order_id>/handback. Returns the desk's answer, or None when the desk could not
    take it (unreachable, refused, or not a desk order) -- the caller then keeps the order and WAITs."""
    jid = spec.get("fleet_order_id")
    token = _worker_token()
    if not jid or not token:
        return None
    from urllib.parse import urlencode
    try:
        req = urllib.request.Request(
            ORCH_BASE + "/p20/jobs/%s/handback?%s" % (jid, urlencode({"host": FLEET_HOST, "why": why[:300]})),
            method="POST", headers={"X-P20-Token": token})
        with urllib.request.urlopen(req, timeout=15) as r:
            ans = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception as e:
        log("hand-back of %s failed (%s) - order stays queued" % (spec.get("name"), type(e).__name__))
        return None
    if not ans.get("ok"):
        log("desk refused the hand-back of %s: %s" % (spec.get("name"), ans.get("error")))
        return None
    return ans


def pinned_here(spec):
    """A pinned order is bound to this specific host and must not be re-routed."""
    return FLEET_HOST in [str(h).strip().lower() for h in (spec.get("pinned_to") or [])]


LOCAL_SMI_TIMEOUT_S = 25        # the health daemon's nvidia-smi bound (p20_health_daemon.smi)

# gpu_clock spec: "unlocked" (card boost), an int MHz lock, or a floor/ceiling band. The factory applies it at launch and
# restores the box's own standing policy on order end (any exit path). The policy is three settings read by
# clock_policy.py (FOUNDRY_TARGET_CLOCK, FOUNDRY_CLOCK_FLOOR, FOUNDRY_CLOCK_CEILING); none set = the card's own boost.
import clock_policy as CP  # noqa: E402
BOX_CLOCK_POLICY = CP.box_policy()
BOX_CLOCK_LOCK_MHZ = BOX_CLOCK_POLICY if isinstance(BOX_CLOCK_POLICY, int) else 0


def _smi_clock(args, log):
    """nvidia-smi clock command; (ok, text). Never raises: no nvidia-smi (the AMD box) is could-not-apply, logged."""
    try:
        p = subprocess.run(["nvidia-smi"] + args, capture_output=True, text=True, timeout=LOCAL_SMI_TIMEOUT_S)
        text = " ".join(((p.stdout or "") + (p.stderr or "")).split())[:160]
        return p.returncode == 0, text
    except Exception as e:                                             # noqa: BLE001
        log("gpu clock: nvidia-smi %s not run (%r)" % (" ".join(args), e))
        return False, repr(e)[:160]


def _card_clock_range(log):
    """(lowest supported graphics clock, the card's max SM clock) in MHz, or None when it cannot say."""
    return CP.card_clock_range(lambda args: _smi_clock(list(args), log))


def _clock_args(want, log):
    """nvidia-smi args for an order's `gpu_clock` (clock_policy.clock_args: "unlocked", a lock, a floor, a ceiling or a
    band), or None when it cannot be expressed (logged)."""
    return CP.clock_args(want, log, lambda: _card_clock_range(log))


def apply_order_clock(spec, log):
    """Set the clock an order declares. Returns the declared value when one was applied (the restore then owes a
    re-lock), else None."""
    want = spec.get("gpu_clock")
    if want in (None, ""):
        return None
    try:
        args = _clock_args(want, log)
    except (TypeError, ValueError):
        log("gpu clock: order declares %r, which is not a clock; not applied" % (want,))
        return None
    if args is None:
        return None
    ok, text = _smi_clock(args, log)
    log("gpu clock: order declares %s -> nvidia-smi %s: %s (%s)" % (want, " ".join(args), "ok" if ok else "FAILED", text))
    return want if ok else None


def box_clock_policy():
    """This box's standing clock policy: a lock (int), a band (dict) or None. The settings when this process reads them,
    else the lock the health daemon itself PUBLISHES (`clock_lock_mhz` in its status file)."""
    if BOX_CLOCK_LOCK_MHZ:
        return BOX_CLOCK_LOCK_MHZ
    if BOX_CLOCK_POLICY is not None:
        return BOX_CLOCK_POLICY
    return box_clock_lock() or None


def box_clock_lock():
    """This box's standing clock LOCK in MHz, or 0: the settings if this process has them, else what the health daemon
    itself PUBLISHES (`clock_lock_mhz` in its status file) - the daemon's own statement is the one derivation."""
    if BOX_CLOCK_LOCK_MHZ:
        return BOX_CLOCK_LOCK_MHZ
    try:
        import p20_status
        with open(os.path.join(p20_status.STATUS_DIR, "health_daemon_%s.json" % FLEET_HOST), encoding="utf-8") as f:
            return int(json.load(f).get("clock_lock_mhz") or 0)
    except Exception:                                                  # noqa: BLE001
        return 0


def restore_box_clock(applied, log):
    """Restore box clock standing state after an order modified it: lock if a standing lock exists, otherwise reset to boost. Never raises."""
    if applied is None:
        return
    policy = box_clock_policy()
    args = (_clock_args(policy, log) if policy is not None else None) or ["-rgc"]
    ok, text = _smi_clock(args, log)
    log("gpu clock: order's %s ends -> box state %s %s (%s)" % (
        applied, CP.label(policy) if args != ["-rgc"] else "unlocked", "restored" if ok else "NOT restored", text))


def local_card_lost():
    """This box's OWN nvidia-smi verdict on its card: a reason, or "" (healthy, or could not tell - no nvidia-smi, as
    on the AMD box). The desk's verdict arrives by a once-a-minute push, so for up to a minute after a card dies it
    still reads healthy; the local driver answers at once. A hung nvidia-smi is the wedged-card signature."""
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, errors="replace", timeout=LOCAL_SMI_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "nvidia-smi timed out (wedged card signature)"
    except Exception:                                                # noqa: BLE001
        return ""
    out = (p.stdout or "") + (p.stderr or "")
    if "GPU is lost" in out or "No devices were found" in out:
        return "GPU lost per this box's nvidia-smi: " + " ".join(out.split())[:160]
    return ""


def card_check_before_launch(job):
    """An order never starts on a box it cannot use (a lost card for GPU orders; Plex transcoding on Softserve for
    every order). A desk order goes back to the desk (HandedBack); a PINNED order, a local order, or one the desk
    could not take WAITs in the queue (LaneBusy). A train order also WAITs: its checkpoints live on this box, so
    re-placing it would restart it. A GPU order also asks this box's own driver (local_card_lost), which knows
    about a lost card before the desk does."""
    gpu = order_uses_gpu(job.spec)
    avail = desk_availability(job.spec)
    if gpu and avail is None:
        raise LaneBusy("card/model availability could not be asked - waiting without loading")
    why = host_unavailable_reason(avail, gpu=gpu, plex_ok=coexists_with_plex(job.spec)) or \
        (local_card_lost() if gpu else "")
    if not why:
        return
    if pinned_here(job.spec):
        raise LaneBusy("card unavailable (%s) - pinned to this box, waiting for it" % why)
    if str(job.spec.get("mode") or "").lower() != "train":
        ans = hand_back_to_desk(job.spec, why, job.log)
        if ans:
            job.log("=== HANDED BACK before launch (%s): desk -> %s ===" % (why, ans.get("outcome")))
            retire_order(job.spec_path)
            raise HandedBack("%s (%s -> %s %s)" % (why, job.name, ans.get("outcome"),
                                                   ans.get("host") or ""))
    raise LaneBusy("card unavailable: %s" % why)


class TrainerUnstoppable(RuntimeError):
    """A trainer will not stop. This is a property of the PROCESS, never the order.

    If a running trainer cannot be stopped (e.g., it ignores pause flags), archiving the associated
    order creates an orphan process that retains hardware locks while the system thinks the lane is free.
    To prevent launching conflicting trainers on the same resource, treat this as a fatal invariant:
    halt further queue processing and require manual intervention. The order remains QUEUED; do not
    advance to the next job."""


def python_cmdlines():
    """[(pid, cmdline)] of every live python.exe via CIM (WMIC is unavailable). Raises on failure: never treat enumeration errors as an empty result."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "ForEach-Object { [string]$_.ProcessId + [char]9 + [string]$_.CommandLine }")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        raise RuntimeError("process enumeration failed (rc=%s): %s" % (r.returncode, (r.stderr or "").strip()[:200]))
    rows = []
    for ln in (r.stdout or "").splitlines():
        pid, _sep, cmd = ln.partition("\t")
        if pid.strip():
            rows.append((pid.strip(), cmd.strip()))
    return rows


def any_trainer_running():
    """(pid, cmdline) of ANY live ai-toolkit trainer, or (None, "") -- ownerless ones included.

    trainer_running(job) answers "is MY trainer alive"; this answers "is the card held by a trainer
    at all", which is the question every launch actually needs and none of the guards asked. Raises
    on failure to enumerate, because a launch guard may never read "I could not tell" as "free"."""
    for pid, cmd in python_cmdlines():
        if "run.py" in cmd and ".yaml" in cmd:
            return pid, cmd
    return None, ""


CLAIM_STALE_AFTER = 1800      # a CURRENT_RUN claim older than this makes its reader ASK THE OS instead of trusting it


def claim_age_s(cur):
    """Seconds since a CURRENT_RUN claim was last written. Unparseable = 0.0: fresh, i.e. still owned.
    The launch guard and the cancel's card check both read the claim's age through this - one derivation."""
    try:
        return (datetime.datetime.now()
                - datetime.datetime.fromisoformat(cur.get("updated"))).total_seconds()
    except Exception:                                                  # noqa: BLE001
        return 0.0


def foreign_run_guard(job, stale_after=CLAIM_STALE_AFTER):
    """Refuse to start a trainer if CURRENT_RUN claims ownership by a different job.

        Safety constraint: Fail-closed on ambiguity. An unreadable or missing CURRENT_RUN
    does NOT imply the card is free; it triggers refusal to prevent double-training collisions.

        Verification logic: Absence of a stated claim is not proof of availability. The guard
        must explicitly confirm no trainer process is running via the OS before considering
        the lane free, as state files can be stale or cleared by cleanup routines while jobs live.
        """
    def _no_stated_claim():
        """Nothing CLAIMS the card. Confirm with the OS before calling that free."""
        try:
            pid, cmd = any_trainer_running()
        except Exception as e:
            raise LaneBusy("cannot enumerate trainers (%r) - refusing to launch %s. Cannot-tell is "
                           "never free." % (e, job.name))
        if pid and job.name not in cmd and trainer_name(job) not in cmd:
            raise LaneBusy(
                "REFUSING to launch %s: no order claims the card, but trainer PID %s is alive and "
                "running %s. An ownerless trainer still holds the GPU -- stop it losslessly "
                "(drop %s, wait for the checkpoint) before anything else launches."
                % (job.name, pid, cmd[:120], STOP_NOW))
        return
    try:
        with open(CURRENT_RUN, "r", encoding="utf-8") as f:
            cur = json.load(f)
    except FileNotFoundError:
        return _no_stated_claim()
    except Exception as e:
        raise RuntimeError("CURRENT_RUN is unreadable (%r) - refusing to launch. Cannot-tell is not "
                           "free; fix or remove %s." % (e, CURRENT_RUN))
    name = cur.get("name") or ""
    if not name:
        return _no_stated_claim()
    if name == job.name:
        return                         # our own claim; a resume re-enters its own lane
    if (cur.get("state") or "").lower() not in ("training", "rendering", "warming"):
        return _no_stated_claim()      # e.g. state=done -- stated free, but ASK the card
    age = claim_age_s(cur)
    if age > stale_after:
        job.log("CURRENT_RUN names %r but is %.0fs stale (> %ds) - checking the card itself"
                % (name, age, stale_after))
        return _no_stated_claim()      # a dead REPORTER is not a dead trainer; ask the OS
    raise LaneBusy(
        "REFUSING to launch %s: CURRENT_RUN states %r is %s at step %s (%.0fs ago). Two trainers on "
        "one card is the 2026-07-27 orphan-bench collision. If that run is genuinely dead, clear %s."
        % (job.name, name, cur.get("state"), cur.get("step"), age, CURRENT_RUN))


def launch_trainer(job):
    foreign_run_guard(job)
    # Remove stale STOP_NOW: pause_trainer consumes it on exit, so a leftover flag
    # would kill the new trainer immediately or cause silent no-ops at resume.
    if os.path.exists(STOP_NOW):
        os.remove(STOP_NOW)
        job.log("removed STALE STOP_NOW before launch (would have no-op'd or killed the fresh trainer)")
    # Self-heal: remove stale RESCUE_PAUSE flag if present.
    # The daemon writes it alongside STOP_NOW, but foundry only consumes it on trainer exit.
    # If a run ends cleanly, the flag outlives the session and persists to the next launch,
    # causing two failures:
    #   1. Foundry misinterprets the first preview pause as a daemon-requested recycle
    #      (false "poisoned trainer context"), triggering a relaunch instead of render.
    #   2. Daemon guards all recycle requests with `not exists(RESCUE_PAUSE)`, so a stale
    #      flag silently disables its strongest escalation for the entire run.
    if os.path.exists(RESCUE_PAUSE):
        os.remove(RESCUE_PAUSE)
        job.log("removed STALE RESCUE_PAUSE before launch (would have faked a daemon recycle at the "
                "first pause AND blocked the daemon from requesting a real one)")
    # Wait for GPU idle before launching trainer to avoid HIP context collision
    # with stopped ComfyUI. Uses card_quiet handshake (2x consecutive quiet reads).
    wait_card_quiet(job)
    wait_card_healthy(job)
    launch_detached(job, "P20 Foundry Trainer", job.launcher)
    t0 = time.time()
    # Process is visible <10s after schtasks /run (measured); 60s = 6x margin. (Stepping takes ~4 min
    # more for latent-cache reload — that's TRAIN_WATCH's business, not launch's.)
    while not trainer_running(job):
        if time.time() - t0 > 60:
            raise RuntimeError("trainer process not visible 60s after launch (measured norm <10s)")
        time.sleep(5)
    job.log("trainer running")


def rescue_count_since(started_iso):
    """Count daemon rescues since the run started, for the end-of-run report."""
    try:
        d0 = datetime.datetime.fromisoformat(started_iso)
        n = 0
        with open(RESCUE_LOG, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                if "CONFIRMED" in ln:
                    try:
                        if datetime.datetime.strptime(ln[:19], "%Y-%m-%d %H:%M:%S") >= d0:
                            n += 1
                    except ValueError:
                        pass
        return n
    except Exception:
        return -1


# Compute orders run serially in the single runner to prevent collision with GPU renders.
# No concurrency guard is needed because they never execute simultaneously on one box.
# Future parallel CPU lanes are gated by measured load classification (`load` field).
COMPUTE_PATH_TOKENS = {
    "{FOUNDRY_ROOT}": P.FOUNDRY_ROOT,
    "{OUTPUT}": P.OUTPUT,
    "{MODEL_CACHE}": P.MODEL_CACHE_DIR,
}


def _expand_compute_path(s, order_out=None):
    """{ORDER_OUT} = this order's own output folder (order_out_dir); the other tokens are per box."""
    if order_out:
        s = s.replace("{ORDER_OUT}", order_out)
    for tok, real in COMPUTE_PATH_TOKENS.items():
        s = s.replace(tok, real)
    return s


COMPUTE_OUT_ROOT = os.path.join(P.OUTPUT, "compute")


def order_out_dir(job):
    """One folder per order: order names are unique, so no two orders write the same place."""
    return os.path.join(COMPUTE_OUT_ROOT, job.name)


def win_long(p):
    """The `\\\\?\\` form of an absolute Windows path, so a copy or walk passes MAX_PATH (260) whatever the box's
    LongPathsEnabled says: BigHonker's is 0, and a 146-character order name shipped into the hard-bench share is a
    323-character path."""
    if os.name != "nt" or p.startswith("\\\\?\\"):
        return p
    p = os.path.abspath(p)
    return "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p


UNSHIPPED = ".unshipped.json"     # beside kept output: {name, deliver_to, error, at}; the runner retries it


def _ship_dir(src, dest_root, name):
    """Copy an order's output folder to `dest_root\\<name>` (timestamp-suffixed if that exists; never overwrites).
    Returns (dest, file count). The unshipped marker never travels."""
    dest = os.path.join(dest_root, name)
    if os.path.exists(win_long(dest)):
        dest = "%s.%s" % (dest, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copytree(win_long(src), win_long(dest), ignore=shutil.ignore_patterns(UNSHIPPED))
    return dest, sum(len(files) for _, _, files in os.walk(win_long(dest)))


def ship_compute(job):
    """Copy the order output to `deliver_to\\<order name>`.

    Never overwrites (existing destinations get a timestamp suffix) and never raises.
    On failure: DM the user, leave output in place with an `.unshipped.json` marker for `reship_kept` retry, and set `job.state["ship_error"]`.
    Caller must not report as done if `ship_error` is set."""
    job.state.pop("ship_error", None)
    dest_root = _expand_compute_path((job.spec.get("deliver_to") or "").strip())
    if not dest_root:
        return None
    src = order_out_dir(job)
    try:
        if not os.path.isdir(src) or not os.listdir(src):
            job.log("ship: %s is empty - nothing to deliver" % src)
            return None
        dest, n = _ship_dir(src, dest_root, job.name)
        _quiet_remove(os.path.join(src, UNSHIPPED))
        job.log("shipped %d file(s) -> %s" % (n, dest))
        job.state["delivered"] = n            # counted by _order_shipped_count (SHIPPED_COUNT_KEYS)
        try:
            job.save()
        except Exception:                                              # noqa: BLE001
            pass
        return dest
    except Exception as e:                                             # noqa: BLE001
        job.state["ship_error"] = repr(e)[:200]
        job.log("ship FAILED (%r) - output kept at %s; marked for reship" % (e, src))
        try:
            with open(os.path.join(src, UNSHIPPED), "w", encoding="utf-8") as f:
                json.dump({"name": job.name, "deliver_to": dest_root, "error": repr(e)[:200],
                           "at": datetime.datetime.now().isoformat(timespec="seconds")}, f, indent=1)
        except Exception as e2:                                        # noqa: BLE001
            job.log("reship marker could not be written (%r)" % (e2,))
        try:
            notify(job, "COMPUTE ORDER SHIP FAILED — %s: %r. Output kept at %s; the runner reships it once the "
                        "share answers" % (job.name, e, src))
        except Exception:                                              # noqa: BLE001
            pass
        return None


def reship_kept(log_line, isdir=None):
    """CLEANUP the order taker owes between orders: deliver every kept output whose ship failed, once its share
    answers (`unreachable_shares` is the one derivation for "down"). A share still down = WAIT, marker kept, no
    log spam; a copy that fails is kept for the next pass. Returns the names delivered."""
    done = []
    for mp in sorted(glob.glob(os.path.join(COMPUTE_OUT_ROOT, "*", UNSHIPPED))):
        src = os.path.dirname(mp)
        try:
            with open(mp, encoding="utf-8") as f:
                m = json.load(f)
        except Exception as e:                                         # noqa: BLE001
            log_line("reship: marker %s unreadable (%r) - left for a human" % (mp, e))
            continue
        name = str(m.get("name") or os.path.basename(src))
        dest_root = str(m.get("deliver_to") or "")
        if not dest_root or unreachable_shares([dest_root], isdir=isdir):
            continue                                                   # the share is still down: wait
        try:
            dest, n = _ship_dir(src, dest_root, name)
        except Exception as e:                                         # noqa: BLE001
            log_line("reship %s FAILED (%r) - kept, retried next pass" % (name, e))
            continue
        _quiet_remove(mp)
        msg = "RESHIPPED %s: %d file(s) -> %s (kept since %s after: %s)" % (name, n, dest, m.get("at"), m.get("error"))
        log_line(msg)
        try:
            notify_desk("COMPUTE ORDER DELIVERED LATE — " + msg)
        except Exception:                                              # noqa: BLE001
            pass
        done.append(name)
    return done


def report_fleet_result(job, ok, why="", status=None):
    """Report completion to the Fleet Desk so chained `after_ids` successors release.

    Called at job completion (not collection) to ensure truthful state propagation:
    success releases successors; failure holds them (fail-closed).
    `status` names the outcome explicitly ("done" | "failed" | "cancelled"); left None it is derived
    from `ok` exactly as before. "cancelled" is an operator's decision, not a defect, and holds
    successors the same way "failed" does (the desk treats both as dead).
    No-op if the job was not collected from the desk.
    Fail-soft: desk outages must never block the retire path, but are logged.
    """
    jid = str(job.spec.get("fleet_order_id") or "").strip()
    if not jid:
        return
    token = _worker_token()
    if not token or not ORCH_BASE:
        job.log("WARNING: fleet step %s finished but no worker token - successors will HOLD" % jid)
        return
    outcome = str(status or ("done" if ok else "failed")).strip().lower()
    try:
        _post_desk_result(jid, outcome, why, token)
        job.log("fleet desk: reported %s for step %s" % (outcome if outcome in ("done", "cancelled") else "FAILED", jid))
        told = True
    except Exception as e:
        # the cancel path reports through a stand-in job with no .name (_desk_report_cancel): never raise here
        kept = _keep_desk_report(jid, outcome, why, getattr(job, "name", None) or job.spec.get("name"), e)
        job.log("WARNING: fleet completion report failed (%r) - %s" % (
            e, "kept; the order taker resends it once the desk answers" if kept
            else "NOT kept - the desk record stays open and chain successors will HOLD"))
        told = kept
    if told:
        try:
            job.desk_reported = True        # the order taker's end-of-order report skips an order that told the desk itself
        except Exception:                   # noqa: BLE001
            pass


# Pending desk completion reports (one <step id>.json each). The order taker resends these between orders to recover from transient failures (e.g., connection refused during deploys) that otherwise leave the desk in a stale state.
DESK_REPORTS = os.path.join(P.STATE_DIR, "desk_reports")


def report_stopped(job, stopped):
    """Skip the failure report if every stop slip is a PAUSE (e.g., a deploy that requeues the order). A pause is not a finish: the requeued run's end will report the result, so a caller waiting on the order's status does not erroneously read "failed" while the order is requeued."""
    if stopped and all(s.get("pause") for s in stopped):
        job.log("=== paused by %s: the desk hears this order's verdict when the requeued run finishes ===" % (
            stopped[0].get("why") or "a deploy"))
        try:
            job.desk_reported = True
        except Exception:                   # noqa: BLE001
            pass
        return False
    report_fleet_result(job, False, "stopped by operator slip")
    return True


def _post_desk_result(jid, outcome, why, token):
    """POST one completion report to the Fleet Desk. Raises on any failure (the caller decides what a failure means)."""
    from urllib.parse import quote
    url = "%s/p20/jobs/%s/result?status=%s" % (ORCH_BASE, jid, outcome)
    if outcome != "done":
        url += "&error=" + quote((why or "see the factory log")[:200])
    req = urllib.request.Request(url, method="POST", headers={"X-P20-Token": token})
    with urllib.request.urlopen(req, timeout=10):
        pass


def _keep_desk_report(jid, outcome, why, name, err):
    """Keep an undelivered completion report for resend_desk_reports. True when kept. Never raises."""
    try:
        os.makedirs(DESK_REPORTS, exist_ok=True)
        path = os.path.join(DESK_REPORTS, "%s.json" % jid)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "outcome": outcome, "why": why or "", "name": name, "error": repr(err)[:200],
                       "at": datetime.datetime.now().isoformat(timespec="seconds")}, f, indent=1)
        os.replace(path + ".tmp", path)
        return True
    except Exception:                                                  # noqa: BLE001
        return False


def resend_desk_reports(log_line):
    """CLEANUP the order taker owes between orders, beside reship_kept: deliver every kept completion report once the
    desk answers. Desk unreachable = WAIT: the pass stops at the first connection failure, markers kept, nothing
    logged. The desk ANSWERING with a refusal (HTTP error) is about that one report, so its marker is set aside as
    `.refused` for a human and the others still go. Returns the step ids reported."""
    import urllib.error
    done = []
    paths = sorted(glob.glob(os.path.join(DESK_REPORTS, "*.json")))
    if not paths:
        return done
    token = _worker_token()
    if not token:
        return done
    for mp in paths:
        try:
            with open(mp, encoding="utf-8") as f:
                m = json.load(f)
            jid, outcome = str(m["jid"]), str(m["outcome"])
        except Exception as e:                                         # noqa: BLE001
            log_line("desk report marker %s unreadable (%r) - left for a human" % (mp, e))
            continue
        try:
            _post_desk_result(jid, outcome, m.get("why") or "", token)
        except urllib.error.HTTPError as e:
            try:
                os.replace(mp, mp + ".refused")
            except OSError:
                pass
            log_line("desk REFUSED the kept %s report for step %s (%s): HTTP %s - set aside as .refused for a human"
                     % (outcome, jid, m.get("name"), e.code))
            continue
        except Exception:                                              # noqa: BLE001
            break                                                      # the desk is still down: wait, quietly
        _quiet_remove(mp)
        log_line("RESENT desk report: %s for step %s (%s; the first try failed at %s: %s)"
                 % (outcome, jid, m.get("name"), m.get("at"), m.get("error")))
        done.append(jid)
    return done


def _desk_report_cancel(spec, name, why, log):
    """Close a desk order that was cancelled on this box. Never raises (it runs inside the control path).
    `log` is the caller's logger: there is no module-level log_line."""
    try:
        shim = types.SimpleNamespace(spec=spec or {}, log=log)
        report_fleet_result(shim, False, ("cancelled on %s by operator slip: %s" % (FLEET_HOST, why or name))[:200],
                            status="cancelled")
    except Exception as e:                                             # noqa: BLE001
        try:
            log("WARNING: desk not told of the cancel of %s (%r)" % (name, e))
        except Exception:                                              # noqa: BLE001
            pass


# A compute payload reports progress the same way the caption pass does: the worker prints its
# own count. Lines starting `[tag] N/M ` are that report; the runner never re-derives it.
COMPUTE_PROG_RE = re.compile(r"^\[[^\]]+\]\s+(\d+)\s*/\s*(\d+)(?=\s)")
# `[beat] N`: a payload still producing inside one long unit (infer_ollama prints it while tokens stream)
COMPUTE_BEAT_RE = re.compile(r"^\[beat\]\s+(\d+)\s*$")
# a progress line may end with that unit's decode rate (`... 93.4 tok/s`); the tile shows the median of the last ten
COMPUTE_TOKS_RE = re.compile(r"\s([\d.]+) tok/s\b")
TOKS_WINDOW = 10
# a graded payload may carry its running score (`... solved 3/5 ...`); the tile shows the latest one
COMPUTE_SOLVED_RE = re.compile(r"\bsolved\s+(\d+)\s*/\s*(\d+)\b")
# rows that measured the instrument, not the model (a1_validity); the tile shows them beside the score
COMPUTE_VOID_RE = re.compile(r"\bvoid\s+(\d+)\b")
# a payload's own alarm (`ALERT: <text>`), DM'd once per distinct text per order - Tolkien speaks the same line
COMPUTE_ALERT_RE = re.compile(r"^ALERT:\s*(.+)$")


def solved_phase(phase, solved):
    """Format as `phase · solved X of Y`; never `X/Y` because the deck parses the first `N / M` in a phase as order progress (e.g., `2/4` displaces the real `4/10`)."""
    return "%s · solved %d of %d" % ((phase,) + tuple(solved)) if solved else phase


# Parse eval rate from engine logs (Ollama serve.log or ORDER_OUT) rather than deriving it from order metadata;
# short answers (< ENGINE_MIN_TOKENS) are dominated by overhead, so raw rate is misleading.
# Format: `eval time = X ms / N tokens ... T tokens per second` (see Lab\bin\live_rate.py) for the canonical parser.
ENGINE_TIMING_RE = re.compile(r"(?<!prompt )eval time =\s*[\d.]+ ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
ENGINE_MIN_TOKENS = 20
# the model server's log: OLLAMA_SERVE_LOG (a setting: a server run under a user account logs in that profile) and
# the lab launcher's own log
OLLAMA_SERVE_LOGS = tuple(p for p in (P.setting("OLLAMA_SERVE_LOG", ""), os.path.join(P.HOLD_DIR, "ollama-serve.log")) if p)
ENGINE_READ_MAX = 2 << 20    # Distinguish generations from embeddings: only tasks with a decode line are counted as calls.
# Embeddings start and end with no timing data.
ENGINE_EVENT_RE = re.compile(
    r"task (\d+) \| (?:(processing task)"
    r"|new prompt, n_ctx_slot = (\d+)"
    r"|prompt eval time =\s*[\d.]+ ms /\s*(\d+) tokens.*?([\d.]+) tokens per second"
    r"|\s*(eval time) ="
    r"|stop processing: n_tokens = (\d+))")


def median(xs):
    """The middle value of xs, or None when empty."""
    s = sorted(xs or [])
    if not s:
        return None
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


class EngineRates:
    """What the serving engine logged since this order started: the box's ollama serve log(s) from their size at the
    start, plus every *.log under the order's own output dir from its first byte. `poll()` returns the decode rates;
    `view()` is the order's model work for the deck's tile rows (write and read tok/s, the call it is on, context)."""

    def __init__(self, order_out, serve_logs=OLLAMA_SERVE_LOGS):
        self.order_out = order_out
        self.live = None       # an engine that publishes its own LIVE state (ENGINE_LIVE_FILE), polled each beat
        self.serve_logs = [p for p in serve_logs if os.path.exists(p)]
        self.offsets = {p: os.path.getsize(p) for p in self.serve_logs}
        self.rates = []
        self.reads = []
        self.calls = 0
        # ollama runs one runner per loaded model, all logging to one serve log, each numbering its own tasks: a chat
        # task and an embedding can share a number (task 366 twice on Softserve, 10-16). So a key counts its starts,
        # and its context sizes are a stack - the short call nested inside a long one ends first.
        self.open = {}         # (file, task) -> started and not yet ended
        self.cap = {}          # (file, task) -> [context size of each start]
        self.timed = set()     # (file, task) that logged a decode line and has not ended
        self.ctx = None        # (tokens held, context size) of the latest generation to end

    def poll(self):
        files = list(self.serve_logs)
        files += glob.glob(os.path.join(self.order_out, "**", "*.log"), recursive=True)
        for p in files:
            try:
                size = os.path.getsize(p)
                start = self.offsets.get(p, 0)
                if size < start:          # rotated or rewritten: read it from the top
                    start = 0
                if start == 0 or size - ENGINE_READ_MAX > start:
                    self._forget(p)       # a restarted file, or lines about to be skipped: its open tasks are unknown
                start = max(start, size - ENGINE_READ_MAX)
                with open(p, "rb") as f:
                    f.seek(start)
                    chunk = f.read(size - start).decode("utf-8", "replace")
                self.offsets[p] = size
            except OSError:
                continue
            for m in ENGINE_TIMING_RE.finditer(chunk):
                if int(m.group(1)) >= ENGINE_MIN_TOKENS:
                    self.rates.append(float(m.group(2)))
            self._events(p, chunk)
        self.rates = self.rates[-TOKS_WINDOW:]
        self.reads = self.reads[-TOKS_WINDOW:]
        self._poll_live()
        return list(self.rates)

    def _poll_live(self):
        """An order whose engine publishes LIVE state declares it in ENGINE_LIVE_FILE (stated, never probed for):
        {"engine": "strata", "metrics": "<url>", "ctx": N}. One bounded local GET per beat; a failed read leaves
        `live` None (the tile shows what the logs say - never a stale live number)."""
        self.live = None
        try:
            with open(os.path.join(self.order_out, ENGINE_LIVE_FILE), encoding="utf-8") as f:
                decl = json.load(f)
            if decl.get("engine") != "strata":
                return
            with urllib.request.urlopen(decl["metrics"], timeout=ENGINE_LIVE_TIMEOUT_S) as r:
                m = json.loads(r.read().decode("utf-8", "replace"))
            self.live = dict(strata_live(m), ctx_size=decl.get("ctx"))
        except (OSError, ValueError, KeyError, TypeError):
            self.live = None

    def _forget(self, p):
        for d in (self.open, self.cap):
            for k in [k for k in d if k[0] == p]:
                del d[k]
        self.timed.difference_update({k for k in self.timed if k[0] == p})

    def _events(self, p, chunk):
        for m in ENGINE_EVENT_RE.finditer(chunk):
            key = (p, m.group(1))
            if m.group(2):
                self.open[key] = self.open.get(key, 0) + 1
            elif m.group(3):
                self.cap.setdefault(key, []).append(int(m.group(3)))
            elif m.group(4):
                if int(m.group(4)) >= ENGINE_MIN_TOKENS:
                    self.reads.append(float(m.group(5)))
            elif m.group(6):
                self.calls += 1
                self.timed.add(key)
            elif m.group(7):
                if self.open.get(key, 0) > 1:
                    self.open[key] -= 1
                else:
                    self.open.pop(key, None)
                caps = self.cap.get(key) or []
                cap = caps.pop() if caps else None
                if not caps:
                    self.cap.pop(key, None)
                if key in self.timed and cap:
                    self.ctx = (int(m.group(7)), cap)
                self.timed.discard(key)

    def view(self, write=None):
        """The `gen` the deck's tile formats (one formatter with Kirk's own work). No tool time: the engine's lines
        carry no clock, so a time here would be the beat's, not the call's."""
        if self.live:                                          # the engine's own live numbers win (Strata)
            lv = self.live
            return {"write": lv["write"] if lv["write"] is not None else write, "read": lv["read"],
                    "turn": lv["calls"] or None, "turn_label": "call", "tool": lv["doing"],
                    "tool_running": lv["busy"],
                    "ctx": [lv["held"], lv["ctx_size"]] if lv["held"] and lv["ctx_size"] else None}
        running = sum(self.open.values())
        on = self.calls + max(0, running - len(self.timed))   # completed generations + the ones still running
        return {"write": write, "read": median(self.reads), "turn": on or None, "turn_label": "call",
                "tool": ("generating" if running else "between calls") if on else None,
                "tool_running": bool(running), "ctx": list(self.ctx) if self.ctx else None}


ENGINE_LIVE_FILE = "engine_live.json"     # written by an order that runs an engine with a live-state endpoint
ENGINE_LIVE_TIMEOUT_S = 2                 # a local GET on the beat's thread: never longer than a beat can spare


def strata_live(m):
    """Strata's GET /metrics -> the tile's live numbers (its Monitor tab's own fields, docs\\DETAILS.md "What the model
    is doing right now"). READING: the read rate is tokens read so far / seconds so far, live; WRITING: `tok_s`, the
    engine's own 2 s window; between calls: the last finished request's rates. `held` = tokens in the context now."""
    lv = m.get("live") or {}
    last = (m.get("requests") or [None])[0] or {}
    state = lv.get("state") or "idle"
    busy = state not in ("idle", None)
    read = write = None
    if state == "reading" and lv.get("prompt_read") and lv.get("elapsed_s"):
        read = lv["prompt_read"] / lv["elapsed_s"]
    elif last.get("prompt_read") and last.get("prompt_ms"):
        read = last["prompt_read"] / (last["prompt_ms"] / 1000.0)
    if state == "generating" and lv.get("tok_s"):
        write = lv["tok_s"]
    elif last.get("decode_tok_s"):
        write = last["decode_tok_s"]
    held = ((lv.get("prompt_tokens") or 0) + (lv.get("generated") or 0)) if busy else \
        ((last.get("prompt_total") or 0) + (last.get("output_tokens") or 0))
    calls = (m.get("totals") or {}).get("requests")       # finished since this order's server started
    calls = calls + int(busy) if isinstance(calls, int) else None
    doing = {"reading": "reading the prompt", "generating": "generating"}.get(state, "between calls")
    return {"read": round(read, 1) if read else None, "write": round(write, 1) if write else None,
            "held": held or None, "calls": calls, "doing": doing, "busy": busy}


LIVE_TOKS_WINDOW_S = 60      # the live rate is tokens per second over the beats of the last minute
LIVE_TOKS_FRESH_S = 90       # with no beat this recent the unit is between generations: fall back to the unit median
LIVE_TOKS_MAX = 5000.0       # a delta faster than this is not tokens (a clock printed on the beat line): skipped


def live_toks(beats, now):
    """Tokens per second from `[beat] N` lines (cumulative tokens), over the last LIVE_TOKS_WINDOW_S seconds, or None
    when fewer than two beats are that recent or the newest is older than LIVE_TOKS_FRESH_S. An implausibly fast
    delta is skipped, so a beat that carries something other than a token count (the delegator's wait beat prints
    the clock) cannot bend the rate; a count that goes DOWN is a restarted payload and becomes the new baseline."""
    if not beats or now - beats[-1][0] > LIVE_TOKS_FRESH_S:
        return None
    recent = [b for b in beats if now - b[0] <= LIVE_TOKS_WINDOW_S]
    toks = secs = 0.0
    good = recent[0] if recent else None      # pairs form against the last GOOD beat, so one bad beat drops out alone
    for t1, n1 in recent[1:]:
        dt, dn = t1 - good[0], n1 - good[1]
        if dn < 0:                            # the count restarted (a new payload process): a new baseline
            good = (t1, n1)
            continue
        if dt <= 0 or dn / dt > LIVE_TOKS_MAX:
            continue
        toks += dn
        secs += dt
        good = (t1, n1)
    return toks / secs if secs > 0 and toks > 0 else None


def toks_phase(phase, toks, live=None):
    """`phase · N tok/s`: uses the live rate if available; otherwise falls back to the median of recent rates. Returns `phase` unchanged if no rate data exists."""
    if live:
        return "%s · %.1f tok/s" % (phase, live)
    if not toks:
        return phase
    return "%s · %.1f tok/s" % (phase, median(toks))


UNC_ROOT_RE = re.compile(r"^\s*(\\\\[^\\/]+[\\/][^\\/]+)")


def share_roots(*things):
    """Every \\\\host\\share named anywhere in these strings / lists / dicts (an order's payload, args, spec)."""
    roots = set()

    def walk(x):
        if isinstance(x, str):
            m = UNC_ROOT_RE.match(x)
            if m:
                roots.add(m.group(1).replace("/", "\\"))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(list(things))
    return sorted(roots)


def mapped_letters(net_use_text):
    """{host: [drive letters]} from `net use` output: the remembered mappings this session holds per server."""
    out = {}
    for ln in net_use_text.splitlines():
        m = re.match(r"^\s*\S+\s+([A-Za-z]):\s+\\\\([^\\\s]+)\\", ln)
        if m:
            out.setdefault(m.group(2).lower(), []).append(m.group(1).upper())
    return out


def heal_share(root, net_use=None, listdir=None):
    """Touch the mapped drive letter(s) this session remembers for `root`'s server to trigger credential re-binding.
    Direct UNC access often fails with invalid credentials; using the remembered letter reconnects with the
    saved mapping's credentials. Returns the letters touched. Never raises."""
    touched = []
    try:
        host = root.lstrip("\\").split("\\")[0].lower()
        text = net_use() if net_use else subprocess.run(["net", "use"], capture_output=True, text=True,
                                                         timeout=30).stdout
        if not isinstance(text, str):
            text = ""
        for letter in mapped_letters(text).get(host, []):
            try:
                (listdir or os.listdir)(letter + ":\\")
            except Exception:                                          # noqa: BLE001
                pass
            touched.append(letter)
    except Exception:                                                  # noqa: BLE001
        pass
    return touched


def unreachable_shares(things, isdir=None, heal=None):
    """Return the shares from `things` that are currently unreachable. Failure is treated as a property of the share, not the order: if a share is down or rejecting credentials, it should trigger a wait (LaneBusy) rather than a permanent failure. If a root is inaccessible, attempt to heal the session once before marking it as bad."""
    isdir = isdir or os.path.isdir
    heal = heal or heal_share
    bad = []
    for r in share_roots(things):
        try:
            ok = isdir(r)
        except OSError:
            ok = False
        if not ok and heal(r):
            try:
                ok = isdir(r)
            except OSError:
                ok = False
        if not ok:
            bad.append(r)
    return bad


def unit_rate(marks):
    """Median seconds per unit from (time, count) marks; None until a unit has landed."""
    per = sorted((t1 - t0) / (n1 - n0) for (t0, n0), (t1, n1) in zip(marks, marks[1:]) for _ in range(n1 - n0))
    if not per:
        return None
    mid = len(per) // 2
    return per[mid] if len(per) % 2 else (per[mid - 1] + per[mid]) / 2


def yield_live_window(args, log):
    """A stop slip on a HAI chat window (helper_window --live: its items folder is args[0]): ask it to finish its turn, save
    the chat and exit (helper_window.YIELD) instead of being killed. -> True when asked; False = not a live window, or the
    marker could not be written, and the caller ends it the old way."""
    if "--live" not in args:
        return False
    try:
        open(os.path.join(args[0], "YIELD"), "w").close()
        log("live window: asked to finish its turn, save and exit (a second slip ends it now)")
        return True
    except OSError as e:
        log("live window: the yield could not be written (%r), ending it" % (e,))
        return False


def run_compute_order(job):
    """Execute a mode:"compute" order. Spec fields: `payload` (a .py path), `args` (list of
    strings), optional `python_exe` (defaults to the foundry's own interpreter), optional `load`
    ("gpu"|"mixed"|"cpu", default "mixed"). Paths in payload/args/python_exe may use
    {FOUNDRY_ROOT} / {OUTPUT} / {MODEL_CACHE} tokens, resolved on THIS box — orders never carry
    drive letters. The order name is appended to the child's argv so the patrol identifies it as
    this order's own process."""
    order_out = order_out_dir(job)
    os.makedirs(order_out, exist_ok=True)
    payload = _expand_compute_path((job.spec.get("payload") or "").strip(), order_out)
    args = [_expand_compute_path(str(a), order_out) for a in (job.spec.get("args") or [])]
    python_exe = _expand_compute_path((job.spec.get("python_exe") or "").strip(), order_out) or sys.executable
    bad = unreachable_shares([payload] + args + [job.spec])
    if bad:
        job.log("input share(s) %s unreachable from this box -> the order WAITS, untouched" % ", ".join(bad))
        raise LaneBusy("input share unreachable: %s" % ", ".join(bad))
    # ORDER can declare progress via `progress_glob` and `progress_total`.
    # If absent, falls back to conventional .progress markers or phase-only.
    prog_glob = _expand_compute_path((job.spec.get("progress_glob") or "").strip(), order_out)
    prog_total = int(job.spec.get("progress_total") or 0)
    if not prog_glob:
        # PROGRESS BY CONVENTION: a payload that writes one .done marker per unit under
        # <out-stem>*.progress needs no declaration -- derive the pattern from the last
        # .json arg (the out path; the wildcard also matches an items-file share rename).
        # A payload that writes no markers just leaves the glob empty = phase-only.
        for a in args:
            if a.endswith(".json"):
                prog_glob = a[:-5] + "*.progress\\*.done"
    if not prog_total:
        # a dealt order carries its slice size; a conventional payload may also write
        # `_total` into its marker dir (read per-beat below, the dir appears after launch)
        prog_total = int(job.spec.get("items_count") or 0)
    if prog_total:
        job.total_steps = prog_total     # the tile's denominator; the 2000 default was a lie
    tag = "compute(%s)" % job.load
    phase_str = "%s: %s" % (tag, os.path.basename(payload))
    # notify:"failures" -- an order may opt its routine lifecycle DMs (started/complete)
    # down to log-only. Failures, stops and warnings ALWAYS DM: severity never opts out,
    # only chatter does.
    quiet = str(job.spec.get("notify") or "").strip().lower() in ("failures", "quiet")
    if not payload or not os.path.exists(payload):
        job.log("compute order payload missing: %r" % payload)
        notify(job, "COMPUTE ORDER FAILED — payload not found: %r" % payload)
        report_fleet_result(job, False, "payload not found")
        retire_order(job.spec_path)
        return
    # GPU/mixed loads require the demon gate; without it, performance degrades ~8x.
    if job.load in ("gpu", "mixed"):
        require_demon(job)
        # the card must be quiet at compute START, not just cleared at order end -- same
        # gate as the trainer launch, one derivation; stop_comfy first mirrors the cleanup
        # and is a cheap no-op on an already-clear card.
        stop_comfy(job)
        wait_card_quiet(job, ollama_busy=False)
    # Claim the card under this order's name so the patrol exempts the child (name-in-cmdline).
    publish_current_run(job, state="compute", phase=phase_str)
    job.state["started"] = job.state.get("started") or datetime.datetime.now().isoformat()
    job.save()
    # -u: unbuffered child stdout. Without it the pipe block-buffers and a long silent phase
    # (a big torch.load) shows a stale log AND a stale heartbeat (the 08-07 known bug, task #8).
    # PARALLEL SHARE: a fleet-split compute order carries its item share in spec["items"]
    # (compose_orders chunks; the submitter only provides the list). Argv cannot carry
    # hundreds of items on Windows, so the runner materialises them to a file and passes
    # `--items-file` -- the payload decides what an item means. Absent = whole-input.
    share_args = []
    if job.spec.get("items"):
        items_path = os.path.join(os.path.dirname(job.spec_path) or ".",
                                  "%s.items.json" % job.name)
        with open(items_path, "w", encoding="utf-8") as f:
            json.dump(list(job.spec["items"]), f)
        share_args = ["--items-file", items_path]
        job.log("compute share: %d items -> %s" % (len(job.spec["items"]), items_path))
    cmd = [python_exe, "-u", payload] + args + share_args + [job.name]   # job.name in argv -> patrol-exempt
    job.log("compute order spawn: %s" % " ".join(cmd))
    if not quiet:
        notify(job, "COMPUTE ORDER started [%s] — %s %s"
                    % (job.load, os.path.basename(payload), " ".join(args)))
    else:
        job.log("notify policy: failures-only — lifecycle DMs suppressed")
    clock_applied = apply_order_clock(job.spec, job.log)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
    except Exception as e:
        job.log("compute order spawn FAILED: %r" % e)
        restore_box_clock(clock_applied, job.log)
        clear_current_run(job)
        notify(job, "COMPUTE ORDER FAILED to spawn — %r" % e)
        report_fleet_result(job, False, "spawn failed: %r" % e)
        retire_order(job.spec_path)
        return
    # Heartbeat + STOP POLL on a TIMER THREAD, not inside the read loop:
    # a silent child must never make CURRENT_RUN look stale. The event stops it on every exit route.
    beat_stop = threading.Event()
    stopped = []           # non-empty => operator stop, not a failure
    handback = []          # non-empty => the card became unusable mid-run: the order goes back to the desk
    prog_base = []         # (t, n) of the FIRST observed progress value — never a sentinel
    prog_state = {}        # the child's own printed N/M, parsed off stdout by the read loop
    beats = [0]
    engine = EngineRates(order_out)     # the factory's own tok/s (serve log + the order's engine logs)

    def _beat():
        # without the nonlocal, assigning prog_total below makes it beat-local and every
        # read of it raises UnboundLocalError into the catch-all — the beat publishes
        # nothing exactly when there is progress to publish
        nonlocal prog_total
        while not beat_stop.wait(COMPUTE_BEAT_S):
            try:
                beats[0] += 1
                if beats[0] % CARD_CHECK_EVERY_BEATS == 0:
                    why = host_unavailable_reason(desk_availability(), running=True,
                                                  gpu=job.load in ("gpu", "mixed"),
                                                  plex_ok=coexists_with_plex(job.spec))
                    if why:
                        # a lost card keeps "succeeding" on CPU at a fraction of the speed; Plex
                        # owns Softserve while it transcodes. Either way this run ends here.
                        handback.append(why)
                        job.log("card unavailable mid-run (%s) -> ending the child to hand the order back"
                                % why)
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                       capture_output=True)
                        return
                slip = stop_order(job)
                if slip and not slip.get("at_step"):   # compute has no steps; a bare slip = now
                    first_stop = not stopped
                    stopped.append(slip)
                    job.log("compute order STOP slip honoured: %r" % (slip.get("why") or ""))
                    try:
                        os.remove(slip["_path"])       # consume it so it cannot re-fire
                    except OSError:
                        pass
                    # A live chat window finishes the turn in flight, saves the chat's state and exits by itself: a kill
                    # would cut the answer and lose what the engine has read. A SECOND slip means now.
                    if first_stop and yield_live_window(args, job.log):
                        continue
                    # /T: a payload may have spawned its own children; end the tree, not the root
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True)
                    return
                step, eta, phase_now = None, "", phase_str
                n = int(prog_state.get("n") or 0)
                if n:
                    # the worker's own count is authoritative (the caption pattern);
                    # its printed total also beats a stale denominator
                    t = int(prog_state.get("total") or 0)
                    if t and t != prog_total:
                        prog_total = t
                        job.total_steps = prog_total
                elif prog_glob:
                    # fallback for a payload that prints nothing but writes markers
                    hits = glob.glob(prog_glob)
                    n = len(hits)
                    if n and not prog_total:
                        # conventional payloads write `_total` beside their markers; read
                        # it once the dir exists so the tile gets a real denominator
                        try:
                            with open(os.path.join(os.path.dirname(hits[0]),
                                                   "_total"), encoding="utf-8") as tf:
                                prog_total = int(tf.read().strip() or 0)
                            if prog_total:
                                job.total_steps = prog_total
                        except OSError:
                            pass
                if prog_glob or prog_state:
                    step = n if n else None   # 0 observed = nothing measurable yet
                    now = time.time()
                    if n and (not prog_base or n > prog_base[-1][1]):
                        prog_base.append((now, n))
                    rate = unit_rate(prog_base)
                    if rate is not None:
                        phase_now = "%s · %.1f s/unit" % (phase_str, rate)
                        if prog_total and n < prog_total:
                            eta = "%dm%02ds" % divmod(int((prog_total - n) * rate), 60)
                engine_toks = engine.poll()       # advanced every beat, so offsets never fall behind
                live = live_toks(prog_state.get("beats"), time.time())
                toks = prog_state.get("toks") or (None if live else engine_toks)
                phase_now = solved_phase(toks_phase(phase_now, toks, live=live), prog_state.get("solved"))
                if prog_state.get("void"):
                    phase_now = "%s · void %d" % (phase_now, prog_state["void"])
                # the tile's write rate is the one the phase carries (live, else the median): one derivation
                publish_current_run(job, state="compute", step=step, eta=eta, phase=phase_now,
                                    work_beat=prog_state.get("beat"), gen=engine.view(write=live or median(toks)))
            except Exception:
                pass   # a heartbeat must never take down the order it reports on
    threading.Thread(target=_beat, daemon=True).start()
    last_out = ""          # the child's last non-empty line IS the reason it died: a
    try:                   # SystemExit message or a traceback's final `Error: ...` line
        for line in proc.stdout:
            job.log("[compute] %s" % line.rstrip())
            if line.strip():
                last_out = line.strip()
                pm = COMPUTE_PROG_RE.match(line)
                if pm:
                    prog_state["n"] = int(pm.group(1))
                    prog_state["total"] = int(pm.group(2))
                    tm = COMPUTE_TOKS_RE.search(line)
                    if tm:
                        prog_state["toks"] = (prog_state.get("toks") or [])[-(TOKS_WINDOW - 1):] + [float(tm.group(1))]
                    sm = COMPUTE_SOLVED_RE.search(line)
                    if sm:
                        prog_state["solved"] = (int(sm.group(1)), int(sm.group(2)))
                        vm = COMPUTE_VOID_RE.search(line)
                        prog_state["void"] = int(vm.group(1)) if vm else 0
                am = COMPUTE_ALERT_RE.match(line.strip())
                if am and am.group(1) not in prog_state.setdefault("alerts", set()):
                    prog_state["alerts"].add(am.group(1))
                    notify_desk("ALERT %s: %s" % (job.name, am.group(1)))
                bm = COMPUTE_BEAT_RE.match(line)
                if bm:
                    prog_state["beat"] = int(bm.group(1))
                    tnow = time.time()
                    prog_state["beats"] = [b for b in prog_state.get("beats") or []
                                           if tnow - b[0] <= LIVE_TOKS_FRESH_S] + [(tnow, prog_state["beat"])]
        rc = proc.wait()
    except Exception as e:
        job.log("compute order stream error: %r" % e)
        rc = proc.wait()
    finally:
        beat_stop.set()
    restore_box_clock(clock_applied, job.log)   # before the lane reads free: the next order must not inherit the clock
    clear_current_run(job)      # cleanup by the route that sees the end (the caller), always
    if handback and not stopped:
        # Not a failure and nothing ships: the rows made after the card went are CPU rows, and the next
        # factory produces the product. Partial output stays in this order's own folder.
        why = handback[0]
        job.log("=== compute order interrupted: %s (rc=%d) ===" % (why, rc))
        if pinned_here(job.spec):
            # stays queued under its own name, so it resumes in this {ORDER_OUT} and the payload skips finished work
            job.log("=== pinned to this box: WAITING for the card, not handing back ===")
            raise LaneBusy("card unavailable mid-run: %s - pinned here, waiting" % why)
        ans = hand_back_to_desk(job.spec, why, job.log)
        if ans:
            job.log("=== HANDED BACK mid-run: desk -> %s %s ===" % (ans.get("outcome"), ans.get("host") or ""))
            retire_order(job.spec_path)
            raise HandedBack("%s (%s -> %s %s)" % (why, job.name, ans.get("outcome"), ans.get("host") or ""))
        # a local order, or the desk could not take it: it stays queued and waits for the card
        raise LaneBusy("card unavailable mid-run: %s" % why)
    # a refused order stays queued and retries under the same name; every other ending ships what exists
    shipped = ship_compute(job) if rc != REFUSED_RC or stopped else None
    if stopped:
        job.log("=== compute order STOPPED at operator request (rc=%d) ===" % rc)
        notify(job, "COMPUTE ORDER STOPPED — %s ended by stop slip; partial output %s"
                    % (job.name, ("shipped -> %s" % shipped) if shipped else "kept"))
        report_stopped(job, stopped)
    elif rc == 0 and job.state.get("ship_error"):
        why = "ran clean but SHIP FAILED (%s); output kept at %s" % (job.state["ship_error"], order_out_dir(job))
        job.log("=== compute order complete (rc=0) but NOT DELIVERED: %s ===" % why)
        report_fleet_result(job, False, why[:300])
    elif rc == 0:
        job.log("=== compute order complete (rc=0) ===")
        if not quiet:
            notify(job, "COMPUTE ORDER COMPLETE — %s (rc=0)%s"
                        % (job.name, (" -> %s" % shipped) if shipped else ""))
        report_fleet_result(job, True)
    elif rc == REFUSED_RC:
        why = (last_out or "no output; see log")[:300]
        # Non-pinned orders must be handed back to the desk; retrying locally risks reloading the model while the engine is free, causing thrashing. Pinned orders stay queued for this box.
        if not pinned_here(job.spec):
            ans = hand_back_to_desk(job.spec, "refused: " + why, job.log)
            if ans:
                job.log("=== REFUSED -> HANDED BACK: desk -> %s %s ===" % (ans.get("outcome"), ans.get("host") or ""))
                retire_order(job.spec_path)
                raise HandedBack("refused: %s (%s -> %s %s)" % (why, job.name, ans.get("outcome"), ans.get("host") or ""))
        n = record_refusal(job.spec_path, why)
        job.log("=== compute order REFUSED by its payload (rc=%d, refusal %d) -> stays queued, "
                "retry in %ds: %s ===" % (rc, n, REFUSED_RETRY_S, why))
        if n == 1 or n % REFUSED_DM_EVERY == 0:
            notify(job, "COMPUTE ORDER REFUSED (held, not failed) — %s, refusal %d: %s. Stays queued; "
                        "retries every %d min." % (job.name, n, why, REFUSED_RETRY_S // 60))
        raise LaneBusy("payload refused (%s) - order held for retry" % why)
    else:
        bad = unreachable_shares([payload] + args + [job.spec])
        if bad:
            # the child died while a share its inputs live on is down: that is the share, not the order
            job.log("=== compute order ended rc=%d while %s is unreachable -> WAITS, not failed ===" % (rc, ", ".join(bad)))
            raise LaneBusy("input share unreachable after rc=%d: %s" % (rc, ", ".join(bad)))
        job.log("=== compute order FAILED rc=%d ===" % rc)
        # the DM carries the reason: the runner streams the child's output, so the last
        # line is already in hand -- never send the operator to fetch what this thread
        # just read.
        why = (last_out or "no output; see log")[:300]
        notify(job, "COMPUTE ORDER FAILED — %s (rc=%d): %s" % (job.name, rc, why))
        report_fleet_result(job, False, ("exited rc=%d: %s" % (rc, why))[:300])
    retire_order(job.spec_path)


# ── state machine ───────────────────────────────────────────────────────────────────────

DEMON_HEARTBEAT = os.path.join(P.TELEMETRY_DIR, "stall_rescue.heartbeat")
DEMON_LOCK = os.path.join(P.TELEMETRY_DIR, "stall_rescue.lock")
DEMON_STALE_S = 120.0
# Required wherever the wedge disease lives (an AMD card whose MES-class collision only the demon's respawn ladder
# cures): FOUNDRY_REQUIRE_DEMON=1, a setting placed on that box; off everywhere else until a box grows a rescue daemon.
DEMON_REQUIRED = P.setting("FOUNDRY_REQUIRE_DEMON", "0").strip() == "1"
_demon_last_dm = [0.0]


def demon_watching():
    """Alive = a fresh heartbeat FILE, nothing else (process-exists and task-status can vouch for a
    corpse). The daemon is single-threaded, so a stale beat file beside fresh pushes means beat()'s
    write is failing -- reported loudly -- not a zombie worker."""
    try:
        return (time.time() - os.path.getmtime(DEMON_HEARTBEAT)) <= DEMON_STALE_S
    except OSError:
        return False


def revive_demon(log):
    """Revive a hung daemon by clearing the stale lock and re-firing.

    Distinguish between a hung loop and a failing heartbeat write:
    - Alive status + stale heartbeat: I/O failure. Do not kill; next instance will also fail.
    - Stale status + stale heartbeat: Hung/dead loop. Kill to allow supervisor recovery."""
    status_path = os.path.join(P.STATE_DIR, "status", "stall_rescue.json")
    try:
        status_age = time.time() - os.path.getmtime(status_path)
    except OSError:
        status_age = None
    if status_age is not None and status_age < 60.0:
        log("demon gate: daemon loop is ALIVE (status file %.0fs old) but its heartbeat file is "
            "stale - a failing beat WRITE, not a hung daemon. NOT killing a live guard; v6.1's "
            "own beat alarm should have DM'd the operator." % status_age)
        return False
    try:
        pid = 0
        try:
            with open(DEMON_LOCK, encoding="utf-8") as f:
                pid = int((f.read().strip() or "0"))
        except Exception:
            pid = 0
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)
            log("demon gate: cleared stale lock holder pid %d" % pid)
        subprocess.run(["schtasks", "/run", "/tn", "P20 Stall Rescue"],
                       capture_output=True, timeout=30)
        t0 = time.time()
        while time.time() - t0 < 45:
            if demon_watching():
                log("demon gate: revived - heartbeat fresh again")
                return True
            time.sleep(5)
    except Exception as e:
        log("demon gate: revival attempt failed (%r)" % (e,))
    return False


def require_demon(job):
    """Enforces daemon availability for protected work types.

    Returns LaneBusy (WAIT) if the daemon is down or lacks required capabilities,
    keeping the order queued rather than failing it. This prevents running unprotected
    work when monitoring is unavailable.

    On failure, one automatic revival attempt is made to handle common mechanical
    issues (e.g., stale singleton holders). If revival fails, the lane retries later.

    Capability matching:
    - The daemon publishes its watched work types in its status file.
    - An order requires a watch capability matching its mode (e.g., 'compute').
    - Daemons without a capabilities list are legacy; they grandfather train/render
      but block compute orders."""
    if not DEMON_REQUIRED:
        return
    if demon_watching():
        _mode = ((getattr(job, "spec", None) or {}).get("mode") or "").strip().lower()
        watch = "compute" if _mode == "compute" else None
        if watch:
            caps = None
            try:
                with open(os.path.join(P.STATE_DIR, "status", "stall_rescue.json"),
                          encoding="utf-8") as f:
                    caps = json.load(f).get("capabilities")
            except Exception:
                caps = None
            needed = "watch." + watch
            if (caps is None) or (needed not in caps):
                if time.time() - _demon_last_dm[0] > 900:
                    _demon_last_dm[0] = time.time()
                    notify(job, "ORDER HELD - the demon is beating but does not declare %r, so "
                                "it cannot monitor this work type. Order %s stays queued until a "
                                "demon version with that arm is running." % (needed, job.name))
                raise LaneBusy("demon beating but lacks %r - this work type would run "
                               "unsupervised" % needed)
        job.log("demon gate: watching (heartbeat fresh) - factory viable")
        return
    job.log("demon gate: heartbeat STALE/ABSENT - attempting one revival")
    if revive_demon(job.log):
        return
    if time.time() - _demon_last_dm[0] > 900:
        _demon_last_dm[0] = time.time()
        notify(job, "DEMON NOT WATCHING and revival failed - %s is DOWN FOR WORK until the stall "
                    "rescue daemon beats again. Order %s stays queued untouched. Check: "
                    "%s / rescue_console.log"
               % (FLEET_HOST, job.name, os.path.join(P.TELEMETRY_DIR, "stall_rescue.heartbeat")))
    raise LaneBusy("demon not watching (heartbeat stale) and revival failed - the machine is "
                   "down for work until the guard is back")


def run(job, shadow=False):
    # "compute" mode skips the state machine (no bake/trainer/daemon) and returns early.
    if job.mode == "compute":
        return run_compute_order(job)
    job.log("=== foundry v0.3 start (shadow=%s) — job %s, %d steps, previews every %d (%s), initiator=%s ==="
            % (shadow, job.name, job.total_steps, job.preview_every, job.previews_mode, job.initiator))

    while True:
        phase = job.state["phase"]

        if phase == "PREFLIGHT":
            # Runs first in preflight: mismatches are spec defects, not transient waits.
            # Archive immediately (do not raise LaneBusy).
            batch_guard(job)
            # Rescue-daemon liveness: check heartbeat freshness, not task status.
            # Task status is unreliable (child exits, parent reports "Ready" even if dead).
            # Heartbeat threshold is generous vs 15s poll to avoid false alarms.
            # Stale/absent heartbeat -> one revival attempt -> LaneBusy (queued retry).
            # See require_demon().
            require_demon(job)

            # The worker writes render_progress.json, the ONLY input for the stall daemon's
            # mercy rule (render_nearly_done). Without it, the daemon cannot distinguish a
            # wedged job from a nearly-finished one and may kill both.
            whb = os.path.join(os.path.dirname(RESCUE_LOG), "render_worker.heartbeat")
            try:
                wage = time.time() - os.path.getmtime(whb)
                if wage <= 120:
                    job.log("render worker: OK (heartbeat %.0fs ago) - mercy rule has data" % wage)
                else:
                    job.log("render worker: WARNING - heartbeat is %.0fs STALE. Foundry renders will "
                            "publish NO progress, so the stall daemon's mercy rule is blind and will "
                            "kill a render at ANY completion percentage. Restart it: "
                            "schtasks /run /tn \"P20 Render Worker\"" % wage)
            except OSError:
                job.log("render worker: WARNING - no heartbeat at %s. Foundry renders will publish NO "
                        "progress, so the stall daemon's mercy rule is blind and will kill a render at "
                        "ANY completion percentage. Restart it: "
                        "schtasks /run /tn \"P20 Render Worker\"" % whb)
            retire_stale_embeds(job)
            all_prompts = (job.preview_prompts + job.rotate_prompts + job.ab_prompts
                           + job.probe_prompts + job.finish_prompts)
            missing = [p for p in all_prompts if cond_needs_bake(p["name"], p.get("text") or "")]
            if missing:
                job.log("preflight: baking %d missing conditioning(s) while the card is free" % len(missing))
                ensure_comfy(job)
                bake_missing(job, all_prompts)
                stop_comfy(job)
            job.state["phase"] = ("PROBE" if job.mode == "probe"
                                  else "CAPTION" if job.mode == "caption" else "LAUNCH")
            job.save()

        elif phase == "LAUNCH":
            if shadow and trainer_running(job):
                job.log("shadow mode: attaching to already-running trainer")
            elif not trainer_running(job):
                launch_trainer(job)
            job.state["started"] = job.state["started"] or datetime.datetime.now().isoformat()
            try:
                _bat = job.spec.get("batch")
                if _bat:
                    _qtxt = " | arm %s of %s in batch '%s'" % (
                        job.spec.get("batch_arm", "?"), job.spec.get("batch_size", "?"),
                        job.spec.get("batch_label") or _bat)
                else:
                    _qtxt = ""
                _others = [x for x in queued_orders()
                           if os.path.basename(x)[:-len(".job.json")] != job.name]
                _same, _diff = [], []
                for x in _others:
                    try:
                        _s = json.load(open(x, encoding="utf-8"))
                    except Exception:
                        _s = {}
                    (_same if (_bat and _s.get("batch") == _bat) else _diff).append(
                        os.path.basename(x)[:-len(".job.json")])
                if _same:
                    _qtxt += "; %d more arm(s) after this" % len(_same)
                if _diff:
                    _qtxt += " | %d unrelated order(s) queued: %s" % (len(_diff), ", ".join(_diff[:3]))
            except Exception:
                _qtxt = ""
            _pv = ("previews OFF" if (job.previews_mode == "none" or not job.preview_prompts)
                   else "previews every %d (%s)" % (job.preview_every, job.previews_mode))
            notify(job, "run started/attached — %d steps, %s%s"
                   % (job.total_steps, _pv, _qtxt))
            job.state["phase"] = "TRAIN_WATCH"; job.save()

        elif phase == "TRAIN_WATCH":
            cks = checkpoints(job)
            publish_runner_card("working", "training watch: %s" % job.name)
            # Training pause triggers immediate lossless checkpoint (STOP_NOW).
            # Unlike renders (which finish the cell to avoid mid-denoise corruption),
            # training saves ck + optimizer within a step, allowing safe resume.
            if pause_requested():
                if trainer_running(job):
                    job.log("OPERATOR PAUSE during training -> lossless checkpoint pause first")
                    pause_trainer(job)
                    # Marker distinguishing deliberate operator pause from crash: only marked
                    # pauses auto-relaunch; unmarked (trainer-down/crash) wait for human.
                    job.state["paused_cleanly"] = True
                    job.save()
                raise PauseRequested("training checkpointed at operator pause")
            # Fires only if milestone checkpoint exists AND high-priority orders are waiting.
            # No timer; late probes run at next natural lane-clear.
            _ydone = job.state.get("yielded") or []
            _ymark = next((m for m in job.yield_at
                           if m not in _ydone and any(c >= m for c in cks)), None)
            if _ymark is not None:
                _waiting = [x for x in queued_orders() if order_priority(x) > 0]
                if _waiting:
                    if trainer_running(job):
                        job.log("LANE YIELD at ck%d: %d priority order(s) waiting -> lossless "
                                "checkpoint pause, lane lent, this run resumes by FIFO"
                                % (_ymark, len(_waiting)))
                        pause_trainer(job)
                        job.state["paused_cleanly"] = True   # yield resumes itself, same marker
                    job.state.setdefault("yielded", []).append(_ymark); job.save()
                    raise LaneYield("yield_at ck%d: %s waiting"
                                    % (_ymark, ", ".join(os.path.basename(x) for x in _waiting[:3])))
                # milestone reached with an empty priority queue: consume it silently -- checking
                # forever would make the mark a standing invitation, not a milestone.
                job.state.setdefault("yielded", []).append(_ymark); job.save()
                job.log("yield_at ck%d reached with no priority orders waiting - mark consumed" % _ymark)
            # Check stop before completion: stops win over 'keep going'.
            # TRAIN_WATCH only: RENDER phase runs uninterrupted to avoid partial outputs.
            _stop = stop_order(job)
            if _stop is not None:
                _at = int(_stop.get("at_step") or 0)
                _now = current_step(job) or 0
                _top = max(cks, default=0)
                if _at and _now < _at and _top < _at:
                    job.log("stop order ACCEPTED: production ends at step %d (now %s) - %s"
                            % (_at, _now or "?", _stop.get("why") or "no reason given"))
                else:
                    if trainer_running(job):
                        job.log("STOP ORDER: ending production - lossless pause first")
                        pause_trainer(job)          # STOP_NOW: checkpoint at this step, clean exit
                        cks = checkpoints(job)
                        _top = max(cks, default=_top)
                    _end = max(_top, _now, 1)
                    # Re-point the ORDER at where production actually ended. Everything downstream
                    # (near_end, the ladder's `s == total_steps` case, the ETA, the register entry)
                    # reads this, so amending it is what makes an early stop indistinguishable from
                    # a run that was always this length -- rather than special-casing five places.
                    job.total_steps = _end
                    job.state["stopped_early"] = {"at": _end,
                                                  "order": os.path.basename(_stop["_path"]),
                                                  "why": _stop.get("why") or ""}
                    retire_order(_stop["_path"])
                    notify(job, "STOP ORDER honoured — production ended at step %d. Shipping what "
                                "exists: ladder renders from the saved checkpoints, then register "
                                "and clean up." % _end)
                    job.log("STOP ORDER honoured at step %d -> LADDER (shipping)" % _end)
                    job.state["phase"] = "LADDER"; job.save(); continue
            # Completion: ai-toolkit saves UNSUFFIXED <name>.safetensors as final.
            # Done if numbered total_steps exists OR (trainer gone + near_end +
            #      (final_file_fresher_than_newest_ckpt OR newest_step >= total)).
            # Guard: only trust crash-inferred done near end; prevents ladder against
            # undertrained weights from emergency pause/resume crashes.
            final_file = os.path.join(job.output_dir, trainer_name(job) + ".safetensors")
            newest_numbered = max((os.path.getmtime(p) for p in cks.values()), default=0.0)
            final_fresh = os.path.exists(final_file) and os.path.getmtime(final_file) > newest_numbered
            newest_step = max(cks, default=0)
            near_end = newest_step >= job.total_steps - job.preview_every
            if job.total_steps in cks or (not trainer_running(job) and near_end and
                                          (newest_step >= job.total_steps or final_fresh)):
                # Render final milestone before ladder takes over (trainer already stopped).
                _planned = (job.total_steps in job.ab_at
                            or job.total_steps in (job.spec.get("extra_previews") or [])
                            or (job.preview_every and job.total_steps % job.preview_every == 0))
                # _planned is not enough: benchmarks may set preview_every == total_steps,
                # yielding zero prompts/checkpoints. We must verify there is something to render.
                _renderable = bool(milestone_prompts(job, job.total_steps)
                                   or (job.total_steps in job.ab_at and job.ab_prompts))
                if (_planned and _renderable
                        and job.total_steps not in (job.state.get("previews_done") or [])):
                    job.log("final step %d is a due milestone - rendering it before the ladder"
                            % job.total_steps)
                    job.state["pause_for"] = job.total_steps
                    job.state["phase"] = "RENDER"; job.save(); continue
                job.state["phase"] = "LADDER"; job.save(); continue
            milestones = sorted(set(list(range(job.preview_every, job.total_steps,
                                                job.preview_every)) + job.extra_previews))
            # Milestones trigger on step reach, not checkpoint existence.
            # Requiring `s in cks` prevented off-grid previews (e.g., save 100 + sample 172).
            # PAUSE creates the checkpoint; thus any cadence works.
            _s = current_step(job)
            due = [s for s in milestones if s not in job.state["previews_done"]
                   and (s in cks or (_s and _s >= s))]
            # A preview is "where the run is NOW", not a backlog to replay. Two ways a stale
            # milestone can queue a pointless pause, and the live 20:01 failure was the second:
            #   * several come due at once (a long gap, or a resume far past them)
            #   * ONE comes due that is older than a preview already rendered -- previews_done was
            #     [100,125,300] at step 304, so 200 was due ALONE and len(due)>1 never fired
            # Both are the same thing: anything at or behind the newest rendered preview shows
            # nothing that preview does not, and each replay costs a full trainer pause. That pause
            # is what landed on a still-booting trainer and produced "needs a human".
            _newest_done = max(job.state["previews_done"] or [0])
            _stale = [s for s in due if s <= _newest_done]
            due = [s for s in due if s > _newest_done]
            if len(due) > 1:
                _stale += due[:-1]
                due = [due[-1]]
            if _stale:
                job.state["previews_done"].extend(_stale)
                job.state["previews_done"] = sorted(set(job.state["previews_done"]))
                job.save()
                job.log("milestones %s skipped: already past them (newest rendered %d); no pause each"
                        % (sorted(set(_stale)), _newest_done))
            if due and job.preview_prompts:
                if job.previews_mode == "deferred":
                    # v0.3 deferred: never pause — the ckpt file is already saved; its previews render
                    # in one batch at FINISH. Just acknowledge the milestone.
                    job.state["previews_done"].append(due[0]); job.save()
                    job.log("milestone ckpt %d saved (deferred previews — no pause)" % due[0])
                    continue
                if job.previews_mode == "quick":
                    # Quick previews run on a smaller base model; they are approximations, not
                    # final quality checks. Artifacts must be tagged as 'quick' in filename,
                    # data metadata, and render key to prevent cache collisions with real previews.
                    #
                    # Milestones are step-based triggers, but quick mode never pauses saves.
                    # The requested step may not have a file yet (e.g., between save intervals).
                    # Always use the newest existing checkpoint at or below the milestone step;
                    # report which step was actually used.
                    _cks = checkpoints(job)
                    _have = [s for s in sorted(_cks) if s <= due[0]]
                    if not _have:
                        # No checkpoint yet. Do NOT consume the milestone -- leave it due so it
                        # fires at the next poll once a save lands. Nothing is lost, nothing lies.
                        job.log("milestone %d due but no checkpoint written yet - quick preview "
                                "waits for the next save (milestone stays due)" % due[0])
                        continue
                    _at = _have[-1]
                    n = enqueue_quick_preview(job, _at)
                    job.state["previews_done"].append(due[0]); job.save()
                    if n:
                        job.log("milestone %d -> %d QUICK preview(s) queued off-card from ck%d; "
                                "trainer keeps stepping (APPROXIMATE - different base model)"
                                % (due[0], n, _at))
                    else:
                        # Not a failure of the RUN. Say so loudly and keep training: a preview that
                        # could not be placed must never consume a healthy training order.
                        job.log("milestone %d: no factory could take a quick preview - "
                                "training continues, previews skipped for this milestone" % due[0])
                    continue
                job.state["pause_for"] = due[0]
                job.state["phase"] = "PAUSE"; job.save(); continue
            write_holds()       # write_holds: crash-safe auto-expiring keep-alive; read state from card to prevent
# stale 'free' status after trainer death.
# Publish `phase` (BOOT) during setup (~27 min, step=0) so the UI distinguishes
# legitimate initialization from a stall.
            _step = current_step(job)
            publish_current_run(job, state="training" if trainer_running(job) else "paused",
                                step=_step, eta=job.state.get("eta") or "",
                                phase="" if _step else boot_progress(job))
            # Stall daemon signals rescue via RESCUE_PAUSE + STOP_NOW.
            # Without this, a clean trainer exit is misidentified as a crash,
            # leaving the job stuck in 'waiting for an operator' state.
            if os.path.exists(RESCUE_PAUSE) and not trainer_running(job):
                job.log("RESCUE_PAUSE present and trainer exited cleanly -> daemon-requested recycle")
                try:
                    os.remove(RESCUE_PAUSE)
                except OSError:
                    pass
                if os.path.exists(STOP_NOW):
                    os.remove(STOP_NOW)     # never let a stale flag kill the fresh trainer
                notify(job, "daemon requested a lossless recycle (poisoned trainer context); "
                            "checkpoint saved, relaunching onto a verified-clean card.")
                launch_trainer(job)         # includes the card_quiet handshake
                job.state["dead_polls"] = 0; job.save()
                continue
            if job.state.get("paused_cleanly") and not trainer_running(job):
                # Resume from clean pause: `paused_cleanly` flag + checkpoint proves safe restart.
                # Distinguishes intentional pause from hard crash (which keeps wait-for-human).
                job.log("clean-pause marker present (operator pause / lane yield) -> relaunching "
                        "from the saved checkpoint")
                job.state["paused_cleanly"] = False
                if os.path.exists(STOP_NOW):
                    os.remove(STOP_NOW)     # never let a stale flag kill the fresh trainer
                job.state["dead_polls"] = 0
                job.state["dead_quiet"] = 0
                job.save()
                launch_trainer(job)
                continue
            if not trainer_running(job):
                job.log("WARNING: trainer not running mid-run (crash? operator pause?) — waiting")
                job.state["dead_polls"] = job.state.get("dead_polls", 0) + 1
                # The process being gone is already evidence; the wait was only ever debounce against
                # a relaunch transient. So debounce on EVIDENCE instead of a 5-minute countdown: a
                # trainer that is coming back still writes to its log, a crashed one does not. Three
                # Six quiet polls (~2 min) still beats the old 15, and leaves margin for a slow
                # relaunch: acting on a trainer that was merely slow to appear could start a
                # SECOND one, which is far worse than noticing a crash a minute later.
                try:
                    _sz = os.path.getsize(job.log_path)
                except OSError:
                    _sz = -1
                job.state["dead_quiet"] = (job.state.get("dead_quiet", 0) + 1
                                           if _sz == job.state.get("dead_log_size") else 0)
                job.state["dead_log_size"] = _sz
                # Distinguish launch failures (pre-step-1, no checkpoint) from mid-run crashes.
                # Launch failures are transient (e.g., stale cache); auto-retry up to 2 times.
                # Mid-run crashes require human intervention; do not retry.
                _boot_fail = (not current_step(job) and not checkpoints(job)
                              and job.state.get("boot_retries", 0) < 2)
                if job.state["dead_quiet"] == 6 and _boot_fail:
                    job.state["boot_retries"] = job.state.get("boot_retries", 0) + 1
                    job.log("died before step 1 -> FAILED LAUNCH, auto-retry %d/2 (re-validating caches)"
                            % job.state["boot_retries"])
                    notify(job, "launch failed before step 1 — retrying automatically (%d/2) after "
                                "re-validating caches. No checkpoint at risk."
                                % job.state["boot_retries"])
                    retire_stale_embeds(job)          # the usual culprit, and free to re-check here
                    job.state["dead_polls"] = 0
                    job.save()
                    launch_trainer(job)
                    continue
                if job.state["dead_quiet"] == 6:       # ~2 min of a SILENT log: really dead
                    notify(job, "trainer NOT RUNNING mid-run and its log has gone silent — I didn't "
                                "pause it, so likely a crash. Waiting for an operator; last ckpt is safe.")
                job.save()
            else:
                if job.state.get("dead_polls") or job.state.get("dead_quiet"):
                    job.state["dead_polls"] = 0; job.state["dead_quiet"] = 0; job.save()
            step = current_step(job)
            if step and not job.state.get("boot_profiled"):
                record_boot_profile(job)
                job.state["boot_profiled"] = True; job.save()
            if step:
                job.log("watch: step %s/%d, ckpts=%s" % (step, job.total_steps, sorted(cks)))
                eta = eta_text(job)
                if eta and eta != job.state.get("eta"):
                    job.state["eta"] = eta; job.save()
            elif trainer_running(job):
                job.log("watch: trainer BOOTING — %s" % boot_progress(job))
            time.sleep(POLL)

        elif phase == "PAUSE":
            # Do not drop STOP_NOW while trainer is loading (no steps yet). Use progress-based
            # timeout, not deadline: a healthy cold boot can take ~27 min, so fixed timeouts
            # kill healthy runs. Detect stall via lack of advancement in either boot waypoints
            # or log size; if neither advances for ~4 min, assume hung.
            _last_sig, _stalled = None, 0
            while trainer_running(job) and not current_step(job):
                try:
                    _codes = _boot_waypoints(job)[1] or []
                except Exception:
                    _codes = []
                try:
                    _sz = os.path.getsize(job.log_path)
                except OSError:
                    _sz = -1
                _sig = (boot_phase(job), len(_codes), _sz)
                if _sig != _last_sig:
                    _last_sig, _stalled = _sig, 0        # something moved: still booting, keep waiting
                else:
                    _stalled += 1
                if _stalled >= 12:                       # ~4 min with NO waypoint and NO log growth
                    job.log("WARNING: boot has not advanced in %d polls (phase=%s, log %d bytes, "
                            "%d waypoints) - not booting, pausing anyway"
                            % (_stalled, _sig[0] or "unknown", _sz, len(_codes)))
                    break
                job.log("PAUSE deferred: trainer still booting (%s) - not dropping STOP_NOW yet"
                        % (boot_phase(job) or "early startup"))
                # Renew claim during boot to prevent foreign_run_guard from reclaiming the GPU
                # while the trainer is initializing (up to 27 min). state="training" is derived
                # from the alive trainer process, not asserted.
                publish_current_run(job, state="training",
                                    step=job.state.get("pause_for"),
                                    phase="trainer booting (%s) - pause deferred"
                                          % (boot_phase(job) or "early startup"),
                                    ttl=RENDER_CLAIM_TTL)
                time.sleep(POLL)
            # Extend claim TTL through render pause; scheduler reclaims after RENDER_CLAIM_TTL
            # but rendering exceeds that duration. Prevents double-booking while ComfyUI holds resource.
            publish_current_run(job, state="rendering", step=job.state.get("pause_for"),
                                phase="pausing for preview render", ttl=RENDER_CLAIM_TTL)
            time.sleep(15)                      # let the save fully flush
            pause_trainer(job)
            job.state["phase"] = "RENDER"; job.save()

        elif phase == "RENDER":
            write_holds()
            step = job.state["pause_for"]
            publish_current_run(job, state="rendering", step=step,
                                phase="rendering preview at step %s" % step, ttl=RENDER_CLAIM_TTL)
            # If no checkpoints exist (e.g. short benches), the order already succeeded;
            # skip rendering to avoid IndexError rather than failing the job.
            _cks = checkpoints(job)
            if not _cks:
                job.log("milestone %s: no numbered checkpoints exist - skipping the render" % step)
                job.state["previews_done"].append(step)
                job.state["phase"] = "RESUME"; job.save(); continue
            ensure_comfy(job)
            _pset = milestone_prompts(job, step)
            bake_missing(job, _pset)
            # The pause saves at whatever step the trainer reached, which can be a step or two
            # past the milestone (172 -> a save at 174). Render the nearest checkpoint AT OR
            # AFTER the milestone; `checkpoints(job)[step]` would KeyError on exactly the
            # off-grid cadences this is meant to support.
            _at = sorted(k for k in _cks if k >= step) or sorted(_cks)
            _use = _at[0] if any(k >= step for k in _cks) else _at[-1]
            if _use != step:
                job.log("milestone %d -> rendering ckpt %d (the pause saved there)" % (step, _use))
            render(job, _cks[_use], _use, _pset, dm_each=True, strength=job.preview_strength)
            # A/B arm: same prompts, same seed, adapter on vs off. Rendered in the SAME pause, so it
            # costs no extra trainer downtime beyond the renders themselves and never competes with
            # the trainer for the card.
            if step in job.ab_at and job.ab_prompts:
                job.log("A/B milestone %d: %d prompt(s), adapter ON then OFF" % (step, len(job.ab_prompts)))
                bake_missing(job, job.ab_prompts)
                render(job, _cks[_use], _use, job.ab_prompts, dm_each=True, tag="_LORA")
                # STRENGTH SWEEP: same prompt, same seed, adapter dialled down. An over-driven
                # adapter on a small corpus degrades out-of-distribution geometry AND loses fine
                # detail -- exactly the pair of symptoms seen at ck750 (legs) and ck804 (global
                # softness). If 0.6/0.8 render cleaner, the fix is a strength default, not more data.
                _sweep = [p for p in job.ab_prompts if p["name"] in (job.spec.get("ab_sweep_names") or [])]
                for _s in (job.spec.get("ab_strengths") or []):
                    if _sweep:
                        job.log("A/B strength sweep @ %.1f (%d prompts)" % (float(_s), len(_sweep)))
                        render(job, _cks[_use], _use, _sweep, dm_each=True,
                               tag="_s%02d" % int(round(float(_s) * 10)), strength=float(_s))
                # `"base": false` on a prompt renders the adapter arm ONLY. Used for the synonym
                # probes: the question there is "does the capability fire on this wording at all",
                # and the base's answer is already known from the core prompts -- so paying another
                # 3.5 min of trainer downtime per synonym buys nothing.
                _base_set = [p for p in job.ab_prompts if p.get("base", True)]
                render(job, _cks[_use], _use, _base_set, dm_each=True, base_only=True, tag="_BASE")
                notify(job, "A/B at step %d complete: each prompt above rendered WITH the adapter "
                            "(_LORA) and with NO adapter (_BASE), same seed 42." % step)
            if not job.resident_server:
                stop_comfy(job)
            else:
                free_comfy(job)
            job.state["previews_done"].append(step)
            # images already streamed per-render (dm_each); this is the wrap-up text + live ETA
            notify(job, "step-%d preview set complete (%d images above). %s"
                   % (step, len(_pset), eta_text(job)))
            job.state["phase"] = "RESUME"; job.save()

        elif phase == "RESUME":
            launch_trainer(job)
            job.state["phase"] = "TRAIN_WATCH"; job.save()

        elif phase == "PROBE":
            # Measure one checkpoint: every prompt with the adapter, the flagged ones without it,
            # and the sweep set at each requested strength. Filenames carry the arm so the result
            # is readable without notes.
            write_holds()
            publish_current_run(job, state="probing", step=job.probe_step,
                                phase="A/B probe: %s" % os.path.basename(job.probe_lora),
                                ttl=RENDER_CLAIM_TTL)
            if not os.path.exists(job.probe_lora):
                raise RuntimeError("probe_lora does not exist: %s" % job.probe_lora)
            ensure_comfy(job)
            bake_missing(job, job.probe_prompts)
            _made, _miss = 0, 0     # DELIVERABLE ACCOUNTING: a probe's product IS its renders.
            _pr, _fl = render(job, job.probe_lora, job.probe_step, job.probe_prompts,
                              dm_each=True, tag="_LORA")
            _made += _pr; _miss += _fl
            _base = [q for q in job.probe_prompts if q.get("base", True)]
            _sweep = [q for q in job.probe_prompts if q["name"] in job.sweep_names]
            # seeds x strengths. Empty probe_seeds = [DEFAULT_SEED] = the old single draw.
            _seeds = job.probe_seeds or [DEFAULT_SEED]
            for _sd in _seeds:
                if _base:
                    job.log("probe BASE arm (no adapter) seed %d (%d prompts)" % (_sd, len(_base)))
                    _pr, _fl = render(job, job.probe_lora, job.probe_step, _base, dm_each=True,
                                      base_only=True, tag="_BASE", seed=_sd)
                    _made += _pr; _miss += _fl
                for _s in job.probe_strengths:
                    if _sweep:
                        job.log("probe sweep @ strength %.1f seed %d (%d prompts)"
                                % (_s, _sd, len(_sweep)))
                        _pr, _fl = render(job, job.probe_lora, job.probe_step, _sweep, dm_each=True,
                                          tag="_s%02d" % int(round(_s * 10)), strength=_s, seed=_sd)
                        _made += _pr; _miss += _fl
            # Persist `_made` to state; ledger runs after retirement and only state survives.
            # Without this, `_order_shipped_count` finds no deliverables and reports 0.
            job.state["probe_rendered"] = _made
            job.save()
            stop_comfy(job)
            # Zero renders from a probe is a failure (not success). Raise to archive
            # as failed; partial results complete but are noted in the log.
            if _made == 0 and (_miss > 0 or job.probe_prompts):
                raise RuntimeError("probe produced 0 renders (%d attempts failed); the deliverable "
                                   "does not exist. Common cause: the render graph's base model is "
                                   "not present on this box. Check the ComfyUI log." % _miss)
            notify(job, "A/B probe complete on %s: %d rendered, %d FAILED. Filenames carry the arm "
                        "(_LORA / _BASE / _sNN)."
                   % (os.path.basename(job.probe_lora), _made, _miss))
            job.state["phase"] = "DONE"; job.save()

        elif phase == "CAPTION":
            import concurrent.futures as _cf
            # Default to this box's root for staging: absolute paths in templates break on factories
            # with different mount roots (e.g. Softserve uses E:, others F:). Using job.name ensures
            # each order gets a unique, empty staging dir, preventing stale-file inheritance bugs.
            _src = (job.spec.get("caption_src") or "").strip() or \
                os.path.join(P.VAR, "work", "caption", job.name)
            _model = job.spec.get("caption_model") or "huihui_ai/qwen3-vl-abliterated:8b"
            _host = job.spec.get("caption_host") or "http://127.0.0.1:11434"
            _sch = (job.spec.get("caption_schema") or caption_default()).strip().lower()
            # The structured schema and its prompt are a MATCHED PAIR: the prompt forbids the
            # paragraph from mentioning framing/cropping/watermarks precisely because the schema
            # requires them as fields. Pairing them by default stops the two halves drifting apart
            # (an order that picked the schema but kept an old prompt would get both the omission
            # and the repetition back).
            _kinds = caption_kinds()
            if _sch not in _kinds:
                raise ValueError("caption_schema %r is not a caption kind this factory has (%s): refused, never "
                                 "coerced into another" % (_sch, ", ".join(sorted(_kinds))))
            _cprompt = job.spec.get("caption_prompt") or _kinds[_sch]["prompt"]
            _mside = int(job.spec.get("caption_max_side") or 768)
            # caption_schema (read above, with the prompt it pairs with): "gated" (default) =
            # prose + confidence-gated fields, right for a general VLM being kept honest.
            # "minimal" = one caption field, for a model TRAINED to write captions, where the
            # extra fields fight its training. "structured" = prose + REQUIRED framing /
            # out_of_frame / watermark fields, for facts that must never be omitted.
            _nctx = int(job.spec.get("caption_num_ctx") or 4096)
            _workers = max(1, min(int(job.spec.get("caption_workers") or 2), 4))
            _over = bool(job.spec.get("caption_overwrite"))
            _stage_from = (job.spec.get("caption_stage_from") or "").strip()
            if _stage_from:
                stage_caption_set(job, _stage_from, _src,
                                  count=int(job.spec.get("caption_stage_count") or 0),
                                  stratify=bool(job.spec.get("caption_stage_stratify", True)),
                                  include=job.spec.get("caption_stage_include") or ())
            if not os.path.isdir(_src):
                raise RuntimeError("caption_src does not exist: %s" % _src)
            publish_current_run(job, state="captioning", step=0,
                                phase="caption preflight: loading %s" % _model,
                                ttl=RENDER_CLAIM_TTL)
            try:
                # Stamped ONCE and persisted, so a resumed order keeps the same cutoff and the
                # sidecars it already wrote still count as done (see caption_targets).
                if not job.state.get("caption_started_at"):
                    job.state["caption_started_at"] = time.time(); job.save()
                _todo, _skipped = caption_targets(_src, overwrite=_over,
                                                  newer_than=job.state["caption_started_at"])
                job.log("caption: %d to caption, %d already done this run (resume keys honored, "
                        "overwrite=%s)" % (len(_todo), _skipped, _over))
                _fields = {}          # basename -> the schema fields, minus the prose
                _framing = {}         # basename -> measured framing, fed INTO the caption prompt
                _stopped = False      # Bounded early to avoid UnboundLocalError in exit handler if framing raises before definition.
                _write_manifest = None

                # Run framing before loading the VLM.
                #
                # Framing provides input for captioning; running it first avoids asking the
                # VLM to guess frame boundaries. This ordering also enforces "one model at a
                # time": the detector runs in a subprocess (clearing GPU memory on exit) so
                # the 8.8 GB VLM can load without conflict.
                if job.spec.get("caption_framing"):
                    # Run in a subprocess to guarantee the detector's weights are freed via process exit,
                    # preventing silent ModuleNotFoundError failures and ensuring the card is clear for
                    # the subsequent 8.8 GB VLM load.
                    _framed, _noperson = 0, 0
                    _fr_err = None
                    if not FRAMING_PY:
                        # Should be unreachable: caption.framing is dropped from the registry when
                        # no torch interpreter exists, so an order requiring it is HELD rather than
                        # run. Kept as a belt, and it is a hard failure, never a warning.
                        _fr_err = ("no interpreter with torch+torchvision was found, so framing "
                                   "cannot run on this factory")
                    else:
                        job.log("framing: keypoint pass via %s over %d image(s) - BEFORE the VLM, "
                                "so measured framing can be fed into each caption prompt"
                                % (FRAMING_PY, len(_todo)))
                        publish_current_run(job, state="captioning", step=0,
                                            phase="framing 0/%d (keypoint pass)" % len(_todo),
                                            ttl=RENDER_CLAIM_TTL)
                        _t_fr = time.time()
                        try:
                            _out, _err, _seen, _rc = run_framing_watched(job, _src, len(_todo))
                            if _rc == -3:
                                # Operator stop DURING framing. Not a failure: end production here
                                # and ship, exactly as a stop honored mid-caption does. Falls
                                # through to the normal completion path so the manifest, the
                                # shipping stage and the archive all behave identically.
                                _stopped = True
                                _todo = []
                                notify(job, "STOP ORDER honored during the framing pass - ending "
                                            "production here. Nothing was captioned in this run.")
                            elif _rc != 0:
                                _fr_err = ("p20_framing.py exited %d after %d/%d image(s): %s"
                                           % (_rc, _seen, len(_todo), _err.strip()[-300:]))
                            else:
                                try:
                                    _all = json.loads(_out)
                                except ValueError as _je:
                                    # Preserve stderr context; JSON errors often mask underlying child failures.
                                    raise RuntimeError(
                                        "p20_framing.py returned unparseable output (%s). It "
                                        "printed %d byte(s) on stdout and said: %s"
                                        % (_je, len(_out or ""),
                                           (_err or "(nothing on stderr)").strip()[-400:]))
                                for _bn, _fr in _all.items():
                                    if not _fr or _fr.get("error"):
                                        _noperson += 1
                                        continue
                                    _rec = _fields.setdefault(_bn, {})
                                    # Only the caption-ready facts land in the record; the raw
                                    # landmark counts stay out, they are the detector's working.
                                    _rec["crop"] = _fr["crop"]
                                    _rec["head_crop"] = _fr["head_crop"]
                                    _rec["framing_tags"] = _fr["framing_tags"]
                                    _rec["limbs_out"] = _fr["limbs_out"]
                                    if _fr.get("inverted"):
                                        _rec["inverted"] = True
                                    _framing[_bn] = _fr
                                    _framed += 1
                        except Exception as _e:
                            _fr_err = repr(_e)[:300]

                    if _fr_err:
                        # Fail fast: requested framing must succeed or the job fails.
                        raise RuntimeError("caption_framing was requested and could not run: %s"
                                           % _fr_err)
                    _inv = sum(1 for v in _framing.values() if v.get("inverted"))
                    job.log("framing: %d tagged in %.0fs, %d with no confident person (recorded as "
                            "absent, never guessed), %d stored UPSIDE DOWN"
                            % (_framed, time.time() - _t_fr, _noperson, _inv))
                    if not _framed:
                        raise RuntimeError(
                            "caption_framing was requested but produced framing for ZERO of %d "
                            "images - shipping captions without the measurement they were "
                            "ordered with is the failure this check exists to stop" % len(_todo))
                    if _inv:
                        notify(job, "framing found %d image(s) stored upside down - the caption "
                                    "prompt is told, the pixels are left alone" % _inv)

                _vlm_ensure_resident(job, _host, _model, num_ctx=_nctx)
                _schema = _kinds[_sch]["schema"]
                _emit_fr = bool(job.spec.get("caption_emit_framing"))
                # The framing vocabulary is a property of the ORDER (a spec field), so an arm can vary it -
                # including list ORDER, which the constrained decoder is sensitive to - without a code change.
                _terms = job.spec.get("caption_framing_terms")
                if _terms and _sch in ("structured", "framing_only"):
                    _schema = json.loads(json.dumps(_schema))       # never mutate the constant
                    _schema["properties"]["framing"]["enum"] = list(_terms)
                    job.log("caption framing enum OVERRIDDEN by the order: %s" % (list(_terms),))
                job.log("caption schema=%s num_ctx=%d prompt_sha=%s"
                        % (_sch, _nctx, __import__("hashlib").sha256(
                            _cprompt.encode()).hexdigest()[:12]))
                _done, _failed = 0, []
                _t0 = time.time()

                _truncated = []

                def _one(p):
                    # Up to 3 draws, keeping the first COMPLETE one. Sampling is the only thing
                    # that varies between attempts, and the variance probe measured that spread
                    # as wide -- which is exactly why a retry is worth anything here (unlike the
                    # 07-28 retry that reproduced its own failure condition).
                    #
                    # The prompt is now PER IMAGE: the constant instruction plus whatever the
                    # detector measured about THIS photograph. Images with no confident person get
                    # the bare prompt, unchanged.
                    _pr = _cprompt + framing_clause(_framing.get(os.path.basename(p)))
                    cap, raw = None, {}
                    for _att in (1, 2, 3):
                        raw = _vlm_caption_one(_host, _model, _pr, p, _mside, _schema,
                                               num_ctx=_nctx)
                        # framing_only is a MEASUREMENT, so its sidecar keeps the framing sentence
                        # (the fields are the product); a training caption suppresses it.
                        cap = compose_caption(raw, emit_framing=_emit_fr or _sch == "framing_only")
                        # Prose schemas get the word floor; tag-only schemas legitimately return
                        # one short sentence, so the floor would reject every one of them.
                        _floor = CAPTION_MIN_WORDS if _sch in ("rich", "rich2", "structured",
                                                               "minimal", "gated") else 0
                        if not caption_looks_truncated(cap, min_words=_floor):
                            break
                    else:
                        _truncated.append(os.path.basename(p))
                    # KEEP THE RAW FIELDS. compose_caption DROPS the negatives on purpose ("only
                    # name what VARIES"), which means a suppressed "Nothing is out of frame." is
                    # invisible in the sidecar -- and the pre-registered failure condition for the
                    # structured schema is precisely an EVASION: a required field satisfied with a
                    # confident falsehood on an image whose head is plainly cropped. A grammar can
                    # force the field to EXIST; only a human can check that it is TRUE, and they
                    # cannot check what was never written down. Mandatory-but-unauditable is half
                    # a feature.
                    if raw and len(raw) > 1:
                        _rec = {k: v for k, v in raw.items() if k != "caption"}
                        if "head_in_frame" in _rec:
                            # Derived, not asked. Stored beside its inputs so the arithmetic stays
                            # auditable -- if the term is ever wrong we can see which of the four
                            # observations produced it.
                            _rec["crop_derived"] = derive_crop(_rec)
                        # Merge via update() to preserve pre-VLM crop/limb facts set by framing pass.
                        # Replacing the dict would drop these, causing manifest/caption inconsistency.
                        _fields.setdefault(os.path.basename(p), {}).update(_rec)
                    sidecar = os.path.splitext(p)[0] + ".txt"
                    tmp = sidecar + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.write(cap + "\n")
                    os.replace(tmp, sidecar)
                    return p

                _mpath = os.path.join(_src, "_caption_manifest.json")
                _mwritten = []
                # Do not reset _stopped here. Initialization moved before framing so that
                # a cancellation during framing is preserved; resetting here would silently
                # clear the stop and allow execution to proceed into captioning.

                def _write_manifest():
                    """Record what was captioned. Called on every exit (including pause).

                    Always MERGE; never overwrite, to preserve records from prior partial runs."""
                    if _mwritten:
                        return
                    _mwritten.append(True)
                    man = {"order": job.name, "src": _src, "model": _model,
                           "prompt_sha256": __import__("hashlib").sha256(
                               _cprompt.encode()).hexdigest()[:16],
                           "captioned": _done, "skipped_existing": _skipped,
                           "failed": len(_failed), "failures": _failed[:50],
                           # kept SEPARATE from failures: these captions were written but ran out
                           # of retries still ending mid-sentence. Silent would be the bug.
                           "truncated_after_retry": len(_truncated),
                           "truncated_files": _truncated[:50],
                           "max_side": _mside, "workers": _workers,
                           "schema": _sch,
                           # TIMING IS A DELIVERABLE: s/img must be in the order's output, not only in the raw run log,
                           # or every speed question means reading an archived .foundry.log and risks reading the wrong
                           # generation of one.
                           "started_at": datetime.datetime.fromtimestamp(_t0).isoformat(
                               timespec="seconds"),
                           "elapsed_s": round(time.time() - _t0, 1),
                           "s_per_img": (round((time.time() - _t0) / _done, 2)
                                         if _done else None)}
                    try:
                        with open(_mpath, encoding="utf-8") as f:
                            prior = json.load(f)
                    except Exception:
                        prior = {}
                    merged = merge_manifest_fields(prior.get("fields"), _fields)
                    gens = list(prior.get("generations") or [])
                    if prior and not gens:
                        gens.append({"prompt_sha256": prior.get("prompt_sha256"),
                                     "schema": prior.get("schema"),
                                     "captioned": prior.get("captioned"),
                                     "note": "reconstructed from a manifest written before "
                                             "generations were tracked"})
                    gens.append({"prompt_sha256": man["prompt_sha256"], "schema": _sch,
                                 "captioned": _done, "elapsed_s": man["elapsed_s"],
                                 "s_per_img": man["s_per_img"], "workers": _workers,
                                 "finished_at": datetime.datetime.now().isoformat(
                                     timespec="seconds")})
                    man["fields"] = merged
                    man["generations"] = gens
                    man["captioned_total"] = len(merged) or _done
                    try:
                        tmp = _mpath + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as f:
                            json.dump(man, f, indent=1)
                        os.replace(tmp, _mpath)
                        job.log("manifest: %d image record(s) across %d generation(s)"
                                % (len(merged), len(gens)))
                    except OSError as _e:
                        job.log("WARNING: manifest write failed (%r)" % (_e,))

                with _cf.ThreadPoolExecutor(max_workers=_workers) as _pool:
                    _batch = 8 * _workers
                    for _i in range(0, len(_todo), _batch):
                        if pause_requested():
                            raise PauseRequested("operator pause honored mid-caption at %d/%d "
                                                 "(sidecars are the resume keys)"
                                                 % (_done, len(_todo)))
                        # Check stop_order() per batch after sidecars are durable.
                        # Must consume (delete) the slip to prevent it from blocking future runs.
                        _so = stop_order(job)
                        if _so is not None:
                            # Retire the stop slip after use to prevent stale files from blocking future runs.
                            try:
                                retire_order(_so["_path"])
                            except Exception as _e:
                                job.log("WARNING: could not retire the stop slip (%r)" % (_e,))
                            _stopped = True
                            job.log("STOP ORDER honored at %d/%d - ending production here and "
                                    "shipping what exists" % (_done, len(_todo)))
                            notify(job, "STOP ORDER honored at %d/%d. Ending production here; "
                                        "everything captioned so far is written, recorded in the "
                                        "manifest and shipped." % (_done, len(_todo)))
                            break
                        _chunk = _todo[_i:_i + _batch]
                        for _f in _cf.as_completed({_pool.submit(_one, p): p for p in _chunk}.keys()):
                            try:
                                _f.result()
                                _done += 1
                            except Exception as _e:
                                _failed.append(repr(_e)[:140])
                                if len(_failed) <= 10 or len(_failed) % 50 == 0:
                                    job.log("caption FAILURE #%d: %s"
                                            % (len(_failed), _failed[-1]))
                                    if len(_failed) in (1, 10):
                                        notify(job, "caption failure #%d (run continues, failures "
                                                    "are counted not silently kept): %s"
                                                    % (len(_failed), _failed[-1]))
                            _rate = (time.time() - _t0) / max(1, _done)
                            _eta_s = _rate * (len(_todo) - _done)
                            _ph = ("captioning %d/%d · %.1f s/img · ~%dm left · %d failed"
                                   % (_done, len(_todo), _rate, _eta_s / 60, len(_failed)))
                            publish_current_run(job, state="captioning", step=_done, phase=_ph,
                                                ttl=RENDER_CLAIM_TTL)
                            publish_runner_card("working", _ph)
                            if _done and _done % 100 == 0:
                                notify(job, "caption progress: " + _ph)
                # (The framing pass used to sit HERE, after the VLM was unloaded. It now runs before
                # the VLM is ever loaded, so its measurements can be fed into each caption prompt --
                # see the keypoint block at the top of this mode. Same card discipline, opposite
                # order, and the measurement finally reaches the thing it was measured for.)
                _write_manifest()
                notify(job, "CAPTION complete: %d captioned, %d skipped (existing sidecar), %d "
                            "FAILED, %d truncated after retry (failures and truncations are "
                            "COUNTED, never silently recorded as good captions). Manifest in the "
                            "source folder." % (_done, _skipped, len(_failed), len(_truncated)))
                # Runs before finally; delivery failures surface as order failures, not swallowed by unload.
                ship_captions(job, _src)
                # A stopped run is DONE, not paused: the operator asked production to END, so the
                # order retires normally rather than sitting in the queue waiting to resume.
                job.state["phase"] = "DONE"
                job.state["stopped_early"] = _stopped
                # `_order_shipped_count` scans for previews/ladders/probes/checkpoints.
                # Caption orders produce none, so they would score 0 and be stamped "SHIPPED NOTHING"
                # if we didn't explicitly set `captioned`. The ledger ignores `_caption_manifest.json`.
                job.state["captioned"] = _done
                job.save()
            finally:
                # Ensure resources are freed and state persisted on any exit path (normal
                # or exception). _vlm_unload is mandatory to free GPU memory; _write_manifest
                # is idempotent, so it is safe to call even if already written.
                try:
                    if _write_manifest:
                        _write_manifest()
                except Exception as _e:
                    job.log("WARNING: manifest write on exit failed (%r)" % (_e,))
                _vlm_unload(job, _host, _model)

        elif phase == "LADDER":
            write_holds()
            # Publish status during LADDER phase; otherwise UI freezes on last TRAIN value.
            publish_current_run(job, state="finishing", step=job.total_steps,
                                phase="rendering the checkpoint ladder", ttl=RENDER_CLAIM_TTL)
            job.log("run complete — rendering the checkpoint ladder")
            ensure_comfy(job)
            bake_missing(job, job.preview_prompts + job.finish_prompts)
            cks = checkpoints(job)
            for s in sorted(cks):
                if s in job.state["ladder_done"]:
                    continue
                if s % job.preview_every and s != job.total_steps:
                    continue    # skip pause exit-saves (421/503-style orphans) — ladder = milestones only
                # live mode: milestones already got their full preview set at the pause -> control
                # prompt only here. deferred mode: this IS the preview render -> full set per milestone.
                ladder_prompts = job.preview_prompts if job.previews_mode == "deferred" \
                    else job.preview_prompts[:1]
                render(job, cks[s], s, ladder_prompts, dm_each=True, strength=job.preview_strength)
                job.state["ladder_done"].append(s); job.save()
            final = os.path.join(job.output_dir, trainer_name(job) + ".safetensors")
            if os.path.exists(final):
                render(job, final, job.total_steps, job.finish_prompts, dm_each=True,
                       strength=job.preview_strength)
            stop_comfy(job)
            job.state["phase"] = "REGISTER"; job.save()

        elif phase == "REGISTER":
            if is_benchmark_order(job.spec):
                job.log("REGISTER skipped: the order's kind declares a benchmark/measurement - "
                        "its weights are byproducts, nothing for an operator to review")
                job.state["phase"] = "RESTORE"; job.save()
                continue
            entry = dict(job.register)
            entry.setdefault("kind", "image_lora")
            entry.setdefault("path", os.path.join(job.output_dir, trainer_name(job) + ".safetensors"))
            entry.setdefault("steps", job.total_steps)
            entry.setdefault("trained", datetime.date.today().isoformat())
            entry.setdefault("status", "pending-review")
            reg_path = os.path.splitext(job.spec_path)[0] + ".registration.json"
            with open(reg_path, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2)
            job.log("registration artifact written: %s (operator/orchestrator applies to person record)" % reg_path)
            job.state["phase"] = "RESTORE"; job.save()

        elif phase == "RESTORE":
            for p in (DOWNTIME_HOLD,):     # keep-awake goes via the spec's delete_files (operator choice)
                try:
                    os.remove(p); job.log("restore: downtime signal cleared (%s)" % p)
                except FileNotFoundError:
                    pass
            for svc in job.restore.get("enable_services", []):
                # spec: {name: "...", startup: "Automatic"|"Manual"} — re-enable a service preflight
                # disabled (recipe step 5). Fail-soft per service.
                r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                    "Set-Service -Name '%s' -StartupType %s; Start-Service -Name '%s' -ea 0"
                                    % (svc["name"], svc.get("startup", "Automatic"), svc["name"])],
                                   capture_output=True, text=True)
                job.log("restore: service %r -> %s (rc=%d)" % (svc["name"], svc.get("startup", "Automatic"), r.returncode))
            for t in job.restore.get("enable_tasks", []):
                r = subprocess.run(["schtasks", "/change", "/tn", t, "/enable"], capture_output=True, text=True)
                job.log("restore: enable task %r -> rc=%d" % (t, r.returncode))
            for t in job.restore.get("start_tasks", []):
                r = subprocess.run(["schtasks", "/run", "/tn", t], capture_output=True, text=True)
                job.log("restore: run task %r -> rc=%d" % (t, r.returncode))
            for cmd in job.restore.get("launch_commands", []):
                # Use Win32_Process.Create (WMI) to launch SYSTEM tasks from session contexts;
                # `schtasks /run` fails for these tasks.
                r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                    "(Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                                    "-Arguments @{CommandLine='%s'}).ReturnValue" % cmd],
                                   capture_output=True, text=True)
                job.log("restore: launch %r -> %s" % (cmd, (r.stdout or "").strip() or r.returncode))
            for p in job.restore.get("delete_files", []):
                try:
                    os.remove(p); job.log("restore: deleted %s" % p)
                except FileNotFoundError:
                    job.log("restore: %s already absent" % p)
                except Exception as e:
                    job.log("WARNING restore: could not delete %s (%r)" % (p, e))
            job.state["phase"] = "DONE"; job.save()

        elif phase == "DONE":
            clear_current_run(job)      # a finished run must stop claiming to be current
            if job.state.get("done_reported"):
                job.log("=== job already complete and reported; idle exit ===")
                return
            job.state["done_reported"] = True; job.save()
            started = job.state.get("started")
            wall = ""
            if started:
                dt = datetime.datetime.now() - datetime.datetime.fromisoformat(started)
                wall = "%dh%02dm" % (dt.total_seconds() // 3600, (dt.total_seconds() % 3600) // 60)
            notify(job, "RUN COMPLETE — %s\nwall: %s | pauses: %d | daemon rescues: %s\nfinal: %s\n"
                   "registration artifact + ladder on the box; box restored."
                   % (job.name, wall, len(job.state["previews_done"]), rescue_count_since(started or ""),
                      os.path.join(job.output_dir, trainer_name(job) + ".safetensors")))
            job.log("=== job complete ===")
            return

        else:
            raise RuntimeError("unknown phase %r" % phase)


# QUEUE_DIR must be derived from foundry_paths, NOT from dirname(COND_DIR).
# Deriving it from COND_DIR breaks compatibility if the cache location changes independently.
QUEUE_DIR = P.QUEUE


def acquire_runner_singleton(log_line):
    """Enforces singleton runner via PID lock.

    Ensures only one order taker accesses the queue to prevent race conditions
    that can wedge the daemon. The logic implements a stale-heartbeat check:
    if the holder PID is alive but its heartbeat is stale, it is treated as a
    corpse (wedged) rather than deferring silently. This prevents silent failures
    by providing explicit error context to operators instead of blocking startup."""
    try:
        if os.path.exists(RUNNER_LOCK):
            old = open(RUNNER_LOCK, encoding="utf-8").read().strip()
            live, why = foundry_lock.holder_is_live(RUNNER_LOCK)
            if live:
                hb_age = None
                try:
                    hb_age = time.time() - os.path.getmtime(RUNNER_HEARTBEAT)
                except OSError:
                    pass
                if hb_age is not None and hb_age > 900:
                    log_line("RUNNER LOCK held by pid %s which is ALIVE but has not heartbeat in "
                             "%.0fs -- likely WEDGED. Deferring anyway (never two order takers). "
                             "Remedy: kill pid %s (elevated if needed), then schtasks /run."
                             % (old, hb_age, old))
                else:
                    log_line("another order taker is live (%s) - exiting so orders are not raced" % why)
                return False
            log_line("reclaiming stale runner lock (%s)" % why)
    except Exception as e:
        log_line("runner lock check failed (%r) - continuing" % (e,))
    try:
        with open(RUNNER_LOCK, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        log_line("could not write runner lock (%r) - continuing unlocked" % (e,))
    return True


def runner_beat():
    """Check liveness via freshness (timestamp), not process existence."""
    try:
        with open(RUNNER_HEARTBEAT, "w", encoding="utf-8") as f:
            f.write("%d %d" % (int(time.time()), os.getpid()))
    except Exception:
        pass


_CAPS_ANNOUNCED = [0.0]
CAPS_ANNOUNCE_EVERY = 120.0     # seconds; cheap, and the scheduler wants a fresh answer


def _announce_capabilities():
    """Announce capabilities to the fleet while idle.

    Reports `state: idle` (part of RELEASING_STATES) to declare capability without claiming the card.
    Rate-limited via `_CAPS_ANNOUNCED`; fail-soft to avoid interrupting order processing."""
    now = time.time()
    if now - _CAPS_ANNOUNCED[0] < CAPS_ANNOUNCE_EVERY:
        return
    if not ORCH_BASE:
        _unlinked_note()
        return
    _CAPS_ANNOUNCED[0] = now
    body = json.dumps({"host": FLEET_HOST, "name": "", "state": "idle",
                       "capabilities": declared_capabilities()}).encode("utf-8")
    rq = urllib.request.Request(FLEET_STATE_URL, data=body,
                                headers={"Content-Type": "application/json"})
    urllib.request.urlopen(rq, timeout=5).read()


def publish_runner_card(state, phase=""):
    """Publish runner status and heartbeat. Call from main loop or inside long-running
    work to prevent stale SILENT/wedged states. Recomputes queue count on each call.
    Never raises."""
    runner_beat()
    try:
        _p20_status.publish(
            "foundry_runner", state,
            sources=[
                _p20_status.source("queue_dir", QUEUE_DIR, stale_after=10 ** 9,
                                   note="%d order(s) pending" % len(queued_orders())),
                # absent BETWEEN orders is the correct state, not a fault
                _p20_status.source("current_run", CURRENT_RUN, stale_after=RENDER_CLAIM_TTL,
                                   note="lane claim", absent_ok=True),
            ],
            notes=str(phase)[:160],
            extra={"lock": RUNNER_LOCK, "heartbeat": RUNNER_HEARTBEAT,
                   "host": FLEET_HOST,
                   "capabilities": declared_capabilities(),
                   "queued": len(queued_orders())})
    except Exception:
        pass


def release_runner_singleton():
    try:
        if os.path.exists(RUNNER_LOCK):
            if open(RUNNER_LOCK, encoding="utf-8").read().strip() == str(os.getpid()):
                os.remove(RUNNER_LOCK)
    except Exception:
        pass


def pause_requested():
    """The operator pause flag. Checked between renders and between orders -- never mid-render."""
    return os.path.exists(PAUSE_FLAG)


def consume_pause_flag():
    try:
        os.remove(PAUSE_FLAG)
    except OSError:
        pass


class OrderCancelled(RuntimeError):
    """Raised when a customer withdraws an order while it is running. This triggers
    archiving via the standard error handler, ensuring the lane is released rather
    than held."""


def order_cancel_file(name):
    """The stop channel for ONE running order: `<order>.stop.json` in the queue dir."""
    return os.path.join(QUEUE_DIR, "%s.stop.json" % name)


def order_cancel_requested(name):
    """Return True if the desk requested cancellation of this specific running order.

    Distinction from general control orders:
    - General control slips are read *between* orders. A cancel slip for a currently
      executing order would be ignored until that order finished, rendering it useless.
    - This method checks `*.stop.json`, which is read by the RUNNING job itself, allowing
      immediate termination of active production.

    Note: This extends the existing stop channel; no new control channels should be added."""
    return os.path.exists(order_cancel_file(name))


def order_priority(path):
    """An order's `priority` (higher runs first; default 0). Fail-soft: unreadable = 0 -- a bad
    field must not change scheduling, and .yaml orders simply keep FIFO behaviour."""
    try:
        with open(path, encoding="utf-8") as f:
            return float(json.load(f).get("priority") or 0)
    except Exception:
        return 0.0


class CapabilityMissing(RuntimeError):
    """Raised when an order requires capabilities not yet present in the factory.

    Unlike ARCHIVE (broken spec), WAIT (busy lane), or holding the factory,
    this indicates a temporary gap. The order is held and re-checked until
    the capability is implemented."""


# What this factory can actually do. A capability is named here ONLY when the code behind it is
# deployed -- this registry is a PROMISE, and the whole point is that it cannot be hand-waved.
FACTORY_CAPABILITIES = {
    "train":                "run a LoRA training order end to end",
    "probe":               "render a probe/measurement set against a checkpoint",
    # Klein baking requires a text encoder (Mothership); rendering uses the GGUF model (BigHonker).
    # `probe` cannot distinguish these capabilities, so they are declared separately to prevent
    # routing bake jobs to render-only nodes.
    "bake.klein":          "encode klein conditioning (Qwen3-4B text encoder present) - the .cond a "
                           "klein render consumes instead of loading an encoder itself",
    "render.klein":        "render against the klein-4B base (klein GGUF present) - a 32B adapter "
                           "will NOT load into a 4B network, so this is not implied by `probe`",
    "render.flux2":        "render against the FLUX.2-dev base (flux2 GGUF present) - the 32B the "
                           "main track trains; declared by whichever box actually holds that file",
    "render.krea2":        "render against the Krea 2 base (Turbo, its Qwen3-VL encoder and the Qwen-Image VAE where "
                           "ComfyUI loads them; p20_krea_render.py) - a FLUX.2 adapter will NOT load into Krea 2",
    "train.krea2":         "train a LoRA on the Krea 2 base with the Krea-ready ai-toolkit installed beside the "
                           "FLUX.2 one (foundry_paths.AI_TOOLKIT_KREA2)",
    "caption":             "VLM-caption an image folder with an audited manifest",
    "caption.schema.minimal":    "prose only, for models trained to write captions",
    "caption.schema.framing_only": "framing as a single focused question",
    "caption.resume":      "sidecar-keyed resume, idempotent across pause/relaunch",
    "caption.audit":       "raw schema fields retained per image in the manifest",
    "caption.timing":      "elapsed / s_per_img / workers recorded in the manifest",
    "caption.ship":        "deliverables shipped to deliver_to, so nothing is collected by hand",
    "caption.ship.images": "ship the SOURCE IMAGES alongside the captions (caption_ship_images) - a "
                           "visual finding cannot be judged from text, and without this the images "
                           "get fetched by hand off the shop floor",
    "caption.stop":        "end a caption run where asked and SHIP what exists (*.stop.json)",
    "caption.stage":       "the ORDER states its inputs; the factory stages them (caption_stage_from)",
    "caption.stage.include": "the order can NAME specific images that must be staged "
                             "(caption_stage_include) - a verification order can guarantee its own "
                             "test case is present instead of hoping a sample contains it",
    "caption.framing":     "mechanical framing from keypoints, BEFORE the VLM loads (caption_framing)",
    "caption.limbs":       "measured framing fed INTO each caption prompt: limbs described when in "
                           "shot, stated as absent when not, inverted subjects flagged",
    "lane.yield":          "lend the lane at a declared milestone and resume losslessly",
    "lane.pause":          "operator pause between units of work, order stays queued",
    "order.after_exists":  "hold an order invisible until a filesystem fact is true",
    "order.priority":      "priority field on the order, no manual queue surgery",
    "order.control_slip":  "pause/requeue/cancel/priority via a slip at the desk",
    "render.browser":      "show a job's page in a browser on this box's real GPU (compute payload bin\\render_order.py, "
                           "venv-render: setup_render_venv.py)",
    # `order.compute` is a KIND promise; `weights.*` rows are DATA-LOCALITY promises.
    # Steps require weights without naming hosts: move weights, routing follows.
    "order.compute":       "run a general compute payload as a factory order (mode:compute, "
                           "load gpu|mixed|cpu; stop via *.stop.json like every order kind)",
    "weights.flux2-dev":   "the FLUX.2-dev 32B training checkpoint is on this box "
                           "(model-cache _official tree) - SVD/analysis jobs read it directly",
    "weights.klein-4b":    "the klein-4B base safetensors are on this box (model-cache "
                           "klein-base-4b tree) - SVD/analysis jobs read it directly",
    # Library triage, classify lane. A compute-order payload
    # (bin\triage_classify.py) that classifies residual library sections against the deployed
    # rubric (bin\triage_rubric.md, byte-identical to Reference\Library-Section-Triage-Rubric.md)
    # with the LOCAL ollama model: JSON schema in `format`, no tools, no RAG, serving model
    # recorded per row. The version suffix is the RUBRIC's version -- bump both together so every
    # result stays attributable to the rubric that produced it.
    "triage.rubric.v1":    "classify library sections against the v1 triage rubric with the "
                           "local ollama model (compute payload bin\\triage_classify.py)",
    # Library triage, rewrite lane. A compute-order payload
    # (bin\triage_rewrite.py) that drafts charter-conformant section rewrites with the local
    # ollama model: charter + section in, {rewrite, cuts, notes} schema out, serving model
    # recorded per row. DRAFTS ONLY -- a human applies them; nothing touches the live library.
    # The version suffix is the CHARTER's version (bin\triage_charter.md, byte-identical to
    # Runbook\LIBRARY-CHARTER.md at deploy) -- bump both together, same rule as the rubric.
    "triage.charter.v1":   "draft charter-conformant library rewrites with the local ollama "
                           "model (compute payload bin\\triage_rewrite.py)",
}


# ComfyUI's own folder keys for a model kind: `unet` and `clip` are its legacy names for the same folders.
_COMFY_KIND_KEYS = {"diffusion_models": ("diffusion_models", "unet"), "text_encoders": ("text_encoders", "clip"),
                    "vae": ("vae",)}


def read_extra_model_paths(path):
    """Read ComfyUI's extra_model_paths.yaml as {entry: {key: value}}.

    Falls back to a manual parser if PyYAML is unavailable; do not rely on PyYAML.
    Manual parser supports ComfyUI's documented format: comments, quoted values, and `|` blocks holding one path per line."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if yaml is not None:
        return yaml.safe_load(text) or {}
    out, entry, block = {}, None, None
    for raw in text.splitlines():
        line = raw.rstrip()
        indent = len(line) - len(line.lstrip())
        if block is not None:
            if line.strip() and indent > block[1]:
                out[entry][block[0]] += line.strip() + "\n"
                continue
            block = None
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, val = line.strip().partition(":")
        val = val.split(" #", 1)[0].strip().strip("'\"")
        if indent == 0:
            entry = key.strip()
            out[entry] = {}
        elif entry is not None:
            out[entry][key.strip()] = "" if val == "|" else val
            if val == "|":
                block = (key.strip(), indent)
    return out


def comfy_model_dirs(kind, comfy_root=None):
    """Every folder this box's ComfyUI loads `kind` from: its own models tree plus each extra_model_paths.yaml entry.
    Read from the file ComfyUI itself reads, so a declared capability cannot disagree with what the loader finds.
    A value may hold several paths, one per line - ComfyUI's own convention."""
    root = comfy_root or P.COMFY_ROOT
    keys = _COMFY_KIND_KEYS.get(kind, (kind,))
    dirs = [os.path.join(root, "models", k) for k in keys]
    try:
        entries = read_extra_model_paths(os.path.join(root, "extra_model_paths.yaml"))
    except Exception:                                                  # noqa: BLE001
        entries = {}
    for entry in entries.values():
        if isinstance(entry, dict):
            base = entry.get("base_path") or ""
            dirs += [os.path.join(base, p.strip()) for k in keys if entry.get(k)
                     for p in str(entry[k]).splitlines() if p.strip()]
    return dirs


def krea2_render_files_present(comfy_root=None):
    """The three files p20_krea_render.py's graph loads, each in a folder ComfyUI reads. The NAMES come from that
    module, so the declaration and the graph are one derivation. An unimportable renderer promises nothing."""
    try:
        import p20_krea_render as K
    except Exception:                                                  # noqa: BLE001
        return False
    need = (("diffusion_models", K.UNET), ("text_encoders", K.CLIP), ("vae", K.VAE))
    return all(any(os.path.isfile(os.path.join(d, name)) for d in comfy_model_dirs(kind, comfy_root))
               for kind, name in need)


def krea2_trainer_present(root=None):
    """A Krea-ready ai-toolkit the foundry can drive: its entrypoint, its krea2 model code, its own interpreter, AND
    the lab's STOP_NOW/SAVE_NOW patch (p20_apply_instrumentation, marker [P20-INSTR]) in its train loop - every
    pause, lane yield, stop order and rescue recycle is a STOP_NOW, and upstream ai-toolkit ignores the flag."""
    root = root or P.AI_TOOLKIT_KREA2
    if not all(os.path.isfile(os.path.join(root, *p)) for p in (
            ("run.py",), ("extensions_built_in", "diffusion_models", "krea2", "krea2.py"), ("venv", "python.exe"))):
        return False
    try:
        with open(os.path.join(root, "jobs", "process", "BaseSDTrainProcess.py"), encoding="utf-8",
                  errors="replace") as f:
            return "[P20-INSTR]" in f.read()
    except OSError:
        return False


# Capabilities are auto-detected from installed packages, not hardcoded by hostname.
# This avoids silent drift when hardware or toolkit versions change.
# The set returned varies by machine; other factory semantics (shape/order) remain identical.
def render_browser_proven(venv_dir):
    """A page-render browser is promised only after `render_order.py prove`, run as an order on this box, opened WebGL on a
    real card on EVERY launch. Absent or unreadable is not proven: a refused context is a blank picture of a good page."""
    try:
        with open(os.path.join(venv_dir, "webgl_proof.json"), encoding="utf-8") as f:
            row = json.load(f)
        return bool(row["launches"]) and row["opened"] == row["launches"] and os.path.exists(
            os.path.join(venv_dir, "Scripts", "python.exe"))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _detected_caps_to_omit():
    """Capabilities this box cannot honestly promise, from what is installed on it."""
    omit = set()
    # Detect installed capabilities, not host configuration.
    # Do NOT check only for `os.path.exists(PY)`: that is a proxy for "Python is present",
    # which fails when `FOUNDRY_PY` points to a system Python without ai-toolkit.
    # Checking for `ai-toolkit/run.py` ensures we detect actual training capability.
    # Install ai-toolkit on any box and `train` appears automatically.
    if not (os.path.exists(PY) and os.path.exists(os.path.join(P.AI_TOOLKIT, "run.py"))):
        omit |= {"train"}          # no ai-toolkit run.py: cannot run a trainer
    if not krea2_trainer_present():
        omit |= {"train.krea2"}
    if not os.path.exists(COMFY_LAUNCH):
        omit |= {"probe"}          # no ComfyUI launcher: cannot render a probe set
        omit |= {"bake.klein", "render.klein", "render.flux2", "render.krea2"}     # all run THROUGH ComfyUI
    else:
        if not krea2_render_files_present():
            omit |= {"render.krea2"}
        _cm = os.path.dirname(COMFY_LAUNCH)
        _models = os.path.join(_cm, "ComfyUI", "models")
        if not os.path.isdir(_models):                     # Mothership's tree is laid out flat
            _models = P.COMFY_MODELS
        if not glob.glob(os.path.join(_models, "text_encoders", "qwen_3_4b*")):
            omit |= {"bake.klein"}     # Architecture detection: add rows to this table for new architectures.
# Each row checks a specific model pattern; if missing, the associated
# render capability is omitted. This prevents false positives from
# registry entries that lack supporting model files.
        for _arch, _pat in (("klein", "*klein*"), ("flux2", "*flux2-dev*")):
            if not glob.glob(os.path.join(_models, "unet", _pat)):
                omit |= {"render.%s" % _arch}
    if not (FRAMING_PY and os.path.exists(os.path.join(P.BIN, "p20_framing.py"))):
        omit |= {"caption.framing", "caption.limbs"}
    # Weights locality: same rule as above. Detected from canonical cache layout,
    # never a hostname. Capability follows file presence; missing paths simply omit.
    if not glob.glob(os.path.join(P.MODEL_CACHE_DIR, "_official", "*flux2-dev*")):
        omit |= {"weights.flux2-dev"}
    if not glob.glob(os.path.join(P.MODEL_CACHE_DIR, "klein-base-4b", "*.safetensors")):
        omit |= {"weights.klein-4b"}
    # Library triage (30b): the promise is only keepable when BOTH the payload and the versioned
    # rubric are actually on this box -- same detected-from-files rule as everything above. The
    # payload asserts model residency itself at run time; a filesystem check here is the honest
    # import-time half.
    if not (os.path.exists(os.path.join(P.BIN, "triage_classify.py"))
            and os.path.exists(os.path.join(P.BIN, "triage_rubric.md"))):
        omit |= {"triage.rubric.v1"}
    # Rewrite lane: same detected-from-files rule -- payload + versioned charter both on
    # this box, or the promise is not keepable and the cap honestly omits.
    if not (os.path.exists(os.path.join(P.BIN, "triage_rewrite.py"))
            and os.path.exists(os.path.join(P.BIN, "triage_charter.md"))):
        omit |= {"triage.charter.v1"}
    return omit


for _cap in _detected_caps_to_omit():
    FACTORY_CAPABILITIES.pop(_cap, None)

# The caption kinds an extension adds declare themselves only on a factory that has that extension.
for _ext in _foundry_extensions():
    FACTORY_CAPABILITIES.update(getattr(_ext, "CAPABILITIES", {}))

# SIZE-AWARE DEALING, factory half: declare THIS box's triage context window so the desk
# never deals a section this box cannot take. Read from the same triage_local.json row the
# classify payload
# uses at run time -- the file is the single authority for the customization; a declaration
# from anywhere else would be a second derivation that drifts. No row / no file = no ctx cap
# declared, and the desk treats an undeclared window as unlimited (the payload's context guard
# remains the measuring backstop either way).
if "triage.rubric.v1" in FACTORY_CAPABILITIES:
    try:
        with open(os.path.join(P.BIN, "triage_local.json"), encoding="utf-8") as _f:
            _row = json.load(_f).get(FLEET_HOST) or {}
    except Exception:                                                # noqa: BLE001
        _row = {}
    _ctx = int(_row.get("num_ctx") or 0)
    if _ctx:
        FACTORY_CAPABILITIES["triage.ctx.%d" % _ctx] = (
            "declared context window for triage classify - the desk's size-aware deal "
            "reads this; the payload's residency+context guards still measure at run time")
    if _row.get("model"):
        # the desk's per-model rewrite policy (tier scope) keys on WHICH model this box's
        # triage row runs; model.* caps list everything installed, which is a different claim
        FACTORY_CAPABILITIES["triage.model.%s" % _row["model"]] = (
            "the model this box's triage_local row runs - the desk's tier-scoped rewrite "
            "deal reads this")


_MODEL_CAPS_CACHE = {"t": 0.0, "caps": []}


def declared_capabilities():
    """Everything this factory declares RIGHT NOW: the static detected set plus live
    `model.<name>` capabilities from the local ollama store -- the factory declares what
    models are available; the desk routes on it.

    Models are the most DYNAMIC capability -- an `ollama pull` adds one with no restart -- so
    they are detected per-push from /api/tags, cached 60 s. Fail-soft: ollama unreachable means
    NO model.* caps this beat, which is honest (a box whose ollama is down cannot serve a
    model), while the static file-detected set still declares. ONE function for every push site
    and for missing_capabilities -- five call sites each assembling their own answer is how two
    derivations drift into disagreement."""
    now = time.time()
    if now - _MODEL_CAPS_CACHE["t"] > 60:
        caps = []
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
                for m in json.load(r).get("models", []):
                    if m.get("name"):
                        caps.append("model." + str(m["name"]))
        except Exception:                                            # noqa: BLE001
            caps = []
        caps += strata_served_caps()
        _MODEL_CAPS_CACHE["t"] = now
        _MODEL_CAPS_CACHE["caps"] = sorted(caps)
    # Asked per push like the models: a box joins or leaves page renders the moment its proof order says so, no restart.
    static = set(FACTORY_CAPABILITIES)
    if not (os.path.exists(os.path.join(P.BIN, "render_order.py"))
            and render_browser_proven(os.path.join(os.path.dirname(P.BIN), "venv-render"))):
        static.discard("render.browser")
    return sorted(static) + _MODEL_CAPS_CACHE["caps"]


# STRATA_SERVED declares models from non-ollama engines (e.g. Strata) that are PRESENT on this box (plan, install, and shards exist) and serve the tested context. A declared model guarantees it fits up to that context and runs with the plan's config.
STRATA_SERVED = os.path.join(P.FOUNDRY_ROOT, "workspace", "flashnext", "strata_served.json")


def _plan_ctx(plan):
    args = [str(x) for x in plan.get("setup_args") or []]
    try:
        return int(args[args.index("--context") + 1])
    except (ValueError, IndexError):
        return None


def _plan_vision(plan):
    """True when the plan's setup enables the vision encoder via `--vision`. The launcher executes this plan, so the declaration must match the server configuration."""
    section = plan.get("vision")
    if isinstance(section, dict):
        # the plan carries Strata's own "vision" config section (the launcher writes it in on every start: Windows with
        # an AMD card, where setup will not): declared only while the encoder and its weights are on this box
        return all(os.path.isfile(str(section.get(k) or "")) for k in ("exe", "mmproj"))
    args = [str(x) for x in plan.get("setup_args") or []]
    try:
        return args[args.index("--vision") + 1].lower() in ("gpu", "cpu", "yes")
    except (ValueError, IndexError):
        return False


def strata_served_caps(path=None, host=None):
    """`model.<name>` for each Strata-served model on this box whose plan, engine and shards are present, and
    `sees.<name>` beside it when that plan serves the model with its vision encoder."""
    try:
        with open(path or STRATA_SERVED, encoding="utf-8") as f:
            rows = json.load(f).get(host or FLEET_HOST) or {}
    except Exception:                                                # noqa: BLE001
        return []
    caps = []
    for model, row in sorted(rows.items()):
        try:
            with open(row["plan"], encoding="utf-8") as f:
                plan = json.load(f)
            present = os.path.isdir(plan["strata"]) and all(
                os.path.isfile(os.path.join(plan["gguf_dir"], s)) for s in plan.get("shards") or {"": ""})
            if present and plan.get("shards") and _plan_ctx(plan) == int(row["ctx"]):
                caps.append("model." + model)
                if _plan_vision(plan):
                    caps.append("sees." + model)       # served with its vision encoder: a picture reaches it
        except Exception:                                            # noqa: BLE001
            continue
    return caps


def missing_capabilities(spec_path):
    """The `requires` entries this factory cannot satisfy. Unreadable spec -> [] (the UNREADABLE
    archive path at pickup owns that failure, not capability assessment)."""
    try:
        with open(spec_path, encoding="utf-8") as f:
            req = json.load(f).get("requires") or []
    except Exception:
        return []
    if isinstance(req, str):
        req = [req]
    return unmet_requires(req, set(declared_capabilities()))


def _ctx_of(cap):
    try:
        return int(str(cap)[len("triage.ctx."):]) if str(cap).startswith("triage.ctx.") else None
    except ValueError:
        return None


def unmet_requires(requires, have):
    """Same rule as the desk (scheduler.unmet_requires): a `triage.ctx.N` requirement is a MINIMUM
    window, met by any declared `triage.ctx.K` with K >= N; every other entry must match exactly."""
    windows = [w for w in (_ctx_of(c) for c in have) if w is not None]
    return [r for r in requires
            if r not in have and not (_ctx_of(r) is not None and any(w >= _ctx_of(r) for w in windows))]


# Do not monitor `var\state` for change detection; it is updated continuously by
# background processes (stall-rescue daemon, render worker) regardless of actual work.
# Only `var\work` and `jobs\archive` reflect true factory output.
SHOPFLOOR = [os.path.join(P.VAR, "work"), P.JOB_ARCHIVE]
PATROL_LEDGER = os.path.join(P.STATE_DIR, "patrol_ledger.json")
# How to get the factory to DO things. Reading is not on this list because reading needs no
# permission -- the observation deck is open (see patrol's docstring on where the line is).
CUSTOMER_SURFACES = {
    "submit an order":      P.QUEUE + "  (drop a .job.json)",
    "change a running order": P.QUEUE + "  (drop a .ctl.json at the desk)",
    "collect results":      "whatever the order declared in deliver_to",
    "ask for something new": "declare it in `requires` -- an unbuildable order is HELD and named",
}


def patrol(log_line, factory_worked=True):
    """Monitor the shop floor for unauthorized modifications.

    Reports file changes (writes, renames, deletes) that occurred while the factory was idle,
    indicating manual intervention or external tooling bypassing the build process.

    Key constraints:
        - `factory_worked` determines the reporting window. Changes during active factory runs are
          silenced to prevent false positives from legitimate build artifacts.
        - Report-only design: Does not block, quarantine, or delete files. Human judgment is required
          for remediation.
        - Read operations are ignored as they are generally indistinguishable from normal observation."""
    try:
        with open(PATROL_LEDGER, encoding="utf-8") as f:
            led = json.load(f)
    except Exception:
        led = {}
    seen, foreign = {}, []
    for base in SHOPFLOOR:
        if not os.path.isdir(base):
            continue
        for root, _d, files in os.walk(base):
            for fn in files:
                p = os.path.join(root, fn)
                if os.path.normcase(p) in PATROL_IGNORE:
                    continue          # the ledger is the patrol's OWN writing; the pause/stop
                    # flags are the documented customer mechanisms (see PauseRequested). A guard
                    # that reports its own file and the operator's sanctioned controls is noise.
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                key = p.lower()
                seen[key] = "%d:%d" % (int(st.st_mtime), st.st_size)
    if not led or factory_worked:
        # ADOPT, DO NOT ACCUSE. First pass ever, or the factory did work since the last pass --
        # in which case these artifacts are its own and reporting them is the 847-false-positive
        # bug. Idleness is measured by the order taker and passed in; it is never assumed here.
        _patrol_save(seen)
        return []
    for key, sig in seen.items():
        prior = led.get(key)
        if prior is None:
            foreign.append((key, "appeared while the factory was idle"))
        elif prior != sig:
            foreign.append((key, "modified while the factory was idle"))
    for key in led:
        if key not in seen:
            foreign.append((key, "REMOVED while the factory was idle"))
    _patrol_save(seen)
    if foreign:
        head = foreign[:8]
        log_line("PATROL: %d shop-floor change(s) the factory did not make" % len(foreign))
        for p, why in head:
            log_line("   TRESPASS %s  (%s)" % (p, why))
        notify_desk(
            "TRESPASS - %d change(s) on the shop floor the factory did not make:\n%s%s"
            % (len(foreign),
               "\n".join("  %s  (%s)" % (p, w) for p, w in head),
               "\n  ...and %d more" % (len(foreign) - len(head)) if len(foreign) > len(head) else ""))
    return foreign


# Never policed: the patrol's own ledger, and the operator control flags, which are the DOCUMENTED
# customer mechanisms (PauseRequested's docstring literally instructs `type nul > STOP_AFTER_RENDER`).
# Policing the controls you told the customer to use is how a guard trains people to ignore it.
PATROL_IGNORE = frozenset(os.path.normcase(p) for p in (
    PATROL_LEDGER, PATROL_LEDGER + ".tmp", PAUSE_FLAG, STOP_NOW))


def _patrol_save(seen):
    try:
        os.makedirs(os.path.dirname(PATROL_LEDGER), exist_ok=True)
        tmp = PATROL_LEDGER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(seen, f)
        os.replace(tmp, PATROL_LEDGER)
    except OSError:
        pass


# Reuse `patrol()` to detect unregistered AI jobs, avoiding a second implementation
# that could drift from existing safety invariants (never police own bookkeeping or operator controls).
#
# Unregistered GPU jobs are invisible to other guards, causing two critical failures:
#   1. The stall daemon treats them as idle and fires burst probes, degrading performance.
#   2. Probes report low throughput, making the guard misdiagnose healthy cards as dying.
#
# Use positive attribution (PID, parent, cmdline) to verify factory ownership.
# Never regress to age heuristics, which caused high false positives.

# Operator controls. DOCUMENTED, therefore NEVER POLICED -- the same rule that keeps
# STOP_AFTER_RENDER off the file patrol's beat. Policing the controls you told the customer to use
# is how a guard trains its operator to ignore it.
# ONE constant, used by both the PowerShell that EMITS rows and the Python that PARSES them, so
# the two can never disagree. Deliberately a plain multi-character ASCII token: no backslash, no
# backtick, nothing a shell or a quoting layer will rewrite on the way through -- and long enough
# that a real command line will not contain it by accident.
GPU_PROC_SEP = "@#@"

# The patrol's own bookkeeping -- and therefore NEVER policed by it (same rule as the file
# patrol's ledger). Holds the last offender set that was actually DM'd, so a standing condition
# alerts once instead of once per poll.
GPU_ALERT_LEDGER = os.path.join(P.STATE_DIR, "gpu_patrol_alerted.json")

GPU_SHORE_LEAVE = os.path.join(P.STATE_DIR, "GPU_SHORE_LEAVE")   # "the card is mine right now"
GPU_NO_ARREST = os.path.join(P.STATE_DIR, "GPU_NO_ARREST")       # alarm only, never kill

# Signature matching is the only fleet-wide method for detecting AI workloads
# (nvidia-smi cannot enumerate per-process VRAM on AMD hardware).
#
# This list is deliberately narrow. Adding browsers, games, or editors is a foot-gun:
# the patrol can arrest processes, so precision prevents killing legitimate non-AI tools.
GPU_WORKLOAD_SIGNS = (
    "ai-toolkit", "comfyui", "ollama", "llama-server", "llama_server",
    "sd-scripts", "kohya", "torchrun", "accelerate launch",
    "stable-diffusion", "text-generation-webui", "vllm",
)

# Strict separation between detection and action to prevent false-positive kills.
# BROAD signatures (above GPU_WORKLOAD_SIGNS) trigger REPORTS only.
# NARROW signatures below trigger ARREST (kill), reserved for verified compute entrypoints
# that directly consume GPU resources, excluding monitors or admin scripts.
GPU_ARREST_SIGNS = (
    "run.py",                  # ai-toolkit / sd-scripts trainer entrypoint
    "main.py",                 # ComfyUI server entrypoint
    "llama-server", "llama_server", "ollama_llama_server",   # inference runner binaries
    "torchrun", "accelerate launch",
    "train_network", "train_db", "sdxl_train",
)

# Lab infrastructure that is neither this factory's nor an order's, but is legitimately ours:
# the orchestrator/HAI's own scripts and watchdogs. Named explicitly rather than left to the
# arrest bar alone, so they are not even reported as unaccounted for.
LAB_INFRA_SIGNS = (
    r"programdata\hai",        # HAI's scripts (ollama-watchdog, keep-awake, model merge)
    "ollama-watchdog", "textinput-watchdog", "keep-awake",
    "orchestrator", "glances",
)

# Processes owned by this factory. Note: p20_rescue_burst must not be arrested,
# as it is critical for the rescue mechanism's operation.
FACTORY_OWNED_SIGNS = (
    "p20_foundry.py",         # the order taker / supervisor itself
    "p20_stall_rescue.py",    # the rescue daemon
    "p20_rescue_burst.py",
    "p20_render_worker.py",   # the render worker
    "p20_preview_render",     # the render path the foundry drives
    "p20_framing.py",         # keypoint framing, runs before the VLM
    "p20_dataset.py",
    "p20_bake_cond.py",
    # the factory's own deploy gate (run_foundry_suite.cmd runs it from the training venv); path-qualified to
    # THIS factory's tests dir so a same-named script elsewhere is not exempted
    os.path.join(P.FOUNDRY_ROOT, "tests", "test_foundry.py").lower(),
)

# Fleet infrastructure the SCHEDULER places work onto, rather than the order desk. The ollama
# server is a long-lived service; it is not itself an unregistered job.
ORCHESTRATOR_OWNED_SIGNS = ("ollama.exe serve", "ollama serve", "ollama app.exe")


def _current_run_claim():
    """Return the stated claim from the current run card, or {} if absent/unreadable.

    This function returns an empty dict on any read error to ensure safety: failure
    to parse prevents treating the run as 'foreign'. This is the opposite of the
    launch guard, where uncertainty must not be treated as safe/free."""
    try:
        with open(CURRENT_RUN, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _gpu_processes():
    """Every AI/GPU-workload process on THIS box, or None if we could not look.

    None is not an empty list, and the difference is the entire point: "nothing is running" and
    "I could not tell" must never render as the same verdict."""
    # PowerShell single-quoted strings do not process escape sequences (e.g., `\t` becomes literal backtick+t).
    # Hence GPU_PROC_SEP: a shared constant for emitter and parser to prevent delimiter mismatch.
    ps = ("Get-CimInstance Win32_Process | Where-Object { $null -ne $_.CommandLine } | "
          "ForEach-Object { '{0}%s{1}%s{2}' -f $_.ProcessId, $_.ParentProcessId, "
          "$_.CommandLine }" % (GPU_PROC_SEP, GPU_PROC_SEP))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    if not (r.stdout or "").strip():
        return None
    return [p for p in (_parse_proc_line(l) for l in r.stdout.splitlines()) if p]


def _norm_cmd(cmd):
    """Normalize command line for signature matching.

        Strips quotes and collapses whitespace to ensure consistent matching
        across environments where paths may be quoted or contain extra spaces.
    """
    return " ".join((cmd or "").lower().replace('"', "").replace("'", "").split())


def _parse_proc_line(line):
    """One `pid<SEP>ppid<SEP>commandline` row -> a dict, if it is an AI workload."""
    parts = line.split(GPU_PROC_SEP)
    if len(parts) < 3:
        return None
    try:
        pid, ppid = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    cmd = GPU_PROC_SEP.join(parts[2:]).strip()
    if not _is_ai_workload(cmd):
        return None
    return {"pid": pid, "ppid": ppid, "cmd": cmd}


PS_EXCLUDE_OBSERVER = "$_.ProcessId -ne $PID -and $_.ParentProcessId -ne $PID"


def ps_process_query(where, select="$_.ProcessId, $_.ParentProcessId, $_.CommandLine",
                     sep=None, timeout=60):
    """Run a Win32_Process query with the observer (this script) excluded by construction.

        The PowerShell process running the query matches its own CommandLine, causing self-referential hits
        (e.g., `pkill -f` killing itself). This function unconditionally ANDs an exclusion filter for the current
        PID and ParentPID to prevent this.

        Args:
            where: Caller's predicate string.
            select: Fields to return.
            sep: Output separator.
            timeout: Command timeout in seconds.

        Returns:
            Raw stdout lines, or None if the query failed (distinguishing failure from 'no matches').
    """
    sep = sep or GPU_PROC_SEP
    fmt = "'{0}%s{1}%s{2}' -f %s" % (sep, sep, select)
    ps = ("Get-CimInstance Win32_Process | Where-Object { %s -and (%s) } | ForEach-Object { %s }"
          % (PS_EXCLUDE_OBSERVER, where, fmt))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return [l for l in (r.stdout or "").splitlines() if l.strip()]


def _gpu_resident_pids():
    """Return PIDs currently holding a GPU context, or None if undetermined.

    Note: None is returned both when the card is likely idle (no PIDs found) and when
    detection failed (missing tools, driver error). Callers MUST NOT treat None as "idle"
    to discard reports; use only to downgrade candidates with other positive evidence."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return None                      # no nvidia-smi (AMD), or it hung -- cannot tell
    if r.returncode != 0:
        return None
    pids = set()
    for line in (r.stdout or "").splitlines():
        line = line.strip().split(",")[0].strip()
        if line.isdigit():
            pids.add(int(line))
    return pids or None                  # EMPTY is "could not tell", never "nothing is running"


INLINE_CODE_SWITCHES = ("-command", "-c", "-encodedcommand", "-ec")


def _identity_of(cmd):
    """Return the process identity: executable and script entrypoint, excluding arguments.
    Inline code (e.g., powershell -Command) is data, not an entrypoint, so it is omitted."""
    raw = (cmd or "").strip()
    if raw.startswith('"'):                             # a quoted image path may contain spaces
        end = raw.find('"', 1)
        image, rest = (raw[1:end], raw[end + 1:]) if end > 0 else (raw[1:], "")
    else:
        image, _, rest = raw.partition(" ")
    image = _norm_cmd(image)
    if not image:
        return ""
    parts = [image]
    for t in _norm_cmd(rest).split():
        if t in INLINE_CODE_SWITCHES:
            break
        if t.endswith(SCRIPT_SIGN_SUFFIXES) and not t.startswith(("-", "|")):
            parts.append(t)                             # the entrypoint only: later scripts are its arguments
            break
    return " ".join(parts)


# Only the first script token (the entrypoint) defines identity; subsequent .py arguments are just data and must not match signatures.
SCRIPT_SIGN_SUFFIXES = (".py", ".bat", ".cmd", ".ps1", ".sh")


def _sig_hit(ident, signs):
    """Return the matching signature from `signs`, or None.

    Scripts (ending in SCRIPT_SIGN_SUFFIXES) must match the basename of the
    **first** script token only. This avoids false positives from substring
    matches (e.g., "run.py" inside "sudo_run.py") and arguments (e.g., passing
    a script name as an argument to another).

    Binaries or directories without suffixes use substring matching against
    the full identity, as they may appear in path components."""
    toks = ident.split()
    scripts = [t for t in toks if t.endswith(SCRIPT_SIGN_SUFFIXES)]
    entry = scripts[0].replace("/", "\\").rsplit("\\", 1)[-1] if scripts else ""
    for s in signs:
        if s.endswith(SCRIPT_SIGN_SUFFIXES):
            if s == entry:
                return s
        elif s in ident:
            return s
    return None


def _is_ai_workload(cmd):
    """Is this process an AI workload?

    Match on identity (executable + script), never on command-line arguments or paths.
    This prevents false positives where non-AI tools merely reference AI-related
    paths (e.g., a downloader writing to a model folder).

    Candidacy checks both GPU_WORKLOAD_SIGNS and GPU_ARREST_SIGNS. Entrypoints
    must be part of candidacy, not just arrest, to catch relative-path launches.
    """
    if _ollama_store_admin(cmd):
        return False
    return _sig_hit(_identity_of(cmd), GPU_WORKLOAD_SIGNS + GPU_ARREST_SIGNS) is not None


# ollama's model-STORE client commands move files and metadata; the server does any GPU work, and a model it
# loads is the fleet service's (ORCHESTRATOR_OWNED). `run` is deliberately absent: it puts a model on the card.
OLLAMA_STORE_SUBCOMMANDS = ("create", "pull", "push", "rm", "cp", "list", "ls", "show", "ps")


def _ollama_store_admin(cmd):
    toks = _norm_cmd(cmd).split()
    for i, t in enumerate(toks):
        if t == "ollama" or t.endswith(("\\ollama.exe", "/ollama.exe")) or t == "ollama.exe":
            return i + 1 < len(toks) and toks[i + 1] in OLLAMA_STORE_SUBCOMMANDS
        if t.endswith(".exe"):
            return False
    return False


def _owned_ancestor(proc, by_pid, max_depth=4):
    """Is this process a CHILD of something already exempt? Returns (owner, why) or (None, "").

    Note: `by_pid` only contains processes classified as AI workloads. If an exempt ancestor is
    separated by a non-AI intermediate process, it will not be found. Such cases fall through to
    normal rules rather than being silently exempted."""
    if not by_pid:
        return None, ""
    seen = set()
    cur = by_pid.get(proc.get("ppid"))
    depth = 0
    while cur is not None and depth < max_depth and cur.get("pid") not in seen:
        seen.add(cur.get("pid"))
        low = _norm_cmd(cur.get("cmd", ""))
        if any(s in low for s in ORCHESTRATOR_OWNED_SIGNS):
            return "orchestrator", ("child (pid %s) of the fleet inference service -- ollama forks "
                                    "one model server per model" % proc.get("pid"))
        if any(s in low for s in FACTORY_OWNED_SIGNS):
            return "factory", "child (pid %s) of a component this factory runs" % proc.get("pid")
        cur = by_pid.get(cur.get("ppid"))
        depth += 1
    return None, ""


def gpu_owner(proc, own_pid=None, claim=None, by_pid=None):
    """Identify the legitimate owner of a GPU process.

    Returns (owner, reason). owner is None if unregistered.

    Critical: The observer must be excluded first. Since the patrol's own command line contains
    every signature it searches for, failing to exclude `own_pid` causes self-arrest."""
    own_pid = os.getpid() if own_pid is None else own_pid
    low = _norm_cmd(proc["cmd"])
    if proc["pid"] == own_pid or proc["ppid"] == own_pid:
        return "police", "the patrol's own query (observer excluded)"
    if any(s in low for s in FACTORY_OWNED_SIGNS):
        return "factory", "a component this factory runs"
    if any(s in low for s in ORCHESTRATOR_OWNED_SIGNS):
        return "orchestrator", "fleet inference service the scheduler places onto"
    anc_owner, anc_why = _owned_ancestor(proc, by_pid)
    if anc_owner:
        return anc_owner, anc_why
    if any(s in low for s in LAB_INFRA_SIGNS):
        return "lab", "lab infrastructure (monitor/watchdog, not a compute job)"
    claim = _current_run_claim() if claim is None else claim
    name = (claim.get("name") or "").strip()
    if name and name.lower() in low:
        # The same identification `trainer_running()` uses: run.py + the job name matches ONLY
        # that order's trainer.
        return "factory", "the running order's own process (%s)" % name
    if _is_our_comfy(low):
        # ComfyUI is factory infrastructure, not an order child. The render worker drives
        # ComfyUI without a CURRENT_RUN claim; strict checking would falsely arrest valid renders.
        # Scoped to FOUNDRY_COMFY_LAUNCH directory to avoid blanket acceptance.
        # Trade-off: hand-run ComfyUI from the same dir is ignored (false negative) to prevent
        # false positives that break live rendering.
        return "factory", "ComfyUI, this factory's own render engine"
    return None, "no order claims it, no scheduler placement, no operator declaration"


def _is_our_comfy(low_cmd):
    """Is this the ComfyUI THIS factory launches? Matched on the configured launcher's directory,
    which is per-host via FOUNDRY_COMFY_LAUNCH -- never a hardcoded drive letter."""
    if "comfyui" not in low_cmd:
        return False
    for hint in (os.path.dirname(COMFY_LAUNCH), LORAS, RENDERS if "RENDERS" in globals() else ""):
        hint = (hint or "").strip().lower()
        # a bare drive root ("f:\") would match everything -- require a real directory component
        if len(hint) > 4 and hint in low_cmd:
            return True
    return "comfyui-rocm" in low_cmd


def gpu_patrol(log_line, arrest=True):
    """Police AI workloads on this factory's cards. Loud alarm + arrest for the unregistered.

    Constraints and limitations:
    - Safe on inconclusive reads: if enumeration fails, returns empty (no false positives/negatives).
    - Narrow scope: arrests only positively identified AI compute (see GPU_WORKLOAD_SIGNS).
    - Exemptions: never arrests factory/orchestrator components or anything during GPU_SHORE_LEAVE.
    - Known gap: cannot distinguish hand-loaded ollama models from scheduler-placed ones
      (both share the same server process; requires cross-checking /api/ps)."""
    if os.path.exists(GPU_SHORE_LEAVE):
        log_line("GPU PATROL: stood down - operator holds the card (GPU_SHORE_LEAVE)")
        return []

    procs = _gpu_processes()
    if procs is None:
        log_line("GPU PATROL: INCONCLUSIVE - could not enumerate processes; no verdict this pass")
        return []

    own, claim = os.getpid(), _current_run_claim()
    # Map pid -> proc to check arrest candidates against their ancestors.
    # Ollama forks a model server per model; without this, the parent is exempt
    # but the child doing the work is not.
    by_pid = {p["pid"]: p for p in procs if isinstance(p.get("pid"), int)}
    foreign = []
    for p in procs:
        owner, why = gpu_owner(p, own_pid=own, claim=claim, by_pid=by_pid)
        if owner is None:
            foreign.append(dict(p, why=why))

    # Verify GPU residency to avoid false positives from zombie processes.
    # _gpu_resident_pids() may return an empty list on non-NVIDIA hardware or if nvidia-smi fails;
    # treat this as ambiguous (fail open) rather than "no foreign processes".
    # Skip the call when `foreign` is empty to avoid unnecessary subprocess overhead.
    resident = _gpu_resident_pids() if foreign else None
    if resident and foreign:
        off_card = [p for p in foreign if p["pid"] not in resident]
        if off_card:
            for p in off_card:
                log_line("   not on the card, ignoring: pid=%s %s" % (p["pid"], p["cmd"][:100]))
            foreign = [p for p in foreign if p["pid"] in resident]

    if not foreign:
        return []

    arrest = arrest and not os.path.exists(GPU_NO_ARREST)
    log_line("!! GPU PATROL: %d UNREGISTERED AI JOB(S) ON THIS FACTORY'S CARD" % len(foreign))
    arrested, escaped, watched = [], [], []
    for p in foreign:
        _norm = _norm_cmd(p["cmd"])
        # Identity check only (not full cmd); see _sig_hit.
        # Must match candidacy logic to avoid tier disagreement.
        _ident = _identity_of(p["cmd"])
        _hit = _sig_hit(_ident, GPU_ARREST_SIGNS)
        chargeable = _hit is not None
        log_line("   UNREGISTERED pid=%s  %s%s"
                 % (p["pid"], "" if chargeable else "(report only) ", p["cmd"][:160]))
        # Log norm as repr to expose whitespace diffs causing signature mismatches. Do not truncate: the arrest must be diagnosable from this line alone.
        log_line("      norm=%r  ident=%r  matched=%r  chargeable=%s"
                 % (_norm, _ident, _hit, chargeable))
        if not arrest or not chargeable:
            # Reported, not arrested: it is AI-adjacent but not a compute entrypoint. See the note
            # on GPU_ARREST_SIGNS -- this branch exists because the alternative killed a watchdog.
            watched.append(p)
            continue
        try:
            k = subprocess.run(["taskkill", "/F", "/T", "/PID", str(p["pid"])],
                               capture_output=True, text=True, timeout=60)
            out = ((k.stdout or "") + (k.stderr or "")).strip()
            if k.returncode == 0:
                arrested.append(p)
            elif "not found" in out.lower() or k.returncode == 128:
                # Return code 128 or 'not found' means the process was already killed by a parent's taskkill /T.
                arrested.append(p)
                log_line("   already gone pid=%s (killed with its parent's tree)" % p["pid"])
            else:
                escaped.append(p)
                log_line("   ARREST FAILED pid=%s rc=%s %s" % (p["pid"], k.returncode, out[:120]))
        except Exception as _e:
            escaped.append(p)
            log_line("   ARREST ERROR pid=%s (%s)" % (p["pid"], type(_e).__name__))

    if not arrest:
        verdict = "NOT arrested - GPU_NO_ARREST is set (alarm-only mode)"
    else:
        verdict = "ARRESTED %d, %d resisted, %d reported only" % (len(arrested), len(escaped),
                                                                 len(watched))
    body = "\n".join("  pid %s  %s\n     (%s)" % (p["pid"], p["cmd"][:150], p["why"])
                     for p in foreign[:6])

    # Alert only when the set of offenders changes, not on every pass.
    # `notify_desk` lacks deduplication; without this guard, a stable fault
    # triggers a flood of DMs (one per cycle). Logs still record every event.
    # The signature list is secondary; this check prevents alert fatigue.
    _key = sorted("%s|%s" % (p["pid"], _norm_cmd(p["cmd"])[:120]) for p in foreign)
    _prev = []
    try:
        with open(GPU_ALERT_LEDGER, encoding="utf-8") as f:
            _prev = json.load(f) or []
    except Exception:
        _prev = []
    if _key == _prev:
        log_line("   (same offender set as last pass - already reported, not re-alerting)")
        return foreign
    try:
        os.makedirs(os.path.dirname(GPU_ALERT_LEDGER), exist_ok=True)
        with open(GPU_ALERT_LEDGER, "w", encoding="utf-8") as f:
            json.dump(_key, f)
    except OSError:
        pass

    notify_desk(
        "!! UNREGISTERED AI JOB ON THE CARD (%s) - %d found, %s\n%s%s\n\n"
        "Nothing placed this through the order desk or the fleet scheduler. An unregistered GPU "
        "job is invisible to the stall daemon -- on 2026-08-06 that got one barraged to 15 s/it "
        "while making the daemon read a healthy card as dying.\n"
        "Submit it as an order, or claim the card: type nul > %s\n"
        "(You get ONE alert per offender set, not one per poll.)"
        % (_host_tag(), len(foreign), verdict, body,
           "\n  ...and %d more" % (len(foreign) - 6) if len(foreign) > 6 else "",
           GPU_SHORE_LEAVE))
    return foreign


def held_marker(spec_path):
    return spec_path[:-len(".json")] + ".held.json" if spec_path.endswith(".json") \
        else spec_path + ".held.json"


# A compute payload that exits REFUSED_RC refused on a precondition of THIS run (card occupied, model
# not resident), not on the order: the order stays queued, steps aside for REFUSED_RETRY_S so the orders
# behind it run, then is offered again. The marker is a `<name>.job.*` sibling, so retirement sweeps it.
REFUSED_RC = 2
REFUSED_RETRY_S = 600
REFUSED_DM_EVERY = 6


def refused_marker(spec_path):
    return spec_path[:-len(".json")] + ".refused.json" if spec_path.endswith(".json") \
        else spec_path + ".refused.json"


def refusal_cooling(spec_path, now=None):
    """True while a refused order is still inside its step-aside window."""
    try:
        with open(refused_marker(spec_path), encoding="utf-8") as f:
            until = float(json.load(f).get("until") or 0)
    except FileNotFoundError:
        return False
    except Exception:
        return False                      # an unreadable marker must not hide an order forever
    return (now if now is not None else time.time()) < until


def record_refusal(spec_path, why, now=None):
    """Write/advance the refusal marker; returns the refusal count for this order."""
    now = now if now is not None else time.time()
    p = refused_marker(spec_path)
    try:
        with open(p, encoding="utf-8") as f:
            count = int(json.load(f).get("count") or 0)
    except Exception:
        count = 0
    count += 1
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"count": count, "until": now + REFUSED_RETRY_S, "why": str(why)[:300],
                   "at": datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds")}, f)
    os.replace(tmp, p)
    return count


def spec_defects(spec_path):
    """Detect spec fields that are accepted but silently ignored by the runtime.

    Complements `assess_capability` (which checks if the factory can BUILD the order)
    by checking if the order's fields actually DO anything. Without this, a spec can
    set parameters that render zero images or skip milestones, reporting success while
    the experiment never ran.

    Rules for checks:
    1. Only flag fields provably ineffective (certainty-required).
    2. Never flag merely odd values or style opinions.
    3. Avoid false positives to prevent guard fatigue.

    Returns a list of plain-English defect strings.
    """
    try:
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
    except Exception:
        return []          # unreadable is a DIFFERENT failure, already handled at load

    mode = (spec.get("mode") or "train").strip().lower()
    out = []

    prompts = spec.get("probe_prompts") or []
    names = {p.get("name") for p in prompts if isinstance(p, dict)}
    sweep = list(spec.get("sweep_names") or [])
    strengths = list(spec.get("probe_strengths") or [])
    seeds = list(spec.get("probe_seeds") or [])
    has_base = any((p.get("base", True) if isinstance(p, dict) else True) for p in prompts)

    if mode == "probe":
        if not prompts:
            out.append("mode is 'probe' but probe_prompts is empty - there is nothing to render.")
        if strengths and not sweep:
            out.append(
                "probe_strengths %s is set but sweep_names is empty, so the strength sweep renders "
                "NOTHING and every image lands at the default strength. List the prompt names you "
                "want swept in sweep_names (all of them, if that is what you mean)." % strengths)
        unknown = [n for n in sweep if n not in names]
        if unknown:
            out.append(
                "sweep_names refers to prompt(s) %s that do not exist in probe_prompts %s - a "
                "typo here silently renders no sweep at all." % (unknown, sorted(n for n in names)))
        if seeds and not sweep and not has_base:
            out.append(
                "probe_seeds %s is set, but with no sweep_names and every prompt at base:false the "
                "seeds are never used - the adapter pass renders once at the default seed." % seeds)
        # render_key() is content-addressed: duplicate prompt texts at the same seed
        # resolve to one cached cell (possibly from an earlier order), not new draws.
        # If multiple distinct images are needed, a sweep over seeds must be provided.
        texts = [(p.get("text") or "").strip() for p in prompts if isinstance(p, dict)]
        dupes = sorted({t for t in texts if t and texts.count(t) > 1})
        if dupes and len(seeds) < 2:
            out.append(
                "probe_prompts repeats %d prompt text(s) (%d entries -> %d distinct) but probe_seeds "
                "is %s, so every repeat is the SAME content-addressed cell and renders ONCE - the "
                "extra entries are served from cache, possibly from an earlier order. If you meant "
                "several DRAWS, set probe_seeds to one seed per draw (e.g. [42,43,44,45]); if you "
                "meant one image, delete the duplicates. First repeated: %r"
                % (len(dupes), len(texts), len(set(texts)), seeds or "[]", dupes[0][:60]))

    if mode == "train":
        if not (spec.get("launcher") or "").strip():
            out.append("mode is 'train' but no launcher is set - nothing would start the trainer.")
        total = spec.get("total_steps")
        try:
            total = int(total) if total is not None else None
        except (TypeError, ValueError):
            total = None
        if total:
            late = [int(x) for x in (spec.get("ab_at") or []) if int(x) >= total]
            if late:
                out.append(
                    "ab_at %s is at or beyond total_steps %d - the completion check fires first, so "
                    "those A/B renders are silently skipped (this cost the whole 19-render A/B on "
                    "2026-07-27)." % (late, total))
            lateyield = [int(x) for x in (spec.get("yield_at") or []) if int(x) >= total]
            if lateyield:
                out.append(
                    "yield_at %s is at or beyond total_steps %d - the run ends before it could ever "
                    "lend the lane." % (lateyield, total))
        # Explicit 'live' with empty prompts is an error; omission (default) is not.
        if str(spec.get("previews") or "").strip().lower() == "live" \
                and not (spec.get("preview_prompts") or []):
            out.append(
                "previews is explicitly 'live' but preview_prompts is empty - nothing will render "
                "at the milestones. Add prompts, or set previews to 'none' to say so on purpose.")

    if mode in ("compute", "cpu"):     # "cpu" = legacy alias for the compute order kind
        # same token expansion the runner applies — a tokenized payload must not read as missing
        # here, or intake archives a good order over a path the runner would have resolved fine
        payload = _expand_compute_path((spec.get("payload") or "").strip())
        if not payload:
            out.append("mode is 'compute' but no 'payload' (a .py to run) is set - nothing would run.")
        elif not os.path.exists(payload):
            out.append("mode is 'compute' payload does not exist on this box: %r" % payload)
        load = (spec.get("load") or "").strip().lower()
        if load and load not in ("gpu", "mixed", "cpu"):
            out.append("compute order 'load' must be gpu | mixed | cpu, got %r "
                       "(it classifies what the payload actually burns)." % load)

    return out


def assess_capability(spec_path, log_line):
    """Intake assessment. True = the factory can build this order.

    Writes/refreshes a `.held.json` beside a spec it cannot fulfil, naming the missing
    capabilities, and REMOVES it the moment they exist -- a held order releases itself, so
    "build the capability" is the whole fix with no second action needed."""
    missing = missing_capabilities(spec_path)
    # Distinguish capability gaps (factory missing) from spec errors (order malformed).
    # Separate reporting prevents ambiguous fixes: BUILD vs FIX.
    defects = spec_defects(spec_path)
    marker = held_marker(spec_path)
    if not missing and not defects:
        if os.path.exists(marker):
            _quiet_remove(marker)
            log_line("CAPABILITY/SPEC now clean -> releasing held order %s"
                     % os.path.basename(spec_path))
        return True
    if defects:
        # Loud, because this one is silent by nature: the order looks fine and would have completed.
        log_line("!! SPEC DEFECT in %s - HELD before it spends the card:"
                 % os.path.basename(spec_path))
        for d in defects:
            log_line("     %s" % d)
    body = {"order": os.path.basename(spec_path), "missing": missing, "defects": defects,
            "held_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "note": ("The factory cannot build this yet. Add the capability, deploy it, and this "
                     "order releases itself on the next pass. Nothing about the order is wrong."
                     if missing and not defects else
                     "This order sets field(s) that nothing will act on, so it would have run the "
                     "WRONG experiment and reported success. Fix the spec in place; the order "
                     "releases itself on the next pass.")}
    try:
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(body, f, indent=1)
    except OSError:
        pass
    return False


def assess_all_queued(log_line):
    """Assess EVERY spec in the queue, held ones included, and announce state changes once.

    Must sweep held orders too: `queued_orders()` hides them, so if assessment only ran over the
    visible list a held order could never be re-checked and would never release. Held-ness is a
    verdict the factory re-issues each pass, not a permanent label."""
    specs = []
    for ext in ("*.job.json", "*.job.yaml", "*.job.yml"):
        specs += glob.glob(os.path.join(QUEUE_DIR, ext))
    for p in sorted(specs):
        was_held = os.path.exists(held_marker(p))
        ok = assess_capability(p, log_line)
        if not ok and not was_held:
            # `assess_capability` returns False for either a capability gap or a spec defect.
            # Report the specific cause: gaps require deploying capabilities; defects require editing orders.
            miss = missing_capabilities(p)
            bad = spec_defects(p) if not miss else []
            if miss:
                log_line("CAPABILITY GAP -> HOLDING %s; this factory cannot do: %s"
                         % (os.path.basename(p), ", ".join(miss)))
                notify_desk("ORDER HELD — capability gap. %s asks for: %s\nThe order is untouched "
                            "and stays queued; nothing about it is wrong. Add the capability, "
                            "deploy it, and it releases itself on the next pass."
                            % (os.path.basename(p), ", ".join(miss)))
            elif bad:
                log_line("SPEC DEFECT -> HOLDING %s; the order sets fields nothing will act on: %s"
                         % (os.path.basename(p), "; ".join(bad)))
                notify_desk("ORDER HELD — spec defect. %s: %s\nThis one is fixed by EDITING THE "
                            "ORDER, not by adding a capability. It stays queued and untouched."
                            % (os.path.basename(p), "; ".join(bad)))
            else:
                # Neither measured: say exactly that rather than picking the scarier story.
                log_line("HOLDING %s - assessment refused it but named neither a missing capability "
                         "nor a spec defect; this is a bug in the assessment, not in the order"
                         % os.path.basename(p))
        elif ok and was_held:
            notify_desk("ORDER RELEASED — the capability it was waiting on now exists: %s"
                        % os.path.basename(p))


def order_after_exists(path):
    r"""Return the order's `after_exists` precondition path, or None.

    Fail-soft: returns None if the path is unreadable (the UNREADABLE-order
    archive path at pickup owns that failure, not scheduling).

    Note: The path is token-expanded. `{FOUNDRY_ROOT}`, `{OUTPUT}`, and
    `{MODEL_CACHE}` are expanded by `run_compute_order`, so paths never
    carry drive letters. This function must handle expanded paths; otherwise
    `os.path.exists()` would check literal tokens like "{{OUTPUT}}\...",
    which always fail, causing orders to become invisible and permanently
    stuck."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = (json.load(f).get("after_exists") or "").strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return _expand_compute_path(raw) or None
    except Exception:
        # Expansion must never make a gate vanish: an unexpandable path stays literal, which fails
        # CLOSED (the order waits) rather than releasing work on a path nobody verified.
        return raw


def queued_orders():
    """Pending orders are sorted by PRIORITY field first, then by oldest-first FIFO.

    An order is only offered if its `after_exists` checkpoint file exists. Orders
    missing their checkpoint remain invisible (not refused or archived) to prevent
    deadlocks where a priority head blocks the queue while waiting for I/O. This
    visibility check aligns with `yield_at` probes, ensuring they appear precisely
    when their dependencies are met."""
    got = []
    for ext in ("*.job.json", "*.job.yaml", "*.job.yml"):
        got += glob.glob(os.path.join(QUEUE_DIR, ext))
    ready = [p for p in got
             if not (order_after_exists(p) and not os.path.exists(order_after_exists(p)))]
    # HELD orders (capability gaps) are excluded like unmet after_exists:
    # they are not defective and must not block the priority queue head.
    # The order taker re-assesses them each pass; they reappear when
    # the factory can build them.
    ready = [p for p in ready if not os.path.exists(held_marker(p))]
    # A payload-refused order steps aside for its retry window (see REFUSED_RC), then reappears.
    ready = [p for p in ready if not refusal_cooling(p)]
    return sorted(ready, key=lambda p: (-order_priority(p), os.path.getmtime(p)))


def stop_order(job):
    r"""Handle a CANCEL-PRODUCTION order: end the run where asked and SHIP what exists.

        Normal orders (\*.job.json) are read only between runs, so they cannot stop a running job.
        This uses \*.stop.json files polled directly by the running job to bypass the queue.

        JSON shape (all fields optional except target-matching):
            {"target": "trainer_v8", "at_step": 1500, "why": "..."}
          target: omitted or matching job.name -> applies to this run
          at_step: omitted -> stop at next poll; set -> stop once reached
    """
    for p in sorted(glob.glob(os.path.join(QUEUE_DIR, "*.stop.json"))):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            # An unreadable stop order must not silently do nothing AND must not stop the wrong run.
            job.log("WARNING: unreadable stop order %s (%r) - ignoring it" % (os.path.basename(p), e))
            continue
        tgt = (d.get("target") or "").strip()
        if tgt and tgt != job.name:
            continue
        d["_path"] = p
        return d
    return None


RENDER_SLOW_TIER = P.RENDER_SLOW_TIER   # a setting; empty = finished renders stay in the foundry's own tree


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _drain_tree(src, dst):
    """Move every file src -> dst with per-file hash verification. Returns (moved, failed).

    A file only leaves the fast tier after its slow-tier copy hashes identical; a mismatch removes
    the bad copy and KEEPS the source. Fail-soft by contract: callers must treat any outcome as
    non-fatal -- renders left on F: cost space, a lost render costs the product.
    """
    moved = failed = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        for f in files:
            sp = os.path.join(root, f)
            dd = os.path.join(dst, rel) if rel != "." else dst
            dp = os.path.join(dd, f)
            try:
                os.makedirs(dd, exist_ok=True)
                want = _file_sha256(sp)
                shutil.copy2(sp, dp)
                if _file_sha256(dp) == want:
                    os.remove(sp)
                    moved += 1
                else:
                    try:
                        os.remove(dp)
                    except OSError:
                        pass
                    failed += 1
            except OSError:
                failed += 1
    # tidy emptied dirs, deepest first; the root goes too if everything moved
    for root, dirs, files in os.walk(src, topdown=False):
        try:
            os.rmdir(root)
        except OSError:
            pass
    return moved, failed


def drain_order_renders(order_name, log=None):
    """CLEANUP: move a finished order's render folder to the slow tier. Never raises."""
    src = os.path.join(P.RENDERS, order_name)
    if not os.path.isdir(src) or not RENDER_SLOW_TIER:
        return
    try:
        moved, failed = _drain_tree(src, os.path.join(RENDER_SLOW_TIER, order_name))
        if log:
            log("render drain: %d file(s) -> %s%s"
                % (moved, os.path.join(RENDER_SLOW_TIER, order_name),
                   (" (%d FAILED verification, left on F:)" % failed) if failed else ""))
    except Exception as e:
        if log:
            log("WARNING: render drain failed (%r) - files remain on the fast tier" % (e,))


def process_control_orders(log_line):
    """Process `*.ctl.json` control slips in the queue dir.

    These allow queue modification (requeue, pause, cancel, priority) without
    direct file-system surgery. Slips are read before orders are picked,
    processed oldest-first, and consumed regardless of success to avoid blocking -- except a cancel
    that must WAIT (its order still holds the card, or that cannot be told): that slip stays and is
    retried every pass, and `waiting_cancels()` keeps the picker from starting its order meanwhile.

    Uses `*.ctl.json` rather than the standard order format so the control 
    reader (this function) does not conflict with the order reader (`run_queue`).
    See `*.stop.json` for a similar sibling mechanism targeting running jobs."""
    arch = os.path.join(os.path.dirname(QUEUE_DIR), "archive")
    # Counted so the caller can tell the patrol that the FACTORY made these changes. Incremented
    # for every slip consumed, including ones that FAIL -- a failed cancel can still have moved a
    # file before raising, and "we touched the floor" is the question the patrol is asking, not
    # "did it succeed".
    actioned = 0
    for p in sorted(glob.glob(os.path.join(QUEUE_DIR, "*.ctl.json")), key=os.path.getmtime):
        name = os.path.basename(p)
        actioned += 1
        try:
            with open(p, encoding="utf-8") as f:
                req = json.load(f)
        except Exception as e:
            log_line("CONTROL %s UNREADABLE (%r) -> discarded" % (name, e))
            _quiet_remove(p)
            continue
        action = str(req.get("action") or "").strip().lower()
        target = str(req.get("order") or "").strip()
        keep = False
        try:
            if action == "pause":
                with open(PAUSE_FLAG, "w"):
                    pass
                log_line("CONTROL pause: flag set; the in-flight unit finishes, then a clean exit")
            elif action == "requeue":
                if not target:
                    raise ValueError("requeue needs an 'order' name")
                # Prevent automated requeue of cancelled orders: internal state checks cannot distinguish
                # between a stop-slip wedge and an explicit cancellation. Use `force` to override.
                _cx = os.path.join(arch, target + ".cancelled")
                if os.path.exists(_cx) and not req.get("force"):
                    _when = ""
                    try:
                        with open(_cx, encoding="utf-8") as f:
                            _when = (json.load(f).get("at") or "")
                    except Exception:
                        pass
                    raise RuntimeError(
                        "%r was CANCELLED by the operator%s -- refusing to requeue it. An "
                        "automated remedy must never undo a human cancel. To bring it back "
                        "deliberately, send the same slip with \"force\": true."
                        % (target, (" at " + _when) if _when else ""))
                if os.path.exists(_cx):
                    _quiet_remove(_cx)      # forced: the human has spoken, clear the tombstone
                    log_line("CONTROL requeue %s: FORCED past the operator cancel" % target)
                moved = []
                # NEWEST wins, and say which one was taken. Resolving `archive/<name>.job.json` by
                # bare name restored the order's FIRST retirement whenever it had run more than
                # once -- silently, because a stale spec looks exactly like a fresh one. Retirement
                # now always timestamps, and this reads every generation and picks the latest by
                # mtime (the glob still matches legacy bare names, so old archives requeue fine).
                for suffix in (".job.json", ".job.state.json"):
                    gens = glob.glob(os.path.join(arch, target + suffix + "*"))
                    if not gens:
                        continue
                    src = max(gens, key=os.path.getmtime)
                    shutil.copy2(src, os.path.join(QUEUE_DIR, target + suffix))
                    moved.append("%s (from %s, %d generation(s) on file)"
                                 % (suffix, os.path.basename(src), len(gens)))
                if not moved:
                    raise FileNotFoundError("no archived order named %r" % target)
                # The spec is copied, NOT moved: the archive keeps the evidence of what happened,
                # which is the whole point of having archived it in the first place.
                log_line("CONTROL requeue %s -> restored %s (archive copy kept as evidence)"
                         % (target, ", ".join(moved)))
            elif action == "cancel":
                if not target:
                    raise ValueError("cancel needs an 'order' name")
                spec = os.path.join(QUEUE_DIR, target + ".job.json")
                if not os.path.exists(spec):
                    # Prevent cancel from vanishing if order is temporarily archived during requeue.
                    # Tombstone the order so the requeue logic refuses to restore it.
                    _running, _why = _order_still_holds_card(target)
                    _gens = glob.glob(os.path.join(arch, target + ".job.json*"))
                    if not _running and _gens:
                        with open(os.path.join(arch, target + ".cancelled"), "w",
                                  encoding="utf-8") as f:
                            json.dump({"at": datetime.datetime.now().isoformat(),
                                       "why": str(req.get("why") or "")[:300]}, f)
                        log_line("CONTROL cancel %s: not queued (archived %d generation(s)) - "
                                 "recorded the cancel, so an automated requeue will NOT bring it "
                                 "back. Use \"force\": true to override." % (target, len(_gens)))
                        # the desk only sends a cancel for an order it still holds as claimed: close that record
                        try:
                            with open(max(_gens, key=os.path.getmtime), encoding="utf-8") as f:
                                _archived_spec = json.load(f)
                        except Exception:                                      # noqa: BLE001
                            _archived_spec = {}
                        _desk_report_cancel(_archived_spec, target, req.get("why"), log_line)
                        continue
                    raise FileNotFoundError(
                        "no QUEUED order named %r%s. If it is the RUNNING order, stop it with the "
                        "stop channel instead: drop an empty file at %s -- honoured at the next "
                        "render boundary (the render in flight always completes), and the order is "
                        "then archived as withdrawn while the queue behind it carries on."
                        % (target, (" (it still holds the card: %s)" % _why) if _running else "",
                           order_cancel_file(target)))
                # REFUSE if the order still owns the card.
                # Archiving while a trainer is alive (paused at render boundary or
                # finishing checkpoint exit) leaves an orphan process. Fail CLOSED:
                # unreadable claim counts as claimed.
                claimed, why = _order_still_holds_card(target)
                if claimed:
                    raise ControlWait("%s still holds the card (%s) -- the cancel waits and is "
                                      "retried every pass; the order is not started meanwhile" % (target, why))
                try:
                    with open(spec, encoding="utf-8") as f:
                        _cancelled_spec = json.load(f)
                except Exception:                                      # noqa: BLE001
                    _cancelled_spec = {}
                retire_order(spec)
                # Tombstone: records that a HUMAN ended this, so an automated requeue (the
                # demon's wedge remedy) cannot quietly undo it. See the requeue branch above.
                try:
                    with open(os.path.join(arch, target + ".cancelled"), "w",
                              encoding="utf-8") as f:
                        json.dump({"at": datetime.datetime.now().isoformat(),
                                   "why": str(req.get("why") or "")[:300]}, f)
                except OSError as e:
                    log_line("WARNING: cancel tombstone for %s not written (%r) - an automated "
                             "requeue could resurrect it" % (target, e))
                log_line("CONTROL cancel %s -> retired before it started" % target)
                # The cancel owns cleanup: the claim is stale (no trainer alive), so nothing else will clear it.
                try:
                    with open(CURRENT_RUN, encoding="utf-8") as f:
                        _claim = json.load(f).get("name")
                except Exception:                                      # noqa: BLE001
                    _claim = None
                if _claim == target:
                    clear_current_run(types.SimpleNamespace(
                        name=target, output_dir=_cancelled_spec.get("output_dir") or "", log=log_line))
                # Report the cancel as failed; reporting success would cause chained successors to lose their hold.
                _desk_report_cancel(_cancelled_spec, target, req.get("why"), log_line)
            elif action == "priority":
                if not target:
                    raise ValueError("priority needs an 'order' name")
                spec = os.path.join(QUEUE_DIR, target + ".job.json")
                with open(spec, encoding="utf-8") as f:
                    body = json.load(f)
                body["priority"] = float(req.get("value") or 0)
                tmp = spec + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(body, f, indent=1)
                os.replace(tmp, spec)
                log_line("CONTROL priority %s -> %s" % (target, body["priority"]))
            elif action == "reship":
                # a RETIRED order whose output never left the box (a share outage at ship time, before the
                # marker existed, or a marker a human removed): mark its kept output from the archived spec's
                # own deliver_to; reship_kept delivers it on the next pass the share answers
                if not target:
                    raise ValueError("reship needs an 'order' name")
                gens = glob.glob(os.path.join(arch, target + ".job.json*"))
                if not gens:
                    raise FileNotFoundError("no archived order named %r" % target)
                with open(max(gens, key=os.path.getmtime), encoding="utf-8") as f:
                    dest_root = _expand_compute_path((json.load(f).get("deliver_to") or "").strip())
                if not dest_root:
                    raise ValueError("%r declares no deliver_to - nothing to ship it to" % target)
                src = os.path.join(COMPUTE_OUT_ROOT, target)
                if not os.path.isdir(src) or not os.listdir(src):
                    raise FileNotFoundError("no kept output at %s" % src)
                with open(os.path.join(src, UNSHIPPED), "w", encoding="utf-8") as f:
                    json.dump({"name": target, "deliver_to": dest_root, "error": "reship slip: %s" % (req.get("why") or ""),
                               "at": datetime.datetime.now().isoformat(timespec="seconds")}, f, indent=1)
                log_line("CONTROL reship %s -> marked; delivers to %s on the next pass the share answers"
                         % (target, dest_root))
            else:
                raise ValueError("unknown action %r (pause|requeue|cancel|priority|reship)" % action)
        except ControlWait as e:
            keep = True
            if name not in _CONTROL_WAIT_LOGGED:
                _CONTROL_WAIT_LOGGED.add(name)
                log_line("CONTROL %s WAITING: %s" % (name, e))
        except Exception as e:
            log_line("CONTROL %s FAILED: %r" % (name, e))
        finally:
            if not keep:
                _quiet_remove(p)
                _CONTROL_WAIT_LOGGED.discard(name)
    return actioned


class ControlWait(RuntimeError):
    """A control that cannot be applied YET - a property of the moment, not of the slip."""


_CONTROL_WAIT_LOGGED = set()


def waiting_cancels():
    """{order name} that a cancel slip still waiting in the queue dir names: the picker skips them."""
    out = set()
    for p in glob.glob(os.path.join(QUEUE_DIR, "*.ctl.json")):
        try:
            with open(p, encoding="utf-8") as f:
                req = json.load(f)
        except Exception:                                                  # noqa: BLE001
            continue
        if str(req.get("action") or "").strip().lower() == "cancel" and str(req.get("order") or "").strip():
            out.add(str(req["order"]).strip())
    return out


def _queued_name(path):
    base = os.path.basename(path)
    for ext in (".job.json", ".job.yaml", ".job.yml"):
        if base.endswith(ext):
            return base[:-len(ext)]
    return base


def _queued_trainer_name(name):
    """The trainer name of a queued order (config.name of the config its launcher runs), or "" if unreadable."""
    try:
        with open(os.path.join(QUEUE_DIR, name + ".job.json"), encoding="utf-8") as f:
            spec = json.load(f)
        return trainer_name(types.SimpleNamespace(name=name, launcher=spec.get("launcher", "")))
    except Exception:                                                  # noqa: BLE001
        return ""


def _order_still_holds_card(name):
    """Return (True, reason) if `name` still owns the card.

    A fresh CURRENT_RUN claim holds the card. Stale claims are untrusted and deferred to OS checks to prevent deadlocks
    from orphaned state. Any live trainer matching the order name or config holds the card."""
    try:
        with open(CURRENT_RUN, encoding="utf-8") as f:
            cur = json.load(f)
        if cur.get("name") == name and str(cur.get("state", "")).lower() not in ("", "done") \
                and claim_age_s(cur) <= CLAIM_STALE_AFTER:
            return True, "CURRENT_RUN says state=%s" % cur.get("state")
    except FileNotFoundError:
        pass                      # absent = nothing stated = free (the documented reading)
    except Exception as e:
        return True, "CURRENT_RUN unreadable (%r) - refusing on the safe side" % (e,)
    tname = _queued_trainer_name(name)
    try:
        for _pid, cmd in python_cmdlines():
            if "run.py" in cmd and (name in cmd or (tname and tname in cmd)):
                return True, "a trainer process for it is still alive"
    except Exception as e:
        return True, "could not enumerate trainers (%r) - refusing on the safe side" % (e,)
    return False, ""


def _quiet_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def quick_preview_arch(job):
    """Return the architecture base for quick preview rendering, or None.

    Priority:
    1. `job.spec['quick_preview_arch']` if present.
    2. Inferred from trainer config's `arch` field (e.g., `flux2_klein_4b` -> `klein`).

    If neither is found or inferable, returns None. The caller must handle
    unrenderable architectures by holding the job rather than falling back
    to a default base."""
    explicit = (job.spec.get("quick_preview_arch") or "").strip()
    if explicit:
        return explicit
    cfg = (job.spec.get("config") or job.spec.get("launcher") or "")
    blob = ""
    for cand in (cfg, os.path.join(P.AI_TOOLKIT, "config", job.name + ".yaml")):
        try:
            if cand and os.path.isfile(cand):
                with open(cand, "r", encoding="utf-8", errors="replace") as f:
                    blob = f.read()
                break
        except OSError:
            continue
    m = re.search(r"arch:\s*([A-Za-z0-9_.-]+)", blob or "")
    if not m:
        return None
    arch = m.group(1).lower()
    if "klein" in arch:
        return "klein"
    if "flux2" in arch or "flux" in arch:
        return "flux2"
    return arch


def enqueue_quick_preview(job, step):
    """Enqueue checkpoint previews to an available render factory. Returns count enqueued.

        Non-blocking: uses existing PULL model (orcha holds job, worker polls). No direct peer-to-peer
    traffic or SSH keys required.

        Fail-soft: any placement error returns 0; caller continues training loop."""
    arch = quick_preview_arch(job)
    if not arch:
        job.log("quick previews: the run does not declare an architecture "
                "(quick_preview_arch, or `arch:` in its trainer config) - cannot route")
        return 0
    if not ORCH_BASE:
        _unlinked_note()
        return 0
    ck = checkpoints(job).get(step)
    if not ck:
        job.log("quick previews: no checkpoint file for step %d yet" % step)
        return 0

    sent = 0
    for p in job.preview_prompts:
        body = json.dumps({
            "kind": "quick_preview",
            "requires": ["render.%s" % arch],
            "run": job.name,
            "step": step,
            "arch": arch,
            "lora_path": ck,
            "prompt_name": p["name"],
            "prompt": p.get("text") or "",
            "strength": SHIP_STRENGTH,
            "approximate": True,
            "tag": QUICK_TAG,
            "caveat": ("APPROXIMATE PREVIEW - rendered on a %s base, not the model being trained. "
                       "Shows whether the concept is binding and whether pose/framing steer. "
                       "NOT valid evidence for electing a checkpoint." % arch),
        }).encode("utf-8")
        try:
            req = urllib.request.Request(ORCH_BASE + "/p20/quick-preview", data=body,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
            try:
                ans = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                ans = {}
            # Fleet endpoints return HTTP 200 even on logical failure; must check body 'ok' field.
            if isinstance(ans, dict) and ans.get("ok") is False:
                job.log("quick preview REFUSED for %s: %s" % (p["name"], ans.get("error")))
                continue
            sent += 1
        except Exception as e:
            job.log("quick preview could not be queued for %s (%s) - training continues"
                    % (p["name"], type(e).__name__))
    return sent


# State keys that count as "order produced something", per mode.
# Adding a new mode? Add its key here. `TestEveryModeIsCounted` reads this tuple
# and will fail until you do, preventing silent false-negatives in the ledger guard.
# Values may be a collection (counted by len) or a number (counted as itself).
SHIPPED_COUNT_KEYS = (
    "previews_done",        # train: preview rounds
    "ladder_done",          # train: finish-ladder rungs
    "probe_rendered",       # probe: renders produced (incl. index reuse — still this order's product)
    "captioned",            # caption: images captioned
    "delivered",            # compute: files shipped from {ORDER_OUT} to deliver_to (ship_compute)
)


def _order_shipped_count(job):
    """Count deliverables produced by this order. Returns 0 if nothing shipped.

    Derived from `job.state` keys (`previews_done`, `ladder_done`, checkpoints),
    not the spec's prose `deliverables` field, which is human-readable text."""
    n = 0
    try:
        st = job.state or {}
        # ONE DERIVATION: the keys live in SHIPPED_COUNT_KEYS above, and `TestEveryModeIsCounted`
        # reads that same constant. A test that grepped this function's source text instead would
        # be asserting how the code is SPELLED, and would break the moment someone tidied it while
        # the property still held.
        for _k in SHIPPED_COUNT_KEYS:
            _v = st.get(_k)
            if not _v:
                continue
            n += len(_v) if isinstance(_v, (list, tuple, dict, set)) else int(_v)
    except Exception:
        return 0
    if n:
        return n
    try:
        n += len(checkpoints(job))
    except Exception:
        pass
    if n:
        return n
    # Only count deliverables that resolve to existing paths on disk.
    # Render orders often use prose descriptions (e.g., "12 PNGs"), which
    # do not resolve to files. This avoids false negatives for successful
    # compute orders while preserving behavior for render-only jobs.
    try:
        for d in (job.spec.get("deliverables") or []):
            if not isinstance(d, str):
                continue
            p = _expand_compute_path(d)
            if p and os.path.exists(p):
                n += 1
    except Exception:
        pass
    return n


def _sweep_order_siblings(stem, spec_path, arch):
    """Move an order's byproducts (`<stem>.state.json`, `<stem>.foundry.log`, the fleet deal's
    `<order>.items.json`, and its hold markers `<stem>.refused.json` / `<stem>.held.json`) into the archive."""
    order = stem[:-len(".job")] if stem.endswith(".job") else stem
    for sib in (stem + ".state.json", stem + ".foundry.log", order + ".items.json",
                stem + ".refused.json", stem + ".held.json"):
        sp = os.path.join(os.path.dirname(spec_path), sib)
        if os.path.exists(sp):
            sd = os.path.join(arch, sib)
            if os.path.exists(sd):
                sd += "." + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.move(sp, sd)


def _retire_spent_deploy_stops(order, spec_path, arch):
    """Retire stop slips for this order to prevent them from halting the requeued run; customer slips are unaffected."""
    qdir = os.path.dirname(spec_path)
    for p in glob.glob(os.path.join(qdir, "fleet_deploy-*.stop.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                if (json.load(f).get("target") or "").strip() != order:
                    continue
        except Exception:                                            # noqa: BLE001 - unreadable: not ours to judge
            continue
        dst = os.path.join(arch, os.path.basename(p) + ".spent-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.move(p, dst)


def sweep_orphaned_sidecars(log):
    """Archive queue sidecars whose order spec is no longer queued.

    The queue directory must list PENDING WORK and nothing else. A sidecar whose spec is still
    queued belongs to pending work (paused/requeued orders stay queued with their state) and is
    left alone; one whose spec is gone is a finished order's litter and moves to the archive.
    Registration files, control slips and stop slips are not sidecars -- registrations await
    operator review, and slips are operator intent with no TTL."""
    moved = 0
    arch = os.path.join(os.path.dirname(QUEUE_DIR), "archive")
    os.makedirs(arch, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(QUEUE_DIR, "*"))):
        base = os.path.basename(path)
        order = None
        for suffix in (".job.state.json", ".job.foundry.log", ".items.json", ".job.refused.json",
                       ".job.held.json"):
            if base.endswith(suffix):
                order = base[:-len(suffix)]
                break
        if not order:
            continue
        if os.path.exists(os.path.join(QUEUE_DIR, order + ".job.json")):
            continue
        dst = os.path.join(arch, base)
        if os.path.exists(dst):
            dst += "." + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            shutil.move(path, dst)
            moved += 1
        except Exception:
            pass                      # a file something still holds open stays; the next start retries
    if moved:
        log("startup sweep: archived %d orphaned sidecar file(s) - the queue lists pending work only"
            % moved)
    return moved


def retire_order(spec_path):
    """Retire finished orders from the queue to prevent re-execution.

        Only terminal states reach this function:
        - DONE, FAILED, STOPPED, or CANCELLED orders are moved to archive.
        - PAUSED and REQUEUED orders stay in the queue; they never reach here
          because PauseRequested is handled upstream.

        All associated artifacts (state.json, foundry.log) are retired with the spec
    to prevent globbing stale files. Registration artifacts remain in the queue
    for operator review."""
    arch = os.path.join(os.path.dirname(QUEUE_DIR), "archive")
    try:
        os.makedirs(arch, exist_ok=True)
        # Always timestamped: no bare canonical name exists in the archive.
        # This prevents the "first-wins" trap where requeueing a job silently
        # resurrects stale state from an earlier run with the same name.
        dst = os.path.join(arch, os.path.basename(spec_path)
                           + "." + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        while os.path.exists(dst):
            dst += "a"                      # same-second retirement of the same name
        shutil.move(spec_path, dst)
        base = os.path.basename(spec_path)
        if base.endswith(".job.json"):
            # Cleanup here ensures all paths (success/fail/archive) trigger it; fail-soft drain
            # to avoid blocking retirement on cleanup errors.
            drain_order_renders(base[:-len(".job.json")])
            _sweep_order_siblings(base[:-len(".json")], spec_path, arch)
            _retire_spent_deploy_stops(base[:-len(".job.json")], spec_path, arch)
        elif base.endswith(".stop.json"):
            # Stopped orders leave .job.byproducts (state/log) behind because
            # _sweep_order_siblings searches for <name>.stop.* which never exists.
            # Explicitly sweep the JOB byproducts to prevent queue litter.
            _sweep_order_siblings(base[:-len(".stop.json")] + ".job", spec_path, arch)
        return dst
    except Exception as exc:
        # Prevent infinite re-run loops: if archiving fails, rename the spec out of the *.job.json
        # glob immediately. queued_orders() matches on that suffix, so renaming (not deleting)
        # ensures a failed order cannot be picked up again.
        why = repr(exc)[:160]
        try:
            if os.path.exists(spec_path):
                parked = spec_path + ".RETIRED-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                os.replace(spec_path, parked)
                return "!archive failed (%s) - spec parked in place as %s so it cannot re-run" % (
                    why, os.path.basename(parked))
            # Nothing to move: the spec is already gone (deleted by hand, or retired by another
            # path). Not a re-run risk -- but writers that legitimately fire AFTER an in-run
            # retirement (the comfy-cleanup `finally` logs; the shipped-count bookkeeping saves
            # state) recreate the sidecars behind the first sweep, so sweep again here: a second
            # retire_order call must leave the queue as clean as the first.
            base = os.path.basename(spec_path)
            try:
                if base.endswith(".job.json"):
                    _sweep_order_siblings(base[:-len(".json")], spec_path, arch)
                elif base.endswith(".stop.json"):
                    _sweep_order_siblings(base[:-len(".stop.json")] + ".job", spec_path, arch)
            except Exception:
                pass
            return "!nothing to archive - spec already absent (%s)" % why
        except Exception as exc2:
            # Both failed. This IS a re-run risk and it must be shouted, not returned as "".
            return ("!! ARCHIVE FAILED AND THE SPEC COULD NOT BE PARKED (%s / %r). The order is "
                    "still in the queue and WILL RUN AGAIN on the next poll - remove it by hand."
                    % (why, exc2))


def release_lane_if_ours(name, log_line):
    """Ensure an order that has exited clears its lane claim.

    `clear_current_run()` is inside `run()` and misses early exits (exceptions,
    dead renders). This runs in the order taker, which sees all exit routes.
    Only clears if the claim matches this order to avoid releasing other jobs' lanes.
    Also clears unreadable claims to prevent permanent queue blocking by `foreign_run_guard`.
    """
    try:
        with open(CURRENT_RUN, "r", encoding="utf-8") as f:
            cur = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:
        log_line("CURRENT_RUN unreadable (%r) after %s ended -> clearing so it cannot pin the lane"
                 % (e, name))
        cur = None
    if cur is not None and (cur.get("name") or "") != name:
        return
    try:
        os.remove(CURRENT_RUN)
        log_line("released the lane: CURRENT_RUN still claimed %s after the order ended" % name)
    except FileNotFoundError:
        pass
    except Exception as e:
        log_line("WARNING: could not release the lane (%r)" % (e,))


def _worker_token():
    """This factory's fleet token, via the module's ONE etc/ env reader."""
    return _etc_env().get("P20_WORKER_TOKEN")


_FLEET_DESK_QUIET = [False]      # so a missing token complains ONCE, not every 30 seconds


def collect_fleet_orders(log_line):
    """Collect orders routed by the Fleet Desk into this factory's local queue.

    Constraints:
    - Pull only via GET /p20/jobs (polling) to respect existing endpoint/store/cap-filter reuse and claim logic.
    - Write specs atomically using os.replace with a temp file that lacks the .job.json extension until complete;
      partial writes are read as broken specs by assess_all_queued and get ARCHIVED.
    - CLAIM an order BEFORE writing its spec; if write fails, the claim prevents duplicate collection
      (avoiding 'two-trainers-on-one-card' collisions).
    - Fail-soft: network errors must not halt processing of locally queued orders."""
    token = _worker_token()
    if not ORCH_BASE:
        _unlinked_note()
        return 0
    if not token:
        if not _FLEET_DESK_QUIET[0]:
            _FLEET_DESK_QUIET[0] = True
            log_line("fleet desk: no P20_WORKER_TOKEN in %s - local queue only" % DM_ENV)
        return 0

    def _call(path, method="GET"):
        req = urllib.request.Request(ORCH_BASE + path, method=method,
                                     headers={"X-P20-Token": token})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    try:
        body = _call("/p20/jobs?host=%s" % FLEET_HOST)
    except Exception as e:
        log_line("fleet desk unreachable (%s) - working local queue" % type(e).__name__)
        return 0

    orders = [j for j in (body.get("jobs") or []) if j.get("kind") == "factory_order"]
    if not orders:
        return 0

    got = 0
    for j in orders:
        jid = j.get("id") or ""
        spec = j.get("spec") or {}
        # the SPEC's name first: it is the name the runner uses for ORDER_OUT, run state and slips; a late-routed desk
        # record's order_name lacks the box suffix the router gave the spec (10-11: one order, two names)
        name = str(spec.get("name") or j.get("order_name") or "").strip()
        if not jid or not name or not spec:
            log_line("fleet desk: skipping malformed order record %r" % (jid or "?",))
            continue
        try:
            claimed = _call("/p20/jobs/%s/claim?host=%s" % (jid, FLEET_HOST), method="POST")
        except Exception as e:
            log_line("fleet desk: claim failed for %s (%s)" % (name, type(e).__name__))
            continue
        if not claimed.get("ok"):
            continue                 # another factory got it first: normal, not an error

        # ONE NAME = ONE ARTIFACT. Never clobber a spec already sitting in the queue; disambiguate
        # with the desk's own id so "which generation?" stays a visible question.
        dest = os.path.join(QUEUE_DIR, "%s.job.json" % name)
        if os.path.exists(dest):
            dest = os.path.join(QUEUE_DIR, "%s-%s.job.json" % (name, jid[:6]))
        tmp = dest + ".incoming"     # deliberately NOT .job.json - see the atomicity note above
        try:
            spec = dict(spec)
            spec.setdefault("fleet_order_id", jid)      # provenance: which desk order became this
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(spec, f, indent=2)
            os.replace(tmp, dest)
        except Exception as e:
            log_line("fleet desk: could not stage %s (%r)" % (name, e))
            try:
                os.remove(tmp)
            except Exception:
                pass
            continue

        got += 1
        log_line("fleet desk: collected order %r -> %s" % (name, os.path.basename(dest)))
    return got


def collect_fleet_controls(log_line):
    """Pull controls from the fleet desk and materialise them as local slips.

    The factory has no inbound network access; it must PULL rather than receive PUSH.
    This function implements the same delivery pattern as `collect_fleet_orders`:

    1. Atomic writes only: Slips are written to temporary files then renamed to prevent
       the receptionist from globbing half-written JSON under live names (*.ctl.json or
       *.stop.json).
    2. Claim-before-write: Controls remain claimed while pending to prevent duplicate
       materialisation if the pull fails mid-stream.
    3. No third channel: Desk controls map directly to existing slip types:
       - 'stop' actions write <order>.stop.json (consumed by running jobs).
       - Other actions write fleet-<id>.ctl.json (consumed between orders).

    Safety constraints:
    - The `force` flag is stripped from all automated desk controls. Automated remedies
      cannot undo human cancellations (tombstones); forcing requires manual intervention.
    - Fail-soft: An unreachable desk never stops the factory loop.

    Returns:
        int: Number of slips written."""
    token = _worker_token()
    if not ORCH_BASE:
        _unlinked_note()
        return 0
    if not token:
        return 0

    def _call(path, method="GET"):
        req = urllib.request.Request(ORCH_BASE + path, method=method,
                                     headers={"X-P20-Token": token})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    try:
        body = _call("/p20/controls?host=%s" % FLEET_HOST)
    except Exception:
        return 0        # the orders pass logs desk unreachability; one complaint per cycle is enough

    wrote = 0
    for c in (body.get("controls") or []):
        cid = str(c.get("id") or "")
        action = str(c.get("action") or "").strip().lower()
        order = str(c.get("order") or "").strip()
        if not cid or not action or not order:
            log_line("fleet desk: skipping malformed control record %r" % (cid or "?",))
            continue
        try:
            claimed = _call("/p20/controls/%s/claim?host=%s" % (cid, FLEET_HOST), method="POST")
        except Exception as e:
            log_line("fleet desk: control claim failed for %s (%s)" % (cid, type(e).__name__))
            continue
        if not claimed.get("ok"):
            continue                 # already collected (or a retry of ourselves): normal, move on
        payload = dict(c.get("payload") or {})
        payload.pop("force", None)   # see docstring: the desk may never force past a human cancel
        if action == "stop":
            dest = order_cancel_file(order)
            slip = {"target": order, "why": str(payload.get("why") or ""),
                    "source": "fleet-desk control %s" % cid}
        else:
            dest = os.path.join(QUEUE_DIR, "fleet-%s.ctl.json" % cid)
            slip = {"action": action, "order": order, **payload}
        tmp = dest + ".incoming"     # deliberately NOT the live suffix — atomicity, as above
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(slip, f, indent=2)
            os.replace(tmp, dest)
        except Exception as e:
            log_line("fleet desk: could not write control slip %s (%r)" % (cid, e))
            try:
                os.remove(tmp)
            except Exception:
                pass
            continue
        wrote += 1
        log_line("fleet desk: consented control %s landed -> %s (%s %s)"
                 % (cid, os.path.basename(dest), action, order))
    return wrote


def _run_order_with_comfy_cleanup(job):
    """Run one order and ALWAYS leave the card without a ComfyUI on it.

        `stop_comfy` is called unconditionally in `finally` to prevent GPU/VRAM leaks from abnormal exits.
        It is a cheap no-op if already stopped, ensuring every exit path releases resources.

        Use `job.log` for reporting failures here, NOT `log_line`, because `log_line` is local to the
    caller and raising NameError would swallow the original exception."""
    try:
        run(job)
    finally:
        try:
            stop_comfy(job)
        except Exception as _e:
            try:
                job.log("comfy cleanup failed (%r) - continuing" % (_e,))
            except Exception:
                pass        # nothing in cleanup may replace the exception that is in flight


# Use the desk's 1-second window cadence (matching helper_window.POLL_S) rather than the
# 30-second full-pass interval to minimize delegation latency. The lightweight GET is
# cheap due to the desk's per-record cache (gate 2559).
QUICK_POLL_S = 1.0


def pass_report(marks):
    """Format pass duration and breakdown by step. `marks`: [("", t0), (step, t_end), ...]."""
    steps = sorted(((t - marks[i][1], name) for i, (name, t) in enumerate(marks[1:])), reverse=True)
    said = ", ".join("%s %.1f" % (name, s) for s, name in steps if s >= 0.1)
    return "pass %.1f s%s" % (marks[-1][1] - marks[0][1], (": " + said) if said else "")


def _idle_wait(poll, collect, sleep=None, clock=None):
    """Sleep up to `poll` seconds in QUICK_POLL_S steps, asking the desk after each; return early (True) as soon as
    `collect()` reports an order. A failing desk call is the full pass's problem, not this wait's."""
    sleep = sleep or time.sleep
    clock = clock or time.time
    t0 = clock()
    while True:
        left = poll - (clock() - t0)
        if left <= 0:
            return False
        sleep(min(QUICK_POLL_S, left))
        try:
            if collect():
                return True
        except Exception:                                                # noqa: BLE001
            pass


def run_queue(poll=30.0):
    """Process job queue serially.

    One GPU = one lane. Orders queue; they do not compete.
    Collision guards enforce single-instance access."""
    log_line = lambda m: print("%s  [QUEUE] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), m), flush=True)
    os.makedirs(QUEUE_DIR, exist_ok=True)
    if not acquire_runner_singleton(log_line):
        return
    busy_logged = {}          # If an order was collected in the immediate previous pass, skip the redundant desk query.
# This avoids duplicate requests within a short window; new arrivals are handled by the next pass.
    _just_collected = [False]
    _worked = [True]          # did the factory do work since the last patrol? starts True so the
    # first pass after a restart ADOPTS whatever the previous instance produced rather than
    # accusing a fresh runner of its predecessor's output.
    log_line("order taker up; watching %s" % QUEUE_DIR)
    try:
        sweep_orphaned_sidecars(log_line)
    except Exception as _e:
        log_line("startup sweep error (%r) - orders continue" % (_e,))
    while True:
        _pass = [("", time.time())]       # what this pass's seconds went on, said with the pickup it delayed (pass_report)
        try:
            # Cancel slips archive specs via retire_order. Since _worked is only set
            # when picking up orders, archived specs appear as TRESPASS (factory_worked=False)
            # unless processed here. Collect Tier B controls first so human-approved slips
            # are assessed in the same pass as intake.
            collect_fleet_controls(log_line)
            if process_control_orders(log_line):
                _worked[0] = True
        except Exception as _e:
            log_line("control desk error (%r) - orders continue" % (_e,))
        _pass.append(("controls", time.time()))
        try:
            if reship_kept(log_line):
                _worked[0] = True         # the factory moved its own deliverables
        except Exception as _e:
            log_line("reship error (%r) - orders continue" % (_e,))
        _pass.append(("reship", time.time()))
        try:
            resend_desk_reports(log_line)
        except Exception as _e:
            log_line("desk report resend error (%r) - orders continue" % (_e,))
        _pass.append(("resend", time.time()))
        try:
            if _just_collected[0]:
                _just_collected[0] = False
            else:
                collect_fleet_orders(log_line)
        except Exception as _e:
            log_line("fleet desk error (%r) - orders continue" % (_e,))
        _pass.append(("collect", time.time()))
        # INTAKE ASSESSMENT: can this factory build what has been ordered? Runs before anything is
        # picked up, and before the trespass sweep, because both are desk work rather than line
        # work. An order the factory cannot fulfil is HELD and named, never silently worked around.
        try:
            assess_all_queued(log_line)
        except Exception as _e:
            log_line("capability assessment error (%r) - orders continue" % (_e,))
        _pass.append(("assess", time.time()))
        try:
            # The order taker is the ONLY component that knows whether the factory did work since
            # the last pass, so it measures it and hands it over. `worked` is set whenever an order
            # is picked up below and cleared here once reported -- so the very pass after a run
            # adopts that run's artifacts instead of accusing them (the 847-false-positive bug).
            patrol(log_line, factory_worked=_worked[0])
            _worked[0] = False
        except Exception as _e:
            log_line("patrol error (%r) - orders continue" % (_e,))
        _pass.append(("patrol", time.time()))
        try:
            # GPU patrol in separate try/except: independent failure containment.
            # Not gated on `factory_worked`: foreign GPU jobs can collide even while busy.
            gpu_patrol(log_line)
        except Exception as _e:
            log_line("gpu patrol error (%s) - orders continue" % (type(_e).__name__,))
        _pass.append(("police", time.time()))
        publish_runner_card("waiting" if not queued_orders() else "picking up")
        _pass.append(("card", time.time()))
        # Idle factories must still publish capabilities (not just during runs)
        # to remain routable. 'idle' is a RELEASING_STATE, so this declares capability
        # WITHOUT claiming the GPU; sticky-capability rules preserve the set.
        try:
            _announce_capabilities()
        except Exception:
            pass                      # never let a status push interrupt the order taker
        _pass.append(("announce", time.time()))
        if pause_requested():
            log_line("OPERATOR PAUSE (flag between orders) -> consuming flag, releasing lock, "
                     "exiting cleanly; queue untouched. Resume: schtasks /run")
            consume_pause_flag()
            release_runner_singleton()
            return
        cancelling = waiting_cancels()
        pending = [s for s in queued_orders() if _queued_name(s) not in cancelling]
        if not pending:
            _just_collected[0] = bool(_idle_wait(poll, lambda: collect_fleet_orders(log_line)))
            continue
        spec_path = pending[0]
        _worked[0] = True     # from here on the factory is producing; its artifacts are its own
        log_line("picking up %s (%d queued; %s)" % (os.path.basename(spec_path), len(pending), pass_report(_pass)))
        try:
            job = Job(spec_path)
        except Exception as e:
            log_line("UNREADABLE order %s (%r) -> archiving so it cannot block the lane"
                     % (os.path.basename(spec_path), e))
            retire_order(spec_path)
            continue
        try:
            card_check_before_launch(job)
            _run_order_with_comfy_cleanup(job)
        except HandedBack as e:
            # the desk has it now (re-placed or held); this box's lane moves on to its next order.
            # Nothing to clear: a pre-launch hand-back never claimed the lane, and a mid-run one
            # cleared its own claim in run_compute_order.
            log_line("HANDED BACK to the desk: %s" % e)
            continue
        except PauseRequested as e:
            # THE OPERATOR ASKED. Not busy, not defective -- the order is healthy and STAYS QUEUED
            # (idempotent resume: its finished renders are on disk + in the index). Leave the box
            # clean the way retirement would, minus the retirement.
            log_line("OPERATOR PAUSE honored (%s) -> %s STAYS QUEUED; releasing lane, exiting "
                     "cleanly. Resume: schtasks /run" % (e, os.path.basename(spec_path)))
            try:
                stop_comfy(job)
            except Exception:
                pass
            release_lane_if_ours(job.name, log_line)
            try:
                clear_current_run()
            except Exception:
                pass
            consume_pause_flag()
            release_runner_singleton()
            notify(job, "FOUNDRY PAUSED by operator flag after the in-flight render. %s stays "
                        "queued; `schtasks /run /tn \"P20 Foundry Runner\"` resumes." % job.name)
            return
        except LaneYield as e:
            # The RUN lent the lane (status-based, see the class docstring). Like a pause EXCEPT:
            # no operator flag to consume, and the loop CONTINUES instead of exiting -- the queue
            # sort runs the priority orders now and FIFO brings this order straight back.
            log_line("LANE YIELD honored (%s) -> %s stays queued; priority orders take the lane"
                     % (e, os.path.basename(spec_path)))
            try:
                stop_comfy(job)
            except Exception:
                pass
            release_lane_if_ours(job.name, log_line)
            try:
                clear_current_run()
            except Exception:
                pass
            notify(job, "LANE YIELDED at a declared milestone: priority orders run now, then this "
                        "run resumes losslessly. (%s)" % e)
            continue
        except LaneBusy as e:
            # TRANSIENT -- the order is fine, the card is occupied. Leave the spec in the queue and
            # retry on the next poll. Bounded by foreign_run_guard's own stale_after: a claim that is
            # genuinely dead ages out at 30 min and this order launches normally.
            last = busy_logged.get(spec_path, 0.0)
            if time.time() - last > 300.0:
                busy_logged[spec_path] = time.time()
                log_line("lane busy -> %s STAYS QUEUED, retrying (%s)"
                         % (os.path.basename(spec_path), e))
            time.sleep(poll)
            continue
        except TrainerUnstoppable as e:
            # A PROCESS we cannot stop, not a defective order. The order STAYS QUEUED untouched --
            # archiving it (what the catch-all below used to do) failed a healthy v10 twice for a
            # fault that was never its own, and left the trainer running for an order that no
            # longer existed. The runner EXITS rather than taking the next order, because the card
            # is held by something the factory does not control and picking up work would mean
            # launching trainer #2 onto it. foreign_run_guard now also refuses on the way back in.
            job.log("FATAL: %r" % (e,))
            try:
                pid, cmd = any_trainer_running()
            except Exception:
                pid, cmd = None, ""
            log_line("TRAINER WILL NOT STOP (%s) -> %s STAYS QUEUED; runner exiting so nothing "
                     "launches onto the held card" % (e, os.path.basename(spec_path)))
            release_runner_singleton()
            notify(job, "FOUNDRY HELD — a trainer will not stop. %s is UNTOUCHED in the queue "
                        "(nothing about the order is wrong). %s\nLive trainer: PID %s %s\n"
                        "Lossless stop: `type nul > %s`, wait for the checkpoint, then "
                        "`schtasks /run /tn \"P20 Foundry Runner\"`."
                        % (job.name, e, pid or "none found", cmd[:120], STOP_NOW))
            return
        except Exception as e:
            job.log("FATAL: %r" % (e,))
            notify(job, "FOUNDRY ERROR — needs a human: %r" % (e,))
            # the desk must hear it, or its record stays `claimed` and the customer counts the order as in flight forever
            try:
                report_fleet_result(job, False, ("FATAL on %s: %r" % (FLEET_HOST, e))[:200])
            except Exception:
                pass
            # Archive anyway: a failed order must not block every order behind it. The state file
            # and log stay put, so nothing about the failure is lost.
            log_line("order FAILED (%r) -> archived; lane cleared" % (e,))
            release_lane_if_ours(job.name, log_line)
            retire_order(spec_path)
            continue
        busy_logged.pop(spec_path, None)
        release_lane_if_ours(job.name, log_line)
        # Record shipped count before archiving to distinguish empty orders from
        # successful ones (prevents "zero renders" orders from appearing identical to
        # full ones in archive logs).
        _shipped = _order_shipped_count(job)
        try:
            job.state["shipped_count"] = _shipped
            job.state["shipped_nothing"] = (_shipped == 0)
            job.save()
        except Exception:
            pass                                  # Desk-side order completion is reported here (not by the compute job) because desk orders otherwise remain 'claimed' and block chained successors.
# 'cancelled' status is used for early stops to explicitly release successors.
        if not getattr(job, "desk_reported", False):
            try:
                _st = getattr(job, "state", None)
                _early = _st.get("stopped_early") if isinstance(_st, dict) else None
                if _early:
                    report_fleet_result(job, False, "stopped by operator slip at step %s; shipped what existed"
                                        % _early.get("at"), status="cancelled")
                else:
                    report_fleet_result(job, True)
            except Exception as e:                # noqa: BLE001 - a desk report must never fail a finished order
                log_line("desk report for %s failed (%r)" % (job.name, e))
        # run_compute_order retires its own order; retiring again would log a FileNotFoundError as if something failed
        dst = retire_order(spec_path) if os.path.exists(spec_path) else "(archived by the order itself)"
        if _shipped == 0:
            log_line("!! order complete but SHIPPED NOTHING (0 renders/checkpoints recorded) -> %s"
                     % (os.path.basename(dst) if dst else "(archive failed)"))
            log_line("   an order that produced nothing is not a clean result - check the log "
                     "before trusting anything downstream of it")
        else:
            log_line("order complete (%d shipped) -> %s"
                     % (_shipped, os.path.basename(dst) if dst else "(archive failed)"))


if __name__ == "__main__":
    if "--queue" in sys.argv:
        run_queue()
    else:
        spec = sys.argv[1]
        shadow = "--shadow" in sys.argv
        job = Job(spec)
        try:
            run(job, shadow=shadow)
        except Exception as e:
            job.log("FATAL: %r" % (e,))
            notify(job, "FOUNDRY ERROR — needs a human: %r" % (e,))
            raise
