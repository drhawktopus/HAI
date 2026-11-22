# Project 34 rigby menu review 2026-10-02 - arm evidence (dominated arms are KEPT; only can't-fit or priced-out arms stop)

**Rule in force (the owner 2026-10-02, [[34-Hard-Bench-Order-Spec]] § "When an arm stops being filled"):** an arm stops on
rigby only if it CANNOT FIT or is PRICED OUT (≥ 10× rigby's cheapest solve, on ≥ 2 families, reproduced on rigby).
The "soft" price-outs below (7B f1/f2, MoE tool) are dominated, not priced out, and stay on the menu as fallbacks.
The 14B verdict waits on the ceiling-reference fix (§ "The ceiling"). Evidence is unchanged.

**Resolved 2026-10-02 (build, the owner: "Hit it baby"):** decision 1 is done (the MoE thinking-on f1 and f2 arms are held by the new
arm hold form; 0 of its orders were queued, so no slip was needed) and decision 4's third option is done (cache-warm rows are
excluded from the ceiling reference: rigby now carries f1 62 s, f2 192 s, tool 26 s instead of 47 / 64 / 12). The 14B and the
7B are NOT held (dominated is not a reason to stop). See [[34-Hard-Bench-Order-Spec]] § "Holds" and "Price ceilings".

