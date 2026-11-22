# RUNBOOK — Automated Maintenance Schedule



## Automated Maintenance Schedule

The lab's scheduled maintenance: systemd timers on SundaeStackarr, scheduled tasks on Softserve, and the droplet's
and the Pi's own jobs. Times are Central Standard Time: a Windows task whose trigger carries a UTC offset fires an
hour later by the wall clock under daylight time. HAI's own periodic jobs and standing services are in the next
section.

| Time | Machine | Task | Notes |
|------|---------|------|-------|
| Every 5 min | DO droplet | Pi-hole ACL update | Resolves router.example.org → updates ufw for home IP |
| Every 10 min | DO droplet | Avery ACL update | Tailscale API beacon → updates ufw port 53 for Avery IP (LEFT IN PLACE — the owner's call) |
| Every 15 min | DO droplet | Plex monitor | Checks external Plex reachability, Discord on change |
| Every 15 min | MinuteMan Pi | DDNS updater | Checks home IP via DO echo endpoint → updates DO DNS if changed |
| Every 15 min | SundaeStackarr | lan.list → MinuteMan sync | Hash-gated push to MinuteMan custom.list + reloaddns |
| 3:00am daily | DO droplet | Pi-hole sync | Pulls gravity/blocklists from primary Pi-hole (3× retry) |
| 2:30am daily | Softserve | **Lab update stager — GPU DRIVERS (#38)** | Task "HAI Lab Update Stage Drivers" (Administrator S4U, `pythonw`): `update_stage.py --drivers-nightly`. NVIDIA on BigHonker then Softserve: NVIDIA's lookup, a WHQL release 7+ days old, rollback package fetched first, console idle 30+ min, BigHonker canary before Softserve, Plex idle, the proven `nvidia_driver_install.ps1`, after-checks (version, GPU health, ollama, generation rate) else rollback + HOLD. Mothership's AMD driver is reported, never installed by the lab. Runs first because an install can take 25 min. **Since 2026-11-14:** the package is PUSHED from Softserve to the box's `C:\ProgramData\HAI\drivers\` over ssh (`push_package`, byte count checked there) — the box reading `\\<softserve-address>\SSDShared` from its ssh session was refused ("Permission denied", BigHonker 11-11..14, the SMB-from-ssh lesson); and a failure that left the driver UNCHANGED (asked via `nvidia-smi`) is a `WAIT`, hold released, while only a changed or unreadable driver keeps the ~18 h `FAIL_HOLD_TTL` hold, its text saying what is known (those four nights each held BigHonker ~18 h as "after-checks failed" with nothing installed). See [[Lab-Update-Inventory-38]] § "The stager" |
| 3:00am daily | Softserve | **Lab update notify (#38)** | Task "HAI Lab Update Notify" (Administrator S4U, `pythonw`): `E:\SSDShared\Lab\bin\update_notify.py` asks every lab box what is installed and waiting (`update_inventory.py`, read-only), diffs each against its last good reading, and writes `Z:\Documentation\State\lab-updates\report.txt` — shown as the **"Lab updates"** field of the 4:30 health check. Run log `notify.log`; history `report-<stamp>.json`. See [[Lab-Update-Inventory-38]] |
| 3:20am daily | Softserve | **Lab update STAGER (#38)** | Task "HAI Lab Update Stage" (Administrator S4U, `pythonw`): `Lab\bin\update_stage.py --nightly`. It picks the first box (BigHonker → Softserve → Mothership) with Windows updates waiting in the 03:00 reading. Steps: desk hold → factory pause (flag + runner task disabled + on-box safety resume task) → Windows Update install as a SYSTEM task → reboot if needed (proven by boot time) → ollama + GPU health checks → resume + release. Its line goes to the top of the 4:30 "Lab updates" field. A failure keeps a 12 h desk hold and says why. Log `stager.log`; records `stage-<box>-<stamp>.json`. Startup task "HAI Lab Update Stage Resume" finishes a stage Softserve's own reboot interrupted. See [[Lab-Update-Inventory-38]] § "The stager" |
| 3:40am daily | Softserve | **Lab update stager — APPS (#38)** | Task "HAI Lab Update Stage Apps" (Administrator S4U, `pythonw`): `update_stage.py --apps-nightly`. Per Windows box, in order BigHonker → Softserve → Mothership: deck idle → desk hold → factory pause → `apps_stage_box.py` upgrades only the winget ids on `APPS_ALLOW` (lab infrastructure: Git, GitHub CLI, PuTTY, Windows Terminal, winget itself, Tailscale, Chrome Remote Desktop host, CrystalDiskInfo, FanControl, Obsidian, LibreOffice, Graphviz, tesseract, WSL, Pi Imager, Etcher, SD formatter) and triggers the Microsoft Store's own update scan → resume + release. Anything offered but not on the list is reported, never touched (toolchains, Plex, drivers, the owner's own apps). No reboot. See [[Lab-Update-Inventory-38]] § "The stager" |
| 4:12am daily | Softserve | **Lab update stager — LINUX (#38)** | Task "HAI Lab Update Stage Linux" (Administrator S4U, `pythonw`): `update_stage.py --linux-nightly`. Sundae: reboot only after a kernel update, Tolkien idle first (a 2–3 min DNS/arr outage, said in the report). rigby: `apt upgrade` + a kernel try-once, Tolkien's rigby lane idle, reboot via `systemd-run` so the stager reads DONE before nologin. See [[Lab-Update-Inventory-38]] § "The Linux stager" |
| 5:30am daily | Softserve | **Lab ENGINE pipeline (#38)** | Task "HAI Lab Update Stage Engine" (Administrator S4U, `pythonw`, 6 h limit): `engine_pipeline.py` advances ONE phase per night — detect (ollama's newest release 7+ days old → its llama.cpp pin vs what the fleet serves) → prepare (patches #0/#3/#2 + compat; a patch that no longer applies is the only HOLD for a person) → build order on Mothership → delivered → BigHonker canary + resume probe → fleet incl. rigby's own build → verify (`fleet_deploy --check`). State `State\lab-updates\engine.json`; `python engine_pipeline.py status` / `reset`. Runs after every other stager so no two share a box. See [[Lab-Update-Inventory-38]] § "The ENGINE pipeline" |
| 2:30am daily | Softserve | Claude Code transcript backup | Task "Claude Transcript Backup" (SYSTEM): additive robocopy of `.claude\projects` → `Z:\Archives\claude-transcripts\softserve\`; verdict in `_state\last_run.json`. See [[RUNBOOK-Claude-Transcript-Backup]] |
| 4:25am daily | DO droplet | Avery status → Sundae | scp avery_status for inclusion in health-check embed |
| 3:50am daily | SundaeStackarr | `apt upgrade` | System packages, Discord notification |
| 4:00am daily | SundaeStackarr | Watchtower | Docker image updates, Discord notification |
| 4:30am daily | SundaeStackarr | Stack health check | Tests DNS, VPN, qBittorrent, arr apps, JukeBot → Discord embed |
| 5:00am Sunday | SundaeStackarr | GitHub config backup | Commits config to `sundaestackarr-config` private repo |
| 5:15am Sunday | SundaeStackarr | Local rsync backup | Full arrstack sync to `E:\SSDShared\Backup\SundaeStackarr\` (**the SSD, not Z:** — see the backup section) |
| 5:20am daily | SundaeStackarr | Photo archive sweep | `hai-photo-archive.timer` → `/usr/local/bin/head_sweep.sh photos/archive-sweep` → Kirk `POST /photos/archive-sweep`: archived chats' conversation-only photos become text descriptions after 14 days (a background `vlm` desk order), then the photo is deleted. DMs only on action/failure. Reference/HAI-Control-Surface § "Attaching files and photos" (since 2026-11-14) |
| 00/04/08/12/16/20:10 daily | SundaeStackarr | Doc claim sweep | `hai-claim-sweep.timer` → `/usr/local/bin/head_sweep.sh claims/sweep` → Kirk `POST /claims/sweep`: reviews changed library docs for unbacked claims as a background desk order (row added 2026-11-14; the timer predates it) |
| 2 min after every Kirk start, then every 24 h | SundaeStackarr (inside Kirk, no timer) | Desk archive sweep | `app._desk_housekeeping` → `p20_queue.archive_old`: finished factory orders and collected control slips older than 30 days are MOVED to `State\p20-render-queue-archive\<YYYY-MM>\` (never deleted; `get(id)` still finds them). Log line `DESK ARCHIVE`. The desk's parsed cache is saved when Kirk stops (`DESK CACHE saved`). Reference: [[Desk-Time-System-Time]] |
| 5:40am daily | Softserve | the utility fetch (task "the utility Daily Fetch") | Headless login → usage+bill zips → scp; + in-session latest-bill PDF hash-check (acts only on a new monthly statement) |
| 7:00am daily | SundaeStackarr | the utility import | Parse incoming zips → import usage/bill/real-bill HA stats + push cost sensors |
| :05 hourly | SundaeStackarr | the utility today accrual | `today_accrue.py` — hour-by-hour temp-driven "today so far" cost → `accrue.log` |
| 6:30am 1st of month | SundaeStackarr | the utility temp-model refit | `fit_temp_model.py` — re-fit kWh-vs-degree-day model (NCEI KMQB) → `fit.log` |
| 6:00am 1st of month | SundaeStackarr | Recyclarr sync | Syncs TRaSH guide custom formats/scores to Radarr & Sonarr |

### HAI's periodic jobs and standing services, machine by machine (read live 2026-11-20)

Every scheduled task and timer HAI itself runs. A **standing** service is re-armed by its schedule (a repeating trigger
with IgnoreNew, or `Restart=always`): the schedule only brings it back if it stopped. The factories' installer
(`foundry\setup_factory.py`, the shapes in `factory_role.json`) sets these up on a new box under "HAI Factory <name>";
the live boxes' tasks predate it and keep the names below.

| Schedule | Machine | Task or timer | What it does |
|---|---|---|---|
| every 2 min + at startup | Softserve | "Kirk Change Watchdog" (SYSTEM) | `Lab\bin\kirk_watchdog_tick.ps1`: watches the head after a change and rolls Kirk back if it does not come up ([[RUNBOOK-Kirk-Change-Watchdog]]) |
| every 1 min | Mothership, BigHonker, Softserve | "HAI GPU Health Reporter", "HAI GPU Load Reporter" (SYSTEM) | push the card's alive/lost verdict, and its use and power, to the head |
| standing (15 min) | Mothership, BigHonker, Softserve | "GPU Telemetry Logger" (SYSTEM) | the card's telemetry into `var\state\telemetry\gpu_telemetry.csv` (AMD logger on Mothership, NVIDIA on the others) |
| standing (15 min) | each factory | the order taker ("P20 Foundry Runner", "BH Foundry Runner", "SS Foundry Runner") and render worker | the factory itself; on Mothership as the console user (ROCm needs the desktop session) |
| standing (15 min) | BigHonker, Softserve | "BH Health Daemon", "SS Foundry Health" | the factory's health guard (the nvidia shape) |
| every 1 min + at logon | Mothership | "P20 Stall Rescue" (the console user) | the amd shape's health guard: rescues a stalled training run |
| standing (15 min) | Mothership | "HAI Stall Recorder", "UPS Power Logger" (SYSTEM) | records a stalled model-server runner; the whole box's power from the UPS |
| every 15 min + at startup | BigHonker | "BH GPU Power Limit" (SYSTEM) | `nvidia-smi -pl 180`, set again because a driver reset clears it |
| at startup | Softserve | "SS GPU Clock Lock" (SYSTEM) | `ss_clocklock.ps1`: locks the 3070 Ti's core clock at a level the card was measured to survive under load (adapted from BigHonker's `bh_clocklock.ps1`) |
| standing (15 min) | Softserve | "Ollama Serve" (SYSTEM) | the model server (on Mothership the AMD bundle's, as the console user, at logon) |
| on demand (the stager) | Mothership, BigHonker | "HAI WU Stage" (SYSTEM) | registered and started by `update_stage.py` at each run; no trigger of its own |
| every 15 min + at startup | Softserve | "HAI P34 menu refresh", "HAI P34 menu fill" | the priced menu's refresh and its top-up orders |
| every 15 min + at startup | Softserve | "Tablet WiFi Watchdog Re-armer" | re-arms each tablet's WiFi watchdog |
| every 10 min + at startup | Softserve | "Fellowship Battery Governor" | charges each tablet between its limits |
| every 15 min / every 1 min | Softserve | "PlexMonitor", "PlexWatchdog" | the media server's monitor and its watchdog |
| every 2 min | rigby | `rigby-card-keeper.timer`, `rigby-model-warmer.timer` | Tolkien's card keeper and the tablets' model warmer |
| standing | rigby | `fellowship-bridge.service`, `fellowship-models.service` | the tablets' USB bridge and their model store |
| every 5 min / every 15 min | SundaeStackarr | `uhura-desk.timer`, `uhura-publish.timer` | the door's desk and its publishing |
| 5:30am Sunday | SundaeStackarr | `hai-code-backup.timer` | the lab's code repos to the private backup repo ([[RUNBOOK-Backup-and-Recovery]]) |
| every 30 min | Home Assistant | the price sensor's `command_line` (`/config/hourly_price_feed.py`) | the hourly electricity price HAI's energy weighing reads |

### Health Check Discord Notification

Sent to the server Discord every morning at 4:30am. Reports:
- All stack containers up
- Pi-hole DNS working and blocking
- VPN connected
- qBittorrent reachable
- Sonarr, Radarr, Lidarr health (no errors)
- JukeBot running with track count
- RAM, disk, busiest containers
- **Avery DNS** status (from DO's daily scp of `/home/<user>/.avery_status`)
- **Lab updates** (#38, since 2026-11-03): `/usr/local/bin/lab_updates_field.py` (source `E:\SSDShared\Lab\bin\`).
  - It reads Softserve's 3:00 report over `/mnt/documentation`, ordered: what got INSTALLED or changed on a box since
    last night (⚠ lines), what is newly WAITING, new UPSTREAM engine releases, then a per-box totals line.
  - It refuses a report without its `asked`/`END` markers or older than 30 h: it says "No fresh lab update report",
    never quiet.
  - The value goes to the embed through an environment variable, not pasted into the script's code.
  - Backup of the pre-change script: `stack-health-check.sh.bak-20261103-labupd`.

Script: `/usr/local/bin/stack-health-check.sh`

### Watchtower

Automatically updates all Docker images nightly at 4am Central except JukeBot (local build — excluded via `com.centurylinklabs.watchtower.enable=false` label).
Container: `watchtower` in docker-compose.yml. `TZ=America/Chicago` must be set or the schedule runs in UTC (4am UTC = 11pm Central).

---
