# What the lab runs for HAI, machine by machine: every running piece, its source, and whether it is in git and in the v0 build

**How this was read (2026-11-19 evening):** every running process with its command line on Softserve, Mothership,
BigHonker, Sundae and rigby; every scheduled task outside `\Microsoft\` (through a full-token ssh session: a filtered token
does not see SYSTEM tasks); every custom systemd unit and crontab; every container; and the library's own records
(`Runbook\RUNBOOK-Automated-Maintenance-Schedule.md`, `Reference\Lab-Update-Inventory-38.md` § "The lab's own factory
engines"). Vendor software (drivers, updaters, RGB, UPS vendors' agents) is left out; it is not HAI.

## Status legend

- **ships**: in git, in the v0 build.
- **in git, left out**: committed, but the build excludes it.
- **NOT IN GIT**: runs in the lab with no repository at all, so no build can carry it until it is committed.

## The lab engine (every factory): in git since 2026-11-19, `E:\SSDShared\lab-engine`, ships as `engine/`

The engine every factory serves models with: ollama 0.40.5 built on upstream llama.cpp at its pin, plus ollama's
compatibility layer, plus the lab's patches. The lossless resume (a stopped or restarted run continues token for token)
lives in these patches. Until 2026-11-19 the patches sat in `Claude Home\research\split-inference` and rigby's build
scripts only on rigby, in no repo; both moved into one repo (`lab-engine\README.md` lists every file).

| Piece | Where it is | Status |
|---|---|---|
| The engine patches: #0 `slot_pending_token`, #3 `slot_resume`, #2 `slot_rng`, #4 `slot_continue` (lossless resume), #5 `vk_fa_dead_cells` (Vulkan), `cuda_graphs_pre_volta` (rigby's card), the two Strata server patches, and the hot-store, profiler and async-RPC patches | `lab-engine\patches` (the pipeline's `ENGINE_PATCHES_DIR`, its default) | ships |
| The compatibility layer and builds: `apply_ollama_compat.py`, `build_cuda114.sh` (rigby's pipeline build, `<short>:<engine folder>`), `install_cuda13.ps1`, `make_revision_tree.py`, `ollama_slotpath.py`, the proof orders (`submit_*.py`), `diag\` | `lab-engine` | ships |
| rigby's own builds: the hot store (a build an order can still ask for), the async-RPC builds incl. the tablets' Android arm64 build, the profiler, the pre-pipeline slot builds | `lab-engine\rigby` (pulled from rigby's `~/llama-build-rpcasync`, which keeps the patch copies they read: hash-equal to `patches\`, 11 of 11, 2026-11-19) | ships |
| #36's analysis tools | `lab-engine\research\split-inference` | ships |
| The lab's Strata server (Flash-Next): upstream `serve/server.py` + the two Strata patches | in force as `workspace\flashnext\strata_server_lab.py` (mothership-build; Mothership's `F:\Strata045` runs those bytes); replaying the two patches on upstream gives the lab tree's server byte for byte | ships (`foundry/`); the lab tree `lab-engine\strata-0.1.45-lab` is derived and git-ignored, its two tests are `lab-engine\strata\serve` |
| The pipeline that rebuilds and deploys it (`engine_pipeline.py`) and its door (`fleet_deploy --engine`) | `Lab\bin`, `mothership-build` | ships |
| Upstream sources (`llama.cpp-*`, `ollama-*-src`, `strata-*`) | `lab-engine\` (git-ignored) | not lab work: fetched from upstream at the pin, never redistributed |

**The pipeline's own state (2026-11-19):** HOLD since 2026-11-18 05:30, because `slot_resume_patch.py` does not apply
to llama.cpp `6c01da6` (ollama 0.40.16's pin): a rebase, which is a person's (`python Lab\bin\engine_pipeline.py status`).
The fleet serves `620d222-r5+compat` on all four factories meanwhile.

## The four factories: the same design, the same guards (read live 2026-11-19 about 14:30 CT)

The fleet is four factories: Mothership, BigHonker and Softserve run the GPU factory from one source (`mothership-build`,
deployed by `fleet_deploy`, all in sync by `fleet_deploy --check`); Tolkien on rigby is the fourth, from `tolkien-build`
(deployed by `fleet_deploy_tolkien`; it shares the placement brain, the answer grader, the chat window and the load
reporter with the others by build context). The health daemon has two variants of one design on the GPU factories
(`Runbook\RUNBOOK-Foundry-and-Rescue-Daemons.md`); Tolkien's guards are its own watchdog and card keeper.

| What runs | Mothership | BigHonker | Softserve | Tolkien (rigby) | Source | Status |
|---|---|---|---|---|---|---|
| The order runner | `p20_foundry.py --queue` | same | same | `tablet_runner.py` (container `tablet-foundry`) | `mothership-build`; `tolkien-build` | ships |
| The render or work lane | `p20_render_worker.py` | same | (no render lane) | the tablets, through the runner | `mothership-build`; `tolkien-build` | ships |
| The health guard | stall rescue `p20_stall_rescue.py` (task "P20 Stall Rescue") | `p20_health_daemon.py` (task "BH Health Daemon") | `p20_health_daemon.py` (task "SS Foundry Health") | `tablet_watch.py` (container `tablet-watchdog`) and the card keeper `rigby_card_keeper.py` (systemd) | `mothership-build`; `tolkien-build` | ships |
| The stall recorder | `hai_stall_recorder.py` | | | | `mothership-build` | ships |
| The GPU health and load report | `gpu_reporter.py` (task "HAI GPU Health Reporter") | same | same | `tolkien_telemetry.py` (container `tolkien-telemetry`, the GPU boxes' own load reporter inside) | `Lab\bin`; `tolkien-build` | ships |
| The model warmer | | | | `rigby_model_warmer.py` (systemd) | `tolkien-build` | ships |
| The update stager's on-box half | `wu_stage_box.ps1` (task "HAI WU Stage") | same | no such task | the Linux stager from Softserve (`update_stage.py --linux-nightly`) | `Lab\bin` | ships |
| The GPU telemetry logger (PowerShell on Windows; per the runbook, not read live: the process read left PowerShell out) | AMD logger | NVIDIA logger | NVIDIA logger | inside `tolkien_telemetry.py` | `Lab\bin`, `mothership-build`; `tolkien-build` | ships |
| The UPS power logger | yes | | | | `mothership-build\hostscripts` | ships |
| The lab engine | `Ollama-0.40.5-lab620d222-r5` | same | same | `/llama/cuda` (the lab build for CUDA 11.4) | `lab-engine` (see "The lab engine") | ships (`engine/`) |
| **How each box starts the factory today** (the launchers its scheduled tasks start) | 5: `etc\run_foundry_queue.cmd`, `etc\headless\run_foundry_queue.vbs`, `p20_render_worker.cmd`/`.vbs`, `p20_stall_rescue.vbs` (the folder's 9 others start nothing; the person-image ones stay home) | 3: `bh_runner.cmd`, `bh_worker.cmd`, `bh_health.cmd` | 4: `softserve_runner.cmd`, `softserve_health.cmd`, `headless\ss_foundry_runner.vbs`, `ss_foundry_health.vbs` | the compose file `/opt/arrstack/docker-compose.yml` (engine and model folder `${ENGINE_HOME}`, set in that folder's `.env`); its systemd units come from the installers in `tolkien-build/host` and `Lab/fellowship/rigby` | `Lab\hosts\<box>\launchers`, `Lab\hosts\rigby\docker-compose.yml` | ships (2026-11-21). Each launcher's working lines are byte-identical to what ran before; `fleet_deploy` carries them (`FLEET_ELSEWHERE` names each one's folder on its box) |
| Each box's settings and credentials | `etc\lab.env`; `etc\dm_relay.env`, `etc\civitai.env` | `etc\lab.env`; `etc\dm_relay.env` | `etc\lab.env`; `etc\dm_relay.env` | `/opt/arrstack/tolkien.env` | on each box | settings: an example belongs in the build (`examples/tablet-head/tolkien.env.example` is Tolkien's); credentials stay home |
| **The installer** (`setup_factory.py` + `factory_role.json`, since 2026-11-17; the shapes since 2026-11-20; since 2026-11-21 it says what a device is: `--role factory`, `head`, `head+factory` or `tablet-head`) | | | | | `mothership-build` | ships. One manifest: the code, the folders, the settings, and one launcher per service in `<root>\etc\services\`, registered as a scheduled task on Windows and a systemd unit on Linux; a service whose setting is unset, whose shapes leave the machine's out or whose operating system is another is skipped. `--shape nvidia`: the order taker, the health daemon, the two GPU reporters, the NVIDIA telemetry logger, all as SYSTEM. `--shape amd`: the stall rescue (`FOUNDRY_AI_TOOLKIT`) and the stall recorder in place of the health daemon, the AMD telemetry logger, and the GPU work (order taker, render worker, stall rescue) run as the logged-on user from logon (`--user`; ROCm on Windows needs the desktop session). On a setting: the render worker (`FOUNDRY_COMFY_LAUNCH`), the UPS power logger (`FOUNDRY_UPS_LOGGER`), the card's power cap (`FOUNDRY_GPU_POWER_LIMIT_W`). Named, not installed: the update stage (the stager registers it at each run) and the tablet head's shape (Tolkien's containers and `install_rigby_host_units.sh`). The live boxes' tasks predate it and keep their own names (`P20 Stall Rescue`, `BH Health Daemon`, ...). |

**Box-specific, beyond the shared factory:**

| What runs | Where | Source | Status |
|---|---|---|---|
| Flash-Next's server: llama-swap v265 with `workspace\flashnext\llama-swap.yaml` | Mothership | `mothership-build\workspace\flashnext\llama-swap.yaml` | ships as `foundry/workspace/flashnext/llama-swap.example.yaml`: the live config line for line, its box values (python, foundry root, serving plan, allowed hosts) as placeholders, beside its launcher (`llama_swap_serve.cmd`/`.vbs`) and task installer (`install_llama_swap_task.ps1`); the live copy stays the box's (it holds the box's address) |
| ComfyUI for renders (`F:\comfyui-rocm`) with its launcher `comfy_launch.bat` and its custom nodes | Mothership | `mothership-build`: `comfy_launch.bat`, `comfy\custom_nodes` (`p20_cond_cache`, `p20_reference_memory`), `comfy\extra_model_paths.yaml` | ships (the live nodes are hand-placed copies that predate the repo's cleanup, same behaviour; no deploy door carries ComfyUI nodes yet) |
| ComfyUI (klein) `F:\comfy-bench` with its launcher `run_comfy_bench.bat` and the lab-made node `p20_cond_cache` | BigHonker | `mothership-build\comfy` (`run_comfy_bench.bat`, started by the tasks "P20 Foundry ComfyUI" and "P20 Worker ComfyUI"; `p20_cond_cache`, the same bytes as Mothership's) | ships |
| The FLUX.2 trainer `F:\ai-toolkit` (`FOUNDRY_AI_TOOLKIT`), lab-patched: the resume and start-up fixes, `[P20-INSTR]` (SAVE_NOW / STOP_NOW, step timing), `[P20-BUF]`; and the lab scripts beside it, two of them live (`p20_rescue_burst.py`, the stall rescue's payload; `p20_apply_instrumentation.py`, which wrote the marker the factory checks) | Mothership | `mothership-build\aitk`: `ai-toolkit-lab.patch` against the ROCm fork at `9aeb17f` (6 files), `scripts\` | ships (the patch reproduces the cleaned tree byte for byte; swapped back to the literal paths it is the same program as what trains) |
| The Krea 2 trainer `D:\ai-toolkit-krea2` (`FOUNDRY_AITK_KREA2`): byte-identical resume, the hard exit, the deferred text encoder, `[P20-INSTR]`; its installer `D:\krea2-train\setup` | Mothership | `mothership-build\aitk\krea2`: `ai-toolkit-krea2-lab.patch` against `ostris/ai-toolkit` `73ebae1` (4 files), `setup\` (installer, four appliers, the hard-exit test) | ships |
| GPU power limit (`nvidia-smi -pl 180`, task "BH GPU Power Limit"); the clock lock is retired (since 2026-10-16, boot task disabled; the health daemon's clock duty is `FOUNDRY_TARGET_CLOCK`, 0 = off) | BigHonker | a task command | box configuration, recorded in `Runbook\RUNBOOK-Foundry-and-Rescue-Daemons.md` |
| Glances (telemetry the head reads) | the three Windows factories | third-party | its setup is in `Runbook\RUNBOOK-GPU-Host-Power-and-Glances.md` |

## Softserve: what it runs besides the factory (Plex, the lab's Windows home)

| What runs | How it starts | Source | Status |
|---|---|---|---|
| The menu's refresh and build (`menu_refresh.py`, `build_menu.py`) | task | `hard-bench` | ships (with `wall_tail.py` and `box_models.json`, which the wall and the filler read, since 2026-11-20) |
| The lab update system: notify, the stagers, the engine pipeline | tasks "HAI Lab Update …" | `Lab\bin` | ships |
| The Fellowship battery governor and WiFi-watchdog re-armer | tasks | `Lab\fellowship` | ships |
| Uhura's transcript publisher | task "Uhura Transcripts" | `uhura-build\deploy` | ships |
| Kirk's off-box watchdog (`kirk_watchdog.py`, `kirk_watchdog_tick.ps1`, `install_kirk_watchdog.ps1`) | task "Kirk Change Watchdog" (runs the tools' copy since 2026-11-19) | `Lab\bin` | ships (its key and state stay in `C:\ProgramData\kirk-watchdog`; its reports now reach the admin, `Runbook\RUNBOOK-Kirk-Change-Watchdog.md`) |
| The household bill download (`utility_fetch.py`) | task "the utility Daily Fetch" | `C:\ProgramData\utilityLogin\` only | **NOT IN GIT**; HAI does not read what it makes (it feeds the household's bill and cost sensors in Home Assistant; HAI's energy weighing reads only the price sensor, see "Home Assistant" below) Skips v0 (the owner, 2026-11-21: "I'm not sure house energy scripts are working so they can skip v0"). |
| The GPU telemetry logger, the Plex monitor and watchdog, the dataset agent | tasks | `Lab\bin`, `softserve-services`, `mothership-build` | ship (the dataset agent's archival extension is the person-image area and stays home) |
| The game-server agent (`gameops_agent.py`) | task | `C:\gameops\` | stays home: it serves the onboarding app |

## Sundae (the head)

| What runs | How it starts | Source | Status |
|---|---|---|---|
| Kirk (the head), the coding runner, the host-ops broker, wake-on-LAN, search, Uhura and its liaison and network guard, the Discord relay | containers | `orchestrator-build`, `uhura-build`, `hawkadminbot` | ship (the stack's compose is `examples/full-stack`) |
| The head's sweeps (`hai-claim-sweep`, `hai-photo-archive`) and their installer | systemd timers | `Lab\bin\head-timers` | ships |
| Uhura's library publisher and desk sync | systemd timers | `uhura-build\deploy` | ships |
| The weekly code backup (`hai_code_backup.sh`) | systemd timer | `Lab\bin` | ships: `tools/hai_code_backup.sh`, its timer installer `tools/install_hai_code_backup.sh`, its settings `etc/hai-code-backup.env.example` |
| The lab-updates field of the morning health check (`/usr/local/bin/lab_updates_field.py`) | inside `stack-health-check.sh` | `Lab\bin\lab_updates_field.py` | ships; the installed copy equals git since 2026-11-20 (the health check exports `LAB_UPDATES_REPORT`) |
| The household energy accounting: the bill import, today's accrual, the monthly temperature-model refit (`utility_import.py`, `today_accrue.py`, `fit_temp_model.py`) | cron | `/opt/arrstack/utility/` only | **NOT IN GIT**; HAI does not read what it makes (cost sensors for the household's dashboards, `Reference\HomeAssistant-Energy.md` § 3) Skips v0 (the owner, 2026-11-21: "I'm not sure house energy scripts are working so they can skip v0"). |
| Tolkien's image builds (built here, run on rigby) | `fleet_deploy_tolkien` | `tolkien-build` | ships |

## rigby: what it runs besides Tolkien's factory (the tablet head)

| What runs | How it starts | Source | Status |
|---|---|---|---|
| The USB reach probe (forced command of the watchdog's key) | ssh | `Lab\fellowship\usb_reach_probe.py` + installer | ships |
| The bridge daemon (`fellowship-bridge-daemon.sh`: keeps every tablet's USB interface in `br0`) | `fellowship-bridge.service` | `Lab\fellowship\rigby` (installed by `install_rigby_host_units.sh`, 2026-11-19) | ships |
| The model server for the tablets (`python3 -m http.server 8078` over `/mnt/fellowship/models`) | `fellowship-models.service` | `Lab\fellowship\rigby` (installed by `install_rigby_host_units.sh`, 2026-11-19) (the unit is a template) | ships |
| The share mounter (`rigby-mount-shares.sh`, which Docker waits for through its drop-in `wait-for-shares.conf`) | `rigby-shares.service` | `Lab\fellowship\rigby` (installed by `install_rigby_host_units.sh`, 2026-11-19) | ships |
| The tablet head's llama.cpp builds (`/llama/cuda` the lab engine, `/llama/bin`, `/llama/vulkan`, `/llama/arm64` the tablets' rpc server, `/llama/next/*` rpcasync, `/llama/hotstore`) | Tolkien's mounts | build trees on rigby, made by `lab-engine\build_cuda114.sh` (the pipeline's) and `lab-engine\rigby\*.sh` (hot store, async RPC incl. arm64, profiler) from upstream + `lab-engine\patches` | the scripts and patches ship (`engine/`); the binaries are build outputs |

## Home Assistant (the house's own box; HAI reads it)

| What runs | How it starts | Source | Status |
|---|---|---|---|
| **The electricity price feed** (`/config/hourly_price_feed.py`): the hourly price HAI's energy weighing reads (`ENERGY_PSP_ENTITY`, `sensor.utility_psp_data`) | Home Assistant `command_line` sensor, every 30 minutes; the utility's addresses are its arguments in `configuration.yaml` | `Lab\house\home-assistant` | ship (`house/home-assistant`, since 2026-11-20; `Reference\HomeAssistant-Energy.md` § 3.2) |
| The house's devices, areas and automations HAI's house tools act on | Home Assistant | the user's own | not HAI's: the user brings their own Home Assistant |

(The local-LLM assist in Home Assistant is a separate, earlier project: `Reference\LocalLLM-Assist-Project.md`.)

## The tablets

Tolkien's on-tablet agent, the WiFi watchdog, the boot scripts (sshd, the agent's supervisor, the keepalive): `tolkien-build`
and `Lab\fellowship\payload` — ship. The whole `Lab\fellowship` folder ships (the build's `LAB_TREES`, since
2026-11-20): the decoder and detector sources (`ncnn-detect`, `ndk`), the cluster benchmarks and the one-off flash
scripts, all reading the tablets, their serials, key and adb from the lab's settings (`fellowship/settings.sh`,
`roster.py`). Left out: the benchmarks' results, the re-armer's state, and the vendored AVB signing tool (Android's own).

**The tablets' payloads** (the programs Tolkien copies to a tablet for an order, `python <payload> items.txt out.json`): the share `E:\SSDShared\tabfoundry-jobs\payloads`, in git since 2026-11-20 (its own repo, verbatim; `a1/` is the A1 payload's deployed copy, whose source ships in the menu). They ship as `tablet-payloads`: the newest generation of each. A payload is never rewritten (`task_versions`), so the release made a clean next generation of each benchmark payload (`hard-bench\payload_generation.py`: comments cleaned, no stage-server default, the versioned tasks under their new ids; checked pair by pair) and `PAYLOAD_GENERATIONS` routes new orders to them; the older generations stay home with the results and manifest pins that point at them (the build reads the list from `task_versions`). The camera payloads (`map_detect`, `map_motion`) were cleaned in place. Tolkien hands every payload the stage server's address (`TABLET_STAGE_URL` in rigby's `tolkien.env`, as `FELLOW_STAGE_URL`; a tablet's own value wins). Left out: `previous/` (older copies), the importance-matrix and KL-divergence inputs (run data).

## The A1 agentic benchmark harness (all four factories)

`hard-bench\a1` (the harness, the order runner, the delegate, the stop rule) and `hard-bench\inspect_lab` (Inspect AI's lab provider, the rigby payload, the Fellowship delegator), plus `map_brief.py` (the brief payload) and `cli_answer.py`: in git since 2026-10-04 and in the build since 2026-11-20 (they had been in no component). Deployed by `fleet_deploy --a1` (the three GPU boxes) and `--rigby-a1` (Tolkien's share). Every lab value is a setting: the head and the share through the foundry's reader (`HAI_URL`, `SSD_SHARED_UNC`, `FLEET_HOSTS`), the tablet desk's share through Tolkien's own `INBOX` / `TABJOBS_SEEN`. Left out: the vendored task set `a1\polyglot-benchmark` (Aider-AI's, cloned by whoever runs A1).

## The cameras lane and the evals

- `Lab\cameras` (the cameras lane, paused, not retired) ships whole except its captured data (NVR config dumps,
  encoder snapshots, motion-event logs, site data, measurement results: every `.json`). It reads the NVR's address
  and login (`NVR_HOST`, `CAMERA_CRED_DOC`, or `NVR_USER` / `NVR_PASSWORD`), the cameras site (`CAMERAS_SITE_URL`,
  `NVR_SITE_URL`) and the tablets from the lab's settings, through `cam_settings.py`, `nvr_settings.py` and
  `hwdec-test/settings.sh`. The decoder's per-tablet config dumps (`hwdec-test/hwdec.*.json`) carry the login and
  stay untracked.
- `Lab\hai-evals` (the independence regression evals and the library-triage calibration) ships whole.

## Each Windows factory's host scripts (`%ProgramData%\HAI`), found 2026-11-20

| Box | Scripts | In git | Status |
|---|---|---|---|
| Mothership | `start-ollama.ps1` (the AMD shape's model server), `keep-awake.ps1`, `textinput-watchdog.ps1`, `gpu-free.ps1`, `operator\mothership_checklist.ps1`, `operator\spawn_census.ps1`, `tools\shot.ps1`, `tools\zt_shot.ps1`, `frontier_mode.py`, `ups_power_logger.py` + its installer, and the one-offs (`get_flash_next_ollama.py` + `.cmd`, `finalize-ollama-move.ps1`, `phase5-architecture-review.ps1`) | `mothership-build\hostscripts` | **ship**: cleaned 2026-11-20 (each box value from its `lab.env`: `OLLAMA_EXE`, `OLLAMA_SERVE_ENV`, `OLLAMA_SERVE_LOG/OUT`, `HAI_URL`, `OWNER_USER`, `OLLAMA_PORT`, `FLASHNEXT_OLLAMA_ROOT`), deployed by `fleet_deploy` (the live copies equalled git before) |
| BigHonker | `start-ollama-system.ps1` (the shared NVIDIA starter), `keep-awake.ps1` (its own one-check design), `register-tasks.ps1`, `install_bh_power_limit.ps1` (its watts from `FOUNDRY_GPU_POWER_LIMIT_W`), `bh_upgrade.ps1` (a one-off from before the shared starter, kept as the record) | `Lab\hosts\bighonker` (the starter in `mothership-build\hostscripts`) | **ship**, deployed 2026-11-20. The live "BH GPU Power Limit" task is the factory shape's `gpu-power-limit` service in its older form (the value is now the setting); moving the box onto `setup_factory`'s service is an update after v0 |
| Softserve | `start-ollama-system.ps1` (task "Ollama Serve"; the shared NVIDIA starter, **deployed 2026-11-20**), `ss_clocklock.ps1` (task "SS GPU Clock Lock") | `mothership-build\hostscripts` (starter), `Lab\hosts\softserve` | the clock-lock task duplicates the health daemon, which holds the same lock through `clock_policy` (`FOUNDRY_TARGET_CLOCK`, floor, ceiling; unset = untouched): its retirement waits on the owner (the tool layer refused removing the task) |

Not taken, on purpose: the update stager's `wu-stage\` files (it writes them at each run from `Lab\bin`), the stall
recorder's evidence folders (data), ZenTimings (a vendor tool), the exported "Ollama Serve" task definition (this box's
paths and account) and the two webhook files (secrets). The model-server starters are one script per shape
(NVIDIA `start-ollama-system.ps1`, AMD `start-ollama.ps1`) with each box's values in its own `lab.env`, deployed
through `fleet_deploy`'s out-of-tree table; on each box `-Show` printed exactly the old script's environment and
command. The two `keep-awake.ps1` are different designs (Mothership's checks five kinds of work, BigHonker's one):
each is cleaned as it is for v0; making them one is an update after v0. Found the same night and fixed: the deployed `gpu_reporter.py` and
`lab_settings.py` (all three boxes) and Mothership's two task installers were older than git, and no door carried
them; `fleet_deploy` now does (`c69cad0`), and every card's next report reached the head with the new code.

## Wrong in the build (2026-11-19)

- The stall rescue runs from the FLUX.2 trainer's folder and puts that folder first on its import path, so it imports the `foundry_lock.py` there, not the factory's; `fleet_deploy` updates only the factory's. Today the two are the same program (they differ in comments), so the guard is right; a change to `foundry_lock` would not reach it. Found 2026-11-19, an update for after the release.
