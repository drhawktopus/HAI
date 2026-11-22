# Project 34 — S10: rigby lead + Fellowship sub-agents, orchestrated by Tolkien — DRAFT for the owner, 2026-09-28

**the owner, 2026-09-28:** *"The bigger wins from that category will come from using a larger model on Rigby that is
helping to do work and have sub agents with the 10 tablets. Sort of a similar shape to how I frequently work with
you… sometimes we send out sub agents… sometimes via HAI. I'm kind of imagining having a similar split there with
Rigby and the Fellowship, all being orchestrated by Tolkien."* Catalogue row: [[34-Shape-Catalogue]] S10.

**the owner, same day, on which shape:** *"they both sound interesting. I think I was more envisioning how I work with you
though, and in this case 'you' would equate to Tolkien… that's my current mental model of how I want my overall lab
to be. With Kirk being 'you' and in that being HAI."*

## Two ways to run it — both measured, the agent loop is the main arm

| | fixed recipe | agent loop ("Tolkien = Claude") |
|---|---|---|
| who decides | Tolkien's code: tablets first, then rigby; or rigby writes tests, tablets generate | the lead model on rigby, turn by turn |
| the lead's tools | none | narrow, typed tools Tolkien executes and guards: `run_tests(code)` (sandboxed — the model sees its own errors), `ask_tablets(prompt, n)` (a window on the Fellowship) |
| why | cheap, predictable baseline | the shape the owner works in; adds the lever no test so far has had — models seeing their own errors and retrying |
| risk | none new | 3B–14B models are weak tool drivers — measured, not assumed; tools stay narrow |

The same split applies one level up: Kirk (deterministic desk) + HAI's 27B (judgment) + the factories as sub-agents.
Authority stays in the code layer in both — the charter's Phase 8 rule ("deterministic validation → execute/reject").

## Lead-model choice: rigby reads slowly, so keep what the lead reads short

Measured 2026-09-28 ([[34-Hard-Bench-Order-Spec]] § rigby alone): family 2 — Coder-3B 0.82 (~10 tok/s), 7B 0.83
(~4.7), 14B **0.92** (~2.4 tok/s writing, **~4.2 tok/s reading a cold prompt**). The prompt cache makes repeated text
nearly free, but every agent turn brings NEW text (test errors, tablet replies): five 150-token tablet answers cost
the 14B ~3 min of reading. So: a fast lead (Coder-3B / 7B) for turn-heavy loops, the 14B as the hard-residue step,
and tools return compact results (pass/fail + the first error line, not transcripts).

**2026-10-06: no rigby tool-calling verdict yet.** A first reading ("Coder-3B cannot drive native tool calls") was void: the A1 bridge sent every tool with no parameters (fixed, seam-tested; archived in `Archive\docs-corrected-20261006.md`). The agreed lead shortlist (the Qwen3.6 MoE thinking-off, then the 14B) is measured on A1 with the fix.

## What the library already settles (build on it, do not re-test it)

- **Generate many, then check, is the lever.** Tablet best-of-N with the hidden checks climbs 0.42 → 0.92 (family 1)
  and 0.61 → 0.94 (family 2) from N 1 to 9 ([[34-Hard-Bench-Order-Spec]]). Majority voting does not help.
- **Refiners degrade answers; critics add nothing at 1.5B** ([[34-Phase-5-Appraisal-Closeout]]). S10 does not
  rebuild the Phase 5 team layouts with a bigger critic.
- **rigby alone is a real rung** (S5r): Coder-3B family 2 0.82 at ~6 s per answer, level with the 9B on BigHonker;
  7B / 14B measured next (the lead-model candidates). Voltron is priced and capped.
- **The two lanes run side by side without slowing each other** (co-location probe, [[Fellowship-Order-Taker-Design]]
  §13), so rigby can lead while the tablets work.
- The charter already names rigby as the home for a controller-side model (Phase 8, "controller augmentation").

## The gap S10 fills

Best-of-N only works when something can **check** an answer. On the Hard Bench the hidden checks do that; in real
work there are no hidden checks. The lead's first job is to supply what the tablets cannot: **the checks, and the
answer when every tablet fails.**

## Arms, in order of cost (all on the same hard pools, same seeds, graded by the same hidden checks)

| arm | what the lead does | what the tablets do | new build |
|---|---|---|---|
| **S10a cascade** | answers only the tasks where no tablet candidate passed | best-of-N candidates (N 5 or 9) | **none** — computed from today's results (tablet matrix rows + rigby S5r rows) |
| **S10b lead writes the tests** | reads the task, writes unit tests (no answer) | best-of-N candidates; Tolkien keeps the first candidate that passes the lead's tests | one `llm` prompt variant ("write tests"); a reducer that runs lead tests on existing tablet candidates |
| **S10c lead plans, tablets draft pieces** | splits a larger task into functions + a contract, assembles | draft each function (best-of-N per piece) | a decomposed task pool + an order that chains both lanes (only if S10a/b earn it) |

