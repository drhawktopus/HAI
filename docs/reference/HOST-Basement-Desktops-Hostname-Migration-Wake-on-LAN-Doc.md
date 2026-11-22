# Basement Desktops — Hostname Migration & Wake-on-LAN Doctrine (§E, WoL)



## §E — Hostname rename as planned migration

Hostnames are keys consumed by DNS, SSH, the scheduler, per-host state files, task names, alert prefixes, and documentation. They are not labels.

Onboarding hosts use temporary hostnames `Worktop` or `StreamBox`. Renaming occurs only after the owner sets the tier.

Rename procedure:
1. Enumerate all consumers of the hostname (code, data, docs, scheduled tasks).
2. Update all enumerated consumers.
3. Grep for residual references the following day.

Conformance instrument: Verify zero residual references post-migration.

### Host enumeration checklist
Operate on this sequence before modifying host identity. Changes to hostname require a reboot and alter SMB/NTLM names and RDP certificate CNs.

1. **Windows computer name**
2. **DNS resolution**
   - Update `lan.list` on Sundae.
   - Run `pihole reloaddns`.
   - Trigger MinuteMan sync.
   - Verify each name resolves to EXACTLY ONE address by count. Do not eyeball. A stale record alongside a new one causes silent misrouting of work.
3. **SSH configuration**
   - Update alias, HostName, User, and key in `~/.ssh/config` on Softserve.
4. **Inventory scripts**
   - Update `HOSTS` in `Lab\bin\probe.py`.
   - Update `KNOWN` in `Lab\bin\lansweep.py`.
5. **Scheduler configuration**
   - Update host rows, weight tables, and any liveness helpers keyed on the name in `scheduler.py`.
6. **Factory fleet state**
   - If the host is a factory, update `FOUNDRY_FLEET_HOST`. A wrong value causes the scheduler to place work on a resource it believes is free.
7. **Per-host state files**
   - Update `_gpu_status_<host>.json`, `rescue-state-<host>.json`, and `p20_status` in STATUS_DIR. These are keyed by hostname by design (single-writer fix).
8. **Scheduled tasks**
   - Update task names (the `SS Foundry Runner` idiom) and launcher paths inside them.
9. **Alerting and UI**
   - Update Discord `_host_tag()` alert prefixes.
   - Update homepage tile URL `GET /p20/factory/{host}`.
10. **Documentation**
    - Update [[Network-Infrastructure]], CURRENT-PROJECTS, this file, the RUNBOOK, and any relevant journal entries.
11. **DHCP reservation**
    - Verify DHCP reservation is keyed on MAC address (survives name change).
    - Note: Router display name update is handled by the owner.

### E2. Execute
1. Rename, reboot, and verify conformance to §A6 (SSH access, elevation status, power settings per scheme).
2. Complete tasks in §E1. Batch all LAN-side changes into a single router Apply.
3. Retire artifacts bearing the old name in the same pass. This includes orphaned state files (`*_<oldname>.json`), task names, and DNS rows. Enforce the "One name = one artifact" rule: move stale or adjacent same-named files to prevent them from acting as decoys for subsequent sessions.

