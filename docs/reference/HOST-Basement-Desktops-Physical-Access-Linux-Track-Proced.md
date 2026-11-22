# Basement Desktops — Physical Access & Linux Track Procedures (§A)



## §A — Physical Access Gateways (A1–A3)

Physical presence is required to execute steps A1 through A3. These steps constitute the mandatory prerequisite for all subsequent onboarding procedures in this document.

### A0. Hardware inventory recording procedure
Record the physical hardware specification for each basement desktop host in [[Network-Infrastructure]] §"The basement desktops".

Required data points:

- **CPU**: Model string, core/thread count, and base clock speed. This value determines CPU-tier classification (e.g., i5 vs i7).
- **RAM**: Total capacity and idle free memory. Worker-count calculations in §C are RAM-bound, not core-bound.
- **Disks**: Drive letters, total size, and available free space. These values determine the `FOUNDRY_ROOT` location (§D3).
- **GPU**: Presence flag and make/model. This determines applicability of GPU-host power standards (A4b) and classifies the host as either a CPU-only scheduler target or a full factory node.
- **OS**: Operating system name, edition, and build number. Verify Hyper-V availability on `Worktop` (requires Pro or better). Confirm OS presence and type on `mordecai`.

### A1. OpenSSH Server configuration
- Install and enable the OpenSSH **Server** feature; set the service startup type to Automatic; confirm listening on port 22.
- Add Softserve's public key to the user's `authorized_keys` file.
- Create `administrators_authorized_keys` at `C:\ProgramData\ssh\`, ACL'd to Administrators+SYSTEM. An admin SSH session lacks administrative privileges without this file. See [[RUNBOOK-Elevation-and-Headless-Tasks]] §ELEVATION for troubleshooting elevation failures.



### A3. Workgroup Assignment: `MOSSMATRIX`

Every host in the lab must reside in the workgroup `MOSSMATRIX`. No host is domain-joined.

**Verification and Correction:**
1.  Verify the host reports workgroup `MOSSMATRIX`.
2.  If a host shows membership in any other workgroup or a stale domain join (e.g., legacy `ad.<tailnet>.ts.net`), remove it using the offline WMI method documented in [[RUNBOOK]] §Workgroup / domain.
    *   **Constraint:** Do not use `Add-Computer`. It requires a live Domain Controller and is invalid for this environment.

**Rationale:**
Stale domain joins to dead Active Directory domains break security-descriptor creation and block SMB peer functionality (e.g., Plex metadata writes). See [[RUNBOOK]] §Workgroup / domain for the impact analysis.

### A4. Power configuration

Power settings are configured per power scheme, not per active profile. Verification requires reading each scheme individually using the procedure in [[RUNBOOK-GPU-Host-Power-and-Glances]].

#### A4a: Disable automatic sleep for headless hosts

Headless operation (SSH/RDP-less or network sessions) is not treated as user presence by Windows. Consequently, idle timeouts fall to the `UNATTENDED` timer.

*   **Design fact:** `UNATTENDSLEEP = 0` and `STANDBYIDLE = 0` are required on **every** power scheme for all basement desktops, regardless of GPU presence.
*   **Verification:** Confirm settings via per-scheme `powercfg /query`. Do not rely on the active scheme’s state; the fleet-wide fix must be present on all schemes (Balanced, High performance, etc.) to prevent drift when the profile changes.

#### A4b: High performance + ASPM Off for compute GPUs only

Apply `High performance` profile with `ASPM Off` **only** if the host contains a GPU performing compute work (see **§A0**).

*   **Design fact:** This configuration is scoped strictly to GPU compute hosts.
*   **Constraint:** Do not apply these settings to CPU-only boxes. Adding unproven extras (e.g., `TdrDelay`) alongside the proven change renders outcomes unattributable.
*   **Procedure:** If A0 identifies a compute GPU, apply A4b. If not, skip it explicitly and record the decision.

### A5. Hostname provisioning during onboarding

1. Onboard hosts under their existing hardware names (`Worktop`, `mordecai`). These are valid identifiers that allow the process to proceed without blocking.
2. Do not assign a permanent lab name at this stage. The final hostname is determined by the tier classification result in §C, per the owner's tier policy (§D1).
3. Rename to the final tier-appropriate hostname is a planned migration step executed in §E. This ensures coordinated updates across DNS, SSH aliases, `FOUNDRY_FLEET_HOST`, per-host state files, and documentation.

### A6. Verify from the box, before walking away

The operator verifies host readiness via these checks:

- `ssh` login from Softserve succeeds.
- The elevation check returns **True** (see [[RUNBOOK]] §ELEVATION):
      ```bash
      ssh <host> "powershell -NoProfile -Command \"\$p=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); \$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)\""
      ```
- `powercfg /query <guid> SUB_SLEEP UNATTENDSLEEP` reads 0 on each of the four schemes.

DHCP reservations are managed by the owner; they are not part of this verification list. Until reservations exist, addresses are dynamic. Use the DNS name instead of the IP address for all connections.

## §A-L — AT THE BOX, **LINUX** TRACK (use INSTEAD of §A for Ubuntu hosts)

This track applies to all Linux hosts in the basement fleet, specifically `mordecai` and `Worktop`.

All basement Linux hosts run Ubuntu 24.04 LTS. Version uniformity is mandatory: every node must run the same Ubuntu release version to ensure identical OpenMPI, BLAS, and compiler versions. This prevents performance confounds in HPL comparisons and fragility in MPI jobs. Mixing releases (e.g., 24.04 and 26.04) is prohibited.

Upgrade all nodes simultaneously when a new LTS path opens. Never upgrade individual hosts alone.

Desktop vs. Server editions may be mixed freely. Only the version number matters for conformance.

See §A-Windows track for Windows host procedures.

### A-L1. ssh — Ubuntu Desktop does not ship `openssh-server`
- Install and enable the service: `sudo apt install -y openssh-server && sudo systemctl enable --now ssh`
- **Verification:** Check the service status, not the GUI toggle.
  - The GNOME Settings → Sharing → "Remote Login" toggle is **not sufficient** if `openssh-server` is uninstalled; it fails silently because no unit exists to start.
  - Command: `systemctl is-active ssh ssh.socket`
  - Note: If the package is missing, only RDP ports (e.g., 3389/3390 via xrdp) may appear open in scans.
- **Firewall:** Allow port 22 if `ufw` is active (`sudo ufw allow 22/tcp`).
  - Do not enable `ufw` blindly as a side effect of installing SSH. Adding a rule to an inactive firewall is harmless; switching the firewall ON may block unexpected access.
- **Authentication:** Configure key-based auth in `~/.ssh/authorized_keys` (directory perms 700, file perms 600).
  - Linux uses `sudo` for elevation (see A-L2). There is no `administrators_authorized_keys` file; that concept applies only to Windows OpenSSH.

### A-L2. Account + elevation

- User account is **`owner`** on all Basement Desktops. This matches fleet convention.
- Elevation uses `sudo`. The non-interactive form used by the fleet is:
  ```bash
  printf '%s\n' "$PW" | ssh <host> "sudo -S -p '' <cmd>"
  ```
  `$PW` is retrieved from `Reference\Credentials\network-credentials.md`.
- **Design constraint:** Because stdin carries the password, scripts cannot also arrive on stdin. Scripts must be copied via `scp` before execution; piping both is not supported.

### A-L3. Workgroup / domain — not applicable on Linux

No SMB peer relationship is required for fleet membership; skip the Windows workgroup step. Add Samba only if the box needs to serve or mount shares.

### A-L4. Linux sleep prevention configuration

Linux uses two independent layers to govern suspend behavior. Setting one layer does not override the other. Configuration errors in either layer can cause unintended suspension.

#### Layer responsibilities

| Layer | Component | Governs |
|---|---|---|
| System-wide (headless/SSH) | `systemd-logind` | Idle actions for SSH and headless sessions |
| Seat-0 GUI | Desktop Environment (GNOME) | Suspend triggers for the local graphical session |

#### Design doctrine: Headless nodes must mask sleep targets

Every headless compute node in the lab must have sleep, suspend, hibernate, and hybrid-sleep targets masked. This prevents any pathway to suspension during unattended operation.

**Procedure:**
```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

