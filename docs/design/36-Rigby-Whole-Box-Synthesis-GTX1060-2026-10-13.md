# #36 — Rigby whole-box synthesis after the GTX 1060: what binds reading and writing, what the lab and the community already know, and the levers left (2026-10-13)

Child of [[36-Split-Inference-Helpers-Read-and-Draft-Design]] and campaign #34 ([[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]]
rows 10-13 14:3x onward). Written at the owner's request: *"take a step back and synthesize from the data we have collected
over the past month, this project, campaign 34, etc… review all the research from external sources… look into online
research about people using multiple cards and of varying powers"* and *"we also have plenty of information on rigby's
a3b"*. The frame (his): *"we want every drop out of these machines"*. Helpers are judged by what they ADD to the 1060 +
CPU, never against the 1060 alone ([[36-Split-Inference-Helpers-Read-and-Draft-Design]] working rule 10).

Certainty tags: **[M]** measured on rigby · **[L]** measured elsewhere in the lab · **[D]** stated by an external
source · **[I]** inferred by arithmetic · **[G]** a guess. Model throughout: Qwen3.6-35B-A3B-UD-Q3_K_XL (16 GB file,
`qwen35moe`: 40 layers, 10 full-attention, 30 linear/DeltaNet), rigby = i7-3770 (AVX + F16C, no AVX2), 24 GB DDR3,
GTX 1060 6 GB on a riser at PCIe 3.0 x1, GT 730 2 GB in a chipset (Gen 2) slot.

## 1. Rigby's A3B, every configuration the lab has measured [M]

| configuration | read tok/s | write tok/s ~2K | write ~12K | source |
|---|---|---|---|---|
| CPU alone | 14.4 (2K) · 18.7 (4K, ub 2048) | 5.5 | 2.7 | GT 730 runbook 10-07; #36 r24 |
| CPU + GT 730 holding the 10 attention layers | 13.6 / 10.8 | 4.3 | 3.3 (+25 %); 16–32K +39 %, >32K +66 % | runbook; #36 A1-log study |
| CPU + 10 tablets reading (split), CPU writes after a handoff | 25.05 (4K) · 17.0 (16K) · 14.4 (32K, multi-rigby) | — | — | #36 r24–r29 |
| **1060 via the fitter, server, ub 512** (r39) | 27 | 12.56 | 11.78 | today |
| **1060, server, ub 2048, `-np 1`** (r40) | **82.8 (2K) · 74.3 (12K)** | **12.43** | **11.64** | today |
| 1060, margin 256 (+765 MiB on the card, verified) (r41) | 86 / 77 | 12.54 | 11.71 | today |
| 1060 + Kepler shader flags (r42) | 82.5 / 74.1 | 12.35 | 11.54 | today |
| 1060 + GT 730 holding ~3.6 GB (r43, stopped) | 32 | 4.0 | 3.6 | today |
| llama-bench, fitter, ub 512/2048, write tg64 (r38) | 38 / 87 | 12.97 / 12.08 | 12.24 / 11.71 (8K) | today |
| llama-bench, fitter, 2 / 4 / 8 threads (r44) | — | 9.85 / 13.14 / 13.57 | — | today |
| llama-bench, whole layers (`-ngl 8`, no slicing) (r44) | — | 6.06 ± 1.1 | — | today |

Quality side [M]: on the HARD end of agentic coding rigby was dominated and slow — hours per exercise, solo 0/4 valid,
R-team 6/9 with three priced out at the 3 h ceiling (partial checks 19/23, 27/31, 2/9); chat-turn is its home tier
(35/35 at 1.9 s); S10b 14/24. Those prices were all set at CPU / GT 730 speeds.

**The 1060 multiplies rigby's A3B by ~4–5× reading and 2.3× (2K) to 4.3× (12K) writing, and the writing speed barely
falls with depth** (12.4 → 11.6 from 2K to 12K, where the CPU halves). It is the biggest single change rigby has had.

## 2. Where a token's time goes now

**Writing** (decode, batch 1) — 76 ms per token at 4 threads [M r44]. Fitting T = G + C/threads to 2 and 4 threads:
**C/4 ≈ 25 ms is CPU work that scales with threads; G ≈ 51 ms does not** [I from M]. Facts that constrain G:
- Moving +765 MiB of experts onto the card bought <1 % [M r41] — expert bytes on the CPU are not the critical path.
- Whole layers on the card (2 hand-offs per token instead of ~80) LOSE badly [M r44] — the 1060's job is the attention
  and shared parts of EVERY layer; the fitter's sliced placement is the right shape.
