# #37 HAI agent surface — one chat window, always an agent, live streaming transcript, triage + placement advisor, seamless lead model, multi-user (child project of campaign #34)

**Registered as project #37 on 2026-10-16** ([[PROJECT-REGISTRY]]; child of #34, the product #34's placement rule
feeds). #37 owns build-order steps 1, 1a, 2, 3 and 6 below, the display/UI pieces and the order-flow work (quick
requests first, Quick Resume). **One writer on the front door (the owner, 2026-10-16): nothing of #34 touches it until #37
is called done** — McCoy (wiring and measurement) stays #34 Phase 8 and starts from the live code then, by the #34
register's "McCoy merge plan — DEPENDENCIES" (incl. the "McCoy fired" mark in the run ledger, pinned by a resume test). **Steps 1, 1a and 2 SHIPPED 2026-10-16**, plus the surface parts of 3 and 6 and the Quick Resume operator
pieces (§ "SHIPPED 2026-10-16"). **#37 builds the SURFACE, never the decisions:** which box, model or tier a request
goes to is Kirk/Spock's from the priced menu (+ McCoy's triage, #34 Phase 8) — #37 changes no routing.

**▶ OWNER since 2026-10-16 ~17:30: the MAIN session** (the owner retired the side session; its handoff verified against the
live system: orchestrator `d060d01`, mirror clean, state-dump payload `ecb1f367` on all three factories, nothing held).
**#37 COMPLETED 2026-10-17** (the owner checked the phone-layout fixes on his phone: "All looks good"). Current state, his answers and
the hand-offs #34 owns: [[37-Closeout]]. Question 2 (an interactive job's unverified code) answered from #12: kept in
its own section and marked, never deleted — now a checkpoint marked UNVERIFIED, restorable. The build history, in order: (1) the KV state dump on Mothership — **MEASURED 17:53** (order
`quickresume-state-dump.20261016-1743_mothership`, the 3.8 on its llama-server recipe, ctx 122,880, 2 rounds agree;
spill 0.43 GB — absolute times slightly pessimistic, the within-run comparison holds): full read 50 s @30k / 118 s
@60k; after an eviction llama-server's BUILT-IN RAM prompt cache (on by default; the BigHonker plan had `-cram 0`, so
the two boxes measured different things) served the next turn in 2.2 s / 2.8 s = the never-evicted cost; a disk slot
restore 1.0 s / 2.0 s + the turn; a disk SAVE 11–14 s / 30–32 s (1.3 / 2.3 GB). **⚠ CORRECTED same evening — no
engine switch:** ollama's runner IS llama-server with the same RAM prompt cache ON (Mothership `serve.log`: "use
`--cache-ram 0` to disable the prompt cache" at every load, 65 "making room for prompt cache entry", 200 "restored
context checkpoint"), and ollama ≈ direct llama-server on speed (10-02, [[Qwen3.8-Flash-Next-on-Mothership]]; the owner:
the prices are the same). **Every full re-read of 10-16 (10:45–14:00) followed a RELOAD of the 3.8** (~25 loads × ~42 s,
each followed by a 60–75 s read of the whole conversation; at 13:37–14:00 a reload every 1–2 min): a reload loses the
RAM cache. Reloads come from a caller asking the 3.8 for a different `num_ctx` (one 8,192-ctx block at 12:10; the
13:4x ping-pong fits two callers alternating sizes — `config.MODEL_NUM_CTX` exists to prevent exactly this, so a
caller bypasses it; culprit to confirm) and from the 5-min idle unload. The disk dump only matters across a restart. (2) **the work-loop redesign** (§ "How the agent does WORK": one
loop, the tests decide, a different-model peer reviews the diff after green, repair in the same loop) — first the
adopt-before-building harness check — DONE 10-16 ([[37-Work-Loop-Harness-Check-2026-10-16]]: no app harness;
reuse HAI's own loop + the overnight verifier + `coding_review`, Inspect `react()` semantics as the reference, gap list
there). **Step 1 SHIPPED 2026-10-16 18:16 (`484c63b`, gate 2689, deployed with park): the FINISH GATE in the
production loop** (`ollama_client.chat`) — on a JOB (`app._is_job`) whose coding loop changed code
(`coding_patch`/`coding_create`/`coding_file`), the model's final answer is refused (`[FINISH REFUSED …]`, a
`finish_gate` event in the live feed, `FINISH GATE refused` in the notes) until `coding_test` reports returncode 0 on
the code as it stands; an edit after a green run closes the gate again; after `CODING_IDLE_TURNS` refusals the answer
ships labelled "Not verified" — never as a finish. Same rule as A1's FINISH_GATE. Proven by 8 tests + a mutation (gate
blind to edits ⇒ 4 red); **not yet proven live** (needs a real job that edits code). **The data on the other two
ideas:** peer review measured NO gain (A1 reviewer arm 10-08: qwen3:8b thinking on, 3/7 = 3/7) and think-hard's
critique swaps a second model onto Mothership (10-16: every swap = a 3.8 reload + a full re-read) → peer review stays
OFF until an A1 arm shows a peer that helps; think-hard keeps its critique for deep QUESTIONS (item 6). Same deploy:
the retry ladder shrinks `num_ctx` only on a memory error (a 400 had stepped the 3.8 down twice, each a reload). The
Background switch is the work loop's surface; (3) ✅ **talking to it while it works — SHIPPED 2026-10-16 18:2x
(`9f2e453`, gate 2697, deployed with park; served page `node --check` OK), PROVEN LIVE 18:31** (ClaudeSupervisor chat
`e157da0113f1`: `POST /query/message` 200 → notes `RUN MESSAGE queued` then `delivered` → a `user_message` event in
the live feed → the answer addressed it → saved as `interjections` on the answer, nothing left pending). Mechanism: a
per-run inbox in `ollama_client`; delivered at the run's next natural point (the start of a model turn, or instead of
ending when the model has just answered); the page keeps Send usable during a run and routes to it (404 = no live run →
sent as a new message). Limit: an EPHEMERAL run (the ghost `test` profile, one-shots) keeps no ledger, so it cannot be
messaged (404); (4) the pin and the queue line are proven by tests only (the owner
declined a browser check; look at them on his next real use; the pin's server side was proven live 2026-10-17).
Cold vs warm lead: answered from the library (§ Open for the owner).

**Status: DESIGN captured with the owner 2026-10-04 (his words: *"I think that's finally all of the pieces that I can think
of. Save all of that."*).** Built by 2026-10-16: steps 1, 1a, 2, the surface of 3 and 6, the Quick Resume operator
pieces (§ SHIPPED); routing, triage and placement are unchanged and stay #34's. Everything else in today's chat still
works as described in [[HAI-Control-Surface]]. This is the product the #34 campaign feeds ([[34-CHARTER]] § "End state — what #34 serves"):
#34 builds the machine half (priced menu → Phase 7 placement → area C), #35 trains the judgment half. Builds on the
facelift's framing — *"the chat window is the ONE DOOR to the organism"* ([[Chat-Interface-Facelift-Plan]]; its
Piece C "live runs" becomes the inline order cards below).

## Why revisit the July design

**What exists (shipped 2026-06-30 → 07-10, [[HAI-Control-Surface]]):** five header axes — Persona · Model (auto / 8B /
27B) · Effort (normal / Hard / **Absurd**) · View · Plan. Hard is the think-and-critique convergence loop (~30 min
cap); **Absurd is the same loop run detached** with a 1h / 4h / 8h / all-night budget, stopping on convergence.

**The question it was built to answer** (the owner, 2026-10-04): *"if a model just given more time to cook on something
could just eventually give me a good answer to a similar quality of what I was getting from working with you"* — built
before *"I didn't understand that we could have models that were specifically just good at chatting or specifically
good for agentic work."* *"It's not bad, but I don't think it's industry standard by any means."*

**What the lab's data has said since** (#34): model choice dominates (8 GB tier writes W1 programs 1–19 % vs the 3.8's
98 %, [[34-CHARTER]] DoD #2); self-critique without a checker adds no right answers (Phase 5: *"refiners degraded"*;
Hard Bench: voting *"reshuffles, creates no right answers"*); a CHECKER is what lifts quality (best-of-N 0.61 → 0.94
only when picked by tests). The lever is checkers + tools + the right model, not more passes. **Not retired on that
reading:** "think-hard loop vs plain agent loop" is an A1 arm, so data decides ([[34-A1-Agentic-Build-Family-Spec]]).

## the owner's requirements (2026-10-04, in his words)

1. **A live transcript, not a drop at the end:** *"I want to be able to see conversations like how I see them when I'm
   with you or other frontier models where I can choose the transcript view and it shows me a live view as you're
   inputting your thinking… see the live output as they're going not just the whole thing dropped at once afterwards."*
2. **One window, no mode picking — always an agent:** *"I want to be able to just log onto that webpage and start
   talking to HAI and not have to worry about if I've set it into chat or code. To be honest, I almost never use your
   Chat mode. I almost always have you in Claude code… I enjoy having Claude code able to interact with my lab."*
3. **Mostly seamless:** *"if I hop on start talking to it about plans and stuff, and then the system decides it needs to
   switch to a code agent or whatever I don't want to feel those hiccups… I'm OK with a little bit of a seam existing.
   I just want to feel mostly seamless."*
4. **Factories must not get in the conversation's way:** *"I want to make sure that we're not overly locked into the
   factory idea where it's inflexible… I worry that the factory order system will interfere with the way that it
   actually needs to work."* (Factories exist for consistency + visibility of WORK — a Task-Manager view of everything
   the lab does, in work units and rates; the owner 2026-10-04, [[P20-FOUNDRY-RUNNER-PLAN]].)
5. **Softserve is the only always-on box; the rest are WoL** (*"Hence why, Star Fleet command lives on soft serve"*).
   A ~30 s cold wake *"really is not bad"*.
6. **Several users at once** — *"there may be points where multiple users are trying to talk to HAI. So that's why we
   need smart routing decisions and smart logic."*
