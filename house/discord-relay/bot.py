#!/usr/bin/env python3
"""
HawkAdminBot
============
A lightweight Discord bot for a homelab stack that does two jobs:

  1. HAI corpus-contribution bridge — polls the HAI orchestrator's admin
     contribution queue and posts each new PENDING item into an admin channel
     with Approve / Reject buttons. An admin's click calls the orchestrator's
     /contributions/approve|reject API: a two-way control surface.

  2. General Discord admin — slash commands the current bots can't run because
     they lack Manage Server / Manage Roles: /invites, /invite-delete, /role,
     plus /whoami and /contributions.

Connection model: gateway (bot token) only. Button interactions arrive over the
gateway, so NO public interactions endpoint / inbound HTTPS is required.

All config comes from environment (see docker-compose block / .env). The bot is
inert without DISCORD_TOKEN, so it is safe to stage before its token exists.
"""

import os
import sys
import json
import logging
import asyncio

import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import tasks

# --- config from env ------------------------------------------------------------
TOKEN               = os.environ.get("DISCORD_TOKEN", "").strip()
HAI_BASE            = os.environ.get("HAI_BASE_URL", "http://orchestrator:8099").rstrip("/")
HAI_ADMIN_USER      = os.environ.get("HAI_ADMIN_USER", "owner")
CONTRIB_CHANNEL_ID  = int(os.environ.get("CONTRIB_CHANNEL_ID", "0") or 0)
POLL_SECONDS        = int(os.environ.get("CONTRIB_POLL_SECONDS", "30") or 30)
STATE_DIR           = os.environ.get("STATE_DIR", "/state")
# Guilds to fast-sync slash commands to (comma/space separated). Empty = global sync.
GUILD_IDS           = [int(x) for x in os.environ.get("GUILD_IDS", "").replace(",", " ").split() if x.strip().isdigit()]
# Discord user IDs allowed to approve/reject + run admin commands (in addition to
# anyone holding Manage Server / Administrator in the guild).
ADMIN_IDS           = set(int(x) for x in os.environ.get("ADMIN_DISCORD_IDS", "").replace(",", " ").split() if x.strip().isdigit())

# Public Plex-status channel: the bot polls Plex and keeps a live status message here.
PLEX_STATUS_CHANNEL_ID = int(os.environ.get("PLEX_STATUS_CHANNEL_ID", "0") or 0)
PLEX_CHECK_URL         = os.environ.get("PLEX_CHECK_URL", "")   # Plex's /identity URL; unset = no Plex status
PLEX_POLL_SECONDS      = int(os.environ.get("PLEX_POLL_SECONDS", "60") or 60)

# Small authenticated HTTP endpoint so other LAN services (the onboarding app) can ask the
# bot to DM a user — e.g. deliver an onboarding link. Disabled unless DM_TOKEN is set.
DM_PORT  = int(os.environ.get("DM_PORT", "8477") or 8477)
DM_TOKEN = os.environ.get("DM_TOKEN", "").strip()

# Games app callback — for the request-a-game approval buttons (Approve/Reject in a DM call back here).
ONBOARDING_URL = os.environ.get("ONBOARDING_URL", "http://onboarding-app:8466").rstrip("/")
GAMEOPS_TOKEN  = os.environ.get("GAMEOPS_TOKEN", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hawkadminbot")

POSTED_PATH = os.path.join(STATE_DIR, "posted_contributions.json")
REMEDY_POSTED_PATH = os.path.join(STATE_DIR, "posted_remedies.json")
PLEX_MSG_PATH = os.path.join(STATE_DIR, "plex_status_msg.json")

# Tier B remedy cards post here; unset -> same channel as contributions.
REMEDY_CHANNEL_ID = int(os.environ.get("REMEDY_CHANNEL_ID", "0") or 0) or CONTRIB_CHANNEL_ID


def load_posted(path=POSTED_PATH) -> set:
    try:
        with open(path, encoding="utf-8") as fh:
            return set(json.load(fh))
    except Exception:
        return set()


def save_posted(posted: set, path=POSTED_PATH) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sorted(posted), fh)
        os.replace(tmp, path)
    except Exception as exc:
        log.warning("could not persist posted set (%s): %s", path, exc)


