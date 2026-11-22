"""Deploy foundry modules to every factory and verify they are running.

Constraints:
- Restart runners and verify PIDs post-date file mtime; imports occur once at process start.
- Hash remote mirrors before push to prevent silent reverts.
- Gate restarts on remote lock status; unreadable locks are treated as held (fail-closed).
- Do not wait for orders in flight; pause, redeploy, and requeue the archived generation.
  Never requeue an order that was explicitly stopped; defer if stop is not honored within WAIT_STOP_S.
- Timestamp backups for one-artifact-per-deploy traceability.
- Validate payloads via ast.parse on the target interpreter.
- Use base64 over stdin to avoid shell quoting and encoding errors.

Note: Does NOT handle containerized orchestrators (scheduler.py/app.py). Do not extend to cover them.

Usage:
    python fleet_deploy.py --check
    python fleet_deploy.py <module.py> [--no-restart]
    python fleet_deploy.py --only <box> <module.py>"""
import argparse
import base64
import datetime
import hashlib
import io
import json
import os
import subprocess
import sys

import foundry_paths as _P      # the workspace's ONE settings reader

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MIRROR = os.path.dirname(os.path.abspath(__file__))

# `root` is the box's FOUNDRY_ROOT; `task` is the scheduled task that owns the runner; `worker_task` the one
# that starts the render worker. Names differ per box. `local` marks the box this tool runs on;
# SSH to self is a network logon and cannot see the interactive session's processes, drives, or
# Credential Manager, so it would report false status for local checks.
def _fleet():
    """The fleet table is the FLEET_TABLE setting (etc\\lab.env beside the foundry root this tool reads):
    host -> {"root", "task", "worker_task"}. `local` is the box this tool runs on, by its own name. Empty = no fleet."""
    me = os.environ.get("COMPUTERNAME", "").lower()
    return [{"host": host, "root": cfg["root"], "task": cfg["task"], "worker_task": cfg.get("worker_task"),
             "local": host.lower() == me}
            for host, cfg in (_P.setting_json("FLEET_TABLE", {}) or {}).items()]


FLEET = _fleet()

# Processes that hold an imported copy of a module until they are restarted.
HOLDERS = ("--queue", "p20_foundry.py", "run_queue", "runner.cmd", "run_foundry_queue")
# Modules running from paths outside the foundry tree: the FLEET_ELSEWHERE setting, {name: {host: folder}}. A copy of
# one in `bin` is a decoy (not live) and is never diffed against; empty = every module lives in <root>\bin.
# The restart rule per out-of-tree module (the lab's table; the setting carries the folders):
#   p20_stall_rescue.py  the ai-toolkit tree; managed by STOP_RESCUE, never restarted by this tool
#   comfy_launch.bat     the drive root; spawned fresh for each render, nothing imports it
#   ollama-watchdog.ps1, frontier_mode.py, hai_stall_recorder.py, ups_power_logger.py   %ProgramData%\HAI; each is run
#     by a SYSTEM task ("HAI Ollama Watchdog", "HAI Frontier Restore", "HAI Stall Recorder", "UPS Power Logger"): end
#     and run that task after a deploy; the runner never imports them
#   gpu_reporter.py, gpu_load_reporter.py, lab_settings.py   %ProgramData%\HAI on every factory; the installed
#     reporters import lab_settings and read the lab.env beside it; their tasks run them fresh each minute
#   install_stall_recorder_task.ps1, install_ups_logger_task.ps1   %ProgramData%\HAI on Mothership; run by hand to
#     (re)register their task, nothing runs them on a schedule


def _elsewhere():
    """{module: {host: folder}} from the FLEET_ELSEWHERE setting; unset or unparseable = nothing runs out of tree."""
    table = _P.setting_json("FLEET_ELSEWHERE", {}) or {}
    return {n: dict(h) for n, h in table.items() if isinstance(h, dict)}


ELSEWHERE = _elsewhere()
# the lab tools' mirror (LAB_TOOLS_DIR); a checkout keeps them beside the foundry as `tools`
LAB_TOOLS = _P.setting("LAB_TOOLS_DIR", "") or os.path.normpath(os.path.join(MIRROR, "..", "tools"))
# A module whose mirror copy is not at the mirror root (its documented home): name -> mirror subfolder.
MIRROR_SUBDIR = {"frontier_mode.py": "hostscripts", "ups_power_logger.py": "hostscripts",
                 "install_ups_logger_task.ps1": "hostscripts", "gpu_reporter.py": LAB_TOOLS, "gpu_load_reporter.py": LAB_TOOLS,
                 "lab_settings.py": LAB_TOOLS, "gpu_telemetry_logger_nvidia.ps1": LAB_TOOLS,
                 "install_gpu_telemetry_logger.ps1": LAB_TOOLS,
                 "start-ollama.ps1": "hostscripts", "start-ollama-system.ps1": "hostscripts",
                 # Mothership's host scripts (one name, one source: BigHonker's own keep-awake.ps1 is not carried here)
                 "keep-awake.ps1": "hostscripts", "textinput-watchdog.ps1": "hostscripts", "gpu-free.ps1": "hostscripts",
                 "get_flash_next_ollama.py": "hostscripts", "get-flash-next-ollama.cmd": "hostscripts",
                 "finalize-ollama-move.ps1": "hostscripts", "phase5-architecture-review.ps1": "hostscripts",
                 "mothership_checklist.ps1": os.path.join("hostscripts", "operator"),
                 "zt_shot.ps1": os.path.join("hostscripts", "tools"),
                 # BigHonker's, kept in the Lab repository beside the tools
                 "bh_upgrade.ps1": os.path.join(os.path.dirname(LAB_TOOLS), "hosts", "bighonker"),
                 "install_bh_power_limit.ps1": os.path.join(os.path.dirname(LAB_TOOLS), "hosts", "bighonker")}
# Each factory's own launchers (what its scheduled tasks start), kept in the Lab repository as hosts\<box>\launchers\*,
# found here by listing that tree; FLEET_ELSEWHERE names the folder each one runs from on its box, so each goes to that
# box only.
def launcher_homes(hosts_dir):
    """{launcher file name: its folder in the Lab repository} for every hosts\\<box>\\launchers\\ file."""
    out = {}
    for box in sorted(os.listdir(hosts_dir)) if os.path.isdir(hosts_dir) else []:
        d = os.path.join(hosts_dir, box, "launchers")
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            out[n] = d
    return out


MIRROR_SUBDIR.update(launcher_homes(os.path.join(os.path.dirname(LAB_TOOLS), "hosts")))

# A1 payload (`--a1`): spawned fresh from A1_SRC for every order, so updates ship without restarting the runner.
# Map: source filename in A1_SRC -> destination filename on the target box.
A1_SRC = _P.setting("A1_SRC", "")          # the A1 payload's source folder on this head; empty = no A1 payload here
A1_GROUP = {"a1_order.py": "a1_order.py", "a1_validity.py": "a1_validity.py", "a1_branch.py": "a1_branch.py",
            "a1_stop.py": "a1_stop.py", "a1_delegate.py": "a1_delegate.py", "a1_harness_v22.py": "a1_harness.py",
            "a1_ollama.py": "a1_ollama.py",   # imported by a1_order and a1_delegate; required outside the A1 tree to resolve lab provider imports
            "a1_chat.py": "a1_chat.py", "lab_provider.py": os.path.join("..", "inspect_lab", "lab_provider.py")}


def files_for(host, files):
    """(the files this box takes, their out-of-tree homes) for one box."""
    take = {n: f for n, f in files.items() if n not in ELSEWHERE or host in ELSEWHERE[n]}
    homes = {n: ELSEWHERE[n][host] for n in take if n in ELSEWHERE}
    return take, homes

