"""Factory health daemon: NVIDIA-card watchdog.

One implementation per factory, parameterised by env vars. Same contract on every host; duties differ by hardware failure mode (NVIDIA vs AMD).

Env vars:
    FOUNDRY_ROOT          Factory root path.
    FOUNDRY_FLEET_HOST    Hostname to report under (defaults to COMPUTERNAME).
    FOUNDRY_TARGET_CLOCK  A clock LOCK in MHz (nvidia-smi -lgc N); wins when set.
    FOUNDRY_CLOCK_FLOOR   At least N MHz; with FOUNDRY_CLOCK_CEILING a band (-lgc lo,hi).
    FOUNDRY_CLOCK_CEILING At most N MHz.    None of the three set = no clock duty: the card's own boost.
    (the three are settings in etc\\lab.env beside the foundry root, read through clock_policy / foundry_paths)

Duties:
1. CLOCK POLICY: Re-applies the box's lock or band when a reading above its ceiling shows it is gone (`nvidia-smi -lgc`
   is non-persistent); a floor-only policy is applied once at start, since a clock reading cannot verify a floor.
2. GPU-LOST DETECTION: Detects "GPU is lost" state invisible to Windows Status.
3. FROZEN-TELEMETRY DETECTION: Flags dead NVML handles by detecting static power readings (identical to 2 dp) across consecutive samples.
4. CAPTION PROGRESS WATCH: Reports stalled runs where process is alive but output has ceased.
5. FAN CONTROL: FanControl running (service on headless, app on logged-on) AND case fans reading rpm in telemetry. Absent = ALERT.
6. HOTSPOT: Card's hotspot and core->hotspot gap from newest telemetry row.
7. MODEL MEMORY: Unloads resident ollama models (except McCoy's) if projected free VRAM falls under reserve to prevent eviction.

Constraints:
- Never reboots, kills processes, or touches other hosts. Duty 7 asks ollama to unload via API (finishes in-flight requests first).
- Reads STATED run (CURRENT_RUN.json); never infers current run by globbing."""
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_paths as P
import p20_status as status

POLL_SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
TELEM = P.TELEMETRY_DIR
CSV = os.path.join(TELEM, "gpu_telemetry.csv")
LOG = os.path.join(TELEM, "health_daemon.log")
HEARTBEAT = os.path.join(TELEM, "stall_rescue.heartbeat")   # the name PREFLIGHT looks for
STOP = os.path.join(TELEM, "STOP_HEALTH")
STATED_RUN = P.CURRENT_RUN
CURRENT_RUN_STALE_S = 3600.0   # a stated run older than this no longer owns the lane
REPORT_URL = P.head_url() + "/fleet/rescue-state" if P.head_url() else ""
# No default host: prevents single-writer clobber when env var is missing.
FLEET_HOST = (os.environ.get("FOUNDRY_FLEET_HOST")
              or (os.environ.get("COMPUTERNAME") or "unknown").lower())

# --- duty 1: the clock policy -----------------------------------------------------------------
# Three settings, read the foundry way (clock_policy.py): a lock, or a floor and a ceiling. Unset = the duty is
# OFF and the card runs at its own boost. TARGET_CLOCK_MHZ is the ceiling the duty enforces (a reading above it is
# the proof the policy is gone); 0 = off.
import clock_policy as CP  # noqa: E402
CLOCK_POLICY = CP.box_policy()
TARGET_CLOCK_MHZ = CP.ceiling(CLOCK_POLICY) or 0
CLOCK_TOLERANCE = 30           # a reported clock may sit a bin either side of the requested value
MAX_RELOCKS = 6
CEILING_NOTE = "ceiling 1695-1750 MHz; 1750 died at 577 s, 1800 at 240 s, factory 11/11"

# --- duty 3: frozen telemetry -----------------------------------------------------------------
FROZEN_SAMPLES = 6             # consecutive byte-identical readings before calling it dead
# --- duty 4: caption progress -----------------------------------------------------------------
PROGRESS_STALL_SECS = 900.0    # a caption advances every ~3 s; 15 min of silence under load is wrong

