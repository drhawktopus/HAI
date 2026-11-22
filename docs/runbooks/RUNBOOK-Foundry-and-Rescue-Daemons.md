# RUNBOOK — Foundry Health, Stall-Rescue & Telemetry Daemons



### Foundry Health Daemon — "Daemon: Not reporting" on the homepage (BigHonker / Softserve)

**What it is.** A per-box daemon that watches the GPU and pushes its own verdict to
`POST /fleet/rescue-state`, which is what fills the **Daemon** row on each factory tile. Mothership
runs the full stall-rescue daemon; BigHonker and Softserve run the lighter card-health variant
(clock lock, GPU-lost, frozen telemetry, caption progress, **fan control, hotspot**).

**Clock lock: an order can own the clock (2026-10-16).** The clock duty re-applies the box's lock
(`FOUNDRY_TARGET_CLOCK`; **0 = OFF on BigHonker since 2026-10-16 10:15 — the 1500 MHz lock was retired on the new PSU,
boot task `BH GPU Clock Lock` disabled; the investigation doc's status line has the rollback**) whenever the card reads above it, EXCEPT while the stated run
(`CURRENT_RUN.json`, fresh within `CURRENT_RUN_STALE_S`) is an order whose spec declares `"gpu_clock"`. The Daemon
line then reads `clock duty yielded to order <name> (gpu_clock=unlocked)`, and the lock returns within one poll of the
order ending. Do NOT switch the duty off with `FOUNDRY_TARGET_CLOCK=0` to run a clock test: declare `gpu_clock` in the
order (`workspace\bhcrash\submit_bhcrash.py` does). Details: [[BigHonker-3070-DPC-Watchdog-Investigation]] §9h.

**Fan control + hotspot on every GPU box (2026-10-05, LAB-TOOL-GAPS #2/#6).** One shared module,
`p20_board_health.py`, used by both daemons, adds to each box's Daemon line:
`hotspot 52 C (core 44, gap 8); fan control running (FanControl.exe); 2 of 7 case-fan headers reading rpm`.
- FanControl is matched by EXACT image name (`FanControl.exe` app, `FanControl.Service.exe` service).
  `AsusFanControlService.exe` runs on every box and is ASUS's own service, NOT ours.
- Absent on BigHonker/Softserve = ALERT + DM (`fan-control` key). On Mothership the stall-rescue report
  carries the line, report only, and its AMD logger records no case-fan rpm.
- Telemetry comes from `foundry_paths.gpu_telemetry_live()`: the NVIDIA loggers write a dated daily file
  `gpu_telemetry-YYYYMMDD.csv`; Mothership's AMD logger writes the undated `gpu_telemetry.csv`.
- Restart a health daemon onto new code with its own flag, not a kill: create `STOP_HEALTH` in the
  telemetry dir, wait for the process to exit, delete the flag, `schtasks /run` the task, and confirm the
  new PID started after the file. Do it over ssh: a session's filtered token cannot see the daemon's
  command line, so a process search from the session finds nothing.

| box | task name | runs |
|---|---|---|
| Softserve | `SS Foundry Health` | `E:\foundry\etc\softserve_health.cmd` |
| BigHonker | `BH Health Daemon` | `F:\foundry\etc\bh_health.cmd` |
| Mothership | (stall rescue daemon) | see [[Driver-RootCause-Investigation-Plan]] |

**If a tile says `Daemon: Not reporting`,** the daemon process is gone. Restart it — and use ssh to
the box itself, because a session's own token is filtered:

```
ssh softserve "schtasks /run /tn \"SS Foundry Health\""
ssh bighonker "schtasks /run /tn \"BH Health Daemon\""
```

Then confirm it actually reported, rather than that the command succeeded:

```
ssh sundae "curl -s http://localhost:8099/p20/factory/softserve"
```

`health` should read `watching` within a minute. The row flips to `DOWN` after **300 s** of silence.

⚠ **A guard's own death is the one failure it cannot report — a daemon task without a restart
policy expires silently.** Both daemons are therefore self-healing: **a past-dated -Once time
trigger carrying a 15-minute repetition, plus an AtStartup trigger, plus `RestartCount 3` +
`ExecutionTimeLimit 0`**. The repeat is safe because `MultipleInstances` is `IgnoreNew` — it
re-fires blindly and the scheduler drops it if the daemon is already alive.

⚠⚠ **THE REPETITION MUST HANG OFF A PAST-DATED `-Once` TIME TRIGGER, NEVER THE BOOT TRIGGER.** A
boot trigger's repetition window only opens when that trigger FIRES — i.e. at the next reboot — so
on a box that has been up for days the repeat never runs and **the task heals nothing while reading
back as perfectly configured**. Every setting verifies (`RestartCount 3`, `PT15M`, boot trigger
present) and the daemon still stays dead. The tell is in the scheduler itself:

```
NextRunTime: N/A          <- inert; the repetition will not fire until reboot
NextRunTime: 11:30:00     <- correct; a past-dated time trigger repeats from now
```

Reading the settings back passes forever on an inert task. **Kill the daemon and watch it come
back — that is the only verification that means anything.**

To re-apply, or to give any other guard the same treatment:

```
ssh <box> "powershell -NoProfile -ExecutionPolicy Bypass -File <path>\fix_health_daemon_task.ps1 -TaskName \"SS Foundry Health\""
```

Script lives at `E:\SSDShared\Lab\bin\fix_health_daemon_task.ps1` (moved 2026-08-13 from the
Claude Home tree — older journals cite the old path); it is idempotent and prints BEFORE/AFTER
plus a VERIFIED line. It is also what gave all three factory ORDER TAKERS their restart policy on
2026-08-13 (`P20 Foundry Runner` / `BH Foundry Runner` / `SS Foundry Runner`) — same defect, same
cure. ⚠ Continuous daemons ONLY: never put a repetition on the on-demand tasks (`P20 Foundry
Trainer`, `P20 Foundry ComfyUI` / `P20 Worker ComfyUI`, `P20 Dataset Job`) — a 15-min repeat there
launches a trainer every 15 minutes.

⚠ Two PowerShell traps it encodes, both of which produce a *silent* wrong result:
`[TimeSpan]::MaxValue` is NOT "indefinite" — it serialises to `P99999999DT23H59M59S`, the scheduler
rejects the whole task XML as out of range, and **the write fails entirely, leaving the broken
settings in place**. Indefinite repetition is an empty Duration. And wrap a `Where-Object` in `@()`
before reading `.Count`: in PS 5.1 a single match returns the object, not an array, so `.Count` is
`$null` and a verifier reports FAILED on a write that succeeded.

### Duty 7 — "two models or one" on Softserve: McCoy stays loaded, Plex first (2026-10-23)

**What it does.** Softserve's ollama holds two models (`OLLAMA_MAX_LOADED_MODELS=2`, a deliberate per-card setting —
[[RUNBOOK-Ollama-Host-Operations]] § "Softserve — ollama config") so McCoy's model (tev1) stays loaded beside each
turn's embed model instead of the two evicting each other every turn. Every 20 s poll, the health daemon reads the
card's free memory (`nvidia-smi` total − used; this card does not drive the display, so empty reads 0 MiB used) and
how much it fell since the last poll. With two or more models on the card and the free memory projected one poll ahead
under **512 MiB** (the fit reserve the launchers already give llama.cpp), it unloads every model except McCoy's
through ollama's own unload call (`/api/generate`, empty prompt, `keep_alive 0`; ollama finishes a request in flight
first). Card memory it cannot read = one at a time. Ollama's own fit check at load reads free memory with Plex in it,
so it does not co-load while Plex holds the room. the owner, 2026-10-23: *"make it have to use one at a time if plex's
transcode usage trends to rising above what can hold both."*

