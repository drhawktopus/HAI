# Project #34 · Phase 5 — Ensemble & Execution-Strategy Experiment Design

**Status:** EXECUTION NEARLY COMPLETE — 1,141 / 1,172 arms complete as of 2026-09-08; 23 HELD (final `u_button` VLM slice, held on capacity), 7 terminal fit exclusions (class-B × Qwen2-VL-2B, the owner-ruled), 1 blocked (heterogeneous class-B text critic, provenance/placement failure). Appraisal not started. See `Reference\HANDOFF-Project34-Phase5-after-Codex-20260908.md`.
**Date:** 2026-09-04
**Author:** Main session, from a joint design walk with the owner after the Fellowship grew from 7 → 11 tablets.
**Supersedes on scope:** the Phase 5 line in [[34-CHARTER]] §22–27, which was written when the roster was 7 aarch64 tablets. The charter's *goals* stand; this doc redefines the arms and concurrency rules to use the wider roster without re-litigating Phase 4.
**Not to be silently rewritten** — subsequent design changes need the owner's go, same as this doc did.

## 0. Execution status — plan unchanged (2026-09-08 update)

The expanded plan below remains the authority: real per-strategy, per-N cohorts packed into parallel
non-overlapping windows. The completed run `phase5-homogeneous-s10.20260906-103344` was the S10
baseline slice; nested-prefix summaries are supplemental and do not replace the specified
physical N arms, timing, reliability, or cohort-composition measurements.

Tolkien's execution prerequisites are all deployed: prompt speculative reassignment,
`distinct_workers=N` with selected/actual cohort proof, and the window envelope (`map.window`) that
reserves disjoint cohorts and runs small-N arms concurrently. Execution has run through the plan;
remaining work is the final 23 HELD `u_button` arms (re-admit through Tolkien's inbox once
capacity permits) and one blocked heterogeneous critic arm awaiting the owner's disposition. Do not
start appraisal before both exceptions are resolved.

## 1. Why the design was revisited

Charter §22–27 (written pre-2026-09) described Phase 5 as a `1 → 3 → 5 → 7`-tablet scaling experiment across
independent-swarm, best-of-N, majority/consensus, solver+critic, solver+verifier, and heterogeneous-specialist
strategies, with the `7` meaning "the whole aarch64 fleet."

The Fellowship is now **11 tablets** (see [[Tablet-Cluster-INDEX]]): the seven originals plus **Sméagol** (Samsung
SM-T560NU, armv7l/32-bit) and **Gimli / Boromir / Faramir** (three additional TB-X606F, class-B). The aarch64
subset is now 10. The added width lets us:
- run every point on the N-ladder up to 10 rather than stopping at 7,
- test team layouts (K-critics, chain depth, parallel verifiers) the old roster couldn't fit while still leaving spares,
- run multiple arms **in parallel** on non-overlapping tablet subsets, cutting wall time,
- give some tablets **fixed specialist roles** without starving the primary arm.

**Not repeated in Phase 5** — Phase 4 (concluded 2026-08-31, see [[Fellowship-Gauntlet-Conclusion-2026-08-31]])
already established that *the winning model is SKU-invariant while fit and speed are not*: per-class champions
are settled (Qwen2.5-1.5B general/tool, Coder-1.5B coding, SmolVLM2-500M VLM, Qwen2-VL-2B VLM-quality-specialist,
Qwen3.5-2B chat-quality-specialist). Phase 5 holds the model constant per task using the Phase-4 champion and
varies **team shape**, not model.

## 2. Roster context

| class | tablets | count | role notes |
|---|---|---|---|
| A (X6C6F, 8c/2.79G RAM) | gandalf, aragorn | 2 | roomiest RAM; VLM/heavy-reasoning specialist candidate |
| B (X606F, 8c/1.76G RAM) | frodo, sam, legolas, gimli, boromir, faramir | 6 | primary ensemble pool; the class the added tablets grew |
| C (X505F, 4c/1.82G) | merry, pippin | 2 | fast/simple worker or tie-breaker specialist candidate |
| Sméagol (SM-T560NU, 4c/1.39G, armv7l/32-bit) | sméagol | 1 | text-only critic; arch-neutral tasks only (no ncnn/hwdec/arm64-native) |
| **aarch64 total** | | **10** | main N-ladder domain |
| **Fellowship total** | | **11** | includes Sméagol on text-only arms |

