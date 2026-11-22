# #36 — Split inference across devices: helpers READ and DRAFT, the main machine WRITES (rigby + tablets first, then Mothership + the lab) — "Voltron v2" design, opened 2026-10-10

**Project #36, a child project of campaign #34** ([[PROJECT-REGISTRY]]; parent charter [[34-CHARTER]]; epic: Delegation).
**Status: CLOSED 2026-10-16 — findings, final numbers and hand-offs: [[36-Closeout]].** The status text below is the
state at close. **Measured through round 76 (2026-10-15). THE WHOLE BOX WORKS AT ONCE (r76): rigby's A3B ~16 tok/s
(−6 % with the 730 serving) + the Fellowship ~50 tok/s across 10 tablets + the GT 730 (`warm_gt730`, fastest node at
10.1 tok/s). Settled 10-15: engine = llama.cpp master `e467e5b` (CUDA 11.4 + graphs patch), MTP draft 2, ubatch 2048,
small-read threshold 256; the A1 runaway-turn guard (`turn_cap` 20,480, built and deployed 10-15); the GT 730 is a
warm-worker capability (`warm_gt730`) whose USE is decided by #34's re-pricing + Spock's table, not by hand; A1 context
compaction on all four factories (10-15). Left before closeout (§ "Where #36 stands" → "What is left"): three software
speed levers, ONE re-pricing on the final config, the full A1 set, the closeout write-up (generalizing up is #34
Phase 9's, fed by #36). Hardware levers (riser, RAM) are out of #36 (the owner 10-15).** Rigby got a GTX 1060 (10-13): the BIGGEST ADDER, not
the end of the helpers (the owner: *"I want it to be additive… we can put all of the other hardware to work in the same
fashion"*; rule 10). Rigby's A3B now **writes ~16.9 tok/s on code** (was 5–6 on the CPU) with the 1060 on CUDA + the
model's own **MTP head** (+19–24 %) and reads 80–110 tok/s. Inside that ONE stream the helpers no longer add (a tablet
reading slice would be the slowest stage), so they add BESIDE it: **the tablets' own work and rigby's A3B stack**
(r71: rigby −0–2 %, tablets −5 %), and the GT 730 serves as an 11th worker (`warm_gt730`) — the whole box measured
working at once in r76. Standard order: [[Fellowship-Order-Taker-Design]] § placement fields; how
the box got there and every lever's verdict: [[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]] (§ 7 for 10-14);
register row 10-13 14:3x. Where #36 stands against what it set out to do: § "Where #36 stands (2026-10-15)" below.
Superseded status texts: `Archive\docs-superseded-20261014-36-status.md`. Reopened by the owner 2026-10-10 ([[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]]
row 10-10 17:0x): *"I think we should reopen the stuff on the small end with Rigby and the fellowship … to really make a
small revolution for our lab."* And: *"this is really what I had in mind with Voltron and all of them working together,
but I had no way of describing it at that point."*

## Why the closed Voltron lost, and what is different now

Phase 6 (closed 10-02) pooled one model's LAYERS across rigby + 10 tablets over llama.cpp RPC: Coder-3B answered 21/30
at **1.18 tok/s** (~89 s per answer) against rigby alone 23/30 at **10.4 tok/s** ([[34-Hard-Bench-Order-Spec]] §
Voltron). In a layer split every GENERATED token crosses every device, so the slowest device sets the writing pace.
That question stays closed. This design is a different one.

**The idea that changes it** (sources below): a model's work has two halves with different limits.
- **Reading** the prompt (prefill) is compute-bound and parallel: helpers can share it.
- **Writing** (decode) is memory-bandwidth-bound and serial: a helper in that loop can only slow it.
- Agent work is reading-heavy (OpenRouter data in the Backburner video: ~24 tokens read per token written; the A1
  spec: the lead's cost around a delegated piece is mostly re-reading).

**Rule of the new shape: helpers never sit in the writing loop. The main machine is the only writer and checker.**

## Hypotheses scorecard after rounds 1–19d (2026-10-11, Qwen3.6-35B-A3B on rigby + 10 tablets)

- **The rule of the new shape holds both ways.** Reading shared: ~1.37× at 4K against rigby at its best ub (round
  24; the "1.57×" in rounds 14–19d compared rigby at ub 64). Writing through the split: 3.6× SLOWER (round 18,
  1.4 vs 5.04 tok/s) — Voltron's closed result reproduced. llama.cpp does not separate the halves, so the design
  needs a **read-split → write-on-rigby handoff — BUILT and working (round 23: slot save 282 MB / 4.2 s, restore
  0.1 s, writer re-reads 1 token, writes 4.96 tok/s)**. Tablet arithmetic makes greedy text diverge early (char 10 vs
  522 with a rigby-only reader); its quality cost is unmeasured.
- **Rounds 25–26 (2026-10-12):** split speed FALLS as ub grows (ub 64 25.05 · 128 24.5 · 256 21.6 · 512 17.6 tok/s) while
  rigby alone rises with ub — ub 64 or lower is the split's setting (32/48 queued, round 27). Fair 32K baseline: rigby
  alone 12.83 tok/s at ub 2048, so multi-rigby 14.42 is ~1.12× and the single-rigby plan (12.76) ~1.0×. **Quality cost of
  tablet arithmetic: none measurable** — KLD vs rigby's own base: split 0.0114 (same top token 93.7 %) vs rigby-against-
  itself noise floor 0.0111 (94.1 %); 16 chunks × 512 of one text, a first check. The early text divergence is greedy
  drift, not damage. A preliminary trace-based estimate of the ub sweep (30/27/26) was wrong by up to 50 % — trace
  periods are not result rows.
- **Rule 2 refined:** each tablet reads ~6× slower per MiB than rigby, yet 10 together pay — aggregate capacity
  decides the gain; per-device speed sets slice size. Class A = class B per MiB (0.0233 vs 0.0227 ms/MiB/token);
  A's edge is memory; class C 1.7× slower.
- **Rule 1 refined:** a WINDOW, not "bigger is better", against rigby alone at its best ub: ~1.34× at 4K (25.05 vs
  18.7), ~1.09× at 16K (17.0 vs 15.66), ~1.12× at 32K with multi-rigby (14.42 vs 12.83; single-rigby plan ≈ 1.0×).
  The 15-stage layout at 16K hung in round 27 (probable cause: a tablet's battery died) and is unmeasured.
  Battery matters: sam and faramir drained on 500 mA ports under load (governor log) — a battery-margin preflight is open.
- **Rule 3 (USB 2):** not the reading limit — linear-layer stages ran identically at 4K and 32K.
- **Job 3 (context archive) — no use at rigby's scale:** attention on a tablet costs ~15× rigby's at 32K, and rigby
  is not short of room (23 GB free).
- **Rule 8 (GT 730):** lost as a reading stage (round 15); cannot hold the attention layers inside a pipeline
  (whole contiguous layers only; -ot turns pipelining off) — a writing-side helper only.
- **Ceilings:** at 4K the limit is MEMORY — balanced by speed alone the fleet could reach ~2.7×, but tablets hold
  only 4 (A) / 2 (B) / 1–2 (C) layers, so rigby keeps ~half and paces the split at ~1.37× its best (lever: more layers per tablet —
  a smaller quant, parked by the owner; or more tablets). At 32K the limit is ATTENTION on tablets (lever: multi-stage
  rigby, blocked on the 15-stage abort).
- **Method record:** the agreed order was writing → 16K window → attention placement; the 16K run was skipped for
  the build. The planner is within ~5 % when its samples are clean; two of my estimates were wrong (rigby alone at
  32K; the 1.85× from rates polluted by writing rows — fixed, gate 731).

- **WRITING AT DEPTH is rigby's biggest cost, and the design left the card out of the writer (2026-10-12/13, from the
  133 A1 MoE logs already on rigby; read-only).** 55 % of the tokens rigby writes in A1 are written past 12K of context,
  only 12 % under 4K. Writing speed from near-pure-writing calls (≤ 64 new tokens in, ≥ 64 out), CPU-only vs GT 730
  attention (`-ot` the 10 attention layers + KV on the card, `--no-op-offload`): <4K 5.14 vs 4.10 · 4–8K 3.93 vs 3.83 ·
  8–16K 2.71 vs 3.43 · 16–32K 2.13 vs **2.97 (+39 %)** · >32K 1.52 vs **2.53 (+66 %)** tok/s — PRELIMINARY (the CPU side
  is 18 calls; agrees with the 10-07 probe, +25 % at 12K). At these speeds writing is ~60 % of A1 wall on rigby, not
  T1's ~39 % (which assumed 5 tok/s). 103 of the 133 A1 runs already wrote with the card; the handoff design's writer
  (rigby alone, CPU) did not. **Production writer = rigby + the card's attention**, and the split's reading gain should be
  quoted against that config (it reads slightly slower than CPU-only: 13.6 / 10.8 vs 14.4 / 12.3 tok/s at 2K / 12K).
  Both builds default `-fa auto`. **Round 32 (2026-10-13, MEASURED):** the read→write handoff restores into the CARD
  writer (16K: 534 MB state, save 8.5 s, restore 0.4 s, writer re-reads 1 token, writes 3.49 tok/s vs its own control
  3.38); the 10-tablet split read 16K at 15.74 tok/s vs 13.59 on the card writer alone (1.16×) and vs 15.66 on rigby's
  CPU alone (round 29, a tie — the plan lacked `--attn-ms`). So the handoff's value is choosing the best config per
  phase: read where reading is fastest, write on the card. **Quantized KV LOSES on the GT 730** (24K, n=2): f16 3.12 ·
  K q8_0 2.55 (−18 %) · K+V q8_0 2.10 (−33 %) tok/s — keep f16. Helpers from one layer up
  (BigHonker as a reading stage) are OUT — the owner, 10-12: per the ladder, work that needs more moves up, it does not
  borrow a bigger box.

**Review against the owner's original ideas + the two videos, with the tests it implies (T1–T6):**
[[36-Hypothesis-Review-and-Video-Crosscut-2026-10-12]].
**After the GTX 1060 (2026-10-13) — rigby's A3B now reads ~75–85 and writes ~12 tok/s on the card + CPU; what binds
each half, the community research on mixed-power cards, the levers left and every device's role:**
[[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]].

## Where #36 stands (2026-10-15) — against what it set out to learn and build

#36 has no charter of its own (rule 6's note: the 10-10 conversations act as one); it serves [[34-CHARTER]] area B
(Fellowship experimental compute) and its purpose line (*"the lab itself is the AI"*, rigby as the Fellowship's lead).
Measured against the owner's five ideas ([[36-Hypothesis-Review-and-Video-Crosscut-2026-10-12]] § 1) and this doc's jobs:

| set out to | verdict 2026-10-15 | evidence |
|---|---|---|
| **1. Shared reading** (tablets help rigby read) | **Learned: works, modestly — then outpaced.** ~1.34× at 4K, ~1.1× at 16–32K against CPU-era rigby; no quality cost (KLD 0.0114 vs floor 0.0111); the read→write handoff works. The 1060 now reads 80–110 tok/s vs the split's ~25, so a tablet slice would be the slowest stage. | rounds 14–32, r40, r51 [M] |
| **2. External drafting** ("external MTP") | **Learned: superseded by the model's own MTP head on rigby** (draft 2: 94–100 % acceptance on code, +19–24 % write; draft 3 loses, r72a). The tablet drafter lost (1.87 vs 2.38, 14B). | r1, r67/r68, r72a [M] |
| **3. Small models fast on tablets** | **True — as independent workers, not rigby's helpers.** The GT 730 is the fastest warm node (0.8B at 10.1 tok/s; tablets 6.5–6.9). | r76; [[Fellowship-Performance-Baselines-and-Benchmarking]] [M] |
| **4. Roles per device, at once** | **Answered.** Helpers INSIDE one answer's writing loop lose (Voltron reproduced, 3.6× slower). Independent work BESIDE rigby stacks: tablets beside the A3B cost rigby 0–2 % (r71); the whole box at once = A3B ~16 tok/s (−6 % with the 730 serving) + the Fellowship ~50 tok/s across 11 nodes (r76). | round 18, r71, r76 [M] |
| **5. Generalize up** (Mothership + the lab below) | **Not started.** BigHonker as a reading stage is OUT (the owner 10-12, ladder doctrine). MTP already runs on Mothership's 3.8. Untested: the context archive for the hybrid 3.8 in Softserve's RAM. Mothership's RAM swap lands Friday 10-16. | register 10-12 [M] |
| **Job 3, context archive** | **No use at rigby's scale** (tablet attention ~15× rigby's at 32K; rigby not short of room). | round 16 [M] |
| **Production — Tolkien serves it** | **Rigby's REAL work now runs on the 1060** (r70, A1 lead on the standard order): wordy 11/12 in 210 s and bowling 13/15 in 767 s, against 2,556 s and 1,564 s on the GT 730 writer config (10-09). react and paasio each ran away (one 25–32K-token no-tool turn to the context cap) → the **A1 runaway-turn guard** (`turn_cap` 20,480, from 1,749 measured calls; built + deployed on all four factories 10-15, live decoy passed; react + paasio validation in flight — react 12/14 hidden at 60 min, no runaway, when a deploy paused it). Warm orders serve each node as it comes up; the 730 is a warm-worker capability (`warm_gt730`, opt-in — WHETHER it is used is #34 pricing + Spock's table). | r70, r76, gates 802–806; register row 10-13 14:3x |
| **"Every drop" from the whole box** (the owner 10-13) | **Single stream: at the wall today** — 16.8–16.9 tok/s write (public twin rig 17). This round: MTP draft 3 loses, `ubatch 4096` reads +30–42 % but writes −19–38 % (2048 stays), llama.cpp master `e467e5b` = the old build ±1 % (adopted), hot-expert pinning closed (+4 % ceiling). **The lever list is NOT exhausted** — 13 of 38 done (`lab-engine\research\split-inference\rigby_arsenal.json`); see next steps. | r72a–r74a, r61–r64; arsenal [M] |
| **Energy** | 1060 decode ~+85 W over idle (n=4), CPU path +58 W; the menu's flat 54 W is right only for CPU-path rows. | `energy_history.py` pairings, register [M] |

**What #36 learned that the lab did not know on 10-10:** inside ONE answer, helpers pay only where they hold compute
the main machine lacks, so a cheap GPU on the main machine outran ten helpers at that job — and the helpers then add
BESIDE it with their own work (the box's capacity is the sum, measured to stack, r71/r76); MTP's value is decided by
acceptance vs check cost; MoE routing is skewed but task-specific; rigby's real limits are its CPU (no AVX2, DDR3) and
the x1 link; on hard tasks with thinking off, the A3B can deliberate itself into the context cap in ONE turn — a guard
sized from measured turn lengths contains it.

**What is left before #36 closes (2026-10-15; the owner: *"Don't worry about ram and the riser. Focus on the stuff we
actually have."* — hardware levers are out of #36: the riser, `ram_speed`, `large_pages`):**
1. ✅ **DONE 2026-10-15 — the speed evolution is over; every arsenal entry has a verdict (`rigby_arsenal.json`: 0
   open).** `prefix_cache_reuse` CLOSED from data, no run (the A1 react+paasio order: 104 calls re-read a median 2.0 %
   of the prompt, at most +23 tokens beyond what each turn appended; `a1_cache_reuse.py`); `async_sched_copies` CLOSED
   not run (PR #25319 open + conflicting; its own P40 = Pascal sm_61 table: MoE writing 1.00×); `cpu_gpu_overlap` OUT
   (no knob — lossy deferral or an engine project). The plan as it stood:
   **The software speed levers that matter for rigby's role**, evolutionary (most promising first, one replicate, sized
   to the decision): `prefix_cache_reuse` (coarser reuse at ub 2048 — A1 re-reads its whole conversation every turn),
   `cpu_gpu_overlap` (the CPU and GPU halves are balanced, 37 vs 37 ms), `async_sched_copies` (~5 % reported).
   The arsenal's other open entries get a written verdict instead of a run: `concurrency` (serving-only, priced in
   step 2), `kv_quant` / `hand_ncmoe` / `sparse_attention` (low value at rigby's scale, reasons on file),
   `expert_cache` / `hotness_placement` (predicted loss on the x1 link), `asymmetric_cook` (a re-quant - the owner's call),
   `fitted_args_record` (an instrument gap: record the fitter's expert split per order).
2. **Re-price ONCE on the final config** — rigby's rows on the 1060 at ~85 W, the GT 730 as a warm-worker row with the
   A3B's −6 % co-location caveat, the plain GGUF archived to the HDD; then Spock's `policy.json` sets the defaults.
   **Scope (test the role; the charter's "no work with obvious answers"):** the A3B on the standard 1060 order across
   rigby's five families (f1, f2, W1, C1, tool) — rigby's role model. The other 11 models keep their CPU-era rows (true
   prices for the CPU path). **The menu cannot express it yet (found 2026-10-15, class 17):** (a) rigby's cell key
   (box, model, family, thinking, temperature, seed) has no PLACEMENT, so 1060 rows would merge with CPU rows;
   (b) `menu_fill.rigby_cell` drops any row with `spec_type` as "outside the grid" — every standard-order row (MTP
   draft 2) would be silently ignored; (c) `menu_policy.RIGBY_MARGINAL_W` is one flat 54 W (right for CPU rows,
   ~1.6× low for 1060 rows). Build those three with tests, then fill.
   **RUNNING 2026-10-15 17:53** — built (`menu_fill.RIGBY_PLACEMENT`: the MTP file's standard-order fields + 85 W,
   read by `menu_policy`; its draft-mtp rows are grid rows; tests 119 OK). The menu fill's whole-box rigby hold (the owner
   10-04: rigby's one-shot grid did its job; roles are measured in A1) is NARROWED, its words kept, to exactly the
   shortlisted lead: the MTP file thinking-off (20 cells, 5 families × 4 knob cells); its thinking-on arms and the
   other 11 models stay held (backup `menu_fill_hold.json.bak-20261015-36-reprice`). The 15-minute top-up carries it.
   **Item 3 DONE 2026-10-16 02:56** — `a1v22-rigby-moe-hai-off-spot-1060-final.20261015` (r70's 10-task spot set + the
   control, standard order, `turn_cap` 20,480, compaction): **6 of 10 tasks solved, control passed**, 11/11 measured,
   not void. Solved: wordy, bowling, poker, robot-name, transpose, zipper. Failed: react 6/7, paasio 9/12, rest-api
   0/4 (priced out at the measured 10,800 s ceiling, 2 compactions), sgf-parsing 6/11 (ended by the runaway guard, 2
   cuts, 10,255 s). **0.70 solves/h** over 8.54 h of serial task work (sum of the 10 rows' walls — the 20:37 unplug's
   resumed rows carry their pre-restart time, so the order's own 6.28 h wall understates it); at the placement's
   85 W that is ~436 kJ/solve (menu wattage, not metered on this order). 0 cap hits. The only earlier complete,
   non-void run of this set: the CPU-era team arm `…team-fe-dir-gt730…20261008-012803`, 6/10 in 10.33 h = 0.58
   solves/h — same solves, 17 % less time (two variables differ: the 1060 placement AND solo vs team). sgf-parsing landed within 0.15 h
   of `RIGBY_A1_CEILING_S`, the re-derive trigger in its docstring: still no rigby A1 item has passed past ~2.9 h.
3. **The full A1 set on the final config** (rigby's role number and its price; with `turn_cap` and compaction).
4. **Generalize up is #34 PHASE 9, not #36** ([[34-CHARTER]]: Phase 9 "generalize upward to Starfleet"; the owner starts
   each phase). #36 closes at rigby + the Fellowship and hands Phase 9 its starting input: § "Generalize UP" below (the
   four jobs one layer up; the 3.8's context archive the one untested idea) plus what #36 measured about which jobs pay.
   What else #36 feeds: its price rows → Phase 7's `policy.json`; rigby's A1-lead number → S10 (rigby leading the
   tablets, between Phases 7 and 8); rigby / the 730 as advisor hosts → Phase 8.
5. **Closeout** — a closeout write-up (the `34-Hard-Bench-Closeout` precedent) that names those hand-offs, the synthesis
   doc's final section, the arsenal's final statuses, register row 10-13 14:3x closed, the registry row CLOSED,
   CURRENT-PROJECTS, memory.

Detail of every lever: [[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]] and the arsenal file.

## The four jobs (one job per device at a time; jobs run SIMULTANEOUSLY across devices)

| job | what the helper does | why it can pay | what must hold (measure) |
|---|---|---|---|
| **1. Reading help** (pipelined prefill) | holds a slice of the model's layers and processes prompt chunks (256-token chunks; only the in-between state crosses, ~10 KB/token on a 27B) | reading is most of an agent's waiting | the helper must read its layers FASTER than the main machine would — Backburner gains only because the A19 Pro has matrix units the Mac lacks; an M1 iPad made reading 42 % slower |
| **2. External drafting** (the owner's "external MTP") | runs a small SAME-FAMILY model that guesses the next tokens; the main machine checks a whole guess in one pass | checking k tokens costs about one writing step on a bandwidth-bound machine; several helpers can draft different branches (a token TREE) checked in one pass | the drafter must produce its guesses faster than the main machine writes one token; the tokenizer must match; the network hop per round must be small (pipelining hides it: drafters keep guessing while the checker works) |
| **3. Context archive** | holds the OLDEST pages of the conversation's KV and does the attention math over them, returning a small partial result | adds ROOM, not speed — only on HYBRID models (Qwen 3.8: 16 of 64 layers keep full KV, ~34 KB/token at 8-bit) | the model must be hybrid; output must stay token-identical (Backburner checked it) |
| **4. Independent worker** | today's role: tablets answer their own small tasks (Phase 7 Fellowship tier) | — | unchanged |

**Working rules from the Backburner source (re-read in full 2026-10-10; no charter yet — the owner: the conversations in
his main session and this one act as the charter for now):**
1. **Helpers join only BIG reads.** In the developer's session only 7 of 36 requests were big enough for the phone to
   join, but they carried ~83 % of all tokens read. Reading help is routed by prompt size; small prompts stay on rigby.
   (So Voltron's ~65-token prompts never tested this design.)
2. **The layer split is sized to each helper's measured speed.** A newer phone given 32 of 64 layers gained 44–55 %; a
   weak helper (M1 iPad) made reading 42 % SLOWER. Per tablet class: class A takes more layers, the 4-core class few
   or none.
3. **The first read after a session loads pays extra** — the helper first needs the existing context for its layers.
   With a USB 2 cable and a 2-bit quant a tester's net gain fell +10 % → 0 → −4 % at 16K/32K/48K. The tablets are USB 2:
   measure the link, never assume it.
4. **Writing on rigby needs the cache for EVERY layer**, the helpers' slices included — what helpers read must reach
   rigby before it writes, and that transfer is part of the price.
5. **Drafting pays where "checking is reading, and reading is the fast part."** On rigby reading is only ~1.8× writing
   (14B) / 2.4× (Qwen3.6-35B-A3B), vs ~10× or more on a GPU — the measured reason step 1 underdelivered. A check round is
   a writing step, so it stays on rigby; helpers never sit in it.
6. **Five levers, each tested on its own** (the owner, main session 10-10: *"I'm kind of intertwining a few different ideas
   at once"*; main untangled them): reading help · external drafting · context archive · built-in MTP (lives with the
   big model, stays on rigby — none of rigby's current GGUFs carry one) · tablets as independent workers (Phase 7's
   Fellowship tier). One lever per order, against rigby's own baseline, so a gain is attributable.
7. **Target model per lever, not one for all** (the owner: *"the 7B on Rigby will probably fare better/faster than the
   14B"*). The 7B leads the reading-help work (rigby's practical model: 0.83 at 4.72 tok/s writing, 8.4 reading). The
   14B stays as the bar for drafting, the only model with a tablet-draft number. The MoE is the likely drafting target
   (cheapest check, rule 5) and the only context-archive target. The check-cost curve covers all three.
   rigby's measured speeds (best Tolkien order each, writing / reading tok/s): 0.5B **43 / 104–110**, MiniCPM5-1B
   **33 / 72**, 1.5B **20 / 39**, Coder-3B 10.4 / 19, Qwen3.6-35B-A3B 6.2 / 14.7, 7B 4.8 / 8.5, 14B 2.4 / 4.2. Any
   tablet drafter races a rigby that runs a 0.5B at 43 tok/s itself.
8. **rigby's GT 730 is part of "rigby and below"** (the owner: *"we can use the GPU that's on Rigby as well"*).
   [[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]]: as a whole-model GPU it loses (2.5–4.3× slower writing, ties
   reading — Kepler has no DP4A); holding the MoE's 10 attention layers + KV it wins at long context (12k: 2.7 → 3.3
   tok/s writing), `--no-op-offload` mandatory. For #36: a second READER inside rigby — if llama.cpp overlaps the
   CPU's layers with the card's, rigby + card reads faster with no USB hop; if it runs them in turn it only moves the
   work (the `bench` order measures CPU-only vs CPU+card layer splits). It pairs with the context archive (the MoE's
   attention already lives on the card). Too slow to be a drafter (~6.7 tok/s on a 1.5B vs the CPU's 43 on a 0.5B).
9. **Order the runs for answers, not completeness** (the owner, 2026-10-10: *"did we queue up our slowest tests first? …
   how many of them are controls that we already have answers for"*). New questions FIRST; numbers the lab already
   has are dropped, except ONE reproducibility cell (a known number re-measured with the new instrument, to tie it to
   the old ones), placed LAST and droppable. Round 1 (`bench-36-r1…`) broke this: ~11 of 34 results re-measured known
   reading/writing speeds, with the 14B second in line.
10. **Every drop out of the WHOLE box — a faster device never retires the helpers** (the owner, 2026-10-13, when the GTX 1060
   arrived and I said the tablets and the 730 "drop out": *"the tablets DID add to stuff… still worth doing… we should
   still be looking into how to use all of the Frankenstein box and not just be satisfied that there's a 1060 in it now.
   We want every drop out of these machines."*; 10-14: *"Ideally we can get everything pinned to maximum"*, and nothing is
   "superseded" — roles are REASSIGNED). The test is never "can a helper beat the new card alone" but **"what does the
   whole box do with every device in a role"**: measure the COMBINATION against the new best single device. Ways a helper
   can stack with a card: hold what does not fit on it, hold attention / KV / context, read-split long prompts, draft,
   or run its own independent work beside it. Applied 10-13/14 ([[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]]
   § 5, § 7): the 730 holding experts beside the 1060 measured −68 % (r43) and the MTP head beside it would cost more
   than it saves [I]; inside ONE stream the 1060 + CPU are at the wall, so the 730's and the tablets' drops are their own
   work beside rigby (Phase 7 Fellowship tier) — reassigned, not retired; that side-by-side is not yet measured.
11. **The target is ABSOLUTE speed — a fast lab — not a margin over a baseline** (the owner, 2026-10-14: *"Idc about finding
   exactly how much faster than baseline we are, i mostly care about how fast we can get… the end goal isn't just to
   get a good result vs baseline, it's to have a fast lab"*). A control exists only to tell whether a change HELPS; once
   it is clear, stop measuring the margin and spend the lane on the next lever. Wall-clock waste counts as speed too
   (e.g. a runaway turn that burns an hour).

MTP itself is NOT one of these: it is a tiny extra layer built into the big model (the 3.8 carries one), driven by the
big model's own last hidden state, so it lives where the big model runs. A standalone draft model is what can move.

**Per-tablet roles** (the owner: *"five of them do read and five of them do write, or something similar … breaking it up
per tablet"*): the split is assigned per tablet by job and decided by measurement, not fixed. The measured hardware
classes already point the way ([[Fellowship-Performance-Baselines-and-Benchmarking]] §tg64): the 2.79 GB octa-core
class writes small models fastest (Qwen3.5-0.8B 7.7–7.9, MiniCPM5-1B 9.4–9.9 tok/s) → drafters; the 4-core class is
weakest at compute-bound prompt reading (pp ~9.5 vs 17–18 t/s) → last pick for reading help; spare RAM suits the
context archive. Tolkien assigns roles, as it places work today.

## Drafting on rigby: ONE configuration measured and lost (2026-10-10) — not concluded (the owner: *"I still believe we can do it… We gotta choose the right models and settings. and do the prefill stuff."*)

[[34-Hard-Bench-Order-Spec]] (rigby, Qwen2.5-14B): plain **2.38 tok/s**; with a 0.5B draft ON rigby **2.86 tok/s (+20 %) at
0.88 acceptance**; the 7B 4.72 → 5.11. The guesses are good, yet the gain is small — and moving the drafter to a tablet
made it WORSE (1.87, step 1 below). The cause is the checker: verifying a 4-token batch costs rigby ~3× a single token
(batch work is compute-bound without AVX2; one fully-accepted row — to be measured properly as a batch 1/2/4/8 cost
curve), where speculative decoding assumes ~1×. If that holds, drafting on rigby is capped near today's +20 % unless the
CHECK gets cheaper — which is job 1's question (reading help) in another form. Levers still untested: draft length
(`--spec-draft-n-max` 1–2) and confidence cut-off (`--spec-draft-p-min`), sampling the draft on the tablet
(`--spec-draft-backend-sampling`, avoids ~600 KB of logits per draft token over USB), a target whose check is cheaper
(rigby reads the Qwen3.6-35B-A3B at 2.4× its writing speed vs 1.8× for the 14B), and pipelined drafting.
**Prefill help looks stronger than drafting:** an octa-core tablet's pp128 (~17–18 tok/s) is roughly 40–50 % of rigby's
prompt compute on a same-size model, so nine healthy tablets hold ~4–5× rigby's reading compute — usable only for
LONG prompts read in pipelined chunks. Voltron's 1.96 vs 19.07 tok/s reading (Coder-3B, ~65-token prompts, one hop
chain per prompt) measured hop latency, not that compute, so it does not decide this. rigby's other walls: READING (~2.3–4 tok/s on the 14B) and its context cap (the
Qwen3.6-35B-A3B cell was void at a 2,048-token cap) — job 3. Superseded estimate ("roughly 2×"):
`Archive\docs-superseded-20261010-36-drafting-estimate.md`.

## Production: TOLKIEN runs it (design 2026-10-10, not built — measurement first)

the owner, 2026-10-10: *"much of this is separate from Tolkien, the controller for Rigby and the fellowship. We need Tolkien
to be able to know about this stuff for final production."* The measurement tools are already Tolkien order kinds
(`bench`, `llm` + `draft_host`), but serving real work in the split shape is not built. What production needs, each a
Tolkien feature (Tolkien is one of the FOUR factories: every factory standard binds it):
1. **Engine per order.** An `engine` / `build` field on `llm` orders (the async-RPC build mounted at `/llama/next`),
   adopted as the default only once it has re-proven the live build's numbers and tool calling.
2. **Split-read serving on the rigby lane.** Tolkien starts the reading stages (rigby's CPU over localhost RPC, the GT
   730, helper tablets' async rpc-servers), a READER llama-server (every layer on a stage, pipelined) and a WRITER
   llama-server (rigby alone, prompt cache on). A request whose new prompt is above the measured threshold is read by
   the reader, its slot saved (`/slots` save) and restored into the writer, which writes (rules 1, 4). Below the
   threshold the writer reads it itself. Same API to the caller (A1 harness, desk pieces), so nothing upstream changes.
3. **Roles per tablet, assigned by Tolkien from measured speeds** (rules 2, 6): class A/B tablets as readers, the
   4-core class as independent workers; a helper LEASE like rigby's lane reservation, released when the writer idles,
   so Phase 7's Fellowship tier (warm workers) and split reading share the tablets through one arbiter.
4. **Declared capability** by detection (`rigby.llm.splitread`: next build mounted + enough healthy helpers), so the
   desk and Spock place long-reading pieces on rigby knowing it reads faster.
5. **Fail safe:** a helper that drops mid-read → the writer reads that request alone (WAIT/fallback, never consume the
   work); every stage server stopped and its port proved closed on every route; the tablet watchdog and the GPU
   police know stage rpc-servers as Tolkien's own.
6. **The deck shows it:** the rigby tile names the stages, whether llama.cpp turned pipeline parallelism on, and read
   vs write speed.

## Generalize UP (Phase 9, "generalize upward to Starfleet"; the 10-04 rule "ONE shape at every layer")

the owner: *"who's to say we couldn't do this with mothership with the rest of the lab below. It just like how we're doing
Rigby with the tablets below it."* The same four jobs one layer up, all to be measured:
- **Drafting for the 3.8** from a small same-family model on an 8 GB box (adds tree width beyond the 3.8's built-in MTP).
- **Reading help** from BigHonker (the 3070's tensor cores are real compute; a 256-token chunk of in-between state is
  ~2.6 MB, ~20 ms on gigabit) — only if it beats the 7900 XTX on its slice.
- **Context archive** in Softserve's RAM: the 3.8 is hybrid, so this could lift it past its 122,880-token ceiling.
- The 8 GB boxes keep their own jobs (HAI chat; Plex always wins on Softserve) — role assignment per device, as below.

Two cascades, complementary: Phase 7 moves whole TASKS down the layers ([[34-Phase-7-Placement-Policy-Design]]); this
moves the pieces of ONE answer across them.

## Measurement plan (each an ORDER, against the main machine's own baseline; n ≥ 2 before a verdict)

1. **One tablet drafts for rigby's 14B** (vs 2.38 plain / 2.86 local-draft). **Mechanism: stock llama.cpp, read in
   source 2026-10-10** (`common/arg.cpp`, master): `--rpc host:port` registers each RPC server as a backend device
   (`add_rpc_devices`), and `--spec-draft-device / --device-draft` takes any registered non-CPU device by name
   (`parse_device_list`). So rigby's `llama-server --rpc <tablet>:<port> --model-draft <0.5B> --device-draft
   <that RPC device>` puts the draft model on a tablet's `rpc-server` while the target stays on rigby. The tablets
   already ran `rpc-server` for Phase 6. **Confirmed 2026-10-10 on the object in force** (the `tablet-foundry`
   container's `/llama/bin/llama-server --help`, commit `70adb1b`): `--rpc`, `--spec-draft-device / --device-draft`,
   `--spec-draft-ngl / -ngld` all present; the tablets' arm64 `ggml-rpc-server` (`/llama/arm64`) is the same commit,
   so the RPC protocol matches. **As a Tolkien order:** `draft_host: "<tablet>"` + `draft_model` on the rigby arm
   (tolkien_llm, 2026-10-10). Tolkien starts that tablet's rpc-server on every core (its default is half), asks
   `--list-devices` for the device name and refuses unless exactly one RPC device answers, launches the target with
   `--device none -ngl 0` (`-ngl` defaults to auto, which would fit the 14B onto the registered RPC device = Voltron)
   and the draft with `--device-draft <dev> -ngld 99`, and kills + verifies the rpc-server on every route. The order
   holds rigby AND the tablet lane, so nothing else shares the tablet link during the measurement.
   **Expectation stated before the result:** stock llama-server drafts and verifies IN TURN, not concurrently, and
   rigby runs a 0.5B (~41 tok/s) far faster than a tablet does (0.8B ~7.8 tok/s on class A), so one tablet drafter is
   expected at or below the local drafter's 2.86. The ~2× estimate above assumes PIPELINED drafting (FlowSpec-style,
   drafter guessing while the checker works), which stock llama.cpp does not do. The order prices the mechanism and
   the per-round RPC cost; pipelining is the step after.
   **RESULT 2026-10-10 — one tablet drafting LOSES on rigby** (`hardbench-rigby-36a-qwen14b-f2-draft-gandalf.20261010`,
   identical to order 13 except `draft_host`; placement verified: rigby's llama-server 9.3 GB RSS = the 14B, gandalf's
   rpc-server 431 MB at `-t 8` = the 0.5B, device `RPC0`; cleanup verified, nothing left listening). Stopped as a known-bad
   arm at 23/73 (the results file says VOID by its own coverage rule; the verdict rests on the matched rows):

   | 21 matched rows (same task + seed) | gen tok/s median | range | acceptance | correct |
   |---|---|---|---|---|
   | order 13, 0.5B draft on rigby | **2.86** | 2.37–3.29 | 0.87 | 20/21 |
   | 36a, 0.5B draft on gandalf | **1.87** | 1.54–2.13 | 0.86 | 20/21 |
   | plain 14B (orders 8 + 10) | 2.38 / 2.36 | — | — | — |

   Slower on **all 21** rows, ranges do not overlap, below even no drafting. Same guesses (acceptance equal), so the
   loss is purely where the drafting runs: gandalf is an MT8768 (Cortex-A53, no dot-product), far slower at a 0.5B than
   rigby's own i7, and stock llama-server drafts and verifies in turn. Prompt reading also fell (4.04 → 3.63 tok/s on
   matched rows): the draft model must read the prompt too, on the tablet.

   **What this says about drafting on rigby at all (from the fully-accepted row `dbg_running_total`, 40 tokens, 30/30
   accepted):** a plain 14B token costs ~0.42 s; a round of 3 drafted + 1 costs ~1.27 s with the draft on rigby and
   ~1.9 s on gandalf. Verifying a 4-token batch on rigby costs ~3× a single token, not ~1× — batch work is
   compute-bound on an Ivy Bridge with no AVX2 (prompt reading ~4 tok/s ≈ its writing speed). **Speculative decoding
   pays when checking k tokens is nearly free; on rigby it is not, so rigby's checking step — not the drafter's
   location — caps drafting there.** The ~2× estimate above is withdrawn for rigby. (Preliminary arithmetic from one
   row; re-derive over every matched row before relying on the 3× figure.) Where checking IS cheap — a GPU checker,
   e.g. the 3.8 on the 7900 XTX, whose built-in MTP already pays — the external-drafter idea is still open (step 5).
   **ROUND 1 BENCH 2026-10-10** (`bench-36-r1-checkcost-prefill-link.20261010`, Tolkien `bench` kind, llama-bench
   `70adb1b`, r=3, 12/12 runs, every result count as expected; reproducibility: writing 7B 4.82 / 14B 2.46 / MoE 6.15
   tok/s vs the server orders' 4.80 / 2.38–2.43 / 6.19):
   - **Check cost on rigby** (a k-token batch, as a multiple of one token): 7B 1.5× / 2.7× / 5.3× at k = 2 / 4 / 8;
     14B 1.4× / 2.5× / 4.8×; **MoE 4.6× / 5.1× / 6.5× — the WORST, not the best** (my rule-5 guess from its 512-token
     read/write ratio was wrong: each token in a small batch routes to different experts, so a batch reads several
     tokens' worth of expert weights). The MoE is off the drafting list. Lever this points at: **draft 1–2 tokens, not
     the default 3** — from these numbers the 14B with 1 drafted token lands near ~3.2 tok/s vs 2.86 at 3 (arithmetic,
     unmeasured; needs a `draft_max` knob on `llm` orders). ⚠ The MoE's pp1 (2.58 ± 0.51) is below its tg64 (6.15) —
     noisy, not explained.
   - **Reading, same Qwen2.5-1.5B ruler, pp512:** rigby 39.3 tok/s · gandalf 9.41 (24 %) · frodo 8.86 (23 %) · merry
     4.88 (12 %). The 40–50 % estimate above was ~2× too high (it leaned on the Fellowship baselines' pp128 17–18,
     unchecked against pp512 — re-check that doc's figure). Aggregate: the 8 healthy octa-core tablets ≈ 1.9× rigby, so a
     speed-sized layer split could read the 7B at up to ~2.5–3× rigby alone — only if llama.cpp overlaps the stages.
   - **USB link (ssh over the wire, 128 MB each way):** gandalf 16.5 / 18.3 MB/s, frodo 16.9 / 18.3, merry 23.4 / 24.2.
     A 7B's in-between state is ~14 KB/token, so a 512-token chunk crosses in ~0.4 s while rigby needs ~62 s to read
     it: **the link is not the limit.** (merry faster than the class-A node: observed, not explained.)
   - **GT 730 as a second reader:** 7B pp512 CPU 8.19 · 4 layers on the card 8.30 · 8 layers 8.12 — **no gain**;
     stock llama.cpp runs the card's and the CPU's layers in turn. Its long-context MoE attention role is unchanged.
   - Order lesson: ~11 of 34 results re-measured known numbers (rule 9).
   **⚠ STOCK llama.cpp CANNOT OVERLAP READING STAGES ACROSS rigby's devices (read in source 2026-10-10, commit
   `70adb1b`, `src/llama-context.cpp` ~L427):** pipeline parallelism needs (1) more than one device, (2) EVERY layer
   off the CPU (`n_gpu_layers > n_layer`), (3) layer split, (4) NO tensor overrides (`-ot` disables it), and (5)
   async compute + events on every non-CPU device. The RPC backend (`ggml-rpc.cpp` ~L1888) reports `async = false,
   events = false`, so **any tablet in the split forces the stages to run in turn** — a speed-sized split onto
   tablets at ~24 % of rigby's per-layer rate would read SLOWER than rigby alone (Voltron's 10× read loss is this).
   The GT 730 cannot be an overlapping stage either: the 7B does not fit on it alone, rigby's CPU cannot be a stage,
   and rigby's Vulkan build has `GGML_RPC=OFF` (no card + tablets in one process). Round 1's card test (`-ngl 4/8`,
   ONE 512-token batch) therefore could not have overlapped anything; it measured in-turn execution, not the card.
   **So reading help needs a PIPELINED ENGINE, not a flag:** either a newer llama.cpp whose RPC backend reports
   async/events, or Backburner's own pipeline (layer split + 256-token chunks overlapped across devices) carried over.
   Next step is reading those two, not another bench order.
   **What the GT 730 CAN do with stock binaries** ([[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]], learned
   08-28 → 10-09): attention over a LONG cache (f16, no quantized matmuls — Kepler has no DP4A), with weights on the
   CPU and `--no-op-offload`: the MoE recipe (+25 % writing at 12k), 65k context fits. For the dense 7B the same idea
   is "all weights on the CPU, the KV cache + attention math on the card" — worth pricing at long depth, both for
   writing and for the check cost (checking k drafted tokens at depth includes attention over the whole cache, which is
   exactly the card's good work).
   **ROUND 2 (2026-10-10 evening) — overlap reached with a lab engine patch; tablets still too slow per chunk:**
   - Built upstream `23b0202` (async RPC, #18626) in its own worktree, mounted as `/llama/next` beside the live build
     (x86 + Vulkan with RPC on, `GGML_SCHED_MAX_COPIES=16`; arm64 rpc-server for the tablets, same commit).
   - Even so, stages ran ONE AT A TIME (live: gandalf 753 % → frodo 796 % → gimli 787 %, rest idle). Cause, read in
     `ggml-backend.cpp` `ggml_backend_sched_copy_input`: the RPC backend's `cpy_tensor_async` is NULL, so every stage
     hand-off falls back to `ggml_backend_synchronize(input_backend)` — the one host thread waits for each stage to
     finish a chunk before feeding the next. Upstream has no RPC `cpy_tensor_async` (checked 2026-10-10).
   - **Lab patch** `lab-engine\patches\rpc_cpy_tensor_async_patch.py` (82 lines, client-side, no
     protocol change — tablets untouched): RPC→RPC copies queued (same server: async `COPY_TENSOR`; across servers:
     a snapshot GET queued on the SOURCE right after the producing compute, then a SET run on the DESTINATION's
     dispatcher thread). Correctness: perplexity of a 1.5B split across two localhost rpc-servers (29/29 layers on
     RPC, pipeline on) **identical to CPU alone on every chunk** (4.8875). Trace instrumentation:
     `rpc_trace_patch.py` (`GGML_RPC_TRACE=1`).
   - **Overlap proven:** a 7B split over two localhost rpc-servers read 512 tokens in 46 s where in-turn stages would
     take ~88 s; the first stage's compute windows ran back to back (never waiting). No speed gain there by design (both
     servers share rigby's CPU).
   - **But rigby + 4 tablets (14 / 3.5 ×4 layers, 64-token chunks) read 3.11 tok/s vs rigby alone 11.84** on the same
     build and sizes. A tablet's real stage time per 64-token chunk is far above the 1.5B-ruler estimate (raw compute
     ~112 GFLOP at ~15 GFLOP/s ⇒ 7–11 s, not 4.6), and a pipeline runs at its slowest stage — yet tablet mean CPU was
     only 58–163 % of 800 %, so the slow stage is not yet NAMED. Next: the trace in Tolkien's stage runs (saved as a
     deliverable, summarised per stage), bigger chunks (128/256 — small chunks waste the tablets' cores), and a split
     balanced from MEASURED per-chunk stage times.
   - Telemetry built first time properly: Tolkien's telemetry logger (`tolkien-telemetry`,
     [[RUNBOOK-Foundry-and-Rescue-Daemons]] § "Tolkien Telemetry Logger") — `summary --run <name>` answers "stages busy
     at the same moment". Its blind spots, measured: the GT 730 reports no utilization and its work is filed under node
     rigby, so card ∥ rigby overlap cannot show; per-node counting merges rigby's two stage processes.
   **ROUND 3 instrument (2026-10-10 night) — the RPC trace is a Tolkien run field:** `trace: true` on a stage run sets
   `GGML_RPC_TRACE=1` (the `/llama/next` x86 + Vulkan `libggml-rpc` now carry `rpc_trace_patch.py`; off unless set),
   ships the whole stderr to `OUTBOX\trace\<order>\<run>.log`, and puts `tolkien_llm.trace_summary` per STAGE into
   each row: `median_ms` / `p90_ms` per chunk computed (a middle stage is read after every chunk; the LAST stage only
   once per pass, so its figure is window ÷ computes, an upper bound that equals its compute time when it never
   starves), `gap_median_ms` (time it waited for input), `copy_in_*` (hand-offs into it, NOT the time its posted copy
   sat waiting for upstream), `busy_share`. Pinned on the real two-localhost-server trace: 16 chunks per stage, 5.3–5.5 s
   each, copies ~ms, consistent with the 46 s per 512-token pass. The card stage is not RPC, so it has no trace row.
   Order `bench-36-r3-trace-name-the-slow-stage.20261010`: diag3 + trace (also a reproducibility cell, must read
   ~3.11), the same stages at 256-token chunks, rigby alone at those sizes last.
   **ROUND 3 RUN 1 RESULT (2026-10-10 22:41) — the stages STILL ran in turn; the cause is a second synchronous door,
   not a slow tablet.** `trace-diag3` reproduced diag3 (3.10 vs 3.11 tok/s, pipeline flag on). Per-chunk compute
   (64 tokens): rigby 3.0 s (14 layers) · gandalf 4.8 · aragorn 6.1 · frodo 6.4 · gimli (last) "18.1". The trace
   timeline shows rigby starting chunk k+1 at the SAME instant chunk k reaches the last stage, every chunk (0.0/20.0,
   20.0/40.0 …); each middle stage's compute + wait sums to the same ~20.3 s cycle and their busy shares sum to ~1.0.
   So gimli's 18.1 is the cycle (the last-stage figure is an upper bound), not its compute — gimli is not shown slow.
   Cause, read in `ggml-backend.cpp` 1817–1825 (23b0202): user inputs (positions, KQ mask) are copied with a
   SYNCHRONOUS `ggml_backend_tensor_copy`; on an RPC stage that write waits in the stage's dispatcher queue behind the
   posted activation copy, which waits for the previous stage, so the host thread walks the whole chain per chunk.
   Fix: lab patch 3 `lab-engine\patches\rpc_input_set_async_patch.py` (non-weight `set_tensor` posted with
   `send_async`; the bytes are already copied into the message, and each server's queue is ordered). To prove: the
   perplexity check, then the same traced run — rigby's next chunk should start ~3 s after its last, not ~20 s.
   **ROUND 4–5 (2026-10-10/11) — overlap REACHED; rigby + 5 tablets reads 1.26× rigby alone.** Patch 3 alone gave
   3.76 tok/s (still in turn). The second drain: `llama-context.cpp` ~1428–1435 — when a chunk REUSES the previous
   graph, llama synchronizes every backend before setting inputs, and an RPC `synchronize` returns once each queue has
   SENT its work, so the host held each chunk until the previous one reached the last stage (round 4's one
   new-graph chunk was its one overlapped chunk). Upstream switch `LLAMA_GRAPH_REUSE_DISABLE=1` = Tolkien run field
   `graph_reuse: false`. Full source audit of per-chunk host waits (23b0202): stage→stage copy (patch 1), input writes
   (patch 3), reuse drain (switch); realloc / output_reserve / embd syncs are not per chunk; RPC `graph_compute` posts
   and `event_wait` is a no-op. **Lab patch 4** `rpc_hostwait_trace_patch.py` logs every host wait ≥1 ms; trace rows
   carry `host_waits` (one per pass = the end-of-pass sync; more = a remaining drain, or — when chunks per pass exceed
   the 16 input copies — the host simply running 16 chunks ahead, which vanished at 128-token chunks).
   Results (`bench-36-r5-all-drains-off.20261010`, 7B Q4_K_M, n=1 each):

   | run | tok/s | note |
   |---|---|---|
   | diag3 split (rigby 15 + 4 tablets), reuse off | **7.45** | was 3.10 / 3.76; rigby starts each chunk 1.3 ms after the last |
   | rigby 19 + aragorn/gandalf/frodo/legolas/gimli, 2048 tok, ub64 | **14.09** | rigby the bottleneck: 4.06 s/chunk, 94 % busy |
   | same, ub128 | 13.35 | every stage exactly 2× — bigger chunks do not help the tablets |
   | rigby alone, 2048 tok, ub64 / ub128 | 11.18 / 11.74 | the reference |

**Sequencing with Phase 7:** no hardware conflict with the placed window (8 GB boxes + Mothership). Settle rigby's new
speed BEFORE pricing Fellowship/rigby pieces for the cascade arm, or the cascade prices a slower rigby. rigby is shared
one layer at a time through Tolkien's lane reservation.
**Sequencing with the RAM swap** (the owner, 2026-10-10: *"probably going to go with layout C"* — Mothership 80 GB, BigHonker
32 GB, Softserve unchanged): rigby and the tablets are not in the swap, so steps 1–4 can run now. RAM-dependent
measurements (KV pages in system RAM, MoE expert offload) wait for the swap or are re-run after it; Mothership's RAM may
drop toward 3,200 MT/s with the 3,200-rated sticks (unmeasured).

## Sources

- **Backburner** (iPhone as a prefill helper for Qwen3.8-27B; MIT, StayLameBro, 2026-10-01): video "I turned my iPhone
  Into a Second GPU for Qwen 3.8 27B" — captions + transcript in `Claude Home\research\ios-llm-video\`;
  github.com/StayLameBro/backburner. Key measured points: reading +29–44 %, writing unchanged (25.0 vs 25.1 tok/s);
  KV pages on the helper past 64K; main-machine DFlash2 drafter 11.3 → 25 tok/s; SSD prompt cache restores a long
  agent start in 0.3–5 s; testers: gains fall to −4 % with USB 2 / weak helpers.
- **llama.cpp expert cache** (PR #29887, merged 10-07, release b11480, `--moe-cache-mib`): video "llama.cpp Just Got
  an Expert Cache. Do You Still Need Strata?" — `Claude Home\research\video-moppXgqNHcc\`. Relevant to Mothership MoE
  offload after the RAM swap; leave it off on 8 GB cards; its MTP-slower-with-experts-in-RAM report is a MoE-offload
  condition, not the 3.8 fully on the card.
- **Local Explained, "Is 24GB All You Need For Serious Local LLM?"** (2026-10-13): KV-cache budgeting for Qwen3.8-27B on
  16 vs 24 GB — [[Research-Local-Explained-24GB-KV-Cache-Video-2026-10-13]] (external claims tagged by how far each was
  checked; compared with the lab's Mothership fit rows in the note). Raw files in
  `Claude Home\research\video-0LO0clJSgZY\`.
- **Kai, "Qwen 3.8 27B Just Hit 200 Token/s on Mac M5 (Lithos Metal)"** (2026-10-16): an external trained drafter (DSpark,
  7 tokens per round) on a bandwidth-bound Apple GPU — the measured run is ~2.4–2.5 accepted per round and ~80 tok/s
  against a 200 headline (a projection); code gains, prose loses; bears on step 5 (external drafter with a cheap GPU
  checker) — [[Research-Kai-Lithos-Metal-DSpark-External-Drafter-Video-2026-10-16]]. Raw files in
  `Claude Home\research\video-L7noXCBSObc\`.
- **Distributed speculative decoding** (drafter on another device, checker on the main machine): SpecEdge
  (arxiv 2505.17052), FlowSpec — pipelined tree drafting across devices, 1.28–1.79× (arxiv 2507.02620), DiP-SD
  (arxiv 2604.20919), GoodSpeed (arxiv 2512.09963).
- Side-session handoff with the owner's discussion: `Claude Home\HANDOFF-side-session-2026-10-10-ram-strata-backburner.md`
  (ideas and estimates, not decisions).
