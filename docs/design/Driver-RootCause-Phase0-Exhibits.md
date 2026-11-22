# Degradation-curve exhibits (Phase 0) — generated 2026-07-22

## Exhibit A — per-boot event census (rescue-daemon era, 07-20 21:20 onward)

| boot | daemon events: stalls | duds (born-degraded) | render-starts | wedge verdicts | context-kills |
|---|---|---|---|---|---|
| 07-20 21:20 | 5 | 0 | 0 | 0 | 0 |
| 07-21 01:13 | 46 | 70 | 2 | 0 | 0 |
| 07-22 03:01 | 81 | 78 | 64 | 4 | 3 |
| 07-22 14:18 | 13 | 11 | 14 | 2 | 3 |
| 07-22 15:06 | 0 | 0 | 2 | 0 | 0 |
| 07-22 15:19 | 0 | 0 | 5 | 0 | 0 |
| 07-22 15:40 | 0 | 0 | 2 | 0 | 0 |
| 07-22 15:57 | 0 | 0 | 2 | 0 | 0 |
| 07-22 16:11 | 0 | 0 | 2 | 0 | 0 |
| 07-22 16:23 | 0 | 0 | 2 | 0 | 0 |

## Exhibit B — RENDER regime: where in a boot do wedges land?

For each boot: ordered render-start events; mark which were followed by a wedge verdict
within 15 min (before the next render-start).