- During decode the 1060 sits at ~55–65 W of 120 W at full clock, CPU threads busy ~85 % [M telemetry] — but this
  build is OpenMP, whose threads spin while waiting, so "busy" does not prove compute.
- **The lab has seen this exact shape on Mothership** [L, [[Qwen3.8-Flash-Next-on-Mothership]]]: a hybrid-DeltaNet MoE
  runs ~8,000 GPU ops per token (a dense 32B ~1,500) near the per-op launch floor (~5 µs/op on a 7900 XTX); llama.cpp's
  one-thread scheduler makes CPU and GPU take turns, and every CPU⇄GPU hand-off costs ~1 ms; GPU kernels were only ~37 %
  of decode wall, CPU *issuing* Vulkan work ~10 %. Rigby's model is the same family (30 of 40 layers DeltaNet), on a
  slower card, a slower CPU (Vulkan command recording runs on it) and an x1 link. **So G is most likely per-op GPU
  dispatch + Vulkan issue cost on the Ivy Bridge + ~80 hand-offs** [I] — which piece dominates is UNMEASURED.

**Reading** (prefill) — **the x1 riser is the limit** [I, strongly supported]. For batches ≥ 32 tokens llama.cpp copies
the CPU-held experts to the card for every micro-batch ("op offload") [D DocShotgun guide]; ~13 GB of CPU-side experts
over a ~1 GB/s link is many seconds per micro-batch. Evidence it binds [M]: reading rose almost in proportion to ub
(37.6 → 59.4 → 85.5 tok/s at ub 512 / 1024 / 2048, r36); during reading one CPU thread is busy and the 1060 fluctuates
40–100 W (waiting on the link); and the GT 730 path with `--no-op-offload` read at CPU speed.

## 3. What the community reports — multiple cards of different power, small GPUs, MoE offload

- **Splitting one model across cards serialises it; the slowest device sets the pace; independent replicas scale.**
  A llama.cpp wrapper's tests: 1 GPU 420 t/s, 4 GPUs split 130 t/s, 4 independent replicas 975 t/s [D]. Layer split
  beats row split on consumer PCIe (row split is CUDA-only, not Vulkan) [D]. "If the model fits on the fast card, run it
  there" [D]. **Our r43 is the textbook case** (1060 + 730: −68 % write, −61 % read) and Voltron (Phase 6) was the same
  lesson with tablets. The transferable rule: a weak device adds value as an INDEPENDENT worker (a replica), not as a
  slice of the fast device's model — matching the lab's own #36 rule "helpers never sit in the writing loop".
- **A near-twin of rigby** [D, two YouTube creators via insiderllm.com; flags only partly on record]: GTX 1060 6 GB +
  i3-8100 (AVX2) + 24 GB DDR4, same model: experts to CPU 3 → 10 tok/s, **`--no-mmap` 10 → 13.5**, pulling some
  experts back 17, quantized KV for context. Rigby's 12.5–13.1 sits at their middle step; the differences are AVX2/DDR4
  and **mmap, which rigby still uses** — and rigby's own server log already prints llama.cpp's warning *"tensor
  overrides to CPU are used with mmap enabled - consider using --load-mode none for better performance"* (our build
  reports `REPACK = 1`; CPU weight repacking needs the weights loaded, not mapped). ⚠ Mothership found the OPPOSITE
  (mmap +30–40 %) because its 35 GB CPU-side set did not fit 32 GB RAM; rigby's 16 GB file fits 24 GB.
