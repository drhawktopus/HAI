"""BigHonker 3070 time-to-failure probe, as a compute order payload.

Usage:
    bh_crash_ttf.py <ORDER_OUT> <arm> <budget_s> <clock: unlocked|MHz> [--cpu-stub] [--fan=PCT] [--load=gemm|llm]
                    [--pl=W] [--graphs=off] [--restore=unlocked|MHz]

Modes:
  --load=gemm: Sustained fp16 GEMM (torch, 8192x8192) until the card dies or the budget is spent.
  --load=llm: Runs ollama's bundled llama-server.exe on the model's own blobs using the exact flags from the
              original crash log to reproduce the specific failure mode. --graphs=off sets GGML_CUDA_DISABLE_GRAPHS=1.
              Heartbeat is tokens streamed.

  --pl=W: Sets the card's power limit for the run (volatile; restored at end).
  --restore: Clock left after the run (default: unlocked).

Verdict Logic:
  Card death (reboot or CUDA hang) often kills the payload process. The verdict is written by:
  1. The run itself, if it survives to budget or detects a loop hang (HANG_S).
  2. The next resume of this order, which detects 'started.json' without 'result.json' and infers death from the
     last heartbeat.

  Note: Heartbeat is the internal iteration counter, NOT nvidia-smi, as NVML continues to serve stale readings
  after device loss.

Output: result.json (arm, verdict died|survived, ttf_s, face, iterations, boots, clocks, system events)."""
import datetime
import json
import os
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HANG_S = 30
BEAT_EVERY = 1.0
OLLAMA = "http://127.0.0.1:11434"


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def ps(cmd, timeout=60):
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                              errors="replace", timeout=timeout).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return "ERR %r" % (e,)


def boot_time():
    return ps("(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('yyyy-MM-ddTHH:mm:ss')")


def smi(args, timeout=25):
    try:
        p = subprocess.run(["nvidia-smi"] + args, capture_output=True, text=True, errors="replace", timeout=timeout)
        return (p.stdout or "").strip() + (p.stderr or "").strip()
    except Exception as e:  # noqa: BLE001
        return "ERR %r" % (e,)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def system_events(since_iso):
    """Bugchecks (1001), nvlddmkm (153), unexpected shutdowns (41/6008) and WHEA since the run started."""
    cmd = ("$s=[datetime]'%s'; Get-WinEvent -FilterHashtable @{LogName='System';StartTime=$s} -EA SilentlyContinue | "
           "Where-Object { $_.Id -in 1001,41,6008 -or $_.ProviderName -in 'nvlddmkm','Microsoft-Windows-WHEA-Logger' } | "
           "ForEach-Object { $_.TimeCreated.ToString('s') + ' ' + $_.ProviderName + ' ' + $_.Id + ' ' + "
           "(($_.Message -split \"`n\")[0]).Substring(0,[Math]::Min(120,(($_.Message -split \"`n\")[0]).Length)) }" % since_iso)
    return [ln for ln in ps(cmd).splitlines() if ln.strip()]


PERF_COUNTERS = (r"\Processor(0)\% DPC Time", r"\Processor(0)\% Interrupt Time", r"\Processor(0)\Interrupts/sec",
                 r"\Processor(_Total)\% DPC Time")


