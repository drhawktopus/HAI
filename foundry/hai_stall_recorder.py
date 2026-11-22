"""Records a stalled ollama runner on this box: evidence first, then one DM. It never kills, restarts or retries
anything - the factory's hung-engine guard holds the order, and the retry gets a fresh runner.

Stall = a runner (llama-server under ollama) reports a request in progress (`/slots` is_processing) and none of its
progress counters (task id, prompt tokens, prompt tokens processed, tokens decoded) move for STALL_S. A 512-token
prompt batch takes ~0.7 s on the 7900 XTX (Vulkan prefill ~700 tok/s) and a decoded token ~12 ms, so 60 s without a
counter moving is ~85x the slowest unit of progress. Model loads happen before a slot is processing, so they never
count.

Evidence, saved once per (runner pid, task) under OUT\\<stamp>-pid<pid>-task<id>\\: the lead-up ring of slot samples
(last 10 min), three more slot samples during the stall, /props, /health, ollama /api/ps + /api/version, a minidump of
the runner with every thread's stack (Windows' own dbghelp), per-thread wait states (twice), the serve log tail, GPU
telemetry for the last 30 min, the foundry queue log tail + CURRENT_RUN, System/Application events for the last hour,
GPU driver and Vulkan versions, and the ollama launcher. `summary.json` says what was saved and what failed.
Heartbeat: OUT\\heartbeat.json on every poll. Runbook: RUNBOOK-Ollama-Host-Operations "STALL RECORDER".

    python hai_stall_recorder.py            the scheduled task 'HAI Stall Recorder' (SYSTEM)
    python hai_stall_recorder.py --decoy    live end-to-end proof: a fake frozen runner; bundle + DM tagged DECOY
"""
import collections
import ctypes
import datetime
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# The foundry root and its settings, read the way the foundry reads them: foundry_paths beside the foundry's code.
# The recorder lives outside that tree (C:\ProgramData\HAI), so it finds the root first: FOUNDRY_ROOT, with
# foundry_paths' own default.
FOUNDRY_BIN = os.path.join(os.environ.get("FOUNDRY_ROOT", os.path.join("F:", os.sep, "foundry")), "bin")
if FOUNDRY_BIN not in sys.path:
    sys.path.insert(0, FOUNDRY_BIN)
import foundry_paths as P  # noqa: E402

OUT = os.path.join(P.HOLD_DIR, "stall-recorder")
# the model server's log: OLLAMA_SERVE_LOG (a setting: a server run under a user account logs in that profile) and
# the lab launcher's own log
SERVE_LOGS = tuple(p for p in (P.setting("OLLAMA_SERVE_LOG", ""), os.path.join(P.HOLD_DIR, "ollama-serve.log")) if p)
TELEM = os.path.join(P.FOUNDRY_ROOT, "var", "state", "telemetry", "gpu_telemetry.csv")
QUEUE_LOG = os.path.join(P.FOUNDRY_ROOT, "var", "log", "foundry_queue.log")
CURRENT_RUN = os.path.join(P.FOUNDRY_ROOT, "var", "state", "CURRENT_RUN.json")
LAUNCHERS = (os.path.join(P.HOLD_DIR, "start-ollama.ps1"), os.path.join(P.HOLD_DIR, "start-ollama-system.ps1"))
OLLAMA = "http://127.0.0.1:11434"
POLL_S = 2
DISCOVER_S = 20
STALL_S = 60
RING = 300                      # 10 min of polls
LOG = os.path.join(OUT, "recorder.log")


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 5_000_000:
            os.replace(LOG, LOG + "." + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def ps(cmd, timeout=120):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=timeout)
        return r.stdout.decode("utf-8", "replace").strip()
    except Exception as e:                                                       # noqa: BLE001
        return "ERROR %r" % (e,)


def get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def llama_pids():
    """The pids of every llama-server.exe, from a process snapshot taken INSIDE this process (Toolhelp32): no child
    process is started. None when the snapshot cannot be taken (the caller then asks the old way)."""
    try:
        import ctypes
        from ctypes import wintypes

        class Entry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", wintypes.WCHAR * 260)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)                 # TH32CS_SNAPPROCESS
        if snap in (None, wintypes.HANDLE(-1).value):
            return None
        try:
            e, pids = Entry(), set()
            e.dwSize = ctypes.sizeof(Entry)
            ok = k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szExeFile.lower() == "llama-server.exe":
                    pids.add(int(e.th32ProcessID))
                ok = k32.Process32NextW(snap, ctypes.byref(e))
            return frozenset(pids)
        finally:
            k32.CloseHandle(snap)
    except Exception:                                                            # noqa: BLE001 - never breaks the recorder
        return None


