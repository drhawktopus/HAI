# Project #15 — Game-server buttons (BUILT 2026-07-14)

One-click start and stop of game servers from the friend-facing web interface. Access is cap-gated.

See [[Project15-GameServer-Launch-Recon]] for superseded launch plans.

## Architecture — OUTBOUND agent (no inbound on the Plex host)
```
Riley phone → welcome.example.org/games   (public vhost, already fronted; in-app cap gate)
   → onboarding app (Sundae, arrnet :8466)       enqueues a start/stop command
   → gameops agent (Softserve) POLLS the app      GET/POST /gameops/sync every 4s (token-authed)
   → agent runs the game's .bat / REST shutdown   status reported back each cycle
```
The system operates as an outbound-only architecture. The `gameops` agent residing in Softserve periodically polls the onboarding application via `GET/POST /gameops/sync` every 4 seconds using token authentication. This pattern is enforced because inbound connections to Softserve are blocked by default: Windows automatically creates inbound BLOCK rules for a listening instance of `pythonw.exe`, and exposing an inbound firewall rule for a process launcher constitutes the specific exposure this design avoids.

The polling mechanism enables the agent to execute game start/stop commands (`.bat` / REST shutdown) initiated by the onboarding app. The agent reports execution status back each cycle.

This outbound-only configuration is preferred over the original listener design, which required exposing inbound ports and altering firewall rules.

## Pieces
The document contains no content under this heading.

### Softserve — the agent (`C:\gameops\`) — repo `github.com/drhawktopus/gameops-agent`
The agent runs in a version-controlled private repository at **`ghub.com/drhawktopus/gameops-agent`** (Private), authenticated via GitHub CLI for HTTPS remote access. SSH host-key verification is disabled to accommodate the configured connection parameters.

**Secret Management and Verification:**
The following files are strictly gitignored: `gameops_token`, `games.json`. Structure documentation resides in **`games.example.json`**. A pre-push secret scan (using `git diff --cached | grep <token>|<password>`) verifies absence of secrets before commit.

**Component Definition and Behavior:**
The agent is a stdlib-only polling worker implementing the following logic:
*   **Polling Interval**: 4 seconds.
*   **Snapshot Generation**: Every cycle builds a status snapshot including process state, Palworld REST player metrics, host RAM usage, and CPU load.
*   **Data Exchange**: Posts snapshots to `/gameops/sync` using the shared token; executes returned commands.
*   **Command Verb Constraints**:
The agent supports only FIXED verbs: `start` (executes game batch file) and `stop` (graceful shutdown via REST API). Arbitrary commands or force-kill operations are prohibited.

**Configuration Schema:**
The configuration resides in **`games.json`**, where each entry defines a per-game config including bat path, workdir, process name (`proc_name`), Palworld REST URL/user/password credentials (ACL-locked), and start cooldown. Adding a new game requires a corresponding entry here.

**Authentication Tokens:**
The file `gameops_token` contains the 48-char shared secret required for data exchange with the application's `GAMEOPS_TOKEN`. This credential is ACL-locked.

### Scheduled Task "GameOps Agent"
The Windows scheduled task configuration enforces the following operational parameters:
*   **User Context**: Runs as `SOFTSERVE\administrator`.
*   **Logon Type**: Interactive (ensures launched servers and manual launches operate within the same console session used by Plex, eliminating stored password requirements).
*   **Priority/Level**: RunLevel Highest.
*   **Triggering Logic**: Triggered At Logon; immediately started upon job creation.
*   **Resilience Settings**:
    *   RestartCount: 5
    *   RestartInterval: 1-minute
    *   ExecTimeLimit: Unlimited
    *   MultipleInstances Strategy: IgnoreNew
*   **Failure Handling**: The task is designed to survive crashes and re-logon events. 
*   **Bootstrapping Constraint**:
The agent only initiates execution once the administrator user's session is active (required for an always-on box with a persistent console session). If autologin functionality were ever disabled, resulting in no initial session post-reboot, the task would wait until logon occurs.

## Access model
- **`gameserver-admin`**: start/stop + live stats + settings editor (rewrites config and bounces server).
- **`gameserver-player`**: start/stop + live stats. No access to the settings editor.
The player cap supports starting and stopping the server. Admins can change settings.

### Cap enforcement: `VALID_CAPS` contract

The `VALID_CAPS` tuple in `invites.py` is the authoritative source for renderable access controls. The admin form renders checkboxes from this tuple and filters saves via `new_caps = [c for c in invites.VALID_CAPS if c in want]`. 

