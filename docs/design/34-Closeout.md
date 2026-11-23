# #34 Local Model Modernization + Fellowship Compute + Adaptive Orchestration — CLOSEOUT (conclusion report, opened 2026-10-23)

**COMPLETED 2026-10-25 night, on the owner's word (*"Mark 34 completed with notes"*), right after HAI 1.9 launched on #34's build — § "Final close (2026-10-25 night)" at the end. Before that:** **Closed 2026-10-23 22:47 CT on the owner's word — reopened 2026-10-24 for area C's last step and CLOSED AGAIN 09:50 CT once it was built and proven ("the request's own needs", the last bullet of § "What is live")** (the owner: *"you are not allowed to close out 34 until you are 100% confident that we have 100% satisfied the charter and the design doc and everything that I've said over the course of the project and intent"*; his go to close, 17:2x: *"I need you to help me get these closed properly"*): every phase closed or answered, DoD 1–7 answered, his six rulings written and every owed item done — § "The close" at the end of this report. Written at ~09:40 CT: the owner, this morning: *"It's my weekend, I'd like to wrap this project up. Ideally we
can finish project 34 today or early tomorrow."* The close-out plan with every line's state is the top of
[[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]]; this document is the conclusion report the registry convention asks for:
what #34 set out to do, what shipped, what the measurements decided, what was disproven, what is handed off, and what
is still unverified — named, never pretended. Charter: [[34-CHARTER]]. Children: [[36-Closeout]] (split inference,
closed 10-16), [[37-Closeout]] (HAI agent surface, completed 10-17). The retrospective — the proposal's hypotheses closed against the measurements, the after-action report, every residual with its owner — is [[34-CAMPAIGN-CONCLUSION]].

## What #34 set out to do (the charter's seven questions) and where each stands

| # | DoD question | answer at the close |
|---|---|---|
| 1 | Anything better than the reigning 27B; does the 3.8 earn the seat? | **Crown shared by job family** (register § "Tournament decisions already made"): `qwen3.8:27b` at xhigh on Mothership for quick families (Phase 2 closed 10-09: 8/9 + 3/7 hard); **Flash-Next** (qwen3.8-flash-next on Strata) for agentic / long-context lead work after the RAM swap rematch (A1 12/15 vs 9/15, its solves a superset; ≈0.58 vs ≈0.94 MJ whole-box per solve; external blind spot-check 10-18). the owner 10-18: *"If there's an obvious winner let's stick with flash next as our preliminary winner."* |
| 2 | Best 8 GB model per box? | `qwen3:8b` keeps the seat on both 8 GB boxes (Phase 3 closed 10-07, reopened 10-17 for the split A3B, **closed 10-18 ~23:40**): the A3B's role rows sit beside it on BigHonker (C1-on 95 %, coding f2 100 %, W1 88 %, tool 93–94 %) and Spock places per scenario; `qwen3.8:27b` on BigHonker priced by speed only (~2 tok/s, a patient lane). |
| 3 | Best Fellowship chat / coder / VLM / tool? | Phase 4 closed 08-29: Chat Qwen3.5-2B / Qwen2.5-1.5B · Coding Qwen2.5-Coder-1.5B · Tool Qwen2.5-1.5B · VLM Qwen2-VL-2B (quality) / SmolVLM2-500M (deployable); behavioural eval confirmed. |
| 4 | Does 3× / 5× inference help; where do returns diminish? | Phase 5 closed 09-26: on the Phase 4 sets ONE tablet reaches the ceiling; Hard Bench best-of-N 0.61 → 0.94 (N 1 → 9) **only when picked by the hidden checks** — a ceiling, not production (majority vote falls to 0.39). |
| 5 | Can pooled inference run a smarter model usefully; does swarm beat it? | Phase 6 closed 10-02: rigby alone beats Voltron at every size rigby holds; #36 (10-10 → 10-16) answered the reopened form — helpers READ and DRAFT, the main machine WRITES ([[36-Closeout]]). |
| 6 | When should routing prefer Fellowship / BigHonker / Mothership / plain code? | **Answered 10-11 (Track A) and now LIVE for chat (area C step 1, 10-22):** every Auto turn McCoy decides is placed by Spock's per-request weighing from the priced menu — quality band first, then the interactive line, home before overflow, cheapest energy, then time; the row that chose the box sets its model, thinking and window. Proven live for the lead tier, the 8 GB tier and the Fellowship (10-22/23). |
| 7 | When does a tiny model improve a controller? | **Answered below (C6 / DoD 7) from the measured rows; the real-traffic half is data-blocked and says so.** |

