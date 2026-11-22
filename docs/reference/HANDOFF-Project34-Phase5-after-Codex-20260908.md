# HANDOFF — Project 34 Phase 5 after Codex execution/recovery

**Date:** 2026-09-08 CT  
**For:** Claude/main session  
**Scope:** finish the last execution exceptions, then prepare the separately gated HAI-led appraisal.

> ✅ **RESOLVED 2026-09-26 (main session) — both "remaining execution items" below are done; execution is
> COMPLETE at 1,165 / 7 excluded / 0 held / 0 blocked of 1,172.** The 23 HELD `u_button` arms were re-admitted from
> their `.HELD` copies in rigby's queue and all have results (one file per window, controls passed, full coverage).
> The blocked class-B critic arm was a structural placement gap (lane items seed in list order → same host as the
> solver, 24/24); fixed by adding `exclude_hosts` to Tolkien map orders (deployed, tested) and an `unblock`
> subcommand to the driver; attempt 25 placed the critic on faramir and validated; driver status `complete`
> (304/304). **Still binding:** no appraisal has been done — "Appraisal gate" below is the next gate and it is
> the owner's. Record: `Journal\2026-09-26-claude.md`.

## Read first

1. `Journal\2026-09-06-codex.md` — expanded submission, adversarial review, abandoned Tolkien cleanup,
   and 304-arm driver admission.
2. `Journal\2026-09-07-codex.md` — hold diagnosis, reconciliation, N-ladder re-admission, and
   Sméagol ARMv7 recovery.
3. `Journal\2026-09-08-codex.md` — fit exclusions, current state, appraisal reconnaissance, doc debt.
4. `E:\SSDShared\tabfoundry-jobs\campaigns\phase5-remaining.20260906-2040\state.json` — live
   structured/heterogeneous authority.
5. The same directory's `hard-fit-exclusions-class-b-qwen2vl.json` — terminal ruling and hashes.
6. `phase5-c02\manifests\intent-phase5-expanded-ready.20260906-142042.json` and
   `...144218.json` — immutable N-ladder authority.
7. `LLM-Project\34-Phase-5-Ensemble-Design.md` for design; its execution-status header is stale.

## Current state

Mechanical snapshot; no answer appraisal:

| population | complete | held/pending | excluded | blocked |
|---|---:|---:|---:|---:|
| coding/chat/tool N-ladder (672) | 672 | 0 | 0 | 0 |
| VLM N-ladder (196) | 173 | 23 HELD | 0 | 0 |
| structured (201) | 201 | 0 | 0 | 0 |
| heterogeneous (103) | 95 | 0 | 7 | 1 |
| **total (1,172)** | **1,141** | **23** | **7** | **1** |

No Phase 5 campaign driver is running. The checkpoint has zero submitted orders and no live lock.
Tolkien has no current order. All ten aarch64 workers were healthy at the snapshot.

## Two remaining execution items

### VLM N-ladder `u_button`

Twelve immutable windows containing 23 arms remain `.HELD`. They were held at 22:26–22:30 CT
because only nine eligible aarch64 workers were healthy. Current aggregate health is ten. Re-admit
the exact HELD specs through Tolkien's inbox; do not regenerate them. Preserve HELDs and first verify
there is no matching result. *(Method superseded 2026-09-27: a `.HELD` order is now re-admitted in
place with a `<order>.ctl.json` = `{"action":"release"}` slip in the inbox — no copy, no resubmit;
[[Fellowship-Order-Taker-Design]] §11.)*

### Heterogeneous class-B text critic

`heterogeneous.text_critic_on_class_b.chat.sisters` is blocked. Solver is complete. Critic reached
24 attempts; each was rejected because the critic host Boromir overlapped the solver's Boromir role.
This is provenance/placement failure, not answer-quality evidence. Do not submit an identical 25th
attempt without resolving how the existing interface can yield a distinct class-B role assignment
or obtaining the owner's disposition.

## Completed exceptions — do not reopen

- `heterogeneous.fast_role_on_smeagol.chat.sisters` completed on attempt 25 after ARMv7 recovery;
  its original 24 staging failures remain in state.
- Seven `heterogeneous.heavy_role_on_class_b.vlm.*` cells are terminal hard-fit exclusions. Do not
  retry, substitute a model, move to class A, relax the guard, create replacements, or score them.
  Their controls were non-executable, so they carry fit/feasibility evidence only.
- Abandoned `team.window` source is archived at
  `C:\Users\<user>\Documents\Codex\2026-09-05\hel\outputs\abandoned-phase5-team-window-2026-09-06`.
  It was never deployed. Active Tolkien mirrors match deployed source.

## Sméagol durable facts

- wired-only is expected: `10.55.0.18`, SSH port 8022;
- wired stage server: `http://10.55.0.1:8078`;
- ARMv7 CLI build: `/home/owner/llama.cpp/build-android-armv7/bin/llama-cli`, source `70adb1b`,
  NDK r27c, `armeabi-v7a`, API 24, `GGML_LLAMAFILE=OFF`;
- model SHA-256: `1adf0b11065d8ad2e8123ea110d1ec956dab4ab038eab665614adba04b6c3370`;
- real model generation was proven; do not repeat for reassurance.

## Appraisal gate — reconnaissance only

No appraisal has started. Reusable production pieces:

- Kirk `eval_campaign` for durable pins with Spock placement and served identity/context evidence;
- HAI force-27B / Think Hard / Absurd for Mothership `qwen3.6:27b` and critic-pool work;
- NI `run_review_campaign`/`campaign_step.py` as a self-cascading review precedent, though its fixed
  historical-interaction schema is not a Phase 5 grader;
- `queue_compute` for whitelisted appraisal-support payloads;
- Phase 4/5 deterministic scorers, controls, coverage, provenance, manifests, and basic reducer.

Phase 5 appraisal still needs a canonical inventory/normalizer, task-valid reducers, ambiguous-review
packets/rubrics, an adapter into existing HAI/Kirk routes, and final evidence/scorecard assembly. Do
not create a second orchestrator or make tablet experimental roles canonical judges.

**Exact next gate:** finish or disposition the 23 HELD N-ladder arms and one blocked heterogeneous
arm. Then the owner reviews/authorizes the appraisal implementation shape. Do not interpret results before
that gate.

## Documentation debt

See `Journal\2026-09-08-codex.md` §Documentation debt. The worst traps are CURRENT-PROJECTS'
September 4 state, the old execution brief's “no order placed/interface missing” claim, and the
Phase 5 design's “window envelope still required” line.
