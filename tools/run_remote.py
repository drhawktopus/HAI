"""Run a root script on one of the lab's Linux boxes (RUN_REMOTE_HOSTS), detached, and report its log.

    python run_remote.py <host> <local-script.sh> [wait_seconds] [--args "<script args>"]

Usage constraints:
- Do not hand-roll ssh+sudo with credentials in argv or shell strings. This tool
  passes the credential via sudo's STDIN (never printed, never in argv, never on disk).
- The remote script MUST print a 'DONE' marker as its last line. Completion is detected
  by this marker, not by timeout. Timeouts exit with code 3 (INCONCLUSIVE)."""
import io
import os
import re
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import lab_settings as S

# The boxes this tool may run on (names only, never IPs: `sshto.resolve()` handles dynamic addressing) and the
# credential locker it reads the sudo password from: both are settings (RUN_REMOTE_HOSTS, LAB_CREDENTIAL_LOCKER).
IN_SCOPE = {h.strip() for h in S.setting("RUN_REMOTE_HOSTS", "").split(",") if h.strip()}
LOCKER = S.setting("LAB_CREDENTIAL_LOCKER", "")
if not IN_SCOPE or not LOCKER:
    sys.exit("set RUN_REMOTE_HOSTS (the boxes this tool may run on) and LAB_CREDENTIAL_LOCKER (the locker file)")

argv = sys.argv[1:]
script_args = ""
if "--args" in argv:
    i = argv.index("--args")
    script_args = argv[i + 1] if len(argv) > i + 1 else ""
    del argv[i:i + 2]
    # The value is interpolated into a remote shell string. Restrict the charset rather than
    # attempting to quote it -- a narrow allow-list cannot become an injection seam.
    if not re.fullmatch(r"[A-Za-z0-9,._:=/-]*", script_args):
        sys.exit("ABORT: --args may only contain [A-Za-z0-9,._:=/-], got %r" % script_args)

if len(argv) < 2:
    sys.exit(__doc__)
host, local = argv[0], argv[1]
wait_s = int(argv[2]) if len(argv) > 2 else 300

if host not in IN_SCOPE:
    sys.exit("ABORT: %r is out of scope - only %s" % (host, ", ".join(IN_SCOPE)))
if not os.path.isfile(local):
    sys.exit("ABORT: no such script %r" % local)
remote = "/tmp/" + os.path.basename(local)
log = remote.rsplit(".", 1)[0] + ".log"


def locker_password():
    for line in open(LOCKER, encoding="utf-8", errors="replace").read().splitlines():
        if re.search(r"base standard", line, re.I) and "`" in line:
            vals = re.findall(r"`([^`]+)`", line)
            if len(vals) != 1:
                raise SystemExit("ABORT: locker line holds %d backticked values, expected 1" % len(vals))
            return vals[0]
    raise SystemExit("ABORT: base standard password not found in the locker")


pw = locker_password()
print("locker : credential loaded (%d chars, not printed)" % len(pw))

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sshto
    addr, banner = sshto.resolve(host)
    if not addr:
        sys.exit("ABORT: %s answers on 22 at NONE of its known addresses (%s).\n"
                 "That is 'unreachable by this method', not 'the box is down'. Sweep with\n"
                 "  python lansweep.py\n"
                 "and add the address to CANDIDATES in sshto.py."
                 % (host, ", ".join(sshto.CANDIDATES.get(host, []))))
    print("resolve: %s -> %s (%s)" % (host, addr, banner))
except ImportError:
    # The scp/ssh calls below use the ssh ALIAS, whose ProxyCommand runs sshto.py as a
    # subprocess -- resolution still works without the import; only this pre-check is lost.
    print("resolve: sshto.py not importable - skipping the reachability pre-check; the ssh "
          "alias still resolves via ProxyCommand")

p = subprocess.run(["scp", "-o", "ConnectTimeout=10", local, "%s:%s" % (host, remote)],
                   capture_output=True, text=True, timeout=120)
if p.returncode != 0:
    sys.exit("ABORT staging: %s" % (p.stderr or "").strip()[:250])
print("staged : %s -> %s:%s" % (os.path.basename(local), host, remote))

cmd = ("sudo -S -p '' bash -c \"setsid bash %s %s >%s 2>&1 </dev/null & disown; exit 0\""
       % (remote, script_args, log))
p = subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, cmd],
                   input=pw + "\n", capture_output=True, text=True, timeout=60)
print("launch : rc=%d %s" % (p.returncode, (p.stderr or "").strip()[:120]))

# Completion is the script's own DONE marker. A timeout is an INCONCLUSIVE, never a pass.
print("\nwaiting for the DONE marker in %s (up to %d s)" % (log, wait_s))
deadline = time.time() + wait_s
text, done = "", False
while time.time() < deadline:
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", host, "cat %s 2>/dev/null" % log],
                       capture_output=True, text=True, timeout=45)
    text = r.stdout or ""
    if "DONE" in text:
        done = True
        break
    time.sleep(6)

print("=" * 78)
print(text.strip() or "(log empty)")
print("=" * 78)
if done:
    print("VERDICT: script reached its DONE marker")
else:
    print("VERDICT: INCONCLUSIVE - no DONE marker within %d s. The log above may be partial;"
          " this is NOT evidence the work failed." % wait_s)
    sys.exit(3)