_relocks = 0        # reported as rescues_today: a re-applied clock lock IS this box's rescue
_incidents = 0      # reported as stalls_today: conditions seen, whether or not we could act
_recent = []


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(TELEM, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def beat():
    """The file PREFLIGHT checks to decide whether a run is protected."""
    try:
        os.makedirs(TELEM, exist_ok=True)
        tmp = HEARTBEAT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now().isoformat())
        os.replace(tmp, HEARTBEAT)
    except OSError:
        pass


def smi(query, timeout=25):
    """nvidia-smi hangs on wedged cards instead of returning an error.
    The timeout is mandatory to prevent the health check itself from blocking."""
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=" + query,
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            return None, (p.stdout or "") + (p.stderr or "")
        return (p.stdout or "").strip(), ""
    except subprocess.TimeoutExpired:
        return None, "nvidia-smi TIMED OUT (wedged card signature)"
    except Exception as e:
        return None, repr(e)


def stated_run():
    """The current run is STATED, never inferred."""
    try:
        with open(STATED_RUN, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clock_owner(run, now=None):
    """The running order that declared it owns the card's clock (spec field `gpu_clock`, e.g. a stock-clock crash
    test), or None. Only a FRESH stated run counts, so the lock returns by itself when the order ends by any route
    (the order taker clears CURRENT_RUN) or its runner dies (the record goes stale)."""
    if not run or not run.get("spec_file"):
        return None
    try:
        age = ((now or datetime.datetime.now())
               - datetime.datetime.fromisoformat(run["updated"])).total_seconds()
    except Exception:
        return None
    if age > CURRENT_RUN_STALE_S:
        return None
    try:
        with open(run["spec_file"], encoding="utf-8") as f:
            clock = json.load(f).get("gpu_clock")
    except Exception:
        return None
    return (run.get("name"), clock) if clock not in (None, "") else None


def check_clock(run=None):
    """Duty 1. Returns (ok, detail). Re-applies the lock when it has gone, bounded. A target of 0 turns the duty off:
    (None, ...) - nothing measured, nothing re-applied. So does a running order that owns the clock (`clock_owner`)."""
    global _relocks
    if TARGET_CLOCK_MHZ <= 0:
        return None, "clock duty OFF (no FOUNDRY_TARGET_CLOCK or FOUNDRY_CLOCK_CEILING set)"
    owner = clock_owner(run)
    if owner:
        return None, "clock duty yielded to order %s (gpu_clock=%s)" % owner
    out, err = smi("clocks.sm,clocks.max.sm")
    if out is None:
        return None, err
    try:
        cur = int(out.split(",")[0].strip())
    except Exception:
        return None, "unparseable clocks: %r" % out
    # An idle card legitimately sits far below the lock; the lock caps the CEILING, so only a
    # reading ABOVE target+tolerance proves it is gone.
    if cur <= TARGET_CLOCK_MHZ + CLOCK_TOLERANCE:
        return True, "clock %d MHz (<= ceiling %d)" % (cur, TARGET_CLOCK_MHZ)
    if _relocks >= MAX_RELOCKS:
        return False, ("clock %d MHz ABOVE ceiling %d and re-lock budget spent (%d) -- REPORTING ONLY. %s"
                       % (cur, TARGET_CLOCK_MHZ, MAX_RELOCKS, CEILING_NOTE))
    _relocks += 1
    try:
        subprocess.run(["nvidia-smi"] + _policy_args(),
                       capture_output=True, text=True, timeout=30)
    except Exception as e:
        return False, "re-lock FAILED: %r" % (e,)
    chk, _ = smi("clocks.sm")
    log("CLOCK POLICY WAS ABSENT (read %d MHz) -- re-applied %s (relock %d/%d). %s"
        % (cur, _clock_duty_label(), _relocks, MAX_RELOCKS, CEILING_NOTE))
    return False, "re-applied %s; now %s MHz" % (_clock_duty_label(), chk or "?")


def _smi_run(args):
    """nvidia-smi with these arguments -> (ok, text); never raises (for clock_policy.card_clock_range)."""
    try:
        p = subprocess.run(["nvidia-smi"] + list(args), capture_output=True, text=True, timeout=30)
        return p.returncode == 0, " ".join(((p.stdout or "") + (p.stderr or "")).split())[:160]
    except Exception as e:                                             # noqa: BLE001
        return False, repr(e)[:160]


def _policy_args():
    """The nvidia-smi arguments that re-apply this box's policy: the band's own, else a lock at the ceiling."""
    if isinstance(CLOCK_POLICY, dict):
        args = CP.clock_args(CLOCK_POLICY, log, lambda: CP.card_clock_range(_smi_run))
        if args:
            return args
    return ["-lgc", str(TARGET_CLOCK_MHZ)]


def _clock_duty_label():
    if TARGET_CLOCK_MHZ <= 0:
        return "clock duty OFF"
    return "clock " + CP.label(CLOCK_POLICY if isinstance(CLOCK_POLICY, dict) else TARGET_CLOCK_MHZ)


def check_alive():
    """Duties 2 + 3. GPU-lost, and frozen telemetry that READS healthy."""
    out, err = smi("power.draw,clocks.sm,utilization.gpu,temperature.gpu")
    if out is None:
        low = (err or "").lower()
        if "lost" in low or "no devices" in low or "timed out" in low:
            return False, "GPU LOST / unreachable: %s" % err.strip()[:120]
        return None, err
    _recent.append(out)
    del _recent[:-FROZEN_SAMPLES]
    if len(_recent) == FROZEN_SAMPLES and len(set(_recent)) == 1:
        return False, ("TELEMETRY FROZEN: %d identical samples (%s). A zombie NVML handle serves a "
                       "dead card's last reading forever -- health is CHANGE, not plausible values."
                       % (FROZEN_SAMPLES, out))
    return True, out


def check_progress(run):
    """Duty 4. Only meaningful while an order owns the lane."""
    if not run or run.get("state") in (None, "done", "idle"):
        return True, "no run owns the lane"
    src = (run.get("caption_src") or run.get("output_dir") or "").strip()
    if not src or not os.path.isdir(src):
        return True, "run states no watchable output dir"
    newest = 0.0
    for f in os.listdir(src):
        if f.lower().endswith(".txt"):
            try:
                newest = max(newest, os.path.getmtime(os.path.join(src, f)))
            except OSError:
                pass
    if not newest:
        return True, "no sidecars yet (run may still be loading)"
    age = time.time() - newest
    if age > PROGRESS_STALL_SECS:
        return False, "no new sidecar for %.0f s while '%s' owns the lane" % (age, run.get("name"))
    return True, "last sidecar %.0f s ago" % age


# --- duties 5 + 6: fan control and hotspot, ONE derivation shared with Mothership's stall rescue ---------------
from p20_board_health import check_fans, fan_processes, hotspot_note, last_row, tasklist  # noqa: E402,F401


# Unloads non-keeper models if free VRAM trends low, ensuring Plex transcodes retain priority over model loading.
# Unreadable VRAM status fails closed to single-model mode to avoid OOM during transcoding.
# A setting (FOUNDRY_PLEX_MODEL_GUARD=1 on the box that shares its card with a media server); off by default, no box
# is special by name.
PLEX_GUARD = P.setting("FOUNDRY_PLEX_MODEL_GUARD", "0") not in ("0", "", "false", "False")
OLLAMA_URL = os.environ.get("FOUNDRY_OLLAMA_URL", "http://127.0.0.1:11434")
KEEPER = os.environ.get("FOUNDRY_RESIDENT_KEEPER", "tev1:latest")
RESERVE_MIB = 512
_prev_free = [None]


def resident_models(timeout=5):
    """[(name, size_vram MiB)] ollama has loaded, or None when ollama could not be asked."""
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/ps", timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        return [(m.get("name") or m.get("model") or "", int((m.get("size_vram") or 0) // (1024 * 1024)))
                for m in d.get("models") or []]
    except Exception:
        return None