_RUNNERS = {"pids": None, "ports": None}


def runners(pids_now=llama_pids):
    """{pid: port} for every llama-server on the box, cached by pid set. Only refresh command lines when the set of pids changes to avoid spawning PowerShell per poll."""
    pids = pids_now()
    if pids is not None and pids == _RUNNERS["pids"] and _RUNNERS["ports"] is not None:
        return dict(_RUNNERS["ports"])
    raw = ps("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
             "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress", timeout=60)
    try:
        rows = json.loads(raw) if raw and not raw.startswith("ERROR") else []
    except ValueError:
        _RUNNERS["pids"] = None                       # could not tell: ask again next poll, keep nothing
        return None
    rows = [rows] if isinstance(rows, dict) else rows
    out = {}
    for r in rows:
        m = re.search(r"--port\s+(\d+)", r.get("CommandLine") or "")
        if m:
            out[int(r["ProcessId"])] = int(m.group(1))
    # kept only when the snapshot and the query agree on which servers exist (one may have started between the two)
    if pids is not None and not raw.startswith("ERROR") and pids == frozenset(int(r["ProcessId"]) for r in rows):
        _RUNNERS["pids"], _RUNNERS["ports"] = pids, dict(out)
    else:
        _RUNNERS["pids"] = None
    return out


def progress_key(slot):
    nt = (slot.get("next_token") or [{}])[0]
    return (slot.get("id_task"), slot.get("n_prompt_tokens"), slot.get("n_prompt_tokens_processed"),
            nt.get("n_decoded"))


class Watch:
    """Per (pid, slot): when its progress key last changed. Pure logic, so it is tested without a box."""

    def __init__(self, stall_s=STALL_S):
        self.stall_s = stall_s
        self.seen = {}               # (pid, slot_id) -> (key, since)
        self.captured = set()        # (pid, id_task)

    def update(self, pid, slots, now):
        """-> [(slot, frozen_for)] that just crossed the stall line and were not captured yet."""
        fire = []
        for s in slots:
            k = (pid, s.get("id"))
            if not s.get("is_processing"):
                self.seen.pop(k, None)
                continue
            key = progress_key(s)
            prev = self.seen.get(k)
            if prev is None or prev[0] != key:
                self.seen[k] = (key, now)
                continue
            frozen = now - prev[1]
            if frozen >= self.stall_s and (pid, s.get("id_task")) not in self.captured:
                self.captured.add((pid, s.get("id_task")))
                fire.append((s, frozen))
        return fire

    def forget(self, live_pids):
        self.seen = {k: v for k, v in self.seen.items() if k[0] in live_pids}


def tail_bytes(path, n):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - n))
        return f.read()


def card_state(window_s=120):
    """'idle' / 'busy' / 'unknown' from the GPU telemetry rows of the last `window_s` (never guessed)."""
    try:
        data = tail_bytes(TELEM, 200_000).decode("utf-8", "replace").splitlines()
        with open(TELEM, encoding="utf-8", errors="replace") as f:
            head = f.readline().strip().split(",")
        now = datetime.datetime.now()
        rows = []
        for ln in data[1:]:
            v = ln.split(",")
            if len(v) != len(head):
                continue
            r = dict(zip(head, v))
            try:
                ts = datetime.datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if (now - ts).total_seconds() <= window_s:
                rows.append(r)
        if not rows:
            return "unknown", "no telemetry row in the last %ds" % window_s
        load = [float(r["gpu_load"] or 0) for r in rows]
        power = [float(r["power_w"] or 0) for r in rows]
        state = "idle" if max(load) == 0 and max(power) < 30 else "busy"
        return state, "%d rows: load max %.0f%%, power %.0f-%.0f W" % (len(rows), max(load), min(power), max(power))
    except Exception as e:                                                       # noqa: BLE001
        return "unknown", "telemetry unreadable (%r)" % (e,)


MINIDUMP_TYPE = 0x1 | 0x4 | 0x20 | 0x40 | 0x100 | 0x800 | 0x1000   # data segs, handles, unloaded modules,
#                                       indirect memory, process thread data, full memory info, thread info


