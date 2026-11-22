"""Live generation rate (tok/s) on a fleet box, read from the ollama server's own per-request timing lines.

    python live_rate.py <box>                  last 10 answers + median
    python live_rate.py <box> --min 50         only answers of >= 50 generated tokens

Every request ollama serves logs `slot print_timing: ... eval time = X ms / N tokens (... T tokens per second)`;
that T is the decode rate the menu's answers ran at. Requests under --min tokens (health probes, 2-token
greedy replies, tool-call stubs) are skipped because their rate is dominated by fixed overhead.
Exit 0 = rates printed, 3 = the log parsed but no answer met --min yet, 2 = unreadable (never a rate)."""
import argparse
import re
import statistics
import subprocess
import sys

import lab_settings as LS   # noqa: E402  where each box's model server logs is a setting
LOGS = LS.setting_json("ENGINE_SERVE_LOGS", {}) or {}
if not LOGS:
    sys.exit("no ENGINE_SERVE_LOGS setting: {box: the model server's log path} goes in %s" % LS.settings_file())
PAT = re.compile(r"task\s+(\d+)\s*\|\s*(prompt eval|eval) time =\s*([\d.]+) ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")


def read(host, tail):
    cmd = ("powershell -NoProfile -Command \"Get-Date -Format HH:mm:ss; Get-Content '%s' -Tail %d | "
           "Select-String -Pattern 'print_timing|GIN' | ForEach-Object { $_.Line }\""
           % (LOGS[host], tail))
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", host, cmd], capture_output=True, timeout=60)
    return [ln for ln in r.stdout.decode("utf-8", "replace").splitlines() if ln.strip() and not ln.startswith("**")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", choices=sorted(LOGS))
    ap.add_argument("--min", type=int, default=20, help="minimum generated tokens for an answer to count")
    ap.add_argument("--last", type=int, default=10)
    ap.add_argument("--tail", type=int, default=5000, help="serve-log lines to scan")
    a = ap.parse_args()
    lines = read(a.host, a.tail)
    if not lines:
        print("UNREADABLE - no output from %s's serve log" % a.host)
        return 2
    rows = [m.groups() for m in map(PAT.search, lines[1:]) if m]
    access = [ln for ln in lines[1:] if "[GIN]" in ln]
    asks = sum(1 for ln in access if "/api/chat" in ln or "/api/generate" in ln)
    print("%s as of %s: %s, %d timing lines parsed" % (
        a.host, lines[0], "%d generation requests" % asks if access else "no request log", len(rows)))
    if not rows and access and not asks:
        print("no generation served in the last %d log lines (only metadata calls) - nothing to rate yet" % a.tail)
        return 3
    if not rows:
        print("UNREADABLE - %d generation requests but zero timing lines; the log format changed" % asks)
        return 2
    gen = [(int(t), int(n), float(r)) for t, kind, ms, n, r in rows if kind == "eval" and int(n) >= a.min]
    if not gen:
        print("no answer of >= %d generated tokens in the last %d log lines yet" % (a.min, a.tail))
        return 3
    for t, n, r in gen[-a.last:]:
        print("  task %-7d %6d tok  %6.1f tok/s" % (t, n, r))
    last = [r for _, _, r in gen[-a.last:]]
    print("median of last %d: %.1f tok/s (range %.1f-%.1f)" % (len(last), statistics.median(last), min(last), max(last)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
