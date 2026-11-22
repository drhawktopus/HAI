# HAI Standing Invariants

<!-- Read by the self-eng loop: injected into every overnight DRIVE and CRITIQUE prompt
     (config SELFENG_INVARIANTS_PATH). Keep it SHORT — every line costs prompt budget on every
     review. One invariant per bullet, phrased as a checkable rule. the owner owns this file; edit
     freely — changes take effect on the next run, no rebuild. Created 2026-07-06 (Claude) after
     the /query/reattach identity-lock hole showed cross-boundary rules are invisible in a diff. -->

Violating any rule below is a CRITICAL defect even if all tests pass.

- **Identity pinning:** every endpoint that reads or acts on per-user data (queries, history,
  memory, uploads, runs/reattach) must pin a NON-admin gated user to their authenticated
  `X-Hai-User` (`_enforce_identity` / `_gated_identity` + `_is_locked`). The self-asserted `user`
  field is LAN-trusted only. Unguessable per-run ids (cancel_id) may act as capabilities.
- **Human-only approval:** nothing HAI writes may create, approve, or modify backlog work items —
  `backlog.yaml` is a read-only mount; `approved_overnight` is human-only. Proposals go to
  `proposals.yaml` and are never eligible to run.
- **Sandbox-only overnight:** overnight work happens in coding-runner workspaces; deploy/live-apply
  tools are mechanically stripped. Nothing reaches the live orchestrator without a human deploy.
- **Claude is explicit-opt-in:** no code path may invoke Claude (API, CLI, subscription) without a
  per-use human approval. Inert request files are fine; auto-firing watchers/schedulers are not.
- **Existing tests are load-bearing:** never weaken, rename, or delete an existing test unless the
  item's `allowed_test_edits` names it — and then only to strengthen or correctly retarget it.
- **Secrets stay out:** never read, log, or commit `.env`, `secrets/`, tokens, or webhook URLs;
  protected paths in workspaces are non-committable by design.
- **Energy gating:** long GPU work respects the energy gate (`energy.is_runnable_now`); only a
  human may override it.
- **Fail toward safety:** on ambiguity, a guard/gate failure must resolve to "flag a human", never
  to silent success (no defaulting a failed check to pass/fixed/converged).
- **Remedies need consent:** never stop, requeue, or change factory work without an approved
  remedy proposal (`fleet_remedy`) — the human's yes is per-proposal, re-verified at yes-time,
  and never inferred, generalized, or reused. Watches report; only consented remedies act.
- **The librarian reflex:** finishing (or abandoning) real work is not done until it is written —
  `journal_append` for the narrative, `handoff_note` for anything the next shift must see first.
  Both are the ONLY library write paths; never sensitive/personal material (person records only),
  never speculation stated as fact — the claim sweep reviews every entry.
