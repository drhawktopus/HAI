"""Deploy lab-engine trees to GPU factories.

The lab tree is a copy of the stock tree excluding stock engine files; cross-compiler DLL mixing (e.g., clang CPU/ROCm plugins with MSVC core) fails, so the set replaces only the serving path used by llama-server.

Usage:
    fleet_deploy.py --engine <delivered order dir> [--only host]   install + activate
    fleet_deploy.py --engine-rollback [--only host]                launcher back to newest pre-lab backup
    fleet_deploy.py --check                                        report engine status"""
import base64
import datetime
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.request

import foundry_paths           # the workspace's ONE settings reader
from foundry_paths import fleet_hosts

# Per-box facts are settings (etc\lab.env beside the foundry root): which engine set a box takes, the launcher script
# that starts its ollama, where a build is staged. Empty tables = no engine deploys on this head.
SETS = foundry_paths.setting_json("FLEET_ENGINE_SETS", {})             # host -> "nvidia" | "amd"
LAUNCHERS = foundry_paths.setting_json("FLEET_ENGINE_LAUNCHERS", {})   # host -> its ollama launcher script
STAGE = foundry_paths.setting("FLEET_ENGINE_STAGE", r"C:\labbuild")
LAN = fleet_hosts()   # the fleet table is a setting; no file keeps its own copy of it
# the runbook's fix for an auto-created Block rule; a firewall rule is a human's change, so the deploy only prints it
FIREWALL_FIX = 'netsh advfirewall firewall set rule name=ollama.exe dir=in program="%s" new action=allow'
FIREWALL_ADD = ('netsh advfirewall firewall add rule name=ollama.exe dir=in action=allow protocol=TCP '
                'program="%s" profile=private,public')
# boxes whose launcher starts ollama as the CONSOLE user: a new exe path makes Windows prompt, and an unanswered prompt
# becomes a Block rule (Mothership's start-ollama.ps1 Start-Process; BigHonker/Softserve run it as SYSTEM - no prompt)
CONSOLE_LAUNCH = set(foundry_paths.setting_json("FLEET_CONSOLE_LAUNCH", []))
FW_QUERY = ("$p = '%s'; $a = @(Get-NetFirewallApplicationFilter | Where-Object { $_.Program -eq $p } | "
            "Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' }); "
            "if ($a | Where-Object { $_.Action -eq 'Block' }) { 'FW block' } elseif ($a | Where-Object { "
            "$_.Action -eq 'Allow' }) { 'FW allow' } else { 'FW none' }")
# MARKERS must be lab-specific: stock llama.cpp contains 'pending_token' (as pending_token_idx), which would cause false positives if used.
MARKERS = ["lab_resume", "lab_rng", "lab_get_carry"]
# rigby's engine dirs are settings: the mounted CUDA bin Tolkien serves from (RUNBOOK-GT730 § CUDA: read the mount, not
# this line) and its in-place build tree, %s = the tag. Empty = this head has no rigby engine to check or swap.
RIGBY_ENGINE = foundry_paths.setting("RIGBY_ENGINE_DIR", "")
# the tablet head builds its engine in place (lab-engine\build_cuda114_<tag>.sh -> RIGBY_BUILD_DIR); the deploy moves the BIN
# into the mounted dir (a bind mount follows the directory, so Tolkien needs no restart). The live tree's sources stay
# where they are: build_slot_resume.sh's swap re-patches them, right for a patch on the same tree, wrong for a version.
RIGBY_BUILD = foundry_paths.setting("RIGBY_BUILD_DIR", "")
# its own prefix: build_slot_resume.sh's `...archive-llama.cpp-latest-<stamp>` dirs sort after any `...archive-2026...`
# name, so a shared prefix made rollback's "newest" an older patch generation (one name = one artifact)
RIGBY_ARCHIVE = foundry_paths.setting("RIGBY_ENGINE_ARCHIVE_PREFIX", "")   # the archive prefix on the tablet head
TABLET_HEAD = foundry_paths.setting("TABLET_HEAD_NAME", "")   # its ssh name (the lane keeps the name rigby); empty = none
RIGBY_SWAP = r"""set -eu
LIVE="$1"; NEW="$2"; ARCH="$3"; MODE="$4"; MARK="$5"
busy() { pgrep -f "[l]lama/cuda/llama-server|[l]lama-cuda-114/bin/llama" >/dev/null; }
if busy; then echo "WAIT: a CUDA llama process is running on rigby"; exit 0; fi
if [ "$MODE" = rollback ]; then
  NEW=$(ls -d "$ARCH"* 2>/dev/null | sort | tail -1)
  [ -n "$NEW" ] || { echo "FAILED: no archived bin to roll back to"; exit 0; }
else
  grep -a -q -E "$MARK" "$NEW/libllama-server-impl.so" || { echo "FAILED: $NEW is not a lab build"; exit 0; }
  cp -a "$LIVE" "$ARCH$(date +%Y%m%d-%H%M%S)"
fi
cp -a "$NEW/." "$LIVE/"
for f in libllama-server-impl.so libllama.so libllama-common.so llama-server; do
  [ -e "$NEW/$f" ] || continue
  cmp -s "$NEW/$f" "$LIVE/$f" || { echo "FAILED: $f differs after the copy"; exit 0; }
done
busy && echo "OK (a CUDA llama process started during the copy - it may hold mixed libraries)" || echo "OK - $LIVE = $NEW"
"""


