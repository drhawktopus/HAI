"""Checks if the model runner is spilling into shared system memory.

Spill is defined as shared memory held while the GPU card is full. Healthy runners may hold some shared memory by design, so a high usage reading alone does not indicate spill; the card-full condition is the required invariant."""
import json
import os
import subprocess
import sys
import time

SPILL_MIN_MB = 512      # threshold set between healthy usage (~170 MB) and spill usage (~1.5 GB)
FULL_MARGIN_MB = 1024   # "full" = within the arsenal's vram_margin (~1 GB) of the card's memory

_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$pids = @(Get-Process llama-server,ollama | ForEach-Object { $_.Id })
$samples = (Get-Counter -Counter '\GPU Process Memory(*)\Shared Usage','\GPU Process Memory(*)\Dedicated Usage','\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction Stop).CounterSamples
$run = @{}; $adapter = @{}
foreach ($x in $samples) {
  $kind = $x.Path.Split('\')[-1]
  if ($x.InstanceName -match '^pid_(\d+)_(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys') {
    if ($pids -contains [int]$Matches[1]) {
      $l = $Matches[2]
      if (-not $run.ContainsKey($l)) { $run[$l] = @{shared = 0.0; dedicated = 0.0} }
      if ($kind -eq 'shared usage') { $run[$l].shared += $x.CookedValue } else { $run[$l].dedicated += $x.CookedValue }
    }
  } elseif ($x.Path -match 'gpu adapter memory' -and $x.InstanceName -match '^(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys') {
    $adapter[$Matches[1]] = $x.CookedValue
  }
}
$cards = @(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*' -Name 'HardwareInformation.qwMemorySize' | ForEach-Object { [double]$_.'HardwareInformation.qwMemorySize' })
$best = $null
foreach ($l in $run.Keys) { if ($best -eq $null -or $run[$l].dedicated -gt $run[$best].dedicated) { $best = $l } }
$o = @{runners = $pids.Count; counter_ok = ($null -ne $samples -and @($samples).Count -gt 0); card_mb = 0; shared_mb = $null; dedicated_mb = $null; adapter_mb = $null}
if ($cards.Count) { $o.card_mb = [math]::Round((($cards | Measure-Object -Maximum).Maximum) / 1MB) }
if ($best -ne $null) {
  $o.shared_mb = [math]::Round($run[$best].shared / 1MB); $o.dedicated_mb = [math]::Round($run[$best].dedicated / 1MB)
  $o.adapter_mb = [math]::Round($adapter[$best] / 1MB)
}
$o | ConvertTo-Json -Compress
"""


def ambient_memory(card_mib):
    """Read ambient memory when the counters prove no runner allocation exists.

    NVIDIA uses its existing per-card memory query; AMD reuses the bounded board-health CSV reader. Age is data:
    the scheduler's existing freshness rule decides whether the AMD reading is usable, not another copied limit.
    """
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=20)
        if p.returncode:
            return None
        used, total = [float(v) for v in p.stdout.strip().split(",")]
        return {"used_mb": used, "age_s": 0.0} if total == card_mib else None
    except FileNotFoundError:
        pass                                      # AMD: the existing telemetry reader owns this measurement
    except Exception:
        return None
    try:
        import foundry_paths
        import p20_board_health
        path = foundry_paths.gpu_telemetry_live()
        header, row = p20_board_health.last_row(path)
        if not header or not row:
            return None
        rec = dict(zip(header, row))
        if rec.get("smi") not in (None, "ok"):
            return None
        return {"used_mb": float(rec["vram_used_mb"]), "age_s": max(0.0, time.time() - os.path.getmtime(path))}
    except Exception:
        return None


# Retry once after a 3s delay: counter samples can fail transiently while the runner set is settling (e.g., mid-model-load). A second failure is definitive and must not be guessed.
RESAMPLE_S = 3


def _query():
    """(reading, None) or (None, why) - one PowerShell counter query."""
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", _PS], capture_output=True, text=True,
                              timeout=90)
        if proc.returncode:
            return None, "powershell exit %s" % proc.returncode
        out = proc.stdout.strip()
        r = json.loads(out.splitlines()[-1]) if out else None
    except Exception as exc:                                               # noqa: BLE001
        return None, "query error %s" % type(exc).__name__
    if not r:
        return None, "empty output"
    if not r.get("counter_ok"):
        return None, "Get-Counter returned no samples"
    if not r.get("card_mb"):
        return None, "card size unreadable"
    return r, None


def snapshot(sleep=None):
    """One successful counter query, including a proven absence of runner allocation; None on a failed query.

    With no allocation the three memory fields are None, not guessed zeros. The load reporter then uses its
    existing per-card reading as ambient memory. Adapter and runner allocations, when present, share one sample.
    A failed query is re-sampled once after RESAMPLE_S (see above).
    """
    if not sys.platform.startswith("win"):
        return None
    r, why = _query()
    if r is None:
        print("[gpu_spill] counter query failed (%s) - re-sampling in %ss" % (why, RESAMPLE_S), flush=True)
        (sleep or time.sleep)(RESAMPLE_S)
        r, why = _query()
        if r is None:
            print("[gpu_spill] counter query failed again (%s) - no reading" % why, flush=True)
            return None
    if all(r.get(k) is None for k in ("adapter_mb", "dedicated_mb", "shared_mb")):
        ambient = ambient_memory(r["card_mb"])
        if ambient is not None:
            r.update(ambient_mb=ambient["used_mb"], ambient_age_s=ambient["age_s"])
    return r


def read():
    """The existing spill-reader contract: None when there is no measured runner allocation."""
    r = snapshot()
    return r if r and r.get("shared_mb") is not None else None


def spilled(r):
    """True when the runner holds shared memory while the card is full (both measured, see the module docstring)."""
    return bool(r) and r["shared_mb"] >= SPILL_MIN_MB and r["adapter_mb"] >= r["card_mb"] - FULL_MARGIN_MB


RELEASED_MB = 1024      # a runner still holding more card memory than this has not let go of its model


def wait_released(timeout=90, poll=3, read_fn=None, sleep=None):
    """After an unload: wait until no runner still holds the card (ollama's /api/ps empties before the memory is
    actually free; loading into that gap is what put gemma3:27b in shared memory). -> True when released (or the
    counters cannot be read - nothing to wait on), False when still held at the timeout."""
    import time
    read_fn, sleep = read_fn or read, sleep or time.sleep
    deadline = time.monotonic() + timeout
    while True:
        r = read_fn()
        if r is None or (r.get("dedicated_mb") or 0) < RELEASED_MB:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(poll)


if __name__ == "__main__":
    reading = read()
    print(json.dumps({"reading": reading, "spilled": spilled(reading) if reading else "could not tell"}))
