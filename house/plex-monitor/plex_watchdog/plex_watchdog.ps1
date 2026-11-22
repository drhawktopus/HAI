#requires -Version 5.1
<#
  Plex local watchdog
  ───────────────────
  Runs every minute via the "PlexWatchdog" scheduled task (the console user's
  interactive session, highest run level). Does two things the companion `plex_monitor`
  does NOT do:

    1. RESTART Plex if the LOCAL API (127.0.0.1:32400) is down. Uses the local endpoint
       on purpose — external/UPnP reachability can drop for network reasons while Plex
       itself is fine, and we must not bounce Plex over an ISP hiccup.
    2. PRIORITY HYGIENE — keep "Plex Media Server" at AboveNormal and the "claude" agent
       processes at BelowNormal, so heavy Claude compute on this (the Plex) host can't
       starve explorer/Plex. A starved shell fails `Shell_NotifyIcon` and the tray app dies,
       which crash-loops the server.

  `plex_monitor` (sibling folder) stays the external Discord notifier — untouched. When
  this watchdog restarts Plex, plex_monitor sends the "back online" notice on its next run.

  Silent on healthy runs; only meaningful events are written to plex_watchdog.log.
#>

# This file is saved as UTF-8 WITH a byte-order mark and its code strings are ASCII: Windows PowerShell 5.1 reads a
# mark-less file as code page 1252, where an em dash decodes to a smart quote that ends a string and swallows the
# code after it. parse_check.ps1 beside this script parses the file the way the task's PowerShell does.
$ErrorActionPreference = 'SilentlyContinue'

# Plex's executable: PLEX_EXE in the environment, else `plex_exe` in plex_watchdog.settings.json beside this script, else
# the standard install path
$PlexExe  = $env:PLEX_EXE
if (-not $PlexExe) { try { $PlexExe = (Get-Content (Join-Path $PSScriptRoot 'plex_watchdog.settings.json') -Raw | ConvertFrom-Json).plex_exe } catch {} }
if (-not $PlexExe) { $PlexExe = Join-Path $env:ProgramFiles 'Plex\Plex Media Server\Plex Media Server.exe' }
$Identity = 'http://127.0.0.1:32400/identity'
$LogFile  = Join-Path $PSScriptRoot 'plex_watchdog.log'
$StateFile= Join-Path $PSScriptRoot 'plex_watchdog_state.json'
$MaxLogKB = 512
$WedgeLimit = 3   # consecutive down-with-process cycles (~3 min) before kill+relaunch

function Log($msg) {
  $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Add-Content -LiteralPath $LogFile -Value $line
}

# Rotate log if oversized
try {
  if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length / 1KB -gt $MaxLogKB)) {
    Move-Item -LiteralPath $LogFile -Destination "$LogFile.1" -Force
  }
} catch {}

# Load state (wedge counter)
$state = @{ unresponsive = 0 }
try {
  if (Test-Path $StateFile) {
    $j = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    if ($null -ne $j.unresponsive) { $state.unresponsive = [int]$j.unresponsive }
  }
} catch {}

function Save-State { $state | ConvertTo-Json | Set-Content -LiteralPath $StateFile }

# ── 1. Local health check ───────────────────────────────────────────────
$up = $false
try {
  $r = Invoke-WebRequest -Uri $Identity -UseBasicParsing -TimeoutSec 8
  if ($r.StatusCode -eq 200) { $up = $true }
} catch { $up = $false }

$proc = Get-Process 'Plex Media Server' -ErrorAction SilentlyContinue

if ($up) {
  if ($state.unresponsive -ne 0) { $state.unresponsive = 0; Save-State }
}
else {
  if (-not $proc) {
    Log 'Plex DOWN (no process, local API unreachable) - relaunching.'
    Start-Process -FilePath $PlexExe
    Log "Launched: $PlexExe"
    $state.unresponsive = 0; Save-State
  }
  else {
    # Process alive but API not answering: mid-startup, or wedged tray app.
    $state.unresponsive++
    if ($state.unresponsive -ge $WedgeLimit) {
      Log "Plex PID $($proc.Id) unresponsive for $($state.unresponsive) cycles - killing and relaunching."
      $proc | Stop-Process -Force
      Start-Sleep -Seconds 2
      Start-Process -FilePath $PlexExe
      Log "Relaunched after wedge: $PlexExe"
      $state.unresponsive = 0
    } else {
      Log "Plex PID $($proc.Id) alive but API not responding (cycle $($state.unresponsive)/$WedgeLimit) - grace."
    }
    Save-State
  }
}

# ── 2. Priority hygiene (every run; only log actual changes) ─────────────
# NOTE: Plex process priority is NOT set here. It is handled persistently via IFEO
# (HKLM\...\Image File Execution Options\<exe>\PerfOptions\CpuPriorityClass=3/High) for all
# Plex exes — see RUNBOOK "Softserve > Plex". That survives reboots and applies on every launch
# (incl. each new transcoder), so a runtime loop here would be redundant. We only lower the
# 'claude' agent procs to BelowNormal, which IFEO/Plex config doesn't cover.
Get-Process 'claude' -ErrorAction SilentlyContinue | ForEach-Object {
  try {
    if ($_.PriorityClass -ne [System.Diagnostics.ProcessPriorityClass]::BelowNormal) {
      $_.PriorityClass = 'BelowNormal'
      Log "Priority: claude PID $($_.Id) -> BelowNormal"
    }
  } catch {}
}
