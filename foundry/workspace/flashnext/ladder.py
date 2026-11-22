"""Flash-Next tuning ladder on llama-server run directly (the engine ollama bundles). Each arm is fully specified in a
plan file: build, --n-cpu-moe N, threads, extra flags. Per arm: load, VRAM from the box telemetry, one discarded
warm-up, 3 x 500-token generations (forced length), 2 x prefill of a ~4k-token prompt (prompt cache off).
Guards: refuses to run unless llama-server sees the ROCm GPU; an arm under 2 GB VRAM is CPU-only and stops the ladder;
an arm that spills VRAM into shared memory is INVALID and skipped. Per arm, a bottleneck profile over the generation
window: typeperf (CPU busy, RAM free, pages-in/s, disk read) + the box logger (gpu load, power, mem_load, spill).
Frontier mode entered first and restored in `finally` (and frontier_mode's auto pass now sees a direct llama-server).

    python ladder.py <ORDER_OUT> <plan.json> [order name, appended by the factory]
plan.json: {"arms": [{"name": "A0", "gguf": "<path to the gguf>", "n_cpu_moe": 28, "threads": 8, "extra": ["-fa", "on"],
                      "bin": "<engine folder>", "load": ["-lm", "none"], "env": {"GGML_CUDA_DISABLE_GRAPHS": "1"}}, ...]}
Only name / gguf / n_cpu_moe are required; bin defaults to ollama's bundle, load to --no-mmap, env to nothing, ctx to
16384. "fit": <MiB> replaces n_cpu_moe with llama.cpp's own fitter (rigby's placement: -fitt <MiB> -fitc <ctx>).
"""
import csv, json, os, re, statistics, subprocess, sys, threading, time, urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in (_ROOT, os.path.join(_ROOT, "bin")):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import foundry_paths as _P             # this workspace's ONE settings reader
# the ollama bundle whose lib\ollama holds the llama-server these scripts drive: FLASHNEXT_ENGINE_DIR (a setting);
# empty = every arm names its own "bin"
ENGINE_DIR = _P.setting("FLASHNEXT_ENGINE_DIR", "")
BIN = os.path.join(ENGINE_DIR, "lib", "ollama") if ENGINE_DIR else ""
ROCM = os.path.join(BIN, "rocm_v7_1") if BIN else ""
FRONTIER = r"C:\ProgramData\HAI\frontier_mode.py"
TELEM = os.path.join(_P.FOUNDRY_ROOT, "var", "state", "telemetry", "gpu_telemetry.csv")
OLLAMA = "http://127.0.0.1:11434"
PORT = 8093
GEN = ("Explain, in detail and with examples, how a mixture-of-experts language model routes tokens to experts, why "
       "that saves compute, and what trade-offs it creates for memory.\n\n")
LONG = " ".join(["The quarterly maintenance log lists each machine, its last service date, the technician, parts replaced, "
                 "and any follow-up needed. Summarise recurring failures and which parts are replaced most."] * 110)


def post(url, body, timeout=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def telem_lines():
    """Reads the box logger's CSV. The logger may hold a write lock, causing PermissionError on read; retry until unlocked, but return None if still locked after 20s. Telemetry is auxiliary and must never cause a finished arm to fail. Also returns None if no logger exists (e.g., BigHonker)."""
    if not os.path.exists(TELEM):
        return None
    for _ in range(20):
        try:
            return open(TELEM, encoding="utf-8", errors="replace").read().splitlines()
        except PermissionError:
            time.sleep(1)
    print("WARNING telemetry locked for 20 s, this reading is unavailable: %s" % TELEM, flush=True)
    return None


def telem_row():
    lines = telem_lines()
    return dict(zip(lines[0].split(","), lines[-1].split(","))) if lines else {}


def vram_used_gb():
    v = telem_row().get("vram_used_mb")
    if not v:                                   # no box logger: an NVIDIA card answers nvidia-smi itself
        try:
            v = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=30).stdout.split()[0]
        except (OSError, IndexError, subprocess.SubprocessError):
            v = None
    return round(float(v) / 1024, 1) if v else None


def spill_gb():
    """GPU memory Windows has spilled into system RAM (shared memory): a spilled arm is not a clean measurement."""
    v = telem_row().get("shared_spill_mb")
    return round(float(v) / 1024, 2) if v else None


