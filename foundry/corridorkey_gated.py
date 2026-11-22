"""Collision-gated CorridorKey launcher.

Required entry point for GPU usage to prevent cross-context collisions with
ComfyUI/Ollama. Uses a file-based hold (`corridorkey.hold`) with ISO-8601
expiry, refreshed every 5 minutes (auto-expires after 15 min crash-safety).
Worker defers new renders while hold is active.

Usage:
    python corridorkey_gated.py <folder-of-clips> [extra args]

Env:
    CK_EXTRA: uv extra to use (e.g., "cuda" on NVIDIA). Defaults to CPU torch
              if unset.
"""
import datetime
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import foundry_paths as P  # noqa: E402
CK_DIR = P.CORRIDORKEY_DIR
HOLD = os.path.join(P.HOLD_DIR, "corridorkey.hold")
COMFY = "http://127.0.0.1:8189"
STATE_GLOB = os.path.join(P.JOB_STATE, "*.state.json")
WAIT_CAP_SECS = 2 * 3600      # give up after 2h of continuous busy
QUIET_CHECKS = 2              # consecutive 30s-apart quiet reads before launching
HOLD_TTL_MIN = 15
REFRESH_SECS = 300


def log(msg):
    print("[ck-gate %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def write_hold():
    expiry = (datetime.datetime.utcnow() + datetime.timedelta(minutes=HOLD_TTL_MIN)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(HOLD), exist_ok=True)
    with open(HOLD, "w", encoding="ascii") as f:
        f.write(expiry)


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
        return True     # can't tell -> assume busy (never fight for the card)


def foundry_active():
    import glob
    for sp in glob.glob(STATE_GLOB):
        try:
            with open(sp, encoding="utf-8") as f:
                if json.load(f).get("phase") in ("PAUSE", "RENDER", "RESUME", "LADDER"):
                    return True
        except Exception:
            continue
    return False


def comfy_busy():
    try:
        with urllib.request.urlopen(COMFY + "/queue", timeout=3) as r:
            q = json.load(r)
        return bool(q.get("queue_running") or q.get("queue_pending"))
    except Exception:
        return False    # comfy down = idle (worker stops it between batches)


OLLAMA = "http://localhost:11434"


def ollama_resident():
    """A resident LLM holds a live GPU context that can collide with other GPU work -- residency counts as busy; unload before proceeding."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=3) as r:
            return bool(json.load(r).get("models"))
    except Exception:
        return False    # ollama down = no context


def unload_ollama():
    """Politely evict resident models (keep_alive:0), same pattern as worker v1.11. The dispatch
    gate keeps NEW chats off Mothership while our hold is up (worker heartbeats 'busy')."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=5) as r:
            models = [m["name"] for m in json.load(r).get("models", [])]
        for m in models:
            body = json.dumps({"model": m, "keep_alive": 0}).encode()
            req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30):
                pass
            log("ollama unload requested: %s" % m)
    except Exception as e:
        log("ollama unload skipped (%r)" % (e,))


def card_quiet():
    return not (trainer_running() or foundry_active() or comfy_busy() or ollama_resident())


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    write_hold()                      # claim FIRST, then verify quiet
    log("hold written -> waiting for a quiet card")
    stop = threading.Event()
    try:
        t0, quiet = time.time(), 0
        while quiet < QUIET_CHECKS:
            if time.time() - t0 > WAIT_CAP_SECS:
                log("card stayed busy for %dh — giving up (re-run later)" % (WAIT_CAP_SECS // 3600))
                sys.exit(1)
            if ollama_resident():
                unload_ollama()     # nudge, then re-check on the next loop
            if card_quiet():
                quiet += 1
                log("quiet check %d/%d" % (quiet, QUIET_CHECKS))
            else:
                quiet = 0
                log("card busy (trainer/foundry/render/ollama) — waiting")
            if quiet < QUIET_CHECKS:
                write_hold()
                time.sleep(30)

        def refresher():
            while not stop.wait(REFRESH_SECS):
                try:
                    write_hold()
                except Exception:
                    pass
        threading.Thread(target=refresher, daemon=True).start()

        extra = os.environ.get("CK_EXTRA", "").strip()
        # --no-sync is required: venv contains AMD ROCm torch not in uv.lock;
        # without it, `uv run` re-syncs and silently reverts to CPU torch.
        cmd = ["uv", "run", "--no-sync"] + (["--extra", extra] if extra else []) + \
              ["corridorkey", "wizard"] + sys.argv[1:]
        log("launching: %s" % " ".join(cmd))
        rc = subprocess.run(cmd, cwd=CK_DIR).returncode
        log("corridorkey exited rc=%d" % rc)
        sys.exit(rc)
    finally:
        stop.set()
        try:
            os.remove(HOLD)
            log("hold released")
        except OSError:
            pass


if __name__ == "__main__":
    main()
