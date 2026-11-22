# Registers the Fellowship Battery Governor as a recurring task on the box that holds adb.
# Must run via ssh-to-self (Register-ScheduledTask needs the full token; a plain
# shell gets "Access is denied" from the filtered token). Same task shape as the
# tablet WiFi-watchdog re-armer: AtStartup + 10-min repetition (duration EMPTY =
# indefinite; never TimeSpan.MaxValue, which the scheduler rejects), IgnoreNew so
# a slow run cannot stack, S4U so it runs whether or not anyone is logged on.
$ErrorActionPreference = "Stop"
# The python the task runs: PYTHON_EXE, else the one on PATH (never the Microsoft Store alias).
$py = $env:PYTHON_EXE
if (-not $py) { $py = (Get-Command python -ErrorAction Stop).Source }
if ($py -match '\\WindowsApps\\') { throw "python resolves to the Microsoft Store alias ($py); set PYTHON_EXE" }
$gov = Join-Path $PSScriptRoot 'battery_governor.py'
$log = Join-Path $PSScriptRoot 'battery_governor.log'

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $py $gov >> $log 2>&1"
$rep     = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)).Repetition
$rep.Duration = ""                      # empty = indefinite
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Repetition = $rep
# A boot trigger's repetition only starts AT BOOT, so a disable/enable (or a re-register) without a reboot
# leaves the task silent ("Next Run Time: N/A"). A second trigger starting now carries the repetition until then.
$now = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
$now.Repetition = $rep
$me        = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
             -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
             -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "Fellowship Battery Governor" -Action $action -Trigger @($trigger, $now) `
    -Principal $principal -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName "Fellowship Battery Governor"
"State:      $($t.State)"
"Principal:  $($t.Principal.UserId) / $($t.Principal.LogonType) / $($t.Principal.RunLevel)"
foreach ($tr in $t.Triggers) {
    "Trigger:    $($tr.CimClass.CimClassName)  start '$($tr.StartBoundary)'  every $($tr.Repetition.Interval)  for '$($tr.Repetition.Duration)' (empty = indefinite)"
}
"NextRun:    $((Get-ScheduledTaskInfo -TaskName 'Fellowship Battery Governor').NextRunTime)"
Start-ScheduledTask -TaskName "Fellowship Battery Governor"
"Started once to confirm; log will show the run."
