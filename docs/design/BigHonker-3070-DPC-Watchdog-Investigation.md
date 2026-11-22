# BigHonker RTX 3070 — `0x133` DPC_WATCHDOG investigation

**Created:** 2026-07-26/27 (Claude, with the owner). **Status (2026-10-21 15:12 CT): CAUSE SEPARATED + FIX LIVE — the
card's power under dense LLM decode (§9i–§9j). Production holds a 180 W power limit (task `BH GPU Power Limit`), which
survived the failing load 2 × 15 min at the same 66–67 tok/s where the board's 280 W default died at 99 s and 226 s.**
How it was found — the SHORT failure came back on the new PSU at stock clocks, and the §9h short runs (3 × 15 min
GEMM survived) had not predicted production: Real 9B orders have lost the
card three times since the 10-16 unlock, every time at stock boost: **10-19 ~20:23:12 CT** (1980 MHz, 237.63 W,
core 65 C / hotspot 87.88 C; 2026-10-19-codex-p34) and **twice on 10-21 under `digest-triage-20261021-1021`
(qwen3.5:9b + mmproj, VRAM full 6.9 GB, ~87 % load): work started 10:21:30 → NVIDIA event 153 ×36 at 10:25:27 →
bugcheck `0x133` (P1 1, P2 0x1e00, `DPC_QUEUE_EXECUTION_TIMEOUT_EXCEEDED`, bucket `0x133_ISR_nvlddmkm`,
`nvlddmkm+3042d6`, stack through `nt!KeAccumulateTicks` — the §2 family) → auto-reboot 10:28:06; resumed 10:30:20
→ lost again 10:34:04 (`smi=error`).** Last values before each: 1965–1995 MHz, 236–245 W, core 65–66 C, hotspot
87–88 C, vcore 1.06–1.08 V. Time to failure **~4 min both times** — the old PSU's 246 / 265 / 311 s window. Evidence
(dump, kd output, per-second telemetry 10:15–10:36): `Claude Home\research\bh-gpu-loss-20261021\`. Recovery: reboot
authorized by the owner 11:34:59 CT (new boot time, nvidia-smi sees the card, `/gpu/health` all healthy).

**Operating state (2026-10-21):** power limit **180 W** held by `BH GPU Power Limit` (§9j); clocks stay unlocked
(the owner's 10-16 decision: `BH GPU Clock Lock` disabled, `bh_health.cmd` `FOUNDRY_TARGET_CLOCK=0`). Recovery is an operator-authorized reboot. The 10-19 recovery is verified
by boot time **20:33:42 CT**, a successful nvidia-smi read and the health daemon's `card healthy` report. The factory
handed the interrupted order back and Starfleet placed it on Mothership. Rollback of the clock policy, if the owner
chooses it: re-enable the task, restore `.bak-20261016-101225-pre-unlock`, restart the daemon through STOP_HEALTH,
and `nvidia-smi -lgc 1500`. Current platform inventory and prior test plan: §9.

## ⭐ The clock dose-response (2026-08-02) — real, but a mitigation, not the cause (see §9)

**This ASUS RTX 3070 boosts to 1965–1995 MHz. A reference 3070's rated boost is ~1725 MHz.
Above ~1700 MHz it cannot hold a SUSTAINED compute load and the GPU stops responding.**

| core clock | result | note |
|---|---|---|
| **1500 MHz** | **DIED at ~4,340 s CONTINUOUS** (2026-09-28, production) | qwen3.5:9b order 68 min straight into a qwen3:8b order, no idle gap; health daemon (20 s poll) saw no clock excursion — §0y |
| **1500 MHz** | **SURVIVED 3638 s — 105 images** | the ONE-HOUR SOAK, clean |
| 1500 MHz | **SURVIVED 911 s** (26 imgs) | |
| 1500 MHz | **SURVIVED 927 s** (27 imgs) | repeat — reproducible |
| **1695 MHz** | **SURVIVED 909 s** (27 imgs) | ≈ reference spec, ONE 15-min run only |
| **1750 MHz** | **DIED at 577 s** (9 imgs) | |
| 1800 MHz | **DIED at 240 s, hung on the FIRST image** | |
| 1965–1995 (factory boost) | **died 11/11 runs**, 131–387 s | |

**⇒ Ceiling is between 1695 and 1750 MHz.** Clean dose-response: the higher the sustained clock,
the faster it dies (1750 → 577 s, 1800 → 240 s).

**⚠ 1695 vs 1500 — pick with the test duration in mind.** TTF GROWS as the clock drops, so a
900 s survival at 1695 may only mean *its* TTF exceeds the 900 s window; it has never been soaked
longer. **1500 is the only clock soaked for an hour (3638 s / 105 images) and then proven on a
487-image production run.** For an hours-long job prefer 1500; 1695 is defensible for shorter work
and is what the memory entry recommends, but it is one 15-minute datapoint, not a soak.

**Cost of the fix: effectively nothing** — inference is memory-bandwidth-bound. Measured on the
487-image production run at 1500 MHz: **4.75 s/image**, card at **138 W / 47–53 °C** versus
250 W / 68 °C at factory boost.

⚠ **`-lgc` does NOT survive a reboot.** ✅ **SHIPPED:** scheduled task **`BH GPU Clock Lock`** on
BigHonker — `onstart`, SYSTEM, `/rl HIGHEST`, created with a past `/sd` so no stray one-shot timer
is armed (verified `Next Run Time: N/A`). Script `C:\Users\<user>\bh_clocklock.ps1` (sha256-verified
on host), logs each boot to `C:\Users\<user>\clocklock.log`, and **verifies by READING BACK
`clocks.sm`** rather than trusting the set call.
⚠ Gotcha inside that verifier, found by testing it: query **`clocks.sm`** (current), NOT
`clocks.max.sm` — the latter reports the card's 2100 MHz ceiling and made a successful lock log a
false FAIL.

⚠ **Why a locked 1800 dies FASTER than a dynamic 1965–1995:** a lock HOLDS that exact frequency
continuously, while factory boost varies with load/power/temp and spends real time lower. Sustained
clock is the stressor, not peak clock. This also explains 11 months of clean logs then sudden
onset: **nothing in software changed — the box's JOB changed.** Bursty chat/embed work never holds
a high clock for minutes; VLM captioning does.

**Evidence that always pointed away from a dying card, and that I under-weighted:** zero WHEA
events ever (no machine-check, no PCIe AER) · TDR never fires · dies COOL (67 °C) and INSIDE the
power budget (257/280 W) · PCIe Replay Counter 0 to the instant of the hang.

**Status of the earlier hardware verdict: WRONG, retracted.** See the retraction note below —
kept deliberately, because the reasoning error is more reusable than the answer.

### ✅ PRODUCTION VALIDATION (2026-08-02 13:23–14:02) — the fix proven on a real job

Not a synthetic soak: the full `nsfwmultitest2` corpus captioned on BigHonker, in an isolated
folder (`I:\claude-3070-test\`) so it could not touch the main session's work.

| run | result |
|---|---|
| gonewild | **246/246 captioned, 0 failed, 0 refused**, 38,624 words, 19m29s |
| realgirls | **241/247** (6 correctly skipped — see below), 0 failed, 0 refused, 39,687 words, 19m28s |
| **total** | **487 captions, ~39 min of continuous 90–94% GPU load, ZERO crashes** |

Card uptime through both runs: **175 minutes, boot unchanged.** For contrast, at factory boost this
workload killed the box at images 3–12, eleven times out of eleven.
⚠ The 6 skipped images were correct behaviour, not a fault: my `tar` copy excluded `*.txt` but not
`_recaption.jsonl`, so the pilot's audit log came along and the resume logic honoured it. **The
audit trail IS the resume state — copying a corpus without it, or with it, changes what runs.**

### ⚡ 9.1× CAPTIONING SPEEDUP — the schema kills the thinking preamble

`qwen3-vl` emits a thinking preamble regardless of `"think": false`, and it was ~90% of generated
tokens. Wrapping the caption in a one-field GBNF schema makes those tokens unemittable.
Measured on 5 images, identical prompt/clock/host — only the schema moves:

| arm | s/img | eval tokens | caption words | 493 images |
|---|---|---|---|---|
| free prose, 768 px | 32.3 | **1871** | 184 | 4.4 h |
| **schema, 768 px** | **3.6** | **199** | 174 | **0.5 h** |
| schema, 512 px | 4.5 | 187 | 172 | 0.6 h |

Production rate through the real factory path was **4.75 s/img** (the bench excludes per-image disk
I/O, audit-log writes and fleet status publishes). Matches the lab's earlier measurement of the same
effect (eval 2438 → 118).
⚠ **512 px is SLOWER than 768 px once generation stops dominating** (4.5 vs 3.6) — the fixed costs
take over. **Keep 768 px and its finer detail**; this inverts the earlier resolution trade-off.
`prompt_eval` is 1367 tokens and identical across all arms — the vision encode is now the floor,
so further gains must come from the image path, not generation.

Shipped in `E:\dataset-runner\p20_dataset.py` as `DESCRIBE_SCHEMA` on the `describe` profile, with
`num_ctx` dropped 8192 → 4096 (the larger context existed only to hold the preamble, and 8192 made
the 5.89 GB model spill to 88.7% resident on the 8 GB card).
⚠ **With a grammar, ollama files the answer under `message.thinking` and leaves `content` EMPTY** —
this path must read BOTH, which is the OPPOSITE of the free-prose path's rule where `thinking` is a
genuine scratchpad and must never be used. The grammar is what makes the fallback safe.

### 🐛 DEFECT FOUND AND FIXED: every successful run exited 1

`raise SystemExit(main(sys.argv[1]))` — `main()` returns an int (0/1) on the acquire path, but the
early-return modes (**recaption, curate, publish, dedupe, stash, recheck, promote, collect**) return
their RESULT DICT. `SystemExit(<dict>)` prints the object and exits **1**. So a 246/246 recaption
that finished perfectly reported FAILURE to anything checking exit codes — including the dataset
agent. Fixed: only a real int becomes the exit code, a result object means success.
**Found only by running the thing end-to-end;** a bench would never have surfaced it.

### ⚠ ACCURACY: the schema bought SPEED, not RELIABILITY

Claude initially reported the speedup as "no quality loss" on the basis of word count (184 → 174)
and one eyeballed sample. **That claim was wrong and is retracted.** A reproducibility audit —
12 images, captioned TWICE, same model/host/prompt/clock, comparing checkable claims rather than
wording — found:

| checkable claim | agreement across two IDENTICAL runs |
|---|---|
| **state_of_dress** | **5/12** |
| **hair_colour** | **6/12** |
| posture | 11/12 |
| mirror_selfie | 11/12 |

Unambiguous prose-level contradictions on the same image: **hair "brown" vs "blonde"** ·
**"underwear" vs "clothed"** · **"leaning backward" vs "leaning forward"** · **"leg extended
straight down" vs "leg crossed over thigh"**. Mean length swung 178 → 164 words.

---

**Superseded status line (2026-08-02, before the clock ladder):** 🔄 IN FLIGHT — root cause NOT
established. The big win is a **deterministic repro that fires in 2–5 minutes every time**, which
the July investigation never had; 11 hypotheses eliminated with it. See §0.

> ⚠ **RETRACTION — this doc briefly said "RESOLVED — FAILING HARDWARE" on 2026-08-02.** That was
> premature and Claude was wrong to write it. the owner's challenge is the reason it came out:
> *"That's your lazy explanation, and even if it's true this time, you're the bot that cried
> wolf."* He was right, and the flaw was structural: **all 7 arms varied the WORKLOAD while
> holding the SOFTWARE STACK constant** — same Windows install, same driver stack, same 22 vendor
> services, same low-level I/O drivers. Seven variations on one axis LOOKS exhaustive and is not.
> "The hardware is broken" was the conclusion requiring no further work, which is exactly why it
> should have been the most suspect. Compare the standing doctrine row: *"If a story starts
> drifting toward 'the hardware is unreliable', the fault is mine — it has been every time."*

---

## 0x. ⚠ 2026-09-29 — TWO `0x133` bugchecks AT the 1500 MHz lock, each 6–7 min into a qwen3-vl:8b coding load

Found 2026-10-02 by the read-only P34 BigHonker menu review (Sonnet subagent, `LLM-Project\34-BigHonker-Menu-Review-20261002.md`);
the event log re-verified on the box by the main session the same morning. Not in this doc or any journal until now.

| time (09-29) | evidence |
|---|---|
| ~00:28:47 (per-second logger) | card stops reporting; logger flat at **1500 MHz, 56 C, 135 W** until it does — no thermal or power signature |
| 00:31:28 | System 1001 WER: **`0x00000133 (0x0000000000000001, 0x0000000000001e00, …)`** — parameter 1 = `0x1`, the CUMULATIVE DPC watchdog; Kernel-Power 41 + EventLog 6008 alongside |
| ~00:40:37 (per-second logger) | second stop, same flat signature |
| 00:43:11 | second 1001: the same `0x133 (0x1, 0x1e00, …)` |

Read-only forensics (Sonnet subagent, key facts re-verified on the box by the main session):

| time | evidence |
|---|---|
| 09-27 18:18:27 | boot; health daemon `up (poll 20s, clock lock 1500 MHz)` — and NO lock alert for the whole boot (5 of the previous 6 boots showed the boot-time lock race) |
| 09-27 22:00:43 | nvlddmkm **153 "Error occurred on GPUID: 100"** during a cold llama-server spawn (ollama: `GPU discovery watchdog timed out`); ollama retried, runner up 8.5 s later, normal GPU speed all evening — a **milder, self-recovering 153** not seen in this doc before |
| 09-28 16:34 → 17:42 | `hardbench-fleet-f2-t06-think-9b` (qwen3.5:9b, thinking on) — 75/75 rows, clean |
| 17:42:45 → | `…-q3-8b` picked up with NO idle gap; fresh healthy runner spawn; continuous load |
| 17:45 → 17:46:37 | an in-flight generation's token rate declines smoothly (67.4 → 62.4 t/s, same curve as every earlier task) — no stutter, no thermal/power signature |
| **17:46:37** | **nvlddmkm 153 (bus-reset TDR); `nvidia-smi`: "GPU is lost. Reboot the system to recover this GPU"** |
| 17:46:46 | `dwm.exe` and `ShellExperienceHost.exe` crash (Application 1000) — the desktop compositor died with the card: a hard device-bus event, not a CUDA-only fault |
| 17:46:54 → | health daemon `ALERT: GPU LOST` every 20 s; ollama `/api/ps` still answers (control plane never touches the GPU) and still reports the model resident |

**Ruled out (System + Application logs, 60 h, counts asserted):** sleep/resume (0 Kernel-Power events near 17:46;
keep-awake held 16:34–18:47), WHEA / PCI AER / bugcheck / TDR-recovery 4101 (0 each), a driver change (32.0.16.1088,
7/21/2026 — the 610.88 of §0z), power plan / ASPM (High performance, ASPM Off — the documented config).

**First run WITH the per-second logger (2026-09-28 21:10–22:28, the same 9B thinking-on order at `num_ctx` 7,168,
~75 min continuous — longer than the fatal 72): the card SURVIVED and the record is flat.** 4,175 rows: core 54.6 →
55.0 °C, hotspot 71.7 → 72.5 °C (gap 17.1 → 17.5), fans 53 → 55 %, power 132–134 W, clock never above 1500 MHz at
1 s resolution, 0 nvidia-smi failures. So no core/hotspot heat soak and no clock excursion in this run — but one
surviving run does not clear the card (n = 1; the fuse is not a fixed timer), and memory/VRM temperatures are not
visible on this card (GDDR6: no memory-junction sensor). The throttle-reason field read `0x1` throughout; with a locked
clock it may carry little information. The next failure will have its full per-second lead-up.

Twice in one day (first ~overnight→08:30, again between 11:08 and 11:39, while IDLE after light
9B text inference) the compute path died with a signature this doc had not seen:

| observation | value |
|---|---|
| `llama-server` on any load attempt | `exit status 255: CUDA error: shared object initialization failed` (`cudaFuncGetAttributes` in the serve log) |
| nvidia-smi | ANSWERS normally — idle card, 27 W / P5 / 54 °C, no "GPU is lost" |
| System event log, 30 h window | **0** × Event 153 / 1001 / 41 (checked, count asserted) |
| sleep/resume events, 30 h | **0** — the box never slept, so the "sleep/wake staleness" story from the 08:30 wedge is unsupported |
| clock lock | in force (`clock 1500 <= lock 1500` on the deck) — NOT the solved OC class |
| ollama binary | unchanged since 2026-07-23 (auto-update eliminated) |
| `Ollama Serve` task restart | cured the 08:30 wedge; did NOT cure the 11:39 one — a FRESH server still fails init, so the sick state is driver-side, not in the server process |

⚠ Same reboot, second finding: the onstart `BH GPU Clock Lock` readback logged **FAIL @ 1905 MHz**
(boot-time load raced the lock); the health daemon alerted, re-applied, RECOVERED — guard chain
proven live. And the verifier's OLD entries (08-12, 08-18) log FAIL @ **210 MHz** on healthy boots
— idle P8 sits below the floor, so the check cried FAIL in both directions. **Fixed 2026-09-28 (the owner: "yep")**:
`bh_clocklock.ps1` now logs OK when the set call returns 0 and the readback is at or below the lock (+30), and FAIL
only for a failed set call or a reading ABOVE the lock (backup `bh_clocklock.ps1.bak-20260929`). A boot-time
readback cannot prove the lock under load; the foundry health daemon's `clock <= lock` check on the deck does that.
First run: `OK lock set to 1500 MHz (set rc 0; readback 1500 MHz)`.

## 0. ⭐ STATE OF THE INVESTIGATION (2026-08-02, Claude + the owner) — read this first

**The card hangs under ANY sustained CUDA load in 2–5 minutes**, while cool (54–68 °C), inside its
power budget (228–257 W of 280 W), on a clean PCIe link (**Replay Counter 0**), with no ECC errors.
It hangs **IN** the loaded state — telemetry freezes mid-stride at ~249 W / 1965 MHz / 99 % — and
the driver then evicts it. Reboot is the only recovery, and it is temporary.

**Repro:** any continuous GPU workload for >3 min. Fastest is VLM captioning
(`scratchpad/vlm_arm.py`), but **pure PyTorch fp16 GEMM does it in 195 s with no llama.cpp
involved at all**.

| arm | workload | variable under test | TTF |
|---|---|---|---|
| 1 | vision (ollama qwen3-vl 8b) | baseline, driver 610.62 | 218 s |
| 2 | vision | **core power −29 %** (280→200 W) | 191 s |
| 3 | vision | **GDDR6 clock −28.6 %** (7001→5001 MHz) | 230 s |
| 4 | vision | **driver 610.62 → 610.88** | 131 s |
| E | vision | **idle gaps removed** (`gap_before` 0.00 s) | ~150 s |
| T | **text only** | vision code path removed | ~300 s |
| **S** | **pure torch GEMM** | **llama.cpp / ollama removed** | **195 s** |

**Eliminated by measurement:** core power · memory clock · driver version · residency/spill ·
idle→load transitions and PCIe link renegotiation · vision-vs-text path · llama.cpp/ollama
entirely · thermals · power budget · PCIe link integrity · ECC. Combined with §3's earlier
eliminations (WHEA, PSU sag, HAGS, MSI/affinity, vendor services, context churn), nothing
software- or config-side remains.

**Why it started 07-23 after 11 months of clean logs** — the long-standing open question in §6,
now answered without needing a software culprit: **nothing in software changed; the box's JOB
changed.** Light 8B brain/embed duty never sustains load for three minutes. Captioning and heavy
generation do.

### 0c. Second batch of arms — the SOFTWARE STACK, which batch 1 never varied (2026-08-02 morning)

| arm | variable under test | images | TTF | verdict |
|---|---|---|---|---|
| P | **Parsec Virtual Display Adapter DISABLED** | 4 | ~168 s | died — Parsec ELIMINATED (the owner predicted this) |
| H | **HAGS ON** (`HwSchMode=2`) | 6 | ~177 s | died — HAGS eliminated. *Silent 153, box did not reboot* |
| C2 | **CLEAN BOOT — 22 vendor services disabled** | 8 | ~318 s | died — user-mode vendor services largely exonerated |

⚠ **Confound I introduced and must own:** the 610.88 install used `-clean`, which RESET driver
settings including HAGS. `HwSchMode` was found NOT SET afterwards. So arms 1–3 (610.62) and arms
4/E/T/S (610.88) may not have had matching HAGS state. Both groups died, so no eliminated
hypothesis is rescued — but it was an unnoticed second variable. the owner's expectation is HAGS ON;
it is now explicitly set to 2 and verified after reboot.

**Possible partial effect, NOT claimed as a result.** Work completed before death, which is fairer
than wall-clock (per-image token counts vary 500–2750):

| arm | images | cumulative eval tokens | TTF |
|---|---|---|---|
| E baseline (no gaps) | 3 | ~7,600 | ~150 s |
| P Parsec off | 4 | ~10,400 | ~168 s |
| H HAGS on | 6 | ~10,800 | ~177 s |
| C2 clean boot | 8 | **~19,600** | **318 s** |

Clean boot roughly DOUBLED the work before failure. **This is n=1 per config and TTF has ranged
131–300 s with NO config change at all, so this may be pure noise.** Recorded as a lead, not a
finding. Anyone continuing this must repeat the clean-boot arm ≥3× before believing the trend.

### 0e. Fleet planning: the two hosts CANNOT run the same captioner

Measured 2026-08-02 (`/api/ps`, both hosts live):

| host | model | size | resident | ctx | quant | rate |
|---|---|---|---|---|---|---|
| BigHonker (3070, 8 GB) | `huihui_ai/qwen3-vl-abliterated:8b` | 5.51 GB | 100% | 4096 | Q4_K_M | **4.75 s/img** |
| Mothership | `aha2025/llama-joycaption-beta-one-hf-llava:Q8_0` | 8.82 GB | 100% | 8192 | Q8_0 | **~3.5 s/img** |

**JoyCaption Q8 is 8.82 GB and does NOT fit an 8 GB card.** So splitting ONE corpus across both
hosts means two different models writing captions into the same training set — a **provenance**
problem, not merely a speed one. Splitting DIFFERENT corpora across them is fine.

⚠ **The "3070 is ~10× slower" figure is OBSOLETE.** It was measured at 31–35 s/img on the
free-prose path; with the schema the 3070 runs **4.75 s/img vs Mothership's 3.5** — comparable, not
an order of magnitude. Any fleet arithmetic using the old number is wrong.

⚠ **SUPERSEDED 2026-08-06 — Softserve IS fleet-wired now, and has been since 2026-08-03.** It runs
its own factory at `E:\foundry` (`SS Foundry Runner`), and its archive holds **6 caption orders**
from 08-03 (`ssfirst`, `ssfirst2`, `ssfirst3`, `female3_softserve`, `gendercheck2_softserve`,
`quarantinereview_softserve`). the owner, 2026-08-06, on the older "doctrinally NEVER" phrasing:
*"obviously it's not never for softserve haha, just very specific scenarios."*

**The rule that actually holds** is narrower than the paragraph below, which is kept for its
reasoning: ordinary work (captioning, CPU tools, hosting a factory) is FINE. What is still barred
is **CRASH-CLASS testing** — clock soaks, anything that can hang the box — because Softserve is the
Plex server, the Hyper-V host, and the machine Claude's own session runs on, so hanging it removes
the tool needed to fix it. Those still **require the owner home and ready**.

⚠ *(original note, 2026-08-02, superseded above)* **Softserve is deliberately NOT fleet-wired.** Its
3070 Ti has never been clock-soaked and is likely factory-OC'd like BigHonker's 3070, so adding it
is a CRASH-CLASS test. It is also the lab's main server and the host Claude's own session runs on —
hanging it removes the tool needed to fix it. **Requires the owner home and ready** (2026-08-02). The fleet machinery itself already exists:
`_resolve_vlm_fleet()` (p20_dataset.py ~line 2111) implements "serialize per GPU, parallelize across
the fleet" and `curate` already uses it; the `describe`/`recaption` path takes a single `vlm_host`
and has never been wired to it. That is the one-function gap.

⚠ The `DEFAULT_VLM_FLEET` comment excluding the 8 GB cards gives two reasons — "the 8B is SLOWER
than the 27B" and "unreliable at JSON". **Both are now measurably false**: the schema makes JSON
mechanically valid (487/487, zero failures) and the rate is comparable. That comment should be
updated before anyone plans off it.

### 0d. Where it stands / next

**Still loaded through every arm above:** the low-level third-party KERNEL drivers, which load at
boot independent of their services — `PawnIO` · `Asusgio2`/`Asusgio3` (AsIO2/AsIO3.sys) ·
`IOMap64` · `XTUComponent` (Intel XTU, iocbios2.sys) · `semav6msr64` (MSR read/write) · `MsIo64` ·
`CtiAIo` · four Razer drivers (`RzCommon`, `RzDev_024e`, `rzendpt`, `rzudd`). **These are the
direct port-I/O / MSR / PCI-config-space drivers — the only plausible software mechanism left for
knocking a device off the bus, and the more suspicious half of the vendor surface.** Next arm:
disable them (state recorded, reversible) and re-run. Awaiting the owner's go — kernel-driver changes
are a higher bar than services.

**The definitive Windows-vs-silicon test remains unrun: boot a Linux live USB and run the same
sustained load.** Dies there ⇒ silicon, and nothing done in Windows can matter. Survives ⇒ the
fault is in this Windows install. Needs a USB stick and a physical boot, so it is the owner's to run.

**Operational stance meanwhile (NOT a verdict on the card):** short chat/embed bursts on BigHonker
are demonstrably fine — 11 months of them — so it remains the primary 8B brain + embed host.
Sustained compute (VLM captioning, long generations, training) goes to Mothership until this is
settled. **Do not ship a power/clock band-aid: there is no operating point to retreat to** (proven
by arms 2 and 3).

**Two independent sources pin the death to the same second.** The torch loop's own iteration
counter and the GPU telemetry stream were captured separately:

| source | last good sample |
|---|---|
| `nvidia-smi` telemetry (2 s interval) | frozen from **01:08:38.817** |
| torch stress loop (iteration counter) | last print **t+197 s = 01:08:38** |

The loop ran **7,575 iterations at a dead-flat 38.5 it/s** — no ramp, no throttle, no degradation —
and stopped mid-stride. **This is a hard instantaneous failure, not progressive degradation**, and
it rules out any "the card was slowly getting worse under load" reading. (The loop's output was
invisible until the box rebooted: ssh block-buffers a remote process's stdout, so a hung CUDA
client shows NOTHING even with `flush=True`. Worth knowing before concluding "the script never
ran" — check the workload's own telemetry instead.)

### 0a. ⚠ NEW `gpu_health()` BLIND SPOT — frozen telemetry reads as a healthy card

§5 documented stale `size_vram` surviving a card's death. There is a worse variant: **a zombie NVML
handle keeps serving the dead card's LAST READING forever.** After the hang, `nvidia-smi -l` kept
emitting rows for 36 minutes showing 248.94 W / 1965 MHz / 99 % util / 68 °C — a perfectly
plausible *card under load*. A naive "is it busy and warm?" check passes with flying colours on a
corpse. (This nearly cost a wrong conclusion: counting samples-above-80 %-util read as a 39-minute
SURVIVAL when the card had died at 195 s.)

**Detection rules that actually work:**
- **Test for CHANGE, not for plausible values.** Real power readings jitter; identical
  floating-point watts to 2 dp across consecutive samples ⇒ dead. (Observed: 249.17 · 248.67 ·
  248.35 · 249.31 · 249.00 → then `248.94` forever.)
- **`nvidia-smi -q` HANGS rather than errors** on a wedged card — any health check that shells out
  to it needs a **timeout**, or the checker wedges too. Live hazard for the fleet gpu-state pusher.
- A CUDA client hangs uninterruptibly (python PID survived 40 min on a dead card); process liveness
  proves nothing.

---

## Original investigation (2026-07-26/27) — narrowing that led here

**Method note (the owner's standard):** *"these seem like bad fixes and not in line with the cool surgery
we did with AMD"* and *"this seems like level 1 service desk 'try uninstalling updates'"* — both
correct, and both course-corrections away from parts-swapping. The AMD precedent
([[Driver-RootCause-Investigation-Plan]]) is the bar: instrument, get the trigger to fire ON DEMAND,
prove it, then guard it in our own code. **We are not there yet on this box** — see §6.

---

## 1. The failure

```
BUGCHECK 0x133 (DPC_WATCHDOG_VIOLATION)
  Arg1 = 1        <- CUMULATIVE variant: total time at DISPATCH_LEVEL, NOT one long DPC
  Arg2 = 0x1e00   <- watchdog period in ticks
  Arg3            <- pointer to nt!DPC_WATCHDOG_GLOBAL_TRIAGE_BLOCK (NOT the offending routine)
