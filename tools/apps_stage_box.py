"""Stages allowed APP updates on a Windows box via SSH. Runs `winget upgrade` for IDs in the allow-list and optionally triggers a Microsoft Store scan. Never touches non-list packages (e.g., Ollama, CUDA, drivers)."""
import base64
import json
import re
import subprocess
import sys

try:                                  # the driver replaces __CFG__ with base64 JSON; run bare, there is nothing to do
    CFG = json.loads(base64.b64decode("__CFG__").decode("utf-8"))
except Exception:                                                                  # noqa: BLE001
    CFG = {"allow": [], "store": False}


# Windows console children spawn visible windows; CREATE_NO_WINDOW keeps subprocesses headless without breaking pipes.
NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" else {}


def winget(*args, timeout=900):
    r = subprocess.run(["winget"] + list(args), capture_output=True, timeout=timeout, **NO_WINDOW)
    return r.returncode, r.stdout.decode("utf-8", "replace").replace("\r", "\n")


def table(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
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


out = {"installed": [], "failed": [], "skipped": [], "store": None}
rc, text = winget("upgrade", "--disable-interactivity", "--accept-source-agreements")
offered = table(text)
out["offered"] = len(offered)
out["source_ok"] = "Failed when searching source" not in text
allow = set(CFG.get("allow") or [])
for pid, (have, want) in sorted(offered.items()):
    if pid not in allow:
        out["skipped"].append("%s %s -> %s" % (pid, have, want))
        continue
    rc, t = winget("upgrade", "--id", pid, "--exact", "--silent", "--accept-source-agreements",
                   "--accept-package-agreements", "--disable-interactivity")
    tail = " ".join(t.split())[-200:]
    if rc == 0:                      # winget's exit code is the verdict; its text says if a restart finishes the install
        note = " (restart required to finish)" if re.search(r"\bRestart\b", t) else ""
        out["installed"].append("%s %s -> %s%s" % (pid, have, want, note))
    else:
        # 0x8A150006 = the installer itself failed (its exit code is in the text); 0x8A15010C = cancelled; both are
        # usually "the app is running" during a daytime run - at 03:40 they retry on their own
        out["failed"].append("%s %s -> %s: 0x%08X %s" % (pid, have, want, rc & 0xFFFFFFFF, tail))
if CFG.get("store"):
    ps = ("$r = Get-CimInstance -Namespace 'root/cimv2/mdm/dmmap' -ClassName 'MDM_EnterpriseModernAppManagement_AppManagement01' "
          "-ErrorAction Stop | Invoke-CimMethod -MethodName UpdateScanMethod; 'ReturnValue=' + $r.ReturnValue")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=300,
                       **NO_WINDOW)
    out["store"] = (r.stdout + r.stderr).strip()[-200:] or "no output (rc %d)" % r.returncode
out["done"] = True
if CFG.get("result"):            # task mode (BigHonker: winget needs the user's OWN session, not ssh): the file is the answer
    tmp = CFG["result"] + ".tmp"
    open(tmp, "w", encoding="utf-8").write(json.dumps(out))
    import os
    os.replace(tmp, CFG["result"])
print("@@" + json.dumps(out))
