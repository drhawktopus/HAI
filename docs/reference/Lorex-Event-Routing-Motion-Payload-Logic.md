# Lorex Camera Integration - Event Routing & Motion Payload Logic



## Lorex Camera Event Routing Paths

This is the **intended** routing model for NVR motion events, classified across four paths. The pipeline is partly built, not fully live: Path A is validated but "not yet deployed as a persistent service" (status note below), and Path C has been demonstrated once via `map_motion.py` (the historical run recorded at the end). Per-path event volumes stated below are design estimates, not measured current rates. See [[Lorex-Camera-Architecture-Fellowship-Deployment]] for the detection component that is resident but currently uncalled.

### Path A: Odd-Hour Person Detection
Events classified as occurring during odd hours and involving a person are routed to `snapshot.cgi`. These snapshots are processed by the GPU model `qwen3-vl:8b` (average processing time ~15 s). The result is sent via Discord DM.

### Path B: Near-Box Motion Detection
Events classified as motion intersecting the litter ROI are routed to `snapshot.cgi`. These snapshots are processed by the GPU model `qwen3-vl:8b` (average processing time ~15 s). The result is written to the Event Log.

### Path C: Bulk and Nightly Motion Detection
All remaining events, including bulk and nightly motion, are routed to the Fellowship node for cheap work. This path performs ROI tests, colour flagging, motion geometry analysis, and deduplication. The results are written to the Event Log.

### Path D: Log Queries
Users query the Event Log via HAI to retrieve answers from recorded events.

---

## Path A: Live Person Alert
This path triggers on detected persons during configured time windows. It bypasses motion-based gating to minimize volume. Expected alert volume is near zero per day.

### Processing Constraints
*   **Latency Budget:** ~15 seconds end-to-end. 
*   **Compute:** GPU-accelerated inference required.
*   **Notification Target:** Discord.

> **Status:** This pipeline is validated for functionality but is not yet deployed as a persistent service.

---
### Litter box monitoring scope

The system prioritizes cheap ROI validation before full processing. The detection pipeline follows this sequence:

1. **Region of Interest Check:** Verify if the changed region intersects a litter tray.
2. **Survivor Processing:** Apply description and identity analysis only to events that pass the intersection check.

**Operational parameters:**
*   **Event Volume:** Expected tens of events per day, not hundreds.
*   **Latency:** Latency is irrelevant for this workflow.

---
### Path C — bulk index (everything else)

Fellowship operates as the **cheap tier**, not the VLM tier. This role prioritizes deterministic, high-volume processing (ROI intersection, saturation analysis, blob geometry, deduplication, and thumbnail generation) on tablet hardware. Semantic analysis via VLM is reserved for the GPU and applied only to subsets that justify the computational cost.

Benchmark context establishes this separation:
* **CPU-bound classical tier:** SmolVLM-500M on `gandalf` requires ~377 CPU-seconds (~51 s wall-clock) to answer a binary presence query ("Yes, there is a person in the image"). This delivers the functional equivalent of the classical tier’s output at the VLM tier’s resource price.
* **GPU-accelerated semantic tier:** `qwen3-vl:8b` on GPU completes similar queries in ~15 s with natural language responses.

The tablet-side VML build remains valid for future evaluation with larger models, but current doctrine reserves semantic inference for GPU resources where latency and throughput justify the expenditure.

### Path D — query
Camera logs support retrospective analysis via query. Use cases include investigating behavioral events (e.g., animal activity, litter box usage) that do not require real-time detection.

## Payload Mechanism: `map_motion.py`

This payload handles retrospective frame analysis when invoked by the Factory.

- **Input:** Frame URL.
- **Process:**
  1. Measures colour-vs-IR context.
  2. Fetches matching median background from `cameras.lan/bg/`.
  3. Computes difference and returns bounding boxes.
- **Dependencies:** numpy + Pillow (verified present on all Fellowship nodes).
- **Error Handling:** Errors are returned as rows; they never crash the node.
- **Security:** Payloads are whitelisted by presence in `payloads/`. Adding a payload is a deliberate human act, serving as the design gate.

## Background Data Protocol

Backgrounds are published once per camera and lighting mode at `cameras.lan/bg/ch<N>_<mode>.jpg`.

- **Design Fact:** Each tablet fetches the required background on demand. Nothing is staged per item.
- **Control Validation:**
  - A real control must be used to prove fetch, background diff, and boxing logic works, rather than merely verifying script execution.
  - Control files (e.g., `full/CONTROL_ch1_ir.jpg`) contain known defects (e.g., one bright rectangle).
  - The control must share its background type with the test subject (e.g., an IR control must be tested against an IR background). A mismatch causes a traceable failure unrelated to detector performance.

## Execution Constraints

- **No Ad-hoc Scripts:** Ad-hoc scripts on Softserve lack global-change guards, time-bucketed backgrounds, and auto-gain median matching. They produce invalid recall data and hide faults.
- **Factory Enforcement:** The Factory refuses orders with invalid controls or unverifiable numbers. This exposes configuration errors (e.g., stale background files) that scripts would miss.

## Historical Run Reference

The following is a record of the first live run using `map_motion.py` on the Factory (`cam-motion.20260808-123919`). It demonstrates successful distribution and error isolation.

```
54 items (53 frames + control) across 5 nodes
control PASS · coverage PASS 54/54 · 0 node errors · usable=True · 13.7 s
split: pippin 16, merry 14, gandalf 8, frodo 8, sam 8   <- work-stealing; the 4-core
                                                           nodes took MORE than the 8-core ones
2 errored: ch2 IR frames, HTTP 404 on bg/ch2_ir.jpg -- which the publisher deliberately
           refused to build from only 2 frames. An honest, traceable failure.
```
