# #34 Phase 7 — the ONE placement rule: priced menu → Spock's policy table (design, started 2026-10-07)

**Why this doc exists now (the owner 2026-10-07):** *"every morning we do this round up and you always tell me we're a 'day
away' from wrapping up this section of work, and getting properly into phase 7."* Phase 7 had no design and no build
plan, so it lived as a promise that every new A1 arm pushed back. The charter never made Phase 7 wait for A1: its
"Still to do" order is (1) Phase 7, (2) area C, (3) **the sub-agent measurements that FEED it**; DoD row 6 says Phase 7
"starts when the menu is full", and the GPU menu was declared complete 2026-10-03 ([[34-CHARTER]] § "Current phase").
So Phase 7 starts here; A1/A1-team/S10/advisor rows plug into it as they land.

## What Phase 7 must produce (the charter's own words)

- **The rule (§52):** *"Use the least expensive source of compute and intelligence that reliably satisfies a task's
  quality + time requirements, while accounting for verification, energy, resource contention, and escalation."*
- **Its axes:** family · checkability · urgency · energy · residency · fallback when busy ([[34-CHARTER]] end-state
  diagram, "Spock — ONE placement rule (Phase 7 + area C)").
- **Recursive:** a job and its sub-tasks are placed the same way — sub-agent value IS the Phase 7 policy applied to
  sub-tasks (§ "How results shape action" 3).
- **Acceptable is per factory:** GPU fleet = the one-shot answer; Fellowship = what the set reaches with a selector
  production has; a final-answer verdict is not a sub-agent verdict; dominated-but-acceptable = a fallback with its
  caveat; not acceptable = not placed there. Price = **per solved** (failures charged to the solves); ceiling = 10× the
  box's cheapest cold solve.
- **Authority stays deterministic** (Spock decides; the Phase 8 advisor only proposes).

## Inputs (all exist today)

