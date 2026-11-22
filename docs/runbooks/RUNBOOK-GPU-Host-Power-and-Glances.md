# RUNBOOK — GPU Host Power Standard & Glances



### Glances (System Stats API)

Provides CPU, RAM, and GPU stats to Homepage. Runs as a Windows scheduled task at boot.

- Install location: Python 3.12 at `C:\Users\<user>\AppData\Local\Programs\Python\Python312\`
- Packages: `glances[web]`, `nvidia-ml-py` (for RTX 3070 Ti GPU monitoring)
- Launcher: `C:\ProgramData\Glances\run-glances.bat`
- Task: `Glances Web Server` in Task Scheduler (runs as Administrator at startup, highest privilege)
- Port: 61208 — firewall rule "Glances Web API" allows inbound TCP
- API base: `http://<softserve-address>:61208/api/4/`
- Flags: `-w -p 61208 -B 0.0.0.0 --disable-process` (`--disable-process` keeps `/api/4/all` small enough for Homepage)
- Homepage uses `customapi` widget pointing at `/api/4/all` — field paths: `cpu.total`, `mem.percent`, `gpu.0.proc`, `gpu.0.temperature`

To restart manually (elevated PowerShell):
```powershell
Start-ScheduledTask -TaskName "Glances Web Server"
```

### Mothership power management + watchdogs

