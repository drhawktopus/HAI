"""Pins the TRAIN-DEGRADED detector to measured real-event fixtures: a threshold change that
un-pins a case is a regression, not a tuning choice. Run: python test_stall_rescue.py"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("p20_stall_rescue_mod",
                                              os.path.join(HERE, "p20_stall_rescue.py"))
mod = importlib.util.module_from_spec(spec)
# The daemon guards its main loop behind __name__ == "__main__" is NOT true here: it runs top to
# bottom. Import only what we need by executing the module's namespace up to the main loop would
# be fragile — instead pull the pure pieces via exec of their source segment.
src = open(os.path.join(HERE, "p20_stall_rescue.py"), encoding="utf-8").read()
# the module head resolves its own directory via __file__; an exec namespace has none
ns = {"__file__": os.path.join(HERE, "p20_stall_rescue.py")}
# Execute only constants + the two pure functions: cut at the first side-effectful section.
cut = src.index("def expected_s_it()")
exec(compile(src[:cut], "p20_stall_rescue_head", "exec"), ns)
train_degraded_arms = ns["train_degraded_arms"]
baseline_update = ns["baseline_update"]

failures = []
cases = [0]


def check(name, cond, detail=""):
    cases[0] += 1
    if cond:
        print("  ok  %s" % name)
    else:
        failures.append(name)
        print("FAIL  %s  %s" % (name, detail))


print("== train_degraded_arms: pinned incidents ==")

hit, arms = train_degraded_arms(92.0, 13.0, None)
check("v11 crawl fires (self+band)", hit and "self" in arms and "band" in arms, arms)

hit, arms = train_degraded_arms(42.75, 23.0, 28.5)
check("v9 regression fires via self-baseline", hit and "self" in arms, arms)

hit, arms = train_degraded_arms(40.8, 39.5, None)
check("legit 768px run stays silent", not hit, arms)

# Born-degraded from step 1 (self-baseline learned the degraded pace): the declared arm catches it
# when the order states an expectation (v11 declares 13 → 2.5x = 32.5).
hit, arms = train_degraded_arms(35.0, 35.0, 13.0)
check("born-degraded caught by declared arm", hit and "declared" in arms, arms)

# Deep born-degraded with NO declaration: absolute band is the backstop.
hit, arms = train_degraded_arms(50.0, 50.0, None)
check("born-degraded caught by documented band", hit and "band" in arms, arms)

# Healthy run, nothing declared: silent.
hit, arms = train_degraded_arms(13.2, 12.8, 13.0)
check("healthy run stays silent", not hit, arms)

# No rate at all (boot/pause writes -1): never a hit — the BLIND alarm owns that case instead.
hit, arms = train_degraded_arms(None, 13.0, 13.0)
check("no rate = no hit (blind alarm's job)", not hit, arms)

print("== baseline_update: cannot go blind at the moment of need ==")

# Warm-up: no baseline before TRAIN_BASELINE_MIN_SAMPLES.
ema = best = None
n = 0
for _ in range(ns["TRAIN_BASELINE_MIN_SAMPLES"] - 1):
    ema, best, n = baseline_update(ema, best, n, 13.0)
check("no baseline during warm-up", best is None, str(best))
ema, best, n = baseline_update(ema, best, n, 13.0)
check("baseline set after warm-up", best is not None and 12.0 < best < 14.0, str(best))

# A degraded stretch can only RAISE the EMA — the min never rises.
for _ in range(50):
    ema, best, n = baseline_update(ema, best, n, 95.0)
check("degraded stretch never raises baseline", 12.0 < best < 14.0, str(best))

# A single glitch row (0.1 s/it would be filtered; 2.0 is diluted by the EMA before touching min).
before = best
ema2, best2, n2 = baseline_update(ema, best, n, 2.0)
check("single glitch cannot crater the baseline", best2 >= before * 0.7, "%s -> %s" % (before, best2))

# Invalid rates ignored outright.
check("rate<=1 ignored", baseline_update(5.0, 4.0, 99, 0.5) == (5.0, 4.0, 99))

print("== beat(): a failing heartbeat write is LOUD (v6.1) ==")
import io as _io
import os as _os
import tempfile as _tempfile
import time as _time

_beat_calls = {"log": [], "dm": []}
_beat_src = src[src.index("_beat_fails = 0"):src.index("def release_singleton")]
_bns = {"io": _io, "os": _os, "time": _time, "STALL_RESCUE_HEARTBEAT": None,
        "log": lambda m: _beat_calls["log"].append(m),
        "notify_dm": lambda m: _beat_calls["dm"].append(m)}
exec(compile(_beat_src, "p20_stall_rescue_beat_segment", "exec"), _bns)
_beat = _bns["beat"]

_tmp = _tempfile.mkdtemp()
_hb = _os.path.join(_tmp, "hb.txt")
_bns["STALL_RESCUE_HEARTBEAT"] = _hb
_beat()
check("beat writes the file", _os.path.exists(_hb))
check("success keeps failure count at 0", _bns["_beat_fails"] == 0)

_bns["STALL_RESCUE_HEARTBEAT"] = _tmp   # a DIRECTORY: open(..., "w") fails
_beat(); _beat()
check("two failures stay quiet (transient blips)", not _beat_calls["dm"], _beat_calls["dm"])
_beat()
check("third consecutive failure fires exactly ONE dm", len(_beat_calls["dm"]) == 1,
      _beat_calls["dm"])
_beat()
check("further failures do not re-fire the dm", len(_beat_calls["dm"]) == 1)
check("alarm names the gate consequence, not just the error",
      "refuse" in _beat_calls["dm"][0] or "DOWN" in _beat_calls["dm"][0],
      _beat_calls["dm"][0][:120] if _beat_calls["dm"] else "(no dm)")

_bns["STALL_RESCUE_HEARTBEAT"] = _hb
_beat()
check("recovery resets the counter and re-arms the alarm",
      _bns["_beat_fails"] == 0 and _bns["_beat_alarmed"] is False,
      "fails=%s alarmed=%s" % (_bns["_beat_fails"], _bns["_beat_alarmed"]))
check("recovery is logged", any("RECOVERED" in m for m in _beat_calls["log"]),
      _beat_calls["log"][-3:])

print("== compute_watch_decide: the arm the r448 ratchet stood in for (v6.2) ==")
import datetime as _dt

compute_watch_decide = ns["compute_watch_decide"]
_now = 1_000_000.0


def _cr(state="compute", name="cap_r448", step=10, upd_age=5.0, **kw):
    d = {"state": state, "name": name, "step": step,
         "updated": (_dt.datetime.now() - _dt.timedelta(seconds=upd_age)).isoformat()}
    d.update(kw)
    return d


_SPEC = {"progress_glob": "shards/0*.npz", "progress_total": 40, "load": "gpu"}

check("no CURRENT_RUN -> None", compute_watch_decide(None, _SPEC, {}, _now) is None)
check("non-compute state -> None",
      compute_watch_decide(_cr(state="training"), _SPEC, {}, _now) is None)

_st = {}
check("first sight baselines as progress",
      compute_watch_decide(_cr(step=10), _SPEC, _st, _now) == "progress")
check("advancing step is progress",
      compute_watch_decide(_cr(step=11), _SPEC, _st, _now + 30) == "progress")
check("static inside the window is watching",
      compute_watch_decide(_cr(step=11), _SPEC, _st, _now + 60) == "watching")
check("static past 900s default on gpu is WEDGED",
      compute_watch_decide(_cr(step=11), _SPEC, _st, _now + 30 + 901) == "wedged")

_st = {}
compute_watch_decide(_cr(step=40), _SPEC, _st, _now)
check("static AT progress_total is ASSEMBLY, never a wedge (r448 npz: 13 min at 40/40)",
      compute_watch_decide(_cr(step=40), _SPEC, _st, _now + 3600) == "assembly")

_st = {}
_cpu = dict(_SPEC, load="cpu")
compute_watch_decide(_cr(step=5), _cpu, _st, _now)
check("cpu order static past window reports, never acts",
      compute_watch_decide(_cr(step=5), _cpu, _st, _now + 901) == "cpu_static")

check("stale runner beat -> runner_stale (the RUNNER is sick, not the payload)",
      compute_watch_decide(_cr(upd_age=120.0), _SPEC, {}, _now) == "runner_stale")
check("unparseable updated -> runner_stale, never a definite verdict",
      compute_watch_decide(_cr(updated="garbage"), _SPEC, {}, _now) == "runner_stale")
check("no progress_glob AND no published step -> unwatchable (the fit-order shape)",
      compute_watch_decide(_cr(step=0), {"load": "gpu"}, {}, _now) == "unwatchable")
check("spec unreadable with no published step -> unwatchable",
      compute_watch_decide(_cr(step=0), None, {}, _now) == "unwatchable")
check("published step WITHOUT a declared glob is watchable (worker-printed progress)",
      compute_watch_decide(_cr(step=10), {"load": "gpu"}, {}, _now) == "progress")

_st = {}
_declared = dict(_SPEC, progress_stale_after=60)
compute_watch_decide(_cr(step=7), _declared, _st, _now)
check("declared 60s window: static 50s is watching",
      compute_watch_decide(_cr(step=7), _declared, _st, _now + 50) == "watching")
check("declared 60s window: static 70s is wedged",
      compute_watch_decide(_cr(step=7), _declared, _st, _now + 70) == "wedged")

_st = {}
compute_watch_decide(_cr(name="order_a", step=5), _SPEC, _st, _now)
compute_watch_decide(_cr(name="order_b", step=5), _SPEC, _st, _now + 2000)
check("a NEW order resets the baseline (no inherited staleness)",
      compute_watch_decide(_cr(name="order_b", step=5), _SPEC, _st, _now + 2030) == "watching",
      _st)

print("== compute_watch_decide: the worker's own beat (2026-09-29, a 919 s thinking answer stopped as a wedge) ==")
_st = {}
compute_watch_decide(_cr(step=8), _SPEC, _st, _now)
check("a moving work_beat inside one long unit is WORKING, not wedged",
      compute_watch_decide(_cr(step=8, work_beat=120), _SPEC, _st, _now + 901) == "working")
check("...and resets the baseline, so the next static poll is watching",
      compute_watch_decide(_cr(step=8, work_beat=120), _SPEC, _st, _now + 1000) == "watching")
check("a beat that stops moving is WEDGED again past the window (a wedged card stops the stream)",
      compute_watch_decide(_cr(step=8, work_beat=120), _SPEC, _st, _now + 901 + 901) == "wedged")
_st = {}
compute_watch_decide(_cr(step=8), _SPEC, _st, _now)
check("no work_beat published (other payloads): unchanged behaviour, wedged past the window",
      compute_watch_decide(_cr(step=8), _SPEC, _st, _now + 901) == "wedged")

print("== watch_transition_note: the deck must not keep a stale UNWATCHABLE (2026-10-07) ==")
watch_transition_note = ns["watch_transition_note"]
_said = set()
check("an order never announced unwatchable gets no note",
      watch_transition_note("progress", "a1x", _said, 1) is None)
_said.add("unwatch:a1x")
check("still unwatchable: no note", watch_transition_note("unwatchable", "a1x", _said) is None)
_n = watch_transition_note("working", "a1x", _said, 1)
check("announced unwatchable, now publishing steps: one WATCHED line naming the step",
      _n is not None and "WATCHED" in _n and "(1)" in _n, _n)
check("said once", watch_transition_note("progress", "a1x", _said, 2) is None)
check("a stale runner is not a transition", watch_transition_note("runner_stale", "a1y", {"unwatch:a1y"}) is None)

print("== Krea renders get no burst; their wedge is judged by their own progress (2026-10-26) ==")
queue_unets, no_burst_render, progress_advanced = ns["queue_unets"], ns["no_burst_render"], ns["progress_advanced"]
# _KREA_Q models a live /queue payload where the UNET is identified by class_type UNETLoader (Krea2).
_KREA_Q = {"queue_running": [[0, "8ec28108", {"1": {"class_type": "UNETLoader", "inputs": {
    "unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_bf16.safetensors", "type": "krea2"}}}]],
    "queue_pending": []}
_FLUX_Q = {"queue_running": [[0, "a1", {"1": {"class_type": "UnetLoaderGGUF", "inputs": {
    "unet_name": "flux2-dev-Q3_K_M.gguf"}}}]], "queue_pending": []}
check("the Krea graph's UNET is read", queue_unets(_KREA_Q) == ["krea2_turbo_fp8_scaled.safetensors"],
      queue_unets(_KREA_Q))
check("a Krea render gets no burst", no_burst_render(_KREA_Q) is True)
check("a FLUX.2 render keeps its burst ladder", no_burst_render(_FLUX_Q) is False)
check("a pending Krea prompt counts too",
      no_burst_render({"queue_running": [], "queue_pending": _KREA_Q["queue_running"]}) is True)
check("an empty or unreadable queue is not a Krea render",
      no_burst_render({}) is False and no_burst_render(None) is False)
# render_progress.json readings: the 18:48:29 'stall' while the render read step 3 of 8 at 18:48:40
check("steps moved forward = advanced",
      progress_advanced({"step": 2, "max": 8, "ts": 100.0}, {"step": 3, "max": 8, "ts": 110.0}) is True)
check("no new step = not advanced (the wedge)",
      progress_advanced({"step": 2, "max": 8, "ts": 100.0}, {"step": 2, "max": 8, "ts": 100.0}) is False)
check("a new render restarting at step 0 is not progress of the old one",
      progress_advanced({"step": 6, "max": 8, "ts": 100.0}, {"step": 0, "max": 8, "ts": 140.0}) is False)
check("a missing reading is not evidence of progress",
      progress_advanced(None, {"step": 3, "ts": 1.0}) is False and progress_advanced({"step": 1, "ts": 1.0}, None) is False)
check("a malformed reading is not evidence either", progress_advanced({"step": "x"}, {"step": 3, "ts": 2}) is False)

print("== Krea TRAINERS get no burst and are judged by their own step advance, report-only (2026-10-26 19:17) ==")
spec_no_burst, krea_step_watch = ns["spec_no_burst"], ns["krea_step_watch"]
check("a Krea training order (base krea2) gets no burst", spec_no_burst({"base": "krea2", "mode": "train"}) is True)
check("a FLUX.2 order keeps the ladder", spec_no_burst({"base": "flux2"}) is False)
check("an order with no base keeps the ladder (the default before this existed)", spec_no_burst({}) is False)
check("an unreadable spec keeps the ladder", spec_no_burst(None) is False and spec_no_burst("krea2") is False)
_ks = {}
check("first reading: nothing to judge", krea_step_watch(_ks, 7, 1000.0) == 0.0)
check("the first move after attaching includes boot time - not counted as a step",
      krea_step_watch(_ks, 8, 1500.0) == 0.0 and not _ks.get("gap"))
check("static with no step interval measured yet: says nothing", krea_step_watch(_ks, 8, 9000.0) == 0.0)
_KMIN = ns["KREA_WATCH_MIN_INTERVALS"]
_ks = {}
krea_step_watch(_ks, 7, 0.0); krea_step_watch(_ks, 8, 85.0); krea_step_watch(_ks, 9, 185.0)
check("slowest step interval is kept (the 1024 test: 85 then 100 s)", _ks["gap"] == 100.0, _ks)
check("too few intervals to have seen the step mix: says nothing however long it sits",
      krea_step_watch(_ks, 9, 185.0 + 5000.0) == 0.0)
_t = 185.0
for _s in range(10, 9 + _KMIN):            # healthy 85 s steps until the watch has its intervals
    _t += 85.0
    krea_step_watch(_ks, _s, _t)
_last = 8 + _KMIN
check("armed after %d intervals" % _KMIN, _ks["n"] == _KMIN and _ks["step"] == _last, _ks)
check("static for 3x the slowest step is not yet reported (healthy 1024 steps ran to 100 s)",
      krea_step_watch(_ks, _last, _t + 300.0) == 0.0)
check("static past 3x the slowest step is reported, with the overrun",
      krea_step_watch(_ks, _last, _t + 330.0) == 30.0)
check("a move resets the clock", krea_step_watch(_ks, _last + 1, _t + 400.0) == 0.0 and _ks["at"] == _t + 400.0)
_ks = {}
_t = 0.0
for _s in range(1, 7):
    krea_step_watch(_ks, _s, _t)
    _t += 11.0
check("kq_int8 21:44: the first 1024 px step after six 512 px ones is NOT reported",
      krea_step_watch(_ks, 6, 55.0 + 46.0) == 0.0 and krea_step_watch(_ks, 6, 55.0 + 68.0) == 0.0, _ks)
_ks = {}
krea_step_watch(_ks, 1, 0.0); krea_step_watch(_ks, 2, 11.0); krea_step_watch(_ks, 7, 33.0)
check("a jump of several steps between rows is per-step time, not one long step", _ks["gap"] == 4.4, _ks)
_ks = {"step": 2000, "at": 0.0, "gap": 5.0, "moved": True}
check("a new run counting from step 1 starts fresh", krea_step_watch(_ks, 1, 50.0) == 0.0 and not _ks.get("gap"))
check("an unreadable step is not evidence", krea_step_watch(_ks, None, 99999.0) == 0.0)
_ks = {"step": 900, "at": 0.0, "gap": 5.0, "moved": True}
check("a blank step cell (num() -> -1) is not a new run and keeps the state",
      krea_step_watch(_ks, -1.0, 10.0) == 0.0 and _ks["step"] == 900 and _ks["gap"] == 5.0, _ks)

print()
if failures:
    print("FAILURES: %d — %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("ALL GREEN (%d cases)" % cases[0])
