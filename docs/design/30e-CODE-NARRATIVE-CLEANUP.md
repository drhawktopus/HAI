# PROJECT #30 CODE HALF — Narrative-Commentary Cleanup Across All Codebases (30e)

**Status:** OPEN — confirmed 2026-08-24 (the owner). IN PROGRESS: detector built, `cameras.py` pilot
underway — see EXECUTION STATUS below. This is the CODE-side twin of the docs restructure
([[30d-OBSIDIAN-WIKI-RESTRUCTURE]]). Same criteria, applied to every codebase we own.

**Goal in one line:** every codebase's comments and docstrings state only what the adjacent code
cannot show for itself; the earned knowledge they used to carry lives in the owning manual; the tests
still pass.

**This doc is executable by ANY worker** — a Claude session, a future session after a compact, or HAI
itself. It is written so work can continue without the session that started it. Read it top to bottom;
each phase names who can do it and what "done" looks like.

## EXECUTION STATUS (kept current — a picker-upper starts HERE)

As of 2026-08-25:

- ✅ **WHOLE-MIRROR FACTORY PASS — THREE OF FOUR MIRRORS GREEN.** Lab order `54e4d06779ba`
  applied 27/27 dispositions to 11 files (`KEEP 20 / CUT 7 / MOVE 0`), commit `c481924`;
  Tolkien order `0d70d80216aa` applied 192/192 to 14 files (`KEEP 155 / CUT 35 / MOVE 2`),
  commit `47b8ccd`, authoritative gate 252 tests / 1 skip / OK; orchestrator order
  `601a4285b8af` applied 784/784 (`KEEP 527 / CUT 230 / MOVE 27`), commit `8301b23`, 128
  already-live files deployed (pre-existing mirror-only `register_lora.py` excluded), every coupled
  consumer rebuilt/recreated, authoritative gate 2,197 / 2 pinned skips / OK, sync baseline
  `3657e86`, whole-tree mirror audit 0 drift. All MOVEs remained byte-unchanged; every changed file
  passed executable-AST equality against HAI's pre-clean archive and exact-LF checks.
- ⚠ **FOUNDRY STOPPED BEFORE APPLY — DRAFT QUALITY FAILED THE NORTH STAR MARGIN GATE.** The first
  whole-mirror order `59a27aba3ccd` was invalid because it included 440 `_pre-sync-*` restore
  snapshot candidates. The corrected ordinary P20 order `6712fcb56811` completed 710/710 active
  candidates (55 scanned files / 41 candidate-bearing files), 0 errors, all served by
  `qwen3.6:27b`, with `KEEP 534 / CUT 167 / MOVE 9`. Exact-ID, live-span, parse, and executable-AST
  dry-run checks passed, but review found definite two-bar failures: malformed single-line KEEP
  rewrites with embedded `#` markers (`0031`, `0138`, `0143`); forbidden dates, versions, warning
  theatrics, and incident language surviving in KEEPs; and over-CUT load-bearing GPU-collision,
  AMD-compatibility, timeout/retry, path, compatibility-alias, and stall-threshold constraints
  (examples `0009`, `0101`, `0168`, `0228`, `0300`, `0473`, `0506`, `0511`, `0530`, `0533`). Per
  the fail-closed instruction, no Foundry source was written, committed, or deployed. The mirror is
  clean at `063a23a`; the deploy claim was released. A corrected HAI review/draft pass is required.
