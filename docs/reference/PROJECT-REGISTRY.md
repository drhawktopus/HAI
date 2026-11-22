# PROJECT REGISTRY — every numbered project, one line each

**The canonical list. Check here before numbering anything new.** Built 2026-08-07 because there
was no such list: assembling it took four separate greps across CURRENT-PROJECTS,
PROJECT-BACKLOG, `LLM-Project\` and the journals, and an automated pass got five numbers wrong.

⚠ **Do not grep for the literal string `Project #N` to check if a number is free — it misses most of
them.** Real usage is inconsistent: `Project #15 —`, `#22 — ONE-CLICK BACKUP`, `#24 = the
self-quantized-VLM project`. That exact mistake made #17, #21, #22, #24 and #25 all read as
unassigned on 2026-08-07 when every one was taken. **Read this file instead.**

**State vocabulary (convention, the owner 2026-08-26):** `IDEA → SCOPED → REGISTERED → ACTIVE →
PARKED → SHIPPED / CONCLUDED`, dated where known. REGISTERED means numbered + scoped-for-later —
**registration is bookkeeping; nothing starts until the owner starts it.** Parentheticals carry the
detail. Legacy rows were mapped best-effort on 2026-08-26 — correct on sight if one reads wrong.

| # | project | state |
|---|---|---|
| #10 | Per-user RAG namespaces / nursing tutor | SHIPPED |
| #11 | HAI Homelab Operations Agent (admin-only host ops) — [[05-HOMELAB-OPS-AGENT-PLAN]] | SHIPPED |
| #12 | HAI Self-Engineering Agent — [[06-SELF-ENGINEERING-AGENT-PLAN]] · [[12-CAMPAIGN-CONCLUSION]] — CAMPAIGN | CONCLUDED 2026-08-31 |
| #13 | Friend Onboarding System | SHIPPED |
| #14 | HawkAdminBot (Discord admin bot) | SHIPPED |
| #15 | Game-server buttons + admin dashboard — `Reference\Project15-*` | SHIPPED (live) |
| #16 | Non-code HAI deliverables with verified accuracy (grounded-doc ladder) | SHIPPED |
| #17 | VGT work-helper persona (proof-of-concept for work) | IDEA |
| #18 | Conversations Suite (multi-chat) — [[10-CONVERSATIONS-SUITE-PLAN]] | SHIPPED (Phases 1–5 all shipped 2026-07-10 same night; forking / retention-quota / cross-user sharing deferred) |
| #21 | Novelty / celebrity / character pairings | IDEA (2026-07-13) |
| #22 | One-click backup / disaster recovery | PARKED (numbered 2026-07-14) |
| #23 | Voice interface to Claude ("talk while driving") | PARKED (v0 kicked off; no active work) |
| #24 | Roll our own abliterated / self-quantized VLMs | REGISTERED (2026-08-01) |
| #25 | True live dashboard via SSE (push, not poll) | SCOPED (2026-08-02) |
| #27 | Caption methodology — 27-CAPTION-METHODOLOGY | SHIPPED (in use) |
| #29 | Bespoke Klein — our own small model — 29-BESPOKE-KLEIN-SCOPE | SCOPED |
| #30 | **Library re-do — knowledge base, not a pile of documents** — CAMPAIGN | **CONCLUDED (2026-08-26)** — [[30-CAMPAIGN-CONCLUSION]] |
| #31 | **Password manager (Vaultwarden) — stop passing credentials through chat** | SCOPED (2026-08-07) |
| #32 | **Tolkien agent mode — the Fellowship's last poll becomes a push** | SHIPPED (core 2026-08-09: resident agent + ncnn detector) |
| #33 | **A front door for unwired hardware — lab bring-up as an order kind** | SCOPED (2026-08-15) |
| #34 | **Local model modernization + Fellowship experimental compute + adaptive orchestration** — CAMPAIGN | **COMPLETED 2026-10-25 night** on the owner's word (*"Mark 34 completed with notes"*), right after **HAI 1.9 launched on #34's build**. The 2026-10-24 reopens (area C step 3, "the request's own needs"; Phase 8's McCoy tuning on real traffic) were done; the last stretch added the matrix that considers every row, Quick / Hard as weights, McCoy advising while Spock places, owner home-first and everyone else cost-first, idle windows as caches, the rigby warm chat window and per-chat quick resume with the array behind it. Gates at the close: orchestrator 3260, Tolkien 974, foundry 570, all four factories on the same code. Conclusion: [[34-Closeout]] (§ "Final close" for the last stretch and what is still unverified) + [[34-CAMPAIGN-CONCLUSION]] (hypotheses closed out, after-action report); ledger [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]]; follow-ups in CURRENT-PROJECTS's top block; leftovers in PROJECT-BACKLOG § "#34 LEFTOVERS"; the judgment half → #35, after HAI 2.0. Earlier closes (10-23 22:47, 10-24 09:50) and reopens: project-registry-history-2026-10. Children: #36 (closed 10-16), #37 (completed 10-17) |
| #35 | **Native Intelligence — lab-derived behavioral training for local cognitive components** — CAMPAIGN | REGISTERED (2026-08-26; NOT started) |
| #36 | **Split inference across devices — helpers READ and DRAFT, the main machine WRITES** (rigby + the tablets first, then Mothership + the lab; child of campaign #34) — [[36-Split-Inference-Helpers-Read-and-Draft-Design]] | **CLOSED 2026-10-16** (the owner: *"Close it out baby and keep going"*; started 2026-10-10). rigby + GTX 1060 + MTP writes ~16.9 tok/s, the whole box stacks (A3B ~16 + Fellowship ~50 tok/s at once); final A1 role number 6/10, 0.70 solves/h; A1 runaway guard + context compaction live on all four factories. Findings + hand-offs to #34 Phases 7/8/9 and S10: [[36-Closeout]] |
| #37 | **HAI agent surface — one window, always an agent, live transcript on every device** (child of campaign #34; the product #34's placement rule feeds) — [[HAI-Agent-Surface-Design]] | **COMPLETED 2026-10-17 ~00:50 CT** (overnight, the main session; the owner's mission *"get 37 closed out overnight"*): the reopen's items are all LIVE - a silent model call judged by its box's load report, an unanswered run kept marked unfinished, the helper calls off fixed clocks, the effort pin (the unification the owner confirmed), the name picker on a phone - and answer 7's live proof DONE: HAI's McCoy-wiring job compacted at 90 % and carried on, the finish gate passed honestly, and the merge deployed (`3c09e70`+, gate 2825). **One confirmation is the owner's:** the new Effort / Work controls and the picker on his phone. Before: completed 10-17 after his phone check, reopened ~02:40Z for the phone layout and ~04:25Z for the clock. (Superseded leads: project-registry-history-2026-10, 37-closeout-history-2026-10.) Live: the same live run on every device, order cards, the agent surface (Auto = HAI decides; the pin follows the account), 🌙 Background, Quick Resume on every lane incl. overnight (drilled live), the work loop (finish gate + own-tests rule, compaction, talk to it while it works), critics that see the work, every factory's LLM orders on the tiles, unverified work kept as a marked checkpoint (the owner's #12 rule). **#37 never changed routing**: Auto = McCoy's triage + Spock's priced menu; the 3.8 is the owner's pin and the unsure-fallback. Hand-offs (each owned in the #34 register): the McCoy-wiring job proves the finish gate + compaction live; multi-user overflow + "the owner first" → Spock; a critic on another box → Spock; an A1 reviewer arm; lend-the-GPU; KV dump at park (only if restarts prove costly); decomposition → S8. Everything: [[37-Closeout]]. (Superseded row: project-registry-history-2026-10.) |

