"""Mothership resident render worker.

Consumes the orchestrator's consent-gated render queue.

Critical constraints:
- Must never touch the GPU while a trainer is running or Foundry owns ComfyUI.
- Runs as highest-priority scheduled task for process visibility.

Workflow per job:
1. Acquire downtime/keep-alive locks.
2. Unload Ollama models and ensure ComfyUI availability.
3. Stage LoRA, bake conditioning, and perform slim render.
4. POST result PNG to user's HAI library."""
import datetime
import io
import json
import glob
import shutil
import os
import re
import subprocess
import time
import urllib.request
import urllib.error
import urllib.parse
import threading

import sys as _sys, os as _os
# isolated interpreters (ai-toolkit's venv runs with -I) ignore PYTHONPATH and the cwd,
# so make this module's own directory importable explicitly - same line p20_health_daemon
# already carries. Without it `import foundry_paths` dies and the worker never starts.
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import foundry_paths as _P
import foundry_lock            # singleton-lock liveness; same sys.path caveat as foundry_paths

ORCH = _P.head_url()          # '' when this worker is not linked to a head
# Hostname-derived, NOT a "mothership" default -- the same fail-open fix as the foundry.
# A worker that loses its env var would otherwise stamp ANOTHER box's card and the tiles
# would silently describe the wrong machine, which is the exact failure per-host cards exist
# to prevent.
FLEET_HOST = (os.environ.get("FOUNDRY_FLEET_HOST")
              or __import__("socket").gethostname().split(".")[0].lower())

# Auto-detects render capability rather than hardcoding hosts.
# WORKER_CAN_RENDER is derived from the presence of the ComfyUI launcher,
# or can be overridden via env var for testing or to explicitly disable rendering.
# This gates only the RENDER duty; other worker responsibilities (heartbeat,
# card state, progress reporting) remain active regardless of render capability.
_render_env = os.environ.get("WORKER_CAN_RENDER")
WORKER_CAN_RENDER = (_render_env != "0") if _render_env is not None \
    else bool(_P.COMFY_LAUNCH and os.path.exists(_P.COMFY_LAUNCH))

DM_ENV = os.path.join(_P.ETC, "dm_relay.env")
COMFY = os.environ.get("FOUNDRY_COMFY", "http://127.0.0.1:8189")
COMFY_LAUNCH = _P.COMFY_LAUNCH
BAKE = os.path.join(_P.BIN, "p20_bake_cond.py")
SLIM = os.path.join(_P.BIN, "p20_preview_render_slim.py")
KREA = os.path.join(_P.BIN, "p20_krea_render.py")     # Krea 2 encodes in-graph: no cond bake on that path
LORAS = _P.COMFY_LORAS
OUTPUT = _P.COMFY_OUTPUT
COND_DIR = os.path.join(_P.CACHE_DIR, "cond_cache")
PY = _P.VENV_PY
LOG = os.path.join(_P.LOG_DIR, "p20_render_worker.log")
DOWNTIME_HOLD = _P.DOWNTIME_HOLD
KEEPAWAKE_HOLD = _P.KEEPAWAKE_HOLD
CORRIDORKEY_HOLD = os.path.join(_P.HOLD_DIR, "corridorkey.hold")   # written by corridorkey_gated.py
POLL_SECS = 30
BUSY_POLL_SECS = 3   # BUSY_POLL_SECS=3s ensures live caption updates (~2.5s interval). Idle defaults to POLL_SECS (30s).
LAST_RENDER_SECS = None    # measured duration of the most recent completed render (ETA source)


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _token():
    try:
        with open(DM_ENV, encoding="ascii") as f:
            for ln in f:
                if ln.startswith("P20_WORKER_TOKEN="):
                    return ln.strip().split("=", 1)[1]
    except Exception:
        pass
    return None