def perf_start(out, stub):
    """typeperf at 1 s into ORDER_OUT/perf.csv for the whole run; survives our death only as far as the file got."""
    if stub:
        return None
    try:
        return subprocess.Popen(["typeperf"] + list(PERF_COUNTERS) + ["-si", "1", "-f", "CSV", "-y",
                                                                      "-o", os.path.join(out, "perf.csv")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        print("[crash] typeperf not started: %r" % (e,), flush=True)
        return None


def perf_summary(out):
    """{counter: {n, median, p95, max}} over the loaded seconds of ORDER_OUT/perf.csv; {} when there is none."""
    import csv
    import statistics
    try:
        with open(os.path.join(out, "perf.csv"), encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return {}
    if len(rows) < 3:
        return {}
    res = {}
    for j, name in enumerate(PERF_COUNTERS, start=1):
        xs = []
        for r in rows[1:]:
            try:
                xs.append(float(r[j]))
            except (IndexError, ValueError):
                pass
        if xs:
            xs.sort()
            res[name.split("\\", 1)[-1]] = {"n": len(xs), "median": round(statistics.median(xs), 2),
                                            "p95": round(xs[int(0.95 * (len(xs) - 1))], 2), "max": round(xs[-1], 2)}
    return res


def finish(out, started, verdict, face, beat, extra=None):
    t0 = datetime.datetime.fromisoformat(started["load_start"]) if started.get("load_start") else None
    last = datetime.datetime.fromisoformat(beat["t"]) if beat and beat.get("t") else None
    res = {"arm": started["arm"], "verdict": verdict, "face": face,
           "ttf_s": round((last - t0).total_seconds(), 1) if (t0 and last) else None,
           "iterations": (beat or {}).get("iters"), "it_per_s": (beat or {}).get("rate"),
           "load_start": started.get("load_start"), "last_beat": (beat or {}).get("t"),
           "clock_requested": started.get("clock"), "clocks_at_load": started.get("clocks_at_load"),
           "fan": started.get("fan"), "load": started.get("load"), "power_limit": started.get("power_limit"),
           "graphs": started.get("graphs"), "engine": started.get("engine"), "perf": perf_summary(out),
           "boot_at_start": started.get("boot"), "boot_now": boot_time(), "recorded": now(),
           "system_events": system_events(started.get("start", now()))}
    res.update(extra or {})
    write_json(os.path.join(out, "result.json"), res)
    print("[crash] RESULT %s" % json.dumps(res), flush=True)


LOST_SIGNS = ("GPU is lost", "No devices were found", "ERR ")
REBOOT_DELAY_S = 30


def card_lost():
    """True when nvidia-smi says the card is gone (or cannot answer, the wedge signature)."""
    out = smi(["-L"])
    return (not out.strip()) or any(s in out for s in LOST_SIGNS), out[:200]


RUN = {"server": None, "perf": None, "restore": "unlocked", "pl_default": None, "restore_pl": "180"}


def stop_children():
    for k in ("server", "perf"):
        p = RUN.get(k)
        if p is not None:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
            RUN[k] = None


def recover(stub, fan):
    """The order's own cleanup after a run it saw end. A lost card only comes back with a reboot, so the order
    schedules one (a reboot also restores the default power limit and the boot-time clock policy) and the factory
    resumes this order, which then finds its result recorded. A live card gets the production clock (--restore), its
    default power limit and fan curve back here. Never raises."""
    try:
        stop_children()
    except Exception:  # noqa: BLE001
        pass
    if stub:
        return
    try:
        lost, out = card_lost()
        if lost:
            print("[crash] card lost (%s) - recovery reboot in %d s" % (out.strip()[:120], REBOOT_DELAY_S), flush=True)
            subprocess.run(["shutdown", "/r", "/t", str(REBOOT_DELAY_S), "/c",
                            "bhcrash: GPU lost under the stock-clock test, recovery reboot"],
                           capture_output=True, text=True, timeout=30)
            return
        r = RUN["restore"]
        print("[crash] clock restore (%s): %s" % (r, smi(["-rgc"] if r == "unlocked" else ["-lgc", str(int(r))])[:120]),
              flush=True)
        if RUN["pl_default"]:
            # production runs at 180 W; never restore the board's 280 W default (it loses the card)
            print("[crash] power limit restored to %s W: %s" % (RUN["restore_pl"], smi(["-pl", RUN["restore_pl"]])[:120]),
                  flush=True)
        if fan is not None:
            print("[crash] fans restored: %s" % (nvml_fans(None),), flush=True)
    except Exception as e:  # noqa: BLE001
        print("[crash] recover failed: %r" % (e,), flush=True)


def nvml_fans(percent):
    """Pin every GPU fan at `percent` (None = restore the card's automatic curve). Returns what NVML reads back per fan
    afterwards - the arm records what was ACHIEVED, not what was asked for. Volatile: a reboot restores the curve."""
    import ctypes
    nv = ctypes.CDLL(r"C:\Windows\System32\nvml.dll")
    if nv.nvmlInit_v2() != 0:
        return "nvml init failed"
    try:
        h = ctypes.c_void_p()
        nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h))
        n = ctypes.c_uint()
        nv.nvmlDeviceGetNumFans(h, ctypes.byref(n))
        rcs = []
        for i in range(n.value):
            rcs.append(nv.nvmlDeviceSetDefaultFanSpeed_v2(h, i) if percent is None
                       else nv.nvmlDeviceSetFanSpeed_v2(h, i, int(percent)))
        time.sleep(3)
        got = []
        for i in range(n.value):
            s = ctypes.c_uint()
            nv.nvmlDeviceGetFanSpeed_v2(h, i, ctypes.byref(s))
            got.append(s.value)
        return {"requested": percent, "set_rc": rcs, "readback_pct": got}
    finally:
        nv.nvmlShutdown()


def unload_ollama():
    import urllib.request
    try:
        ps_ = json.load(urllib.request.urlopen(OLLAMA + "/api/ps", timeout=15))
        for m in ps_.get("models") or []:
            body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
            urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                                          headers={"Content-Type": "application/json"}), timeout=60)
            print("[crash] unloaded %s" % m["name"], flush=True)
    except Exception as e:  # noqa: BLE001
        print("[crash] ollama unload skipped: %r" % (e,), flush=True)


