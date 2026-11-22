"""Tolkien telemetry logger.

Writes continuous history to SQLite (indexed on run/node/ts) to answer
concurrency questions about llama.cpp pipeline stages.

Key invariants:
- Cadence: 1 s for rigby, 2 s per tablet (stages take seconds per chunk).
- Run column is STATED from pointers, never inferred.
- Liveness is a fresh heartbeat FILE.
- NULL cell = could not read (never zero).
- Tablet CPU is read from per-process /proc/<pid>/stat via adb; /proc/stat
  is unreliable on these devices and Termux cannot see setsid'd processes.

CLI:
  python tolkien_telemetry.py summary --run <name> [--since/--until epoch s]
  python tolkien_telemetry.py tail --node <node> --last <s>"""
import argparse
import datetime
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                        # noqa: BLE001
        pass

import tablet_foundry as tf

OUT_DIR = os.environ.get("TOLKIEN_TELEMETRY_DIR") or os.path.join(tf.STATE_DIR, "telemetry")
DB = os.path.join(OUT_DIR, "telemetry.sqlite")
HEARTBEAT = os.path.join(OUT_DIR, "telemetry.heartbeat")
CURRENT_FILES = (("fleet", tf.CURRENT), ("rigby", tf.CURRENT_RIGBY))
PROC = os.environ.get("TOLKIEN_TELEMETRY_PROC", "/proc")
HOSTLIB = os.environ.get("TOLKIEN_HOSTLIB", "/hostlib")
NVIDIA_SMI = os.environ.get("TOLKIEN_NVIDIA_SMI", "/usr/bin/nvidia-smi")
ADB = os.environ.get("TOLKIEN_ADB", "adb")
RETAIN_DAYS = float(os.environ.get("TOLKIEN_TELEMETRY_RETAIN_DAYS", "7"))
RIGBY_S = 1.0
TABLET_S = 2.0
TABLET_IDLE_S = 10.0                # no stated run: the battery, memory and reachability readings stay fresh (< 120 s)
GPU_EVERY = 5                       # nvidia-smi is ~0.2 s per call: every 5th rigby tick
BUSY_PCT = 200.0    # CPU > 100% is normal (multi-core); < 60% indicates loading/idle
BUCKET_S = 2.0      # "at the same moment" = within one tablet sample period
WATCH_PROCS = ("ggml-rpc-server", "rpc-server", "llama-bench", "llama-server")
COLUMNS = ["ts", "run", "node", "proc", "pid", "proc_cpu_pct", "cpu_total_pct", "busiest_core_pct", "cores",
           "mem_avail_mb", "temp_c", "battery_pct", "charging", "link", "link_rx_mb_s", "link_tx_mb_s",
           "gpu_mem_mb", "gpu_core_mhz", "gpu_mem_mhz", "gpu_power_w", "gpu_temp_c", "note"]


# --- parsers (pure, tested) ----------------------------------------------------------------------------------------

def stat_ticks(text):
    """utime + stime from a /proc/<pid>/stat line; the name may contain ')' so split on the LAST one."""
    fields = text.rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])


