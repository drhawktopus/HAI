# Basement Desktops — Capacity Determination & Pending Decisions (§C, §D)



## §C — Capacity determination for basement desktops

Capacity limits are derived from controlled probes on fully configured, idle systems.

**Procedure:**
1. Provision the box with OS settings and configurations (§A/§B).
2. Perform a controlled probe: run baseline workload (N units, secondary job OFF), then run with secondary workload enabled (N units, ON). Compare rates to determine safe concurrency limits.
3. Set default concurrency based on probe results.

**Rules:**
- A disrupted run is not valid data for capacity planning.
- Ship conservative defaults (niced processes; refuse when GPU busy) until a controlled probe establishes specific limits.
- Do not add secondary workloads to boxes running priority jobs without first establishing these limits via idle-box testing or completion of the priority job.

### C0. Ship conservative FIRST, measure second
- Hosts join the cluster with **niced** scheduling or are **refused when GPU-busy**. This conservative default remains until performance metrics from C1–C3 justify relaxation.

See [[HOST-ONBOARDING-Basement-Desktops]] §Onboarding Procedure for implementation steps.

### C1. Baseline performance metrics
Measure baseline CPU performance on an idle system using the four standard metrics: Cores, RAM, HPL GFLOP/s, and STREAM Triad. These metrics align with the Fellowship fleet for comparability ([[Fellowship-Crunch-Integration]] §2).

**Conformance requirements:**
*   **Method consistency:** Use identical inputs and methodology across all baseline measurements.
*   **System isolation:** The host must be otherwise idle during measurement. Any concurrent load invalidates the result.
*   **Direct measurement:** Perform live benchmarks on the target hardware. Do not use spec-sheet estimates or inherited values from generic processor data.

#### C1 Benchmark Results: HPL and STREAM Performance

**Test Harness Configuration**
The performance baseline is established using **HPL 2.3** (netlib, sha256 `32c5c17d…`) on **OpenMPI 4.1.6**. The environment uses **serial OpenBLAS** with `OPENBLAS_NUM_THREADS=1`, asserted at runtime to enforce single-threaded BLAS operations despite Ubuntu's default threaded build.

**Execution Parameters**
- **HPL**: `NB=192`; one rank per physical core; N sized to approximately 50% of available RAM.
- **STREAM**: `ARRAY_SIZE=20M`, compiled with `-O3 -fopenmp`, executed with `NTIMES=20`.
- **Validation**: Runs are idle-gated (refused if 1-minute load > 1.0) and repeated. HPL residual check must pass on every repetition.

**Node Specifications and Results**

> ⚠ **2026-08-28 HARDWARE CHANGE — CPUs were SWAPPED between the boxes; rigby's RAM was rebuilt.**
> The benchmark table further down (HPL/STREAM/burn.py) was measured BEFORE this change — with the CPUs
> in the OPPOSITE boxes and rigby on its old 1333 RAM — so **those rows are STALE and misattributed;
> re-measure before trusting them.** The table immediately below is the authority for CURRENT physical config:
>
> | | rigby (primary node) | mordecai |
> |---|---|---|
> | **Board** | HP Compaq Elite 8300 SFF (HP 3397, Q77) | Asus x86 (2 DIMM slots) |
> | **CPU** | **i7-3770**, 4c/8t — swapped IN 2026-08-28 | **i5-3470**, 4c/4t — swapped IN 2026-08-28 |
> | **RAM** | **24 GB**: 2×8GB + 2×4GB DDR3-**1600**, all 4 slots, balanced 12/12 dual-channel (Configured 1600 MT/s, verified 08-28) | ⚠ **pending re-verify** — its 2×8GB were moved to rigby for the 24GB build; box asleep/unreachable at update time |
> | **GPU** | GeForce GT 730 (Kepler GK208, 2GB) — llama.cpp Vulkan/CPU inference | none |
> | **Sleep/WoL** | sleep/suspend/hibernate MASKED since 2026-08-15 (unattended work); rigby also carries the tablet USB bridge (br0 + 7×enx*), so it must NOT sleep or the tablets drop with it — WoL is therefore moot | ⚠ **status UNRESOLVED** — not on the LAN at update time, and my raw WoL magic packet from Softserve didn't wake it in 150s. ⚠ This CONTRADICTS the PROVEN 2026-08-19 [MAIN] finding that mordecai WoL WORKS (twice on hardware) and does NOT idle-sleep — so most likely my raw-broadcast method was wrong (unreachable-by-that-method), or the 08-28 CPU swap reset its BIOS WoL — NOT that it idle-sleeps. Documented wake method: `Reference\Credentials\network-credentials.md`. Verify when the owner next powers it; do not trust "broken"/"sleeps" (see [[HOST-Basement-Desktops-Hostname-Migration-Wake-on-LAN-Doc]]) |
>
> Why: rigby is the primary basement node (GPU + §41 advisory model), so it took the stronger i7. Full
> story incl. the cable-pinch POST saga (4/1/5-beep loop = a DIMM walked loose by case cabling): Journal 2026-08-28.

