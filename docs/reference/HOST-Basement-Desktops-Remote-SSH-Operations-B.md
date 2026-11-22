# Basement Desktops — Remote SSH Operations (§B)



## §B — Remote SSH Operations (Scriptable; No Local Console)

### Tool Selection: Bash vs. PowerShell

Remote SSH operations must use the `Bash` tool, not `PowerShell`. The Bash tool supports `ssh *` via approved wildcard expansion. The PowerShell tool requires 423 exact literals and does not support wildcards for SSH commands. Using PowerShell for SSH risks command rejection due to literal matching failures. See [[RUNBOOK-Elevation-and-Headless-Tasks]].

### Authentication and Context Limitations

SSH is a network logon. It does not inherit local session state:
- It cannot access mapped network drives.
- It cannot access Windows Credential Manager stores.

Use SSH commands only for privilege escalation and remote execution. Do not use SSH to test share access or validate local credential contexts. See [[HOST-ONBOARDING-Basement-Desktops]] §B4.

### B1. Reachability instrument

Each host must be registered in `Lab\bin\probe.py` within the `HOSTS` list and its associated class, and in `Lab\bin\lansweep.py` within the `KNOWN` dictionary. Registration enables automated reachability verification and prevents re-derivation of host identity during subsequent sessions.

Verify conformance by confirming all basement desktops appear in both registries with current name/role assignments.

### B2. SSH alias configuration
Add an entry to the Softserve host `~/.ssh/config`
(`C:\Users\<user>\.ssh\config`). The entry must match the existing block defined in
[[Network-Infrastructure]] §SSH Access.

### B3. DNS Registration and Validation

**Requirement:** Every host must have a static entry in Pi-hole’s `lan.list` before deployment. The Fellowship scheduler resolves hosts by `<host>.lan`, not by IP. A missing or incorrect DNS record causes immediate connectivity failure for that node.

**Procedure:**
1. Add the line `<host>.lan <IP>` to `/opt/arrstack/pihole/etc-pihole/hosts/lan.list` on the Sundae server.
2. Execute `docker exec pihole pihole reloaddns` on Sundae.
3. Wait for MinuteMan sync (max 15 minutes) or verify resolution manually.

**Constraints and Safety:**
- **File Hygiene:** Pi-hole loads every file in `/opt/arrstack/pihole/etc-pihole/hosts/`. Never leave `.bak` files in this directory; they serve as live DNS records. Store backups in `hosts-archive/`.
- **Resolution Uniqueness:** Verify that the hostname resolves to exactly one IP address. Use the validation command documented in [[Network-Infrastructure]]. Multiple addresses for a single name do not error but cause silent routing failures.

### B3b. `accept-routes` stays OFF on a LAN resident

Every LAN-resident host must have `accept-routes` disabled in Tailscale. A LAN machine must not accept the tailnet's subnet route for its own subnet; doing so detours all its LAN traffic through Softserve, causing masked sources and intermittent timeouts.

Conformance:
1. Run `tailscale set --accept-routes=false`.
2. Verify `Get-NetRoute -DestinationPrefix <lan-subnet>/24` shows ONLY the on-link Ethernet route.

see [[HOST-ONBOARDING-Basement-Desktops]] §"A LAN HOST MUST NOT ACCEPT THE TAILNET'S SUBNET ROUTE"

### B4. Share access (conditional on library/corpus need)

**Requirement:** Map Softserve shares by IP address (`\\<IP>\...`), not by hostname.
- **Reasoning:** Hostname resolution fails from Mothership, producing System Error 53. This indicates a DNS/name-resolution failure, not an SMB service block (see [[RUNBOOK]] §"SMB from Mothership"). A stored credential is required for the connection.

**Verification protocol:** Test share access in the user’s interactive session only. Do not test over SSH.
- **Risk:** SSH probes return `isdir=False` and `Access is denied` even when the console session has full write permission. These results are artifacts of the session context, not true access state.
- **Procedure:** Use `schtasks /ru <user> /it` to execute the test in the target user’s interactive context.

**Drive letter doctrine:** Drive letters are local to the host; do not assume consistency across the fleet.
- **Fact:** Softserve mounts documentation on `Z:`. Mothership mounts documentation on `Y:`.
- **Rule:** Verify mount points and drive letters explicitly on every new machine before proceeding.

### B5. Scheduled-task conventions
- Always pass a past `/sd` (e.g., `01/01/2020`) with `/sc once`, or the "create then run" idiom leaves a live one-shot timer.
- Use `/rl HIGHEST` for any task requiring elevation.
- Every daemon or guard task must include `AtStartup` plus a repetition interval and `RestartCount`. No execution time limit. A guard with no restart policy expires silently.
- Do not use `[TimeSpan]::MaxValue` for indefinite duration; it causes the scheduler to reject the XML and fail the write, leaving broken settings in place. Use an empty Duration for indefinite runs.
- Verify task state by observing live behavior (kill and watch restart), not by reading settings back.

### B6. Factory deployment procedures

- **Implementation uniqueness:** Deploy only `p20_foundry.py`. Do not create secondary implementations. The `FOUNDRY_ROOT` environment variable enables blue/green deployment without code divergence; the same codebase runs across factories. See [[P20-MULTI-FACTORY-FLEET]] §1.

