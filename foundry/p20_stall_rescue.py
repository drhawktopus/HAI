r"""Stall rescue daemon: monitors GPU telemetry to detect stalled training/rendering
on AMD RDNA3 hardware and spawns fresh processes to restore throughput.

This addresses a known driver scheduler bug where the compute engine locks at high
clock but low power (~200-230 W), reducing step rate by ~8x. Spawning an external
process forces the scheduler to flip back to normal efficiency (~385 W).

Detection heuristic:
    STALL = (power < 300 W) AND (core_clock > 2900 MHz) AND (gpu_load > 90%)
            AND (step_number is present).
    High clock requirement excludes idle, startup, and checkpoint save states.

Behavior:
    - Spawns FRESH processes for bursts; in-process contexts degrade alongside the trainer.
    - Prophylactic bursts trigger on idle->training transitions or new checkpoint files,
      guarded by PROPH_MIN_GAP and active-step checks.
    - Burst-echo guard ignores telemetry rows during/after a burst to prevent false clears.
    - Render-mode support: handles stalls in ComfyUI rendering queue when no training step
      is present.
    - Self-disable mechanism triggers only on repeated failures of FAST bursts, not on
      degraded spawns (duds).

Safety:
    - Non-blocking payload with hard kill limit.
    - Stop via the foundry's var\state\telemetry\STOP_RESCUE flag.
"""
import io
import os, sys, time, glob, datetime, subprocess, json, urllib.request

# Make this module's own directory importable before the shared import, the same line the worker
# and the health daemon already carry: an isolated interpreter ignores PYTHONPATH and the cwd, and
# a guard that fails to START is strictly worse than the bug it was sent to fix.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_lock          # singleton-lock liveness, shared with the runner and the worker
# The settings reader lives in the foundry's bin, not beside this daemon (it runs from the trainer's folder), so that bin
# is searched after this one. Without it the daemon watches but has no head to report to and no relay to message.
_FOUNDRY_BIN = (os.path.join(os.environ["FOUNDRY_ROOT"], "bin") if os.environ.get("FOUNDRY_ROOT")
                else os.path.dirname(os.path.abspath(__file__)))     # FOUNDRY_ROOT's bin, else the bin it runs from
if _FOUNDRY_BIN not in sys.path:
    sys.path.append(_FOUNDRY_BIN)
try:
    import foundry_paths as P  # the one settings reader
except ImportError:
    P = None

POLL_SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
# Poll at 5s: telemetry updates every ~11s.
# <5s wastes CPU re-reading stale rows; >5s increases stall detection latency.
# Every path here is a setting: this daemon runs OUTSIDE the foundry tree, so it reads the
# reader rather than restating a box's own path; without the reader, the environment; never a drive of its own.
_ROOT = P.FOUNDRY_ROOT if P else os.environ.get("FOUNDRY_ROOT", "")
_TOOLKIT = P.setting("FOUNDRY_AI_TOOLKIT", "") if P else os.environ.get("FOUNDRY_AI_TOOLKIT", "")
if not (_ROOT and _TOOLKIT):
    sys.exit("stall rescue: FOUNDRY_ROOT / FOUNDRY_AI_TOOLKIT unset (etc\\lab.env beside the foundry root, or the environment)")
TELEM = os.path.join(_ROOT, "var", "state", "telemetry")
CSV = os.path.join(TELEM, "gpu_telemetry.csv")
LOG = os.path.join(TELEM, "stall_rescue.log")
STOP = os.path.join(TELEM, "STOP_RESCUE")
PY = os.path.join(_TOOLKIT, "venv", "python.exe")
PAYLOAD = os.path.join(_TOOLKIT, "p20_rescue_burst.py")
OUTPUT_GLOB = os.path.join(_ROOT, "output", "image-lora", "*", "*.safetensors")

CONFIRM = 3               # consecutive stalled samples before acting (kills one-off blips).
                          # 2 -> 3 with POLL_SECS 15 -> 5: two samples at 5 s is only 10 s of
                          # evidence and would act on a blip. Three keeps ~15 s of evidence
                          # (was 30 s) while still HALVING time-to-detect.
COOLDOWN = 240.0          # seconds after a GRADED (fast-burst) rescue before re-arming detection rescues
SHORT_COOLDOWN = 60.0     # after an all-dud rescue attempt (no information gained)
BURST_SECS = 25.0
PAYLOAD_TIMEOUT = 60.0    # timeout: burst (25s) + overhead + 2x margin; avoids old 3m hang cost
SETTLE = 25.0             # grade wait (logger now writes every ~10 s)
STALE_SECS = 150.0
MAX_INEFFECTIVE = 3       # consecutive FAST-burst failures before self-disabling (duds never count)
MAX_DUD_RESPAWNS = 15    # v4.5: mood-night clusters ran past 10; retries cost ~5s each     # v4.2: evening spawn-quality clusters run long (5+ duds observed); retries cost ~5s each now that marginals abort at 3s
FLIP_TFLOPS = 14.0        # Below this TFLOP threshold, completed bursts failed to flip; treat as duds and skip cooldown sleep.
ECHO_GUARD_SECS = 35.0    # ignore telemetry rows written within a burst window + this tail
PROPH_DELAY = 60.0        # fire prophylactic this long after a launch/resume/save trigger
PROPH_MIN_GAP = 120.0     # skip prophylactic if any burst ran this recently

C_TS, C_RUN, C_STEP, C_RATE, C_CLK, C_PWR, C_LOAD, C_MEMLOAD = 0, 1, 2, 3, 4, 6, 9, 17
SATURATED_W = 300

# Grade rescue by healthy TFLOP throughput, not just power draw or crossing the lower flip threshold.
# High power (>= SATURATED_W) indicates load but does not guarantee the card is performing cleanly;
# degraded cards can saturate power while running significantly slower than healthy baselines.
VIRGIN_TFLOPS = 55.0             # clearly-healthy, not merely better-than-stalled

# >= SATURATED_W means "not stalled" (power draw), NOT "fully restored" (throughput).
# Rescues are one-shot bursts only; never hold the card in a keep-warm state.
TRUE_SATURATED_W = 385.0
MAX_PARTIAL_BURSTS = 4
STATED_RUN = os.path.join(_ROOT, "var", "state", "CURRENT_RUN.json")

# Stalled cards are clocked up but idle (mem_load ~4%, gpu_load 100%), whereas spilling cards
# move data (mem_load 16-33%). Power draw (~230-280 W) cannot distinguish them, so mem_load is
# the required discriminator. Blank/absent defaults to UNKNOWN/no veto, preserving pre-patch behavior.
MEMLOAD_STALL_MAX = 10.0

# --- born-degraded: the parked Phase 2 item, now cheap ------------------------------------------
# The detector looks for a rate COLLAPSE, so a context that spawned degraded and was slow from step
# 1 never trips it. With the order's own expected rate we can ask the absolute question instead.
# Guards: only after WARMUP_STEPS (step 0-4 legitimately run 5-15x -- v9 step 0 took 335 s), and
# only when an expectation exists. Reported ONCE per run, because it is a diagnosis for a human and
# a burst cannot fix a context that was born wrong -- the remedy is a recycle, which the existing
# escalation path already owns.
BORN_DEGRADED_FACTOR = 2.5
BORN_DEGRADED_AFTER_STEP = 10

# The stalled-training band (43.5-47.9 s/it) comes from the published docs
# (Flux2-32B-QLoRA-AMD-ROCm-Windows.md, Driver-RootCause-Investigation-Plan.md), never from an
# order's declared expectation -- a mis-set expectation cannot move it. Caveat: 43.5 was measured at
# rank16/448px and legitimate 768px runs reach ~40.8 (6% under the floor), so born-degraded stays
# REPORT-ONLY.
DOCUMENTED_STALL_MIN_S_IT = 43.5

# Three independent stall detectors (no telemetry veto, no order override):
#   arm 1 SELF-OBSERVED: rate > 1.6x EMA-smoothed run minimum (catches mid-run degradation).
#   arm 2 DOCUMENTED BAND: rate >= 43.5 s/it (catches born-degraded runs; see constant above).
#   arm 3 DECLARED: rate > 2.5x expect_s_it if order declares one (belt for unusual configs).
#
# TRAIN_DEGRADED_CONFIRM consecutive hits triggers the same escalation as signature stalls:
# identical burst ladder, lossless-recycle cure, and cooldowns. No veto consulted.
#
# If no valid rate is available for TRAIN_BLIND_ALARM_S during training,
# emit one log + DM (never treat "unknown" as "fine").
TRAIN_DEGRADED_FACTOR = 1.6      # vs the run's own best sustained rate (arm 1)
TRAIN_DEGRADED_CONFIRM = 3       # consecutive polls (~65 s) — transients don't trip it
TRAIN_BASELINE_MIN_SAMPLES = 12  # EMA warm-up before arm 1 arms itself (~4 min of telemetry)
TRAIN_BLIND_ALARM_S = 600.0      # training with no measurable rate for this long -> say so