**S10a result, family 2 (computed 2026-09-28 from delivered results, `hard-bench-20260928\s10a-cascade-f2.md`,
`s10a_cascade.py`; PRELIMINARY):** tablets alone (Coder-1.5B best-of-5) 0.75 · rigby alone (Coder-3B, three trials)
0.75–0.88 · **cascade 0.83–0.92**, with rigby answering only the 6 tasks no tablet candidate solved (a quarter of
its work). The team beats either alone. The catch that makes S10b the key arm: the tablets' "first candidate that
passes" used the hidden checks as the selector — real work has none, so the lead has to supply the checks.

What each arm is priced on (the catalogue scorecard): accuracy against the hidden checks, wall time, rigby time
used, tablet time used, and against the comparisons already on the menu — rigby alone (S5r), tablet best-of-N (S2),
the 9B (S6), the 27B (S7).

- **S10a** asks: does the head only need to work on the hard residue? (Cheap: from existing rows.)
- **S10b** is the real test of the owner's split: a lead that makes tablet work *checkable* turns best-of-N into something
  usable on tasks without hidden checks. Its honesty check: how often the lead's tests accept a candidate the hidden
  checks reject (false pass) and reject one they accept (false fail).
- **S10c** is the full "lead + sub-agents" shape, and the most build. Gate it on S10a/b.

## S10c BUILT (2026-10-06) — the lead's `delegate` deals tablet pieces through Tolkien's inbox

Built on the A1 harness (34-A1 "A1-team"), not on the hard pools: the lead on rigby runs the A1 agentic loop with
one extra tool, `delegate(brief)`, and each brief becomes a Tolkien `map` order on **`map_brief.py`** (a tablet
answers one free-text brief; the brief rides in the item, base64) drafted by one tablet, with the control riding
along. The order is dropped in Tolkien's own INBOX by the rigby lane and run by the fleet lane beside it; the lead
waits on the order's status (delivered / HELD / retired) and gets the answer as the tool result. Pieces are priced
per brief on the row (`delegations`, `delegate_wait_s`). Code: `payloads\a1\a1_fellowship.py`,
`payloads\map_brief.py`, `a1_task.DELEGATE`; submitter `hard-bench\inspect_lab\submit_s10c.py`; piece orders carry
`notify: failures` (one DM per lead run). **Organic run (the tool merely available), 2026-10-06:** the MoE lead on the
spot set made **0 delegated pieces** in two exercises (wordy fail, 51 min; sgf-parsing fail, 2.9 h) — cut at that
boundary by the owner's standing evolutionary rule; same answer as the 3.8 on the GPU layer. **Directed run** (the lead is
told to use the helper, `team.mode: "directed"`, `submit_s10c.py --directed`) submitted 12:53:
`a1v22-rigby-moe-hai-off-spot-team-fe-dir.20261006-1253`; control = the full-set MoE solo's rows on the same
exercises. The MoE's CPU prompt-eval price (~1–3 h per exercise as transcripts grow) is the lead-choice problem the
design's "rigby reads slowly" section predicted. Results: 34-A1 spec, A1-team section. S10b is built below.

## S10b BUILT (2026-10-06 18:04) — the lead writes the CHECKS, the tablets' existing candidates are chosen by them

Computed on the family-2 pool, no new tablet run (the tablets' 24 × 5 Coder-1.5B calibration candidates of 2026-09-28
are the best-of-N; `calibration_rows-coding-f2.csv`). Three pieces, the lab's own machinery throughout:

- **`hard_tasks_f2_tests.py`** — the 24 family-2 tasks with the lead's job inverted: the prompt shows the task exactly
  as family 2 does (description + ORIGINAL code) and asks for the CHECKS a correct implementation must pass, in the
  lab's own check contract — one ```python block assigning `TESTS = ["lambda: f(...) == ...", ...]`, ≥ 3 strings.
  The ONE on-box check is a SHAPE check (TESTS exists, ≥ 3 lambda strings that compile); REFERENCE = the hidden checks
  as a TESTS block (positive control), ORIGINAL = the function (a lead that solved instead fails the shape check).
  `selftest()`: shape check True on every REFERENCE, False on every ORIGINAL; the reference suite accepts
  `hard_tasks_f2.REFERENCE` and rejects its ORIGINAL (a selector that cannot tell right from wrong is a hole).
  Payload `payloads\map_swarm_code_hard_f2tests.py` from `gen_hard_payload.py --pool f2tests --suffix _f2tests`
  (sha `8fffa26c`; references 24/24 through the payload grader, control 4/4, selftest PASS).