def lab_tree(stock, tag):
    """The versioned lab tree beside a stock tree: ...\\Ollama-0.40.5 -> ...\\Ollama-0.40.5-lab<tag>."""
    base = stock.rstrip("\\")
    if "-lab" in os.path.basename(base):
        base = base[:base.rindex("-lab")]
    return base + "-lab" + tag


def repoint(text, old_tree, new_tree):
    """The launcher with its ACTIVE line(s) moved from one tree to the other; comments are history and stay as written.
    -> (new text, lines changed)."""
    out, n = [], 0
    for line in text.splitlines(keepends=True):
        if not line.lstrip().startswith("#") and old_tree + "\\" in line:
            line = line.replace(old_tree + "\\", new_tree + "\\")
            n += 1
        out.append(line)
    return "".join(out), n


REMOTE = r'''
import base64, glob, hashlib, json, os, re, shutil, subprocess, tarfile, time, urllib.request

CFG = json.loads(base64.b64decode("__CFG__").decode("utf-8"))
LAUNCHER, ACTION, TAG = CFG["launcher"], CFG["action"], CFG.get("tag", "")
MARKERS = [m.encode() for m in CFG["markers"]]
rep = {"host": os.environ.get("COMPUTERNAME", "?"), "action": ACTION, "steps": []}
__FUNCS__

def read_launcher():
    with open(LAUNCHER, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def active_tree(text):
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = re.search(r"'([^']*\\Ollama[^'\\]*)\\ollama\.exe'", line)
        if m:
            return m.group(1)
    return None


def engine(tree):
    lib = os.path.join(tree, "lib", "ollama")
    out = {"tree": tree}
    try:
        r = subprocess.run([os.path.join(lib, "llama-server.exe"), "--version"], capture_output=True, text=True,
                           errors="replace", timeout=60, cwd=lib)
        out["version"] = " | ".join(l.strip() for l in (r.stdout + r.stderr).splitlines() if l.strip())[:200]
    except Exception as e:
        out["version"] = "unreadable: %r" % e
    marks, compat = {}, 0
    for p in glob.glob(os.path.join(lib, "*.dll")) + glob.glob(os.path.join(lib, "*", "*.dll")):
        try:
            data = open(p, "rb").read()
        except OSError:
            continue
        c = sum(data.count(m) for m in MARKERS)
        if c:
            marks[os.path.relpath(p, lib)] = c
        compat += data.count(b"llama_ollama_compat")       # ollama's llama/compat layer (stock libllama.dll: 65)
    out["markers"] = marks
    out["lab"] = bool(marks)
    out["compat"] = compat
    return out


def serving_exe():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='ollama.exe'\" | ForEach-Object { $_.ExecutablePath }"],
                       capture_output=True, text=True, timeout=60)
    return sorted({l.strip() for l in r.stdout.splitlines() if l.strip()})


def restart(old_tree):
    """ollama and ITS runners only - by executable path under the tree being left. Mothership's llama-swap runs other
    llama-server.exe builds (C:\\ProgramData\\HAI\\tools, Flash-Next) that this must never touch."""
    subprocess.run(["schtasks", "/end", "/tn", "Ollama Serve"], capture_output=True)
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith('%s\\', "
          "[StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "
          "$_.ProcessId }" % old_tree.replace("'", "''"))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120)
    rep["steps"].append("stopped %s (processes running from %s)" % (r.stdout.split() or "nothing", old_tree))
    time.sleep(3)
    subprocess.run(["schtasks", "/run", "/tn", "Ollama Serve"], capture_output=True)
    for _ in range(60):                                     # the server answering is the status we wait on
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=5) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(2)
    return None


text = read_launcher()
tree = active_tree(text)
rep["launcher_tree"] = tree
if ACTION == "check":
    rep["engine"] = engine(tree) if tree else {"tree": None}
elif ACTION == "install":
    stock = tree if "-lab" not in os.path.basename(tree) else tree[:tree.rindex("-lab")]
    lab = lab_tree(stock, TAG)
    rep["stock"], rep["lab_tree"] = stock, lab
    stage = os.path.join(r"__STAGE__", "engine-" + TAG)
    shutil.rmtree(stage, ignore_errors=True)
    with tarfile.open(CFG["tar"]) as t:
        t.extractall(stage, filter="data")
    manifest = json.load(open(os.path.join(stage, "manifest.json"), encoding="utf-8"))
    if os.path.normcase(lab) == os.path.normcase(tree):
        rep["result"] = "REFUSED - the lab tree is the one serving now; activate a new tag instead"
    else:
        shutil.rmtree(lab, ignore_errors=True)
        shutil.copytree(stock, lab)
        lib = os.path.join(lab, "lib", "ollama")
        removed = 0
        for p in glob.glob(os.path.join(lib, "*")):
            if os.path.isdir(p):
                shutil.rmtree(p)
                removed += 1
            elif p.lower().endswith((".dll", ".exe")):
                os.remove(p)
                removed += 1
        rep["steps"].append("removed %d stock engine entries from the lab tree" % removed)
        src = os.path.join(stage, "set")
        bad = []
        for rel, sha in manifest.items():
            if not rel.startswith("lib/ollama/"):
                continue
            s = os.path.join(src, *rel.split("/"))
            d = os.path.join(lab, *rel.split("/"))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            h = hashlib.sha256(open(d, "rb").read()).hexdigest()
            if h != sha:
                bad.append(rel)
        rep["steps"].append("laid in %d files, %d hash mismatches" % (len(manifest), len(bad)))
        rep["engine"] = engine(lab)
        rep["result"] = ("FAILED - hash mismatch: %s" % bad[:5] if bad else
                         "FAILED - no lab marker in the installed tree" if not rep["engine"]["lab"] else
                         "INSTALLED %s (not active yet)" % lab)
        shutil.rmtree(stage, ignore_errors=True)
elif ACTION in ("activate", "rollback"):
    if ACTION == "activate":
        stock = tree if "-lab" not in os.path.basename(tree) else tree[:tree.rindex("-lab")]
        target = lab_tree(stock, TAG)
        if not os.path.exists(os.path.join(target, "ollama.exe")):
            rep["result"] = "REFUSED - %s is not installed" % target
            print("@@" + json.dumps(rep))
            raise SystemExit(0)
        new, n = repoint(text, tree, target)
        bak = LAUNCHER + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + "-pre-lab" + TAG
    else:
        baks = sorted(glob.glob(LAUNCHER + ".bak-*-pre-lab*"), key=os.path.getmtime)
        if not baks:
            rep["result"] = "REFUSED - no pre-lab launcher backup to roll back to"
            print("@@" + json.dumps(rep))
            raise SystemExit(0)
        with open(baks[-1], encoding="utf-8-sig", errors="replace") as f:
            new = f.read()
        n, target = 1, active_tree(new)
        bak = LAUNCHER + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + "-pre-rollback"
    if n != 1:
        rep["result"] = "REFUSED - the launcher's active ollama line matched %d times (want 1)" % n
    else:
        shutil.copy2(LAUNCHER, bak)
        with open(LAUNCHER, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        rep["steps"].append("launcher %s -> %s (backup %s)" % (tree, target, os.path.basename(bak)))
        ver = restart(tree)
        exes = serving_exe()
        rep["serving"], rep["version"] = exes, ver
        ok = ver is not None and exes and all(os.path.normcase(e).startswith(os.path.normcase(target) + "\\")
                                              for e in exes)
        rep["engine"] = engine(target)
        rep["result"] = ("OK - serving from %s" % target if ok else
                         "CHECK - ollama %s, serving %s" % ("answers" if ver else "DOES NOT ANSWER", exes))
print("@@" + json.dumps(rep))
'''


