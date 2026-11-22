"""Set a machine up as a HAI head, a HAI factory, or both, from factory_role.json (plan step 5.2, a script first).

    python setup_factory.py --root <folder> --name <this box's name> --shape <nvidia|amd> [--user <name>]
                            [--head <head URL>] [--token-file <file>] [--python <python>] [--set NAME=value ...] [--apply]
    python setup_factory.py --role head [--head-set NAME=value ...] [--no-start] [--apply]
    python setup_factory.py --role head+factory --root <folder> --name <name> --shape <nvidia|amd> [...] [--apply]

--role says what this device is: `factory` (the default), `head`, or `head+factory` (a head that is also a factory, as
the lab's own head machine is). For a head (the manifest's `head`) it takes the quick-start's steps: settings.env from
the example beside the head's compose file (an existing one is kept; --head-set changes the named settings), the
head's folders, the worker token (made once, kept), then `docker compose up -d --build` (not with --no-start). With
head+factory the factory part reports to that head (its address and its token file, unless given).

The SHAPE is the card's kind (the manifest's `shapes`): it picks the services (a service naming shapes runs only on
those) and, on Windows, who runs the ones that drive the GPU: SYSTEM on nvidia, the logged-on user on amd (--user, the
account the box signs in as at boot). Without --apply it prints exactly what it would do and changes nothing. With
--apply it:
  1. lays the code down: the manifest's `code.from` (paths relative to the HAI tree this script sits in) into <root>/bin;
  2. creates the factory's folders;
  3. writes the settings: <root>/etc/lab.env (HAI_URL, OLLAMA_PORT and any --set), and the worker token, read from
     --token-file (never from the command line), into <root>/etc/dm_relay.env;
  4. writes one launcher per service into <root>/etc/services/ (a service whose `only_if_setting` is unset, whose shapes
     leave this one out, or whose `os` is another is skipped; `{NAME}` in its arguments is that setting's value);
  5. registers each as a standing service: on Windows a scheduled task "HAI Factory <service>" (at startup, or at logon
     for one run as the user, repeated every 15 minutes or on its own schedule, never two at once, three restarts, no
     time limit; the lab's own recipe), on Linux a systemd unit (and timer for a periodic one) written to --units
     (default /etc/systemd/system), then enabled. It never starts a second copy: the scheduler drops a start while one
     runs.
A factory works alone first: with no --head it takes orders from its own queue folder and pushes no reports.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "factory_role.json")


def tree_root():
    """The HAI tree this script was run from: the public tree's foundry/ folder sits beside tools/; in the lab's own
    foundry repo the tools are not beside it, and the caller says where with --tree."""
    return os.path.dirname(HERE)


def plan(args, manifest, tree):
    root = os.path.abspath(args.root)
    settings = dict(kv.split("=", 1) for kv in args.set)
    steps, files = [], {}
    code_ext = (".py", ".json", ".ps1", ".cmd", ".bat", ".vbs", ".sh", ".yaml", ".yml", ".toml")
    for entry in manifest["code"]:
        path, into = os.path.join(tree, entry["from"]), os.path.join(root, entry["into"])
        if os.path.isdir(path):
            walk = os.walk(path) if entry.get("recursive") else [(path, [], os.listdir(path))]
            for d, _ds, fs in walk:
                for f in sorted(fs):
                    if f.endswith(code_ext) and os.path.isfile(os.path.join(d, f)):
                        files[os.path.join(d, f)] = os.path.join(into, os.path.relpath(os.path.join(d, f), path))
        elif os.path.isfile(path):
            files[path] = os.path.join(into, os.path.basename(path))
        else:
            raise SystemExit("the manifest's code %r is not in the tree at %s" % (entry["from"], tree))
    steps.append("copy %d code files into %s" % (len(files), root))
    folders = [os.path.join(root, f) for f in manifest["folders"]]
    steps.append("create %d folders under %s" % (len(folders), root))
    lab_env = {"HAI_URL": args.head or "", "OLLAMA_PORT": settings.pop("OLLAMA_PORT", "11434")}
    lab_env.update(settings)
    steps.append("write %s (%s)" % (os.path.join(root, "etc", "lab.env"), ", ".join(sorted(lab_env))))
    if args.token_file:
        steps.append("write the worker token into %s" % os.path.join(root, "etc", "dm_relay.env"))
    env = {"FOUNDRY_ROOT": root, "FOUNDRY_FLEET_HOST": args.name, "LAB_SETTINGS_FILE": os.path.join(root, "etc", "lab.env"),
           "PYTHONPATH": os.path.join(root, "bin")}
    env.update({k: v for k, v in lab_env.items() if k.startswith(("FOUNDRY_", "OLLAMA_"))})
    services = []
    this_os = "windows" if os.name == "nt" else "linux"
    steps.append("shape %s: %s" % (args.shape, manifest["shapes"][args.shape]["why"]))
    for s in manifest["services"]:
        if s.get("shapes") and args.shape not in s["shapes"]:
            continue                       # another shape's service: not this machine's
        gate = s.get("only_if_setting")
        value = lambda n: lab_env.get(n) or os.environ.get(n, "")
        if gate and not value(gate):
            steps.append("skip %s (%s is not set)" % (s["name"], gate))
            continue
        if s.get("os") and s["os"] != this_os:
            steps.append("skip %s (%s only)" % (s["name"], s["os"]))
            continue
        s = dict(s, run=[re.sub(r"\{([A-Z0-9_]+)\}", lambda m: value(m.group(1)), a) for a in s["run"]])
        services.append(s)
    for s in services:
        steps.append("service %-18s %-12s %-11s %s" % (s["name"], s["kind"] if s["kind"] == "daemon" else
                                                       "every %d min" % s["minutes"], runs_as(manifest, args, s),
                                                       " ".join(s["run"])))
    for o in manifest.get("installed_by_others", []):
        steps.append("not set up here: %s (%s)" % (o["name"], o["by"]))
    return root, files, folders, lab_env, env, services, steps


def runs_as(manifest, args, s):
    """Who a service runs as: the logged-on user (--user) for GPU work on a shape that needs the desktop session, on
    Windows; SYSTEM (Windows) or root (Linux) otherwise."""
    if os.name == "nt" and s.get("gpu_work") and manifest["shapes"][args.shape]["gpu_work_runs_as"] == "interactive":
        return args.user
    return "SYSTEM" if os.name == "nt" else "root"


def command(root, py, s):
    """The service's command line: a .py runs on the factory's Python, a .ps1 on PowerShell, anything else as itself."""
    first, rest = s["run"][0], s["run"][1:]
    q = lambda a: '"%s"' % os.path.join(root, "bin", a) if a.endswith((".py", ".ps1")) else a
    if first.endswith(".py"):
        return '"%s" -u %s' % (py, " ".join(q(a) for a in s["run"]))
    if first.endswith(".ps1"):
        return "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File %s %s" % (
            q(first), " ".join(rest))
    return " ".join([first] + rest)


def launcher_cmd(root, py, env, s):
    lines = ["@echo off", "setlocal"] + ["set %s=%s" % kv for kv in sorted(env.items())]
    if s.get("start_model_server"):
        port = env.get("OLLAMA_PORT", "11434")
        lines += ['curl.exe -s --max-time 3 http://127.0.0.1:%s/api/version >nul 2>&1' % port,
                  "if errorlevel 1 (", '  start "" /b ollama serve', "  timeout /t 8 /nobreak >nul", ")"]
    lines += ['cd /d "%s"' % os.path.join(root, "bin"),
              '%s >> "%s" 2>&1' % (command(root, py, s).rstrip(), os.path.join(root, s["log"])),
              "endlocal"]
    return "\r\n".join(lines) + "\r\n"


def launcher_sh(root, py, env, s):
    lines = ["#!/bin/sh"] + ["export %s='%s'" % kv for kv in sorted(env.items())]
    if s.get("start_model_server"):
        port = env.get("OLLAMA_PORT", "11434")
        lines += ["curl -s --max-time 3 http://127.0.0.1:%s/api/version >/dev/null 2>&1 || "
                  "{ nohup ollama serve >/dev/null 2>&1 & sleep 8; }" % port]
    lines += ['cd "%s"' % os.path.join(root, "bin"),
              'exec %s >> "%s" 2>&1' % (command(root, py, s).rstrip(), os.path.join(root, s["log"]))]
    return "\n".join(lines) + "\n"


def task_ps1(name, launcher, s, user="SYSTEM"):
    """The lab's standing-task recipe (Lab/bin/fix_health_daemon_task.ps1): at startup + a past-dated time trigger
    repeated (15 min for a daemon, its own minutes for a periodic one), IgnoreNew, three restarts, no time limit. A task
    run as a user (GPU work on the amd shape) runs interactively in that user's session, from logon instead of startup."""
    every = 15 if s["kind"] == "daemon" else s["minutes"]
    interactive = user != "SYSTEM"
    return "\r\n".join([
        '$ErrorActionPreference = "Stop"',
        '$a = New-ScheduledTaskAction -Execute "cmd.exe" -Argument \'/c ""%s""\'' % launcher,
        "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddYears(-5) -RepetitionInterval (New-TimeSpan -Minutes %d)" % every,
        '$t.Repetition.Duration = ""',
        ('$b = New-ScheduledTaskTrigger -AtLogOn -User "%s"' % user) if interactive else "$b = New-ScheduledTaskTrigger -AtStartup",
        "$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) "
        "-ExecutionTimeLimit (New-TimeSpan -Seconds 0) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
        ('$p = New-ScheduledTaskPrincipal -UserId "%s" -LogonType Interactive -RunLevel Highest' % user) if interactive
        else '$p = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest',
        'Register-ScheduledTask -TaskName "%s" -Action $a -Trigger @($t, $b) -Settings $set -Principal $p -Force | Out-Null' % name,
        '$v = Get-ScheduledTask -TaskName "%s"' % name,
        '"{0}: restarts={1} instances={2}" -f $v.TaskName, $v.Settings.RestartCount, $v.Settings.MultipleInstances']) + "\r\n"


