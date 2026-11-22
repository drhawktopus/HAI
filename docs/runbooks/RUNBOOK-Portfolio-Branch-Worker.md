# RUNBOOK — Portfolio branch worker (portfolio.hawktopus.org page loop) — the worker session's first read, every page and after every compaction (rev 2, 2026-09-12 evening)

You are the worker session on portfolio.hawktopus.org (the owner's job-application portfolio, 28 pages when done). the owner supervises. This file is NOT a second checklist. The ONE checklist is **THE PASS** in `portfolio-content-audit.md` (§"⭐ THE PASS", ~line 312): STEP 1 FRAME (project identity from `PROJECT-REGISTRY.md` only, sniff-test, flag to the owner), STEP 2 the pre-flight items 1–19, STEP 3 the read (27B first pass, one Sonnet cold read, apply real findings, A− minimum). Run THE PASS on every page. This file only tells you where things stand and what not to do.

## After a compaction, do this before anything else
1. Re-read THE PASS (audit doc §THE PASS) and this file.
2. Get the page state from the board, not from the compaction summary: `python <main-scratchpad>\portfolio_status.py`. The 15:20 summary carried two wrong facts (page 14 "B−", page 17 "needs a card") and cost time. Files are the truth.
3. Continue the page you were on. Do not re-open closed pages. Do not re-raise a settled rule (THE PASS item 19).

## The loop that works (page 15 took 12 minutes this way)
the owner says "page N is next" → FRAME against the registry → THE PASS items 1–19 with the Edit tool → verify every number against the library → `page_lint.py` 0 hits → `build_preview.py` → send the PREVIEW with a one-line note → 27B first pass in the background (`build_hai_query.py`, then POST to `/query`; read `route/host/model` back; discount link/table/date findings, it sees stripped text and has a pre-2026 cutoff) → Sonnet cold read (compact brief, résumé v9, the approved pages as context) → apply only real, library-verified findings → A− closes the page; one confirming read only if a factual change landed after the graded read. the owner's skim can happen in parallel with the reads; fold his notes in before closing.

What made the other pages slow was NOT the loop: it was a project-identity error (14), a number that covered the wrong scope (688 vs the whole Gauntlet), leapfrogging (16, 17 before 14, 15), scope growth (a new page, a cross-page sweep), and re-raising settled rules. Every one of those is now a STEP 1 or item-19 check.

## State (from the board and the audit doc, 18:10 CT)
| page | state |
|---|---|
| 01 02 03 08 09 10 | approved earlier; touch only for a consumer sweep the owner names |
| 04 Gauntlet | PARKED v12 for the group pass; H1 now "over 1,000 model-by-device runs" (the owner's correction); body keeps 688 as the matrix |
| 06 QLoRA, 07 orchestrator, 11 pilots, 13 self-engineering, 14 project 30, 15 gpu boost | CLOSED A− |
| 12 friend onboarding | PARKED B+ (the owner: "park with notes"), for the group pass |
| 16 cameras | content-done at A−; deferred item = pipeline diagram (maps batch) |
| 17 ops agent | re-scoped to the confirm-on-write story (the owner: option 2); parked for the group-pass grade |
| 18 conversations suite | PARKED B+ on the owner's call (18:29) for the group pass |
| 19 energy | IN PROGRESS (19:08): reframed (price fetch runs on demand, the automation is the open piece; energy chip is admin-only), both readers B+, "What is open" section cut. Disposition per THE PASS item 22, then 20. |
| 20 media stack | pre-flight done 14:37, PREVIEW STALE; needs the *arr-assembled-not-authored framing; THE PASS items 20–22 apply |
| 21 project 19 | tails removed; links point at 22 |
| maps ×3, landing | not drafted. The set is the 27 planned pages + the skills sheet = 28, plus 14 (Project 30 library re-do, kept). |

## Do-nots (each one cost time today)
- No "What is open" / roadmap section on any page (THE PASS item 21). No asking "close or park?" (item 22: A− closes, structural-only B+ parks and you move on).
- Write the page's three-sentence frame BEFORE opening any source doc (item 20); match the register of 07 and 11.
- **Fresh pages (20, 21, 22 per the owner, 2026-09-12 evening): rename the old draft `<page>.html.bak-draft`, start blank, and WRITE THE PAGE UNDER THE PASS.** Every item of THE PASS is a constraint on the first draft (frame, registry identity, the owner's voice, no roadmap, right altitude, verified numbers only, category shape, anti-sandbag, no authorship tail). The old draft is a quarry for already-verified facts, never the skeleton. Lint, preview, and the reads confirm the page; they are not the pass where it gets fixed.
- No new pages, no new slugs, no cross-page sweeps unless the owner names them. Flag, then wait.
- No re-opening closed pages except a consumer sweep the owner named (as with the 14/22 split links).
- No scripts for page edits (the owner asked why, twice). Edit tool. Backups by `cp` first.
- No archeology (CSV parsing, transcript mining) when the library has the number; the registry and the conclusion docs are the sources.
- No narrating the process to the owner. One line per page: what changed, what was verified, grade.
- No "should I keep X" on a settled rule. Item 19.
- Journal: `Journal\2026-09-12-claude-portfolio-branch.md` (not the main session's file). Status tag `[PORTFOLIO-BRANCH]` in CURRENT-PROJECTS.

## Paths
- Drafts `E:\SSDShared\SSD\Open Access\Claude Home\portfolio-hawktopus\drafts\`; reports `_coldread_<page>_v<N>_<date>.md` beside them. EVERY read gets its own file with the page version in the name, never overwritten: today the board shows 13 and 14 at "B+" because only the first read was saved and the A− reads were not. The grade of record must be on disk.
- Main-session scratchpad (scripts + `resume_v9.txt` + `coldread_brief.txt`): `C:\Users\<user>\AppData\Local\Temp\claude\E--SSDShared-SSD-Open-Access-Claude-Home\c80b7d59-69eb-4f6e-bf22-2fa749de141d\scratchpad\`. Your own scratchpad copies must stay in sync with these (the lint rules changed twice today).
- Fixed slugs: current-lab amd-gpu-wedge fellowship gauntlet qlora-32b hai-orchestrator doctrine native-intelligence skills friend-onboarding cameras media-stack gpu-boost-investigation project-12 project-19 project-30 conversations-suite pilots ops-agent energy architecture-lab architecture-ai architecture-network index. (22's slug pending the owner.)