7. **Triage — not every request deserves the big card:** *"maybe I have two users and one of them is just asking what's
   the weather going to be like this week and the other one is asking for hard coding stuff."* **The 3.8 27B is the
   default** *"because we've done so much work with it and tested with it and made it my frontier like model. I always
   run into issues anytime that we've tried to give the 8B or lower work that really should've gone to the 27B."*
   **Default = the fallback when triage is unsure and the owner's usual pin; on Auto, triage (McCoy) sends simple requests
   to small models and real work to the lead — Auto is NOT "always the 3.8" (the owner 2026-10-16).** (Both the side
   session and the main session misread this line on 10-16; an "Auto → the lead" route was built, deployed for ~2.5
   min with no request using it, and rolled back — § SHIPPED.)
8. **rigby and the tablets are in the lineup** — *"if somebody's asking a stupid simple query of HAI, then it could
   perhaps just have the AI assisted Starfleet command route it to a tablet or Rigby (by sending it to Tolkien and
   Tolkien intelligently routes it)."*
9. **The advisor advises placement as well as triage** — *"I kind of assumed that we would be having it advise Spock on
   placement as well as advise on triage"* (and Tolkien; [[34-CHARTER]] area X, 10-04 *"advisors to our schedulers like
   Tolkien or star fleet command with Kirk and Spock"*).

## What exists today that this builds on (measured / read 2026-10-04)

- **`/query/stream` streams STATUS only** (`app.py` `query_stream`): heartbeats + status text, then the whole result in
  one piece — exactly requirement 1's complaint. **Kept:** runs are server-authoritative and survive a refresh
  (`/query/reattach`, `_detach_stream_worker`).
- **ollama streams thinking, text and tool calls**; the foundry's `infer_ollama._chat_stream` reassembles all three
  (tool calls since 2026-10-04, `050bbee`).
