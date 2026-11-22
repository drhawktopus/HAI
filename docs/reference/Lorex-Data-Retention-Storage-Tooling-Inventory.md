# Lorex Camera Integration - Data Retention, Storage & Tooling Inventory



## 6. Goals · Bonus · Stretch · Impossible

### Scope and Design Goals

The Lorax camera integration system adheres to the following design specifications. Deviation from these specifications constitutes a defect.

| ID | Specification |
|---|---|
| G1 | The service endpoint is `cameras.lan` |
| G2 | **Event log**: Every motion event records timestamp, channel, colour flag, and snapshot path |
| G3 | **Litter-box ROI**: A Region of Interest is defined per camera; events are tagged as box or not-box |
| G4 | **Description**: Selected events carry a natural-language description (requires GPU, uses `scheduler.place`) |
| G5 | **Queryability**: The event log is queryable via HAI |
| G6 | **Service Model**: Runs as a supervised on-box service with restart policy, heartbeat, INCONCLUSIVE verdict handling, and per-channel debounce |

### Bonus targets — likely, low risk if they slip
| | |
|---|---|
| B1 | Cat ID for Lucy / Binks / Zeke in any lighting |
| B2 | All five cats identified during colour hours |
| B3 | Interaction description — "two cats, one swatting" |
| B4 | Discord notice on box events |
| B5 | Fellowship absorbing the cheap tier at full volume (~35 W, zero GPU) |
| B6 | Raise recording to 4K (currently discarding ~89% of the sensor) |

---

### Stretch goals: future capability targets

These capabilities are classified as stretch goals. They are not part of the current baseline scope and require additional verification, model training, or architectural changes before implementation.

| ID | Capability | Design constraint / status |
|---|---|---|
| S1 | **Zag vs Stella IR differentiation.** Colour features currently drive identification; greyscale removal eliminates this signal. Requires a model trained on these specific subjects to succeed in IR mode. |
| S2 | **Confident attribution of "who pooped outside the box."** Requires simultaneous correctness of identity, location, behavior, and timing. Current deployment simplifies this via operational constraints: cats are visually distinct signatures; multi-cat disambiguation is rare because cats are isolated during basement access (single-cat scenarios). Per-cat ID is not an unsolved research problem; it maps to B1/B2 baseline requirements with existing VLM paths. |
| S3 | **Fine-tuned classifier on resident cats.** Feasible using existing NVR archive and proven `cam/playback` extraction for retrospective labeled corpus generation. |
| S4 | **Low-latency alerting (< a few seconds).** |
| S5 | **Cross-camera tracking.** Tracking subjects across camera channels (e.g., ch2 to ch1). |

### In-Scope vs Out-of-Scope: Cat Behavior Monitoring

The camera system monitors cat behavior using observable actions rather than direct detection of waste, which is often unrecoverable on dark stone surfaces at D1 resolution.

#### Out-of-Scope Items (IMPOSSIBLE / Settled NO)
The following capabilities are excluded from the design due to hardware limitations, architectural constraints, or physical impossibility. These exclusions are definitive design decisions.

| ID | Capability | Reason for Exclusion |
|---|---|---|
| I1 | On-camera person/vehicle detection | The Lorex devices lack an NPU and supporting firmware. Verified against vendor documentation and device UI. |
| I4 | Sub-second alerting via the Fellowship network | The Fellowship nodes are designed as a batch farm with a 6x penalty for coupling. Real-time alerting is architecturally incompatible; this is a tool mismatch, not a tuning problem. |
| I5 | Colour-based ID at night | Sufficient color information does not exist in the video frame under low-light conditions. |

#### In-Scope: Behavioral Detection
Accidents occurring on open floors are in-scope when they meet specific behavioral criteria. Unlike in-tray events (e.g., ch1 tray in a dark fireplace recess), which remain heavily occluded and out-of-scope, open-floor accidents occur in decent lighting and are detectable via behavior analysis.

**Detection Logic:**
The system logs behavior rather than attempting to detect waste directly. The observable pattern is defined as:
> "Cat crouched at location X for N seconds, then pawed around the same spot."

This behavioral signature has been proven effective for incidents on open floors (e.g., hearth and adjacent flooring). All relevant zones (including ch2 basement areas) are currently covered with GenAI detections enabled.

## 7. Dead ends — do not re-propose

| design | killed by |
|---|---|
| **Person detection as a filter** | ~77% of events contain a person (the owner lives there); fires on 4 of 5 events — a rubber stamp, not a filter |
| **HOG+SVM pedestrian detector** | found 17/17 people AND flagged 5/5 cat-only frames across an 18-config sweep — accuracy equals the base rate |
| **Tablet GPU (ncnn Vulkan) for detection** | measured and re-measured: 26–35× SLOWER than the same tablet's CPU on these Mali chips; the tablet detector path is CPU |
| **llama.cpp Vulkan on the tablets** | requires Vulkan 1.2; the tablets expose Vulkan 1.1 (`vulkan.version=4198400`) — the backend cannot load |

## The NVR Is a Rolling Buffer; The Lab Is the Archive

The Lorex NVR functions as a short-retention rolling buffer, not a long-term archive. Short retention on the device is acceptable because significant footage is offloaded to persistent lab storage before the buffer cycle completes.

