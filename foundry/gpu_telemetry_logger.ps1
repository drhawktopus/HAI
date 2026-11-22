# GPU + HOST telemetry logger (P20) — pairs GPU sensors AND host contention metrics with the live training step.
# GPU-only telemetry cannot separate a stall from a saturated card, so host-side metrics are logged beside it
# (CPU %, RAM, swap/pagefile). Rotates the CSV on schema change.
# Durable: run by the "GPU Telemetry Logger" scheduled task (SYSTEM, AtStartup, no time limit).
$ErrorActionPreference = 'SilentlyContinue'
# the foundry root is the box's FOUNDRY_ROOT machine variable; the registry copy covers a service started before it was set
$root = $env:FOUNDRY_ROOT
if (-not $root) { $root = [Environment]::GetEnvironmentVariable('FOUNDRY_ROOT', 'Machine') }
if (-not $root) {                                   # the settings file beside the installed tools names it (the reporters' rule)
    $pd = Join-Path $env:ProgramData 'HAI\lab.env'
    if (Test-Path $pd) { $m = Get-Content $pd | Where-Object { $_ -match '^FOUNDRY_ROOT=' } | Select-Object -First 1; if ($m) { $root = ($m -split '=', 2)[1] } }
}
if (-not $root -or -not (Test-Path $root)) { exit 1 }
# the sensor library: FOUNDRY_LHM_DLL in the box's settings file (<root>\etc\lab.env), else LHM\ beside this script
$lhm = $null
$labenv = Join-Path $root 'etc\lab.env'
if (Test-Path $labenv) { $m = Get-Content $labenv | Where-Object { $_ -match '^FOUNDRY_LHM_DLL=' } | Select-Object -First 1; if ($m) { $lhm = ($m -split '=', 2)[1] } }
if (-not $lhm) { $lhm = Join-Path $PSScriptRoot 'LHM\LibreHardwareMonitorLib.dll' }
Add-Type -Path $lhm
$c = New-Object LibreHardwareMonitor.Hardware.Computer
$c.IsGpuEnabled = $true
$c.Open()

$csv = Join-Path $root 'var\state\telemetry\gpu_telemetry.csv'
# mem_load_pct / vcore_v / d3d_compute are the discriminating variables between a STALLED and a SATURATED card:
# stalled reads mem_load ~6 %, vcore ~1.10 V; saturated ~23 %, ~0.94 V. mem_load near 6 % while "gpu_load" reads
# 100 % is the tell: the card is NOT bandwidth-bound, it is spinning or waiting.
$hdr = 'ts,run,step,rate_s_it,core_mhz,mem_mhz,power_w,temp_c,hotspot_c,gpu_load,vram_used_mb,vram_free_mb,shared_spill_mb,cpu_pct,ram_used_mb,ram_pct,swap_pct,mem_load_pct,vcore_v,d3d_compute'
New-Item -ItemType Directory -Force (Split-Path $csv) | Out-Null
# rotate if an older-schema file exists, so history is kept but columns stay consistent
if (Test-Path $csv) {
  $existing = (Get-Content $csv -TotalCount 1)
  if ($existing -ne $hdr) {
    Move-Item $csv ($csv -replace '\.csv$', ('_v1_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.csv')) -Force
  }
}
if (-not (Test-Path $csv)) { $hdr | Set-Content $csv -Encoding ascii }

