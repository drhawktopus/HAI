# Project #30 — Division of Labor: Claude, HAI, and the owner (who does what, how work is delegated)

The definitive statement of how work on the lab is divided between the owner, HAI (the Home AI
orchestrator), and Claude, and how it is delegated and authorized. **This is the North Star:** a
session or assistant unsure "who does this / how does this run / didn't the owner say something different
once" reads here for the answer and its resolution, instead of asking the owner to re-explain. The
conclusions live here; the dated blow-by-blow evidence (every quote, with timestamps) is the receipts
log at `Journal\2026-08-24-claude-dol-timeline.md`.

## Bottom line (the conclusion)

Across the whole record (2026-07-24 → 08-25), **the owner's position has been consistent** — this is one
coherent design restated many times, not a moving target. The split, in one paragraph: **the lab is
built to run itself on HAI; the owner directs and authorizes; Claude is a contractor who helps build it
today and is brought in occasionally after.** Work flows as **orders** the lab routes, chunks, and
fulfills — Claude does not hand-do the pipeline's job, and does not pre-split what Kirk/Spock split.
Claude's one carve-out is **engineering** (building a needed capability); the **removal/rewrite work
itself is HAI's.** The apparent contradictions people trip on all resolve (see *Reconciled tensions*);
none is the owner changing his mind. When in doubt, lean on this doc and the plan — do not re-derive.

## Two regimes — end state vs today

- **End state (the design target).** The lab depends on **HAI, not Claude.** HAI runs the work on
  its own loop; it presents the owner findings; the owner directs and authorizes. Claude is **an external
  contractor the owner brings in occasionally** — not part of the final machinery, and the final
  machinery does not require Claude coding for every need. It is a finished, self-running system.
- **Today (transitional).** Claude and the owner build toward that end state **together**, until the work
  is done or Claude's usage runs out. Everything Claude and the owner build is designed so **HAI (or
  Codex) can pick it up** if Claude drops out — but "designed for HAI pickup" is a *fallback for
  continuity*, not a handoff of the current work to another assistant.

## Roles

- **the owner** — directs where the work goes and authorizes it. He is not the mechanism; he does not code
  for each need. Do not gate routine, doctrine-covered actions on him (archive-first backups are
  self-authorized); reserve his explicit yes for genuine safety writes and irreversible actions.
- **HAI** — does the bulk of the work through the lab's own mechanisms (the order pipeline, the gated
  library writers, the review tools). If HAI lacks a capability, the capability gets wired and
  declared — not worked around by hand.
- **Claude** — supervises and reviews at the margin; directs and authorizes alongside the owner in the
  transitional regime. **Claude may do engineering** (see the seam rule below). Claude does **not**
  hand-do the pipeline's job.

## The engineering-vs-work seam (the rule that is easiest to get wrong)

"Run it through HAI / you are not the machinery" and "you may do engineering" are **not** in conflict.
The scope is exact:

- **HAI-eligible work** = doing the pipeline's job — e.g. the #30 comment/narrative **code removal
  and rewriting** across the codebases. HAI drafts on-box and invokes the gated apply tools; the owner
  authorizes; Claude reviews at the margin. Claude does **not** hand-edit those files.
- **Claude-eligible engineering** = building the small capability the pipeline needs (a payload, a
  gated tool, a detector) when waiting on self-engineering would stall progress. This is the
  carve-out; it does not extend to doing the removal work itself.

## How work runs — as orders

- Work is submitted as a **factory / starfleet order**, not run by hand and not built as a new
  parallel mechanism. Much of the pipeline is already automated, self-running, and self-splitting;
  reuse it rather than reinventing it.
- The **factory declares its capabilities** (including which models each box runs); **starfleet
  command (Kirk) routes** on those declarations and **Kirk/Spock chunk and split** the work.
  Claude does **not** pre-split — chunking is part of the order the lab fulfills.
- If a required capability is not wired, the system **rejects or holds** the order and reports what
  needs wiring; it does not silently improvise.
- Routing is decided by Kirk/Spock, but may be requested manually in the order.
- Do **not** reach for the `selfeng.overnight` build as a template — it is a special case, not the
  default intake. The default is the standard order path.

## Routing constraint

**Softserve is last** (it is the Plex host, and a session's own host); spread work across the three
factories, and a box drops out when Plex needs its GPU. Route compute/verify to BigHonker or
Mothership by default.

## #30 goals (what "done" means)

- The library re-do makes the library **efficient and removes dead content — it is not just about
  making it smaller.** Do not over-cut; a wiki that documents completed work and findings is fine.
- **Findability is not currency.** Structure that makes docs findable does not make them *correct*;
  a document must state what is true **now**. The document tiers exist for this reason (live lab =
  what is; docs = intended design + how-to; archive = superseded). Where the lab has evolved past a
  doc, measure live and update the doc — do not re-derive and do not make the owner adjudicate stale docs.
- **Code and docs are both in scope**, to the same standard: written the way a normal-practices,
  skilled human writes — **not comment-city, no war-stories.** A comment survives only if it passes a
  **two-bar AND test**: it states a true constraint the code cannot show, AND it is dense enough that
  a skilled human would bother writing it there. Fail either bar → cut.
- **Nothing is lost.** Removed narrative is **re-synthesized** into the journals and, where framed as
  design doctrine, the manuals — not deleted. Superseded content moves to the Archive; removal of
  old content is justified or points to its successor.
- **Narrative in the library** is allowed **only when framed as design doctrine or a constraint**;
  pure commentary/discovery narrative belongs in the work journals (charter rule 1). This refined the
  earlier "no narrative in docs at all," which the flat reading over-applied.
- The project is **not done until the Obsidian integration is fully in place**, with the tiers
  correctly applied.

## Reconciled tensions (things that look contradictory — and the resolution)

These are the points where a reader thinks the owner contradicted himself. He didn't; each resolves. This
is the section to read when confused.

1. **"No narrative in docs, only journals" vs "narrative can go in the manuals too."** Resolution:
   narrative belongs in the library **only when framed as design doctrine or a stated constraint**;
   pure commentary/discovery/war-story narrative belongs in the work journals. The later statement
   *refined* the earlier flat rule — it did not reverse it. (This doc is an example of the allowed
   kind: conclusions and rules, not a discovery story.)
2. **"Run it through HAI / you're not the machinery" vs "you may do engineering."** Resolution: HAI
   does the bulk work through the lab's mechanisms; Claude's **only** takeover is **engineering** a
   needed capability when waiting on self-engineering would stall progress. The **removal/rewrite
   work itself stays HAI's.** Scoped exception, not a contradiction. (This is the seam that gets
   muddied most — see the seam rule above.)
3. **"Design it so HAI/Codex can pick up" vs "you and I finish this."** Resolution: designing for
   pickup is a **continuity fallback** in case Claude's usage runs out; it is **not** parking the
   work for another assistant. In the present regime Claude and the owner work until it's done or usage
   runs out.
4. **Present role vs end-state role.** These are two regimes, not a contradiction: **today** Claude
   is an active builder alongside the owner; **at the end state** Claude is an occasional external
   contractor and HAI runs it. Conflating the two is the most common self-inflicted confusion.

## Why this keeps getting re-explained

The repetition is driven by Claude re-deriving or drifting across sessions and compacts, not by the owner
changing his mind — the record is consistent (the owner named this same "same conversation 15 times a day"
pattern on 2026-07-24, a month before #30). The standing remedy is to **lean on this doc and the
plan** rather than reconstruct the split from scratch. Full dated receipts:
`Journal\2026-08-24-claude-dol-timeline.md`.
