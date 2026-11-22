#!/usr/bin/env python3
"""
discord_admin.py — drive Discord through HawkAdminBot's token (REST only).
================================================================================
A reusable admin toolkit so the *bot* performs structural Discord actions
(inspect / create / move / rename channels + categories, set visibility, read
channels) instead of a human clicking through the web app. REST-only: no gateway
connection, so each call is fast and scriptable. stdlib only (urllib) → runs on
the host or inside the container with no extra deps.

Token: taken from $HAWKADMIN_DISCORD_TOKEN, else parsed from $HAWKADMIN_ENV_FILE or a .env beside this script.
Never pass the token on the command line.

Examples:
  python3 discord_admin.py channels <guild_id>
  python3 discord_admin.py read <channel_id> --limit 8
  python3 discord_admin.py mkcat <guild_id> "🔒 Backend" --private
  python3 discord_admin.py mkchan <guild_id> hai-approvals --parent <cat_id> --sync
  python3 discord_admin.py edit <channel_id> --name uptime --parent <cat_id>
  python3 discord_admin.py visibility <channel_id> --guild <gid> --public|--private
  python3 discord_admin.py syncperms <channel_id>
"""
import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error

API = "https://discord.com/api/v10"

# Channel types
TEXT, DM, VOICE, GROUP, CATEGORY, NEWS = 0, 1, 2, 3, 4, 5
TYPE_NAME = {0: "text", 2: "voice", 4: "CATEGORY", 5: "news", 13: "stage", 15: "forum"}

# Permission bits
VIEW_CHANNEL = 1 << 10   # 1024
SEND_MESSAGES = 1 << 11  # 2048


def get_token() -> str:
    t = os.environ.get("HAWKADMIN_DISCORD_TOKEN")
    if t:
        return t.strip()
    # the file fallback: HAWKADMIN_ENV_FILE (the stack's env file), else a .env beside this script
    for path in (os.environ.get("HAWKADMIN_ENV_FILE", ""), os.path.join(os.path.dirname(__file__), ".env")):
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                for ln in fh:
                    if ln.startswith("HAWKADMIN_DISCORD_TOKEN="):
                        v = ln.split("=", 1)[1].strip()
                        if v:
                            return v
        except FileNotFoundError:
            continue
    sys.exit("ERROR: no HAWKADMIN_DISCORD_TOKEN in the environment, HAWKADMIN_ENV_FILE or a .env beside this script")


TOKEN = get_token()


def req(method: str, path: str, body=None):
    """One REST call, with basic 429 retry. Returns (status, parsed_json_or_text)."""
    url = API + path
    for attempt in range(5):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(url, data=data, method=method)
        r.add_header("Authorization", f"Bot {TOKEN}")
        r.add_header("Content-Type", "application/json")
        r.add_header("User-Agent", "HawkAdminBot-admin/1.0 (homelab)")
        try:
            with urllib.request.urlopen(r) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw.decode(errors="replace")
            if e.code == 429:
                retry = 1.0
                if isinstance(parsed, dict):
                    retry = float(parsed.get("retry_after", 1.0))
                time.sleep(retry + 0.25)
                continue
            return e.code, parsed
    return 429, {"message": "rate limited after retries"}


# ---- commands ------------------------------------------------------------------
def cmd_channels(a):
    st, ch = req("GET", f"/guilds/{a.guild}/channels")
    if st != 200:
        print(st, ch)
        return
    byid = {c["id"]: c for c in ch}

    def sort_key(c):
        parent = c.get("parent_id") or ""
        # categories first within their own grouping
        return (parent, 0 if c["type"] == CATEGORY else 1, c.get("position", 0))

    for c in sorted(ch, key=sort_key):
        t = TYPE_NAME.get(c["type"], str(c["type"]))
        parent = byid.get(c.get("parent_id"), {}).get("name", "-") if c.get("parent_id") else "-"
        # private if @everyone has VIEW_CHANNEL denied
        priv = ""
        for o in c.get("permission_overwrites", []):
            if o["id"] == a.guild and (int(o.get("deny", 0)) & VIEW_CHANNEL):
                priv = " 🔒"
        print(f'{c["id"]}  [{t:8}] {c["name"]:<26} parent={parent}{priv}')


