"""Tests for p20_apply_hard_exit.py, against run_orig.py: the trainer's run.py before the patch (upstream, CRLF as the
Windows checkout has it).

1. The applier: applies once, is idempotent, keeps a backup, refuses an unknown run.py, reverts byte-identically.
2. The behaviour: the patched run.py's tail, with main() swapped for each ending a trainer can have, run as a real
   process. Exit code must be what Python itself would have returned; printed output must arrive; and an atexit
   handler must NOT run - the proof that interpreter shutdown (where the hang lives) is skipped."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APPLIER = os.path.join(HERE, "p20_apply_hard_exit.py")
ORIG = os.path.join(HERE, "run_orig.py")
fails, n = [], [0]


def check(name, ok, detail=""):
    n[0] += 1
    print("  %s  %s%s" % ("ok" if ok else "FAIL", name, "" if ok else "  -> %r" % (detail,)))
    if not ok:
        fails.append(name)


def apply(target, *extra):
    r = subprocess.run([sys.executable, APPLIER, "--target", target] + list(extra), capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


tmp = tempfile.mkdtemp(prefix="p20exit_")
t = os.path.join(tmp, "run.py")
shutil.copy2(ORIG, t)
orig = open(ORIG, "rb").read()

print("== applier ==")
rc, out = apply(t)
patched = open(t, "rb").read()
check("applies to the real run.py", rc == 0 and b"[P20-EXIT]" in patched, out)
check("keeps a byte-identical backup", open(t + ".p20bak-exit", "rb").read() == orig)
check("everything before main() is untouched",
      patched.startswith(orig[:orig.index(b"if __name__ == '__main__':")]))
check("CRLF throughout, no bare LF", b"\n" not in patched.replace(b"\r\n", b""))
rc, out = apply(t)
check("second run is a no-op", rc == 0 and "already applied" in out and open(t, "rb").read() == patched, out)
odd = os.path.join(tmp, "odd.py")
open(odd, "wb").write(orig.replace(b"    main()\r\n", b"    main()  # changed\r\n"))
rc, out = apply(odd)
check("refuses a run.py whose ending it does not know", rc == 2 and "REFUSED" in out, out)
rc, out = apply(t, "--revert")
check("revert restores the original bytes", rc == 0 and open(t, "rb").read() == orig, out)

print("== behaviour: the patched tail as a real process ==")
tail = patched[patched.index(b"def _p20_hard_exit"):].decode("utf-8")
HEAD = ("import os, sys, atexit\n"
        "def print_acc(*a): print(*a, flush=True)\n"
        "atexit.register(lambda: print('ATEXIT RAN', flush=True))\n")
# positive control for the instrument: WITHOUT the patch, the same atexit marker must appear, or "did not run" below
# would mean nothing
_c = os.path.join(tmp, "control.py")
open(_c, "w", encoding="utf-8").write(HEAD + "def main():\n    print('trained', flush=True)\n\n"
                                      "if __name__ == '__main__':\n    main()\n")
_r = subprocess.run([sys.executable, _c], capture_output=True, text=True, timeout=60)
check("CONTROL: unpatched, interpreter shutdown runs (atexit marker appears)", "ATEXIT RAN" in _r.stdout, _r.stdout)
CASES = [
    ("a normal finish", "def main():\n    print('trained', flush=True)\n", 0),
    ("a STOP_NOW / KeyboardInterrupt path (sys.exit(0))", "def main():\n    sys.exit(0)\n", 0),
    ("sys.exit(3)", "def main():\n    sys.exit(3)\n", 3),
    ("an uncaught error", "def main():\n    raise ValueError('boom')\n", 1),
    ("sys.exit('message')", "def main():\n    sys.exit('bad config')\n", 1),
]
for name, body, want in CASES:
    p = os.path.join(tmp, "case.py")
    open(p, "w", encoding="utf-8", newline="\r\n").write(HEAD + body + "\n\n" + tail.replace("\r\n", "\n"))
    r = subprocess.run([sys.executable, p], capture_output=True, text=True, timeout=60)
    both = r.stdout + r.stderr
    check("%s -> exit %d" % (name, want), r.returncode == want, (r.returncode, both[-300:]))
    check("%s: the [P20-EXIT] line arrives" % name, "[P20-EXIT] job returned (exit code %d)" % want in both, both[-300:])
    check("%s: interpreter shutdown skipped (atexit did not run)" % name, "ATEXIT RAN" not in both, both[-300:])
    if want == 1 and "raise" in body:
        check("an uncaught error still prints its traceback", "ValueError: boom" in both, both[-300:])
    if "bad config" in body:
        check("sys.exit('message') still prints the message", "bad config" in both, both[-300:])
    if "trained" in body:
        check("output printed during the job arrives", "trained" in r.stdout, r.stdout)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("FAILURES: %d - %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("ALL GREEN (%d cases)" % n[0])
