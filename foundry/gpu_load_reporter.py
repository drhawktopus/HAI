"""Reports GPU utilization and power draw to the orchestrator.

Sources:
  - nvidia-smi (NVIDIA boxes)
  - Telemetry CSV (Mothership; must be < STALE_S seconds old)
  - Ollama API (this box's own loopback, on the OLLAMA_PORT setting)

Measurement failures are reported as stale=True. Never report 0 utilization;
a broken reporter must not look like an idle card.

The head's address is the HAI_URL setting (the head's own name for it); a box with no head
configured pushes nothing."""
import csv
import datetime
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

try:
    import lab_settings as S          # the workspace's ONE reader
except ImportError:                  # noqa: BLE001 - this file also runs as the deployed copy in C:\ProgramData\HAI
    S = None


def _head():
    return S.head_url() if S else os.environ.get("HAI_URL", "").rstrip("/")


def _ollama_port():
    """This box's own ollama port; same setting name the other tools use."""
    return S.setting("OLLAMA_PORT", "11434") if S else os.environ.get("OLLAMA_PORT", "11434")


ORCH = _head() + "/fleet/gpu-load" if _head() else ""
HOST = (sys.argv[1] if len(sys.argv) > 1 else socket.gethostname()).strip().lower()
# this box's foundry root is a setting in the lab.env beside this script (FOUNDRY_ROOT); empty = no foundry here
FOUNDRY_ROOT = (S.setting("FOUNDRY_ROOT", "") if S else os.environ.get("FOUNDRY_ROOT", "")) or ""
TELEMETRY_CSV = os.path.join(FOUNDRY_ROOT, "var", "state", "telemetry", "gpu_telemetry.csv") if FOUNDRY_ROOT else ""
STALE_S = 120


TELEMETRY_ROWS = 5                      # the logger writes a row every ~11 s: the last minute, like the NVIDIA window
NVIDIA_SAMPLES, NVIDIA_GAP_S = 5, 1.0   # a working card reads 0% at instants between requests; the peak of a short window answers "is it busy?"


def nvidia():
    """Peak utilization and power over NVIDIA_SAMPLES readings NVIDIA_GAP_S apart; memory from the last reading."""
    reads = []
    for i in range(NVIDIA_SAMPLES):
        if i:
            time.sleep(NVIDIA_GAP_S)
        one = _nvidia_once()
        if one is None:
            return None
        if one.get("stale"):
            continue
        reads.append(one)
    if not reads:
        return one
    return {"util_pct": max(r["util_pct"] for r in reads), "power_w": max(r["power_w"] for r in reads),
            "vram_used_mb": reads[-1]["vram_used_mb"],
            "source": "nvidia-smi peak of %d over %.0f s" % (len(reads), (NVIDIA_SAMPLES - 1) * NVIDIA_GAP_S)}


def _nvidia_once():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,power.draw,memory.used",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return {"stale": True, "source": "nvidia-smi timed out"}
    if r.returncode != 0 or not r.stdout.strip():
        return {"stale": True, "source": "nvidia-smi rc=%d %s" % (r.returncode, (r.stdout + r.stderr).strip()[:120])}
    try:
        util, power, mem = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        return {"util_pct": float(util), "power_w": float(power), "vram_used_mb": float(mem), "source": "nvidia-smi"}
    except Exception as e:
        return {"stale": True, "source": "nvidia-smi unparseable: %r" % (e,)}


def telemetry_csv():
    if not TELEMETRY_CSV or not os.path.exists(TELEMETRY_CSV):
        return None
    try:
        age = datetime.datetime.now().timestamp() - os.path.getmtime(TELEMETRY_CSV)
        if age > STALE_S:
            return {"stale": True, "source": "telemetry csv %.0fs old" % age}
        with open(TELEMETRY_CSV, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", "replace")
        with open(TELEMETRY_CSV, encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        lines = [l for l in tail.strip().splitlines() if l and not l.startswith("ts,")][-TELEMETRY_ROWS:]
        rows = [dict(zip(header, r)) for r in csv.reader(lines)]
        return {"util_pct": max(float(r.get("gpu_load") or 0) for r in rows),
                "power_w": max(float(r.get("power_w") or 0) for r in rows),
                "vram_used_mb": float(rows[-1].get("vram_used_mb") or 0),
                "source": "telemetry csv peak of %d rows to %s" % (len(rows), rows[-1].get("ts", ""))}
    except Exception as e:
        return {"stale": True, "source": "telemetry csv unreadable: %r" % (e,)}


def ollama_loaded():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/api/ps" % _ollama_port(), timeout=5) as r:
            return [m.get("name") for m in (json.loads(r.read().decode("utf-8")).get("models") or []) if m.get("name")]
    except Exception:
        return []


def memory_snapshot():
    """Reuse the foundry's ONE GPU allocation reader, never infer free VRAM from an empty /api/ps."""
    # read at call time: the environment wins over the file, as the reader's rule says
    root = ((S.setting("FOUNDRY_ROOT", "") if S else os.environ.get("FOUNDRY_ROOT", "")) or FOUNDRY_ROOT)
    if not root:
        return None
    try:
        bindir = os.path.join(root, "bin")
        if bindir not in sys.path:
            sys.path.insert(0, bindir)              # the reader's existing board-health/path helpers live beside it
        path = os.path.join(bindir, "gpu_spill.py")
        spec = importlib.util.spec_from_file_location("reporter_gpu_spill", path)
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        return reader.snapshot()
    except Exception:
        return None


def ram():
    """Returns a dict with \\"free_mb\\" and \\"total_mb\\" from Windows' GlobalMemoryStatusEx.

    \\"free_mb\\" corresponds to available physical memory (equivalent to Glances' `mem.free`).
    Returns None if memory status cannot be read; never returns estimated or default values.
    Used for local system checks when no external monitoring agent (like Glances) is running."""
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MemoryStatusEx()
        st.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) or not st.ullTotalPhys:
            return None
        return {"free_mb": round(st.ullAvailPhys / 1048576.0), "total_mb": round(st.ullTotalPhys / 1048576.0)}
    except Exception:
        return None


def ollama_version():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/api/version" % _ollama_port(), timeout=5) as r:
            return json.loads(r.read().decode("utf-8")).get("version")
    except Exception:
        return None


def main():
    sample = nvidia() or telemetry_csv() or {"stale": True, "source": "no nvidia-smi and no telemetry csv"}
    body = {"host": HOST, "ollama_loaded": ollama_loaded(), "gpu_memory": memory_snapshot(),
            "ollama_version": ollama_version(), "ram": ram(), **sample}
    data = json.dumps(body).encode("utf-8")
    try:
        rq = urllib.request.Request(ORCH, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=10) as resp:
            print("reported %s -> %s" % (json.dumps(body)[:160], resp.read().decode()[:160]))
    except Exception as e:
        print("report FAILED (%r) - local sample was %s" % (e, json.dumps(body)[:160]))


if __name__ == "__main__":
    main()
