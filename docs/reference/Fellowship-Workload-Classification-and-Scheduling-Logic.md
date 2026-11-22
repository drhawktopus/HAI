# Fellowship Cluster - Workload Classification and Scheduling Logic



## 4. SCHEDULER INTEGRATION: `orchestrator-build/scheduler.py`

The scheduler integrates Fellowship nodes as additive CPU-only capacity, not a separate scheduling mechanism.

### Job Kind and Eligibility

* **Target Kind:** `KINDS["cpu"]` (`vram_gb 0.0`, `cpu_heavy True`).
* **Supported Workloads:** Dataset acquisition, deduplication, collection, caption transforms, RAG chunking.
* **Excluded Workloads:** chat, chat27b, embed, vision, render, train, caption route to the GPU fleet — they run the fleet's 8B/27B chat models and GPU image/video models, which exceed a tablet's RAM and need an accelerator the Fellowship lacks. This is a **per-model fit** constraint, not "tablets can't run LLMs": current tiny models load and serve on the Fellowship — measured on all 7 nodes 2026-08-27, MiniCPM5-1B / Qwen3.5-0.8B / Qwen2.5-1.5B (Q4) at ~4–10 tok/s, ceiling ~1.5B on the 1.76 GB nodes and ~2B on the 2.79 GB nodes (mmap-backed, degrades gracefully above free RAM). Numbers: [[Fellowship-Performance-Baselines-and-Benchmarking]] §Local tiny-model LLM inference. A tablet-hosted tiny-model worker kind is under evaluation in #34 area B ([[34-CHARTER]]).

### Host Table Configuration

* Add five CPU-only hosts alongside the four existing x86 boxes.
* **Architecture:** `aarch64/bionic`.
* **Binary Constraint:** x86 binaries and glibc builds do not run on these hosts (see §6).

### Weighting and Slicing

* **Weighting:** Apply `TABLET_GFLOPS` (or reuse `CAPTION_S_PER_IMG` from §2) to ensure proportional job splits.
* **Slicing Algorithm:** Use rank-based slicing. Do not use band-hashing; band-hashing causes remainder re-splits that starve faster-finishing nodes.

### Liveness and Failure Handling

* **Connectivity Profile:** Tablets experience two daily WiFi disconnects (~40 s each), managed by the watchdog ([[Fellowship-WiFi-Watchdog]]). Brief unreachability is normal operational behavior.
* **Scheduling Doctrine:** 
  * "I cannot tell" ≠ "it is free."
  * A node claiming `BUSY` remains `BUSY` until updated, even if stale.
  * Unreachable nodes are simply not offered new work.
  * Dropped nodes must return their work unit to the queue.

### Claim Protocol

* The existing `claim(host, resource, holder, ttl)` function applies. Tablet jobs must claim the node upon assignment.

### Capability Gap Resolution

* When `place_order()` cannot place small CPU workloads on busy x86 boxes, Fellowship nodes provide the fallback capacity designed for this fleet.

## Workload Fit and Constraints

Fellowship nodes handle CPU-bound batch jobs where the unit of work is "a few KB in, minutes of CPU, a few KB out." Suitable workloads include:

*   Rate-limited API or dataset pulls
*   Image hashing, deduplication, and thumbnailing
*   Checksums and file inventory
*   Parameter sweeps and Monte Carlo simulations
*   Brute-force search
*   RAG chunking
*   Audio transcription (e.g., Whisper). Benchmark: **29m54s** (1794 s) episode transcribed in 14m13s (852 s) on one tablet = 2.10× realtime. Fleet capacity was estimated as ≈ 2 episodes/hour/node × 5 nodes ≈ 10 episodes/hour (~240/day) at ~35 W with zero GPU usage — ⚠ **that estimate does not reconcile with the measured table** (852 s per 1794 s episode ⇒ ≈4 episodes/hour/node, ≈20/hour on 5 nodes) unless it includes unrecorded extraction/transfer overhead; treat as UNVERIFIED until re-measured (flagged 2026-09-10 by a cold review of the portfolio; the portfolio quotes only the measured per-node figure). Full recipe and artifacts are in [[Tablet-Job-Whisper-Subtitles]]. *(2026-09-10: corrected "20m54s" → "29m54s" per the primary table in the Whisper doc; the old figure did not reconcile with 2.1× and was caught by a cold review of the portfolio.)*

**Unsuitable workloads:**

*   Model inference of any size (RAM-bound)
*   GPU-accelerated work
*   Processes requiring >1.5 GB per process
*   Tightly coupled or MPI-based tasks
*   Large data movement (all nodes share a single radio link)
*   Latency-sensitive interactive work

**Runtime constraints:**

1.  **Threading:** Termux OpenBLAS is compiled without threading support. Each process maps to one core. Multi-process parallelism is required for full node utilization; single-process benchmarks underestimate node performance by approximately 7×.
2.  **Multiprocessing Start Method:** Python 3.14 defaults to `forkserver`, which pickles initialization arguments and degrades performance (measured 0.69 vs 20.9 GFLOP/s). Explicitly set the start method to `fork`:
    ```python
    import multiprocessing as mp
    mp.set_start_method("fork", force=True)
    ```

## Scheduling Constraints: Tablets vs Cards

Tablets are integrated via the `TABLETS` table in `scheduler.py`, separate from `CARDS`. Both tables are read from the
device roster file (`Z:\Documentation\State\device-roster.json`: its `tablets` and `devices` lists), not written in the
code; [[RUNBOOK-Orchestrator-Deploy-Procedure]] § "The device roster file".

*   **Separation Rationale:** `CARDS` carries GPU/LLM semantics assumed by other consumers. Integrating tablets into `CARDS` creates a larger blast radius for non-GPU work. The deviation from §4.2 maintains the intent (resource utilization) with smaller scope.
*   **Selection Criteria:** Tablets are offered only for independent CPU work.
    *   Gates: `cpu_heavy and vram==0 and not interactive and not needs_container`.
    *   Test Coverage: The container gate is validated by the existing test suite (`tests/test_scheduler.py`).
*   **Performance Weighting:** Tablets carry a provisional GFLOPS score (e.g., 40+). This ensures free x86 boxes outrank tablets when available, and tablets are selected only when real computers are busy.

## Worker Count Formula and Memory Limits

The optimal worker count is determined by the node's available memory, not solely its core count. The design fact for worker allocation is:

```
workers = min(cores, MemAvailable_GB / 0.15)
```

At 8 workers, nodes with 1.76 GB RAM hit a performance cliff: free RAM drops to ~0.07 GB and swap usage climbs. The constant `0.15` is the validated divisor; do not reason about this constant. Measure the curve.

### Validated Worker Counts (Reference Data)

The following table shows the best-performing worker counts derived from size-stratified 24-image samples. These values serve as initial defaults pending live verification against current hardware specs.

| Node | Cores / RAM | Best Workers | Note |
|---|---|---|---|
| Gandalf | 8 / 2.79 GB | 8 | Core-limited; still rising at 8w |
| Frodo | 8 / 1.76 GB | 7 | Memory-limited |
| Sam | 8 / 1.76 GB | 7 | Memory-limited |
| Merry/Pippin | 4 / 1.82 GB | 4 | Core-capped |
