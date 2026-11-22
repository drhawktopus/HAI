# 30c — HAI in the library execution loop

**Status: ✅ DELIVERED 2026-08-22.** Parent: PROJECT #30 (library realignment); sibling: 30b
(fleet triage — DELIVERED). Exit bar met: Stage 8 executed end-to-end through the three lanes —
VERIFY worklist dispositioned, FIRM archives moved, 17 mixed-doc rewrites drafted on the 27B,
reviewer-patched, and applied — with review margins holding (reviewing a drafted doc costs
minutes against hours of hand-rewriting; every lane defect was caught by mechanical checks plus
a targeted read). Phase O (HAI operator tool) remains the open follow-on. Execution record:
Journal 2026-08-22.

## The goal

30b delegated the READING (the fleet triages 728 sections in ~6 min). The remaining #30 labor is
EXECUTING: verify flagged claims, archive the concluded material, rewrite mixed docs to charter.
That loop repeats for stages 4, 6, 7, 8-execution, and 9 — so integrating HAI/the fleet here
multiplies every remaining stage and takes Claude's usage out of the mechanical middle. Humans
keep judgment (the charter's own rule); Claude keeps supervision and the hard cases.

## What already exists (build on it, never beside it)

- **The work is already atomized.** Triage output = per-section rows with tier + action +
  rules-mined claims. Median section 859 chars — every unit fits ANY tier's context window with
  the charter beside it. HAI's documented failure mode (window overflow on big-file multi-edit
  self-changes) does not apply to this shape.
- **Order machinery** (30b): capability-gated routing, parallel dealing (size-aware, measured
  weights), typed refusals, receipts, `notify: "failures"`. Batch work goes through the desk —
  the chat-door tripwire ruling stands.
- **HAI's durable state** (coding-runner): `task` (objective + acceptance criteria),
  `decision` (2–4 options presented to the owner, persisted, resumable), `project_driver`
  (dependency-gated multi-task graphs), `checkpoint`, independent `coding_review` on the 27B.
- **A rewrite gold set, free:** the Archive holds the pre-rewrite snapshots of completed stages
  (`runbook-pre-charter-rewrite-20260821`, `current-projects-pre-stage2-20260821`, …) — real
  before→after pairs a human approved. Ground truth for calibrating the rewrite lane.
- **Mechanical conformance grading exists:** the per-stage narrative-marker greps (⚠ density,
  "a session", corrected-dates, first-person) already measure charter conformance numerically.

## The three lanes

### Lane 1 — VERIFY (the 89-section rules-flagged worklist)
Split by what settles it:
- **Instrument-checkable claims** (a task exists, an IP answers, a file/path exists, a service
  responds): a read-only probe payload run as **cpu compute orders** — the fleet pattern, zero
  model involvement, verdicts with evidence. Output: per-claim CURRENT / STALE / UNREACHABLE.
- **Judgment claims** (a described behavior, a design still true?): HAI reads the section + the
  live source its claim names, answers a schema (verdict + evidence + confidence), 27B tier,
  `ephemeral`, served-model recorded per row. Low-confidence and UNREACHABLE rows fall to the
  human worklist — an absent value never proves an absent feature.

