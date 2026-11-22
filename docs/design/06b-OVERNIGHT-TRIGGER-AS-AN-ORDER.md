# 06b — The overnight self-engineering trigger becomes a STARFLEET ORDER (design proposal)

Status: **✅ SIGNED by the owner, 2026-08-15 morning ("Okay let's do that, I'll sign") — building.**
Author: main session, 2026-08-15.
Parent: [[06-SELF-ENGINEERING-AGENT-PLAN]] (Phase 7A/7B, shipped 2026-06-29). Supersedes-on-adoption:
the request-driven timer half of 7B. Related: `PROJECT-BACKLOG` "fire-in-cheapest-window" (~:2239).

## the owner's ruling (2026-08-15 morning, quotable)

> *"Does my description not imply that my original intent is that it gets handled by the
> scheduler? Seems like a no-brainer to me"* — placement in TIME belongs to fleet command the
> same way placement in SPACE does.

And the load-bearing disclosure, same conversation:

> *"our energy gating system currently has a critical flaw that I keep putting off…every day the
> the utility fetch fails. So the data is probably wildly stale… It's mostly being done manually for
> now."*

## Today's shape, and what moves

Phase 7B built a bespoke timer INSIDE Kirk: `POST /self-eng/overnight/schedule` writes
`scheduled.json`, a 5-minute poll fires `overnight.should_fire` when `price <= ceiling`, one-shot,
self-consuming (`app.py:5023` "clear before launching so we never double-fire"). It works, but it
is Kirk timing itself — a second, private answer to a question the desk already owns for factory
work (the trough doctrine).

**The two order layers (the owner, 2026-08-15, ruling): STARFLEET COMMAND at the top; the individual
factories' desks below it.** This order lives at the TOP layer. Division: **Starfleet Command
owns WHEN** (the order + its window gate, at the Kirk/Spock layer) · SPOCK owns WHERE (unchanged —
every call an overnight run makes already places through Spock) · KIRK owns RUNNING (unchanged —
`overnight_worker`, all safety semantics intact). Factory desks are involved only if the run
itself submits factory work, which then flows down normally.

## The order

* **Kind:** `selfeng.overnight` — a STARFLEET-COMMAND-layer order (precedent: the foundry runs
  `mode: probe` orders about itself; Fleet Command scheduling its own maintenance is the same
  shape at the top layer).
* **Fields:** `max_seconds` · `dry_run` · exactly one of:
  * `window: {start, end}` — **operator-supplied, first-class** (formalizes what the owner does
    manually today; works NOW, before the the utility fix);
  * `window_mode: cheapest_4h` — computed from `/self-eng/energy`, **permitted only when the
    price data passes the freshness invariant below.**
* **Dispatch:** when the window arrives and gates pass, Starfleet Command's order layer fires
  Kirk's EXISTING `POST /self-eng/overnight/start` — the endpoint keeps every safety property it
  has (sandbox-only enforcement, `backlog.yaml` arming HUMAN-ONLY and kernel-enforced ro, DM
  notify, run-state archive). The RUN half never times itself; the ORDER half never
  self-engineers.
* **Lifecycle:** standard order semantics at the top layer (queue, cancel/control, retirement,
  timestamped archive — same contract the factory desks honor below). One-shot per order;
  "recurring" = the owner queues another order, or later commissions a standing re-queue rule — NOT
  built here.

## ⛔ The freshness invariant (the the utility caveat, designed-in) — COVERAGE, not age

**The feed's cadence (the owner, 2026-08-15): the utility publishes ONCE daily, ~6pm Central, day-ahead
pricing for the upcoming day** (text message; *"I think"* the website updates at the same time —
certainty self-declared, so the build VERIFIES what timestamp/coverage the fetch actually
returns before trusting this detail). A naive fetched-within-N-hours bound is therefore the
WRONG SHAPE — a 6h timer would invalidate perfectly good day-ahead data by midnight (his catch
on the first draft of this section).

**The invariant:** a computed window may be used only if the price dataset's COVERAGE INTERVAL
contains the proposed window — the check matches the claim's shape ("this window is cheap"
requires data ABOUT that window, not data that is merely recent). Practical ceiling falls out of
the cadence (~30h from publication, stated as derived-from-cadence in the config comment, not a
magic number). No coverage ⇒ the order REFUSES to auto-fire, loudly, naming the the utility
dependency — never a decision on data that doesn't cover the question (the frozen-telemetry
class, coverage costume). Operator-supplied windows bypass the computed path entirely and are
the expected mode until the fetch is fixed. **The the utility fetch repair itself is the owner's parked
item and is a named dependency here, not part of this build.**

## Migration + consumer enumeration

`/self-eng/overnight/schedule` + the 5-min poll are DEPRECATED on cutover (the `/start` endpoint
stays as the desk's dispatch target). Consumers to sweep before retiring the timer: the poll task,
`scheduled.json` readers, [[RUNBOOK-HAI-Fixture-Test-Cycle]] (operator procedure gains the
order-submission step), `06-…` Phase 7B section (cross-referenced, not rewritten), the homepage
tile if it reads `scheduled.json`. Grep again next day per migration doctrine.

## What this does NOT change

Arming stays human-only (kernel-enforced) · sandbox-only for unattended runs · Spock placement ·
the run's own budget/bail semantics · nothing recurring exists unless separately commissioned.

## ~~Open question~~ RESOLVED — and the answer was ALREADY IN THE RUNBOOK

the owner restated it in chat ("two order layers, starfleet command at the top, then the individual
factories' desks") and then pointed out, correctly, that this is documented: **[[RUNBOOK-Compute-and-Training-Order-Submission]]** — *"the fleet order taker IS the orchestrator +
scheduler. It already exists… must not be rebuilt as one"* (:842) · *"Fleet level decides WHO
builds; factory level builds"* (:845-848) · vocabulary verbatim at :838-840. Also
[[P20-MULTI-FACTORY-FLEET]] §opening and[[Fellowship-Order-Taker-Design]] §1.

**So `selfeng.overnight` is a new KIND at the EXISTING fleet order desk** (`POST /p20/order`,
tested by `test_fleet_desk.py`'s 21 pins) — not a new surface, not "Kirk's order layer"
abstractly. Two build notes straight from the documented traps:
* This kind is never pulled by a factory — its dispatch target is Kirk's own
  `/self-eng/overnight/start` at the window (the desk already holds orders and releases chained
  steps on receipts, :827-831; a TIME-window hold is a sibling of the `after_ids` hold).
* ⚠ The fleet store is shared with render jobs and the render worker's `RENDER_KINDS` allow-list
  silently skips unknown kinds (:889-895) — correct failure direction, but check there first if
  a `selfeng.overnight` order ever "never runs"; and specs stage as `.incoming` + `os.replace`
  (:897-899), never written in place.

## Build shape (after sign-off)

Desk-side order kind + dispatcher (crib the probe-order lifecycle) · freshness check in the
energy module (one function + tests) · operator-window field · deprecation sweep. Standard
gates, red→green, live decoy = a dry-run order through the full path in a manual window.