- ⚠ **THE 29 MOVEs ARE REVIEWED — AND THE DRAFTER OVER-MOVED 19 OF THEM.** The three green applies
  left every MOVE byte-unchanged (files-twice: the fact must reach the owning manual before the cut,
  and that gate is the reviewer's). Reviewed 2026-08-26 in
  [[30e-MOVE-DISPOSITIONS-REVIEWED]]: **19 KEEP-CLEAN** (a load-bearing constraint survives the strip
  — the deliberate busy-refusal removal, the detector-shim exclusion, live-mount fixture isolation,
  `size_vram` staleness), **7 CUT-OK**, **3 flagged for the owner** (no owning manual, DECISION 3).
  Filed this pass: the `p20_intent` family → [[HAI-Core-Architecture-API-Contracts]] § *P20 render
  INTENT parsing*. ⚠ The files-twice scan must grep the **live manuals only** — including `Journal\`
  and `Archive\` returned a false 29/29 FILED; scoped correctly it is 23/6. **`triage_code` v2
  (2026-08-26)** narrows MOVE at the measured defect: length/narrative is not what decides it — strip
  the story FIRST, and relocate only if NOTHING is left that binds this code. The 29 re-draft under
  v2 is staged (`library-triage\s30-code-{orchestrator,tolkien}-moverework.work.json`) and runs when
  Mothership's lane clears.
- ✅ **SAFETY ENGINEERING SHIPPED BEFORE THE APPLIES:** snapshot-dir exclusion `f3b6faa`; stale-draft
  retirement `f50765c`; UTF-8 AST byte-column handling in shared Lab/bin `7e725d4`; and
  commentary-only KEEP normalization, fresh candidate re-resolution, executable-AST equality, and
  BOM-safe parsing `eab3b6b`. HAI's full gate remained 2,197 / 2 pinned skips / OK. No work used
  HAI's separate Overnight subsystem; all reviews were ordinary serialized P20 factory orders.

- ✅ **Detector BUILT:** `E:\SSDShared\Lab\bin\code_narrative_scan.py` — deterministic (`tokenize` +
  `ast`, so a `#` or a date inside a string literal is never a false hit). Classifies
  dated / warn_block / owner / citation / quote; `--self-test` carries positive + negative controls;
  UTF-8 stdout so the `⚠`-in-comment output never dies on a cp1252 console. Run:
  `python code_narrative_scan.py <file-or-dir>` (per-file worklist) or add `--json` (per-candidate).
  Any worker — session, HAI coding lane, Codex — runs it as-is.
- ✅ **Baseline worklist (orchestrator):** app.py 163 · tools.py 86 · config.py 57 · scheduler.py 50 ·
  identity.py 32 · … (run the detector for the live list; the count drops as files are cleaned).
- ✅ **PILOT `cameras.py` — workflow PROVEN (2026-08-24).** 9 candidates → HAI's 27B drafted all 9
  dispositions and it **grounded itself** (retrieved the subject-definitions doc to confirm "six cats,
  no dog"; verified the Frigate-retirement is already filed in three manuals). Review (the review-margin
  role) accepted 3 rewrites as-is, kept a terse constraint on 3 HAI had over-CUT (decision 4), dropped a
  stale "Frigate" HAI left in one rewrite, and restored one constraint HAI lost to a truncated input.
  Applied archive-first (`orchestrator-build\backups\cameras.py.preclean-30e-20260824`); re-scan = **0
  narrative**; `test_cameras.py` **24/24** green locally; no source-inspection test trips on the comment
  edits. **DEPLOYED 2026-08-24** in the first batch (with test_cameras.py + comfy_client.py).
- 🧠 **PILOT LEARNINGS (feed the tooling):** (a) HAI's 27B drafts well and self-grounds, but leans
  **aggressive-CUT** (comfy_client.py: 6 of 14 dispositions cut/moved real code-local constraints). The
  *tell is its reasoning*: it treats "the fact is filed in a manual" as grounds to cut — the wrong proxy,
  since files-twice deliberately keeps load-bearing constraints in BOTH places. It is not blind or
  context-saturated; it sees fine and scores the wrong criterion, so the fix is the **delegation prompt**
  (default-keep + files-twice-is-not-grounds-to-cut + the two-bar test), not tool recalibration. Note the
  cut *direction* is closer to the density goalpost than an over-keep, so the reviewer applies the
  **two-bar test** (strip story; keep a surviving constraint only if a skilled dev would write it here) —
  not a blanket "restore every terse constraint," which is its own over-keep. (b) Detector
  fixed twice mid-pilot: docstrings were truncated to 400 chars (→ full text + real end-line), and the
  `quote` signal was noisy (matched prose spanning two unrelated quoted words → tightened to the
  emphasised-quote form; real quotes co-occur with a date/the owner anyway). (c) A module's TEST file carries
  narrative too (`test_cameras.py` docstrings) — clean it in the same batch.
- ✅ **`test_cameras.py` + `comfy_client.py` CLEAN (2026-08-24).** test_cameras.py: 7 candidates → 0,
  24/24 green. comfy_client.py: 14 (HAI-drafted) → 0, reviewed under the two-bar test, git-committed
  (`335a0f1`). **All three DEPLOYED 2026-08-24** through the RUNBOOK container gate — lane claimed,
  live backed up, bytes byte-identical host+container, `orchestrator` image Recreated, health 200, gate
  **green (2187 collected, pin ok, 2 pinned skips)**, workspace synced, claim released. Detector
  re-verified `[]`; instrument validated against `--self-test` + app.py positive control.
- ✅ **ENGINEERING BUILD DONE + DEPLOYED 2026-08-24 (DECISION 2's Claude carve-out).** The capability is
  live: (1) **`triage_code`** foundry payload (`mothership-build`, on-box 27B two-bar drafter, sibling of
  `triage_rewrite`) — deployed to all three factories via `fleet_deploy --no-restart`; (2) **`code_cleanup.py`**
  orchestrator engine (detect→work→order→byte-splice glue; reuses `code_narrative_scan` + `code_apply_cleanup`
  imported from the mount, one copy each); (3) two **gated tools** `code_propose_cleanup` / `code_apply_cleanup`
  (pending→confirm, `destructive=True`, admin-only) + `tests/test_code_cleanup.py` (10 pins). Shipped through
  the RUNBOOK container gate — image Recreated, health 200, suite green (**2197**, pin ok, 2 skips), live brain
  wired. Write path = a **narrow rw carve-out** for the code mirrors (`/code-mirror/{orchestrator-build,
  mothership-build,tolkien-build,Lab/bin}:rw`, same posture as `/library-triage`; broad `/downloads` stays ro;
  detector imported from the ro `/downloads/Lab/bin` via `CODE_LAB_BIN`). The session-side draft/apply scripts
  were the wrong shape and are retired; the pilot files (cameras/comfy) were hand-applied — that stops here.
- ⏭ **NEXT — HAI runs the REMOVAL orders (DECISION 2: removal is HAI's, not Claude's).** Now just another
  order, per file: **`code_propose_cleanup(mirror, file)`** → the 27B drafts a two-bar disposition per
  candidate on-box (drafts land on the `library-triage` share) → review the drafts →
  **`code_apply_cleanup(mirror, file)`** → pending→confirm → RUNBOOK/suite gate ships it. `mirror` is one of
  `orchestrator | foundry | tolkien | lab`; `file` is the path within it (e.g. `app.py`, `bin/x.py`).
  Orchestrator worklist: `app.py` ~148, `tools.py` ~86, `config.py`, `scheduler.py`, `p20_queue.py` … the owner
  authorizes each apply; Claude reviews at the margin. (`scheduler.py`/`config.py` are coupled — a cleanup
  there rebuilds all three images; the rest are orchestrator-only.)

**To resume as HAI or Codex:** read this doc; run the detector for the current worklist; take the
top-priority file not yet clean; delegate its rewrite to HAI's 27B (or draft directly) per the
CRITERIA; apply archive-first; run the deploy-gate suite (`ssh sundae "sudo /opt/arrstack/run-suite.sh"`);
update this section.

## Why this is #30, not a new project

HAI's bad answers traced to a messy RAG library plus dangling commentary; the docs half fixed the
library. The *same* dangling commentary — dated war-story blocks, incident retellings, quotes — lives
throughout the code. the owner, 2026-08-24: *"all of our code is subject to this cleanup as well… the same
criteria that we've been using for the rest of the stuff."* This was raised earlier in the arc and lost
across a compact; it is now anchored in the library (here + CURRENT-PROJECTS [MAIN] + PROJECT-BACKLOG)
so it cannot be lost again.

## The criteria (identical to the docs)

Sources: [[LIBRARY-CHARTER]] rule 1 + the `feedback-no-narrative-in-code-comments` memory.

**The goalpost (the owner, 2026-08-24):** the result must read like a **normal-practices, skilled human
developer** wrote it — not "comment city." Humans comment sparsely: they annotate the non-obvious, the
surprising, and the dangerous, and let good names and clear code carry the rest. War stories never.

A comment survives only if it passes BOTH bars — this is an **AND**, not a choice:

1. **True-constraint bar** — it states a **constraint the adjacent code cannot show for itself**
   (`._pth interpreters omit the script dir — insert it before sibling imports`; "strength stays 0.5 —
   higher denoise melts the subject"). Stripping the story to leave only the real constraint still has
   to clear bar 2.
2. **Density bar** — a normal skilled dev would actually bother writing it *here*. A true-but-obvious or
   marginal rationale (a magic number a good constant name already explains) fails this bar and is cut
   even though it is accurate.

Fail EITHER bar → cut. But the density bar does **not** license cutting a real constraint: when a
comment is both true AND load-bearing — a safety/concurrency invariant, a compatibility gotcha, a
don't-bump-this tuning limit — keep it, terse.

- OUT of comments: dates, "the owner said", incident retellings, doctrine-row citations, before/after
  stories, pasted quotes, ⚠-block theatrics.
- **Test docstrings too:** name the property the test pins, not the outage that motivated it.

## The safety gates — what makes this "properly", not a delete-fest

0. **BACK UP EVERYTHING FIRST — the outer net** (the owner, 2026-08-24: *"archive it all before we even
   start to delete stuff"*). Before touching any codebase: (a) a full source copy on the **Z: array**
   (`Z:\Archives\code-mirrors-<tag>-<date>` — NOT the SSD; storage design rule in
   `Network-Storage-Infrastructure-Drives-SMB-Shares.md`), and (b) each mirror is a **local git repo**
   (commit the baseline before a batch, commit again after each file). Git gives per-commit diff +
   restore; the array copy is the disk-redundant local copy ("we have the space — local copies of
   everything"). **Local git, not GitHub** (these mirrors carry infra detail). **DONE 2026-08-24:** all
   four mirrors (orchestrator/foundry/tolkien/Lab-bin) backed up to
   `Z:\Archives\code-mirrors-pre-30e-20260824` and git-init'd with baseline commits.
1. **FILES TWICE** (charter rule 9): the earned knowledge in a narrative comment must exist in the
   **owning manual** as a declarative fact before the comment is cut. Journal = the story; manual =
   the reusable constraint/procedure; comment = only what the code cannot show. A lesson that exists
   only in a comment is not documented — so file it, then cut.
2. **MOVE-NOT-DELETE:** code has no automatic archive snapshot. Archive the pre-clean file (timestamped)
   BEFORE editing; cut a narrative comment ONLY after its content is verified present in the manual or
   journal. Stripping narrative is a MOVE, never a blind delete.

## The pipeline (HAI-drivable; mirrors the docs triage pipeline)

Per file (or module):

1. **DETECT** — find every narrative comment/docstring (deterministic scan, asserts its own count).
2. **CLASSIFY** — for each candidate, one of: **KEEP-CLEAN** (rewrite to the bare constraint) ·
   **MOVE** (the knowledge belongs in a manual → file it, then cut the comment) · **CUT** (pure story
   whose knowledge is already filed → delete).
3. **PRESERVE** — before any MOVE/CUT, verify the knowledge is in the owning manual (RAG check); if not,
   file it first. This is the FILES-TWICE gate.
4. **REWRITE** — produce the cleaned file.
5. **APPLY** — archive-first → write → run the deploy-gate suite (behaviour must be unchanged) → confirm
   green.

Same shape as the docs: classify → verify → rewrite → apply(archive + gate).

## Tooling — detect + apply BUILT (`Lab\bin`); DRAFTING is a FOUNDRY ORDER, not a session script

⚠ **Correction 2026-08-24 (the owner: "why isn't Kirk/Spock/HAI handling the chunking?").** The drafting
step was first built as a session-side delegate (`code_cleanup_delegate.py`) that looped the `/query`
CHAT endpoint and hand-rolled chunking to fight its response cap — the classic reinvent-the-wheel trap.
The factory already does this properly: the docs half ran as **triage orders** (`triage_classify` /
`triage_rewrite` in `mothership-build`) processed **on-box** by the foundry, using
`_chat(..., schema=SCHEMA)` — **direct ollama structured output, one item per call**. That has none of
the problems the session loop hit: no chat-cap truncation, no chunking to solve, plus residency assert,
a failure breaker, progress tiles, and fleet **shares** for parallelism. Drafting must route through the
foundry. `code_cleanup_delegate.py` + `build_cleanup_query.py` are RETIRED (session-shape).

| stage | tool | notes |
|---|---|---|
| detect | `code_narrative_scan.py <path> [--json]` (BUILT) | deterministic; per candidate emits line span, **exact char offsets + verbatim `raw`**, classes; `--self-test` +/- controls |
| draft | **`triage_code` FOUNDRY ORDER** — payload `mothership-build\triage_code.py`, modeled on `triage_rewrite.py` (**BUILT + DEPLOYED 2026-08-24**) | runs ON-BOX against the resident 27B via `_chat(..., schema=)`: structured output, **per-candidate** (no chunking, no truncation); reuses triage_classify's `_assert_resident`/`_chat`, and triage_rewrite's shares/`--merge`/breaker/progress. Two-bar prompt + `{disposition,new_text,note}` schema. Submitted via `code_cleanup.submit_code_order`; the gated tool applies the draft. |
| review | (the supervisor) | two-bar double-check of the draft — the load-bearing step; the 27B leans aggressive-CUT (PILOT LEARNINGS a) |
| apply | `code_apply_cleanup.py <src> <cands.json> <disp.json> [--apply --stamp <tag>]` (BUILT) | offset-splice bottom-up, archive-first, ast-checked + re-scanned; CUT never eats a trailing comment's code line; dry-run by default |

Run order per file (**DEPLOYED — the HAI-driven path**): **`code_propose_cleanup(mirror, file)`** (the
`code_cleanup` engine runs the detector, builds the work file, submits the foundry order) → **review the
dispositions** on the `library-triage` share → **`code_apply_cleanup(mirror, file)`** (dry-run preview →
pending→confirm applies the byte-exact splice, archive-first, ast-checked + re-scanned) → the RUNBOOK/suite
gate ships it. The standalone `Lab\bin` CLIs (`code_narrative_scan`, `code_apply_cleanup`) remain for manual
dry-runs. The `preserve`/files-twice gate is the reviewer's job (verify a MOVE's fact is filed before
allowing the cut); the apply leaves MOVEs in place until then.

## A STOPPED DRAFTING RUN RESUMES — the drafter keeps every draft on disk as it is made (since 2026-11-18)

`triage_code.py` (code-v4) writes each draft as one JSON line to `<drafts>.partial.jsonl` beside the output the
moment it is made (a KEEP-CLEAN/CUT/MOVE row, a too-large skip or an error alike). A run that is cancelled, killed or
stopped by its own breaker (3 consecutive failures; rc 3, so the desk never marks the order done) keeps everything it
made; rerunning the same order reads the sidecar back, counts those items as done on the tile, and sends only the
missing ones to the model. The sidecar is removed once the whole drafts file is written. Before this, the drafts lived
in memory until the end and a cancel lost the run. Pinned: `test_triage_code.EveryDraftLandsOnDiskAsItIsMade`.

## REVIEWING THE DRAFTS before apply — the instruments, what the drafts get wrong, the faithful edit (the 2026-11-15 pass)

The review step has tools, in `E:\SSDShared\hai-release\instruments\`. A sweep's name is what follows `s30-code-`:
a mirror (`orchestrator`, `foundry`, `tolkien`, `lab`, `menu`, `engine`, and since 2026-11-20 `cameras`, `evals`,
`fellowship`: `Lab\cameras`, `Lab\hai-evals`, `Lab\fellowship`) or one file (`orchestrator-config-py`).

**The cameras lane, the evals and the fellowship are clean (2026-11-20).** Python by the faithful edit
(`hai-release\review\doc-jobs\destory_files.py`, 86 candidates in 59 files; 27 by hand through `--hand`: the owner's
name as the role, incident narration rewritten, a removed date's stray punctuation); the 17 shell and PowerShell
scripts and `fellowship\ncnn-detect\hwdec.c` by hand (the detector does not read C). Code unchanged apart from comments
and docstrings in every file, the detector at 0 per folder, originals in `Z:\Archives\code-preclean\{cameras,evals,
fellowship}`. ⚠ **Run the detector on ONE folder per call**: given several paths it reads only the first, so a
three-folder run printed "0 candidates" while the third folder still held 9.

**Which mirrors exist is the head's `CODE_MIRRORS` setting** (JSON, mirror -> folder under `/code-mirror`, each one
also a rw mount in the head's compose override). Both tools' schemas name whatever the setting holds
(`tools._code_mirror_names`, pinned by `test_code_cleanup`), because the model treats the schema as its contract and
refuses a mirror it does not name. Adding a mirror = the setting + the mount (RUNBOOK-Orchestrator-Deploy-Procedure
§ "A new compose SETTING or MOUNT"), then a recreate. The sweep reads every `.py` under the mirror except vendored and
upstream trees (the detector's `skip_dir`: `llama.cpp-*`, `ollama-<version>`, `strata-<version>` since 2026-11-19, so
the engine mirror's sweep is its own 48 files). Shell and PowerShell files are not swept: their comments are scanned by
the same detector and rewritten by hand.

| step | tool | what it does |
|---|---|---|
| first pass | `review_cleanup_drafts.py <sweep> --out report.txt` · `show_flagged.py report.txt "<flag>"` · `show_cand.py <sweep> <id>…` | flags a rewrite that is empty, not shorter, carries a non-comment line, still has a date / name / quotation, or DROPS A NUMBER; a CUT whose text states a rule; every MOVE and error |
| corrections | `apply_review_overrides.py <sweep> [overrides.json] [--write]` | mechanical: a comment rewrite keeps only comment lines; a docstring gets balanced triple quotes and its original backslash form (raw stays raw, else doubled); dates out. Then the hand overrides (`cleanup_overrides_<sweep>.json`), taken exactly as written (no correction, so a verbatim keep stays byte-identical). Refuses to write while anything needs a hand. The old drafts stay as `.superseded-<ns>` |
| backslashes | `escape_warnings.py <sweep>` | rewrites that would add an invalid-escape warning |
| plan | `dry_apply_cleanup.py <sweep>` | plans every file as the apply does; names the files it refuses and why |
| a file's lines moved since the scan, its comments did not | `rebase_work_lines.py <sweep> [--write]` | re-scans and writes the live line numbers and offsets into the work file, only when every candidate's kind and text still match in order; anything else is refused (propose that file again). The review stands |
| a file changed since the scan | `drop_drifted_files.py <mirror> <file>…` | takes those files out of a whole-mirror sweep (the apply refuses the lot otherwise); each is proposed again by itself |
| apply | ask HAI for `code_apply_cleanup` (whole mirror, or `mirror` + `file`), then `confirm_pending.py <mirror>` or `<mirror>/<file>` | confirms the one pending action at `/pending/act`; refuses when none is pending, which is how a model that ANSWERED "applied" without calling the tool is caught |
| archive | `archive_preclean.py <mirror dir> <name>` | moves the apply's `.preclean-<stamp>` copies to `Z:\Archives\code-preclean\<name>\`, verified before removal |
| stop regrowth | `orchestrator-build\tests\test_no_new_stories.py` | the gate fails on a story not in `tests/story_baseline.json`; the baseline only shrinks (`python tests/test_no_new_stories.py --write`) |
| page scripts | `code_narrative_scan.py --embedded-js` / `scan_source(..., embedded_js=True)` | the `//` and `/* */` comments inside a string literal that holds a page (`<script`), kind `jscomment`. **A Python-only scan cannot see them** (found 2026-11-15: 37 story runs inside `app.py`'s `CHAT_HTML`, dated quotes and "the owner asked"). The story gate and the publish check turn it on; the cleanup pipeline does not (its drafting and apply handle Python comments only), so those 37 are cleaned by hand as a faithful edit, then the baseline shrinks. **Done 2026-11-16** with `hai-release\review\doc-jobs\destory_jscomments.py [--hand <json>] [--write]` (dry run lists every change; the write archives the original, refuses a drifted span, re-scans) and `check_page_scripts.py <app.py> [<old app.py>]` (`node --check` on the page's script, and code / styles / markup compared with comments removed) |

**What the 27B's drafts got wrong (all four mirrors, 1,458 drafts), each caught before a deploy:**
- **A rewrite that carries code.** A comment rewrite ending with the code line that follows it (a constant, a `return`,
  a call to `_refresh_registry_aside()`), or a docstring rewrite starting with its `def` line. Spliced as drafted it
  duplicates code.
- **Broken docstrings.** Closed with one quote or four, a `"}` tail, or `""" -> """`; nine foundry docstrings had
  their backslashes un-doubled.
- **A CUT that drops the rule.** About 40 of 100 rule-stating cuts were a docstring saying what a function returns, or
  a rule beside a dated incident. Kept as one clean sentence by hand.
- **A comment that a test or a tool READS is not a comment.** The `# --- LOOP RULE:` start marker (the block pinned
  byte-identical in `infer_ollama.py` and `tolkien_llm.py` by `test_fleet_deploy.TheLoopRuleIsOneSource`) was
  rewritten in both trees; the foundry's `render.py` was cleaned while the runner's copy (its one source) was not. Both
  put back from the committed copy. That block's comments were cleaned by hand, identically in both files
  (`hai-release\review\doc-jobs\loop_block_faithful_edit.py`; check with `instruments\loop_block.py`).
- **An invented reason.** `config.py`'s tier-threshold rewrite said lowering the line "causes wrong-lane routes"; the
  original says nothing of the kind. About 1 in 20 of the rewrites read closely looked questionable. The drafts were
  made with thinking off (`triage_classify._chat` hard-codes it).

**For a rule-dense file use the FAITHFUL edit, not the drafted rewrites** (`destory.py <sweep> --out overrides.json`,
then `destory_show.py` to sample it and mark the rest `MOVE`): it keeps each comment's own words and figures and takes
out only a date, who asked and their quoted words, and a doctrine citation, so nothing can be invented. Used for
`config.py`, `scheduler.py` and `chat_window.py` (121 of 129 clean; 8 that name users left untouched). It leaves
undated incident sentences in place: accurate, still history, to be tightened by hand. Before 2026-11-16 it also
deleted every empty `()` (a call named in a comment lost its brackets) and pulled `.attr` onto the word before; both
fixed, and the seven brackets it took from scheduler.py are back (`doc-jobs\find_lost_parens.py` checks a cleaned file
against its archived original).

**Owed:** a VERIFY pass over the rewrites already applied (does each state only what its original stated?), as a
foundry order with thinking on; the originals are in `Z:\Archives\code-preclean\` and git. The folders outside the
sweep: `coding-runner` (the detector skips it), the onboarding app, Uhura, the Discord relay.

## How HAI participates — belt-and-suspenders

Every phase has a path that does NOT require a Claude session, so progress continues if one drops out.

- **MEASURE (today, no new tooling):** HAI runs the inventory commands below via its coding lane →
  a per-file worklist. Pure measurement; HAI can do this now.
- **PLAN:** HAI's 27B can draft recommendations for the OPEN DECISIONS below; a session or the owner
  finalizes them. The inventory it produces informs sequencing.
- **BUILD:** HAI's self-eng lane is a candidate to build the tooling FROM THIS SPEC (the spec is the
  acceptance) — **when the owner arms it** (self-eng arming is human-only). Otherwise a Claude session builds
  the detector + apply tool; HAI drafts + runs.
- **APPLY:** HAI runs `code_apply_cleanup` per file (or, pre-tooling, cleans one file via its coding
  lane following the criteria + the two gates), and the deploy-gate suite gates every apply.
- **Review margin:** a session or the owner reviews HAI's drafted rewrites before apply, per the
  review-margin rule (review must cost less than authoring; flawed items re-queue).

## Sequencing — pilot first, like cameras was for the docs

0. **PILOT** — one self-contained module, end-to-end, tests green + reviewed. Proves the workflow and
   the gates before any scale sweep.
1. **orchestrator (HAI)** — daily-touched, ~36k lines top-level. Recommended first after the pilot.
2. **foundry (P20)** — ~29k lines (`mothership-build`, deployed to `F:\foundry`).
3. **tablet / tolkien** (`tolkien-build`).
4. **`Lab\bin` + `mothership-build` utilities.**

Batch by MODULE. One module = one timestamped archive artifact. Tests green after each batch.

## Inventory commands — run on demand (a fresh count beats a stale one)

From each mirror root (`orchestrator-build`, `mothership-build`, `tolkien-build`, `Lab\bin`, the menu's `hard-bench`, `lab-engine`):

```bash
# per-file worklist: files with the most narrative comments first
grep -rcE "#.*20[0-9][0-9]-[0-9]|⚠|#.*\bowner\b" --include="*.py" . \
  | grep -vE "backups|__pycache__|workspaces|coding-runner" \
  | awk -F: '$2>0' | sort -t: -k2 -rn

# totals (baseline / progress tracking)
grep -rhE "#.*20[0-9][0-9]-[0-9]" --include="*.py" . | grep -vcE "backups|__pycache__|workspaces"  # dated
grep -rhcE "⚠" --include="*.py" . | grep -vE "backups|__pycache__|workspaces"                       # ⚠-blocks
```

**RESIDUAL measured 2026-08-26** with the detector itself (`--self-test` validated first: positive
control finds 3, negative clean). This is the done-bar's "measured residual count":

| mirror | residual candidates | files | reading |
|---|---|---|---|
| Lab\bin | **2** | 2 | effectively done |
| tolkien | **7** | 4 | done bar the 2 held MOVEs |
| orchestrator | **37** | 29 | done bar the 27 held MOVEs — ~10 genuine stragglers |
| foundry (mothership-build) | **1155** | 56 | untouched: its draft pass was STOPPED at the margin gate |

**So the remaining 30e work is essentially the FOUNDRY.** Three mirrors are at or near the bar once
the reviewed MOVEs land ([[30e-MOVE-DISPOSITIONS-REVIEWED]]); the foundry still carries 1,155
candidates and needs the corrected draft pass (`triage_code` v2) before any apply.

**Baseline measured 2026-08-24** (top-level `*.py`, excluding backups/pycache/workspaces):

| codebase | dated comments | ⚠-blocks | "the owner" comments | lines |
|---|---|---|---|---|
| orchestrator | 434 | 216 | 159 | 36,170 |
| foundry (mothership-build) | 729 | 317 | 237 | 29,192 |
| tablet/tolkien | — | — | — | (to count) |
| Lab\bin | — | — | — | (to count) |

## DECISIONS — LOCKED 2026-08-24 (the owner: "let's lock in and execute")

1. **Codebase order:** pilot → **orchestrator** → foundry → tablet/tolkien → Lab. Orchestrator first
   (it's the daily-touched codebase).
2. **Division of labor — RULED 2026-08-24 (the owner).** *(Authoritative, project-wide: `30f-DIVISION-OF-LABOR.md`; full dated record: `Journal\2026-08-24-claude-dol-timeline.md`.)* ENGINEERING is Claude's: a *small* build of the
   code-cleanup capability, the same expedient as the 08-23 hand-builds (the owner didn't want to stall on
   self-eng). That capability = (a) a **code-rewrite payload** in the foundry (rides the
   `triage_rewrite`/`_chat` skeleton: two-bar test on comments, structured output, on-box, fleet shares),
   (b) a **GATED apply tool** (pending→confirm — the removal path HAI *invokes*; byte-exact splice using
   the detector's offsets, NOT a raw script, which the auto-classifier blocks), (c) the **detector** as
   the work-file builder (already built). The **code REMOVAL itself** — cutting/rewriting comments across
   the codebases — is **HAI-ELIGIBLE WORK**: HAI drafts on-box and invokes the gated tool to do the
   removal, per file. **Claude does NOT hand-edit code files** — the pilot comfy/cameras hand-edits were
   Claude doing HAI's job; that stops. the owner authorizes; Claude reviews at the margin; the RUNBOOK/suite
   gate catches breakage.
3. **When a comment's knowledge is NOT yet in any manual:** **file the durable constraint into the
   owning manual first** (honours files-twice); flag only the genuinely ambiguous for a human.
4. **Borderline "why" comments — the TWO-BAR test (recalibrated 2026-08-24):** a comment must be a true
   constraint AND worth a skilled dev's line. Always strip the story; then keep the surviving constraint
   only if a normal skilled dev would write it here — keep safety/concurrency invariants, real
   compatibility gotchas, and don't-bump tuning limits; drop magic-number "why" notes a good constant
   name already carries. Cutting on density is correct; cutting a genuine load-bearing constraint to look
   lean is NOT. (The earlier "when in doubt, keep the constraint" over-kept — that was comment-city with
   good comments.)

Execution posture (the owner, 2026-08-24): burn usage as needed as long as it's intelligent, per-plan,
delegates to HAI where possible, and leaves every artifact resumable by HAI or Codex.

## Acceptance / done-bar (per codebase, then overall)

- 0 dated war-story comments, 0 ⚠-narrative-blocks, 0 "the owner said" in comments; docstrings state
  properties, not outages.
- Earned knowledge verified present in the owning manuals (files-twice satisfied).
- Full deploy-gate suite green after each batch — behaviour unchanged is the load-bearing invariant.
- CURRENT-PROJECTS [MAIN] flips the code half to CLOSED with a measured residual count of ~0.

## Doctrine to obey (or it bites)

- The detector must **assert its count** — a scan that silently finds nothing reads exactly like a clean
  codebase.
- **Tests-green gates every apply.** A comment edit must not change behaviour, and the suite is the proof
  (comment-only diffs still get the full gate — a stray edit outside the comment is exactly what it
  catches).
- **One module = one archive artifact**, timestamped.
- **Never drive-by-cut** without the preserve gate. When editing a legacy file for other reasons, still
  write new additions clean, but a wholesale narrative strip is this project's job, gated as above.