def cpu_lines(text):
    """{"cpu": (busy, total), "cpu0": ...} from /proc/stat."""
    out = {}
    for ln in text.splitlines():
        if ln.startswith("cpu"):
            p = ln.split()
            v = [int(x) for x in p[1:]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            out[p[0]] = (sum(v) - idle, sum(v))
    return out


def net_bytes(text, pattern):
    """{iface: (rx, tx)} from /proc/net/dev for interfaces matching `pattern`."""
    out = {}
    for ln in text.splitlines()[2:]:
        if ":" not in ln:
            continue
        name, rest = ln.split(":", 1)
        name = name.strip()
        if re.match(pattern, name):
            f = rest.split()
            out[name] = (int(f[0]), int(f[8]))
    return out


def mem_avail_mb(text):
    m = re.search(r"^MemAvailable:\s+(\d+)\s+kB", text, re.M)
    return round(int(m.group(1)) / 1024.0, 1) if m else None


def smi_values(text):
    """nvidia-smi csv,noheader,nounits -> [floats or None]: '[N/A]' (Kepler reports no utilization) is None."""
    out = []
    for v in (text or "").strip().split(","):
        v = v.strip()
        try:
            out.append(float(v))
        except ValueError:
            out.append(None)
    return out


TABLET_CMD = ("cat /proc/uptime; "
              "for p in $(pidof %s 2>/dev/null); do echo P $p $(cat /proc/$p/comm) $(cat /proc/$p/stat); done; "
              "grep MemAvailable /proc/meminfo; "
              "echo T $(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null); "
              "echo B $(cat /sys/class/power_supply/battery/capacity 2>/dev/null) "
              "$(cat /sys/class/power_supply/battery/status 2>/dev/null); "
              "cat /proc/net/dev") % " ".join(WATCH_PROCS)


def parse_tablet(text):
    """One tablet sample: uptime_s, procs {pid: (name, ticks)}, mem_avail_mb, temp_c, net {iface: (rx, tx)}."""
    lines = (text or "").splitlines()
    up = None
    try:
        up = float(lines[0].split()[0])
    except (IndexError, ValueError):
        pass
    procs = {}
    for ln in lines:
        if ln.startswith("P "):
            parts = ln.split(" ", 3)
            try:
                procs[int(parts[1])] = (parts[2], stat_ticks(parts[3]))
            except (IndexError, ValueError):
                pass
    t = None
    m = re.search(r"^T (\d+)", text or "", re.M)
    if m:
        t = int(m.group(1)) / (1000.0 if int(m.group(1)) > 1000 else 1.0)
    start = next((i for i, ln in enumerate(lines) if ln.startswith("Inter-")), None)
    net = net_bytes("\n".join(lines[start:]), r"(rndis\d|usb\d|ncm\d|wlan0)") if start is not None else {}
    battery, charging = battery_status(text or "")
    return {"uptime": up, "procs": procs, "mem": mem_avail_mb(text or ""), "temp": t, "net": net,
            "battery": battery, "charging": charging}


def battery_status(text):
    """(percent, status) from the `B <capacity> <status>` line; (None, None) when the node has no battery file (an
    absent value is 'could not tell', never 0 %). Status keeps the kernel's words: Charging, Discharging, Full,
    'Not charging'."""
    m = re.search(r"^B[ \t]+(\d+)[ \t]*(.*)$", text or "", re.M)
    if not m:
        return None, None
    pct = int(m.group(1))
    return (pct if 0 <= pct <= 100 else None), (m.group(2).strip() or None)


def stated_run():
    """The run(s) Tolkien says it is working, from its own pointer files; '' when idle."""
    names = []
    for lane, path in CURRENT_FILES:
        try:
            with open(path, encoding="utf-8") as f:
                claim = json.load(f)
            for r in tf.claim_orders(claim):             # the fleet lane runs several orders at once
                names.append("%s:%s" % (lane, r["name"]))
        except (OSError, ValueError):
            pass
    return "|".join(names)


# --- store --------------------------------------------------------------------------------------------------------

def _col_type(c):
    return ("REAL" if c in ("ts",) or c.endswith(("_pct", "_mb", "_c", "_s", "_mhz", "_w")) else
            "INTEGER" if c in ("pid", "cores") else "TEXT")


SCHEMA = "CREATE TABLE IF NOT EXISTS samples (%s)" % ", ".join("%s %s" % (c, _col_type(c)) for c in COLUMNS)
INDEXES = ("CREATE INDEX IF NOT EXISTS s_run_ts ON samples(run, ts)",
           "CREATE INDEX IF NOT EXISTS s_node_ts ON samples(node, ts)",
           "CREATE INDEX IF NOT EXISTS s_ts ON samples(ts)")


def connect(path=None):
    path = path or DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")       # the CLI reads while the logger writes
    db.execute(SCHEMA)
    have = {row[1] for row in db.execute("PRAGMA table_info(samples)")}
    for c in COLUMNS:                           # a store made before a column existed gets it added, never rebuilt
        if c not in have:
            db.execute("ALTER TABLE samples ADD COLUMN %s %s" % (c, _col_type(c)))
    for ix in INDEXES:
        db.execute(ix)
    return db


def drain_batch(q, first, clock=time.time, wait=1.0, cap=2000):
    """Returns [first, ...more rows] collected within `wait` seconds of the batch start (not the last row), up to `cap` rows. Using a fixed start time ensures the batch terminates even under a steady stream of data."""
    rows, t0 = [first], clock()
    while len(rows) < cap:
        left = wait - (clock() - t0)
        if left <= 0:
            break
        try:
            rows.append(q.get(timeout=left))
        except queue.Empty:
            break
    return rows


class Writer(threading.Thread):
    """The one writer: batches rows, commits once a second however fast rows arrive, prunes old rows once an hour."""

    def __init__(self, path=None):
        threading.Thread.__init__(self, daemon=True)
        self.q, self.path = queue.Queue(), path

    def run(self):
        db = connect(self.path)
        last_prune = 0.0
        sql = "INSERT INTO samples (%s) VALUES (%s)" % (", ".join(COLUMNS), ", ".join("?" * len(COLUMNS)))
        while True:
            rows = drain_batch(self.q, self.q.get())
            try:
                db.executemany(sql, [[r.get(c) for c in COLUMNS] for r in rows])
                if time.time() - last_prune > 3600:
                    db.execute("DELETE FROM samples WHERE ts < ?", (time.time() - RETAIN_DAYS * 86400,))
                    last_prune = time.time()
                db.commit()
            except Exception as e:                                           # noqa: BLE001
                print("telemetry: write failed: %s" % e, flush=True)


def _r(x, n=1):
    return None if x is None else round(x, n)


# --- rigby --------------------------------------------------------------------------------------------------------

def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


CARD_NAME = "GT 730"      # the GPU rigby's own rows describe; every other card gets a row of its own (gpu_node)


def gpu_sample():
    """Returns a dict of {GPU name: [mem MiB, core MHz, mem MHz, power W, temp C]} for every card nvidia-smi lists ({} = could not ask).
    Keys are GPU names because index 0 is ambiguous on multi-GPU nodes (e.g. rigby)."""
    argv = [os.path.join(HOSTLIB, "ld-linux-x86-64.so.2"), "--library-path", HOSTLIB, NVIDIA_SMI,
            "--query-gpu=name,memory.used,clocks.sm,clocks.mem,power.draw,temperature.gpu",
            "--format=csv,noheader,nounits"]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout
    except Exception:                                                        # noqa: BLE001
        return {}
    return smi_rows(out)


def smi_rows(text):
    """`name, v1, ..., v5` lines -> {name: [5 floats or None]}."""
    out = {}
    for line in (text or "").strip().splitlines():
        name, _, rest = line.partition(",")
        if name.strip() and rest:
            out[name.strip()] = (smi_values(rest) + [None] * 5)[:5]
    return out


def gpu_node(name):
    """Telemetry node for a card other than the GT 730: 'NVIDIA GeForce GTX 1060 6GB' -> 'gtx10606gb'."""
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("nvidia", "").replace("geforce", ""))