## C6 / DoD 7 — when a tiny model improves a controller (written up 2026-10-23 from the decision-model rows and the shadow log)

**The real-traffic half — answered 2026-10-23 evening from the owner's REAL requests, not from weeks of live use:**
119 of his requests to HAI (its history store) replayed through McCoy + the weighing (`Datasets\Project34-Menu\
real_requests_replay.md`): McCoy sure of the tier at the 0.90 line for 21 of 119; the rest go to the lead by rule,
including 14 simple questions he called 8 GB / Fellowship work at 0.3–0.6 confidence. So on real traffic the tiny model
improves the controller where it is sure (one in five turns) and costs nothing where it is not (0.19 s; the lead is the
safe default) - and the calibration that would let it be sure more often is #35's, with this dataset as its first input.
Five of the requests were run live end to end as a check on the records (same file).

**The answer the rows support:** a tiny decision model improves a controller **where its confidence is calibrated on
the controller's own question and code acts only above a threshold set on the lab's rows** — never as a general
oracle, never below the line.

- **Triage — YES.** tev1 (4B, decision-model interface `/v1/systemone`, Softserve) at the 0.90 line: 10/10 right out of
  set on the owner's 36 requests, deciding ~19–28 % of turns in 0.18 s, 0 under-routes; everything under the line goes up
  to the lead as before. This is McCoy as built: ACTIVE for the owner and ClaudeSupervisor since 10-17 (`3c09e70`), for every user since 10-23, sure
  "below the lead" turns weighed by Spock, unsure turns to the lead.
- **Judging a worker's answer — PER TASK FAMILY, with the bar = the lead's own error rate.** W1 reason: NO (best 5.6 %
  false pass against the lead's 3.1 %). W1 ops: YES (tev1 4B at 0.95: 165 of 287 cheap answers accepted, 0 wrong; 8
  questions, preliminary).
- **Coding difficulty from the request's wording — NO** (C7, 10-19): a reworded tier question got 7 of 106 menu tasks
  right instead of 2, but McCoy's confidence fell so far that he decided 1 of 106 at the 0.90 line. A 4B reading the
  words alone cannot tell that a small model solves a formal coding spec; unsure goes to the lead, which is safe. The
  measured truth for real requests that would teach it is #35's.
- **Family from the request — NO, at least not from tev1's family head** (10-22, `mccoy_family_score.py`): 63 % at the
  0.90 line against the placement record — his family confidence reads TOPIC where the menu's families are SHAPE
  (chat-c1 vs chat-turn vs tool). The weighing therefore takes the family from the TIER (a fixed map), not from McCoy;
  the family head stays recorded for #35's training data.
- **The real-traffic half is still BLOCKED ON DATA, honestly:** McCoy's log holds 106 rows (10-17 → 10-23). Human rows:
  19, all the owner, all SHADOW (10-19/20, before McCoy went active for him) — 6 with a tier (all unsure → lead, which is
  what active would have served) and 13 blank skip rows; **0 human Auto turns since active**. The 23 active rows are
  ClaudeSupervisor's (18 lead, 5 below); 13 of 106 rows cleared the line (8 lead, 5 fellowship). The agreement of
  shadow rows with the served route at the 0.90 line is read the day the owner's Auto turns exist; nothing about it is
  claimed today.