REMOTE = r'''
import ast, base64, datetime, glob, hashlib, io, json, os, subprocess, sys, time

CFG = json.loads(base64.b64decode("__CFG__").decode("utf-8"))
ROOT, TASK = CFG["root"], CFG["task"]
FILES = CFG["files"]          # {name: {"sha": ..., "b64": ...}}  (b64 absent when checking only)
DO = CFG["deploy"]
RESTART = CFG["restart"]
STAMP = CFG["stamp"]
HOLDERS = tuple(CFG["holders"])

# How long to let the factory honour a pause request before escalating to force. The order taker
# polls every ~30 s and checks slips BEFORE picking up work, so two poll cycles plus slack is
# generous (measured exits are well under 20 s).
PAUSE_WAIT_S = 90
# The foundry's CLAIM_STALE_AFTER (p20_foundry.py): this remote script cannot import the foundry, so the value is
# restated here and pinned equal by test_fleet_deploy.StaleClaimIsNotInFlight.
CLAIM_STALE_AFTER = 1800

rep = {"host": os.environ.get("COMPUTERNAME", "?"), "root": ROOT, "files": {}, "steps": []}


HOMES = {"test_foundry.py": "tests"}   # where the gate runs it (run_foundry_suite.cmd); everything else in bin
TESTS_BESIDE = "test_foundry_ext_"     # the suite's extensions (test_foundry.py loads them from its own folder)


def dest(nm):
    home = (CFG.get("homes") or {}).get(nm)              # a module that runs OUTSIDE the foundry tree (ELSEWHERE)
    if home:
        return os.path.join(home, nm)
    return os.path.join(ROOT, HOMES.get(nm) or ("tests" if nm.startswith(TESTS_BESIDE) else "bin"), nm)


def ps(cmd):
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                              capture_output=True, text=True, timeout=120).stdout
    except Exception as e:
        return ""


def _self_and_ancestors():
    """This process and every parent of it: never kill these.

    The module under deploy is an ARGUMENT on this tool's own command line, so a holder pattern matches the
    observer itself; a process-listing predicate must exclude the observer before it counts or kills anything,
    on the local box as on a remote one (the lab's rule for every CommandLine filter).
    """
    out, seen = set(), set()
    try:
        raw = ps("Get-CimInstance Win32_Process | ForEach-Object { $_.ProcessId.ToString() + '~' "
                 "+ $_.ParentProcessId.ToString() }")
        parent = {}
        for line in raw.splitlines():
            a = line.strip().split("~")
            if len(a) == 2:
                try:
                    parent[int(a[0])] = int(a[1])
                except ValueError:
                    pass
        cur = os.getpid()
        while cur and cur not in seen:
            seen.add(cur)
            out.add(cur)
            cur = parent.get(cur)
    except Exception:
        out.add(os.getpid())
    return out


def procs():
    """Every python/cmd process, with the command line WHEN VISIBLE. Excludes the observer.

    CommandLine reads NULL for a process at a different integrity level than the querying session (a runner
    task and this query can both run as the same account at different elevations). An invisible command line is
    "unknown", never "not a holder": a deploy tool that cries wolf gets muted.
    """
    out = ps("Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python|cmd' } | "
             "ForEach-Object { $_.ProcessId.ToString() + '~' + "
             "$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') + '~' + [string]$_.CommandLine }")
    mine = _self_and_ancestors()
    rows = []
    for line in out.splitlines():
        p = line.strip().split("~", 2)
        if len(p) != 3:
            continue
        if "win32_process" in (p[2] or "").lower():
            continue                                  # the observer's own query
        try:
            pid = int(p[0])
        except ValueError:
            continue
        if pid in mine or "fleet_deploy" in (p[2] or "").lower():
            continue                                  # never the observer or its parents
        rows.append({"pid": pid, "started": p[1], "cmd": (p[2] or "")})
    return rows


def task_running():
    out = ps("schtasks /query /tn \"%s\" /fo LIST" % TASK)
    for line in out.splitlines():
        if line.strip().lower().startswith("status:"):
            return "running" in line.lower()
    return None                                        # could not ask - never "no"


def holders():
    """Processes holding an imported copy. THE RUNNER IS READ FROM ITS OWN LOCK, never inferred.

    Identity-by-command-line fails in BOTH directions on a box that returns a NULL CommandLine for
    its runner (a different integrity level):

        false NEGATIVE - "no runner came back up" about a runner that is demonstrably up. Loud.
        false POSITIVE - a short-lived process whose command line DOES match gets counted as the
                         runner, so the tool kills nothing, restarts nothing, and reports
                         "OK - deployed and running new code". Silent, and the one that matters:
                         the box then works whole orders on stale code and ships under old names.

    A deploy tool that reports success without checking the actual process is worse than no tool -
    it converts "I forgot to restart" into "I verified the restart", which is the failure this
    entire file exists to prevent.

    The runner already STATES its pid in `runner.lock` - that is the singleton it uses to refuse a
    second instance. Read the stated value; never infer it from a process listing. Exactly the
    CURRENT_RUN rule, applied to a pid.
    """
    rows, seen = [], set()
    lock = os.path.join(ROOT, "var", "state", "runner.lock")
    stated = None
    try:
        stated = int(io.open(lock, encoding="utf-8").read().strip())
    except Exception:
        stated = None
    live = {r["pid"]: r for r in procs()}
    if stated is not None and stated in live:
        r = live[stated]
        rows.append({"pid": stated, "started": r["started"],
                     "cmd": (r["cmd"] or "(command line not visible)")[:110], "src": "runner.lock"})
        seen.add(stated)
    elif stated is not None:
        # A lock naming a dead pid is stale, not a runner. Say so rather than silently ignoring it.
        rows.append({"pid": stated, "started": "", "cmd": "STALE LOCK - pid not alive",
                     "src": "runner.lock (stale)"})
        seen.add(stated)
    # Everything else that holds an imported copy (worker, health daemon) still comes from the
    # command line, which is fine: those are corroborating, not the thing being verified.
    for r in procs():
        if r["pid"] in seen:
            continue
        if any(h.lower() in (r["cmd"] or "").lower() for h in HOLDERS):
            rows.append({"pid": r["pid"], "started": r["started"], "cmd": r["cmd"][:110],
                         "src": "cmdline"})
    return rows


def boot_epoch():
    """This box's boot time (epoch s) from the kernel's own uptime counter, or None if it cannot be asked."""
    try:
        import ctypes
        return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:
        return None


def lane():
    q = [os.path.basename(x) for x in glob.glob(os.path.join(ROOT, "jobs", "queue", "*.job.json"))]
    q += [os.path.basename(x) for x in glob.glob(os.path.join(ROOT, "var", "queue", "*.job.json"))]
    cur = None
    for p in (os.path.join(ROOT, "var", "state", "CURRENT_RUN.json"),):
        if os.path.exists(p):
            try:
                d = json.load(io.open(p, encoding="utf-8"))
                if d.get("name") and d.get("state") not in ("done", "failed", "stopped"):
                    cur = "%s state=%s" % (d.get("name"), d.get("state"))
                    # A claim last written BEFORE this boot names a process the reboot killed: OS evidence,
                    # not a stale-age guess; the runner re-enters its own order on start.
                    # A claim older than the foundry's CLAIM_STALE_AFTER with NO trainer process alive was left by an
                    # order that ended without its cleanup. Same rule as the foundry's foreign_run_guard / cancel
                    # check: a stale claim is not trusted - ask the OS.
                    try:
                        age = (datetime.datetime.now()
                               - datetime.datetime.fromisoformat(d.get("updated"))).total_seconds()
                    except Exception:
                        age = 0.0
                    if age > CLAIM_STALE_AFTER and not any("run.py" in (r["cmd"] or "") for r in procs()):
                        cur = None
                        rep["claim_stale_no_trainer"] = "%s (claim %.0fs old, no trainer process alive)" % (
                            d.get("name"), age)
                    boot = boot_epoch()
                    if boot is not None and os.path.getmtime(p) < boot - 5:
                        cur = None
                        rep["claim_predates_boot"] = "%s (claim %s, boot %s)" % (
                            d.get("name"), datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%H:%M:%S"),
                            datetime.datetime.fromtimestamp(boot).strftime("%H:%M:%S"))
            except Exception as e:
                cur = "UNREADABLE %r (fail closed: treated as HELD)" % (e,)
    return q, cur


# ---- PAUSE-AND-REDEPLOY, the factory's own controls --------------------------------------------------------------
# An order in flight is a reason to STOP it, not to wait (RUNBOOK Order Submission § PAUSE A FACTORY steps 3-4): the
# lab's deploy methods allow a deploy at any time, safely, regardless of run status. The driver calls these two
# actions around a normal deploy.
ACTION = CFG.get("action")
QDIR = os.path.join(ROOT, "jobs", "queue")
PAUSE_SLIP = os.path.join(QDIR, "zz_fleet_deploy_pause.ctl.json")      # ONE name: the stop below and the restart
PAUSE_BODY = {"action": "pause",                                         # further down both write this slip
              "why": "fleet_deploy asking for a clean exit so the runner can relaunch on newly "
                     "deployed code. Queue is untouched; the runner is relaunched after it exits."}


def _put(path, body):
    with io.open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(body, f)
    os.replace(path + ".tmp", path)


if ACTION == "stop":
    tgt = CFG["target"]
    # a stop the order's CUSTOMER already asked for: the deploy still stops it, but must never requeue it
    cust = []
    for p in glob.glob(os.path.join(QDIR, "*.stop.json")):
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("target") or "") in ("", tgt) and not str(d.get("why") or "").startswith("fleet_deploy "):
            cust.append(os.path.basename(p))
    # the PAUSE first: the order taker reads slips between orders, so without it the runner takes the NEXT
    # queued order the moment this one stops, the deploy then finds an order in flight and refuses. With it, the
    # runner exits instead; the deploy relaunches it.
    _put(PAUSE_SLIP, PAUSE_BODY)
    slip = os.path.join(QDIR, "fleet_deploy-%s.stop.json" % STAMP)
    # target-named: stops exactly this order, never the next one. `pause`: this deploy requeues it (not its customer's own
    # stop), so the foundry sends the desk no verdict for the stop (p20_foundry.report_stopped); never a chat window, which
    # Kirk reopens on the verdict (the Tolkien twin: fleet_deploy_tolkien.comes_back)
    _put(slip, {"target": tgt, "why": CFG["why"], "pause": not cust and not tgt.startswith("hai-win-")})
    rep.update(result="STOP SLIP %s" % os.path.basename(slip), customer_stopped=cust)
    print("@@" + json.dumps(rep))
    sys.exit(0)
if ACTION == "requeue":
    tgt = CFG["target"]
    # archived specs carry a generation stamp (`<name>.job.json.<YYYYMMDD-HHMMSS>`): newest generation, either form
    cands = sorted((p for p in glob.glob(os.path.join(ROOT, "jobs", "archive", glob.escape(tgt) + ".job.json*"))
                    if p.endswith(".job.json") or ".job.json.2" in os.path.basename(p)), key=os.path.getmtime)
    if not cands:
        rep["result"] = "REQUEUE FAILED - no archived spec named %s (requeue it by hand: RUNBOOK § PAUSE step 4)" % tgt
    else:
        name = json.load(io.open(cands[-1], encoding="utf-8")).get("name") or tgt
        _put(os.path.join(QDIR, "fleet_deploy-%s-requeue.ctl.json" % STAMP),
             {"action": "requeue", "order": name, "why": CFG["why"]})
        rep["result"] = "REQUEUE SLIP for %s (from %s)" % (name, os.path.basename(cands[-1]))
    print("@@" + json.dumps(rep))
    sys.exit(0)


# ---- report current state of each file ------------------------------------------------------
for nm, meta in FILES.items():
    t = dest(nm)
    e = {"exists": os.path.exists(t)}
    if e["exists"]:
        b = io.open(t, "rb").read()
        e["sha"] = hashlib.sha256(b).hexdigest()[:16]
        e["bytes"] = len(b)
        e["mtime"] = datetime.datetime.fromtimestamp(os.path.getmtime(t)).strftime("%Y-%m-%d %H:%M:%S")
        e["in_sync"] = (hashlib.sha256(b).hexdigest() == meta["sha"])
    rep["files"][nm] = e

pre = holders()
rep["holders_before"] = pre
# stale_process: a holder that STARTED BEFORE the newest file it may have imported
newest = max([rep["files"][n].get("mtime", "") for n in rep["files"]] or [""])
# REQUIRE A NON-EMPTY `started`. An empty string sorts BEFORE any timestamp, so a holder whose start
# time could not be read would be flagged "PREDATES THE FILE (running old code)" - a false alarm about
# the exact condition this tool exists to detect. Unknown is not old; say so rather than guessing the
# alarming direction.
rep["stale_process"] = [h for h in pre
                        if newest and h.get("started") and h["started"] < newest]
rep["unknown_start"] = [h["pid"] for h in pre if not h.get("started")]

q, cur = lane()
rep["queued"], rep["current"] = q, cur

if not DO:
    print("@@" + json.dumps(rep))
    sys.exit(0)

# ---- deploy ----------------------------------------------------------------------------------
# QUEUED IS NOT RUNNING. Refusing on `q or cur` makes a factory with orders WAITING but nothing in
# flight undeployable - a deadlock the moment the waiting orders need the very fix being deployed.
#
# `cur` (an order in flight) is a REAL refusal and stays: never restart a runner mid-order.
# `q` alone is safe, and the safety is already built -- the restart path asks the factory to PAUSE
# with a control slip, whose documented contract is "the in-flight unit finishes, then a clean
# exit; QUEUE UNTOUCHED". A queue that survives a restart is the whole point of the queue.
#
# Race: the runner may pick up a queued order between this check and the restart. The pause wait
# then sees an order in flight and ends DEFERRED without killing anything (`pause_outcome`); the
# runner exits on the slip after that order and the task's repetition relaunches it on the new files.
if cur and (CFG.get("payload") or not RESTART):
    # the refusal exists so a runner is never restarted mid-order; a per-order payload (the A1 group) or a
    # --no-restart file restarts nothing, and a process already running keeps the copy it loaded
    rep["steps"].append("order %s in flight - no restart in this deploy; running processes keep their copy" % cur)
elif cur:
    rep["result"] = "REFUSED - an order is IN FLIGHT (current=%s); queued=%s" % (cur, q)
    if not cur.startswith("UNREADABLE"):
        rep["in_flight_name"] = cur.split(" state=")[0]     # the driver stops, deploys and requeues it
    print("@@" + json.dumps(rep))
    sys.exit(0)
if q and RESTART and not cur:
    rep["steps"].append("lane has %d queued order(s) but none in flight - deploying via the pause "
                        "slip; the queue survives the restart" % len(q))

written = []
for nm, meta in FILES.items():
    t = dest(nm)
    if rep["files"][nm].get("in_sync"):
        rep["steps"].append("%s already in sync - not rewritten" % nm)
        continue
    # `bak` MUST be re-bound per file, and None when there is nothing to back up: set only inside the
    # exists-branch, a FIRST-TIME deploy would hand the rollback paths below a PREVIOUS iteration's name
    # (or an unbound one) and either restore the wrong file's backup over this one or raise into a bare
    # `except: pass` and report a restore that never happened. A cleanup path that lies about what it did
    # destroys the diagnosis it exists to protect.
    bak = None
    if os.path.exists(t):
        bak = t + ".bak-" + STAMP
        try:
            import shutil
            shutil.copy2(t, bak)
            rep["steps"].append("%s backed up -> %s" % (nm, os.path.basename(bak)))
        except Exception as e:
            rep["result"] = "REFUSED - could not back up %s: %r" % (nm, e)
            print("@@" + json.dumps(rep))
            sys.exit(0)
    else:
        rep["steps"].append("%s is NEW on this box - no prior copy to back up" % nm)

    def _rollback(why):
        """Restore the pre-deploy state and say honestly which of the two states that is."""
        if bak:
            try:
                import shutil
                shutil.copy2(bak, t)
                return why + ", backup restored"
            except Exception as e:
                return why + ", AND THE RESTORE FAILED (%r) - this box is left mid-deploy" % (e,)
        try:
            os.remove(t)
            return why + "; the file was new here, so the partial write was removed"
        except Exception:
            return why + "; the file was new here and the partial write could NOT be removed"

    blob = base64.b64decode(meta["b64"])
    if not os.path.isdir(os.path.dirname(t)):        # a NEW workspace payload (`--workspace <dir>`, first ship)
        os.makedirs(os.path.dirname(t))
        rep["steps"].append("created %s (first payload in this workspace dir)" % os.path.dirname(t))
    io.open(t, "wb").write(blob)
    got = hashlib.sha256(io.open(t, "rb").read()).hexdigest()
    if got != meta["sha"]:
        rep["result"] = "FAILED - " + _rollback("sha mismatch writing %s" % nm)
        print("@@" + json.dumps(rep))
        sys.exit(0)
    if nm.endswith(".py"):
        try:
            ast.parse(io.open(t, encoding="utf-8").read())
        except Exception as e:
            rep["result"] = "FAILED - " + _rollback(
                "%s does not parse on this box (%r)" % (nm, e))
            print("@@" + json.dumps(rep))
            sys.exit(0)
    # `exists` TOO, not just sha/mtime/in_sync. The file table is snapshotted BEFORE the write; refreshing
    # sha/mtime/in_sync afterwards but leaving `exists` at its pre-deploy value makes show(), which checks
    # `exists` FIRST and short-circuits, print a brand-new, verified file as `ABSENT`. Two clocks in one
    # table is how a report lies without ever being wrong.
    rep["files"][nm]["exists"] = True
    rep["files"][nm]["sha"] = got[:16]
    rep["files"][nm]["mtime"] = datetime.datetime.fromtimestamp(
        os.path.getmtime(t)).strftime("%Y-%m-%d %H:%M:%S")
    rep["files"][nm]["in_sync"] = True
    written.append(nm)
rep["written"] = written


# ---- THE RENDER WORKER HOLDS ITS MODULES TOO ---------------------------------------------------------------------
# HOLDERS names only the order taker, but the render worker is a separate long-lived process that imports its modules
# once, so a deploy that restarts only the runner leaves the worker on old code while printing "NEW CODE LIVE". Same
# rule as the runner: read the pid the
# worker STATES (its heartbeat), ask it to exit between jobs (RENDER_WORKER_RESTART), and only if it ignores that
# (a worker from before the flag existed) force an IDLE one - never a worker with a bake/render child in flight.
WORKER_MODULES = ("p20_render_worker.py", "foundry_paths.py", "foundry_lock.py")
WORKER_HB = os.path.join(ROOT, "var", "state", "telemetry", "render_worker.heartbeat")
WORKER_FLAG = os.path.join(ROOT, "var", "state", "RENDER_WORKER_RESTART")
WORKER_TASK = CFG.get("worker_task")            # per box (FLEET); None = this box runs no render worker
WORKER_WAIT_S = 90


def worker_after_wait(alive, busy):
    """One poll of the worker wait. "exited": it honoured the flag. "deferred": it is mid-job (a bake/render child
    is alive) - never killed; it exits on the flag after that job. "kill_idle": alive, idle, and ignoring the flag."""
    if not alive:
        return "exited"
    return "deferred" if busy else "kill_idle"


def stated_worker():
    """The pid the worker writes into its own heartbeat ('<ts> <pid>'). Unreadable = no stated worker."""
    try:
        return int(io.open(WORKER_HB, encoding="utf-8").read().split()[1])
    except Exception:
        return None


def worker_busy(pid):
    """A live child (the bake or the render subprocess) means a job is in flight. Cannot tell = busy (fail closed)."""
    out = ps("(Get-CimInstance Win32_Process -Filter 'ParentProcessId=%d' | Measure-Object).Count" % pid)
    try:
        return int(out.strip() or "0") > 0
    except ValueError:
        return True


def task_exists(name):
    return bool(name) and subprocess.run(["schtasks", "/query", "/tn", name], capture_output=True,
                                         text=True).returncode == 0


def restart_worker():
    mods = [os.path.join(ROOT, "bin", m) for m in WORKER_MODULES if os.path.exists(os.path.join(ROOT, "bin", m))]
    if not mods or not os.path.exists(os.path.join(ROOT, "bin", "p20_render_worker.py")):
        return "no render worker on this box"
    # Never stop a worker nothing can bring back: a box with no worker task to relaunch it keeps its idle worker
    # as it is.
    if not task_exists(WORKER_TASK):
        return ("no worker task %r on this box - render worker left untouched%s" % (
            WORKER_TASK, " (it may still predate these files: start it from its own task by hand)"
            if stated_worker() else ""))
    newest = max(datetime.datetime.fromtimestamp(os.path.getmtime(m)).strftime("%Y-%m-%d %H:%M:%S") for m in mods)
    pid = stated_worker()
    live = {r["pid"]: r for r in procs()}
    if pid is None or pid not in live:
        return "no live render worker stated in its heartbeat - its task's repetition starts one on these files"
    if live[pid]["started"] and live[pid]["started"] >= newest:
        return "render worker pid %d started %s, after the files - current" % (pid, live[pid]["started"])
    io.open(WORKER_FLAG, "w").close()
    outcome = None
    for _ in range(WORKER_WAIT_S // 5):
        time.sleep(5)
        outcome = worker_after_wait(pid in {r["pid"] for r in procs()}, worker_busy(pid))
        if outcome == "exited":
            break
    if outcome == "deferred":
        return ("DEFERRED - render worker pid %d (started %s) is mid-job; it exits on the restart flag after this job "
                "and its task's 15-min repetition relaunches it on the new files. A worker from before the flag "
                "existed ignores it: re-run this deploy when it is idle." % (pid, live[pid]["started"]))
    if outcome == "kill_idle":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, text=True)
        time.sleep(3)
        if pid in {r["pid"] for r in procs()}:
            return "STOPPED SHORT - idle render worker pid %d ignored the restart flag and survived taskkill" % pid
    try:
        os.remove(WORKER_FLAG)                         # a fresh worker must never read the old worker's flag
    except OSError:
        pass
    subprocess.run(["schtasks", "/run", "/tn", WORKER_TASK], capture_output=True, text=True)
    for _ in range(20):
        time.sleep(3)
        npid = stated_worker()
        row = {r["pid"]: r for r in procs()}.get(npid)
        if npid and npid != pid and row and row["started"] >= newest:
            return "render worker NEW CODE LIVE: pid %d started %s (old pid %d %s)" % (
                npid, row["started"], pid, "exited on the flag" if outcome == "exited" else "was idle; forced")
    return "CHECK - render worker relaunched but no fresh pid stated in its heartbeat within 60 s"


if RESTART or CFG.get("worker"):
    rep["worker"] = restart_worker()

if not RESTART:
    rep["result"] = "OK - deployed, NOT restarted (asked for --no-restart; anything holding an "
    rep["result"] += "imported copy is still running the old code)"
    print("@@" + json.dumps(rep))
    sys.exit(0)
# THE RESTART CONDITION IS "A HOLDER PREDATES THE FILE", NOT "SOMETHING WAS WRITTEN". Writing and running
# are independent facts and this tool exists to keep them from drifting apart, so gating the restart on
# having written is the same conflation in miniature: a file already in sync at the correct sha would be
# "nothing to write, runner left alone" while a runner from before the file kept serving orders on stale
# code - reporting success with the exact defect this file was built to catch in front of it.
newest_now = max(rep["files"][n].get("mtime", "") for n in rep["files"])
stale_holders = [h for h in holders()
                 if h.get("started") and newest_now and h["started"] < newest_now]
if not written and not stale_holders:
    # No runner at all while orders wait is not "left alone" - a paused factory would sit idle.
    if q and not holders():
        rep["steps"].append("files already in sync, but NO runner is alive while %d order(s) wait - "
                            "relaunching" % len(q))
        pre = []
    else:
        rep["result"] = "OK - already in sync and nothing predates the file; runner left alone"
        print("@@" + json.dumps(rep))
        sys.exit(0)
if not written and stale_holders:
    rep["steps"].append("files already in sync, but pid(s) %s predate them - restarting for that "
                        "reason alone" % [h["pid"] for h in stale_holders])
    pre = holders()

# ---- restart: ASK THE FACTORY TO PAUSE, do not fight it -----------------------------------------
#
# WHY THIS IS NOT taskkill. The runners are launched `/rl HIGHEST` and an ssh session gets a FILTERED
# token, so a kill by PID over ssh can NEVER succeed: every restart would end "STOPPED SHORT - old pids
# would not die" with files in sync and every runner serving the OLD code, "deployed is not running"
# reintroduced through the restart step itself; a half-successful kill leaves a box with NO order taker.
#
# The factory already has a clean mechanism for this and it needs no elevation: a `{"action":
# "pause"}` control slip. The order taker reads it between orders, finishes any in-flight unit,
# consumes the flag, releases its own lock and exits. Gentler AND it actually works.
#
# taskkill survives only as a LAST RESORT, after the polite request has been given real time.
def pause_outcome(alive, cur):
    """One poll of the pause wait. "exited": every old runner pid is gone. "in_flight": the runner picked up an
    order after the lane check - it reads the pause only between orders, so that order is waited out, never
    killed. None: keep waiting."""
    if not alive:
        return "exited"
    if cur:
        return "in_flight"
    return None


_slip = PAUSE_SLIP
outcome, slipped = None, False
if pre:
    try:
        io.open(_slip, "w", encoding="utf-8").write(json.dumps(PAUSE_BODY))
        slipped = True
        rep["steps"].append("asked the factory to pause (control slip) rather than killing it")
    except OSError as e:
        rep["steps"].append("could not write the pause slip (%r) - falling back to taskkill" % e)

    for _ in range(PAUSE_WAIT_S // 5):
        time.sleep(5)
        outcome = pause_outcome([h["pid"] for h in holders() if h["pid"] in [x["pid"] for x in pre]], lane()[1])
        if outcome:
            break
    if outcome == "in_flight":
        rep["result"] = ("DEFERRED - an order went in flight during the pause wait (current=%s); nothing killed. "
                         % lane()[1]) + (
            "The runner finishes it, exits on the pause slip, and the task's repetition relaunches it on the files "
            "deployed above - verify with --check after that order." if slipped else
            "The pause slip could NOT be written, so the runner keeps the old code: re-run this deploy once the "
            "lane is clear.")
        print("@@" + json.dumps(rep))
        sys.exit(0)

alive = [h["pid"] for h in holders() if h["pid"] in [x["pid"] for x in pre]]
if alive:
    # The polite route did not land (runner wedged, or the slip could not be written). Escalate,
    # and SAY that we escalated -- a silent force-kill is how a wedged runner's cause gets lost.
    rep["steps"].append("pause not honoured within %ds by pid(s) %s - escalating to taskkill"
                        % (PAUSE_WAIT_S, alive))
    for h in pre:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(h["pid"])],
                       capture_output=True, text=True)
    subprocess.run(["schtasks", "/end", "/tn", TASK], capture_output=True, text=True)
    time.sleep(3)
    alive = [h["pid"] for h in holders() if h["pid"] in [x["pid"] for x in pre]]

# the slip is consumed by the runner; remove it if the runner never got to it
try:
    if os.path.exists(_slip):
        os.remove(_slip)
except OSError:
    pass

if alive:
    rep["result"] = ("STOPPED SHORT - old pids %s survived both a pause request and taskkill; NOT "
                     "relaunching, because two runners on one lane is the documented collision. "
                     "This is a WEDGED runner, not a deploy problem - look at it." % alive)
    print("@@" + json.dumps(rep))
    sys.exit(0)

subprocess.run(["schtasks", "/run", "/tn", TASK], capture_output=True, text=True)
post = []
for _ in range(15):
    time.sleep(2)
    post = holders()
    if post:
        break
rep["holders_after"] = post
newest = max(rep["files"][n].get("mtime", "") for n in rep["files"])
old = [h for h in post if h["started"] < newest]
rep["running_new_code"] = bool(post) and not old
rep["result"] = ("OK - deployed and running new code" if rep["running_new_code"]
                 else "CHECK - relaunched but %s still predate the file" % [h["pid"] for h in old]
                 if post else "CHECK - no runner came back up")
print("@@" + json.dumps(rep))
'''