def rigby_loop(writer, stop):
    prev_cpu, prev_net, prev_proc, t_prev = None, None, {}, None
    gpus, tick = {}, 0
    clk = os.sysconf("SC_CLK_TCK")
    while not stop.is_set():
        t0 = time.time()
        run = stated_run()
        try:
            cpu = cpu_lines(_read(os.path.join(PROC, "stat")))
            net = net_bytes(_read(os.path.join(PROC, "net", "dev")), r"(enx|br0|eth|enp)")
            mem = mem_avail_mb(_read(os.path.join(PROC, "meminfo")))
        except OSError as e:
            writer.q.put({"ts": t0, "run": run, "node": "rigby", "note": "proc read failed: %s" % e})
            stop.wait(RIGBY_S)
            continue
        if tick % GPU_EVERY == 0:
            gpus = gpu_sample()
            for name, v in gpus.items():
                if CARD_NAME not in name:
                    writer.q.put({"ts": t0, "run": run, "node": gpu_node(name), "gpu_mem_mb": v[0],
                                  "gpu_core_mhz": v[1], "gpu_mem_mhz": v[2], "gpu_power_w": v[3], "gpu_temp_c": v[4]})
        gpu = next((v for n, v in gpus.items() if CARD_NAME in n), [None] * 5)
        tick += 1
        procs = {}
        for pid in os.listdir(PROC):
            if not pid.isdigit():
                continue
            try:
                name = _read(os.path.join(PROC, pid, "comm")).strip()
                if any(name == w[:15] for w in WATCH_PROCS):
                    procs[int(pid)] = (name, stat_ticks(_read(os.path.join(PROC, pid, "stat"))))
            except (OSError, ValueError, IndexError):
                continue
        if prev_cpu is not None:
            dt = max(1e-6, t0 - t_prev)
            per = {k: 100.0 * (b - prev_cpu[k][0]) / max(1, t - prev_cpu[k][1])
                   for k, (b, t) in cpu.items() if k in prev_cpu}
            cores = [v for k, v in per.items() if k != "cpu"]
            writer.q.put({"ts": t0, "run": run, "node": "rigby", "cpu_total_pct": _r(per.get("cpu")),
                          "busiest_core_pct": _r(max(cores) if cores else None), "cores": len(cores),
                          "mem_avail_mb": mem, "gpu_mem_mb": gpu[0], "gpu_core_mhz": gpu[1], "gpu_mem_mhz": gpu[2],
                          "gpu_power_w": gpu[3], "gpu_temp_c": gpu[4]})
            for pid, (name, ticks) in procs.items():
                if pid in prev_proc:
                    writer.q.put({"ts": t0, "run": run, "node": "rigby", "proc": name, "pid": pid,
                                  "proc_cpu_pct": _r(100.0 * (ticks - prev_proc[pid][1]) / clk / dt)})
            for iface, (rx, tx) in net.items():
                if iface in prev_net and (rx, tx) != prev_net[iface]:
                    writer.q.put({"ts": t0, "run": run, "node": "rigby", "link": iface,
                                  "link_rx_mb_s": _r((rx - prev_net[iface][0]) / 1048576.0 / dt, 2),
                                  "link_tx_mb_s": _r((tx - prev_net[iface][1]) / 1048576.0 / dt, 2)})
        prev_cpu, prev_net, prev_proc, t_prev = cpu, net, procs, t0
        try:
            with open(HEARTBEAT, "w", encoding="utf-8") as f:
                json.dump({"ts": t0, "pid": os.getpid(), "run": run}, f)
        except OSError:
            pass
        stop.wait(max(0.0, RIGBY_S - (time.time() - t0)))


