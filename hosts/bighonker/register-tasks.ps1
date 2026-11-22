$ErrorActionPreference = 'Stop'

$haiDir = 'C:\ProgramData\HAI'
$keepAwake = Join-Path $haiDir 'keep-awake.ps1'
$startOllama = Join-Path $haiDir 'start-ollama-system.ps1'

if (-not (Test-Path $keepAwake)) {
    throw "Missing keep-awake script: $keepAwake"
}
if (-not (Test-Path $startOllama)) {
    throw "Missing Ollama launcher script: $startOllama"
}

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$startup = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

$ollamaAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startOllama`""
Register-ScheduledTask -TaskName 'Ollama Serve' -Action $ollamaAction -Trigger $startup -Principal $principal -Settings $settings -Force | Out-Null

$keepAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$keepAwake`""
Register-ScheduledTask -TaskName 'HAI Keep-Awake' -Action $keepAction -Trigger $startup -Principal $principal -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName 'Ollama Serve'
Start-ScheduledTask -TaskName 'HAI Keep-Awake'

Get-ScheduledTask -TaskName 'Ollama Serve','HAI Keep-Awake' |
    Select-Object TaskName,TaskPath,State |
    Format-Table -AutoSize
