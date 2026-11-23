#!/usr/bin/env python3
"""The lab's repos in the morning health embed: anything uncommitted, untracked or unpushed, by repo.

    REPO_STATE_REPOS="<path> <path> ..." python3 repo_state_field.py

Prints one field for the 4:30 stack health check (stack-health-check.sh passes it through the environment). A repo
that cannot be read says so; nothing is quiet, and the field names how many repos it read, so "all clean" is a count,
not an absence. Unpushed means ahead of the branch's upstream; a repo with no upstream is backed up as a snapshot by
the weekly code backup, so only its working tree is judged."""
import os
import subprocess
import sys

MAX_CHARS = 1024     # a Discord embed field's limit
# Repos under this folder belong to Windows git, whose global default is core.autocrlf=true, so a repo that does not set
# its own is read with that default: over the share, a Linux git would otherwise call every CRLF file changed. A
# repo's own setting always wins, as it does on Windows.
WINDOWS_ROOT = os.environ.get("REPO_STATE_WINDOWS_ROOT", "")


def _git(path, *args, crlf=None):
    # --no-optional-locks: status never rewrites the index, so the reader cannot change what it reads (a status from
    # another machine's git would otherwise refresh the index with its own view of the files)
    base = ["git", "--no-optional-locks", "-c", "safe.directory=*"]
    if crlf is None and WINDOWS_ROOT and os.path.normpath(path).startswith(os.path.normpath(WINDOWS_ROOT) + os.sep):
        own = subprocess.run(base + ["-C", path, "config", "--local", "--get", "core.autocrlf"], capture_output=True,
                             text=True, timeout=60)
        crlf = [] if own.stdout.strip() else ["-c", "core.autocrlf=true"]
    return subprocess.run(base + (crlf or []) + ["-C", path, *args], capture_output=True, text=True, timeout=120)


def repo_state(path):
    """(name, None or a problem string) for one repo."""
    name = os.path.basename(os.path.normpath(path)) or path
    try:
        st = _git(path, "status", "--porcelain")
        if st.returncode:
            return name, "could not tell (%s)" % (st.stderr.strip().splitlines() or ["git failed"])[-1][:80]
        lines = [ln for ln in st.stdout.splitlines() if ln.strip()]
        changed = sum(1 for ln in lines if not ln.startswith("??"))
        untracked = sum(1 for ln in lines if ln.startswith("??"))
        ahead = 0
        up = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if up.returncode == 0:
            count = _git(path, "rev-list", "--count", "@{u}..HEAD")
            ahead = int(count.stdout.strip() or 0) if count.returncode == 0 else -1
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return name, "could not tell (%s)" % type(exc).__name__
    parts = []
    if changed:
        parts.append("%d uncommitted" % changed)
    if untracked:
        parts.append("%d untracked" % untracked)
    if ahead < 0:
        parts.append("unpushed count unreadable")
    elif ahead:
        parts.append("%d unpushed" % ahead)
    return name, ", ".join(parts) or None


def field(paths):
    if not paths:
        return "No repos named (REPO_STATE_REPOS is empty) - could not tell."
    states = [repo_state(p) for p in paths]
    bad = [(n, s) for n, s in states if s]
    if not bad:
        return "All %d repos committed and pushed." % len(states)
    text = "⚠ " + "\n⚠ ".join("%s: %s" % (n, s) for n, s in bad)
    text += "\n%d of %d repos clean. Uncommitted or unpushed work is the finder's to resolve." % (
        len(states) - len(bad), len(states))
    return text.replace("\\", "/").replace("'", "")[:MAX_CHARS]    # pasted into a python string by the health check


if __name__ == "__main__":
    print(field(os.environ.get("REPO_STATE_REPOS", "").split()))
    sys.exit(0)