def gpu_window(t0, t1):
    """Means of the box logger's gpu_load / power_w / mem_load_pct / cpu_pct / ram_pct and the max spill over the
    window [t0, t1] (strings in the logger's own "%Y-%m-%d %H:%M:%S" form)."""
    lines = telem_lines()
    if not lines:
        return {"samples": 0, "unavailable": "telemetry locked" if os.path.exists(TELEM) else "no box logger"}
    hdr = lines[0].split(",")
    sel = [dict(zip(hdr, l.split(","))) for l in lines[-600:]]
    sel = [r for r in sel if t0 <= r.get("ts", "") <= t1]

    def mean(k):
        v = [float(r[k]) for r in sel if r.get(k)]
        return round(statistics.mean(v), 1) if v else None
    spill = [float(r["shared_spill_mb"]) for r in sel if r.get("shared_spill_mb")]
    return {"samples": len(sel), "gpu_load": mean("gpu_load"), "power_w": mean("power_w"), "core_mhz": mean("core_mhz"),
            "mem_load_pct": mean("mem_load_pct"), "cpu_pct": mean("cpu_pct"), "ram_pct": mean("ram_pct"),
            "spill_gb_max": round(max(spill) / 1024, 2) if spill else None}


# Disk instances are addressed by typeperf index, not drive letter (0=D, 1=E, 2=C, 3=F, 4=B on Mothership).
# Verify with `typeperf -qx` before changing, as indices can shift on re-install.
COUNTERS = [r"\Processor Information(*)\% Processor Time", r"\Processor Information(_Total)\% Processor Performance",
            r"\Memory\Available MBytes", r"\Memory\Pages Input/sec", r"\Memory\Page Reads/sec", r"\Memory\Cache Bytes",
            r"\Memory\Standby Cache Normal Priority Bytes", r"\Memory\Modified Page List Bytes", r"\Memory\Committed Bytes",
            r"\Paging File(_Total)\% Usage",
            r"\PhysicalDisk(*)\Disk Read Bytes/sec", r"\PhysicalDisk(*)\Avg. Disk Queue Length",
            r"\PhysicalDisk(*)\Avg. Disk sec/Read", r"\PhysicalDisk(*)\% Idle Time",
            r"\GPU Engine(*)\Utilization Percentage", r"\GPU Adapter Memory(*)\Dedicated Usage",
            r"\GPU Adapter Memory(*)\Shared Usage"]