**PRE-SWAP benchmark table** (⚠ historical — i7 was in mordecai, i5 in rigby, rigby@1333). ARCHIVED / accurate-historical, superseded by the 2026-08-28 swap. NEW post-swap tables being built (rigby-only until mordecai is back up). Retained here for history:

| Metric | mordecai (then i7-3770) | rigby (then i5-3470, ex-Worktop) | Combined Independent |
|---|---|---|---|
| **CPU** | i7-3770 | i5-3470 | |
| **Cores** | 4c / 8t | 4c / 4t | 8 physical |
| **RAM** | 16 GB (2×8 DDR3-1600, 2/2 slots) | 12 GB (2+4+2+4 DDR3-1333 @1333, 4/4 slots) | 28 GB |
| **HPL GFLOP/s** | 100.97 – 101.60 (N=30720) | 89.91 – 91.13 (N=26880) | ≈ 192 |
| **HPL Spread** | 0.6% apart | 1.4% apart | |
| **STREAM Triad** | 16,092 – 16,130 MB/s | 11,977 – 12,065 MB/s | ≈ 28.2 GB/s |
| **`burn.py` GFLOPS (DGEMM)** | 101.71 – 102.99 | 72.61 – 78.46 | ≈ 180 |

**Performance Note**
The "Combined" values represent the arithmetic sum of two independent boxes. This metric differs from distributed cooperative performance; see the distributed result section for cooperating node capabilities.

#### Basement Desktops: Distributed Pairing and Performance Limits

The basement desktops (mordecai and rigby) form a distributed compute pair. Unlike the Fellowship fleet, where cross-node MPI jobs yield only 16% retained performance due to network constraints, this pair retains **83%** when running as one MPI job (8 ranks, 4 per box). This capability exists because the gigabit wired link (iperf3: 941 Mbit/s symmetric) is sufficient for HPL workloads at large N (N=41088), where O(N³) arithmetic amortizes communication costs. Small or communication-heavy workloads will not achieve this retention; re-measure performance assumptions for non-HPL workload shapes.

Both modes are available: the boxes can operate as independent workers or as a single ~160 GFLOP/s unit. Selection depends on workload characteristics.

**Performance Baselines and Architecture**

*   **ISA Ceiling:** Both hosts use 3rd-gen Ivy Bridge CPUs (`avx`, `sse4_2`; no AVX2, no FMA). This architecture delivers 8 DP flops/cycle/core. Compare to BigHonker (Coffee Lake i5-9600K with AVX2+FMA), which delivers 16 DP flops/cycle/core.
*   **Efficiency:** Against sustained all-core clocks (~3.4 GHz), mordecai reaches ~92% and rigby reaches ~84% of achievable peak performance.
*   **SMT Impact:** SMT oversubscription reduces throughput by ~37%. `burn.py` defaults to logical CPU count; on SMT-enabled hosts (mordecai), this produces misleadingly low results compared to physical-core-count instruments.

