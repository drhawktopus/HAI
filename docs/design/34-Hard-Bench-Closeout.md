<!-- SUPERVISOR AUDIT RECORD (Claude main session, 2026-09-28) — precedes HAI's revision 2 -->
> **Layer 3 — the Hard Bench closeout, supervisor-audited.** HAI (Kirk Absurd lane, route heavyweight) drafted from
> 11 reduce products pasted verbatim (`hardbench_layer3_submit.py`) under six rulings the owner made (preliminary; strict
> tool arguments; 1× fleet vlm VOID; 3B = operator error; tablet energy a floor; chat/vlm best-of-N out).
> **Runs:** draft `abr-1790607475-77ba71`; revision 1 `abr-1790608196-9a39ff`; revision 2 `abr-1790612049-7824d9`
> (charter framing, the owner 2026-09-28: the bench fills a priced menu, it does not make routing choices).
> **Sources actually read (tool trail, `read_check`):** revision 2 fetched all four governing docs in full with
> get_document — 34-CHARTER, 34-Shape-Catalogue, 34-Hard-Bench-Order-Spec, 34-Phase-5-Appraisal-Closeout. (Revision 1
> made no tool calls — a blind pass; its corrections were carried forward and re-audited here.)
> **Number audit** (`hardbench_layer3_audit.py`, against the original packet + the four docs, decoy-proven): 557
> tokens, one flag — "1,173" split at its comma, the 27B's above-idle J in `fleet.md`.
> **Supervisor edits to HAI's revision 2 (the only changes to its body):** working note above the title removed;
> §3.3 S2 energy cell — "≈ 250 J per solved" is the single-attempt 1.5B wall floor, not the N=9 ensemble, relabelled
> as not yet measured for that shape; §3.4 "the front-runner" softened (the tournament has not run); status line.
> Earlier supervisor edits (revision 1) are carried in the prior draft HAI revised: §1.2 task count wording, §2.5
> coding-including-idle 596 / 1201 J, a duplicated §3.3 sentence.
> Source: `tabfoundry-jobs\campaigns\hard-bench-closeout\layer3_draft.20260928-111410.md` (sha256 ed29878c11dde8d4).

# Project 34 Hard Bench closeout — harder task pools on the Fellowship and the fleet — DRAFT for audit

**Status:** layer 3 revision 2, supervisor-audited — the record of what the Hard Bench measured; every reading PRELIMINARY; results are priced menu entries, not routing choices

## Governing sources and decision rule

This report applies these sources in order:

- [[34-CHARTER]] — campaign charter; §52 (cheapest reliable intelligence principle) is the decision rule; §53 (larger model vs more attempts) is the question, answered per task class.
- [[34-Shape-Catalogue]] — the priced menu of shapes (S1–S9) and the scorecard these results feed into.
- [[34-Hard-Bench-Order-Spec]] — Hard Bench order (inputs, method, deliverables); every calibration and matrix result; energy figures and their limits; the VLM-at-2× re-run.
- [[34-Phase-5-Appraisal-Closeout]] — closeout shape precedent and section-order contract.

The decision rule is the charter's §52: *"Use the least expensive source of compute and intelligence that reliably satisfies a task's quality + time requirements, while accounting for verification, energy, resource contention, and escalation."* The research question is §53: *"when is intelligence gained more efficiently by making one model larger vs running more independent attempts?"* — answered per task class, not universally. The Hard Bench produces priced entries for the Shape Catalogue, not routing verdicts; area C's scheduler turns those entries into policy in Phase 7, which cannot proceed before Phase 6 (Voltron) is measured.

The order spec's Preliminary rule binds all numbers in this document: one task family per division, one seed block per cell, one run per fleet rung. A reading graduates only when it holds across a second task family and reproduces once (the owner, 2026-09-27: *"we shouldn't be saying authoritative things if we can't prove authoritative things"*).