def run_on(box, cfg):
    src = REMOTE.replace("__CFG__", base64.b64encode(
        json.dumps(cfg).encode("utf-8")).decode("ascii"))
    if box["local"]:
        # Use SSH even for local hosts to run as SYSTEM (via sshd). A direct child
        # process lacks elevation, causing `taskkill` failures during restart.
        r = subprocess.run(["ssh", box["host"], "python.exe -"], input=src.encode("utf-8"),
                           capture_output=True, timeout=420)
    else:
        r = subprocess.run(["ssh", box["host"], "python.exe -"], input=src.encode("utf-8"),
                           capture_output=True, timeout=420)
    out = r.stdout.decode("utf-8", "replace")
    hit = [x for x in out.splitlines() if x.startswith("@@")]
    if not hit:
        return {"result": "NO REPLY", "stdout": out[-400:],
                "stderr": r.stderr.decode("utf-8", "replace")[-400:]}
    return json.loads(hit[-1][2:])


# Delete only after archiving to bin/_retired/<stamp>/ and verifying the hash matches; refuse to delete a file still imported by other modules in bin.
RETIRE = r'''
import base64, hashlib, json, os, re, shutil
args = json.loads(base64.b64decode("__ARGS__").decode("utf-8"))
root, names, stamp = args[:3]
sub = args[3] if len(args) > 3 else "bin"          # "bin", or a workspace folder ("workspace/<dir>")
bin_dir = os.path.join(root, *sub.split("/"))
out = {"host": os.environ.get("COMPUTERNAME", "?"), "files": {}}
sha = lambda q: hashlib.sha256(open(q, "rb").read()).hexdigest()
# only bin's modules import one another; a workspace folder's files are run per order, never imported by bin
others = [f for f in os.listdir(bin_dir) if f.endswith(".py") and f not in names] if sub == "bin" else []
for nm in names:
    p = os.path.join(bin_dir, nm)
    if not os.path.isfile(p):
        out["files"][nm] = "absent"
        continue
    mod = nm[:-3] if nm.endswith(".py") else nm
    pat = re.compile(r"^\s*(import|from)\s+" + re.escape(mod) + r"\b", re.M)
    users = [f for f in others
             if pat.search(open(os.path.join(bin_dir, f), encoding="utf-8", errors="replace").read())]
    if users:
        out["files"][nm] = "REFUSED - imported by " + ", ".join(sorted(users)[:5])
        continue
    dst = os.path.join(bin_dir, "_retired", stamp, nm)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(p, dst)
    if sha(dst) != sha(p):
        out["files"][nm] = "REFUSED - the archive copy does not match"
        continue
    os.remove(p)
    out["files"][nm] = "retired -> " + dst
print("@@" + json.dumps(out))
'''


