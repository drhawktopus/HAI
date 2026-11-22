"""Frontier mode: exclusive host access for large models.

Commands:
  enter [--only name1,name2]  Stop non-essential apps, pause WSearch/SysMain, record state
  restore                     Relaunch recorded apps, restart services, verify
  auto [--grace-min N]        Scheduled restore after N minutes of inactivity
  status                      Show current state

Constraints:
- Allow-list only: STOP_APPS entries are the only processes terminated.
- No pattern matching.
- Games are reported, never killed.
- State file (frontier_state.json) is merged, never overwritten."""
import json, os, subprocess, sys, time, urllib.request
if sys.stdout is not None:                  # pythonw (the scheduled task) has no console: stdout is None
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
NO_WINDOW = 0x08000000                      # CREATE_NO_WINDOW: prevents child processes from spawning visible console windows on the user's desktop
_HAI_DIR = os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "HAI")   # the lab's folder on a Windows box
STATE = os.path.join(_HAI_DIR, "frontier_state.json")
LOG = os.path.join(_HAI_DIR, "frontier_mode.log")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab_settings as _S   # noqa: E402  the per-box values file beside this script
USER = _S.setting("FRONTIER_CONSOLE_USER", "")   # the account whose interactive desktop runs the apps frontier mode stops
if not USER:
    sys.exit("frontier_mode: FRONTIER_CONSOLE_USER is not set in the lab.env beside this script")
STOP_APPS = ["RzSynapse", "RadeonSoftware", "AMDRSSrcExt", "asus_framework", "PhoneExperienceHost", "SystemSettings", "claude"]
# Windows starts these on demand and they exit when idle. Relaunch once but never require them to stay up, as requiring persistence triggers auto-reopens of the Settings window during periodic passes.
ON_DEMAND = {"phoneexperiencehost", "systemsettings"}
PAUSE_SERVICES = ["WSearch", "SysMain"]
GAME_DIRS = ("\\steamlibrary\\", "\\steamapps\\", "\\xbox games\\", "\\epic games\\", "\\windowsapps\\microsoft.gaming")
FRONTIER_MODELS = ("qwen3.8-flash-next",)


def log(m):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), m)
    if sys.stdout is not None:
        print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, creationflags=NO_WINDOW)
    return (r.stdout or "").strip()


def procs():
    out = ps("Get-CimInstance Win32_Process | ForEach-Object { [pscustomobject]@{n=$_.Name; p=$_.ProcessId; "
             "x=$_.ExecutablePath; c=$_.CommandLine; s=$_.SessionId} } | ConvertTo-Json -Compress")
    data = json.loads(out) if out else []
    return data if isinstance(data, list) else [data]


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"apps": [], "services": [], "entered": None, "last_frontier_seen": None}


def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, STATE)


