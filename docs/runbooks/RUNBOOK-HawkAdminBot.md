# RUNBOOK — HawkAdminBot (Discord admin)



## HawkAdminBot (Project #14)

Discord admin bot on SundaeStackarr. Container `hawkadminbot` (compose **profile `hawkadmin`**), source
`/opt/arrstack/hawkadminbot` (mirror `E:\SSDShared\hawkadminbot`), token `HAWKADMIN_DISCORD_TOKEN` in `.env`.
Bot username `HawkAdminBot#9525`, Client ID `<discord-id>`. Has **Administrator** in both guilds
(Plex Requests and Info + The Roundtable). Admin = the owner's Discord ID `<discord-id>` (`HAWKADMIN_ADMIN_IDS`).

```bash
# IMPORTANT: bot.py is COPY'd into the image → ALWAYS rebuild on code change:
cd /opt/arrstack && docker compose up -d --build hawkadminbot
docker logs hawkadminbot --tail 30
```
A bare `--force-recreate` WITHOUT `--build` silently runs the OLD image.

**What it does:**
- **Contribution bridge** — polls HAI `GET /contributions?user=the owner&status=pending` every 30s → posts each new one
  to `#hai-approvals` (private, in the `🔒 Backend` category) with ✅ Approve / 🗑️ Reject buttons → click calls
  `POST /contributions/approve|reject`. Buttons disable instantly + an in-flight guard prevents double-approve.
  Channel wired via `HAWKADMIN_CONTRIB_CHANNEL_ID`.
- **`#plex-status`** (public) — polls Plex `/identity` every 60s, keeps ONE live pirate-styled 🟢/🔴 status message
  (edits in place → no user pings) + flips the channel-name emoji on state change. `HAWKADMIN_PLEX_STATUS_CHANNEL_ID`.
- **Slash commands** (admin-only via `default_member_permissions=Manage Server`): `/whoami /invites /invite-delete
  /role /contributions`.
- **`discord_admin.py`** — REST toolkit (stdlib, no gateway) to drive Discord structure via the bot token:
  `channels <guild>`, `read <chan>`, `mkcat --private`, `mkchan --parent --sync`, `edit --name/--parent/--topic`,
  `visibility --public|--private`. Run: `cd /opt/arrstack/hawkadminbot && python3 discord_admin.py ...`. This is how
  Claude does Discord channel changes (no browser). Current structure in Plex Requests and Info: private
  `🔒 Backend` category (audit-log, uptime, watchtower-updates, media-ready, hai-approvals) + public `plex-status`.

**Known bug (backlog):** `contributions.approve()` TOCTOU race — writer runs outside the lock; the web Review panel
could double-write. Bot side is guarded; orchestrator fix pending. *(The REMEDY queue below does NOT inherit this —
its decision and execution share one lock by design.)*

**Decision queues (10d):** the contribution poll is a **decision-queue table** (`QUEUES` in bot.py) —
contributions AND Tier B fleet remedies ride one poll/one interaction handler. A new consent queue = a table row.
Remedy cards post to `REMEDY_CHANNEL_ID` (env; unset → same channel as contributions). Old cards keep working
(custom_id prefix = table key). Game-request buttons are NOT a queue (they call the games app) and keep their own
handler.

---
