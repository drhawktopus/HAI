# gpu-free.ps1 - report AMD GPU VRAM as JSON for the Home AI orchestrator.
# Output: {"total_mb":24576,"used_mb":573,"free_mb":24003}
# Used by the orchestrator's Mothership gaming-busy pre-check: if free_mb is
# below the 27B model's footprint + headroom, route to BigHonker instead of
# loading a model onto a GPU a game is already using.
$ErrorActionPreference = "SilentlyContinue"

# Total dedicated VRAM from the AMD card's registry key.
# qwMemorySize is a 64-bit value (Win32_VideoController.AdapterRAM overflows >4GB).
$total = 0
Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}" |
  ForEach-Object {
    $p = Get-ItemProperty $_.PSPath
    if ($p.DriverDesc -match "Radeon|AMD" -and $p.'HardwareInformation.qwMemorySize') {
      $v = [int64]$p.'HardwareInformation.qwMemorySize'
      if ($v -gt $total) { $total = $v }
    }
  }
$total_mb = [int][math]::Round($total / 1MB, 0)

# Current usage = max Dedicated Usage across GPU adapter instances. The AMD card
# dominates; the Parsec / Remote virtual display adapters report ~0.
$used_mb = 0
(Get-Counter "\GPU Adapter Memory(*)\Dedicated Usage").CounterSamples |
  ForEach-Object {
    $m = [int][math]::Round($_.CookedValue / 1MB, 0)
    if ($m -gt $used_mb) { $used_mb = $m }
  }

$free_mb = $total_mb - $used_mb
[pscustomobject]@{ total_mb = $total_mb; used_mb = $used_mb; free_mb = $free_mb } | ConvertTo-Json -Compress