| Host Configuration | Workers (Logical) | GFLOPS (Range) |
|---|---|---|
| mordecai (SMT enabled) | 8 | 64.05 – 65.33 |
| mordecai (SMT disabled/Pinned to physical) | 4 | 101.71 – 102.99 |

**Instrumentation Rules:**
*   Any cross-fleet `burn.py` comparison involving an SMT box must pin the worker count to physical cores. The modified `burn_n.py` is benign on non-SMT hosts (rigby readings overlap with standard `burn.py`).

**Memory Reporting Caveats**
*   **Mordecai:** Asus firmware does not emit `Configured Memory Speed`. Only raw speed (1600 MT/s) is reported. No configured speed is quoted.
*   **Channel Mode:** Not reported by dmidecode on either board; has not been inferred.
*   **STREAM Gap:** The 1.34× STREAM performance gap between hosts is partially attributed to clock differences (1600 vs 1333 MT/s accounts for ~1.20×). The remainder is unattributed. Rigby’s mixed-size DIMMs are a hypothesis, not a confirmed finding.

### C1b. Performance measurement methodology and constraints
Performance comparisons require self-measured data from the same instrument to be valid. Datasheet figures are prohibited; they invalidate comparison context.

#### Measurement requirements
- **Reference baseline:** BigHonker CPU-only, idle state, using the standard HPL/STREAM harness. This row is mandatory for validation.
- **Contextual rows:** Include Mothership and Softserve CPU results (same instrument, idle state) for scale.
- **Instrument consistency:** All rows must use identical test software and configurations.

#### Operational constraints
- **Power management:** Disable sleep on BigHonker during measurement. The default 30-minute sleep timeout causes run failures if the system suspends mid-test.
- **Host load management (Softserve):** Softserve serves as the session host and Plex/VM host. Maintain minimal footprint; schedule measurements only when the owner is available to monitor. Avoid casual saturation.
- **Resource accounting (Sundae):** Sundae runs as a guest VM on Softserve. Its cores and RAM are subsets of Softserve’s resources. Do not count Sundae’s resources separately from Softserve’s to avoid double-counting.
- **Idle-state enforcement:** Every measurement row must be taken on an idle box. Measurements taken during concurrent workloads are void, not representative of degraded performance. Reference the 7900 XTX "76 → 6.4 TFLOP/s" error as a precedent for occupancy-induced invalidation.

### C1b Measurement Status: Blocked

The performance measurement for BigHonker is currently blocked. No HPL benchmark data exists for this host.

**Blockers:**
1. **Scope restriction.** Testing is restricted to `rigby` and the stream box only. BigHonker, Mothership, and Softserve are out of scope until explicitly authorized by the owner.
2. **Environment mismatch.** The measurement harness uses Linux (netlib HPL + OpenMPI + OpenBLAS). BigHonker runs Windows without WSL. Performing a same-instrument measurement requires installing WSL on the live factory host, which constitutes a production configuration change and likely reboot. This is a decision point for the owner, not an autonomous step.

**Prohibited actions:**
* Do not estimate performance from spec sheets. The checklist explicitly forbids using datasheet figures for BigHonker.
* Do not derive synthetic values. An i5-9600K has AVX2+FMA (16 flops/clock/core vs 8 on current targets). A paper estimate would yield ~200 GFLOP/s, appearing decisive but constituting a fabricated verdict.

### C1b Status: Closed — Won't-Do on BigHonker

No software is installed on **BigHonker** for the purpose of generating naming data. The HPL comparison row in this document remains permanently empty.

This constraint exists because filling the HPL row requires a WSL install and a probable reboot on a live factory host, which yields no operational benefit beyond a naming decision. Future sessions must not:
1. Reopen this issue.
2. Fill the row from datasheets.
3. Install anything on BigHonker to "unblock" naming.

