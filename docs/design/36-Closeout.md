# #36 closeout — split inference on rigby + the Fellowship: what it found, what it hands on (CLOSED 2026-10-16)

**Project #36, a child of campaign #34** ([[PROJECT-REGISTRY]]; parent [[34-CHARTER]] area B; working doc
[[36-Split-Inference-Helpers-Read-and-Draft-Design]]; how the box got there: [[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]]).
Closed on the owner's word, 2026-10-16: *"Close it out baby and keep going."* Scope at close (the owner 10-15): *"Focus on the
stuff we actually have"* — the riser and RAM are out; generalizing up is #34 Phase 9, fed by this doc.

## Conclusion

Inside ONE answer, helpers only pay where they hold compute the main machine lacks. A GTX 1060 in rigby outran ten
tablets at that job, so rigby's A3B now writes ~16.9 tok/s on code (5–6 on the CPU) with the model's own MTP head, and
the helpers add BESIDE it with their own work: the whole box measured working at once (r76) — the A3B ~16 tok/s
(−6 % with the GT 730 serving) plus the Fellowship ~50 tok/s across 10 tablets and the 730.

## Against what #36 set out to do

| set out to | verdict |
|---|---|
| Shared reading (tablets help rigby read) | worked modestly (~1.34× at 4K) on the CPU-era box, then outpaced by the 1060 |
| External drafting | superseded by the model's own MTP head (draft 2, +19–24 % write) |
| Small models fast on tablets | true as independent workers; the GT 730 is the fastest warm node (0.8B at 10.1 tok/s) |
| Roles per device, at once | helpers inside the writing loop lose (3.6× slower); beside it they stack (rigby −0–2 %, r71) |
| Every drop from the box | single stream at the wall (16.8–16.9 tok/s write); every arsenal lever has a verdict (`rigby_arsenal.json`, 0 open) |
| Production | rigby's real work runs on the 1060 standard order (Tolkien), with the runaway guard and compaction |
| Generalize up | not #36's — #34 Phase 9 (below) |

Full evidence per row: the working doc § "Where #36 stands".

## Final measurements

- **A1 role number (the standard order):** `a1v22-rigby-moe-hai-off-spot-1060-final.20261015` — **6 of 10 tasks solved,
  control passed, not void; 0.70 solves/h** over 8.54 h of serial work; ~436 kJ/solve at the placement's 85 W (menu
  wattage, not metered). Failures: react 6/7, paasio 9/12, rest-api priced out at the measured 10,800 s ceiling,
  sgf-parsing ended by the runaway guard. The only earlier complete run of the set (CPU-era team arm, 10-08): 6/10 at
  0.58 solves/h — same solves, 17 % less time (two variables differ).
- **Re-price on the final config:** `menu_fill.RIGBY_PLACEMENT` + per-placement energy in `menu_policy` (built and
  tested 10-15). The A3B's 20 thinking-off cells are filling through the 15-minute top-up — 11 missing, 1 outstanding at
  2026-10-16 06:55 (first cell: f1 30/30 at 7.0 s/solve vs 19.5 on the CPU). It finishes on its own; no #36 action
  remains.

## Hand-offs

| to | what |
|---|---|
| #34 Phase 7 (`policy.json`, Spock) | rigby's 1060 price rows as they land; the GT 730 as an opt-in warm worker (`warm_gt730`) whose use is the policy's call, with the measured −6 % on the A3B when both serve (r76); the 1060's +85 W decode energy (the flat 54 W is right only for CPU rows) |
| #34 S10 (rigby leading the tablets) | the A1 number above |
| #34 Phase 8 (advisor) | rigby / the 730 as possible advisor hosts (not measured as such) |
| #34 (rigby's rung — fleet-class or Fellowship) | **decided against BigHonker running the same split A3B after its RAM lands 2026-10-17**, not from rigby's numbers alone: rigby runs the 35B-A3B as a split (card + CPU-side experts in RAM), the 8 GB boxes could too, and BigHonker's CPU, RAM and card are all stronger (register row 10-16 09:4x). rigby's claim is the work BigHonker has no time for, not the model |
| #34 Phase 9 (generalize up) | the four jobs one layer up (working doc § "Generalize UP"); the one untested idea is the 3.8's context archive in Softserve's RAM; Mothership's RAM swap lands 10-16 |

## Built by #36 that outlives it

- The A1 **runaway-turn guard** (`turn_cap`, sized from 1,749 measured turns) and **context compaction** (Inspect's
  `CompactionSummary`, one call path `a1_chat.py`) on all four factories.
- The 1060 **standard order** (Fellowship-Order-Taker-Design § placement fields): CUDA 11.4 + graphs patch, llama.cpp
  `e467e5b`, MTP draft 2, ubatch 2048, small-read threshold 256.
- **Placement-aware menu pricing** (`RIGBY_PLACEMENT`, per-placement watts).

## Cleanup

- **The plain (non-MTP) A3B GGUF archived** to `/mnt/fellowship/archive/models-retired-20261016/` on rigby's HDD (not
  `models/`, which is the tablet store): two 17 GB A3B files cannot share rigby's 24 GB page cache, so a stray load of
  the old one would cost a cold re-read of the one in use. Copied and hashed with direct I/O so the running MTP model's
  cache was not evicted; sha256 verified equal to Unsloth's original (`a832b968…`, the r69 KLD reference) before the
  original was removed; `README.txt` beside it. Its CPU-path price rows stay on the menu (held); re-measuring them
  means copying it back.

## Dropped, with reasons

- **Hardware levers** (riser, `ram_speed`, `large_pages`): out of #36 by the owner's word 10-15.