def card_free_mib():
    """Free card memory in MiB from nvidia-smi (total - used, every process on the card), or None."""
    out, _err = smi("memory.used,memory.total", timeout=15)
    try:
        used, total = [float(x) for x in out.splitlines()[0].split(",")]
        return total - used
    except Exception:
        return None


def two_or_one(resident, free_mib, prev_free_mib, keeper=None, reserve=RESERVE_MIB):
    """-> (models to unload, verdict). The one derivation of duty 7; pure. With two or more models on the card and the
    free memory projected one poll ahead under `reserve`, everything but the keeper goes (the keeper absent: everything
    but the largest, the dearest to reload). Free memory unreadable = one at a time."""
    keeper = KEEPER if keeper is None else keeper
    on_card = [(n, v) for n, v in resident if v > 0]
    if len(on_card) < 2:
        return [], "one model or none on the card"
    keep = keeper if any(n == keeper for n, _v in on_card) else max(on_card, key=lambda x: x[1])[0]
    extra = [n for n, _v in on_card if n != keep]
    if free_mib is None:
        return extra, "card memory unreadable - one at a time (Plex first)"
    fall = max(0.0, prev_free_mib - free_mib) if prev_free_mib is not None else 0.0
    projected = free_mib - fall
    if projected < reserve:
        return extra, ("free %d MiB, falling %d MiB per poll, projected %d < reserve %d - one at a time"
                       % (free_mib, fall, projected, reserve))
    return [], "two models: free %d MiB, projected %d >= reserve %d" % (free_mib, projected, reserve)


