# Project 34 Phase 5 appraisal — layer 1 (deterministic reduce) — 2026-09-26

**Status:** layer 1 DONE (tables below); layer 2 (bounded model audit of the grader) SUBMITTED as eval
campaign order `5f4191f58fdf` (18 pins, production 27B via HAI); layer 3 (conclusion doc, HAI-drafted,
the owner-ruled) NOT STARTED. **No interpretation of what the numbers mean for routing is made here.**
Product dir: `E:\SSDShared\tabfoundry-jobs\campaigns\phase5-appraisal-20260926\` (`summary.md`, `summary.json`,
`cells.csv`, `team_arms.csv`, `inventory.json`, `grader_fails.json`, `review_packets_coding_tool.json`,
`layer2_order.json`, `manifest.json` = sha256 of every input). Reducer: `phase5-prep\commission-artifacts\phase5-c02\drivers\phase5_appraise.py`
(order header states inputs / method / deliverables / cleanup). Design + gate: [[34-Phase-5-Ensemble-Design]],
[[HANDOFF-Project34-Phase5-after-Codex-20260908]] § Appraisal gate.

## 0. What the reduce found about the INSTRUMENT before it found anything about tablets

Every one of these is a grader/pipeline fact, corrected in a declared, reproducible way with the original
verdict kept beside the corrected one. None was found by a model; all by reading the rows.

| # | finding | effect | correction |
|---|---|---|---|
| 1 | **Window-level `usable` is not the unit.** 80/465 window files are VOID because one packed arm came up a tablet short while the other arm in the same file is complete + control-green | naive window filter dropped 150 good arms | choose per ARM across all files of a window; 788/868 arms `ok`, 80 `present_but_unusable` (each with the coverage string ledgered in `inventory.json`) |
| 2 | **Task sets differ across N** once arms drop, so ladder rows compared different tasks | N-vs-N comparisons confounded | each (division, strategy) row uses the task set present at EVERY N; N=5 repeats (identical-cohort variance arms) averaged within task first |
| 3 | **Refiner-stage answers are whole llama-cli transcripts** (spinner, ASCII logo, help text, echoed prompt, THEN the reply) — 14/16 coding + 11/16 tool refiner rows, 2 coding critic rows | the driver's `canonical_review` graded the transcript: coding refiner layouts read 0.125–0.375; one tool layout read 0.625 because the grader found the *echoed draft's* correct tool call | `ok_regraded`: same grader on the reply only (cut at the CLI's `... (truncated)` echo marker or the intact prompt; 0 unlocated). Both directions moved. **The refiner payload's capture is a pipeline defect to fix before any future team run.** |
| 4 | **Chat grader demanded the digit `5`**; 16 of 19 chat team-arm "failures" answered the word "five" | every chat team layout read 0.0 | driver `canonical_review` now accepts `5|five` (whole word, case-insensitive) — the N-ladder payload already matched gold as a whole word |
| 5 | **`t_word` vision cell is defective**: the image is clipped — it reads "the quick brown fo" with the last word cut off; gold is "fox". The model's "brown" (136/136 times) is the last complete word in the picture | 136 of the 287 vision failures are the test, not the model. ⚠ The same cell scored against every VLM in Phase 4 ([[Fellowship-Performance-Baselines-and-Benchmarking]] § VLM item 2 — "all three fail the positional read" was built on this image) | **RESOLVED same day (the owner: "resubmit those correctly").** Re-rendered to fit (`Lab\fellowship\vlm_set\make_t_word.py` → `t_word2.png` on rigby's stage store), the 15 windows re-run 1:1 as `phase5-tword2-rerun.20260926-130041` (manifest pinned): **150/150 tablet answers "fox"**. Declared overlay in the reducer: `t_word2` replaces `t_word` (26 original cells flagged `superseded_by`, never counted). Vision ladder after overlay: swarm/majority **0.857** (6/7) at every N, best-of-N 0.833 (6-task common set); the one remaining vision miss is `p_light` (gold checked by eye: green; the 500M says red 151/151 — a real model failure). Phase 4 doc corrected in place with a dated strike-through; the 2B/Qwen2-VL re-run on aragorn is queued, not done. |

Net after corrections: **30 team-arm verdicts flip** (both directions). Ladder verdicts are unchanged (the
N-ladder payloads' own grading had none of these defects).

## 1. Inventory (mechanical)

- N-ladder: 868 expected arms in 465 manifest-pinned windows → **788 usable**, 80 present-but-unusable (a
  tablet short at run time), 0 missing, 0 duplicates, 0 unexpected files. **After the `t_word2` overlay
  (15 re-run windows, 28 arms added, 26 original `t_word` cells superseded) the product reads 896 expected →
  816 usable / 80 unusable, 790 cells** — that is the number set `summary.md` carries and the closeout cites;
  the grader-marked failure count stays 415 rows (coding 222, vlm 153, tool 21, chat 19) post-overlay.
- Team arms (structured + heterogeneous): 304 → **297 complete, 7 terminal hard-fit exclusions** (the owner's ruling), 0 other.
- Grader-marked failures: 549 rows — coding 222, vlm 287, tool 21, chat 19 — collapsing to **35 distinct
  (task, answer) pairs** for coding+tool, 3 for vlm, 4 for chat. 17 of the 35 are instrument failures
  (transcript or empty), 18 are real model replies → those 18 are layer 2.

## 2. Ceiling table — smallest N reaching the best success (one seeded run per cell; common task set per row)

| division | strategy | success@N=1 | best | smallest N at best | wall at best (s) | note |
|---|---|---|---|---|---|---|
| chat | all three | 1.0 | 1.0 | 1 | 12–15 | tasks saturate at N=1 — no discriminating power |
| tool | all three | 1.0 | 1.0 | 1 | 28–33 | same (swarm per-tablet acc dips to 0.98 at N≥6) |
| coding | independent_swarm | 0.857 | 0.857 | 1 | 24 | per-tablet accuracy is flat in N (same model, same seeds) |
| coding | best_of_n (verifier) | 0.833 | 1.0 | 9 | 34 | the only task moving is `fizzbuzz`: 0 at N≤8, then a lucky pass at 9 and 10 — single-run noise, not a knee |
| coding | majority_vote | 0.857 | 0.857 | 1 | 25 | voting on canonical code HURTS at N=3/5/9 (splits + ties: 1/4/2); voting on pass/fail is also reported per cell |
| vlm | independent_swarm | 0.857 | 0.857 | 1 | 29 | **after the `t_word2` overlay:** 6/7 at every N; the only miss is `p_light` (model), identical on every tablet |
| vlm | best_of_n (ORACLE) | 0.833 | 0.833 | 1 | 30 | oracle upper bound — no selector exists for vision; 6-task common set |
| vlm | majority_vote | 0.857 | 0.857 | 1 | 26 | same single miss |

Cost side (coding, independent swarm, mean of window max): 25 s at N=1 → 33 s at N=9 → **80 s at N=10** (the
single-arm all-ten windows; straggler/contention, one variable to chase before quoting).

## 3. Team arms (driver canonical_review; `regraded` = after the corrections in §0)

| family | division | layout | arms | success | regraded |
|---|---|---|---|---|---|
| structured | coding | solver / solver_alone / solver+critic(s) / parallel_verifiers | 8 each (+24) | 0.875 | 0.875 |
| structured | coding | solver_critic_refiner | 8 | 0.375 | **0.625** |
| structured | coding | solver_critic_verifier_refiner | 8 | 0.125 | **0.375** |
| structured | tool | solver / solver_alone / solver+critic(s) / parallel_verifiers | 8 each (+24) | 1.0 | 1.0 |
| structured | tool | solver_critic_refiner | 8 | 0.25 | **0.375** |
| structured | tool | solver_critic_verifier_refiner | 8 | 0.625 | **0.25** |
| structured | chat (1 task: `sisters`) | every non-refiner layout | 1 each | 0.0 | **1.0** |
| structured | chat | the two refiner layouts | 1 each | 0.0 | 0.0 |
| heterogeneous | coding / tool / vlm | specialists | 32 / 40 / 14 (+7 excl.) | 0.875 / 1.0 / 0.786 | same |
| heterogeneous | chat | specialists | 10 | 0.0 | 0.0 (all answered "three"/"four") |

Structural note, not an interpretation: in every layout WITHOUT a refiner the final answer IS the solver's
answer, so those rows equal `solver` by construction; only the refiner layouts change the answer. Chat has
one task in the team matrix (n=1), which supports nothing.

## 4. Layers 2 and 3

- **Layer 2 DONE (14:09):** order `5f4191f58fdf`, 18 pins, all rows clean (served `qwen3.6:27b` on Mothership,
  no downgrade), control pin correct. Verdicts: **14 genuine_error · 1 formatting_only · 3 correct_but_misgraded**.
  The three disputes were settled by CODE (`phase5_layer2_recheck.py` runs the payload's own check lambdas on
  the disputed answers): 2/4, 2/3, 2/3 checks pass — **the grader was right all three times, the 27B wrong.**
  ⇒ **grader false-negative rate on real model replies: 0 / 18.** The deterministic table in §2–3 stands
  unqualified. Side finding for Area A: qwen3.6:27b, asked to audit code against explicit test lambdas,
  called failing code correct 3 times in 18 — a reviewer weakness worth a cell in the 3.6-vs-3.8 comparison.
  Product: `layer2_verdicts.json`. Composer/readback: `phase5_layer2_campaign.py`, `phase5_layer2_readback.py`.
- **qwen3.8 head-to-head DONE (17:47, the owner: "flip it, and let it run"):** HEAVY_MODEL flipped to
  `qwen3.8:27b`, the identical order re-submitted (`de55ff2110f4`, `--tag=qwen38`), every row served
  `qwen3.8:27b`. Verdicts **16 genuine_error · 1 formatting_only · 1 correct_but_misgraded**, and the one
  dispute (`coding-02`) was again refuted by code (3/4 checks pass) — its own reason text says the grader was
  right while its label says otherwise. **Layer-2 conclusion double-sourced: 0 / 18 grader false negatives
  under either reviewer.** Reviewer accuracy: 3.6 wrong 3/18, 3.8 wrong 1/18. Cost of 3.8: 2/18 replies
  carried a stray `</think>` with the verdict JSON emitted twice (`think_leak` / `verdict_objects` now recorded
  per row); the first readback rejected those rows until the parser was changed to a real `raw_decode` from
  each `{"verdict"` start (3.6 re-parse identical, so the change is safe). HEAVY_MODEL **reverted to
  qwen3.6:27b** the same evening; 3.8 adoption is gated on the leak (backlog 2026-09-26). Product:
  `layer2_verdicts.qwen38.json`. Note for the record: `eval_campaign` pins still carry no model field — the
  lab's `requires: ["model.<name>"]` pin is honoured by the p20 desk but not by this order kind (backlog).
- **Layer 3 DRAFT AUDITED (evening):** HAI drafted the closeout through the Absurd lane (packet = this
  doc's numbers via `summary.md` verbatim + both layer-2 tables + the team regrade table + the owner's three
  rulings), the main session traced every number (454/454, then 491/491 on the revision) and sent seven
  sentence-level corrections back with the contradicting ladder rows — all landed. Product:
  [[34-Phase-5-Appraisal-Closeout]] (rulings + audit record on top, HAI's revision verbatim). **the owner ruled
  the same evening:** one tablet per task (provisional until the project ends), refiners parked, closeout
  canonical; the harder bench is a Phase 7 prerequisite — [[34-Hard-Bench-Order-Spec]].
  Same shape as [[Fellowship-Gauntlet-Conclusion-2026-08-31]].

## 5. Caveats that bind any reading

- one seeded run per cell → fit/cost numbers, not distributions (the N=5 variance arms are the only repeat);
- best-of-N selection is real only where a verifier exists (coding, tool); chat/vlm best-of-N is an oracle;
- majority ties count as failures and are listed per cell;
- team-arm success is the driver's deterministic canonical review of the FINAL stage answer only;
- chat and tool task sets saturate at N=1 for a 1.5B model — they measure cost, not quality.