def enter(only=None):
    names = only or STOP_APPS
    st = load_state()
    st["entered"] = st.get("entered") or time.strftime("%Y-%m-%d %H:%M:%S")
    games = [p for p in procs() if (p.get("x") or "").lower() and any(g in (p.get("x") or "").lower() for g in GAME_DIRS)]
    for g in games:
        log("GAME RUNNING - not stopped (ask the owner): %s pid %s" % (g["n"], g["p"]))
    known = {(a["name"], a["cmd"]) for a in st["apps"]}
    stopped = 0
    for p in procs():
        base = (p.get("n") or "").rsplit(".", 1)[0]
        if base.lower() not in [n.lower() for n in names] or not p.get("x"):
            continue
        rec = {"name": base, "exe": p["x"], "cmd": p.get("c") or ('"%s"' % p["x"]), "session": p.get("s")}
        if (rec["name"], rec["cmd"]) not in known:
            st["apps"].append(rec)
            known.add((rec["name"], rec["cmd"]))
        subprocess.run(["taskkill", "/PID", str(p["p"]), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        stopped += 1
        log("stopped %s pid %s (%s)" % (base, p["p"], p["x"]))
    if not only:
        for svc in PAUSE_SERVICES:
            status = ps("(Get-Service %s -EA SilentlyContinue).Status" % svc)
            if status == "Running":
                ps("Stop-Service %s -Force -EA SilentlyContinue" % svc)
                if svc not in st["services"]:
                    st["services"].append(svc)
                log("paused service %s" % svc)
    save_state(st)
    log("ENTER done: %d process(es) stopped, %d app record(s), services %s" % (stopped, len(st["apps"]), st["services"]))


def launch_in_session(cmd, tag):
    """Start `cmd` inside USER's interactive desktop session via a one-shot Interactive task, then remove the task."""
    exe_args = cmd.strip()
    if exe_args.startswith('"'):
        exe, _, args = exe_args[1:].partition('"')
    else:
        exe, _, args = exe_args.partition(" ")
    tn = "HAI frontier restore - " + tag
    script = ("$a = New-ScheduledTaskAction -Execute '%s'%s; "
              "$p = New-ScheduledTaskPrincipal -UserId '%s' -LogonType Interactive; "
              "Register-ScheduledTask -TaskName '%s' -Action $a -Principal $p -Force | Out-Null; "
              "Start-ScheduledTask -TaskName '%s'; Start-Sleep 3; Unregister-ScheduledTask -TaskName '%s' -Confirm:$false"
              % (exe.replace("'", "''"), (" -Argument '%s'" % args.strip().replace("'", "''")) if args.strip() else "",
                 USER, tn, tn, tn))
    ps(script)


def restore():
    st = load_state()
    if not st["apps"] and not st["services"]:
        log("RESTORE: nothing recorded")
        return True
    up = {(p.get("n") or "").rsplit(".", 1)[0].lower() for p in procs()}
    for i, a in enumerate(st["apps"]):
        if a["name"].lower() in up:                    # already back (an earlier pass relaunched it): never duplicate
            continue
        launch_in_session(a["cmd"], "%s-%d" % (a["name"], i))
        log("relaunched %s" % a["name"])
    for svc in st["services"]:
        ps("Start-Service %s -EA SilentlyContinue" % svc)
        log("started service %s" % svc)
    time.sleep(10)
    running = {(p.get("n") or "").rsplit(".", 1)[0].lower() for p in procs()}
    missing = [a["name"] for a in st["apps"] if a["name"].lower() not in running and a["name"].lower() not in ON_DEMAND]
    bad_svc = [s for s in st["services"] if ps("(Get-Service %s).Status" % s) != "Running"]
    if missing or bad_svc:
        log("RESTORE INCOMPLETE - still down: apps %s services %s (state kept; next auto pass retries)" % (missing, bad_svc))
        return False
    os.replace(STATE, STATE + ".restored-" + time.strftime("%Y%m%d-%H%M%S"))
    log("RESTORE done: %d app(s) back, services %s running; state archived" % (len(st["apps"]), st["services"]))
    return True


def frontier_loaded():
    """Detects active frontier workloads: models in Ollama, direct llama-server instances, or the Strata engine.

    Note: Direct llama-server and Strata processes are invisible to the Ollama API; they must be checked via process listing to avoid false negatives."""
    try:
        direct = [p for p in procs() if ((p.get("n") or "").lower() == "llama-server.exe"
                                         and "flash-next" in (p.get("c") or "").lower())
                  or (p.get("n") or "").lower() == "strata.exe"]
    except Exception:
        return None
    if direct:
        return True
    try:
        models = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=10)).get("models", [])
    except Exception:
        return None                                   # could not tell: never restore on a guess
    return any(m.get("name", "").startswith(FRONTIER_MODELS) for m in models)


def auto(grace_min=10.0):
    if not os.path.exists(STATE):
        return
    st = load_state()
    loaded = frontier_loaded()
    if loaded is None:
        log("auto: ollama unreadable - no action")
        return
    now = time.time()
    if loaded:
        st["last_frontier_seen"] = now
        save_state(st)
        return
    seen = st.get("last_frontier_seen") or time.mktime(time.strptime(st["entered"], "%Y-%m-%d %H:%M:%S"))
    if now - seen >= grace_min * 60:
        log("auto: no frontier model loaded for %.0f min - restoring" % ((now - seen) / 60))
        restore()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    if cmd == "install-task":
        # Requires Interactive logon (apps restore to user desktop), Highest privileges, and IgnoreNew to prevent overlapping runs.
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        print(ps("$a = New-ScheduledTaskAction -Execute '%s' -Argument '\"%s\" auto'; "
                 "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5); "
                 "$p = New-ScheduledTaskPrincipal -UserId '%s' -LogonType Interactive -RunLevel Highest; "
                 "$s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) "
                 "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable; "
                 "Register-ScheduledTask -TaskName 'HAI Frontier Restore' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null; "
                 "$i = Get-ScheduledTaskInfo -TaskName 'HAI Frontier Restore'; 'task next run: ' + $i.NextRunTime"
                 % (pyw, os.path.abspath(__file__), USER)))
    elif cmd == "enter":
        enter([x for x in (arg("--only") or "").split(",") if x] or None)
    elif cmd == "restore":
        sys.exit(0 if restore() else 1)
    elif cmd == "auto":
        auto(float(arg("--grace-min", 10)))
    else:
        print(json.dumps(load_state(), indent=1))
