# Project #15 — On-demand game servers from welcome.example.org: RECON (2026-07-10, read-only)

This component implements on-demand game server instantiation triggered by friend-facing buttons. It extends the onboarding and capabilities system defined in [[Friend-Onboarding-Design]] §Onboarding/Caps System.

## Operational Scope

No changes are made to the core launch scripts except for the specific fixes required for Palworld 1.0 compatibility. All other launch behavior remains static.

## What exists today (verified on-host)

**Palworld is the only game server.** Softserve (Windows, <softserve-address>), bare exe — NOT Docker:
- Install: `C:\steamcmd\steamapps\common\PalServer` (steamcmd at `C:\steamcmd`, app id **2394010**,
  `login anonymous`).
- Launcher: `Start Palworld Server.bat`. Full launch line: `-ServerName="Diddy's World" -port=8211
  -players=32 -log -useperfthreads -NoAsyncLoadingThread -UseMultithreadForDS
  -NumberOfWorkerThreadsServer=8 EpicApp=PalServer` (crossplay on).
- Server settings: `Pal\Saved\Config\WindowsServer\PalWorldSettings.ini`.
- Version: **1.0** via steamcmd (5.8 GB). Old world save from Feb 27 still at
  `Pal\\Saved\\SaveGames\\0\\64F5EA4C…`. See Palworld Upgrade for regen instructions.

## Game server management architecture

### GameOps agent specification
The game management interface uses a lightweight, token-authenticated HTTP service running on Softserve. The implementation follows the hostops pattern, deployed as either a Windows service or scheduled task. Resource constraints adhere to Plex-host doctrine: BelowNormal priority.

Interface rules:
* **Network**: LAN/Tailnet-only listener.
* **Authentication**: Token stored in `hostops_token` file.
* **Permissions**: Narrow verbs only; whitelisted per game. No arbitrary command execution.
* **Supported commands**:
  * `status`: Report current state.
  * `start`: Execute the game's batch script (which handles self-update and launch).
  * `stop`: Initiate graceful termination.

### Palworld REST API integration
PalServer provides an official REST API for state management. Configuration requires `RESTAPIEnabled=True` and an `AdminPassword` in `PalWorldSettings.ini`. The API listens on port 8212. (Note: Verify availability in version 1.0; feature added ~v0.23).

Endpoints used:
* `/v1/api/save`: Force save.
* `/v1/api/shutdown`: Graceful stop.
* `/v1/api/players|metrics`: Status and resource usage.

Operational constraints:
* Processes are never killed forcefully; clean save+stop is mandatory.
* Auto-shutdown triggers after N minutes at 0 players (satisfies backlog resource-limit requirement).

### Onboarding app (arrnet) integration
The onboarding application interfaces with the game management system via direct LAN connection to Softserve. The previous bridge-egress routing quirk is resolved.

Security and access control:
* **Capability model**: Per-game capability (`pal`) or umbrella `games` capability. Granular caps are preferred (matches existing philosophy).
* **User interface**: Welcome card displays status and provides a Start button.
* **Request handling**: POST requests undergo session cap-check, CSRF validation, and rate-limiting (one start per N minutes) before forwarding to GameOps.
* **Audit logging**: All actions are recorded in person-centric records.

## Game traffic path for UDP 8211

Palworld operates on **UDP 8211** and cannot traverse the droplet HTTPS front door (Caddy forward_auth supports TCP/HTTP only).
- **Option A (recommended v1):** Tailnet peers connect directly to Softserve's tailscale IP `<tailscale-address>:8211`. These peers are ACL'd to Seerr + Softserve (see CLAUDE.md for port scope details). This approach maintains zero new public exposure and aligns with the "selected users" gating requirement. **Constraint:** Players must reside on the tailnet.
- **Option B (future extension):** If non-tailnet peers require access, UDP relay is configured via iptables DNAT / nginx stream on the droplet to forward UDP 8211 → Softserve over the tailscale network. This hides home IPs but opens a public port on the droplet; see `reference-homelab-design-constraints` for compliance.
- **Prohibited:** Port-forwarding at home contradicts standing design constraints and is not an option.

## Resource constraints for Palworld server

Softserve hosts Palworld game servers. The system runs Plex, Hyper-V VMs, and Ollama-WSL alongside the game server stack.

**Capacity limit:** Only one game server may run at a time. Running two servers exceeds available resources.

**Memory behavior:** Palworld servers consume 8–16 GB RAM with uptime. To fit within Softserve's resource envelope, idle auto-shutdown is mandatory.

**Deployment architecture:** Game servers reside on Softserve. Alternative deployment via WoL to Mothership/BigHonker (gaming PCs with higher headroom) is deferred. See `wol-sidecar` precedent for agent architecture.

## Security notes

### Tailnet Direct Routing Requirements
If a tailnet friend ACL exposes all Softserve ports, traffic may bypass the cap gate and hit the gameops agent directly. In this scenario, token authentication on the agent is mandatory.

Ideally, restrict the friend ACL to allow access only for Plex (port 32400) and specific game ports.

## Open items before build

### 1. ACL scope verification on Softserve
Verify friend Access Control List port scope on Softserve using Tailscale admin or API (token in memory `reference-noip`).

### 2. REST API conformance check after launch
Once the server is running, verify Palworld 1.0 REST API availability. Ensure ports and paths conform to the current spec; deviations indicate changes from documented baseline.

### 3. Design constraints requiring decision
*   Define per-game capacity limits versus umbrella `games` cap.
*   Set idle-shutdown timeout policy.
*   Assign priority ownership for resource acquisition (who gets it first).

### 4. Fresh-world launch configuration note
The fresh-world question regarding the 1.0 launch involves renaming old save folders to prevent conflicts with new world data.
