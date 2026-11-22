# RUNBOOK — GPU box fan control (FanControl): the lab standard, BigHonker as a SERVICE (works with or without a login), and how to prove the fans are really controlled

Written 2026-10-05, after BigHonker's case fans turned out to have been on CPU-only motherboard control since January:
FanControl was configured there, but its only launcher was a LOGON task, and BigHonker has no auto-logon: sometimes
the owner or Avery is logged in, sometimes nobody is (the owner, 2026-10-25), so whenever nobody was the fans were never controlled.

## The lab standard (what Mothership and Softserve run)

| | Softserve | Mothership | BigHonker (since 2026-10-05) |
|---|---|---|---|
| FanControl version | V270 | V262 | V270 (copied from Softserve) |
| how it starts | logon task, RunLevel Highest, in an **auto-logon** console session (`AutoAdminLogon=1`, Administrator) | same, `<user>` auto-logon | **Windows service `FanControl.Service`, SYSTEM, automatic, needs no session** (no auto-logon; a person may or may not be logged in) |
| config (selected by `Configurations\CACHE` → `CurrentConfigFileName`) | `OwnerNormal.json` | `userConfig.json` | `BigHonker.json` |
| case fans | on **Mix = max(CPU, GPU)** | on Mix = max(CPU, GPU) (AIO on CPU) | 4 enabled fans (nct6798d control 0, 1, 2, 4) on Mix, **30 % floor** |
| GPU fans | FanControl drives them on "Auto GPU" (`NVApiWrapper/0-GA104-A/sensor/0`) | left to the AMD card | FanControl drives them on "Auto GPU" (same sensor id, same GA104 family) |

The point of the Mix curve: the case fans react to the **hottest** of CPU and GPU. A CPU-only curve leaves the case
fans idle during a GPU-only compute load, because the CPU is near idle.

## Boxes without auto-logon: run FanControl as a SERVICE (V268+)

A logon task does not fire until someone logs in, and on a box without auto-logon that is not every boot, so the fans fall back to the motherboard vendor's control (ASUS
Fan Xpert, usually CPU-based) and nobody notices. FanControl V268+ ships `FanControl.Service.exe`. It runs the
config named in `Configurations\CACHE`, with no session and no UI. The app's own "Start service on boot" checkbox
registers it like this (strings read from `FanControl.dll`); do the same by hand on a box without auto-logon:

```
sc create FanControl.Service binPath= "\"C:\Program Files (x86)\FanControl\FanControl.Service.exe\"" DisplayName= "FanControl Service" type= own type= interact start= auto obj= LocalSystem error= normal
sc description FanControl.Service "FanControl background service"
sc failure FanControl.Service reset= 86400 actions= restart/5000/restart/5000/restart/5000
sc start FanControl.Service
```

⚠ **A CLEAN STOP IS NOT A FAILURE - the `sc failure` restarts never fire for it.** 2026-11-16 03:41 on BigHonker a
Windows Store update of WSL (`wsl.msi` 3.0.5.0) used Windows' Restart Manager, which shut `FanControl Service` down
("Service stopped successfully", Application log, RestartManager 10002 with no 10003 restart after it), and nothing
started it again: the fans sat on the board's CPU-only control and the health daemon's `fan-control` alert fired.
(On 2026-11-11 03:22 the same manager stopped and restarted it.) **The keep-alive (since 2026-11-16):** task
`Lab\FanControl Keepalive`, SYSTEM, highest, every 15 minutes with no end, runs `sc start FanControl.Service` (a no-op
while it runs). It also covers "the service after a reboot", within 15 minutes. Proven with a live decoy: service
stopped by hand, task run, service Running with its process back. Find a stop's cause in the Application log:
`Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=...}` around the time, looking for RestartManager
and MsiInstaller events.

`sc` warns that "interactive" services are deprecated. That flag is the app's own; leave it. Disable the old logon
task, because a UI started in a session stops the service. V270 needs the .NET 10 runtime AND the .NET 10
**WindowsDesktop** runtime (`C:\Program Files\dotnet\shared\Microsoft.WindowsDesktop.App`).

## Prove the fans are controlled — "service RUNNING" is not proof

The service leaves no log and does not re-save its config, so silence proves nothing. Two checks, in order:

1. **It is working:** `(Get-Process FanControl.Service).Modules` includes `LibreHardwareMonitorLib.dll`,
   `NvAPIWrapper.dll` and `nvapi64.dll`, and its CPU time grows over 20 s (a polling loop).
2. **It applies YOUR config (positive control):** set both GPU fan controls to `ManualControl: true, 60` in the
   config, restart the service, and read the GPU fans with NVML (`nvmlDeviceGetFanSpeed_v2`). An idle card on its
   own curve reads 0 %; under the service it must read ~60 %. Then restore the config byte-for-byte and restart.
   Measured on BigHonker 2026-10-05: 0 → 53/47 → 60/61 % within ~12 s, then back to the curve after the restore.
   Script: Claude scratchpad `bh_fc_positive_control.py`. NVML's fan control policy field stays 0 under FanControl
   (it drives the fans through NvAPI), so that field cannot be the proof.

3. **Case fans:** the GPU telemetry logger (v4+) records the board's Super I/O fans as `mb_fan1..7_rpm` /
   `mb_fan1..7_pct`, so a case-fan change shows up there within a few seconds. Read a WINDOW, not one row: on
   BigHonker, header #1's tach drops single readings to 0 rpm (30 times in 486 s, never more than 2 s in a row)
   while the fan spins at every commanded level (step calibration 2026-10-05). A single 0 is not a stopped fan.
   That fan needs no start/stop calibration.

After a reboot: the service is set to automatic start, and the 15-minute keep-alive above starts it within one
interval in any case.

## Housekeeping found doing this

- BigHonker's previous V252 install is archived, verified, at `C:\Program Files (x86)\FanControl.bak-v252-20261005`
  (it holds the January `userConfig.json`).
- Softserve's `C:\Program Files (x86)\FanControl\log.txt` is a **2.1 GB dead log** from January (V251 spamming
  `NVAPI_ERROR` from `GetClientFanCoolersStatus`), not written since 2026-01-14, plus 73 rotated 5 MB logs. It is
  stale disk use; archive or remove it when convenient. ⚠ A tar of the FanControl folder carries these along —
  exclude `log*.txt`.