| boot | render# in boot | start | wedged after? |
|---|---|---|---|
| 07-21 01:13 | 1 | 02:45:43 | no |
| 07-21 01:13 | 2 | 02:46:13 | no |
| 07-22 03:01 | 1 | 03:03:48 | no |
| 07-22 03:01 | 2 | 03:04:18 | no |
| 07-22 03:01 | 3 | 03:07:37 | no |
| 07-22 03:01 | 4 | 03:18:57 | no |
| 07-22 03:01 | 5 | 04:29:33 | no |
| 07-22 03:01 | 6 | 04:30:04 | no |
| 07-22 03:01 | 7 | 04:34:04 | no |
| 07-22 03:01 | 8 | 07:11:12 | no |
| 07-22 03:01 | 9 | 07:11:42 | no |
| 07-22 03:01 | 10 | 07:15:43 | no |
| 07-22 03:01 | 11 | 07:16:13 | no |
| 07-22 03:01 | 12 | 07:30:01 | no |
| 07-22 03:01 | 13 | 07:44:11 | no |
| 07-22 03:01 | 14 | 10:35:20 | no |
| 07-22 03:01 | 15 | 10:35:50 | no |
| 07-22 03:01 | 16 | 10:38:21 | no |
| 07-22 03:01 | 17 | 10:38:51 | no |
| 07-22 03:01 | 18 | 10:48:19 | no |
| 07-22 03:01 | 19 | 10:48:49 | no |
| 07-22 03:01 | 20 | 10:49:49 | no |
| 07-22 03:01 | 21 | 10:52:50 | no |
| 07-22 03:01 | 22 | 10:53:20 | no |
| 07-22 03:01 | 23 | 11:07:31 | no |
| 07-22 03:01 | 24 | 11:11:40 | no |
| 07-22 03:01 | 25 | 11:14:41 | no |
| 07-22 03:01 | 26 | 11:15:11 | no |
| 07-22 03:01 | 27 | 11:17:19 | no |
| 07-22 03:01 | 28 | 11:17:49 | no |
| 07-22 03:01 | 29 | 11:27:00 | no |
| 07-22 03:01 | 30 | 11:27:30 | no |
| 07-22 03:01 | 31 | 11:45:26 | no |
| 07-22 03:01 | 32 | 11:45:56 | no |
| 07-22 03:01 | 33 | 12:00:32 | no |
| 07-22 03:01 | 34 | 12:01:02 | no |
| 07-22 03:01 | 35 | 12:10:34 | no |
| 07-22 03:01 | 36 | 12:11:04 | no |
| 07-22 03:01 | 37 | 12:11:51 | no |
| 07-22 03:01 | 38 | 12:49:13 | no |
| 07-22 03:01 | 39 | 12:49:43 | no |
| 07-22 03:01 | 40 | 13:09:26 | no |
| 07-22 03:01 | 41 | 13:09:56 | no |
| 07-22 03:01 | 42 | 13:17:10 | no |
| 07-22 03:01 | 43 | 13:17:40 | no |
| 07-22 03:01 | 44 | 13:18:28 | no |
| 07-22 03:01 | 45 | 13:22:49 | no |
| 07-22 03:01 | 46 | 13:23:19 | no |
| 07-22 03:01 | 47 | 13:31:21 | no |
| 07-22 03:01 | 48 | 13:31:51 | no |
| 07-22 03:01 | 49 | 13:32:38 | no |
| 07-22 03:01 | 50 | 13:39:35 | no |
| 07-22 03:01 | 51 | 13:40:05 | no |
| 07-22 03:01 | 52 | 13:41:09 | no |
| 07-22 03:01 | 53 | 13:52:22 | no |
| 07-22 03:01 | 54 | 13:52:52 | no |
| 07-22 03:01 | 55 | 13:58:59 | no |
| 07-22 03:01 | 56 | 13:59:29 | no |
| 07-22 03:01 | 57 | 14:01:39 | no |
| 07-22 03:01 | 58 | 14:02:09 | YES |
| 07-22 03:01 | 59 | 14:07:48 | no |
| 07-22 03:01 | 60 | 14:08:48 | no |
| 07-22 03:01 | 61 | 14:09:18 | YES |
| 07-22 03:01 | 62 | 14:13:21 | no |
| 07-22 03:01 | 63 | 14:13:51 | no |
| 07-22 03:01 | 64 | 14:15:07 | YES |
| 07-22 14:18 | 1 | 14:20:45 | no |
| 07-22 14:18 | 2 | 14:21:16 | no |
| 07-22 14:18 | 3 | 14:31:18 | no |
| 07-22 14:18 | 4 | 14:31:48 | no |
| 07-22 14:18 | 5 | 14:33:05 | YES |
| 07-22 14:18 | 6 | 14:37:19 | no |
| 07-22 14:18 | 7 | 14:38:19 | no |
| 07-22 14:18 | 8 | 14:38:49 | no |
| 07-22 14:18 | 9 | 14:45:05 | no |
| 07-22 14:18 | 10 | 14:51:58 | no |
| 07-22 14:18 | 11 | 14:52:28 | YES |
| 07-22 14:18 | 12 | 14:58:17 | no |
| 07-22 14:18 | 13 | 14:59:17 | no |
| 07-22 14:18 | 14 | 14:59:47 | no |
| 07-22 15:06 | 1 | 15:07:38 | no |
| 07-22 15:06 | 2 | 15:08:08 | no |
| 07-22 15:19 | 1 | 15:21:26 | no |
| 07-22 15:19 | 2 | 15:21:57 | no |
| 07-22 15:19 | 3 | 15:22:46 | no |
| 07-22 15:19 | 4 | 15:36:49 | no |
| 07-22 15:19 | 5 | 15:37:19 | no |
| 07-22 15:40 | 1 | 15:42:30 | no |
| 07-22 15:40 | 2 | 15:43:00 | no |
| 07-22 15:57 | 1 | 15:59:33 | no |
| 07-22 15:57 | 2 | 16:00:03 | no |
| 07-22 16:11 | 1 | 16:12:48 | no |
| 07-22 16:11 | 2 | 16:13:18 | no |
| 07-22 16:23 | 1 | 16:24:44 | no |
| 07-22 16:23 | 2 | 16:25:15 | no |

**Wedge rate by render index within a boot** (the H1 curve):