def _remote(host, cfg):
    import inspect
    funcs = inspect.getsource(lab_tree) + "\n" + inspect.getsource(repoint)
    src = (REMOTE.replace("__CFG__", base64.b64encode(json.dumps(cfg).encode()).decode("ascii"))
           .replace("__FUNCS__", funcs).replace("__STAGE__", STAGE))
    r = subprocess.run(["ssh", host, "python.exe -"], input=src.encode("utf-8"), capture_output=True, timeout=1800)
    out = r.stdout.decode("utf-8", "replace")
    hit = [x for x in out.splitlines() if x.startswith("@@")]
    if not hit:
        return {"result": "NO REPLY", "stdout": out[-400:], "stderr": r.stderr.decode("utf-8", "replace")[-400:]}
    return json.loads(hit[-1][2:])


def stage_set(order_dir, set_name, tag):
    """One tar of a delivered set + its manifest, local temp. -> path."""
    meta = json.load(open(os.path.join(order_dir, "engine.json"), encoding="utf-8"))
    files = meta["sets"][set_name]["files"]
    tmp = os.path.join(tempfile.gettempdir(), "labengine-%s-%s.tar" % (set_name, tag))
    with tarfile.open(tmp, "w") as t:
        for rel in files:
            t.add(os.path.join(order_dir, set_name, *rel.split("/")), arcname="set/" + rel)
        man = os.path.join(tempfile.gettempdir(), "labengine-manifest-%s.json" % tag)
        with open(man, "w", encoding="utf-8") as f:
            json.dump(files, f)
        t.add(man, arcname="manifest.json")
    return tmp


