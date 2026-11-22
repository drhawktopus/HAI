# keep-awake.ps1 - keep this box awake while it is actually WORKING.
#
# HAI wakes the box by WoL (an "unattended" wake), and Windows' idle-sleep timer is INPUT-only: blind to GPU
# inference, an unattended self-engineering run between model calls, or a headless SSH session. So this watcher
# asserts ES_SYSTEM_REQUIRED while ANY of those is true and releases the moment they all clear, so the OS returns to
# its normal idle sleep. OS-awake then tracks actual work, not keypresses.
#
# Busy = (keep-awake.hold file with an ISO expiry) OR (an Ollama model resident) OR (the head's /self-eng/busy) OR
#        (an active inbound SSH session) OR (a ComfyUI image generation running or queued, plus a 45-minute tail).
#        ComfyUI model RESIDENCY does NOT count: ComfyUI holds models indefinitely, so residency would block sleep
#        forever after one image.
# Settings, in the lab.env beside this script: HAI_URL (the head), OWNER_USER (whose self-engineering runs count),
# OLLAMA_PORT (default 11434). Runs as a scheduled task at startup (SYSTEM), polling every 30 s. ES_SYSTEM_REQUIRED
# blocks only automatic/idle sleep; a user-initiated sleep still works.

function Get-LabSetting([string]$Name, [string]$Default = '') {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ($v -and $v.Trim()) { return $v.Trim() }
    $file = $env:LAB_SETTINGS_FILE
    if (-not $file) { $file = Join-Path $PSScriptRoot 'lab.env' }
    if (Test-Path $file) {
        $m = Select-String -Path $file -Pattern ('^' + [regex]::Escape($Name) + '=(.*)$') | Select-Object -First 1
        if ($m -and $m.Matches[0].Groups[1].Value.Trim()) { return $m.Matches[0].Groups[1].Value.Trim() }
    }
    return $Default
}
$HeadUrl = (Get-LabSetting 'HAI_URL').TrimEnd('/')
$OwnerUser = Get-LabSetting 'OWNER_USER'
$OllamaPort = Get-LabSetting 'OLLAMA_PORT' '11434'

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class PowerState {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@

$ES_CONTINUOUS      = [uint32]2147483648  # 0x80000000
$ES_SYSTEM_REQUIRED = [uint32]1           # 0x00000001
$logFile = "C:\ProgramData\HAI\keep-awake.log"

function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

function Test-Busy {
    # 0. explicit hold file for long non-Comfy jobs (model downloads, LoRA training). Holds a single
    #    ISO-8601 UTC expiry timestamp; honored until then. Empty/unparseable => 12h cap from mtime so a
    #    stale file can't pin the box awake forever; expired files are cleaned up on sight.
    try {
        $hold = "C:\ProgramData\HAI\keep-awake.hold"
        if (Test-Path $hold) {
            $raw = (Get-Content $hold -Raw -ErrorAction SilentlyContinue)
            if ($raw) { $raw = $raw.Trim() }
            $active = $false
            if ($raw) {
                try {
                    $exp = [datetime]::Parse($raw, [Globalization.CultureInfo]::InvariantCulture, ([Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal))
                    $active = ((Get-Date).ToUniversalTime() -lt $exp)
                } catch { $active = (((Get-Date) - (Get-Item $hold).LastWriteTime).TotalHours -lt 12) }
            } else {
                $active = (((Get-Date) - (Get-Item $hold).LastWriteTime).TotalHours -lt 12)
            }
            if ($active) { return "hold-file" } else { Remove-Item $hold -Force -ErrorAction SilentlyContinue }
        }
    } catch {}
    # 1. Ollama model resident (covers an active run + its keep_alive tail)
    try {
        $p = Invoke-RestMethod -Uri "http://localhost:$OllamaPort/api/ps" -TimeoutSec 5
        if ($p.models -and @($p.models).Count -gt 0) { return "model-resident" }
    } catch {}
    # 2. orchestrator reports an unattended self-engineering job active (covers gaps between calls
    #    when the model briefly unloads but a run is still in flight)
    try {
        if ($HeadUrl -and $OwnerUser -and
            (Invoke-RestMethod -Uri "$HeadUrl/self-eng/busy?user=$([uri]::EscapeDataString($OwnerUser))" -TimeoutSec 5).busy) {
            return "self-eng-busy"
        }
    } catch {}
    # 3. an active inbound SSH session (headless remote work the input timer can't see)
    try {
        if (@(Get-NetTCPConnection -LocalPort 22 -State Established -ErrorAction SilentlyContinue).Count -gt 0) { return "ssh-session" }
    } catch {}
    # 4. a ComfyUI image job running or queued (+ a 45-min tail after the last one, mirroring
    #    the grace ollama gets from keep_alive) - GPU work the input timer can't see
    try {
        $q = Invoke-RestMethod -Uri "http://localhost:8188/queue" -TimeoutSec 5
        if (@($q.queue_running).Count -gt 0 -or @($q.queue_pending).Count -gt 0) {
            $script:lastComfy = Get-Date
            return "comfy-active"
        }
    } catch {}
    if ($script:lastComfy -and ((Get-Date) - $script:lastComfy).TotalMinutes -lt 45) { return "comfy-tail" }
    return $null
}

$script:lastComfy = $null
Write-Log "keep-awake watcher started (hold | model | self-eng | ssh | comfy)"
$asserting = $false

while ($true) {
    $reason = Test-Busy
    $busy = [bool]$reason

    if ($busy -and -not $asserting) {
        [void][PowerState]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
        $asserting = $true
        Write-Log "busy ($reason) -> asserting ES_SYSTEM_REQUIRED (sleep blocked)"
    }
    elseif (-not $busy -and $asserting) {
        [void][PowerState]::SetThreadExecutionState($ES_CONTINUOUS)
        $asserting = $false
        Write-Log "idle -> released (normal 30-min idle sleep resumes)"
    }

    Start-Sleep -Seconds 30
}
