# Home AI Coding Agent Plan

Last updated: 2026-06-15. **⚠ AGE WARNING — partially superseded, verified against live 2026-08-14
(session 4, #12 Phase G).** The body below is the 2026-06-15 state. Two things moved since and are
corrected inline where they appear: **(a) `orchestrator-real` IS registered and is now the DEFAULT
workspace** (`promote.py:117`), so the "use `orchestrator-pilot` unless read-only" guidance and the
open decision in "Remaining Decisions" are both closed; **(b) the self-engineering phases advanced to
6** (see [[06-SELF-ENGINEERING-AGENT-PLAN]]). Verify any other load-bearing value against live
config before repeating it — do not quote this file as current without checking.

## Implementation Status - 2026-06-15

**C1, safe C2, and C3 are deployed and verified. A sanitized real-code pilot is live.**

**Do not describe the current coding runner as read-only.** C1's read-only stage is complete and
was superseded by safe C2 for the disposable `sample` workspace. Mothership can patch and create
files in `sample`; all other repositories remain unavailable for writes.

- Internal-only, token-authenticated `coding-runner` service is live on Sundae.
- Registered writable Git workspaces are `sample` and the sanitized real-code `orchestrator-pilot`.
- Mothership has 13 coding tools, including project manifests, guarded file management, local Git
  checkpoints/restore, isolated worktrees, durable pause/cancel/resume, and independent review.
- Ordinary 8B workers do not receive coding tools. Coding loops have a separate 20-step budget.
- Runner suite: 27 tests pass; one Windows symlink-creation test is skipped.
- Two autonomous live tasks passed, including dirty-worktree preservation, new-file creation,
  durable planning, complete diff review, and four passing tests.
- Live probes rejected traversal, protected edits, stale hashes, unknown commands, and
  unauthenticated access.
- `orchestrator-pilot` is a sanitized Git-backed copy of real orchestrator source. It supports
  ordinary isolated coding work but cannot promote or deploy itself to production.

Details and rollback paths: `coding-runner/README.md`.

The sanitized real-code `orchestrator-pilot` has write access; canonical production source does not.
Arbitrary shell, packages, remote Git operations, remote/deploy operations, credentials, and
Claude/Codex execution remain unavailable. Local checkpoint commits are automatic restore points
inside the sandbox only. Registered repositories must have no Git remotes, and runner-spawned
processes have Git transport disabled.

### Supervisor Handoff Contract — the packet ("leaflet") a supervising session hands HAI (current 2026-10-05)

Codex or Claude directs Home AI with a bounded `SUPERVISED LOCAL CODING JOB` packet; HAI uses only its constrained
coding tools. **The VEHICLE that carries the packet is decided per [[RUNBOOK-HAI-Coding-Escalations]] step 3** (the
Absurd vehicle is retired for coding since 2026-10-05; its replacement is designed in [[HAI-Agent-Surface-Design]] §
"How the agent does WORK" and not built — until it is, the owner routes coding jobs).

**Workspace — the copy HAI edits must equal what runs, checked by hash BEFORE submitting** (a stale copy makes HAI
build on old code, and porting its diff back silently reverts the newer fixes): `orchestrator-real` for HAI's own
source (synced by `/opt/arrstack/sync-workspace.sh`); `sample` for the #34 A1 harness (synced by copying the live
`hard-bench\a1` files in and committing; 2026-10-05 it was a protocol version behind — `81198b3`/`16b7ec1` fixed it).

```text
SUPERVISED LOCAL CODING JOB
Supervisor: Codex | Claude
Workspace: orchestrator-real | sample        (hash-checked against live before submitting)
Mode: isolated edit-test-review
Objective: ...
Acceptance criteria (each one checkable by a test or a command, never by HAI's report):
- ...
Pinned design decisions: ...                 (precision pays: unpinned, the draft improvises)
Constraints:
- local-only Git; no GitHub/remotes
- no packages, deployment, credentials, arbitrary shell, or canonical production writes
Stop and report if:
- a required step is outside policy
Final report:
- task id, workspace, files changed, the EXACT test command and its summary line (Ran N, OK/FAILED),
  the names of any failing tests, checkpoint/review, human follow-up
```

**Extra items for any packet that builds a HARNESS, GRADER, REVIEWER or BENCHMARK** (the 2026-10-04/05 A1 lessons,
CLAUDE.md doctrine rows "verify a BENCHMARK HARNESS or a REVIEWER"; [[34-A1-Agentic-Build-Family-Spec]]):
1. **Leak check** — list every tool the model gets and what each RETURNS; nothing it returns may touch the answer key
   (v1's `run_tests` showed the hidden tests: the 3.8's 33/34 was a full-feedback number).
