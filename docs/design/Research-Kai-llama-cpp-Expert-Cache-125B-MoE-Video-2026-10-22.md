# Research note — "llama.cpp Just Made 125B MoE Models 4× Faster!" (Kai, 2026-10-22): the per-expert GPU cache, where the 4× comes from, and which cards lose

**EXTERNAL SOURCE NOTE + checks against the primary threads and the lab's own data.** The headline hardware is **the lab's own Mothership card** (RX 7900 XTX, 24 GB) running **the lab's own model** (Qwen3.8-Flash-Next), so the comparison table below matters more than usual. Earlier lab work on the same feature: [[36-Hypothesis-Review-and-Video-Crosscut-2026-10-12]] § 4 (the first expert-cache video), [[Qwen3.8-Flash-Next-on-Mothership]], [[Qwen3.8-Flash-Next-Speed-Levers-Research]], and the BigHonker entry in [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] (Phase 3).

Filed 2026-10-22 at the owner's request. Raw files (captions, info JSON, description, cleaned transcript) are in `Claude Home\research\video-shl8FFSoANE\`. Captions came through the Pinchflat container on Sundae.

**Certainty tags:** **[D]** said in the video, unchecked · **[V]** checked at a primary source · **[L]** compared with a lab measurement · **[?]** named but not found.

## The video

| | |
|---|---|
| Title | llama.cpp Just Made 125B MoE Models 4× Faster! |
| Channel | Kai (`@kaiexplainsYT`) |
| Uploaded | 2026-10-22 15:30 UTC (the third Kai upload seen at :30 past the hour) · 28 min 32 s · 12 chapters · 34,494 views at pull time |
| Thesis | The famous 4× (9.7 → 37.2 tok/s on a 7900 XTX) is real but measured against a baseline that held no experts on the GPU, on an experimental branch; the shipped cache is closer to 1.5–2.2×, and it loses on small cards and narrow PCIe links. A 15-minute test gives your own number |

## Claims

