"""On-demand gaming-busy probe for the lead (the AMD gaming PC that hosts the 27B).

When the lead is AWAKE we must not load the ~17GB model onto a GPU a game is already using, or the
load competes with the game (stutter) or spills to CPU. We SSH in and run the pre-deployed
`gpu-free.ps1`, which reports free VRAM via the Windows GPU performance counter (there is no
amd-smi/rocm-smi on the consumer Adrenalin driver). An ASLEEP lead can't be gaming, so this is
only consulted when the host is already reachable. Fail-open: if we can't determine free VRAM, we do
NOT block the lead (preserves prior behavior).
"""
import base64
import hashlib
import json
import os
import subprocess

import config
import notes
import ollama_client

# PowerShell to (re)start Ollama via the documented scheduled task, pre-encoded as -EncodedCommand
# (UTF-16LE base64) so it survives the ssh -> Windows cmd -> powershell quoting layers untouched.
_RESTART_PS_B64 = base64.b64encode(
    "Start-ScheduledTask -TaskName 'Ollama Serve'".encode("utf-16-le")
).decode()

# Same trick for ComfyUI's launcher task (see start_comfy).
_COMFY_PS_B64 = base64.b64encode(
    "Start-ScheduledTask -TaskName 'P20 Worker ComfyUI'".encode("utf-16-le")
).decode()


def _key_args():
    """ssh's `-i <key>` for LEAD_BOX_SSH_KEY, or nothing when no key is set (ssh's own default)."""
    return ["-i", config.LEAD_BOX_SSH_KEY] if config.LEAD_BOX_SSH_KEY else []


def _target():
    """`user@host` for the lead (LEAD_BOX_SSH_USER / LEAD_BOX_SSH_HOST), or the host alone when no user is set."""
    return "%s@%s" % (config.LEAD_BOX_SSH_USER, config.LEAD_BOX_SSH_HOST) if config.LEAD_BOX_SSH_USER else config.LEAD_BOX_SSH_HOST


def _reported_free_mb():
    """Free VRAM (MB) from the lead's OWN load report (pushed every minute by its reporter: `gpu_memory.card_mb` -
    `adapter_mb`, the counter the ssh probe reads), or None when the report is missing, older than two pushes, or its
    counter failed. Never raises."""
    try:
        import scheduler
        rec = scheduler.gpu_load(scheduler.LEAD_BOX)
        age = scheduler._report_age_s(rec) if rec else None
        gm = (rec or {}).get("gpu_memory") or {}
        if age is None or age > 2 * scheduler.USER_CACHE_S or not gm.get("counter_ok"):
            return None
        return int(float(gm["card_mb"]) - float(gm["adapter_mb"]))
    except Exception:                                                    # noqa: BLE001 - the probe below still asks
        return None


