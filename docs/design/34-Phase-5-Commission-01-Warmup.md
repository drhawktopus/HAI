# Project #34 · Phase 5 · Commission 01 — Warm-up (mechanism-only)

**Owner:** HAI (worker). Claude + the owner supervise.
**Armed by:** the owner, 2026-09-04 evening (arming is human-only per `feedback-backlog-deferral-doctrine`).
**Parent design:** [[34-Phase-5-Ensemble-Design]] — the authoritative spec for the whole Phase 5 experiment. This commission is the warm-up arm defined in that spec's §8 ("What starts execution").
**Charter reference:** [[34-CHARTER]] §22–27, Phase 5.

**⚠ Rescoped 2026-09-04 evening.** The prior version of this commission (coding-swarm with Qwen2.5-Coder-1.5B) was routed through HAI's self-engineering plan-mode decomposer, which read it as a coding task and produced a plan that mocked the warm-up as a `run_warmup_task` identity/hash function rather than actually running Fellowship orders. The plan candidate at `/selfeng-proposals/plans/u-arming-phase-5-commission-01-the-warm-58411/` is **stale — do not promote**. This rescoped version narrows the warm-up to mechanism-only, uses the existing `map_echo` payload (no capability gap), and is authored to be run via **direct Absurd** (no plan mode).

**⚠ Second revision 2026-09-04 late evening (post-first-attempt).** HAI's first-attempt Absurd run `abr-1788574758-961440` actually **succeeded** at submitting three `tablet_map` orders (real result files exist in the outbox), but its own critic-loop (§48 blind-reviewer failure mode: big `get_document` reads early in the trail crowded out later `tablet_map` evidence at critic-context budget) falsely concluded the submissions never happened. HAI then rewrote its final answer as an escalation. This revision fixes two real spec bugs from that attempt: (a) `control_item` collided with the first item in the list and inflated `coverage` denominators + set `VOID=True` on all three otherwise-clean runs; (b) monotonic-wall-time criterion was structurally unmeasurable because `tablet_map.submit` has no `hosts=` parameter — the scheduler picks whichever tablet claims first, and trivial payloads got fully drained by one tablet before others could pull. Dropped that criterion; cohort-size arms come after either a `hosts=` extension or the SKU-lane WIP ships.

## 1. Purpose

Prove Tolkien's `map`-kind fan-out mechanism works cleanly across increasing cohort sizes on the 11-tablet fleet, before any real Phase 5 arm consumes tablet-hours. Deliberately excludes the model, the coding-swarm payload (doesn't exist yet), and any ensemble-semantics test — those come in later commissions. This warm-up answers exactly one question: *does an order dispatched to N tablets get all N tablets to process items, return results, and roll up cleanly, for N ∈ {1, 2, 3}?*

The warm-up's success criteria (§3) are all mechanism-focused; none of them require model inference. Piling model inference on top of the mechanism test is bad experimental design (two things at once), which is why the coding scope was pulled out into its own later commission.

## 2. Scope — the exact experiment

