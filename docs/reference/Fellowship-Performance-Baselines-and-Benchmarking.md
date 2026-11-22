# Fellowship Cluster - Performance Baselines and Benchmarking



## Performance Baselines
Gflops measurements for the fleet have been rebaselined. Previous recorded values were pessimistic by 11–52% and are superseded.

## Benchmark reference values and scheduling weights

This section records the measured performance baseline for the Fellowship nodes, used by the scheduler to weight task distribution. These values are historical benchmark records; current node capacity is defined by live instrumentation.

### Instrumentation notes

- **DGEMM burn instrument**: Used for `legolas` and `aragorn` at provisioning. Numbers from this instrument are not comparable to HPL/STREAM results below. See `Fellowship-flash-docs.md` for DGEMM data.
- **HPL & STREAM**: Used for the original five nodes (`gandalf`, `frodo`, `sam`, `merry`, `pippin`).
- **IP and roster ownership**: Managed by the Index ([[Tablet-Cluster-INDEX]]). Do not maintain IP addresses or roster status here.

### Original Five: HPL/STREAM Baseline

| node | cores | RAM | HPL GFLOP/s (own job) | STREAM Triad | notes |
|---|--:|--:|--:|--:|---|
| gandalf | 8 | 2.79 GB | **14.93** | 8864 MB/s | fastest; best radio (312 Mbit/s) |
| frodo | 8 | 1.76 GB | **12.29** | 8845 MB/s | LOS 18.1 — mid-pack, not "flaky" (every node has the 12-h blip; see [[Fellowship-WiFi-Watchdog]]) |
| sam | 8 | 1.76 GB | **11.27** | 8820 MB/s | LOS 18.1 |
| merry | 4 | 1.82 GB | **9.25** | 4941 MB/s | |
| pippin | 4 | 1.82 GB | 1.52 ⚠ | 4877 MB/s | measured at ~24% battery — **re-baseline, expect ≈9** |

### Fellowship Aggregate Reference (five-node era; "Fleet" here was a naming slip, the Fleet is the x86 GPU boxes per [[Tablet-Cluster-INDEX]])

The following aggregate values define the expected total capacity for scheduling weight splits, mirroring `CAPTION_S_PER_IMG` in `scheduler.py`. An even split results in `gandalf` idling while waiting for `merry`.

- **Total Cores**: 32
- **Total RAM**: ~10 GB
- **Total HPL GFLOP/s**: ~50
- **Total Power Draw**: ~35 W — ⚠ not measured (2026-09-11): no per-node reading exists anywhere in the library, and the owner states ~35 W is the spec-sheet PEAK per node, so this five-node total is unverified. Measure at the UPS (minus Rigby and Softserve) if it ever matters.

## Performance Test Method

Use a stratified sample instead of a full corpus sweep to save time while maintaining accuracy. OCR cost tracks image size; therefore, sort the corpus by normalised megapixels, split into N buckets, and take one image from each bucket.

### Sample Accuracy vs Full Corpus

| Sample Type | P90 Error vs Full Corpus |
|---|---|
| Random 25 | 5.3% (larger than most effects being measured) |
| **Stratified 24** | **0.5%** (built sample: 0.04%) |
| Stratified 80 | 0.2% (use when a test needs ≥2 batches per node) |

A full sweep of the 1,049-image corpus takes 71 minutes. A stratified 24-image test on one node takes ~10 minutes. Use a deterministic seed so every test arm processes the SAME images, isolating variables.

## Performance Metric: s/MP

**Do not use `img/s`**. It is a misleading unit that conflates node speed with image size (e.g., Frodo appeared 36% slower than Sam because it processed higher-MP images for the same count).

**Use `s/MP` (seconds per megapixel) or total MP completed in equal wall time.** Normalise by image size to compare nodes accurately.

## x86 CPU Benchmark: Tesseract OCR Throughput (img/s)