The naming decision is **not** blocked by this constraint. BigHonker was already measured on the OCR axis (0.458 img/s) as of 2026-08-06; therefore, every host in the lab exists on that single scale. See §C1c for measurement details.

The tier verdict is determined by the **OCR / task-farm axis**, not the HPL axis. The OCR axis is the correct metric because it reflects the class of work these hosts actually receive. This difference results in a ~3.7× variance depending on the test used; §C5 must explicitly state that the OCR axis governs the tier verdict.

### C1b (BigHonker CPU row) is deliberately OPEN — the owner's ruling. Do not re-litigate.

C1b has no tier verdict and no name; boxes stay at the §C0 conservative default
(**niced / refused-when-GPU-busy**). Do not fill the BigHonker row from a datasheet, and do
not install WSL on BigHonker to unblock the measurement, without asking the owner again. The
question is parked, intact, and answerable whenever he chooses to spend the change on BH.

**Options on the table for when the question reopens:**
| option | cost | gives a valid C1b row? |
|---|---|---|
| WSL + this same harness on BigHonker | config change + probable reboot on a factory host | ✅ yes, exact |
| Native-Windows HPL/OpenBLAS build on BH | no reboot, but a *different* toolchain — a weaker comparison that must be labelled as such | ⚠ partial |
| Measure a different, already-Linux box as the scale | free, in scope | ❌ answers a different question |
| Leave C1b open; boxes stay at the §C0 conservative default | free | ❌ no verdict, but nothing is broken |

#### CPU Baseline: BigHonker OCR Throughput Reference

The lab maintains a recorded CPU performance baseline using Tesseract 5.5.0 OCR throughput (img/s). This measurement was established during the 2026-08-06 "CPU fair fight" evaluation.

**Recorded Baseline Values**
*   **BigHonker:** 0.458 img/s
*   **Mothership:** 1.50 img/s
*   **Fellowship:** ~0.36–0.40 img/s

These values were captured using identical images, verified controls, and swept concurrency settings. See [[Fellowship-Crunch-Integration]] §10b for the full test record.

**Usage Rules**
*   **Naming Verification:** The naming rule clause *"kicking near BH (not including GPU)"* can be evaluated without modifying BigHonker’s configuration. To assess a new host (e.g., Mordecai or Rigby), run the same Tesseract OCR harness. Compare the result against the BigHonker baseline above.
*   **Metric Isolation:** This OCR throughput metric is distinct from HPL GFLOP/s measurements. Do not mix instruments: the one-variable rule forbids comparing OCR img/s figures against HPL GFLOP/s figures to derive a single tier verdict.
*   **Workload Specificity:** Always specify the axis of comparison in verdicts. Claims such as *"≈BH on OCR throughput"* and *"≈BH on HPL"* represent different workload characteristics (branchy/memory-bound vs. FMA-bound). An Ivy Bridge system without AVX2/FMA may perform relatively better on OCR than on HPL. Do not assume equivalence across metrics.

### OCR Task-Farm Axis Performance Baseline

Every host in the lab sits on one OCR task-farm axis without touching BigHonker. This axis is used for production work.

**Measurement Instrumentation**
*   **Harness:** PORT of `bench_bh.py` (from 2026-08-06), not a fresh instrument. Constrained to match BigHonker's baseline:
    *   Same 6 images (set-hash `82d87ce2c63e507041b5ab483d74cb64`, byte-for-byte identical on both boxes).
    *   Same unmodified `wm_ocr.py` (imported, never reimplemented).
    *   `tessdata_best` data.
    *   `ThreadPoolExecutor` (not `mp.Pool`; pool type changes scheduling because real work is in the tesseract subprocess).
    *   One warm scan, idle gate, two full reps.
*   **Declared Deviations:** Tesseract 5.3.4 (Ubuntu) vs 5.5.0 (Windows); Linux vs Windows build.

