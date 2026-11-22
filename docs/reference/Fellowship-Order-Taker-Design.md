# Fellowship Order Taker — design and scope (the Fellowship's factory layer)

## Architecture and components

*   **Factory implementation**: `E:\SSDShared\tolkien-build\`. The primary script is `tablet_foundry.py` (2704 lines). Supporting scripts are `tablet_runner.py` and `tablet_fulfil.py`.
*   **Deployment context**: The factory build context is isolated in the `arrstack-tablet-foundry` image, distinct from the shared orchestrator image. See [[RUNBOOK]] §TWO BUILD LANES.
*   **Interface**: Orders are submitted via `INBOX` and results returned to `OUTBOX` on the network share. Specification files use the `.tjob.json` extension and conform to the shape defined in this document.

## Operational mechanisms

The order taker implements the following controls:

*   Queue management: run queue with pull batching.
*   Reliability: speculative re-execution and per-batch checkpointing.
*   Control logic: positive control, `retire_order`, and health monitoring.
*   Admin tooling: The `tablet_map` tool in `orchestrator-build\tablet_orders.py` provides the CUSTOMER counter. It is admin-gated and limited to whitelisted payloads: `map_detect`, `map_echo`, `map_fetch`, `map_motion`.

## Order Taker Scope and Scheduler Integration

The Fellowship architecture separates orchestration (`scheduler.py`) from order intake. The scheduler handles node discovery, ranking, and placement; the missing component is the **Order Taker** (factory half), which accepts and structures work requests.

## Scheduler Capabilities (`scheduler.py`)

`scheduler.py` serves as the orchestrator for the Fellowship cluster.

| Component | Specification |
|---|---|
| Node Roster | `TABLETS`: Defines Fellowship nodes including cores, RAM, and GFLOP/s, read from the device roster file's `tablets` list (`Z:\Documentation\State\device-roster.json`). Source of truth for the hardware: [[Tablet-Cluster-INDEX]]. |
| Availability Check | `tablet_alive(host)`: TCP probe on port 8022 with a 60-second cache. **Design Doctrine**: Fail-closed; unreachable hosts are never offered work. |
| Placement Logic | `place(kind, ...)`: Ranks tablets below any free x86 box. Gated to independent CPU work only. |
| Chunking Strategy | `chunk(items, hosts, weights)`: Rank-based weighted split. **Design Doctrine**: Band-hash is prohibited due to historical node starvation issues. |
| Order Placement | `place_order(requires, ...)`: Performs capability-checked placement; returns per-host item lists. |
| Resource Claims | `claim(host, resource, ...)`: Manages per-node claims with Time-To-Live (TTL). |

## Missing Component: Order Taker

The scheduler half is complete. The system lacks the **Order Taker** component required to receive and process incoming work orders.

## 1a. Design Rationale: Centralized Order Processing vs. Private Scripts

The lab uses a centralized Order Taker to manage tablet workloads because private scripts introduce reliability defects that the GPU factory already resolves at the infrastructure layer.

### Defect Classes Eliminated by the Order Taker

| Failure Mode in Private Scripts | Resolution Provided by Order Taker / Factory |
|---|---|
| Manual implementation of pull queues, speculation, requeue logic, and checkpointing is error-prone and fragmented. | The Order Taker provides robust, shared implementations of queue management, fault tolerance, and state persistence.
| Process killing commands (e.g., `pkill`) match their own SSH command lines, terminating the controlling session. | Documented self-match rules prevent accidental self-termination.
| Unhandled transient exceptions (e.g., `TimeoutExpired` from `sh()`) escape to catch-all handlers, silently dropping work items (images) and killing worker threads. | The factory enforces the rule: "a transient condition must never reach a catch-all that consumes work."
| Background monitors holding stderr attached prevent SSH session closure, blocking job start (`sshd` job-object teardown is transitive and kills detached children). | Jobs are launched via `schtasks`, isolating them from SSH session lifecycle dependencies.

### Design Fact

Private scripts incur recurring operational costs and risk silent data corruption (wrong results rather than explicit errors). The Order Taker exists to enforce the factory’s reliability standards uniformly, eliminating per-script re-derivation of solved infrastructure problems.

## 3. Shape — every tablet its own lane; Tolkien schedules the orders (the node pool, 2026-10-23)

the owner 2026-10-23: *"if Tolkien has 10+ tablets, the orders are intended to only take the tablets they need so other
things can come through using the other ones"* (his own correction of "supposed" to "intended": never built, not
broken) · *"KIRK is NOT the scheduler of the fellowship. So Kirk can send 10
orders to Tolkien since Tolkien can say 'yep I still have a free spot to run your work keep it coming'"* · *"each tablet
can be its own lane, but he can coordinate larger work to use multiple tablets"*. Serial orders made sense while the
menu was being built; this is the final shape. The GPU factories run one job per CARD; Tolkien runs one job per NODE.

- **The node pool** (`tablet_runner`: `take_nodes` / `give_back`, `NODE_HOLDER`): every fleet-lane order takes exactly
  the tablets it runs on, all or none, and gives them back when it ends by ANY route (`build_order` and `run_one` both
  give back). The next order starts at once on the free ones — Kirk sends, Tolkien fills the free spots.
- **What an order takes:** an elastic `map`/sweep order — one tablet per batch it deals, the fastest free first (a
  one-item order holds one tablet; an SKU-affinity order every free one); a `distinct_workers` cohort — its count; a
  `map.window` — exactly its cohorts (`window_hosts`, execute_window's own selection); a placed `warm_nodes` piece —
  that many; `warm` (CHAT1 shape) — every free tablet; Voltron and a `bench` — the whole fleet (and rigby).
- **Waiting is a status, not a clock:** an order whose tablets other orders hold raises `NodesBusy` and waits untouched
  until an order gives tablets back (`NODES_FREED`), or the full pass. The tablets it could use are RESERVED against
  every order queued behind it, so a large order (Voltron, a window) is never starved by a stream of small ones.
- **Choosing is one at a time, running is parallel** (`dispatch_fleet` / `launch_fleet_order`): orders pick and take
  their tablets in queue order; once an order holds its tablets the dispatcher starts the next.
- **A piece waiting on a busy rigby takes a free tablet** (`rebind_to_free_worker`): Tolkien resolves a placed piece
  to the first READY row of its placement run when it collects it (presence) and keeps the run's other ready rows
  (`placement_options`); while rigby is busy, a still-queued piece moves to the run's `warm` row only when a tablet is
  free AND the move pays on Spock's OWN weighing (`_move_pays` -> `scheduler.weigh_rows`, one derivation): the row is
  inside the request's quality band (the owner's overflow rule, 10-02: *"a slightly pricier box of similar quality"*) and
  faster than the line for rigby ((ahead + running + its own) x rigby's priced time). Could not tell = no move.
  `placement_row.moved_from` records a move; a piece that stays logs why once. rigby's pick and a move share one lock.
  Measured 2026-10-23: the first build moved two chat turns to class-A tablets (quality 0.625, below the 0.90 band) and
  each took ~26 s against ~16 / ~23 s in rigby's line - the rule exists because of that. The table now prices each
  Fellowship worker's desk-order overhead from its own deliveries (rigby 8.6 s n=13, a tablet 28.6 s n=2).
- **Health probes skip tablets other orders hold** — the running order is their evidence of life; an ssh probe would
  only load a busy node.
- **rigby** stays one unit: the rigby lane's order and a Voltron order share the rigby lock (§13).
- **Tolkien reports his free spots; Spock decides** (generalizing DOWN, 2026-10-23 — the same shape as Starfleet one level down): every run-state push carries `capacity` = {nodes, free_nodes, rigby_busy, rigby_line} (`tablet_runner.fleet_capacity`, re-pushed the moment it changes: `push_capacity_if_changed`). Spock's weighing reads it (`scheduler.fellowship_load`, with Kirk's own unanswered Fellowship turns for a burst Tolkien has not collected yet) and prices a busy rigby's line as WAIT on rigby's row, so the band's own ordering takes the in-band overflow (the owner 10-02: *"a slightly pricier box of similar quality when the best one is busy"*); no free tablet = an unknown wait, never inside the line. Live proof: PROVEN 2026-10-23 15:28 CT on a quiet lab (three ClaudeSupervisor Auto chats 1.5 s apart, McCoy sure of all three): two queued on rigby (13.6 / 18.4 s); the one weighed while two were in flight went to BigHonker qwen3:8b, its placement record reading "rigby busy: 2 ahead of this turn (+24 s wait; Tolkien: rigby busy, 0 in line, 11 free tablets; Kirk: 2 Fellowship turns unanswered)", past the interactive line; McCoy's shadow rows beside every Tolkien decision. GAP FOUND: that overflow turn took 37.9 s against a 12.0 s price - a GPU chat turn served through a NEW window pays the window's desk-order overhead and HAI's full agent prompt (9,418 tokens read), and neither is in the GPU row's price (the Fellowship rows got their measured overhead today; the GPU rows' window overhead is unpriced - class 7).
- **McCoy advises Tolkien (shadow)** — one McCoy for both levels (the owner 2026-10-23): once an order has its worker, Tolkien asks Kirk's `/mccoy/tolkien` door (which worker, how many tries, stay or go up) on a background thread and logs the advice beside his decision (`state/mccoy_shadow.jsonl`; `TOLKIEN_MCCOY=off` stops it). Nothing waits on it or acts on it until those questions have a measured threshold (#35); the log is that data.

### Execution Flow

1. **Submission**: Orders are submitted by dropping a spec file into the INBOX, `E:\SSDShared\tabfoundry-jobs\inbox\<name>.tjob.json` (§9); the order taker copies it into its own queue. Stop / hold / release / priority go through the same door as slips (§11).
2. **Polling**: The `run_queue()` singleton polls for new orders and starts every fleet order that fits on the free tablets
   (§3); the rigby lane runs one order at a time (§13). Inbox intake and slip routing also run every few seconds while orders run.
3. **Health Check**: Before execution, `fleet_health()` verifies each node is alive, doze-whitelisted, and holding a wake-lock.
4. **Placement**: Items are placed using the existing scheduler: `scheduler.place("cpu", only=TABLETS)`. No new placement logic is introduced.
5. **Distribution**: Nodes pull batches when free. Static splitting is never used; distribution depends on node availability.
6. **Re-execution**: If a batch is held longer than `max(3x median, 120s)`, speculative re-execution triggers to reclaim it — **except a pinned `distinct_workers` sample, which is never stolen from a live straggler** (tolkien `2dff647`, 2026-09-28): a pinned sample re-run on another tablet fails the distinct-worker check, so the steal could only VOID the window, and the losing original kept its tablet busy into the next window. With fast answers (thinking off, ~40 s median) the 120 s floor stole from every slower tablet — 4 of the first 6 Qwen3.5-2B thinking-off calibration windows went VOID that way. A pinned tablet that FAILS still requeues its item, and that reassignment is reported VOID, visibly.
7. **Checkpoints**: Progress records append to `var/state/tablet/<name>.jsonl` per batch.
8. **Control**: An item with a known answer rides every order as a positive control.
9. **Completion**: `retire_order()` ships deliverables, clears staging, and archives the spec.

## 4. Order spec (`*.tjob.json`)

The `*.tjob.json` format mirrors the GPU job spec's intent fields so both factories read the same way.

```json
{
  "name": "watermark_sweep_v2fleet",
  "kind": "PRODUCT - ships a per-image JSON verdict list. Crops are byproducts.",
  "question": "Which corpus images carry a readable attribution watermark?",
  "items_from": "D:/Datasets/_archive/caption-staging-20260805/v2fleet",
  "items_list": "wm_full_names.txt",
  "payload": "wm_ocr.py",
  "invoke": "python {payload} {listing} {out}",
  "batch": 8,
  "control": {"file": "ZZ_POSITIVE_CONTROL.jpg",
              "src": ".../framing_a/gonewild_1mmi137.jpg",
              "expect": "bonf"},
  "deliverables": ["<name>.results.json"],
  "cleanup": "remote staging removed per batch; local pass-through removed at retire"
}
```

### Placement fields on `map` / `map.window` orders (opt-in, validated at intake by `spec_defects`)

| field | meaning | notes |
|---|---|---|
| `require_arch` | only tablets whose `scheduler.TABLETS` arch matches (e.g. `aarch64`, `armv7l`) | fleet default is aarch64; an exception (Sméagol) declares its arch |
| `distinct_workers: N` | exactly N items, each on a different tablet, batch=1; a pinned item waits for its own tablet (never speculatively stolen) | `map` only; cannot combine with `sku_affinity`; a window sums its arms' N (max 10) |
| `sku_affinity: true` | each item's first `~` field names a lane (`A`/`B`/`C` — three hardware classes; sam is Class B. `sam` exists as a lane label only for the gauntlet's 2026-08 diagnostic rows); lanes are pulled only by tablets of that SKU | same-SKU tablets split a lane by dynamic pull |
| `support_files: [paths]` | extra files staged beside the payload on the node, so a payload can import siblings (the GPU factories' payloads import from `bin`) | **added 2026-10-02.** Container paths (`/ssdshared/...`); refused at intake if not a list, a file is missing, or two names (incl. the payload) collide. First use: `grade_jobs.py` + the two coding scorers ([[RUNBOOK-Compute-and-Training-Order-Submission]] §"MENU GRADING"). |
| `exclude_hosts: [names]` | tablets this order must NOT run on; unknown names are refused at intake | **added 2026-09-26.** Tolkien has no memory across orders, so a multi-stage team (solver order, then critic order) can only keep its roles on different tablets if the second order names the first one's host. Applied to the window's pool, never per arm. If it leaves no eligible node the order is HELD (`exclude_hosts leaves no eligible healthy node`). Born from Phase 5 arm `heterogeneous.text_critic_on_class_b.chat.sisters`: lane items seed to hosts in list order, so 24/24 critic attempts landed on the solver's tablet. |

## 5. Operational Invariants

1. **Positive control on every order.** Every processing run includes a positive control. If the control fails, the run is void (not empty). A missing or failed control indicates that broken detectors and clean cor producing identical outputs cannot be distinguished.
2. **Fail-closed liveness.** Unreachable nodes are excluded from work distribution. The system never assumes node health without verification.
3. **Requeue on failure; never drop.** If a node fails its batch, work returns to the queue. Unhandled exceptions must not consume items (e.g., `sh()` raising `TimeoutExpired` caused silent item loss and thread termination in prior incidents).
4. **Failed workers are unmeasured.** Non-zero exit codes record as errors triggering re-runs, not as clean completions.
5. **Checkpoint every batch.** Processing jobs checkpoint after each batch to prevent total loss of long-running sweeps (e.g., 70-minute duration) due to driver instability.
6. **Batch size constraints.** Batch size must meet or exceed the maximum worker count across all nodes to prevent starvation (e.g., `BATCH=3` caused 40% throughput loss on node `gandalf`).
7. **Dynamic load distribution.** Work assignment uses dynamic pulling based on current throughput. Static splits by stored weights are prohibited because node performance variance is high and rapid (e.g., node `pippin` recorded latencies between 0.61 s and 48 s within 80 seconds).

## Fleet Health — Android suppression mechanisms

Two independent Android mechanisms suppress a tablet. Both must be active for full performance; if only one is present, the node appears healthy but delivers ~1/12th of expected speed.

| mechanism | symptom | fix | verify |
|---|---|---|---|
| doze / app-standby freezer | process SUSPENDED minutes at a time; cold CPU; short bursts fine | `cmd deviceidle whitelist +com.termux` (requires adb; Termux itself is refused) | `cmd deviceidle whitelist \| grep termux` |
| CPU wake-lock not held | sustained CPU throttled ~8-12x; process runs but crawls | `termux-wake-lock` (requires the Termux APP context) | `dumpsys power \| grep -c termux` ≥ 2 |

A node revived outside the Termux app context is a degraded node. Revival via `adb run-as` creates an orphan process that holds no wake-lock. Prefer a reboot, which triggers Termux:Boot to start sshd and the wake-lock within the correct app context.

## Execution environment: rigby (built on Sundae)

⚠ *Corrected 2026-09-27: this section described the pre-2026-08-28 placement (containers on Sundae,
tablets reached by Softserve's ssh aliases). That is superseded — see the first paragraph of this doc.*

The order taker (`tablet-foundry`) and its watchdog (`tablet-watchdog`) run on **rigby**, host
network, so they reach both the LAN and the USB `br0` tablet segment. `scheduler.py` is not a
separate service here: the tolkien image COPYs it from the orchestrator build context at build time
and the head imports it (one placement brain, never reimplemented). Tablets are reached by IP + the
cluster key mounted at `/run/secrets/tabcluster_key`, never by an alias. Sundae's copies of the two
services are profile-gated and must stay down (a second foundry would double-write run-state).

### Deployment procedure

Follow [[RUNBOOK-Orchestrator-Deploy-Procedure]] § "TWO BUILD LANES" exactly: mirror audit →
back up + copy into `/opt/arrstack/tolkien/` on Sundae → `docker compose build tablet-foundry
tablet-watchdog` → `run-tolkien-suite.sh` (predict the count) → `docker save | docker load` from
Softserve into rigby → recreate BOTH consumers on rigby as root via `Lab\bin\run_remote.py` → verify
`/app` hashes in both containers and "order taker up" — at once since 2026-10-30: a clean stop (SIGTERM) makes the
holder RELEASE its lock and exit (`tablet_foundry.release_singleton` / `install_stop`), and the next taker takes a
released lock immediately unless the old one has beaten since; the ~90 s heartbeat-silence rule remains for an
unclean stop. Every container's PID-1 Python handles SIGTERM, so a stop no longer waits docker's 10 s grace.

**Constraint:** `docker compose restart` executes old code baked into the previous image. Always build + recreate.

### Fleet identity and API contract

The tablet fleet publishes as a single factory with `host: "fellowship"`. This matches the GPU factory model:
- `POST /fleet/run-state` keys records by HOST.
- `app.py::_fleet_run_write` stores one record per host.
- `scheduler.factory_capabilities(host)` reads capabilities back from that single key.

See [[HAI-Orchestrator-Internals]] §"POST /fleet/run-state" for the API contract.

### Identity separation: Factory vs. Node

Two distinct identities are maintained deliberately:
1. **Factory identity (Fellowship):** Answers "can this order be built here?" using aggregated capability data.
2. **Node identity (Individual tablets):** Answers "which node runs this item?" using hardware-specific rankings in `scheduler.place()`.

Do not collapse these identities. Capability is a factory-level question; placement ranking is a node-level hardware question.

### Relationship to GPU factory

The tablet fleet is a sibling factory to the GPU factory, not an alternative implementation. Both share:
- Spec field names.
- Order lifecycle stages: ORDER → FULFILMENT → SHIPPING → CLEANUP.
- Generic helpers: timestamped archive naming, runner singleton, heartbeat, control slips (the
  same customer vocabulary — `<order>.stop.json` and `*.ctl.json` with `action` / `order` / `why` /
  `value` — see §11; ⚠ this line claimed control slips from the start, but the tablet code had
  NONE until 2026-09-27: declared ≠ present, logged in `RUNBOOK-DEFECT-LOG`).
- Doctrine rows: fail-closed, requeue, never-drop.

They maintain separate:
- Queue directories.
- Runner processes.
- State files.

**Architectural distinction:**
- GPU factory: Serial execution (one card, one job; orders wait).
- Tablet fleet: Parallel farm (one order, items fanned across nodes).

A shared queue directory is prohibited. GPU runners cannot build tablet work, and vice versa, creating a capability gap with no operational benefit.

### Capability publishing policy

The fleet publishes readiness via the same mechanism as other factories so `place_order()` sees FLEET readiness, not just per-node liveness.

**Design fact:** The fleet MUST publish capability while IDLE.  
*Reasoning:* If capability is published only during active runs, idle nodes report zero capabilities. The scheduler interprets this as unavailability, causing routing failures exactly when capacity is free.

Health checks require both mechanisms defined in §6 of this document, not only `tablet_alive()`.

## 9. How to Submit a Tablet Order — Inbox, Outbox, and Discord Notice

Submit a tablet order by placing a `*.tjob.json` file in `E:\SSDShared\tabfoundry-jobs\inbox\`. This is the complete submission procedure from any host with access to the share. No running session or additional steps are required.

| Stage | Location / Signal | Observer Action |
|---|---|---|
| Submit | `E:\SSDShared\tabfoundry-jobs\inbox\` | File disappears upon acceptance |
| Watch | Homepage tile **Fellowship - Tablets** | Monitor Fleet / Order / Phase / Progress / Rate / ETA — pushed per finished item (`tablet_runner.window_eta`). Rate = seconds per item on one tablet; ETA assumes the window's items run `distinct_workers` at a time (the rounds it needs × per-item time), not one after another. Blank until the first item lands. |
| Finish | Discord DM from `[fellowship:desk]` | Receive SHIPPED or VOID status with reason |
| Collect | `E:\SSDShared\tabfoundry-jobs\outbox\` | Retrieve `<name>.<timestamp>.results.json` |

### Inbox vs. Queue Architecture
The queue, archive, and state reside on the local disk of **rigby** (`/opt/arrstack/tablet-foundry/`, container `/tabfoundry`; Sundae until 2026-08-28). Intake is a copy operation across the share boundary, not a rename. `os.replace` between a CIFS mount and local disk raises `EXDEV`; this error would occur during the `retire_order` ARCHIVE step after work completion if attempted. Files are held for 120 seconds if detected mid-copy to allow remote writers to finish, then retried.

### Results Validation: Verdicts and Usability
A shipped order is not automatically usable. Every results file carries `control_passed` and `complete` flags; an order is **usable** only if both are true (`usable = control_passed AND complete`). These signals fail independently:

*   `control_passed`: Indicates detector functionality. A known-positive control rides in every order. If the control is not read back, the run is VOID and results are invalid.
*   `complete`: Indicates corpus coverage. Compare `n_measured` vs `n_expected` and check `never_measured` for specific files. A detector that functions but misses part of the corpus is a critical failure; other signals may remain green.

The Discord DM specifies which verdict failed using these exact terms. `VOID` in the results file indicates at least one verdict failed.

## 10. THE FELLOWSHIP WATCHDOG — the tablet fleet's daemon

`tablet_watch.py` runs as a dedicated container (`tablet-watchdog`), separate from `tablet-foundry`. Separation is required so the watchdog can detect process hangs, silent deaths, or infinite loops in the foundry, which a shared process cannot observe.

The watchdog measures three metrics and reports them every minute to `POST /fleet/rescue-state`. This endpoint unifies daemon status across all four factories, ensuring the homepage's **Daemon** row derives from a single source without dialect drift.

| watched | threshold | design rationale |
|---|---|---|
| order taker heartbeat | stale > **300 s** | The runner beats every ~30 s; 300 s represents ten missed beats. |
| current order progress | frozen > **20 min** | Worst-case batch of 8 items measures ~6.5 min at 48 s/item, plus speculative-execution cutoff buffer. |
| rigby lane order progress | frozen > **20 min** | Same rule on the rigby lane's own claim (§13), alert key `stall-rigby`. **A chat window (`hai-win-rigby-…`) keeps that claim current** (2026-11-13): turns served as `done`, the stream lines of the turn in flight as `work`, restated every runner beat by a keeper thread written as rigby's lane (`tablet_runner.build_chat_window_order`, `tablet_foundry.window_turn_in_flight`); between turns its phase is `tablet_foundry.WINDOW_WAITING` and the watchdog does not judge it. Before, the window wrote "0 done" once, so every window over 20 min DM'd "frozen at 0" while it served turns. |
| fleet reachability | 0 nodes answer | Probing occurs only while IDLE to avoid adding SSH load to busy nodes during order processing. |

### Reporting-only architecture
The watchdog is reporting-only. It has no ACT tier and no Docker socket access, preventing accidental escalation to destructive actions. A guard that can act destructively requires a higher verification bar than one that reports. For the tablet fleet, the container restart policy combined with human intervention provides sufficient remedy for hung orders; automatic remediation offers no justified advantage.

### Inconclusive as first-class verdict
"Unable to read heartbeat" is never rendered as "runner down." Stall baselines derive from the first value actually observed. Sentinel values (e.g., `step0 = current_step() or 0`) are prohibited, as they disable detection at resume boundaries where detection is critical.

### Operator control: Standdown mode
Creating the file `\tabfoundry\WATCHDOG_STANDDOWN` (`/opt/arrstack/tablet-foundry/WATCHDOG_STANDDOWN` on rigby) places the watchdog in standby. The watchdog reports its status on every beat, and the dashboard displays **⏸ Stood down**. This distinction ensures a stood-down guard is never mistaken for a healthy one. The watchdog does not police the existence of this control flag to prevent self-muting via flag monitoring.

## 11. STOP / HOLD / RELEASE / CANCEL / PRIORITY a tablet order — stop slips (`.stop.json`) and control slips (`.ctl.json`)

**Built 2026-09-27.** Before that the design claimed control slips (§"Relationship to GPU factory")
but none existed in `tablet_foundry.py` / `tablet_runner.py` / `tablet_fulfil.py`, so the only way
to stop a tablet order was renaming its `.tjob.json` to `.HELD` by hand in rigby's queue — a hand
action on the factory floor (it also broke the running window's own archive step:
`window archive failed (FileNotFoundError) - spec left in queue`). The vocabulary is the GPU
foundry's (`p20_foundry.py` `stop_order()` / `process_control_orders()`), so a customer learns one
set of words for all four factories.

**Where:** drop the slip in the INBOX, `E:\SSDShared\tabfoundry-jobs\inbox\` (container:
`/ssdshared/tabfoundry-jobs/inbox`) — the same door as orders. Slips never enter the queue:
`drain_inbox()` and `queued()` only take `*.tjob.json`. Every slip must be valid JSON (`{}` is fine).

| slip | body | effect |
|---|---|---|
| `<order>.stop.json` | `{"why": "..."}` (optional) | stop that order — queued, held or RUNNING |
| `<order>.ctl.json` | `{"action": "hold"}` | park a QUEUED order as `<order>.tjob.json.HELD` |
| `<order>.ctl.json` | `{"action": "release"}` | re-admit a `.HELD` order to the queue (replaces re-submitting a held copy through the inbox) |
| `<order>.ctl.json` | `{"action": "cancel"}` | identical to the stop slip |
| `<order>.ctl.json` | `{"action": "priority", "value": 5}` | set the spec's `priority` (higher runs first); no `value` = front of the queue (max of the others + 1) |
| any `*.stop.json` / `*.ctl.json` | `{"prefix": "<run-id>", ...}` | apply to EVERY queued/held order whose queue name starts with the prefix (one slip for all windows of a run) |
| any | `"dry_run": true` | resolve + report what would change; change nothing |

Fields match the GPU foundry: **`action`**, **`order`** (target name; defaults to the slip's file
name minus `.stop.json`/`.ctl.json`; `target` is also read), **`why`** (the cause, quoted in the DM
and recorded in the archive), **`value`** (priority). Names are QUEUE names — the file name minus
`.tjob.json` / `.tjob.json.HELD`, which is the spec `name` for every inbox order.

**Refusals (named, archived as `<slip>.refused-<stamp>`, one DM, nothing changed):** a `prefix`
that is empty or shorter than **8** characters; an unknown `action`; a non-number `value`; a body
that is not a JSON object. A slip still unreadable after 120 s (half-written on the share gets that
grace) is archived `.unreadable-<stamp>`.

**States and what happens:**

| order is… | stop / cancel | hold | release | priority |
|---|---|---|---|---|
| queued | spec retires to `archive/<order>.tjob.json.stopped-<stamp>` with `_stopped: {at, state, cause, slip}` inside | → `.HELD` | "already queued" | spec `priority` rewritten |
| held (`.HELD`) | retired the same way | "already held" | → `.tjob.json` (refused if a queued order of that name exists) | "held (release it first)" |
| RUNNING | see below | refused, DM names the `.stop.json` to use | refused | refused |
| unknown | slip archived `.unknown-<stamp>`; DM says "no queued, running or held order matches" (or "already finished (retired …)" if it just retired) | same | same | same |

**Stopping the RUNNING order:** while an order runs, the runner's `StopWatch` routes the inbox every
**5 s** (`TABLET_STOP_POLL`) — so hold / release / priority slips for OTHER orders land mid-run — and
a slip naming the running order sets the line's stop event. From then on `run_order()` deals no new
batch and steals no straggler, and **every batch already on a tablet is ended** (2026-10-03, `tablet_fulfil`
`kill_attempt_cmd`): the payload processes whose working directory is that attempt's stage — and their children
(`llama-cli`) — are killed, matched by cwd, never by command line. Parity with rigby (cuts the answer in flight) and the
GPU factories (end the child). Before this the stop relied on `batch_timeout` to end in-flight work, and that clock was
removed 2026-09-29 (13a): a stopped order's looping item then held the Fellowship for three hours. Live decoy
2026-10-03 11:48: stop 11:48:33 → both in-flight batches killed by 11:48:36 → retired 11:48:37, no orphan `llama-cli`.
Known leftover: a killed attempt's remote stage dir is not removed (next Tolkien change). Everything never dealt or
cut comes back as `never_measured` (stopped work is
reported, never silently dropped). The order then ships normally with **`"stopped": true`,
`"stopped_by": <slip>`, `"stop_cause": <why>`** in the results file (always `usable: false` unless
every item had already finished), the spec retires as `.stopped-<stamp>`, a desk-collected order
reports FAILED so chained successors hold, and only THEN is the slip routed — so a prefix slip that
stops the running window also retires the queued/held windows behind it in the same pass.

**One DM per slip, never per item** (via the existing `notify_desk`, prefix `[fellowship:desk]`).
A stopped running order sends the STOPPED DM *instead of* its SHIPPED/VOID DM. Shapes:

- `STOPPED <order> (queued|held): <why>`
- `STOPPED <order> (running): <why> -- shipped N finished row(s) flagged stopped:true -> <results file>`
- `STOPPED prefix <p>: N order(s) (a running, b queued, c held): <why>` (+ `| not changed: …`)
- `HELD <order> (queued): <why>` · `RELEASED <order> (held): <why>` · `PRIORITY <order> -> <v> (front of the queue): <why>`
- `REQUEUED <order> (from <archived spec>, checkpoint N row(s)): <why>` · `REQUEUE <order>: <reason> - nothing changed`
  (`requeue` = put the newest archived generation back in the queue under its own name; exact name only)
- `DRY-RUN <action> prefix <p>: would affect N order(s) (…) - nothing changed`
- `REFUSED <slip>: <reason> - nothing changed` · `STOP <order>: no queued, running or held order matches - nothing changed`

**Archive:** every routed slip lands in `archive/<slip>.<done|refused|unknown|dryrun|unreadable>-<stamp>`
with a `_routed` record (what changed, what was skipped and why). Timestamped, never bare.

Code: `tablet_foundry.py` § CONTROL SLIPS (`parse_slip` — the ONE reading shared by the router and
the StopWatch — `process_slips`, `archive_stopped_spec`), `tablet_runner.py` `StopWatch`,
`tablet_fulfil.run_order(stop_event=)`, `requeue_order`. Tests: `tests/test_tablet_slips.py` (43 test methods on
2026-10-02, including `RequeueSlipTests`). Operator steps:
[[RUNBOOK-Compute-and-Training-Order-Submission]] § "STOP / HOLD / RELEASE a TABLET order", beside
the GPU foundry's own stop section.

## 12. Tolkien reaches tablets over the USB wire first (WiFi backup) — routing, the USB link keeper, and a busy lane that waits

### Routing: USB wire first, WiFi as the backup — ON (`TABLET_ROUTE_PROBE=1`)

**Design (the owner):** every tablet is cabled to rigby; *"USB for main. WiFi for backup. Hence why Tolkien lives on
Rigby."* `tablet_foundry.node_ip(host, port)` returns a node's wire address `10.55.0.<WIRE_OCTET>` (the octets are the `TABLET_WIRE_OCTETS` setting in `tolkien.env` since 2026-11-21) when that port
answers there (a 1.5 s connect, remembered `TABLET_ROUTE_TTL` = 60 s), else its WiFi address. ssh (health checks,
dispatch, scp), the fellow agent's HTTP port and the detection port all go through it. **adb takes the wire too
(2026-10-10):** `_adb_targets` tries `10.55.0.<octet>:5555` first and `<host>.lan:5555` as the backup, so a broken
wire still falls to WiFi for repairs (caveat: a class-A wire STALL — connects, then hangs — would hold an adb command
until its timeout before anything else is tried). **Liveness follows the same route (2026-10-10):** `node_health` counts a node alive when its
wire answers, else asks `scheduler.tablet_alive` (WiFi); before this a node fine on its wire but with WiFi down read
"unreachable" (legolas, two days — [[Fellowship-WiFi-Watchdog]]). Fallback to WiFi happens only when the wire
connection was never made (refused, timed out, no route): the route is pinned to WiFi for the TTL and the call is
retried once. A command that started and then failed is never run a second time.

**Status (2026-09-28): ON — `TABLET_ROUTE_PROBE=1` in `/opt/arrstack/tolkien.env` on rigby.** Proven through
Tolkien's own code on two nodes of every class (gandalf, aragorn · frodo, gimli · merry, pippin): ssh over the wire
with the tablet seeing `SSH_CLIENT=10.55.0.1`, and fellow-agent calls on `10.55.0.<octet>:8730`.

**If a class-A node (gandalf, aragorn) handshakes on the wire and then stalls at the ssh key exchange,** its
`rndis0` TX offload is on — the tablet's own gso/sg, not rigby's. `/fellowship/status` shows `offload: on`, the USB
link keeper counts that as a down link and restores it, and `usb-restore` always turns it off
([[Tablet-RNDIS-Setup-Configuration-Procedures]] § "Class-A USB TCP stall").

To change the setting: edit `TABLET_ROUTE_PROBE` in `tolkien.env` and recreate both consumers as root
(`Lab\bin\run_remote.py rigby <script>`; [[RUNBOOK-Orchestrator-Deploy-Procedure]]). The proof for turning it on is
one node of **each class**, never one node. The test gate always runs with `TABLET_ROUTE_PROBE=0`
(`run-tolkien-suite.sh`) so no test can reach a real wire.

### The USB link keeper

A tablet's USB network does not survive a reboot that bypasses Tolkien (any raw `adb reboot`, a power loss, a
crash); with `sys.usb.config=none` the node even disappears from rigby's USB bus. `tablet_control.keep_usb_links`,
a thread inside the order taker, checks every node every `TOLKIEN_USB_KEEP_EVERY` (300 s) over WiFi adb and
restores a node whose link is down — **two confirming passes** (`TOLKIEN_USB_KEEP_CONFIRM`), **only while the
order taker is idle**, at most once per `TOLKIEN_USB_KEEP_COOLDOWN` (1800 s) per node, every action posted to the
desk (`USB keeper: <node> restored | restore-failed`). A link is the INTERFACE — `rndis0` operstate `up` with the
node's own `10.55.0.<octet>` — not `sys.usb.config`: on the Android 11 configfs nodes that property stays `adb` on
a working link. An up interface whose TX offload reads `on` is also down (it corrupts the large frames the tablet
sends); a restore fixes the offload without rebuilding the gadget. "Cannot tell" (no adb, or offload `?`) neither
counts nor clears. First live action: boromir, 2026-09-27 21:17.

A USB unplug or a rigby reboot can also leave a node's USB link down; the keeper restores those the same way. The
gadget rebuild is keyed on what the gadget actually links, not only on the Android version: an A12 node gets
`setprop sys.usb.config rndis,adb`, and if no config slot links an rndis function after that, it gets the configfs
relink as well. Class A needs this: its init rc cannot replace an `ffs.adb` link, so the setprop alone left gandalf
and aragorn `restore-failed` from 2026-09-29 to 10-02 ([[Tablet-RNDIS-Setup-Configuration-Procedures]] § "Class A
… restore-failed"). A failed restore lists `gadget_slots` in its desk message.

### The charge keeper — a tablet "Charging" that never gains is rebooted before it dies (2026-10-12)

A tablet can read "Charging" and never gain until it dies, and a dead tablet is out of remote reach: it comes back on
rigby's USB as an empty HID gadget with no adb and no network, rigby's root ports cannot cut its power, and someone has
to hold its power button ([[Tablet-Architecture-Topology-Fleet-Status]] § power delivery). faramir died that way on
10-12 (7 % flat for 6 h); sam sat at 16 % flat for 10+ h at −9 mA and a plain reboot cured it (+339 mA on the same
500 mA port; cause unknown — NOT the `boost_work` threads, which every X606F shows).

`tablet_control.keep_charge`, in the same keeper thread, reads each node's last **2 h** of Tolkien telemetry
(`TOLKIEN_CHARGE_STALL_S`): every reading `Charging`, below 100 %, and no gain at all ⇒ stalled. Healthy nodes on the
same ports gained 7–11 points an hour once charging, so 2 h flat is far outside normal; the governor's knob flips
held nodes to `Discharging`, so they never qualify. **Cannot tell** (window not covered, no readings, or any stated
run in the window) never acts. A stalled node is rebooted through `reboot()` (which restores its USB link) **only
while the order taker is idle**, at most once per **24 h** per node (`TOLKIEN_CHARGE_COOLDOWN`); still stalled inside
that cooldown ⇒ one desk message, *"still not charging after a reboot … needs its cable/port checked at the tablet"*,
and no further reboots. Every action goes to the desk. Tests: `tests/test_usb_keeper.py` `ChargeVerdict` /
`ChargeKeeper` (shapes from the 10-12 telemetry).

### A busy lane waits — `LaneBusy`

An order that cannot start because of the fleet's state RIGHT NOW waits: it stays queued untouched, the taker runs
the next order, and it comes back after `TABLET_LANE_BUSY_RETRY` (30 s), before anything queued behind it. Log line:
`WAIT <order>: … -- left queued untouched; next order first`. Two conditions wait:
- enough nodes are healthy but some are still finishing a speculative item from the previous order;
- too few nodes are healthy (dozing, off the network, rebooting), but the order's **roster** — every node it could
  ever use after `require_arch` and `exclude_hosts`, healthy or not — is big enough (`… N of the M nodes it can use
  are healthy -- waiting for the fleet`).

Only an order that asks for more nodes than its roster can EVER supply is HELD (`… only M nodes can ever run it`),
because that is a property of the order the customer must fix. Before 2026-09-27 a short-handed window started and
went VOID (`hardbench-vlm-cal.20260927-193946.win.02`); until 2026-09-28 a node dozing went HELD and needed a
manual release slip (35 family-2 matrix windows when merry and pippin dozed).

## 13. rigby is its own unit — the rigby lane and order kind `llm` (one model served by Tolkien; Voltron)

**Kind `llm`** (`tolkien_llm.py`): one model served by llama-server under Tolkien, graded by the tablets' own
payload scorer inside the order. Two arms, named for what runs them:
- **`rigby`** — rigby itself (i7-3770 since 2026-08-28, 24 GB DDR3-1600, no AVX2; GT 730 2 GB; since 2026-10-13 a
  GTX 1060 6 GB, PCIe 3.0 x16 on a full-width riser since 2026-10-26) — CPU alone unless the order places layers on a card (fields below). Not Voltron.
- **`voltron`** — rigby AND every healthy wire tablet serving the model as one machine (`--rpc <tablets> -ngl 99`).
  Only this is Voltron. Backends are started over ssh and killed + verified over adb on every route.

```json
{"kind": "llm", "name": "hardbench-rigby-8-qwen7b-f2.20260928", "model": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
 "payload": "/ssdshared/tabfoundry-jobs/payloads/map_swarm_code_hard_f2.py",
 "tasks": ["dbg_running_total", "..."], "replicates": 3, "arms": ["rigby"]}
