# 10c — G5: HAI as the daily-driver fleet supervisor (design proposal)

Status: **✅ ALL THREE QUESTIONS RULED BY owner 2026-08-15 afternoon — building per the rulings**
(admins-only arming · 60s live tick · LCARS page SCOPED below, built later: "Scope the page now,
but we'll do it later 😁"). Author: main session, 2026-08-15.
Parent: [[10-HAI-INDEPENDENCE-PLAN]] (G5 row, :138). Siblings: `06b` (signed+built today — the
lifecycle pattern this cribs), `10b` (the Kirk-executes-never-grades boundary). the owner's goal
statement (2026-08-15): G5 is *"the most useful one to me, and the daily driver"* — you ask HAI
to watch fleet work; HAI arms the watcher, reads the deck, DMs you on a verdict.

## What the library sweep found (citations in the sweep record, journal 08-15)

The lab already owns: the on-box guards (stall/rescue daemon with `watch.<kind>` capabilities,
health daemons, GPU patrol), ONE DM relay (`hawkadminbot:8477/dm`, Kirk already sends via
`_overnight_dm_send`, fail-soft), the deck (15 GET surfaces incl. today's order-fate pane), the
daemon's verdict VOCABULARY (progress/watching/wedged/…/unwatchable), and the doctrine set
(no-ratchets · exit-on-verdict+INCONCLUSIVE · positive-control-the-matcher · report-vs-act
two-tier · guards-never-narrate-unmeasured · one-derivation · restart-policy+decoy · an
automated remedy never undoes a human · never block the owner's input).

**The four true gaps:** (1) HAI cannot READ the deck — no tool wraps `/p20/*` (doctrine 17:
Kirk serves the surfaces and cannot call them); (2) no watch/arm tool — all 118 tools are
snapshot-reads; (3) no Kirk-side watcher registry; (4) no generic verdict→DM path (every sender
is hard-wired to one lifecycle).

## The design — two increments, REPORT-ONLY by construction

### Increment 1 — `fleet_status` (the read; pure wrapping, no new mechanism)

One tool wrapping the deck's own derivations: `/p20/factories` (post-phantom-fix), per-host tile,
`/p20/rescue`, `/p20/order/{id}`, `/self-eng/overnight/status`. Answers "is anything wrong with
the fleet right now?" from the SAME derivations the homepage shows — one-derivation rule; never
a second deck. Selector-friendly description carrying the words the owner types ("fleet", "factory",
"training run", "is anything wrong", "what's the card doing"). Internal call, no HTTP loopback.

### Increment 2 — `fleet_watch` (the daily driver)

* **Arm:** `fleet_watch(action=arm, target, condition, deadline_minutes, note)` returns a
  `watch_id` IMMEDIATELY (never blocks the owner's input — 5.13). Targets: an order id (fate change),
  a host (state/health change, e.g. wedged/DOWN/idle-when-should-be-training), the overnight
  run. **Conditions come from the EXISTING verdict vocabulary** — the watch matches what the
  deck already derives; it never invents a discriminator (the daemon/tile own those).
* **Registry:** watch records in Kirk's store (the 06b pattern: typed fates WAIT →
  FIRED / EXPIRED(deadline, reported as the deadline verdict, e.g. STILL_IDLE_75M) /
  INCONCLUSIVE(could-not-measure — stale deck source = say so, never silence). Deck-readable
  via a fate pane (`GET /p20/watch/{id}` — same shape as the order pane, fate not internals).
* **Evaluator:** a Kirk-side tick beside `_selfeng_orders_tick` — **on-box by construction**
  (no-ratchets satisfied; survives sessions; the scheduler loop already has the cadence +
  restart story via the container's own lifecycle). It READS deck derivations only.
* **Verdict → DM:** a generic `_watch_dm()` wrapping `_overnight_dm_send`'s fail-soft shape —
  the relay everything already uses, never a second Discord path. The DM names the watch, the
  verdict, and the deck reading it fired on. (Pattern precedent: HawkAdminBot's live
  poll→post→button loop for contributions.)
* **REPORT-ONLY, hard:** G5 watchers NEVER act — no kills, no requeues, no slips. Remedies stay
  with the on-box guards that own them (the daemon's round-capped ladder, the patrol's tiers).
  This sidesteps the entire remedy minefield (operator-override scar 5.8, two-tier 5.5) for v1;
  an acting tier would be its own signed proposal.
* **Honesty invariants:** a watch whose SOURCE is stale/unreadable reports INCONCLUSIVE with the
  reason (never green, never silent — 5.4/5.7); every watch carries its deadline verdict; where
  a matcher parses text, it arms with a positive control first (5.3).

### Chat wiring

`fleet_status` + `fleet_watch` (arm/list/cancel) tool schemas; ungated reads, watch-ARM…
**open question 1** below. `catch_up` untouched.

## Explicitly NOT in v1 (named, with reasons)

Acting/remedy watchers (own proposal) · camera watches (nothing performs detection since 08-12 —
scope doc :767) · watches keyed on non-compute order COMPLETION (only compute orders report
receipts — RUNBOOK :829; named dependency, not silently absorbed) · G4 order submission (still
its own build) · any new alert path or discriminator.

## Open questions — TWO RULED by the owner (2026-08-15 afternoon), one pending

1. **Who may ARM watches? ✅ RULED: "Admins only"** — ADMIN_USERS (the owner + ClaudeSupervisor).
   A watch spends the operator's DM attention.
2. **Watch cadence: ✅ RULED — go live-speed.** the owner: *"any reason to not go live? Most things
   in the fleet are reporting that often I think"* — and the record agrees (foundry run-state
   ~20s, rescue daemon 60s, gpu-state 60s). **Dedicated `_watch_loop` at WATCH_TICK_SECONDS=60**
   (own thread, own try, the existing scheduler-loop pattern; the 5-min overnight tick is
   untouched). Verdict latency ≈ source freshness + ≤60s.
3. **The LCARS FLEET COMMAND DECK** (his Star Trek bridge-page idea, CURRENT-PROJECTS:4218):
   fold its first increment in, or keep separate? Lean: separate — G5 is the engine, LCARS the
   paint; the watch registry + fate panes become its feed either way. PENDING his word.

## SCOPED, DEFERRED: the LCARS FLEET COMMAND DECK page (the owner's bridge console)

Ruled 2026-08-15: scope now, build later. The scope, complete enough for a cold pickup:
* **What it is:** a read-only, LCARS-styled (Star Trek bridge aesthetic) web console rendering
  the fleet in one screen — per-factory tiles (card/run/health from `/p20/factory/{host}`),
  the aggregate strip (`/p20/factories`, post-phantom-fix), active + recent WATCHES with their
  verdicts (the G5 registry's fate panes), the overnight/self-eng row (`/self-eng/overnight/
  status`, after its nulls-after-restart fix), and the rescue/daemon row (`/p20/rescue`).
* **One-derivation rule, absolute:** the page RENDERS deck endpoints via client-side fetch; it
  derives NOTHING and stores NOTHING — a skin, not a second deck. Any number it wants that the
  deck lacks = a deck addition first.
* **Where it lives:** served by Kirk as a static page (the CHAT_HTML pattern) or a Caddy static
  site; reachable as a `.lan` name via the standard Caddyfile + Pi-hole `lan.list` pattern, and
  remotely via the existing Tailscale path. HTTP only (`.lan` gets no public certs).
* **Auth story:** LAN-trusted read like the homepage tiles; no controls in v1 (a later "hail
  the fleet" button tier would ride the same admin gating as watch-arming).
* **Non-goals:** no writes, no chat, no graphs-that-compute (rate/ETA render as served), no
  mobile app — responsive CSS suffices.
* **Effort class:** a cosmetics-day build once G5's panes exist; zero new backend beyond the
  already-planned watch fate panes. Backlog entry: PROJECT-BACKLOG "LCARS FLEET COMMAND DECK".

## Build shape (after sign-off)

Increment 1 ships first (small: one tool + schema + selector words + tests, ~a day's window
with gates). Increment 2 follows (registry + tick + DM + fate pane + tests + live decoy: arm a
watch on a dry-run overnight order and watch the DM arrive). Standard discipline throughout:
red→green, predicted suite counts, live decoys, claim lane, journal.