```

⚠ **Arg1=1 is the cumulative variant.** Read as "a single DPC overran" for several hours, which sent
the whole investigation toward "find the bad routine". It is **system-wide DISPATCH_LEVEL
saturation**, so the fix direction is *reduce total interrupt/DPC work*, not *find one function*.

⚠ **Arg3 is the triage block, not the DPC address.** Its low bits are identical across every crash
because it is the same kernel structure — that similarity is meaningless for identification and was
briefly mistaken for "the same routine every time".

### 1a. Two distinct failure MODES, not one

| mode | signature | notice |
|---|---|---|
| bugcheck | Event 1001 + minidump, box reboots | obvious |
| **silent device loss** | **no bugcheck, no dump**, box stays up | **invisible** — see §5 |

---

## 2. Debugger evidence (5 minidumps, `kd !analyze -v`)

| time | bucket | symbol | process |
|---|---|---|---|
| 10:45 | `0x133_ISR_nt!KeAccumulateTicks` | `nt!KeAccumulateTicks+59c` | System |
| 10:51 | `0x133_ISR_nvlddmkm` | `nvlddmkm+fd40f` | System |
| 10:57 | `0x133_ISR_nvlddmkm` | `nvlddmkm+fd40f` | **llama-server.e** |
| 18:08 | `0x133_ISR_nvlddmkm` | `nvlddmkm+de8fc` | System |
| 21:29 | `0x133_ISR_nvlddmkm` | `nvlddmkm+102a9f` | System |

**The offsets VARY and one crash names the kernel's clock-tick catch-up path** (`KeAccumulateTicks`
fires when the box has *missed timer interrupts*). So there is no single hot routine — whatever is
executing when the cumulative budget expires gets blamed. That is starvation, and it means chasing a
specific `nvlddmkm` offset is a dead end.

Debuggers were installed via the Windows SDK bootstrapper (winget's community source is broken on
this box, `0x8a15000f`; the Store MSIX refuses over an ssh **network logon**, `0x80070520`; there is
no console session for `/it`). The installer was **Authenticode-verified Microsoft-signed before
being run**.

---

## 3. What has been ELIMINATED, with measurements

| hypothesis | verdict | evidence |
|---|---|---|
| Failing hardware | **NOT tested** (downgraded 2026-10-05, §9) | zero WHEA events — but a consumer-board GPU fault does not raise WHEA, and NVIDIA treats Xid 79 as hardware/power; zero WHEA says nothing about the card |
| PCIe link instability | ruled out | Gen3 x16 at max, stable under load, **0 replay rollovers** |
| PSU sag | **NOT tested** (downgraded 2026-10-05, §9) | smooth 14.7 W → 235 W in ~1 s NVML board-power samples — an average that cannot see the millisecond transients that trip PSU protection or sag a rail; PSU model, age and cable topology never recorded |
| Thermal | ruled out | 39 → 61 °C, clocks pinned 1995 MHz, no throttle |
| Power plan / PCIe ASPM | **not the cause** | crashed *with* High-performance + ASPM Off applied and verified |
| "Dies leaving compute" | ruled out | crashed *during* sustained inference at 87 % util |
| Concurrent GPU consumers | ruled out | **no other process on any GPU engine**, idle or loaded — Parsec's virtual display is present but completely idle |
| HAGS | not the fix | DPC −77 %, **ISR unchanged** (§4) |
| Interrupt affinity / MSI vectors | inert | `MessageNumberLimit=1`; raising to 8 + spread changed nothing measurable |
| Reserving core 0 for the driver | no effect | ISR 3.42 % vs 3.65 % — noise |
| Vendor bloat (Armoury/AURA/Razer) | small | ISR **avg** ~20 % lower, **peak unchanged**. RGB kept (the owner) |
| New workload | ruled out | **196 heavy generations BEFORE the first crash, zero bugchecks**; 07/12 ran 87 heavy + 30 loads clean |
| GPU driver change | ruled out | unchanged since **2026-03-29** |
| Context churn (model swap) | **repro NEGATIVE** | 24 rapid evict+load cycles, rock-steady timings |
| Idle eviction (`keep_alive`) | **repro NEGATIVE** | 2 rounds through the boundary, evicted cleanly at +6 m |
| Vision path (images) | **repro NEGATIVE** | 30 images through `qwen3-vl`, ~1.0 s each, zero failures |
| CorridorKey as 2nd CUDA consumer | not present | no install, no task on this box |

---

## 4. What MOVES the numbers (harness: `bh_harness.py`, results in `dpc_results.csv`)

Per-core `% DPC Time` / `% Interrupt Time` under identical load:

| config | core0 DPC avg/max | core0 ISR avg/max | throughput |
|---|---|---|---|
| HAGS-ON (baseline) | 1.67 / 10.91 | 2.93 / 15.61 | 53.5 tok/s |
| HAGS-OFF | **0.39 / 4.66** | 3.65 / 15.43 | 53.6 tok/s |
| HAGS-OFF + spread (1 vec) | 0.71 / 7.77 | 3.16 / 14.05 | 51.0 tok/s |
| HAGS-ON + MSI×8 + spread | 1.79 / 9.31 | 3.39 / 13.86 | ~52 tok/s |
| stock, graphs ON | 1.84 / 9.37 | 3.65 / 16.99 | 53.5 tok/s |
| **stock, CUDA graphs OFF** | **14.20 / 40.09** | **8.35 / 26.28** | — |
| vendor services stopped | 2.06 / 10.85 | 2.92 / **10.91** ⚠ | 61.6 tok/s |
| vendor services stopped (repeat) | 1.64 / 9.37 | 2.81 / **15.42** | — |

⚠ The 10.91 % ISR peak did **not** reproduce (15.42 % on the repeat). Reported as a 36 % win before
the repeat ran; the repeat is what caught it. **Single runs cannot resolve these peaks.**

### The two real findings

1. **CUDA graphs are worth ~7.7× DPC and ~2.3× ISR, and they are already ON by default.** Disabling
   `GGML_CUDA_DISABLE_GRAPHS` sends DPC from 1.84 % → 14.20 % (peak 40 %). This is the largest lever
   found by an order of magnitude, it confirms interrupt load scales with GPU submission count, and
   **nobody in the community threads has quantified it**. Keep graphs enabled.
2. **ISR is invariant to everything reachable from Windows.** 13.9–17.0 % peak on core 0 across HAGS
   on/off, 1/8 vectors, spread/default, core-0 reserved or not, vendor services on/off. Cores 1–5
   never exceed 0.25 %. The interrupt cost lives inside `nvlddmkm`.

**Hardware context:** i5-9600K, **6 cores / 6 threads (no hyperthreading)**, one of which services
every GPU interrupt. Mothership and Softserve have 16 and have never produced this bugcheck.

---

## 5. ⚠ `gpu_health()` had a blind spot — FIXED

A card lost *after* a successful load reports **100 % resident forever** (`size_vram` is written at
load time and never revised). Observed 2026-07-26 19:29:

```
nvidia-smi   "GPU is lost. Reboot the system to recover this GPU"   <- only accurate source
Windows      Status=OK  ConfigManagerErrorCode=0  CM_PROB_NONE
ollama       qwen3.5:9b 100% resident (5.58/5.58 GB)                <- stale bookkeeping
gpu_health() HEALTHY                                                <- WRONG
llama-server 1046 s of CPU, inferring a 9B on six CPU cores         <- reads as "the box is slow"
```

The `<5 %-VRAM` heuristic only catches a model that loaded onto an **already** dead card — the
opposite order. **Fix:** the box pushes its own nvidia-smi verdict (`POST /fleet/gpu-state`,
scheduled task every minute as SYSTEM). Stale **LOST stays LOST**; stale **ok** is discarded.
"Could not ask" is never "dead". AMD box unaffected. 7 tests.

⚠ Scheduler policy note #9 (`nvidia-smi is NOT a usable signal`) **corrected**: it conflated *busy*
(true — it cannot tell you) with *alive* (false — it was the only source that knew).

---

## 6. Where it stands

**Crash rate fell ~10× on 2026-07-26**, but the causes are confounded (driver 610.62 *and* the power
config landed close together):

```
before:  10:21 10:27 10:33 10:39 10:45 10:51 10:57   (6 in 36 min, driver 591.86)
after:   18:08, 21:29 + one silent device loss 19:29 (2-3 in ~11 h, driver 610.62)
```

**Three targeted repros all came back clean** because they were hunting a *morning* failure rate
against an *evening* configuration.

⚠ **Every config comparison in §4 is underpowered for stability.** Three-request runs cannot resolve
a ~2-per-11-hours event. §4 measures interrupt load honestly and says nothing about crash immunity.

**Open onset question:** 11 months of clean logs (System log retains back to 2025-08-30), first-ever
bugcheck 07-23 20:12. Candidate changes in the window: the July Windows cumulative (07-15 → 26100.8875,
07-21 → 26100.8894 / KB5121767), and our own 07-23 CorridorKey/CUDA install + SAC-off on that box.
The GPU driver is ruled out (unchanged since March) and so is the workload. **Not resolved.**

⚠ **KB5121767 is a FIX, not the culprit** — an out-of-band repair for KB5101650's clash with the
**Intel Innovation Platform Framework** driver ("changes in performance, power consumption, or system
behavior"). BigHonker has `Intel(R) Power Engine Plug-in` and an Intel i5-9600K. Removing it would
move *backwards*; `wusa` refuses anyway (cumulative). Rolling back further means removing a
**Security Update** — the owner's call, not a default.

**Running now:** `HAI GPU Soak` — 8 h mixed workload (heavy text / vision+images / swaps), scheduled
`onstart` **so a bugcheck restarts it and the log records the crash-to-crash interval unattended**.
Log: `C:\Users\<user>\soak.log`. A session-bound watcher would die with the crash and tell us nothing.

## 7. Next

1. Read `soak.log` for the residual crash rate and what was running at the end.
2. Only with a rate in hand can any config be compared on stability.
3. If it stays probabilistic, the engineering answer is the one already shipped: detect the lost card
   in <1 min and route around it (§5) — the same shape as the AMD outcome, where the fix was a guard
   in our own code rather than a driver setting.

## 8. 2026-10-05 — two failures in one night under A1 (time-to-failure data, per §7)

Workload: A1 agentic coding orders (qwen3:8b / qwen3.5:9b thinking-on, one exercise after another), clock lock
1500 MHz (`clocklock.log` OK at 00:20:27 and 04:52:40).

| failure | when (BH local) | load before it | evidence |
|---|---|---|---|
| **0x133 DPC watchdog** | froze ~00:10 (last factory beat 00:09:47; EventLog heartbeat 00:02:51), dump 00:19:59, auto-reboot 00:20 | **~2.5 min** — A1 qwen3:8b spawned 00:07:33 after the card had been idle since ~21:50 | System 1001 `0x00000133 (0x1, 0x1e00, …)`, 41, 6008 |
| **GPU lost** (nvlddmkm) | 04:35:09 | **~4 h 13 min continuous** (orders back to back from 00:21:51) | factory: `GPU is lost. Reboot the system to recover`; recovered by reboot 04:52 |
| **GPU lost** (nvlddmkm) | 05:33:04 | **~39 min** — A1 qwen3:8b resumed 04:53:57 after the recovery reboot | factory: same message; A1 NOT resumed on BigHonker after this one (order cancelled, 8/25 solved kept); recovery reboot 05:37 |

**Per-second telemetry (`F:\foundry\var\state\telemetry\gpu_telemetry-20261005.csv`, read 2026-10-05 morning):**

| signal, 10 min before each failure | 0x133 | GPU lost #1 | GPU lost #2 |
|---|---|---|---|
| core clock | 1500 MHz every second | 1500 | 1500 |
| vcore | 0.77 V flat | 0.77 | 0.77 |
| temp / hotspot | 54-55 / ~72 C | 56 / 73-74 | 55 / 72-73 |
| power | ~141 W | ~148 W | ~148 W |
| load | 94-97 % | 96-97 % | 95-96 % |
| throttle reasons | 0x0 / 0x1 | 0x1 | 0x1 |

**The 00:10 failure does not fit a fuse that burns down under long load** — it came minutes into load after hours
idle; the 04:35 one fits it. No power event: BigHonker's apcupsd log shows only the two startups. Same night,
Mothership (AMD 7900 XTX, different vendor, different UPS) froze solid at ~00:13, also under A1 generation, with its
GPU clock collapsing twice in 30 s first (journal 2026-10-04, "Mothership FROZE SOLID") — two boxes within ~3 min,
cause unknown, recorded rather than theorised. Defences that worked: the factory ended the child, kept the order
queued and pinned, and resumed it after the reboot; the recovery reboot followed RUNBOOK-Ollama-Host-Operations.

> **2026-10-10 09:46 — variable changed:** GeForce driver **610.88 → 617.42 WHQL** on BigHonker (silent install, no
> reboot, clock lock re-applied and read back at 1500 MHz). Any 0x133 / device-loss row after this stamp is on 617.42.

## 9. ⭐ 2026-10-05 — ROOT-CAUSE REVIEW: the clock lock is a band-aid; power feed, platform and card never separated

the owner, 2026-10-05: *"lower the clock speed over and over is just putting a band aid on it and it wasn't actually
addressing the cause."* §0d already said so on 08-02 (*"Do not ship a power/clock band-aid"*) and the lock shipped
anyway. Six failures have happened AT the lock since (09-28 ×1, 09-29 ×2, 10-05 ×3).

### 9a. Platform inventory — never recorded before (measured read-only, 2026-10-05)

| item | value |
|---|---|
| CPU | i5-9600K, 6 cores / 6 threads, running ~4.35 GHz (`% Processor Performance` ≈117 % of 3.7 GHz = stock all-core turbo) |
| board / BIOS | ASUS PRIME Z390-P, BIOS **3006** (2021) — the latest ASUS publishes for this board |
| RAM | 2 × 8 GB Corsair CMK16GX4M2B3200C16 at 3200 (XMP) |
| GPU | RTX 3070, VBIOS 94.04.3a.00.e9, driver 610.88, PCIe gen3 ×16 capable (gen2 at idle), power limit 280 W (max 350 W) |
| tuning software | **AI Suite 3 (3.01.10) AND Armoury Crate (6.5.14.0) both installed** — AI Suite's Fan Xpert service (`AsusFanControlService`) runs; Intel XTU service + `iocbios2.sys` run |
| low-level kernel I/O drivers loaded | Asusgio2, Asusgio3, CTIAIO, MSIO, PawnIO, uiomap, semav6msr64, XTUComponent, IOMap, four Razer drivers |
| GPU power cabling | **ONE PCIe cable with a split (daisy-chain/pigtail) feeds both of the card's 8-pin inputs** (the owner, by eye, 2026-10-05). Card makers recommend two separate cables at this power class. At the 1500 MHz lock the steady draw is ~148 W (well inside one cable); at factory boost ~250 W — so if the pigtail matters, it is through transients and contact resistance, which 1 s telemetry cannot see. UNTESTED |
| PSU | **REPLACED by 2026-10-15: Lian Li 850 W dual-chamber, 80 Plus Gold** (the owner). **Cabling on the new unit: TWO separate PCIe cables, one per 8-pin, no pigtail** (the owner, 2026-10-21) — the 12 V feed is new end to end. The old unit's make/model/age was never recorded. The §9f step-1 test (factory boost, 3 × 15 min) on the new PSU is §9h |
| UPS | APC **Back-UPS ES 750G** (450 W nominal, standby type — passes mains straight through while online), 18 % load ≈ 80 W at idle; apcupsd logged no events at any failure. Gives whole-box draw at ~4.5 W steps — not transients |
| +12 V rail visibility | the board's Super I/O reports a +12 V reading; **FanControl 252** (installed 2026-01-09, which brought PawnIO) ships `LibreHardwareMonitorLib.dll` 0.9.4, which can read it. ⚠ `AsusFanControlService` (AI Suite 3's Fan Xpert) polls the same chip, so a second reader is a second variable. Log it during the crash-test session, at 1 s it shows a sustained sag, not ms transients |

### 9b. Eliminated or refuted today

- **CPU undervolt / tune via XTU** — XTU's own log says every boot *"BIOS unpatchable for undervolt protection"*, and
  `PerformanceSettings.bin` is 0 bytes. XTU applies nothing.
- **Smart App Control off (07-23) as the onset**, via the vulnerable-driver blocklist — refuted.
  `VulnerableDriverBlocklistEnable = 1`; Code Integrity blocks the revoked `AsIO.sys` (events 3023/3077) at the same
  cadence before and after 07-23 (06-24 → 10-05). Memory Integrity was already off by ~2025-10-15 (XTU logs). The
  AsIO2/AsIO3/MsIo/CtiAIo drivers have loaded on every boot since 2025-10-13 — nine months clean before the first
  bugcheck. They are still untested (§0d), but they cannot explain the onset alone.
- **Out-of-date BIOS** — it is current.

### 9c. Corrections to this doc

- §3's *"Failing hardware — ruled out (zero WHEA)"* and *"PSU sag — ruled out"* are downgraded to NOT TESTED (the
  rows say why). Both were checks of the wrong shape.
- **Unresolved contradiction:** §3 says *"New workload — ruled out: 196 heavy generations BEFORE the first crash"*,
  while §0 says *"the box's JOB changed"*. Both cannot carry the onset. Bursty heavy generations versus minutes of
  sustained load would reconcile them, but nobody has measured the sustained-load history before 07-23.
- **Rising rate (1 → 2 → 3 per night) is NOT established.** Last night was also the longest sustained load since the
  lock went on. Normalise failures per hour of load before claiming the card is getting worse.

### 9d. What the outside world says (searched 2026-10-05)

⚠ **Measured 2026-08-02 (Journal `2026-08-01-claude-3070-vlm.md`), and it bounds the cabling idea:** the PCIe
Replay Counter stayed at **0, with 0 rollovers, up to the instant of the hang**, and the link held Gen3 ×16. That
kills the signal-integrity family: slot contact, risers, link errors. It says nothing about the **12 V feed** (the
PCIe 8-pin pigtail, §9a), which is a separate path from the PCIe data link.

### 9f. Plan — one variable per test, scored with the FAST repro

**Instrument:** factory boost (lock removed) dies 11/11 in 131–387 s (§0 table). A change that survives 3 × 15 min
at factory boost is a real result. At 1500 MHz the time-to-failure is hours and noisy, so it cannot score anything.
This is crash-class testing on BigHonker: it needs the owner's go, and BigHonker pulled from the fleet for the session
(HAI's chat failover moves to the other boxes).

1. **Power feed (the owner, physical — scheduled for the evening of 2026-10-05):** the card is on ONE cable with a split
   (§9a).
   - Record the PSU make, model, wattage and age, and whether it has a second free PCIe cable.
   - Change exactly one thing: move to **two separate PCIe cables**, one per 8-pin.
   - Reseat the card and both connectors, and look for discoloured pins on both ends.
   - Then run the fast repro at factory boost, three runs of 15 minutes. If it survives, the pigtail or the contacts
     were the cause. If it still dies in 131–387 s, cabling is cleared with the right instrument.

   This goes first: it is cheap, it is the online consensus's top cause, and §3 "tested" it with the wrong instrument.
2. **Kernel I/O drivers (remote, §0d):** uninstall AI Suite 3, which is legacy and overlaps Armoury Crate. Stop the
   XTU / AsIO / MsIo / CtiAIo / IOMap / semav6msr64 / PawnIO drivers, with their state recorded so it is reversible.
   Then run the fast repro.
3. **Windows vs hardware (the owner, physical):** a Linux live USB, running the same torch GEMM stress at factory boost.
4. **Card vs platform:** the 3070 in another box, or another card in BigHonker. The most decisive test, and the most
   costly.

**Since 2026-10-05 ~11:40 BigHonker is BACK IN THE FLEET taking ordinary orders** (the owner: *"let it back into the
fleet to take orders. Let's just not intentionally crashing it"*): the 1500 MHz lock stays on, the factory now WAITs
instead of launching onto a lost card, and FanControl runs as a service. First order: the A1 v2.1 qwen3:8b lead
(`a1v21-py-agentic-bi-q3-8b.20261005-1140`), which doubles as the cooling comparison under real load.

### 9g. Results — the crash-test session (the owner's go 2026-10-05 09:30)

**Instrument:** compute order `bhcrash-<arm>-rN` with payload `workspace\bhcrash\bh_crash_ttf.py` (submitter
`submit_bhcrash.py` beside it). It runs ONE run per order: the documented arm-S workload (torch 2.13+cu126 fp16
8192² GEMM, ~5 GB resident), lock removed (`nvidia-smi -rgc`), and a 900 s budget. The verdict comes from the loop's
OWN iteration heartbeat, never NVML (a dead card's NVML serves its last reading): written by the run itself on
survival or a 30 s stall, or by the order's next start after a reboot. All four paths were tested on a CPU stub.

The health daemon's clock duty is set to 0 for the session (`bh_health.cmd`, backups `.bak-20261005-*`). This
needed a fix first: until 2026-10-05, `FOUNDRY_TARGET_CLOCK=0` did NOT disable the duty, despite the docstring and
the .cmd. Fixed in `p20_health_daemon.py` 0103e3ee, with 4 tests in `test_foundry.py`. (That session-wide switch
and its manual restore are no longer how a test runs: since 2026-10-16 the order owns the clock, §9h.)
|---|---|---|---|---|---|---|
| A r1 | baseline: lock off, software as of 10-05 | **died** | **311 s** | silent loss: nvlddmkm 153 one second after the last beat, no reboot | 1965–1980 MHz core, 6801 MHz mem, ~245 W | core 70 °C, **hotspot 89.3 °C and still climbing** (80 → 89 over the run, no plateau) |

| A r2 | baseline (same) | **died** | **265 s** | silent loss: nvlddmkm 153 at the last beat's second | 1980–1995 MHz, 6801 MHz, ~250 W | core 70 °C, **hotspot 89.5 °C** five seconds before death, 90.4 °C in the first frozen row |
| A r3 | baseline (same) | **void** | — | the factory LAUNCHED it 36 s after r2's card died (09:46:14, before the recovery reboot): the desk still read the card healthy, so CUDA init failed and the order was FAILED. Fixed same pass (below); re-run as `bhcrash-A-baseline-r1.20261005-1015` | — | — |
| A r3 (re-run) | baseline (same) | **died** | **246 s** | silent loss: nvlddmkm 153 one second before the last beat was 30 s stale | 1965–1980 MHz, 6801 MHz, ~250 W | core 70 °C, **hotspot 89.75 °C** three seconds before death, 90.5 °C frozen |

**Baseline: 3 of 3 died, 246 / 265 / 311 s (mean 274 s)**, all inside August's 131–387 s window — today's software
reproduces the fault at factory boost.

**GPU fan behaviour during the runs (per-second telemetry):** the fans rose from 0 to 56–67 % within 30 s and
settled at 73–74 %, tracking the CORE temperature, which plateaued at ~70 °C. The hotspot kept climbing to death, and
the core→hotspot gap widened under load (~17 → ~20 °C), a common sign of tired paste or poor cooler contact (an
indicator, not proof). The case fans run under AI Suite's Fan Xpert (`AsusFanControlService`), which most likely
follows the CPU; the CPU is near idle under this GPU-only load. Not yet read: case fan RPM (needs the Super I/O via
FanControl's LibreHardwareMonitor library).

**Case fans — FanControl is configured but never runs (read 2026-10-05, read-only).** the owner's intent: *"I could have
sworn I set them to react to the hottest component."*
- **Start:** FanControl's only launcher is a scheduled task with a LOGON trigger for `<user>`. BigHonker boots
  headless, so it never starts, and the case fans fall back to Fan Xpert (`AsusFanControlService`) and the BIOS
  curve.
- **Saved config:** `userConfig.json` was untouched since 2026-01-09, the install day, so nothing overwrote it. Case
  fans #1/#2/#3/#5 are on curve "Auto", sourced from the **CPU** (`/intelcpu/0/temperature/1`). A second curve,
  "Auto 1" (`/gpu-nvidia/0/temperature/0`), exists with **no fan assigned**. The "hottest component" version would be
  a Max(CPU, GPU core, GPU hotspot) mix curve.
- **Conflict:** if FanControl ran, it and Fan Xpert would both write the same Super I/O fan registers. One must own
  the fans.

**DONE 2026-10-05 (the owner: "similar configuration to my other two gpu computers… start it as system"; and "I don't
want to just be deliberately crashing the gpu all day… trying to mitigate and solve it").**
- FanControl V270 now runs as the boot-time SYSTEM service `FanControl.Service`, with config `BigHonker.json`
  (the peers' shape).
- Proven by a positive control: the GPU fans followed a manual 60 % on an idle card, then returned to the curve.
- The crash series STOPPED at the baseline. No fans-100 % crash arm is run.
- How it works and how to verify: [[RUNBOOK-GPU-Box-Fan-Control]].

**MEASURED 2026-10-05 ~12:30 — the fan change does not move GPU temperatures at the lock.** Loaded seconds only
(gpu_load ≥ 90), same lock, same model (qwen3:8b A1):

| | before, 10-04/05 night (n 9,905) | after, FanControl service (n 888) |
|---|---|---|
| GPU fans | 54.6 % | 59.1 % |
| core | 55.1 °C | 55.2 °C |
| hotspot | 72.5 °C mean / 74.8 max | 72.6 / 73.6 |
| core→hotspot gap | 17.4 °C | 17.5 °C |

At the lock the card is cool, and the peer-style curve barely moves the fans. **Every death AT the lock happened
with the hotspot at ~73 °C**, so heat is not what kills it there. The ~90 °C pattern exists only at factory boost
(and is confounded with time). A 17.5 °C gap is unremarkable, so there is no evidence for a re-paste. **The strongest
untested suspect is now the 12 V power path** — one split cable feeding both 8-pins (§9a); tonight's cabling change
is the next one-variable test.

**Case-fan A/B, 2026-10-05 12:35–12:50** (the owner: *"the case fans on that computer move a ton of air and could very well
affect the cooling on the top of the card"*). One variable: the case fans only (headers 1/2/3/5) on the Mix curve
(~57 %) versus a manual 100 %, same A1 load, GPU fans on their curve, 6 min of loaded seconds each (telemetry v4):

| | Mix (~57 %) | case 100 % | Δ |
|---|---|---|---|
| case fans #2 / #3 | 651 / 495 rpm | 914 / 681 rpm | +40 % |
| GPU core | 55.5 °C | 54.3 °C | −1.2 |
| hotspot | 72.6 °C | 71.4 °C | −1.2 |
| core→hotspot gap | 17.1 | 17.1 | 0 |

Case airflow is worth about 1 °C at the lock, and leaves the gap untouched. Together with the ~73 °C hotspot at
every lock-death, **cooling is not the cause at the lock.**

**Case Fan #1 (`/lpc/nct6798d/control/0`): not a stopped fan, a flaky TACH reading.** The A/B snapshot showed it at
0 rpm at the curve's 57 %. A step calibration (manual 30→100→30 %, service restarted per step, 5 × 1 s reads each)
found it **spinning at every level, 400–1,045 rpm, no start or stop threshold**. Across 486 s of normal running the
log has 30 single readings of 0, never more than 2 s in a row, against a median of 869 rpm and a max of 1,753. The
0s are dropouts in the reading, and they also explain why the calibration's averages are not monotonic (40 % read
545, 30 % read 718). So the fan needs no start/stop calibration. The open question is the tach itself (header,
fan or cable): **check which physical fan is on header #1** during the physical work.

**UPS (whole-box power) — new independent instrument.** Home Assistant's history of BigHonker's APC ES 750G
(`sensor.bighonker_load`, ~60 s steps) around every death:
- At the lock, the box drew ~49–52 % (≈220–235 W); at boost, ~79–80 % (≈355 W).
- Mains held 116–119 V with no status change, so wall power is ruled out.
- For both "GPU lost" deaths the UPS shows the box already at ~90 W **25–38 s BEFORE NVML reported the loss**, while
  NVML's rows in that span were still varying (live). Clocks verified to agree within 1 s.
- **Unresolved:** either the card stopped before the driver noticed, or a once-a-minute sample caught an A1 lull.
  Telemetry v4 now logs the UPS every 5 s on the card's own clock, so the next natural death settles it.

**Next — mitigation, measured without crashing:** compare the hotspot and the core→hotspot gap under BigHonker's
next ordinary load at the 1500 MHz lock against the pre-change record (§0y: core ~55 °C, hotspot ~72–73 °C, GPU fans
53–55 %, card-curve controlled). A lower hotspot and a narrower gap say the cooling change is doing work. A gap that
stays ~18–20 °C under better airflow points at the card's own paste and pads → a re-paste/re-pad (physical, the owner)
together with the cabling change.

⚠ **Confound, stated before anyone reads the hotspot pattern as a cause:** in these runs the hotspot climbs
monotonically with TIME, so "dies at ~90 °C hotspot" and "dies at ~4–5 min" are perfectly collinear. The
fans-at-100 % arm is the run that separates them.
⚠ **A pattern to test, NOT a finding: both deaths came as the hotspot reached ~89–90 °C**, climbing with no plateau,
while the core held ~70 °C. It cannot be the whole story: last night's three deaths AT the 1500 lock came at a
hotspot of 72–74 °C. A heat source the card does not report (GDDR6 has no junction sensor; nor does the VRM) would
fit both, and would also explain the clock dose-response. Cheap one-variable test: the same factory-boost run with
the fans pinned at 100 % (FanControl is installed and drives NVIDIA fans).

**Factory defect found and fixed by run 3 (class 9 — a transient machine condition consumed a work item):**
`card_check_before_launch` asked only the DESK, whose lost-card verdict arrives by a once-a-minute push. It now
also asks this box's own nvidia-smi (`local_card_lost()`): "GPU is lost", "No devices were found" or a timeout
(the wedge signature) count as unavailable, so a GPU order WAITs (LaneBusy) and is never FAILED. No nvidia-smi (the
AMD box) is could-not-tell. Tests: `test_foundry.py`, pin 436. `p20_foundry.py` 111eb364 is deployed on ALL
THREE GPU factories (BigHonker 10:14, Softserve 10:17 and Mothership 11:17 via pause slips). `fleet_deploy --check`
shows all in sync with no process predating its file, and Mothership's on-box gate is 436 OK. The payload also
now sets aside a `started.json` whose attempt never loaded the card, instead of recording it as a death.

### 9h. Stock-clock test on the NEW PSU — and how a crash test now runs unattended (2026-10-16)

**The one variable:** the PSU, now a Lian Li 850 W dual-chamber, 80 Plus Gold (the owner, 2026-10-15). the owner: *"now that
BH has a new psu, let's see if we can push it back to having stock clocks on the gpu and load the workload that was
making it fail. Just do it in the safe way that allows us to still fix it without me needing to intervene."* Same
instrument and budget as the 10-05 baseline (arm S GEMM, factory boost, 900 s): orders
`bhcrash-psu-lianli850-r1..r3.20261016-0001`. Baseline to beat: **3 of 3 died, 246 / 265 / 311 s**. Survival of all
three = the power path was the cause (one split cable / the old unit); deaths inside 131–387 s = the PSU is cleared
with the right instrument.

**How a stock-clock test runs without anyone at the console** (built 2026-10-16):
- **The ORDER owns the clock, not the session.** `submit_bhcrash.py` puts `"gpu_clock": "unlocked"` in the spec. The
  health daemon's `clock_owner()` stands its re-lock down ONLY while the stated run (`CURRENT_RUN.json`) is fresh and
  its spec declares `gpu_clock`. When the order ends by any route the factory clears the run and the daemon re-locks
  to 1500 within one poll; a dead runner's record goes stale after `CURRENT_RUN_STALE_S`. No `.cmd` edit, no restore
  step. Tests: `HealthDaemonClockDuty` (pin 485).
- **The order recovers the card itself.** After any verdict it saw (survived, silent loss, or a CUDA error mid-loop),
  `recover()` re-applies `-lgc 1500` on a live card, or schedules `shutdown /r /t 30` when nvidia-smi says the card is
  lost (only a reboot brings it back). After the reboot the boot task `BH GPU Clock Lock` re-locks, the factory resumes
  the order, and the payload finds its result recorded. A 0x133 bugcheck auto-reboots on its own (`AutoReboot = 1`,
  read 2026-10-16). Cleanup never raises.
- **Chat moves off the card by itself:** the order's fleet claim makes `scheduler.gpu_state` read BigHonker busy.
- **What it cannot recover:** a freeze with no bugcheck and no reboot (seen once, 07-26, before the ASPM fix). That
  one needs a power cycle.

| run | verdict | TTF | core clock under load (per-second telemetry, ≥ 90 % load) | power | hotspot med / max | system events |
|---|---|---|---|---|---|---|
| r1 (00:18:43) | **survived** | 900 s, budget spent | median 1965, 1965–1980 MHz | 256 W median | 85.8 / **92.9 °C** | 0, no reboot |
| r2 (00:33:52) | **survived** | 900 s | median 1965, 1965–1980 MHz | 253 W | 84.5 / 87.9 °C | 0 |
| r3 (00:49:02) | **survived** | 900 s | median 1965, 1965–1995 MHz | 250 W | 84.4 / 88.2 °C | 0 |

### 9i. 2026-10-21 — what separates death from survival on the NEW PSU (every load episode since 10-01, read-only)

Every sustained-load episode in the per-second telemetry (`load_episodes.txt` in
`Claude Home\research\bh-gpu-loss-20261021\`, 1,246 episodes, 21 days). Since the PSU swap:
- **Steady GEMM at 250–257 W (arm S): ~45 min, survived** (10-16 r1–r3) — and the stopped 96 s run above.
- **Dense 9B LLM inference at ~238 W, stock clocks: 3 deaths, at 196 s (10-19), ~235 s and 231 s (10-21)**; other
  dense-LLM episodes at 230–263 W ended on their own after 63–675 s (none ran longer).
- **The A3B (qwen3.6 35B MoE, partly on CPU) at 1995 MHz, ~115 W, VRAM full 7.3 GB: 6.6 h continuous (10-18) and
  hours more, survived.**
- Before the swap, at the 1500 lock (~130–148 W), dozens of 25–130 min episodes at full VRAM survived.
So NOT: VRAM fill (full-VRAM episodes survive for hours), the core clock by itself (1995 MHz survives at 115 W),
heat (r1's hotspot reached 92.9 °C and lived), the PSU unit (deaths on the new one). The trigger is **dense LLM
inference at high card power** — the same card at the same power survives a steady GEMM and dies under LLM decode.
Two mechanisms fit and are not yet separated: (a) **load TRANSIENTS** — decode swings the card's draw every token,
which a steady GEMM never does, stressing the card's own power stage or the 12 V feed (cable topology on the new PSU
unrecorded); (b) **interrupt / DPC load** — decode submits work many times per token, all interrupts land on core 0
of a 6-core CPU (§4), and the 0x133 is the cumulative DPC watchdog. One-variable tests that separate them, each a
crash order the factory already recovers from unattended (§9h): the failing LLM load at stock clocks with the power
limit cut (transients shrink, submissions do not); the same load with CUDA graphs off (submissions ×~7.7, §4, power
unchanged).

### 9j. ⭐ 2026-10-21 — SEPARATED: it is the card's POWER under LLM decode; production now holds 180 W

**Instrument** (`bh_crash_ttf.py --load=llm`, built 10-21): the exact failing load — ollama's OWN bundled
`llama-server.exe` on qwen3.5:9b's own blobs (+ mmproj) with the flags ollama used for the 10-21 crash, ctx 19,968,
streamed decode as the heartbeat — plus core 0's DPC / interrupt time / interrupts per second sampled every second
(typeperf). Proven on the card first: 60 s smoke, 6.7 GB on the GPU, the server's pid among the compute apps,
66 tok/s, no events. Stock clocks every arm; one variable each:

| arm | runs | verdict | ttf | tok/s | core 0 DPC / ISR median | interrupts/s |
|---|---|---|---|---|---|---|
| baseline (board default 280 W) | 2 | **died, died** | **99 s, 226 s** | 66.1 / 66.8 | ~1.5 % / ~0–1.5 % | 1,400–2,100 |
| **power limit 180 W** | 2 | **survived, survived** | 913 s each (budget) | **66.2 / 67.4** | 1.54 % / 1.54 % | ~1,500 |
| CUDA graphs off (280 W) | 1 | **died** | **60 s** | 57.5 | **24.9 % / 10.9 %** | **~20,000** |

**Reading.** Baseline and 180 W carried the SAME interrupt load, and only the power separated death from survival, so
**the card's power draw is the cause and interrupt load is not** (at ~1.5 % DPC the cumulative watchdog is nowhere
near; the 0x133 is how the driver dies once the card stops answering). Graphs-off died fastest (n = 1): consistent
with a spikier draw (more launch gaps = larger current swings), not separated from the 13× interrupt rise. **The cap
costs nothing on decode** (66–67 tok/s either way: decode is memory-bound; the extra 60–100 W at the board's factory
280 W limit bought crashes, not tokens). Together with §9i: not the PSU (new unit + TWO separate PCIe cables), not
VRAM fill, not heat, not the core clock by itself (1995 MHz ran 6.6 h at ~115 W). **The weak link is power delivery
at high, bursty draw — most likely the card's own (a factory-OC board whose default limit is 280 W against the
reference 3070's 220 W).** Card vs board would need a card swap; stable operation does not.
