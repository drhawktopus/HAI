# #36 — Hypothesis review against the owner's original ideas, and a cross-cut of the two videos with the lab's history (2026-10-12)

Child of [[36-Split-Inference-Helpers-Read-and-Draft-Design]] (results through round 29) and campaign #34. Written at the owner's
request while the rig was idle. Certainty tags: **[M]** measured here · **[D]** stated in a doc/video · **[I]** inferred
by arithmetic or analogy, not tested · **[G]** a guess. Anything tagged [I] or [G] is a hypothesis with a test attached.
**Dated snapshot (10-12, CPU-era rigby).** What changed after the GTX 1060 (MTP adopted, split reading outpaced):
[[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]] § 7; current standing: the design doc's Status.

## 1. What the owner actually said (primary source: main session, 2026-10-10 21:57–22:09 UTC)

The ideas, in his words, then my reading of each. **I am assuming the four; the owner to correct.**

1. **Reading shared across devices** — *"we spread out the read across devices"* (from the Backburner video).
2. **External MTP** — *"while models internally do MTP who's to say we couldn't externally do MTP?"* — a drafter on other devices, rigby checks.
3. **Small models are fast on the tablets** — *"some of the models that we have are fast on the tablets when we're using the small models"* — tablets as drafters (or independent workers).
4. **Roles per tablet, at the same time** — *"five of them do read and five of them do write … breaking it up per tablet."*
5. (Then, not one of the four:) **Generalize up** — *"do this with mothership with the rest of the lab below."*

the owner on the tangle: *"I know I'm kind of intertwining a few different ideas at once."* The main session untangled them into the five levers of the #36 doc (rule 6): reading help · external drafting · context archive · built-in MTP · tablets as independent workers.

## 2. Scorecard against those ideas

| Idea | Verdict | Evidence |
|---|---|---|
| 1. Shared reading | **Works, modestly.** [M] | 4K ~1.34× (25.05 vs 18.7 tok/s, rigby at its best ub); 16K ~1.09×; 32K ~1.12× with multi-rigby (single-rigby plan ≈1.0×). Quality cost not measurable (KLD 0.0114 vs noise floor 0.0111). Handoff to rigby for writing works (4.2 s save, 0.1 s restore). |
| 2. External MTP on tablets | **Not shown; one config lost; structural headwinds.** [M]+[I] | Measured once: 14B on rigby, tablet drafter 1.87 vs plain 2.38 (worse); a 0.5B drafter on rigby itself +20 %. Headwinds in §4. |
| 3. Fast small models on tablets | **True, but they cannot beat rigby's own tiny drafter.** [D] | Tablets write 0.8–1B models at 7.7–9.9 tok/s; rigby runs a 0.5B at 43. Real role: Phase 7 independent workers. |
| 4. Simultaneous per-tablet roles | **Half-tested.** [M] | Tablets READ for rigby: pays. Tablets in the WRITING loop: 3.6× slower (Voltron reproduced). Tablets reading and working independently at once: unmeasured; contention likely. |
| 5. Generalize up | **Not started, and the videos say it is where the best result is** — §5. |

**The strongest single finding against the charter:** the idea is strongest where the lab least uses it. Gain is largest on short prompts (1.34×) and fades with length (1.09× at 16K), while Backburner's premise is that big reads carry 83 % of tokens. Two named ceilings: at 4K the limit is MEMORY (tablets hold 1–4 layers each; ~2.5–2.7× would be reachable by speed alone [I]); at 32K it is ATTENTION cost on tablets (~15× rigby's per layer [M]).

## 3. Video 1 — Backburner ("I turned my iPhone into a second GPU…", 2026-10-10) against our results

