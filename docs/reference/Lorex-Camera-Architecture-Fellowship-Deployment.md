# Lorex Camera Architecture — Fellowship Decode & Tolkien Detection



## Processing Runs on the Fellowship

The lab architecture processes images via the Factory on Fellowship nodes rather than ad-hoc scripts on Softserve. This ensures control validation and error isolation.

## Detector Threads

Two distinct detection threads exist. Do not conflate them:

1. **Retrospective Bulk Analysis (`map_motion.py`)**
   - **Status:** Superseded by the Fellowship/Tolkien detection pipeline for live operations.
   - **Use Case:** Retrospective bulk analysis of mined frames only.
   - **Mechanism:** Standalone background subtraction detector.
   - **Note:** Recall measurements on this thread do not reflect the system currently in service. See [[RUNBOOK-Lorex-NVR-Operation]] for current pipeline state.

2. **Live Object Detection (ncnn/Deepstack Protocol)**
   - **Status:** Active live goal. Delegating object detection to Fellowship tablets over the Deepstack/CodeProject.AI protocol.
   - **Requirement:** Requires PROJECT #32 (`fellow` agent mode). Per-request process spawn reloads the ncnn model every frame; this load dwarfs the 27 ms inference time.

## 7e. Architecture ruling — detection on Fellowship, not bespoke

Object detection runs on the Fellowship tablets (ncnn), with Tolkien as the head node coordinating event-driven bursts from the Lorex NVR. Analysis runs only during motion events; the clip is hardware-decoded on demand and no decode occurs when idle. The pipeline relies on existing lab layers rather than rebuilding proven tools from first principles to satisfy a single hardware constraint.

**Design fact:** Do not rebuild a proven wheel (such as Frigate) from first principles because of one hardware constraint. If the tool fits, use it; if it must be removed, redistribute its functions deliberately across layers the lab already owns. Frigate is retired and no longer part of the stack.

## Object detection on the Fellowship

### Design doctrine

Object detection runs on the Fellowship nodes using a resident ncnn detector listening on port `:8801`. The system prioritizes detection accuracy over inference speed. Detection labels are restricted to `cat`, `dog`, and `person`.

**Processor selection:** Detection runs on **CPU**, not GPU. Vulkan acceleration is disabled because it is significantly slower on Mali/PowerVR devices. Benchmarks showed CPU inference at 173–312 ms versus GPU at ~1723 ms.

**Model Selection: Accuracy Over Throughput**

The resident detector deploys `mobilenet_ssd_voc` (ncnn, PASCAL VOC labelmap filtered to `cat`, `dog`, `person`) on CPU. Which model is live is defined in code — `tolkien-build/tablet_foundry.py` (`DETECT_PARAM` / `DETECT_WEIGHTS` and the `--deploy` push) is the conformance source.