| input | where | what it gives |
|---|---|---|
| the priced menu | `Documentation\Datasets\Project34-Menu\standings.csv` (+ `menu-cells.csv`, `best-settings.json`) | per box × job family: quality (with spread), s/solve, J/solve for every measured setting; best settings |
| hard placement facts | `scheduler.py` (`CARDS`, `KINDS`, the eleven policies, Plex gate, claims, `gpu_health`, residency) | what CAN run where right now |
| role rows | A1 / A1-team / S10 / advisor reductions (`workspace\a1\summary\`, campaign reduces) | the lead's agentic price; each helper's sub-agent price; the reviewer; the advisor |
| what actually happened | `State\placements\YYYY-MM.jsonl` (since 2026-10-07) | every decision with its candidates and reasons — the check that the table changes real choices |

## The design (v1)

1. **A compiled policy table, not a new engine.** `menu_policy.py` (hard-bench, beside `menu_standings.py`) reads
   standings + best-settings and writes `Project34-Menu\policy.json`: for each **job family** (coding-f1/f2, work-w1,
   chat-c1, tool, vlm/vlm-2x; A1-agentic and sub-task families as their rows land), an ordered list of
   `{box, model, settings, quality, quality_low, s_per_solve, j_per_solve, caveats}`, under three lenses — time,
   cost and quality (the owner 2026-10-07: *"there's three factors speed cost, and quality"*): `fastest` (s/solve),
   `least_energy` (J/solve) and `best_quality` (the 95% lower bound). Prices are per SOLVE (failures charged to the
   solves). A setting slower than 10× the family's fleet-fastest solve — the Order Spec's price line applied across
   the fleet — is priced out of `fastest` (*"nobody wants to wait 1000 seconds after sending a simple chat"*); the
   background lenses keep it. **What the three factors MEAN (the owner 2026-10-22, correcting a session that had redefined
   them):** **SPEED** is the time the user waits, from the request arriving to the answer on their screen — a cold
   load, a re-read and a wake all count here — AND the time until the model first shows something (*"We are counting the
   thinking box that is piped in as interactivity so that the user that is waiting on a chat is still seeing stuff. It
   is not only the time to answer final. It is also the time until the model starts thinking and shows stuff."*); users
   differ: *"Some users will be more turned off by a long time overall, but other users will be fine if a long time is
   going on and they're able to see the thinking process as well."* **COST** is literal electricity (*"Expense has
   always been literal electricity expense for cost"*) — J/solve, never load time. **QUALITY** is the solve rate's lower
   bound. The menu prices total time per solve today; time to first visible output is measured only for the Fellowship
   chat rows (CHAT1, below) and a few lead probes. **The three are weighed TOGETHER per request** (Starfleet, with
   McCoy advising triage and placement) — never one lens per request or per tier, and never a fourth lens to stand in
   for the trade-off (a 2026-10-22 attempt did both and was rolled back). Every correction, item by item:
   [[34-Starfleet-Routing-and-HAI-Controls-Do-Not-Collapse-Checklist]]. Which lens a request uses (urgency, deadline, the energy trough) is area C's input,
   via the existing `energy.py`, not decided by the table. Dominated-but-acceptable entries stay as fallbacks with their caveat;
   unacceptable ones are listed as REFUSED with the reason, never silently dropped.
2. **Spock reads it.** `scheduler.place()` keeps every hard rule it has (fit, health, Plex, claims, residency, pins) and
   uses the table only to ORDER the hosts that pass them, for a request that names its family + urgency. Unknown family
   or missing table ⇒ today's behaviour unchanged (fail-open to the existing policy, logged). One derivation: the table
   is generated from the menu, never hand-edited.
3. **Recursive by construction:** a delegated piece is a request with a family + urgency, placed by the same call
   (the A1-team delegators already go through the desk — `a1_delegate.Delegator` / `RigbyDelegator`).
4. **Verified by the placement record:** replay the recorded decisions through the table; report where it would have
   chosen differently and why. That diff is the owner's review surface and the advisor's placement items (Phase 8).

## Build steps (each small and usable on its own)

1. `menu_policy.py` + tests → `policy.json` from the current standings. **DONE 2026-10-07** (`hard-bench\menu_policy.py`,
   `test_menu_policy.py` 9 OK, incl. a live-join positive control). Every rule is an existing one:
   - **Keyed by the TRIAGE TIERS of [[HAI-Agent-Surface-Design]] § "The design"** — the advisor's own labels
     (`advisor_tasks.TIERS`: tool · fellowship · 8gb · lead): triage picks the tier, the table orders boxes within it.
     HOME boxes first (fellowship = rigby + tablets, 8gb = BigHonker + Softserve, lead = Mothership), then the other
     tiers' boxes as OVERFLOW with a caveat. `tool` = plain code, no model.
   - **Acceptable = the stop rule** ([[34-CHARTER]] § "How results shape action"; [[34-Hard-Bench-Order-Spec]] § "When an
     arm stops being filled"): out only if it cannot fit, is priced out (per answer or per correct answer, 10× the box's
     cheapest cold solve), or the owner ruled it out — read from the filler's own `menu_fill_auto_holds.json` and the owner's
     `menu_fill_hold.json` through `menu_fill.cell_hold` (one derivation; 172 verdicts stop 132 standings rows).
     **Thinking OFF is acceptable only on a quality TIE (added 2026-10-22, the owner's rule of 2026-10-19 — *"you probably
     should just never use the thinking off option… we only win by having a few seconds saved"*):** an `off` setting
     is refused when the same model on the same box in the same family with thinking on has a Wilson lower bound above
     off's score (`menu_policy.think_off_refusal`, 3 tests). Before it, `fastest` chose thinking-off on every chat cell
     (cheaper per solve) — the opposite of `ollama_client.lead_think`, which already turns thinking on at call time.
     First regeneration: 60 rows refused across families (tool 19, chat-c1 15, vlm-2x 10, vlm 7, work 5, coding 4).
     "Dominated is NOT a reason to stop." No floor of the table's own: the first draft's 90%-of-the-champion bar made
     chat Mothership-only, and the owner: *"Surely general chat could go on the other boxes and models too."*
   - **Mothership is not reserved** (corrected 2026-10-07, the owner: *"The card is not supposed to keep the 3.8 loaded. It's
     supposed to unload all models when it's not being used"*): the card unloads after 5 min idle, so every model there is
     priced and placed like any other, carrying a residency caveat (a cold load; a swap only if the 3.8 is warm) — a
     COST for step 4's residency input, never a refusal. The first draft refused non-lead models there, which hid
     Nemotron 30B and qwen3-coder:30b from the table.
   **Readout (fastest lens, home picks):** chat-c1 8gb → qwen3:8b on Softserve / BigHonker (65%, ~2 s per solve), lead
   → the 3.8 (88%, 4.7 s); coding and tool → qwen3:8b on the 8 GB boxes (90–93%), rigby's small Qwen2.5 on the
   fellowship tier; vlm 1x has no lead home (the owner's ruling: 1x vision is void by method for the 27Bs). The C1 exam priced
   the tablets out of chat (10% / 5% and 1,300 / 5,450 s per solve, 2,000-word documents) — so measurement 3 of
   [[HAI-Agent-Surface-Design]] was BUILT 10-07: **CHAT1**, HAI's own 7 simple-chat requests on every tier
   (`reduce_chat1.py` → `Project34-Menu\chat1.md`, family `chat-turn` in this table). **Simple chat now has a
   small-tier home:** rigby Qwen2.5-1.5B **35/35, 1.9 s per solve, first token ~0.3 s**; tablets Qwen2.5-Coder-0.5B
   class B 20/23, 8.8 s (each turn loads the model with `llama-cli` — a COLD-turn price), both inside the 15.9 s
   interactive line. Both are the design's simple-chat tier: Tolkien administers the Fellowship and picks the node,
   rigby or a tablet ([[HAI-Agent-Surface-Design]] § "The design"). **Warm tablets DECIDED 10-07 (the owner, Option 1):**
   Tolkien keeps a resident llama-server per node (`tolkien_llm` `warm` arm) and deals turns to whichever node is free,
   many chats at once — first measured Coder-0.5B: class A/B/C median 1.35 / 1.25 / 34.8 s, first token 0.56 / 0.38 /
   1.41 s (the other three models re-running as `chat1-warm-*.20261007-1642`); the 8 GB tier
   qwen3:8b 21/21 at 1.6–2.0 s; the lead (3.8, thinking off) 21/21 at 3.0 s. Tablet J is unmetered (sorts after
   metered rows). The tablets ARE metered: they charge from rigby over USB, so they sit on rigby's plug (the rack's smart plug,
   rigby ≈ 41–46 W idle, ≈ 97–101 W working — [[34-Hard-Bench-Order-Spec]] § "Rigby (S5r) energy"). What is missing
   is the tablets' own marginal: a plug hour (the Govee app's CSV export) holding tablet work with rigby's lane idle.
2. Replay check against `State\placements\` (read-only) → the diff table. **DONE 2026-10-07** (`hard-bench\
   replay_placements.py` → `Project34-Menu\placement_replay.md`). First replay, 52 decisions (10-07 09:10–16:10):
   **49 pinned** (menu orders naming their host — the table orders hosts, never overrides a pin), **2 unpriced**
   (`review`: no job family yet), **1 free** (an interactive chat → BigHonker qwen3:8b — the table agrees). Finding for
   step 3: Spock's candidate list for that chat held only BigHonker + Softserve — **rigby and the tablets are not
   chat candidates in `scheduler.place` at all**, so the fellowship tier's home (rigby 1.5B, warm tablets) cannot be
   chosen until step 3 wires the tier through Tolkien. Re-run the script as the record grows.
3. Spock consumes it behind the family/urgency request fields (orchestrator deploy, gated), fail-open. **BUILT +
   LIVE 2026-10-07** (gate 2572, container `scheduler.py` `d78dc922`): `scheduler.place(kind, …, family=, tier=,
   lens=)` — the hard rules (fit, health, Plex, claims, training, the caller's pins) decide who is eligible, THEN the
   table re-orders only those hosts (never adds, never removes); `lens` defaults to fastest when interactive, else
   least_energy; the reason lands in each host's `why` and the placement record gets `policy: {family, tier, lens,
   applied}`. No family / no table / unknown family = today's ranking unchanged (`tests/test_scheduler_policy.py`, 8).
   `config.POLICY_TABLE` = `/docs/Datasets/Project34-Menu/policy.json`. **Chat consults it since 2026-10-22/23**
   through the per-request weighing (area C step 1, below); a single-lens attempt (2026-10-22) was rolled back first for
   collapsing the matrix ([[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] § "CHAT PLACEMENT FROM THE MENU"). **Two routers
   (the owner 2026-10-07):** Spock's choice is fleet-level — Mothership, an 8 GB box, or "the Fellowship" as ONE destination
   (an order to Tolkien); the table's rigby / tablet-class rows inside the fellowship tier are TOLKIEN's placement input
   (which Fellowship worker), taught to Tolkien the way the Softserve advisor is taught, never a target Spock picks. A
   user's pin always wins (decision 10-07).
4. Area C inputs as they become measured: ETA (per-solve times already in the table), urgency (request field),
   energy-in-placement (J/solve already in the table), residency as a cost. **Residency BUILT + LIVE 2026-10-10 18:0x**
   (orchestrator gate 2588 pinned; container `scheduler.py` 53b12a45, `ollama_client.py` ced5d3d4):
   `scheduler.residency_cost` adds a row's MEASURED cold load to its seconds per solve on the `fastest` lens when
   ollama `/api/ps` says the row's model is not resident; unmeasured or unanswered = no charge, named in the `why`;
   home stays ahead of overflow; the other lenses ignore it (a load's energy is unmeasured)
   (`tests/test_scheduler_policy.ResidencyCost`, 5). The price is measured, never assumed: `infer_ollama` records
   ollama's own `load_duration` as `placement.load_s` on every order (`--cold-load` evicts first; foundry suite 480,
   all three GPU factories), `submit_cold_load.py` orders every GPU box × model the table can place at its tested
   context in two passes, and `menu_policy.load_prices` stamps the median per box × model on each row (`load_s`,
   `load_n`; `test_menu_policy.ResidencyPrice`). First readings 10-10: 4–22 s on BigHonker/Softserve, the passes within
   ~1 s. How to use: [[RUNBOOK-Compute-and-Training-Order-Submission]] § "PLACED ORDERS".
   **The table regenerates itself (self-sustaining, 10-10):** task "HAI P34 menu refresh" on Softserve runs
   `menu_refresh.py` every 15 min — `build_menu` → `menu_standings` → `menu_policy` when the menu's inputs changed
   (≈15 s warm); proven by the task's own first run (exit 0, policy regenerated). ⚠ **It then never ran again until 2026-10-16:** its
   only trigger was AtStartup + a 15-min repetition, which arms only when the boot trigger fires, and the task postdated
   Softserve's last boot — six days of rows (rigby's whole #36 re-price) never reached the table. Fixed 10-16 with a second
   trigger (Once at a past date, same indefinite repetition; `hard-bench\fix_menu_refresh_task.ps1`, `NextRunTime` read
   back); `schtasks /query` hides repetition on boot triggers, so verify with `Get-ScheduledTask`. Defect log 2026-10-16.
   **Since 2026-10-30 the same refresh keeps the MODEL MATRIX current** (`model-matrix.csv` / `.md`: every measured config,
   accepted or refused with the reason, ranked on the three lenses by `menu_policy`'s own keys; the "point cloud" view of
   the menu, not a merged score) — [[RUNBOOK-Compute-and-Training-Order-Submission]] § "The model matrix".
5. Fold role rows in as they deliver (A1 lead challengers, rigby/BigHonker/tablet helper prices, the advisor).
   **DONE for leads + helpers 2026-10-10:** `reduce_a1.py --label phase7-roles-<day>` over the role arms (it now
   writes each arm's lead model / effort / ctx / task set) → `menu_policy.compile_roles` → `policy.json` `roles`
   (family `agentic-a1`: `crown`, `lead_defaults` — gate NO, reviewer NO, delegation UNDECIDED until the cascade —,
   `leads` pooled per box × model × effort and priced per solve, `helpers` per helper × model × path priced per piece
   with the lead's round trip; `test_menu_policy.RoleRows` + `LiveRoles`, 17 OK). Spock reads only
   `families[…].tiers`, so the role rows inform and change no placement. First readout: the 3.8 xhigh 11/16 at
   3,604 s per solve (crowned); BigHonker qwen3:8b pieces 4.6 s of work but a **40 s round trip through the desk as
   an order vs 16 s through the window** — the desk overhead, not the helper, is the piece price; rigby 221 s of
   work, 290 s round trip (one piece). Advisor rows: Phase 8.

## The measurement section's exit criterion (so it cannot keep moving)

The pre-Phase 7 measurement list is **closed to new arms** as of 2026-10-07. The arms already queued finish and fold in
([[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]]). A new arm is added only when Phase 7 cannot make a specific placement
decision without it — named in this doc, with the decision it unblocks.

**Named 2026-10-10 (the owner, after the fixed-pair team arms #3/#4 delivered prices but no cascade: *"it's supposed to be
cascade not just one or the other"*): the CASCADE ARM.** The decision it unblocks is the table's **split decision** —
for a job family, is the lead-alone price or the lead-plus-placed-pieces price the acceptable one, and at what
overhead (the lead's split + read cost, the pieces' desk round trips)? Nothing measured so far can price it: the
A1-team arms pin ONE helper (`only: [helper]`), so they price that helper, never a placement; and the hard Polyglot
set fails on hidden spec details no piece can supply. The arm, checked against the library before locking:
- **Mechanism = the A1 spec's own design, finished:** `delegate(brief)` "placed by Spock on any worker below it (an
  8 GB box, rigby, the tablets) exactly like an order" ([[34-A1-Agentic-Build-Family-Spec]] § Sub-agents) — the build
  pinned it "for NOW" (10-07) because the table did not exist; it does now (step 3). So: the delegator drops
  `only`/`requires model.<helper>`, carries `family` + urgency, and `scheduler.place()` orders the eligible hosts by
  `policy.json` (fail-open). Spock's choice is fleet-level (an 8 GB box or "the Fellowship" as one destination); a
  Fellowship piece is an order to Tolkien. **Known limit of arm 1:** Tolkien's in-Fellowship choice (rigby vs tablet
  class) is not built — the design says it is taught like the Softserve advisor — so arm 1 places `llm` pieces on
  rigby and `map` pieces on tablets by kind, and the table's Fellowship rows wait for that build. `escalate` moves the
  whole task up, same door.
- **Work that divides:** the W1 work family's code / reasoning / ops parts ([[34-CHARTER]] § "How results shape
  action" 3: *"prices for sub-task shapes (W1's code / reasoning / ops parts per arm are the start)"*) — the
  decomposed pool S10c designed and never built ([[34-S10-Rigby-Lead-Fellowship-Design]] § Arms: *"a decomposed task
  pool + an order that chains both lanes"*), one level up. Not the Polyglot exercises.
- **A selector production has:** Fellowship pieces are accepted by lead-written checks (the S10b machinery,
  measured 10-09: false pass ≤ 3 %), never by the hidden grader (charter: *"an answer key is not a selector"*).
- **Compared with:** the lead alone on the same W1 items (the menu's W1 rows at the 3.8's best settings), on the
  three lenses, per solve; early stop by the standing rule; n=1 first, reproduces-once before it moves the table.
- **It runs as orders** (a `submit_cascade.py` the lab owns), so #34 can be re-run without Claude.

**The placed delegate — build plan (2026-10-10, read against the code before writing):**
- **A. Desk accepts the placement fields.** `scheduler.compose_orders(**kw)` → `place_order(**kw)` → `place(kind,
  family=, tier=, lens=)` already carries them; only `FactoryOrderReq` (`app.py`) lacks `family` / `tier` / `lens`.
  Add the three, pass them in `compose_kw`, record them in the order's `route` so a held order is placed the same way.
- **B. The model follows the box.** A piece must not name its helper model (that pins the box through
  `requires: model.<name>`). The template carries `{POLICY_MODEL}` / `{POLICY_THINK}` / `{POLICY_TEMPERATURE}`
  tokens and requires only `order.compute`; after `place()` picks the host, `compose_orders` resolves them from the
  SAME `policy.json` row that ranked that host (`families[family].tiers[tier][lens]`), and the host's tested context
  from `box_models.json`. One derivation: the row that chose the box chooses its setting. No row for the chosen
  host = refuse with the reason, never a default model.
- **C. The delegator's placed mode.** `a1_delegate.Delegator` gains a mode that sends the piece with
  `family` (the piece's job family: coding-f2 for a code piece, work-w1 for a reasoning/ops piece) + `tier`
  (`8gb`; the Fellowship tiers too since D, 10-17) + `lens` (from urgency: interactive → fastest, else least_energy), no `only`,
  no model; the answer is read as today. Pinned team arms keep working unchanged (pricing).
- **D. Fellowship destination — BUILT + LIVE 2026-10-17 (checklist B1 + B2).** A placed piece brings a FELLOWSHIP
  FORM beside its GPU template (`/p20/order` field `fellowship`: a Tolkien `llm` order naming no worker, requires
  `fellowship.placed`). Spock ranks "the Fellowship" as ONE destination at its best worker row (`rigby` / the tablet
  pool map onto the one desk host; a HOME row leads) and sends the form with `placement {family, tier, lens}`.
  Tolkien picks the worker when it collects it (two routers, the owner 10-07): within the Fellowship's RUN of the table
  (from its first Fellowship row to the next row that is another destination's), the first PRESENT row fills the
  spec from that row's `order` fields (`menu_policy`, from `menu_fill`'s own builders — rigby = the standard 1060
  order; a tablet row = one warm node, the fastest measured, ctx 16,384). No row of the run present → handed BACK to
  the desk, which re-places the GPU template on the table's next destination. Live proofs 10-17 (the 1060 had fallen
  off the bus, so all three paths ran on real conditions): rigby gone + `fellowship` tier → warm on gandalf, correct
  in 1.9 s; rigby gone + `rigby` rung → handed back, re-placed on BigHonker (`nemotron-3-nano:4b` from the row),
  correct in 0.5 s; after rigby's reboot → the standard 1060 order, correct at 16.5 tok/s. Results carry `placement`
  + `placement_row` (the pick and every skip). Mechanism: [[Fellowship-Order-Taker-Design]] § PLACED orders.
  **Two rungs inside the Fellowship (2026-10-16, `menu_policy.py` `TIER_HOMES`):** `fellowship` is unchanged (homes
  rigby + the tablet pool — Tolkien's whole destination, which `cascade_select.py` and the 7 simple-chat triage labels
  still name); two tiers sit beside it: **`rigby`** (homes rigby: the 35B-A3B on the 1060, a quality peer of the 8 GB
  tier at 2–6× the seconds) and **`tablets`** (homes the tablet + GT 730 pool, 0.8B–3B). Backward compatible: Spock's
  `policy_order` treats a tier as an opaque key and returns `[]` for an unknown one; all 72 existing family/tier/lens
  cells unchanged; the 13:30 refresh kept the rungs. **The rungs are PLACEMENT, not triage (decided 10-17, register
  B7 + C2):** McCoy names a difficulty (tool / fellowship / 8gb / lead) read from the request; the union `fellowship`
  tier stays its destination and Tolkien picks the rung from these rows. the owner, 10-17: *"real life Work isn't going to
  have premade labels that's the point of the routing team"* — so no `rigby` label, and tev1's 0.90 line stands.
  Whether rigby is ALSO an 8 GB-tier home is a table row decided by BigHonker's split-A3B test, not a label.
- **E. `escalate`** submits the whole task one tier up through the same door. Then `submit_cascade.py`.
Gate + deploy per piece (orchestrator door for A/B, `fleet_deploy --a1` for C, Tolkien for D).
**A + B BUILT + LIVE 2026-10-10 16:24** (orchestrator gate 2580 OK pinned; container `app.py` 6b599a8f, `scheduler.py`
8cadcb97; `tests/test_fleet_desk.PolicyFieldsReachPlacement`, `tests/test_scheduler_policy.PolicyTokens`;
`scheduler.policy_lens` is the one lens rule for `place()` and the tokens). **C BUILT** (`a1_delegate.PlacedDelegator`,
`a1_order` picks it for `team.placed`; A1 suite 166 OK). **Live decoy 16:25:** a piece with family coding-f2 / tier
8gb / urgency background, no host, no model → the desk placed it on BigHonker from the least_energy row and resolved
`--model nemotron-3-nano:4b --num-ctx 261120 --temperature 0` (no think args), recorded `policy_row` on the spec and
`policy` in the route; BigHonker's factory ran it and delivered a correct answer (104 tok/s) 40 s after submit. (The
decoy session had no worker token, so its status polls were refused — a harness gap on Softserve, not the mechanism;
A1 orders poll with the factory's token.) **C2 — the PLACED WINDOW, BUILT + LIVE 17:2x** (`PlacedWindowDelegator`;
the window is placed once per run from the table, pieces answer warm): live decoy item 1 14.3 s, item 2 **1.1 s**
([[34-A1-Agentic-Build-Family-Spec]] § "Measured price"). Round-trip fixes the same afternoon: desk POST 11 → 0.4 s,
preflight 9 → 2 s. **D BUILT + LIVE 2026-10-17 (above); E is a rule of the cascade harness (below).**

**The cascade arm — build plan (2026-10-10 17:5x, decided within the locked frame):**
- **E = escalation is a rule of the cascade harness, not a model tool** (10-07: "escalation passes the WHOLE task UP";
  [[HAI-Agent-Surface-Design]]: the advisor proposes *when*, the checkers decide). A piece that fails its check moves
  ONE tier up through the same placed door (fellowship → 8gb → lead); the lead answers what nothing below passed (S10a's
  residue shape: cheap workers first, the lead on what they miss).
- **Arm 1 = W1's 8 CODE tasks** (`hard_tasks_w1.py` `code_*`): the selector is real there — the lead writes the checks
  (S10b machinery, false pass ≤ 3 % measured), never the hidden grader. W1's 8 `reason_*` (exact answer) and 8 `ops_*`
  (multiple choice) have NO production selector: a lead "check" there is the lead solving it. **The decision-model
  selector (checklist B3)** — tev1 0.8B and 4B (the models that fit Softserve's 8 GB card, `/v1/systemone`;
  [[Research-Decision-Models-System-One-Landscape-2026-10-15]]) judge every usable menu W1 answer of a kind against the
  hidden grade (`submit_w1_judge.py`, `Project34-Menu\w1_judge.md`). **The bar is the lead's own error rate on the same
  answers** (qwen3.8:27b on Mothership, thinking on — its production setting; derived by the reducer from the same
  rows): a cheap answer the judge vouches for must be at least as likely right as asking the lead, with ≥ 1 pass kept.
  (Restated 10-17 at the owner's question; the rule stated before the data had borrowed S10b's code-checker rate, ≤ 3 %.)
  **Reason — MEASURED, does NOT qualify:** the lead is wrong on 3.1 % (3 of 96; thinking off 5.2 %, medium effort);
  best tev1 4B at threshold 0.99 accepts 178 of 1,415 below-lead answers with **5.6 %** false pass (95 % CI 3.1–10.0)
  — real discrimination, not the bar; 0.8B worse at every threshold. 33 answers hit the endpoint's 8,192-token context
  (counted, never scored); bound: at most 4.7 % even if all 33 were correct accepts, so the verdict cannot move under
  either bar. **So W1 reason pieces stay the lead's.** **Ops — MEASURED, tev1 4B QUALIFIES** (the first run judged
  none: every original ops row is void, letter-biased, and the judge's task table lacked the shuffled `ops_*_sh`
  tasks; re-run `b0dd166dc56e`, 719 answers): bar 0 % (the lead wrong on 0 of 72); tev1 4B at 0.95 accepts 165 of 287
  below-lead answers with 0 wrong (CI 0–2.3 %), keeping 165 of 265 passes; it separates right from wrong answers
  WITHIN a task (`ops_stale_image_sh`: accepted exactly its 18 right answers of 36). Scope: 8 questions, wrong answers
  in 3 of them, below-lead workers qwen3.5:9b + qwen3:8b thinking off only. **Adopted 2026-10-17 by the charter and
  the data** (a checker is a production selector; the judge accepts answers, Kirk/Spock still move whatever it will not
  vouch for, whole, to the lead): W1 ops pieces start on an 8 GB box (qwen3.5:9b, thinking off) with tev1 4B vouching
  at 0.95 — ~6.3 s / ~1.25 kJ per piece against the lead alone at 8.7 s / 3.3 kJ (its cheapest perfect setting,
  thinking off). Recorded as `policy.json` `roles.lead_defaults.ops_selector` (preliminary; register B3). The cascade
  arm for W1 code is unchanged (`cascade_select`; hard code starts at the lead).
- **Shape (revised 18:1x, the S10a/S10b pattern — "no new tablet run"):** every tier's W1 code answers ALREADY exist in
  the menu (tablets 5 seeds per model, BigHonker/Softserve 3 seeds per setting, Mothership's models). So the arm needs
  ONE new order — **the 3.8 writes the checks for the 8 W1 code tasks** (`hard_tasks_w1_tests.py` → `gen_hard_payload
  --pool w1tests` → `submit_cascade.py`, 3 replicates like S10b) — and a reducer (`cascade_select.py`) that walks the
  tiers per task, fellowship → 8gb → lead: the first candidate (seed order) passing the lead's checks is accepted, else
  up a tier; the lead's own W1 rows answer the residue. Graded after by the hidden checks (false pass / false fail, as
  S10b). Price per tier = the rows' own wall + J + the measured per-piece overhead of the placed window (~1 s warm,
  one load per tier per run). Honesty of the lead's checks per suite: accepts W1 REFERENCE, rejects its NEGATIVE.
  A LIVE run through placed windows is the reproduction step, not the first measurement.
- **Compared with:** the 3.8 alone on the same 8 tasks at the same settings (its W1 menu rows), on the three lenses,
  per solve; n=1 first, reproduces-once before it moves the table.
- **Fellowship tier (D, BUILT 10-17):** the reducer walks the Fellowship rows already; a LIVE cascade run can now place
  Fellowship pieces through the same door (the placed delegate's Fellowship form).
- **Built 10-10 17:3x–18:0x:** `hard_tasks_w1_tests.py` + `gen_hard_payload --pool w1tests` (payload
  `map_swarm_code_hard_w1tests.py`), `submit_cascade.py` (order `cascade-lead-checks-q38-xhigh-t06-qwen3.8-27b.
  20261010-173541`, Mothership, 27 items), `cascade_select.py` (the reducer). The lead tier's answers are read from the
  10-07 xhigh W1 delivery directly (`fleet-g-mo-qwen3.8-27b-w1-t10-xhigh-s1`, 19/24): **the menu does not carry the
  3.8's xhigh W1 cells** — its 3.8 W1 "on" rows are the 10-03 cells at MEDIUM effort (register: EFFORT-LEVEL SANDBAG),
  and of the 10-07 xhigh re-runs only t1.0 delivered. A replicate = the r-th answer in seed order (seed bases differ
  per box: rigby 1, BigHonker 2, Softserve 2–7).
- **Positive control PASSED (10-10 17:4x):** fake lead checks = the hidden W1 checks → 24/24 suites valid (accept W1
  REFERENCE, reject NEGATIVE), zero accepted answers the hidden grader fails. It is also the arm's **CEILING** (perfect
  checks), PRELIMINARY: below the lead the tiers answer 2 of 8 tasks per replicate (1 Fellowship, 1 8 GB); quality
  equals the lead alone (7/8, 6/8, 6/8), and the walked seconds are ~5× the lead alone's. Per worker on W1 code (the
  rows the reducer walks; hidden pass, median s and J per answer): rigby Qwen2.5-14B 3/24, 220 s, ~12 kJ (54 W);
  tablets Coder-1.5B 1/40, 133 s, unmetered; BigHonker qwen3:8b 3/21, 4 s, 568 J; Softserve qwen3:8b 1/47, 3 s, 570 J;
  the 3.8 xhigh 19/24, 152 s, 59 kJ. Read: on HARD code the Fellowship tier costs ~900 s per task to save the lead
  one task in eight; the 8 GB tier is cheap enough to pay on energy only if the lead's check-writing is cheap — the
  lead order prices exactly that.

## Two shapes of delegation (the owner 2026-10-10)

the owner: *"Cascade can mean things like sending a sub agent to read/find something for a main agent while the main stays
doing something else… Fetch is perfectly normal. So is sending sub agents to run simple commands."* So the delegation
default has two shapes, and the lab had measured neither as production uses them:

1. **ESCALATION** — a whole task moves down (cheap tiers first) or up (the lead takes the residue). Measured by the
   cascade arm above on W1 code; its result prices THIS shape only.
2. **ERRAND** — the lead sends a helper WITH TOOLS to read, find, fetch or run something and keeps working; the
   result arrives when it is done. Value = the lead's time not spent on the errand (overlap), not a saved answer.

Every A1 team arm so far was a third thing: a synchronous, toolless piece (`delegate(brief) -> result`, the lead waits
on the desk; the helper is one model call with the brief as its only input). The 18-piece delegation corpus
(`workspace\a1\summary\delegation-corpus-20261007-0509.jsonl`) is 15 "write this function" pieces and 3 attempts to
FETCH a test file that a toolless helper could not do — the errand shape, asked for and not available.

**The errand build, from parts that exist (decided within the frame; nothing new invented):**
- **The helper's turns go through the PLACED WINDOW** (`PlacedWindowDelegator`; the window is the helper box's foundry
  order, so 12e visibility holds and Spock + `policy.json` choose the box/model; measured 1.1 s per warm turn). Gap: a
  window item is one prompt -> one answer. It gains a chat turn: item `messages` + `tools` -> the reply message with its
  `tool_calls` (`infer_ollama._chat` already passes ollama native tools).
- **The errand loop runs in the LEAD's harness**, beside the workspace: the helper model's tool calls execute there
  against a READ-ONLY subset of the lead's own tools (list / read / search / run the visible tests — the tools the A1
  harness already has and already sandboxes). The helper never writes the lead's files.
- **Asynchronous:** `delegate(brief, wait=false)` returns a handle at once and the lead keeps calling tools; a finished
  errand's result is delivered to the lead as a note on its next turn; `finish` waits for (or reports) open errands.
- **Measured as an A1 arm, one variable:** the same lead on the same tasks, solo vs errand-team, priced on the lead's
  wall time, the helper's cost and the hidden-test score.
- **BUILT 2026-10-10 18:1x** (A1 suite 178; deployed: `helper_window.py` + the A1 payload to the three GPU factories,
  `a1_harness.py` to rigby's share — four-factory parity): `helper_window` takes an errand turn (`messages` + `tools` →
  the reply's `tool_calls`); `a1_harness._Errands` (tools `errand` → a handle at once, `errand_wait`; finished reports
  posted to the lead before its next turn; helper calls run through the lead's own dispatcher restricted to
  `ERRAND_READONLY`; ended by a report, the loop cutter's no-progress rule, a helper error, or the lead's end — never a
  clock; every errand recorded on the row); `WindowDelegator.turn` (one write-and-wait path shared with pieces);
  `submit_a1.py --team-errands` (placed window, family `tool` — an errand IS tool use —, tier 8gb, lens fastest, no
  `delegate`). A placed helper never lands on the lead's own box (`a1_delegate.lead_box()` → the desk's `exclude`).
  **Live decoy 18:14:** the desk placed the window on BigHonker (qwen3-vl:8b, ctx 5,120, from the table row); the
  foundry REFUSED it (rc 2, held): the model came up 5.5/6.0 GB on the card after a clean reload — the residency guard
  working; retried every 10 min.
- **An errand is a SMALL QUESTION, not a reading job** (the owner 10-10: *"could not be like hey can you find a count of
  how many things there are rather than it having to be like hey I need you to read all of this stuff?"*): how many
  places call X, where is Y defined, which visible tests fail and on what line, does Z exist. The helper answers with a
  search and returns the count / location / yes-no plus one line of evidence, never file contents (`ERRAND_TOOL` and
  `ERRAND_SYSTEM` say so). That is also why a helper's small tested context (BigHonker qwen3-vl:8b at 5,120) is enough;
  cap hits are still counted (13a). Simple COMMANDS (next arm) make most of these one call (a count is a grep).
- **…and a BOUNDED job the lead COULD do but is better off handing out** (the owner 10-10: *"if it could be answered with a
  single grep maybe it's better to just do that instead of sending off an agent on an errand but it should be tightly
  bound tasks"*). The rule the lead is given: an errand is a job of SEVERAL steps with a short answer (run the visible
  tests and report which fail and where; check every caller of X for Y); anything one search or read answers, the lead
  does itself. Measured, not assumed: each errand on the row carries `one_call` (done in ≤ 1 helper tool call = a job
  the lead should have done itself), so the arm reports how often the lead used errands the wrong way.
- **The published rules we follow, not our own (the owner 10-10: *"you use sub agents all the time. What are the rules
  that you use?… We can't be the first people to do this"*).** Claude Code's own Agent-tool guidance: delegate *"when
  you have independent work to run in parallel, or when answering would mean reading across several files — delegate
  it and you keep the conclusion, not the file dumps. For a single-fact lookup where you already know the file, symbol,
  or value, search directly."* Anthropic, "How we built our multi-agent research system" (engineering blog): each
  subagent gets an objective, an output format, tool guidance and explicit boundaries (vague briefs → duplication and
  gaps); effort scales to the question (simple fact-finding = 1 agent, ~3–10 tool calls); spawning agents for simple
  queries is a named failure; the lead synthesises; and **multi-agent fits most coding tasks poorly** (fewer
  parallelisable parts than research; ~15× the tokens of a chat). **Consequence:** errands are measured where
  reading across files IS the work — not the Polyglot spot set (one stub per exercise, the brief already in the
  prompt: nothing to send a helper for; the queued arm `…-spot-team-bi-err-exhigh.20261010-1913` was cancelled at
  the desk before it started). The A1 task set built for that work is **Terminal-Bench 2.0** (approved 10-04, never
  started; 89 containerised agentic tasks, hidden tests) — its own harness adopted, not rebuilt (doctrine: "Aider's
  own benchmark.py… Inspect AI…").
- **The FOREST — what level must the lead be (the owner 10-10: *"part of the project was trying to figure out at what level
  the lead has to be… still choose the most energy efficient and most best fitting… time quality and cost. Make sure
  you're not missing the forest for the trees"*).** Delegation is an OPTION ON A LEAD, not the question. The question
  is the one placement rule: for each job family, the cheapest lead that does the job acceptably — alone or with
  helpers below it — on the three lenses (time · quality · energy). So the Terminal-Bench measurement is a LEAD-LEVEL
  grid, not "the 3.8 ± errands": (1) each lead level SOLO first — the 3.8 on Mothership, the 8 GB lead
  (BigHonker / Softserve), the rigby lead — does the job get done at that level at all; (2) errands where they can
  move the answer — a smaller lead just short alone, or a big lead whose time or energy errands could cut — helpers
  always from the tiers below that lead; (3) every arm priced per solve on all three lenses into `policy.json`
  `roles.leads`, so Spock picks the lead level the way it picks everything else. Promising arm first, stop rules on
  clear losers, controls last (evolutionary mindset).
- **The answer key is locked, not the tools (the owner 10-10: *"make that specific area with the answer key locked off
  instead of locking the tool"*).** On the box this already holds: the hidden tests are invisible to every read and
  refused in the lead's own tests. The next errand arm adds the tools a real errand has — FETCH and simple COMMANDS —
  with the key locked: commands run in a copy of the workspace without the hidden tests; fetch blocks the known
  benchmark sources, and since the Polyglot tests are public (repos, forks, mirrors) every fetched page is also checked
  at grading against that exercise's hidden tests and a row that fetched them is VOID, not scored. One variable per arm:
  look-only errands vs solo first, then fetch + commands vs look-only. Errand helpers come from the table (8 GB tier now).
  D (built 10-17) places single PIECES on the Fellowship; an errand helper is a multi-turn WINDOW, and the Fellowship
  has no window form yet — errands are exactly the small work rigby and the tablets exist for, so that form is owed
  (register checklist B8), and D is NOT retired by the cascade result.

## Area C step 1 — every Auto request placed from the menu, chat first: the PER-REQUEST WEIGHING (designed 2026-10-22 ~21:30 CT, Fable main session; approved "let's hit it"; BUILT in the steps of § 6 — each step's status is on its line there)

**Why a design first.** The 15:27 build that did this by hand was rolled back the same day for collapsing the matrix
([[34-Starfleet-Routing-and-HAI-Controls-Do-Not-Collapse-Checklist]] has every correction). This section is written
against that checklist item by item, from the code as it is tonight (read: `mccoy.py`, `app._mccoy_serve`,
`app.pick_brain`, `scheduler.place` / `_apply_policy` / `residency_cost`, `chat_window.py`, `helper_window.py`,
`energy.py`, the live `policy.json` and McCoy's decision log). Checklist A2 names where the weighing lives: *"area C
(evolve `scheduler.place` / `energy.py`: urgency, deadline, energy trough, contention, ETA) plus McCoy's PLACEMENT
advice — not a lens."* So this is area C's first step, inside `place()`, and the chat caller is its first consumer.

### 1. The rule, made operational — §52 with the three values of every row read AT ONCE

The charter's rule: *"the least expensive source of compute and intelligence that reliably satisfies a task's quality +
time requirements, while accounting for verification, energy, resource contention, and escalation."* Read literally
it is a constrained choice, not a sort and not a weighted sum: the request brings NEEDS (a quality floor and a time
budget), the lab's state brings the real COSTS of each row right now, and the pick is the cheapest row that meets the
needs. For each row the hard rules already passed (fit, health, Plex, claims, pins — unchanged, they decide eligibility
first), compute three numbers for THIS request from the row's measured fields and the live state:

| factor (the owner's definition, step 1 above) | for this request, from what is measured | unmeasured parts |
|---|---|---|
| **QUALITY** | the row's `quality_low` (the 95 % lower bound) — acceptable = the tier's HOME rows (the menu's stop rule already made them acceptable); OVERFLOW rows are acceptable with their caveat | — |
| **SPEED** = request in → answer on screen, AND time to first visible output | wait for the box (another run's window turn or an order ahead: the holder's remaining time, from the window's current turn or the deck's `eta`) + wake + `residency_cost` (cold `load_s` + re-read of this conversation at `read_tok_s`, already built) + `s_per_solve`; first visible output = wait + load + this prompt's read (`prompt_tokens / read_tok_s`) | `read_tok_s` missing on some rows; tablet `load_s`; first-token on the GPU rows (checklist B5) — named in the `why`, never guessed |
| **COST** = electricity | `j_per_solve` × the price now (`energy.price_curve().current`); a cold load's joules | the load's energy (unmeasured, named); rigby's J is a plug marginal (row caveat says so) |

Then, in order (**corrected by the step-0 replay the same evening — the first draft weighed overflow rows as peers and
had no quality band, and picked an overflow 8 GB row at a 0.68 lower bound for LEAD-tier turns; the second fell to
that row whenever Mothership was cold, because a 45 s load put every lead row past the line**):
(a) **the QUALITY NEED binds first:** the tier's best HOME row's lower bound is what McCoy's tier promised; a row whose
point quality is below it, home or overflow, is a dominated fallback — the same test `menu_policy.think_off_refusal`
already applies to thinking-off rows (the owner 10-19). An overflow row counts only inside that band: *"a slightly pricier
box of SIMILAR quality"* (the owner 10-02). (b) **within the band, rows inside the family's interactive line before rows past
it** (checklist A4: same quality, answers in time, it wins, home or overflow — a chat never waits "because we want a
specific model on a specific device"); a cold lead past the line is still the pick when nothing of that quality is
inside it: the wait is the price and the user is told it, and a lower tier is never the answer to a cold load ("unsure
⇒ the 3.8"; under-routing costs more). (c) home before overflow. (d) the cheapest ELECTRICITY. (e) the fastest; rows
with no J (the tablets are unmetered) sort last and say so. (f) record every candidate's three numbers and the reason
it lost in the placement record (`State\placements\`), which is the owner's review surface and later McCoy's
placement-advice items. Nothing here adds a lens: the three lenses stay the three sorted views of the same rows; the
decision reads all three values of a row together, per request, which is what checklist A1 asks for. **What changes a
pick between two requests is the REQUEST (its tier, its urgency, its conversation size) and the LAB'S STATE (who holds
what, what is warm, the price now) — never a per-type table.** Background work (a job) is not bound by the line and
the energy trough may defer it — the overnight gate's own machinery (`energy.py`), not a new one.

**The intended shape, stated by the owner 2026-10-24 (this section is its layer two; layer one is McCoy's triage, partial):** the owner's intended shape, in his words (2026-10-24 ~08:3x CT): **two layers.** *"from the VERY BEGINNING I've been describing it as a layered system"* — first Starfleet weighs the REQUEST (*"is it simple? Does it require a hard model? Does it come from a priority user? Does the request/chat itself require speed over depth…? What tools does it require"*), then takes that *"against the menu and availability and makes the choice according to the matrix"* of cost (literal electricity, incl. price spikes), speed and quality — *"Every 'request'/job/whatever gets weighted differently according to user"* (his overrides queue him ahead), a peak-price *"quick dump… and then quick resume"*, the Fellowship for *"extremely reduced prices"*. Layer two is BUILT (the weighing reads all three prices of every row per request; peak → energy first; extreme → hold with a notice; pins and his override pass). Layer one is McCoy's triage (tier / family / kind at the 0.90 line) — it does not yet ask priority user, speed-vs-depth as its own need, or tools. NOT BUILT: per-user weighting (the queue is quick-first, then first-come), a price-triggered dump-and-resume of in-flight work (the slot's in-memory pause is the mechanism), layer one's three unasked needs. Owner: **#34 area C step 3 — **BUILT + LIVE 2026-10-24 ~09:36 CT** (orchestrator gate **3135** at the pin, container `14:35:40Z`, bytes verified; Tolkien rebuilt on the same `scheduler.py`, gate **959**, three containers `14:37:11Z`; mirror audit clean before)** (§ 6 step 8 below; #35 trains judgment on what it logs; per-user weighting was already live as B4a).

### 2. The request → its needs (triage is McCoy's; the user never classifies — checklist C1, C3)

McCoy's row already carries `tier`, `family`, `kind`, each with a confidence (`mccoy.triage`). Only the TIER has a
measured line (0.90, out-of-set on the owner's 36); family and kind are unmeasured, and on the owner's own six logged turns the
family confidence read 0.12–0.49. So:

- **tier (sure)** → the acceptable set: the tier's home rows, then its overflow rows with caveat (`TIER_HOMES` /
  `TIER_OVERFLOW` in `menu_policy.py`). Unsure, no card, error → the lead (unchanged: *"unsure ⇒ the 3.8"*).
- **kind → urgency.** `chat` = someone is waiting (the interactive line binds, time to first visible output counts);
  `job` = enters as an ORDER (the work loop / Background that #37 shipped) and is weighed as background: the energy
  trough (`energy.price_curve`, the overnight gate's own machinery) may defer it. Kind is used only when sure at the
  same 0.90 line; unsure = interactive, the direction that never makes a user wait.
- **family → the menu family: MEASURED 2026-10-22 22:20 CT and REJECTED.** McCoy's families (chat · coding · ops ·
  image · research · writing) and the menu's (chat-turn · chat-c1 · coding-f1 / f2 · work-w1 · tool · vlm) were never
  mapped (checklist C3). The proposed mapping (chat → chat-turn / chat-c1, coding → coding-f1/f2, ops / research /
  writing → work-w1, image → tool / vlm) was scored on the C1b replay's 106 menu rows (`hard-bench\
  mccoy_family_score.py` → `Project34-Menu\mccoy_family_score.md`): at McCoy's 0.90 line 57 of 106 decided and 36 of
  those (63 %) mapped right; at 0.50, 51 %. The misses are SHAPE vs TOPIC — the menu's families are task shapes (chat-c1
  = read a document and answer; tool = a fixed tool set; W1 = multi-part work) while McCoy names the TOPIC (a clinic
  document → ops, a ferry timetable → writing, W1's reasoning puzzles → coding). So McCoy's family cannot choose the
  menu family. **The menu family comes from the TIER:** fellowship → `chat-turn` (CHAT1's simple-turn rows), 8gb and
  lead → `chat-c1`, tool → the `tool` family on the 8 GB rows (the model that drives the tool). A `job` enters the
  work loop, whose lead and families are the job path's own (the crown, `lead_model`). McCoy's family stays on the
  row for the record and for C5's training data; it decides nothing. On the owner's 23 real chats in the same replay the
  family was sure on 2 (the same picture).
- **A pin** (model / box / effort) filters first, as today (`only` / `exclude`); the weighing only orders what the pin
  allows and may INFORM, never override (decision 10-07).

### 3. Destinations — all of them (checklist C5), and what each costs to serve

- **the lead** → Mothership's window (the existing lead dispatch), at the ROW's model, thinking and tested context.
- **the 8 GB tier** → a window on the PLACED box at the ROW's model / thinking / context — not the box's standing brain.
  `_brain_option` today maps a host to `config.BRAIN_MODEL` / `FAILOVER_MODEL` / `PC1_BRAIN_MODEL` (`MOTHERSHIP_BRAIN_MODEL` until 2026-11-16); the row
  supplies these instead (one derivation: the row that chose the box chooses its setting, as the placed delegate
  already does with `{POLICY_MODEL}`). The constants stay as the fail-open fallback when no table or row exists.
- **the Fellowship** → an ORDER TO TOLKIEN. `place(fellowship=True)` already ranks "the Fellowship" as one
  destination and Tolkien picks rigby or a warm tablet from the same rows (§ D, live for PIECES). What does not exist
  is the chat TURN form: Tolkien's `warm` arm answers one item on a resident node ([[Fellowship-Order-Taker-Design]] §
  PLACED orders, `warm_nodes: 1`), and CHAT1 measured it (rigby 1.9 s a turn, first token ~0.3 s; tablets by class).
  The Fellowship chat form = a warm node answering ONE conversation turn, streamed back into Kirk's transcript like a
  window item. **Until it ships, a Fellowship pick is recorded as the winner and the turn runs on the next acceptable
  row with the caveat logged** — never typed as an 8 GB request (the 15:27 build's collapse). The form is build step 3
  below, not a deferral.
  ✅ **Shipped 2026-10-23 08:27 (`fellowship_turn.py`), and the proof priced what the table had not:** a Fellowship chat
  turn is a desk ORDER — Kirk waited 86.8 s for a 1.9 s row (desk round trip, Tolkien's pickup, rigby's `llama-server`
  launched PER ORDER, the prompt read on the GT 730). A Fellowship row is not a card, so `residency_cost` had nothing
  to ask and the weighing charged 0 before its first token. Now the table stamps the MEASURED `order_overhead_s`
  (median over delivered hai-chat orders, `menu_policy.fellowship_overheads`) on every rigby / tablet-pool row and the
  weighing charges it as that row's `first_s`; an unmeasured overhead is never judged inside the line. Read honestly:
  **the Fellowship is a background chat destination today** (a sure job keeps it — no line, home first, cheapest J);
  an INTERACTIVE one needs the warm-node form this bullet describes — one server kept up between orders on Tolkien's
  side (register, the owner's call). The "warm arm" CHAT1 priced (first token ~0.3 s) is that warm server; a per-order
  launch is not it.
- **tool / plain code** → the existing special routes and tools (no model).
- **the per-turn EMBED (library search, tool selection) → placed from the menu's `embed` family since 2026-10-23
  evening** (`scheduler.embed_placement` → `embeddings._hosts_to_try`; the owner's ruling 17:2x pulled it into #34). One
  row per GPU box from the embed-price orders (foundry `workspace\embed\embed_check.py`, `hard-bench\
  submit_embedcheck.py`, deliveries `campaigns\embed-<day>`, compiled by `menu_policy.embed_family`): ONE model
  everywhere, so quality is the model's — 1.0 once a box's vectors agree with the fleet's for the same probes, and a
  box that disagrees is refused as an instrument fault, never a cheaper row; the 8 GB boxes are home and the lead's
  one-model card overflow (an embed there evicts whatever the lead holds). Priced 10-23 (30 s warm loops under the
  foundry's own meter, 12 real lab queries): Mothership 17.9 ms / 1.03 J, Softserve 23.6 ms / 1.2 J, BigHonker
  28.9 ms / 0.99 J per query; cold loads 1.3–1.7 s; agreement 1.0000 over 12 probes across CUDA and ROCm. No
  Fellowship rows: a desk order per embed pays the table's measured overhead before its first token (rigby 10.2 s, a
  tablet 28.6 s) against 20–30 ms on a card, and a warm embed server would be a second kept-loaded model (E1). The
  placement is held 30 s (a turn's two embeds land seconds apart; every input moves on the keep-alive's scale), a
  box just found unreachable is neither weighed nor dialled, a card held for another model is still skipped
  (`occupied_for`), and the fixed `EMBED_HOSTS` list serves only while the table has no embed family. Until this
  step that list — not placement — dropped every embed on Softserve and evicted McCoy all day on 10-23 (the owner:
  *"seems like we just…weren't thinking about the menu we've been building"*). **Live proof 17:56–17:57 CT:** the
  first embed after the deploy weighed bighonker > softserve > mothership (home, cheapest J; all cold, past the
  0.18 s line) — the orchestrator's note says so, and the placement record carries the three rows at the hour's PSP
  price. The proof also caught a warm box priced cold (the row names the untagged model, `/api/ps` the `:latest`
  tag; `residency_cost`) — fixed the same pass. **First visible output (B1/B5, 20:xx):** every ollama window
  turn records its time to first output (`chat_window.record_turn`); the menu stamps the warm-window median per
  box × model on its GPU rows (`first_out_s`) and the weighing shows and records it beside the total - the ordering
  stays on the total until B2 is ruled. **Per-order energy (22:4x, the owner: "probably" price it):** every Fellowship row
  carries `j_order` (its measured desk overhead × the worker's watts) and the weighing adds it to the row's J - cost
  is literal electricity (B3), and a per-order server's launch is most of a short turn's; re-measured every refresh,
  so a shorter desk time lowers it by itself. **The Fellowship is priced too (19:1x, the owner: "we can price it"):**
  Tolkien's `embed` order kind (`tolkien_llm.run_embed`: one llama-server `--embeddings` per order on the named card,
  the GPU payload's out.json shape beside Tolkien's record) measured rigby's GTX 1060 at 15.0 ms a query, 0.81 J,
  3.0 s start, vectors identical to the GPU boxes'; a HOME row with its measured desk overhead (10.2 s), so the
  weighing rejects it for an interactive turn by measurement and ranks it first on energy for a background batch.
  A tablet class is not priced (rigby-only kind); the background caller (the library reload as a Fellowship order)
  is not built — both leftovers with those reasons.

### 4. Residency and windows — nothing stays loaded (checklist E1, E2)

A chat window exists so turns do not pay a desk order each; it is not a standing model. Two consequences, both
checks against the code as it is: (a) **an idle ollama window must end at the engine's own idle rule** — `_open`
passes `idle_ttl` only for Strata windows (`STRATA_IDLE_TTL_S` = 300); an ollama window stays open after ollama
unloaded its model at keep_alive, so Spock reads a COLD box as held (the E2 finding, confirmed in `helper_window.py`
lines 241–243 and `chat_window._open`). Fix: ollama windows end at the box's keep_alive (`KEEP_ALIVE_OVERRIDES`),
the same rule Strata already follows. (b) **a window in USE by another run is contention** (B4b, live): that box's
row pays the holder's remaining time, or is overflowed when the user would wait — the user told, per § 5. An IDLE
window holding a different model is not a refusal: placing the row's model there costs that window its cache, which
is a SPEED cost to its next turn (a re-read at `read_tok_s`), charged like any residency cost. (c) **A Fellowship
  row's residency is its desk-order overhead** (added 2026-10-23 from the step-3 proof): the table's measured
  `order_overhead_s` — the price before the first token of a destination reached through the desk rather than a card.
  Unmeasured = noted, charged nothing, and NOT judged inside the interactive line (an unknown first-token price is not
  a fast one). (a) is live since 2026-10-23 (a BigHonker window ended by itself ~347 s after its turn, rule 300 s).

### 5. The controls — the same for every user (checklist D1–D6; a separate build, same design)

A pin to a busy box is an ORDER that QUEUES: the window order at the user's priority (the owner-first, B4a) with a notice in
the stream — *busy, queued* — the holder's remaining time as a rough ETA (the same number § 1 uses for the wait), and
the offer to reroute to the weighing's next acceptable row with its caveat; the user's answer picks. The two HAI 1.0
holdovers go in the same pass: the 27B option shown to and honoured for every user (the resource guard is the queue +
the owner-first + the owner's override, no longer a hidden box), and the Nursing persona showing Model and Effort (pinned Auto by
default, as everyone's). the owner's owner override stays.

### 6. Build order (each step usable alone; each proven before the next)

0. **Replay, read-only, before any code reaches chat — ✅ DONE 2026-10-22 22:18–22:30 CT** (`hard-bench\
   weighing_replay.py` → `Project34-Menu\weighing_replay.md`; `mccoy_family_score.py` → `mccoy_family_score.md`; both
   re-runnable, both assert their joins): 25 McCoy rows with a tier, 18 joined to a placement record. It changed the
   design three times before a line of Spock was written: (a) the family mapping rejected (§ 2); (b) overflow rows
   are fallbacks inside the quality band, not peers — the first draft sent lead-tier turns to a 0.68 8 GB row; (c) the
   quality need binds before the line — the second draft fell to that row whenever Mothership was cold (a 45 s load
   put every lead row past the 15 s line). Final replay: every lead-tier and unsure turn → the 3.8 on Mothership
   (cold ones flagged "past the line — the user is told the wait"); McCoy-sure fellowship turns ("17 times 23") →
   BigHonker qwen3:8b thinking off at 208 J, because **rigby's simple-chat row (Qwen2.5-1.5B, 35/35, 1.9 s, CHAT1) is
   REFUSED from the table by the owner's 2026-10-04 rigby hold** — whose own text says *"Lane held; delivered rows stay on
   the menu"*: the hold was written as a lane hold (`orders_only`, which `menu_fill` supports) but recorded as a plain
   hold, so `menu_policy.refusal` drops the delivered rows. A table defect against the owner's stated intent; **fixed
   22:42 CT after the live proof** (the 11 holds flagged `orders_only`, backup kept; table regenerated): rigby's
   1.5B row is now the Fellowship tier's HOME for chat-turn (lower bound 0.90, 1.9 s, 103 J) and the replay's
   McCoy-sure fellowship turns pick it — recorded and passed over until step 3 ships the chat form. Also found:
   rigby's CHAT1 rows carry no `order` fields (temperature blank), which the Fellowship chat form (step 3) will need.
1. **`scheduler.place` gains the weighing — ✅ BUILT + LIVE 2026-10-22 22:38 CT** (`auto_needs`, `policy_rows`,
   `weigh_rows`, `place(urgency=)`, `place_weighed`; `tests/test_scheduler_policy.Weighing`, 11; gate 3040/2). Behind
   the existing `family / tier / context_tokens` fields plus `urgency`; residency is read once per box; the placement
   record's `policy.weighing` carries every row's quality / time / J, band and eligibility. Not in it yet: the wait for
   a held box as TIME (today a held box is simply ineligible — B4b — until step 4's queue gives it an ETA) and the
   electricity price (it multiplies every row alike within a request; the trough matters to background orders, which
   the desk's own energy gate already handles). Fail-open as before. The lens path stays for callers that name a lens
   (the placed delegate's pieces) until they move over, one caller at a time, each with its tests.
2. **The chat caller — ✅ BUILT + LIVE, PROVEN 22:39 CT:** `mccoy.verdict` (lane + tier + kind + confidences) →
   `app._placed_brain` → `place_weighed("chat", family, tier, interactive, context_tokens)` → the first placed box
   reachable and free for the row's model, at the row's model, thinking (`ollama_client.placed_effort`: a row priced
   thinking-off is SENT off) and measured window; a Fellowship pick is RECORDED and passed over (no chat form yet); a
   lead the box cannot take overflows as today; nothing servable = `pick_brain`, logged. Proof (ClaudeSupervisor, "17
   times 23?"): McCoy sure fellowship → `weighed placement chat-turn/fellowship/interactive -> bighonker qwen3:8b
   think=off (q 1.00, 11.1 s, 208 J)` → `PLACED EFFORT … think=False` → a new qwen3:8b window on BigHonker → "391" in
   39.5 s, every row in the record (register, this evening's entry).
3. **The Fellowship chat form — ✅ BUILT + LIVE, PROVEN 2026-10-23 08:27 CT** (`fellowship_turn.py`: a Fellowship
   pick is an `llm` order to Tolkien with the row's `order` fields, placed through the desk with its `placement`,
   waited on by status, read from its delivery; served BEFORE the GPU rows are asked; mirror `9720846`, +10 tests).
   Proof: ClaudeSupervisor "17 times 23?" → route `fellowship`, rigby answered "391". **And the proof priced the
   gap § 3 now records:** 86.8 s to the answer on a 1.9 s row — a per-order server launch plus the desk round trip —
   so the table stamps `order_overhead_s` on Fellowship rows and the weighing charges it (§ 4 c; +3 tests, gate
   3056). Honest reading: today the Fellowship is a BACKGROUND chat destination; the warm-node form (a server kept up
   between orders) is what § 3 describes and is an open register item. `idle_ttl` on ollama windows (§ 4 a) —
   ✅ LIVE 10-23, proven (a BigHonker window ended by itself ~347 s after its turn).
4. **Controls — ✅ BUILT 2026-10-23 08:45 CT, gate 3056/2 green, deployed the same morning** (§ 5):
   `MOTHERSHIP_FORCE_USERS` retired — the 27B option for every user; a pin to a busy Mothership queues with *busy -
   queued: N ahead (the rule; the holder has had it N s). To answer now instead, Stop and pick Model: 8B - about N% of
   the lead's measured quality* (the caveat from the table's own rows, `app._reroute_offer`); Nursing shows Model and
   Effort and no longer forces Auto (since 2026-11-06 a sub-mode routes nothing: it is the brief, and McCoy and the weighing place the turn, checklist D4a); `/modes` drops
   `force_routing_users`. The ETA: since 10-23 evening a ROUGH ETA from hold lengths the slot has SEEN (medians per
   kind), or the reason there is none ("no estimate yet", "a long job is ahead and has no set length") - never a
   guess; re-shown when the place or the estimate moves; no give-up clock (live proof 16:4x).
5. **McCoy's placement advice** (register C3) re-ranks WITHIN the acceptable set only once multi-user data exists —
   proposes, code validates; nothing above waits on it.
6. **Area C step 2 — THE PRICE OF THE HOUR as a placement input (the owner, 2026-10-22 ~23:00 CT, after step 1 went live;
   ✅ BUILT 2026-10-23 ~09:15 CT, mirror `a89ad3a`, +9 tests, gate pin 3065 — deploy status in the register).** What
   shipped, against (a)–(c) below: `energy.price_now` (the PSP price when the sensor answers, else the $0.11 placeholder
   — `config.ENERGY_PRICE_PLACEHOLDER`, his stated average — with the SOURCE in every placement record row:
   `cents`, `usd_per_kwh`, `price_source`, `price_band`); `energy.price_band` against `ENERGY_PEAK_PRICE` /
   `ENERGY_EXTREME_PRICE`, both UNSET until they come from the home's price history or the owner names them (an unset line
   never engages — no arbitrary limits; the ruling is in the register's list B); at **peak** `weigh_rows` sorts the
   quality band energy-first, before the line and before home ("slow and low"), noting it on every row; at **extreme**
   every row is marked `held` with a notice (the price and source, the line, the cheapest coming hour when a curve can
   say, "to run it anyway, pick a Model") and `app` serves that notice as the turn's answer AFTER the pinned routes, so
   every user's pin and the owner's override still run (D3). Background orders keep the desk's own energy gate
   (`is_runnable_now`, ceiling $0.10) — the overnight trough is where the owner wants the lab busy. The PSP sensor answers (10-23: $0.0398 at 17:00, $0.0978 at 17:57, on every placement row); the peak /
   extreme LINES — unset by the owner's 10-23 ruling until 2026-10-24 13:10 CT, now $0.12 / $0.36 (step 8c) — so the band reads normal / peak / extreme live: live plumbing,
   inert lines, which is the honest state. His words: *"it doesn't really matter what the price is right now, I think my energy pipeline for
   prices is still broken but that's another day's problem. We have examples of prices from the past for my home so we
   can extrapolate from that to get placeholders for now. I mean we could even just say $0.11 /kwhr since I think that's
   the average."* And: *"if it's during peak pricing hours I don't want people to be able to have my lab running full
   tilt, I want everything to be slow and low on energy if we're in high prices. And heck maybe disabled if we have a
   crazy peak like a couple days ago when it was $1.00 PER KWHR."* So: (a) cost in the record = J × the hour's price,
   with a PLACEHOLDER when the pipeline (the HA the utility PSP sensor, `energy.price_curve`) cannot be read — $0.11/kWh
   as his stated average, or a value extrapolated from the home's past price history, never a silent zero; the record
   says which it used; (b) a HIGH price shifts every placement, chat included, toward low energy and slower rows
   (the time budget loosens, energy weighs more) — the bands come from the home's price history, not a round number;
   (c) an EXTREME price holds the lab's non-essential work with a notice to the user (his own override stays, D3).
   The energy pipeline itself is a separate repair (register B10: HA's energy entities `unknown` since 09-25).

7. **Embeddings onto the menu — ✅ BUILT + LIVE 2026-10-23 17:5x CT** (the owner's ruling 17:2x: pulled into #34; § 3's embed
   bullet has the design and the prices). Orchestrator gate 3120/2; Tolkien rebuilt on the same scheduler (949); the embed
   payload on all three GPU factories by `fleet_deploy --workspace embed`; the table regenerated by the lab's own
   refresh task. The 10-23 backlog follow-up "EMBEDDINGS placed by the menu" is done by this step and archived.

Live proof for each step as ClaudeSupervisor with McCoy-sure turns per tier, read back from the placement record and
the window's own row (host, model, thinking, context agree end to end), then the owner's turns.

### 7. Cross-check against the checklist (so a reader can see nothing is collapsed)

A1 three factors together per request (§ 1) · A2 no fourth lens; the weighing is area C in `place()` (§ 1) · A3 the
menu is the matrix: every number is a row field (§ 1) · A4 chat's speed is one need, the line, not the only one (§ 1a,
quality floor first) · A5 two analyses: § 2 the request, § 1 the best fit · A6/A7 no request-type table: tier + kind +
family from McCoy, nothing enumerated · B1–B4 definitions used verbatim (§ 1 table) · B5 first-visible-output gap
named, not invented · C1 user classifies nothing · C3 kind and family kept, mapping to be MEASURED · C4 front end
untouched; back end Starfleet → the factory window / desk · C5 every destination incl. Tolkien (§ 3) · D1–D6 (§ 5) ·
E1 nothing stays loaded, windows end at the engine's rule (§ 4) · E3 overflow = the owner's rule through the weighing's
next row, no new mechanism · E4 everything an order, two layers.

8. **Area C step 3 — THE REQUEST'S OWN NEEDS (layer one of the owner's two-layer design; #34 REOPENED for it 2026-10-24,
   the owner: "Okay sounds good" · "Hit it baby, be efficient") — DESIGN, written 2026-10-24 morning against the checklist and
   the code as it is; each deliverable's status is on its line.**

   **What is already there (read before writing — the register's top block over-stated the gap):** the owner's four
   request-side questions map onto the lab like this. *"Does it require a hard model? Is it simple?"* = McCoy's `tier`
   (measured line 0.90). *"What tools does it require?"* = the `tier` criterion `tool` (a lab tool or plain code does it),
   `kind` (`job` = many steps / tools / an order) and `family` (ops · image · coding …) — asked already, in those terms;
   no second "tools" question (it would be a second derivation of one idea, class 8). *"Does it come from a priority
   user?"* is a FACT the code holds, not a question for a model: `config.PRIORITY_USERS` (default the owner) — ✅ LIVE as B4a
   (`3c09e70`): the Mothership slot ranks quick before jobs and, within each, priority users first; the queue line says
   so to whoever waits ("the owner's requests go first within each"); a priority user's turn also passes the extreme-price
   hold (D3) and is never deferred by the energy gate. *"Speed over depth?"* is the one need NOTHING asks today: `kind`
   sets interactive-vs-background, but a one-line "what's the weather" and "analyse this report" are both `chat`, both
   interactive, both placed by the same line. **So step 3 is: one new McCoy question (depth), a per-user proof (not a
   build), and one new mechanism (the price pause).** Nothing here adds a lens (A2); every effect is a change to the
   REQUEST's needs or the LAB's state, never a per-type table (A7).

   **8a. DEPTH — the fourth triage question, used only at a measured line — ✅ **BUILT + LIVE 2026-10-24 ~09:36 CT** (orchestrator gate **3135** at the pin, container `14:35:40Z`, bytes verified; Tolkien rebuilt on the same `scheduler.py`, gate **959**, three containers `14:37:11Z`; mirror audit clean before); front-door proof on `ClaudeSupervisor`: three Auto turns - trivia (McCoy depth `quick` 0.77), an analysis request (`deep` 0.71), a Fellowship line (`quick` 0.91, sure tier 0.99 → weighed → rigby in 12 s) - McCoy's log row carries `depth` + its confidence on every turn, and the weighed turn's placement record carries `needs: {deep: false, prio: false}` (ClaudeSupervisor is not a priority user); every depth read was below the 0.90 line, so every turn stayed `quick`, as designed; where `deep` acts: on the turns the WEIGHING places - a McCoy-sure below-lead turn, and a lead turn that overflows (Mothership held) - the direct lead path is the top quality already and has no line decision to make. The shipped wording is v2 (8d); the line is `MCCOY_DEPTH_THRESHOLD` = the tier's 0.90 (candidate 0.80 recorded). NOT built from the paragraph below: the effort / window lift - the weighed row already sets the model's full effort (`ollama_client.OLLAMA_THINK`, PLACED EFFORT) and its tested window, so there was nothing to lift. Tests: `test_scheduler_policy` (the line, the gate that stops binding on the 8 GB fixture - the home A3B past the line ahead of the in-time overflow, the record), `test_mccoy` (the question set, triage, the verdict).** `depth`: `quick` = *"a short, direct answer
   the user wants now: a fact, a figure, a yes/no, a one-liner"*; `deep` = *"the user would rather wait for a thorough
   answer: analysis, a plan, generation, research, writing, multi-part reasoning"*. Same instrument as the other three
   (`/v1/systemone`, a probability per option), same rule — **code trusts `deep` only at or above a measured line**;
   below it, and whenever McCoy is unsure, the turn is `quick`: the direction that never makes a user wait (the same
   default `auto_needs` gives `kind`). The line is read off the lab's own rows the way the tier's was
   (`hard-bench\systemone_thresholds.py`: coverage · accuracy on the covered · under-routes, per threshold; in-sample =
   a candidate): the replay (8d) runs the four questions over the 119 real requests and over the HAI history's own
   turns, whose labels are mined, not typed — a turn whose answer called tools or ran long was `deep`, a short no-tool
   answer `quick` (the label rule is in the replay script; its two cut-offs come from the set's own distribution and
   are printed with the table, never a round number). **Effect in the weighing (`auto_needs` returns `depth`; the
   gates stay, one of them stops binding):** `quick` → exactly today. `deep` (sure) → the interactive LINE no longer
   rejects a row — the band keeps quality first, then home, then energy, then time, and rows past the line stay in
   contention because a deep answer is worth its wait (checklist A4: speed is chat's PRIMARY need, not its only one;
   B1/B2: the time to first visible output is still shown, so the user watching the thinking box is served either way).
   `deep` also lifts the row's effort where the row has it (a thinking-on row at the model's top effort — don't sandbag,
   13a) and the window to the row's measured maximum. Recorded per turn in the placement record (`depth`,
   `conf_depth`) — the owner's review surface and #35's corpus. ⚠ Adding a question changes the prompt; the tier line is
   re-measured on the same replay before the question ships (the 10-22 set and the 119 must still decide the same
   turns the same way, or the question waits).

   **8b. PRIORITY USER — PROOF, not a build — ✅ 2026-10-24: `prio` in the placement record (proven False for a non-priority user above; `PRIORITY_USERS` live = `the owner`), the queue's the owner-first order pinned by the existing B4a tests; nothing new to build.** B4a is live; what step 3 adds is the evidence and one wire: the
   placement record carries `prio` so the review surface shows WHY a turn went first, and a pinned test that the flag
   comes from `config.PRIORITY_USERS` (never a literal). Known limit, written down: only Mothership has a queue (the
   one slot); on the 8 GB boxes and the Fellowship a busy row is skipped for the next row, so "ahead of others" there
   means "placed first, not queued first" — a queue per box is not built, and nothing the owner said asks for one.

   **8c. THE PRICE PAUSE — in-flight work pauses at an EXTREME price and quick-resumes when it drops — ✅ BUILT 2026-10-24 (`_LeadSlot.pause_for_price`, `app._yield_point` on `ollama_client.YIELD_HOOK`, `app._price_now`; 4 pins in `test_serialization`: pauses at extreme and resumes when the band drops with a quick request served meanwhile, a priority user's job never pauses and peak / normal / a quick request pause nothing, an unreadable price is `normal`, a Stop lands mid-pause and leaves nothing queued). LIVE TRIGGER since 13:10 CT: the lines are SET (peak $0.12, extreme $0.36 - the paragraph below); the proof so far is the unit decoy + the config read back from the container, the first real extreme hour will be the live one. The history they came from: `Datasets\Project34-Menu\utility_psp_hourly.md` (120 days, 2,880 hours - p90 $0.097, p99 $0.36, max $1.00 once; the owner 10-24: "most hourly prices range from like 3 cents to like 10c").** the owner: *"during
   peak power pricing times we might do a quick dump of a request/job/whatever somebody has working, and then quick
   resume once power prices are normal."* Mechanism = the one that exists: a job holding Mothership already hands the
   slot over between model turns (`ollama_client.YIELD_HOOK` → `_LeadSlot.yield_to_quick`) and takes it back,
   its conversation paused in memory — the Xbox quick-resume shape; the KV-slot dump is the full form, this pause is
   the fallback that is never worse. Step 3 adds a second reason to pause at that same hand-over point: **when
   `energy.price_band(price_now)` is `extreme` and the job's user is not a priority user, the job releases the slot,
   tells its user ("paused at $X/kWh — the lab does not run full tilt at an extreme price; resumes when it drops; Stop
   still works"), and waits on the BAND, not the clock** — it re-reads the price at the curve's own granularity (the
   price is hourly; the re-read cadence is the hour's remainder, floor 60 s — a measured fact, not a timeout), and
   rejoins the queue the way `yield_to_quick` does. `peak` does not pause anything: peak is "slow and low", the
   weighing's job (step 2). New Auto work at `extreme` is already held with a notice (step 2); step 3 makes the hold
   cover work already running. Fail-soft like every price read (a sensor that cannot be read is the placeholder,
   `normal`). **the owner's override untouched (D3): a priority user's job never pauses.** ⚠ The lines
   `ENERGY_PEAK_PRICE` / `ENERGY_EXTREME_PRICE` — UNSET by his 10-23 ruling — were SET 2026-10-24 13:10 CT on the owner's anchor (*"usually utility themselves call out higher energy prices, like they text me about it. That's a good starting point"*): **PEAK $0.12** = the utility's PSP High Price Alert trigger, measured from his alert texts against 120 days of hourly prices (lowest hour ever listed 12.5 c, highest left out 11.7 c); **EXTREME $0.36** = the top 1 % of hours in that history (p99: 43 of 2,880, summer evenings; the $1.00 hour of 10-13 was one) — `Datasets\Project34-Menu\utility_psp_hourly.md`. `docker-compose.override.yml` env (backed up), container recreated, read back in force (bands normal / peak / extreme at 5 / 13 / 40 c; now 1.9 c = normal). The first live hour at or above a line is still to come - this autumn's curve runs 1–9 c; the proof so far is the unit decoy + the read-back.

   **8d. Replay before build (step 0's pattern) — ✅ RUN 2026-10-24 09:25 and 09:5x CT, two wordings (`Datasets\Project34-Menu\depth_replay.md`, `depth_replay_v2.md`; 250 rows = the 119 + 131 HAI-history human turns from the owner, Jordan, Riley; labels mined from each answer's tool trail and length — median 328 chars, upper quartile 894; 108 labelled).** The fourth question does not move the tier: v1 118/119 same sure/unsure (one row at 0.903 → 0.899, on the line), v2 **119/119**. Depth itself: **v1 is not decidable** — tev1 sure at 0.90 on 1 of 108 labelled rows (at 0.80: 2); **v2 (the shipped wording: *"decide only HOW the user wants it handled"*) is** — at 0.90: 6 covered, 5 right, 0 under-routes; at 0.80: 14 covered, 13 right, 1 over-wait, 0 under-routes; its errors are over-waits (it leans deep), never a deep answer called quick. **Line kept at the tier's 0.90** (the error that stays is the one that never makes a user wait; n is small), 0.80 recorded as the candidate — a lower line comes from a larger measurement (`MCCOY_DEPTH_THRESHOLD`, never by hand). On the 119 nothing moved at 0.90 (no sure deep row among the owner's own); the mechanism's effect is pinned by test on the fixture (a home row past the line ahead of the in-time overflow row). The design as written: `depth_replay.py` in the orchestrator container — four questions over
   the 119 + the HAI history's labelled turns → per threshold: coverage, accuracy, the under-routes (a `deep` called
   `quick` costs quality; a `quick` called `deep` costs the user a wait); the tier's decisions re-compared with
   `real_requests_replay.jsonl` (same sure-set, same picks, or the question waits); the weighing re-run with `deep` on the
   sure rows to show what moves (expected: the cold lead stops being "past the line" for an analysis request; a
   trivia line stays exactly where it is). Table → `Datasets\Project34-Menu\depth_replay.md`.

   **8e. Build order, each proven before the next — ✅ steps 1–5 done 2026-10-24 (this section's lines; register top block; journal). One procedure miss, recorded: the orchestrator recreate ran without `deploy_claim.py claim` while `fleet_deploy` held the lane for Tolkien - no collision (two images, both verified), the release step returned 1.** (1) `mccoy_questions.json` + `advisor_systemone.questions` gain
   `depth` (pinned equal, as today) → `triage()` returns it → `auto_needs` returns `(family, tier, interactive, depth)`
   → `weigh_rows(..., deep=False)` → `_weigh_turn` / `_placed_brain` pass it and the record carries it; tests: the
   unsure default, the line, the gate that stops binding, the effort/window lift, the record. (2) `prio` in the record
   + the config-sourced pin. (3) `_LeadSlot.pause_for_price(status)` called from the YIELD_HOOK path; tests: pauses
   only at `extreme`, never a priority user, waits on the band not the clock, Stop lands, rejoin order, fail-soft on a
   dead price read. Gate on Sundae, `fleet_deploy scheduler.py` for Tolkien, mirror audit, `verify_deploy`. (4) Proofs
   through HAI's front door on `ClaudeSupervisor`: a trivia turn and an analysis turn, their records showing `depth`
   and the line's behaviour; the two-user queue proof re-read for `prio`; the price pause by unit decoy only (no live
   line). (5) Docs: this section's lines flip to ✅ with times; register top block; checklist A4/B1 cross-refs;
   CURRENT-PROJECTS; then #34 closes again.

   **Assumptions said out loud (the owner, correct any):** (i) "tools" is asked already as tier/kind/family — no fourth
   question for it; (ii) "priority user" = `PRIORITY_USERS`, the owner alone unless he names others; (iii) the price pause
   fires at `extreme` only, `peak` stays "slow and low"; (iv) it covers HAI jobs holding Mothership — desk orders keep
   the desk's own energy gate; (v) a `deep` turn keeps the quality gate and loses only the time-line gate — it does not
   jump tiers.


**the owner's call on this section:** approve the design as the frame (then steps 0–2 proceed without re-asking), or
correct it here first. The one judgement inside it that is mine and not the library's: § 2's proposed family
mapping — which is why it ships as a measurement, not a constant.

## the owner's calls

- The crown at the end of #34 (decision 3) — unchanged; the table carries the 3.8 as the lead until then.
- Already his and already set: the 10× price line (the stop rule) and each hold in `menu_fill_hold.json`.
