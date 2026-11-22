"""Lab stager for Windows Update: installs updates one box at a time, enforcing the lab's safety invariants.

Usage:
    python update_stage.py --nightly                   # Nightly task (03:20): process first pending box
    python update_stage.py <box> [--allow-reboot]      # Manual run: reboot only if explicitly requested
    python update_stage.py --resume-inflight           # Recover from interrupted stage

Stage Protocol:
    1. WAIT if box is busy or status is unclear.
    2. ACQUIRE Hold: claim GPU to prevent placement.
    3. PAUSE Factory: Stop renders, disable runner, set safety resume task.
    4. INSTALL: Execute `wu_stage_box.ps1` as SYSTEM. Canary-only (KBs proven elsewhere).
    5. REBOOT: Verify via `LastBootUpTime` (not SSH ping).
    6. HEALTH CHECK: Verify GPU health and services.
    7. RESTORE: Resume factory; release hold only if healthy, else hold for human.
    8. CONFIGURE: On first success, switch Windows Update to "Download only" (AUOptions=3).

Logging:
    Writes state to `<DOCS_STATE_DIR>\\lab-updates\\` and appends to `stager.log`."""
import base64
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

import lab_settings as S

LAB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the Lab tree (this bin's parent)
DOCS_STATE = S.setting("DOCS_STATE_DIR", "")       # the library's State folder (lab-updates, gpu-reports live under it)
HEAD = S.setting("HEAD_NAME", "")                  # the box that runs the head's compose stack (an ssh name)
TABLET_HEAD = S.setting("TABLET_HEAD_NAME", "")    # the box that runs the tablet foundry
MIRRORS = S.setting_json("CODE_MIRRORS", {}) or {} # name -> the code mirror's folder on this box
HAI_DIR_ON_BOX = r"C:\ProgramData\HAI"            # the lab's folder on every Windows box: the same on each, not a lab path

STATE = os.path.join(DOCS_STATE, "lab-updates")
DESK = S.head_url()                        # the head; every head endpoint derives from this
OLLAMA_PORT = S.setting("OLLAMA_PORT", "11434")   # same name the other tools use
THIS_BOX = os.environ.get("COMPUTERNAME", "").lower()               # the box this tool runs on (downloads live here)
# Per-box roles are settings: boxes that take only proven KBs, the box whose AMD driver is reported (never installed)
PROVEN_KBS_ONLY = set(S.setting_json("PROVEN_KBS_ONLY", []))
AMD_DRIVER_BOX = S.setting("AMD_DRIVER_BOX", "")
GPU_REPORTS = os.path.join(DOCS_STATE, "gpu-reports")


def boxes():
    """name -> {ip, runner, state dir}. The address is the fleet table (FLEET_HOSTS); the runner
    task name and state dir are per-box settings (BOX_RUNNERS / BOX_STATE_DIRS). No fleet means
    nothing to stage, and that is said rather than assumed."""
    runners = S.setting_json("BOX_RUNNERS", {})
    states = S.setting_json("BOX_STATE_DIRS", {})
    return {name: {"ip": ip,
                   "runner": runners.get(name, ""),
                   "state": states.get(name, "")}
            for name, ip in S.fleet_hosts().items()}


BOXES = boxes()
# The boxes to stage, in criticality order (least critical first). The order is a setting; with
# no fleet configured there is no order, and every loop over ORDER finds nothing to do.
ORDER = S.setting_json("STAGE_ORDER", sorted(BOXES))
HOLD_TTL = 1800                 # rolling: refreshed at every poll, so a dead stager's hold lapses on its own
FAIL_HOLD_TTL = 12 * 3600       # a box that failed its after-checks stays out of placement until someone looks
SAFETY_RESUME_H = 4             # the on-box resume task fires this long after the pause if the stager never resumed
INFLIGHT = os.path.join(STATE, "stager-inflight.json")
NOISE = re.compile(r"Security Intelligence Update for Microsoft Defender", re.I)

# ---- the box side: one python script, one action per call (the fleet_deploy REMOTE pattern) ---------------------------
REMOTE = r'''
import base64, json, os, subprocess, time
C = json.loads(base64.b64decode("__CFG__").decode("utf-8"))
D = os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "HAI", "wu-stage")
os.makedirs(D, exist_ok=True)
out = {"action": C["action"]}
def ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()
def ps_file(name, body):
    p = os.path.join(D, name)
    open(p, "w", encoding="utf-8-sig").write(body)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", p], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()
# the runner is `python[w].exe ... p20_foundry.py --queue` on every box. Match the process NAME
# too: the querying powershell's own command line contains both strings (exclude the observer)
RUNNER_Q = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\\.exe$' -and "
            "$_.CommandLine -like '*p20_foundry.py*' -and $_.CommandLine -like '*--queue*' }")
def runners():
    q = ps(RUNNER_Q + " | ForEach-Object { $_.ProcessId }")
    return [int(x) for x in q.split() if x.isdigit() and int(x) != os.getpid()]
flag = os.path.join(C["state"], "STOP_AFTER_RENDER")
resume_ps1 = ("$f = '%s'\n" % flag
              + "$r = @(" + RUNNER_Q + ")\n"
              + "if ((Test-Path $f) -and $r.Count -eq 0) { Remove-Item -Force $f }\n"
              + "Enable-ScheduledTask -TaskName '%s' | Out-Null\nStart-ScheduledTask -TaskName '%s'\n" % (C["runner"], C["runner"])
              + "Unregister-ScheduledTask -TaskName 'HAI Update Stager Resume' -Confirm:$false -ErrorAction SilentlyContinue\n")
a = C["action"]
if a == "pause":
    open(flag, "w").close()
    open(os.path.join(D, "resume_foundry.ps1"), "w", encoding="utf-8-sig").write(resume_ps1)
    out["disable"] = ps("Disable-ScheduledTask -TaskName '%s' | Out-Null; [string](Get-ScheduledTask -TaskName '%s').Settings.Enabled" % (C["runner"], C["runner"]))
    at = (time.time() + C["safety_h"] * 3600)
    out["safety"] = ps_file("register_safety.ps1",
        "$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File %s'\n"
        "$t = New-ScheduledTaskTrigger -Once -At ([datetime]'%s')\n"
        "$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest\n"
        "Register-ScheduledTask -TaskName 'HAI Update Stager Resume' -Action $a -Trigger $t -Principal $p -Force | Out-Null\n"
        "[string](Get-ScheduledTaskInfo -TaskName 'HAI Update Stager Resume').NextRunTime\n"
        % (os.path.join(D, "resume_foundry.ps1"), time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at))))
    out["flag"] = os.path.exists(flag)
elif a == "runner":
    out["procs"] = runners()
    out["flag"] = os.path.exists(flag)
elif a == "start_wu":
    open(os.path.join(D, "wu_stage_box.ps1"), "w", encoding="utf-8-sig").write(C["ps1"])
    res = os.path.join(D, "result.json")
    if os.path.exists(res):
        os.replace(res, res + ".prev")
    out["start"] = ps_file("register_wu.ps1",
        "$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File %s -Mode %s -Only %s'\n"
        "$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest\n"
        "$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0) -MultipleInstances IgnoreNew\n"
        "Register-ScheduledTask -TaskName 'HAI WU Stage' -Action $a -Principal $p -Settings $s -Force | Out-Null\n"
        "Start-ScheduledTask -TaskName 'HAI WU Stage'\n'started'\n"
        % (os.path.join(D, "wu_stage_box.ps1"), C["mode"], C.get("only") or "none"))   # KBs are digits+commas only
elif a == "poll":
    res = os.path.join(D, "result.json")
    try:
        out["result"] = json.loads(open(res, encoding="utf-8-sig").read())
    except (OSError, ValueError):
        out["result"] = None
    out["task"] = ps("[string](Get-ScheduledTask -TaskName 'HAI WU Stage' -ErrorAction SilentlyContinue).State")
    out["boot"] = ps("(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('s')")
elif a == "reboot":
    r = subprocess.run(["shutdown", "/r", "/t", "60", "/c", "HAI lab update stager (#38): restart after Windows updates"],
                       capture_output=True, text=True)
    out["rc"] = r.returncode
elif a == "resume":
    out["resume"] = ps_file("resume_now.ps1", resume_ps1)
    time.sleep(20)
    out["runner_task"] = ps("$t = Get-ScheduledTask -TaskName '%s'; [string]$t.State + ' enabled=' + [string]$t.Settings.Enabled" % C["runner"])
    out["procs"] = runners()
    out["flag"] = os.path.exists(flag)
elif a == "nvsmi":
    r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], capture_output=True, text=True)
    out["driver"] = r.stdout.strip().split(",")[-1].strip() if r.returncode == 0 and "," in r.stdout else None
    out["error"] = (r.stdout + r.stderr).strip()[-120:] if not out["driver"] else None
elif a == "nvidia_install":
    # the lab's documented procedure (Lab\bin\nvidia_driver_install.ps1: Authenticode, SYSTEM one-shot task, -s -noreboot,
    # the box's lock/power task re-applied, version read back). The package is copied LOCAL first: a SYSTEM task cannot
    # read the share. The script's own log is the record.
    import shutil
    local_dir = os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "HAI", "drivers")
    os.makedirs(local_dir, exist_ok=True)
    src = C["package"]
    local = os.path.join(local_dir, os.path.basename(src))
    if not (os.path.exists(local) and os.path.getsize(local) == os.path.getsize(src)):
        shutil.copyfile(src, local)
    ps1 = os.path.join(local_dir, "nvidia_driver_install.ps1")
    open(ps1, "w", encoding="utf-8-sig").write(C["script"])
    logp = os.path.join(local_dir, "install-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-Package", local, "-Log", logp]
    if C.get("lock_task"):
        args += ["-LockTask", C["lock_task"]]
    r = subprocess.run(args, capture_output=True, text=True, timeout=2400)
    text = (r.stdout + r.stderr)
    out["ok"] = r.returncode == 0 and "DRIVER INSTALL OK" in text
    out["tail"] = " | ".join(x.strip() for x in text.strip().splitlines()[-4:])[-300:]
    out["log"] = logp
elif a == "au3":
    k = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
    subprocess.run(["reg", "export", r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate", os.path.join(D, "wu-policy-before-au3.reg"), "/y"], capture_output=True)
    subprocess.run(["reg", "add", k, "/v", "NoAutoUpdate", "/t", "REG_DWORD", "/d", "0", "/f"], capture_output=True)
    subprocess.run(["reg", "add", k, "/v", "AUOptions", "/t", "REG_DWORD", "/d", "3", "/f"], capture_output=True)
    out["AUOptions"] = ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU').AUOptions")
print("@@" + json.dumps(out))
'''


REMOTE_DRIVER_DIR = os.path.join(HAI_DIR_ON_BOX, "drivers")     # = the remote side's local_dir


def _ssh_out(box, cmd, run=subprocess.run, timeout=120):
    r = run(["ssh", "-o", "ConnectTimeout=10", box, cmd], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip()


def push_package(box, path, run=subprocess.run, log=print):
    """Copy via ssh to avoid SMB permission failures (ssh sessions lack saved share credentials), and skip if the destination size matches the source."""
    name = os.path.basename(path)
    dest = REMOTE_DRIVER_DIR + "\\" + name
    want = os.path.getsize(path)
    size_cmd = ("powershell -NoProfile -Command \"New-Item -ItemType Directory -Force -Path '%s' | Out-Null; "
                "if (Test-Path '%s') { (Get-Item '%s').Length } else { 0 }\"" % (REMOTE_DRIVER_DIR, dest, dest))
    have = _ssh_out(box, size_cmd, run=run)
    if have.isdigit() and int(have) == want:
        log("package already on %s (%d bytes)" % (box, want))
        return dest
    log("pushing %s to %s (%d MB)" % (name, box, want // 1_000_000))
    r = run(["scp", "-q", "-o", "ConnectTimeout=10", path, "%s:%s" % (box, dest.replace("\\", "/"))],
            capture_output=True, text=True, timeout=3600)
    have = _ssh_out(box, size_cmd, run=run)
    if not (have.isdigit() and int(have) == want):
        raise RuntimeError("pushing %s to %s left %s bytes there, %d here (scp rc %s: %s)" % (
            name, box, have or "no", want, getattr(r, "returncode", "?"), (getattr(r, "stderr", "") or "")[-160:]))
    return dest


def remote(box, action, **kw):
    cfg = dict(kw, action=action, runner=BOXES[box]["runner"], state=BOXES[box]["state"])
    if action == "nvidia_install":
        cfg["script"] = open(os.path.join(HERE, "nvidia_driver_install.ps1"), encoding="utf-8").read()
        # the package lives on this box's disk; every other box gets its own copy pushed over ssh first (push_package)
        if box != THIS_BOX:
            cfg["package"] = push_package(box, cfg["package"])
    src = REMOTE.replace("__CFG__", base64.b64encode(json.dumps(cfg).encode("utf-8")).decode("ascii"))
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", box, "python -"], input=src.encode("utf-8"),
                       capture_output=True, timeout=300)
    hit = [x for x in r.stdout.decode("utf-8", "replace").splitlines() if x.startswith("@@")]
    if not hit:
        raise RuntimeError("%s %s: no answer (%s)" % (box, action, (r.stdout + r.stderr).decode("utf-8", "replace")[-300:]))
    return json.loads(hit[-1][2:])


# ---- the lab's other instruments (read-only) -----------------------------------------------------------------------------
def deck(box):
    with urllib.request.urlopen("%s/p20/factory/%s" % (DESK, box), timeout=15) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def ollama_ok(box):
    try:
        with urllib.request.urlopen("http://%s:%s/api/version" % (BOXES[box]["ip"], OLLAMA_PORT), timeout=10) as r:
            return json.loads(r.read().decode("utf-8")).get("version")
    except Exception:                                                              # noqa: BLE001
        return None


def gpu_report(box):
    """(healthy, age_s, detail) from the box's own pushed health verdict; (None, None, why) when it cannot tell."""
    try:
        d = json.load(open(os.path.join(GPU_REPORTS, box + ".json"), encoding="utf-8"))
        ts = datetime.datetime.fromisoformat(d["ts"])
        return d.get("healthy"), (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds(), d.get("detail")
    except Exception as e:                                                         # noqa: BLE001
        return None, None, "no report (%s)" % type(e).__name__


def hold(box, why, ttl=HOLD_TTL):
    os.environ.setdefault("HAI_STATE_DIR", DOCS_STATE)
    sys.path.insert(0, MIRRORS.get("orchestrator", ""))
    import scheduler
    return scheduler.claim(box, "gpu", scheduler.OPERATOR_HOLD + " #38 lab update stager: " + why, ttl=ttl)


def release(box):
    os.environ.setdefault("HAI_STATE_DIR", DOCS_STATE)
    sys.path.insert(0, MIRRORS.get("orchestrator", ""))
    import scheduler
    held = scheduler.active_claim(box, "gpu") if hasattr(scheduler, "active_claim") else None
    if held and "#38 lab update stager" not in str(held.get("holder") or ""):
        return False                      # someone else's hold (a person's operator hold) is never ours to lift
    return scheduler.release(box, "gpu")


def proven_kbs(exclude):
    """KBs Windows Update installed successfully (result 2) on the OTHER boxes, from their newest inventory readings."""
    kbs = set()
    for box in BOXES:
        if box == exclude:
            continue
        files = sorted(glob.glob(os.path.join(HERE, "update-inventory", box + "-*.json")))
        for f in reversed(files):
            d = json.load(open(f, encoding="utf-8"))
            if "could_not_ask" in d:
                continue
            for h in d.get("wu_history") or []:
                if h.get("result") == 2:
                    kbs.update(re.findall(r"KB(\d+)", h.get("title") or ""))
            break
    return sorted(kbs)


def pending(box):
    """Windows updates waiting on `box` per its newest inventory reading, Defender signatures left out."""
    files = sorted(glob.glob(os.path.join(HERE, "update-inventory", box + "-*.json")))
    for f in reversed(files):
        d = json.load(open(f, encoding="utf-8"))
        if "could_not_ask" not in d:
            return [u["title"] for u in d.get("wu_pending") or [] if not NOISE.search(u.get("title") or "")
                    and "Drivers" not in (u.get("categories") or "")]
    return []


# ---- one stage --------------------------------------------------------------------------------------------------------
class Ctx:
    """Everything a stage touches, injectable for tests."""
    remote = staticmethod(remote)
    deck = staticmethod(deck)
    ollama_ok = staticmethod(ollama_ok)
    gpu_report = staticmethod(gpu_report)
    hold = staticmethod(hold)
    release = staticmethod(release)
    proven_kbs = staticmethod(proven_kbs)
    sleep = staticmethod(time.sleep)
    now = staticmethod(time.time)
    plex_busy = staticmethod(lambda: False)
    hai_jobs_open = staticmethod(lambda: 0)
    suspend = staticmethod(lambda: ([], None))      # main() wires suspend_runs(kirk_call); a test sees nothing in flight
    save_inflight = staticmethod(lambda d: _write_json(INFLIGHT, d))
    clear_inflight = staticmethod(lambda: os.path.exists(INFLIGHT) and os.remove(INFLIGHT))
    poll_s = 30


def archive(component, version, klass, rollback, **kw):
    """Every stage records what it replaces BEFORE replacing it (lab_archive: one store, one manifest per generation). An
    archive that cannot be written is said in the stage record, never a reason to skip the stage."""
    try:
        import lab_archive
        # Read LAB_ARCHIVE_DIR at call time so tests can override it to a temp dir, preventing writes to the live store.
        return lab_archive.archive(component, version, klass, rollback, store=os.environ.get("LAB_ARCHIVE_DIR"), **kw)
    except Exception as e:                                                         # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _write_json(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(path + ".tmp", path)


def wait_for(ctx, box, test, limit_s, what, log):
    """Poll a STATUS until test(answer) is true; refresh the hold each round. -> (answer, True) or (last, False) when the
    status never got there within limit_s (the INCONCLUSIVE bound: the caller reports it, it never assumes success)."""
    t0, last = ctx.now(), None
    while ctx.now() - t0 < limit_s:
        try:
            last = test()
            if last:
                return last, True
        except Exception as e:                                                     # noqa: BLE001 - a missed answer: ask again
            last = "%s: %s" % (type(e).__name__, e)
        ctx.hold(box, what)
        ctx.sleep(ctx.poll_s)
    log("%s: still not there after %d s (%s)" % (what, limit_s, str(last)[:200]))
    return last, False


def stage(box, allow_reboot, ctx=Ctx, log=print, only=None):
    rec = {"box": box, "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "steps": [],
           "verdict": None}
    say = lambda m: (rec["steps"].append(m), log("%s: %s" % (box, m)))  # noqa: E731
    try:
        d = ctx.deck(box)
    except Exception as e:                                                         # noqa: BLE001
        rec["verdict"] = "WAIT - could not read the deck (%s)" % type(e).__name__
        return rec
    if d.get("order") or str(d.get("state") or "") not in ("free", "idle"):
        rec["verdict"] = "WAIT - the factory is busy (%s %s); another night" % (d.get("state"), d.get("order") or "")
        return rec
    if box in PROVEN_KBS_ONLY and only is None:
        only = ctx.proven_kbs(box)
    paused = False
    healthy = False
    try:
        ctx.hold(box, "installing Windows updates")
        say("desk hold placed")
        paused = True            # BEFORE the call: a pause that fails half-way (flag written, task disabled) must still be
        p = ctx.remote(box, "pause", safety_h=SAFETY_RESUME_H)
        say("factory pause: runner task enabled=%s, flag=%s, safety resume at %s" % (p.get("disable"), p.get("flag"),
                                                                                    p.get("safety")))
        _r, gone = wait_for(ctx, box, lambda: not ctx.remote(box, "runner").get("procs") or None, 1200,
                            "waiting for the runner to exit between orders", say)
        if not gone:
            rec["verdict"] = "WAIT - the runner did not exit within 20 min; nothing installed"
            return rec
        boot0 = ctx.remote(box, "poll").get("boot")
        ps1 = open(os.path.join(HERE, "wu_stage_box.ps1"), encoding="utf-8").read()
        ctx.remote(box, "start_wu", ps1=ps1, mode="install", only=",".join(only or []))
        say("Windows Update install started%s" % (" (canary: only %s)" % ",".join(only) if only else ""))
        res, done = wait_for(ctx, box, lambda: (lambda r: r if r and r.get("done") else None)(
            ctx.remote(box, "poll").get("result")), 3 * 3600, "installing", say)
        rec["result"] = res if isinstance(res, dict) else None
        if not done or not isinstance(res, dict):
            rec["verdict"] = "HOLD - the install did not report done; the box stays out of placement"
            return rec
        if res.get("phase") == "error":
            rec["verdict"] = "HOLD - the install failed: %s" % res.get("error")
            return rec
        n = len(res.get("found") or [])
        if n:
            kbs = ",".join(u.get("kb") or "?" for u in res.get("found") or [])
            archive("windows-update", kbs[:60] or "unnamed", "vendor", box=box, stage="windows-update",
                    note="installed: " + "; ".join((u.get("title") or "")[:70] for u in res.get("found") or []),
                    rollback="wusa /uninstall /kb:<n> /quiet /norestart  (on %s; one per KB: %s)" % (box, kbs))
        bad = [u for u in res.get("per_update") or [] if u.get("result") not in (2, 3)]
        say("installed %d update(s)%s; reboot required: %s; skipped %d" % (
            n, " (%d FAILED)" % len(bad) if bad else "", res.get("reboot_required"), len(res.get("skipped") or [])))
        if res.get("reboot_required"):
            if not allow_reboot:
                say("reboot NOT done (daytime run without --allow-reboot): it is pending")
            elif box == PLEX_BOX and ctx.plex_busy():
                say("reboot deferred: Plex is serving someone (Plex first)")
            else:
                suspended = []
                if box == PLEX_BOX:
                    # the Plex box is shared: suspend active runs, reboot only if the OS requires, resume via --resume-inflight.
                    suspended, swhy = ctx.suspend()
                    if suspended is None:
                        say("reboot deferred: %s" % swhy)
                if box != PLEX_BOX or suspended is not None:
                    if suspended:
                        say("%d HAI run(s) suspended mid-turn for the restart: %s" % (
                            len(suspended), ", ".join(s[:8] for s in suspended)))
                    ctx.save_inflight({"box": box, "boot0": boot0, "rec": rec, "suspended": suspended or []})
                    ctx.remote(box, "reboot")
                    say("restart issued (60 s warning)")
                    b, back = wait_for(ctx, box, lambda: (lambda x: x if x and x != boot0 else None)(
                        ctx.remote(box, "poll").get("boot")), 45 * 60, "waiting for the box to come back", say)
                    if not back:
                        rec["verdict"] = "HOLD - the box did not come back from its restart within 45 min"
                        return rec
                    say("back up: booted %s" % b)
        v, up = wait_for(ctx, box, lambda: ctx.ollama_ok(box), 15 * 60, "waiting for ollama", say)
        g, fresh = wait_for(ctx, box, lambda: (lambda h: h if h[0] is True and h[1] is not None and h[1] < 300 else None)(
            ctx.gpu_report(box)), 15 * 60, "waiting for a fresh healthy GPU report", say)
        healthy = bool(up and fresh and not bad)
        say("after: ollama %s, GPU %s" % (v if up else "NOT ANSWERING", g[2] if fresh else "NO FRESH HEALTHY REPORT"))
        rec["verdict"] = ("OK - %d update(s) installed, the box is healthy" % n) if healthy else \
            "HOLD - the after-checks failed; the box stays out of placement"
        return rec
    except Exception as e:                                                         # noqa: BLE001
        # the verdict is ONE line (it goes into the Discord field): the remote's ssh banner and traceback stay out
        msg = " ".join(str(e).split())
        msg = re.sub(r"\*\* .*?(?=Traceback|$)", "", msg)
        rec["verdict"] = "HOLD - the stager failed: %s: %s" % (type(e).__name__, msg[-160:])
        return rec
    finally:
        if paused:
            try:
                rr = ctx.remote(box, "resume")
                say("factory resumed: %s, runners %s, flag left: %s" % (rr.get("runner_task"), rr.get("procs"), rr.get("flag")))
            except Exception as e:                                                 # noqa: BLE001 - the safety task resumes it
                say("RESUME FAILED (%s) - the box's safety task resumes the factory by itself" % type(e).__name__)
        try:
            if healthy or (rec["verdict"] or "").startswith("WAIT"):
                ctx.release(box)
                say("desk hold released")
            else:
                ctx.hold(box, "after-checks failed - a person should look", ttl=FAIL_HOLD_TTL)
                say("desk hold kept %d h: %s" % (FAIL_HOLD_TTL // 3600, rec["verdict"]))
        except Exception as e:                                                     # noqa: BLE001
            say("hold bookkeeping failed (%s)" % type(e).__name__)
        ctx.clear_inflight()


def night_stage_lines(state=STATE):
    """Returns one line per stage record written since the latest 03:00 report."""
    reports = sorted(glob.glob(os.path.join(state, "report-*.json")))
    since = os.path.basename(reports[-1])[7:-5] if reports else ""
    lines = []
    paths = glob.glob(os.path.join(state, "stage-*-*.json"))
    for p in sorted(paths, key=lambda q: "-".join(os.path.basename(q)[:-5].rsplit("-", 2)[1:])):   # by time, not box
        stamp = os.path.basename(p)[:-5].rsplit("-", 2)
        if len(stamp) == 3 and "-".join(stamp[1:]) >= since:
            try:
                r = json.load(open(p, encoding="utf-8"))
                lines.append("🔧 the lab staged %s: %s" % (r["box"], r["verdict"]))
            except (OSError, ValueError, KeyError):
                continue
    return lines


def record(rec):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    _write_json(os.path.join(STATE, "stage-%s-%s.json" % (rec["box"], stamp)), rec)
    with open(os.path.join(STATE, "stager.log"), "a", encoding="utf-8") as f:
        f.write("%s %s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), rec["box"], rec["verdict"]))
    try:                                   # into the 4:30 field: the stager runs after the 03:00 notice
        import update_notify as N
        latest = sorted(glob.glob(os.path.join(STATE, "report-*.json")))[-1]
        rep = json.load(open(latest, encoding="utf-8"))
        text = N.field_text(os.path.basename(latest)[7:-5], rep["boxes"], rep.get("upstream") or [],
                            first=night_stage_lines())
        report = "asked %s\n%s\nEND\n" % (rep["asked"], text)
        with open(os.path.join(STATE, "report.txt.tmp"), "w", encoding="utf-8", newline="\n") as f:
            f.write(report)
        os.replace(os.path.join(STATE, "report.txt.tmp"), os.path.join(STATE, "report.txt"))
    except Exception:                                                              # noqa: BLE001 - the record is the truth
        pass


# APPS_ALLOW lists only tools that lack native updaters. Toolchains (Ollama, CUDA, etc.) and personal apps are excluded by design: they have self-updaters or are managed by separate systems.
APPS_ALLOW = [
    "Git.Git", "GitHub.cli", "PuTTY.PuTTY", "Microsoft.WindowsTerminal", "Microsoft.AppInstaller",   # winget itself
    "Tailscale.Tailscale", "Google.ChromeRemoteDesktopHost",          # the CRD backlog item: no updater of its own
    "CrystalDewWorld.CrystalDiskInfo", "Rem0o.FanControl", "Obsidian.Obsidian", "TheDocumentFoundation.LibreOffice",
    "Graphviz.Graphviz", "tesseract-ocr.tesseract", "Microsoft.WSL",
    "RaspberryPiFoundation.RaspberryPiImager", "Balena.Etcher", "SDAssociation.SDMemoryCardFormatter",
]
APPS_DENY = ["Ollama.Ollama", "Nvidia.CUDA", "Python.Launcher", "Microsoft.VCRedist.2013.x64", "Microsoft.VCRedist.2013.x86",
             "Microsoft.VCRedist.2015+.x64", "Microsoft.VCRedist.2015+.x86", "Microsoft.VisualStudio.2022.BuildTools",
             "OpenJS.NodeJS.LTS", "Microsoft.DotNet.Runtime.8", "Microsoft.DotNet.SDK.10", "Plex.PlexMediaServer",
             "namazso.PawnIO"]            # named so a test can assert none of them ever reaches ALLOW


# winget writes a DPAPI-encrypted setting on every run; under a public-key ssh logon the box's console account cannot unseal its
# master key (`Settings.cpp … 80070005 Access is denied` → 8A15000F, broken since 07-26). The documented answer to the
# wrong-logon-context class (RUNBOOK-Windows-Reachability-Traps, share_cred_by_file.py) is a task IN that user's own
# interactive session: `/ru <user> /it`, pythonw (no console on the desktop), the answer read back from a file.
APPS_VIA_TASK = S.setting_json("APPS_VIA_TASK", {})   # box -> {"pythonw": path}; the console user is read live
APPS_TASK_DIR = os.path.join(HAI_DIR_ON_BOX, "apps-stage")

TASK_DRIVER = r'''
import base64, json, os, re, subprocess, time
C = json.loads(base64.b64decode("__CFG__").decode("utf-8"))
D = os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "HAI", "apps-stage")
os.makedirs(D, exist_ok=True)
script, result = os.path.join(D, "apps_stage_box.py"), os.path.join(D, "result.json")
open(script, "w", encoding="utf-8").write(C["script"])
if os.path.exists(result):
    os.replace(result, result + ".prev")
out = {}
# the user AT THE CONSOLE, read live (it can differ from the account ssh lands as) - an Interactive
# principal runs only in that user's own session, which is the whole point, and needs no password to register
q = subprocess.run(["query", "user"], capture_output=True, text=True).stdout
m = re.search(r"^\s*>?\s*(\S+)\s+console\s+\d+\s+Active", q, re.M)
if not m:
    out["no_console_user"] = " | ".join(ln.strip() for ln in q.splitlines()[:4])
    print("@@" + json.dumps(out)); raise SystemExit(0)
user = m.group(1)
admins = subprocess.run(["net", "localgroup", "administrators"], capture_output=True, text=True).stdout.lower()
level = "Highest" if re.search(r"^\s*(%s|[^\\\n]+\\%s)\s*$" % (re.escape(user.lower()), re.escape(user.lower())), admins, re.M) else "Limited"
out["user"], out["run_level"] = user, level
tn = "HAI Apps Stage"
ps = ("$a = New-ScheduledTaskAction -Execute '%s' -Argument '\"%s\"'; "
      "$p = New-ScheduledTaskPrincipal -UserId '%s' -LogonType Interactive -RunLevel %s; "
      "$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2); "
      "Register-ScheduledTask -TaskName '%s' -Action $a -Principal $p -Settings $s -Force | Out-Null; "
      "Start-ScheduledTask -TaskName '%s'; 'started'" % (C["pythonw"], script, user, level, tn, tn))
c = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
out["create"] = (c.stdout + c.stderr).strip()[-200:]
if "started" not in c.stdout:
    print("@@" + json.dumps(out)); raise SystemExit(0)
t0 = time.time()
while time.time() - t0 < C["wait_s"]:                  # the task's STATUS: its result file, never a clock alone
    if os.path.exists(result):
        try:
            out["result"] = json.load(open(result, encoding="utf-8"))
            break
        except ValueError:
            pass
    q = subprocess.run(["schtasks", "/query", "/tn", tn, "/fo", "LIST"], capture_output=True, text=True).stdout
    if "Status:" in q and "Running" not in q and time.time() - t0 > 60:
        out["task_ended_without_result"] = [ln.strip() for ln in q.splitlines() if "Status" in ln or "Last Run Result" in ln]
        break
    time.sleep(10)
subprocess.run(["schtasks", "/delete", "/f", "/tn", tn], capture_output=True)
try:
    os.remove(script)
except OSError:
    pass
print("@@" + json.dumps(out))
'''


def apps_on_box(box, allow=APPS_ALLOW, store=True, run=subprocess.run):
    """Run apps_stage_box.py on `box` with the allow-list -> its JSON report. Over ssh, or (APPS_VIA_TASK) as a one-shot
    interactive task in the box user's own session, read back from its result file."""
    src = open(os.path.join(HERE, "apps_stage_box.py"), encoding="utf-8").read()
    if box in APPS_VIA_TASK:
        t = APPS_VIA_TASK[box]
        inner = src.replace("__CFG__", base64.b64encode(json.dumps(
            {"allow": allow, "store": store, "result": APPS_TASK_DIR + r"\result.json"}).encode()).decode("ascii"))
        src = TASK_DRIVER.replace("__CFG__", base64.b64encode(json.dumps(
            {"script": inner, "pythonw": t["pythonw"], "wait_s": 3000}).encode()).decode("ascii"))
    else:
        src = src.replace("__CFG__", base64.b64encode(json.dumps({"allow": allow, "store": store}).encode()).decode("ascii"))
    r = run(["ssh", "-o", "ConnectTimeout=10", box, "python -"], input=src.encode("utf-8"), capture_output=True,
            timeout=3600)
    hit = [x for x in r.stdout.decode("utf-8", "replace").splitlines() if x.startswith("@@")]
    if not hit:
        raise RuntimeError("%s apps: no answer (%s)" % (box, (r.stdout + r.stderr).decode("utf-8", "replace")[-300:]))
    rep = json.loads(hit[-1][2:])
    if box in APPS_VIA_TASK:
        if "result" not in rep:
            raise RuntimeError("%s apps: the interactive task left no result (%s) - nobody at the console, or the task "
                               "did not start" % (box, rep.get("task_ended_without_result") or rep.get("no_console_user")
                                                  or rep.get("create")))
        rep["result"]["task"] = {"user": rep.get("user"), "run_level": rep.get("run_level")}   # who actually ran it
        rep = rep["result"]
    return rep


def stage_apps(box, ctx=Ctx, log=print, apps=apps_on_box):
    """App updates on one Windows box, in the window: deck idle -> desk hold -> factory pause -> the allow-listed winget
    upgrades + the Store's update scan -> resume -> release. No reboot (none of these needs one); the after-check is the
    report itself: a failed install is said and holds nothing (the box is unchanged for that app)."""
    rec = {"box": box, "class": "apps", "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "steps": []}
    say = lambda m: (rec["steps"].append(m), log("%s apps: %s" % (box, m)))  # noqa: E731
    try:
        d = ctx.deck(box)
    except Exception as e:                                                         # noqa: BLE001
        rec["verdict"] = "WAIT - could not read the deck (%s)" % type(e).__name__
        return rec
    if d.get("order") or str(d.get("state") or "") not in ("free", "idle"):
        rec["verdict"] = "WAIT - the factory is busy (%s %s); another night" % (d.get("state"), d.get("order") or "")
        return rec
    paused = False
    try:
        ctx.hold(box, "installing app updates")
        paused = True
        p = ctx.remote(box, "pause", safety_h=SAFETY_RESUME_H)
        say("desk hold placed; factory pause (runner task enabled=%s)" % p.get("disable"))
        _r, gone = wait_for(ctx, box, lambda: not ctx.remote(box, "runner").get("procs") or None, 1200,
                            "waiting for the runner to exit between orders", say)
        if not gone:
            rec["verdict"] = "WAIT - the runner did not exit within 20 min; nothing installed"
            return rec
        rep = apps(box)
        rec["result"] = rep
        for x in rep.get("installed") or []:              # "Id old -> new[ (note)]": the archive row is the rollback command
            m = re.match(r"(\S+) (\S+) -> (\S+)", x)
            if m:
                archive("app/" + m.group(1), m.group(2), "vendor", box=box, stage="apps",
                        rollback="winget install --id %s --version %s --exact --silent --force   (on %s)" % (m.group(1), m.group(2), box),
                        note="replaced by %s" % m.group(3))
        if not rep.get("source_ok", True):
            say("winget's source did not answer on this box (known broken on BigHonker since 07-26)")
        say("installed %d, failed %d, not in the lab's list %d; Store scan: %s" % (
            len(rep["installed"]), len(rep["failed"]), len(rep["skipped"]), rep.get("store")))
        for x in rep["installed"]:
            say("  installed " + x)
        for x in rep["failed"]:
            say("  FAILED " + x)
        rec["verdict"] = "OK - %d app update(s) installed%s" % (
            len(rep["installed"]), "; %d FAILED (said, nothing held)" % len(rep["failed"]) if rep["failed"] else "")
        return rec
    except Exception as e:                                                         # noqa: BLE001
        msg = re.sub(r"\*\* .*?(?=Traceback|$)", "", " ".join(str(e).split()))
        rec["verdict"] = "HOLD - the app stage failed: %s: %s" % (type(e).__name__, msg[-160:])
        return rec
    finally:
        if paused:
            try:
                rr = ctx.remote(box, "resume")
                say("factory resumed: %s" % rr.get("runner_task"))
            except Exception as e:                                                 # noqa: BLE001
                say("RESUME FAILED (%s) - the box's safety task resumes the factory by itself" % type(e).__name__)
            try:
                ctx.release(box)
                say("desk hold released")
            except Exception as e:                                                 # noqa: BLE001
                say("hold release failed (%s)" % type(e).__name__)


# PLEX on Softserve
# Two rules: only upgrade releases at least PLEX_MIN_AGE_DAYS old (avoid broken builds) and never while active (watching).
# PlexWatchdog must be PAUSED before install (it would relaunch Plex mid-upgrade) and RE-ENABLED after to restore Plex.
PLEX_FEED = "https://plex.tv/api/downloads/5.json"
PLEX_MIN_AGE_DAYS = 7
PLEX_BOX = S.setting("PLEX_BOX", "")      # the box that serves Plex (its reboots wait for viewers); empty = none
PLEX_ID = "Plex.PlexMediaServer"


def plex_feed(fetch=None):
    """Plex's own download feed -> (version, release datetime UTC) for Windows, or (None, None) when it cannot be read."""
    try:
        if fetch is None:
            with urllib.request.urlopen(PLEX_FEED, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
        else:
            d = fetch()
        w = d["computer"]["Windows"]
        return w["version"], datetime.datetime.fromtimestamp(int(w["release_date"]), datetime.timezone.utc)
    except Exception:                                                              # noqa: BLE001
        return None, None


def plex_decision(installed, feed_version, released, winget_available, now=None, min_age_days=PLEX_MIN_AGE_DAYS):
    """-> (target version or None, why). Install only when the feed's build is newer than what runs, at least
    min_age_days old, and the SAME build winget offers (two sources must agree on what we are about to install)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not feed_version or not released:
        return None, "WAIT - Plex's feed could not be read"
    base = lambda v: str(v or "").split("-")[0]  # noqa: E731  "1.43.5.11079-b3b13e506" -> "1.43.5.11079"
    if base(feed_version) == base(installed):
        return None, "OK - Plex %s is current" % base(installed)
    age = (now - released).total_seconds() / 86400
    if age < min_age_days:
        return None, "WAIT - Plex %s is %.1f days old; the lab installs releases %d+ days old" % (
            base(feed_version), age, min_age_days)
    if base(winget_available) != base(feed_version):
        return None, "WAIT - Plex's feed says %s but winget offers %s; the two must agree" % (
            base(feed_version), base(winget_available) or "nothing")
    return base(feed_version), "install %s (released %.0f days ago)" % (base(feed_version), age)


def plex_streams():
    """Tautulli's live stream count via the live orchestrator's own plex.activity() (any stream, not only transcodes);
    None when it could not be asked (the stage then waits: fail closed)."""
    try:
        r = subprocess.run(["ssh", HEAD, "docker exec orchestrator python -c \"import plex,json; a=plex.activity(); "
                            "print(json.dumps(a and a.get('streams')))\""], capture_output=True, text=True, timeout=60)
        v = json.loads(r.stdout.strip().splitlines()[-1])
        return None if v is None else int(v)
    except Exception:                                                              # noqa: BLE001
        return None


PLEX_BOX_SCRIPT = r'''
import base64, json, re, subprocess, time, urllib.request
C = json.loads(base64.b64decode("__CFG__").decode("utf-8"))
NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
out = {"action": C["action"]}
def identity_version(xml):
    # the MediaContainer's version, never the XML header's version="1.0"
    m = re.search(r'<MediaContainer[^>]*\sversion="([^"]+)"', xml)
    return m.group(1) if m else "?"
def identity():
    try:
        with urllib.request.urlopen("http://127.0.0.1:32400/identity", timeout=5) as r:
            return identity_version(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
def table(text):
    # = apps_stage_box.table (one derivation, pinned equal by test_update_stage.PlexStage)
    lines = [ln for ln in text.replace("\r", "\n").splitlines() if ln.strip()]
    hdr = next((i for i, ln in enumerate(lines) if re.search(r"Name\s+Id\s+Version\s+Available", ln)), None)
    if hdr is None:
        return {}
    h = lines[hdr][lines[hdr].index("Name"):]
    cols = [h.index(c) for c in ("Id", "Version", "Available", "Source")]
    out = {}
    for ln in lines[hdr + 1:]:
        if set(ln.strip()) <= {"-"} or re.match(r"^\d+ upgrades? available", ln) or len(ln) < cols[3]:
            continue
        pid = ln[cols[0]:cols[1]].strip()
        if pid:
            out[pid] = (ln[cols[1]:cols[2]].strip(), ln[cols[2]:cols[3]].strip())
    return out
def ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, **NO_WINDOW)
    return (r.stdout + r.stderr).strip()
if C["action"] == "probe":
    out["identity"] = identity()
    w = subprocess.run(["winget", "upgrade", "--disable-interactivity", "--accept-source-agreements"],
                       capture_output=True, **NO_WINDOW)
    row = table(w.stdout.decode("utf-8", "replace")).get(C["id"])
    out["winget_installed"], out["winget_available"] = row if row else (None, None)
    out["watchdog"] = ps("[string](Get-ScheduledTask -TaskName PlexWatchdog).State")
elif C["action"] == "install":
    out["watchdog_off"] = ps("Disable-ScheduledTask -TaskName PlexWatchdog | Out-Null; [string](Get-ScheduledTask -TaskName PlexWatchdog).State")
    args = ["winget", "install" if C.get("version") else "upgrade", "--id", C["id"], "--exact", "--silent",
            "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity"]
    if C.get("version"):
        args += ["--version", C["version"], "--force"]         # a rollback: the exact earlier build, over the newer one
    r = subprocess.run(args, capture_output=True, timeout=1800, **NO_WINDOW)
    out["rc"] = r.returncode & 0xFFFFFFFF
    out["tail"] = " ".join(r.stdout.decode("utf-8", "replace").split())[-200:]
    out["watchdog_on"] = ps("Enable-ScheduledTask -TaskName PlexWatchdog | Out-Null; Start-ScheduledTask -TaskName PlexWatchdog; [string](Get-ScheduledTask -TaskName PlexWatchdog).State")
    t0 = time.time()
    while time.time() - t0 < 300:                 # the watchdog relaunches Plex within a minute; read its STATUS
        v = identity()
        if v:
            out["identity"] = v
            break
        time.sleep(10)
    else:
        out["identity"] = None
print("@@" + json.dumps(out))
'''


def plex_on_box(action, run=subprocess.run, **kw):
    cfg = dict(kw, action=action, id=PLEX_ID)
    src = PLEX_BOX_SCRIPT.replace("__CFG__", base64.b64encode(json.dumps(cfg).encode()).decode("ascii"))
    r = run(["ssh", "-o", "ConnectTimeout=10", PLEX_BOX, "python -"], input=src.encode("utf-8"), capture_output=True,
            timeout=2400)
    hit = [x for x in r.stdout.decode("utf-8", "replace").splitlines() if x.startswith("@@")]
    if not hit:
        raise RuntimeError("plex %s: no answer (%s)" % (action, (r.stdout + r.stderr).decode("utf-8", "replace")[-300:]))
    return json.loads(hit[-1][2:])


def stage_plex(ctx=Ctx, log=print, feed=plex_feed, streams=plex_streams, box=plex_on_box, dry=False):
    """Plex on Softserve, in the window: feed + winget agree on a build >= 7 days old -> nobody streaming (Tautulli, any
    stream; could-not-ask = wait) -> desk hold -> watchdog paused -> winget upgrade -> watchdog back (it relaunches Plex)
    -> /identity shows the new build. Not the new build -> roll back to the exact old one, and HOLD for a human."""
    rec = {"box": PLEX_BOX, "class": "plex", "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "steps": []}
    say = lambda m: (rec["steps"].append(m), log("plex: " + m))  # noqa: E731
    p = box("probe")
    installed = (p.get("identity") or "").split("-")[0] or p.get("winget_installed")
    fv, released = feed()
    target, why = plex_decision(installed, fv, released, p.get("winget_available"))
    say("installed %s; feed %s (%s); winget offers %s -> %s" % (installed, fv, released.date() if released else "?",
                                                              p.get("winget_available"), why))
    if not target:
        rec["verdict"] = why
        return rec
    if dry:
        rec["verdict"] = "DRY - would install %s" % target
        return rec
    n = streams()
    if n is None or n > 0:
        rec["verdict"] = "WAIT - %s; another night" % ("Tautulli could not be asked" if n is None else "%d Plex stream(s) active" % n)
        return rec
    held = False
    try:
        ctx.hold(PLEX_BOX, "installing Plex %s" % target, ttl=1800)
        held = True
        n = streams()
        if n is None or n > 0:
            rec["verdict"] = "WAIT - a stream started (%s); another night" % n
            return rec
        archive("plex-media-server", installed, "vendor", box=PLEX_BOX, stage="plex", note="replaced by %s" % target,
                rollback="winget install --id %s --version %s --exact --silent --force   (on softserve; PlexWatchdog paused)" % (PLEX_ID, installed))
        r = box("install")
        say("winget rc 0x%08X; watchdog %s -> %s; Plex back as %s" % (r.get("rc", 0), r.get("watchdog_off"), r.get("watchdog_on"), r.get("identity")))
        got = (r.get("identity") or "").split("-")[0]
        if r.get("rc") == 0 and got == target:
            rec["verdict"] = "OK - Plex %s -> %s, back and answering" % (installed, target)
            return rec
        say("not the expected build after install (%s); rolling back to %s" % (r.get("identity"), installed))
        rb = box("install", version=installed)
        back = (rb.get("identity") or "").split("-")[0]
        rec["verdict"] = "HOLD - Plex %s install %s (rc 0x%08X, now %s); rollback to %s %s" % (
            target, "did not take" if r.get("rc") == 0 else "failed", r.get("rc", 0), r.get("identity"), installed,
            "restored" if back == installed else "FAILED (%s) - a person should look" % rb.get("identity"))
        return rec
    except Exception as e:                                                         # noqa: BLE001
        rec["verdict"] = "HOLD - the Plex stage failed: %s: %s" % (type(e).__name__, " ".join(str(e).split())[-160:])
        return rec
    finally:
        if held:
            try:
                ctx.release(PLEX_BOX)
            except Exception:                                                      # noqa: BLE001
                pass


# NVIDIA driver updates for BigHonker and Softserve are safe to automate via the documented procedure, but the stage must strictly enforce the pre-checks below. A valid WHQL release must be 7+ days old, and a verified rollback package must be present before attempting installation (no rollback, no install). Softserve is strictly sequenced after BigHonker has successfully updated and validated to the same version (canary). AMD drivers (Mothership) are excluded from automation because the installer can deadlock in the Acronis filter; the stage only reports inventory status and the reason for non-action.
NVIDIA_LOOKUP = ("https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php?"
                 "func=DriverManualLookup&psid=127&pfid=%s&osID=135&languageCode=1033&beta=0&isWHQL=1&dltype=-1&dch=1"
                 "&upCRD=0&qnf=0&sort1=0&numberOfResults=4")
NVIDIA_PFID = S.setting_json("NVIDIA_PFID", {})                 # box -> NVIDIA product-family id of its card (psid 127 series)
NVIDIA_PKG_URL = "https://us.download.nvidia.com/Windows/%s/%s-desktop-win10-win11-64bit-international-dch-whql.exe"
DRIVER_MIN_AGE_DAYS = 7
DRIVER_DIR = os.path.join(LAB_ROOT, "downloads")                      # one copy serves both cards; the last two versions are kept
# Which boxes get a driver stage, in order. A setting like ORDER: unset means no box is staged.
DRIVER_ORDER = S.setting_json("DRIVER_ORDER", [])
POST_INSTALL_TASK = S.setting_json("POST_INSTALL_TASK", {})     # box -> the scheduled task re-run after a driver install


def nvidia_feed(box, fetch=None):
    """NVIDIA's own lookup for the box's card -> [(version, release date UTC, url)], newest first; [] if unreadable."""
    try:
        if fetch is None:
            with urllib.request.urlopen(NVIDIA_LOOKUP % NVIDIA_PFID[box], timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
        else:
            d = fetch(box)
        out = []
        for x in d.get("IDS") or []:
            i = x["downloadInfo"]
            if str(i.get("IsBeta")) not in ("0", "False", "None", ""):
                continue
            rel = datetime.datetime.strptime(i["ReleaseDateTime"], "%a %b %d, %Y").replace(tzinfo=datetime.timezone.utc)
            out.append((i["Version"], rel, urllib.parse.unquote(i["DownloadURL"])))
        return sorted(out, key=lambda t: [int(p) for p in t[0].split(".")], reverse=True)
    except Exception:                                                              # noqa: BLE001
        return []


def driver_decision(installed, feed, now=None, min_age_days=DRIVER_MIN_AGE_DAYS):
    """-> (target version, url, why) or (None, None, why): the newest WHQL release at least min_age_days old that is
    newer than what runs."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not feed:
        return None, None, "WAIT - NVIDIA's lookup could not be read"
    vt = lambda v: [int(p) for p in str(v).split(".")]  # noqa: E731
    for ver, rel, url in feed:
        age = (now - rel).total_seconds() / 86400
        if vt(ver) <= vt(installed):
            return None, None, "OK - driver %s is current (newest %s)" % (installed, feed[0][0])
        if age >= min_age_days:
            return ver, url, "install %s (released %.0f days ago; running %s)" % (ver, age, installed)
    return None, None, "WAIT - %s is %.1f days old; the lab installs releases %d+ days old (running %s)" % (
        feed[0][0], (now - feed[0][1]).total_seconds() / 86400, min_age_days, installed)


def fetch_package(version, url=None, log=print):
    """Download NVIDIA's package once into DRIVER_DIR\\nvidia-<version>\\ (verified by Content-Length) -> local path. Also
    the smart-archiving rule: the folders of the last two versions stay (the one we roll back to is kept until the new one
    passes); older ones are removed."""
    url = url or NVIDIA_PKG_URL % (version, version)
    d = os.path.join(DRIVER_DIR, "nvidia-" + version)
    p = os.path.join(d, url.rsplit("/", 1)[-1])
    os.makedirs(d, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as h:
        want = int(h.headers.get("Content-Length") or 0)
    if not (os.path.exists(p) and os.path.getsize(p) == want and want > 0):
        log("downloading %s (%d MB)" % (os.path.basename(p), want // 1_000_000))
        with urllib.request.urlopen(url, timeout=120) as r, open(p + ".part", "wb") as f:
            while True:
                b = r.read(1 << 22)
                if not b:
                    break
                f.write(b)
        if os.path.getsize(p + ".part") != want:
            raise RuntimeError("download of %s is %d bytes, header said %d" % (version, os.path.getsize(p + ".part"), want))
        os.replace(p + ".part", p)
    keep = sorted((x for x in os.listdir(DRIVER_DIR) if x.startswith("nvidia-")),
                  key=lambda x: [int(q) for q in x[7:].split(".")])[-2:]
    for x in os.listdir(DRIVER_DIR):
        if x.startswith("nvidia-") and x not in keep:
            import shutil
            shutil.rmtree(os.path.join(DRIVER_DIR, x), ignore_errors=True)
    return p


def console_idle(box, run=subprocess.run):
    """(True, why) when nobody is using the box's desktop: no Active console session, or its idle time is 30+ min."""
    r = run(["ssh", "-o", "ConnectTimeout=10", box, "query user"], capture_output=True, text=True, timeout=60)
    for ln in r.stdout.splitlines():
        m = re.match(r"^\s*>?\s*(\S+)\s+console\s+\d+\s+Active\s+(\S+)", ln)
        if m:
            idle = m.group(2)
            if idle in ("none", "."):
                return False, "%s is active at the console" % m.group(1)
            mins = sum(int(a) * b for a, b in zip(reversed(idle.split(":")), (1, 60, 1440))) if ":" in idle else int(idle or 0)
            return (mins >= 30, "%s at the console, idle %s" % (m.group(1), idle))
    return True, "no active console session"


def canary_ok(box, version, state=STATE):
    """Softserve takes a driver only on a night after BigHonker ran that same version OK (its stage record says so)."""
    if box == DRIVER_ORDER[0]:
        return True, "canary box"
    for p in sorted(glob.glob(os.path.join(state, "stage-%s-*.json" % DRIVER_ORDER[0])), reverse=True):
        try:
            r = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("class") == "driver" and r.get("target") == version and str(r.get("verdict", "")).startswith("OK"):
            return True, "BigHonker ran %s OK on %s" % (version, r.get("started", "")[:10])
    return False, "BigHonker has not run %s yet" % version


def gen_rate(box, run=subprocess.run):
    """One short generation on the box's ollama -> (model, tok/s) or (None, None): the first-token-after-driver check."""
    try:
        ip = BOXES[box]["ip"]
        with urllib.request.urlopen("http://%s:%s/api/tags" % (ip, OLLAMA_PORT), timeout=10) as r:
            models = [m["name"] for m in json.loads(r.read().decode())["models"]]
        model = next((m for m in models if m.startswith("qwen3")), models[0] if models else None)
        if not model:
            return None, None
        body = json.dumps({"model": model, "prompt": "Count from one to twenty, words only.", "stream": False,
                           "options": {"num_predict": 64}}).encode()
        req = urllib.request.Request("http://%s:%s/api/generate" % (ip, OLLAMA_PORT), data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read().decode())
        return model, round(d["eval_count"] / (d["eval_duration"] / 1e9), 1)
    except Exception:                                                              # noqa: BLE001
        return None, None


def stage_nvidia(box, ctx=Ctx, log=print, feed=nvidia_feed, idle=console_idle, package=fetch_package, dry=False):
    rec = {"box": box, "class": "driver", "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "steps": []}
    say = lambda m: (rec["steps"].append(m), log("%s driver: %s" % (box, m)))  # noqa: E731
    r = ctx.remote(box, "nvsmi")
    installed = r.get("driver")
    if not installed:
        rec["verdict"] = "WAIT - nvidia-smi gave no driver version (%s)" % r.get("error")
        return rec
    target, url, why = driver_decision(installed, feed(box))
    say("running %s -> %s" % (installed, why))
    rec["target"] = target
    if not target:
        rec["verdict"] = why
        return rec
    ok, cwhy = canary_ok(box, target)
    if not ok:
        rec["verdict"] = "WAIT - %s; another night" % cwhy
        return rec
    try:
        d = ctx.deck(box)
    except Exception as e:                                                         # noqa: BLE001
        rec["verdict"] = "WAIT - could not read the deck (%s)" % type(e).__name__
        return rec
    if d.get("order") or str(d.get("state") or "") not in ("free", "idle"):
        rec["verdict"] = "WAIT - the factory is busy (%s %s); another night" % (d.get("state"), d.get("order") or "")
        return rec
    free, iwhy = idle(box)
    if not free:
        rec["verdict"] = "WAIT - %s; another night" % iwhy
        return rec
    if box == PLEX_BOX and ctx.plex_busy():
        rec["verdict"] = "WAIT - Plex is serving someone; another night"
        return rec
    if dry:
        rec["verdict"] = "DRY - would install %s over %s (%s; %s)" % (target, installed, cwhy, iwhy)
        return rec
    paused = False
    healthy = False
    untouched = False          # a failure that left the driver as it was: released, not held
    try:
        ctx.hold(box, "installing NVIDIA driver %s" % target)
        paused = True
        p = ctx.remote(box, "pause", safety_h=SAFETY_RESUME_H)
        say("desk hold; factory pause (runner task enabled=%s)" % p.get("disable"))
        _r, gone = wait_for(ctx, box, lambda: not ctx.remote(box, "runner").get("procs") or None, 1200,
                            "waiting for the runner to exit between orders", say)
        if not gone:
            rec["verdict"] = "WAIT - the runner did not exit within 20 min; nothing installed"
            return rec
        rollback = package(installed, log=say)            # the rollback package FIRST: none served = no install
        new = package(target, url, log=say)
        say("packages ready: %s (rollback) and %s" % (os.path.basename(rollback), os.path.basename(new)))
        a = archive("nvidia-driver", installed, "vendor", box=box, stage="driver", location=rollback,
                    rollback="python update_stage.py --driver-rollback %s  (runs nvidia_driver_install.ps1 with %s)" % (box, rollback),
                    note="replaced by %s" % target)
        say("archived: %s" % (a.get("dir") or a.get("error") or "%s/%s" % (a["component"], a["stamp"])))
        model0, rate0 = gen_rate(box)
        say("before: %s %s tok/s" % (model0, rate0))
        res = ctx.remote(box, "nvidia_install", package=new, lock_task=POST_INSTALL_TASK.get(box, ""))
        rec["install"] = res
        say("install: %s" % (res.get("tail") or res))
        v, up = wait_for(ctx, box, lambda: ctx.ollama_ok(box), 15 * 60, "waiting for ollama", say)
        g, fresh = wait_for(ctx, box, lambda: (lambda h: h if h[0] is True and h[1] is not None and h[1] < 300 else None)(
            ctx.gpu_report(box)), 15 * 60, "waiting for a fresh healthy GPU report", say)
        now = ctx.remote(box, "nvsmi").get("driver")
        model1, rate1 = gen_rate(box)
        say("after: driver %s, ollama %s, GPU %s, %s %s tok/s (before %s)" % (
            now, v if up else "NOT ANSWERING", g[2] if fresh else "NO FRESH HEALTHY REPORT", model1, rate1, rate0))
        slow = bool(rate0 and rate1 and rate1 < 0.8 * rate0)
        healthy = bool(up and fresh and now == target and res.get("ok") and not slow)
        if healthy:
            rec["verdict"] = "OK - NVIDIA %s -> %s on %s, card healthy, %s %s tok/s" % (installed, target, box, model1, rate1)
            return rec
        say("not healthy after %s (driver now %s, ok %s, slow %s); rolling back to %s" % (target, now, res.get("ok"), slow, installed))
        rb = ctx.remote(box, "nvidia_install", package=rollback, lock_task=POST_INSTALL_TASK.get(box, ""))
        back = ctx.remote(box, "nvsmi").get("driver")
        rec["verdict"] = "HOLD - NVIDIA %s on %s failed its after-checks; rollback to %s %s" % (
            target, box, installed, "restored" if back == installed else "FAILED (now %s) - a person should look" % back)
        return rec
    except Exception as e:                                                         # noqa: BLE001
        err = "%s: %s" % (type(e).__name__, " ".join(str(e).split())[-160:])
        # Distinguish 'unchanged' (safe to WAIT) from 'changed' (must HOLD). A generic failure here implies a state change unless verified otherwise.
        try:
            now = ctx.remote(box, "nvsmi").get("driver")
        except Exception:                                                          # noqa: BLE001
            now = None
        if now and now == installed:
            untouched = True
            rec["verdict"] = "WAIT - the driver stage failed before the driver changed (still %s): %s; another night" % (
                installed, err)
        else:
            rec["verdict"] = "HOLD - the driver stage failed with the driver at %s (was %s): %s" % (
                now or "UNREADABLE", installed, err)
        return rec
    finally:
        if paused:
            try:
                rr = ctx.remote(box, "resume")
                say("factory resumed: %s" % rr.get("runner_task"))
            except Exception as e:                                                 # noqa: BLE001
                say("RESUME FAILED (%s) - the box's safety task resumes the factory by itself" % type(e).__name__)
            try:
                if healthy or untouched:
                    ctx.release(box)
                    say("desk hold released" + ("" if healthy else " (the box is unchanged)"))
                else:
                    ctx.hold(box, "%s - a person should look" % str(rec.get("verdict") or "driver stage")[:140],
                             ttl=FAIL_HOLD_TTL)
                    say("desk hold kept %d h" % (FAIL_HOLD_TTL // 3600))
            except Exception as e:                                                 # noqa: BLE001
                say("hold bookkeeping failed (%s)" % type(e).__name__)


def stage_amd_report():
    """The AMD box's driver (AMD_DRIVER_BOX): reported, never installed by the lab (see the block comment above)."""
    rec = {"box": AMD_DRIVER_BOX, "class": "driver", "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "steps": []}
    if not AMD_DRIVER_BOX:
        rec["verdict"] = "REPORT - no AMD box is set (AMD_DRIVER_BOX)"
        return rec
    try:
        h = json.load(open(os.path.join(GPU_REPORTS, AMD_DRIVER_BOX + ".json"), encoding="utf-8")).get("detail", "")
    except Exception:                                                              # noqa: BLE001
        h = "no health report"
    rec["verdict"] = ("REPORT - %s; AMD drivers stay a human install (the AMD installer's restore-point snapshot can deadlock "
                      "in Acronis's filter until its exclusions are entered at the console)" % h)
    return rec


# ---- the Linux boxes: Sundae (the arr stack + the desk) and rigby (Tolkien) ------------------------------------------
LINUX_DIR = os.path.join(HERE, "linux_stage")


def kv(text):
    out = {}
    for ln in (text or "").splitlines():
        k, sep, v = ln.partition("=")
        if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", k.strip()):   # CONTAINER_tablet-foundry has a dash
            out[k.strip()] = v.strip()
    return out


def linux_host(role):
    """The box a Linux stage runs on: its scripts are linux_stage/<role>_*.sh (the lane names: "sundae" is the head's
    stage, "rigby" the tablet head's), its box is a setting. '' = this lab has no such box."""
    return {"sundae": HEAD, "rigby": TABLET_HEAD}.get(role, "")


def linux_check(box):
    """The read-only before/after reading (linux_stage/<box>_check.sh, `box` the stage's role) -> {KEY: value}; {} when
    the box did not answer or this lab has none."""
    host = linux_host(box)
    if not host:
        return {}
    script = open(os.path.join(LINUX_DIR, "%s_check.sh" % box), "rb").read()
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, "bash -s"], input=script,
                       capture_output=True, timeout=120)
    out = r.stdout.decode("utf-8", "replace")
    return kv(out) if "DONE" in out else {}


def rigby_root(script, args="", wait=1800):
    """A root script on the tablet head through the lab's door (run_remote.py: sudo's password from the locker over
    stdin)."""
    if not TABLET_HEAD:
        raise RuntimeError("rigby %s %s: TABLET_HEAD_NAME unset (no tablet head)" % (script, args))
    cmd = [sys.executable, os.path.join(HERE, "run_remote.py"), TABLET_HEAD, os.path.join(LINUX_DIR, script), str(wait)]
    if args:
        cmd += ["--args", args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=wait + 120)
    if "VERDICT: script reached its DONE marker" not in r.stdout:
        raise RuntimeError("rigby %s %s: no DONE marker (%s)" % (script, args, r.stdout[-300:]))
    return kv(r.stdout)


def tolkien_busy():
    """Tolkien's running orders, from its own claim files (fleet_deploy_tolkien._current_orders, the deploy door's
    reader); unreadable -> busy (fail closed)."""
    try:
        sys.path.insert(0, MIRRORS.get("foundry", ""))
        import fleet_deploy_tolkien
        return fleet_deploy_tolkien._current_orders()
    except Exception as e:                                                         # noqa: BLE001
        return ["could not read Tolkien's claims (%s)" % type(e).__name__]


def dns_ok():
    """The house's DNS server (macvlan, unreachable FROM the head itself) answering, asked from
    another box. The address is the PIHOLE_ADDRESS setting; unset means DNS is not checked."""
    pi_hole = S.setting("PIHOLE_ADDRESS", "")
    if not pi_hole:
        return True
    r = subprocess.run(["nslookup", "example.com", pi_hole], capture_output=True, text=True, timeout=20)
    return "Address" in r.stdout and r.stdout.count("Address") >= 2


def rigby_ok(before, after, kernel=None):
    """-> list of what is wrong after a rigby reboot ([] = healthy), compared with the reading before."""
    bad = []
    if kernel and after.get("KERNEL") != kernel:
        bad.append("booted %s, not %s" % (after.get("KERNEL"), kernel))
    if after.get("GPU_COUNT") != before.get("GPU_COUNT") or "470." not in after.get("GPUS", ""):
        bad.append("GPUs %s (before %s)" % (after.get("GPUS"), before.get("GPUS")))
    if int(after.get("ENX") or 0) < int(before.get("ENX") or 0):     # every tablet is meant to be on USB
        bad.append("tablet USB links %s (before %s)" % (after.get("ENX"), before.get("ENX")))
    if int(after.get("NODES_HEALTHY") or 0) < int(before.get("NODES_HEALTHY") or 0):     # Tolkien's own measure
        bad.append("Fellowship %s/%s healthy (before %s/%s)" % (after.get("NODES_HEALTHY"), after.get("NODES_TOTAL"),
                                                               before.get("NODES_HEALTHY"), before.get("NODES_TOTAL")))
    for c in ("tablet-foundry", "tablet-watchdog", "tolkien-telemetry"):
        if after.get("CONTAINER_" + c) != "running":
            bad.append("%s %s" % (c, after.get("CONTAINER_" + c)))
    if int(after.get("ORDER_TAKER_UP") or 0) < 1:
        bad.append("Tolkien's order taker not up")
    if after.get("BRIDGE") != "active":
        bad.append("fellowship-bridge %s" % after.get("BRIDGE"))
    return bad


def wait_boot(box, boot0, limit_s=1500, check=None, sleep=time.sleep):
    """Poll the box's own reading until its boot time moves (the proof it restarted) -> the new reading or None."""
    check = check or linux_check
    t0 = time.time()
    sleep(90)
    while time.time() - t0 < limit_s:
        try:
            d = check(box)
            if d.get("BOOT") and d["BOOT"] != boot0:
                sleep(60)                     # let the services settle, then read again
                return check(box)
        except Exception:                                                          # noqa: BLE001
            pass
        sleep(30)
    return None


def tolkien_idle(limit_s=25 * 60, sleep=time.sleep, busy=None):
    """Wait until Tolkien is idle (no active runs) or timeout. Do not return True on timeout (False), as upstream reviews may still be running."""
    busy = busy or tolkien_busy
    t0 = time.time()
    while time.time() - t0 < limit_s:
        if not busy():
            return True
        sleep(60)
    return False


def stage_rigby(log=print):
    rec = {"box": "rigby", "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "steps": []}
    say = lambda m: (rec["steps"].append(m), log("rigby: " + m))  # noqa: E731
    if not tolkien_idle():
        rec["verdict"] = "WAIT - Tolkien stayed busy for 25 min (%s); another night" % ", ".join(tolkien_busy())[:120]
        return rec
    before = linux_check("rigby")
    if not before:
        rec["verdict"] = "WAIT - rigby did not answer its check"
        return rec
    rec["before"] = before
    apt = rigby_root("rigby_apt.sh", wait=2400)
    rec["apt"] = apt
    say("apt: rc %s, %s left; newest kernel %s (nvidia built: %s, modules-extra: %s); reboot required: %s" % (
        apt.get("apt_rc"), apt.get("upgradable_left"), apt.get("NEWEST"), apt.get("DKMS_NEWEST"),
        apt.get("EXTRA_NEWEST"), apt.get("REBOOT_REQUIRED")))
    if apt.get("apt_rc") != "0":
        rec["verdict"] = "HOLD - apt upgrade failed on rigby (rc %s); nothing rebooted" % apt.get("apt_rc")
        return rec
    k, running = apt.get("NEWEST"), before.get("KERNEL")
    new_kernel = bool(k and k != running)
    if not new_kernel and apt.get("REBOOT_REQUIRED") != "yes":
        rec["verdict"] = "OK - packages installed, no reboot needed"
        return rec
    if new_kernel and not (k.startswith("6.8.") and apt.get("DKMS_NEWEST") not in (None, "0")
                           and apt.get("EXTRA_NEWEST") not in (None, "0")):
        rec["verdict"] = ("WAIT - kernel %s is not ready for rigby (6.8 only; nvidia-470 built: %s; modules-extra: %s); "
                          "staying on %s" % (k, apt.get("DKMS_NEWEST"), apt.get("EXTRA_NEWEST"), running))
        return rec
    if not tolkien_idle():
        rec["verdict"] = "WAIT - an order started on Tolkien during apt and ran 25 min; the reboot waits for another night"
        return rec
    archive("rigby-apt", running, "vendor", box="rigby", stage="linux", note="apt upgrade; newest kernel %s" % k,
            rollback="apt's old kernel stays installed: `rigby_boot.sh default` boots it; packages: apt install <pkg>=<old> from /var/log/apt/history.log")
    if new_kernel:
        r = _rigby_try(k)
        if r.get("ONE_TIME") != "1":
            rec["verdict"] = "HOLD - the one-time boot of %s was not set (%s); nothing rebooted" % (k, r)
            return rec
        say("trying kernel %s once (one-time boot set; the default stays %s)" % (k, running))
    else:
        rigby_root("rigby_boot.sh", args="default", wait=300)
        say("reboot (same kernel %s)" % running)
    after = wait_boot("rigby", before.get("BOOT"))
    if after is None:
        rec["verdict"] = "HOLD - rigby did not come back within 25 min (a power cycle returns it to %s)" % running
        return rec
    # Post-reboot: restore the entire segment (tablets + rigby's bridge) before proceeding.
    up = subprocess.run([sys.executable, os.path.join(LAB_ROOT, "fellowship", "usbnet_up.py"), "--apply"],
                        capture_output=True, text=True, timeout=1800)
    seg = [ln for ln in up.stdout.splitlines() if "verified ON THE WIRE" in ln]
    say("usbnet_up --apply: %s" % (seg[-1].strip("= ") if seg else "no verdict (rc %s)" % up.returncode))
    after = linux_check("rigby") or after
    bad = rigby_ok(before, after, k if new_kernel else running)
    t0 = time.time()               # Tolkien's USB keeper restores links on its own cadence (300 s, confirmed over 2 passes):
    while bad and time.time() - t0 < 20 * 60:     # judge after it has had its passes, not in the first minute
        time.sleep(60)
        after = linux_check("rigby") or after
        bad = rigby_ok(before, after, k if new_kernel else running)
    rec["after"] = after
    if not bad:
        if new_kernel:
            kept = _rigby_keep(k)
            say("kernel %s kept as the default (%s)" % (k, kept.get("update_grub_rc")))
        rec["verdict"] = "OK - %s, healthy (GPUs, %s tablet links, Tolkien up)" % (
            "now on kernel %s" % k if new_kernel else "rebooted", after.get("ENX"))
        return rec
    say("after-checks FAILED: %s" % "; ".join(bad))
    if new_kernel:
        rigby_root("rigby_boot.sh", args="default", wait=300)
        back = wait_boot("rigby", after.get("BOOT"))
        still = rigby_ok(before, back or {}, running) if back else ["did not come back"]
        rec["verdict"] = "HOLD - kernel %s failed (%s); rolled back to %s: %s" % (
            k, "; ".join(bad)[:150], running, "healthy" if not still else "STILL WRONG: " + "; ".join(still)[:150])
    else:
        rec["verdict"] = "HOLD - rigby after its reboot: %s" % "; ".join(bad)[:200]
    return rec


KIRK = DESK
SUSPEND_GIVE_UP_S = 45 * 60          # the stage's old park give-up, kept: a window that cannot cut a turn lets it finish
KIRK_BACK_S = 25 * 60                # after Softserve's own restart: the same 25 min wait_boot gives Sundae to come back


def kirk_call(method, path, body=None, timeout=30):
    """One HTTP call to Kirk from Softserve -> the parsed answer, or None when it could not be had (never guessed)."""
    import urllib.request
    try:
        req = urllib.request.Request(KIRK + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:                                                              # noqa: BLE001
        return None


def suspend_runs(kirk, sleep=time.sleep, give_up_s=SUSPEND_GIVE_UP_S, clock=time.time):
    """Suspend every HAI run in flight -> (the ids THIS call suspended, None), or (None, why) with nothing left suspended
    by it. A run someone else had already suspended is not touched and not resumed afterwards. The wait is on Kirk's own
    status (`running` reaching 0), not on a clock; the give-up resumes what it suspended and changes nothing."""
    st = kirk("GET", "/runs/parked")
    if not isinstance(st, dict) or "running_ids" not in st:
        return None, "Kirk could not be asked which runs are in flight"
    mine = []
    for rid in st["running_ids"]:
        r = kirk("POST", "/runs/suspend", {"user": S.setting("OWNER_USER", "owner"), "run_id": rid})
        if isinstance(r, dict) and r.get("ok"):
            mine.append(rid)
    t0 = clock()
    while True:
        st = kirk("GET", "/runs/parked")
        if isinstance(st, dict) and st.get("running") == 0:
            return mine, None
        if clock() - t0 > give_up_s:
            resume_runs(kirk, mine, sleep)
            return None, "HAI's runs did not all suspend within %d min; resumed the ones this stage had suspended" % (give_up_s // 60)
        sleep(5)


def resume_runs(kirk, ids, sleep=time.sleep):
    """Resume the runs this stage suspended -> the ids that could NOT be resumed (empty = all back)."""
    left = []
    for rid in ids:
        r = None
        for _ in range(3):                 # Kirk answers /health before every route is warm; three asks, 5 s apart
            r = kirk("POST", "/runs/resume", {"user": S.setting("OWNER_USER", "owner"), "run_id": rid})
            if isinstance(r, dict) and r.get("ok"):
                break
            sleep(5)
        if not (isinstance(r, dict) and r.get("ok")):
            left.append(rid)
    return left


def stage_sundae(log=print, sleep=time.sleep, check=None, kirk=kirk_call):
    rec = {"box": "sundae", "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "steps": []}
    say = lambda m: (rec["steps"].append(m), log("sundae: " + m))  # noqa: E731
    before = (check or linux_check)("sundae")
    if not before:
        rec["verdict"] = "WAIT - Sundae did not answer its check"
        return rec
    rec["before"] = before
    if before.get("REBOOT_REQUIRED") != "yes":
        rec["verdict"] = "OK - nothing needs a reboot"
        return rec
    if before.get("WATCHTOWER_DONE_TODAY") in (None, "0") and datetime.datetime.now().hour < 6:
        rec["verdict"] = "WAIT - Watchtower has not finished this morning; no reboot under it"
        return rec
    # Use suspend (mid-token save) rather than park (waits for turn to end) to avoid long waits during reboot.
    mine, why = suspend_runs(kirk, sleep)
    if mine is None:
        rec["verdict"] = "WAIT - %s; no reboot" % why
        return rec
    rec["suspended"] = mine
    say("%d HAI run(s) suspended mid-turn (%s); restarting Sundae for %s" % (len(mine), ", ".join(m[:8] for m in mine) or "none in flight",
                                                                        before.get("PKGS")))
    archive("sundae-kernel", before.get("KERNEL"), "vendor", box="sundae", stage="linux", note="reboot for %s" % before.get("PKGS"),
            rollback="the previous kernel stays installed: pick it in GRUB (sudo grub-reboot) if the new one misbehaves")
    subprocess.run(["ssh", HEAD, "sudo systemctl reboot"], capture_output=True, timeout=60)
    after = wait_boot("sundae", before.get("BOOT"))
    if after is None:
        rec["verdict"] = "HOLD - Sundae did not come back within 25 min; %d run(s) are suspended on disk: %s" % (len(mine), ", ".join(mine))
        return rec
    rec["after"] = after
    left = resume_runs(kirk, mine, sleep) if after.get("HEALTH") == "200" else list(mine)
    if left:
        say("NOT RESUMED: %s (POST /runs/resume as an admin, or the chat's own Resume)" % ", ".join(left))
    elif mine:
        say("%d run(s) resumed from where they were cut" % len(mine))
    want = set(filter(None, before.get("RUNNING", "").split(",")))
    have = set(filter(None, after.get("RUNNING", "").split(",")))
    bad = []
    if want - have:
        bad.append("containers not back: %s" % ",".join(sorted(want - have)))
    if after.get("HEALTH") != "200":
        bad.append("orchestrator /health %s" % after.get("HEALTH"))
    if after.get("MOUNT_DOCS") != "yes" or after.get("MOUNT_SSD") != "yes":
        bad.append("shares docs=%s ssd=%s" % (after.get("MOUNT_DOCS"), after.get("MOUNT_SSD")))
    if not dns_ok():
        bad.append("Pi-hole %s not answering" % S.setting("PIHOLE_ADDRESS", ""))
    if left:
        bad.append("%d suspended run(s) not resumed: %s" % (len(left), ",".join(i[:8] for i in left)))
    rec["verdict"] = ("OK - restarted onto %s; %d containers, desk, shares and DNS back; %d run(s) suspended and resumed"
                      % (after.get("KERNEL"), len(have), len(mine))
                      if not bad else "HOLD - Sundae after its restart: %s" % "; ".join(bad)[:260])
    return rec


# run_remote.py allows only [A-Za-z0-9,._:=/-] in --args, so a mode and a kernel travel as ONE argument "mode_kernel"
def _rigby_try(k):
    return rigby_root("rigby_boot.sh", args="try_" + k, wait=300)


def _rigby_keep(k):
    return rigby_root("rigby_boot.sh", args="keep_" + k, wait=300)


def hai_jobs_open(fetch=None):
    """Count pending/running HAI jobs on the desk -> int, or None if unavailable (fail-closed).
    Queries over HTTP to avoid running code in the live container."""
    def _fetch():
        r = subprocess.run(["ssh", HEAD, "curl -s -m 20 http://localhost:%s/hai/jobs" % S.setting("ORCH_PORT", "8099")], capture_output=True, timeout=60)
        return r.stdout.decode("utf-8", "replace")
    try:
        jobs = json.loads((fetch or _fetch)())["jobs"]
        return sum(1 for j in jobs if j.get("status") in ("pending", "running", "claimed"))
    except Exception:                                                              # noqa: BLE001
        return None


def jobs_say_wait(n):
    """None when no HAI job is on the desk; otherwise why a reboot under Kirk waits."""
    if n == 0:
        return None
    return "Kirk could not be asked whether HAI jobs are on the desk" if n is None else "%d HAI job(s) are on the desk" % n


def plex_busy():
    """Plex first (scheduler policy 4): ask the object in force - the live orchestrator - whether Plex is transcoding."""
    try:
        r = subprocess.run(["ssh", HEAD, "docker exec orchestrator python -c \"import scheduler; "
                            "print(scheduler.plex_transcoding()[0])\""], capture_output=True, text=True, timeout=60)
        return r.stdout.strip().splitlines()[-1] != "False"     # anything but a clear False is "busy" (fail closed)
    except Exception:                                                              # noqa: BLE001
        return True


def au3_once(box, log=print):
    st_path = os.path.join(STATE, "stager-state.json")
    try:
        st = json.load(open(st_path, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if box in st.get("au3", []):
        return
    r = remote(box, "au3")
    log("%s: Windows Update now download-only (AUOptions=%s) - the lab installs from here on" % (box, r.get("AUOptions")))
    if str(r.get("AUOptions")).strip() == "3":
        st.setdefault("au3", []).append(box)
        _write_json(st_path, st)


def main(argv):
    Ctx.plex_busy = staticmethod(plex_busy)
    Ctx.hai_jobs_open = staticmethod(hai_jobs_open)
    Ctx.suspend = staticmethod(lambda: suspend_runs(kirk_call))
    if "--resume-inflight" in argv:
        try:
            inf = json.load(open(INFLIGHT, encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        box = inf["box"]                  # the reboot that interrupted us was this box's own (Softserve): finish it
        rec = inf["rec"]
        rec["steps"].append("stager restarted after the reboot; finishing the stage")
        if inf.get("suspended"):
            # Wait for Kirk to be responsive before resuming the specific runs suspended by the restart.
            t0 = time.time()
            while kirk_call("GET", "/runs/parked") is None and time.time() - t0 < KIRK_BACK_S:
                time.sleep(15)
            left = resume_runs(kirk_call, inf["suspended"])
            rec["steps"].append(("%d run(s) resumed after the restart" % len(inf["suspended"])) if not left else
                                "NOT RESUMED: %s (POST /runs/resume as an admin, or the chat's own Resume)" % ", ".join(left))
        v, g = ollama_ok(box), gpu_report(box)
        try:
            rr = remote(box, "resume")
            rec["steps"].append("factory resumed: %s" % rr.get("runner_task"))
        except Exception as e:                                                     # noqa: BLE001
            rec["steps"].append("RESUME FAILED (%s) - the safety task resumes it" % type(e).__name__)
        ok = bool(v) and g[0] is True
        rec["verdict"] = "OK - installed, restarted, healthy" if ok else "HOLD - after the restart: ollama %s, GPU %s" % (v, g[2])
        (release(box) if ok else hold(box, "after-checks failed - a person should look", ttl=FAIL_HOLD_TTL))
        os.remove(INFLIGHT)
        record(rec)
        if ok:
            au3_once(box)
        return 0
    linux = {b: f for b, f in ((HEAD, stage_sundae), (TABLET_HEAD, stage_rigby)) if b}   # by their settings; unset = none
    if "--apps-nightly" in argv:               # 03:40: after the Windows stage (03:20), before Sundae's apt (03:50)
        for box in ORDER:
            rec = stage_apps(box)
            record(rec)
            print("%s apps: %s" % (box, rec["verdict"]))
        rec = stage_plex()                     # Plex rides the same task: Softserve only, week-old builds, nobody watching
        record(rec)
        print("plex: %s" % rec["verdict"])
        return 0
    if "--drivers-nightly" in argv:            # 02:30: before everything else; a driver install can take 25 min
        for box in DRIVER_ORDER:
            rec = stage_nvidia(box)
            record(rec)
            print("%s driver: %s" % (box, rec["verdict"]))
        rec = stage_amd_report()
        record(rec)
        return 0
    if "--driver" in argv:
        for box in [a for a in argv if a in DRIVER_ORDER]:
            rec = stage_nvidia(box, dry="--dry" in argv)
            record(rec)
            print("%s driver: %s" % (box, rec["verdict"]))
        return 0
    if "--plex" in argv:
        rec = stage_plex(dry="--dry" in argv)
        record(rec)
        print("plex: %s" % rec["verdict"])
        return 0
    if "--apps" in argv:
        for box in [a for a in argv if a in BOXES]:
            rec = stage_apps(box)
            record(rec)
            print("%s apps: %s" % (box, rec["verdict"]))
        return 0
    if "--linux-nightly" in argv:              # 04:12: after apt (03:50) and Watchtower (04:00), before the 4:30 report
        for box in [b for b in (HEAD, TABLET_HEAD) if b in linux]:
            rec = linux[box]()
            record(rec)
            print("%s: %s" % (box, rec["verdict"]))
        return 0
    for box in [a for a in argv if a in linux]:  # a Linux box by hand: reboots are part of it (say so before running)
        rec = linux[box]()
        record(rec)
        print("%s: %s" % (box, rec["verdict"]))
    argv = [a for a in argv if a not in linux]
    if not [a for a in argv if not a.startswith("--")] and "--nightly" not in argv:
        return 0
    allow = "--allow-reboot" in argv or "--nightly" in argv
    boxes = [a for a in argv if not a.startswith("--")]
    if "--nightly" in argv:
        waiting = {b: pending(b) for b in ORDER}
        boxes = [b for b in ORDER if waiting[b]][:1]
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "stager.log"), "a", encoding="utf-8") as f:     # a quiet night leaves a line too
            f.write("%s nightly: waiting %s -> %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"),
                                                        {b: len(v) for b, v in waiting.items()}, boxes or "nothing to stage"))
        if not boxes:
            print("nothing waiting on any box")
            return 0
    for box in boxes:
        rec = stage(box, allow)
        record(rec)
        print("%s: %s" % (box, rec["verdict"]))
        if rec["verdict"].startswith("OK"):
            au3_once(box)
    return 0


if __name__ == "__main__":
    try:
        rc = main(sys.argv[1:])
    except Exception:                                                              # noqa: BLE001 - headless: leave the trace
        import traceback
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "stager.log"), "a", encoding="utf-8") as f:
            f.write("%s CRASH %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), traceback.format_exc()))
        rc = 1
    sys.exit(rc)
