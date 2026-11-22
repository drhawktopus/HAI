# The fleet is MULTI-FACTORY — same shape per machine, one scheduler above them

**Built 2026-08-02** (Claude side session, at the owner's direction). **Status: ✅ LIVE — THREE GPU
factories running** (refreshed 2026-08-13; the original note said two), per-factory homepage tiles,
host-prefixed alerts. Live capability lists are the deck's job, not this file's:
`GET /p20/factory/{host}` (`mothership` · `bighonker` · `softserve` · `fellowship`).

the owner's model, in his words: *"a total of three same shaped factories. One per machine."* Plus a
fleet scheduler above them: *"When we're submitting orders to the fleet, it should be going to the
big scheduler overall; if I say to submit an order to a machine, then it goes to the per factory
scheduler."*

| | as of 2026-08-13 (live truth = the deck, not this row) |
|---|---|
| Mothership | full factory — train · probe · caption · render (flux2) · bake.klein · root `F:\foundry` |
| BigHonker | factory as of 2026-08-02 — caption · render (klein) · compute orders · root `F:\foundry` |
| Softserve | **factory since ~2026-08-03** — caption · compute orders · root **`E:\foundry`** (not F:) — runner task `SS Foundry Runner`, suite needs `FOUNDRY_ROOT=E:\foundry` ([[RUNBOOK-GPU-Police-Operation]]) |
| Fellowship | the tablet cluster under Tolkien — a fleet placement target with its own tile, not a GPU factory (see [[RUNBOOK#Tablet compute cluster]]) |

---

## 1. The rule that made this cheap: DON'T BUILD A SECOND FACTORY

[[P20-FOUNDRY-RUNNER-PLAN#Do not build a second factory]] already said it, and
`foundry_paths.py` already provided the lever:

> *"`FOUNDRY_ROOT` is an ENV VAR with a default. That is what makes blue/green possible at all — the
> same code runs either factory depending on one variable, so green is not a fork that drifts from
> blue and then has to be reconciled."*

That was written for blue/green on ONE box. **It is the identical lever for a SECOND BOX.** So
BigHonker runs the *same* `p20_foundry.py`, `p20_render_worker.py`, `p20_status.py` and
`foundry_paths.py` — hash-verified against Mothership — and every difference is an env var.

**Same shape by construction, not by discipline.** A parallel implementation would drift; this
cannot, because it is the same file.

### What "same shaped" means (the owner's clarification)
> *"It needs to be able to do the same capabilities, but the specifics that are specific to any one
> individual card are obviously able to be changed. So the guards, the tests, etc. need to be
> adjusted so that they will be applicable to the machine that it's running on but they all need to
> be similarly shaped."*

**The contract is identical everywhere; the implementation behind it varies by host.**

Contract every factory implements — this is the part that must never differ:
* accept an order (spec file in a queue dir)
* **claim the lane** so the fleet knows *that* card is busy
* **publish a card + heartbeat** on a fixed cadence
* **resume idempotently** (sidecar as the key)
* honour **pause** and **priority**
* **retire** the order and release the claim by *any* exit path
* failures **COUNTED**, never silently written as success
* patrol the shop floor; hold orders whose `requires` it cannot satisfy

Per-host: which modes it runs, which guards apply, what the thresholds are.

---

## 2. What had to change to make one file serve two hosts

All defaults unchanged — Mothership behaves exactly as before unless the env vars are set.

| change | why it mattered |
|---|---|
| foundry-owned paths derive from `foundry_paths` (`P.STATE_DIR`, `P.BIN`, `P.CACHE_DIR`, `P.ETC`, `P.QUEUE`, `P.JOB_ARCHIVE`, `P.TELEMETRY_DIR`) rather than `F:\foundry\...` literals | the literals **silently defeated `FOUNDRY_ROOT`** — a second factory would have written its lock, heartbeat and STOP flags into the FIRST factory's tree, i.e. the two-order-takers-one-lane collision the layout doc calls the hard rule |
| `FLEET_HOST` → `FOUNDRY_FLEET_HOST` env var | was hardcoded `"mothership"` with the comment *"the foundry only ever runs here"*. A wrong value **claims another box's card in the fleet's own state**, so the scheduler would place work on a GPU it believed free |
| external tool paths env-overridable (`FOUNDRY_PY`, `FOUNDRY_LORAS`, `FOUNDRY_COMFY`, `FOUNDRY_COMFY_LAUNCH`) | a host with no ai-toolkit/ComfyUI must still **import** the module to run caption orders |
| `FACTORY_CAPABILITIES` per-host via `_HOST_CAPS_OMIT` | see §3 |
| `p20_status.STATUS_DIR` derives from `foundry_paths` | hardcoded, every host wrote to ONE board and one factory's card landed under another's identity |

---

## 3. Capabilities are a PROMISE, and they are per-host

`FACTORY_CAPABILITIES` already carried the right idea — *"a capability is named here ONLY when the
code behind it is deployed; this registry is a PROMISE, and the whole point is that it cannot be
hand-waved."* It just assumed one factory.

BigHonker declares **19** capabilities (all `caption.*`, `order.*`, `lane.pause`, `lane.yield`) and
does **not** claim `train` or `probe`. An order requiring those is **HELD with the missing
capability named** — not failed, not archived. That is the correct, self-documenting outcome and it
is what makes "then build the capability" a real workflow.

⚠ **Absence is a DEPLOYMENT fact, not a verdict on the hardware.** the owner, 2026-08-02: *"don't sell
it short, just because I don't have it as my render host today doesn't mean I won't do it in the
future."* A 3070 is a perfectly capable render card. So `WORKER_CAN_RENDER` **auto-detects** from
whether the ComfyUI launcher exists rather than hardcoding a hostname — the day the toolchain lands
on BigHonker its worker starts claiming render jobs with no code change and nothing to remember.

---

## 4. ⚠ THE BUG CLASS THIS EXPOSED: single-writer stores

**Three stores in this lab were written by exactly one process for their whole lives, so none
needed a host key. All three stopped being single-writer the moment a second factory existed, and
NONE of them errored.**

| store | what happened |
|---|---|
| `_gpu_status.json` | BigHonker's worker posted `state: free` every 30 s and overwrote Mothership's card status |
| `rescue-state.json` | BigHonker's health daemon overwrote Mothership's daemon verdict |
| `p20_status` STATUS_DIR | would have put one factory's component cards under the other's identity |

Caught within the hour, and **only because both boxes happened to be idle**. With Mothership
mid-run the homepage would have confidently reported an idle card during a live job — the exact
"status board lies" class those files' own comments were written about.

**The generalisable lesson: when adding a second producer, audit what the first one WROTE, not just
whether the code is portable.** Portability was checked; the shared destination was not.

Fixes: per-host files (`_gpu_status_<host>.json`, `rescue-state-<host>.json`), legacy path kept and
written **only by a host that cannot name itself**, so rollout is one host at a time rather than a
flag day.

---

## 5. Fleet-facing surface (orchestrator, deployed)

| endpoint | purpose |
|---|---|
| `GET /p20/factories` | fleet view — `{factories: {host: {card, run, health, summary}}}` |
| `GET /p20/factory/{host}` | **flat**, one object per factory, what a homepage tile reads |
| `GET /p20/training` | unchanged shape; now asks for `host="mothership"` with legacy fallback |
| `GET /p20/rescue` | unchanged shape; now shares `_rescue_verdict()` with the tiles |

`_rescue_verdict()` was **extracted** so the per-factory tiles and the original rescue card cannot
disagree about whether a daemon is alive. A second similar-looking derivation is how two views of
one fact start drifting.

Homepage: **Foundry - Mothership** and **Foundry - BigHonker** tiles, each on its own
`/p20/factory/<host>` URL — so a tile structurally cannot describe another box.

⚠ **`/fleet/rescue-state` has a CONTRACT** (`RescueStateReq`): `host, alive, pid, disabled, last,
last_tflops, stalls_today, rescues_today, watching`. There is no `state` or `detail`. Posting a
nicer-looking shape means pydantic drops it and a healthy daemon reads `unknown` — that happened,
and the fix was to use the contract that already existed.

---

## 6. Discord: the factory host leads every alert

the owner: *"I need for it to be differentiated by having the factory Host at the beginning."*

```
[mothership] [foundry:<order>] <message>
[bighonker]  [foundry:desk] <message>
[bighonker]  [health] ALERT <message>
```

Defined once in `_host_tag()` so the format cannot drift between components.
⚠ Image DMs keep the host on the SECOND line — leading with a machine identifier ate the first and
most-read line on a phone (measured, 2026-07-27).
⚠ Health alerts are **edge-triggered** (on transition, repeat hourly, one RECOVERED on exit). A
20 s poll on a persistent condition would send ~180 DMs/hour and train the reader to ignore them.

---

## 7. BigHonker's daemon: same role, different duties

Mothership's stall-rescue hunts an AMD wedge and fires rescue bursts — none of which applies to a
3070. BigHonker's `bh_health_daemon.py` keeps the SHAPE (heartbeat at the path PREFLIGHT checks,
status card, log, STOP flag, fleet report, reads the STATED run) and changes the DUTIES:

1. **clock-lock verification** — the highest-value duty on that box; `-lgc` does not survive a
   reboot and a driver install resets it (see the 3070 investigation). Re-applies, bounded, reports.
2. **GPU-lost detection** — nvidia-smi is the only source that knows.
3. **frozen-telemetry detection** — identical readings mean a dead card, however healthy they look.
4. **caption-progress stall** — sidecars should keep appearing while an order owns the lane.

It never reboots and never kills the order taker: a lost GPU needs a reboot, and that is an
operator decision.

---

## 8. Open / next

* ~~**Softserve is NOT a factory yet**~~ **SUPERSEDED (2026-08-13): Softserve IS a factory** —
  live by 2026-08-03 with the same `p20_foundry.py` at root `E:\foundry`, its own runner task
  (`SS Foundry Runner`), health daemon (`SS Foundry Health`) and patrol. The original bullet's
  argument won.
  ⚠ Standing constraint UNCHANGED: Softserve is the lab's main server AND the host Claude's
  session runs on, so crash-class testing there **requires the owner home and ready**.
* **Degraded mode:** factories should keep draining their LOCAL queue when the orchestrator is
  unreachable. Fleet-wide placement needs Sundae; already-placed work should not.
* **Capability-aware dispatch:** `scheduler.place()` should read `FACTORY_CAPABILITIES` (and which
  models fit which card) rather than hardcoded policy. JoyCaption Q8 is 8.82 GB and does not fit an
  8 GB card — that is a placement fact, not a rule to remember.
* **Mothership pickup:** see [[HANDOFF-mothership-factory-fleet-changes]].