# --- HAI orchestrator client ----------------------------------------------------
async def hai_get(session, path, params=None):
    async with session.get(HAI_BASE + path, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
        r.raise_for_status()
        return await r.json()


async def hai_post(session, path, payload):
    async with session.post(HAI_BASE + path, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
        body = await r.json(content_type=None)
        return r.status, body


# --- bot ------------------------------------------------------------------------
class HawkAdminBot(discord.Client):
    def __init__(self):
        # Default (non-privileged) intents are enough: slash commands + button
        # interactions arrive over the gateway, and role/member args resolve
        # per-interaction, so no privileged Server Members intent is required.
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.http_session: aiohttp.ClientSession | None = None
        # One posted-ids set per decision queue (QUEUES below). Contributions keep their
        # original state file, so nothing already posted is ever re-posted.
        self.posted = {"contrib": load_posted(POSTED_PATH),
                       "remedy": load_posted(REMEDY_POSTED_PATH)}
        self._plex_up = None  # last known Plex state (None = unknown)
        self._processing = set()  # (queue, id) decisions in flight (double-click guard)
        self._dm_runner = None

    async def setup_hook(self):
        # Keep setup_hook light: it runs INSIDE login(), before the gateway
        # connects, so any slow/rate-limited HTTP here stalls the whole connect.
        # Command sync happens in on_ready (only for guilds we're actually in)
        # and in on_guild_join — never a blind startup sync that 429s forever.
        self.http_session = aiohttp.ClientSession()
        self.poll_queues.start()
        if PLEX_STATUS_CHANNEL_ID:
            self.poll_plex_status.start()
        await self._start_dm_server()
        log.info("loops started (contrib_chan=%s plex_chan=%s)",
                 CONTRIB_CHANNEL_ID, PLEX_STATUS_CHANNEL_ID)

    # --- DM-on-request HTTP endpoint (for the onboarding app) ---------------------
    async def _start_dm_server(self):
        if not DM_TOKEN:
            log.info("DM endpoint disabled (no DM_TOKEN set)")
            return
        app = web.Application(client_max_size=10 * 1024 ** 2)   # default 1MB rejects preview PNGs
        app.router.add_post("/dm", self._handle_dm)
        app.router.add_post("/dm-file", self._handle_dm_file)
        self._dm_runner = web.AppRunner(app)
        await self._dm_runner.setup()
        await web.TCPSite(self._dm_runner, "0.0.0.0", DM_PORT).start()
        log.info("DM endpoint listening on :%d", DM_PORT)

    async def _handle_dm(self, request):
        if request.headers.get("X-DM-Token", "") != DM_TOKEN:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=403)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "bad json"}, status=400)
        uid, msg = data.get("user_id"), data.get("message")
        if not uid or not msg:
            return web.json_response({"ok": False, "error": "user_id and message required"}, status=400)
        try:
            user = self.get_user(int(uid)) or await self.fetch_user(int(uid))
            req = data.get("request") or {}
            view = GameRequestView(req["id"]) if req.get("id") else None
            await user.send(msg, view=view)
            log.info("DM sent to %s%s", uid, " (game-request buttons)" if view else "")
            return web.json_response({"ok": True})
        except discord.Forbidden:
            return web.json_response(
                {"ok": False, "error": "their DMs are closed (or they don't share a server with the bot)"})
        except discord.NotFound:
            return web.json_response({"ok": False, "error": "no Discord user with that id"})
        except Exception as exc:
            log.warning("DM to %s failed: %s", uid, exc)
            return web.json_response({"ok": False, "error": str(exc)})

    async def _handle_dm_file(self, request):
        """DM with a file attachment. Multipart fields:
        user_id, message, file (binary, filename honored). Same token gate as /dm."""
        if request.headers.get("X-DM-Token", "") != DM_TOKEN:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=403)
        try:
            data = await request.post()          # aiohttp's own multipart parser
            uid = (data.get("user_id") or "").strip()
            msg = data.get("message") or ""
            f = data.get("file")
            fname = getattr(f, "filename", None) or "image.png"
            fbytes = f.file.read() if hasattr(f, "file") else None
        except Exception as exc:
            log.warning("dm-file multipart parse failed: %s", exc)
            return web.json_response({"ok": False, "error": "bad multipart: %s" % exc}, status=400)
        if not uid or fbytes is None:
            return web.json_response({"ok": False, "error": "user_id and file required"}, status=400)
        if len(fbytes) > 8 * 1024 * 1024:
            return web.json_response({"ok": False, "error": "file too large (8MB cap)"}, status=413)
        try:
            import io
            user = self.get_user(int(uid)) or await self.fetch_user(int(uid))
            await user.send(msg or "", file=discord.File(io.BytesIO(fbytes), filename=fname))
            log.info("DM+file sent to %s (%s, %d bytes)", uid, fname, len(fbytes))
            return web.json_response({"ok": True})
        except discord.Forbidden:
            return web.json_response(
                {"ok": False, "error": "their DMs are closed (or they don't share a server with the bot)"})
        except Exception as exc:
            log.warning("DM+file to %s failed: %s", uid, exc)
            return web.json_response({"ok": False, "error": str(exc)})

    async def _sync_guild(self, gid):
        g = discord.Object(id=gid)
        try:
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
            log.info("synced slash commands to guild %s", gid)
        except discord.Forbidden:
            log.warning("guild %s: not a member yet — invite the bot.", gid)
        except Exception as exc:
            log.warning("guild %s sync failed: %s", gid, exc)

    async def on_guild_join(self, guild):
        """When the bot is added to a server, register its commands there immediately."""
        log.info("joined guild %s (%s) — syncing commands", guild.id, guild.name)
        await self._sync_guild(guild.id)

    async def _resolve_channel(self, cid):
        """get_channel(), falling back to a REST fetch for channels not yet cached."""
        ch = self.get_channel(cid)
        if ch is None:
            try:
                ch = await self.fetch_channel(cid)
            except Exception as exc:
                log.warning("cannot resolve channel %s: %s", cid, exc)
        return ch

    async def close(self):
        if self._dm_runner:
            await self._dm_runner.cleanup()
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def on_ready(self):
        log.info("logged in as %s (id=%s); in %d guild(s): %s",
                 self.user, self.user.id, len(self.guilds), [g.id for g in self.guilds])
        # Sync commands only to guilds we're actually in (no blind 403/429 storm).
        want = set(GUILD_IDS) if GUILD_IDS else {g.id for g in self.guilds}
        for g in self.guilds:
            if g.id in want:
                await self._sync_guild(g.id)
        if not CONTRIB_CHANNEL_ID:
            log.warning("CONTRIB_CHANNEL_ID unset — contribution cards will not be auto-posted.")

    # --- decision-queue polling (contributions + Tier B remedies — QUEUES below) --------------
    # One poll for every consent queue: the descriptor table keeps the generic core, and a new
    # consent queue is a table row, not a second loop.
    @tasks.loop(seconds=POLL_SECONDS)
    async def poll_queues(self):
        for qkey, q in QUEUES.items():
            chan_id = q["channel_id"]()
            if not chan_id:
                continue
            try:
                data = await hai_get(self.http_session, q["poll_path"],
                                     params={"user": HAI_ADMIN_USER, "status": "pending"})
            except Exception as exc:
                log.warning("%s poll failed: %s", qkey, exc)
                continue
            channel = await self._resolve_channel(chan_id)
            if channel is None:
                continue
            for rec in data.get(q["list_key"], []):
                rid = rec.get("id")
                if not rid or rid in self.posted[qkey]:
                    continue
                try:
                    await channel.send(embed=q["embed"](rec), view=DecisionView(qkey, rid))
                    self.posted[qkey].add(rid)
                    save_posted(self.posted[qkey], q["state_path"])
                    log.info("posted %s card %s", qkey, rid)
                except Exception as exc:
                    log.warning("failed to post %s %s: %s", qkey, rid, exc)

    @poll_queues.before_loop
    async def before_poll(self):
        await self.wait_until_ready()

    # --- Plex status --------------------------------------------------------------
    async def _check_plex(self) -> bool:
        """True if Plex answers /identity. No auth needed for that endpoint."""
        try:
            async with self.http_session.get(PLEX_CHECK_URL,
                                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                return r.status < 500
        except Exception:
            return False

    @tasks.loop(seconds=PLEX_POLL_SECONDS)
    async def poll_plex_status(self):
        log.info("plex tick")
        if not PLEX_CHECK_URL:
            return                       # no Plex configured: nothing to report, never "Plex is down"
        channel = await self._resolve_channel(PLEX_STATUS_CHANNEL_ID)
        if channel is None:
            log.warning("plex-status channel %s unresolved", PLEX_STATUS_CHANNEL_ID)
            return
        up = await self._check_plex()
        emoji = "🟢" if up else "🔴"
        state = "ONLINE" if up else "OFFLINE"
        color = 0x2ECC71 if up else 0xE74C3C
        if up:
            title = f"{emoji} Plex be sailin' the high seas! 🏴‍☠️"
            desc = "The media ship be afloat, matey — hoist the sails an' stream away!"
        else:
            title = f"{emoji} Plex be run aground! ☠️"
            desc = "Blimey! The ol' ship ain't answerin' the call. Hold fast — she'll be back on the water afore long."
        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_footer(text="Last spied from the crow's nest 🔭")
        embed.timestamp = discord.utils.utcnow()

        # Edit the persistent status message, or create it the first time.
        msg_id = None
        try:
            with open(PLEX_MSG_PATH, encoding="utf-8") as fh:
                msg_id = json.load(fh).get("message_id")
        except Exception:
            pass
        try:
            if msg_id:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(embed=embed)
                except discord.NotFound:
                    msg_id = None
            if not msg_id:
                msg = await channel.send(embed=embed)
                try:
                    os.makedirs(STATE_DIR, exist_ok=True)
                    with open(PLEX_MSG_PATH, "w", encoding="utf-8") as fh:
                        json.dump({"message_id": msg.id}, fh)
                except Exception:
                    pass
        except Exception as exc:
            log.warning("plex-status update failed: %s", exc)
            return

        # On a state CHANGE only (rare), flip the channel-name emoji so it shows in
        # the sidebar without opening. Channel renames are rate-limited (2/10min),
        # so we never do this on every poll.
        if self._plex_up is not up:
            self._plex_up = up
            try:
                await channel.edit(name=f"{emoji}plex-status")
            except Exception:
                pass
            log.info("Plex state changed → %s", state)

    @poll_plex_status.before_loop
    async def before_plex(self):
        await self.wait_until_ready()


bot = HawkAdminBot()


# --- admin gate -----------------------------------------------------------------
def is_admin(user, guild) -> bool:
    if user.id in ADMIN_IDS:
        return True
    member = guild.get_member(user.id) if guild else None
    perms = getattr(member, "guild_permissions", None) or getattr(user, "guild_permissions", None)
    return bool(perms and (perms.manage_guild or perms.administrator))


# --- contribution card + buttons ------------------------------------------------
def contrib_embed(rec: dict) -> discord.Embed:
    title = rec.get("title") or "Untitled"
    e = discord.Embed(
        title=f"📥 Corpus contribution: {title}",
        description=(rec.get("content") or "")[:1500],
        color=0x5865F2,
    )
    e.add_field(name="Submitter", value=rec.get("submitter") or "—", inline=True)
    e.add_field(name="Target corpus", value=rec.get("target_namespace") or "—", inline=True)
    e.add_field(name="Submitted", value=rec.get("created_at") or "—", inline=True)
    e.set_footer(text=f"contrib id {rec.get('id','?')}")
    return e


def remedy_embed(rec: dict) -> discord.Embed:
    """Tier B remedy card. The preview names the EXACT artifact (kind/order/host); the embed's
    job is to make the decision legible on a phone in one glance."""
    e = discord.Embed(
        title=f"🔧 Fleet remedy proposed: {rec.get('preview') or rec.get('id')}",
        description=(rec.get("why") or "")[:1000],
        color=0xB8860B,
    )
    e.add_field(name="Proposed by", value=rec.get("proposed_by") or "—", inline=True)
    e.add_field(name="Filed", value=rec.get("created_at") or "—", inline=True)
    e.add_field(name="Safety", value="Re-verified against the deck at yes-time — "
                                     "fires only if the problem is still there.", inline=False)
    e.set_footer(text=f"remedy id {rec.get('id', '?')}")
    return e


def _contrib_finish(action: str, body: dict):
    """(color, title_prefix, note_suffix) for a decided contribution card."""
    if action == "approve":
        fn = (body.get("contribution") or {}).get("result_filename") or "written"
        return 0x2ECC71, "✅ Approved", f"→ `{fn}`"
    return 0xE74C3C, "🗑️ Rejected", ""


def _remedy_finish(action: str, body: dict):
    """Remedies have a third clean outcome: resolved_self — answered, nothing fired."""
    r = body.get("remedy") or {}
    if action == "approve" and r.get("status") == "resolved_self":
        return 0x95A5A6, "🌤 Not fired", (r.get("result") or "the condition cleared itself")[:180]
    if action == "approve":
        return 0x2ECC71, "✅ Approved", (r.get("result") or "executed")[:180]
    return 0xE74C3C, "🗑️ Rejected", ""


# The decision-queue table. The custom_id prefix IS the table key, so cards posted before a bot
# restart (or before this table existed — every live contrib card) keep working: on_interaction
# parses the prefix and looks it up here. (gamereq buttons are NOT a queue — they call the games
# app, not the orchestrator — and keep their own handler below.)
QUEUES = {
    "contrib": {
        "poll_path": "/contributions", "list_key": "contributions",
        "decide_path": "/contributions/{action}", "channel_id": lambda: CONTRIB_CHANNEL_ID,
        "embed": contrib_embed, "finish": _contrib_finish, "state_path": POSTED_PATH,
    },
    "remedy": {
        "poll_path": "/remedies", "list_key": "remedies",
        "decide_path": "/remedies/{action}", "channel_id": lambda: REMEDY_CHANNEL_ID,
        "embed": remedy_embed, "finish": _remedy_finish, "state_path": REMEDY_POSTED_PATH,
    },
}


class DecisionView(discord.ui.View):
    """Persistent-style view: buttons carry queue + record id in their custom_id so decisions
    survive a bot restart (handled in on_interaction below, not via callbacks)."""
    def __init__(self, qkey: str, rid: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Approve", style=discord.ButtonStyle.success,
            custom_id=f"{qkey}:approve:{rid}", emoji="✅"))
        self.add_item(discord.ui.Button(
            label="Reject", style=discord.ButtonStyle.danger,
            custom_id=f"{qkey}:reject:{rid}", emoji="🗑️"))