Resource budgeting for the NVR prioritizes video quality over retention duration. Footage requiring long-term preservation is moved to lab storage during offload, where it may be downsampled or processed by available compute resources.

### Path E — On-Demand Clip Export

The system supports human-initiated, on-demand export of specific video clips from the NVR for permanent retention in the lab.

**Workflow:**
1. Operator identifies a moment or event (via log or timestamp).
2. System pulls the corresponding clip from the NVR (`cam/playback`).
3. Clip is retained within the lab storage.

**Implementation Notes:**
- Volume is low; no dedicated fleet or cross-compiled `ffmpeg` is required.
- The existing Sundae `ffmpeg` container handles transcoding/export operations.

**Retention Doctrine:**
NVR retention functions as a rolling buffer. Because only deliberately selected clips are retained permanently, short NVR retention (e.g., ~14 days for 4K footage) is an acceptable design trade-off.

## Camera Access Surfaces and Host Configuration

The camera system presents four distinct interfaces. Each serves a specific operational role; none is specialized for a single subject type (e.g., cats).

| Surface | URL / Location | Function |
|---|---|---|
| **Event Timeline** | `cameras.lan` | Primary household event view. Displays newest-first timeline with motion regions boxed. Supports filtering by camera, region presence, and IR vs. colour status. Served as static HTML via Caddy `file_server`. |
| **Lorex Appliance** | `nvr.lan` | Live video view and raw playback from the vendor NVR hardware. |
| **Homepage Tile** | Dashboard integration | Displays aggregate counts and provides links to the above surfaces. Functions strictly as a status board; does not render image galleries. |
| **HAI Interface** | Query surface | Exposes event log data for querying once the log exists. |

### Host Configuration (Sundae)

The static assets for the Event Timeline are hosted on Sundae.
*   **Source Path:** `/opt/arrstack/cameras`
*   **Caddy Volume Mount:** Read-only bind mount of `/opt/arrstack/cameras` to `/srv/cameras` in `docker-compose.yml`.

### Design Doctrine: Uniform Data Model

The system treats all events as equivalent data points regardless of subject (people, deliveries, pets).
*   **No Subject-Specific Logic:** The interface is not themed around any specific subject. "Cats" are frequent subjects but hold no structural privilege.
*   **Filtering as Structure:** Queries such as "when did I last clean the litter box" and "was anyone at the front door" are structurally identical operations differing only in region and time window parameters. The system relies on filtering rather than special-cased views.

## Event log architecture
The event log resides in per-day JSONL format, not SQLite, to align with laboratory storage conventions. The logs are stored locally at `/opt/arrstack/tablet-foundry/cameras/` on a local ext4 bind mount; CIFS is excluded from this store.

## Retention and backup policy
Metadata retains for 365 days. Snapshots retain for 90 days at 960 px resolution (~126 KB each). A hard 12 GB ceiling enforces pruning on the snapshot directory, removing the oldest files first regardless of age and logging dropped items. These two independent bounds prevent reliance on static event rate assumptions.

Backups operate locally with natural overwrite cycles (approximately 14 generations kept on a second local path). Off-box replication is excluded by design; manual cherry-picking of valuable data off-box is the designated backup strategy for long-term preservation.

## Camera Tooling and Artifacts (`Lab\cameras\`)

The following scripts, configurations, and datasets reside in `Lab\cameras\`. They support NVR integration, frame extraction, motion analysis, and ground-truth labelling.

| File/Directory | Function |
|---|---|
| `nvr_rpc2.py` | RPC2 client for login and configuration read; writes proven inert data. |
| `nvr_config_diff.py` | Snapshot and diff the NVR's full configuration to identify key changes from UI updates. |
| `find_recordings.py`, `list_motion_events.py` | Enumerate recorded events. |
| `make_extract_batch.py`, `extract_frames.sh` | Harvest frames from `cam/playback` via the ffmpeg container. |
| `capture_motion_corpus.py` | Live event-driven snapshot capture. |
| `contact_sheet.py` | Generate numbered sheets for human labelling. |
| `colour_audit.py` | Per-frame colour-vs-IR measurement. |
| `test_hog.py`, `.venv-hog\` | HOG evaluation environment (pinned `opencv-python==4.10`; OpenCV 5 removed `HOGDescriptor`). |
| `labels_20260808.json` | Ground truth dataset: 22 ch1 frames, sourced from the owner. |
| `config-backups\` | Full NVR configuration backup (846 KB, 13,001 keys). |
| `..\tabcluster\ndk\` | NDK cross-compile scripts and `llama-mtmd-cli` for tablets. |

## cuts

- **Destination**: `archive`
  **Content**: Date stamp "built 2026-08-08" from section heading.
  **Reason**: Superseded historical marker; tooling exists currently regardless of build date. The charter forbids live status/dates in docs; the existence of the files is the current state.

- **Destination**: `journal`
  **Content**: Implicit narrative context regarding why these specific tools were built or how they were derived (if any existed beyond the functional descriptions provided).
  **Reason**: Any discovery paths or reasoning behind tool selection belongs in the journal, not the reference manual. The current text contains only functional descriptions, which remain.