def check(hosts=None):
    """Each GPU factory's serving engine + rigby's mounted CUDA engine: version and lab patch markers."""
    rows = {}
    for host in [h for h in SETS if not hosts or h in hosts]:
        rows[host] = _remote(host, {"launcher": LAUNCHERS[host], "action": "check", "markers": MARKERS})
    for host, rep in rows.items():
        e = rep.setdefault("engine", {})
        m = re.search(r"-lab(%s)$" % TAG_RE, str(e.get("tree") or ""))
        # include +compat in the tag: builds can share a tree name/commit but differ by the ollama compat layer, which is required to load ollama-format blobs
        e["tag"] = (m.group(1) + ("+compat" if e.get("compat") else "")) if m else "stock"
    if (not hosts or "tolkien" in hosts or "rigby" in hosts) and not (TABLET_HEAD and RIGBY_ENGINE and RIGBY_BUILD):
        rows["rigby"] = {"engine": {"error": "TABLET_HEAD_NAME / RIGBY_ENGINE_DIR / RIGBY_BUILD_DIR unset: no rigby engine "
                                             "on this head"}}
    elif not hosts or "tolkien" in hosts or "rigby" in hosts:
        # Tag identifies the specific build version; byte-comparison verifies the live binary matches that exact lab build, as marker presence alone is insufficient to distinguish builds (e.g., pre-parity builds with identical markers).
        cmd = ("for f in libllama-server-impl.so libllama-common.so libllama.so; do printf '%%s ' $f; "
               "grep -c -a -E '%s' %s/$f 2>/dev/null || echo 0; done; for d in %s; do cmp -s $d/libllama-server-impl.so "
               "%s/libllama-server-impl.so && echo TAG $d; done; echo COMPAT $(grep -a -c llama_ollama_compat "
               "%s/libllama.so)" % ("|".join(MARKERS), RIGBY_ENGINE, RIGBY_BUILD % "*", RIGBY_ENGINE, RIGBY_ENGINE))
        r = subprocess.run(["ssh", TABLET_HEAD, cmd], capture_output=True, text=True, timeout=60)
        marks = {l.split()[0]: int(l.split()[1]) for l in r.stdout.splitlines()
                 if len(l.split()) == 2 and l.split()[1].isdigit()}
        tags = re.findall(r"^TAG .*/llama\.cpp-(%s)/" % TAG_RE, r.stdout, re.M)
        cm = re.search(r"^COMPAT (\d+)", r.stdout, re.M)
        compat = int(cm.group(1)) if cm else 0
        rows["rigby"] = {"engine": {"tree": RIGBY_ENGINE, "markers": {k: v for k, v in marks.items() if v},
                                    "lab": any(marks.values()), "compat": compat,
                                    "tag": (tags[0] + ("+compat" if compat else "")) if tags else "unknown"}}
    return rows


