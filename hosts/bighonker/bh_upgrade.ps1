# One-off, from before the shared NVIDIA starter: upgrade this box's ollama and add the single-model settings to
# its launcher, then verify. The shared starter (start-ollama-system.ps1) now takes these from lab.env, so this
# edits a launcher layout that no longer exists; it is kept as the record of what ran. Run by path
# (powershell -File), which avoids the bash->ssh->powershell nested-quoting trap.
#   powershell -File bh_upgrade.ps1 -OllamaDir <the stock ollama install folder>
#   1. ollama 0.30.8 -> latest (standard CUDA path, two minor versions behind)
#   2. OLLAMA_MAX_LOADED_MODELS=1 + OLLAMA_NUM_PARALLEL=1 -- missing on this box. Two concurrent
#      clients each got a parallel slot with its own KV cache, blew past 8 GB and forced CPU
#      offload. That is what contaminated the earlier benchmark (40% GPU util = offload, not busy).
# Backs up the binary dir AND the launcher before touching either.

param([Parameter(Mandatory = $true)][string]$OllamaDir)
$ErrorActionPreference = 'Stop'
$Launcher  = 'C:\ProgramData\HAI\start-ollama-system.ps1'
$Task      = 'Ollama Serve'
$Stamp     = Get-Date -Format 'yyyyMMdd-HHmmss'
$Tmp       = 'C:\ProgramData\HAI\ollama-upgrade'

Write-Output "== before =="
try {
  $v = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 8
  Write-Output ("   running version: " + $v.version)
} catch { Write-Output "   version probe failed" }

Write-Output "== finding latest release =="
$rel = Invoke-RestMethod 'https://api.github.com/repos/ollama/ollama/releases/latest' `
       -Headers @{ 'User-Agent' = 'HAI-upgrade/1.0' } -TimeoutSec 45
$asset = $rel.assets | Where-Object { $_.name -eq 'ollama-windows-amd64.zip' } | Select-Object -First 1
if (-not $asset) { Write-Output "FATAL: no ollama-windows-amd64.zip in $($rel.tag_name)"; exit 2 }
Write-Output ("   latest: {0}  ({1:N0} MB)" -f $rel.tag_name, ($asset.size/1MB))

New-Item -ItemType Directory -Force $Tmp | Out-Null
$zip = Join-Path $Tmp 'ollama.zip'
Write-Output "   downloading ..."
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest $asset.browser_download_url -OutFile $zip -TimeoutSec 1800
$got = (Get-Item $zip).Length
Write-Output ("   downloaded {0:N0} MB" -f ($got/1MB))
if ($got -lt ($asset.size * 0.98)) { Write-Output "FATAL: short download - refusing to install"; exit 3 }

Write-Output "== stopping '$Task' =="
schtasks /end /tn $Task 2>&1 | Select-Object -First 1 | ForEach-Object { "   $_" }
Start-Sleep -Seconds 2
# schtasks /end does NOT reliably kill the child process - kill by name (house gotcha).
foreach ($n in @('ollama','ollama app')) {
  Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force; Write-Output "   killed $n PID $($_.Id)"
  }
}
Start-Sleep -Seconds 3

$bak = "$OllamaDir.bak-$Stamp"
Write-Output "== backing up binary dir -> $bak =="
Copy-Item $OllamaDir $bak -Recurse -Force
Write-Output "   backed up"

Write-Output "== extracting =="
Expand-Archive -Path $zip -DestinationPath $OllamaDir -Force
Write-Output "   extracted over $OllamaDir"

Write-Output "== launcher: adding single-model guard =="
Copy-Item $Launcher "$Launcher.bak-$Stamp" -Force
$body = Get-Content $Launcher -Raw
$add = @()
if ($body -notmatch 'OLLAMA_MAX_LOADED_MODELS') { $add += "`$env:OLLAMA_MAX_LOADED_MODELS = '1'" }
if ($body -notmatch 'OLLAMA_NUM_PARALLEL')      { $add += "`$env:OLLAMA_NUM_PARALLEL = '1'" }
if ($add.Count -gt 0) {
  $lines = Get-Content $Launcher
  $out = New-Object System.Collections.Generic.List[string]
  $done = $false
  foreach ($ln in $lines) {
    $out.Add($ln)
    if (-not $done -and $ln -match '^\s*\$env:OLLAMA_MODELS') { foreach ($a in $add) { $out.Add($a) }; $done = $true }
  }
  if (-not $done) { $tmp = New-Object System.Collections.Generic.List[string]; foreach ($a in $add) { $tmp.Add($a) }; foreach ($l in $out) { $tmp.Add($l) }; $out = $tmp }
  Set-Content -Path $Launcher -Value $out -Encoding utf8
  Write-Output ("   added: " + ($add -join ', '))
} else { Write-Output "   already present" }

Write-Output "== starting '$Task' =="
schtasks /run /tn $Task 2>&1 | Select-Object -First 1 | ForEach-Object { "   $_" }
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 2
  try {
    $v = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 4
    Write-Output ("   up after {0}s, version {1}" -f ($i*2), $v.version); $ok = $true; break
  } catch { }
}
if (-not $ok) {
  Write-Output "FATAL: ollama did not come back. Restore: $bak  and  $Launcher.bak-$Stamp"
  exit 4
}
Write-Output "== done =="