class GameRequestView(discord.ui.View):
    """Approve/Reject a friend's game-server request. The request id rides in the
    custom_id so a click still works after a bot restart (handled in on_interaction)."""
    def __init__(self, rid: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Approve", style=discord.ButtonStyle.success,
            custom_id=f"gamereq:approve:{rid}", emoji="✅"))
        self.add_item(discord.ui.Button(
            label="Reject", style=discord.ButtonStyle.danger,
            custom_id=f"gamereq:reject:{rid}", emoji="🚫"))


async def _handle_gamereq(interaction: discord.Interaction, custom_id: str):
    """Approve/Reject a game request: verify admin, disable the buttons, call the onboarding app's
    decision endpoint, and reflect the outcome in the DM."""
    try:
        _, action, rid = custom_id.split(":", 2)
    except ValueError:
        return
    if interaction.user.id not in ADMIN_IDS:      # DM has no guild -> gate on the admin id list
        await interaction.response.send_message("⛔ Admins only.", ephemeral=True)
        return
    disabled = discord.ui.View()
    for lbl, sty in (("Approve", discord.ButtonStyle.success), ("Reject", discord.ButtonStyle.danger)):
        disabled.add_item(discord.ui.Button(label=lbl, style=sty, disabled=True))
    try:
        await interaction.response.edit_message(view=disabled)
    except discord.InteractionResponded:
        pass
    decision = "approve" if action == "approve" else "reject"
    try:
        async with bot.http_session.post(
                f"{ONBOARDING_URL}/gameops/request/{rid}/{decision}",
                headers={"x-gameops-token": GAMEOPS_TOKEN},
                timeout=aiohttp.ClientTimeout(total=20)) as r:
            body = await r.json(content_type=None)
    except Exception as exc:
        await interaction.followup.send(f"⚠️ Couldn't reach the games app: {exc}", ephemeral=True)
        return
    result = (body or {}).get("result") or (body or {}).get("already") or "done"
    notes = {"installing": "⬇️ Installing now — it'll appear on the games page shortly.",
             "provisioning": "🔧 Approved — flagged for setup (Claude will provision it).",
             "rejected": "🚫 Rejected.", "done": "Done."}
    note = notes.get(result, f"Result: {result}")
    try:
        await interaction.message.edit(
            content=(interaction.message.content or "") + f"\n\n**{note}**", view=disabled)
    except Exception:
        pass
    await interaction.followup.send(note, ephemeral=True)


