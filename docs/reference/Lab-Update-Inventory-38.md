# Lab update inventory (#38) — what every box has installed and which updates are waiting: Windows Update, drivers, winget, apt, vendor auto-updaters

**Project:** #38 Lab update system (PROJECT-BACKLOG § #38; the owner started it 2026-11-02, *"lab update stuff"*).
**This page is #38's home: the inventory, the nightly notice, the stagers per class, smart archiving, and the
state of each.**

## STATE 2026-11-04 (built in three days; the owner's "Automatic Plus": the lab installs, he is informed)

| nightly | class | state |
|---|---|---|
| 02:30 | GPU drivers — NVIDIA (BigHonker → Softserve) | **live**; dry-run proven; first real install = the next night the gates pass (621.01 is due on both) |
| — | GPU drivers — AMD (Mothership) | **report only** until the owner enters the Acronis exclusions at the console (the installer's snapshot deadlock) |
| 03:00 | notice (every box, every update, upstream engines incl. ollama's llama.cpp pin) | **live** since 11-03 |
| 03:20 | Windows Update (BigHonker → Softserve → Mothership canary) | **live**; proven on BigHonker; reboot path not yet exercised live |
| 03:40 | apps (winget allow-list + Store scan) + Plex (7-day rule, nobody streaming) | **live**; proven on Softserve + BigHonker; Plex's first guarded install = next qualifying release |
| 04:12 | Linux (Sundae reboot after a kernel; rigby apt + kernel try-once) | **live**; both proven (Sundae 11-04 unattended, rigby 11-03) |
| 04:30 | the morning report's "Lab updates" field | **live** |
| 05:30 | lab engine (llama.cpp at ollama's pin + lab patches + compat, all four factories) | **live** (`engine_pipeline.py`, task "HAI Lab Update Stage Engine"); detect proven live (0.40.14 → WAIT, 2 days old); **prepare proven live for b11963** (patches + compat applied, tree ready); build → canary → fleet → verify unproven live until 0.40.14 turns 7 days old (~11-10) |
| — | smart archiving | **a store + a tool** (`lab_archive.py`, `Z:\Archives\lab-updates\<component>\<stamp>-<version>\manifest.json`); every stage records what it replaces before replacing it; `list` / `show` / `prune` |

the owner's own items, outside the lab's reach: the Acronis Active Protection exclusions (console); `CrashOnCtrlScroll`;
the Steam web-view test; a tablet reseat at rigby (separate matter).

## The tool — `E:\SSDShared\Lab\bin\update_inventory.py` (READ-ONLY)

```bash
python E:\SSDShared\Lab\bin\update_inventory.py                    # every box, in parallel (~4 min)
python E:\SSDShared\Lab\bin\update_inventory.py mothership rigby   # just these
```

Each box's full answer goes to `Lab\bin\update-inventory\<box>-<stamp>.json` (one name, one generation). The printed
summary is a view of it. A box that cannot be asked prints **COULD NOT ASK**, never "up to date".

- **Windows** (Mothership, BigHonker, Softserve) — `update_inventory.ps1`, sent through `ssh <box> "python -"`:
  - the OS build and whether a reboot is pending;
  - the Windows Update policy keys (auto-update, driver exclusion, active hours, pause, Store auto-download);
  - **Windows Update's own pending search and its last 30 history entries** (its COM searcher: a search, never a
    download);
  - every non-Microsoft driver;
  - installed programs from the Uninstall registry keys (machine + loaded user hives; never `Win32_Product`, which
    triggers MSI repairs);
  - every vendor updater service and scheduled task;
  - Defender platform/engine/signature versions;
  - the running ollama/llama-server paths and API version;
  - `winget upgrade --disable-interactivity`. It never accepts a source agreement; BigHonker's winget source is
    broken, known since 2026-07-26.
- **Linux** (Sundae, rigby, mordecai) — `update_inventory.sh` via `bash -s`:
  - apt's **cached** lists (no `apt update`, so the lists' age is printed beside the count);
  - `reboot-required`;
  - the unattended-upgrade units;
  - NVIDIA driver, docker, snaps.
- **Not covered yet:** the Fellowship tablets, the MinuteMan Pi, the DigitalOcean Pi-hole, the router, and Sundae's
  container images (Watchtower already updates those at 4:00 with Discord notices).

## The nightly NOTICE — `update_notify.py` → the 4:30 morning report (since 2026-11-03)

Softserve task **"HAI Lab Update Notify"** runs daily at 03:00: Administrator, S4U logon (no stored password; local
paths + outbound ssh), `pythonw`. It runs the inventory on every box and diffs each against its last GOOD reading
(a box that could not be asked is said so, and is diffed next time). Output goes to `Z:\Documentation\State\lab-updates\`:
- `report.txt` — the Discord field (≤1,000 chars);
- `report-<stamp>.json` — everything;
- `upstream.json` — the last-seen upstream releases;
- `notify.log` — one line per run, or a traceback.

Sundae's 4:30 health check shows `report.txt` as **"Lab updates"** ([[RUNBOOK-Automated-Maintenance-Schedule]]).

- **⚠ INSTALLED** covers anything that changed since last night: Windows build, a driver, a program version, an engine's
  git/torch/packages, a new Ollama tree, a reboot now pending, a docker image, a kernel, and **a vendor updater task
  that appears or changes state**.
- **WAITING** covers newly offered Windows Update items (Defender signatures left out as daily noise), new or changed
  winget offers, and new apt packages.
- **UPSTREAM** covers new releases of ollama, ComfyUI and llama-swap, and new ai-toolkit commits (GitHub API, a failed
  ask is said).
- **The lab's own `HAI …` tasks are ignored** (a guard does not report its own bookkeeping).
- Tests: `Lab\bin\test_update_notify.py` (11), on real readings pinned in `Lab\bin\test-fixtures\update-inventory\`.
  Two bugs were caught there before it ever ran nightly. The inventory kept only the last 6,000 characters of winget's
  output (an arbitrary cap of mine), which cut Mothership's table header, so it parsed 0 of 38. And the separator line
  counted as a package.
- **First scheduled run, 2026-11-03 12:25:** rc=0 in ~50 s, every box asked over ssh in the S4U context (mordecai
  asleep). It reported the AMD tasks switched off, four Watchtower image pulls on Sundae, **Sundae waiting on a reboot
  for kernel 7.0.0-42** (running -38), an Intel network driver offered on BigHonker, and Softserve's winget changes.

## AMD's auto-update on Mothership — DISABLED 2026-11-03 (the owner: "Let's do it")

The second reading found **`\AMD Install Manager - Install Updates`** (`AMDInstallManager.exe -InstallUpdates -Auto`,
SYSTEM, trigger **At idle time**). It had been created by the 03:00 `Check For Updates`, whose chipset log concluded
"Is New Package Needed: Yes" with the installed version = latest, because it read the supported-driver list as "???".
So the check re-arms an idle-time install every day. That is the likely initiator of the 10-31 card loss (graphics
driver 16:15, `C:\AMD\AMDSoftwareInstaller_DIME_Chipset` 16:17). `AMDAutoUpdate` last ran ten minutes before the
10-22 crash-install. All three tasks were **disabled** (read back `Disabled`):
- `\AMD Install Manager - Check For Updates`
- `\AMD Install Manager - Install Updates`
- `\AMDAutoUpdate`

Their XML is kept in `C:\ProgramData\HAI\backups\amd-tasks-20261103\`. Undo with
`schtasks /change /tn "<name>" /enable`. The notifier reports them if AMD Software switches them back on or recreates
them. AMD driver/chipset updates now come only through #38's held lane (the owner's say until staging is built).
Softserve's `AMDAutoUpdate` (its iGPU) is still enabled.

## The stager — the lab installs Held updates itself (Automatic Plus; build order and state)

the owner, 2026-11-03: *"the lab handles the whole update process… Automatic Plus."* Every Held class gets a lab installer
that runs in the nightly window, **one box at a time**, and follows the same steps:
1. Pause the box's factory lane (the `fleet_deploy` stop→requeue).
2. Take a BEFORE reading with the lab's instruments (GPU health push, deck answering, ollama `/api/version`, ssh/LAN,
   and for drivers a salted speed check + fit probe).
3. Install.
4. Reboot if needed, and prove the box came back (`LastBootUpTime`, never "ssh answers").
5. Take an AFTER reading with the same inputs. A failure rolls back and is reported.
6. Requeue the lane, report in the morning field, and only then move to the next box.

Build order (state as of 2026-11-03):

| # | class | why this order | state |
|---|---|---|---|
| 1 | **Windows Update** (cumulative/security) | caused two of Mothership's freezes (10-10 0x9F, 10-28 hang) | **LIVE 2026-11-03** (see below); WU is download-only on a box from its first clean stage: **BigHonker since 11-03**, Softserve and Mothership at theirs |
| 2 | **Linux** — Sundae reboot after a kernel, rigby full `apt upgrade` + reboot | Sundae waits on 7.0.0-42 now; rigby 55 behind | to build (Sundae's reboot is a 2–3 min DNS/arr outage at ~4 AM, said in the report) |
| 3 | winget apps on an ALLOW list (Git, Tailscale, PuTTY, Obsidian, …) | many waiting, low risk | to build; DENY list: Ollama, CUDA, Python, VC++ runtimes, Build Tools, Node, .NET |
| 4 | Plex Media Server (Softserve) | serves people; updater now off | **BUILT 2026-11-04** (`update_stage.stage_plex`, rides the 03:40 apps task) under the owner's two rules: only a build **7+ days old** per Plex's own feed, and **nobody streaming** (Tautulli, any stream; could-not-ask = wait). Feed and winget must name the SAME build. PlexWatchdog paused for the install and re-enabled after (it relaunches Plex). Not the expected build on `/identity` afterwards → winget rollback to the exact old build + HOLD. ⚠ Its first "dry run" (18:45) was not dry: the probe ran `winget upgrade --id Plex.PlexMediaServer`, which UPGRADES — Plex went 1.43.4 → 1.43.5 outside the stage (streams were 0, Plex back in 13 s, no restart needed; `PlexInstaller` event 1000). The probe now runs a bare `winget upgrade` listing and a test refuses an `--id` in it. The guarded path waits for the next qualifying release. |
| 5 | GPU drivers | highest risk, rarest | **NVIDIA BUILT 2026-11-04** (`stage_nvidia`, task "HAI Lab Update Stage Drivers" 02:30): NVIDIA's own lookup per card; a WHQL release **7+ days old**; the **rollback package downloaded and verified first** (none served = no install); nobody at the console (idle 30+ min); **BigHonker first, Softserve only on a night after BigHonker ran that version OK**; Plex not streaming for Softserve; the proven `nvidia_driver_install.ps1` (Authenticode, SYSTEM one-shot, `-s -noreboot`, the box's clock/power task re-applied); after: version changed + fresh healthy GPU push + ollama answering + a generation at ≥ 80 % of the pre-install rate, else rollback + HOLD. **AMD on Mothership: REPORT ONLY** — the AMD installer's snapshot can deadlock in Acronis's filter until the exclusions are entered at the console (the owner); the morning field says so each night. |
| 6 | lab engines | already have their doors | **BUILT 2026-11-04** (`engine_pipeline.py`, task "HAI Lab Update Stage Engine" 05:30 — § "The ENGINE pipeline" below): the lab rebuilds the engine itself when ollama moves its llama.cpp pin, one phase a night, stopping for a person only when a lab patch no longer applies. The 03:00 notice's pin line reads the pipeline's state. ollama v0.40.14 (released 11-03) pins **b11963**; its tree is already prepared (patches + compat applied); the build runs once the release is 7 days old. |

### Smart archiving — the rule, and where it is already implemented (the owner: archive what we made or changed, not stock vendor packages)

- **Vendor packages we might roll back to are kept until their replacement has passed, then the older ones go.**
  NVIDIA: `Lab\downloads\nvidia-<version>\` keeps the last two versions (`fetch_package`); the installed version's
  package is fetched before a new one is installed. Plex: winget's manifests hold every version, rollback is
  `--version <old>`. Windows Update: Windows' own uninstall path.
- **Lab-made or lab-changed software is archived per generation, timestamped, before it is replaced:** the lab engine
  (`fleet_deploy --engine`: old trees kept side by side on the GPU boxes, rigby archives its bin as
  `llama-cuda114-bin-archive-engine-<stamp>`), the orchestrator (`known-good` image tag + `*.bak.<tag>` files per
  deploy), the foundry (`fleet_deploy` backups), `F:\ai-toolkit` with the FIX patches (never upgraded in place —
  `foundry_paths` says why), ComfyUI custom nodes and launchers (back up before any update), Kirk's `known-good`.
- **Not archived:** stock driver and OS packages beyond the one rollback version, Store apps, apps on the allow list
  (winget keeps them).
- Every stage writes its record (`State\lab-updates\stage-*.json`); the records are the archive of what changed when.

**The STORE (2026-11-04 evening, the owner: "Smart archive needs to be structure, not just a rule") — `Lab\bin\lab_archive.py`.**
One store, one manifest per generation, one tool: `Z:\Archives\lab-updates\<component>\<stamp>-<version>\manifest.json`
(+ the files, where the lab fetched them itself). Two classes: **`vendor`** (drivers, Plex, apps, Windows Update, apt:
the rollback COMMAND is kept, and the file where we fetched it — retention the last **2** generations per component)
and **`lab`** (the lab engine and its patched trees, launchers, Kirk: kept per generation, pruned only on the owner's word).
A generation another mechanism keeps (fleet_engine's side-by-side trees, rigby's `llama-cuda114-bin-archive-*`, the
orchestrator's `*.bak.<tag>`) is INDEXED with `location` instead of copied, so this one listing covers everything:
`python lab_archive.py list [component]` · `show <component> <stamp>` · `prune [--apply]`. Every stage calls
`update_stage.archive(...)` BEFORE it replaces anything; the engine pipeline records the prepared tree and each box's
superseded engine. Tests point the store at a tempdir (`LAB_ARCHIVE_DIR`) — the live store is never a test's
(`ArchiveIsolation`; nine fake generations my first tests wrote there were removed the same evening).

### The Windows Update stager — `Lab\bin\update_stage.py` + `wu_stage_box.ps1` (live 2026-11-03)

```bash
python E:\SSDShared\Lab\bin\update_stage.py bighonker                  # one box now; a daytime run never reboots
python E:\SSDShared\Lab\bin\update_stage.py bighonker --allow-reboot   # ...unless told
python E:\SSDShared\Lab\bin\update_stage.py --nightly                  # what the 03:20 task runs
```

One stage, each step the lab's own documented mechanism:
1. **The deck shows no running order.** A busy factory is WAIT, another night; it is never stopped for an update.
2. **Desk hold:** an `operator:` gpu claim through the orchestrator mirror's `scheduler.claim`
   ([[RUNBOOK-Compute-and-Training-Order-Submission]] § HOLD A BOX), refreshed every poll so a dead stager's hold
   lapses in 30 min. `release()` never lifts someone else's hold.
3. **Factory pause** (§ PAUSE A FACTORY): `STOP_AFTER_RENDER` + runner task disabled + a one-shot SYSTEM **"HAI Update
   Stager Resume"** task on the box, 4 h out, so the factory comes back even if the stager dies. Then wait for the
   runner (`python… p20_foundry.py --queue`, matched by process name so the asking PowerShell never matches itself) to
   exit between orders.
4. **Install:** `wu_stage_box.ps1` runs as a one-shot SYSTEM task "HAI WU Stage" (Windows Update refuses installs
   started from an ssh session). It searches `Type='Software'`, skips feature upgrades and anything needing a licence
   accepted, downloads and installs, and writes `C:\ProgramData\HAI\wu-stage\result.json` at every phase. The stager
   reads that STATUS (INCONCLUSIVE bound 3 h). **Mothership installs only KBs that succeeded on another box** (canary,
   from their inventory history).
5. **Reboot** only when the install needs it, at night or with `--allow-reboot`. It is proven by `LastBootUpTime`
   moving (45 min bound). On Softserve it waits if Plex is transcoding (asks the live orchestrator).
6. **After:** every update's result, ollama `/api/version` answering, and a fresh healthy GPU health push.
7. **Always** (finally): resume the factory (re-enable + start the runner task, delete an unconsumed flag, remove the
   safety task). A healthy box has its hold released. A failure keeps a **12 h hold** with the reason (HOLD FOR HUMAN).
8. A box's first clean stage sets **`AUOptions=3`** (download-only): from then on the lab installs.

Tests: `Lab\bin\test_update_stage.py` (12; a fake box records every call):
- the order of the controls;
- resume after a failed install, a crash, or a half-failed pause;
- a box that never comes back is held, not assumed fine;
- a daytime run never reboots;
- the Mothership canary;
- `release()` leaves other holds;
- the box-side script EXECUTES, not just compiles.

**Live proof, BigHonker 2026-11-03.**
- *First run (13:1x) failed safely-ish and found two bugs:* the box-side script crashed before acting (a `%` applied
  to half a string concatenation; my test only compiled it), and a failed pause was not resumed because the "paused"
  mark came after the call. Nothing on the box had changed, but the failure verdict kept a 12 h desk hold on
  BigHonker. I released it by hand within minutes. Both bugs are fixed and pinned in tests.
- *Second run (13:18): OK.* Hold → pause (runner exited) → Defender definitions KB2267602 1.463.110.0 installed
  (result 2, no reboot) → ollama 0.40.5 + GPU healthy → factory resumed with a NEW runner (pid 69992), task enabled,
  no flag left → hold released → `AUOptions=3`.
- *The 03:20 task run by hand under S4U:* result 0, `nightly: waiting {…0…} -> nothing to stage`.
- **Not yet exercised live:** a reboot, Softserve's own reboot + the startup resume, and the Mothership canary. The
  first real cumulative update (next Patch Tuesday) exercises them, and the record will say.

### The Linux stager — Sundae and rigby (`update_stage.py --linux-nightly`, task "HAI Lab Update Stage Linux" 04:12)

- **Sundae** (`stage_sundae`): only when `/var/run/reboot-required` says so; refuses before Watchtower's 04:00 pass is
  done; parks HAI's runs (`/runs/park`) and waits for `all_parked`; `systemctl reboot`; proves the boot time moved;
  then every container that was running is back, the desk answers `/health`, both CIFS shares are mounted, and the
  house Pi-hole answers (asked from Softserve, since Sundae cannot reach its own macvlan). 2–3 min of DNS/HAI/arr
  outage, said in the report.
- **rigby** (`stage_rigby`): waits on Tolkien's status until it runs nothing (bounded 25 min, said); `rigby_apt.sh`
  (refuses without the nvidia-470 stub pin); a new kernel is tried ONCE (`grub-reboot`, the default untouched) and
  only when it is 6.8, the 470 module is built for it and `linux-modules-extra` is installed; after the boot the
  documented `usbnet_up.py --apply` runs; the compare is GPUs on 470, USB links, Tolkien's own healthy-node count,
  the three containers, the order taker and the bridge, judged after the USB keeper has had its passes; a pass makes
  the kernel the default (GRUB backed up), a failure reboots back to the old kernel. Root work goes through
  `run_remote.py` (the locker's sudo password on stdin). Scripts: `Lab\bin\linux_stage\`.
- **First unattended night, 2026-11-04:** 03:00 notice rc 0 → 03:20 Windows stage "nothing waiting" → **04:12
  Sundae: restarted onto 7.0.0-42, 31 containers, desk, shares and DNS back** (its first live reboot by the lab) →
  04:16 rigby: WAIT, Tolkien busy → 04:30 health check posted (exit 0). Two defects found and fixed 11-04 morning:
  the stage gave up on a busy Tolkien at once (the 4-hourly claim review lands at 04:10 and runs ~7 min, so every
  night would have collided; it now waits on status up to 25 min), and the second stage record of the night
  overwrote the first in the Discord field (Sundae's OK was never shown; the field now carries every stage line
  since the night's report, oldest first). Tests: 15 + 11.

### The APPS stager — winget allow-list + the Microsoft Store (`update_stage.py --apps-nightly`, 03:40; built 2026-11-04)

- **Microsoft Store auto-updates are OFF since 2026-11-04 ~17:20** on all three Windows boxes: policy
  `HKLM\SOFTWARE\Policies\Microsoft\WindowsStore\AutoDownload = 2`, read back `0x2` on each (the key did not exist
  before; recorded in `held-lane-20261103\store-policy-before.txt`). Why: the 11-03 22:05 Mothership stall began with
  Store app downloads → a restore-point snapshot (the LTT side quest's timeline, § below). Undo: delete the value.
- **In the window the lab runs the Store's own update scan** (`MDM_EnterpriseModernAppManagement_AppManagement01 →
  UpdateScanMethod`), because winget cannot see Store apps headless (`--source msstore` lists nothing over ssh on
  Softserve and Mothership, 11-04).
- **winget apps on an ALLOW list** (`update_stage.APPS_ALLOW`, lab infrastructure only). Not on it, by design:
  the toolchains the factories run on (Ollama, CUDA, Python, VC++ runtimes, Build Tools, Node, .NET), Plex (its own
  class), kernel drivers (PawnIO → the drivers class), and the owner's own desktop apps (games, Adobe, Spotify, iTunes,
  Teams, Outlook, Claude, ChatGPT, VS Code, LM Studio, OpenRGB…), which are his and carry their own updaters.
  `APPS_DENY` names the first two groups so a test fails if one is ever added to ALLOW. Whatever is offered but not
  listed is REPORTED in the stage record ("not in the lab's list"), never touched.
- Box side: `Lab\bin\apps_stage_box.py` over `ssh <box> "python -"` (one `winget upgrade --id <id> --exact --silent
  …` per allowed id; a failure is said and holds nothing — that app is simply unchanged). Same hold → pause → resume
  wrapper as the other stages; no reboot.
- **The Chrome Remote Desktop host is on the list**, which closes the 10-30 backlog item "CRD host never
  auto-updates" the lab's way (its first install: see the live proof below).
- Tests: `test_update_stage.AppsStage` (3): deny never reaches allow; the box script, executed against a fake winget,
  upgrades only the allowed id and reports the rest; the stage holds, pauses, always resumes, and a crash is a HOLD.

**BigHonker's winget is broken, diagnosed 2026-11-04** (known since 07-26 as `0x8a15000f`): its own diag log shows
`Settings.cpp … 80070005 Access is denied` → `PreIndexedPackageSourceFactory … 8A15000F` → "Failed to open available
source: winget", every run, under the ssh user **<user>** (not <user> — ollama's task runs as <user>, ssh lands as
<user>). `winget source reset --force` + `source update` succeed and change nothing. **The denied write is winget's
DPAPI-encrypted setting** — the log line before the first error is `[CORE] Setting action: Set, Type: Encrypted,
Name: rest_information` → `Settings.cpp(413) … 80070005`. Over ssh (a public-key network logon) <user>'s DPAPI master
key on BigHonker cannot be used, so the encrypted write fails and winget falls back to a per-user index cache it does
not have for that profile (`LocalCache\Microsoft\WinGet` absent; the only index is a stale 07-26 one under
`%TEMP%\WinGet`). Same class as the Mothership share credential ([[RUNBOOK-Windows-Reachability-Traps]]): the WRONG
LOGON CONTEXT, not a broken tool. Softserve (Administrator) and Mothership (<user>) over ssh are fine, so the key
protection differs on BigHonker's <user>. **The remedy, built 2026-11-04 evening (`update_stage.APPS_VIA_TASK` +
`TASK_DRIVER`):** on BigHonker the stage does not run winget over ssh. The ssh leg only stages
`apps_stage_box.py` under `C:\ProgramData\HAI\apps-stage\`, reads **who is at the console right now** (`query user`,
the Active console session — on 11-04 that was **<user>**, not <user>), registers a one-shot task for that user with an
**Interactive** principal (`Register-ScheduledTask -LogonType Interactive`: it runs only in that user's own session,
which is the point, and registering it needs no password), `pythonw` so nothing flashes on the desktop, Highest only
if that user is a local administrator, starts it, and waits on the task's STATUS: the result file the script writes
(`result.json`, atomic), or the task ending without one. Nobody at the console → the stage says so and holds nothing.
The task and the staged script are removed afterwards. Probes that found the cause: Claude scratchpad
`winget_diag.py`, `winget_where.py`, `winget_acl.py` (2026-11-04).

**Live proof, BigHonker 2026-11-04 18:3x–18:5x.** First run: in <user>'s session winget's source ANSWERED (11 offered;
`source_ok`) — the diagnosis held — but the one allowed update, WSL, died `0xC000013A` right after "Installer hash
verified", and the Store scan's PowerShell returned nothing. Cause: under a windowless (`pythonw`) task every console
child opens its own visible window on the desktop, and a window can be closed or disturbed. Fix: children launched
with `CREATE_NO_WINDOW` (`apps_stage_box.NO_WINDOW`), output still through the pipes. Second run, one variable
changed: **WSL 2.3.26 → 2.7.14 installed, Store scan `ReturnValue=0`**, factory resumed, hold released, task gone.
10 offered apps were left alone as not on the list (Epic, Outlook, Teams, VC++ 2010, Notepad++, Ollama, Python
Launcher, Zoom, PawnIO). The record notes which user and level the task ran as (`result.task`).

**Live proof, Softserve 2026-11-04 17:27–17:33 (daytime, `--apps softserve`):** hold → pause → winget → Store scan
(`ReturnValue=0`) → resume → release. **Installed 10:** CrystalDiskInfo 9.9.4, GitHub CLI 2.103.3, **Chrome Remote
Desktop host 143 → 157.0.8103.41** (the backlog item), Graphviz 16.1.0, winget 1.29.380, WSL 2.7.14, Windows Terminal
1.25, Obsidian 1.15.0, SD Formatter 5.0.4, Tailscale 1.104.5. **LibreOffice 26.8.2 installed too** and asks for a
restart — the first cut counted that as a failure (no "Successfully installed" line); fixed: winget's exit code is the
verdict, the text adds "(restart required to finish)". **Two real failures, both "the app is running" in daytime:**
Git (`0x8A150006`, the installer's own exit 1 — Git Bash sessions from this very tool were open) and FanControl
(`0x8A15010C`). They stay at their versions and retry at 03:40, when nothing holds them. 11 offered apps were left
alone as not on the list (Node, .NET, VC++, Plex, Ollama, Outlook, ChatGPT, PawnIO…). Record:
`State\lab-updates\stage-softserve-20261104-17xxxx.json`.

**The Mothership constraint (applies to 1, 4 and 5):** an installer's VSS restore-point snapshot can deadlock in
Acronis's filter ([[RUNBOOK-GPU-Host-Power-and-Glances]] § sleep-crash: 10-10, 10-22, 10-28). Mothership installs go
last in each class, after the other boxes prove the package. They run only with nothing else starting on the box
(no ssh bursts, factory paused), and a stuck install must be detected by status, not by a clock.

### The ENGINE pipeline — `Lab\bin\engine_pipeline.py` (task "HAI Lab Update Stage Engine" 05:30; built 2026-11-04)

the owner, 2026-11-04: *"since when is the engine rebuild a me thing? We always have had you or the lab do it."* The lab
rebuilds its own llama.cpp engine when ollama moves its pin, exactly the way the 10-22 build was done by hand
([[RUNBOOK-Ollama-Host-Operations]] § LAB ENGINE), **one phase per night**, state in `State\lab-updates\engine.json`:

| phase | what runs | stops when |
|---|---|---|
| **detect** | ollama's newest release → its `LLAMA_CPP_VERSION` → the llama.cpp commit; `fleet_deploy --check` for what the fleet SERVES | same commit → nothing to do; release younger than **7 days** → WAIT (the lab's week rule) |
| **prepare** | llama.cpp at that tag + ollama's source at that release → `lab-engine\llama.cpp-<short>`; lab patches #0 / #3 / #2 / #4 / #5 (their own scripts, in the engine repo `E:\SSDShared\lab-engine\patches`) + `apply_ollama_compat.py` | **a patch missing from the folder → HOLD naming it, before any patch runs; a patch whose anchor no longer matches → HOLD** — the one step that is a person's (a rebase); everything else never asks |
| **build** | the `labengine` desk order on Mothership (`build_lab_engine.py`: nvidia cuda_v13 + amd vulkan sets, AVX2 and lab markers asserted, `engine.json` + sha256 manifest) | the desk refuses → HOLD |
| **delivered** | the order's fate at the desk + `engine.json` on the campaigns share | still building → WAIT; failed set → HOLD |
| **canary** | `fleet_deploy --engine <dir> --only bighonker`, then the lossless-resume probe there (`submit_resume_probe.py`) | probe not LOSSLESS → `--engine-rollback` + HOLD |
| **fleet** | Softserve + its probe, Mothership, then rigby (the tree and `patches\cuda_graphs_pre_volta_patch.py` over, `build_cuda114.sh <short>:<engine folder>` through `run_remote`, `--engine --only rigby`) | any failure → rollback that box + HOLD |
| **verify** | `fleet_deploy --check`: every factory on `<short>+compat` | → **done**; the pin the 03:00 notice compares against moves with it |

`python engine_pipeline.py status` says where it stands; `reset` restarts at detect after a HOLD was dealt with. The
`lab_archive` store gets the prepared tree (class `lab`) and each box's superseded engine (indexed at fleet_engine's
side-by-side tree / rigby's bin archive). **Fixed on the way:** `fleet_engine.deploy` named every lab tree with the
literal `620d222` — a second build would have been `-lab620d222` again and REFUSED as "the tree already serving";
the tag now comes from the order's `engine.json` (`tag_of`, commit `c4e36d5`). Tests: `test_engine_pipeline.py`
(18 since 2026-11-19: the decision, every phase against fakes, the patch HOLD, the missing-patch HOLD, rollback on a failed canary, hold-until-reset, the tablet head's copy and build arguments, and a fail-closed check that every patch the pipeline applies is a file in the patches folder; the fakes answer for any name, so only that check can fail on a missing one).

**Proven live 2026-11-04 evening:** detect read GitHub and the fleet (0.40.14 is 2 days old → WAIT); **prepare ran
for real on b11963 (`39febd2`)**: all three patches and the compat layer (8 call sites) applied cleanly, so the first
automated rebuild will not stop on a patch; the tree sits in `lab-engine\llama.cpp-39febd2` for the pipeline to reuse.
**Not yet proven live:** build → canary → fleet → verify — they run on their own once 0.40.14 is a week old (~11-10);
the morning field will say each night's phase. If a night's phase HOLDs, the state file carries the verdict verbatim.

## First run, 2026-11-02 12:10 CT — the findings

| box | OS | Windows Update / apt waiting | GPU driver | program updates (winget) |
|---|---|---|---|---|
| Mothership | 11 Pro WS 26300.9818 (26H2) | 2: Defender signatures, a Razer HID driver | AMD 32.0.32015.12012 (dated 10-27, in since 10-31) | 38 |
| BigHonker | 11 Pro WS 26300.9818 (26H2) | 5: Defender signatures + 4 Intel/Microsoft drivers (PCIe controller, Bluetooth, network) | NVIDIA 32.0.16.1742 (617.42) | winget source broken |
| Softserve | 11 Pro WS 26200.9711 (25H2) | 6 drivers: Realtek LAN, three AMD system/security, ASIX net, Microsoft | NVIDIA 32.0.16.1692 (616.xx); AMD iGPU 31.0.21925.1001 | 24 |
| Sundae | Ubuntu 26.04.1, kernel 7.0.0-38 | 5 apt + 1 snap; unattended-upgrades on | — | — |
| rigby | Ubuntu 24.04.4, kernel **6.8.0-31** (the release kernel) | **55 apt**; unattended-upgrades on | NVIDIA 470.256.02 (1060 + 730) | — |
| mordecai | — | COULD NOT ASK (asleep; ssh timed out, not woken for a read) | — | — |

**What can install something on its own (the "unannounced" sources #38 exists for):**
- **Windows Update with no policy at all on all three boxes** (no `NoAutoUpdate`/`AUOptions`, drivers not excluded).
  Drivers arrive through it: every box has driver updates waiting. Softserve's pause expired 2026-08-15.
- **AMD on Mothership:** `AMD Install Manager - Check For Updates` (daily 03:00) and `AMDAutoUpdate` (weekly, next
  11-06). **`AMDAutoUpdate` last ran 2026-10-22 09:20:11, ten minutes before that day's AMD Software 26.10.1 install
  completed (09:30), the install that hung the box into a 0x9F.** That is consistent with it starting that install,
  but a last-run time does not prove it. The 10-31 install (16:15) matches neither task's schedule; its initiator is
  still not established (2026-10-31-claude). Softserve also has `AMDAutoUpdate` (its CPU's Radeon iGPU).
- **ASUS Update** (twice-daily tasks on all three boxes; BigHonker's `EZ Update` was running), **Intel Driver &
  Support Assistant** (BigHonker), **NVIDIA App self-update** (BigHonker, Softserve), **Plex Update Service**
  (Softserve, and Mothership has a Plex Media Server install of its own), Edge, Office (Mothership), Adobe, Apple,
  Ubisoft, Zoom and OneDrive (BigHonker).
- **Not updating at all:** the Chrome Remote Desktop host on Softserve (143.0.7499.7; 157 is out). See the backlog
  item "CHROME REMOTE DESKTOP HOST NEVER AUTO-UPDATES".

**⚠ Traps for any "update everything" step — never run `winget upgrade --all` on a lab box:**
- **Ollama:** winget reads the old STOCK installs (0.32.13 Mothership, 0.30.7 Softserve) and offers 0.40.13. The lab
  serves its own build (`Ollama-0.40.5-lab620d222`, engine parity on all four factories, `fleet_deploy --engine`). A
  winget upgrade would install a stock engine beside it and could take the tray launch path that the Mothership runbook
  forbids. Ollama changes go through `fleet_engine` only.
- **Toolchains the lab depends on:** NVIDIA CUDA 13.0, Python Launcher, Git, the Visual C++ runtimes, Visual Studio
  Build Tools, Node, .NET. These need a staged test, never a blind upgrade.
- **Plex Media Server on Softserve** serves people; it updates through its own service. Do it in a window, not ad hoc.

## The lab's own factory engines — the update class that matters most (read 2026-11-02 13:00)

the owner: *"You're missing our other lab factory engines."* These are vendor-sourced but lab-built, lab-patched or pinned
for a measured reason. An "update" for them is never a vendor installer. It is a lab build/order through the door each
already has, with its own proof. The inventory reads each one where it lives: the foundry's own constants
(`foundry_paths`), git revision from `.git`, package versions from each environment's dist-info (no imports).

| engine | where | version now | its door / proof |
|---|---|---|---|
| **LLM: the lab engine** (ollama 0.40.5 + upstream llama.cpp `620d2225c` + ollama's compat layer + lab patches #0/#2/#3) | Mothership (Vulkan), BigHonker + Softserve (CUDA 13), rigby (`/llama/cuda`, CUDA 11.4) | tag `620d222+compat` on all four | `fleet_deploy --engine` / `--engine-rollback`, `--check` `tag=`; re-fit every box + resume probe ([[RUNBOOK-Ollama-Host-Operations]] § LAB ENGINE). **An upstream ollama/llama.cpp release = a new lab build at the new pinned commit, never winget.** Old trees stay side by side (rollback): Mothership 0.32 / 0.34 / 0.40.2 / 0.40.5 / lab, the same on the others |
| **Flash-Next server** | Mothership: llama-swap **v265** on :11435 | v265 (only a `.retired` Flash-Next tree in `F:\`) | `fleet_deploy --workspace flashnext llama-swap.yaml` |
| **rigby / Fellowship llama.cpp builds** | rigby mounts: `/llama/cuda` (lab engine), `/llama/bin` (x86), `/llama/vulkan`, `/llama/arm64` (the tablets' `ggml-rpc-server`), `/llama/next/*` (rpcasync), `/llama/hotstore` | `llama.cpp-latest` sources at e467e5b (10-15); the live CUDA bin is the lab build | `fleet_deploy --check` tag; `RUNBOOK-GT730` § "Which tree is LIVE" (read the mount) |
| **ComfyUI (renders: FLUX.2, Krea 2)** | Mothership `F:\comfyui-rocm`, Python `python_env` | git `63be919f15`; torch **2.12.0 + ROCm 7.15 alpha** (2026-07-14); diffusers 0.39.0; transformers 5.13.1; 10 custom nodes; **ships `comfyui-rocm-updater.bat`** | render/probe orders; the stall daemon's render watch |
| ComfyUI #19 original | Mothership `E:\ComfyUI` | git `f3a36e7484`; torch 2.9.1 + ROCm 7.2.1 | not used by the factory ([[RUNBOOK-ComfyUI-Image-Generation]]) |
| **ComfyUI (klein)** | BigHonker `F:\comfy-bench\ComfyUI` (zip install, no git), venv `F:\comfy-bench\venv` | torch **2.5.1 + cu121**; 3 custom nodes | render.klein orders |
| **ai-toolkit (FLUX.2 trainer) — LAB-PATCHED** | Mothership `F:\ai-toolkit` | git `9aeb17f609` main + the FIX-01..09 resume patches; torch 2.12.0 + ROCm 7.15 alpha; diffusers 0.37.0.dev0 | training orders. **Smart archiving applies: this tree carries lab changes** (`foundry_paths` says upgrading it in place would put FLUX.2 runs at risk) |
| **ai-toolkit (Krea 2 trainer)** | Mothership `D:\ai-toolkit-krea2` (own venv) | git `73ebae13a4` (detached); torch 2.12.0 + ROCm 7.15 alpha; diffusers 0.39.0.dev0; transformers 5.5.3 | train.krea2 orders |
| Softserve foundry venv | `E:\foundry\venv` | torch 2.11.0 + cu128 | foundry suite |
| **Locally built containers** (Watchtower does not touch them) | Sundae: `arrstack-orchestrator` (Kirk, also tagged `known-good`), `coding-runner`, `arrstack-tablet-foundry/-watchdog`; rigby runs the two tablet images | built 2026-11-02 17:5x | orchestrator deploy procedure (gate); `fleet_deploy` Tolkien branch |
| Fellowship tablets (Android/Termux) | the tablets | not read yet | Tolkien |

What the engines mean for the update system:
- **They update by ORDER, not by installer.** Each has a door, and the door already pauses work, keeps the old version
  beside the new and checks the bytes. What is missing is the NOTICE (an upstream release appeared) and the COMPARISON
  (same inputs, old vs new: speed check, fit probe, render/train probe, resume probe) as the standard first step.
- **Three engines can update themselves by a vendor path:** ComfyUI's bundled updater (Mothership), stock Ollama via
  winget (the trap above), and pip inside any venv. All three must stay manual.
- **Smart archiving list (the owner's rule: archive what we made or changed):** the lab engine builds (rigby already archives
  its bin; the GPU boxes keep the old trees), `F:\ai-toolkit` with the FIX patches, the ComfyUI custom nodes and
  launchers, Kirk's `known-good` image. Not stock driver packages.

## How this connects

- GPU driver versions and their history: [[RUNBOOK-GPU-Host-Power-and-Glances]] § "GPU driver versions in force".
- The crashes that started #38 (installer snapshots deadlocking in Acronis's filter): same runbook, § sleep-crash.
- Each box's health push already carries its driver version: [[RUNBOOK-GPU-Reporters-and-Load-Accounting]].

## 2026-11-04 — the stall evidence that makes #38 the fix (handed here from the LTT side quest; pick up with this)

**New 2026-11-05:** a third trigger, and the first the lab can move itself: a 2.2 GB file written to C: (a Flash-Next session save) started a 16-minute
stall at 00:41; the same file on F: caused none. Timeline: [[RUNBOOK-GPU-Host-Power-and-Glances]], first entry of the stall list. Flash-Next's saves moved
to F: that night; ollama's chat saves on Mothership are still on C:.

the owner, driving, 2026-11-04: *"Why is it taking snapshots all of the time? Would it not make sense for it to just be
on an update cycle like how we do with plenty of other things in the lab?"* — and *"Obviously I don't want my own lab
to be tripping my stuff."* Both are #38's job. What was established today (full timeline, six read-only pulls and
the control: `Claude Home\research\ltt-jarvis-prompts\runs\mothership-hang-20261104\FINDINGS.md`):

- **The stalls are back, and they happen most days unnoticed.** 11-03 22:08–22:33: Store/Windows Update downloads →
  restore-point snapshots → Mothership "looks alive and is not" for 13 min (ollama HTTP silent, sshd no banner,
  telemetry logger stopped, ESENT HUNGIO on C:, VSS freeze-too-long); nobody was asking the box. 11-04 13:32–14:02:
  eight self-releasing waves; HAI asked the box mid-wave and three jobs fell to the 8 GB tier.
- **Positive control:** the UPS power logger (long-running Python, F: writes every 5 s, open USB HID handle) never
  missed a row — the stall is the system volume / new-process path under Acronis's filter, not the GPU, not USB.
- **Canary, already logged on the box:** PowerPanel's self-restart (Application event 4100) fires at the end of each
  wave; first on record 10-20, 1–12 per day since. With the GPU telemetry row gaps that is a free detector.
- **Acronis on Mothership has NO backup task.** What runs is Active Protection (anti-ransomware). Its log shows the
  **lab's own monitors** feeding it ~1,000 inspected events a minute (the stall recorder spawns PowerShell every 2 s,
  `hai_stall_recorder.py:79`; board-health shells `tasklist`; heartbeat/state files replaced every few seconds; a
  blocked `telemetry.acronis.com` lookup retried constantly). Its exclusion list is encrypted on disk, no CLI found
  (11-04): **the list to enter at the console is in [[RUNBOOK-GPU-Host-Power-and-Glances]] § Defender exclusions,
  Acronis paragraph.** Defender's three missing folders (krea2) were applied 11-04 on the owner's go.
- **Snapshots "all the time" = Windows restore points at every unscheduled install** (Store apps 11-03 22:05, AMD
  installer 10-22, Windows Update 10-10/10-28) — exactly what #38's stager replaces with a lab-chosen window. 11-04's
  first wave had no snapshot: it coincided with the Flash-Next engine's 8-minute teardown (unproven mechanism).

**For #38 to close this:** (1) updates only in the lab's window, box idle, snapshot expected — and the window itself
treated like a factory claim so nothing is placed on the box during it; (2) Acronis Active Protection exclusions for
the lab (the owner, console); (3) the monitors stop spawning shells every beat — in-process queries (psutil / WMI via COM)
in `hai_stall_recorder.py`, `p20_board_health.py`, the rescue daemon, `helper_window.py`; (4) the stall detector on
the deck from the canary + telemetry gaps, so the orchestrator's wake path reads "Mothership: stalled (N min)" instead
of "did not wake" (the wake path's own fixes — direct probe, stall = leave alone, one ssh restart per wake, the status
says what was measured — SHIPPED 2026-11-04 15:02, gate 3335; see the FINDINGS § HAI); (5) still owed from before:
`CrashOnCtrlScroll` for a real dump next time. The LTT side quest's remaining runs resume when (1)–(4) are in.
