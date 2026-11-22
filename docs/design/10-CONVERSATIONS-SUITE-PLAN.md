# 10 — PROJECT #18: CONVERSATIONS SUITE (hai.lan multi-chat)

**Origin (2026-07-10):** Riley request — "swap between chats instead of just making a new one and
losing the old one" — promoted from a backlog feature to a numbered project at the owner's direction, on the
"one size up" scope: not just switching, but conversations as first-class objects (manage, search,
scope, export). Numbering verified: #15 game servers, #16 non-code deliverables, #17 VGT persona → this
is **#18**. Doc series: 10- (doc numbers ≠ project numbers, as ever).

**Owner model:** Claude-built (real user-facing feature; UI is Claude's lane — HAI's verifier can't test
CHAT_HTML). Small pure-Python helpers (index manipulation, migration) are viable HAI fixture-corpus
shapes later, but the build itself doesn't route through the factory.

## Current architecture (researched 2026-07-10, the constraint set)
- `history.py`: ONE thread per user — `State/history/<user>.json` (messages) + `<user>.summary.json`
  (rolling compaction summary). `_persist` appends; `reset_history` wipes; `_trim` caps context.
- The chat UI re-renders history on page open (the reopened-page render path exists); "New chat" =
  `reset_history` = destructive — THE thing Riley is asking to fix.
- Durable user memory (`usermemory.py`) is global per user — stays global (who-you-are, not
  per-conversation). Uploads/corpora are user/persona-scoped, not chat-scoped (today).
- Identity enforcement (`_enforce_identity`) guards per-user access; chats inherit it (a chat id is only
  ever resolved under the authenticated user's dir — no cross-user reach by construction).
- Active-run reattach (`hai.activeRun.<user>` in localStorage + server registry) is per-user today; an
  Absurd run started in chat A must land its result in chat A (Phase 1 edge case).

## Phases

### Phase 1 — Multi-chat core (Riley feature) — ✅ SHIPPED 2026-07-10 same night (1143 green,
history dir backed up pre-deploy, live-probed; one design note vs plan: Absurd is ephemeral and
renders from its own registry, so the origin-chat reattach edge case doesn't apply to history)
Storage: `State/history/<user>/<chat_id>.json` + `State/history/<user>/index.json`
(`{chats: [{id, title, created_at, updated_at}], active: id}`). Title = first user message, clipped
(rename comes in P2). **Migration:** on first touch, existing `<user>.json` → chat `default`, index
created; `.summary.json` follows its chat (summaries become per-chat for free).
Backend: `GET /chats?user=`, `POST /chats/new`, `POST /chats/switch {chat_id}`; `/query` reads/writes
the ACTIVE chat (server-side pointer, so every client agrees); `reset_history` semantics: "New chat" =
create+switch; the old thread persists.
UI: chat switcher dropdown in the header (next to ⚙; sidebar deferred to P5 if wanted); switching
re-renders that chat's history via the existing reopened-page path.
Edge cases: active-run reattach binds to the chat that started it (store chat_id in the run registry);
Absurd results append to their origin chat regardless of what's active at finish.
Acceptance: switch away and back loses NOTHING; New chat preserves the old; migration keeps every
existing user's history byte-content intact; suite green; identity enforcement covers all new endpoints.

### Phase 2 — Management (rename · archive · delete) — ✅ SHIPPED 2026-07-10 (same night).
**Auto-naming added 2026-07-10 late (the owner: "before calling it done"):** the raw first-message title is
a PLACEHOLDER; a background 8B call generates a 3-6 word title (fail-soft, never blocks the answer;
guarded set — a manual rename that lands first always wins; sanitizer strips quotes/prefixes/multiline).
Live-proven: probe chat self-titled "RAM vs VRAM Differences" in ~10s; AUTO_TITLE notes line logged.
Rename (index edit); archive flag (hidden from switcher by default, "show archived" toggle); delete =
soft-delete to `State/history/<user>/.trash/` (no hard deletes — house doctrine) with an admin-visible
retention sweep later. Auto-title improvement: retitle from the first exchange summary instead of raw
first message.

### Phase 3 — Search — ✅ SHIPPED 2026-07-10 (same night, as designed: plain-text, own chats only,
archived searchable, trash not)
Plain-text search across the user's own chats first (server-side grep over their JSON, ranked by
recency; SQLite FTS only if scale ever demands it). UI: search box in the switcher; results jump to the
chat (+ scroll-to-message if cheap). Explicitly NOT embedding-based in v1 — the corpus/RAG machinery is
for documents; conversations need findability, not semantics.

### Phase 4 — Per-chat scoping + export — ✅ SHIPPED 2026-07-10 (the owner's fork decisions: pins =
PRIORITY BOOST (`RAG_PIN_BOOST`, default 5.0, added to BM25 score pre-gate — never a filter);
trails PERSIST for deliberative answers only (think-hard extras on the assistant message; Absurd
already persists via its registry; quick answers stay lean). Pin UX: cited sources in the answer
meta are click-to-pin toggles (📌, gold when pinned, cap 10/chat). Export: ⬇️ header button →
markdown download honoring the View axis (thinking adds 🧠 journals, verbose adds 🧭 steps;
pre-P4 messages export honestly as plain). Reopened chats now RENDER persisted trails.)
Pin documents to a chat (a nursing-assignment chat carries its rubric: pinned doc ids get retrieval
priority in that chat only); export a chat (markdown download — pairs with the View axis: export honors
Verbose/Summary). Cross-chat rules stated explicitly in the UI: memory crosses chats, context/pins do
not.

### Phase 5 — Polish / deferred
~~Sidebar layout~~ ✅ SHIPPED 2026-07-10 (the owner pulled it forward the same night: full-height left
sidebar replaces the dropdown — rows with active-row actions, in-list search, Show archived footer,
☰ toggle with persisted preference, narrow screens default hidden; hidden chatSel kept as JS mirror).
Still deferred: cross-chat "continue from here" forking, retention/quota per user, chat sharing
between users (needs an access-model decision — NOT designed here).

## Invariants (checked against homelab constraints + house doctrine)
- Presentation vs request: chat_id IS a new `/query` param (unlike the View axis) — additive, optional,
  defaulting to the active chat, so every existing client/tool keeps working unchanged.
- No schema/infra additions: flat JSON files on the existing docs share, same as today (SQLite-on-SMB is
  the known Pi-hole trap — avoided by construction). Bounded per-file sizes via the existing `_trim`.
- Privacy: all endpoints behind `_enforce_identity`; a user can only ever list/switch/read their own
  chats. Admin sees nothing new (audit stays at the notes/diag layer).
- Memory global; history per-chat; uploads per-user (P4 adds per-chat PINNING, not per-chat storage).

## Order + success criteria
P1 ships alone (Riley unblocked, everything after is additive). Overall success: Riley manages a
semester of coursework as parallel chats without asking for anything above; nothing about #12's factory
or the existing single-chat users' history breaks along the way.
