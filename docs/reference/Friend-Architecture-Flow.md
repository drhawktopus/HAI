# Friend Onboarding System - Architecture & Flow



## Purpose
The system enables friends to join the media stack via self-serve procedures. Outcomes include Plex access (Movies and TV only), Discord membership, Seerr connectivity, and tour reception.

**Operational Status:**
The following operational facts apply:
*   Deployment state: BUILT + DEPLOYED + LIVE as of 2026-07-05.
*   Implementation authors: Claude + the owner.

## Access architecture (DO-native — NO Cloudflare)

### Topology
```
friend → *.example.org  (DO DNS A → static public IP) 
       ↓
hardened reverse proxy on the DO Pi-hole droplet (public HTTPS via Let's Encrypt, DNS-01) 
       ↓
over Tailscale (private transport)
       ↓
home Caddy (home LAN)  [sees only outbound-established traffic]
       ↓
Seerr / onboarding app
```

### Infrastructure Composition
The system utilizes the existing DigitalOcean droplet as the public front door and employs **Tailscale** for private transport to the home network. This configuration functions as a self-hosted equivalent of a Cloudflare Tunnel.
- The droplet maintains a static public IP address via DO API provisioning, eliminating the need for DDNS on these records.
- Home infrastructure ports remain closed; traffic ingress occurs exclusively through Tailscale's outbound-established connections.

### Rationale and Trade-offs
This architecture was selected to maintain fully-owned infrastructure without migrating nameservers, avoiding dependence on a third-party cloud provider's access layer (specifically Cloudflare Access or WAF).
The accepted trade-off is the absence of turnkey managed security features such as automatic Web Application Firewalls. Risk mitigation relies strictly on droplet hardening and operational gating procedures.

### Integration Boundaries
- **Plex Streaming:** Does not traverse this network path. Plex utilizes its own remote access mechanisms independent of the Tailscale gateway described here.

### URLs
| URL | Purpose | Gate |
|-----|---------|------|
| `join.example.org` | Onboarding flow entry point | invite code |
| `welcome.example.org` | Explanation and tutorial page (text + GIFs) | public |
| `request.example.org` | Seerr request interface | Plex login |
| `hai.example.org` | Home AI assistant access | onboarding identity verification |
| `portfolio.hawktopus.org` | Public engineering portfolio (recruiter-facing; Hawktopus-Portfolio-Site) | public |

## Onboarding flow (`join.example.org`)
1. **Invite code gate** — single-use, 30-day expiry, labeled per friend, carries caps. Access is provided embedded at `join.example.org/join/<code>`, auto-filling form fields OR via typed input.
   - Minting occurs from the admin panel (returns ready-to-send link).
   - State store: JSON mapping `{code → {label, caps, used, createdAt, expiresAt}}`.
2. **Auth surface** — "Sign in with Google" + typed-email fallback.
   - Primary path: Google returns verified Gmail address for most friends (one click).
   - Fallback covers Apple/password users; specifically handles Apple users typing their Plex-account email.
   - Constraint: NO "Sign in with Apple" button implemented. Rationale requires a $99/yr Apple Developer account AND an Apple per-app Hide-My-Email relay which would yield an address different from the recorded Plex handle, breaking email matching logic.
3. **Auto-invite to Plex via API** — shares ONLY Movies + TV Shows libraries (config value `SHARED_LIBRARY_IDS`, default set to Movies+TV; Music and others excluded).
   - Target: Invites captured email.
   - Policy: Encourage a recognizable Plex username (real name, gamertag, or nickname) for friends creating NEW accounts to ensure identifiability. Display-name auto-fix is applied under Automation for existing cryptic handles.
4. **Discord** — recommended but OPTIONAL/skippable; not a gate.
   - Provision: If absent, prompt creation at `discord.com/register` with rationale (chat request capability, ping notification when ready, server announcements).
   - Process: Join-server link provided → bot auto-assigns member role → links Discord ID for attribution + notifications.
   - Opt-out: Always show "Skip for now." Friend who skips retains full functionality via `request.example.org` but loses `/request` shortcut and availability pings. Note: No email fallback exists since no mail service is run; web-only friends check Seerr/Plex themselves. Discord acts as the unique identity bridge not covered by standard OAuth flows, linked solely by its own Discord ID.
5. **Seerr** — "Sign in with Plex" → Overseerr auto-imports users with default new-user permissions. Includes button to `request.example.org`.
6. **Completion** → redirects to `welcome.example.org` for the full tour.

## Seerr "access denied" during onboarding: the Plex invite is not accepted yet
Seerr refuses a Plex user until they have accepted the library invite. The log line in the `seerr` container is `Failed sign-in attempt by Plex user without access to the media server`, with their `plexUsername` and `email`.

**Fix for the friend:** accept the invite from the Plex email or in Plex under Settings, then Manage Library Access, then sign in to Seerr with Plex again. Seerr creates their user on that sign-in.