- **Vulkan vs CUDA on NVIDIA** [D]: decode ~10–25 % behind CUDA; prompt processing 3× behind (RTX 4080) and up to
  ~10× behind on Pascal (P40, one user's report, cause unverified). CUDA graphs cut per-launch overhead and help MoE
  decode most (a 2026-08 PR restored graph capture for MoE: +13 %) — Vulkan has no equivalent. **CUDA on the 1060 is
  the one lever aimed squarely at G.** Constraint: rigby runs driver 470 (the GT 730 needs it) = CUDA 11.4 max; CUDA
  11.4 + 470 is reported working for llama.cpp on Kepler/Pascal but on older source trees [D]; Pascal needs CUDA ≤ 12.x.
- **Expert cache (`--moe-cache-mib`, in our build)**: gains on full x16 slots; losses on 6–8 GB cards and narrow
  links; the author's advice is to leave it off below x8 [D]. Rigby is x1 → predicted loss [I].
- **MTP with experts in RAM: −27 % decode** [D] — checking drafted tokens pulls more experts. Rigby's CPU check-cost
  for the MoE was 4.6× one token for a 2-token batch [M r1], and n-gram speculation lost 29–34 % on the CPU [M]. On the
  1060 path the check cost has NOT been measured.
- **Big prefill batches**: `-b/-ub 4096` recommended for hybrid MoE; raise `GGML_OP_OFFLOAD_MIN_BATCH` on low-bandwidth
  links [D DocShotgun]. Rigby's ub 4096 is untested; the trend says it is still link-bound at 2048.

## 4. Levers, ranked (evidence → prediction → cost)

**Reading (link-bound):**
1. **ub 4096** — trend 37.6 → 59.4 → 85.5 [M]; predicted ~105–115 tok/s [I]; write cost small (ub 2048 cost −1 % [M]).
   Cost: one order (the `ubatch` knob exists; >2048 also sets `-b`).
2. **A full-width riser (x16 electrical in the CPU slot)** — the link is ~1 GB/s at x1 vs ~15.8 GB/s at Gen3 x16;
   prefill should then be limited by the 1060's compute, plausibly several × today [G — no x16 number exists for this
   card]. Cost: hardware, the owner's call (he mentioned buying one).

**Writing (G ≈ 51 ms fixed + C ≈ 25 ms CPU):**
3. **`--load-mode none` (no mmap) so CPU weight repacking applies** — llama.cpp's own warning in our log + the twin
   rig's +35 % [D]; attacks C. Cost: a `load_mode` knob on llm/bench orders + one order. Check RAM: 16 GB resident in
   24 GB with the containers' own use.
4. **Threads 8** — +3 % [M r44]. Cost: a `threads` knob on llm orders (bench has it).
5. **Measure G before choosing between 6 and 7** — the lab's own instruments exist [L]: our scheduler profiler patch
   (`GGML_SCHED_PROFILE`, Mothership `patches\sched_profile.py`), Vulkan's per-op timer (`GGML_VK_PERF_LOGGER=1`), and
   the `graph splits` / `graph nodes` counts printed at load with `-v`. One order splits G into GPU kernels / Vulkan
   issue / hand-off waits.
6. **CUDA build for the 1060 (CUDA 11.4 under driver 470, keeps the 730 working)** — the only lever aimed at per-op
   dispatch + graphs, and at Pascal's Vulkan prefill penalty [D]. Unknown: whether today's llama.cpp builds with 11.4.
   Cost: a build attempt (no hardware change; the live and `next` builds stay).
7. **A newer llama.cpp** (our `next` is `23b0202`, ~10-10) — DeltaNet/graph fusion lands upstream continuously;
   fewer nodes = less G [I]. Cost: a rebuild + the lab patches re-applied.