def retire_on(box, names, stamp, sub="bin"):
    src = RETIRE.replace("__ARGS__", base64.b64encode(
        json.dumps([box["root"], names, stamp, sub]).encode("utf-8")).decode("ascii"))
    r = subprocess.run(["ssh", box["host"], "python.exe -"], input=src.encode("utf-8"), capture_output=True,
                       timeout=120)
    out = r.stdout.decode("utf-8", "replace")
    hit = [x for x in out.splitlines() if x.startswith("@@")]
    if not hit:
        return {"files": {}, "error": "NO REPLY: " + (out + r.stderr.decode("utf-8", "replace"))[-300:]}
    return json.loads(hit[-1][2:])


def retire(names, hosts=None, sub="bin"):
    """--retire: archive-then-remove the named files from <root>\\bin (or, with --workspace <dir>, from that workspace
    folder) on every GPU factory (or --only)."""
    bad = [n for n in names if os.path.basename(n) != n or not n]
    if bad:
        print("--retire takes bare file names in bin, not paths: %s" % bad)
        return 2
    parts = sub.split("/")
    if not (sub == "bin" or (len(parts) == 2 and parts[0] == "workspace" and parts[1] and parts[1] not in (".", ".."))):
        print("--retire works in bin or one workspace folder (--workspace <dir>), not %r" % sub)
        return 2
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    rc = 0
    for box in [b for b in FLEET if not hosts or b["host"] in hosts]:
        rep = retire_on(box, names, stamp, sub)
        print("-- %-10s %s" % (box["host"], rep.get("error", "")))
        for nm, what in sorted(rep.get("files", {}).items()):
            print("   %-28s %s" % (nm, what))
            rc |= 1 if what.startswith("REFUSED") else 0
        rc |= 1 if rep.get("error") else 0
    return rc


