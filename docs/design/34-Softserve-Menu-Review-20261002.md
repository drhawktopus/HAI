# Project 34 Softserve menu review 2026-10-02 - arm evidence (dominated arms are KEPT; only can't-fit or priced-out arms stop)

**Rule in force (the owner 2026-10-02, [[34-Hard-Bench-Order-Spec]] § "When an arm stops being filled"):** an arm stops on
this box only if it CANNOT FIT (answers hit the tested-max context) or is PRICED OUT (≥ 10× this box's cheapest solve,
on ≥ 2 families, reproduced on this box). Verdicts below that say "PRICE OUT - dominated" mean dominated — those arms
stay on the menu as fallbacks for when the best box is busy. Evidence is unchanged.

**Resolved 2026-10-02 (build, the owner: "Hit it baby"):** decisions 1 (per-arm hold + cancel slips: 75 queued orders + the stopped
9B f1 order, applied when the runner next starts), 2 (GPU price line), 4 (qwen3:8b thinking-on: held except one repeat cell
per family) and 9's menu rebuild are done — [[34-Hard-Bench-Order-Spec]] § "Holds" and "Price ceilings". Decisions 3, 5–8 are
still the owner's; ollama on Softserve was brought back up separately that morning.

**Status:** every verdict below is PRELIMINARY (the order spec's rule: one task family per division, one seed block per
cell, one run per rung; a reading graduates only when it holds across a second task family and reproduces once -
[[34-Hard-Bench-Order-Spec]], [[34-Hard-Bench-Closeout]]). Read-only review written for the owner ahead of resuming the
Softserve and BigHonker factories (paused 2026-10-01 08:22 by the owner; nothing was touched, placed, cancelled or resumed
to write this). **Project:** #34 area A (Phase 3, the 8 GB tier) and the GPU half of the menu fill ([[34-Shape-Catalogue]],
[[34-CHARTER]]). No earlier Softserve menu review exists in the library (searched `Z:\Documentation` for "menu review",
"price out", "menu filler", "menu_fill"), so this inventory covers every Softserve menu result delivered, 2026-09-28 20:24
to 2026-09-30 22:02.

## What was parsed (assert non-zero)