def unload_model(name, timeout=30):
    """Ollama's own unload (empty prompt, keep_alive 0): a runner mid-request is released when it finishes."""
    try:
        rq = urllib.request.Request(OLLAMA_URL + "/api/generate",
                                    data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                                    headers={"Content-Type": "application/json"})
        urllib.request.urlopen(rq, timeout=timeout).read()
        return True
    except Exception as e:
        log("duty 7: unload %s failed (%r)" % (name, e))
        return False


def check_plex_guard(reserve=RESERVE_MIB):
    """(None, note) when off or ollama cannot be asked; (True, note) otherwise - acting when two_or_one says so."""
    if not PLEX_GUARD:
        return None, "two-or-one guard off on this box"
    resident = resident_models()
    free = card_free_mib()
    prev, _prev_free[0] = _prev_free[0], free
    if resident is None:
        return None, "two-or-one guard: ollama could not be asked"
    unload, verdict = two_or_one(resident, free, prev, reserve=reserve)
    done = [n for n in unload if unload_model(n)]
    if unload:
        log("duty 7 (Plex first): %s; unloaded %s" % (verdict, ", ".join(done) or "nothing (unload failed)"))
    return True, "models: %s%s" % (verdict, ("; unloaded " + ", ".join(done)) if done else "")


def report(state, detail, watching=""):
    """Publish to /fleet/rescue-state. Contract: host, alive, pid, disabled, last,
    last_tflops, stalls_today, rescues_today, watching. `last` is the human line;
    orchestrator derives status from `alive`, `disabled`, and age (see _rescue_verdict)."""
    try:
        body = json.dumps({
            "host": FLEET_HOST,
            "alive": True,                       # we are executing, so we are alive by definition
            "pid": os.getpid(),
            "disabled": False,                   # this daemon has no self-disable path
            "last": detail[:300],
            "stalls_today": _incidents,
            "rescues_today": _relocks,           # a re-applied clock lock IS this box's rescue
            "watching": watching[:200],
        }).encode()
        rq = urllib.request.Request(REPORT_URL, data=body,
                                    headers={"Content-Type": "application/json"})
        urllib.request.urlopen(rq, timeout=8).read()
    except Exception:
        pass          # fail-soft: a watchdog must never die because a report failed


# Alerts are edge-triggered (not level-triggered) to prevent spam on persistent conditions.
# Only the transition into/ out of a state triggers a DM, plus one repeat after ALERT_REPEAT_SECS.
ALERT_REPEAT_SECS = 3600.0
_alert_state = {}


def dm_alert(key, active, message):
    """Edge-triggered Discord alert. `key` identifies the condition so several can be tracked."""
    now = time.time()
    prev = _alert_state.get(key)
    if active:
        if prev is None or (now - prev) >= ALERT_REPEAT_SECS:
            _alert_state[key] = now
            _send_dm("ALERT %s" % message)
    elif prev is not None:
        _alert_state.pop(key, None)
        _send_dm("RECOVERED %s" % key)