### Measurement Methodology
Throughput is measured in images per second (`img/s`) using Tesseract 5.5.0 for watermark detection. The benchmark uses the `tessdata_best` language data and identical test images across all hosts to ensure comparability. These figures are **not** convertible to HPL/LINPACK GFLOP/s scores; do not compare this scale to FLOPS benchmarks. For GFLOP/s data, see [[HOST-ONBOARDING-Basement-Desktops]] §C1b.

### Design Constraints and Caveats
*   **Queue Depth Effects:** The values below are understated comparison figures, not absolute capacity metrics. They reflect short-batch sweeps (6 images) against worker pools, which incur tail penalties from idle workers waiting for the slowest image. Deep-queue testing shows performance gains of 13–15% over these sweep figures. Ratios between hosts remain largely valid, but individual absolute values should not be used for corpus ETA estimates without deep-queue adjustment.
*   **Caching Artifacts:** Mothership’s baseline (1.50 img/s) is optimistic due to caching benefits from repeated control images in the sweep. It likely overstates throughput for varied corpora. Weight this figure lower when estimating real-world performance.
*   **Comparability Requirement:** New hosts may only be added to this table if tested with the same Tesseract harness, identical images, and `tessdata_best` data.

### Recorded Throughput Data
| Host | Specs | Concurrency Sweep (Wall Clock) | Best img/s | Notes |
|---|---|---|---|---|
| **Mothership** | 16 cores | 1w: 5.5s · 4w: 1.43s · 8w: 0.89s · 12w: **0.67s** | **1.50** | Optimistic end; caching benefit present. |
| **BigHonker** | i5-9600K (6c/6t) | 1w: 7.52s · 3w: 3.19s · 6w: **2.18s** · 8w: 2.18s | **0.458** | Saturates at 6-way; no HT gain at 8-way. Baseline for "kicking near BH" rule. |
| **Fellowship** | 5 nodes (32 cores)† | Per-node rates summed (§10) | **~0.36–0.40** | ⚠ Stale: Predates 2026-08-13 re-baseline. Table shows 5-node subset. |
| **mordecai** | i7-3770 (4c/8t) | 1w: 0.063s · 2w: 0.113s · 4w: 0.135s · 6w: 0.141s · 8w: **0.145s** | **0.145** | Added 2026-08-16. |
| **rigby** | i5-3470 (4c/4t) | 1w: 0.055s · 2w: 0.096s · 4w: 0.109s · 6w: 0.113s · 8w: **0.115s** | **0.115** | Added 2026-08-16. |
| **Basement Pair** | Combined Unit | 24 units dealt 13/11 by rate, joint wall-clock | **0.282** | 95% retention efficiency. Added 2026-08-16. |

† *Fellowship Status:* The table above reflects a historical 5-node measurement. The current roster is seven nodes ([[Tablet-Cluster-INDEX]]). A full 7-node fair-fight benchmark has not been run. Estimated throughput with leg folded in is ~0.45–0.50 img/s (at or slightly above BigHonker), excluding aragorn.

### Interpretation Guidelines
*   **BigHonker Baseline:** BigHonker’s CPU baseline is 0.458 img/s. This value serves as the reference point for the naming rule "kicking near BH (not including GPU)." Candidate boxes are placed on this axis by running the same OCR harness; BigHonker need not be re-benchmarked.
*   **Efficiency Comparison:** Mothership is ≈3.3x BigHonker and ≈15–18x a single tablet. The Fellowship reaches 87% of BigHonker’s throughput but achieves roughly 2x img/s per watt (~35 W fleet vs ~95 W CPU TDP). This efficiency advantage validates the tablet cluster approach against simple PC replacement.
*   **Ratio Validity:** Do not rely on the third digit of ratios derived from this table. Tail penalties scale with workers-vs-batch-size, introducing non-linear variance between hosts tested with different concurrency settings.

## Local tiny-model LLM inference (llama.cpp CPU, measured 2026-08-27)