@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Only handle our component (button) interactions here; slash commands are
    # dispatched by the CommandTree automatically.
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = (interaction.data or {}).get("custom_id", "")
    if custom_id.startswith("gamereq:"):
        await _handle_gamereq(interaction, custom_id)
        return
    try:
        qkey, action, rid = custom_id.split(":", 2)
    except ValueError:
        return
    q = QUEUES.get(qkey)
    if q is None:
        return

    if not is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Admins only.", ephemeral=True)
        return

    # Double-click guard: if a decision for this record is already in flight, drop the second
    # one entirely (the remedy store also locks server-side; contributions still don't).
    key = (qkey, rid)
    if key in bot._processing:
        await interaction.response.send_message("⏳ Already handling that one — one moment.", ephemeral=True)
        return
    bot._processing.add(key)
    try:
        # ACK the interaction AND disable both buttons in ONE atomic step, so a second
        # click can't fire while the (slow) decision round-trip is in flight.
        disabled = discord.ui.View()
        for lbl, sty in (("Approve", discord.ButtonStyle.success), ("Reject", discord.ButtonStyle.danger)):
            disabled.add_item(discord.ui.Button(label=lbl, style=sty, disabled=True))
        try:
            await interaction.response.edit_message(view=disabled)
        except discord.InteractionResponded:
            pass

        endpoint = "approve" if action == "approve" else "reject"
        try:
            status, body = await hai_post(bot.http_session, q["decide_path"].format(action=endpoint),
                                          {"id": rid, "user": HAI_ADMIN_USER})
        except Exception as exc:
            await interaction.followup.send(f"⚠️ Orchestrator call failed: {exc}", ephemeral=True)
            return

        msg = interaction.message
        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        if status == 200:
            color, prefix, suffix = q["finish"](action, body if isinstance(body, dict) else {})
            embed.color = color
            embed.title = f"{prefix} — {embed.title}"
            note = f"{prefix} by {interaction.user.display_name}" + (f" {suffix}" if suffix else "")
        elif status == 404:
            embed.color = 0x95A5A6
            note = "Already decided / expired / no longer pending."
        else:
            # 409 from a remedy approve = retryable (deck unreadable or execution failed; the
            # yes was NOT consumed) — re-enable the buttons so the human can simply try again.
            detail = body.get("detail") if isinstance(body, dict) else body
            await interaction.followup.send(f"⚠️ {status}: {detail}", ephemeral=True)
            try:
                await interaction.message.edit(view=DecisionView(qkey, rid))
            except Exception:
                pass
            return

        embed.set_footer(text=f"{embed.footer.text or ''} • {note}")
        await interaction.message.edit(embed=embed, view=disabled)
    finally:
        bot._processing.discard(key)


