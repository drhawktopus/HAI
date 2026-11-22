# HAI Autonomy Gaps — observed during the Sméagol tablet intake (2026-09-03 case study, epic backlog)

A live case study for the HAI-independence epic ([[10-HAI-INDEPENDENCE-PLAN]]). HAI was handed a real
end-to-end job — drive the Sméagol (Samsung SM-T560NU) tablet intake — using the freshly built
`host_admin` SSH capability. It got **most of the way** and then stalled on a small number of concrete,
recurring capability gaps. Each is an epic item: close it in HAI and the next drive needs no human wrench.

## What HAI did WELL (unassisted, to be preserved)
Installed Heimdall on Rigby; downloaded LineageOS 18.1 + TWRP recovery with **real, verified sha256 hashes**
(no placeholders — its own requirement, met); added a correct SM-T560NU/gtelwifiue profile; confirmed the
device in Download Mode; and **correctly self-diagnosed** its own blocker. The data/artifact prep was sound.

## The gaps (ranked by how much they cost)

### G1 — No cross-host FILE DEPLOY primitive (the direct blocker)
`host_admin_run/exec` run a *command on a host*. HAI had no way to **stage a file** — specifically its
583-line `tablet_intake.py` runner — from its Sundae workspace **to Rigby**. It could edit small data
(`profiles.json`) with inline `python -c` over `host_admin_exec`, but not deploy a whole source file. So it
knew exactly what it needed to do ("replace Rigby's Lenovo-only runner with the Samsung-capable version")
and had no mechanism to do it. **Fix:** a `host_admin_put(host, local_path|content, dest)` affordance
(broker-mediated, same auth/confirm model), or a git-based deploy lane for the tablet-intake runner.

### G2 — HAI's own coding-runner work products are not durably retained
HAI had earlier built the Samsung support "to the pre-flash boundary" (task `46d1aaf1…`, worktree
`wt-46d1…`, commit `dd1977c`, checkpoint `bd9c2756`). By the time it went to deploy that code, **the
worktree was gone, the commit was not a valid git object, and the checkpoint file did not exist** — its own
`coding_search` for `_execute_samsung` returned nothing. HAI was hunting for code it had written and lost.
**Fix:** completed coding tasks must persist their diff/commit to a durable ref (tag/branch that survives
worktree cleanup and gc), and a checkpoint must be rehydratable. A "done to checkpoint" task whose code
evaporates is worse than not done — it produces false confidence.

### G3 — Long-drive CONTEXT SATURATION (the thrash multiplier)
The drive ran 22+ turns / 37 tools; the Mothership 27B hit **~32.7k prompt tokens, timed out, was
context-downgraded to 32k (measurements marked VOID), and truncated answers** (`done_reason='length'`). It
began re-reading the same files and re-issuing `host_admin_run` in loops. **Fix:** long procedural drives
need sub-tasking / checkpoint-and-summarize so the working set stays small; a single monolithic drive of a
multi-stage physical procedure will always saturate. Relates to the tool-schema saturation gotcha but here
it is drive-length, not schema count.

### G4 — No clean REMOTE FILE-WRITE / EDIT primitive
Editing `profiles.json` via multi-line `python -c` heredocs through `host_admin_exec` worked but is
fragile — it introduced a text artifact (`"Rej ect"` with a stray space) and does not scale past tiny
edits. **Fix:** a structured remote write/patch primitive (write file, apply JSON merge) rather than
smuggling code through a command string. Overlaps G1.

### G5 — The tablet-intake runner is an ORPHAN artifact
`tablet_intake.py` lives as a standalone file on Rigby, decoupled from any repo HAI builds/deploys through
its normal self-engineering flow. So HAI's "add Samsung support" work had **nowhere to land** — it built
against a copy in its orchestrator worktree that never reaches Rigby. **Fix:** version-control the
tablet-intake runner and give it a deploy lane (like the orchestrator's canary→promote), so a code change
flows build→test→deploy the same way HAI already ships orchestrator code.

### G6 — HAI cannot bootstrap its own execution primitives (the meta-gap)
The reason Claude built `host_admin` at all: HAI's coding lane can write code but had **no shell/SSH
primitive**, so it could not enroll a key, install a package, or operate a host — it could not build its own
hands. **Fix (directional):** this is the Project-35 core — HAI needs a governed way to provision new
capabilities for itself (the "internal capability transition" the owner named), so a missing primitive triggers
self-provisioning under review rather than escalation to Claude/Codex.

### G7 — Recognize "needs a human at the device" instead of thrashing
The destructive flash (Download→TWRP transition, TWRP GUI) is inherently human-in-loop for a new device
family. HAI kept trying to progress rather than surfacing "this step needs a human + here is the exact
ask." **Fix:** a first-class "human physical action required, here is the checklist, I will resume on your
signal" state (the runner has `NeedsHuman`; HAI's drive loop should mirror it).

## What Claude had to do this session (the human/wrench interventions to eliminate)
1. **Build the `host_admin` SSH capability** (G6) — HAI could not bootstrap its own shell primitive.
2. **Rebuild the Samsung runner code** (G2) — HAI's was lost/unrecoverable.
3. **Stage it to Rigby** (G1) — HAI had no cross-host file-deploy.
4. **Prove `inspect_samsung` live** — non-destructive, confirmed device + PIT + hashes.

Closing G1, G2, and G3 would have let HAI finish the software side of this itself; only the physical flash
(G7) legitimately needs a human. Operational procedure: `RUNBOOK-Fellowship-Samsung-Heimdall-Intake.md`.
Capability built: `RUNBOOK-HAI-Host-Admin-SSH.md` / [[RUNBOOK-HAI-Host-Admin-SSH]].