| render # within boot | n | wedged | rate |
|---|---|---|---|
| 1 | 9 | 0 | 0% |
| 2 | 9 | 0 | 0% |
| 3 | 3 | 0 | 0% |
| 4 | 3 | 0 | 0% |
| 5 | 3 | 1 | 33% |
| 6 | 2 | 0 | 0% |
| 7 | 2 | 0 | 0% |
| 8 | 2 | 0 | 0% |
| 9 | 2 | 0 | 0% |
| 10 | 2 | 0 | 0% |
| 11 | 2 | 1 | 50% |
| 12 | 2 | 0 | 0% |
| 13 | 2 | 0 | 0% |
| 14 | 2 | 0 | 0% |
| 15 | 1 | 0 | 0% |
| 16 | 1 | 0 | 0% |
| 17 | 1 | 0 | 0% |
| 18 | 1 | 0 | 0% |
| 19 | 1 | 0 | 0% |
| 20 | 1 | 0 | 0% |
| 21 | 1 | 0 | 0% |
| 22 | 1 | 0 | 0% |
| 23 | 1 | 0 | 0% |
| 24 | 1 | 0 | 0% |
| 25 | 1 | 0 | 0% |
| 26 | 1 | 0 | 0% |
| 27 | 1 | 0 | 0% |
| 28 | 1 | 0 | 0% |
| 29 | 1 | 0 | 0% |
| 30 | 1 | 0 | 0% |
| 31 | 1 | 0 | 0% |
| 32 | 1 | 0 | 0% |
| 33 | 1 | 0 | 0% |
| 34 | 1 | 0 | 0% |
| 35 | 1 | 0 | 0% |
| 36 | 1 | 0 | 0% |
| 37 | 1 | 0 | 0% |
| 38 | 1 | 0 | 0% |
| 39 | 1 | 0 | 0% |
| 40 | 1 | 0 | 0% |
| 41 | 1 | 0 | 0% |
| 42 | 1 | 0 | 0% |
| 43 | 1 | 0 | 0% |
| 44 | 1 | 0 | 0% |
| 45 | 1 | 0 | 0% |
| 46 | 1 | 0 | 0% |
| 47 | 1 | 0 | 0% |
| 48 | 1 | 0 | 0% |
| 49 | 1 | 0 | 0% |
| 50 | 1 | 0 | 0% |
| 51 | 1 | 0 | 0% |
| 52 | 1 | 0 | 0% |
| 53 | 1 | 0 | 0% |
| 54 | 1 | 0 | 0% |
| 55 | 1 | 0 | 0% |
| 56 | 1 | 0 | 0% |
| 57 | 1 | 0 | 0% |
| 58 | 1 | 1 | 100% |
| 59 | 1 | 0 | 0% |
| 60 | 1 | 0 | 0% |
| 61 | 1 | 1 | 100% |
| 62 | 1 | 0 | 0% |
| 63 | 1 | 0 | 0% |
| 64 | 1 | 1 | 100% |

## Exhibit C — TRAINING regime: born-degraded spawn rate vs boot age

Dud = a fresh burst process measuring <14 TFLOP/s at spawn. If boot freshness mattered,
dud rate should rise with boot age; if spawn-lottery, it should be flat.

| hours since boot | dud spawns | median dud TFLOP/s |
|---|---|---|
| 0 | 14 | 9.8 |
| 1 | 8 | 12.2 |
| 2 | 13 | 12.2 |
| 3 | 1 | 10.3 |
| 4 | 17 | 12.0 |
| 5 | 6 | 11.8 |
| 7 | 5 | 12.8 |
| 8 | 4 | 11.0 |
| 10 | 11 | 10.8 |
| 11 | 12 | 8.5 |
| 12 | 3 | 7.3 |
| 13 | 4 | 10.0 |
| 14 | 1 | 11.0 |
| 15 | 1 | 9.6 |
| 16 | 2 | 9.8 |
| 18 | 31 | 2.5 |
| 19 | 1 | 9.6 |
| 20 | 4 | 9.7 |
| 21 | 6 | 9.4 |
| 22 | 10 | 11.2 |
| 24 | 4 | 10.3 |
| 25 | 1 | 12.5 |

## Exhibit D — telemetry: fraction of samples in stall-state vs boot age

Stall-state row = gpu_load>=95 AND power<290W AND (mem_load<15 OR rate>20s/it).
Denominator = rows with gpu_load>=95 (i.e., GPU 'busy'). Per hour-since-boot, pooled over era.

| hours since boot | busy samples | stalled | stalled % |
|---|---|---|---|
| 0 | 507 | 195 | 38% |
| 1 | 393 | 113 | 29% |
| 2 | 381 | 132 | 35% |
| 3 | 466 | 69 | 15% |
| 4 | 368 | 174 | 47% |
| 5 | 225 | 104 | 46% |
| 6 | 91 | 12 | 13% |
| 7 | 112 | 41 | 37% |
| 8 | 149 | 38 | 26% |
| 9 | 166 | 6 | 4% |
| 10 | 220 | 80 | 36% |
| 11 | 254 | 45 | 18% |
| 12 | 222 | 77 | 35% |
| 13 | 231 | 13 | 6% |
| 14 | 310 | 48 | 15% |
| 15 | 253 | 12 | 5% |
| 16 | 252 | 34 | 13% |
| 17 | 204 | 72 | 35% |
| 18 | 155 | 154 | 99% |
| 19 | 293 | 84 | 29% |
| 20 | 222 | 13 | 6% |
| 21 | 150 | 33 | 22% |
| 22 | 224 | 63 | 28% |
| 23 | 290 | 0 | 0% |
| 24 | 185 | 78 | 42% |
| 25 | 222 | 45 | 20% |