Design principle: prefer detection accuracy over inference speed, since motion-triggered bursts are latency-tolerant. A higher-accuracy model such as `yolov4-tiny` (40.2% mAP@0.5, ~144 ms/inference) over a faster `yolo-fastestv2` (24.1% mAP@0.5) is the accuracy target for when the detector is wired to an active caller (PROJECT #32).

**Capacity (calibrated to the yolov4-tiny target):**
*   **Throughput requirement:** Sufficient for 2 cameras across 5 nodes.
*   **Capacity validation:** 5 inference tablets yield ~35 inferences/sec (3.5x headroom). A single tablet (~7/sec) is insufficient; two tablets (~14/sec) meet the minimum threshold.
*   **Degradation constraint:** If node count or inference hardware drops below the required capacity, the system degrades to a faster, lower-accuracy model (e.g., `yolo-fastestv2`), reducing detection reliability.

**Floor detector:** The `cpu1` node remains in the detection pool as a mandatory floor. If all tablets are offline or sleeping, `cpu1` ensures the house is still watched. Removing `cpu1` from the pool increases load on the tablets; keeping it is the primary knob for balancing reliability against tablet usage.

### Benchmarking protocol

Before benchmarking any detector, feed it what its consumer actually feeds it. The pipeline crops the motion region to a 320 px square (`data.region` = `[x, y, w, h]` NORMALISED — not corners) and scales that region before sending it to the detector. Feeding whole-room images (704x480) yields invalid results (e.g., animal detection drops from 10/24 to 1/24).

### Failure mode verification

A detector whose failure mode is “silently finds nothing” must be verified beyond a clean startup. Live decoy testing identified the following critical bugs that would have hidden behind a silent failure:
1.  **Linking:** The binary requires `-static-libstdc++`. Termux lacks `libc++_shared.so`, causing exec failures.
2.  **Upgrades:** `ETXTBSY` errors cause every upgrade after the first to fail silently. Fixed by writing beside and then renaming.
3.  **Control frames:** The control frame is a double-resized image that scores 0.0, indicating a broken feedback loop.

## Fellowship Nodes as Detection Compute

The Fellowship tablets provide the compute resources for object detection in the Lorex NVR pipeline. This architecture replaced the retired Frigate deployment; see `RUNBOOK-Lorex-NVR-Operation` for the current operator view.

### Design Fact: CPU-Only Platform Classification
Fellowship nodes are classified as CPU-only platforms for object detection. Neither Qualcomm Hexagon NPU nor DSP paths are available for inference.

*   **Driver availability:** All nodes report `NNAPI feature: 0`. Zero NNAPI HAL drivers are present on the SoC families.
*   **Peripheral libraries:** Nodes *merry* and *pippin* contain Qualcomm Hexagon RPC stubs (`libadsprpc.so`, `libcdsprpc.so`). Accessing the DSP requires the Hexagon SDK and signed DSP images, which are not installed. Node *gandalf* lacks these peripherals entirely.

All detector latency metrics (27.5 ms, 53 ms, 144 ms) represent CPU-bound execution.

### Power Management Requirements
Every Fellowship node must run Termux:Boot to ensure process persistence across reboots. The system must prevent Android power management from suspending or throttling container processes.

#### Doze Mode Whitelist
To prevent the OS from suspending Termux during low-power states, add the Termux package to the device idle whitelist. This ensures CPU cold starts do not interrupt short-burst operations.
```bash
cmd deviceidle whitelist +com.termux
```

#### CPU Wake-Lock Enforcement
To prevent aggressive CPU throttling (up to 12x reduction), a wake-lock must be held active during operation. Use the `termux-wake-lock` command within the Termux app context.

#### Verification and Monitoring Constraints
*   **Whitelist Status:** Verify presence on every node manually or via direct shell access. Do not infer whitelist status from remote metrics alone.
*   **Wake-Lock State:** Wake-lock state is unknown over SSH by design. The command `dumpsys power` returns zero bytes when called by an app UID. Health checks must never interpret this empty return value as "no lock" or a failure condition; it is an expected limitation of the interface.

### Build artifacts (`Lab\fellowship\ndk\`)

**Scripts**
- `ndk_ncnn.sh`: Builds benchncnn with Vulkan support.
- `ndk_llama_vulkan.sh`: Builds llama.cpp with Vulkan support.

**Configuration requirements**
- Cross-compilation requires header-only dependencies placed in the NDK SYSROOT. `find_package` and the include path do not access the host’s `/usr`.
- Both scripts assert that the GPU backend is enabled (`grep NCNN_VULKAN` for NCNN; `GGML_VULKAN:BOOL=ON` for llama.cpp). If assertions fail, builds abort to prevent silent CPU fallback, which would produce a working binary that answers the wrong question.
- Vulkan builds require matching header versions. Host Vulkan headers (e.g., v309) fail static_assert against NDK C headers (v275). Use Vulkan-Hpp v1.3.275 to match the NDK’s v275 C headers.
- Set `ANDROID_PLATFORM=android-28` for Vulkan builds. Vulkan 1.1 symbols (e.g., `vkGetPhysicalDeviceFeatures2`) do not exist in `libvulkan` for `android-24`.

## Hardware Decode Allocation by Node Class

Video decoding targets the dedicated hardware chipset on each tablet. CPU serves only as a fallback when hardware decode is unavailable or unsupported. The constraint is stream resolution compatibility, not node capability: every tablet possesses a hardware decoder block, but maximum accepted resolutions vary by System-on-Chip (SoC).

Decoder selection is deterministic. If a node does not use its designated decoder component, it is a defect.

### Decoder assignments

| Node Class | SoC | Required Decoder Component | Supported Stream Profile |
|---|---|---|---|
| `gandalf` | mt6765 | `c2.mtk.hevc.decoder` | Main stream (3072x1728 @ 30fps) |
| `frodo`, `sam`, `legolas` | mt6765 | `OMX.MTK.VIDEO.DECODER.HEVC` | Main stream (3072x1728 @ 30fps) |
| `merry`, `pippin` | msm8937 | `OMX.qcom.video.decoder.hevc` | Sub stream only (704x480) |

### MSM8937 decoding constraints

Qualcomm SoC nodes (`merry`, `pippin`) decode sub-streams via the dedicated hardware engine. Main streams exceed the configuration limits of this decoder component and fail to configure.

* **Sub stream (704x480):** Decodes successfully using `OMX.qcom.video.decoder.hevc` with hardware acceleration.
* **Main stream:** Decoder fails to configure; unsupported by this SoC's HEVC decoder component.

### `_fellow_burst` inputs

`_fellow_burst` processes recorded playback, which consists of main streams only. On sub-2K-decoder nodes (Qualcomm), burst input forces CPU decoding because the main stream resolution exceeds hardware limits. This is a property of the input path, not node capability. To verify node hardware decode performance, probe live substreams.

### Stream codec conformance

All Lorex camera streams—main and sub-streams, across both channels (ch1, ch2)—encode in H.265 (HEVC). The `hwdec` path and burst processing are HEVC-only; H.264 is unsupported and results in decoder errors.

See [[RUNBOOK-Lorex-NVR-Operation]] §Camera Streams for live conformance verification.

### Camera placement and stream assignment

Any node can serve any camera provided the assigned stream matches its hardware block capabilities. Stream assignment is governed by node configuration (`~/hwdec.json`), not deployment topology.

* **MediaTek nodes:** Assigned main streams (3072x1728 @ 30fps).
* **Qualcomm nodes:** Assigned sub-streams (704x480). This aligns with the detector's native input geometry and prevents CPU-bound decoding.

## TOLKIEN System Role and Deployment Target

TOLKIEN serves as the system head. It manages node health, subscribes to NVR events, decides whether analysis is warranted, and delegates detection workloads across Fellowship nodes via port `:8802`. Frigate has been retired; its prior responsibilities for event processing and object detection are now distributed between Tolkien (orchestration) and the Fellowship tablets (hardware decoding and ncnn detection).

Deployment specifications:
- **Current:** Runs inside the `tablet-foundry` container on **rigby** (<rigby-address>; moved from Sundae 2026-08-28, project 34). The NVR camera subscriber and the `:8802` detect head moved with it; rigby reaches the Lorex NVR (<nvr-address>) over the LAN.
- **Target:** Dedicated VM with persistent disk, database storage, and a web UI.

**Constraint:** TOLKIEN is not a mobile tablet. Designs assuming a Galaxy Tab A7 Lite form factor are invalid.

### Layer responsibilities and ownership

The camera integration pipeline distributes work across the NVR, Fellowship nodes, and Tolkien.

| Job | Owner | Design specification |
|---|---|---|
| Motion detection | **NVR** (hardware) | The NVR generates motion events via `eventManager.cgi?action=attach&codes=[All]&heartbeat=5`. This is a free, hardware-level capability. |
| Still of what moved | **NVR** (`snapshot.cgi`) | The NVR provides snapshot images on motion. No ffmpeg processing is used for still capture. |
| 24/7 recording | **NVR** (local storage) | The NVR handles continuous recording to its local 2 TB drive. Tolkien REFERENCES clips via Path E but never stores raw video. |
| Decode for analysis | **Fellowship nodes** | Every Fellowship node carries the `hwdec` decoder capability. Nodes walk the `HEVC_TRY[]` list, selecting the first configured and starting decoder that matches their hardware (three tablet models exist). Only *gandalf* supports 4K decoding; all other Fellows handle 2K streams, which is the required resolution for this workload. |
| Object detection | **Fellowship nodes** (ncnn) | The detector listens on port `:8801` on all Fellowship nodes with labels `cat,dog,person`. This component is resident but currently uncalled by any upstream service. |
| Action classification ("what is it doing") | **GPU fleet** via orchestrator | Active classification workload. |
| Delegation and placement | **Tolkien** | Tolkien acts as the order taker, manages node health, and serves as the `:8802` head for delegation. |
| Tracking across frames | **Tolkien** | Not built. |
| Zones, dwell times, thresholds | **Tolkien** | Not built. |
| Event store, search, UI | **Tolkien** | Not built. |

### Decode architecture: event-driven burst mode

The Lorex-NVR integration uses an event-driven decode model. The NVR detects motion and signals the system; Tolkien evaluates whether analysis is warranted. If approved, a Fellow hardware-decodes the specific clip segment in a burst, processing every frame for detection, tracking, and dwell. Surviving events are sent to the VLM.

Design consequences:
*   **Idle state:** No decoding occurs when no motion is detected.
*   **Burst resolution:** Motion events are decoded at full frame rate (not the fixed 5 fps of continuous models), supporting `loitering_time` and behavior signatures like crouch-hold-paw. Dwell logic addresses false-positive bands where confidence thresholds fail (e.g., distinguishing Lucy from noise).
*   **Throughput:** Burst decoding stacks with faster-than-real-time playback (§ ops doc). Multiple windows increase speed (4 windows → 4x; 10 windows → 7-9.3x). A 10 s motion clip requires seconds of tablet time; multiple Fellows process clips concurrently.

### `hwdec` invocation rules

The same `hwdec` binary supports both live streaming and burst decoding modes via different argument configurations:

*   **Live stream (--serve mode):** Use `--serve PORT`. This serves raw yuv420p over TCP as an ffmpeg input.
*   **Burst decode:** Use `--out` + `--frames`. The input comes from stdin. Do not use `--input-cmd` in this mode.

⚠ **Critical constraint:** `--input-cmd` is ignored unless `--serve` is also provided. If `--serve` is absent, `in_fd` defaults to 0 (stdin). If no data is piped to stdin, the decoder blocks indefinitely producing no frames and not exiting.

**Correct burst invocation:** Run ffmpeg externally and pipe its stdout into `hwdec`'s stdin. Use an argv list for ffmpeg rather than a shell string. Provide `--out` and `--frames` arguments to `hwdec`, but omit `--input-cmd`. Reference implementation: `tablet_foundry._fellow_burst`.

### Diagnostic pitfalls

*   **Flag existence ≠ mode validity:** Flags like `--input-cmd`, `--out`, and `--frames` are valid and accepted by the parser, but their combination may not function if architectural preconditions (like `--serve`) are unmet. A silent block on empty stdin is a failure mode of burst mode without `--serve`.

### Current system state

Frigate is retired. The following design facts define the current operational baseline:

* **Object Detection:** No distributed detector runs on Fellowship nodes via active upstream calls. The ncnn detector previously resident on port `:8801` (labels: `cat,dog,person`) has no active caller and is effectively idle. Gandalf and Sam retain `hwdec.json` assignments for LIVE-SERVE (`704x480@5fps`, `:8803`), which are vestigial under the current burst-analysis model.
* **Camera Streams:** Both cameras operate at `3072x1728@30fps` HEVC. This is the effective ceiling; 4K resolution is limited to 15fps. Substreams match at `704x480 H.265`.
* **HAI Integration:** `cameras.py` previously pointed at Frigate (`http://frigate:5000`). With Frigate removed, this path is broken until updated to query Tolkien.

#### Grading and Verification Instruments
Tolkien’s output is graded against four existing instruments. These define the precision and recall anchors for development:

1.  **NVR Motion Events (Recall Anchor):** NVR-fired events are candidate positives, not ground truth. They fire on IR noise and clutter. A burst analysis finding nothing on an NVR event is a candidate miss. Contested misses are adjudicated by snapshot review or the owner’s eye; they are never automatically scored as misses.
2.  **`labels_20260808.json`:** Contains 22 frames from channel 1, labelled by the owner. Serves as a precision anchor.
3.  **Lucy Events:** Two events confirmed by the owner (§6 I3). Serve as known-positive clips for live decoy testing.
4.  **the owner’s Eye:** The only recordable quality verdict for final acceptance.

Instruments 2–4 serve as precision anchors. Instrument 1 serves as a recall anchor. Do not grade unbuilt rows against other baselines.