## 3. Arms

### 3.1 N-ladder (independent-swarm / best-of-N / majority-vote)

For each strategy separately, run every cohort size N:

| strategy | N values |
|---|---|
| independent-swarm | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 |
| best-of-N | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 |
| majority-vote | **1, 3, 5, 7, 9** (odd only — even N pollutes with tie-breaks) |

Model = Phase-4 champion for the task type. Cohort composition is whatever aarch64 tablets are free at run time
(no dedicated class-composition axis — Phase 4 settled model quality per class; the residual composition effect is
a straggler-timing artifact worth a follow-on only if it appears in the numbers).

**Sméagol slot-in (opportunistic, not a dedicated arm):** for arch-neutral text-only tasks, Sméagol may be
substituted for one aarch64 tablet at any N to see whether it helps, hurts, or is neutral. Never folded into the
aarch64 headline number. **Not a specialist-role assumption:** the owner's framing (2026-09-04) — Sméagol was added
because he found the tablet and thought it would be funny to include, not because we're relying on it. If the
numbers show it's only good at one narrow thing (or nothing beyond brute compute), that is a fine outcome; do not
design arms around making it useful.

### 3.2 Team layouts (solver+critic / solver+verifier / chained refinement)

All four shapes run against the coding + tool divisions (where deterministic verification is possible) and
against a text-only quality task for the critic arms:

| layout | tablets | what it tests |
|---|---|---|
| **baseline 1-vs-2** | 1 solver alone vs 1 solver + 1 critic | the original charter question; the floor every other layout is measured against |
| **K-critics** | 1 solver + K critics (K = 1, 3, 5) voting on the critique | does adding critique breadth beyond one critic keep helping |
| **chain depth** | S → S+C → S+C+R → S+C+V+R (R = refiner, V = verifier) | does adding refinement/verification stages past the critique keep helping or hit diminishing returns |
| **1 solver + N parallel verifiers** | 1 solver + N tablets running deterministic checks in parallel (test-runner, parser, static-check) | narrow but cheap on coding tasks; verifiers don't do inference, so this uses the extra width without adding ensemble noise |