**What the setup page does (since 2026-11-06):** `plex.invite_pending()` returns True (pending), False (no pending invite) or None (plex.tv could not be asked). `/flow` asks only while the Plex share is sent and the Seerr step is unfinished. On True the Seerr step shows the accept instructions, an Open Plex button, "I've accepted", and a smaller "Open requests anyway" link, kept because Plex forum reports describe invites that stay listed as pending after acceptance. False and None show the normal Open requests button, so a plex.tv outage cannot block anyone. An accepted share overrides a pending row.

## Email = primary key
The friend record is keyed by the email address captured at `join` time. The system maintains a per-friend status dashboard driven by this email, tracking progression from invited to accepted Plex, signed into Seerr, and Discord linked.
Mismatched states are flagged within the dashboard when an invitee (X) differs from the user's current state in Seerr (Y). Operational guidance instructs users to use their primary Google account on Plex. The system flags cases where a multi-account user selects an incorrect identity.

## Onboarding app architecture and deployment prerequisites

### Component structure
The onboarding application follows the Lightweight-Flask-container pattern (doplarr-admin). Source code resides in `onboarding-app/`:

*   `app.py`: Host-aware routes, Google OAuth via Authlib, typed-email fallback, resumable flow, `forward_auth` gate at `/hai/authz`, admin mint panel.
*   `invites.py`: Granular caps and single-use enforcement.
*   `store.py`: Email-keyed resumable records.
*   `plex.py`: Sign-in-with-Plex PIN integration, owner share of Movies+TV, runtime resolution of section IDs.
*   `seerr.py`: Import logic; display-name mapped to code label.
*   `hai.py`: Persona cross-reference vs general/nursing/law; provision adapter; new-persona/provision Discord pings.
*   `templates/`: Base, landing, start, flow, hai, admin, admin_login, welcome views.
*   Configuration: `Dockerfile`, `requirements`, `.env.example`.

### Deployment configuration
DNS records for `join`, `welcome`, `hai`, and `requests` resolve to `<droplet-address>`.

Deployment to production requires:
1.  Population of `.env` with Google client ID/secret, two Discord invite links, and HAI webhook URL. Credentials are documented in `see arr-stack-credentials.md §Google OAuth`
2.  Container deployment to arrstack.
3.  Droplet Caddy vhosts for join/welcome/hai endpoints, including a default-deny catch-all.
4.  Implementation of the HAI data-driven-namespace provisioning endpoint. (Interim state: ping-to-provision is operational.)
5.  Provisioning of welcome GIF assets.
6.  Supervised end-to-end testing with a live invite. Note: The first Plex share operation is side-effecting.

## Tutorial (`welcome.example.org`)
The tutorial page serves three operational functions:
- **Request mechanisms**: Seerr web interface, Discord `/request` command, and Plex watchlist integration.
- **Consumption guidance**: Instructions for launching Plex applications on specific devices.
- **Support escalation**: Direct link to the Discord channel for assistance.

## Auth-provider semantics
The application recognizes three authenticated identity providers: `google`, `email` (unverified), and `plex`. These are defined by two constant sets:
- `VERIFIED_PROVIDERS = (`"google", "plex`") — Represents identities that have been actually proven.
  This set gates access to HAI, the radarr/sonarr/qbit `/svc/<cap>/authz` endpoints, and `_is_verified_admin`. The untyped provider `email` is intentionally excluded from this verified set due to spoofability risks.
- `AUTHED_PROVIDERS = (`"google", "email", "plex`") — Represents entities that have completed an authentication flow (distinguishing them from backfill or added entries). This drives the display of the roster "joined" badge via `_person_state`.

## Welcome-page identity verification policy

The welcome page (`welcome.example.org`) and infrastructure pages (`/showcase`, `/projects`, `/architecture`) enforce strict identity verification before disclosing user data or administrative views.

**Identity Disclosure Rules**

1. **Personalization requires verified providers.** The system displays user identity (name) and personalized application lists only when the session originates from a provider in `VERIFIED_PROVIDERS` (Google, Plex). Unverified sessions display no personalization.
2. **Typed-email login is untrusted for identity.** The `auth_email` mechanism sets `session["email"]` for any input string without verifying ownership. This mechanism does not grant access to personalized views or administrative features.
3. **Administrative access requires authentication.** Access to infrastructure pages requires a verified session (`_is_admin_authed()`). Mere presence of an email address in `ADMIN_EMAILS` via unverified typed-email login does not grant access. Unverified attempts receive HTTP 404 responses.

**Cache and Session Scope**

Identity data is scoped to individual browser sessions via cookies. The server returns `Vary: Cookie` headers and excludes shared `Cache-Control` directives for these pages, preventing cross-user cache leaks. Anonymous or unauthenticated visitors receive the generic sign-in interface.

**Open Design Decisions**

Typed-email login currently permits entry into `/flow`, exposing onboarding-step status for the typed email address. Resolution requires one of:
* Require verified providers for all `/flow` access.
* Deprecate unverified typed-email identity tracking entirely.