- **Reading vs writing.** Their core split and ours are identical, and ours reproduces it: helper joins reading, stays out of writing. Their writing is unchanged (25.0 vs 25.1) because the Mac owns every layer in write mode; ours is the same shape — rigby holds the whole 35B and takes the cache by slot restore. [M]
- **The gain decays with context in both.** Theirs: +44 % at 16K, +29 % at 32K, +30 % at 48K; the USB-2 tester +10 % → 0 → −4 %. Ours: 1.34× → 1.09× → 1.12×. Same shape, different cause (theirs: link and first-read context push; ours: tablet attention). [M]/[D]
- **Their rule 2 ("a weak helper makes reading SLOWER; M1 iPad −42 %") is only half right for us.** Each tablet reads ~6× slower per MiB than rigby [M], yet ten together pay, because slices are sized to speed. The iPad lost on a fixed layer split. Corrected rule: a slower helper adds throughput if its slice is sized to it, until memory or overhead binds. [M]
- **Their helper has a kind of compute the main machine lacks (matrix units).** Ours do not; we win only on aggregate. That is why our ceiling is a small multiple, not theirs. **This is the main cross-cut: the best helper for rigby is a device with compute rigby lacks.** [I] Candidates: BigHonker's 3070 (tensor cores), Mothership's 7900 XTX. The 3070 has ~6–7 GB usable; that is about a third of the Q3 file, so reading time falls to ~⅔ at best — **≤ ~1.5× from that stage alone, upper bound by arithmetic** [I] — and a GPU stage could hold the 10 attention layers plus KV, which is exactly the 32K ceiling. Unknowns: does an async-RPC CUDA build exist for BigHonker; its availability (HAI chat host, sleeps); gigabit hop for a 256-token chunk (~20 ms by their numbers [D]).
- **Their SSD prompt cache restores a long agent start in 0.3–5 s [D]; ours is the same primitive (slot save/restore).** A fresh agent session starts at ~27K tokens of system prompt and tools [D]. **The fastest read is no read.** For the lab's agents, what matters is the NOVEL tokens per request, not the total — and we have that data (A1 and desk traces). Measuring it sets how much any reading speed-up is worth. [I] → test T1.
- **Their agent workload is incremental reading (2,000 new tokens onto a 16K cache), ours has only measured cold full-prompt reads.** The two regimes differ: the incremental case needs the helpers to hold the KV for their layers across turns, and our handoff moves the full state to rigby (282 MB at 4K). Sending it back to the stages each turn is an unmeasured cost; llama-bench has a depth option we excluded from rate-fitting. [I] → test T2.
- **The neural engine slowed their writing 26 % because it shared the memory bridge with the GPU, so they turned it off [D].** Ours: the GT 730 lost as a reader; multi-rigby (more stages on the same 4 cores) delivered 1.26× vs ~1.7× predicted, because stages on one machine contend for the same cores and memory. Same lesson: compute that shares the bottleneck resource adds nothing. [M]
- **Their drafter (DFlash2) took writing 11.3 → 25 tok/s on a GPU Mac [D].** That is drafting where checking is cheap; see §4.
- **Practical strings** (screen on, thermals, sideloading) map to ours: battery and port limits (faramir 26 % → 7 % over ~18 h while charging on a 500 mA port [M], sam 19 %), the 15-stage layout needing every tablet alive, the fleet probe running only when idle. [M]

## 4. Video 2 — llama.cpp expert cache ("Do you still need Strata?", 2026-10-09) against our results

- **Drafting on a MoE with experts off the fast path loses.** The video: with experts in RAM, MTP made decode 27 % SLOWER because checking several drafted tokens pulls in more experts [D]. Our MoE: each token picks a few experts per layer; k drafted tokens in one check touch up to k times as many. Decode is memory-bound on those expert weights, so **the "checking is reading, reading is cheap" premise fails for a MoE on a memory-bound machine** [I] (I believe 8 of 256 experts per token; not verified from the file). The dense 14B has its own problem: batch checking costs rigby ~3× a token because it is compute-bound without AVX2 [D]. So drafting on rigby is handicapped on both models. The doc's rule 5 ("drafting pays where checking is cheap") already says this; rigby is the wrong checker. The cheap decisive test is the check-cost curve (batch 1/2/4/8) on both models. → T3. If checking k tokens costs ~k, drafting on rigby is closed and belongs one layer up (Mothership's 3.8, already MTP-equipped).
- **U-shaped tuning curves.** Cache size and hit rate: too little and too much both lose [D]. Ours: split speed vs ub peaks at 64 (22.2 / 22.7 / **25.05** / 24.5 / 21.6 / 17.6 at ub 32 / 48 / 64 / 128 / 256 / 512) while rigby alone rises with ub. Same advice: test your own setup before and after, small hardware loses first. [M]
- **Move less data between boxes** (their "racks" advice). Our link is not the limit (10 KB/token; linear-layer stages identical at 4K and 32K [M]). The thing that moves is the state at handoff.
- **Strata's prompt reading at 2,320 tok/s against llama.cpp's 249 [D]** says engines differ ~10× on prefill. That is a reminder that rigby's read speed is partly an engine and CPU-instruction issue (no AVX2); not actionable without a different engine, but it is the only 10× number on the table. [G]

