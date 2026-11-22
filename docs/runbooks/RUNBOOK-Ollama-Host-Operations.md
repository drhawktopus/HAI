# RUNBOOK — Ollama & Local LLM Cluster Host Operations



### Home AI — Mothership cold-wake settle delay

When Mothership is woken from sleep (WoL) for a heavyweight/27B request, `_ensure_lead()` waits
`PC1_WAKE_SETTLE` seconds (default **5**, env-overridable; `MOTHERSHIP_WAKE_SETTLE` until 2026-11-16) AFTER the host becomes reachable and
BEFORE the model load. This separates the boot/PSU spin-up from the GPU model-load surge so the shared
UPS doesn't see one stacked inrush. Only applies on a real cold wake (no delay when Mothership is already
awake). Set `PC1_WAKE_SETTLE=0` to disable. NOTE: Mothership's UPS telemetry in HA
(`sensor.mothership_ups_*`) goes `unavailable`/freezes (shows last state, often "On Battery") while
Mothership sleeps because the monitoring software runs on Mothership itself — that's expected, not a UPS
fault.

### Home AI — Ollama autostart per host + Mothership gotchas

All three GPU hosts run Ollama via a persistent **"Ollama Serve" scheduled task** so it survives reboot/wake
(BigHonker = `/ru SYSTEM /sc onstart`; Softserve = SYSTEM, boot trigger + 15-min self-restart repetition,
see §"Softserve `Ollama Serve` restarts itself" below; **Mothership = AtLogon, runs as `<user>` interactively,
RunLevel Highest** — ROCm needs the user session). Full
setup in [[02-PHASE-C-BUILD]] §1c.

**To (re)start Ollama on a host remotely:** trigger the task — `Start-ScheduledTask "Ollama Serve"` (over SSH
for Mothership). An SSH-launched `ollama serve` / `ollama app.exe` does **not** persist past the SSH session.

**Self-heal:** the orchestrator does this automatically for Mothership.
`mothership.restart_ollama()` SSHes in and triggers the `Ollama Serve` task (sent as `-EncodedCommand` so
the quoting survives ssh→cmd→powershell); `_ensure_lead()` calls it inside the wake loop, so when the
box is **up but Ollama is down** (the flaky AtLogon `app.exe`/0x1 case) it recovers without a human. It's a
no-op (returns False) while the box is still asleep, so the WOL handles the cold-wake case first. Verified
2026-06-26: a cold `_ensure_lead()` woke the box, triggered the task, and was serving in ~22s.

## Local LLM Cluster (Home AI — BUILT + deployed)

### Hosts & models (all Ollama, **native — NOT WSL**)