def cmd_read(a):
    st, msgs = req("GET", f"/channels/{a.channel}/messages?limit={a.limit}")
    if st != 200:
        print(st, msgs)
        return
    for m in reversed(msgs):
        who = m["author"]["username"]
        c = (m.get("content") or "").replace("\n", " ")
        extra = ""
        for e in m.get("embeds", []):
            extra += f' | E:{e.get("title","")}: {(e.get("description") or "")[:80]}'
            for f in e.get("fields", [])[:4]:
                extra += f' [{f["name"]}={str(f["value"])[:40]}]'
        print(f'[{who}] {c[:120]}{extra}')


def cmd_mkcat(a):
    body = {"name": a.name, "type": CATEGORY}
    if a.private:
        body["permission_overwrites"] = [{"id": a.guild, "type": 0, "deny": str(VIEW_CHANNEL)}]
    st, c = req("POST", f"/guilds/{a.guild}/channels", body)
    print(st, c.get("id") if isinstance(c, dict) else c)


def cmd_mkchan(a):
    body = {"name": a.name, "type": VOICE if a.voice else TEXT}
    if a.parent:
        body["parent_id"] = a.parent
    if a.topic:
        body["topic"] = a.topic
    st, c = req("POST", f"/guilds/{a.guild}/channels", body)
    if st in (200, 201) and isinstance(c, dict):
        cid = c["id"]
        print(st, cid)
        if a.sync and a.parent:
            # inherit the category's permission overwrites (private/public follows parent)
            s2, _ = req("PATCH", f"/channels/{cid}", {"lock_permissions": True})
            print("  sync-perms:", s2)
    else:
        print(st, c)


def cmd_edit(a):
    body = {}
    if a.name:
        body["name"] = a.name
    if a.parent:
        body["parent_id"] = a.parent
    if a.topic is not None:
        body["topic"] = a.topic
    if a.position is not None:
        body["position"] = a.position
    st, c = req("PATCH", f"/channels/{a.channel}", body)
    print(st, "ok" if st == 200 else c)


def cmd_syncperms(a):
    st, c = req("PATCH", f"/channels/{a.channel}", {"lock_permissions": True})
    print(st, "ok" if st == 200 else c)


def cmd_visibility(a):
    """Make a channel public (allow @everyone VIEW) or private (deny it)."""
    ov_id = a.guild  # @everyone role id == guild id
    if a.public:
        body = {"type": 0, "allow": str(VIEW_CHANNEL), "deny": "0"}
    else:
        body = {"type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}
    st, c = req("PUT", f"/channels/{a.channel}/permissions/{ov_id}", body)
    print(st, "ok" if st in (200, 204) else c)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Drive Discord via HawkAdminBot token (REST).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("channels", help="list a guild's channels/categories")
    s.add_argument("guild")
    s.set_defaults(func=cmd_channels)

    s = sub.add_parser("read", help="read recent messages of a channel")
    s.add_argument("channel")
    s.add_argument("--limit", type=int, default=8)
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("mkcat", help="create a category")
    s.add_argument("guild")
    s.add_argument("name")
    s.add_argument("--private", action="store_true", help="deny @everyone view")
    s.set_defaults(func=cmd_mkcat)

    s = sub.add_parser("mkchan", help="create a text/voice channel")
    s.add_argument("guild")
    s.add_argument("name")
    s.add_argument("--parent", help="category id")
    s.add_argument("--topic")
    s.add_argument("--voice", action="store_true")
    s.add_argument("--sync", action="store_true", help="inherit parent category perms")
    s.set_defaults(func=cmd_mkchan)

    s = sub.add_parser("edit", help="rename / move / set topic / reorder a channel")
    s.add_argument("channel")
    s.add_argument("--name")
    s.add_argument("--parent")
    s.add_argument("--topic")
    s.add_argument("--position", type=int)
    s.set_defaults(func=cmd_edit)

    s = sub.add_parser("syncperms", help="sync a channel's perms to its category")
    s.add_argument("channel")
    s.set_defaults(func=cmd_syncperms)

    s = sub.add_parser("visibility", help="make channel public/private to @everyone")
    s.add_argument("channel")
    s.add_argument("--guild", required=True, help="guild id (== @everyone role id)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--public", action="store_true")
    g.add_argument("--private", action="store_true")
    s.set_defaults(func=cmd_visibility)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