- **Environment configuration:** Set the following environment variables to enable single-file multi-host service:
  - `FOUNDRY_ROOT`
  - `FOUNDRY_FLEET_HOST`
  - `FOUNDRY_PY`
  - `FOUNDRY_LORAS`
  - `FOUNDRY_COMFY`
  - `FOUNDRY_COMFY_LAUNCH`

  **Design constraint:** `FOUNDRY_FLEET_HOST` must be correct. An incorrect value causes the host to claim another box’s GPU in fleet state, leading the scheduler to assign work to a GPU perceived as free.

- **Deployment verification:**
  1. Use `fleet_deploy.py`. Do not copy files manually.
  2. Run `verify_deploy.py`.
  3. Confirm the runner process timestamp postdates the deployed file timestamp. Deployed code is not running if this condition is not met.

- **Capability declaration:**
  - A capability is registered only when its underlying code is deployed. The registry constitutes a binding promise.
  - Orders requiring an absent capability are held, with the missing capability explicitly named. They are not failed or archived.

- **Single-writer store audit:**
  - Before the new factory writes any data, audit all stores that were previously single-writer. Adding a second producer introduces race conditions and data corruption risks in these stores.
  - Confirm the new host uses per-host files: `_gpu_status_<host>.json`, `rescue-state-<host>.json`, and the `p20_status` STATUS_DIR.
  - Verify the new host cannot fall back to legacy shared paths. See [[P20-MULTI-FACTORY-FLEET]] §4.

- **API Contract adherence:**
  - The `/fleet/rescue-state` endpoint enforces the `RescueStateReq` contract.
  - Posting non-conformant shapes results in Pydantic rejection; the daemon reads the payload as unknown. Use only the existing contract structure.

### B7. Scheduler integration — additive to `scheduler.py`, never a parallel mechanism

Host onboarding integrates with the scheduler as an additive change to `scheduler.py`. A parallel scheduling mechanism is prohibited.

Follow the integration shape proven for the Fellowship ([[Fellowship-Crunch-Integration]] §4 + §7).

#### Weighted slicing and capacity
*   **Proportional splits via per-host weight:** Host rows must include a **per-host weight table** derived from measured numbers (the `CAPTION_S_PER_IMG` idiom, see §C). Splits are proportional to these weights. Even splits cause fast boxes to idle while waiting for slow ones.
*   **Rank-based slicing:** Use rank-based slicing, not band-hashing. Hash-band splits can assign zero images to the fastest box on remainder re-splits without error. Rank items and slice the ranked list by weight; remainders rebalance rather than inheriting empty bands.

#### Liveness and placement policy
*   **Fail-closed liveness:** "Cannot tell" is not "free." A stale BUSY claim remains BUSY until updated. Unreachable hosts are simply not offered work.
*   **Centralized placement via `scheduler.place()`:`** All placement must go through **`scheduler.place()`**, which implements eleven documented policies into one decision. Do not hand-pick a host.
*   **Pre-execution verification:** Ask "where's the order?" before any work runs to ensure compliance with placement policy.

#### Deployment and validation
*   **Test pinning:** Pin tests in `tests/test_scheduler.py` before deployment.
*   **Deployment procedure:** Deploy per [[RUNBOOK-Orchestrator-Deploy-Procedure]]:
    1.  Backup.
    2.  SCP to host.
    3.  Verify SHA256 on the host **and inside the container**.
    4.  Run `docker compose build` and `up -d`.
    *   **Note:** Restart alone runs the old code; a rebuild is required.
*   **Container consumer enumeration:** `up -d` may recreate only one consumer of an image. Verify every container consuming the image, as other containers may continue running previous images even if their hash checks pass.

### B8. Health daemon duties and liveness definition

The health daemon retains its interface shape: it provides a heartbeat at the path PREFLIGHT checks, generates a status card, maintains a log, supports the STOP flag, produces a fleet report, and reads the STATED run. Its operational duties are defined by [[P20-MULTI-FACTORY-FLEET]] §7, restricted to failure modes specific to this host class.

Liveness is defined strictly as the age of the heartbeat file. Process existence checks, push-age metrics, and task-status indicators are unreliable and are not used for liveness determination. A valid heartbeat indicates daemon presence but does not guarantee worker thread activity if the heartbeat generation is decoupled from work execution.


<-- CUTS -->
{
  "cuts": [
    {
      "destination": "journal",
      "reason": "Narrative history of specific failure dates (07-26, 08-08) and discovery reasoning ('Process-exists lied', 'push-age lied'). This belongs in the session journal explaining why these metrics were discarded."
    },
    {
      "destination": "component-manual",
      "reason": "The reference to [[P20-MULTI-FACTORY-FLEET]] §7 defines the specific duties. While cited, the actual list of duties is external. If this section intended to define the duties locally, they are missing. As written, it only points elsewhere. This pointer is retained as a design fact."
    }
  ]
}

<-- NOTES -->
The original text contained instructional language ('Keep the shape... change the duties') and narrative history of metric failures. These were converted to declarative design facts: 'Liveness is defined strictly...' and 'Process existence checks... are not used'. The specific dates of past failures (07-26, 08-08) are removed as they are historical data points, not current policy; the reasoning for discarding those metrics remains in the journal. The heading was refined to include 'liveness definition' for better searchability regarding health checks.
