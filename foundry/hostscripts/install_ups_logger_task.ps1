# Installs 'UPS Power Logger' (ups_power_logger.py) as a self-healing SYSTEM task, the same shape as the HAI Stall
# Recorder / foundry health daemons (Lab\bin\fix_health_daemon_task.ps1): a past-dated time trigger carrying a 15-min
# repetition (empty Duration = indefinite), an AtStartup trigger, IgnoreNew, RestartCount 3, no execution time limit.
param([string]$Python = "",   # the interpreter that runs the logger; the box's own python when not given
      [string]$Script = "$env:ProgramData\HAI\ups_power_logger.py")
$ErrorActionPreference = "Stop"
$name = "UPS Power Logger"
if (-not $Python) { $Python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $Python -or -not (Test-Path $Python)) { "MISSING python: '$Python' (pass -Python <path>)"; exit 1 }
if (-not (Test-Path $Script)) { "MISSING script: $Script"; exit 1 }

$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory "$env:ProgramData\HAI"
$rep = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)).Repetition
$rep.Duration = ""
$time = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddYears(-5)
$time.Repetition = $rep
$boot = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($time, $boot) -Settings $settings `
    -Principal $principal -Force | Out-Null

$v = Get-ScheduledTask -TaskName $name
$bootCount = @($v.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskBootTrigger" }).Count
$repOnTime = @($v.Triggers | Where-Object {
    $_.CimClass.CimClassName -eq "MSFT_TaskTimeTrigger" -and $_.Repetition.Interval -eq "PT15M" }).Count
$ok = ($v.Settings.RestartCount -eq 3) -and ($repOnTime -ge 1) -and ($bootCount -ge 1) -and `
      ($v.Settings.MultipleInstances -eq "IgnoreNew") -and ($v.Principal.UserId -eq "SYSTEM")
"task: boot=$bootCount repetitionOnTime=$repOnTime restart=$($v.Settings.RestartCount) instances=$($v.Settings.MultipleInstances) user=$($v.Principal.UserId) limit=$($v.Settings.ExecutionTimeLimit)"
"VERIFIED: $ok"
if (-not $ok) { exit 1 }
Start-ScheduledTask -TaskName $name
"started"