LLM_MODEL = "qwen3.5:9b"     # the model of both 10-21 deaths and 10-19's
LLM_CTX = 19968              # the context ollama gave it for the 10-21 crash (-c 19968 in its llama-server line)
LLM_PORT = 18431
LLM_PROMPT = ("Write a long, detailed technical essay on the history of electrical power grids, from the first "
              "generating stations to modern smart grids. Cover generation, transmission, distribution, frequency "
              "control, failures and blackouts, and the engineers involved. Use many sections and keep going.")


def ollama_home():
    """The running ollama.exe's directory - its bundled llama-server and ggml backends live under lib\\ollama."""
    path = ps("(Get-CimInstance Win32_Process -Filter \"Name='ollama.exe'\" | Select-Object -First 1).ExecutablePath")
    path = path.strip()
    return os.path.dirname(path) if path.lower().endswith("ollama.exe") else None


def model_blobs(model):
    """[model blob, projector blob?] - the files ollama itself loads, from its own /api/show modelfile."""
    import re
    import urllib.request
    req = urllib.request.Request(OLLAMA + "/api/show", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=60))
    return [p.strip() for p in re.findall(r"^FROM (.+)$", d.get("modelfile") or "", re.M) if os.path.isfile(p.strip())]


def llm_start(out, graphs_off):
    """ollama's bundled llama-server on the model's own blobs with the flags ollama used for the 10-21 crash, so the
    load is the production load and the environment is the arm's. Waits for /health while the server is alive and
    its log is still growing (a load that stops making progress for 120 s fails the start - it never loaded the card,
    so the order records no death). Raises unless the server is up AND on CUDA0."""
    import urllib.request
    home = ollama_home()
    if not home:
        raise RuntimeError("ollama.exe is not running - cannot find its bundled llama-server")
    exe = os.path.join(home, "lib", "ollama", "llama-server.exe")
    blobs = model_blobs(LLM_MODEL)
    if not blobs:
        raise RuntimeError("no model blob for %s from /api/show" % LLM_MODEL)
    cmd = [exe, "--model", blobs[0], "--port", str(LLM_PORT), "--host", "127.0.0.1", "--no-webui", "--offline",
           "-c", str(LLM_CTX), "-np", "1", "--log-verbosity", "4", "--no-log-prefix", "--no-log-timestamps",
           "--no-jinja", "--chat-template", "chatml", "--load-mode", "none",
           "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--flash-attn", "on", "-b", "512", "-ub", "512",
           "--context-shift", "--keep", "4"]
    if len(blobs) > 1:
        cmd += ["--mmproj", blobs[1]]
    libs = [os.path.join(home, "lib", "ollama"), os.path.join(home, "lib", "ollama", "cuda_v13")]
    # ggml.dll searches only its own folder for backends; the CUDA one lives in cuda_v13, loaded via GGML_BACKEND_PATH
    # (the env name read from ggml.dll itself, 10-21: without it the server came up CPU-only and the GPU check refused)
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="0", OLLAMA_LIBRARY_PATH=os.pathsep.join(libs),
               PATH=os.pathsep.join(libs + [os.environ.get("PATH", "")]),
               GGML_BACKEND_PATH=os.path.join(libs[1], "ggml-cuda.dll"))
    if graphs_off:
        env["GGML_CUDA_DISABLE_GRAPHS"] = "1"
    log_p = os.path.join(out, "llama-server.log")
    log = open(log_p, "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
    RUN["server"] = p
    size, moved = -1, time.time()
    while True:
        if p.poll() is not None:
            raise RuntimeError("llama-server exited rc=%s before it was healthy" % p.returncode)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % LLM_PORT, timeout=5) as r:
                if r.status == 200:
                    break
        except Exception:  # noqa: BLE001 - not up yet
            pass
        s = os.path.getsize(log_p)
        if s != size:
            size, moved = s, time.time()
        elif time.time() - moved > 120:
            raise RuntimeError("llama-server made no progress for 120 s and never became healthy")
        time.sleep(1)
    # on the GPU? asked of the card, not the log (the 10-21 first runs read a log that never names the device at
    # the default verbosity and refused a healthy server): the server's pid among the card's compute apps, or the
    # card's memory holding the model (production's load was 6.9 GB; the card idles at ~0.3 GB)
    apps = smi(["--query-compute-apps=pid", "--format=csv,noheader"])
    used = smi(["--query-gpu=memory.used", "--format=csv,noheader,nounits"]).strip()
    on_gpu = str(p.pid) in apps.split() or (used.isdigit() and int(used) >= 3000)
    if not on_gpu:
        raise RuntimeError("llama-server is not on the GPU (compute apps %r, memory.used %r MiB)" % (apps[:80], used))
    return {"exe": exe, "cmd": " ".join(cmd), "graphs_off": graphs_off, "gpu_memory_used_mib": used,
            "pid_on_gpu": str(p.pid) in apps.split()}