**Arm:** independent fan-out (Tolkien's `map`-kind order).
**Payload:** **`map_echo.py`** — the existing whitelisted live-decoy payload at `/mnt/fellowship/payloads/map_echo.py` (also mirrored under `/mnt/ssdshared/tabfoundry-jobs/payloads/`). Echoes each item back with row metadata. Nothing to build.
**Cohort sizes:** submit **three orders**, one per cohort size.

| order name | items | control_item | control_expect |
|---|---|---|---|
| `phase5-warmup-c01-a` | 5 unique strings `item-000`…`item-004` | **`control-canary`** (distinct from any item) | `echo:control-canary` |
| `phase5-warmup-c01-b` | 10 unique strings `item-000`…`item-009` | **`control-canary`** | `echo:control-canary` |
| `phase5-warmup-c01-c` | 15 unique strings `item-000`…`item-014` | **`control-canary`** | `echo:control-canary` |

⚠ **`control_item` MUST be distinct from every entry in `items`** — the factory counts control + items separately in `n_expected`; if the control name matches an item, `coverage` shows `N/(N+1)` and `VOID=True`. This bit the first attempt.

Tablet selection is **whatever the scheduler picks** — `tablet_map.submit` has no `hosts=` parameter, so cohort size is not something this commission controls. The three orders differ only in item count; the point is proving the mechanism (submit → fan → gather → read) works, not measuring cohort behaviour (that's a later commission).

**Order-submission path:** the standard Tolkien intake — drop each `*.tjob.json` in `/mnt/ssdshared/tabfoundry-jobs/inbox/`. **No hand-placed files in `var/work`, no ad-hoc taskkills, no direct foundry-internal calls.** Per the owner's "customer submits ORDERS" rule at the top of `E:\SSDShared\CLAUDE.md`.

**Per-row measurement (must record):** item, host that ran it, wall time, echo result.

**Per-order roll-up (must record):** cohort size, items sent, rows returned, coverage rate, ensemble wall time (max of per-tablet times), straggler ratio (slowest tablet / fastest tablet), control result.

## 3. Success criteria

The warm-up is a **pass** if all of:
1. **100% item coverage** — every submitted item came back with `map_echo`'s expected echo result. `n_measured == n_expected` and `VOID=false`.
2. **Control passed on all three orders** (`control_passed=true`).
3. **Every order reached `usable=true`** — none stuck at `VOID=true` or held.
4. **No unhealthy tablets during the run** — `/p20/factory/fellowship`'s `health_detail` still reports all-N healthy at the end.

Cohort-size behaviour (N=1 vs N=2 vs N=3 tablets doing the work) is **explicitly NOT a warm-up criterion** — the current `tablet_map.submit` interface cannot control it, and testing it belongs in a later commission that ships either a `hosts=` extension or the SKU-lane WIP.

A **fail** on any of the four means STOP and escalate before advancing.

## 4. Where results go

- **Raw per-row data:** `/mnt/ssdshared/tabfoundry-jobs/outbox/phase5-warmup-c01-*.results.json` (this is where map orders land automatically; no new plumbing needed).
- **Rollup + VERDICT:** report **in chat as the Absurd run's final `answer`** — no file writes required this pass. HAI has no general file-write tool at the worker tier, and my first-attempt commission asked for `/mnt/fellowship/phase5/` writes that HAI cannot do. The Absurd `answer` field is durable in `/absurd/status`, readable in the UI, and Claude+the owner can persist it to disk if the Verdict is worth archiving. Format the answer as: (a) a table of the three orders with their `n_measured`, `n_expected`, `VOID`, `control_passed`, `usable`, `wall_s`, and observing `_host` values; (b) one paragraph per §3 criterion (pass/fail with the specific numbers); (c) the single line `STATUS: warmup_passed` or `STATUS: warmup_failed` at the end.

## 5. What NOT to do

- **Do NOT run this in plan mode.** Plan mode invokes HAI's self-engineering decomposer, which will read this as a coding task and produce a mock. Use **direct Absurd** — EFFORT: Absurd, plan mode OFF.
- **Do NOT read the parent design/ensemble/INDEX docs unless a specific field is genuinely unclear.** Each `get_document` returns a whole file and eats critic-context budget. On the first attempt, three early `get_document` calls crowded out the later `tablet_map submit/status/rows` entries from the critic's trail window, and the critic falsely concluded the submissions never happened. This time: read THIS commission doc, then go straight to `tablet_map`. Skip the others unless truly needed.
- **When you report your final answer, RE-STATE the specific tool outputs you rely on (order names + coverage + wall_s + `_host`).** That way the critic's trail-scan finds the values in the answer text itself, not only in early-turn tool entries it may have already dropped. Guard against the same blind-reviewer failure as attempt-1.
- **Do NOT silently rewrite the experiment.** If the mechanism at hand needs something not covered by the standard `map`-kind fan-out, propose the change and hold — do not modify and run.
- **Do NOT redo any Phase 4 work.** Model choice + per-class champions are settled and are not part of this warm-up.
- **Do NOT run on Sméagol.** 32-bit armv7l; not on the aarch64 pool this warm-up targets. Sméagol's opportunistic slot-in comes in later commissions.
- **Do NOT hand-place files in `var/work`.** Drop into `/mnt/ssdshared/tabfoundry-jobs/inbox/` only.
- **Do NOT touch the current plan candidate at `/selfeng-proposals/plans/u-arming-phase-5-commission-01-the-warm-58411/`** — it is stale by design. Do not promote it. It can be left alone (harmless as an unpromoted candidate) or explicitly cancelled at your discretion.

## 6. Stuck / failure escalation

If any of the following happens, **stop and escalate to the owner via DM** (never auto-call Claude — per `feedback-hai-claude-usage-gated`):

- A capability HAI needs is missing (e.g. can't write to `/mnt/fellowship/phase5/`, `map_echo` payload not found, tabfoundry inbox unreachable). File the specifics in `PROJECT-BACKLOG.md` and stop.
- A tablet flips from healthy to unhealthy during the warm-up — investigate briefly, note in the run log, escalate before continuing.
- Any of §3's four criteria fail — write the VERDICT doc explaining which, then escalate.
- Wall time exceeds 20 minutes total for the three orders combined. `map_echo` is trivial; this should complete in a couple minutes wall time end-to-end. Twenty is a runaway.

## 7. On pass — what happens next

HAI writes the VERDICT doc + updates `STATUS.txt` to `warmup_passed`. Then STOPS and waits for the owner's arming of the next commission. **Commission 02 will be a self-engineering commission to build `map_swarm_code.py`** (the real coding-swarm payload that Commission 03 will use for the Phase-5 coding arm). Do not proactively launch Commission 02 or the full ladder.

## 8. Handoff to Claude (if the owner pings me back)

If the owner pings me back for supervision, I should:
- Read `VERDICT.md`, then `rollup.json`, then a sample of the raw `outbox/*.results.json` files.
- Sanity-check the straggler ratio and monotonic-decrease claim by hand (a rollup can hide a systematic bias).
- Commission 02 (the self-eng `map_swarm_code.py` build) was never armed — abandoned 2026-09-28; its staged
  inputs are in `Archive\phase5-commission02-staged-20260928\`.

Under NO condition auto-approve from HAI's own summary — a private note of the owner's adversarial probes applies to HAI's reports too.
