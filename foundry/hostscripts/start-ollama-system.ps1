# The model server on an NVIDIA factory, run as SYSTEM by the task "Ollama Serve" at boot. One script for every box of
# this shape; what is particular to a box is a setting in the lab.env beside this script:
#   OLLAMA_EXE        the ollama.exe to run (required)
#   OLLAMA_SERVE_ENV  the server's environment as a JSON object, e.g. {"OLLAMA_MODELS": "D:\\models",
#                     "OLLAMA_NUM_PARALLEL": "1"}; a variable the box already sets at machine scope need not repeat here
#   OLLAMA_SERVE_LOG  the server's log (default: ollama-serve.log beside this script)
# cmd writes the log, appending ollama's own UTF-8 bytes: PowerShell 5.1's *>> re-encodes to UTF-16LE and hard-wraps at
# 120 columns, which leaves the log unsearchable with an ASCII grep. The command is passed to cmd unquoted, so neither
# path may contain a space (refused below rather than run wrong).
#
#   powershell -NoProfile -File start-ollama-system.ps1 -Show    prints the environment and the command; starts nothing
param([switch]$Show)

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

$ollama = Get-LabSetting 'OLLAMA_EXE'
if (-not $ollama) { throw "OLLAMA_EXE is not set (in the environment or $PSScriptRoot\lab.env)" }
$log = Get-LabSetting 'OLLAMA_SERVE_LOG' (Join-Path $PSScriptRoot 'ollama-serve.log')
if ($ollama.Contains(' ') -or $log.Contains(' ')) { throw "OLLAMA_EXE and OLLAMA_SERVE_LOG may not contain a space" }
$serveEnv = (Get-LabSetting 'OLLAMA_SERVE_ENV' '{}') | ConvertFrom-Json
foreach ($p in $serveEnv.PSObject.Properties) { Set-Item -Path ('Env:' + $p.Name) -Value ([string]$p.Value) }

if ($Show) {
    foreach ($p in $serveEnv.PSObject.Properties) { '{0}={1}' -f $p.Name, [Environment]::GetEnvironmentVariable($p.Name) }
    'cmd.exe /d /c "{0} serve >> {1} 2>&1"' -f $ollama, $log
    exit 0
}
"Starting SYSTEM Ollama at $(Get-Date -Format o) with $ollama models=$env:OLLAMA_MODELS" | Out-File -FilePath $log -Append -Encoding ascii
& cmd.exe /d /c "$ollama serve >> $log 2>&1"