def train_degraded_arms(rate, t_best, exp):
    if not rate or rate <= 1.0:
        return False, ""
    arms = []
    if t_best and rate > t_best * TRAIN_DEGRADED_FACTOR:
        arms.append("self%.1fx" % (rate / t_best))
    if rate >= DOCUMENTED_STALL_MIN_S_IT:
        arms.append("band")
    if exp and rate > exp * BORN_DEGRADED_FACTOR:
        arms.append("declared")
    return bool(arms), "+".join(arms)


def baseline_update(ema, best, n, rate):
    """EMA-smoothed running MIN of the run's own s/it.

    Degraded stretches raise the EMA but never lower `best` (the min),
    preventing the baseline from going blind during degradation.
    Single glitch rows are diluted by the ~5-sample EMA before affecting `best`."""
    if not rate or rate <= 1.0:
        return ema, best, n
    ema = rate if ema is None else (0.8 * ema + 0.2 * rate)
    n += 1
    if n >= TRAIN_BASELINE_MIN_SAMPLES:
        best = ema if best is None else min(best, ema)
    return ema, best, n


COMPUTE_STALE_DEFAULT_S = 900.0   # spec may declare `progress_stale_after`; default is deliberately
                                  # conservative — a false stop costs a respawn, a missed wedge
                                  # costs hours (r448: one cond sat 6.5 h)
COMPUTE_RUNNER_BEAT_STALE_S = 90.0  # CURRENT_RUN.updated older than this = the RUNNER is the sick
                                    # one, not the payload: report, never act (class 12a)
COMPUTE_ROUNDS_MAX = 8            # respawns bank shards, but a permanently-broken payload must not
                                  # stop/requeue forever; past the cap a human decides


def watch_transition_note(verdict, name, said, step=None):
    """The one line to log when an order announced UNWATCHABLE starts publishing steps, or None. The deck's health
    text is the daemon's last log line, so without this the stale 'UNWATCHABLE' stays on the tile for the whole
    order. Marks `said` so it is said once."""
    if verdict in (None, "unwatchable", "runner_stale") or ("unwatch:" + name) not in said \
            or ("watched:" + name) in said:
        return None
    said.add("watched:" + name)
    return ("watch.compute: %s now publishes steps (%s) - WATCHED by its own step count and work beat"
            % (name, step if step is not None else "?"))


def compute_watch_decide(cr, spec, st, now):
    """PURE verdict for one poll of the compute arm.

    cr:   CURRENT_RUN dict (or None) — the runner's own published state, ONE derivation with the
          homepage tile (the runner counts progress_glob itself; we never re-expand the glob).
    spec: the order's spec dict (or None if unreadable).
    st:   mutable per-order watch state {name, last_step, static_since}.
    Returns one of:
      None            — no compute order running (loop clears state)
      "runner_stale"  — runner's beat is old: report-only, the runner has its own supervisor
      "unwatchable"   — order declares no progress_glob: nothing to measure, say so once
      "progress"      — step advanced (baseline reset)
      "working"       — step static but the payload's own work_beat moved (baseline reset)
      "assembly"      — static AT progress_total: the npz-assembly shape (r448 sat 13 min at
                        40/40, healthy) — NEVER a wedge
      "watching"      — static but inside the allowed window
      "cpu_static"    — static past window on a load:"cpu" order: the wedge disease is a GPU
                        class; respawn is not the documented cure, so report-only
      "wedged"        — static past window on gpu/mixed: the documented wedge, remedy applies
    """
    if not cr or (cr.get("state") or "").strip().lower() != "compute":
        return None
    name = (cr.get("name") or "").strip()
    if not name:
        return None
    if st.get("name") != name:
        st.clear()
        st.update({"name": name, "last_step": None, "static_since": now})
    upd = (cr.get("updated") or "").strip()
    upd_age = None
    try:
        upd_age = (datetime.datetime.now()
                   - datetime.datetime.fromisoformat(upd)).total_seconds()
    except Exception:
        upd_age = None
    if upd_age is None or upd_age > COMPUTE_RUNNER_BEAT_STALE_S:
        return "runner_stale"
    try:
        step = int(cr.get("step") or 0)
    except (TypeError, ValueError):
        step = 0
    # a runner PUBLISHING steps is watchable regardless of what the spec declares — the
    # runner parses the worker's own printed count (one derivation, never re-expanded here)
    if (not spec or not str(spec.get("progress_glob") or "").strip()) \
            and not step and not st.get("last_step"):
        return "unwatchable"
    if st.get("last_step") != step:
        st["last_step"] = step
        st["static_since"] = now
        return "progress"
    # A moving beat (work_beat) indicates active work, overriding the static step window to prevent false wedge detection on long single-unit operations.
    beat = cr.get("work_beat")
    if beat is not None and beat != st.get("last_beat"):
        st["last_beat"] = beat
        st["static_since"] = now
        return "working"
    try:
        total = int(spec.get("progress_total") or 0)
    except (TypeError, ValueError):
        total = 0
    if total and step >= total:
        st["static_since"] = now
        return "assembly"
    try:
        stale_after = float(spec.get("progress_stale_after") or COMPUTE_STALE_DEFAULT_S)
    except (TypeError, ValueError):
        stale_after = COMPUTE_STALE_DEFAULT_S
    if (now - st.get("static_since", now)) <= stale_after:
        return "watching"
    if (str(spec.get("load") or "mixed").strip().lower()) == "cpu":
        return "cpu_static"
    return "wedged"


# Krea UNETs must not receive burst rescues (secondary GPU contexts) because they cause ComfyUI to wedge; FLUX.2 renders retain the standard burst ladder.
NO_BURST_UNETS = ("krea",)


def queue_unets(q):
    """The UNETLoader `unet_name` of every prompt running or pending in a ComfyUI /queue reply ([] if none/unreadable)."""
    names = []
    for item in ((q or {}).get("queue_running") or []) + ((q or {}).get("queue_pending") or []):
        graph = item[2] if isinstance(item, (list, tuple)) and len(item) > 2 and isinstance(item[2], dict) else {}
        for node in graph.values():
            if isinstance(node, dict) and node.get("class_type") == "UNETLoader":
                name = (node.get("inputs") or {}).get("unet_name")
                if name:
                    names.append(str(name))
    return names


def no_burst_render(q):
    """True when ComfyUI is running/holding a render of a NO_BURST_UNETS model."""
    return any(n.lower().startswith(p) for n in queue_unets(q) for p in NO_BURST_UNETS)


def progress_advanced(prev, cur):
    """True when the render progress file moved FORWARD between two readings: newer timestamp AND a higher step.
    Missing or unreadable readings are not evidence of progress (a new render restarting at step 0 is not either)."""
    try:
        return (bool(prev) and bool(cur) and float(cur["ts"]) > float(prev["ts"])
                and float(cur["step"]) > float(prev["step"]))
    except (KeyError, TypeError, ValueError):
        return False


def spec_no_burst(spec):
    """True when a training order's `base` is a NO_BURST_UNETS model (orders state it: "base": "krea2")."""
    base = str(spec.get("base") or "").lower() if isinstance(spec, dict) else ""
    return any(base.startswith(p) for p in NO_BURST_UNETS)


def no_burst_training():
    """True when the stated run (CURRENT_RUN.json -> its spec) trains a NO_BURST_UNETS base. Unreadable = False, so
    the FLUX ladder stays the default it was before this existed."""
    try:
        with open(STATED_RUN, "r", encoding="utf-8") as f:
            spec_path = json.load(f).get("spec_file")
        with open(spec_path, "r", encoding="utf-8") as f:
            return spec_no_burst(json.load(f))
    except Exception:
        return False


# Krea trainer health is judged solely by step advancement rate, not absolute latency. Healthy Krea runs exhibit high variance (e.g., 448px ~4s vs 1024px ~70s+), overlapping the stalled-band thresholds of FLUX. Therefore, stall detection must be relative to the run's own observed step history (KREA_STUCK_FACTOR) rather than a fixed global timeout. Do not apply burst or recycle actions to Krea trainers; only report status (class 12a) to avoid triggering GPU driver hangs or killing legitimate long-running steps.
KREA_STUCK_FACTOR = 3.0
# Must wait for enough intervals to establish the step-time mix; otherwise slow 1024px steps are misclassified as stalls in mixed-bucket runs.
KREA_WATCH_MIN_INTERVALS = 10


def krea_step_watch(st, step, now):
    """Feed one telemetry step reading into st (a per-run dict). Returns the seconds the step has been static past
    KREA_STUCK_FACTOR x the slowest step interval seen this run, else 0. Fewer than KREA_WATCH_MIN_INTERVALS intervals
    measured -> 0 (the run has not shown its step mix yet, so it cannot judge and says nothing). The first move after attaching includes boot time, so it is not counted as a step interval.
    A blank cell reads as num()'s -1 default: no evidence, never a new run."""
    if step is None or step < 0:
        return 0.0
    if st.get("step") is None or step < st["step"]:
        st.clear()
        st.update(step=step, at=now)
        return 0.0
    if step > st["step"]:
        if st.get("moved"):
            g = (now - st["at"]) / (step - st["step"])
            st["gap"] = max(st.get("gap") or 0.0, g)
            st["n"] = st.get("n", 0) + 1
        st.update(step=step, at=now, moved=True)
        return 0.0
    if not st.get("gap") or st.get("n", 0) < KREA_WATCH_MIN_INTERVALS:
        return 0.0
    over = (now - st["at"]) - KREA_STUCK_FACTOR * st["gap"]
    return over if over > 0 else 0.0


