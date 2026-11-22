# Handoff to Claude — Project 34 / Project 37 — 2026-10-20 CT

Prepared by Codex at the owner's request: **“Make a detailed handoff for Claude.”**

## Read this first

- **Project 34 is active, in the combined Phase 7/8 integration and closure work.** Phases 0–6 are closed; phases 2/3 were re-closed October 18. Phase 7's placement rule is already built/live/self-refreshing. Phase 8 has measured results and live McCoy **shadow** wiring. Phase 9 has not opened.
- **Project 37 is completed.** Its streaming, account controls, quick-first/the owner-first slot, checkpoint/park/resume and live proofs exist. Its remaining handoffs are owned by #34. Do not start rebuilding #37 or routing.
- **Production and the candidate are different.** Canonical orchestrator is clean at `ed9643bd580ef0e122c83344ca746873cee0e458`. All core chat/factory integration below is **workspace-only, uncommitted, undeployed work for review**. “Staged” here does NOT mean Git's index. The current working directory itself is not a Git repository.
- Latest candidate gates: **foundry 579 tests, pin 579; orchestrator 3003 tests, exactly two expected skips, pin 3003/2.** These are unit/regression gates, not proof of live model handoff or campaign completion.
- **the owner challenged two assumptions and was right to demand evidence.** Codex withdrew a Strata ownership exception and removed a routing shortcut. Both mistakes and the remaining gaps are recorded below. Do not inherit earlier assurances as facts.
- **No integration deployment, new police exemption, stand-down flag, GPU/image test campaign, or replacement routing engine occurred.** The live orchestrator still has no `inference_orders.py` module.

The next action should be to read the current contracts and review the candidate against the actual existing implementation. Do not wholesale-copy the scratch directory into production.

## the owner's instructions and the reason behind the rules

the owner added this standing rule during the review:

> “If you want to change a rule, you must first understand why it was made that way.”

It is now in `E:\SSDShared\SSD\Open Access\Claude Home\AGENTS.md`. Read the supporting doctrine, runbooks, component docstrings and incident history before modifying a rule or guard. A rule is not an obstacle to remove to make a new integration work.

**One executing job per GPU card, one model/slot per box.** Overlapping workloads caused contention, stalls and failures, including the documented 2026-07-27 collision class. The GPU police are deliberate protection/discovery machinery. A matching ownership record is not proof that no other workload is running; a dead HTTP worker is not proof its persistent model engine stopped. Concurrency comes from different fleet boxes, not additional slots on one card.

Other instructions that remain active:

- Read the library before inventing mechanisms; reuse Kirk/Spock/Tolkien, the existing order desks, controls and factory lifecycle.
- Model work, **chat included**, is an order. Kirk owns tools/the work loop; McCoy advises; Spock deterministically places; Tolkien owns placement inside the Fellowship. No model chooses hardware routing.
- Honor user pins, measured per-model/per-box windows, Plex/user priority, availability, consent and energy gates. No arbitrary output/tool/context limits or generation clocks.
- No customer walking the factory floor: no manual runner kills, queue renames, `CURRENT_RUN` deletion or hand-placed work. Submit through the existing desk; the owner explicitly says when he wants the direct-to-factory route.
- Do not create `GPU_SHORE_LEAVE` or `GPU_NO_ARREST`. No session-side supervisor that stops/resubmits work, and no new watcher to substitute for factory machinery.
- Before modifying an existing rule, establish its purpose and failure mode, not just what the code presently does.
- the owner dislikes repeated permission requests and repeated promises without work. Continue within authorized scope; report real limitations accurately. Do not request preferences already recorded in the library.
- **Mock image-generation tests.** the owner explicitly said not to waste time generating many real images. No further images were generated for this integration audit.
- Do not edit another assistant's private memory. This handoff and the library/journal updates are shared project records.

## Authoritative documents, in order

All library paths are under `Z:\Documentation`.