Tiny LLMs run on the Fellowship. This retires the "1.76 GB is too small to load any lab model" claim
(it was over-broad; the fleet's 8B/27B genuinely do not fit, tiny models do). Measured on all 7 nodes,
warm + `termux-wake-lock` held, `llama-bench -p 128 -n 64 -r 5` (llama.cpp build `70adb1b`), Q4_K_M
GGUFs. Instrument + durable aarch64 binaries: `Lab\fellowship\bin\` (llama-cli, llama-bench, libomp)
and `~/llm/tiny_arm.sh` on each node. Full per-node table + quality/thinking notes:
2026-08-27-claude-fellowship. This is #34 area B, first move ([[34-CHARTER]]).

**Generation throughput (tg64, tokens/sec) by hardware class:**

| model (Q4_K_M) | 2.79 GB X6C6F (aragorn/gandalf) | 1.76 GB X606F (frodo/legolas) | 4-core X505F (merry/pippin) |
|---|--:|--:|--:|
| MiniCPM5-1B (651 MiB) | 9.4–9.9 | 8.5 | 5.9 |
| Qwen3.5-0.8B (497 MiB) | 7.7–7.9 | 6.4–7.1 | 4.9 |
| Qwen2.5-1.5B (935 MiB) | 5.4–5.8 | 4.8–5.2 | 3.5 |

Prompt-processing (pp128) is ~17–18 t/s on octa-core nodes and ~9.5 on the 4-core nodes — **the
4-core penalty falls on compute-bound prompt-processing, not on generation.** The **2.79 GB X6C6F
nodes are the fastest, from a faster SoC, not just more RAM.** (`sam` runs ~25% slower than its
1.76 GB siblings — a node-specific degradation, not the class; use frodo/legolas as the 1.76 GB anchor.)

**Model-size ceiling — graceful, because the GGUF is memory-mapped.** A model larger than free RAM
still runs (pages fault from storage), degrading rather than hard-failing: Qwen3.5-2B (1.18 GiB) runs
on the 2.79 GB nodes (tg 4.16) and even on the 1.76 GB nodes (pp 6.23 vs 7.89, ~20% slower). 4B and
above thrash. **Practical sweet spot: ~1B on the small nodes, ~1.5–2B on the big nodes.**

**Method invariants (or the numbers are fiction):** hold a `termux-wake-lock` (cold nodes park the
little cores at 400 MHz → up to 12× throttle) and warm the node before measuring; discard the first
(load-inclusive) round; report ranges. Per-node CPUs are independent — the USB bus contention in
[[Tablet-Performance-Benchmarks-Bus-Contention]] does NOT apply to local single-node inference, so the
whole roster can be benchmarked concurrently.

## Phase 4 tournament — division champions

Champions are per-division (a model good at one is often poor at another). The tok/s + RAM ceiling are
above; these are the QUALITY winners — judged on WORK (executed code, clean structured output), not
public benchmarks. Full chat/tool scorecards: 2026-08-27-claude-fellowship; coding:
2026-08-29-claude-fellowship.

| division | champion | score | notes |
|---|---|---|---|
| **Chat / Generalist** | class A → **Qwen3.5-2B no-think**; class B → **Qwen2.5-1.5B** | 8/10 each | thinking models impractically slow on tablets (~100 s/answer); **CoT is load-bearing below 2B** — thinking-off collapses a small thinker to 3/10. Qwen3.5-2B holds reasoning no-think; Qwen2.5-1.5B (a gen old) is the class-B fit. |
| **Coding** | **Qwen2.5-Coder-1.5B-Instruct-Q4** | **7/8, all 3 classes** | see below |
| **Tool / Agent** | **Qwen2.5-1.5B** (non-think) | 6/6 sel · 6/6 args · 2/2 decline | 3 models tie at ceiling on basic routing (Qwen2.5-1.5B, Coder-1.5B, Qwen3.5-2B); MiniCPM lags (0/8 native JSON, over-declines). Grammar-constraint near-free for strong models, load-bearing for weak. Detail below. |
| **VLM** | quality → **Qwen2-VL-2B**; deployable → **SmolVLM2-500M** | Qwen2-VL **6/7** · SmolVLM-500M 5/7 | class A (aragorn). Qwen2-VL uniquely reads spatial-color; SmolVLM-500M is the only one that FITS class-A RAM (stable, ~26 s/q) — the 2B models spill to swap and OOM-killed sshd back-to-back. Detail below. |

*§45–48 (respect-plans / correction / failure-recovery / blind-reviewer) are eval **methodology** folded into
each division's scoring, not a separate division (charter §44–48) — the four divisions are Chat, Coding, Tool, VLM.
**Evaluated 2026-08-29 — see "Behavioral evaluation" below.***

### Coding division (detail, 2026-08-29)
8 executable tasks (4 impl · 2 debug · 1 modify · 1 parse), generated on the tablet and run against
hidden asserts on rigby (`~/cluster/code_tourney.py`; deterministic verification, no LLM judge). Solve
= every task check passes.

| model | frodo B | gandalf A | merry C | aragorn A | pippin C | legolas B |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| **Qwen2.5-Coder-1.5B** | **7/8** | **7/8** | **7/8** | — | — | — |
| Qwen2.5-1.5B (general) | 7/8 | — | — | 4/8‡ | 7/8 | — |
| Qwen2.5-Coder-0.5B | — | — | — | — | — | 4/8 |
| Qwen3.5-2B no-think | — | 0/8‡ | — | 4/8‡ | — | — |

1. **Champion = Coder-1.5B, a consistent 7/8 across all three classes** (frodo/gandalf/merry —
   *byte-identical scorecards*, only `fizzbuzz` 3/4). Direct proof that **coding quality is
   SoC-independent**: deterministic aarch64 output at fixed seed+temp → the same code → the same score
   on any chip. Only *speed* varies by class (the tok/s tables above).
2. **The specialist ties the general model on correctness** — Qwen2.5-1.5B (the chat/tool champion) is
   also 7/8 clean (its miss is `parse_kv`, the Coder's is `fizzbuzz`). The coding-tuned specialist is
   the division pick at equal size + correctness.
3. **~1.5B is the coding sweet spot:** Coder-0.5B → 4/8 (fails the impl tasks, too small); Coder-3B is
   over the class-A RAM ceiling (OOM'd gandalf's sshd).
4. **No-think hurts coding:** Qwen3.5-2B with thinking off failed the debug/modify tasks (0/3 each on
   its clean run) — the CoT-load-bearing finding, in the coding division.

‡ **Anomalous runs, not quality signals** (fixed seed ⇒ deterministic output, so a differing score is a
transient generation failure to re-run, not a class effect): Qwen2.5-1.5B @aragorn 4/8 (vs 7/8 on
frodo+pippin), Qwen3.5-2B-nothink @gandalf 0/8 (produced no output). Per-task wall time was not captured
this run, so no speed claim is drawn from this data.

### Tool/Agent division (detail, 2026-08-29)
The §21 division that got only a one-task footnote on 08-27, now run to its criteria — charter calls it
"potentially the most valuable to Tolkien." 8 tasks under TWO conditions each: 6 tool-fit (restart_service ×2,
read_log ×2, query_db ×2 — gold tool AND gold args literally extractable from the request) + 2 no-fit (correct
answer is the `"none"` sentinel). **grammar-ON** = a hand-written GBNF (`tool.gbnf`) constrains output to
`{"tool":<enum incl "none">,"arguments":{…}}`; **grammar-OFF** = native-compliance control. Deterministic scoring,
no LLM judge. Harness `scratchpad\tool_tourney.py`, results `tool_results.json`. (Build 70adb1b's `-j/-jf`
json-schema flag is a broken stub — `Failed to initialize samplers` — so raw `--grammar-file` GBNF is the working
mechanism; smoke-tested before the field. Recovery-after-failure, the 6th §21 criterion, is covered by the
behavioral §47 test below, not repeated here.)

| model (node) | tool selection | argument correctness | admitting uncertainty | schema ON / OFF | median latency‡ |
|---|:-:|:-:|:-:|:-:|:-:|
| **Qwen2.5-1.5B** (aragorn) | **6/6** | **6/6** | **2/2** | 8/8 / 8/8 | 24.7 s |
| **Qwen2.5-Coder-1.5B** (gandalf) | **6/6** | **6/6** | **2/2** | 8/8 / 8/8 | 23.3 s |
| **Qwen3.5-2B** no-think (aragorn) | **6/6** | **6/6** | **2/2** | 8/8 / 8/8 | 31.9 s |
| MiniCPM5-1B (aragorn) | 4/6 | 1/6 | 2/2 | 8/8 / **0/8** | 14.0 s |

1. **Three models are ceiling-competent on basic tool-routing** — Qwen2.5-1.5B, Coder-1.5B, Qwen3.5-2B each 6/6
   selection · 6/6 args · 2/2 decline, schema-valid in BOTH conditions. The task set separates the weak model, not
   these three from each other.
2. **MiniCPM5-1B lags materially** — wrong tool on 2 fittable tasks (**over-declines** to "none"), leaves arguments
   EMPTY even when the tool is right (1/6 args), and — sharpest result — produces **zero** schema-valid JSON without
   the grammar: it spends its whole token budget on a `[Start thinking]` preamble and never reaches the object. Its
   low latency is a symptom of that under-generation, not a virtue.
3. **The grammar-OFF control is the real discriminator AND it validates the instrument.** The three strong models
   emit clean tool-JSON natively (8/8 OFF — including Coder's markdown-fenced ```json, which the balanced-brace
   extractor recovers); MiniCPM 0/8. A rubber-stamp scorer would give everyone 8/8 — the spread is proof it
   discriminates (a live negative control in-field, not just pre-run cases).