# --- tablets ------------------------------------------------------------------------------------------------------

def tablet_period(run):
    """Seconds between a tablet's samples: fast while Tolkien states a run (the preflight and the watchdog read these),
    slow when idle. An adb shell every 2 s keeps a tablet's CPU awake, and sam and faramir sit on 500 mA ports that
    barely cover their idle draw. Slow enough still beats the preflight's wait (STAGE_MEM_WAIT_S 20 s)."""
    return TABLET_S if run else TABLET_IDLE_S


def tablet_loop(name, ip, writer, stop):
    prev, noted = None, 0.0
    target = "%s:5555" % ip
    while not stop.is_set():
        t0 = time.time()
        try:
            r = subprocess.run([ADB, "-s", target, "shell", TABLET_CMD], capture_output=True, text=True, timeout=10)
            text = r.stdout if r.returncode == 0 else ""
        except Exception:                                                    # noqa: BLE001
            text = ""
        s = parse_tablet(text)
        run = stated_run()
        if s["uptime"] is None:
            if time.time() - noted > 60:
                noted = time.time()
                try:
                    subprocess.run([ADB, "connect", target], capture_output=True, timeout=10)
                except Exception:                                            # noqa: BLE001
                    pass
                writer.q.put({"ts": t0, "run": run, "node": name, "note": "unreachable over adb %s" % target})
            prev = None
            stop.wait(tablet_period(run))
            continue
        if prev is not None and s["uptime"] > prev["uptime"]:
            dt = s["uptime"] - prev["uptime"]
            writer.q.put({"ts": t0, "run": run, "node": name, "mem_avail_mb": s["mem"], "temp_c": _r(s["temp"]),
                          "battery_pct": s["battery"], "charging": s["charging"]})
            for pid, (pname, ticks) in s["procs"].items():
                if pid in prev["procs"]:
                    writer.q.put({"ts": t0, "run": run, "node": name, "proc": pname, "pid": pid,
                                  "proc_cpu_pct": _r(100.0 * (ticks - prev["procs"][pid][1]) / 100.0 / dt)})
            for iface, (rx, tx) in s["net"].items():
                if iface in prev["net"] and (rx, tx) != prev["net"][iface]:
                    writer.q.put({"ts": t0, "run": run, "node": name, "link": iface,
                                  "link_rx_mb_s": _r((rx - prev["net"][iface][0]) / 1048576.0 / dt, 2),
                                  "link_tx_mb_s": _r((tx - prev["net"][iface][1]) / 1048576.0 / dt, 2)})
        prev = s
        stop.wait(max(0.0, tablet_period(run) - (time.time() - t0)))


