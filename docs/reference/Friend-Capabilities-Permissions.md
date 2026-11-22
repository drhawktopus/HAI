# Friend Onboarding System - Capabilities & Permissions



## Friends' HAI orchestration scope
Expose the Home AI orchestrator at `hai.example.org` to a subset of onboarded friends.
The system determines access and persona assignment via capability flags on invite records.

### Capability-based exposure logic
*   **Invite record extension**: Add a `caps` array (e.g. `["plex","hai"]`) to the invite record during code minting.
    *   Only codes containing the `hai` flag unlock the HAI onboarding step; all other users are bypassed for this stage.
*   **Onboarding flow** (executed only if `hai` capability is present):

### Persona assignment and provisioning
Upon receiving use-case inputs:
1.  Cross-reference user input against the existing persona set.
2.  **If covered by an existing persona**: Assign the `general` persona and provision a dedicated corpus identical to established HAI users.
3.  **If novel (no match)**: Route the session to the general persona immediately and alert the owner via Discord webhook for manual persona creation based on the summary.

### HAI provisioning recipe (from `orchestrator/config.py`, verified 2026-07-05)

Structure = **persona-shared corpus per persona** (`nursing_shared`→`/docs/NursingCorpus`, `law`→`/docs/LawCorpus`)
**+ each user's own writable private corpus** (grounding = `identity_ns ∪ persona-shared`). **"Jordan" is the template**
for a friend: a *general* user with his own buildable corpus. To mint another Jordan:
1. Create corpus dir `USERCORPORA_DIR/<name>` (Workspace share `//<softserve-address>/Workspace/.../corpus` → `/usercorpora/<name>`).
2. Register `RAG_NAMESPACES["<name>"] = [".../<name>"]`. Not in `READONLY_NAMESPACES` ⇒ **writable by default**.
3. Map `USER_NAMESPACE` env: `<User>:<name>`.
4. Persona: none needed — `default_persona_for()` → `general` (only nursing/law users need `USER_DEFAULT_PERSONA`).
5. Redeploy orchestrator.

Personas are a fixed registry `PERSONAS=(general,nursing,law)` via `GET /modes`. A genuinely NEW domain requires a new persona and real authoring (system prompt + shared corpus). This stays a manual the owner job. Per-user corpora do not need a new persona.

RAG namespaces for per-user data are data-driven. A registry file or auto-scan of `/usercorpora/*` merges into `RAG_NAMESPACES` and `USER_NAMESPACE` at load time. Onboarding creates the corpus directory, appends the registry entry, and triggers a reload. The onboarding application acts as the admin actor for provisioned users.

Provisioning reuses the existing HAI per-user corpus mechanism. Identity is the onboarding email (primary key), threading HAI corpus access to the same person as Plex/Seerr.

Identity uses the onboarding session cookie. The Droplet Caddy at `hai.example.org` performs `forward_auth` to the onboarding app (`/hai/authz`). The application checks if the caller is a known friend with the `hai` cap and HAI provisioned. If yes, Caddy proxies through to the HAI orchestrator (home, over Tailscale). If no, it redirects to `join.example.org`. HAI itself stays auth-agnostic behind this gate.

## Capability grants and defaults

A code carries a subset of available capabilities:
caps ⊆ {plex, discord, roundtable, hai}
The flow logic executes only the steps corresponding to granted caps.

| cap | function |
default at mint|
|-----|----------|------------------|
| `plex` | Plex library share (Movies+TV) + Seerr requests |	**ON** |
| `discord` | Main/Media Discord server access (community, request pings) |	**ON** |
| `roundtable` | Private *gaming* Discord for closest friends |	**OFF** |
| `hai` | Home-AI step + `hai.example.org` access|	**OFF** |

The default mint configuration is `[plex, discord]`. This corresponds to the common media invite.
The admin panel provides checkboxes to enable or disable capabilities:
- Adding `roundtable`
or `hai`
to a mint.
- Dropping either capability from the defaults.

`roundtable` uses a separate server-invite link distinct from the main one. Both invites are environment-driven, utilizing keys `DISCORD_MAIN_INVITE` and `DISCORD_ROUNDTABLE_INVITE`, respectively.

## Auto-provisioning and corpus sync
Granting the `hai` capability triggers immediate auto-provisioning of the friend's corpus via a real server-to-server call to the orchestrator.

## Cap-driven flow resumption
The onboarding flow is cap-driven and resumable. New capabilities added to `caps` surface as new steps or cards during the friend's next sign-in.

## Access change notification
A **"🎉 You've got new access"** banner displays on `/flow` when current `caps` differ from stored `caps_seen`. The system updates `caps_seen` once the banner is shown. The check is impersonation-safe: previewing the page does not consume or update the flag.

## ADDENDUM — Co-owner service caps (radarr/sonarr/qbit)

**Purpose and scope:**
Possessing a co-owner cap exposes the home **arr admin tools** to *fully-trusted, co-owner-level* people.
These grants provide FULL control (Radarr/Sonarr add/**delete**; qBittorrent manage any torrent).
The applications lack per-user permissions; therefore, these caps are never general friend capabilities
and must be granted per-person sparingly.
Bandwidth consumption for the management UIs is negligible. QBit's actual torrent data traverses the VPN
to disk without using the WebUI/droplet bandwidth allowance (1 TB/mo).

**Access model:***Gate plus application authentication.*
The droplet gate permits access only to a verified friend holding the cap.
The app's own login serves as the second lock:
- qBittorrent uses its native single-login system.
- Radarr/Sonarr require auth enablement (optionally active for belt-and-suspenders).

**Implementation:**
This pattern replicates HAI/Seerr droplet behavior.
The following configuration applies to `radarr`, `sonarr`, and `qbit`:
1.  **Capabilities**: Services added to `invites.VALID_CAPS` + `GATED_SERVICE_CAPS`;
a welcome page displays a "Co-owner tools" button group; admin/manage panels display them with CO-OWNER labels.
2.  **Authorization Gate** (`GET /svc/<cap>/authz`) onboarding:
    - Returns `302→login` if unauthenticated.
    - Returns `200` if verified and holds the cap.
    - Returns `403` otherwise.
    No identity headers are injected; the application handles its own identity verification.
3.  **Droplet Caddy** per service:
    - `radarr`: reverse proxy to host port `7878` via droplet `<sundae-address>:8466` with gate route `/svc/<cap>/authz`.
    - `sonarr`: reverse proxy to host port `8989` via droplet `<sundae-address>:8466` with gate route `/svc/<cap>/authz`.
    - `qbit**: ExpressVPN netns (no host port); hops through Sundae Caddy (`reverse_proxy <sundae-address>:80 { header_up Host qbit.lan }`).
4.  **DNS**:
a-records for `radarr`, `sonarr`, and `qbit` resolve to the droplet, overriding the home wildcard (`*`).
The record ID maps via a direct Seerr alias (known as `seer.lan`) with `/login`. IDs are recorded in RUNBOOK.

**Extension procedure:**
To add another service cap later:
1.  Add the cap to `VALID_CAPS`+`GATED_SERVICE_CAPS`.
2.  Add a welcome button.
3.  Add a droplet Caddy `route{}` configuration block.
4.  Add a DNS A-record pointing to the droplet.
Candidates for future inclusion: lidarr, tautulli, prowlarr, jackett, pinchflat (see `.lan` list).