| # | Claim | Tag | Note |
|---|---|---|---|
| 1 | 9.7 → 37.2 tok/s (3.84×) on a 24 GB 7900 XTX, same model | **[V]** | Issue #29949 (lich-77): 7900 XTX, 64 GB RAM, Qwen3.8-Flash-Next **UD-IQ3_XXS**, baseline `-ngl 99 -ncmoe 48 -c 4096` = **9.7**; **160 slots = 37.2 at 89.4 % hit rate**; 160 slots + MTP-Q8 = 44.8. **This is the experimental branch, not the merged PR.** The video's opening narration blurs that ("merged … they got 37.2"); it corrects itself at 12:13 |
| 2 | Baseline `-ncmoe 48` keeps every expert (24,576 = 48 layers × 512) in system RAM, so the card held none | **[V]** | Flag and baseline confirmed in the issue; 48 × 512 = 24,576 and the lab's own doc gives 48 layers, 512 routed, 10 active **[L]** |
| 3 | The merged cache (PR #29887, `--moe-cache-mib`, merged **2026-10-07**) is **2.2× on an RTX 5090** against default `--fit` | **[V, with a caveat]** | PR table (Q4_0, 93.7 GiB total, 65.4 GiB experts, EPYC 7742, PCIe 4.0 ×16): 5090 `--fit` 30.8 → `--fit --moe-cache-mib 6544` **54.5 (1.77×)** → `-cmoe --moe-cache-mib 19000` **67.8 (2.20×)**; 4090 25.0 → 39.4 (1.57×) → 40.7 (1.62×). **The 2.2× is the `-cmoe` row** (all experts on the host), not the default-use row; the PR says "10 % of total expert size is good" and that it only helps batches of **≤ 32 tokens** |
| 4 | Experimental branch is 1.55× faster than the merged cache on the same machine | **[V]** | Issue: same machine, same prompt, ~6.5 GiB cache, IQ4 reverse placement: #29887 **20.1 (75.0 % hit)** vs branch **31.2 (77.9 %)** = 1.55×. The branch runs the LRU decision as a graph op and copies from pinned host memory, with no per-layer CPU round trip |
| 5 | The Oct 16 patch (PR #30449) fixed Windows pinned memory: **37.76 → 62.73 tok/s** | **[V]** | Matches (Windows, RTX 5090, `-sm none --moe-cache-mib 15000`, `-cmoe`, draft model, speed-bench coding, 8 samples; prompt 73.28 → 110.66). **It is a CUDA change** (caps one pinned host buffer at 4 GB and splits big weight sets across several), **not HIP**, and on **Linux it did nothing** (73.16 vs 73.13) |
| 7 | Hit-rate break-even about **85 %**; below it the cache costs speed; need roughly a quarter of the expert weights (~16 GB at Q4) | **[D]** | The video's rule of thumb from reported runs; the PR says nothing about a break-even or about small GPUs. The expert-count arithmetic checks: 24,576 experts × 2.73 MiB ≈ 65.4 GiB (the PR's expert size) |
| 9 | PCIe arithmetic: ~137 MB per token at 90 % hit; x16 Gen4 31.5 GB/s fine; an x4 slot (7.9 GB/s) caps at ~58 tok/s; a 2080 Ti on Gen3 ×16 did better with whole layers (58.5 vs 53; with MTP 70.9 vs 46.1) | **[V arithmetic]** / **[D]** owner data | 480 experts per token × 2.9 MB ≈ 1.39 GB; 10 % misses ≈ 139 MB ✓; 7.9 GB/s ÷ 137 MB = 57.7 ✓. The 2080 Ti numbers are an owner report |
| 10 | Prompt reading drops with the cache: 2×4090 test decode 1.85×, prompt 0.73× | **[D]** | From PR #30112, **not fetched**. PR #29887's own thread has the opposite report for one AMD/HIP user (4,543-token prompt 51.5 → 66.5 pp t/s) and ROCm/Vulkan regressions for others — the effect is **setup-dependent** |
| 12 | A repetition loop reported on a 5090 with the cache on, unproven cause | **[D]** | Stated as unproven in the video |
| 14 | A comment (screenshot): a 7900 XT 20 GB + 32 GB RAM owner got 15 tok/s on llama.cpp (64 K context), 11 with the cache and a 90 K context, **52 tok/s on Strata's UI**, using the 58 GB ISTA "q1" Coder build with half the experts removed | **[D, comment]** | A viewer's report, not the video's claim. The lab's note on that Coder build is [[Research-Lukes-Dev-Lab-Strata-Flash-Next-Coder-Video-2026-10-14]] |

## Compared with the lab's own data [L]

| Question | Video / threads | Lab | Read |
|---|---|---|---|
| **Same card, same model** (7900 XTX, Flash-Next) | llama.cpp + the experimental cache: 9.7 → 37.2 (IQ3_XXS, 64 GB RAM); Strata ahead of llama.cpp on one PC | Mothership, 64 GB RAM since 10-17: **Strata varied median 68.4 (last five 67.9), prefill 838–843**, load 36 s; llama.cpp control (q2k, MTP 2, mmap) **last-5 median 26.9**, prefill 143–147 → **Strata 2.5× writing, 5.8× reading**. On the old 32 GB box it was 45.8 vs 19.8 | **Agrees in direction and size.** Video: Strata 1.48× writing (85.8 / 58.1) and 5.1× reading (2,320 / 451). The lab's 5.8× reading matches; the lab's writing gap is larger because the lab's llama.cpp control (10-04/10-17) is the **older build** (Unsloth b11160 + the PR #27861 draft cache, q2k), **not** the merged cache nor lich-77's branch |
| **Has the lab tested the merged cache on Mothership?** | PR #29887 (b11480, release 0.7.0) | The lab's own build `llama-mix-<sha>` carries the **older draft cache (PR #27861)**; the merged `--moe-cache-mib` build and the experimental branch are **not in the lab's data** | A real gap, and it is the lever the video's headline comes from. Candidate for the lever list ([[Qwen3.8-Flash-Next-Speed-Levers-Research]] / `arsenal.json`); nothing queued |
| **Do small cards lose with the cache?** | 8 GB 4060 laptop: 23.4 → 15; 12 GB 3060: 32 → 12; "below ~85 % hit rate it costs speed" | **BigHonker (RTX 3070, 8 GB), qwen3.6 35B-A3B: `--moe-cache-mib 1024` = the winner, 26.5–32.9 vs 22.9–24.2 tok/s** (1,536 overlaps, 768 lower, 2,048 spilled); adopted into BigHonker's launcher ([[RUNBOOK-Ollama-Host-Operations]]). Context: the register had the cache **off** in BigHonker's earlier tuned launch (written "#36: off on 8 GB") until this engine round measured the win; [[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]] carries the community rule ("losses on 6–8 GB cards and narrow links", tagged **[D]**) and **predicts** a loss on rigby's x1 link (tagged **[I]**, not measured) | **A direct disagreement on the same model family.** Both sides see a U-shaped curve in cache size. The lab's win used a 1 GB cache on an A3B (small expert pool, hit rate high); the video's loss used 1.5–2 GB on a laptop 4060 and 8 GB on a 3060 for a much larger model. Not one variable: hardware, link, model and cache size all differ. **Neither result transfers to the other box** |
| **Pinned host memory** | Pinned copies ~2.7× faster than unpinned; PR #30449 splits big pinned buffers on Windows CUDA | The lab already found that the IQ weights sit in pinned `CUDA_Host` memory where the K-quant's do not, and that this is part of why the IQ file is faster at the tuned launch (register Phase 3, "File per box") | **Consistent.** Whether BigHonker's lab engine contains #30449 (Windows CUDA, merged 10-16) is **not known**; ollama's own llama.cpp serves production |
| **Speculative decoding on a MoE with experts in RAM** | MTP slower with experts in RAM unless the cache absorbs the burst | Lab: MTP **loses** on BigHonker's A3B at depth 2–3 (72 % / 57 % acceptance) "experts in RAM make every verify token pull more RAM"; MTP is on for the 27B | **Agrees** (the lab's measured reason is the video's reason) |
| Model structure | 48 layers × 512 experts, 10 active per token (24,576 total; 480 per token) | Lab: 125B MoE, 48 layers, 512 routed + 1 shared, 10 active, ~6B active | **Agrees** |

## Implications for the lab

1. **Mothership is the card this video is about, and the lab is ahead of the video on Strata but behind on the llama.cpp side.** Nothing here changes the chosen engine (Strata); what it adds is that the lab's llama.cpp comparison arm is **out of date**: the merged expert cache and the experimental branch are not in it. If the lab wants the llama.cpp-vs-Strata gap to be honest (class 13), the arm needs the merged cache build. Not queued.
2. **BigHonker's cache setting stands on the lab's own measurement.** The video's "small cards lose" rule is a population statistic from other hardware and models; the lab's 8 GB result (and its U-shape) is the one that binds BigHonker. Worth keeping as a documented counterexample.
3. **One unknown worth one read:** whether the lab's BigHonker engine includes PR #30449 (the CUDA pinned-buffer cap), since BigHonker is Windows + NVIDIA + pinned weights. The PR reports no gain on Linux and a large gain on one Windows 5090 setup; an 8 GB 3070 may see nothing.
4. **The 15-minute test is the lab's own practice already** (compare against the best tuned whole-layer placement, not against zero; log the hit rate; warm up first): Doctrine-Measurement-and-Verification and the speed-check orders.

## Sources named — resolution status

| Source | Status |
|---|---|
| Issue #29949 (lich-77's branch, the 9.7 → 37.2 run) | **[V]** fetched; numbers above |
| PR #29887 (merged cache, 2026-10-07; RTX 4090/5090 table) | **[V]** fetched; numbers above |
| PR #30449 (Windows pinned memory, 2026-10-16) | **[V]** fetched; numbers above |
| PR #30112 (multi-GPU cache and the prompt-speed table), release v0.7.0, b11480, Qwen3.8-Flash-Next card | **[?]** not fetched |