**Validity Constraints & Controls**
Two validity threats were identified and mitigated during instrument design:
1.  `--tessdata-dir` redirects config-file lookup. A model dir without `configs/` yields zero rows at exit 0 (indistinguishable from a clean image). A positive control is required to surface this.
2.  Image count must match the reference set (6 images). Using more images conflates node speed with image size, producing silently incomparable results.

**Shallow-Queue Performance (6-image batch)**
Measured on identical hardware configuration:

| workers | mordecai rep1 / rep2 | rigby rep1 / rep2 |
|--:|--:|--:|
| 1 | 0.063 / 0.063 | 0.055 / 0.055 |
| 2 | 0.113 / 0.113 | 0.097 / 0.095 |
| 3 | 0.127 / 0.127 | 0.105 / 0.105 |
| 4 | 0.135 / 0.135 | 0.109 / 0.109 |
| 6 | 0.141 / 0.138 | 0.110 / 0.115 |
| 8 | 0.138 / **0.145** | 0.112 / **0.115** |

**Best shallow-queue rate (same method as every other host on the axis): mordecai 0.145 · rigby 0.115 img/s.**

**Queue-Depth Effect & Capacity Planning**
The 6-image batch against an 8-worker pool represents one wave with a long tail: workers finish cheap images and idle waiting on the slowest. At realistic depth, backfill occurs instead.

| Metric | 6-image sweep (shallow) | Deep queue (13/11-unit batch) | Delta |
|---|--:|--:|--:|
| mordecai | 0.145 | **0.167** | **+15%** |
| rigby | 0.115 | **0.130** | **+13%** |

**Design Rule for Planning:**
*   Use the **deep-queue rate** for capacity planning.
*   Use the **shallow rate** only to compare against other hosts measured with the same shallow method.
*   BigHonker's 0.458 and Fellowship figures were taken on shallow batches and are understated. This common-mode pessimism largely protects ratios but not perfectly, because tail penalty scales with workers-vs-batch-size (BigHonker ran 6-way on 6 images; these boxes ran 8-way). Do not defend the third digit of any ratio on this axis.

#### Task-farm concurrency retention — design conclusion

Independent work retains more throughput than coupled work when farmed across the basement pair: rate-split task-farming retains ~95% of summed single-box rates (the ~5% loss is integer split granularity, shrinking as unit count grows), while coupled distributed HPL retains ~83% (a real communication cost). Prefer independent task-farming; measurements: Journal 2026-08-15.

#### Axis Inversion: Workload-Specific Performance Verdicts

Performance comparison between the basement desktop pair and the six-node Fellowship cluster varies by workload axis. The following measurements apply to the current hardware configuration:

| Axis | Pair vs Fellowship | Notes |
|---|---|---|
| Dense matmul (`burn.py`) | ≈2.0× faster on pair | Same instrument both sides |
| HPL | ≈2.8× faster on pair | Linear algebra benchmark |
| OCR / Task Farm (img/s) | ≈0.55× slower on pair | Throughput scales with core count |

**Design Fact:** Performance advantage depends on workload characteristics.
*   **Fellowship Cluster (40 cores):** Dominates throughput-bound workloads that scale with core count, including production OCR, captioning, and embedding tasks.
*   **Desktop Pair (8 physical cores):** Dominates workloads dependent on per-core floating-point width.

**Verdict Rule:** A tier verdict is meaningless without specifying the performance axis. The governing axis for any host assignment must match the primary workload class that host will receive.

**Status of §C5:** Unresolved. C1b remains open by the owner's ruling. No BigHonker HPL row exists; no specific names are assigned to this result set.

#### `burn.py` SMT artifact: workload-dependent performance impact

The `burn.py` benchmark exhibits a significant performance penalty when spawned with `os.cpu_count()` (logical CPUs) on systems using Simultaneous Multithreading (SMT). Independent replication confirms that running the harness with one worker per logical core yields ~38–44% lower throughput compared to pinning one rank per *physical* core. This artifact can invert node performance rankings, making SMT-enabled nodes appear slower than non-SMT nodes.

