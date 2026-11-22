<!-- SUPERVISOR AUDIT RECORD (Claude main session, 2026-09-26) — precedes HAI's draft verbatim -->
> ## ✅ CANONICAL PHASE 5 CLOSEOUT — the owner ruled 2026-09-26 (late evening)
> 1. **Routing: keep ONE tablet per task, PROVISIONAL.** the owner: *"keep 1 for now, although we will really be
>    deciding this once we're done with this project and get full tablet information."* The redundancy-pool
>    option in §3.2 stays open for that later decision.
> 2. **Refiner layouts PARKED** until the transcript-capture defect (§3.1) is fixed and they are retested on a
>    harder bench. the owner: *"yeah sure."*
> 3. **Canonical status:** the owner deferred (*"I don't care"*); the supervisor's recommendation applies — this
>    document is the Phase 5 closeout as HAI revised it, body verbatim below, with one framing sentence added
>    HERE rather than in the body: **the harder task set the retest queue asks for is a Phase 7 prerequisite
>    (swarm vs Voltron by task class), not a Phase 5 retest.** Phase 5 delivered exactly what its design
>    defines as its output — per task type, the smallest team shape reaching the quality ceiling (one tablet
>    on the Phase 4 task sets) — as the input to Phase 6 and Phase 8. Its numbers are a floor on known
>    ground, not a verdict on ensembles for harder work. Spec for that bench: [[34-Hard-Bench-Order-Spec]].
>
> **Supervisor addendum, same night (after the ruling, found while building the hard bench):** the payload
> every Phase 5 arm ran (`map_vlm_swarm.py`, all four divisions) decodes at `--temp 0 --seed 1`. Same-model
> tablets therefore produced near-identical outputs by construction; the only diversity an ensemble could
> vote over was hardware/thread noise (which is why coding `any_ok_rate` still crept to 1.0 at N=10 while the
> mean fell). **The flat result is partly a property of deterministic decoding, not only of the tasks.** The
> closeout's numbers stand as measured; this bounds what they mean for ensembles in general. The hard bench
> samples deliberately (temperature > 0, seed = replicate + 1, the contract `map_swarm_code.py` already has).
>
> **Layer 3 audit record** (kept for traceability). HAI (Kirk Absurd lane, route heavyweight,
> drafting on qwen3.6:27b, critique pass on qwen3.6:35b-a3b) drafted from the layer-1 reduce
> (`summary.md` verbatim), the two-reviewer layer-2 table and the team-arm regrade table, under three rulings
> the owner made: answers are graded, transcripts are the "how" (regraded numbers canonical); `t_word2` replaces
> the clipped `t_word`; layer 2 is double-sourced at 0/18 grader false negatives.
> **Runs:** first draft `abr-1790480131-8bdf29` (462 s); revision `abr-1790480882-f947b0`.
> **Audit:** every numeric token traced to the packet material or the two named library docs
> (`phase5_layer3_audit.py`: first draft 454/454, revision 491/491; instrument proven on planted decoys).
> Seven sentence-level corrections were sent back with the contradicting ladder row and all landed:
> coding-swarm "flat" → declines to 0.814 at N=10; majority ties 1/4/1; coding best-of-N cost 24.7→50.1 s;
> N=3 redundancy penalty 2–14 s; heterogeneous chat 9 of 10 correct after the digit-vs-word fix;
> post-overlay inventory 816/896 (788/868 labelled pre-overlay); preamble removed.
> **the owner rules on:** §3.2 (single-tablet default vs. a small redundancy pool), §3.3 (refiner layouts
> parked until the capture defect is fixed), and whether this draft becomes the canonical closeout.
> Source draft: `tabfoundry-jobs\campaigns\phase5-appraisal-20260926\layer3_draft.20260926-224804.md`
> (sha256 9d2e54926249be10). Body below is HAI's revision verbatim.

