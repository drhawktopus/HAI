# Repoint the "Tablet WiFi Watchdog Re-armer" task at the re-armer in this folder. Changes the
# ACTION ONLY -- triggers, settings and principal are left alone, because the live task was already
# verified correct (AtStartup + time trigger, PT15M repetition, empty duration = indefinite,
# IgnoreNew, RestartCount 3).
#
# !! Must run with a FULL token. From a session, PowerShell gets a filtered token and
# Set-ScheduledTask returns "Access is denied" -- run this via `ssh softserve powershell -File ...`
# (RUNBOOK "ELEVATION"), not from the PowerShell tool directly.
#
# !! VERIFY WITH A LIVE DECOY, not by reading the settings back: run the task and confirm a fresh
# line lands in the NEW rearm.log.
$ErrorActionPreference = 'Stop'
$TaskName = 'Tablet WiFi Watchdog Re-armer'
$NewDir   = $PSScriptRoot   # this folder, where the re-armer lives
$NewFile  = Join-Path $NewDir 'rearm_wifi_watchdogs.ps1'

if (-not (Test-Path $NewFile)) { throw "target script missing: $NewFile" }

$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument ('-NonInteractive -NoProfile -ExecutionPolicy Bypass -File "' + $NewFile + '"')
Set-ScheduledTask -TaskName $TaskName -Action $act | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
Write-Output ("ACTION : " + $t.Actions.Arguments)
foreach ($tr in $t.Triggers) {
    Write-Output ("TRIGGER: {0} rep={1}" -f $tr.CimClass.CimClassName, $tr.Repetition.Interval)
}
Write-Output ("SETTINGS: MultipleInstances={0} RestartCount={1}" -f `
    $t.Settings.MultipleInstances, $t.Settings.RestartCount)
if ($t.Actions.Arguments -match 'Claude Home') { throw 'FAILED: action still points at Claude Home' }
Write-Output 'OK: action repointed, triggers/settings preserved'
