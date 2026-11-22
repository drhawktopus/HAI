# Project #30e — the 29 MOVE comments, REVIEWED (files-twice gate + two-bar re-check)

Companion to [[30e-CODE-NARRATIVE-CLEANUP]]. The three green mirror applies left every **MOVE**
disposition byte-unchanged in the source on purpose: files-twice says a narrative comment may be cut
only once its knowledge exists in the **owning manual**, and that preserve gate is the reviewer's
job, not the applier's. This doc closes that gate for all 29 and records a two-bar re-check of each
disposition, so the remaining cut is a mechanical apply with no judgment left in it.

Reviewed 2026-08-26. Source worklist: `library-triage\s30-code-MOVES-worklist.json` (drafts joined to
work packets by id, verbatim comment text preserved).

## Headline: the drafter over-MOVEd. 19 of 29 must keep a terse constraint in code.

This is [[30e-CODE-NARRATIVE-CLEANUP]] PILOT LEARNING (a) reproducing at scale — the 27B leans
aggressive-CUT and scores the wrong criterion, treating "the fact is filed in a manual" as grounds to
remove a code-local constraint. Files-twice deliberately keeps load-bearing constraints in BOTH
places. Applying these MOVEs as drafted would have deleted a don't-re-add-this warning at each of the
exact sites where someone would re-add it.

| verdict | count | meaning |
|---|---|---|
| **KEEP-CLEAN (terse)** | 19 | over-MOVEd: strip the story, a real constraint survives in code |
| **CUT-OK** | 7 | knowledge is filed and nothing code-local survives |
| **FLAG FOR owner** | 3 | no clear owning manual; genuinely ambiguous (DECISION 3) |

## The files-twice scan — and why the first run was wrong

The check greps the **live manuals only** (`Reference`, `Runbook`, `LLM-Project` — 202 docs). A first
run that also searched `Journal\` and `Archive\` returned a clean **29/29 FILED** and was false:
files-twice is explicit that the journal is the STORY and an archived snapshot is superseded, so
neither satisfies the gate. Scoped correctly the result was **23 filed / 6 unfiled**.

The scan carries a positive and a negative control and refuses to report if either fails — a
MISSING verdict from an unvalidated matcher is worth nothing. Instrument:
`scratchpad\files_twice_check.py` (hand-picked probes per candidate; an auto-extracted probe reports
absence for a fact that is simply filed under different words).

**Filed during this pass:** the `p20_intent` family (ids 0017, 0103, 0598) →
[[HAI-Core-Architecture-API-Contracts]] § *P20 render INTENT parsing*: the module is stdlib-only so
the gate is testable, the three guards (PROXIMITY / NEGATION / LENGTH cap), and why all three are
needed together.

## KEEP-CLEAN — a load-bearing constraint survives (19)

Each is a real constraint at the exact site where it binds. Strip dates, quotes and incident
retelling; keep one or two terse lines.

| id | file | the constraint that must survive |
|---|---|---|
| 0010 | app.py | Per-frame detection must NOT be mounted on this app — it belongs in `tablet_foundry.detect_head_serve()`. A ~10/s frame class routed through here blocks the event loop and starves every other caller. |
| 0025 | app.py | Hand-maintained marker; `06-SELF-ENGINEERING-AGENT-PLAN.md` owns the number — bump both together. |
| 0227 | energy.py | The HA curve's key format (`"1".."24"`, hour N covers (N-1):00–N:00 local) and the gate FAILS CLOSED — an unconfirmable price defers. |
| 0231 | flame.py | Closed-form fit (no torch/GPU) and the asset paths the loader needs. |
| 0441 | tests/test_collision_gate.py | No unit test may wait on a network timeout — see `tests/_no_live_probes.py`. |
| 0473 | tests/test_dataset_subjects.py | Every test redirects `DATASET_FLAGS_FILE` to a temp dir; the real file holds hundreds of hand-made judgements and no test may touch it. |
| 0485 | tests/test_direct_route_bounds.py | The trigger phrase is preserved BYTE-FOR-BYTE — do not retype or tidy this fixture. |
| 0492 | tests/test_fellowship_seam.py | These tests read ORCHESTRATOR sources to assert properties of Kirk; the fulfilment half stays in the tolkien suite. |
| 0520 | tests/test_gpu_lost_report.py | `size_vram` is written at LOAD time and never revised, so a card lost AFTER load reports full residency forever; the <5%-VRAM check only catches the opposite order. |
| 0569 | tests/test_notes_isolation.py | `notes` honours `HAI_NOTES_DIR` so a test run cannot write the real vault. |
| 0601 | tests/test_p20_render.py | Assert the CAP and the refusal, never the wording. |
| 0610 | tests/test_propose_work.py | `config.SELFENG_PROPOSALS` is a REAL read-write mount holding the live review queue; every test inherits `_ProposalCase`, which redirects all three self-eng paths and ASSERTS the redirect took effect before anything writes. |
| 0664 | tests/test_self_engineering_phase2.py | This pin enforces the phase number — bump it together with the plan doc that owns it. |
| 0672 | tests/test_stash.py | A metadata wipe must NOT take the titles with it; titles are scrubbed only when explicitly requested. |
| 0676 | tests/test_stated_run_guard.py | `training_busy` is the SUBJECT of this file and is deliberately NOT pinned in `_no_live_probes`; only the incidental probes are. |
| 0688 | tests/test_think_harder.py | Assert `deliberation.TH_REFINE_RULES`, not the app source spelling. |
| 0724 | tools.py | The busy-refusal is removed ON PURPOSE and must not return: it was a second implementation of "is the card free" outside the components that own it, and two derivations of one idea drift. A busy lane is a queue position, not a refusal. |
| 0220 | deliberation.py | Probes must be woven BEFORE the output-contract paragraph. Appended after it they bury the contract and the critic returns invalid JSON. |
| 0171 | tolkien_cameras.py | Node choice uses `fellow_health()`; it reports an unreachable node as `alive: False` rather than omitting it — a node missing from a map reads as "not asked", a different claim. Do not add a second liveness notion. |

## CUT-OK — filed, nothing code-local survives (7)

`0017` · `0103` · `0598` (p20_intent family — filed this pass) · `0133` (superseded `_factory_view`
derivation) · `0474` (reload-resumes rationale) · `0512` (routing-vs-effort split) · `0543`
(identity-pipeline provenance) · `0573` (nudge-token origin).

## FLAG FOR owner — no clear owning manual (3)

DECISION 3 says file the durable constraint into the owning manual and flag only the genuinely
ambiguous. These have no owner and low reuse; inventing a doc for them would be worse than asking.

## What happens next

The cut is now mechanical: apply CUT-OK as drafted, and re-draft the 19 KEEP-CLEANs as terse rewrites
using the constraint column above. Removal remains HAI's per [[30f-DIVISION-OF-LABOR]] DECISION 2 —
`code_propose_cleanup` → review → `code_apply_cleanup` → the RUNBOOK/suite gate. Nothing in this doc
edits code.
