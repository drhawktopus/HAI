# Mutation testing design

## Overview

Mutation testing validates that item tests catch regressions. Tiered review skips breadth of critique on low-risk diffs; this is safe only if the item's own tests actually catch regressions. Mutation testing proves the tests bite by mutating changed code: if the test suite stays green, the tests do not cover the change. This mechanism enables trust in light review tiers.

## Execution constraints

- Deterministic and diff-scoped.
- No model calls; no model-generated code.
- Mutates the item's own already-verified source with a fixed catalog of mechanical transforms.
- Re-runs the item's test profile against a transiently-mutated copy in the sandbox.
- Bounded time execution.
- Never touches the real workspace commit.

## Module `mutation.py`

### Generator: `mutants(source_text, changed_line_ranges)`

**Signature:** `list[Mutant]`
**Property:** Pure function.
**Behavior:** For each changed function or region, applies a fixed catalog. One mutation per Mutant.

**Mutation catalog:**
- Comparison flips: `<`↔`>`, `<=`↔`>=`, `==`↔`!=`
- Boolean/Logical: `and`↔`or`
- Arithmetic: `+`↔`-`
- Literals: `True`↔`False`
- Return value: Drop return value to `return None`
- Guards: Remove guard (`if cond:` → `if True:`)
- Off-by-one on integer literals

**Mutant structure:** `(file, line, original, mutated, kind)`

**Sampling:** Cap mutant count at `MUTATION_MAX_PER_ITEM` (e.g., 12). Take a representative sample; do not perform exhaustive mutation.

### Runner: Coding-runner endpoint

**Endpoint:** `POST /mutation/run`
**Behavior:** For each Mutant:
1. Apply mutation to a scratch copy.
2. Run the item's test profile with a timeout.
3. Revert changes.
4. Return `killed: bool` (true if suite went red) and the failing test output, if any.

**Environment:** Sandbox-only. No network access. Bounded time.

## Gate wiring in `overnight_worker`

**Trigger:** After verifier is green and review passes.
**Condition:** `MUTATION_TESTING` is enabled.

**Process:**
1. Generate mutants for the item's changed functions.
2. Run mutations.
3. Compute **mutation score** = killed / total.

**Outcomes:**
- **Pass:** Mutation score ≥ `MUTATION_MIN_SCORE` (e.g., 0.7). Item proceeds to DONE.
- **Fail:** Mutation score < `MUTATION_MIN_SCORE`. Item tests are weak. Route through `bail_human` channel with reason `"weak_tests_mutation_survived"` and list surviving mutants.

**Failure handling:** Fails open. A mutation-run error never bails a good item. Kill switch: `MUTATION_TESTING`.

## Interaction with review tiers

- **Low-tier items:** Must pass mutation testing to reach DONE status.
- **Medium/High-tier items:** Low mutation score is advisory.

This policy ensures the fast path is safe by construction: less review is allowed precisely because the tests are proven to bite.

## Tests

**File:** `test_mutation.py`

**Coverage:**
1. Pure `mutants()` catalog: Each transform produces the expected mutant; capped count; applies only to changed regions.
2. Wiring test for `work_item` (stubbed runner):
   - All mutants killed → DONE.
   - Survivor below threshold → bail_human with survivor listed.
   - Kill switch enabled → skip mutation step.

## Cost and timing

- Runs after the verifier is already green; executes only on known-good code.
- Scope: Only changed functions.
- Limits: Capped mutant count, per-mutant timeout.
- Resource usage: A handful of fast test-suite runs per item. No model minutes consumed.

## PART B — DRIVE OPTIMIZATION AND SAFETY CONSTRAINTS

The review phases (Part A: D0–D3) operate near MAX speed. The remaining review work (D3) serves as a safety counterweight, not a speed lever. Analysis confirms the drive stage (the 27B author tool-loop) is the primary latency factor (~25–37 min/item), which tiering does not address. Consequently, density optimization targets the drive.

Drive optimization differs from review optimization. Review changes are safe because critics only judge; they do not introduce bugs. The drive writes code. Accelerating the drive (via fewer turns or reduced deliberation) trades directly against correctness. Therefore:

1. Every drive change is measured against DEFECT-ESCAPE, not just speed.
2. D3 (mutation testing) serves as the CORRECTNESS GATE for the drive, enabling trust in faster configurations.

---

### Dr0 — Author loop telemetry instrumentation

The author loop instruments capture per-drive performance metrics during execution. The `ollama_client.chat` module records:

- **turns**: Tool-loop iteration count.
- **infer_s**: Model inference time.
- **tool_s**: Tool-execution time.
- **tool-call distribution**: Breakdown by operation type (reads, searches, patches, test-runs).

Data is emitted via a `DRIVE_TELE …` log line per coding loop, adhering to the existing SHORTANS observability pattern. A copy of this data is also placed in an optional caller `telemetry` dict for downstream trail integration. This instrumentation provides visibility into time allocation (inference vs. exploration vs. test-running) analogous to review stage timings.

Use `DRIVE_TELE` logs to analyze latency distribution across loop iterations.

### Dr1 — Author seed context design
The author drive prompt front-loads relevant file content and call sites to reduce discovery turns. This behavior is gated by `SELFENG_DRIVE_CONTEXT` (default ON) and bounded by `SELFENG_DRIVE_CONTEXT_MAX_CHARS`. The implementation is mechanical, incurring no model cost beyond the initial grep.

**Workflow:**
1. `review_context.extract_seed_symbols(objective+acceptance)` extracts code symbols named in the task (`*.py` files, `module.func` dotted refs, `snake_case` identifiers).
2. `review_context.build_seed_packet` greps the workspace via `coding_client.search/read`.
3. Results are injected into `app._drive_seed_context`.

**Conformance:**
The system is best-effort; a miss leaves the author to explore as in previous designs. Conformance is measured by comparing `DRIVE_TELE` logs against Dr0 baselines: successful implementation shows fewer turns and significantly fewer `coding_files_read`/`coding_search` calls per item.

### D3 — Mutation testing — the correctness gate for the drive
Mutation testing validates test coverage for changed functions. The process mutates the modified code and asserts that the test suite fails, proving the tests detect the change. This mechanism prevents reduced scope from compromising correctness. Implementation occurs as part of the DRIVE work.

## Constraint: Sync and Review Scope

### Sync Policy
Deploy-time sync is the sole synchronization method. Use `/opt/arrstack/sync-workspace.sh`. Run-start auto-sync is excluded from the design.

### Review Invariants
Density derives from skipping redundant review of low-risk diffs. It does not derive from weakening review standards. The following invariants are immutable:
- Evidence anchor
- Invariants injection
- Tamper flags
- Human deploy gate
- Claude opt-in doctrine

### Backlog Automation
The backlog is a pure human declaration. Automatic pulling of work from `backlog.yaml` (the owner’s general PROJECT-BACKLOG) is excluded. The deferral function in the backlog serves only to manage HAI user requests during peak energy pricing.

## Measurement: Density Scoreboard Metrics
1. Minutes per TRULY-CORRECT item (primary) — see Part C for correction rationale.
2. Review overhead per item (initial critique → verdict).
3. Fix/churn rounds per item.
4. Defect-escape rate post-run (guardrail metric; must not rise).

## Density Measurement and Defect Analysis

### Metric Definitions
- **Machine-scored density**: Time per item marked "done" by automated verification.
- **Honest density**: Time per item confirmed truly correct, accounting for integration gaps.

### Observed Impact (Old vs New Code)
- Machine-scored density improved from 56.9 to 39.4 min/done (+31%).
- Honest density improved from 75.8 to 65.7 min/truly-correct (+13%).

### Integration-Hollow Defects
Items can satisfy unit tests while skipping required application wiring (e.g., `app.py`), resulting in false positives.
- **Mechanism**: Verifier scopes are too narrow, marking items green despite missing integration logic. Critics cannot quote diff evidence for absence defects, causing the anchoring gate to downgrade findings to advisory status.
- **Impact**: Scoreboard reports `correct: 5, incorrect: 0` when actual correctness is lower. This gap defines the "integration-hollow" finding category.

### Review Process Efficiency
- Pair critique cost reduced from ~15 min to ~2.5 min for low-tier items; review is no longer the bottleneck.
- Drive waste modes (search-storms of 40–50 calls) are mitigated by a graduated hard cap (25) and seed context (healthy drives require 2–9 searches).

### Lifecycle Handling
- Escalation, keep-green, and flag-for-human states compose correctly in live operation. 
- Example sequence: Trio found 6 issues → fix broke verifier → keep-green restored status + flagged for human review.

### Residual Costs
- Lifecycle-class items account for ~114 min (58% of run), comprising real work and fix rounds, excluding search storms.
- One escape data point recorded: Energy overnight-wrap label bug passed pair critique but was caught by the 9pm full pool.

## Phase A — Harvest outcomes and current state