**Where it shows.** Softserve's tile, Daemon line: `models: two models: free … MiB` / `… one at a time; unloaded …`;
status extra `models`. Acting passes are logged in `health_daemon.log` as `duty 7 (Plex first): …`.

**Settings.** On by default only on `softserve` (`FOUNDRY_PLEX_MODEL_GUARD=0` turns it off; `=1` turns it on
elsewhere); keeper `FOUNDRY_RESIDENT_KEEPER` (default `tev1:latest`). McCoy's own call sends `keep_alive -1` only on
`config.MCCOY_RESIDENT_HOSTS`: the `MCCOY_RESIDENT_HOSTS` setting when it is set (empty = none), else the roster's
advisor box (`softserve` in this lab), and none on a one-device head, where McCoy would hold room the person's own
model needs (`config.mccoy_resident_hosts`, pinned by `tests/test_mccoy_residency.py` since 2026-11-21).

**Proven 2026-10-23 10:43 (live decoy, `scratchpad\decoy_two_or_one.py`, the DEPLOYED daemon's functions):** both
models on the card together (tev1 5,338 MiB + nomic 320 MiB, 2,192 MiB free); one pass with the room marked taken
unloaded nomic and kept tev1; one pass at the real reserve with both loaded kept both. McCoy answered warm in
**0.18 s** (every live call before this reloaded him: 6.7–10.8 s). Tests: `test_foundry.HealthDaemonTwoOrOne` (10).

### 🚑 THE STALL RESCUE DAEMON — where `p20_stall_rescue.py` ACTUALLY lives, and what launches it

**Its code is NOT under `F:\foundry\`.** The launcher and the logs are, which is exactly why this
keeps getting re-derived. Verified end to end on Mothership 2026-08-16:

```
Scheduled task "P20 Stall Rescue"   (runs as the console-user SID …-1001, repetition PT1M on BOTH triggers, IgnoreNew)
  └─ wscript.exe //B "F:\foundry\etc\headless\p20_stall_rescue.vbs"     ← launcher (hides the console)
       └─ cmd /c F:\ai-toolkit\venv\python.exe -u F:\ai-toolkit\p20_stall_rescue.py
            >> F:\foundry\var\state\telemetry\rescue_console.log 2>&1    ← console log
```

* **Daemon source:** `F:\ai-toolkit\p20_stall_rescue.py` — the ONLY copy on the box (bounded search,
  1 match). It lives in the ai-toolkit **external block** deliberately and runs on the ai-toolkit
  **venv** interpreter, not a plain python.
* **Console log:** `F:\foundry\var\state\telemetry\rescue_console.log` (stdout+stderr, appended).
* **Telemetry it reads:** take the path from the daemon's OWN `TELEM` constant — currently
  `F:\foundry\var\state\telemetry\gpu_telemetry.csv`. **Never** infer it from a filename: the green
  cutover moved it once already, and the stale sibling left in the parent dir read as live data for
  eight days.

**Deploying it (2026-10-05):** `python fleet_deploy.py --no-restart --only mothership p20_stall_rescue.py`. The tool's
`ELSEWHERE` table sends it to `F:\ai-toolkit\` (backup beside it) and NOT to any box's `bin`. A `bin` copy is a decoy
nothing runs: one was diffed on 2026-10-05 and misread as a missing fix for an hour. The decoys were archived to
`<root>\backup\p20_stall_rescue.py.bin-decoy-retired-20261005`. A `--no-restart` deploy never refuses an order in
flight (it ships the file and restarts nothing; a running process keeps the copy it loaded), and since 2026-10-17 a
RESTART deploy does the pause-and-redeploy itself (stop slip → deploy → requeue — never wait). Then restart the daemon
with its own flag: create `F:\foundry\var\state\telemetry\STOP_RESCUE`, wait for the process to exit (the log says
`STOP_RESCUE flag seen -> exiting cleanly`; the daemon removes the flag itself); the task's 1-minute repetition
relaunches it. Prove the new start banner is after the file and the heartbeat is fresh. ⚠ 2026-10-07 the deployed task
had DRIFTED to PT15M on both triggers (the guard stayed down after a restart) — restored to PT1M and proven by a live
decoy. If the banner does not reappear within ~2 minutes, read the triggers (`Get-ScheduledTask 'P20 Stall Rescue'`)
before anything else: the doc states the design; a deployed value worse than the design is a regression to fix. It
imports `p20_board_health` from `F:\foundry\bin` (appended to its path), so ship that module there first.

⚠ It is a **guard**, so the standing guard rules apply: verify with a live decoy (kill it, watch the
scheduler bring it back) rather than by reading settings; never conclude "not running" from a
`CommandLine` match (that filter matches the querying process itself); and liveness is a fresh
heartbeat FILE — process-exists and push-age have each lied about this specific daemon before.

### Krea 2 on the stall daemon — NO burst, NO recycle; renders and trainers judged by their own progress (v6.3, 2026-10-26)

The burst ladder, the power/clock wedge signature and the 43.5 s/it stalled band were all calibrated on FLUX.2, and
on Krea 2 every one of them misfires: a healthy Krea trainer at 1024 px reads `~290 W, clk ~3050, load 100` (the FLUX
wedge signature) at 44–100 s/it (past the band), and a burst is a second GPU context on a card Krea holds — the lab's
documented wedge cause. 2026-10-26: render-start bursts were followed by ComfyUI's death within ~70 s, five renders
running; at 19:17 the daemon "confirmed a stall" on a healthy 1024 trainer, burst onto it and requested a recycle,
and that trainer hung in the driver until the owner rebooted.

What the daemon does now (`NO_BURST_UNETS = ("krea",)`, tests in `test_stall_rescue.py`):
- **Krea render** (ComfyUI's UNETLoader names `krea…`): no prophylactic burst; a stall is a wedge only when
  `render_progress.json` stops advancing — then the :8189 owner is ended (no burst) and the owner gets a DM.
- **Krea trainer** (the stated run's order says `"base": "krea2"`): no prophylactic, no signature stall, no
  TRAIN-DEGRADED, no recycle. It logs `KREA TRAINER STATIC` and DMs once when the step has not advanced for 3× the
  run's own slowest step so far (≈70 s at 448 px, ≈5 min at 1024; saves never exceeded 23 s; preview pauses publish
  `rendering` and are excluded) — **report only**, and only after the run has shown 10 step intervals: a mixed
  [512, 1024] run draws its bucket at random per step, and judging before it had met a 1024 step DM'd a false
  "static" (kq_int8, 21:44: six ~11 s steps, then a normal ~69 s one). The one Krea training hang seen ignored every kill; the cure was a
  reboot ([[RUNBOOK-Compute-and-Training-Order-Submission]] § "FOUNDRY HELD — a trainer will not stop").
- FLUX.2 renders and trainers keep the full ladder. An order with no `base`, or an unreadable spec, keeps it too.

Before another image model renders or trains on Mothership, read its healthy telemetry against these FLUX rules —
Krea showed the calibration does not transfer.

### GPU Telemetry Logger (Mothership)
Continuous GPU time-series for the RX 7900 XTX. **Why it exists:** an on-demand read leaves no
history, and a step-rate regression without history cannot be explained after the fact.

- Script: `gpu_telemetry_logger.ps1` in the foundry mirror, deployed to `F:\foundry\bin` by `fleet_deploy`; the task runs
  that copy (since 2026-11-18). It takes the foundry root from the environment, else the `FOUNDRY_ROOT` machine variable,
  else `FOUNDRY_ROOT` in `C:\ProgramData\HAI\lab.env` (the reporters' rule: **Mothership and BigHonker have NO machine
  variable, only Softserve does**), and the sensor library from `FOUNDRY_LHM_DLL` in `F:\foundry\etc\lab.env` (placed:
  `F:\tools\LHM\LibreHardwareMonitorLib.dll`); no GUI/web server. **Register or re-point the task** with the lab's installer,
  shipped to ProgramData by `fleet_deploy` and run ON the box:
  `ssh mothership "powershell -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\HAI\install_gpu_telemetry_logger.ps1 -Script F:\foundry\bin\gpu_telemetry_logger.ps1"`
  (stops the running instance, registers the same self-healing policy, starts it, prints BEFORE/AFTER). Verify: the task
  `Running` and `gpu_telemetry.csv` fresh - `Ready` with Last Result 1 means the logger exited at its first line (the root).
- Task: **"GPU Telemetry Logger"** — SYSTEM, RunLevel Highest, **AtStartup + a 15-min repetition (empty duration =
  indefinite)**, `MultipleInstances IgnoreNew`, `ExecutionTimeLimit 0`, 3 restarts. ⚠ The repetition was MISSING on
  Mothership until 2026-10-22 (BigHonker/Softserve had it): when that morning's AMD driver install reset the GPU at
  ~09:29 the logger exited 0 — `RestartCount` only fires on a FAILURE — and nothing restarted it. The CSV froze, the
  load reporter sent `stale` samples (`telemetry csv 9814s old`), and model_fit HELD every chat window to Mothership
  (`no fresh card-memory report`) for ~2.7 h; found by a chat-window live proof. A daemon that must always run needs
  the repeat; the restart count alone does not cover a clean exit. **Same pass, the same repeat (+ `IgnoreNew`) went on
  every long-running guard task that lacked it** (the scratchpad `guard_repeat.py` refuses a task not `Running` — on a
  launcher that starts something detached and exits, a repeat would start duplicates): Mothership `HAI Keep-Awake`,
  `HAI Ollama Watchdog`, `HAI TextInput Watchdog`; BigHonker `BH Foundry Worker`, `HAI Keep-Awake`. **Live decoy:**
  Mothership's TextInput Watchdog stopped at 12:19 → came back by itself at 12:30:01 (the next quarter), no hand.
  ⚠ **The repeat is only safe on a task whose OWN process is the daemon.** Mothership's logger ran as `wscript //B
  …\gpu_telemetry_logger.vbs`, whose `Run …, 0, False` starts PowerShell detached and exits — the task never reads
  Running, `IgnoreNew` never sees an instance, and the repeat started a second and third logger (three appending to
  one CSV by 12:30). Fixed 2026-10-22: the task now runs `powershell.exe -NoProfile -ExecutionPolicy Bypass
  -WindowStyle Hidden -File "F:\tools\gpu_telemetry_logger.ps1"` directly — BigHonker/Softserve's shape. Proven: the
  12:45 repeat started exactly ONE logger, task `Running`, CSV writing, load report fresh. (The .vbs is now unused.)
- Output: **`F:\foundry\var\state\telemetry\gpu_telemetry.csv`**, one row / 10 s (v5 cadence — the logger is the
  stall daemon's detection floor):
  `ts,run,step,rate_s_it,core_mhz,mem_mhz,power_w,temp_c,hotspot_c,gpu_load,vram_used_mb,vram_free_mb,shared_spill_mb,cpu_pct,ram_used_mb,ram_pct,swap_pct,mem_load_pct,vcore_v,d3d_compute`
- The run column is **STATED, never inferred**: it reads `F:\foundry\var\state\CURRENT_RUN.json` and
  follows the log that pointer names. No pointer = factory idle = blank run/step — a newest-log
  fallback stamps a finished run's name for hours and is forbidden. `shared_spill_mb` = D3D Shared
  Memory Used = the **PCIe spill** (the known rate killer).

```powershell
Start-ScheduledTask -TaskName "GPU Telemetry Logger"     # restart
Get-Content 'F:\foundry\var\state\telemetry\gpu_telemetry.csv' -Tail 5
```
**Rule: a monitor that isn't started, persisted, and writing history is not monitoring.**

### UPS Power Logger (Mothership) — WHOLE-BOX WATTS at the wall, CyberPower GX1500U over USB HID
Installed 2026-10-17 for #34 energy pricing: the card meter (`gpu_j`) undercounts CPU-heavy runs (Flash-Next on
Strata holds the CPU at ~95 %), so whole-box joules come from the UPS. Reads the UPS through Windows' own `hid.dll`
with a SHARED handle — **PowerPanel keeps the device; nothing installed, no shutdown policy changed.**

- Script: `C:\ProgramData\HAI\ups_power_logger.py` (source `E:\SSDShared\mothership-build\hostscripts\`), run by the
  plain Python `pythoncore-3.14-64` (not a factory venv, so the GPU police do not read it as an AI job)
- Task: **"UPS Power Logger"** — SYSTEM, AtStartup + a 15-min repetition with `IgnoreNew`, `RestartCount 3`, no time
  limit (installer `install_ups_logger_task.ps1`, same shape as the HAI Stall Recorder)
- Output: **`F:\foundry\var\state\telemetry\ups_power.csv`**, one row / 5 s:
  `ts,epoch,active_w,apparent_va,load_pct,volts,runtime_s` — `ts` carries date + UTC offset; join per-order joules by
  `epoch` against the A1 rows' `t0`/`t1`. A failed read writes NO row (a gap, never a fake 0 W) and reopens next tick.
- ⚠ `volts` reads ~274 on 120 V mains — that HID field's unit exponent is not applied; **do not use it**. `active_w`
  cross-checks (523 W = 58 % of the unit's 900 W rating). The figure is everything on the battery outlets.

```powershell
Start-ScheduledTask -TaskName "UPS Power Logger"         # restart
Get-Content 'F:\foundry\var\state\telemetry\ups_power.csv' -Tail 5
```

### Tolkien Telemetry Logger (rigby + GT 730 + every Fellowship tablet) — SEARCHABLE store, read by SUMMARY, never raw

One of Tolkien's organs (since 2026-10-10, #36): Tolkien's code and image, deployed with Tolkien by `fleet_deploy`,
writing Tolkien's state dir; its own container **`tolkien-telemetry`** (rigby `/opt/arrstack/docker-compose.yml`) only
so it keeps recording while the order taker is down or being redeployed — the reason `tablet-watchdog` is separate.
**Why it exists:** 2026-10-10 a pipelined #36 bench ran 25 min before anyone could see that its stages computed ONE
AT A TIME; the only views were hand-rolled peeks. the owner: telemetry first, and *"searchable and not just something
you'll read every speck of every time."*

- Script: `tolkien_telemetry.py` (`E:\SSDShared\tolkien-build`, baked into the Tolkien image). `pid: host` (it sees
  every stage process), `network_mode: host`, the host's `nvidia-smi` run under the host loader (`/hostlib`).
- Cadence: rigby every 1 s (per-core CPU, any `ggml-rpc-server` / `llama-bench` / `llama-server` process, MemAvailable,
  every `enx*`/`br0` link, GT 730 memory/clocks/power/temp every 5 s), each wire tablet every 2 s over adb (its llama
  processes from per-process `/proc/<pid>/stat` — never `/proc/stat` or Termux `top`, see
  [[Tablet-Instrumentation-Maintenance]] — MemAvailable, temperature, `rndis0`/`wlan0` bytes). Run column STATED from
  Tolkien's `CURRENT_ORDER*.json`. A link row is written only when it carried traffic; an unreachable tablet writes a
  `note`, never a zero.
- Store: **`/opt/arrstack/tablet-foundry/state/telemetry/telemetry.sqlite`** (in the container
  `/tabfoundry/state/telemetry/`), one `samples` table indexed on (run, ts), (node, ts), ts; rows older than 7 days
  pruned (`TOLKIEN_TELEMETRY_RETAIN_DAYS`). Heartbeat: `telemetry.heartbeat` beside it (liveness = its age).
- **Read it — answers, not dumps (a dozen lines whatever the store holds):**
  ```bash
  ssh rigby 'docker exec tolkien-telemetry python tolkien_telemetry.py summary --run <order name or part>'
  ssh rigby 'docker exec tolkien-telemetry python tolkien_telemetry.py summary --since <epoch> --until <epoch>'
  ssh rigby 'docker exec tolkien-telemetry python tolkien_telemetry.py tail --node gandalf --last 60'
  ```
  `summary` gives one line per node (llama CPU mean/max, link MB/s, memory min, temperature; rigby adds total CPU and
  card memory) plus **"stages busy at the same moment: max N"** — a stage = a llama process at ≥ 200 % CPU within one
  2 s slot. In-turn pipeline stages read **1**; overlapping stages read more. The `bench` order's `max_busy_stages`
  and the homepage tile's live "busy now N/M" line come from this same store (one derivation).
- Restart policy `unless-stopped`, **proven by a live crash decoy 2026-10-11 02:30** (logger process SIGKILLed from
  inside → Docker restarted it the same second, new PID, fresh heartbeat). ⚠ A MANUAL `docker kill` / `docker stop`
  leaves it DOWN by design (Docker's operator stand-down) — `docker start tolkien-telemetry` brings it back; a decoy
  must crash the process (`python -c "import os,signal; os.kill(<heartbeat pid>, signal.SIGKILL)"` inside the
  container, after checking that PID's cmdline is `tolkien_telemetry.py` — with `pid: host`, `kill 1` would hit rigby's
  init).

### GPU Telemetry Logger (BigHonker + Softserve, NVIDIA) — one row per SECOND, hotspot + fans + throttle reasons + encoder/decoder

**Softserve added 2026-09-28 22:20** (the owner: "yes to logging") — to measure Plex's real footprint (VRAM, encoder /
decoder load, any compute) so our work can SHARE the card with Plex instead of being evicted by it. There it lives in
`E:\tools\` (Softserve has no F:) and writes `E:\foundry\var\state\telemetry\`. **v3 columns** (both boxes) add
`mem_junction_c` (the 3070 Ti's GDDR6X reports it; BigHonker's GDDR6 does not → blank) and `enc_util,dec_util`
(nvidia-smi — the engines Plex transcodes on). ⚠ Softserve ALSO has the Ryzen's integrated Radeon; the logger reads
ONLY the NVIDIA card (v1/v2 merged all GPUs' sensors by name, so shared names like `Clock|GPU Core` could come from
either card). Verified both boxes: 27 columns, 0 malformed rows; each day's earlier-schema file is set aside as
`gpu_telemetry-<date>_prev-schema_<time>.csv`, never overwritten.
**Why it exists (2026-09-28):** BigHonker's RTX 3070 was lost at the 1500 MHz clock lock after ~72 min of continuous
load and there was no continuous record — the health daemon polls every 20 s and logs only state changes, so a
short clock spike, a hotspot/fan trend or a throttle event before a loss could not be seen
([[BigHonker-3070-DPC-Watchdog-Investigation]] §0y). Same library as Mothership's logger (LibreHardwareMonitorLib,
copied to `F:\tools\LHM\`); the 1 s cadence is set by that question.

- Script: `gpu_telemetry_logger_nvidia.ps1` (source `E:\SSDShared\Lab\bin\`), shipped by `fleet_deploy` to `C:\ProgramData\HAI`
  on BigHonker and Softserve since 2026-11-18 (its `FLEET_ELSEWHERE` row) and run from there by the task. The root: the
  environment, else the machine variable, else `FOUNDRY_ROOT` in `C:\ProgramData\HAI\lab.env`; the sensor library from
  `FOUNDRY_LHM_DLL` in the box's `etc\lab.env` (placed: `F:\tools\LHM\…` on BigHonker, `E:\tools\LHM\…` on Softserve),
  else `LHM\` beside itself. Register or re-point the task with the installer beside it, run ON the box:
  `ssh bighonker "powershell -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\HAI\install_gpu_telemetry_logger.ps1"`; installer
  `install_gpu_telemetry_logger.ps1` (same restart policy as `fix_health_daemon_task.ps1`: boot trigger + a
  PAST-DATED time trigger carrying a 15-min repetition, `IgnoreNew`, 3 restarts, no time limit, SYSTEM).
- Task: **"GPU Telemetry Logger"** on BigHonker. Heartbeat: `F:\foundry\var\state\telemetry\gpu_telemetry.heartbeat`
  (liveness = a fresh heartbeat FILE, not a process match). **Live decoy 2026-09-28:** task stopped 21:05:43, the
  15-min repetition brought it back 21:15:27 unaided (heartbeat moved) — the restart policy is proven, not just read.
- Output: **`<foundry root>\var\state\telemetry\gpu_telemetry-YYYYMMDD.csv`** (daily, ~20 MB/day; no retention policy yet):
  `ts,run,core_mhz,mem_mhz,power_w,power_limit_pct,board_power_pct,temp_c,hotspot_c,mem_junction_c,fan1_rpm,fan2_rpm,fan1_pct,fan2_pct,vcore_v,gpu_load,mem_ctrl_load,bus_load,vram_used_mb,shared_spill_mb,pcie_rx_bps,pcie_tx_bps,pstate,throttle_reasons,enc_util,dec_util,smi`
  **v4 (2026-10-05, 49 columns)** appends:
  `mb_fan1..7_rpm, mb_fan1..7_pct` — the Super I/O case-fan speeds and the % FanControl commands, read through
  LHM's motherboard group; `mb_vcore_v, mb_v2/v5/v7/v14_raw` — raw voltages; and `ups_load_pct, ups_line_v,
  ups_status` — apcupsd NIS on `127.0.0.1:3551`, read every 5 s with a 1 s timeout.
  - The voltages are raw because this LHM build has no map for the PRIME Z390-P, so +12 V is an unscaled
    `Voltage #n`; a sag still shows as a change.
  - The UPS is whole-box power, INDEPENDENT of the GPU driver: on 2026-10-05 it caught BigHonker's draw falling
    25–38 s before NVML reported "GPU lost" (Home Assistant's ~60 s history, investigation §9). These columns put
    that on the card's own clock at 5 s resolution.
  - Why: [[BigHonker-3070-DPC-Watchdog-Investigation]] §9 and [[LAB-TOOL-GAPS]] #3.
  - ⚠ The first schema change of a day sets the earlier rows aside as `_prev-schema_<time>.csv`. Scripts that read
    "today's file" must read both.
- First load reading, BigHonker 2026-09-28 22:20 (9B thinking-on, 1500 MHz lock): core 55 °C, hotspot ~72.5 °C (gap
  ~17.5 °C, idle ~11.5), fans 55 % / ~1,070 rpm, 134 W (47 % of limit).
- `run` is STATED (`CURRENT_RUN.json` `name`), never inferred. `pstate`/`throttle_reasons` come from
  `nvidia-smi --query-gpu=pstate,clocks_event_reasons.active` with a hard 5 s timeout; `smi` = `ok` / `timeout` /
  `error`, so a wedged or lost card is written as a failed read, never as a value.
- Not exposed on this card: memory-junction temperature (GDDR6). Hotspot IS (idle 2026-09-28: core 42 °C, hotspot
  ~53.5 °C — a core-to-hotspot gap that widens under load is the thermal-contact signature to look for).

```powershell
Get-Content 'F:\foundry\var\state\telemetry\gpu_telemetry.heartbeat'
Get-Content (Get-ChildItem 'F:\foundry\var\state\telemetry\gpu_telemetry-*.csv' | Sort-Object LastWriteTime | Select-Object -Last 1).FullName -Tail 5
```