**Applies to:** `Worktop` (headless server).

**Exceptions:** Interactive desktop hosts (e.g., `mordecai`) do not have these targets masked by default. Masking removes functionality required for interactive use. Changes to interactive host sleep policy require explicit operator authorization.

#### Verification procedure

To verify a node will not sleep, confirm all three conditions:
1. **logind**: `IdleAction` is set to `ignore` (check `/etc/systemd/logind.conf` or compiled defaults).
2. **GNOME**: `sleep-inactive-ac-type` is set to `nothing`.
3. **Journal**: Zero suspend events recorded in recent history.

All three signals must agree. A single passing signal is insufficient evidence that the node will not sleep.

#### Diagnostic notes

*   **`gsettings` over SSH**: Reading GNOME dconf via SSH reports the SSH session's configuration, not necessarily the seat-0 GUI configuration. It is indicative but not authoritative for headless policy. `systemd-logind` settings are authoritative for headless sessions.
*   **`systemd-inhibit --list`**: Lists active inhibitors preventing sleep *right now*. Inhibitors (mode `delay`) are transient runtime states, not persistent policy. Do not rely on inhibitors to enforce "never sleep" behavior. They indicate luck or temporary interference, not configuration.

#### GPU compute standards do not apply

Standard A4b (High performance + ASPM Off) does not apply to basement desktops. These hosts lack dedicated compute GPUs (`mordecai`: GT 730; `Worktop`: Intel HD). Applying A4b standards to non-compute hardware is prohibited.