**Rulings already made by the owner (bind this draft; not re-litigated):**
1. Every number here is PRELIMINARY — see above.
2. Tool arguments are graded STRICTLY (case-insensitive exact string). `scale_double`'s `search_service` stays a miss; it is a naming question the task left open, not a reasoning error.
3. The 1× fleet VLM rows (qwen3.6:27b 0.53, qwen3.8:27b 0.89) are VOID as model comparisons — images too small and thinking off. The 2× rows with thinking on are the valid comparison. qwen3-vl:8b receives the same image size at 1× and 2× (ollama resizes it), so its 2× row is not an image-size test.
4. The 3B coder fits no tablet; its rung is Voltron / the Tolkien host. The 3B tablet windows were an operator error, not data.
5. Tablet energy is a wall-side FLOOR (some tablets drew on their batteries); it is not like-for-like with the GPU board-power figures.
6. Chat and VLM best-of-N are excluded (no selector). Judge calibration is parked to the orchestration phase.

## Executive conclusion

Three findings from the harder task pools:

1. **The middle band exists for coding at 1.5B, and ensembles with a verifier move on it.** The 1.5B coder's best-of-N rose from 0.417 (N=1) to 0.917 (N=9) with the real code verifier — the shape matches independent-draw predictions. Majority vote is useless for code at this model size (59 of 96 cells tied). The 0.5B coder needs 9× to reach about what 1.5B does at 3× in success, which is the compute-matched question in one row.
2. **Tool has no middle band at 1.5B.** Sampling varies the answers (6 tasks gave different outputs across seeds) but never turns a wrong call right — 18 tasks always correct, 6 never. The ensemble matrix was not run for tool; that is a finding, not a failure.
3. **VLM tablet model barely moves with larger images, and the fleet VLM rows at 1× were voided by method.** SmolVLM2-500M's pooled accuracy is flat across 1× (0.677), 2× (0.667), and 4× (0.688) within noise; counting stays never at every size. The Qwen 27Bs' 1× misses were caused by the method (images too small, thinking off); both score 1.00 at 2× with thinking on but at high energy cost.

No reading has graduated to authoritative status; all are preliminary and await retest.

## 1. Conclusions required by the order

### 1.1 Coding — calibration (1.5B, tablets)

The candidate pool of 21 tasks yielded a clear middle band under sampled decoding (temperature ~0.6, 5 seeds per task):

| bucket | tasks (pass rate over 5 or 10 seeds) |
|---|---|
| always pass (1.0) | add_reverse_param, fix_average_empty, fix_binary_search, fix_dedupe_order, int_to_roman, merge_intervals, rle_encode, roman_to_int, rotate_matrix, search_insert |
| in band 0.3–0.7 | caesar 0.6 · valid_ipv4 0.6 · longest_common_prefix 0.5 · top_k_words 0.3 |
| rarely right 0.1–0.2 | flatten 0.2 · balanced_brackets 0.1 · eval_rpn 0.1 · rle_decode 0.1 |
| never (0.0) | group_anagrams · parse_csv_line · word_wrap |

Source: hard-bench-20260926\calibration.md (Materials 1)

**Reading:** A middle band exists for the 1.5B coder — the risk that this division would yield no between-tasks discriminating power did not materialise. The four in-band tasks were kept, along with the four rarely-right tasks (flatten, balanced_brackets, eval_rpn, rle_decode) and two anchors (rotate_matrix as a positive anchor, group_anagrams as a negative anchor), giving 10 tasks in the full matrix.

### 1.2 Coding — ensemble matrix (1.5B, tablets)

The 10-task matrix (the 8 kept tasks — 4 in 0.3–0.7 plus 4 rarely-right — and 2 anchors) was run across N ∈ {1, 3, 5, 9} with 3 seeds per cell: 120/120 arms usable, 540 tablet runs. The per-N table below is over the 8 in-band tasks only (as its footnote states).

| N | per-tablet accuracy (swarm mean) | best-of-N with verifier | majority of verdicts | majority of canonical answers | wall max s |
|---|---|---|---|---|---|
| 1 | 0.417 | 0.417 | 0.417 | 0.417 | 33.2 |
| 3 | 0.417 | 0.708 | 0.417 | 0.042 | 38.9 |
| 5 | 0.383 | 0.750 | 0.458 | 0.083 | 46.4 |
| 9 | 0.403 | 0.917 | 0.417 | 0.042 | 49.1 |