- **What this means for #35:** the data sources are named and recording — McCoy's log (`State\mccoy`), the placement
  record (`State\placements`, every `place()` decision with its candidates and the weighing's rows since 10-22), and
  the owner's own Claude transcripts' escalation points (register, 10-22). C3 placement advice, C4 decomposition advice and
  C5 a lab-trained decision model are the judgment half; whether they hand to #35 is the owner's ruling below.

## What is live at the close (the machine half; every item proven through HAI's front door)

- **The priced menu → `policy.json`** (`Datasets\Project34-Menu`, rebuilt every 15 min by `menu_refresh.py`): families
  × tiers × three lenses (fastest / least energy / best quality), every row its quality + Wilson lower bound,
  s/solve, J/solve, role (home / overflow), caveats, and its prices — the measured cold `load_s`, the measured
  `read_tok_s`, and (since this morning) the Fellowship rows' measured `order_overhead_s`; the Fellowship rows carry
  the Tolkien `order` fields they were priced with. CHAT1's second seed folds in through the chain (10-23).
- **The request's own needs (area C step 3, 2026-10-24, **BUILT + LIVE 2026-10-24 ~09:36 CT** (orchestrator gate **3135** at the pin, container `14:35:40Z`, bytes verified; Tolkien rebuilt on the same `scheduler.py`, gate **959**, three containers `14:37:11Z`; mirror audit clean before)):** McCoy's fourth question - `depth`, quick or deep - logged with its confidence on every Auto turn and acted on only at `MCCOY_DEPTH_THRESHOLD` (0.90; the 10-24 replay over 250 real turns: v2 wording sure on 6 of 108 labelled rows, 5 right, 0 under-routes); a sure `deep` turn keeps rows past the interactive line in contention, every other gate unchanged; `needs` (deep, prio) in every weighed placement record (front-door proof on `ClaudeSupervisor`: three Auto turns - trivia (McCoy depth `quick` 0.77), an analysis request (`deep` 0.71), a Fellowship line (`quick` 0.91, sure tier 0.99 → weighed → rigby in 12 s) - McCoy's log row carries `depth` + its confidence on every turn, and the weighed turn's placement record carries `needs: {deep: false, prio: false}` (ClaudeSupervisor is not a priority user); every depth read was below the 0.90 line, so every turn stayed `quick`, as designed); the priority user (B4a, live since 10-23) proven in the record; a job holding Mothership pauses at an EXTREME price at its hand-over point and quick-resumes when the band drops (unit-pinned; the lines were set 13:10 CT — $0.12 / $0.36 — and the first live hour is yet to come). where `deep` acts: on the turns the WEIGHING places - a McCoy-sure below-lead turn, and a lead turn that overflows (Mothership held) - the direct lead path is the top quality already and has no line decision to make.
- **Spock's per-request weighing** (area C step 1, `scheduler.weigh_rows` / `place_weighed`, live 10-22 22:38):
  an ordered choice over every row's three measured prices — quality band (the tier's best home lower bound), the interactive line
  (10× fleet-fastest for the family), home before overflow, cheapest J, then time; a cold box pays its measured load
  and its conversation's re-read; a Fellowship row pays its desk-order overhead; every row's numbers and the pick in
  the placement record. Urgency from McCoy's kind (a sure job = background, no line).
- **The price of the hour** (area C step 2, 10-23): each record row's `cents` at the PSP price or the owner's $0.11
  placeholder, said; peak = energy before time; extreme = Auto held with a notice, pins still answer. The peak and
  extreme lines were SET 2026-10-24 (peak $0.12 = the utility's alert trigger, extreme $0.36 = p99 of 120 days of PSP prices,
  `Datasets\Project34-Menu\utility_psp_hourly.md`) — live, awaiting the first hour at or above them.
- **McCoy** (Phase 8 triage, tev1 on Softserve) active for every user since 10-23 (`*,ClaudeSupervisor`); lane + tier + kind + confidences
  per Auto turn; the 3.8 is the pin and the unsure fallback, never "Auto".
- **Chat windows as factory orders** on the GPU boxes (B4b overflow, the owner-first slot, the B11b slot save / restore on
  every yield route) and, since 10-23, **ollama windows end at the box's keep-alive** (proven: a BigHonker window ended
  itself ~347 s after its turn).
- **The Fellowship chat form** (10-23): a Fellowship pick is an order to Tolkien, placed through the desk, answered by
  rigby, back in the chat as route `fellowship` — proven, and priced by the proof (next section).
- **The controls, the same for every user** (design § 5, 10-23): the 27B option for all (`MOTHERSHIP_FORCE_USERS`
  retired after four months), a pin to a busy Mothership queues with the notice, the holder's elapsed time and the
  reroute offer with the table's own caveat; Nursing shows Model and Effort; a user's pin always wins.
