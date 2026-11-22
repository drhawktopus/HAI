# ollama-watchdog.ps1 - keep Ollama serving on Mothership for unattended overnight runs.
# If the API stops responding (process crash, not a reboot - a reboot self-recovers via the
# auto-login "Ollama Serve" task), restart it: kill stray ollama procs + re-trigger "Ollama Serve"
# (which runs start-ollama.ps1 with the q8/flash env in the console user's session). Mirrors the VPN watchdog.
# Runs continuously from a SYSTEM boot task. Checks every 90s. /api/version stays responsive during
# a model load, so a long load never false-triggers a restart.
$ErrorActionPreference = "SilentlyContinue"
$log = "$env:ProgramData\HAI\ollama-watchdog.log"
# The Discord webhook is a secret: one line in this file, never in the script. It is read at each notify,
# so replacing the file rotates it with no restart.
$WEBHOOK_FILE = "$env:ProgramData\HAI\ollama-watchdog.webhook"

function Write-Log($m){ "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m | Add-Content $log -ErrorAction SilentlyContinue }
function Get-Webhook { try { return (Get-Content $WEBHOOK_FILE -Raw -ErrorAction Stop).Trim() } catch { return "" } }
function Notify($m){
    $u = Get-Webhook
    if (-not $u) { Write-Log "notify SKIPPED (no webhook in $WEBHOOK_FILE): $m"; return }
    try { Invoke-RestMethod -Uri $u -Method Post -ContentType 'application/json' -Body (@{text=$m} | ConvertTo-Json) -TimeoutSec 10 | Out-Null } catch {}
}
function Test-OllamaUp { try { Invoke-RestMethod "http://localhost:11434/api/version" -TimeoutSec 6 | Out-Null; return $true } catch { return $false } }

# A SLOW answer is not a DEAD server: a short /api/version timeout under a long-context turn reads as "down",
# and a watchdog that restarts on it kills a busy, healthy server mid-measurement, then kills it AGAIN while the
# fresh process is still loading, orphaning the model server the GPU police then arrest. Restart only on evidence
# of death - no ollama process, or nothing accepting on the port - and never while a fresh process is still loading.
function Test-PortOpen {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $ar = $c.BeginConnect("127.0.0.1", 11434, $null, $null)
        $ok = $ar.AsyncWaitHandle.WaitOne(3000)
        if ($ok) { $c.EndConnect($ar) }
        $c.Close()
        return $ok
    } catch { return $false }
}
function Get-ServeProc { Get-Process ollama -ErrorAction SilentlyContinue | Sort-Object StartTime | Select-Object -First 1 }
$STARTUP_GRACE_S = 300    # a just-started ollama loading the 3.8 (~45 s measured) is left alone this long

# Planned-downtime signal: full-card jobs (LoRA training/rendering) declare downtime by writing an
# ISO-8601 UTC expiry into this file (keep-awake.hold convention — auto-expiring, so a crashed job
# re-arms the watchdog by itself). While live: stay armed, but skip the restart + Discord, or the
# watchdog restarts the model server mid-training and fights the trainer for the card.
$DOWNTIME = "$env:ProgramData\HAI\planned-downtime.hold"
function Test-PlannedDowntime {
    try {
        if (-not (Test-Path $DOWNTIME)) { return $false }
        $expiry = [datetime]::Parse((Get-Content $DOWNTIME -Raw).Trim(), $null,
                                    [System.Globalization.DateTimeStyles]::AdjustToUniversal)
        return ((Get-Date).ToUniversalTime() -lt $expiry)
    } catch { return $false }                          # unreadable/garbled file = NOT downtime (fail-armed)
}

Write-Log ("ollama-watchdog started (webhook: {0})" -f $(if (Get-Webhook) { "present" } else { "MISSING - notifications will be skipped" }))
$wasDown = $false
$downtimeLogged = $false
$busyLogged = $false

while ($true) {
    if (Test-OllamaUp) {
        if ($wasDown) { Write-Log "ollama back up"; $wasDown = $false }
        $downtimeLogged = $false
        $busyLogged = $false
    } elseif (Test-PlannedDowntime) {
        if (-not $downtimeLogged) {
            Write-Log "ollama down but PLANNED DOWNTIME declared ($DOWNTIME unexpired) -> standing by, no restart/notify"
            $downtimeLogged = $true
        }
    } else {
        Start-Sleep -Seconds 10                       # debounce a transient blip before acting
        $proc = Get-ServeProc
        $age = if ($proc) { ((Get-Date) - $proc.StartTime).TotalSeconds } else { $null }
        if (Test-OllamaUp) {
            # answered on the second ask: a blip
        } elseif ($proc -and $age -ne $null -and $age -lt $STARTUP_GRACE_S) {
            if (-not $busyLogged) { Write-Log ("ollama API not answering but the process is {0:N0} s old - still starting, left alone" -f $age); $busyLogged = $true }
        } elseif ($proc -and (Test-PortOpen)) {
            # alive and accepting connections, just slow to answer: BUSY (a long generation), never a reason to kill
            if (-not $busyLogged) { Write-Log "ollama API slow but the process is alive and the port accepts - BUSY, not restarting"; $busyLogged = $true }
        } else {
            $busyLogged = $false
            Write-Log ("ollama DEAD (process: {0}, port open: {1}) -> restarting (kill procs + 'Ollama Serve' task)" -f [bool]$proc, (Test-PortOpen))
            Get-Process ollama* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
            Start-ScheduledTask -TaskName "Ollama Serve"
            Start-Sleep -Seconds 25                    # allow the server to come up (model loads lazily)
            if (Test-OllamaUp) {
                Write-Log "ollama RECOVERED after restart"
                Notify ":robot_face: Mothership: Ollama was DOWN -> auto-restarted, serving again."
                $wasDown = $false
            } else {
                Write-Log "ollama STILL DOWN after restart attempt"
                if (-not $wasDown) { Notify ":rotating_light: Mothership: Ollama DOWN and auto-restart FAILED -- needs attention." }
                $wasDown = $true
            }
        }
    }
    Start-Sleep -Seconds 90
}