| Host | GPU | Role | Model | num_ctx | Notes |
|------|-----|------|-------|---------|-------|
| **BigHonker** (<bighonker-address>) | RTX 3070 8GB | 8 GB tier home on the menu (which models: the rows in `policy.json`, never a list here); an embed box on the menu's embed family; the brain cascade's first box (Quick is weighed from the menu like Auto since 2026-10-25 — checklist D2a) | `qwen3.5:9b` | **19,968**, the desktop-aware fit (`campaigns\fit-20261019-desktop`: 594 MiB baseline; 20,480 first spills). `menu_fill.TESTED_CTX` → `menu_policy` → `policy.json contexts` supplies HAI's window; `triage_local` agrees, pinned by `test_ctx_stores_agree.py`. The temporary 7,168 Compose override is removed. A measured fit is not a reservation: the desk's fit-before-load check (`model_fit.check`) runs on every load against the card's live report, and since 2026-10-25 a chat window opens at the largest MEASURED point that fits beside the desktop's current use (`model_fit.fit_ctx` via `scheduler.window_ctx`; a logged-in session, even locked, keeps the compositor at ~1 GB) — the full window when it fits, never a refusal while a smaller measured point fits. | Shared desktop: sometimes the owner or Avery is logged in, sometimes nobody. `BRAIN_MODEL` / an explicit `BRAIN_NUM_CTX` remain operator overrides; otherwise policy.json is authoritative. Ollama server runs **q8_0 KV cache + flash attention** (`OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_FLASH_ATTENTION=1`). Headless-safe: SYSTEM `Ollama Serve` task + HAI keep-awake watcher. |
| **Softserve** (<softserve-address>) | RTX 3070 Ti 8GB | 8 GB tier home with Plex first; McCoy's host (tev1 kept loaded — the one exception; two-model card since 10-23); embed + chat rows placed by the weighing | `qwen3:8b` | 17408 (tested max, since 2026-09-28) | Shares GPU with Plex. Normal/recovery routing must prefer Mothership before Softserve; Quick mode also prefers Mothership small-model fallback before Softserve. |
| **Mothership** (<mothership-address>) | **AMD** RX 7900 XTX 24GB | the lead tier (qwen3.8:27b; Flash-Next for agentic / long context); the `force_27b` pin for every user; overflow for small rows at a swap cost | `qwen3.8:27b` (MTP on by default) | **122880** (tested max 123,904; = Mothership's `triage_local` row, since 2026-09-29) | **Vulkan backend since 2026-10-02** (`OLLAMA_LLM_LIBRARY=vulkan` + `LLAMA_ARG_FIT_TARGET=512` in `C:\ProgramData\HAI\start-ollama.ps1`; 82.4 vs 53.8 tok/s on ROCm, whole model on the card; rollback = `start-ollama.ps1.bak-20261002-pre-vulkan` + rerun `Ollama Serve` — [[34-A-OLLAMA-UPGRADE-GATE]]). **One model, one slot since 2026-10-04 00:12** (the owner: *"we want single model at a time per card"*): `OLLAMA_MAX_LOADED_MODELS=1` + `OLLAMA_NUM_PARALLEL=1` in the same launcher, read back from serve.log's own server config — Mothership had neither (default ceiling 2: ollama opened 1 OR 2 slots per load on its own, an uncontrolled variable in its 10-03 fit and speed rows), BigHonker and Softserve already ran both. Rollback = the `start-ollama.ps1.bak-20261003-235021-pre-maxloaded` / `.bak-20261004-001246-pre-numparallel` copies. ollama 0.34.0 from the AMD AI Bundle tree; woken on demand (WoL). keep_alive 5m; 30-min OS ready-window + keep-awake watcher. Gaming-busy guard before any cold load. ctx authority: `GET /health` → `heavy_num_ctx`. See routing note below. |

⚠️ Model defaults are huge (256K-class) and overflow to CPU — the orchestrator MUST set
`options.num_ctx`.

### Ollama specifics
- **ALL THREE GPU hosts: ollama 0.40.5 since 2026-10-15 23:18–23:19** (the owner: "We want all of them on the latest
  version"; needed for decision models — tev1 requires ≥ 0.40.4; changelog 0.40.3–0.40.5: native decision models, a
  llama.cpp update twice, Nemotron 3.5 support). Each a VERSIONED tree `…\Ollama-0.40.5\` beside the 0.40.2 tree
  (= rollback: restore the launcher backup `*.bak-20261015-23*-pre0.40.5` and rerun `Ollama Serve`). Post-upgrade
  re-check orders: `speedcheck-{mo,bi,so}-*.20261015-232216` (`hard-bench\submit_speedcheck.py --arms=...`).
- **NVIDIA hosts** (BigHonker, Softserve): each as a VERSIONED tree `%LOCALAPPDATA%\Programs\Ollama-<version>\` (upstream `ollama-windows-amd64.zip`, sha256-verified, the parallel-tree method of [[34-A-OLLAMA-UPGRADE-GATE]]; the previous tree stays beside it as rollback). Procedure + traps: §"Ollama upgrade (BigHonker, Softserve, Mothership)" below.
  - **BigHonker is headless-safe** (the owner approved the SYSTEM service/task tradeoff):
    scheduled task **`Ollama Serve`** runs as SYSTEM at startup via `C:\ProgramData\HAI\start-ollama-system.ps1`.
    It sets `OLLAMA_HOST=0.0.0.0:11434`, `OLLAMA_MODELS=C:\Users\<user>\.ollama\models`,
    `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KEEP_ALIVE=5m`,
    `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `LLAMA_ARG_FIT_TARGET=512` (10-15), and since 2026-10-17
    20:18 (the owner OK, #34 B10) **`LLAMA_ARG_THREADS=4`** (4 > 5 > 3 >> 6 on the 6-core i5-9600K with MoE experts in RAM)
    + **`LLAMA_ARG_MOE_CACHE_MIB=1024`** (llama.cpp's expert cache: qwen3.6 35B-A3B 26.5–32.9 tok/s vs 22.9–24.2
    without, varied prompts) — ⚠ **BROKE EVERY NON-MoE MODEL ON BIGHONKER (found 2026-10-19 ~01:10 CT):** a dense model does not get "MoE cache is disabled", it FAILS TO LOAD — `llama_init_from_model: failed to initialize the context: MoE cache requires a MoE model` → HTTP 500 (`qwen3.5:9b` reproduced; `nemotron-3-nano:4b` failed all 20 loads since 10-17 20:18, voiding the #34 B5 reviewer arm). The var is box-wide (every llama-server ollama spawns), so it cannot stay in the launcher: **REMOVED 2026-10-19 01:10:51 CT** (the owner: *"Okay fix it"*; backup `start-ollama-system.ps1.bak-20261019-011051-pre-drop-moecache`; new ollama PIDs 01:10:55/01:11:01; `qwen3.5:9b` and `nemotron-3-nano:4b` 262,144 both HTTP 200, fully resident). The A3B runs WITHOUT the expert cache until a per-model form exists (22.9–24.2 tok/s class) — see the defect log; the A3B's
    batch is its OWN default — `PARAMETER num_batch 2048` in `qwen3.6:35b-a3b-ud-q3kxl-mtp` (re-created FROM itself
    20:47, same blob; manifest backup `C:\ProgramData\HAI\manifest-backup-qwen3.6-35b-a3b-ud-q3kxl-mtp.20261017-204738`),
    so the menu, HAI and any caller get it; env cannot do this one (ollama passes -b/-ub on its command line). ⚠ 0.40
    keeps manifests in `models\manifests-v2\ollama.com\library\…`, not the old `manifests\registry.ollama.ai\…`.
    Proven in force from serve.log: `n_threads = 4`, `MoE cache size = 1023.94 MiB`, `n_ubatch = 2048`. Backup
    `start-ollama-system.ps1.bak-20261017-201805-pre-a3b-threads-moecache`. ⚠ The launcher has MIXED line endings
    (CRLF lines + bare-LF lines added by script) — split on `\n`, compare without `\r`. Log: **`C:\ProgramData\HAI\ollama-serve.log`
    (UTF-8, since 2026-09-27 16:01)** — see §"BigHonker ollama serve log location" below. The pre-09-27 log
    is archived as `ollama-system.utf16-until-20260927-160059.log` and is **UTF-16LE** (an ASCII grep finds
    nothing in it). Current verified Ollama version: **0.40.5** (2026-10-15 23:18:59, exe `C:\Users\<user>\AppData\Local\Programs\Ollama-0.40.5\ollama.exe`, launcher backup `start-ollama-system.ps1.bak-20261015-231848-pre0.40.5`; answers over the LAN); previous: 0.40.2 (2026-10-10 09:30, `Ollama-0.40.2\` = rollback; the 0.32.3 tree is still `…\Programs\Ollama\` = rollback, launcher backup `start-ollama-system.ps1.bak-20261010-093001-pre0.40.2`). Inbound reach is the port rule **`Ollama LAN`** (TCP 11434, <lan-subnet>/24 + <tailscale-range>/10), not the old per-program rules — see the upgrade section's firewall trap.
  - **Softserve — ollama config (brought to fleet parity 2026-09-28, the owner-approved):** scheduled task
    **`Ollama Serve`** runs **`C:\Users\<user>\AppData\Local\Programs\Ollama-0.40.5\ollama.exe serve`**
    as **SYSTEM** — ollama **0.40.5** (2026-10-15 23:18:34, upstream zip sha256-verified; launcher backup
    `start-ollama-system.ps1.bak-20261015-231820-pre0.40.5`; the 0.40.2 tree beside it = rollback). The 0.32.3 tree stays at `…\Programs\Ollama-0.32.3\`
    and the 0.30.7 tree at `…\Programs\Ollama\` — **rollback = repoint `$ollama` in the launcher and rerun the task**. (Before 21:25 on 09-28 the live server was an orphaned manual `ollama serve` started as
    Administrator on 09-18, NOT the task.) **Since 2026-10-06 the task runs the launcher
    `C:\ProgramData\HAI\start-ollama-system.ps1`** (BigHonker's, with Softserve's exe path; previous task XML backed up as
    `C:\ProgramData\HAI\ollama-serve-task.bak-20261006-*.xml`), which appends ollama's own output to
    **`C:\ProgramData\HAI\ollama-serve.log`** (UTF-8, same file name and encoding rule as BigHonker). Settings are
    MACHINE-scope environment variables, confirmed in force by the log's own `server config` line, plus ONE explicit
    line in the launcher since 2026-10-10: **`OLLAMA_MODELS=E:\ollama-models`** (the store moved off C: — 16 GB free,
    and 0.40 rewrites every model on first use keeping the original; copied with robocopy, 56 files / 31.78 GB,
    byte totals equal; the old `C:\Users\<user>\.ollama\models` copy is still on C: pending the owner's
    call on the free-space list). The rest: `OLLAMA_HOST=0.0.0.0`, `OLLAMA_KEEP_ALIVE=2m`,
    **`OLLAMA_MAX_LOADED_MODELS=2` since 2026-10-23 10:42 — a DELIBERATE per-card difference, for McCoy ALONE** (the owner
    2026-10-23: *"McCoy is the singular exception, for the record"* — his model is the only one kept loaded; the second
    slot only stops each turn's embed from evicting him, and the embed model still unloads at `OLLAMA_KEEP_ALIVE`; BigHonker and Mothership
    stay at their own values; doctrine: two MODELS on a card is set per card by the memory math, `NUM_PARALLEL` is the
    other knob). Why: McCoy's model (tev1) and each HAI turn's embed (`nomic-embed-text`) evicted each other every
    turn — all 17 live McCoy calls 10-22/23 reloaded tev1 (6.7–10.8 s; the answer itself ~0.1 s). the owner 10-23: *"Let
    softserve run two models at current context size, and make it have to use one at a time if plex's transcode
    usage trends to rising above what can hold both."* The second half is the health daemon's **duty 7**
    ([[RUNBOOK-Foundry-and-Rescue-Daemons]] § "Duty 7"); McCoy's call carries `keep_alive -1` on Softserve only
    (`config.MCCOY_RESIDENT_HOSTS`). Footprints on this card (nvidia-smi, display on the APU so the empty card reads
    0 MiB): tev1 5,338 MiB as ollama launches it (`-c 8192 -np 16`), nomic 485 MiB. Previous value saved in
    `C:\ProgramData\HAI\ollama-env.bak-20261023-104232.txt`; rollback = set it back at machine scope and restart
    `Ollama Serve` (scratchpad `softserve_two_models.py` is the restart, the engine tool's shape).
    `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_CONTEXT_LENGTH=8192`, and since 2026-09-28
    **`OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0`** (what BigHonker and Mothership set in their launch
    scripts — Softserve never had them). Measured: with both changes every model's tested max context equals
    BigHonker's (RUNBOOK §"Fleet VRAM residency map"). ⚠ Keep the three boxes' ollama settings in step — this drift
    halved qwen3:8b's context and pushed two vision models off the card, silently.
  - **Softserve `Ollama Serve` restarts itself — no 72-hour limit (2026-10-02, the owner-approved).** The 09-28 task
    was created with the scheduler's default **72 h execution limit (`PT72H`) and restart count 0**, so the
    scheduler ended `ollama serve` when the limit ran out and nothing brought it back (found down 2026-10-02).
    It now has the lab's standard service-task shape (the `fix_health_daemon_task.ps1` pattern): **no time limit
    (`PT0S`)**, a **boot trigger**, a **15-minute repetition** on a past-dated time trigger, **`IgnoreNew`**
    (a repeat while ollama is up is dropped), and restart-on-failure 3 × 5 min. Proven by a live decoy: ollama
    down at 08:55, started by the repetition at **09:00:01**, answering at 09:00:10, no hand involved.
    **BigHonker and Mothership** already had no time limit and restart-on-failure 3 × 1 min, but have **no
    repetition** (boot / logon trigger only), so a clean exit or exhausted restarts leaves them down until the
    next boot (Mothership is also restarted by `mothership.restart_ollama()`, above).
  - BigHonker also runs SYSTEM task **`HAI Keep-Awake`** at startup: `C:\ProgramData\HAI\keep-awake.ps1` polls
    `localhost:11434/api/ps` and asserts `ES_SYSTEM_REQUIRED` while a model is resident. Hidden
    `UNATTENDSLEEP` AC is **1800s**. Log: `C:\ProgramData\HAI\keep-awake.log`.
  - All three hosts answer ping (BigHonker's `FPS-ICMP4-ERQ-In-V2` rule enabled, local-subnet
    scoped). Still probe the SERVICE (`:22`/`:11434`) for a real liveness answer; ports closed +
    `/llm/bighonker` = `offline` usually just means ASLEEP (UNATTENDSLEEP 1800s), and it WoLs on
    demand.
  - ⚠ **BigHonker's 3070 runs at a 180 W POWER LIMIT (since 2026-10-21 15:12 CT) — do not remove it.** At the
    board's factory 280 W default, dense LLM decode (~240 W drawn) drops the card off the bus within minutes, even on
    the new PSU with two separate PCIe cables; capped at 180 W the same load survived 2 × 15 min at the SAME 66–67
    tok/s (decode is memory-bound). Held by SYSTEM task **`BH GPU Power Limit`** (`nvidia-smi -pl 180` at startup + every
    15 min; the limit is volatile). Check: `nvidia-smi --query-gpu=power.limit --format=csv` → `180.00 W`. Cause, tests,
    installer, rollback: [[BigHonker-3070-DPC-Watchdog-Investigation]] §9i–§9j. The 1500 MHz CLOCK lock stays retired
    (since 2026-10-16: boot task `BH GPU Clock Lock` disabled, the health daemon's clock duty off) — the power cap
    replaces it and leaves full boost for light loads. History of the failure (minutes at factory boost, ~72 min at the
    1500 lock on the old PSU): §0y. Signature:
    `nvlddmkm` event 153 → `nvidia-smi` *"GPU is lost"*, `dwm.exe` crashes seconds later; reboot
    recovers. Since 2026-09-28 a per-second **GPU Telemetry Logger** records clocks, hotspot, fans and
    throttle reasons ([[RUNBOOK-Foundry-and-Rescue-Daemons]]), and a factory hands a GPU order back to
    the desk when its card is lost instead of running it on CPU.
    ⚠ **Lost AT the 180 W cap, 2026-11-16 15:20:04** (event 153): the telemetry logger's last seconds read 179.4 W
    (software power-cap throttle `0x4`), 57 °C core / 72 °C hotspot, 89 % load, 714 MB spilled to shared memory, after
    ~50 min of continuous decode by a HAI coding job on `qwen3.5:9b`. The cap was proven on 2 × 15 min, so it does not
    hold for a long continuous job: the cause is not settled. Reboot pending the owner (his desktop).
  - Starting `ollama serve` directly over SSH does not persist - OpenSSH kills child processes on disconnect;
    use the scheduled task.
- **Mothership (AMD)**: `C:\Users\<user>\AppData\Local\AMD\AI_Bundle\Ollama-0.40.5\ollama.exe`
  — **stock ollama 0.40.5** since 2026-10-15 (0.40.2 from 2026-10-10, its tree kept as rollback) (parallel tree from upstream `ollama-windows-amd64.zip` +
  `ollama-windows-amd64-rocm.zip`, both sha256-checked against the release digests) with BOTH GPU backends,
  `lib\ollama\rocm_v7_1` and `vulkan`. **The backend is ONE launcher line**, `$env:OLLAMA_LLM_LIBRARY = 'vulkan'`
  (or `'rocm_v7_1'`) in `C:\ProgramData\HAI\start-ollama.ps1`, then rerun the `Ollama Serve` task; Vulkan is the
  default (the owner 2026-10-02, faster). **Verified live 2026-10-10:** serve.log `Listening … (version 0.40.2)` +
  `inference compute … library=Vulkan … "AMD Radeon RX 7900 XTX" total="24.0 GiB"`; qwen3.8:27b at `num_ctx`
  122880 `size_vram == size` (17.83 GB), MTP on (`draft-mtp`, n_max 4), flash attention on; the 3.8's two
  CHAT1 cells re-run through the desk read the same as on 0.34.0 (21/21 each, median decode 64.3 vs 65.3 and
  64.3 vs 63.6 tok/s, 0 limit hits). ROCm on the same tree (`library=ROCm compute=gfx1100`) serves the same
  thinking-off cell at 21/21 but slower: median 53.1 tok/s and 210 J per answer vs Vulkan's 64.3 and 191, the
  same direction as 2026-10-02 — so Vulkan stays the default and ROCm is the one-line fallback. Rollback generations, kept intact: `…\AI_Bundle\Ollama-0.34.0\` and
  `…\AI_Bundle\Ollama\` (0.32.0); to roll back below 0.40 restore BOTH `start-ollama.ps1.bak-20261010-pre040`
  AND the model manifests from `E:\Ollama\manifests.bak-20261010-pre040` (0.40 rewrites migrated models'
  manifests; the original blobs stay in the store), then rerun the task. The launcher starts **`ollama.exe serve`
  directly — never the tray `ollama app.exe`**. Serve's log: `C:\Users\<user>\AppData\Local\Ollama\serve.log`.
  Firewall: the port rule `Ollama LAN` (TCP 11434 from <lan-subnet>/24 + <tailscale-range>/10) plus allow rules for the
  0.40.2 exe — see the upgrade section's fourth trap. Execution record: [[34-A-OLLAMA-UPGRADE-GATE]].
  **2026-10-15: now `…\AI_Bundle\Ollama-0.40.5\`** (both zips; backends cuda_v12, cuda_v13, rocm_v7_1, vulkan; serve.log
  `inference compute … library=Vulkan … RX 7900 XTX total=24.0 GiB`; launcher backup
  `start-ollama.ps1.bak-20261015-231923-pre0.40.5`). **The fourth trap hit AGAIN:** Windows auto-created Block rules
  (TCP + UDP) for the 0.40.5 exe, so the LAN timed out while localhost served. Fixed 2026-10-16 ~00:00 with the owner's
  go: `netsh advfirewall firewall set rule name=ollama.exe dir=in program="C:\Users\<user>\AppData\Local\AMD\AI_Bundle\Ollama-0.40.5\ollama.exe" new action=allow`
  ("Updated 2 rule(s)"; `/api/version` → 0.40.5 from Softserve).
  BigHonker and Softserve did NOT hit it (both answer over the LAN on 0.40.5).

  ### Mothership model storage — which drive a model lives on (the owner's rules; read BEFORE placing, copying or moving any model)

  the owner's own words, collected 2026-09-29 after a session moved and duplicated his model stores without asking:
  * **Everything's permanent home is Softserve's HDD array** (`Z:\Archives\Local-Models`, `Z:\Datasets`). Download
    on Softserve (torrent SSD) → copy to the array → *"copy the subsets we need to mothership on the NVME. Then
    mothership makes a copy on to the big ssd. If it's about to train it uses what is on NVME before it would use the
    big SSD."* (2026-07-25)
  * **Big, needy models run from NVMe (F:), not D:** — *"I specifically want to use NVME for these, not the D drive
    since it kinda slow"* (2026-07-15, the Flux 2 models on `F:\ComfyModels`). D: (870 QVO, QLC) is the big-SSD copy /
    staging tier; it reads fine but sustained writes crawl.
  * **Models already placed stay put** — *"Just keep the models where they are, they are in specific drives for speed
    reasons and we have caching set up on them etc"* (2026-07-28). Moving or duplicating a placed model needs the owner's OK.
  * **Ollama's store is `E:\Ollama\Models`** — the owner moved it there (2026-06-28, Codex finished the consolidation;
    `C:\Users\<user>\.ollama\models` is a junction to it). Its free space is finite: do the arithmetic before any pull
    or import, and a model too big for E: is a placement question for the owner, never a reason to move the store.
  * **Copies we can re-derive or re-download are not kept** (2026-09-29): originals live on the array; no stray
    duplicates on Mothership.
  * **Drives on Mothership (measured 2026-09-29):** C: 1 TB (pagefile, fixed 100–120 GB) · D: "Operations" 2 TB
    Samsung 870 QVO (SATA QLC) · E: 500 GB 860 EVO (the ollama store) · F: "NVME Storage" 1 TB PNY CS2241 (big
    models) · **B: "Big Home Drive" 12 TB USB HDD** (docked; work files such as a model's full-precision GGUF master).
    **D:, F: and B: are NTFS-compressed at the root; C: and E: are not** (read back 2026-09-29) — see the next
    section before writing anything big to D:, F: or B:.

  ### FRONTIER MODE — a frontier-size model gets the machine to itself, and everything stopped is restarted after
  the owner, 2026-09-30: *"if I want to use a frontier size model… I will need to be doing nothing else on the computer…
  we can cut extra stuff BUT we are REQUIRED to restart whatever we stopped once the frontier size is done."*
  * **Applies to** frontier-size work: a model larger than card + RAM, run partially resident (Qwen3.8-Flash-Next,
    [[Qwen3.8-Flash-Next-on-Mothership]]), where every free GB of RAM and every CPU thread goes to the model.
  * **WHY it exists — the intent binds, not the block list** (the owner, 2026-10-18): it was made when Mothership was LOW ON
    RAM, so the owner could use the biggest box in the lab *"to do the biggest and craziest things I could think of"*. It is
    for **testing and experimentation** (factory orders, measurement arms — `strata_arm.enter()` / `infer_ollama
    --strata` keep it), **not daily use**: Flash-Next served live to HAI through llama-swap does NOT enter it (§ "Flash-Next
    through llama-swap" below). *"Remember why it was made is more important than exactly what it's blocking right now."*
  * **Strata counts as loaded (2026-10-18):** `frontier_loaded()` also treats a running `strata.exe` as a frontier model
    — Strata is invisible to ollama, so before this the auto pass restored the apps 10 min into every Strata order.
    `frontier_mode.py` ships with `fleet_deploy --only mothership --no-restart frontier_mode.py` (source
    `mothership-build\hostscripts\`, `fleet_deploy.MIRROR_SUBDIR`).
  * **Stop (non-essential, user-level):** Razer Synapse, the ASUS framework, Radeon Software's UI (`RadeonSoftware`,
    `AMDRSSrcExt`), Phone Link, open Settings windows, the Claude desktop app, and similar tray apps. **Games: list and
    ask, never kill** (unsaved progress).
  * **Never stop:** FanControl (thermals), the UPS agent (PowerPanel), Tailscale, ollama, the foundry runner / health
    / telemetry tasks, the Radeon *service*, Windows processes.
  * **The restart is REQUIRED and must not depend on memory — built 2026-09-30:** `C:\ProgramData\HAI\frontier_mode.py`
    (mirror `mothership-build\hostscripts\`). `enter` stops ONLY the allow-list `STOP_APPS` (never a pattern) and pauses
    WSearch/SysMain, recording name + full command line + session in `C:\ProgramData\HAI\frontier_state.json` (merged,
    never replaced); `restore` relaunches each app inside the owner's session (a one-shot Interactive task), restarts the
    services, verifies every one is running, and only then archives the state (`.restored-<stamp>`); a failed restore
    keeps the state so the next pass retries. Task **"HAI Frontier Restore"** runs `pythonw frontier_mode.py auto` every
    5 min as <user> (Interactive, Highest, IgnoreNew): once no `qwen3.8-flash-next*` model has been loaded for 10 min it
    restores; if ollama cannot be read it does nothing (never restores on a guess). Log `C:\ProgramData\HAI\frontier_mode.log`.
    Proven on Notepad: launch-in-session, enter, restore, auto holding while the model was loaded and restoring after it
    unloaded. Usage: `ssh mothership "python.exe C:\ProgramData\HAI\frontier_mode.py enter"` before frontier work;
    `status` shows what is stopped; the task brings it all back afterwards (or run `restore` by hand).
  * **Why it pays (measured 2026-09-30, q2mix):** generation is CPU-bound on the Ryzen 7 5800X (8 of 8 default
    runner threads pinned, GPU ~42% busy); 12 threads beat 8 by ~25%, and 16 lost to 12 with other apps running —
    the all-threads test belongs inside frontier mode.

  ### Flash-Next through llama-swap — a model ollama cannot run, served on demand for HAI's live work (2026-10-18)
  the owner, 2026-10-18: *"get it all wired up and ready for Real use"* / *"I'm surprised we don't already use llama swap"*.
  Design surface: the 2026-10-01 backlog entry "SERVE FLASH-NEXT THROUGH HAI" (adapter + tracking seam, placement,
  lifecycle, context, deck). Flash-Next runs on Strata (its own OpenAI-compatible server), which ollama cannot run, so
  every other model's intake path (ollama pull/create → the box declares it) does not reach it.
  * **On the box:** llama-swap v265 (`F:\llama-swap\v265\llama-swap.exe`, sha256-verified against the release's
    checksums) on **:11435**, task **"Llama-swap Serve"** (<user>, Interactive, Highest — ROCm needs the user session;
    AtLogon + 15-min repetition, IgnoreNew, RestartCount 3; `llama_swap_serve.vbs` waits on it). Config
    `F:\foundry\workspace\flashnext\llama-swap.yaml`, **watched** (`--watch-config`): ship a change with `fleet_deploy
    --workspace flashnext llama-swap.yaml` and it applies with no restart. Log `F:\llama-swap\llama-swap.log`.
    Installer `workspace\flashnext\install_llama_swap_task.ps1` (also the firewall: rule "Llama-swap LAN", TCP 11435,
    LAN + tailnet like "Ollama LAN", and it DISABLES the inbound **Block** rules Windows auto-adds for a new listening
    program — a block beats an allow; that is why Softserve first timed out).
  * **The model's command** is `strata_serve.py run <plan>` (cmdStop `strata_serve.py stop <plan> ${PID}`) — strata_arm's
    one lifecycle, split so a load does not re-pay setup: the first run installs (shard SHA-256 + Strata's setup,
    125 s with the load), later loads ~30-36 s. It REFUSES while a factory order holds the card (`CURRENT_RUN.json`,
    the desk's releasing states) and when a shard changed since install (re-installs). It unloads ollama's models and
    does **NOT** enter frontier mode (daily use). Idle unload `ttl: 300` = the box's `OLLAMA_KEEP_ALIVE=5m` rule.
    llama-swap's two required waits (`healthCheckTimeout`, `unloadTimeout`) are set to one day so they never decide
    anything: the load is governed by status (the owner: *"You know how I feel about random timers"*).
  * **Daily-use speed, measured 2026-10-18 13:1x (3 warm salted calls, 3.2K-token read, thinking off, frontier OFF):**
    read 1,123–1,154 tok/s, write 51.6–56.4 tok/s (cold first call 893 / 29.0). The frontier-mode serving numbers
    (v045 plan: 4K read 1,359, shallow write 75.2) were other prompts — a paired A/B is an experiment for a frontier slot.
  * **HAI's side (orchestrator, `config.SWAP_MODELS`):** a model listed there speaks OpenAI chat/completions to
    llama-swap (`ollama_client._chat_once_swap`) inside the same turn as an ollama call — live deltas, in-flight
    registry, Stop — and comes back in ollama's shape. Its silence watch reads **llama-swap's own state** (`/running`:
    `starting` = loading, keep waiting; `ready` = the idle-card stall rule), never a clock. Any ollama call to the same
    box first frees the card (`_free_swap`: GET /running, unload only when something runs). A JOB-shaped call to the
    crown's box runs on the crown's model (`lead_model`, from the placement table's `roles.crown`). The gate points
    llama-swap at a dead port (`run-suite.sh -e PC1_SWAP=http://127.0.0.1:9`, and `tests/run_suite.py` the same;
    pinned by `tests/test_swap_route.py`). The setting is `PC1_SWAP` (`MOTHERSHIP_SWAP` until 2026-11-16).
  * **Factory orders** that run Strata (`infer_ollama --strata`, A1's Strata engine) start their own server on the same
    port 8080, so they and llama-swap can never both serve; a live load refuses while an order holds the card.

  ### "No space left on device" / OSError 28 while the drive shows free space — NTFS compression RESERVED clusters
  **Symptom:** `OSError: [Errno 28] No space left on device` (or 0 bytes free in Explorer) although nothing big was
  written and every folder-size scan adds up. **Check:** `fsutil volume allocationreport F:` (elevated) — if
  **Reserved clusters** ≈ **Free clusters**, the free space is spoken for. **Cause:** the folder inherited NTFS
  compression, and a program wrote a huge file in pieces / in place (a parallel range downloader, a converter). NTFS
  reserves room for the uncompressed data of every dirty compressed range; Explorer, `Get-PSDrive`, `dir` and
  folder scans cannot see reservations. A reboot releases them (that is why a reboot "fixed" it once on 2026-09-29).
  **Fix (our own folder only):** `compact /u /s:<folder> /i /q` — reserved fell 95.5 GB → 55 MB for the Flash-Next
  download, and the file then SHA-256-verified (compression changes storage, not content). **Prevent:** before a
  big write, `compact /u <new folder>` so new files land uncompressed (`(Get-Item <folder>).Attributes` must not say
  `Compressed`). Model/weight files barely compress anyway (F: saves ~0.1 of 131 GB in `foundry`). Clearing the
  compression drive-wide is a parked mission: backlog "MOTHERSHIP F: IS NTFS-COMPRESSED DRIVE-WIDE".

  ### A JOB'S MODEL CANNOT SEE — "The model serving this job is not set up to take pictures" / `seen_by: null` — give an ollama model its vision file
  A lead is served with its eyes on, on every factory that serves it ([[HAI-Agent-Surface-Design]] § "A lead is served
  with its eyes on"). Symptom: every `coding_look` in a job returns `"seen_by": null` with that sentence, and since
  2026-11-15 the operator gets a `[job:blind]` desk notice at the first one. Cause every time so far: the ollama model
  was built from the weights alone, with no vision (projector, `mmproj`) file, so `/api/show` lists no `vision`.
  * **Find it:** `python E:\SSDShared\Lab\bin\vision_parity.py` (read-only, loads nothing). It fails when the same
    model name or the same weights see on one box and not another, or when rigby declares `sees.<file>` and the GPU
    boxes' twin does not see; it also lists every tool-calling model with no eyes. Run it after installing or
    re-creating any model, and add a `SAME_WEIGHTS` row when rigby gains a model.
  * **Fix it through ollama's own API, metadata only (2026-11-15, the 35B A3B MTP build on BigHonker and Mothership):**
    1. hash the archived projector and `POST /api/blobs/sha256:<digest>` with the file as the body (899 MB took about a
       minute over the LAN); `HEAD` the blob to confirm.
    2. `POST /api/create {"model": "<name>-eyestest", "files": {"model.gguf": "sha256:<the model blob the existing
       model's FROM line names>", "mmproj.gguf": "sha256:<projector>"}, "parameters": {…the existing ones…}}`. Both
       blobs are already in the store, so nothing is copied. ⚠ `from` and `files` are exclusive in 0.40.5
       (`server/create.go`): use `files`. ⚠ Do NOT send `template`: for a llama.cpp-layout model `/api/show` echoes
       the GGUF's own jinja, and ollama refuses it as a Go template (`function "content" not defined`).
    3. compare `/api/show` of the test tag with the real one: capabilities gain `vision` and nothing else moves
       (parameters, template hash, model_info). Then create the REAL name the same way and delete the test tag.
    4. prove it through Doc, pinned, with `research\ltt-jarvis-prompts\eyesproof-prompt.txt` and a picture the model
       has never seen: the look must return `seen_by: you` and the answer must be true of the picture.
    Scripts: `bh_eyes_step1.py`, `bh_eyes_step2.py`, `bh_eyes_proof.py` in the 2026-11-14 session tools archive.
  * **The card:** ollama 0.40.5 itself adds `--no-mmproj-offload` when the projector does not fit beside the model
    (`llm/llama_server.go`), so a split model keeps its placement. BigHonker's 35B read 7.1 of 19.1 GB on the card
    after (6.8 of 18.8 before) and wrote 23 to 25 tok/s, inside its documented band; not a controlled before/after.
  * **Undo:** create the same name again with `files` = the model blob only. ⚠ ollama 0.40.5 keeps manifests under
    `manifests-v2`, not `manifests`: a backup script written for the old path finds nothing. The 2026-10-17 copy in
    `C:\ProgramData\HAI\` on BigHonker is the no-vision manifest.
  * Kirk caches a box's "no" for one minute (`ollama_client.CAPABILITY_NO_LIFE_S`) and a "yes" for the process, so a
    fixed model sees without a Kirk restart.

  ### Putting a model into ollama WITHOUT `ollama create` — store blob + manifest (how Flash-Next went live)
  `ollama create` from a local GGUF runs a llama-quantize COPY validation that writes a full-size temp copy and
  **crashes on a single tensor ≳ 28 GB** (Qwen3.8-Flash-Next's `per_layer_token_embd.weight`: iostream error;
  ollama issue #18146). A registry pull does not validate. What ollama actually serves is only two things in
  `E:\Ollama\Models`: blobs named `sha256-<digest>` and a JSON manifest at
  `manifests\registry.ollama.ai\library\<name>\<tag>` listing them (config, model, projector, params, license).
  So a model can live on another drive and be **symlinked in** (`os.symlink`, needs the full token — ssh gives it):
  one link per blob into `E:\Ollama\Models\blobs\`, one for the manifest; ollama lists and loads it with no restart
  (`/api/tags`, `/api/show`). The config blob's `requires` field sets the minimum ollama version. Flash-Next,
  2026-09-29: `qwen3.8-flash-next:125b-a6b-q4_K_M` pulled whole to `F:\Ollama-FlashNext` in store layout by
  `C:\ProgramData\HAI\get_flash_next_ollama.py` (every layer SHA-256-verified, manifest written last), 5 links into
  E:. A self-cooked GGUF goes in the same way with a hand-written manifest (reuse the registry's projector, params
  and license blobs; config `file_type` set to match). Full model record: [[Qwen3.8-Flash-Next-on-Mothership]].

  ### Mothership ollama CPU-only / size_vram 0 / no ROCm device — the tray auto-updater is the cause
  If every model on Mothership loads with `size_vram: 0` (silent ~20x CPU crawl, requests still succeed) and
  serve's log shows discovery finding only `id=cpu` with `total_vram="0 B"`: the stock ollama.com updater has
  replaced the AMD AI Bundle build in place. It did exactly this on 2026-08-18 (tray app ran `OllamaSetup.exe`
  silently → the resulting tree had NO GPU backend for this card: `rocm_v7_1` and `vulkan` dirs gone → three
  days of CPU serving that nothing alarmed on). ⚠ **Corrected 2026-09-12:** the fault is the stock
  *installer* (it laid down only `cpu` + `cuda_v12`), not stock ollama. The "AMD AI Bundle" ollama IS stock
  ollama — upstream's `ollama-windows-amd64.zip` + `ollama-windows-amd64-rocm.zip` laid together — and a stock
  **0.32.13** served the 7900 XTX on ROCm on this box from 08-15 to 08-18 (its update kept `rocm_v7_1`). So a
  newer ollama does NOT break ROCm here; an in-place install without the rocm add-on does. Upgrade path and
  the qwen3.8 gate: [[34-A-OLLAMA-UPGRADE-GATE]] (parallel tree from the two zips, never the installer).
  **Fix for the CPU-only state: restore the bundle tree from a pre-clobber snapshot** — VSS shadow copy of
  `C:` (`vssadmin list shadows /for=C:` → mklink the shadow → robocopy the whole
  `AMD\AI_Bundle\Ollama` folder, never mix generations) or the parked copy
  `...\AI_Bundle\Ollama.stock-0.32.14-clobbered-20260818`'s sibling era. Then verify: fresh serve.log
  discovery line names `ROCm0 ... RX 7900 XTX`, and a real chat load shows `/api/ps` `size_vram == size` at
  `num_ctx` 65536. **Prevention is the launcher itself**: serve-direct means the updater never runs — do not
  reintroduce `ollama app.exe` to any launch path on this box.
- All set `OLLAMA_HOST=0.0.0.0`, API on `:11434`, reachable from Sundae (the orchestrator host).
- **GPU check = `ollama ps`** (PROCESSOR column) — vendor-neutral. `nvidia-smi` is NVIDIA-only and does
  NOT work on Mothership; AMD uses `rocm-smi`/`amd-smi`. (Phase E load-monitoring: Glances GPU is
  NVIDIA-centric → Mothership needs an AMD-aware "busy" signal, e.g. amd-smi or Ollama `/api/ps`.)

### "does not fit before load" on a model we KNOW fits (the 27B on Mothership) — check the fit PROFILE, not the desktop

**The lab's known fits are measured, so a refusal that contradicts one is a defect to find, never news for the owner.**
The 3.8 27B on the 7900 XTX at 122,880–125,952 is a months-tested operating point (17.1 GB `size_vram == size`,
82 tok/s; the Mothership row above). If HAI refuses it with
`model fit: qwen3.8:27b @ N needs X MiB including other tenants; does not fit before load`, do this in order:
1. Read the guard's inputs, not the box. The arithmetic is `predicted = other + required_mib`, refused above
   `card_mib`. `other` = the box's last load push (`State\gpu-load\<host>.json`: `gpu_memory.adapter_mb` minus what
   our runners own). `required_mib` = the point in `policy.json` → `model_fits.hosts.<host>.<model>.points`,
   compiled by `menu_policy.memory_profiles()` → `model_fit.compile_profiles()` from the fit probes
   (`tabfoundry-jobs\campaigns\fit-*\fitprobe-<host>*\fit.json`).
2. Compare `required_mib` with the probe row it came from (`spill.dedicated_mb` at that `num_ctx`, `fits: true`). If
   required is LARGER than any measured allocation at that window, the profile is wrong, not the card.
3. Only then look at what else holds the card (GPU process counters: dwm, Parsec, apps).
   **Seen once, 2026-11-11 — watch for it again, no mechanism built (the owner: "pay attention if it happens again"):
   after a game on Mothership the desktop compositor kept 2.8 GB of the card** (`dwm.exe`; usual ~0.24 GB, whole
   desktop ~1.3 GB) after the game and Steam were closed, and the 27B's window was REFUSED every 600 s
   ("holds 1773 MB in shared system memory on a full card", `F:\foundry\var\log\foundry_queue.log`) for three hours.
   The runner was its documented size (22.2 GB at 125,952, the same on engine r4 and r5:
   `enginememory-…-r4-vs-r5.20261111-213745-c755`). the owner allowed a compositor restart
   (`ssh mothership "taskkill /F /IM dwm.exe"`; Windows starts it again, the screen blinks): the desktop fell from
   3.5 GB to 1.4 GB and the next retry loaded. It is his desktop: ask before restarting it.

**2026-11-02, the case that wrote this section.** The pin was refused at 122,880 for 23,658 MiB beside a 1,222 MiB
desktop. The probe had measured 22,964 MiB at that window on 10-16, beside a 1,265 MiB desktop, and it fit. The cause:
`compile_profiles` applied the LARGEST overhead seen at any window (156,672's) to EVERY window, adding ~700 MiB that was
never measured at 122,880. Fixed in `model_fit.py` (orchestrator `f1d25c7`, gate 3329): a window that fit is its own
measurement, and only a spilled window keeps the estimate. `policy.json` regenerated through `menu_policy` (old copy:
`Archive\policy-json\policy.json.20261102-pre-fitfix`): 213 of 430 points went down, none up. The 3.8 now opens at
125,952 beside the same desktop. Same defect, smaller: medgemma:4b on BigHonker shed ~1.3 GB, gemma3/medgemma/Ornith
27–35B on Mothership 100–470 MiB. ⚠ The first diagnosis (that session's) blamed the desktop and handed it to the owner as
"his call". The library already held the answer: the model's documented fit.

### BigHonker refuses the 9B ("does not fit before load") while someone is logged in or the desktop is locked — sign the console out

A logged-in Windows session on BigHonker, even a locked one, keeps about 1 GB of the 8 GB card (the compositor; closing apps frees
~23 MB, measured 10-25). Beside that the 9B's smallest measured window misses the fit line and the 9B gets no window at all; chats
move to the next model with no error. **The fix is not a rule change: when nobody is at the box, sign the console out.**
1. Is anyone there? `quser` + a locked console (`LockApp` running), the deck's accounting `idle`, or the owner says so.
2. `ssh bighonker "logoff <session id>"` (the id from `quser`). Every lab process there runs as SYSTEM in session 0 (ollama, the
   foundry runner, the render worker, the health daemon, fan control, sshd), so nothing of the lab's stops. Open apps in the
   session close, so check for real work first (2026-11-05: only Edge and vendor helpers).
3. Read back: `quser` says no user, and `scheduler.window_ctx("bighonker", "qwen3.5:9b", 19968)` answers 19,968.
Done 2026-11-05 on the owner's word (*"just signing the person out when I already confirmed that nobody is at the box"*): the 9B went
from no window to its full 19,968. History: with a light desktop the 9B fit to 31,744 (10-22) and to 39,936 with nobody logged in (10-16).

### A NEW CHAT IS SLOW TO START (a minute before the thinking appears) — where the seconds go, "stale placement … evicting and reloading clean", "no measured window fits" on a model whose window is open

**Read the stages before theorising; every one is logged.** Kirk's work log stamps SECONDS since 2026-11-14
(`Notes\<day>-orchestrator.md`), and three records split a slow start:
1. **Kirk, before the window is ordered:** the lines `EMBED placement`, `weighing needs`, `McCoy:`, `tool selection`,
   `CHAT WINDOW opened`, and one `PRE-TURN … gate= permit= state= select= block=` line for any turn that spent a second or
   more between its routing and its request (`select` is Starfleet's pick, an embed: ~2.7 s measured).
2. **The factory, from the order to the first request:** the window order's own log beside its spec,
   `<foundry>\jobs\queue\<window name>_<box>.job.foundry.log` (`archive\` once it ends): preflight, the window program's
   start, then either `not loaded - performing a clean load` or a placement line.
3. **The engine:** `CHAT WINDOW … first output N s after open (load L s, read T tokens in S s)`. ⚠ `load` here is the
   engine's own number for the TURN; a load the window did before the turn (item 2) is not in it and reads 0.0.

**Measured 2026-11-14, the owner's own new chat on Mothership's 27B, 60 s from send to thinking:** Kirk ~14 s (embed + weighing +
McCoy 5, the tool pick 4, sizing and ordering the window 5) · the factory claiming the order and starting the window ~10 s ·
**the model's load 15 s** (the previous window had closed under the 300 s idle rule, `helper_window.idle_rule`) · **reading
12,315 tokens cold 18 s** (the saved starter covers only the core tools, and its file had been renamed by that day's
deploys and was rebuilt after the turn) · first token ~2 s.

**Kirk's own seconds, measured the same evening on a fresh chat with the 27B unloaded (client-timed, `scratchpad
fresh_chat_timing.py`): 10.6 s from "Request received" to the turn reaching the model, 7.5 s after one fix.** The fix:
`mothership.gpu_free_mb` (the gaming check) logged into Mothership over ssh and started PowerShell to read one counter,
**2.2 s a call** (0.35 s of it ssh), and that turn ran it twice (Spock's weighing through `user_on_card`, then
`_ensure_lead`). The box's own minute report already carries the number (`gpu_memory.card_mb - adapter_mb`; both
said 1,548 MB at the same moment), so the report answers when it is no older than two pushes and its counter is ok, and
the ssh probe is the fallback (Kirk `9968b98`). What is left of the 7.5 s: ~1 s before the weighing, ~1 s weighing, 3.0 s
inside the chat call (`PRE-TURN`: the tool pick 2.0, the user's tool permissions 0.5, the lead/render check 0.5), ~2 s
sizing and ordering the window. Two more Kirk-side causes found and fixed the same evening: **the library index rebuilt ON
the turn** after any library document was edited (12 s before McCoy was even asked; now rebuilt aside, log line
`LIBRARY INDEX … rebuilt aside`, while an upload or save still reloads its namespace at once), and **the desk's own
polls** (below). Last readings, 2026-11-14 20:28 and 20:36: about 4 s from send to the window opening
(`PRE-TURN … gate=0.5s permit=0.0s state=0.0s select=0.7s`). **A third one, fixed: `permit=4.2s` in the `PRE-TURN` line.** Every turn's
tool list passes through the dataset source registry (`tools.tools_for` → `_ground_dataset_sets` → `_dataset_registry`),
which is read from Mothership over ssh. **Root cause, fixed 2026-11-14:** Kirk built the registry's path from
`mothership.DATASET_PY`, which still named `F:\flux2-train\p20_dataset.py`, a tree that has held only models since the
factory moved to `F:\foundry`. Every read failed after about four seconds of ssh, a failed read is never cached, and the
next turn after each one-minute quiet window paid for another. The constant now names `F:\foundry\bin\p20_dataset.py`
(the registry sits beside it) and the same turn reads `permit=0.0s`. The same fault had left HAI with no dataset sources
and unable to launch a dataset job on Mothership. A copy past ten minutes is also served at once and refreshed aside,
and Kirk asks once at startup, so a slow Mothership no longer lands on a turn either. **If `permit=` is seconds again:**
run the read by hand (`ssh mothership "python.exe -"` with the body of `mothership._DATASET_REGISTRY_BODY`) and look at
which file answers, before touching the cache.
**The factory's ~10 s, measured and fixed** ([[Desk-Time-System-Time]] § "The pass before a pickup"): the runner's pass
before a pickup was `pass 2.4 s: controls 0.9, collect 0.6, patrol 0.5, police 0.4` and is `pass 1.0 s: patrol 0.5,
police 0.4`; every guard still runs before every pickup. The half seconds were Kirk's desk parsing all 4,853 order
records on every factory's every ask (`DESK POLL slow`). Order → window program is now ~3–4 s on Mothership.

**`stale placement: <model> at X/Y GB - evicting and reloading clean` on a declared split is a defect, fixed 2026-11-14.**
BigHonker's 35B lives 6.8 of 18.8 GB on its 8 GB card by design (`offload_ok`), and every new chat window evicted it and
reloaded it to the same 6.8/18.8, 32 s. Now the last CLEAN placement of each model at each context is remembered on the box
(`<foundry>\var\state\clean-placements.json`, `infer_ollama._assert_resident`): a resident placement at least as good is
`kept without a reload`; a worse one (a load that landed while a desktop held the card), or another context, is still
reloaded. The first window after the file is missing loads once and records.

**`WINDOW SIZE … no measured window fits beside the card's current use` for a model whose own window is OPEN is the same
family, fixed 2026-11-14.** bh-d (16:13) was refused its own pinned model on its second turn and ended unanswered: the
window had just reloaded the model, the box's minute-old load report still said "not loaded", so the RAM check counted the
model's own RAM against it. `chat_window.sized_ctx` now joins a window Kirk itself has open for that model on that box
(log: `the card reads full …, but this model's own window is open there - joined`). A run that ended that way continues
from its save: `POST /runs/resume {"user": <admin>, "run_id": …}` (done for bh-d at 16:28, `RUN RESTORED FROM ITS SAVE`).

### Ollama upgrade (BigHonker, Softserve, Mothership) — parallel tree; the four traps; 0.40 model migration and symlinked manifests

**Procedure (in force since 2026-10-10, both boxes on 0.40.2):** `ollama_upgrade_tree.ps1` (BigHonker
`I:\downloads\ollama-0.40.2\`, Softserve `E:\tmp\ollama-0.40.2\`) — download upstream `ollama-windows-amd64.zip`, check
its sha256 against the GitHub release digest · extract into `…\Programs\Ollama-<version>\` beside the running tree ·
repoint `$ollama` in `C:\ProgramData\HAI\start-ollama-system.ps1` (timestamped backup) · end the task, kill `ollama.exe`
AND `llama-server.exe` · `schtasks /run "Ollama Serve"` · prove `/api/version` and that the new PID's path is the new
exe. Rollback = repoint `$ollama` to the previous tree, rerun the task.

| trap (proven live 2026-10-10) | symptom | fix |
|---|---|---|
| `ollama.exe` carries no file-version resource | `VersionInfo.FileVersion` empty | ask the binary: `ollama.exe --version` |
| with a server running, `--version` prints the SERVER's "ollama version is X" | the new exe reports the old version | read the "client version is Y" line |
| per-program Windows Firewall allow rules bound to the old exe path | localhost answers, every remote caller times out (`10060`) | a port rule: BigHonker `Ollama LAN` (TCP 11434 inbound, <lan-subnet>/24 + <tailscale-range>/10); Softserve already has `Ollama LAN (Sundae+HA)`; Mothership `Ollama LAN` since 2026-10-10 |
| Windows auto-creates **Block** rules (`ollama.exe`, TCP + UDP, Private/Public) for a NEW exe path the first time it listens — a dismissed or unanswered firewall prompt. **Block beats Allow**, so the port rule above does not help (Mothership 2026-10-10, again 2026-10-15, and 2026-10-22 for the `-lab620d222` tree: 14 min unreachable from Kirk and the desk — the foundry logged `card/model availability could not be asked`). Only Mothership hits it: it starts ollama as the console user, BigHonker/Softserve as SYSTEM (no prompt) | same: local answers, the LAN times out | the owner accepting the prompt on the console, or `netsh advfirewall firewall set rule name=ollama.exe dir=in program=<new exe> new action=allow`, then `curl http://<box>:11434/api/version` from Softserve. `fleet_deploy --engine` runs that curl itself after activating and rolls the box back + prints the netsh line when it fails (mirror `28a27c5`) |
| **A model's tested context can stop fitting after an upgrade.** llama.cpp's fit step keeps a free-memory target (ollama passed 1,927 MiB on the 3070), so a model measured with a few MiB to spare tips over when a new version's estimate grows (0.40.2 → 0.40.5: qwen3.5:9b at 14,336 projected 5,131 → 5,139 MiB, 4 MiB short → 33/34 layers) | `size_vram < size`, "offloaded N-1/N layers", orders REFUSED by their residency guard | **every box runs `LLAMA_ARG_FIT_TARGET=512`** (Mothership launcher since 2026-10-02; BigHonker launcher line and Softserve machine env since 2026-10-15). After ANY upgrade: re-fit every box (`hard-bench\fit_probe_orders.py`) before trusting a tested context |

**The Mothership (AMD) tree** is the same procedure with BOTH zips (base + `-rocm`) extracted into
`…\AI_Bundle\Ollama-<version>` and the launcher `C:\ProgramData\HAI\start-ollama.ps1` (`FilePath` + the
`OLLAMA_LLM_LIBRARY` backend line). Back up `E:\Ollama\Models\manifests` before the first 0.40+ start (rollback
without re-pulls). Restart only with the factory idle and a `planned-downtime.hold` written.

**0.40 behaviour to know:** each model is migrated on its first use (`migrate.go … completed local compat GGUF
migration`, minutes; the first request pays it), the original blob is kept as a backup (store grows until cleaned),
`/api/tags` keeps reporting the old size while `/api/ps` reports the migrated one, and a downgrade below 0.40 needs a
re-pull of migrated models. Softserve's store is `E:\ollama-models` (set in the launcher and the machine environment).
**Which models migrate** is decided per architecture by `compatmigrate/<family>.go` (`NeedsMigration`, ollama
v0.40.2): ollama-format qwen3.5/3.6 (vision or `mtp.` tensors), gemma3/medgemma, qwen3-vl. Models already in
llama.cpp layout do NOT migrate: Mothership's qwen3.8:27b (it carries `nextn_predict_layers`, so MTP is native),
the hf.co GGUFs (Ornith, Nemotron), qwen3 8B/14B, qwen3-coder. A migration needs 1.25 × the model + 512 MiB free
on the store's drive. **Hand-installed models (symlinked manifests) stop loading on 0.40.1+**: serve.log `bad
manifest … manifest symlink target "…" is not a sha256 blob` — this hit Mothership's Flash-Next q2k/q2mix
2026-10-10 (unlisted, files untouched; ollama also skips its blob prune while a manifest is bad). **Thinking
effort for qwen3.8 on 0.40.5 — MEASURED 2026-10-16, it CHANGED:** rendered prompt (salted, nothing cached) is 25
tokens for `true`, `"medium"` AND `"high"`, and 67 for `"xhigh"` (the vendor's effort sentence); `false` 27. So on
0.40.5 **`"high"` = MEDIUM and only the literal `"xhigh"` renders xhigh** (0.34 returned 400 for `"xhigh"` and rendered
`"high"` as xhigh, which is where the foundry's `infer_ollama.OLLAMA_THINK` xhigh→`"high"` came from). Omitted
`think` was not measured. Before trusting any effort label on a new ollama version, re-run the render probe
(`#37` journal 2026-10-16: one salted call per setting, compare `prompt_eval_count`).

### SLOT SAVE / RESTORE on every GPU box — keep a conversation's read state across an eviction (#34 B11b, 2026-10-21)

**What it is:** ollama 0.40.5 serves each model through its OWN bundled `lib\ollama\llama-server.exe` (serve log:
`starting llama-server … --port <n> --host 127.0.0.1 …`), and that build supports slot save/restore. Its
`libllama-common.dll` reads **`LLAMA_ARG_SLOT_SAVE_PATH`**, so one launcher line turns it on for every runner — the
same channel as `LLAMA_ARG_FIT_TARGET`. **Set on all three boxes 2026-10-21** (ollama restarted after):
`$env:LLAMA_ARG_SLOT_SAVE_PATH = 'C:\ProgramData\HAI\slots'` in `C:\ProgramData\HAI\start-ollama-system.ps1`
(BigHonker, Softserve) and `C:\ProgramData\HAI\start-ollama.ps1` (Mothership); backups `*.bak-20261021-*-pre-slotsave`;
installer `lab-engine\ollama_slotpath.py` (idempotent; `OLLAMA_LAUNCHER` picks the file).
**Mothership's saves live on `F:\HAI\slots` since 2026-11-05** (the owner: "Move them to F:"; a 2.2 GB save to its C: started a 16-minute stall, RUNBOOK-GPU-Host-Power-and-Glances; the move is
`Lab\bin\ms_move_ollama_slots.py`). **The launcher's line is the one truth for where a box saves:** `helper_window.runner_slot_dir()` reads it, so the window and the engine cannot disagree.
**Use:** an on-box caller finds the runner's port on its command line (`Win32_Process` `llama-server.exe` →
`--port <n>`), POSTs `http://127.0.0.1:<n>/slots/0?action=save {"filename": "<f>.bin"}` before the model is evicted,
and after the reload `…?action=restore` with the same file before the next turn; ollama's next request then reuses the
restored prefix. **Measured (order `b11b-slotdump-*`, payload `mothership-build\workspace\slotdump\slot_dump_probe.py`,
on every factory via `fleet_deploy --workspace slotdump`):** BigHonker qwen3.5:9b, a 6.3k-token conversation — turn 2
after an eviction read in **0.13 s with the restore vs 2.38 s without** (save 0.32 s / 272 MB, restore 0.16 s);
Mothership qwen3.8:27b (Vulkan) — **0.54 s vs 9.29 s** (save 3.1 s / 725 MB, restore 2.5 s). ⚠ ollama's
`prompt_eval_count` counts the WHOLE prompt even when the cache served it — compare `prompt_eval_duration`. ⚠ Slot
files land on **C:** (Softserve's C: is the tight one): whatever saves a slot must delete it once restored or
abandoned. Between turns it is exactly the prefix that was saved; MID-answer it is lossless only on the lab engine
(pending token / RNG / MTP state = lab patches #0/#2/#3 — § "LAB ENGINE on all four factories" below).
**The callers (2026-10-21):** a live chat window that yields its card to desk work (`helper_window.py` B11b), and
**McCoy BORROWING a card** when none is free (`mccoy.triage(lend=True)` / `POST /mccoy/triage`, worker token): Kirk
writes `LEND` in his own IDLE chat window's folder, the window saves its slot and answers `LENT`, McCoy's tev1 takes the
card, and the window's next item reloads its model and restores before answering (both markers then removed). Spock
offers a borrowable card only with `place(..., lend=True)` and ranks every free card above it; the shadow never lends.
**Live proof** (`ClaudeSupervisor` chat `9c2b7558accc`, BigHonker qwen3.5:9b): McCoy answered in 6.9 s end to end
(handshake + save 0.51 s / 13,901 tok + tev1 load 2.76 s); turn 2 read 14,549 tokens in **0.46 s** (cold read of the
same conversation: 5.45 s). ⚠ **A slot save or restore waits for the slot to go IDLE** — on a box where another call
is generating on the same model (Kirk's `AUTO_TITLE` thinks 45–83 s on the chat's own card after turn 1), the lend
waits for it: the first live lend waited 83 s, past McCoy's 30 s clock, and McCoy correctly deferred.

**CHAT QUICK RESUME — per CHAT, since 2026-10-25** (the owner: *"a customized quick resume"*; *"not every single swap of
chat disrupts"*; *"the most recent five for each user"*, older *"saved to the hard drive array"*). Every live window turn
names its conversation (Kirk's item `conv` = `"<user>/<chat id>"`, `app._stream_worker` → `ollama_client` →
`chat_window.open_stream`); `helper_window.ConvSlots` keeps that chat's slot under its OWN file
`<the box's slot folder>\conv-<model>-<ctx>-<sha1 of conv>.bin` + a `.json` sidecar (user, chat, model, ctx, time —
one writer per file). It SAVES when the chat goes idle waiting on its user (the window's `IDLE` = no run is using the
window at all — tabs and devices watch a run, they never hold one, so closing one tab saves or unloads nothing) and
otherwise before a different chat's turn, at a lend and at the window's close — only if the chat served a turn since
its last save; consecutive turns of one chat never save. It RESTORES the incoming chat's file before its turn, **unless
the saved state's tools or effort are not this turn's** (since 2026-11-14, every factory: the sidecar keeps `start` =
`helper_window.start_sig` of the last turn asked; those render first, so a state that differs there gives back almost
nothing; log `chat state of <conv> not restored: …`). Measured on BigHonker's split 35B, where a restore costs nearly
what reading does (27,384 tokens in 16.2 s, 40,424 in 22.5 s, on an NVMe that reads 2,165 MB/s: the engine, not the
disk): bh-d paid 16 s for a state saved before its job's tool block was replaced, then read all 26,501 tokens anyway.
The system text is NOT compared (it renders after the tools and differs between the plain and lead paths); a file with
no recorded `start` and a turn parked mid-answer are restored as before. Retention
(`helper_window.retain`): each user's 5 most recently USED chats stay in the slot dir (every GPU box's C: is NVMe); older
ones move — file + sidecar — to `\\<softserve-address>\Big Shared\HAI\chat-slots\<box>\`, never deleted (an unwritable array
leaves them on the box, said). The model/context file of B11b remains only for a turn with no chat. **Live proof**
(ClaudeSupervisor chats `107696539ab8` / `bf60a3493773`, one Mothership qwen3:14b window, 15:12 CT): A saved at idle
(8,171 tok, 711 MB, 0.33 s) → B (no save at the switch: A was already saved; B saved at idle 0.32 s) → **A restored,
8,171 tok in 0.20 s**, answered, saved 9,402 tok in 0.41 s. **Placement prefers the box holding a chat's state (since
2026-10-25 evening):** Kirk records the (box, model, window size) whose window last finished a turn of each chat
(`chat_window.record_home`, `<STATE_DIR>/chat-state-homes.json`, newest 5,000), and the weighing prices a COLD row on
that box at its load without the conversation's re-read (`scheduler.weigh_rows(conv=)`); warm rows keep today's price.
**Older chats come back from the array — restore OR re-read, by measurement (since 2026-10-25 night):** a turn of a
chat whose state THIS box last saved at this model and window size (Kirk's item `state_here`, from `record_home`) and
whose file is no longer in the slot dir asks the array (`helper_window.fetch_from_array`). It copies the file back —
file + sidecar, a move — only when that beats re-reading the conversation: copy = file size ÷ this box's last MEASURED
array read (`<the box's slot folder>\array-read.json`, written by every copy), re-read = the sidecar's saved tokens ÷
the menu's measured read rate for the pair (Kirk's item `read_tok_s`, `scheduler.read_rate`). The copy watches its own
progress and stops the moment it is projected to lose (the partial file dropped, the rate still recorded), so a slow or
unmeasured array costs at most a chunk over a re-read; a chat with no measured read rate is brought back (restoring is
never worse). A new chat, or one another box served last, never asks the array. **Typing prefetch (the owner's trigger,
"open AND typing"):** the page's first keystroke in a chat posts `POST /chats/prefetch` once per chat per page;
`chat_window.prefetch` writes a `<id>.fetch.json` ask (`{conv, read_tok_s}`) into the OPEN window that holds the chat's
state, and `ConvSlots.prefetch_asked` answers it in `serve()` while no turn is pending — the same restore-or-re-read
choice, off the turn's critical path. No open window on that box → nothing is asked, and the turn's own fetch runs.

**WHICH BOXES CAN REACH THE ARRAY AS SYSTEM (the account chat windows run as): ALL THREE, since 2026-11-02.** Each
was measured with a one-shot `schtasks /ru SYSTEM` task that lists, writes, reads and deletes under
`Big Shared\HAI\chat-slots`. BigHonker ✅: SYSTEM holds a `<softserve-address>` credential for `SOFTSERVE\Administrator`.
Softserve ✅: the array is local. **Mothership ✅ since 2026-11-02 08:13.** It read `Access is denied` from 10-25
until then, because the 2026-10-09 remedy had stored the credential in the owner's own session and not in SYSTEM's, so a
SYSTEM process connected anonymously (the share and NTFS grant Everyone Full, so identity was the whole gap).
**How it was stored for SYSTEM (the locker method, no secret on any command line):**
1. scp `Reference\Credentials\network-credentials.md` to `C:\ProgramData\HAI\locker.tmp.md` on the box.
2. Run `E:\SSDShared\Lab\bin\system_share_cred.py` there as a one-shot SYSTEM task (`/sc once /sd 01/01/2020 /ru
   SYSTEM`, then `/run`, then `/delete`).
3. The script deletes the locker copy first. It then runs `cmdkey /add:<softserve-address> /user:SOFTSERVE\Administrator`,
   probes the share, writes a masked result to `system_share_cred.out`, and deletes itself.

A box without the credential keeps older chats on its own NVMe (said in the window log, nothing lost), and its window
asks the array once, then not again. ⚠ An ssh probe cannot test this: ssh is a network logon with no stored
credentials, so "Permission denied" over ssh says nothing about SYSTEM.

**AN IDLE WINDOW IS A CACHE — a turn for another model closes it and takes the card (2026-10-25).** After a Hard turn
the idle Flash-Next window held Mothership for its 5-min idle rule: the 27B pin was refused as "a game" and a Nursing
turn found no box. Now: the gaming guard counts Kirk's own window as ours (`mothership.gaming_busy`); a CHAT turn reads
an idle own window of another model as a cache (`scheduler.occupied_for(..., takes_window=True)` — never for an embed,
which would load BESIDE the idle model and evict it); the window door closes it (`chat_window.close_idle`), waits for it
to END and for a card report taken after that (`await_card_freed`, bounded by the reporter's freshness budget x3), THEN
sizes the new window. A window any run is using is never touched.
**Closing a window whose order has not STARTED also cancels it at the desk (since 2026-10-29, `chat_window._cancel_unstarted`).** CLOSE only reaches a running window. An order still in a factory's queue never reads it, and it once stayed queued for good: a closed qwen3-coder window, queued on Mothership since 10-28 and too big to fit beside the desktop's 1.4 GB, retried at the head of the queue every ~36 s, and both Flash-Next research runs behind it waited until HAI cut them. The symptom to recognise: `foundry_queue.log` repeating `STAYS QUEUED, retrying` for a `hai-win-…` order whose window HAI already closed. The fix by hand is the desk's own cancel (`POST /p20/order/cancel {"id":"<desk id>"}`), never a hand-deleted queue file.

### WINDOW PICKUP — a restart of HAI picks its chat windows back up, a turn being written is reconnected (2026-11-05)

the owner's standard is Xbox Quick Resume: *"fully lossless … bit identical"*. A chat window is a factory order, and its engine does
not care that Kirk restarted, so nothing has to be restored and the result is identical by construction. Design:
[[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]]. Every window writes `WINDOW.json` beside `OWNER.json` (host, model, size,
desk id). At startup `chat_window.adopt_windows` registers every folder that has one, has no CLOSE and whose owner is not
past its silence rule, keeping only the runs that are really resuming; a window nobody returns to goes IDLE and ends by its own
rule. A resumed run goes back to its own window without weighing (`take_resumed`), its turn claims the unfinished item when
model and messages match (`_claim_item`), and its tool list comes from the ledger's checkpoint (`Slot.tool_names`), because a
different list would re-read everything. **The log lines to look for, in `Notes\<date>-orchestrator.md`:** `CHAT WINDOW picked up
<name> desk=<id> (claimed, N item(s), owner silent N s, runs [...])`, `TOOL LIST kept across the restart: 32 of 32 tools`,
`turn <id> reconnected after the restart (still being written), not asked again`. **Silence budget:** owner period 15 s × 3 = 45 s;
the measured gap of a recreate is 18–29 s. A park raises the period to 60 s. If the budget runs out the window ends and the run opens
a new one: today's cost, never worse. **Measured** (cookie job, Flash-Next, no park, answer mid-generation): gone 18 s, one item,
same desk id, context 98K → 103K straight through; next turn `prompt_n` 10,635, `cache_n` 80,555, 9.3 s against 67.9 s for a full read.

### FLASH-NEXT QUICK RESUME — Strata session files, a chat's state across an engine stop (2026-11-05)

**Flash-Next runs on Strata, not on ollama: the lab engine's patches do not reach it.** Strata 0.1.45 has its own session files on
the same API (`POST /slots/0?action=save|restore {"filename"}`, its docs `docs\DETAILS.md` § Session files; source copy
`E:\SSDShared\lab-engine\strata-0.1.45`). The serving plan's `slot_save_path` (`F:\HAI\slots`, never Mothership's C:) turns them on
(`strata_arm.apply_serving`, which also survives setup rewriting the config). A Strata window keeps per-chat state exactly as an
ollama window does (`helper_window.ConvSlots`, `engine_port` = the window's own engine): saved at idle, at a chat switch and when the
window closes, restored when the chat comes back, same retention. About 1.2 GB per 63K tokens. A stop slip lets the window finish
its turn and save first (§ STOP in [[RUNBOOK-Compute-and-Training-Order-Submission]]). **Results and the proof order:** see
[[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] § Status. Re-run: `python E:\SSDShared\Lab\bin\submit_strata_resume_probe.py`
(payload `workspace\flashnext\strata_resume_probe.py`, needs the card).

### THE HOT START — a new chat starts from a saved file of what every chat shares (2026-11-05)

the owner, 2026-10-25: *"some sort of pre-saved state that the models load up"*, on the fastest drive of each box. A chat with no state of
its own restores a slot file holding the part of its prompt every chat has, so its first turn reads only the rest. **What can be
shared is set by the prompt's order: tools render first**, so the shared start ends at the first tool that differs (`hot_tools`
on the item). **Since 2026-11-14 a new chat's block is the MASTER block** (the selector's always-offered set, the same for
every new chat; Starfleet's picks ride in the first message; log `MASTER START`), Kirk says the whole block is shared
(`hot_tools` = its length), and the start runs through the tools AND the system text: **5,120 tokens on the 27B, one per
user**, where the old start was the 11–12 leading core tools, ~2,300 tokens, shared by all (`chat_window.shared_tool_count`,
still what a job, an index outage, or an engine that cannot be called by description gets). Check it worked with the
window's own line `hot start <file>: N tokens already read` and Kirk's first-output line, which says how many tokens
were `already in the engine` (the engine's own count). Design and proof: [[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]]
§ "The master start". **Mechanism** (`helper_window.HotStarts`, on all four factories: rigby's window runs it too since 2026-11-08): two renderings through the engine's own template,
the longest text they agree on is the start, the file is `hot-<model>-<ctx>-<sha16>.bin` named by that text, so users share it. Built
once while the chat waits on its user and its own state is saved. Restored only when that beats re-reading, by the sidecar's measured
`restore_s` and the turn's `read_tok_s`. Newest 8 per model and size.
**Why the answer stays bit identical to a cold read: the cut sits on the engine's read grid.** The engine reads a prompt in multiples of
`n_batch` from token 0 and its rounding depends on where those batches fall, so a start cut anywhere else changes the answer (measured on
Softserve's 9B, an engine that repeats itself: cold = cold, hot = hot, hot ≠ cold at the same character every run). So: tokenize the start
(`/tokenize`), cut at the largest multiple of `n_batch`, read that plus a throwaway tail of `min(n_batch, 4 + n_ubatch)` tokens from token 0
(`cache_prompt: false`, `n_predict: 0`). That tail makes the engine break a batch exactly at the cut and take its rollback checkpoint
there ("create checkpoints that many tokens before the end of the prompt", `server-context.cpp`), and a slot file carries its checkpoints. A
real prompt diverges at the first tail token, the engine rolls back to the cut, and it reads the rest in a cold read's batches. A start
shorter than one batch is not built. Grid: ollama's runner `-b 512 -ub 512` (read off its command line, `helper_window.runner_batch`);
rigby `-ub U`, `-b U` only above 2048 (`tolkien_llm.read_grid`). Strata skips a cut inside the tool list:
it pins at message boundaries and has no tokenizer endpoint. **rigby's real chat turns carry no tools or system message, so they have
no shared start to save; the mechanism is there if that changes.** Every answer row says what its turn started from (`state_from`:
`hot`, `own`, `in the slot`, or absent = read everything), and the window writes `HOT_BUILT.json` when it builds one. **Re-run the
proof:** `dump_real_tools.py` inside the orchestrator container for the tool file, a real system prompt as text, then `python
E:\SSDShared\Lab\bin\hot_start_proof.py <box> tools.json system.txt`. Results: [[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]].

### RIGBY CHAT WINDOW — rigby keeps its chat model warm between HAI turns (Tolkien `chat.window`, 2026-10-25)
GPU-factory parity (the owner): the GPU boxes serve chat through a window order (`helper_window.py`); rigby answered each turn
as a whole `llm` order (fresh server, control run). Now Tolkien has a `chat.window` kind on rigby's lane
(`tolkien_llm.run_window`, `tablet_runner.build_window_order`, embed-kind shape): one llama-server for the order, Kirk's
items read from the window folder on the jobs share (`/ssdshared/tabfoundry-jobs/chat-windows/<name>`, the same folder
Kirk writes at `/tabjobs/chat-windows`), each turn streamed as OpenAI chunks, ended by CLOSE, a silent owner, a stop slip
or idle past its rule while marked IDLE; server stopped on every route; holds rigby's lane while open. Kirk side: rigby
is an OpenAI address only the window door understands (`config.TOLKIEN_WINDOW_URL`, a loopback discard port so a bypassed call
fails at once), `chat_window._open_tolkien_window` sends the Fellowship-form desk order with the weighed row's own settings
(`tolkien_order`), and `app._tolkien_window_turn` serves a weighed rigby row through it with the bare priced turn — any
failure falls back to the whole-order Fellowship turn. **On per user, no redeploy:** `Z:\Documentation\State\
rigby-window.json` = `{"users": ["ClaudeSupervisor"]}` (or `["*"]`); absent = off.

**The serving is the GPU boxes' own program (built 2026-11-08, deploy pending at writing; foundry `ca82a8e`, Tolkien
`bb0a21d`):** `run_window` starts rigby's llama-server exactly as an llm order does (profile, vision, slot folder) and hands
the window to `helper_window.serve` (`a.llama`: an engine its caller started; baked into Tolkien's image with
`COPY --from=foundry helper_window.py infer_ollama.py gpu_spill.py`). Rows, chat states, hot start, park, loop rule, owner
and idle rules are the GPU boxes' (the owner heartbeat and the idle mark read on every pass on all four since `ac2528f`;
they had ridden the desk's 30 s timer); rigby's own are its launch, its read grid and its stop slips, which become the GPU
factories' YIELD file. **Chat-state retention is the fleet's rule** (five per user, older ones moved to the array, never
deleted) as soon as the array is reachable at `/bigshared/HAI/chat-slots` in the container; until then rigby keeps its newest
50, and the window's log line says which rule it is on. rigby mounts the array with `Claude Home\rigby_mount_bigshared.sh`
(the owner's step: fstab + the container volume, backups and read-back; the next Tolkien deploy picks it up). **A window feature is built once, in `helper_window`, and reaches
all four factories** — deploy the GPU boxes first (`fleet_deploy`), then Tolkien (`fleet_deploy_tolkien` refuses a foundry
file Mothership does not run). Wiring pin: `test_fleet_deploy.RigbyRunsTheFleetsWindowProgram`.

### LAB ENGINE on all four factories — `fleet_deploy --engine` (lossless mid-answer resume; engine parity, 2026-10-22)

**Since 2026-11-04 the REBUILD is the lab's, not a person's:** `Lab\bin\engine_pipeline.py` (task "HAI Lab Update
Stage Engine", 05:30, one phase per night) does the procedure below on its own when ollama moves its llama.cpp pin —
prepare (patches #0/#3/#2/#4/#5 + compat) → `labengine` build order on Mothership → BigHonker canary + resume probe → fleet
(+ rigby's `build_cuda114.sh <short>:<engine folder>`) → `fleet_deploy --check`. It stops for a person ONLY when a lab patch's anchor no
longer matches the new upstream (a rebase). State: `State\lab-updates\engine.json`; detail in
[[Lab-Update-Inventory-38]] § "The ENGINE pipeline". The hand procedure below stays as the reference of what it does.

#### Where the lab engine's patches and build scripts live: `E:\SSDShared\lab-engine` (git, since 2026-11-19)

The lab engine's source is one git repo, `E:\SSDShared\lab-engine` (its `README.md` lists every file). Before
2026-11-19 the patches sat in `Claude Home\research\split-inference` beside one-off research scripts, in no repo, and
rigby's build scripts existed only in rigby's `~/llama-build-rpcasync`; both moved here, and the old folder is empty.

| Folder | What | Who reads it |
|---|---|---|
| `patches\` | every lab engine patch: #0/#3/#2/#4/#5 (the fleet), the pre-Volta patch (rigby), the two Strata server patches (Flash-Next), the hot-store, profiler and async-RPC patches | `engine_pipeline.py` (`ENGINE_PATCHES_DIR`, default this folder) |
| top level | `apply_ollama_compat.py`, `make_revision_tree.py`, `build_cuda114.sh` (rigby's pipeline build), `ollama_slotpath.py`, the `submit_*.py` proof orders | the pipeline, or by hand |
| `rigby\` | rigby's own builds (hot store, async RPC incl. the tablets' arm64 build, the profiler) | `run_remote.py rigby rigby\<script>.sh` |
| `research\split-inference\` | #36's analysis tools (closed) | by hand |
| `strata\serve\` | the tests for the two Strata server patches | inside a patched Strata tree |

The source trees (`llama.cpp-<commit>`, `ollama-*-src`, `strata-*`) sit beside these and are git-ignored: they are
derived from upstream plus `patches\`. rigby's pipeline build is `build_cuda114.sh <short>:<engine folder>` (one
argument, `run_remote`'s `--args` takes no spaces); it reads the pre-Volta patch from `<engine folder>\patches`, which
the pipeline copies over with the tree, so it no longer depends on the copies in rigby's own build folder.

**STATE 2026-11-11 19:36 CT (`fleet_deploy --check`): all four factories serve `620d222-r5+compat`.** r5 = r4 + lab
patch #5 (`vk_fa_dead_cells_patch.py`: **Mothership's Vulkan attention no longer reads the engine's history** - the
coopmat attention shader loads zeros for the V of KV cells no row of the batch can see; before it, an engine that had
answered anything no longer repeated itself and a resumed 27B answer drifted. The patch changes one Vulkan shader; the
CUDA boxes carry r5 for one name everywhere). r4 = lab patch #4 (`slot_continue_patch.py`: a cut answer is CONTINUED
when the same request is asked again, [[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] § "The mid-turn dump on
the fleet boxes"). Build `labengine-620d222-r5-compat.20261111-174332`. Mothership went on last, after the owner ran the
operator checklist at its console (the section below); proofs on the serving engine are in that Quick-Resume doc's F2
row. Rollback per box: `fleet_deploy.py --engine-rollback --only <host>` (the r4 trees are kept). `--only` takes ONE
host: a comma list does nothing and prints nothing. Earlier state blocks:
`Archive\runbook-ollama-host-ops-superseded-20261105.md` and `…-superseded-20261111.md`.

#### Mothership operator checklist — the desktop shortcut for a new engine's firewall Allow, the dump key and the Acronis list

**"Mothership is HELD: the new engine tree has no firewall Allow rule"** (`fleet_deploy --engine` prints it) **is cured
by the owner double-clicking `Lab checklist (double-click me)` on Mothership's desktop** (2026-11-05, the owner: *"stick a shortcut
on my desktop on mothership for me to hit run as admin on and get my required checklist done"*). It asks Windows for
admin once and asks Y/N per item; nothing changes without a Y, and every run writes `checklist-<time>.log` beside the
script (`C:\ProgramData\HAI\operator\`). Source: `Claude Home\mothership-operator-checklist\` (script, Acronis list, the
shortcut); `-DryRun` shows everything and changes nothing.
1. **Firewall:** every `AI_Bundle\Ollama-*` tree whose `ollama.exe` has no inbound Allow gets a copy of the rules the
   serving engine has (two rules named `ollama.exe`, TCP and UDP, Private + Public); a tree with Windows' auto-made
   Block rules has them turned to Allow (the 2026-10-16 fix). First real run 2026-11-05 14:46: r4 got its two rules,
   read back; `fleet_deploy --engine … --only mothership` then went through without a hold.
2. **Dump key:** `CrashOnCtrlScroll = 1` under `kbdhid` and `i8042prt` (set 2026-11-05; live from the next restart;
   dump type on the box is kernel). On the next freeze: hold Right-Ctrl, press Scroll Lock twice.
3. **Acronis:** opens `acronis_exclusions.txt` and Acronis side by side (its exclusions are UI-only).
   **2026-11-05: the owner turned Acronis Active Protection OFF for now**; the exclusions go in piece by piece later.
Claude does not write these itself (its own line on a machine's security settings; the owner, 2026-11-11: calling it his
rule "is a misattribution"): when a lab change needs one, it updates the script or the list and says so.
**An engine can be approved BEFORE it is installed** (2026-11-11): a folder name in `upcoming_trees.txt` beside the
script (`C:\ProgramData\HAI\operator\`, source `Claude Home\mothership-operator-checklist\`) shows up in item 1 as
"NOT INSTALLED YET: named ahead by the lab" and gets the serving engine's rules on a Y, so the switch itself needs
nobody at the box. First use: `Ollama-0.40.5-lab620d222-r6`, approved 23:59 CT, two rules read back; that revision was then not cut (its patch was no faster than r5), so those two rules name a folder that does not exist and the list is empty again.
The other route for the firewall alone: `fleet_deploy --engine … --engine-operator-present` switches a box that has no
rule while the owner is at its console, waits 30 minutes for his click on Windows' own prompt, and rolls back without it.

**A LAB REVISION is its own engine tag** (2026-11-05): a new lab patch on the same upstream commit gets its own source
tree `lab-engine\llama.cpp-<short>-r<N>`, built with `python lab-engine\submit_labengine.py <short>-r<N>`, installed as
`Ollama-0.40.5-lab<short>-r<N>` beside the stock tree, and built on rigby with `build_cuda114.sh <short>-r<N>`. One name
on all four factories (`fleet_engine.TAG_RE`). Make the tree by running the patch script on a fresh copy of the base
tree, never by editing a tree a live build came from: patch #4's first build was made in `llama.cpp-620d222` itself and
came out tagged with the name of the tree every box was serving.

A lab build MUST be: upstream at ollama's pinned commit (`LLAMA_CPP_VERSION` in ollama's source:
0.40.5 → `b11669` = `620d2225c`) + ollama's **`llama/compat/`** layer (one hook patch into `llama-model-loader.cpp` and
`mtmd/clip.cpp` + four sources linked into `llama`; it translates ollama-format GGUFs in memory at load) + the lab
patches — then re-fit every box (traps table) before trusting it. **`E:\SSDShared\lab-engine\apply_ollama_compat.py
<tree> <ollama src>\llama\compat`** does the compat part exactly as ollama's `llama/server/CMakeLists.txt` does
(idempotent; handles a CRLF tree without `.git`, as rigby's copy is); ollama 0.40.5's source is in
`E:\SSDShared\lab-engine\ollama-0.40.5-src\`. `build_lab_engine.py` asserts the layer in `llama.dll`
(`llama_ollama_compat` symbols: stock `libllama.dll` 65, the first lab build 0) and rigby's recipe prints its count.

**What runs:** ollama 0.40.5 bundles upstream llama.cpp `620d2225c` + ollama's patches. The lab builds that SAME commit +
lab patches #0 (pending token), #3 (`lab_resume`, MTP carry) and #2 (sampler RNG) — rigby's B11 patches — paths:
BigHonker/Softserve `…\Programs\Ollama-0.40.5-lab620d222` (CUDA 13, sm_86), Mothership
`…\AMD\AI_Bundle\Ollama-0.40.5-lab620d222` (Vulkan), rigby `/llama/cuda` (CUDA 11.4, sm_61; see
RUNBOOK-GT730 § "Which tree is LIVE"). The GPU boxes' tree = a copy of the stock tree whose `lib\ollama` holds no stock
engine file (a clang-built plug-in beside the MSVC core would load into it) + the lab set, every file sha256-checked.
**Build** = a desk order (`labengine-*`, payload `workspace\labengine\build_lab_engine.py`, MSVC + CUDA 13 + Vulkan on
Mothership; asserts `AVX2 = 1` and the lab markers). rigby builds its own (`lab-engine\build_cuda114_620d222.sh`).
**Deploy / check / roll back — the only door (`mothership-build\fleet_engine.py`):**
- `python fleet_deploy.py --engine <delivered labengine order dir> [--only host]` — GPU boxes: install the versioned
  tree, repoint the launcher (backup `*.bak-<stamp>-pre-lab<tag>` = the rollback), restart ollama under the lane's own
  stop/requeue slips, killing only processes from the OLD tree (llama-swap's Flash-Next is untouched); rigby: copy the
  build into the mounted bin (refuses with `WAIT` while a CUDA llama process runs; archives the live bin as
  `/home/owner/llama-cuda114-bin-archive-engine-<stamp>`; byte-compares after).
- `python fleet_deploy.py --check` — prints `lab=` and **`tag=`** per factory and `ENGINE DRIFT` when the tags differ or
  one cannot be read. Markers alone are not parity: rigby's pre-parity e467e5b build carried them too.
- `python fleet_deploy.py --engine-rollback [--only host]` — launcher back to its newest pre-lab backup / rigby's newest
  `…archive-engine-*`.
**Proof** = `lab-engine\submit_resume_probe.py <host> <model> <num_ctx>` (GPU boxes, payload
`workspace\labengine\engine_resume_probe.py`: cut → slot save → evict + reload → restore → `lab_resume` continue vs a
control without it) and `lab-engine\submit_rigby_resume_proof.py` (Tolkien's dump probe). Results 2026-10-22, each with
the uninterrupted answer as reference and the same restore WITHOUT `lab_resume` as control:
| factory | model | verdict | lab_resume | control |
|---|---|---|---|---|
| BigHonker (compat build) | qwen3.5:9b @ 19,968 | **LOSSLESS** | read 0 tokens, identical | diverges at 61 (cut 60) |
| Softserve (compat build) | qwen3:8b | **LOSSLESS** | read 0 tokens, identical | diverges at 61 |
| rigby 1060 (compat build) | Qwen3.6-35B-A3B MTP, temp 0.6 (B11's r10b spec) | **LOSSLESS** 511/511 | read 1 token | old path 210, re-read 207 (= 10-21 to the token) |
| rigby 1060 (compat build) | same, temp 0 (r9c spec) | **LOSSLESS** 383/383 | read 1 token | old path 358, re-read 169 (= 10-21) |
| Mothership (compat build) | qwen3.8:27b @ 122,880, MTP on, `--fresh-load --hang-up` (2026-11-05) | **LOSSLESS** in all 5 arms, 160 tokens: temp 0.6 at cuts 34, 63, 65, 94; temp 0 at 67 | read 0 tokens | temp 0.6 diverges 1 to 11 tokens past the cut; a whole second run from a fresh load matches 160/160 |
| Mothership, cut by asking for exactly `cut` tokens | same | **Do not read as a resume result.** temp 0.6 drifts at a repeatable token (105 at cut 60, three times), temp 0 stays lossless. The cut limits the last drafted batch, which no real stop does. Measured: the cut method; inferred: rounding that follows batch shape | | |
| Mothership, no `--fresh-load` | same | **VOID**: a second run in an already-used engine differs from a fresh one before the cut (token 15 on 10-22, 27 on 11-05) | | |
**Run the MTP boxes' proof with `--fresh-load --hang-up`.** `lab_resume` runs only in rigby's whole orders, in no chat window: a stop slip finishes the turn first and saves, and a crash or forced kill leaves no save, so that turn is asked again ([[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] § Status). Compare tokens: with drafting on, the server records candidates for almost no token, and the probe's `rows_with_numbers` says so.
(BigHonker/Softserve also proved LOSSLESS on the 07:34 build before the rollback.)
The probe says `VOID` when the cut run differs from the reference before the cut and `STOCK ENGINE` when the save carries
no lab fields — never "NOT LOSSLESS" for a comparison it could not make.
⚠ Three traps the deploy hit: a bare `cmake` in Python's `subprocess` resolves with the PARENT's PATH (use full paths);
a `shutil.ignore_patterns("build*")` also drops `common/build-info.cpp.in`; text-mode stdin from Windows sends CRLF and
bash on rigby read `set -eu\r` (send bytes).

### McCoy's model lives on the NVMe — a SATA model store made his cold load time out (2026-10-21)

**Model stores by disk:** BigHonker `C:\Users\<user>\.ollama\models` = WD_BLACK SN770 **NVMe** · Softserve
`E:\ollama-models` = PNY CS900 **SATA** SSD (C: is the WD SN570 NVMe) · Mothership `E:\Ollama\Models` = Samsung 860 EVO
**SATA** SSD, 235 GB (C: T-FORCE NVMe 207 GB free, F: PNY CS2241 NVMe 28 GB free — the whole store does not fit).
**Measured on Softserve beside two Plex streams:** McCoy's (tev1) cold load ran past his 30 s call clock twice
(`Load failed … context canceled` at 26–27 s); a plain read of his 4.6 GB file took **17.2 s from the SATA SSD (268
MB/s) vs 1.3 s from the RAM cache**; with the file cached the load was **3.0 s, call 5.9 s**. ollama's `/api/ps` shows
NOTHING while a model loads (polled every 0.2 s on BigHonker), so there is no loading status to supervise on — the cure
is a fast load. **Done:** his blob `sha256-50696f76…` copied to `C:\ollama-nvme\blobs\` (sha256 = its name) and the
store path replaced by a file symbolic link to it, on Softserve (proven: load 3.0 s, call 4.1 s through the link) and
Mothership (linked; load not yet proven — the 3.8 was resident). Originals kept as `blobs-bak-mccoy-20261021\` (Softserve)
/ `blobs-bak-nvme\` (Mothership) — re-pullable, removable once the owner says. **Tool:** `Lab\bin\model_to_nvme.ps1 -Store
<store> -Nvme C:\ollama-nvme -Blob sha256-<hex> -Model <name>` — refuses while the model is loaded or when the copy does
not hash to its name. ⚠ **Creating a symbolic link needs the ELEVATED token:** run it through `ssh <box>` (RUNBOOK §
ELEVATION) — on 10-21 a local run renamed the original and then failed the link, leaving McCoy without a model for ~3 min.
**2026-10-25 — the SERVED models moved to the NVMe on Softserve and Mothership** (the owner: *"if there's enough space that
seems obvious to put them on the NVME's for each device"* — his OK for the "models already placed stay put" rule above):
Softserve all 13 remaining store models → `C:\ollama-nvme` (52 GB of 230 GB free; tev1:0.8b refused while tev1 was
loaded — the tool's name match), Mothership the six it serves (qwen3.8:27b, qwen3-coder:30b, qwen3:14b, qwen3.5:9b,
qwen3:8b, nomic-embed-text; ~58 GB of 195 free) → `C:\ollama-nvme`, each by `model_to_nvme.ps1` (hash-verified, symlink
in the store, original kept in `blobs-bak-nvme\` until the owner says). BigHonker's store was already NVMe. ⚠ Mothership's
FIRST qwen3:14b copy hashed WRONG (same size, different bytes; the SATA original good) — kept as
`C:\ollama-nvme\blobs\sha256-a8cc…40e.bad-20261025-144042` for diagnosis; the re-copy verified and was linked. One bad copy
of six on a box that has had hard hangs: worth watching (RAM / NVMe), not concluded. **Measured cold loads** (`--cold`
speed checks, desk orders, 2026-10-25): qwen3.8 on Mothership **13.1 s** (policy's prior SATA price 45.4 s), qwen3.5:9b on
Softserve **13.5 s** (prior 23.4 s) — ⚠ confounded: the copy + hash had just read those files, so part may be the RAM
standby cache; a load after the cache has turned over separates it.

### LIVE tok/s — "what's the current rate?" for whatever a box is generating right now

**For factory work, read the tile.** Each factory tile on the homepage has a **Speed** row (deck field `tok_s` on
`GET /p20/factory/{host}`): the median decode rate of the last ten answers of the order in flight, on all four
factories. The payload prints each answer's rate (GPU: ollama's `eval_count / eval_duration`; Tolkien: llama-server's
`predicted_per_second`), the factory appends `· N tok/s` to the phase it publishes, and the desk parses it. Blank =
nothing generating, or the work is not an LLM (training, renders).
**The rate is a FACTORY feature, not the order's (2026-10-16, the owner: "It's supposed to be features of the factory,
not features of the orders").** When a GPU payload prints no rate (the Flash-Next ladder never did, so its runs showed
a blank Speed row), the factory reads the serving engine's own `print_timing … eval time = … tokens per second` lines
itself: the box's ollama serve log from the order's start, and every `*.log` in the order's output dir (where a
direct llama-server's log lives) — `p20_foundry.EngineRates`, same parser rule as `live_rate.py` (answers under 20
tokens skipped). A payload's own printed rate still wins. Proven live: `decoy-factory-toks-silent` (prints nothing)
showed `59.3 tok/s` on BigHonker's tile. Tolkien launches its own llama-server and already reads the engine.

**Comparing two arms' speed (a clock, an engine, a setting):** the ollama prompt cache serves any prefix it has
already read, so two orders with identical prompts on a loaded model are one measurement and one cache replay — the
second arm's read rate is inflated (2026-10-16: +50 % that way, +22 % salted and cold). `hard-bench\submit_speedcheck.py`
salts every prompt with its stamp by default and takes `--cold` (evict first) and `--items=` (e.g.
`make_longread_items.py`, long documents where reading IS the measurement); an order declares its clock with
`--gpu-clock=unlocked|<MHz>` and the factory applies and restores it. Treat each arm's first row as warm-up.

### NVIDIA card PARKS at ~450 MHz during a MoE-offload decode (experts in RAM) — slow tok/s, low GPU util, P3

**Symptom:** a model split between card and RAM (ollama's fitter, `--n-cpu-moe`, any MoE with experts in system RAM)
writes far below what the box should do; `nvidia-smi` during the answer shows `clocks.sm` ~400–550 MHz, `pstate P3`,
`clocks_throttle_reasons.active 0x1` (GPU idle), util 30–40 %. **Cause:** the decode alternates CPU (experts) and GPU
(attention) ~30 times a token, the card idles between bursts, and the Windows driver clocks it down — every one of
the ~4,000 small GPU ops per token then runs at a quarter of its speed. Measured on BigHonker's 3070, qwen3.6 35B-A3B,
2026-10-17 (#34 B10): unlocked 20.3–21.7 tok/s, `gpu_clock 1965` 25.3–26.6, same config, alternated in time. A
fully-resident model keeps the card busy and does not show it. **Fix:** keep the clock from parking — an order sets
`gpu_clock` (the factory applies `nvidia-smi -lgc` and restores the box state after). **DECIDED AND LIVE 2026-11-14 (the owner: "a clock floor for work is a good idea"): BigHonker's chat WINDOWS hold a clock
FLOOR while they serve** — `config.WINDOW_GPU_CLOCK = {"bighonker": {"floor": 1965}}` (Kirk), applied by the factory
as `nvidia-smi -lgc 1965,<card max>` and restored to stock boost when the window ends. `gpu_clock` now has three forms
(the owner: *"a clock floor versus a clock lock versus a clock ceiling"*): a number = LOCK, `{"floor": N}`,
`{"ceiling": N}`, or both (`p20_foundry._clock_args`). Live proof: window for bh-c, factory log `-lgc 1965,2100: ok`,
card 1950–1980 MHz P2 at 38–141 W while serving (180 W cap untouched). The text below is the history that led here.
For SERVING on BigHonker there
was **no box-level clock lock before this**, and the two settings answer DIFFERENT problems: the `BH GPU Clock Lock` (a 1500 MHz
CEILING against crashes) was retired 2026-10-16 to get stock boost back on the new PSU (the owner: *"Let's unlock BH baby"*;
[[BigHonker-3070-DPC-Watchdog-Investigation]]), and the 180 W POWER LIMIT (2026-10-21) stops dense-decode crashes at
~240 W. **Neither addresses these dips.** A clock FLOOR (`-lgc <min>,<max>`) for the MoE parking was measured (+20–25 %:
25.3–26.6 vs 20.3–21.7 tok/s), proposed 10-17/18, and is **still the owner's open call** — never ruled on, never rejected
([[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] B10, `:1009-1011`; the owner 10-18: *"idk, I'm surprised that we even have to mess
with these settings and it doesn't auto manage"*; NVIDIA "Prefer maximum performance" was the alternative named, not
recorded as applied). Evidence for it: the A3B ran 6.6 h continuous at 1995 MHz / ~115 W on 10-18 (investigation
`:927`), well under the 180 W cap; a floor WITH the cap is untested. Until he decides, a served A3B decode on BigHonker
runs at the parked rate (~20–22 tok/s). (Corrected 2026-11-14: an earlier line here said "the owner's settings — do not swap
in a clock lock"; that was Claude's wording, not his.) Measure the clock **in-process
(NVML)** while timing: spawning `nvidia-smi` twice a second on a 6-core box cost the same arm ~10 % (23.5 vs 26).
The tuned A3B launch for BigHonker and its numbers: [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] § B10.

For anything ollama serves, factory or not (HAI chat included):

```bash
python E:\SSDShared\Lab\bin\live_rate.py mothership      # or bighonker; last 10 answers + median tok/s
```

It reads ollama's own per-request line in the serve log (`slot print_timing: … eval time = X ms / N tokens (… T
tokens per second)`), so T is the real decode rate of each answer, live, for any order or chat. Answers under 20
generated tokens are skipped (probes and 2-token replies are mostly overhead). Logs: Mothership
`C:\Users\<user>\AppData\Local\Ollama\serve.log`, BigHonker and Softserve `C:\ProgramData\HAI\ollama-serve.log`
(Softserve since 2026-10-06). Exit 3 with "no generation served" means the request log shows only metadata calls,
not a broken log. Mothership's log carries the `print_timing` lines on ollama 0.40.5 (verified 2026-10-16: the A1
run read 134–142 tok/s); before the upgrade it had none and the tool reported UNREADABLE there. The deck's `rate` is
seconds per unit, not tok/s.

### Ollama HUNG — answers time out, card idle, no error (Mothership 2026-10-02) — detect, restart the TREE

**Signature:** `/api/version` and `/api/ps` answer and the model reads fully resident, but generations never produce a
token: a factory order logs `[infer] N/M … ? 600.0s ERROR TimeoutError: timed out` every 10 minutes, ollama's log shows
only `aborting completion request due to client closing the connection`, and the GPU telemetry reads IDLE (single-digit
watts, ~0% load) — **not** the wedge signature (220–250 W). **What is known (2026-10-02, two events, both
`qwen3.8:27b` on ollama's Vulkan backend with MTP drafting):** 18:2x on a tool-call cell (thinking on) and 20:0x on the
first answer of a 2× vision cell. Both times the runner STARTED the answer normally and stopped partway with no error
and no finish line; card AND CPU sat idle (the engine was waiting, not computing); no driver/TDR events; the stuck
runner stalled every later request until it was replaced (the first by a restart, the second by ollama's own idle
unload + reload at the order's retry). Not a length ceiling (600+ answers ran longer). ~2 stalls in ~5,500 requests;
never seen on ROCm, nor in the few dozen direct-`llama-server` Vulkan bench answers (MTP draft 2/3 there; ollama forces
draft 4, keeps an 8 GB prompt cache with checkpoints, and runs at a 512 MB VRAM margin). Leading hypothesis: the
llama.cpp Vulkan + MTP/speculative hang reported upstream on AMD — unproven until a stall is captured. The next one is
recorded automatically (§"STALL RECORDER" below). ROCm rollback: [[34-A-OLLAMA-UPGRADE-GATE]].
**Why the factory did not catch it (fixed):** each silent answer became an error ROW, and rows read as progress, so the
order looked slow, not stuck. Since 2026-10-02 `infer_ollama` probes the engine (4 tokens, ≤180 s) after any silent
answer; a silent probe = **engine hung → exit 2 (held, not failed)**, the silent answer is not recorded (the retry re-asks
it), and the foundry DMs and retries the order every REFUSED_RETRY_S. A silent answer is never a `priced_out` row.
**Recovery:** archive the log (`serve.log` → `serve.log.hang-<stamp>`), then **stop the whole TREE**, not just
`ollama.exe`: `taskkill /PID <port-11434 owner> /T /F` — `Stop-Process` on `ollama.exe` leaves its `llama-server` child
alive (2026-10-02: the frozen runner survived as an orphan holding VRAM until the GPU police arrested it). Then
`Start-ScheduledTask 'Ollama Serve'` and prove a real answer (load at 122,880, `size_vram == size`, tokens/s).
**Before any recovery, let the recorder finish** (its DM arrives ~1 min into the stall): a restart destroys the only
live evidence.
**A second shape, seen once (2026-11-04 13:33–13:42, Mothership):** the SERVER blocks, not a generation — `/api/version`
and `/api/ps` return no HTTP reply for 40 s while `/api/tags` still answers, with NO model loaded; the GPU telemetry
logger stalled over the same minutes and one ssh banner exchange timed out; llama-swap on :11435 stayed responsive.
It began the minute a 4-hour Flash-Next window (Strata through llama-swap) ended and cleared by itself after ~8 min.
HAI read it as "Mothership did not wake" and overflowed to the 8 GB tier. **Resolved the same afternoon: not an
ollama fault at all** — it is the box's system-volume stall ([[RUNBOOK-GPU-Host-Power-and-Glances]] § sleep-crash,
11-03/11-04 entries): ollama's HTTP was queued behind a C: lock along with sshd, the telemetry logger and the UPS agent,
while a long-running F: writer never missed a row. Do not restart ollama for this shape; read the telemetry gaps and
the PowerPanel 4100 canary. Journal: 2026-11-04-claude-ltt-pitch.

### STALL RECORDER — evidence when an ollama runner stalls (Mothership, `HAI Stall Recorder`)

`C:\ProgramData\HAI\hai_stall_recorder.py` (mirror `E:\SSDShared\mothership-build`, tests
`test_hai_stall_recorder.py`), SYSTEM task **`HAI Stall Recorder`** (15-min repeat + boot trigger + IgnoreNew + restart,
installed by `install_stall_recorder_task.ps1`). Every 2 s it reads each runner's own `/slots`; a slot that is
processing with no counter moving (task, prompt tokens, prompt processed, `n_decoded`) for **60 s** is a stall (a
512-token prompt batch is ~0.7 s and a token ~12 ms, so 60 s is ~85× the slowest unit of progress). It **records and
DMs once per stalled task, and never kills, restarts or retries** — the factory's hung-engine guard holds the order.
Evidence lands in `C:\ProgramData\HAI\stall-recorder\<stamp>-pid<pid>-task<id>\`: `summary.json` (what was saved, card
state), the 10-minute lead-up of slot samples, three more during the stall, a **minidump of `llama-server` with every
thread's stack** (Windows `dbghelp`, no tools to install; open with WinDbg/`cdb`), per-thread wait states twice,
`/props`, `/health`, ollama `/api/ps` + version, the serve-log tail, 30 min of GPU telemetry, the foundry queue log,
System/Application events, GPU driver + `vulkaninfo --summary`, and the launcher. Heartbeat:
`stall-recorder\heartbeat.json` every poll; log `stall-recorder\recorder.log`. Proof: `--decoy` serves a fake frozen
runner and runs the real capture + DM, tagged DECOY (passed 2026-10-02 20:47: 17 files, 1.5 MB dump, DM sent).
Self-healing proven the same night: killed at 20:48:36, back on its own at 21:00:01 (new pid, fresh heartbeat).
**Its process query costs nothing between changes (2026-11-05).** The recorder used to start a PowerShell on every 2 s
poll to read the model servers' command lines: 25 of the 47 process starts a minute on Mothership, each inspected by
the box's file-filter stack. It now takes the pids from a process snapshot inside itself and asks only when that set
changes (census after: 20 a minute, none from the recorder). **Deploying it:** the live copy is this ProgramData file,
not the foundry tree, and `fleet_deploy` restarts nothing that holds it (`verify_deploy.py` reports STALE). Back the
live file up, place the mirror's file, compare hashes, end the old pid, `schtasks /run /tn "HAI Stall Recorder"`, and
read `stall-recorder\heartbeat.json` for a new pid and a fresh `ts`. To see what starts processes on a box for one
minute: `powershell -File C:\ProgramData\HAI\operator\spawn_census.ps1` (read-only).

### BigHonker ollama serve log location + runner-crash "CUDA error: shared object initialization failed"

**Serve log:** `C:\ProgramData\HAI\ollama-serve.log`, UTF-8, appended by `C:\ProgramData\HAI\start-ollama-system.ps1`
(the launcher the SYSTEM task "Ollama Serve" runs — `schtasks /query /tn "Ollama Serve" /xml`), via
`cmd /d /c "... serve >> log 2>&1"`. The pre-2026-09-27 log is `C:\ProgramData\HAI\ollama-system.utf16-until-20260927-160059.log`
— UTF-16LE, so read it with Python (`decode("utf-16-le")`), never `findstr`/`grep`. Each crashed runner also
leaves an **Application** log event (`Application Error` 1000, `llama-server.exe`, `ucrtbase.dll`, `0xc0000409`);
the System log shows nothing for it.

**The crash signature:** `/api/chat` returns HTTP 500 after ~15 s with body `llama-server process has terminated:
exit status 255: CUDA error: shared object initialization failed`. In the serve log the runner aborts at
`common_init_: warming up the model` (`ggml-cuda.cu:106`), and the spawn shows `load_tensors: CPU model buffer`
/ `llama_context: CPU output buffer` where a healthy spawn shows `CUDA_Host` — the pinned host allocation
already failed. It happens only at runner SPAWN (every model or `num_ctx` change is one, since
`OLLAMA_MAX_LOADED_MODELS=1`; Kirk's nomic embeds evict the 9B), never on a warm model.

**Cause:** Windows commit-charge exhaustion at spawn. BigHonker has 16 GB RAM; a 9B spawn needs several GB
of commit headroom for its pinned buffer. With the system-managed pagefile at ~2 GB (its state from the
2026-08-18 move onto its own UPS, mechanism unproven) headroom fell below 540 MB at every spawn.

**Current configuration (the fix):** fixed pagefile `C:\pagefile.sys` 16384–32768 MB,
`AutomaticManagedPagefile=False` — commit limit ~31.9 GB. Check it:
`Get-CimInstance Win32_PageFileSetting` and `Win32_OperatingSystem` (`TotalVirtualMemorySize`,
`FreeVirtualMemory`). The Diagnostic Policy Service svchost has been seen at 3.3 GB after weeks of uptime
(~80 MB after a boot; ~50 MB on the peers) — watch it; `Restart-Service DPS` frees it without a reboot.

**If the crash returns:**
1. Preflight, read-only: `quser` (a locked console — LockApp/LogonUI running — means no one is acting),
   `F:\foundry\var\state\CURRENT_RUN.json` absent, `F:\foundry\jobs\queue` empty, `nvidia-smi` idle,
   `/api/ps` empty, desk `accounting` idle, `/hai/inflight` bighonker 0.
2. Check the pagefile setting and commit headroom above; restore the fixed pagefile with CIM
   (`Set-CimInstance Win32_ComputerSystem AutomaticManagedPagefile=$false`, then `Win32_PageFileSetting
   C:\pagefile.sys InitialSize=16384 MaximumSize=32768`), saving the old values first.
3. Reboot immediately (`shutdown /r /f /t 0`) when no one is on the box; verify by `LastBootUpTime`.
4. The foundry starts before ollama, so its first check-in may lack `model.*` capabilities; it corrects
   within a minute (read `fleet-run-state.json` `capabilities` as a list — `/p20/factory/<host>` returns it
   as a string).
5. Prove it through the factory, never raw curl on the box (that reads as UNACCOUNTED load): a compute order
   of `infer_ollama.py` whose items alternate `options.num_ctx` 6144/4096 so every row is a spawn; require
   0 crashes over ≥15 rows.

### SSH access (from Softserve)
- BigHonker: `ssh -i C:\Users\<user>\.ssh\bighonker_ed25519 <user>@<bighonker-address>`
- Mothership: `ssh -i C:\Users\<user>\.ssh\mothership_ed25519 <user>@<mothership-address>` (<user> = admin)
- Both run OpenSSH Server, auto-start on boot. **Security TODO:** private keys were transferred via the
  `\\<softserve-address>\Media\software\...` share — delete those share copies (locked copies live in Softserve's `.ssh`).

### Mothership sleep, keep_alive & keep-awake watcher

Mothership sleeps (S3) when idle to save summer power; HAI wakes it via WoL on demand. Cold 27B load ≈ **18s**
(models are on SSD, not the HDD). **The mechanism this guards against:** after an unattended WoL wake Windows
applies the hidden *"System unattended sleep timeout"* (`UNATTENDSLEEP`, stock **2 min**) INSTEAD of the normal
idle timer — an unfixed box sleeps ~2 min after each run and dumps the resident model (S3 clears VRAM),
defeating Ollama's keep_alive.

**Fix (3 layers, all live + verified):**
1. **`UNATTENDSLEEP` AC → 30 min** (the "ready" window; return within 30 min = no new WOL/HDD spin-up):
   `powercfg -attributes SUB_SLEEP 7bc4a2f9-d8fc-4469-b07b-33eb785aaca0 -ATTRIB_HIDE` (unhide), then
   `powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP 7bc4a2f9-d8fc-4469-b07b-33eb785aaca0 1800 && powercfg /setactive SCHEME_CURRENT`.
2. **Mothership Ollama `keep_alive` → 5m** via orchestrator `config.LEAD_BOX_KEEP_ALIVE` (named `MOTHERSHIP_KEEP_ALIVE` until 2026-11-16) +
   `KEEP_ALIVE_OVERRIDES[HEAVY_HOST]` (model unloads ~5m after use → frees 17 GB VRAM for other PC use; reload ~18s).
3. **keep-awake watcher** `C:\ProgramData\HAI\keep-awake.ps1` — SYSTEM scheduled task **"HAI Keep-Awake"**
   (AtStartup, unlimited runtime). Polls `localhost:11434/api/ps` every 30s; asserts
   `SetThreadExecutionState(ES_SYSTEM_REQUIRED)` while a model is resident, releases on unload — so the OS never
   sleeps mid-run and an active session extends past the 30-min ceiling. Log: `C:\ProgramData\HAI\keep-awake.log`.
   Verify: `powercfg /requests` (SYSTEM section shows powershell.exe while a model is loaded). Manage:
   `Get-ScheduledTask "HAI Keep-Awake"` / `Start-ScheduledTask` / `Stop-ScheduledTask`.

**Net behavior:** WOL wake → ready 30 min → model drops ~5 min after last use → never guillotined mid-run.
The hard HDD spin-up the owner hears is the S3 *resume* itself (inherent — drives lose power in sleep) → the lever is
waking LESS often (30-min window + Phase-2 routing that doesn't wake Mothership for trivia).

**`gpu-free.ps1`** (`C:\ProgramData\HAI\gpu-free.ps1`) — prints `{"total_mb","used_mb","free_mb"}` from the Windows
GPU Performance Counter (`\GPU Adapter Memory(*)\Dedicated Usage`, max instance) + registry `qwMemorySize`. There
is **no amd-smi/rocm-smi** on Mothership (consumer Adrenalin driver, not ROCm dev stack — Perf Counter needs zero
install). Used by the orchestrator's gaming-busy check: `free_mb < ~19000` (27B needs ~17 GB) ⇒ a game holds the
GPU ⇒ route to BigHonker, don't disturb the game.

> **Routing (since 2026-10-22/23, #34 — the HAI 1.0 ladder this note once described is archived in
> `Archive\hai-1.0-routing-docs-history-2026-10.md`).** Every Auto turn: McCoy (the advisor, tev1 on Softserve, 0.2–0.4 s)
> triages it — tier, chat vs job, family, how sure (unsure ⇒ the lead) — and Spock places it from the priced menu by the
> per-request weighing (quality band → interactive line → home → energy → time, the hour's electricity price on every
> row); the turn runs as a factory WINDOW order on the placed box at the row's model and settings. Simple turns McCoy is
> sure of go to the Fellowship (rigby through Tolkien, a desk order ~9 s); the 8 GB boxes are mid-weight and overflow;
> the lead is Mothership (qwen3.8:27b / Flash-Next by job family). The same controls for every user — Model = `auto` ·
> `quick_8b` (Quick: a WEIGHT in the same weighing, speed heavy, a better model about as fast still preferred - D2a,
> 10-25) · `force_27b` (the 27B pin, honoured for everyone since 10-23; `MOTHERSHIP_FORCE_USERS` retired); a pin to
> a busy Mothership QUEUES with a notice (place, rule, the holder's time, a rough ETA from holds the slot has seen) and
> a reroute offer with the table's caveat, no give-up clock (`MOTHERSHIP_QUEUE_TIMEOUT` retired 10-23). HAI's per-turn
> embeds are placed from the menu's `embed` family (10-23). Design: [[HAI-Agent-Surface-Design]] § "The design";
> [[34-Starfleet-Routing-and-HAI-Controls-Do-Not-Collapse-Checklist]]; [[34-Phase-7-Placement-Policy-Design]].
>
> **Gaming-busy guard:** before loading the 27B onto an AWAKE Mothership, `_ensure_lead()` first checks
> `mothership.warm()` (the `qwen3.6:27b` family already resident = GPU is ours, not a game → proceed); otherwise
> it SSHes in and runs `gpu-free.ps1` (`lead_ssh.gaming_busy()`; `mothership.py` until 2026-11-16); if `free_mb < PC1_MIN_FREE_MB`
> (19000) **AND no Ollama model is resident**, a non-Ollama process (a game) holds the GPU → don't load, route
> to BigHonker. `gaming_busy()` treats a resident Ollama model as **swappable, NOT a game** — a DIFFERENT
> resident Ollama model (`medgemma:27b`, `qwen3:14b`) also holds VRAM low, and reading that as a game wrongly
> routes away and errors nursing users. So: low free + an Ollama model loaded ⇒ proceed (Ollama swaps it for
> the 27B); only low free + NO Ollama model ⇒ a real game. Asleep = not gaming → WoL as normal. Fail-open if
> the probe can't reach. Container has `openssh-client` + key `/run/secrets/mothership_key`.
> **No lingering auxiliary model:** the *only* non-`qwen3.6` 27B ever loaded on Mothership is the optional
> MedGemma medical-critique pass, and that call passes **`keep_alive="0"`** (per-call override on
> `ollama_client.chat`) so MedGemma unloads immediately rather than sitting as the "resident" model —
> eliminating the confusing condition at its source; the model-aware `gaming_busy()` is the backstop.
> **Nursing-friendly errors:** when the 27B genuinely can't be reached, nursing users get a plain message
> (`_heavy_unavailable_msg`) — "the deeper model that powers EBP Research/Educator is busy, try again or switch
> to Reference/Tutor" — instead of the homelab "Mothership/gaming PC/Normal-Quick" jargon.
>
> **Serialization:** only ONE request may touch Mothership's 27B at a time — a single
> `threading.Lock` ("Mothership slot", `_lead_slot`) held across *ensure + dispatch* in
> `_dispatch_to_lead` (and the temp-brain path). Concurrent heavy requests would otherwise double-WoL the
> box and thrash the model in/out of VRAM (the medical-critique swaps make that worse). A 2nd heavy request
> waits for its turn — the user sees its place, a rough ETA and a reroute offer; no give-up clock since 10-23
> (`MOTHERSHIP_QUEUE_TIMEOUT` retired) — and finds the 27B already warm (faster, no 2nd wake). 8B
> brain requests never take the lock.

### Orchestrator design (BUILT — this is the realized architecture, not a plan)
- FastAPI service on **SundaeStackarr** (Docker, always-on, CPU-only — no model itself).
- Routing: rules -> **BigHonker** (primary brain) -> **Mothership** (heavyweight / preferred failover) -> **Softserve** (absolute last resort) -> **Claude**.
- Brain = reasoning agent w/ tool-calling: `search_docs`, `get_credential` (gated), `ask_helper`→a peer
  llama/qwen, `escalate_to_claude`. RAG from `Z:\Documentation\Reference\` (secrets gated in `Credentials\`).
- **🔒 Claude = explicit user request ONLY** (never auto-escalate — cost guardrail).
- **Security:** Ollama `:11434` is unauthenticated → LAN-locked to the orchestrator host, **never** exposed to
  the tailnet. Tailnet user access happens through the orchestrator front door (with auth) in a later phase.
- Status frames ("🧢 thinking cap…", "📟 waking…", "🏋️ heavyweight…") surface slow paths to the user.

### Home AI handoffs and long-running requests
- Treat Home AI as one assistant. Users normally ask for the outcome without naming a host; the orchestrator
  chooses BigHonker, Softserve, or Mothership internally.
- Machine names are optional routing overrides. Phrases such as `ask Mothership`, `work with Mothership`,
  `do it on Mothership`, or `Mothership, ...` deterministically route to the 27B model instead of relying on
  the 8B brain to decide whether to call an escalation tool.
- Heavyweight prompts include an internal continuation brief: Mothership is a component of the same Home AI,
  receives the existing profile history and RAG context, and must answer directly without narrating a future
  handoff or asking the user to coordinate machines.
- Web UI requests use `POST /query/stream` (newline-delimited JSON). It reports stage changes and an elapsed-time
  heartbeat every 8 seconds, so a slow model load or response remains visibly active. The original `POST /query`
  endpoint remains available for Home Assistant and other API clients.
- Ollama chat calls allow up to 15 minutes and retry transient HTTP/model-load failures twice with short backoff.
  Exhausted retries return a truthful error and preserve conversation history; empty/error responses are not
  written into profile history.

### Home AI model-load recovery

- Ollama calls retry transient load/HTTP failures with lower context sizes. Mothership attempts
  32768, 16384, then 8192 tokens; 8B hosts use their safe 8192 floor.
- Responses shorter than two characters are treated as inference failures and are not persisted.
- A normal request that fails on one brain automatically continues on the next available host and reports that
  recovery through `/query/stream`. The fallback order is BigHonker -> Mothership -> Softserve (absolute last resort).
- Explicit Mothership requests remain deterministic and use context-degradation retries on Mothership.
- Final model errors are emitted to orchestrator container stdout for diagnosis with:
  `cd /opt/arrstack && docker compose logs --since 30m orchestrator`
- Source: `E:\SSDShared\orchestrator-build\`; live: `/opt/arrstack/orchestrator/`.

### Home AI identity and computation provenance

- Home AI is the user-facing orchestrator and always speaks as one continuous assistant.
- Internal workers must not identify the assistant as Qwen, a model size, or a particular machine.
- Explicit aliases `Mothership`, `27b`, and `heavyweight` route to the local `qwen3.6:27b` tier.
- Worker transitions are not narrated as conversations. Say `my 27B reasoning tier` rather than
  `I asked Mothership` or `Mothership shared`.
- The web UI shows provenance after the answer:
  `Computed by <host> - <model>`, plus source documents when applicable.

### Home AI chat Markdown rendering

- Assistant replies render Markdown instead of displaying formatting characters literally.
- Supported blocks: headings, paragraphs, bullet and numbered lists, blockquotes, separators, and fenced code.
- Supported inline formatting: bold, italics, links, and inline code.
- Model-provided HTML is escaped before Markdown transformations; user messages remain plain literal text.
- Saved assistant history uses the same renderer when reloaded.

### Home AI context compaction

- The orchestrator maintains two context layers per profile:
  - Full recent transcript: `/docs/State/history/<profile>.json`
  - Durable compacted memory: `/docs/State/history/<profile>.summary.json`
- Before a request, the orchestrator estimates context size and compacts when either the uncovered transcript
  reaches 20 messages or the combined context reaches roughly 18,000 characters.
- A reachable local model merges older turns into a factual Markdown summary. It preserves preferences,
  decisions, corrections, completed work, unresolved tasks, and continuity while removing repetition.
- The newest 12 messages remain verbatim. The compacted memory and uncovered recent turns are both supplied to
  whichever worker answers the next request.
- UI: `Compact context` manually requests compaction. Automatic compaction appears briefly as a working status
  and adds `context compacted` to the answer provenance footer.
- API:
  - `GET /history/context?user=the owner` - summary coverage, message counts, and estimated tokens
  - `POST /history/compact?user=the owner` - force compaction when older eligible turns exist
  - `POST /history/reset?user=the owner` - clears both transcript and compacted memory
- Defaults are configurable with `HISTORY_MAX_MESSAGES`, `CONTEXT_RECENT_MESSAGES`,
  `COMPACT_TRIGGER_MESSAGES`, `CONTEXT_CHAR_BUDGET`, and `COMPACT_SOURCE_CHAR_LIMIT`.

## llama-server run DIRECTLY (outside ollama) on Mothership — engines, the flags that matter, three gotchas

Written 2026-10-01 from the Flash-Next speed work ([[Qwen3.8-Flash-Next-on-Mothership]] has every measurement).
ollama cannot express explicit MoE expert placement, a Vulkan backend, or an MTP draft head; for a large MoE on a
24 GB card those three are the difference between 15 and 28 tok/s, so the model is run on `llama-server.exe` itself.

**Engines on Mothership (`C:\ProgramData\HAI\tools\` unless noted):**
| engine | backend | CPU backend | has | use for |
|---|---|---|---|---|
| ollama's bundle `…\AMD\AI_Bundle\Ollama-0.34.0\lib\ollama` (commit `0f3a71be1`) | ROCm (`rocm_v7_1\ggml-hip.dll` — set `GGML_BACKEND_PATH` to it or it runs CPU-only) | AVX2 variants | no MTP | prefill-heavy jobs (174 tok/s at N=32–34) |
| `llama-unsloth-b11160-vulkan` (Unsloth fork, `a6922cc`) | Vulkan | AVX2 variants + expert REPACK | MTP (`--spec-type draft-mtp`) | **generation — the fast path** |
| `llama-unsloth-b11160` (Unsloth fork, ROCm) | ROCm | ⚠ **single `ggml-cpu.dll` with NO SIMD** | MTP | **nothing — every number on it is void** |

**Flags that matter (Flash-Next q2k, 2026-10-01 best):** `-ngl 99 --n-cpu-moe 32 -ot per_layer_token_embd=CPU
-lm none --no-host -np 1 -fa on -ctk q8_0 -ctv q8_0 -ub 512 -c 8192 -t 8 -md <MTP head> --spec-type draft-mtp
--spec-draft-n-max 2 -ngld 99` (`-ub 512`: same generation, +11% prefill over 256; measured with 256 everywhere else) (the bundle spells `-lm none` as `--no-mmap` and has no `--spec-type`). `--n-cpu-moe N`
keeps the FIRST N layers' experts on the CPU; each costs ~1 ms/token and past N≈30 the CPU-side set no longer fits
32 GB RAM and pages. `-np 1` because one request at a time; four slots cost ~1 GB of recurrent-state cache.

**Gotchas (each cost an order or more):**
1. **Read `system_info` before the first arm.** `llama-server -m nonexistent.gguf -v` prints the compile-time CPU
   features before touching a model: `CPU : … AVX2 = 1 | FMA = 1` is required. A GPU-visible engine with a scalar
   CPU backend runs the offloaded experts scalar and every number is void (the Unsloth ROCm zip). The ladder's
   preflight now asserts it; a hand run must too.
2. **Vulkan + `--n-cpu-moe` needs `--no-host`.** Without it the CPU-side experts go into `Vulkan_Host` (pinned
   host-visible device memory, under ReBAR = the card), `Failed to allocate pinned memory` repeats, and context
   creation OOMs on the recurrent-state cache. `GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM=1` is the other switch.
3. **Cross-compiler DLL mixing does not work.** The Unsloth ROCm zip is clang-built, the Vulkan zip MSVC-built;
   CPU variants from one do not load in the other (`0xC0000135`), and the HIP backend does not register inside the
   MSVC loader. Use an engine as shipped or build it.
Also: the box telemetry CSV is held by its writer — a reader may get `PermissionError`; retry, never let it fail a
measurement. Frontier mode's auto pass treats a direct llama-server on a Flash-Next model as "loaded" (`1a3c58d`).

### Softserve networking cleanup (deferred)
Leftover `netsh portproxy <softserve-address>:11434→127.0.0.1` from an abandoned WSL experiment is still present
(harmless; works). On the next Ollama restart, do together: set `OLLAMA_HOST=0.0.0.0` (machine) +
`netsh interface portproxy delete v4tov4 listenaddress=<softserve-address> listenport=11434`.

---