# --- reading: answers, never raw dumps ----------------------------------------------------------------------------

def _window(run=None, since=None, until=None):
    where, args = [], []
    if run:
        where.append("run LIKE ?")
        args.append("%" + run + "%")
    if since is not None:
        where.append("ts >= ?")
        args.append(since)
    if until is not None:
        where.append("ts <= ?")
        args.append(until)
    return (" WHERE " + " AND ".join(where)) if where else "", args


def overlap(db, run=None, since=None, until=None):
    """(max stages computing at the same moment, buckets seen) - a stage = a llama process at >= BUSY_PCT; the moment
    = a BUCKET_S slot. In-turn stages give 1; overlapping stages give more."""
    w, a = _window(run, since, until)
    q = ("SELECT CAST(ts / ? AS INTEGER) AS b, COUNT(DISTINCT node) FROM samples%s%s proc != '' AND proc_cpu_pct >= ? "
         "GROUP BY b" % (w, " AND" if w else " WHERE"))
    rows = db.execute(q, [BUCKET_S] + a + [BUSY_PCT]).fetchall()
    return (max((n for _b, n in rows), default=0), len(rows))


def summary(db, run=None, since=None, until=None):
    """One line per node for a run or window - the reading path. Never returns raw rows."""
    w, a = _window(run, since, until)
    n, t0, t1 = db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples" + w, a).fetchone()
    if not n:
        return ["no samples%s" % ((" for run ~%s" % run) if run else "")]
    busy, buckets = overlap(db, run, since, until)
    out = ["%d samples %s -> %s | stages busy at the same moment: max %d (over %d busy moments)"
           % (n, time.strftime("%H:%M:%S", time.localtime(t0)), time.strftime("%H:%M:%S", time.localtime(t1)),
              busy, buckets)]
    and_ = " AND" if w else " WHERE"
    q = ("SELECT node, AVG(proc_cpu_pct), MAX(proc_cpu_pct), MIN(mem_avail_mb), MAX(temp_c), AVG(link_rx_mb_s), "
         "MAX(link_rx_mb_s), MAX(link_tx_mb_s), MAX(gpu_mem_mb), MAX(cpu_total_pct) FROM samples%s GROUP BY node "
         "ORDER BY node" % w)
    for node, pa, pm, mm, tm, ra, rm, tx, gm, ct in db.execute(q, a).fetchall():
        f = lambda v, u="": "-" if v is None else ("%.0f%s" % (v, u))
        line = "%-9s llama cpu mean %s max %s | link rx mean %s max %s tx max %s MB/s | mem min %s | temp max %s" % (
            node, f(pa, "%"), f(pm, "%"), "-" if ra is None else "%.1f" % ra, "-" if rm is None else "%.1f" % rm,
            "-" if tx is None else "%.1f" % tx, f(mm, " MB"), f(tm, "C"))
        if node == "rigby":
            line += " | cpu total max %s | card mem max %s" % (f(ct, "%"), f(gm, " MiB"))
        out.append(line)
    notes = db.execute("SELECT node, COUNT(*) FROM samples%s%s note != '' GROUP BY node" % (w, and_), a).fetchall()
    if notes:
        out.append("notes: " + ", ".join("%s x%d" % nn for nn in notes))
    return out