def start_sampler(path):
    """typeperf at 2 s into a CSV for the arm's generation window, the whole tree above (wildcards expand at start,
    so the engine's GPU-engine instances exist: the sampler starts after the server is up)."""
    return subprocess.Popen(["typeperf"] + COUNTERS + ["-si", "2", "-f", "CSV", "-o", path, "-y"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cols(path):
    """{column path: [values...]} from a typeperf CSV; blank or unparsable cells are skipped, never read as 0."""
    rows = list(csv.reader(open(path, encoding="utf-8", errors="replace")))
    if len(rows) < 2:
        return {}
    hdr = rows[0]
    out = {h: [] for h in hdr[1:]}
    for r in rows[1:]:
        for h, v in zip(hdr[1:], r[1:]):
            try:
                out[h].append(float(v))
            except ValueError:
                pass
    return out


def tree_summary(cols):
    """Group the counters into the system tree: mean (and max where a peak matters) per subsystem and instance."""
    def m(vs):
        return round(statistics.mean(vs), 2) if vs else None

    def pick(sub):
        return {h: v for h, v in cols.items() if sub in h and v}
    t = {"cpu": {}, "memory": {}, "disk": {}, "gpu": {}}
    cores = {h.split("(")[1].split(")")[0]: v for h, v in pick(r"% Processor Time").items() if "_Total" not in h}
    core_means = sorted((m(v) for v in cores.values()), reverse=True)
    tot = [v for h, v in pick(r"% Processor Time").items() if "(_Total)" in h]
    t["cpu"] = {"total_pct": m(tot[0]) if tot else None, "busiest_core_pct": core_means[0] if core_means else None,
                "cores_over_80pct": sum(1 for c in core_means if c and c > 80), "n_cores": len(core_means),
                "perf_pct": m(next(iter(pick("% Processor Performance").values()), []))}
    for key, sub, scale in (("available_gb_min", r"\Available MBytes", None), ("pages_input_s", r"\Pages Input/sec", 1),
                            ("hard_fault_reads_s", r"\Page Reads/sec", 1), ("cache_gb", r"\Cache Bytes", 2 ** 30),
                            ("standby_gb", r"Standby Cache Normal", 2 ** 30), ("modified_gb", r"Modified Page List", 2 ** 30),
                            ("committed_gb", r"\Committed Bytes", 2 ** 30), ("pagefile_pct", r"% Usage", 1)):
        v = next(iter(pick(sub).values()), [])
        if key == "available_gb_min":
            t["memory"][key] = round(min(v) / 1024, 2) if v else None
        else:
            t["memory"][key] = round(m(v) / scale, 2) if v and m(v) is not None else None
    disks = {}
    for h, v in cols.items():
        if "PhysicalDisk(" not in h or "_Total" in h or not v:
            continue
        d = h.split("PhysicalDisk(")[1].split(")")[0].split(" ")[-1]
        k = h.rsplit("\\", 1)[-1]
        disks.setdefault(d, {})[{"Disk Read Bytes/sec": "read_mb_s", "Avg. Disk Queue Length": "queue",
                                 "Avg. Disk sec/Read": "read_ms", "% Idle Time": "idle_pct"}.get(k, k)] = (
            round(m(v) / 1048576, 1) if k == "Disk Read Bytes/sec" else round(m(v) * 1000, 2) if k == "Avg. Disk sec/Read"
            else m(v))
    t["disk"] = disks
    eng = {}
    for h, v in pick("GPU Engine(").items():
        et = h.split("engtype_")[-1].split(")")[0].strip()
        eng.setdefault(et, []).append(v)
    t["gpu"]["engine_util_pct"] = {et: round(sum(m(v) or 0 for v in vs), 1) for et, vs in eng.items()}
    ded = [m(v) for v in pick("Dedicated Usage").values()]
    sh = [m(v) for v in pick("Shared Usage").values()]
    t["gpu"]["dedicated_gb"] = round(max(ded) / 2 ** 30, 2) if ded else None
    t["gpu"]["shared_gb"] = round(max(sh) / 2 ** 30, 2) if sh else None
    return t


SMI_Q = "clocks.sm,clocks.mem,pstate,utilization.gpu,power.draw"


def start_smi(path):
    """Samples NVIDIA GPU metrics (clock, power, util) every ~500ms via NVML in-process.

    Using NVML avoids the overhead of spawning `nvidia-smi` repeatedly, which would distort
    the performance measurements being taken. Returns None if NVML is unavailable (e.g., AMD)."""
    import ctypes
    try:
        nv = ctypes.WinDLL("nvml.dll")
        if nv.nvmlInit_v2() != 0:
            return None
        h = ctypes.c_void_p()
        if nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h)) != 0:
            return None
    except (OSError, AttributeError):
        return None

    class Util(ctypes.Structure):
        _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]
    stop = threading.Event()

    def tick():
        sm, mem, ps, mw, u = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_int(), ctypes.c_uint(), Util()
        with open(path, "w", encoding="utf-8") as f:
            while not stop.is_set():
                if (nv.nvmlDeviceGetClockInfo(h, 1, ctypes.byref(sm)) == 0                 # NVML_CLOCK_SM
                        and nv.nvmlDeviceGetClockInfo(h, 2, ctypes.byref(mem)) == 0        # NVML_CLOCK_MEM
                        and nv.nvmlDeviceGetPerformanceState(h, ctypes.byref(ps)) == 0
                        and nv.nvmlDeviceGetUtilizationRates(h, ctypes.byref(u)) == 0
                        and nv.nvmlDeviceGetPowerUsage(h, ctypes.byref(mw)) == 0):
                    f.write("%d, %d, P%d, %d, %.2f\n" % (sm.value, mem.value, ps.value, u.gpu, mw.value / 1000.0))
                    f.flush()
                stop.wait(0.5)
    t = threading.Thread(target=tick, daemon=True)
    t.start()
    return stop, t


def stop_smi(proc, path):
    """{core_mhz median/min/max, mem_mhz median, util/power mean, pstates seen} from start_smi's file, or None."""
    if proc is None:
        return None
    stop, t = proc
    stop.set()
    t.join(15)
    rows = []
    for line in open(path, encoding="utf-8", errors="replace").read().splitlines() if os.path.exists(path) else []:
        f = [x.strip() for x in line.split(",")]
        try:
            rows.append((float(f[0]), float(f[1]), f[2], float(f[3]), float(f[4])))
        except (ValueError, IndexError):
            pass
    if not rows:
        return {"samples": 0}
    core = [r[0] for r in rows]
    return {"samples": len(rows), "core_mhz_median": statistics.median(core), "core_mhz_min": min(core),
            "core_mhz_max": max(core), "mem_mhz_median": statistics.median(r[1] for r in rows),
            "pstates": sorted({r[2] for r in rows}), "util_pct_mean": round(statistics.mean(r[3] for r in rows), 1),
            "power_w_mean": round(statistics.mean(r[4] for r in rows), 1)}


