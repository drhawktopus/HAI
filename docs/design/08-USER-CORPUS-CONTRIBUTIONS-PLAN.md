# 08 — User Corpus Contributions (conversational "save to my/our stuff")

**Status:** STARTED. **Slice 1 (personal `save_to_corpus`) SHIPPED + LIVE 2026-07-03** — self-serve
chat tool writes a titled, provenance-stamped `.md` into the caller's OWN writable corpus and reindexes
(mirrors `/upload`); gated to own namespace; graceful message if the user has no writable corpus; nursing
library brief nudges it; 6 new tests, 774 suite green; byte-identical deploy; backups `*.bak-corpus1-20260703-002405`.
NOTE: model *triggering* in live chat not yet smoke-tested (peak energy / no GPU) — mechanics verified via
unit tests + live dispatch check.

**P0 core (persistent contribution queue + admin approve/reject API) SHIPPED + LIVE 2026-07-03** — new dep-free
`contributions.py` (cross-user, durable, JSON-per-record, injected `writer`); `config.CONTRIB_STATE_DIR`/
`CONTRIB_MAX_CHARS`/`CONTRIB_NOTIFY_WEBHOOK`; app.py `CONTRIB` queue + `_write_contribution` (writes approved
material into the shared corpus w/ provenance + reindex, bypassing the user-writable gate) + admin-only
`GET /contributions`, `POST /contributions/approve|reject`. 9 new tests, 783 suite green; live-verified admin
gating (the owner 200 / Riley 403 / nonexistent 404); backups `*.bak-p0-20260703-003557`.