1. `Reference\OPERATING-BRIEF.md` and the four `Reference\Doctrine-*` pages.
2. `LLM-Project\34-DECISIONS-AND-OPEN-ITEMS-REGISTER.md` — current pickup was replaced with the October 20 charter/phase alignment; prior pickup archived verbatim. Read this after compaction.
3. `LLM-Project\34-CHARTER.md` — especially the end state, hard constraints and October 18 re-closure decisions.
4. `LLM-Project\34-Phase-7-Placement-Policy-Design.md` — the ONE placement rule and existing placed pieces/windows.
5. `LLM-Project\HAI-Agent-Surface-Design.md` — October 19 decision that all model work is a factory order; its V3 chat-holder workflow is NOT a V2 default to build now.
6. `LLM-Project\37-Closeout.md` — shipped work, decisions and handoffs.
7. `LLM-Project\34-CHAT-FACTORY-CONTRACT-AUDIT.md` — current candidate audit, mistakes, evidence and open rows. This is a candid review, not a claim the integration is ready.
8. `Runbook\RUNBOOK-Compute-and-Training-Order-Submission.md` — factory-contract checklist, helper windows, Strata lifecycle, Stop, priority-consumer handback and typed outcomes.
9. `Runbook\RUNBOOK-Ollama-Host-Operations.md`, `RUNBOOK-GPU-Police-Operation.md`, `RUNBOOK-Foundry-and-Rescue-Daemons.md`, and `RUNBOOK-Orchestrator-Deploy-Procedure.md`.
10. `Reference\CURRENT-PROJECTS.md` and our own `Journal\2026-10-19-codex-p34.md`, `Journal\2026-10-20-codex-p34.md`.

Some older charter/registry/phase-plan paragraphs still say “not started” or quote superseded crowns/test counts. Use the later dated closure decisions and current checklist, not those older summaries. Do not infer that the working router is absent.

## Charter and phase position

| Phase / area | Current position |
|---|---|
| 0–1 groundwork/harness | Closed. |
| 2 heavyweight tournament | Re-closed October 18. Crown shared by family: Flash-Next for agentic/long-context lead work; qwen3.8:27b for quick families. |
| 3 smaller-box tournament | Re-closed October 18. BigHonker's A3B role prices are on the menu alongside qwen3:8b. The 3.8's slow BigHonker speed was measured; the owner waived remaining role cells. |
| 4–6 Fellowship/ensembles/Voltron | Closed. Parked ceiling/refinement work is not an unfinished phase or permission to restart grids. |
| 7 placement policy | Core concluded, built, live and self-refreshing. Priced menu → policy → Spock; placed GPU/Fellowship orders, Tolkien worker choice and handback exist. Combined 7/8 integration/acceptance checklist remains open. |
| 8 controller intelligence | Measurements and McCoy shadow wiring exist; active validated advice through production Auto and formal controller closeout are unfinished. |
| 9 generalize upward | Not opened; gated by Phase 7/8 closure. |
| #37 surface | Completed; remaining handoffs belong to #34. |

Do not reopen settled decisions: **B5 reviewer = no measured gain; B8 lead works alone; B9 existing between-turn handover = no new tool-wait mechanism.** C7 easy-code difficulty is a **DATA WATCH**, not a speculative classifier build. C5's older “84 labels only” statement must be read with the later replay's 106 measured cheapest-tier labels; that later data exists. B4b overflow and B11 sampler-RNG/MTP-drafter state remain open, as does resume/reload pricing. Greedy non-MTP rigby dump/continue was already proven lossless; do not redo or describe it as unbuilt.

Live McCoy configuration was checked: no `MCCOY_MODE` environment override; the running image defaults it to `shadow`. It observes Auto turns and logs advice; it is not active automatic routing.

## Locations and operating environment

| Item | Location |
|---|---|
| Working scratch/home | `E:\SSDShared\SSD\Open Access\Claude Home` |
| Candidate root (called STAGE below) | `E:\SSDShared\SSD\Open Access\Claude Home\.p34-chat-stage` |
| Canonical orchestrator Git repo | `E:\SSDShared\orchestrator-build` |
| Canonical GPU foundry mirror | `E:\SSDShared\mothership-build` |
| Canonical Tolkien mirror | `E:\SSDShared\tolkien-build` |
| Shared library | `Z:\Documentation` |
| Hard-bench/advisor source used this session | `Claude Home\phase5-prep\commission-artifacts\hard-bench` |
| Live orchestrator | `ssh sundae`, `/opt/arrstack/orchestrator`, container `orchestrator`, API `http://<sundae-address>:8099` |
| Mothership | `<mothership-address>`, factory `F:\foundry`, 7900 XTX |
| BigHonker | `<bighonker-address>`, factory `F:\foundry`, RTX 3070 |
| Softserve | `<softserve-address>`, factory `E:\foundry`, RTX 3070 Ti, Plex/load-bearing services first |