Model held constant per role using the Phase-4 champion for that role (coding role → Coder-1.5B; critic role →
Qwen2.5-1.5B; verifier is not a model, it's a deterministic check). Sméagol slots in as a text-only critic where
the task's critique can be done on text alone.

### 3.3 Heterogeneous specialists

Each tablet locked into one role for the run. Try both the hardware-natural assignment and a set of
"surprise-combo" assignments, so the numbers pick the winners rather than the hardware assumption:

| role | hardware-natural candidate | surprise-combo candidates |
|---|---|---|
| main pool (writer/solver) | class-B (frodo/sam/legolas/gimli/boromir/faramir) | class-A; class-C |
| heavy-reasoning / VLM specialist | class-A (gandalf/aragorn) | class-B; class-C for VLM-simple |
| fast/simple worker or tie-breaker | class-C (merry/pippin) | class-B; Sméagol on text |
| text critic / spelling-and-tone | (no specialist earmarked — assign whichever tablet is free) | Sméagol; class-C; class-B on text-only |
| deterministic verifier | any aarch64 (verification is not inference) | Sméagol on text-verifier tasks |

Each surprise-combo arm swaps exactly one role assignment vs the hardware-natural baseline, to keep the
one-variable rule.

**Sméagol's expected role is honestly unknown** — the owner added it for the fun of having a genuinely different SKU
in the roster rather than because a specific specialist niche needed filling. It may end up genuinely useful at
some role, or it may end up only viable for arch-neutral brute compute. Both outcomes are acceptable; do not
inflate a role for it that the data doesn't support.

**Execution exceptions surfaced by this section** (as of 2026-09-08):
- **Seven `heterogeneous.heavy_role_on_class_b.vlm.*` cells are terminal `hard_fit_exclusion`.** Qwen2-VL-2B
  cannot pass the pre-inference RAM guard on class-B hardware, and controls are also non-executable. These
  cells provide fit/feasibility evidence only and carry no quality/performance evidence. the owner-ruled; do not
  retry, substitute a model, move to class A, relax the guard, or create replacements. The driver has an
  explicit `excluded` terminal state (distinct from `complete`); campaign completion counts them separately.
- **One blocked arm — `heterogeneous.text_critic_on_class_b.chat.sisters`.** Solver complete. Class-B critic
  reached 24 attempts because critic host Boromir kept overlapping the prior solver's Boromir role. This is
  placement/provenance failure, not answer-quality evidence; awaiting the owner's disposition on whether the
  existing interface can yield a distinct class-B role assignment or an alternative arrangement is authorized.
  Do not blindly retry a 25th identical attempt.

Full state and receipts:
`Reference\HANDOFF-Project34-Phase5-after-Codex-20260908.md`,
`E:\SSDShared\tabfoundry-jobs\campaigns\phase5-remaining.20260906-2040\state.json`,
`.../hard-fit-exclusions-class-b-qwen2vl.json`.

## 4. Concurrency (running arms in parallel)

**Default:** run arms in parallel wherever they fit on non-overlapping tablet subsets. The old sequential
`N=3 → N=5 → N=7` was forced by the 7-tablet roster; with 10 aarch64 we can pack the every-N ladder from §3.1
across parallel windows. Concrete example schedule (independent-swarm arm, 10 aarch64):

| window | concurrent runs |
|---|---|
| 1 | `N=10` (single arm — whole aarch64 fleet, no parallel partner fits) |
| 2 | `N=9` + `N=1` |
| 3 | `N=8` + `N=2` (or `N=7` + `N=3`) |
| 4 | `N=6` + `N=4` (or `N=5` + `N=5` — pair of same-size cohorts, extra data point on variance) |
| 5 | `N=3` + `N=3` + `N=3` + `N=1` (four parallel runs, three same-size for variance measurement) |

Same principle applied per strategy (independent-swarm / best-of-N / majority-vote / team layouts / specialist
arms): any two cohorts that add up to ≤ 10 aarch64 can run in the same window. Sméagol arms and small
odd-window residuals slot into whatever window has time.

**WiFi contention mitigation (the owner's 2026-09-04 point):** every tablet is on the tolkien USB segment on rigby's `br0` ([[Tablet-Architecture-Topology-Fleet-Status]]). Tablet-to-tablet traffic prefers the segment. Tolkien's own traffic to the tablets goes wire-first, WiFi as the backup (ON, `TABLET_ROUTE_PROBE=1`, [[Fellowship-Order-Taker-Design]] §12).

**Fall back to sequential only on measured contention** — not on theoretical contention. WiFi is the shared
medium (single AP, 5 GHz, aggregate ceiling ≈ 333 Mbit/s — see [[Fellowship-Purpose-Architecture-and-Network-Constraints]]);
if two concurrent arms both hit the network hard enough to slow each other measurably, we split them. Chunked
local inference (each tablet computes locally, only submits results) should not contend meaningfully — the
per-run traffic is small.

**No strict spare quota.** The tablets are Fellowship-owned; background work (watchdog, battery governor,
camera detector on the two camera-attached nodes) is negligible and unaffected by Phase 5 arms.

## 5. Mechanisms available

- **Order composition:** the existing `map`-kind fan-out ([[Tablet-Cluster-INDEX]] §"How do I actually RUN
  something") is payload-agnostic — hand it the item list and payload path, it fans across the healthy nodes.
  For Phase 5 we compose one order per arm × cohort size × strategy triple.
- **SKU-lane fan-out** — Codex's uncommitted opt-in `host_sku` mechanism in `tolkien-build/tablet_fulfil.py`
  (per-SKU queues, cross-SKU straggler-stealing disabled, unclaimed-in-a-lane visibly reported never silently
  dropped, backwards-compatible when `host_sku=None`). **Not shipped by default.** Reach for it only if we need
  to isolate a homogeneous cohort or a specialist-role assignment — otherwise the standard single-queue fan-out
  handles everything above.
- **Head + coordination:** tolkien container on rigby (`arrstack-tablet-foundry`) already sees 11/11 nodes as
  healthy and drives them all through the shared SSH key. No new coordination code required.
- **Deterministic verification** per §40 of the charter — always prefer a code check to an LLM judge for the
  coding and tool divisions.

## 6. Deliverables (per arm)

- per-tablet wall time (start / finish / straggler tail)
- per-tablet answer + score (rubric or deterministic verifier)
- ensemble answer + ensemble score
- ensemble wall time (max of tablet times)
- coverage / control checks per the standard tablet-order contract (§9 of [[Fellowship-Order-Taker-Design]])
- rigby-side energy telemetry across the run (already collected by the battery governor / telemetry pipeline)

Aggregate deliverable at end of Phase 5: a table showing, per task type, the smallest team shape that reaches
the quality ceiling — the input to Phase 6 (Voltron) and Phase 8 (controller intelligence / routing).

## 7. Out of scope for Phase 5

- **Any re-run of Phase 4 model×class characterization.** Phase 4 concluded the model choice is SKU-invariant;
  Phase 5 holds the champion model constant.
- **Voltron / pooled inference** — that is Phase 6 by design (charter §Research phase sequence). Phase 5 is
  chunked-out per-tablet ensemble work; Voltron is one model sharded across tablets.
- **New model tournaments** — Phase 4 delivered the champions; introducing new models is a fresh phase, not a
  Phase 5 axis.
- **GPU factory work** — area A stays gated on ollama-pin / ROCm per the charter.

## 8. What starts execution

⚠ **Historical — execution has run.** The the owner go-ahead was given; the warm-up arm (Commission 01, `map_echo`)
passed. Commission 02 as planned (HAI self-engineering building `map_swarm_code.py`) never ran and was abandoned
2026-09-28 — its staged inputs are in `Archive\phase5-commission02-staged-20260928\`. Execution completed
2026-09-26 at 1,165 complete / 7 excluded / 0 held / 0 blocked of 1,172 arms, and Phase 5 closed —
[[34-Phase-5-Appraisal-Closeout]] is canonical.

- the owner's go-ahead on this doc (or edits + go-ahead). ✅ 2026-09-04.
- One warm-up arm first (probably the smallest — swarm N=1 → N=3 on a coding task) to shake out the harness
  before the full parallel run, so a harness bug doesn't waste a batch. ✅ Commission 01, `map_echo`, passed
  2026-09-06.
- Then the parallel-by-default schedule above. ✅ Ran through 2026-09-08.

**Next gate:** finish or disposition the 23 HELD `u_button` VLM N-ladder arms and the one blocked
heterogeneous critic arm, then the owner reviews/authorizes the appraisal implementation shape.

## 9. References

- [[34-CHARTER]] §22–27 (original Phase 5 language), §49 (phase sequence), §Excluded Workloads
- [[Fellowship-Gauntlet-Conclusion-2026-08-31]] (Phase 4 close — per-class champions, "SKU-invariant winner")
- [[Fellowship-Performance-Baselines-and-Benchmarking]] (per-node speed baselines and Phase 4 detail)
- [[Fellowship-Workload-Classification-and-Scheduling-Logic]] (which jobs fit; scheduler integration)
- [[Fellowship-Order-Taker-Design]] §9 (order submission contract), §10 (watchdog), §11 (homepage tile)
- [[Tablet-Cluster-INDEX]] (11-tablet roster + capabilities)
- CURRENT-PROJECTS 2026-09-04 top block (Fellowship-eleven landing + Codex reconciliation)
