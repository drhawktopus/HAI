# HAI Orchestrator - Operations, Testing & Known Issues



## Testing HAI's own behaviour — the test users (`test-homelab`, `ClaudeSupervisor`)

**To probe HAI at a chosen tier, use `user:"test-homelab"`, `ephemeral:true`, `routing:"force_27b"`.** `test-homelab`
is a test user (`is_test_user()` matches `test*` → never persists history, never a real profile) grounded in the
**homelab** RAG namespace, so probes exercise the same retrieval a real question would (standing account since
2026-08-10). It is not an admin: no profile tools, no Home Assistant device control — a test account has no business
changing the house.

* **`routing:"force_27b"` is honoured for EVERY user since 2026-10-23** (`MOTHERSHIP_FORCE_USERS` retired; the HAI 1.0
  gate and its `docker-compose.override.yml` list are archived in `Archive\hai-1.0-routing-docs-history-2026-10.md`). A
  pin to a busy Mothership QUEUES with a notice, a rough ETA and a reroute offer — it is never silently downgraded.
* **Every response carries `model` and `host`: read them back and record them.** Asking for a tier and getting it are
  still different claims (2026-08-10: a session reported HAI's behaviour and nobody could say which tier answered).
* **Live PROOFS of run features use `ClaudeSupervisor` with an explicit `chat_id`** (the owner can open and watch the chat);
  `user:"test"` is ephemeral and persists nothing.

⚠ **A/B-ING A PROMPT CHANGE: INTERLEAVE THE ARMS, AND EXPECT NOISE.** Measuring whether a
`GROUNDING_GUARDRAILS` edit changes behaviour on a fixed question went: n=1 paired → "it works";
n=3 **blocked** (all treatment, then all control) → the exact reversal, 3-0 each way. A blocked
design confounds ARM with WHEN IT RAN, and a single pairing of a stochastic generator is not a
measurement at all. Alternate A/B/A/B, run several pairs, and score mechanically against a
verdict you can check — the tablet question has a MEASURED answer (frodo 2026-08-10: the detached
process fired on time; radio loss did not kill it), which is what makes it scoreable.

## Operational gotchas (hard-won; don't relearn)
- **Build the orchestrator image as ROOT / via the broker.** A non-root `docker compose build` silently bakes an EMPTY module from any non-world-readable (0600) source file. `_promote`/`_restore` now chmod 0644.
- **Deploy-driving `/query` as the owner must be `ephemeral:true`** — a normal query loads the owner's long history → `_compaction_worker` + window overflow (empty answers / the old `softserve_allowed` crash). Run such `/query` calls **detached on Sundae** (`nohup … &`), because a client disconnect (e.g., a 2-min ssh timeout) triggers the cancel-on-disconnect and aborts the worker.
- **A deploy recreates the orchestrator container → its `docker logs` history is wiped.** For post-deploy forensics use the persistent stores (`notes` diag at `/docs/State/diag/<date>.jsonl`, `ops-audit.jsonl`), not `docker logs`.
- **PowerShell→`ssh sundae`:** base64-encode a staged script and pipe (`echo <b64> | base64 -d | bash`); raw quoting mangles. The Bash tool is LAN-sandboxed and lacks coreutils — use PowerShell for `ssh`, the Grep/Read tools for files.
- **The 27B narrates instead of executing** on subtle multi-edit self-changes (partly the window overflow above). Don't babysit past ~2 failed passes — escalate to Claude/Codex (which is itself "the escalation path working").
- **`coding_client.task("finish", …)` takes `merge`/`discard` (a worktree-merge action), NOT `outcome`/`summary`.** Passing `outcome=`/`summary=` is a silent `TypeError` (swallowed under `2>/dev/null`). **To CLOSE a task, use `task("update", task_id=…, status="complete"|"cancelled", note=…)`.** (`coding_client.task` actions: `list`/`start`/`get`/`update`/`finish`/`resume`; `decision`: `create`/`list`/`get`/`resolve`; `project_driver`: `start`/`advance`/`get`/`list`/`resume`.)
- **`docker exec orchestrator python3 -c "…"` with nested `\"`-quotes inside a base64'd ssh script GARBLES** (prints blank). For any non-trivial inline Python, **write it to a `.py` file, `base64 -d > /tmp/x.py`, `docker cp` into the container, then `docker exec -e PYTHONPATH=/app -w /app … python3 /tmp/x.py`.** (Also why `import app` fails bare: needs `PYTHONPATH=/app`.)
- **Continuation-driver hijack:** `_self_engineering_needs_continuation` continues while ANY `orchestrator-real` task/project is `status=="active"` — so a stale/forgotten/errored active task hijacks a later request. Fixed 2026-06-29 with `_task_is_fresh` (idle > `SELF_ENG_TASK_STALE_SECONDS`=7200s is ignored). Still: close tasks on terminal outcome (`status=complete`) so they don't linger.

## Cross-refs
- **User-facing hai.lan control surface** (4-axis header, Absurd, personas-as-hats, planning-for-all + its layer
  separation) → [[HAI-Control-Surface]].
- Project #12 status, decisions, defects, and the authoring-reliability probe → CURRENT-PROJECTS.
- Phase plans → [[06-SELF-ENGINEERING-AGENT-PLAN]] (+ [[03-CODING-AGENT-PLAN]], [[04-CODING-RUNNER-STATUS]]).
- Stack/network/deploy basics → parent `E:\SSDShared\CLAUDE.md`.