## Exhibit E — worker-era renders (07-22): boot freshness vs outcome

| job | boot | mins after boot | duration s | outcome |
|---|---|---|---|---|
| 893c029b | 03:01 | 505 | 285 | done |
| 2985533b | 03:01 | 523 | 313 | done |
| fa2110b7 | 03:01 | 538 | 271 | done |
| b5696e80 | 03:01 | 548 | 301 | done |
| cf8c57e8 | 03:01 | 587 | 301 | done |
| d8769ad4 | 03:01 | 607 | 322 | done |
| 7a657c5a | 03:01 | 615 | 304 | done |
| 7a657c5a | 03:01 | 615 | 270 | done |
| 0f25708a | 03:01 | 620 | 301 | done |
| beca912a | 03:01 | 629 | 301 | done |
| 9b25b262 | 03:01 | 637 | - | no-DONE-line |
| caaf48ce | 03:01 | 650 | - | no-DONE-line |
| 8b5cd950 | 03:01 | 665 | - | no-DONE-line |
| 6a667db2 | 03:01 | 671 | - | no-DONE-line |
| 159f5647 | 14:18 | 1 | 316 | done |
| 384dcfd3 | 14:18 | 12 | - | no-DONE-line |
| d225c9c2 | 14:18 | 18 | - | no-DONE-line |
| 9229f3ac | 14:18 | 33 | - | no-DONE-line |
| 80673db5 | 14:18 | 39 | - | no-DONE-line |
| 067aef89 | 15:06 | 0 | 327 | done |
| e64b776b | 15:19 | 1 | 323 | done |
| af0db58e | 15:19 | 17 | - | no-DONE-line |
| af0db58e | 15:19 | 20 | - | no-DONE-line |
| 22512d95 | 15:40 | 0 | 337 | done |
| 1cd9f084 | 15:57 | 0 | 337 | done |
| e33a4d83 | 16:11 | 0 | 337 | done |
| d051e224 | 16:23 | 0 | 337 | done |

## VERDICT (Phase 0, 2026-07-22 ~18:00) — H0 ANSWERED: TWO PHENOMENA

