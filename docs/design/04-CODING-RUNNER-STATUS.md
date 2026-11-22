# Home AI Coding and Deliberation Status

Verified 2026-11-10. Doc is the owner's working lab collaborator after Claude. It performs significant engineering through the existing HAI mechanisms; it is not restricted to a read-only assistant or a toy coding pilot. the owner directs and authorizes. [[30f-DIVISION-OF-LABOR]], [[12-CAMPAIGN-CONCLUSION]], [[HAI-Autonomous-Behavior-Self-Engineering]] and the current runbooks define the workflow.

## Coding and operational access

- The current coding family has 19 tools: workspace/project inspection; file list/read/search; Git inspection; hash-guarded patch/create/file operations; allow-listed tests; durable tasks, decisions, escalations and project drivers; checkpoints; independent review; file fetching; rendering; and image inspection. Availability is filtered by the caller's actual permissions.
- `orchestrator-real` is the Git-backed working copy used for HAI self-engineering. Doc can edit and test it. Canonical promotion and live deployment use the existing admin-only `self_deploy`, `deploy_status`, `deploy_promote` and `deploy_cancel` workflow. It is incorrect to describe Doc as unable to deploy merely because direct workspace writes do not affect production.
- **Doc can take on any repo in the lab's shared storage with an admin's approval** (`coding_workspace_add`, since 2026-11-16; [[06-Coding-Harness-Workspace-Mechanics]] § Workspace Management): no Claude or Codex step, no rebuild. `orchestrator-real` and every workspace added this way are shared: several jobs at once, each in its own worktree. For the foundry, Tolkien and `Lab\bin` repos only `compile` runs honestly in the runner today (their suites need a factory, Tolkien's image, or import scripts that act); their real gates stay on their own deploy doors.
- `orchestrator-pilot` and `sample` remain registered development workspaces. `tablet-intake-canonical` supports the delegated Fellowship intake work. Discover the current registry and its exact profiles with `coding_workspace` and `coding_project`; do not treat this page as an exhaustive workspace registry.
- A successful workspace open makes the existing permitted engineering toolkit available together, including the operational, deployment and handoff doors already present in that caller's pool. The runtime tool result names the callable tools. Read-only, supervised and sandbox-only callers cannot acquire tools absent from their permitted pool.
- General host administration uses `host_admin_list`, `host_admin_run` and human-confirmed `host_admin_exec` through the existing hostops broker. The current registrations include Sundae, Softserve, Mothership, BigHonker, Rigby, and the separate publication-only Softserve connection. See [[RUNBOOK-HAI-Host-Admin-SSH]]. Workspace boundaries are not limits on Doc's separate host-administration role. Credentials remain broker-owned.
- Factory work uses the existing Starfleet order and fleet-deployment doors. Host access does not authorize bypassing factory ownership, stopping active work, or hand-editing queue/worker state. [[OPERATING-BRIEF]] and [[RUNBOOK-Orchestrator-Deploy-Procedure]] apply.

## Working sequence and approvals

Inspect project instructions and Git state; start or resume durable tasks for substantial work; make guarded edits; run the relevant profiles; obtain the existing independent review; resolve valid findings; then create the final tested checkpoint. For HAI's direct self-engineering workspace, review before the final checkpoint so the diff remains visible. Keep work resumable and preserve unrelated changes.

Use the existing canary/deploy and promotion approvals for orchestrator self-deployment. Use the existing per-command approval for mutating host administration. Do not approve your own pending actions. Registered hosts, workspace containment, protected paths, ownership, source staleness, test gates, isolation and rollback remain enforced. Routine work inside those mechanisms is supported; do not invent an additional permission gate or default to sending it to Codex.

Git transport remains disabled inside the coding runner: local commits, worktrees, merges and restores are supported; remote clone/fetch/push are not runner operations. The coding runner has no arbitrary shell or production Docker socket. Those facts describe that component, not all of Doc's capabilities through the broker. File reads are slice-bounded, with fitting ranges suggested on refusal; there is no whole-file edit-size restriction. SHA anchoring, unique replacements, protected paths and review/test/deployment gates protect edits.

External assistants remain explicit opt-in; never invoke Claude autonomously. If a required operation fails, report the exact tool result, investigate the documented door, and prepare the supported correction or escalation. An unprovided tool, a permission refusal, a missing file and an unavailable host are different conditions.

## Current verification and test environment

- Full deployment gate: 3,770 tests, two existing expected skips; last gate 2026-11-10, 53.555 seconds.
- Coding-runner suite: 191 tests, all green (2026-11-16). The story gate now runs in the runner too (`CODE_LAB_BIN` points at the lab tools in the shared storage); before, it skipped there.
- Registered workspace unit profile: 3,770 tests, green, 23 fully enumerated environment skips. These are unmeasured cases, not passes; the full deployment gate is still required. Details are in [[RUNBOOK-Orchestrator-Deploy-Procedure]].
- `orchestrator-real` and `orchestrator-pilot` unit profiles set `HAI_THINK_CAPS_PROBE=0` in that test subprocess only. Live model thinking and `REACH_TIMEOUT` are unchanged.
- The established workspace sync includes named assets plus the fixed `ui` and `tests/fixtures` trees. Dirty work and workspace-only Python files still refuse synchronization; unrelated files are not removed. Run the guarded sync as the final deployment step.
- A real 27B Doc request as **the owner** opened the workspace, inspected its profiles, listed both asset directories, reached Sundae through `host_admin_run`, and recognized the complete self-deploy workflow. Separate broker checks verified all four approved core hosts, their administrative identities, non-admin denial, unknown-host refusal and the read-door's refusal of a write-shaped command.

Evidence and exact versions: `research/doc-workflow-parity-20261110/REPORT.md` in Claude Home. The previous version of this page is preserved verbatim in `Archive/04-coding-runner-status-superseded-20261110.md`; its old pilot-only and no-deployment claims are not current restrictions.
