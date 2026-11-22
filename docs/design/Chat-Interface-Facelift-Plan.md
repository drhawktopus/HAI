# Chat-Interface Facelift — build plan (from the 2026-08-28 wiring audit + the owner's refinements)

Status: SPEC (the owner-directed). Author: main-session Opus fork, 2026-08-28. Precedes build.
Origin: the chat-wiring audit (`library-triage\chat-wiring-audit-20260828\FINDINGS-*.md`) found the
tool plumbing clean but the newest capabilities not shown on the chat "door." the owner greenlit a
facelift and expanded scope. Framing: **the chat window is the ONE DOOR to the organism; make it
show what the lab can now do** (see 2026-08-28 journal §framing; NI proposition §11 Kirk-is-the-
organism, §10 three layers).

Governing constraints: CHAT_HTML is a NON-RAW Python string (`gotcha-embedded-js-in-python-string`
— a bad `\n` kills the whole page incl. login); every UI change is validated by extracting the
SERVED `<script>` and running `node --check`, and rolls back first on any breakage. Deploys go
through `RUNBOOK-Orchestrator-Deploy-Procedure.md` with the test-count pin, one piece at a time.

## DONE
- ✅ **Camera pool fix (audit Finding 1)** — `_CAMERA_TOOLS` into `HEAVY_TOOLS` + behavioral parity
  pin. Shipped 2026-08-28, gate `2330/2` predicted exactly.
- ✅ **Piece A1 — run titles (BACKEND)** — `app._derive_run_title` (prefers an `Objective:` line,
  skips `Mode:` headers, sanitized via the existing `_sanitize_title`, falls back to the machine
  id); wired into `/absurd/start` (record `title`, status API carries it, kickoff message reads
  "Started: <title>"). 5 behavioral pins (`test_run_titles.py`). Shipped 2026-08-28, gate `2335/2`
  predicted exactly, sync `a66eaac`. **A2 (render titles in the UI badge/tiles) rides with the
  CHAT_HTML work below.**