# Project 34 Phase 5 closeout — tablet ensembles (N-ladder + team arms)

**Status:** CANONICAL — layer 3 revision 1, supervisor-audited, the owner ruled 2026-09-26 (rulings in the block above; title and this line are the only two lines of HAI's revision changed)

## Governing sources and decision rule

This report applies, rather than replaces, the existing design and rubrics:

- [[34-Phase-5-Ensemble-Design]] — N-ladder specification (N=1 to 10), three strategies per division (best_of_n, independent_swarm, majority_vote), structured vs. heterogeneous team-arm layouts, task manifest.
- [[34-CHARTER]] — four divisions, deterministic verification, §45–48 behavior criteria, cheapest reliable intelligence principle.
- [[HANDOFF-Project34-Phase5-after-Codex-20260908]] § Appraisal gate — inputs/method/deliverables/cleanup contract for the appraisal order.
- [[Fellowship-Gauntlet-Conclusion-2026-08-31]] — Phase 4 closeout shape and decision-rule precedent.

The primary decision rule is the charter's rubric. The ensemble experiment contributes measured scale-up fit (how per-tablet accuracy changes with N) and structured-team utility (whether adding critic/verifier/refiner stages helps or hurts on these tasks). Supplemental cost analysis (wall time per cell, straggler behavior) is explicitly labeled and does not redefine Phase 4 champions.

**Rulings already made by the owner (bind this draft):**
1. Team arms are graded on the model's final answer only. The refiner/critic payload leaked whole llama-cli transcripts into `answer`; the reduce cut the reply back out (27 arms affected, 30 verdicts flipped, 0 replies unlocated). The `success_regraded` / `ok_regraded` numbers are canonical; the raw `success` column is reported only as the instrument defect it exposes.
2. The original `t_word` vision cell used a clipped image (gold unreadable). It was re-rendered and re-run 1:1 as `t_word2`; the re-run cells replace the originals under the name `t_word` (28 cells, all pass). The Phase 4 "positional read floor" finding is retracted on that basis.
3. Layer 2 is double-sourced: the 18 real-reply failure packets were audited by qwen3.6:27b and again by qwen3.8:27b, and every "the grader was wrong" claim was refuted by running the payload's own checks — grader false negatives on real replies: 0/18 under both reviewers. The deterministic tables stand unqualified.

## Executive conclusion

The ensemble experiment measured whether adding more tablets (N-ladder) or structuring them into multi-agent layouts (team arms) improves per-tablet success beyond what a single Qwen2.5-1.5B tablet already achieves on the Phase 4 task sets. Three findings govern the closeout:

1. **Scale-up is flat.** For three of four divisions, per-tablet accuracy at N=1 equals the best accuracy observed at any N up to 10. The only movement is coding best_of_n (verifier), which reaches 1.0 at N=9 — driven entirely by a single task (`fizzbuzz`) that passes at N=9 and N=10 but fails at N≤8, read as single-run noise rather than a reliable knee. VLM accuracy after the `t_word2` overlay is flat at 0.857 (6/7) for independent_swarm and majority_vote; the sole remaining miss (`p_light`) is identical on every tablet and reflects a model failure, not an ensemble one. For coding independent_swarm, the ceiling is at N=1 (0.857) and per-tablet accuracy declines to 0.814 by N=10 while any_ok_rate rises to 1.0 — adding tablets lowers the mean and raises the chance that at least one is right, which only helps where a selector exists (none for coding without a verifier).

2. **Refiner stages degrade answers.** In structured team arms, layouts without a refiner produce the solver's answer unchanged (those rows equal `solver` by construction). Only refiner layouts change the final output, and they do so for the worse on coding (regraded 0.625 for solver_critic_refiner, 0.375 for solver_critic_verifier_refiner) and tool (0.375 and 0.25 respectively). The raw `success` columns read lower still because they graded llama-cli transcripts, not the model reply — an instrument defect that must be fixed before any future team run.

3. **Heterogeneous specialists are not better than homogeneous ones.** The heterogeneous coding ensemble (32 arms) scores 0.875 regraded — identical to the structured coding `solver` (8 arms). Heterogeneous tool (40 arms) is 1.0, also identical. Heterogeneous VLM (14 usable of 21; 7 excluded) is 0.786, which does not exceed the homogeneous swarm's 0.857 after overlay. Heterogeneous chat (10 arms) scored 0.0 raw and 0.9 regraded — but all regrade flips came from the grader demanding the digit `5` when the model answered "five", a grader defect, not an ensemble finding; furthermore, chat had only one task (`sisters`), which supports nothing.

**No ensemble shape earns default routing.** The cheapest reliable intelligence remains a single Qwen2.5-1.5B tablet (or Coder-1.5B for coding). Adding tablets costs wall time without improving accuracy on these tasks; adding structure either leaves the answer unchanged or degrades it through the refiner stage.

## 1. Conclusions required by the rubric

### 1.1 N-ladder ceiling — what scale-up achieves

| division | strategy | success@N=1 | best_success | smallest_n_at_best | wall_at_best_s | note |
|---|---|---|---|---|---|---|
| chat | all three strategies | 1.0 | 1.0 | 1 | 12–15 | tasks saturate at N=1 — no discriminating power |
| tool | all three strategies | 1.0 | 1.0 | 1 | 28–33 | same (swarm per-tablet acc dips to 0.98 at N≥6) |
| coding | independent_swarm | 0.857 | 0.857 | 1 | 24 | ceiling at N=1; per-tablet accuracy declines to 0.814 by N=10 while any_ok_rate rises to 1.0 |
| coding | best_of_n (verifier) | 0.833 | 1.0 | 9 | 34 | only `fizzbuzz` moves: 0 at N≤8, pass at 9 and 10 — single-run noise |
| coding | majority_vote | 0.857 | 0.857 | 1 | 25 | voting on canonical code HURTS at N=3/5/9 (splits + ties: 1/4/1) |
| vlm | independent_swarm | 0.857 | 0.857 | 1 | 29 | after `t_word2` overlay: 6/7 at every N; only miss is `p_light` (model failure, identical on every tablet) |
| vlm | best_of_n (ORACLE) | 0.833 | 0.833 | 1 | 30 | oracle upper bound — no selector exists for vision; 6-task common set |
| vlm | majority_vote | 0.857 | 0.857 | 1 | 26 | same single miss |

Source: summary.md § Ceiling table

**Reading:** For chat and tool, the task sets are too easy for a 1.5B model to be discriminated by scale — they measure wall time, not quality. For coding, independent_swarm peaks at N=1 (0.857) and per-tablet accuracy declines to 0.814 by N=10 while any_ok_rate rises to 1.0 — adding tablets lowers the mean and raises the chance that at least one is right, which only helps where a selector exists. Majority vote on source code degrades (ties count as failures: 1 tie at N=3, 4 at N=5, 1 at N=9); best_of_n with a verifier reaches 1.0 only because one task (`fizzbuzz`) randomly passes at N=9 and N=10 out of ten attempts — not a reproducible knee from a single run. For VLM, after the `t_word2` overlay retracted the Phase 4 "positional read floor" finding, accuracy is flat at 6/7; the only miss is a real model failure (`p_light`: gold green, SmolVLM2-500M says red 151/151 times).

### 1.2 N-ladder full table — wall time cost of adding tablets

The complete ladder (all strategies, all N values) shows monotonically increasing or flat wall time with no corresponding accuracy gain in three of four divisions. Selected observations:
- Coding independent_swarm mean-of-window-max wall time: 23.8 s at N=1 → 34.7 s at N=10 (per summary.md).
- The single-arm all-ten windows show **80 s at N=10** for coding independent_swarm (straggler/contention; one variable to chase before quoting a cost figure).
- Tool independent_swarm success dips from 1.0 to 0.9875 at N=10 despite every task still being present — per-tablet accuracy is not perfectly maintained in the swarm mean at high N, though `any_ok_rate` remains 1.0 (at least one tablet succeeds).
- Coding majority_vote shows ties at N=3 (1), N=5 (4), and N=9 (1); these count as failures.

Source: summary.md § Ladder

### 1.3 Team arms — structured layouts, canonical review of final answer

| family | division | layout | arms | complete | excluded | success (raw) | success_regraded (CANONICAL) |
|---|---|---|---|---|---|---|---|
| structured | coding | solver / solver_alone / solver+critic(s) / parallel_verifiers | 8 each (+24) | — | 0 | 0.875 | 0.875 |
| structured | coding | solver_critic_refiner | 8 | 8 | 0 | 0.375 | **0.625** |
| structured | coding | solver_critic_verifier_refiner | 8 | 8 | 0 | 0.125 | **0.375** |
| structured | tool | solver / solver_alone / solver+critic(s) / parallel_verifiers | 8 each (+24) | — | 0 | 1.0 | 1.0 |
| structured | tool | solver_critic_refiner | 8 | 8 | 0 | 0.25 | **0.375** |
| structured | tool | solver_critic_verifier_refiner | 8 | 8 | 0 | 0.625 | **0.25** |
| structured | chat | every non-refiner layout | 1 each | — | 0 | 0.0 | **1.0** |
| structured | chat | the two refiner layouts | 1 each | — | 0 | 0.0 | 0.0 |

Source: team_arms.csv; success_regraded = ok_regraded/complete after cutting llama-cli transcripts and fixing the digit-vs-word grader defect

**Reading:** In every layout without a refiner, the final answer IS the solver's answer, so those rows equal `solver` by construction (0.875 coding, 1.0 tool). Only layouts with a refiner stage change the output. On coding, adding critic → refiner lifts raw success from 0.375 to regraded 0.625 — still below the solver's 0.875. Adding verifier between critic and refiner degrades further to 0.375 regraded (raw was 0.125). On tool, the pattern is steeper: solver_critic_refiner drops from 1.0 to 0.375 regraded (raw 0.25), and solver_critic_verifier_refiner drops to 0.25 regraded (raw 0.625 — the raw column graded the echoed draft's correct tool call found in the transcript). Chat non-refiner layouts all read 1.0 regraded because the grader originally demanded the digit `5`; the model answered "five", which is correct. The two chat refiner layouts remain 0.0 (the refiner stage itself produced an incorrect final answer on the single task `sisters`).

### 1.4 Team arms — heterogeneous specialists

| family | division | layout | arms | complete | excluded | success_regraded (CANONICAL) |
|---|---|---|---|---|---|---|
| heterogeneous | chat | specialists | 10 | 10 | 0 | 0.9 |
| heterogeneous | coding | specialists | 32 | 32 | 0 | 0.875 |
| heterogeneous | tool | specialists | 40 | 40 | 0 | 1.0 |
| heterogeneous | vlm | specialists | 21 | 14 | 7 | 0.786 |

Source: team_arms.csv

**Reading:** Heterogeneous coding (32 specialist arms) is identical to structured coding `solver` (0.875). Heterogeneous tool (40 arms) is also identical (1.0). Heterogeneous VLM (14 usable of 21; 7 terminal hard-fit exclusions) scores 0.786, which does not exceed the homogeneous swarm's 0.857 after overlay. Nine of ten heterogeneous chat arms answered correctly once the digit-vs-word grader defect was fixed; the one remaining miss answered "four". One task (`sisters`) supports nothing further.

### 1.5 Grader integrity — layer 2 audit

| reviewer | rows audited | genuine_error | correct_but_misgraded | formatting_only | misgraded claims refuted by code | grader false negatives on real replies |
|---|---|---|---|---|---|---|
| qwen3.6:27b | 18 | 14 | 3 | 1 | 3/3 | 0/18 |
| qwen3.8:27b | 18 | 16 | 1 | 1 | 1/1 | 0/18 |

Source: layer2_verdicts.json, layer2_verdicts.qwen38.json (18 identical pins, served model verified per row)

**Reading:** Both reviewers flagged a small number of rows as "the grader was wrong." Every such claim was refuted by running the payload's own check lambdas against the disputed answers. Under qwen3.6:27b all 3 disputes were refuted; under qwen3.8:27b the 1 dispute was refuted (its own reason text stated the grader was correct while its verdict label said otherwise). Grader false-negative rate on real model replies is 0/18 under both reviewers. The deterministic tables in §§1.1–1.4 stand unqualified. Side note: reviewer accuracy differed (qwen3.6 wrong on 3/18, qwen3.8 wrong on 1/18), and qwen3.8 carried a `think_leak` / duplicate-verdict parsing issue on 2/18 rows requiring parser repair before readback — adoption of 3.8 as HEAVY_MODEL is backlog-gated on the leak.

## 2. Supplemental findings from the same measurements

### 2.1 Instrument defects discovered and corrected

All found by reading the rows, none by a model. Each correction is reproducible; original verdicts are kept beside corrected ones.

| # | defect | effect | correction |
|---|---|---|---|
| 1 | Window-level `usable` is not the unit: 80/465 window files VOID because one packed arm came up a tablet short while the other arm in the same file is complete + control-green | Naive window filter dropped 150 good arms | Choose per ARM across all files of a window; post-`t_word2`-overlay inventory: 816/896 arms `ok`, 80 `present_but_unusable` (coverage string ledgered in `inventory.json`; pre-overlay count was 788/868) |
| 2 | Task sets differ across N once arms drop, confounding N-vs-N comparisons | Ladder rows compared different tasks | Each (division, strategy) row uses the task set present at EVERY N; N=5 variance arms averaged within task first |
| 3 | Refiner-stage answers are whole llama-cli transcripts (spinner, ASCII logo, help text, echoed prompt, THEN the reply) — 14/16 coding + 11/16 tool refiner rows, 2 coding critic rows | Driver's `canonical_review` graded the transcript: coding refiner layouts read 0.125–0.375; one tool layout read 0.625 because the grader found the *echoed draft's* correct tool call | Same grader on the reply only (cut at the CLI echo marker or intact prompt; 0 unlocated). Both directions moved. The refiner payload's capture is a pipeline defect to fix before any future team run. |
| 4 | Chat grader demanded the digit `5`; 16 of 19 chat team-arm "failures" answered the word "five" | Every chat team layout read 0.0 | Driver `canonical_review` now accepts `5\|five` (whole word, case-insensitive) — the N-ladder payload already matched gold as a whole word |
| 5 | `t_word` vision cell is defective: image clipped, reads "the quick brown fo" with last word cut off; gold "fox". Model's "brown" (136/136 times) is the last complete word in the picture. The same cell scored against every VLM in Phase 4 | 136 of the 287 vision failures are the test, not the model. Phase 4 § "all three fail the positional read" was built on this image | Re-rendered to fit (`t_word2.png`), 15 windows re-run 1:1; 150/150 tablet answers "fox". `t_word2` replaces `t_word` (26 original cells superseded). Phase 4 doc corrected in place with dated strike-through. |

Source: 34-Phase-5-Appraisal-Layer1.md §0

### 2.2 Wall time as a function of N — cost side

For coding independent_swarm, the mean-of-window-max wall times from summary.md show:
- N=1: 23.8 s
- N=5: 30.3 s
- N=9: 33.2 s
- N=10: 34.7 s

However, the single-arm all-ten windows (the actual cost of running ten tablets in one window) reach **80 s at N=10** — straggler/contention effect not captured by per-cell means. One variable to chase before quoting a production cost figure.

For VLM independent_swarm, wall time rises from 29.3 s (N=1) to 46.1 s (N=9), then falls slightly to 45.8 s (N=10). Accuracy is flat at 0.857 throughout, so every second above N=1 is pure cost with no quality return.

Tool independent_swarm shows accuracy dipping from 1.0 to 0.9875 at N=10 while wall time reaches 38.5 s — adding tablets here is actively counterproductive (accuracy loss for higher cost), though `any_ok_rate` remains 1.0.

Source: summary.md § Ladder, 34-Phase-5-Appraisal-Layer1.md §2

### 2.3 Coding best_of_n and the `fizzbuzz` noise event

The only division/strategy pair showing accuracy growth in N is coding best_of_n (verifier): 0.833 at N=1 through N=8, then 1.0 at N=9 and N=10. The reduce identified that the only task moving is `fizzbuzz`: it fails at N≤8, then passes at N=9 and N=10. Under a single seeded run this is noise, not a knee — one task out of six happened to produce a verified-correct output on its ninth and tenth attempts. This does not establish that best_of_n with a verifier is a reliable improvement over a single solver arm (which scores 0.875 on the same task set).

Source: summary.md § Ceiling, 34-Phase-5-Appraisal-Layer1.md §2

### 2.4 Majority vote on code is counterproductive

Coding majority_vote shows ties (counted as failures per caveat) at N=3 (1 tie), N=5 (4 ties), and N=9 (1 tie). The `success` column drops from 0.857 at N=1 to 0.714 at N=3, 0.643 at N=5, 0.857 at N=7, then 0.714 at N=9 — the `any_ok_rate` is higher (individual tablets sometimes succeed when the majority doesn't agree), but the ensemble verdict fails on splits and ties. Voting on canonical source code among identical models with identical seeds produces agreement only when all tablets produce the same output; divergence leads to failure, not improvement.

Source: summary.md § Ladder

## 3. Recommendations requiring later implementation or policy

### 3.1 Fix the refiner-stage transcript capture before any future team run

The refiner payload currently captures whole llama-cli transcripts (spinner, logo, echoed prompt, then the reply) into the `answer` field. This is a pipeline defect that made it impossible to grade those arms correctly without post-hoc correction. Any future team-arm experiment using refiner stages must capture the model reply only. Cost: unknown [UNSOURCED: development effort for the payload fix]. Priority: blocker — no further team-arm data is valid until fixed.

### 3.2 Do not add tablets for quality; add them only if latency budget permits and redundancy is desired

For chat and tool divisions, single-tablet accuracy is already 1.0 on these task sets. Adding tablets costs wall time (24.7 s → 50.1 s for coding best_of_n at N=10; 29 s → 46 s for VLM swarm at N=9) without improving accuracy. For coding, independent_swarm peaks at N=1 (0.857) and per-tablet accuracy declines to 0.814 by N=10 while any_ok_rate rises to 1.0 — adding tablets lowers the mean and raises the chance that at least one is right, which only helps where a selector exists (none for coding without a verifier). The only scenario where multiple tablets provide value is redundancy: if one tablet is unavailable or times out, others may still complete. This is a reliability decision, not a quality one. Options:
- **Option A — single-tablet default.** Continue routing to one Qwen2.5-1.5B (or Coder-1.5B for coding). Wall time per task: ~12–32 s depending on division. Measured accuracy from this experiment is consistent with Phase 4 champions.
- **Option B — small redundancy pool (N=3).** If tablet availability is a concern, submit the same task to three tablets and accept the first verified response (coding/tool) or any response (chat). Wall time penalty at N=3: roughly 2–14 s above N=1 depending on division (summary.md § Ladder N=3 rows: chat 14.4–16.2 s vs 12.3–14.8 at N=1; coding 29.7–30.9 s vs 23.8–24.7; tool 29.4–34.8 s vs 27.8–32.5; vlm 34.7–40.2 s vs 26.1–29.9). No accuracy gain on these tasks; potential gain on unmeasured tasks where occasional single-tablet failures exist.
- the owner rules on which option, if either, becomes policy.

### 3.3 Do not adopt refiner layouts until the capture defect is fixed and their utility is re-evaluated

On these task sets, every structured layout with a refiner produces worse accuracy than the solver alone (coding: 0.875 → 0.625 or 0.375; tool: 1.0 → 0.375 or 0.25). This may be task-dependent — the Phase 4 tasks are relatively simple for a 1.5B model — but it is a strong signal that refiner stages add cost without improving output on this workload. Before considering refiner adoption:
1. Fix the transcript capture defect (§3.1).
2. Re-run refiner layouts on a harder task set where the solver alone does not already score 0.875–1.0.
3. Measure whether the refiner improves accuracy enough to justify its wall-time cost (refiner layouts are inherently slower due to extra stages).

### 3.4 Heterogeneous specialists offer no advantage over homogeneous tablets on these tasks

The heterogeneous coding ensemble (32 arms of mixed models) scores 0.875 — identical to the homogeneous `solver` layout (8 arms, all Qwen2.5-Coder-1.5B). Heterogeneous tool (40 arms) is 1.0, also identical. Heterogeneous VLM (14 usable of 21; 7 excluded by hard-fit) scores 0.786, below the homogeneous swarm's 0.857 after overlay. There is no evidence from this experiment that mixing models in an ensemble improves accuracy over using a single model on every tablet. If heterogeneous ensembles are considered for future work, they should target a task set where a single model shows systematic weaknesses that another model can compensate for — not these Phase 4 tasks, which are largely saturated.

### 3.5 Retract the Phase 4 "positional read floor" finding

The Phase 4 closeout stated "all three VLMs fail the positional read" based on the `t_word` vision cell. That cell used a clipped image (gold unreadable); after re-rendering as `t_word2` and re-running, 150/150 tablet answers are correct. The Phase 4 document has been corrected in place with a dated strike-through. No further VLM positional-read finding is supported by this data.

Source: 34-Phase-5-Appraisal-Layer1.md §0, item 5

## Evidence limits and retest queue

### Evidence limits

- **One seeded run per cell.** All accuracy and cost numbers are single-point measurements, not distributions. The N=5 variance arms (identical-cohort repeats) are the only repeated cells; they were averaged within task before inclusion in the ladder. Findings describe fit and cost at one point, not statistical confidence.
- **Chat/VLM best_of_n is an ORACLE upper bound.** No selector exists for chat or vision outputs that could implement real best-of-N selection (unlike coding/tool, where a verifier checks correctness). The reported best_of_n accuracy for these divisions assumes an omniscient selector — it is not achievable in production.
- **Majority ties count as failures.** Where the majority vote splits evenly or produces no clear winner, the cell is scored as failed. Tie counts are reported per cell in the ladder table.
- **Team-arm success is the driver's deterministic canonical review of the FINAL stage answer only.** Intermediate outputs (solver drafts, critic feedback) are not graded — only whatever the last stage emitted as `answer`. After the regrade corrections, this means the model's actual reply, not a transcript.
- **Chat and tool task sets saturate at N=1 for a 1.5B model.** These divisions measure wall-time cost of adding tablets, not quality improvement — accuracy is already 1.0 at a single tablet on these tasks.
- **Chat team arms have only one task (`sisters`).** A sample of n=1 supports no conclusion about chat ensemble utility. The regrade flip (raw 0.0 → regraded 1.0 for non-refiner layouts) was a grader fix (digit vs. word), not an ensemble finding.
- **7 VLM arms excluded by hard-fit.** These are terminal exclusions; their absence from the heterogeneous VLM score (0.786 on 14 usable arms) is expected, not a shortfall.

### Retest queue

| item | reason | priority |
|---|---|---|
| Qwen2-VL-2B re-run on aragorn (class A) with `t_word2` overlay | The Phase 4 overlay was completed for SmolVLM2 tablets; the 2B/Qwen2-VL re-run was queued but not done as of layer 1. Needed to confirm the class-A quality-specialist score survives the corrected image. | Medium — completes the `t_word2` correction story |
| Retest coding best_of_n with multiple seeds | The N=9/10 accuracy lift on `fizzbuzz` is a single-run event. Determining whether it reproduces under different seeds would distinguish noise from a real verifier-effect knee. | Low — single-run caveat already stated; no production decision depends on it |
| Re-evaluate refiner layouts on harder coding/tool tasks | If the pipeline defect (§3.1) is fixed, test solver_critic_refiner and solver_critic_verifier_refiner on tasks where the solver alone scores below 0.875 to determine whether refiners help under difficulty. | Conditional — gated on defect fix |

## Traceability and generated artifacts

**Order header:** Phase 5 appraisal, deterministic reduce layer; ensemble design gate from [[HANDOFF-Project34-Phase5-after-Codex-20260908]] § Appraisal gate.

**Product directory:** `E:\SSDShared\tabfoundry-jobs\campaigns\phase5-appraisal-20260926\`

**Artifacts and source mapping:**

| artifact | content |
|---|---|
| `summary.md` | Full N-ladder tables (ceiling + per-N rows), team-arm table, caveats. Source for §§1.1–1.4 ladder numbers, §2.2 wall-time observations, §2.3 `fizzbuzz`, §2.4 majority-vote ties. |
| `summary.json` | Machine-readable equivalent of summary.md. |
| `cells.csv` | Per-cell results: division, strategy, N, task, success, wall time, tie count, arm status. Source for every ladder row number. |
| `team_arms.csv` | Per-arm results for structured and heterogeneous teams: family, division, layout, arms, complete, excluded, success (raw), success_regraded (canonical), banner_arms. Source for §§1.3–1.4 team-arm numbers. |
| `inventory.json` | Per-arm coverage ledger: which arms are `ok` vs. `present_but_unusable`, with the coverage string explaining each unusable arm. Post-`t_word2`-overlay inventory: 816 ok / 80 present_but_unusable of 896 expected (790 cells; pre-overlay count was 788/868). Source for §2.1 defect #1. |
| `grader_fails.json` | The 35 distinct (task, answer) pairs from coding+tool grader failures, plus 3 VLM and 4 chat pairs. Collapsed from 549 raw fail rows. Source for layer-2 pin selection. |
| `review_packets_coding_tool.json` | The 18 real-reply failure packets submitted to layer 2 (after excluding 17 instrument failures: transcript or empty). |
| `layer2_order.json` | Layer-2 eval campaign order manifest (`5f4191f58fdf` for qwen3.6, `de55ff2110f4` for qwen3.8). |
| `layer2_verdicts.json` | qwen3.6:27b audit of 18 pins: 14 genuine_error, 3 correct_but_misgraded, 1 formatting_only; all 3 disputes refuted by code → 0/18 grader false negatives. Source for §1.5 row 1. |
| `layer2_verdicts.qwen38.json` | qwen3.8:27b audit of the same 18 pins: 16 genuine_error, 1 correct_but_misgraded, 1 formatting_only; the 1 dispute refuted by code → 0/18 grader false negatives. Source for §1.5 row 2. |
| `manifest.json` | SHA-256 of every input file consumed by the reduce. |

**Reducer:** `phase5-prep\commission-artifacts\phase5-c02\drivers\phase5_appraise.py` — order header states inputs, method, deliverables, cleanup.

**Layer 2 composer/readback:** `phase5_layer2_campaign.py`, `phase5_layer2_readback.py`
