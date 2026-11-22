# #12 — Auto-Decompose Driver for Supervised Coding Jobs (design + build record)

**Status: ✅ BUILT AND DEPLOYED 2026-08-27 (overnight)** — gate `Ran 2241 OK (skipped=2)`, 15
driver pins (5 P1 evidence-contract + 8 P2 worker + 2 P3 real-machinery integration) + the
interactive-flow contract pin. Invocation: a ClaudeSupervisor `SUPERVISED LOCAL CODING JOB`
packet with header line `Mode: decomposed edit-test-review` via `POST /absurd/start`
([[RUNBOOK-HAI-Coding-Escalations]] §DELEGATE). Two build corrections worth knowing: the worker
originally checked an invented plan verdict ("accepted" — the real vocabulary is
`ready`/`needs_review`), caught ONLY by the P3 integration pin (the mocked unit pins spoke the
same invented dialect — a fake must speak the production vocabulary); and the evidence
requirement briefly broke the interactive planning flow's contract, resolved as the opt-in
`strict_evidence` (the driver demands it; the human flow is unchanged and now pinned so).

**the owner's directive** (2026-08-26 night, watching the G4 build): *"We need to teach that
decompose to the lab system, so it can automatically do that."* Registered in
PROJECT-BACKLOG §2026-08-27; calibration evidence in `Journal\2026-08-26-claude.md`
§"#12 RESUMED" and §"G4 CLOSED".

## The problem, measured

A supervised coding job too big for one tool loop fails in a characteristic way (G4 round 1,
absurd `abr-1787796507`): the 27B maps every symbol correctly, exhausts its budget reading,
writes nothing — then fabricates when a refine pass improvises. Hand-decomposition fixed it the
same night: budget-sized packets with the exact code EMBEDDED landed cleanly (the hoist used
17/50 tool steps). The supervisor's costs concentrated in spec-writing and diff review; the
27B's merge/test/checkpoint/escalation behavior was correct throughout.

Two calibration facts the existing planner does not yet encode:

1. **The map must travel with the leaf.** Each leaf carries the exact snippets/anchors it needs,
   gathered at PLANNING time, so a fresh leaf context spends its budget WRITING, not re-reading.
2. **Leaves are budget-shaped.** Sized in expected tool calls: `decompose.PLAN_SYSTEM` rule 8 asks for at most
   ~25 per leaf, not only "independently test-verifiable". That was half the old 50-turn loop cap; since
   2026-10-05 no cap enforces it, so it is a sizing heuristic only (open question for the owner: keep, or derive it).

## What already exists (build on it, never beside it)