**Measured Impact by Workload:**

| Workload Type | SMT Impact (Logical vs Physical Cores) | Notes |
|---|---|---|
| Dense Matmul (`burn.py`) | **−44%** | Two independent runs confirm penalty. Default `os.cpu_count()` yields ~57–64 GFLOPS; pinned physical cores yield ~103 GFLOPS on *mordecai*. |
| OCR / Task-Farm | **~0 to +2%** | Negligible impact. Scheduling overlap, not SMT penalty, drives minor gains. Verified via control node *rigby* (4c/4t, no SMT), which shows identical ~5% scaling behavior from 4→8 workers. |
| HPL (High Performance Linpack) | **Unmeasured** | No valid data exists. The test arm (`--bind-to core`) fails to bind 8 ranks to 4 cores, preventing measurement. Any previous citations of "−9%" or similar figures are unearned assertions and have been removed. |

**Design Directive:**
Benchmarking nodes requires pinning one rank per *physical* core to avoid SMT artifacts. Relying on default thread counts produces false performance baselines.

> See CURRENT-PROJECTS §C1 for original discovery context.

### Worker-count derivation procedure

- Derive the worker count via empirical sweep on a **size-stratified sample** of nodes. Do not reason about the constant; measure the curve.
- Target range: workers 1..cores. Select the configuration that yields the best performance metrics.
- Sample strategy: Use stratified sampling, not random sampling. Stratified-24 yielded 0.5% p90 error vs 5.3% for random-25 (an effect size larger than most targets) with ~10 min per node vs 71 min.
- Metric unit: Use **s/MP** or total MP completed in equal wall time. Do not use `s/img` as it is misleading and has caused incorrect conclusions on other hardware.

#### Design doctrine
- The constant used in the formula `workers = min(cores, MemAvailable_GB / 0.15)` is workload-specific. It is not transferable between different architectures or RAM configurations (e.g., from tablets to basement desktops).
- Re-derive the constant for each new hardware profile to avoid cargo-culting parameters.

### C3. Co-location probe measurement protocol
- **Protocol**: For systems with a GPU, execute N units with the CPU job OFF and N units with it ON, using identical inputs. Use the delta to set the concurrency default. This determines capacity limits.
- **Sample size**: Execute measurements multiple times. A single run (n=1) is a hypothesis, not a finding; treat base rates as derived from repeated trials.
- **Reporting**: Report the spread and whether ranges overlap. Do not report bare deltas. Non-overlapping ranges indicate significant difference.
- **Repetition type**:
  - Deterministic instruments: Repeat by adding more scenes.
  - Latency/load numbers: Repeat by separating rounds in time.
- **Execution**: Treat measurements as orders, not direct script calls. Define inputs, method, deliverables, and cleanup. Submit via `mode:"cpu"` orders (`run_cpu_order` in `p20_foundry.py`).

### C4. Record the result where the scheduler reads it
- [ ] Write weights to `scheduler.py`.
- [ ] Document the numbers and method in this document and [[Network-Infrastructure]].

