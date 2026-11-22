"""Verify that long-lived processes are running the latest code on disk.

Long-lived processes (e.g., `run_queue`) import modules at startup and do not
see subsequent file changes. A successful `scp` updates disk mtime but does not
update memory; the process continues executing stale bytecode until restarted.

Checks if any process holding a handle to the module predates the file's mtime.
Run this after deploying modules owned by long-lived processes.

Args:
    path: Paths to check. Defaults to the foundry module if unspecified.

Returns:
    0 if running code matches disk.
    1 if a process is running stale code (restart required).
"""
import json
import os
import re
import subprocess
import sys
import time

FOUNDRY_ROOT = os.environ.get("FOUNDRY_ROOT") or os.path.join("F:" + os.sep, "foundry")
DEFAULT = [os.path.join(FOUNDRY_ROOT, "bin", "p20_foundry.py")]

PS = ("Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
      "Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress")


def python_procs():
    """[(pid, start_epoch, cmdline)] for every running python."""
    out = subprocess.run(["powershell", "-NonInteractive", "-Command", PS],
                         capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
    except Exception:
        return []
    d = d if isinstance(d, list) else [d]
    rows = []
    for p in d:
        m = re.search(r"/Date\((\d+)\)/", str(p.get("CreationDate")))
        rows.append((p.get("ProcessId"), int(m.group(1)) / 1000.0 if m else 0.0,
                     p.get("CommandLine") or ""))
    return rows


def check(path):
    if not os.path.exists(path):
        print("  UNCHECKABLE  %s  (not present from here -- wrong box, or wrong FOUNDRY_ROOT)"
              % path)
        return None
    mt = os.path.getmtime(path)
    base = os.path.basename(path)
    # Exclude self and other copies of this tool from the process scan,
    # as their command lines contain the target basename, creating false positives.
    holders = [r for r in python_procs()
               if base in r[2] and r[0] != os.getpid() and "verify_deploy" not in r[2]]
    print("  %s  (deployed %s)" % (base, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mt))))
    if not holders:
        print("      no running process holds it -- next start picks it up")
        return True
    ok = True
    for pid, ct, _cl in holders:
        stale = ct < mt
        ok = ok and not stale
        print("      PID %-7s started %s  %s"
              % (pid, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ct)),
                 "STALE -- running PRE-DEPLOY code, restart required" if stale else "fresh"))
    return ok


if __name__ == "__main__":
    targets = sys.argv[1:] or DEFAULT
    print("deploy verification (FOUNDRY_ROOT=%s)" % FOUNDRY_ROOT)
    results = [check(t) for t in targets]
    print("")
    if any(r is False for r in results):
        # A real stale process anywhere is the loudest state and wins.
        print("VERDICT: STALE PROCESS -- the fix is NOT live")
        sys.exit(1)
    if any(r is None for r in results):
        # exit 2, distinct from both 0 and 1: a caller that treats "could not tell" as either
        # "verified" or "broken" is exactly the bug this three-state split exists to prevent.
        print("VERDICT: INCONCLUSIVE -- could not see %d target(s) from this box; nothing was "
              "measured, so nothing is claimed. Run it ON the factory, or set FOUNDRY_ROOT."
              % sum(1 for r in results if r is None))
        sys.exit(2)
    print("VERDICT: all running code is post-deploy")
    sys.exit(0)