(means of the 8 in-band task means; pooled per-tablet accuracy over all 432 in-band samples is 0.400.)

Source: hard-bench-20260927\matrix.md (Materials 2)

**Findings:**
- With a verifier, best-of-N rose the way independent draws predict. From calibration pass rates (1−(1−p)^N, mean over 8 tasks): predicted 0.31 / 0.59 / 0.71 / 0.83 at N=1/3/5/9; observed 0.42 / 0.71 / 0.75 / 0.92 — same shape, slightly above because this seed block's per-tablet rate (0.40) sits above calibration's. Consistent with independent tries; not yet proof of it (one block, one family).
- Per-tablet accuracy is flat in N (0.38–0.42). Adding tablets does not make any one tablet better; the gain is entirely in selection.
- Majority vote does nothing here (0.42–0.46 by verdict), and majority-by-canonical-answer is ~0 with 59 of 96 cells tied. Sampled code answers almost never match textually; voting needs a verifier or an AST-level canonicaliser, which is a Phase 8 tool, not a tablet property.
- Cost: window wall max rises 33 → 49 s from N=1 to N=9 (+48%) for +0.5 absolute success.

### 1.3 Coding — size ladder (0.5B rung, tablets)

The 0.5B coder ran on the same 8 tasks: 104 of 120 arms usable (16 VOID at Tolkien's contract — one tablet's row missing from a window).

| N | per-tablet accuracy (0.5B) | best-of-N with verifier (0.5B) | 1.5B for comparison |
|---|---|---|---|
| 1 | 0.333 | 0.333 | 0.417 |
| 3 | 0.111 | 0.190 | 0.708 |
| 5 | 0.256 | 0.444 | 0.750 |
| 9 | 0.333 | 0.682 | 0.917 |

Source: hard-bench-20260927\matrix-05b.md (Materials 3)

**Reading, held loosely:** The 0.5B is a worse single attempt (~0.3) and its best-of-9 (0.68) lands about where the 1.5B's best-of-3 does — i.e. on this bench 9 × 0.5B ≈ 3 × 1.5B in success. The N=3 dip is the VOID arms, not a property of the model.

### 1.4 Coding — fleet rungs (same 8 in-band tasks)

The same 8 in-band coding tasks were run on BigHonker (9B) and Mothership (27B) as factory orders: 3 rounds, production sampling, verification offline with the tablet payload's checker. This is the metered re-run of the fleet rungs (the order spec's earlier table reporting 9B at 0.75 per attempt and 5.2 s wall mean is the first run; both are one run each).

| rung | per-attempt pass rate | best-of-3 (mean over tasks) | wall_mean_s |
|---|---|---|---|
| 9B (qwen3.5:9b, BigHonker, num_ctx 6144) | 0.792 | 1.000 | 3.9 |
| 27B (qwen3.6:27b, Mothership) | 1.000 | 1.000 | 3.6 |

Source: hard-bench-20260927\fleet\fleet.md (Materials 4)

**Reading:** The 9B reaches ceiling via best-of-3 on every in-band task at under 4 s per answer and a fraction of the 27B's energy. The 27B passes on the first attempt; it is the quality ceiling but also the energy ceiling.

### 1.5 Tool — calibration (1.5B, tablets)

The hard tool pool (24 tasks, 8 tools with near-neighbours, derived arguments, no-fit traps) yielded:

| bucket | tasks |
|---|---|
| always right (1.0) | alert_critical, alert_warning, nofit_disk, nofit_email, nofit_translate, query_customer, query_distractor, query_word_id, readlog_dozen, readlog_first_step, readlog_negation, restart_bounce, scale_five, scale_zero, search_oom, search_timeout, stop_mailer, stop_worker |
| never right (0.0) | nofit_deploy, nofit_reboot_host, readlog_words, scale_double, update_paid, update_resolved |
| in band 0.3–0.7 | *(none)* |

Source: hard-bench-20260927\calibration-tool.md (Materials 5)

**Finding:** Sampling does vary the answers (6 tasks gave different outputs across seeds), but it never turns a wrong call right. Tool has **no middle band at 1.5B** and leaves the ensemble matrix. That is a finding, not a failure.

