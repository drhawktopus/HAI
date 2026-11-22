# Qwen3.8-Flash-Next on Mothership — files, measurements, and our own quant (the "cook")

**Plain-language key (the owner 2026-10-01 19:35: "many of them have names that are just letters and numbers"):**
- **Model files ("cooks", our own quantisations of the same model):** **q2k** = the main cook, holds the speed record
  (30.2 tok/s on a repeated prompt) but is bigger than 32 GB RAM, so new prompts page from disk (varied ~11–19).
  **q2kd2** = q2k with the CPU-side experts' "down" part squeezed to Q2_0 so the whole set fits in RAM: no paging,
  steady ~13–17 on any prompt, fastest prefill (~175), but no CPU fast-layout (repack), so it cannot reach 30.
  **iq2xxs** = Unsloth's smaller ready-made quant: lost (cheaper to store, slower to compute).
- **Engines:** **Unsloth build** `llama-unsloth-b11160-vulkan` = stock reference, holds the record.
  **`llama-mix-<sha>`** = our builds (Unsloth's engine + expert cache + our patches; the sha is just the source
  fingerprint): `512d05731` = the timing/profiling build; the deferral build is `flashnext-build-mixdefer`.
  **ollama ROCm bundle** = a different engine, only a long-prompt route.
- **Settings:** **N=32** = 32 of 48 layers keep their experts on the CPU; **MTP** = draft head guessing 2 tokens
  ahead (+~45%); **mmap / none** = experts read from the file on demand / loaded into memory.

**Status 2026-10-01 19:05 — READ THESE SECTIONS FIRST, IN THIS ORDER, before proposing any lever:**
1. **"The lab's own speed tricks, mapped onto Flash-Next"** — the owner's bag of tricks (transform once · cache
   in-between results · keep the hot thing on the GPU · pre-bake · load once run many) and their counterparts here.
   Three sessions missed it by collapsing it to "a quant that fits RAM".
2. **"The arsenal, combined"** — every weapon from the tricks + every research source, by the part of the token it
   attacks, with conflicts; then **"The arsenal is a SYSTEM — the WHOLE box"** (every resource, idle ones too —
   NOT two memory budgets; the owner corrected that framing) and the **build order**.
3. **"Known-bad gate: `plan_lint.py`"** — every ladder plan passes it before placement.
4. **Full telemetry** (in the tricks section): every arm prints `TREE` lines for GPU / CPU / RAM / paging / every
   drive / engine caches. the owner: *"Guesses are bad, and informed educated theories are good."*
5. **Yardsticks come from the data, never from memory:** `python workspace\flashnext\yardsticks.py` reads every
   delivered `ladder.json` / `strata.json` and prints the best warm-repeated rate and the best varied median per
   engine, each with its source order (Flash-Next arms only — this workspace also holds 27B comparison arms). Quote a
   comparison against its output, not against a number recalled from this page (2026-10-04: a stale "12-14 varied"
   from a pre-RAM-tune row was quoted against Strata; the current figure is the 17.7 median below).

**Strata (2026-10-04, PRELIMINARY — one run per arm, same day, same box, the 10 prompts of `validate-mmap` MTP-t0-mmap-10p):**
Strata v0.1.39 Windows HIP, its own GSQ-RCO **Q2_0**, low-RAM mode (`flashnext-strata-plateau.20261004-1547`) vs the
best varied llama.cpp cook unchanged (`flashnext-strata-plateau-control.20261004-1538`, Unsloth b11160 Vulkan, q2k,
mmap, MTP 2, N=32). Varied gen: **Strata 7.3, 11.1, 12.8, 27.3, 34.7, 33.5, 45.8, 39.8, 54.1, 52.8** (median of the
last 5 = 45.8; full-length answers among them 45.8 / 54.1 / 52.8; answers 2, 6, 8 ended early at 370 / 392 / 212
tokens — Strata cannot force a length) vs **llama.cpp 12.1 … 19.8** (median of the last 5 = 19.8) → **~2.3–2.7× after
warm-up**; slower than llama.cpp for the first 3 prompts while its expert cache fills; load 285 s. Prefill **449–681 vs
84–135** (~5×). Repeated prompt: a tie (30.5 vs 30.2). Still paging (~21.6k pages-in/s mean, low-RAM mode reads experts
from F:). **Not measured: answer quality** (a different quant than q2k) — needs its own order before any menu use.
Untried lever: Strata's setup reports no hipBLASLt tuning table for gfx1100 + hipBLASLt 100500 (prefill on plain
hipBLAS; `tools\hip\tune_hipblaslt`). Payload `workspace\flashnext\strata_arm.py`, plans `strata-plateau*.20261004.json`.