Windows remote-exec idiom: pipe a Python/PowerShell source FILE to `ssh <box> "python.exe -"` / the appropriate interpreter. Avoid quoting layers/heredoc backslash corruption. A filtered local token or sandbox-denied CIM/loopback read is not proof of a broken lab. Follow the brief's established elevated-context method and actual tool permissions; report tool-layer rejections rather than silently constructing alternate mechanisms.

Factory deployments use `fleet_deploy.py` and its source/workspace mappings, never hand-copy to the floor. Read `--check`/the runbook first. Check its shared-file table for coupled Tolkien changes. For orchestrator deployments follow the existing lane claim, drift/baseline, copy+hash, build, full pinned gate, park, recreate, runtime verification, workspace sync and release procedure. A restart does not rebuild the image. No deploy claim was acquired for this candidate; check lane status before any future deploy.

## What actually shipped before the candidate

These are production work already completed, not pending prototype features:


## Two mistakes that must not be reintroduced

### Withdrawn Strata guard exception

Codex originally described a proposed owner-matching exception as fixing a Strata problem. That framing was inaccurate. The existing launcher correctly refuses to start under a factory claim. The proposed migration made chat itself an order, and the shortcut attempted to allow a matching order/request/model/PID through that guard. Fourteen focused tests checked matching metadata, **not exclusive execution through Stop/cleanup/handoff**. the owner challenged this under the one-job-per-card rule.

The exception was never deployed. It was withdrawn; staged `strata_serve.py` and its tests were restored byte-for-byte to canonical. Preserve the guard. Withdrawn files are in `STAGE\withdrawn-strata-ownership-exception-20261020` and are **not deployment sources**. Live/staged baseline launcher SHA-256 is `0b9f6d89be9718630748bef15448a195ff6b40f475b13c9153db75b8762a8c1c`.

The replacement candidate uses the existing **order-owned `strata_arm` lifecycle**, with a child engine and release verification, instead of asking the daily llama-swap launcher to bypass a claimed card.

### Removed routing shortcut

The first candidate also added a `factory_facts` branch to `app.pick_brain` that immediately returned BigHonker's configured fast model/window. It skipped `brain_order()` and the existing per-host model/window choice. This was an actual integration mistake; earlier green tests did not cover that branch.

It has been removed. The original chooser is restored. A focused mocked test passes with the restored function and fails with the **exact withdrawn function**, because the latter never calls `brain_order`. Evidence: `withdrawn-fast-model-shortcut-20261020.json`, `routing-shortcut-control.log`, `routing-reuse-evidence.json`. Do not simplify away the existing chooser to make the transport easier.

## Candidate architecture — reviewable work, not an approved final design

Intent: one model turn executes as an ordinary compute order; Kirk retains tools, the live transcript and run ledger. Existing Spock admission/placement and on-box factory lifecycle stay authoritative. The factory contacts only its local model service and relays original response lines. No model HTTP fallback when placement/admission fails.

**Important prior art before accepting this design:**

- Canonical `helper_window.py` already owns warm model work as a real order, reads item files, preserves completed items and closes by owner status/CLOSE.
- `mothership-build\workspace\tbench\tb_relay.py` already gives Harbor an OpenAI endpoint backed by that model window. Its `_stream` explicitly sends the **whole completed answer as one SSE chunk**, not live token streaming.
- `tb_order.py` ties the lead window to its real desk order and closes it in a finally.
- Existing A1 `WindowDelegator` / `PlacedWindowDelegator` and the Inspect lab provider are also prior art. The relevant source is in the hard-bench tree and deployed workspace, not necessarily a root-level `a1_order.py` in this mirror.

There is a real streaming/lifecycle integration gap, but this does not prove that Codex's new relay/store is the smallest correct final implementation. Compare with those existing components and refactor/prune the candidate if appropriate. Do not add a second responsibility owner or accept the prototype merely because its tests pass.

### Orchestrator candidate files

Under `STAGE\model`:

| File | Changes |
|---|---|
| `inference_orders.py` (new) | Request/response artifacts inside existing `p20_queue.QUEUE_DIR\model-turns\<32hex RID>`, not another queue. Stable checkpoint/request fingerprint, real `/p20/order` intake, unique attempt names, model/engine/window requirements, priority mapping, exact claim revision, sequenced original-line journal, cancellation through existing controls. |
| `factory_inference.py` (new) | `httpx.Response`/byte-stream adapter over the durable journal. Measured contexts, saved pins, actual served host/order evidence, queue time separate from model silence, claim-change fencing and no direct fallback. |
| `p20_queue.py` | In-process claim/mark/cancel serialization and atomic non-JSON temporary publication. Existing store/cache/queue remains the owner. |
| `ollama_client.py` | Native chat/generate and supervised native helpers use the adapter; unchanged parsers/deltas/tools/checkpoint loop retained. Execution phases update accounting/watch on actual host. Waiting/preparing rows do not explain model GPU load. Existing off-scope collision checks still need review. |
| `app.py` | Authenticated `/p20/model-turn/{job_id}/request` and `/stream` endpoints; query context/pin/facts and served identity. Original brain chooser/slot/order intake restored/preserved. Physical availability belongs to real order admission. |

Protocol details worth preserving:

- Worker token plus exact claimed host/revision fences private request and stream operations. Bodies/prompts are not command-line arguments.
- Allowed current protocols: Ollama `/api/chat`, `/api/generate`; Strata `/v1/chat/completions`. Request model/window must agree with declared memory needs. No guessed context.
- Raw thinking/content/tool/terminal lines are kept without output clipping. 256 KiB batching is wire framing, not a total response cap; one large line stays whole.
- Byte cursor metadata is atomically committed; replay is idempotent; stale/foreign writers and conflicting sequences refuse. Uncommitted tails are not shown.
- User cancellation persists across restart/reroute and is not silently re-created. An old failed reader cannot stop a new claim. Unfinished attempts are not mixed with replacement output.
- `_intake` no longer grants `rush=True` to everyone; admin permission uses the existing queue contract. Review ordinary/background/system caller attribution as part of full consumer migration.
- Authorized force pins and ghost comparison's SAME-box requirement are carried. The new same-box pin tests do not establish that every auto reroute uses the actual box's maximum context.

### Factory candidate files

Under `STAGE\foundry`:

- **`infer_live.py`**: stdlib local HTTP worker. Fetches input using actual `FOUNDRY_ORDER_ID`, spools original response lines under `ORDER_OUT`, replays completed delivery without regeneration, validates actual protocol terminal, pulses claim/cancel/phase and closes the socket on Stop. A completed cache does not launch a model again.
- Its Strata `model_engine` calls existing `strata_arm` / verified install-cache helpers: occupied-port refusal, serving-plan/context validation, `enter(frontier=False)` for daily use, launch as its own child, actual `/props` check, existing engine-live declaration, then stop/reap by every normal exception route. The original daily launcher is untouched.
- **`p20_foundry.py`**: injects real order ID/spec path, collects controls, has factory-side engine-release verification before clearing a claim, and connects safe batch return to existing `LaneYield`. Uses existing `TrainerUnstoppable` typed fate to stop consuming a lane if shutdown is unproved; does not archive a healthy order as a process fault.
- For interrupted native requests: acknowledged existing unload channel + valid empty `/api/ps` + existing GPU-allocation release handshake (`gpu_spill.RELEASED_MB`, no invented threshold). Unknown/retained allocation holds. Completed turns keep warm cache. Positive `gpu_ok=False` keeps existing **UNAVAILABLE/hand-back** semantics only after acknowledged model shutdown; it does not invent healthy counters or excuse an unknown shutdown.
- For Strata: independently asks existing CIM process reader and configured serving port. A remaining process, held port or inconclusive/error read retains the lane. CIM query makes errors terminating instead of successful empty output. Installed engine image `strata.exe` was verified read-only under `F:\Strata045\Strata-0.1.45\engine`; CPU-only image-name decoy validates the query without loading Strata/GPU work.
- **`infer_ollama.py`**: existing batch payload, now with a completed-item cooperative boundary. Completed row/marker is banked and fsynced before returning the shared typed code; existing main finally handles Strata/meter cleanup. Resume reads its existing partial file and skips completed answers.
- **`test_foundry.py`**: existing monolithic gate, candidate pin **579** (530 baseline +49 new cases). New focused cases are assembled into it; no separate hidden production test gate is invented.

### What the batch yield connection does, and what already existed

Verified installed source on **all three boxes**:

1. Routing, #37 `_LeadSlot` / `YIELD_HOOK`, queue priority/FIFO, training `yield_at` and `LaneYield` handler already exist.
2. `p20_foundry.run(mode=compute)` returns to `run_compute_order` before the training state machine.
3. Installed `infer_ollama._run` saves partial rows but has no between-item yield. Running the new boundary fixture against the **unmodified installed-equivalent payload** finishes both mocked answers and fails the first-item-lending assertion, with no unrelated error.