Any capability enforced by `app.py` but absent from `VALID_CAPS` is invisible in the UI and silently removed on every save operation. All game-server capabilities must be present in `VALID_CAPS` to prevent accidental revocation.

Cap assignment requires:
1. A verified provider (Plex or Google). Typed email cannot self-grant access.
2. The agent's token as a secondary lock. Tailnet friendship alone does not grant access.

## Ops / how to
- **Add a game:** add an entry containing the fields \`bat\`, \`proc_name\`, and REST creds to `C:\gameops\\games.json`; append the corresponding value as an element of the array referenced by environment variable `GAME_LIST`; invoke `docker compose up -d onboarding-app`.
- **Rotate the token:** write a new value into Windows credential store `C:\gameops\gameops_token`; update the local reference in `.env` variable `GAMEOPS_TOKEN`; restart the "GameOps Agent" task and run `docker compose up -d onboarding-app`.
- **Restart the agent after editing it:** execute \`Stop-ScheduledTask 'GameOps Agent'\`, then \`Start-ScheduledTask 'GameOps Agent'\` (the running process holds the old code in memory).
- **Read gameops state:** read `/data/gameops.json` via `docker exec onboarding-app cat /data/gameops.json`; alternatively, inspect the Softserve process directly. Manual status peeks to listeners no longer apply.

## Palworld admin/REST (PalWorldSettings.ini)",- port: 8212,- user agent endpoint:\`admin\`,,- path:gatorwrangler\",
text_source":"Reference/Project15-GameServer-Buttons.md","line_start":89,"line_end":99

## Python listener behavior on Windows
The `pythonw.exe` executable auto-blocks inbound rules when it listens for connections. This overrides existing port Allow rules with a Block rule.
The agent must use outbound polling, or pre-stage an allow rule before first listen.
## File encoding defaults on Windows
The default file handle uses cp1252, not UTF-8.
The configuration loader (`_load_games()`) failed when `games.json` contained non-ASCII characters. Every config that can hold non-ASCII MUST specify `encoding="utf-8"`. The agent must use this parameter for every file read.

## Resource constraints

Softserve supports exactly one game server instance. The host provides approximately 12 GB of free RAM with a baseline CPU usage of ~60%. Palworld idles at approximately 1.2 GB RAM and consumes additional resources during uptime.

The status page displays host RAM and CPU metrics to indicate available headroom before launching a second instance.

### Auto-shutdown capability

The monitoring agent tracks `player_count`. Logic to auto-stop the server when `player_count` reaches 0 is pending implementation in the next buildout phase.

## Second game: Satisfactory
- **Branch configuration.** The install uses Steam for updates and `steamcmd` with no `-beta` flag. Files reside in `C:\steamcmd\steamapps\common\SatisfactoryDedicatedServer`. A separate experimental client copy at `C:\Program Files (x86)\Steam\...` remains untouched.
- **Save path.** Dedicated saves are located in `%LOCALAPPDATA%\FactoryGame\Saved\SaveGames\server\`. The path `%LOCALAPPDATA%\FactoryGame\Saved\SaveGames\Server\` is the intended location, distinct from `FactoryServer` which is not valid. Worlds include "boys in the booty" (active), "Epstien's Vision", and "Manual1".
- **Startup.** Use `Start Satisfactory Server.bat`. The process name is `FactoryServer-Win64-Shipping-Cmd`. Bind to port 7777 using `-Port=7777`.
- **Type declaration.** Entries in `games.json` for this game carry the type value `process`:
    - Host status: Process existence determines online state.
    - Shutdown behavior: Commanding stop performs a graceful terminate. This preserves data but may discard progress accumulated since the last autosave interval.

## Request-a-game flow
An authorized player or admin requests a game via the `/games` interface. The system sends an approval request to the owner (Discord ID `<discord-id>`) using HawkAdminBot with **✅ Approve / 🚫 Reject** options.

Upon approval, if a recipe exists for the requested title in `/gameops/recipes.json`, an automated installation occurs. Requests lacking recipes are flagged as `provisioning` tasks for agent configuration (e.g., Satisfactory).
The final game entry appears on the dashboard; launch execution is optional and may be tested separately.

### Recipes (`C:\gameops\recipes.json`)
The system maintains a whitelist of supported AppIDs. A friend's free-text request never carries an internal appid.
- **Seeded recipes**:
  - `valheim`: AppID `896660`, install status tested.

### Pending Recipes (Ready for Seeding)
The following titles are identified as candidates; each requires launch/port testing before inclusion in the whitelist.
- V Rising: AppID `1829350`
- Enshrouded: AppID `2278520`
- 7 Days to Die: AppID `294420`
- Project Zomboid: AppID `380870` 
The installation agent runs as a background thread (`steamcmd`). It executes `app_update <appid>`, generates an auto-updating start batch file, and appends the entry to `games.json`. The outcome links back to the original request state (e.g., `installed` or `install_failed`).

## Request statuses (rendered on `/games`

The following request statuses are defined for items rendered on `/games`:

| Status | Icon | Definition |
|---|:---:|:--|
| `pending` | ⏳ | Awaiting the owner's review. |
| `installing` | 🛠️ | An auto-install recipe is executing. |
| `provisioning` | 🔧 | The request has been approved via a generic (recipe-less) game and awaits a Claude session for specific configuration.
|
*Note: Status icons render as described above; their meaning defines the current operational state of the request.*

## Provisioning a recipe-less request

A "recipe-less" request is defined as any provisioning operation flagged with the parameter `$provisioning`, indicating it was generated by Claude. Such requests bypass standard installation templates and require manual validation of hostability before proceeding.

## Hostability Constraints

Before attempting deployment, verify that every game possesses a dedicated SERVER appid distinct from its client application ID (e.g., Valheim: Game `892970` / Server `896660`). Consult SteamDB for the server application ID and check the Steam store's multiplayer categories.

The pipeline rejects installation if any of the following conditions exist:
*   **Host-based co-op models**: Includes P2P, host-offline workarounds (e.g., SaveSync), Photon/EOS/Steam-relay matchmaking via "join via friend / session code", or games offering only an "Online Co-op" category without a dedicated server.

If these conditions apply to the request:
1.  Set the request status field `not_hostable`.
2.  Record a descriptive note detailing why hostability is impossible.
3.  Report this outcome immediately; do not proceed with installation attempts or forcing an install.

## Deployment Procedure for Hostable Games

If the game satisfies all hostability requirements, execute the following procedure:

1.  **Update**: Run `steamcmd +app_update <server appid>` to fetch and update assets.
2.  **Automation**: Implement a self-updating start bat script if applicable.
3.  **Configuration**: Create an entry in `games.json` with either type `$palworld_rest` or `$process` (depending on the architecture).
4.  **Validation**: Perform functional tests of the startup and shutdown sequences to ensure reliability.
5.  **Recipe Generation**: Add a corresponding entry to `recipes.json` containing the application ID and launch template parameters.
6.  **Completion**: Update the request status field `installed` once validation passes.

## Dashboard auto-discovers installed games via `known_games`

The `/games` dashboard and associated start/stop/settings routes derive their game list from `gameops.known_games(GAME_LIST)`. This function returns every game for which the agent reports status, prioritizing entries defined in the `GAME_LIST` environment variable and appending any additional discovered games. New installations appear automatically without modifying `GAME_LIST`.

## Friendly world-settings editor
A form interface (sliders/toggles/dropdowns) located at `/games/<game>/settings`. Access is admin-only and invoked via the ⚙ icon on each card.
- **Palworld:** Manages 12 curated settings from `PalWorldSettings.ini` including XP/catch/spawn rates, day/night speed, gather/enemy-drop/work-speed rates, damage-taken, death penalty (dropdown), PvP (toggle), and server password (text).
The schema for these settings resides in the agent's `SETTINGS_SCHEMA`. This schema enables instant pre-filling of the form against a status snapshot.
- **Apply path:** Executed via the agent `apply_settings` verb within a background thread. The process reads current values; upon save, if the server is running it executes: stop → wait for full exit → write INI file → start. This sequence respects that `PalServer rewrites the ini on exit`. If off, only the INI is written.
- **INI safety:** A paren/quote-depth-aware splitter edits curated keys while preserving all other settings (e.g., nested `CrossplayPlatforms`, server name). The implementation has been verified by applying ExpRate changes both off-server and on a running server; crossplay/name/password/saves remain intact.
- **Other game support:** Adding another game requires one `SETTINGS_SCHEMA` entry plus an appropriate writer if the target format is not standard INI. Satisfactory uses its HTTPS API (`ApplyAdvancedGameSettings`). Valheim utilizes launch arguments.
- **Jinja note:** `settings.values` resolves to the dict's `.values()` method; access must use `settings['values']`.
