# Registers a Windows Task Scheduler task that runs the Plex monitor every 15 minutes.
# Run this once as Administrator:  Right-click → "Run with PowerShell as Administrator"

$taskName   = "PlexMonitor"
$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$batFile    = Join-Path $scriptDir "run.bat"

# Remove any existing task with the same name
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$batFile`""
$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) -Once -At (Get-Date)
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action   $action `
    -Trigger  $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "Checks Plex external accessibility every 15 minutes and alerts Discord if down." `
    | Out-Null

Write-Host "Task '$taskName' registered successfully." -ForegroundColor Green
Write-Host "It will run every 15 minutes. Logs: $scriptDir\plex_monitor.log"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'   # run it right now"
Write-Host "  Get-ScheduledTask   -TaskName '$taskName'   # check status"
Write-Host "  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false  # remove it"