# --- slash commands -------------------------------------------------------------
@bot.tree.command(description="Show your Discord ID and whether HawkAdminBot treats you as an admin.")
async def whoami(interaction: discord.Interaction):
    admin = is_admin(interaction.user, interaction.guild)
    await interaction.response.send_message(
        f"You are **{interaction.user}** (id `{interaction.user.id}`).\n"
        f"Admin: **{'yes' if admin else 'no'}**.", ephemeral=True)


@bot.tree.command(description="List pending HAI corpus contributions (admin only).")
async def contributions(interaction: discord.Interaction):
    if not is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        data = await hai_get(bot.http_session, "/contributions",
                             params={"user": HAI_ADMIN_USER, "status": "pending"})
    except Exception as exc:
        await interaction.followup.send(f"⚠️ Orchestrator call failed: {exc}", ephemeral=True)
        return
    items = data.get("contributions", [])
    if not items:
        await interaction.followup.send("✅ No pending contributions.", ephemeral=True)
        return
    lines = [f"• `{r.get('id')}` — **{r.get('title')}** from {r.get('submitter')} → {r.get('target_namespace')}"
             for r in items]
    await interaction.followup.send("**Pending contributions:**\n" + "\n".join(lines[:20]), ephemeral=True)


@bot.tree.command(description="List this server's active invite links (needs Manage Server).")
async def invites(interaction: discord.Interaction):
    if not is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Admins only.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        invs = await interaction.guild.invites()
    except discord.Forbidden:
        await interaction.followup.send("⚠️ I lack **Manage Server** here.", ephemeral=True)
        return
    if not invs:
        await interaction.followup.send("No active invites.", ephemeral=True)
        return
    lines = []
    for i in invs:
        ch = f"#{i.channel.name}" if i.channel else "?"
        exp = "never" if not i.max_age else f"{i.max_age}s"
        inviter = i.inviter.name if i.inviter else "?"
        lines.append(f"• `{i.code}` → {ch} · uses {i.uses}/{i.max_uses or '∞'} · exp {exp} · by {inviter}")
    await interaction.followup.send("**Active invites:**\n" + "\n".join(lines[:25]), ephemeral=True)