def llm_step(state):
    """One streamed completion; every streamed token advances the heartbeat. A per-read timeout a little over HANG_S
    makes a card that stopped answering raise here as well as trip the beater."""
    import urllib.request
    body = {"prompt": LLM_PROMPT, "n_predict": 1024, "stream": True, "cache_prompt": True, "temperature": 0.8}
    req = urllib.request.Request("http://127.0.0.1:%d/completion" % LLM_PORT, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=HANG_S + 5) as r:
        for raw in r:
            if raw.startswith(b"data:"):
                state["iters"] += 1


def set_power_limit(watts):
    """-> {requested, default, readback}; remembers the default for recover()."""
    q = smi(["--query-gpu=power.default_limit,power.limit", "--format=csv,noheader,nounits"])
    default = q.split(",")[0].strip() if "," in q else None
    RUN["pl_default"] = default
    set_out = smi(["-pl", str(watts)])
    back = smi(["--query-gpu=power.limit", "--format=csv,noheader,nounits"]).strip()
    return {"requested": watts, "default": default, "readback": back, "set": set_out[:160]}


def main():
    flags = ("--cpu-stub", "--fan=", "--load=", "--pl=", "--graphs=", "--restore=", "--restore-pl=")
    argv = [a for a in sys.argv[1:] if not a.startswith(flags)]
    stub = "--cpu-stub" in sys.argv

    def flag(name, default=None):
        return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(name + "=")), default)
    fan = int(flag("--fan")) if flag("--fan") else None
    load = flag("--load", "gemm")
    pl = int(flag("--pl")) if flag("--pl") else None
    graphs_off = flag("--graphs") == "off"
    RUN["restore"] = flag("--restore", "unlocked")
    RUN["restore_pl"] = flag("--restore-pl", "180")
    out, arm, budget, clock = argv[0], argv[1], float(argv[2]), argv[3]
    os.makedirs(out, exist_ok=True)
    started_p, beat_p, result_p = (os.path.join(out, n) for n in ("started.json", "beat.json", "result.json"))

    if os.path.exists(result_p):
        print("[crash] result already recorded: %s" % open(result_p, encoding="utf-8").read()[:400], flush=True)
        return 0
    prior = read_json(started_p)
    if prior and not prior.get("load_start"):
        # the earlier attempt never put load on the card (e.g. CUDA init failed): not a death, start fresh
        os.replace(started_p, started_p + ".noload-" + datetime.datetime.now().strftime("%H%M%S"))
        print("[crash] earlier attempt never loaded the card - set aside, starting fresh", flush=True)
        prior = None
    if prior:
        beat = read_json(beat_p)
        rebooted = boot_time() != prior.get("boot")
        finish(out, prior, "died", "box rebooted (bugcheck/hang)" if rebooted else "run ended without a verdict",
               beat, {"recorded_by": "resumed start of the same order"})
        return 0

    started = {"arm": arm, "clock": clock, "budget_s": budget, "start": now(), "boot": boot_time(), "stub": stub,
               "fan_requested": fan, "load": load, "graphs": "off" if graphs_off else "default",
               "restore": RUN["restore"]}
    if not stub:
        unload_ollama()
        # the limit in force BEFORE the arm touches it: since 10-21 production holds 180 W, so a "stock" baseline must
        # ask for --pl=280 explicitly - this field makes the record say which one it got
        started["power_limit_at_start"] = smi(["--query-gpu=power.limit", "--format=csv,noheader,nounits"]).strip()
        smi(["-rgc"] if clock == "unlocked" else ["-lgc", str(int(clock))])
        if fan is not None:
            started["fan"] = nvml_fans(fan)
            print("[crash] fans: %s" % (started["fan"],), flush=True)
        if pl is not None:
            started["power_limit"] = set_power_limit(pl)
            print("[crash] power limit: %s" % (started["power_limit"],), flush=True)
    write_json(started_p, started)

    state = {"iters": 0, "t": None}
    counts_itself = False            # an LLM step advances the heartbeat per token, inside the step
    if stub:
        hang_after = int(os.environ.get("BHCRASH_STUB_HANG_AFTER", "0"))   # test hook: a step that never returns

        def step():
            if hang_after and state["iters"] >= hang_after:
                time.sleep(10 ** 6)
            sum(i * i for i in range(20000))
    elif load == "llm":
        try:
            started["engine"] = llm_start(out, graphs_off)
        except Exception as e:  # noqa: BLE001 - never loaded the card: set aside by the next start, not a death
            print("[crash] LLM engine did not start: %r" % (e,), flush=True)
            recover(stub, fan)
            return 1
        write_json(started_p, started)
        counts_itself = True

        def step():
            llm_step(state)
    else:
        import torch
        dev = torch.device("cuda")
        a = torch.randn(8192, 8192, dtype=torch.float16, device=dev)
        b = torch.randn(8192, 8192, dtype=torch.float16, device=dev)
        ballast = [torch.empty(256 * 1024 * 1024, dtype=torch.uint8, device=dev) for _ in range(20)]  # ~5 GB resident

        def step():
            torch.mm(a, b)
            torch.cuda.synchronize()

    RUN["perf"] = perf_start(out, stub)
    if counts_itself:
        started["load_start"] = now()   # the first streamed token IS load; a full step first would skew ttf by ~20 s
    else:
        step()
        started["load_start"] = now()
    started["clocks_at_load"] = "stub" if stub else smi(["--query-gpu=clocks.sm,clocks.mem,power.draw,temperature.gpu",
                                                         "--format=csv,noheader"])
    write_json(started_p, started)
    print("[crash] load started %s clocks %s" % (started["load_start"], started["clocks_at_load"]), flush=True)

    stop = threading.Event()

    def beater():
        last_iters, last_change, t_prev = -1, time.time(), time.time()
        while not stop.is_set():
            time.sleep(BEAT_EVERY)
            it = state["iters"]
            if it != last_iters:
                rate = (it - max(last_iters, 0)) / max(time.time() - t_prev, 1e-6)
                last_iters, last_change, t_prev = it, time.time(), time.time()
                beat = {"t": now(), "iters": it, "rate": round(rate, 2)}
                write_json(beat_p, beat)
            elif time.time() - last_change > HANG_S:
                finish(out, started, "died", "loop stopped advancing for %ds (silent device loss)" % HANG_S,
                       read_json(beat_p), {"recorded_by": "the run itself", "smi": smi(["-L"])[:300]})
                recover(stub, fan)
                os._exit(0)

    threading.Thread(target=beater, daemon=True).start()
    t_end = time.time() + budget
    next_print = 0
    try:
        while time.time() < t_end:
            step()
            if not counts_itself:
                state["iters"] += 1
            el = budget - (t_end - time.time())
            if el >= next_print:
                print("[crash] %d/%d s  iters %d" % (int(el), int(budget), state["iters"]), flush=True)
                next_print += 30
    except Exception as e:  # noqa: BLE001 - a CUDA error mid-load is a death, recorded like the others
        stop.set()
        finish(out, started, "died", "step raised %s" % (repr(e)[:200],), read_json(beat_p),
               {"recorded_by": "the run itself", "smi": smi(["-L"])[:300]})
        recover(stub, fan)
        return 0
    stop.set()
    time.sleep(BEAT_EVERY * 1.5)
    stop_children()                  # typeperf flushes perf.csv on exit; finish() reads it
    time.sleep(1)
    finish(out, started, "survived", "budget spent", read_json(beat_p), {"recorded_by": "the run itself"})
    recover(stub, fan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
