# Project 34 BigHonker menu review 2026-10-02 - arm evidence (dominated arms are KEPT; only can't-fit or priced-out arms stop)

**Rule in force (the owner 2026-10-02, [[34-Hard-Bench-Order-Spec]] § "When an arm stops being filled"):** an arm stops on
this box only if it CANNOT FIT (answers hit the tested-max context) or is PRICED OUT (≥ 10× this box's cheapest solve,
on ≥ 2 families, reproduced on this box). Verdicts below that say "PRICE OUT (dominated)" mean dominated — those arms
stay on the menu as fallbacks for when the best box is busy. Evidence is unchanged.

**Resolved 2026-10-02 (build, the owner: "Hit it baby"):** decisions 1, 2, 3(partly: the t0.0 duplicates remain), 4 and 7 below are
done — arm holds, the GPU price line, the ledger fix, the stop list and its cancel slips (73 queued orders + the stopped 9B f1
order, applied when the runner next starts) and the menu rebuild; see [[34-Hard-Bench-Order-Spec]] § "Holds" and "Price
ceilings". Decisions 5, 6 and 8 are still the owner's.

**Status: PRELIMINARY** (the spec's own rule, [[34-Hard-Bench-Order-Spec]] and [[34-Shape-Catalogue]]: a reading graduates only when it holds across a
second task family and reproduces once). **Written for the owner, morning of 2026-10-02, before BigHonker is resumed** (the owner 08:50: *"I want to review
before I turn them loose again"*). **Read-only review:** nothing was queued, cancelled, resumed or edited; BigHonker's and Softserve's pause flags,
runner tasks and queues are as the owner left them on 2026-10-01 08:22. Project: #34, area A/B (the GPU menu fill, [[34-CHARTER]]).

## What was read, and how much (counts asserted)

- **72 BigHonker result files parsed, 3,825 answer rows, 72 distinct delivered orders** (3,041 rows solved). 60 are `out.json` files inside
  `..._bighonker` order folders under `tabfoundry-jobs\campaigns\hard-bench-2026092{8,9}\` and `...27\fleet-tv\`; 12 are the earlier hand-run
  `out_*.json` rungs (09-27/09-28) that the spec already quotes. Zero parse failures. Grading: 56 files carry their own on-box grade
  (`infer_ollama --grade`); 9 earlier coding files use the menu table's re-grade; 7 tool/vlm files were re-graded here with the payloads' own scorers
  (`map_tool_swarm_hard.score_tool`, `map_vlm_swarm_hard._vlm_score`, the same calls as `hard_bench_fleet_tv.reduce`). Strict grading as ruled
  2026-09-28.
- **Also read:** `menu_fill.py`, `menu_fill_hold.json`, `menu_fill_topup.log` (to 08:23 today), the BigHonker foundry queue directory (listing only), BigHonker's
  Windows System/Application event logs and `gpu_telemetry-20260929.csv` (read-only over ssh), the charter, spec, catalogue, closeout, the 09-28
  to 10-02 journals and [[BigHonker-3070-DPC-Watchdog-Investigation]].
- **"Since the last review":** no earlier review of BigHonker's menu results exists in the library (searched the 34-* docs and the 09-28 to 10-02 journals;
  the 10-01 "adversarial review" is the Flash-Next engine, a different subject). The window is therefore the whole BigHonker fill (first fleet rungs
  2026-09-27 through the last delivery, 2026-10-01 08:06). The menu table `Datasets\Project34-Menu\` was last built 2026-09-29 18:16 and **does not contain
  the 40 files / 1,950 rows delivered after that** (nor `tool-vl8b-bh`, which `fleet_tv.md` also lacks): rebuild it (`build_menu.py`, run with no timeout)
  before reading it.
- **Not parsed:** Softserve, Mothership and rigby result files. Cross-factory numbers below are quoted from the spec and `fleet_tv.md` and are labelled as such.

## The price rule as the docs define it, and what BigHonker has of it

[[34-Hard-Bench-Order-Spec]] sec. "Price ceilings": an answer is **priced out once it costs 10x the cheapest measured solve of that task family on the SAME box**; a
priced-out answer is a stated measurement (`priced_out`), never a fail; "is another box cheaper" is the routing question, not the price. The 10x is Claude's
owner call (2026-09-30) and the owner's to change (`PRICE_X`).

**Finding: the ceiling is implemented for rigby only.** `menu_fill.py` attaches `price_ceiling_s` in `rigby_submit` and nowhere in `submit_cell` (the GPU path);
`price_ceiling` / `priced_out` appear only in `menu_fill.py` and Tolkien's `tolkien_llm.py` / `tablet_runner.py`, nowhere in the foundry's `infer_ollama` path. BigHonker
cells therefore run every answer to completion, however long. To price BigHonker arms I applied the filler's own rigby recipe to BigHonker's delivered
results: per task, the cheapest solved wall time by any BigHonker arm; the order-level ceiling = 10 x the worst of those.

| family | worst per-task cheapest BigHonker solve | order-level ceiling (10x) |
|---|---|---|
| coding f1 | `eval_rpn` 1.9 s | **19.0 s** |
| coding f2 | `dbg_chunk_list` 2.4 s | **24.0 s** |
| tool | `update_resolved` 0.7 s | **7.0 s** |
| vlm 1x | `h_count_tri9` 5.3 s | **53 s** |
| vlm 2x | `h_count_red7` 6.7 s | **67 s** |

(Reconstruction, not a stored value; it only tightens as cheaper solves arrive. These are time ceilings; the 3070's working draw is near-flat (~135 W in the logger rows below), so energy ratios track time ratios.)

## Headline (five findings)

1. **89% of BigHonker's delivered answer time went into cells that are void by the context-limit rule.** 25 of 72 orders (1,326 rows) had >= 5% of rows end at the
   context cap: 21.9 of 24.6 answer-hours. These are **qwen3.5:9b thinking-on** (19 coding orders and the 2x vision order; 31-43% of rows at the 7,168 cap pooled) and **qwen3-vl:8b on
   coding** (4 orders, 27-61% at its 5,120 cap), plus one 9B non-thinking order with 2 of 33 rows at the cap. Doctrine 13a: a cell where a real share of rows hit a cap is VOID, not a price. The caps ARE the tested maximum on this 8 GB card, so these cells **cannot be re-run
   with room on BigHonker**; more orders only repeat the void.
2. **The queue still holds 93 job specs (+ the one stopped by the 10-01 pause) ~ 50 GPU-hours (estimate), ~40 h of it in those same void arms**, plus 9 h of qwen3:8b
   thinking-on (valid but priced out). What is worth running is ~0.6 h.
3. **Two undocumented BigHonker bugchecks (0x133) happened on 2026-09-29 at 00:28:47 and 00:40:37**, mid-order, at the 1500 MHz lock, 6-7 minutes into a qwen3-vl:8b
   coding load; neither is in the 3070 investigation or any journal. Details and which results they touch: sec. "3070 stability".
4. **BigHonker's winners are cheap and boring:** qwen3:8b thinking-off (coding 0.92-0.93, 0.9-1.4 s, 106-163 J per solved) and qwen3-vl:8b for vision
   (1.00, 259-264 J) and tool (0.97, 58 J). Everything else on the box is dominated by one of these.
5. **At temperature 0.0 with thinking off, seed sets 4 and 7 are byte-identical to seed set 1** (qwen3.5:9b: 33/33 and 75/75 answer texts equal): the filler orders them anyway.

## Summary table (BigHonker arms; PRELIMINARY)

Pass rate = solved / rows pooled over the arm's orders, strict grading; range = per-order min-max. s and J are **per solved** (sum of all answer times, or all board
joules, divided by solved; board power `nvidia-smi@4Hz`, not above idle, idle baseline differs 23-49 W by order so compare ratios >= 2x only). "Over ceiling" = share of
rows slower than the ceiling above. At cap = rows ending at the context limit.

| arm (model, family, thinking) | orders / rows | pass rate (range) | s / solved | J / solved | at cap | over ceiling | verdict |
|---|---|---|---|---|---|---|---|
| qwen3:8b, coding f1, off | 6 / 198 | **0.92** (0.88-0.97) | 1.4 | 163 | 0 | 0% | **KEEP** |
| qwen3:8b, coding f2, off | 4 / 300 | **0.93** (0.92-0.93) | 0.9 | 106 | 0 | 0% | **KEEP** |
| qwen3:8b, tool, off | 1 / 72 | 0.93 | 0.6 | 58 | 0 | 0% | KEEP, re-measure once (one order) |
| qwen3-vl:8b, vision 1x, off | 1 / 72 | **1.00** (24/24 tasks always right) | 2.2 | 264 | 0 | 0% | **KEEP** |
| qwen3-vl:8b, vision 2x, thinking on (ctx 4096) | 1 / 72 | **1.00** | 2.2 | 259 | 0 | 0% | **KEEP** (ctx 4096 never capped) |
| qwen3-vl:8b, tool, off | 1 / 72 | 0.97 | 0.6 | 58 | 0 | 0% | **KEEP**, best BigHonker tool arm (answers filed under `thinking`, graded via the reducer's fallback) |
| qwen3.5:9b, coding f2, off, t 0.0 | 2 / 150 | 0.96 (identical orders) | 1.5 | 151 | 0 | 0% | KEEP the cell; a non-dominated point (+3-4 pts for ~1.5x the cost) but ONE deterministic sample set |
| qwen3.5:9b, coding f2, off, t 0.3-0.9 | 10 / 750 | 0.79-0.88 | 3.3-5.7 | 383-697 | 11 | ~2% | **PRICE OUT** (dominated by qwen3:8b off) |
| qwen3.5:9b, coding f1, off (all temps) | 18 / 594 | 0.81 (0.70-0.97) | 6.6 | 827 | 6 | 4% | **PRICE OUT** (dominated by qwen3:8b off: lower rate, 4.7x time, 5x J) |
| qwen3.5:9b, tool, off | 1 / 72 | 0.89 | 1.0 | 94 | 0 | 0% | PRICE OUT (qwen3:8b 0.93 and qwen3-vl:8b 0.97, both 58 J) |
| qwen3.5:9b, vision 1x, off | 1 / 72 | 0.50 (11 tasks never right) | 1.1 | 74 | 0 | 0% | PRICE OUT on reliability (vision belongs to qwen3-vl:8b); no more seeds needed |
| qwen3.5:9b, vision 2x, thinking on | 1 / 72 | 0.96 | 23.3 | 3,043 | **9 (12%)** | 17% | **VOID** + PRICE OUT (12x qwen3-vl:8b's J) |
| qwen3.5:9b, coding f1, thinking on | 10 / 330 | 0.54 (0.33-0.67) | 149 | 19,591 | **143 (43%)** | 88% | **VOID** + PRICE OUT; cannot be re-measured with room on this box |
| qwen3.5:9b, coding f2, thinking on | 9 / 675 | 0.68 (0.63-0.77) | 86 | 11,437 | **211 (31%)** | 58% | **VOID** + PRICE OUT; same |
| qwen3:8b, coding f1, thinking on | 1 / 33 | 0.97 | 32 | 4,464 | 0 | 82% | **PRICE OUT** (27x the off arm's J; +5 pts is inside the off arm's 0.88-0.97 seed spread); re-measure once only if the owner wants the number |
| qwen3:8b, coding f2, thinking on | 1 / 75 | 0.96 | 27.5 | 3,895 | 0 | 29% | **PRICE OUT** (37x the off arm's J for +3 pts) |
| qwen3-vl:8b, coding f1, off AND on | 2 / 66 | 0.39 / 0.52 | 165 / 132 | n/m / 18,180 | **20 / 18 of 33** | 88-91% | **VOID**; the card hung twice during the off order; cannot be measured at the 5,120 cap |
| qwen3-vl:8b, coding f2, off AND on | 2 / 150 | 0.73 / 0.72 | 58 / 60 | 7,989 / 8,407 | **20 / 22 of 75** | 61-63% | **VOID**; same. The "off" arm is a thinking run (the model ignores `think:false`: 20 empty answers) |
| qwen3.5:9b, tool / vision, thinking on; qwen3:8b tool/thinking-on; qwen3-vl:8b tool/vision thinking-on | 0 | unmeasured | | | | | unmeasured (not yet ordered) |
| aha2025 joycaption, huihui qwen3-vl-abliterated, medgemma:4b, qwen3.5:35b | 0 | **unmeasured** | | | | | unmeasured; see decisions 5 |

Every number above is computed from the 72 files (appendix lists each order). Per-knob detail (temperature) is in the appendix.

## Evidence per verdict

### KEEP: qwen3:8b thinking-off is the cheapest reliable BigHonker coding arm
f1: 6 orders, six seed/temperature blocks, 31/33 30/33 30/33 31/33 32/33 29/33 (0.88-0.97); f2: 4 orders 69-70/75 at every temperature 0.0-0.9 (flat, no knob effect).
Typical answer 0.9-1.3 s, 0 rows at cap, ctx 17,408 (tested max). It beats qwen3.5:9b (same box) on both families at 4-5x lower time and energy. Spec corroboration
([[34-Hard-Bench-Order-Spec]] sec. "Every model the GPU factories hold"): qwen3:8b f2 0.931, 0.9 s, 97 J.

### KEEP: qwen3-vl:8b for vision and tool
Vision 1x 72/72 and 2x-thinking-on 72/72 (always-right on all 24 tasks), 2.2 s, 259-264 J, reproduced on Softserve per the spec (`vlm-8b-ss` 291 J, `vlm2x-8b-ss4k` 312 J).
Tool 70/72 at 0.6 s / 58 J (every answer filed under `thinking`; 72/72 empty `content`, graded through the same fallback the reducer uses). The only vision/tool arm on the
box with both reliability and low cost. (Its coding cells are the opposite, below.)

### PRICE OUT: qwen3.5:9b non-thinking arms (dominated on the same box)
Same task, same box, same grading: qwen3:8b off is more reliable on f1 (0.92 vs 0.81), f2 (0.93 vs 0.85) and tool (0.93 vs 0.89), and 4-5x cheaper in time and joules. The 9B's best
cell is greedy f2 (0.96, byte-identical across two seed sets, so n = 75 effective, not 150) which buys 3-4 points for ~1.5x the cost: kept as a stated point, not a recommendation.
Temperature shapes the 9B (f2: 0.96 / 0.88 / 0.83 / 0.79 at 0.0 / 0.3 / 0.6 / 0.9, monotone across 2-4 orders each; f1 non-monotone, 0.73 / 0.91 / 0.84 / 0.77) and is flat for qwen3:8b.
The 9B is HAI's chat model on this box; this verdict is about its menu cells only.

### VOID and PRICE OUT: qwen3.5:9b thinking-on, and qwen3-vl:8b on coding
- 9B thinking-on: all 19 coding orders (f1 + f2) have 20-61% of rows at the 7,168 cap (10-20 of 33 f1 rows; 15-28 of 75 f2 rows; 354 of 1,005 rows in all); capped rows have empty `content`
  (140 + 209 empty answers) and read as failures. The spec already named this ("the 9B needs the same re-run but its box is down", 09-28) and the doc's own rule
  (13a) says re-run with room - but 7,168 is the card's tested maximum, so the room does not exist here. Independent of the cap, its answers average 55-80 s against 3-5 s with thinking off
  (24x the joules per solve) and the pass rate it reaches is **lower** than off, so even the lower bound makes it a priced-out arm.
- qwen3-vl:8b coding: `think:false` is ignored by this model (the "off" orders have 20/33 and 20/75 empty answers), so off and on are the same thinking run capped at its 5,120 context;
  27-61% of rows end at the cap. Not a coding model on this box. If the owner wants it measured, the room exists on Mothership (qwen3-vl:8b tested ctx 222,208 in `menu_fill.py`).
- Answer time already spent on these cells: 3.0 h (qwen3-vl:8b coding, 4 orders) + 18.3 h (9B thinking-on coding, 19 orders); 46 + 5 more cells are queued.

### PRICE OUT: qwen3:8b thinking-on (valid, but 27-37x cost)
Two clean cells (0 rows at cap; ctx 17,408): f1 0.97, f2 0.96, but 31 s / 26 s per answer against 1.3 / 0.9 s off, 4,464 / 3,895 J per solve against 163 / 106. 82% (f1) and 29% (f2) of
rows exceed the ceilings above. The f1 gain (+5 pts over the off arm's pooled 0.92) lies inside the off arm's own seed range (0.88-0.97). One order per family: PRELIMINARY; the cost ratio is
the finding and it will not change with a second run. 22 more thinking-on cells are queued (9.2 h).

### Temperature 0.0, thinking off: duplicates
qwen3.5:9b f1 off t0.0: 24/33 in all four orders (seed sets 1, 4, 7 and the 09-28 run); f2 t0.0: 72/75 in both; answer texts of seed sets 4 and 7 equal seed set 1 on 33/33
and 75/75 rows (verified here). The spec says the same of tablets (greedy gives N identical tries). Seed sets 4 and 7 add rows and no information. Thinking-on t0.0 is
not byte-identical (30-31 of 33 identical) and is a different matter.

## 3070 stability: results taken near an unstable card

Context: [[BigHonker-3070-DPC-Watchdog-Investigation]] (factory OC; 1500 MHz lock "extends the fuse, does not remove it"; 09-28 17:46:37 lost after ~72 min).
Checked read-only over ssh on 2026-10-02.

| when (BigHonker local) | evidence | source |
|---|---|---|
| 09-28 17:46:37 | nvlddmkm 153, GPU lost; dwm.exe crash 17:46:46 | System/Application log (already documented, sec. 0y) |
| 09-28 20:55:45 | reboot (the owner-approved) | boot log |
| **09-29 00:28:47** | per-second logger's last row before a 3-minute hole: core 1500 MHz, 56 C, hotspot 73 C, 137 W, load 92-93%, VRAM 7,256 MB; order `vl8b-f1` has no row between 00:27:50 and 00:33:36 | `gpu_telemetry-20260929.csv`, order rows |
| 09-29 00:31:28 | WER 1001: "rebooted from a bugcheck 0x133", dump `092926-14046-01.dmp` (no nvlddmkm event was logged for this first hang) | System log |
| **09-29 00:40:37** | nvlddmkm **153 and 14** at 00:40:37; logger's last row same second: 1500 MHz, 56 C, hotspot 72.7 C, 135 W, load 91% | System log, logger |
| 09-29 00:43:11 | WER 1001 bugcheck 0x133, dump `092926-12765-01.dmp`; boot 00:42:57 | System log |
| 09-29 00:43:12 to now | no bugcheck, TDR 4101 or nvlddmkm event in 3.3 days (checked 08:36 10-02); logger heartbeat fresh 08:36 | System log |

**Signature identical to sec. 0y** (flat clock, flat temperature, power inside budget) but **time-to-failure far shorter: ~6.3 min (00:22:31 first order row to 00:28:47) and ~7 min (00:33:36 to 00:40:37) of continuous
qwen3-vl:8b load**, against 72 min on 09-28. The same qwen3-vl:8b load then ran 78 minutes straight (00:45-02:03) and every order since without an event, so this is not a clean
"the card is worse now": it is two more data points for the investigation, **neither recorded there or in any 09-29/09-30 journal**.

**Which results are affected**
- `vl8b-f1` (09-29 00:22-01:09, qwen3-vl:8b coding f1): interrupted twice (rows after the holes: 6 / 6 / 21 across three segments); VOID anyway (61% at cap). Not usable.
- `vl8b-f2` (01:10-02:03): no event inside it, starts 27 min after the second hang; VOID anyway (27% at cap). Treat as suspect.
- `tool-q3-8b` (00:11-00:12): finished before the first hang; unaffected.
- 09-28 `think-9b` (16:34-17:42) ended 4 min before the 17:46:37 loss; `think-q3-8b` (12 partial rows, 11 of 75 on the GPU per the 09-28 journal) was never delivered; `think-9b-7k` (21:10-22:25, after the reboot, per-second logger running) is flat.
- 53 of the 72 orders started after the second reboot (09-29 00:43:12) and ran on that one boot with no event; `vl8b-f2` is the only one within 30 minutes of a hang. The 19 earlier orders ran on the 09-27 18:18 boot (11 of them
  09-27/28 daytime) or in the cases above. One more overlap, already documented in sec. 0y: the 09-27 22:00-22:05 `tool-9b` and `vlm-8b` rungs coincide with the milder self-recovering nvlddmkm 153 at 22:00:43 (ollama retried; their
  results, 0.89 and 1.00, equal Softserve's twins in `fleet_tv.md`). **Apart from the two vl8b orders, no result was taken while the card was visibly misbehaving.**
- Throughput check on every order (rows > 15 s, > 1,500 tokens): median tokens/s 59-64 (9B), 62-64 (qwen3-vl), 67 (qwen3:8b), no order below 0.8x its model's median. No CPU-fallback signature anywhere.

## The queue the owner is about to release (BigHonker, counted 2026-10-02 08:3x)

93 `*.job.json` in `F:\foundry\jobs\queue` + the stopped `grid-bi-9b-f1-t06-on-s7` (requeue slip `pause-bh.ctl.json` waiting, 9 rows kept: 5 solved, 4 at cap). `menu_fill_topup.log` 08:23:
BigHonker grid 672, missing 523 not yet submitted, outstanding 94; hold `bighonker` (no `until`).

| queued cells | count | measured cost per cell (mean delivered order of the same arm) | est. hours | assessment |
|---|---|---|---|---|
| qwen3-vl:8b coding f1 off/on, f2 off/on | 12 + 11 + 12 + 11 = **46** | 36-54 min | 34.4 | void at its cap; price out |
| qwen3.5:9b coding f2 thinking on (s7) | 4 | 73 min | 4.9 | void; price out |
| qwen3.5:9b coding f1 thinking on (s7) | 1 | 44 min | 0.7 | void; price out |
| qwen3:8b coding f1/f2 thinking on | 11 + 11 = **22** | 17 / 33 min | 9.2 | valid but 27-37x cost; price out |
| qwen3:8b coding f1/f2 off | 7 + 9 = 16 | 0.7-1.1 min | 0.3 | keep (4 are t0.0 duplicates) |
| qwen3.5:9b coding f2 off (s7) | 4 | 4 min | 0.3 | keep t0.0 only if not duplicate (1 is a duplicate) |
| **total** | **93** | | **~49.8** | **~40 h void arms; ~9 h priced-out arms; ~0.6 h worth running** |

(Estimate = cells x mean delivered order wall of the same arm. The vl8b means come from ONE delivered order per family, so treat them as rough; qwen3:8b thinking-on likewise.)

**What the filler orders next after the queue drains below 25** (breadth-first by seed set, models in alphabetical order): joycaption, huihui qwen3-vl-abliterated, medgemma:4b,
qwen3.5:35b - four models with **no result at all** (360 of the 523 missing cells), then tool/vision for the three measured models. Facts from the docs: qwen3.5:35b is 23.9 GB "in the store only" on an 8 GB card with 16 GB RAM
([[34-Hard-Bench-Order-Spec]], `OFFLOAD_OK` rule: it is deliberately not on the offload list), so its orders meet `infer_ollama`'s residency guard (the first Flash-Next cell "was refused forever" the same way);
joycaption (5.8 GB) and huihui (6.1 GB) have no tested context (they would run at the 6,144 bench standard; unmeasured whether they stay resident). Nothing about their results is known: unmeasured.

**Hold granularity.** `menu_fill_hold.json` takes `"<box>"` or `"<box>/<model>"` only. Holding `bighonker/qwen3-vl:8b` would also block its 70 not-yet-ordered tool/vision cells (the ones that are valuable); there is
no hold by family or by thinking setting. Pricing out a knob today means cancelling queued orders by control slip (the desk mechanism), not a filler setting. (The filler's count of 94 outstanding = the 93
queued specs + the stopped order; the spec's known ledger gap is that a stopped or cancelled order stays "outstanding" forever.)

## Cross-factory view (routing, informational: not a price-out under the same-box rule; numbers quoted, not parsed)

| task | BigHonker best (this review) | other factory (quoted) | note |
|---|---|---|---|
| coding f2 | qwen3:8b off 0.93, 0.9 s, 106 J/solved | Mothership qwen3-coder:30b **1.000**, 0.5 s, 37 J/answer; qwen3.6:35b-a3b 0.986, 0.7 s, 144 J ([[34-Hard-Bench-Order-Spec]] sec. "Every model the GPU factories hold") | Mothership meter is the 11-s telemetry CSV (coarse) vs 4 Hz; not like-for-like |
| coding f2 | same | rigby Coder-3B 0.82, 6 s, 803 J at the wall (spec) | BigHonker cheaper and more reliable |
| tool | qwen3-vl:8b 0.97, 58 J | Mothership `tool-vl8b` 1.00, 44 J; `tool-coder30b` 0.96, 60 J (`fleet_tv.md`) | comparable |
| vision | qwen3-vl:8b 1.00, 259-264 J | Mothership qwen3-vl:8b 1.00 466 J; qwen3.8:27b 940 J; qwen3.6:27b ~4,200 J (spec) | **BigHonker is the cheapest reliable vision cell in the menu so far** |

If the menu's catalogue says "route to the cheapest reliable cell", BigHonker's coding arms (all of them) are dominated by Mothership's MoE models on speed and energy, while BigHonker owns vision. That is a
Phase 7 routing call, not a reason to stop measuring BigHonker, and it is why the verdicts above are same-box only.

## Appendix A: per-knob detail (pooled over orders; n = rows)

qwen3:8b off (ctx 17,408): f1 t0.0/0.3/0.6/0.9 = 30/33, 30/33, 92/99, 31/33; f2 69/75, 70/75, 70/75, 70/75; tool (t0.6) 67/72.
qwen3.5:9b off (ctx 6,144 for the 09-27/28 runs, 7,168 for the 09-29+ grid): f1 t0.0 96/132 (4 identical orders), t0.3 120/132 (0.88-0.97), t0.6 139/165 (0.76-0.91), t0.9 101/132 (0.70-0.82); f2 t0.0 144/150 (2 identical orders), t0.3 197/225, t0.6 248/300, t0.9 178/225.
qwen3.5:9b on: f1 t0.0/0.3/0.6/0.9 = 0.49 / 0.65 / 0.56 / 0.42 with 41/37/28/37 rows at cap; f2 0.69 / 0.73 / 0.68 / 0.63 with 47/38/72/54 at cap.

## Appendix B: every order parsed (start time in local time on Softserve; "n/m" = energy not metered on every row)

| # | start (local) | order | model | fam | think | temp | seed set | ctx | rows | solved | rate | rows at ctx cap | s / answer | J / solved | note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 09-27 19:21 | `fleet/out_9b` | qwen3.5:9b | f1 | off | n/r | 1 | 6144 | 33 | 26 | 0.79 | 0 | 3.4 | 521 |  |
| 2 | 09-27 22:00 | `fleet-tv/out_tool-9b` | qwen3.5:9b | tool | off | 0.6 | 1 | 6144 | 72 | 64 | 0.89 | 0 | 0.9 | 94 |  |
| 3 | 09-27 22:02 | `fleet-tv/out_vlm-8b` | qwen3-vl:8b | vlm | off | 0.6 | 1 | 4096 | 72 | 72 | 1.00 | 0 | 2.2 | 264 |  |
| 4 | 09-28 08:43 | `fleet-tv/out_vlm2x-8b-bh4k` | qwen3-vl:8b | vlm2x | on | 0.6 | 1 | 4096 | 72 | 72 | 1.00 | 0 | 2.2 | 259 |  |
| 5 | 09-28 11:52 | `fleet-t03/out_9b` | qwen3.5:9b | f1 | off | 0.3 | 1 | 6144 | 33 | 29 | 0.88 | 0 | 3.7 | 511 |  |
| 6 | 09-28 11:55 | `fleet-t06/out_9b` | qwen3.5:9b | f1 | off | 0.6 | 1 | 6144 | 33 | 30 | 0.91 | 0 | 4.1 | 542 |  |
| 7 | 09-28 11:57 | `fleet-t09/out_9b` | qwen3.5:9b | f1 | off | 0.9 | 1 | 6144 | 33 | 27 | 0.82 | 1 | 6.6 | 1011 | 1 at cap |
| 8 | 09-28 12:01 | `fleet-f2-t06/out_9b` | qwen3.5:9b | f2 | off | 0.6 | 1 | 6144 | 75 | 62 | 0.83 | 2 | 4.1 | 601 | 2 at cap |
| 9 | 09-28 12:10 | `fleet-t00/out_9b` | qwen3.5:9b | f1 | off | 0.0 | 1 | 6144 | 33 | 24 | 0.73 | 0 | 6.9 | 1183 | greedy: identical to other seed sets |
| 10 | 09-28 12:35 | `fleet-f2-t03/out_9b` | qwen3.5:9b | f2 | off | 0.3 | 1 | 6144 | 75 | 66 | 0.88 | 1 | 2.7 | 356 | 1 at cap |
| 11 | 09-28 12:39 | `fleet-f2-t09/out_9b` | qwen3.5:9b | f2 | off | 0.9 | 1 | 6144 | 75 | 59 | 0.79 | 0 | 3.5 | 543 |  |
| 12 | 09-28 13:25 | `fleet-f2-t06-rep2/out_9b` | qwen3.5:9b | f2 | off | 0.6 | 101 | 6144 | 75 | 64 | 0.85 | 1 | 3.4 | 480 | 1 at cap |
| 13 | 09-28 13:32 | `f1-t06-rep2-9b` | qwen3.5:9b | f1 | off | 0.6 | 101 | 6144 | 33 | 25 | 0.76 | 1 | 5.7 | 924 | 1 at cap |
| 14 | 09-28 16:08 | `f2-t06-models-q3-8b` | qwen3:8b | f2 | off | 0.6 | 1 | 6144 | 75 | 70 | 0.93 | 0 | 0.9 | 108 |  |
| 15 | 09-28 16:09 | `f1-t06-models-q3-8b` | qwen3:8b | f1 | off | 0.6 | 1 | 6144 | 33 | 31 | 0.94 | 0 | 1.3 | 161 |  |
| 16 | 09-28 16:34 | `f2-t06-think-9b` | qwen3.5:9b | f2 | on | 0.6 | 1 | 6144 | 75 | 49 | 0.65 | 26 | 54.1 | 10980 | VOID: 26/75 at cap |
| 17 | 09-28 21:10 | `f2-t06-think-9b-7k-9b` | qwen3.5:9b | f2 | on | 0.6 | 1 | 7168 | 75 | 51 | 0.68 | 23 | 59.5 | 11644 | VOID: 23/75 at cap |
| 18 | 09-29 00:12 | `tool-q3-8b` | qwen3:8b | tool | off | 0.6 | 1 | 17408 | 72 | 67 | 0.93 | 0 | 0.5 | 58 |  |
| 19 | 09-29 00:22 | `vl8b-f1-vl8b` | qwen3-vl:8b | f1 | off | n/r | ? | 5120 | 33 | 13 | 0.39 | 20 | 64.8 | n/m | VOID: 20/33 at cap; energy metered on 21/33 rows (resumed order) |
| 20 | 09-29 01:10 | `vl8b-f2-vl8b` | qwen3-vl:8b | f2 | off | n/r | ? | 5120 | 75 | 55 | 0.73 | 20 | 42.5 | 7989 | VOID: 20/75 at cap |
| 21 | 09-29 15:32 | `vlm2x-9b-bh` | qwen3.5:9b | vlm2x | on | 0.6 | 1 | 7168 | 72 | 69 | 0.96 | 9 | 22.4 | 3043 | VOID: 9/72 at cap |
| 22 | 09-29 15:59 | `think-q3-8b-bh-q3-8b` | qwen3:8b | f1 | on | 0.6 | 1 | 17408 | 33 | 32 | 0.97 | 0 | 31.3 | 4464 |  |
| 23 | 09-29 16:17 | `think-q3-8b-bh-f2-q3-8b` | qwen3:8b | f2 | on | 0.6 | 1 | 17408 | 75 | 72 | 0.96 | 0 | 26.4 | 3895 |  |
| 24 | 09-29 16:51 | `vlm-9b-bh` | qwen3.5:9b | vlm | off | 0.6 | 1 | 7168 | 72 | 36 | 0.50 | 0 | 0.6 | 74 |  |
| 25 | 09-29 18:00 | `tool-vl8b-bh` | qwen3-vl:8b | tool | off | 0.6 | 1 | 5120 | 72 | 70 | 0.97 | 0 | 0.6 | 58 |  |
| 26 | 09-29 18:01 | `q3-8b-bh-t00-q3-8b` | qwen3:8b | f1 | off | 0.0 | 1 | 17408 | 33 | 30 | 0.91 | 0 | 1.3 | 169 | greedy: identical to other seed sets |
| 27 | 09-29 18:02 | `q3-8b-bh-t03-q3-8b` | qwen3:8b | f1 | off | 0.3 | 1 | 17408 | 33 | 30 | 0.91 | 0 | 1.3 | 163 |  |
| 28 | 09-29 18:04 | `q3-8b-bh-t09-q3-8b` | qwen3:8b | f1 | off | 0.9 | 1 | 17408 | 33 | 31 | 0.94 | 0 | 1.2 | 156 |  |
| 29 | 09-29 18:08 | `q3-8b-bh-f2-t00-q3-8b` | qwen3:8b | f2 | off | 0.0 | 1 | 17408 | 75 | 69 | 0.92 | 0 | 0.9 | 109 | greedy: identical to other seed sets |
| 30 | 09-29 18:10 | `q3-8b-bh-f2-t03-q3-8b` | qwen3:8b | f2 | off | 0.3 | 1 | 17408 | 75 | 70 | 0.93 | 0 | 0.9 | 104 |  |
| 31 | 09-29 18:12 | `q3-8b-bh-f2-t09-q3-8b` | qwen3:8b | f2 | off | 0.9 | 1 | 17408 | 75 | 70 | 0.93 | 0 | 0.9 | 103 |  |
| 32 | 09-29 18:13 | `9b-bh-f2-t00-9b` | qwen3.5:9b | f2 | off | 0.0 | 1 | 7168 | 75 | 72 | 0.96 | 0 | 1.4 | 148 | greedy: identical to other seed sets |
| 33 | 09-29 18:16 | `think-9b-bh-9b` | qwen3.5:9b | f1 | on | 0.6 | 1 | 7168 | 33 | 18 | 0.55 | 13 | 79.3 | 19571 | VOID: 13/33 at cap |
| 34 | 09-29 19:00 | `think-vl8b-bh-vl8b` | qwen3-vl:8b | f1 | on | 0.6 | 1 | 5120 | 33 | 17 | 0.52 | 18 | 68.2 | 18180 | VOID: 18/33 at cap |
| 35 | 09-29 19:38 | `think-vl8b-bh-f2-vl8b` | qwen3-vl:8b | f2 | on | 0.6 | 1 | 5120 | 75 | 54 | 0.72 | 22 | 42.9 | 8407 | VOID: 22/75 at cap |
| 36 | 09-29 20:33 | `q3-8b-bh-seeds4-q3-8b` | qwen3:8b | f1 | off | 0.6 | 4 | 17408 | 33 | 32 | 0.97 | 0 | 1.3 | 156 |  |
| 37 | 09-29 20:34 | `9b-bh-seeds4-9b` | qwen3.5:9b | f1 | off | 0.6 | 4 | 7168 | 33 | 26 | 0.79 | 1 | 8.3 | 1341 | 1 at cap |
| 38 | 09-29 20:40 | `q3-8b-bh-seeds7-q3-8b` | qwen3:8b | f1 | off | 0.6 | 7 | 17408 | 33 | 29 | 0.88 | 0 | 1.3 | 175 |  |
| 39 | 09-29 20:41 | `9b-bh-seeds7-9b` | qwen3.5:9b | f1 | off | 0.6 | 7 | 7168 | 33 | 28 | 0.85 | 0 | 3.3 | 459 |  |
| 40 | 09-29 20:44 | `grid-bi-9b-f1-t00-off-s1-9b` | qwen3.5:9b | f1 | off | 0.0 | 1 | 7168 | 33 | 24 | 0.73 | 0 | 6.8 | 1177 | greedy: identical to other seed sets |
| 41 | 09-29 20:48 | `grid-bi-9b-f1-t03-off-s1-9b` | qwen3.5:9b | f1 | off | 0.3 | 1 | 7168 | 33 | 29 | 0.88 | 0 | 3.7 | 509 |  |
| 42 | 09-29 20:51 | `grid-bi-9b-f1-t06-off-s1-9b` | qwen3.5:9b | f1 | off | 0.6 | 1 | 7168 | 33 | 30 | 0.91 | 0 | 4.0 | 540 |  |
| 43 | 09-29 20:54 | `grid-bi-9b-f1-t09-off-s1-9b` | qwen3.5:9b | f1 | off | 0.9 | 1 | 7168 | 33 | 27 | 0.82 | 1 | 7.1 | 1104 | 1 at cap |
| 44 | 09-29 20:58 | `grid-bi-9b-f1-t00-on-s1-9b` | qwen3.5:9b | f1 | on | 0.0 | 1 | 7168 | 33 | 16 | 0.48 | 15 | 80.1 | 22027 | VOID: 15/33 at cap |
| 45 | 09-29 21:43 | `grid-bi-9b-f1-t03-on-s1-9b` | qwen3.5:9b | f1 | on | 0.3 | 1 | 7168 | 33 | 21 | 0.64 | 12 | 75.3 | 15709 | VOID: 12/33 at cap |
| 46 | 09-29 22:25 | `grid-bi-9b-f1-t09-on-s1-9b` | qwen3.5:9b | f1 | on | 0.9 | 1 | 7168 | 33 | 11 | 0.33 | 20 | 91.5 | n/m | VOID: 20/33 at cap; energy metered on 9/33 rows (resumed order); resumed after pause; stopped original kept as partial |
| 47 | 09-30 08:14 | `grid-bi-9b-f2-t03-off-s1-9b` | qwen3.5:9b | f2 | off | 0.3 | 1 | 7168 | 75 | 66 | 0.88 | 1 | 3.0 | 398 | 1 at cap |
| 48 | 09-30 08:19 | `grid-bi-9b-f2-t06-off-s1-9b` | qwen3.5:9b | f2 | off | 0.6 | 1 | 7168 | 75 | 62 | 0.83 | 2 | 4.5 | 675 | 2 at cap |
| 49 | 09-30 08:25 | `grid-bi-9b-f2-t09-off-s1-9b` | qwen3.5:9b | f2 | off | 0.9 | 1 | 7168 | 75 | 59 | 0.79 | 0 | 3.5 | 549 |  |
| 50 | 09-30 08:30 | `grid-bi-9b-f2-t00-on-s1-9b` | qwen3.5:9b | f2 | on | 0.0 | 1 | 7168 | 75 | 51 | 0.68 | 24 | 59.6 | 11566 | VOID: 24/75 at cap |
| 51 | 09-30 09:45 | `grid-bi-9b-f2-t03-on-s1-9b` | qwen3.5:9b | f2 | on | 0.3 | 1 | 7168 | 75 | 58 | 0.77 | 15 | 52.5 | 8996 | VOID: 15/75 at cap |
| 52 | 09-30 10:51 | `grid-bi-9b-f2-t09-on-s1-9b` | qwen3.5:9b | f2 | on | 0.9 | 1 | 7168 | 75 | 47 | 0.63 | 28 | 65.6 | 13916 | VOID: 28/75 at cap |
| 53 | 09-30 12:14 | `grid-bi-9b-f1-t00-off-s4-9b` | qwen3.5:9b | f1 | off | 0.0 | 4 | 7168 | 33 | 24 | 0.73 | 0 | 6.9 | 1194 | greedy: identical to other seed sets |
| 54 | 09-30 12:18 | `grid-bi-9b-f1-t03-off-s4-9b` | qwen3.5:9b | f1 | off | 0.3 | 4 | 7168 | 33 | 30 | 0.91 | 2 | 9.9 | 1406 | VOID: 2/33 at cap |
| 55 | 09-30 12:24 | `grid-bi-9b-f1-t09-off-s4-9b` | qwen3.5:9b | f1 | off | 0.9 | 4 | 7168 | 33 | 23 | 0.70 | 0 | 3.4 | 595 |  |
| 56 | 09-30 12:27 | `grid-bi-9b-f1-t00-on-s4-9b` | qwen3.5:9b | f1 | on | 0.0 | 4 | 7168 | 33 | 17 | 0.52 | 13 | 78.8 | 20521 | VOID: 13/33 at cap |
| 57 | 09-30 13:10 | `grid-bi-9b-f1-t03-on-s4-9b` | qwen3.5:9b | f1 | on | 0.3 | 4 | 7168 | 33 | 21 | 0.64 | 15 | 82.1 | 17524 | VOID: 15/33 at cap |
| 58 | 09-30 13:56 | `grid-bi-9b-f1-t06-on-s4-9b` | qwen3.5:9b | f1 | on | 0.6 | 4 | 7168 | 33 | 19 | 0.58 | 15 | 82.1 | 19006 | VOID: 15/33 at cap |
| 59 | 09-30 14:42 | `grid-bi-9b-f1-t09-on-s4-9b` | qwen3.5:9b | f1 | on | 0.9 | 4 | 7168 | 33 | 17 | 0.52 | 17 | 87.0 | 22482 | VOID: 17/33 at cap |
| 60 | 09-30 15:30 | `grid-bi-9b-f2-t00-off-s4-9b` | qwen3.5:9b | f2 | off | 0.0 | 4 | 7168 | 75 | 72 | 0.96 | 0 | 1.5 | 154 | greedy: identical to other seed sets |
| 61 | 09-30 15:33 | `grid-bi-9b-f2-t03-off-s4-9b` | qwen3.5:9b | f2 | off | 0.3 | 4 | 7168 | 75 | 65 | 0.87 | 0 | 2.9 | 397 |  |
| 62 | 09-30 15:37 | `grid-bi-9b-f2-t06-off-s4-9b` | qwen3.5:9b | f2 | off | 0.6 | 4 | 7168 | 75 | 60 | 0.80 | 1 | 3.3 | 494 | 1 at cap |
| 63 | 09-30 15:42 | `grid-bi-9b-f2-t09-off-s4-9b` | qwen3.5:9b | f2 | off | 0.9 | 4 | 7168 | 75 | 60 | 0.80 | 3 | 6.3 | 993 | 3 at cap |
| 64 | 09-30 15:50 | `grid-bi-9b-f2-t00-on-s4-9b` | qwen3.5:9b | f2 | on | 0.0 | 4 | 7168 | 75 | 52 | 0.69 | 23 | 59.6 | 11528 | VOID: 23/75 at cap |
| 65 | 09-30 17:05 | `grid-bi-9b-f2-t03-on-s4-9b` | qwen3.5:9b | f2 | on | 0.3 | 4 | 7168 | 75 | 52 | 0.69 | 23 | 57.1 | 11039 | VOID: 23/75 at cap |
| 66 | 09-30 18:17 | `grid-bi-9b-f2-t06-on-s4-9b` | qwen3.5:9b | f2 | on | 0.6 | 4 | 7168 | 75 | 52 | 0.69 | 23 | 58.9 | 11296 | VOID: 23/75 at cap |
| 67 | 09-30 19:31 | `grid-bi-9b-f2-t09-on-s4-9b` | qwen3.5:9b | f2 | on | 0.9 | 4 | 7168 | 75 | 47 | 0.63 | 26 | 59.1 | 12582 | VOID: 26/75 at cap |
| 68 | 09-30 20:46 | `grid-bi-9b-f1-t00-off-s7-9b` | qwen3.5:9b | f1 | off | 0.0 | 7 | 7168 | 33 | 24 | 0.73 | 0 | 6.9 | 1193 | greedy: identical to other seed sets |
| 69 | 09-30 20:50 | `grid-bi-9b-f1-t03-off-s7-9b` | qwen3.5:9b | f1 | off | 0.3 | 7 | 7168 | 33 | 32 | 0.97 | 0 | 2.8 | 340 |  |
| 70 | 09-30 20:53 | `grid-bi-9b-f1-t09-off-s7-9b` | qwen3.5:9b | f1 | off | 0.9 | 7 | 7168 | 33 | 24 | 0.73 | 0 | 3.6 | 602 |  |
| 71 | 09-30 20:55 | `grid-bi-9b-f1-t00-on-s7-9b` | qwen3.5:9b | f1 | on | 0.0 | 7 | 7168 | 33 | 16 | 0.48 | 13 | 78.2 | 21622 | VOID: 13/33 at cap |
| 72 | 09-30 21:39 | `grid-bi-9b-f1-t03-on-s7-9b` | qwen3.5:9b | f1 | on | 0.3 | 7 | 7168 | 33 | 22 | 0.67 | 10 | 71.0 | n/m | VOID: 10/33 at cap; energy metered on 5/33 rows (resumed order); resumed after pause; stopped original kept as partial |

## Sources

`E:\SSDShared\tabfoundry-jobs\campaigns\hard-bench-20260927\{fleet,fleet-tv}`, `...-20260928\*`, `...-20260929\*` (72 result files, listed above); `...\fleet-tv\fleet_tv.md`;
`E:\SSDShared\SSD\Open Access\Claude Home\phase5-prep\commission-artifacts\hard-bench\{menu_fill.py, menu_fill_hold.json, menu_fill_topup.log, box_models.json}`;
`Documentation\Datasets\Project34-Menu\{menu-cells.csv, build-manifest.json, coverage.md}` (built 2026-09-29 18:16); BigHonker (read-only over ssh): `F:\foundry\jobs\queue` listing,
`F:\foundry\var\state\telemetry\gpu_telemetry-20260929.csv`, System and Application event logs since 2026-09-28; [[34-CHARTER]], [[34-Shape-Catalogue]], [[34-Hard-Bench-Order-Spec]], [[34-Hard-Bench-Closeout]],
[[BigHonker-3070-DPC-Watchdog-Investigation]], journals 2026-09-28 to 2026-10-02. Analysis scripts were session scratch files and are not preserved.
