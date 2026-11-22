# Install Kirk's off-box watchdog as a self-healing scheduled task (run elevated ON the box that watches the head).
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_kirk_watchdog.ps1 [-Python <python.exe>]
# The task runs kirk_watchdog_tick.ps1 beside this installer every two minutes as SYSTEM, so -Python must be a path
# SYSTEM can run (default: the python on this account's PATH). Before installing: put the watchdog's own ssh key at
# KIRK_WATCHDOG_KEY, readable by SYSTEM and Administrators only (Windows OpenSSH refuses a key others can read), and
# set the settings kirk_watchdog_tick.ps1 names. Same restart policy as install_gpu_telemetry_logger.ps1: boot trigger +
# a PAST-DATED time trigger carrying the repetition (a repetition on the boot trigger is inert until the next reboot),
# IgnoreNew so a re-fire while a tick runs is a no-op, three restarts; a tick may run five minutes at most. Read back
# where the repetition landed; the proof is the next line in the watchdog's tick.log.
param([string]$Python = "")
$ErrorActionPreference = "Stop"
$name = "Kirk Change Watchdog"
$tick = Join-Path $PSScriptRoot "kirk_watchdog_tick.ps1"
if (-not (Test-Path $tick)) { throw "missing $tick" }
if (-not $Python) { $Python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $Python -or -not (Test-Path $Python)) { throw "pass -Python <python.exe> (a path SYSTEM can run)" }
$was = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($was) { "BEFORE: {0}" -f ($was.Actions | ForEach-Object { $_.Arguments }) }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Python "{1}"' -f $tick, $Python)
$rep = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 2)).Repetition
$rep.Duration = ""                                   # indefinite = EMPTY duration
$time = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddYears(-5)
$time.Repetition = $rep
$boot = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $name -Action $action -Trigger @($time, $boot) -Settings $settings `
    -Principal $principal -Force | Out-Null

$v = Get-ScheduledTask -TaskName $name
$repOnTime = @($v.Triggers | Where-Object {
    $_.CimClass.CimClassName -eq "MSFT_TaskTimeTrigger" -and $_.Repetition.Interval -eq "PT2M" }).Count
$bootCount = @($v.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskBootTrigger" }).Count
"AFTER {0}: repeatOnTimeTrigger={1} boot={2} restart={3} limit={4} instances={5} user={6}" -f $name, $repOnTime,
    $bootCount, $v.Settings.RestartCount, $v.Settings.ExecutionTimeLimit, $v.Settings.MultipleInstances,
    $v.Principal.UserId
"ACTION: {0}" -f ($v.Actions | ForEach-Object { $_.Arguments })
if ($repOnTime -lt 1 -or $bootCount -lt 1 -or $v.Settings.RestartCount -ne 3) { throw "task settings did not land" }
Start-ScheduledTask -TaskName $name
"STARTED"
