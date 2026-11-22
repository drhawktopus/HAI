# textinput-watchdog.ps1 - kill a RUNAWAY TextInputHost on this box.
# TextInputHost (the Windows input host) can get stuck pegging a full CPU core; as the INPUT host it then generates
# phantom input that immediately wakes the box from S3 (a wake storm) and spins the fans. Killing it restarts it clean
# and idle. Runs continuously from a SYSTEM boot task, checking every 60 s. Debounced (runaway across TWO 3 s samples)
# so brief legitimate input use is never killed.
$ErrorActionPreference = "SilentlyContinue"
$log = "C:\ProgramData\HAI\textinput-watchdog.log"
# The webhook is a secret: one line in this file, never in the script. It is read at each notify,
# so replacing the file rotates it with no restart.
$WEBHOOK_FILE = "$env:ProgramData\HAI\textinput-watchdog.webhook"
$THRESHOLD = 0.80   # fraction of one core
$SAMPLE = 3         # seconds per sample

function Write-Log($m){ "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m | Add-Content $log -ErrorAction SilentlyContinue }
function Get-Webhook { try { return (Get-Content $WEBHOOK_FILE -Raw -ErrorAction Stop).Trim() } catch { return "" } }
function Notify($m){
    $u = Get-Webhook
    if (-not $u) { Write-Log "notify SKIPPED (no webhook in $WEBHOOK_FILE): $m"; return }
    try { Invoke-RestMethod -Uri $u -Method Post -ContentType 'application/json' -Body (@{text=$m} | ConvertTo-Json) -TimeoutSec 10 | Out-Null } catch {}
}
function Get-TIHFrac {
    $p1 = Get-Process TextInputHost -ErrorAction SilentlyContinue
    if (-not $p1) { return -1 }
    $c1 = ($p1 | Measure-Object CPU -Sum).Sum
    Start-Sleep -Seconds $SAMPLE
    $p2 = Get-Process TextInputHost -ErrorAction SilentlyContinue
    if (-not $p2) { return -1 }
    $c2 = ($p2 | Measure-Object CPU -Sum).Sum
    return [math]::Round(($c2 - $c1) / $SAMPLE, 2)
}

Write-Log "textinput-watchdog started (threshold ${THRESHOLD} core)"
while ($true) {
    $f1 = Get-TIHFrac
    if ($f1 -gt $THRESHOLD) {
        Start-Sleep -Seconds 4            # debounce: confirm it's sustained, not a transient typing spike
        $f2 = Get-TIHFrac
        if ($f2 -gt $THRESHOLD) {
            Write-Log "RUNAWAY TextInputHost (cores: $f1 then $f2) -> killing (restarts clean)"
            Get-Process TextInputHost -ErrorAction SilentlyContinue | Stop-Process -Force
            Notify ":fire: Mothership: runaway TextInputHost (pegging a core + waking it from sleep) -> killed, restarts clean."
        }
    }
    Start-Sleep -Seconds 60
}
