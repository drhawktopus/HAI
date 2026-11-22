Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class PowerReq {
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
$ES_CONTINUOUS = 0x80000000
$ES_SYSTEM_REQUIRED = 0x00000001
$log = 'C:\ProgramData\HAI\keep-awake.log'
"Starting BigHonker HAI keep-awake at $(Get-Date -Format o)" | Out-File -FilePath $log -Append -Encoding utf8
$held = $false
while ($true) {
  $hasModel = $false
  try {
    $ps = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/ps' -TimeoutSec 2
    $hasModel = ($ps.models -and $ps.models.Count -gt 0)
  } catch {
    $hasModel = $false
  }
  if ($hasModel) {
    [PowerReq]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED) | Out-Null
    if (-not $held) { "Holding system awake while Ollama model is resident at $(Get-Date -Format o)" | Out-File -FilePath $log -Append -Encoding utf8 }
    $held = $true
  } else {
    [PowerReq]::SetThreadExecutionState($ES_CONTINUOUS) | Out-Null
    if ($held) { "Released system awake hold at $(Get-Date -Format o)" | Out-File -FilePath $log -Append -Encoding utf8 }
    $held = $false
  }
  Start-Sleep -Seconds 30
}