while ($true) {
  $v = @{}
  foreach ($hw in $c.Hardware) {
    $hw.Update()
    foreach ($s in $hw.Sensors) {
      if ($null -ne $s.Value) { $v[($s.SensorType.ToString() + '|' + $s.Name)] = [math]::Round($s.Value, 1) }
    }
  }
  # AUTHORITATIVE first: the foundry states which run is live in CURRENT_RUN.json, so this never infers it.
  # Inferring it from the newest log attaches the logger to the render worker's log instead -- blanking
  # `step`, which makes `training = fresh and step != ""` permanently false and leaves the stall daemon
  # unable to CONFIRM a training stall at all. The worker logs every 30 s while the trainer logs per
  # step, so the slower a run gets the more reliably it loses a "newest" race: the daemon would go
  # blind exactly when the run needed it most.
  $lf = $null
  $cr = Join-Path $root 'var\state\CURRENT_RUN.json'
  if (Test-Path $cr) {
    try {
      $curLog = (Get-Content $cr -Raw | ConvertFrom-Json).log
      if ($curLog -and (Test-Path $curLog)) { $lf = Get-Item $curLog }
    } catch { }
  }
  # NO fallback: the run is STATED, never inferred. A newest-log glob here is the documented anti-pattern:
  # with the factory IDLE (pointer legitimately cleared after a finished run) it stamps the last run's
  # name for hours. The foundry publishes CURRENT_RUN.json in every phase, so a missing pointer MEANS
  # idle, and a blank run column is the truth. The stall daemon treats a blank step as not-training.
  $run = ''; $step = ''; $rate = ''
  if ($lf) {
    $run = $lf.BaseName
    $line = (Get-Content $lf.FullName -Tail 1 -EA SilentlyContinue) -replace [char]13, ''
    # step ONLY from genuine TRAINING bars. An any-"N/M [" parse matches BOOT progress bars too
    # ("Caching text embeddings ... 41/54 [") and reports a step during load phases -- telling the
    # stall daemon "training is active" while the card shows a stall-like load signature.
    # Discriminator: training bars run in s/it (10-400 s/step) and carry "lr:"; boot bars run in it/s
    # and never do.
    if (($line -match 's/it|lr:') -and ($line -match '(\d+)/(\d+)\s*\[')) { $step = $Matches[1] }  # gate FIRST: $Matches must end holding the step groups
    if ($line -match '([0-9.]+)s/it')    { $rate = $Matches[1] }
  }
  # host-side contention read NATIVELY via CIM.
  # WHY NOT Glances: an HTTP call times out precisely during RAM-pressure spikes -- i.e. it drops the exact rows
  # that matter. CIM is in-process, always responsive under load, and has no service dependency.
  $cpu = ''; $ramMb = ''; $ramPct = ''; $swapPct = ''
  try {
    $os = Get-CimInstance Win32_OperatingSystem
    $totKb = [double]$os.TotalVisibleMemorySize; $freeKb = [double]$os.FreePhysicalMemory
    $ramMb = [math]::Round(($totKb - $freeKb) / 1024, 0)
    $ramPct = [math]::Round((($totKb - $freeKb) / $totKb) * 100, 1)
    $cpu = [math]::Round((Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average, 1)
    $pf = Get-CimInstance Win32_PageFileUsage | Select-Object -First 1
    if ($pf -and $pf.AllocatedBaseSize -gt 0) { $swapPct = [math]::Round(($pf.CurrentUsage / $pf.AllocatedBaseSize) * 100, 1) }
  } catch { }

  $row = @(
    (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $run, $step, $rate,
    $v['Clock|GPU Core'], $v['Clock|GPU Memory'], $v['Power|GPU Package'],
    $v['Temperature|GPU Core'], $v['Temperature|GPU Hot Spot'], $v['Load|GPU Core'],
    $v['SmallData|GPU Memory Used'], $v['SmallData|GPU Memory Free'], $v['SmallData|D3D Shared Memory Used'],
    $cpu, $ramMb, $ramPct, $swapPct,
    $v['Load|GPU Memory'], $v['Voltage|GPU Core'], $v['Load|D3D Compute 0']
  ) -join ','
  Add-Content -Path $csv -Value $row -Encoding ascii
  # 10 s rows, not 30: the logger cadence is the rescue daemon's detection floor; at 10 s rows CONFIRM=2
  # reacts in ~15-40 s instead of ~35-95 s. CSV growth is trivial (~8k rows/day).
  Start-Sleep 10
}