# Run the foundry suite post-deploy to verify the pin; a red result, exit code 3 (pin mismatch), or execution failure fails the deploy.
# The environment is configured with FOUNDRY_ROOT and cwd as required by the runbook (RUNBOOK-Orchestrator-Deploy-Procedure).
SUITE = r'''
import json, os, subprocess, sys
root = %r
p = subprocess.run([sys.executable, "-W", "error::SyntaxWarning", "test_foundry.py"], cwd=os.path.join(root, "tests"),
                   env=dict(os.environ, FOUNDRY_ROOT=root), capture_output=True, text=True)
out = [l for l in (p.stdout + p.stderr).strip().splitlines() if l.strip()]
print("@@" + json.dumps({"rc": p.returncode, "tail": out[-6:]}))
'''


def run_suite(box):
    """{"rc", "tail"} from the box's own foundry suite; rc None = it could not be run or did not answer."""
    try:
        r = subprocess.run(["ssh", box["host"], "python.exe -"], input=(SUITE % box["root"]).encode("utf-8"),
                           capture_output=True, timeout=420)      # run_on's bound for the same kind of ssh call
    except Exception as exc:                                     # noqa: BLE001
        return {"rc": None, "tail": ["could not run: %r" % exc]}
    hit = [x for x in r.stdout.decode("utf-8", "replace").splitlines() if x.startswith("@@")]
    if not hit:
        return {"rc": None, "tail": [r.stderr.decode("utf-8", "replace")[-300:]]}
    return json.loads(hit[-1][2:])