**Submit tool + Discord notify (task #8) SHIPPED + LIVE 2026-07-03** — `contribute_to_shared_corpus` tool
(self-serve; resolves the persona-shared corpus via `config.shared_namespace_for_persona`; graceful when the
persona has no shared corpus). Queue made a process-wide SINGLETON (`contributions.queue()`) so the tool and
the approve endpoints share one instance (verified live: `app.CONTRIB is contributions.queue()`). Fail-soft
`contributions.notify()` Discord ping on submit (`CONTRIB_NOTIFY_WEBHOOK`, unset=no-op) with `CONTRIB_PANEL_URL`
deep link. Nursing prompt nudges both save + contribute. 4 new tests, 787 suite green; backups `*.bak-p8-20260703-004840`.

**BACKEND COMPLETE.** An admin can already approve/reject via the API today; only the UI surfaces (task #9 —
admin panel + in-chat card, frontend) remain. Deep link target `#review` is provisional until the panel ships.
**LIVE SMOKE TEST PASSED 2026-07-03 (cheap energy)** — ephemeral `test-nursing`: `save_to_corpus` triggered on
8B (relayed the graceful no-corpus msg), `contribute_to_shared_corpus` triggered on 27B (queued w/ auto-title),
reject API worked live (no pollution), Fix #3 audit log captured both calls in `/docs/Notes`. FOLLOW-UPS found:
(a) minor UX — 27B contribute reply was generic ("You're welcome!") instead of relaying "submitted for review"
(think-hard convergence dropped the tool-result relay); prompt nudge should fix. (b) `save_to_corpus` WRITE not
live-tested (no writable test corpus — `test-nursing`→read-only shared); write is unit-tested. (c) cosmetic:
running the suite writes tool-audit lines into the prod `/docs/Notes` log (tests should redirect notes to tmp).

**POLISH FIXES SHIPPED + LIVE 2026-07-03** — (1) relay nudge in NURSING_BASE: after save/contribute the model
must tell the user the outcome; VERIFIED live (27B now says "I've submitted the note ... for the owner to review"
instead of a generic ack). (2) notes isolation: `notes.py` honors `HAI_NOTES_DIR`; new `tests/conftest.py`
redirects notes for pytest; in-container `unittest` deploy command adds `-e HAI_NOTES_DIR=/tmp/hai-test-notes`;
VERIFIED the vault `/docs/Notes` stayed unchanged (8 lines) through a full suite run. 2 new tests, 789 green;
backups `*.bak-polish-20260703-172857`.

**UI (task #9) SHIPPED + LIVE 2026-07-03 — PROJECT COMPLETE.** Admin-only `📚 Review` header button + `#reviewPanel`
(on-demand, like the Audit panel) + auto-surfacing `#contribPanel` in-chat card (like the pending-actions panel),
both polling `/contributions` every 6s, rendering title/submitter/target/date/content-preview with ✓Approve/✗Reject
→ POST `/contributions/approve|reject`; `#review` hash deep-link opens the panel (for the Discord tap-in). Gated via
`isAdminUser()` (admin_users from /modes). JS node-checked; 3 static UI tests; 792→793 green. Backups `*.bak-ui-*`.
ALL THREE SURFACES done (panel + in-chat card + Discord deep-link tap-in). Only outstanding: set a real
`CONTRIB_NOTIFY_WEBHOOK` for live Discord pings (currently no-op), and interactive in-Discord buttons remain
deferred to the future Discord admin bot project.

**UNRELATED UX FIX (same session):** hai.lan chat textarea now auto-grows with multi-line input (was fixed 46px) —
CSS min/max-height + JS `autoGrowInput` on input; node-checked; test `test_chat_ui.py`; live. Backups `*.bak-textarea-*`.
**Owner:** Claude + the owner. **Related:** two-corpus-layers (shipped), `remember`/durable-memory (shipped),
generalized upload (shipped), pending-actions/confirm pattern (shipped), parked Discord admin bot idea.

## Goal
Let users **conversationally save material into the corpora that already exist** — their OWN personal
corpus (self-serve) and, when the material is broadly useful, the **persona-shared** corpus (admin-gated).
No new-corpus authoring, no dynamic registry, no user-authored personas — creating a brand-new persona/corpus
stays a rare **admin** action (ask the owner).

## Settled decisions (the owner, 2026-07-02)
- **Two write targets:** personal corpus = **self-serve** (their space, their risk); persona-shared corpus =
  **admin-gated** (affects everyone → the owner approves before it lands).
- **Reuse the existing `/upload` tool** for text extraction + writing; contributions just write to a
  **staging** area first, then move into the shared corpus on approval.
- **Three approval/notification surfaces** (all clients of ONE backend):
  1. **hai.lan admin review panel** — queue with content preview + Approve/Reject.
  2. **In-chat pop card** — pending contribution surfaces as an interactive card in hai.lan chat (reuse the
     existing pending-action card pattern).
  3. **Discord** — a notification with a **one-tap DEEP LINK to the review panel** ("approve from my phone").
     NOTE: true **interactive Approve/Reject buttons INSIDE Discord are DEFERRED** to the future "Discord
     admin bot" project (a webhook is one-way; buttons need a real bot + interactions endpoint + admin-ID
     check). For now = tap-in link only.
- **Fine-tuning / per-user model adaptation:** parked as research-first (see PROJECT-BACKLOG §B), NOT part of
  this project. The built path is RAG + curation + durable memory.

## Architecture — one core, three thin clients
**Core backend (build once):**
- **Persistent contribution queue** — NOT the existing `pending_actions` (that's same-user + in-memory +
  15-min TTL; this is cross-user Riley→the owner and may sit for hours/days). A small durable store
  (JSON/dir) of `{id, submitter, target_corpus, title, content/staged-file, created_at, status}`. The
  `pending_actions.py` docstring already anticipates this ("future UI work can surface the same records
  through an endpoint or persistent store").
- **Approve/Reject API** (admin-only) — approve moves the staged doc into the target shared-corpus dir
  (which is already writable: personal corpora are `:rw` mounts, shared `/docs/*Corpus` are under the rw
  `/docs` mount) → `rag.py` auto-reload indexes it. Reject discards.
- **Notification emitter** — reuse the existing notify-webhook (`OVERNIGHT_NOTIFY_WEBHOOK`, currently unset)
  to Discord; message includes title + submitter + deep link to the panel.
- **Provenance stamp** on every saved doc: who added it, when, source — so bad entries are traceable/removable.

**Write tools (chat-facing):**
- **Personal save** — `save_to_corpus(title, content)`-style tool: writes a titled `.md` into the CALLER's
  own namespace, auto-indexed. Gated to the caller's own corpus. (Assistant may proactively offer, or user
  asks.) Doubles as lightweight curation (save the good bits as you go).
- **Shared contribution** — same idea, target = persona-shared corpus → stages + enqueues + pings, does NOT
  land until admin approves.

**Boundary to keep clear:** `remember` = short *facts/preferences* that steer answers (memory layer);
save-to-corpus = *material/documents* retrieved via RAG. Assistant should route by type (one-liner fact →
memory; a chunk of material → corpus) or ask which.

## Phasing
- **P0 — core:** persistent contribution queue + approve/reject API + Discord notify (deep link) + provenance.
- **P1 — personal save-to-corpus tool** (self-serve; reuse upload extraction) + **in-chat pop card ③** +
  **admin panel ①**. (Panel + card are thin once the core exists.)
- **P2 — shared-contribution flow** end-to-end (stage → notify → approve → move → index) across all three
  surfaces (Discord = deep-link tap-in).
- **Later / other project:** interactive Approve/Reject buttons INSIDE Discord — folded into the future
  Discord admin bot project.

## Open items / to decide later
- Exact "save this" UX: proactive offer vs explicit user request vs both.
- Multiple named corpora per user? (Probably not needed now — one personal corpus each.)
- Dedup / near-duplicate detection when contributing to shared (avoid piling near-identical notes).
- Whether personal-corpus writes also want a light "are you sure / edit title" confirm.

## Non-goals (explicit)
- User-authored personas / dynamic corpus registry / self-serve NEW corpus creation (stays admin).
- Fine-tuning (parked, research-first).
- Interactive in-Discord approval buttons (deferred to the Discord admin bot project).