| #38 | **Lab update system — a notifier for every driver / program / OS update, staged and tested, rolled over blue/green, smart archiving** (the owner, 2026-10-22, after Mothership's install-triggered 0x9F) — scope in PROJECT-BACKLOG; home [[Lab-Update-Inventory-38]] | ACTIVE — **BUILT 2026-11-04**, every class running nightly; open: AMD drivers wait on the owner's Acronis console step; engine rebuilds stay a person's (lab-patched) |
| #39 | **Jade Empire: the owner Remake 1.0 — a private 4K remake / remaster-plus of *Jade Empire: Special Edition* (Steam copy), couch-playable on Mothership's display** — CAMPAIGN — source packet in `Archive\jade-empire-remake-proposal-source-20261024\`; charter [[39-CHARTER]]; scope history in PROJECT-BACKLOG § "CAMPAIGN #39" | KICKED OFF 2026-10-30 — Phase 1 (foundations & the adopt question) |

**Next free: #40.**

## Campaigns — a classification, not a second numbering space

**Convention launched by the owner, 2026-08-26.** A CAMPAIGN is a large, spanning endeavor that
contains (or will contain) several sub-phases/projects/plans and must be scoped and planned
accordingly — as opposed to a single-deliverable project. Campaigns live in this same registry
with ordinary numbers (one registry, one namespace — a parallel "campaign number" space would
drift); the word CAMPAIGN in the project column is the flag.