def suite_verdict(s):
    """(ok, word): GREEN only on exit 0; the suite's own exit 3 is a pin mismatch; no reply is COULD NOT RUN (fail closed)."""
    rc = s.get("rc")
    if rc == 0:
        return True, "GREEN"
    return False, {3: "PIN MISMATCH", None: "COULD NOT RUN"}.get(rc, "RED (exit %s)" % rc)


DESK = _P.head_url()          # the head's desk; '' when this box is not linked
DEPLOY_STOP_TAIL = ": stop for a deploy (answered rows are kept; requeued after the restart)"
WAIT_STOP_S = 1800      # fleet_deploy_tolkien.deploy's wait_stop_s: the same bound on the same kind of wait


def deck_order(host):
    """The order the factory's DECK says it is running ('' = none), read on the head - zero cost
    to the box. An unlinked box has no deck to ask, so the answer is 'unknown' ('')."""
    if not DESK:
        return ""
    import urllib.request
    with urllib.request.urlopen("%s/p20/factory/%s" % (DESK, host), timeout=15) as r:
        return str(json.loads(r.read().decode("utf-8") or "{}").get("order") or "")


def stop_deploy_requeue(box, cfg, name, stamp, run=None, order_on_deck=deck_order, sleep=None,
                        wait_s=WAIT_STOP_S, poll_s=10):
    """PAUSE-AND-REDEPLOY for a box whose lane holds an order: a stop slip naming exactly that order, wait until the
    deck no longer shows it (the order keeps its answered rows), deploy, then - in a finally, so a failed deploy still
    gives the order back - a requeue slip for its newest archived generation. An order its own CUSTOMER had already
    asked to stop is stopped but never requeued. A stop the factory does not honour within `wait_s` deploys nothing
    and requeues nothing (the order is still running): DEFERRED, said plainly. Returns the deploy's report."""
    import time as _t
    run = run or run_on
    sleep = sleep or _t.sleep
    why = "fleet_deploy %s%s" % (stamp, DEPLOY_STOP_TAIL)
    small = dict(cfg, files={}, target=name, why=why)
    st = run(box, dict(small, action="stop"))
    steps = ["stopped %s for this deploy (%s)" % (name, st.get("result"))]
    if not str(st.get("result") or "").startswith("STOP SLIP"):
        return {"result": "DEFERRED - the stop slip for %s was not written: %s" % (name, st.get("result")),
                "steps": steps}
    customer = st.get("customer_stopped") or []
    t0, left = _t.time(), None
    while _t.time() - t0 < wait_s:
        try:
            if name not in order_on_deck(box["host"]):
                left = True
                break
        except Exception:                                            # noqa: BLE001 - the deck could not tell: ask again
            pass
        sleep(poll_s)
    if not left:
        return {"result": "DEFERRED - %s still on the deck %d s after its stop slip; nothing deployed, nothing "
                          "requeued (the order is still running)" % (name, wait_s), "steps": steps}
    rep = {"result": "EXCEPTION before the deploy"}
    try:
        rep = run(box, cfg)
    finally:
        if customer:
            steps.append("NOT requeued: its customer had already asked it to stop (%s)" % ", ".join(customer))
        else:
            rq = run(box, dict(small, action="requeue"))
            steps.append("requeue: %s" % rq.get("result"))
        rep.setdefault("steps", [])
        rep["steps"] = steps + list(rep["steps"])
    return rep


def show(box, rep, deploying):
    print("-- %-11s %s" % (box["host"], rep.get("root", "")))
    if rep.get("result"):
        print("   %s" % rep["result"])
    for nm, e in sorted((rep.get("files") or {}).items()):
        if not e.get("exists"):
            print("   %-22s ABSENT" % nm)
            continue
        print("   %-22s %s  %s  %s" % (nm, e.get("sha"),
                                       "in sync" if e.get("in_sync") else "STALE",
                                       e.get("mtime")))
    for s in rep.get("steps") or []:
        print("   step  %s" % s)
    for h in rep.get("holders_before") or []:
        if h in (rep.get("stale_process") or []):
            tag = "  <-- PREDATES THE FILE (running old code)"
        elif not h.get("started"):
            tag = "  <-- start time UNREADABLE (not a verdict either way)"
        else:
            tag = ""
        print("   held  pid %-7s started %-19s%s" % (h["pid"], h.get("started") or "?", tag))
    for h in rep.get("holders_after") or []:
        print("   new   pid %-7s started %s" % (h["pid"], h["started"]))
    if rep.get("claim_predates_boot"):
        print("   lane  CURRENT_RUN claim predates this boot - nothing can hold it: %s" % rep["claim_predates_boot"])
    if rep.get("claim_stale_no_trainer"):
        print("   lane  CURRENT_RUN claim is stale and no trainer is alive - not in flight: %s"
              % rep["claim_stale_no_trainer"])
    if rep.get("queued") or rep.get("current"):
        print("   lane  queued=%s current=%s" % (rep.get("queued"), rep.get("current")))
    if "running_new_code" in rep:
        print("   NEW CODE LIVE: %s" % rep["running_new_code"])
    if rep.get("worker"):
        print("   worker %s" % rep["worker"])
    for k in ("stdout", "stderr"):
        if rep.get(k):
            print("   %s %s" % (k, rep[k].replace("\n", " ")[:250]))
    print("")


