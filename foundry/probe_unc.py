"""UNC reach probe (30b goldcal pre-check): can THIS factory runner's own logon context read the
handoff share? An ssh probe proves nothing about the runner's session (the Mothership SMB lesson:
network logons see neither mapped drives nor Credential Manager), so the question is asked as a
compute order and answered from inside the runner's process tree.

`python probe_unc.py <dir>` -> rc 0 REACHABLE (+ entry count), rc 2 UNREACHABLE with the named
OSError. Read-only; touches nothing.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

path = sys.argv[1]
try:
    names = os.listdir(path)
except OSError as e:
    raise SystemExit("UNREACHABLE (%s): %s -- %s" % (type(e).__name__, e, path))
print("REACHABLE: %s -- %d entries, sample %s" % (path, len(names), sorted(names)[:4]))
