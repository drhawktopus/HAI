"""Add LLAMA_ARG_SLOT_SAVE_PATH to a GPU box's ollama launcher (ollama 0.40.5's bundled llama-server reads it, so
every runner gets slot save/restore). Run ON the box: `ssh <box> "python.exe -" < ollama_slotpath.py`, with the
launcher's path in env OLLAMA_LAUNCHER (default: LAUNCHER below).
Backs the launcher up, inserts one line before the first `$ollama =` / `Start-Process` line keeping that line's own
ending (launchers mix CRLF and LF), creates the slot dir, prints the env lines. Idempotent: a launcher that already has
the line is left alone. Takes effect at the next ollama restart (the `Ollama Serve` task).
Put the slot folder on a data drive, not the system drive: a save is gigabytes, and a big write to C: can stall it."""
import datetime
import os
import shutil

LAUNCHER = os.environ.get("OLLAMA_LAUNCHER") or r"C:\ProgramData\HAI\start-ollama-system.ps1"
SLOTS = r"C:\ProgramData\HAI\slots"
LINE = "$env:LLAMA_ARG_SLOT_SAVE_PATH = '%s'" % SLOTS

text = open(LAUNCHER, "rb").read().decode("utf-8")
if "LLAMA_ARG_SLOT_SAVE_PATH" in text:
    print("already set")
else:
    bak = LAUNCHER + ".bak-%s-pre-slotsave" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(LAUNCHER, bak)
    lines = text.split("\n")
    idx = [i for i, l in enumerate(lines) if l.strip().startswith(("$ollama =", "$ollama=", "Start-Process"))]
    assert idx, "no `$ollama =` / Start-Process line to insert before"
    i = idx[0]
    cr = "\r" if lines[i].endswith("\r") else ""
    lines.insert(i, LINE + cr)
    open(LAUNCHER, "wb").write("\n".join(lines).encode("utf-8"))
    print("backup", bak)
os.makedirs(SLOTS, exist_ok=True)
for n, l in enumerate(open(LAUNCHER, encoding="utf-8").read().split("\n"), 1):
    if "env:" in l or l.strip().startswith(("$ollama", "Start-Process")):
        print(n, repr(l[:140]))
print("slots dir", os.path.isdir(SLOTS))