Two more campaign conventions (the owner, 2026-08-26, same pass):

Current campaigns:

| campaign | member/child work | charter |
|---|---|---|
| #12 HAI Self-Engineering | Phases 1–5 + Independence close shipped — **CONCLUDED 2026-08-31** | [[06-SELF-ENGINEERING-AGENT-PLAN]] · [[12-CAMPAIGN-CONCLUSION]] |
| #30 Library re-do | docs half + code half (all four mirrors) + the recon reuse of its machinery — **CONCLUDED 2026-08-26** | [[30-CAMPAIGN-CONCLUSION]] (charter consolidation + conclusion report, incl. maps) |
| #34 Model modernization + Fellowship compute + adaptive orchestration | areas A/B/C, phased like #12; child projects **#36 split inference** (2026-10-10, closed 10-16) and **#37 HAI agent surface** (2026-10-16) | [[34-CHARTER]] (2026-08-27) |
| #35 Native Intelligence | recon Parts 1–3 (done) + training/eval phases to come; **intake from #34:** C3/C4/C5 (train the judgment; the corpus is the lab's transcript stores — *"thousands of transcripts"*, the owner 2026-10-24 — not "blocked"); the two-layer design's MECHANISMS are #34's own (area C step 3, reopened 10-24); **sequenced AFTER HAI 2.0 drops** — the owner, 2026-10-24 afternoon: *"that's making a model from scratch when we already are supposed to have models that work for now. Custom baked McCoy is maybe a better one for a later day after I drop hai 2.0, since McCoy is already new to hai 2.0"* (HAI 2.0 ships on #34's build, nothing half-built visible to users) | owed at kickoff (`35-*.md`) |
| #39 Jade Empire: the owner Remake 1.0 | Phases 1–5: foundations & the adopt question → native enhancement → structure & content → engine extension → New Game+ playground; stoppable after any | [[39-CHARTER]] (2026-10-30, with the campaign map and phase timeline) |

Member projects keep their own numbers and rows above — the charter, not the registry, carries the
phase detail.

## Epics — threads that tie campaigns together

**Convention set by the owner, 2026-08-27.** An EPIC is a named THREAD that runs THROUGH campaigns — a
long arc several campaigns/projects serve, larger than any one of them. Like campaigns it is a
classification in this ONE registry, never a second numbering space; an epic carries no number of
its own, it NAMES its member campaigns/projects.

The word was reclaimed from the code the same day: decompose.py's task-scale "epic" (one objective
decomposed into test-verifiable leaves) is now called a **commission**, so "epic" means only this
registry tier. Legacy `kind: epic` data still loads — the code maps it to `commission`.

Current epics:

| epic | the thread | members |
|---|---|---|
| **Delegation** | work moves from Claude's hands to the lab's own machinery — self-eng builds the mechanisms, orchestration modernizes the compute they run on, native intelligence trains the judgment into local models | #12 → #34 → #35 |

Naming further epics is the owner's call, same as campaigns; add a row here in the same pass.

**#1-#9 are not numbered projects.** The convention starts at #10; earlier work is phase-based in
[[00-MASTER-PLAN]]. A bare `#3` in the library is almost always a follow-up index, a
tier, or a footnote — not a project.

## When numbering something new
1. Read this table; take the next free number.
2. Add the row here **in the same pass** — a number that exists only in a journal is not registered.
3. Scope it in PROJECT-BACKLOG, and if it is starting now, announce it at the top of
   CURRENT-PROJECTS (that is what a session reads first).
