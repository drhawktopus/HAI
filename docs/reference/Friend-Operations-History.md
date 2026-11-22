# Friend Onboarding System - Operations & History



## Automation on the owner's side (near-zero)
The following components are configured by default during user import:
- **Plex**: Invite generation is handled automatically via the Plex API for Movies+TV libraries.
- **Seerr**: Auto-import of Plex users triggers upon sign-in. New-user permissions follow sane defaults with an option to request approval before auto-approval. The watchlist sync target is `watchlistSyncMovies/Tv`, and a default request is issued immediately upon import.
- **Discord**: Membership role assignment occurs automatically via bot logic when the user joins.

### Resumable onboarding
An unfinished friend onboarding step resumes automatically after re-authentication via Google or email. Progress tracking and the resumption key are keyed by identity rather than a fresh code, allowing completion of optional steps without generating new codes.
The single-use entry gate blocks unauthenticated random access to limit initial system exposure.",

## Post-launch additions
This document records configuration updates applied to the system. Historical context, decision processes for these changes, and narratives regarding their implementation are preserved in session journals.
The following live-state facts apply as of this document's revision:

### `request.example.org`
*   **Function:** DNS resolution terminates here (previously serving plural name `requests`).
*   **Routing Logic:** Caddy reverses to the Overseerr application URL and environment variables.
*   **Compatibility Layer:** 301-redirects from old paths are managed by Caddy. Consult `reference-hai-user-identity-map` in Claude memory for identity context.

### HAI Authentication (`hai.example.org`)`
The login endpoint `/hai/authz` enforces the following flow:
*   **Response:** Returns a 302 redirect to `join…/start?next=hai` for unauthenticated users.
*   **Authorization Grants:** Allows access only after Google-verified admin validation or provisioned friend status.`

### Admin Dashboard (`admin.example.org`)`
The mint/dashboard panel is now gated as follows:
*   **Access Control:** Requires a verified `ADMIN_EMAILS` (default: <email>). Password login is disabled.
*   **Spoofing Prevention:** Email-based access requires `auth_provider=='google'`. Non-admins receive `admin_denied.html`
types).

### User Provisioning Matrix
The following mapping applies to existing Plex/Seerr users mapped by email key:
| Status | Profile(s) | Allowed Services |
|---|---|---|
| **HAI + Provisions** | Riley/nursing, Jordan/jordan, Officer Parker/law, Morgan/nursing_morgan, Avery/ungrounded (5 profiles total) | `hai`, Discord, Plex/Seerr |
| **Plex/Discord Only** | Remaining mapped users (10 profiles excluded from HAI selection) | Plex/Seerr, Discord |
*   **Excluded:** the owner(admin), Discord-bot.

### Backfill Execution`The following command executes the backfill script located in `onboarding-app/backfill.py`:"
```bash
dry-run: default (no state change)
cmd --commit: applies mapping to provisioned users
```
The map used for identity resolution resides at `reference-hai-user-identity-map` within Claude memory.

### The rest of HAI
1. **Identity handoff and account-selection LOCK.** Orchestrator `app.py` reads the gate's `X-Hai-User` header (`_gated_identity`, `_is_locked`, `_enforce_identity`, `_enforce_user_param`) to pin a non-admin user to their own profile on `/query`+`/query/stream` and per-user endpoints (`/history`,`/memory`,`/voice`, `/upload`, resets, compact). The self-asserted `user` cannot override this lock. `/profiles` returns the locked user; the chat UI auto-pins it, dims other accounts, and displays a **Jurassic-Park "ah ah ah! you can only pick your own account"** notification for blocked selections. Admins (identified by `ADMIN_USERS={the owner}`) receive full selector access when sent `X-Hai-User: the owner`. LAN requests without the header remain unchanged.
Caddy's `hai` block is wrapped in a Caddyfile `route{}` to strip client `X-Hai-*` headers before forward_auth processes them (anti-forgery).
The container suite passing 800 tests confirms health status 200 and live-probed readiness. A backup `app.py.bak-hailock-<date>` exists.
Reference: see Claude memory `reference-hai-user-identity-map`.

2. **Apple-relay / Plex-verify login.** The login page offers "Sign in with Plex" alongside Google and typed-email inputs, accompanied by an on-page explainer for Apple users signing into Plex via a non-Google email.
The `/auth/plex` endpoint initiates Plex PIN OAuth to `/auth/plex/callback`, which maps the resulting account to the friend record using Plex username/email (`_find_by_plex`) and sets `auth_provider="plex"`. This marks "plex" as a VERIFIED auth provider. Consequently, HAI access now strictly requires a verified provider (**google** OR **plex**) for authenticated sessions; typed-email alone no longer grants access.
This configuration closes the spoof vulnerability by ensuring Apple users like Riley gain entry via Plex.
Verification confirms that the button and explainer render correctly.
The `/auth/plex` endpoint redirects (302) to `app.plex.tv`

3. **Data-driven namespace provisioning.** The `POST /admin/provision_user` interface allows NEW friends to automatically receive a corpus matching Jordan-style configurations. Existing users are already mapped; this provision is intended for future additions.

## Build plan
The onboarding process follows the following phases:

### Phase 0: Infrastructure and Secrets Setup
Create GCP project `onboarding-hawktopus`. Configure consent screen settings (External, publish; non-sensitive scopes). Provision Web OAuth client with redirect URI. Harden the droplet configuration.
Store CLIENT_ID and CLIENT_SECRET in a gitignored `.env` file accessible only by manual input during setup.

### Phase 1: Network Exposure
Configure the reverse proxy to serve at `request.example.org`. Implement DNS-01 LE certs, DO firewall rules, three DNS A records, and establish Tailscale routing home Caddy. Verify Seerr availability.

### Phase 2: Virtual Host Configuration
Assign local onboarding app vhosts to the names defined in scope.

### Phase 3: Application Implementation
Implement core authentication flows (Google/email). Enable Plex auto-invite [Movies+TV], Discord integration, and status dashboard. Containerize components using the lightweight-Flask pattern identical to `doplarr-admin`.

### Phase 4: Automation Layering
Configure Seerr/Discord automation for default permissions/watchlist enforcement and bot role assignment.

### Phase 5: User Guidance
Deploy welcome tutorial page with GIFs.

### Phase 6: Validation
Execute end-to-end test using a real invite flow.

## Related facts / reuse
- Onboarding app follows the `doplarr-admin` pattern. This includes using a Flask container with secrets gitignored in `.env`, served via Caddy.
  
- The Seerr service is deployed as the `seerr` container, using service key `overseerr`. It operates at port 5055 on arrnet and must be reachable via that host:port combination.

---