def unit_files(name, launcher, s):
    svc = ("[Unit]\nDescription=HAI factory %s\nAfter=network-online.target\n\n[Service]\nExecStart=/bin/sh %s\n"
           "Restart=%s\nRestartSec=300\n\n[Install]\nWantedBy=multi-user.target\n"
           % (s["name"], launcher, "always" if s["kind"] == "daemon" else "no"))
    out = {name + ".service": svc}
    if s["kind"] == "every":
        out[name + ".timer"] = ("[Unit]\nDescription=HAI factory %s, every %d min\n\n[Timer]\nOnBootSec=1min\n"
                                "OnUnitActiveSec=%dmin\n\n[Install]\nWantedBy=timers.target\n" % (s["name"], s["minutes"], s["minutes"]))
    return out


def _set_line(text, name, value):
    """`text` (a settings file) with NAME set to `value`: an existing or commented-out NAME line is replaced in place,
    else the line is added at the end."""
    rx = re.compile(r"^#?\s*%s=.*$" % re.escape(name), re.M)
    line = "%s=%s" % (name, value)
    if rx.search(text):
        return rx.sub(lambda _m: line, text, count=1)
    return text + ("" if text.endswith("\n") or not text else "\n") + line + "\n"


def setup_stack(args, h, label):
    """One container stack's part of an install: the head (the manifest's `head`) or the tablet head (`tablet_head`).
    Plan it, and with --apply do it: its settings file from the example beside its compose file (kept if it exists;
    --head-set changes the named settings), its folders, its worker token if it has one (made once, kept), its host
    installer when --tablet-units gives that installer's argument, then `docker compose up -d --build` (not with
    --no-start). Returns (rc, its URL or "", the token's file or "")."""
    compose = os.path.join(args.tree, *h["compose"].split("/"))
    base = os.path.dirname(compose)
    example = os.path.join(base, h["settings_example"])
    settings = os.path.join(base, h.get("settings", "settings.env"))
    token = os.path.join(base, *h["token"].split("/")) if h.get("token") else ""
    if not os.path.isfile(compose) or not os.path.isfile(example):
        raise SystemExit("the %s's compose file or its example settings are not in the tree at %s" % (label, args.tree))
    overrides = [kv.split("=", 1) for kv in args.head_set]
    steps = ["%s: %s" % (label, h["why"])] + ["first, by hand: %s" % p for p in h.get("prerequisites", [])]
    steps.append(("keep %s" % settings) if os.path.exists(settings) else
                 ("write %s from %s" % (settings, os.path.basename(example))))
    if overrides:
        steps.append("set in %s: %s" % (os.path.basename(settings), ", ".join(n for n, _v in overrides)))
    steps.append("create the %s's folders under %s: %s" % (label, base, ", ".join(h["folders"])))
    if token:
        steps.append(("keep the worker token in %s" % token) if os.path.exists(token) else
                     ("make the worker token in %s" % token))
    host = None
    if h.get("host_installer"):
        if args.tablet_units:
            host = ["bash", os.path.join(args.tree, *h["host_installer"].split("/")), args.tablet_units]
            steps.append("install the host's services: %s" % " ".join(host))
        else:
            steps.append("leave the host's services (run again with --tablet-units %s)" % h["host_installer_arg"])
    start = ["docker", "compose", "-f", compose, "up", "-d", "--build"]
    steps.append(("do not start the %s (--no-start)" % label) if args.no_start else
                 ("start the %s: %s" % (label, " ".join(start))))
    for s in steps:
        print(("" if args.apply else "would ") + s)
    if not args.apply:
        return 0, h.get("url", ""), token
    text = open(settings if os.path.exists(settings) else example, encoding="utf-8").read()
    for n, v in overrides:
        text = _set_line(text, n, v)
    if overrides or not os.path.exists(settings):
        open(settings, "w", encoding="utf-8", newline="\n").write(text)
    for f in h["folders"]:
        os.makedirs(os.path.join(base, *f.split("/")), exist_ok=True)
    if token and not os.path.exists(token):
        import secrets
        open(token, "w", encoding="utf-8", newline="").write(secrets.token_hex(32))
    for cmd, what in ((host, "host services"), (None if args.no_start else start, "start")):
        if cmd:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode:
                print("%s NOT set up: %s failed: %s" % (label, what, (r.stderr or r.stdout or "").strip()[-300:]))
                return 1, h.get("url", ""), token
    print("%s set up at %s%s" % (label, base, "" if args.no_start or not h.get("url") else "; open " + h["url"]))
    return 0, h.get("url", ""), token


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--role", default="factory", choices=("factory", "head", "head+factory", "tablet-head"),
                    help="what this device is: a factory (the default), a head, a head that is also a factory, or the "
                         "tablet head (the tablet factory, Tolkien: a factory with the tablets' desk)")
    ap.add_argument("--head-set", action="append", default=[], metavar="NAME=value",
                    help="a setting to write into the head's settings.env (or the tablet head's tolkien.env)")
    ap.add_argument("--tablet-units", default="", metavar="ARG",
                    help="tablet head: the host-services installer's argument (its usage names the parts)")
    ap.add_argument("--no-start", action="store_true", help="set the head up but do not start it")
    ap.add_argument("--root", default="")
    ap.add_argument("--name", default="", help="this factory's name, as the head will call it")
    ap.add_argument("--shape", default="", help="the card's kind: one of the manifest's shapes (nvidia, amd)")
    ap.add_argument("--user", default="", help="the account the box signs in as, for GPU work on a shape that needs it")
    ap.add_argument("--head", default="", help="the head's address (HAI_URL); empty = the factory works alone")
    ap.add_argument("--token-file", default="", help="a file holding the head's worker token (P20_WORKER_TOKEN)")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--tree", default=tree_root(), help="the HAI tree to lay the code down from")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=value")
    ap.add_argument("--units", default="/etc/systemd/system", help="where to write systemd units (Linux)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-register", action="store_true",
                    help="lay the factory down and write its launchers, but register no standing service")
    args = ap.parse_args(argv)
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    if args.role == "tablet-head":
        return setup_stack(args, manifest["tablet_head"], "tablet head")[0]
    if args.role in ("head", "head+factory"):
        rc, url, token = setup_stack(args, manifest["head"], "head")
        if rc or args.role == "head":
            return rc
        args.head = args.head or url                     # the factory beside this head reports to it
        args.token_file = args.token_file or (token if os.path.exists(token) else "")
    missing = [f for f in ("root", "name", "shape") if not getattr(args, f)]
    if missing:
        raise SystemExit("a factory needs --%s" % ", --".join(missing))
    if not args.name.replace("-", "").isalnum() or args.name != args.name.lower():
        raise SystemExit("--name must be lowercase letters, digits and dashes")
    if args.shape not in manifest["shapes"]:
        raise SystemExit("--shape must be one of: %s" % ", ".join(sorted(manifest["shapes"])))
    if os.name == "nt" and manifest["shapes"][args.shape]["gpu_work_runs_as"] == "interactive" and not args.user:
        raise SystemExit("--user is required for the %s shape: the account the box signs in as runs its GPU work"
                         % args.shape)
    root, files, folders, lab_env, env, services, steps = plan(args, manifest, args.tree)
    windows = os.name == "nt"
    for s in steps:
        print(("" if args.apply else "would ") + s)
    if not args.apply:
        print("dry run: nothing changed (add --apply)")
        return 0
    for f in folders:
        os.makedirs(f, exist_ok=True)
    os.makedirs(os.path.join(root, "etc", "services"), exist_ok=True)
    for src, dst in files.items():
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    with open(os.path.join(root, "etc", "lab.env"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join("%s=%s\n" % kv for kv in sorted(lab_env.items())))
    if args.token_file:
        token = open(args.token_file, encoding="utf-8").read().strip()
        with open(os.path.join(root, "etc", "dm_relay.env"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("P20_WORKER_TOKEN=%s\n" % token)
    failed = []
    for s in services:
        task = "HAI Factory %s" % s["name"]
        if windows:
            launcher = os.path.join(root, "etc", "services", "hai_%s.cmd" % s["name"].replace("-", "_"))
            open(launcher, "w", encoding="utf-8", newline="").write(launcher_cmd(root, args.python, env, s))
            ps1 = os.path.join(root, "etc", "services", "register_%s.ps1" % s["name"].replace("-", "_"))
            open(ps1, "w", encoding="utf-8", newline="").write(task_ps1(task, launcher, s, runs_as(manifest, args, s)))
            if not args.no_register:
                r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                                   capture_output=True, text=True)
                print(r.stdout.strip() or r.stderr.strip()[:300])
                if r.returncode:
                    failed.append("%s (exit %d)" % (task, r.returncode))
        else:
            launcher = os.path.join(root, "etc", "services", "hai_%s.sh" % s["name"].replace("-", "_"))
            open(launcher, "w", encoding="utf-8", newline="\n").write(launcher_sh(root, args.python, env, s))
            unit = "hai-factory-%s" % s["name"]
            if not args.no_register:
                for fname, text in unit_files(unit, launcher, s).items():
                    open(os.path.join(args.units, fname), "w", encoding="utf-8", newline="\n").write(text)
                for cmd in (["systemctl", "daemon-reload"],
                            ["systemctl", "enable", "--now", unit + (".timer" if s["kind"] == "every" else ".service")]):
                    r = subprocess.run(cmd, capture_output=True, text=True)
                    if r.returncode:
                        failed.append("%s: systemctl %s (exit %d) %s" % (unit, cmd[1], r.returncode, (r.stderr or "").strip()[:200]))
                        break
    if failed:
        print("factory %s NOT set up: %d service(s) did not register: %s" % (args.name, len(failed), "; ".join(failed)))
        return 1
    print("factory %s set up at %s with %d services" % (args.name, root, len(services)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