def _send_dm(text):
    """Fail-soft DM through the foundry's relay. Imported lazily: the daemon must start and keep
    watching even if the foundry module or its creds are unavailable."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import p20_foundry as F
        token, user_id = F._dm_creds()
        if not (token and user_id):
            return
        body = json.dumps({"user_id": user_id,
                           "message": "%s [health] %s" % (F._host_tag(), text)}).encode()
        rq = urllib.request.Request(F.DM_URL, data=body,
                                    headers={"Content-Type": "application/json",
                                             "X-DM-Token": token, "Host": F.DM_HOST})
        urllib.request.urlopen(rq, timeout=10).read()
    except Exception as e:
        log("WARNING: DM alert failed (%r) -- continuing" % (e,))


def main():
    log("health daemon up (poll %.0fs, %s, host %s)" % (POLL_SECS, _clock_duty_label(), FLEET_HOST))
    if isinstance(CLOCK_POLICY, dict) and TARGET_CLOCK_MHZ <= 0:
        args = CP.clock_args(CLOCK_POLICY, log, lambda: CP.card_clock_range(_smi_run))
        ok, text = _smi_run(args) if args else (False, "no arguments")
        log("clock %s applied once at start (%s; a floor cannot be verified from a clock reading): %s"
            % (CP.label(CLOCK_POLICY), "ok" if ok else "NOT applied", text))
    while True:
        if os.path.exists(STOP):
            log("STOP flag present -- exiting"); return
        beat()
        run = stated_run()
        clock_ok, clock_note = check_clock(run)
        alive_ok, alive_note = check_alive()
        prog_ok, prog_note = check_progress(run)
        live_csv = P.gpu_telemetry_live()
        t_head, t_row = last_row(live_csv)
        fan_ok, fan_note = check_fans(tasklist(), t_head, t_row)
        hot_note = hotspot_note(t_head, t_row)
        try:
            _g_ok, guard_note = check_plex_guard()
        except Exception as e:                       # a guard that can crash its host is worse than none (12)
            guard_note = "two-or-one guard error (%s)" % type(e).__name__

        global _incidents
        bad = [n for ok, n in ((alive_ok, alive_note), (prog_ok, prog_note), (fan_ok, fan_note)) if ok is False]
        if clock_ok is False:
            bad.append(clock_note)
        state = "ALERT" if bad else "watching"
        if bad:
            _incidents += 1
            log("ALERT: " + " | ".join(bad))
        # `last` is the human line the card shows; `watching` says what this daemon is FOR, so a
        # reader can tell a BigHonker card from a Mothership one without knowing the internals.
        report(state, (" | ".join(bad) if bad else ("card healthy; " + clock_note)) + "; " + hot_note + "; " + fan_note
               + ("; " + guard_note if PLEX_GUARD else ""),
               watching="%s, GPU-lost, frozen telemetry, caption progress, fan control, hotspot%s"
                        % (_clock_duty_label(), ", two-or-one models (Plex)" if PLEX_GUARD else ""))

        # One alert key per DUTY, so a clock problem and a lost card are tracked (and recover)
        # independently instead of one masking the other.
        dm_alert("gpu-unhealthy", alive_ok is False, alive_note)
        dm_alert("clock-lock", clock_ok is False, clock_note)
        dm_alert("caption-progress", prog_ok is False, prog_note)
        dm_alert("fan-control", fan_ok is False, fan_note)

        try:
            status.publish(
                # Component name is per-HOST; duplicate names cause silent data overwrite.
                "health_daemon_%s" % FLEET_HOST, state,
                notes=("; ".join(bad) if bad else "card healthy, " + (
                    "clock lock in force" if clock_ok else clock_note)),
                extra={"clock_lock_mhz": TARGET_CLOCK_MHZ, "clock_policy": CLOCK_POLICY, "relocks": _relocks,
                       "clock": clock_note, "gpu": alive_note, "progress": prog_note,
                       "fans": fan_note, "hotspot": hot_note, "models": guard_note},
                sources=[
                    status.source("gpu_telemetry", live_csv, stale_after=120.0,
                                  note="this daemon's OWN constant, not a same-named file next door",
                                  absent_ok=True),
                    status.source("current_run", STATED_RUN, stale_after=CURRENT_RUN_STALE_S,
                                  note="which order owns the lane", absent_ok=True),
                ])
        except Exception:
            pass
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
