# 07 — Adversarial Fixer Dispute (#4) — PLAN (for review, not yet built)

**Status:** ✅ BUILT + DEPLOYED (2026-07-01 ~11:25 CDT). the owner's §6 decisions: adjudicator = **same critic pool**
(quorum default **2**, `ADVERSARY_DISPUTE_QUORUM`); disputable = **LOW severity only** (medium+ always fix-only);
rollout = **on immediately** (`ADVERSARY_DISPUTE_ENABLED=true`, kept as a kill-switch); author decides per finding
(default). Implemented as a **dispute filter on critiques** (fix path untouched): `deliberation.DISPUTE_AUTHOR_SYSTEM`
+ `DISPUTE_ADJUDICATE_SYSTEM`; `adversary._ledger_key`/`_apply_ledger` + `run_ladder(dispute=…)` with `_run_dispute`
applied to the initial critique + every re-critique; `app._ov_review._dispute` (author decision → pool adjudication →
quorum) wired in. Upheld disputes recorded in `trail["disputes"]`. Tests: LedgerTests (2) + DisputeTests (6). Deploy
verified: backup `pre-dispute-20260701-162131`; 5 files sha256-matched; in-container suite **748 OK**; /health 200.
Sibling improvements #1/#2/#5 already DEPLOYED. **NEXT: review #3 together** (see §7 — #2+#4 may make it moot).

## 1. Problem

In the overnight ladder (`app.py:_ov_review` → `adversary.run_ladder`), when the critic pool returns findings the
author (27B) is **forced to "test-anchor + fix" every actionable finding, every round** (`anchor_and_fix`). On an
already-correct solution whose findings are nitpicks/non-material, this degenerates: the author piles on tests/changes
to satisfy nits that aren't real defects (the unify benchmark: a 206-line integration-test file bolted onto a 120-line
module, and the run still bailed). The author has **no way to push back** — no "this finding is not material / already
handled / incorrect, and here's why" — which is exactly the move a strong human engineer makes in code review.

## 2. Proposed behavior — fix OR dispute, critic adjudicates

Per actionable finding, the author may choose:
- **FIX** — current behavior: add a failing test, fix until the deterministic verifier is green.
- **DISPUTE** — produce a written rebuttal (no code change) arguing the finding is non-material, already-handled, or
  wrong.

Disputed findings then go to an **adjudication** step: the critic pool re-evaluates each rebuttal and either **ACCEPTS**
the dispute (finding drops) or **REJECTS** it (finding stands → must be fixed next round). A finding resolved by an
accepted dispute counts as resolved for convergence — same as a fix — so a correct solution can converge *honestly*
(by argument) instead of by test-piling. Crucially the **critic, not the author, has final say** — the author cannot
unilaterally dismiss anything.

This mirrors real review: author pushes back with reasoning; reviewer adjudicates.

## 3. The hard part — making an accepted dispute STICK

A dispute resolves a finding by *argument*, not a code change. So a fresh re-critique of the (unchanged) code will
very likely **re-raise the same finding**. This is the core design risk and where most of the work is:

**Plan: an accepted-dispute LEDGER + prompt-priming.**
- Keep a ledger of accepted disputes for the item: `{finding_key, rationale, adjudicator_verdict}`. Reuse
  `merge_decorrelated`'s near-duplicate keying (first ~80 chars of the explanation, casefolded) as `finding_key`.
- On each subsequent re-critique: (a) **filter** out findings whose key matches a ledgered accepted-dispute
  (deterministic — they don't re-count), AND (b) **prime the critique prompt** with "these concerns were reviewed and
  dismissed with the following rationale; do not re-raise them unless you have a NEW, materially different reason:
  …". (a) guarantees they don't churn the loop; (b) reduces wasted critic effort and lets a critic escalate with a
  genuinely new angle.
- A re-raise that survives the filter (materially different) is treated as a fresh finding — the author can fix or
  dispute again, but the ledger prevents infinite argue-loops on the *same* point.

## 4. Safety guards (anti-abuse)

The failure mode is the author disputing everything to force convergence. Guards:
1. **Critic adjudicates every dispute** — author can't self-approve.
2. **Severity floor on dispute-eligibility:** HIGH/CRITICAL findings are **NOT** dispute-resolvable — they must be
   fixed (or the critic must explicitly downgrade them first). Only medium/low findings are dispute-eligible. Keeps
   real bugs from being argued away. (This is the overlap with #3 — see §7.)
3. **Full audit trail:** every dispute + rationale + adjudication is written into the `findings_log` (from #1) and the
   two-axis score (#5) so a human reviewing a bail_human sees exactly what was argued away and by whose ruling.
4. **Dispute budget:** optionally cap disputes-per-round or require ≥N critics to accept a dispute (decorrelated
   adjudication) — a knob, default conservative.

## 5. Code touch points (all orchestrator-side; no runner rebuild)

- **`app.py:_ov_review`** — `anchor_and_fix` becomes `anchor_fix_or_dispute`: prompt the 27B to return a STRUCTURED
  per-finding decision `{action: "fix"|"dispute", rationale}`; run the current fix path for "fix", collect rationales
  for "dispute". Add an `_adjudicate(disputes)` that calls the critic pool with an adjudication prompt → accepted/
  rejected. Maintain the accepted-dispute ledger across rounds (in the closure, like `holder`).
- **`adversary.py`** — the re-critique path (`_recritique`) gains a ledger filter (drop re-raised accepted-disputes);
  possibly a small pure helper `filter_ledgered(findings, ledger)` (unit-testable). The dispute/adjudicate model calls
  stay INJECTED so `run_ladder` remains model-free/testable.
- **`deliberation.py`** — likely a new `ADJUDICATION_SYSTEM` prompt (sibling to `CRITIQUE_SYSTEM`) + a parser for the
  structured author decision. No `converge()` change needed — disputes feed the existing finding-count loop.
- **config.py** — `ADVERSARY_DISPUTE_ENABLED` (default off until proven), severity-eligibility set, dispute budget.
- **Tests** — author-decision parsing; adjudication accept/reject; ledger filtering (a re-raised accepted-dispute is
  dropped, a materially-new one survives); severity guard (a high finding can't be disputed away); end-to-end ladder
  round where a dispute converges a run that would otherwise churn.

## 6. Open decisions for the owner

1. **Adjudicator = the same critic pool (recommended, simpler) or a separate model** (more decorrelation, more cost)?
2. **Author decides fix-vs-dispute per finding (recommended, most engineer-like) or a heuristic pre-filter** (crude)?
3. **Severity floor:** confirm HIGH/CRITICAL are non-disputable (fix-only). Where's the line — medium+low disputable,
   or low-only?
4. **Dispute budget / quorum:** single-critic adjudication, or require ≥2 critics to accept a dispute?
5. **Default on or off:** ship behind `ADVERSARY_DISPUTE_ENABLED=false` and enable after a shadow night, or on from
   the start?

## 7. Sequencing vs #3

#4 and #3 attack the SAME pain (critic noise churning a correct solution) from opposite sides — #4 lets the author
*argue a nit away* (precision via reasoning, critic-adjudicated); #3 *filters what counts* (precision via a severity/
quorum gate, no reasoning). #4 is safer (nothing is dropped without a critic ruling + full audit) and subsumes part of
#3 via the severity floor in §4.2. **Recommendation: build #4, run a shadow night, measure whether critic-noise still
bails correct solutions, THEN decide if #3's blunter filter is still needed.** Likely #4 + keep-best (#2) together make
#3 unnecessary — which is what the owner's "not sure about #3" instinct already pointed at.
