# #37 HAI agent surface — CLOSEOUT 2026-10-16 (one window, always an agent, live on every device)

**✅ COMPLETED 2026-10-17 ~00:50 CT (overnight, the main session — the owner's mission: *"get 37 closed out overnight"*).**
Reopened ~04:25Z 10-16/17 because the live proof handed to the McCoy wiring (answer 7) died on a 240 s clock; every
item of that reopen is DONE and LIVE (§ "Reopened 2026-10-17 night — closed"). **One confirmation is the owner's:** check
the new Effort / Work controls and the name picker on his phone (the bar he set at the last close). Hand-offs stay
owned in the #34 register. Earlier headers (completed after the phone check; reopened for the phone layout; reopened
for the clock): 37-closeout-history-2026-10. Charter: [[HAI-Agent-Surface-Design]] (registered 10-16, child of
campaign #34). Built 10-16/17 — by a side session until ~17:30, then by the main session. Every change went build →
full gate (count predicted) → park → recreate → container bytes checked.

## Reopened 2026-10-17 night — closed

- **A model call had a fixed clock, and it killed the live proof — fixed and PROVEN live.** `CHAT_TIMEOUT=240` bounded
  every silent stretch of a call; ollama sends nothing while it reads a prompt, and on the 3.8 a fresh deep read
  outlasts 240 s. Now a silent call asks its box's own load report: a busy card is waited on, our model loaded on an
  idle card for 2 reports is a stall (`ModelStalled`), no fresh report = the old clock; Stop works during a silent read
  (`5d82c73`). **Live:** the McCoy-wiring job's reads of **254, 382, 347, 295 s** were carried where the old clock would
  have killed each (`LONG READ`).
- **A run that ended without an answer vanished from its chat — fixed:** the message stays, marked `⚠ Not finished.`
  with the reason (`_keep_unanswered`, `5d82c73`).
- **The five helper calls with their own clocks — moved:** action/claim/coding review, genetics ×4, identity go through
  `ollama_client.post_supervised` (the same silence watch for a non-streamed call; `4c57eb2`).
- **The effort pin (the unification the owner confirmed, "matches the aha") — LIVE:** the page's **Effort** = Auto · Off ·
  Low · Medium · Max (the model's own level; Max = xhigh on the 3.8), a pin that follows the account and wins over the
  job rule; **Work** = Answer · Think hard beside 🌙 Background (`e2ffaa1`). The Auto half (effort from Spock's priced
  row) is #34 Phase 8's, with McCoy's routing.
- **The name picker fits a phone — fixed** (`4c57eb2`; margin:auto safe centring). Was the last listed leftover.
- **THE LIVE PROOF (answer 7) — DONE.** HAI's McCoy-wiring job, attempt 2 (run `b6b1b3a3…`, ClaudeSupervisor chat
  `7c0e2e408492`, 72 min): **CONTEXT COMPACTED** at 111,096 of 122,880 (90%) and carried on; **the finish gate passed
  honestly** — the last edit 00:07 CT, a full unit run green 00:08, the runner suite 00:17, a checkpoint re-run at
  00:31, no `FINISH GATE refused`; independent review 0 findings. Merged onto live by the main session with one review
  fix (McCoy's triage call onto `post_supervised` — its first live call took **28.0 s** cold against the old 30 s
  clock), deployed `3c09e70`+ (gate **2825**, Softserve `/v1/systemone` count unchanged across the gate: 371 → 371).
  **Live:** `mccoy.triage` answered from Softserve (tev1, `8gb` at 0.44 → below 0.90 → `lead`), and the front door
  logged its first shadow row (`skipped: machine`, `State\mccoy\2026-10.jsonl`).
- **Found by the merge's own gate, fixed the same pass:** the image did not carry `mccoy_questions.json` (`COPY *.py`)
  — `import app` would have failed in production; the gate's collection pin caught it (1981 of 2825). Runbook:
  [[RUNBOOK-Orchestrator-Deploy-Procedure]] § "A NEW NON-.py FILE needs its own Dockerfile COPY line".
- **Open, #34's (B12):** an unidentified caller loaded qwen3.5:9b onto Mothership three times during the job, evicting
  the 3.8 (each a 5–6 min re-read). Evidence and next instrument in the #34 register checklist.

## Phone layout (reopened and fixed 2026-10-17; the owner checked it on his phone: "All looks good")

- **The open chat list could not be closed on a phone.** Below 760 px the list is an overlay drawn over the header,
  so it covered ☰ and nothing else closed it. Now: a ✕ inside the list, a dimmed backdrop that closes it on a tap,
  Escape, and it closes by itself once a chat is picked, started or opened from search.
- **Long lines made the conversation wider than the screen.** A bubble was a flex item sized to its content, capped
  at 760 px but never at the screen, so a long code line or test name stretched it past a phone. Now a bubble is capped
  at the conversation's width; code blocks and tables scroll inside it; images shrink.
- +2 tests, gate 2774 (`34c8a12`). Verified by the owner on his phone. (The in-app browser HANGS on HAI's page — its live
  feeds never let it "finish loading"; `find` sat 23 min. Now a titled gotcha in RUNBOOK-Known-Quirks-and-Gotchas.)
- The "Who's chatting?" name picker ran off the right edge at 375 px — FIXED 2026-10-17 (`4c57eb2`, safe centring);
  the owner's phone check pending (with the new Effort / Work controls).

## the owner's answers to the closing questions (2026-10-16 evening) and what each became

| # | question (short) | the owner | result |
|---|---|---|---|
| 1 | overnight self-eng drives: resumable like the other lanes? | "build" | built: § "Overnight drives resume" below |
| 2 | finish gate on an interactive job: keep the work labelled "Not verified" (today), or revert it / add a scope check like the overnight lane? | *"we've talked about that during project 12, keep in its own section and marked. Not deleted. We may want to come back to it."* (#12 lesson 7, "failed runs still contain assets") | the decision stands; the code did not fully honour it in two places, both fixed — § "Unverified work: its own section" |
| 3 | think-hard critic | "agreed" | the same 3.8 now (live); a critic on another box later, via Spock |
| 4 | peer review on coding | "agreed" | stays off; an A1 arm with other reviewers is owed under #34 |
| 5 | Model/Effort pin per device or per account | "yes" (account) | live: the pin follows the account |
| 6 | Step / Context / Speed rows for FACTORY orders | "tackle it" | live on all four factories |
| 7 | live proof of compaction + the finish gate on a long job | "circle back… probably McCoy wiring" | handed to McCoy (#34 Phase 8) |

the owner also said of the think-hard run he watched: *"Looks like it took forever and was getting dinged for lame things
and I wouldn't be surprised if they were blind or sandbagged reviewers/critics."* Both were true and both are fixed:
the critics saw only the first 12 steps (now the steps that bear on the answer, plus the library excerpts), and the
second critic was a different model with thinking off on the same card (now the lead itself, at its own effort).

## What is live (orchestrator, mirror `orchestrator-build` = container)

| piece | what it does for the owner | proof |
|---|---|---|
| Live transcript (step 1) + message on accept (1a) | every open device shows the message at once and the same live stream | 2 viewers, same 108 events; message in history 2 s after accept |
| Order cards (step 2) | an order a conversation starts shows inside it, live | end to end on the 3.8 |
| Surface of "always an agent" (step 3) | Model/Effort optional; your pick is a remembered 📌 pin; Auto = HAI decides | page harness 7/7 (no browser check — the owner declined) |
| Fairness shown (step 6 surface) | a waiting request is told its place on Mothership and the rule | unit test (no browser check — the owner declined) |
| 🌙 Background switch | replaces Absurd on the page; your time budget; says so if the budget, step cap or idleness stopped it | live |
| Quick Resume | checkpoint at every turn AND tool; deploys PARK runs; rolling saves + pin; ⑂ branch; suspend/resume one run | live proofs (restart mid-answer, park, branch, suspend) |
| Kirk's work on the tiles | write/read tok/s, step + tool seconds, context fill | live |
| Stale-workspace refusal (R2) | a coding task refuses to start on an out-of-date HAI workspace; lag on the Deep Work tile | live, in sync after the sync |
| Lead effort on job routes | job-shaped requests run the 3.8 at real xhigh (`"xhigh"`, not `"high"` = medium on 0.40.5) | LEAD EFFORT line + thinking deltas |
| **Finish gate** (work loop) | a job that changed code is done only when `coding_test` passes on the current code; else "Not verified" | 8 tests + mutation; live proof → McCoy (below) |
| **Talk to it while it works** | send during a run → taken at its next step, shown on every device, saved with the answer | LIVE 18:31, ClaudeSupervisor chat `e157da0113f1` |
| Retry fix | `num_ctx` shrinks only on a memory error | tests; ends the reload storms |
| Context compaction in the loop | at 0.9 of the window the agent summarises its own work and keeps going (request + latest step kept) | 3 tests + mutation; first long job is the live proof |
| Critic sees the work | think-hard's critic gets the edits, test runs, the trail's end and the steps behind the answer's claims | 5 tests + mutation |
| Own-tests rule | the finish gate also refuses a code change that adds or changes no test | +3 tests |
| Cold wake said | "Waking Mothership…" / "loading the lead model…" on the status line | 4 tests; LIVE 19:0x CT (forced think-hard on the 3.8: "Mothership is awake; loading the lead model (cold load, about 45 s)") |
| Critic sees the reference notes | think-hard's critic also gets the library excerpts the responder was handed | +3 tests (`a1a22de`, gate 2716); found by that live proof: a correct answer drew 3 "unsupported" findings |
| No false "Tool snag" | a list result (doc search hits) is data, not a failure | +1 test; found by the same proof |
| **Pin follows the account** (answer 5) | a Model/Effort pin set on the phone shows on the desktop; Auto + Normal clears it; each user their own (`GET/POST /prefs/pin`, the page reads the server over its local copy) | 5 tests; `1f2068f`, gate 2729, served page `node --check` OK; **live round-trip 2026-10-17 01:45Z** (ClaudeSupervisor: set → read back → Auto+Normal → cleared) |
| **Critic = the lead** (answer 3) | think-hard's critique runs on the same 3.8, so Mothership never swaps a second model in for it | 1 test; `1f2068f` |
| **Overnight drives resume** (answer 1) | a deploy parks an overnight drive at its next turn; the new process restarts the run by itself: the item in flight first, on its coding task, its drive from the saved turn (a finished drive is not re-run), the run's own clock, no DM. Two UNPLANNED restarts with no progress end it (`restart_loop`); a deploy's park leaves a mark, so deploys never count. A resumed item is judged by whether LIVE moved since it started (its own edits are not "stale"); when a deploy moved live under an item with no edits yet, the restart waits for the deploy's workspace sync | 19 + 7 tests (gate 2748 → 2759). **Drilled live** on fixture `fx-status-summary-wiring`: park held the drive 19:51→19:53 at a saved turn → recreate → `OVERNIGHT RESUMED (#1)` 3 s later on the same task, at turn 5 within 35 s. A second, deploy restart (#2, no crash-loop refusal) showed the race the last row fixes: the resume beat the deploy's own sync by ~30 s and the stale check ended the item |
| **Factory orders on the tiles** (answer 6) | every LLM order shows Speed (write · read tok/s), Step (call N · generating / between calls) and Context, on all four factories, through Kirk's one tile formatter; the factory states them as `gen` on its run-state push | desk 8 tests (gate 2729); foundry: the engine's own log lines, 5 tests (501), all three GPU boxes NEW CODE LIVE; Tolkien: metered at `complete()`/`chat()`, 9 tests (819), both images recreated; Fellowship tile gained Step + Context rows (config repo `7a915d0`). **Seen live on BigHonker 20:03 CT** (speed-check order, deck polled every 5 s): progress 4 / 11 · Speed "write 63 · read 1,403 tok/s" (write = the phase's 62.7, one derivation) · Step "call 5 · generating…" · Context "203 / 40k (1%)". Mothership, Softserve and rigby run the same code but have not had an LLM order since; an order shorter than one 15 s factory beat shows only its first call |

## Unverified work: its own section, marked, never deleted (answer 2, 2026-10-17)

the owner's rule from #12 stands. Measured against the code, two places did not honour it:
- **The shared-workspace abandon deleted work.** The 08-29 fix (`_preserve_before_discard`) saved work only on the
  ISOLATED discard path; `task_abandon` on a non-isolated task (the overnight lane's `bail_task`, any abandon on
  `orchestrator-real`) ran `reset --hard` + clean with nothing saved. Now `_preserve_before_reset` checkpoints first
  (skipping work a checkpoint at the same commit already holds); 3 runner tests, restore proven in the test.
- **Interactive "Not verified" work stayed loose in the shared workspace** — marked only in the chat answer, blocking
  the next coding job's start (dirty-workspace block) and the deploy's sync, and one later abandon from loss. Now the
  gate hands app the tasks the run itself worked on; each is saved as a checkpoint marked `UNVERIFIED (…) - kept on
  purpose, not deleted`, then abandoned (workspace clean); the answer names the checkpoint, and HAI's
  `coding_checkpoint restore` brings it back. A task the overnight lane has in flight is left to that lane.
- Same pass, my own resume fix: an interrupted item a deploy moved live under now restarts on a new task, the old one
  closed by STATUS — an abandon would reset the workspace to before the deploy's sync.
- Deployed 2026-10-17 01:22Z (`67dfa12`): orchestrator gate 2771 as predicted, container bytes = mirror; coding-runner
  rebuilt, in-container sha = mirror, its suite 84/84 OK in the container (+3 new).
- **Cross-cut in the the owner pass (`2809765`):** the runner had two MORE hard resets that saved nothing — the automatic
  baseline restore after a repeated broad test failure, and a restore back to a checkpoint (the work since). All three
  resets now save first; a forward restore loses nothing and saves nothing. Runner suite 86/86 OK in the container;
  the running runner = mirror `8487cc16` since 01:44:42Z (the closing review caught the ~20 min between the first
  runner deploy and this one, when the mirror was ahead of the container).
- **Proven LIVE 2026-10-17 01:53Z with a decoy** (doctrine 12b; on the runner's `sample` workspace, never HAI's):
  the UNVERIFIED path (marked checkpoint → abandon adds no duplicate → the file leaves → the checkpoint holds it →
  restore brings it back) and the runner's own net (dirty work → abandon saves it first → restore works) — 12/12 checks.
  The sample workspace kept the same files; its history gained the decoy's commits.
- **Said plainly (charter WORK item 2, "the tests decide, run by the system"):** in an interactive job the gate reads a
  `coding_test` result the MODEL chose to run; if it never runs tests, nothing does, and the answer ships "Not verified"
  with its work kept. Accepted through answer 2; the overnight lane keeps its own verifier. Not yet seen live — the next coding
  job that ends "Not verified", or the next overnight bail, is the live proof (its notes line: `UNVERIFIED work kept`
  / the runner's `task_preserve_before_reset` audit).

## Found by tonight's live proofs (fixed the same pass)

- **HAI's embeddings knocked over a BigHonker order.** A cold speed check evicted its model at 19:50:26; HAI's
  `/api/embed` (the drill's query) loaded nomic-embed into the gap at 19:50:29 (BigHonker's own serve log), and the
  order refused at "0.0 GB resident" and requeued. BigHonker is the PRIMARY embed host and embeds ignored fleet
  claims. Now a claimed card is skipped for embeds (BM25-only if every host is held), said once per holder
  (`96e983c`, gate 2752). **Seen working live 20:01 CT:** `EMBED skips http://<bighonker-address>:11434: its card is held by
  speedcheck-…-p37long…` during the same order's retry. Cross-cut the same pass (gate 2764): every Kirk-side call
  that reaches a box by URL now asks one lookup, `scheduler.claim_for_url` — embeds, the chat auto-title (it used
  BigHonker's 9B unconditionally; now the failover, or the placeholder stays when both are held) and the genetics
  helper. Chat routing already asked (`brain_host_busy`).
- **A resumed overnight item raced the deploy's own workspace sync** (the drill's second restart): the stale check
  compared live with the workspace's files, so it refused an item that only needed the sync ~30 s later — and would
  also have refused any resumed item's OWN uncommitted edits. Now judged by whether live moved since the item started,
  waiting for the sync when the item had no edits (`722ed43`, gate 2759).
- **The overnight run-state lister read the wrong file as "the last run":** `<run>.adjudicate-request.json` is
  written after the final state and also matched `ovr-*.json`, so an auto-resume could restart a finished run. Now
  exactly `<run_id>.json`.

## What the measurements decided

- **State dump (KV):** no engine switch. ollama's runner IS llama-server with the RAM prompt cache ON; ollama ≈
  direct llama-server on speed (10-02). Today's full re-reads all followed a **3.8 reload** (~25 in 3 h) — from the
  retry ladder (fixed) and think-hard's same-card critique (handed off below). A disk dump only matters across a
  restart (save 11–32 s, restore 1–2 s at 30k/60k).
- **Work loop:** no app harness ([[37-Work-Loop-Harness-Check-2026-10-16]]); HAI's own loop with the tests deciding.
  Peer review stays OFF (A1 reviewer arm 10-08: no gain); think-hard keeps its critique for deep questions.
- **Routing is not #37's:** a hard-coded "Auto → the 3.8" was live ~2.5 min and rolled back. Auto = McCoy's triage
  + Spock's priced menu; the 3.8 is the owner's pin and the unsure-fallback.

## Handed off (not #37's; each written where its owner reads)

| item | to | where |
|---|---|---|
| A critic on ANOTHER box, chosen from the priced menu (answer 3) | Spock / #34 placement | 34 register |
| An A1 arm with OTHER reviewers (peer review stays off until it says otherwise; answer 4) | #34 A1 fills, after McCoy frees Mothership | 34 register |
| McCoy's triage GOING LIVE (shadow wiring LIVE since `3c09e70`, 2026-10-17; routing unchanged until Phase 8 decides) + the Auto half of the effort pin (effort from Spock's row) | #34 Phase 8 | 34 register checklist C |
| Interim (a) job-or-chat for effort, (b) quick-first slot | McCoy / Spock replace them | design doc § SHIPPED |
| Lend the GPU during tool waits | main session, #34 (the owner's call) | design doc Quick Resume table |
| KV dump at park across restarts | #34 engine side, only if restarts prove costly | design doc |

## Known limits (written where the next session lands)

An abandon resets a shared-workspace task to the commit it STARTED from, so a deploy's workspace sync that landed
mid-task is undone (the work is saved first; the stale-workspace check then refuses coding jobs loudly until the next
deploy's sync) — the runner's #12 behaviour; a resumed overnight item now avoids it by restarting on a new task, and
an interactive job hits it only if it checkpointed and a deploy synced mid-job. An EPHEMERAL run (ghost
`test`, one-shots) has no ledger, so it cannot be messaged or resumed; a machine user (ClaudeSupervisor) always gets
the coding tools boosted in tool selection (by design), which can crowd out a tool the request names.

## Cleanup done at close

Stale McCoy coding task `ab29dd86…` closed by STATUS only (the runner's "abandon" would have reset HAI's workspace
to 10-10); the morning McCoy draft moved to `orchestrator-build\backups\mccoy-morning-draft-20261016\`; Tolkien's
intake refusal of a control that repeats an item deployed (`fleet_deploy`); proof chats kept in ClaudeSupervisor's
history (the owner watches them); journals: 2026-10-16-claude, 2026-10-16-claude-p37.

## Charter audit (2026-10-16 evening, against HAI-Agent-Surface-Design: requirements 1–9, § The design, § How the agent does WORK items 1–6, § QUICK RESUME, build order, § Open for the owner)

**→ ALL FOUR BUILT THE SAME NIGHT** (`782a869`, gate 2712, deployed with park 23:52Z; container bytes = mirror; served
page `node --check` OK; workspace synced): (1) in-loop compaction at 0.9 (A1's Inspect rule, request + latest step kept,
fail-soft; 3 tests + mutation); (2) the critic sees the work - **finished properly 2026-10-19** (orchestrator
`9d1c4c8`/`938b12c`, gate 2885): the 10-16 build only chose WHICH 12 steps (edits/tests, the trail's end, steps sharing
tokens with the answer); the 12-step / 1,200-char / 12,000-char caps and the trail's own 80-call / 220-char clip stayed -
a sandbag on a 125,952-token critic (the owner 10-17: *"we want them to be seen"*). Now the trail keeps every call whole for
the critics (the page and history get a compact copy), and the evidence is sized from the critic's own window with the
measured reply room and characters-per-token (`app._critic_room_chars`: 192,066 chars live); the ranking decides only
what is left out when a trail exceeds even that; (3) the finish gate adds A1's OWN-TESTS rule = the
overnight hollow-pass check (a code change with no test change is refused even when the suite passes; +3 tests) —
the scope gate and the revert stay with the overnight lane on purpose: a scope gate needs a spec's named targets (an
interactive job has none), and an interactive job keeps its work for the user to inspect, labelled "Not verified",
instead of being reverted; (4) waking / cold-loading the lead is said on the run's status line (4 tests). The three
questions are ANSWERED from the library (charter § Open for the owner). Findings as written before the builds:**

**In #37's own scope and NOT done at audit time (read from the live code, not from memory):**
1. **The agent summarises its own context when it fills** (charter § Open for the owner, item 4: "default: the agent
   summarises its own context and keeps the full transcript on the server"). Not built: `ollama_client.chat` has no
   in-loop compaction (only the manual "Compact context" button for the conversation). 10-16 runs reached 120,764 of
   122,880 tokens — a long Background job will hit the ceiling. (A1 adopted Inspect's compaction on 10-15; production
   did not.)
2. **Deep-think critic sees the steps that bear on the claims, not the first 12** (§ WORK item 6). Not fixed:
   `deliberation._EV_MAX_STEPS = 12` (the BLIND REVIEWER class).
3. **"The tests decide" = the overnight verifier's full chain** (§ WORK item 2: verifier → scope gate → hollow-pass
   check → revert). Partial: the finish gate requires a passing `coding_test` on the current code; no scope gate, no
   hollow-pass check (A1's gate also requires the lead's OWN tests), no revert.
4. **A cold wake shown honestly in the stream** (§ The design, remaining seams). Not built: `_ensure_lead`
   wakes and settles with a notes line only; the stream does not say "waking Mothership".

**The three "Open for the owner" questions — answered from the library (the owner: "the three questions already may have
documents that answer them"):** warm while in use / cold when idle (10-07 keep-alive rule, 10-04 "30s waking up really
is not bad"); a supervised job enters as a packet in ClaudeSupervisor's thread run by the work loop
(RUNBOOK-HAI-Coding-Escalations § "THE VEHICLE NOW", updated tonight); decomposed mode stays RETIRED (10-05).

**Done and verified:** requirement 1 (live transcript; view presets Normal/Thinking/Verbose/Summary on the page),
1a, 2 (no chat/code mode; pin; talk while it works), 4 (order cards; the conversation is never an order), 5 (front door
on Softserve), Background (WORK item 5), WORK item 1 (one loop to the answer/idle/deadline, streamed), Quick Resume
rows except the two the owner assigned elsewhere, the stale-workspace refusal, the adopt-before-building check.

**Not #37's by charter (hand-offs above):** triage and chat-vs-job (requirements 3, 7 → McCoy); overflow placement,
rigby/tablets in the lineup, advisor placement advice (requirements 6, 8, 9 → Spock / Tolkien / McCoy); "the live
conversation gets priority on Mothership; orders wait — via a Spock claim" (Spock, backlog "HAI's heavy jobs must claim
Mothership through Spock"); decomposition into orders (WORK item 4 → McCoy/Kirk); which peer helps (WORK item 3 → an
A1 arm).