def stop_sampler(proc, path):
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"], capture_output=True)
    proc.wait()
    cols = _cols(path)
    if not cols:
        return {"samples": 0}
    t = tree_summary(cols)
    free = next((v for h, v in cols.items() if h.endswith(r"\Available MBytes")), [])
    pin = next((v for h, v in cols.items() if h.endswith(r"\Pages Input/sec")), [])
    total_rd = sum(d.get("read_mb_s") or 0 for d in t["disk"].values())
    return {"samples": len(free), "cpu_pct": t["cpu"]["total_pct"],
            "ram_free_gb_min": t["memory"]["available_gb_min"],
            "pages_in_s": round(statistics.mean(pin)) if pin else None, "pages_in_s_max": round(max(pin)) if pin else None,
            "disk_read_mb_s": round(total_rd, 1), "tree": t}


def tree_lines(name, sysp, gpup):
    """The tree as printable lines: every subsystem's load next to its ceiling, idle resources included."""
    t = sysp.get("tree")
    if not t:
        return ["%s: tree: no samples" % name]
    c, mem, g = t["cpu"], t["memory"], t["gpu"]
    out = ["%s: TREE gpu   core %s MHz | %s W | engines %s | VRAM %s GB dedicated + %s GB shared | mem_load %s%%"
           % (name, gpup.get("core_mhz"), gpup.get("power_w"), g.get("engine_util_pct"), g.get("dedicated_gb"),
              g.get("shared_gb"), gpup.get("mem_load_pct")),
           "%s: TREE cpu   total %s%% | busiest core %s%% | %s/%s cores >80%% | clock %s%% of base"
           % (name, c["total_pct"], c["busiest_core_pct"], c["cores_over_80pct"], c["n_cores"], c["perf_pct"]),
           "%s: TREE ram   free min %s GB | file cache %s GB | standby %s GB | modified %s GB | committed %s GB"
           % (name, mem["available_gb_min"], mem["cache_gb"], mem["standby_gb"], mem["modified_gb"], mem["committed_gb"]),
           "%s: TREE paging pages-in %s/s | hard-fault reads %s/s | pagefile %s%% used"
           % (name, mem["pages_input_s"], mem["hard_fault_reads_s"], mem["pagefile_pct"])]
    for d, v in sorted(t["disk"].items()):
        out.append("%s: TREE disk %s read %s MB/s | queue %s | %s ms/read | idle %s%%"
                   % (name, d, v.get("read_mb_s"), v.get("queue"), v.get("read_ms"), v.get("idle_pct")))
    return out


def profile_line(name, sysp, gpup):
    return ("%s: profile cpu %s%% | pages-in %s/s (max %s) | disk read %s MB/s | RAM free min %s GB | gpu load %s%% %s W"
            " @ %s MHz | mem_load %s%% | spill max %s GB" % (name, sysp.get("cpu_pct"), sysp.get("pages_in_s"),
                                                 sysp.get("pages_in_s_max"), sysp.get("disk_read_mb_s"), sysp.get("ram_free_gb_min"),
                                                 gpup.get("gpu_load"), gpup.get("power_w"), gpup.get("core_mhz"),
                                                 gpup.get("mem_load_pct"), gpup.get("spill_gb_max")))


def wait_ready(proc):
    while proc.poll() is None:
        try:
            if json.loads(urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=5).read()).get("status") == "ok":
                return True
        except (OSError, ValueError):
            pass
        time.sleep(2)
    return False


TIMINGS = []


SAMPLES = []


def complete(prompt, n, temperature=0):
    d = post("http://127.0.0.1:%d/completion" % PORT, {"prompt": prompt, "n_predict": n, "temperature": temperature,
                                                       "ignore_eos": True, "cache_prompt": False})
    t = d.get("timings", {})
    TIMINGS.append(t)                                          # full block: draft_n / draft_n_accepted when speculating
    SAMPLES.append(d.get("content") or "")                     # full text: quality arms compare whole answers
    return t.get("predicted_per_second"), t.get("prompt_per_second"), t.get("prompt_n")