4. **Grammar-constraint is near-free for competent models, load-bearing for the weak one:** ON≈OFF latency for the
   strong three (the grammar only shapes already-clean output), while it is the *only* thing that gets MiniCPM to
   valid JSON at all. For a tablet tool-worker: always constrain — free insurance on the good models, a rescue on
   the marginal ones.

**Champion — Qwen2.5-1.5B holds** (already the chat-B champion; class-A resident, ceiling on every quality
criterion). Coder-1.5B and Qwen3.5-2B are equally ceiling-competent on routing; this run does not break that tie.

‡ **Latency is NOT a ranking signal here** — one round, confounded by node (aragorn vs gandalf) and model size
(MiniCPM is 1B, so "fastest" partly by under-generating). Only the deterministic quality scores (SoC-independent)
rank models. ⚠ **Confidence caveat:** the set is SMALL (8) and the fit tasks are EASY (tool name ≈ stated in the
request, args literally in the prompt), so three 6/6s mean "cleared a low bar," not "separable excellence." A harder
set (ambiguous tool choice, multi-arg, malformed requests) is the follow-on to rank the top tier — same class as the
behavioral eval's caveat below.

### VLM division (detail, 2026-08-29)
First VLMs to run on the Fellowship. `llama-mtmd-cli` (aarch64, already cross-compiled on rigby
`build-android/`, staged to `~/llm`) on **aragorn** (class-A 2.79 GB, camera-decode-free + detection
dormant → co-location safe). 7 deterministic tasks (number/OCR · device-screen · UI button · chart ·
scene-count · scene-color · last-word-of-line), code-checked answers, temp 0. Harness `scratchpad\vlm_tourney.py`.