def minidump(pid, path):
    k32, dbg = ctypes.windll.kernel32, ctypes.windll.dbghelp
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x0400 | 0x0010 | 0x0040, False, pid)
    if not h:
        return "OpenProcess failed (error %d)" % k32.GetLastError()
    try:
        import msvcrt
        with open(path, "wb") as f:
            fh = msvcrt.get_osfhandle(f.fileno())
            dbg.MiniDumpWriteDump.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            ok = dbg.MiniDumpWriteDump(h, pid, fh, MINIDUMP_TYPE, None, None, None)
        return "ok (%d bytes)" % os.path.getsize(path) if ok else "MiniDumpWriteDump failed (error %d)" % k32.GetLastError()
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def save(d, name, content):
    p = os.path.join(d, name)
    try:
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=1, ensure_ascii=False, default=str)
        with open(p, "wb") as f:
            f.write(content if isinstance(content, bytes) else str(content).encode("utf-8", "replace"))
    except Exception as e:                                                       # noqa: BLE001
        with open(p + ".FAILED.txt", "w", encoding="utf-8") as f:
            f.write(repr(e))


def capture(pid, port, slot, frozen, ring, tag=""):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = os.path.join(OUT, "%s-pid%d-task%s%s" % (stamp, pid, slot.get("id_task"), tag))
    os.makedirs(d, exist_ok=True)
    notes = {"pid": pid, "port": port, "slot": slot.get("id"), "task": slot.get("id_task"),
             "frozen_s": round(frozen, 1), "progress_key": progress_key(slot), "tag": tag.strip("-"),
             "started": stamp}
    base = "http://127.0.0.1:%d" % port
    still = []
    for i in range(3):
        try:
            s = get(base + "/slots")
            still.append({"t": time.time(), "slots": s})
        except Exception as e:                                                   # noqa: BLE001
            still.append({"t": time.time(), "error": repr(e)})
        if i < 2:
            time.sleep(5)
    save(d, "slots_during_stall.json", still)
    save(d, "slots_leadup_ring.jsonl", "\n".join(json.dumps(x, default=str) for x in ring) + "\n")
    q = ("Get-Process -Id %d | ForEach-Object { $_.Threads } | Select-Object Id,ThreadState,WaitReason,"
         "@{n='cpu_s';e={$_.TotalProcessorTime.TotalSeconds}},@{n='start';e={$_.StartTime}} | ConvertTo-Json -Compress" % pid)
    save(d, "threads_1.json", ps(q))
    notes["minidump"] = minidump(pid, os.path.join(d, "llama-server-%d.dmp" % pid))
    save(d, "threads_2.json", ps(q))
    for name, url in (("props.json", base + "/props"), ("health.json", base + "/health"),
                      ("ollama_ps.json", OLLAMA + "/api/ps"), ("ollama_version.json", OLLAMA + "/api/version")):
        try:
            save(d, name, get(url))
        except Exception as e:                                                   # noqa: BLE001
            save(d, name + ".FAILED.txt", repr(e))
    save(d, "processes.json", ps("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe' OR Name='ollama.exe'\" | "
                                 "Select-Object Name,ProcessId,ParentProcessId,CreationDate,HandleCount,ThreadCount,"
                                 "WorkingSetSize,KernelModeTime,UserModeTime,CommandLine | ConvertTo-Json"))
    for p in SERVE_LOGS:
        if os.path.exists(p):
            save(d, "serve.log.tail", tail_bytes(p, 12_000_000))
            notes["serve_log"] = p
    for p, n, name in ((TELEM, 3_000_000, "gpu_telemetry.tail.csv"), (QUEUE_LOG, 300_000, "foundry_queue.log.tail")):
        try:
            data = tail_bytes(p, n)
            if name.endswith(".csv"):
                with open(p, "rb") as f:
                    data = f.readline() + data.split(b"\n", 1)[-1]
            save(d, name, data)
        except OSError as e:
            save(d, name + ".FAILED.txt", repr(e))
    for p in (CURRENT_RUN,) + LAUNCHERS:
        if os.path.exists(p):
            shutil.copy(p, os.path.join(d, os.path.basename(p)))
    save(d, "events.json", ps("Get-WinEvent -FilterHashtable @{LogName='System','Application'; StartTime=(Get-Date).AddHours(-1)} "
                              "-ErrorAction SilentlyContinue | Where-Object { $_.Level -le 3 -or $_.ProviderName -match "
                              "'amd|Display|dxg|LiveKernel|WHEA|Windows Error Reporting' } | Select-Object TimeCreated,"
                              "LogName,ProviderName,Id,LevelDisplayName,Message | ConvertTo-Json", timeout=180))
    save(d, "gpu_driver.json", ps("Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate | ConvertTo-Json"))
    save(d, "vulkaninfo_summary.txt", ps("& vulkaninfo --summary 2>&1 | Out-String", timeout=120))
    notes["card"] = card_state()
    notes["files"] = {f: os.path.getsize(os.path.join(d, f)) for f in sorted(os.listdir(d))}
    notes["finished"] = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    save(d, "summary.json", notes)
    return d, notes