### E3. Verification of final state
- [ ] `probe.py <newname>` returns a response.
- [ ] `<newname>.lan` resolves to exactly one address.
- [ ] The scheduler offers the host.
- [ ] If the host was factory reset, its card publishes under the new identity and no service writes under the old name.
- [ ] Search the indexed documentation (`Reference\`, `LLM-Project\`, `Runbook\`) for the old hostname to ensure removal. A secondary search is mandatory the following day to catch residual references.

### Linux Host Renaming Procedure and Name History Enumeration

#### Scope
Applies to Linux hosts (`mordecai`, `rigby`). Windows-specific steps (SMB/NTLM name changes, RDP certificate CN updates, reboot requirements) do not apply. On Linux, hostname changes via `hostnamectl set-hostname` and `/etc/hosts` `127.0.1.1` entry updates take effect immediately without a reboot.

#### Verification
Verification requires re-reading state from the host. Execute `hostname`, `hostnamectl --static`, and inspect `/etc/hosts`. All three sources must agree with the new hostname. A zero exit code is insufficient for verification.

#### Consumer Enumeration Checklist
Before modifying any system, enumerate all consumers of the old hostname(s). The following consumers were repointed during the `StreamBox` → `mordecai` and `pinky` → `rigby` migration:

| Consumer | Status / Action |
|---|---|
| Hostname on the box | Changed; verified via re-read back. |
| `~/.ssh/config` on Softserve | New `Host` blocks added; old aliases retained for continuity. |
| SSH key files | Files renamed (`streambox_ed25519` → `mordecai_ed25519`; `worktop_ed25519` → `rigby_ed25519`). Old files moved to `.ssh/retired/`; not deleted. |
| `Lab/bin/lansweep.py` (`KNOWN`) | Rows updated for both hosts. |
| Credential locker | Headings, Titles, URLs, key names, and summary table updated. |
| Bench harness (`twobox.py`, `bench_pair.py`) | Updated; see *Alias Handling in Rate Limiting*. |
| DNS / `lan.list` | N/A — No records existed prior to migration. |
| DHCP reservations | N/A — None exist; addresses remain dynamic. |
| Scheduler / Fleet State | N/A — Neither host was wired into `scheduler.py`, `FOUNDRY_FLEET_HOST`, per-host state files, scheduled tasks, Discord `_host_tag()`, or homepage tiles at time of migration. |

#### Pitfall: Full Name History Enumeration
A host that has been renamed once may be recorded under every name it has ever carried. Searching only for the immediate predecessor name (e.g., `pinky`) misses entries filed under older names (e.g., `Worktop`).

*   **Design Fact:** A rename pass must search every name the host has ever carried, not just the outgoing one.
*   **Example:** `lansweep.py` contained `Worktop` pinned at `.57`, an address the box left when reinstalled. The grep for `streambox|pinky` missed this entry because it was filed under the pre-`pinky` name.

#### Pitfall: Quoted Spans and Historical Fixtures
A blind text substitution (e.g., `sed`) cannot distinguish between references to a machine and historical quotes or command fixtures. Rewriting names inside quoted spans creates fabrications.

*   **Design Fact:** A rename pass must skip QUOTED SPANS — human quotes, command strings, log lines, error text — because there the old name is the CONTENT, not a reference.
*   **Prose vs. Quote:** Prose says *which machine*; a quote says *what was said or run*.
*   **Human Quotes:** Text such as "You can keep running tests on pinky and stream box" in CURRENT-PROJECTS must retain original names to preserve historical accuracy. Restoring changed quotes is required if fabricated.
*   **Command Fixtures:** Byte-verbatim command strings, such as `F._identity_of(r'python.exe sudo_run.py pinky openmpi_install.sh')` in the RUNBOOK GPU-police signature test, must not be renamed. Renaming pins a string that was never run, violating the doctrine of byte-verbatim reproduction.
*   **Snapshots:** Files in `State/claim-review/snapshots/` are point-in-time copies of library docs used as review baselines. Names inside these files record what the doc *said* at that time, not what the machine is called now. Leave them stale on purpose; do not rewrite names.

#### Pitfall: Alias Handling in Rate Limiting
Code keyed on the hostname the box *reports* (e.g., via `socket.gethostname()`) breaks if aliases are added to iteration containers.

*   **Defect Pattern:** Adding old names as extra keys to a container that is iterated (e.g., `RATE = {"StreamBox": ..., "pinky": ...}` in `bench_pair.py`) causes silent unbalancing. If the dealing loop iterates over `RATE`, work is dealt four ways instead of two, corrupting joint measurements.
*   **Correct Fix:** Implement an `ALIAS` map that normalizes to the canonical name *before* lookup. Assert that the length of the rate dictionary matches the expected number of active hosts (e.g., `assert len(RATE) == 2`).
*   **Design Fact:** Alias-by-adding-keys is a bug wherever the container is also iterated.

#### Outstanding Items
DNS (`mordecai.lan` / `rigby.lan`) and DHCP reservations are not configured. Addresses are dynamic. This requires explicit approval for Pi-hole and router configuration changes until then, address-by-IP is the only reliable access path.

## mordecai — Wake-on-LAN and sleep (operational doctrine)

**WoL is configured and proven** (suspend → magic packet → resume, uptime intact; no BIOS
change was needed — firmware already permits it; if a CMOS reset ever breaks it: "Power On
By PCI-E"/"Wake on LAN" ENABLED, ErP / Deep S5 DISABLED).

- **Mechanism:** `/etc/systemd/system/wol-enp3s0.service` — a `oneshot` running
  `/usr/sbin/ethtool -s enp3s0 wol g`, with
  `WantedBy=multi-user.target suspend.target hibernate.target hybrid-sleep.target`, so it
  applies at boot and again immediately before every sleep. Check with
  `systemctl is-enabled wol-enp3s0` and `journalctl -u wol-enp3s0 -n 5`.
- **`ethtool` showing `Wake-on: d` on an AWAKE box is expected, not a fault** — this driver
  resets the flag across every resume; the unit re-applies it on the way into the next
  sleep. Grade WoL by the unit's journal or by an actual suspend-and-wake — the NIC is the
  authority, never a config read-back.
- **Netplan cannot configure WoL on this driver** — both `wakeonlan: true` and the NM
  passthrough form (`ethernet.wake-on-lan: "64"`) read as configured and do nothing; no
  netplan WoL stanza exists for this reason. Original netplan dir backup:
  `/root/netplan-backup-20260819`.
- **To wake it:** the sundae sidecar — `curl 'http://<sundae-address>:9119/wake?mac=<mac-address>'`
  (broadcasts to `<lan-broadcast>` and `255.255.255.255`). Do not build a second waker.
- **To sleep it:** `sudo systemctl suspend` (via `Lab\bin\run_remote.py` — sudo needs a
  password and a bare ssh sudo hangs on an invisible prompt). WoL works from suspend only,
  not from powered-off.
- **Idle auto-suspend is configured but does not fire**: logind + GNOME idle settings are
  live, but the gdm greeter session on tty1 never reports idle and GNOME power daemons hold
  sleep inhibitors, so the box never meets the all-sessions-idle condition. Making it sleep
  on its own requires removing the greeter (`systemctl set-default multi-user.target`) —
  the owner's call. Grade sleep behaviour only by a real decoy, never by `busctl` readings.

Credential data for the box: `Reference\Credentials\network-credentials.md` (this section
is the doctrine home; the credential file's Notes point here).