| model | footprint (model+mmproj) | score | misses | speed (wall / vision-encode) | class-A fit |
|---|---|:-:|---|---|---|
| **SmolVLM2-500M** Q8 | 0.55 GB | 5/7 | scene-color, last-word | **~26 s / ~18 s** | **FITS RAM — stable** |
| SmolVLM2-2.2B Q4 | 1.7 GB | 5/7 | scene-color, last-word | ~160 s / ~120 s‡ | over ceiling (swap) |
| **Qwen2-VL-2B** Q4 | 1.7 GB | **6/7** | last-word only | ~80 s / ~44 s | over ceiling (swap) |

1. **Architecture beats size.** SmolVLM2 at 500M and 2.2B score *identically* — 5/7, the same two misses —
   so 4× the parameters fixed nothing. **Qwen2-VL-2B (a different vision encoder) is the only model that reads
   spatial-color** (lit traffic light: SmolVLM → "red", Qwen2-VL → "green"). The lever for tablet-VLM quality is
   the encoder family, not parameter count.
2. ~~**All three fail the positional read** ("last word of *the quick brown fox*" → all say "brown"). A consistent
   cross-model floor: tiny VLMs read the text but not word ORDER.~~ ⚠ **CORRECTED 2026-09-26 — this was the TEST,
   not the models.** The `t_word.png` cell was rendered wider than its 480 px canvas: the picture reads "the quick
   brown fo" with the last word cut off, so "brown" IS the last complete word in the image and the gold "fox"
   was never readable. Re-rendered to fit (`Lab\fellowship\vlm_set\make_t_word.py`, published as `t_word2.png`)
   and re-run 1:1 through Phase 5's 15 `t_word` windows: **SmolVLM2-500M answered "fox" on 150 of 150 tablet
   runs.** There is no positional-read floor in this evidence; "byte-identical misses across three models on
   one cell" was the inversion tell and should have pointed at the instrument. Consequence for the scorecards
   above: SmolVLM2-500M's honest synthetic score is **6/7** (only `p_light`, the lit-light colour, remains a real
   miss — gold checked by eye, the light is green, the model says red); the 2B models' 5/7 → 6/7 and Qwen2-VL-2B's
   6/7 → 7/7 are *expected* on the same reasoning but not yet re-measured (their re-run on aragorn is queued).
   Record: [[34-Phase-5-Appraisal-Layer1]] §0, 2026-09-26-claude.
