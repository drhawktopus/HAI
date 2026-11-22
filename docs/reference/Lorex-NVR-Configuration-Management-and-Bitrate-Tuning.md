# Lorex NVR - Configuration Management and Bitrate Tuning



## Configuration Verification

Camera moves invalidate pixel coordinates and derived backgrounds (zones, masks, medians). Stale configurations may load and evaluate against incorrect geometry. To verify a channel's ground truth:
1. Query the name via `getConfig&name=ChannelTitle`.
2. Compare the returned name with a live snapshot of the feed.

Names are user-typed metadata; visual snapshots are the source of truth for alignment.

## NVR `setConfig` API: URL-encoded brackets and empty response interpretation

The Lorex NVR `setConfig` endpoint requires square brackets in parameter names to be URL-encoded. An empty HTTP response from this endpoint does not indicate success; it indicates a malformed request due to missing or incorrect encoding.

## Configuring NVR stream bitrate via CGI

**Design fact:** Lorex NVR streams are configured exclusively through the `configManager.cgi` interface. Direct enumeration of supported values via `getConfigCaps` is unsupported on this unit; configuration relies on copying parameters from existing, functional channels.

## Write operation
Use `curl` with Digest authentication to set bitrates. The `Encode` index is 0-based (`Encode[1]` targets channel 2).

```bash
curl --digest -u admin:<pw> -G http://<nvr-address>/cgi-bin/configManager.cgi \
  --data-urlencode "action=setConfig" \
  --data-urlencode "Encode[1].ExtraFormat[0].Video.BitRate=512"
```

## Verification procedure
Always verify the written value by reading it back. Do not assume success from HTTP status codes alone.



### Encoder Slot Architecture and Resolution Conformance

Each channel exposes **seven encoder slots**: four `MainFormat` slots (`MainFormat[0..3]`) and three `ExtraFormat` slots (`ExtraFormat[0..2]`). Configuration targets a specific slot index; setting "the main stream" requires selecting one of the four MainFormat options.

#### Slot Definitions

| Slot | Function |
|---|---|
| `MainFormat[0]` | General / live view. Served by RTSP `subtype=0`. |
| `MainFormat[1]` | Motion-triggered recording. |
| `MainFormat[2]` | Alarm recording. |
| `ExtraFormat[0]` | Primary sub stream (RTSP `subtype=1`). |
| `ExtraFormat[1..2]` | Secondary and tertiary sub streams. |

#### Design Facts

1.  **Independent Resolution per Slot:** Live view (`MainFormat[0]`) and recorded clips (`MainFormat[1]` or `MainFormat[2]`) are configured independently. They may differ in resolution and frame rate.
2.  **Sub-Stream Resolution Cap:** All three `ExtraFormat` slots are capped at 704x480 (D1). No higher-resolution sub-streams exist in any slot. This confirms the D1 cap for all auxiliary streams.

### Camera Reconnect Resets Encoder Settings

Camera reconnects, power cycles, and physical maintenance events are not idempotent; they silently revert encoder configurations to defaults. Specifically, toggling `RemoteDevice.uuid:….Enable` false→true reverts the substream codec and resolution (e.g., from H.264/1536 back to H.265/256) without generating a log entry.

After any camera reconnect, power cycle, or physical work:
1. Re-read the ENCODER SLOTS.
2. Re-measure the stream configuration.

**Rule:** Do not assume state persistence across connectivity events. Verify encoder settings explicitly after every disconnection.

### Lorex NVR Substream Resolution and Bitrate Limits

The Lorex NVR caps substream resolution at D1 (704x480) device-wide. This is a unit limitation, not a per-channel fault; all ExtraFormat slots on both channels report this maximum, and higher resolutions are refused.

Substream bitrate is variable within hardware limits:
- 512–1536 kbps Constant Bitrate (CBR): Accepted on both channels. Measured stream rate approximates 1540–1600 kb/s at the upper limit.
- 3072 kbps CBR: Silently refused.

To configure bitrates above default levels, use the "copy settings from a channel already running them" rule. This method is required because the NVR does not enumerate its own bitrate capabilities.

### The RPC2 route exists — CGI evidence cannot prove an NVR limitation