def concurrent_batch(prompts, n, temperature=0):
    """Fire len(prompts) requests at once (needs -np >= len). Returns (aggregate tok/s over wall time, per-request
    results). Aggregate = all predicted tokens / wall clock from first send to last reply."""
    import threading
    out = [None] * len(prompts)

    def one(i):
        out[i] = complete(prompts[i], n, temperature)
    th = [threading.Thread(target=one, args=(i,)) for i in range(len(prompts))]
    t0 = time.time()
    for t in th:
        t.start()
    for t in th:
        t.join()
    wall = time.time() - t0
    toks = sum(int(TIMINGS[-1 - k].get("predicted_n") or 0) for k in range(len(prompts)))
    return (toks / wall if wall else 0.0), out


def engine_env(bin_dir):
    """PATH + GGML_BACKEND_PATH for an engine folder (the ROCm or CUDA backend may sit in a subfolder: without the path
    the server silently runs CPU-only). ollama's NVIDIA bundle ships cuda_v12 and cuda_v13: the newest is taken (an
    arm's env may name another GGML_BACKEND_PATH)."""
    gpu = sorted(os.path.join(d, f) for d, _, fs in os.walk(bin_dir) for f in fs
                 if f.lower() in ("ggml-hip.dll", "ggml-cuda.dll"))
    dirs = sorted({os.path.dirname(h) for h in gpu})
    env = dict(os.environ, PATH=";".join([bin_dir] + dirs + [os.environ.get("PATH", "")]))
    if gpu:
        env["GGML_BACKEND_PATH"] = gpu[-1]
    return env


