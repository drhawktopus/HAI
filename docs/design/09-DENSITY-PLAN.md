# 09 — DENSITY PLAN

## Design Doctrine: Density Optimization Strategy

The optimization strategy prioritizes reducing HAI wall-clock time while increasing the volume of correct fixes and features per run. Optimizations must be evidence-driven, targeting measured bottlenecks without degrading review quality.

## Bottleneck Analysis

Performance constraints are governed by the following design facts regarding the author tool-loop (27B model):

1.  **Primary Constraint**: The drive phase is the single largest time sink, averaging ~25–37 minutes per item. This includes the initial drive and subsequent fix rounds.
2.  **Secondary Constraint**: Critique operations average ~4 minutes. A single-critic path saves approximately 5 minutes within a ~60-minute item lifecycle, which is considered noise-level impact.
3.  **Optimization Levers**: Efficiency gains must target:
    *   Reduction of phantom-driven fix churn (review side).
    *   Acceleration of the drive phase (drive side).

## Execution Phases

*   **Part A**: Review-side optimizations (largely complete regarding phantom churn reduction).
*   **Part B**: Drive-side optimizations (addressing the missing half of the density equation).

## Review overhead and throughput constraints

The review process imposes a fixed overhead per item, independent of diff size. A single item requires multiple calls to the Mothership model (27–30B parameter variants) for initial critique, convergence, materiality assessment, and dispute resolution. This "review tax" applies equally to small changes (e.g., 5 lines) and large changes (e.g., 500 lines).

- **Review cost:** Approximately 15 minutes per item.
- **Throughput impact:** In a five-leaf commission, fixed review overhead accounts for approximately 75 minutes of total wall-clock time. Leaf count is a primary lever for density optimization.
- **Churn mitigation:** The evidence anchor pattern (combined with context packets and keep-green protocols) resolves false positives by defusing phantom critiques and high-severity noise. Fix rounds are now restricted to genuine findings only.
- **Timing data:** Historical wall-clock times for individual items ranged from 14.6 minutes (small leaf) to 69.7 minutes (integration leaf). A retry scenario recorded 58.5 minutes.

### D0 — Instrument before optimizing
Per-stage wall-clock timings are recorded in the run trail and morning report. Stages include:
*   plan
*   drive
*   verify
*   initial critique
*   each fix round
*   re-critique
*   disputes

Timings are mechanical and test-verifiable.

**Run-start staleness check**
The system refuses to start when the workspace baseline does not match live state, issuing a clear error message. It does not auto-sync at run start because the workspace is often legitimately ahead of live state. This complements the post-deploy `sync-workspace.sh`.

### D1 — Risk-tiered review
A **mechanical** risk score per item diff is calculated without a model call. Inputs:
- Lines changed
- Files touched
- Core-vs-test split
- Whether changed symbols graze the INVARIANTS surface (auth/identity/approval/secrets regions always map to max risk)

Tiers:
- **low** → single-critic pass + evidence anchor + materiality only (no trio, no convergence rounds)
- **medium** → trio, single round
- **high** → full ladder; `force_adversarial` override remains available

Design intent: reduce fixed overhead for high-volume small items.
Conformance check: minutes-per-done-item must drop materially with no rise in defect escapes (defects found post-run).

#### D1 — Adversary Risk Tiering (gated OFF by default)

**Status:** `ADVERSARY_RISK_TIERING=false` is the default. The system is production-ready but runs in A/B mode until explicitly enabled.

**Design Doctrine:**
Risk tiering applies a mechanical, zero-model-cost score to every item based on diff size and metadata. It selects the depth of the adversary ladder without altering critic prompts, evidence anchors, disputes, materiality logic, or the deploy gate. The full ladder remains the default for non-trivial items.

**Component: `review_risk.py`**
*   **Function:** Pure module mirroring `review_context.py` style. Contains 16 unit tests.
*   **Interface:** `score(item, diff) -> (tier, reasons)` where tier is `"low"`, `"medium"`, or `"high"` and reasons is `list[str]`.
*   **Inputs:**
    *   Changed files and added/removed line counts (reusing `review_context.changed_files` and a line counter).
    *   Core-vs-test split: diffs containing only test files are inherently lower risk.
    *   Invariants-surface grep: If any changed file or symbol touches auth, identity, approval, secrets, or deploy regions defined in `see [[HAI-INVARIANTS]]`, the tier is forced to **high** regardless of size. This is a non-negotiable floor.
    *   Backlog override: The item's declared `risk:` field in `backlog.yaml` can pin a tier high but never lower than the computed value.

**Tier Definitions & Ladder Behavior:**
*   **Low:** Tiny diff, no core logic, no invariant surface. Runs one decorrelated critic + evidence anchor + materiality check. Zero fix rounds budgeted beyond the first.
*   **Medium:** Normal change. Runs the full trio of critics, a single critique round, and up to N fix rounds (excluding the long re-critique tail).
*   **High:** Large diff, core logic touch, invariant surface touch, or `force_adversarial` flag. Runs the full ladder exactly as defined in the baseline.

**Wiring & Configuration:**
*   **Call Site:** `app._ov_review` calls `review_risk.score(item, diff0)` after building `diff0`. It passes the resulting tier-specific arguments to `adversary.run_ladder(...)` and records `reasons` in `trail["risk"]` for auditability.
*   **Gate:** Controlled by `config.ADVERSARY_RISK_TIERING`. Default is OFF to allow A/B comparison against baseline. Enable with `ADVERSARY_RISK_TIERING=true`.
*   **Tunable Thresholds (Defaults):**
    *   Low: ≤15 changed lines AND ≤2 files.
    *   High: ≥120 lines OR ≥5 files OR invariant surface touch.
    *   Medium: All other cases.

**Guardrails & Escalation:**
*   **Escalate-on-Signal:** Enabled by default (`config.ADVERSARY_RISK_ESCALATE=true`). If a low/medium tier run’s initial critique surfaces a MATERIAL critical/high severity after anchoring, `select_adversary` re-picks the richest affordable tier. The fix and every subsequent re-critique run on the fuller pool. `trail["escalated"]` records the transition (from/to/trigger). This ensures low-tier items never resolve real defects with fewer eyes than a high-risk item.
*   **Invariant Floor:** Low/medium tiers may skip breadth of critique but never skip the evidence anchor, invariants injection, tamper/authorized-test check, or acceptance-delta guard.
*   **Force Adversarial:** Items marked `force_adversarial` always receive the full ladder upfront.

**Validation:**
Compare minutes-per-item and defect-escape rate between baseline (tiering OFF) and tiered runs. Defect-escape rate must not rise. Mutation testing (D3) serves as the long-term correctness backstop.

### D2 — Leaf-size calibration
`decompose.py` PLAN_SYSTEM enforces rule (6) SIZE CALIBRATION: every leaf incurs a fixed review cost. To minimize total overhead, the system merges any leaf that would be a lone tiny helper or contain under ~20 changed lines into its sibling node. The goal is the fewest substantial-but-independently-verifiable leaves, avoiding long lists of trivial ones.

This rule takes effect on the next commission decomposition.

## Pages in this document

- **Mutation testing design** — see [[Mutation-testing-design]]
- **Drive Capability Design and Status** — see [[Drive-Capability-Design-and-Status]]