`system.listService` over RPC2 returns 305 services on this unit, including
`RemoteDevVideoInput` — encode-side controls that CGI probes report absent. The CGI write
path is a documented lying success (returns OK, changes nothing), so any "the NVR cannot
do X" conclusion whose evidence is entirely CGI is unproven; check the RPC2 service list
before declaring a limitation.

#### RPC Parameter Shapes from Client Source

The Lorex NVR serves its own JavaScript client at `/app/jsCore/RPC.js` without authentication. This file defines the exact parameter shapes required by the RPC API, superseding documentation or community forums.

**Key Design Facts:**
* Instance handles are keyed by **UUID**, not by channel index.
* Capability enumeration requires `config` AND `stream` parameters; `channel` alone is insufficient.
* Configuration operations targeting a remote device must include a `target` extra parameter.

**Confirmed Working Patterns:**

```js
// Instance resolution uses UUID
RemoteMagicBox.getProductDefinition = (uuid, name) =>
    instance({uuid}).then(o => send("RemoteMagicBox.getProductDefinition", {name}, {object: o.result}))

// Capability enumeration requires config and stream
send("encode.getConfigCaps", {channel: N, config: C, stream: "All"})

// Remote device configuration uses 'target' extra
getConfigTarget = (name, ch, x, target) => send("configManager.getConfig", {name, channel}, {target})
setConfigTarget = (...)                 => send("configManager.setConfig", payload,          {target})
```

**UUID Resolution:**
* `RemoteMagicBox.factory.instance({"uuid":"uuid:System_CONFIG_NETCAMERA_INFO_0"})` returns an object ID.
* UUIDs are retrieved via `configManager.getConfig{name:RemoteDevice}` or `LogicDeviceManager.getCameraAll`.
* Channel mapping:
  * ch0 → `10.1.1.128`
  * ch1 → `10.1.1.129`
  * Both use port **35000**.

#### Open questions — RPC2 encode control

* `configManager.getConfig` with `{target:0}` and `{target:1}` returns byte-identical
  tables matching the recorder's own — `target` is probably silently ignored. Do not
  report it as "reading the camera" without proving the two differ.
* `encode.getConfigCaps` needs the right `config` value; the UI passes it in from the
  encode page module (not `RPC.js`) — the caller still needs finding.

### Camera Resolution Constraints and Access Limits

The E841CA hardware supports 4K@15, 5MP@30, 1080p@30, and 720p@30. Lorex markets 5MP as "2K".

#### Direct Camera Access is Blocked

The NVR does not provide direct API access to individual camera configuration or capabilities from the LAN. All attempts to bypass the NVR result in failure:

| route | result |
|---|---|
| Cameras on NVR PoE subnet (10.1.1.128/.129, Port 80) | Unreachable from LAN; TCP timeout on ports 80 and 554 |
| Dahua Virtual Host / channel pass-through | `VirtualHost`, `RemoteDeviceVirtualHost`, `NetWorkVirtualHost` return **400** (invalid key) |
| Proxied port on NVR | Only ports 80 and 35000 are open; neither exposes a camera interface |
| `encode.cgi?action=getConfigCaps` | Returns **400** (not supported on current firmware) |
| `configManager.cgi...&channel=N` targeting remote device | Returns **400** |

#### Verification Methods and Observations

- **API Response Interpretation**: A `400` response indicates the endpoint/verb does not exist. An empty `200` indicates the verb exists but the value is unset. These states are distinct; conflating them leads to false conclusions about missing features (e.g., person detection).
- **Capability Check**: `devVideoInput.cgi getCaps` returns `MaxWidth=3840 MaxHeight=2160`, confirming hardware support for 4K.
- **Device Identification**: `magicBox.cgi getDeviceType` returns `N844A8`.
- **Resolution State Discrepancy**: The field `CustomResolutionName` may display `3840x2160` while the active stream emits 1080p (verified via `resolution`, `Width`, `Height` fields showing 1920x1080). Always verify active resolution via live stream fields, not metadata labels.

#### High-Resolution Video Requirement

To achieve 5MP@30 ("2K30") video streams, a camera must be physically disconnected from the NVR's internal PoE switch and connected directly to the LAN switch with an external PoE injector. This modification is not recommended for detection workflows, as model input constraints make high resolution unnecessary.
