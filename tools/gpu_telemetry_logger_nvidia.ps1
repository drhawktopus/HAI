# GPU telemetry logger - NVIDIA boxes. One row per second, daily files.
# WHY: a card can be lost under a clock lock after an hour of continuous load, and a health daemon that polls every
# 20 s and only logs state changes leaves no continuous record - a short clock spike, a hotspot or fan trend, or a
# throttle event before the loss cannot be seen. Mirrors the AMD box's gpu_telemetry_logger.ps1
# (LibreHardwareMonitorLib, same telemetry dir); the per-second cadence is set by the question (spikes shorter
# than the 20 s poll). Throttle reasons come from nvidia-smi with a hard timeout, so a wedged card cannot hang the
# logger - a failed read is written as a failed read, never a value.
# Installed by install_gpu_telemetry_logger.ps1 as the "GPU Telemetry Logger" task (SYSTEM, boot + 15-min repeat).
#   Also logged: encoder/decoder utilisation (the engines a media server transcodes on, when the box shares its card
#   with one), box-agnostic paths (the foundry root differs per box), and a header check that sets a day's file
#   aside on a column change instead of mixing two layouts in one file.
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
#   The motherboard's Super I/O (case-fan rpm and the % FanControl commands; raw voltages, because
#   this LHM build has no board map, so +12 V shows as an unscaled 'Voltage #n' - a sag still shows as a change) and
#   the UPS (apcupsd NIS on 127.0.0.1:3551, read every 5 s): whole-box power on the same clock as the card, the
#   instrument that is independent of the GPU driver. LHM serialises Super I/O access with FanControl's own LHM.
$c.IsMotherboardEnabled = $true
$c.Open()

$dir = Join-Path $root 'var\state\telemetry'
New-Item -ItemType Directory -Force $dir | Out-Null
#   Reads ONLY the NVIDIA card - a box with an integrated Radeon too has sensors that share
#   names (Clock|GPU Core ...) and would overwrite or fill the NVIDIA columns depending on enumeration order; + the
#   memory-junction temperature where the card reports it (GDDR6X does, GDDR6 does not).
$hdr = 'ts,run,core_mhz,mem_mhz,power_w,power_limit_pct,board_power_pct,temp_c,hotspot_c,mem_junction_c,fan1_rpm,fan2_rpm,fan1_pct,fan2_pct,vcore_v,gpu_load,mem_ctrl_load,bus_load,vram_used_mb,shared_spill_mb,pcie_rx_bps,pcie_tx_bps,pstate,throttle_reasons,enc_util,dec_util,smi,' +
       'mb_fan1_rpm,mb_fan2_rpm,mb_fan3_rpm,mb_fan4_rpm,mb_fan5_rpm,mb_fan6_rpm,mb_fan7_rpm,' +
       'mb_fan1_pct,mb_fan2_pct,mb_fan3_pct,mb_fan4_pct,mb_fan5_pct,mb_fan6_pct,mb_fan7_pct,' +
       'mb_vcore_v,mb_v2_raw,mb_v5_raw,mb_v7_raw,mb_v14_raw,ups_load_pct,ups_line_v,ups_status'
$ups = @{ t = [datetime]::MinValue; v = ',,' }

function Read-Ups {
  # apcupsd NIS 'status' on the local port, 1 s socket timeout: "load,linev,status" or ",,error"
  try {
    $t = New-Object System.Net.Sockets.TcpClient
    $ar = $t.BeginConnect('127.0.0.1', 3551, $null, $null)
    if (-not $ar.AsyncWaitHandle.WaitOne(1000)) { $t.Close(); return ',,timeout' }
    $t.EndConnect($ar); $t.ReceiveTimeout = 1000
    $st = $t.GetStream()
    $cmd = [Text.Encoding]::ASCII.GetBytes('status')
    $st.Write([byte[]](0, $cmd.Length), 0, 2); $st.Write($cmd, 0, $cmd.Length)
    $f = @{}
    while ($true) {
      $h = New-Object byte[] 2
      if ($st.Read($h, 0, 2) -lt 2) { break }
      $n = $h[0] * 256 + $h[1]
      if ($n -eq 0) { break }
      $buf = New-Object byte[] $n; $got = 0
      while ($got -lt $n) { $r = $st.Read($buf, $got, $n - $got); if ($r -le 0) { break }; $got += $r }
      $ln = [Text.Encoding]::ASCII.GetString($buf, 0, $got)
      if ($ln -match '^(\w+)\s*:\s*(.*)$') { $f[$matches[1]] = $matches[2].Trim() }
    }
    $t.Close()
    $load = ($f['LOADPCT'] -split ' ')[0]; $linev = ($f['LINEV'] -split ' ')[0]; $status = ($f['STATUS'] -replace ',', ' ')
    return ('{0},{1},{2}' -f $load, $linev, $status)
  } catch { return ',,error' }
}
$heartbeat = Join-Path $dir 'gpu_telemetry.heartbeat'
$currentRun = Join-Path $root 'var\state\CURRENT_RUN.json'

