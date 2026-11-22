# The model server on an AMD factory, started by the task "Ollama Serve" in the console user's session (this shape's GPU
# work runs as the signed-in user). One script for every box of this shape; what is particular to a box is a setting in
# the lab.env beside this script:
#   OLLAMA_EXE        the ollama.exe to run (required)
#   OLLAMA_SERVE_ENV  the server's environment as a JSON object, e.g. {"OLLAMA_MODELS": "D:\\models",
#                     "OLLAMA_LLM_LIBRARY": "vulkan"}
#   OLLAMA_SERVE_LOG  where the server's stderr goes, which is its log (required)
#   OLLAMA_SERVE_OUT  where its stdout goes (required)
# ollama serve runs directly, never through the tray app, whose auto-updater replaces the engine build with a stock one.
#
#   powershell -NoProfile -File start-ollama.ps1 -Show    prints the environment and the command; starts nothing
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
$log = Get-LabSetting 'OLLAMA_SERVE_LOG'
$out = Get-LabSetting 'OLLAMA_SERVE_OUT'
foreach ($pair in @(@('OLLAMA_EXE', $ollama), @('OLLAMA_SERVE_LOG', $log), @('OLLAMA_SERVE_OUT', $out))) {
    if (-not $pair[1]) { throw ("{0} is not set (in the environment or {1}\lab.env)" -f $pair[0], $PSScriptRoot) }
}
$serveEnv = (Get-LabSetting 'OLLAMA_SERVE_ENV' '{}') | ConvertFrom-Json
foreach ($p in $serveEnv.PSObject.Properties) { Set-Item -Path ('Env:' + $p.Name) -Value ([string]$p.Value) }

if ($Show) {
    foreach ($p in $serveEnv.PSObject.Properties) { '{0}={1}' -f $p.Name, [Environment]::GetEnvironmentVariable($p.Name) }
    "Start-Process -FilePath '$ollama' -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardError '$log' -RedirectStandardOutput '$out'"
    exit 0
}
Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardError $log -RedirectStandardOutput $out