- **`decompose.py`** — commission → ordered, test-verifiable leaves + one integration leaf
  (the noun was "epic" until 2026-08-27; "epic" now names the registry's campaign-connective tier);
  draft/critique/refine with injected model calls; structural validation; topo order. Wired only
  to the overnight-backlog path today.
- **The coding runner** — durable tasks, isolated worktrees, checkpoints, resume,
  finish(merge)/abandon, allow-listed tests, independent fresh-context 27B review.
- **The Absurd lane** — async supervised runs: one-at-a-time on the 27B, energy-gated, status
  pollable, self-reporting webhook. The G4 build ran on it all night.
- **`CODING_JOB_TOOLS`** (shipped same night) — machine-user supervised packets get the
  purpose-built coding SET, so no leaf can lose a tool to selection ranking.

## Design

**Trigger (explicit, never heuristic):** packet header `Mode: decomposed edit-test-review` on a
machine-user `SUPERVISED LOCAL CODING JOB`. No size guessing — the supervisor (or later, HAI
itself via escalation-on-exhaustion) opts in.

**Plan pass:** `decompose.emit_plan` with propose/critique wired to the heavy model.
Additive extensions to the plan contract:
- `Leaf.evidence` (new field, default ""): verbatim snippets + `file:line` anchors this leaf
  needs. The plan prompt REQUIRES it per leaf; structural validation flags an implementation
  leaf with empty evidence.
- Budget rule in the plan prompt: each leaf's objective must be achievable in ≤ 25 tool calls;
  the sizing guidance already in `PLAN_SYSTEM` (merge trivial leaves) stays.

**Plan-gate calibration (2026-08-27, the owner's pick after the first two live refusals):** the
critic's any-finding-is-a-veto contract (`parse_critique` flips pass→changes_requested on any
finding) plus a missing rebuttal channel let advisory-grade nitpicks — and one line-anchor
misread the critic rated "high" — hold two reasonable plans hostage for all three rounds. Two
driver-only opt-ins fixed it, both fail-soft, the interactive `/plan` flow unchanged:

- **Advisory pass** (`advisory_pass=True`): only CRITICAL-severity model findings (plus every
  structural and human finding) block acceptance; sub-critical survivors ride the run record as
  `advisory_findings` warnings instead of forcing another round.
- **Dispute exchange** (`dispute=_plan_dispute_dep(...)`): deliberation.py's fixer-dispute
  ported to the plan domain (`PLAN_DISPUTE_AUTHOR_SYSTEM`/`PLAN_DISPUTE_ADJUDICATE_SYSTEM`) —
  the planner answers each finding fix-or-dispute with quoted evidence, the decorrelated critic
  adjudicates each disputed finding skeptically (uncertain = the finding stands), an upheld
  rebuttal drops it. A finding now costs proof, not just an utterance.

**Execute loop (the driver):** leaves in topo order, each as ONE fresh-context chat cycle
carrying: the commission header + the leaf's objective/acceptance/evidence + a standing constraint
block (isolated task, only this leaf, finish(merge) on green). Per leaf:
`coding_task(start, isolate)` → implement → allow-listed tests → checkpoint → independent
`coding_review` → `finish(merge)` on pass. On a failed leaf: ONE retry carrying the review
findings verbatim; a second failure → `coding_escalation` + STOP the loop (fail closed — later
leaves depend on earlier ones; a half-built chain must not keep building on sand).

**Home:** a mode of the Absurd worker (same async/status/energy plumbing; `last_status` gains
"leaf k/n: <title>"). One new entry field on the run record: the plan trail. No second pipeline.

**Terminal hygiene:** every task the driver opens reaches `finish` or `abandon` before the run
record closes — the stale-task busy-pinning class (8 orphans on 2026-08-27) is structurally
impossible on this path.

**Boundary unchanged:** the driver replaces the SUPERVISOR'S hand-packeting, not the
supervisor. Workspace merges stay inside the runner; promotion to production remains a human
act (review → port → gates → deploy).

## Increments (the build's own leaves)

- **P1** — `Leaf.evidence` + plan-prompt evidence/budget rules + structural check
  (decompose.py) + pins.
- **P2** — the leaf-executor loop in the Absurd worker + pins (mocked model + runner).
- **P3** — wiring: mode detection, leaf-progress status, abandon hygiene, integration test
  driving a two-leaf commission end-to-end against a fixture workspace.

## Maiden voyage (2026-08-27) — first live commission, and the packet-contract rule

`abr-1787840129-fba740` (the NI kickoff tool `run_review_campaign`): gather → plan → 3/3
leaves → done, unattended, first live use of the advisory pass (6 non-critical findings
persisted, plan proceeded — the pre-hardening gate would have refused it). Ported to
production after margin review, deploy cycle gate 2278 predicted exactly.

**Leaf-sizing rule (binding on every multi-concern packet; earned on commission 3,
2026-08-27):** give PRESCRIPTIVE plan-shape guidance — name the leaves, one concern each.
Left to itself the planner sizes leaves by topic, not by context budget: commission 3's
leaf 2 bundled a decision table AND four submission builders, and ~90 minutes in the 27B
degraded into malformed tool calls (null argv fields) and burned both attempts. The
driver's whole premise is a fresh context per leaf — packet guidance is what keeps every
leaf inside one. Budget note: at the observed ~40 min/leaf, pick the time choice so
(leaves + gather + plan) fits with margin; `all_night` is cheap insurance, runs still
stop early on completion.

**Packet-contract rule (binding on every future packet):** the coding workspace is the
model's ENTIRE world — any contract whose source lives outside it (payload argv in
`mothership-build`, container mount points, live endpoint shapes) must be embedded in the
packet VERBATIM, or the model invents a plausible dialect and its own mocks pin the
invention green. Both maiden defects were this class: invented argv for the two NI payloads
(real contracts: extract `--out-dir <dir> --mode <mode>`; review positional
`work.json review.json`) and a `Path.exists()` on a UNC path that can never resolve inside
the Linux container (fixed with `config.LIBRARY_TRIAGE_MOUNT` mapping). A green
workspace suite proves consistency with the packet, not with the world.