3. **The 2B-class models are over the class-A practical ceiling.** Their 1.7 GB footprint exceeds ~2 GB free;
   back-to-back cold CLI loads stacked swap until the **OOM killer took aragorn's sshd** (adb-root control plane
   survived; recovered via the Tolkien `/fellowship/reboot` endpoint, 72 s — see 2026-08-29-claude-fellowship).
   They measure cleanly only one-inference-at-a-time with a recovery pause; ‡the 2.2B encode number is inflated by
   swap paging, not clean compute.
4. **Vision encode dominates every model** (~55–75 % of wall time) — the mmproj/clip CPU pass, not text gen. That
   is THE tablet-VLM cost; a persistent multimodal server (load-once) would amortize both the model load and the
   per-call encode, and is the obvious next step before any VLM tablet worker ships.

**Champion — a tradeoff, not one winner** (the owner starts P5 with this): **Qwen2-VL-2B** is the quality champion
(6/7, the only spatial-color capability — matters for camera-as-sensor observation, which is latency-tolerant per
[[Lorex-Camera-Architecture-Fellowship-Deployment]]) but must run single-inference with pacing, never back-to-back
on class-A. **SmolVLM2-500M** is the deployable champion — the only VLM that fits class-A RAM with headroom, stable,
3× faster. Scores are single-round (temp 0 = deterministic; the 500M's 3 rounds were byte-identical, validating that).

#### Real-world photo test (2026-08-29) — the division's synthetic-only gap, closed
The synthetic set (number/chart/UI/device-screen) never included a real photograph. Five neutral public-domain
photos from `pd12m-full` (fruit still-life · mountain landscape · sepia river · butterfly · yellow thistle), one
specific checkable question each (object-id / scene / medium / count / color), scored deterministically vs ground
truth set by eye (dataset auto-captions were unreliable — one called the sepia photo's sky "deep blue"). Harness
`scratchpad\vlm_real.py`; data `rigby:~/fellowship-data/results/vlm_real_results_20260829.json`.

| model | real-photo score | miss | speed |
|---|:-:|---|---|
| **Qwen2-VL-2B** | **5/5** | — | ~85–126 s / img |
| SmolVLM2-500M | 4/5 | medium (calls the sepia photo "color") | ~26 s / img |

1. **The synthetic champion order HOLDS on real photos** — Qwen2-VL-2B again the quality champion (perfect),
   SmolVLM2-500M the fast deployable one. SmolVLM's one miss is perceptual (cannot tell sepia is not colour),
   consistent with its synthetic weakness on spatial-colour.
2. ⚠ **Full-resolution photos break tablet VLM encoding.** Qwen2-VL's dynamic-resolution encode on a 1536 px photo
   ran **~15 min for a single image** on the tablet CPU (vision tokens scale with image area). Downscaling to 512 px
   longest side cut it to ~90 s with every checkable fact intact — **any tablet VLM pipeline must downscale
   on-device first**, alongside the persistent-multimodal-server point above.

### Behavioral evaluation (§45–47, 2026-08-29) — the eval methodology, run at last
The half of every division's tournament that the correctness rounds skipped: does a worker respect a plan,
take a correction, recover from a tool failure (charter §44–48)? 4 worker models (the chat/tool/coding
champions + MiniCPM as a thinking-model contrast) × 6 tasks (2 per dimension), class-A nodes. Harness
`scratchpad\behavioral_tourney.py`; correction tasks EXECUTE the fixed code (§40, deterministic).

| model | §45 respect-plan | §46 correction | §47 recovery | total |
|---|:-:|:-:|:-:|:-:|
| **Qwen2.5-1.5B** (tool/chat-B champ) | 2/2 | 2/2 | 2/2 | **6/6** |
| **Qwen2.5-Coder-1.5B** (coding champ) | 2/2 | 2/2 | 2/2 | **6/6** |
| **Qwen3.5-2B** no-think (chat-A champ) | 2/2 | 2/2 | **1/2** | **5/6** |
| MiniCPM5-1B (thinking) | 2/2 | 2/2 | 2/2 | 6/6\* |

1. **§46 correction is the trustworthy result (code executed): ZERO models got defensive, overcorrected, or
   reverted** — all four gave the clean minimal fix (celsius→212, maxof→larger). The rubric failure mode that
   most matters for a coachable worker; nobody tripped it.
2. **§45 respect-plan: all four stayed in scope**, no needless redesign (Coder-1.5B the most disciplined).
3. **§47 is the one separator: Qwen3.5-2B re-runs the failed call** on a not-found error ("retrieve ID 42
   *again*") = the rubric's "repeat blindly". The others adapt; MiniCPM reasons best ("we cannot call get_user
   again — ask the user"). Qwen3.5-2B was BEST on the timeout case (retry w/ increased timeout), so its weakness
   is specific to not-found, not recovery in general.
4. **Behavior dethrones no correctness champion** — it CONFIRMS Qwen2.5-1.5B and Coder-1.5B as clean workers,
   and adds one caveat: **Qwen3.5-2B (chat-A champ) is weaker at failure-recovery** — carry it before that model
   does agentic/tool work.

⚠ **Confidence caveats (honest):** the task set is SMALL (2/dimension) and EASY, so a 6/6 means "cleared a low
bar," not "excellent" — weak discrimination; a harder, larger set is the follow-on. §47 is JUDGMENT (the
auto-checker misfired 3×; scores are eyeballed against the rubric, answers stored verbatim to re-judge).
\*MiniCPM's verbosity (all thinking-traces) is a **speed** caveat under §48, NOT a behavior penalty — it reasoned
correctly and recovered best; not docked for the token budget. Instrument lesson: the first run scored the
ECHOED PROMPT, not the model's answer (all-4-identical was the tell) — validate against a known positive before
trusting a negative. History: 2026-08-29-claude-fellowship.

### Phase 5 (ensemble) — first cut
Qwen2.5-1.5B, seeds 1–5 across 5 nodes (one independent sample/node, temp 0.7): majority-of-5 **rescues
a random error** (bat_ball — one node wrong, outvoted 4–1) but **cannot fix a systematic one** (sisters —
all 5 share the misconception). Consensus helps sampling noise, not a consistent blind spot — the case
for heterogeneous specialists (§22–27). Phase 5 proper is the owner-gated.