**Status:** PRELIMINARY (every verdict below; the order spec's rule in [[34-Hard-Bench-Order-Spec]]: a reading graduates only when it holds across a second task family and reproduces once). **Read-only review** written 2026-10-02 ~09:00 for the owner, who asked whether the P34 menu filler needs to "price itself out of some of the arms it's been running" on rigby (Tolkien's CPU lane: i7-3770, 24 GB, no AVX2). No order, slip, hold or config was touched; the verdicts are recommendations for the owner to accept or change. Rule being applied: [[34-CHARTER]] §52 (cheapest source that reliably satisfies the task, accounting for energy and time), priced as in [[34-Hard-Bench-Order-Spec]] §"Filling the GPU menu" (the rigby paragraph and "Price ceilings").

## Bottom line

1. **Price out the Qwen3.6-35B-A3B thinking-ON arms on family 1 and family 2.** 723 of 747 rows were priced out (240/240 and 483/507), 24 rows were solved (all on f2, all correct), and the two arms took 14.65 h of rigby time, 34% of the 42.8 h the menu has used. Same weights with thinking OFF solve the same tasks at 1.000 (f1) and 0.997 (f2) in 11.9 to 20.7 s per solve. Four cells per family remain (7.3 h). This holds at every ceiling the filler has carried (47 to 222 s), so it is not an artefact of the ceiling.
2. **Price out Qwen2.5-14B on all three families.** Another cell is both cheaper per solve and more reliable in every family: f1 the 7B / Coder-3B / MoE-off, f2 the MoE-off (0.997 at 1,178 J vs 0.914 at 3,224 J), tool the 7B (0.990 at 664 J vs 0.601 at 2,281 J). Holds even counting only the rows the 14B was allowed to finish. 10.2 h used (24%); 2.8 h left (f2 and tool).
3. **Qwen3.6-35B-A3B thinking OFF is the standout** (f1 240/240, f2 574/576, both seed sets agree) and **Qwen2.5-7B is the tool champion** (570/576 at 664 J). Keep.
4. **The price ceiling is doing something other than what it reads as.** It is 10x the single cheapest solve any model ever made on the worst-case task, and the references are tiny-model, prompt-cache-warm rows. The tool-family ceiling is now **12.0 s**, below the 14.5 s the MoE needs just to read a cold tool prompt, so a third of its tool rows were priced out for reading the prompt, not for the quality of the answer. Section "The ceiling" has the one-variable proof. The arms I recommend pricing out do not depend on it; a few "priced out" rows already delivered do.
5. About **79 cells / 18.0 h** are still unfilled (filler's own count: missing 79, matches). Of that, **10.1 h is firm price-out, 3.5 h more is soft**, leaving 4.4 h of cells on kept arms.

## What was read, and what was counted

- **237 rigby menu result files** (`E:\SSDShared\tabfoundry-jobs\outbox\hardbench-rigby-m-*.results.json`, first delivered 2026-09-30 09:41:47, last 2026-10-02 08:24:17) parsed in full: **13,762 rows** (13,525 graded + 237 controls). Count asserted non-zero before any table was built. A further 22 earlier `hardbench-rigby-1..20` result files were parsed only for the ceiling reference (259 files total, the same set the filler's own history reads).
- **No earlier review exists** in the library or journals (searched Journal, LLM-Project, Reference for a rigby or menu review); the menu itself began 2026-09-30 09:38, so "since the last review" is the entire menu. This page is the baseline for the next one.
- **Ceilings per order** are from the filler's log (`phase5-prep\commission-artifacts\hard-bench\menu_fill_topup.log`, 237 submissions parsed, one ceiling each); the rule itself from `menu_fill.py` (`PRICE_X = 10`, `rigby_ceiling`).
- **Pass** = the row's own `ok` (the payload's scorer; tool arguments strict, case-insensitive exact, per the closeout's ruling). A priced-out row counts as not solved in the "all rows" figure; "of answered" excludes priced-out rows. Priced-out is a stated measurement, never a fail ([[34-Hard-Bench-Order-Spec]]); both figures are given where they differ.
- **Time per solve** = the order's `wall_s` summed over the arm's orders divided by solved rows (so it includes time spent on priced-out and failed rows, load and control; the honest price of a solve). **Energy is derived, not recorded per order:** J = 99 W (the spec's metered working draw at the wall on the rack's smart plug) x that wall time per solve. The spec states the draw was measured under the MoE and is ASSUMED equal for the small models; no plug hour holds one of them alone. Time and energy are the same ratio on rigby.
- **Statistics:** 95% Wilson intervals on pass over all rows; cells are sampled-decoding replicates so the interval is on rows, not independent items (greedy cells repeat; see "Redundant cells").

## Limit and void check (doctrine 13a)

- Context stated on every one of the 237 orders: **32,768**. Rows with `finish_reason` = `length`: **0** of 13,762. Largest prompt + generation in any row: 5,278 tokens. Finish reasons seen: `stop` 12,294, `priced_out` 1,467, `stopped` 1.
- Controls passed on **237/237** orders; no order reports an infrastructure error. The 1,475 non-empty `error` fields are grader reasons on failed answers (`wrong_tool` 1,101, `wrong_args` 220, assertion failures), not faults.
- So **no cell is void by a context or token cap.** The only void-flagged file is the cancelled order below. Priced-out is a price, not a void, but where a priced-out share is large I say whether the price is real (the arm is dominated anyway) or an artefact of the ceiling.
- Largest runaways under the cap: Qwen2.5-0.5B f1 answers up to 5,187 tokens, Coder-0.5B 1,965 and 2,169 tokens on f1 and tool; none near 32,768.

## Summary table

"Cells" is menu orders delivered of the 12 per arm (4 temperatures x 3 seed sets); the `t0.6` seed-set-1 cells for several arms were delivered earlier by the numbered rigby-N orders and are counted as covered by the filler (that is why its missing count is 79 and not 87).

| arm | family | think | cells delivered / 12 | rows | pass (solved / all rows) [95% CI] | priced out as run | median s per solved answer | s per solve (order wall) | J per solve (99 W) | verdict (PRELIMINARY) |
|---|---|---|---|---|---|---|---|---|---|---|
| Qwen2.5-0.5B | f1 | off | 12 | 360 | 0.233 (84/360) [0.19-0.28] | 3 (1%) | 3.2 | 25.4 | 2518 | keep (grid complete; dominated by 1.5B) |
| Qwen2.5-1.5B | f1 | off | 12 | 360 | 0.400 (144/360) [0.35-0.45] | 0 (0%) | 4.2 | 11.9 | 1179 | keep (grid complete) |
| Qwen2.5-7B | f1 | off | 7 | 210 | 0.824 (173/210) [0.77-0.87] | 0 (0%) | 17.3 | 22.3 | 2212 | price out remaining cells (soft) |
| Qwen2.5-14B | f1 | off | 11 | 330 | 0.424 (140/330) [0.37-0.48] | 113 (34%) | 30.2 | 92.1 | 9119 | **price out** (firm; nothing left to order) |
| Coder-0.5B | f1 | off | 8 | 211 | 0.265 (56/211) [0.21-0.33] | 9 (4%) | 4.7 | 31.3 | 3097 | keep (cheap; dominated) |
| Coder-1.5B | f1 | off | 7 | 210 | 0.386 (81/210) [0.32-0.45] | 0 (0%) | 4.3 | 12.3 | 1220 | keep (cheap; dominated) |
| Coder-3B | f1 | off | 8 | 240 | 0.754 (181/240) [0.70-0.80] | 0 (0%) | 10.2 | 13.4 | 1330 | keep |
| Qwen3.6-35B-A3B (MoE) | f1 | off | 8 | 240 | 1.000 (240/240) [0.98-1.00] | 0 (0%) | 17.9 | 20.7 | 2047 | **keep - excellent** |
| Qwen3.6-35B-A3B (MoE) | f1 | on | 8 | 240 | 0.000 (0/240) [0.00-0.02] | 240 (100%) | - | - | - | **price out** (firm) |
| Qwen2.5-0.5B | f2 | off | 12 | 864 | 0.211 (182/864) [0.18-0.24] | 0 (0%) | 1.3 | 8.2 | 809 | keep (grid complete; dominated) |
| Qwen2.5-1.5B | f2 | off | 12 | 864 | 0.605 (523/864) [0.57-0.64] | 0 (0%) | 2.6 | 5.5 | 541 | keep (grid complete) |
| Qwen2.5-7B | f2 | off | 7 | 504 | 0.839 (423/504) [0.80-0.87] | 0 (0%) | 11.2 | 17.3 | 1710 | price out remaining cells (soft) |
| Qwen2.5-14B | f2 | off | 8 | 537 | 0.914 (491/537) [0.89-0.94] | 4 (1%) | 21.0 | 32.6 | 3224 | **price out** (firm) |
| Coder-0.5B | f2 | off | 7 | 504 | 0.397 (200/504) [0.36-0.44] | 0 (0%) | 1.0 | 4.0 | 395 | keep (cheapest per solve) |
| Coder-1.5B | f2 | off | 7 | 504 | 0.552 (278/504) [0.51-0.59] | 0 (0%) | 2.7 | 6.0 | 593 | keep (cheap; dominated) |
| Coder-3B | f2 | off | 7 | 504 | 0.827 (417/504) [0.79-0.86] | 0 (0%) | 5.9 | 8.2 | 814 | keep |
| Qwen3.6-35B-A3B (MoE) | f2 | off | 8 | 576 | 0.997 (574/576) [0.99-1.00] | 0 (0%) | 10.3 | 11.9 | 1178 | **keep - excellent** |
| Qwen3.6-35B-A3B (MoE) | f2 | on | 8 | 507 | 0.047 (24/507) [0.03-0.07] | 483 (95%) | 72.0 | 1662.0 | 164540 | **price out** (firm) |
| Qwen2.5-0.5B | tool | off | 12 | 864 | 0.351 (303/864) [0.32-0.38] | 0 (0%) | 0.8 | 2.6 | 260 | keep (grid complete; dominated) |
| Qwen2.5-1.5B | tool | off | 12 | 864 | 0.793 (685/864) [0.76-0.82] | 0 (0%) | 1.3 | 2.0 | 198 | keep (grid complete) |
| Qwen2.5-7B | tool | off | 8 | 576 | 0.990 (570/576) [0.98-1.00] | 0 (0%) | 5.3 | 6.7 | 664 | **keep - excellent** |
| Qwen2.5-14B | tool | off | 8 | 576 | 0.601 (346/576) [0.56-0.64] | 230 (40%) | 10.7 | 23.0 | 2281 | **price out** (firm) |
| Coder-0.5B | tool | off | 8 | 576 | 0.438 (252/576) [0.40-0.48] | 1 (0%) | 0.8 | 2.4 | 235 | keep (cheap; dominated) |
| Coder-1.5B | tool | off | 8 | 576 | 0.741 (427/576) [0.70-0.78] | 0 (0%) | 1.7 | 2.6 | 260 | keep (cheap; dominated) |
| Coder-3B | tool | off | 8 | 576 | 0.889 (512/576) [0.86-0.91] | 0 (0%) | 3.3 | 4.4 | 434 | keep |
| Qwen3.6-35B-A3B (MoE) | tool | off | 8 | 576 | 0.649 (374/576) [0.61-0.69] | 192 (33%) | 4.3 | 15.9 | 1579 | price out remaining cells (soft; priced-out rows are a ceiling artefact) |
| Qwen3.6-35B-A3B (MoE) | tool | on | 8 | 576 | 0.616 (355/576) [0.58-0.66] | 192 (33%) | 4.9 | 17.7 | 1751 | price out remaining cells (soft) |

Notes: the 14B f2 row includes the 33 graded rows of the cancelled order. "Firm" = dominated or near-zero by a margin no ceiling choice changes. "Soft" = dominated only on one comparison, or the delivered priced-out rows are an artefact. Where the arm is "grid complete" a keep costs nothing more; the verdict only matters for arms with cells left.

### Cheapest-first frontier per family (cost = J per solve, quality = pass over all rows)

- **f1:** Qwen2.5-1.5B (0.400 at 1,179) -> Coder-3B (0.754 at 1,330) -> MoE-off (1.000 at 2,047). Dominated: 7B (0.824 at 2,212: MoE-off is both cheaper and better), 0.5B, Coder-0.5B, Coder-1.5B, **14B (0.424 at 9,119)**, MoE-on.
- **f2:** Coder-0.5B (0.397 at 395) -> 1.5B (0.605 at 541) -> Coder-3B (0.827 at 814) -> MoE-off (0.997 at 1,178). Dominated: 0.5B (Coder-0.5B is better at half the cost), Coder-1.5B (1.5B plain is better and cheaper, intervals overlap), **7B (0.839 at 1,710) and 14B (0.914 at 3,224): the MoE-off is both cheaper and better than either**, MoE-on.
- **tool:** 1.5B (0.793 at 198) -> Coder-3B (0.889 at 434) -> 7B (0.990 at 664). Dominated: 0.5B, Coder-0.5B, Coder-1.5B (1.5B is better and cheaper), **14B, MoE-off, MoE-on (the 7B is both cheaper and better than all three)**.

## Evidence per verdict

### Price out, firm

**Qwen3.6-35B-A3B thinking ON, f1 and f2.** Every one of 16 delivered cells reproduces: f1 0/120 priced-in solves on both seed sets (s1, s4); f2 18/219 and 6/288. Rows priced out 723/747. The ceiling is not the cause: ceilings carried were 47 to 55 s (f1) and 63 to 98 s (f2) here, against an earlier 690 s order (spec, `16r4`) where the same model's median solve was 198 s with runaways of 2.7 h and 3.6 h. The 24 f2 rows that finished were all correct, took 35 to 95 s each and 210 to 575 tokens, but they are the quick tail: only 10 of those 24 are under today's 64 s ceiling. Measured rigby time: 3.57 h (f1) + 11.08 h (f2), of which **13.88 h was spent on rows that were then cut as priced out** (mean 51 s per f1 row, 78 s per f2 row). The same weights with thinking off, same tasks, same rigby: f1 240/240 at 20.7 s per solve; f2 574/576 at 11.9 s per solve. **The remaining 8 cells (f1 4 cells 1.79 h, f2 4 cells 5.54 h) would add nothing the 16 delivered do not already state.** A quirk to note for whoever implements it: the documented hold forms are `box` and `box/model` only (`rigby_fill` tests `"rigby/<gguf>" in hold`), so a hold on this model also stops its excellent thinking-off cells. Pricing out the thinking-on arm needs either a new hold form or `RIGBY_THINKING` edited in `menu_fill.py` - the owner's call, I changed nothing.

**Qwen2.5-14B, all families.**
- f1: 0.424 over all rows; 34% priced out as run, 37% when every row is re-priced at today's 47 s ceiling (123/330). Even excluding the priced-out rows, pass of answered is 0.645 (140/217), below the 7B's 0.824 at 22 s per solve and the MoE-off's 1.000. J per solve 9,119, the dearest f1 arm. Its misses are concentrated on `eval_rpn`, `valid_ipv4`, `caesar` (31, 25 and 22 of 33 rows priced out, so most failures here are cost, not wrong code).
- f2: 0.914 (491/537) [0.89-0.94], 3,224 J. Good but dominated by the MoE-off (0.997 at 1,178 J, 2.7x cheaper). A first-replicate read costs a median 46.4 s on f2 (cold prompt, doc: rigby reads at ~4.2 tok/s on the 14B).
- tool: 346/576 = 0.601, but **346/346 = 1.000 on the rows it was allowed to finish**; 40% priced out as run, 57% re-priced at today's 12 s ceiling. Its cost is the point: 10.7 s median per solved answer and 2,281 J per solve against the 7B's 5.3 s and 664 J at 0.990. It does not beat the 7B on quality even before pricing, and costs 3.4x more.
- The arm has used 10.24 h of rigby time (24% of the menu). Cells remaining: f2 3 (1.67 h) and tool 4 (1.11 h); f1 has none left.
- Not an artefact of the ceiling: pass of answered and answered-only cost give the same order in all three families (see tables above).

### Price out, soft

**Qwen2.5-7B on f1 and f2 (remaining 4 + 4 cells, 1.77 h).** f1 0.824 at 2,212 J and f2 0.839 at 1,710 J are strong results, and the 7B is the S10 lead-model candidate in [[34-S10-Rigby-Lead-Fellowship-Design]]. The case is only that the MoE-off dominates it on both families (cheaper and more reliable); it is not dominated on tool, where it is the champion. The remaining 7B cells would repeat what seed sets 1 and 4 already agree on (f1 75/90 and 98/120; f2 180/216 and 243/288). Same hold-granularity problem as above: a model-level hold would also stop the 7B's tool cells, which should be kept.

**Qwen3.6-35B-A3B on tool, thinking off and on (remaining 4 + 4 cells, 1.70 h).** The delivered rows look like a failure (33% priced out, pass 0.649 / 0.616) but are not: by seed set, **s1 cells (ceilings 22-25 s) had 0 of 288 priced out and pass 0.979 (off) / 0.927 (on); s4 cells (ceiling 12 s) had 192 of 288 priced out.** Same model, same tasks. In the t0.0 s4 order inspected row by row, replicates 4 and 5 of every task were cut at 14.9-15.8 s and replicate 6 solved in 4.4-4.6 s (23 of 24 correct), which fits the tool prompt costing about 14.5 s to read cold (the control row: 212 prompt tokens at 14.57 tok/s) and being cached after. Thinking-on adds nothing: 0.927 vs 0.979 and 1,040 vs 893 J at the s1 ceiling, so the on-arm is dominated by the off-arm. Even with the generous ceiling the MoE-off is dominated by the 7B (0.979 at 893 J vs 0.993 at 656 J on the same seed set). Verdict is price out the remaining cells, and treat the 192 + 192 delivered priced-out rows as censored data, not as zero-quality answers.

### Keep, and why

- **MoE thinking off, f1 and f2:** s1 and s4 agree (f1 120/120 and 120/120; f2 287/288 and 287/288), all four temperatures within a point of each other. Two task families, reproduced once: the nearest thing on this page to graduating. 2,047 J (f1) and 1,178 J (f2) per solve; median answer 17.9 s and 10.3 s. Cold first read is the main cost (23.3 s f1, 17.2 s f2 median) and sits under both ceilings.
- **Qwen2.5-7B on tool:** 570/576 = 0.990, s1 286/288 and s4 284/288; 664 J. Byte-identical answers across all four temperatures (see below).
- **Coder-3B (all families)** is on every frontier: 0.754 / 0.827 / 0.889 at 1,330 / 814 / 434 J; no priced-out rows.
- **Cheap arms** (0.5B, 1.5B, Coder-0.5B, Coder-1.5B on all families): several are dominated by another small arm, but they are minutes of rigby time each (0.08 to 0.26 h of cells left per arm, about 1.0 h across the six that still have cells), 1.5B on tool is the cheapest solve on the whole menu (198 J at 0.793), and Coder-0.5B on f2 is the cheapest f2 solve (395 J). Pricing them out would save about an hour in total. Nothing here is a spend problem.

## The ceiling (what it is today, and what it prices)

- **Rule as coded:** per order, `10 x max over the order's tasks of (cheapest measured solve of that task on rigby by any model)`; the cheapest solve is one `wall_s` of one row with `ok`, taken from every `hardbench-rigby-*` result.
- **Today's references** (from the 259 files): f1 worst-of-cheapest 4.7 s (`eval_rpn`, a 0.5B row that read 73 prompt tokens) -> **47.0 s**; f2 6.4 s (`dbg_max_subarray_sum`, a Coder-3B row with 1 prompt token, i.e. a fully cached prompt) -> **64.0 s**; tool 1.2 s (`scale_double`, a Coder-1.5B row with 1 prompt token) -> **12.0 s**. These match the 47.0 / 64.0 / 12.0 the filler logged on its last orders.
- **History in the log:** f1 184 s (2026-09-30 09:41) -> 55 (11:32) -> 47 (10-01 08:09); f2 222 -> 98 (09-30 22:53) -> 64 (10-01 20:28); tool none on the first two orders, then 9 (09-30 10:25), 16, 133, 63, 25 (10-01 05:35), 22, 15 (10-01 12:26), 12 (10-02 04:09). Per-order values are not monotonic (the ceiling is the worst over the tasks IN that order, so a small partial order can carry 63 and a full one 98); the reference only falls. The spec's "a ceiling only tightens" is true of the reference, not of what each order carried.
- **One-variable proof it moves the verdict, not the model:** Qwen2.5-14B tool, seed set 1: temperatures 0.0 and 0.3 carried a **16 s** ceiling and had **37 of 72** rows priced out; temperatures 0.6 and 0.9 carried **133 s** and had **0 of 72**. Same model, tasks and seed set; the answers across the four temperatures are byte-identical on all 68 + 105 comparable rows. The MoE tool s1-vs-s4 split above is the second instance.
- **What it effectively prices:** a cold prompt read against a warm-cache minimum of a tiny model. Re-pricing every delivered row at today's ceilings, the share that would be priced out is 57% for 14B tool, 50% for MoE tool (both thinking settings), 37% for 14B f1, 5% for 14B f2, 0% for the 7B on every family and at most 4% (Coder-0.5B f1) for every smaller arm; the first-replicate median wall (cold read) of the 7B on tool is 9.6 s against a 12 s ceiling, so the champion sits 2.4 s under the line.
- **Not my call:** the 10x is the owner's (`PRICE_X`), and the reference choice (min of any model, warm rows included) is the rule as written. I flag that at the current tool value, priced-out will keep landing on every tool arm bigger than the 7B on a cold prompt whatever its quality. See "Decisions for the owner".

## Redundant cells (information per hour)

- **Greedy cells repeat.** At temperature 0.0 the three replicates in a seed set give the same answer for the large majority of tasks (same-seed-set replicates identical: tool 72/72 task groups for the 0.5B and 1.5B, 48/48 for the 7B, Coder arms and 24/24 for the MoE; f2 42 to 63 of 48 to 72 groups for the Qwen2.5 arms and 48/48 for the MoE), and the s1 / s4 / s7 sets repeat each other (one distinct answer across all t0.0 rows for 24 of 24 tool tasks on eight arms, 19 to 24 of 24 on f2, 10 of 10 on f1 for the MoE-off). The cancelled order below is such a cell: 22 of its 29 finished answers equal an answer already delivered at t0.0.
- **Tool temperature is inert for the strong arms.** Answers at t0.0 vs t0.3 and t0.6 vs t0.9 are byte-identical on 100% of comparable rows for the 7B (144/144, 144/144), 14B (68/68, 105/105) and MoE-on (96/96, 96/96), 92% and 99% for the MoE-off (88/96 t0.0 vs t0.3, 95/96 t0.6 vs t0.9). For the 0.5B it does matter (tool pass 0.417 at t0.0 down to 0.296 at t0.9).
- **How much it is worth:** the t0.0 cells still unfilled on kept arms are 12 cells / ~1.1 h; the rest of the remaining time is on arms already recommended for price-out. This is not a spend problem next to the 14.65 h on MoE thinking-on, and the owner's scope rule is a full menu, so I recommend leaving it. Recorded so the redundancy is a known number.

## The one known hole: the cancelled 14B order

`hardbench-rigby-m-qwen2.5-14b-instruct-q4-k-m-f2-t00-off-s7.20261002-0753` (file `...20261002-082417.results.json`): stopped by control slip `...ctl.json` ("pause-for-redeploy: Tolkien USB keeper class-A fix (c1919ac); the owner approved accepting the menu hole 2026-10-02"), `stopped: true`, `VOID: true`, `complete: false`, `n_measured 34 / n_expected 73` (coverage "33/73 answers"), control passed, wall 1,157 s. Of the 33 graded rows: **28 solved, 4 priced out (ceiling 64 s), 1 stopped**. 40 pairs (e.g. `dbg_transpose`, `dbg_word_freq`, `mod_clamp`) never measured. It is a t0.0 seed-7 cell on the 14B, so the lost information is the cheapest kind: **22 of the 29 finished answers equal answers delivered at t0.0 in s1/s4**, and the arm is recommended for price-out anyway. The filler re-orders only missing pairs of a partial cell (spec), and the file counts as delivered rather than outstanding, so it will re-queue those 40 pairs on its own unless the 14B is held. Its 33 graded rows are included in the 14B f2 row above.

## Decisions for the owner

1. **Price out MoE thinking-on on f1 and f2 (7.3 h of the 18.0 h unfilled)?** Firm. Needs a hold form that can name one arm (the documented ones are box and box/model), or removing the model from `RIGBY_THINKING` in `menu_fill.py`.
2. **Price out Qwen2.5-14B (2.8 h left on f2 and tool; f1 is done)?** Firm; a `rigby/Qwen2.5-14B-Instruct-Q4_K_M.gguf` hold does exactly that. Check first that the S10 design does not need more 14B cells than the 0.914 it already has.
3. **Soft price-outs: 7B f1/f2 (1.77 h) and MoE tool on/off (1.70 h).** These need arm-level holds to be done cleanly (a model hold would also stop the 7B's tool cells, which you want). Accept, or leave running; the cost is 3.5 h.
4. **The ceiling reference.** The rule as written prices a cold prompt read against a warm tiny-model minimum, which now lands the tool ceiling at 12 s, below the MoE's 14.5 s cold read. Options for your call (none applied): leave it and treat priced-out rows as censored, not zero; price against the median cold solve instead of the single cheapest row; or exclude cache-warm rows (`prompt_tokens` 1) from the reference. The 10x stays yours.
5. **Redundant greedy and tool-temperature cells (~1.1 h):** leave them under the full-menu rule, or skip? My recommendation: leave.
6. **Doc and review cadence.** The order spec's sentence "a ceiling only tightens" and its ceiling values (f1 184 s, f2 222 s "on 2026-09-30") are now 47 / 64 / 12 and per-order values are not monotonic; I did not edit the spec. If you want this review repeated, rerun it once the price-out decisions above are in force and the filler has delivered more cells.
