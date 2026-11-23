# P20 Foundry Runner — Plan (written 2026-07-21, build AFTER TestFemaleR + Avery v2 runs)

**Goal:** the identity-model "foundry" (train → checkpoint previews → finish ladder → register → restore)
runs as a **resident of Mothership**, with no dependency on a live Claude session. End state: HAI's
orchestrator queues a job spec and the foundry does the rest, notifying humans via Discord.

**Why (observed 2026-07-21):** today's TestFemaleR run was choreographed by ~46 session-bound background tasks —
watchers, monitors, the rescue daemon's keep-alive ssh, every pause/render/resume. It worked, but the rescue
daemon dies with the session (Windows sshd job-object teardown kills `Start-Process` children), monitors die
with the session, and nothing resumes choreography after a Claude restart. The pipeline's brain must move
on-box.

---

## The operating model: a job spec is a PRODUCT ORDER

the owner, 2026-07-26: *"I'm thinking of them as product orders that a factory fulfills, makes according
to the specifications and desires, and then ships and cleans up."*

**This is the model. Anything that runs work on a card goes through it — no parallel launchers.**

| stage | owner | answers |
|---|---|---|
| **ORDER** | the job spec (`<name>.job.json`) | specifications AND desires: what to make, and how the human wants to be told |
| **FULFILMENT** | `p20_foundry.py` | reads the order and runs the STANDARD line: `wait_card_quiet` → `wait_card_healthy` → schtasks launch → `publish_current_run` → step watch → `STOP_NOW` handshake |
| **SHIPPING** | the order's declared deliverables | what actually leaves the building |
| **CLEANUP** | retirement | staged copies retired, `CURRENT_RUN` cleared, byproducts archived — the line is clear for the next order |

### PRODUCT vs BYPRODUCT — declare it in the order

A **training** order ships **weights**; its renders are byproducts.
A **benchmark** order ships **a number**; its weights are byproducts.

Nothing used to say which was which, and the cost was concrete: 37 staged LoRA copies (8 GB, up to
nine days old) accumulated in ComfyUI's SHARED `models\loras`, because `render()` stages a copy and
nothing retired it. Orders now declare `deliverables`, `ships_what` and `cleanup`.

**An order whose deliverable lives OUTSIDE the order is not self-contained.** The 2026-07-26 rank
sweep kept its measurements in the driver process; when the driver died, the product vanished — twice.

### Do not build a second factory