- **54 `out.json` files + the partial rows of 1 stopped order = 55 Softserve order records, 2,920 rows (2,777 graded
  non-control rows + 143 control rows; 143 of 143 controls passed).** Found by walking
  `E:\SSDShared\tabfoundry-jobs\campaigns` for every result folder ending `_softserve` or `_softserve.<stamp>` (a resumed
  order's second folder). Per-day delivery: 09-28 x1, 09-29 x28, 09-30 x25 (out.json mtimes). The Softserve foundry's own queue
  (`E:\foundry\jobs\queue`) was read for what is still pending.
- **Grading.** Coding rows (family 1 = 10 tasks, family 2 = 24 tasks, x 3 rounds) were graded ON THE BOX by the payload
  (`map_swarm_code_hard.py` / `_f2.py`, fence-tolerant extractor). Tool and vision rows are ungraded in `out.json`; I graded
  them in memory with the reducer's own scorers (`hard_bench_fleet_tv.reduce`: `score_tool`, `_vlm_score`), nothing written.
  Cross-check: my counts equal `fleet-tv\fleet_tv_rows.csv` for the six rungs it holds (tool 64/72 and 67/72, vlm 1x 72/72 and 37/72,
  vlm 2x 72/72 and 71/72). `tool-vl8b-ss` is NOT in that CSV (the reducer last ran 09-29 17:58, the order finished 18:00):
  70/72 is my in-memory grade.
- **The menu table is behind.** `Datasets\Project34-Menu\menu-cells.csv` / `coverage.md` were built 2026-09-29 18:16 and hold
  none of Softserve's temperature-grid cells (their Softserve rows are only the older temperature-0.6 / default-sampler orders). A
  `build_menu.py` rebuild is due before anyone reads the coverage page as current.
- **Limit rule.** A row counts "at a limit" when `done_reason` is not `stop` or prompt + generated tokens reach `num_ctx - 8`
  (the reducer's own test, doctrine 13a). `out.json` row-level `error` was empty on all 2,777 rows.
- **Energy** is GPU board power from `nvidia-smi` at 4 Hz inside each order (the same meter on BigHonker, so Softserve vs
  BigHonker joules are like-for-like; Mothership's telemetry-csv meter is coarser and wall-side figures are not comparable,
  per the spec). Four Softserve orders are only partly metered (listed in Appendix A as "partial meter"; they are left
  out of every J figure here). Per-row energy on sub-second answers is 2 - 4 meter samples: read J as a pooled figure, never per row.
- Per-order evidence: Appendix A (55 rows). Per-task cheapest solves: Appendix B. Scratch scripts: the Claude Home session
  scratchpad (`inv2.py`, `ana3.py` - `ana6.py`, `appx.py`).

## The price rule as the docs define it, and what exists in code

"Priced out" is a measured cost ratio: an answer is priced out once it has cost **10x the cheapest measured solve of that
task family on the SAME box** (the owner 2026-09-29, "real costs not arbitrary time"); the ceiling is recomputed from every
delivered result per task and an order carries 10x the worst per-task cheapest; a cheapest solve only falls so a ceiling only
tightens; the 10x is Claude's owner call (2026-09-30 04:49) and the owner's to change (`PRICE_X` in `menu_fill.py`). A priced-out
answer is a stated measurement, never a fail. "Is another box cheaper" is the routing question, not the price
([[34-Hard-Bench-Order-Spec]] section "Price ceilings").

**Gap found while reading the code (class 17, a rule that exists but is unwired for this box):** the ceiling is implemented
for rigby only. `menu_fill.py` computes it in `rigby_ceiling()` and writes `price_ceiling_s` into Tolkien `llm` orders; the GPU
path (`submit_cell` -> `hard_bench_fleet_orders.py` / `hard_bench_fleet_tv.py` -> `infer_ollama.py`) carries no ceiling, and
`infer_ollama.py` has no ceiling argument (grep: no "ceiling"/"priced"). So no Softserve answer was ever cut or labelled
`priced_out`: thinking-on rows ran until they ended or hit the context (up to 98 s each). Everything below applies the rule
**after the fact** to delivered rows. Measured on Softserve itself (all models, thinking on and off, solved rows only), the
order-level ceilings are:

| family | worst per-task cheapest solve | 10x = ceiling |
|---|---|---|
| coding-f1 | 1.7 s (`group_anagrams`) | **17.0 s** |
| coding-f2 | 2.0 s (`dbg_chunk_list`) | **20.0 s** |
| tool | 0.6 s (`update_paid`, `update_resolved`) | **6.0 s** |
| vlm 1x | 3.1 s (`h_count_tri9`) | **31.0 s** |
| vlm 2x | 5.4 s (`h_count_red7`) | **54.0 s** |

Caveats: wall times have 0.1 s resolution and the cheapest solves are 0.3 - 2 s, so these ceilings are tight and a
`wall_s` of 1.2 vs 1.7 is within resolution; the tool ceiling rests on sub-second solves. Tasks no model solved on Softserve have
no price (none are in this table).

## Summary - one row per arm (model x family x thinking), Softserve

"pass" = solved / answered non-control rows pooled over that arm's cells; [range] = lowest..highest single cell. "per solved"
= total wall (or total board joules) of ALL answers in the arm divided by the number solved, so failures are charged to
the solves. "over ceiling" = share of answered rows whose wall exceeded the family ceiling above. "at cap" = rows that hit
the context. n is cells (one cell = 3 rounds of the family's tasks at one temperature / seed set).

| arm (model, family, thinking) | cells | pass | at cap | wall per solved | J per solved | over ceiling | verdict |
|---|---|---|---|---|---|---|---|
| **qwen3-vl:8b, vlm 1x, off** | 1 | **1.000** (72/72) | 0 | 1.8 s | 291 | 0 of 72 | **KEEP - excellent** |
| **qwen3-vl:8b, vlm 2x, on** | 1 | **1.000** (72/72) | 0 | 2.0 s | 312 | 0 of 72 | **KEEP - excellent** (see D3) |
| **qwen3-vl:8b, tool, off** | 1 | **0.972** (70/72) | 0 | 0.5 s | 68 | 0 of 72 | **KEEP - excellent** |
| **qwen3:8b, tool, off** | 1 | 0.931 (67/72) | 0 | 0.5 s | 67 | 0 of 72 | **KEEP** |
| **qwen3:8b, coding-f1, off** | 1 | 0.900 (27/30) | 0 | 1.4 s | 207 | 0 of 30 | **KEEP** (BigHonker repeats it, 6 cells) |
| **qwen3:8b, coding-f2, off** | 1 | 0.903 (65/72) | 0 | 1.0 s | 135 | 0 of 72 | **KEEP** (BigHonker repeats it, 4 cells) |
| qwen3:8b, coding-f1, on | 1 | 0.967 (29/30) | 1 | 53.4 s | 10,432 | 25 of 30 | **PRICE OUT** (exceeds ceiling; gain is 2 rows) |
| qwen3:8b, coding-f2, on | 1 | 0.958 (69/72) | 2 | 22.1 s | 4,421 | 20 of 72 | **PRICE OUT** as a default; keep one cell as an escalation reference (D4) |
| qwen3.5:9b, tool, off | 1 | 0.889 (64/72) | 0 | 0.8 s | 106 | 0 of 72 | **PRICE OUT** - dominated (soft) |
| qwen3.5:9b, coding-f1, off | 13 | 0.823 (321/390) [0.700 - 0.933] | 0 | 4.1 s | 693 | 6 of 390 | **PRICE OUT** - dominated by qwen3:8b off |
| qwen3.5:9b, coding-f2, off | 9 | 0.858 (556/648) [0.750 - 0.958] | 7 (1.1%) | 3.3 s | 555 | 16 of 648 | **PRICE OUT** - dominated by qwen3:8b off |
| qwen3.5:9b, vlm 1x, off | 1 | 0.514 (37/72) | 0 | 1.1 s | 95 | 0 of 72 | **PRICE OUT** - dominated; likely void by method (D5) |
| qwen3.5:9b, vlm 2x, on | 1 | 0.986 (71/72) | 5 (6.9%) | 13.3 s | 2,394 | 8 of 72 | **PRICE OUT** - dominated by qwen3-vl:8b; near-void (cap hits) |
| qwen3.5:9b, coding-f1, on | 12 (11 delivered + 1 stopped) | 0.499 (176/353) [0.300 - 0.700] | **175 (50%)** | 142.2 s | 26,913 | 337 of 353 | **VOID + PRICE OUT** |
| qwen3.5:9b, coding-f2, on | 8 | 0.663 (382/576) [0.569 - 0.736] | **185 (32%)** | 72.2 s | 14,069 | 336 of 576 | **VOID + PRICE OUT** |
| qwen3-vl:8b, coding-f1, off | 1 | 0.233 (7/30) | **23 (77%)** | 259.3 s | 50,535 | 29 of 30 | **VOID - skip on Softserve** |
| qwen3-vl:8b, coding-f2, off | 1 | 0.653 (47/72) | **25 (35%)** | 60.4 s | 11,828 | 50 of 72 | **VOID - skip on Softserve** |
| medgemma:4b (all families), joycaption (all) | 0 | unmeasured | - | unmeasured | unmeasured | - | **unmeasured** (D8) |

Sources: Appendix A rows; group totals recomputed from `out.json` / `partial.jsonl`. J per solved for the 9B thinking-on arms
uses only the fully metered cells (10 of 12 and 6 of 8). Cell ranges and the stopped order are in Appendix A.

## Evidence per verdict

### Excellent arms - keep
- **qwen3-vl:8b on vision** solved all 72 of 72 at 1x (thinking off) and 72 of 72 at 2x (thinking on), at 1.8 - 2.0 s and
  291 - 312 J per solve, nothing at the cap, nothing over the 31 s / 54 s ceilings. BigHonker agrees (1.00, 259 - 264 J,
  [[34-Hard-Bench-Order-Spec]] and `fleet_tv.md`). Context was 5,120 (1x) and 4,096 (2x) - the model's tested maximum on an
  8 GB card - and it ran clean there, so this is a fit that works.
- **qwen3-vl:8b on tool** 70/72 (0.972) at 0.5 s / 68 J; the two misses are on one task. It matches BigHonker (0.972) and
  beats qwen3:8b (0.931) and the 9B (0.889) on pass at the same cost.
- **qwen3:8b with thinking off is the 8 GB coding and tool champion on this box:** f1 27/30 at 1.4 s / 207 J, f2 65/72 at
  1.0 s / 135 J, tool 67/72 at 0.5 s / 67 J. It is one cell per family on Softserve, but BigHonker holds the same arm at 6 cells
  (f1: 0.917, 1.5 s, 175 J) and 4 cells (f2: 0.927, 1.0 s, 108 J), so the reading is not one box's noise. Softserve's weak
  tasks for it: `dbg_chunk_list` and `dbg_is_palindrome` never solved (f2), `group_anagrams` never solved (f1).

### Dominated arms - price out
- **qwen3.5:9b thinking off is dominated by qwen3:8b thinking off on both coding families and on tool.** f1: 0.823 at 4.1 s /
  693 J (13 cells) against 0.900 at 1.4 s / 207 J: lower pass, 2.9x the time, 3.3x the energy. f2: 0.858 at 3.3 s / 555 J (9 cells)
  against 0.903 at 1.0 s / 135 J: 3.3x the time, 4.1x the energy. tool: 64/72 against 67/72 at 0.8 s / 106 J against 0.5 s / 67 J
  (the pass gap is 3 rows - within noise; the cost gap is not). The qwen3:8b side is a single Softserve cell, so the verdict leans on BigHonker's
  repeats of the same arms; a second Softserve seed block would graduate it.
- **qwen3.5:9b on vision** is dominated by qwen3-vl:8b: at 1x 0.514 against 1.000; at 2x thinking-on 0.986 at 13.3 s / 2,394 J against
  1.000 at 2.0 s / 312 J (6.6x time, 7.7x energy), with 5 of 72 rows (6.9%) at the 7,168 cap (all five still correct). The 1x reading
  (37 of 72, ten tasks never solved) has the shape of the method handicap the owner ruled void for the 27Bs (images of ~175
  prompt tokens, thinking off) - D5.
- **qwen3:8b thinking on** buys little at a large multiple. f1: 29/30 against 27/30 for 38x the time (53.4 s vs 1.4 s per solve)
  and 50x the energy; 25 of 30 rows exceed the 17 s ceiling. f2: 69/72 against 65/72 for 22x the time and 33x the energy
  (22.1 s / 4,421 J vs 1.0 s / 135 J); 20 of 72 rows exceed 20 s, 56 of 72 exceed their own task's 10x. One seed block each; BigHonker
  reads the same pass (0.967 / 0.958) at 35.2 s and 28.5 s. Not void (1 and 2 rows at a limit).

### Void cells - the cell measured the cap, not the model (doctrine 13a)
- **qwen3.5:9b thinking on, both coding families.** 175 of 353 f1 rows (50%) and 185 of 576 f2 rows (32%) hit the 7,168 context.
  Only 5 of the 175 and 1 of the 185 at-cap rows were solved, while 171 of the 178 and 381 of the 391 rows that finished
  were solved - so the 0.499 / 0.663 mostly reads how many answers were cut, and the model's uncapped accuracy is unmeasured. The
  cap cannot be raised on this card (7,168 is the model's tested maximum on both 8 GB boxes, `menu_fill.TESTED_CTX`), so a
  non-void re-run does not exist on Softserve. Independently priced out: the median SOLVED row took 45.5 s (f1) and 15.8 s (f2) and the slowest row 98 s,
  against ceilings of 17 / 20 s (337 of 353 and 336 of 576 rows over). Pass by temperature 0.0 / 0.3 / 0.6 / 0.9: f1 0.52 / 0.62 / 0.50 / 0.34, f2 0.74 / 0.69 / 0.63 / 0.60; at-cap rows f1 37 / 35 / 47 / 56 (of 90, 90, 90, 83) and f2 38 / 42 / 50 / 55 (of 144 each).
- **qwen3-vl:8b on coding, thinking off.** 23 of 30 (f1) and 25 of 72 (f2) rows hit the 5,120 context although
  thinking is "off" (this model ignores `think:false` - all 33 rows of one of these orders carry thinking text - as the doctrine index records); every row that finished was correct (7/7 and
  47/47) and every at-cap row failed - the cell measures the cap. 5,120 is its tested ceiling on 8 GB, so no un-void run exists
  on Softserve. BigHonker's matching cells are void the same way (67% and 28% at cap). **48 more of these cells are queued on
  Softserve (12 each: f1 off, f1 on, f2 off, f2 on) and would be void by construction** - see D1.

### Knobs inside the surviving arm (qwen3.5:9b thinking off, Softserve, pooled seed sets)
| temperature | f1 pass (3 cells, 90 rows) | f2 pass (2 cells, 144 rows) |
|---|---|---|
| 0.0 | 0.800 | 0.958 |
| 0.3 | 0.900 | 0.889 |
| 0.6 | 0.867 | 0.847 |
| 0.9 | 0.722 | 0.785 (5 rows at cap vs 0 - 1 elsewhere; 7.2 s per solve vs 1.3 - 3.2) |
The default-sampler cells read 0.833 (f1) and 0.764 (f2). Temperature 0.9 is the worst single-attempt knob on both families
and the one with the most at-cap rows (5 on f2); it is priced out as a single-attempt setting (it is the diversity knob for
best-of-N, which nobody has asked of the 9B here). The 9B is already dominated, so this matters mostly for the spec's grid, not for routing.

**Greedy cells are exact replicas across seed sets.** At temperature 0.0 with thinking off, the verdict vector was identical across
seed sets 1/4/7 (f1) and 1/4 (f2) on Softserve, and across 1/4/7 and 1/4 on BigHonker, and the three rounds inside one greedy cell never
disagreed (0 of 10 tasks f1, 0 of 24 f2). Seed sets 4 and 7 at temperature 0.0 therefore add nothing (the spec's own small-shapes finding:
"N > 1 adds nothing at 0.0"). Thinking-on greedy is NOT reproducible (f1 differs on both boxes, f2 differs on BigHonker), so
those replicas do carry information. **Queued on Softserve: 9 thinking-off t0.0 cells at seed sets 4 or 7** - one on the 9B (f2, proven redundant by the 9B's
own cells), four on qwen3:8b (inferred from the 9B; not yet measured for qwen3:8b), four on qwen3-vl:8b (void anyway).

### Orders stopped or checkpointed by pauses
- **2026-10-01 08:22 (the owner: "pause softserve and BH until I say resume"): one Softserve order was running** -
  `hardbench-fleet-grid-so-9b-f1-t09-on-s7-9b.20260929-182047` (qwen3.5:9b, f1, t0.9, thinking on, seed set 7). Recovered from its two partial
  folders: **25 of 33 rows answered (23 graded + 2 controls), 9 solved, 14 of the 23 at the 7,168 cap**. The foundry's `state.json` says phase
  PREFLIGHT, delivered 26. A requeue slip (`pause-ss.ctl.json`, action `requeue`, "answered rows kept") sits in `E:\foundry\jobs\queue`;
  the journal (2026-09-30-claude section "2026-10-01 morning") records the runner task disabled by `pause_indefinite.py` (I did not re-read the task). It is a thinking-on 9B f1 cell - void on arrival (above); resuming it is wasted time
  unless D1 keeps it.
- Earlier pauses already healed: f2 t0.3 thinking-on seed set 1 was stopped 09-29 23:02 at 36 of 75 rows and finished 09-30 08:28 (72 of 72, but only 37 rows
  carry energy, hence "partial meter"); the 09-30 22:12 stop caught the order above with 7 rows.
- **Not Softserve state, but the pause's second half:** BigHonker's stopped order (9 of 33 rows) and its 94 outstanding orders are untouched here.
- **Ollama is not running on Softserve right now.** The filler's first "cannot list models (URLError)" for Softserve is the 2026-10-01 21:53 run
  (`menu_fill_topup.log`), every run since; at review time `tasklist` shows no ollama process, `127.0.0.1:11434` returns nothing, and nothing listens on 11434 here.
  Bring it up before resuming (not touched; cause not investigated).

### What the queue would run on resume (105 orders in `E:\foundry\jobs\queue`, plus the requeue slip)
| queued arm | orders | review verdict |
|---|---|---|
| qwen3-vl:8b coding (f1 off / f1 on / f2 off / f2 on) | 48 | void by construction - skip (D1) |
| qwen3:8b coding f1 off 12, f1 on 11, f2 off 12, f2 on 12 | 47 | off: keep (minus 4 greedy replicas); on: D4 |
| qwen3.5:9b f2 (4 off, 4 on) | 8 | off: dominated; on: void - skip |
| medgemma:4b f1 and tool | 2 | unmeasured, fine |
Tool and vision cells for the grid (the strongest Softserve arms) are NOT queued yet: the filler orders breadth-first and had 106 outstanding
against a target of 25, so it stopped adding; the grid line before the pause was "432 cells, 279 missing, 106 outstanding". Note the ledger gap
the spec already names: the stopped order counts as outstanding forever.

### Cross-factory notes (routing, not price - the spec keeps them apart)
Same arms on BigHonker's 3070 versus Softserve's 3070 Ti, same meter, same context: the pairs below agree within 25% on time and joules except
9B f1 off (Softserve 28% cheaper in J and 47% faster, with 5 BigHonker rows at cap), and no arm is Softserve-specific. 9B f1 off 693 J / 4.1 s (Softserve) vs 959 J / 7.7 s (BigHonker); 9B f2 off 555 / 3.3 vs 479 / 4.0;
qwen3:8b f1 off 207 / 1.4 vs 175 / 1.5; f2 off 135 / 1.0 vs 108 / 1.0; tool qwen3-vl:8b 68 / 0.5 vs 58 / 0.6. Therefore every verdict above
is also a BigHonker verdict (its own review is a separate pass). Against Mothership (held; Flash-Next), the 8B thinking-on arms
are dominated on the doc's own numbers (qwen3.8:27b family 2: 0.972 at 1.2 s / ~404 J, qwen3-coder:30b 1.000 at 0.5 s / 37 J, per the order spec;
Mothership's meter is coarse and wall vs board is not like-for-like), which is the routing layer's call.

## Decisions for the owner before resuming

1. **Approve the skip list for the queued orders.** The filler's hold key is `<box>/<model>` (whole model), not per family, so skipping
   qwen3-vl:8b's 48 void coding cells by hold would also hold its excellent tool and vision cells. Options: (a) cancel those 48 queued
   specs by control slip and the 8 qwen3.5:9b thinking-on f2 cells, and add a per-family hold to `menu_fill.py` so they are not re-ordered; (b) hold the whole
   model and accept losing its tool/vision grid. My recommendation is (a). Cancelling is the factory desk's work, not mine.
2. **Enforce the ceiling on GPU orders, or keep it rigby-only.** Today thinking-on 9B rows ran 60 - 98 s each to the context cap while the
   documented ceiling is 17 - 20 s. Enforcement needs `infer_ollama.py` to accept a per-order `price_ceiling_s` and stop an item at the ceiling (the Tolkien
   `llm` path already does), and the filler to compute it per task from delivered Softserve rows as `rigby_ceiling()` does. Without it, every
   thinking-on cell on an 8 GB box costs its full cap and is void anyway. Also your call whether 10x is right on sub-second
   solves (it gives 6 s for tool), where 0.1 s resolution makes the cheapest solve noisy.
3. **qwen3-vl:8b's vision cells: 1x and 2x see the same image.** Per the spec, ollama resizes this model's input to a fixed budget (~1,110 prompt tokens at 1x and
   2x), so its 2x row "is a repeat of the 1x question with thinking on". The grid orders both scales x both thinking settings x 4 temperatures x 3 seed sets.
   Keep both (they differ in thinking), or collapse 2x into 1x for this model?
4. **qwen3:8b thinking on: price out or keep as the escalation reference?** Numbers above: +2 rows (f1) / +4 rows (f2) for 22 - 38x the time. I would price it out of the default
   fill and keep the one measured cell per family. 23 such cells are queued (f1 11, f2 12).
5. **Extend the "void by method" ruling to qwen3.5:9b's 1x vision rows?** Your ruling named the two 27B 1x rows; the 9B's 0.514 (Softserve) and 0.500 (BigHonker)
   have the same shape. If yes, those two cells are void rather than just dominated.
6. **qwen3.5:9b thinking on cannot be measured without a cap on 8 GB (7,168 tested maximum).** Price the knob out on the 8 GB boxes, or schedule a fit test (a
   larger context with KV quantisation) first? Mothership's model list (`hard-bench\box_models.json`) has no qwen3.5:9b.
7. **Drop the seed-set 4 and 7 replicas at temperature 0.0, thinking off** (9 queued on Softserve; BigHonker's queue was not counted). Safe for the 9B (proven); for qwen3:8b
   it is by analogy until one greedy qwen3:8b seed pair is compared.
8. **Unmeasured models.** medgemma:4b (2 cells queued) and `aha2025/llama-joycaption-beta-one-hf-llava:Q4_K_M` (a captioner, in the grid, none queued, 60 cells) have no result. Your scope rule keeps both
   in the grid ("nothing is left out except a model that cannot generate text"); say if joycaption should come out.
9. **Bring ollama up on Softserve before resume** and rebuild the menu table (`build_menu.py`) so `coverage.md` shows the 55 orders reviewed here. Also decide whether Softserve's Plex duty
   (its first grid order was HELD at the desk, "Plex first: gpu busy (mem 87% util 92%)") should cap how much of the fill runs while Plex is in use.

## Appendix A - every Softserve order parsed (55)

(`rows at limit` per the rule above; `answered / expected` excludes the 3 control rows of coding orders; "partial meter" = fewer rows carry joules than rows,
excluded from J figures; `default` temperature = the sampler default, no `--temperature` given.)

| # | order (stamp) | model | family | think | temp | seed set | num_ctx | answered / expected | solved | pass | rows at limit | wall per solved s | J per solved | state |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | vl8b-ss-vl8b.20260929-175915 | qwen3-vl:8b | coding-f1 | off | default | 1 | 5120 | 30 / 30 | 7 | 0.233 | 23 | 259.3 | 50535 | delivered |
| 2 | grid-so-9b-f1-t00-off-s1-9b.20260929-182007 | qwen3.5:9b | coding-f1 | off | 0.0 | 1 | 7168 | 30 / 30 | 24 | 0.800 | 0 | 3.7 | 621 | delivered |
| 3 | grid-so-9b-f1-t00-off-s4-9b.20260929-182023 | qwen3.5:9b | coding-f1 | off | 0.0 | 4 | 7168 | 30 / 30 | 24 | 0.800 | 0 | 3.8 | 625 | delivered |
| 4 | grid-so-9b-f1-t00-off-s7-9b.20260929-182040 | qwen3.5:9b | coding-f1 | off | 0.0 | 7 | 7168 | 30 / 30 | 24 | 0.800 | 0 | 3.8 | 628 | delivered |
| 5 | grid-so-9b-f1-t03-off-s1-9b.20260929-182008 | qwen3.5:9b | coding-f1 | off | 0.3 | 1 | 7168 | 30 / 30 | 27 | 0.900 | 0 | 4.8 | 845 | delivered |
| 6 | grid-so-9b-f1-t03-off-s4-9b.20260929-182024 | qwen3.5:9b | coding-f1 | off | 0.3 | 4 | 7168 | 30 / 30 | 26 | 0.867 | 0 | 2.9 | 474 | delivered |
| 7 | grid-so-9b-f1-t03-off-s7-9b.20260929-182041 | qwen3.5:9b | coding-f1 | off | 0.3 | 7 | 7168 | 30 / 30 | 28 | 0.933 | 0 | 4.0 | 678 | delivered |
| 8 | grid-so-9b-f1-t06-off-s1-9b.20260929-182009 | qwen3.5:9b | coding-f1 | off | 0.6 | 1 | 7168 | 30 / 30 | 27 | 0.900 | 0 | 4.2 | 721 | delivered |
| 9 | grid-so-9b-f1-t06-off-s4-9b.20260929-182026 | qwen3.5:9b | coding-f1 | off | 0.6 | 4 | 7168 | 30 / 30 | 25 | 0.833 | 0 | 4.2 | 712 | delivered |
| 10 | grid-so-9b-f1-t06-off-s7-9b.20260929-182042 | qwen3.5:9b | coding-f1 | off | 0.6 | 7 | 7168 | 30 / 30 | 26 | 0.867 | 0 | 5.0 | 858 | delivered |
| 11 | grid-so-9b-f1-t09-off-s1-9b.20260929-182010 | qwen3.5:9b | coding-f1 | off | 0.9 | 1 | 7168 | 30 / 30 | 23 | 0.767 | 0 | 3.4 | 565 | delivered |
| 12 | grid-so-9b-f1-t09-off-s4-9b.20260929-182027 | qwen3.5:9b | coding-f1 | off | 0.9 | 4 | 7168 | 30 / 30 | 21 | 0.700 | 0 | 4.0 | 640 | delivered |
| 13 | grid-so-9b-f1-t09-off-s7-9b.20260929-182043 | qwen3.5:9b | coding-f1 | off | 0.9 | 7 | 7168 | 30 / 30 | 21 | 0.700 | 0 | 3.9 | 621 | delivered |
| 14 | ss-9b-f1-9b.20260929-005341 | qwen3.5:9b | coding-f1 | off | default | 1 | 7168 | 30 / 30 | 25 | 0.833 | 0 | 5.9 | 965 | delivered |
| 15 | grid-so-9b-f1-t00-on-s1-9b.20260929-182011 | qwen3.5:9b | coding-f1 | on | 0.0 | 1 | 7168 | 30 / 30 | 15 | 0.500 | 13 | 131.6 | partial meter | delivered |
| 16 | grid-so-9b-f1-t00-on-s4-9b.20260929-182028 | qwen3.5:9b | coding-f1 | on | 0.0 | 4 | 7168 | 30 / 30 | 16 | 0.533 | 12 | 120.4 | 22951 | delivered |
| 17 | grid-so-9b-f1-t00-on-s7-9b.20260929-182044 | qwen3.5:9b | coding-f1 | on | 0.0 | 7 | 7168 | 30 / 30 | 16 | 0.533 | 12 | 121.1 | 22993 | delivered |
| 18 | grid-so-9b-f1-t03-on-s1-9b.20260929-182012 | qwen3.5:9b | coding-f1 | on | 0.3 | 1 | 7168 | 30 / 30 | 17 | 0.567 | 13 | 110.8 | 21558 | delivered |
| 19 | grid-so-9b-f1-t03-on-s4-9b.20260929-182029 | qwen3.5:9b | coding-f1 | on | 0.3 | 4 | 7168 | 30 / 30 | 18 | 0.600 | 12 | 106.6 | 20269 | delivered |
| 20 | grid-so-9b-f1-t03-on-s7-9b.20260929-182045 | qwen3.5:9b | coding-f1 | on | 0.3 | 7 | 7168 | 30 / 30 | 21 | 0.700 | 10 | 88.8 | 16911 | delivered |
| 21 | think-9b-ss-9b.20260929-153151 | qwen3.5:9b | coding-f1 | on | 0.6 | 1 | 7168 | 30 / 30 | 13 | 0.433 | 18 | 182.0 | 35519 | delivered |
| 22 | grid-so-9b-f1-t06-on-s4-9b.20260929-182030 | qwen3.5:9b | coding-f1 | on | 0.6 | 4 | 7168 | 30 / 30 | 17 | 0.567 | 14 | 133.9 | 25288 | delivered |
| 23 | grid-so-9b-f1-t06-on-s7-9b.20260929-182046 | qwen3.5:9b | coding-f1 | on | 0.6 | 7 | 7168 | 30 / 30 | 15 | 0.500 | 15 | 142.9 | 27406 | delivered |
| 24 | grid-so-9b-f1-t09-on-s1-9b.20260929-182013 | qwen3.5:9b | coding-f1 | on | 0.9 | 1 | 7168 | 30 / 30 | 10 | 0.333 | 20 | 245.4 | 47579 | delivered |
| 25 | grid-so-9b-f1-t09-on-s4-9b.20260929-182031 | qwen3.5:9b | coding-f1 | on | 0.9 | 4 | 7168 | 30 / 30 | 9 | 0.300 | 22 | 286.3 | 54512 | delivered |
| 26 | grid-so-9b-f1-t09-on-s7-9b.20260929-182047 | qwen3.5:9b | coding-f1 | on | 0.9 | 7 | 7168 | 23 / 30 | 9 | 0.391 | 14 | 190.0 | - | STOPPED at 10-01 08:22 pause (partial) |
| 27 | ss-q38b-f1-q3-8b.20260929-005340 | qwen3:8b | coding-f1 | off | default | 1 | 17408 | 30 / 30 | 27 | 0.900 | 0 | 1.4 | 207 | delivered |
| 28 | ss-q38b-f1-t06-think-q3-8b.20260929-005355 | qwen3:8b | coding-f1 | on | 0.6 | 1 | 17408 | 30 / 30 | 29 | 0.967 | 1 | 53.4 | 10432 | delivered |
| 29 | vl8b-ss-f2-vl8b.20260929-175915 | qwen3-vl:8b | coding-f2 | off | default | 1 | 5120 | 72 / 72 | 47 | 0.653 | 25 | 60.4 | 11828 | delivered |
| 30 | grid-so-9b-f2-t00-off-s1-9b.20260929-182014 | qwen3.5:9b | coding-f2 | off | 0.0 | 1 | 7168 | 72 / 72 | 69 | 0.958 | 0 | 1.3 | 182 | delivered |
| 31 | grid-so-9b-f2-t00-off-s4-9b.20260929-182032 | qwen3.5:9b | coding-f2 | off | 0.0 | 4 | 7168 | 72 / 72 | 69 | 0.958 | 0 | 1.2 | 175 | delivered |
| 32 | grid-so-9b-f2-t03-off-s1-9b.20260929-182016 | qwen3.5:9b | coding-f2 | off | 0.3 | 1 | 7168 | 72 / 72 | 66 | 0.917 | 0 | 1.3 | 177 | delivered |
| 33 | grid-so-9b-f2-t03-off-s4-9b.20260929-182033 | qwen3.5:9b | coding-f2 | off | 0.3 | 4 | 7168 | 72 / 72 | 62 | 0.861 | 0 | 2.4 | 382 | delivered |
| 34 | grid-so-9b-f2-t06-off-s1-9b.20260929-182017 | qwen3.5:9b | coding-f2 | off | 0.6 | 1 | 7168 | 72 / 72 | 63 | 0.875 | 0 | 2.5 | 403 | delivered |
| 35 | grid-so-9b-f2-t06-off-s4-9b.20260929-182035 | qwen3.5:9b | coding-f2 | off | 0.6 | 4 | 7168 | 72 / 72 | 59 | 0.819 | 1 | 3.9 | 662 | delivered |
| 36 | grid-so-9b-f2-t09-off-s1-9b.20260929-182018 | qwen3.5:9b | coding-f2 | off | 0.9 | 1 | 7168 | 72 / 72 | 59 | 0.819 | 5 | 10.0 | 1831 | delivered |
| 37 | grid-so-9b-f2-t09-off-s4-9b.20260929-182035 | qwen3.5:9b | coding-f2 | off | 0.9 | 4 | 7168 | 72 / 72 | 54 | 0.750 | 0 | 4.1 | 693 | delivered |
| 38 | ss-9b-f2-9b.20260929-005341 | qwen3.5:9b | coding-f2 | off | default | 1 | 7168 | 72 / 72 | 55 | 0.764 | 1 | 4.2 | 704 | delivered |
| 39 | grid-so-9b-f2-t00-on-s1-9b.20260929-182020 | qwen3.5:9b | coding-f2 | on | 0.0 | 1 | 7168 | 72 / 72 | 53 | 0.736 | 19 | 58.9 | 11392 | delivered |
| 40 | grid-so-9b-f2-t00-on-s4-9b.20260929-182036 | qwen3.5:9b | coding-f2 | on | 0.0 | 4 | 7168 | 72 / 72 | 53 | 0.736 | 19 | 59.5 | 11245 | delivered |
| 41 | grid-so-9b-f2-t03-on-s1-9b.20260929-182021 | qwen3.5:9b | coding-f2 | on | 0.3 | 1 | 7168 | 72 / 72 | 51 | 0.708 | 20 | 65.4 | partial meter | delivered |
| 42 | grid-so-9b-f2-t03-on-s4-9b.20260929-182037 | qwen3.5:9b | coding-f2 | on | 0.3 | 4 | 7168 | 72 / 72 | 48 | 0.667 | 22 | 71.4 | partial meter | delivered |
| 43 | think-9b-ss-f2-9b.20260929-153151 | qwen3.5:9b | coding-f2 | on | 0.6 | 1 | 7168 | 72 / 72 | 46 | 0.639 | 25 | 76.4 | 14955 | delivered |
| 44 | grid-so-9b-f2-t06-on-s4-9b.20260929-182038 | qwen3.5:9b | coding-f2 | on | 0.6 | 4 | 7168 | 72 / 72 | 45 | 0.625 | 25 | 77.0 | 14690 | delivered |
| 45 | grid-so-9b-f2-t09-on-s1-9b.20260929-182022 | qwen3.5:9b | coding-f2 | on | 0.9 | 1 | 7168 | 72 / 72 | 41 | 0.569 | 28 | 94.6 | 17860 | delivered |
| 46 | grid-so-9b-f2-t09-on-s4-9b.20260929-182039 | qwen3.5:9b | coding-f2 | on | 0.9 | 4 | 7168 | 72 / 72 | 45 | 0.625 | 27 | 82.0 | 15569 | delivered |
| 47 | ss-q38b-f2-q3-8b.20260929-005340 | qwen3:8b | coding-f2 | off | default | 1 | 17408 | 72 / 72 | 65 | 0.903 | 0 | 1.0 | 135 | delivered |
| 48 | f2-t06-think-q3-8b.20260928-195830 | qwen3:8b | coding-f2 | on | 0.6 | 1 | 6144 | 72 / 72 | 69 | 0.958 | 2 | 22.1 | 4421 | delivered |
| 49 | tool-vl8b-ss.20260929-175907 | qwen3-vl:8b | tool | off | 0.6 | 1 | 5120 | 72 / 72 | 70 | 0.972 | 0 | 0.5 | 68 | delivered |
| 50 | tool-9b-ss.20260929-005356 | qwen3.5:9b | tool | off | 0.6 | 1 | 7168 | 72 / 72 | 64 | 0.889 | 0 | 0.8 | 106 | delivered |
| 51 | tool-q3-8b-ss.20260929-005356 | qwen3:8b | tool | off | 0.6 | 1 | 17408 | 72 / 72 | 67 | 0.931 | 0 | 0.5 | 67 | delivered |
| 52 | vlm-8b-ss.20260929-005356 | qwen3-vl:8b | vlm 1x | off | 0.6 | 1 | 5120 | 72 / 72 | 72 | 1.000 | 0 | 1.8 | 291 | delivered |
| 53 | vlm-9b-ss.20260929-153219 | qwen3.5:9b | vlm 1x | off | 0.6 | 1 | 7168 | 72 / 72 | 37 | 0.514 | 0 | 1.1 | 95 | delivered |
| 54 | vlm2x-8b-ss4k.20260929-001130 | qwen3-vl:8b | vlm 2x | on | 0.6 | 1 | 4096 | 72 / 72 | 72 | 1.000 | 0 | 2.0 | 312 | delivered |
| 55 | vlm2x-9b-ss.20260929-153126 | qwen3.5:9b | vlm 2x | on | 0.6 | 1 | 7168 | 72 / 72 | 71 | 0.986 | 5 | 13.3 | 2394 | delivered |

## Appendix B - cheapest measured solve per task on Softserve (the price each 10x ceiling is built from)

(Solved rows only, all models and thinking settings; the order ceiling per family is 10x the largest value in that family.)

| family | task | cheapest solve s | by (model, think) | 10x |
|---|---|---|---|---|
| coding-f1 | eval_rpn | 1.7 | qwen3:8b, off | 17 |
| coding-f1 | group_anagrams | 1.7 | qwen3.5:9b, off | 17 |
| coding-f1 | caesar | 1.4 | qwen3:8b, off | 14 |
| coding-f1 | valid_ipv4 | 1.3 | qwen3:8b, off | 13 |
| coding-f1 | rle_decode | 1.3 | qwen3:8b, off | 13 |
| coding-f1 | balanced_brackets | 1.2 | qwen3:8b, off | 12 |
| coding-f1 | top_k_words | 1.0 | qwen3:8b, off | 10 |
| coding-f1 | longest_common_prefix | 1.0 | qwen3:8b, off | 10 |
| coding-f1 | flatten | 0.8 | qwen3:8b, off | 8 |
| coding-f1 | rotate_matrix | 0.7 | qwen3:8b, off | 7 |
| coding-f2 | dbg_chunk_list | 2.0 | qwen3.5:9b, off | 20 |
| coding-f2 | dbg_dedupe_sorted | 1.2 | qwen3:8b, off | 12 |
| coding-f2 | mod_flatten_one | 1.1 | qwen3:8b, off | 11 |
| coding-f2 | dbg_max_subarray_sum | 1.0 | qwen3:8b, off | 10 |
| coding-f2 | mod_words_longer_than | 1.0 | qwen3.5:9b, off | 10 |
| coding-f2 | mod_stats | 1.0 | qwen3:8b, off | 10 |
| coding-f2 | dbg_is_palindrome | 0.9 | qwen3.5:9b, off | 9 |
| coding-f2 | mod_find_max_index | 0.9 | qwen3:8b, off | 9 |
| coding-f2 | dbg_second_largest | 0.9 | qwen3:8b, off | 9 |
| coding-f2 | dbg_running_total | 0.8 | qwen3:8b, off | 8 |
| coding-f2 | dbg_count_vowels | 0.8 | qwen3:8b, off | 8 |
| coding-f2 | dbg_binary_to_int | 0.8 | qwen3:8b, off | 8 |
| coding-f2 | dbg_word_freq | 0.8 | qwen3:8b, off | 8 |
| coding-f2 | dbg_transpose | 0.8 | qwen3:8b, off | 8 |
| coding-f2 | mod_clamp | 0.8 | qwen3:8b, off | 8 |
| coding-f2 | dbg_count_evens | 0.7 | qwen3:8b, off | 7 |
| coding-f2 | dbg_append_to_all | 0.7 | qwen3:8b, off | 7 |
| coding-f2 | mod_keep_positive | 0.7 | qwen3:8b, off | 7 |
| coding-f2 | mod_greet | 0.7 | qwen3:8b, off | 7 |
| coding-f2 | mod_reverse_str | 0.7 | qwen3:8b, off | 7 |
| coding-f2 | mod_total | 0.6 | qwen3:8b, off | 6 |
| coding-f2 | mod_count_words | 0.6 | qwen3:8b, off | 6 |
| coding-f2 | mod_join_words | 0.5 | qwen3:8b, off | 5 |
| coding-f2 | mod_get_field | 0.5 | qwen3:8b, off | 5 |
| tool | update_paid | 0.6 | qwen3:8b, off | 6 |
| tool | update_resolved | 0.6 | qwen3:8b, off | 6 |
| tool | readlog_words | 0.5 | qwen3:8b, off | 5 |
| tool | readlog_dozen | 0.5 | qwen3:8b, off | 5 |
| tool | readlog_negation | 0.5 | qwen3:8b, off | 5 |
| tool | readlog_first_step | 0.5 | qwen3:8b, off | 5 |
| tool | search_timeout | 0.5 | qwen3:8b, off | 5 |
| tool | search_oom | 0.5 | qwen3:8b, off | 5 |
| tool | scale_five | 0.5 | qwen3:8b, off | 5 |
| tool | scale_double | 0.5 | qwen3:8b, off | 5 |
| tool | query_customer | 0.5 | qwen3:8b, off | 5 |
| tool | query_word_id | 0.5 | qwen3-vl:8b, off | 5 |
| tool | query_distractor | 0.5 | qwen3:8b, off | 5 |
| tool | alert_critical | 0.5 | qwen3:8b, off | 5 |
| tool | alert_warning | 0.5 | qwen3:8b, off | 5 |
| tool | scale_zero | 0.5 | qwen3:8b, off | 5 |
| tool | stop_worker | 0.4 | qwen3:8b, off | 4 |
| tool | stop_mailer | 0.4 | qwen3:8b, off | 4 |
| tool | restart_bounce | 0.4 | qwen3:8b, off | 4 |
| tool | nofit_disk | 0.4 | qwen3:8b, off | 4 |
| tool | nofit_reboot_host | 0.4 | qwen3:8b, off | 4 |
| tool | nofit_email | 0.3 | qwen3:8b, off | 3 |
| tool | nofit_translate | 0.3 | qwen3:8b, off | 3 |
| tool | nofit_deploy | 0.3 | qwen3:8b, off | 3 |
| vlm 1x | h_count_tri9 | 3.1 | qwen3-vl:8b, off | 31 |
| vlm 1x | h_num4827 | 1.2 | qwen3-vl:8b, off | 12 |
| vlm 1x | h_table_oldest | 1.2 | qwen3-vl:8b, off | 12 |
| vlm 1x | h_label_b | 1.1 | qwen3-vl:8b, off | 11 |
| vlm 1x | h_thermo_humid | 1.0 | qwen3-vl:8b, off | 10 |
| vlm 1x | h_corner | 1.0 | qwen3-vl:8b, off | 10 |
| vlm 1x | h_plate | 1.0 | qwen3-vl:8b, off | 10 |
| vlm 1x | h_thermo_set | 0.9 | qwen3-vl:8b, off | 9 |
| vlm 1x | h_battery | 0.9 | qwen3-vl:8b, off | 9 |
| vlm 1x | h_sign_level | 0.9 | qwen3-vl:8b, off | 9 |
| vlm 1x | h_table_bob | 0.6 | qwen3.5:9b, off | 6 |
| vlm 1x | h_count_red7 | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_count_star6 | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_receipt_total | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_clock_min | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_ui_checked | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_ui_tab | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_bars_short | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_bars_value | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_word_second | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_light_yellow | 0.5 | qwen3.5:9b, off | 5 |
| vlm 1x | h_ui_red | 0.4 | qwen3.5:9b, off | 4 |
| vlm 1x | h_shape_color | 0.4 | qwen3.5:9b, off | 4 |
| vlm 1x | h_arrow | 0.4 | qwen3.5:9b, off | 4 |
| vlm 2x | h_count_red7 | 5.4 | qwen3-vl:8b, on | 54 |
| vlm 2x | h_count_tri9 | 5.2 | qwen3-vl:8b, on | 52 |
| vlm 2x | h_count_star6 | 1.8 | qwen3-vl:8b, on | 18 |
| vlm 2x | h_bars_value | 1.5 | qwen3-vl:8b, on | 15 |
| vlm 2x | h_table_oldest | 1.2 | qwen3-vl:8b, on | 12 |
| vlm 2x | h_num4827 | 1.1 | qwen3-vl:8b, on | 11 |
| vlm 2x | h_clock_min | 1.1 | qwen3-vl:8b, on | 11 |
| vlm 2x | h_label_b | 1.0 | qwen3-vl:8b, on | 10 |
| vlm 2x | h_thermo_humid | 1.0 | qwen3-vl:8b, on | 10 |
| vlm 2x | h_bars_short | 1.0 | qwen3-vl:8b, on | 10 |
| vlm 2x | h_corner | 1.0 | qwen3-vl:8b, on | 10 |
| vlm 2x | h_plate | 1.0 | qwen3-vl:8b, on | 10 |
| vlm 2x | h_ui_checked | 0.9 | qwen3-vl:8b, on | 9 |
| vlm 2x | h_word_second | 0.9 | qwen3-vl:8b, on | 9 |
| vlm 2x | h_light_yellow | 0.9 | qwen3-vl:8b, on | 9 |
| vlm 2x | h_thermo_set | 0.8 | qwen3-vl:8b, on | 8 |
| vlm 2x | h_battery | 0.8 | qwen3-vl:8b, on | 8 |
| vlm 2x | h_ui_red | 0.8 | qwen3-vl:8b, on | 8 |
| vlm 2x | h_ui_tab | 0.8 | qwen3-vl:8b, on | 8 |
| vlm 2x | h_table_bob | 0.8 | qwen3-vl:8b, on | 8 |
| vlm 2x | h_shape_color | 0.8 | qwen3-vl:8b, on | 8 |
| vlm 2x | h_sign_level | 0.8 | qwen3-vl:8b, on | 8 |
| vlm 2x | h_receipt_total | 0.7 | qwen3-vl:8b, on | 7 |
| vlm 2x | h_arrow | 0.7 | qwen3-vl:8b, on | 7 |
