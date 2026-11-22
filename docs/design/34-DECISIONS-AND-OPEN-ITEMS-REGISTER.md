# #34 DECISIONS AND OPEN ITEMS REGISTER — what the owner approved, what is built, what is still owed (read this after EVERY compaction)

**Why this exists (the owner 2026-10-07):** *"Review my conversations over the past few days for every time we step back
and re-ground because I've discussed literally all of those things with you … I'm really getting tired of doing this
multiple times a day, every day."* Every compaction left only a raw transcript dump; the next session re-derived the
plan, missed approved work, and asked the owner again. This register is the distilled ledger: one row per decision or
approved build, its status CHECKED against the queues/code (not remembered), and the owner of the next step.

**Rules:** (1) read it before proposing work or asking the owner anything about #34; (2) when an item's state changes,
update its row in the same pass (move finished rows to `Archive\34-register-done-<date>.md` verbatim — class 21);
(3) a new step-back conversation ends by adding/updating rows here, quoting the owner. Source dump for 10-03 → 10-07:
session scratchpad `convo_both.txt` / `convo_owner.txt` (both speakers, timestamps).

Authoritative plan docs this register indexes: [[34-CHARTER]] · [[34-A1-Agentic-Build-Family-Spec]] ·
[[34-S10-Rigby-Lead-Fellowship-Design]] · [[HAI-Agent-Surface-Design]] · [[34-Shape-Catalogue]] · PROJECT-BACKLOG.

## ✅ COMPLETED 2026-10-25 night — #34 closed for good on the owner's word; HAI 1.9 launched on its build the same night

the owner, 2026-10-25 ~21:00 CT, right after announcing 1.9: *"Mark 34 completed with notes"*. The two 2026-10-24 reopens
(area C step 3, "the request's own needs", done 10-24 09:50; Phase 8 McCoy tuning on real traffic, built and live 10-24
16:45) are finished; their blocks moved verbatim to `Archive\34-register-done-20261025.md`.

**What the last stretch (10-24 night → 10-25 night) added, all live and gated** — the full list is [[34-Closeout]] §
"Final close": the matrix considers every row; Quick and Hard as weights (the owner: *"there needs to be nuance to the
weights and not just a blind tear up or tear down"*); McCoy advises, Spock places; owner home-first, everyone else
cost-first; idle windows are caches; the crown's one fallback; the rigby warm chat window; per-chat quick resume with
the array behind it and the typing prefetch; 8 GB windows sized to fit beside the desktop; free RAM in the minute
report; the gate that stopped sshing into Mothership. Gates at the close: orchestrator **3260**, Tolkien **974**,
foundry **570**, all four factories on the same code.

**Not proven live at the close (named, never pretended):** (1) an older chat actually coming back from the array — no
chat has been moved there yet (a 6th chat per user on a box); (2) Mothership cannot reach the array as SYSTEM until its
credential store holds the Softserve entry (the owner's step); (3) the Tolkien deploy's clock fix, until a deploy stops a
running order; (4) the electricity price lines, never yet fired on a live hour. These and the rest of the follow-ups
are in CURRENT-PROJECTS's top block; refinements no result called for stay in PROJECT-BACKLOG § "#34 LEFTOVERS".

## 🏁 CLOSE-OUT PLAN — 2026-10-23 (the owner: "It's my weekend, I'd like to wrap this project up. Ideally we can finish project 34 today or early tomorrow") — **CLOSED 2026-10-23 22:47 CT**

> **HAND-OFF 09:20 CT, Fable → Opus: [[HANDOFF-Project34-Opus-20261023]] was the plan Opus started from.** Its step 1
> is DONE (09:31–09:40 CT): orchestrator `f567763` live (gate 3066), Tolkien's 1 s idle wait live (gate 911), the GPU
> takers' 1 s quick poll + the counter re-sample live on all three (foundry suite 539), and **the Fellowship turn
> measured 86.8 s → 8.5–9.1 s** (three desk orders) and **8.8 s through HAI's front door** (route `fellowship`). The 86 s
> was three missing cross-cuts (handoff § 4): Tolkien's 30 s polls (the GPU takers' 10-06 idle wait had never reached
> it; now 1 s on all four — the owner: "as close to live as possible"), a GT 730 reading Kirk's grounded prompt (the form now
> sends the bare turn; first token 31 s → 0.53 s), and a 3 s per-order load, which stays.

Every open line in this register, sorted into what the main session builds without asking (the approved frame) and
what only the owner can rule. Rows move to ✅ here as they land; the campaign ends with the conclusion report
(registry convention) and the registry row.

**A. Build today → tomorrow (in this order; each gated + proven before the next):**
1. ✅ **10-23 08:33** ollama chat windows end at the box's keep_alive like Strata windows (design § 4 a, the E2
   finding) — live proof: a BigHonker window ended by itself ~347 s after its turn (rule 300 s); journal 10-23 § 08:00.
2. ✅ **10-23 09:13 PASS** B4b's "a live HAI run is OCCUPIED" live decoy (`scratchpad\proof_live_occupied.py`): a
   ClaudeSupervisor Auto turn generating ~500 words (103 s) on Softserve's qwen3:8b — the box carried a `live` claim
   naming that model (holder `hai-chat:ClaudeSupervisor`) and one in-flight Kirk request on the deck's row for 61 of
   73 samples during the turn, and the claim aged out by itself 120 s after it ended. The window build's re-proof is
   the morning's idle-window proof (a turn through a BigHonker window, then the window ending at the keep-alive).
3. ✅ **10-23 08:27** The Fellowship chat form (design § 3): a sure below-the-lead turn weighed onto the rigby row was
   placed through the desk, run by Tolkien on rigby and answered correctly — route `fellowship`, mirror `9720846`.
   ⚠ **Finding: the turn took 86.8 s on a 1.9 s row and the weighing had charged 0 before its first token** (a
   Fellowship row is not a card; rigby launches its server per order). Fixed the same pass: the table stamps a
   MEASURED `order_overhead_s` on Fellowship rows from delivered hai-chat orders (86.1 s, n=1) and the weighing charges
   it; unmeasured is never "inside the line". Consequence, honest: an INTERACTIVE Fellowship-tier turn now goes to the
   overflow row inside the line; a sure job keeps the Fellowship. **✅ 09:37 — RE-PROVEN after the three cross-cut
   fixes:** three desk orders 9.1 / 8.5 / 8.6 s (load 3.0, control 2.3, turn 1.3 s, first token 0.53 s); the table's
   overhead re-measured at **6.2 s, n=3** (`menu_policy.OVERHEAD_SINCE` drops the pre-fix delivery); and a ClaudeSupervisor
   chat turn through HAI went McCoy → Spock → **route `fellowship`, answered by rigby in 8.8 s** ("391"). The
   Fellowship is an interactive destination, no warm server needed. The chat turn's total was 31.7 s: **McCoy's own
   decision took 10.3 s** (10.8 s in the morning's proof) against the 0.18 s measured in his advisor tests — the next
   lever on a simple turn, a finding, not built (journal 10-23 § Opus).
4. ✅ **10-23 08:52 LIVE (gate 3056/2 green 08:49, recreated 08:52, bytes verified in the container, baseline
   `b8c014c`; the overhead price of item 3 shipped in the same image)** The controls (design § 5, D1–D6): `MOTHERSHIP_FORCE_USERS` retired — the 27B
   option for every user; a pin to a busy Mothership queues with the notice, the holder's elapsed time and the reroute
   offer with the table's own caveat; Nursing shows Model and Effort; `/modes` no longer names force users.
5. ✅ **10-23 09:06 LIVE (`a89ad3a` + `d706e2c`; gate 3065/2 green 09:0x after the pin caught a swallowed test header at
   3064; recreated 14:06:55Z, four runtime files verified in the container, baseline committed, lane released)** Area C
   step 2 (design build step 6): every placement-record row carries `cents` = J × the hour's price with its source
   (PSP when the sensor answers — it does, $0.0978 at 17:57 on 10-23 — else the $0.11 placeholder, said, never a silent zero); peak → the band sorted
   energy-first before the line and home ("slow and low"); extreme → Auto held with a notice naming the price, the
   line, the cheapest coming hour and "pick a Model" (pins still answer, D3). **The peak and extreme lines: SET 2026-10-24 13:10 CT on the owner's anchor (*"usually utility themselves call out higher energy prices, like they text me about it. That's a good starting point"*): **PEAK $0.12** = the utility's PSP High Price Alert trigger, measured from his alert texts against 120 days of hourly prices (lowest hour ever listed 12.5 c, highest left out 11.7 c); **EXTREME $0.36** = the top 1 % of hours in that history (p99: 43 of 2,880, summer evenings; the $1.00 hour of 10-13 was one) — `Datasets\Project34-Menu\utility_psp_hourly.md`. `docker-compose.override.yml` env (backed up), container recreated, read back in force (bands normal / peak / extreme at 5 / 13 / 40 c; now 1.9 c = normal). The first live hour at or above a line is still to come - this autumn's curve runs 1–9 c; the proof so far is the unit decoy + the read-back.**
6. ✅ **already built — verified in the code 10-23 09:00, nothing left to write:** B11's "owed CALLER" line (10-21
   15:33) predates the B11b window wiring that landed the same afternoon (16:05 proof). `helper_window.py` saves the
   slot on BOTH yield routes — a waiting order taking the card (line 587, `save_slot` before the yield; proven live
   10-21: an order made the idle window yield and it SAVED) and McCoy's LEND (lines 159–187) — and the next window
   restores it and retires the file (line 573, `restore_slot` removes it either way). The register's 10-21 "Next
   (owed)" sentence was the stale line; this item closes on that evidence.
7. ✅ **10-23 08:53** qwen3-vl:8b on BigHonker: tested context 15,360 (fit probe, 790 MiB ambient) in `TESTED_CTX`; the
   read-rate order `speedcheck-bi-qwen3-vl-8b-ctx15360-readrate.20261023-085222` DELIVERED; the refresh folds it.
8. ✅ **10-23 09:4x** C6 / DoD 7 written up from the measured rows + the shadow log as it stands — [[34-Closeout]] § "C6 /
   DoD 7" (charter DoD row 7 points there). Shadow log read 09:2x: 106 rows; 19 human (the owner, all shadow 10-19/20, 6
   unsure → lead, 13 blank skips); 0 human Auto turns since active — the real-traffic half stays data-blocked, said.
9. ✅ **10-23 09:4x DRAFTED — [[34-Closeout]]** (what shipped, what the measurements decided, what was disproven, what is
   handed off, what is unverified, the owner's calls); charter DoD table updated. ⬜ **Left for the close itself (after
   list B):** the registry row, CURRENT-PROJECTS header, PROJECT-BACKLOG § #34 DoD — written when the owner rules, so the
   library never says "closed" before he does.

**B. the owner's rulings (each one line; the plan proceeds on the rest meanwhile):**

> **Opus's recommendation on each (10-23 09:50), for a one-pass answer:** Phase 9 → close as satisfied by
> construction · C3/C4/C5 → #35 · McCoy for every user → AFTER his 10 s decision time is fixed (it would add ~10 s to
> every user's simple turn) · parked energy items → the backlog, outside #34 · placed-delegate pieces → leave the lens
> path, noted · A1 other-reviewers arm → drop, as B5 decided · step 2 lines → stay unset until the price pipeline is
> back, then derive from the home's history · Sundae's LV → leave at 157 GB; the deploy-day prune keeps it at ~80 % ·
> `fleet_deploy` suite gate → build it inside the close (small; it is how a red suite hid today) · McCoy's 10 s → look at
> it inside the close (it gates the "every user" ruling).
- **Phase 9 "generalize upward to Starfleet"** — McCoy already advises at Starfleet level (triage live for Kirk;
  placement advice is Spock's input, blocked on multi-user data). Close Phase 9 as satisfied by construction, with
  the data-blocked advisor roles handed to #35, or keep #34 open for them?
- **C3 placement advice · C4 decomposition advice · C5 a lab-trained decision model** — all blocked on data → hand
  to #35 Native Intelligence (its charter is exactly "train the judgment half")? **→ #35 (ruled 10-23) — and NOT "blocked on data": the lab holds thousands of Claude transcripts across three
  stores (the owner 2026-10-24: "we have thousands of transcripts with you to mine from for data"); the 119-request replay was cut
  from them. The two-layer design's MECHANISM pieces stay in #34 (area C step 3, reopened 10-24 — top block); #35 trains
  judgment on what they log. HAI 2.0 ships on what #34 built (nothing half-built visible to users); #35's training — a custom-baked McCoy — comes AFTER HAI 2.0 drops. the owner, 2026-10-24 afternoon: *"that's making a model from scratch when we already are supposed to have models that work for now. Custom baked McCoy is maybe a better one for a later day after I drop hai 2.0, since McCoy is already new to hai 2.0"*.**
- ✅ **McCoy ACTIVE for every user (`*`)** — DONE 2026-10-23 (D below; `MCCOY_ACTIVE_USERS=*,ClaudeSupervisor`). Was: the owner + ClaudeSupervisor; the shadow log's human rows are 6 (all
  unsure → the lead, which is what active would do). Widen now, or after more shadow rows?
- **Energy items parked 10-17** (per-order whole-box joules, HA energy entities `unknown` since 09-25, BigHonker's
  APC replug) — stay parked / move to the backlog, outside #34's close?
- **The placed delegate's pieces onto the weighing** (today they use the one-lens path) — ship inside the close, or
  leave the lens path (it works) and note it?
- **The A1 "other reviewers" arm** (#37 hand-off; B5 decided NO with one reviewer) — run it, or drop it as decided?
- ✅ **Step 2 numbers — SET 2026-10-24 13:10 CT on the owner's anchor (*"usually utility themselves call out higher energy prices, like they text me about it. That's a good starting point"*): **PEAK $0.12** = the utility's PSP High Price Alert trigger, measured from his alert texts against 120 days of hourly prices (lowest hour ever listed 12.5 c, highest left out 11.7 c); **EXTREME $0.36** = the top 1 % of hours in that history (p99: 43 of 2,880, summer evenings; the $1.00 hour of 10-13 was one) — `Datasets\Project34-Menu\utility_psp_hourly.md`. `docker-compose.override.yml` env (backed up), container recreated, read back in force (bands normal / peak / extreme at 5 / 13 / 40 c; now 1.9 c = normal). The first live hour at or above a line is still to come - this autumn's curve runs 1–9 c; the proof so far is the unit decoy + the read-back. Was 10-23: OUT OF SCOPE ("we will use a placeholder for now"), lines unset, PSP feed live.** Was: $0.11/kWh placeholder as said; the peak and extreme lines come from the home's price history
  (a read of the HA history), or a value you name now.
- **Sundae's root volume (10-23):** the VG has 43 GB unallocated on the 200 GB data disk the owner moved it to on 10-10,
  but that disk is a growing file on Softserve's `E:` (155 GB now; `E:` had 69.5 GB free). Extend the guest LV into
  it (the file can then grow toward ~25 GB free on `E:`), or leave it at 157 GB? Pruning already took `/` from 100 %
  to 81 %. Also: `sda3` (77 GB, the old PV, empty) — reclaim or leave.
- **`fleet_deploy` runs no suite on a GPU-factory deploy (10-23 finding):** its Tolkien branch gates on Tolkien's suite;
  its GPU branch only checks bytes and new PIDs, which is how the 08:11 deploy left the foundry suite one test over
  its pin, unseen until 09:4x. Teach it to run the foundry suite per factory and refuse on red / pin mismatch?
- **McCoy's live decision time — INVESTIGATED 10-23 10:0x (the owner: "Let's do mccoy's investigation first"):** every one
  of McCoy's **17 live calls** (10-22 → 10-23, Softserve's ollama log `C:\ProgramData\HAI\ollama-serve.log`, `POST
  /v1/systemone`) took **6.7–10.8 s**, and every one contained a **cold load of tev1** (`llama-server started in
  5.8–7.0 s`), sometimes after a 2–3 s GPU-memory refresh (09:37: the discovery watchdog timed out first). The answer
  itself is ~0.1 s — his tests' 0.18 s warm figure is right; he has **never once been warm in production**. Cause, read
  in the same log: **Softserve holds ONE model (`OLLAMA_MAX_LOADED_MODELS=1`)**, and each HAI turn's RAG embed
  (`nomic-embed-text`) lands there ~10 s after McCoy whenever BigHonker's card is held (`EMBED_HOSTS` = BigHonker,
  then Softserve) — so the embed evicts tev1, and the next turn's McCoy evicts the embed (10-22 12:24–12:27: tev1,
  nomic, tev1, nomic … four turns in a row, calls 21–104 s apart). Against the design: checklist **E1** — the owner 10-22,
  *"The only model I've said that we can keep sort of loaded, is the model that runs under McCoy's hood"* — was never
  implemented, and with one model slot it cannot be; HAI-Agent-Surface-Design's bar is *"loads and answers in seconds
  regardless of Plex"*, measured warm 0.18 s / cold 26.4 s. The CLAUDE.md "8 GB = 1 worker" law is about parallel
  slots (`NUM_PARALLEL`); two MODELS on a card is `MAX_LOADED_MODELS`, which needs the per-card arithmetic: tev1's
  measured peak 5.9 GB (at the systemone default context — a ~400-token request does not need it), nomic small, Plex
  median 0.48 GB / max band 2.4 GB on Softserve's 8 GB card. **Options for the owner (nothing built):** (a) keep embeds off
  Softserve while McCoy lives there; (b) measure tev1 at a request-sized context, then let Softserve hold two models
  with McCoy's kept loaded, if the arithmetic clears Plex's max band; (c) both. Recommendation: (b) — it is the design
  as written (McCoy's model the one that stays loaded), and it starts with a measurement order, not a setting.
  **10:2x — it is a SEQUENCING issue inside ONE turn** (the owner asked; `scratchpad\softserve_turn_sequence.py`): on
  Softserve a McCoy-decided turn runs **embed (RAG retrieval) → McCoy (tev1) → embed again (tool selection) → the chat
  model**, and with one model slot each step evicts the one before: 10-22 12:24 nomic warm 44 ms → tev1 load 5.8 s →
  nomic reload 1.3 s; 10-23 09:08 tev1 6.8 s → nomic 1.3 s → qwen3:8b. Up to three model swaps per turn, all
  self-inflicted: the turn evicts its own Starfleet models. The embeds land on Softserve because `EMBED_HOSTS` falls
  back from BigHonker whenever BigHonker's card is held. Ollama runs every loaded model as its own `llama-server`
  process, so "evict" = end that process and "load" = start a new one, read 4.3 GB (mmap is off on Windows CUDA), init
  CUDA, fit — there is no lighter "reload". **The 3 s GPU-discovery watchdog timeouts** (3 in two days: 10-22 01:53,
  10-23 08:27, 09:37) each came on the first load after Softserve's ollama had been idle 4–15 min — likely the card
  waking from idle on ollama's free-memory probe, which then proceeds on old values; likely, not proven. Fix shape, in
  the lab's own intent (a card swap never throws work away; McCoy's is the one model that stays): keep Starfleet's two
  small models (tev1 + nomic) resident together so a turn never evicts its own, and put the turn's embeds side by
  side instead of on both sides of McCoy.
  ✅ **FIXED + LIVE 2026-10-23 10:43–10:45 CT** (the owner: *"Let softserve run two models at current context size, and make
  it have to use one at a time if plex's transcode usage trends to rising above what can hold both"*; his 16 slots
  kept): Softserve `OLLAMA_MAX_LOADED_MODELS=2` (a deliberate per-card setting); the health daemon's **duty 7** drops
  to one model (McCoy's kept) when the card's free memory, projected one poll ahead, falls under the 512 MiB fit
  reserve — live decoy PASS; McCoy's call sends `keep_alive -1` on Softserve only (orchestrator `dcd0bca`, gate 3067).
  **Through HAI's front door: McCoy 0.41 s and 0.40 s** on consecutive turns (was 6.7–10.8 s on every call), both
  models still resident after each turn's embeds. The embed reorder was not needed once the two share the card.
  ⚠ **Remaining gap (turn 3 of the proof): each new chat's AUTO-TITLE** runs on a 9B/8B with thinking ON (1,711 tokens
  for a five-word title) through a chat WINDOW — on BigHonker, or on Softserve when BigHonker is held — and a qwen3:8b
  window on Softserve evicts tev1; McCoy was then placed on BigHonker cold: **10.92 s**. the owner's call: titles off the
  window / no thinking / not on McCoy's card. **→ the owner 10-23: "Can we not auto title with a tablet or something else in
  the lab?" — BUILT + LIVE 11:26 CT** (orchestrator `107ea1d`, gate 3071): a title is a background chat-turn ORDER to
  the Fellowship (Spock's weighing with no interactive line → rigby's 1.5B by energy), priority 0 so it queues behind
  real Fellowship work; the GPU brains only when the Fellowship cannot serve. **Re-proof PASS 11:30:** three turns
  (Fellowship, lead, lead) — McCoy 0.20 / 0.34 / 0.28 s, every call on Softserve with tev1 resident after each, and all
  three titles made on the Fellowship. **McCoy's time: CLOSED.**
  Runbooks: Ollama-Host-Operations § Softserve config, Foundry-and-Rescue-Daemons § "Duty 7".

**C. CHARTER CROSS-CHECK of this close-out list (2026-10-23 ~11:45 CT, Opus; the owner: "compare the registers close-up list
to the project charter").** Read: [[34-CHARTER]] end to end; this list; the Phase 7 + 8 closure checklist below; the
10-20 Codex alignment below; a library-wide search for every charter item the list does not name.

*Covered by the list or already closed (no action):* DoD 1–7 all ✅ (DoD 7 answered today) · Phases 0–6 closed, 2/3
re-closed 10-18 · Phase 7's rule (live since 10-10) + area C steps 1–2 (weighing, price of the hour) · area X McCoy
triage live (home moved rigby → Softserve by the owner 10-21) · hard constraints held (local-first, deterministic-first —
McCoy advises, Spock decides) · Tolkien's grading on the one grader (the Tolkien image copies `grade_jobs.py`).

*In the CHARTER, missing from the close-out list — each needs a disposition before the close (the owner's rulings):*
1. **Phase 7 + 8 closure checklist** (built 10-17 under the owner's "no new phase until every earlier one is 100 % closed"):
   still marked open/in-work — **B4b** (now done: overflow live 10-22, decoy PASS today), **B10** (A3B priced on rigby
   and BigHonker; Phase 3 closed 10-18 on it — the mark is stale), **B11** (closed by B11b 10-22 + today's caller check —
   stale), **C1b** (McCoy replay, measured 10-19), **C7 code difficulty read from the request** (first measurement
   10-19; needs McCoy's difficulty line + B4b — genuinely OPEN). Marks to update; C7 needs a ruling (close as measured,
   or #35).
2. **S10 — rigby lead + the tablets as sub-agents** (charter: "sits between Phase 7 and Phase 8"; DoD 4's production
   selector "needs a checker such as S10b"): S10a computed 09-28, **S10b PRELIMINARY on one lead order (10-09)**. Not
   in the list: close as preliminary, or one more S10b order, or → #35/#36.
3. **Area C pieces the charter names that were never built or ruled** (backlog §#34 scoping: "the genuinely MISSING
   pieces"): **ETA** — only the queue notice's elapsed time (no completion estimate); **deadline routing** — urgency
   yes (McCoy's kind), deadlines no; **p95 completion distributions** — not built (the table uses medians + Wilson
   lower bounds); **empirical feedback loop** — partly: the table re-derives from every delivery every 15 min (today's
   Fellowship overhead is that loop working), but live outcomes (McCoy's calls vs what served well) do not feed back
   yet (= C5). **Zero mentions** in register / design / closeout for p95, ExecutionPlan, opportunity/disruption cost.
4. **The charter's "smaller" list (status 10-03), none dispositioned since:** `verify_cost` (a menu field for where a
   check ran) · **VLM ladder on rigby** (image support in Tolkien's kind `llm`) · **per-tablet energy** · **judge
   calibration** (parked "to the orchestration phase" by the Hard-Bench closeout; B5 later decided no critic gain) ·
   **job-specific families for the specialists** (captioning for huihui/joycaption, medical for medgemma; their generic
   cells held). Library search: no record of any being done or dropped.
5. **Phase 9** — already on list B.

*On the close-out list, NOT charter scope (lab hygiene found while working — suggest the backlog, not #34's gate):*
Sundae's LV extension · `fleet_deploy` running no suite on GPU deploys · the embeddings follow-up (already filed).

**D. DISPOSITIONS — applied from the frame the owner already set (2026-10-23 ~12:00 CT; the owner: "I've laid this all out
before… McCoy is supposed to help advise on multiple layers… read from the documentation and journals"). No new
rulings asked; each line cites its source.**
- **The split that settles most of it** — [[34-CHARTER]] § "End state": *"#34 builds the MACHINE half (the priced menu →
  Phase 7 placement → area C), #35 trains the JUDGMENT half into local models"*; McCoy's roles — [[HAI-Agent-Surface-Design]]
  § "The ADVISOR": Kirk (triage, decomposition), Spock (placement, which helper takes which piece), Tolkien (class,
  tries, escalation), all (result comparison, escalation). **Machine half: built for Kirk's and Spock's roles** (the advisor call,
  Spock's door, the placed delegate, the weighing, the Fellowship form) — **for Tolkien's: a door since 2026-10-23 evening, SHADOW only** (`/mccoy/tolkien`: worker / tries / escalate logged beside Tolkien's own decision, never acting, no measured threshold yet; before it Tolkien had no door at all, checked ~14:10 CT). the owner, 2026-10-23: the project was meant to build on the tablets and generalize UP; it built McCoy at the fleet and now generalizes DOWN - one McCoy advising both levels is fine with him (*"it might just make the most sense for us to keep McCoy. I'm really not against that"*); the name Eru Ilúvatar is held for a Tolkien-level advisor if one is ever measured to be needed. **Judgment half beyond triage → #35** (C3
  placement, C4 decomposition, C5 a lab-trained model, Tolkien's advice, result comparison, C7's measured-truth step).
- **Phase 9** → RULED by the owner 17:2x: CLOSED, answered by evidence, not built — see § E (the noon "closed by construction" line is archived).
- **Closure checklist** (D1 below): B4b ✅ (overflow live 10-22, decoy 10-23) · B10 ✅ (A3B priced on both; Phase 3
  closed 10-18) · B11 ✅ (B11b 10-22; caller path verified 10-23) · C1b ✅ measured · C7 ✅ measured — finding added to
  DoD 7: a 4B reading only the wording cannot judge coding difficulty; unsure goes to the lead (safe) — its next step
  (measured truth for real requests) → #35.
- **McCoy for every user** → do it: checklist C1 (*"the end users can just pretty much pop it on auto and not have to
  think about anything"*) and D (every user the same controls); the "widen after more shadow rows" condition was a
  session's note, not the owner's. McCoy now answers in 0.2–0.4 s, and unsure still goes to the lead.
- **S10** → RULED by the owner 17:2x: accepted as answered (three replicates) — § E.
- **Area C extras** — PROJECT-BACKLOG §#34 scoping already says opportunity-cost / contention and "factory telemetry /
  ExecutionPlan metadata" EXIST in `scheduler.place` / capability + the deck; urgency + energy-in-placement + residency
  are built; the empirical loop runs (the table re-derives from deliveries). Not built: deadlines, p95 distributions, an
  ETA beyond the holder's elapsed time — the charter's own guardrail is "does it make routing measurably better", and no
  measurement calls for them → backlog, not #34's gate.
- **Charter's 10-03 "smaller" list** → judge calibration closed by B5 (no critic gain); specialist families stay held
  (the Hard-Bench spec's specialist ruling); `verify_cost`, VLM on rigby, per-tablet energy → backlog.
- **NOT owed — the owner 2026-10-23: *"McCoy is the singular exception, for the record. I don't believe I have outlined any
  other special cases yet."*** Models are not kept loaded; McCoy's is the only one that may stay (Softserve's second
  model slot exists for him alone; the embed model still unloads at the box's keep_alive). Checklist C5's old line
  ("Tolkien keeps chat models WARM on the nodes … — DECIDED 10-07") is resolved by it: the owner's 10-07 ask was several
  chats at once, which the node pool delivers with per-order servers (8.5–9.1 s a rigby turn). A resident embed server would also be a second
  exception — the embeddings follow-up says so.
- **✅ CLOSED 2026-10-23 — the Fellowship hosts several chats at once (Tolkien's node pool, built and proven: two orders on gandalf + aragorn at once), his free-spot report weighed by Spock, and the overflow proven 15:28 CT (with rigby busy, the third chat went to BigHonker on the record 'rigby busy: 2 ahead')). Was: the Fellowship served chat turns ONE AT A TIME.**
  the owner 10-07 (verbatim, transcript `fb5bc5f2`): *"One of the cool parts about us having a lot of tablets is that we are
  supposed to be able to host multiple chats at the same time. Soooooo….sort of option 1?"* — Option 1 as Claude had
  worded it was "Tolkien keeps a model warm on each tablet". **Measured 13:00 CT** (`scratchpad\probe_fellowship_concurrency.py`,
  three Fellowship chat-turn orders submitted at the same moment through Kirk's desk, ClaudeSupervisor): all three
  answered correctly, **one after another** — run windows 0–7 s, 7–13 s, 14–20 s; Kirk saw the answers at 9.7, 16.2 and
  22.7 s. Every turn went to rigby's lane (turns 0.4–1.3 s; a tablet turn is 8–25 s), and Tolkien's fleet lane is
  "serial in ORDERS" by design (`tablet_runner.py` docstring), so the tablets add no second chat either. Multi-user is
  still SERVED today by Spock's overflow (a busy home → the next row), so no user is refused; what is missing is the
  Fellowship itself hosting several chats. **CAUSE (the owner 2026-10-23: *"if Tolkien has 10+ tablets, the orders are SUPPOSED to only take the tablets they need so other things can come through using the other ones"*):** Tolkien has no per-tablet scheduling and never had it — [[Fellowship-Order-Taker-Design]] § 3 "serial in orders per lane and parallel in items" (August, the GPU factory's one-card lane copied onto a fleet), the rigby lane split rigby off 09-28, and nothing ever gave each TABLET its own claim; the GPU factories' rule is one job per CARD, so the faithful mirror is one job per NODE. the owner has asked for this shape since 08-30 (*"we have seven tablets … so we can be running multiple tests at the same time"*) and 09-27; the lab answered inside ONE order (item fan-out, `map.window` cohorts) and never across orders. Warm vs cold is a separate, smaller question. Fix proposed to the owner: the fleet lane deals each order the nodes it needs and starts the next order on the free ones (rigby one node under its lock; Voltron still takes everything; speculative re-takes stay inside the order's own nodes; a chat turn takes a free node when rigby is busy).
  **BUILT + LIVE 2026-10-23 13:39 / 13:48 CT** (Tolkien gate 935 → 940, both as predicted; `fleet_deploy`, both containers verified; [[Fellowship-Order-Taker-Design]] § 3): the node pool (`take_nodes` / `give_back`, `dispatch_fleet`), the fleet claim listing every running order, the watchdog per order, the tile's `N orders` line, the deploy tool stopping every running order. **Proven live:** two one-batch echo orders submitted together took one tablet each (gandalf 13:50:17–21, aragorn from 13:50:19, overlapping), both shipped, the second's health probe skipped the busy gandalf. **Chat turns:** the first build moved two of three turns to class-A tablets — concurrent, but each ~26 s and quality 0.625 against the 0.90 band (the owner's overflow rule: similar quality) — so a move now must pass Spock's own weighing (band + faster than rigby's line); re-proven 13:50: all three stay on rigby (9.7 / 16 / 22.7 s), Tolkien logs the reason. Per-worker desk overhead in the table (`menu_policy.fellowship_overheads` by `placement_row.box`): rigby 8.6 s, a tablet 28.6 s. **Left for the owner (a placement-design question, checklist C2/E3):** several CHATS at once still queue on rigby, because no Fellowship tablet row is inside the chat band and Spock cannot see rigby's line — the in-band overflow (BigHonker qwen3:8b, ~2 s) is never chosen for user 2+. A hand-back cannot do it (Kirk pins Fellowship chat orders `only: fellowship`, so the desk would HOLD them); the shape that fits is Tolkien publishing rigby's line and the weighing pricing it as wait. **✅ BUILT + LIVE 2026-10-23 (the owner: "Hit both please"):** Tolkien's free-spot report in every run-state push, Spock's weighing pricing rigby's line as wait (`scheduler.fellowship_load` + Kirk's in-flight count), and McCoy's door for Tolkien (`/mccoy/tolkien`, shadow) — orchestrator gate 3087, Tolkien gate 949. Live proof: PROVEN 2026-10-23 15:28 CT on a quiet lab (three ClaudeSupervisor Auto chats 1.5 s apart, McCoy sure of all three): two queued on rigby (13.6 / 18.4 s); the one weighed while two were in flight went to BigHonker qwen3:8b, its placement record reading "rigby busy: 2 ahead of this turn (+24 s wait; Tolkien: rigby busy, 0 in line, 11 free tablets; Kirk: 2 Fellowship turns unanswered)", past the interactive line; McCoy's shadow rows beside every Tolkien decision. GAP FOUND: that overflow turn took 37.9 s against a 12.0 s price - a GPU chat turn served through a NEW window pays the window's desk-order overhead and HAI's full agent prompt (9,418 tokens read), and neither is in the GPU row's price (the Fellowship rows got their measured overhead today; the GPU rows' window overhead is unpriced - class 7). [[Fellowship-Order-Taker-Design]] § 3.
- **Other list B:** the placed delegate's pieces → BUILT onto the weighing 10-23 evening (§ E item 6); the A1 other-reviewers arm → dropped (B5); energy items + step 2's peak / extreme lines → OUT OF SCOPE (the owner 10-23); Sundae's LV + `fleet_deploy` suite gate → not #34 scope (the suite gate is a guard hole: fixed, doctrine 6).

**E. GAPS THAT STOP THE CLOSE — status 2026-10-23 ~17:10 CT (Opus; banked for the next session)** (the owner: *"you are
not allowed to close out 34 until you are 100% confident that we have 100% satisfied the charter and the design doc and
everything that I've said over the course of the project and intent"*). Sources: the charter, the do-not-collapse
checklist, [[HAI-Agent-Surface-Design]], and a sweep of **every the owner turn 08-25 → 10-23** in all three transcript stores
(4,097 turns → 817 stating a requirement, read in full; working files `scratchpad\sweep_notes*.md`,
`sweep_result_34.md`, `sweep_status_34.md` of session 33992c58). The first version of this list is in
`Archive\34-register-history-2026-10.md`.

*BUILT + LIVE tonight (orchestrator `80b1947` `c7b882e` `e61ad6e` `cfdca68`; gate **3109/2**; each deploy: whole-tree mirror
audit clean, gate at the predicted count, container bytes verified, workspace synced; Tolkien rebuilt via `fleet_deploy
scheduler.py`, all four factories in parity; A1 payload shipped `fleet_deploy --a1`, in sync on all three GPU boxes):*
1. ✅ **Busy-pin queue notice (checklist D4):** place, rule, the holder's elapsed time, a **rough ETA from hold lengths
   the slot has SEEN** (or why there is none: "no estimate yet", "a long job is ahead and has no set length"), the
   reroute with its measured caveat; re-shown when it moves. **LIVE PROOF PASS 16:4x** (`scratchpad\proof_queue_eta.py`:
   a pinned 27B turn queued behind another was told "1 ahead … your turn in about a minute … the 8 GB tier answers
   general questions at about 89 % of the lead's measured quality"; started 28 s in). Its one blemish ("about 1
   minutes") fixed + pinned.
2. ✅ **The queued wait has no clock** — `MOTHERSHIP_QUEUE_TIMEOUT` (1,860 s) retired; a waiter leaves on its turn or a
   Stop (13a; the owner 09-29, 10-19).
3. ✅ **Found + fixed: queue entries were found BY VALUE** — two equal waiters let a Stop remove the OTHER's place and
   strand its own at the front, freezing the queue. Now by identity; the regression test fails on the old code.
4. ✅ **Auto reads coding as coding (checklist C3), narrowly:** McCoy's "coding" sure at 0.90 on a turn he is sure is
   BELOW the lead weighs the 8 GB coding rows (`mccoy_coding_family_score.py`: 42 of 42 code requests at 0.90, all 36
   coding tasks caught). NOT on the lead tier: its coding rows' home is qwen3-coder:30b thinking off (function-sized
   tasks), and a turn McCoy sends to the lead is one he judged hard - the 3.8's. His other families stay off (topics,
   63 %).
5. ✅ **New-window price (checklist B1 + the 37.9 s vs 12.0 s overflow turn):** a NEW ollama window's first turn records
   open → first output Kirk read, with the engine's own load / read (`State\chat-window-first.json`, last 20 per box);
   the chat weighing charges the median (load left out) on a box with no window for the row's model. Accrues from the
   next new window on each box; until then the note says "not measured on <box> yet".
6. ✅ **Desk orders with `urgency` are placed by the weighing** (one placement rule, recursively): computed once
   (`scheduler.weighed_entries`), the row that ranked the host fills its order; the A1 placed delegate sends urgency.
   FINDING: the placed delegate exists ONLY in the A1 harness - production chat has no delegate tool (10-11: the lead
   works alone).

*Checked, no change needed:* the stuck-tablet re-take under the node pool (idle in-order tablets steal a straggler's
batch every 5 s; a one-batch order never had a median to judge against).

*Out of scope by the owner:* price lines and the price pipeline (*"we will use a placeholder for now, that's out of
scope"*). FINDING for the docs at the close: `energy.price_now` reads the utility PSP live right now ($0.0398 at 17:00,
source psp) - the "$0.11 placeholder while the pipeline is down" text across the #34 docs is STALE.

*RULED by the owner 2026-10-23 17:2x CT (the "Fable consult" was this session, on Fable; the owner: "I think that sounds good
… I need you to help me get these closed properly. We can worry about read speed ups for another project. The external
readers via networking I mean"). Each ruling, with the evidence it rests on:*
- **Phase 9 — CLOSED, answered by evidence, not built** (never started; a session's "closed by construction" was
  wrong). Of the four jobs one layer up ([[36-Closeout]] § Hand-offs): reading help from a helper box — OUT (the owner 10-13);
  the context archive adds room, not speed (Flash-Next holds 262,144); roles per device = the placement (built);
  **drafting for the 3.8 from an 8 GB box over the network — NOT measured here, by the owner's word: external readers /
  drafters over the network are ANOTHER PROJECT.** Evidence on file: built-in MTP beat an external drafter (published,
  RTX 3090; #36 on rigby), and an RPC hop per round only widens that. Honest line for the record: not measured on the
  7900 XTX.
- **S10 — ACCEPTED as answered:** three replicates of the 10-09 lead order, 14/24 each; the lead's checks add no
  solves over first-candidate, false pass ≤ 3.3 %. Which helper takes which piece = judgment → #35.
- **McCoy's placement advice (C3) — ACCEPTED on the owner's 10-21 rule:** the model is for where there is no hard-coded
  answer, and the weighing is one. McCoy advises placement only where the weighing cannot decide (a tie, a missing
  row) → #35, narrow door; not a #34 gap.
- **The two unmeasured items — SPLIT:** one plain-English unpinned request that Starfleet decomposes end to end (the owner
  10-16) → **RUN inside the close** (one ClaudeSupervisor turn, records read); an 8 GB lead delegating DOWN → not
  measured — the solo lead verdict (10-11) says an 8 GB lead is not the production lead, so delegation from one is not
  a role we ship.
- **The Qwen3 reranker — PARKED outside #34** (retrieval quality for HAI's library search, not placement). The check
  order `7f31c9e33091` (BigHonker) is read when it lands; result + "what it would take" → PROJECT-BACKLOG.
- **Embeddings onto the menu — PULLED INTO #34**, narrowly (the project's own thesis: a per-turn call routed by a
  hard-coded list instead of the matrix): an **embed family** priced on the three GPU boxes, placed by the same
  weighing (`scheduler.occupied_for` already skips a held card); Fellowship rows excluded by their measured desk
  overhead (8.6 s rigby / 28.6 s a tablet against 0.1–0.3 s on a GPU); no resident embed server (McCoy's is the one
  exception). The eviction harm is already gone (Softserve holds two models since 10:45); this is consistency.

*OWED BEFORE THE CLOSE (in this order):* (1) ✅ **17:5x** the embed family — priced on the three GPU boxes (Mothership
17.9 ms / 1.03 J, Softserve 23.6 ms / 1.2 J, BigHonker 28.9 ms / 0.99 J a query; agreement 1.0000), on the menu
(`embed`/`gpu`: the 8 GB boxes home, Mothership overflow), placed by the weighing (gate 3120/2, Tolkien 949, payload on
all three factories), proven live 17:56 (note + placement record); the proof caught a warm box priced cold (model tag) —
fixed; [[34-Phase-7-Placement-Policy-Design]] § 3 + § 6 step 7. **19:1x — the Fellowship PRICED too** (the owner, on my
roundup leaving rigby's 1060 and the tablets off: *"Eh we can price it, should be quick"*): Tolkien gained an `embed`
order kind (`tolkien_llm.run_embed`, gate 959, both containers recreated); rigby's GTX 1060 (the CUDA build, HAI's own
nomic weights copied to rigby's model store, sha-verified) measured 1,824 queries in a 30 s loop: **15.0 ms a query,
0.81 J (plug marginal), 3.0 s server start, vectors identical to the GPU boxes' (min cos 1.0000)** - a HOME row with its
10.2 s desk overhead stamped, so the weighing rejects it for an interactive turn by MEASUREMENT (past the 0.15 s line)
and ranks it FIRST on energy for a background batch. Not priced: a tablet class (Tolkien's embed kind is rigby-only;
a node would need its own llama-server --embeddings) - leftover with that reason; and the background caller (the
library reload's corpus embeds as a Fellowship ORDER) is not built - leftover. Three submits: the first put the weights
in the tablet store (`/mnt/fellowship/models`, not Tolkien's `/models` = `/home/owner/models`), the second lost the
vectors to Tolkien's own results record landing on the same filename (fixed: out.json beside the record); (2) ✅ 18:1x the plain-English end-to-end run (`scratchpad\proof_end_to_end.py`, ClaudeSupervisor, Auto, nothing pinned):
*"Which of the three GPU factories are free right now and what did each one last run? Then tell me in one sentence which
one you would send a long captioning job to tonight, and why."* — McCoy: job / ops, unsure (0.35) → the lead; the
turn's embeds placed by the menu (record 18:12:49, `embed`/`gpu`, bighonker home first); Spock → Mothership; the lead
took 4 steps with 6 tool calls (`fleet_status` ×4, `check_host`, `catch_up`) and answered with the live deck state of
all three boxes (incl. "Mothership busy — running HAI's own 27B chat window: that's this conversation") and a pick,
171 s end to end (cold 3.8 load ~45 s; 18.8k prompt tokens; status lines streamed throughout). FINDING from run 1
(18:06, *"is electricity cheap right now, which hour tonight is cheapest for a long GPU job"*): routing worked the same
(job → lead, 29 tools selected) but HAI has NO tool that reads the house's price and the HA device tool was not among
the selected 29, so the lead answered from the docs and said it could not read the live number — a HAI tool-inventory
gap (#37 lineage), not a placement one; for the owner to place. Second finding: McCoy was unsure on both (tier 0.35) — a
sure "8gb" would have been minutes cheaper; his calibration is #35's.
(3) ✅ the reranker result read + parked (PROJECT-BACKLOG: the check order ran on Mothership, 4 of 4 right, ~0.9 s a
4-document rerank on CPU); (4) ✅ 18:1x the doc pass (`scratchpad\doc_pass_34.py` + `_34b`: 10 live docs, 11 passages archived verbatim) — register § D lines that § E overturned (Phase 9 "by construction", S10 → #35 as a
disposition, the delegate pieces "follow-up") → archive; checklist § G's three stale marks (ETA, family from the tier
only, pipeline down); [[34-Closeout]]'s "the owner's calls" (old list B incl. the warm Fellowship server) and its "unverified"
lines answered tonight (queue notice seen live; pipeline live); the four HAI 1.0 routing docs; the backlog's
"token streaming" entry (built by #37); the price-pipeline "placeholder while down" text; the charter's stale text
listed below — every superseded line MOVED to `Archive\` verbatim (class 21); (5) ✅ 22:47 CT CLOSED on the owner's word: [[34-Closeout]] final, registry row, CURRENT-PROJECTS, PROJECT-BACKLOG § #34 → LEFTOVERS, memory.

*CLOSURE CHECKLIST (the owner 20:0x CT: "Let's get everything closed out"; items from my plain list, each with its proof):*
- ✅ **McCoy on REAL traffic** (C6 / DoD 7's data-blocked half; the owner 10-19 "mine my transcripts … retry with the new
  routing as testers"): 119 of the owner's real requests (111 from HAI's own history store - his chats to HAI, verbatim - and 8
  he described to Claude) replayed through `mccoy.triage` + `scheduler.auto_needs` + `weigh_rows` inside the orchestrator
  (read-only; nothing served) → `Datasets\Project34-Menu\real_requests_replay.md` (+ .jsonl). **Finding: McCoy is sure
  of the tier at the 0.90 line for 21 of 119 (18 %); 98 go to the lead by the "unsure ⇒ the 3.8" rule, including 14
  simple questions he himself called 8 GB / Fellowship work at 0.3–0.6** ("how many in a bakers dozen?", "what year is
  it?" → a cold 3.8). Safe by rule, costly; McCoy's own time is not the cost (median 0.19 s). What a lower line WOULD
  decide is tabulated (0.80: 31 sure; 0.70: 46; 0.60: 65; 0.50: 84) - whether it would be RIGHT is #35's measured-truth
  calibration, with this dataset as its input. 37 of the 119 are render asks (HAI's render intent, before placement).
  Five of them were then run LIVE through the front door (table at the end of that file).
- ✅ **The busy-pin notice with TWO users** (`proof_queue_two_users.py`, run 3, 20:2x): ClaudeSupervisor held the 27B with
  an 800-word answer; test-homelab pinned the 27B 12 s later and was told *"Mothership is busy - you're queued: 1 ahead of
  you (quick requests go first; … held it 11 s; your turn in about 2 minutes)"* with the reroute caveat, waited 45 s, both
  answered on Mothership. Run 1 (5 s stagger) showed the second user reaching the slot FIRST while the first was still in
  its pre-work - the queue worked the other way round. Defect found + fixed the same pass: the "has held it N s" number
  froze at 14 s while the stream re-sent the line for 40 s → the line is re-told at least every 30 s of the holder's time
  (`_LeadSlot.HELD_RESHOW_S`, test pinned).
- ✅ **Time to FIRST VISIBLE OUTPUT on every GPU row** (checklist B1/B5; the owner 10-22: speed is also the time until the
  model shows something): every ollama window turn now records its first output (`chat_window.record_turn`,
  `State\chat-window-turns.json`, new windows flagged), the menu stamps the warm-window median per box × model on its GPU
  rows (`menu_policy.stamp_first_output` → `first_out_s`), and the weighing shows it beside the total ("first output ~N s")
  and records it. **The ordering stays on the total time** until the owner rules which half a user minds (B2) - priced, not
  yet weighed. Gate 3124/2, deployed 20:19 CT; accrues from the next turns.
- ✅ **RULED + PRICED 22:45 CT (the owner 22:4x: "Probably" - price it; the bulk-embed door stays unbuilt, a desk-time project follows):** every Fellowship row on the menu now carries `j_order` = its measured desk overhead × the worker's marginal watts (rigby 10.2 s × 54 W ≈ 551 J; the tablet pool unmetered, None), and the weighing adds it to the row's J per request with the note "+N J per-order launch" (`menu_policy.stamp_fellowship`, `scheduler.weigh_rows`; gate 3125, Tolkien rebuilt). Interactive turns: unchanged (the Fellowship was already past the line on time); background simple turns lean to the 8 GB boxes until desk time shrinks - and because the number is re-measured from deliveries every refresh, the Fellowship comes back on its own when it does. **Hand-off, the owner's words: a follow-up mini project on "desk time / system time"** - tonight's baseline: rigby desk overhead 10.2 s, per-order server start 3.0 s, a tablet 28.6 s, Tolkien's inbox pickup 15–229 s, the 3.8's cold load ~45 s, a new chat window ~9 s to first output; McCoy's FIRST call after a long idle ~2.5 s (22:54, after 2.5 h idle - the card waking; 0.19 s on the next call, his model resident throughout). The original finding, for the record: bulk embeds to the Fellowship as an order - NOT built, the FINDING that led here: the leftover said rigby wins
  the library-reload batch on energy (0.81 J a query). Counting the per-ORDER energy the Fellowship row's J leaves out
  (the 10.2 s desk overhead + 3 s server start at rigby's 54 W ≈ 700 J per order), rigby breaks even only past ~3,700
  texts - a whole-library rebuild (3,293 chunks) does not; by TIME it wins past ~900. **The same gap is in every
  Fellowship row's J on the menu** (a chat turn's per-order launch ≈ 550 J beside its 97 J solve): class 7, an unpriced
  energy. the owner's call: price the per-order energy into Fellowship rows' J (it would move simple background turns off the
  Fellowship toward the 8 GB boxes - against the design's wish for the tablets' parallelism, so not a change to slip in),
  and whether the batch door is worth building for time alone.


*Charter text that is STALE (fix in the close pass, doctrine 19/21):* member table — area C "not started", area X "not
started — candidates installing" (both built); hard constraint "Champion-until-beaten: qwen3.8:27b is the reigning
heavyweight" (crown SHARED by job family since 10-18: Flash-Next agentic/long-context, the 3.8 quick families); end
state "user-facing surface … designed, not built" (built: #37); campaign map "#36 ACTIVE" (closed 10-16) and "mordecai
GPU ID" (resolved 08-28: the GT 730 identified and moved into rigby); the "Status 2026-10-03 evening … Still to do"
block (superseded — archive it); phase diagram Phase 2 "reopened 10-03" (closed 10-18).

## Current pickup and charter alignment — 2026-10-20 (Codex)

Detailed handoff requested by the owner: [[HANDOFF-Project34-37-Claude-20261020]]. Candidate hashes: `Claude Home\.p34-chat-stage\HANDOFF-SNAPSHOT-20261020.json`.

the owner asked: "Compare to project charter and phases plan, where do we stand?"

**Phase position:** phases 0–6 are closed, with phases 2/3 re-closed on 10-18.
Phase 7's placement rule is built, live and self-refreshing; the combined Phase
7/8 closure checklist is still open. Phase 8 has measured advisor results and
live McCoy SHADOW wiring, not completed active Auto routing. Phase 9 has not
opened. Project #37 is completed; its remaining integration hand-offs belong to
#34. Older charter/registry summaries that say area C/X or the HAI surface are
"not started" are superseded by the dated build/closure records below, the
Phase 7 design, [[37-Closeout]] and CURRENT-PROJECTS.

**Tournament decisions already made:** crown shared by job family — Flash-Next
for agentic/long-context lead work, the 3.8 for quick families. The A3B is priced
on BigHonker by role beside qwen3:8b; the 3.8's BigHonker speed was measured and
the owner closed the remaining role-cell work. Fellowship individual, ensemble and
Voltron phases are closed; parked ceiling/refinement work is not an unfinished
phase. B5 reviewer = no measured gain, B8 lead works alone, B9 existing
between-turn handover = no new mechanism. Those decisions stand.

**The remaining charter gap is integration and acceptance:** all model work,
chat included, enters the existing order lifecycle; existing Kirk/Spock/Tolkien
decisions, constraints and pins remain authoritative; calibrated advisor signals
can guide Auto without asking the user for benchmark labels. The priced policy,
placed GPU/Fellowship orders, Tolkien fallback, #37 streaming, Quick Resume and
quick-first/the owner-first slot already exist. Do not rebuild them.

**Current integration candidate is STAGED ONLY:** lifecycle/release corrections,
relay and batch-to-existing-LaneYield connection; foundry gate 579/579,
orchestrator 3003 with its pinned two skips. No integration deployment. The
staged pick_brain fast-model shortcut was a real mistake, removed; original
chooser restored and covered by a regression control. Installed source hashes
and stated runner freshness checked on all three factories; live orchestrator
still has the original transport. Current review/open implementation contracts:
[[34-CHAT-FACTORY-CONTRACT-AUDIT]].

**Shape review 2026-10-20 evening (Claude):** the candidate's one-order-per-TURN shape is rejected (per-order
overhead on every turn, Flash-Next cold-start per turn and never placeable, every token through the CIFS desk store);
chat-as-order is built as one WINDOW order per HAI run on `helper_window.py` + live streaming + yield between turns —
[[34-CHAT-FACTORY-CONTRACT-AUDIT]] § "Shape review".
**Status 2026-10-21 ~01:15 CT: LIVE for ClaudeSupervisor** (switch `HAI_CHAT_WINDOWS`), proven live: answer, warm
reuse (8.6 s turns, one window order), tool call, Stop, owner-heartbeat cleanup, Plex hold. Widening needs a
direct-path latency comparison, Strata windows, folder retention and the remaining direct consumers.
**Window vs direct MEASURED 2026-10-21 (ABA, same user/questions/box):** window 9.2 s / 9.4 s median vs direct 16.3 s —
because the direct path's per-query embed evicted BigHonker's 9B every turn (seen live on `/api/ps`). **FIXED + verified
live 10-21 09:01 CT** (embeds ask `occupied_for`; plain chats mark their box live for its keep_alive; warm direct turn
6.8 s). Also fixed: Auto's warm-27B shortcut ignored factory claims. Windows widened to the owner. Detail:
[[34-CHAT-FACTORY-CONTRACT-AUDIT]] § "MEASURED" / "FIXED".
**Menu routing for chat (the owner 2026-10-21: *"I still want menu routing to apply as much as possible. It's just one of the
category on the menu, speed. A chat user will want a more interactive speed"*):** checked — nothing overrules the menu
(Codex's `pick_brain` shortcut was removed unshipped; windows only EXECUTE where the chooser picks), but live chat does
not consult it yet: `brain_order` calls `scheduler.place("chat")` with no family/tier, which keeps the legacy ladder.
`policy.json` prices `chat-turn` / `chat-c1` with a `fastest` lens. ✅ **BUILT 2026-10-22 (area C steps 1–2, `place_weighed`; the price of the hour 10-23):** chat asks the menu
(family `chat-turn`, tier from McCoy or the crown when unsure, lens `fastest`; a user pin still wins); a `fellowship`
tier pick goes through Tolkien's orders, not a GPU window.
**Checked 2026-10-21 ~09:10 CT before wiring — NOT wired, on purpose:** the Phase 7 design itself says callers opt in
"once triage (McCoy, Phase 8) supplies family + tier for Auto turns". The data agrees: `chat-turn` (CHAT1, 7 one-line
requests) is SATURATED — every 8 GB model 42/42 — so the policy would pick on speed alone and move everyday chat from
qwen3.5:9b (3.06 s/solve) to qwen3:8b thinking off (1.7 s); a real question is `chat-c1`, where tiers separate (8 GB 65%,
the 3.8 88%). Which family a turn is = McCoy's triage, still SHADOW (C1b replay: 96/106 over-routed). So chat's menu
routing is gated on Phase 8 McCoy going active, not on plumbing; until then chat keeps the legacy ladder (now with
claims respected), a user pin still wins.
**FOUND 2026-10-21 ~16:05 CT — HAI CHAT RE-READS ITS WHOLE PROMPT EVERY TURN, warm or not (one live run; a finding to
confirm, not yet a verdict).** The B11b window wiring (`helper_window.py` saves the runner slot when a live window yields,
the next window on the box restores it; deployed ×3, 29 tests) was proven live through HAI's front door (ClaudeSupervisor
chat `a40a8949520a`): turn 1 (a runbook excerpt) read 18,734 tokens in 7.69 s on BigHonker's 9B window; an order made
the idle window yield and it SAVED `win-qwen3.5-9b-19968.bin`; turn 2's new window RESTORED it (file consumed) — and
still read its whole 8,063-token prompt in 3.19 s (~2,530 tok/s = the cold read rate), because turn 2's prompt is not
turn 1's plus new text. Its messages (the window item): system 9,235 chars · a per-turn `[TOOL SELECTION]` system message
· the previous ASSISTANT answer only (turn 1's user message — the document — is gone) · the new user text + per-turn RAG
notes; and the tool schemas are chosen per request, which Qwen's template renders at the TOP of the system block. Any one
of those breaks the shared prefix near the start, so a chat turn pays a full re-read even with the model resident and
no eviction — ~3 s per 8k on the 9B, ~11 s per 8k on the 3.8 (~700 tok/s) before the first word. The slot dump cannot
help until the prompt is APPEND-STABLE. **Owed (chat speed — the menu's interactive axis):** (1) measure it as an order,
one variable at a time on the same conversation (identical prompt twice = the cache works; per-turn tool set changed;
TOOL SELECTION message moved; history with vs without the earlier user turn) — the read time per turn is the product;
(2) then make Kirk's chat prompt order stable → volatile (static system + a per-chat stable tool set + the whole
conversation, then this turn's selection notes / RAG / new text), which the agent loop already is (its turns append).
**✅ (1) MEASURED 2026-10-21 16:13 CT** (order `chatprefix-bi.20261021-1612`, payload `mothership-build\workspace\
chatprefix\chat_prefix_probe.py` on HAI's real turn-2 request; each arm primes the cache with it, then sends one
variant; read = `prompt_eval_duration`): cold 3.04 s · identical 0.05 s · **append the answer + a new user message
0.08 s** · tools reordered 3.02 · one tool dropped 2.84 · the `[TOOL SELECTION]` text reworded 3.02 · HAI's real next
turn 2.53 (6,781 tok). **ANY change before the end costs a FULL re-read, not a partial one** — qwen3.5 is a hybrid
(recurrent state cannot rewind to an arbitrary token; only the engine's checkpoints near the last prompt's end), so for
these models only APPEND-ONLY turns are cheap: ~35× less reading per warm turn. Also found reading the code:
`history.context_messages` keeps the newest messages under a fixed **`CONTEXT_CHAR_BUDGET` = 18,000 chars (~4.5k
tokens)** whatever the model's window (9B 19,968, the 3.8 122,880 tokens) — chat FORGETS past ~4.5k tokens (doctrine 13a:
no arbitrary limits) and the window SLIDES every turn once full, which breaks the prefix too. **(2) the fix, owed now:**
a per-chat STABLE tool set (grown only when a turn needs a new tool); the selection note moved to the END of this turn's
message; earlier user turns kept EXACTLY as sent (their notes included); the history budget from the serving model's
measured window, compacted in rare big steps instead of sliding per turn.
**✅ (2) BUILT + LIVE + PROVEN 2026-10-21 ~16:45 CT** (orchestrator `9fb0185` + `35b1f4c`, gate 3002/2;
`ollama_client` § APPEND-STABLE CHAT, `tests/test_append_stable_chat.py`): the conversation keeps its tools (keyed by its
opening; grown only at the end, within the selection's budget — a new pick that does not fit is left out, never a
reshuffle), the selection and coding-tasks notes ride at the END of the turn's message, history stores each user turn
as SENT (`sent` beside the plain `content`) and replays it, `CONTEXT_CHAR_BUDGET` = 2 × `BRAIN_NUM_CTX` chars (half the
brain's measured window; Tolkien rebuilt for `config.py`). **Live, three turns in one ClaudeSupervisor chat pinned to
one box (`quick_8b`, BigHonker 9B window): turn 1 13,643 tok read 5.37 s (cold) · turn 2 14,578 tok read 0.46 s ·
turn 3 15,502 tok read 0.76 s — ~8–12× less reading per warm turn** (before: turn 2 at the cold rate). The first live
run (unpinned) caught a bug — the conversation key changed between turns (turn 1 is keyed before its note is appended,
turn 2 replays it with the note) — fixed in `35b1f4c` with a regression test. ⚠ **What it does NOT fix: chat MOVING
BOX between turns** (that run: turn 1 on Mothership qwen3-14b, turns 2–3 on BigHonker's 9B — a cold read whatever the
prompt). Keeping a conversation where its cache is = chat placed by Spock with the re-read price (`residency_cost`),
i.e. the menu routing owed above (gated on McCoy). Also: a document near the window (18.6k tok on the 9B's 19,968)
still forces history to drop it — that is the window, not a rule.
⚠ **GAP FOUND 2026-10-21 ~18:40 CT — tool GROWTH is a full re-read.** The tool block renders BEFORE the conversation,
so growing it "at the end" still changes everything after it: a ClaudeSupervisor chat on Mothership qwen3-14b went
29 → 34 tools on turn 2 (`read_config`, `code_propose_cleanup`, `save_to_corpus`, `library_carve_h1`,
`dataset_acquire` — retrieval noise for "name one setting"), identical message prefix, and turn 2 re-read 17,811 tok in
10.3 s (cold turn 1: 12.9 s). The 16:45 proof passed only because its tool set happened not to grow. **Decided
(the owner 2026-10-21, correcting my freeze/pre-fill menu): "we said we should have a dynamic toolset that is chosen by
starfleet"** — the documented design ([[HAI-LLM-Execution-Context-Mechanics]] § Tool selection: the orchestrator
picks a SET per request), so no cache rule overrides Starfleet's per-turn choice. **Measured offline the same evening**
(`select()` replayed in the container on the same two-turn chat, both pools, windows 40,960 / 19,968): giving the held
set to `select()` as in-flight state trims some noise but **fails the positive control** (an image request on
BRAIN_TOOLS @19,968 got NO render tools — the boosted held set filled the budget) → rejected. For user `the owner` (no
coding state) every short follow-up ("name one setting", "one more?") still adds 4–5 unrelated tools
(`code_propose_cleanup`, `code_apply_cleanup`, `library_carve_h1`, `dataset_review`, …) at every pool/window, while
the image request rightly adds `render_person`/`render_status`. **So the re-read on growth is the price of the dynamic
set, and the cost is driven by selection NOISE on short follow-ups** — the documented lever is selection precision
(sharp, stage-distinct descriptions; the unmeasured RECALL/precision instrument `retrieval_probe.py`), not a cache
rule. ✅ **Fixed at the source the same evening — LIVE (`b0a4804`, gate 3018/2, ~20:50 CT).** Live proof (same
three-turn pinned chat as 16:45, BigHonker 9B window): tools 23 → 23 → 23, each turn an exact prefix of the next;
turn 2 read 15,277 tok in **1.04 s**, turn 3 16,457 in **0.90 s** (cold turn 1: 5.50 s); whole turns 10.3 / 10.2 s
vs 35.5 s. How it was found and measured: `retrieval_probe.py` rebuilt (it was
cited but missing) and run on the owner's 125 real follow-up turns — today 56 % of them grew the block; a held block now grows
on an UNSURE turn only with the turn's own strongest tool when that reading is sure → **14 %**, 0 of 10 recall controls
missed, negative control clean (Jordan: 62 % → 25 %, n = 8). [[HAI-LLM-Execution-Context-Mechanics]] § Tool selection. ✅ **Same pass, fixed (`e660251`, gate 3014/2):** the AUTO_TITLE call joined Kirk's own
chat window (same model) and thought 45–83 s (2–5k tokens for a 40-char title) directly on the new chat's card right
after turn 1, outside the window, while the user's turn 2 waited; a title now asks `occupied_for(host)` with no model
— the failover titles it or the placeholder stays.

**Was owed before Phase 7/8 closure — ALL DONE by 2026-10-23** (close-out plan list A; actual-host context: the weighing takes `config.ctx_for(url, model)` of the box it places on, `app.py` weighed placement): finish and prove chat/order integration,
remaining model protocol consumers and actual-host context selection; B4b
overflow and live placement proof; resume/reload pricing (B11's lossless rigby resume itself
is closed 10-21: MTP and temp > 0 both identical, engine patches #0/#2/#3); **B11b — the SAME lossless
dump/resume on the three GPU factories (FACTORY PARITY, the owner 10-21: *"All four factories are supposed to have the
same capabilities… I've been explicitly telling you about this dump and resume stuff for ages now"*).** The 10-16
"no engine switch" call (HAI-Agent-Surface-Design § QUICK RESUME) assumed ollama's RAM prompt cache covers every
handover; it does not cover an EVICTION (a McCoy call, another model, a second user), which is where the dump pays.
Read 10-21: ollama 0.40.5 runs its model through its own bundled `lib\ollama\llama-server.exe` (BigHonker serve.log:
`starting llama-server … --port <n> --host 127.0.0.1 … --load-mode none`), with no slot-save path — so the build is
the rigby engine standard (patches #0/#2/#3 + `--slot-save-path`) inside ollama's runner, and the on-box factory
dumps the slot before an eviction and restores it after; open first: ollama's runner flags (e.g. `--load-mode`) say
its llama-server is ollama's own build, so the lab build must match its llama.cpp version and flags. **Read 10-21
(the binary's own strings, BigHonker 0.40.5):** the bundled build already has `--slot-save-path`, and its
`libllama-common.dll` reads **`LLAMA_ARG_SLOT_SAVE_PATH`** — the same env channel the boxes use for
`LLAMA_ARG_FIT_TARGET` — so **stage 1 needs no binary swap: set that env for ollama on all three hosts**, and the
on-box side (the factory / a chat window, which can reach the runner's 127.0.0.1 port from its command line) saves
the slot before it hands the card over and restores it before the next turn. That covers the eviction that happens
BETWEEN turns (a McCoy call, another order) — measured on this very build 10-16: restore 0.10–0.17 s vs a 2.8–5.3 s
re-read. Stage 2 (mid-answer, lossless) = the lab patches #0/#2/#3 in a lab build of ollama's llama.cpp version.
**✅ STAGE 2 — ENGINE PARITY, DONE 2026-10-22 ~10:00 CT (all four on `620d222+compat`, lossless proven; B11b closed ~12:15 CT) (the owner: *"Get them in synch… use the deploy tool to ALL FOUR"*).**
Found: fleet_deploy had NEVER carried an engine — rigby builds its own in place, the GPU boxes ran ollama's bundled
llama-server — so the patches reached rigby alone and no check could see it. ollama 0.40.5's engine = **upstream
llama.cpp `620d222`** (2026-10-15, `--version` 0.7.0-dev, Clang 18). Source fetched to `E:\SSDShared\lab-engine\`;
patches applied: #0 and #2 as written, **#3 PORTED** — upstream now saves the drafter's memory itself
(`save_slot_draft`: ctx_dft + `common_speculative_get_state`, which covers an EAGLE-style boundary stash, NOT the MTP
drafter's `pending_h`), so the variant writes no drafter state of its own and its lab section follows upstream's draft
section (`slot_resume_patch.py`, rigby's path unchanged); CPU build on rigby compiles, lab markers in the libraries.
Build = an ORDER on Mothership (`workspace\labengine\build_lab_engine.py`, VS 2022 MSVC, CUDA 13.0.2 installed for it,
Vulkan SDK 1.4.363; NVIDIA set sm_86 + AMD set Vulkan; rush — power was $0.118 vs the $0.10 ceiling; the first try died
on `FileNotFoundError: cmake`, a bare name resolved with the parent's PATH). **fleet_deploy now carries engines**
(`fleet_engine.py`, mirror `cfea1b3`): `--engine <order dir>` = a versioned `Ollama-0.40.5-lab620d222` tree beside the
stock one (no stock engine file left in it), every file hashed, launcher repointed (backup = rollback), ollama restarted
under the lane's stop/requeue slips, only processes from the ollama tree killed; **`--check` reports every factory's
engine and prints ENGINE DRIFT**, on the engine's VERSION `tag=` as well as its markers (mirror `e2be4a3`: rigby's
pre-parity e467e5b build carried the markers too, so markers alone read a version mismatch as parity). Proof per box:
`engine_resume_probe.py` (cut, save, evict + reload, restore, `lab_resume` continue = the uninterrupted answer token for
token) / rigby's own dump probe.
**FOUR GPU factories, one engine version (the owner 2026-10-22: *"Rigby has a 1060"* / *"Tolkien has a special factory that
has GPU's as well as the tablets"*).** Tolkien's factory on rigby (GTX 1060 + GT 730 + the tablets) is the fourth GPU
factory, so parity = the SAME llama.cpp `620d222` + patches on all four: rigby's 1060 builds it with its own CUDA 11.4
recipe (`lab-engine\build_cuda114_620d222.sh` -> `llama-build-rpcasync\`, the patched tree in `/home/owner/lab-engine/`,
graphs patch included). Build #4 on Mothership delivered both Windows sets
(`labengine-620d222.20261022-073403`: NVIDIA 27 files incl. cuBLAS 13 + AMD/Vulkan 24 files, MSVC per-chip CPU
plug-ins, `AVX2 = 1`, lab markers in llama / llama-common / llama-server-impl); builds #1–#3 failed on my mistakes
(cmake by bare name, NVVM left out of the CUDA install, a copy filter that dropped `common\build-info.cpp.in`).
**STATE 2026-10-22 ~08:50 CT: rigby LIVE on `620d222` + lab patches; the three Windows boxes ROLLED BACK to stock —
the Windows lab build is missing OLLAMA'S OWN llama.cpp patches.** All four ran it briefly (`--check`: `tag=620d222`
everywhere) and three proved LOSSLESS: BigHonker qwen3.5:9b and Softserve qwen3:8b (0 read, identical; control
diverges at 61), rigby B11's r10b + r9c specs unchanged (511/511, 383/383; old-path controls 207/169 = 10-21 to the
token). Then the re-fit (the upgrade-traps rule) failed on `huihui_ai/qwen3-vl-abliterated:8b`, `medgemma:4b` and
`glm-ocr` — loaded fine on stock 10-15 — with `key not found in model: qwen3vl.rope.dimension_sections`: ollama
builds llama-server from upstream `620d222` **plus its `llama/patches/*`**, which read ollama-format blobs; the lab
build was upstream + lab only. Every load goes through llama-server on stock too (BigHonker serve log), so this is the
build, not the engine choice. Rolled back by `--engine-rollback` (BigHonker/Softserve 08:44, Mothership 08:46; LAN
200 on all). **✅ ~10:00 CT ALL FOUR on `620d222+compat`; LOSSLESS PROVEN on rigby (511/511, 383/383), BigHonker (qwen3.5:9b
@19,968) and Softserve (qwen3:8b) on the compat build; all models load (re-fits vs same-day stock: equal or better).
✅ B11b CLOSED 2026-10-22 ~12:15 CT — Mothership PROVEN by distribution:** (⚠ corrected 2026-11-05: the next clause was too broad. The 27B repeats itself from a fresh load, 16 arms,
temperature 0 and 0.6; token 15 is a second run in an already-used engine. Mid-answer resume there IS token-identical when cut the way a real stop cuts (5 arms, `--fresh-load --hang-up`, 11-05); the probe's old cut, exactly `cut` tokens, drifted by itself.
[[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] § Status. And B11b never covered Flash-Next, which runs on Strata; that is the same doc.) token equality cannot pass there (the 3.8
leaves its own uninterrupted answer at token 15 / 53 before any cut — the documented `Vulkan is NOT run-to-run
deterministic at temperature 0`, [[Qwen3.8-Flash-Next-on-Mothership]]), so `workspace\labengine\
engine_resume_dist_probe.py` (`submit_resume_probe.py <host> <model> <ctx> --dist`) uses the lab's equivalence method
(r26): next-token distributions at 40 teacher-forced positions, every arm built by the SAME prefill — live vs live after
a reload = the FLOOR, resumed (save → evict → reload → restore) vs live = the CLAIM, shifted positions = sensitivity.
**Positive control Softserve (deterministic): floor 0.0, claim 0.0, same top 40/40. Mothership qwen3.8:27b: floor
0.00023, claim 0.00025, same top 40/40, shift 17.5; the restore re-reads 4 tokens.** ⚠ The first version FAILED its
control (Softserve claim 0.008 on a 0 floor): its live arms read the prefix inside the query (one batch of 89) while the
resumed arm re-decoded only the last token — a batch-shape difference CUDA's numerics follow; two variables, one claim.
The control caught it; all arms now prefill identically. Build history: ollama 0.40.5's `llama/compat` layer (base
`b11669` = `620d2225c`, verified) applied to both lab trees by `lab-engine\apply_ollama_compat.py`; rigby rebuilt +
swapped (`--check` `tag=620d222+compat`), its proofs re-running; Windows build `labengine-620d222-compat.20261022-092140`
on Mothership, then: the three failing blobs must load, re-fit, reproof, redeploy. (Mothership bugchecked `0x9F` at
~09:10 entering S3 — RUNBOOK-GPU-Host-Power-and-Glances § sleep-crash; back in service.) Deploy-door fixes
from the same morning (mirror `372e60a`…`4e92290`): rigby leg (bytes on stdin, `engine-` archive prefix, WAIT while
busy); VERSION tag drift; firewall read BEFORE the switch (Mothership was cut off the LAN 08:19–08:33 by an
auto-created Block rule — the owner accepted the prompt remotely); LAN must answer after it or roll back; the runner task is
restarted after a paused-lane activation (Mothership and Softserve were left runner-less); and the foundry now retires
a deploy's stop slip with the run it targeted (a leftover slip stopped Mothership's requeued proof 16 s in).
**✅ STAGE 1 LIVE on all three GPU boxes 2026-10-21 ~15:33 CT** (launcher line + ollama restart on each; RUNBOOK-Ollama-
Host-Operations § "SLOT SAVE / RESTORE on every GPU box"). Proven through ollama by order `b11b-slotdump-*`: after an
eviction, turn 2 of a 6.3k-token conversation read in **0.13 s vs 2.38 s** (BigHonker 9B) and **0.54 s vs 9.29 s**
(Mothership 3.8, Vulkan); Softserve's probe is HELD by the desk for a live Plex transcode (Plex first) and runs when
it ends. **✅ The CALLER (verified 2026-10-23, close-out list A)** — whoever evicts saves first and restores after: a chat window yielding its card
(`helper_window.py`), McCoy's on-demand bump, a factory order taking a warm card; plus deleting each slot file when
spent (C: space).
The bundled CUDA backend loads from `cuda_v13` via `GGML_BACKEND_PATH` (also read from `ggml.dll`'s strings).
Phase 8 DoD Q7
write-up and its measured/admissible production use, with placement,
decomposition and training questions gated by adequate data. C7 remains a DATA
WATCH, not a speculative easy-code classifier build. C5's older "84 labels only"
block must be read with the later replay's 106 measured cheapest-tier labels;
do not pretend the later data was never collected. No new phase opened.

**Already deployed in this pickup:** desktop-aware BigHonker 9B window 19,968;
fit-before-load admission using 40 measured profiles and fresh start occupancy;
HAI render identity/capability/claim/accounting repair; unified image preferences
in the existing top Models group, automatic image intent and truthful identity
metadata. Available identity adapters recovered by archive hash; deliberately
unplugged USB sources are not assumed present. Bare-9B comparison completed:
5 held / 2 comparable / 16 lost, with box/backend/context caveats and no default
change. BigHonker GPU recurrence recovered by the owner-authorized reboot; hardware
root cause remains deferred. Proof/history: 2026-10-19-codex-p34 and
2026-10-20-codex-p34.


## ⚠ PHASES 2 AND 3 REOPENED 2026-10-17 ~22:30 CT by the RAM swap — they close FIRST (no new phase until earlier ones close)

Phase 2 (Flash-Next now at the champion's pace, blind 3–2–3) and Phase 3 (the split A3B on BigHonker, ~29–33 tok/s)
— what closes each: [[34-CHARTER]] § "REOPENED 2026-10-17". Phase 7/8 closure work (the A1 other-reviewers arm, B8's
trial) waits behind them. Staged, not submitted: `a1_delegate.GPU_PIECE_PRIORITY = 100` (GPU helper / reviewer / window
orders run next, as rigby's pieces do; tests 32/32 in `a1\tests\test_a1_delegate.py`) — **deployed 22:30 with the A1
group** (it rode `fleet_deploy --a1`; affects helper/reviewer orders only).

**▶ IN FLIGHT — Phase 2 / 3 live block (opened 10-17 22:35, last updated 10-18 08:30 CT; journal 2026-10-18-claude):**
- **Phase 2 — Flash-Next's LEAD-ROLE test, the Strata path BUILT + DEPLOYED (all four factories, rigby-a1 gate 70):**
  `inspect_lab\lab_provider.py` backend `openai` (`openai_chat` / pure `parse_openai_stream` / `template_kwargs`; reuses
  infer_ollama's IDLE_S / `looping` / `[beat]`; Strata's own over-context 400 wording → `exceed_context_size` so
  `a1_chat.is_context_full` forces a compaction; tests +5 `OpenAIBackend`, 17 OK) · `a1\a1_order.py` `engine: "strata"`
  (Strata starts inside the run's try via `strata_arm` prepare/enter/launch, stopped in the finally; no ollama residency
  check; backend openai; A1 suite 203 OK) · `workspace\flashnext\strata_arm.py` split into ONE lifecycle
  (prepare / enter / launch / stop, stop never raises). **Plan = Nemotron's challenger plan
  (`a1v22-…-nemotron30-best-thon-spot.20261008-0755`) with only engine / model / context 131,072 / vendor sampling
  (t 0.6, top_p 0.95, top_k 20) / think xhigh changed** — submitter `hard-bench\a1\submit_fn_a1.py`
  (`--decoy` = wordy only, no stop rule). **DECOY CLEAN (22:47):** `…-flashnext-strata-decoy.20261017-2233` — control
  affine-cipher PASS 16/16, wordy PASS 25/25 in 18 turns (21 tool calls, verified before finish), num_ctx 131072 served,
  0 capped turns, 0 void, 75 tok/s. **FULL 15 DONE 10-18 02:38:** `a1v22-py-agentic-hai-flashnext-strata-lead.20261017-2248`
  (desk `90d07480d4e2`) — **12/15 solved**, control PASS, 0 void / 0 errors / 0 capped / 0 compactions, 227 min,
  70–80 tok/s. Fails: dot-dsl 10/12, tree-building 12/13, paasio 24/25. **SAME-HARNESS comparison (harness
  `72403f73`, the 3.8's own xhigh run `…q38-best-thon-spot-exhigh.20261017-0047`, the original 10):** 3.8 **8/10, 226
  min, 5,236 kJ CARD-only** vs Flash-Next **9/10, 96 min, 2,931 kJ WHOLE-BOX** (UPS; card 1,881 kJ) — Flash-Next's
  whole box draws less than the 3.8's card alone, so the energy order holds with no 3.8 UPS run. Differences: react
  (FN pass, 3.8 13/14); both miss paasio. Hard 7 (3.8 only on the OLD harness `cee5ef60`, 10-08): 3.8 3/7 vs FN 5/7 (FN
  adds forth 54/54, pov 15/15). ⚠ Run-to-run spread is real: the 3.8's own 10-10 run of the same 10 read 6/10, so
  9 vs 8 is within noise — the result is "at least as good, 2.4× faster, under half the energy". Whole-box from the
  UPS logger: Mothership 511 W avg during A1 = **1.56× the card meter** (pov…zipper, 13 rows, 147 min covered).
  **ALL 15 ON ONE HARNESS (10-18 04:46):** the 3.8's hard 7 re-run on `72403f73`
  (`…q38-best-thon-spot-exhigh-hard7.20261018-0241`, copy of its 10-17 plan, only exercises changed): **2/7** (scale-gen,
  sgf pass; dot-dsl 10/12, forth 52/54, pov 14/15, tree-building 12/13 (1 capped turn, not at ceiling — scored),
  react 13/14), 0 void, 123 min, **3,817 kJ whole-box at 519 W = 1.34× its card**. Totals over the 15:
  **Flash-Next 12/15, 227 min, ≈6.9 MJ whole-box** (dot-dsl + forth pre-logger, estimated at its 1.56× ratio) vs
  **3.8 9/15, 274 min, ≈8.5 MJ** (its 8 non-hard rows from 10-17 00:47, estimated at 1.34×) → **≈0.58 vs ≈0.94 MJ per
  solve**. Flash-Next solved everything the 3.8 solved plus forth, pov, react; the 3.8 solved nothing Flash-Next
  missed. The 3-solve gap is wider than the 3.8's observed run-to-run spread (6–8/10; hard 7 3/7 → 2/7); n = 1 run of
  Flash-Next. Both boxes draw ~515 W at the wall — the energy gap is time, not watts.
  **▶ PRELIMINARY CROWN → FLASH-NEXT (the owner 10-18 ~05:0x: *"If there's an obvious winner let's stick with flash next
  as our preliminary winner"*).** It is obvious on the lead-role test (12 vs 9 of 15, superset of the 3.8's solves,
  ~0.6× the energy per solve). Preliminary until the owner's spot-check of headtohead-v2; the charter's "keep / share by job
  family / move" is decided by pricing Flash-Next on the menu per family (the 3.8 holds every family until a priced
  Flash-Next row beats it there).
  **▶ MENU ROLE TEST, 10-18 morning — 5 cells, NOT a grid** (the owner: *"Why are you doing champion testing with the lame
  results? … are you filling a grid rather than testing a role or testing upper limits?"* — a 35-cell dry run was cut
  before anything was ordered): one cell per family (f1 f2 w1 c1 tool) at the top config, xhigh at vendor 0.6, seed set
  1, no price line, against the 3.8's best per family. **Built:** `infer_ollama --strata <plan>` (starts Strata through
  `strata_arm`'s one lifecycle, refuses rc 2 unless `/props` n_ctx == --num-ctx, stops it by any route; the plan's vendor
  sampler lies under the order's own temperature/seed; ollama's JSON-schema `format` → `response_format` — Strata
  VALIDATES after writing where ollama constrains, measured as served); the openai wire moved INTO infer_ollama (one
  copy; `lab_provider.openai_chat` delegates); `menu_fill.STRATA` names the model + its serving plan
  `workspace\flashnext\plans\strata-serve-q2_0-ctx131k.20261018.json`; the fleet order builders pass `--strata` and drop
  the ollama-only `model.<name>` requirement (pinned instead). Tests: foundry 514 (+9 `InferOpenAIWire`), menu 94 (+3
  `StrataServedModel`), lab_provider 17 (5 replaced), A1 203. Deployed (fleet_deploy core + `--a1`). Submitter
  `hard-bench\submit_fn_menu_role.py`. **Tool cell DONE (live proof, 07:17–07:23):** **71/72 at 2.2 s/answer**, 0
  errors / caps / priced out, Strata up in 36 s at 131,072, stopped clean (`…tool-t06-xhigh-s1.20261018-071733`).
  The menu's on-delivery top-up then ordered the other four WITH the menu's price lines (f1 10.2 s, f2 7.4 s, c1 71.8 s)
  and W1's at-cap brake — which would have measured the line, not the model. Fixed: `menu_fill.PRELIM_CHAMPION` +
  `is_champion()` (ONE derivation for the three champion checks: no price line, no brake, never auto-stopped; test
  `test_the_preliminary_winner_is_priced_on_the_champions_terms`, menu 95 OK); the four cancelled at the desk; the f1
  one had shipped price-lined partial rows → `menu_void.json`. **Re-ordered unceiled 07:30** (desk `13775d7ea501`
  `25c040d6c41a` `2a8c63d52d13` `8def97d50cfb`, all `-r2`): **f1 30/30 at 5.0 s, f2 72/72 at 5.4 s** (delivered,
  valid - short prompts, the context setting cannot change them); W1 + C1 CANCELLED 07:5x for the context move below.
  **▶ CONTEXT → 262,144 (the owner 10-18: "would it be worth pursuing bringing flash next to maximum context? Is it a 1M
  max?" → "Just do it now so we waste less time on cells we will probably supersede").** The GGUF's own
  `qwen4exp.context_length` = **262,144** (no rope-scaling keys: 1M is not in this file). 131,072 was Strata's
  `--low-ram auto` pick, then carried into the A1 test — an untested number, not a measured limit (13a). Cheap by
  design: only 12 of 48 layers hold KV (full attention every 4th), 2 KV heads × 256, int8 → ~3 GB at 262K, and Strata
  keeps only the last 32K resident (`--kv-resident 32768`), the rest in RAM; Strata's own docs run this Q2_0 at 262K
  (50.9 → 62.6 tok/s with KV streaming). **Check RUNNING:** `flashnext-strata-plateau-ctx262k.20261018-0758` (desk
  `963c0ec7919b`, priority 10) — the 10-17 post-RAM plateau plan with ONE change (`--context 262144`) + a deep read
  (`strata_arm.deep_read`: ~150-180K-token non-repeating log, one needle at mid-depth, thinking off; reports read and
  write tok/s at depth and whether the needle came back). Baseline (10-17, 131,072): write 68.8, read ~840 at 4K.
  **✅ CLEAN 08:1x — ADOPTED:** served `--max-context 262144`, load 36 s (same), write median **66.9** (range 58-81;
  131K: 68.8 - within spread), 4K read **~820** (131K ~840), RAM pinned 35.9 GB (+1.8); **deep read 191,857 tokens
  at 808 tok/s (238 s), needle found (`7391-QX`)**, write at that depth 28.6 tok/s. Serving plan
  `strata-serve-q2_0-ctx262k.20261018.json` (the 131K plan kept as the record); `menu_fill.STRATA` ctx 262,144 (menu
  95 OK). W1 + C1 re-order at 262K. Stopping the superseded W1 cell: the desk's cancel waits for the factory's next
  poll; the documented `<order>.stop.json` slip is the immediate stop (used, worked).
  **▶ READ-SPEED LEVER — hipBLASLt tuning table (the owner 10-18: "more interesting to try first since it doesn't cost
  writing speed"):** Strata's setup: "no hipBLASLt tuning table for gfx1100 with hipBLASLt 100500" → plain hipBLAS;
  Strata's docs: a calibrated table took a fresh 32K read 638 → 1,177 tok/s on another card, decode unchanged. The
  lab's ROCm kits (ai-toolkit / CorridorKey venvs) are TheRock 7.15 = hipBLASLt 1.4.1, not the engine's 1.5.0; the owner:
  *"Would it not behoove us to update our stuff rather than trying to adapt"* → the engine's EXACT ROCm 10.2.0a20260930
  in its own venv `E:\StrataBuild\.rocm-win` (download OK: *"Should good"*). Order
  `flashnext-tune-hipblaslt-gfx1100.20261018-0811` (desk `cb0000b4136c`, payload `workspace\flashnext\tune_hipblaslt_order.py`):
  install → assert hipBLASLt 100500 → build ONLY `tune_hipblaslt` (Strata's own Windows flags) → calibrate the 26
  shipped gfx1100 shapes → table delivered, NOT installed. Adopt only after a read A/B (with/without, salted fresh
  prompt, write checked) and `STRATA_HIPBLASLT_VERBOSE=1` reporting `fallbacks=0`. Next levers after it: `--prefill`
  size ladder (BigHonker's A3B: 2.4× reads at -4-6 % write), PCIe link Gen4 x16 check.
  **Table built 08:54 (run 3 of 3):** 26 rows, `gfx1100 100500`, 30 s calibration. Runs 1-2 failed
  `hipErrorInvalidDeviceFunction`: Strata's own guide (`docs\AMD_HIP.md` Windows § "The HIP runtime next to strata.exe")
  — the AMD driver's `amdhip64_7.dll` in System32 beats PATH; the tuner must run with the bundled runtime in its own
  folder (`E:\StrataBuild\engine-run`). **▶ EVOLUTIONARY TUNING, GEN 1** (the owner: *"Evolutionary please, there's all
  sorts of data in the library and people online to draw from"*; *"our 7900xtx CLOBBERS those smaller cards"*) — order
  `flashnext-strata-kvres-gen1.20261018-0924` (desk `8df228c66065`, plan `strata-kvres-gen1.20261018.json`). GEN 0 =
  Strata's OWN RX 7900 XTX 24 GiB + 64 GiB measured arm (`docs\AMD_HIP_PERFORMANCE.md`: `STRATA_PREFILL_MMQ=1`, a gfx1100
  hipBLASLt table, `STRATA_PREFILL_RING=96`, `STRATA_IO_THREADS=32`, `--prefill 8192`, `--adapt-every 0`, `--pcie-frac
  0`, `--vram-reserve-mib 1024`; 1.8-2.0× prefill vs their prior runtime) with OUR table. Arms: recipe + the whole 262K
  KV on the card (~3 GB int8 — setup's 32K window is a 12-16 GB-card rule), the recipe as published, today's serving
  config LAST. Left out: `--pool-workers 15` (their 16-core CPU; our measured optimum is 8 threads),
  `--resident-cpu-experts` (Linux). Each arm: salted ~32K + ~4K reads, a ~190K-deep write of ~300 tokens with the
  engine's per-request stats (the 10-18 "28.6 at depth" was a ~7-token answer — not a rate), 3 shallow writes. The
  table-only A/B (`68a6bcae14f9`) was cancelled and folded in. **Gen-2 candidates:** Strata upgrade 0.1.39 → 0.1.44.1
  (v0.1.42 announced +13-16 %; a download — the owner's OK), `--spec` / `--spec-min-p` depth, `--prefill` beyond 8192,
  `parallel` slots (HAI throughput), engine work at depth if the stats point there.
  **▶ SUPERSEDED 09:2x — UPGRADE + BEST CONFIG, not a sweep** (the owner: *"No just upgrade dude. We are aiming for its best
  config, not pricing every single config. That was a way earlier phase of experimentation"*). Gen-1 order
  `8df228c66065` cancelled before it ran. **Strata v0.1.45** (released 2026-10-17; HIP zip SHA256 matched the release's
  `SHA256.txt`) downloaded side by side into `F:\Strata045` — `F:\Strata` 0.1.39 untouched = the rollback. v0.1.45
  **ships `tools\hip\gfx1100-hipblaslt-100500.txt`** (the official table for our card + engine library; ours is now
  redundant) and adds opt-in `STRATA_GDN_CHUNKED=1` (gfx11 chunked prompt recurrence, validated on gfx1151 only — a later
  candidate). ONE run: order `flashnext-strata-045-best.20261018-0928` (desk `52aad1c171ed`, plan
  `strata-045-best.20261018.json`) = setup's own choices + the 7900 XTX recipe + the whole 262K KV on the card;
  measures 32K/4K reads, a ~190K-deep write, shallow writes. Adopt if clean and not slower; back off one setting at a
  time if not.
  **✅ ADOPTED 10:0x — Flash-Next's serving config = Strata v0.1.45 best** (`strata-serve-q2_0-v045.20261018.json`):
  read 32K **1,469** / 4K **1,359** / 192K **1,454** tok/s (0.1.39: 4K ~830, 192K 808); **write at 192K depth 64.4**
  over 346 tokens with 0 % PCIe and no RAM/file expert reads (the whole context on the card); shallow write **75.2**
  (0.1.39 ~67-69); needle found at every depth. `STRATA_GDN_CHUNKED` measured and LEFT OUT
  (`flashnext-strata-045-gdn`: reads +4-6 %, writes unchanged; validated on gfx1151 only, rounding differs).
  The official v0.1.45 gfx1100/100500 table equals our own calibration on 25 of 26 shapes (+6 more shapes); per shape
  it beat plain hipBLAS ×1.12-12.2 (median ×5.35) in our tuner log. Wired: `strata_arm.apply_serving` (the plan's
  `env` + `engine_args`, after setup rewrites the config) for infer_ollama --strata and a1_order; every Strata order
  now declares `engine_live.json` (the deck's LIVE read/write/context rows from Strata's own `/metrics`). Menu: all
  five role cells re-ordered ONCE on v0.1.45 (the 0.1.39 f1/f2/tool rows voided as a different engine; W1/C1 never
  finished there): desk `39770ef9f512` f1, `be2c102b27a3` f2, `a8625ff8eff1` tool, `c0d41bfe4916` C1,
  `fb53fea66deb` W1. **Phase 3 cut to role cells** (the owner: *"Yeah I think we should cut bh down"*; charter updated):
  BigHonker keeps W1-on (running) + C1 off/on + tool off/on; 14 grid cells cancelled + held.
  **▶ FLASH-NEXT MENU ROLE CELLS DONE 10:5x (v0.1.45 serving config, xhigh @0.6, unceiled) vs the 3.8's best priced
  row per family (`Datasets\Project34-Menu\menu-cells.csv`, Mothership):** f1 **30/30 5.6 s** vs 3.8 100 % 1.4 s
  (off) · f2 **72/72 2.9 s** vs 100 % 0.9 s (off) · tool **71/72 1.9 s** vs 100 % 0.7 s (off) · C1 **55/60 5.8 s**
  vs **100 %** 6.5 s · W1 **68/72 12.1 s** vs **97.9 %** 16.4 s. Card energy per answer far higher for Flash-Next
  (W1 24.2 vs 6.3 kJ; f1 1.7 vs 0.1 kJ). W1's 4 fails are the model's: 2 loops (loop rule), 1 wrong (`reason_dice`),
  and 1 that Strata ended as "length" at 8,009 tokens with 261,908 of room — its REPEAT GUARD ("one token ('0') 256
  times in a row"), a loop, not a cap. Instrument fix: `infer_ollama` relabels a Strata "length" far below the context
  as `loop` (+`engine_repeat_stop`), test pinned (foundry 518). C1's 5 fails are real (a wrong date ×3, 2
  instruction-format misses). **Reading against the charter's keep / share / move: SHARE by family** — Flash-Next
  leads the agentic / long-context lead role (A1 12/15 vs 9/15, less time + energy per solve there); the 3.8 keeps the
  menu's quick families (equal or better quality, 2-4× faster, far less energy). Decided below (external
  spot-check).
  **Door fix found on the way:** `fleet_deploy`'s pause-and-redeploy stopped BigHonker's in-flight cell, the runner took
  the NEXT queued order before the deploy's pause slip, and the deploy refused (one cell interrupted, nothing deployed).
  The stop step now writes the lane pause slip first (one name, `PAUSE_SLIP`; test
  `test_the_stop_pauses_the_lane_first_so_the_next_order_never_starts`, 34 OK); the retry deployed. Both interrupted
  BigHonker cells were requeued by slip (w1-t00-on-s1 resumed from 58/75 rows; w1-t06-on-s1's slip applies between
  orders).
  Whole-box energy: DONE above (UPS logger). **Spot-check DONE 10-18 ~12:4x by an EXTERNAL reviewer** (the owner: *"I may need to trust your judgment on that or we can ship it off to ChatGPT and have it review as an external reviewer so we're not biased by our own work"*): Codex CLI (gpt-6.1-sol, read-only sandbox) graded `headtohead-v2-blind-pairs.md` blind, given only that file (no key, no prior grades) → **Strata 2 · champion 2 · tie 4** (champion's two wins CLEAR: code-1, a heap-tie TypeError in Flash-Next's cache; ops-1, an invalid Compose example; Flash-Next's: ops-2 clear, code-2 slight). My blind grades agreed on 5 of 8; the 3 disagreements all leaned Flash-Next by one notch (I called reason-2 and write-1 slight Flash-Next wins, Codex ties) — the known optimism, not a different verdict. Both read QUALITY PARITY. Files: `mothership-build\workspace\flashnext\quality\headtohead-v2-grades-codex.json` + `-codex-prompt.txt`. Codex also flagged write-1's grader key premise (plain `importlib.reload` keeps the module object) — a key defect, both answers tied on it. **▶ CROWN = SHARED BY JOB FAMILY** (Claude under that delegation; the owner may overturn): Flash-Next leads the agentic / long-context lead role (A1 12/15 vs 9/15, less time and energy per solve); the 3.8 keeps the menu's quick families (equal or better quality, 2-4x faster, far less energy). **▶ WIRED FOR REAL USE 2026-10-18 ~13:25** (the owner: *"get it all wired up and ready for Real use"*): Mothership advertises `model.qwen3.8-flash-next-strata-q2_0` (foundry `strata_served.json`, tested 262,144); llama-swap v265 serves it on demand on :11435 (task "Llama-swap Serve", 5-min idle unload, NO frontier mode - that is for experiments, the owner); HAI's job-shaped calls to Mothership run on the crown (`ollama_client.lead_model`, `policy.json` roles.crown = Flash-Next at xhigh, matched to its priced A1 lead row 12/14) over the OpenAI wire, status-based silence watch, card freed for ollama calls. Live proof 13:24 (`LEAD MODEL ... -> qwen3.8-flash-next-strata-q2_0`, 47 s incl. load, accounted on the deck). Daily-use speed (frontier off, warm): read ~1,140 / write ~54 tok/s. Gate 2872, Tolkien 901, foundry 523. RUNBOOK-Ollama-Host-Operations § "Flash-Next through llama-swap". **Phase 2 CLOSED 10-18.**
- **Phase 3 — BigHonker's A3B menu cells** (35 MISSING at 20:4x; 24 queued on BigHonker at 21:55, `HAI P34 menu
  refresh` submits): when priced, its rows join the menu beside qwen3:8b (cost per correct answer per family) and Spock places per scenario - no seat to award (charter: the menu serves every case: low-and-slow overnight, a stand-in when the ideal box is busy, quality or time that a lesser box meets; the owner 10-18 reminder).
  **▶ 10-18 evening — the A3B role cells are DONE and on the menu; the work is now the FILE per box** (the owner: *"Each box needs
  to have the optimal model that I can run"* / *"some tuning will be quantization specific, so we can't assume 1-1"*).
  BigHonker, best row per family vs qwen3:8b on the same box: C1 thinking-on **95 %** (93.5 s) vs 83 %; coding f1/f2 off
  **100 %** at 2-4 s vs 91-93 % at 1-1.5 s; tool on/off 94 % / 93 % (1.9-10 s) vs 97 % / 93 % (0.6-3 s); W1 off **88 %**
  (47 s) vs 52-85 % (7-90 s, best row cap-bound). Both stay on the menu; Spock picks per scenario. Two menu bugs found
  and fixed on the way (a requeued order re-delivers into a stamped folder; the tool reducer was not in the refresh chain)
  and a hold takes `orders_only` so the BigHonker scope cut no longer hides its rows from placement.
  **File per box, measured:** A3B `UD-Q4_K_M` is slower than the IQ file at the IQ file's tuned launch (writes 22.6-25.0 vs
  25.6-26.3 tok/s, reads 922-957 vs ~1,590): 5.11 vs 3.88 bits/weight, same layers on the card, CPU-side 16,106 vs 11,051
  MiB, and the IQ weights sit in pinned `CUDA_Host` memory where the K-quant's do not (arsenal `cpu_fit_quant`). qwen3.8:27b
  on BigHonker as BACKUP intelligence (the owner 10-18; charter): K-quant lmstudio Q4_K_M writes ~2 tok/s (MTP2 1.88-2.01, plain
  1.73-1.78; 91 % of drafts accepted), MTP stays on for that role. **FILE QUESTIONS CLOSED 20:30:** same-size
  bartowski Q3_K_M (3.85 bpw) writes 26.1-26.4 / reads 1,311-1,504, level with the IQ file (so decode follows BYTES, not
  the quant's kernels - the 'K kernels are faster per byte' reading is withdrawn); Q4_0 (4.69 bpw) 21.2-24.0 / ~1,337 (5
  threads 11.5-14.7); Q4_0 + `--no-host` repacks (13,405 MiB q4_0_8x8) and writes 24.1-26.2 but reads ~91 (-93 %). The IQ
  file STAYS the A3B's BigHonker file. The 27B: Q4_K_M MTP2 1.88-2.01 beats Q4_0 MTP2 1.43-1.52; 6 threads = 4; with `--no-host` (repack, 9.4 GB q4_K_8x8) Q4_K_M writes 2.34-2.38 and Q4_0 2.06-2.15 but both read ~8-10 tok/s
  (a 4K prompt ~7 min): net worse per agentic turn unless write-dominated; RAM verified dual-channel 4000, CPU half ~20-28 GB/s
  effective. All Q4_0
  results hash-verified on BigHonker (a gate I had let through unchecked, then closed). Quality unmeasured for non-IQ
  files. The tuned launch is a research launch: production still runs ollama's stock one (writes ~18, reads ~640 for the A3B
  - what the menu rows price); the clock's production form stays open. Next: the 3.8's priced rows on BigHonker and rigby
  (rigby has no AVX2, so its file is the K-quant already copied there and sha-verified).
  **Phase 3 CLOSED 2026-10-18 ~23:40** (the owner, on the 3.8's role cells at ~2 tok/s: *"I guess skip"*): the A3B's role rows
  are on the menu beside qwen3:8b (BigHonker: C1-on 95 %, coding f2 100 %, W1 88 %, tool 93-94 %) and Spock places per
  scenario; the A3B's BigHonker file stays the IQ UD-Q3_K_XL (four files measured, register Phase 3 block); qwen3.8:27b on
  BigHonker is priced by SPEED only - lmstudio Q4_K_M, MTP2, ~2 tok/s (reads ~365), a patient lane, no role cells; rigby's
  3.8 skipped (older CPU, no AVX2). What would change it: VRAM (each GB of the model on the card saves ~1/11 of a token).
- Held for after Phases 2/3: the A1 other-reviewers arm (Nemotron 4B reviewer on BigHonker), B8's commands trial.

## ▶ IN FLIGHT RIGHT NOW (2026-10-17 ~00:55 CT) — the work a compaction loses; REPLACE this block as each line resolves
Earlier blocks (10-09 → 10-11, and the McCoy-wiring job's attempts 1 and 2) are archived verbatim in
`Archive\34-register-inflight-20261017.md`; results live in the rows below and in
[[34-Terminal-Bench-Lead-Level-Grid-Plan]]. Child projects **#36 (closed 10-16, [[36-Closeout]])** and **#37
(completed 10-17 ~00:50 CT overnight, [[37-Closeout]]; the owner's phone check of the new controls pending)** are done;
their hand-offs are owned here (§ "HAI agent surface").

- **McCoy's wiring (Phase 8) — DONE and LIVE (`3c09e70`, deployed 2026-10-17 00:45 CT, gate 2825).** HAI's coding job
  (attempt 2, run `b6b1b3a3…`, task `203031cf…` complete, checkpoint `d355476f…`, review 0 findings) wired
  `mccoy.observe(q)` into `app._run_query_core`: once per run, special routes / machine users / pinned routing log a
  skip, every other Auto turn is triaged in the background (tev1 on Softserve `/v1/systemone`, ≥ 0.90 decides, else
  `lead`), routing UNCHANGED. Main-session merge: onto tonight's tree; review fix — the triage call moved onto
  `post_supervised` (its first live call took 28.0 s cold against the old 30 s clock); the image needed
  `COPY mccoy_questions.json` (the gate's pin caught `import app` failing). Proven live: `mccoy.triage` answered
  (`8gb` @ 0.44 → `lead`), the front door logged its first row (`skipped: machine`, `State\mccoy\2026-10.jsonl`).
  Softserve's systemone count did not move across the gate run (371 → 371; my one probe → 372). Also #37's answer-7
  proof: compaction at 90 % carried on; the finish gate passed honestly (last edit 00:07, unit green 00:08, checkpoint
  re-run 00:31). **Next on this line (checklist C):** read the shadow rows as they land (the owner's first Auto turns);
  C2 closed 10-17 (no labels from the owner — rungs are placement, not triage); C6 DoD 7.
- **The effort pin — LIVE** (`e2ffaa1`, deployed with the merge): Effort = the model's own level (Auto · Off · Low ·
  Medium · Max), a pin that follows the account; Work = Answer / Think hard. The Auto half (effort from Spock's row)
  rides McCoy's routing into Spock (checklist C).
- **PHASE 7 + 8 CLOSURE CHECKLIST (built 10-17 00:2x CT under the owner's rule "no new phase until every earlier one is 100 %
  closed" — § Standing decisions). Phase 9 opens only when every line is ✅ or names a block outside the lab's reach.**
  - **A. Void xhigh re-runs (§ EFFORT SANDBAG #2)** — Mothership, AFTER the McCoy-wiring job frees it; each re-submitted
    as a NEW order with identical args (a `requeue` would skip every row already answered), proved by effect (+42
    prompt tokens). ✅ A1 `chat1` 3.8 xhigh-r2 post-0.40 re-check (result below) (its void delivery is now on `menu_void.json` ✅ and
    `reduce_chat1` honours the void list ✅ — it had pooled those MEDIUM rows into the 3.8's xhigh chat price; class 17,
    fixed 10-17) · **✅ A2 `cascade-lead-checks` DONE 12:3x CT — `escalation` ("hard code starts at the lead") HOLDS at
    TRUE xhigh, stronger** (t06 `164be9b82f09` + sb4 `22d623e56721`, `cascade-select-w1code-xhighfix-{t06,sb4}[-8gb].md`;
    sb4 was paused and requeued twice for deploys, which adds at most a ~1 min model reload per resume to totals in the
    thousands of seconds). In all 6 replicates the cascade solves exactly what the lead alone solves (7/8, 6/8, 6/8):
    **from the 8 GB tier at 2.6–4.3× the lead's time and 2.6–4.3× its energy** (t06 2.6–4.3× / sb4 3.0–4.2× time;
    5,378–6,957 s vs 1,501–2,298 s; 2.06–2.67 MJ vs 0.58–0.89 MJ) and **with the Fellowship at 5.3–7.9× its time**
    (t06 5.3–7.7×, sb4 5.5–7.9×); valid check suites 3/8 (t06) and 3–5/8 (sb4). One variable vs 10-10: the lead-alone
    rows are the same 10-07 xhigh delivery, only the check-writer's effort changed (medium → xhigh) — the checks cost
    more and buy nothing. Re-read recorded in `menu_policy.LEAD_DEFAULTS["escalation"]`, table regenerated 12:35 CT
    (value unchanged) · ✅ A3 A1 spot
    `…-thon-spot-exhigh` (result below) · ✅ **A4 DONE 19:47 CT 10-17 — the first TRUE-xhigh Terminal-Bench runs of the 3.8** (`campaigns\tbench\delivered\
    tb-{solo,errand}-q38-xhighfix.20261017-1515_softserve\out.json`): **solo 8/10**, 199 calls, 96.2 min model time,
    1.92 MJ (12.0 min · 240 kJ per solve); **errand arm 8/10, the SAME 8 tasks, 0 errands** (the lead never delegated;
    helper 0 calls), 176 calls, 78.3 min, 1.58 MJ — so the errand arm is a second solo replicate (spread 78–96 min).
    **This CONFIRMS a known result, not a new one:** the void (really medium) errand arm also sent 0 errands in 10
    tasks ([[34-Terminal-Bench-Lead-Level-Grid-Plan]] § errand arm) — the 3.8 lead does not delegate at medium OR xhigh.
    0 calls at the context limit in either. Both miss configure-git-webserver + raman-fitting. Against the void
    medium row (7/10, 38.4 min, 628 kJ, 5.5 min · 90 kJ per solve): xhigh +1 solve for ~2.2× the time per solve.
    The helper was refused 17:41–18:52 (BigHonker's post-RAM-swap startup apps pushed the 9B's load over
    `gpu_spill`'s full-card line; closed, it loaded 18:52) — no errand was asked in that window, so nothing was lost.
    ✅ **Q6 re-read DONE 2026-10-23** ([[34-Terminal-Bench-Lead-Level-Grid-Plan]] § SOLO VERDICT + § ERRAND ARM RESULT; the 10-11 passages archived): delegation holds (3 runs, 0 errands); the lead LEVEL holds on quality (8/10 vs 5/10) and time (12.0 vs 31 min per solve), but energy per solve is now about equal (240 vs 226 kJ) — the 10-11 energy win was the MEDIUM run. Was: 🔧 A4 Terminal-Bench solo (`tbwin-tb-mo-qwen3`) + errand (`…-th-err-bi-…`)
    via `workspace\tbench\submit_tb.py` → re-read Q6's SOLO LEAD LEVEL and DELEGATION verdicts. **Running since 12:34
    CT 10-17:** solo `c6ff75856bca` (Softserve; the 3.8's window `1a7ace7d201a` on Mothership), errand `4e8ca19a3c05`
    next. ⚠ **A slow spell 12:38–12:54 CT:** window calls r00009/r00010 wrote at ~16 tok/s (r00001–08: 37–73; 62 again
    from 12:55), while Mothership's sshd stopped answering its banner and the deck's reporter went silent (`health DOWN`,
    age 361 s) — the model stayed 100 % resident (`/api/ps` 17.9/17.9 GB, the only model). The SCORE cannot carry it
    (`tb_run.sh`: `--agent-timeout-multiplier 50`, trials end only on the no-progress rule, and slow is progress); the
    TIME price does (~12 min extra on two calls) — name it beside the solo arm's wall time. After A4, read the spell's
    cause from Mothership's own logs (not probed while A4 runs). **⏸ STOPPED 14:27 CT for the layout-C RAM swap**
    (the owner: *"I need both MS and BH to be idle"*): errand `4e8ca19a3c05` cancelled at the desk before collection; solo
    `c6ff75856bca` stopped by its `.stop.json` slip at 7/10 trials (a TB arm cannot resume — it re-runs from zero);
    window `1a7ace7d201a` closed. Both resubmitted identically after the swap (B8 still deploys only after the new
    errand arm has started). The 3 errand decoys
    measured nothing: not re-run. **⏳ QUEUED 2026-10-17 00:47 CT** (desk ids): A3 `b750bcd77add` (running first,
    priority 101) · A1 `2400c9278878` · A2 `164be9b82f09` + `22d623e56721`; A4 waits for those four (a TB lead on
    Softserve would sit waiting on a busy Mothership lane and inflate its own timing), plans diffed against the void arms
    first — 0 differences with `--lead-ctx=122880` / `--helper-ctx=14336` / `--helper-think=on` (new `submit_tb.py`
    flags). A3 differs from its void run in the harness version only (A1 gained compaction 10-15) — say so beside its row.
    **✅ A3 DONE 04:36 CT — 8/10 at TRUE xhigh, proven by effect:** the first lead call reads 2,066 prompt tokens, equal
    to the pre-0.40 xhigh run (10-07, unaffected) and +38 over the void medium run (10-10: 2,028). Same arm, one run
    each: medium 6/10 (void) · xhigh 7/10 (10-07) · xhigh 8/10 (tonight). Misses: react 13/14, paasio 24/25. No context
    ceiling hit (`length_at_ceiling` 0, no compaction); 4 exercises had a turn cut by the loop guard (both misses among
    them). **✅ A1 DONE 04:45 CT — chat1 re-check:** the 3.8 at xhigh 21/21 at 19.6 s / 7.1 kJ per solve, the pre-0.40
    "high" row (also xhigh on 0.34) 21/21 at 18.4 s — reproduced; thinking off 63/63 at 3.1 s / 1.0 kJ. Saturated at
    every effort, so the charter line holds: simple chat → thinking off.
  - **B. Phase 7 builds** — ✅ **B1 Fellowship destination + B2 Tolkien's in-Fellowship choice — BUILT, LIVE and
    PROVEN on all three paths 2026-10-17 01:14–01:51 CT** (orchestrator `8f15d5c` + `d0ed5aa` gate 2834; Tolkien gate
    841; table regenerated with each Fellowship row's `order` fields; A1 payload in sync on all three GPU boxes, A1
    203): Spock ranks "the Fellowship" as one destination and sends the customer's Fellowship form with `placement`;
    Tolkien picks within the Fellowship's RUN of the table the first PRESENT row, else hands the piece BACK to the desk.
    Live (the 1060 was off the bus, so the paths ran on real conditions): `fellowship` tier → warm on gandalf, correct
    in 1.9 s; `rigby` rung → handed back → BigHonker (`nemotron-3-nano:4b` from the row), correct in 0.5 s; after
    rigby's reboot (06:45 UTC) → the standard 1060 order, correct at 16.5 tok/s. Fixed on the way: piece payloads now
    inherit f2's whole surface (`extract_code` was missing — it had also broken RigbyDelegator's pieces since the
    one-grader move); a re-placed order record keeps its spec's name and the delegator reads where an order ENDED;
    the 1060's presence is the build's own device lookup and any rigby card order WAITS (one DM per drop) instead of
    shipping VOID; ✅ **rigby's CARD KEEPER** (10-17, the owner: *"adding a quick reboot once it sees an order and no card"*;
    Tolkien gate 854, host timer installed and decoy-proven) reboots rigby when an order waits on a card the driver
    cannot list and both lanes are idle — once per drop, then gives up and the DM names it hardware
    ([[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]] § Xid 79). Detail: [[34-Phase-7-Placement-Policy-Design]] § D, [[Fellowship-Order-Taker-Design]] § PLACED
    orders, journal 10-17 · ✅ **B3 MEASURED AND DECIDED, both halves — REASON: no selector; OPS: tev1 4B is the
    selector** (`submit_w1_judge.py`,
    `Project34-Menu\w1_judge.md`). **The bar, restated 10-17 at the owner's question ("is that an arbitrary 3%?"):** the rule
    stated before the data borrowed S10b's code-piece checker rate (false pass ≤ 3 %, 4 of 360 on 10-09) — measured, but
    another selector's number. The right bar is **the lead's own error rate on the same answers** (qwen3.8:27b on
    Mothership, thinking on, from the same usable menu rows; the reducer now derives it from the key): a cheap answer
    the judge vouches for must be at least as likely right as asking the lead. **Reason (2,476 answers):** the lead is
    wrong on 3 of 96 = **3.1 %** (thinking off 5 of 96 = 5.2 %; medium effort, so true xhigh is likely lower — a bar
    generous to the judge); best tev1 4B @ 0.99 = **5.6 %** (CI 3.1–10.0) on 178 of 1,415 accepted (12 %), and even
    counting the 33 context-refused answers as correct accepts the bound is 4.7 % → fails under either bar → **W1 reason
    pieces stay the lead's.** ⚠ **The first run judged NO ops answers:** every original ops row is void (letter-biased,
    superseded by the shuffled `ops_*_sh` tasks in `hard_tasks_w1ops`) and the judge's task table held only the
    originals, so ops matched zero rows and nothing said so (class 4a). Fixed: `TASKS` includes the `_sh` tasks and
    `build()` refuses any kind that matches nothing (test_w1_judge 12). **Ops (719 answers: 432 lead box, 287 8 GB;
    order `b0dd166dc56e`, 08:19 CT):** the lead is wrong on 0 of 72 (on and off) → bar 0 %. **tev1 4B @ 0.95 accepts 165
    of 287 below-lead answers, 0 wrong (CI 0–2.3 %), keeps 165 of 265 passes**; 0.8B @ 0.70: 92 accepted, 0 wrong. It
    discriminates within a task, not by rejecting hard tasks (`ops_stale_image_sh`: 18 right / 18 wrong → accepted
    exactly the 18 right), and it also passes the old 3 % bar. **Scope:** 8 questions; the 22 wrong answers sit in 3
    of them; the below-lead workers are only qwen3.5:9b + qwen3:8b, thinking off, on the 8 GB boxes (no Fellowship or
    rigby ops rows exist) — the judge is in effect solving 8 multiple-choice questions. **Decided 2026-10-17 by the
    charter and the data** (the owner: *"do what we have outlined in the project charter and what the Data is telling us"*):
    the charter names "a checker" among the selectors production has, and "no model picks routing" binds PLACEMENT —
    the judge only accepts an answer; what it will not vouch for goes WHOLE to the lead, moved by Kirk/Spock. Priced
    from the menu (§52, the cheapest source that reliably meets quality): per ops piece qwen3.5:9b off (~4.2 s,
    ~500 J) + tev1 (0.2 s), the 22 % it will not vouch for to the lead → **~6.3 s / ~1.25 kJ against the lead alone at
    its cheapest perfect setting (thinking off, 8.7 s / 3.3 kJ)**, ~78 % of ops pieces off Mothership. Recorded as
    `policy.json` `roles.lead_defaults.ops_selector` (preliminary) + `reason_selector` (none) — a decision record like
    `escalation` / `delegation`, which no runtime reads yet; it shapes placement when a caller sends W1-ops-shaped
    pieces (Phase 8) ·
    ✅ **B4a "the owner first"** LIVE (`3c09e70`): Mothership's slot orders quick before jobs and, within each, the
    `PRIORITY_USERS` (default `the owner`) first; never preempts a running job · ✅ (10-23: overflow live since 10-22; the "live run is OCCUPIED" decoy PASS 10-23) **B4b overflow** (an Auto request that
    would wait behind another user's work goes to Spock's next box with its caveat) — needs Auto's tier from McCoy's
    routing, so it follows McCoy going live (C). ✅ **Its "a live HAI run is OCCUPIED" half is DONE in Spock (10-17,
    orchestrator `44c9af0`, gate 2842):** a HAI job's own chat calls hold a rolling `live` claim on their box
    (`ollama_client.track`, `scheduler.LIVE`); background kinds that `yields_to_live` (review) skip that box, and every
    hand-coded fallback asks `scheduler.occupied_for(url, model)` — a gpu claim always blocks, a live claim blocks
    unless the fallback would send the model already serving the job (chat's small brain, the auto-title, the genetics
    helper, the claim sweep). the owner's question moved it there: *"it's supposed to wait or use a different device per our
    routing rules right?"* — the sweep's private check was the "every caller remembers" shape (doctrine 12e). The
    `review` kind stays sized for the 27B class (16 GB, Mothership only — the runbook's reason), so a review WAITS while
    Mothership is busy rather than moving to an 8 GB box's 9B. ✅ **Live proof SEEN 2026-10-23 09:13 CT** (journal 10-23 § A2: the B4b decoy turn's lead-effort probes put live claims on BigHonker and Mothership). Was owed: gated (6 tests) and deployed, not
    yet seen on a real HAI job — the next one shows a `live` claim on its box (`scheduler.claim_for_url(url, LIVE)`);
    no synthetic job while Mothership runs the cascade / TB measurements (co-location) · ✅ **B5 a critic on ANOTHER box — DECIDED 2026-10-19 ~01:55 CT: NO (no gain measured).** Arm desk `d086bb7efb25` (re-run after the 00:37 run `713d55873566` went VOID — 10/10 reviews failed to load on BigHonker's MoE-cache env): A3's finish branched, reviewer BigHonker nemotron-3-nano:4b thinking on. **8/10 solved = A3's 8/10** (react, paasio fail either way); the reviewer marked paasio — a real failure — "NO DEFECTS", prompted 2 repairs (rest_api, zipper) on exercises that already passed, and cost 20–69 s of wait per exercise; 0 void, 0 review errors. Scope (the owner pass): n = 10 exercises, one reviewer model; matches the 10-09 lead default "reviewer no". Revisit only if a reviewer with a measured edge on the lead's misses appears
    (does a different model as critic help at all?), queued after A · ✅ **B6** chat's hand-coded fallbacks (Mothership's
    small brain, its wake, the recovery list, Softserve) ask `claim_for_url` (`3c09e70`, 3 tests) · ✅ **B7 DECIDED 10-17: the union `fellowship`
    tier STAYS** — it is the destination McCoy names; the `rigby` / `tablets` rungs are table rows that Tolkien picks
    among (B2) and Spock prices, never triage labels (C2). `cascade_select` needs no rung split either: A2 closed the
    cascade question with the Fellowship answering 1–3 of 8 pieces per replicate at the same solves · ✅ **B8 the errand arm with FETCH + simple COMMANDS — DECIDED 2026-10-19 ~06:45 CT: the lead still works alone.** Built 10-17 (`workspace\tbench`, `lab_errands` mode `commands`: RUN one simple command in the errand's private copy, FETCH one page, `fetch_audit.py` voids self-fetched tests/solutions; `submit_tb --errand-mode=commands`). Decoy (desk `9a087ebab1e3`, fix-git) solved 1/1 with 0 errands — its trajectory shows the lead received the full commands offer and chose not to use it. **Scored arm** (desk `b9199b7f057b`, A4's errand arm + commands, one variable): **7/10, 0 errands, helper 0 calls** (A4 look mode 8/10, 0 errands; solo 8/10). The one difference is mteb-leaderboard (A4 solved it): 71 steps, the conversation filled the 3.8's whole tested context (122,875) and cycled compact → refill → cut for ~2.5 h (47 of 242 calls `length`, all in that task) until the no-progress cutter ended it — a runaway at the tested ceiling is the model's result, scored (the a1_validity / Ornith precedent), not void; the other nine tasks ran 6–34 steps. `lead_defaults.delegation` stays "the lead works alone" (3 runs, 0 errands). **Conditional under it NOT built:** the Fellowship errand WINDOW (errands measured nothing to build on).
    ✅ **B9 lend the GPU while a HAI job's tools run — RESOLVED BY MEASUREMENT 2026-10-19 ~01:00 CT: no new
    mechanism; the between-turns handover (`7be72e9`) IS the design.** Read from the five saved runs
    (`State\hai-runs-saved`, the feed's `tool_result.ms`; 173 tool results = the feed's 91+79+3): 166 of 173 tools
    return in < 1 s (median 14 ms). The card is idle only during `coding_test` (n=6, 55–67 s) and
    `coding_checkpoint` (n=1, 51 s) — **2.6 % and 3.8 % of the two real coding jobs' wall time**, against a model
    turn that holds the card ~59–75 s (wall minus tool time over turns; includes compaction). `coding_review`
    (131 s) is NOT idle: it is a 3.8 call on Mothership at the job's own context (`ctx_for` → 122,880, no reload).
    **Other placed work cannot use the windows:** anything but the resident 3.8 at the job's context evicts it, and
    taking the job back costs the reload (43–45 s) + a re-read of 60–122k tokens at ~2,300 tok/s (26–53 s) ≈ 70–100 s
    — more than the 51–67 s window. **Quick requests on the 3.8 already get the card** at the next turn boundary
    at ~2–3 s cost to the job; lending them the test window too would shorten a quick wait only when it lands
    inside a test (~3 % of a job's time), by ≤ 67 s. Evolutionary rule: no build for a shape that measures ~nothing.
    **Scope (the owner pass):** n = 2 real jobs, both one task (the McCoy build), tests run against the orchestrator
    suite; **revisit trigger:** a job class whose model-free tool time reaches a model turn's length regularly
    (a long test suite, a build) — re-run this read on its saved runs. ✅ (10-23: priced on both; Phase 3 closed on it 10-18) **B10 the A3B priced on BOTH rigby and BigHonker — not either/or (the owner 2026-10-18: *"we are having it on both and
    we are simply pricing out ITS costs in each category for each box. Hopefully they will be equivalent in quality and
    it'll be mostly just a speed and energy difference"* / *"It's OK if we have to use a different model version between
    the two of them, it's just slightly less than ideal if they have differences in the way that they output"*): each box
    runs the file that fits ITS hardware, one shared file preferred when it costs nothing; if they differ, quality is
    priced per box like every row, so an output difference is measured, never assumed (gen 0 2026-10-18: the hardware-fit K-quant, plan
    `bh-a3b-quant-gen0.20261018.json`; rigby: 24 GB DDR3, AVX only — a Q4 K-quant fits, Q4_0's repack needs AVX2) — RAM IN (layout C, 10-17 ~14:45 CT:
    BigHonker 32 GB = the 2 × 16 Trident Z Neo 4000 from Mothership, running 4000 MT/s, 0 WHEA after boot) and
    ROUND 1 RUN** (`speedcheck-bi-qwen3.6-35b-a3b-ud-q3kxl-mtp-ctx65536-clkunlocked-a3b-split.20261017-145324`, desk
    `d982d3d53348`, the staged `draft-bh-a3b-20261017.md` as written: ollama, thinking off, t 0.6, ctx 65,536, salted +
    cold, `--offload-ok`, stock clock): **split 6.8 of 18.0 GB on the 3070; WRITE 19.6–23.9 tok/s (median ~21.6) on
    rows 2–11 vs rigby 16.9**; 11/11 `stop`, 0 errors, 0 limit hits; reads 126–177 tok/s on ~110-token prompts (NOT the
    long-read rate). **Long read** (`…-a3b-split-longread.<stamp>`, desk `7e9135d870f7`, `make_longread_items.py`
    six runbooks, one-word replies, salted + cold): **READ 555–589 tok/s on 2,500–9,400-token documents** (all `OK`,
    0 errors) vs rigby ~100–109 tok/s at 12K — ~5.5× faster reading, ~1.3× faster writing, same file. 0 WHEA on
    BigHonker after both loads. **TUNED 10-17 15:41–16:50 (`workspace\flashnext\ladder.py` orders `flashnext-bh-a3b-*`,
    plans in `workspace\flashnext\plans\bh-a3b-*.json`, results in `…\delivered\`; same blob, ollama 0.40.5's own
    llama-server, fixed prompt, 500-token forced answers, t 0):** ① **THE CARD PARKS** — unlocked, the 3070 decodes at
    **~450–525 MHz P3** (measured live during a generation: 1080 → 480 MHz in 15 s, idle throttle, 30–40 % util): a
    half-CPU MoE decode is bursty and the driver clocks down between bursts. Same config, `gpu_clock 1965` (the card's
    factory-boost floor, 1965–1995 MHz, `BigHonker-3070-DPC-Watchdog-Investigation` §0/§9h): **25.3–26.6 tok/s vs
    20.3–21.7 unlocked**, unlocked → locked → unlocked, three locked orders agree, no overlap. ② ollama's stock launch
    IS rigby's recipe (llama.cpp's own fitter, experts in RAM; its serve log: `-np 1 --load-mode none -ctk/-ctv q8_0
    -fa on -b 512 -ub 512`, 3 threads); rigby's ub 2048 + f16 KV cost expert layers on the card (14–15 tok/s).
    ③ Threads 4 > 5 > 3 >> 6 (DDR4-bandwidth-bound expert decode, Flash-Next ledger; 6 starves the driver thread).
    ④ fit margin 512 +5 % (one more expert layer). ⑤ ub 2048: −4 to −6 % writing for **2.4× reading** (1,590 vs 650
    tok/s on a 4K prompt) — kept, BigHonker's A3B role is long reading. ⑥ MTP loses here at depth ~3 (57 % acc.) AND at
    the lab's full form, depth 2 (72 % acc., 22.2–22.7 vs 26 locked) — experts in RAM make every verify token pull more
    RAM. ⑦ Off on purpose: expert cache (#36: off on 8 GB), repack (no AVX2 kernel for iq3_xxs/iq4_xs), q4_0 KV (+2 %
    for a quality cost). **Launch:** `-fitt 512 -fitc 65536 -c 65536 -t 4 -tb 6 -np 1 --load-mode none -ctk q8_0
    -ctv q8_0 -fa on -b 2048 -ub 2048` + a clock that cannot park. **Result: writing 25.3–26.6 / reading ~1,590 vs
    stock ollama unlocked 17.7–18.2 / ~640 (+44 % / 2.5×) vs rigby 16.9 / ~105 (12K; 4K here).** Instrument note: a
    GPU sampler that spawned `nvidia-smi` twice a second read the same arm 23.5 (A/B/A with the in-process NVML sampler:
    off 25.8–26.3 · NVML 25.6–26.2 · off 25.3–26.6) — the ladder now samples through NVML. **Open:** the clock's
    production form — a FIXED lock holds that frequency continuously (the investigation saw a locked 1800 die faster
    than dynamic boost; hours-long lock on the new PSU unmeasured), a FLOOR (`-lgc <min>,<max>`) stops the parking
    without holding peak — the owner's call (box setting; he is setting NVIDIA "Prefer maximum performance" instead, 10-17
    ~20:30 — then one unlocked order with the NVML sampler proves the card holds its clock). **Engine round (varied
    prompts, 1965 MHz): llama.cpp's expert cache `--moe-cache-mib 1024` = the winner** (26.5–32.9 tok/s vs 22.9–24.2
    without; 1,536 overlaps, 768 lower, 2,048 spilled; + MTP2 still loses, 22.4–25.9; CUDA graphs off −22 %; ub 4096
    reads 2,050); safe at the box's fit margin 512 (29.3–32.3, no spill). **SERVING (the owner OK 20:1x):** BigHonker's
    launcher gained `LLAMA_ARG_THREADS=4` + `LLAMA_ARG_MOE_CACHE_MIB=1024` (RUNBOOK-Ollama-Host-Operations, BigHonker
    entry); serve.log proves `n_threads = 4`, `MoE cache size = 1023.94 MiB`; one ollama answer 37.1 tok/s (single
    prompt, clock free). **MENU (the owner 20:3x "Offload allowed"):** `menu_fill.TESTED_CTX` bighonker A3B 65,536 +
    `OFFLOAD_OK`; the 10-16 hold lifted (backup `menu_fill_hold.json.bak-20261017-204004-pre-lift-bh-a3b`) → 35 cells
    MISSING, submitted by the `HAI P34 menu refresh` task. **The cells price the TUNED launch** (the owner: *"set the menu to
    run our tuned setting for BH"*): threads + expert cache are the box's env, and `num_batch 2048` is the model's own
    default since 20:47 (`PARAMETER num_batch 2048`, same blob) — proven with a request that passes no batch option:
    serve.log `n_ubatch = 2048`, `MoE cache size = 1023.94 MiB`, `n_threads = 4`. Not carried: `-tb 6` (no env;
    reading runs 4 threads) and the clock (the owner setting NVIDIA "Prefer maximum performance"). The 10-16 framing stands — rigby's claim is the A3B work BigHonker has no time for, not
    the model.
    **Owed (energy, parked by the owner 10-17 "we're getting off track"):** the menu's `gpu_j` is the CARD meter per
    answer (consistent across boxes; rigby rows add a measured ~85 W CPU adder), which undercounts CPU-heavy runs —
    Flash-Next on Strata: card 372 kJ / 301 W avg with the CPU at 94.5 % vs the champion's 494 kJ / 364 W at 28.6 %
    (same 8 prompts). Mothership's CyberPower GX1500U READS WHOLE-BOX WATTS over USB HID with Windows' own hid.dll,
    shared with PowerPanel (no install, no shutdown change): `ActivePower` 150 W, `PercentLoad` 16 %, runtime, volts
    (scratchpad probe `ms_ups_hid.py`, 2026-10-17 22:1x). **LOGGER LIVE 2026-10-17 23:26 (the owner: *"make sure we're
    recording the live energy readings throughout the runs tonight"*):** task "UPS Power Logger" → 
    `F:\foundry\var\state\telemetry\ups_power.csv`, 5 s rows (RUNBOOK-Foundry-and-Rescue-Daemons § UPS Power Logger);
    first rows 523–531 W during the Flash-Next lead run. **BigHonker's APC** (logged 5 s by its GPU Telemetry Logger,
    `ups_*` columns) reads `COMMLOST` since ≤ 10-15 — no APC USB device enumerated (cable likely not re-plugged after the
    10-16 PSU / RAM work). the owner 10-17 23:5x: replug tomorrow, then backfill the gap — open question: whether the ES 750G
    keeps any load history on-board to backfill from (not confirmed). **Since 2026-10-30 the menu prices every answer
    at the wall** (`energy_j` = whole box, measured or the box's fitted line; `card_j` beside it —
    [[34-Hard-Bench-Order-Spec]] § "The menu prices the WHOLE BOX at the wall per answer"). The A1 and chat
    reducers follow the same rule, and the tablets are priced at their USB port's spec ceiling (2.5 W each). Still owed:
    HA's `mothership_ups_power` (`unknown` since 09-25). ✅ (10-23: B11b closed 10-22 on the GPU boxes; the caller path verified in `helper_window.py` 10-23) **B11 KV dump IN pause-and-redeploy — BUILD (the owner 10-17: *"I feel like it's the obvious thing to add in whenever
    we're trying to do a pause and redeploy… Maybe it wouldn't work 100.000% of the time, but dang good"*).** Where it pays,
    from the code: a GPU-factory pause-and-redeploy restarts the order taker, not ollama, and A1 checkpoints before every
    model call (`a1_order`: resume replays the turns, then goes live) — the model stays loaded, its RAM prompt cache serves
    the resumed turn. A TOLKIEN deploy recreates the container and kills rigby's llama-server, so a resumed rigby A1 task
    re-reads its whole context at rigby's read speed (~100 tok/s: ~10 min at 60k). Build: for rigby llm orders a deploy
    stop waits for the TURN BOUNDARY (the hybrid's recurrent state cannot be rewound — a mid-answer dump is useless), saves
    the slot (`--slot-save-path`, the #36 handoff machinery, `tolkien_llm.handoff_body`), and the resume restores it before
    replaying; a restored prefix that does not match exactly falls back to today's full re-read (never worse). Proven
    when a stopped-and-resumed rigby order's first live call reads only the new tokens. **Read from the code 10-17:** a
    stop ABORTS the in-flight answer (`tolkien_llm.chat`: `finish = "stopped"`) and the resume re-asks the same question
    — so the dump must hold exactly that QUESTION: abort, have the server read the question with no answer
    (`max_tokens` 0), save the slot, stop; restore before the replay. That is #36's proven handoff shape (reader reads
    the prompt, saves; writer restores, re-reads 1 token — on rigby's hybrid A3B). **Unknown, measure FIRST:** what the
    re-read at stop time costs after an aborted answer (how far llama-server's context checkpoints let a hybrid rewind) —
    a rigby measurement order, then the build. **✅ MEASURED 2026-10-17 11:23 CT** (order
    `dumpprobe-rigby-a3b-p30k.20261017`, Tolkien gate 863, rigby's standard 1060 launch — Qwen3.6-35B-A3B MTP Q3_K_XL,
    CUDA, fit 1024, ctx 65,536; usable, control re-read 30,000 / 30,000): a deploy's resume at a 30k-token context
    costs **3.1 s with the dump vs 296 s without (~96×)** — the stop re-read only **3 tokens** after a cut answer
    (llama-server's context checkpoints rewind the hybrid; 0.46 s), save 1.1 s (813 MB), restore 1.0 s, the resumed
    turn re-read **1 token**. Caveat: the resumed answer is not byte-identical to a fresh one (float/batch order, MTP)
    — both valid. Not yet proven: the build saves the TEMPLATE-RENDERED conversation (the probe used raw tokens), so
    the resume must render the same messages. ⚠ **That probe dumped the QUESTION and re-asked — a turn-boundary save,
    not what the owner asked for** (*"I've been asking for #1 this whole time"*: dump the HALF-WRITTEN answer and CONTINUE
    it from the exact token; turn/tool-boundary checkpoints are #37's, live `30ff407`). **the owner's hypothesis:** continue
    (1) + the sampler's random position (2) + the MTP draft state (3) = a LOSSLESS 1-to-1 resume. **Testing it
    (submitted 11:57 CT, Tolkien gate 866):** `dump_probe` mode `continue` — reference n tokens straight through;
    the same question cut at `cut`, dump, restart, restore, continue from the exact ids (`return_tokens`); compare
    token-for-token, plus fresh-vs-fresh (does the engine even repeat itself) and continue-WITHOUT-the-dump. Three
    orders, one variable each vs O1: O1 MTP on temp 0 · O2 MTP off temp 0 (#3) · O3 MTP on temp 0.6 (#2).
    **Round 1 (12:01–12:15 CT; 4k prompt, cut 128 of 384; fresh runs agreed on all 128 in every order):**
    **#1 CONFIRMED — MTP off, temp 0: dump + continue is LOSSLESS (384/384 tokens identical), while continuing after a
    full re-read WITHOUT the dump drifts at token 213** — only the bit-exact restored state gets there (re-reading in
    big batches rounds differently). **#3 is the gap — MTP on, temp 0: dump and no-dump BOTH diverge at token 129**
    (the restored state is exact; the MTP draft state is not carried or its batch shapes differ). #2 NOT TESTED: MTP on
    temp 0.6 came out identical, but the prompt was repeated boilerplate (near-certain at every token — a result that
    could not move). Round 2: a real document (`Runbook/RUNBOOK.md`) + an open question, near-ties counted from the
    server's top-2 probabilities; R1 MTP off temp 0 · R2 MTP off temp 0.6 (#2) · R3 MTP on temp 0 (#3).
    **Round 2 (12:13–12:26 CT; real text, 32–35 near-ties per answer — the answer CAN move):** **R1: LOSSLESS
    384/384 with the dump; without it drifts at 192 → #1 PROVEN on real text, and only the dump gets there.** R2 (temp
    0.6): both diverge at 131, three tokens after the resume — the sampler's random stream restarts (#2 is a REAL
    gap). R3 (MTP on): dump 280, no-dump 136 — the drafter's state is not in the slot (#3 is a REAL gap). (MTP on also
    reports degenerate probabilities: its near-tie count reads 0 — not the temperature, as first guessed.) **the owner's
    hypothesis holds piece by piece.** Path: (a) BUILD the continue-dump into Tolkien's deploy (lossless now for
    greedy non-MTP orders; exact up to the stop and coherent after it otherwise, and it saves the full re-read); (b)
    lab ENGINE patches on rigby's llama.cpp: save/restore the sampler RNG state (#2) and the MTP drafter state (#3)
    with the slot, then re-run R2/R3 expecting 384/384. **Build step 1 — the CHAT path (order `dumpprobe-chat-nomtp-t0-r2.20261017`,
    submitted 13:06 CT, Tolkien gate 879; one variable vs R1: the path. The first run, 13:01, was VOID on my own
    request — a reasoning-only prefill with no `content` key, which the server refuses — and it shipped `steps_done 0`
    after two full reads; both fixed: the prefill carries `content: ""`, a failed probe keeps its measured steps and
    the server's own reason):** the orders speak the chat
    API and a stop LEAVES THE STREAM, so mode `chat` reads the exact live state from the slot file, rebuilds the half
    answer as a `continue_final_message` prefill, checks that it renders to the saved ids, continues, and compares
    ids. Read from the engine source first (23b0202): the slot never holds the last token the server SAMPLED (decoded
    only on the next step), and a continue that equals the cache makes the hybrid roll back to its prompt-end
    checkpoint and re-read the half answer — so the probe adds an ORACLE continue with that one token. If the oracle
    is lossless and the plain continue is not, the pending token is a third thing to save with the slot (beside #2
    and #3). Order field documented: [[Fellowship-Order-Taker-Design]] § QUICK RESUME probe. **r2 (13:11, all 5 steps)
    — its ID comparisons are VOID:** the server saves `server_tokens::serialize()` (a PACKED list: [-1][1][count][ids]
    [media]), and my reader took it as raw ids — off by 4, with the count at index 2 "differing" between every pair
    (DECLARED ≠ PRESENT: I read the file WRITER, not what the server hands it). What r2 does show, from the server's
    own counts: the restore brought back 4,139 tokens + 2 context checkpoints, and the continue reused 4,006 of them and
    re-read 136 (the half answer) vs the control's 4,142 — so the chat-path dump saves the prompt re-read, and the
    rendered prefill parted from the saved state somewhere after the prompt. **r3 (13:24) — instrument clean, one
    prefill bug:** prompt matches the server's (4,010, read cold); the two fresh runs agree token for token; the cut
    slot holds prompt + 129 generated (the client saw 128; the 130th, sampled, is in no cache — as the source said).
    **The text `chat()` holds at a stop renders EXACTLY to the saved ids** (`client_render_match`: the server's
    parser and renderer round-trip). My slot-text prefill rendered `<think>` `\n\n` where the original is `<think>`
    `\n` — the engine's prefill re-adds the template's newline and I had kept it too — so the continue, the oracle and
    the control all parted from the reference at 4009: no lossless verdict from r3. r4 keeps whichever prefill form
    renders to the saved ids (`pick_prefill`, gate 881). **✅ r4 (13:34) — THE CHAT PATH ANSWERS owner'S #1:**
    the prefill renders exactly to the saved state (`render_match`, form 0 = without the template's tail). **With the
    one PENDING token added (the oracle), the resumed answer is LOSSLESS — all 383 tokens identical to the
    uninterrupted reference, re-reading 1 token after the restore.** Without it the continue equals the cache, the
    hybrid rolls back to its prompt-end checkpoint and re-reads the half answer (133 tokens), and the answer drifts at
    token 212; the no-dump control (full re-read, 4,139) drifts at 184. 18 near-ties in the reference, so identical
    output means something. The pending token was taken from the reference (valid: the fresh runs agree, temp 0);
    one scene, corroborated by R1's raw-path result. **So lossless on the production path = the dump + the client's
    text (which renders exactly) + the pending token, which only the engine knows (`slot.sampled`) → engine patch #0:
    hand it back with the slot save. #2 (sampler RNG) and #3 (MTP drafter state) remain for orders at temp > 0 or
    with MTP — the standard rigby order uses MTP.** **✅ ENGINE PATCH #0 LIVE + PROVEN 2026-10-17 (r6, 13:58 CT):**
    `lab-engine\patches\slot_pending_token_patch.py` — the slot save answers `pending_token` — built into the
    LIVE tree (`llama.cpp-latest` e467e5b → `/llama/cuda`, 18:53Z, marker verified inside the container; pre-patch bins
    archived). r6 on it: `pending_token 424` = the reference's next token; the prefill rendered the saved state + 1;
    the server reused 4,139 and read **1** token; **383/383 identical to the uninterrupted answer** (every pair clean);
    the no-dump control (full re-read) drifts at 184. ⚠ The first build went into `llama.cpp-rpcasync` (the runbook's
    stale mount line — RUNBOOK-DEFECT-LOG 10-17), so r5 ran unpatched and only reproduced r4 via the oracle. **r7 / r8
    (14:05 / 14:09 CT), one variable each vs r6 — the owner's #2 and #3 are both REAL and both the last gap:** r7 MTP on:
    pending matches, read 1, identical until **358 of 383** (no-dump control 169) — the MTP drafter's state is not in
    the slot, so its drafts differ after the restore, the target verifies differently shaped batches and rounding
    flips a close call late (#3: save/restore the drafter's KV + `common_speculative` state with the slot). r8 temp
    0.6: pending matches, read 1, drifts at **137**, 7 tokens after the resume (control 134) — the sampler's random
    stream restarts at the continue (#2: hand the dist sampler's RNG state back with the save and install it on the
    continue). Upstream has neither (community check 10-17). **Build order:** (c) the Tolkien integration first — the
    dump + pending token on every rigby deploy stop (exact for greedy non-MTP, a long exact run then a late drift
    otherwise, and never a full re-read); then engine patch #3 (the standard 1060 order uses MTP), then #2.
    **(c) BUILT 10-17 ~14:15 CT (`tolkien_llm` § QUICK RESUME, `tests/test_quick_resume.py`):** rigby launches with
    `--slot-save-path`; a stop slip that cuts `complete()`/`chat()` dumps the slot + pending token after the stream
    closes (`dump_on_stop`, keyed by the call's `request_key`); the requeued order ACCEPTS the dump at start if made
    for the same launch and engine files (`prepare_resume`) and RESTORES it right before the cut call itself
    (`restore_for_cut`) — first version restored at start, and walking the live proof's requeue path showed the
    resumed order's CONTROL runs first and overwrites slot 0; the cut call then continues from the slot's ids + pending
    token (`continue_for`, render-verified prefill), its row marked `resumed_from_dump` and priced as time before the
    stop + after (no restart gap). A cut inside a tool call re-asks on the restored prefix. Dumps retire when spent, on
    a finished order, or when their order leaves the queue. Gate 899 live 19:21Z; the restore-at-cut-call fix (gate
    901) waits on Mothership: `fleet_deploy` refuses a Tolkien deploy it cannot check against Mothership's grader,
    and Mothership is off for the RAM swap (deployed 20:13Z once it was back, gate 901). **✅ PROVEN LIVE 2026-10-17
    15:27–15:30 CT — LOSSLESS QUICK RESUME ON A REAL ORDER:** R, X and X2 ran uninterrupted — byte-identical answers
    (1,633 tokens, temp 0, no MTP; the setting is deterministic). X3 (`qresume-proof-X3.20261017`, `rewrite_12k`,
    12k-token prompt) got a stop slip at ~605 tokens: Tolkien dumped the slot (15,258 tokens, 510 MB, 0.69 s, pending
    token 198) and retired the order stopped; a `requeue` slip restarted it on a new server — the dump was accepted,
    the CONTROL ran on a fresh slot, the dump was restored right before the cut call (0.62 s) and the call continued
    from token 775 (pending token + exact render), reading **1 token**. **The answer is byte-identical to R's** (4,904
    chars), graded ok, priced 255.7 s vs R's 254.9 s (no restart gap); the dump retired itself after use. **✅ ENGINE
    PATCH #3 LIVE + PROVEN 2026-10-21 — MTP RESUMES LOSSLESS** (`lab-engine\patches\slot_resume_patch.py`,
    built aside in a copy of `llama.cpp-latest` by `build_slot_resume.sh` and swapped into `/llama/cuda` in place,
    `libllama-server-impl.so` 37a885c6…, marker verified inside the container; pre-swap bin archived). Read from the
    engine: r7's drift had two causes — the slot file held neither the MTP drafter's memory (`ctx_dft`) nor its carry
    (`pending_h`, the target's hidden row paired with the next token), and the resume read the pending token ALONE
    where the uninterrupted run decoded it with its drafts as one verify batch, so every later batch was offset and
    CUDA rounded a near-tie differently. The patch saves both with the slot (a `LABR` section after the checkpoint
    appendix) and lets a continue with `lab_resume: true`, whose prompt is the saved state + the pending token, take
    the interrupted run's NEXT STEP. **r9 (r7's spec) / r9b (charter doc, cut 200 of 512), MTP on, temp 0: the
    `lab_resume` arm is 383/383 and 511/511 identical to the uninterrupted answer, reading 0 tokens**; the same
    restore without the flag (drafter state alone) still drifts at 358 (= r7) / 234, the full re-read at 169 / 245 —
    the outcome could move, and only the next-step resume holds it. Neither save landed mid-replay (a hybrid's
    partial rejection re-decodes next step; such a save is reported `lab_replay` and not armed — its rate is
    unmeasured). Tolkien: the probe has the arm (4c, gate 903); the quick resume sends `lab_resume` when the restore
    says `lab_resume_armed` and the render is exact (gate 904). **✅ ENGINE PATCH #2 LIVE + PROVEN 2026-10-21 —
    B11 CLOSED** (`slot_rng_patch.py`, same build-aside/swap; `libllama-server-impl.so` 3801c15f…, `libllama.so`
    50ef3bf0…): the save carries the target sampler's random state (the dist sampler's mt19937 + the rejection rng;
    every task's sampler reset reseeds it, which is r8's drift), and the lab resume installs it after the reset. With
    the default greedy drafter MTP draws no random numbers, so that is the whole state. **r10 (r8's spec, MTP off,
    temp 0.6, 30 near-ties) 383/383 and r10b (MTP on, temp 0.6 — the production shape) 511/511 identical, 0 read;
    r9c (r9 re-run on the new engine) 383/383** — while the old path drifts at 137 (= r8) / 210 / 358 and the full
    re-read at 134 / 207 / 169. Attribution caveat: no arm ran the lab resume WITHOUT #2 at temp > 0; #2's share rests
    on the mechanism (step 4 reproduces r8's drift to the token). Open, measured nowhere yet: how often a stop lands
    mid-replay (`lab_replay` — not armed, falls back to the exact-state continue; 0 of 5 saves so far)
    · ✅ **B12 FOUND + FIXED 10-17 ~02:0x CT: the CLAIM SWEEP.**
    Its own findings log: `## sweep 2026-10-17 00:22 (host: mothership (free; warm (qwen3.8:27b resident)))` — the 4-hourly
    timer (00:10) found tonight's doc edits, `scheduler.place('review')` (sized 16 GB: Mothership only) called the box free
    while HAI's job ran there, and each review sent BigHonker's 9B (`CLAIM_REVIEW_MODEL`) — three reviews, three evictions
    (00:11:09 / 00:17:46 / 00:23:42). No desk order touched Mothership then (2,453 records read; positive control: the
    window widened to 05:50 UTC catches exactly the 4 A orders). **Fixed in Spock** (orchestrator `44c9af0`, gate 2842;
    B4b above): a HAI job's calls hold a `live` claim, `place('review')` skips that box, and the sweep reports the live
    claim and advances no snapshot, so the next sweep reviews the same churn; the review model follows the box
    (`claim_sweep.review_model_for`: Mothership → the lead, as the runbook says: "it does not choose its own host or
    model"). The disabled assistant-manager hook, the other `review` consumer, takes its model from the same lookup.
    (The first fix, a private check inside the sweep, is in `Archive\34-register-history-2026-10.md`.)
    **Second sweep defect, FIXED 2026-10-22 (`ebf63c3`, gate 3027/2): the sweep's calls were invisible.** The timer ran it
    as its own `docker exec … python`, so its `ollama_client.track` registration lived in a process the GPU accounting
    never reads, and every sweep that reviewed something reported `UNACCOUNTED GPU LOAD on mothership` (16:12, 20:12).
    It now runs in the app process through `POST /claims/sweep`; RUNBOOK-Orchestrator-Deploy-Procedure § "The
    documentation claim sweep" has the detail.
    Also: a coding review on the same 3.8 (one slot) evicts the conversation's cache by design — HAI's next turn re-reads
    (00:25:46, 82,830 tokens).
  - **C. Phase 8** — ✅ C1 McCoy shadow wiring LIVE (`3c09e70`, 10-17 00:45 CT; above) · ✅ **C1c McCOY IS PLACED BY
    SPOCK — LIVE 2026-10-21 ~17:30 CT (`1de2be1`, gate 3006/2, Tolkien rebuilt via `fleet_deploy scheduler.py`).** the owner:
    *"it's not a bad idea to run McCoy on any device, but it is definitely going to be best to have it on device on soft
    serve."* Spock kind `advisor` (6 GB, interactive, `home: softserve`, `coexists_with_plex`, `yields_to_live`):
    Softserve wins while Plex transcodes (tev1 measured beside a live transcode) and any other box with tev1 takes it
    when the home cannot; no card = a `deferred` shadow row kept for a replay, never an error (`mccoy.advisor_host()`).
    tev1:4b-q8_0 now installed on all three GPU boxes. Live read-back: `advisor_host()` → softserve, "coexists with
    Plex; free; home". ✅ **The on-demand BUMP — LIVE 2026-10-21 ~18:40 CT** (orchestrator `559aa3a`, gate 3013/2;
    `helper_window.py` via `fleet_deploy`): with no card free, a caller whose work WAITS on McCoy (`lend=True`; door
    `POST /mccoy/triage`) borrows a card held only by Kirk's own idle chat window — the window saves its conversation
    (LEND/LENT) and restores it before its next turn; free cards always rank first; the shadow never lends. Proven live:
    McCoy 6.9 s end to end on BigHonker, turn 2 read 14,549 tok in 0.46 s (cold 5.45 s). Same pass: McCoy's own call
    marked his box LIVE (kind chat) and so kept him off his home for its keep_alive after every call — fixed (kind
    `advisor`). Not covered: a card held by a FACTORY ORDER or a live HAI JOB is never borrowed (an order's mid-answer
    state is not saved by ollama's stock runner; a job's model would be re-read). Runbook § "SLOT SAVE / RESTORE".
    ⚠ **2026-10-23 regression, fixed 14:27 CT:** widening to `*` at 12:44 CT stopped NAMING ClaudeSupervisor, and `mccoy.special_route` rule 7 skips machine users unless named - every ClaudeSupervisor Auto turn skipped McCoy until a three-chat proof showed no McCoy rows at all (14:24). The 12:44 check read the setting back (printenv) and never ran a turn. Override now `*,ClaudeSupervisor` (backup `…bak-20261023-*-mccoy-machine`); pinned by `test_fellowship_spots_and_tolkien_mccoy.MachineUsersNeedNamingEvenUnderStar` (gate 3089). Real users were never affected.
    ✅ **McCoy ACTIVE for `ClaudeSupervisor,the owner` since 2026-10-22 ~12:31 CT** (`MCCOY_ACTIVE_USERS`, override backup
    `.bak-20261022-mccoy-owner`) after the fixed-build window proof (planning → lead on Mothership, a one-line sum →
    below on BigHonker; a held lead now overflows instead of erroring, `ef0a3f7`). Everyone else: shadow. Next widen:
    `*`, once real Auto traffic shows the shadow log agreeing with the served route. ✅ Widened to `*` 2026-10-23 (`4f6b280`). ✅ The
    caller that sets `lend=True` on a live turn is built (`mccoy.py`: the waiting turn's triage) — and McCoy's
    multi-role matrix (the owner 10-21: McCoy advises Kirk on triage + decomposition, Spock on placement, Tolkien on tablet
    class, and escalation; Starfleet weighs NEEDS against COSTS per thing, never difficulty or speed alone) · ✅ **C2 CLOSED 10-17 — no labels from the owner, and no `rigby` label at
    all.** the owner: *"real life Work isn't going to have premade labels that's the point of the routing team and LLM
    assistant. There's no possible way that I will be able to foresee every type of work and classification hence the
    whole project."* McCoy reads DIFFICULTY from the request (tool / fellowship / 8gb / lead); which BOX serves a
    difficulty is placement — inside the Fellowship Tolkien picks rigby or a warm tablet from the measured rows (B2,
    live), and whether rigby is also an 8 GB-tier home is a table row decided by measurement (B10's split-A3B test),
    not a label. McCoy's option set does not change, so tev1's 0.90 line stands as measured. Where ground truth is ever
    needed it is MEASURED — the cheapest tier whose answer passes — never asked of the owner · 🔧 C6 DoD 7 written up from the decision-model
    rows + the shadow log — **draft from the measured rows (10-17): a tiny decision model improves a controller WHERE
    its confidence is calibrated on the controller's own question and code acts only above a threshold set on the
    lab's rows** — triage YES (tev1 4B ≥ 0.90: 10/10 right out of set on the owner's 36, deciding ~19–28 % in 0.18 s, 0
    under-routes; the rest go up as before); judging a worker's answer is PER TASK FAMILY (B3, bar = the lead's own error
    rate): W1 reason NO (best 5.6 % false pass vs the lead's 3.1 %), W1 ops YES (tev1 4B @ 0.95: 165 of 287 cheap answers,
    0 wrong; 8 questions, preliminary). **The
    real-traffic half is BLOCKED ON DATA:** McCoy's shadow log holds 0 human Auto turns so far (1 row: a machine skip,
    10-17 00:46) — it fills as the owner uses Auto; then the shadow rows' agreement with the route actually served is read
    against the same 0.90 line. **Blocked on data:** C3 placement
    advice (mined 10-15: nothing on current traffic; revisit when multi-user traffic exists) · C4 decomposition advice
    (delegation corpus 18 pieces — re-count) · C5 a lab-trained decision model (84 labels; needs McCoy's logged calls).
    · 🆕 **DATA SOURCE for C1/C5 (the owner 2026-10-22): his own Claude transcripts' ESCALATION / DE-ESCALATION points.** *"I tend
    to name and greet each model as I flip-flop around… hello Fable if I'm escalating up… or if I've brought down to
    opus or down to sonnet… a good starting point."* No new marker is needed: every assistant turn in the Claude Code
    JSONL carries its exact `"model":"claude-…"` id (verified 2026-10-22), so swap points are read from METADATA and
    his greetings ("hello Fable") are the corroborating signal for why. The turn(s) just before an up-swap = work the owner
    judged worth more model; a down-swap = work he judged fine on less. Caveat (his): some swaps are usage-driven, not
    difficulty-driven — a noisy revealed preference, never ground truth; measured tiers (the cheapest tier whose answer
    passes) stay the truth, this ranks and seeds. Sources: `~\.claude\projects\*\*.jsonl` (101 in Claude Home alone;
    5 with a plain greeting match) + the transcript backup `Z:\Archives\claude-transcripts\`. Fits the same mining as
    C1b (his real asks as McCoy's inputs). C1b was measured 10-19 (McCoy replay); mining his asks into TRAINING data is the judgment half → #35 (close-out § D).
    · ✅ (10-23: measured; its next step → #35, § D above) **C1b McCOY REPLAY, MEASURED 2026-10-19 ~06:50 CT (the owner: "None of it should require waiting on me" — mine the menu's prompts and his own short chats):** one systemone order on Softserve (desk `055cb2b6e997`, tev1:latest, campaign `mccoy-replay-20261019`; `submit_advisor systemone --set custom`), 129/129 answered in 63 s, no truncation (longest 12,571 chars = 9,268 tokens, same ratio as the other long prompts). **Menu (106 text tasks, truth = the cheapest tier whose best setting solves the task as often as the lead; key builder `menu_key.py`, oracle shapes out, settings chosen by `menu_policy.wilson_low`): right 2 · over-routed 96 · "tool" 8 · UNDER 0.** At the 0.90 line McCoy decides 16 of 106 — all over-routed. ⚠ The key is lopsided (fellowship 93 / 8gb 12 / lead 1: the menu is mostly tasks every tier solves), so this tests over-routing and barely tests under-routing. **Cause = the question, not the model:** the tier criteria define lead as "coding, research, writing… and anything unsure", so every coding prompt goes up by definition — this is C7 measured. **the owner's 23 own-voice chats** (cut verbatim, sha256; `pile2_items.py`): small talk → fellowship at 0.84–0.99 (right); live-status asks ("what is Mothership working on", tok/s) → lead, where a deck read (tool) is the right route; "is anybody watching Plex" → fellowship 0.76, not a Plex check; plans/research → lead 0.85–0.98. Their measured truth (answer at each tier) is owed. The 129 items are the regression set for C7's new question · **C7 first measurement 2026-10-19 ~08:25 CT — a reworded tier question (by DIFFICULTY and what the request needs; `advisor_systemone --question difficulty`, v1 byte-identical as default; deployed `fleet_deploy --workspace advisor`; desk `105901a25b3c`, one variable vs the 06:33 v1 run):** menu right 2 → 7, over 96 → 91, under 0 → 1 (`code_parse_duration` → 8gb at 0.83, below the line); live-status chats now → tool (aragorn visible, tok/s, Plex); trivia → fellowship. **But confidence fell: 1 of 106 decided at 0.90 (v1: 16, all over-routed)** — at the live line it changes almost nothing; below it everything still goes to the lead. A 4B reading wording alone cannot tell that a small model solves a formal coding spec. **Unblocks C5:** the replay's answer key is 106 tasks with MEASURED cheapest tiers (no labels from anyone) — the data C5 was blocked on · **Kirk's RETRIEVAL signal, measured 2026-10-19 ~08:40 CT** (the 07-25 design, PROJECT-BACKLOG § "TOOL SELECTION should be RETRIEVAL"; the owner confirmed the frame: retrieval is KIRK's deterministic signal, McCoy stays the advisor, the difference answers DoD Q7). Order: `advisor_embed.py` (new payload, `submit_advisor embed`; nomic-embed-text 8,192 window, `truncate:false` - 9 over-long menu prompts REFUSED -> lead, never cut), desk `7d97b8948f4e`. Reduce (`retrieval_reduce.py`, leave-one-out; unsure = split vote or best match below the p10 menu-to-menu similarity 0.740 -> lead): **k=1 right 80 / over 23 / UNDER 3 / unsure 18** (always-fellowship: right 93, UNDER 13; always-lead and McCoy@0.90: 1–2 right, 0 under). ⚠ **Mostly suite recognition:** with same-family neighbours removed, right 39 / UNDER 5 / unsure 60. **On the owner's 23 real chats: 23/23 UNSURE** (best similarity 0.38–0.61) - the menu holds nothing shaped like a real request, and retrieval knows it. McCoy@0.90 on top: no change on the menu; on the chats he is the only signal with conviction (small talk -> fellowship 0.91–0.99). **DoD Q7 so far:** a tiny model helps where meaning is plain and retrieval has no examples (greetings; status asks with the difficulty wording); it does not judge code difficulty from words; retrieval helps only where the pool holds similar MEASURED requests. **Next (owed): measured truth for real requests** - the owner's 23 answered on the 8 GB tier and the 3.8 through HAI, the 3.8 judging whether the cheaper answer held (RouteLLM's labelling method); then every Auto turn can feed the pool · **REAL-REQUEST TRUTH MEASURED 2026-10-19 ~09:50 CT** (the owner: "Sure that's fine"): his 23 own-voice chats answered through HAI's front door on the quick tier (BigHonker qwen3.5:9b) and the 3.8 (all 46 served by the tier asked, 0 errors; `chat_replay.py`, ghost `test-homelab`), then judged by the 3.8 at xhigh in BOTH orders (desk `4b4fb1417c79`, 46/46 `stop`; RouteLLM's labelling method): **the 9B answer held on 4 of 23** (eggs estimate, pong, what year, the can-you-write-code question); 19 needed the lead. The judge's reasons are substantive, not style - the 9B FABRICATED (NJ "has no official capital", 7900 XTX "32 GB RDNA2", MS-Celeb-1M licence "confirmed"), misread live state, missed the question, used the wrong user's name; ~3 calls are soft. 2 of 23 orders disagreed (position bias). **McCoy @0.90 vs this truth (both questions): right 17 / over 4 / UNDER 2** - the 2 unders are greetings sent to the Fellowship at 0.91–0.99, where the measurement favoured the 3.8 over the 9B; no Fellowship answer was measured. **Reading for C7:** on real HAI requests the cheap tier fails on CONFABULATION (live state, library, facts), not on difficulty - the routing question is "does this need live state / the library / real facts?", not "is it easy?". Scope: n = 23, one run, one judge model · **THE WINDOW WAS NOT THE CAUSE (one variable, 2026-10-19 ~11:45 CT):** HAI had run the 9B at 7,168 (the pre-0.40 fit) while the menu used its measured 39,936 - fixed everywhere (HAI READS windows from policy.json `contexts`, orchestrator `b0fe7dd`; triage_local.json + rigby via fleet_deploy; pinned by hard-bench test_ctx_stores_agree.py). Re-run of the 9B on the same 23 chats at 39,936, judged against the SAME 3.8 answers (desk `0a9c75368a42`, 46/46 stop): **4 of 23 again**, the same 3 chats held (eggs, pong, the code-tools question) + one swap (NJ capital in, "what year" out = run-to-run noise). With 5.6x the room it called tools LESS (3 of 23 chats vs 6). The 9B in HAI mostly CHOOSES to answer from memory; the open question is why (next one-variable test: the same 23 to the bare 9B, no HAI wrapper - does the wrapper hurt it, or is it the model?) · 🔧 **QUICK-TIER MODELS ON THE 23 CHATS, THINKING ON, 2026-10-19 ~14:35 CT** (the owner: *"you probably should just never use the thinking off option"* → thinking ON by default in HAI, `5e10133`; *"How's the A3B… a Qwen3 issue versus a Qwen3.x issue"*): each model through HAI's real quick tier on BigHonker at its measured window (`app.quick_eval_model`, test users only), judged vs the SAME 3.8 answers, both orders, held/comparable/lost/escalated — **9B ON@39,936 4/5/14/0 · A3B `qwen3.6:35b-a3b-ud-q3kxl-mtp` ON@65,536 6/5/11/1 · qwen3:8b ON@23,552 4/2/17/0** (9B OFF: 4/2/17 @7,168, 4/4/15 @39,936). Median wall 25 s / 77 s / 21 s. Limits clean: every call `think=True`, no compaction, no ctx downgrade, all `stop`. **Reading: the GENERATION matters (old Qwen3 8B with thinking = the 9B with thinking off); the A3B is the best quick-tier model on real chats but ~3x slower and still loses half to the 3.8; it escalated one planning chat to Claude rather than answer.** n=23, one run each — a direction, not a verdict. Every model lost the same research / plan / live-state chats = lead work, McCoy's job. Desk orders `chatjudge-q38-xhigh-a3b-think.20261019-135506`, `…-qwen3-8b-think.20261019-135507`; journal 2026-10-19 § 12:30-14:00 · ✅ (10-23: measured — a 4B reading the wording cannot judge coding difficulty; unsure → the lead; measured-truth step → #35) **C7 CODE DIFFICULTY, READ FROM THE REQUEST — the user never has to say** (the owner 10-17: *"how are we defining
    'hard code'… the user shouldn't need to know or signify manually (but can I guess)"*). Today: `escalation` = "hard
    code starts at the lead" was measured on the 8 W1 `code_*` tasks only (a BENCHMARK label); McCoy's tier rule sends
    EVERY coding request to the lead ("real work… coding… anything unsure"), while the table prices easier code
    (`coding-f1` / `coding-f2`) as acceptable on the 8 GB tier (~0.8 s per solve) — nothing bridges a live coding
    request to a difficulty, so nothing can send easy code down. The user may still steer (model pin, Think hard);
    nobody has to. ⚠ **And real requests carry NO family label** (the owner, same day: *"normal requests in real production…
    will probably not have the denotation already attached to them"*) — a benchmark prompt is a formal spec, a real one
    is "can you fix my script", so a classifier set on menu task prompts proves nothing about live turns. **Path:** the
    menu's outcomes (does the 8 GB tier solve this task one-shot, across f1 / f2 / W1 code) may SEED a difficulty
    question, but its line is set and checked only on REAL requests — McCoy's labelled set (the owner's own 36 + v1) and the
    shadow log's coding turns as they arrive — the same way the 0.90 tier line was proven out-of-set; **MEASURED 2026-10-19 ~17:00 CT, the real demand:** every user message in the owner's 36 HAI history records (149 messages, 128 unique; `code_scan.py`, whole list read from file) — 28 coding-shaped by keyword; read whole, **0 are standalone small code**: 5 are not coding (tools / capability questions, a link, a library lookup), 2 are fragments needing their conversation ("generate the code for phase 1"), and the other 21 are multi-step workspace / live-host JOBS (McCoy self-engineering, the Sméagol intake on Rigby, Project 12 Phase 5 design) = lead work by nature. McCoy's shadow log holds 2 real Auto turns so far, neither coding. **So "every coding request starts at the lead" is also what the real traffic needs today; the easy-code-down path protects a request shape that has not yet occurred.** C7 stays open as a WATCH, not a build: the shadow log tags each coding turn as it arrives, and the difficulty line is set when real easy-code turns exist to set it on; a NEW question
    (rewording the tier question voids its line); until it separates, every coding request starts at the lead. **Stakes,
    from the table:** on easy code the lead BOX is also the fastest (`coding-f2`: qwen3-coder:30b on Mothership 0.34 s
    per solve vs qwen3:8b on an 8 GB box 0.81 s), so sending all code up costs Mothership's time and energy, not the
    user's wait. **It is a STARFLEET COMMAND job, not McCoy's alone** (the owner: *"Star command is supposed to be able to use
    McCoy, Kirk, and Spock all working together"*; the roles are already in [[HAI-Agent-Surface-Design]] § "The ADVISOR"
    table): McCoy ADVISES Kirk "hard? sure?" → Kirk's deterministic check (menu families; unsure ⇒ the 3.8) → Spock
    places on the LIVE board: Mothership free ⇒ easy code goes there anyway (fastest); Mothership busy with another
    user's work ⇒ a SURE-easy request takes the 8 GB home instead of queueing (B4b's overflow, the same caveat line) →
    the result is checked where a checker exists (B3's ops selector is that row's first measured case). So C7 needs
    McCoy's difficulty line AND B4b's overflow; neither alone changes a route.
  - **Parked by the owner (closed, not hanging):** the 3.8's remaining one-shot xhigh menu cells (10-07, "pretty much just
    refining… don't get stuck on the menu"; `hold_38_xhigh_refinement.py`); new measurement questions after the 10-08
    freeze (PROJECT-BACKLOG, next model generation).
  - **Also staged:** BigHonker A3B (B10) · the A1 arm with OTHER reviewers (#37 hand-off; B5's input) — decided
    10-17: a BRANCH at finish from A3's xhigh delivery (`submit_a1.py` `--branch-from=<A3> --branch-point=finish`: only
    the review + one repair turn run, paired against A3's own finish), reviewer **`bighonker:nemotron-3-nano:4b`,
    `--review-think=on`** — the 10-08 reviewer (qwen3:8b, thinking on) shared the lead's Qwen family and gained
    nothing; A1-review's own intent is a DECORRELATED reader, and Nemotron is the strongest different-family coder
    the review path reaches (GPU boxes only; rigby would need a RigbyDelegator review). Submitted when A3 delivers,
    after A4 on Mothership's lane.
- **Gates:** orchestrator **2825** LIVE (2026-10-17 00:45 CT, `tests/gate_pin.py`; the #37 leftovers, the effort pin,
  B4a, B6, McCoy's wiring and B1's desk half `8f15d5c`/`d0ed5aa` — the mirror and live agree), coding-runner 86
  (in-container), foundry `test_foundry.py` 501, Tolkien **841** (10-17, placed orders + the card guard), rigby-a1 65,
  A1 suite **203**, hard-bench `test_menu_policy` 34 / `test_menu_fill` 91 / `test_w1_judge` 9.

## Standing decisions (how to work) — never re-ask

| date | decision (the owner's words where quoted) |
|---|---|
| **10-19 ~17:40** | **MODEL WORK IS A FACTORY ORDER — CHAT INCLUDED; ONE DOOR ONTO THE CARDS.** *"Generally speaking when models are running, I want that work to be tracked. So that means putting it in as a factory order."* Rejected: chat as a separate path (*"I kind of feel like you're just defending a second portal onto the cards… I would need a much better argument"*). Latency is fixed INSIDE the loop (*"make the HAI system and loop have as little latency and unnecessary waiting as possible"* — checkpoints, dumps, resume; priority + lossless pause), never by going around it. Quick non-model favours stay quick. Recorded drift: #37's "no blind spots without making chat an order" (read from doctrine 12e) never had his decision behind it; his 08-14 and 09-11 words said chats go through Starfleet / the orders system ([[HAI-Agent-Surface-Design]] § "What exists today"). Also: **a box with a person on it is FREE GAME when our work fits beside them with no slowdown** (*"if she's doing basically nothing and we can guarantee no disruptions or slowdowns to the user, then that's free game"*); otherwise route to another box (Softserve ≈ BigHonker for the 9B) or wait in the box's queue — the routing decides, never a hand `exclude`. And the cache-served "reload" rate is a REAL number for that case (*"our all-time high specifically for that type of reload"*): shown beside read, never in place of it (orchestrator `44af03a`). |
| **10-17 00:1x** | **NO NEW PHASE UNTIL EVERY EARLIER ONE IS 100 % CLOSED (the owner, giving the overnight mission "get 37 closed out, then continue working on 34 and try to close that"):** *"the prerequisite to letting you open a new phase is that you are 100% certain that all preceding phases are 100% closed and there's no hanging items you have marked 'for later' that can be knocked out before you move on to the next phase."* So Phase 9 opens only after Phases 7 and 8 have NO open row a session could finish — every "owed", "later", "after X" item in this register is either done or genuinely blocked on something outside the lab's reach (the owner's hands, hardware in transit), and says which. |
| 10-03 | All FOUR factories in parity (Tolkien included): *"we need to keep all my factories in synch"*. |
| 10-03 | Quality AND speed AND price; acceptable is per factory, per box AND fleet; *"lots of nuance … make sure we don't collapse"*. |
| 10-03 | A worker that grinds for hours on an item = that item fails for that model/config; look for the next rung up. |
| 10-03 | Lab agents (HAI/fleet orders) are "free"; Claude subagents only with the owner's say-so. |
| 10-04 | The crown is named at the END of #34, after A1: *"I don't want to crown a challenger yet until we've finished our stuff."* |
| **10-13 14:3x** | **✅ #36 CLOSED 2026-10-16 → [[36-Closeout]]** (A1 role number 6/10, 0.70 solves/h; the re-price finishes on the menu top-up; hand-offs to Phases 7/8/9 + S10). **#36 — rigby got a GTX 1060 6 GB (x16 CPU slot via riser, PCIe 3.0 x1); OPEN for the owner's research session tonight: rigby's ROLE.** First quick probe (llama.cpp's own fitter, `-dev Vulkan0`, single rep): MoE read 2K **87.7 tok/s** (CPU 18.7, 10-tablet split 25), write **12.3 tok/s** (CPU 5.5, GT 730 writer 4.3) — rigby is now Mothership's hybrid GPU+RAM MoE shape at small scale, untuned. the owner's options (his words: *"Rigby is still being governed by Tolkien… amenable to upgrading Rigby to being its own Fleet class box"*): **(A)** stays under Tolkien, the 1060 = its big card; **(B)** rigby CPU + 1060 promoted to a fleet-class factory, the GT 730 joins the Fellowship as a tablet-class worker taking work orders. Research input: the Flash-Next speed-trick arsenal (`Qwen3.8-Flash-Next-on-Mothership.md`, `arsenal.json`) applied to rigby. **Frame (the owner): use the WHOLE Frankenstein box — "we want every drop out of these machines"**; the tablets and the 730 are measured against what they ADD to the 1060 + CPU (e.g. holding the experts that do not fit on the card instead of DDR3-over-x1, attention/KV/context, long-prompt read split, drafting, parallel work), never against the 1060 alone. Probe 2 at 16K depth: 1060 read 2K-on-16K 59.5, write 8.43 tok/s. Setup done today: both `/dev/nvidia*` in Tolkien's containers, GPU placements pinned by name (gate 764), `fit_target` knob for llama.cpp's fitter (gate 765), two-GPU telemetry (gate 763). **14:23 local: the 1060 FELL OFF THE BUS** (`Xid 79`, then `RmInitAdapter failed` ×296) at the start of r36's first run (4K read, ub 512), ~1 min after probe 2 finished; all four r36 runs refused cleanly (`expected one Vulkan device named 'GTX 1060'` — the by-name pin did its job, nothing benched the 730 by mistake). Recovery needs a power cycle; cause under load = power (600 W PSU going in tonight) or the riser. **r36 held until after the PSU swap**; re-running before changes nothing (class 11). **15:2x RESULTS (r36 + r36b, 1060 via the fitter, MoE Q3_K_XL, `llama-bench` n=2, stable 31 min after the 600 W/6-pin fix):** batch size on the x1 link, read p4096: ub 512 **37.6**, ub 1024 **59.4**, ub 2048 **85.5** tok/s (keep climbing — ub 4096 untested). Depth curve (ub 2048), read 2K / write 64: d0 **83.6 / 10.27**, d8K **68.0 / 8.29**, d16K **58.3 / 6.73**, d32K **45.1 / 4.86** tok/s. Rigby CPU alone: read 18.7, write ~5.5 at d0. Gap found: `bench` runs show no live rate (llama-bench prints per test) — the lab's live Speed needs streamed answers, so the 1060 now also runs as an `llm` order (`gpu`/`fit_target`/`build`, speed_rewrite_probe payload) for the deck. **16:08 r37 (streamed `llm`, same 1060 + fitter, server timings, n=3, all graded ok, deck showed live tok/s):** write **12.5 tok/s at ~2K, 11.75 at ~12K**. ⚠ DISAGREES with `llama-bench` on the same placement (write 10.27 at d0, 8.29 at d8K, 6.73 at d16K): the server barely slows with depth, llama-bench falls ~35 % by 16K. Unseparated differences: instrument, fit context (bench ~35K vs server 32768), the rewrite's repetitive code. One variable at a time before either curve prices a rung — tonight's session. **17:26 r38 (llama-bench, fitter, ub 512 vs 2048, both fitted for ~8K context, n=2):** write d0 **12.97 / 12.08**, d8K **12.24 / 11.71**; read 2K d0 38.2 / 87.2. My prediction (ub explains the whole 10.3→12.5 gap) FAILED in size: ub costs ~7 % of write; the rest of the r36b curve's slowness is the FIT CONTEXT — r36b fitted one placement for ~35K, which both lowers write (12.1 → 10.3 at ub 2048) and steepens the depth slope (−19 % by 8K vs −3…−6 % here). Working reading (4 numbers, a hypothesis): the fitter's reservation scales with ub × context, fewer experts land on the card, and a large context may push KV/attention to the CPU. **Rule until disproved: set the fitter's context to the order's real working context; r36b's depth curve prices a 32K-sized placement, not the card's best.** **18:31 r39 (streamed `llm`, r37's exact spec + `parallel: 1` = `-np 1`, n=3, all graded ok):** write **12.56 / 12.58 / 12.54 at ~2K, 11.82 / 11.76 / 11.78 at ~12K** vs r37 12.48 / 12.50 / 12.46 and 11.75 / 11.75 / 11.76 → +0.1 tok/s (<1 %, inside run-to-run spread): prediction FAILED, slot memory is NOT the binding term for this model (the lab's `-np 1` finding was the 27B with a larger recurrent state). Knobs tried so far on the 1060 (ub, fit context, slots) move write by ≤ ~8 %; the plateau ~12–13 tok/s is set by the experts still on the CPU, so the next step up is MORE BYTES ON GPU-CLASS MEMORY (the 730's 2 GB, a lower quant, a bigger card), not another flag. `parallel` stays as an opt-in knob. **19:1x METHOD CORRECTION (Opus, after the owner: *"I think sonnet was mistaken with its analysis and testing methodology"*):** (1) every "plateau / CPU-bound" claim above was asserted without reading the telemetry Tolkien already records; (2) the fitter re-places on every ub / context / slot change, so none of r38–r39 was one variable — the placement moved with each; (3) three knobs of the lab's 39-lever `arsenal.json` were tried and called exhausted. **Measured from r39's telemetry (10-s buckets, `scratchpad\r39_timeline.txt`):** writing = ~130 s stretches with all 4 llama threads busy (cpu_total ~42 % of 8, busiest core 85 %, 1060 ~60 W) → writing IS CPU-bound; **READING = ~24 of the 39 min**, 12K prompts at ~27 tok/s with ONE thread busy and the 1060 at 40–100 W — the server's default ub 512 over the x1 riser (llama-bench: 38 at ub 512 vs 87 at 2048). The bigger lever for A1-shaped work is the server's ub, not another write knob. `ubatch` added to `llm` orders (gate 775); r40 = r39 + ub 2048 queued. **19:04 r40 (r39 + `ubatch: 2048`, n=3, all ok):** read 2K **82.8** (was 27), 12K **74.3** (was 27.6) = 2.7–3×; write **12.43 / 11.64** (−1 %, under the −5…−8 % predicted); wall **25 min vs 39**. ⚠ Prompt-cache reuse got coarser: same prompts, r39 reused 1,929 cached tokens, r40 only 397 (cause unmeasured; matters for A1, which lives on cache reuse). **Default for 1060 `llm` orders: `ubatch: 2048, parallel: 1`.** Next (placement build: both cards + `kepler_flags` + llama-fit-params placement recorded in the cohort): r41 margin 256, r43 1060+730 experts, r42 Kepler-flags control. **19:36 r41 (r40 + 1060 margin 256):** the card held **+765 MiB** (telemetry 5,895 vs 5,130 MiB — verified, not assumed) and write moved **12.43 → 12.54 / 11.64 → 11.71 (<1 %)**; a CPU-expert-bytes limit predicts ~+5 %. **So expert bytes on the card do NOT set the 1060's write speed.** Also retracted: ub 2048 did not push experts off the card (r39 5,087 vs r40 5,130 MiB; the fitter's `-fitp` estimate of a 2,372 MiB compute buffer is not the allocation). Caveat on "4 threads busy = CPU-bound": this build uses OpenMP, whose threads spin while waiting. Discriminating order **r44** (bench, write tg64 d0 ub 512): fitter at 2 / 4 / 8 threads + whole layers (`gpu_layers 8`, 2 hand-offs per token instead of ~80) — CPU compute predicts 2 threads ≈ half and whole layers ≈ 7; hand-off cost predicts 2 threads ≈ flat and whole layers > 13. `llama-fit-params -fitp on` prints the estimate, not the fitted arguments — fix the report before relying on it. **20:01 r42 (r40 + Kepler flags on the 1060 alone):** read/write within 1 % of r40 → the 730's process-wide flags cost the 1060 ~nothing; r43 compares cleanly. **20:03 r44 (bench, write tg64 d0 ub 512, n=2):** fitter t4 **13.14**, t2 **9.85**, t8 **13.57**, whole layers (`-ngl 8`) **6.06 ± 1.1**. Neither pure hypothesis held: whole layers ≈ CPU-alone (the 1060's job is attention + shared parts of EVERY layer; the fitter's sliced placement is right), and 2 threads cost −25 %, not −50 % or 0. Fit T = G + C/threads: **C/4 ≈ 25 ms, G ≈ 51 ms of a 76 ms token do not scale with threads** — GPU work plus ~80 card↔CPU hand-offs per token over the x1 riser (1060 at ~half power in decode → leans hand-off latency; UNMEASURED). Explains r41 (moving experts only shaves the 25 ms part). 8 threads +3 % (the `llm` kind is fixed at 4). r43 prediction revised before it lands: the 730 adds hand-offs → flat or slower. **Next instrument (tonight): split G with a per-op GPU trace (`GGML_VK_PERF_LOGGER`) before choosing a lever** — candidates: fewer sync points per split / async copies (arsenal `async_sched_copies`, `shexp_overlap`, open), threads 8, the link. **20:22 r43 STOPPED at 3 of 7 (1060 + 730 via the fitter, 1060 margin 1024, 730 margin 256):** write **4.0** (2K) / **3.6** (12K, stopped mid-answer) and read **32** vs r42 (same Kepler flags, 1060 alone) 12.35 / 82 → **−68 % write, −61 % read**. The fitter's estimate put ~3.6 GB on the 730 (`Vulkan1 3580 140 4803`), i.e. a big share of every token's work on the slow card behind the chipset slot. **CLOSED: the GT 730 does not hold experts for the 1060.** Its whole-box role is separate work (Fellowship / its own orders), tonight's question. Stopped by slip (known-bad arm); 3 rows shipped `stopped:true`. **20:5x SYNTHESIS written** ([[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]]): reading is bound by the x1 riser (op offload streams the CPU-held experts per micro-batch); writing = ~51 ms fixed (GPU dispatch / Vulkan issue / hand-offs — the Mothership shape, unmeasured split) + ~25 ms CPU; levers ranked (ub 4096, x16 riser, `--load-mode none` + repack, threads 8, measure G, CUDA under driver 470, newer llama.cpp, check cost on the 1060); the 730 and the tablets are replicas (independent work), not slices; rigby's role decided by re-pricing its menu cells on the 1060. **the owner 21:0x: *"it's probably a bigger deal for us to get write speeds up"*** — agreed by the numbers (T1 traffic at the 1060's speeds: writing ~80 % of rigby's A1 model time, was 71 %). **20:50 r45 CHECK COST on the 1060 (bench, fitter, n=3):** a pass of k tokens costs **1.01× / 1.76× / 2.15× / 2.87× / 5.09×** one decode step at k = 1 / 2 / 3 / 4 / 8 (CPU round 1: 4.6× at k=2). Drafting 2 and checking 3 → **+13 % at 80 % acceptance, +26 % at 90 %** [I] — a real but modest lever (n-gram on code rewrites first; it LOST −29…−34 % on the CPU path). Correction: the ~51 ms that does not scale with threads is mostly PER TOKEN (each extra batched token adds ~30–58 ms), not a fixed per-pass toll. Next: Tolkien knobs `load_mode` / `threads` / `draft_max`, then one-variable orders. **Knobs BUILT (gate 784: `threads`, `load_mode`, `draft_max` → each spec type's own flag, `op_offload_min_batch` → `GGML_OP_OFFLOAD_MIN_BATCH`).** **21:10 r46 (r40 + `--load-mode none`, verified in the server argv) — NO EFFECT on write:** 12.47 / 11.64 / 12.39 vs r40 12.43 / 11.64 / 12.37; stopped at 4/7 as known-flat. Inferred cause: llama.cpp's CPU repack kernels need AVX2 (the i7-3770 has AVX only), so loading instead of mapping buys nothing here; the community's +35 % (`--no-mmap`) was on an AVX2 CPU. Running: r47 threads 8, then r48 n-gram draft 8. **21:36 r47 (r40 + 8 threads, n=3, all ok) — NO EFFECT:** write 12.47 / 12.46 / 12.40 and 11.64 / 11.67 / 11.64 vs r40 12.43 / 11.64; read unchanged (83 / 74). Verified it ran: CPU median **84.5 %** (vs 12–42 % at 4 threads) — OpenMP threads spin while waiting, so busy cores ≠ compute (confirmed). **Every CPU-side and placement lever is now flat on the served path** (experts on the card r41, slots r39, load mode r46, threads r47): the write limit is the per-token GPU / dispatch / hand-off cost — next instruments: the scheduler profiler and a CUDA build. **22:02 r48 (r40 + `ngram-simple`, ≤8 drafted, verified `--spec-ngram-simple-size-m 8` in the argv):** write 12.36 / 12.34 / 12.25 and 11.58 / 11.65 / 11.58 (−0.5 %), all ok, output token-identical to r39/r40 (fixed seeds). **`draft_n` empty on all 7 rows = speculation NEVER DRAFTED** (llama-server fills the draft timings only when a draft was made; Tolkien reads them, `tolkien_llm.complete`) — the probe's rewrites do not repeat 12-token spans of the prompt. Verdict limited to n-gram on rewrite-style writing; drafting itself is not closed on the 1060 (r45 check cost). **Plan offered to the owner 21:5x:** (1) the scheduler profiler on a rebuilt `next` to split the ~50 ms; (2) a newer llama.cpp beside it (DeltaNet fusion); (3) CUDA for the 1060 under driver 470 (CUDA 11.4, likely in a container) — held for his call. **23:4x THE PROFILE (r50; lab scheduler profiler `GGML_SCHED_PROFILE=100`, ported to 23b0202 as `lab-engine\patches\sched_profile_23b0202.py`; next-build swapped with archive `/home/owner/llama-next-vulkan-archive-20261014-041539`; reproduction r49 within 0.5 % of r40, stopped early):** per written token, 38 CPU + 38 Vulkan splits; at ~2K **wall 81 ms = CPU expert compute 36.9 (46 %) · CPU waiting on the 1060 + copies 29.3 (36 %) · issuing Vulkan work 7.8 (10 %) · outside the scheduler 4.9 · GPU-side copies 1.9**; at ~12K wall 84–88 = the GPU wait grows to 34.5, the rest unchanged. **Corrects r44's inference** (the CPU expert part is the biggest piece; 8 threads did nothing because 4 physical cores are saturated). Two halves: CPU experts ~37 ms (levers: fewer CPU-side expert bytes — an asymmetric quant, the owner's call; RAM is 4× DDR3-1600 at rated speed, pairs look symmetric) and GPU wait + issue ~37–42 ms (lever: CUDA). **the owner 00:0x: CUDA approved** (*"if CUDA is our best bet we probably should have looked into that in the beginning"*) — building in `nvidia/cuda:11.4.3-devel-ubuntu20.04` (sm_61), Tolkien `build: "cuda"` wired (gate 790, not yet deployed). **00:2x ARSENAL REVIEW (the owner: review the arsenal + rigby's own data before the CUDA orders):** `lab-engine\research\split-inference\rigby_arsenal.json` (36 levers, same schema as Mothership's, priced against the r50 terms) built from the WHOLE outbox — a script over 2,160 results files found **476 orders / 19,701 rigby rows (3,283 on the A3B)**. **RETRACTION from it:** r48's "the probe never repeats 12-token spans" is WRONG — on 10-06 the same probe with n-gram on the CPU drafted **3,504, accepted 1,113 of 1,609 written (69 % of written tokens from drafts)** and lost only because a CPU check cost 4.6× a step; r48 made NO drafts (cause unknown: my size-m 8 mapping or the next build). n-gram on the 1060 is OPEN, and with checks at 2.15× (k=3) it is the most promising write lever after CUDA. Also from the record: rigby's A3B menu standing (thinking off f1 240/240 at 20.7 s/solve, f2 574/576 at 11.9 s, tool 0.979; dominated by the 7B on tool) was priced at CPU speeds — re-price on the 1060. **09:2x r51 — CUDA WINS (CUDA 11.4 build in `nvidia/cuda:11.4.3-devel-ubuntu20.04`, sm_61, `build: "cuda"`, CUDA pinned to the 1060 by UUID from `/proc/driver/nvidia/gpus/*/information`; r40 config + profiler, 1 replicate, all ok):** write **14.23 at ~2K (+14 %), 13.78 at ~12K (+18 %)**, read **80.6 at 2K (level), 109.3 at 12K (+47 %)** vs Vulkan. Profile per token at 2K: wall 70 ms = CPU experts 36.1 (unchanged) · **CPU waiting on the GPU 15.4 (was 29.3)** · CUDA input copies 4.2 · issuing 8.0 (unchanged — graphs may not be engaging; control r53) · outside 6.1; at 12K the wait is 18.1 (Vulkan 34.5). **CPU experts are now 52 % of a written token.** Also found: r48's n-gram drew NOTHING because my `draft_max` set the draft length (size-m 8) under the lookup (size-n 12) and llama.cpp refuses such drafts (`common/ngram-map.cpp`: copy_max < n_draft_min) — Tolkien now caps the lookup with the length (gate 791, test over every N). Next (1 replicate each, evolutionary): r52a n-gram short (8/8), r52b n-gram default + op-offload threshold 512 (no streaming of its 49-token checks), r53 graphs-off control last/droppable. **09:2x r52a (CUDA + n-gram, lookup 8 / draft 8 — drafting verified: acceptance 0.61, 1,070 of 1,760, mean run 5.9): write 9.42 vs 14.23 (−34 %), stopped; r52b cancelled before it ran.** SPECULATION IS CLOSED ON THIS PLACEMENT: a k-token check sends each token to its own CPU-held experts (52 % of a token), so the check costs ~k× the CPU half and only the GPU half batches — the community's "MTP with experts in RAM: −27 %"; MTP and a draft model close with it (no MTP download for rigby). **09:4x r53 (r51 + `GGML_CUDA_DISABLE_GRAPHS=1`): identical to r51** (wall 70 ms, issue 8.0, 14.2 tok/s) → CUDA graphs were NEVER engaging; ~8 ms/token of issue cost is still recoverable once the reason is found (llama.cpp logs "disabling CUDA graphs due to …" at a higher log level — next instrument). Stopped at its 2K verdict. Whole-box role analysis written: [[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]] § 5. **10:0x the owner:** CUDA graphs approved (source read: `ggml_cuda_graph_set_enabled` disables graphs below `GGML_CUDA_CC_VOLTA` — the 1060 is Pascal 6.1; MUL_MAT_ID does not block decode (MMVQ path); env-gated lab patch `lab-engine\patches\cuda_graphs_pre_volta_patch.py`, `LAB_CUDA_GRAPHS_PRE_VOLTA`, building). **Lower-bit CPU-side experts: SKIP** — the owner: *"Iirc any lower than what we have and there's a steep drop off"*, matching the lab's Mothership record (Unsloth Flash-Next KLD: UD-Q3_K_XL 0.107 / 88.3 % top-1 → UD-Q2_K_XL 0.225 / 82.7 %; `down` experts the most sensitive) for ~+8 % [I]. **New lever, the owner: "worth looking into":** pin the HOT experts (not whole layers) on the 1060 — static, no runtime uploads over x1; first step = per-expert usage counts from `llama-imatrix` over a sample of rigby's own A1 traffic (how skewed is routing?); the placement itself needs an engine patch (experts of a layer are one tensor). **The 730 holding experts stays closed** (r43 −68 %; ~2.5 ms per hand-off on its slot, Kepler has no DP4A). **the owner: new RAM arrives Friday 2026-10-16** (layout C: Mothership + BigHonker; rigby is not in the swap) — Mothership's RAM-dependent MoE measurements wait for it. **10:3x RIGBY MENU PRICING PARKED by the owner** (*"I would rather come back around to the pricing rows later once we've actually discussed it because Rigby has a lot of complexity now and the fellowship as well"*): the four re-pricing orders (r56 f2 / r57 tool / r58 f1 at op-offload 512, r59 f2 control) were written and withdrawn unrun (`hard-bench\orders\llm-36-r5[6-9]-*`, named outside the `hardbench-rigby-*` ledger so they never mix with CPU cells). ⚠ **Rigby's energy constant is stale for the 1060 config:** the menu prices rigby at a flat 54 W marginal (Govee plug 2026-09-29/30, CPU path); one clean APC pairing (`energy_history.py`) of r51 on CUDA read **+78.3 W above idle (176.7 → 255.0 W), n=1 — PRELIMINARY**; the 600 W PSU feeding the 1060 is on the same APC. Pricing any 1060 row with 54 W would undercount. **09:4x–10:1x CUDA GRAPHS (r54 / r55, 1 replicate each):** graphs ON via the lab patch (`LAB_CUDA_GRAPHS_PRE_VOLTA=1`): issue 8.0 → 1.1 ms/token, but the wait on the card rose 15.4 → 21.1 — the issue cost had been overlapping the card's own work, so the GPU half's floor is **~25 ms of real 1060 work per token** (attention + DeltaNet + shared parts over 40 layers). Write **14.51 / 14.02** vs r51 14.23 / 13.78; control r55 (rebuilt binary, flag off) **14.28 / 13.83** = r51 → **graphs +1.5 %, kept in the standard 1060 order.** **Standard 1060 `llm` order now:** `gpu gtx1060, fit_target 1024, build cuda, ubatch 2048, parallel 1, engine_env {LAB_CUDA_GRAPHS_PRE_VOLTA: 1}` (fields documented in [[Fellowship-Order-Taker-Design]] § 13). Per written token: CPU experts ~36 ms · the 1060 ~25 · other ~7. **10:42 r60 ROUTING COUNT (new bench run type `imatrix`, gate 793: llama-imatrix over 120 × 512 tokens of rigby's own A1 traffic — corpus `payloads/imatrix/a1-rigby-moe-20261014.txt` built from the Inspect logs' model events, de-duplicated; product `outbox/bench-36-r60-…route.imatrix.gguf`, analysis `lab-engine\research\split-inference\routing_skew.py`): routing is SKEWED** — the hottest 16 / 32 / **46** / 64 / 128 of 256 experts per layer carry median **27 / 43 / 53 / 63 / 86 %** of routed tokens (uniform 6 / 12.5 / 18 / 25 / 50 %); 46 per layer ≈ the 1060's ~2.5 GB of expert room; layer 0 is the flattest (30 % at 46), deep layers up to 66 %; instrument check: 491,520 routed per layer = 61,440 tokens × 8, no expert unused. Today's whole-layer placement covers ~12 % of routing; **pinning each layer's 46 hottest experts on the card would cover ~53 % → ~70 → ~53 ms/token, ~19 tok/s (+30 %) [I]**, lossless. The expert cache cannot do it (every selected expert must be in a slot; misses always upload over x1 — ~170 MB/token at a 60 % hit rate). **Needs an engine patch** (hot/cold split of each layer's expert tensors + `build_moe_ffn` routing to two MUL_MAT_IDs, summed) + a repack tool; KTransformers is out (needs AVX2/AMX). **12:1x r61 — upstream's hot-expert store LOSES on rigby as shipped.** Library-first search found llama.cpp **PR #26563** (closed, unmerged: hot experts copied to GPU slots and run there, cold ones computed on the CPU by a new cold-only op; author measured 17 → 36 tok/s on this model on an 8 GB card); its successor #30059 and the merged #29887 upload misses (loses on x1). the owner OK'd the source download. Built CUDA 11.4 sm_61 at `/home/owner/llama.cpp-hotstore` (commit 3897637, base 08-05), Tolkien `build: "hotstore"` + order field `expert_hot` {slots, dwell, hyst} (gate 795). Standard 1060 placement, write 2K / 12K: **control r61c (same build, store off) 14.19 / 13.70** (= r51: the older base is not the cause); **r61a store auto 13.54 / 13.39 (−5 / −2 %)**; **r61b frozen after the first re-sync 12.67 / 12.46 (−11 / −9 %)**; read −8–10 %. Autofit gave only **27 slots/layer** (1.5 GB; ub 2048's compute buffer + KV + the 1 GB margin took the rest) ≈ 35–40 % of routing by r60. **Why it loses [I, from the code]:** the PR hooks per MATRIX (`build_lora_mm_id`) — gate, up and down each become a GPU hot op + a CPU cold op + an add, ~6 extra GPU↔CPU hand-offs per layer (~240/token) over the x1 link, plus a `synchronize()` on every decoded token to update its heatmap. **Next:** split at the whole expert BLOCK (hot experts' full FFN on the 1060, cold experts' full FFN on the CPU, one sum per layer = the stock crossing count), hot set seeded statically from r60's counts (no per-token sync), reusing the PR's slot store / cold-only op / LUT. Its quality check must run at ub 1 (the tier engages only at n_tokens = 1). Orders `hard-bench\orders\llm-36-r61{a,b,c}-*`. **14:1x HOT-EXPERT PINNING CLOSED ON RIGBY (r62–r64).** The lab block split was built (`lab-engine\patches\hotstore_block_patch.py`, env `LAB_HOTSTORE_BLOCK=1` + `LAB_EXPERT_HOT_RANKING=<file>` — static fill from a ranking, no heatmap, no per-token sync; rankings by `expert_ranking.py`; Tolkien engine_env takes the ranking file from the payloads share only, gate 796). **r62a** block + r60's A1 ranking, 27 slots: write **12.42 / 12.08** (below r61a); **r62b** hit-rate log: **9.4 %** = chance (27/256); **r62c** 48 slots (ub 512, margin 256): 12.99 / 12.71, read 32 / 31. **r63** (imatrix over the probe's own 12K prompt, `routing_overlap.py`, positive-controlled on r60 vs itself): the probe is MORE skewed (its top 27 / 48 carry 50 / 67 %) but r60's A1 set catches only **22 / 37 %** of it (top-set overlap 26 / 38 %) → **routing is task-specific**. **r64 CEILING** (block split filled from the probe's OWN ranking, 48 slots): write **14.71 / 14.32** = **+4 % over the store-off control r61c, ~+1.5 % over the standard order r54 (14.51 / 14.02)**, at read 32 / 31 vs 78 / 106 (ub 512); hit rate **41.9 %** (22–63; mapping verified — the ranking alone moved write +13 % vs r62c). Moving ~42 % of the CPU's expert work to the 1060 nets ~2.5 ms of the ~15 ms it frees: the dual path's own GPU work and hand-offs eat the rest. **Verdict: best case ≈ +2 % write for −60–70 % read, and real mixed work would hit below the tuned-set ceiling — closed.** Binding terms: the x1 link, 6 GB (~48 slots/layer), task-specific routing. `build: "hotstore"`, `expert_hot`, the patch and both rankings stay in place for a bigger card or a wider link. Arsenal `static_hot_experts` → closed. Standard 1060 order unchanged (r54). Orders `hard-bench\orders\llm-36-r6[2-4]*`, `bench-36-r63-*`. **14:5x CO-LOCATION (730 job beside the 1060 A3B) — NOT measured; my two orders were void before they ran, both knowably:** r65b (r32's 10-tablet + 730-writer handoff, resubmitted unchanged) was refused in 30 s by the stage-memory preflight (aragorn 1,998 / gandalf 1,952 MiB available < 1,718 + 300 needed; r32 fit on 10-13), and r65a (the standard A3B order) WAITed because **Tolkien's rigby claim is whole-box** (`RIGBY_LOCK`: any order touching rigby holds it, either lane) — then stopped by slip as a repeat of r54/r55/r61c. the owner: *"avoid doing stuff that would be already known to fail… We have MONTHS of data"* ([[Fellowship-Order-Taker-Design]] § "Before submitting a rigby / Fellowship order"). Standing argument from the data: rigby's A3B write is CPU-bound (CPU experts 52 % of a token, 4 physical cores saturated, r47/r51), so a co-tenant that uses rigby's CPU (any big model on the 730) slows both; only a small model wholly on the 730's 2 GB could co-run, and that needs a factory opt-in past the whole-box claim — **not built (the owner: no).** **17:5x THE 1060 FELL OFF THE BUS AGAIN — AT IDLE** (`Xid 79` 2026-10-14 20:57:47 UTC = 15:57 CDT, then `RmInitAdapter failed` ×2,900+; still enumerated on PCI 01:00.0; no AER/PCIe errors logged before it). Telemetry (node `gtx10606gb`) shows it idle right up to the drop: ~25 W, 30 °C, 400–550 MHz, 0 MB used — no job ran after r65a stopped at 14:58 CDT. **CORRECTION (the owner, 10-14 ~18:0x): the 600 W PSU fed the card's 6-pin from installation — it was never a "fix"** (I misread his 10-13 message; the "600 W/6-pin fix" wording in the 10-13 row above is that error). Both drops happened on the same power setup; what recovered the first was the REBOOT. This one came cold ~27 h after that reboot → leading suspect the **riser / x1 link** [I]. Recovery = reboot via `run_remote.py` (the owner: no power cycle needed); runbook + `RUNBOOK-DEFECT-LOG` corrected. Rebooted 23:02 UTC (`uptime -s` verified), CUDA sees the 1060, orders resubmitted unchanged 18:03 CDT. **18:1x r66 SMALL-READ THRESHOLD (r54 + `op_offload_min_batch: 256`):** the 47-token read **6.52 s → 3.07 s (7.2 → 15.3 tok/s, 2.1×)**; 2.4K / 14K reads 80.5 / 108.8 and write 14.55 / 14.01 = r54 (all within 0.6 %) → free for A1's small turns (median new read ~92 tokens); predicted ~25 tok/s, got 15.3 (direction right, half the size). **Adopt into the standard 1060 order** (after the MTP pair). **18:2x r67 MTP (Unsloth MTP file, `--spec-type draft-mtp --spec-draft-n-max 2`; in-model head — the MTP context is `llama_init_from_model(model_tgt)`, so `-ngld` does not apply):** r67a write **16.74 / 16.71** vs its own no-speculation control r67c **14.03 / 13.50 (+19 / +24 %)**, read −8–10 % (71.8 / 98.8 vs 80.0 / 107.4); acceptance **99.6 %** (drafted / written = 0.67 exactly: every step 2 drafts both accepted + 1) — REAL but the probe's BEST CASE (it re-emits function bodies verbatim). The MTP file's re-quantized experts write ~3.5 % slower than ours (r67c vs r54). A 3-token step costs **2.51×** a plain one → draft-2 MTP breaks even at **~82 % acceptance** [I]; Mothership saw ~72 % on fresh code, ~45 % on prose → MTP could LOSE on non-copy writing. **18:3x r68 MTP ON FRESH CODE (f1 suite `map_swarm_code_hard.py`, its 17 `impl` tasks, temp 0.6 = A1 on rigby; both arms 18/18 graded ok):** acceptance **94.1 %** (1,382 / 1,468; per task 78–100 %), write **16.88 vs 13.92 tok/s = +21 %** (token-weighted; per-task ranges 15.2–17.7 vs 13.3–14.4 do not overlap). **MTP WINS on rigby: +19–24 % write on copy-like and fresh code**, −8–10 % read; Qwen3.6's own MTP head accepts far above the ~82 % break-even (Mothership's 3.8-27B head: ~72 %). Net vs today's standard order ≈ +17 % (~16.9 tok/s, level with the best public 1060 rig's 17). **Before adopting:** KLD of the MTP file vs ours (its experts are re-quantized, layer 1 lower precision); then the new standard 1060 order = MTP file + `draft-mtp` draft 2 + `op_offload_min_batch 256` + graphs patch. Closure of `mtp` / speculation in the arsenal was WRONG — it generalised r52a (n-gram, 8-token drafts, 61 %) to MTP. Our plain file = Unsloth's published release byte-for-byte (sha256 `a832b968…` = HF oid); header diff (`gguf_type_diff.py`): only 5 tensors changed TYPE (layer 1 experts down to IQ4_XS/IQ3_XXS, layer 39 gate/up up to IQ4_XS), the other expert tensors re-quantized at the same type; Unsloth publishes NO quality numbers for the MTP build (model card + docs checked). **19:4x r69 KLD (r26's instrument, 16 × 512 tokens of lab docs, CPU): MTP file vs ours mean KLD 0.0159, p99 0.086, same top token 92.6 %, PPL 26.70 vs 26.83** vs our own float-order floor (r26) 0.0111 / 94.1 % / 26.73 → **equivalent** (a hair above noise, below even UD-Q4-vs-BF16's ~0.013 scale; PPL not worse); base PPL reproduced r26 to the digit. **ADOPTED — new standard 1060 order:** MTP file + `spec_type draft-mtp, draft_max 2` + `op_offload_min_batch 256` + r54's rest (`build cuda, fit_target 1024, ubatch 2048, parallel 1, LAB_CUDA_GRAPHS_PRE_VOLTA`) → ~16.9 tok/s write on code (was 14.5); written into [[Fellowship-Order-Taker-Design]] § placement fields. The 730/tablets cannot host the MTP head (it reads the target's hidden state every token; r43: ~2.5 ms per 730 hand-off); a separate Q8 head file (tomngdev, 1.99 GB) is heavier than the 424 MB grafted head — not needed. **20:0x owner PICKED A + C** from the review ([[36-Split-Inference-Helpers-Read-and-Draft-Design]] § "Where #36 stands"): (A) rigby's real A1 work on the new standard order, (C) the tablets + the 730 as independent workers beside rigby. **C1 MEASURED — the Fellowship and rigby STACK (r71, no factory change: a `warm` order takes the fleet lane and never claims rigby):** the warm 0.8B chat tier (`submit_chat1_warm.py` fields, 15 replicates, all 10 tablets) ran fully overlapped with the standard A3B order — rigby's A3B **16.42 / 16.68** vs 16.74 / 16.71 alone (−0–2 %, noise), reads identical; the tablets **gen median 6.61** (IQR 4.87–6.93) vs the SAME-EVENING alone control **6.96** (6.45–7.35) → **−5 % median, a heavier low tail**, first token and correctness unchanged (10-07's 7.51 differed by day / battery state, −7 % on its own — why the same-day control was needed). C2 (a job on the 730 beside the A3B) needs a factory change past the whole-box claim; design after A. **A started 20:10 CDT:** `a1v22-rigby-moe-hai-off-spot-1060-mtp-ctx65536.20261014` (inspect_lab\orders) — the R2 spot set (10 tasks) with only the placement changed vs the 10-09 GT 730 runs; no Tolkien deploys while it runs. **23:4x RIGBY'S POWER ON THE 1060, from history (the owner: *"we should be able to do the math… I saw roughly 230w on the APC screen"*):** `hard-bench\energy_history.py 2026-10-14T08:00:00-05:00` (HA `sensor.softserve_load` × Tolkien windows, each ≥ 3-min window vs the idle 5 min before it; 29 windows → 8 with a clean idle lead-in). APC total while working **225–268 W** vs 157–183 W idle. Extra power by work type: **A3B writing on the 1060 (CUDA `llm`: r51 +78, r54 +111, r61a +90, r62a +81) → median ~85 W, n=4**; reading-heavy imatrix (experts streamed over x1) +42 / +40; **CPU-only A3B (r69 KLD) +58 W** (matches the menu's 54 W plug figure for the CPU path); A3B + 10 tablets serving (r71) +86. ⇒ **The menu's flat 54 W is right for CPU-path rows and ~1.6× low for 1060 rows**; per solve the 1060 still costs far less (CPU-era coding row 19.5 s × 54 W ≈ 1,050 J vs ~300–550 J at 3–5× the speed × ~85 W [I]). The APC also feeds the 1060's 600 W PSU and any tablet charging through rigby — the wall's real cost, the right number for pricing. Feeds the re-pricing of rigby's menu rows (live `policy.json`, generated 10-11, still CPU-era: A3B coding-f1 19.5 s/solve, 54 W). **Sequencing (the owner 23:5x): *"I don't want to restart the pricing until we're fully done with the speed up evolution testing"*** — re-price ONCE on the final config; **the GT 730's role is decided by that pricing, not by hand (the owner 10-15: "we have the project 34 stuff to help us to know whats defaults"):** the re-pricing adds `rigby-730` as a warm worker row (r76: 0.8B at 10.1 tok/s) and the A3B's co-location caveat (−6 % with the 730 serving, r76), and Spock's table (`policy.json`, [[34-Phase-7-Placement-Policy-Design]]) places warm work on it or not. Tolkien carries the capability (`warm_gt730`, opt-in per order), never a default; then also fold the 54 W constant (copied in `menu_policy.py`, `a1\reduce_a1.py`, `reduce_chat1.py`, `s10b_select.py`, `cascade_select.py`) into one per-placement table (CPU 54 · 1060 decode ~85 W); and once rigby's rows name the MTP file, MOVE the plain `Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf` (Unsloth's original, sha256 `a832b968…`, the r69 KLD reference) from `/home/owner/models` to the HDD `/mnt/fellowship` as an archive — two 17 GB A3B files cannot share rigby's 24 GB page cache, so a stray load of the old one costs a cold re-read each way. **10-14/15 NIGHT — A stopped, the speed probes, the runaway, the engine update.** A (r70) stopped by slip after 5 tasks (the owner: get answers fast; the full set re-runs once for pricing): control 60 s, wordy 210 s 11/12, bowling 767 s 13/15, react + paasio both hit the 65,536 context cap through ONE no-tool deliberation turn each (31,948 / 25,447 tokens; totals 54K / 50K). **Runaway diagnosis (all read-only, one variable each):** chat templates identical (`lab-engine\research\split-inference\gguf_template_diff.py`); no tool-call markup and ~no repetition inside the runaway turns (`runaway_markup.py`); the harness change cee5ef6 → 8fff18e only adds errand tools offered with an errand_fn; `--reasoning off` = `enable_thinking=false` in the template; the server's speculative verify samples every position with the target sampler (distribution-preserving). **MTP CLEARED (a1v22-…-1060-nomtp-react, MTP off):** react ran away again (5,174 s, 55,643 tokens, cap, 2/7), then a second attempt finished normally (803 s, 8,423 tokens, 5/7 = the 10-09 best) → the runaway is STOCHASTIC model behaviour on hard tasks with thinking off (10-09 also had a react attempt at 6,731 s / 14.7K tokens / 0/7 when a deploy stopped it). **A1 RUNAWAY-TURN GUARD (the owner 10-15: "Yes we want the runaway guard"; built 10-15):** measured over every rigby A1 Inspect log (150 logs, 1,749 model calls; `lab-engine\research\split-inference\a1_turn_lengths.py`): calls that acted p50 63 · p99 3,927 · p99.9 10,875 · max **14,888** tokens (a real sgf-parsing action); the runaways **25,447 / 31,948 / 34,789**, all no-tool, all cut by the context; nothing in between. Cap = order field `turn_cap` **20,480** (inside that gap: +37 % over the longest acting turn, under every runaway — a healthy turn cut would sandbag, so the cap sits high). Sent per call as max_tokens (rigby: `tolkien_llm.chat` num_predict → max_tokens; GPU path: ollama num_predict). A reply cut there with no tool call is a **runaway**: discarded, the model told to act (`RUNAWAY_TEXT`); the first cut nudges, a second ends the exercise as `ended: "runaway"`, graded on the workspace. Validity: a guard cut is the model's result, not a void cap hit (`a1_validity`; rigby `cap_hits` excludes it, the row carries `runaway_cuts` + `turn_cap`); a checkpoint replays the cut; a branch parent must match `turn_cap`. Mechanism on all four factories; the VALUE is measured on rigby only (A3B, thinking off) — a GPU-fleet value needs its own turn-length measurement (thinking-on answers run to 28K), so GPU plans send none; **Deployed 10-15 ~09:05 on all four** (Tolkien gate 805, rigby-a1 gate 57, GPU A1 group in sync on Mothership/BigHonker/Softserve, `test_a1_validity` same-bytes green). **Live decoy (12b) PASSED** (`a1v22-rigby-guard-decoy.20261015`, turn_cap 48 on the real llama-server): control_add and wordy each ended `runaway` after 2 cuts, graded (0/4, 0/25), `void` None, `cap_hits` 0, the model acting between cuts (wordy: 4 reads + a list in 6 turns). **Validation at 20,480 (`a1v22-rigby-guard-react-paasio.20261015`, shipped 11:33): no runaway this time, no healthy turn cut** — paasio 23/25 in 619 s (finish), react 12/14 in 1,093 s (finish, the fresh re-run). It exposed a SEAM BUG: the react attempt resumed after a deploy pause ran 41 turns until the conversation itself outgrew 65,536 (HTTP 400 `exceed_context_size`), which ended as an engine error → void → re-run (~46 min) against the 10-09 rule (filling the model's own tested context = its result, scored). **Fixed 10-15:** the harness ends such an exercise `ended: "context_full"`, graded; the payload counts it as a cap hit, so `at_ceiling` scores it at rigby's tested 65,536 and voids it below (A1 suite 195, rigby-a1 gate 58). **GPU path — OPEN, put to the owner 10-15:** ollama does not refuse an over-context request, it TRUNCATES (logs `msg="truncating input prompt"`, e.g. Mothership 08-21: limit 4098, prompt 15,856), so a GPU A1 conversation that outgrows `num_ctx` would continue on a silently shortened history. Checked: Mothership's live log (`%LOCALAPPDATA%\Ollama\serve.log`, per `C:\ProgramData\HAI\start-ollama.ps1`) holds 0 truncations, but it is overwritten at each ollama restart and starts 10-14 04:37 — the A1 runs of 10-05..10-12 are NOT covered, so past GPU rows are neither cleared nor convicted. Do NOT use prompt_eval_count shrinks as the detector: a 10-15 scan flagged 40+ 'shrinks' in 3-12K conversations at 122,880, none of them truncation — the previous reply's eval_count includes its THINKING, which the harness never sends back, so the next prompt is smaller than prompt + output. (A same-day claim that ollama counts only the newly read part when cached was WRONG: measured on Softserve qwen3:8b, prompt_eval_count is the full prompt cold and cached alike — 1,355 both, read 340 → 16 ms.) **Closed for new runs 10-15 by context compaction on all four factories** (Inspect's summary compaction on the one A1 call path, calibrated on ollama's own full prompt count, keeps every conversation below num_ctx — see [[34-A1-Agentic-Build-Family-Spec]] § Context compaction; GPU decoy `a1v22-py-agentic-hai-so-q3-8b-spot-tc13312.20261015-1439`: 2 compactions, graded). Past GPU rows stay neither cleared nor convicted; **#36 CLOSING (10-15 evening):** speed evolution over (every rigby lever has a verdict; `rigby_arsenal.json` 0 open); the ONE re-price of the shortlisted lead (A3B thinking-off, MTP file, 1060 standard order) is running through the menu fill (`menu_fill.RIGBY_PLACEMENT` + energy per placement in `menu_policy`; the 10-04 rigby hold narrowed, its words kept) and the final A1 role set (`a1v22-rigby-moe-hai-off-spot-1060-final`) with turn_cap + compaction: wordy 25/25, bowling 31/31 (83 min: 38K tokens + one compaction costing 686 s — the summary call re-reads the whole conversation, and summarized to ~149 tokens), react 12/14, paasio 22/25. Generalize-up is #34 Phase 9 (charter); the closeout hands prices → Phase 7, rigby's A1 number → S10. upstream report alike, unresolved: llama.cpp #26266 (Qwen3.6 + MTP "thinking" after a version bump). **r72a MTP draft 3: LOSES** (15.97 vs draft 2's 16.88 tok/s, acceptance 88.6 vs 94.1 %, 17/18) → draft 4 cancelled unrun; **draft 2 stays.** **r73 `ubatch 4096`: read 101.6 / 127.7 (+42 / +30 %) but write 13.32 / 10.38 (−19 / −38 %)** — the 4.3 GB compute buffer pushes weights off the card; A1 is write-bound → **2048 stays** (4096 a candidate for read-heavy job types). **1060 fell off the bus a THIRD time, idle** (Xid 79 09:37 UTC 10-15); rebooted 12:09 UTC (uptime -s verified). **llama.cpp UPDATED (the owner: "Update llama please"):** current master `e467e5b` (10-15) cloned to `/home/owner/llama.cpp-latest`, lab pre-Volta graphs patch re-applied, CUDA 11.4 sm_61 build verified (sees the 1060, every Tolkien flag incl. `draft-mtp` present); compose `/llama/cuda` repointed (backup `.bak-20261015-121021-before-cuda-latest`; the 10-10 build kept in place as fallback); deployed together with C2 (`warm_gt730`, gate 802). Process slip: no watcher on r73 → the lane idled 01:42 → 07:08. **10-15 MORNING — new engine checked, THE WHOLE BOX MEASURED.** r74a (new engine e467e5b, standard order): write 16.81 / 16.86, read 72.6 / 99.6 = the 10-10 build within ~1 % → **kept**. r75 (first whole-box try) **VOID**: its A3B ran 07:23–07:29 while the warm servers were still LOADING — merry and pippin had lost their USB (RNDIS) links in the 07:09 rigby reboot (Tolkien's USB keeper restored them only at 07:43), and the warm load waited the full 600 s for them before dealing any work. **r76 (redo; A3B submitted only after the warm servers were resident — watcher `scratchpad\wait_resident.py`, positive-controlled both ways; full overlap 07:47:36–07:54:20 inside 07:46:51–07:54:27):** rigby's A3B write **15.71 / 15.90 (−6 % vs alone)**, reads unchanged; **the GT 730 as an 11th warm node (`warm_gt730`) served 24 of 175 turns at median 10.1 tok/s — the fastest node** (tablets 6.5–6.9; merry / pippin 4.7); Fellowship total **~50 tok/s** while serving (22.8K tokens / 456 s); 151/175 correct (~86 %, as the tablets alone). ⇒ **THE WHOLE BOX ADDS: ~16 tok/s of A3B-class output + ~50 tok/s of small-model output at once** (pre-#36: ~5 tok/s A3B). The 730 costs the A3B ~6 % (its server needs CPU; tablets alone cost 0–2 %, r71) for ~10 tok/s of small-model work. **Fix built from r75 (finish what is flagged): warm nodes serve the moment THEIR server answers** (`start_warm` announces each node via `on_up`, polled between staging steps at a 1 s timeout; the 730 polled in the same loop) — one dead node no longer idles the rest; gate 803 (a first gate run failed on a progress test that assumed a fixed total — fixed to assert the per-report invariant, not weakened). **Live-proven (chat1-warm-q3508bnt-dealearly-check):** 16 turns done at 08:01:09, a minute before the resident line (08:02:13). That check also exposed a PRE-EXISTING deck bug: warm orders' x/y read "39 / 36", "44 / 36" — the runner's total counted one control per ARM, warm answers one per NODE. Fixed: the deck shows the arm's own count; the completeness count is unchanged; gate 804; live re-check read 12/39 → 39/44 → 44/46. `warm_gt730` + both fixes documented in [[Fellowship-Order-Taker-Design]] (warm orders). | Found when r66 (small-read threshold) and r67a/c (MTP arm + control; Unsloth MTP build downloaded with the owner's OK, sha256-verified; `gguf_tensor_diff.py`: 613 non-expert tensors identical, all 120 routed-expert tensors RE-QUANTIZED, +20 MTP tensors 424 MB — so r54 is not their control) were refused in 1 s each by the by-name card check (`found: []`; nothing ran on the wrong card). Orders unchanged in `hard-bench\orders\llm-36-r66-*`, `-r67a-*`, `-r67c-*`; resubmit after recovery (needs a power cycle — the owner's hands). |
| **10-13 11:4x** | **#36 round 32 — the handoff restores into the CARD writer and writes at card speed (16K: 3.49 tok/s, cache reused, 1 token re-read); quantized KV loses on the GT 730 (24K: f16 3.12 · K q8_0 2.55 · K+V q8_0 2.10 tok/s) — production writer = rigby + card, f16.** The 10-tablet split read 16K 1.16× faster than the card config and tied rigby's CPU alone (plan without attention cost). Round 31 (8 tablets) lost aragorn to Android's low-memory killer at 16K (259 MiB left): the stage estimate missed up to +173 MiB of growth — fixed, one function `stage_need_mib` for planner + preflight (gate 762). Earlier the same day: handoff `writer` placement + KV-type bench knob (gate 761). Next: T2 (reading at depth) planned WITH `--attn-ms`, then production wiring. |
| **10-13 00:0x** | **#36 — MAIN SESSION OWNS IT (the owner, 10-12; the side session idle). Re-plan from data already on disk: WRITING AT DEPTH is rigby's biggest cost and the design's writer left out the GT 730** — 55 % of A1 tokens are written past 12K; card vs CPU writing 2.97 vs 2.13 tok/s at 16–32K, 2.53 vs 1.52 past 32K (preliminary, CPU n=18 calls); writing ≈ 60 % of A1 wall on rigby ([[36-Split-Inference-Helpers-Read-and-Draft-Design]] scorecard). BigHonker as a reading stage OUT (the owner: the ladder moves work up, it does not borrow a bigger box). **Tablet power, resolved:** sam was NOT starved by its port — a reboot took it −9 → +339 mA on the same 500 mA port (cause unknown; the `boost_work` threads are normal on every X606F); faramir died at 7 % and sits in charger mode (empty HID gadget) — rigby's root ports have no power switching (xHCI `HCCPARAMS1` PPC = 0, measured), so it needs its power button (the owner, 10-13). **DEPLOYED (gate 759):** Tolkien's charge keeper reboots a node "Charging" with no gain for 2 h, idle only, once per 24 h, then asks for a physical check ([[Fellowship-Order-Taker-Design]] § "The charge keeper"). Next: card-writer handoff · writing-at-depth sweep (CPU/card × KV f16/q8_0) · T2 reading at depth vs the card config · then production wiring. |
| **10-12 07:5x** | **#36 rounds 25–29 — answers: (1) the split is fastest at ub 64 (25.05; 32→22.2, 48→22.7, 128→24.5, 256→21.6, 512→17.6 tok/s) while rigby alone rises with ub, so no ub tuning left; (2) the window against rigby's best ub: 4K 25.05 vs 18.7 = ~1.34×; 16K 17.0 vs 15.66 = ~1.09×; 32K multi-rigby 14.42 vs 12.83 = ~1.12× (single-rigby plan 12.76 ≈ 1.0×); (3) quality: tablet arithmetic costs nothing measurable — KLD vs rigby base 0.0114 (same top token 93.7 %) vs rigby-against-itself noise floor 0.0111 (94.1 %), 16 chunks × 512 of one text, a first check; the handoff is quality-safe.** Round 27's fourth run (15-stage layout at 16K) hung 4.7 h, all threads asleep, 0 % everywhere; Tolkien's watchdog flagged "frozen" every minute after 20 min (report-only by design) and a stop slip cleared it in 2 min with the finished runs shipped. Probable cause: faramir's battery — the governor log (dated cycle headers) has it 26 % (10-11 07:53) → 19 % (17:44) → 7 % (19:44), flat at 7 % "Charging" for six hours, last reading 7 % at 02:04:33 on 10-12, unreachable from 02:14:33; run 4 started ~02:01. It is unreachable and trickle-charging (the owner away). Sam has declined for six weeks (60 % in September → 41 % 10-09 → 16 % "Charging" 10-12): (the port explanation was refuted 10-13 — see that row: a reboot cured sam on the same port). **10-12 ~14:30 DEPLOYED (gate 745):** battery % + charging status in Tolkien's per-tablet telemetry (stores migrate in place); a stage run refuses a tablet under 50 % (the governor's band low, interim until a loaded discharge curve exists) or with no fresh reading; the watchdog's "work" beat for bench orders is now the stages' own CPU from telemetry ("looks dead" instead of "not measured"); per-call read/write times and cached tokens recorded for llama-server calls (gate 738). Live check: telemetry matches the governor on all nine reachable tablets. Open: round 30 (shared-rigby 16K retry) when faramir is back; test whether Tolkien's 2-second adb sampling itself keeps sam/faramir from charging; ports/cables for sam and faramir (physical, the owner); planner should skip low-battery tablets; drafting (job 2) one layer up; production wiring of split reading + handoff. |
| **10-11 21:0x** | **#36 round 24 — BASELINE CORRECTED: rigby alone at 4K reads 16.2 / 16.6 / 18.1 / 18.7 tok/s at ub 64 / 128 / 512 / 2048, so every split speedup quoted against ub 64 was inflated: the 4K split (25.6) is ~1.37× rigby at its best, not 1.55×; 32K's 1.26× likely ~1.1× (rigby alone at 32K, large ub, unmeasured).** Handoff mechanics clean: with a rigby-only reader the written text matched the control for 522 chars (vs 10 with tablets) — the residual divergence is float order (reader ub 64 vs control), the early one is the tablets' ARM arithmetic. Open: does tablet arithmetic cost quality (a KLD/perplexity check vs rigby), and does the split itself gain at larger ub (only ub 64 tested). |
| **10-11 20:3x** | **#36 rounds 20–23 — the 15-stage abort was the OUTPUT LAYER ALONE on the last stage (not stage count, not the copy ring: a 32-copy rebuild and a one-batch prompt still aborted; every stage computed its chunks, the output-only stage none) — round 13's fault too, mis-read then as "rigby must be last". Planner + Tolkien now forbid it (gate 733). Multi-stage rigby then RAN: 32K 14.42 tok/s vs rigby alone 11.46 (1.26×; best before 1.11×; planner said ~1.7× — rigby's 5 stages on 4 cores ran slower than its sum model). HANDOFF BUILT AND WORKS (round 23, gate 734):** read 4K on the split, save the slot (282 MB incl. 2 recurrent checkpoints, 4.2 s), restore on a rigby-only writer (0.1 s); the writer re-read **1 token** and wrote at **4.96 tok/s** (control 4.92). Open: (a) the text diverged from the control at char 10 — round 24 repeats the handoff with a rigby-only reader (identical arithmetic) to separate tablet rounding from a handoff fault; (b) **owner PASS on our baseline:** every rigby-alone reference ran at ub 64 (the tablets' chunk); through llama-server rigby alone read 17.8 tok/s at its default ub vs the split's 19.3 — the 1.55× may be inflated; round 24 sweeps rigby alone at ub 128/512/2048. The async engine (`/llama/next` x86) was rebuilt with `GGML_SCHED_MAX_COPIES=32` (old binaries archived `/home/owner/llama-next-bin-archive-20261011-224919`); round 17's "1.11×" and this row's 1.26× share the ub-64 caveat. Supersedes row 17:0x's "Parked" line. |
| **10-11 17:0x** | **#36 rounds 18–19d — WRITING through the split is 3.6× SLOWER (1.4 vs rigby alone 5.04 tok/s, 128 tokens after 4K): tablets READ only; production = read on the split, hand the cache to a rigby-only writer** (hybrid model: ~80 MiB cache at 4K; `llama-server` slot save/restore is the untested path). **Multi-stage rigby BUILT (gate 729–731: `rigby2..rigby16` stages, one local rpc-server each, cores shared by layer share; `tolkien_plan --shared-rigby` paces on rigby's TOTAL; ggml's 15-stage cap — `GGML_SCHED_MAX_BACKENDS` 16 incl. the host — enforced in the planner and in `spec_defects`) but a 15-stage layout aborts** (rounds 19b/19c: "Remote RPC server crashed" — gandalf's server went silent on a GET mid-run; no LMK, no native crash, rigby's servers fine; rigby stage servers now log beside the trace). The same planner at 11 stages ran fine (round 19d, B tablets 2 layers each): **25.42 tok/s — 4K stays ~1.57×**, the bottleneck is the A tablets + rigby. Fixed same pass: round 18's writing rows had leaked into the planner's reading rates (~30 % fast; it promised 1.85×) — reading samples now require a whole-chunk read from an empty cache (gate 731). **Parked (the owner's usage budget):** (1) why gandalf goes silent at 15 stages (unlocks multi-rigby at 32K, ~1.6× on paper); (2) the read-split → write-rigby cache handoff. |
| **10-11 14:0x** | **#36 rounds 16–17 MEASURED — at a 32K prompt split reading barely pays: rigby alone 11.46 tok/s, round 14's layout 11.79 (1.03×), the attention-aware planned layout 12.76 (1.11×); at 4K it is 25.05 vs 16.2 (1.55×, round 14 reproduced).** Linear layers cost the same at 4K and 32K; an attention layer on a tablet goes 0.7 → 3.3 s per chunk (B) while on rigby it costs ~0.2 s, and every 4th layer is attention, so any tablet holding 4+ contiguous layers carries one. Planner now takes a measured per-class attention cost (`--attn-ms`, gate 724); its 32K bottleneck prediction 5.1 s came in at 5.0. With the detector paused, B tablets hold 2 layers (ran fine). My pre-run estimate of rigby alone at 32K (~8.7 s/chunk) was wrong — 5.6. **Next lever (arithmetic, unmeasured, the owner's call — usage budget):** rigby holds all 10 attention layers as several small rigby stages between the tablets' linear stretches (~3.5 s vs 5.6 → ~1.6× at 32K); needs Tolkien to allow rigby as more than one stage. |
| **10-11 07:3x** | **#36 — SPLIT READING PAYS on rigby's best model: Qwen3.6-35B-A3B MoE, rigby + 10 tablets 25.1 tok/s vs rigby alone 16.2 on a 4K prompt (1.55×)** ([[36-Split-Inference-Helpers-Read-and-Draft-Design]] § "ROUND 13–14"). Reached by removing three host drains (lab patches 1/3 + `graph_reuse:false`, trace-proven with patch 4) and planning the split from measured costs (`tolkien_plan.py`: per-class, per-tensor-type rates fitted from Tolkien's own traces; "frodo is slow" was frodo holding Q6_K-heavy layers). Same pass, Tolkien (gate 709): stage memory preflight (GGUF + llama.cpp's split vs real MemAvailable — rpc-server reports TOTAL RAM as free), DRAM preflight, rpc-server `-c` cache (load 141 s → 41 s), parallel weight loading (patch 5), copy-only-what-changed, the Tolkien gate no longer writes to the live order desk, telemetry commits once a second (readers were ~15 s stale). Battery was tested and ruled out (the owner's bet); the battery governor was fixed (survives an unreachable tablet, verifies the knob, covers all 10) and is back ON (09:0x row). Next lever: memory on the class B tablets (~20 % busy, one MoE layer each). |
| **10-10 22:1x** | **#36 round 2 — pipeline overlap reached with a LAB ENGINE PATCH; tablets not yet paying** ([[36-Split-Inference-Helpers-Read-and-Draft-Design]] § plan 1, "ROUND 2"). Async-RPC llama.cpp `23b0202` built in its own worktree and mounted at `/llama/next` (live builds untouched; `GGML_SCHED_MAX_COPIES=16`). Stock still ran stages one at a time: RPC `cpy_tensor_async` is NULL, so every hand-off blocks the host thread (read in `ggml-backend.cpp`). Lab patch (`lab-engine\patches\rpc_cpy_tensor_async_patch.py`, client-only): perplexity identical to CPU on a 2-server split, overlap proven (7B over two local servers: 46 s vs ~88 s in turn). rigby + 4 tablets: 3.11 vs rigby alone 11.84 tok/s — the slow stage not yet named. Built same night: Tolkien's telemetry logger (`tolkien-telemetry`, searchable SQLite, summary-only reads, crash-decoy proven), bench live stats to the homepage tile, mid-run stop for bench, fleet_deploy no longer requeues a customer-stopped order. Next: the RPC trace inside Tolkien's stage runs, bigger chunks, a measured split. |
| **10-10 19:3x** | **#36 round-1 bench MEASURED** (Tolkien `bench` kind, built + gated + deployed 10-10; [[36-Split-Inference-Helpers-Read-and-Draft-Design]] § plan 1). Check cost on rigby: checking 4 drafted tokens costs 2.5–2.7× one token on the 7B/14B and 5.1× on the MoE (MoE off the drafting list; the drafting lever left is draft length 1–2). Reading, same 1.5B ruler: tablets 23–24 % of rigby (class A/B), 12 % (4-core); the 8 octa tablets ≈ 1.9× rigby combined. USB link 16–24 MB/s: not the limit. GT 730 as a second reader: no gain (layers run in turn). Next (round 2, new questions first): pipelined reading across rigby + the octa tablets (llama-bench `-rpc`/`-ts`/`-ub`), and draft length 1–2 on the 7B/14B. Bench rows now checkpoint per run (gate 632). |
| **10-10 18:0x** | **#36 step 1 MEASURED — one tablet drafting for rigby's 14B LOSES in this configuration; NOT concluded** (the owner: *"I still believe we can do it… choose the right models and settings. and do the prefill stuff"* — the levers left and the prefill case are in the #36 doc) ([[36-Split-Inference-Helpers-Read-and-Draft-Design]] § Measurement plan 1). Order `hardbench-rigby-36a-qwen14b-f2-draft-gandalf.20261010`, identical to order 13 except `draft_host: gandalf` (Tolkien `draft_host`, built + gated 610/610 + deployed 10-10). 21 matched rows: **1.87 tok/s** median (1.54–2.13) vs **2.86** (2.37–3.29) with the draft on rigby and 2.38 plain; slower on all 21, acceptance equal (0.86 / 0.87), correct 20/21 each. Stopped as a known-bad arm at 23/73 (results file VOID by coverage; verdict = the matched rows). Why: gandalf (MT8768, Cortex-A53) drafts a 0.5B far slower than rigby's i7, stock llama-server drafts and verifies in turn, and verifying a 4-token batch costs rigby ~3× one token (no AVX2) — so the "~2×" estimate is withdrawn for rigby (archived). Still open: external drafting where checking is cheap (a GPU checker, step 5); tree drafting on rigby inherits the same verify cap. Side finding, fixed same pass: Tolkien's liveness and adb now take the USB wire first (legolas was benched two days with a wire that worked — [[Fellowship-WiFi-Watchdog]]). |
| **10-10 17:0x** | **REOPENED as project #36 (child of campaign #34, registered 17:3x — [[36-Split-Inference-Helpers-Read-and-Draft-Design]]): the small end — rigby with the tablets as its helpers, the new shape** (roles assigned PER TABLET, at the same time — the owner: *"five of them do read and five of them do write"*: some tablets help rigby READ, others DRAFT for it; rigby alone writes and checks; the owner: *"this is really what I had in mind with Voltron … I had no way of describing it at that point"*; and it generalizes UP: *"who's to say we couldn't do this with mothership with the rest of the lab below"* = Phase 9's "generalize upward") (the owner: *"I think we should reopen the stuff on the small end with Rigby and the fellowship … to really make a small revolution for our lab"*). Sources: the Backburner video (prefill/decode split; helpers only READ, never sit in the writing loop; old KV pages on helpers for hybrid models; main-machine prompt cache + drafter) and the llama.cpp expert-cache video (`Claude Home\research\`, side-session handoff `HANDOFF-side-session-2026-10-10-ram-strata-backburner.md`). the owner's own extension: *"we spread out the read across devices and then while models internally do MTP who's to say we couldn't externally do MTP?"* = **drafting on the Fellowship, verifying on rigby** (distributed / tree speculative decoding; published shapes: SpecEdge, FlowSpec, DiP-SD). This is NOT the closed Voltron question (pooled layers through the writing loop, priced 10-02); it is a new design, measurement first. No build started. |
| **10-10 01:1x** | **Delegation is a CASCADE, not pairs — restated by the owner a second time** (*"somebody collapsed the teams and delegation testing, I've explained that it's supposed to be cascade not just one or the other"*). The frozen list's team arms (#3 3.8+rigby, #4 3.8+BigHonker) are fixed-pair PRICING arms and have now shown what they can: a helper's PRICE, and that on the hard set one pinned helper does not raise a solve (the misses are hidden-test spec blind spots the brief inherits; #4: 2/7 vs 3/7 solo, one context runaway). **No delegation conclusion is drawn from them** (the owner 01:2x: *"I didn't want you to draw the wrong conclusions from us having pinned a specific delegation"*) — a pinned pair is a price measurement, the cascade is the test. **The cascade has never been run because its mechanism is not built:** the PLACED delegate (`delegate` → an order at Kirk's desk with family + urgency, no target → Spock places on the 8 GB tier or the Fellowship → Tolkien picks rigby or a tablet class; `escalate` moves the whole task up) — the first production build after measurement (rides Phase 7 step 3, 10-07 row). **LOCKED 10-10 01:4x after the library check (the owner: *"compare against design docs and charter before we lock in"*):** (1) #3 stops after its next completed exercise (its pieces = rigby's round-trip price; the corpus it grows is of artificial single-file splits, the wrong shape for the advisor), rigby's R-team repair requeued, #7/#8/#9 drain; (2) BUILD the placed delegate = the A1 spec's own `delegate` design finished (placed by Spock via `policy.json`, no `only`; Fellowship pieces to Tolkien; `escalate` up); (3) ONE cascade arm, named in [[34-Phase-7-Placement-Policy-Design]] § exit criterion with the decision it unblocks (the table's split decision), on the W1 parts (the S10c decomposed pool, one level up) with S10b-style lead-written checks as the selector, vs the lead alone. Known limit: Tolkien's rigby-vs-tablet-class choice is unbuilt; arm 1 places by piece kind. |
| **10-09 20:0x** | **CROWN: `qwen3.8:27b` at xhigh on Mothership (Vulkan) — PHASE 2 CLOSED.** the owner: *"Sounds like according to all data we need to crown the 3.8"*. Data: the 3.8 8/9 original + 3/7 hard (pooled 9 of 14 scored); Ornith stopped 13:27 at 2/6 (best case a tie at 8); Nemotron stopped 19:10 at 0/6 (best case a tie at 9, failed two the 3.8 passes). No behaviour arms owed to a challenger. Lead defaults so far: finish gate NO (same solves, 3× cost), reviewer NO (identical score); delegation defaults from #3/#4 (Phase 7, running). |
| 10-04 | Test each box in its END-STATE ROLE, not more grid cells: *"we're not just blindly testing models."* |
| 10-04 | ONE shape at every layer: *"If 3.8 needs tablets then it uses tablets"*; delegation goes DOWN the layers (10-06: *"Rigby should be calling down to the tablets, not up"*). |
| 10-04 | No answer key built by Claude; public task sets with their own tests (Aider Polyglot, then Terminal-Bench). |
| 10-04 | ROUTING SHAPE = [[HAI-Agent-Surface-Design]] § "The design": triage picks tool / fellowship (rigby + tablets via Tolkien, simple chat) / 8gb (mid-weight + overflow with caveat) / lead (real work, every unsure case); Spock places within the tier. Phase 7's table is keyed by these tiers (2026-10-07). Re-explained by the owner on several days running — *"Every day we get the design settled and then you get lost in the sauce of Work"* (10-07). |
| 10-07 | **Fellowship chat = Option 1: Tolkien keeps chat models WARM on the nodes and hosts several chats at once.** the owner: *"Rigby is separate from the fellowship, although the tasks … that get run on it are administered by Tolkien by default. We should be thinking of it as a wickedly overpowered tablet … if chat has cascaded past a higher tier model down to the fellowship … Tolkien is supposed to know how to do those … we are supposed to be able to host multiple chats at the same time. Soooooo… sort of option 1?"* So: rigby is one node (the strongest); Tolkien places each cascaded chat turn on a warm node, many in parallel. Not Voltron (one model across nodes), not "rigby answers everything". First step: measure a WARM tablet turn per class (the 08-31 Gauntlet's open persistent-server comparison) — Claude. |
| 10-07 | General chat is NOT lead-only: *"Surely general chat could go on the other boxes and models too."* Acceptable was ALREADY settled (10-02/10-03): out only by the stop rule (cannot fit / priced out per correct answer at 10× / the owner's holds); dominated = fallback. `menu_policy.py` reads those verdicts, no floor of its own. |
| 10-04 | Single model per card on the AMD box (no concurrent models on the 7900 XTX). |
| 10-05 | Best settings = top quality AND speed for the task type (one resolver, `best_settings.py`). |
| 10-05 | Don't sandbag: diff prompts/protocol against the published source; count cap hits. |
| 10-05 | One layer at a time for team runs (shared hardware). |
| 10-05 | Adopt before building (Aider's own harness, Inspect AI). |
| 10-05/06/07 | **Factory deploys use pause/stop → deploy → requeue; never wait, never ask** (*"THATS WHY WE BUILT THE PAUSE AND RESUME"*, 10-06; *"I tell you this every day"*, 10-07). |
| 10-06 | Use the factory control slips (*"You have my permission to correctly use the factory control slips"*). |
| 10-06 | Control arms culled to SPOT tests (the 10-exercise set); controls run last and are droppable. |
| 10-06 | Softserve = the always-on ADVISOR host, answers in SECONDS, no synthetic Plex load. |
| 10-07 | v2.1 retired; every lane on harness v2.2. |
| 10-07 | **Test ROLES, not the menu** (repeat): every queued arm must name the role it informs (lead, sub-agent, reviewer, advisor, lead challenger); arms that only fill the grid or add external comparability are cancelled. |
| 10-08 | **#34 TESTING IS FROZEN to the FINAL TEST LIST below** (the owner: "I'm so tired of having open things on this project… every day you telling me that we have a day or two left of testing"). **Only REPAIRS may be added** (the owner, same hour: "I want to add tests if we have legitimately messed up our methodology or we didn't give the models the best fair shake… Half of these added tests were because of the constant compaction and re-hammer outs") — a result invalid because the METHOD was wrong or a model did not get its fair best-settings shot. **New questions** go to PROJECT-BACKLOG for the next model generation. Every change to the list names which it is. After a compaction, READ the list — never reconstruct it. **Finish: queue drains ~Fri 10-09 afternoon → the same day: crown decision, lead defaults (gate / reviewer / delegation), helper prices into `policy.json`, Phase 2 CLOSED, measurement CLOSED.** What remains after that is building (Phase 7 callers, McCoy) — not measuring. |
| 10-08 | **The spot set is SATURATED for the 3.8 at xhigh (9/9) — arms that must show an IMPROVEMENT cannot run there** (the owner: "this is what I'm saying when I tell you to look at the tests we have queued to see if they make sense… we just spent ALL NIGHT running wrong/unnecessary tests"). The 10 were picked when the 3.8 scored 5–7 at medium; the effort fix put it at the ceiling and nobody re-asked what each queued arm could still show. **Cancelled** (plans kept): gate+review, team-ri-dir, the 4 BigHonker-team arms, the gate replicate, Ornith, Nemotron. **The 24-exercise probe was itself cancelled before it ran** (the owner: "if it passed all of those additional 24… that still gives us no new discriminating information"): in all three earlier full-34 runs (medium) 19 of those 24 always passed. **HARD 7 from history instead:** dot-dsl, forth, pov, tree-building (failed in every full run), scale-generator (failed once), react (failed again at xhigh 10-08), sgf-parsing (16 min → 3 h run to run). Queued 07:51 on it, early-stopped vs the hard-7 baseline: the 3.8 solo (baseline, priority 30) → reviewer branched from it at finish (29) → 3.8 + rigby directed (28) → 3.8 + BigHonker directed (27) → Ornith → Nemotron, challengers thinking on. Finish gate dropped (same solves, 3× cost). **Hard sets are PER MODEL**
(the owner: "they need to be run on ones that the Challenger models failed… not just all of the ones the extra models"; the
spec's own principle — the spot set was "the 10 exercises that split the models"): a "does X help model M" arm runs on
M's OWN solo failures; a crown comparison runs where EITHER M or the 3.8 fails. Re-queued 07:55: **Ornith on 12** (the
3.8's hard 7 + Ornith's history fails bowling, zipper, rest-api, wordy, transpose); **Nemotron on 15** (spot ∪ hard 7 —
it has no usable full run), both early-stopped vs the 3.8's POOLED fixed-tools baseline (spot `…-exhigh.20261007-1542`
+ hard-7 `…-exhigh.20261008-0751`; `a1_stop` accepts a list). **Kept:** the solo baseline, the 3 behaviour arms (they score behaviour, not solves), the menu cells. The gate arm's 8/9 at 3× cost stands as a cost/breakage reading only. |
| 10-08 | **Early stop for A1 arms** (the owner: "Yes option 1"): an arm ends once it cannot come within run-to-run noise (2 solves, the measured spread of four 3.8 runs) of its baseline; never for being better; off until the baseline delivers; not for behaviour arms or replicates ([[34-A1-Agentic-Build-Family-Spec]] § "Early stop"). Added to 8 queued arms. |
| 10-07 | **Branch arms from saved runs** (the owner: "couldn't we save the regeneration of the initial part… and use what we got earlier as the input?"): an arm reuses a parent run up to its own difference — gate+review from the gate run at its finish, b-recover/b-correct from the solo baseline at the first test ([[34-A1-Agentic-Build-Family-Spec]] § "Branching"). Paired comparison, ~4–5 h less card time; branched copies replaced the unbranched ones 23:30. |
| 10-07 | **Every project must be re-runnable by the lab's own machinery** (the owner: "we may need to run this entire project again in a year when the latest models release… ideally able to be run without your aid… maybe asking you as a consult for interpreting the results" — "that's true of all the projects"). Build every step as an order / tool the lab owns, never a Claude-only procedure; Claude is a consultant for interpretation. |
| 10-07 | **Phase 3 (8 GB tournament) CLOSED** (the owner: "Yep sounds good"): the scope challengers that fit 8 GB (MiniCPM5-1B, MiniCPM-V 4.6, Nemotron 3 Nano 4B) were priced on both 8 GB boxes and lost on merit; qwen3:8b keeps the seat. Phase 2 stays open only for Ornith's and Nemotron's A1 lead-role runs ([[34-CHARTER]] § "Current phase & gate"). |
| 10-07 | **Delegation goes DOWN to ANY tier below; escalation passes the WHOLE task UP** (restated by the owner after it collapsed to fixed one-helper team arms): a lead at any layer places each piece on any worker below it; a task that needs a higher tier moves up whole and the higher layer owns it. The fixed `--team=<box>` arms are pricing measurements, not the production shape ([[34-CHARTER]] § "How results shape action" 3). **Two routers:** Spock places on the fleet (Mothership / 8 GB boxes / "the Fellowship" as one destination); a Fellowship-bound piece is an order to TOLKIEN, which picks rigby or a tablet class (rigby merely hosts Tolkien; the lead never targets rigby directly — today's `RigbyDelegator` pins `requires: rigby.llm` and must stop doing so in the production delegate). **No model picks routing:** the lead model's `delegate`/`escalate` only submit an order to Kirk; Spock places it deterministically (advisor advises only); Tolkien places Fellowship orders. The PINNED team arms are correct for NOW (the owner 10-07: *"we were OK with having pinned things just for the testing purpose… we don't have all of the routing data yet"*) — they ARE the helper prices routing will read. Production build, once that data is in (rides Phase 7 step 3): the placed delegate as an order to Kirk's desk carrying family + urgency (no target), placed by Spock, Fellowship pieces placed by Tolkien; `escalate` likewise — Claude. The Softserve advisor model's role is named **McCOY** (the owner 2026-10-07: *"Let's do McCoy"* — Uhura/Geordi fought autocorrect, Data collides with normal usage). |
| 10-07 | **The user's own model/effort pick is never removed** (*"I could fully see myself just wanting to give myself the full throttle whenever I'm the one that's using it so we can't delete that choice from me"*): the owner pins the 3.8 at full effort when he wants; the Phase 7 table + advisor route only Auto turns, HAI's background work and sub-agent pieces; a pin is never overridden, only informed ([[HAI-Agent-Surface-Design]] § "Always an agent"). |
| 10-07 | **Price line = from the intelligent arm** (*"the Priceline should definitely be computed from the intelligent one. I don't want us to be throwing out results for the top-tiers unjustly"*): cheapest arm's cost per solve, failures charged ([[34-Hard-Bench-Order-Spec]] § "Price ceilings"); 15 arm-families un-stopped. Mothership is never reserved for one model (it unloads after 5 min idle) — residency is a caveat/cost in the table, not a refusal. |
| 10-07 | Rigby's GT 730 is used for the model (*"the gpu should be used no matter what … so they aren't sandbagged"*): attention layers + `--no-op-offload`, no speculation. |

---

## 🧾 FINAL TEST LIST — #34 measurement (frozen 2026-10-08; READ this after a compaction, never reconstruct it)

Every remaining test, the ONE question it answers, where it runs and what it is compared with. Changes only by a
REPAIR (method wrong / model not given its fair best-settings shot) — each change logged below the table with "repair:"
and why. Fixed tools = A1 harness `cee5ef60`. Hard 7 = dot-dsl, forth, pov, tree-building, scale-generator, react,
sgf-parsing. Early stop (`a1_stop`, margin 2) where marked.

| # | Test (order) | Question it answers | Exercises | Compared with | Status |
|---|---|---|---|---|---|
| 0 | 3.8 solo xhigh (`…-exhigh.20261007-1542`) | the 3.8's clean score on the original set; baseline for 6, 7, 8 | original 10 | — | **DONE 10-08: 8/9** (react 13/14; paasio unscored; 1 void row rerun) |
| 1 | 3.8 solo xhigh (`…-exhigh.20261008-0751`) | where does the 3.8 still fail at full effort? (the probe AND the baseline) | hard 7 | — | **DONE 10-08: 3/7** (scale-gen, react, sgf pass; dot-dsl 10/12, forth 52/54, pov 14/15, tree-building 11/13 — 94% of tests) |
| 2 | reviewer, branched at finish from #1 (`…-rev-bi-rthon-exhigh-br.20261008-0751`) | does a second reader (qwen3:8b, thinking on) fix the 3.8's misses? | hard 7 | #1, paired | **DONE 10-08: no gain** — identical score on every exercise (3/7) |
| 3 | 3.8 + rigby directed (`…-team-ri-dir-exhigh.20261008-0751`) | does rigby as sub-agent help on hard work? (rigby reserved by its lane slip) | hard 7 | #1, early stop | **STOPPED 10-10 01:27 by the owner's call, 1/7 + forth's in-flight turns** (delivered `…team-ri-dir-exhigh.20261008-0751_mothership.20261010-012702`). A pinned-pair arm; what it licenses is **rigby's price as a piece worker at 65k**: dot-dsl, one directed piece — helper wall 221 s, desk round trip 290 s (the lead waited 290 s), brief 3.5k chars, answer 2.8k chars — against BigHonker's 4.7 s / 45 s for the same kind of piece. Not a delegation verdict (see 10-10 01:1x decision). |
| 4 | 3.8 + BigHonker, **mandatory** delegation (`…-team-bi-must-exhigh.20261009-0905`; repair of `…-team-bi-dir-exhigh.20261008-0751`, cancelled) | does the 8 GB tier as sub-agent help? | hard 7 | #1, early stop | **DONE 10-10 00:55: 2/7** (vs solo 3/7; a fixed-pair PRICING arm, no delegation conclusion drawn — the cascade is the test, 10-10 01:1x row). Ran from 10-09 19:49; **instrument checked 10-10 00:2x on the artifacts (the owner: "seems suspicious")** — the lead's briefs are real specs (dot-dsl: a full `_build_graph` contract with error messages), the helper (qwen3:8b, think off = the menu's best coding setting) answered in 4.7 s / 256 tokens with a correct-looking function, and the lead ADOPTED it (patched it in, then wrote its own tests). So the mechanism works; the score does not move because every miss is the same hidden test the 3.8 misses alone (dot-dsl `test_unknown_item` + `test_malformed_graph_item`, pov `test_errors_if_destination_does_not_exist`, tree-building cycle/root, react remove-twice) — spec blind spots the brief inherits, which no helper can add. Caveat on what "mandatory" measures: the lead delegates exactly ONCE per exercise (6 of 7) and then works alone — compliance, not appetite; the one exception (forth, 4 pieces) filled its context. **DONE 10-10 00:47: 2/7** (scale-generator, sgf-parsing) vs the 3.8 alone 3/7 — forced delegation to the 8 GB tier LOST react (13/14) and gained nothing; forth's re-run failed 50/54 in 27 min with 4 pieces (its first attempt, the 97-min context runaway, is the scored one under the ceiling rule). Delivered `…team-bi-must-exhigh.20261009-0905_mothership`. **What this licenses (the owner 10-10 01:2x: *"I didn't want you to draw the wrong conclusions from us having pinned a specific delegation"*):** the PRICE of qwen3:8b as a piece worker (4.7 s / 256 tokens per piece, ~40 s desk round trip) and the fact that ONE forced helper of that kind does not raise solves on exercises that fail on hidden spec details. It is NOT a delegation default and NOT a verdict on the cascade (placed per piece across tiers), which has not been run. PRELIMINARY, n=1. |
| 5 | Ornith solo, thinking on (`…-ornith-best-thon-spot.20261008-0755`) | is Ornith a better lead? | its 12 (hard 7 + its own fails) | #0 + #1 pooled, early stop | **STOPPED 10-09 13:27 by the owner's call** (*"surely we already measured the value of them as fallback… this was supposed to show the upper limits"*): 6/12 run, the hard four failed as in every earlier run, scale-generator + react PASS; best possible finish 8 = the 3.8's pooled baseline ⇒ cannot beat it, remaining exercises only re-measured fallback value the menu already priced. As recorded 2/6; under the ceiling re-read pov → PASS (3/6). **Partial NOT yet delivered**: ship failed (Softserve share refused Mothership's logon), kept at `F:\foundry\output\compute\<order>`; reship slip queued behind the foundry deploy. Delivered partial of 08:01 holds the first 4 rows. |
| 6 | Nemotron solo, thinking on (`…-nemotron30-best-thon-spot.20261008-0755`) | is Nemotron a better lead? | 15 (original 10 ∪ hard 7) | #0 + #1 pooled, early stop | **STOPPED 10-09 19:10 by the owner's call: 0/6** (dot-dsl 10/12, forth 50/54, pov 14/15, tree-building, scale-generator 0/17 after 94 min of loops, react 12/14 — two of those the 3.8 passes) with 9 left ⇒ at best a tie with the pooled 9, cannot beat the 3.8. Ran 15:30–19:10 net of two stops (share, deploy). Partial kept on F:, reships on the next between-orders pass. |
| 7 | b-recover / b-correct, branched at first test from #0 | does the lead recover from a tool fault / take a correction cleanly? | original 10 | #0, paired | **b-recover DONE 10-10 10:29: 8/10, void 0** (`…-brecover-exhigh-br.20261007-2330`, 11 shipped to `workspace\a1\delivered\…_mothership\`; control PASS; spanned the 07:5x reboot, resumed from checkpoint). Misses react 13/14 and paasio 24/25 — #0's pair again, so the injected tool fault cost no solve; HOW the lead recovered is the transcript read in the paired reduce (#0/#7/#8). **b-correct DONE 10-10 13:30: 8/10** (`…-bcorrect-exhigh-br.20261007-2330`, desk record `done`; misses react 13/14 and paasio 24/25, #0's pair again; 2 rows loop-capped, on sgf-parsing which it passed and paasio which every arm fails). **PAIRED REDUCE DONE 14:20** (`workspace\a1\summary\20261010-1420-behaviour-arms-paired-20261010.md`, PRELIMINARY n=1): all four arms (#0, b-plan, b-recover, b-correct) solve the same **8/9** scored; behaviour verdicts **recover 10/10, correction 8/10** (its two misses are react and paasio, which no arm solves), **plan kept 4/10**; wall 23,868 s solo vs 16,458 / 15,033 / 10,754 s and J per solve 1.07 M vs 0.80 / 0.43 / 0.51 M (the branched arms price only their live part). |
| 8 | b-plan (`…-bplan-exhigh.20261007-1110`) | does the lead respect stated constraints? | original 10 | #0 | **DONE 10-10 06:01: 8/10, void 0** (delivered to `mothership-build\workspace\a1\delivered\…-bplan-exhigh.20261007-1110_mothership\results.json` + `transcripts\`, 10 rows, 06:01; Mothership archive stamp `.20261010-060142`; control PASS). Misses react 13/14 and paasio 23/25 (loop-capped, 34 turns / 6,780 s) — the same two as #0 (react 13/14 there too; paasio unscored there), so the plan constraint cost no solve. Per-exercise walls 96 s – 77 min; wordy also loop-capped (15 turns) yet passed. **Whether the lead FOLLOWED the stated plan is read from the transcripts in the paired reduce with #7** (`reduce_a1` over #0/#7/#8), owed when b-recover and b-correct land — not from the solve count. |
| 9 | 5 menu cells (re-opened by the price-line fix) | fallback prices for the placement table | menu | — | **DONE 10-10 13:49–14:08, all on Mothership, IN THE MENU 14:2x** (`hard_bench_fleet_tv.py reduce` → `build_menu` → `menu_standings` → `menu_policy`; policy.json regenerated). Rows at a context limit: **0 in every cell.** Nemotron-3-nano 30B think-on t0.6: chat-c1 63 rows (3 control), 61 right, median 4.7 s; work-w1 75 rows, 51 usable (24 letter-biased rows voided by method, superseded by the w1ops rows), 39 right, median 11.5 s. Tool/vision, 72 rows each: qwen3-vl:8b tool 67 right, 5 priced out; qwen3.5:9b vlm-2x 69 right, 3 priced out; qwen3.6:35b-a3b vlm-2x 61 right, 11 priced out (a priced-out row is a measured cost, counted not solved). |
| R1 | rigby LEADS + tablets directed + gate + review (`a1v22-rigby-moe-hai-off-spot-team-fe-dir-gate-rev-fe-gt730.20261007-0508`, Tolkien) | do gate + a tablet reviewer help rigby lead? | rigby's team-arm fails only (sgf-parsing, react, bowling) — ends after bowling by stop slip | R-team (10-08 01:28: 6/9 — but sgf, bowling, rest-api ENDED ON ERRORS, so VOID, not fails) | every hard row VOID (400 at the 32k context / ToolCall crash); reruns at **65,536** queued: `…-gate-rev-fe-gt730-repair-ctx65536.20261009-0005` (sgf, react, bowling; wordy 24/25 valid) and `…-team-fe-dir-gt730-repair-ctx65536.20261009-0005` (sgf, bowling, rest-api). **10-09 16:14, the owner: the gate+review repair CANCELLED by extrapolation** (gate + review = no gain on the 3.8 the same day; re-asking one layer down costs three hours-long exercises) — *"testing roles not just menu"*; **the R-team repair (rigby LEADING the tablets, 6/6 valid on 10-08) is the one live rigby row and runs now.** **10-10 04:50 DELIVERED** (`…team-fe-dir-gt730-repair-ctx65536.20261009-0005.20261010-045026`, 4/4 measured, usable, not VOID, control PASS): sgf-parsing, bowling and rest-api ALL PRICED OUT at rigby's measured 3 h ceiling (10,800 s each, 0 cap hits; partial checks 19/23, 27/31, 2/9; rest-api resumed with 33 turns kept and 7 delegations). **R-team final: 6/9 solved, the three hard misses priced out at 65k** — rigby leading the tablets reads as R2 did: not preferred on the hard end of agentic coding, with the easier families' menu standing. Last rigby row of the frozen list; rigby's lane is clear. |
| R2 | rigby solo (`a1v22-rigby-moe-hai-off-spot-gt730-ctx65536.20261009-0005`, Tolkien) | rigby's own score — the control for R-team and R1 (no solo data exists: earlier attempts measured 1–2 of 11) | original 10 | — | at 65,536: control ✓, wordy FAIL (42 min), **sgf-parsing FAIL by the 10-03 grind rule** (stopped 10-09 07:34 after 21,521 s on the one item); the other 8 continue as `…-ctx65536-rest.20261009-0740` — 14:47: react FAIL (5,009 s, after two deploy stops cost it ~2 h), bowling FAIL (1,564 s). **STOPPED 10-09 16:14 by the owner's call. Verdict, stated without collapsing (the owner 16:3x: *"rigby is not PREFERRED as an agentic lead in many circumstances but it still could be fine for easier tasks"*): on the HARD end of agentic coding (the spot/hard Polyglot set) rigby solo is DOMINATED and slow** — 0 of 4 valid (wordy 42 min, sgf 6 h, react 83 min, bowling 26 min) at hours per exercise against the 3.8's minutes, so it is not preferred there and the six exercises left could not change that. **Not a verdict on easier work:** rigby's one-shot families keep their menu standing ([[34-Rigby-Menu-Review-20261002]]: the soft price-outs are dominated, not out — fallbacks with caveats), chat-turn is its home tier (35/35 at 1.9 s), vision/camera detection runs there already (Tolkien-hosted), and as a LEAD over the tablets (R-team 6/6 valid, S10b) and as the 3.8's sub-agent (#3) it is measured in role. An easy-agentic rigby row is not on the frozen list and no Phase 7 placement is blocked without it (agentic tasks route to the 8 GB and lead tiers, both measured); if that changes, it is a backlog question, not a repair. |
| R3 | S10b, rigby writes the checks (`s10b-rigby-moe-f2tests-s1.20261006-1804`, Tolkien) | can the lead write the checks the tablets are graded by? | 24 family-2 tasks × 3 | tablets' calibration | **DONE 10-08: 73/73 measured, control passed, usable. REDUCED 10-09 12:30** (`s10b-select-rigby-moe-s1-20261009-20261009-1230.md`): **14/24 on all three replicates** = the no-selector baseline (first candidate 14/24); the hidden-check ceiling is 18/24. The lead's checks are honest but strict: false pass 4/120, 0, 0; false fail 13, 18, 9 of 120; 18–19 of 24 suites valid (the rest reject the reference). "Nothing selected" 9–10 per replicate = where rigby would answer itself (S10a's residue). Lead wall 834–1,330 s per replicate. PRELIMINARY (one lead order). |

**When it drains:** crown decision (#1, #5, #6) · lead defaults: gate (done: no — same solves at 3× cost), reviewer
(#2), delegation (#3, #4) · behaviour (#7, #8) · helper prices into `policy.json` · Phase 2 CLOSED · measurement CLOSED.
**Change log:** 10-09 22:0x — **repair: the ceiling test needs a few tokens of slack** (the owner: *"why is the bh team going
slowly?"*). #4's forth: the 3.8 with mandatory delegation FILLED its 122,880 context by turn 13 and then produced 36
more turns of `length` cuts over 97 minutes until the loop guard ended it (`no_progress`, 48 turns, 2 delegations
answered by BigHonker in 30 s each — the helper was never the cost). Every cut turn summed to 122,876–122,879, 1–4
tokens short of `num_ctx` (the turn's template tokens are in neither count), so `at_ceiling` counted none, the row was
VOIDED as "a limit we imposed" and scheduled for a second ~1.5 h run. `a1_validity.CEILING_SLACK = 16` (4× the
largest measured gap; Ornith's cuts landed exactly on 262,144); test pins the real counts. Shipped `--rigby-a1` +
`--a1` (no restarts; the running arm keeps its loaded copy, so its forth re-run still happens under the old rule —
the reducer reads the first attempt as the model's result either way). Reading for the delegation default: forced
delegation on a hard exercise can push the 3.8 into a context runaway it does not have alone (solo forth: 29 turns,
52/54).
10-09 13:15 (Fable main) — **the 10:50 repair is read back onto the attempts the earlier code
voided, by the reducer, so one rule reads every row.** Ornith's dot-dsl (22,226 + 239,918) and pov (53,372 + 208,772)
first attempts each filled exactly 262,144 and were voided + re-run under the old code; pov's first attempt had
PASSED 15/15 (the re-run failed 14/15), dot-dsl's failed 8/12 (re-run 10/12). `a1_validity.at_ceiling(turns, num_ctx)`
is now the one derivation (a1_order live; `reduce_a1.ceiling_attempts` re-reads `transcripts/<ex>.voidN.json`); the
ceiling attempt is scored as graded and the re-run discarded, named per exercise in the reduce. Effect on #5 at the
crown: pov → PASS, react → its first attempt's grade (it also filled 262,144), dot-dsl unchanged. The live order's own
early-stop count still reads its rows as recorded (react PASS from the re-run) — the stop may fire one exercise later
than the corrected count would; no cut is made on that basis. A1 suite 163; a1_validity shipped to rigby (gate ok).
The rule reads BOTH sides: the 3.8's #0 also had a ceiling attempt (robot_name filled its 122,880, passed; its re-run
passed too — no change to 8/9), proven on the delivered data by a dry run of the reduce 12:58.
10-09 10:50 — **repair: a run to the model's OWN context ceiling is scored, not void** (the owner: hitting
limits on ones the champ succeeded on is not sandbagging). Ornith's react: one call ran 42 min, 249,062 generated +
13,082 prompt = exactly its tested 262,144, then `length`; `a1_validity` used to VOID every length cut and, after a
second, drop the exercise from scoring — flattering a challenger on an exercise the champion solved in 122,880. Now:
a length cut whose call filled `num_ctx` (the plan's tested max) is the model's result; a cut below it is a limit we
imposed and stays VOID + rerun (13a). Same rule on rigby (cap hits at its tested 65,536). Settings checked first:
Ornith ran its vendor's general sampling (0.6 / 0.95 / 20; the vendor quotes benchmarks at 1.0). Ornith resumed on
the new rule 10:55 (5 kept).
10-09 08:51 — **repair: #4 could not test delegation** (the owner: *"we want the test to actually test
delegation since that's the shape of the whole lab"*). Every delivered team arm: told-to ("directed") the 3.8 made
**0** delegate calls in 10 exercises at medium (team-bi-dir 10-06), unprompted 0 in 10; forced ("mandatory") 15 in 10.
#4 re-submitted as mandatory (same plan otherwise; `repair` field in the plan); the directed order cancelled. #3
(team-ri-dir) stays directed — at xhigh it delegated on its 1 exercise so far. Safety net, both paths: the existing
`a1_validity` "measuring the lead alone" flag (a told-to-delegate arm with no delegation on its last 3 exercises) now
also STOPS the arm (`lead_alone`, one derivation for the ALERT and the stop; fleet a1_order + Tolkien).
10-08 08:20 — ORDER only (no test added): Phase 2 first (the owner: "close out the earlier phase stuff before
I get back to the later phase stuff"): #1 → #5 Ornith (priority 29.6) → #6 Nemotron (29.5) → then #2–#4, #7–#9.
Concurrency gains nothing: every test needs Mothership's card for its lead.
10-08 ~09:15 — **repair: engine/speed fair shot for the 3.8** (the owner: "check them out on all of the engines and use
whatever one was working best… At the very least… one order with the highest priority"). The engine check itself WAS
done (10-01/02, Vulkan adopted live 10-02, [[Qwen3.8-Flash-Next-on-Mothership]] § "The 27B's fast recipe"); never
measured: n-gram speculation (lever `ngram_mod`, open). (MTP depth was ALREADY closed — on ollama's own Vulkan engine
draft 2/3/4 are within noise, ~81–84 tok/s; the session first misquoted Unsloth's temp-0.6 draft-4 figure (61.5) as a
+30% lever, the owner caught it: "I'm pretty sure we already ran tests of mtp depth and tradeoffs".)
Results 08:51: n-gram ALONE = code-2 **36.4** tok/s (acceptance 0.15), edit-1 **52.7** (0.63) vs MTP ~81–84 ⇒ n-gram does
not replace MTP. The stacked arm was refused for 14 GB spill (the stopped baseline's ollama model still resident;
`answers.py` now unloads ollama first) and re-runs alone as `flashnext-q27b-ngram-stack.20261008`.
10-08 ~09:55 — **repair: rigby's lane was missing from this list** (written Mothership-only at 08:00; rigby's three orders
were approved 10-04/10-06, register rows "rigby as LEAD" and "S10b"). Added as R1–R3. R1 trimmed by the owner's per-model rule
("they need to be run on ones that the Challenger models failed"): after bowling the remaining 6 exercises all passed in
R-team (ceiling) or are unscored (paasio) — saves ~10 h of rigby time.
**VERDICT 09:00 — n-gram is not a lever for the 3.8; serving stays as is.** MTP3+ngram-mod: code-2 **75.5** tok/s
(acceptance 0.58) vs MTP3 alone 83.4 on the same prompt; edit-1 (whole-file edit, n-gram's best case) **84.6** (0.59) vs
MTP-only structured work ~82–85 (ollama live: code 81.9, JSON 84.6). Stacking adds rejected guesses and no speed. n=1
per prompt, sampled, but no cell points to a gain. Lever `ngram_mod` for the 27B: tested, no gain. Baseline #0 resumed.
10-08 ~23:45 — **repair: rigby's A1 context was an unmeasured limit that binds** (class 13a). With the 400's reason now
kept, rigby's gate+review repair showed sgf-parsing and react failing on `exceed_context_size_error` (prompt 32,854 /
32,779 vs n_ctx 32,768). 32,768 was set for the one-shot menu (largest use 5,278 tokens, 0 length hits) and carried to
A1 unmeasured. Rows that hit the cap are VOID; rows that finished under it never touched the limit and stay valid.
Done: the running repair stopped, R2 held, and three CONTEXT PROBES submitted (`submit_speed_probe.py --only=
gt730-attn-nooff --ctx=N`, N = 131,072 / 98,304 / 65,536; the KV is allocated at load, so a clean load + both rewrites
= tested). **Result 10-09 00:04: 98,304 fails to create its context on the GT 730; 65,536 loads → rigby's A1 context
is 65,536.** The arithmetic agrees: 10 full-attention layers × 2 KV heads × 256 × 2 × 2 B = 20 KiB/token, + 191 MiB
weights + buffers on a 2 GiB card → ceiling ~72–76k (≈140k with an 8-bit KV cache, held in reserve if 65k ever binds).
Reruns at 65,536 queued (R1 sgf/react/bowling, R-team sgf/bowling/rest-api, R2 solo); the 32k R2 cancelled.
Also live since 10-08: **void rows rerun once (after the engine answers) and alert** on both A1 paths (`a1_validity`, one
copy, pinned), `fleet_deploy --a1/--workspace`, floor-hook rule 5 (no hand deploys), and the ollama-watchdog no longer
kills a busy ollama (15:14 it killed the 3.8 mid-sgf on a 6 s API timeout).
Order `flashnext-q27b-ngram.20261008` (priority 40, Mothership, `answers.py` + plan `q27b-ngram.20261008.json`, ~10 min):
trimmed same hour to a QUICK settings check (the owner: "a quick small test to confirm settings… why are you including
controls when we… already have data for dozens and dozens of controls") — ONE arm, MTP3+ngram-mod, 2 prompts (code-2,
compared against thinkdraft's draft 3/4 numbers; edit-1, whole-file edit); no control arms.
SPEED ONLY — no A1 score changes; a winner changes SERVING (the owner's call), not the frozen tests. If it wins, serving through
`llama-server` must first re-prove xhigh effort and tool-call parsing there (ollama's renderer does both today).
Status: RUNNING since 08:40 — the owner wanted it now ("I'd like the answer to it now"), so #0 was stopped by slip at 08:39
(5/10 kept; rest-api, 30 min in, reruns) and requeued at priority 35, so it resumes right after, ahead of #1.
The "~69 min/exercise" read was the deck's MEAN, skewed by sgf-parsing's 180 min; real per-exercise 5–46 min ⇒ #0 ends
~11:00–11:30 after resuming.
10-08 08:02 — **owed if a challenger wins (fairness repair, not a new test):** the behaviour arms (#7, #8) exist only for
the 3.8. If Ornith or Nemotron beats the 3.8 on its set, that challenger gets the same behaviour arms before it is crowned.
If the 3.8 holds the seat, nothing is owed.

## ⚠ EFFORT SANDBAG #2 — found 2026-10-16: every Mothership "xhigh" order since its ollama 0.40.2 install (10-10) ran MEDIUM

**Measured.** #37's salted render probe on Mothership's ollama 0.40.5 (qwen3.8:27b `prompt_eval_count`): `false` 27 ·
`true` 25 · `"medium"` 25 · `"high"` 25 · `"xhigh"` 67 (+42 = the vendor's effort sentence). The foundry mapped our
label xhigh → `"high"` (`infer_ollama.OLLAMA_THINK`), which was right on 0.34.0 (where `"xhigh"` was a 400) and became
MEDIUM after the upgrade. **Confirmed on 0.40.2 from our own data:** the same C1 xhigh exam before (10-07, 0.34) and
after (10-10 `…-post040`) reads exactly **42 fewer prompt tokens on all 8 of 8 items** (`scratchpad\c1_render_check.py`).
**Fixed 14:07:** `OLLAMA_THINK = {"xhigh": "xhigh", …}` + test, `fleet_deploy --no-restart` to all three GPU factories
(hash `077c855b…` in sync; the payload loads per order, no runner holds it). The orchestrator's own copy was fixed by
#37 the same hour; the runbook line and a defect-log row were corrected by #37.
**VOID — these Mothership orders labelled xhigh ran MEDIUM** (desk records, `scratchpad\xhigh_audit.py`):
`hardbench-fleet-chat1-mo-qwen3-8-27b-xhigh-r2-…-post040` (10-10) · `cascade-lead-checks-q38-xhigh-t06` and `…-sb4`
(10-10) · `a1v22-py-agentic-hai-q38-best-thon-spot-exhigh.20261010-1913` (10-11; its `team-bi-err-exhigh` sibling was
cancelled) · `tbwin-tb-mo-qwen3…` (10-11) + three `tbwin-tb-errand-decoy*` (10-11/12, decoys) +
`tbwin-tb-mo-qwen3.8-27b-th-err-bi-qwen3.5-9b` (10-12). **Not affected:** everything before 10-10 (incl. the 10-07
xhigh A1 8/9 and the crown race), thinking-off arms, the 8 GB boxes (no qwen3.8), rigby (llama.cpp `--reasoning`).
✅ **Re-run 2026-10-17** (checklist A1–A4, each proved by +42 prompt tokens; Q6 re-read 10-23). Was owed: re-run each void cell with nothing else changed (doctrine 13a), AFTER #37 and McCoy free Mothership, and
re-read any conclusion drawn from them — chiefly the cascade lead checks and the Terminal-Bench lead-level window
([[34-Terminal-Bench-Lead-Level-Grid-Plan]]). Prove the next xhigh order by effect (+42 prompt tokens vs a medium row).

## ⚠ EFFORT-LEVEL SANDBAG — found 2026-10-07, every qwen3.8 "thinking on" result is MEDIUM effort

the owner: *"I knew it was suspicious that the thinking off was doing better than the thinking on … We were wanting to
find out on the highest effort mode."* Two causes, both on the champion, both fixed the same day:
1. **Effort.** Qwen3.8 has reasoning efforts xhigh (vendor default) / medium / low, set by one system-prompt sentence
   in its chat template. ollama's `qwen3.8` renderer maps `think: true` → **medium** (no sentence); only an unset think
   or the string `"xhigh"` renders xhigh (ollama `model/renderers/qwen38_test.go`). `infer_ollama` and the A1 path
   always sent `bool(think)`. Vendor index: xhigh 33.7 · medium 27.6 · low 26.2 · off 20.2. **Fixed:**
   `infer_ollama --think-level`, A1 `submit_a1.py --effort=xhigh`, the menu filler's `xhigh` arm (`EFFORT_LEVELS`);
   deployed to all three GPU boxes (fleet gate 452).
2. **Price lines cut the champion's thinking answers.** Mothership's per-order lines are 3–8 s on f1/f2/tool, so 18/90
   (f1), 36/216 (f2) and 34/216 (tool) of the 3.8's thinking-on answers were CUT and counted unsolved — a binding
   limit (13a) inside the "thinking on" numbers. the owner's standing rule says no ceiling may drop the champion; the filler
   now never sends it one. C1 and W1 had no cuts.
**What the old rows are:** medium-effort data with cuts on f1/f2/tool — kept, labelled `on`, never read as the 3.8's
thinking ceiling. **Redone at xhigh (2026-10-07 ~11:10):** the 24 menu cells (6 families × temps 0.0/0.6/1.0 + a
0.6 replicate) and the seven A1 arms that were queued at medium (solo, gate, gate+review, team-ri-dir, three
behaviour arms — the medium copies cancelled by slip). **Wire value:** ollama **0.34.0** (installed) accepts only
`"high"`/`"medium"`/`"low"`/`"max"`/bool — `"xhigh"` is a 400 — and its qwen3.8 renderer renders `"high"` as the
vendor's xhigh; an UNSET think is turned into `true` (medium) by the ChatHandler before rendering. So the lab label
is `xhigh` and the payloads send `"high"` (`infer_ollama.OLLAMA_THINK`, `a1_ollama`); deployed 11:19 on all three
boxes before any xhigh order ran. **Live proof (2026-10-07):** the xhigh solo's first turn on `wordy` read 2,066
prompt tokens vs 2,028 for the medium solo on the same exercise — **+38, the effort sentence** (instrument control:
medium = off − 2 on all 10 exercises). xhigh renders on the installed 0.34.0.
**Lab-wide audit (2026-10-07, `hard-bench\effort_audit_20261007.md`, 26 of 26 models):** only the **qwen3.8 renderer
family** (qwen3.8:27b, its mtp tag, flash-next — the latter two held) has effort levels; their `on` = MEDIUM, 1,062
standings rows in 12 arms (+72 mtp f2). Every other model's `on` is already its maximum (qwen3/3.5/3.6/vl, Nemotron
Nano 4B/30B, Ornith, rigby's Qwen3.6 GGUF via `--reasoning on`); Qwen2.5/Coder cannot think. Open: tablet
Qwen3.5-0.8B `on` (16 rows, no `-rea` flag, non-thinking by default — UNVERIFIED that it thought); rigby's 2
`default` rows (template default, UNVERIFIED); qwen3-vl:8b `off` is not off (already treated void). HAI production
sends `think: false` to the 3.8 (genuinely off — a design question for the agent surface, not a measurement fault).

## CHAT1 — the adjusted chat arm (HAI-Agent-Surface-Design measurement 3) — BUILT + RUNNING 2026-10-07

the owner: *"let's build the adjusted chat arms"* / *"nobody wants to wait 1000 seconds after sending a simple chat."* C1's
2,000-word documents priced the small tier out; CHAT1 prices the turn the tier exists for. Pool `hard_tasks_chat1.py` =
HAI's OWN 7 requests the advisor labels `fellowship` tier ("hows your day going", "whats the capital of new jersey",
PONG, …), verbatim, nothing appended; the reply is the graded data (`REPLY = …`): non-empty + no leaked thinking on
all, Trenton / exact PONG / one short sentence where objective; open ones read by eye. Payloads via
`gen_hard_payload.py --pool chat1` (generic + the four tablet models at C1's setting; the control keeps its own fence
instruction — the first generation lost it with the emptied suffix, caught before grading, all 13 orders withdrawn and
resubmitted). Orders: tablets (`hard_bench_calibrate.py submit --lane chat1-<m> --pull`, 4 models × 35 turns), rigby
(`inspect_lab\submit_chat1_rigby.py`: MoE GT 730 thinking-off, Qwen2.5-7B, 1.5B; priority 50), 8 GB boxes + the 3.8
(off and xhigh, priority 26) via `hard_bench_fleet_orders.py --pool chat1` (own campaign `chat1-<date>` so the menu
never reads it as family 1). First results: BigHonker qwen3:8b off **21/21, median 0.4 s/turn**, max 9.5 s;
qwen3.5:9b off 21/21, median 1.1 s. **Reduce BUILT** (`reduce_chat1.py` → `Project34-Menu\chat1.md/.csv` + 
`chat1_standings.csv`, tablets per CLASS, results count only when the control passed) and **folded into
`menu_policy.py`** as family `chat-turn` (12 tests). Second instrument bug, caught by the 0-solved tablet rows: the
chat extract was appended AFTER the payload's `__main__` guard and the tablets RUN the payload as a script, so every
tablet reply was graded as Python — fixed (def before the guard, asserted), tablets resubmitted as `-r3`; the reduce
reads only `-r3` tablet results. Rigby's S10 team arm stopped 14:56 to front-load the three short chat orders
(requeued after; resumes from its checkpoint). Tablet wall = cold turn (model load per item).

## PHASE 7 — STARTED 2026-10-07 ([[34-Phase-7-Placement-Policy-Design]])

the owner: *"every morning … you always tell me we're a 'day away' from wrapping up this section … and getting properly
into phase 7."* The charter never made Phase 7 wait for A1 (A1 FEEDS it); it had no design doc, so it never started.
Now: the design + build steps exist; **the measurement list below is CLOSED to new arms** (queued arms finish and fold
in; a new arm only when Phase 7 names a decision it cannot make without it). **Step 1 BUILT 10-07:** `menu_policy.py`
→ `policy.json` (triage tiers × time / energy / quality lenses; acceptable = the stop rule; Mothership = the resident
lead; chat-turn family from CHAT1). **Next:** step 2 (replay `State\placements\` through the table), then step 3 (Spock
reads it, fail-open; orchestrator deploy) — Claude.

## A1 / pre-Phase 7 measurement — status checked 2026-10-07 08:15

**Re-checked 2026-10-09 20:3x against the live queues (the owner: *"make sure our pre-phase seven is done"*).** The
pre-Phase 7 list (the Phase 7 design's "measurement section", closed to new arms 10-07) resolves as: leads solo —
DONE, crown to the 3.8 (Phase 2 CLOSED); replicates of solo + gate — CANCELLED 10-08 (saturated set); Aider
comparison — CANCELLED 10-07; finish gate — DONE, default NO; A1-review — DONE, default NO; A1-team pricing — the
fixed-helper arms are the Phase 7 rows now running (#4 BigHonker, then #3 rigby); behaviour arms — #7/#8 queued;
rigby as LEAD — solo STOPPED (dominated on hard agentic coding), gate+review CANCELLED, R-team repair DONE 10-10
04:50 (6/9; the three hard misses priced out at the 3 h ceiling — every rigby row is done); rigby 14B — DROPPED; S10b — REDUCED 10-09; delegation-on-direction — DECIDED; #35 corpus — rides along;
CHAT1 incl. the warm-tablet arms — delivered 10-07 and folded into `chat1.md` / `policy.json` 18:37–18:46.
**Six languages + Terminal-Bench — the one approved item never started**: not on the 10-08 FINAL TEST LIST, so the
freeze parks it (a new question for the next model generation, PROJECT-BACKLOG) unless the owner re-opens it. So
pre-Phase 7 is DONE: the FINAL TEST LIST drained 10-10 14:08 (#7, #8, #9 done; #3 and #4 ended 10-10); every rigby
row is done (10-10 04:50); nothing else is owed before building. The table below is the 10-07 snapshot it supersedes.

| item | approved | status | next step / owner |
|---|---|---|---|
| Leads solo at best settings: the 3.8 (on/off), Ornith, Nemotron-30 | 10-04 decision 3 | 3.8 thinking-on was MEDIUM effort (6/10) → **xhigh solo** 8/9 so far (re-running the 2 freeze-voided exercises); thinking-off spot delivered 10-07 07:52; Ornith + Nemotron-30 QUEUED behind the xhigh arms (their `on` is already max effort — audit) | reduce on delivery — Claude |
| Replicates of solo + gate (ladder is n=1) | the owner pass, 10-07 | QUEUED (priority 9) | Claude |
| Published Aider comparison for the 3.8; the minimal-tools control | 10-05 | **CANCELLED 10-07** by slip — the owner: *"Remember we're testing for roles, not just to fill menu."* Neither changes which box/model does which job (external comparability; a tools control production never uses). The Mothership→Sundae ssh question it raised is moot. | none |
| One layer at a time for `team-ri-dir` | 10-05 | the medium-effort `team-ri-dir` was stopped 11:07 and cancelled (effort sandbag); `…-team-ri-dir-exhigh.20261007-1110` QUEUED (priority 23). rigby's directed arm and S10b requeued, gate+review and the solo control released 11:40 (rigby was idle for an arm many hours away; checkpoints kept) | BUILT 10-07: the team run carries its own rigby lane RESERVATION (tablet_foundry `*.lane.json` reserve/release; a1_delegate.RigbyDelegator.reserve/close; Tolkien gate 579) - live decoy passed and found a requeue race, fixed (requeue only once the stopped order has retired) |
| Finish gate | 10-06 21:07 plan item 1 | BUILT, 7/10 (n=1) | replicate queued |
| A1-review (different model reviews diff + test output) | 10-06 plan item 2 | BUILT, 8/10 (n=1) — **but the review measured nothing**: audited 10-07, the reviewer (BigHonker qwen3:8b) received the package (1.3k–8.7k prompt tokens, inside its 17,408 ctx) and answered `NO DEFECTS` in exactly 5 tokens with zero thinking on all 10, including paasio and wordy which FAILED hidden tests. Cause: the settings resolver picked the reviewer's settings from one-shot CODING (`helper_think: false`), the wrong task type for reviewing. The 8/10 is the finish gate plus a rubber stamp. | Re-run QUEUED 10-07: `a1v22-py-agentic-hai-q38-best-thon-spot-gate-rev-bi-rthon.20261007-0811` (reviewer thinking ON via new `submit_a1.py --review-think=on`). Still owed: price other peers ("which peer helps most"); a review task type in the resolver — Claude |
| A1-team (organic / directed / mandatory / window) | 10-04, 10-06 13:04 | BUILT, priced (5/7/6 of 10; window cut lead wait 48 → 16 s) | rung 4 design from the delegation corpus |
| Behaviour arms (unverified success, failure recovery, taking a correction, plan respect) | spec 10-04; "cheapest high-value next build" 10-05 20:17; "build everything" 10-06 | **BUILT 10-07** (harness `225002c7`, 3 GPU boxes; unverified success recorded on every run); arms QUEUED `…-b{recover,correct,plan}.20261007-0836` | reduce on delivery — Claude |
| The 3.8 delegating to rigby as a sub-agent ("sub-agent to the 3.8") | decision 3a, 10-04; 10-06 12:25 plan | **BUILT 10-07**: `a1_delegate.RigbyDelegator` — each piece is a Tolkien `llm` order placed through KIRK'S DESK by capability (`only: fellowship`, `requires: rigby.llm` + `rigby.gt730`, both DETECTED by Tolkien), `priority` 100, delivered to a stated path; `submit_a1.py --team=rigby:<MoE gguf>`. Tolkien gate 551, A1 suite 124 OK; live decoy passed (desk → Tolkien → rigby GT 730 → delivery → a correct answer, control passed, 18 s). Arm QUEUED `a1v22-py-agentic-hai-q38-best-thon-spot-team-ri-dir.20261007-0859` (priority 9.5). One layer at a time: when it starts, rigby's running arm is stopped and its queue held until it ends (then requeued — resumes from checkpoints). | Claude (watcher armed on its start) |
| rigby as LEAD: MoE solo + S10c (leads tablets) + gate+review (tablet reviewer) | 10-04, 10-06 | RUNNING on the GT 730 placement (`…-gt730.20261007-0508`); tablet fix `e3b1717d` unproven live until a delegation lands | Claude |
| rigby 14B | 10-06 13:04 "worth it?" | DROPPED (priced out by its menu review) | — |
| S10b (the lead writes the checks) | 10-06 plan item 3 | BUILT, queued on rigby (CPU) | runs |
| Delegation on direction, not instinct (the desk/advisor decides the split) | 10-06 21:14 (single input, user never asks) | DECIDED, recorded in the spec | feeds Phase 7/8 |
| #35 corpus (transcripts as lab experience) | 10-05 23:33 | delegation corpus + transcripts recorded | rides along |
| Six languages + Terminal-Bench | 10-04 decisions 2/4 | NOT STARTED (toolchain inventory first) | after Phase 1 Python closes — Claude proposes timing |
| Rigby's wordy pass (the 3.8 failed it in all 7 MEDIUM arms; passed it at xhigh 10-07) | — | **VERIFIED same grader 10-07:** rigby's Inspect path calls `a1_harness.run_task`; its copy (`payloads\a1\a1_harness.py` 321b1d71) differs from the GPU v2.2 (225002c7) only in today's behaviour-arm code — `grade` / `grade_detail` / `_grade_run` are byte-identical (AST diff) | — |

## Phase 8 advisor (Softserve) — status

| item | approved | status | next |
|---|---|---|---|
| Advisor v1: 48 labelled HAI requests, Nemotron 4B + MiniCPM5-1B, thinking off, seconds | 10-06 12:57 | MEASURED (triage + Plex footprint; qwen3:8b 31/48 tier) | — |
| Advisor v2 TRIAGE: the owner's own requests from the transcripts | 10-06 12:57 → the owner "makes sense" 13:04 | **DELIVERED 10-07 09:03, REDUCED 10-09 20:35** (`submit_advisor.py reduce-plex --set v2`, idle pass, ctx 2048): tier right on the owner's 36 requests — qwen3.5:9b **25/36** (median 0.9 s, max 2.8 s; VRAM peak 6.5 GB leaves 1.7 GB — fails the Plex MAX band), qwen3:8b **23/36** (0.5 s; peak 5.1 GB, fits every Plex band), Nemotron 4B 14/36 (0.6 s), MiniCPM5-1B 11/36 (0.4 s). Reading for McCoy: qwen3:8b is the only candidate that is both near the top on triage and inside every Plex headroom band; the 9b buys +2 for a headroom it does not have. PRELIMINARY (one run). **BUILT + SUBMITTED 10-07**: `advisor_tasks.TASKS_V2` (36 standalone requests 10-03..10-07, names/personal content out; adds the STATUS asks whose right route is a deck read — tier tool); `submit_advisor.py plex --set v2` (own campaign `advisor-20261007-v2`, v1 untouched) → `advisor-v2-ss-fit-ctx2048.20261007-090250` on Softserve, the four candidates at the request-sized context | reduce on delivery (`reduce-plex --set v2`) — Claude |
| Advisor DECISION MODELS (ollama 0.40.5 `/v1/systemone`, tev1:0.8b + tev1:latest): same task sets, one variable = the interface (`advisor_systemone.py`) | the owner "Let's work baby" 10-15 | **REDUCED 2026-10-16 00:2x** (`reduce-systemone`; orders `advisor-v{1,2}-ss-systemone.20261016-00073x`, run beside a live Plex transcode via `coexists_with_plex` + priority 90). Tier right — **v2 (the owner's 36): tev1:latest 20/36, tev1:0.8b 18/36** vs qwen3:8b 23/36 · **v1 (48): tev1:latest 34/48, tev1:0.8b 14/48** vs qwen3:8b 31/48. Under-routed 0 for every cell. Warm median 0.18 s (latest) / 0.08 s (0.8b); cold first call 26.4 s / 6.5 s; VRAM peak 5.9 GB (leaves 2.3 GB, every Plex band ok) / 1.7 GB. Reading: no candidate beats qwen3:8b on the owner's own requests; tev1:latest wins the v1 set by 3 — mixed, so qwen3:8b stays McCoy's triage model. ⚠ The decision models' timing was taken under a transcode, qwen3:8b's idle; the tier counts are unaffected (deterministic choices). One run each (the choices are deterministic, repeats add nothing). **CONFIDENCE IS THE VALUE, NOT ACCURACY (10-16, read-only, `hard-bench\systemone_thresholds.py`; the owner's design "hardcoded with LLM assist" — the model proposes, code decides; local only, Chatterbox/video session 10-16):** tev1:latest's tier confidence separates right from wrong (median 0.91 right vs 0.45 wrong); a threshold set on v1 (≥ 0.90: 9/9 right) **held out-of-set on the owner's 36: 10/10 right**, deciding ~19–28 % of requests itself with 0 under-routes; the rest go up as today. qwen3:8b's own `sure` field does not separate on v1 (65 % vs 64 %) and only partly on v2 (91 % on 11 rows vs 52 %). A cascade (tev1 when sure, else qwen3:8b) adds only +1/+2 over qwen3:8b alone — noise. The 0.8B's confidence is weaker (88.9 % at ≥ 0.95). Candidate rule for McCoy: tev1:latest ≥ 0.90 decides, else the request escalates. **Nimble 9B (`nimble:9b-q4_K_M`, 5.63 GB — the biggest quant that fits 8 GB; the owner "Yep download"; orders `advisor-v{1,2}-ss-systemone.20261016-091539`): OUT.** v1 31/48 (= qwen3:8b), but on the owner's own 36 only **15/36 with 12 under-routes** — the costly mistake; its confidence does not transfer (≥ 0.80: 95.5 % on v1 → 50 % with 2 under-routes on v2); warm 0.31 s, peak 5.9 GB (every Plex band ok). **tev1:latest stays the candidate.** Lab-trained decision model: not yet — 84 labelled triage rows (48 + 36); the 314 placement records are the rule's own choices, not labels. | — Claude |
| Advisor v2 PLACEMENT: cases Spock cannot settle | same | **RECORD BUILT 10-07** (the history the design doc assumed did not exist — class 17): `scheduler.record_placement` writes every `place()` decision with its job shape, every candidate + its reason, and the choice to `State\placements\YYYY-MM.jsonl` (fail-soft; 3 tests; orchestrator gate 2562; live decoy wrote a real row). Mirror commits `28e7eb0` (+ `09487b0`, the 10-06 desk cache committed after the fact). | **MINED 2026-10-15 — nothing for an advisor on current traffic** (`hard-bench\placement_mine.py`, 259 recorded decisions 10-07..10-15): 24 had ≥ 2 candidates; the only close ones (6, one cascade burst 10-10 16:31) were decided by `policy.json`'s rank, not a score tie — 0 real ties; 106 'no host' = 85 pinned to one busy box (76 Mothership renders) + 21 one hourly 16 GB `review` job with no free 24 GB card — waits, not judgment. Revisit when the traffic shape changes (multi-user HAI agent surface) — Claude |
| Advisor v2 DECOMPOSITION: from A1-team transcripts | same | WAITS ON DATA: the delegation corpus is 18 pieces (10-07); the rigby team arm and replicates queued today grow it | after the team arms deliver — Claude |

## HAI agent surface / work-loop redesign — status (→ project #37 since 2026-10-16)

**2026-10-16: registered as child project #37** ([[PROJECT-REGISTRY]]; [[HAI-Agent-Surface-Design]] is its charter).
#37 owns build steps 1, 1a, 2, 3, 6; #34 Phase 8 still owes steps 4 (triage, McCoy) and 5 (placement advice, Spock)
as inputs. The rows below stay as the design history; new #37 status lives in the design doc.

- ✅ **AREA C STEP 1 — SPOCK WEIGHS A McCOY-DECIDED CHAT TURN PER REQUEST FROM THE PRICED MENU — BUILT, GATED AND LIVE
  2026-10-22 22:38 CT** (Fable, main session since this evening; design approved by the owner ~21:50 — *"let's hit it"*;
  mirror `86e7faa` + `bcace96`, gate **3040/2**, orchestrator recreated 22:38 CT, container hashes = mirror; Tolkien
  image rebuilt via `fleet_deploy scheduler.py`). Design: [[34-Phase-7-Placement-Policy-Design]] § "Area C step 1".
  **What runs:** `mccoy.verdict` hands Spock the tier (the only measured line), the kind and the confidences;
  `scheduler.auto_needs` turns the TIER into the menu family (fellowship → chat-turn, 8gb / lead → chat-c1, tool →
  tool on the 8 GB rows) and a sure `job` into background; `scheduler.place(urgency=)` / `place_weighed` orders the
  hosts the hard rules passed by the WEIGHING — the quality need (the tier's best HOME row's lower bound) first, then
  rows inside the family's interactive line, home before overflow, cheapest energy, fastest — and the placement record
  carries every row's quality / time / J, band and eligibility; `app._placed_brain` serves the turn on the first placed
  box that is reachable and free for THAT ROW's model, at the row's model, thinking (`placed_effort`: a row priced off
  is sent off) and measured window; a Fellowship pick is recorded and passed over until the Fellowship chat form
  exists; nothing servable = the cascade, logged. The lead lane is unchanged (the chat-c1 lead band IS the 3.8); a held
  lead still overflows by B4b's live rule until step 4's queue + notice. The lens path stays for the placed delegate's
  pieces. **Step 0 (read-only replay, `hard-bench\weighing_replay.py` + `mccoy_family_score.py` → `Project34-Menu\
  weighing_replay.md` / `mccoy_family_score.md`) reshaped the design three times before code:** McCoy's family →
  menu family REJECTED (63 % at the line: the menu's families are SHAPES, his are TOPICS); overflow rows are fallbacks
  inside the quality band, not peers; the quality need binds before the line (a cold lead past the line still wins
  over a lower tier). **Live proof 22:39 CT (ClaudeSupervisor chat `bf37e2e94ed6`, "17 times 23?"):** McCoy sure
  `fellowship` (0.93) → `weighed placement chat-turn/fellowship/interactive -> bighonker qwen3:8b think=off (q 1.00,
  11.1 s, 208 J)` → `PLACED EFFORT … think=False (the menu row: off)` → a NEW window for qwen3:8b on BigHonker (not
  the box's standing 9b) → "17 times 23 is 391." in 39.5 s; the record lists the tablets' 0.8B row as the band's home
  (past the 17 s line, J unmetered, not eligible — no chat form), Softserve and Mothership as cold overflow past the
  line, the Coder-0.5B tablet row below the band. **Found by the replay, fixed as its own step (22:4x):** rigby's
  simple-chat rows (CHAT1, Qwen2.5-1.5B 35/35 at 1.9 s) were REFUSED from the table by the owner's 2026-10-04 rigby hold
  although its text says *"Lane held; delivered rows stay on the menu"* — the hold lacked the `orders_only` flag
  `menu_fill` already defines for exactly that; flagged, table regenerated. **✅ Built 2026-10-23 (design § 6):** step 3 the
  Fellowship chat form (Tolkien, one warm-node turn streamed back) + ollama windows ending at the engine's idle rule
  (E2); step 4 the controls (D1–D6: busy pin → queue + notice + ETA + reroute offer; the 27B option for all; Nursing
  shows the boxes); the pieces' lens path onto the weighing, one caller at a time; McCoy to `*` once the shadow log
  agrees with the served route. **the owner's QUESTION the same night (23:39 CT: *"is it weighing them as a matrix or…like sequential gates?"*) and MY
  answer, which an earlier edit here filed as his read-back — corrected 2026-10-24:** the built resolution step is an
  ordered choice — quality need → inside the line → home → energy → time, each threshold measured (Wilson bound, the
  10× line), the menu's three measured prices per row, the request's needs as the thresholds, no invented exchange rate
  between seconds and joules. He never ruled a matrix out. the owner's intended shape, in his words (2026-10-24 ~08:3x CT): **two layers.** *"from the VERY BEGINNING I've been describing it as a layered system"* — first Starfleet weighs the REQUEST (*"is it simple? Does it require a hard model? Does it come from a priority user? Does the request/chat itself require speed over depth…? What tools does it require"*), then takes that *"against the menu and availability and makes the choice according to the matrix"* of cost (literal electricity, incl. price spikes), speed and quality — *"Every 'request'/job/whatever gets weighted differently according to user"* (his overrides queue him ahead), a peak-price *"quick dump… and then quick resume"*, the Fellowship for *"extremely reduced prices"*. Layer two is BUILT (the weighing reads all three prices of every row per request; peak → energy first; extreme → hold with a notice; pins and his override pass). Layer one is McCoy's triage (tier / family / kind at the 0.90 line) — it does not yet ask priority user, speed-vs-depth as its own need, or tools. NOT BUILT: per-user weighting (the queue is quick-first, then first-come), a price-triggered dump-and-resume of in-flight work (the slot's in-memory pause is the mechanism), layer one's three unasked needs. Owner: **#34 area C step 3 — **BUILT + LIVE 2026-10-24 ~09:36 CT** (orchestrator gate **3135** at the pin, container `14:35:40Z`, bytes verified; Tolkien rebuilt on the same `scheduler.py`, gate **959**, three containers `14:37:11Z`; mirror audit clean before)** (design § 6 step 8; #35 trains judgment on what it logs; per-user weighting was already live as B4a). Cost = electricity (joules per
  solve; the dollar figure is not in the record yet). **✅ AREA C STEP 2 — THE PRICE OF THE HOUR (the owner 2026-10-22
  ~23:00 CT; BUILT 2026-10-23 09:06 CT, `a89ad3a`):** placeholder price when the pipeline is broken (*"we could even just say $0.11 /kwhr since I
  think that's the average"*, or extrapolated from the home's past prices); *"if it's during peak pricing hours I don't
  want people to be able to have my lab running full tilt, I want everything to be slow and low on energy if we're in
  high prices. And heck maybe disabled if we have a crazy peak like a couple days ago when it was $1.00 PER KWHR"* —
  design § "Area C step 1" build step 6. **⏳ OVERNIGHT 2026-10-22 → 23 (queued 23:53 CT):** 11 cold salted
  long-read speed checks (`speed-20261022`, tag `readrate`) for the GPU rows with no `read_tok_s` — the weighing's
  re-read / first-visible-output input — and CHAT1 seed 2 on BigHonker (`chat1-20261022`, `-r2` tags) to lift
  "preliminary" from the chat-turn rows it serves; both fold into the table at the 15-min refresh. Read back: journal
  10-22 § "23:53". **✅ MORNING 10-23 07:45 CT:** 12 of 13 done; read rates on the table for 8 of the 11 pairs
  (BigHonker qwen3-vl:8b @17408 no longer FITS before load — cancelled, a one-model fit probe queued; the two
  Mothership pairs have no tested context); CHAT1 seed 2 folded — BigHonker chat-turn rows `holds` (n=63, qwen3:8b off
  0.943 low at 1.69 s) after fixing `reduce_chat1` (hard-coded PRELIMINARY; not in the refresh chain — both class 17,
  fixed + pinned). BigHonker rebooted 03:31 by Windows Update (26H2) — #38's case; no order lost. Journal 10-23.

- ✅ **CHAT PLACEMENT FROM THE MENU — BUILT 2026-10-22 (three lenses weighed together, `place_weighed`; the earlier single-lens attempt was deployed and ROLLED BACK 2026-10-22
  ~16:50 CT, revert `f8dbb08`). READ [[34-Starfleet-Routing-and-HAI-Controls-Do-Not-Collapse-Checklist]] BEFORE
  ANY WORK HERE.** It sorted the 8 GB rows by one invented fourth lens, typed every turn as chat-c1
  and folded the Fellowship tier into the 8 GB boxes - a fixed formula standing in for the matrix. the owner: Starfleet
  weighs speed, cost and quality TOGETHER per request, with McCoy advising both triage and placement; the user never
  classifies a request, nobody enumerates request types. Definitions (the owner 10-22, now in
  [[34-Phase-7-Placement-Policy-Design]] step 1): SPEED = request in -> answer on screen AND time to first visible
  output (streamed thinking counts); COST = electricity (J/solve); QUALITY = solve-rate lower bound. Kept from the
  attempt: `menu_policy.main()` now runs end to end in a test. Still a finding to check against the window design: an
  idle ollama chat window keeps its card claim after the model unloads (Strata windows end at the idle rule). The
  record of the rolled-back build: 34-register-history-2026-10.
- ✅ **EVERY USER, THE SAME CONTROLS — recorded 2026-10-22, BUILT 2026-10-23** (journal 10-23 § A4: `MOTHERSHIP_FORCE_USERS` retired, busy pin queues with a notice, Nursing no longer hides the controls). the owner: every user has the same option boxes
  (Auto the default and it must just work; the pin is everyone's); a pinned request for a busy machine goes in as a
  QUEUED order and the user gets a notice — *"machine is busy … queued"* — with the offer to reroute to another machine
  and its caveat (B4b's overflow, offered to the user instead of taken silently); the owner keeps an owner override on his
  own machinery. Two HAI 1.0 holdovers contradict it today: the 27B choice is shown to and honoured for
  `MOTHERSHIP_FORCE_USERS` only, and the Nursing persona hides Model and Effort. NOT a new idea: the owner's 2026-06-28
  Cluster B already asked to offer a busy-Mothership user the queue plus a rough ETA (B4b kept only the silent-overflow
  half). Full wording, the reason the 1.0 gate existed and the lineage: [[HAI-Agent-Surface-Design]] § "EVERY USER, THE
  SAME CONTROLS".
- ✅ **CHAT AS A FACTORY ORDER — LIVE FOR EVERYONE 2026-10-22 ~12:28 CT (`HAI_CHAT_WINDOWS=*`, override backup
  `docker-compose.override.yml.bak-20261022-chatwin-all`).** Gate: the fixed build's re-proof as ClaudeSupervisor 6/6
  answered (one Mothership 3.8 window served every lead turn — cold first turn ~100 s incl. window open + load, warm
  ~20 s, tool call, a ten-bullet plan, Stop, and McCoy's below-lead turn on a BigHonker window); then the `test` user,
  never on the named list, answered through a new Softserve window (right after a restart the previous process's windows
  hold their boxes until their owner beat lapses, ~45 s — the documented owner rule). ✅ **B4b OVERFLOW PROVEN LIVE
  12:34 CT** (decoy: Mothership's telemetry logger stopped so its load report went stale — the hold McCoy's claim check
  cannot see): McCoy → lead on Mothership → desk HELD the window (`no fresh card-memory report`) → ONE attempt, no CTX
  DOWNGRADE → `McCoy: lead -> 8 GB tier (Mothership could not take it…)` → a BigHonker window → answered (a ten-point
  plan, ~70 s). ✅ **The 'would WAIT behind another run' half PROVEN 13:52 CT** (`e5da330`): two ClaudeSupervisor chats,
  A a long job on the Mothership lead window; B, asked while A ran, logged `lead -> 8 GB tier (Mothership held …)` and
  answered on BigHonker in 41 s with A still running. The first try (`c771fb4`) counted only a GENERATING turn: A was
  running six tools between turns, B went to Mothership and waited ~5.5 min — fixed so a run HOLDING the window is busy
  until it ends. ⚠ Seen in the same proof: the overflow's legacy 8 GB ladder (`pick_brain`) opened a Mothership
  `qwen3:14b` window ("small brain" fallback) — overflow landing back on the held box; menu routing replaces that ladder.
  History below. A model
  turn runs through a factory WINDOW order (`chat_window.py` + `helper_window.py`, one desk order per host/model/window,
  priority 100, rush) behind `HAI_CHAT_WINDOWS` (live value since 2026-10-22 ~12:28 CT: `*`, every user). Shipped 10-21 with
  six live proofs, an ABA (window 9.2 s vs direct 16.3 s — cause: embed eviction, fixed), Strata/Flash-Next windows and
  folder retirement — record: [[34-CHAT-FACTORY-CONTRACT-AUDIT]]. The 10-20 per-turn staged design
  (`.p34-chat-stage`) is superseded by windows (old line: 34-register-history-2026-10). **2026-10-22 re-proof on
  today's builds** (lab engine everywhere, slot save, McCoy lends): tool call, Stop at delta 12, warm reuse through a
  BigHonker window — and two turns ENDED UNANSWERED: Mothership's window was HELD (`model fit: no fresh card-memory
  report` — its telemetry logger had died at the AMD driver reset, now restart-policied) and Kirk (a) treated the hold
  as an OOM ("card-**memory**" matched `_CTX_SHRINK_ERROR`), re-asking the same box at 62,976 and 8,192, then (b)
  returned an error instead of B4b's overflow. Fixed (`ef0a3f7`): a held/refused window raises `WindowUnavailable`,
  asked once, `ChatError.unavailable`; McCoy's lead overflows to the 8 GB tier on it. ✅ **Widened to `*` 2026-10-23** after the re-proof (journal 10-23). Was owed before `*`: the re-proof
  green on the fixed build; the direct consumers are settled (embeds ask `occupied_for` by design; McCoy is placed by
  Spock with lends; `ollama_proxy` served Frigate, retired 08-28, 0 calls in a week).
- 🔧 **MENU CELL: the resume / reload price (the owner 10-19, "I don't know if we necessarily priced that into the menu").**
  The time a turn takes to get back to where it was — cache-served reload, checkpoint resume, dump restore — per
  model and box, so placement weighs it (a warm box beats a faster cold one by exactly this number).
  **BUILT 2026-10-21 ~09:35 CT (cold reload = load + re-read):** `menu_policy.read_prices` stamps every `policy.json` row
  with `read_tok_s` / `read_n` — the median rate of delivered rows that READ ≥ 2,000 tokens with the engine's own
  `prompt_eval_duration` (ollama counts evaluated tokens only, so a cache-served row cannot pass); unmeasured = None,
  never zero — beside the existing `load_s`. Positive control: Flash-Next 1,120 tok/s from deliveries vs the runbook's
  independent 1,123–1,154. Live table 09:32 (`menu_refresh --force`): 452 rows, 85 measured (3.8 on Mothership 694 ·
  qwen3.5:9b / qwen3:8b on BigHonker ~2,850 · A3B 1,486 · Flash-Next 1,120). `scheduler.residency_cost` charges a COLD
  box `load_s + context_tokens / read_tok_s` on the fastest lens (`place(..., context_tokens=)`); a warm box pays 0.
  Long-read orders `speedcheck-*-longread.20261021-093005` fill the missing chat pairs (Softserve 8b/9b, Mothership 14b).
  ✅ chat passes `context_tokens` (`app.py` → `place_weighed`, 10-22). A dump-restore price beside cold reload is NOT in the table: the saved slot belongs to the box that saved it, so it only lowers a same-box cold cost — a refinement → PROJECT-BACKLOG § "#34 LEFTOVERS".

**#37 COMPLETED 2026-10-17 ~00:50 CT ([[37-Closeout]]; reopened twice — for its phone layout, then for the clock that killed its live proof — both closed; the owner's phone check of the new Effort / Work controls pending). Hand-offs to #34 (each owned here now):**
- **McCoy's wiring job proved the FINISH GATE and in-loop COMPACTION live — DONE 2026-10-17** (HAI run `b6b1b3a3…`): compaction at 90 % of the window and carried on; the finish gate passed honestly (last edit 00:07, unit tests green 00:08, checkpoint re-run 00:31 CT). Detail: [[37-Closeout]] § "Reopened 2026-10-17 night".
- **Think-hard's critic = the lead itself, the same 3.8** (the owner 10-16: *"agreed"*; `config._THINK_HARD_POOL_DEFAULT
  = HEAVY_MODEL`, deployed 10-17 ~00:3xZ) — no second model on Mothership's card, so no reload per critique. **Owed
  to Spock (later): a critic on ANOTHER box** chosen from the priced menu — where a critic runs is placement, not a
  constant. (Superseded text: 34-register-history-2026-10.)
- **Peer review on coding stays OFF; the A1 arm with OTHER reviewers RAN as B5 (2026-10-19): no gain, decided NO** (the owner 10-16 *"agreed"* to running it; B5 above). The 10-08
  reviewer arm measured one reviewer shape (no gain); the owed arm varies only the reviewer (a different model / box
  as the critic of the lead's patch, everything else as the 10-08 arm). Queue it with the A1 fills, not before McCoy
  frees Mothership.
- **Placement fix found by #37's live tile proof (Spock's area, done the same pass, 2026-10-17):** HAI's per-query
  embeds went to BigHonker by a static list and broke a speed-check order mid-reload (refused at 0.0 GB resident).
  Every Kirk-side call that reaches a box by URL now asks `scheduler.claim_for_url` — embeds, chat auto-titles,
  genetics (gate 2764). For Spock: Kirk's own transient calls now honour claims; any new one must too.
  **Chat's own fallbacks now honour claims — DONE 2026-10-17 (checklist B6, `3c09e70`):** Mothership's small brain, its wake, the recovery list and Softserve each ask `claim_for_url` before a chat loads a model.
- **Multi-user placement → Spock (Phase 7), checklist B4:** ✅ **B4a "the owner first" LIVE** (`3c09e70`) — Mothership's slot queues quick before jobs and, within each, `PRIORITY_USERS` (default `the owner`) first; never preempts a running job. ✅ (10-23: live since 10-22, decoy PASS 10-23) **B4b overflow** (an Auto request that would wait behind another user goes to Spock's next box with its caveat) needs Auto's tier from McCoy's routing, so it follows McCoy going live.
- **KV dump / quick resume → B11 + B11b (above) since 2026-10-21:** rigby lossless mid-answer (B11, closed); the three
  GPU boxes save/restore the read-in state between turns (B11b stage 1; stage 2 = engine parity, owed). Where it is NOT
  yet used, ranked with measured costs: `Claude Home\research\quick-resume-survey-20261021.md` (13 candidates; top:
  idle-unload of a chat window saves nothing, a review evicting a job's 3.8, McCoy's cold load, stop/yield on the GPU
  factories). The 10-16 entry this replaces is in `Archive\34-register-history-2026-10.md`.
- **Decomposition of a job into orders (charter WORK item 4) → #34's S8 shape for Kirk/McCoy** ([[34-CHARTER]]:
  "takes the job · decomposes (S8)"); decomposed MODE stays retired (10-05).
- **Interim rules McCoy/Spock replace:** (a) job-or-chat decides effort (`app._is_job`, from the request shape);
  (b) quick-first on Mothership's slot. Both labelled interim in the design doc.
- **Lend the GPU during tool waits:** main session (the owner's call), a scheduling design under area C.

**2026-10-16 13:3x — fellowship split into two rungs (piece 2, done; Sonnet delegate, HAI cannot reach
`hard-bench`):** new tiers `rigby` and `tablets` beside an unchanged `fellowship`; backward compatible with deployed
Spock; `test_menu_policy` 24 → 29, hard-bench policy modules 128 → 133; live `policy.json` carries the rungs. The rungs
stay PLACEMENT rows — no McCoy label, the union tier stays (checklist B7 + C2, closed 10-17). Detail:
[[34-Phase-7-Placement-Policy-Design]] § D.

**2026-10-16 ~15:xx — McCoy WAITS FOR #37 TO FINISH (the owner: "do you not see why this could be bad to have both running
at the same time??" … "we need to finish project 37").** Lesson: HAI built McCoy in its sandbox on a 10-10 base of
`app.py` while #37 rewrote and deployed the same front door five times. McCoy stays #34 Phase 8 (an hour-long detour
moving it into #37 was reverted the same afternoon); its front-door work starts only after #37 is done, from the live
code, per the dependency plan below. The Opus delegate driving HAI was stopped for good.

**McCoy merge plan — DEPENDENCIES, not just order (the owner: "We don't want it building off of whatever it found when it
started").** HAI's McCoy checkpoint `d68fdf3` sits on `a2e4d42` (10-10); it is saved as
`State\hai-mccoy-checkpoint-a2e4d42..d68fdf3-20261016.patch`. Since then #37 shipped a great deal on the same front
door ([[37-Closeout]] lists it): `_run_query_core` + `_is_job` (the seam McCoy's shadow call plugs into), run
checkpoint/resume and park, lead effort, the quick-first Mothership slot, the finish gate, in-loop compaction, the
`:11434` gate guard, `scheduler.claim_for_url`. McCoy starts from LIVE as it is when the work begins (mirror HEAD then;
gate pin in `tests/gate_pin.py`), never from a remembered hash. So, in order:
1. **Preserve, then refresh the base.** The patch above is the preserved copy; the workspace is synced to live at every
   deploy (`sync-workspace.sh`) and a coding job refuses a STALE workspace (step 5, shipped). Never resume HAI coding on
   a stale workspace — 10-16's whole cost came from it.
2. **Re-apply onto the new structure, not textually.** The shadow call moves to where #37 now decides the request's
   shape (`_run_query_core`, beside `_is_job`), and must: fire ONCE per request, never again on a checkpoint RESUME or
   a park/recreate; emit nothing into the live feed (shadow = log only); not touch the Mothership slot (it calls
   Softserve); tolerate its background thread dying in a recreate (shadow log line missing, nothing else).
3. **Re-verify against what is live now:** drop `mccoy._under_unit_tests()`; the guard file is identical to the mirror's
   (no second copy); full gate with the current pin (`tests/gate_pin.py`, 2772 on 2026-10-17) + McCoy's new tests
   (state the predicted count first); Softserve `/v1/systemone` count unchanged across the gate run (baseline 117 at
   12:55 on 10-16).
4. **Labels are #34's:** McCoy ships with today's tier labels (no `rigby` label → the 0.90 line stands); the
   `rigby`/`tablets` rungs are placement rows, never McCoy labels (checklist C2, closed 10-17).
5. **Structural, so this cannot recur (Research-Synthesis R2) — SHIPPED by #37 (`1dcdcc0`, kept through the `30ff407` routing rollback):** a coding job and every
   overnight item refuse a STALE workspace (`overnight.stale_reason`, one derivation for both doors) and the Deep Work
   tile shows the lag.

**2026-10-16 14:04 — McCoy shadow wire PAUSED (the owner: let #37 finish first; HAI's turns held Mothership).** ✅ Superseded: the wire shipped after #37 (McCoy active 10-22, every user 10-23); the steps below are history. State, in
HAI's sandbox `orchestrator-real` on checkpoint `fac708a` (uncommitted on top): McCoy's call moved urllib → httpx so
the gate's `:11434` guard (`tests/test_zzz_no_live_model_calls.py`, commit 4a6b125, copied in unmodified) blocks it in
tests; new `TestNoRealMcCoyCallUnderTest`; Softserve leak count steady at 117 since 12:55. **Left:** HAI's last full
unit run (18:58Z) exited 1, failing test not yet identified → fix → checkpoint → review → rebase onto mirror HEAD →
deploy with park → workspace sync. Resume: ask HAI to run the unit profile and quote the failing test. Journal § "10:5x".

**2026-10-16 — the front door measured (a delegate drove HAI as the owner on "help me finish #34"; journal § "10:5x"):**
- **Routing is KEYWORD, not triage.** A coding follow-up containing "make" + "the owner" was taken by the P20 create route
  and queued rush render `7b4349432550` (desk cancel cannot reach it); a plain coding follow-up went to BigHonker's 9B
  (no tools, invented paths); turns reached the 3.8 only when the self-engineering regex matched. → McCoy (step 4) and
  the chat-or-job call ([[Research-Synthesis-2026-10-16]] R1) are what replace this.
- **The chat path cannot reach the champion's priced best:** `ollama_client.py` sends `"think": False` on every chat
  call, so the 3.8 at xhigh (A1 8/9) is unreachable through `/query/stream` (effort-sandbag class). → #37 step 3 (the
  lead's settings from A1, not a hard-coded flag).
- **Kirk answers a project request as long chat turns, never desk orders;** one turn died after 86 min ("no user query
  found in messages") and lost its own record. **HAI's sandbox suite reaches production:** McCoy's shadow call fired 117
  real `/v1/systemone` calls at Softserve during HAI's test runs (68 cold loads on the Plex box) — merge blocked until
  isolated structurally. **HAI's workspace** was 6 days behind the mirror and lacked the McCoy draft and the
  hard-bench files (pieces 2 and 3 cannot be reached by HAI at all).

| item | approved | status | next |
|---|---|---|---|
| The end state (one window, always an agent, live transcript, triage + placement advisor, Softserve always-on, WOL, multi-user; Auto = McCoy's triage + Spock's priced menu, the 3.8 = the owner's pin and the unsure-fallback — the owner 10-16) | 10-04 22:20–23:00 "Save all of that" | **#37 BUILT build steps 1, 1a, 2, 3 and step 6's surface** ([[37-Closeout]]); #34 owns step 4 (triage, McCoy), step 5 (placement advice, Spock) and step 6's PLACEMENT (overflow + "the owner first", Spock) — hand-off list above | McCoy (Phase 8), Spock (Phase 7) |
| Absurd retired; redesign = B (a Claude-Code-like live agent) | 10-05 21:16 | DECIDED | — |
| Eight redesign questions Claude listed 10-05 21:23 | — | **Already answered** by the owner's 10-04 requirements and standing rules (mapped one by one in [[HAI-Agent-Surface-Design]] § "Open for the owner", 10-07). Only #4's summarising had no decision → default recorded (the agent summarises, the server keeps the full transcript). the owner: *"I think some of these are even covered over the past few days."* | none |
| Adopt-before-building check of current agent harnesses for the work loop | 10-05 | DONE 2026-10-16 ([[37-Work-Loop-Harness-Check-2026-10-16]]): no app harness; HAI's own loop with the tests deciding (finish gate), Inspect's compaction rule adopted in production | — |

## Backlog blockers named in the 10-05 re-ground (in PROJECT-BACKLOG)

- HAI's heavy jobs must claim Mothership through Spock (2026-10-04 entry).
- Orchestrator gate's two pinned skips break the no-skips rule (2026-10-04 entry).
- No remote restart for a frozen Mothership.
- rigby's GT 730 as a Fellowship node (2026-10-07 entry).

## Housekeeping owed

- **rigby's queue `.HELD` files are DELIBERATE — leave them** (checked 2026-10-07 from journals + handoffs; the owner:
  *"I'm pretty sure we cancelled/held those orders for a reason"*):
  - 213 `phase5-expanded-ready.*` (held 09-06…09-08): Phase 5 execution COMPLETE 09-26 (0 held); the handoff says
    *"Preserve HELDs … do not regenerate them"* — the immutable originals of finished work
    ([[HANDOFF-Project34-Phase5-after-Codex-20260908]]; `Journal\2026-09-26-claude.md`). Appraisal gate is the owner's.
  - 31 `p5r-19f354e4-w*` (09-07): the Phase 5 campaign driver's superseded attempts; driver complete 304/304.
  - 27 `hardbench-codef2-q352bt16k-cal` / `q3508bt16k-cal` (09-29, thinking-on tablet calibration at 16k):
    superseded — Phases 2/3 CLOSED 10-03 (the owner *"Yes good sir"*), tablets tested in ROLE not grids (decision 3a,
    10-04), and 10-04: *"Qwen3.5-2B W1 thinking-on does not fit the 2 GB tablets — not a re-order."*
  - 2 `cam-recall-basement` (08-09): the cameras pipeline is PAUSED, not dead ([[Cameras-INDEX]]).
- Memory index near its size limit — the owner already authorised it (10-03 17:21: *"You may undertake this with great
  care while we are in between steps"*): Claude does it between steps, nothing dropped without a pointer, diff shown.