Candidate connection:

- The same factory beat thread pulls orders/controls using the already-defined quick-intake cadence; status publication keeps its own cadence. No second runner/watchdog. It waits on `min(QUICK_POLL_S, COMPUTE_BEAT_S)` so a shorter configured/test status cadence remains honored.
- Payload reads the existing `queued_orders` / `order_priority` decision from the actual `FOUNDRY_ORDER_SPEC`, including existing HELD/dependency filtering. Only strictly higher priority triggers lending.
- Shared `COMPUTE_YIELD_RC=75` is accepted only from the **canonical bin path** of the resumable inference payload. A same-named script elsewhere cannot assert that fate.
- Parent confirms appropriate engine cleanup, clears the active claim, raises the **existing** `LaneYield`, and sends no completion/failure receipt, shipment or retirement. Operator Stop/host-unavailable outcomes retain precedence.
- Existing queue sort runs higher priority first, then resumes the original batch from its partial rows. Last completed item finalizes normally instead of yielding an already finished product. Legacy Strata input without a new explicit context argument is not rejected solely by the release check.

This is a narrow connection to existing policy, not a new router or a tool-wait lending design. **Live factory handoff is still unproven.** B9's “no new mechanism during tool waits” decision stands.

## Open contracts and honest next work

Read the audit before choosing order. These are not all “new builds”:

1. **Remaining direct model consumers:** McCoy `/v1/systemone`, `embeddings._embed`, `ollama_proxy.forward`. Claim-based embedding prechecks exist (and were proven in #37), but are not atomic admission. Preserve semantics and use the same order door; do not invent a model/window or silently route to a smaller model. Existing advisor/systemone/embed measurements and payloads are prior art. The register records an embedding arm at 8,192 with `truncate:false`; do not assume no evidence exists. Generic `fit_probe.load_at` uses generation and must not blindly warm an embedding/decision protocol that cannot use it.
2. **Actual-host maximum context on reroute:** preserving the nominal request's window is not automatically selecting the actual box's largest tested window. Resolve via existing Spock policy/model/context machinery before compaction/checkpointing; never silently mutate a checkpointed request or cap it to make placement fit.
3. **Strata capability declaration:** candidate still looks for `FACTORY_LIVE_PROTOCOL=1` in the restored daily wrapper, whose marker was removed with the bad exception. Thus live Strata capability is intentionally not advertised. Replace that obsolete dependency with evidence of the correct owned capability only after required proof; do not restore the exception to make it advertise.
4. **Idle shared Strata → native memory admission:** `gpu_spill` managed allocation currently names Ollama/llama-server PIDs; Strata can look like an unrelated tenant before native worker has authority to unload it. This may wait for the 300-second TTL. Do not lie about reclaimable memory or broadly exempt Python to force a fit. Review the existing engine-ownership/allocation path.
5. **On-box progress/supervision:** relay pulses/timer-thread life are not worker progress. Prove long silent native preparation/prefill is correctly supervised by the existing factory machinery; do not add a session watcher or arbitrary work clock.
6. **Artifact lifetime:** identify retention/retirement ownership for desk model-turn request/response artifacts and factory spools. Existing `ORDER_OUT` and queue retirement conventions apply; no silent janitor has been added.
7. **Runtime proofs:** actual front-door stream/thinking/tool calls; queued and running Stop; handoff/claim revision; restart without duplicate inference/tool execution; priority batch resume; image coexistence using mocks first. Do not call green unit counts a live proof.
8. **Finish remaining charter items:** B4b overflow; B11's MTP drafter/sampler RNG state and resume/reload pricing; Phase 8 controller findings/data-dependent work. C7 stays a data watch. Keep settled no-build/no-gain decisions closed.

Also review legacy direct guards and all caller contexts rather than treating `factory_facts` as permission to bypass routing. The full candidate has not received a blanket architectural approval.

## Candidate files, baselines and tests

**Use these sources only for review/promotion, not the whole STAGE tree:**

```
model/app.py
model/ollama_client.py
model/p20_queue.py
model/inference_orders.py
model/factory_inference.py
model/tests/gate_pin.py
model/tests/test_silence_watch.py                 # existing fixture adaptation
model/tests/test_inference_order_store.py
model/tests/test_factory_store_atomicity.py
model/tests/test_model_turn_api.py
model/tests/test_factory_inference.py
model/tests/test_model_turn_pins.py
foundry/p20_foundry.py
foundry/infer_live.py
foundry/infer_ollama.py
foundry/test_foundry.py
```

`foundry/tests/test_live_model_{relay,factory,priority}.py` are focused source cases used by `assemble_foundry_gate.py` to generate the monolithic gate. Editing only the generated monolith and reassembling loses those edits. The restored `foundry/workspace/flashnext/{strata_serve,test_strata_serve}.py` are unchanged baseline copies, not new code to deploy. `ui/`, `unified/`, `windows-foundry-gate/` and withdrawn directories contain historical/test artifacts and must not be deployed wholesale.

Evidence/baselines in STAGE:

| Artifact | Meaning |
|---|---|
| `HANDOFF-SNAPSHOT-20261020.json` | Fresh handoff UTC timestamp, hashes of 21 candidate/baseline wrapper files and evidence logs; all canonical baseline hashes matched at creation. |
| `model-integration-baseline.json` | Original canonical app/client/queue/pins/fixture and foundry/monolithic-test/batch hashes. Check drift before editing/promoting. |
| `strata-integration-baseline.json` | Original unchanged wrapper/test hashes. |
| `installed-routing-yield-evidence.json` | Installed hashes and stated PID/source freshness for all three factories; live orchestrator hashes and absent new module. Snapshot, not perpetual current health. |
| `routing-reuse-evidence.json` | AST equality of original chooser, slot, intake, queue/rank, LaneYield, queue runner and collectors. |
| `standard-staged-gate.log` | **3003 tests, OK (skipped=2), exact 3003/2 staged pin.** |
| `windows-foundry-gate.log` | **579 tests, OK, pin 579.** Includes CPU-only real-CIM image-name control. |
| `release-negative-controls.log` | Disabling release verification makes both actual compute-runner boundary tests fail as expected. |
| `original-batch-boundary-negative-control.log` | Unmodified batch completes both mocked rows; expected boundary assertion fails. |
| `routing-shortcut-control.log` | Restored chooser passes; exact withdrawn shortcut fails the chooser-preservation case. |

Installed runner snapshot: Mothership PID 15252, BigHonker 9724, Softserve 2736; each started after its verified source mtime. Recheck before deployment. Live original source hashes include app `1c74dbace0d732ab...`, client `d55bd60ebaac9ca7...`, foundry `2d6101a3d1b91856...`; full values are in the manifests.

### Reproduce the candidate gates without deploying

From the working home, after inspecting the scripts:

```powershell
python .p34-chat-stage\assemble_foundry_gate.py
python .p34-chat-stage\run_windows_foundry_gate.py
python .p34-chat-stage\run_standard_staged_gate.py
```

- Windows runner creates a **workspace-only** foundry copy, then runs its existing monolithic gate with its pin. Loopback/CIM positive controls need the appropriate execution permissions. These are CPU fixtures, not GPU work. Direct sandbox runs had permission-related failures; don't weaken code or fixtures to hide them.
- Orchestrator runner reads the real `/opt/arrstack/run-suite.sh` invocation and uses read-only staged source + complete test overlays. It retains the lab's state redirections, notification-secret isolation, dead-loopback swap setting, embedding disable and central no-live-model guard. It neither builds nor replaces the production image/container.
- Baseline 2958 +45 new orchestrator cases =3003; baseline 530 +49 foundry cases =579. Change pins deliberately with test changes; don't use environment overrides to force a pass.
- `run_offline_integration_gates.py`, `offline-*.log` and early focused logs include failed approximate runs with missing fixtures/incorrect platform/environment. They are diagnostic history, not deployment evidence. Use the standard logs above.
- Default tempfile access was restricted in the sandbox. Existing scripts redirect `TEMP`/`TMP`/`tempfile.tempdir` to `STAGE\model-test-tmp` where needed. Avoid claiming infrastructure failures as application defects.
- Negative-control helpers: `verify_release_negative_controls.py`, `prove_existing_batch_boundary.py`, `review_routing_shortcut_control.py`. Mutations are in-memory/disposable containers and do not modify source or live work.

## Suggested first move for Claude

Read the register/current audit and inspect the candidate diff against the recorded clean baseline, including existing helper-window/relay code. Confirm the smallest remaining connection needed for the charter's “all model work is an order” requirement. Preserve the restored router and one-lane invariants. Continue the existing Phase 7/8 closure work with evidence; do not reinterpret the candidate or this handoff as permission to redesign the lab, reopen #37, launch new benchmark grids, or declare Phase 9 started.