## 5. Cross-cuts with the lab's history and conventions

- **Voltron (Phase 6) measured hop latency, not compute** — ~65-token prompts. Confirmed by round 18 and by rule 1 (helpers join only big reads). The new finding refines it: helpers should also join only when the NOVEL read is big enough to repay the handoff — roughly a few hundred tokens at 13.5 ms/token saved against ~4 s of save [I].
- **"Sizing to each class" (feedback: tablet classes are rungs).** Backed: class A = B per MiB, C 1.7× slower, A's edge is memory.
- **"Measure the type, not the count"** (frodo's Q6_K layers were 1.5× per MiB on an A53). **Open and tied to the quant the owner parked:** i-quants (IQ3_XXS 48 % of our file, IQ4_XS 32 %) are likely slow on ARM, while plain Q4_0 (with ARM repacking) and Q4_K usually are not [G]. A different quant TYPE could raise tablet throughput per MiB without a smaller file, and the KLD instrument we just built can price its quality cost against the 0.011 noise floor and the published Q4/Q3 scale. → T4. (Shelved by the owner as "skip the quant for now"; this is the case for revisiting, not a decision.)
- **Baselines (13a):** every split speedup in rounds 14–22 was quoted against rigby at ub 64; corrected in round 24. The same class bit the ub tuning of the layouts (rounds 16–22 ran only at ub 64, which turns out to be the peak).
- **Report-only guards (class 5a/12a) work as designed:** the watchdog flagged the round-27 hang for 4.7 h; the missing piece is an owner who acts, and the unwired CPU beat ("worker CPU not measured").
- **"Cache tricks the lab used before" (speed-trick family):** transform once and save, keep hot things on the fast device, pre-bake — here: prompt-prefix cache (T1), GPU-resident attention (BigHonker, §3), repack/quant type (T4). Do not collapse to the one lever that matches the loudest number (the reading speed-up).
- **Arithmetic for a 24:1 read-to-write workload (the OpenRouter ratio [D], NOT ours):** rigby reads 18.7 and writes ~5.0 tok/s, so 24 read + 1 written is 1.28 s reading + 0.20 s writing; reading is ~87 % of the time. A 1.34× read speed-up gives ~1.28× overall; at 16K (1.09×) about 1.07× [I].

## 6. Tests this implies (each an order or a free read; none started)

- **T1 — novel tokens per request.** Read-only over existing A1 / desk traces: share of prompt tokens already in a cached prefix. Free. Decides how much reading speed matters at all.
- **T2 — incremental reading.** Split vs rigby alone reading 2–4K new tokens at depth 16K/32K, stages already holding their KV; plus the cost of returning handoff state to the stages. Orders; tablets needed (faramir/sam out).
- **T3 — check-cost curve: ALREADY MEASURED in round 1 (2026-10-10; `bench-36-r1-checkcost-prefill-link`), found 2026-10-12 after I nearly re-ran it.** Time for a batch of k tokens against one decode step **[M]**: dense 7B 1.4× / 2.5× / 4.8× at k = 2 / 4 / 8 (tok/s 4.4, 6.4, 7.1, 7.3 at k = 1, 2, 4, 8; decode 4.82); dense 14B the same shape (2.48, 3.54, 3.88, 4.08; decode 2.46); **MoE 4.6× / 5.1× / 6.5×** (2.58, 2.68, 4.80, 7.51 tok/s at k = 1, 2, 4, 8; decode 6.15; prefill 14.69 at 512). Verifying d drafted tokens is a batch of d+1. At the 0.88 acceptance the doc records, **[I] arithmetic**: 14B best case is ~+27–30 % at 1–2 drafted tokens and falls past 3 (the measured +20 % with a 0.5B drafter sits right under it — the curve EXPLAINS the cap); **MoE: even a free drafter loses** (batch 2 costs 0.75 s against 0.16 s for a decode step, so ~2.5 tok/s against 6.15). Drafting on rigby is closed for the MoE as built; the MoE's small-batch path would need an engine-level fix (decode runs 2.4× faster per token than the same tokens as a 2-token batch).
- **T4 — quant type vs tablet speed vs KLD** (Q4_0-repack / Q4_K against the IQ mix). Needs files; the owner's call (parked).
- **T5 — GPU reading stage feasibility** (BigHonker): read-only first — is there an RPC/CUDA build for that box, how busy is it, what does one slice cost per MiB. the owner's call before anything runs there.
- **T6 — per-tablet concurrent roles.** Tablets reading for rigby while others serve independent jobs; measure both. After T2.

## 7. T1 result (2026-10-12): what the lab's own rigby agent traffic looks like

Source: 141 Qwen3.6-35B-A3B and other A1 logs on rigby (about 1,500 model calls; row field `prompt_tokens` is llama-server `timings.prompt_n`, i.e. tokens actually PROCESSED, not cached — Tolkien `tolkien_llm.py` lines 469/556; `cache_n` is not recorded). **[M]**

- **Same shape as Backburner's "7 of 36 requests carry 83 %".** Calls after the first: median 92 new tokens, p90 1,189. Calls of 512+ new tokens are 25.5 % of later calls and carry 81.6 % of their new tokens; counting first calls too, 30 % of calls carry 85.5 %; 15 % of calls (1,024+) carry 67 %.
- **Cold starts are small here:** the first call is median 1,227 tokens (p90 1,924; one 37,677 outlier), 21 % of all new tokens. Backburner's coding agent starts at ~27K.
- **So the reads that matter are 512 to a few thousand tokens, incremental, at some depth** — the regime least measured (T2), and the one where the split's gain is smallest.
- **Reading is not most of the wall time.** 98 MoE rows: 868K new read tokens vs 563K generated; at the measured rigby speeds (read 18.7, write ~5.0 tok/s) that is ~16 % of wall for reading and ~39 % for writing; the rest (~45 %) is tool runs, model load and other overhead, not modeled. **[I]** Allowing for slower reads at small batch and depth (say 10 tok/s effective), reading is at most ~30 %. A 1.1–1.34× read speed-up then saves **~2–8 % of wall time**.
- **A direct measurement failed:** the per-call read time (`prompt_eval_s`) is not recorded for llama-server calls (0 of 1,504 calls), so the read share is an estimate with a range, not a measurement. Closing it is a small Tolkien change (record `timings.prompt_ms` and `predicted_ms` per call) — proposed, not done.
- **Consequence for the ideas:** in this workload the prefix cache already removes most reading, and WRITING is the larger share. That raises the value of idea 2 (drafting / MTP) and of anything that speeds writing, and lowers the value of split reading for A1 on rigby — while §4 says drafting on rigby is structurally weak. The honest reading: the #36 reading result is real but small for this workload; the bigger lever is on the writing side or one layer up.
- **Not shown by T1:** Mothership/BigHonker traffic, the desk's non-A1 traffic, and any workload with long fresh prompts (document reading, repo ingest) where cold reads dominate — that is where the split earns its keep.

What would change this review: T1 showing most tokens are already cached (reading speed matters less; prefix-cache management is the real work), or T3 showing checks are nearly free on rigby (drafting revived).