def tail(db, node, last_s=60):
    """The last `last_s` seconds of one node, aggregated per BUCKET_S slot (a minute = ~30 lines at most)."""
    q = ("SELECT CAST(ts / ? AS INTEGER) * ?, MAX(proc_cpu_pct), MAX(link_rx_mb_s), MAX(link_tx_mb_s), "
         "MIN(mem_avail_mb), MAX(cpu_total_pct), MAX(gpu_mem_mb) FROM samples WHERE node = ? AND ts >= ? "
         "GROUP BY 1 ORDER BY 1")
    out = []
    d = lambda v, u="": "-" if v is None else "%s%s" % (v, u)
    for b, pc, rx, tx, mm, ct, gm in db.execute(q, (BUCKET_S, BUCKET_S, node, time.time() - last_s)).fetchall():
        out.append("%s llama %s | rx %s tx %s MB/s | mem %s | cpu %s | card %s" % (
            time.strftime("%H:%M:%S", time.localtime(b)), d(pc, "%"), d(rx), d(tx), d(mm), d(ct, "%"), d(gm)))
    return out or ["no samples for %s in the last %ds" % (node, last_s)]


# Rigby does not push its own load report, so this container runs the shared gpu_load_reporter.py every minute
# to keep the box's load data fresh for chat_window._box_reporting and load accounting.
# The reporter is baked into the image (identical to the GPU boxes' version) and uses the local nvidia-smi.
LOAD_REPORT_S = 60.0
REPORTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_load_reporter.py")
LOAD_GPU = os.environ.get("TOLKIEN_LOAD_GPU", "1060")   # the card the report describes: the 1060 serves, the GT 730 never does


def load_gpu_uuid(listing, name=LOAD_GPU):
    """The UUID of the card whose `nvidia-smi -L` line names `name`, or None. By UUID, not index: the order can change."""
    for line in (listing or "").splitlines():
        if name in line and "UUID:" in line:
            return line.split("UUID:", 1)[1].strip().rstrip(")").strip()
    return None


def nvidia_wrapper(dirpath, uuid=None):
    """An `nvidia-smi` the shared reporter can run here: the host's, under the host loader exactly as gpu_sample runs it (plain
    `nvidia-smi` finds no libnvidia-ml.so in this container), pinned to one card. -> its path."""
    os.makedirs(dirpath, exist_ok=True)
    path = os.path.join(dirpath, "nvidia-smi")
    pick = ' -i "%s"' % uuid if uuid else ""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('#!/bin/sh\nexec "%s" --library-path "%s" "%s"%s "$@"\n'
                % (os.path.join(HOSTLIB, "ld-linux-x86-64.so.2"), HOSTLIB, NVIDIA_SMI, pick))
    os.chmod(path, 0o755)
    return path


