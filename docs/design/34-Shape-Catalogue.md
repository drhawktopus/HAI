# Project 34 — the Shape Catalogue: every way the lab can run a job, priced on one scorecard — DRAFT 2026-09-27

**Status:** DRAFT for the owner. Written after the 2026-09-27 regroup; replaces the phase-map amendment I had
proposed. the owner's thesis, verbatim: *"the main thesis for this whole thing is that we are trying to find the
most efficient / best / cheapest ways to use the lab for any variety of different tasks or jobs."*
**Project:** #34 (charter §52–53). **Front door in the end state:** every order enters at **Kirk**, who
consults Spock and this catalogue and sends the job to Tolkien's Fellowship or to the fleet (BigHonker,
Mothership, rarely Softserve). Working Tolkien's inbox directly, as we do now, is the bench, not production.

**⚠ Hardware since this draft (2026-10-22 — rigby's rows below were written CPU-only).** rigby's factory (Tolkien)
now has THREE kinds of hardware under one controller: the **GTX 1060 6 GB** (CUDA 11.4, sm_61; rigby's GPU LANE — the
A3B lead, ~16.9 tok/s writing: [[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]]), the **GT 730 2 GB** (works in the
FELLOWSHIP POOL as the fastest warm node, `warm_gt730`; Phase 7's `tablets` rung = "the tablet + GT 730 pool"), and the
**eleven tablets**. S5r's "rigby alone" is the 1060 now; S5 Voltron is closed; the measured way to use the whole box is
independent work side by side (#36 r76: the A3B on the 1060, −6 % while the 730 serves, + the Fellowship ~50 tok/s across
11 nodes). Vocabulary: [[Tablet-Cluster-INDEX]] § VOCABULARY (where hardware sits is not the lane it works in).

## The scorecard (identical for every cell, whatever ran it)

| field | meaning | source |
|---|---|---|
| success | deterministic check passed (or judged, flagged as such) | tablet work: graded ON THE TABLET by the payload's own scorer (`score` / `score_tool` / `_vlm_score`), Tolkien ships the verdicts. Fleet work: the same scorers, run inside the order on the box (`infer_ollama --grade`, since 2026-09-28) — the order ships graded rows. rigby / Voltron (`llm` kind): the tablets' payload scorer, inside the order |
| wall_s | order in → result out, including verification | window/order record |
| effort | tablet-seconds × node class, or GPU-seconds; **watt-hours** where telemetry exists | rigby battery/energy telemetry; Mothership GPU power log |
| tier | which hardware can run it at all (fit) | capability declarations |
| verify_cost | seconds and where the check/judge ran | Tolkien / Kirk |
| n, seeds, spread | how many samples, and min..max, never a bare mean | reducer |
| status | PRELIMINARY until a shape holds across ≥2 task families and reproduces once | this page |

**Standing rules.** Sampled decoding for any ensemble arm (Phase 5 ran temperature 0 and same-model tablets
were identical by construction). A deterministic check always outranks a model judge; a judge never grades
the model that wrote the answer. The fleet is never partly idle while a queue exists — any arm that does not
care which tablets it gets is packable, and independent arms run side by side.

## The big table — every cell, stored forever

the owner, 2026-09-28: *"I want the huge thing"* — the same idea as the Fellowship Gauntlet's 688-cell table, across
every machine. This page is the curated reading; the table is the evidence.

- **`Datasets\Project34-Menu\menu-cells.csv`** — one row per measured answer (fleet, tablets, rigby, Voltron, and
  the Gauntlet's cells), 24 columns incl. shape, box, model, family, thinking, temperature, decoding, ok, wall,
  energy, window `usable`, source file. `menu-answers.jsonl` holds the answer text; `build-manifest.json` counts
  every source. First build 2026-09-28: 5,453 rows from 161 source files, 0 duplicate ids.
- **Rebuild** after results land: `hard-bench\build_menu.py` (re-grades with the current scorers; refuses to write
  if any cell id repeats).
- **What is empty:** `hard-bench\menu_coverage.py` → `Datasets\Project34-Menu\coverage.md` — one row per
  shape · box · model · thinking · temperature, missing TEXT cells, missing VISION cells (capability unchecked),
  and thinking models measured on one setting only. It submits nothing; the owner picks what to fill.

## Shapes (rows) × knobs (columns)

| shape | what runs | checker / selector | knobs | measured so far | not yet |
|---|---|---|---|---|---|
| **S1 one tablet** | one model, one attempt | Tolkien check | model size, temperature, task family | coding: 21-task calibration, 10 seeds (`hard-bench-20260926`); tool 1.5B: 18 always / 6 never, no middle band; vlm SmolVLM2 at 1×/2×/4× images: pooled 0.677 / 0.667 / 0.688 (image size is not the limit); energy ≈ 250 J per solved at the wall, a FLOOR (1.5B coder) — [[34-Hard-Bench-Closeout]] | temperature ≠ 0.6; node-class effort; a second task family; vlm size ladder (Qwen2-VL-2B) |
| **S2 N individuals + head** | N tablets, same model, sampled | Tolkien: check → best-of-N; vote when no check; judge for ties/unverifiable | N, temperature, model size | coding matrix N∈{1,3,5,9}×3 repeats (`hard-bench-20260927`): best-of-N 0.42→0.92, per-tablet flat, voting ~0; 0.5B rung: best-of-9 0.68 ≈ 1.5B best-of-3; vlm vote matrix: voting reshuffles, creates no right answers (all PRELIMINARY — [[34-Hard-Bench-Closeout]]) | energy per solved for the ensemble, verify_cost, a second seed block / task family, reproduction |
| **S3 team layouts** | solver / critic / verifier / refiner stages | stage contract + Tolkien check | layout, model per role, temperature | Phase 5 at temp 0: refiners degraded; capture defect in the refiner payload | re-run sampled after the capture fix; harder tasks |
| **S4 heterogeneous specialists** | different models per tablet | as S2 | model mix | Phase 5 at temp 0: no gain | sampled re-run |
| **S5 Voltron** | Tolkien using rigby AND all the tablets at the same time for one task, as one machine (one model's layers spread over the tablets by ggml RPC) | the tablets' payload scorer, inside the order | model, split | Tolkien order kind `llm`, arm `voltron` (`tolkien_llm.py`, 2026-09-28). Coder-3B, family 1, 3 seeds, one order with both arms: **Voltron 21/30 at ~89 s per answer (1.18 tok/s, 126 s load) vs rigby alone 23/30 at ~10 s** — same accuracy within noise, ~9× slower at a size rigby holds — [[34-Hard-Bench-Order-Spec]] § rigby alone | the owner 2026-09-28: priced and capped here; the only open question is a model too big for rigby's 23 GB (parked); energy (no meter) |
| **S5r rigby (the Tolkien head) alone** | one model on rigby's CPU (i7-3770 since 2026-08-28, 24 GB DDR3-1600, no AVX2; GT 730 2 GB beside it) — NOT Voltron | the tablets' payload scorer, inside the order | model size | kind `llm`, arm `rigby`, 3 seeds, one run each (2026-09-28): family 1 — Coder-0.5B 0.37 (5.5 s/answer, ~41 tok/s), Coder-1.5B 0.30 (3.8 s, ~20 tok/s); family 2 — Coder-1.5B 0.58 (2.7 s), **Coder-3B 0.82 (6.1 s, ~10 tok/s) — level with the 9B on BigHonker for this family**, general 7B 0.83 (12 s, ~4.7 tok/s), **general 14B 0.92 (22 s median with the prompt cache; 60–80 s on a cold prompt — rigby reads prompts at ~4.2 tok/s, no AVX2)** — [[34-Hard-Bench-Order-Spec]] § rigby alone | Qwen3.6-35B-A3B MoE; speculative decoding (draft / n-gram — the first attempt ran with speculation off, `--spec-type` defaults to none); energy (rigby shares the APC with the tablets' charging) |
| **S6 8 GB box** | 9B-class on BigHonker / Softserve 3070s | payload scorer, graded inside the order (`infer_ollama --grade`) | model, N (sampled best-of-N applies here too), temperature, thinking | hard pool, sampled (Hard Bench pricing — NOT the Phase 3 8 GB tournament, which has not started): coding 9B per attempt 0.79, best-of-3 1.00, 353 J above idle; family 2: 0.875 / 0.819 / 0.778 per attempt at 0.3 / 0.6 / 0.9, best-of-3 0.92–0.96, ~130 J median per attempt; tool 9B 0.89 at 94 J/solved; vlm qwen3-vl:8b 24/24 at 2× (num_ctx 4096) ≈ 260 J/solved; qwen3:8b family 2 0.931 at 0.9 s; contention: BigHonker is HAI's chat brain — [[34-Hard-Bench-Closeout]] | Softserve's 3070 Ti (does not hold qwen3-vl:8b); the Phase 3 tournament itself (the owner starts it) |
| **S7 Mothership 27B** | the champion, single attempt (or N) | payload scorer, graded inside the order (`infer_ollama --grade`) | model (3.6 vs 3.8), N | layer-2 reviewer accuracy (3.6 wrong 3/18, 3.8 1/18); hard pool: coding 1.00 at 1,173 J above idle; family 2: 3.6 1.000 at ~613 J median per attempt, **3.8 0.972 (best-of-3 1.00) at ~404 J, 1.2 s vs 2.0 s**; family 1: 3.8 1.000; tool 3.6 1.00 at 313 J/solved, 3.8 1.00 at 222 J/solved; vlm at 2× thinking on: 3.6 ≈ 4,200 J, 3.8 ≈ 940 J per solved (one run each); every other model Mothership holds priced on family 2 (qwen3-coder:30b 1.00, qwen3.6:35b-a3b 0.99, qwen3:14b 0.97) — [[34-Hard-Bench-Order-Spec]]. This is Hard Bench pricing, NOT the Phase 2 24 GB tournament: champion-until-beaten is decided there, on actual lab work (charter §10) | the Phase 2 tournament itself (the owner starts it) |
| **S8 decomposition** | Kirk splits a big job into checkable pieces, each placed by this catalogue | per-piece checks | piece size, shape per piece | Absurd decomposed mode exists | measured against the hard pool |
| **S9 ordinary code** | no model at all | n/a | — | the deterministic core | keep as the floor every shape must beat |
| **S10 rigby lead + Fellowship sub-agents** | a larger model on rigby does the work that needs it (plan, hard parts, final answer) and hands sub-tasks to the 10 tablets (drafts, checks, tests), all orchestrated by Tolkien — the same split as Claude working with its own sub-agents and with HAI (the owner, 2026-09-28: "the bigger wins from that category") | per-piece checks + the lead's own review | lead model size, sub-task kind, split | design: [[34-S10-Rigby-Lead-Fellowship-Design]] (DRAFT). **S10a cascade, family 2** (computed from delivered results): tablets best-of-5 0.75, rigby Coder-3B 0.75–0.88, **tablets-first-then-rigby 0.83–0.92** with rigby on 6/24 tasks — but the tablets' selector was the hidden checks. **S10c BUILT and RUNNING 2026-10-06** (the lead's `delegate` → a Tolkien map order on `map_brief.py`, one tablet drafts the piece; [[34-S10-Rigby-Lead-Fellowship-Design]] § S10c): organic arm (tool merely available) 0 pieces in 2 exercises, cut — the MoE lead costs 1–3 h per exercise on rigby; directed arm running, first piece drafted by aragorn in 305 s. **The same shape one layer up (GPU: the 3.8 lead on Mothership, qwen3:8b helper on BigHonker), 10-exercise spot set, 2026-10-06:** solo 6/10 · team organic 5/10 (0 pieces) · directed 7/10 (0 pieces) · MANDATORY 6/10 with 15 pieces, the 8B answering each in 2–15 s, lead wait 20–83 s per piece, **671 kJ per solved incl. pieces vs solo's 436** — the 3.8 does not delegate unless made to, and made to it scores the same at 1.6× the wall (n=1 per arm; the 5/6/7/6 spread is its own run-to-run variance). **S10b BUILT 2026-10-06 18:04** (the lead writes the CHECKS; the tablets' existing candidates are chosen by them and graded by the hidden checks, false pass / false fail = the checks' honesty — § "S10b BUILT"); lead order `s10b-rigby-moe-f2tests-s1.20261006-1804` queued behind S10c. Priced entries: [[34-A1-Agentic-Build-Family-Spec]] § "Priced entries" (`hard-bench\a1\reduce_a1.py`) | S10b rows (`s10b_select.py` when the lead order delivers); the directed S10c rows; the GPU gate and gate+review arms |

## Knobs worth their own columns

- **temperature / seeds** — the diversity source; S2's whole effect depends on it.
- **N** — the attempts axis; cost linear, success 1−(1−p)^N when tries are independent (preliminary).
- **model size** — the compute-matched ladder on Rigby's store: Coder 0.5B / 1.5B / 3B; general 0.5B / 1.5B /
  Qwen3.5-0.8B / Gemma-4-E2B / MiniCPM-1B; vision SmolVLM2-500M / Qwen2-VL-2B. The Coder 3B does not fit a single
  tablet of any class (measured 2026-09-27, [[34-Hard-Bench-Order-Spec]]); its rung is Voltron or the Tolkien host.
- **task family** — coding (hidden tests), tool (schema), vlm (exact/contains), chat (short-answer exact);
  open text has no checker yet → judge only, flagged.
- **node class** — A / B / C tablets cost different effort for the same run; "nine tablets" is not one number
  until measured per class.
- **thinking on / off** — a setting on every thinking model (Qwen3.x), recorded per row (the owner 2026-09-28: "just
  another menu setting"). GPU fleet orders default to OFF (`infer_ollama` `think=False`; `--think` turns it on), so
  every GPU coding and tool row before 2026-09-28 16:30 is thinking-OFF; the vision 2× rows are ON. ON rows: fleet
  family 2 (`fleet-f2-t06-think`, paired with the OFF rows at the same seeds). Tablets: OFF runs by day; ON is the
  "low and slow" overnight row (Qwen3.5 small models ~258 s per answer, a fifth hitting the 400 s limit).
- **decoding speed-ups** — the same model, faster: speculative decoding with a draft model (rigby, `llm`
  `draft_model`) and built-in multi-token prediction (Ollama `qwen3.8:27b-mtp` tag — same weights).

## The question the catalogue answers (charter §53), and how

*When is intelligence gained more efficiently by a larger model vs more independent attempts?* — run the SAME
hard task pool through S1, S2 (size ladder, compute-matched), S6 and S7, score every cell on the scorecard,
and read the crossover per task family. Then S5 and S3/S4 fill in as further rows. The scheduler's rule is
whatever the priced menu says for a job's family and its checkability.

## Next fills (the plan's order; when one finishes, start the next — all reuse the hard-bench machinery)

The Hard Bench filled S1/S2/S6/S7 on one task family per division ([[34-Hard-Bench-Closeout]]). Remaining cells:

1. **Small shapes — filled 2026-09-28** ([[34-Hard-Bench-Order-Spec]] § Small shapes): temperature (greedy is the best
   single attempt 0.50 but gives N identical tries; sampling is what the ensemble needs), verdict reproduction 50/50,
   node class A 28 s · B 31 s · C 45 s per item. Still open: **verify_cost** (Tolkien's check per answer) and a
   same-payload re-run of the 0.6 arm.
2. **A second coding task family — filled 2026-09-28** ([[34-Hard-Bench-Order-Spec]] § Second coding task family):
   24 debug/modify tasks; fleet S6/S7 on two seed sets (9B 0.78–0.88 per attempt, 27B 1.00); tablet S2 matrix
   best-of-N 0.61 → 0.94 at N 1 → 9 **picked by the hidden checks (a ceiling — production has no answer key)**,
   majority voting 0.39.
3. **S5r rigby alone — IN FLIGHT 2026-09-28** (kind `llm`, arm `rigby`): Coder-0.5B / 1.5B / 3B done on both
   families; Qwen2.5-7B and 14B on family 2 queued — candidates for the S10 lead model.
4. **S5 Voltron — CLOSED 2026-10-02 (the owner: "Voltron is closed"); priced, then capped (the owner 2026-09-28: "don't go overboard… we know it's going to be slow").**
   Coder-3B family 1 (running) plus the August 7B (~0.5 tok/s) are enough to price it: at every size rigby can hold,
   rigby alone is far faster. The one question left for Voltron is a model too big for rigby's 23 GB (rigby + the
   tablets pooled, ~40–70B heavily quantised) — parked, not scheduled.
5. **S10 rigby lead + Fellowship sub-agents** — the direction the owner named as the bigger win. Prerequisites: the second
   Tolkien lane (rigby as its own unit, being built) and a lead model picked from S5r. Design first, then orders.
6. **VLM size ladder:** Qwen2-VL-2B does not fit a tablet (swap/OOM per the library); its home is rigby, which needs
   image support in kind `llm` (text-only today).
7. **S3 / S4 sampled re-runs** after the refiner transcript-capture fix; then **S8 decomposition** on the hard pool.
8. **Judge calibration** (so S2 extends to unverifiable families) — parked to the orchestration phase (the owner 2026-09-27).
