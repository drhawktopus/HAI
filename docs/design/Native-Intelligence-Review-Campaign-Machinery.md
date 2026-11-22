# Native-Intelligence Review-Campaign Machinery — HAI-runnable design

**Status 2026-08-27: BUILT + DEPLOYED.** The Recommended design below was implemented as
`campaign_step.py` + the receipt hook in `p20_job_result` + the chain-arming
`run_review_campaign` kickoff (built BY the auto-decompose driver as its commissions 2–3,
margin-reviewed and ported; orchestrator gate 2320 green 2026-08-28). The chain advances on
order-completion receipts — no session, no HAI loop — exactly as specced: kickoff confirm →
extract → parallel shares → merge → residual escalation (2 top-tier attempts → auto-exclude)
→ findings. **First LIVE campaign still to run as of 2026-09-08** (verified: no
`FINDINGS-*.md` newer than the machinery's 08-27 deployment, and Codex's 09-08 handoff still
lists it as a *precedent* rather than a used campaign). Arm via `run_review_campaign`; watch
on the factory tiles — every chain order name carries the campaign id. The 08-26 by-hand
Part-1/Part-2 runs are the proof-of-mechanism; they precede the machinery and are not the
"first live campaign" in this sense.

*(Original status 2026-08-26: design / gap analysis — the payloads existed fleet-wide but the
iterate-to-completion driver was a Claude session, which the factory doctrine forbids as the
permanent home. This doc was the spec for the missing machinery; it is now its record.)*

## The process (what HAI must be able to run end-to-end)

1. **Extract** — `native_history_extract.py` mines candidates (mode `positive` | `corrective`) from
   the transcript corpus → a `.work.json` (items + inline controls + sha).
2. **Review, fleet-parallel** — submit `native_history_review.py` as a parallel `order.compute`
   items order; the desk deals size-aware across capable GPU factories; each box reviews its share
   with exact-quote grounding, fail-closed, and ships a share file (grounded rows; failures → the
   residual). `triage.rubric.v1` + the size-reserve registry entry already exist.
3. **Iterate to completion** (the missing driver):
   - `--merge` the shares → COMPLETE, or a residual `.work.json` (carries the parent's controls).
   - a **stopped** share (Plex yield, operator) ships via `--ship-checkpoint` — its grounded rows
     are recovered, its remainder folds into the residual.
   - the residual escalates to a **bigger tier** (capability pin `triage.ctx.65536` = the 27B); a
     row the top tier cannot ground twice is **terminally ungroundable** → `--exclude <id>
     --exclude-reason` (recorded in-file). Loop until COMPLETE-or-all-excluded.
4. **Assemble findings** — aggregate all share files + the residual review, dedup by id (prefer
   grounded), compute pattern/recovery/source/model stats → `FINDINGS-*.md`.

Proven end-to-end BY HAND 2026-08-26 (Part 1: 1388/1389; Part 2: this run). Every step above is a
real order in the DM-mirror log — the machinery just has no non-session driver.

## What exists vs. what's missing

| piece | state |
|---|---|
| extract payload | ✅ deployed (`native_history_extract.py` v3) |
| review payload (+merge/ship-checkpoint/exclude) | ◐ `native_history_review.py` v2.1 `8d5017ef`: Softserve 2026-10-05; BigHonker + Mothership at their next order boundary. Until 2026-10-05 no box had `--ship-checkpoint` (the 08-26 mirror edit never shipped; RUNBOOK-DEFECT-LOG) |
| size-aware deal for it | ✅ registered in `compose_orders` (2026-08-26) |
| order submission / chains / receipts | ✅ `POST /p20/order`, `after_ids`, `report_fleet_result` |
| **findings assembly as an order** | ✅ **BUILT 2026-08-26** — `native_history_findings.py` (v1), a `load:"cpu"` compute payload: reads `<base>.share-*.json` + `<base>.residual.review.json`, dedups by id (prefer grounded), writes a stats JSON + templated FINDINGS md with a human INTERPRETATION section (no auto-quality-score). Parameterised (corpus dir, review base, mode). 2 pins (suite 31 green); fleet-deployed BH+SS, Mothership pending its lane clearing. So the three-payload SPINE — extract → review → findings — is now all durable machinery. |
| **HAI kickoff tool** | ✅ **BUILT 2026-08-27** — `run_review_campaign(work_dir, mode[, work_file])`: admin + sandbox-blocked, pending→confirm; ARMS THE CHAIN (extract stamped with the campaign block, or the review+merge steps order directly when a canonical `<mode>.work.json` is supplied — non-canonical names refused, the chain derives all paths from work_dir+mode) |
| **iterate-to-completion driver (on-box)** | ✅ **BUILT 2026-08-27** — `campaign_step.on_order_result`, hooked into the desk's own completion receipt (`p20_job_result`, fail-soft): merge COMPLETE → findings; residual → 27B (`triage.ctx.65536`) twice → auto-exclude (formulaic reason, recorded in-file) → findings; any failure or unreadable state stops the chain fail-closed. Review outs are read from their `results` rows — the `items` key is the WORK-file schema (a margin-review catch; conflating them over-excludes grounded rows) |

## Recommended design — a SELF-DRIVING campaign, reusing existing mechanisms

Do NOT build a session loop or a long-blocking tool. Reuse the factory's own chain machinery so the
campaign advances itself on order-completion receipts (`report_fleet_result` already POSTs
done/failed when a compute order ends):

- **`campaign.step` on the review order**: when a review/merge order ends with a residual, its
  completion handler composes + submits the NEXT tier's order (escalation), tagged with the same
  campaign id. Terminal-ungroundable detection (2 top-tier attempts, quote-refusal) auto-adds the
  `--exclude`. This turns steps 2–3 into a self-cascading chain — no session, no HAI loop.
- **Findings as the terminal step** — a `native_history_findings` compute payload (promote the
  scratchpad assembler) runs when the chain reaches COMPLETE, writing `FINDINGS-*.md`.
- **Gated HAI kickoff tool `run_review_campaign(corpus, mode)`** (admin, pending→confirm like the
  other write tools): extracts (or takes an existing work file) and submits the head of the chain.
  HAI then just watches via `fleet_status`/`fleet_watch` and reports — the subscribe-not-supervise
  posture. Quality caveat stays human: the findings note that scores need the owner's eye (his memory:
  Claude scores run optimistic), no auto-quality-number.

## Build path — the owner's call (two honest options)

- **A — Claude builds it now**, properly: the chain-step logic + `native_history_findings` payload +
  the gated tool + tests + RUNBOOK-gated deploy (orchestrator + foundry). This is the #30-tooling
  class of work (a real multi-step deploy on the critical orchestrator), NOT a quick install.
- **B — route it to HAI's self-engineering** (per `feedback-hai-is-the-worker` "if HAI lacks a
  tool, have HAI build it"): file this spec as an approved self-eng item; HAI builds the tool +
  driver on-box through the gated coding pipeline. Durable + on-box by construction; slower but
  it's HAI doing its own work, which is the standing model.

Either way the **deploy discipline is non-negotiable**: tested, gated, RUNBOOK procedure — no
half-tested tool hacked onto the live orchestrator.