Phase A artifacts are deployed. The following components constitute the current baseline:

* **escalation-note**: New-run version with spec-correct format `esc:<from>->@<to>`.
* **lifecycle-clock**: Version `f05075e` (9pm HELD). Provides richer seam and clocks `created_at`. Note: the new run’s instance was flagged_for_human; this is a defect to fix per design doctrine that every Fellowship node carries the persist prop. See <[[09-DENSITY-PLAN]]> § Phase B for evidence requirements.
* **energy-label**: Version `ad97b00` (9pm HELD). Handles overnight wrap `/tomorrow`.
* **parse_test_count**: New-run pure helper.
* **_ov_verify**: Attaches `"tests"` to item records. Parses FULL runner output. Parsing the summary line alone yields None because the summary is only the last line.
* **_ov_drive**: Collects telemetry via `Q.drive_telemetry` and `_merge_drive_tele` per-pass aggregation. Returns `{"telemetry": ...}` mapped to `timings["drive_tele"]` on the item record. Note: `chat()` OVERWRITES its telemetry dict per call, necessitating this collection pattern.

### Conformance and Metrics

* **Determinism**: The lifecycle suite runs deterministic in the gate where it previously flaked. This is a design fact; any regression is a defect.
* **Health**: In-container health endpoint returns 200.
* **Workspace Baseline**: `f511947`.
* **API Contracts**: Documented in [[HAI-Orchestrator-Internals]] §signatures.

### Morning Report and Density Scoreboard

From the current run onward, the morning report includes:

1. Per-item drive telemetry
2. Suite counts

Inputs 1–3 for the density scoreboard are now machine-collected.

### Backlog Status

In `backlog.yaml`:

* All 5 items are marked un-approved with DONE comments.
* The 2 hollow-half items (`_ov_verify`, `_ov_drive`) are annotated as Phase-B evidence.

## Acceptance-Coverage Gate (Phase B)

**Design fact:** The overnight worker enforces scope coverage against negation-stripped specifications before marking work DONE. A work item fails if the diff does not touch symbols demanded by the spec.

### Scope Target Extraction
`review_context.extract_scope_targets(text)` generates targets from the objective and acceptance text by:
1. Stripping negated clauses (containing "not", "never", "without", "avoid") and steady-state markers ("stays green", "still pass", "existing", "remain", "keep").
2. Splitting remaining clauses on `[;\n]` or period-followed-by-whitespace.
3. Extracting symbols via `extract_seed_symbols`, preserving `app.py` and `module.func` references.

### Scope Verification & Retry Logic
In `overnight_worker.work_item` DONE path (after empty-diff guard, before acceptance-delta guard), `_missed_scope(item, diff)` compares targets against the cumulative diff:
- **Coverage definition:** A named `*.py` file is covered if its BASENAME appears in touched files. Any other symbol is covered if it appears ANYWHERE in the diff text (dotted refs count as bare attributes).
- **First miss:** Forces ONE feedback-driven fix round. The worker re-drives with an item copy whose objective appends: `"SCOPE GAP: names X, Y but your diff never touches them"`. This uses the `drive_item` channel (`_ov_drive` reads `item["objective"]`); no `app.py` plumbing is used.
- **Persistent miss:** If the gap survives the retry, the worker calls `bail_human` with reason `acceptance_scope_missed`. Work is reverted, and a high finding is attached.
- **Fail-open conditions:** No failure occurs for `get_diff` errors, fetch errors, or symbol-free specs.

### Anchoring Exemption for Omissions
`apply_evidence_anchor(...)` exempts unanchored crit/high findings from downgrade if:
1. The finding’s text names a symbol present in `spec_symbols` (passed to `run_ladder` via `app.py` calling `extract_scope_targets(objective+acceptance)`).
2. The symbol is verifiably absent from `spec_corpus` (the BARE diff, NOT the anchor_corpus-widened packet). A call-site line in the review packet must not make the symbol appear present.

Exempted findings keep claimed severity, are tagged `omission: true`, and are persisted in `findings_log`. Fabricated omissions (symbol not in spec or present in diff) downgrade exactly as before.

### Scoreboard Reporting
`overnight.HOLLOW_REASONS` includes `"no_changes_produced"`, `"hollow_no_tests"`, and `"acceptance_scope_missed"`. 
`score_run` adds `coding.hollow` (a sub-count of incorrect, distinct from honest reds) to track the "LYING-green" class. Reports show `"coding N/M verified (K hollow)"` when K>0.
