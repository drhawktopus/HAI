# Install a GPU telemetry logger as a self-healing scheduled task (run elevated ON the box, e.g. via ssh).
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_gpu_telemetry_logger.ps1 [-Script <deployed logger>]
# -Script names the logger the task runs: the NVIDIA logger beside this installer by default (fleet_deploy ships both to
# ProgramData\HAI), or an AMD box's <foundry root>\bin\gpu_telemetry_logger.ps1. A running instance is stopped first so the
# new definition is the one in force; the loggers find their sensor library through FOUNDRY_LHM_DLL (etc\lab.env).
# Same restart policy as fix_health_daemon_task.ps1 (read its comments): boot trigger + a PAST-DATED time trigger
# carrying a 15-min repetition (a repetition on the boot trigger is inert until the next reboot), IgnoreNew so a
# re-fire while alive is a no-op, restarts, no execution time limit. Verified by reading back WHERE the repetition
# landed, and then by a live decoy (stop the logger, watch the scheduler bring it back) - settings are not proof.
param([string]$Script = "")
$ErrorActionPreference = "Stop"
$name = "GPU Telemetry Logger"
$script = if ($Script) { $Script } else { Join-Path $PSScriptRoot "gpu_telemetry_logger_nvidia.ps1" }
if (-not (Test-Path $script)) { throw "missing $script" }
$was = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($was) {
    "BEFORE: {0}" -f ($was.Actions | ForEach-Object { $_.Arguments })
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue      # the old instance ends; the new definition starts below
    Start-Sleep -Seconds 3
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $script)
$rep = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)).Repetition
$rep.Duration = ""                                   # indefinite = EMPTY duration
$time = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddYears(-5)
$time.Repetition = $rep
$boot = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $name -Action $action -Trigger @($time, $boot) -Settings $settings `
    -Principal $principal -Force | Out-Null

$v = Get-ScheduledTask -TaskName $name
$repOnTime = @($v.Triggers | Where-Object {
    $_.CimClass.CimClassName -eq "MSFT_TaskTimeTrigger" -and $_.Repetition.Interval -eq "PT15M" }).Count
$bootCount = @($v.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskBootTrigger" }).Count
"AFTER {0}: repeatOnTimeTrigger={1} boot={2} restart={3} limit={4} instances={5} user={6}" -f $name, $repOnTime,
    $bootCount, $v.Settings.RestartCount, $v.Settings.ExecutionTimeLimit, $v.Settings.MultipleInstances,
    $v.Principal.UserId
if ($repOnTime -lt 1 -or $bootCount -lt 1 -or $v.Settings.RestartCount -ne 3) { throw "task settings did not land" }
Start-ScheduledTask -TaskName $name
"STARTED"