@bot.tree.command(description="Delete an invite by its code (needs Manage Server).")
@app_commands.describe(code="The invite code (the part after discord.gg/)")
async def invite_delete(interaction: discord.Interaction, code: str):
    if not is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Admins only.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    code = code.strip().rsplit("/", 1)[-1]
    try:
        invs = await interaction.guild.invites()
        target = next((i for i in invs if i.code == code), None)
        if target is None:
            await interaction.followup.send(f"No invite `{code}` in this server.", ephemeral=True)
            return
        await target.delete(reason=f"Deleted via HawkAdminBot by {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send("⚠️ I lack **Manage Server** here.", ephemeral=True)
        return
    await interaction.followup.send(f"🗑️ Deleted invite `{code}`.", ephemeral=True)


@bot.tree.command(description="Add or remove a role from a member (needs Manage Roles).")
@app_commands.describe(action="add or remove", member="The member", role="The role")
@app_commands.choices(action=[
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
])
async def role(interaction: discord.Interaction, action: app_commands.Choice[str],
               member: discord.Member, role: discord.Role):
    if not is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if action.value == "add":
            await member.add_roles(role, reason=f"HawkAdminBot by {interaction.user}")
            verb = "Added"
        else:
            await member.remove_roles(role, reason=f"HawkAdminBot by {interaction.user}")
            verb = "Removed"
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ I can't manage that role — I need **Manage Roles** and my top role must be above it.",
            ephemeral=True)
        return
    await interaction.followup.send(f"✅ {verb} **{role.name}** {'to' if verb=='Added' else 'from'} {member.mention}.",
                                    ephemeral=True)


# --- lock EVERY command to admins ------------------------------------------------
# Set Discord-side default_member_permissions = Manage Server on all commands so
# Discord itself HIDES them from non-admin members (they won't even appear in the
# slash-command list). guild_only stops them being used in DMs. The is_admin()
# runtime checks stay as defense-in-depth and still honour the ADMIN_DISCORD_IDS
# allowlist. A server owner can still grant specific roles access per-command in
# Server Settings → Integrations → HawkAdminBot if they ever want to.
_ADMIN_ONLY = discord.Permissions(manage_guild=True)
for _cmd in bot.tree.get_commands():
    _cmd.default_permissions = _ADMIN_ONLY
    _cmd.guild_only = True


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not TOKEN:
        log.error("DISCORD_TOKEN is not set — the bot is staged but cannot log in. "
                  "Set DISCORD_TOKEN in the container's environment (its env file) and restart.")
        sys.exit(0)
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