COMMIT_ATTR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def mirror_snapshot(paths):
    """{realpath: sha256} of the carried files' bytes when a deploy starts: what it is about to ship."""
    out = {}
    for p in paths:
        try:
            with open(p, "rb") as f:
                out[os.path.realpath(p)] = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            out[os.path.realpath(p)] = None
    return out


def commit_mirrors(paths, label, run=subprocess.run, shipped=None):
    """Commit carried files in their respective git repos -> [(repo, short hash or None)].

    Only committed paths are touched; a failure is reported but does not raise (fail-safe). With `shipped` (a
    mirror_snapshot taken when the deploy started), a file whose bytes changed DURING the deploy is not committed: the
    deploy shipped its earlier bytes, so a commit saying "deployed" would describe code that never went out."""
    out = []
    try:
        if shipped is not None:
            now = mirror_snapshot(paths)
            moved = [p for p in paths if now.get(os.path.realpath(p)) != shipped.get(os.path.realpath(p))]
            for p in moved:
                print("mirror  %s changed during the deploy - NOT committed (the deploy shipped its earlier bytes)" % p)
            paths = [p for p in paths if p not in moved]
        by_repo = {}
        for p in paths:
            # realpath on BOTH sides: git answers with the long form, a path can arrive in its 8.3 short form,
            # and relpath across two spellings climbs out of the repo, so `git add` refuses it (found by the test)
            p = os.path.realpath(p)
            r = run(["git", "-C", os.path.dirname(p), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
            if r.returncode != 0 or not r.stdout.strip():
                print("mirror  %s is in no git repo - not committed" % p)
                continue
            top = os.path.realpath(r.stdout.strip())
            by_repo.setdefault(top, []).append(os.path.relpath(p, top).replace(os.sep, "/"))
        for top, rels in sorted(by_repo.items()):
            name = os.path.basename(top)
            add = run(["git", "-C", top, "add", "--"] + rels, capture_output=True, text=True)
            if add.returncode != 0:
                print("mirror  %s NOT committed - git add failed: %s" % (name, (add.stderr or "").strip()[-200:]))
                continue
            staged = run(["git", "-C", top, "diff", "--cached", "--quiet", "--"] + rels, capture_output=True, text=True)
            if staged.returncode == 0:              # exit 0 = no staged difference: git itself says nothing changed
                print("mirror  %s: nothing new to commit" % name)
                out.append((top, None))
                continue
            c = run(["git", "-C", top, "commit", "-q", "-m", label, "-m", "\n".join(rels), "-m", COMMIT_ATTR, "--"] + rels,
                    capture_output=True, text=True)
            if c.returncode != 0:
                print("mirror  %s NOT committed - git commit failed: %s" % (name, ((c.stderr or "") + (c.stdout or "")).strip()[-200:]))
                continue
            h = run(["git", "-C", top, "log", "-1", "--format=%h"], capture_output=True, text=True).stdout.strip()
            print("mirror  %s committed %s (%d file(s))" % (name, h, len(rels)))
            out.append((top, h))
    except Exception as exc:                                         # noqa: BLE001 - never replace the deploy's verdict
        print("mirror  commit could not run: %r" % exc)
    return out


def deploy_a1(a, sub="a1", group=None, src_dir=None):
    """A payload group to every GPU factory's <root>\\workspace\\<sub>: hashed, backed up, verified, no restart.
    Default: the A1 group. `--workspace <dir> f.py ...` ships mirror workspace\\<dir> files the same way."""
    group = group or A1_GROUP
    src_dir = src_dir or A1_SRC
    if not src_dir:
        print("REFUSED: A1_SRC is not set - no A1 payload source on this head (a setting in etc\\lab.env)")
        return 2
    files = {}
    for box_name, src in group.items():
        b = io.open(os.path.join(src_dir, src), "rb").read()
        files[box_name] = {"sha": hashlib.sha256(b).hexdigest(), "b64": base64.b64encode(b).decode("ascii")}
        print("src     %-22s %s  %d bytes%s" % (box_name, files[box_name]["sha"][:16], len(b),
                                               "" if src == box_name else "  (from %s)" % src))
    print("")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bad = 0
    for box in [b for b in FLEET if not a.only or b["host"] in a.only]:
        home = box["root"] + "\\workspace\\" + sub
        # no holders: the runner never imports these (each order's own process does), so a runner older than the
        # file is not running old A1 code
        cfg = {"root": box["root"], "task": box["task"], "stamp": stamp, "holders": [],
               "deploy": not a.check, "restart": False, "payload": True, "homes": {n: home for n in files},
               "files": ({n: {"sha": f["sha"]} for n, f in files.items()} if a.check else files)}
        try:
            rep = run_on(box, cfg)
        except Exception as exc:
            rep = {"result": "EXCEPTION %s" % repr(exc)[:200]}   # never dump the payload
        rep["stale_process"] = []                # the runner never imports the A1 group: its age is not a verdict here
        show(box, rep, not a.check)
        if str(rep.get("result", "")).startswith(("FAILED", "REFUSED", "STOPPED", "NO REPLY", "EXCEPTION", "CHECK")):
            bad += 1
        elif any(not f.get("in_sync") for f in (rep.get("files") or {}).values()):
            bad += 1
    if a.check:
        print("check only - nothing was written")
    return 1 if bad else 0


def main():
    if not FLEET:
        print("no fleet table: set FLEET_TABLE (host -> root, task, worker_task) in %s" % _P.LAB_ENV)
        return 2
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", help="module filenames in the mirror, e.g. p20_foundry.py")
    ap.add_argument("--check", action="store_true", help="report only; write nothing")
    ap.add_argument("--worker", action="store_true",
                    help="with --no-restart: restart the render worker anyway, between its jobs. It is not the order "
                         "taker, so an order in flight does not stop it")
    ap.add_argument("--no-restart", action="store_true",
                    help="deploy without bouncing the runner (only safe for a module no "
                         "long-lived process has imported)")
    ap.add_argument("--only", action="append", default=None, metavar="HOST",
                    help="limit to one or more hosts")
    ap.add_argument("--a1", action="store_true",
                    help="the A1 payload group -> <root>\\workspace\\a1 on every GPU factory (no restart)")
    ap.add_argument("--workspace", metavar="DIR",
                    help="ship the named files from this mirror's workspace\\DIR to <root>\\workspace\\DIR on every "
                         "GPU factory (a per-order payload: no restart), e.g. --workspace flashnext answers.py")
    ap.add_argument("--rigby-a1", action="store_true",
                    help="rigby's A1 payload -> the share Tolkien loads it from (suites gated in its image first)")
    ap.add_argument("--engine", metavar="ORDER_DIR",
                    help="a delivered lab ENGINE build (labengine-* order dir) -> a versioned Ollama-*-lab tree on each "
                         "GPU factory, launcher repointed, ollama restarted under the lane's own pause (fleet_engine.py)")
    ap.add_argument("--engine-tag", metavar="TAG",
                    help="with --engine: the build's own commit with a lab revision added (620d222-r2), for a build "
                         "whose source tree was renamed to its revision after it was built; anything else is refused")
    ap.add_argument("--engine-operator-present", action="store_true",
                    help="with --engine: the operator is about to click Allow on Windows' own firewall prompt on a "
                         "console-launch box (Mothership). A box with no rule for the new exe is switched instead of "
                         "held, and the deploy waits 30 min for the click before rolling back. Writes no firewall rule")
    ap.add_argument("--engine-rollback", action="store_true",
                    help="each GPU factory's launcher back to its newest pre-lab backup, ollama restarted")
    ap.add_argument("--retire", action="store_true",
                    help="archive-then-remove the named files from <root>\\bin on every GPU factory (or --only); "
                         "refuses a file another module there still imports. With --workspace <dir>, from "
                         "<root>\\workspace\\<dir> instead (no import check: workspace files run per order)")
    a = ap.parse_args()
    if a.retire:
        if not a.files:
            print("--retire needs the file names to retire")
            return 2
        return retire(a.files, a.only, sub=("workspace/" + a.workspace) if a.workspace else "bin")
    if a.engine or a.engine_rollback:
        import fleet_engine as E
        tag = E.revision_tag(a.engine, a.engine_tag) if (a.engine and a.engine_tag) else None
        rows, bad = E.deploy(a.engine, hosts=a.only, run_on=run_on, stop_deploy_requeue=stop_deploy_requeue,
                             deck_order=deck_order, rollback=a.engine_rollback, tag=tag,
                             operator_present=a.engine_operator_present)
        E.show(rows, judge=False)
        print("")
        drift = E.show(E.check(a.only))
        return 1 if (bad or drift) else 0
    if a.rigby_a1:
        import fleet_deploy_tolkien as T
        if a.check:
            return T.rigby_a1_check()
        res = T.deploy_rigby_a1()
        print("-- rigby-a1  " + res)
        return 0 if res.startswith("OK") else 1
    if a.a1:
        return deploy_a1(a)
    if a.workspace:
        if not a.files:
            print("--workspace needs the file names to ship")
            return 2
        src = os.path.join(MIRROR, "workspace", a.workspace)
        ws_paths = [os.path.join(src, n) for n in a.files]
        ws_shipped = mirror_snapshot(ws_paths)
        rc = deploy_a1(a, sub=a.workspace, group={n: n for n in a.files}, src_dir=src)
        if rc == 0 and not a.check:
            commit_mirrors(ws_paths, "Deployed workspace\\%s to the factories" % a.workspace, shipped=ws_shipped)
        return rc

    # FOUR factories, one front door: a file deploys to the family whose mirror holds it (the GPU factories:
    # this mirror; Tolkien: tolkien-build). A bare --check reports all four.
    import fleet_deploy_tolkien as T
    tk = [n for n in a.files if n in T.SHARED or os.path.exists(os.path.join(T.MIRROR, n))]
    both = [n for n in tk if os.path.exists(os.path.join(MIRROR, n))]
    if both:
        print("ambiguous - in BOTH the GPU and the Tolkien mirror, deploy refuses to guess: %s" % both)
        return 2
    tk_wanted = (not a.only or "tolkien" in a.only)
    tk_rc = 0
    if tk_wanted and (tk or (a.check and not a.files)):
        if a.check:
            tk_rc = T.check(tk)
        else:
            # The files Tolkien shares with the head (T.SHARED) are built from the head's LIVE copy on Sundae, never
            # shipped from a mirror here, so their commit belongs to the head's door; only Tolkien's own files commit.
            tk_own = [os.path.join(T.MIRROR, n) for n in tk if n not in T.SHARED]
            tk_shipped = mirror_snapshot(tk_own)
            res = T.deploy(tk)
            print("-- tolkien  " + res)
            tk_rc = 0 if res.startswith("OK") else 1
            if tk_rc == 0:
                if any(n in T.SHARED for n in tk):
                    print("mirror  %s: shared with the head, committed by the head's door when it deploys them"
                          % ", ".join(n for n in tk if n in T.SHARED))
                if tk_own:
                    commit_mirrors(tk_own, "Deployed to Tolkien (its gate green)", shipped=tk_shipped)
        print("")
    gpu_files = [n for n in a.files if n not in tk]
    if a.files and not gpu_files:
        return tk_rc
    if a.only and not [b for b in FLEET if b["host"] in a.only]:
        return tk_rc
    names = gpu_files or ["p20_foundry.py"]
    files = {}
    for nm in names:
        p = os.path.join(MIRROR, MIRROR_SUBDIR.get(nm, ""), nm)
        if not os.path.exists(p):
            print("no such module in either mirror: %s" % nm)
            return 2
        b = io.open(p, "rb").read()
        files[nm] = {"sha": hashlib.sha256(b).hexdigest(),
                     "b64": base64.b64encode(b).decode("ascii")}
        print("mirror  %-22s %s  %d bytes" % (nm, files[nm]["sha"][:16], len(b)))
    print("")

    boxes = [b for b in FLEET if not a.only or b["host"] in a.only]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bad = 0
    shipped = []
    for box in boxes:
        box_files, homes = files_for(box["host"], files)
        skipped = sorted(set(files) - set(box_files))
        if skipped:
            print("-- %-10s not shipped (does not run on this box): %s" % (box["host"], ", ".join(skipped)))
        if not box_files:
            continue
        shipped.append(box)
        cfg = {"root": box["root"], "task": box["task"], "stamp": stamp, "holders": list(HOLDERS),
               "worker_task": box.get("worker_task"),
               "deploy": not a.check, "restart": not a.no_restart, "worker": a.worker, "homes": homes,
               "files": ({n: {"sha": f["sha"]} for n, f in box_files.items()} if a.check
                         else box_files)}
        try:
            rep = run_on(box, cfg)
            if (not a.check and not a.no_restart and rep.get("in_flight_name")
                    and str(rep.get("result", "")).startswith("REFUSED - an order is IN FLIGHT")):
                print("-- %-10s %s - pause-and-redeploy: stop it, deploy, requeue it" % (box["host"], rep["result"]))
                rep = stop_deploy_requeue(box, cfg, rep["in_flight_name"], stamp)
        except Exception as exc:
            rep = {"result": "EXCEPTION %s" % repr(exc)[:200]}   # never dump the payload
        show(box, rep, not a.check)
        if str(rep.get("result", "")).startswith(("FAILED", "REFUSED", "STOPPED", "NO REPLY",
                                                  "EXCEPTION", "CHECK", "DEFERRED")):
            bad += 1
    if a.check:
        print("check only - nothing was written")
    elif not bad:
        for box in shipped:                       # the suite each factory runs on the code it now holds
            s = run_suite(box)
            ok, word = suite_verdict(s)
            print("-- %-10s foundry suite %s: %s" % (box["host"], word, " | ".join(s.get("tail") or [])[-300:]))
            if not ok:
                bad += 1
        if bad:
            print("*** FOUNDRY SUITE NOT GREEN on %d box(es) - the files ARE deployed; fix or roll back (backups above) ***"
                  % bad)
        elif gpu_files:
            gpu_paths = {nm: os.path.join(MIRROR, MIRROR_SUBDIR.get(nm, ""), nm) for nm in gpu_files}
            commit_mirrors(list(gpu_paths.values()),
                           "Deployed to %s (every foundry suite green)" % ", ".join(b["host"] for b in shipped),
                           shipped={os.path.realpath(p): files[nm]["sha"] for nm, p in gpu_paths.items() if nm in files})
    # a file the Tolkien image bakes in from THIS mirror (the one grader) reaches the fourth factory too - only after the
    # GPU factories took it, because the Tolkien branch ships only a copy Mothership already runs
    fs = [n for n in names if n in T.FOUNDRY_SHARED]
    if fs and not a.check and not bad and tk_wanted and (not a.only or "tolkien" in a.only):
        res = T.deploy(fs)
        print("-- tolkien  (foundry-shared %s) %s" % (", ".join(fs), res))
        tk_rc = tk_rc or (0 if res.startswith("OK") else 1)
    a1_rc = 0
    if a.check and not a.files:                  # the routine check covers the A1 payload too: drift shows where looked
        print("")
        print("== A1 payload (workspace\\a1)")
        a1_rc = deploy_a1(a)
        print("")
        print("== rigby's A1 payload (tabfoundry-jobs\\payloads\\a1)")
        a1_rc = T.rigby_a1_check() or a1_rc
        print("")
        print("== ENGINES (the serving llama-server on each factory: lab patch markers = lossless resume)")
        import fleet_engine as E
        try:
            a1_rc = E.show(E.check()) or a1_rc
        except Exception as exc:                                     # noqa: BLE001 - the code check above still stands
            print("engine check could not run: %r" % exc)
            a1_rc = 1
    return 1 if (bad or tk_rc or a1_rc) else 0


if __name__ == "__main__":
    sys.exit(main())