def show(rows, judge=True):
    """Print rows; judge=True (a check's rows) also returns drift(). A deploy's rows carry no tag - its check judges."""
    for host, rep in rows.items():
        e = rep.get("engine") or {}
        print("-- %-10s engine %s  lab=%s tag=%s  %s" % (host, e.get("tree"), e.get("lab"), e.get("tag"),
                                                         (e.get("version") or "")[:60]))
        for k in ("result", "serving"):
            if rep.get(k):
                print("   %s: %s" % (k, rep[k]))
        for s in rep.get("steps") or []:
            print("   step  %s" % s)
    return drift(rows) if judge else 0


def drift(rows):
    """1 when the factories checked do not all run one lab build of one version (an unreadable tag is drift)."""
    lab = {h: (r.get("engine") or {}).get("lab") for h, r in rows.items()}
    tag = {h: (r.get("engine") or {}).get("tag") for h, r in rows.items()}
    if len(set(lab.values())) > 1:
        print("ENGINE DRIFT - lossless resume on %s only" % [h for h, v in lab.items() if v])
    if len(set(tag.values())) > 1 or set(tag.values()) & {"unknown", None}:
        print("ENGINE DRIFT - versions %s" % tag)
    return 1 if (len(set(lab.values())) > 1 or len(set(tag.values())) > 1
                 or set(tag.values()) & {"unknown", None}) else 0


# Engine identity: llama.cpp short commit + optional -r<N> for a lab revision.
# The revision number distinguishes distinct source trees on the same upstream commit,
# ensuring unique artifact naming across all build environments.
TAG_RE = r"[0-9a-f]{7,}(?:-r\d+)?"


def tag_of(order_dir):
    """Extracts the engine commit hash from `engine.json`'s `source` field.

    The lab tree on the target box is named `llama.cpp-<tag>`, so a new build must
    never inherit a previous build's tag to ensure the artifact name matches the
    correct source tree."""
    meta = json.load(open(os.path.join(order_dir, "engine.json"), encoding="utf-8"))
    m = re.search(r"llama\.cpp-(%s)[\\/]*$" % TAG_RE, str(meta.get("source") or ""))
    if not m:
        raise SystemExit("engine.json in %s has no llama.cpp-<commit> source - cannot name the lab tree" % order_dir)
    return m.group(1)


def revision_tag(order_dir, tag):
    """A tag given by hand for a delivered build -> that tag, only when it is the build's own commit with a lab revision
    (-r<N>) added: for a build whose source tree was renamed to its revision after the build. Anything else is refused -
    a hand-typed tag must not be able to name a build as another commit."""
    own = tag_of(order_dir)
    if tag != own and not re.fullmatch(re.escape(own.split("-r")[0]) + r"-r\d+", tag or ""):
        raise SystemExit("--engine-tag %r is not this build's commit %s with a lab revision (-r<N>)" % (tag, own))
    return tag


# How long an activation waits for the operator's click on Windows' own Allow prompt before it rolls the box back
# (operator_present): polls of lan_ok at 5 s. A bound on a wait for a PERSON, with the rollback as its safe end.
OPERATOR_WAIT_TRIES = 360