def expected_s_it():
    """The s/it this order says it expects, or None. Read fresh each poll (a run can be replaced
    under us); any failure returns None (fail-soft)."""
    try:
        with open(STATED_RUN, "r", encoding="utf-8") as f:
            spec_path = json.load(f).get("spec_file")
        if not spec_path:
            return None
        with open(spec_path, "r", encoding="utf-8") as f:
            v = json.load(f).get("expect_s_it")
        return float(v) if v else None
    except Exception:
        return None


TRAINER_STOP = os.path.join(_ROOT, "var", "state", "STOP_NOW")      # lossless: checkpoint at this step, clean exit
RESCUE_PAUSE = os.path.join(_ROOT, "var", "state", "RESCUE_PAUSE")   # marker: the foundry relaunches instead of waiting
MAX_RESCUE_PAUSES = 4            # 4 covers a long run (a 14 h run can need 2)
RECYCLE_MIN_GAP = 1800.0         # >=30 min between recycles: bounded, can never loop
RENDER_COOLDOWN = 45.0   # v4.5: render stalls block everything - retry cadence, not training-mode 240s.0


_HEAD = P.head_url() if P else ""
REPORT_URL = _HEAD + "/fleet/rescue-state" if _HEAD else ""
DM_URL = P.setting("OVERNIGHT_DM_URL", "") if P else ""
DM_HOST = P.setting("OVERNIGHT_DM_HOST", "") if P else ""
REPORT_EVERY = 1          # REPORT_EVERY=1 polls every ~15s. Lowering this increases exposure to
# slow orchestrator timeouts; the HTTP timeout below is tightened accordingly
# to ensure status reporting never stalls the stall-detection loop.
_report_tick = 0
_board = {"t": 0.0, "line": ""}


def _board_line():
    """Fan control + hotspot (LAB-TOOL-GAPS #2/#6) via the shared p20_board_health, refreshed at most once a minute
    so the 15 s report loop never waits on tasklist. Report only; never raises."""
    now = time.time()
    if now - _board["t"] >= 60:
        try:
            # this daemon runs outside the foundry tree; the shared module is deployed to the
            # foundry's bin (appended, so nothing beside the daemon is shadowed). Telemetry =
            # this daemon's OWN constant.
            _bin = os.path.join(_ROOT, "bin")
            if _bin not in sys.path:
                sys.path.append(_bin)
            import p20_board_health as _bh
            h, r = _bh.last_row(CSV)
            _, fan = _bh.check_fans(_bh.tasklist(), h, r)
            _board.update(t=now, line="%s; %s" % (_bh.hotspot_note(h, r), fan))
        except Exception as e:                                       # noqa: BLE001
            _board.update(t=now, line="board health unreadable (%s)" % type(e).__name__)
    return _board["line"]


def _report_status():
    """POST what this daemon currently sees and has done. Never raises. With no head address
    there is nothing to report to."""
    if not REPORT_URL:
        return
    try:
        import json as _json
        import urllib.request as _url
        today = time.strftime("%Y-%m-%d")
        last, last_tf, stalls, rescues = "", None, 0, 0
        try:
            with open(LOG, "rb") as _f:
                _f.seek(max(0, os.path.getsize(LOG) - 65536))
                rows = [l for l in _f.read().decode("utf-8", "ignore").splitlines()
                        if l.startswith(today)]
            stalls = sum(1 for l in rows if "STALL CONFIRMED" in l)
            rescues = sum(1 for l in rows if "SATURATED" in l or "context KILLED" in l)
            for l in reversed(rows):
                if last_tf is None and "TFLOP/s" in l:
                    try:
                        last_tf = float(l.split("=>")[-1].split("TFLOP")[0].strip())
                    except Exception:
                        pass
                if not last and not l.strip().endswith("respawn"):
                    last = l.split("  ", 1)[-1].strip()[:120]
                if last and last_tf is not None:
                    break
        except Exception:
            pass
        body = _json.dumps({
            "host": P.this_box() if P else (os.environ.get("FOUNDRY_FLEET_HOST")
                                            or __import__("socket").gethostname().split(".")[0].lower()),
            "alive": True,
            "pid": os.getpid(),
            "poll_s": POLL if "POLL" in globals() else None,
            "disabled": bool(globals().get("disabled", False)),
            "last": (last + " | " if last else "") + _board_line(),
            "last_tflops": last_tf,
            "stalls_today": stalls,
            "rescues_today": rescues,
            "watching": os.path.basename(globals().get("TELEM_CSV", "") or ""),
        }).encode("utf-8")
        rq = _url.Request(REPORT_URL, data=body, headers={"Content-Type": "application/json"})
        _url.urlopen(rq, timeout=2).read()
    except Exception:
        pass          # Status board input constraints: paths/ages must be explicit because previous versions of these
# inputs silently lied (stale CSVs, split render_progress paths, stale CURRENT_RUN).
# Publishing path + measured age + verdict makes each input self-announce in one line.
    try:
        import p20_status as _st
        _st.publish(
            "stall_rescue",
            "disabled" if globals().get("disabled") else "watching",
            sources=[
                _st.source("gpu_telemetry", CSV, stale_after=STALE_SECS,
                           note="the daemon's OWN constant, not a same-named file next door"),
                _st.source("render_progress", os.path.join(TELEM, "render_progress.json"),
                           stale_after=180.0, note="mercy rule input", absent_ok=True),
                _st.source("current_run", STATED_RUN, stale_after=2400.0,
                           note="which run owns the card", absent_ok=True),
                # v6.1: the daemon's OWN beat file is a source, so the board announces a failing
                # beat write instead of the gate silently reading DOWN. 120.0 matches
                # p20_foundry.DEMON_STALE_S -- if you change one, change both (they are two
                # copies of one constant; the daemon cannot import the foundry to share it).
                _st.source("own_heartbeat", STALL_RESCUE_HEARTBEAT, stale_after=120.0,
                           note="the demon gate (require_demon) reads THIS file's age; "
                                "stale here = Mothership down for work"),
            ],
            notes=last[:160],
            extra={"last_tflops": last_tf, "stalls_today": stalls, "rescues_today": rescues,
                   "heartbeat": STALL_RESCUE_HEARTBEAT,
                   "beat_failing": _beat_fails >= BEAT_ALARM_AFTER,
                   # v6.2: what THIS daemon can supervise, declared from the live process so the
                   # claim dies with it (a stale status file = no claim). The foundry's intake
                   # HOLD reads this: an order kind nobody can watch gets held, not run unwatched.
                   "capabilities": ["watch.train", "watch.render", "watch.compute"]})
    except Exception:
        pass          # a status board must never break the rescuer either


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line); sys.stdout.flush()
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# Liveness check constraint: Task Scheduler `RestartOnFailure` is unreliable because
# `schtasks /end` leaves the cmd child process running (invisible to Scheduler), preventing
# restart triggers even when the daemon is dead. We detect this via heartbeat file staleness.
STALL_RESCUE_HEARTBEAT = os.path.join(os.path.dirname(LOG), "stall_rescue.heartbeat")
STALL_RESCUE_LOCK = os.path.join(os.path.dirname(LOG), "stall_rescue.lock")


