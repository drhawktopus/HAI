# Lorex Camera Integration - Hardware, Network & Stream Specs



## Physical Topology and Network

The video surveillance system uses a Lorex **N844A8** NVR (`<nvr-address>`). Cameras connect via the NVR’s private PoE subnet `10.1.1.0/24`. Streams are pulled through the NVR, not directly from cameras.

The domain `cameras.lan` is active (resolved via Caddy + Pi-hole).

### Channel Mapping and Field of View

NVR channel labels do not match physical room locations (the channels were renamed and ch2's camera physically relocated late 2026-08-08). Current mapping, device-verified via the NVR `ChannelTitle` config:

| Channel | Physical Location | Field of View |
| :--- | :--- | :--- |
| **ch1** | Dining Room | Fireplace (litter tray inside the fireplace recess), dining table. |
| **ch2** | Basement | Couch, desk, litter tray, water bowl. |

> **Design Fact:** Channel identity is determined by viewpoint content, not NVR label name. Use the channel index for routing (event `index` is 0-based: `index = channel - 1`); use context for event interpretation. A camera move invalidates every zone, mask and derived background from before it.

## Stream Configuration and Performance Constraints

### Main Stream

- **Resolution:** 3072x1728 (4K equivalent, 16:9 aspect ratio).
- **Codec:** H.265 (HEVC).
- **Frame Rate:** 30 fps.
- **Bitrate:** 6144 kbps CBR.
- **Uniformity:** Both channels use identical settings. This allows any processing node to handle any camera stream without format negotiation.

**Hard Limits:**
- The camera hardware caps output at 30 fps for resolutions below full 4K (3840x2160).
- Full 4K resolution is limited to 15 fps by the sensor encoder. This limit cannot be exceeded via firmware or configuration.

### Substream

- **Resolution:** 704x480.
- **Codec:** H.265 (HEVC) on both channels (per [[RUNBOOK-Lorex-NVR-Operation]], "H.265 on every slot"; confirm the emitted codec by SPS, never from the config table).
- **Bitrate:** 1536 kbps.
- **Frame Rate:** Implicitly capped by device hardware; verified as uniform across both channels.

**Design Fact:** Substream resolution is a device-wide hard cap. It cannot be increased. Uniformity between channels is mandatory for node compatibility.

See [[RUNBOOK]] §"What resolution / frame rate can the cameras run?" for safe change procedures and valid mode lists.

## API Interfaces

All interfaces were verified operational against the current NVR firmware.

| Interface | Purpose | Notes |
| :--- | :--- | :--- |
| `eventManager.cgi?action=attach&codes=[All]&heartbeat=5` | Live motion event feed | Push-based alert stream. |
| `snapshot.cgi?channel=N` | On-demand JPEG snapshot | No external processing (e.g., ffmpeg) required for single-frame capture. |
| `rtsp://.../cam/realmonitor?channel=N&subtype=0\|1` | Live video streaming | `subtype=0`: Main stream (H.265). `subtype=1`: Substream (H.265). |
| `rtsp://.../cam/playback?channel=N&starttime=...&endtime=...` | Retrospective video playback | Requires precise timestamp ranges. |
| `mediaFileFind.cgi` | Event enumeration | Lists recorded event files. |
| `/RPC2` (JSON-RPC) | Configuration and status | **Writes are accepted.** Use this interface to change stream parameters or system settings. See client implementation: `Lab\cameras\nvr_rpc2.py`. |

**Critical Distinction:** RPC2 accepts configuration writes. CGI interfaces do not reliably accept writes (often returning success without applying changes). Always use RPC2 for configuration mutations.

## On-Device Intelligence

The Lorex E841CA cameras **support on-camera person/vehicle detection** — the feature exists in firmware and is currently switched OFF, not absent. The cameras are not analytics-blind.

- Person/vehicle detection is a real, unexposed capability on the E841CA; it can be enabled on-camera.
- The lab's analytics nonetheless run EXTERNALLY: an NVR hardware motion event → a Fellowship tablet hardware-decodes the clip → the resident ncnn detector on `:8801` (labels cat/dog/person) → the Tolkien head. The external path does the fine-grained cat / litter-box work the on-camera person/vehicle feature does not.

**Design Fact:** Do not state that the cameras cannot detect. On-camera person/vehicle detection exists and is switched off; the external Fellowship→Tolkien pipeline is the lab's chosen analytics path.

## Main Stream Design Doctrine

The main stream provides the primary ingestion source for motion detection and event classification. Both channels encode the main stream at **3072x1728 @ 30 fps** using **H.265 (HEVC)**, capped at **6144 kbps**. This is the 30 fps ceiling (5.3 MP, 16:9); 3840x2160 (4K) is available only at 15 fps. Channel 1 and Channel 2 share this configuration.

### Rationale
*   **Spatial fidelity priority:** The system’s primary goals (identifying specific cats, observing actions, locating subjects relative to the tray) are spatial problems, not temporal ones. Higher resolution is prioritized over higher frame rates within fixed bitrate constraints.
*   **Bitrate efficiency:** H.265 provides approximately half the bit count for equal quality compared to H.264, partially offsetting the storage cost increase from upscaling to 4K.
*   **VBR usage:** Variable Bitrate allows dynamic allocation of bits to complex scenes while saving space on static frames, optimizing retention within the fixed disk capacity.

## Substream Configuration

The substream is configured at **704x480 @ H.265** (per [[RUNBOOK-Lorex-NVR-Operation]], which reports H.265 on every slot, both channels; verify the emitted codec by decoding the SPS). This stream serves as the input for cost-effective tier consumers and low-resolution previews. Resolution is not increased to avoid unnecessary storage overhead without functional benefit.

## Playback and Codec Compatibility

*   **Mixed codec tolerance:** The pipeline supports mixed codecs on disk. Live ingestion uses H.265, while legacy recordings remain in H.264. `ffmpeg` successfully pulls playback frames from older H.264 recordings, ensuring historical data remains accessible.
*   **Live validation:** `snapshot.cgi` returns valid 4K JPEGs. `ffmpeg` pulls live frames successfully. The Lorex mobile application plays both main and substreams correctly.

## Tooling Limitations and Workarounds

*   **RTSP Description Script:** `rtsp_describe.py` reports `unparsed` for H.265 main streams because it currently decodes only H.264 SPS data. This is an expected limitation, not a system fault.
    *   **Workaround:** Use the `rtpmap` line in the RTSP description to identify the codec. If resolution verification is required, implement an HEVC parser or use alternative validation methods (e.g., live frame capture via ffmpeg).
*   **Retention Monitoring:** Retention duration is scene-dependent and must be measured, not calculated. The existing `check_retention.py` script currently returns HTTP 400 errors for most date probes and cannot distinguish between "no footage" and "query rejected." This tool requires repair before it can provide accurate retention metrics. Until repaired, assume retention is bitrate-bound and monitor manually.

## Change Control Procedure

*   **Rollout strategy:** Codec or major resolution changes are applied one channel at a time to isolate failures. One channel is held on the previous configuration as a control while the other is updated. This ensures at least one functional camera remains available during transitions.

---
title: Lorex Camera Encoder Constraints and Verified Behaviors
---

### Lorex camera encoder constraints and verified behaviors

The Lorex camera enforces hardware limits that differ from generic datasheet values. These constraints are design facts; deviations indicate a defect.

| Parameter | Expected Request | Actual Behavior | Constraint / Status |
|---|---|---|---|
| Bitrate (4K) | 8192 / 16384 kbps | Clamped to **6144** kbps | Hard cap at 4K resolution. Quality cannot be purchased via bitrate increases above this limit. |
| Frame Rate | 10 / 15 / 30 fps | Accepted | Discrete set of supported values. |
| Frame Rate | 20 / 25 fps | Silently retained previous value | Unsupported discrete values are ignored; the camera retains the last valid frame rate. |
| Codec (H.265) | H.265 | Accepted; all slots currently emit H.265 | H.265 (HEVC) is the codec on every slot, both channels (per [[RUNBOOK-Lorex-NVR-Operation]]; confirm by SPS). An earlier report of a silent revert to H.264 is unexplained and superseded — see §H.265 Codec Behavior. |
| Bitrate Mode | VBR | Accepted | **UNVERIFIED**: Effective behavior under variable bitrate conditions requires further testing. |

**Verification**: See [[Lorex-Camera-Integration-SCOPE]] §VBR Verification Status for ongoing tests.

### Lorex E841CA Frame Rate Constraints

The Lorex E841N camera hardware enforces strict frame rate ceilings based on resolution. The NVR interface does not validate these constraints; it accepts configuration values that exceed the camera's physical capabilities, resulting in silent failure or undefined behavior.

**Hardware Spec (Verbatim):**
```
Video Compression:      H.265/H.264H
Resolution/Frame rate:  4K(8MP)@15fps, 5MP@30fps, 1080p@30fps, 720p@30fps
```

**Design Fact:**
The maximum frame rate at 4K (3840x2160) resolution is **15 fps**. The NVR will accept a setting of 30 fps for 4K streams, but the camera cannot deliver this output. Any configuration attempting to exceed 15 fps at 4K is invalid.

**Configuration Rule:**
To achieve 30 fps, resolution must be lowered to 5MP or below. 30 fps is not available at 8MP/4K.

**Available Operational Modes:**
- **4K @ 15 fps:** Selected for identification detail.
- **5MP @ 30 fps:** Selected for motion clarity (e.g., distinguishing group dynamics).

These modes are mutually exclusive. The camera cannot output both high resolution and high frame rate simultaneously.

### H.265 Codec Behavior

Lorex E841CA cameras support H.265/H.264H encoding, as stated in the official spec sheet.

The N844 manual (p30) recommends selecting H.265 for compatible cameras to minimize disk space usage.

**Current state:** every slot emits H.265 (per [[RUNBOOK-Lorex-NVR-Operation]]; SPS is the authority).

**Historical note (superseded):** an earlier observation held that `Compression=H.265` was stored but the RTSP stream still advertised `H264`. The likely cause is `rtsp_describe.py`, which decodes only H.264 SPS and reports H.265 streams as `H264`/`unparsed` (see §Tooling Limitations) — a tool artifact, not the emitted codec. Always confirm the emitted codec by decoding the SPS with an HEVC-aware tool, never from `rtsp_describe`'s rtpmap or the config table alone.

### VBR Quality Configuration

Variable Bit Rate (VBR) requires an explicit Quality parameter. The valid range is 1 (lowest) to 6 (highest). If Quality is not explicitly set, the device defaults to `Quality=4`, which may produce lower fidelity than expected.

**Encoder State Asynchrony**: Device firmware applies encoder configuration changes asynchronously. A successful `setConfig` response indicates the command was queued, not that the change has taken effect. The device requires a settle period before new settings are active.

**Verification Procedure**: Do not trust the config table state immediately after write. Verify encoding parameters by decoding the H.264 Sequence Parameter Set (SPS) from the live stream. This confirms what the encoder is actually emitting.