**AFTER LAYOUT C's RAM (2026-10-17, Mothership 32 → 64 GB, 2 × 32 @ 3600; the 10-04 plans re-run unchanged, the box is
the one variable — `strata-plateau-ram64.20261017.json` + `-control-ram64`, orders `…-ram64.20261017-2042`):**
Strata's own rule (`setup.py low_ram_needed`: RAM < arena 34 GB + 10) now picks its **standard mode** under the same
`--low-ram auto` (setup log: "KV streaming on… more experts fit in VRAM"). **Strata varied: 73.6, 64.5, 82.2, 79.8, 63.3,
67.9, 68.8, 74.6 (short, 258 tokens), 63.8, 61.2 → median 68.4, last-5 median 67.9** (was 45.8 on 32 GB low-RAM: +48 %;
no warm-up ramp this time — the first answer is already 73.6); prefill **838–843** on 4,125 tokens (was 449–681);
load **36 s** (was 285 s); pages-in ~3/s (was ~21.6k/s). **llama.cpp control (q2k, N=32, MTP 2, mmap): 14.8 → 27.1,
last-5 median 26.9** (was 19.8: +36 % from the RAM alone); prefill 143–147. Strata leads 2.5× writing / 5.8× reading.
Against the viral 3060 rig (24.4) ~2.8×; against the 3.8-27B champion's fast recipe (73–82 on its own prompts — a
different prompt set, so context, not a comparison) it is now within reach. ⚠ The ladder's `spill_gb 34.03` on the
Strata arm is Strata's host-resident experts read through the Windows "shared GPU memory" counter (pinned host memory),
not a spill — pages-in ~3/s and 68 tok/s say so (the same instrument confound as BigHonker's pinned `CUDA_Host`,
[[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] § B10).

**REMATCH ON REAL WORK (2026-10-17 21:19–21:41, order `flashnext-strata-answers-ram64.20261017-2119`, `strata_arm.py`
answer mode, the 10-02 set `quality\prompts-v1.json`: 8 prompts, thinking on, t 0.6 / top_p 0.95 / top_k 20 / seed 1234,
Strata `--context 131072`, no max_tokens):** all 8 `stop`, 0 void, **88,441 tokens in 21.5 min = 68.6 tok/s overall**
(per answer 64.7–83.6) — against the champion's fast recipe on the same set **93,753 tokens in 21 min (73.5)** and
Flash-Next q2k's own 10-02 run **156,299 tokens in 4.1 h (10.6)**: Flash-Next now runs at the champion's pace, and
thinks less than its 10-02 self (code-1 29.6k vs 69k tokens). **Blind head-to-head vs the champion's 10-02 answers**
(`quality\headtohead_v2_strata.py`, seed 20261017, graded by Claude before unblinding, `headtohead-v2-grades.json` +
`-key.json`): **Strata 3 · champion 2 · tie 3** — Strata clear on ops-2 (the champion's macvlan fix routes via a
host-side veth that does not exist), slight on reason-2 and write-1; the champion slight on code-1 (its tests hit
update-in-place via put) and ops-1. Answers v1 (10-02) was 4–1 for Flash-Next q2k. **Second reader (10-18, external):** Codex
CLI graded the same file blind — Strata 2 · champion 2 · tie 4; agreed with mine on 5 of 8, mine one notch toward
Flash-Next on the other 3. Quality parity; crown shared by job family (register, Phase 2 block).
**LEAD-ROLE CODING TEST (A1) ON STRATA (2026-10-17 22:48 → 10-18 02:38, `a1v22-py-agentic-hai-flashnext-strata-lead.20261017-2248`,
the champion's crowning protocol with the 10-15 compaction harness; A1 reaches Strata through `lab_provider`'s openai
backend + `a1_order` `engine: strata`):** **12/15**, 0 void / capped / compactions, 227 min. On the same harness
(`72403f73`) and the same 10 exercises as the 3.8's own xhigh run (`…q38-best-thon-spot-exhigh.20261017-0047`):
Flash-Next **9/10 in 96 min, 2,931 kJ whole box** vs the 3.8 **8/10 in 226 min, 5,236 kJ card alone**. The 3.8's
own runs of that set vary 6–8/10, so quality reads "at least as good"; time and energy are not close. **Whole box vs
card:** Mothership averaged 511 W at the wall during the run = 1.56× the card meter (Flash-Next holds the CPU busy) —
from the UPS Power Logger (RUNBOOK-Foundry-and-Rescue-Daemons § UPS Power Logger). **All 15 on one harness** (the
3.8's hard 7 re-run 10-18 02:41): Flash-Next **12/15** vs the 3.8 **9/15**; Flash-Next solved every exercise the 3.8
did plus forth, pov and react; ≈0.58 vs ≈0.94 MJ whole-box per solve. The 3.8 draws 519 W at the wall (1.34× its card)
— the two boxes draw the same; the energy gap is time. Detail: register, Phase 2 block.
**CONTEXT 262,144 — its TRAINED maximum, adopted 2026-10-18** (the owner: "would it be worth pursuing bringing flash next
to maximum context? Is it a 1M max?"). The GGUF's `qwen4exp.context_length` is **262,144**; it carries no rope-scaling
keys, so 1M is not in this file. 131,072 had been Strata's `--low-ram auto` pick, carried into the A1 test - never a
measured limit. Long context is cheap on this model: full attention on only 12 of 48 layers (`full_attention_interval`
4), 2 KV heads × 256, int8 → ~3 GB of KV at 262K, and Strata keeps only the last 32K resident (`--kv-resident 32768`).
Order `flashnext-strata-plateau-ctx262k.20261018-0758` (the 131K plateau plan, one variable): write median 66.9 vs
68.8, 4K read ~820 vs ~840, +1.8 GB RAM, and a **deep read of 191,857 tokens at 808 tok/s with the mid-depth needle
found** (the "28.6 tok/s at depth" was a ~7-token answer — not a rate; the v0.1.45 measurement below is the real one).
Its plan `plans\strata-serve-q2_0-ctx262k.20261018.json` is superseded by the v0.1.45 serving plan below. How a menu
cell runs on Strata: [[RUNBOOK-Compute-and-Training-Order-Submission]] § "RUN A MODEL THAT IS SERVED BY STRATA". Speed
levers for Strata are in `workspace\flashnext\arsenal.json` (`strata_*`). Journal: 2026-10-18-claude.
**⚠ SINCE 2026-11-05 19:02 THE SERVING PLAN IS `plans\strata-serve-q2_0-v045-vision.20261105.json`: the plan below + Strata's `vision` config section, so Flash-Next READS PICTURES** (OpenAI `image_url` parts; HAI sends them when the box declares `sees.<model>`). Strata 0.1.45's setup refuses images on Windows with an AMD card, so the CPU encoder (`strata-vision.exe`, llama.cpp's mtmd at Strata's pinned commit) is built on the box by `workspace\flashnext\build_strata_vision.py` (`submit_strata.py --build-vision`; it installs beside the engine, copies `mmproj-F16.gguf` into the model folder and proves the encoder), and `strata_arm.apply_serving` writes the section into Strata's run config at every start. After a Strata upgrade or a moved model folder: run the build order again. Measured: encoder READY 1.4 s, ~1 s a picture on 8 threads; a picture question 2.5 s; text 83–88 tok/s vs 75–81 without it the same hour; VRAM unchanged. Rollback: point `llama-swap.yaml` and `strata_served.json` back at the plan below.

**Numbers now (19:05, RAM at 3800 / FCLK 1900 1:1 since 18:33):** warm repeated prompt **30.1–30.2 tok/s**
(Unsloth b11160 Vulkan, q2k, N=32, MTP draft 2, `-np 1`, `-ub 512`; was 28.1–28.4 at 4200 2:1); varied prompts on
q2k mmap **11.7–20.8, median 17.7** (fresh boot — part of that gain is memory state, see "RAM at 1:1").
**REAL WORK (2026-10-02, `flashnext-fn-answers`, the 8 answer-test prompts, thinking on, temp 0.6, 131k ctx, N=34,
same engine + MTP 2): 8.8–13.1 tok/s per answer, 10.6 overall, 156,299 tokens in 4.1 h** — against the 27B's fast
recipe on the same prompts at **73.5 tok/s, 93,753 tokens in 21 min**. Flash-Next also thought longer (code-1: 69k vs
25k tokens). The 30 tok/s headline is a warm repeated short prompt; it is not the rate for real work. Prefill
best **174** (ollama ROCm bundle) / **159** (q2kd2). **Our own engine** `llama-mix-*` (b11160 + MTP #144 + expert
cache #27861 + our patches): the expert cache works with full repack (repack-path hooks), Q2_K repack is a split
verdict (warm level, varied loses — repack × mmap). **Measured split of a warm token:** CPU expert compute ~45%,
CPU waiting on GPU + copies ~27%, GPU kernels ~37%, CPU and GPU strictly take turns (§"MEASURED: where the winning
config's time goes"). **Next, awaiting the owner:** Expert Deferral Stage A (overlap CPU and GPU — research row
`expert_deferral`); the varied-arm uptime re-run to separate RAM from fresh-boot memory state.
**Pre-preliminary** (the owner): no menu cells; Mothership held from the menu filler (`menu_fill_hold.json`).
Backlog origin: PROJECT-BACKLOG "SIDE QUEST: Qwen3.8-Flash-Next on Mothership". Narrative:
2026-09-30-claude (the 2026-10-01 sections).

## Synthesis 2026-10-01 21:20 — the whole box, and the asymmetric cook (the owner: "synthesize from ALL this data")
**One warm decode pass of the 30 tok/s winner, by resource** (per graph, `GGML_SCHED_PROFILE`, defer-diag control):
CPU expert compute ~9 ms (DDR4-bound; 1:1 RAM bought +6%) · CPU waiting on the GPU ~6 ms (per-step GPU latency:
~7,200 nodes at ~5 µs, 66 CPU⇄GPU splits per main graph) · outside the scheduler ~4 ms (UNMEASURED: sampling, MTP
draft logic, host-side PLE hashing — main-graph reuse is fine, ~186/190) · issuing Vulkan work ~2.3 ms. Capacity:
**VRAM full (22.4 GB) and RAM full (31.7 GB CPU-side set)** — both binding; **GPU bandwidth idle** (mem_load 4.5%,
matvecs 20–28% of 960 GB/s); GPU compute idle (waiting, not working).
**The insight:** both binding capacities are filled with expert BYTES, but a byte on the GPU costs almost no TIME
(the card is latency-bound, its bandwidth idle) while a byte on the CPU costs time (DDR4), RAM, and two hand-offs per
layer. The cooks so far gave both sides the same format; the iq2xxs verdict (slower) was the CPU side's IQ2 decode
cost — the GPU side was never priced alone. ⇒ **Asymmetric cook:** GPU-resident layers squeezed hard (gate/up
IQ2_XXS + down Q2_0 — Vulkan HAS Q2_0 mul_mat_vec / mul_mat_vec_id / matmul_id; build 5 kept IQ4_NL on the GPU only
as a precaution), CPU-resident layers keep the repackable q2k recipe. Sizes (from q2k's 31.7 GB / 32 layers): 0.99
GB/layer → ~0.65 GB/layer on the GPU side ⇒ 24 GPU layers in today's ~15.8 GB ⇒ **N=24**: CPU-side set ~23.8 GB
**fits RAM** (varied prompts stop paging) and 8 fewer CPU layers = 16 fewer hand-offs per main graph. Estimate (soft:
the 1 ms/layer N-sweep slope was pre-MTP): warm ~33–38 tok/s, varied the bigger win. Order
`flashnext-quant-asym24.20261001` (layers 24–47 asymmetric; output `D:\Models\Qwen3.8-Flash-Next-asym24.gguf`).
**Quality bar (the owner 21:20): "noticeably better than normal qwen 3.8 27B".** The lab's Hard Bench cannot show it (the
27B scores 0.97–1.00 there — ceiling), so the instrument is **bits per byte** (`workspace\flashnext\ppl.py`) on a fixed
corpus of the lab's own prose + code (`quality\corpus-v1.txt`, sha256 `f6a3e98a…`, never in any training set):
order `flashnext-quality-v1` scores qwen3.8:27b, q2k, iq2xxs (2-bit gate/up on every layer = the cook's worst case)
and q2kd2 (Q2_0 down on 32 layers). Closed tonight (do not re-run): threads 12/16, whole-layer split, concurrency
without cache, clock floor, HAGS, AMD profile, submit cap, cpu_affinity, draft 3, Q8 head, fault-ahead as built,
hotness placement as built, HIP graph capture, expert deferral (parity). Later multipliers: measure the 4 ms
"outside" slice; fused DeltaNet ops upstream (fewer nodes); headless-Linux frontier (parked by the owner).
**⚠ QUALITY RESULT, 21:38 — the cook is at or below the bar (order `flashnext-quality-v1`):** qwen3.8:27b (Q4_K_M)
**BPB 0.6455** (band 0.639–0.652) vs Flash-Next **q2k 0.6792** (0.672–0.686) — same tokenizer (44,727 tokens both),
bands do not overlap: today's 2-bit cook predicts the lab's text ~5% WORSE than the 27B. **Online data agrees:**
full-precision Flash-Next beats the 27B by only ~1–2.5 points (LiveCodeBench v6 91.9 vs 90.3, GPQA-D 91.7 vs 89.2;
wins 18/18 but narrowly); Unsloth's KLD table for Flash-Next: UD-Q2_K_XL (78.9 GB, our q2k's class) **82.7% top-1,
KLD 0.225**; UD-IQ3_XXS 85.4% / 0.165; UD-Q3_K_XL (90 GB) 88.3% / 0.107; **UD-Q4_K_XL (111 GB) 92.3% / 0.047**; Q8_0
94.1% / 0.027; a hands-on report has the 27B (FP16) finishing a two-day agentic coding session Flash-Next at INT4 could
not, Flash-Next better for creative chat. ⇒ **The asymmetric cook was CANCELLED (21:50, the owner: "answer test next")**:
squeezing harder buys speed against the quality bar. What clears the bar is MORE bits (Q4-class experts, hot set
~70–80 GB excluding the n-gram table) = more fast memory — Mothership has two free DIMM slots (A1/B1); 4 sticks may
need 3600/1800 instead of 3800/1900. The answer test (`flashnext-answers-v1`, 27B vs q2k, blind pairwise) is the judge.
**Full ladder (22:01):** 27B **0.6455** < q2k **0.6792** (+5.2%) < iq2xxs **0.6906** (+7.0%; IQ2_XXS gate/up on all
48 layers) < q2kd2 **0.7075** (+9.6%; Q2_0 down on 32 layers) — **the `down` experts are the sensitive tensors**: Q2_0
there on 32 layers costs more than IQ2_XXS gate/up on all 48. Any future cook spends bits on `down` first.
**Unsloth UD-IQ3_XXS (order `flashnext-quality-ud3`, 2026-10-02 12:54):** BPB **0.6772** (0.6703–0.6840) vs a same-day
q2k control **0.6792** (0.6723–0.6859) — the control reproduced the 10-01 value to four decimals, so the instrument is
stable, and the 0.002 gap is inside both bands: **UD-IQ3_XXS is not measurably better than q2k on BPB** (and is the
larger file). No speed run spent on it. BPB undersold q2k on the answer test below, so this is "not better on the lab's
text", not "not better at work".
**⚖ ANSWER TEST RESULT (2026-10-02 ~12:20, PRELIMINARY — one sampled run per prompt, one grader):** q2k (131k, N=34,
MTP 2) vs the 27B's fast recipe (131k, MTP draft 3), the 8 prompts of `quality\prompts-v1.json`, graded blind pairwise
against the grader keys (pairs shuffled by `headtohead_blind_pairs.py`, seed 20261002; grades written BEFORE unblinding —
`quality\headtohead-v1-{blind-pairs.md,grades.json,key.json}`). **Flash-Next 4 · 27B 1 · tie 3.** Flash-Next won code-1
(clear: strictly O(1) + far fuller tests; the 27B's heap made `put` O(log n)), ops-1 (clear: names SQLite WAL -shm +
byte-range locks, confirm = sqlite3 on the mount), **ops-2 (decisive: the 27B's macvlan fix is factually wrong** — routes
to a host-side veth "with the container MAC" that macvlan never creates) and explain-1 (slight); the 27B won write-1
(slight; Flash-Next invented incident details); code-2 / reason-1 / reason-2 tied. **So the BPB gap did NOT predict the
practical answers — on hard practical prompts the 2-bit cook beat the 27B.** Cost: **7× lower density** (10.6 vs 73.5
tok/s) and more thinking (156k vs 94k tokens): **4.1 h vs 21 min** for the same 8. Reading: interactive work → 27B;
Flash-Next is a candidate only for overnight jobs where correctness outweighs time. Not yet a finding: n = 1 per prompt
at temp 0.6 (the 27B's ops-2 miss may be sampling luck), and the owner's eye is pending on ops-2 and code-1.
⚠ Own-goal 22:13: `answers-v1` ran with `-c 32768`, an invented limit — the 27B's first answer passed 23.5k tokens of
thinking; the lab's measured value is 65,536 (`34-Hard-Bench-Order-Spec`). Stopped and re-placed as
`flashnext-answers-v1b` (65,536; Flash-Next at N=33 for the KV room).
⚠ Own-goal 22:18: v1b launched the 27B through llama-server with hand-written flags and NO MTP (log: `blk.64.nextn.*
… unused -- ignoring`) while the lab serves it through ollama WITH MTP draft 4 (~54 tok/s, `34-Hard-Bench-Order-Spec`).
Re-placed as `flashnext-answers-v1c` (`--spec-type draft-mtp --spec-draft-n-max 4`, plain fallback arm). **Rule: when
a test launches a model outside its normal serving path, copy that path's real launch flags (ollama's are in
`serve.log`), never write them fresh** — the owner: "I thought you were using the standard fast recipe we found?"
**The 27B's fast recipe (2026-10-01/02, the owner 22:40: "focus on getting 27B speed up"; he optimises tok/s = density,
not answer time).** qwen3.8:27b Q4_K_M (ollama blob `sha256-f5f1dd89…`, 15.7 GB) is dense, fully on the GPU and
COMPUTE-bound (97% compute, ~2.7 GHz, 300–390 W) — the opposite of Flash-Next. Orders `flashnext-q27b-speed3`,
`-draftmap` (5 varied prompts, temp 0, median tok/s): Vulkan MTP draft 2 **82.5** · **draft 3 87.5** · draft 4 73.9 ·
draft 5 **23.2** · draft 6 15.0 · draft 8 21.6 (a cliff from draft 5 — verifying 6+ tokens per pass) · ollama's own
ROCm engine at draft 4 **50.1** (prefill faster: ~900 vs ~700) · f16 KV = q8 KV. On REAL thinking work (`-thinkdraft`,
temp 0.6, 4 prompts): draft 3 **81.8**, draft 2 72.7, draft 4 61.5 — the peak holds. ⇒ **Recipe: Unsloth Vulkan
`llama-server` + `--spec-type draft-mtp --spec-draft-n-max 3 -ngld 99` (in-model MTP head, no `-md`) + `-lm none
--no-host -fa on -ctk q8_0 -ctv q8_0 -np 1`: ~82 tok/s on thinking work vs ~50 for ollama's default (ROCm, draft 4)
= ~1.6×, same weights.** `--no-host` is required (without it pinned host buffers read as GPU spill). Largest clean
context (`-maxctx`, load-time spill check): 262,144 spilled 3.67 GB, 196,608 spilled 1.21 GB, **131,072 clean (20.94
GB on the card, 0.34 GB shared)**. Sampled runs (temp 0.6) do NOT give identical answers across draft depths — compare
tok/s, not token counts. Online: Q4_K_M ≈ BF16 on GPQA-D / IFBench / Terminal-Bench (Quesma) — the quant is not the
limit. The lab's serving still uses ollama's default — switching is the owner's call.
**The gap is the BACKEND, not ollama (order `flashnext-q27b-ollamavk`, 2026-10-02 12:07, PRELIMINARY one round):** ollama
0.34.0's OWN bundled engine with only its Vulkan backend loaded (`GGML_BACKEND_PATH=…\lib\ollama\vulkan\ggml-vulkan.dll`),
same 5 prompts, temp 0: MTP draft 2 **83.5** (73–86, acceptance 0.76) · draft 3 **80.8** (66–93, 0.64) · Unsloth Vulkan
draft-3 control **87.4** (70–93, 0.66); all 17.1–17.2 GB on the card, ≤0.12 GB spill. Ranges overlap, so draft 2 vs 3 and
ollama vs Unsloth are within noise; **ollama-engine Vulkan (~81–84) vs ollama-engine ROCm (~50) is the real gap.** Device
inferred (logs not verbose: only the Vulkan DLL loaded + full residency + a speed ROCm never reached), not read from a
device line. ⇒ The cheap serving path is ollama on its Vulkan backend; still to check that `ollama serve` exposes the
backend choice and the MTP draft depth the way this direct `llama-server` run did.
**Checked (order `flashnext-q27b-ollama-serve`, 2026-10-02 13:18; payload `workspace\flashnext\ollama_arm.py`, a private
`ollama serve` on :11435 with the LIVE launcher settings, num_ctx 122,880, same 5 prompts):** live autodetect = **ROCm0
53.8** tok/s (66/66 layers, 16.61/16.61 GB); `OLLAMA_LLM_LIBRARY=vulkan` = **Vulkan0 59.5 / 64.8 / 65.4** (device read from
the debug log). So `ollama serve` on Vulkan is only **~+20%**, not the +60% of the direct runs, for two logged reasons:
(1) **ollama hard-codes `--spec-draft-n-max 4`** on its runner and `LLAMA_ARG_SPEC_DRAFT_N_MAX` is ignored ("environment
variable is set, but will be overwritten by command line argument") — draft 4 is the ladder's worst of 2/3/4; the three
"draft" arms were therefore one config run three times (a repeatability check: 59.5–65.4); (2) **on Vulkan ollama's
auto-fit (`-fit on`) keeps a 1,936 MiB free target and came 38 MiB short, leaving ~1.1 GB of the model off the card**
(`/api/ps` 16.36 of 17.44 GB). Next: one arm with a smaller `LLAMA_ARG_FIT_TARGET` (fit is env-honoured); draft 3 needs
either an ollama setting not yet found or serving through `llama-server` directly.
**→ RESIDENCY WAS THE GAP (order `flashnext-q27b-ollama-fit`, 13:21, one round):** `OLLAMA_LLM_LIBRARY=vulkan` +
`LLAMA_ARG_FIT_TARGET=512` → **Vulkan0, 16.61/16.61 GB on the card, median 80.9 tok/s** (81.9, 59.1, 84.6, 80.9, 51.1 —
the same per-prompt shape as every earlier run) vs live ROCm **53.8** = **~+50%, with ollama's own draft 4**, level with the
direct-`llama-server` runs. ⇒ The 27B serving switch is two env vars in `C:\ProgramData\HAI\start-ollama.ps1`
(+ restart), same ollama / model / HAI routing. Before adopting: repeat once; Vulkan prefill ~700 vs ROCm ~900 tok/s;
a 512 MiB free target leaves little slack if anything else loads on the card (test under the real co-load); it changes
the backend [[34-A-OLLAMA-UPGRADE-GATE]] signed off (ROCm serving) — the owner's decision, recorded there.
**ADOPTED LIVE 2026-10-02 ~14:00** (the owner: *"switch it all over to fast"*): live `ollama serve` verified `Vulkan0`, 66/66
layers, 16.61/16.61 GB, **82.4 tok/s** median (82.4, 84.3, 48.8). **Why one prompt is always slow — MTP acceptance is
task-shaped** (per-prompt `draft acceptance` in the `-ollama-fit` log): JSON 0.76 → 84.6 tok/s · code 0.72 → 81.9 · math
0.71 → 80.9 · plain-language explanation 0.45 → 59.1 · **creative story 0.35 → 51.1** (2.4 tokens per verify pass vs
3.9 for code). Predictable text (code, JSON, worked math) accepts most drafts; open prose accepts fewer, so it runs
nearer the no-speculation rate. Not a regression and not fixable by draft depth — expect ~80+ on structured work and
~50–60 on long creative writing.
**n-gram speculation for the 27B: tested 2026-10-08, NO GAIN** (lever `ngram_mod`; orders `flashnext-q27b-ngram` and
`-ngram-stack.20261008`, `answers.py`, Unsloth Vulkan `llama-server`, temp 0.6, one arm each, comparison = the data
above): n-gram ALONE 36.4 tok/s on `code-2` (15% of guesses accepted) and 52.7 on a whole-file edit (63%); STACKED on
MTP draft 3 (`--spec-type draft-mtp,ngram-mod` — the build accepts both at once, contrary to the arsenal's old
"one --spec-type at a time" note) 75.5 on `code-2` (vs 83.4 for MTP alone on the same prompt) and 84.6 on the edit (vs
~82–85 for MTP on structured work). The extra guesses get rejected and cost verify passes. **Serving unchanged; the
27B's speed is the card's.** Also: the depth question was already closed above (ollama's draft 4 ≈ draft 2/3 on its
own Vulkan engine); the thinkdraft 81.8-vs-61.5 pair is Unsloth's engine and does not transfer.

## The model
~180B total: a 125B MoE (48 layers, 512 routed experts + 1 shared per layer, **10 active per token, ~6B active
params**) + a **51B n-gram lookup table** (`per_layer_token_embd`, read ~2.7 KB per token — cheap to leave on disk)
+ a 4B MTP draft head + a Qwen3-VL vision tower. llama.cpp architecture name `qwen4exp` (merged 2026-08-27).
Why the owner cares: *"similar to what the lab does but inside of the model itself"* — cheap memory for facts, expensive
compute only where it is needed.

## What is where
| What | Where | Size |
|---|---|---|
| Qwen full weights bf16 (rev `de4b8e4`) + official FP8 (rev `236dfdf`) — permanent originals | `Z:\Archives\Local-Models\Qwen3.8-Flash-Next`, `…-FP8` (batch `downloads-2026-09-29-flash-next`, 288 files verified) | 360 + 186 GB |
| Unsloth UD-IQ3_XXS (3 parts) + mmproj + **their imatrix** `imatrix\imatrix_unsloth.gguf` (580,038,720 B) | `Z:\Archives\Local-Models\Qwen3.8-Flash-Next-GGUF` | 81 GB |
| llama.cpp source at **`0f3a71be1`** (= the commit ollama 0.34.0's bundled engine is built from) | `Z:\Archives\Local-Models\llama.cpp-src\` → unpacked `D:\Models\llama.cpp\src-0f3a71be1` | 39 MB |
| Working copy of the bf16 weights (SHA-256 verified vs the manifest: 132 sha + 12 size, 0 bad) | `D:\Models\Qwen3.8-Flash-Next-bf16` | 360 GB |
| Full-precision **GGUF master** + imatrix — the permanent copy | `B:\Models\Qwen3.8-Flash-Next-work\` | 354.6 GB |
| **Working copy of the master for cooks** (robocopy from B: 2026-09-30, size + 16/16 sampled chunks verified) | `F:\Models\Qwen3.8-Flash-Next-work\` | 354.6 GB |
| **iq2xxs cook (ruled out, on the owner's delete list 2026-10-02) — the only copy now** (its ollama tag and F: copy were removed 2026-10-04 to make room for Strata on F:, the owner: *"trade it for another flash next cook that has been ruled out"*; the F: folder kept as `F:\Ollama-FlashNext-iq2xxs.retired-*` with its manifest) | `D:\Models\Qwen3.8-Flash-Next-iq2xxs\` | 76.6 GB |
| **Build 5 — "q2kd2"** (cooked 2026-10-01 10:25–11:16, 51 min, order `flashnext-quant-q2kd2b`, Unsloth's quantizer): the q2k recipe with `ffn_down_exps` on layers 0–31 at **Q2_0** (2.25 bpw, block 64) instead of IQ4_NL, layers 32–47 unchanged — so the CPU-side expert set at N=32 is ~24 GB (RAM-resident on 32 GB) with no new GPU kernels. Not installed in ollama (llama-server reads it by path). Quality unverified | `D:\Models\Qwen3.8-Flash-Next-q2kd2\` | 69.6 GB |
| **Unsloth llama.cpp fork b11160** (Windows ROCm gfx110X, commit `a3c12db9d`) — the engine with MTP | `C:\ProgramData\HAI\tools\llama-unsloth-b11160\` | 169.7 MB zip |
| **MTP draft head** `mtp-Qwen3.8-Flash-Next-shared-Q4_K_M.gguf` (sha256 `f521868a…`) | `F:\Models\Qwen3.8-Flash-Next-work\MTP\` | 1.9 GB |
| **Unsloth llama.cpp fork b11160 Vulkan** (Windows, `a6922cc`) — the fast engine (MTP, AVX2 + repack) | `C:\ProgramData\HAI\tools\llama-unsloth-b11160-vulkan\` | 26.4 MB zip |
| MTP head `mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf` (sha256 `5ff54097…`) — measured no gain over Q4_K_M | `F:\Models\Qwen3.8-Flash-Next-work\MTP\` | 2.8 GB |
| **Build toolchain** (installed 2026-10-01 01:06 for the expert-cache build): LunarG Vulkan SDK 1.4.363.0, CMake + Ninja (pip, user-level in the plain Python), VS 2022 Build Tools (pre-existing); llama.cpp clone with PR #27861 fetched | `C:\VulkanSDK\1.4.363.0\`, `D:\Models\llama.cpp\src-moe-cache\` | — |
| **Our own build: llama.cpp PR #27861 head `bccbacdb8` + Vulkan** (built 01:30 in 2.5 min by order `flashnext-build-moecache4`, `build_moe_cache.py`): `--moe-expert-cache N` / `--moe-expert-cache-inserts N`, CPU backend AVX2+FMA+REPACK+OpenMP, no MTP (upstream branch). MSVC shim `/Dflockfile=_lock_file /Dfunlockfile=_unlock_file` | `C:\ProgramData\HAI\tools\llama-moecache-bccbacdb8\` | — |
| **The "mix" build `c7c6b40da`** (09:28, order `flashnext-build-mix2`, `build_mix.py`): upstream tag b11160 + Unsloth's MTP PR #144 (`ca14269`, from their own b11160-mix release notes) + the cache PR #27861, Vulkan, MSVC shim, `src/CMakeLists.txt` merged with git's union driver (both PRs add sources there — the only conflict). Has `--spec-type draft-mtp` AND `--moe-expert-cache`. Run with `--no-repack` | `C:\ProgramData\HAI\tools\llama-mix-c7c6b40da\` | — |
| Ladder / sweep / head-to-head scripts and plan files; delivered results | `F:\foundry\workspace\flashnext\` (mirror `E:\SSDShared\mothership-build\workspace\flashnext\`, results in `delivered\`) | — |

**Retired 2026-09-30 (the owner: "get rid of Ollama version and keep ours and the master"):** the registry build
`qwen3.8-flash-next:125b-a6b-q4_K_M` — shared blobs moved into the q2mix folder and re-linked first, then `ollama rm`,
then `F:\Ollama-FlashNext` removed (119.2 GB; re-downloadable from the registry). ⚠ `SendToRecycleBin` from an ssh
session deletes permanently — a network logon has no Recycle Bin; F: went 23 → 141.5 GB free at once. Its
measurements below stay as the baseline Build 2 is compared against.

**Retired 2026-10-27 (the owner: "Clear … the losing flash next builds. Keep the winning flash next and the master"):** our
cooks q2k and q2mix — folders `F:\Ollama-FlashNext-q2k` / `-q2mix` (~162 GB) and their 9 links in `E:\Ollama\Models`
(no other model used those blobs). Re-cook from the master with the recipes below; record and the old table row:
`Archive\flashnext-doc-superseded-2026-10-27.md`. Their measurements below stay as history.

All work folders were created **uncompressed** (`compact /u`) — D:, F:, B: are NTFS-compressed at the root and that
cost a night: [[RUNBOOK-Ollama-Host-Operations]] §"No space left on device… RESERVED clusters".

## Build 1 — ollama's registry q4_K_M (measured 2026-09-29; retired 2026-09-30)
Installed by pulling the registry layers whole to F: and symlinking blobs + manifest into the store — `ollama create`
crashes on the 28.8 GB n-gram tensor: [[RUNBOOK-Ollama-Host-Operations]] §"Putting a model into ollama WITHOUT
`ollama create`". Layers: model 119.15 GB, vision projector 0.91 GB, license, params (no `draft_num_predict`).
- **Placement:** 23.1 of 58.3 GB on the card at num_ctx 8192 (all 49 layers "offloaded"; the overflowing expert
  tensors run from memory-mapped F:). Only 12 of 48 layers keep a KV cache: ~280 MiB at 8192, so context is cheap.
- **Speed (one run, card otherwise idle):** load 181–253 s; **generation 1.91 tok/s; prompt reading 0.89 tok/s**.
  Mothership has **32 GB RAM** (2×16), so ~80 GB of experts cannot stay resident — every token re-reads experts
  from F:. That, not the model, sets the speed.
- **Structured output (the 3.8-27B H7 test):** `think:false` + JSON schema → `{"category":"technical",
  "urgency":"high"}`, `thinking` empty. Pass.
- **Coding family 1, one round, temp 0, thinking off:** **11/11**, every check passing (43–141 tokens per
  answer, ~95 s per answer, ~5.9 kJ GPU per answer; about three quarters of the time is prompt reading).
  3.8 27B on the same set: 11/11 at 1–9 s and 0.3–1.1 kJ. **Family 1 does not separate them** — family 2 is the
  discriminating set; run it on the cook, not on this build.
- **MTP: absent** — no `nextn` tensors in the GGUF (parser proven on qwen3.8:27b-mtp, which has 4). See below.
Orders (local, Mothership factory desk): `flashnext-look-h7`, `flashnext-look-f1`; results in
`E:\SSDShared\tabfoundry-jobs\campaigns\flashnext-firstlook-20260929\` (outside the hard-bench folders, so the menu
ledger does not count them).

## Why a partially-resident model needed `--offload-ok`
`infer_ollama.py` refuses (rc 2, the factory holds and retries) any model with `size_vram < size` after a clean
reload — right for dense models (silent CPU crawl ≠ the model). A model bigger than the card can never pass, so it
was refused forever. **`--offload-ok`** (2026-09-29): the order declares partial residency is expected; the payload
does its clean load, runs as placed and records `placement {size_vram, size, offload}` in `out.json`. A model with
nothing on the card is still refused. The menu filler adds the flag only for models named in `OFFLOAD_OK`
(`menu_fill.py`, per box — never inferred from size: BigHonker's qwen3.5:35b is "in the store only").

## MTP status (2026-09-30 evening)
Stock llama.cpp and ollama still cannot run it (upstream PR #28243 unmerged), and our cooks carry no MTP block — the
converter drops it for `qwen4exp`. **But it does not need to be in our cook:** Unsloth publishes the head as a
separate draft model, and their fork runs it beside any main GGUF. Both are on disk (table above; engine and head
downloaded with the owner's OK 2026-09-30). Run shape: Unsloth engine, `-lm none` (this fork's spelling of `--no-mmap`),
`-md <head> --spec-type draft-mtp --spec-draft-n-max N -ngld 99`. Sources, verdicts and the corrections made to
the research: [[Qwen3.8-Flash-Next-Speed-Levers-Research]].
- **First attempt VOID** (`flashnext-ladder-mtp.20260930`, q2k, N=30, the bundle's tuning not carried over): the
  no-MTP control M0 spilled **1.3 GB of VRAM into shared system memory** and generated **5.96 tok/s**. Stopped by slip
  at 20:21; frontier restored by hand at 20:22 (a stop slip kills the payload before its `finally`).
- **Second attempt (`mtp2`, 21:05) — the head does not fit at N=32** with the VRAM tuning (`-fa on -ctk q8_0
  -ctv q8_0 -ub 256 -c 8192`): 23.7 GB on the card + **2.0 GB spilled**. Caught by the ladder's new spill guard;
  stopped. A spilled arm is now INVALID and skipped (`8964308`), not measured.
- **Third attempt (`mtp3`, 21:10) — MTP meets the RAM cliff.** Every arm at N=36 (clean VRAM). The first arm, MTP
  draft 3 at 16 threads, generated **0.6 tok/s** while draft acceptance was healthy (**71–73%**, mean accepted draft
  length 3.1 — so the head itself works). Measured mid-run: llama-server 52.8 GB private / **16 GB resident**,
  **6,777 page faults/s** (the clean baseline pages ~130/s), RAM free 3.4 GB, 14 cores busy stalling on page-ins.
  Mechanism: the head's ~2 GB of VRAM pushes expert layers to the CPU side (N=36 ≈ 36 GB of experts vs 28 at the
  baseline), and verifying 3 drafted tokens per step touches ~3× the experts, so the per-step working set no longer
  fits in RAM and every step pages. **On this box MTP's cost is a RAM cliff, not a slower verify.** Stopped at 21:34.
- **Fourth (`mtp4`, 21:35–22:27, last arm stopped by slip) — on the scalar-CPU engine, so RELATIVE only** (every arm shares the same crippled
  CPU backend; none of these is an absolute). N=35, 8 threads unless stated, gen = 3 × 500 tokens (cold, warm, warm):

  | arm | gen tok/s | prefill | VRAM | acceptance |
  |---|---|---|---|---|
  | M2 (draft 2) | 5.6, **10.1**, 9.0 | 23 / 45 | 21.5 GB | 77.9% |
  | M1 (draft 1) | 5.8, 9.8, **10.2** | 25 / 41 | 21.1 | — |
  | M0 (no MTP) | 5.0, 6.6, 8.1 (still climbing) | 19 / 27 | 19.3 | — |
  | M0, 16 threads | warm-up alone ran at **0.23 tok/s** (16 threads on 8 cores + paging) | — | — | — |

  Within this engine MTP is a **1.2–1.5× win**, the opposite of what the confounded comparison said. Whether that
  holds on an AVX2 backend is exactly what `backend-fix`'s VM2-vs-V0 pair measures. 16 threads is dead on this
  workload (and was already known not to help through ollama).

## Synthesis (2026-09-30): video + Cyberdyne + the lab's own tricks → the tuning ladder
- **NVMe is already our memory tier — through the mapped model file, not the pagefile.** ollama mmaps the GGUF on F:;
  what does not fit card + RAM is read from F: on demand and Windows' standby cache is the hot-expert cache (why
  generation climbed 5.5 → 9.8 → 12.9 tok/s run over run). Clean file-backed pages never touch the pagefile.
- **The pagefile route is still worth one arm** (the owner: our big NVMe pagefiles made the Flux 2 loads work): with
  `use_mmap: false` the CPU-side weights live in process memory, which CAN page to the 100–120 GB C: pagefile — and
  llama.cpp's own load log for this model says *"tensor overrides to CPU are used with mmap enabled - consider using
  --load-mode none for better performance"*.
- **Prefill is where a 7900 XTX should beat the 3060 rigs.** Generation is memory-bound (their 64 GB RAM beats our 32);
  batched prefill is compute-bound — each expert's weights cross PCIe once per batch and serve every token in it, so
  bigger batches (`num_batch`; Strata reads 8k-token chunks) amortise the transfer, and the XTX has several times a
  3060's compute. The video's cheap rig: 50 tok/s prefill.
- **Ladder (one variable per arm, same ~6.5k-token prompt, clean reload each):** base (`num_batch` 512 default) →
  `num_batch` 2048 → 4096 → `use_mmap: false` at the best batch → cook #2 (gate/up IQ2_XXS) only if generation is
  still spill-bound. Script: `Claude Home` scratchpad `q2mix_ladder.py`.
- **Results 2026-09-30 ~08:00 (q2mix, 6,596-token cold read, num_ctx 16,384, clean reload per arm):**

  | `num_batch` | prefill | read time | generation after it |
  |---|---|---|---|
  | 512 (default) | 30.4 tok/s | 217 s | 9.4–10.0 tok/s |
  | 2048 | 42.0 tok/s | 157 s | 8.0–8.6 tok/s |
  | 4096 | **56.8 tok/s** | 116 s | 6.7–7.1 tok/s |

  Bigger batches read faster (4096 beats the video's cheap rig at 50) but cost ~30% generation — the larger compute
  buffer pushes experts off the card. So it is per-job: long prompt + short answer → big batch; short prompts or long
  answers → default (below 512 prompt tokens the setting does nothing). Warm cache helps prefill too: re-reading 2,050
  tokens after the first pass ran 83 tok/s. Second-run "prompt tok/s" in the raw output is prompt-cache reuse — ignore.
- **The pagefile route (`use_mmap: false`) wins — the owner's call was right** (same prompt, clean reload per arm):

  | `use_mmap` false + `num_batch` | load | prefill | generation |
  |---|---|---|---|
  | 4096 (run 1 / repeat) | 98 / 65 s | 65.2 / 71.6 tok/s | 11.4 / 11.7–12.3 tok/s |
  | 8192 | 82 s | 46.7 tok/s | 5.2–5.6 tok/s |
  | **512 (default)** | 57 s | **103.9 tok/s** | **13.0 tok/s** |

  vs the mmap baseline: **3.4× prefill, +30% generation**. With mmap the CPU-side weights are file pages faulted in
  from F: on demand (the compute thread stalls per fault), they sit in the low-priority file cache, and llama.cpp
  warns *"tensor overrides to CPU are used with mmap enabled - consider --load-mode none"*. Without mmap the weights
  are read once, sequentially, into process memory; Windows keeps the hot parts in RAM and pages cold parts (mostly
  the n-gram table) to the C: NVMe pagefile (6.9 GB in use, 20.9 GB peak, commit 69 of 132 GB; RAM ~2 GB free
  while loaded). Once weights are in real memory, big batches only cost VRAM — 8192 was worst. Which mechanism
  dominates (fault stalls, cache priority, pinned-memory PCIe transfers, C: vs F:) is not yet measured.
  **Made the default 2026-09-30:** q2mix's params blob carries `use_mmap: false` (new params `sha256:4605fe6a…`);
  proven by a load with NO request options → 80.7 GB in-memory placement (mmap would show ~52).
- **What limits generation now (500-token answers, use_mmap off):** CPU. The runner's 8 default threads are pinned
  (7.4–8.0 cores busy), the GPU sits ~42% busy at ~81 W, pagefile/disk reads only ~35 MB/s (C: 24, F: 12).
  **Threads, alternated to cancel warm-up (the Ryzen 7 5800X, 8C/16T):** 8 → 11.54, 11.97 · 12 → 11.58, 12.17 tok/s —
  no real difference; an earlier "12 beats 8 by 25%" was the first run being cold (9.97). 16 threads (11.41) lost with
  other apps running — re-test inside FRONTIER MODE ([[RUNBOOK-Ollama-Host-Operations]]). **Steady warm generation ≈
  12 tok/s.** More cores do not help, which points at CPU-side memory speed/latency: RAM is 2×16 GB DDR4-4000 CL18
  then running at 4200 MT/s, 2:1 (now 3800 / 1900 1:1 since 2026-10-01 — see "RAM at 1:1" below). Levers left: 64 GB RAM (the
  video's rig), Defender exclusions for the
  Flash-Next folders (the owner adds; `F:\ai-toolkit`/`ComfyModels`/`flux2-train` are already excluded), pausing
  WSearch/SysMain in frontier mode, and a CPU-friendlier cook (Q2_K unpacks cheaper than IQ2 on CPU) vs a smaller one
  (IQ2_XXS) — both from the master.
- Not available through ollama: a GPU hot-expert cache (`--moe-expert-cache`, draft PR #27861) and MTP (unmerged
  upstream; runnable outside ollama via Unsloth's fork — "MTP status"). GGUF already stores each expert's slice contiguously (Cyberdyne's layout gain is mostly
  ours already).

## Build 2 — our cook "q2mix": DONE 2026-09-30 05:28, installed as `qwen3.8-flash-next:q2mix`
Conversion finished 03:06 (rc 0, 354.03 GB, 3 h 40 min); quantize finished 05:28 (rc 0): 337,619 MiB → **75,449 MiB
(3.58 bpw), file 79.1 GB**, "194 of 1224 tensors required fallback quantization" (the 640-wide tensors below).
Installed by store blob + hand-written manifest (`install_q2mix.py`): model blob `sha256:a4dd15e2…` → the GGUF on F:,
config = the registry config with `file_type` IQ2_XS, projector/license/params reused from Build 1 (so the weights are
the ONLY difference). Placement: **23.3 of 52.1 GB on the card** (Build 1: 23.1 of 58.3).

| first look (same settings as Build 1) | Build 1 registry q4_K_M | **Build 2 q2mix** | qwen3.8:27b |
|---|---|---|---|
| load | 181–253 s | **67 s** | ~50 s |
| prompt, cold | 0.89 tok/s | **2.49 tok/s** | ~485 |
| gen, same prompt ×3 (page cache warming) | 1.91 | **5.45 → 9.84 → 12.91 tok/s** | ~39 |
| H7 structured output | pass, 53 s | **pass, 24 s** | pass |
| coding family 1, temp 0, 1 round | 11/11, 95.5 s/answer, ~5.9 kJ | **11/11, 20.7 s/answer, ~1.5 kJ** | 11/11, 1–9 s, 0.3–1.1 kJ |
| coding family 2, temp 0, 1 round, thinking off | — | **25/25, 14.2 s/answer, ~1.1 kJ, 0 cap hits** | 98% (352/360, temp 0.6), median 1.2 s |
Generation climbs run over run because Windows' standby cache fills with the experts in use — one repeated prompt
flatters it; the varied coding sets are the truer number. **Family 2 thinking-off is SATURATED on Mothership** — every
model the menu ran there scores 97–100% (3.6 27B 100%, Coder-30B 100%, 3.8 27B 98%), so 25/25 shows the ~2.4-bit
experts lost nothing visible here, not that Flash-Next is better. The discriminating tests still to run: thinking-on
family 2, tool calls, vision — and speed is where it currently loses (~14–21 s/answer vs 1–2 s for the 27Bs).
Next cook to try: gate/up at IQ2_XXS (experts ~43 GB) to shrink the ~6 GB that still spills past card + RAM.
Orders: `flashnext-q2mix-h7`, `flashnext-q2mix-f1`, `flashnext-q2mix-f2` (same first-look folder).

### How the cook was made
| Tensor | Unsloth UD-IQ3_XXS (read from its headers) | **Ours** | Size |
|---|---|---|---|
| n-gram `per_layer_token_embd` (51B) | IQ4_NL | IQ4_NL (lives on disk) | 28.8 GB |
| `ffn_down_exps` | IQ4_NL | asked IQ2_S → **IQ4_NL (forced)** | ~22.7 GB |
| `ffn_gate_exps`, `ffn_up_exps` | IQ2_S (a few IQ3_S) | **IQ2_XS** | ~22.8 GB |
| everything else (attention, shared expert, heads) | Q6_K / Q8_0 | Q6_K base | ~4 GB |
| **experts / file** | ~48 GB / 81 GB | **~45 GB / ~79 GB** (planned 35 / 68) | |

**Shape constraint (found 2026-09-30 03:30 by the override check):** `ffn_down_exps` is `[640, 2560, 512]` — its rows
are **640 wide**, and every K-quant and IQ type packs 256-element blocks (640 is not a multiple), so `llama-quantize`
silently substitutes a 32-block type (IQ4_NL) whatever `--tensor-type` asks. That is why Unsloth's build also has the
down experts at IQ4_NL. Lowest 32-block options: IQ4_NL / Q4_0 (4.5 bpw), MXFP4 (4.25). So on llama.cpp the experts
cannot go below ~43 GB (down 4.5-bit + gate/up IQ2_XXS) — ~5 GB over Mothership's ~38–40 GB fast-memory budget.
Strata reaches ~34 GB with its own formats in its own engine.
Calibration: Unsloth's imatrix (45 chunks, `unsloth_calibration_Qwen3.8-Flash-Next.txt`).

**Steps (each a local order at Mothership's factory desk; the specs are in the order archive):**
1. `flashnext-convert-bf16.20260929` — `run_tool.py` → `convert_hf_to_gguf.py D:\Models\Qwen3.8-Flash-Next-bf16
   --outtype bf16 --outfile B:\…\Qwen3.8-Flash-Next-BF16.gguf` (ai-toolkit venv python: torch/transformers present).
   1,224 tensors / 354.0 GB — the same tensor count as ollama's and Unsloth's builds. **Single-core:** ~23–25 MB/s,
   ~4 h, multi-minute pauses at 100% of one core with zero disk I/O (the converter has no thread option; PR #12837
   is unmerged — backlog "FASTER HF→GGUF CONVERSION"). Measure progress by the converter's own tqdm line in the
   order log or the process's I/O counters — the output file's listed length lags while it is open.
2. `flashnext-quant-q2mix.20260930` (queued behind 1) — ollama's own
   `…\Ollama-0.34.0\lib\ollama\llama-quantize.exe --imatrix …\imatrix_unsloth.gguf --tensor-type
   ffn_down_exps=iq2_s --tensor-type ffn_gate_exps=iq2_xs --tensor-type ffn_up_exps=iq2_xs --tensor-type
   per_layer_token_embd=iq4_nl <master> F:\Ollama-FlashNext-q2mix\Qwen3.8-Flash-Next-q2mix.gguf Q6_K 16`.
   Check the first per-tensor lines: every `ffn_*_exps` must say its override type (a regex that matches nothing
   is silent).
3. Install via store blob + hand-written manifest (reuse Build 1's projector/params/license blobs).
4. Same first look as Build 1; then family 2 at the tested context.

`run_tool.py` (`F:\foundry\workspace\flashnext\`) exists because the factory appends the order name to a payload's
argv and strict tools reject it: [[RUNBOOK-Compute-and-Training-Order-Submission]] §"Third-party tools as payloads".

## Builds 3 and 4 — q2k and iq2xxs (2026-09-30), and the three-way speed head-to-head
Same recipe as q2mix except the gate/up expert format: **q2k** = Q2_K (`flashnext-quant-q2k-nvme`, 69 min, 78,449 MiB
= 3.72 bpw); **iq2xxs** = IQ2_XXS (`flashnext-quant-iq2xxs-f2d2`, 84 min reading the F: master, 73,049 MiB = 3.46 bpw).
Both: rc 0, 146/1224 fallbacks (q2mix 194), the same 6 imatrix-less tensors. Per-block cost of the gate/up search:
Q2_K ~1.0 min, IQ2_XXS 1.34 (1.53 when the master was read off B:), IQ2_XS ~2.5; the ~20-min n-gram phase is CPU-bound
and did not change with the source drive.

**Head-to-head** (`flashnext-h2h-speed.20260930`, `workspace\flashnext\h2h_speed.py`, frontier mode on, all three on F:,
each loaded fresh after the previous was unloaded, one discarded warm-up, 3 × 500-token generations at temp 0,
default threads, `use_mmap:false`):

| build | loaded | on GPU | load | generation tok/s (3 runs) |
|---|---|---|---|---|
| q2mix (IQ2_XS) | 80.7 GB | 22.6 GB | 122 s | 13.32 / 13.33 / 13.33 |
| **q2k (Q2_K)** | 83.9 GB | 22.3 GB | 147 s | **14.38 / 14.34 / 14.38** |
| iq2xxs (IQ2_XXS) | 78.2 GB | 22.5 GB | 86 s | 13.46 / 13.47 / 13.48 |

**q2k generates fastest despite being the biggest** — consistent with generation being CPU-bound and Q2_K being cheaper
to decode than the IQ2 lattice formats (inference, not measured). **Load times are not comparable** across builds here:
the files had just been copied and the day's large copies had evicted the standby cache (q2mix loaded in 67 s this
morning). **Prefill is VOID in this run:** both long-prompt calls evaluated all 1,497 tokens (no prefix reuse), but the
first call per build was 2–10× slower than the second (q2mix 26/66, q2k 10/53, iq2xxs 49/509 tok/s) — the first pass
touches most experts and pages cold ones in. Needs more warm repetitions before any prefill claim. Quality not yet
compared (next: the coding families at temp 0 on each build).

## Explicit MoE placement: llama-server run directly (2026-09-30)
The ollama bundle ships the same engine as `llama-server.exe` (`…\Ollama-0.34.0\lib\ollama`, commit `0f3a71be1`, ROCm
backend in `rocm_v7_1\`). ⚠ **Run directly it is silently CPU-only** unless `GGML_BACKEND_PATH` points at
`rocm_v7_1\ggml-hip.dll` (or it is started from that folder): `--list-devices` shows nothing, the model "loads", and
the card sits at 0.9 GB / 0% — the first sweep was voided for exactly this. Flags that exist: `-ngl`, `--n-cpu-moe N`,
`--cpu-moe`, `-ot`, `--no-mmap`/`--load-mode`; NOT a GPU hot-expert cache. Sweep `flashnext-ncmoe-sweep-c`
(`workspace\flashnext\ncmoe_sweep.py`): q2k, `-ngl 99 --n-cpu-moe N -ot per_layer_token_embd=CPU --no-mmap -t 8
-c 16384`, raw `/completion` with 500 forced tokens, prompt cache off, VRAM from the box telemetry:

| N (layers' experts on CPU) | VRAM | generation (warm runs; 1st run ~7–8 cold) | prefill, 4,073 tokens (3rd pass) |
|---|---|---|---|
| 48 (all experts CPU-side) | 7.1 GB | **3.3** (paging) | — (stopped) |
| 34 | 20.2 | 14.04 / 14.06 | **174** |
| 32 | 22.2 | 14.41 / 14.64 | **173** |
| 30 | 23.2 | 15.17 / 14.98 | 90 |
| **28** | 23.7 | **15.32 / 15.32** | 80 |
| ollama, q2k (its own layer split) | 22.3 | 14.38 | not trustworthy (cold) |

Placement buys **+6.5% generation (15.3 at N=28) and ~1.7× prefill (174 at N=32–34)**; past N=32 prefill falls as the
card fills (no room for its compute buffers) — per job, N=32 for long prompts, N=28 for long answers. Generation barely
moves with placement. ⚠ N=30/28 ran after frontier mode's auto-restore (18:57) brought the apps
back — it watched only ollama; fixed (`1a3c58d`: a direct llama-server on a Flash-Next model now counts as loaded).

**Tuning ladders after the sweep** (`ladder.py`, same 500-token forced generations; prefill = a ~4k-token prompt,
2 passes, prompt cache off; `-c 16384`):

| order / arm | build, flags | VRAM | generation (cold, warm, warm) | prefill (2 passes) |
|---|---|---|---|---|
| `ladder-b` G-bestguess | q2k, N=26, `-fa on -ctk q8_0 -ctv q8_0 -ub 256 --prio 2 --poll 100` | 23.5 GB | 7.72, 15.28, 15.29 | 34.6, 55.6 |
| `ladder-c` I26 | iq2xxs, N=26, `-fa on -ctk q8_0 -ctv q8_0 -ub 256` | 23.5 | 12.66, 13.56, 13.74 | 62.9, 65.0 |
| `ladder-c` I24 | iq2xxs, N=24, same | 23.5 | 12.57, 14.58, 14.49 | 67.6, 69.7 |

Flash attention + 8-bit KV + a smaller micro-batch free enough VRAM for two more expert layers on the card (N=26),
and generation does not move (15.3 — the same as N=28 without them); prefill drops. (`--prio 2 --poll 100` rode in
the same arm, so its share is not separated — neither moved generation.) The smaller iq2xxs, even with
more of it on the card, generates slower than q2k.

### What limits generation (measured 2026-09-30 ~20:00, q2k under llama-server)
⚠ **Corrected 2026-10-01 10:45 (the owner: "you were saying the CPU couldn't keep up"):** "CPU-bound on expert decode"
was the wrong word. The ~28 ms/token of CPU-side work on the warm prompt is real, but 12 threads did not move it
and the repack bought only ~10%, so at least half of it is **DDR4 bandwidth** (streaming ~0.6 GB of expert weights
per token), not cores; a faster CPU on DDR5 would roughly halve that chunk (warm 28 → ~33 tok/s), no more. The
larger pieces were never the CPU: the ~38 ms GPU-side overhead (driver/node count — what MTP attacks) and, on
varied prompts, paging (RAM capacity). Ranking of what "cannot keep up": RAM capacity ≫ GPU-side overhead > DDR4
bandwidth > cores.
- **CPU-side expert work** (as first measured). All 8 threads pinned; the cheaper-to-decode Q2_K beats the smaller
  IQ2_XXS at every placement tried, so per-token decode cost matters more than size.
- **Not paging:** llama-server had 46 GB allocated, 21.7 GB of it resident, and paged in only **~0.5 MB/s (~130
  pages/s)** during generation — far too little to be the wall.
- **Not memory compression:** `Get-MMAgent` reads `MemoryCompression: False`; the compressor process used **0.11 of a
  core** and held ~460 MB during the run.
- So placement and VRAM tuning top out around 15.3 tok/s.

**Where the 65 ms per token go (derived 2026-09-30 22:00 from the sweep's slope, not yet measured directly):**
N=28 → 65.3 ms/token, N=34 → 71.2 ms: each expert layer moved to the CPU costs **~1.0 ms/token** (that figure includes
the GPU↔CPU hop for the layer, not just the compute). So the 28 CPU-side layers account for **~28 ms**, and the
intercept — attention/DeltaNet for all 48 layers, the GPU-side experts, the n-gram lookup, sampling, framework
overhead — is **~38 ms**. A 6B-active model's dense work on a 7900 XTX should take ~12 ms; GPU load during
generation reads ~42% at ~81 W, i.e. the card spends most of each token waiting. Two explanations, each with a
test: (a) per-kernel launch/sync latency (ROCm through WDDM; DeltaNet layers are many small kernels) — tested by
disabling graph capture (`GGML_CUDA_DISABLE_GRAPHS=1`) and by a **Vulkan** backend; (b) ops the ROCm backend does
not support hopping to the CPU — read from the graph-split count at `-v` (≈ 2×N+1 means none).
**Answered 2026-09-30 22:35 from `backend-fix` R0's verbose log (ollama's bundled engine, q2k, N=36):**
`graph nodes = 8098`, `graph splits = 74 (with bs=1)` = exactly 2×36+2 — **(b) is ruled out**, the splits are the
MoE offload and nothing else; `ggml_backend_cuda_graph_compute: CUDA graph warmup complete` per GPU segment — **graph
capture IS engaging**, so it is not the missing cure (the graphs-off arm prices what it is worth). What remains is
**node count: ~8,100 graph nodes per token** (a dense 32B is ~1,500) — the DeltaNet layers are expressed as hundreds
of small ops each, and at ~38 ms for ~8,000 nodes the card is at ~5 µs per node, launch-floor territory even with
graphs. GPU memory-controller load during generation reads 4.5% (`mem_load`), i.e. the card is nowhere near its
bandwidth. The levers that follow are a backend with cheaper per-node dispatch (Vulkan arm) or an engine version
whose DeltaNet graph is fused into fewer nodes (compare the Vulkan build's `graph nodes` count against 8,098).
Also from R0's profile: at N=36 the 35.6 GB of CPU-side experts no longer fit (RAM free min 3.2 GB) and the arm
paged at **14k pages-in/s, 55 MB/s** — so the ~1.0 ms/layer slope above includes paging onset past N≈30, and the
GPU-side intercept is **at least** 38 ms. Buffers at N=36: ROCm0 model 15.4 GB · CPU (experts) 35.6 GB ·
CPU_Mapped (the n-gram table, file-backed even under `--no-mmap`) 27.5 GB.
⚠ **Vulkan gotcha (2026-09-30 22:41):** the Vulkan backend put those same 35.6 GB of CPU-side experts into
**`Vulkan_Host`** — pinned, host-visible device memory, which under Resizable BAR is the card itself — logged 13×
`Failed to allocate pinned memory (ErrorOutOfDeviceMemory)` during load, and then could not allocate the 450 MB
recurrent-state cache (`failed to allocate buffer for rs cache`). Fix: **`--no-host`** ("bypass host buffer"), which
keeps them in a plain CPU buffer as ROCm had them; the env `GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM=1` is the other
switch in this build. Re-run: `flashnext-backend-vk2.20260930`.
**`backend-fix` results (22:27–22:55, ollama's bundled engine, q2k, N=36, 8 threads, AVX2 CPU backend):**

| arm | gen tok/s (cold, warm, warm) | prefill (2 passes) | VRAM | profile |
|---|---|---|---|---|
| R0, graphs on | 6.58, **13.40**, 13.39 | 22.4 / 43.0 | 17.4 GB | cpu 52% · pages-in 14k/s · disk 55 MB/s · gpu 42% 90 W · mem_load 4.5% |
| Rnographs (`GGML_CUDA_DISABLE_GRAPHS=1`) | 6.34, 13.22, **13.56** | 22.5 / 42.7 | 16.9 | cpu 51% · pages-in 14k/s · disk 54 MB/s · gpu 41% 90 W · mem_load 4.3% |

**Graph capture is worth nothing here** (within run-to-run noise, both directions) even though it engages — so the
GPU-side ~38 ms is not launch *submission* cost that graphs remove; it is per-kernel execution overhead across
~8,000 nodes plus 74 CPU↔GPU syncs per token. Levers that remain for that half: a backend/engine that FUSES the
DeltaNet ops into fewer nodes (compare the Vulkan build's `graph nodes`; the Vulkan backend has its own fusion pass,
`GGML_VK_DISABLE_FUSION` exists), or fewer CPU-side layers (VRAM-bound). The Vulkan arms (V0, VM2) failed to load
in this order for the `Vulkan_Host` reason above and run again as `backend-vk2`.

**`backend-vk2` V0 (23:06, Unsloth b11160 Vulkan, `--no-host`, otherwise identical flags, N=36): gen 6.49, 14.87,
16.12 tok/s; prefill 23.9 / 42.9; VRAM 16.5 GB; profile cpu 43% · pages-in 18.7k/s · disk 73 MB/s · RAM free min
1.6 GB · gpu 42% 91 W · mem_load 5.5%.** +11–20% over the ROCm control at the same N, still accelerating on the third
run, and **16.1 is above the day's best (15.3 at N=28 on the bundle)**. ⚠ Two variables, not one: backend AND engine
commit. The newer engine's load shows **`CPU_REPACK model buffer size = 16200 MiB`** beside `CPU 19397` — it repacks
the CPU-side expert weights (the IQ4_NL `ffn_down` experts, ~45% of them) into an AVX2-friendly layout; the bundle's
R0 had no such buffer (`CPU 35597`), and the Vulkan arm's CPU busy is 43% vs R0's 52% for more tokens. So the gain
may be the repack (CPU side), Vulkan (GPU side), or both. The Vulkan build does not print `graph nodes` at `-v`, so
the node-count comparison is not available from it. Separation planned: a repack-off arm if the build has the switch,
and Vulkan at N=30 (fewest CPU-side layers the head-less budget allows under Vulkan's 23.7 GB) for the fair fight
against 15.3.

**`backend-vk2` VM2 (23:11, Unsloth b11160 Vulkan, `--no-host`, N=36, MTP shared-Q4_K_M head, draft n-max 2): gen
7.15 (cold), 23.62, 23.04 tok/s; draft acceptance 78–82%, mean accepted length 2.55–2.63 per step; the head adds a
1.8 GB Vulkan0 model buffer and the recurrent-state cache grows 450 → 1,351 MB.** That is **+45% over the same engine
without MTP (16.1) and 1.5× the day's previous best** — the same 1.3–1.7× band Unsloth reports. The ladder filed the
arm as `ERROR` because its post-generation telemetry read stayed locked for 10 s (the numbers above are from the
arm's server log; no prefill and no VRAM reading for it). Fixed same pass: a locked telemetry read is a warning,
never an arm error (`3798273`). ⚠ This reverses the earlier MTP verdict completely — on the scalar-CPU ROCm engine
MTP read 9–10 tok/s and I called it a loss; on an AVX2 engine it is the biggest single lever measured tonight.
Next: MTP at the lowest N the head fits under Vulkan (N≈33), draft 2 and draft 3 (`backend-vk4`).

**`backend-vk3` (23:12–23:30, Unsloth b11160 Vulkan, `--no-host`, 8 threads):**

| arm | gen tok/s (cold, warm, warm) | prefill | VRAM | profile | compare to |
|---|---|---|---|---|---|
| V-N30 (N=30) | 9.48, **18.95**, 17.24 | 46.8 / 92.9 | 22.2 GB, no spill | cpu 45% · pages-in 11k/s · gpu 45% 94 W | bundle N=30: 15.0–15.2; day's best 15.3 |
| V-norepack (N=36, `--no-repack`) | 6.18, 14.64, 13.76 | 24.0 / 44.5 | 16.7 | cpu 38% · pages-in 18k/s · RAM free min 1.2 GB | V0 (repack on): 14.9 / 16.1; bundle R0: 13.4 |

**Without MTP, the Vulkan build at equal layer split beats the bundle by ~20% (19.0 vs 15.2 at N=30)** and is the
new no-MTP best. The N=36 pair decomposes the 16.1: repack off still reads +9% over the bundle (backend/commit), and
repack adds another ~+10% (CPU side). One pair each, so indicative; the ranking held in both warm runs. Prefill
under Vulkan at N=30 (93 tok/s on the second pass) is below the bundle's 174 at N=32–34 — the bundle keeps the
prefill crown for now; per job, as before.

**`backend-vk4` VM2-N33 (23:38, Unsloth b11160 Vulkan, `--no-host`, N=33, MTP shared-Q4_K_M head, draft n-max 2):
gen 10.17 (cold), 27.16, 25.74 tok/s; prefill 32.8 / 76.0; draft acceptance 79–81%, mean accepted length 2.6;
VRAM 22.1 GB, spill max 0.2 GB (under the 0.5 guard); profile cpu 42% · pages-in 19.5k/s · disk 76 MB/s · RAM free
min 3.3 GB · gpu load 28% 89 W.** ⭐ **Best measured on Mothership:
~26–27 tok/s** — 1.75× the morning's 15.3, ~14× yesterday's registry build (1.9). Note the GPU load fell to 28%
while the token rate rose: with MTP each GPU pass yields ~2.6 tokens, so the per-token GPU-side cost is amortised
across accepted drafts — exactly the mechanism that makes MTP the answer to a node-count-bound card.
**VM3-N33 (draft n-max 3, same order): gen 8.46 (cold), 27.17, 23.14; acceptance 68–70%, mean accepted length
3.04–3.10; VRAM 22.5 GB; gpu load 25%.** Even with draft 2 on the best run and behind on the other: the extra
accepted token per step is paid back by verifying one more. **Draft 2 is the setting.**

**`backend-vk5` (23:44–00:03, same engine and flags as VM2-N33):**

| arm | gen tok/s (cold, warm, warm) | prefill | VRAM | acceptance | gpu load |
|---|---|---|---|---|---|
| VM2-N33-repeat (identical to VM2-N33) | 9.48, **27.10**, 25.66 | 29.5 / 78.0 | 22.1 GB, spill 0.12 | 80.1% | 34% |
| VM2-N32-np1 (`-np 1`, N=32) | 9.37, **28.37**, 26.36 | 34.1 / 80.2 | 22.1 GB, spill 0.09 | 81.2% | 34% |

**`backend-vk6` (00:03–00:28, three re-tests on the VM2-N33 base, one variable each):** 12 threads 26.5 / 24.7
(prefill 30.6 / 69.6) — no gain; **`-ub 512` 27.6 / 25.7 with prefill 29.2 / 88.9** — generation unchanged, prefill
+11%, adopted for serving; CPU-side experts from the LAST 33 layers (`-ot` regex, first 15 on the card) 26.5 / 28.7
with prefill 30.5 / 55.9 and a lighter paging profile — generation within spread, prefill worse, not adopted.
**`backend-vk7` (00:40–01:05, after the Defender exclusions and the Q8 head download):** Q4-N32-np1-ref **28.3 /
25.6** (prefill 34.0 / 84.1, load 115 s) — the best config reproduced a third time, exclusions change nothing;
Q4-N33-np1-ctl 27.8 / 25.8; **Q8-N33-np1 25.7 / 26.7** (prefill 29.2 / 71.3, VRAM 22.1 vs 21.3) — the shared-Q8_0
head buys nothing over Q4_K_M at ~80% acceptance and costs ~1 GB; not adopted.
**Reproduced:** 27.1 / 25.7 against 27.2 / 25.7 the first time — the 27 is a measurement, not a lucky run.
**`-np 1`:** one slot instead of the default four shrinks the recurrent-state cache 1.35 → ~0.34 GB, which bought
one more expert layer on the card at identical VRAM: **28.4 / 26.4 — the best measured, +4%.** The ladder only ever
has one request in flight, so this costs nothing; for a served model `-np` is the concurrency setting, a different
decision. **Why this matters more than the expert cache:** the cache shrinks the 28 ms, but
the hop per CPU-side layer stays; if the hop is half of the 1.0 ms, the cache's floor is ~14 ms → ~52 ms/token
(≈19 tok/s). Cracking the 38 ms is worth more than everything else combined.

- ⚠ **Every number taken on the Unsloth ROCm engine before 22:10 is VOID as an absolute — its CPU backend has no
  SIMD.** `llama-server -v` prints the compile-time features before loading a model: the Unsloth **ROCm** zip's single
  `ggml-cpu.dll` reports `CPU : LLAMAFILE = 1 | REPACK = 1` and nothing else, while the Unsloth **Vulkan** zip and
  ollama's bundle report `AVX = 1 | AVX2 = 1 | F16C = 1 | FMA = 1 | BMI2 = 1` (they ship per-CPU `ggml-cpu-*.dll`
  variants and the loader picks the best). So the CPU-side experts ran scalar on every fork arm: the N=30 "spill" arm
  (5.96), and the MTP arms (draft 2 at N=35: 9–10 tok/s) — which I had read as "MTP loses" against the **bundle's**
  14.0, a comparison across two different CPU backends. **Withdrawn.** Grafting the Vulkan zip's AVX2 CPU variants
  onto the ROCm engine failed both ways (MSVC variants in the clang folder: `0xC0000135`; clang `ggml-hip.dll` in
  the MSVC folder: loads, but registers no device) — the two zips are built with different compilers, and
  cross-compiler DLL mixing is a dead end (the dead folders `…-rocm-avx2` / `…-rocm-avx2b` under
  `C:\ProgramData\HAI\tools\` can be deleted). **What runs instead (`flashnext-backend-fix.20260930`):** engines
  sane as shipped — ollama's bundled engine (ROCm + AVX2, commit `0f3a71be1`) for the ROCm control and the
  graphs-off arm, Unsloth's Vulkan build (AVX2 + MTP, `a6922cc`) for the Vulkan arm and the MTP arm. ROCm vs Vulkan
  therefore spans two commits three weeks apart (labelled); MTP vs no-MTP is clean within the Vulkan engine. The
  ladder's preflight now asserts `AVX2 = 1` in `system_info` for every engine — the tell was that it checked the GPU
  and never the CPU.

## RAM at 1:1 — the owner's BIOS change (planned 2026-09-30 evening)
Mothership's kit is G.Skill Trident Z Neo **F4-4000C18-16GTZN** (2×16 GB, rated DDR4-4000 CL18) running **4200 MT/s**
— above ~4000 the BIOS runs the memory controller at half the RAM clock (2:1), which adds latency. Zen 3's fabric
clock (FCLK) tops out around 1800–2000 MHz per chip and throws **WHEA 19** errors past what that chip holds.
Read-only pre-checks (2026-09-30): **0 WHEA errors** since the log began (2026-05-20) at FCLK 1800; SoC 1.1375 V;
VDDG IOD/CCD 1.048/0.947 V; CPUID `00A20F12` = **B2** stepping (the later silicon, widely reported to clock the fabric
higher); ASUS ROG Strix **B550-F Gaming**, BIOS **3611** (2024-09); sticks in A2/B2.
**Plan (updated 2026-10-01 17:00, the owner asked how high it can go):** walk DOWN from the top and stop at the first
clean step — **4000/2000 → 3933/1966 → 3866/1933 → 3800/1900** (all 1:1: ASUS Ai Tweaker, D.O.C.P. on, DRAM
Frequency, FCLK Frequency, UCLK DIV1 MODE = UCLK==MEMCLK). Clean = boots, survives heavy load, zero WHEA 19 in
Event Viewer (checkable remotely). Community data: most Zen 3 hold 1900; most throw WHEA 19 from 1933 up; a minority
of 5800Xs on 2-DIMM boards run clean; B2 anecdotally has more headroom
(overclock.net Zen 3 RAM guide and WHEA 19 thread; ROG forum). Fallback below 1900: 3600 / 1800.
**Ladder results (2026-10-01):** **4000 / 2000 — NOT CLEAN**: boots (17:21, both DIMMs at 4000 with D.O.C.P.
18-22-22-42, 1.40 V), but WHEA 19 at idle — 2 in the first minute, then bursts of ~100 at 17:23 and 17:25
(~200 in 4 min, on the minute marks when scheduled telemetry tasks wake). **3933 / 1966 — NOT CLEAN**: ZenTimings
confirmed true 1:1 (MCLK = FCLK = UCLK = 1967; VSOC 1.0875 V, VDDG CCD 0.947 / IOD 1.048), but **92 × WHEA 19 in
3 min** (57 at 17:30, 32 at 17:31). **3800 / 1900 (D.O.C.P., SoC auto)**: 11 × WHEA 19 at boot, NOT watched past
the window (the owner rebooted into another mix) — undecided. **4000 / 2000 loose timings (D.O.C.P. off → CL28-27-27-58)**:
103 in 2.5 min. **4000 / 1900 loose timings**: 0 at 2.5 min — but read BEFORE the burst window, so no evidence.
**4000 / 2000, D.O.C.P., SoC manual 1.1375 V (reads 1.125 V)**: **205 in 4 min** (28 / 73 / 101 bursts) — voltage
alone does not rescue 2000. ⚠ **Every boot follows one clock: 1–2 errors, then a ~100 burst ≈ 2½ minutes after boot**
(MCA bank 27, "Bus/Interconnect Error" = the fabric). Any WHEA read earlier than ~3 minutes after boot is
inconclusive; the watcher waits 5 minutes. Not the DipAwayMode startup error: WHEA log held ZERO entries from May to
17:00 today across months of boots with that error, and none appears on these boots. The board's AUTO SoC voltage
fell from 1.1375 V (at 4200 / FCLK 1800) to 1.0875 V on every new mix.
**3800 / 1900 loose timings (D.O.C.P. off, CL28), SoC auto 1.0875 V**: 0 at 3.4 min. **✅ CLEAN CONFIG (18:33
boot): 3800 / 1900 true 1:1 (MCLK = FCLK = UCLK = 1900), D.O.C.P. CL18-22-22-42 1T, SoC manual (reads 1.119 V),
VDDG CCD 0.947 / IOD 1.048 auto — 0 × WHEA 19 at 3.8 min**, past the burst window. Speed order
`flashnext-ram-after` placed 18:38 (warm ×2 + five varied prompts, flags identical to today's "before" runs); the
model's memory load doubles as the stress test — recount WHEA after it. Summary of the ladder: this chip's fabric
holds 1900, not 1933+, at any SoC tried up to 1.125 V. The BIOS (B550-F 3611) has no
separate "UCLK DIV1 MODE" item; its AMD Overclocking › Infinity Fabric Frequency and Dividers help reads "Auto:
FCLK = MCLK" — leave that on Auto and set the pair in Ai Tweaker. **Read MCLK / UCLK / FCLK with ZenTimings, already
installed and checksum-verified 2026-09-30: `C:\ProgramData\HAI\tools\ZenTimings\ZenTimings.exe` (v140.665)** — the
library had no pointer to it, so it was nearly re-downloaded.
⚠ **Corrected caveat:** this section used to say 1:1 "lowers peak bandwidth". On a single-CCD Zen 3 (the 5800X)
DRAM reads cross the fabric at ~32 bytes per FCLK cycle, so read bandwidth is capped by FCLK (~57.6 GB/s at today's
1800) — BELOW what DDR4-4200 offers. Raising FCLK to 1900/2000 likely raises the real read ceiling AND cuts
latency. A documented property of the chip, not yet measured here. It matters because CPU expert compute is ~45%
of each warm token and looks bandwidth-bound (§"MEASURED: where the winning config's time goes"). Measured the same
way either way: the same ladder order before and after, nothing else changed.
**✅ RESULT (order `flashnext-ram-after`, 18:40–19:01, plan `plans\ram-after.20261001.json`; Unsloth b11160
Vulkan, q2k, N=32, 8 threads, `--no-host -np 1`, MTP draft 2, `-ub 512`, flags identical to the day's "before" runs):**

| arm | before (4200 2:1) | after (3800/1900 1:1) | draft acceptance before → after |
|---|---|---|---|
| warm repeated prompt, gens 2 / 3 | **28.11 / 26.22** (same day; best ever 28.4 / 26.4, `vk5`) | **30.10 / 27.70** and repeat **30.24 / 27.35** | ~0.80 → 0.812 both |
| warm prefill | 42.5 / 110.9 | 39.7 / 116.5 · 50.6 / 112.6 | |
| five varied prompts, mmap | **8.88, 12.98, 14.24, 13.70, 12.69** (`validate-mmap`, 09:11) | **11.65, 17.71, 20.77, 20.30, 17.35** | mix → 0.754 |
| varied prefill | 47 / 122 | 90.3 / 142.8 | |
| varied paging | 60k pages-in/s, 241 MB/s, RAM free min 0.25 GB | 47.5k/s, 189 MB/s, RAM free min 0.46 GB | |

**Warm: +6–7%, replicated (n=2, both arms 30.1–30.2 / 27.4–27.7), acceptance level — attributable to the RAM**
(the warm arm does not page; 18k pages-in/s is the load tail). This is the first config past 30 tok/s.
**Varied: median 12.98 → 17.71 (+36%) — NOT all RAM.** Paging FELL 21%, which faster RAM cannot cause; the "after"
run was on a fresh boot (18:33, little else resident) while the "before" ran after hours of uptime, so memory
state is a second variable. Separate it by re-running the varied arm alone after several hours of uptime, nothing
else changed. **Uptime check (order `flashnext-q2kd2-ram`, 19:24, ~55 min after boot, same flags):** q2k mmap
**10.91, 14.41, 19.49, 19.50, 16.60** (median 16.6, pages-in 49k/s) — the fresh boot was worth ~6%; still +28% over
the morning's 13.0, which had hours of uptime, so the RAM's varied share lies between the warm +6% and that.
**q2kd2 `-lm none` on the new RAM, same order:** **16.84, 13.22, 16.69, 15.91, 12.93** (median 15.9, was 14.7),
prefill **173.6 / 178.7** (was 117.6 / 158.5), pages-in 1.4k/s, acceptance 0.766 — flat, RAM-resident, does not
decay with uptime. The two varied configs are now level on median; q2kd2 wins prefill 2× and stability.
**Stability: 0 × WHEA 19 from the 18:33 boot through the whole order (27 min, incl. full model
load)** — the clean config holds under load. Keep 3800 / 1900.

## Review of the night's claims (2026-10-01 08:30, adversarial pass at the owner's request)
Each claim with how much evidence stands behind it and what would overturn it. "n" = independent pairs.

| claim | strength | what could be wrong | fix / status |
|---|---|---|---|
| **28.4 / 26.4 tok/s best** (Vulkan + MTP, N=32, `-np 1`) | reproduced 3× (28.3–28.4 / 25.6–26.4), tight spread | **one prose prompt, temperature 0, repeated** — hot expert set, 80% acceptance and prefill cache state are all prompt-specific; serving runs varied prompts at t up to 0.9 | `validate-prompts` order (five prompt kinds × t0 / t0.6 / t0.9, + plain t0): the production number is that spread |
| MTP +45–50% | several pairs across N (16.1→23.6 at N=36; 19.0@N30 vs 28.4@N32) | the V0 baseline (14.9, 16.1) was still warming, so +45% at N=36 may be +35–45%; acceptance falls with temperature | same order measures acceptance per prompt and temperature |
| forced 500-token generations not inflated by post-answer filler | checked: no end-of-turn marker, 0 repeated windows, tails mid-answer (2 arms) | — | ✅ |
| Vulkan +9% over the ROCm bundle at equal split (before repack) | n=1 (14.6 / 13.8 vs 13.4 / 13.4), cross-commit | within run-to-run spread; **should read "+3–9%, one pair"**; the engines differ by a month of upstream | honest label applied; not worth a re-test while Vulkan+MTP leads outright |
| engine CPU repack ≈ +10% | n=1 (V0 14.9 / 16.1 vs norepack 14.6 / 13.8) | same spread caveat; V0 still warming | indicative only |
| graph capture worth nothing | n=1 null (13.4 vs 13.2–13.6) | a null at n=1 is weak, but consistent with mem_load 4.5% | low priority |
| ~8,100 nodes/token, GPU launch-bound ≈ 38 ms | derived (sweep slope + intercept), supported by gpu 42% / mem_load 4.5% and by MTP cutting GPU load to 28% | never measured per node; paging onset is folded into the slope | stands as a labelled hypothesis |
| draft 3 no better than draft 2 | n=1 each; second runs 23.1 vs 25.7, outside the ±0.4 spread of VM2 repeats | — | ✅ |
| `-np 1` +4% | n=1 vs 3 repeats of the 4-slot form | small; the mechanism (one more layer) is real | ✅ keep |
| `-ub 512` prefill +11% | n=1; **second-pass prefill at the same config ranges 70–84**, so 88.9 is +6–27% | prefill measurement is noisy (page-cache state); "+11%" overstated | keep (generation unchanged, no downside); re-measured in `validate-prompts` (uses 512) |
| 12 threads no gain; last-33 placement no gain, worse prefill | n=1 each; last-33's 55.9 prefill is outside the spread, its 28.7 gen inside | "slightly worse" for 12 threads was overclaiming; "no gain" is right | ✅ |
| Defender exclusions no effect | n=1 pair + load times | — | ✅ (kept for doctrine, not speed) |
| Q8 head no gain | n=1 (25.7 / 26.7 vs 27.8 / 25.8) | within spread either way; costs VRAM | ✅ not adopted |
| cache +31% at 48 slots | 6-gen series, cache still climbing (22.7) vs control plateau (17.3) | **warmed on six repeats of ONE prompt** — the LRU memorised that prompt's experts; varied prompts will hit less; 58% hit rate was measured on repeats too | the mix ladder uses the fixed prompt for comparability; a varied-prompt cache arm is the next check |
| 96 slots at N=38 = paging cliff | RAM free min 0.11 GB | a RAM fact, not a cache fact: on 32 GB, N≥38 unrepacked is out | structural; sets the slot budget |
| Unsloth ROCm zip has no SIMD | `system_info` on the binary | — | ✅ |
| Vulkan non-deterministic at t0 | 3 runs, 2 arms | — | ⚠ narrower than this row said (2026-11-05): runs in one already-used engine differ; the 27B from fresh loads matched itself in 16 arms ([[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]]). `GGML_VK_DISABLE_COOPMAT` untested |
| garbage with cache + repack is a PR defect | 2 arms garbage with repack, 3 coherent without; the PR thread has the same class on SYCL | — | ✅ root cause confirmed in source and FIXED by our patch `moecache_unrepack.py` (de-interleave on upload; coherent 13:12). Not reported upstream (the owner) |

**⚠ `validate-prompts` MTP-t0 (08:27) — the review's worry was right, and larger than feared: five different
prompts (code, explanation, arithmetic, JSON, fiction) at temperature 0 on the 28.4 config read 7.68, 9.21, 9.93,
10.43, 10.85 tok/s; prefill 45.9 / 112.1.** Profile: **pages-in 28,484/s (max 67,862), disk 113 MB/s, RAM free min
0.41 GB, gpu 15% at 78 W.** The 28.4 was a prompt-specific hot set that happened to be resident; each new prompt
pages its own experts in through 4 KB pagefile faults, and the 32 GB of CPU-side experts do not fit in 32 GB of RAM.
The rate climbs prompt to prompt as the resident set grows, so the plateau over many prompts is above 10 — but for
varied work **paging is the wall** (⚠ 14:00 refinement: not "RAM" as such — full telemetry shows the PAGEFILE DRIVE
C: saturated while the model drive F: idles, and several levers move the wall without RAM: mmap, the cache, the
fitting cook; see the whole-system table), and every lever measured on the repeated prompt was
measured on a warm cache. These prompts also engage thinking (`<think>` with content; the MoE prompt's was empty).
**`validate-prompts` complete (09:00) — the varied-prompt table (five prompts: Python+pytest, sky-is-blue
explanation, two-trains arithmetic, JSON records, lighthouse fiction; 500 tokens each, in that order):**

| arm (Unsloth Vulkan, N=32, `-np 1 -ub 512`) | gen tok/s per prompt | prefill | acceptance per prompt |
|---|---|---|---|
| MTP, t=0 | 7.68, 9.21, 9.93, 10.43, 10.85 | 45.9 / 112.1 | 0.93, 0.65, 0.91, 0.85, 0.53 (mean 0.75) |
| MTP, t=0.6 | 9.13, 10.92, 11.96, 11.08, 11.77 | 45.3 / 104.9 | 0.88, 0.65, 0.85, 0.86, 0.60 |
| MTP, t=0.9 | 7.65, 7.85, 6.54, 7.29, 6.40 | 29.1 / 85.7 | 0.87, 0.65, 0.84, 0.86, 0.52 (RAM free min 0.18 GB) |
| no MTP, t=0 | 7.06, 9.02, 9.48, 9.85, 9.93 | 41.6 / 112.1 | — |

Reads: (1) **on varied prompts the wall is paging, and MTP is worth ~+10% there, not +45%** (10.9 vs 9.9 on the fifth
prompt) — a draft head cannot speed up a page-in; (2) **acceptance is task-shaped**: code 0.93, arithmetic 0.91, JSON
0.85, explanation 0.65, fiction 0.53 — the MoE prompt's 0.80 was mid-high, so the warm 28.4 was also a kind prompt;
(3) temperature 0.6 costs nothing (acceptance barely moves), 0.9 ran slowest with the tightest RAM — variance of a
paging run more than a sampling effect; (4) all outputs coherent (samples kept per arm). **The honest production
number on 32 GB RAM today: ~10–12 tok/s on varied prompts, ~28 on a warm repeated one.**
Next (`validate-mmap`): `-lm mmap`, dismissed on warm single-prompt data (−30% gen through ollama), re-tested on
varied prompts — file-backed experts page in as contiguous ~500 KB reads from the NVMe instead of pagefile faults.

**Not measured at all, and relevant to serving:** context sizes above 8k (VRAM → fewer layers); concurrent requests
(`-np` > 1); long multi-turn sessions (prompt cache on); the hard-bench scorer's view of answer quality from this
engine (only the text's coherence was checked). These are the next orders if Flash-Next goes on the menu.

## MEASURED: where the winning config's time goes (2026-10-01 15:45, order `flashnext-profile`)
the owner 15:30: *"try to stop guessing… We want educated theories. Based on evidence."* The 28.4 config exactly
(Unsloth b11160 Vulkan, q2k, N=32, MTP, warm prompt) with Vulkan's per-op GPU timer (`GGML_VK_PERF_LOGGER=1`,
summaries every 2,000 GPU graph runs). The timer adds overhead (this arm ran 20.8 / 19.7 vs 28.35 untimed), so
absolute ms are inflated; shares are what it is for. Analyser: scratchpad `profile_analyse.py`, `profile_dims.py`.
- **GPU kernels are only ~37% of warm decode wall time** (33% and 41% in the two warm generations; 18.3 s of
  49.4 s). **~63% of each token is spent outside GPU kernels** — CPU expert work plus the waits and hand-offs
  between CPU and GPU. Matches the telemetry: GPU load 28–35%, nothing saturated.
- **Inside the GPU, dense Q6_K mat-vecs dominate** (attention / shared projections; shapes 2560×6144,
  10240×2560, 6144×2560, 12288×2560 with n=3 from MTP's verify batch). They run at **~190–270 GB/s effective
  against the card's ~960 GB/s** — 20–28% of its bandwidth — and the small ones (m=4, 320, 640) at 2–93 GB/s:
  launch-latency territory. The routed-expert matmuls on the GPU (`MUL_MAT_ID` iq4_nl / q2_K) are ~9%.
- **What is still unmeasured:** how the ~63% outside the GPU splits between CPU expert compute and pure waiting.
  The N-sweep slope (~1 ms per CPU-side layer, measured 2026-09-30) puts CPU expert work at ~32 ms of a ~92 ms
  untimed pass; GPU kernels ~35 ms untimed (37% of 92); that leaves **~25 ms per pass unexplained** — the
  hand-off/sync candidate. That split is the next measurement, not an assumption.
- **BOTH SIDES IN ONE RUN (16:35, order `flashnext-profile2b`, the owner: "why didn't we measure it at the same
  time?"):** our scheduler profiler (`patches\sched_profile.py`, env `GGML_SCHED_PROFILE=N`) times, for every
  segment of every graph, the wait before it, its input copies (which include waiting on the other device) and
  its launch — a CPU segment's launch IS its compute; a GPU segment's launch is only the submit. On the 28.4 flags
  (our build `llama-mix-512d05731`, IQ4_NL repack only), per graph of ~24–25 ms (stable summaries):
  **CPU expert compute 10.2–12.0 ms (~45%)** · CPU waiting for GPU results + copies 6.6 ms (~27%) · outside the
  scheduler (sampling, MTP logic, final GPU sync) 4.0 ms (~16%) · **CPU time issuing Vulkan work 2.5 ms (~10%)** ·
  GPU-side copies 0.4 ms. The CPU and GPU strictly take turns — nothing overlaps. CPU expert time is consistent
  with DDR4 bandwidth (12/16 threads and faster Q2_K kernels did not help warm; bytes per pass ÷ bandwidth lands
  here) — an inference from three measurements. The profiling build ran at winner speed (28.33 first warm gen vs
  the untimed Unsloth reference's 28.11 in the same order), so this split stands for the 28.4 config.
  **Submit cap tested 16:45:** `GGML_VK_MAX_NODES_PER_SUBMIT=1000` on the winner read **27.93 / 25.72** vs the
  reference **28.11 / 26.22** — no gain; the ~10% issue cost is per-node command recording, not submit count. The
  400 arm was stopped as a predicted no-op.
- **Overlap research (17:05, Sonnet subagent, primary sources: llama.cpp master via `gh api`, the KTransformers
  SOSP'25 paper):** the CPU/GPU turn-taking is structural — `ggml_backend_sched_compute_splits` runs splits in
  order, every CPU split's input copy synchronizes the GPU, one event per backend, and in `qwen4exp.cpp`
  `build_layer_ffn` the GPU shared expert is built AFTER the CPU routed experts. Within one token the next layer's
  attention truly depends on this layer's experts, so EXACT overlap is not available. The only published technique
  that overlaps it is **KTransformers' Expert Deferral**: route the top experts immediately, defer the rest to the
  next layer's residual add (decode only, never the last layer, ≥2 immediate): **+33% decode on DeepSeek-V3, up to
  +45% vs llama.cpp-style hybrid, ≤2 points on HumanEval/MBPP/GSM8K/StrategyQA** — not lossless. Inferred for us
  (deferring ~4 of 10 experts under the ~7 ms of GPU waiting): **~1.15–1.3×, ~32–36 tok/s**. Risks: Qwen3.8's
  4-stream hyper-connections make the delayed add unvalidated; MTP's 3-token verify batches can widen the expert
  union. Lossless pieces: llama.cpp PR #25319 (async scheduler copies, open, ~5% on a UMA device) and running the
  shared expert concurrently (it is one 640-wide expert — inferred ≤5%). Plan if the owner wants deferral: Stage A
  graph-only (~60–100 lines in `build_layer_ffn`, measure quality + MTP acceptance first), then Stage B scheduler
  events (~120–200 lines). Ledger levers: `expert_deferral`, `async_sched_copies`, `shexp_overlap`.
- **Expert Deferral BUILT and MEASURED (2026-10-01 19:20–20:20, the owner: "Yep let's keep going").** Patches (mirror
  `workspace\flashnext\patches\`, all env-gated, off = the upstream graph): `moe_expert_defer.py` (Stage A: the 4
  lowest-weighted of 10 experts on layers < `LAB_MOE_DEFER_LAYERS` join the residual at the END of the next layer;
  routing weights normalised over all 10 first; adds a `weights_in` parameter to `build_moe_ffn` and fixes its
  aggregation to sum the passed count — it summed `hparams.n_expert_used(il)` = 10 views regardless),
  `moe_defer_overlap.py` (Stage B, `LAB_SCHED_OVERLAP=1`: the deferred CPU split skips the scheduler's pre-split GPU
  sync; its inputs are pinned to the CPU by name `lab_defer_cpu*` via the graph callback; a Vulkan event after the
  previous GPU split's input copies protects pinned host memory), `moe_defer_onecopy.py`, `moe_defer_order.py`.
  **Why the scheduler serialises (read in source):** `ggml_backend_sched_compute_splits` syncs the previous backend
  before any input-less split on another backend, and a CPU split's GPU-input copy syncs the GPU (CPU backend has no
  async copy); the CPU compute buffer is ALWAYS the GPU's pinned host buffer (`llama-context.cpp`, regardless of
  `--no-host`), so CPU→GPU copies are async reads of host memory — skipping a sync needs that memory protected.
  **Measured, order `flashnext-defer-diag` (same binary `llama-mix-44357256e`, winner flags, per-split timer):**
  everything off **30.0 / 28.8 tok/s, 22.1 ms/graph** (9 splits per backend; wait+copies 6.6 ms); deferral alone
  **19.2 / 20.2, 30.4 ms** (17 splits); deferral + overlap **22.1 / 23.8, 24.3 ms** (overlap engaged — server log
  `lab overlap: deferred-expert CPU split running alongside the GPU`; acceptance 0.785 vs 0.812; text coherent).
  ⇒ **The overlap works (−6 ms/graph) but deferral's graph costs +8 ms by nearly doubling the CPU⇄GPU hand-offs**;
  the scheduler is ONE thread launching splits in order, so while it runs the deferred CPU split it cannot queue the
  GPU's next work. ⚠ An earlier reading in chat blamed copies — wrong: the timer's "copy" buckets include waiting.
  **Ordering build (`llama-mix-058c7d1f3`, order `flashnext-defer-d`, 20:27): 22.2 / 23.1**, 9.2 splits per backend,
  25.9–29.4 ms/graph — the GPU separator (shared-expert gate multiply) was placed on the CPU by the scheduler, so the
  deferred split merged into the next CPU split (server log: `a deferred-expert split has 5 input(s) - it waits for
  the GPU (no overlap)`); even with no extra splits the separate deferred mul_mat_id chain costs 4–7 ms (per-op
  thread barriers). **Verdict so far: in llama.cpp's one-thread scheduler every CPU⇄GPU hand-off costs ~1 ms and the
  prize is bounded at ~3.6 ms/graph (the deferred experts' share of 9 ms CPU compute, against 6.6 ms of waiting) ≈
  30 → ~35 tok/s at zero overhead; every graph-level design spent more than that.** The one design left that could
  approach it: the deferred experts on a helper thread pool INSIDE the existing CPU split while the main thread waits
  on the GPU (a custom op).
  **Prefix-first (the owner 20:40 "trying it inside the step"; `moe_defer_prefix.py`, build `llama-mix-14c35c260`, order
  `flashnext-defer-e`):** a CPU split that starts with the deferred experts runs them BEFORE its input copies (the
  GPU wait) — no helper thread needed, no extra hand-offs (server log `lab prefix: deferred experts computed before
  the split's GPU wait (14 nodes)`). Results, same order and flags: prefix **24.62 / 26.36**; prefix + one CPU→GPU
  copy of both results (`LAB_DEFER_CONCAT=1`) **25.22 / 27.83**, CPU→GPU copies back to 0.37 ms/graph, wall
  **22.8 / 21.1 / 25.3 ms/graph** vs the control's 22.1 / 22.4 / 25.3; control **29.67 / 28.48**. Acceptance 0.785 vs
  0.812 on every deferral arm. **VERDICT (21:05): built, measured, PARKED AT PARITY.** Per pass deferral now equals
  the plain graph; tokens per pass fall with acceptance, and gen 2 reads low on every deferral arm (25 vs 30; gen 3
  27.8 vs 28.5). The overlap hides only part of the CPU's wait: most of that wait is per-step GPU latency (thousands
  of small kernels), not GPU work. Graph reuse is NOT the 4 ms "outside the scheduler": the main graph was reused
  ~186 of ~190 passes per 500-token answer (`graphs reused` in the server log). Resume only with a reason the
  acceptance cost can be bought back (e.g. k=2) or the GPU per-step latency falls.
- **Card health ruled out (16:05, the owner: "make sure we aren't running into the stalled card stuff"):** the stall
  daemon's own burst (`p20_rescue_burst.py`, dim 6144, 25 s) on the idle card, as two orders: **77.3 and 77.0
  TFLOP/s** — at the library's healthy ceiling (~76; stalled ≈ 2%, flip boundary 14;
  `Driver-RootCause-Phase0-Exhibits`). No second GPU context at the time, and today's telemetry (75–93 W,
  600–1,350 MHz) is nothing like the wedge signature (220–250 W, >2,900 MHz). The card is healthy; the GPU's low
  share of each token is the workload, not a sick card.

## The lab's own speed tricks, mapped onto Flash-Next (read BEFORE proposing any lever)
the owner asked for this on 2026-09-29 21:44: *"Look at the library and see what cache tricks we have used in the past,
that's how I got big models to fit and run and load quickly."* The reply found the right list in
`Flux2-32B-QLoRA-AMD-ROCm-Windows.md` and then collapsed it to ONE lever — "cook a quant that fits RAM" — and three
sessions (Opus 5.5, Fable, Opus 5.5) chased RAM for two days. the owner, 2026-10-01: *"I've definitely talked about
caching and preloading… is this just because you guys keep getting stuck on the ram issue?"* Yes. The tricks are a
FAMILY, and each has a Flash-Next counterpart:

| lab trick (where it came from) | Flash-Next counterpart | status |
|---|---|---|
| **Do the expensive transform once, save the result** (Flux 2: bf16 → uint4 `.pt`, 8 min load → seconds) | weight repack at load (decode once into the CPU's fast layout); our own cooks | IQ4_NL repack ✅; **Q2_K repack on AVX2 patched 13:22** (31.2 GB repacked vs 14.4) |
| **Cache in-between results so a phase never re-runs** (text embeds / `.cond`: the encoder never loads during a render) | prompt-prefix KV cache (`cache_prompt`, `--slot-save-path`) for served use; a saved hot-expert profile to pre-fill cache slots | open — both exist or nearly exist, neither wired |
| **Keep the hot thing on the GPU, park the rest** (one model on the card at a time) | hot-expert cache: VRAM holds the experts actually used, not whole layers | ✅ works with repack since 13:12 (our patch) |
| **Pre-bake ahead of the run** (bake missing `.cond` files first via `p20_bake_cond.py` before a render ladder) | warm the cache / page cache before timing (first gens run 7.7 cold vs 27 warm) | open |
| **Load once, run many** (menu filler runs a model's cells back to back) | a served engine that stays up instead of a reload per job | backlog: serve through HAI |
| **Manual non-mmap loader for huge files** (Windows mmap crash at ~64 GB) | `-lm none` vs `mmap` choice | measured both |

**Full telemetry (since 2026-10-01 14:00, ladder `f548dd0`):** every arm now prints `TREE` lines — GPU (clock,
power, utilisation per engine type incl. the Copy engine = PCIe traffic, VRAM dedicated + shared), CPU (total,
busiest core, cores >80%, clock % of base), RAM lists (free, file cache, standby, modified, committed), paging
(pages-in, hard-fault reads, pagefile %), every drive (read MB/s, queue, ms/read, idle %), and the engine's own
draft acceptance + expert-cache hit rate. Self-test on the live box: `tree_selftest.py`. the owner: *"Guesses are bad,
and informed educated theories are good"* — full telemetry is what unlocked the Flux work.
**First reading (theory, load-time samples only):** drives are C: = T-FORCE TM8FPZ001T NVMe (Windows + the only
pagefile, 100–120 GB), F: = PNY CS2241 Gen4 NVMe (models), D: 870 QVO SATA, E: 860 EVO SATA, B: USB HDD. During
the reference arm's load C: sat at **0% idle, queue 5–13, up to 10 ms/read, 25–40 MB/s** with 13–23 GB on the
modified list, while F: idled (99.9%) — and F: read **930 MB/s at queue 27** when asked deep. Theory: `-lm none`
and repacked experts are anonymous memory, so past RAM they are WRITTEN to the pagefile on the OS drive and read
back from it; mmap'd experts are clean file pages, dropped and re-read from F: with no writes. That one mechanism
explains mmap > none, full repack losing on varied prompts, and q2kd2 winning there. ✅ **CONFIRMED under
generation 14:35** (`stack2` varied arm: cache 32 + full repack + MTP, N=34, mmap): gen **8.1, 6.1, 9.0, 5.7, 9.2**
(cache hit 38%); **C: 96.8% busy, queue 11, 177 MB/s; F: 94.5% idle; RAM free 0.0 GB with 7.4 GB on the modified
list; GPU 613 MHz, compute engine 9.8%, 78 W; CPU 34%, no core above 55%.** Every resource idles behind the pagefile
drive. (Warm, the same engine left C: 68% idle — the wall is varied-work paging of anonymous memory.) Consequences:
keep paging-prone experts file-backed on F: (mmap, no repack — the `hot48` design); a pagefile on a fast non-OS
drive is now an evidenced option (the owner's setting). ⚠ Instrument note: the TREE `ms/read` column read 0.0 in these
arms while the 13:55 probe read 10–23 ms — treat it as unverified until checked against a known positive.
**Instrument note:** the ladder's profile line printed RAM, paging and disk, but not the GPU clock, so every
session's dashboard was RAM-shaped. It prints `@ <MHz>` since `9608ef4`; the card generates at ~1,350 MHz.

## The arsenal, combined (2026-10-01 13:50 — the owner's bag of tricks + every source in the research doc, read in full)
New from reading the two never-mined sources (the DocShotgun MoE offload guide; the Flash-Next speed thread,
HF discussion #3): **VRAM margin** — keeping ~1 GB free vs filling the card is reported as a 3–4× decode
difference (our runs carry 0.1–0.28 GB of shared-memory spill); **`-lzm` lazy mode** — "master mmaps lazy tensors;
halves prefill and adds 50 s load", workaround `-lzm off` (our runs log `per_layer_token_embd … lazy read enabled`,
default `auto`); **big batches for prefill** — `-b 4096 -ub 4096` (guide) / `-ub 2048` (4×3090, 1,029 tok/s
prefill), `GGML_OP_OFFLOAD_MIN_BATCH`; **expert-cache tuning** — `--moe-expert-cache-inserts 2`, and **q4_0 KV**
("+0.4% perplexity") to free VRAM for slots ("+25% with 120 slots/layer"); **`--reasoning-effort low`** to stop
~1.6k unwanted thinking tokens (useful-answer time, not tok/s); **ngram-mod** speculation (+11%; our engine has
it, one `--spec-type` at a time); and a **custom fork with "hyper-connection fusion" + sparse-attention decode**
(5090: 47–62 tok/s) — fusion is exactly the cure for our ~8,100-node GPU side, unresearched.

| term it attacks | weapons (✅ in use · ⏳ built/testing · ○ open) | conflicts / stacking |
|---|---|---|
| **G** GPU side, ~38 ms (derived) | ✅ MTP · ○ fused-kernel fork (research: fusion already in b11160) · ○ VRAM margin / zero spill · ○ HAGS | the low clock (600–1,350 MHz) is a starvation gauge, not a lever (the owner 15:00): the card clocks down because it waits; feed it and it ramps |
| **C** CPU expert reads | ✅ IQ4_NL repack · ⏳ Q2_K repack (warm 24.7 → 27.5, prefill best yet 39 / 97) · ✅ expert cache (our patch) · ○ cache inserts / q4_0 KV for more slots · ○ hot-expert warm start (moe-autopilot's idea) | **cache × Q2_K repack: needs a `block_q2_Kx8` inverse** before they stack; slots need VRAM → from q4_0 KV or fewer GPU layers, never more CPU layers |
| **P** paging | q2kd2 fits but slow kernels · mmap · ○ fault-ahead patch (backlog) · ○ `-lzm` | lazy-off maps 27 GB of embedding table into the page cache — competes with experts for RAM |
| **prefill** (measured best: **174** ollama's ROCm bundle N=32–34, **159** q2kd2 `-lm none` (no paging), 112 varied; the MTP + Vulkan serving config only 80–97; peers 340–1,029) | ○ `-ub 2048/4096` · ○ `-lzm off` · ○ op-offload batch threshold · ○ prompt-prefix cache | prefill is the most backend- AND RAM-sensitive term: ROCm beat Vulkan 174 vs 93 at equal N, and the fitting cook doubled it. Big `-ub` grows the compute buffer → VRAM fights slots and margin |
| **useful output per wall-clock** | ○ `--reasoning-effort low` · ○ prompt-prefix cache · ○ serve-and-stay-loaded | — |
| **batching** | closed until C moves to the GPU | reopen after cache + full repack |

**The arsenal is a SYSTEM — the WHOLE box, every resource, not two memory budgets** (the owner 2026-10-01: *"some of
them may have effects that affect the others… make sure this whole arsenal isn't just being looked at as
individual tools"*, then correcting the first draft of this section, which framed it as RAM + VRAM: *"we want
everything to be working at its maximum like the GPU we want to actually be under full load. So it's really the
whole system… GPU, RAM, VRAM, the CPU, the paging files on the NVMes, the caches… and anything else we can think
of"*). The goal is every resource doing useful work; an IDLE resource is as much a finding as a saturated one.
Plan each config as a whole system, never one tool against a fixed background, and read it with full telemetry:

| resource (ceiling) | what draws on it | measured state in the serving config | weapons that move it |
|---|---|---|---|
| **GPU compute** (7900 XTX, ~2.5 GHz boost, ~350 W) | ~8,100 nodes/pass; GPU-side experts; cache hits | **starved: 9–35% load, 600–1,350 MHz, 75–93 W** — the clock is DPM correctly clocking down while the card waits on the CPU and the drives (the owner 15:00: *"it's supposed to ramp itself up just like it does for every other job or game I play"*) — read it as a gauge of starvation | MTP, batching (once C moves), cache (moves expert work here), anything that stops the waiting |
| **VRAM** (24 GB, keep ~1 GB free) | GPU-side expert layers ~1.05 GB each · MTP head 1.8 · recurrent state 0.34 (`-np 1`) / 1.35 (`-np 4`) · KV · compute buffer (grows with `-ub`) · cache slots | full, 0.1–0.28 GB spilled to shared | q4_0 KV, smaller head, fewer slots/layers |
| **CPU cores** (8C/16T Zen 3, AVX2) | expert matmuls for every CPU-side layer, per token in the pass | 37–48% total; 8 threads best (12/16 lost) | repack (IQ4_NL, Q2_K), cache (fewer reads), fewer CPU-side layers |
| **DDR4 bandwidth** (1:1 3800 / FCLK 1900 since 2026-10-01; fabric caps a single CCD at ~61 GB/s) | streaming ~0.6 GB of experts per pass | the half of the CPU time cores can't fix | fewer bytes touched: cache, smaller CPU-side quants, RAM 1:1 (BIOS, pending) |
| **RAM capacity** (32 GB) | CPU-side experts ~1.05 GB/layer (q2k; 0.77 q2kd2) · page cache (mmap'd experts, the 27 GB embedding table under lazy mode) · Windows ~3–4 GB | 0.1–3 GB free | mmap, fitting cooks, cache, keeping N ≤ 34 |
| **Pagefile drive C:** (T-FORCE NVMe, also Windows) | anonymous memory past RAM: `-lm none` experts, repacked experts — written out, read back | **first samples: 100% busy, queue 5–13, up to 10 ms/read** (load-time; confirm in generation) | keep paging-prone data file-backed (mmap, unrepacked); a pagefile on a fast non-OS drive (the owner's setting) |
| **Model drive F:** (PNY Gen4 NVMe) | mmap'd expert page-ins, the lazy embedding table | idle 99.9% in those samples; **930 MB/s at queue 27** when asked deep | fault-ahead prefetch (backlog), mmap |
| **PCIe** | activations at every CPU↔GPU hop (66/token), cache uploads, prefill op-offload | unmeasured until the Copy-engine counter (in TREE since `f548dd0`) | fewer hops is NOT a win (whole-layer split lost); bigger `-ub` for prefill |
| **per-pass work** (the multiplier) | the UNION of experts touched by every token in a pass: MTP drafts, parallel requests, varied prompts | why MTP is +45% warm but +10% varied; batching flat | cache and repack shrink it; more tokens per pass grow it |
| **engine caches** | expert cache slots (hit rate), MTP acceptance, prompt/KV cache | hit 43% at 32 slots, acceptance 0.62–0.94 by task | slot count, inserts, warm start, prefix cache |

**Interaction measured 13:38 — repack × mmap fight:** with Q2_K repack too, all 31.2 GB of CPU-side experts are
copied out of the file into anonymous `CPU_REPACK` memory, so under RAM pressure they page through the pagefile in
4 KB faults instead of mmap's file read-ahead. Varied prompts on mmap: **8.9, 9.2, 10.2, 11.1** (first four) vs
IQ4-only repack's 8.9, 13.0, 14.2, 13.7 (fifth prompt 11.3 vs 12.7). Warm (resident) the full repack is level with
the same-day reference (27.5 vs 28.35); prefill 39 / 97 vs the reference's 42.5 / 110.9. Rule: repack only what stays resident; whatever must page should stay file-backed — or
shrink what is touched (the cache).
**Fusion research (13:40, verified via `gh api` + the thread's raw JSON):** hyper-connection fusion (PR #28901, ops
`DSV4_HC_*`, Vulkan shaders present) is already in b11160 and worth +2–4% by its own numbers. The 5090's 47–62 tok/s
is mainly open PR **#28213** (gather-based sparse attention for QSA decode, existing ops only → Vulkan-portable;
+6% at 31k, +19% at 62k, +50% at 130k context) plus a CUDA-only 4-head indexer kernel. A long-context lever for
serving, invisible in 8k benchmarks; port onto the mix build when long-context serving is the target.

So each step below is tested as a whole configuration read through full telemetry, and a winner is re-checked
against the levers it changed the budget for (e.g. full repack changes CPU time per layer, which moves the best
N and the best slot count; a bigger `-ub` costs slots).

**⚠ The collapse caught again (14:30, the owner: "reaching higher… closer to 30 to 50… you or one of the other agents
tells me that we've forgotten some of the levers or they've collapsed the toolset"):** the synthesis named "place by
HOTNESS, not by layer" as the structural waste, and then the cache was only ever tested as a 2 GB add-on (32 slots)
bolted onto LAYER placement — never as the placement itself. The full form: ALL 48 expert layers CPU-side and
file-backed (mmap, `--no-repack`, so nothing becomes anonymous memory), and the ~17 GB of VRAM that held layers
32–47 (cold experts included) handed to cache slots across every layer (~160/layer ≈ a third of each layer's
experts; measured skew: 9% of experts served 58% of reads; the PR's RX 6950 hit ~85% at 128 slots). Theory: at
~80% hits the CPU reads ~a fifth of today's bytes (C ≈ 28 → ~9 ms/pass) and the idle GPU does the rest →
~40+ tok/s warm with MTP. Order `hot48` (160 / 128 slots warm, 128 varied), placed with
`known_bad_ok` on each arm (the gate's N ≤ 34 rule assumes the CPU computes every CPU-side expert).

**Build order (each step keeps the winners of the last; updated 15:05):** (1) ✅ cache + full repack + MTP in one
engine (three patches; warm level, varied loses — the pagefile drive); (2) ✅ placement by hotness measured: N=48 /
160 slots hit 82% but 7.7 tok/s, N=40 / 88 slots 19.7 / 16.6 — the cache is ~4× more efficient per GB than whole
layers, but its MISSES are expensive (shallow paging on F:, plain kernels, refill uploads); (3) **make misses cheap:
fault-ahead** (`patches\moe_fault_ahead.py`, batched `PrefetchVirtualMemory`, env-gated; build staged, held at
the owner's interrupt 15:00), then the pagefile drive (the owner's setting); (4) a prefill pack (`-ub` 2048/4096 × `-lzm`);
(5) cache sizing (q4_0 KV, inserts); (6) re-open batching on the winner; (7) served config. The GPU clock is NOT a
step: it is a starvation gauge (the owner 15:00) — the card ramps when it is fed.

## Synthesis — how the levers interact (2026-10-01 12:45, the owner: "have we been looking at each one in a vacuum?")
Mostly yes: levers were stacked when they won on the warm prompt (Vulkan + repack + MTP + `-np 1` + `-ub 512`), but
each was judged alone, mostly on the warm prompt, and the conflicts surfaced late. The mechanical model that fits
every measurement so far — per forward pass:
- **G, GPU-side** ~38 ms, node-count bound at ~1,350 MHz. Amortised by every extra token the pass serves (MTP
  ~2.6/pass; concurrency N). Shrunk by clock (untested).
- **C, CPU-side experts** ~28 ms at N=32: bytes of active CPU-side experts ÷ DDR4 bandwidth × kernel efficiency.
  Repack lowers it ~10%; an unrepackable cook raises it a lot (q2kd2: warm 28.4 → 14.1). It GROWS with tokens per
  pass, because a pass touches the union of every token's experts — why draft 3 lost to draft 2.
- **P, paging**: appears whenever the experts touched exceed free RAM. Varied prompts grow the touched set; so does
  every lever that adds tokens per pass.

**The resource picture:** RAM capacity and DDR4 bandwidth are scarce; GPU compute is idle (13–25% load, ~80 W).
Every measured loss moved work toward the CPU (whole-layer split, Q2_0's plain kernels, 16 threads). Every win moved
work to the GPU or spread one CPU read over more tokens (MTP, Vulkan, the cache).

**Measured conflicts (not additive):** MTP × paging (+45% warm, +10% varied: more tokens per pass, bigger union).
Cache × repack (garbage until fixed). Fitting cook × repack (Q2_0 trades P for C). Cache slots × RAM (slots take
VRAM, push N up, push RAM over). **Predicted:** concurrency × paging (each extra request adds its own experts — why
the concurrency order runs the no-paging cook first).

**The structural waste this exposes:** `--n-cpu-moe` places by LAYER, so VRAM holds every expert of layers 32–47,
cold ones included, while the hot experts of layers 0–31 sit in RAM. The cache measurement says expert use is
skewed: **48 slots (9% of a layer's 512 experts) served 58% of reads.** Placing by HOTNESS across all layers would
put most expert reads on the idle GPU, shrink C, and leave RAM holding only the warm set (cold experts page in
rarely). The cache PR is the dynamic form of that; making it work with repack (research pass running) and spending
VRAM on slots rather than whole layers is the lever that answers "double the VRAM isn't getting us further."
**Best stack to aim at:** repack + cache sized from VRAM + MTP + concurrency for batch work (the clock follows the
load — the owner 15:00; it is not a separate lever) —
each attacks a different term (C, C, G, G and C-per-token, G).

## Known-bad gate: `plan_lint.py` (every ladder plan passes it before placement)
`workspace\flashnext\plan_lint.py <plan.json>` refuses any arm that re-runs a closed regime from the ledger below —
RAM cliff per cook (q2k N>34, q2kd2 N>33), threads ≠ 8, draft > 2, whole-layer `-ngl` splits, concurrency without
the cache, cache + repack on an unpatched engine, q2kd2 on mmap, the Q8 head, the no-SIMD ROCm zip — each with the
measurement that closed it. the owner 2026-10-01 13:25: *"when are we going to stop sending in known bad configs and
regimes??"* (a 48-slot arm at N=35 walked into a cliff already on file). Proven against five past bad plans (all
refused with evidence) and one clean plan (passed). A ledger verdict that closes a regime adds its rule in the same
pass; a deliberate re-test after the regime changes (RAM, engine, cook) sets `"known_bad_ok": "<what changed>"`.
Rule of thumb it encodes: anything that costs VRAM must come from FEWER CPU-side layers, never more.
**The anti-collapse half (2026-10-01 14:45, `workspace\flashnext\arsenal.json` + the same gate):** the owner: *"what are
you going to do to stop yourself from collapsing things even though that's like 80% of what I've been talking to
you about today."* A promise is not a mechanism; the gate is. Every lever lives in `arsenal.json` with its FULL
form, the forms actually tested, and a status (open · testing · partial · done · closed · needs-owner). A lever
counts as tried only when its full form has run — the expert cache tested as a 2 GB add-on is `cache_addon` (done)
and `hotness_placement` (the full form) is its own lever. Every plan must carry `"levers": [ids]`; the gate refuses
a plan that names none or names an id the ledger lacks, and EVERY placement prints the levers whose full form has
not run — so the forgotten ones are in front of whoever submits work, every time. Update the ledger in the same
pass a result lands. **Every lever also names the resources it acts on** (gpu_compute · gpu_sched · vram · cpu ·
ddr4 · ram · drive_pagefile · drive_model · pcie · engine), and the gate prints whole-box coverage on every
placement (the owner, 14:50: *"remind you [not to count only] on VRAM and RAM while you're building this out"*). The
first coverage pass found levers on GPU scheduling, CPU placement, memory pages and the PCIe link missing entirely,
now added: HAGS on/off, the AMD compute workload profile, Vulkan env knobs, pinning threads to physical cores,
Windows large pages, verifying the PCIe link, and model-drive choice (36 levers, 22 with full form untried).

## Lever ledger (2026-09-30 23:55 — every lever named today, with its status in the CURRENT regime)
**Best measured:** q2k on Unsloth b11160 **Vulkan** (`--no-host -np 1`) + MTP draft 2, N=32 → **28.4 / 26.4 tok/s**
gen (`backend-vk5`; the N=33 four-slot form reproduced at 27.1 / 25.7); without MTP 19.0 (N=30). The ollama-bundle ROCm engine's 15.3 (N=28) keeps the **prefill** crown
(174 at N=32–34 vs Vulkan's 76–93). The regime changed twice tonight (engine, then MTP), so verdicts taken on the old
engine are marked ↻ = re-measured or queued on the new one. the owner, 23:50: *"make sure you're looking into the full
array of levers and knobs."*

| lever | status | evidence |
|---|---|---|
| **MTP draft head** (shared-Q4_K_M) | ✅ **THE lever**: +45% at N=36, 27 tok/s at N=33 | `vk2`, `vk4`; acceptance 79–81% |
| draft n-max 2 vs 3 | ✅ settled: 2 | `vk4` VM3: even on the best run, behind on the other |
| **Vulkan backend** (Unsloth b11160) | ✅ +9% at equal N over the bundle ROCm, before repack | `vk3` V-norepack 14.6 vs R0 13.4 |
| **engine CPU repack** (newer commit) | ✅ ~+10% on top | `vk3` V-norepack vs `vk2` V0 |
| explicit expert placement (`--n-cpu-moe`) | ✅ settled: lowest N that fits; ~1 ms/token per CPU-side layer, paging past N≈30 | sweep + `backend-fix` profiles |
| flash attention + q8_0 KV + `-ub 256` | ✅ keep (VRAM; no generation cost) | ladder-b |
| `-np 1` (one slot: RS cache 1.35 → ~0.34 GB → one more layer) | ✅ +4%: 28.4 / 26.4 at N=32, same VRAM | `vk5` VM2-N32-np1 |
| reproducibility of 27 tok/s | ✅ 27.1 / 25.7 vs 27.2 / 25.7 | `vk5` VM2-N33-repeat |
| threads 12 | ❌ no gain: 26.5 / 24.7 vs 27.1 / 25.7 at 8 (cpu 59% vs 41% busy for fewer tokens) | `vk6` VM2-N33-t12 |
| **GPU clock during generation — a GAUGE, not a lever** | ❌ **closed as a lever 15:00.** Telemetry reads 600–1,350 MHz, 75–93 W, load 9–35% through every arm today (memory clock at max). Earlier this row proposed a minimum-clock floor; the owner corrected the framing: *"It really shouldn't matter what its minimum clock speed is, it's supposed to ramp itself up just like it does for every other job or game I play or model running."* Right: DPM clocks the card down because it is IDLE-WAITING on the CPU and the drives, and a floor would only burn power while it waits. Read the clock (and load) as the starvation gauge: a config that feeds the card will show it ramping on its own. `clock_floor` and `amd_compute_profile` are closed in `arsenal.json` | `gpu_telemetry.csv`, TREE lines |
| **hop count: whole layers on the CPU** (`-ngl K` + `-ot output\.weight=Vulkan0`) instead of `--n-cpu-moe` | ❌ **closed 12:37.** `--n-cpu-moe 32` crosses CPU↔GPU 66 times per token; whole-layer split (`-ngl 17`, layers 0–30 entirely on the CPU, output on the card) crosses twice — and read **6.5 / 12.5 / 12.2 tok/s** vs 28.4, prefill 26 / 65, gpu load 13% at 79 W. Running 31 layers' attention/DeltaNet on the CPU costs far more than the hops it removes; the hops are not the GPU-side 38 ms. Remaining arms (−ngl 16, reference) stopped by slip | `fullsplit` |
| **cache × repack — OUR PATCH** (`workspace\flashnext\patches\moecache_unrepack.py`, mirror `6682ea4`) | ⏳ **built 2026-10-01 ~12:55, under test.** Root cause confirmed in source (research pass + spot check): `upload_slice()` in `src/llama-moecache.cpp` copies an expert's bytes raw from `src->data`; ggml-cpu repack (`repack_iq4_nl_to_iq4_nl_8_bl` → `make_block_iq4_nlx8`, AVX2) has rewritten IQ4_NL experts IN PLACE into 8-row interleaved `block_iq4_nlx8`, no original kept; the device slot is plain IQ4_NL → garbage. The interleave stays inside each expert (`ne[1] % 8 == 0`) and is a pure byte shuffle, so the patch de-interleaves on upload (18-byte `block_iq4_nl` ← 144-byte x8 block; round-trip test bit-exact). Second defect fixed in the same patch: the layer gate checked only `ffn_up_exps`'s buffer, so a repacked `down` slipped through — it now checks up, gate and down and skips any layer in a layout it cannot upload. `build_mix.py --patch` applies it as its own commit. Not reported upstream (the owner). ✅ **WORKS (13:12, engine `llama-mix-9b19d2d00`):** repack ON (15.3 GB repacked) + 32 slots + MTP at N=34 writes coherent text (sample: a clean MoE explainer), acceptance 81.5% (same as the coherent runs; the broken build's slashes accept ~100%). Warm gen **26.4, 24.4, 25.1, 27.4, 27.8** (cold 7.7) — about level with 28.4 while carrying TWO more CPU-side layers; hit rate 43.2% at 32 slots; prefill 18 / 57 (worse than 34 / 80–89); gpu 35% at 93 W. 48 slots at N=35 read **4.1 tok/s** on its first warm generation (hit rate 50.8% — but the RAM cliff of 35 CPU-side layers) and was stopped by slip with the droppable control; VRAM for slots must come from fewer CPU layers, never more | `flashnext-build-mixp`, `cache-repack` |
| **concurrency** (N requests at once, `-np 4`, MTP, five varied prompts cycled; aggregate = all tokens ÷ wall) | ❌ **no gain while experts are on the CPU (13:02).** q2kd2 N=33 (no paging): aggregate **13.5 → 12.7 → 12.2** at 1 / 2 / 4 requests (per request 14.2 → 7.8, 7.1 → 3.3 each). q2k N=33 mmap: **9.8 → 13.7 → 10.0** (4 requests: 60k pages-in/s, 239 MB/s, RAM free 0.11 GB). Per-pass CPU cost scales with the UNION of every request's experts, so batching different prompts adds CPU work as fast as it adds tokens; GPU load only rose to ~31%. Batching pays only after expert reads move off the CPU (the cache) — re-test then | `concurrency` |
| **pre-decode: Q2_K repack on AVX2** (`patches\q2k_repack_avx2.py`, mirror `2dfd85c`) | ⚖ **MEASURED 13:30–13:45 (engine `llama-mix-e541f68a4`): a split verdict.** Repacked at load **31.2 GB (vs 14.4), 0.5 GB left plain.** Warm (resident) N=32 + MTP: **24.7 → 27.5** (2 warm gens, still climbing), prefill **39 / 97**; the same-day reference in the same order (Unsloth engine, IQ4-only repack) read **28.35 / 26.19, prefill 42.5 / 110.9** — so warm is level, prefill slightly lower. Varied prompts on mmap: **8.9, 9.2, 10.2, 11.1, 11.3** vs IQ4-only repack's 8.9–14.2 (prefill 47 / 122): **loses** — repacked experts are anonymous memory, so under RAM pressure they go through the pagefile instead of mmap's file read-ahead (the repack × mmap row in the arsenal section). Keep for resident sets; never for paging-prone ones. History: the owner 13:15 asked whether the render side's pre-baked conditioning (Mistral encoded once into `.cond` files so no encoder runs in flight — `P20-FOUNDRY-RUNNER-PLAN`, `Flux2-32B-QLoRA…` §resume) has a counterpart here. Repack IS that shape: decode-once-at-load into the CPU's fast layout. In this engine `ggml_repack_get_optimal_repack_type()` enables Q2_K repack only when `ggml_cpu_has_avx512()`, yet `arch/x86/repack.cpp` has AVX2 bodies for both kernels (`ggml_gemv_q2_K_8x8_q8_K` under `#if defined(__AVX2__)`; `ggml_gemm_q2_K_8x8_q8_K` under `__AVX2__ \|\| __AVX512F__` with an AVX-512BW/DQ block and an AVX2 block after it). So ~17–20 GB of Q2_K gate/up experts run the plain kernel every token. One-line selector patch; test order `q2k-repack` (warm, varied, 28.4 reference). ⚠ Conflict to resolve if it wins: the cache patch only de-interleaves IQ4_NL, so repacked Q2_K layers are skipped by the cache until a `block_q2_Kx8` inverse is written. Same-shape levers still open: a saved hot-expert profile to pre-fill cache slots at start (the cold climb is 7.7 → 27 over six generations), and prompt-prefix KV caching for served use (`cache_prompt`, `--slot-save-path`; prefill is 57–89 tok/s, so a 4k system prompt costs ~45–70 s per request uncached) | `flashnext-build-mixq` |
| **cache × Q2_K repack — the second interaction** (`patches\moecache_repack_path.py`, mirror `9a9ed0e`) | ⏳ **found 14:30, rebuild `flashnext-build-mixs` queued.** `stack1` on `llama-mix-e52e62f8b` (cache + full repack + MTP): coherent, warm **24.0, 26.3, 25.7, 24.1, 26.8** (6 gens) — but its full telemetry said **cache hit rate 0.0%**, and the server log read `hits=0 misses=0`: the cache initialised on all 34 layers and never saw a routing decision. Cause in source: the cache PR hooks ggml-cpu.c's `ggml_compute_forward_mul_mat_id` twice — it reads the skip table in `dst->src[3]` (zeroes rows the cache serves) and reports `ffn_gate_exps` routing to the observer (`ggml_get_moe_obs_callback`). A tensor in a CPU_REPACK buffer runs repack.cpp's `tensor_traits::forward_mul_mat_id` instead, which does neither; once Q2_K repacks, the GATE tensor takes that path, so the cache goes deaf. (The IQ4-only build worked because gate/up stayed on the standard path; its repacked down ignored the skip table harmlessly — its inputs for cached experts were already zero.) The patch copies both behaviours into the repack path. stack1's varied arm stopped by slip (cache-off full repack on varied = the 13:38 loss). `plan_lint.py` now lists only engines whose cache WORKS with repack — this one would have been caught by a "hit rate > 0" check, so the TREE line is now the instrument that proves a cache arm is a cache arm | `stack1` |
| threads 16 | ❌ **closed 11:45 on the real engine too**: q2k, mmap, MTP, N=32, five varied prompts **6.81, 8.69, 10.14, 10.19, 9.36** vs 8 threads' 8.88–14.24 (−25%); prefill 61 / 87 vs the 8-thread run's; cpu 72%, pages-in 53k/s at 212 MB/s. More threads do not fault more pages in parallel — the SMT siblings contend for the same cores and the fault rate actually fell. 8 threads stays (earlier: dead under ollama; 0.23 tok/s on the scalar engine) | `validate-mmap-threads` |
| `-ub 512` for prefill under Vulkan | ✅ **adopt for serving**: gen 27.6 / 25.7 (unchanged), prefill 29.2 / **88.9** vs 78 at `-ub 256` (+11%); VRAM 22.2 GB | `vk6` VM2-N33-ub512 |
| CPU-side experts from the LAST layers instead of the first | ≈ inconclusive on gen (26.5 / **28.7** — the single best run seen, sibling within spread), prefill clearly worse (55.9); lower paging (16k vs 19k pages-in/s, RAM free min 4.4 vs 3.2 GB). Stays first-N | `vk6` VM2-last33 (`-ot "blk\.(1[5-9]\|[2-4][0-9])\.ffn_.*_exps=CPU"`) |
| `--prio 2 --poll 100` | ❌ no change on the bundle; not re-tested (low prior) | ladder-b |
| graph capture (`GGML_CUDA_DISABLE_GRAPHS`) | ❌ engages, worth nothing | `backend-fix` |
| graph splits / unsupported ops | ✅ ruled out: splits = 2N+2 exactly | `backend-fix` R0 `-v` |
| memory compression | ✅ already off, 0.11 core | measured 20:00 |
| paging | ⚠ **the wall for varied prompts.** 28–60k pages-in/s at 113–241 MB/s, RAM free ≤0.5 GB — the CPU-side set (32–36 GB) exceeds RAM. ⚠ The MB/s is NOT the NVMe's limit (3+ GB/s sequential): demand paging fetches **one 4 KB page per synchronous fault**, so throughput = fault latency × concurrency (~33k faults/s ≈ 130 MB/s), and the drive sits at a queue depth of a few. mmap doubled it through readahead. Hence the two routes: fault bigger (mmap ✅), fault more in parallel (❌ 16 threads under mmap lost 25%, `validate-mmap-threads` 11:45 — the threads row), or **stop faulting** (a RAM-resident set: the Q2_0-down cook ✅ — the q2kd2 row: 4.6k pages-in/s, 19 MB/s) | profiles; the owner 10:05 *"I thought those NVMEs are faster than 130MB/s"* |
| **Defender exclusions** (fleet standard since 2026-10-01, [[RUNBOOK-GPU-Host-Power-and-Glances]]) | ✅ applied on all three GPU boxes 00:28–00:40 (the owner: "per our usual doctrine for models" / "cross cut city"); see the row below for the measured effect | `Get-MpPreference` read-backs |
| WSearch / SysMain | ✅ stopped by frontier mode during every arm | |
| `GGML_CUDA_ENABLE_UNIFIED_MEMORY` | ✅ unset (moot under Vulkan) | |
| RAM 4200 2:1 → 3800/1900 1:1 | ✅ **done 2026-10-01 (the owner, BIOS):** warm 28.1 → **30.1–30.2** (n=2, acceptance level); varied median 13.0 → 17.7 but confounded by a fresh boot (paging fell 21%) — uptime re-run pending; 0 WHEA 19 under load | "RAM at 1:1", `flashnext-ram-after` |
| smaller cook (iq2xxs) | ❌ lost on the bundle (decode cost > size); on the new engine IQ2_XXS is not repackable, so likely worse again — not re-tested | h2h, ladder-c |
| a ~3-bit cook from the master | ⏸ quality lever, costs VRAM/RAM; after speed settles | |
| shared-Q8_0 MTP head (2.8 GB, downloaded + sha-verified 00:29) | ❌ no gain: 25.7 / 26.7 vs the Q4 head's 27.8 / 25.8 at the same N=33; costs ~1 GB more VRAM. **Q4_K_M head stays** | `vk7` Q8-N33-np1 vs Q4-N33-np1-ctl |
| Defender exclusions (Mothership Flash-Next folders, applied 00:28) | ✅ done; **no measurable effect**: reference 28.3 / 25.6 vs 28.4 / 26.4 before, load 115 s vs 111–113 | `vk7` Q4-N32-np1-ref |
| `-lm mmap` vs `none` | ↻ **REVERSED for varied prompts (09:11):** `validate-mmap` MTP-t0-mmap-5p read **8.88, 12.98, 14.24, 13.70, 12.69** on the five prompts vs 7.68–10.85 with `-lm none` — **+30–40%**; profile pages-in 60k/s, **disk 241 MB/s** (vs 113), RAM free min 0.25 GB. File-backed experts page in as contiguous NVMe reads with readahead instead of 4 KB pagefile faults. The old verdict (−30% gen) was a warm single-prompt measurement through ollama where nothing paged. **For varied work on 32 GB: mmap.** 10-prompt arm (09:22): 8.44, 11.38, 12.73, 12.82, 11.96, 13.25, 13.42, 13.11, 14.79, 12.58 — **plateau ~12–14 tok/s**, still 59k pages-in/s at 235 MB/s: mmap raises the paging floor, it does not remove it. Acceptance across the ten: 0.93, 0.65, 0.91, 0.85, 0.53, 0.68, 0.79, 0.75, 0.73, 0.76 | `validate-mmap` |
| `num_batch` / `-b` | ✅ settled on the bundle: default for generation; only `-ub` matters for prefill | synthesis |
| **GPU hot-expert cache** (PR #27861, our Vulkan build `llama-moecache-bccbacdb8`) | ⚠ **BUILT and measured 01:30–01:55 — the cache arms emit GARBAGE.** `cache-ladder`: C0-N30 (no cache) 16.3 / 12.2 tok/s, coherent text; **C48-N34 29.4 / 29.7 / 29.6, prefill 172 / 187, gpu 65% 197 W, pages-in 653/s — output `////////…`**; C96-N38 28.5 ×3, prefill 158 / 170 — same garbage; C48-N30 spilled 1.3 GB, skipped. **Those speeds are void** (a decode emitting slashes touches almost no experts, which is exactly why it is fast and why RAM free read 11 GB). Hypothesis under test (`cache-ladder2`): this build's CPU backend REPACKS host-side experts (`REPACK = 1`) and the cache uploads the repacked bytes into GPU slots the kernel reads as plain quant layout — one variable, `--no-repack`, cache on vs off at N=34. The ladder's new per-arm text sample (`77b4458`) is what caught it; without it 29.6 would have been "the number". **`cache-ladder2` (02:17, `--no-repack`): hypothesis CONFIRMED — the cache arm writes coherent text with repack off.** Speed at N=34, both arms still climbing on their third run: cache 9.5 / 10.1 / **17.8** vs no cache 8.4 / 10.6 / **16.0** (prefill 65 vs 62; VRAM 21.6 vs 18.5; RAM free min 0.9 / 1.3 GB, pages-in 17–21k/s). The cache is real; its gain here is unproven (n=1, warm-up not reached) and this upstream build runs ~15–20% below Unsloth's Vulkan at equal N without it. **Its own log explains the small gain: `moe-cache: steps=1536 hits=303172 misses=222468 hit-rate=57.7%`** (48 slots/layer on 512-expert layers; the PR's 85–93% came with 128–280 slots) — ~43% of expert reads still hit the CPU while the cache costs ~4 layers of VRAM. **`mix-ladder2` (09:30–, the mix build `llama-mix-c7c6b40da`: MTP + cache on one engine, `--no-repack`, `-lm none`,
five varied prompts):** M-N36 (MTP alone) **4.13, 5.75, 5.06, 4.87, 5.52**; MC24-N36 (MTP + 24 slots) **6.18, 6.47,
6.91, 5.74, 6.32** (prefill 24.8 / 34.0, VRAM 20.4 GB, pages-in 33k/s) — **+15–25% in the paging regime**: an
expert served from the VRAM cache is an expert that never pages in. MC48-N38: ~4.4 tok/s on its first prompt (RAM
cliff at N=38, as expected) — **stopped by slip at 10:24 on the owner's call** (*"call it a not good config and keep
going"*); its verdict was already known from `cache-ladder3`'s C96-N38. Both arms sit far below the Unsloth engine's mmap numbers (8.9–14.2) because this
ladder ran `-lm none` at N=36 without repack; the cache's value is the within-order delta, not these absolutes.
**`cache-ladder3` (02:20–02:59, six generations per arm, no-repack): 48 slots at N=34 → 9.1, 15.0, 17.1, 16.7, 20.1, 22.7 — still climbing as the LRU warms, +31% over the no-cache control (8.1, 13.5, 16.1, 17.3, 17.0, 17.3, plateaued) by the sixth generation — the PR's own +31%.** 96 slots at N=38: 7.5 → 17.4 → 11.7, RAM free min 0.11 GB — a paging cliff (38 CPU-side layers unrepacked exceed 32 GB), not a cache result. All coherent. **Verdict: the cache is real and worth ~+30% on this build at 48 slots; but this build without it (17.3 at N=34) is below Unsloth's repacked Vulkan, and MTP alone is +45% there. The prize is cache + repack + MTP together, which needs (a) the PR fixed to upload the unrepacked layout (the garbage defect) and (b) Unsloth's "mix" recipe on top — a day's project.** The repack defect is worth reporting upstream (the owner's call) | `cache-ladder`, `cache-ladder2`, `cache-ladder3`, build order `flashnext-build-moecache4` |
| Vulkan vs ROCm at the SAME commit | ⏸ unresolved (Unsloth's ROCm zip has no SIMD; cross-compiler DLL mixing failed). Only matters if the cache is built for ROCm | |
| lemonade-sdk ROCm nightlies (mainline, gfx1100) | ⏸ would give a modern ROCm+AVX2 engine without MTP; not needed while Vulkan+MTP leads | research doc |
| More RAM (32 → **96 GB**) | 🛒 **bought 2026-10-09, install pending**: 2 × 32 GB G.Skill Ripjaws V DDR4-3600 CL18 added beside the 2 × 16 ([[Network-Homelab-Machine-Inventory-Virtualization]] §"RAM / DIMM inventory"). Expected, **unmeasured**: q2k (82.3 GB) becomes fully resident in RAM + VRAM, so varied prompts should stop paging and move toward the warm-repeated rate; IQ3-class cooks (Unsloth recommends 96 GB) and MTP at higher N stop hitting the RAM cliff. Cost: four DIMMs likely run ~3600 / 1800 instead of 3800 / 1900. **Measure with a before/after order** using the same five prompts and flags as `flashnext-ram-after` | inventory doc |
| exllamav3-rocm-moe | ❌ out: Linux + AVX-512 (silicon). WSL2 ROCm would cover the Linux half only | research doc |
| Strata / moe-autopilot | moe-autopilot NVIDIA-only (idea only). **Strata on Mothership MEASURED 2026-10-04** (Windows HIP v0.1.39, own Q2_0, **low-RAM mode** because 32 GB is below its bar): varied ~2.3–2.7× llama.cpp, prefill ~5× (§"Strata", top of page; quality unmeasured). Strata's own page (read 2026-10-09) sets standard mode at **48 GB+ RAM for Q2_0 and 64 GB for IQ3_XXS**, and lists an RX 7900 XTX owner at 64.5 tok/s at 200K context. Mothership's RAM upgrade (bought 2026-10-09) clears both bars, so **standard mode is the next Strata arm after install** | research doc; backlog "STRATA FOLLOW-UPS" |
| **the Q2_0-down cook (q2kd2) — RESULT 11:33** | ✅ **the no-purchase lever works.** Five varied prompts, MTP, N=32: `-lm none` **16.09, 10.77, 15.23, 14.73, 12.41** (prefill **117.6 / 158.5**, pages-in 4.6k/s at 19 MB/s, cpu 51%, gpu 27%) vs q2k's best 8.9–14.2 (mmap) / 7.7–10.9 (none): first prompt fast (no warm-up climb), paging down ~6×, prefill 2.5×; mmap on this cook 5.9–14.7 (page cache competes for the same RAM — process memory wins once the set fits). Spread is acceptance-shaped (code 0.94 → 16, explanation 0.62 → 11). Outputs coherent. **Quality unverified beyond coherence.** ⚠ **12:10 `q2kd2-push` — the cook's ceiling is the KERNEL, not RAM:** warm repeated MoE prompt (Unsloth Vulkan, MTP, `-lm none`, N=32) **13.8 / 14.1 / 14.1** vs q2k's 28.4 on the same prompt and config (acceptance 0.76 vs ~0.80). Cause in the load logs: q2k repacks 14.4 GB of CPU-side tensors (the IQ4_NL down) into the fast kernel layout; q2kd2 repacks **0 bytes** — Q2_0 has no repack path, and Q2_K (gate/up, 17.3 GB plain on both cooks) does not repack in this build either. So q2kd2 runs ~14–16 flat whether the prompt is warm or new, and q2k runs 28 warm / ~13 paging. On varied work q2kd2 `-lm none` is still the best measured (median ~14.7 vs q2k mmap ~12.7). **An all-Q2_0 cook is closed** (more unrepacked tensors). Expert cache on this cook (mix build, N=34, mmap, 24 slots): **5.6, 8.6, 13.5, 12.5, 10.2** vs its MTP-only control 5.4, 8.5, 12.9, 11.5, 10.3 — **+0–9%, within spread**; both pay paging again at N=34 (20k pages-in/s). Repackable types in this engine are 4-bit and up (IQ4_NL, Q4_0, Q4_K, Q8_0), so no smaller repackable down exists | `q2kd2-ladder`, `q2kd2-push` |
| ffn_down below IQ4_NL | ↻ **REOPENED 09:05** — the Unsloth quantizer has **`Q2_0` (2.25 bpw, group 64)**, which fits the 640 width (the floor was for 256-block types). `ffn_down` is 16.2 GB of the ~32 GB CPU-side set at IQ4_NL; at Q2_0 on the CPU-side layers only (0–31) it is ~8 GB → **CPU-side ~24 GB at N=32 = RAM-resident on 32 GB**, the one no-purchase answer to the varied-prompt paging. GPU-side layers keep IQ4_NL (no new Vulkan kernels). Cook order `flashnext-quant-q2kd2` (recipe = q2k + `blk\.([0-9]\|[12][0-9]\|3[01])\.ffn_down_exps=q2_0` first, Unsloth's quantizer, F: master → D:, ~70 min) — queued after the mix ladder. **Quality unknown** (Strata runs whole models at Q2_0; ours would put only `down` there); the coding families decide | quantizer `--help` on `llama-unsloth-b11160-vulkan` |
| serving it (ollama cannot run this engine/MTP) | ⏸ integration: HAI would need a llama-server route — backlog "SERVE FLASH-NEXT THROUGH HAI" (the owner's decisions) | |
| **output quality / determinism** (checked 00:30 from the verbose logs' generated text) | ✅ every arm coherent, same structure; MTP text starts identical to plain Vulkan (lossless verification). ⚠ **Vulkan is NOT run-to-run deterministic at temperature 0** (same prompt: 1685 / 1624 / 1623 chars, even without MTP); the ROCm bundle is byte-identical. Matters for anything that assumes temp-0 repeatability. Candidate causes to test: `GGML_VK_DISABLE_COOPMAT=1`, `GGML_VK_DISABLE_FUSION=1` | `text_compare.py` on `backend-fix` R0, `vk2` V0, `vk5` VM2-N32-np1 |
- Still open, not speed: quality head-to-head of q2mix / q2k / iq2xxs (the coding families); a warm prefill
  measurement through ollama.
- ⚠ **A cook MEMORY-MAPS the master; a remount of its volume mid-cook kills it.** 2026-09-30 11:46: the Xbox app
  moving games onto B: (a USB-attached drive) remounted the volume (NTFS event 98 "Volume B: is healthy" at 11:46:26)
  and 13 s later the iq2xxs cook died at tensor 1106/1224 with rc 3221225478 = `0xC0000006 STATUS_IN_PAGE_ERROR`.
  Likely USB/Windows-specific (the owner: an internal SATA mount would probably have ridden it out). **A partial cook
  cannot be resumed:** `llama-quantize` writes a zeroed header placeholder, streams tensors, and only writes the real
  header at the end (`src/llama-quant.cpp`: `::zeros(fout, meta_size)` … `fout.seekp(0)`), and has no resume
  option — re-cook from scratch.
- **Keep the 354 GB master** — it saves the 4-hour conversion on every re-cook. The permanent copy is on B: (USB HDD);
  **cooks read the working copy on F:** (staged 2026-09-30 with robocopy, 39.5 min at 149 MB/s average — the HDD slows
  from ~250 to ~120 MB/s toward its inner tracks). Output never goes to B: (the owner: *"Don't cook onto b"*); finished
  cooks are copied to B: one at a time for safekeeping (still to do for q2k and iq2xxs).
- **Quantizing on the GPU is not a lever** (research 2026-09-30, 2026-09-30-claude): llama.cpp's
  quantize path is CPU-only in source (`quantize.cpp` takes only a thread count; `ggml-quants.c` has no CUDA/HIP
  packing), the only GPU-capable step is imatrix calibration (we reuse Unsloth's), ik_llama.cpp does not target ROCm,
  and the one gfx1100 EXL3 fork marks conversion untested (and EXL3 is not GGUF).
- Softserve (48 GB RAM + 3070 Ti) matches Strata's Q2_0 minimum — parked by the owner ("another day"; Plex host).