8. **Check cost on the 1060 path** (bench `-p 1,2,4,8`, as r1's T3) — decides whether MTP / n-gram / a draft model can
   come back for writing. Cheap; one order.

**Closed or predicted losses (do not re-run):** more experts on the card for writing (r41, <1 %); the 730 holding
experts (r43, −68 %); whole-layer placement (r44); `-np 1` alone (r39, <1 %); the expert cache on x1 [I + D];
MTP / n-gram on the CPU path [M]; Kepler shader flags cost (r42, ~0 — not a lever, a clean control).

## 5. The whole box — every part pinned to its maximum (revised 2026-10-14)

Frame (the owner, 10-14): *"idk that anything is 'superseded' due to our philosophy for this being the Frankenstein machine.
It's been given more hardware to work with, rather than replacing the hardware ... Ideally we can get everything
pinned to maximum."* A job the 1060 now does faster is a job to **reassign**, never a device to retire.

**What 10-14 settled for the A3B [M]:** CUDA 11.4 on the 1060 (r51) writes **14.2 / 13.8 tok/s at 2K / 12K** (CPU
alone 5.5 / 2.7 = 2.6× / 5.1×) and reads **81 / 109** (CPU 14.4 / 12.3 = 5.6× / 8.9×). Per written token (70 ms):
CPU experts 36 (52 %) · waiting on the GPU 15 · CUDA copies 4 · issuing 8 · outside 6. **All speculation is closed on
this placement** (r52a: n-gram drafted at 61 % acceptance and still wrote 34 % slower — a k-token check costs ~k× the
CPU-held experts; the same mechanism closes MTP and a draft model). The CPU's 4 cores are saturated while it writes.

**Every part, its maximum job [M where measured, I otherwise]:**
- **1060 + CPU** — the A3B engine, both halves, CUDA. Next drops: the CPU-held experts (36 ms; a lower-bit cook of the
  CPU-side layers only — the owner's call, priced by the lab KLD instrument), the op-offload threshold for short prompts
  (menu / A1 reads are hundreds of tokens and each ≥32-token batch pays a ~10 s stream at x1), ub 4096 for long reads,
  CUDA graphs (r53), an x16 riser (reads; also reopens the expert cache).
- **GT 730** — its own work beside the A3B: a small model fully on the card (Vulkan, Kepler flags), or vision / camera
  detection. It shares no compute with the A3B's halves except one host thread for issuing; **measure the A3B with
  and without a 730 job running** before giving it work (the CPU is saturated during writing).
- **Tablets (10, the Fellowship)** — independent workers (Phase 7), their own small models; they cost rigby only
  orchestration. The read-split machinery is kept for any model the 1060 does not hold and for the layer-up design.
- **CPU during the A3B's READING** — reading runs one thread (the stream over x1), so 3 cores idle for the read's
  duration; during WRITING it has nothing to spare. A CPU replica job would have to yield to writing.

**Rigby's role (A: stays under Tolkien · B: fleet-class).** What the data says [I from M]: rigby is now the only box
below Mothership that runs the 35B-A3B at a usable speed (8 GB cards run 8–9B dense), and the A3B's quality on the
menu sat well above the 8 GB tier where both were measured (thinking off: f1 240/240, f2 574/576; tool 0.979 —
behind the 7B on tool). Those prices were set at CPU speed, so the rung is unpriced at 14 tok/s. The cheapest path to
using it does not need a re-architecture: the desk already places pieces on rigby by DETECTED capability (`rigby.llm`,
`rigby.gt730`; `a1_delegate.RigbyDelegator`) — a `rigby.cuda` capability + re-priced menu cells make rigby a mid
rung (A3B quality between the 8 GB tier and Mothership) while Tolkien keeps governing it (A, with B's reach). Whether
it should become a fleet factory proper (B) is the owner's decision after those cells land.
**Next orders:** superseded by § 7 (10-14 results; the earlier list is in `Archive\docs-superseded-20261014-36-status.md`).

## 6. Method record (what went wrong today, so it does not repeat)

- Hypotheses were asserted before reading Tolkien's own telemetry; every time the telemetry was read it changed the
  story (reading was 24 of 39 min; "CPU-bound" was not proven).
- The fitter re-places on every ub / context / slot change, so "one variable" tests also moved the placement —
  placement must be RECORDED per order (cohort `fit_placement`; note `llama-fit-params -fitp on` prints the estimate,
  not the fitted arguments — run it without `-fitp` to get them).
- Three of 39 `arsenal.json` levers were tried and called exhausted. This doc's §4 is the per-lever map for rigby.
- Retracted the same day: "experts on the card are the write lever" (r41), "ub 2048 pushed 1.8 GB of experts off the
  card" (telemetry: 5,087 vs 5,130 MiB), "the 730 will add experts and help" (r43), "the gap is the handoffs alone"
  (r44 whole layers).

## 7. 2026-10-14 — CUDA, MTP, and the wall (r50–r69)

**Where rigby's A3B ended the day** (1 replicate each unless noted; write 2K / 12K, read 2K / 12K tok/s):

| config | write | read | source |
|---|---|---|---|
| Vulkan, ub 2048 (10-13 best) | 12.43 / 11.64 | 82.8 / 74.3 | r40 [M] |
| CUDA 11.4 (sm_61, driver 470 kept) | 14.23 / 13.78 | 80.6 / 109.3 | r51 [M] |
| + CUDA graphs (lab patch `LAB_CUDA_GRAPHS_PRE_VOLTA`) | 14.51 / 14.02 | 80.8 / 109.5 | r54 [M] |
| + small-read threshold `op_offload_min_batch 256` | 14.55 / 14.01 | 80.5 / 108.8; **47-token read 6.5 → 3.1 s** | r66 [M] |
| **+ MTP (Unsloth MTP file, `draft-mtp`, 2-token drafts)** | **16.74 / 16.71** (fresh code 16.88) | 71.8 / 98.8 | r67a / r68a [M] |

Per written token at r54: CPU experts ~36 ms · the 1060 ~25 ms · other ~7 ms [M, scheduler profiler]. MTP turns each
~71 ms step into ~179 ms for 3 tokens (2.51×), so it pays above ~82 % acceptance [I]; Qwen3.6's head accepted 94 %
(fresh code) to 99.6 % (copy-like) [M]. **The standard 1060 order** is in [[Fellowship-Order-Taker-Design]] § placement
fields. The best public 1060 rig (i3-8100 AVX2 + DDR4) reports 17 tok/s [D]; rigby (no AVX2, DDR3) is now level —
**the single-stream wall for this hardware.**

**Levers closed today, each by measurement** (lever map `lab-engine\research\split-inference\rigby_arsenal.json`):
- **n-gram speculation** (r52a): −34 % — 8-token drafts at 61 % acceptance; each checked token pays its own CPU experts.
  (This was wrongly generalised to MTP in the morning; r67/r68 reversed it.)
- **Lower-bit CPU-side experts:** skipped (the owner: steep quality drop below Q3; the Flash-Next KLD record agrees).
- **Hot-expert pinning** (r60–r64): routing IS skewed (top 46 of 256 carry ~53 % on A1 traffic) but **task-specific**
  (A1's hot set catches only 22–37 % of the probe's routing, r63); upstream's store (llama.cpp PR #26563, closed) and
  the lab's block-split patch (`lab-engine\patches\hotstore_block_patch.py`) lose or tie as shipped; the ceiling
  with a hot set tuned to the very text is +4 % over its control, ~+1.5 % over the standard order, at −60–70 % read.
  Binding: x1 link, 6 GB (~48 slots), task-specific routing. Build `hotstore` + order field `expert_hot` stay wired.
- **The 730 as MTP-head host:** no — the head reads the target's hidden state every token and a 730 hand-off costs
  ~2.5 ms (r43) vs the head's ~1.8 ms/step on the CPU [I]. A separate Q8 head file is heavier than the grafted 424 MB one.
- **730 co-location beside the A3B:** NOT measured — Tolkien's rigby claim is whole-box and the A3B saturates rigby's 4
  cores; only a small model wholly on the 730 could co-run, which needs a factory opt-in the owner declined (r65, void).

**The MTP file vs ours** (r69, KLD on 16 × 512 lab-doc tokens): mean 0.0159 (our own noise floor 0.0111), same top
token 92.6 %, PPL 26.70 vs 26.83 → equivalent. Ours is Unsloth's plain release byte-for-byte; the MTP release keeps
every non-expert tensor, re-quantizes the experts (layer 1 down, layer 39 up) and adds the 424 MB head; Unsloth
publishes no quality numbers for it.

**Hardware:** the 1060 fell off the bus twice (10-13 under first load; **10-14 at idle**, Xid 79) on the same power
setup — the 600 W PSU has fed its 6-pin since installation. A reboot recovers it; leading suspect the riser / x1 link
[I] ([[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]], RUNBOOK-DEFECT-LOG 2026-10-14).

**Method record (10-14):** library-first found PR #26563 before any patch was written; every order now gets its own
watcher at submit (r62b/c sat unread 35 min); check each order against the factory's claims, preflights, live node
state and prior results BEFORE submitting (r65: both void, knowably); a model "with MTP added" was not byte-identical
— hash and diff before assuming; ETAs from Softserve's clock, not rigby's UTC log stamps.

## 8. 2026-10-26 — the full-width riser: x1 → x16, reads 3.5× [M]

the owner swapped the 1060's x1 riser for a full-width one (link read back: `nvidia-smi` gen 3 / width 16, sysfs
`max_link_width 16`, no AER errors). Measured by re-running the 10-16 menu-cell order UNCHANGED (the standard 1060
order, c1 / thinking off / temp 0.6 / seed 1, 60 items + control) as `riser-x16-c1-standard-1060.20261026`, against
`hardbench-rigby-m-qwen3.6-35b-a3b-mtp-ud-q3-k-xl-c1-t06-off-s1.20261016-0823` (x1) and its seed-4 replicate.
**All 61 answers were token-for-token identical to the x1 run**, so both did the same work. Per paired row:

| | x1 (s1 / s4) | x16 (s1 / s4) | x16 ÷ x1, paired (s1 / s4) |
|---|---|---|---|
| read, ~2,050-token prompts (9 rows) | 101.5 / 101.5 tok/s | **349.7 / 349.4** tok/s | **3.54× / 3.52×** (2.40–3.98) |
| read, tiny prompts (52 rows, median 4 tokens) | 10.0 / 10.1 | 16.9 / 16.9 | 1.68× / 1.64× |
| write (61 rows) | 15.9 / 15.8 | 17.2 / 16.9 | 1.07× / 1.08× (1.04–1.14) |
| time to first token, median | 0.6 / 0.6 s | 0.3 / 0.3 s | |
| order wall | 1,117 / 1,242 s | 950 / 1,002 s | −15 % / −19 % |
| model load | 15 / 24 s | 60 (cold) / 12 s | the same when warm — see below |

Both seeds' x16 runs (`riser-x16-c1-standard-1060.20261026`, `…-s4.20261026`) gave 61/61 answers identical to their
x1 twins.

Reading: as § 4 predicted ("prefill should then be limited by the 1060's compute, plausibly several ×"). Writing:
+7 %, but NOT attributable to the riser alone — the engine was rebuilt 2026-10-22 with the quick-resume patches (#0/#2/#3,
sampler / drafter / slot state) between the two runs; the read path is untouched by them, the write path is not.
Load: the first x16 run came ~30 min after the riser install's reboot, so the 17 GB model came from disk instead of the
page cache (60 s); the second, warm, loaded in 12 s — a cache effect, not a link one.

**Re-measured at x16 on 2026-10-27 (paired orders, 3 answers each; written up 2026-10-30 from the deliveries — the
first attempts on 10-26 16:17 were VOID, 0 answers in 5 s, and re-ran the next morning):**

| order pair | write tok/s (median) | read tok/s (median) | verdict |
|---|---|---|---|
| `riser-x16-mtp-hotstore-auto` vs `…-hotstore-control` (the expert hot store, § 4's "gains on full x16 slots") | 14.47 vs 14.54 | 292 vs 308 | **no write gain at x16 either**; reads −5 %. The `hotstore` build itself writes ~18 % below the standard `cuda` build (its control 14.5 vs 17.7 below) — stays closed |
| `riser-x16-mtp-oomb-default` vs `…-oomb256` (`op_offload_min_batch`, 256 set for x1) | 17.67 vs 17.72 | 299 vs 299 | tie: the x1 threshold costs nothing at x16; 256 stays |
| `riser-x16-a1v22-rigby-1060-final` | — | — | the A1 role arm on x16: 11/11 answers, usable |

The standard 1060 order writes ~17.2 (seed 1) / 16.9 (seed 4) on the c1 cell, level with the best public 1060 rig's 17.
**llama.cpp's merged expert cache (`--moe-cache-mib`, PR #29887) LOSES on rigby — measured 2026-10-30** (Tolkien order
field `moe_cache_mib`, added that day; orders `moecache-x16-*`, speed_rewrite_probe, write tok/s on the 2K / 12K
rewrite, everything else the standard order). Controls at the start, middle and end agree: 18.56/17.75, 18.68/17.80,
18.54/17.73. Cache 512 MiB 13.23/13.02 · 1024 13.45/10.50 · 1536 12.02/8.17 · 1024 with fit 2048 9.85/6.20 · 1024
with MTP off 11.99/11.60 — every size −25 to −65 %. The library's own research had predicted it
([[Research-Kai-llama-cpp-Expert-Cache-125B-MoE-Video-2026-10-22]]: small cards lose, ~85 % hit-rate break-even, MTP
worse with a small cache); BigHonker's 8 GB win (register B10) does not carry to the 6 GB 1060. ⚠ The arms were sized
off BigHonker's result without reading that note first. The standard order stays rigby's production profile
(`tolkien-build\rigby_profiles.py`, laid under every rigby launch since 2026-10-30). Still unmeasured at x16: ub 4096
(a READ lever, for read-heavy job types only) and whether the Xid 79 drops stop
([[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]] § "Two cards on rigby").