def _list_cards():
    argv = [os.path.join(HOSTLIB, "ld-linux-x86-64.so.2"), "--library-path", HOSTLIB, NVIDIA_SMI, "-L"]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout
    except Exception:                                                        # noqa: BLE001 - no listing: the wrapper picks no card
        return ""


def load_report_loop(stop, run=subprocess.run, period=LOAD_REPORT_S, log=print, lister=_list_cards):
    """Push rigby's load report every `period` s until `stop`. A failed push is a log line when it changes, never a raise; a
    push that carried no card sample is said as STALE, never as pushed."""
    env = dict(os.environ, HAI_URL=str(getattr(tf, "HAI_URL", "") or os.environ.get("HAI_URL") or "").rstrip("/"))
    if not env["HAI_URL"]:
        env.pop("HAI_URL")                     # no head: the reporter pushes nothing and says so
    wrap = os.path.join(tempfile.gettempdir(), "hai-load-nvidia")
    nvidia_wrapper(wrap, load_gpu_uuid(lister()))
    env["PATH"] = wrap + os.pathsep + env.get("PATH", "")
    said = None
    while not stop.is_set():
        try:
            r = run([sys.executable, REPORTER, "rigby"], capture_output=True, text=True, timeout=period, env=env)
            out = (r.stdout or "").strip().splitlines()
            word = out[-1][:160] if out else "rc=%s %s" % (r.returncode, (r.stderr or "").strip()[-160:])
        except Exception as e:                                               # noqa: BLE001 - a report never stops telemetry
            word = "load report not run: %r" % (e,)
        stale = word.startswith("reported") and '"util_pct": null' in word
        state = "stale" if stale else "ok" if word.startswith("reported") else word
        if state != said:
            log("telemetry: rigby load report %s" % (
                "pushed every %.0f s" % period if state == "ok" else
                "pushed but STALE, no card sample: %s" % word[:140] if state == "stale" else state))
            said = state
        stop.wait(period)


# --- entry --------------------------------------------------------------------------------------------------------

def run_logger():
    os.makedirs(OUT_DIR, exist_ok=True)
    # positive control: this process can read its own /proc entry, or every rigby number would be blank
    stat_ticks(_read(os.path.join(PROC, str(os.getpid()), "stat")))
    writer = Writer()
    writer.start()
    stop = threading.Event()
    nodes = {h: tf.wire_ip(h) for h in tf.WIRE_OCTET if tf.wire_ip(h)}   # empty when no wire subnet is set
    print("telemetry: rigby every %.0fs, %d tablets every %.0fs -> %s (keep %g days)"
          % (RIGBY_S, len(nodes), TABLET_S, DB, RETAIN_DAYS), flush=True)
    for n, ip in sorted(nodes.items()):
        threading.Thread(target=tablet_loop, args=(n, ip, writer, stop), daemon=True).start()
    threading.Thread(target=load_report_loop, args=(stop,), daemon=True).start()
    rigby_loop(writer, stop)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("summary")
    s.add_argument("--run")
    s.add_argument("--since", type=float)
    s.add_argument("--until", type=float)
    t = sub.add_parser("tail")
    t.add_argument("--node", required=True)
    t.add_argument("--last", type=float, default=60)
    a = ap.parse_args(argv)
    if a.cmd is None:
        tf.install_stop()      # PID 1: exit on a stop instead of making docker wait its 10 s grace (sqlite commits are atomic)
        return run_logger()
    db = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=30)
    lines = summary(db, a.run, a.since, a.until) if a.cmd == "summary" else tail(db, a.node, a.last)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