- **`s10b_select.py`** — the reducer: per (task, replicate) the lead's TESTS (read by `ast`, never exec'd) are graded
  through the family-2 payload's own child-process `score()` — the ONE grader every hard-bench row went through,
  proven on a known-good program first — against the REFERENCE (must accept), the ORIGINAL (must reject) and every
  tablet candidate in seed order; the first candidate the lead accepts is the pick, and the HIDDEN checks grade the
  pick. Honesty per candidate: FALSE PASS (lead accepts, hidden rejects), FALSE FAIL (lead rejects, hidden accepts).
  Baselines on the same candidates: tablets best-of-5 by the hidden checks (the selector real work does not have) and
  first-candidate (no selector). Prices: lead wall and energy (54 W metered marginal), tablet wall. Output
  `campaigns\hard-bench-20260928\s10b-select-<label>-<stamp>.md/.json`, PRELIMINARY. Tests `test_s10b_select.py` (4):
  a valid suite that a constant-answer candidate slips past (false pass, selected-but-wrong), an invalid suite
  (rejects the reference: false fail + false pass), a function instead of checks (nothing to select with), the
  reference suite as TESTS (right pick, no false verdicts).
- **`submit_s10b.py`** — the lead order, kind `llm` on rigby, same lead settings as S10c (MoE, thinking off, ctx
  32,768, temperature 0.6), 24 tasks × 3 replicates, `notify: failures`, no price ceiling (no measured solve yet).
  **Submitted 18:04: `s10b-rigby-moe-f2tests-s1.20261006-1804`**, queued behind the directed S10c in the rigby lane.
  Reduce with `python s10b_select.py --lead s10b-rigby-moe-f2tests-s1.20261006-1804` when it delivers.
  **RESULT 2026-10-09 12:30 (PRELIMINARY, one lead order; `campaigns\hard-bench-20260928\s10b-select-rigby-moe-s1-
  20261009-20261009-1230.md/.json`):** lead-checks-select-tablet-candidate solves **14/24 on each of the three
  replicates** — exactly the no-selector baseline (first candidate in seed order, 14/24) and 4 short of the
  hidden-check best-of-5 ceiling (18/24). The lead's checks are honest but strict: false pass 4, 0, 0 of 120
  candidate verdicts (one task, `mod_keep_positive` rep 1, accepted the original); false fail 13, 18, 9 of 120; 18–19
  of 24 suites valid (the invalid ones reject the reference, spread across both task kinds). "Nothing selected" 9–10
  tasks per replicate: 6 have no correct candidate at all (the tablets' own ceiling), the rest are suites that
  accepted none of the five — the residue the lead would answer itself.
  Lead cost 834–1,330 s wall (28.8–49.4 s median per suite), 45–72 kJ per replicate. Reading: on family 2 the lead's
  checks add **no solves over "take the first candidate"**, but they almost never ship a wrong answer (false-pass
  rate ≤ 3.3 %), which is the property real work pays for; the 4-task gap to the hidden ceiling is the price of not
  having hidden checks.

**The rest of the ladder on this layer (2026-10-06 19:20, from the charter — "the same shape across all layers; if the
3.8 needs tablets it uses tablets" — and this design's `ask_tablets`; the A1 spec's "delegation only goes DOWN the
layers"):** the rigby lead's finish gate and A1-review are the GPU layer's, one layer down. **The reviewer is a
TABLET** (Coder-1.5B at ctx 16,384, the same `map_brief` path as a drafted piece, order prefix `a1review-`,
`a1_fellowship.FellowshipDelegator(prefix="a1review-")`, spec `review` block, `submit_s10c.py --review`); the gate is
`submit_s10c.py --finish-gate` (spec `finish_gate: true`). Rows carry `finish_gate`, `review` (the verdict record),
`review_spec`, `review_pieces`, `review_wait_s`; `reduce_a1.rigby_arm` reads them like a GPU row. Queue on the rigby
lane after S10b: the MoE solo SPOT control (`--solo`; the full-set solo `a1v22-rigby-moe-hai-off.20261006-0721` is
HELD, releasable), then `…-team-fe-dir-gate`, then `…-team-fe-dir-gate-rev-fe` — each rung one variable against the
one below, as on the GPU layer.

What it answers: accuracy of lead-checks-select-tablet-candidate against the hidden checks, versus the tablets'
best-of-5 with the hidden selector (S10a's number) — the gap is the price of not having hidden checks; the false-pass
rate is the cost real work would pay in shipped wrong answers; "nothing selected" is S10a's residue, where the lead
would answer itself (S10a + S10b together is the full split; computed next from the lead's own S5r rows).

## How it runs (the lab's own machinery)

Every step is a Tolkien order: tablet candidates are `map.window` orders in the fleet lane; lead work is kind `llm` in
the rigby lane. S10a and S10b need no new order kind; S10c would add one order that holds rigby and deals tablet
pieces — Tolkien orchestrating both lanes, as the owner described. Results ship to the outbox like every order.

## Decisions for the owner

1. Order: S10a now (no build) → S10b → S10c only if a/b show value. Or a different first cell.
2. Lead model: pick from the S5r 7B / 14B results when they land today (speed vs accuracy on rigby).