1. **RENDER wedges are churn-accumulated and reboot-curable.** The 07-22 03:01 boot ran 57 straight
   render-starts clean and only wedged at #58/61/64 (11h of foundry+play churn in). The 14:18 boot
   wedged young (#5, #11) — but inside a 40-min retry storm (4 failed jobs + context-kills). ALL
   first-renders-on-fresh-boot were clean: 7/7 (three normal boots + four Fresh-Serve cycles).
   Fresh-Serve works because churn-state resets with the kernel.
2. **TRAINING born-degraded spawns are a lottery, flat vs boot age.** Duds appear from hour 0 (14 in the
   first hour!) to hour 25 at similar rates and speeds (9–12 TFLOP/s). This is WHY the recipe's 07-20
   reboot test failed to fix training stalls — reboot never was that regime's medicine. Bursts/respawns are.
3. Corollary: the two mitigation systems we already run (Fresh-Serve for renders, barrage/respawn for
   training) map one-to-one onto the two diseases. Correct by evolution, now confirmed by data.
4. **Bonus anomaly worth a look in Phase 2:** an hour-18 cluster of 31 duds with median 2.5 TFLOP/s
   (deep-degradation episode, 07-21 evening) — the worst state we've recorded.

Chart: `Driver-RootCause-Phase0-TwoPhenomena.png`. Raw logs snapshot: session scratchpad `driverlogs/`
(source of truth remains on Mothership F:\flux2-train\).

## Phase 1 discriminator — DEPLOYED (daemon v4.9, 2026-07-22 17:52)

The wedge verdict itself already proves fresh spawns are sub-flip DURING a wedge (global state, not
process-local). v4.9 adds the missing half at both kill sites: one measured fresh spawn ~8s after
killing the wedged ComfyUI context. Fast-now ⇒ wedge state was HELD BY the context (MES queue-poisoning
pattern, ROCm#2689). Slow-now ⇒ machine-wide state that survives context death. Logs as
"DISCRIMINATOR (fast-path|event-path): ..." in stall_rescue.log. Zero GPU cost; data accrues at every
future wedge automatically.

## ⭐⭐ JACKPOT — 2026-07-22 23:09 — first harness-induced wedge + FIRST DISCRIMINATOR VERDICT

**Scoreboard: lean >60 clean · heavy >25 clean · multi >25 clean · KITCHEN SINK: wedged on CYCLE 1.**

Sequence: first PRODUCTION-SIZE render of the night (20-step 1024px) ran while an SDXL gen executed
concurrently on :8188 (accidental timing — the residency fix-up) + routine daemon bursts. Daemon burst
degraded to 23.5 TFLOP/s (first sub-par burst all night) → RENDER-STALL CONFIRMED (210W/3158MHz/load
100) → ladder duds 6.9/7.3/10.9 TFLOP/s (EXACTLY the play-day dud band) → WEDGED verdict → context
kill → **DISCRIMINATOR: post-kill fresh spawn 76.6 TFLOP/s — instant virgin-kernel health. VERDICT:
CONTEXT-HELD.**

Rewrites:
1. **ONE disease, mechanism identified:** a context gets poisoned (MES-queue pattern, per ROCm#2689);
   while it lives, its own work crawls (~2% speed) AND fresh spawns measure sub-flip (the entire
   "born-degraded lottery" was contention with a living poisoned context). Kill it → instant full
   recovery, no reboot.
2. **Reboots were never necessary** — Fresh-Serve worked because rebooting kills every context incl.
   the poisoned one. The daemon's targeted context-kill is the complete cure in ~30s.
3. **Trigger co-factors narrowed to:** big-latent render × concurrent other-context GPU work. 60+
   512px renders w/ concurrent bursts never tripped it; the first 1024px render + concurrent SDXL
   did, in ~3 min, on a FRESH kernel (kernel age irrelevant — consistent with context-held).
4. "Churn causes wedges" reinterpretation: churn multiplies poisoning OPPORTUNITIES (more
   renders x more concurrent windows), it doesn't accumulate damage.

Bisect running (v1.4): Leg A = FULLSIZE renders solo (if it wedges, size alone suffices);
Leg B = FULLSIZE + deliberate concurrent SDXL per cycle (expected repro). Raw log archived:
`churn_harness_kitchen_wedge1.log` + stall_rescue.log 23:03-23:11 window.

## ✅ BISECT COMPLETE — 2026-07-23 00:37 — REPRODUCIBLE ON DEMAND

- **Leg A (production render + LoRA + bake, SERIALIZED): 15/15 clean.** The full worker job is
  harmless alone.
- **Leg B (same + one SDXL gen fired 15s into each render): WEDGED ON CYCLE 1**, ~60s after the
  collision. Duds 7.6/8.0/9.3 TFLOP/s (the eternal band). Discriminator #2: post-kill fresh spawn
  76.3 TFLOP/s — CONTEXT-HELD, 2-for-2.
- **Season totals: 125 collision-free cycles = 0 wedges; 2 collision cycles = 2 wedges (~90s each).**

**THE ANSWER: big-latent render on one context + concurrent generation on another context
⇒ poisoned context (MES-queue class), deterministic within ~90s. Not cumulative, not kernel-age,
not churn, not VRAM traffic, not runtime interleave. Cure = kill the poisoned context (~30s,
daemon already automated). Reboots/Fresh-Serve obsolete as medicine (they worked by killing
everything).**

Repro recipe (for the upstream report): fresh boot → ComfyUI-A 20-step 1024px Flux.2 GGUF render →
15s in, ComfyUI-B SDXL 6-step 512px gen → stall inside ~90s (power <250W @ >3100MHz, memload <10%,
zero OS events). Harness: F:\flux2-train\churn_harness.py (HARNESS_FULLSIZE + HARNESS_CONCURRENT).
Logs: churn_harness_legA_clean15.log, churn_harness_kitchen_wedge1.log, stall_rescue.log
23:09 + 00:37 windows.

**Practical mitigations (in priority order):**
1. Serialize the card by architecture — ComfyUI consolidation to ONE server/queue (backlogged
   2026-07-22, now promoted to THE FIX).
2. Interim guard until consolidation: orchestrator generate_image must defer/queue when a P20
   render is active (render_busy() check) — TODO.
3. Daemon context-kill stays as the safety net (30s cure, proven).
4. Runtime-pin A/B is now OPTIONAL (mechanism known; only worth running to check if a newer/older
   nightly fixes the collision bug itself).

## 🛡 THE FIX — DEPLOYED + VERIFIED (2026-07-23 00:55)

Consolidation executed same night (the owner: "let's keep it going"):
1. `extra_model_paths.yaml` on F:\comfyui-rocm gained `generic_estore:` mapping E:/ComfyUI/ComfyUI/models/
   (zero files moved; RealVisXL + flux1-fp8 + sd_xl_base all resolve on :8189).
2. Orchestrator `config.py` COMFY_URL default → :8189 (deployed via standard mirror flow; env override
   unused — compose has no COMFY_URL entry).
3. E:\ComfyUI "ComfyUI Server" schtask DISABLED (reversible) + process stopped. :8188 retired.
4. **VERIFICATION: Leg B's exact collision recipe vs the single server — flux render +165s, SDXL gen
   queued behind it, done +195s, daemon silent. The wedge recipe no longer has a doorway.**

Cleanup crumbs (backlog): worker free_8188() is now a harmless no-op (remove on next worker touch);
comment-only 8188 refs in app.py/comfy_client.py; "ComfyUI Server" task can be deleted after a week
of clean operation.

## Naming legend (the labels drifted mid-night; decoded here once)
- **Stage 1** = 40-spawn distribution (virgin kernel) — the "lottery" test
- **Legs 1-4 (numbered)** = sequential exoneration campaign: 1 lean churn · 2 heavy VRAM ·
  3 multi-runtime interleave · 4 kitchen sink (the jackpot)
- **Legs A/B (letters)** = the bisect pair after the jackpot: A = full production job SERIALIZED
  (control) · B = same + deliberate concurrent SDXL gen (collision arm)
- **Cycles L/G** = Science Round 3 mnemonics: L = pure model-LOAD collision · G = real GENERATION
  collision (Ollama 27B, cross-runtime)
Write-ups for outsiders (upstream report) use plain descriptions only, never these labels.

## Ops: headless conversion (2026-07-23 ~01:50, the owner: "make sure these are all launching headless")
All 9 Mothership scheduled tasks (worker, daemon, foundry trio, ComfyUI launchers x3, churn harness,
telemetry logger) now launch via hidden VBS wrappers (F:\flux2-train\headless\, originals in
ORIGINAL_ACTIONS.txt). Gotcha pair for the books: schtasks /change /tr prompts for the run-as
password on interactive tasks (blocked), but /create /f does NOT (registers under the ssh user's
interactive token) — recreate, don't change. Verified: worker+daemon relaunched headless, ComfyUI
launcher live-tested UP in 48s windowless.

## ⚖ CROSS-VERSION A/B — 2026-07-23 ~02:50 — BUG IS LONG-STANDING

Built a full clone of the render stack (F:\comfyui-rocm-714AB, models junctioned) on the NEWEST
PUBLIC wheel for gfx110X: **torch 2.10.0+rocm7.14.0a20260611** (June 11 — note: our production
7.15.0a20260714 is a MONTH NEWER than anything publicly downloadable; nobody can "try newer").
Collision recipe vs the clone: **WEDGED ON CYCLE 1** (601s timeout, same signature). Post-kill
burst: **76.0 TFLOP/s — context-held confirmed on the June wheel too.**

**Conclusions:** (1) the collision bug spans at least torch 2.10/ROCm 7.14 → torch 2.12/ROCm 7.15 —
LONG-STANDING, not a July regression; H2 (bad-nightly) is dead. (2) **Runtime pinning is NOT an
escape — the serialization architecture is the only existing fix.** (3) Report upgraded: repro
reproduces across two runtime generations.

Build gotchas taught along the way (for the gotcha file): robocopy `/XD models` excludes dirs named
"models" at ANY depth (amputated comfy/ldm/models AND pip/_internal/models — one flag, three
disguises; use ABSOLUTE paths in /XD); vendor-bundle torch transplants must move the WHOLE family
(torch + rocm[libraries] + torchvision --no-deps + uninstall amd-torch-device-* meta-pins) and be
smoke-tested under the launcher env (rocm-sdk init), not bare python.

Season final: **3 collision cycles across 2 runtimes = 3 wedges · 125+ collision-free cycles = 0.**
