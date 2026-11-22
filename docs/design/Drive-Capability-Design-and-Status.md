# Drive Capability Design and Status

The coding tool loop has **no turn or search count** (since 2026-10-05, doctrine 13a; the old 50-turn cut ended HAI jobs minutes into a 4-hour budget). It ends when the model answers; after `config.CODING_IDLE_TURNS` (3) consecutive turns whose calls all repeat a call already made on an unchanged workspace (a successful `coding_patch`/`create`/`file`, checkpoint create/restore or task start/finish/resume/abandon is a change), with one "NO NEW WORK" note at the first such turn; or at the job's own deadline, carried by `ollama_client.call_context(loop_deadline=...)` (Absurd: its chosen budget; decomposed leaves: the run's budget; overnight drive: remaining run time minus `OVERNIGHT_LADDER_RESERVE_SECONDS`; think-hard tightens it to its EffortState budget via `tighten_loop_deadline`, refused outside a call_context so no pooled thread inherits one), with one "FINAL TURNS" note when the deadline is under three of the run's own average turns away. Telemetry: `nudges_idle`, `nudges_deadline`, `ended` (`idle`/`deadline`); records before 2026-10-05 carry `nudges_soft`/`nudges_hard`. **A JOB's loop (`call_context(job=True)`: a Background turn, think-hard, Absurd, self-engineering) ends the same way whatever tools it was offered** (since 2026-10-31, commit `90efe2f`). That day a Background research turn that Spock overflowed to BigHonker was offered no `coding_*` tools, got `TOOL_LOOP_MAX` (5) steps, and ended after 6 minutes of a 4-hour budget; the same brief on Mothership had run 44 steps. Only plain, non-job chat loops keep `config.TOOL_LOOP_MAX`.

The `_ov_drive` prompt uses a PLAN-FIRST structure. It requires a numbered plan and the first tool call in the same turn to prevent plan-only replies from stalling continuation passes.

The scope-gate calibration is deployed with the following rules:
*   `review_context.strip_diff_comments`: String literals count as wiring; data-key wiring IS wiring.
*   `_missed_scope` (B1) and `_omission_exempt` (B2): Comment-satisfies-gate logic is disabled.
*   B1.1 example-clause fix: "e.g./such as" clauses no longer mint demanded targets.

The test-file-stem extraction class is closed; the corpus run identified zero benign scope-notes, resolving the last known scope-gate false-positive class.

## Correctness Gate and Future Work

D3 mutation testing serves as the drive-correctness gate. It is currently parked in PROJECT-BACKLOG "NOTED FOR LATER" #1, scoped as an architectural-readiness correctness floor. High-tier critic count adjustments are also parked until run data indicates a need for revision.

## Campaign Status

The density campaign is complete. Phase D thesis is proven: the target of ≤45 minutes per truly-correct drive is met. Remaining work (#12) moves to Phase E solo rehearsal. See `Runbook\` §HAI Fixture Test Cycle for operator procedures.

## Phase E — UNATTENDED-READINESS CLOSEOUT
The final phase of the Density Plan defines the state where the operator runs the factory unsupervised. The following conditions constitute readiness:

1. **Persistent auto-DM**: Wired via untracked override compose; test DM verified.
2. **Run-status display**: Status seeds the real ID before the thread starts. The front-end does not render the `(starting)` string. The `/start` RESPONSE key remains `bl-start-response-runid`, armed in the pool.
3. **Open items to resolve**:
   - Flaky `test_query_lifecycle`.
   - Dry-run `no_progress_limit` quirk.
   - Tautulli rotation (owner: the owner).
4. **Final rehearsal**: the owner arms, schedules, and harvests a run solo.

see [[09-DENSITY-PLAN]] §Order rationale for phase sequencing logic.

## The Review-Margin Invariant

**Definition:** For every item, the Claude-token cost of the review step (harvest or flagged-queue adjudication) must be strictly less than the cost of Claude authoring the item directly. If review cost ≥ authorship cost, the factory is negative-value. This bounds the CLAUDE axis analogously to how min/truly-correct bounds the machine axis.

**Enforcement and Policy:**

1.  **Default Action for Flawed Items:** Re-queue to HAI with sharpened acceptance criteria.
2.  **Exception (Hand-Finishing):** Permitted only when:
    *   The fix is trivial (≤10 minutes), AND
    *   HAI has already exhausted one fix-round or re-queue attempt.
3.  **Gate Function:** The Phase-B gate serves as the enforcement mechanism for this invariant. It forces HAI to either finish (fix round) or bail cleanly, ensuring human decisions remain cheap re-queues rather than costly builds.

**Reporting Requirements:**

*   Morning reports must be decision-sufficient. Accept/reject/re-queue determinations must be decidable from the report alone without workspace spelunking.
*   Required evidence in report: `findings_outstanding` quotes, `scope_missed`, hollow counter, diffstat, and tier.
*   **Defect Condition:** Any item requiring workspace spelunking to decide is a Report Defect. File a backlog item for the missing evidence.

**Measurement and Success Criteria:**

*   Track rough Claude-effort per harvested item versus authorship estimate per run.
*   Phase C and E success criteria include maintaining this ratio.
*   Reference benchmark: Phase C, Run #3 defined success as reviews decidable from report + ≤10 minutes of diff eyeballing per flagged item.