### Lane 2 — REWRITE (mixed + keep docs, drafts only)
Per SECTION, as fleet orders (the classify pattern with a rewrite payload): input = charter core
rules + section text + its triage row; output = the declarative rewrite draft + a cut-list
(what moves to archive/journal). **Drafts land in staging (`State\triage-drafts\<doc>\`), never
the live library.** A human (the owner or Claude) reviews and applies; the REVIEW-MARGIN INVARIANT
binds — if reviewing a draft costs more than authoring it, the item re-queues with the failure
named, and repeated failures park the lane.

### Lane 3 — ARCHIVE (the FIRM candidates)
Mechanical: copy → sha256-verify → delete source, dated folder + ARCHIVE-README naming what and
why (the 07-24 mechanics, already doctrine). Scripted payload, **dry-run first, human confirms
the batch, then execute** — destructive steps stay human-gated. HAI's role: compose the batch
manifest + the READMEs from the triage rows.

## HAI-the-orchestrator's role (the succession argument)

Kirk's brain is the OPERATOR, not the laborer: it submits the orders, tracks lanes as
coding-runner tasks with acceptance criteria, presents batch sign-offs as decision gates
(`/pending?user=the owner`), and answers "where is the library work?" from the durable stores. The
laborers are the fleet (orders) — same shape 30b proved. HAI never calls Claude on its own
(standing rule); escalation is a decision gate naming what failed.

## Reliability gating (the "reliably figured out" bar)

1. **Rewrite calibration first**: N sections sampled from the completed-stage before→after pairs
   → the rewrite payload drafts from the BEFORE → graded against the human AFTER on (a) the
   mechanical conformance greps, (b) zero invented facts (every claim in the draft traceable to
   the before-text — spot-checked by human), (c) cut-list correctness. Ship the lane only when
   the review margin holds on the sample.
2. **Every row records the serving model** (drill doctrine); a row served by the wrong tier is
   refused from grading, not averaged in.
3. **Boundary noise is known** (69% raw / 83% actionable agreement): BORDERLINE docs and unsure
   rows never enter automated lanes — they are the human's list by construction.
4. **First real batch small**: Stage 8's 4 FIRM archive docs + one mixed doc's rewrite, end to
   end with sign-offs, before any full-stage run.

## Rollout order

1. Verify-lane probe payload (cpu orders) over the 89-claim worklist — lowest risk, immediate
   value, no model judgment.
2. Rewrite calibration against the stage snapshots → the owner reviews the sample grades.
3. First real batch (Stage 8: 4 FIRM archives + 1 rewrite) with decision-gate sign-offs.
4. Then stages 9 → 4 → 6 → 7 through the same loop; #30 main mission resumes with HAI carrying
   the mechanical middle.

## Rulings

1. **Reviewer = whoever submitted the order.** During the build, the owner + Claude co-review;
   the end state has the lab presenting findings to the owner, with Claude external and invoked
   on request.
2. **Rewrites and other judgment orders run on the LEAD for judgment work** (`requires` =
   `config.judgment_requires()`: `order.compute` plus the model the lead POSITION holds when the
   order is placed; declared-models routing, no host pin and no model name at a call site) until a
   smaller model earns its stripes on calibration.
3. **Staging home: `State\triage-drafts\<doc>\`** — drafts never touch the live library.
4. **Verify probes are a blanket read-only class**: a payload that provably only reads runs
   as ordinary compute orders with no per-batch confirmation. Reads are ungated; writes are
   preview→confirm — the same doctrine as HAI's tool gates.

## Design doctrine (as built)

* **Verdicts grade the DOC LINE, not the resource.** `CURRENT` = the line is still true;
  `STALE` = no longer true (e.g. the path is gone); `UNREACHABLE` = the target did not
  answer, which may be a down service or a stale line — the human decides; `CANNOT-TELL` =
  the instrument could not measure, never rendered as any definite state; `ERROR` = a row
  the payload has no handler for, named per-row, never silent.
* **Read-only is provable, not promised.** The verify payload's only subprocess calls are
  `schtasks /query` and `tasklist`; its only write is the verdicts deliverable. Its tests
  pin this behaviourally (recorded subprocess argv allowlist, only-artifact-created sweep).
* **Gold controls ride every claims file** — per box: a present task, a present path, an
  absent path, an open port, a closed port, a live endpoint, and an unknown claim type. The
  instrument is validated on each box before any negative verdict is believed; the `expect`
  field is grader-only and the payload never reads it.
* **Zero rows addressed to a box exits rc 2** — every box always has gold rows, so a
  zero-match join can only mean breakage, never a clean sweep.
* **The residency gate has four paths**: fully resident → pass; absent from `/api/ps` →
  one clean load, then judge (absent is not occupied); spilled → evict (`keep_alive: 0`) +
  one clean reload, then judge; still spilled after the one honest attempt → refuse, the
  card is genuinely occupied. Shared by both lanes from `triage_classify`, one derivation.
* **The serving model is read from the RESPONSE per row.** Routing is a request; only the
  response says who answered. A draft served by the wrong tier is refused at review, not
  graded as noise.
* **A `num_ctx` is calibrated to a model.** An order that pins a model a box's triage row
  does not match must also state its window; the payload refuses rather than borrowing a
  mismatched row's context size.
* **Drafts carry their provenance**: the charter version (`triage.charter.v1`, byte-tied to
  [[LIBRARY-CHARTER]] at deploy) and the serving model, so every draft is
  attributable to the charter and model that produced it. Every cut in a draft names its
  destination (journal / archive / component-manual) — the schema offers no silent drop.
* **A rewrite applies only with its cuts filed.** The pre-rewrite text is archived verbatim
  first (nothing is ever lost, even a mis-filed cut). Then every cut in the draft is filed
  at its named destination — or verified already present there — in the same pass as the
  apply: component-manual cuts are load-bearing facts being MOVED, not history being
  parked. The reviewer's checklist is the draft's own cut list, grouped by destination.
* **The same rule governs narrative stripped from CODE**, which has no automatic archive
  snapshot: a narrative comment is cut only after its content is verifiably in the journal
  or the owning manual (charter rule 9's removal half).
* **Drafts are Obsidian-compatible, not Obsidian-complete.** The #30 wiki/Obsidian
  restructure (single-topic page splitting + `[[wikilink]]` conversion) is deferred and
  gated on a tooling decision. Rewrites therefore stay plain Markdown with no wikilinks and
  no frontmatter, and keep cross-references as `see <FILE> §<heading>` so the restructure's
  library-wide pass converts them mechanically. Clean single-topic sections with sharpened
  H1/H2 titles are the prep the split consumes — the lane builds toward the restructure, it
  does not pre-empt it.
* **Compute payload authoring rules** (sibling imports under embedded interpreters,
  fail-soft import pins, the failure-DM reason) are in the Runbook's compute-order section.
