# HANDOFF — Chat-interface wiring audit: backend capability vs chat-frontend exposure

**For: a secondary session driven by the owner. Read `Reference\OPERATING-BRIEF.md` FIRST.**
Written 2026-08-28 by the main session (Fable). This doc is the charter; the owner is the operator.

## Why (the owner's directive, 2026-08-28, verbatim)

> "we need to ensure that all of the pipes and capabilities are wired into the chat
> interface, since that's my primary way of interacting with HAI and the lab. we have
> caught ourselves where it was wired on the backend but not on the front end."

The class is CLAUDE.md doctrine **class 17: a capability that exists but is unwired fails
exactly like one that doesn't exist.** Known instances (all since fixed — they are your
method validators, see below): `coding_client.create` existed but was never OFFERED to the
27B's tool selection (2026-08-27 G4-3, rhyming with the 08-15 selection gap); the absurd/
driver lane ran for a day with only a completion webhook until the "HAI - Deep Work"
homepage tile was added over `/absurd/status` — an endpoint that existed, unpublished;
driver runs then needed `phase`/`progress` fields added because the tile could not show
tangible progress.

## The acceptance test (the owner's terms — this is the bar, not registry parity)

**the owner types a request into the chat window (hai.lan / hai.example.org) and HAI can
actually DO the thing** — kick a commission, arm a campaign, check the fleet, submit
compute, show progress — or visibly route/refuse with a reason. A capability passes only
if it is reachable BY ASKING, in conversation, at whatever tier answers him. Admin-gated
capabilities must work for the owner-as-admin from that window; "the owner can't reach it by design"
must be distinguished from "nobody wired it." Judge every capability against this test.

## Scope — three layers, audited per capability

"Wired into chat" is THREE different claims (the DECLARED ≠ PRESENT ≠ VISIBLE doctrine):

1. **Tool layer** — HAI's tool registry vs what is actually OFFERED to the model in chat.
   Enumerate every registered tool (schemas in the orchestrator's tools module), then the
   selection machinery: retrieval-based tool selection, `CORE_TOOL_NAMES`, the
   `_heavy_toolset` sets (`CODING_JOB_TOOLS`, `CODING_SURVEY_TOOLS`), admin gating,
   sandbox gating. For each tool: in which chat contexts can it actually be selected, and
   by which tier? A tool registered but unreachable by any selection path is the gap.
2. **Endpoint layer** — FastAPI routes (app.py + the per-feature modules) vs their
   consumers. An endpoint no UI or tool consumes is either deliberate plumbing or an
   unpublished capability — classify which, with evidence.
3. **Chat-UI layer** — what the owner can SEE and TRIGGER in the chat interface itself
   (`CHAT_HTML` in app.py — ⚠ read-only this pass; it is a NON-raw Python string, the
   documented `gotcha-embedded-js-in-python-string` trap). Specifically check the NEW
   surfaces shipped this week render for him: commission/driver runs (phase/progress),
   campaign-chain visibility (campaign id on orders), the tiered-refusal path
   (`refused_draft` — can the owner reach a preserved draft from chat?), advisory findings
   (`advisory_findings` — visible anywhere in chat, or backend-only?).

Cross-check (secondary): homepage deck tiles — the standing rule is every lane ships with
its deck surface; verify each current lane has one.

## Method — doctrine-bound

- **READ-ONLY.** No code edits, no deploys, no container restarts, no factory-floor
  actions. The deliverable is a findings table + build list, not fixes.
- **Currency first:** the mirror `E:\SSDShared\orchestrator-build\` is your read surface,
  but a mirror is only a mirror if just hashed — for any load-bearing claim, verify the
  file against LIVE on Sundae (`ssh sundae`, container `/app`) or note the claim as
  mirror-based. Ignore `*.preclean-*` / `*.wip-*` snapshots.
- **Enumerations go to FILES** (work dir: `E:\SSDShared\library-triage\chat-wiring-audit-20260828\`),
  then grep the files. Never cite a bounded window (`head`/`tail`/offset reads) as an
  artifact. Every matcher/join must **assert its match count and fail on zero**.
- **Validate the instrument against known positives before trusting a negative:** your
  enumeration must correctly show `run_review_campaign` as wired+admin-gated,
  `catch_up` in `CORE_TOOL_NAMES`, and `queue_compute` as admin-gated AND
  sandbox-blocked. If it can't see those wires, the instrument is broken — fix it before
  reading any absence.
- **A deliberate gate is NOT a gap.** Admin-only, sandbox-blocked, and consent-gated
  capabilities are classified GATED-BY-DESIGN — citing the enforcing LINE of code, not a
  doc claim (documented protection ≠ enforcement).
- Verdict vocabulary, one per capability: **WIRED** · **GATED-BY-DESIGN** (cite line) ·
  **BACKEND-ONLY** (the gap the owner means) · **UI-ONLY** (orphan control) · **UNKNOWN**
  (say so explicitly — never render "could not tell" as a definite state).

## Deliverables

1. `LLM-Project\Chat-Interface-Wiring-Audit-FINDINGS.md` — the wiring table
   (capability → backend location → selection/exposure path → chat-visible? → verdict),
   totals asserted (N tools enumerated, N endpoints, N verdicts — counts must reconcile),
   plus a **prioritized build list**: each BACKEND-ONLY gap sized as either "HAI
   commission through the driver" or "small direct change," with a one-line why-it-matters.
2. Side-session journal `Journal\2026-08-28-claude-chatwiring.md` + a completion block at
   the top of `Reference\CURRENT-PROJECTS.md`.
3. Nothing else changes. Fixes are sequenced afterwards by the owner + the main session
   (default path: HAI commissions).

## Standing constraints

- Light footprint on Softserve (Plex host). Reading is free; the observation deck is
  yours. The deploy lane and factory queues are not.
- If blocked, report the exact command and stop — never build a second mechanism.
