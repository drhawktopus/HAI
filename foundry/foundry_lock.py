"""Verify singleton lock liveness by comparing process start time against lock file mtime.

Prevents stale locks after PID reuse (e.g., Windows reboot recycling PIDs).
If the current owner's start time is earlier than the lock file's modification
time, it is genuine. If later, the PID was recycled and the lock is stale.
Reclaims on ambiguity ('cannot tell') to avoid indefinite guard downtime,
as brief double-polls are less costly than frozen supervisors.
"""
import os
import subprocess

__all__ = ["proc_start_epoch", "holder_is_live"]

# Emits the process's creation time as a Unix timestamp, or nothing at all when there is no such
# process. Single-quoted -Filter on purpose: a double-quoted one has to survive Python, and the
# lab has paid for backslash/quote mangling through shell layers more than once.
_PS_START = (
    "$p = Get-CimInstance Win32_Process -Filter 'ProcessId=%d' -ErrorAction SilentlyContinue; "
    "if ($p) { [int64]([DateTimeOffset]$p.CreationDate).ToUnixTimeSeconds() }"
)


def proc_start_epoch(pid, timeout=30):
    """Process start time as epoch seconds, or None when it cannot be read (no such process, or
    the query failed). None is NOT "alive" and NOT "dead" -- the caller decides, and every caller
    here treats it as reclaimable."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", _PS_START % pid],
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    out = (r.stdout or "").strip()
    if not out:
        return None
    try:
        return float(out.splitlines()[0].strip())
    except ValueError:
        return None


def holder_is_live(lock_path, my_pid=None, _start_epoch=None, _mtime=None):
    """(live, reason) for the process named in `lock_path`.

    `live` is True ONLY when a process with that PID exists AND started before the lock was
    written. `reason` is a short phrase built from what was actually observed, so a caller can log
    the finding instead of asserting a state it never measured.

    The two underscore params are seams for the pins: they let a test supply a start time and an
    mtime without inventing a process, so the RECYCLED-PID case can be exercised for real.
    """
    if my_pid is None:
        my_pid = os.getpid()
    try:
        with open(lock_path, encoding="utf-8") as fh:
            raw = fh.read().strip()
    except OSError as exc:
        return False, "lock unreadable (%s)" % type(exc).__name__
    if not raw.isdigit():
        return False, "lock holds no pid (%r)" % raw[:32]
    pid = int(raw)
    if pid == my_pid:
        return False, "lock names this very process"

    if _mtime is None:
        try:
            _mtime = os.path.getmtime(lock_path)
        except OSError as exc:
            return False, "lock mtime unreadable (%s)" % type(exc).__name__

    start = proc_start_epoch(pid) if _start_epoch is None else _start_epoch
    if start is None:
        return False, "pid %d is not running" % pid
    if start > _mtime:
        # The whole point. Do not soften this with slack: a holder writes its lock milliseconds
        # after starting, so a start time LATER than the mtime is not timing jitter, it is a
        # different process wearing the same number.
        return False, ("pid %d started %.0fs AFTER the lock was written - recycled pid, "
                       "not the holder" % (pid, start - _mtime))
    return True, "pid %d started %.0fs before the lock was written" % (pid, _mtime - start)