```
Models and binaries are read-only host mounts in rigby's compose: `/models` (`~/models`), `/llama/bin` (rigby's
x86 llama.cpp build), `/llama/arm64` (the tablets' `ggml-rpc-server`, same llama.cpp commit). A model not in
`~/models` cannot be ordered. Orders written as kind `voltron` with arms `local`/`pooled` are read as `llm`
`rigby`/`voltron`. The finish DM says `RIGBY …` or `RIGBY vs VOLTRON …`.

### rigby's PRODUCTION launch per model — `rigby_profiles.LAUNCH` (since 2026-10-30)

the owner 2026-10-30: *"in normal production the model settings wouldn't really be changing per order … we would want to
just be using our best settings."* Every rigby launch of a model (an `llm` order's rigby arm, a `chat.window`, the dump
probe) takes that model's profile through `tolkien_llm.launch_spec`; an order changes a setting only by naming it (a
TEST — an explicit `null` turns one off), and an order that places the model on another card or by hand takes none.
Intake judges the spec as it will launch. The menu reads the same file (`menu_fill.RIGBY_PLACEMENT["fields"]`), so
what is priced is what runs. Kirk's chat windows carry only the row's card and sampling (`chat_window.TOLKIEN_ORDER_FIELDS`)
— never the tuning, which would pin an older copy over a newer profile. Change a profile value only from a measurement.

### Placement and engine fields on a rigby-arm `llm` order (2026-10-13/14; all opt-in, unset = the model's profile above, else the earlier behaviour)

Validated at intake (`tolkien_llm.llm_defects`); every value lands in the results file's `cohort.arms[]` (the rows do
not carry them — read the cohort). Measured effects: [[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]], lever map
`lab-engine\research\split-inference\rigby_arsenal.json`.

| field | what it does | the measured default for the 1060 |
|---|---|---|
| `gpu` | the card BY NAME: `gt730` (default) / `gtx1060`, or a list (the fitter spreads over them — 1060 + 730 measured −68 %, r43) | `gtx1060` |
| `fit_target` | llama.cpp's own fitter on that card, margin in MiB (one int, or one per card); never with `gpu_layers` | `1024` |
| `gpu_layers` / `override_tensor` / `op_offload` | hand placement (the GT 730 attention recipe, runbook) | — |
| `build` | `next` (async-RPC Vulkan build) · `cuda` (the 1060's CUDA 11.4 build, 1060 only) · `hotstore` (llama.cpp PR #26563's expert hot store + the lab block-split patch, CUDA 11.4, 1060 only, `/llama/hotstore`) · unset = live Vulkan | `cuda` |
| `expert_hot` | `hotstore` build only: `{slots: -1 (fit to free VRAM) or 1..512, dwell?, hyst?}` → `-ehs` / `--expert-dwell` / `--expert-hyst` | unset — closed on rigby (r61–r64: best case +1.5 % write, −60–70 % read; x16 10-27: no write gain) |
| `moe_cache_mib` | llama.cpp's MERGED expert cache (PR #29887, a different mechanism from `expert_hot`): `--moe-cache-mib N`, 1..16384 MiB of VRAM; `cuda` / `next` build, rigby arm, with a card placement. Since 2026-10-30. Every row carries `moe_cache_mib` and `engine_report` (the server's own load-time lines about the cache - what is PRESENT, not just asked for) | unset — LOSES on rigby's 1060 at every size, −25 to −65 % write (2026-10-30, `moecache-x16-*`, synthesis § 8) |
| `ubatch` | llama-server `-ub` (and `-b` above 2048) | `2048` (read 27 → 83 tok/s, write −1 %) |
| `parallel` | `-np` | `1` (no measurable effect on this model) |
| `threads` | `-t` (default 4) | 4 (8 = no effect, r47) |
| `load_mode` | `--load-mode` (`none` loads instead of mapping) | unset (`none` = no effect without AVX2, r46) |
| `spec_type` + `draft_max` | speculation; `draft_max` caps a round and the lookup with it (an n-gram lookup longer than its draft drafts NOTHING); `draft-mtp` = the model's own MTP head (needs an MTP GGUF; the head shares the loaded model, so no draft placement) | **`draft-mtp`, `draft_max 2`** with the MTP model (r67/r68: +19–24 % write, 94–100 % acceptance on code, read −8–10 %). n-gram LOSES here (r52a −34 %, 8-token drafts at 61 %) |
| `op_offload_min_batch` | `GGML_OP_OFFLOAD_MIN_BATCH`: prompt batches below it are not streamed to the card | **`256`** (r66, on the old x1 riser: a 47-token read 6.5 → 3.1 s; big reads and write unchanged). ⚠ Not re-measured on the x16 riser (2026-10-26): with 16× the link the best threshold is likely lower |
| `engine_env` | allow-listed instrument switches: `GGML_SCHED_PROFILE` (lab profiler), `GGML_VK_PERF_LOGGER`, `GGML_VK_DISABLE_FUSION`, `GGML_CUDA_DISABLE_GRAPHS`, `LAB_CUDA_GRAPHS_PRE_VOLTA`; hotstore build: `LAB_HOTSTORE_BLOCK` (block split), `LLAMA_EXPERT_HITRATE` (per-token hit rate — syncs, telemetry only), `LLAMA_EXPERT_DEBUG`, and `LAB_EXPERT_HOT_RANKING` = a ranking FILE (absolute path under `/ssdshared/tabfoundry-jobs/payloads/` only; `lab-engine\research\split-inference\expert_ranking.py`); the server log's tail ships in the cohort | `LAB_CUDA_GRAPHS_PRE_VOLTA: 1` = +1.5 % (r54 vs the rebuilt-binary control r55) |
| `kepler_flags` | the GT 730's shader flags on a placement without it (a control) | — |

**On a `warm` order (arms `["warm"]`, the Fellowship serving from resident servers):** `warm_gt730: true` adds
rigby's GT 730 as one more warm node (opt-in: WHETHER warm work uses it is #34 placement policy — the priced menu →
Spock's table, [[34-Phase-7-Placement-Policy-Design]] — never a factory default) — served locally by the live Vulkan build with the whole model on the card (`-ngl 99`,
Kepler flags, 2 threads, port 8095), the model's GGUF must also be in rigby's models mount (`/home/owner/models`). The
730 has its own claim: a warm node holds it non-blocking (busy ⇒ the node is skipped, the order runs on the tablets),
and a rigby-lane order placed ON the 730 waits (`LaneBusy`), while 1060-only orders (`build cuda`) run beside it.
Measured r76 (10-15): the 730 served 0.8B turns at median 10.1 tok/s (fastest node; tablets 6.5–6.9); with it serving,
rigby's A3B wrote ~6 % slower (tablets alone beside it: 0–2 %). Since 10-15 every warm node SERVES THE MOMENT ITS OWN
SERVER ANSWERS (no waiting for the slowest or a dead node), and the deck's x/y counts one control turn per node.

Every fitted launch also records the fitter's report (`cohort.arms[].fit_placement`, from `llama-fit-params`).

**The standard 1060 `llm` order (2026-10-14, after r66–r69):** `model: Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf` (Unsloth's MTP
build — r69 KLD vs the plain file 0.0159, same top token 92.6 %, PPL 26.70 vs 26.83, i.e. equivalent), `gpu: gtx1060`,
`fit_target: 1024`, `build: cuda`, `ubatch: 2048`, `parallel: 1`, `spec_type: draft-mtp`, `draft_max: 2`,
`op_offload_min_batch: 256`, `engine_env: {LAB_CUDA_GRAPHS_PRE_VOLTA: 1}` → ~16.9 tok/s write on code.
Template: `hard-bench\orders\llm-36-r67a-mtp-draft2.20261014.tjob.json` + `op_offload_min_batch`.
**An A1 (agentic, `a1_payload.py`) order on it adds `turn_cap: 20480`**, the runaway guard's per-call cap. It was
measured on this model: acting turns are ≤ 14,888 tokens and runaways ≥ 25,447. Design:
[[34-A1-Agentic-Build-Family-Spec]] § "Runaway guard".

### QUICK RESUME probe — the `dump_probe` order: save rigby's slot, restart, continue a stopped answer (#34 B11, 2026-10-17)

An `llm` order with a `dump_probe` field and no payload/tasks measures the owner's quick resume (his standing reference:
Xbox quick resume — dump the exact live state, pick it up after a restart) on rigby's PRODUCTION launch
(`tolkien_llm.rigby_server_kwargs`, pinned equal to `run_arm`'s; `--slot-save-path` = `<state>/slots`). Rigby alone,
no draft model. Fields: `p` (question tokens; `doc` = a library `.md` path under the docs mount makes the question an
open question about that real document — use it, repeated boilerplate cannot move), `cut` (answer tokens before the
stop), `n`, `mode`, `temperature`, `seed`. Modes:
- `question` — the turn-boundary dump: save the question with no answer, restart, restore, re-ask (30k context: 3.1 s
  with the dump vs 296 s without).
- `continue` — raw `/completion` token ids: cut the answer, save, restart, restore, continue from the exact ids; a
  no-dump control. Result (round 2, real text): **lossless 384/384 with MTP off at temp 0**; temp 0.6 and MTP on
  diverge on stock llama.cpp (its slot file holds neither the sampler's random stream nor the MTP drafter's state —
  the `chat` mode and the lab engine patches below close both: #3 the drafter, #2 the random stream).
- `chat` — the same through the chat API the orders use, stopped the way `chat()` stops (leaving the stream). Exact ids
  come from the slot FILE (`slot_file_tokens`: u32 magic `ggsq`, u32 version 4, u32 n, int32 ids, then the state). It
  reports whether the half answer, rebuilt as a `continue_final_message` prefill (`prefill_message`), RENDERS to the
  saved ids (`render_match`), and adds an ORACLE continue with the one token the slot never holds (the last token the
  server sampled is decoded only on the next step; a continue that EQUALS the cache makes a hybrid roll back to its
  prompt-end checkpoint and re-read the half answer — `server-context.cpp` [TAG_PROMPT_LOGITS]). On a lab-patched
  engine the save answers `pending_token` (patch #0, so the oracle is not needed) and, with patch #3
  (`slot_resume_patch.py`), `lab_drafter_bytes` / `lab_replay`; when the restore answers `lab_resume_armed`, arm
  **4c** runs the same continue with `lab_resume: true` — the engine takes the interrupted run's NEXT STEP (the
  pending token decoded with its drafts as one batch) and reads 0 tokens (`resume_lossless`, `resume_first_diff`,
  `resume_reread_tokens`). Step 4 stays the plain pending-token continue, so 4 vs 4c is one variable. 2026-10-21,
  MTP on, temp 0: 4c 383/383 and 511/511 identical; step 4 drifts at 358 / 234. With patch #2 (`slot_rng_patch.py`,
  the save also carries the sampler's random state, `lab_rng_bytes`): temp 0.6, MTP off 383/383 and MTP on 511/511
  ([[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] B11). The production quick resume (`continue_for`) sends the same flag
  when its restore was armed.
Submit as any `.tjob.json` (template: the standard 1060 order above, plus `dump_probe`; keep `spec_type`/`draft_max`
to test the production MTP launch, drop them for MTP off); the result row carries every metric and the desk DM names lossless / render / re-read. Live state of
the build: [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] B11.

### PLACED orders — the desk sends "the Fellowship" ONE order, Tolkien picks rigby or a warm tablet (`placement`, `fellowship.placed`, `warm_nodes`; 2026-10-17)

#34 Phase 7 design § D, the "two routers" (the owner 2026-10-07): **Spock picks the destination, Tolkien picks the worker.**
A customer that lets the placement table choose (the A1 placed delegate) sends the desk its GPU template AND a
**Fellowship form** — `{"spec": <llm order with no arms / model / settings>, "requires": ["fellowship.placed"]}` in the
`/p20/order` field `fellowship`. The table ranks "the Fellowship" as one destination at its best worker row (its boxes
`rigby` and `fellowship` = the tablet pool); if it wins, the desk sends the form with `placement: {family, tier, lens}`.
- **Tolkien resolves it when it COLLECTS it from the desk, before claiming** (`tolkien_llm.resolve_placed`): it chooses
  only within the Fellowship's RUN of the table — the Fellowship rows from the table's first Fellowship row (the row
  Spock ranked "the Fellowship" by) up to the next row that is another destination's (`policy_rows`). In the
  `fellowship` tier that is rigby then the tablets; in the `rigby` rung it is rigby alone (its tablet row is overflow
  ranked BEHIND the GPU boxes — 2026-10-17 a rigby-rung piece with the 1060 gone fell to it before this rule). Within
  the run, the first row whose worker is PRESENT — rigby: `rigby.llm`
  declared, the model file in rigby's models mount, its build mounted, and the row's card VISIBLE TO THAT BUILD
  (`card_usable` = the launch's own `--list-devices` lookup; `/proc/driver/nvidia/gpus` still lists a card that has
  fallen off the bus — 2026-10-17 a piece resolved to the dropped 1060 that way and shipped VOID); a tablet row: a
  healthy tablet (`tablet_cpu`).
- **Any rigby order whose card its build cannot see WAITS** (since 2026-10-17, `tablet_runner._wait_for_cards`): queued
  and untouched, the lane runs its next order, one `RIGBY CARD NOT VISIBLE` DM per drop; after the reboot
  ([[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]] § Xid 79) the waiting orders run by themselves. Before, every 1060
  order behind a drop launched and shipped VOID. **The reboot is rigby's CARD KEEPER's** (a root timer on rigby's host,
  2026-10-17): while an order waits, Tolkien keeps `card_wanted.json` fresh in its state dir; the keeper reboots rigby
  once both lanes are idle, at most once per drop, and gives up (Tolkien's DM then quotes its reason) if the card is
  still missing after its reboot. The drop DM reads the keeper's heartbeat (`card_keeper.json`) and says SILENT when no
  keeper is checking in — full rules in the runbook section above. That row's `order` fields fill the spec (`menu_policy` stamps
  them from `menu_fill`'s own builders: a rigby row = **the standard 1060 order**; a tablet row = `arms ["warm"]`,
  `warm_nodes: 1`, ctx 16,384), and `placement_row` records the row plus every row skipped and why. The queue only
  ever holds a resolved spec, so its lane follows from the worker.
- **Busy is never a skip.** A busy rigby is a WAIT in its lane like any order. **No worker of the run present** (1060
  off the bus, no healthy tablet) = the order is **HANDED BACK** unclaimed (`POST /p20/jobs/<id>/handback`, the GPU
  foundry's own door; log `fleet desk: <name> HANDED BACK - <why>`): the desk re-places it from its route — the
  template's requires and the Fellowship form, the Fellowship cooled off 15 min — on the table's next destination, or
  HOLDS it. A refused hand-back leaves it unclaimed and asked again next pass (`WAITS (hand-back refused)`, once per
  reason). **No Fellowship row for the key at all** = claimed and FAILED with the reason (a property of the order
  against the table; no wait can change it).
- **`warm_nodes: N`** (a warm order only): N warm nodes, the FASTEST measured free ones (`scheduler.TABLETS` GFLOP/s,
  after the class rules) — one answer never loads a model on every tablet. Short of N free: WAIT if some are finishing,
  else the roster rule (WAIT / HOLD).
- **Guard:** an order with `placement` and no `arms` is refused at intake (`llm_defects`) — `normalize()`'s legacy
  default (rigby + Voltron) must never run an order whose worker was never chosen.
- **Declared by detection:** `fellowship.placed` = the placement table is readable at
  `/docs/Datasets/Project34-Menu/policy.json` (`TOLKIEN_POLICY_TABLE`); without it the desk never sends a form here.
- Tests: `tests/test_placed_orders.py` (Tolkien), `tests/test_scheduler_policy.FellowshipDestination` +
  `tests/test_fleet_desk.PolicyFieldsReachPlacement` (desk), `test_menu_policy.FellowshipOrders` (table),
  `a1/tests/test_a1_delegate.Placed` (the customer).

### Before submitting a rigby / Fellowship order — check what will refuse, void or already answer it

the owner, 2026-10-14, after two orders were void in 30 s: *"avoid doing stuff that would be already known to fail, like why
would you even try to submit orders that dont have things fitting? We have MONTHS of data, please use that."* Run the
CHECK, not the order, whenever a check is cheap and decisive:
1. **The rigby claim is WHOLE-BOX.** Any order that touches rigby (its CPU, either card, or a stage on rigby) holds the
   claim in EITHER lane (`RIGBY_LOCK` in `tablet_runner.py`); a second one WAITs (`rigby is busy with …`). Two jobs on
   rigby never run side by side — a "co-location" test needs a factory change first.
2. **Stage-memory preflight sizes against each tablet's CURRENT free memory** (`stage_need_mib`: weights + KV + growth
   + 300 MiB reserve; battery floor 50 %). A spec that fit days ago is not proof it fits now — read the live state (deck,
   telemetry `mem_avail_mb` / `battery_pct`) before resubmitting an old staged spec (r65b: aragorn / gandalf refused).
3. **The card is named, and it can be gone.** The 1060 has dropped off the bus (Xid 79) even at idle (four drops by
   2026-10-17). Confirm it with the CUDA build's `--list-devices` or host `nvidia-smi -L` — NOT
   `/proc/driver/nvidia/gpus/*/information`, which still lists a fallen card. Since 2026-10-17 an order behind a drop
   WAITS queued (one `RIGBY CARD NOT VISIBLE` DM) instead of shipping VOID, so it costs time, not the order; recovery is
   a rigby reboot, which rigby's card keeper does by itself once both lanes are idle
   ([[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]] § the 1060's Xid 79).
4. **Prior results may already answer it.** Search the outbox / register / lever map first; drop arms that repeat a
   known baseline (working rule 9 of [[36-Split-Inference-Helpers-Read-and-Draft-Design]]).
5. **Validate in the LIVE container** (`docker exec -i tablet-foundry python -` + `tf.spec_defects`, with a known-bad
   positive control) and read back the acceptance and pickup order (`docker logs tablet-foundry`) — a slip or an order is
   a request, the queue is the fact.
6. **Quote ETAs from Softserve's clock** (`date`) — rigby's log stamps are UTC.

**Tool payloads (2026-09-30, `4d2867c`).** A payload with `score_tool` (`map_tool_swarm_hard.py`) makes the order a
TOOL order: each prompt is the payload's `PREAMBLE` around the task's `req`, the answer is constrained by the payload's
own `GBNF` (sent as the request's `grammar`, the tablets' constraint), graded by `score_tool` against the task's
`gold_tool`/`gold_args`, and the control is the payload's own (`control_tool`). `load_scorer` proves the scorer on the
control's gold call before any model time. Same 24 tasks, preamble and golds as the GPU tool cells (verified equal).
**Graded field:** an answer filed under `thinking` with empty `content` is graded from `thinking` and the row says so
(`graded_field`), the lab's content-or-thinking extraction rule. ⚠ A thinking model under a grammar may be forced to
skip its reasoning — read the `thinking` field of a reasoning-on tool cell before comparing it to reasoning-off.
**The control is never priced out (`563c8e4`):** `price_ceiling_s` applies to graded answers only; a ceiling-cut control
read as a FAILED control and voided rigby-20 (all 30 MoE f1 answers priced out at 86 s, control included).
**At-cap brake (`at_cap_brake`, 2026-10-03):** an llm order may state a share in (0, 1]; after each graded answer
`tolkien_llm.brake_verdict` counts the answers that ran to the order's context (`finish_reason` "length" — rigby asks
`max_tokens -1`, so "length" is the context filling; token sums cannot tell, a cached prompt reports `prompt_tokens` 1
and the server stops just short of ctx) and stops the arm once the Wilson 95% lower bound of that share reaches the
stated value (2 of 2 does; 1 of 2 does not). Every finished answer counts, graded or not; controls never count.
**A `map.window` arm must state `distinct_workers`** (refused at intake since 2026-10-03; before, it was accepted and
then HELD on `KeyError`): a window is a set of pinned distinct-tablet cohorts. Work with no cohort — independent
single-tablet runs every free tablet should pull — is a plain `map` order. The arm ends like a finished one (rows shipped, `cohort.arms[].braked` carries
the evidence), not as a stopped/held order. P34's menu filler states it on every unceiled-family order (W1) — the same
rule `infer_ollama --at-cap-brake` applies on the GPU factories. Tests `tests/test_llm.py::AtCapBrake`.
**Model manifest:** the rigby lane writes `E:\SSDShared\tabfoundry-jobs\rigby-models.json` (`/ssdshared/…`,
`tolkien_llm.publish_models`) whenever `~/models` changes — how desks that cannot see rigby's disk (the P34 menu filler)
know what rigby can serve. Projector files (`mmproj*`) excluded.

**Answers end on silence, never on a clock (2026-09-29, `3311df4`).** Each answer streams from llama-server; the
only failure is `TOLKIEN_LLM_IDLE_S` (default 600 s, the same window as the GPU fleet's `infer_ollama`) with no byte.
The old 5,400 s whole-answer clock killed 3 real Qwen3.6-35B-A3B answers (order 16, `TimeoutError`). The context
(`ctx`) still bounds a runaway thinking answer — read `finish_reason` / `gen_tokens` before believing a score.
**Re-running only what a stopped order never finished:** `"items": [["task", replicate], ...]` runs exactly those
pairs, each at its original seed (seed = replicate); totals and "complete" follow the list. Build it from the stopped
results file (`rows` with `ran`), keep every other field of the original spec, and score both deliveries together.
**Pause, deploy, resume (since 2026-10-02).** An `llm` order that runs again under its own name resumes from
`state/<order>.checkpoint.jsonl` (`tolkien_llm.resume_rows`): answered and priced-out pairs are kept (`resumed: true`),
stopped / errored / missing pairs and the control run again — the GPU fleet's `infer_ollama` rule. So a deploy never
has to wait for a rigby order: `<order>.stop.json` (it ships what finished, `stopped:true`) → deploy →
`<order>.ctl.json {"action":"requeue"}`, which puts the newest archived spec back in the queue under its own name.
A recreate mid-order resumes too (the spec never left the queue). A complete order archives its checkpoint
(`archive/<order>.checkpoint.jsonl.<stamp>`) so a later order of the same name starts fresh. Tablet `map`/window
orders do not resume: their rows carry no stable pair key.

**Two lanes (2026-09-28).** An order whose arms are `rigby` only runs in the **rigby lane**, beside whatever the
fleet lane is doing with the tablets. Everything else — tablet orders and any order with a `voltron` arm — runs in
the **fleet lane**. rigby is ONE unit: the rigby lane's order and a Voltron order both hold the same rigby lock, and
whichever finds it held **WAITs** (`WAIT … rigby is busy with <order>`), never HELD.
- **Claims:** `state/CURRENT_ORDER.json` (fleet lane — lists EVERY running fleet order under `orders`, oldest first, with the
  tablets each holds, and repeats the oldest at the top level; read it with `tablet_foundry.claim_orders`), `state/CURRENT_ORDER.rigby.json` (rigby lane).
- **Tile:** the Fellowship tile carries both — `rigby: <order> n/m` in the Phase row beside tablet work, or as the
  Order itself when the tablets are idle.
- **Watchdog:** watches every running fleet order (the oldest on alert key `stall`, each other on `stall-<order>`) and the rigby
  lane's claim (`stall-rigby`), the same 20-min rule. The tile shows several fleet orders as one line (`N orders: a + b`).
- **Heartbeat:** an `llm` order writes it from its own progress and model load (before 2026-09-28 it did not, and a
  long Voltron arm drew false "order taker SILENT" alarms).
- **Cleanup:** an order's retire removes only its OWN local staging dirs (`tablet_fulfil.clear_stage_dirs`, the
  same naming rule that creates them). Until `d34f349` a retire cleared every `stage_*` dir — harmless with one
  lane, but on 2026-09-28 15:22 the rigby lane's order retiring deleted a running tablet window's staging and five
  tablets failed to fetch their results (the window was correctly voided).
- **Slips:** every routing pass names every running order in both lanes, so a stop reaches the lane it names and a
  hold never renames the other lane's running spec. Inbox intake runs during runs, so an order submitted mid-run is
  queued within seconds and a cancel slip can reach it.

**Co-location, measured 2026-09-28 (one probe, preliminary):** the same tablet window (f2 matrix window 44, same
items and seeds) 3× with rigby idle and 3× while the rigby lane ran Qwen2.5-7B. Window wall 62–64 s both ways; per
tablet, the same item took the same time to within 0.5 s; rigby itself held 4.7 tok/s during the windows; answers
60/60 identical. A busy rigby does not slow the tablets, so the two lanes run side by side by default. Reducer:
`phase5-prep\commission-artifacts\hard-bench\coloc_probe_reduce.py` (compares each tablet against itself — Tolkien
picks a window's tablets from a hash of the window NAME, so pooled medians would mix "rigby busy" with "which
tablet"). Re-measure before pairing a heavier rigby model (14B+) with tablet work that leans on rigby's network path.

**What runs where, measured** ([[34-Hard-Bench-Order-Spec]] § rigby alone): Coder-3B answers in ~6 s on rigby
alone and ~2–3 min as Voltron (1.2 tok/s across rigby + 10 tablets). At every size rigby can hold, rigby alone is
faster; Voltron's only unique job is a model too big for rigby (parked, [[34-Shape-Catalogue]] S5).

## Monitoring: the homepage tile

A factory is not considered complete until its homepage monitoring wiring is in place.

The **Fellowship - Tablets** tile resides in the Home AI group alongside the three GPU factories. It reads run-state data from `http://orchestrator:8099/p20/factory/fellowship`.

### Tile field set

All four factory tiles (the tablet fleet and the three GPU factories) display the same fields:

* Fleet/Card (labeled "Card" for GPU factories; labeled "Fleet" for the tablet cluster)
* Order
* Phase
* Progress
* Rate
* ETA
* Order taker (`reporting`)
* Daemon (`health`)

The **Order taker** field distinguishes between a legitimately idle factory and one whose order-taker process has failed. Both states present as `idle` with blank fields in the base run-state record; the tile derives the distinction from the age of the run-state record on the idle path.

The **Daemon** field reports health status, consistent across all factories (see §10).

### Dashboard scope and data separation

A dashboard tile answers *what is this factory doing right now*. It does not display capability sets. Capability strings are excluded from the tile because they change infrequently (approximately monthly) and their length obscures real-time operational rows on standard screens.

Capabilities continue to ride the run-state push and determine routing decisions. To retrieve current capabilities, query `/p20/factories`.

### Factory identification logic

Both `GET /p20/factory/{host}` and `POST /fleet/run-state` gate access on "is this a known factory" using separate checks:

1.  Publish path: consults `NON_CARD_FACTORIES`.
2.  Tile path: consults `scheduler.CARDS` (which identifies hosts owning GPUs) AND `NON_CARD_FACTORIES`.

Any new factory-recognizing check must consult both sets to avoid mismatches between publication and display status. Grep for `NON_CARD_FACTORIES` when adding or modifying factory identification logic.

## Related Documentation and Code Locations

### Reference Documents
See the following for operational and design context:

- **[[Tablet-Cluster-INDEX]]**: Primary entry point.
- **[[Fellowship-Crunch-Integration]]**: Hardware wiring, scheduler integration, and per-node specifications.
- **`P20-FOUNDRY-RUNNER-PLAN`**: The GPU order-taker architecture mirrored by this component.
- **[[HAI-Orchestrator-Internals]]**: Contracts for `/fleet/run-state` and `/fleet/rescue-state`.
- **[[RUNBOOK]]**: SSH access and elevation procedures.

### Source Code
All source code resides in `E:\SSDShared\tolkien-build\` (the Fellowship's own build lane since the 2026-08-13 split; this line said `orchestrator-build` until 2026-09-27), is built on Sundae from `/opt/arrstack/tolkien/`, and runs on rigby — procedure in [[RUNBOOK-Orchestrator-Deploy-Procedure]] § TWO BUILD LANES:

- **`tablet_foundry.py`**: Desk layer (intake, health checks, capabilities, inbox/outbox, DM).
- **`tablet_fulfil.py`**: Line layer (fan-out, speculative re-execution, control and coverage verdicts).
- **`tablet_runner.py`**: Main execution loop.
- **`tablet_watch.py`**: Watchdog process.