def acquire_singleton():
    """Refuse to start if a LIVE instance already holds the lock. Stale locks are reclaimed.

        Identity matters, not just existence: Windows recycles PIDs after reboot,
        so we verify the holder's actual process (via mtime comparison in `foundry_lock`),
        not merely whether a PID exists.
    """
    try:
        if os.path.exists(STALL_RESCUE_LOCK):
            live, why = foundry_lock.holder_is_live(STALL_RESCUE_LOCK)
            if live:
                log("another rescue daemon is live (%s) - exiting so we do not double-poll" % why)
                return False
            # Report the observation, never a verdict we did not measure.
            log("reclaiming stale lock (%s)" % why)
    except Exception as e:
        log("lock check failed (%r) - continuing" % (e,))
    try:
        with io.open(STALL_RESCUE_LOCK, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        log("could not write lock (%r) - continuing unlocked" % (e,))
    return True


_beat_fails = 0        # consecutive heartbeat-write failures (v6.1)
_beat_alarmed = False  # one loud announcement per failure episode, reset on recovery
_last_beat_ok = 0.0
BEAT_ALARM_AFTER = 3   # transient blips stay quiet; a PERSISTENT failure announces itself


def beat():
    """Update heartbeat file with timestamp and PID.

        Liveness is determined by file freshness, not process existence,
        to prevent stale-file "zombie" scenarios where the external daemon
    gate (p20_foundry.require_demon) incorrectly kills healthy daemons.

        Failures are treated as critical: a missing or stale heartbeat causes
        the gate to block compute work and terminate the daemon. Silent failures
        must be avoided; errors trigger immediate alerts via log push and DM."""
    global _beat_fails, _beat_alarmed, _last_beat_ok
    try:
        with io.open(STALL_RESCUE_HEARTBEAT, "w", encoding="utf-8") as f:
            f.write("%d %d" % (int(time.time()), os.getpid()))
        _last_beat_ok = time.time()
        if _beat_fails:
            log("heartbeat writes RECOVERED after %d failures" % _beat_fails)
        _beat_fails = 0
        _beat_alarmed = False
    except Exception as e:
        _beat_fails += 1
        if _beat_fails >= BEAT_ALARM_AFTER and not _beat_alarmed:
            _beat_alarmed = True
            log("heartbeat WRITE FAILING x%d (%r) -- the demon gate reads this file: "
                "Mothership reads DOWN FOR WORK while I am alive and watching" % (_beat_fails, e))
            try:
                notify_dm("stall-rescue heartbeat FILE writes are FAILING (%r). The daemon is "
                          "ALIVE and watching, but the demon gate reads that file's age -- "
                          "Mothership will refuse gpu/mixed compute work (and the gate may "
                          "taskkill this healthy daemon trying to revive it) until the write "
                          "path is fixed: %s" % (e, STALL_RESCUE_HEARTBEAT))
            except Exception:
                pass  # the alarm must never take down the thing it reports on


def release_singleton():
    try:
        if os.path.exists(STALL_RESCUE_LOCK):
            cur = io.open(STALL_RESCUE_LOCK, encoding="utf-8").read().strip()
            if cur == str(os.getpid()):
                os.remove(STALL_RESCUE_LOCK)
    except Exception:
        pass


def num(v, d=-1.0):
    try:
        return float(v)
    except Exception:
        return d


def last_row():
    try:
        with open(CSV, "r") as f:
            rows = f.readlines()
        for r in reversed(rows[1:]):
            p = r.strip().split(",")
            if len(p) > C_MEMLOAD:
                return p
    except Exception:
        pass
    return None


def row_age(row):
    try:
        ts = datetime.datetime.strptime(row[C_TS].strip(), "%Y-%m-%d %H:%M:%S")
        return (datetime.datetime.now() - ts).total_seconds()
    except Exception:
        return None


def render_nearly_done():
    """
    Check if a render is nearly complete to apply mercy (grace period) instead of
    killing it. Returns True if progress > 70% and data is recent (<180s).
    If the progress file is stale or missing, normal kill policy applies.
    """
    try:
        with open(os.path.join(TELEM, "render_progress.json")) as f:
            d = json.load(f)
        if time.time() - float(d.get("ts", 0)) > 180:
            return False
        return float(d.get("step", 0)) / max(1.0, float(d.get("max", 20))) >= 0.7
    except Exception:
        return False


_rate_prev = {"ts": 0.0, "step": -1.0}


def render_step_rate():
    """Calculate steps-per-second (s/step) from the worker's progress file.

    Returns s/step when two successive samples of the same render show step
    advance. Returns None if not computable: stale file (>180s), first sample,
    new render, step went backwards, or no advance (e.g., during model load).

    This metric detects intermediate performance degradation that hardware
    signatures (power/clock) may miss."""
    try:
        with open(os.path.join(TELEM, "render_progress.json")) as f:
            d = json.load(f)
        ts, step = float(d.get("ts", 0)), float(d.get("step", -1))
        if time.time() - ts > 180:
            return None
        p = dict(_rate_prev)
        _rate_prev.update(ts=ts, step=step)
        if step > p["step"] >= 0.0 and ts > p["ts"] > 0.0:
            return (ts - p["ts"]) / (step - p["step"])
        return None
    except Exception:
        return None


def render_queue():
    """ComfyUI :8189's /queue reply, or None when it cannot be read. Cheap, short timeout."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8189/queue", timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def render_active():
    """True if ComfyUI :8189 is up AND actually working (non-empty queue)."""
    q = render_queue()
    return bool(q) and (bool(q.get("queue_running")) or bool(q.get("queue_pending")))


def render_progress():
    """The worker's render progress reading {step, max, ts}, or None when absent or stale (>180 s)."""
    try:
        with open(os.path.join(TELEM, "render_progress.json")) as f:
            d = json.load(f)
        return d if time.time() - float(d.get("ts", 0)) <= 180 else None
    except Exception:
        return None


def newest_ckpt_mtime():
    """Newest .safetensors mtime under the training output tree (0.0 if none)."""
    try:
        return max((os.path.getmtime(p) for p in glob.glob(OUTPUT_GLOB)), default=0.0)
    except Exception:
        return 0.0


def run_payload_once():
    """One fresh burst. Returns (rc, achieved_tflops) — rc None = launch fail/timeout, achieved None = unparsed."""
    try:
        p = subprocess.Popen([PY, PAYLOAD, "6144", str(BURST_SECS)],
                             cwd=os.path.dirname(PAYLOAD),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
    except Exception as e:
        log("  payload FAILED to launch: %r" % (e,))
        return None, None
    t0 = time.time()
    while p.poll() is None:
        if time.time() - t0 > PAYLOAD_TIMEOUT:
            log("  payload exceeded %.0fs -> killing" % PAYLOAD_TIMEOUT)
            try:
                p.kill()
            except Exception:
                pass
            return None, None
        time.sleep(2)
    out = ""
    try:
        out = (p.stdout.read() or "").strip()
    except Exception:
        pass
    achieved = None
    for ln in out.splitlines():
        if "TFLOP" in ln or "WARNING" in ln or "FAILED" in ln or "DUD" in ln:
            log("  payload: " + ln.strip())
            import re as _re
            m = _re.search(r"=>\s+([\d.]+) TFLOP", ln)
            if m:
                achieved = float(m.group(1))
    return p.returncode, achieved


DM_ENV = os.path.join(_ROOT, "etc", "dm_relay.env")


def notify_dm(message):
    """Only escalate on actual failures (not autonomous rescues); do so
    immediately, and never disrupt the rescue loop if DM delivery fails."""
    log("ESCALATE-DM: " + message)
    try:
        import json as _json
        import urllib.request as _rq
        creds = {}
        with open(DM_ENV, "r", encoding="ascii") as f:
            for ln in f:
                if "=" in ln:
                    k, v = ln.strip().split("=", 1)
                    creds[k] = v
        if not (creds.get("OVERNIGHT_DM_TOKEN") and creds.get("OVERNIGHT_DM_USER_ID")):
            return
        req = _rq.Request(
            DM_URL,
            data=_json.dumps({"user_id": creds["OVERNIGHT_DM_USER_ID"],
                              "message": "[stall-rescue] " + message}).encode(),
            headers={"Content-Type": "application/json", "X-DM-Token": creds["OVERNIGHT_DM_TOKEN"],
                     "Host": DM_HOST},
        )
        with _rq.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log("  (escalation DM failed: %r — continuing)" % (e,))


def discriminator_probe(context):
    """v4.9 [driver-investigation Phase 1]: ONE fresh measured spawn immediately after a wedged-context
    kill. The wedge verdict already proves fresh spawns are sub-flip DURING the wedge (global state);
    this probe adds the other half: if it's fast now, the wedge state was HELD BY the killed context
    (MES queue-poisoning pattern, ROCm#2689); if still slow, machine-wide state survives context death
    and only reboot clears it. Free evidence at every wedge; feeds the degradation exhibits."""
    try:
        time.sleep(8)
        rc, achieved = run_payload_once()
        log("DISCRIMINATOR (%s): post-kill fresh spawn -> rc=%s achieved=%s TFLOP/s "
            "(wedge-time spawns were sub-flip; fast-now=context-held, slow-now=machine-wide)"
            % (context, rc, ("%.1f" % achieved) if achieved is not None else "?"))
        return achieved
    except Exception as e:
        log("  (discriminator probe failed: %r — continuing)" % (e,))
    return None


def trainer_running():
    """Is a LoRA trainer alive? Name -eq python.exe keeps the query from matching its OWN powershell
    command line, which contains the 'run.py' literal."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and "
             "$_.CommandLine -like '*run.py*' } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=25)
        return (r.stdout or "0").strip() not in ("", "0")
    except Exception:
        return False


def request_trainer_recycle(achieved):
    """Escalate a poisoned trainer context to a lossless recycle.

    Direct bursts cannot clear poison held by the trainer's own context,
    causing severe performance degradation. A hard kill is forbidden;
    instead, we trigger STOP_NOW for a checkpointed exit and write
    RESCUE_PAUSE to signal the foundry to auto-relaunch without operator
    intervention."""
    why = ("every fresh spawn born degraded (barrage exhausted)" if achieved is None
           else "discriminator %.1f TFLOP/s vs ~%.0f virgin" % (achieved, VIRGIN_TFLOPS))
    open(RESCUE_PAUSE, "w").close()
    open(TRAINER_STOP, "w").close()
    log("!! POISONED TRAINER CONTEXT: %s -> requesting LOSSLESS recycle "
        "(RESCUE_PAUSE + STOP_NOW; zero steps lost)." % why)
    notify_dm("Rescue escalated (%s): the trainer's own context is holding the poison, which no burst "
              "can clear. Asked the foundry for a lossless checkpoint-and-relaunch onto a "
              "health-verified card - no steps lost." % why)


def fire_burst_with_retries(reason):
    """Retry loop: respawns on dud (rc 3) or marginal performance (< FLIP_TFLOPS).

    Avoids the previous long-sleep bug by looping immediately until a spawn clears
    the flip boundary or MAX_DUD_RESPAWNS is exhausted. Maintains echo-guard window.

    Returns:
        'fast': Success on first attempt.
        'all-dud': All retries failed due to duds or marginal performance.
        'failed': Other failure modes."""
    global burst_start, burst_end
    for attempt in range(1, MAX_DUD_RESPAWNS + 2):
        burst_start = time.time()
        rc, achieved = run_payload_once()
        burst_end = time.time()
        if rc == 3 or (rc == 0 and achieved is not None and achieved < FLIP_TFLOPS):
            kind = "dud spawn" if rc == 3 else ("marginal burst %.1f TFLOP/s" % achieved)
            log("  %s (#%d) -> barrage respawn" % (kind, attempt))
            # Stop laddering if we hit RENDER_WEDGE_RESPAWNS consecutive failures
            # while a render is active (burst-immune context). Prevents the old
            # 15-deep ladder from delaying context-kill by 7+ min per event.
            # Note: only apply to active renders; training-time prophylactics
            # (queue idle) keep the full ladder.
            if attempt >= RENDER_WEDGE_RESPAWNS and render_active() and not render_nearly_done():
                log("  %d consecutive sub-flip spawns on a render -> WEDGED verdict" % attempt)
                return "wedged"
            time.sleep(2)
            continue
        if rc == 0:
            return "fast"
        return "failed"
    log("  %d consecutive dud/marginal spawns for %s -> giving up this event (short cooldown)" % (MAX_DUD_RESPAWNS + 1, reason))
    return "all-dud"


log("=== stall rescue daemon v6.3 (v6.2 watch.compute + Krea renders AND trainers get no burst; a Krea "
    "trainer is judged by its own step advance, report-only) ===")
log("watching %s  poll=%.0fs confirm=%d cooldown=%.0f/%.0fs settle=%.0fs stale>%.0fs" %
    (CSV, POLL_SECS, CONFIRM, COOLDOWN, SHORT_COOLDOWN, SETTLE, STALE_SECS))
log("prophylactic: delay=%.0fs min-gap=%.0fs triggers=launch/resume + new ckpt in %s" %
    (PROPH_DELAY, PROPH_MIN_GAP, OUTPUT_GLOB))
log("payload %s (%.0fs, dud-abort in payload v2)   stop with: type nul > %s" % (PAYLOAD, BURST_SECS, STOP))
if os.path.exists(STOP):
    os.remove(STOP)
    log("cleared a pre-existing STOP_RESCUE flag")

stalled = 0
rstalled = 0              # render-stall confirmation counter (v4.1)
rstall_progress0 = None   # the render's progress reading when the current stall streak began (no-burst renders)
last_rescue = 0.0
rescues = 0
ineffective = 0
rescue_pauses = 0          # v5.0: lossless trainer recycles requested this daemon lifetime
partial_bursts = 0         # v5.3: extra bursts spent climbing from 'flipped' to FULLY saturated
last_recycle = 0.0         # v5.1: enforce RECYCLE_MIN_GAP so a recycle can never loop
disabled = False
warned_stale = False
INCIDENT_WINDOW = 600.0
last_confirm = 0.0
last_memload_log = 0.0     # throttle for the mem_load veto
born_degraded_reported = False   # one-shot: a diagnosis for a human, not a retry loop
# v6.0 TRAIN-DEGRADED state
tdegraded = 0                    # consecutive rate-evidence hits (mirror of rdegraded)
t_best_ema = None                # EMA-smoothed rate; its running MIN is the self-observed baseline
t_best = None                    # best (lowest) sustained s/it this run has demonstrated
t_base_n = 0                     # samples into the EMA warm-up
t_last_rate_seen = time.time()   # for the blind alarm: last time a valid training rate existed
t_blind_said = False             # one-shot per run
t_run_name = ""                  # baseline resets when the run changes
krea_st = {}                     # v6.3: krea_step_watch state for the current run
krea_stuck_said = False          # v6.3: one report per static episode
incident_attempts = 0
incident_escalated = False
attempts_since_clear = 0   # v4.6: render attempts with NO intervening clear -> wedged detector
KILL_AFTER_ATTEMPTS = 2
RENDER_DEGRADED_S_PER_STEP = 18.0   # Threshold: 18.0 s/step.
# Tuned to avoid false positives from VAE jitter (~13s) and
# LoRA repatch stalls (~120s). Genuine degradation >19s.
RENDER_DEGRADED_CONFIRM = 3         # v4.10.1: 2 -> 3 consecutive computed-slow rates (~45-90 s at
                                    # POLL_SECS=15) -- single-sample jitter must not count toward
                                    # a kill; real degradation sustains the rate for minutes.
rdegraded = 0
RENDER_WEDGE_RESPAWNS = 3
burst_start = burst_end = 0.0
prev_training = None      # v4.1 boot fix: None = uninitialized; baseline from the FIRST fresh row,
                          # so booting against an already-running trainer fires no spurious prophylactic
proph_due = 0.0           # wall-clock time a prophylactic should fire (0 = none pending)
proph_reason = ""
prev_render = False
last_ckpt_mtime = newest_ckpt_mtime()   # baseline so we don't fire on pre-existing files at boot
# ── v6.2 watch.compute state ──
QUEUE_DIR = os.path.join(_ROOT, "jobs", "queue")   # where stop/ctl slips go (the desk's own channels)
COMPUTE_ROUNDS_FILE = os.path.join(os.path.dirname(LOG), "compute_watch_rounds.json")
comp_state = {}            # per-order watch state for compute_watch_decide (in-memory)
comp_said = set()          # once-per-order report throttles ("unwatchable:<name>", "cap:<name>", ...)
comp_spec_cache = {}       # spec_file -> (mtime, dict)
comp_rounds = {}           # order name -> {"n": respawns, "ts": last touch}; persisted + 24h-pruned
try:
    with io.open(COMPUTE_ROUNDS_FILE, encoding="utf-8") as _f:
        comp_rounds = {k: v for k, v in json.load(_f).items()
                       if time.time() - float(v.get("ts", 0)) < 86400}
except Exception:
    comp_rounds = {}


def comp_rounds_save():
    try:
        with io.open(COMPUTE_ROUNDS_FILE, "w", encoding="utf-8") as f:
            json.dump(comp_rounds, f)
    except Exception:
        pass    # bookkeeping must never take down the arm


def comp_spec_for(cr):
    """The order's spec dict via CURRENT_RUN's own spec_file pointer (never a name guess)."""
    try:
        sp = (cr.get("spec_file") or "").strip()
        if not sp or not os.path.exists(sp):
            return None
        m = os.path.getmtime(sp)
        hit = comp_spec_cache.get(sp)
        if hit and hit[0] == m:
            return hit[1]
        with io.open(sp, encoding="utf-8") as f:
            d = json.load(f)
        comp_spec_cache.clear()     # one live compute order per lane; no need to hoard
        comp_spec_cache[sp] = (m, d)
        return d
    except Exception:
        return None

if not acquire_singleton():
    sys.exit(0)
log('singleton lock held; heartbeat -> %s' % STALL_RESCUE_HEARTBEAT)

while True:
    beat()          # first thing every poll: a stale heartbeat now MEANS something
    _report_tick += 1
    if _report_tick % REPORT_EVERY == 0:
        _report_status()
    if os.path.exists(STOP):
        log("STOP_RESCUE flag seen -> exiting cleanly")
        release_singleton()
        try:
            os.remove(STOP)
        except Exception:
            pass
        break

    now = time.time()

    # ── cross-cut A trigger 2: new/updated checkpoint file (works even if telemetry is stale) ──
    m = newest_ckpt_mtime()
    if m > last_ckpt_mtime + 1.0:
        last_ckpt_mtime = m
        if not disabled:
            proph_due = now + PROPH_DELAY
            proph_reason = "ckpt-save"
            log("prophylactic scheduled in %.0fs (trigger=ckpt-save)" % PROPH_DELAY)

    try:
        comp_cr = None
        try:
            with open(STATED_RUN, "r", encoding="utf-8") as _f:
                comp_cr = json.load(_f)
        except Exception:
            comp_cr = None
        if comp_state.get("phase") == "stopping":
            _nm = comp_state.get("name", "")
            _gone = not comp_cr or (comp_cr.get("name") or "") != _nm
            _spec_gone = not os.path.exists(os.path.join(QUEUE_DIR, _nm + ".job.json"))
            if _gone and _spec_gone:
                _ctl = os.path.join(QUEUE_DIR, _nm + ".requeue.ctl.json")
                with io.open(_ctl, "w", encoding="utf-8") as _f:
                    json.dump({"action": "requeue", "order": _nm,
                               "why": "watch.compute respawn round %d — wedge stopped, shards "
                                      "banked, restoring the order" % comp_rounds.get(_nm, {}).get("n", 0)}, _f)
                log("watch.compute: requeue slip placed for %s (round %d)"
                    % (_nm, comp_rounds.get(_nm, {}).get("n", 0)))
                comp_state.clear()
            elif now - comp_state.get("stop_placed_t", now) > 180:
                log("watch.compute: stop slip for %s NOT honoured in 180s — standing down on "
                    "this order; the runner may be the sick one" % _nm)
                if ("stopfail:" + _nm) not in comp_said:
                    comp_said.add("stopfail:" + _nm)
                    notify_dm("watch.compute: stop slip for %s was not honoured in 180 s. Not "
                              "retrying blindly — check the foundry runner on Mothership." % _nm)
                comp_state.clear()
        else:
            _verdict = compute_watch_decide(comp_cr, comp_spec_for(comp_cr) if comp_cr else None,
                                            comp_state, now)
            _note = watch_transition_note(_verdict, comp_state.get("name", "?"), comp_said,
                                          (comp_cr or {}).get("step"))
            if _note:
                log(_note)
            if _verdict is None:
                if comp_state.get("name"):
                    comp_state.clear()
            elif _verdict == "unwatchable":
                _nm = comp_state.get("name", "?")
                if ("unwatch:" + _nm) not in comp_said:
                    comp_said.add("unwatch:" + _nm)
                    log("watch.compute: %s declares no progress_glob — UNWATCHABLE, saying so "
                        "once rather than staying silently green" % _nm)
            elif _verdict == "runner_stale":
                _nm = comp_state.get("name", "?")
                if ("rstale:" + _nm) not in comp_said:
                    comp_said.add("rstale:" + _nm)
                    log("watch.compute: CURRENT_RUN beat stale during compute order %s — the "
                        "RUNNER is the sick one; reporting, not acting (its supervisor's job)" % _nm)
                    notify_dm("watch.compute: the foundry runner's beat went stale mid-compute "
                              "(%s). Not acting on the order; check the runner." % _nm)
            elif _verdict == "cpu_static":
                _nm = comp_state.get("name", "?")
                if ("cpustatic:" + _nm) not in comp_said:
                    comp_said.add("cpustatic:" + _nm)
                    log("watch.compute: %s (load=cpu) progress static past its window — the wedge "
                        "cure is for GPU work; reporting only" % _nm)
                    notify_dm("watch.compute: cpu compute order %s has static progress past its "
                              "window. Respawn is not the documented cure for CPU work — take a "
                              "look when convenient." % _nm)
            elif _verdict == "wedged":
                _nm = comp_state["name"]
                _rec = comp_rounds.get(_nm, {"n": 0, "ts": now})
                if _rec["n"] >= COMPUTE_ROUNDS_MAX:
                    if ("cap:" + _nm) not in comp_said:
                        comp_said.add("cap:" + _nm)
                        log("watch.compute: %s wedged AGAIN past round cap %d — standing down, a "
                            "human decides" % (_nm, COMPUTE_ROUNDS_MAX))
                        notify_dm("watch.compute ROUND CAP: %s has been respawned %d times and "
                                  "wedged again. Respawn is not converging — the order needs a "
                                  "human. Shards so far are banked in its output dir."
                                  % (_nm, COMPUTE_ROUNDS_MAX))
                else:
                    _rec = {"n": _rec["n"] + 1, "ts": now}
                    comp_rounds[_nm] = _rec
                    comp_rounds_save()
                    _stop = os.path.join(QUEUE_DIR, _nm + ".stop.json")
                    with io.open(_stop, "w", encoding="utf-8") as _f:
                        json.dump({"target": _nm,
                                   "why": "watch.compute: progress static past window while the "
                                          "runner beats — the documented wedge; respawn banks "
                                          "all completed shards (round %d/%d)"
                                          % (_rec["n"], COMPUTE_ROUNDS_MAX)}, _f)
                    log("watch.compute: WEDGE on %s (step static) — stop slip placed, round %d/%d"
                        % (_nm, _rec["n"], COMPUTE_ROUNDS_MAX))
                    comp_state["phase"] = "stopping"
                    comp_state["stop_placed_t"] = now
    except Exception as _e:
        log("watch.compute arm error (%r) — arm skipped this poll" % (_e,))

    row = last_row()
    if row is None:
        time.sleep(POLL_SECS); continue

    age = row_age(row)
    fresh = age is not None and age <= STALE_SECS
    if not fresh:
        if not warned_stale:
            log("telemetry STALE (last row %s old) — logger stopped? detection standing down until it resumes"
                % ("unparseable" if age is None else "%.0fs" % age))
            notify_dm("telemetry logger looks DEAD — stall detection is BLIND until it resumes. "
                      "Check the GPU Telemetry scheduled task on Mothership.")
            warned_stale = True
        stalled = 0
    else:
        if warned_stale:
            log("telemetry fresh again — re-armed")
            warned_stale = False

    step = row[C_STEP].strip()
    training = fresh and step != ""

    # Detect render queue waking up from idle.
    # Fresh renders on context-switched GPUs are stall-prone, so schedule
    # a prophylactic check sooner than for training (30s vs default).
    ra = render_active()
    if ra and not prev_render and not disabled:
        if no_burst_render(render_queue()):
            log("render-start: %s render - NO prophylactic burst (a burst is a second context: the collision)"
                % "/".join(NO_BURST_UNETS))
        else:
            proph_due = now + 30.0        # renders start doing GPU work sooner than trainers do
            proph_reason = "render-start"
            log("prophylactic scheduled in 30s (trigger=render-start)")
    prev_render = ra

    # ── cross-cut A trigger 1: idle -> training transition (launch or resume) ──
    # v4.1: prev_training baselines from the first fresh row (None) — no spurious boot prophylactic
    if fresh and prev_training is None:
        prev_training = training
    elif fresh:
        if training and not prev_training and not disabled:
            if no_burst_training():
                log("train-start: %s training - NO prophylactic burst (a burst is a second context: the collision)"
                    % "/".join(NO_BURST_UNETS))
            else:
                proph_due = now + PROPH_DELAY
                proph_reason = "launch/resume"
                log("prophylactic scheduled in %.0fs (trigger=launch/resume, step=%s)" % (PROPH_DELAY, step))
        prev_training = training

    # ── cross-cut A: fire a due prophylactic ──
    if proph_due and now >= proph_due:
        proph_due = 0.0
        if disabled:
            pass
        elif (now - burst_end) < PROPH_MIN_GAP or (now - last_rescue) < PROPH_MIN_GAP:
            log("prophylactic (%s) skipped — a burst ran within %.0fs" % (proph_reason, PROPH_MIN_GAP))
        elif no_burst_render(render_queue()) or no_burst_training():
            log("prophylactic (%s) skipped — a %s render or trainer is on the card (a burst is the collision)"
                % (proph_reason, "/".join(NO_BURST_UNETS)))
        # Fire unconditionally after launch/resume/save: telemetry steps are delayed
        # by latent-cache reload (~4 min), so stepping checks would miss the stall window.
        # Unconditional burst prevents establishment; min-gap above prevents stacking.
        else:
            log("PROPHYLACTIC burst (trigger=%s, step=%s) — firing without waiting for detection" % (proph_reason, step))
            outcome = fire_burst_with_retries("prophylactic")
            last_rescue = time.time()
            stalled = 0
            # fire-and-forget: no grading, no ineffective accounting (there may have been no stall at all)
            continue

    if not fresh:
        time.sleep(POLL_SECS); continue

    # ── cross-cut C: freeze the state machine on burst-echo rows ──
    row_wall = now - (age or 0.0)
    if burst_start and (burst_start - 2.0) <= row_wall <= (burst_end + ECHO_GUARD_SECS):
        time.sleep(POLL_SECS); continue

    rate, clk, pwr, load = num(row[C_RATE]), num(row[C_CLK]), num(row[C_PWR]), num(row[C_LOAD])
    signature = pwr > 0 and pwr < SATURATED_W and clk > 2900 and load > 90
    _memload = num(row[C_MEMLOAD]) if len(row) > C_MEMLOAD else None
    if signature and _memload and _memload > MEMLOAD_STALL_MAX:
        if (now - last_memload_log) > 300:
            log("mem_load %.1f%% (> %.1f) at %.0fW - the card is MOVING DATA, not spinning. A stall "
                "reads ~4%%. NOT a stall." % (_memload, MEMLOAD_STALL_MAX, pwr))
            last_memload_log = now
        signature = False
    # Prefer mem_load for stall detection: per-order expected rates are often miscalibrated,
    # which can mask real stalls. Do not reintroduce a rate-based veto.
    _exp = expected_s_it()
    _kt = training and no_burst_training()     # v6.3: a Krea trainer - FLUX signature and band do not apply
    # Diagnosis logic for 'BORN DEGRADED' status. Uses train_degraded_arms() to
    # ensure reporting and action derive from the same predicates.
    _diag_hit, _diag_arms = train_degraded_arms(rate if (rate and rate > 1.0) else None, None, _exp)
    if (training and not _kt and not born_degraded_reported
            and (num(row[C_STEP]) or 0) >= BORN_DEGRADED_AFTER_STEP
            and _diag_hit):
        born_degraded_reported = True
        log("!! BORN DEGRADED: %.1fs/it [%s] at step %s. Slow since the start - the collapse "
            "detector cannot see this by design. v6.0 TRAIN-DEGRADED will escalate via the "
            "standard ladder." % (rate, _diag_arms, row[C_STEP].strip()))
        notify_dm("BORN-DEGRADED RUN: %.1f s/it [%s] at step %s. The daemon's rate detector is on "
                  "it (burst ladder -> lossless recycle, bounded)." % (rate, _diag_arms, row[C_STEP].strip()))
    # ── v6.0 TRAIN-DEGRADED: rate evidence, no signature required, no veto consulted ──────────
    # Baseline resets per RUN (a new order legitimately runs at a different rate).
    _cur_run = ""
    try:
        with open(STATED_RUN, "r", encoding="utf-8") as _f:
            _cur_run = json.load(_f).get("name") or ""
    except Exception:
        pass
    if _cur_run != t_run_name:
        t_run_name = _cur_run
        t_best_ema = t_best = None
        t_base_n = 0
        tdegraded = 0
        t_blind_said = False
        t_last_rate_seen = time.time()
        krea_st = {}
        krea_stuck_said = False

    _valid_rate = training and rate is not None and rate > 1.0
    if _valid_rate:
        t_last_rate_seen = now
        t_best_ema, t_best, t_base_n = baseline_update(t_best_ema, t_best, t_base_n, rate)

    _td_hit, _arms = train_degraded_arms(rate if _valid_rate else None, t_best, _exp)
    if (_td_hit and not _kt and (num(row[C_STEP]) or 0) >= BORN_DEGRADED_AFTER_STEP
            and not os.path.exists(TRAINER_STOP)):
        tdegraded += 1
        log("TRAIN-DEGRADED by throughput: %.1f s/it (best %s, floor %.1f) [%s] [%d/%d]"
            % (rate, ("%.1f" % t_best) if t_best else "unset", DOCUMENTED_STALL_MIN_S_IT,
               _arms, tdegraded, TRAIN_DEGRADED_CONFIRM))
    elif _valid_rate:
        tdegraded = 0

    # Reset blindness clock during non-training windows (checkpoints, renders) to avoid false alarms.
    _lane_training = False
    try:
        with open(STATED_RUN, "r", encoding="utf-8") as _f:
            _lane_training = (json.load(_f).get("state") or "").strip().lower() == "training"
    except Exception:
        pass
    if not _lane_training or render_active() or os.path.exists(TRAINER_STOP):
        t_last_rate_seen = now
    # v6.3 Krea trainer: judged by its own step advance, REPORT only (see KREA_STUCK_FACTOR).
    if _kt:
        if not _lane_training or render_active() or os.path.exists(TRAINER_STOP):
            if krea_st:
                krea_st["at"] = now      # a preview, pause or stop is not a static step
        else:
            _kprev = krea_st.get("step")
            _over = krea_step_watch(krea_st, num(row[C_STEP]), now)
            if _over and not krea_stuck_said:
                krea_stuck_said = True
                log("!! KREA TRAINER STATIC: step %s has not advanced for %.0f s (%.0fx its slowest step so far, "
                    "%.0f s). REPORT ONLY - no burst, no recycle." % (krea_st.get("step"), now - krea_st["at"],
                                                                     KREA_STUCK_FACTOR, krea_st.get("gap") or 0))
                notify_dm("Krea training has not advanced past step %s for %.0f min (its slowest step so far was "
                          "%.0f s). The daemon is taking no action - check whether the trainer is alive."
                          % (krea_st.get("step"), (now - krea_st["at"]) / 60.0, krea_st.get("gap") or 0))
            elif krea_stuck_said and _kprev is not None and krea_st.get("step") != _kprev:
                krea_stuck_said = False
                log("Krea trainer advancing again (step %s)" % krea_st.get("step"))
    if (training and not t_blind_said and (now - t_last_rate_seen) > TRAIN_BLIND_ALARM_S
            and not os.path.exists(TRAINER_STOP)):
        t_blind_said = True
        log("!! BLIND: state=training but no valid rate for %.0f s — this guard cannot currently "
            "judge the run. NOT rendering that as healthy." % (now - t_last_rate_seen))
        notify_dm("Stall daemon is BLIND: training but no measurable step rate for %.0f min. The "
                  "run may be fine — but the guard cannot tell, and says so rather than staying "
                  "green." % ((now - t_last_rate_seen) / 60.0))

    is_stall = training and signature and not _kt

    if is_stall:
        stalled += 1
    else:
        if stalled:
            log("clear (pwr %.0fW clk %.0f load %.0f) — resetting" % (pwr, clk, load))
        stalled = 0
    # Clear incident only when queue is idle (render finished), not during active renders.
    if not signature and (incident_attempts or incident_escalated) and (now - last_confirm) > ECHO_GUARD_SECS:
        if training or not render_active():
            if incident_escalated:
                notify_dm("incident RESOLVED — card saturated again after %d attempt(s)." % incident_attempts)
            incident_attempts = 0
            incident_escalated = False

    # ── v4.1 render-mode: same signature, no training step, ComfyUI actively rendering ──
    if not training and signature and render_active():
        rstalled += 1
        if rstalled == 1:
            rstall_progress0 = render_progress()
    else:
        if rstalled and not signature:
            log("render clear (pwr %.0fW clk %.0f load %.0f) — resetting" % (pwr, clk, load))
        if not (not training and signature):
            rstalled = 0

    # Render degraded if step rate exceeds threshold (no signature required).
    # Cold start safe: no steps advanced during model load, so rate is uncomputable.
    if not training and render_active() and not disabled:
        _rr = render_step_rate()
        if _rr is not None and _rr > RENDER_DEGRADED_S_PER_STEP:
            rdegraded += 1
            log("render DEGRADED by throughput: %.1f s/step (healthy ~6.5, threshold %.1f) [%d/%d]"
                % (_rr, RENDER_DEGRADED_S_PER_STEP, rdegraded, RENDER_DEGRADED_CONFIRM))
        elif _rr is not None:
            rdegraded = 0
        if (rdegraded >= RENDER_DEGRADED_CONFIRM
                and (time.time() - last_rescue) > RENDER_COOLDOWN
                and not render_nearly_done()):
            log("RENDER-DEGRADED CONFIRMED (%d slow readings) -> killing :8189 owner for a fresh "
                "context (operator-demonstrated remedy: clean re-render ~4 min incl. cold load)"
                % rdegraded)
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "$p = (Get-NetTCPConnection -LocalPort 8189 -ea 0 | Where-Object { $_.State -eq 'Listen' -or $_.RemoteAddress -in '0.0.0.0','::' } | "
                            "Select -First 1).OwningProcess; if ($p) { Stop-Process -Id $p -Force }"],
                           capture_output=True, timeout=30)
            notify_dm("render DEGRADED (step rate over %.0f s/step, healthy ~6.5) — context killed; "
                      "the render's owner reboots it fresh." % RENDER_DEGRADED_S_PER_STEP)
            rdegraded = 0
            rstalled = 0
            last_rescue = time.time()
            time.sleep(POLL_SECS)
            continue
    else:
        rdegraded = 0

    # Render stalls block the whole pipeline, so use a shorter 45s cooldown (vs standard 240s) to keep
    # the daemon checking continuously until the render clears.
    if (rstalled >= CONFIRM and not disabled and (time.time() - last_rescue) > RENDER_COOLDOWN
            and no_burst_render(render_queue())):
        _now_prog = render_progress()
        if progress_advanced(rstall_progress0, _now_prog):
            # the render's own steps moved during the signature: slow, not wedged - say so and keep watching
            log("render signature (pwr %.0fW clk %.0f) but its steps ADVANCED %s -> %s: not a wedge"
                % (pwr, clk, (rstall_progress0 or {}).get("step"), (_now_prog or {}).get("step")))
            rstalled = 0
            time.sleep(POLL_SECS)
            continue
        log("NO-BURST RENDER WEDGE: pwr=%.0fW clk=%.0f load=%.0f, no step advance (%s) -> killing :8189 owner, "
            "no burst (a burst is the collision)" % (pwr, clk, load, (_now_prog or {}).get("step", "no progress")))
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "$p = (Get-NetTCPConnection -LocalPort 8189 -ea 0 | Where-Object { $_.State -eq 'Listen' -or $_.RemoteAddress -in '0.0.0.0','::' } | "
                        "Select -First 1).OwningProcess; if ($p) { Stop-Process -Id $p -Force }"],
                       capture_output=True, timeout=30)
        notify_dm("Krea render wedged (no step advance at the wedge signature) — context killed with no burst; "
                  "the render's owner retries on a fresh context.")
        rstalled = 0
        attempts_since_clear = 0
        last_rescue = time.time()
        time.sleep(POLL_SECS)
        continue

    if rstalled >= CONFIRM and not disabled and (time.time() - last_rescue) > RENDER_COOLDOWN:
        log("RENDER-STALL CONFIRMED  pwr=%.0fW clk=%.0f load=%.0f (ComfyUI queue active) -> spawning fresh burst"
            % (pwr, clk, load))
        incident_attempts = incident_attempts + 1 if (time.time() - last_confirm) < INCIDENT_WINDOW else 1
        last_confirm = time.time()
        attempts_since_clear += 1
        if attempts_since_clear >= KILL_AFTER_ATTEMPTS and not render_nearly_done():
            # v4.6 kill-and-requeue backstop: zero clears across this many attempts = wedged context
            # (grinders clear intermittently — the 856s street survivor never trips this). Kill the
            # ComfyUI owner; whoever owns the render (foundry retry / worker / operator) boots fresh.
            log("WEDGED CONTEXT: %d attempts, zero clears -> killing :8189 owner for a fresh context" %
                attempts_since_clear)
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "$p = (Get-NetTCPConnection -LocalPort 8189 -ea 0 | Where-Object { $_.State -eq 'Listen' -or $_.RemoteAddress -in '0.0.0.0','::' } | "
                            "Select -First 1).OwningProcess; if ($p) { Stop-Process -Id $p -Force }"],
                           capture_output=True, timeout=30)
            notify_dm("wedged render context KILLED after %d burst-immune attempts — the render's owner "
                      "reboots it fresh (foundry/worker retry)." % attempts_since_clear)
            discriminator_probe("event-path")
            attempts_since_clear = 0
            rstalled = 0
            last_rescue = time.time()
            continue
        if incident_attempts >= 2 and not incident_escalated:
            incident_escalated = True
            notify_dm("RENDER stall persisting after %d rescue attempts (daemon fighting; context-kill "
                      "fires at %d burst-immune attempts)." % (incident_attempts, KILL_AFTER_ATTEMPTS))
        outcome = fire_burst_with_retries("render-rescue")
        rescues += 1
        rstalled = 0
        if outcome == "wedged":
            # v4.6.1: burst-immune render context — execute immediately, don't wait for event counts
            log("WEDGED CONTEXT (fast-path) -> killing :8189 owner for a fresh context")
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "$p = (Get-NetTCPConnection -LocalPort 8189 -ea 0 | Where-Object { $_.State -eq 'Listen' -or $_.RemoteAddress -in '0.0.0.0','::' } | "
                            "Select -First 1).OwningProcess; if ($p) { Stop-Process -Id $p -Force }"],
                           capture_output=True, timeout=30)
            notify_dm("wedged render context KILLED (fast-path: burst-immune ladder) — the render's "
                      "owner retries on a fresh context.")
            discriminator_probe("fast-path")
            attempts_since_clear = 0
            last_rescue = time.time()
            continue
        last_rescue = time.time() - (RENDER_COOLDOWN - 20.0 if outcome == "all-dud" else 0.0)   # v4.5: all-dud -> next swing in ~20s
        # graded loosely: render contexts end on their own; no ineffective accounting here
        continue

    # Trainer checkpointing/relaunching pegs card with no step advance, satisfying stall rule.
    # STOP_NOW file or rate < 0 (no data) indicates intentional pause, not a stall.
    # Reset stall counter to avoid false escalation.
    if stalled >= CONFIRM and (os.path.exists(TRAINER_STOP) or rate < 0):
        if not globals().get('_standdown_said'):
            globals()['_standdown_said'] = True
            log('stand-down: trainer is pausing/booting (STOP_NOW=%s rate=%.1f) - not a stall'
                % (os.path.exists(TRAINER_STOP), rate))
        stalled = 0
    elif stalled < CONFIRM:
        globals()['_standdown_said'] = False

    # v6.0: rate-evidence confirmation raises the SAME escalation as the signature. The whole
    # point is that the ladder below (burst -> all-dud -> lossless recycle, cooldowns, caps,
    # ineffective accounting) is shared machinery — one escalation path, two independent senses.
    if tdegraded >= TRAIN_DEGRADED_CONFIRM and not disabled:
        log("TRAIN-DEGRADED CONFIRMED (%d rate readings; no signature required) -> escalating via "
            "the standard ladder" % tdegraded)
        stalled = max(stalled, CONFIRM)
        tdegraded = 0

    if stalled >= CONFIRM and not disabled and (time.time() - last_rescue) > COOLDOWN:
        partial_bursts = 0
        log("STALL CONFIRMED  step=%s rate=%.1fs pwr=%.0fW clk=%.0f load=%.0f -> spawning fresh burst"
            % (step, rate, pwr, clk, load))
        incident_attempts = incident_attempts + 1 if (time.time() - last_confirm) < INCIDENT_WINDOW else 1
        last_confirm = time.time()
        if incident_attempts >= 2 and not incident_escalated:
            incident_escalated = True
            notify_dm("training stall PERSISTING after %d rescue attempts (step %s, ~2x slow). "
                      "Manual barrage may be needed." % (incident_attempts, step))
        outcome = fire_burst_with_retries("rescue")
        rescues += 1
        stalled = 0

        if outcome == "all-dud":
            # "all-dud" while trainer is live: indicates poisoned context held by trainer.
            # Bursts cannot clear it; must recycle trainer to break contention.
            if (rescue_pauses < MAX_RESCUE_PAUSES and time.time() - last_recycle > RECYCLE_MIN_GAP
                    and not os.path.exists(RESCUE_PAUSE) and trainer_running()):
                log("barrage EXHAUSTED (every fresh spawn born degraded) while a trainer is live "
                    "-> the trainer's own context holds the poison; no burst can clear it.")
                request_trainer_recycle(None)
                rescue_pauses += 1
                last_recycle = time.time()
                last_rescue = time.time()
                continue
            # no information about the flip mechanism — short cooldown, no ineffective count
            last_rescue = time.time() - COOLDOWN + SHORT_COOLDOWN
            continue
        last_rescue = time.time()
        if outcome == "failed":
            log("post-rescue #%d: payload failed outright (see above)" % rescues)
            continue

        time.sleep(SETTLE)
        after = last_row()
        a_age = row_age(after) if after else None
        # grade on a row written AFTER the burst ended (echo guard applies to grading too)
        if after and a_age is not None and a_age <= STALE_SECS and (time.time() - a_age) > burst_end:
            a_pwr = num(after[C_PWR])
            worked = a_pwr >= SATURATED_W
            fully = a_pwr >= TRUE_SATURATED_W
            log("post-rescue #%d: rate=%s pwr=%.0fW memload=%s%% -> %s"
                % (rescues, after[C_RATE], a_pwr, after[C_MEMLOAD],
                   ("FULLY SATURATED (%.0fW >= %.0f)" % (a_pwr, TRUE_SATURATED_W) if fully else
                    "partially flipped (%.0fW, target %.0f) - not done" % (a_pwr, TRUE_SATURATED_W))
                   if worked else "still stalled (INEFFECTIVE — fast burst, no flip)"))
            ineffective = 0 if worked else ineffective + 1

            # v5.3: flipped but not FULLY saturated -> keep bursting. The documented reproduction
            # walked 258->387->390->401 W (49 -> 5.66 s/it) across successive bursts; stopping at the
            # first >=300 W reading leaves ~2.5x of throughput on the table.
            if worked and not fully and partial_bursts < MAX_PARTIAL_BURSTS:
                partial_bursts += 1
                log("  -> bursting again (%d/%d) to climb toward %.0fW"
                    % (partial_bursts, MAX_PARTIAL_BURSTS, TRUE_SATURATED_W))
                last_rescue = time.time() - COOLDOWN + SHORT_COOLDOWN
                continue
            if fully and partial_bursts:
                log("  -> reached full saturation after %d extra burst(s)" % partial_bursts)
            partial_bursts = 0

            if (not worked and trainer_running() and rescue_pauses < MAX_RESCUE_PAUSES
                    and time.time() - last_recycle > RECYCLE_MIN_GAP
                    and not os.path.exists(RESCUE_PAUSE)):
                log("fast burst ran but did NOT flip -> a live context is holding the poison; "
                    "bursts cannot evict a holder. Escalating to a lossless trainer recycle.")
                request_trainer_recycle(None)
                rescue_pauses += 1
                last_recycle = time.time()
                last_rescue = time.time()
                ineffective = 0          # we acted on the evidence; do not also march toward disable
                continue

            # v5.0: "worked" on POWER is not "clean". Measure the card. If it is flipped but still far
            # below virgin health while a trainer is live, the trainer's own context is holding the
            # poison and no further burst can clear it — escalate to a LOSSLESS recycle instead of
            # declaring victory and leaving the run at 3x its true cost.
            if (worked and rescue_pauses < MAX_RESCUE_PAUSES
                    and time.time() - last_recycle > RECYCLE_MIN_GAP
                    and not os.path.exists(RESCUE_PAUSE)):
                achieved = discriminator_probe("post-rescue health")
                if achieved is not None and achieved < VIRGIN_TFLOPS and trainer_running():
                    request_trainer_recycle(achieved)
                    rescue_pauses += 1
                    last_recycle = time.time()
                    last_rescue = time.time()
                elif achieved is not None:
                    log("post-rescue health: %.1f TFLOP/s (virgin ~%.0f) -> %s"
                        % (achieved, VIRGIN_TFLOPS,
                           "card is clean" if achieved >= VIRGIN_TFLOPS else
                           "still dirty but no trainer to recycle"))
        else:
            log("post-rescue #%d: no fresh post-burst telemetry to grade against" % rescues)

        if ineffective >= MAX_INEFFECTIVE:
            disabled = True
            log("!! %d FAST-burst rescues in a row failed to flip -> DISABLING (duds never count toward this). "
                "The flip mechanism itself has changed — investigate before re-arming." % ineffective)
            notify_dm("SELF-DISABLED: %d fast bursts in a row failed to flip — the flip mechanism itself "
                      "changed. Run continues UNPROTECTED (stall = ~2x slow, not fatal). Restart daemon after "
                      "investigating: schtasks /end + /run \"P20 Stall Rescue\"." % ineffective)
        continue

    time.sleep(POLL_SECS)
