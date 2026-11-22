# RUNBOOK — Lorex NVR Operation & Tooling



## Cameras — the Lorex NVR (Frigate removed 2026-08-12)

⛔ **There is no Frigate.** The container and its `docker-compose.yml` entry were removed on
2026-08-12; on **2026-08-28** every remaining vestige was deleted too — the `/opt/arrstack/frigate`
directory (config + media + `frigate.db`, ~8.7 GB), the `frigate:stable` image, the `frigate.lan`
Pi-hole record and its Caddy route, and the deletion-era backups. Nothing named `frigate` remains
(⚠ hard-deleted with no archive retained — should have archived first; see
`Journal\2026-08-28-claude-fellowship.md`). The tablets now decode and the Fellowship detects, under
the Tolkien head (on **rigby** as of 2026-08-28).

[[Lorex-NVR-Operations]] is the full operations guide for the NVR and camera stack.

| | |
|---|---|
| Credentials | `Reference\Credentials\camera-credentials.md`, GATED |
| Cameras | 2 × Lorex **E841CA** on the NVR's private `10.1.1.0/24` PoE subnet — streams must be pulled THROUGH the NVR |
| Tooling | `Lab\cameras\` — `nvr_rpc2.py` (the client), `read_encode.py`, `set_2k30.py`, `set_3072x1728.py` |

⚠ **ch1 = Dining Room, ch2 = Basement.** A camera move invalidates every zone coordinate and every
derived background.

### 📷 Camera resolution, frame rate and bitrate — what the cameras run and what they can run

**The 30 fps ceiling is `3072x1728`** (5.3 MP, 16:9). Above it only `3840x2160` remains, and 4K is
limited to 15 fps by the camera.

Current format, both channels:

| | live main (`subtype=0`) | recorded | sub (`subtype=1`) |
|---|---|---|---|
| ch1 dining | `hevc 3072x1728 @30`, 6144 | `hevc 3072x1728 @30` | `hevc 704x480`, 1536 |
| ch2 basement | `hevc 3072x1728 @30`, 6144 | `hevc 3072x1728 @30` | `hevc 704x480`, 1536 |

H.265 on every slot, both channels.

**Recordings follow `MainFormat[0]`** — the live main stream. There is no separate lower-quality
recording profile, so any resolution or bitrate change to the live stream lands directly on disk
usage and retention. The purpose of `MainFormat[1..3]` is not established; do not infer a slot's role
from its name. To check what is actually recorded, pull a clip over
`cam/playback?channel=N&starttime=…&endtime=…` and read its SPS.

**Valid main-stream modes**, from the device's own `encode.getConfigCaps` — H.264/H.265,
bitrate 768–6144:

```
3840x2160  3072x2048  3072x1728  2592x1944  2688x1520  3M  2304x1296  1080P  1_3M  720P
```

`2560x1440` is not in that list and will be refused.

⚠ **That list is the RECORDER's claim and does not describe the camera.** It advertises `FPSMax 30`
across every mode including 3840x2160, which the camera cannot do. The E841CA datasheet
(`Lab\cameras\e841ca_specs.txt`) states **4K@15fps, 5MP@30fps, 1080p@30fps, 720p@30fps**. Only the
stream's SPS establishes what a camera actually emits.

⚠ **Declared bitrate caps are soft and codec-dependent.** `getConfigCaps` reports the sub ceiling as
1280 for H.264 and 768 for H.265; both subs are configured at 1536 and emit above 768 on H.265. Read
caps for the current geometry with `Lab\cameras\read_bitrate_caps.py` — a stored capture carries
whatever geometry and codec it was taken under.

**Delivered bitrate is measured, never read from the table** — 20 s stream-copy capture, bytes ÷
duration. ⚠ **"CBR" on this device behaves as capped VBR.** Delivered rate tracks scene content —
a still room encodes well below the configured cap and a busy scene can exceed it. Two channels'
delivered rates differ because their scenes differ, not because one is misconfigured. Compare
direction and magnitude, never equality.

⚠ **The SUBSTREAM is hard-capped at D1 704x480**, device-wide — all three `ExtraFormat` slots on
both channels. There is no higher-resolution sub stream hiding in a slot.

⚠ **There are SEVEN encoder slots per channel, not two:** `MainFormat[0..3]` + `ExtraFormat[0..2]`.
`MainFormat[0]` is what RTSP `subtype=0` serves **and is also what gets recorded** (measured — see
the table above).

### Changing an encoder setting

**Write via RPC2** — `Lab\cameras\nvr_rpc2.py`, `configManager.setConfig`. This is the interface the
NVR's own web UI uses.

⛔ **CGI `setConfig` returns "OK", writes the table, and the encoder never emits the value.** If it
is used anyway, brackets must be URL-encoded and an empty response body is not success.

⛔ **A read-back does not prove an encoder change took.** It proves the recorder stored it; the
encoder lives in the camera. Verify against the stream's SPS.

* A **refused** resolution still writes to the table, leaving a value the device never emits. Resync
  the table after any refusal. `set_3072x1728.py` auto-reverts when the stream does not match.
* **Allow ~80 s for a stream to settle** before reading it. At 12 s a changed resolution can still
  report the old frame rate, which is indistinguishable from the frame rate having been silently
  halved.
* A camera that reconnects, power-cycles or is physically moved **can return with different encoder
  settings**. Re-read every slot and re-measure the stream after any physical work.
* **Bitrate caps at 6144.** 8192 is accepted into the table and ignored.

### Other NVR behaviour

* **Playback is paced 1× per session.** Pull concurrent windows for ~7-10× (overnight ≈ 50 min).
  `loadfile.cgi` returns 400 and `RPC_Loadfile` requires an RPC2 cookie; neither works on this
  firmware.
* **The E841CA supports on-camera person/vehicle detection, and it is switched off.** The cameras
  are not analytics-blind — the feature is unexposed, not absent. An empty `getConfig` response and
  a `400` are different results: empty means a valid key that is unset, 400 means no such key.
* **`ffprobe` runs on a tablet, not Softserve** — Softserve has no ffmpeg. gandalf
  (`<lan-address>`, ssh port 8022) is the usual probe host.

### Running the camera tools: the settings they read (NVR_HOST, CAMERA_CRED_DOC, NVR_USER / NVR_PASSWORD)

The tools in `Lab\cameras\` read every lab value from `Lab\etc\lab.env` (since 2026-11-20), never from the script:

| Setting | What it is | Read by |
|---|---|---|
| `NVR_HOST` | the NVR's address | `nvr_settings.py` (every NVR tool), `hwdec-test/settings.sh`; `extract_frames.sh` takes it from its container's environment |
| `CAMERA_CRED_DOC` | the camera credentials document (the NVR's login first, then Frigate's) | `nvr_settings.login()`, `build_site.py` |
| `NVR_USER` / `NVR_PASSWORD` | the NVR login in the environment; when both are set they win over the document | `nvr_settings.login()` |
| `CAMERAS_SITE_URL`, `NVR_SITE_URL` | the cameras site the lane publishes to, and the NVR's own pages it links | `submit_motion_order.py`, `build_site.py`; `submit_paired_order.py` (run in the orchestrator container) takes `CAMERAS_SITE_URL` from its environment |
| `FELLOWSHIP_ROSTER`, `TABLET_SSH_KEY`, `ADB_EXE` | the tablets, their ssh key, adb | `cam_settings.py` (`tablets()`, `tablet(name)`); `hwdec-test/settings.sh` sources `fellowship/settings.sh` |

A tool run on another box (Sundae, a tablet) gets that box's values from its environment or `LAB_SETTINGS_FILE`; the
roster path in `lab.env` is Softserve's, and `tablets()` says so when it cannot read it. The tablet-side
`hwdec-test/run_serve*.sh` and `burst_clip.sh` take `NVR` and `CRED` from their caller. Stream URLs come from
`nvr_settings.rtsp()`, which percent-encodes the login. The decoder's per-tablet config dumps
(`hwdec-test/hwdec.*.json`) carry the login inside their stream URL and stay out of git.

---
