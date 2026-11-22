# Research synthesis 2026-10-16 — agent workflows (Pocock skills), external drafting (Lithos), decision models: what the lab should use

**SYNTHESIS OF EXTERNAL SOURCES — not a lab measurement, and not a decision.** Written 2026-10-16 by a side session at
the owner's request ("Lots of good stuff today, especially the workflows video"). It maps three outside ideas onto the lab's
live projects and today's real failures, and ranks what to take. **No existing doc was edited.** the owner picks; each
recommendation says where it lands, what changes, what it costs and how we would know it worked. Where no clean
measurement exists, it says so.

**Read for this note:** 2026-10-16-claude-research · [[Research-Pocock-Agent-Skills-Workflow-Video-2026-10-16]] ·
[[Research-Kai-Lithos-Metal-DSpark-External-Drafter-Video-2026-10-16]] · the claim-review sweep
`State\claim-review\findings\2026-10-16.md` · the three raw transcripts (Pocock, Lithos, Alberta Tech) ·
2026-10-16-claude §10:5x and §11:1x · 2026-10-14-claude-research · headings and the cited sections of
[[12-AUTO-DECOMPOSE-DRIVER-DESIGN]], [[Subagent-Delegation-Published-Guidance]], [[HAI-Agent-Surface-Design]] (#37),
[[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] (Phase 8 rows), [[36-Closeout]] (hand-offs) · the doctrine classes at the top of
`E:\SSDShared\CLAUDE.md` · the wired hooks in `C:\Users\<user>\.claude\hooks\`.

**Tags:** **[V]** verified in a repo, paper or pinned study · **[D]** said in a video or a README projection · **[?]**
named but not opened · **[L]** the lab's own record · **[M]** measured for this note · **[I]** my inference.

## The short version

Today the lab asked HAI to finish campaign 34 in plain English. Kirk answered it as one chat turn (812 s, then a second
turn of 5,162 s that died), never cut it into orders, asked one question that was a fact, and worked in a sandbox six
days behind the mirror that did not hold the files the plan assumed. The research maps onto exactly that: Pocock's flow
is "settle the decisions first, in one round, facts looked up by the agent; then slice into fresh-context pieces; then
review at a different desk; then fix the environment, not the agent." Every stage below the first already exists in the
lab (`decompose.emit_plan`, the coding runner, `coding_review`, seven hooks). What is missing is the front half — the
triage verdict "this is a job" and the one grill turn that produces the objective and nodes the driver needs — and that
is where today's run broke.

## 1. The three ideas worth keeping

**1. Decisions before code, batched, with a recommended answer; facts are the agent's job (Pocock).** The whole flow
rests on one claim: given one sentence, an agent fills every gap with a guess and keeps going. So before building, the
agent maps the decisions, asks the whole settled frontier in ONE numbered round with its own recommended answer per
question, reads the code itself for anything that is a fact, and may not start until the frontier is empty **[V]**.
Then it slices the settled spec into pieces that each fit one fresh context **[V]** (the skill gives no token number;
the "~150 K smart zone" is the video's **[D]**). The lab earned the same rule twice already: the packet-contract rule and
the leaf-sizing rule in [[12-AUTO-DECOMPOSE-DRIVER-DESIGN]] **[L]**. Today it broke a third way **[L]**: Kirk's one
question ("which box's RAM are you waiting on") was a fact sitting in CURRENT-PROJECTS, and when it finally called
`coding_project_driver` the call was rejected for a null objective and nodes — the driver was waiting for exactly the
artifact a grill-then-spec turn produces. The second turn then ran 86 minutes as one chat and lost its own user message:
the shape both Pocock and #12 say never to use.

**2. Review at a different desk, split by axis and blind to the other axis; and a routine retro that turns mechanical
violations into checks (Pocock).** The implementer thinks its code is fine because it wrote it, so two FRESH reviewers
read the diff: one against the spec (missing, unrequested, looks-done-but-wrong), one against the standards and
Fowler's smells — and each is not given the other's input, reports never merged **[V]**. Then, before the chat is
cleared, a retro reads the session and proposes environment changes in severity order: a pointer where the agent
hunted, a lint rule where it violated a convention, trimming of large or no-op steering files; "default to building the
check over writing the rule"; suggest only, the human picks **[V]**. The lab has the first half (BLIND REVIEWER CLASS,
`coding_review`, the `assistant_manager.py` Stop hook as a second reader of Claude's own turns, seven wired hooks, the
claim-review sweep) **[L][M]**. It lacks the axis split, and its retro is reactive: a row is added after a class of
mistake costs a round-trip, never as a routine pass. The lab also has the exact precedent for the retro's steering-file
rule: on 2026-09-02 `reread_runbook_on_compact.py` was rewritten because it hard-coded a 44 KB runbook read on every
compaction ("wasted context"); the fix was to point at the library MAP instead **[M]**. CLAUDE.md at ~36.5 K tokens per
session is the same shape, one layer up.

**3. For agents, time the task, not the token (Lithos video, with the study behind it).** The "200 tok/s" is a projection
that assumes six of seven guesses land and excludes drafting, acceptance and rewind **[V]**; the team's own measured run
is ~2.4–2.5 accepted per round, ≈80 tok/s derived **[V][A]**. Drafting gains swing with content (code up, prose down,
negative on misses — owner threads **[?]**), and an agent session reads about six tokens for every one it writes **[?]**,
so prefill speed decided the whole-task time and the faster-decode engine finished later **[A]**. For the lab this is
(a) the first outside data point on #36's open step 5 (an external, target-trained drafter on a bandwidth-bound
checker): a modest edge on code, a loss on prose, Apple-only, one chip, n=4 prompts; and (b) support for #36's premise
that helpers READ — reading is where the agent bill goes. Decision models (Alberta Tech, context only): the lab has
already measured what the video claims; the register's candidate rule — tev1:latest decides at confidence ≥ 0.90,
else escalate — is the lab's own result, and the 26.4 s cold first call is the number that matters for an advisor that
sits in every request's path **[L]**.

## 2. Verified vs claimed

| Claim | Where it comes from | Status | Note |
|---|---|---|---|
| `mattpocock/skills`: 27 skills, 16 user-invoked, MIT; `grilling` asks the whole frontier in one round, facts are the agent's job, no start until the user confirms | repo + skill files | **[V]** | read directly by the research session |
| `to-tickets` sizes each slice to a fresh context window | skill file | **[V]** | no token number in the skill |
| ~150,000-token "smart zone"; reasoning degrades past it | video (Dex Horthy's term) | **[D]** | not measured on any lab model; the lab's rule 13a says a limit comes from a measurement |
| `code-review` = two fresh reviewers (spec / standards), each blind to the other axis, reports kept separate | skill file | **[V]** | |
| Hide the coding standards from the implementer | Matt's Sep 18 post | **[D][?]** | untested; an A1 arm, not a default |
| `retro`: mechanical violation → deterministic check; large/no-op steering files flagged; suggest only | skill file + release notes | **[V]** | "don't autopilot, it chases false positives" is **[D]** |
| 660 tokens for the installed set; >1 M installs; 41-story / 10-ticket / 14 K-line run | creator | **[?]** | |
| `E:\SSDShared\CLAUDE.md` loads into every session at ~36.5 K tokens (146,011 chars); memory index ~4.3 K tokens | research session | **[M]** | re-measured today: 147,393 bytes; 39 doctrine rows begin `\| **`; only 2 rows name a hook file (`no_factory_floor.py`); the actual share of a live window was NOT measured |
| The lab re-pays steering cost after every compaction/resume via a SessionStart hook; that hook was rewritten 09-02 to stop bulk-loading a 44 KB runbook | `reread_runbook_on_compact.py` docstring | **[M]** | |
| Seven hooks wired: assistant_manager, no_factory_floor, no_file_authoring_in_shell, no_long_foreground_calls, no_unbracketed_pattern_kill, notify_on_compact, reread_runbook_on_compact | `settings.json` | **[M]** | |
| Lithos 168 / 127 tok/s are projections excluding drafting, acceptance, rewind | README | **[V]** | |
| Lithos measured: 152 rounds, 2.401 / 2.510 accepted per round, 12.46–12.47 ms GPU time per delivered token, 40-core M5 Max | study at the pinned commit | **[V]** | ≈80 tok/s is derived **[A]**; n=4 prompts; the study calls it "not a universal acceptance-quality claim" |
| DSpark: 60–85 % per-user speedup vs MTP-1 in DeepSeek-V4 serving | arXiv 2607.05147 abstract | **[V]** | whether a drafter must be trained per target is not stated |
| Code 59 vs 55 (32 K: 49 vs 39); English 34 vs 38; prefill 374 vs 948; 196 K read : 31 K written; drafted output diverges at temp 0; xhigh = 5.5× tokens | owner threads, issue 54, AgentPerf-Local | **[?]** | none opened; the 13.0 vs 17.5 min agent bill reproduces from them **[A]** |
| "The lab's 3.8 on Vulkan measured a median 64.3" and "on rigby external drafting superseded by the model's own MTP head" | the Lithos note | **[L] — FLAGGED** | claim-review 10-16 flagged both as uncited; not repeated as fact here |
| No MTP-off cell exists for the 27B on Mothership | the 10-13 VRAM note, repeated in the Lithos note | **[L]** | not re-checked against the menu today (the policy file was not where I looked) |
| Jev: 193× faster, 444× cheaper, "cannot hallucinate" | vendor, via Alberta Tech | **[D]** | the 10-14 note found an independent median of 7× and could not find the 444× |
| Decision models in the lab: tev1:latest 20/36 (the owner's set) and 34/48; confidence ≥ 0.90 → 10/10 right held-out, deciding ~19–28 % itself, 0 under-routes; warm 0.18 s, cold 26.4 s; Nimble 9B out (12 under-routes) | register Phase 8 row, reduced 10-16 | **[L]** | one run each; the choices are deterministic |
| The A1 harness captures `prompt_eval_duration` per call | `a1_chat.py`, `a1_order.py`, `lab_provider.py` | **[M]** | whether the per-task reduce reports read vs write time was not checked |
| Today's failures: one chat turn, no orders; driver rejected null objective/nodes; sandbox 6 days behind; empty chat on other devices for 14 min; delegates burned the session bar | 2026-10-16-claude §10:5x, §11:1x | **[L]** | |

## 3. Recommendations, ranked by value for cost

Today's three real failures (no decomposition, sandbox drift, the per-session steering cost) are at the top because the
research speaks to each. Each row: where it lands · what changes · what it costs · how we would know.

### R1 — Teach Kirk the chat-or-job call, and make "job" produce what the driver already asks for

- **Lands:** #34 Phase 8 step 4 (triage, McCoy) · #12 driver (`12-AUTO-DECOMPOSE` "Design / Trigger": today the only
  trigger is an explicit packet header) · #37 step 2 (inline order cards). The design sentence it serves is already in
  [[HAI-Agent-Surface-Design]]: "The conversation is never an order; the work it starts is."
- **What changes:** three things, none a new engine.
  1. Triage gains the question **"chat turn or job?"** The 84 labelled rows label the TIER only; today's miss was this
     question. Labelling the owner's requests since 10-07 for it is the third piece of today's own request.
  2. On a "job" verdict, Kirk runs ONE **grill turn**: the frontier of DECISIONS as a numbered round, each with a
     recommended answer, facts already looked up. (Today's RAM question was a fact: CURRENT-PROJECTS line 25.) The lab's
     "decide within the agreed frame" and "don't invent the owner's goals" rules bound it: decisions only, never what the
     library answers. Pocock's whole skill is 28 lines; this is a prompt and a stop condition, not a build.
  3. The settled decisions become the commission packet with the contract embedded verbatim (packet-contract rule) →
     `decompose.emit_plan` (leaves with `Leaf.evidence`, the lab's own `to-tickets`) → `coding_project_driver`. A
     deterministic rule in McCoy's validation: a "job" verdict must yield at least one order, or an explicit
     "answered as chat because …" — never a critique loop.
- **Costs:** the labelling (~36 rows plus the new ones); one prompt; one triage label; one validation rule. No card time
  beyond what today's run already spent.
- **How we would know:** replay today's request (chat `224cacfcf320`) — pass = at least one desk order with objective and
  nodes appears in the queue and on the deck within triage time (seconds), not 812 s; project-sized requests answered as
  a single chat turn → 0; "job" rows classed as chat on the held-out set → 0 (the under-route, the costly direction).

### R2 — Workspace sync and "where is the code" as a preflight of every coding order

- **Lands:** the coding runner / registered-workspace rule ([[04-CODING-RUNNER-STATUS]]), #37 step 2; doctrine classes
  16 (mirror ≠ synced unless just hashed) and 17 (a capability that exists but is unwired).
- **What changes:** a coding order's preflight (a) syncs `orchestrator-real` to the mirror's HEAD (the sync-to-live step
  exists; it last ran 10-10), (b) records workspace HEAD vs mirror HEAD on the order record and the deck tile, and
  (c) REFUSES at the desk, naming the path, any job whose named files live outside a registered workspace — today that
  was `menu_policy.py` and `advisor_tasks.py` in `hard-bench`, and HAI's "0 hits" was true. Pocock's retro would have
  proposed exactly "one line pointing at where the code is"; the lab's version is a check, not a line.
- **Costs:** small — the sync exists; one preflight; one deck field.
- **How we would know:** HEAD lag reads 0 at every coding-order start; the "coding_search = 0 hits on a file the plan
  names" turn class disappears; a replay of today's turn 1 finds `mccoy.py`.
- **Honest note:** the research supplies the framing ("a mistake a machine can spot gets a check"); the mechanism is the
  lab's own doctrine. It ranks this high because the cost is near zero and it bit today.

### R3 — A context budget for the steering files: the 09-02 hook lesson applied to CLAUDE.md

- **Lands:** `E:\SSDShared\CLAUDE.md` (the owner's file; docs win) and `.claude\hooks`; the precedent is doctrine 5a (a row
  became a hook) and the 2026-09-02 rewrite of `reread_runbook_on_compact.py`.
- **What changes, in order:**
  1. **Measure first.** The note computed ~36.5 K tokens from character count; nobody read a live session's window at
     start. One read of the context size before the first user token (the status line, or Claude Code's context view),
     and again after a compaction, since the SessionStart hook re-pays some of it.
  2. **Classify the 39 rows**, each into one bucket: *already a hook* (shorten to a one-line pointer; only 2 rows name
     one today) · *mechanical, no hook yet* (build it — candidates are visible in the rows themselves: `cd /d` plus
     print `__file__`; assert the compared count is non-zero; `/sd 01/01/2020` on any `schtasks /sc once`;
     `: float = None` in a pydantic model) · *judgement* (keep) · *"Before I X" rows* (a Skill, loaded when X is about to
     happen — the Skill tool is present in these sessions).
  3. **Add the missing RETIRE rule beside the ADD rule.** Pocock's test: a skill goes when the harness catches up. The
     lab's equivalent is "when a HOOK catches it", never "when the model seems to remember". Retired text is archived
     (class 21), not struck.
- **Costs:** the owner's decision time; one classification pass; one small Python file per mechanical row, each tested
  against the real strings (the 10-08 rule-5 lesson: the first matcher draft let 8 of 12 through).
- **How we would know:** tokens at session start, before and after. And the counter-metric that must NOT move: repeat
  incidents per class — the table already tallies "N× in one day". If a retired row's class recurs, the hook was wrong,
  not the retirement; put the row back and fix the hook.

### R4 — Two-axis blind review for the coding lanes, decided by an A1 arm

- **Lands:** #37 "How the agent does WORK" item 3 (peer review after green, of the artifact, a different model) and
  `coding_review`; the #12 driver's per-leaf review; A1 behaviour arms; #36's Phase 9 hand-off (helpers READ — a reviewer
  is a reader, so an 8 GB box or rigby can hold one while Mothership writes).
- **What changes:** `coding_review` becomes two calls — a SPEC reviewer given the commission, the diff and the full
  test output but not the standards; a STANDARDS reviewer given the standards and the diff but not the spec. Reports
  return separately; a finding goes back to the same loop as a repair turn (no cold refine pass, per #37).
- **Costs:** one extra review call per leaf, placeable on a helper box; one A1 arm.
- **How we would know:** A1, one variable at a time, same lead, same tasks: solo · solo + one-axis review · solo +
  two-axis. Report score, wall time, and findings-per-review that led to a passing repair (precision) — so the "chases
  false positives" risk is measured, not assumed. "Standards hidden from the implementer" is a separate arm (standards
  in the implementer's prompt on/off).

### R5 — A suggest-only end-of-session retro (Claude sessions), and a transcript-mining order (HAI's)

- **Lands:** the main session's journal routine; `assistant_manager.py` is the pattern (cheap model, advisory,
  fail-open); RUNBOOK-DEFECT-LOG; the doctrine row "repeating a documented mistake → remove the environmental
  inconsistency".
- **What changes:** before a substantial session ends, read its transcript for: time lost hunting (→ a pointer);
  repeated mechanical violations (→ a hook); rows that were no-ops this session (→ R3 candidates); tool economy (today:
  Claude delegates burned the session bar — lab errands belong to HAI's host-admin tools). Output a severity-ordered
  list; the owner picks. Never auto-apply (Pocock's caveat **[D]** and the lab's 12a two-tier bar agree). For HAI, the same
  pass is an order over its own transcripts.
- **Costs:** minutes per session; no build.
- **How we would know:** proposals accepted per week; and doctrine rows whose class was first caught by a retro rather
  than by a round-trip that cost the owner time.

### R6 — The one-variable controls before any drafter work: MTP-off on the 27B; read-vs-write time per A1 task

- **Lands:** #34 Phase 9 (the [[36-Closeout]] hand-off), step 5 "a GPU checker where checking is cheap";
  `submit_speedcheck.py` (salted prompts and `--cold` since 10-16).
- **What changes:** (a) submit the 27B's MTP-off cell on Mothership — the 10-13 note says none exists; check the menu
  first; (b) have the A1 reduce print prompt-eval time against eval time per task — the field is already captured per
  call, so this is a column, not a measurement.
- **Costs:** one order on an idle Mothership window; one reduce column.
- **How we would know:** the MTP gain on the 27B with everything else held (salted, cold first row discarded); the lab's
  own read:write ratio on real agent traffic (the video's 6:1 is an unopened third-party figure).
- **The external DSpark drafter: not now.** Apple Metal only, trained per target, no known ollama or llama.cpp loader,
  n=4 prompts, and a temp-0 divergence bug open **[?]**. Revisit only if a loader exists AND the MTP-on/off pair shows
  headroom on code.

### R7 — A merge-danger line on deploys

- **Lands:** [[RUNBOOK-Orchestrator-Deploy-Procedure]] and `fleet_deploy`. One sentence: one-way or two-way door, blast
  radius. **Cost:** nil. **Measurement: none clean** — it is a habit, not a metric; say so and keep it anyway.

### R8 — Parallel leaves across boxes (`implement-spec`) — later

- Only after R1's serial path works end to end, which it has not yet. The lab's constraint is one model per card and
  concurrency from the fleet, so "one agent per ticket" means one box per ticket. Measurement when it comes: the same
  spec serial on Mothership vs parallel across boxes, wall time plus merge conflicts.

## 4. What NOT to take, and why

| Idea | Why not |
|---|---|
| "Turn thinking effort down from xhigh" (Lithos video) | Contradicts the owner's standing rule: density, not duration; don't sandbag. The 5.5× figure is an unopened owner thread, and the lab's effort audit already prices effort per model. |
| The 150 K-token smart zone as a sizing constant | **[D]**, and a limit picked without a measurement is the class-13a mistake. The lab's leaf budget (≤ 25 tool calls, `Leaf.evidence`) stands until a quality-vs-window-fill curve is measured on the 3.8 (candidate measurement 6 in the Pocock note; it would feed Phase 7's placed-window cost). |
| Retro on autopilot | Pocock's own caveat **[D]**; the lab's 12a bar (a guard that acts needs a higher bar than one that reports); an agent fixing its own findings is the 08-02 "self-exculpation" pattern in reverse. |
| The grill read as "ask more" | The lab's rules say stop asking what the library or the frame already answers. The grill is DECISIONS only, batched. Today's one question was a fact — the failure the grill rule forbids, not the behaviour it licenses. |
| Hide standards from the implementer, as a default | **[D]**, one practitioner's post; it is an arm (R4). |
| Jev's 193× / 444× / "cannot hallucinate" | Vendor headlines; the lab measured its own candidates and found the value in the confidence, not the speed claims. |
| The external DSpark drafter | See R6. |
| Shrinking CLAUDE.md by judgement about what "the model remembers now" | The table exists because repeats cost the owner time. Only hook-backed retirements (R3) are safe; the rest stay until a hook exists. |
| A new shared glossary page | Low value for a build: component-names and vocabulary memories exist. A plain-language re-pitch ("wait-what") is a style the main session already owes under its own rule. |
| Parallel implementer agents as the default shape | One model per card; the serial driver first (R8). |

## 5. Open questions only the owner can answer

1. **CLAUDE.md:** may rows that already have a hook behind them be reduced to one-line pointers (text archived)? Is the
   Skill tool an acceptable home for "Before I X" rows, or must they stay auto-loaded?
2. **Grill turns from HAI:** how many questions per round is tolerable (Pocock: ~6 on a small change, ~20 on a normal
   one)? Should the recommended answer come from the lead, or — for decisions with a lab default such as tier or box —
   from McCoy's decision-model confidence, with only the sub-0.90 ones reaching him?
3. **Chat-or-job labels:** does he label the requests since 10-07 himself (his intent is the ground truth) or review
   Claude's labels?
4. **Reviewer placement:** is a reviewer on an 8 GB box or rigby acceptable while Mothership writes (the #36 split), or
   does he want the same lead reviewing fresh?
5. **Retro cadence:** every substantial session, or only after a delegation-heavy day like today?
6. **The MTP-off order:** run in Mothership's next idle window, or hold until layout C's RAM lands (10-17)?
7. **Merge-danger line:** written by the deploying session, or asked for by the gate?