def _api(path, data=None, headers=None, timeout=30):
    if not ORCH:
        raise RuntimeError("no head address: this worker is not linked")
    req = urllib.request.Request(ORCH + path, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ALLOW-list, not deny-list: p20_queue is shared with other consumers.
# A deny-list would cause this worker to silently consume & finish unknown
# job kinds (destroying orders), reproducing the catch-all-consumes-work bug.
#
# "" is required: person renders enqueue'd by p20_queue lack a `kind` key.
RENDER_KINDS = ("", "render", "quick_preview")


def _live_job():
    """Return the state file of the currently live run.

    A live run is defined as the state file with the newest mtime
    among those not marked DONE. Finished runs must never be considered
    live, even if their files are alphabetically later than active ones.
    """
    import glob as _glob
    # Try CURRENT_RUN.json first (authoritative source of truth).
    # The glob fallback handles older foundry versions that don't publish it,
    # but is risky as it may pick up stale DONE runs.
    try:
        with open(_P.CURRENT_RUN, encoding="utf-8") as f:
            cur = json.load(f)
        sp = cur.get("state_file")
        if sp and os.path.exists(sp):
            with open(sp, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("phase") != "DONE":
                return sp, st
    except Exception:
        pass
    best = None
    for sp in _glob.glob(os.path.join(_P.JOBS, "*.state.json")):
        try:
            with open(sp, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            continue
        if st.get("phase") == "DONE" or not st.get("started"):
            continue
        m = os.path.getmtime(sp)
        if best is None or m > best[0]:
            best = (m, sp, st)
    return (best[1], best[2]) if best else (None, {})


def _progress(sp):
    """(step, total) for the live run — display only, so the foundry's own watch line is the source.

    Decisions still use checkpoint FILES (P20-FOUNDRY-RUNNER-PLAN "gotchas"); this is the number on
    the dashboard, and the foundry already writes it every poll.
    """
    if not sp:
        return (None, None)
    try:
        log = sp.replace(".state.json", ".foundry.log")
        with open(log, "rb") as f:
            f.seek(max(0, os.path.getsize(log) - 8192))
            tail = f.read().decode("utf-8", "ignore")
        m = re.findall(r"watch: step (\d+)/(\d+)", tail)
        if m:
            return (int(m[-1][0]), int(m[-1][1]))
    except Exception:
        pass
    return (None, None)


def _stated_phase(sp):
    """Return the verbatim human phase string from CURRENT_RUN.json for this run.

    Returns an empty string if:
    - The file is missing or invalid JSON.
    - The `state_file` field does not match the provided `sp` (stale/wrong job).
    - The data is older than 30 minutes (stale status).
    """
    try:
        with open(_P.CURRENT_RUN, encoding="utf-8") as f:
            cur = json.load(f)
        if sp and cur.get("state_file") and cur["state_file"] != sp:
            return ""
        upd = cur.get("updated") or ""
        import datetime as _dt
        age = (_dt.datetime.now() - _dt.datetime.fromisoformat(upd)).total_seconds()
        if age > 1800:
            return ""
        return (cur.get("phase") or "").strip()[:160]
    except Exception:
        return ""


def gpu_status():
    """What the card is doing right now + the best live ETA we can source. Training ETA comes from
    the foundry's own state files (it publishes eta each watch poll — v0.3.2)."""
    sp, st = _live_job()
    step, total = _progress(sp)
    # Duplicate key for compatibility: readers depend on both `total` and `steps_total`.
    prog = {"step": step, "total": total, "steps_total": total,
            "pct": (round(100.0 * step / total, 1) if step and total else None),
            "job": (os.path.basename(sp).replace(".job.state.json", "") if sp else "")}
    if st.get("phase") in ("PAUSE", "RENDER", "RESUME"):
        out = {"state": "training-pause", "detail": "mid-run preview cycle (%s)" % st["phase"],
               "eta": st.get("eta", "")}
        out.update(prog)
        return out
    if st.get("phase") == "LADDER":
        out = {"state": "finishing", "detail": "run complete, rendering the checkpoint ladder",
               "eta": ""}
        out.update(prog)
        return out
    if trainer_running():
        out = {"state": "training",
               "detail": _stated_phase(sp) or "a training run owns the GPU",
               "eta": st.get("eta", "")}
        out.update(prog)
        return out
    if corridorkey_active():
        return {"state": "busy", "detail": "a CorridorKey keying job owns the GPU", "eta": ""}
    # CAPTION runs via OLLAMA are invisible to process checks; rely on CURRENT_RUN claim
    # to prevent reporting "free" while the card is owned.
    try:
        with open(_P.CURRENT_RUN, encoding="utf-8") as f:
            _cur = json.load(f)
        if _cur.get("state") == "captioning":
            _ph = _stated_phase(None) or "a captioning run owns the GPU"
            _out = {"state": "busy", "detail": "captioning", "eta": "",
                    "job": _cur.get("name", ""), "step": None, "total": None,
                    "steps_total": None, "pct": None, "rate": ""}
            m = re.search(r"captioning\s+(\d+)\s*/\s*(\d+)", _ph)
            if m:
                _d, _t = int(m.group(1)), int(m.group(2))
                _out.update(step=_d, total=_t, steps_total=_t,
                            pct=(round(100.0 * _d / _t, 1) if _t else None))
            # The orchestrator parses `rate` from the ETA string via regex; omitting it breaks display.
            mr = re.search(r"([\d.]+)\s*s/img", _ph)
            me = re.search(r"~(\d+)m left", _ph)
            _bits = []
            if mr:
                _out["rate"] = mr.group(1) + " s/img"
                _bits.append(mr.group(1) + " s/img")
            if me:
                _bits.append("~%sm left" % me.group(1))
            if _bits:
                _out["eta"] = " · ".join(_bits)
            mf = re.search(r"(\d+)\s+failed", _ph)
            if mf and mf.group(1) != "0":
                _out["detail"] = "captioning · %s FAILED" % mf.group(1)
            return _out
    except Exception:
        pass
    # Status depends on GPU usage, not process ownership.
    # If a render launched elsewhere owns the GPU, report "rendering"
    # to prevent other workers from claiming it as "free".
    busy, n = _comfy_busy()
    if busy:
        out = {"state": "rendering",
               "detail": "a ComfyUI render owns the GPU (%d queued)" % n, "eta": ""}
        out.update(prog)
        return out
    return {"state": "free", "detail": "", "eta": ""}


def compute_holds_card():
    """Returns the name of the active GPU compute order holding the card, or an empty string if none.

    Note: CPU orders do not hold the card and are treated as unclaimed. An unreadable state file is treated as non-free to avoid false availability signals."""
    try:
        with open(_P.CURRENT_RUN, encoding="utf-8") as f:
            cur = json.load(f)
    except FileNotFoundError:
        return ""
    except Exception as e:
        return "CURRENT_RUN unreadable (%r)" % (e,)
    if cur.get("state") != "compute" or str(cur.get("phase") or "").startswith("compute(cpu"):
        return ""
    return str(cur.get("name") or "a compute order")


def _comfy_busy():
    """(is ComfyUI executing anything, queue depth). Fail-soft: a probe that cannot answer must not
    claim the card is free, so an unreachable-but-listening server counts as busy."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8189/queue", timeout=5) as r:
            q = json.loads(r.read().decode())
        n = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
        return (n > 0, n)
    except urllib.error.URLError:
        return (False, 0)          # nothing listening on 8189 = genuinely no render
    except Exception:
        return (True, 0)           # listening but unparseable — assume busy, never invent "free"


def post_gpu_status(token, status):
    """Publish this card's state to the orchestrator.

    The payload MUST include a 'host' key. Without it, multiple workers writing to
    the same orchestrator log will overwrite each other's status silently
    (last-writer-wins), preventing per-factory tracking."""
    global LAST_GPU_STATUS
    LAST_GPU_STATUS = dict(status)
    try:
        payload = dict(status)
        payload.setdefault("host", FLEET_HOST)
        _api("/p20/gpu-status", data=json.dumps(payload).encode(),
             headers={"X-P20-Token": token, "Content-Type": "application/json"})
    except Exception as e:
        log("gpu-status post failed: %r" % (e,))


def fetch_jobs(token):
    """Request jobs this factory can build.

    Passes the current host ID so the orchestrator filters by capability.
    Jobs without requirements are backward compatible."""
    try:
        raw = _api("/p20/jobs?host=%s" % urllib.parse.quote(FLEET_HOST),
                   headers={"X-P20-Token": token})
        return json.loads(raw).get("jobs", [])
    except Exception as e:
        log("poll failed: %r" % (e,))
        return []


def claim_job(token, job_id):
    """Acquire this exact desk record before any unload, bake or render. A lost race is ordinary."""
    try:
        raw = _api("/p20/jobs/%s/claim?host=%s" % (job_id, urllib.parse.quote(FLEET_HOST)),
                   data=b"", headers={"X-P20-Token": token})
        return json.loads(raw).get("ok") is True
    except Exception as exc:
        log("claim unavailable for %s (%r) - job left untouched" % (job_id, exc))
        return False


def post_result(token, job_id, png=None, error=""):
    qs = "?status=done" if png else "?status=failed&error=" + urllib.parse.quote(error[:400])
    try:
        _api("/p20/jobs/%s/result%s" % (job_id, qs), data=png or b"x",
             headers={"X-P20-Token": token, "Content-Type": "application/octet-stream"}, timeout=60)
        return True
    except Exception as e:
        log("result post failed for %s: %r" % (job_id, e))
        return False


def do_quick_preview(token, job):
    """Render one approximate training preview and post it back.

        Uses a smaller base model for speed; artifacts are labeled APPROXIMATE
    to prevent confusion with full-fidelity training checkpoints. Uses the same
    render pipeline (SLIM/ComfyUI) as other jobs, differing only in adapter source
    and output naming.

        Fail-soft: exceptions are caught, a FAILED result is posted, and control
        returns immediately. The preview process must never crash the worker or
        interfere with the remote training run."""
    jid = job["id"]
    step = job.get("step")
    arch = (job.get("arch") or "?").strip().lower()
    name = job.get("prompt_name") or "preview"
    log("job %s: QUICK PREVIEW %s ck%s %s (APPROXIMATE - %s base, not the trained model)"
        % (jid, job.get("run"), step, name, arch))

    src = job.get("lora_path") or ""
    if not src:
        post_result(token, jid, error="quick preview carried no lora_path")
        return
    staged = os.path.join(LORAS, os.path.basename(src))
    try:
        if os.path.abspath(src) != os.path.abspath(staged):
            if not os.path.isfile(src):
                post_result(token, jid,
                            error="checkpoint not reachable from this box: %s" % src)
                return
            shutil.copy2(src, staged)
    except Exception as e:
        post_result(token, jid, error="could not stage the adapter (%s)" % type(e).__name__)
        return

    cond = job.get("cond_name") or name
    if not os.path.isfile(os.path.join(COND_DIR, cond + ".cond")):
        post_result(token, jid,
                    error=("no conditioning %r on this box. Quick previews render from baked "
                           "conditioning (that is why no encoder lives here); it has to be baked "
                           "where the encoder is and shipped." % cond))
        return

    write_holds()
    unload_ollama()
    if not comfy_up() and not launch_comfy():
        post_result(token, jid, error="ComfyUI failed to boot")
        return

    prefix = "APPROX_%s_ck%s_%s" % (job.get("run") or "run", step, name)
    cmd = [PY, SLIM, os.path.basename(staged), prefix, cond, "42", "1024", "1024",
           str(job.get("strength") or 0.6)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        log("  slim rc=%s %s" % (r.returncode, ((r.stdout or "").strip().splitlines() or [""])[-1]))
    except Exception as e:
        post_result(token, jid, error="render failed (%s)" % type(e).__name__)
        return

    png = None
    try:
        hits = sorted(glob.glob(os.path.join(OUTPUT, prefix + "*.png")),
                      key=os.path.getmtime)
        if hits:
            with open(hits[-1], "rb") as f:
                png = f.read()
    except Exception:
        png = None
    if not png:
        post_result(token, jid, error="render produced no PNG")
        return

    post_result(token, jid, png=png)
    log("  posted APPROXIMATE preview %s ck%s %s (%d KB)" % (job.get("run"), step, name,
                                                             len(png) // 1024))


def trainer_running():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { "
             "$null -ne $_.CommandLine -and $_.CommandLine.Contains('run.py') -and "
             "$_.CommandLine.Contains('.yaml') } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return out not in ("", "0")
    except Exception:
        return True     # can't tell -> assume busy (never fight a trainer for the card)


def corridorkey_active():
    """Return True if a CorridorKey run is active (holding the GPU).\n\nChecks for a hold file containing an ISO-8601 UTC expiry timestamp.\nIf the file is missing, unreadable, or expired, returns False (fail-open\nto allow renders to proceed despite potential stale state)."""
    try:
        with open(CORRIDORKEY_HOLD, encoding="ascii") as f:
            expiry = f.read().strip()
        return datetime.datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ") > datetime.datetime.utcnow()
    except Exception:
        return False


def comfy_up():
    try:
        with urllib.request.urlopen(COMFY + "/system_stats", timeout=3):
            return True
    except Exception:
        return False


def write_holds():
    expiry = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for p in (DOWNTIME_HOLD, KEEPAWAKE_HOLD):
        try:
            with open(p, "w", encoding="ascii") as f:
                f.write(expiry)
        except Exception:
            pass


def unload_ollama():
    """Unload resident LLMs and verify GPU context is clear.

    Cross-context collisions poison the GPU if a model remains in-flight after
    unload. We poll /api/ps until empty (bounded) to ensure safety, trading a
    short delay for correctness."""
    def resident():
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=5) as r:
            return [m.get("name") for m in json.loads(r.read()).get("models", []) if m.get("name")]

    try:
        t0 = time.time()
        while True:
            models = resident()
            if not models:
                if time.time() - t0 > 2:
                    log("ollama idle after %.0fs wait (collision guard)" % (time.time() - t0))
                return
            for m in models:
                body = json.dumps({"model": m, "keep_alive": 0}).encode()
                req = urllib.request.Request("http://localhost:11434/api/generate", data=body,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30):
                    pass
                log("unload requested: %s" % m)
            if time.time() - t0 > 120:
                log("WARN: ollama still resident after 120s (%s) — proceeding; daemon is the net" % models)
                return
            time.sleep(5)
    except Exception as e:
        log("ollama unload skipped (%r)" % (e,))




def launch_comfy():
    subprocess.run(["schtasks", "/create", "/f", "/tn", "P20 Worker ComfyUI", "/tr", COMFY_LAUNCH,
                    "/sc", "once", "/st", "00:00"], capture_output=True, timeout=30)
    subprocess.run(["schtasks", "/run", "/tn", "P20 Worker ComfyUI"], capture_output=True, timeout=30)
    t0 = time.time()
    while not comfy_up():
        if time.time() - t0 > 300:      # measured: warm 52s, cold ~240s (same numbers as the foundry)
            return False
        time.sleep(5)
    log("ComfyUI up (%.0fs)" % (time.time() - t0))
    return True


def stop_comfy():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "$p = (Get-NetTCPConnection -LocalPort 8189 -ea 0 | Where-Object { $_.State -eq 'Listen' -or $_.RemoteAddress -in '0.0.0.0','::' } | Select -First 1).OwningProcess; "
                    "if ($p) { Stop-Process -Id $p -Force }"], capture_output=True, timeout=30)


def _render_status(job_id, phase, **progress):
    status = {"state": "rendering", "job_id": job_id, "phase": phase,
              "detail": "rendering job %s" % job_id, "step": None, "steps_total": None,
              "s_per_step": None, "remaining_secs": None, "eta": ""}
    if job_id == ACTIVE_RENDER_JOB and ACTIVE_RENDER_STARTED:
        status["job_started_at"] = ACTIVE_RENDER_STARTED
    status.update(progress)
    return status


def _render_stages(job):
    if not is_krea_job(job):
        return None
    stages = {"1": "Loading Krea model", "2": "Loading text encoder", "3": "Loading image decoder",
              "4": "Encoding prompt", "5": "Preparing conditioning", "6": "Preparing canvas",
              "7": "Loading generation model and preparing sampler", "8": "Decoding image", "9": "Saving image",
              "20": "Loading identity adapter", "21": "Loading behavior adapter",
              "34": "Releasing completed encoder VRAM"}
    if job.get("reference"):
        stages.update({"4": "Encoding reference and instruction", "5": "Encoding reference conditioning",
                       "30": "Loading reference photo", "31": "Encoding reference image",
                       "32": "Loading reference-edit adapter", "33": "Preparing reference for generation",
                       "34": "Releasing completed encoder VRAM"})
    return stages


def _progress_reporter(token, job_id, stop_ev, stages=None):
    """Listen for ComfyUI websocket progress events to update remaining time estimate.

    Calculates: (steps-left) * (measured pace of THIS render, EMA over recent steps).
    Updates orchestrator every ~5s. Fail-soft: static estimate used on any error."""
    try:
        import websocket
        # clientId MUST match SLIM's (ComfyUI addresses progress events to the prompt's OWNING
        # client-id — listening under any other name receives nothing; found 15:24 after the
        # reporter sat eventless through a full healthy render)
        ws = websocket.create_connection("ws://127.0.0.1:8189/ws?clientId=p20_preview_slim",
                                         timeout=8)
        ws.settimeout(10)
        last_t, pace, last_post = None, None, 0.0
        while not stop_ev.is_set():
            try:
                raw = ws.recv()
            except Exception as _we:
                # v1.7: timeouts are NORMAL during slow/stalled steps — keep listening (the v1.5
                # thread died on its first 20s-quiet stretch, killing the countdown AND leaving the
                # daemon's axe blind to progress). Only a real socket error ends the watch.
                if "timed out" in str(_we).lower():
                    continue
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if stop_ev.is_set():
                break
            if stages and msg.get("type") == "executing":
                node = str((msg.get("data") or {}).get("node"))
                if node in stages:
                    post_gpu_status(token, _render_status(job_id, stages[node]))
                continue
            if msg.get("type") != "progress":
                continue
            d = msg.get("data", {})
            val, mx = d.get("value"), d.get("max")
            if not val or not mx:
                continue
            now = time.time()
            if last_t is not None:
                step_secs = now - last_t
                pace = step_secs if pace is None else (0.6 * pace + 0.4 * step_secs)
            last_t = now
            # Written for stall daemon to prevent premature kill of near-complete renders
            try:
                with open("F:/foundry/var/state/telemetry/render_progress.json", "w") as _pf:
                    _pf.write(json.dumps({"step": val, "max": mx, "ts": now}))
            except Exception:
                pass
            if val == 1 or val == mx or now - last_post > 5:
                remaining = int((mx - val) * pace) + 15 if pace else None
                post_gpu_status(token, _render_status(job_id, "Sampling", step=val, steps_total=mx,
                                        eta=("step %d/%d @ %.1fs/step" % (val, mx, pace)) if pace else "",
                                        s_per_step=round(pace, 2) if pace else None,
                                        remaining_secs=remaining))
                last_post = now
        try:
            ws.close()
        except Exception:
            pass
    except Exception:
        pass


# Fresh-Serve Mode is dormant unless FRESH_SERVE_FLAG exists.
FRESH_SERVE_FLAG = os.path.join(_P.STATE_DIR, "FRESH_SERVE_MODE")
BOOT_MARKER = os.path.join(_P.TELEMETRY_DIR, "renders_this_boot.txt")


def _boot_id():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return out or None
    except Exception:
        return None


def fresh_serve_due():
    """Returns True if the current kernel should be rebooted before rendering.

    This enforces a 'fresh kernel' policy: if FRESH_SERVE_FLAG exists, any render
    on a kernel that has previously served a request triggers a reboot. This prevents
    degradation in unsupervised play sessions where kernels might otherwise linger.

    The check is fail-soft: if the boot ID cannot be determined or the marker file
    is missing/corrupt, it returns False (no reboot)."""
    if not os.path.exists(FRESH_SERVE_FLAG):
        return False
    bid = _boot_id()
    if not bid:
        return False
    try:
        with open(BOOT_MARKER) as f:
            stored = f.read().strip()
        return stored == bid          # same boot already rendered -> reboot before serving
    except Exception:
        return False                  # no marker = virgin kernel -> serve now


def mark_kernel_used():
    bid = _boot_id()
    if bid:
        try:
            with open(BOOT_MARKER, "w") as f:
                f.write(bid)
        except Exception:
            pass


_HORIZONTAL_POSE = re.compile(r"(?i)\b(lying|laying|lounging|reclining|sunbathing|sprawled|lies?\s+(?:on|in|back)|asleep|sleeping|floating|relaxing|resting)\b")
_VERTICAL_POSE = re.compile(r"(?i)\b(standing|walking|full[\s-]?body|full[\s-]?length|head[\s-]?to[\s-]?toe)\b")


def pick_canvas(prompt):
    """Returns canvas dimensions based on pose detected in the prompt.

    Square canvases (1024x1024) distort horizontal/reclining poses;
    use wider aspect ratios for these and taller ones for vertical poses.
    All resolutions are ~1MP to maintain consistent VRAM usage."""
    if _HORIZONTAL_POSE.search(prompt or ""):
        return 1216, 832
    if _VERTICAL_POSE.search(prompt or ""):
        return 832, 1216
    return 1024, 1024


ACTIVE_RENDER_JOB = None
ACTIVE_RENDER_STARTED = None
LAST_GPU_STATUS = {}


def is_krea_job(job):
    """A HAI order for the Krea 2 base. The desk states it in `requires`; nothing here guesses from the renderer name."""
    return "render.krea2" in (job.get("requires") or [])


def run_job(token, job):
    global ACTIVE_RENDER_JOB, ACTIVE_RENDER_STARTED
    if not claim_job(token, job["id"]):
        return
    ACTIVE_RENDER_JOB = job["id"]
    ACTIVE_RENDER_STARTED = time.time()
    try:
        os.remove(os.path.join(_P.TELEMETRY_DIR, "render_progress.json"))
    except OSError:
        pass
    try:
        return _run_job(token, job)
    finally:
        ACTIVE_RENDER_JOB = None
        ACTIVE_RENDER_STARTED = None


def _run_job(token, job):
    global LAST_RENDER_SECS
    t_start = time.time()
    jid = job["id"]
    # Quick previews are factory-generated checkpoints: no requester, so job["user"] is missing.
    # Branch early to avoid KeyError when expecting consent/user fields.
    if (job.get("kind") or "") == "quick_preview":
        return do_quick_preview(token, job)

    _generic = not job.get("lora_path")
    log("job %s: %s for %s (use=%s)"
        % (jid, job.get("person") or "[generic]", job["user"], job.get("use")))
    write_holds()
    post_gpu_status(token, _render_status(jid, "Releasing language models"))
    unload_ollama()
    post_gpu_status(token, _render_status(jid, "Starting image renderer"))
    if not comfy_up() and not launch_comfy():
        post_result(token, jid, error="ComfyUI failed to boot")
        return
    if _generic:
        lora_name = "none"          # slim renderer drops the adapter node for this sentinel
    else:
        lora_src = job["lora_path"]
        lora_name = "p20job_%s.safetensors" % jid
        r = subprocess.run(["cmd", "/c", "copy", "/Y", lora_src, os.path.join(LORAS, lora_name)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            post_result(token, jid, error="LoRA stage failed: %s" % lora_src)
            return
    cond = "job_%s" % jid
    krea = is_krea_job(job)
    if not krea:
        rb = subprocess.run([PY, BAKE, cond, job["prompt"]], capture_output=True, text=True, timeout=900)
        if "BAKED" not in (rb.stdout or ""):
            post_result(token, jid, error="cond bake failed: %s" % (rb.stdout or rb.stderr or "")[-300:])
            return
    prefix = "p20job_%s" % jid
    seed = str(job.get("seed", int(jid, 16) % 2_000_000_000))
    _stop = threading.Event()
    _rep = threading.Thread(target=_progress_reporter, args=(token, jid, _stop, _render_stages(job)), daemon=True)
    global WORKER_RENDERING
    WORKER_RENDERING = True          # so the external watcher stands down and we get ONE reporter
    _rep.start()
    try:
        if job.get("width") is not None and job.get("height") is not None:
            w, h = job["width"], job["height"]
        else:
            w, h = pick_canvas(job.get("prompt", ""))
        if (w, h) != (1024, 1024):
            log("job %s: scene-aware canvas %dx%d" % (jid, w, h))
        cmd = ([PY, KREA, lora_name, prefix, job["prompt"], seed, str(w), str(h)] if krea
               else [PY, SLIM, lora_name, prefix, cond, seed, str(w), str(h)])
        if krea:
            owner = _P.setting("OWNER_USER", "")          # the lab's owner profile (a setting)
            is_owner = bool(owner) and job.get("user") == owner
            cmd.append(str(job.get("strength", 1.4 if is_owner else 1.0)))
            # a second adapter loads only when it is the one this factory names (KREA2_SECOND_ADAPTER)
            if job.get("second_adapter") and job["second_adapter"] != _P.setting("KREA2_SECOND_ADAPTER", ""):
                raise ValueError("Unknown Krea second adapter (not this factory's KREA2_SECOND_ADAPTER)")
            if job.get("reference"):
                if not is_owner or job.get("lora_path"):
                    raise ValueError("Reference renderer requires the owner's reference-image order (OWNER_USER)")
                cmd.extend([str(job.get("second_adapter_strength", 3.0)) if job.get("second_adapter") else "0",
                            json.dumps(job["reference"])])
            elif job.get("second_adapter"):
                cmd.append(str(job.get("second_adapter_strength", 3.0)))
        rr = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    finally:
        _stop.set()
        WORKER_RENDERING = False
    png_path = os.path.join(OUTPUT, "%s_00001_.png" % prefix)
    if not os.path.exists(png_path):
        # Include stderr so renderer crashes (tracebacks) are visible in the error report
        post_result(token, jid, error="render produced no PNG: %s" % ((rr.stdout or "") + (rr.stderr or ""))[-400:])
        return
    with open(png_path, "rb") as f:
        png = f.read()
    post_gpu_status(token, _render_status(jid, "Delivering image to your library"))
    if post_result(token, jid, png=png):
        mark_kernel_used()
        LAST_RENDER_SECS = time.time() - t_start
        log("job %s DONE (%d bytes, %.0fs end-to-end) -> user library" % (jid, len(png), LAST_RENDER_SECS))
    # housekeeping: the staged LoRA copy + baked cond are per-job artifacts — don't accumulate
    # The .txt sidecar records what the .cond was baked from; delete it WITH the .cond or
    # per-job sidecars accumulate forever -- swapping a stale-data leak for a file leak.
    for p in (os.path.join(LORAS, lora_name),
              os.path.join(os.path.join(_P.CACHE_DIR, "cond_cache"), cond + ".cond"),
              os.path.join(os.path.join(_P.CACHE_DIR, "cond_cache"), cond + ".txt")):
        try:
            os.remove(p)
        except OSError:
            pass


WORKER_RENDERING = False


_ew_n = [0]      # external-render-watch failure count (see the handler below)
_ew_t = [0.0]    # last time we complained


def _external_render_watch(token):
    """Publish step progress for external renders sharing the ComfyUI client-id.

    These renders (foundry preview/ladder) bypass worker launch, so the dashboard
    would otherwise show "free" and stall detection would fail because render
    clocking behavior differs from training stalls. This listener unifies reporting."""
    rep = stop = None
    refreshed = 0.0
    while True:
        try:
            # Liveness beats from HERE, every 5 s, not only from the main loop -- the main
            # loop blocks inside run_job() for the length of a person render, so a
            # main-loop-only heartbeat goes stale during healthy work and turns its consumer
            # into noise. This is also the correct semantics: the heartbeat is read to answer
            # "can the worker publish render progress?", and that capability IS this thread.
            beat()
            if ACTIVE_RENDER_JOB and time.monotonic() - refreshed >= POLL_SECS:
                # This existing heartbeat thread also covers encoder loading, before sampler
                # progress exists. Keep the named order fresh for accounting and placement.
                status = dict(LAST_GPU_STATUS)
                status.update(state="rendering", job_id=ACTIVE_RENDER_JOB)
                status.setdefault("detail", "Preparing image order")
                post_gpu_status(token, status)
                refreshed = time.monotonic()
            # _comfy_busy(): fail-soft; unparseable-but-listening counts as BUSY.
            # Never invent 'free' on parse errors.
            active = _comfy_busy()[0] and not WORKER_RENDERING
            if active and (rep is None or not rep.is_alive()):
                stop = threading.Event()
                rep = threading.Thread(target=_progress_reporter,
                                       args=(token, "foundry-preview", stop), daemon=True)
                rep.start()
                log("external render detected -> progress reporter attached")
            elif not active and stop is not None:
                stop.set()
                rep, stop = None, None
                # Clear stale progress file to prevent new renders from misinterpreting
                # old 100% status during the model-load gap before new data publishes.
                try:
                    os.remove("F:/foundry/var/state/telemetry/render_progress.json")
                except OSError:
                    pass
                log("external render finished -> progress reporter detached (progress cleared)")
        except Exception as e:
            # Throttle + escalate. This handler logged once per 5 s for days and nobody read
            # it; a defect that repeats forever at the same volume is indistinguishable from
            # noise. Log the first, then once a minute, and say it is PERSISTENT.
            _ew_n[0] += 1
            if _ew_n[0] == 1 or (time.time() - _ew_t[0]) > 60:
                _ew_t[0] = time.time()
                log("external render watch FAILING (%d times): %r -- foundry renders are "
                    "NOT publishing progress, so the stall daemon has no mercy data and will "
                    "axe them at any completion percentage" % (_ew_n[0], e))
        time.sleep(5)


WORKER_HEARTBEAT = os.path.join(_P.TELEMETRY_DIR, "render_worker.heartbeat")
WORKER_LOCK = os.path.join(_P.TELEMETRY_DIR, "render_worker.lock")
# fleet_deploy writes this to have the worker exit BETWEEN jobs and come back on the files it just deployed (the worker
# imports its modules once, so a deploy is inert until it restarts). Only a flag newer than this process counts.
RESTART_FLAG = os.path.join(_P.STATE_DIR, "RENDER_WORKER_RESTART")
STARTED_AT = time.time()


def restart_requested(flag=None, started_at=None):
    """True (and the flag consumed) when a deploy asked this worker to restart after it started. A flag older than the
    process was meant for a predecessor and is removed without acting on it."""
    flag = flag or RESTART_FLAG
    try:
        written = os.path.getmtime(flag)
    except OSError:
        return False
    try:
        os.remove(flag)
    except OSError:
        pass
    return written >= (STARTED_AT if started_at is None else started_at)


def acquire_singleton():
    """Acquire singleton lock.

    Refuse to start if a live worker holds the lock; reclaim a stale one.
    This prevents duplicate workers from double-polling the orchestrator when
    the scheduled task repeats. It checks process identity (not just PID
    existence) to avoid false positives from recycled PIDs on Windows.
    """
    try:
        if os.path.exists(WORKER_LOCK):
            live, why = foundry_lock.holder_is_live(WORKER_LOCK)
            if live:
                log("another render worker is live (%s) - exiting so we do not double-poll" % why)
                return False
            log("reclaiming stale lock (%s)" % why)
    except Exception as e:
        log("lock check failed (%r) - continuing" % (e,))
    try:
        os.makedirs(os.path.dirname(WORKER_LOCK), exist_ok=True)
        with io.open(WORKER_LOCK, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        log("could not write lock (%r) - continuing unlocked" % (e,))
    return True


def beat():
    """Touch the heartbeat. Liveness is a FRESHNESS question -- process-exists lied for 7.4 h on the
    rescue daemon, and 'the task last ran on the 26th' told nobody anything here."""
    try:
        with io.open(WORKER_HEARTBEAT, "w", encoding="utf-8") as f:
            f.write("%d %d" % (int(time.time()), os.getpid()))
    except Exception:
        pass



def main():
    log("=== P20 render worker start (poll %ds, orchestrator %s) ===" % (POLL_SECS, ORCH))
    if not acquire_singleton():
        return
    beat()
    token = _token()
    if not token:
        log("FATAL: no P20_WORKER_TOKEN in %s — exiting" % DM_ENV)
        return
    threading.Thread(target=_external_render_watch, args=(token,), daemon=True).start()
    while True:
        if restart_requested():
            log("RESTART requested by a deploy - exiting between jobs; the task relaunches me on the new files")
            return
        try:
            beat()          # every poll: a worker that stops beating is DOWN, whatever
                            # `tasklist` says about the process still existing
            status = gpu_status()
            post_gpu_status(token, status)      # live "what's the card doing" for HAI's ETA answers
            if status["state"] != "free":
                time.sleep(POLL_SECS); continue
            if not WORKER_CAN_RENDER:
                # This host publishes heartbeat, card state and progress, but never CLAIMS a render
                # job. Declining by not fetching is deliberate: a job this box takes and cannot
                # finish is worse for the requester than one that waits for a box that can.
                time.sleep(POLL_SECS); continue
            if compute_holds_card():
                time.sleep(POLL_SECS); continue     # the job waits in the desk's line; the fresh-serve reboot below waits too
            jobs = fetch_jobs(token)
            if not jobs:
                time.sleep(POLL_SECS); continue
            if jobs and fresh_serve_due() and not trainer_running():
                log("FRESH-SERVE: kernel already served this boot -> rebooting before render "
                    "(job waits in queue; worker auto-resumes onlogon)")
                post_gpu_status(token, {"state": "training-pause",
                                        "detail": "fresh-kernel reboot before this render (~3 min)",
                                        "eta": "rebooting for best quality — ~8 min total"})
                subprocess.run(["shutdown", "/r", "/t", "5",
                                "/c", "P20 Fresh-Serve reboot"], capture_output=True)
                time.sleep(120)         # die with the box; onlogon resurrects us
                continue
            for job in jobs:
                if (job.get("kind") or "") not in RENDER_KINDS:
                    continue            # not this worker's work — leave it for whoever collects it
                if trainer_running() or corridorkey_active() or compute_holds_card():
                    break               # a run started mid-batch: yield the card immediately
                if os.path.exists(RESTART_FLAG):
                    break               # a deploy is waiting: finish here, the loop top exits
                post_gpu_status(token, _render_status(job["id"], "Preparing image order"))
                run_job(token, job)
            if not trainer_running():
                stop_comfy()            # leave the card clean between batches
            _st = gpu_status()
            post_gpu_status(token, _st)
        except Exception as e:
            log("loop error: %r" % (e,))
            _st = {}
        # Adaptive polling: fast while busy (to avoid stale UI), slow when idle (to save resources on shared box).
        _busy = (_st or {}).get("state") in ("training", "training-pause", "finishing",
                                             "rendering", "busy")
        time.sleep(BUSY_POLL_SECS if _busy else POLL_SECS)


if __name__ == "__main__":
    main()
