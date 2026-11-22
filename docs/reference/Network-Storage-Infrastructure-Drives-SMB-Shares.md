# Storage Infrastructure: Drives & SMB Shares



## Softserve Drives & SMB Shares

[Section content required]

### Drive Assignment

The lab uses three primary drive letters for specific functional roles. These assignments are fixed; changing them breaks path-dependent configurations in VMs, scripts, and the HAI index.

| Letter | Label | Size | Purpose |
|--------|-------|------|---------|
| C: | — | 464.9 GB | Windows OS partition |
| E: | 1 TB SSD Storage | 931 GB | Downloads staging, application data, VM configuration files |
| Z: | Big Boy Pool | 46 TB | HDD array, media library storage |

**Design Rule:** The `Z:` drive is the root for all large-artifact archives and documentation backups. Do not use `C:` or `E:` for long-term storage of lab artifacts; they are reserved for system stability and active workflow staging.

### SMB Shares

The homelab server exposes the following SMB shares for media access and data staging. All paths resolve to local disk mounts on `<softserve-address>`.

| Share Name | UNC Path | Local Mount Point | Content Scope |
|---|---|---|---|
| Media | `\\<softserve-address>\Media` | `Z:\Media` | Movies, TV, Music |
| SSDShared | `\\<softserve-address>\SSDShared` | `E:\SSDShared` | Downloads/staging, app data |
| Big Shared | `\\<softserve-address>\Big Shared` | `Z:\` | Full HDD array |

### Key Paths on Softserve

- **Plex data storage**: Data resides at `E:\ProgramData\Plex LocalAppData\Plex Media Server`. The path `C:\Users\<user>\AppData\Local\Plex Media Server` is a directory junction to this location; both resolve to the same data. Settings and server tokens are stored in the Windows Registry (`HKCU\Software\Plex, Inc.\Plex Media Server`; value `PlexOnlineToken`). There is no `Preferences.xml` file. See `Runbook\Plex.md` for token details.
- **Plex execution model**: Plex runs as a console-session tray app under `SOFTSERVE\Administrator` via auto-login. The `PlexWatchdog` scheduled task relaunches the app if it terminates. It does not run via the WinSW service.
- **WinSW service status**: The WinSW Plex service at `C:\ProgramData\PlexService\` is disabled and unused.
- **Remote access**: Remote access uses UPnP-mapped port assignment.
- **Arr config backup**: Backup data resides at `E:\SSDShared\arr-config-backup\` (directories: `raw/`, `zips/`).
- **HAI storage topology**: `Z:\Documentation` and `Z:\Workspace` are directory junctions pointing to `E:\Documentation` and `E:\Workspace` respectively. This isolates HAI RAG reads and State writes from Plex media traffic on the shared spinning array (`Z:\Media`). SMB shares, Sundae CIFS mounts, and all `Z:\` references resolve through these junctions transparently.

### SMB Mounts on SundaeStackarr

SMB shares are defined in `/etc/fstab` with options `nosharesock,_netdev,nofail,seal`. Credentials reside at `/etc/smb-credentials` (mode 600, root only).

| Mount | Share | Purpose |
|-------|-------|---------|
| `/mnt/media` | `\\<softserve-address>\Media` | Z:\Media — all media |
| `/mnt/ssdshared` | `\\<softserve-address>\SSDShared` | E:\SSDShared — downloads |
| `/mnt/documentation` | *(not specified in source)* | Documentation |
| `/mnt/workspace` | *(not specified in source)* | Workspace |

**Design constraint:** All CIFS mounts must include `nosharesock` and `seal` to prevent SMB signature verification errors (`CIFS: VFS: SMB signature verification returned error = -13`). Softserve requires SMB signing; shared sockets across mounts cause verification failures. Encryption via `seal` (SMB3 AES-GCM) supplies integrity, preventing the failing write-signing path.

**Verification:** Mounts must show `encrypted` in `/proc/fs/cifs/DebugData`. Write tests must produce zero sign-fail errors. Health check (`hai /health`) must return 200.

### Softserve ↔ Mothership 2.5G SMB — RSS/Multichannel Configuration

SMB between **Softserve** (I225-V, .204) and **Mothership** (I226-V, .76) requires Intel driver registry modifications to enable RSS and Multichannel. The stock driver (v2.1.5.7) ships with `RssCapable=False`; without explicit configuration, SMB signing (`RequireSecuritySignature=True`) restricts throughput to a single thread (~940 Mbps).

**Required Registry Configuration:**
Set the following values under each NIC’s registry class key:
`HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\[DeviceID]`
*   Softserve DeviceID: `0004`
*   Mothership DeviceID: `0000`

| Value Name | Type | Data | Purpose |
|---|---|---|---|
| `*RSS` | REG_DWORD | `1` | Enables RSS. If missing or 0, `RssCapable=False`. |
| `*NumRssQueues` | REG_DWORD | `4` | Sets queue count for Multichannel. |

After applying these keys, restart the adapter. Verify `RssCapable=True`. Expected SMB throughput with signing enabled is ~2073 Mbps.

**Maintenance:**
*   NIC driver reinstalls may remove `*RSS` settings. If throughput drops to ~940 Mbps, re-apply registry keys.
*   Revert files are stored on both hosts: `C:\Users\Public\rss_backup.json` and `rss_revert.ps1`.

**Bulk copies Softserve → Mothership: use SMB, not tar-over-ssh.** The link is 2.5G (~2.1 Gbps measured), so a copy
that levels off near ~110–160 MB/s is NOT the network. 2026-09-29: the 360 GB Flash-Next weights went `tar | ssh
mothership "tar -x"` and averaged ~135 MB/s (ssh encryption runs on one core; the Z: HDD array and D:'s QLC writes are
the other suspects). Twice now a session has read that ceiling as "gigabit" without checking this section. Unmeasured:
which of the three it was, and what a plain SMB copy of the same set reaches.

### Mothership Routing Priority for <lan-subnet>/24

Mothership must route local `<lan-subnet>/24` traffic over the direct 2.5G Ethernet interface, not Tailscale. Softserve advertises the /24 as a subnet router; incorrect metrics cause traffic to take the higher-latency Tailscale path.

**Required Configuration on Mothership:**
1.  Set Ethernet `InterfaceMetric=1` (lower than Tailscale’s metric of 5).
2.  Add a persistent static route for `<lan-subnet>/24` via the direct Ethernet interface.

Verify routing table ensures local LAN traffic bypasses Tailscale.