def deploy(order_dir, hosts=None, run_on=None, stop_deploy_requeue=None, deck_order=None, rollback=False, tag=None,
           operator_present=False):
    """Install + activate (or roll back) per GPU factory; ollama restarts under the factory's pause.
    `tag` defaults to the engine tag from engine.json (see TAG_RE).
    `operator_present`: If an operator is manually approving the Windows firewall prompt on a console-launch box,
    hosts with no existing rule are switched instead of HELD. The LAN check waits
    OPERATOR_WAIT_TRIES * 5s for approval before rolling back. Hosts with a BLOCK rule are still HELD
    because Windows does not re-prompt for blocked items. The deploy never writes firewall rules."""
    tag = (tag or tag_of(order_dir)) if not rollback else ""
    rows, bad = {}, 0
    for host in [h for h in SETS if not hosts or h in hosts]:
        base = {"launcher": LAUNCHERS[host], "markers": MARKERS, "tag": tag}
        awaiting_click = False
        if not rollback:
            tar = stage_set(order_dir, SETS[host], tag)
            remote_tar = STAGE + "\\" + os.path.basename(tar)
            subprocess.run(["ssh", host, "if not exist %s mkdir %s" % (STAGE, STAGE)], capture_output=True)
            subprocess.run(["scp", "-q", tar, "%s:%s" % (host, remote_tar.replace("\\", "/"))], check=True)
            rep = _remote(host, dict(base, action="install", tar=remote_tar))
            if not str(rep.get("result", "")).startswith("INSTALLED"):
                rows[host], bad = rep, bad + 1
                continue
            fw = firewall_verdict(host, rep["lab_tree"] + "\\ollama.exe")
            awaiting_click = fw == "none" and host in CONSOLE_LAUNCH and operator_present
            if fw == "block" or (fw == "none" and host in CONSOLE_LAUNCH and not operator_present):
                # checked BEFORE the switch: activating would cut the box off the LAN until someone answers a prompt
                rows[host] = dict(rep, result="HELD: %s has %s for %s\\ollama.exe - activating would cut the box "
                                              "off the LAN. A human runs on %s: %s  - then re-run this deploy" % (
                                                  host, "a firewall BLOCK rule" if fw == "block" else "no firewall "
                                                  "Allow rule (and launches ollama as the console user, so Windows "
                                                  "prompts and an unanswered prompt becomes Block)", rep["lab_tree"],
                                                  host, (FIREWALL_FIX if fw == "block" else FIREWALL_ADD)
                                                  % (rep["lab_tree"] + "\\ollama.exe")))
                bad += 1
                continue
        action = dict(base, action="rollback" if rollback else "activate")
        box = {"host": host, "root": "", "task": "", "local": host.lower() == os.environ.get("COMPUTERNAME", "").lower()}
        name = deck_order(host) if deck_order else ""
        if name and stop_deploy_requeue:
            def run(b, cfg, _a=action):
                return run_on(b, cfg) if cfg.get("action") in ("stop", "requeue") else _remote(host, _a)
            from fleet_deploy import FLEET
            fb = next(b for b in FLEET if b["host"] == host)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            cfg = {"root": fb["root"], "task": fb["task"], "stamp": stamp, "holders": [], "deploy": True,
                   "restart": False, "files": {}}
            rep = stop_deploy_requeue(fb, cfg, name, stamp, run=run)
            # Stop slip exit requires an explicit restart; fleet_deploy's auto-restart does not apply here
            st = subprocess.run(["ssh", host, 'schtasks /run /tn "%s"' % fb["task"]], capture_output=True, text=True,
                                timeout=60)
            rep.setdefault("steps", []).append("runner task %s: %s" % (fb["task"], (st.stdout or st.stderr).strip()))
        else:
            rep = _remote(host, action)
        waiting = not rollback and awaiting_click
        if waiting and str(rep.get("result", "")).startswith("OK"):
            print("WAITING: %s now serves from the new tree on-box. Windows is asking on ITS DESKTOP whether to allow "
                  "ollama.exe: until Allow is clicked there the LAN cannot reach it. This waits %d min for the click, "
                  "then rolls the box back by itself." % (host, OPERATOR_WAIT_TRIES * 5 // 60), flush=True)
        if not rollback and str(rep.get("result", "")).startswith("OK") and not (
                lan_ok(host, tries=OPERATOR_WAIT_TRIES) if waiting else lan_ok(host)):
            # serving on-box is not serving the fleet: Kirk and the desk reach ollama over the LAN
            back = _remote(host, dict(base, action="rollback"))
            rep = dict(rep, result="FAILED: %s serves on-box but the LAN cannot reach :11434 - a Windows Firewall "
                                   "BLOCK rule for the new exe (RUNBOOK-Ollama-Host-Operations upgrade traps). Rolled "
                                   "back: %s. A human runs on %s: %s" % (
                                       host, back.get("result"), host, FIREWALL_FIX % lab_exe(rep)))
        rows[host] = rep
        bad += 0 if str(rep.get("result", "")).startswith("OK") else 1
    if not hosts or "tolkien" in hosts or "rigby" in hosts:
        rows["rigby"] = rigby_swap(tag, rollback)
        bad += 0 if rows["rigby"]["result"].startswith("OK") else 1
    return rows, bad


def lan_ok(host, tries=6, wait_s=5):
    """ollama answering on the LAN address (curl from Softserve). A box with no address in the
    fleet table cannot be checked, so it is reported as not-ok rather than guessed."""
    addr = LAN.get(host, "")
    if not addr:
        return False
    for i in range(tries):
        try:
            with urllib.request.urlopen("http://%s:11434/api/version" % addr, timeout=6) as r:
                if r.status == 200:
                    return True
        except Exception:                                            # noqa: BLE001
            pass
        if i < tries - 1:
            time.sleep(wait_s)
    return False


def firewall_verdict(host, exe):
    """'allow' / 'block' / 'none' for inbound rules on this exact program path; 'unknown' when it could not be asked
    (treated as none - a console-launch box then holds, the others proceed and the LAN gate still checks after)."""
    cmd = base64.b64encode((FW_QUERY % exe.replace("'", "''").lower()).encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["ssh", host, "powershell -NoProfile -EncodedCommand " + cmd], capture_output=True,
                           text=True, timeout=60)
    except Exception:                                                # noqa: BLE001
        return "none"
    m = re.search(r"^FW (\w+)", r.stdout, re.M)
    return m.group(1) if m else "none"


def lab_exe(rep):
    serving = rep.get("serving") or []
    return serving[0] if serving else (rep.get("engine") or {}).get("tree", "<lab tree>") + "\\ollama.exe"


def rigby_swap(tag, rollback=False):
    """rigby's mounted CUDA bin <- its in-place lab build (or the newest archived bin). A running CUDA llama process
    is a busy lane: WAIT, nothing touched - Tolkien drains its own work, re-run the deploy after."""
    if not (TABLET_HEAD and RIGBY_ENGINE and RIGBY_BUILD and RIGBY_ARCHIVE):
        return {"result": "REFUSED: TABLET_HEAD_NAME / RIGBY_ENGINE_DIR / RIGBY_BUILD_DIR / RIGBY_ENGINE_ARCHIVE_PREFIX unset "
                          "(no rigby engine on this head)",
                "steps": [], "engine": {"tree": ""}}
    args = [RIGBY_ENGINE, RIGBY_BUILD % tag, RIGBY_ARCHIVE, "rollback" if rollback else "install", "|".join(MARKERS)]
    # BYTES on stdin: text mode on Windows writes \r\n and bash on rigby reads `set -eu\r`
    r = subprocess.run(["ssh", TABLET_HEAD, "bash -s -- " + " ".join("'%s'" % a for a in args)],
                       input=RIGBY_SWAP.encode("ascii"), capture_output=True, timeout=300)
    out = [l for l in r.stdout.decode("utf-8", "replace").splitlines() if l.strip()]   # the verdict: its last line
    err = [l for l in r.stderr.decode("utf-8", "replace").splitlines() if l.strip() and "post-quantum" not in l
           and "store now" not in l and "upgraded" not in l]
    return {"result": out[-1] if out else "FAILED: no output (rc %s)" % r.returncode, "steps": out[:-1] + err,
            "engine": {"tree": RIGBY_ENGINE}}