Capacity metrics must reside in indexed documentation (`Reference\`) or configuration (`scheduler.py`). They must not exist solely in session transcripts, which are not indexed for retrieval by operators or HAI.

### C5. Naming policy by performance tier

Host naming is determined by benchmark results against BigHonker (BH). Performance tiers and their corresponding naming conventions are defined in the table below. Benchmarks must measure individual nodes and combined capacity.

| measured outcome | tier | naming consequence |
|---|---|---|
| Individual node ≈ BH CPU performance | Top tier | **Starfleet-level name** — joins the named FLEET |
| Only ≈ BH when nodes are combined | Mid tier | **Distinct subgroup** — themed, separate from Fellowship; requires a designated head node (see §Head-node selection) |
| Clearly below BH performance | Low tier | **Joins the FELLOWSHIP** — see §Fellowship x86 integration |

#### Benchmark reporting requirements
To determine the tier, provide:
1. Per-box and combined benchmark figures.
2. The spread of results and whether ranges overlap with BH’s. A gap within noise margins (e.g., <5%) is not considered a performance difference.

#### Head-node selection for Mid-tier subgroups
If hosts form their own subgroup, they require a head node. Do not create new heads or keypairs reflexively. Evaluate existing infrastructure:
* Softserve serves as the Fellowship head (`Lab\fellowship\`).
* Verify if the tablet cluster head resides on Sundae/Softserve.
* Reuse an existing compatible head rather than minting a new one.

#### Fellowship integration for Low-tier hosts
If hosts join the Fellowship, they must integrate as a **separate x86 island**, not as peers to ARM nodes. The Fellowship consists of aarch64/bionic nodes; these hosts are x86. 

**Constraint:** Never run one MPI job across ARM and x86. Use homogeneous islands coordinated by a task layer. "Joining the Fellowship" means operating under the same task layer/placement target, with x86 and ARM work units kept on their respective islands.

Upon final tier assignment by the owner, proceed to §E for name registration.

#### HOST ONBOARDING: Basement Desktops (`mordecai`, `rigby`, `benson`)

**Nomenclature and Role Assignment**

Per §C5 classification on the OCR / task-farm axis:

- **`mordecai`**: Formerly `StreamBox`. Task-farm node.
- **`rigby`**: Formerly `pinky`. Task-farm node.
- **`benson`**: Designated dispatcher for the pair. Role analogous to `Tolkien` for the Fellowship.

**Classification Basis: Axis Inversion**

Tier assignment depends on workload axis. Per ⭐ THE AXIS INVERSION:

1.  **OCR / Task-Farm Axis (img/s):**
    The pair performs at ~54% of BigHonker's CPU baseline (shallow 6-image basis). Individually, `mordecai` is 32% and `rigby` is 25%. By policy table standards, this falls into the bottom row, conventionally indicating "join the FELLOWSHIP."

2.  **Dense Matmul Axis:**
    The pair provides ≈2.0× the capacity of the entire six-node Fellowship. Folding them into the Fellowship would bury per-core capability, 16 GB RAM availability, native Linux support, and x86 binary compatibility.

**Design Decision**

`mordecai` and `rigby` are maintained as a distinct pair (`MID-tier` naming consequence) rather than joining the Fellowship. This preserves their individual addressability and high-per-core capability on dense workloads.

**Dispatcher Status: `benson`**

- **State:** Not built.
- **Location Options:** Open. May be a process beside `Tolkien` on Softserve, or reside on `mordecai`. See §D.
- **Constraint:** Do not hide the pair behind `benson`. Unlike the homogeneous tablet cluster (hidden behind `Tolkien`), `mordecai` and `rigby` are individually useful and directly addressable. They must remain distinct targets to preserve their earned naming rationale.

## §D — Pending Decisions and Data Gaps

This section lists decisions pending external data (A0 inventory) or operator policy (§C). Do not proceed with configuration until the dependency is resolved.

| # | Decision / Data Gap | Dependency / Blocking Condition |
|---|---|---|
| D3 | **`FOUNDRY_ROOT` path assignment** | Requires A0 disk inventory. Path tracks the largest fast disk (`F:\foundry` or `E:\foundry`). See **§A** for inventory procedure. |
| D4 | **GPU power standard applicability** | Requires A0 GPU presence confirmation. Standard applies only if the box runs GPU compute. Applying blindly violates `TdrDelay` warnings. See **§C** for scope definition. |
| D5 | **Host role: Full Factory vs. CPU-only Scheduler** | Requires A0 GPU/disk findings AND the owner’s "to what capacity" decision from **§C**. Do not select role before §C is finalized. |