function Read-Smi {
  # one nvidia-smi query, killed after 5 s: "pstate,reasons,enc,dec,ok" or ",,,,timeout" / ",,,,error"
  $p = New-Object System.Diagnostics.Process
  $p.StartInfo.FileName = 'nvidia-smi.exe'
  $p.StartInfo.Arguments = '--query-gpu=pstate,clocks_event_reasons.active,utilization.encoder,utilization.decoder --format=csv,noheader,nounits'
  $p.StartInfo.UseShellExecute = $false
  $p.StartInfo.RedirectStandardOutput = $true
  $p.StartInfo.RedirectStandardError = $true
  $p.StartInfo.CreateNoWindow = $true
  try { [void]$p.Start() } catch { return ',,,,error' }
  $out = $p.StandardOutput.ReadToEndAsync()
  if (-not $p.WaitForExit(5000)) { try { $p.Kill() } catch { }; return ',,,,timeout' }
  $txt = ($out.Result -replace "`r|`n", '').Trim()
  if ($p.ExitCode -ne 0 -or -not $txt) { return ',,,,error' }
  $parts = $txt -split ',\s*'
  if ($parts.Count -lt 4) { return ',,,,error' }
  return ('{0},{1},{2},{3},ok' -f $parts[0], $parts[1], $parts[2], $parts[3])
}

while ($true) {
  $now = Get-Date
  $csv = Join-Path $dir ('gpu_telemetry-' + $now.ToString('yyyyMMdd') + '.csv')
  if ((Test-Path $csv) -and ((Get-Content $csv -TotalCount 1) -ne $hdr)) {
    Move-Item $csv ($csv -replace '\.csv$', ('_prev-schema_' + $now.ToString('HHmmss') + '.csv')) -Force
  }
  if (-not (Test-Path $csv)) { $hdr | Set-Content $csv -Encoding ascii }

  $v = @{}
  $mb = @{}
  foreach ($hw in $c.Hardware) {
    $ht = $hw.HardwareType.ToString()
    if ($ht -eq 'Motherboard') {
      foreach ($sub in $hw.SubHardware) {
        try { $sub.Update() } catch { }
        foreach ($s in $sub.Sensors) {
          if ($null -ne $s.Value) { $mb[($s.SensorType.ToString() + '|' + $s.Name)] = [math]::Round($s.Value, 3) }
        }
      }
      continue
    }
    if ($ht -ne 'GpuNvidia') { continue }
    try { $hw.Update() } catch { }
    foreach ($s in $hw.Sensors) {
      if ($null -ne $s.Value) { $v[($s.SensorType.ToString() + '|' + $s.Name)] = [math]::Round($s.Value, 2) }
    }
  }
  if (($now - $ups.t).TotalSeconds -ge 5) { $ups.v = Read-Ups; $ups.t = $now }
  # the run is STATED by the foundry, never inferred (the same rule as the AMD box's logger)
  $run = ''
  try { $run = (Get-Content $currentRun -Raw | ConvertFrom-Json).name } catch { }
  $smi = Read-Smi
  $row = @(
    $now.ToString('yyyy-MM-dd HH:mm:ss'), $run,
    $v['Clock|GPU Core'], $v['Clock|GPU Memory'], $v['Power|GPU Package'], $v['Load|GPU Power'],
    $v['Load|GPU Board Power'], $v['Temperature|GPU Core'], $v['Temperature|GPU Hot Spot'],
    $v['Temperature|GPU Memory Junction'],
    $v['Fan|GPU Fan 1'], $v['Fan|GPU Fan 2'], $v['Control|GPU Fan 1'], $v['Control|GPU Fan 2'],
    $v['Voltage|GPU Core Voltage'], $v['Load|GPU Core'], $v['Load|GPU Memory Controller'], $v['Load|GPU Bus'],
    $v['SmallData|GPU Memory Used'], $v['SmallData|D3D Shared Memory Used'],
    $v['Throughput|GPU PCIe Rx'], $v['Throughput|GPU PCIe Tx'], $smi,
    $mb['Fan|Fan #1'], $mb['Fan|Fan #2'], $mb['Fan|Fan #3'], $mb['Fan|Fan #4'], $mb['Fan|Fan #5'], $mb['Fan|Fan #6'],
    $mb['Fan|Fan #7'],
    $mb['Control|Fan #1'], $mb['Control|Fan #2'], $mb['Control|Fan #3'], $mb['Control|Fan #4'], $mb['Control|Fan #5'],
    $mb['Control|Fan #6'], $mb['Control|Fan #7'],
    $mb['Voltage|Vcore'], $mb['Voltage|Voltage #2'], $mb['Voltage|Voltage #5'], $mb['Voltage|Voltage #7'],
    $mb['Voltage|Voltage #14'], $ups.v
  ) -join ','
  Add-Content -Path $csv -Value $row -Encoding ascii
  Set-Content -Path $heartbeat -Value $now.ToString('yyyy-MM-dd HH:mm:ss') -Encoding ascii
  $spent = ((Get-Date) - $now).TotalMilliseconds
  if ($spent -lt 1000) { Start-Sleep -Milliseconds (1000 - [int]$spent) }
}