Mothership idles down when not in use and self-recovers, so unattended overnight runs are safe.
Pieces (all SYSTEM scheduled tasks on Mothership; scripts in `C:\ProgramData\HAI\`):
- **Sleep:** `STANDBYIDLE` = **0 (never)** — the GPU-host FLEET STANDARD below governs (read 0 on 2026-10-22); the
  older "sleep after 45 min" policy no longer applies and is in `Archive\current-projects-history-20260821\2026-06.md`.
  S3 only (hibernate blocked by Guarded Host/Hyper-V). WoL wakes it (orchestrator
  auto-WoLs on heavy requests; direct magic-packet fallback in comfy_client for mirror-side
  scripts).
- **⚠ System UNATTENDED sleep timeout — THE ONE THAT DEFEATS KEEP-AWAKE.** `SUB_SLEEP /
  UNATTENDSLEEP` is a *separate, hidden* timer from `standby-timeout` ("Sleep after"). It governs
  re-sleep after any **unattended wake** (WoL, wake timer, network) — i.e. EVERY
  orchestrator/pipeline use, since those all WoL the box — and **`ES_SYSTEM_REQUIRED` (what
  keep-awake.ps1 asserts) does NOT survive it**: after a WoL wake Windows treats the machine as
  unattended and sleeps it when UNATTENDSLEEP expires regardless of any keep-awake assertion. The
  Windows default (AC 30 min, DC 2 min) silently overrides both the 45-min policy and keep-awake.
  **Set on BOTH Mothership and BigHonker:** `powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP
  UNATTENDSLEEP 0` (+`setdcvalueindex` +`setactive`) → **0 = never**, so keep-awake governs after
  WoL wakes and the 45-min idle timer is the sole sleep trigger. Verify:
  `powercfg /q SCHEME_CURRENT SUB_SLEEP UNATTENDSLEEP` → both indices `0x00000000`.
  **When a WoL-woken box sleeps "too early" despite keep-awake, it is ALWAYS the unattended
  timeout — check UNATTENDSLEEP first; never band-aid with self-heal/re-WoL.**
  **↔ CROSS-CUT for new services:** `UNATTENDSLEEP=0` is machine-level and covers all workloads;
  `keep-awake.ps1` busy-detection is PER-SERVICE (ollama-resident / ComfyUI-queue / self-eng /
  SSH). Any NEW long-running Mothership process keeps the box awake via the **GENERAL
  `keep-awake.hold` file (condition #0)**: the job writes `C:\ProgramData\HAI\keep-awake.hold`
  with an ISO-8601 UTC expiry at start — no per-service code change (a dedicated port/PID poll
  remains an option for always-on services). Without it the 45-min idle timer can sleep the box
  mid-job. Checklist for a new Mothership service: (1) confirm UNATTENDSLEEP=0 on that box;
  (2) add a keep-awake trigger; (3) verify with a >45-min idle-during-work test.
  **BigHonker is also on 45-min `standby-timeout-ac`/`-dc`.** Its keep-awake is Ollama-residency +
  ssh-session based (no ComfyUI). **SSH: `ssh bighonker` → `<user>@<bighonker-address>` with
  `~/.ssh/bighonker_ed25519` (NOT the mothership key).**
- **`HAI Keep-Awake`** (`keep-awake.ps1`, boot task): asserts `ES_SYSTEM_REQUIRED` while **(Ollama model resident)
  OR (orchestrator `/self-eng/busy`) OR (an active inbound SSH session)** — so Windows' INPUT-only idle timer
  can't sleep it mid-27B-run, mid-overnight-job, or mid-headless-session. Releases when idle → normal 30-min
  sleep. (Enhanced 2026-06-29 from model-only; log `keep-awake.log`.)
  - **Hold-file — condition #0 (the GENERAL keep-awake):** for any long non-instrumented Mothership
    job (model downloads, a training run, video gen), write `C:\ProgramData\HAI\keep-awake.hold` containing an
    ISO-8601 UTC expiry — asserted until then (12h cap from mtime if empty/unparseable; auto-cleaned when expired). Set
    with `(Get-Date).ToUniversalTime().AddHours(8).ToString('o') | Set-Content C:\ProgramData\HAI\keep-awake.hold`.
    The script also now counts a **ComfyUI job running/queued (+45-min tail)**. Backup of the pre-hold script:
    `keep-awake.ps1.bak-20260714-2210`. Verify a hold is live: `powercfg /requests` shows a SYSTEM process hold +
    `keep-awake.log` logs `busy (hold-file)`.
- **`HAI Ollama Watchdog`** (`ollama-watchdog.ps1`, boot task, every 90s, Mothership only): restarts ollama ONLY ON
  EVIDENCE OF DEATH — no `ollama` process, or nothing accepting on port 11434 — then kills stray ollama procs and
  re-triggers the `Ollama Serve` task. **A slow `/api/version` with the process alive and the port open is BUSY and
  is never killed**, and a serve process under 5 min old is left alone while it loads (the 3.8 takes ~45 s). ⚠ Until
  2026-10-08 it restarted on a 6 s `/api/version` timeout: at 15:14 that killed a busy, healthy ollama mid-A1
  (the 3.8's long sgf-parsing turn), then killed the recovering one at 15:18 after a 25 s wait, orphaning a
  llama-server the GPU police arrested. **Mirror `E:\SSDShared\mothership-build\ollama-watchdog.ps1`, deploy with
  `fleet_deploy.py --no-restart --only mothership ollama-watchdog.ps1` (ELSEWHERE → `C:\ProgramData\HAI`), then
  restart the TASK (`schtasks /end` + `/run "HAI Ollama Watchdog"`) and confirm ONE instance started after the file.**
  Forced-test 2026-06-29: killed Ollama → recovered ~29s. Discord-notifies. Log `ollama-watchdog.log`.
  **The Discord webhook is not in the script (since 2026-11-15):** it is one line in
  `C:\ProgramData\HAI\ollama-watchdog.webhook` (readable by SYSTEM, Administrators and <user> only), read at each
  notify, so replacing the file rotates it with no restart. The start line in the log says `(webhook: present)` or
  `MISSING - notifications will be skipped`; a missing file never stops the watchdog, it only logs the skipped notice.
- **`HAI TextInput Watchdog`** (`textinput-watchdog.ps1`, boot task, every 60s): kills a RUNAWAY `TextInputHost`.
  Why it matters: `TextInputHost` (the
  Windows input host) can get stuck **pegging a full core**, and being the *input* host it then generates **phantom
  input that immediately wakes the box from S3** (~2-3s after every sleep = a wake storm). All OS power telemetry
  looked fine (timers 30m, nothing in `powercfg /requests`, no sleep-veto event) precisely because the wake came
  from spurious *input*, not a power policy. Killing it (restarts clean+idle) fixed BOTH the fans and the
  can't-stay-asleep. The watchdog debounces (runaway across two 3s samples) so legitimate input use is never
  killed. Discord-notifies. Log `textinput-watchdog.log`.
- **Sleep-crash: the `0x9F` is NOT fixed — it recurred 2026-10-22.** (The 2026-06-29 "likely fixed" rested on clean
  cycles after the lighting daemons were disabled; `0x9F` also hit 2026-10-10 07:54.) ~08:36–09:10 CT, with the lane
  idle and no model resident (an ollama tree rollback had just emptied it), a device held a power request: `0x9F`
  arg1 = 3 (a device blocked a power IRP). **NAMED by kd (minidump `102226-17187-01`, `!devstack`/`!irp`): the RX
  7900 XTX itself** — `PCI\VEN_1002&DEV_744C`, driver `amduw23g` (`amdkmdag.sys`), blocked an `IRP_MJ_POWER /
  IRP_MN_SET_POWER` to **D0** (args `0 1 1` = device power, D0) with `dxgkrnl`'s `IRP_MN_WAIT_WAKE` pending under it:
  the card was being powered back UP from an idle device state and the driver never finished. kd's default bucket
  `0x9F_3_IMAGE_pci.sys` names only the bus driver — read the devstack. NOT system sleep: `STANDBYIDLE` = 0 (never,
  the fleet standard below), S3 never entered. The box-level standard (High performance, ASPM off, never sleep) was
  all in force. **ROOT CAUSE (full kernel dump `MEMORY-20261022-0915.DMP`, kd on BigHonker): a VSS snapshot
  deadlocked in ACRONIS's file filter, and the GPU was the bystander that tripped the bugcheck.** `!poaction`: no
  system power action. The AMD D0 handler (`amdkmdag+0x4f5da7` → `IoGetDriverDirectory` → `NtOpenFile`) was waiting
  on the volume's NTFS VCB resource (`Ntfs!NtfsAcquireSharedVcb`), held EXCLUSIVELY by a `VSSVC.exe` thread in
  `NtfsFlushVolume` (the snapshot freeze) that was RUNNING inside `file_protector.sys` + `fltsrv.sys` (Acronis Active
  Protection / Acronis storage filter): 2:07 of kernel time, 12,312 contentions, 71 waiters. Every file open on that
  volume queued behind it — sshd's per-login process, scheduled tasks, the patrol, remote desktop — and the GPU
  power-up during the driver swap needed one. **Trigger = an installer's restore-point snapshot:** today `msiexec`
  (the AMD Software Installer 26.10.1 — VSS 8231 at 08:48:48 and 09:09:08, freeze-too-long 12349 at 09:11), on
  2026-10-10 Windows Update (`wuauserv`, VSS 07:46/07:55, 0x9F 07:54); 2026-10-16 06:38 was the same freeze warning
  without a crash. Display-off and commit exhaustion were checked and refuted (the card reads D0 idle; its S0 mapping
  is D0; no 2004 events). The lighting stack is not implicated. **While stuck it looks alive and is not**: running listeners answer (ollama :11434, SMB, the RDP port) but
  nothing new can start — sshd accepts TCP and never sends its banner, scheduled tasks (the deck reporter, a UPS
  service start) time out, the patrol logs `could not enumerate processes`, remote desktop says access denied. It
  bugchecked ~09:10 and auto-rebooted; ollama returned via auto-login, the foundry runner via its 15-min repeat
  (`LogonType Interactive` skips the boot trigger). Dump: `D:\crash-dumps\MEMORY-20261022-0915.DMP` + minidump
  `C:\Windows\Minidump\102226-17187-01.dmp` — the driver name is owed (`kd` on BigHonker, § "Analyse a dump").
- **Recurred 2026-10-24 15:34 CT — and the TRIGGER is named: the lab's own DEPLOY GATE (fixed + proven the same night).** Idle box (card 7 W, nothing queued), same "looks alive and is not" signature, no bugcheck, no dump; Kernel-Power 41 / 6008 "previous shutdown at 3:34:10 PM unexpected"; reset by the owner 21:52. Evidence (read-only collectors, `scratchpad\mothership_freeze_evidence.py` / `ap_spike.py` / `logon_sources.py`): Acronis Active Protection logged until **15:34:18, mid-line**; Mothership started ~40–48 processes/min (the lab's monitors) until **15:33, then 215 in that minute** — 64 sshd, 66 python, 37 cmd via inbound ssh, plus Windows idle maintenance's .NET compile (39 `mscorsvw` from `ngen`) — every start inspected by Acronis's filter; the Security log shows **88 logons in 20:33Z, 58 of them `<user>` via sshd** (1 in the eight minutes before). **The orchestrator deploy gate had started at 15:33:23** — and the test suite runs `docker compose run orchestrator`, i.e. with the live Mothership key and address: a full run makes ~44–58 real `ssh <user>@<mothership-address> python.exe -` sessions (`mothership.dataset_registry` / `dataset_jobs`, reached from ~20 tests through `tools.py` building HAI's tool list; listed by `find_ssh_callers.py`). **2026-10-23 matches:** gate 3089 ran ~14:27 and the freeze was observed ~14:28 (its 6008 "1:55:54 PM" stamp is NOT the hang time — the log has entries to 14:28:02). Not every gate froze it (e.g. 10-24 09:34): the burst plus Windows maintenance plus the Acronis filter is the shape; the exact lock is unproven without a dump. **Fixed (orchestrator gate 3149, 2026-10-25 03:2xZ):** `run-suite.sh` gives the gate `PC1_SSH_HOST=gate-no-ssh.invalid` + `PC1_SSH_KEY=/dev/null` (container boundary; named `MOTHERSHIP_SSH_HOST` / `MOTHERSHIP_SSH_KEY` until 2026-11-16, and `tests/run_suite.py` sets the same), and `tests/test_zzz_no_live_model_calls.py` blocks + records any ssh/scp to a real box and, in the gate, fails the run on one. **Proven: a redirected gate run produced 0 ssh logons on Mothership** (vs 58). Also fixed: HAI's tool list asked an unanswering Mothership over ssh on every turn (8 s each) - now once per quiet window. **Remote power path, temporary (the owner, 2026-10-25):** Mothership now sits on the Govee H5086 plug HA calls `switch.subwoofer_powerswitch` (GoveeLife cloud integration, on/off only — needs the internet; HomeAssistant-Energy). A power cut is a HARD cut of a box that may be mid-write: operator action only, never automatic, and whether Mothership powers back on by itself after the cut (BIOS "restore on AC power loss" / ErP for WoL) is NOT yet verified. **Still the owner's (not done):** capture a dump on the next hang (`CrashOnCtrlScroll` registry key: Right-Ctrl + Scroll Lock twice forces a bugcheck with a full dump - a system setting change), Acronis Active Protection exclusions for the lab's own processes. Other ssh bursts still exist: `fleet_deploy` and diagnostics - keep them few and serial.
- **Recurred 2026-10-28 ~06:28 CT — an unplanned Windows Update again; NOT a deploy burst, NOT an installer snapshot.** Idle box, same "looks alive and is not" signature (deck silent from ~06:28, ssh/ollama/ComfyUI unanswering), no bugcheck, no dump (Kernel-Power 41 `BugcheckCode 0`, 6008 "previous shutdown at 6:15:06 AM" — not the hang time, the System log has entries to 06:28:08); the owner reset it by hand ~06:58. **The last events before the log went silent were all Windows Update:** 06:21 Defender intelligence KB2267602; 06:23–06:24 Defender **platform** update KB4052623 (4.18.26100.4), which **unloaded and reloaded Defender's file-system filter `WdFilter`** (FilterManager 1/6 at 06:24:18/28) and installed a new kernel service (`KslD`, 06:24:31); 06:27:55–06:28:08 Windows Update began more downloads — then nothing. **Ruled out, with a positive control:** the OpenSSH/Operational log (enabled, kept back to 10-26, 382 of my logons recorded the same evening) shows **zero ssh sessions 06:10–06:30**, so Codex's `fleet_deploy` found the box already stuck rather than causing it; no VSS / System Restore / MsiInstaller events in the window, so it is not the 10-22 restore-point path. The Security log could not help: its oldest event is the 06:58 boot (rolled over). Best-supported reading: a filter-driver swap on a volume Acronis's filters also sit on, set off by an update nobody scheduled — the same family as 10-10 and 10-22, the 10-23 BITS flip, and the reason for PROJECT-BACKLOG #38 (lab update system). Unproven without a dump (the `CrashOnCtrlScroll` key is still the owner's). After the reboot Windows Update kept installing (Store `WindowsAppRuntime` packages at 06:59, 16:43 and 16:53). Collectors: `hang_events*.py` / `hang_logons*.py` in the 2026-10-28 main session scratchpad.
- **Recurred 2026-11-05 00:41 CT (16 min, released on its own), started by the lab itself: a 2.2 GB Flash-Next session file written to C:** (`C:\ProgramData\HAI\slots`, Strata's save, first ever run of it on this box). Telemetry stopped 00:41:09, the file's last write 00:41:19, the save's HTTP call never returned in its 600 s, SCM 7011 on the UPS agent 00:44:45, telemetry back 00:57:12. An engine kill 15 minutes earlier caused no gap, so this one reads as the write, not a teardown (inferred from timing; no dump). **The same file on F: (`F:\HAI\slots`): 2–20 s, no gap across two runs and six engine starts.** Rule: **don't put a multi-GB file on Mothership's C:.** ⚠ Ollama's chat saves there still go to `C:\ProgramData\HAI\slots` and can reach this size on a long conversation; moving them is an open decision (an ollama restart on the owner's PC). [[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]].
- **Recurred 2026-11-03 22:08 CT (unnoticed, 13 min) and 2026-11-04 13:32 CT (eight self-releasing waves to 14:02) — the same stall, this time letting go on its own.** Signature identical from outside (ollama :11434 accepts TCP, HTTP silent 40 s+; sshd banner timeouts; the GPU telemetry logger's rows stop; SCM 7011 on the UPS agent; ESENT **HUNGIO** on a C: write). **Positive control that pins it to the system volume / new-process path, not the GPU or USB:** the UPS power logger (long-running Python, F: writes every 5 s, open HID handle) wrote 540/540 rows through the whole 11-04 window. **Canary with history:** PowerPanel's `4100 SvcStop Success` (its Monitor restarting a hung agent) — first on record 10-20, 1–12 per day since, each at the end of a telemetry gap → these stalls happen most days; nothing was asking the box. **11-03's trigger was the documented one** (Store/Windows Update → VSS snapshot 22:07:52 → gap 22:08:10–22:21:05, VSS 12349 freeze-too-long, three installs failed 0x80073D02). **11-04's first wave was the Flash-Next (Strata) engine's teardown** (24 GB VRAM + the mmapped shards released 13:32:50→13:40:34) with no snapshot and no update in the window — timing exact, mechanism unproven (the dump key is still owed). HAI read it as "Mothership did not wake" three times and sent 15 ssh logons at it (wake-path fixes owed, orchestrator). Evidence + timeline: `Claude Home\research\ltt-jarvis-prompts\runs\mothership-hang-20261104\FINDINGS.md`; journal 2026-11-04-claude-ltt-pitch.
- **Recurred 2026-10-23 ~14:28 CT, and this time it did NOT bugcheck or reboot itself** — 50 min frozen until the owner
  reset it by hand (15:18:25; Kernel-Power 41, unclean; every volume `healthy` on boot). Same signature from outside:
  ports 22 / 11434 / 445 / 3389 / 5985 accept TCP, ollama's HTTP never answers, sshd never sends its banner (a 150 s
  wait timed out), the deck went SILENT then DOWN, a lead turn failed after 813 s. **The last events before the log
  itself went silent: the Background Intelligent Transfer Service (Windows Update's transfer service) flipped to auto
  start at 14:26:01 and back at 14:28:02** — then nothing until the reset (even EventLog could not write). Consistent
  with the 10-10 Windows Update trigger; not proven (no dump, no bugcheck). It began during a chat turn's cold 27B
  window load; the load is not ruled out as a factor. **Recovery is manual only** — no remote restart exists
  (PROJECT-BACKLOG "Mothership FROZE SOLID", 10-05: workgroup `shutdown /m` is denied; the CyberPower GX1500U has
  no network management — USB HID + one dry contact to the PC, RJ45 jacks surge-only, checked 2026-10-23 against the
  vendor's spec sheet and manual). The foundry runner returns on its own 15-min repeat after the reboot.

## GPU host power configuration — FLEET STANDARD

**Every box that runs GPU compute gets the same two settings. This is not per-machine taste.**

| Setting | Required value | Why |
|---|---|---|
| Power scheme | **High performance** | Balanced lets the platform manage links/states aggressively |
| PCI Express → Link State Power Management (ASPM) | **Off**, AC *and* DC | Link-state transitions wedge the GPU driver coming out of compute |
| Sleep → standby idle (`STANDBYIDLE`) | **0 (never)** | normal user-present idle timer |
| **Unattended sleep (`UNATTENDSLEEP`)** | **0 (never), on EVERY scheme** | headless work does not count as user presence — see below |

### ⚠ `UNATTENDSLEEP` and the per-scheme trap

the owner: *"headless interactions count towards a different time than keyboard and mouse."* Exactly right.
Windows does **not** treat an ssh/RDP-less/network session as user presence, so a box being hammered
over ssh falls to the **UNATTENDED** idle timer, not `STANDBYIDLE`. A machine can be mid-job and look
idle to the OS. That is the documented "sleeps too early" cause.

⚠ **Power settings are PER-SCHEME, and that is how a real fix silently stops applying.** Setting
`UNATTENDSLEEP=0` on **Balanced** while the box RUNS **High performance** leaves the stock `0x78`
= **120 seconds** in force — the fix reads as applied and protects nothing, and any future scheme
switch can revert a fix that was set only on the active scheme.

**Therefore: set it on EVERY scheme, not the active one.** Then a future scheme switch cannot revert it.

```
for %G in (381b4222-f694-41f0-9685-ff5bb260df2e 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
           a1841308-3541-4fab-bc81-f71556f20b4a e9a42b02-d5df-448d-aa00-03f14749eb61) do (
  powercfg /setacvalueindex %G SUB_SLEEP UNATTENDSLEEP 0
  powercfg /setdcvalueindex %G SUB_SLEEP UNATTENDSLEEP 0
  powercfg /setacvalueindex %G SUB_SLEEP STANDBYIDLE 0 )
powercfg /setactive SCHEME_CURRENT
```

Verify with `powercfg /query <guid> SUB_SLEEP UNATTENDSLEEP` **per scheme** — querying only the active
one is what hid this. `powercfg /requests` shows what is currently holding the box awake; a
`[DRIVER] Legacy Kernel Caller` hold is luck, not configuration, and must not be relied on.

**Status:** Mothership ✅ all 4 schemes, BigHonker ✅ all 4 schemes (both 2026-07-26).

Apply (elevated; over Windows OpenSSH an admin gets a full token, so ssh is fine):

```
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
powercfg /setacvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM 0
powercfg /setactive SCHEME_CURRENT
```

Verify by RE-READING (`powercfg /query <guid> SUB_PCIEXPRESS ASPM`) — a failed cmdlet still lets the
success message print.

⚠ **Why this is a FLEET standard written here, not a per-machine registry setting: a fix that
exists only on one box is not a fix.** A GPU box left on Balanced with ASPM Moderate(AC)/Max(DC)
takes `0x133` DPC_WATCHDOG bugcheck loops and hard hangs (Event 41, no dump) under compute, up to
needing a physical power cycle. Carrying a host-level fix to every peer box AND into the library
is part of the fix itself.

**The diagnostic signature**, so this is recognisable next time:
* bugcheck **`0x133` DPC_WATCHDOG_VIOLATION**, and/or Event 41 with **no** 1001 (hard hang, no dump);
* **no WHEA events** — this is NOT a failing part, do not RMA on this evidence;
* crash lands **shortly after inference STOPS, not during it** — measured: 1m42s / 2m00s / 2m08s after
  the last `/api/chat` while only `/api/ps` polls ran, and once **one second** after the last request
  (the `nvlddmkm` Event-14 burst at 14:52:56 followed a chat that ended 14:52:55). It is the
  transition OUT of compute, not the compute;
* a driver update does **not** fix it (591.86 → 610.62 only changed a recoverable bugcheck into a
  hard hang).

⚠ **`TdrDelay` is NOT part of this fix.** It is unset on Mothership, which is stable — so raising it
is speculation, not a carried-over remedy. Left in reserve deliberately; adding it alongside the
proven change would make the outcome unattributable.

⚠ **Do NOT reach for "prefer maximum performance" / pinning P0 first.** It stops the idle drop to P8
but burns ~25-45 W continuously on machines that live in rooms people sit in, and Mothership has never
needed it. ASPM Off costs nothing measurable — BigHonker idled at 15.96 W before and after.

**Status:** Mothership ✅, BigHonker ✅ (both verified 2026-07-26). Softserve — check before
assuming; it is the Plex/VM host and a power-plan change there is the owner's call, not a silent
cross-cut.

## Crash dumps — KEEP EVERY DUMP (`MEMORY.DMP` discarded, `Overwrite=0`, "no minidump so not a bugcheck")

**Standard (the owner, 2026-10-10: "obviously I want to keep all my dumps somewhere, it's what I do for everything else"):
a bugcheck's dump is evidence and is never overwritten or lost.** With `CrashControl\Overwrite=0` and a dump
already in the path, Windows discards every later dump (volmgr 162 at boot, nothing on disk); the `BugcheckCode`
still lives in the Kernel-Power **41** event whether or not a dump survived.

| box | `CrashDumpEnabled` | `Overwrite` | kept where | mechanism |
|---|---|---|---|---|
| Mothership | 2 (kernel dump → `C:\Windows\MEMORY.DMP`) | **1** (set 2026-10-10) | `D:\crash-dumps\MEMORY-<yyyyMMdd-HHmm>.DMP` (named by the dump's own write time) + `D:\crash-dumps\minidump\` | scheduled task **`Keep Crash Dumps`** (ONSTART +1 min, SYSTEM, HIGHEST) runs `D:\crash-dumps\keep_dumps.ps1`: moves `MEMORY.DMP` into the archive, copies new minidumps, logs to `D:\crash-dumps\keep_dumps.log`. Proven live 10-10 09:00 (moved the 08-28 dump, log line written). |
| BigHonker | 3 (minidumps only, one file each) | 1 | `C:\Windows\Minidump\` (never overwritten) | none needed; **kd lives here** |
| Softserve | 3 (minidumps only) | 1 | `C:\Windows\Minidump\` | none needed |

**Read the bugcheck code without a dump:** `Get-WinEvent -FilterHashtable @{LogName='System'; Id=41}` → `[xml]$_.ToXml()`
→ `EventData` `BugcheckCode` (decimal: 159 = 0x9F DRIVER_POWER_STATE_FAILURE, 59 = 0x3B SYSTEM_SERVICE_EXCEPTION,
0 = no bugcheck: freeze/power/reset) and `PowerButtonTimestamp` (non-zero = the power button was held). EventLog
**6008**'s "previous shutdown at HH:MM" is the log's periodic alive-stamp and lags the real stop by minutes — take the
last line of a workload's own log for the time.

**Analyse a dump:** `kd` is installed only on BigHonker (`C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\kd.exe`,
see [[BigHonker-3070-DPC-Watchdog-Investigation]] §2 for the install story). Never analyse on the box that is running a
measurement (a 6 GB `!analyze -v` with symbol download is CPU + disk + network). Copy the dump over `scp` via
Softserve (`scp mothership:D:/crash-dumps/X.DMP <Softserve>` then `scp <Softserve> bighonker:I:/crash-dumps/`; shares
are invisible to an ssh network logon, scp is not), then run `I:\crash-dumps\analyze_dump.cmd <dump> <log>` as a
one-shot task **`schtasks /create … /sc once /st 00:00 /sd 01/01/2020 /ru SYSTEM /rl HIGHEST`** + `/run` (symbols cache
in `I:\symbols`). ⚠ Without `/ru SYSTEM`, a task created over ssh belongs to the ssh user with an interactive-only
logon: `/run` prints "Attempted to run" and nothing ever starts (`LastTaskResult 267011` = has not run; 2026-10-22).
Confirm `Get-Process kd` before waiting on the log. The log carries `!analyze -v`, `.bugcheck` and
the `!irp` of the blocked request (0x9F arg 4).

## GPU driver versions in force (read before comparing any crash, speed or residency number across dates)

| box | driver | since | how it was installed |
|---|---|---|---|
| BigHonker RTX 3070 | **GeForce 617.42 WHQL** (32.0.16.1742) | 2026-10-10 09:46 | NVIDIA package, Authenticode-checked, `-s -noreboot` as a one-shot SYSTEM task (`nvidia_driver_install.ps1`, kept beside the package in `I:\downloads\nvidia-617.42\`); `BH GPU Clock Lock` re-run after, read back 1500 MHz; no reboot needed |
| Softserve RTX 3070 Ti | GeForce 616.xx (32.0.16.1692, 2026-09-03) | — | not updated 2026-10-10 |
| Mothership RX 7900 XTX | **32.0.32015.12012 (OpenGL 26.10.2.261022; package `u0205499`, dated 2026-10-27/28)** | 2026-10-31 16:15 | AMD's own installer (`C:\AMD\AMDSoftwareInstaller_DIME_Chipset`). The card failed to start (`CM_PROB_FAILED_POST_START`), then fell off the bus until the owner's cold power-cycle at 18:29 (2026-10-31-claude). The initiator is not established: it matches neither AMD task's schedule ([[Lab-Update-Inventory-38]]). Before: **26.10.1 (32.0.32015.7029)** from 2026-10-22 09:30. That earlier install's first attempt at 08:48 hung the box into the 0x9F at ~09:10 (§ sleep-crash) and completed after the reboot. `AMDAutoUpdate` last ran 10-22 09:20:11, which is consistent with it starting that install (#38 inventory). Before that: 32.0.31041.1004 (2026-08-16). Numbers from before/after 10-22 09:30 and 10-31 16:15 are on different drivers |

**Mothership's AMD auto-update is OFF since 2026-11-03** (the owner's go): `AMD Install Manager - Check For Updates`,
`- Install Updates` (an idle-time SYSTEM install the daily check re-armed every day; the likely 10-31 initiator) and
`AMDAutoUpdate` are disabled. A new AMD driver arrives only through #38's held lane. The nightly notice reports if
the tasks come back ([[Lab-Update-Inventory-38]]).

**Since 2026-11-04 the NVIDIA rows above are kept by #38's driver stage** (02:30: BigHonker first, Softserve the night
after BigHonker passes; a WHQL release 7+ days old; the rollback package kept; `nvidia_driver_install.ps1` as before).
Update this table from the stage records (`State\lab-updates\stage-<box>-*.json`, class `driver`) — the row's "since" is
the record's date. [[Lab-Update-Inventory-38]].

A driver update on BigHonker is a VARIABLE in the open 0x133 investigation ([[BigHonker-3070-DPC-Watchdog-Investigation]] §9): stamp the doc when it changes.

## GPU host Defender exclusions for model and engine folders — the practice, and how to read it back

the owner, 2026-10-01: *"Exclude the defender per our usual doctrine for models."* The practice (it had never been written
down — the only trace was three paths on Mothership): **every folder that holds model weights the box loads, and the
engine folders that run them, gets a Defender folder exclusion.** Real-time protection stays ON everywhere; this is
folder scoping, not disabling. Why it matters for inference: a model load streams the whole file through the filter
driver (Flash-Next: 82 GB per load, 108–143 s), and a file-backed tensor (the n-gram table, `CPU_Mapped`) pages in
from disk on demand through it too. Anonymous memory and the pagefile are not scanned, so generation speed is mostly
unaffected; load time and cold-start are.

Read back (any box, read-only): `(Get-MpPreference).ExclusionPath` and `(Get-MpComputerStatus).RealTimeProtectionEnabled`.
Apply (elevated — over Windows OpenSSH an admin gets a full token): `Add-MpPreference -ExclusionPath '<folder>'`, then
read back and confirm every path is listed. Script: `Claude Home` scratchpad `defender_exclude.py` (2026-10-01).

**Status — FLEET STANDARD applied 2026-10-01 00:40 (the owner: *"cross cut city is calling our names"*), every path read
back from the box (`OLLAMA_MODELS` machine env, the running `ollama.exe`'s folder, the foundry root), real-time ON:**
- **Mothership:** `F:\ai-toolkit`, `F:\ComfyModels`, `F:\flux2-train` (pre-existing) + `E:\Ollama\Models`,
  `C:\Users\<user>\AppData\Local\AMD\AI_Bundle`, `F:\foundry` (00:40) + `F:\Ollama-FlashNext-q2k`, `…-q2mix`,
  `…-iq2xxs`, `F:\Models`, `C:\ProgramData\HAI\tools` (queued behind the running measurement order so its arms
  stay comparable; `defender_exclude.py`) **+ `F:\krea2-models`, `D:\krea2-train`, `D:\ai-toolkit-krea2` (applied
  2026-11-04 14:43 on the owner's go, read back: 14 paths, real-time ON).**
  ⚠ **Acronis on Mothership (read 2026-11-04): there is NO backup task** (`TrueImageHome\Scripts` is empty); what runs
  is **Active Protection** (anti-ransomware; services `AcronisActiveProtectionService`, `aakore`, `afcdpsrv`,
  `syncagentsrv`) and its antimalware policy. Its log (`C:\ProgramData\Acronis\ActiveProtection\Logs`, ~2 MB per
  rotation, 520 rotations in four days) records every file create/replace by the lab's own processes (e.g.
  `F:\ai-toolkit\venv\python.exe` writing `F:\foundry\var\state\...`) with per-process heuristics — it watches the
  lab's working drives, not just C:. Its config and `user_exclusion_list.xml` are **encrypted on disk**, so exclusions
  can only be set in the Acronis UI at the console. **the owner, 2026-11-04: "Obviously I don't want my own lab to be
  tripping my stuff" — exclude the lab.** The list to enter (Acronis → Protection → Active Protection → Manage
  exclusions; folders AND programs; read back by watching the log quiet down):
  - Folders: every Defender-excluded path above (`F:\foundry`, `F:\Models`, `F:\Ollama-FlashNext-*`, `E:\Ollama\Models`,
    `F:\ai-toolkit`, `F:\ComfyModels`, `F:\flux2-train`, `F:\krea2-models`, `D:\krea2-train`, `D:\ai-toolkit-krea2`,
    `C:\ProgramData\HAI\tools`, `C:\Users\<user>\AppData\Local\AMD\AI_Bundle`) plus the lab's state and tool roots the
    log shows it inspecting every few seconds: `C:\ProgramData\HAI` (whole), `F:\tools`, `F:\llama-swap`,
    `F:\foundry\workspace\flashnext`.
  - Programs: `C:\Users\<user>\AppData\Local\Python\bin\python.exe` and `…\Python\pythoncore-3.14-64\python.exe`
    (the reporters, stall recorder, UPS logger), `F:\ai-toolkit\venv\python.exe` (foundry, rescue daemon), `ollama.exe`
    and its `llama-server.exe` (`C:\Users\<user>\AppData\Local\Programs\Ollama`), `F:\llama-swap\v265\llama-swap.exe`,
    the Strata engine binary under `F:\foundry\workspace\flashnext`, `C:\Windows\System32\OpenSSH\sshd.exe`.
  - What it does NOT fix: the filter stays on the volume, so a restore-point snapshot can still freeze it — that is
    #38 (lab-controlled update cycle). It does remove the ~1,000 inspected events/min the lab's monitors generate
    (Acronis log, 2026-11-04 13:25–13:29), which is the standing load 10-24's "burst" sat on top of.
- **BigHonker:** `C:\Users\<user>\.ollama\models`, `C:\Users\<user>\AppData\Local\Programs\Ollama`, `F:\foundry`.
- **Softserve:** `C:\Users\<user>\.ollama\models`,
  `C:\Users\<user>\AppData\Local\Programs\Ollama-0.32.3` (version-named folder — re-add on an
  ollama upgrade), `E:\foundry`.
Script: `Claude Home` scratchpad `defender_exclude_fleet.py` (env `BOX` = bh | ss | ms-extra). A new model store,
engine folder or factory root on any box gets added here AND to this list in the same pass.

## Glances on Softserve — the recurring "always broken" (a 72h task-kill clock)

**What it is:** Softserve runs Glances (v4.5.4) as scheduled task **"Glances Web Server"** →
`C:\ProgramData\Glances\run-glances.bat` → `glances.exe -w -p 61208 -B 0.0.0.0 --disable-process`
(glances.exe lives in `C:\Users\<user>\AppData\Local\Programs\Python\Python312\Scripts\`).
Homepage widgets: Softserve = **api v4**, DO-Pihole (<droplet-address>) + MinuteMan (<minuteman-address>) = **api v3**,
`metric:` field required (`services.yaml`).

**ROOT CAUSE of the every-few-days breakage (not bad luck — a 72h clock):** the task had
`ExecutionTimeLimit = PT72H`, so Task Scheduler **force-killed Glances every 72 hours**
(`LastTaskResult 0x41306 = SCHED_S_TASK_TERMINATED`). Trigger was **At-Boot only** + `RestartCount=0` +
`StartWhenAvailable=False`, so once killed it stayed dead until the next reboot. Bonus fragility: the principal
used a **stored password**, which breaks on any password/domain change.

**The required task settings (no reboot needed):**
```powershell
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
       -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 99 -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Set-ScheduledTask -TaskName "Glances Web Server" `
  -Principal (New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest) -Settings $set
Start-ScheduledTask -TaskName "Glances Web Server"
```
= **unlimited runtime** (no 72h kill) + **restart-on-failure** (every 1m ×99) + **catch up a missed boot** +
**S4U logon** (no stored password → survives password/domain changes). Verified: listening on :61208, homepage
reaches `api/4` (200). Health check: `Get-ScheduledTaskInfo -TaskName "Glances Web Server"` (0x41301 = running is good);
`Get-NetTCPConnection -LocalPort 61208 -State Listen`. NOTE: Glances takes ~15-20s to bind after start.