2. **Sandbag check** — when the packet claims a published protocol, the prompts are diffed against THAT project's
   source before the first run (v2 sent half of Aider's brief and told the model a false thing about its tests).
3. **Controls** — reference solutions pass and stubs fail on EVERY task (positive and negative), and a changed mode is
   proven message-for-message identical to the old one where it should be (the v2.1 side-by-side).
4. **Reviewer input** — a reviewer gets the ARTIFACT (the diff and the full test output), and the packet names exactly
   what it receives (the BLIND REVIEWER class: Absurd's critic saw the first 12 trail steps, never an edit).
5. **Transcripts keep both sides** — the model's turns AND what each tool returned, so a result can be audited without
   replaying it (A1's transcripts store only the model's side; a 2026-10-05 diagnosis needed a replay script).
6. **Report honesty** — the session checks the report against the trail and reruns the tests itself: HAI has reported
   passing tests in a file that could not import, and a baseline count from its CHANGED tree (journal 2026-10-04).
   Predict the test count before reading the result.

### C3 Review Status - Deployed 2026-06-15

The first C3 increment is live:

- `coding_review` builds an evidence-only packet from task metadata, Git status/diff, and optional
  allow-listed test output.
- The 27B model remains the lead and owns every edit. Review uses a fresh-context second pass of
  `CODING_REVIEW_MODEL`, which defaults to `HEAVY_MODEL` = `qwen3.8:27b` (config.py, read 2026-10-05) —
  the SAME model as the lead, so the review is not decorrelated (the design wants a different model:
  [[HAI-Agent-Surface-Design]] § "How the agent does WORK"; whether a peer helps is A1-review). Pinned to
  Mothership; Softserve is not eligible for coding review.
- Lead task notes and conversational reasoning are omitted from reviewer packets.
- Each durable task gets at most one independent review pass and one bounded lead repair cycle.
- Reviewer output is schema-validated JSON with verdict, severity, file, line, explanation, and
  suggested test.
- A four-case seeded review benchmark compared `qwen3.6:27b` and `qwen3:14b` on Mothership and
  measured both bug detection and unnecessary findings on correct code. The 27B pass caught all
  seeded defects across the diagnostic runs and left correct code alone; 14B repeatedly missed
  severe defects, so it was not selected.
- Final selected-reviewer benchmark: 4/4 on the fresh-context 27B pass in 27 seconds.
- End-to-end task `bef446a98462474badb806bf4ead123b` preserved dirty work, added guarded division
  and two tests, passed 6/6 tests, received an independent pass verdict, and completed durably.
- Runner regression: 13 pass, one Windows symlink test skipped. Orchestrator mode/reviewer suite:
  13 pass.
- Rollbacks:
  `/opt/arrstack/orchestrator/backups/pre-c3-reviewer-20260615T1014Z/` and
  `/opt/arrstack/orchestrator/backups/pre-deliberation-20260615T1026Z/`.

### Deliberation Status - Deployed 2026-06-15

- UI and API expose `Normal` and `Think Really Hard`.
- Think Really Hard runs a bounded 27B first pass, fresh-context critique, and optional refinement.
  Default budget is 600 seconds and three passes.
- Streaming status reports phase changes and elapsed time. Responses include a concise work journal.
- Optional `Deep Trace` returns a compact, artifact-friendly pass/evidence handoff rather than raw
  chain-of-thought.
- Live test completed three passes in 135 seconds; critique found precision errors and the final pass
  corrected them.
- Explicit Claude/Codex requests now require Direct, Normal interaction, Background review, or
  Dress to Impress selection. The external executor remains disabled, so nothing is sent or billed.

## Goal

Give Home AI a coding workflow comparable to Codex or Claude Code:

- inspect an existing repository before acting;
- search and read code efficiently;
- maintain a visible plan for substantial tasks;
- edit files with reviewable patches;
- run formatters, linters, tests, and builds;
- inspect failures and iterate;
- preserve unrelated user changes;
- use Git for status, diffs, and optional checkpoints;
- perform approved remote/deployment work;
- report exactly what changed and what was verified.

The realistic target is comparable workflow and safety, not guaranteed parity with the strongest
cloud model on difficult reasoning. Local models should handle bounded repository work well and
offer an explicit Claude/Codex path when the task exceeds them.

## Recommendation

Keep the existing Home AI orchestrator as the user-facing coordinator, but do not put arbitrary
shell or filesystem access directly into the current container. Add a separate **coding runner**
with narrow APIs, workspace isolation, approvals, audit logs, timeouts, and output limits.

Default model roles:

| Role | Model/host | Responsibility |
|------|------------|----------------|
| Front door / triage | `qwen3:8b`, BigHonker or Softserve | Understand request, choose workspace, gather cheap context, decide whether coding mode is needed |
| Lead coding agent | `qwen3.6:27b`, Mothership | Plan, select tools, implement, diagnose tests, synthesize final answer |
| Repository scout | `qwen3:8b`, other available host | Search files, map architecture, summarize focused evidence |
| Reviewer | Independent second model pass | Review the proposed diff for bugs, regressions, security issues, and missing tests |
| Optional specialist | Coding-focused model selected by benchmark | Patch generation or difficult debugging; run sequentially on Mothership if VRAM prevents concurrency |
| Paid fallback | Claude/Codex, explicit user approval only | Hard tasks that local evaluation shows are beyond the local stack |

The models should exchange structured task packets, not hold an open-ended conversation. A packet
contains the objective, workspace, constraints, relevant files, current plan, tool evidence, diff,
test results, unresolved questions, and requested output. This makes collaboration inspectable and
prevents one model's unsupported claim from silently becoming another model's premise.

## Architecture

```text
User / HA / Web UI
        |
        v
Home AI orchestrator (:8099)
  - profile/history
  - route and model selection
  - task state and status stream
  - approval requests
  - planner/reviewer coordination
        |
        v
Coding runner (separate service)
  - workspace registry
  - canonical path checks
  - read/search/patch APIs
  - sandboxed command execution
  - Git/test/build APIs
  - audit log and artifact capture
        |
        +--> approved local workspaces
        +--> isolated task worktrees/snapshots
        +--> named remote-host adapters (later, gated)
```

The runner should initially operate on explicitly registered repositories only. It must not receive
the entire Softserve filesystem, raw Docker socket, unrestricted SSH keys, or unrestricted network
access.

## Tool Surface

### 1. Workspace and discovery tools

- `workspace_list`: registered projects, paths, language, default branch, allowed operations.
- `workspace_open`: select one workspace and return its policy and basic metadata.
- `file_list`: bounded directory listing with ignore rules.
- `file_read`: line-ranged reads with size limits and binary-file rejection.
- `text_search`: ripgrep-backed search with path/glob/result limits.
- `symbol_search`: optional language-server or tree-sitter symbol lookup.
- `project_manifest`: detect package managers, build files, test commands, and repo instructions.

These are read-only and should be the first milestone.

### 2. Git inspection tools

- `git_status`
- `git_diff`
- `git_log`
- `git_show`
- `git_branch_info`
- `git_untracked`

Git output should be structured where practical. The agent must inspect status before edits and must
never discard unrelated changes.

### 3. Editing tools

- `apply_patch`: the primary edit mechanism; records an exact patch and rejects paths outside the
  selected workspace.
- `file_create`: creates a new text file through the same path and size validation.
- `file_move`: same-workspace rename only, with collision checks.
- `format_files`: invokes a registered formatter on an explicit file list.
- `patch_preview`: returns the pending diff before approval-sensitive changes.

Avoid a general "write arbitrary file contents anywhere" tool. Patch-based edits are easier to
review, retry, and audit.

### 4. Command, test, and build tools

- `command_run`: sandboxed process with argv array, explicit working directory, environment
  allow-list, timeout, output cap, and no shell interpolation by default.
- `test_run`: registered test profiles such as unit, integration, targeted file, or full suite.
- `lint_run`
- `typecheck_run`
- `build_run`
- `process_poll` and `process_stop`: only for runner-owned sessions.
- `artifact_list` / `artifact_read`: inspect generated reports, logs, screenshots, or coverage.

Each workspace should define allowed commands in a checked-in or administrator-managed policy file.
Unknown commands require approval; destructive commands remain denied even after a model asks.

### 5. Task-control tools

- `task_start`: objective, workspace, constraints, risk class, and acceptance criteria.
- `plan_update`: steps with pending/in-progress/completed states.
- `task_note`: durable facts and decisions, separate from conversational memory.
- `task_checkpoint`: capture current diff, test state, and model handoff packet.
- `task_resume`
- `task_finish`: verification summary and remaining risks.

Coding tasks need durable state beyond chat compaction. Store task records under a separate
`State/coding-tasks/` area and reference file hashes/commits rather than relying on prose memory.

### 6. Review and collaboration tools

- `delegate_scout`: ask an 8B worker for a bounded repository investigation; read-only tools only.
- `delegate_implement`: optional coding-specialist pass against a prepared task packet.
- `delegate_review`: independent diff review without exposing the lead model's chain of reasoning.
- `review_findings`: structured severity, file, line, explanation, and suggested test.
- `resolve_finding`: lead agent accepts, fixes, or explains a finding.

Use collaboration when there is real parallel value:

- architecture mapping across separate modules;
- independent review after a meaningful diff;
- test-failure diagnosis in parallel with documentation lookup;
- comparing two implementation approaches before editing.

Do not use multiple models for trivial changes or let them repeatedly debate. Limit each coding task
to one lead, up to two focused workers, and one review pass unless the user asks for more.

### 7. Remote and operational tools, later and gated

- `remote_read`: read a file or service state from a named host.
- `remote_command`: execute only a registered command template on a named host.
- `service_logs`: bounded logs for a registered service.
- `deploy_preview`: show files, destination, backup, build, restart, and rollback plan.
- `deploy_apply`: approval-required deployment.
- `service_restart`: approval-required and service allow-listed.
- `http_request`: allow-listed hosts/methods with secret redaction.

Never expose generic SSH credentials to a model. The runner owns credentials and maps a named action
to a constrained adapter. Production deployment should always create a rollback point first.

### 8. Optional external research

- `docs_search`: current official documentation only for technical dependencies.
- `package_metadata`: versions and compatibility from official registries.
- `web_fetch_allowlisted`: explicit domains and size limits.

Network access should be off by default during command execution. Dependency installation and
internet-enabled tests require approval.

## Approval Policy

| Class | Examples | Default |
|-------|----------|---------|
| Read-only | list/read/search, Git status/diff/log | Automatic |
| Workspace edit | apply patch, create file, formatter | Automatic in a registered task workspace; always show final diff |
| Local execution | known tests/lint/build | Automatic with timeout and resource limits |
| Dependency/network | install package, download, external request | Ask |
| Git history | local checkpoint/task branches | Automatic inside registered sandbox workspaces |
| Remote Git | clone, fetch, pull, push, PR | Disabled by local-only policy; not approval-gated |
| Remote change | copy/deploy/restart/config edit | Ask with preview and rollback |
| Destructive | recursive delete, reset, credential changes, broad service stop | Deny or require a specially defined human action |

Secrets must be redacted from model-visible command output and logs. The current credential tool
returns whole files and is too broad for coding mode; replace it with named secret injection into an
approved command or HTTP request whenever possible.

## Workspace Isolation

Each registered project needs:

```yaml
id: orchestrator
path: /workspaces/orchestrator
read: true
write: true
git: true
network: false
allowed_commands:
  - ["python", "-m", "pytest"]
  - ["python", "-m", "compileall", "."]
  - ["ruff", "check", "."]
protected_paths:
  - ".env"
  - "credentials/"
deploy_profile: sundae-orchestrator
```

Preferred task isolation:

1. Existing dirty repo: work in place, preserve unrelated edits, and record the initial diff.
2. Clean Git repo: optionally create a per-task worktree for larger changes.
3. Non-Git folder: create a snapshot manifest and backup only files the task will modify.

The runner resolves every path and rejects traversal, symlink escapes, alternate data streams, and
paths outside the workspace root.

## Agent Loop

1. **Classify:** conversational answer, read-only code question, coding task, or operational change.
2. **Select workspace:** never infer a writable target from a filename alone.
3. **Inspect:** repo instructions, status, manifests, relevant files, and tests.
4. **Plan:** required for multi-file, risky, remote, or ambiguous work.
5. **Implement:** small patch, then inspect the diff.
6. **Verify:** targeted test first; broader checks based on blast radius.
7. **Review:** independent model pass for meaningful changes.
8. **Repair:** address valid findings and rerun relevant checks.
9. **Approve operations:** deployment, restart, or network access. Remote Git remains unavailable.
10. **Report:** changed files, behavior, tests, deployment state, and residual risk.

Tool calls and results should not consume the ordinary five-call chat loop. Coding mode needs a
separate bounded budget, for example 30-60 tool actions, wall-clock limits, and a maximum number of
repair cycles. Stop conditions must distinguish completion, approval needed, user input needed, and
genuine failure.

## Deliberation and External-Agent Modes

Home AI should expose explicit effort modes instead of making every request either a quick answer or
an open-ended agent run.

### Normal

- Existing conversational and coding behavior.
- One lead pass, targeted verification, and independent review only when risk justifies it.
- Optimized for responsiveness.

### Think Really Hard

A fully local, multi-pass mode for difficult questions and coding work. Default wall-clock budget:
5-10 minutes, configurable per request.

1. Clarify the objective, constraints, and acceptance criteria.
2. First 27B pass develops and executes a solution.
3. Independent critic pass challenges assumptions and searches for defects.
4. Lead performs one or two bounded repair/refinement passes.
5. Verification pass checks evidence, tests, and whether acceptance criteria were actually met.
6. Final synthesis reports the result, verification, disagreements, and residual uncertainty.

The interface should stream useful progress such as:

- "Mapping the repository and constraints"
- "Implementing the first pass"
- "Running targeted tests"
- "Independent review found two issues"
- "Repairing and rechecking"

Do not expose private chain-of-thought, hidden scratch work, or internal token-by-token reasoning.
Show a concise work journal: current phase, tools used, evidence found, plan changes, test results,
review findings, elapsed time, and remaining budget. This is more useful and less misleading than a
raw reasoning transcript.

Limits:

- explicit wall-clock and tool-action budgets;
- at most two repair cycles by default;
- cancellation and graceful partial-result support;
- no expansion of filesystem, command, network, credential, or deployment permissions;
- ask before crossing into paid external execution.

### External Agent Choice

When the user explicitly asks for Claude or Codex, Home AI should ask how they want the external
agent used. Do not silently choose a cost or privacy posture.

| Choice | Behavior |
|--------|----------|
| Direct | Route the original task to Claude/Codex with minimal local preprocessing |
| Normal interaction | Use the external agent as the primary interactive worker; Home AI carries context and presents results |
| Background review | Local 27B leads; Claude/Codex receives a bounded evidence packet and reviews the result |
| Dress to Impress | Local stack performs multiple preparation and review passes, then hands a polished packet to Claude/Codex for final improvement or execution |

The prompt should include estimated local preparation time, whether external billing will occur,
what workspace material will be shared, and whether the external agent will have execution access or
review-only access. Remember the user's preferred choice only if they explicitly ask Home AI to do
so; otherwise ask per external-agent request.

### Dress to Impress

This mode prepares the strongest possible handoff before paid external work:

1. Local scout gathers only relevant repository context and instructions.
2. 27B lead creates a plan or implementation and runs available verification.
3. Independent local reviewer checks the diff, tests, omissions, and policy compliance.
4. Lead repairs valid findings and produces a compact evidence packet.
5. Claude/Codex receives the objective, constraints, relevant files, current diff, tests, review
   findings and resolutions, unresolved questions, and requested role.
6. External output returns through the same diff, test, approval, and reporting controls.

For coding, the default external role should be final reviewer/improver, not an unrestricted second
executor. The user can explicitly choose direct external execution. A polished handoff must preserve
uncertainty and failed checks rather than presenting incomplete local work as finished.

### Orchestration State

Long-running modes need durable pass-level state:

```json
{
  "mode": "think_hard",
  "phase": "independent_review",
  "started_at": "...",
  "deadline_at": "...",
  "passes_completed": 2,
  "repair_cycles_used": 0,
  "current_summary": "...",
  "evidence": [],
  "approval_needed": null,
  "external_role": null
}
```

Add status events for phase start/completion, tests, review findings, approvals, external handoff,
timeout, cancellation, and final synthesis. Store concise summaries and artifacts, never private
model reasoning.

## Rollout

### Phase C1 - Read-only repository assistant

Status: **COMPLETED, then superseded by C2 write access for `sample` only.**

Build the runner, workspace registry, path validation, audit logging, and read/search/Git-inspection
tools. Register one low-risk test repository. Home AI can explain code and propose a patch but cannot
modify files.

Exit criteria:

- no path escape in adversarial tests;
- accurate repo map and file citations;
- handles dirty worktrees without suggesting destructive cleanup;
- output truncation and timeouts work.

### Phase C2 - Patch and verify in one sandboxed workspace

Status: **SAFE HALF DEPLOYED AND VERIFIED for disposable workspace `sample`.**

Add `apply_patch`, file creation, registered tests/lint/build, diff inspection, and task state.
Start with the orchestrator source or a disposable fixture repo, not production deployment.

Exit criteria:

- completes five representative bug/feature tasks;
- preserves unrelated changes;
- produces syntactically valid patches;
- iterates from a failing test to a passing test;
- gives an accurate final change/test summary.

### Phase C3 - Git checkpoints and independent review

Add optional worktrees/checkpoints and planner-worker-reviewer packets. Benchmark the currently
installed `qwen3:14b` and `qwen3.6:27b`, plus one coding-focused model that fits Mothership, using
the same local task set. Choose roles by measured success, not model branding.

Status: **COMPLETED.** Reviewer benchmark, evidence packet, single-pass guard, local Git
checkpoints/restore, isolated task worktrees, merge/discard, and live review workflow are verified.

Exit criteria:

- reviewer catches seeded bugs without rewriting correct code unnecessarily;
- handoffs include evidence and do not invent completed work;
- collaboration improves task success enough to justify latency.

### Phase C4 - Approved remote operations

Add named adapters for Sundae and selected services. Begin with read-only logs and health checks,
then deploy preview, backup, apply, verify, and rollback.

Exit criteria:

- every change has a preview and rollback;
- no raw SSH key or unrestricted shell reaches the model;
- interrupted deployments resume or roll back cleanly;
- production changes require explicit approval.

### Phase C5 - Product-quality coding experience

Add streaming task timeline, diff viewer, approval cards, artifact links, cancellation, resume, and
per-project instructions. Support longer tasks without mixing their state into ordinary profile chat.

Add Normal and Think Really Hard effort controls, pass-level status updates, elapsed/budget display,
graceful cancellation, and durable multi-pass resume.

Status: **MOSTLY DEPLOYED.** Effort controls, pass-level streaming updates, elapsed/budget metadata,
work journal, compact Deep Trace, durable coding-task pause/cancel/resume across runner restarts, and
unfinished-task auto-surfacing are live. User-facing cancellation of an actively generating model
response and richer timeline/diff UI remain pending.

### Phase C6 - Optional Claude/Codex executor

Keep explicit-only billing. Package the same task packet and workspace snapshot for the external
agent, stream its status, and return its diff/test results through the same approval and review UI.
Do not create a separate unsafe execution path.

When Claude or Codex is explicitly requested, present Direct, Normal interaction, Background
review, and Dress to Impress choices. Implement Dress to Impress only after C3 review packets and C5
long-running task state are reliable.

Status: **CONTRACTS LIVE, EXECUTOR DISABLED.** Choice/consent routing and Deep Trace packet format are
deployed. No external request is sent and no billing occurs.

## Evaluation

Create a fixed local benchmark of 15-25 tasks:

- explain an unfamiliar module accurately;
- narrow one-line bug with a focused regression test;
- multi-file feature using an existing pattern;
- repair a test failure;
- update configuration without exposing secrets;
- preserve pre-existing dirty changes;
- reject an out-of-workspace edit;
- request approval for dependency installation;
- review a seeded security bug;
- prepare, but do not execute, a deployment;
- resume after interruption from a task checkpoint.

Score:

- functional correctness;
- tests passing;
- unnecessary changes;
- preservation of user work;
- factual accuracy of final report;
- approval-policy compliance;
- time and model/host usage.

Do not enable unattended production work until policy compliance is perfect on the destructive and
path-escape tests.

## First Build Milestone

Implement **C1 plus the safe half of C2**:

1. Separate `coding-runner` service.
2. One registered disposable repository.
3. `workspace_list`, `workspace_open`, `file_list`, `file_read`, `text_search`.
4. `git_status`, `git_diff`, `git_log`.
5. `apply_patch` against the disposable repository only.
6. One registered test command.
7. Task record, plan updates, audit log, and final diff.
8. Mothership 27B as lead; 8B as read-only scout.

This proved the architecture with a real edit-test loop. The deployed pilot now also supports local
checkpoint commits, restore, isolated worktrees, and durable pause/resume while keeping remote access,
package installation, user-directed commits/pushes, deployment, and credentials out of scope.

## Remaining Decisions

- ~~Whether and when to register a canonical production repository for controlled write access.~~
  **CLOSED — `orchestrator-real` is registered and is the default workspace** (verified live
  2026-08-14, `promote.py:117`).
- What approval and promotion workflow should move reviewed pilot changes into canonical production.
- Whether local user-named commits should be added; remote pushes remain disabled by policy.
- Whether to add narrowly allow-listed package installation, deployment, or remote execution.
- Whether a Windows-native runner is needed later; the current hardened runner lives on Sundae.
- How to implement actual Claude/Codex dispatch after the non-spending choice contract.