def main():
    out, plan = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
    os.makedirs(out, exist_ok=True)
    for b in sorted({a.get("bin", BIN) for a in plan["arms"]}):
        devs = subprocess.run([os.path.join(b, "llama-server.exe"), "--list-devices"], capture_output=True, text=True,
                              cwd=b, env=engine_env(b)).stdout
        print("devices [%s]:" % b, " | ".join(l.strip() for l in devs.splitlines() if l.strip()), flush=True)
        assert re.search(r"\b(ROCm|Vulkan|CUDA)\d", devs), \
            "llama-server in %s sees no GPU device - refusing to run a CPU-only ladder" % b
        # CPU offloaded experts run without SIMD on non-optimized builds; this check ensures AVX2 is present to prevent invalid scalar results. system_info is printed before model load.
        probe = subprocess.run([os.path.join(b, "llama-server.exe"), "-m", "nonexistent.gguf", "-v", "--port", str(PORT + 1)],
                               capture_output=True, text=True, cwd=b, env=engine_env(b), timeout=60)
        sysinfo = next((l.strip() for l in (probe.stdout + probe.stderr).splitlines() if "system_info" in l), "")
        print("cpu backend [%s]:" % b, sysinfo[sysinfo.find("CPU :"):][:160] or "(no system_info line)", flush=True)
        assert "AVX2 = 1" in sysinfo, "CPU backend in %s has no AVX2 - refusing to run scalar expert kernels" % b
    try:
        for m in json.loads(urllib.request.urlopen(OLLAMA + "/api/ps", timeout=30).read()).get("models", []):
            post(OLLAMA + "/api/generate", {"model": m["name"], "keep_alive": 0}, 120)
            print("unloaded ollama model", m["name"], flush=True)
    except OSError:
        pass
    res = {"arms": [], "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    frontier = os.path.exists(FRONTIER)                  # Mothership's frontier mode; other boxes have none
    if frontier:
        print("frontier enter:", subprocess.run([sys.executable, FRONTIER, "enter"], capture_output=True,
                                                text=True).stdout[-120:], flush=True)
    try:
        for a in plan["arms"]:
            log = os.path.join(out, "server_%s.log" % a["name"])
            b = a.get("bin", BIN)
            env = engine_env(b)
            env.update(a.get("env", {}))                       # per-arm env, e.g. GGML_CUDA_DISABLE_GRAPHS=1
            del TIMINGS[:]
            del SAMPLES[:]
            ctx = int(a.get("ctx", 16384))
            if a.get("fit") is not None:
                # rigby's placement (tolkien_llm.server_argv): llama.cpp's own fitter fills the card to a margin of
                # `fit` MiB, sized for ctx - no hand -ngl / --n-cpu-moe / -ot, which would switch it off
                place = ["-fitt", str(a["fit"]), "-fitc", str(ctx)]
            else:
                place = ["-ngl", "99", "--n-cpu-moe", str(a["n_cpu_moe"]), "-ot", "per_layer_token_embd=CPU"]
            argv = [os.path.join(b, "llama-server.exe"), "-m", a["gguf"]] + place + list(a.get("load", ["--no-mmap"])) + [
                    "-t", str(a.get("threads", 8)), "-c", str(ctx),
                    "--host", "127.0.0.1", "--port", str(PORT)] + list(a.get("extra", []))
            arm = dict(a, argv=argv)
            t0 = time.time()
            with open(log, "w", encoding="utf-8") as lf:
                proc = subprocess.Popen(argv, stdout=lf, stderr=subprocess.STDOUT, cwd=b, env=env)
            try:
                ok = wait_ready(proc)
                arm["load_s"] = round(time.time() - t0, 1)
                if not ok:
                    tail = open(log, encoding="utf-8", errors="replace").read().splitlines()[-4:]
                    arm["result"] = "FAILED TO LOAD (rc %s): %s" % (proc.returncode, " | ".join(tail)[-300:])
                    print("%s: %s" % (a["name"], arm["result"]), flush=True)
                    res["arms"].append(arm)
                    continue
                time.sleep(12)
                arm["vram_used_gb"] = vram_used_gb()
                arm["spill_gb"] = spill_gb()
                # "REPACK" engine capability does not guarantee repacking for this model; parse logs to verify actual buffer types (e.g., Q2_0 may repack 0 bytes) and detect slow-kernel fallbacks.
                ltxt = open(log, encoding="utf-8", errors="replace").read()
                arm["cpu_repack_mib"] = max([float(x) for x in re.findall(r"CPU_REPACK model buffer size =\s*([\d.]+) MiB", ltxt)] or [0.0])
                arm["cpu_plain_mib"] = max([float(x) for x in re.findall(r"\bCPU model buffer size =\s*([\d.]+) MiB", ltxt)] or [0.0])
                print("%s: CPU-side %.0f MiB plain + %.0f MiB repacked%s" % (a["name"], arm["cpu_plain_mib"], arm["cpu_repack_mib"],
                      "  <- NOTHING REPACKED: slow kernels on every CPU-side tensor" if arm["cpu_repack_mib"] == 0 else ""), flush=True)
                if arm["spill_gb"] is not None and arm["spill_gb"] > 0.5:
                    arm["result"] = "INVALID: %.1f GB spilled to shared memory" % arm["spill_gb"]
                    print("%s: %s - skipping (more n_cpu_moe needed)" % (a["name"], arm["result"]), flush=True)
                    res["arms"].append(arm)
                    continue
                if arm["vram_used_gb"] is not None and arm["vram_used_gb"] < 2:
                    arm["result"] = "INVALID: CPU-only (%.1f GB VRAM)" % arm["vram_used_gb"]
                    print("%s: %s - stopping" % (a["name"], arm["result"]), flush=True)
                    res["arms"].append(arm)
                    break
                complete(GEN, 64)
                sampler = start_sampler(os.path.join(out, "sys_%s.csv" % a["name"]))
                smi = None if a.get("gpu_sampler") == "off" else start_smi(os.path.join(out, "smi_%s.csv" % a["name"]))
                t_gen0 = time.strftime("%Y-%m-%d %H:%M:%S")
                try:
                    temp = float(a.get("temperature", 0))
                    if a.get("concurrency"):                 # N requests at once per level; aggregate tok/s is the product
                        pool = [p + "\n\n" for p in a.get("prompts", [])] or [GEN]
                        gens, arm["aggregate"] = [], []
                        for c in a["concurrency"]:
                            agg, per = concurrent_batch([pool[k % len(pool)] for k in range(c)], 500, temp)
                            gens += per
                            arm["aggregate"].append({"concurrency": c, "aggregate_tok_s": round(agg, 2),
                                                     "per_request": [round(g[0] or 0, 2) for g in per]})
                            print("%s: concurrency %d -> aggregate %.2f tok/s (per request %s)"
                                  % (a["name"], c, agg, [round(g[0] or 0, 2) for g in per]), flush=True)
                    elif a.get("prompts"):                   # one 500-token generation per prompt (varied-prompt arms)
                        gens = [complete(p + "\n\n", 500, temp) for p in a["prompts"]]
                    else:                                    # the fixed prompt, N times (per-arm count; steady-state arms need more)
                        gens = [complete(GEN, 500, temp) for _ in range(int(a.get("gens", 3)))]
                finally:
                    t_gen1 = time.strftime("%Y-%m-%d %H:%M:%S")
                    arm["profile"] = {"sys": stop_sampler(sampler, os.path.join(out, "sys_%s.csv" % a["name"])),
                                      "gpu": gpu_window(t_gen0, t_gen1),
                                      "smi": stop_smi(smi, os.path.join(out, "smi_%s.csv" % a["name"]))}
                    if arm["profile"]["smi"]:
                        print("%s: SMI %s" % (a["name"], arm["profile"]["smi"]), flush=True)
                    print(profile_line(a["name"], arm["profile"]["sys"], arm["profile"]["gpu"]), flush=True)
                    for line in tree_lines(a["name"], arm["profile"]["sys"], arm["profile"]["gpu"]):
                        print(line, flush=True)
                pres = [complete(LONG + " (%d)" % i, 1) for i in range(2)]
                arm.update({"gen_tok_s": [round(g[0], 2) for g in gens], "prefill_tok_s": [round(p[1], 1) for p in pres],
                            "prefill_tokens": pres[0][2], "timings": list(TIMINGS), "samples": list(SAMPLES), "result": "ok"})
                print("%s: sample (gen 2, first 160 chars): %r" % (a["name"], SAMPLES[2][:160] if len(SAMPLES) > 2 else ""),
                      flush=True)
                drafted = sum(t.get("draft_n") or 0 for t in TIMINGS[1:1 + len(gens)])
                accepted = sum(t.get("draft_n_accepted") or 0 for t in TIMINGS[1:1 + len(gens)])
                if drafted:
                    arm["draft_acceptance"] = round(accepted / drafted, 3)
                    print("%s: draft acceptance %.1f%% (%d of %d drafted tokens)" % (a["name"], 100.0 * accepted / drafted,
                                                                                  accepted, drafted), flush=True)
                hr = re.findall(r"moe-cache: steps=\d+ hits=(\d+) misses=(\d+) hit-rate=([\d.]+)%",
                                open(log, encoding="utf-8", errors="replace").read())
                if hr:
                    arm["cache_hit_rate_pct"] = float(hr[-1][2])
                if "--moe-expert-cache" in a.get("extra", []) and (not hr or int(hr[-1][0]) + int(hr[-1][1]) == 0):
                    # Zero hits and misses means the cache was never engaged, so the metrics reflect a no-cache run, not a failed cache.
                    arm["result"] = "INVALID: expert cache never engaged (hits+misses = 0) - numbers are a no-cache run"
                    print("%s: %s" % (a["name"], arm["result"]), flush=True)
                print("%s: TREE engine  draft acceptance %s | expert-cache hit rate %s"
                      % (a["name"], arm.get("draft_acceptance"),
                         ("%.1f%%" % arm["cache_hit_rate_pct"]) if hr else "(no cache)"), flush=True)
                arm["gen_best_warm"] = max(arm["gen_tok_s"][1:])
                print("%s: %s N=%s t=%s %s | load %.0fs | VRAM %s GB | repacked %.0f MiB | gen %s | prefill %s"
                      % (a["name"], os.path.basename(a["gguf"])[:28], a.get("n_cpu_moe", "fit%s" % a.get("fit")),
                         a.get("threads", 8),
                         " ".join(a.get("extra", [])), arm["load_s"], arm["vram_used_gb"], arm["cpu_repack_mib"],
                         arm["gen_tok_s"], arm["prefill_tok_s"]), flush=True)
            except Exception as e:                                   # noqa: BLE001
                arm["result"] = "ERROR %s: %s" % (type(e).__name__, e)
                print("%s: %s" % (a["name"], arm["result"]), flush=True)
            finally:
                if proc.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                    proc.wait()
                time.sleep(5)
            res["arms"].append(arm)
            json.dump(res, open(os.path.join(out, "ladder.json"), "w"), indent=1)
    finally:
        if frontier:
            print("frontier restore:", subprocess.run([sys.executable, FRONTIER, "restore"], capture_output=True,
                                                      text=True).stdout[-120:], flush=True)
    json.dump(res, open(os.path.join(out, "ladder.json"), "w"), indent=1)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