Every failure in that sweep was a re-derivation of something the foundry already does: launching
without `card_quiet`/`card_healthy` (arms measured a poisoned card at 100 s/it against a documented
6.47 bs2 best), never publishing `CURRENT_RUN` (so the telemetry logger kept following a stopped run,
`step` stayed empty, and the rescue daemon could not confirm a stall — journal 2026-07-26 #5), and a
watchdog that could not tell "booting" from "stuck" (killed two arms seconds before their first step).

**If work needs to run on a card, write an ORDER and hand it to the foundry.** A benchmark is an order
too — see `jobs\bench_rank{16,24,32}.job.json`, which carry `kind: benchmark`, `varies`,
`held_constant`, `measures`, `question`, `baselines` and `invalid_if`, so an operator or HAI can read
what is being made and when the result must be thrown away.

---

## The caching shape — cache by WHAT IT IS, record what made it, gate on that record

Caching is not an optimisation in this factory, it is why a 32B model fits on a 24 GB card at all
(`flux2-dev-uint4.pt` 17.8 GB, `mistral-uint4.pt` 14.2 GB, `_latent_cache`, `_t_e_cache`,
`cond_cache`, `p20_embeds_<trigger>.pt`). It is load-bearing, so it has a standard shape. Every rule
below was learned by breaking it on 2026-07-26.

**1. Content-addressed, never run-addressed.** `p20_embeds_{trigger}.pt` is keyed by the TRIGGER,
`flux2-dev-uint4.pt` by the MODEL — not by the run that happened to create them. That is what makes
reuse across runs legitimate instead of lucky.

**2. Validity must be PROVABLE.** `cond_cache` was keyed by name alone with no record of the prompt it
was baked from, so `bake_missing()` could only ask "does a file exist?". v2's previews silently
rendered on conditioning baked 21 h earlier for v1 and nothing anywhere could detect it. Fix:
`p20_bake_cond.py` writes a `<name>.txt` sidecar with the exact prompt; the cache carries its own
inputs.

**3. Unverifiable is treated as STALE.** No sidecar ⇒ re-bake. The asymmetry sets the default: a wrong
cached artifact costs a whole run, a re-bake costs a minute.

**4. The gate keys on the cache's OWN conditions and nothing incidental.** `P20_RESUME_MODE` required
`has_ckpts and has_embeds`, but the thing it guards is text-encoder work whose validity depends on
`has_embeds and has_mist` — `has_mist` was computed and never used. FIX-07 had made the embeds cache
global and trigger-keyed; the gate was never widened to match, so every fresh run paid a 14 GB Mistral
load + a 67 s GPU move to rebuild embeddings already on disk (~2 min per cold boot). **When a cache is
upgraded, re-read its gate.**

**5. A miss must say WHY.** `absent` / `TEXT CHANGED since bake` / `no text record (pre-2026-07-26)` —
never silence. A silent miss is indistinguishable from a silent wrong hit.

**One line:** cache by what the artifact IS, record what produced it, gate on that record, fail toward
recompute, and let the factory retire what it owns.

---

## Component 1 — Rescue daemon as a Windows scheduled task — ✅ DONE 2026-07-21 16:31 (task "P20 Stall Rescue", SYSTEM/AtStartup/no-limit/restart×3; session-daemon retired via STOP flag; v4.1 boot-fix = clean handover)
- `schtasks /create`: SYSTEM, AtStartup, no execution time limit, restart-on-failure ×3 — mirror the proven
  "GPU Telemetry Logger" task config.
- Action: `F:\ai-toolkit\venv\python.exe -u F:\ai-toolkit\p20_stall_rescue.py 15`, stdout → append a log file
  (wrap in a .cmd that redirects, or add file logging — the daemon already tees to `stall_rescue.log`).
- Keep the `STOP_RESCUE` flag as the pause mechanism (task keeps restarting? NO — set restart policy so an
  intentional clean exit (code 0) does NOT restart; only crashes do. Verify schtasks semantics; may need a
  wrapper that exits nonzero only on crash).
- Retire the persistent-ssh launch pattern from the runbooks once converted.
- Est: ~30 min + a stall-cycle verification.

## Component 2 — `p20_foundry.py` run orchestrator — 🔄 v0.1 BUILT + DEPLOYED 2026-07-21 (state machine PREFLIGHT→LAUNCH→WATCH→PAUSE/RENDER/RESUME→LADDER, idempotent state file, shadow mode; REGISTER/RESTORE/Discord = ✅ v0.2 BUILT+DEPLOYED 2026-07-22 00:35, elevated schtasks-hosted, all child launches via one-shot schtasks; see journal 07-21 post-midnight for the /rl HIGHEST + job-object gotchas). First shadow: avery_flux2_v2.job.yaml (resident_server experiment in-spec). Runner itself still session-hosted for the supervised shadow — schedule-task it in v0.2.
Single resident script (Mothership, ai-toolkit venv, stdlib-only like the drivers) executing a **job spec**:


**State machine:** `PREFLIGHT → LAUNCH → TRAIN_WATCH → (PAUSE → RENDER → RESUME)* → FINISH → LADDER →
REGISTER → RESTORE → DONE` — each state idempotent + persisted to a state file so a crashed/rebooted runner
RESUMES choreography (the property today's setup lacks entirely).

Encodes today's proven primitives + every lesson:
- **PREFLIGHT:** disable `ComfyUI Server` + `HAI Ollama Watchdog` tasks, stop Ollama procs, keep-awake.hold
  (+N h), verify Defender exclusions + telemetry logger alive. (NO reboot — obsolete per 07-20/21.)
- **TRAIN_WATCH:** poll the checkpoint **files** (mtime/names — never log-line greps, they scroll), poll the
  bar for step/rate; heartbeat to the status log.
- **PAUSE:** `type nul > F:\foundry\var\state\STOP_NOW` (green path since 2026-07-27) → wait for trainer exit (process gone) → identify exit-save
  step from the newest `_0000NNNNN.safetensors`.
- **RENDER:** `comfy_launch.bat` → wait `/system_stats` (do/until, not try-as-expression) → stage adapter to
  loras → `p20_preview_render_slim.py` per prompt (bake missing .cond files first via `p20_bake_cond.py` —
  one Mistral load amortizes all bakes) → kill by port-owner PID.
- **RESUME:** relaunch launcher (auto-resumes from exact step; rescue daemon covers the restart stall).
- **LADDER:** at completion render every banked ckpt (same seed/prompt/guidance 2.5) into a labeled
  contact sheet (PIL, the session's sheet code).
- **REGISTER:** `person_record.add_model` + `model_tier` — NOTE: person-records.json lives on Z:
  (Softserve share). Decision needed: (a) Mothership writes via UNC `\\SOFTSERVE\...` mapped path, or
  (b) runner drops a `registration-pending.json` and a Softserve-side hook (or HAI) applies it. (b) is
  cleaner for permissions; leans on the existing mirror-deploy doctrine.
- **RESTORE:** re-enable tasks, restart Ollama, clear hold — the checklist steps 2–5 inverse.
- Est: ~1–2 focused sessions incl. shadow-mode testing.

## Component 3 — Notifications (Discord) — ✅ v0.2: DM-to-the owner via hawkadminbot relay (Caddy route hawkadmin.lan, creds F:\foundry\etc\dm_relay.env, initiator:direct gate; hai-initiated runs report in HAI chat instead). Attachments: /dm-file multipart endpoint live since 2026-07-22 (8MB cap) — used by foundry previews + the CorridorKey offload kit (`F:\CorridorKey\ck_notify.py`, both boxes).
- Reuse the Watchtower-style webhook: milestone messages (launch, each preview, stall-rescue summaries,
  completion) + **attach preview PNGs** (webhook multipart) so subjects see their model progress live.
- Digest cadence replaced by event-driven posts + a compact end-of-run report (steps, wall-clock, rescues,
  rate stats from telemetry CSV).
- Est: ~1 h.

## Test plan
1. Component 1 alone (daemon task): kill session mid-run → daemon survives; stall-cycle verified.
2. Foundry **shadow mode** on the next training run (person #5 or a Avery v2 re-run): foundry drives, Claude
   only watches + compares against today's manual transcript.
3. First solo run: Claude reads the Discord trail afterward, audits the state log.
4. Only then: HAI queueing (Phase 3).

## Gotchas the implementation MUST encode (all hit live 2026-07-21)
**⚠ NEW (22:0x): STOP_NOW silently NO-OPS if consumed at/near the resume step** — flag dropped during a
load phase was eaten at ~start_step+1 with NO save and NO exit (likely a save-block guard skipping saves at
the just-resumed step). Working theory only — READ the BaseSDTrainProcess save-block before trusting.
Foundry rule until fixed: never drop STOP_NOW during load; wait until the run is ≥3 steps past resume, and
ALWAYS verify exit actually happened (process-gone + new exit-save file) rather than assuming.
sshd job-object teardown (no `Start-Process` children from ssh) · STOP_NOW/SAVE_NOW flags exist — never
kill the trainer blind · watch checkpoint FILES not log lines · PowerShell `try{}` is not an expression
(do/until) · burst-echo guard + dud spawns (daemon v4.1 handles) · renders stall too (v4.1 render-mode) ·
ComfyUI :8189 is launch-on-demand, :8188 is the task-managed #19 instance · conditioning bakes are
per-(prompt, TE) · in-session node caching = batch renders per pause · `.cond`/slim graph = pixel-identical
(validated) · registration writes are cross-machine (Z:) · restore checklist steps 2–5 (reboot obsolete).

**Sequencing:** after TestFemaleR completes + Avery v2 trains under the current (session-driven) setup →
Component 1 → Component 2 (+3) with shadow-mode test → Component 4 waits for Phase 1 enforcement.