## Piece A — HUMAN-LEGIBLE NAMES (foundational; do first)
**Problem (the owner, 2026-08-28):** "orders and things like that... human understandable and human
legible, not just collections of letters and numbers" (`abr-1787841129-ea1708`).
**Design:** the machine id stays the stable KEY (receipts, dedup, campaign linkage, "one name = one
artifact"). Add a human **`title`** field alongside it, auto-derived from the kickoff/purpose text
via the EXISTING `app._sanitize_title` (the same machinery that auto-titles chats via
`_auto_title_worker`), ≤60 chars, user-overridable. Precedent already in-tree: `commission_title`
coexists with `commission_id`.
- Absurd runs (`absurd.new_run_id()` → `abr-<epoch>-<hex>`) gain a `title`; set at
  `/absurd/start` from the packet/request text (sanitized). Campaigns carry the title on the run
  record + the campaign-id linkage on factory tiles.
- Surface the TITLE everywhere the user sees the thing (chat kickoff line, in-chat tracker tile,
  deck tiles, campaign page); keep the id as a subtitle/hover so linkage stays visible.
- Pins: title present + sanitized on a new run; id unchanged as key; title falls back to the id
  when no usable text.

## Piece B — REFUSED-DRAFT DISCLOSURE + conversational correction (audit Finding 3)
**Problem:** G2c tiered refusal preserves the withheld draft in `extras["refused_draft"]` (+
`unresolved_findings`), persisted WITH the chat message (same `extras` dict whose `transcript` the
UI already renders), but the chat UI never exposes it. And (the owner, 2026-08-28) corrections must be
**speakable, not only hand-written** — "I'll need to be able to talk to the system to give it
corrections as well as hand-writing."
**Design:**
- **Disclosure (build now):** on a refused answer, a collapsed-by-default **"Show the withheld
  draft"** control that reveals the draft + the findings that caused the refusal. Data already
  reaches the client via the persisted `extras`; this is a message-renderer addition mirroring the
  existing transcript disclosure.
- **Override = an EXPLICIT control, not NL parsing (the owner, 2026-08-28):** "maybe a checkbox or
  specific thing for me to select for me to override, that way it can't misinterpret me saying
  something." So a refused answer gets a deterministic **"Ship this draft anyway"** button the user
  CLICKS → a reason box → Confirm. No natural-language override path — removes the misinterpretation
  risk by construction.
- **⭐ The reason makes the system RECONSIDER, not just file it (the owner, 2026-08-28 addendum):** "when
  I override with a reason, I want the system to THINK about the answer, not just file it away for
  later use." So the override does NOT promote the draft verbatim — it RE-ANSWERS: the model gets
  the held draft + the findings that held it + the user's reasoning, and produces a reconsidered
  answer (confirm the draft, refine it, or address a concern the reasoning didn't cover). The reason
  ALSO persists (NI signal) — double duty. Single focused 27B reconsideration pass (not a full
  think-hard); falls back to the verbatim draft if Mothership is unavailable (`reconsidered:false`
  so the UI can say so). The reconsidered answer appends as a new bot message ("Reconsidered after
  your override").
- Pins: refused answer exposes draft+findings; a plain answer shows neither; override path acts.

**Grounded build spec (contracts traced 2026-08-28):**
1. **Server (safe, non-UI):** the think-hard result dict (`app.py` ~3804, `_run_think_hard`) is
   built right after `_apply_unresolved_gate` populates `extras`. Surface the refusal data into the
   response: when `extras.get("refused_draft")`, add `result["refused"]=True`,
   `result["refused_draft"]=extras["refused_draft"]`, `result["unresolved_findings"]=
   extras.get("unresolved_findings") or []`; also surface `unresolved_findings` on the WARN case
   (advisory banner) so the same UI shows advisory findings richly. NOTE: response currently carries
   `answer`/`transcript`/`work_journal` only — the draft is persisted (`_persist` extras) but NOT
   sent to the client, so this server add is required first. History-reload path must expose the
   same fields for a refused message loaded later.
2. **Override endpoint (backend):** `POST /answer/override` — promotes the preserved draft to the
   accepted answer and RECORDS the override WITH the user's reason (audit + NI signal). Keyed by
   chat_id + message ref; admin-gated; updates the persisted message (refusal → draft,
   `overridden: true`, `override_reason: <text>`). Deterministic, click-driven — NEVER inferred from
   chat text. ⭐ The reason is a human decision-with-rationale — exactly the correction/override
   signal the NI campaign wants (the owner, 2026-08-28: "I want to be able to give it feedback on why I'm
   saying ship anyway"); persist it where the NI extractors can later find it.
3. **UI (CHAT_HTML — careful, node --check):** mirror the existing `<details class="thoughts">`
   disclosure pattern (render fn near `add('bot',…)` ~7929, trail fields read ~8346 `d.thoughts`/
   `d.transcript`). On `d.refused`: a collapsed "Show the withheld draft" `<details>` (draft +
   findings list, `fndClass` severity styles already exist) + an explicit **"Ship this draft
   anyway"** button that opens a small REASON box; submitting POSTs the override + reason and swaps
   the displayed answer to the draft. Reason is encouraged (the NI signal) — allow blank but prompt
   for it.
4. **Pins:** server carries refused_draft+findings on refuse and neither on a plain answer;
   override endpoint promotes+records and is admin-gated; findings render by severity.
**Build note:** cohesive feature (backend + fragile UI + history-aware override) — best built as ONE
careful unit; the CHAT_HTML half gets served-JS `node --check` + roll-back-first, per the governing
constraint. Backend is HAI-commissionable; the CHAT_HTML string wants a careful supervisor hand.

**✅ PIECE B COMPLETE 2026-08-28 (backend + UI both shipped).**
- Backend (gate `2338/2`, sync `b97fc1a`): think-hard response carries
  `refused`/`refused_draft`/`unresolved_findings`; `history.apply_override` promotes the draft +
  records `override_reason`, no double-override (3 pins, `test_answer_override.py`); admin-gated
  `POST /answer/override` (query params `user`/`chat`/`reason`).
- UI (gate `2338/2`, sync `a48c346`): `refusedDraft(el, d)` in CHAT_HTML — a "📝 show the draft"
  `<details>` (findings by severity via `fndClass` + the draft) with an explicit **"Ship this draft
  anyway" → reason box → Confirm** flow POSTing the override. Pure DOM construction (no backslash
  trap). Verified BOTH layers: app.py compiles + the SERVED JS passes `node --check`, page renders.
  Override is a CLICK, never NL-parsed (the owner's requirement met).
- **⭐ USER PICKS THE REASON'S DELIVERY MODE (gates `2341/2` ×2, syncs `c6ac9f9`+`d42daff`) — the owner's
  addendum + refinement.** First addendum: "make the system THINK about the answer, not file it
  away." Then the refinement: "an option for each — I get to select how my reason is delivered, as a
  comment or reconsider and resubmit." So at ship time the user chooses:
  - **`mode=comment`** — the draft ships verbatim; the reason rides along as a comment (shown inline
    + recorded). Passive path.
  - **`mode=reconsider`** (default) — `history.refused_context` gathers the held
    question/draft/findings, `_reconsider_after_override` runs a single 27B pass re-answering
    (weighing reason vs findings), result persisted. Falls back to the draft + `reconsidered:false`
    if Mothership is down. Active path.
  Both persist via `history.apply_override(user, answer, reason)` (stores the shipped answer, returns
  bool) and record the reason (NI signal). UI: "Ship this draft anyway" → reason box → two buttons
  ("Ship + add reason as comment" / "Reconsider & resubmit"). 6 pins (`test_answer_override.py`,
  isolated temp HISTORY_DIR). Both layers re-verified each deploy (compile + pins + served-JS
  `node --check` + ~125 KB render). **The reason is BOTH a chosen live input (comment or nudge) AND
  a persisted NI decision-with-rationale signal.**

**What the owner actually wants the page to show (confirmed 2026-08-29): "live runs AND a whole numbered
board."** So the page is TWO layers:
1. **The numbered board** — the whole `PROJECT-REGISTRY` rendered LIVE: every #10–#35 with state
   (`IDEA→SCOPED→REGISTERED→ACTIVE→PARKED→SHIPPED/CONCLUDED`), the CAMPAIGN/EPIC flags, charter
   link, current phase/gate. "See just the campaign status as I move through things."
2. **Live runs** — everything actually executing now (factory orders / p20 cascades / absurd
   commissions / self-eng / overnight), each rolled up to the project/campaign it serves.

Plus the two chat surfaces:
3. **In-chat tracker tile** — a small, DISMISSIBLE tile that lives in the chat window until clicked
   closed (persist dismissal), a glanceable live tracker of a run you kicked off; modeled on the
   Absurd run-status display. "So I don't always have to open a separate window."
4. **Kickoff + pointer in chat** — starting a run replies with its human title + id + a pointer to
   the full page.

**Inventory (2026-08-29, `app.py` route sweep) — what exists vs. what's new:**
- **Live runs ALREADY have surfaces — aggregate, don't rebuild** (read-the-deck): `/p20/factories`
  + `/p20/factory/{host}` (per-host lane/progress/health), `/absurd/status` (commissions/coding),
  `/self-eng/state|report|overnight/status`, `/plan/commissions`, `/p20/order/{id}`, `/p20/jobs`.
  Live-runs half = a roll-up endpoint over these + a campaign/project tag on each.
- **The numbered board has NO endpoint** (`/projects`, `/registry`, `/board` all absent). The
  registry is markdown (`PROJECT-REGISTRY.md`, three tables: projects · campaigns · epics). So the
  board is the ONE net-new backend: PARSE the registry (source of truth — parse, never mirror into
  a second writer, class 2) → structured JSON → serve, with pins on the fixed table format.
- Doctrine seam: the DECK is the permanent home ("read the deck, don't build one"); the in-chat
  tile is a compact GLANCE that pours the deck status over, never a second supervisor — it READS,
  never drives.

**First HAI dogfood target = the numbered-board endpoint** (self-contained, testable, and it reads
`DOCS_DIR/Reference/PROJECT-REGISTRY.md` at runtime — the doc is DATA outside the coding workspace,
so its table format gets embedded in the packet verbatim per the packet-contract rule; HAI writes
the parser+endpoint in-workspace). Cleaner + more useful than the cascade reconstruction.

## Build order & mechanism
A (names, backend-first) ✅ → B (disclosure + override, self-contained UI) ✅ → **C (work
visibility — the big one: numbered board + live-runs page + in-chat tile + kickoff pointer).**

**C internal sequence (proposed 2026-08-29):**
1. ✅ **Numbered-board endpoint — SHIPPED 2026-08-29** (`project_registry.py` + `GET /projects` +
   10 pins; gate `2351/2` exact; serves the live board 26 projects / 5 campaigns / 1 epic).
   **⚠ Attempted as the first HAI dogfood — it did NOT ship, but surfaced real coding-pipeline gaps
   (see 2026-08-29-claude):** `coding_test` only runs the FULL suite (no scoped iteration for a
   job that adds tests), and a non-converged isolated task force-deletes its branch on abandon
   (`git branch -D`, runner.py:965) → 73 min of real work discarded, unrecoverable. So I
   supervisor-ported it (guaranteed morning progress) and filed the pipeline fixes to backlog.
   **⚠ SUPERSEDED 2026-08-29 eve — dogfooding is NO LONGER gap-blocked.** Gap (b) work-preservation =
   F2, SHIPPED. Gap (a) scoped-test-profile was RETRACTED by main as not-warranted (sandbox iterates in
   ~29 s and scoping would let the model skip red tests — "no going over red"). Proven the same night:
   **dogfood-3 (the SyntaxWarning commission) had the 27B produce CORRECT, deployable code** — only its
   self-REPORT refused, on a self-regenerating pin-stop finding (→ correction A, FIXED + deployed) plus
   refine-loop task churn (→ correction B, scoped). The remaining constraint is **task-scoping to the
   27B's reach + B's convergence**, not a missing pipeline feature. Detail: PROJECT-BACKLOG "FIRST
   SUCCESSFUL HAI DOGFOOD".
2. ✅ **Live-runs endpoint — SHIPPED 2026-08-29** (`GET /runs/active`, admin) — cross-user
   in-progress deep-work (Absurd) runs, projected `{run_id,title,status,last_status,user,elapsed_s}`
   (no packet text); `absurd.list_active_all` + `absurd.active_runs_view`, 7 pins; gate `2358/2`
   exact, sync `0af6140`. **Scoped to the Absurd source first** (the cleanest slice); a fuller
   roll-up that folds in `/p20/factories` factory orders + tags each run with its project/campaign
   is a later slice.
3. ✅ **The page (deck) — SHIPPED 2026-08-29 eve** as `GET /board` (`BOARD_HTML`), a **SEPARATE
   route** from CHAT_HTML (chosen deliberately: a render bug here can't take down chat/login; the
   in-chat pointer in item 4 will link here). Renders the numbered board (`/projects`, public) +
   the live-runs panel (`/runs/active`, admin), identity via `/profiles`; graceful admin-gate
   degradation, 15 s runs / 60 s board refresh. **Raw-string** `BOARD_HTML` so the embedded JS
   escapes stay literal (out of the non-raw CHAT_HTML trap). Deployed via RUNBOOK: audit DRIFT 0,
   in-container app.py sha `8026a074`, served-JS `node --check` clean, gate **2368/2** exact (+5
   pins `tests/test_board_page.py`), health 200, `/board` 200, sync `4aeb039`. Open follow-on: the
   live-runs roll-up (fold `/p20/factories` in + tag by project/campaign) — **TEED UP FOR SUPERVISED
   HAI Tuesday 2026-08-30** with a packet-ready spec (the owner, 2026-08-29: "add it in… hand to hai if
   big or flag for us to supervise hai on once usage resets"). Spec + slice split (slice 1 = fold-in,
   dogfoodable; slice 2 = project tagging, a DESIGN FORK for the owner) in `PROJECT-BACKLOG.md`
   "TEED UP FOR SUPERVISED HAI".
4. ✅ **In-chat tile + kickoff pointer + A2 titles — SHIPPED 2026-08-29 eve** (CHAT_HTML,
   supervisor-hand). A dismissible **Absurd run-tracker tile** (`#absurdtile`, modelled on the p20
   render tile) tracks the deep-work run you kicked off: **title (A2)** + live `last_status`/elapsed
   + a link to **/board** (the pointer), persisted dismissal (`hai.absurdDismissed`). Wired into
   `startAbsurd` (also adds a `/board` link to the kickoff meta), `pollAbsurd` (updates each 15 s
   poll, clears on finish), and `resumeAbsurd` (restores on reload). **Non-raw trap avoided: the
   dismiss handler is bound via `.onclick` property, so my added JS has ZERO backslash escapes.**
   Verified: `eaten_escapes` gate guard green, **served `/` (login) JS `node --check` clean** (the
   real login-safety proof — checked against `app.CHAT_HTML`, the Python-interpreted bytes, not the
   raw source), in-container app.py sha `9a6260e9`, gate **2369/2** exact (+1 pin `test_chat_html_js`),
   `/` 200, sync `28c439f`. **⚠ Methodology note:** node-checking the *source* of a non-raw string
   FALSE-alarms on pre-existing `\\[` regexes — check `app.CHAT_HTML`/the served page, never the
   literal's source bytes.

Each piece ships as its own deploy through the RUNBOOK cycle with a predicted test-count pin and
(for UI) served-JS `node --check`. Supervisor-authored where the CHAT_HTML string is touched; HAI
commissions where a piece is cleanly decomposable. Margin review before each deploy.