Layer-2 failure audit (Material 11): the 30 failed tool rows were examined byte-for-byte against raw result files — no truncation. All tool answers are complete JSON. `scale_double`'s miss on `search_service` vs. gold `search` stays per the owner's ruling. The `nofit_` tasks that chose wrong tools (e.g. calling `scale_service` for "deploy", `restart_service` for "reboot the host") are genuine reasoning failures, not grading artefacts.

### 1.6 VLM — calibration and image-size question (SmolVLM2-500M, tablets)

Three calibrations at different image sizes:

**1× images (480×320):** 155 rows kept across three manifests; band tasks: h_bars_short (0.7), h_corner (0.7). Pooled accuracy 105/155 = 0.677.

Source: hard-bench-20260927\calibration-vlm.md (Materials 6)

**2× images (960×640):** 120 rows, band tasks: h_bars_short (0.6), h_word_second (0.6). Pooled accuracy 80/120 = 0.667.

Source: hard-bench-20260927\calibration-vlm-2x.md (Materials 7)

**4× images (1920×1280):** 112 rows usable (window 11 VOID at Tolkien's contract). Band tasks: h_bars_short (0.5), h_corner (0.6), h_word_second (0.6). Pooled accuracy 77/112 = 0.688; 85/120 = 0.708 if the VOID window's known-correct answers are counted — conclusion unchanged within noise.

Source: hard-bench-20260928\calibration-vlm4x.md (Materials 8)

**Finding:** SmolVLM2-500M's accuracy is flat across image sizes (0.677 → 0.667 → 0.688). Per-task shifts exist but are n=5 cells. Counting stays never at every size. Wall time rises sharply: ~30 s (1×) → ~44 s (2×) → ~190 s (4×). The image does reach the model (encode time rises with size); it just does not help.

### 1.7 VLM — majority-vote matrix (SmolVLM2-500M, tablets, 1× images)

Seven tasks that were "sometimes" right at 1× were run through the majority-vote ensemble:

| N | per-tablet (swarm mean) | majority answer right |
|---|---|---|
| 1 | 0.714 | 0.714 |
| 3 | 0.633 | 0.650 |
| 5 | 0.657 | 0.667 |
| 9 | 0.646 | 0.667 |

Source: hard-bench-20260927\matrix-vlm.md (Materials 9)

**Finding:** Majority vote does not improve accuracy over a single tablet on these harder VLM tasks — flat at ~0.65–0.71 across all N values. No selector exists for vision answers.

### 1.8 Fleet rungs — tool and VLM

The same hard tool and VLM pools were run on BigHonker (9B) and Mothership (27B) as factory orders: 648 rows total, temperature 0.6, 3 rounds.

**Tool fleet rungs:**
| rung | model | per-attempt right | always / never / sometimes | J per solved | meter |
|---|---|---|---|---|---|
| tool-9B | qwen3.5:9b (BigHonker) | 0.89 | 19 / 1 / 4 | 94 | nvidia-smi@4Hz |
| tool-27B | qwen3.6:27b (Mothership) | 1.00 | 24 / 0 / 0 | 313 | telemetry-csv |

**VLM fleet rungs (2× images, thinking ON — the valid comparison):**
| rung | model | per-attempt right | always / never / sometimes | J per solved | meter |
|---|---|---|---|---|---|
| vlm2x-27b-q36 | qwen3.6:27b (Mothership) | 1.00 | 24 / 0 / 0 | ≈4,200 | telemetry-csv |
| vlm2x-27b-q38 | qwen3.8:27b (Mothership) | 1.00 | 24 / 0 / 0 | ≈940 | telemetry-csv |
| vlm2x-8b-bh4k | qwen3-vl:8b (BigHonker, num_ctx 4096) | 1.00 | 24 / 0 / 0 | ≈260 | nvidia-smi@4Hz |
| vlm2x-8b | qwen3-vl:8b (Mothership, num_ctx 8192) | 1.00 | 24 / 0 / 0 | ≈470 | telemetry-csv |

The 1× VLM fleet rows (qwen3.6:27b at 0.53/297 J, qwen3.8:27b at 0.89/140 J) are VOID per the owner's ruling #3.

Source: hard-bench-20260927\fleet-tv\fleet_tv.md (Materials 10)

**Finding:** For tool calls, size is the lever — 9B achieves 0.89 and the 27B reaches 1.00, with measured costs of 94 J vs 313 J per solved task (and 353 J vs 1,173 J above idle for coding on the same machines). For VLM with thinking on, qwen3.6 spent ≈4,200 J per solved while qwen3.8 spent ≈940 J (one run; the gap size is rough). All fleet VLM models score 1.00 at 2× with thinking on; qwen3-vl:8b does so at the lowest energy.

### 1.9 Energy per solved task — coding

| rung | meter | J per solved task | J per solved above idle |
|---|---|---|---|
| 9B (qwen3.5:9b, BigHonker) | nvidia-smi@4Hz | 596 | 353 |
| 27B (qwen3.6:27b, Mothership) | telemetry-csv | 1201 | 1173 |

Source: hard-bench-20260927\fleet\fleet.md (Materials 4)

Tablet reference (1.5B coder): ≈ 250 J per solved task at the wall — a FLOOR, because during the controlled run gandalf and merry drew on their batteries and pippin fell from 100 to 97%, so part of the work was paid from batteries the wall never saw (Order Spec). Not like-for-like with GPU numbers (wall vs board).

## 2. Supplemental findings from the same measurements

### 2.1 Layer-2 failure audit — truncation ruled out, failure modes catalogued

The layer-2 audit (Materials 11) compared every failed tablet answer to gold across all divisions: 30 tool failures + 50 VLM 1× failures + 40 VLM 2× failures = 120 rows total. CSV equals raw byte-for-byte; no near-cap truncation on any VLM row. Key patterns:

- **Tool — wrong tool chosen for semantically distinct operations:** `nofit_deploy` chose `scale_service`; `nofit_reboot_host` chose `restart_service`. These are genuine reasoning failures (the model picked the nearest available action).
- **Tool — refused to act when it should have:** `update_paid` and `update_resolved` returned `{"tool": "none", "arguments": {}}` 5 of 5 times each (4 compact + 1 pretty-printed JSON, same answer). The model declined to update records.
- **Tool — incomplete arguments:** `readlog_words` omitted the required `service` argument (5/5). `scale_double` got the replica count right but used `search_service` instead of gold `search` (4/5) — stays a miss per ruling #2.
- **VLM — counting failures are total and persistent:** `h_count_red7`, `h_count_star6`, `h_count_tri9` are 0.0 at every image size (1×, 2×, 4×). The model cannot count small-object scenes regardless of resolution.
- **VLM — distractor confusion:** `h_bars_value` consistently read the wrong number (answered 45 instead of gold 35, 8/8 at 1× and 5/5 at 2×). `h_table_oldest` picked the wrong name from a table: Alice ×4 and Bob ×1 at 1×; Alice ×5 at 2×.
- **VLM — left/right reversal:** `h_arrow` answered "right" 8/8 times at 1× and 4/4 at 2× when gold is "left". Spatial orientation is weak.

### 2.2 Anchors moved — calibration n, not the grader

Per the order spec: `rotate_matrix` ("always pass", 5/5 seeds) reads 0.82 per tablet at N=9 over its 9 matrix cells at 1.5B and 0.37 at 0.5B; `group_anagrams` ("never", 0/10 seeds) reads 0.11 at 1.5B and 0.00 at 0.5B (N=9 cells). These are the N=9 cell values from matrix.md and matrix-05b.md respectively, not pooled across all 54 samples per anchor. The 0.0/1.0 labels at n ≤ 10 were extremes of small samples. Treat "always/never" from ≤10 seeds as "≥0.8" / "≤0.2".

Source: Materials 2 (matrix anchors footnote) and Materials 3.

### 2.3 VLM majority-vote matrix task-level detail

At the per-task level (Materials 9), the 7 VLM vote tasks break into three quality tiers:
- **Easy for this model:** h_light_yellow, h_shape_color, h_ui_checked — 1.00 at every N for both per-tablet and majority; voting adds nothing because they already pass. h_bars_short is not 1.00 throughout on per-tablet (1.00 / 0.78 / 0.93 / 0.85 at N = 1/3/5/9), though its majority column stays 1.00 — note that all four were "sometimes" in calibration and read higher here, which is itself a caution on the calibration labels (different seeds, small n).
- **Sometimes right, voting does not help:** h_corner (per-tablet 1.00 → 0.52 as N grows; majority 0.33 → 0.67), h_bars_value (near-zero across all N).
- **Never:** h_arrow is 0.00 at every N and every selector.

### 2.4 VLM fleet: qwen3-vl:8b never saw a bigger image

Per the order spec, Ollama resizes its input to a fixed budget for qwen3-vl:8b: ~1,109 prompt tokens per question at 1× and ~1,111 at 2×. The 27Bs went from ~175 to ~630 tokens on the image. Its 2× row is a repeat with thinking on, not an image-size test. This means qwen3-vl:8b's apparent "size invariance" across rungs is partly a measurement artefact — it was tested at the same effective resolution both times.

### 2.5 Coding fleet energy gap

The 9B coder costs 353 J per solved task above idle vs 1,173 J for the 27B; including idle, 596 J vs 1201 J per solved task. Per-attempt, the 9B is 0.792; with best-of-3 it reaches 1.000 across all 8 in-band tasks. The 27B passes on the first attempt. On these tasks, the cheaper rung reaches ceiling with two extra attempts.

## 3. Recommendations requiring later implementation or policy

### 3.1 Retest coding best-of-N with a second task family

The primary finding (best-of-N rises as independent draws predict) requires one retest to graduate: run the same ensemble matrix on a different set of hard coding tasks (e.g., medium-complexity algorithmic problems from a separate pool). If the shape holds — flat per-tablet, rising best-of-N with verifier, dead majority vote — the reading graduates.

**Cost:** Reuses the existing hard-bench machinery (the order spec's matrix driver), one matrix order. The tool division has no middle band to retest; this would be coding only.

### 3.2 VLM at 2× with a tablet majority-vote matrix

Not pursued. The voting matrix reshuffles the tablet model's right and wrong answers but does not create correct ones (§1.7, §2.3), and the calibration at 1×/2×/4× shows image size is not the tablet's limit (§1.6). Re-running it at a different resolution would cost tablet wall time without adding discriminating information.

### 3.3 Option A — priced coding entries for the Shape Catalogue (S2 vs S6)

The Hard Bench produces two priced entries for area B's coding shape, not a routing choice (that belongs to area C's scheduler and Phase 7):

| shape | what it is | success | wall max s | energy / contention note |
|---|---|---|---|---|
| **S2** — 9 tablets, 1.5B coder, best-of-N with verifier | the swarm option on hardware otherwise idle | 0.917 at N=9 | ~49 s window wall | energy per solved for this N=9 shape NOT yet measured (the ≈ 250 J wall FLOOR is the single-attempt 1.5B coder, not the ensemble); contention cost is low — tablets are idle during normal operation |
| **S6** — BigHonker 9B, best-of-3 | the GPU option on HAI's chat brain | 1.000 | ~3.9 s per answer | 353 J above idle; contention cost is real — BigHonker is HAI's conversational tier and running coding displaces chat duty |

The charter's §52 requires accounting for resource contention, not just energy. The tablets are otherwise idle; BigHonker is not. Which of these two shapes a given job takes depends on the scheduler's view of current contention, urgency, and the task's tolerance for ~49 s vs ~4 s — a per-job decision that belongs to area C and cannot be decided before Phase 6 (Voltron) produces its own priced entry for comparison in Phase 7.

### 3.4 Option B — VLM evidence for the area A 8 GB tournament (Phase 3)

Under champion-until-beaten (§10), a model is adopted on actual lab work, not on one benchmark run. The fleet VLM results provide three rows for the 8 GB tournament's evidence base:

| candidate | per-attempt right (2×, thinking ON) | J per solved | host |
|---|---|---|---|
| qwen3-vl:8b | 1.000 | ≈ 260 | BigHonker (num_ctx 4096) |
| qwen3.8:27b | 1.000 | ≈ 940 | Mothership |
| qwen3.6:27b | 1.000 | ≈ 4,200 | Mothership |

qwen3-vl:8b reached 24/24 on the hard VLM pool at the lowest energy of the fleet VLM rungs. It enters the 8 GB tournament with the best numbers measured here; adoption waits for the tournament's own test protocol (respects plans, correction, failure-recovery per §45–47), not this one-run measurement.

### 3.5 Parking: refiner layouts and judge calibration

Per the order spec, refiner layouts remain excluded until the transcript-capture defect is fixed. Judge calibration (picking among tablet answers where nothing can verify them) is parked to the orchestration phase. These recommendations carry forward from Phase 5 unchanged.

## Evidence limits and retest queue

**What these numbers are not:**
1. One task family per division, one seed block per cell, one run per fleet rung. None has been reproduced with a second task set or a second seed block.
2. Tool has no middle band at 1.5B — the ensemble matrix was not run for this division because there is nothing to vote over. This may change at other model sizes.
3. The VLM majority-vote matrix used 1× images on the tablets; it is not re-run (see §3.2).
4. Tablet energy figures are wall-side floors, not per-device measurements. Battery draw during load was not captured by the APC meter.
5. Per-task N=3 seeds per cell is enough to see a spread but not enough to characterise variance reliably for planning purposes.

**Retest queue (ordered by impact on Phase 7):**
1. **Coding best-of-N shape on a second coding task family** — the one retest that can graduate the primary reading into policy-ready status.
2. **Phase 6 — Voltron (S5)** — Phase 7 compares swarm with Voltron by task class; Voltron has no priced numbers yet.
3. **qwen3.6 vs qwen3.8 energy and quality** — moves to the area A 24 GB tournament (champion-until-beaten), not a Hard Bench retest.
4. **Tool at larger effective model size on the Fellowship** — the 1.5B has no middle band; the size question is a Voltron / Tolkien-host 3B question, parked behind Phase 6.

## Traceability and generated artifacts

**Order header:** Hard Bench closeout, layer 3 draft. Ensemble design gate from [[34-Hard-Bench-Order-Spec]].

**Product directories:**
- `tabfoundry-jobs\campaigns\hard-bench-20260926\` — coding calibration
- `tabfoundry-jobs\campaigns\hard-bench-20260927\` — coding matrix (1.5B), 0.5B matrix, fleet coding rungs, fleet tool+vlm rungs, energy measurements
- `tabfoundry-jobs\campaigns\hard-bench-20260928\` — VLM 4× calibration

**Material source mapping:**

| material | source file | section(s) sourced |
|---|---|---|
| Materials 1 | hard-bench-20260926\calibration.md | §1.1 coding calibration table |
| Materials 2 | hard-bench-20260927\matrix.md | §1.2 coding ensemble matrix, §2.2 anchors |
| Materials 3 | hard-bench-20260927\matrix-05b.md | §1.3 size ladder (0.5B) |
| Materials 4 | hard-bench-20260927\fleet\fleet.md | §1.4 coding fleet rungs, §1.9 energy |
| Materials 5 | hard-bench-20260927\calibration-tool.md | §1.5 tool calibration |
| Materials 6 | hard-bench-20260927\calibration-vlm.md | §1.6 VLM 1× calibration |
| Materials 7 | hard-bench-20260927\calibration-vlm-2x.md | §1.6 VLM 2× calibration |
| Materials 8 | hard-bench-20260928\calibration-vlm4x.md | §1.6 VLM 4× calibration |
| Materials 9 | hard-bench-20260927\matrix-vlm.md | §1.7 VLM majority-vote matrix, §2.3 task-level detail |
| Materials 10 | hard-bench-20260927\fleet-tv\fleet_tv.md | §1.8 fleet tool + VLM rungs |
| Materials 11 | hard-bench-20260927\layer2_failures.md | §2.1 layer-2 failure audit |

**Layer-2 auditor:** `layer2_truncation_check.py` (Claude main session, 2026-09-28), confirmed no truncation on any of the 120 failed rows. Grader false negatives ruled out per the owner's ruling on the tool argument grading policy.
