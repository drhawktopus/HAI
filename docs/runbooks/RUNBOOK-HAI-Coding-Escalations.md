# RUNBOOK — HAI Coding-Job Escalations & Delegating Supervised Coding Jobs (ClaudeSupervisor)



## HAI coding-job ESCALATIONS — where a blocked job surfaces (HAI-authored)

When a supervised coding job escalates (`coding_escalation` — capability gaps, blocked
operations), the packet no longer sits silent: **Kirk's watch loop DMs the admin within ~60s**
(🚧 "HAI CODING JOB BLOCKED: …" via the one relay), and the packets are reviewable at
`GET /escalations?user=<admin>` · `GET /escalations/{id}` · `POST /escalations/ack {id,user}`
(ack = local seen-bookkeeping in `State\hai-escalations\`, never touches the runner's records).
Module `escalation_feed.py` — the foundry's notify_desk hold-DM pattern cross-cut to the coding
pipeline, authored BY HAI. An acked packet stops
appearing; a recreate re-DMs anything STILL pending (deliberate — an unhandled escalation
deserves a second knock). Raw packet truth stays in `/opt/arrstack/coding-state/escalations/`.

---

## A coding job ended with "Not finished. Home AI hit an internal error", or its folder is EMPTY: a crashed tool, and where the job's files really are

**Its files are in the job's WORKTREE until the job merges them, so an empty folder under `hai-scratch` does not mean it made nothing.** Inside the `coding-runner` container:
`/app/workspaces/.worktrees/<task id>/<the folder it was given>`. List open tasks with `coding_client`'s `task("list")`, or `coding_review._coding_client().task("list")` from
the orchestrator. Copy the files out; do not delete the worktree. 2026-11-05: a 3.6 h job ended with that error, its `hai-scratch` folder was empty, and its finished STL was
sitting in the worktree.

**A tool that raises no longer ends the run (since 2026-11-05).** `tools.run_tool`, the one function every tool call crosses, hands the error back to the model as
`{"error": "The <tool> tool crashed inside HAI (...)"}`. The model carries on and is told to say the step did not run. The orchestrator's notes get a `TOOL CRASH <tool>` line with
the traceback: grep for that when an answer mentions a crashed tool. `Cancelled` and `RunSuspended` still pass through, since those are the operator stopping or parking the run.
"Not finished. Home AI hit an internal error" can still come from a fault outside a tool, in the model call or the loop itself. Read the `query error` and `RUN ENDED WITHOUT AN
ANSWER` lines in the notes for which one.

**Task notes have one shape:** `{"timestamp", "text"}`. Tasks stored before 2026-11-05 can also hold plain strings, so anything reading `task["notes"]` must take both
(`coding_review._note_text`).

## `fatal: detected dubious ownership` in a job's worktree; a merge said `merged: true` and the files were gone; `git_commit: ""`; "there is no diff to review"

**All four are one fault, fixed 2026-11-05 (`edad70e`): git could not run in a task's worktree of a share-based workspace, and the runner read no answer as "clean".**
`hai-scratch` and `tablet-intake-canonical` sit on the share, owned by another user; the compose file allow-lists those two bases for git (`GIT_CONFIG_*` =
`safe.directory`), and a task's worktree under `/app/workspaces/.worktrees/<task id>` was not on the list. So every git command there returned rc 128 and nothing:
checkpoints committed nothing and returned an empty commit id, `coding_review` saw no diff, a discard preserved nothing, and `task finish merge` fast-forwarded nothing
and then force-removed the worktree with the job's files in it. One 3.6 h job lost its folder twice in a day this way.

**Now:** the runner tells git on each call that a worktree it made has its base's trust (`-c safe.directory=<worktree>`); a repository git cannot work in raises
`git cannot work in workspace …; nothing was changed`; a status git could not give is never read as clean; a checkpoint that cannot commit says so; a merge removes
the worktree only after the base is seen to hold the worktree's last commit. Runner suite 99 in the container.

**Check it live** (through the runner's own API, nothing reaches the base): start an isolated task on `hai-scratch`, create a file, `git status` must show it, a checkpoint
must return a 40-character `git_commit`, then discard. Script: Claude Home scratchpad `runner_live_decoy.py` (6 checks). A job's own transcript is the first place to
look: the job that hit this printed the exact error and its cause in its `coding_git status` result five hours before anyone read it.

**Putting a lost worktree back** (done three times on 2026-11-05, only with a byte-verified copy of the files): back up the task record outside `tasks/`, `git worktree add
-b task/<id> <path> HEAD` from the base, copy the files in and compare hashes, remove `worktree_finished` from the task record, restart the runner so it re-registers the
worktree (`_reattach_worktrees`), then confirm git lists the files as untracked. The run itself continues with `/runs/resume` from its saved checkpoint
([[HAI-Agent-Surface-Design]] § QUICK RESUME).

---

## DELEGATE a supervised coding job TO HAI — ClaudeSupervisor machine packet (Absurd submission RETIRED for coding 2026-10-05)

**This is how a Claude/Codex session hands HAI a real coding job on its own source.** First
written down 2026-08-27 (the mechanism shipped 2026-08-14/15 but had no titled runbook section —
the G4 delegation had to re-derive it from journals).

1. **Packet shape:** `SUPERVISED LOCAL CODING JOB` — the recommended template lives in
   `LLM-Project\03-CODING-AGENT-PLAN.md` §"Supervisor Handoff Contract" (objective, acceptance
   criteria, constraints, stop-and-report, final-report format). Workspace for HAI's own source
   is **`orchestrator-real`** (the default since 08-14; `orchestrator-pilot` is the sanitized
   fallback). Precision pays: pin design decisions in the packet, or the draft improvises.
2. **Identity:** submit as **`ClaudeSupervisor`** (machine identity; admin + Mothership-force
   grants in force since 08-14). Its dedicated thread is `2093e941826c`. The machine gate
   refuses a PERSISTENT packet that names no existing `chat_id` — but **ephemeral submissions
   are exempt**, which is what makes the Absurd path work with no chat_id.
3. **Vehicle — RETIRED for coding jobs (the owner 2026-10-05: *"I don't know that I've ever had a successful run of
   absurd mode so I'm down for it to be scrapped or redesigned"*).** Do NOT submit supervised coding jobs to
   `POST /absurd/start`: its critic is blind on coding work (it sees the first 12 trail steps, never an edit or a
   test run — CLAUDE.md doctrine "BLIND REVIEWER CLASS"), and a run has no cancel. The retired vehicle text is in
   `Archive\runbook-absurd-coding-vehicle-retired-20261005.md`.
   **THE VEHICLE NOW (#37, 2026-10-16):** send the `SUPERVISED LOCAL CODING JOB` packet as an ordinary turn
   (`POST /query/stream`, user `ClaudeSupervisor`, its thread's `chat_id`); add `background: "<1h|4h|8h|all_night>"`
   for a long job (the page's 🌙 Background). The packet is job-shaped (`app._is_job`), so the 3.8 runs at real xhigh
   in ONE agent loop with: the FINISH GATE (done only when `coding_test` passes on the current code AND the change
   adds or changes a test — else the answer ships labelled "Not verified"); context compaction at 0.9 of the window;
   messages you send mid-run (`POST /query/message`) taken at its next step; Stop at any time. Peer review of the
   diff is OFF (A1 reviewer arm 10-08: no gain). Decomposed mode stays RETIRED (below). Closeout: [[37-Closeout]].
4. **Workspace freshness FIRST:** `orchestrator-real` is synced to live by
   `/opt/arrstack/sync-workspace.sh` — the LAST step of the orchestrator deploy procedure. If
   a deploy skipped it, HAI drafts against stale code. Check before submitting:
   `sha256sum` of the touched modules in `coding-runner` container's
   `/app/workspaces/orchestrator-real/` vs the mirror; run the sync script if they differ.
5. **What HAI can and cannot do there:** full constrained coding toolset (MACHINE_USERS get it),
   isolated worktree, local checkpoints, allow-listed tests, independent 27B review. NO deploy,
   no remotes, no shell, no credentials. **The session ports the reviewed diff to the canonical
   mirror and deploys through the standard gates** — promotion is never HAI's act.
6. **Escalations** from the job surface per the section above (DM within ~60s + `/escalations`).

### HAI drafting a DOCUMENT (a closeout, a write-up) — it must READ what it cites, and the tool trail proves it, not its say-so

Same vehicle (`POST /absurd/start` as ClaudeSupervisor). the owner, 2026-09-28: *"make sure that when you have HAI write
closeout it's not doing a blind and has access to the things it's supposed to be citing."* Rules:

1. **Name every governing doc in the packet and tell HAI to `get_document` each one, IN FULL, before writing — on
   EVERY pass, revisions included.** A revision template that only lists corrections gets a blind pass: the Hard
   Bench revision 1 (`abr-1790608196-9a39ff`) made **zero** tool calls and wrote that the spec's figures were "not in
   these materials". Include the project CHARTER whenever the document makes recommendations — the Hard Bench first
   draft was never given it and wrote routing verdicts the charter reserves for later phases.
2. **Paste the measured products verbatim** (no hand-typed numbers); the docs carry the rules and rulings.
3. **Check the tool trail after the run.** `/absurd/status` returns `transcript` — one entry per tool step with
   `tool`, `args`, `result`. A full fetch reads `get_document` with a result starting `[Full text of <file>]`.
   `hardbench_layer3_submit.py` (`phase5-prep\commission-artifacts\hard-bench\`) does this as `read_check()` against
   `REQUIRED_READS` and ends `VERDICT: BLIND` (exit 4) if any is missing — discard such a draft unread and re-run.
4. **Then audit** numbers against the ORIGINAL packet (not a revision packet, which contains the prior draft and
   lets a draft trace to itself) with a decoy-proven instrument (`hardbench_layer3_audit.py --decoy`), and read it
   sentence by sentence: a traced number can still be misused. Send corrections back; name every edit you make
   yourself in the audit record on top of the published doc.

### Decomposed mode — RETIRED with the Absurd coding vehicle (2026-10-05)

Decomposed jobs ran through `POST /absurd/start`, so they are retired with it: on 2026-10-05 a five-leaf job
was still on leaf 1 after 35 minutes because every leaf ran the full blind review cycle. The retired procedure
is in `Archive\runbook-absurd-coding-vehicle-retired-20261005.md`; the design record stays at
`LLM-Project\12-AUTO-DECOMPOSE-DRIVER-DESIGN.md`.