- **⭐ DECIDED 2026-10-19 — MODEL WORK IS A FACTORY ORDER, CHAT INCLUDED (one door onto the cards).** the owner: *"Generally
  speaking when models are running, I want that work to be tracked. So that means putting it in as a factory order."*
  (and 08-14 *"When HAI is talking through chat all of those get routed through the orchestrated and scheduler to
  determine what hardware it runs on"*; 09-11 *"I was low key expecting it to go through the orders system"*). A chat
  turn is an order to Starfleet: Kirk takes it, McCoy advises, Spock places it with the SAME availability judgement as
  every order (a person on the card, fits beside what is there, Plex first, lost cards), it runs as a priority order
  that pauses lower work losslessly, and streams back. Latency is solved INSIDE the loop (checkpoints, dumps, resume —
  the owner: *"make the HAI system and loop have as little latency and unnecessary waiting as possible"*), never by a second
  path. Quick non-model favours stay quick. **Today (2026-10-22): chat turns run as factory WINDOW orders for every user**
  (`HAI_CHAT_WINDOWS=*`; [[34-CHAT-FACTORY-CONTRACT-AUDIT]]), so the desk applies the order checks. **TWO LAYERS
  (the owner 2026-10-22):** STARFLEET (Kirk takes the turn, McCoy advises, Spock picks the box FROM THE PRICED MENU) →
  then that factory's own DESK QUEUE executes it. ✅ The Starfleet half is menu-driven for chat since 2026-10-22/23:
  McCoy's tier and kind feed Spock's per-request weighing from the priced menu ([[34-Phase-7-Placement-Policy-Design]],
  area C step 1; the family comes from the tier only — checklist C3, open). **Two documented exceptions to "an order", both Starfleet-level by decision, not gaps:**
  (1) **McCoy's own advisor calls** — Spock places them as kind `advisor` (home Softserve, any box when it cannot,
  coexists with Plex, yields to live work) and a caller waiting on him BORROWS an idle chat card by save → lend →
  restore (register C1c, 2026-10-21); (2) **transient calls** (per-query embeds, titles, small fallbacks) skip an
  occupied card through ONE lookup, `scheduler.occupied_for` (2026-10-17, CLAUDE.md doctrine "place ANY job"). The earlier line here ("No blind spots without making chat an
  order", from doctrine 12e) is archived in `Archive\HAI-Agent-Surface-Design-history-2026-10.md`.
- **Routing today:** `scheduler.place()` (Spock), brain order **BigHonker → Mothership → Softserve last** (Plex host),
  WoL wake with `PC1_WAKE_SETTLE` (`MOTHERSHIP_WAKE_SETTLE` until 2026-11-16) 5 s + cold 27B load ≈ 18 s ([[RUNBOOK-Ollama-Host-Operations]]; measured on an
  older 27B — re-measure for the 3.8 at 122,880). The 3.8 is HAI's `HEAVY_MODEL`, resident on Mothership.
- **Concurrency today:** Mothership `OLLAMA_NUM_PARALLEL=1` (set 2026-10-04 for menu parity) — two users on the 3.8
  queue; Absurd runs one at a time on the 27B (`has_active()`).
- **Tool selection by retrieval** (designed 2026-07-25, PROJECT-BACKLOG § "TOOL SELECTION should be RETRIEVAL"): the
  8B was choking on 86 tool schemas = 100 % of its context; escalation was itself a tool call; the owner: *"the ORCHESTRATOR
  should decide routing, not the small model self-reporting."* Three signals: meaning (embeddings), the user's own
  corpus, in-flight state; *"when the top scores are weak or bunched — ASK."*

## The design

```
user (any device) ─► FRONT DOOR on Softserve (always on): accepts instantly, starts streaming the transcript
          │
          ▼
     TRIAGE (Kirk + the ADVISOR) ── how hard? which family? chat turn or a job? how sure?
          │                           unsure ⇒ the 3.8   (under-routing costs more than over-routing)
          ▼
     SPOCK places from the priced menu (+ the ADVISOR's placement advice, validated deterministically)
          ├─ tool / plain code ......... e.g. "weather this week" = a Home Assistant call + one sentence
          ├─ TOLKIEN → tablets / rigby . simple chat; Tolkien picks the node (+ the ADVISOR, within the Fellowship)
          ├─ 8 GB boxes ................ mid-weight; overflow when the lead is busy (with its caveat)
          └─ THE LEAD (the 3.8 today) .. real work and every unsure case; delegates pieces DOWN to any worker
```

**A conversation holds orders; its model work runs through factory orders, chat included.** This is the decided
target, not a claim that the current direct-call path already does it. Kirk retains the conversation, tools and
live transcript; Starfleet and the factory queues own placement and execution, using the same availability
judgement, priority and checkpoint/resume machinery as other orders. Model output streams back into the same
conversation and its order card. Interactive latency is addressed within that path, with no direct-call bypass.
The later section "A WORKFLOW HAI must handle without gaps" describes an optional likely-V3 usage, not a V2 default.

**Always an agent.** Tools on every turn; Model and Effort stop being REQUIRED dropdowns, and the user's own pick ALWAYS
wins. **DECIDED 2026-10-07 (the owner): the choice is never removed.** *"Generally speaking when I'm working with the lab I'm
going to be using the 3.8 just because I like using the best thing… I could fully see myself just wanting to give
myself the full throttle whenever I'm the one that's using it so we can't delete that choice from me."* So: a user can
pin model + effort (the owner's likely default: the 3.8 at full effort); triage/placement (the advisor + Phase 7 table) runs
only for turns the user leaves on Auto, and for HAI's own background work and sub-agent pieces. A pin is honoured like
any order pin — the table orders hosts and never overrides a pin; it may INFORM (e.g. "xhigh adds time with no measured
gain on one-shot chat"), never block. The View axis survives as the transcript-view presets (live thinking, tool trail,
summary).

**EVERY USER, THE SAME CONTROLS (the owner 2026-10-22; the full checklist of that day's routing + controls corrections:
[[34-Starfleet-Routing-and-HAI-Controls-Do-Not-Collapse-Checklist]]).** *"You are not to be removing options from users. All users should
have the option boxes that we have discussed 1 million times. It's just that we want it so every user can still be on
auto and have no issues. So every user should have the same control scheme pretty much."* Auto is the default and must
just work; the pin is every user's. A pinned request is an ORDER like any other — it does not take a busy card: *"If
it's something that they're saying, they really require the biggest models for, then we can have the order go in and
then if the machine is busy, then they get the little pop-up saying machine is busy Chat/order/whatever they're doing
is queued, and it tells them that they can reroute to another machine with X caveat."* the owner keeps an owner override
on his own machinery (*"I don't want to be limited by my users… there's a secret little option where it's the owner can
override to use his own machinery"*) — the HAI 1.0 per-user gates existed so other users could not take Mothership out
from under his 27B work; queueing + the owner-first priority (B4a, `PRIORITY_USERS`) + the override replace that purpose.
**Lineage — this is NOT new (2026-06):** HAI 1.0 Phase 2 (2026-06-18) shipped the routing dropdown with Force 27B
admin-gated (`MOTHERSHIP_FORCE_USERS`, non-admins degrade to Auto) and the Mothership slot lock whose busy result says
"busy, use Quick/Normal" (option A, the owner's pick); the owner's 2026-06-28 brain-dump, Cluster B (`Archive\current-projects-
history-20260821\2026-06.md`), asked for the rest: *when Mothership is busy, answer via BigHonker as now **but also
offer to QUEUE for Mothership***, a long job checkpoints and yields at a safe point so *"multiple people share
Mothership with only a time penalty instead of being blocked"*, and each queued user sees **a rough ETA** until their
turn. Carried forward since: the owner-first (B4a, live), checkpoint/resume (B11/B11b, Quick Resume), B4b's overflow — but
the user-facing half (offer to queue, ETA, offer to reroute with its caveat) was dropped from the register's B4b.
**HAI 1.0 holdovers still live (not reconciled, 2026-10-22):** (1) the 27B choice in the Models box shows only to
`MOTHERSHIP_FORCE_USERS` and the server ignores a 27B pin from anyone else (`app.py` `force_lead`; in the
2026-08-24 baseline; [[HAI-Control-Surface]] § Model); (2) the Nursing persona hides the Model and Effort boxes and
pins Auto. **Status: BUILT + LIVE 2026-10-23** — `MOTHERSHIP_FORCE_USERS` retired (the 27B option is every user's), a
pin to a busy Mothership queues with the notice, the holder's elapsed time and the reroute offer with the table's own
caveat, Nursing shows Model and Effort; McCoy serves every user's Auto (`MCCOY_ACTIVE_USERS=*,ClaudeSupervisor`, 10-23: machine users such as ClaudeSupervisor are skipped unless NAMED - `mccoy.special_route` rule 7 - so "*" alone silently dropped the lab's own live proofs from 12:44 to 14:27 CT). Register §
"CLOSE-OUT PLAN" A4.

**Seamless = one resident lead that both talks and builds.** The big seam is swapping models on a card (load time, one
model per card), so the lead does chat AND agentic work and stays resident WHILE IN USE — the card still unloads every
model after 5 min idle (`OLLAMA_KEEP_ALIVE=5m`, RUNBOOK-Ollama-Host-Operations; the owner 2026-10-07: *"It's supposed to
unload all models when it's not being used"*), so Mothership is never reserved for one model; sub-agents run on OTHER boxes — the owner's
2026-10-04 *"small specialists… so we don't always have to do a model swap on the 7900xtx."* **A1 picks the lead**
(can it talk, build, delegate). Remaining seams, by design: a cold wake (~30 s, shown honestly in the stream, or the
lead kept warm while the owner is active — an energy trade for the owner to price); Mothership busy with an order (the live
conversation gets priority on Mothership; orders wait — via a Spock claim, see the backlog); a job becoming an order
("starting that as a job, watch it here").

**The ADVISOR — named McCOY (the owner 2026-10-07; Kirk's advisor who says what he thinks and leaves the call to Kirk) — one
small model, several jobs, deterministic authority on every one (Phase 8).** Lives on Softserve (it
is in every request's path; the owner named Softserve the advisor host). Proposes → deterministic validation → execute or
reject ([[34-CHARTER]] area X):

| advises | on | deterministic check |
|---|---|---|
| Kirk | triage (hard? family? chat vs job? sure?), decomposition | menu families; unsure ⇒ the 3.8 |
| Spock | placement: which box/fallback, wait for Mothership or go now, which helper takes which piece | fits the card, one model per card, Plex priority, energy gates, claims |
| Tolkien | which tablet class or rigby, how many tries, when to escalate up — **door built 2026-10-23, SHADOW** (`/mccoy/tolkien` on the same call triage uses; Tolkien logs the advice beside his decision in `state/mccoy_shadow.jsonl`, McCoy never sees the decision; acts on nothing until the questions are measured, #35) | Fellowship capability + lane rules |
| all | result comparison; when to escalate (bigger worker, or Claude) | the checkers (tests, schemas) where they exist |

*Decision models* (typed choice / yes-no / score answers with per-option probabilities, served at `/v1/systemone`) are the
candidate interface for McCoy's calls; evidence ranking and open design questions: [[Research-Decision-Models-System-One-Landscape-2026-10-15]].

**Several users.** Overflow goes to the next box with its caveat (the owner 2026-10-02: a slightly pricier box of similar
quality, to run concurrently); priority and fairness (the owner first, energy-gated users deferred — Absurd does a version).
Concurrency comes from the FLEET, never from parallel slots: every box runs one model and one slot
(`OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, [[RUNBOOK-Ollama-Host-Operations]]; the owner 2026-10-04: *"we
want single model at a time per card"*), and the 3.8 at 122,880 is the card's tested maximum with that one slot.
**The single exception — McCoy (2026-10-23).** Softserve holds two models so McCoy's model stays loaded beside each
turn's embed (the owner: *"Let softserve run two models at current context size, and make it have to use one at a time if
plex's transcode usage trends to rising above what can hold both"*; *"McCoy is the singular exception, for the
record"*); the health daemon's duty 7 drops it to one when Plex needs the room. McCoy: 0.2–0.4 s a call (was 6.7–10.8 s).
Phase 7 + area C (urgency, contention, residency).

## Measurements it needs (role-shaped — the menu's next cells, not new grids)

1. **The lead** — A1 solo + A1-team for the 3.8 / Ornith / Nemotron (in flight); the think-hard loop vs plain agent
   as an A1 arm; the behaviour arms (unverified success, recovery, correction, plan respect).
2. **Cold model LOAD for the 3.8 at 122,880** — the runbook's ~18 s was an older 27B. Measured by an ordinary
   order (`hard-bench\lead_probe\cold_load_probe.py`: clear the card, time the first word cold, then warm; several
   rounds). The WAKE itself is not measured: WoL works daily, the 5 s settle is a config constant, and the owner ruled
   the ~30 s total acceptable (2026-10-04: *"30s waking up really is not bad"*). **Measured 2026-10-06**
   (`lead-cold-load-q38.20261005-1713`, 3 rounds, fully on the card): cold first word **43–45 s** (load 42.8–44.5 s),
   warm first word **0.10–0.11 s**.
3. **Interactive latency of the small tier** — tablets per class and rigby answering a one-line chat turn through
   Tolkien (C1 is already priced per class; this adds time-to-first-token for a live conversation). **MEASURED
   2026-10-07 (CHAT1, `Project34-Menu\chat1.md`):** rigby Qwen2.5-1.5B 35/35, first token ~0.3 s, 1.9 s a turn; tablets
   6–40 s a turn by class and model, but every tablet turn reloads the model (`llama-cli` per item) — a cold-turn price.
   **the owner 2026-10-07:** the Fellowship hosts several chats at once (rigby counts as one node, "a wickedly overpowered
   tablet"). **Built 2026-10-23 as Tolkien's node pool** — each order takes only the tablets it needs and the next runs
   on the free ones ([[Fellowship-Order-Taker-Design]] § 3); models start per order (a rigby turn 8.5–9.1 s), not kept warm: McCoy's is
   the only kept model (the owner 10-23: *"McCoy is the singular exception"*).
4. **The advisor (Phase 8 family)** — triage: past HAI chat requests labelled with the right tier; placement: lab-state
   snapshots + the menu, scored against the best known choice and against what happened (Spock's placement history —
   which did NOT exist until 2026-10-07: `scheduler.record_placement` now writes every decision with its candidates and
   reasons to `State\placements\YYYY-MM.jsonl`; placement items are mined from it — run records); decomposition + result comparison from A1-team transcripts. Under-routing and unsafe placements
   weighted heavier; every proposal the deterministic layer rejects counts against the advisor. These requests and
   transcripts are also #35 data. **Triage v1 measured 2026-10-06** (`hard-bench\advisor_tasks.py`: 48 real HAI
   request shapes, Claude's v1 gold labels, the owner may relabel; orders pinned to Softserve, structured output,
   thinking off, greedy; `campaigns\advisor-20261006\reduce.json`): right tier / under-routed (lead sent lower) /
   wall median — **qwen3:8b 31/48, 0, 0.50 s** · qwen3.5:9b 31/48, 0, 1.00 s · Nemotron 3 Nano 4B 30/48, **6**,
   0.70 s · MiniCPM5-1B 16/48, 3, 0.40 s (answers `family: chat` on every row). All four answer in under a second
   per request; the 8B is the candidate to beat on an IDLE card — its misses are library lookups over-routed to the
   lead. Family and kind scores are v1-label-bound and not yet read. The measurement is an order; in production the
   advisor is a tracked call (the owner 2026-10-06). ⚠ **The bar is "loads and answers in seconds REGARDLESS of Plex
   activity"** (the owner 2026-10-06: *"the softserve advisor would need to be able to load and run regardless of plex
   activity, so while the 8b and 9b did well, idk that they are going to be final choices"*): Softserve's 3070 Ti is
   Plex's transcode card, so the v1 cells (idle card) are necessary, not sufficient. **Plex's footprint, measured**
   (Softserve's per-second GPU telemetry, 32,978 transcode seconds since 09-29 with none of our orders running): VRAM
   **median 478 MB, p90 1,322 MB, max 2,427 MB** over the 175 MB idle baseline; encoder and decoder both reach 100 %.
   And the v1 cells loaded the small models as fat as the big ones — MiniCPM5-1B and Nemotron 4B at **6,355 MB** each
   because they ran at their menu-max contexts (131k / 261k): KV cache, not weights. **Footprint measured 2026-10-06
   at a request-sized context (2,048; the largest v1 request is 387 + 36 tokens), card as it is, peak VRAM sampled
   every 2 s** (`advisor-v1-ss-plex-ctx2048.20261006-091532`, `hard-bench\advisor_under_plex.py`; the owner: no waiting
   for a transcode and no synthetic load — *"leaving it open for Plex"*): MiniCPM5-1B **783 MB** · Nemotron 4B
   **2,815 MB** · **qwen3:8b 5,085 MB (leaves 3,107 MB — clears Plex's median, p90 and max bands)** · qwen3.5:9b
   6,479 MB (clears p90, not the max band). Tier scores unchanged (deterministic). So on the measured numbers the 8B
   fits beside any transcode seen so far with ~0.9 GB to spare; the behavioural proof is organic — the telemetry
   logger records the advisor's residency the first time a real transcode overlaps it. Reduce:
   `submit_advisor.py reduce-plex`. **Behavioural proof landed 2026-10-07** (`advisor-v1-ss-fit-ctx2048.20261007-153622`,
   run while a real transcode held the card — 1 stream throughout, encoder to 90–100 %): **qwen3:8b unchanged — tier
   31/48, median 0.5 s, max 0.8 s (idle: 0.5 / 0.7), peak 5,439 MB, leaves 2,753 MB, clears the max band.** qwen3.5:9b
   same tier but max stretched 1.1 → **4.1 s** and leaves 1,359 MB (fails the max band). MiniCPM5-1B and Nemotron 4B
   unchanged. **Verdict: qwen3:8b is the Softserve advisor; it loads and answers in under a second beside a live
   transcode.** One transcode is what overlapped; Plex's max band (2,252 MB) is covered by the 8b's arithmetic above.
   **Triage v2 (the owner's own 36 requests 10-03..10-07, `advisor_tasks.TASKS_V2`; `advisor-v2-ss-fit-ctx2048.20261007-090250`,
   ctx 2,048, idle card, greedy):** right tier / under-routed / median / max / Plex max band —
   **qwen3:8b 23/36, 2, 0.5 s, 0.7 s, fits (leaves 3,109 MB)** · qwen3.5:9b 25/36, 2, 0.9 s, 2.8 s, does NOT fit the
   max band (leaves 1,713 MB) · Nemotron 3 Nano 4B 14/36, **11** · MiniCPM5-1B 11/36, **11**. The two small models
   under-route a third of the requests (the costly mistake), so on v1 + v2 the 8B is the advisor to beat: near the 9B on
   tier, about half its latency, and the only one of the two that fits beside Plex's worst transcode.
   **The behavioural proof under a real transcode — RUNNING 2026-10-07 15:36** (`advisor-v1-ss-fit-ctx2048.20261007-153622`,
   `--passes plex`, the four candidates while Tautulli reports a transcode in flight). Softserve's factory used to stop
   every order during a transcode, so this pass could never run; an order that declares `coexists_with_plex` now runs
   beside Plex (only a lost card stops it; the payload's residency refusal is itself a result).

## How the agent does WORK — Absurd's GOAL kept, its machinery replaced (2026-10-05; PROPOSED shape, not built)

**What Absurd was FOR (the owner, keep this):** agentic behaviour from a local model, made smarter by MORE TIME and by PEERS
who grade its work, because local models are weaker than a frontier model. His original question (2026-10-04): *"if a
model just given more time to cook on something could just eventually give me a good answer to a similar quality of
what I was getting from working with you."* And 2026-10-05: *"I was trying to get "agentic" behavior out of a local
model where it can get reviewed on its work and be smarter through having peers to help grade, because I knew that
local models weren't as powerful as you are."* The July design already named the anchor (2026-07-06): the trust chain
hangs off the deterministic verifier; critique without ground truth *"buys convergence, not accuracy."*

**What failed is the machinery, not the goal.** the owner, 2026-10-05: *"I don't know that I've ever had a successful run
of absurd mode so I'm down for it to be scrapped or redesigned"* — after the supervisor measured why (*"technically a
blind reviewer class bug"*): the peers never saw the work, and the extra time went to arguing about the REPORT.

**The finding (2026-10-05; ✅ the blindness it describes is FIXED as of 2026-10-19 - orchestrator `9d1c4c8`: the trail
keeps every call whole for the critics and their evidence is sized from the critic's own window, `app._critic_room_chars`,
192,066 chars live; 37-Closeout § Charter audit item 2).** Absurd is the think-hard engine (built to ANSWER carefully:
look things up, a critic reviews the answer, refine) with a bigger budget, and it was the vehicle for every supervised
coding job. For coding its review was blind: the critic receives `deliberation.evidence_block()` = the first `_EV_MAX_STEPS` (12) steps of the tool trail —
on a coding job always workspace-open / git status / reads, never an edit or a test run — each result already clipped
to 220 chars by `ollama_client`'s `_TR_RESULT` (cutting unittest's summary line), from a trail capped at 80 entries
SHARED across passes, so whatever a refine pass verifies is never recorded (the G2b comment in `_run_think_hard` says
the re-check depends on exactly that). Every run therefore drew "unverified claim" findings, refine passes re-entered
the coding loop cold (lost track of their own earlier work, off-design rewrites), and on 2026-10-04's seven finished
supervised jobs ~51 min of first-pass coding carried ~86 min of review/refine. The same model in a plain loop with the
tests as judge (A1) solved 33/34 small exercises — small tasks, so it shows the wrapper is the drag, not that the model
would ace large jobs. Absurd also had no cancel (stopping one meant an orchestrator restart).

**The shape, from parts that already exist** (reuse, not a new engine) — each of Absurd's three ideas kept, done so it
can work:
1. **More time to cook** → ONE agent loop for the job — the coding loop as deployed 2026-10-05 (no turn count; ends on
   the answer, on demonstrated idleness, or at the job's deadline), streaming every step to the transcript (build step 1).
2. **Ground truth** → the TESTS decide, run by the system, not reported by the model — the self-engineering worker's
   deterministic verifier (`overnight_worker.work_item`: verifier → scope gate "does the diff touch what the spec
   names" → hollow-pass check → revert on failure).
3. **Peers who grade the work** → PEER review AFTER green, of the ARTIFACT — the diff plus the full test output, never
   the worker's prose. `coding_review` (diff-based) is the reviewer today; the peer should be a DIFFERENT model from the
   worker (decorrelated, as the July trio intended). A finding goes back to the SAME loop as a repair turn; no cold
   refine passes.
4. Decomposition only for a job that measurably cannot fit one loop; each piece runs this same shape.
5. Background work is the same agent with a time budget and a visible cancel, not a separate mode.
6. Deep-think QUESTIONS keep the think-hard engine, with its evidence window fixed so the critic sees the steps that
   bear on the claims, not the first 12.

**Two rules from the 2026-10-04 sketch that the work shape must keep** (Claude's sketch items 3 and 5, discussed with
the owner and not objected to; dropped from this doc's first version, restored 2026-10-05):
- **One loop for measuring AND for production.** The A1 harness is the agent loop in miniature (tools, tests,
  no-progress cut, behaviour arms). The loop A1 measures must be the loop HAI runs, never a second one, or the
  measurement prices something production does not do. Step taken: A1's `agentic-hai` mode (2026-10-05) gives the
  measured model HAI's production coding tools, with the same names, contracts and descriptions.
- **Adopt before building.** Open-source agent harnesses can drive local ollama models. Before any new loop is built,
  check the CURRENT releases of the main ones (by search and by what is installable, never from memory) against
  three needs: Starfleet's order and visibility model (doctrine 12e: every unit of work visible), the
  deterministic test verdict, and HAI's own tool contracts. Adopting one that fits beats building a second engine;
  record the check and its result here.


**Whether peers actually make a local model smarter is a MEASUREMENT, and A1 is where it is taken**: the same lead on the
same tasks, solo vs solo + a peer review of its diff and test output with one repair turn, and which peer helps most
(the leads and the 8 GB / rigby shortlist as reviewers). Same rule as A1-team: one variable, one layer at a time.

**Open for the owner:** how an on-demand supervised job enters the self-engineering lane, given that lane's rule that only a
human arms backlog items; whether decomposed mode survives; what replaces the Absurd button on the page (a "work on
this in the background" switch on the same agent is the proposal).

## ✅ SHIPPED 2026-10-16 — steps 1 + 1a, Kirk's work on the factory tile, checkpoint-and-resume (side session)

Deployed 18:10:44Z (gate **2623**, pin ok; mirror commits `8a0da94` `1194c4d` `849462a` `4a6b125` on baseline
`7ab1f80`). The pre-ship handoff and build-state notes: `Archive\hai-agent-surface-handoff-20261016.md`.
- **Step 1 — live transcript.** `ollama_client` streams every model chunk (text, thinking, tool calls, tool results,
  a `discard` for a cut attempt) to a per-thread sink; `live_runs.py` keeps one event log per run, and the sender's
  `/query/stream` and every `GET /query/watch?cancel_id=` read the SAME log. The page draws it live in the working
  bubble and keeps it, collapsed, under the answer ("📡 Live transcript").
- **Step 1a — server-authoritative turn.** The user's message and a new chat's placeholder title are written on
  ACCEPT (`<chat>.pending.json`, outside the transcript the model reads, so it never reaches the model twice); the
  chat is pinned on accept; `/history` returns `pending` (+ `live`); an open page finds runs started elsewhere via
  `GET /query/live` (3 s poll, visible pages only). The worker starts at accept, so a run happens even if the sender
  never reads a byte.
- **Factory tile (the owner 10-16: the boxes show their rate whether or not an order runs).** `/p20/factory/{host}`: with
  no order and a Kirk call open, Card = Busy, Phase = "HAI · <purpose> for <user> · <model>", Speed = Kirk's tok/s
  (live, else the median `eval_count/eval_duration` of the last 10 answers, the factories' rule); the lane state stays
  in `lane`. Fleet watches and HAI's fleet tool are unchanged.
- **Checkpoint and resume** — § below; chat and Absurd lanes live; overnight not covered.
- **Gate guard:** `tests/test_zzz_no_live_model_calls.py` blocks any real `:11434` request from the whole suite (found
  because this work's first tests made real calls on BigHonker via the auto-titler and reddened the gate).
- **Seamless across a deploy (18:42Z, `7a14687`):** a deploy PARKS Kirk's runs at natural points (`POST /runs/park`,
  wait for `GET /runs/parked` → `all_parked`; runbook step 9), the live feed is restored with the same seq numbers, and
  the page is APPEND-ONLY (the owner: *"the user should never see an edited transcript, it should always just be a
  continuous line"*): streamed text, rendered as it arrives, is the message; no restart notices.
- **Lead effort + agent tile rows, LIVE 19:18Z (`df367e5`, gate 2634, deployed with park):** job-shaped requests
  (think-hard, Absurd, self-eng drives, an admin's self-engineering intent, supervised coding packets — decided from
  the REQUEST in `app._is_job`) on the crown's model+box send think `"xhigh"` (`ollama_client.lead_think`, value from the
  placement table's `roles.crown`). ⚠ On ollama 0.40.5 `"high"` renders MEDIUM; only `"xhigh"` renders xhigh (salted
  render probe: 25 vs 67 prompt tokens). Proven live: think-hard → `LEAD EFFORT think='xhigh'` + thinking streamed.
  **Since 2026-10-19 (`5e10133`) every OTHER call thinks too** — plain "on" for any model whose box's `/api/show` lists
  `thinking` (`ollama_client.can_think`, cached); no flag for a model without it. the owner: *"you probably should just never
  use the thinking off option"*. Tile rows: Speed = write · read tok/s, Step = turn · tool time, Context = used /
  window (filled for Kirk's work; factory-order parity is #34's lane).
- **Step 2 — inline order cards, DEPLOYED 19:32Z (`efe6b71`, gate 2642, with park):** every new order is announced
  at the boundary it crosses (`p20_queue.ENQUEUE_HOOKS`, `tablet_orders.ENQUEUE_HOOKS`); on a chat run's thread it
  becomes an `order` event in the live transcript and is saved with the answer (`orders`), and the page draws a card
  that reads `GET /orders/card/{id}` (the desk's fate + the box's own live numbers; status only) until the order
  ends. The gate now redirects `TABFOUNDRY_SHARE` (it had inherited the live tablet share). **Proven end to end
  20:0xZ on the 3.8:** HAI called `tablet_map`, the `order` event streamed, the card followed pending → done, and the
  saved answer kept the order id (two bugs it surfaced were fixed in `7be72e9`: the tablet card read "pending" for an
  accepted order; a control item that repeats an item now refused at the counter).
- **INTERIM until McCoy/Spock own them (main session + the owner, 2026-10-16):** (a) **job-or-chat for effort** —
  `app._is_job` classifies a request as a job from its SHAPE (Absurd / self-eng / think-hard / an admin's
  `SELF_ENGINEERING_INTENT` / `SUPERVISED_JOB`); the effort VALUE comes from the table's `roles.crown`, the
  classification is a fixed rule in app.py. (b) **quick-first on Mothership's slot** (`_LeadSlot`, `4e2daaa`):
  quick requests queue ahead of jobs and a job hands the card over between model turns. Both match the charter (one
  lead that talks and builds; the live conversation gets priority) but decide in app.py what Spock should; they move
  when McCoy/Spock take the job/quick call.
- **Rolled back — routing is not #37's (2026-10-16):** `1dcdcc0` (deployed 20:54:55Z) sent Auto turns to the lead
  (the 3.8) with an overflow note; the owner via the main session: *"It is not supposed to choose what auto routes to.
  That's the whole point of the menu and Star Fleet command to be determining live."* `30ff407` (20:56:59Z, gate 2666)
  restored the routing block byte-for-byte; no request used the route in its ~2.5 min (notes: no `Auto: the lead`
  line). A second draft placing Auto as the lead tier via `scheduler.place()` was dropped before commit — Auto is
  not "always the lead" (requirement 7).
- **Quick Resume operator pieces + R2, LIVE 20:56:59Z (`30ff407`, gate 2666):** checkpoints between tools; rolling
  saves + save on request; branch from an answer; the stale-workspace refusal (`coding_task` start on a STALE
  `orchestrator-real` is refused, one derivation with the overnight guard: `overnight.stale_reason`) and the Deep Work
  tile's Workspace row — § QUICK RESUME.
- **Steps 3 + 6, the SURFACE only, LIVE 21:12Z (`5358976`, gate 2671) + `cd374b2` (21:16Z, gate 2672):** step 3 —
  Model/Effort are no longer required choices: a pick is the user's PIN (remembered per user on the device, marked
  📌; Auto + Normal = HAI decides, sent exactly as before); step 6 — a request waiting for Mothership is told its place
  and the rule ("N ahead of you — quick requests go first…"). Per-run suspend/resume (§ QUICK RESUME).
- **Proven live 21:12–21:17Z (ClaudeSupervisor):** a run suspended at accept → `RUN SUSPENDED`, `/runs/parked`
  `running 0, suspended [id]`, the chat kept its pending message, no answer for 20 s; `POST /runs/resume` → one
  answer, pending cleared; the finished run appeared in `/runs/saved`; ⑂ branch from that answer → a new chat holding
  exactly user + answer, source untouched. A tool-using run watched INSIDE the container: checkpoint before turn 1 →
  mid-turn save `tools_done=0` (18.00 s) → tool-started mark (18.05 s) → `search_docs` ran → turn-2 checkpoint with
  the tool result saved → done. Served page passes `node --check`. Workspace synced (`df4ece2`): `workspace_lag` =
  in_sync, `stale_reason` None, tile "✅ in sync with live".
- **🌙 Background replaces the page's Absurd option, LIVE 21:33Z (`d060d01`, gate 2680; the owner 10-16: "sounds
  good").** The same agent turn on `/query/stream` with `background: <time choice>`: admitted like the Absurd door
  admitted long unattended work (known choice, else 400; energy-price deferral for non-exempt users, 409); job-shaped
  as Absurd was (interim (a)); routing untouched (no `absurd_max_seconds`, so no forced 27B); the loop deadline is the
  user's budget; the page frees the composer after accept and the bubble keeps its own "⏹ Stop background run"; one
  line to the Absurd webhook when it lands. A budgeted run ended by the clock, the tool-step cap or "no new work"
  says so in the answer ("Stopped at your 4 hours budget: this is where the work stood, not a finished answer").
  `/absurd/start` and the Absurd tile stay for the machine lanes. **Proven live:** unknown choice → 400; a background
  turn whose sender hung up after the meta line → pending in the chat → answer landed 21 s later, nothing pending;
  served page passes `node --check`.
- **Decided by the owner 10-16:** lend the GPU during tool waits → the main session's (#34); the KV state dump → finish it
  (§ QUICK RESUME); the proof order's result in Tolkien's outbox stays (the lab keeps delivered results there — the
  menu refresh reads `outbox\`).
**Proven by effect (ClaudeSupervisor, its own chats):** message in `/history` 2 s after accept; sender and a second
viewer got the same 108 deltas (first ~3 s, result 26.7 s, identical answer); BigHonker's tile read Busy ·
qwen3.5:9b · ~57 tok/s with lane free; a 1,200-word answer killed mid-stream by `docker restart orchestrator` resumed
under the same id ("Resumed after an orchestrator restart") and finished, one copy in the chat, nothing pending. The
phone/second-browser check was done with a second API viewer, not a browser (the owner stopped the browser step).

## Kirk's own runs survive a deploy — checkpoint and resume on every lane (2026-10-16, SHIPPED for chat + Absurd)

the owner, 2026-10-16, asked to kill his think-hard so a deploy could land: *"we should extend the system to all lanes so
that we can ALWAYS checkpoint and deploy. Remember why the system is in place? I don't want to kill and lose the current
conversation."* The factory lanes already pause and resume losslessly (stop slip → deploy → requeue; A1 keeps every
exercise and, since 10-08, every turn: `a1_branch.ReplayChat` over `<exercise>.partial.json`). Kirk's own runs were the
one lane with no checkpoint: the deploy runbook (step 9) says they *"die with the container"*, so every orchestrator
deploy had to wait for HAI to go idle — the waiting the pause-and-redeploy system exists to end.

**Today (measured in the code, 10-16):** chat / think-hard on `/query/stream` — lost; Absurd — marked ERROR
*"interrupted by an orchestrator restart"* (`absurd.py` load); overnight / self-eng — item-level crash-resume
(`overnight.py` 6D `resume_done_ids`), but the item in flight restarts from zero.

**The shape — A1's checkpoint, one deliberate difference:**
- **One ledger per run** (`State/hai-runs/<run>/`): the run's request at accept, and for each `ollama_client.chat()`
  call the run makes, in order: the conversation (messages incl. tool results) written BEFORE every model turn, and
  the call's answer when it returns. The ledger is set on the worker thread like the delta sink (`call_context`), so
  every lane that drives `chat()` gets it from one place.
- **Resume = replay**: on startup the orchestrator restarts each run whose ledger has no end, under the SAME run id.
  `chat()` call k with a recorded answer returns it at once (no model call); the call that was in flight continues
  from its last saved conversation; later calls run live. The run's own code between calls re-derives its flow from
  the same answers. Cost of a deploy: the model turn in flight, re-asked — the unit in flight, as for orders.
- **The difference from A1: replay never re-executes a tool.** A1 re-runs replayed tool calls because each exercise
  starts in a fresh sandbox that must be rebuilt. Kirk's world persists across the restart — the coding workspace
  (the coding-runner container is not recreated), submitted orders, written files — so re-running a patch or a
  submit would apply it twice. The recorded tool results are already in the saved conversation.
- **Per lane:** `/query/stream` (and `/query`) — the request is recorded at accept (step 1a already pins the chat and
  writes the pending message); the resumed run keeps its cancel id, so the page's pending row and `/query/watch` pick
  it up again. Absurd — resumed instead of marked ERROR. **Overnight / self-eng — resumed since 2026-10-17** (the owner:
  "build"): each drive runs under a per-item ledger, the run restarts by itself after a restart with the item in
  flight first (same coding task, drive from its saved turn, the run's own clock, no DM), and a resumed item is judged
  by whether live moved since it started. Detail and the live drill: [[37-Closeout]], deploy runbook step 9.
  (Superseded text: hai-agent-surface-design-superseded-2026-10.)
- **Not covered, said plainly:** model calls that do not go through `chat()` (`coding_review`, `_explain`, a few
  critics) re-run on resume; they are single calls, not loops.

### QUICK RESUME — the target this grows into (the owner, 2026-10-16)

*"I'm inspired by the Xbox implementation where they figured out quick resume by dumping the state of the game to
storage so that the exact live state could be picked up. That's what I'm envisioning for this stuff."* And on where
the boundaries are: *"Sometimes while the model is on its turn and thinking it is waiting on a tool call or a result
from something. I view those as good boundaries because it would be waiting and doing nothing."* He asked for all of
it ("I think all three"). What exists and what is owed:

| piece | state |
|---|---|
| State to storage at every model turn; restore after a restart under the same id; viewers continue | ✅ shipped (ledger + feed snapshot) |
| Park at natural points for deploys | ✅ shipped (`/runs/park`) |
| Quick requests first; a job hands Mothership over BETWEEN model turns and takes it back | ✅ live `7be72e9` — INTERIM (b) above. A quick request does NOT cost the job a full re-read: ollama's runner keeps llama-server's RAM prompt cache, so after the handover the job's next turn costs what a never-evicted turn costs (Mothership 10-16 17:53: 2.2 s @30k / 2.8 s @60k). Full re-reads come only from a RELOAD of the 3.8 (10-16: ~25 reloads from a retry ladder and same-card critique; 10-17: the claim sweep's model swaps, fixed by Spock's live claim) |
| **Lend the GPU while a tool runs** (the model is waiting — the GPU is idle) | ✅ **Resolved by measurement 2026-10-19 (#34 register B9): no new mechanism** — 166 of 173 tool results return in under 1 s and the card idles only in `coding_test` / `coding_checkpoint` (2.6–3.8 % of a coding job's wall time), so the between-turns handover (`7be72e9`) is the design |
| **Checkpoint at tool boundaries** — before each tool of a turn the conversation is saved with `tools_done = i` and a small "tool i started" mark; a resume runs the remaining tools WITHOUT re-asking the model; a tool cut off mid-run is NEVER re-run (no read-only classification exists) — the model gets a note that it was interrupted and its effect is unknown, and decides; a deploy can park between tools | ✅ live `30ff407` (`run_ledger.Slot.save(..., tools_done)` / `tool_started`; tests: crash mid-tool, crash between tools) |
| **Saves + branching** — the owner: save on request, *"a couple of Auto saves that are like a rolling five"*, keep-all for model testing; branches from the whole turn's MESSAGE, like the Claude-app branch button | ✅ live `30ff407`: a finished run's ledger (every call's whole conversation + answer) moves to `State/hai-runs-saved/` (newest `HAI_RUN_SAVES_KEEP`=5; 0 = none; < 0 = keep all); `POST /runs/save` pins a run in flight → `State/hai-runs-pinned/`; `GET /runs/saved[?save=]` (admin) reads them back for starting again from any internal turn. ⑂ **Branch** under every saved answer → `POST /chats/branch` = a new chat holding the transcript through that answer (the source untouched; the answer's text guards the count) |
| **Suspend one run to storage on demand**, free its slot, resume later | ✅ live `5358976` + `cd374b2` (21:12Z / 21:16Z): `POST /runs/suspend` (admin) → the run stops at its next natural point, its thread and Mothership slot are freed, it waits on disk THROUGH restarts (its feed is restored so the chat still shows it waiting); `POST /runs/resume` continues it. A call made inside a tool never stops (its parent stops at its own next point). Absurd gets status `suspended` (not active: the one-big-run lane frees). "On any box" is not built — the run resumes on whatever its own code places |
| **The RAM dump:** the model's KV cache (ollama does not expose it; llama-server can save/restore a slot) — today a resume RE-READS its context (~26 s for 60k tokens at ~2,300 tok/s read) | **MEASURED 2026-10-16 (the owner: "finish what you started"), mechanism PROVEN on BigHonker.** Order `quickresume-state-dump.20261016-1650` (`mothership-build\workspace\quickresume\`, commit `0bd2463`): qwen3.5:9b on the llama-server that OLLAMA ITSELF ships (`lib\ollama\llama-server.exe`, CUDA backend from `cuda_v12`; supports `--slot-save-path`), q8 KV + FA, ctx 14,336, `-fit off`, `-cram 0`; fully resident (VRAM +5,445 MiB for a 5,418 MiB GGUF). Per turn after an eviction, both rounds agreeing: **~6.6k saved tokens — re-read 2.79–2.82 s vs restore 0.10 s + the new turn 0.28 s (save 0.15–0.18 s, 168 MB); ~12.7k — re-read 5.30–5.31 s vs restore 0.17 s + 0.29 s (save 1.6–1.9 s, 275 MB).** After a restore the turn costs what a never-evicted turn costs: only the new tokens are read. Same shape as #36's Tolkien handoff (`tolkien_llm.handoff_body`: restore 0.1–0.4 s, writer re-reads 1 token). ⚠ Arms use exact token IDs: a hybrid's recurrent state cannot be rewound, so a next prompt that diverges from the cache by even the model's own reply forces a full re-read. **Mothership (the 3.8, 30k/60k, its documented recipe): VOID run 10-16** — loaded with 2.6 GB spilled while the owner played a game; re-runs when `mothership.gaming_busy()` reads clear twice. **Decided (37-Closeout § "What the measurements decided"): no engine switch.** The RAM prompt cache already serves every handover (row above), so a DISK dump only pays across a real restart (save 11–32 s, restore 1–2 s at 30k/60k); ollama exposes no slot API, so it would mean serving the long job through a llama-server HAI manages. **#34 checklist B11 is a BUILD since 10-17** (the owner: *"it's the obvious thing to add in whenever we're trying to do a pause and redeploy"*; *"I've been asking for #1 this whole time"*): where a deploy DOES restart the engine — rigby's llama-server under a Tolkien deploy — dump the slot holding the half-written answer and continue it from the exact token. Measured on rigby (register B11): continue-from-dump is LOSSLESS (384/384 tokens) for greedy decoding without MTP; the sampler's random stream (#2) and the MTP drafter's state (#3) are not in llama.cpp's slot file — two engine patches owed. The register row is the live state |

### QUICK RESUME — window pickup, Flash-Next, the hot start (built and proved 2026-11-05)

The rows above are true of ollama's engine and rigby. Flash-Next runs on Strata, a separate engine, so register "B11b CLOSED" did not cover
the model jobs actually run on. the owner's bar (2026-11-04): *"the bit identical resume that we spent ages building for the other stuff"*. All three
are now built and proved; results and method in [[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] § Status, operations in
[[RUNBOOK-Ollama-Host-Operations]] § WINDOW PICKUP, § FLASH-NEXT QUICK RESUME, § THE HOT START.

- **A restart of HAI picks its windows back up** (first named as #5 of `Claude Home\research\quick-resume-survey-20261021.md`). The engine never stops.
  **So deploy under a live run, with no park and no waiting** (the owner, 2026-11-05: *"if we actually built it correctly we should be able to deploy without disturbing runs"*). Done that morning under a 27B job at max effort: orchestrator gone 26 s, `CHAT WINDOW picked up`, `TOOL LIST kept across the restart: 36 of 36`, the turn `reconnected … not asked again`.
- **A run that ENDED in an error continues from its last checkpoint, never from the start** (since 2026-11-05): `POST /runs/resume {user: <admin>, run_id}`. A finished run's ledger is kept as a save (`State\hai-runs-saved`, the rolling five); `run_ledger.restore_save` puts it back and the lane's own resume continues it before the step that failed. A run that finished its answer is refused. Why: a 3.6 h job died on a crashed tool at its final step and the only option was a full rerun.
- **Flash-Next's own state across an ENGINE restart.** Session files on F:, identical answers across real restarts.
- **THE HOT START.** the owner, 2026-10-25: *"for cold loads we could have like some sort of pre-saved state that the models load up? That way they're
  not loading as many things when they're doing a cold load? Essentially like a customized quick resume"*, on the fastest drive of each box; on
  2026-11-04 he named it the hot start or primer: the model starts from a state with the system prompt already read, and appends the new part. It
  saves what every chat shares, which today is the always-offered tools (~2,300 tokens), because the tool block renders first and is chosen per
  chat. **Still the owner's choice:** a short fixed tool list plus a find-a-tool tool would let the whole start be shared, on every box (the design doc's
  option 3). The earlier form, *"prep a state for McCoy's model to load"* (2026-10-21): the survey (#2) found a saved preamble buys McCoy little.

## A WORKFLOW HAI must handle without gaps — NOT the default, likely a V3 shape (the owner, 2026-10-19)

the owner described a way he foresees working, and was explicit that it is **not** the default (*"I think that sort of
assumes that there's only one user at a time"*) and may be *"a V3 shape and we're currently working on the two of
HAI"*: **chats are high-priority work orders and real work is slower orders** (*"one would expect for a chat to come
through quickly and one would expect for actual work to come through slower. That's sort of the mixed approach that the
menu is supposed to help"*). While the 3.8 is doing a long job, **a smaller model on another card picks up the chat
function and the user still has an interactive chat** — or **the 3.8 sends an order to another model on a different
card** — because the lab cannot spin up copies (*"I don't have unlimited hardware for things to just spin up a bunch of
copies of models. As fun as my house is, I am no Data Center"*). That is what the delegation tests were for: delegation
*"with the context of what the lab is supposed to be"*, not whether a model delegates on its own. Requirement for V2:
**this must not hit weird gaps** — the chat-as-order path, priorities and hand-back must not make it impossible. It is
not a target to build now, and the 10-19 quick-tier tests (small models answering ALONE) stay valid for the lab as it
is. Not yet tested in this shape: a small model holding the chat with the busy job's state in front of it.

## Pinning an exact model AND box — Model-menu rows, "this chat", `/on <box>` (admins and power users; LIVE 2026-11-04)

the owner, 2026-11-04: *"Let's actually do 2, 3 & 4"* (menu rows, a per-chat pin, a one-message command) and *"I want it to
just be admins and power users that have routing like this, as we said"* — checklist D2 ("pin model / effort / box"),
D2a (an exact pick is a pin the weighing may only inform). Code: `row_pins.py`; serve path `app._turn_pin` /
`app._pinned_brain`; tests `tests/test_row_pins.py` (gate 3368).

**Who.** `config.ADMIN_USERS` plus `config.POWER_USERS` (env `POWER_USERS`, comma-separated, empty by default — add a
name there to give someone pins; it grants nothing else). Everyone else sees the Model menu exactly as before, and a
pin they send is refused with "Pinning a box is for admins and power users", never silently placed elsewhere
(checked live 2026-11-04 21:1x for both a `/on` message and a page pin).

**The choices are the priced menu's own rows** (`Datasets\Project34-Menu\policy.json`, read when asked — a box or model
the menu measures appears with no list to keep): the agentic crown first (`roles.crown`), then every model-and-box
row of the `chat-c1`, `work-w1` and `coding-f1` families in best-quality order, one per (box, model). The tablets are
not pinnable (Tolkien places them, checklist C5). Row id = `box/model`; the page shows e.g. "Qwen 3.8 27B · Mothership".
`GET /models/pins?user=` lists them (empty for anyone else) — eight rows on 2026-11-04.

**Three ways to set one, most specific first:**

| | how | kept |
|---|---|---|
| this message | start it `/on <box> <message>`, or `/on <box>:<words> <message>` to pick another row on that box (`/on mothership:27b …`) | that message only (a resumed turn keeps it: `Q.pin_once`) |
| this chat | pick a row in Model, tick **this chat** | `<STATE_DIR>\user-prefs\<user>.chatpins.json`, `GET`/`POST /prefs/chat-pin` |
| the page | pick a row in Model | the account pin (`/prefs/pin`, value `row:<box>/<model>`) |

**What a pinned turn does.** It skips McCoy and Spock's weighing, runs on that box at that row's model, thinking
setting and measured window, with HAI's full toolset (minus the escalation tool), and is **never rerouted** — a box
that cannot be reached or a Mothership that will not wake is SAID, and the call is not moved. A busy box (a factory
claim, a render, another model's live job) is **waited for** on status with one notice — the user can Stop; there is no
give-up clock (D4). Mothership pins still queue in its slot (the owner first). A pin keeps its EXACT model: the job rule
that swaps a job call on Mothership's 27B to the crown (`ollama_client.lead_model`) is off under a pin
(`call_context(exact_model=True)`). The user's own Effort pin still wins over the row's thinking setting.
**"Full toolset" is `tools.HEAVY_TOOLS`** — the brain's pool plus the coding tools, without the Mothership escalation —
and a pinned request that shows coding intent gets the same coding brief the lead gets (`app._work_briefs`, one
derivation; the lead's identity handoff is not added). Until 2026-11-05 16:26 the code handed a pin the brain's pool
minus the escalation, which has NO coding tools: a pinned 27B given a coding job said it had no tool to create a file
and stopped after two minutes (`0b54ca0`, `tests/test_row_pins.py` ServingAPin). A pinned turn is still ONE chat call
on that row, not the lead's multi-pass path.

**rigby** is served through Tolkien's chat window (`chat.window`) with that row's own launch fields (model, ctx, gpu,
build, …) and the full toolset — the window forwards `tools` to rigby's llama-server. It needs rigby's window on for
the account (`State\rigby-window.json`); otherwise the pin says so. rigby's row runs at its menu `ctx` (32,768), so a
long job there compacts sooner than on Mothership.

**Proven live 2026-11-05:** pinned turns that answer, as ClaudeSupervisor (an admin, so it may pin), on all four
factories — the mid-turn park proofs ([[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] § "The mid-turn dump on
the fleet boxes", row LIVE PROOF). A pinned coding JOB that uses its tools: the 27B AssBench rerun started 16:27
(`research\ltt-jarvis-prompts`); its result is recorded there.

## The right-tools set — coding_fetch, coding_render, coding_look: a job sees what it made (LIVE 2026-11-05)

the owner, 2026-11-05, after grading the LTT Labs cookie cutter a draw: *"ideally I should be using the 'right tools' set"*,
*"we want to do it because it's right, not because it's what LTT labs did"*, on a shell: *"I feel like a shell is
redundant?"*, on a helper model that describes pictures: *"a weak middleman option"*. What the blind job got wrong: the
wrong logo (it could read only TEXT from the web), a part that was a mirror image of the logo, a file no slicer opens
whose own 33 tests passed (its reader had the same wrong header length), and 1 KB of path data retyped by hand with a
piece dropped. Three tools, all in `tools.CODING_TOOLS`, so every path that has the coding tools has them (the lead,
a supervised job, an exact pin):

| tool | what it does | where |
|---|---|---|
| `coding_fetch` | a PUBLIC web file saved into the workspace byte for byte (an image, an SVG, data); never retype fetched data | `lanfetch.fetch_bytes` (the public web door: every hop checked) → runner `/files/create_bytes` |
| `coding_render` | an `.html` / `.svg` opened in headless Chromium (WebGL in software) at the size asked, with clicks, drags, scrolls, keys and waits, one screenshot after the load and after each step, plus the page's console lines, script errors, failed and REFUSED requests, and `varied` per screenshot (0.0 = one flat colour); an `.stl` read by the format's own rules, measured (size, triangles, closed or not, distinct heights), with views from above, below, the front and 3/4, each captioned with its axes so a mirrored part shows; or `url=` a public web page (screenshot, title, text, its images largest first) | runner `render.py`, `/render`; pictures land in `renders/` beside the file |
| `coding_look` | a picture in the workspace goes INTO THE CONVERSATION of a model that can see, as one marked message after the turn's tool results; a newer picture of the same file replaces the older image. A model not set up to see is told nothing looked. No describing helper | `ollama_client.can_see` / `offer_picture` / `_add_pictures`, runner `/files/read_bytes` |

**Hard checks first, eyes second.** The console, script errors, `varied`, and the STL format check and measurements are
facts in the tool result for every model, seeing or not; the picture is for what only eyes catch (the right way round,
nothing torn).

### THE GOOD HABITS, SUPPLIED BY THE TOOLS — how the lab compensates for a smaller model without a per-task rig (design 2026-11-15, piece 1 built)
**the owner, 2026-11-15:** *"It's not as fun to just be like 'you need Qwen 3.8 for everything' … the models in the lab are
capable of things"*; *"I don't want to have to set all this up for every time. We need to look for inspiration from
flash next and 27B in how they worked … transferrable lab architecture that can be reused in shape"*; and the
condition: *"make sure we aren't taking away the ability of models to actually look on their own … maybe they want to
do a final look before handing it in and catch something the render sheet didn't catch"*.

**What the stronger models do, measured on their saved runs** (`run_habits.py`, same measures for every run; one run
per model per task; the 27B's AssBench record predates the eyes):

| habit | Flash-Next AssBench (run-w) | 35B AssBench (bh-e) | 27B cookie (run-v) | 35B cookie (box2-y) |
|---|---|---|---|---|
| looks at a frame AFTER an interaction | 52 of 93 | 6 of 58 | n/a | n/a |
| looks at a reference picture | 1 | 0 | 8 of 19 | 0 of 9 |
| renders with scripted interactions | 95 of 160 | 25 of 75 | n/a | n/a |
| whole-file rewrites : patches | 15 : 220 | 44 : 34 | 1 : 24 | ten files v1..v10 |
| test runs / tests of its own | 4 / 10 | 2 / 0 | 53 / 8 | 33 / 1 |

Four habits, none tied to a task: exercise the thing and look at what the exercise did; change one thing instead of
starting over; look at the target before building; write checks of your own and run them. No run, strong or weak, used
the decision, note or checkpoint tools, so the shape does not lean on them. ⚠ A habit may be a result of ability, not
its cause: whether SUPPLYING a habit lifts the 35B is a test, one piece at a time.

**The shape (agreed with the owner), all of it on tools every job already crosses, nothing set up per task:**
1. **The tools do the habit by default.** BUILT, `tools._coding_render` (Kirk, so every draw path gets it: the runner,
   the GPU boxes, rigby): a page the model scripted no steps for is exercised with the lab's standard set
   (`standard_exercise`: a drag across half the width, a scroll of 300, a click in the middle, ArrowRight, a wait; the
   values are the commonest ones the saved runs scripted), and any render with two or more pictures also returns ONE
   sheet of all of them (`_with_sheet`: `renders/<name>-sheet-<hash>.png`, 960 px wide, numbered as in `shots`), handed
   to a model that can see as one picture after the turn's tool results.
2. **One meter of how the run is working.** BUILT, `run_habits.Habits` (Kirk `56cc912`): counted for EVERY run as its
   tool calls go by (whole-file writes and edits in place per file, renders, which pictures of the last render were
   seen, looks at the load frame / after an interaction / at a reference / at a sheet, pictures listed on pages it
   opened). Where the `meter` switch is on, the tool's own result carries one sentence at the moment it matters:
   "this is whole-file write 3 of page.html (edits in place: 1) … to change one thing, coding_patch it"; "this render
   made 6 pictures and none has been looked at yet"; "2 pictures of that render, taken after an interaction, have not
   been looked at"; "you opened pages that list 95 pictures and have looked at none of them" (once). Whether the model
   can see is learned from the run's own looks, so a model that takes no pictures is never told to look.
3. **The finish gate and the deck read that same meter.** BUILT. Gate (`finish_look` switch, jobs only, after the
   tests' rule and on the same count of refusals): a job that has rendered may not hand in while its files changed
   after the last render, or while a seeing model looked at nothing of it: `FINISH REFUSED: … Render it once more and
   look at the result (the sheet, or any frame with coding_look)`. That IS the last look before handing in; the
   model's own look at any one frame satisfies it. Deck: a running job on `GET /hai/jobs` carries `habits`, the
   meter's one line, and the run's end logs `HABITS host=…`. The meter's state rides in the loop's checkpoint, so a
   restart loses none of it (`tests/test_run_habits.TheMeterSurvivesARestart`).

**WHERE IT APPLIES: everywhere a render is made, by what the tool is, never by which task or model** (the owner 2026-11-15:
*"the lab needs to know when its appropriate to use tools. It's another render is it not? … dont get locked into
specifics like 'its not Assbench'"*; *"make sure this is applied around the lab, not just in one spot for the 35B.
Cross cuts"*). Inventory, by grep of all three code mirrors, 2026-11-15:

| what | where it lives | covered |
|---|---|---|
| every render | ONE caller, `tools._coding_render`, in front of all three draw paths (the runner in software; a GPU box's `render_order.py`; rigby's `tolkien_page.py`), which share ONE `render.py` (the runner's and the foundry's copies hash the same; Tolkien's image copies the foundry's) | sheet + exercise for a page, an STL's views and a web page, on any box |
| every model | the picture goes through `offer_picture`, which asks `can_see` for the serving model: ollama, Strata (Flash-Next), rigby | a seeing model is handed the sheet; a blind one still gets the file and the hard facts |
| chats and jobs | the meter is counted in `ollama_client.chat()`'s one tool loop, which serves every chat turn and every job on all four factories | counted and said back for both; the last-look gate is for jobs, where the finish gate already lives |
| NOT covered, on purpose | the A1 benchmark harness (`hard-bench\a1`, Inspect) and the medical reviewer run their own tool loops and have no render tool; A1 is a measured protocol and is not to be changed by the lab's help | n/a |
| where the operator sees it | the live status every tool step writes (`Tool step 41: coding_patch · 12 renders, 9 looks (6 after an interaction); page.html: 3 rewrites, 20 edits`, `run_habits.Habits.short`), which the chat page and the HAI-Thinking tile already show; the longer line on a running job in `GET /hai/jobs`; `HABITS host=…` in Kirk's log at the run's end | shown since 2026-11-15 10:42 (Kirk gate 3,934); seen live on box2-z and bh-f, each with the counts it had before that deploy's restart |

Proven live: the 27B on Mothership (a page, job `8b6e7defe195`), rigby's 35B (a page, box2-z's first render), BigHonker's
35B (an STL, bh-f, see the journal). Not yet proven live: Flash-Next, and a blind model.

**Nothing is taken away.** Every frame is still saved at full size under `shots[].path`; `coding_look` is unchanged and
any frame can be looked at as often as the model likes; the sheet's own note says it is small per frame and for gross
faults, not detail. The model's own steps are used exactly as given.

**The switch.** `<STATE_DIR>\hai-habits.json`, read on every call, OFF when the file is absent:
`{"render_sheet": true, "auto_exercise": true, "meter": true, "finish_look": true}`, each habit its own key, so a test
can turn one on at a time. `"except_chats": ["<chat id>"]` leaves the chats named with the tools they started with (a
measured run in flight when the switch is turned); `"only_chats": [...]` turns the habits on for the chats named and
for nothing else (a live proof, or one arm of a test). Deployed OFF on 2026-11-15 so the cookie rerun in flight (bh-f)
kept its tools; turned on by writing the file, no deploy. A sheet costs one picture (about 350 tokens) per render.
The meter COUNTS whether or not anything is switched on (it changes nothing the model sees), so every job's deck line
and end-of-run `HABITS` line exist from 2026-11-15.

**Safety.** `netguard.py` is the ONE "public internet only" rule: `lanfetch` asks it per hop, and the runner's browser
asks it for EVERY request a rendered page makes, so a page a job wrote cannot reach the home network or the tailnet
(file:// only inside the job's workspace; WebSockets and service workers off). The browser is in the runner image on
purpose (Playwright 1.64.1 + Chromium; the owner's 11-04 line: a job may change its workspace freely, changing the runner
is his call), which grew the image from 2.2 to 3.6 GB.

**Who can see.** An ollama model that declares `vision` in `/api/show` (the 27B, the 9Bs, the 35B A3B MTP build). A Strata model only when
its own box declares `sees.<model>` (read by the factory off the serving plan's setup: `p20_foundry._plan_vision`);
then HAI's Strata path sends the pictures as `image_url` parts. **Flash-Next on Mothership sees since 2026-11-05 19:02.** Strata 0.1.45's setup leaves images off on Windows with an AMD card, so the lab built Strata's own CPU image encoder on the box (order `flashnext-strata-vision-build.20261105-1847`, payload `workspace\flashnext\build_strata_vision.py`: 145 s, READY in 1.4 s, about 1 s a picture on 8 threads) and the serving plan carries Strata's `vision` config section, written in at every start (`strata_arm.apply_serving`; plan `strata-serve-q2_0-v045-vision.20261105.json`). Order `flashnext-strata-vision-on-built.20261105-1857` read two colour-band pictures whose answers are each other's reverse; text speed and VRAM unchanged. Through HAI, pinned Flash-Next passed the eyesight check 4 of 4 (chat `aaa1bb814df5`, one run). A picture is up to 300 tokens there (the CPU encoder's default); [[Qwen3.8-Flash-Next-on-Mothership]] has the serving detail.

**Proofs.** Runner suite in its own image `Ran 116` (a real page drawn, clicked, heard, and kept off <router-address> and
`file:///etc/passwd`); orchestrator gate 3474 (`tests/test_right_tools.py`); a live pass through the real dispatcher
(`tools_decoy.py`, 2026-11-05 18:13); and the eyesight check through HAI itself, pinned to the 27B
(`look_test.py`: a reference and four pictures under blind names - a copy, a mirror image, one with a piece missing,
a blank): **4 of 4** in 83 s (chat `f849e8226963`; HAI's notes show the five pictures handed to the model). The
repeat's result is in the day's journal.

## Build order (cheapest step with the biggest felt difference first)

1. **Token streaming to the page** — forward the thinking / text / tool-call chunks through `/query/stream` (the ndjson
   transport already exists) and render them live in the transcript view. Nothing else changes.
   **1a. Persist the user's message when the request is ACCEPTED, not when it is answered (found 2026-10-16).** Today
   `_persist` writes the user turn, the placeholder title and the auto-title in one step at the END of the turn
   (`app.py` `_persist` → `history.append` → `_auto_title_worker`); the browser draws the user's own bubble locally, so
   only the device that typed the message sees anything until the answer lands. A request sent through the API (a
   delegate speaking as the owner, 10-16) or opened from a second device shows an EMPTY chat for the whole run — 20+ minutes
   on a project-sized request. the owner's requirement, 10-16: *"the final intended shape is that it feels seamless, like how
   yours is. I can go from phone to computer and even have both open at the same time and they show the live stuff
   coming in."* So the transcript is SERVER-authoritative from the first byte: write the user message + placeholder
   title on accept; every open page (phone, desktop, both at once) subscribes to the same run (`/query/reattach` already
   survives a refresh — extend it to any number of viewers) and renders the same live stream; the auto-title still
   upgrades the placeholder when the answer lands. Ships with step 1 — the same transport carries both.
2. **Inline order cards** — orders the conversation starts appear live in the transcript (facelift Piece C).
3. **One window, always an agent** — retire Model/Effort as user choices (override kept), the lead from A1.
4. **Triage** — the 07-25 retrieval signals + the advisor; unsure ⇒ the 3.8.
5. **Placement advice** to Spock and Tolkien, validated deterministically.
6. **Multi-user** — overflow to the next box with its caveat, placed by Spock (Phase 7), the owner first.
Each step ships through the orchestrator deploy procedure (its gate, `node --check` on the served script — the UI is a
non-raw Python string, see the CLAUDE.md doctrine row) and is usable on its own.

## Open for the owner (preferences, not blockers) — ALL ANSWERED from the library (2026-10-16, #37 closeout)

- ~~Cold lead vs warm lead while he is active~~ → **warm while in use, cold when idle**: the card unloads every model
  after 5 min idle (the owner 2026-10-07: *"It's supposed to unload all models when it's not being used"*); the cold cost is
  measured (43–45 s load) and accepted (the owner 2026-10-04: *"30s waking up really is not bad"*); the stream now SAYS a
  cold wake/load (#37).
- **How an on-demand supervised job enters:** not through the self-engineering backlog (only a human arms that); as a
  `SUPERVISED LOCAL CODING JOB` packet in ClaudeSupervisor's thread, run by the work loop (finish gate, compaction,
  talk-while-working; optional Background budget) — [[RUNBOOK-HAI-Coding-Escalations]] § "THE VEHICLE NOW".
- **Whether decomposed mode survives:** no — RETIRED with Absurd's coding vehicle 2026-10-05 (the owner; RUNBOOK-HAI-Coding-
  Escalations § "Decomposed mode"); decomposition lives on as #34's S8 shape for Kirk/McCoy, not as a mode.

**The work-loop redesign questions (Claude listed them 2026-10-05 21:23) — most are ALREADY ANSWERED by the owner's
requirements above or by standing lab rules; checked 2026-10-07 against his words, not left as questions:**
1. **Talking to it while it works** → requirement 2: it behaves like Claude Code (*"I almost always have you in
   Claude code"*), so it takes messages mid-task and can be interrupted/redirected. ANSWERED.
2. **What it may do without asking** → standing rule: promotion and deploys are never HAI's act; host actions go
   through the registered `host_admin` broker ([[RUNBOOK-HAI-Host-Admin-SSH]] / hostops). An approve step in the
   transcript is the proposal for anything outside that. ANSWERED by existing rules.
3. **Where it works** → registered workspaces only ([[04-CODING-RUNNER-STATUS]]), per-user identity and consent
   (`person_record.check_use`, the HAI user identity map). ANSWERED by existing architecture.
4. **Long sessions** → runs are server-authoritative and survive a refresh (`/query/reattach`, above). Summarising
   when the 3.8's 122k context fills has no recorded decision — **default: the agent summarises its own context
   and keeps the full transcript on the server** (Claude Code's shape, requirement 2). Proceed on that default.
5. **"Done" for non-code work** → the library rule: a claim is done when its tool trail shows it read what it cites
   (doctrine "pin the behaviour, not the fact"; the HAI dark-day drill). ANSWERED by existing doctrine.
6. **Handing off to Claude** → standing rule: HAI never calls Claude on its own (Claude usage is gated by the owner).
   ANSWERED.
7. **Multiple users** → requirement 6 (smart routing for several users); privacy between users is the existing
   per-user identity/consent model. ANSWERED by requirement + existing architecture.
8. **How we know it's better** → the charter's "beat it in ACTUAL lab work" and A1 as the measure (the owner 10-04:
   *"That's what we have the A1 agent stuff to help with"*). ANSWERED.
