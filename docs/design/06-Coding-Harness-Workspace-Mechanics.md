# Self-Engineering Agent - Coding Harness & Workspace Mechanics



## HAI Coding Agent Capabilities

HAI operates a coding-agent harness composed of the **coding-runner** sidecar, `coding_client.py`, the `CODING_TOOLS` surface, and `coding_review.py`. The system supports supervised single-task coding in an isolated workspace.

## Workspace Management
Workspaces are registered in `coding-runner/registry.json`. HAI maintains these workspace types:
* **`sample`**: A calculator fixture for testing.
* **`orchestrator-pilot`**: A sanitized, git-backed copy of HAI’s source code. HAI practices coding on this copy.
* **`orchestrator-real`**: A non-sanitized working copy.
* **`tablet-intake-canonical`**: The Fellowship tablet-intake source (mounted from Codex Home).
* **`hai-scratch`**: **HAI's own scratch workspace**, in the shared co-working space `Z:\Workspace\hai-scratch`
  (Sundae `/mnt/workspace/hai-scratch`, mounted rw into `coding-runner` at `/app/workspaces/hai-scratch`, git trust as
  `GIT_CONFIG_*_1` in the compose override). Use one folder per job (e.g. `jade39/`) for patches, notes, drafts and
  test scripts. Nothing in it is canonical. Wired 2026-11-01: the owner had set up homes for Claude, Codex and a shared
  one for HAI, but the coding runner could not reach it, so an overnight job delivered its patches as text.
  **A new workspace (since 2026-11-16): Doc asks, an admin approves.** Doc calls `coding_workspace_add(path, id,
  description, test_commands)` in the admin's own chat; the runner validates it first (dry run: under a share root
  `CODING_RUNNER_SHARE_ROOTS`, a git repo with no remote, test commands only from the safe templates - unittest with
  explicit -s/-t/-p, `compileall`, one test file; never a bare "discover everything"), then a pending card shows in
  plain words what Doc gets; the admin approves (button, or "approve" in chat - the card belongs to the requester, so
  another user cannot approve it). The runner then registers it at runtime in `/state/registry.local.json`
  (persisted, no rebuild); `.git`, `.env`, `secrets`, `backups` are always protected; new workspaces are `shared`
  (several jobs at once, each its own worktree). The lab's shared storage is mounted at `/app/shares/ssdshared`
  (= Sundae `/mnt/ssdshared` = `E:\SSDShared`), e.g. the foundry mirror is `/app/shares/ssdshared/mothership-build`.
  The baked `registry.json` still holds the built-in set (a rebuild changes those). Design and origin: gap G6 in
  [[HAI-Autonomy-Gaps-Fellowship-Intake]].

Workspaces support durable checkpoints: create, get, and restore (roll back to a known-good commit).

## File Operations
File operations include read, list, search, `patch` (sha256-guarded), create, move, and delete. Protected paths (`.env`, `secrets`, `state`, `history`, compose files) are off-limits.

## Testing Constraints
Test execution is restricted to allow-listed profiles only:
* **`unit`**: unittest discover.
* **`compile`**: compileall.
Arbitrary shell execution is not permitted.

## Task Lifecycle
Tasks support start, get, update, finish, and resume operations. Each task includes an objective, acceptance criteria, and a plan. Tasks support an `isolate` (worktree) option and merge or discard on finish.

## Code Review and Routing
* **Independent review**: `coding_review` runs a separate read-only diff review on the 27B model. The `benchmarks/review_cases.json` set tunes this reviewer.
* **Routing**: Substantial coding tasks auto-escalate to the Mothership 27B via `escalate_to_mothership`. A pasted "SUPERVISED LOCAL CODING JOB" is treated as substantial. `escalate_to_claude` exists as a stub.
