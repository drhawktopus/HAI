# HANDOFF — Fellowship tiny-model tournament (#34 slice, Delegation epic)

**Written 2026-08-27 by the main session for a side session (Opus) that the owner will drive.**
This is the Delegation epic's tablet-side work: #34's registered "unblocked cheap first
move." #34 is REGISTERED-not-started; **the owner carrying this handoff to a session IS the
start of this slice** — record it (see Kickoff obligations) rather than asking again.

## The question

Can a current tiny LLM actually live on a Fellowship node? Concretely: does a
**MiniCPM5-1B Q4** (and one or two peers from the #34 roster) load and serve on a 1.76 GB
tablet node at a usable rate — or does the standing **"no LLM on tablets" doctrine**
survive contact? Either answer is a win: a fit gives the Delegation epic its cheapest
local tier (where #35's small adapters could eventually sit); a no-fit retires a
recurring "but could the tablets…" question with a measured number.

**Slice B (optional, same session, same grounding):** finish the **Voltron serve-many
bind** — on 2026-08-22 the persistent `llama-server` silently ran LOCAL on rigby,
ignoring the RPC backends (parked "while the main session uses the tablets"; they are
free now). Detail: `Journal\2026-08-22-claude-fellowship.md` + the Voltron paragraph in
PROJECT-BACKLOG §PROJECT #34.

## Read first, in this order

1. `Reference\OPERATING-BRIEF.md` — you are a side session; this is the standing brief.
2. PROJECT-BACKLOG §"PROJECT #34" — the scope, the model roster (confirmed current
   2026-08-25), and what's already proven vs open.
3. [[Tablet-Cluster-INDEX]] — the Fellowship front door (roster, addresses, ssh paths).
4. [[Fellowship-Crunch-Integration]] — how the tablets already do compute work (§10b has
   the BH-CPU baseline for comparisons).
5. [[Tablet-Performance-Benchmarks-Bus-Contention]] — per-node GFLOP/s and the bus
   contention findings; your numbers land beside these.
6. [[Lorex-Camera-Architecture-Fellowship-Deployment]] — **which nodes carry live camera
   duty.** This gates node choice (below).
7. [[Fellowship-Node-Provisioning]] — the documented hands-on path (Termux, ssh, what a
   node durably carries).

## Binding constraints (each one is paid-for case law — CLAUDE.md doctrine table)

- **Camera duty is LIVE on Fellowship nodes.** Do not co-locate the tournament with a
  node doing detection work without a controlled probe: run on an off-duty node, or
  measure detection latency N-off/N-on before calling co-location safe
  (`feedback-measure-before-colocating-workloads`). Never degrade camera coverage
  silently; anything touching a duty node's workload is the owner's call first.
- **DECLARED ≠ PRESENT ≠ VISIBLE.** On these tablets a `setsid` orphan is INVISIBLE to
  Termux's own `ps`/`pgrep` while alive and holding a port — enumerate over adb, kill
  over ssh; validate any "nothing is running" claim against a known positive. A started
  decoder/server is not a working one: only a delivered token/frame is.
- **One variable.** Same node, same model file, same context size across arms; a number
  taken during a WiFi re-association blip or beside camera duty is not a measurement.
- **Probe the SERVICE, not ping**; a failed probe means unreachable-by-that-method.
- **Verify current tooling** — llama.cpp builds already exist on the nodes from the
  distributed-7B work (08-22); check what's installed before building anything.
- **Disk is not free.** Tiny models are small, but stage downloads deliberately and note
  where they land; clean up decoys (one name = one artifact).
- **No factory-floor moves.** The tablet-foundry container (orchestrator side) runs
  camera jobs — never hand-kill its work. Hands-on Termux/ssh experimentation on an
  off-duty node is the documented, normal path.
- **The orchestrator deploy lane may be FROZEN** by the main session's in-flight
  commissions — this slice needs no orchestrator changes; do not make any.

## Deliverables (the tournament is a measurement — this is its order form)

- Per model × node arm: **loads? / serves? / tok/s (prompt + gen, n≥3 rounds with
  spread) / RAM headroom / thermal-throttle note**. Discard the first (load-inclusive)
  round; report ranges, not single numbers.
- The doctrine verdict, written where it binds: update the "no LLM on tablets" claim in
  the tablet docs to the measured truth (cite the arms), whichever way it goes.
- Numbers filed beside the existing benchmarks in
  [[Tablet-Performance-Benchmarks-Bus-Contention]] (same format), not a new orphan doc.
- Slice B if taken: the RPC bind either working (documented in the same journal thread)
  or the exact failure captured.

## Kickoff obligations (conventions, same pass — not optional)

- **Registry**: dated note on the #34 row — "Fellowship tournament slice ACTIVE
  <date> (side session)". The campaign row itself stays as-is; one slice ≠ the umbrella.
- **Charter**: #34's charter doc is owed at kickoff ([[PROJECT-REGISTRY]] convention).
  A thin `LLM-Project\34-CHARTER.md` stub (members, current phase = this slice, gate) is
  enough to discharge it; the owner approves the draft.
- **Journal**: side sessions own a suffixed journal (`YYYY-MM-DD-claude-fellowship.md`
  precedent) + a `[SIDE — Fellowship]` entry in CURRENT-PROJECTS at close.

## Explicitly out of scope

Orchestrator/desk code · the camera pipeline · #35 training work (Mothership-shaped,
gated on the ollama-pin/ROCm decision) · the 24 GB champion tournament (#34 area A, same
gate) · anything needing the deploy lane.
