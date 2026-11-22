# One-off: once the model-store merge task ('HAI Ollama Model Merge') has finished, point the console user's
# OLLAMA_MODELS at the merged store, repoint 'Ollama Serve' at the starter and restart the model server.
#   powershell -File finalize-ollama-move.ps1 -ModelsDir <the merged store>
param([Parameter(Mandatory = $true)][string]$ModelsDir)
$ErrorActionPreference = 'Stop'
$log = 'C:\ProgramData\HAI\ollama-finalize.log'

try {
    do {
        Start-Sleep -Seconds 10
        $merge = Get-ScheduledTask -TaskName 'HAI Ollama Model Merge'
    } while ($merge.State -eq 'Running')

    $result = (Get-ScheduledTaskInfo -TaskName 'HAI Ollama Model Merge').LastTaskResult
    if ($result -gt 7) {
        throw "Model merge failed with robocopy result $result"
    }

    [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $ModelsDir, 'User')
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\ProgramData\HAI\start-ollama.ps1"'
    Set-ScheduledTask -TaskName 'Ollama Serve' -Action $action | Out-Null
    Stop-ScheduledTask -TaskName 'Ollama Serve' -ErrorAction SilentlyContinue
    Get-Process ollama, 'ollama app' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName 'Ollama Serve'
    Start-Sleep -Seconds 12

    $models = (Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10).models.name
    "$(Get-Date -Format o) SUCCESS merge=$result models=$($models -join ', ')" | Set-Content $log
} catch {
    "$(Get-Date -Format o) FAILED $($_.Exception.Message)" | Set-Content $log
    exit 1
}
