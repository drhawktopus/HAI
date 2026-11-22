# 34-A — qwen3.8 on Mothership: the ollama-pin / ROCm gate, researched (412 "requires a newer version of Ollama", CPU-only after update, AMD AI Bundle vs stock)

**Status: ✅ EXECUTED 2026-09-16 — Mothership is on stock 0.34.0, qwen3.8:27b pulled and resident. The gate is
OPEN.** **Serving backend since 2026-10-02 ~14:00: VULKAN, not ROCm** (the owner: *"switch it all over to fast"*) —
`C:\ProgramData\HAI\start-ollama.ps1` sets `OLLAMA_LLM_LIBRARY=vulkan` + `LLAMA_ARG_FIT_TARGET=512` (the 512 MiB fit
margin keeps the whole 27B on the card; ollama's default 1,936 MiB target left ~1.1 GB in system RAM). Verified live:
`using device Vulkan0`, 66/66 layers, 16.61/16.61 GB at num_ctx 122,880, **82.4 tok/s vs 53.8 on ROCm**; prompt reading
is slower on Vulkan (~700 vs ~900 tok/s). Evidence: [[Qwen3.8-Flash-Next-on-Mothership]] § "The 27B's fast recipe".
**Known cost: runner stalls** — 2 in ~5,500 requests on day one (27B, mid-answer, card idle); the factory holds and
retries, and `HAI Stall Recorder` captures the next one. Detail: [[RUNBOOK-Ollama-Host-Operations]] §"Ollama HUNG".
**Rollback:** restore `start-ollama.ps1.bak-20261002-pre-vulkan` (or delete the two env lines) and rerun the `Ollama
Serve` task. The ROCm add-on stays installed (`lib\ollama\rocm_v7_1`), so rollback needs no download. (Research completed 2026-09-12; decisions D1–D4 taken; D5 window named
by the owner 2026-09-16.) Original research by the tertiary Claude session; execution recorded in
2026-09-16-claude. This is the gate that held [[34-CHARTER]] area A (24 GB champion tournament)
and the adapter-serving path of #35; the ⚠ "qwen3.8:27b PULL BLOCKED" backlog entry is now closed.

## EXECUTED 2026-09-16 — results

Parallel-tree upgrade per §6, D1–D4 as decided (target **0.34.0**). Every hurdle cleared:

| hurdle | result |
|---|---|
| H1 (≥0.32.12) | ✅ 0.34.0 serving; qwen3.8:27b `requires 0.32.12` satisfied — the 412 is gone |
| H2 (ROCm serves 7900 XTX) | ✅ `library=ROCm compute=gfx1100 … "AMD Radeon RX 7900 XTX" libdirs=ollama,rocm_v7_1`; **no** `id=cpu total_vram="0 B"` |
| H3 (throughput parity, 3.6) | ✅ **34.1 tok/s gen** (spread 34.09–34.14, card idle) vs 30.6 on 0.32.0 — ~11% FASTER, no regression; cold prompt ~485 tok/s (no HIP prompt-processing regression observed) |
| H7 (3.8 under extraction) | ✅ `think:false` + JSON-schema → clean `{"category":"technical","urgency":"high"}`, `thinking` field empty — behaves like 3.6 |
| H8 (disk) | ✅ C: 81 GB free after; models on E: (131 GB free) |
| H9 (residency) | ✅ qwen3.6:27b (18.63 GB) and qwen3.8:27b (17.54 GB) both `size_vram == size`, 100% VRAM at 65 536 |

**Mechanics:** base+rocm zips sha256-verified, extracted to `…\AI_Bundle\Ollama-0.34.0` (0.32.0 tree
kept intact as rollback); `start-ollama.ps1` repointed (backup `.bak-20260916-pre034`), env preserved
(OLLAMA_MODELS/FLASH_ATTENTION/KV q8_0); restarted via the `Ollama Serve` task (new PID's exe path
confirmed = the 0.34.0 tree). GPU police: the `Ollama-0.34.0\ollama.exe pull` drew **report-only**
alerts (candidacy, `chargeable=False`) — expected discovery noise, nothing killed; the model runner
(`Ollama-0.34.0\lib\ollama\llama-server.exe`) is correctly exempt via owned-ancestor. **Not yet done:**
the deck-level residency ALARM (D3 / the 08-22 backlog item) — the per-load `size_vram==size` proof was
done here, but the durable deck verdict is still open; and D4's fleet sampling-default record.

## 1. The blocker in one paragraph

`qwen3.8:27b` declares `requires: 0.32.12` in its registry config blob; Mothership serves ollama
**0.32.0**, so the pull is refused with HTTP 412. Mothership sits on 0.32.0 because the ollama tray
auto-updater replaced the install in place on 2026-08-18 and the resulting tree had no ROCm or
Vulkan backend (three days of silent CPU serving, restored from a shadow copy on 08-22). The lab
then recorded the lesson as "newer ollama breaks AMD ROCm; wait for an AMD build that supports
3.8". **That premise is wrong, and the evidence for it was on the box the whole time:** a stock
0.32.13 — one point-release above the 3.8 floor — served this exact card on ROCm from 08-15 to
08-18. The regression was an installer that dropped the ROCm add-on, not a ROCm incompatibility.

## 2. Evidence (all read-only, 2026-09-12)

| claim | source | what it says |
|---|---|---|
| version floor | `registry.ollama.ai/v2/library/qwen3.8` config blob | `model_family qwen35 · renderer qwen3.8 · parser qwen3.5 · requires 0.32.12`; Q4_K_M 16.81 GB + 0.93 GB vision projector. Control `qwen3.6:27b`: `renderer qwen3.5 · requires 0.30.0`, 16.84 GB + 0.93 GB. Same family, same footprint. The model page on ollama.com states no version. |
| what Mothership runs | `/api/version`, `dir` of the bundle tree | 0.32.0 from `C:\Users\<user>\AppData\Local\AMD\AI_Bundle\Ollama`; files dated 2026-07-13; `lib\ollama` = `cuda_v12`, `cuda_v13`, `vulkan`, `rocm_v7_1` (17 dlls + rocblas library: gfx1030/1100/1101/1102/1150/1151/1200/1201/906). |
| the bundle is stock | central-directory listing of upstream `ollama-windows-amd64-rocm.zip` (0.32.15 and 0.34.0) and `ollama-windows-amd64.zip` (0.34.0) | rocm zip = `lib/ollama/rocm_v7_1/` with the same files and sizes (amdhip64_7.dll 18.2 MB, rocblas.dll 41.1 MB, ggml-hip.dll ~480 MB, 914 entries, gfx1100 kernels). Base zip = `ollama.exe` + `cuda_v12` + `cuda_v13` + `vulkan`. AMD's bundle is the two zips laid together. |
| stock served ROCm here | `app-5.log`…`app-1.log`, `server-5.log`…`server-1.log` on Mothership | 08-15 19:49 tray update to **0.32.13**. 08-15 20:08 → 08-18 18:47: four 0.32.13 instances log `inference compute … library=ROCm compute=gfx1100 name=ROCm0 … libdirs=ollama,rocm_v7_1`; 08-18 11:27 `qwen3.6:27b` loads `ROCm0 model buffer size = 14789 MiB`, KV 2176 MiB. |
| the 08-18 break | `upgrade.log`, `server.log`, the parked `Ollama.stock-0.32.14-clobbered-20260818` tree | 08-18 18:59 silent `OllamaSetup.exe` (0.32.14, Inno, 166 files) → tree has only `cpu` + `cuda_v12`; first 0.32.14 instance discovers `id=cpu total_vram="0 B"`. The Inno log records zero deletes — whatever removed `rocm_v7_1`/`vulkan` is not in it; the parked tree simply lacks both. |
| why the restore landed on 0.32.0 | 2026-08-22-claude | the pre-clobber VSS shadow was 08-15 19:35, fourteen minutes before the 0.32.13 update — so the restore reverted the good update too, and the 08-22 write-up read the 08-18 18:47 instance as "the bundle still working" when it was already stock 0.32.13. |
| host | `Get-CimInstance` over ssh | Ryzen 7 5800X (no iGPU → ollama#12754 dual-GPU misdetection does not apply); Adrenalin **26.8.1**, driver 32.0.31041.1004 dated 2026-08-16; no ROCm SDK on the box (the add-on ships its own HIP runtime, `amdhip64_7.dll`); C: 88.7 GB free; models on `E:\Ollama\Models`. |
| upstream | GitHub releases API, `docs.ollama.com/windows`, `/gpu` | latest **0.34.0** (2026-09-05). Windows: "ROCm v7 / HIP7-capable driver stack", RX 7900 XTX listed; ROCm is a separate `ollama-windows-amd64-rocm.zip` extracted into the same dir; Vulkan ships in the base zip, "enabled by default when installed"; `OLLAMA_VULKAN=0` disables it. 0.33.0: "Fixed broken default packaging caused by macOS-specific assumptions affecting Linux/Windows builds". |
| fleet spread | `/api/version` ×3 | Mothership 0.32.0 · BigHonker 0.32.3 · Softserve 0.30.7. |

Upstream behaviour changes between 0.32.0 and 0.34.0 that touch us (from the release notes; the
rest is MLX/Apple, app integrations, agent launchers):

| version | change | our exposure |
|---|---|---|
| 0.32.10 | models with no `repeat_penalty` default to 1.0 instead of 1.1 | both 27Bs set `repeat_penalty: 1` explicitly → 27B lane unaffected. Other Mothership models (`qwen3:14b`, `qwen3-coder:30b`, `medgemma:27b`, joycaption, the qwen3-vl tags) may shift; BigHonker (0.32.3) and Softserve (0.30.7) stay at 1.1 → a cross-factory sampling difference for any lane that fans out (class 8, one derivation). Check each model's params before/after. |
| 0.32.12–0.32.15 | Qwen 3.8 support; developer instructions; non-leading system messages normalized | the point of the exercise. 3.8's chat rendering differs from 3.6 (`renderer qwen3.8`); tournament prompts must be held constant. |
| 0.32.15 | model-metadata cache, ~50% faster time-to-first-token on cold load | cold-load figures in the runbook (18 s / 40 s), `PC1_WAKE_SETTLE`, keep_alive tuning were calibrated on the slower load. Nothing breaks; re-measure before retuning. |
| 0.33.0 | KV-cache-breaking "tokens left" system message disabled; packaging fix for Linux/Windows | packaging fix is the one that matters — a stock Windows tree assembled from zips is the documented, post-fix path. |
| 0.33.3 | GGUF-defined default parameters honored | same sampling-default exposure as 0.32.10, for models whose GGUF carries defaults. |

Unchanged across the range and load-bearing for us: `/api/ps` (`size_vram` still written at load, never
revised), `/api/embed`, `/api/version`, `format` schema grammars, `think:false` (still ignored by some
VLMs → the `content or thinking` fallback stays), `OLLAMA_KV_CACHE_TYPE=q8_0` + `OLLAMA_FLASH_ATTENTION=1`
(already in force on the hybrid-GDN 3.6 at 65 536 ctx: KV 2176 MiB + recurrent state 150 MiB).

## 3. The hurdles, ranked by cost

Tags: **REAL** = still a constraint · **MEASURE** = unknown until a controlled probe · **PROCESS** =
ours to keep · **RETIRE** = a workaround whose premise is gone.

| # | hurdle | tag | what settles it |
|---|---|---|---|
| H1 | Mothership must run ≥ 0.32.12 | REAL | a stock tree (base zip + rocm zip) installed **beside** the 0.32.0 tree, launcher repointed, verified by the discovery line and `size_vram == size` at 65 536. No installer, no tray, no in-place write. |
| H2 | ROCm on the new build actually serves the 7900 XTX | MEASURE (strongly expected yes) | proven for 0.32.13 on this box; 0.32.15/0.34.0 ship the same `rocm_v7_1` set. Verification is the serve.log line `library=ROCm compute=gfx1100 libdirs=ollama,rocm_v7_1`. Fallback arm: 0.32.15 (closest to the proven 0.32.13). |
| H3 | throughput parity for the control model (3.6) under the new build | MEASURE | one variable: same box, same occupancy (card idle, nothing else resident), same prompt set, 3.6 on 0.32.0 vs 3.6 on the new tree; report gen tok/s and prompt tok/s with spread over ≥3 rounds. Baseline on record: **30.6 tok/s median** gen (278 rewrite sections, 2026-08-22). llama.cpp has OPEN prompt-processing regressions for the qwen35 family on HIP (ggml-org #18823, #20218 dup) — they may already be in our 0.32.0 build; the upgrade can move either way, so measure, don't assume. |
| H4 | Vulkan as an alternative backend | MEASURE (optional arm) | the base zip carries `vulkan`; ollama picks ROCm when both see the card. A Vulkan arm (`rocm_v7_1` removed from a copy, or ROCm hidden) answers "is HIP the slow path for hybrid-GDN on Windows" — upstream reports say Vulkan wins prompt processing on these models on some AMD parts. Known Vulkan caveat: hybrid DeltaNet garbage at batch 512 (llama.cpp discussion #27164). |
| H5 | the auto-updater must never touch a serving tree again | PROCESS (already in place) | serve-direct launcher since 08-22; zips contain no updater; keep `ollama app.exe` out of every launch path; parallel trees make an accidental in-place write impossible to mistake for an upgrade. |
| H6 | consumers of the path and version | PROCESS | `C:\ProgramData\HAI\start-ollama.ps1` (path); `RUNBOOK-Ollama-Host-Operations` host table + Mothership bullet; `mothership-build\test_foundry.py` pins the real invocation string with the bundle path (re-paste byte-verbatim if the path changes); GPU-police `ORCHESTRATOR_OWNED_SIGNS` matches `ollama.exe serve` regardless of path — no change. `verify_deploy`-style check afterwards: the serving PID's exe path is the new tree. |
| H7 | 3.8 behaviour under our extraction lanes | MEASURE | thinking is ON by default with `reasoning_effort`; parser is `qwen3.5` (same as 3.6) so `think:false` + schema grammar should behave like 3.6 — prove it on the classify gold set before any lane routes to 3.8. |
| H8 | disk for 3.8 alongside 3.6 | REAL, small | +17.7 GB on `E:\Ollama\Models`; check free space at execution (not read this session). |
| H9 | a CPU-landed model still does not ALARM | GUARD HOLE, open since 08-22 | only per-job checks exist (`triage_classify` refuses, foundry caption residency check, scheduler read). The upgrade procedure carries an explicit residency probe; the deck-level `size_vram == size` verdict from the 08-22 backlog item is the durable fix and belongs before or with the upgrade (class 6: a diagnosed guard hole is not backlog food). |
| H10 | fleet version spread and sampling defaults | DECIDE | see D4. |

Not a hurdle, and not fixed upstream: the wedge/collision class (context-held, kill = cure) is
driver/HIP behaviour under co-located work, independent of ollama version — [[Driver-RootCause-Investigation-Plan]]
stays authoritative. The 8 GB one-worker law is VRAM arithmetic, unchanged. qwen3.8 ships **27B only**
(three 18 GB tags), so the 8 GB tournament is untouched by this gate.

## 4. What we optimized around that can be retired (the owner's discussion list)

| we built / assumed | premise | verdict |
|---|---|---|
| "Mothership is pinned to 0.32.0; newer ollama breaks AMD ROCm into CPU" (backlog, charter gate, runbook) | a stock build has no AMD backend | **premise false** — stock 0.32.13 served ROCm here; the stock rocm zip *is* the backend. Retire the **pin as policy**. Keep the **mechanisms**: serve-direct, no tray, shadow/parallel-tree rollback, residency verification. |
| "wait for an AMD AI Bundle release that supports 3.8" (the backlog's DECISION NEEDED) | AMD builds something ollama does not | **moot** — the bundle is upstream's two zips. Retire the dependency on AMD's bundle cadence; the bundle directory can stay as the 0.32.0 rollback generation. |
| runbook: "stock build has NO AMD backend: rocm_v7_1 and vulkan dirs deleted" | | corrected 2026-09-12 (installer laid none down; the zips carry them); logged in RUNBOOK-DEFECT-LOG. |
| cold-load numbers (18 s small / 40.5 s 27B at 65k), `PC1_WAKE_SETTLE`, keep_alive 5 m | measured on ≤ 0.32.0 | not retired — **re-measure** after 0.32.15's metadata cache; retune only on a number. |
| `think:false` fallback to `message.thinking` | model behaviour | keep — model-dependent, not version-dependent. |
| `size_vram` recorded-at-load caveat in `scheduler.py` | ollama semantics | keep — unchanged upstream. |
| 65 536 num_ctx authority via `HEAVY_NUM_CTX` / `/health` | ollama's vram-based default is 32 768 | keep — the new build still defaults 32k from VRAM; we must keep setting it. |

## 5. Decisions the owner owns

> **DECIDED 2026-09-12 (the owner, main session):** D1 = **yes, parallel tree**. D2 = **0.34.0** (0.32.15
> the fallback arm). D3 = **residency alarm in the same pass**. D4 = **Mothership only for now**,
> record the sampling-default shift. **D5 (window) still open** — execution waits on it; the
> portfolio sessions are using Mothership. Nothing executes until the owner names the window and gives the go.


- **D1 — method:** approve the *parallel-tree* upgrade (extract base zip + rocm zip into a new
  sibling directory, repoint `start-ollama.ps1`, restart via the `Ollama Serve` task; rollback = repoint
  back). No `OllamaSetup.exe`, no in-place write, ever. Recommended: yes.
- **D2 — target:** latest stable at execution time (**0.34.0** today; includes the 0.33.0 Windows
  packaging fix) with **0.32.15** as the fallback arm if discovery or parity fails (it is the nearest
  release to the proven 0.32.13 and carries the 3.8-specific fixes). Recommended: 0.34.0 first.
- **D3 — guard first?** land the deck-level residency verdict (H9) before or in the same pass.
  Recommended: same pass at minimum; the procedure below is not "done" without a residency proof either way.
- **D4 — fleet parity:** BigHonker (0.32.3) and Softserve (0.30.7) stay for now, OR move in the same
  pass so sampling defaults match across factories. Recommended: hold them, record the per-model
  default shift, and revisit when a fanned-out lane shows a cross-factory difference. Softserve is the
  Plex host and the last-resort brain — its upgrade needs its own window.
- **D5 — window:** the switch costs one ollama restart (minutes) and evicts whatever is resident.
  Schedule it when no session is using Mothership; declare it with the watchdog's
  `planned-downtime.hold` expiry so nothing auto-restarts mid-swap; tell the other sessions.

## 6. Procedure sketch (an ORDER, not a script — the desk builds the steps)

Inputs: chosen version (D2), the two upstream zips + their published checksums, the launcher.
Deliverables: serve.log discovery line naming ROCm0, `/api/ps` residency at 65 536 for 3.6, the H3
parity table, `qwen3.8:27b` pulled and resident, doc updates. Cleanup: nothing deleted — the 0.32.0
bundle tree stays as the rollback generation, timestamped by its own directory name.

1. Announce + `planned-downtime.hold`; confirm nothing resident (`/api/ps`) and no session on the box.
2. Fetch `ollama-windows-amd64.zip` + `ollama-windows-amd64-rocm.zip` for the target; verify sha256
   against the release's checksum file; extract both into `…\AMD\AI_Bundle\Ollama-<version>` (sibling,
   never over the live tree). Verify the tree lists `lib\ollama\rocm_v7_1` and `vulkan`.
3. Repoint `start-ollama.ps1` (backup `.bak-<date>` beside it, Write tool, verify bytes); keep the
   q8_0 / flash-attention env and the serve.log redirect; **no `ollama app.exe` anywhere**.
4. Restart through the `Ollama Serve` task; verify the new PID's executable path and start time; read
   serve.log: `Listening on … (version <target>)` and `library=ROCm compute=gfx1100 libdirs=ollama,rocm_v7_1`.
   Any `id=cpu total_vram="0 B"` → repoint back, done, diagnose cold.
5. Load `qwen3.6:27b` at `num_ctx` 65 536; `/api/ps` `size_vram == size`; KV buffer ≈ 2176 MiB in the
   load log. Then the H3 parity rounds (card otherwise idle). A parity miss beyond spread is a stop.
6. `ollama pull qwen3.8:27b`; load; residency again; the H7 classify-gold probe with `think:false` +
   schema. Then it is a tournament challenger and area A opens.
7. Docs same pass: runbook host table + Mothership bullet (path, version, "verified live <date>"),
   backlog item closed, charter gate cell, `test_foundry.py` invocation string if the path moved,
   journal + defect log if anything else was wrong. Clear the hold.

## Sources

- `registry.ollama.ai` manifests + config blobs for `qwen3.8:27b` and `qwen3.6:27b` (2026-09-12).
- GitHub releases API `ollama/ollama` (0.31.1 → 0.34.0), `docs.ollama.com/windows`, `docs.ollama.com/gpu`.
- Mothership: `serve.log`, `server-1..5.log`, `app.log`, `app-1..5.log`, `upgrade.log`, bundle and parked trees.
- ggml-org/llama.cpp #18823, #20218, #20598, discussion #27164; ollama/ollama #12754 (not applicable, no iGPU).
- Library: 2026-08-22-claude, [[RUNBOOK-Ollama-Host-Operations]], [[34-CHARTER]], PROJECT-BACKLOG 2026-08-22 items.