**Corollary, earned on commission 3:** the rule covers only what the packet actually
embeds — commission 3's packet embedded argv, file names, and the `status` field of review
outs, but NOT their row-array key, and the model invented `items` (the WORK-file key) for
reading review rows, with its own fixture pinning the invention green; the margin review
caught it because the reviewer had measured the real key (`results`) the same day. So:
embed the SCHEMA of every cross-boundary file the code reads or writes — every key it will
touch — not just the file's name and the fields the design mentions.

**Observed drift → the survey lock (commission 2, `abr-1787856467-8db053` — LANDED +
DEPLOYED 2026-08-27):** the maiden gather pass ignored its read-only instruction and
implemented during survey (contained — nothing merges without finish(merge)). Fix, built
by the driver itself in 65 min (done 2/2 leaves) and ported the same day:
`CODING_SURVEY_TOOLS` (inspect/search/library only) selected by `_heavy_toolset` — still
the ONE toolset derivation — when the packet carries the shared `SURVEY_MODE_LINE`
constant, which the gather packet (producer) and the matcher (consumer) both use so they
cannot drift. Enforcement is mechanical, not prompt language. The commission transcribed
the packet's embedded code byte-exactly (its one deviation, a redundant fifth test, was
dropped at port); gate 2282 predicted exactly. Every commission from the third onward
runs its survey under the lock.

**Run-record status contract:** the driver owns `phase`
(gathering → planning → executing → done/error; deep-think on the classic path) and
`progress` (`leaf 2/3`) fields, pinned by worker tests — the deck's Deep Work tile renders
both. `last_status` remains the deepest layer's live line; overall progress comes ONLY from
these driver-owned fields.

## Correction-A validation dogfood (2026-08-29) — supervised follow-on converged

Run `abr-1788059989-e2fdb9` implemented the live-runs roll-up's first slice: project active
GPU-factory records into the same row shape as Absurd runs and append them in
`GET /runs/active`. It ran five passes, survived two substantive changes-requested rounds,
and ended **pass with 0 findings**. The earlier self-regenerating pin-stop stdout finding did
not recur, and the worker did not refuse its own correct work. That is the intended
one-variable evidence that correction A held; correction B remains unarmed.

The supervisor's post-merge live probe caught a contract defect that the green fixtures could
not: the packet said idle/stale/releasing runs were blanked to `state=""`, but the real
`p20_factories()` output uses `name="" state="idle"` for an idle host. HAI implemented the
packet faithfully; the packet was wrong. Final production code mirrors the producer's own
show-run predicate: a displayed run has a name and a state outside `("", "done")`. Live proof
showed the running Fellowship gauntlet and excluded three idle factories. Nine focused tests
and the full **2378/2** gate passed; sync baseline `367148a`.

**Packet-contract addendum:** embedding a cross-boundary schema is necessary but not sufficient
when the contract includes derived semantics such as liveness. Pin at least one fixture from
the real producer output, or run a live positive/negative probe before declaring the packet
correct. A mock copied from an incorrect packet only proves the implementation matches the
incorrect packet.

## Later (cross-campaign, deliberately not now)

- Parallel independent leaves across factories — bounded today by coding-capable model capacity;
  **#34**'s lane (which cards can host a coding tier).
- The judgment half (when to decompose, leaf sizing instincts) as a learned disposition — **#35**.

## Campaign-close integration (2026-08-31)

Project #12 concluded with the driver inside a clean order-owned system rather than as a parallel
session-side scheduler. The closing five-pin suite entered once as `eval_campaign` order
`86ae2ce3d046`; Kirk decomposed/executed, Spock placed each pin, factories owned timing/residency,
and the client only read and externally graded the evidence product. Result: five clean rows and
5/5 accepted after E1/E4 human review. This is the terminal #12 proof of the same responsibility
split the commission driver was built to enforce.

The driver remains inherited machinery. #34 owns future compute/placement evolution; #35 may learn
decomposition judgment, but deterministic authority and arming remain outside the learned model.

External comparison (2026-10-16): the packet-contract and leaf-sizing rules above match Matt Pocock's grill → spec →
fresh-context tickets flow; see [[Research-Pocock-Agent-Skills-Workflow-Video-2026-10-16]] for the point-by-point and
the candidate adoptions (nothing here changed).