- **Fit-before-load, the lab engine, the effort levels at their true value** (register: the two effort sandbags found
  and fixed; every Mothership xhigh order since 10-16 really xhigh), **split inference** (#36), **the one-window agent
  surface** (#37).

## Found by the close-out proofs (fixed the same pass, 2026-10-23)

- **The Fellowship paid 85 s the weighing never charged — and the owner read it right: three missing cross-cuts, not a
  Fellowship limit.** The step-3 proof answered correctly in 86.8 s on a 1.9 s row. First the pricing hole was closed:
  `residency_cost` asks a CARD, a Fellowship row has none, so it had been charged nothing; the table now stamps the
  measured `order_overhead_s` from hai-chat deliveries and the weighing charges it (unmeasured is never "inside the
  line"). Then Tolkien's own log placed the 86 s (handoff § 4): **46 s of two 30 s polls** — the GPU takers' 2026-10-06
  idle wait (`_idle_wait` / `QUICK_POLL_S`) had never reached Tolkien; carried over to both lanes at **1 s on all four
  factories** (the owner: "as close to live as possible"); **31 s of a GT 730 reading Kirk's RAG-grounded prompt** — the
  form now sends the bare turn the row was priced on; **6 s per-order load**, which stays. **Shipped and measured
  09:31–09:36 CT:** the same order the form builds, sent to the desk three times in a row, answered "391" each time in
  **9.1, 8.5 and 8.6 s** Kirk-side (order wall 6.9–7.0 s: load 3.0 s, control 2.3 s, turn 1.3 s with first token at
  0.53 s, was 31 s). The table's overhead price re-measured from those deliveries: **6.2 s, n=3** (deliveries before
  the fix no longer count — `menu_policy.OVERHEAD_SINCE`). The Fellowship is an interactive chat destination, inside
  the 16.9 s line, with no warm server.
- **The gate pin caught a swallowed test** (3064 of 3065): an insertion took the header off `test_ui_axes_model_and_effort`
  and its body ran inside the new test — green, uncollected. Predicting the count is what found it.
- **BigHonker rebooted at 03:31 for Windows 11 26H2** unannounced — no order lost, parity intact; #38's case (a lab
  update system) for the second time.
- **The register carried a stale "owed" line** (B11's caller) that the code had satisfied the same afternoon it was
  written — closed on the code, with line numbers.

## What was disproven along the way (so nobody re-tests it)

- Best-of-N and majority vote as production levers (Phase 5); swarm / Voltron over rigby alone (Phase 6); the
  "8 GB = one worker" rule as a physical limit (B10: the split A3B runs on BigHonker); McCoy's family head as the menu's
  family (63 % at the line — topic, not shape); two specific blended drafts of the weighing (the replay showed why:
  overflow as peers sent lead turns to a 0.68 row; line-before-quality dropped a cold lead to 8 GB) — NOT the matrix:
  the owner's intended two-layer design was never disproven, an earlier line here said so without his word (corrected
  2026-10-24, [[34-CAMPAIGN-CONCLUSION]] H1);
  "thinking on" results taken at their label (medium, twice); a lock-vs-stock speed pair without a salt (+50 % that
  was +22 %); the 8 GB cards as "slow" (a lost GPU, an occupied probe); the sub-agent tests of the wrong shape
  (blind, toolless, synchronous pieces — register 10-10).

## Handed off (each written where its owner reads)

- **A warm Fellowship server for interactive chat** — not needed: measured 8.5–9.1 s after the cross-cut fixes.
- **Embeddings placed by the menu** — BUILT inside #34 (the owner's ruling 17:2x; live 17:5x): the `embed` family, GPU rows
  only, no warm server ([[34-Phase-7-Placement-Policy-Design]] § 3 + § 6 step 7); the follow-up entry is archived.
- **Sundae's root volume** — full at 09:15 (five image builds), pruned to 81 %; the 43 GB VFree extension and a
  deploy-runbook cleanup step are owed (handoff § 5).
- **C3 / C4 / C5 (placement advice, decomposition advice, a lab-trained decision model)** — data-blocked; proposed to
  #35 Native Intelligence (register list B).
- **The energy price pipeline** (HA's energy entities `unknown` since 09-25; the PSP sensor) — register B10; step 2's
  bands wake up when it returns and the lines are ruled.
- **#38 lab update system** — the two unannounced factory updates (Mothership 10-10, BigHonker 10-23).
- **qwen3-vl:8b on BigHonker** — tested context 15,360 (fit probe 10-23); read-rate order
  `speedcheck-bi-qwen3-vl-8b-ctx15360-readrate.20261023-085152` queued; folds in at the refresh after it delivers.

## the owner's calls at the close — RULED 2026-10-23 17:2x CT (register § E)

Phase 9 closed by evidence, not built (external readers / drafters over the network = another project) · S10 accepted as
answered (three replicates; helper-piece assignment → #35) · McCoy's placement advice accepted on his 10-21 rule (the
weighing is the hard-coded answer; a narrow door → #35) · the plain-English end-to-end run inside the close; an 8 GB lead
delegating down not measured (not a shipped role) · the Qwen3 reranker checked (4 of 4) and parked outside #34 · embeddings
PULLED IN and built (the `embed` family, 17:5x). Earlier calls, already his: C3/C4/C5 → #35; McCoy active for every user
(done 10-23); the parked energy items and step 2's peak / extreme lines OUT OF SCOPE ("we will use a placeholder for now");
the placed delegate's pieces moved onto the weighing (built 10-23 evening); the A1 "other reviewers" arm dropped (B5);
no warm Fellowship server ("McCoy is the singular exception").

## Unverified at the close (named, not pretended)

- Step 2's peak and extreme behaviour: unit pins, and since 2026-10-24 a live trigger - the lines are SET (peak $0.12 = the utility's alert trigger, extreme $0.36 = p99 of 120 days); no hour at or above them has occurred yet (this autumn's curve runs 1–9 c); the price feed is live (PSP, read on every placement row).
- The Fellowship's desk-order overhead is priced per worker from delivered hai-chat orders (10-23: rigby 8.6 s, a tablet 28.6 s, `menu_policy.fellowship_overheads`); small n, refined by every further turn.
- The queue notice for a pinned turn behind a busy Mothership: SEEN LIVE 10-23 16:4x (one user, two pinned turns: "1 ahead … your turn in about a minute", the reroute caveat; `proof_queue_eta.py`); not yet with two different human users.
- The C6 real-traffic half: 0 human Auto turns since McCoy went active for the owner.

## The close (2026-10-23 evening, on Fable)

the owner ruled all six open items at 17:2x (register § E): Phase 9 closed by evidence, not built (external readers over the
network = another project); S10 accepted (three replicates); McCoy's placement advice accepted on his 10-21 rule; the
end-to-end run owed and the 8 GB-lead-delegates-down not a shipped role; the Qwen3 reranker parked; embeddings pulled in.
Then, inside the close: the `embed` family on the menu (one priced row per GPU box and, on the owner's word, rigby's GTX 1060
through Tolkien's new `embed` order kind - 15 ms a query, the cheapest energy on the family, vectors identical to the GPU
boxes'; placed by the weighing, gates 3120 and 959, live 17:56); then the owner's closure checklist ("Let's get everything
closed out"): McCoy and the weighing replayed over 119 of his REAL requests (the real-traffic half of C6: sure at the
0.90 line for 21 of 119, the rest to the lead by rule - the calibration question is #35's, with the data in
`Datasets\Project34-Menu\real_requests_replay.md`), five of them run live end to end, the busy-pin notice proven with
two different users (and its frozen "held it N s" fixed), every GPU row's time to FIRST VISIBLE OUTPUT measured from
the windows' own turns and shown beside the total, and the Fellowship rows' per-order energy priced into their J on
the owner's word ("probably") instead of building the bulk-embed door - with the desk-time numbers handed to the follow-up
project he named (gate 3125); the reranker checked (Mothership, 4 of 4, ~0.9 s a rerank on CPU) and parked in the backlog
with its result; the plain-English end-to-end run — a plain-English unpinned request went McCoy → Spock → the lead with six tool calls and answered with the live deck state in 171 s; five of the owner's real requests then ran live the same way; the doc pass (10 live docs current, 11 passages archived; the
HAI 1.0 routing text gone from the live library). Honest edges that stay named: the queue notice seen live with one user's
two pinned turns, not two humans; C6's real-traffic half data-blocked (0 human Auto turns); the peak / extreme lines set
2026-10-24 but not yet seen firing live; the Fellowship's desk overhead on small n, refined by every delivery.

## Final close (2026-10-25 night)

**On the owner's word, ~21:00 CT**, minutes after he announced HAI 1.9 to his users: *"Mark 34 completed with notes."* The
2026-10-24 reopens were done before this (area C step 3 at 09:50; Phase 8's McCoy tuning built and live at 16:45); the
last stretch, 10-24 night to 10-25 night, built what the 1.9 release rests on. Each item below is live on all four
factories, gated (orchestrator **3260**, Tolkien **974**, foundry **570**), with its doc named:

- **The matrix considers every row** (the owner: *"it's a damn matrix so it should be able to place anything anywhere
  based on the inputs and constraints"*): inputs reorder, only hard constraints remove; Hard's band is every row whose
  95 % range overlaps the best (`scheduler.wilson_high`), and the walk is the band, then every row below the need.
  Checklist D2a / D2b.
- **Quick and Hard are weights, not tier steps** (*"there needs to be nuance to the weights and not just a blind tear
  up or tear down"*): Quick favours rows within `QUICK_TIME_SLACK_S` of the fastest, a clearly better one still wins;
  Hard leans on quality and the lead's row takes the think-hard path. [[HAI-Control-Surface]].
- **McCoy advises, Spock places** (*"McCoy isn't supposed to be able to unilaterally make choices"*): a lead verdict
  is weighed, and the lead path is taken only when the weighing puts the lead first.
- **Owner home-first, everyone else cost-first** (*"keep the best resources for myself and let everybody else be using
  the optimal combo"*): `config.PRIORITY_USERS`; the cheapest tied row before home for every other user.
- **An idle window is a cache**: a turn for another model closes it, waits for the card to be freed, then sizes;
  Kirk's own window is not a foreign job to the gaming guard. [[RUNBOOK-Ollama-Host-Operations]].
- **The crown's one fallback**: a window that fails before any output is unavailable (never retried); a crown that
  cannot serve falls once to the box's standing model; a call that ends unavailable releases its own live claim, and a
  run's job claims are released when it ends.
- **The rigby warm chat window** (Tolkien kind `chat.window`, Kirk `TOLKIEN_WINDOW_URL`; on for everyone via
  `State\rigby-window.json`): ~3 s warm turns, was 8–13 s.
- **Per-chat quick resume with the array behind it**: five chats per user on the box, older ones to
  `Big Shared\HAI\chat-slots\<box>`; placement leans toward the box holding the chat's state; an older chat comes back
  only when copying it beats re-reading it (measured array read vs the menu's read rate), and the first keystroke in a
  chat prefetches it. [[RUNBOOK-Ollama-Host-Operations]] § CHAT QUICK RESUME.
- **8 GB windows sized to fit beside the desktop**, free RAM in every box's minute report, a split window refused when
  its measured RAM side does not fit; and the deploy gate no longer sshes into Mothership (the October 24 hangs).
- **An operator hold survives the factory's idle push** (`scheduler.OPERATOR_HOLD`) — the RAM-test hold of 10-25.

**Unverified at the final close (named, not pretended):**
1. An older chat actually coming back from the array: no chat has moved there yet (it takes a sixth chat per user on
   a box). The window tests and the endpoint answer; the live round trip is owed.
2. Mothership's chat windows cannot reach the array as SYSTEM until its credential store holds the Softserve entry
   (the owner's step); BigHonker and Softserve can, measured 10-25 ~20:05 as SYSTEM.
3. The Tolkien deploy's clock fix (`fleet_deploy_tolkien.rigby_since`): proven by tests, live only once a deploy stops
   a running order.
4. The electricity price lines: still never fired on a live hour.
5. C6's real-traffic half and the busy-pin notice with two human users: as at the first close.

Every follow-up, with its owner, is in CURRENT-PROJECTS's top block; refinements no result called for stay in
PROJECT-BACKLOG § "#34 LEFTOVERS". Journal: 2026-10-25-claude.
