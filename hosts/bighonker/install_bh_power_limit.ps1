# Hold an NVIDIA card's power limit at FOUNDRY_GPU_POWER_LIMIT_W watts (the foundry's setting, the same one the
# factory shape's gpu-power-limit service reads). Where a card loses itself under dense decode at its factory limit,
# a lower cap keeps it stable at the same token rate. nvidia-smi -pl is VOLATILE (a reboot or a driver reset
# restores the default), so a SYSTEM task applies it at startup and every 15 minutes (MultipleInstances IgnoreNew,
# no execution limit, restart on failure) - the lab's standard service-task shape.
# Rollback: Unregister-ScheduledTask 'BH GPU Power Limit'; nvidia-smi -pl <the card's default>.
#   powershell -File install_bh_power_limit.ps1 [-Watts N]   (default: FOUNDRY_GPU_POWER_LIMIT_W)
param([int]$Watts = 0)
$ErrorActionPreference = 'Stop'
if ($Watts -le 0) {
    $root = [Environment]::GetEnvironmentVariable('FOUNDRY_ROOT')
    if (-not $root) { $root = 'F:\foundry' }
    $m = Select-String -Path (Join-Path $root 'etc\lab.env') -Pattern '^FOUNDRY_GPU_POWER_LIMIT_W=(\d+)' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($m) { $Watts = [int]$m.Matches[0].Groups[1].Value }
}
if ($Watts -le 0) { throw 'no -Watts and no FOUNDRY_GPU_POWER_LIMIT_W in the foundry settings' }
$smi = 'C:\Windows\System32\nvidia-smi.exe'
$action = New-ScheduledTaskAction -Execute $smi -Argument ('-pl {0}' -f $Watts)
$boot = New-ScheduledTaskTrigger -AtStartup
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'BH GPU Power Limit' -Action $action -Trigger @($boot, $every) -Settings $settings `
    -Principal $principal -Description ('Hold the card at {0} W (FOUNDRY_GPU_POWER_LIMIT_W)' -f $Watts) -Force | Out-Null
Start-ScheduledTask -TaskName 'BH GPU Power Limit'
Start-Sleep -Seconds 5
$t = Get-ScheduledTask -TaskName 'BH GPU Power Limit'
$i = Get-ScheduledTaskInfo -TaskName 'BH GPU Power Limit'
'task state: {0}; last run {1}; last result {2}; triggers {3}' -f $t.State, $i.LastRunTime, $i.LastTaskResult, @($t.Triggers).Count
& $smi --query-gpu=power.limit,power.default_limit,power.draw --format=csv