def dm(text):
    try:
        sys.path.insert(0, FOUNDRY_BIN)
        import p20_foundry as F
        token, user_id = F._dm_creds()
        if not (token and user_id):
            log("DM skipped - no relay credentials")
            return
        body = json.dumps({"user_id": user_id, "message": "%s [stall-recorder] %s" % (F._host_tag(), text)}).encode()
        rq = urllib.request.Request(F.DM_URL, data=body, headers={"Content-Type": "application/json",
                                                                  "X-DM-Token": token, "Host": F.DM_HOST})
        urllib.request.urlopen(rq, timeout=10).read()
        log("DM sent")
    except Exception as e:                                                       # noqa: BLE001
        log("WARNING: DM failed (%r)" % (e,))


def heartbeat(state):
    try:
        tmp = os.path.join(OUT, "heartbeat.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(state, ts=datetime.datetime.now().isoformat(timespec="seconds"), pid=os.getpid(),
                           poll_s=POLL_S), f)
        os.replace(tmp, os.path.join(OUT, "heartbeat.json"))
    except OSError:
        pass


def report(d, notes):
    nt = notes["progress_key"]
    card, why = notes.get("card", ("unknown", ""))
    dm("ollama runner STALLED%s: pid %s task %s, no progress for %ds (prompt %s/%s, decoded %s); card %s (%s). "
       "Evidence saved: %s (minidump %s, %d files). Nothing was restarted or killed."
       % (" [DECOY - test]" if notes.get("tag") else "", notes["pid"], notes["task"], notes["frozen_s"], nt[2], nt[1],
          nt[3], card, why, d, notes["minidump"], len(notes["files"])))


def run(stall_s=STALL_S, fixed=None, tag="", max_s=None):
    """`fixed` = {pid: port} for the decoy; otherwise runners are rediscovered every DISCOVER_S."""
    os.makedirs(OUT, exist_ok=True)
    w = Watch(stall_s)
    ring = collections.deque(maxlen=RING)
    known, last_disc, t0, captures = {}, 0.0, time.time(), 0
    log("recorder up: stall after %ds without progress, poll %ds%s" % (stall_s, POLL_S, " " + tag if tag else ""))
    while max_s is None or time.time() - t0 < max_s:
        try:
            now = time.time()
            if fixed is not None:
                known = fixed
            elif now - last_disc >= DISCOVER_S or not known:
                found = runners()
                if found is not None:
                    known = found
                    w.forget(set(known))
                last_disc = now
            for pid, port in list(known.items()):
                try:
                    slots = get("http://127.0.0.1:%d/slots" % port, timeout=5)
                except Exception as e:                                           # noqa: BLE001
                    ring.append({"t": now, "pid": pid, "error": repr(e)[:160]})
                    last_disc = 0.0
                    continue
                ring.append({"t": now, "pid": pid, "slots": [{"id": s.get("id"), "p": s.get("is_processing"),
                                                              "k": progress_key(s)} for s in slots]})
                for s, frozen in w.update(pid, slots, now):
                    log("STALL pid %d task %s: no progress for %.0fs - capturing" % (pid, s.get("id_task"), frozen))
                    d, notes = capture(pid, port, s, frozen, list(ring), tag)
                    log("captured -> %s (minidump %s)" % (d, notes["minidump"]))
                    report(d, notes)
                    captures += 1
            heartbeat({"runners": known, "captures": captures, "tracking": len(w.seen)})
        except Exception as e:                                                   # noqa: BLE001
            log("poll error (%r)" % (e,))
        time.sleep(POLL_S)
    return captures


class _Frozen(http.server.BaseHTTPRequestHandler):
    SLOT = [{"id": 0, "is_processing": True, "id_task": 999001, "n_prompt_tokens": 212,
             "n_prompt_tokens_processed": 212, "next_token": [{"n_decoded": 236}]}]

    def do_GET(self):
        body = json.dumps(self.SLOT if self.path == "/slots" else {"status": "decoy"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def decoy():
    """A fake frozen runner served on a free port, a real sleeping child process to dump, the real capture + DM."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Frozen)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    try:
        n = run(stall_s=10, fixed={child.pid: srv.server_address[1]}, tag="-DECOY", max_s=40)
    finally:
        child.kill()
        srv.shutdown()
    print("DECOY RESULT: %d capture(s)" % n)
    return 0 if n == 1 else 1


if __name__ == "__main__":
    sys.exit(decoy() if "--decoy" in sys.argv else run())
