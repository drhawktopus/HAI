# Discord relay

A small Discord bot for a home lab running HAI. It does three jobs:

1. **Contribution approvals.** Every 30 seconds it asks the head for pending contributions
   (`GET /contributions?user=<HAI_ADMIN_USER>&status=pending`) and posts each new one in an admin channel with
   **Approve** and **Reject** buttons. A click calls the head's `/contributions/approve` or `/contributions/reject`.
2. **Notices to a person.** HAI and its factories send direct messages through it: a factory's overnight notices, the
   head's claim-sweep notices and quick previews. It listens for those on port 8477 (`DM_PORT`) and accepts a request
   only with the shared token (`DM_TOKEN`).
3. **Server admin commands** that other bots do not cover: `/invites`, `/invite-delete <code>`,
   `/role add|remove <member> <role>`, `/whoami`, `/contributions`. With `PLEX_CHECK_URL` set it also posts a Plex
   server's status.

It talks to Discord over the gateway only, so it needs no public address and no inbound HTTPS.

## Run it

The full-stack example runs it as the `hawkadminbot` service, under a profile (`examples/full-stack`):

1. **Make the bot in the Discord developer portal:**
   - Bot → Privileged Gateway Intents → turn on **Server Members Intent**.
   - Bot → turn **Public Bot** off.
   - Invite it to your server with the scopes `bot applications.commands` and the permissions Manage Server, Manage
     Roles, Create Invite, Read Messages and Send Messages.
   - Copy the bot's token.
2. **Fill in the relay's settings.** In `examples/full-stack`, copy `relay.env.example` to `relay.env` and fill it
   in: the bot's token, your Discord user id, your channel and server ids. For `DM_TOKEN`, use the contents of
   `state/secrets/dm_token`, the token file the stack's setup made. Keep `relay.env` to yourself; it holds the bot's
   token.
3. **Give the head its half.** In `sidecars.env`, uncomment the Discord relay lines and set your Discord user id. The
   head reads the same token from `state/secrets/dm_token`.
4. **Start it:** `docker compose --profile discord up -d`, then read `docker logs hawkadminbot --tail 30`.

Without the full-stack example, build this folder (`docker build -t hai-discord-relay .`) and run the image with the
same settings in an env file, on a network where it can reach the head.

## Settings

| Setting | Meaning |
|---|---|
| `DISCORD_TOKEN` | The bot's token. Unset: the bot logs a warning and exits. |
| `DM_TOKEN` | The shared token for direct-message requests. Unset: no DMs are sent. |
| `DM_PORT` | The port it takes those requests on (8477). |
| `ADMIN_DISCORD_IDS` | Space- or comma-separated Discord user ids allowed to approve, reject and run the admin commands. Anyone with Manage Server or Administrator in the server also qualifies. |
| `CONTRIB_CHANNEL_ID` | The channel contribution cards are posted to. 0: no posting (`/contributions` still works). |
| `GUILD_IDS` | Server ids for instant slash-command sync. Empty: global sync, which can take an hour. |
| `HAI_BASE_URL` | The head's address (the full-stack example sets `http://hai:8099`). |
| `HAI_ADMIN_USER` | The HAI profile whose pending contributions it posts: your `OWNER_USER`. |
| `CONTRIB_POLL_SECONDS` | How often it asks the head (30). |
| `REMEDY_CHANNEL_ID` | Where fleet remedies are posted; 0 uses the contributions channel. |
| `PLEX_CHECK_URL`, `PLEX_STATUS_CHANNEL_ID`, `PLEX_POLL_SECONDS` | Optional: a Plex server's identity URL, the channel its status goes to, how often (60). |
| `STATE_DIR` | Where it keeps what it has already posted (`/state`). |
| `ONBOARDING_URL`, `GAMEOPS_TOKEN` | The lab's own onboarding app, which is not part of this release: leave them unset. |

## The admin tool

`discord_admin.py` is a command-line helper for the same Discord admin jobs (`python3 discord_admin.py channels
<server id>` and so on). It reads the token from `HAWKADMIN_DISCORD_TOKEN`, else from the file `HAWKADMIN_ENV_FILE`
names, else from a `.env` beside it. Never pass the token on the command line.