def gpu_free_mb():
    """Free VRAM (MB) on the lead, or None if it cannot be determined: the box's own minute report when it is
    fresh, else SSH + gpu-free.ps1."""
    reported = _reported_free_mb()
    if reported is not None:
        return reported
    if not config.LEAD_BOX_SSH_HOST:        # no ssh reach configured: nothing to ask
        return None
    cmd = [
        "ssh",
        *_key_args(),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        _target(),
        r"powershell -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\HAI\gpu-free.ps1",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as exc:
        notes.log(f"gpu-free probe failed: {str(exc)[:120]}")
        return None
    if out.returncode != 0:
        notes.log(f"gpu-free probe rc={out.returncode}: {(out.stderr or '').strip()[:120]}")
        return None
    try:
        return int(json.loads(out.stdout.strip()).get("free_mb"))
    except (ValueError, TypeError, AttributeError) as exc:
        notes.log(f"gpu-free probe parse error: {str(exc)[:80]} raw={out.stdout.strip()[:80]!r}")
        return None


def restart_ollama():
    """Trigger the lead's documented 'Ollama Serve' scheduled task over SSH to (re)start Ollama when the
    box is powered ON but Ollama isn't serving (the flaky AtLogon `ollama app.exe`/0x1 autostart — see
    RUNBOOK 'The lead Ollama gotchas'). This is the runbook-prescribed remote recovery; a bare
    `ollama serve` over SSH would not persist. Returns True only if the task was triggered (i.e. the box
    is SSH-reachable); False if unreachable/asleep or on any error, so callers can fall through to a WOL
    wake instead. ROCm needs the task's interactive console session, so we cannot serve it headlessly here."""
    if not config.LEAD_BOX_SSH_HOST:        # no ssh reach configured: nothing to ask
        return False
    cmd = [
        "ssh",
        *_key_args(),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        _target(),
        f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {_RESTART_PS_B64}",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as exc:
        notes.log(f"restart_ollama ssh failed (box asleep/unreachable?): {str(exc)[:120]}")
        return False
    if out.returncode != 0:
        notes.log(f"restart_ollama rc={out.returncode}: {(out.stderr or '').strip()[:120]}")
        return False
    notes.log("restart_ollama: triggered 'Ollama Serve' task on %s" % config.LEAD_BOX_LABEL)
    return True


def start_comfy():
    """Trigger the lead's 'P20 Worker ComfyUI' scheduled task over SSH.

    ComfyUI is not a persistent service (launched per-job). This ensures it starts even if no
    render job is currently in flight, avoiding failures where the host is awake but the server
    process is not running.

    Uses the same launch path as the worker itself to maintain a single lifecycle. Returns True
    only if the task was triggered (SSH reachable); False if unreachable or on any error,
    allowing callers to fall back to WOL + polling."""
    if not config.LEAD_BOX_SSH_HOST:        # no ssh reach configured: nothing to ask
        return False
    cmd = [
        "ssh",
        *_key_args(),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        _target(),
        f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {_COMFY_PS_B64}",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as exc:
        notes.log(f"start_comfy ssh failed (box asleep/unreachable?): {str(exc)[:120]}")
        return False
    if out.returncode != 0:
        notes.log(f"start_comfy rc={out.returncode}: {(out.stderr or '').strip()[:120]}")
        return False
    notes.log("start_comfy: triggered 'P20 Worker ComfyUI' task on %s" % config.LEAD_BOX_LABEL)
    return True


# The factory's tree on the lead box: its FOUNDRY_ROOT device setting (<prefix>_FOUNDRY_ROOT, e.g. PC1_FOUNDRY_ROOT);
# "" = the lead has no foundry reachable this way, and every launch here says so.
FOUNDRY_ROOT = config.device_setting(config.LEAD_BOX, "FOUNDRY_ROOT")
FOUNDRY_BIN = FOUNDRY_ROOT + r"\bin" if FOUNDRY_ROOT else ""
FOUNDRY_JOBS_DIR = FOUNDRY_ROOT + r"\jobs" if FOUNDRY_ROOT else ""


def _lead_python(script, timeout=60, what="lead_python"):
    """Run a Python script on the lead by piping it to the remote interpreter.

        Piping via stdin avoids nested shell quoting issues (bash -> cmd -> powershell)
        that can cause silent failures. Inside the script, use subprocess with a LIST
        argv for any further commands to remain quoting-safe.

        Returns:
            tuple: (ok, stdout) on success; (False, message) on error.
    """
    if not config.LEAD_BOX_SSH_HOST:        # no ssh reach configured: nothing to ask
        return False, ("this head has no ssh reach into %s, the lead's machine (its %s_SSH_HOST setting is empty)"
                       % (config.LEAD_BOX_LABEL, config._roster_device(config.LEAD_BOX).get("prefix") or "PC1"))
    cmd = [
        "ssh",
        *_key_args(),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        _target(),
        "python.exe -",
    ]
    try:
        out = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        notes.log(f"{what} ssh failed: {str(exc)[:120]}")
        return False, str(exc)[:200]
    if out.returncode != 0:
        notes.log(f"{what} rc={out.returncode}: {(out.stderr or '').strip()[:160]}")
        return False, (out.stderr or out.stdout or "").strip()[:300]
    return True, (out.stdout or "").strip()


_WRITE_SPEC_BODY = '''
import hashlib, os, sys
if os.path.exists(path):
    print("EXISTS " + path)
    sys.exit(0)
with open(path, "w", encoding="utf-8", newline="\\n") as f:
    f.write(body)
raw = open(path, "rb").read()
print("SHA " + hashlib.sha256(raw).hexdigest())
print("BYTES " + str(len(raw)))
'''


def _write_verified_spec(path, body_text, what):
    """Write `body_text` to `path` on the lead and prove the bytes landed intact.

    The remote hashes what actually landed and we compare against the same hash computed here. That
    check is not ceremony: a job spec is nothing but Windows paths (the lead's foundry and dataset
    roots), which is exactly the content class the backslash-eating-write curse targets —
    and that entry's rule is "after ANY generated write, verify the actual bytes, not just that it
    parsed".

    Refuses to overwrite: a spec names its own .state.json and .log, so clobbering one orphans the
    audit trail of a run that may still be going.

    Returns (ok, path_or_err).
    """
    script = ("path = %r\nbody = %r\n" % (path, body_text)) + _WRITE_SPEC_BODY
    ok, out = _lead_python(script, timeout=45, what=what)
    if not ok:
        return False, out
    if out.startswith("EXISTS"):
        return False, "a spec already exists at %s — pick another name" % path
    want = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
    got = ""
    for line in out.splitlines():
        if line.startswith("SHA "):
            got = line[4:].strip()
    if got != want:
        notes.log(f"{what} BYTE MISMATCH {path}: want {want[:12]} got {got[:12]}")
        return False, ("the spec did not land byte-identical (wanted %s, got %s) — refusing to "
                       "launch against it" % (want[:12], got[:12]))
    notes.log("%s: %s (%s verified)" % (what, path, want[:12]))
    return True, path


_START_FOUNDRY_BODY = '''
import subprocess
cmd = 'python "' + foundry + '" "' + path + '"'
# /rl HIGHEST is required, not cosmetic: without it the task gets a limited token, and
# Win32_Process.CommandLine reads NULL for processes it did not spawn, so a live trainer can read as dead.
# /create /f RE-REGISTERS; `schtasks /change /tr` on an interactive task prompts for a password
# and blocks over ssh. Recreate, don't change.
c = subprocess.run(["schtasks", "/create", "/f", "/tn", task, "/tr", cmd,
                    "/sc", "once", "/st", "00:00", "/rl", "HIGHEST"],
                   capture_output=True, text=True)
print("CREATE rc=" + str(c.returncode) + " " + ((c.stdout or c.stderr) or "").strip()[:120])
if c.returncode == 0:
    r = subprocess.run(["schtasks", "/run", "/tn", task], capture_output=True, text=True)
    print("RUN rc=" + str(r.returncode) + " " + ((r.stdout or r.stderr) or "").strip()[:120])
'''


def _launch_via_schtasks(runner_py, spec_path, task_name, what):
    """Run `runner_py <spec_path>` via schtasks to avoid sshd session teardown killing detached children."""
    script = ("foundry = %r\npath = %r\ntask = %r\n"
              % (runner_py, spec_path, task_name)) + _START_FOUNDRY_BODY
    ok, out = _lead_python(script, timeout=45, what=what)
    if not ok:
        return False, out
    if "CREATE rc=0" not in out:
        return False, "schtasks refused the task definition: %s" % out[:200]
    notes.log("%s: launched %r -> %s" % (what, task_name, spec_path))
    return True, out


# Training runs reach the factory as ORDERS through the desk (tools._queue_training). The direct
# spec write and launch that once lived here went with that change.

# The lead's dataset root is its DATASET_ROOT device setting (<prefix>_DATASET_ROOT); "" = no dataset work there.
# Keep dataset jobs outside FOUNDRY_JOBS_DIR: render workers glob that tree for
# *.state.json to check GPU load; a dataset job there would be mistaken for a live
# training run and block renders.
DATASET_OUT_ROOT = config.device_setting(config.LEAD_BOX, "DATASET_ROOT")
DATASET_JOBS_DIR = DATASET_OUT_ROOT + r"\_jobs" if DATASET_OUT_ROOT else ""
# The dataset runner, and beside it the source registry it merges (dataset-sources.json):
# RUNBOOK-Training-Dataset-Acquisition. The registry read builds its path from this one.
DATASET_PY = FOUNDRY_BIN + r"\p20_dataset.py" if FOUNDRY_BIN else ""

# The array share a finished dataset is kept on, as the factory reaches it. Empty means no archive
# target is configured, and the spec carries no archive path.
DATASET_ARCHIVE_UNC = os.environ.get("DATASET_ARCHIVE_UNC", "")


def write_dataset_spec(name, body_text):
    """Write a dataset job spec to <DATASET_ROOT>\\_jobs\\<name>.spec.json on the lead. Returns (ok, path_or_err)."""
    if not DATASET_JOBS_DIR:
        return False, "the lead box has no DATASET_ROOT device setting"
    return _write_verified_spec("%s\\%s.spec.json" % (DATASET_JOBS_DIR, name),
                                body_text, "write_dataset_spec")


def start_dataset_job(spec_path):
    """Launch the dataset runner against a spec. Returns (ok, output)."""
    if not DATASET_PY:
        return False, "the lead box has no FOUNDRY_ROOT device setting"
    return _launch_via_schtasks(DATASET_PY, spec_path, "P20 Dataset Job", "start_dataset_job")


_DATASET_STATE_BODY = '''
import glob, json, os
out = []
for p in sorted(glob.glob(jobs_dir + "\\\\*.state.json"), key=os.path.getmtime, reverse=True)[:6]:
    try:
        with open(p, encoding="utf-8") as f:
            s = json.load(f)
        s["_mtime_age_s"] = int(__import__("time").time() - os.path.getmtime(p))
        out.append(s)
    except Exception as exc:
        out.append({"name": os.path.basename(p), "phase": "unreadable", "detail": str(exc)[:80]})
print("JSON " + json.dumps(out))
'''


_DATASET_REGISTRY_BODY = '''
import json, sys
# Ask the RUNNER for its merged view rather than reading dataset-sources.json directly. The runner
# merges that file over its own built-in defaults, so reading the file alone shows a SUBSET and a
# built-in source stays invisible: one list, read from the one place that builds it.
# Falls back to the raw file if the runner cannot be imported, so an unimportable runner degrades
# to the old behaviour rather than reporting no sources at all.
try:
    sys.path.insert(0, path.rsplit("\\\\", 1)[0])
    import p20_dataset as _p
    print("JSON " + json.dumps({k: v for k, v in _p.SOURCES.items() if isinstance(v, dict)}))
except Exception:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        print("JSON " + json.dumps({k: v for k, v in d.items() if isinstance(v, dict)}))
    except Exception as exc:
        print("ERR " + str(exc)[:140])
'''


def dataset_registry():
    """
        Return the source registry (dataset-sources.json) as a dict. Returns {}
        on any error.

        Sources are loaded from this single file to avoid keeping three separate
        definitions in sync: the tool schema enum, this module's dict, and the
        runner's config.
    """
    if not DATASET_PY:
        return {}
    path = DATASET_PY.rsplit("\\", 1)[0] + "\\dataset-sources.json"
    ok, out = _lead_python(("path = %r\n" % path) + _DATASET_REGISTRY_BODY,
                                 timeout=30, what="dataset_registry")
    if not ok:
        return {}
    for line in out.splitlines():
        if line.startswith("JSON "):
            try:
                return json.loads(line[5:])
            except ValueError:
                return {}
    return {}


def dataset_jobs():
    """Recent dataset jobs, newest first, as a list of state dicts. Fail-soft: ([] on any error).

    Read over SSH rather than through a queue file because the runner is launched fire-and-forget
    (the queue_training shape) — the state file on the lead IS the record, and copying it to a
    second place would only create somewhere for the two to disagree.
    """
    if not DATASET_JOBS_DIR:
        return []
    script = ("jobs_dir = %r\n" % DATASET_JOBS_DIR) + _DATASET_STATE_BODY
    ok, out = _lead_python(script, timeout=30, what="dataset_jobs")
    if not ok:
        return []
    for line in out.splitlines():
        if line.startswith("JSON "):
            try:
                return json.loads(line[5:])
            except ValueError:
                return []
    return []


_TRAINER_RUNNING_BODY = r'''
import subprocess
# subprocess with a LIST argv - the PowerShell text is one argument and never touches a shell,
# so $_ survives (bash eats it in double quotes).
# Match the TRAINER, not "anything living under ai-toolkit": a venv-path pattern is permanently TRUE
# on the lead (the stall-rescue daemon and the render worker run from the same venv, and the querying
# PowerShell's own CommandLine carries the pattern), so it would refuse every run.
# Requiring Name -eq 'python.exe' drops the self-match and the cmd.exe wrappers. The trainer is then
# identified by its ENTRY POINT, `run.py`, and nothing else.
#
# Do NOT add an '*ai-toolkit*' term: the launcher does `cd /d` into the toolkit first, so the live
# command line is RELATIVE (`venv\python.exe  -u run.py config\<job>.yaml`) and such a term matches
# NOTHING. Failing to detect is worse than over-matching: it would let a SECOND run start on the same card.
# Excluded by this match: p20_stall_rescue.py, p20_render_worker.py, p20_foundry.py, glances.
ps = ("Get-CimInstance Win32_Process | "
      "Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*run.py*' } | "
      "Measure-Object | Select-Object -ExpandProperty Count")
r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
print("COUNT " + (r.stdout or "0").strip())
'''


def trainer_running():
    """True only if a training process is POSITIVELY seen on the lead.

    Fail-open (unknown -> False) to match gaming_busy: an unreachable probe must not permanently
    block queueing. Callers that need certainty should also consult the GPU heartbeat.
    """
    ok, out = _lead_python(_TRAINER_RUNNING_BODY, timeout=30, what="trainer_running")
    if not ok:
        return False
    for line in out.splitlines():
        if line.startswith("COUNT "):
            try:
                return int(line[6:].strip()) > 0
            except ValueError:
                return False
    return False


def gaming_busy(min_free_mb=None):
    """Return True if GPU VRAM is insufficient for the workload.

        Uses `config.LEAD_BOX_MIN_FREE_MB` by default (sized for a 27B model).
        Image generation uses `config.IMG_MIN_FREE_MB`, which has a lower threshold.

        Fails open: returns False on probe errors or unreachable GPUs to maintain
        usability.
    """
    threshold = min_free_mb or config.LEAD_BOX_MIN_FREE_MB
    free = gpu_free_mb()
    if free is None:
        return False
    if free >= threshold:
        return False
    # Low free VRAM. But is it a GAME (a non-Ollama process) or just ANOTHER Ollama model holding the
    # card? If Ollama already has a model resident, the VRAM is Ollama's — it will swap that model for
    # the 27B — so this is NOT gaming-busy. Only a low-free GPU with NO Ollama model resident looks like
    # a real game. (Without this, e.g. a resident medgemma:27b or qwen3:14b made us wrongly "route away".)
    try:
        if ollama_client.models_loaded(config.HEAVY_HOST):
            notes.log(f"{config.LEAD_BOX_LABEL} GPU full ({free}MB free) but an Ollama model is resident (swappable) -> not gaming")
            return False
    except Exception:
        pass
    # A resident ollama or Strata window (e.g. Flash-Next) counts as "ours" and must not be treated as gaming; it either joins the turn or closes if idle.
    try:
        import scheduler as _sched
        own = _sched.own_window(_sched.LEAD_BOX)
        if own:
            notes.log(f"{config.LEAD_BOX_LABEL} GPU full ({free}MB free) but Kirk's own window holds it ({own}) -> not gaming")
            return False
    except Exception:
        pass
    notes.log(f"{config.LEAD_BOX_LABEL} GPU busy: {free}MB free < {threshold}MB and no Ollama model resident -> routing away (assume game)")
    return True


def warm():
    """True if the lead is reachable AND the 27B is already resident in VRAM.

    A warm 27B answers with no wake and no model load, so Auto routing prefers it (better answers
    for free). A resident model also means the GPU is busy with US, not a game, so no gaming-busy
    check is needed on this path. Fail-soft: any error -> False (fall back to the normal 8B cascade).
    """
    try:
        if not ollama_client.reachable(config.HEAVY_HOST):
            return False
        family = config.HEAVY_MODEL.split(":")[0]
        return any(str(m).startswith(family) for m in ollama_client.models_loaded(config.HEAVY_HOST))
    except Exception:
        return False
