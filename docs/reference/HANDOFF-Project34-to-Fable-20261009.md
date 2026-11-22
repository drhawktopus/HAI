# HANDOFF — #34 to Fable, 2026-10-09 12:15 (Claude main session → Fable, for part of today)

Short on purpose. The plan and its state live in the library; this only says where to look, what is live right now,
and what bit today. **Do not reconstruct the plan from this file — read the two docs below.**

## Read first, in this order
1. [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] — standing decisions (never re-ask), the FINAL TEST LIST (frozen 10-08,
   repairs only) and its change log, Phase 7 / Phase 8 / agent-surface status.
2. [[34-CHARTER]] — § "End state" (the cascade, every layer the same shape) and § "How results shape action" (do-not-
   collapse list: per-box vs fleet · one-shot vs multi-attempt · final-answer vs sub-agent · dominated vs unacceptable).
3. As needed: [[34-Phase-7-Placement-Policy-Design]], [[34-S10-Rigby-Lead-Fellowship-Design]],
   [[HAI-Agent-Surface-Design]] § "The design", [[34-A1-Agentic-Build-Family-Spec]].

## The stage we are in (the thing today's session kept losing)
Measurement is FROZEN to the final test list. This stage is the champion's TOP LIMIT and the lead defaults at the top
(qwen3.8:27b at xhigh on its hard 7: 3/7, 94 % of tests; gate no help; reviewer no help), the Phase 2 lead-role test
for the challengers, and the Fellowship layer's role arms on rigby. These answer ROLE questions per layer of the
cascade — they are not comparable to the earlier menu's one-shot numbers, and they are not a Mothership-vs-rigby
scoreboard. When the list drains: crown, lead defaults, helper prices into `policy.json`, Phase 2 + measurement
CLOSED. After that it is BUILDING (Phase 7 callers, McCoy triage wiring, Tolkien placing Fellowship pieces, area C,
agent surface on the owner's timing).

## Live at 12:10 (read the DECK, `GET /p20/factory/<host>`; never poll boxes over ssh)
- **Mothership:** #5 Ornith (`a1v22-py-agentic-hai-ornith-best-thon-spot.20261008-0755`) 6/12, solved 2, early stop
  armed (margin 2 vs the 3.8's pooled baseline) — not fired yet. Then #6 Nemotron (29.5), #3 team-ri-dir (28),
  #4 team-bi-must (27), behaviour arms #7/#8, five menu cells #9. Read priorities back from the queue, not from slips.
- **Rigby (Tolkien):** R2-rest (`a1v22-rigby-moe-hai-off-spot-gt730-ctx65536-rest.20261009-0740`) 1/9 (control
  passed) — exercises take 1–6 h each. Then R1 repair and R-team repair (both at the measured 65,536 ctx).
- **BigHonker, Softserve:** idle.
- **All four factories in sync** at 11:4x (`fleet_deploy.py --check` bare covers foundry, A1 payload, rigby A1
  payload, Tolkien). Any code change ships only through `fleet_deploy` (`--a1`, `--rigby-a1`, `--workspace`) —
  hook rule 5/5b blocks hand pushes. Deploys = pause/stop slip → deploy → requeue; checkpoints resume on all four.

## Owed / open (all already in the register — listed so nothing is dropped)
- **S10b reduce** not run: `python s10b_select.py --lead s10b-rigby-moe-f2tests-s1.20261006-1804` (hard-bench). A
  CPU reduce on delivered results.
- **Deck ETA for rigby is wrong right now** — "~25 m left" is the median of the one control item (172 s); real
  exercises are 1–6 h. The median unit time should exclude control items.
- Crown write-up when #5/#6 finish (or early-stop); behaviour arms owed to a challenger only if it beats the 3.8.

## What bit today (so it does not bite twice)
- Mothership hard-froze 01:47 (Kernel-Power 41, no bugcheck); the owner power-cycled 07:41. A post-boot share-auth blip
  archived 12 orders → requeued; now a down share makes a compute order WAIT (`LaneBusy`), not fail.
- Rigby lost ~2.8 h to deploy stops before it had checkpoint/resume; parity is now a test
  (`test_fleet_deploy.A1ParityAcrossAllFourFactories`).
- A cut at a model's OWN tested context ceiling is scored, not voided (register change log 10:50).
- #4 re-run as MANDATORY delegation — directed arms made 0 delegate calls; a told-to-delegate arm that stops
  delegating is now stopped (`lead_alone`).

## Session-side notes
- The Claude main session had a 45-minute report timer; it is being stopped at this handoff so two sessions do not
  both report. Re-arm your own if the owner wants the cadence (gather script: `Claude Home\p34_report_gather.py`,
  read-only, deck-based).
- Today's journal: `Journal\2026-10-09-claude.md`.
