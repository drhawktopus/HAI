# Tablet Cluster ("The Fellowship") — START HERE

## ⚠ VOCABULARY — \"Fleet\" and \"Fellowship\" are DIFFERENT THINGS, not synonyms

| word | what it means | who runs it |
|---|---|---|
| **Fellowship** | the **eleven tablets** — gandalf, frodo, sam, merry, pippin, legolas, aragorn, **sméagol** (armv7l/32-bit; joined 2026-09-03), **gimli**, **boromir**, **faramir** (all class-B X606F; joined 2026-09-04) | **TOLKIEN** (head; distributes work *inside* the Fellowship) |
| **Fleet** | the **starship fleet** — the x86 GPU boxes (Mothership, BigHonker, Softserve) | **KIRK** (orchestrator) + **SPOCK** (scheduler) |
| **rigby's factory** = **Tolkien's factory** (synonyms, 2026-10-22) | the FOURTH factory, on **rigby**: rigby's own GPUs (GTX 1060 + GT 730) AND the Fellowship tablets — a GPU factory too, so "the three GPU boxes" is never the parity line (there are four) | **TOLKIEN** (the factory controller for rigby's GPUs and the Fellowship) |

the owner types either name (*"I don't want to type that out every time"*); written out, it is "rigby's factory (Tolkien)".
"Tolkien's factory" dates from when Tolkien lived on Softserve/Sundae with the tablets wired there. **Synonyms only at the
FACTORY level** (the owner: *"there's nuance though"*): a bare **"rigby"** usually means the BOX (its GPUs, disks, Docker) or
its GPU LANE ("a rigby order" runs on the 1060, not the tablets); a bare **"Tolkien"** usually means the CONTROLLER (the
order taker choosing rigby's GPUs vs the tablets); **the Fellowship** is the tablets only. The factory is bigger than the
box (the tablets are not inside rigby); the box is more than the factory (it hosts Tolkien's containers).
**⚠ Where hardware SITS is not which lane it WORKS in — the GT 730** (the owner 2026-10-22: *"we can use the 730 as a wildly
overpowered tablet"*): it sits in rigby but works in the **Fellowship pool** as an eleventh-plus worker (`warm_gt730`,
the fastest warm node; [[34-Phase-7-Placement-Policy-Design]] `tablets` rung = "the tablet + GT 730 pool";
[[36-Split-Inference-Helpers-Read-and-Draft-Design]]). **rigby's GPU lane = the GTX 1060.** So as a placement pool,
"the Fellowship" = the tablets + the 730; "a rigby order" = the 1060.

Use \"Fellowship-wide\", \"the Fellowship's gflops\", and \"the Fellowship's tools\" for anything with a tablet in it. Never use \"fleet\" for any item containing a tablet.

From the lab scheduler's point of view, **the whole Fellowship is ONE placement target**, which requires strict separation between these two names to prevent misplacement.

## Document inventory and reading order

| # | Doc | Contents |
|---|---|---|
| 1a | [[Fellowship-Purpose-Architecture-and-Network-Constraints]] | What the cluster is: purpose, architecture, network constraints, the wired-USB-vs-WiFi tradeoff. |
| 1b | [[Fellowship-Workload-Classification-and-Scheduling-Logic]] | Workload classification + `scheduler.py` integration; which jobs fit; worker-count sizing. |
| 1c | [[Fellowship-Performance-Baselines-and-Benchmarking]] | Performance baselines, benchmark reference values, the test method, s/MP metric, OCR throughput. |
| 1d | [[Fellowship-Operations-Connectivity-and-Reliability]] | Head-node identity/keys, SSH access, liveness signal, freeze mitigation, DRAM-degradation detection. |
| 1e | [[Fellowship-Build-Configuration-and-Status]] | Building aarch64 binaries, cluster build status, related docs. |
| 2 | [[Fellowship-Order-Taker-Design]] | Factory layer. Order specification, fulfilment, capability publishing; order submission (§9); watchdog (§10); homepage tile (§11). |
| 4 | [[Tablet-Job-Whisper-Subtitles]] | Proven workload end-to-end template. Use to determine if a job fits the cluster. |
| 5a | [[RUNBOOK-Fellowship-Tablet-Intake]] | **Canonical operator entry point for a new tablet.** Rigby-hosted, resumable detection → hardware/hash gate → physical unlock prompts → family-specific flash → full provisioning → production registration/acceptance. Distinguishes the recovered #5/#6 harness from tablet #7's hardcoded handoff scripts. |
| 5 | [[HANDOFF-tablet-lineage-flash]] | Flashing, GSI, and bootloader procedures. Required for re-imaging units. |
| 5b | [[FLASH-NEW-TABLET-gandalf-class-X6C6F-from-rigby]] | Flashing procedure for gandalf-class (TB-X6C6F) units. A/B partitions with dynamic `super`; `system` installed via fastbootd, not classic fastboot. Driven from rigby (Ubuntu); Windows WinUSB/driver material does not apply. Supersedes §2 of doc 5 for this device family. |
| 6a | [[Tablet-Architecture-Topology-Fleet-Status]] | USB-networking architecture, topology, RNDIS fleet status. Every tablet has a wire address on `10.55.0.11`–`.21` (the roster is the Fellowship row above, never a count here); the segment does not survive a reboot. |
| 6b | [[Tablet-RNDIS-Setup-Configuration-Procedures]] | Bring-up procedure; set `sys.usb.config` over WiFi ADB only; RNDIS bring-up traps (ADB/RNDIS conflict, netd routing over WiFi). |
| 6c | [[Tablet-Performance-Benchmarks-Bus-Contention]] | Latency/throughput (7–11× lower node-to-node delay, 1.83× on bulk coupled work), protocol overhead, bus contention, peer traffic. |
| 6d | [[Tablet-Instrumentation-Maintenance]] | Instruments (`Lab\bin\`); bridge restoration after a rigby reboot (host `br0` self-heals via `fellowship-bridge.service`; tablet side via `usbnet_up.py --apply`). |
| 7 | [[Fellowship-WiFi-Watchdog]] | WiFi watchdog configuration. 12-hourly re-association blip (auto-recovery ~40 s); scan tier; Softserve re-armer; phantom-process check. Use when a node is unreachable or the detector is silent. |
| 2a | [[Fellowship-Inference-Output-Format]] | **Reading the model's answer from `llama-cli` / `llama-mtmd-cli` stdout** — measured boundaries (prompt echo truncated at 500 characters, stats line, mtmd answer-only stdout), the shared `cli_answer` extractor, and when to re-measure. Use before writing or changing any payload that parses model output. |
| 8 | [[Fellowship-Node-Provisioning]] | Node provisioning. Durable ADB (`persist.adb.tcp.port`); Termux node build; offline packages; benchmark gotchas (OpenBLAS threading, forkserver). Use when adding or rebuilding a node. |

## Journal references

Session records for tablet builds and configuration:
* 2026-08-03-claude-tablets (build and flashing)
* 2026-08-05-claude
* 2026-08-06-claude
* 2026-08-07-claude (freeze root causes, worker sweeps, order taker)

## Quick Reference: Cluster Composition and Constraints

| Component | Specification |
|---|---|
| **Nodes** | **Eleven tablets** in `TABLET_NET` (running tolkien container) + `scheduler.TABLETS` (read from the device roster file's `tablets` list, `Z:\Documentation\State\device-roster.json`). All hold RS300 DHCP reservations (the four 2026-09 additions were lazy-locked to their initial DHCP-assigned IPs by the owner 2026-09-04 AM — no moves) and resolve as `<name>.lan`.<br>• **gandalf**: `<lan-address>`, HA1WKD37 (8c/2.79G, TB-X6C6F, LOS 19.1)<br>• **frodo**: `.72`, HA1998VW (8c/1.76G, TB-X606F, LOS 18.1)<br>• **sam**: `.73`, HA15WNRT (8c/1.76G, TB-X606F)<br>• **merry**: `.74`, HA10FXCV (4c/1.82G, TB-X505F, LOS 19.1)<br>• **pippin**: `.75`, HA10F5DZ (4c/1.82G, TB-X505F, LOS 19.1)<br>• **legolas**: `.78`, HA197X4L (8c/1.76G, TB-X606F, LOS 18.1)<br>• **aragorn**: `.80`, HA1TXV62 (8c/2.79G, TB-X6C6F, LOS 19.1)<br>• **sméagol**: **`10.55.0.18`** (USB-RNDIS), `da2dec72221ccc9a` (4c/1.39G, **Samsung SM-T560NU `gtelwifiue`, LOS 18.1, armv7l/32-bit**). Truthful capabilities: `task:ping` + `task:payload` + `arch:armv7l`. Explicitly **unavailable**: `task:detect`, `task:burst`, `ncnn_detect`, `hwdec`, arm64-native payloads. Router reservation for the wlan0 MAC is set for when Sméagol moves off USB-only.<br>• **gimli**: `<lan-address>` (reserved), MJ0ETXPE (8c, TB-X606F, class-B)<br>• **boromir**: `<lan-address>` (reserved), HA198QW5 (8c, TB-X606F, class-B)<br>• **faramir**: `<lan-address>` (reserved), MJ0EW27F (8c, TB-X606F, class-B). Rebooted as the class-B canary 2026-09-04: ADB/Termux:Boot/detector/wake-lock/doze/SSH all returned automatically. |
| **USB Segment** | Subnet `10.55.0.11`–`.21` on rigby's `br0` — every tablet; the wire is the main path and WiFi the per-node fallback (Tolkien's wire-first routing, [[Fellowship-Order-Taker-Design]] §12). Sméagol at `.18` is Samsung SM-T560NU, RNDIS from Termux via a rigby-side reassert on boot. A reboot drops a node's USB function; Tolkien's USB keeper restores it automatically. See [[Tablet-Architecture-Topology-Fleet-Status]]. |
| **Head Node** | Softserve at `E:\SSDShared\Lab\fellowship\` (contains key, harness, adb). Sundae mounts same path via CIFS at `/mnt/ssdshared/...`. |
| **Access** | SSH: `ssh -i tabcluster_ed25519 -p 8022 <ip>`. Use explicit IP and key; aliases exist only in Softserve's `~/.ssh/config`, not in containers.<br>ADB: Port `:5555` on all nodes. No pairing required; persists across reboot. |
| **DNS Dependency** | `scheduler.tablet_alive()` resolves `<host>.lan`, not the IP in `TABLET_NET`. New nodes require Pi-hole records in `hosts/lan.list`; absence causes fail-closed exclusion from Fellowship.<br>**Constraint:** Never leave `.bak` files in `/etc/pihole/hosts/`. Pi-hole loads all files in that directory; stale backups cause resolution errors. |
| **Worker Scaling** | Formula: `min(cores, MemAvailable_GB / 0.15)`.<br>• 8-core aarch64 nodes (gandalf, frodo, sam, legolas, aragorn, **gimli, boromir, faramir**): 7 or 8 workers depending on RAM.<br>• 4-core nodes (merry, pippin): 4 workers.<br>• **Sméagol** (4-core armv7l/32-bit, 1.39 GB): ping + architecture-neutral payloads only. Coverage-critical items MUST be delegated to an aarch64 node — the scheduler already redelegates cleanly (proven 2026-09-03 sméagol→gandalf, full coverage).<br>See Integration §10 for measurements. |
| **Compute Model** | Independent task farm only. Do not run single MPI jobs across the Fellowship; distributed MPI retains ~16% performance vs independent tasks on this fleet due to small N over WiFi. See `RESULTS-hpl.md`.<br>**Note:** This rule applies to the Tablet Cluster hardware specifically; other hardware (e.g., basement x86) may retain 83% distributed performance. Measure before applying elsewhere. |
| **Benchmarking (`burn.py`)** | **Limitation:** `burn.py` underreports SMT node performance by ~38-44%. It spawns one process per `os.cpu_count()`, which equals physical cores on these tablets (no SMT). On hyperthreaded nodes, it reads like a slow node. Do not quote HPL figures from this tool for HT systems; use physical-core count or explicit process-count argument.<br>**Tablet Baseline:** Tablets lack SMT; `burn.py` results are valid for this fleet. See CURRENT-PROJECTS §C1 for SMT replication data on other hardware.

## ⚠ Before you touch anything — the traps
1. **Verify zero load average after a local kill.** A high `load average` with empty process lists indicates orphans still consuming resources.
2. **Benchmark hot nodes only.** Idle hotplug operations park cores and clocks; cold node measurements (e.g., via `nproc`) will under-report available capacity.
3. **Use multi-threaded benchmarks.** Single-threaded probes in environments like Termux (where OpenBLAS may lack threading) can yield misleadingly healthy results for degraded nodes due to sequential read caching or input size conflation (`s/img`).
4. **Compare node performance simultaneously.** Sequential comparisons across runs introduce measurement artifacts that obscure true per-node gaps.
5. **Use `s/MP` for throughput metrics, not image count.** Throughput must be measured as total work completed in equal wall time to account for variable input sizes.
6. **Account for power state constraints on legacy hardware.** Power states may be unreadable or undefined (e.g., node "legolas") and should never be assumed constant without measurement.

## How do I actually RUN something on the Fellowship? (submit a tablet order)

**Drop a `*.tjob.json` in `E:\SSDShared\tabfoundry-jobs\inbox\`.** That is it — from any box that
can see the share, with no session running. The factory accepts it, fans it across the healthy
nodes, checks a positive control AND coverage, DMs you the outcome, copies the results to
`E:\SSDShared\tabfoundry-jobs\outbox\` and clears the line.

Watch it on the homepage tile **Fellowship - Tablets**. Spec fields and the two verdicts every
result carries (`control_passed` + `complete` → `usable`): Order-Taker Design §9.

⚠ **To ask the deck whether the Fellowship is busy, the factory name is `fellowship`** —
`GET http://<sundae-address>:8099/p20/factory/fellowship` (port **8099**; `/p20/factories` lists them
all). **`tolkien` is NOT a deck name** and returns `"not a known factory"`, which reads like an
outage rather than a typo: TOLKIEN is the head *process*, `fellowship` is the *factory*. Check this
before any action that takes nodes out of service — a bounce during a live order is trespass on the
floor, and "could not read the deck" is never "idle".
The watchdog and its stand-down flag: §10.

**TWO order kinds since 2026-08-07 evening** (the owner: "add general compute tasks"):
* `kind: "sweep"` (default) — the original file sweep: items are files staged from `src_host`,
  control is a known-positive file.
* `kind: "map"` — general compute: `items` is an inline list of opaque STRINGS handed to the
  payload one per line (queries, URLs, test names…), nothing staged, control is a known-answer
  item (`control: {item, expect}`). Payload contract unchanged: `python payload.py items.txt
  out.json`, every row MUST echo `"item"` — coverage counts by it, so forgetting the echo fails
  the run instead of silently passing. Reviewed payloads live in
  `E:\SSDShared\tabfoundry-jobs\payloads\` (`map_echo` = the live decoy, `map_fetch` = URL batch).
* **HAI can submit map orders itself**: the `tablet_map` tool (admin-gated, sandbox-blocked)
  composes the spec and drops it in the inbox; `action=status/rows` reads the outbox. The payload
  whitelist IS the payloads directory — HAI cannot submit arbitrary code, only items.

⚠ **Do not hand-place files in the factory's working directories or hand-run its steps.** A customer
submits ORDERS; reading anything on the floor is free, doing the floor's work by hand is not — and
it is indistinguishable from an intruder to the guards. If the factory cannot express what you need,
that is a missing capability to build, not to work around.

## Testing cheaply: stratified-24 sample specification

The test protocol uses a **stratified-24 sample** (see `Integration` §10). The subset is constructed by sorting the full corpus by normalised megapixels and selecting one entry per bucket.

*   **Coverage**: Represents the 1,049-image corpus within a 0.5% margin of error. A random 25-image sample yields a 5.3% deviation.
*   **Cost**: Requires approximately 10 minutes per node, compared to 71 minutes for full coverage.
*   **Reproducibility**: Uses a deterministic seed to ensure every test arm processes identical inputs.

## iOS devices (iPad / iPhone) — candidate Fellowship compute nodes, NOT onboarded

the owner's spare Apple devices, intended as Fellowship compute under #34 area B ([[34-CHARTER]]). Listed in an
archived session on 2026-09-04 ("Project handoff from ChatGPT/Codex") and confirmed in chat on 2026-10-10. Status:
idea stage. Nothing has been measured, flashed or registered. Chip, RAM and iOS ceilings are general knowledge,
not read off the units, so check each device's model number before planning on it.

| device | condition (the owner) | chip / RAM / last iOS (unverified) | route to running code |
|---|---|---|---|
| **iPhone 17 Pro** ("Mycroft") | **the owner's DAILY phone, not a spare**: a plug-in-when-convenient helper, never a 24/7 node | **A19 Pro: 6-core GPU WITH Neural Accelerators** / 12 GB LPDDR5X / USB 3 10 Gb/s / iOS 26.6.1 (published specs; model and iOS read off the phone 2026-10-10) | Backburner's helper class (same chip as its tested 17 Pro Max). Needs an Apple Silicon Mac host today; the owner may get an old Mac from his landlord (must be M1+, not Intel). Watch heat: no vapor-chamber margin like the Pro Max |
| iPhone XR | broken screen | A12 (has Neural Engine) / 3 GB / iOS 18 | sideload (AltStore, free Apple ID); not checkm8-vulnerable |
| iPhone 7 | "maybe" | A10 / 2 GB / iOS 15 | checkm8 jailbreak → SSH + native arm64 binaries |
| iPhone 6s | — | A9 / 2 GB / iOS 15 | checkm8 jailbreak |
| iPad mini 2 | Wi-Fi only | A7 / 1 GB / iOS 12 | checkm8 jailbreak |
| **iPad 7th gen** (10.2", 2019) | Wi-Fi; model **A2197** read off the back by the owner 2026-10-10 | A10 Fusion / **3 GB** / iPadOS 17.7 (verified against published specs) | checkm8 jailbreak (A10 is covered; confirm palera1n's iPadOS 17 support) → SSH + native arm64 binaries. Strongest candidate: 3 GB AND jailbreakable |
| iPad 3rd gen | **NOT for compute**: still used for YouTube | A5X / 1 GB | excluded |

**Shape they would fit:** the old devices (everything except the 17 Pro: USB 2 / Lightning, 1–3 GB RAM, no GPU matrix
units) are Fellowship-style tiny-model or vision worker nodes under TOLKIEN, too small to hold 27B layers. The
**iPhone 17 Pro** is the exception: a Backburner-class prefill helper for a 27B, part-time only, since it is
the owner's daily phone.

**Onboarding research (2026-10-10, web only, no device touched):** `E:\SSDShared\SSD\Open Access\Claude Home\research\ios-devices-onboarding\ONBOARDING-RESEARCH.md`
(verdict table, sources with dates, an 11-step plan marking each step that needs the owner's hands). Verdict: **iPad 7th gen
first and probably only** — Dopamine 3.0.7+ (rootless, semi-untethered: tap Jailbreak after a reboot, no computer;
A10 on 16.7–18.7.1 per iClarified 2026-08-07 / iThinkDiff 2026-08-08 — the official release page does not state the
chip range, confirm on the device); free-Apple-ID signing expires every 7 days on 16.7+ (re-sign from rigby over USB
with PlumeImpactor); never update iPadOS. palera1n works from rigby too but every reboot needs rigby + DFU. iPhone 7 /
6s: maybe later (2 GB; TrollStore install on iOS 15, no 7-day expiry). iPhone XR: Dopamine covers A12 to 18.7.1 but
needs a working touch layer. **iPad mini 2: dropped** (A7 not supported on Linux, 1 GB). Integration: no USB network
interface on iOS — Tolkien would reach the iPad through `usbmuxd` + `iproxy` (a localhost port per device), a new
transport. llama.cpp CPU only (its Metal path needs A14+); no published A10 build or speed numbers. Untested: USB
Restricted Mode cutting USB data after an hour locked. Before anything: the owner picks the signing Apple Account and checks
Find My / Activation Lock, and the iPad is backed up.

**Related research (2026-10-10):** Backburner (github.com/StayLameBro/backburner) splits Qwen3.8-27B between a Mac and
an iPhone 17 Pro Max over USB-C. The phone only speeds up prompt READING (+29–44%) and never joins token WRITING, so
a slow helper cannot set the pace. That is the opposite of llama.cpp RPC, and why Voltron (rigby + 10 tablets) ran
~9× slower than rigby alone ([[34-Hard-Bench-Order-Spec]] §Voltron). Backburner's host is Mac-only, and it supports
iPhone 15 Pro+ or M-series iPads only; an M1 iPad Pro made reading up to 42% slower. Captions and transcript:
`E:\SSDShared\SSD\Open Access\Claude Home\research\ios-llm-video\`; side journal 2026-10-09-claude-ram.

## Open Issues and Verification Status

* **Pre-reboot slowdown ambiguity:** DRAM gather data suggests memory state issues, but orphaned-process contamination (see [[Fellowship-Operations-Connectivity-and-Reliability#DRAM State Degradation Detection and Remediation]]) produces identical symptoms cured by reboot. Root cause is not cleanly separated.
* **Order taker deployment:** The order taker is live in Sundae's containers as `tablet-foundry` + `tablet-watchdog`. End-to-end verification confirms an order dropped in the inbox from Windows ran 17 items across 5 nodes in 126 s, passed control and coverage, was DM'd, and shipped to the outbox without manual intervention.
* **Battery telemetry blind spot:** Telemetry is unreadable on 3 of the 5 original nodes. Legolas has never been measured.