### A-L5. Hostname: onboard under existing name

Hosts are onboarded using their current hostname unless a rename is mandated by the tier verdict (§C5). The final name is determined by the verdict; execution of the rename is handled in §E.

Linux hosts require `hostnamectl set-hostname` and an update to `/etc/hosts`. Changes propagate to every consumer listed in §E1.

### A-L6. Verify host connectivity and configuration

- [ ] `ssh <alias>` succeeds from Softserve. (Aliases are configured in `C:\Users\<user>\.ssh\config`.)
- [ ] Passwordless sudo returns exit code 0 (`sudo -n true`).
- [ ] Sleep table entries match intended state on both layers.
- [ ] Host key conflicts are resolved by removing stale `known_hosts` entries, not by disabling `StrictHostKeyChecking`. (Reinstalling the OS changes the host key; this applies to `Worktop` after Ubuntu install.)

### A-L7. Scheduled work on Linux — systemd unit design doctrine

The design doctrine for scheduled work on Linux mirrors §B5 (Windows): **starting a service is not installing it.** A guard with no restart policy has an expiry date and fails silently upon crash or reboot.

All long-running daemons and periodic jobs in the homelab must conform to the following systemd specifications. Non-conformance is treated as a defect.

#### Daemon/Guard Units
A daemon unit must define:
- `Restart=always`
- `RestartSec=` (set to an appropriate interval)
- `WantedBy=multi-user.target` (ensures start at boot)
- **No** `TimeoutStartSec` directive that could terminate a slow initialization sequence.

#### Periodic Jobs
A periodic job must use a **timer** unit with:
- `Persistent=true` (ensures missed runs fire immediately after downtime recovery)

#### Verification Protocol
Verification requires a live failure test. Reading the unit file configuration is insufficient and considered invalid verification.
1. Stop the unit.
2. Confirm it restarts automatically within the configured `RestartSec` window.
3. If using a timer, simulate a missed run to confirm `Persistent=true` behavior.
