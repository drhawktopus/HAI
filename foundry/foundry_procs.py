"""Factory process control for long-lived loops.

Usage (pipe to remote Python via SSH):
    cat foundry_procs.py | ssh host "python.exe -"          # list
    cat foundry_procs.py | ssh host "python.exe -" --kill   # kill matched processes
    cat foundry_procs.py | ssh host "python.exe -" --count  # count only

Note: Must be executed via `python.exe -` to avoid shell-layer quoting issues
with PowerShell arguments across SSH.
"""
import subprocess
import sys

MATCH = "p20_foundry|stall_rescue|render_worker"
KILL_MATCH = "p20_foundry|stall_rescue"     # never the render worker: it is its own singleton


def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True, timeout=90)


LIST = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '%s' "
        "-and $_.CommandLine -notmatch 'Get-CimInstance' } | ForEach-Object { "
        "'{0}|{1}|{2}' -f $_.ProcessId, $_.CreationDate, "
        "$_.CommandLine.Substring(0,[Math]::Min(70,$_.CommandLine.Length)) }") % MATCH

arg = sys.argv[1] if len(sys.argv) > 1 else "--list"

if arg == "--count":
    # count ONLY the order taker: what the deploy script waits on before touching files
    out = ps("(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'p20_foundry' "
             "-and $_.CommandLine -notmatch 'Get-CimInstance' } | Measure-Object).Count")
    print((out.stdout or "0").strip())
    sys.exit(0)

if arg == "--kill":
    ps("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '%s' "
       "-and $_.CommandLine -notmatch 'Get-CimInstance' } | "
       "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ea 0 }" % KILL_MATCH)
    print("kill issued for: %s" % KILL_MATCH)
    sys.exit(0)

print((ps(LIST).stdout or "").strip() or "(no matching processes)")
