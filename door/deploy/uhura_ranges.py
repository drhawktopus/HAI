#!/usr/bin/env python3
"""Refresh the droplet's list of addresses allowed to reach Uhura's tool and token endpoints.

OpenAI says its connector ranges change, so this runs daily from a systemd timer. A failed fetch or
an invalid result leaves the previous list in force and exits non-zero, so the failure shows in the
unit's status. Anthropic's range is from its connector documentation.
"""
import ipaddress
import json
import os
import subprocess
import sys
import time
import urllib.request

ANTHROPIC = ["160.79.104.0/21"]
OPENAI_URL = "https://openai.com/chatgpt-connectors.json"
TARGET = "/etc/caddy/uhura-ranges.caddy"
CADDYFILE = "/etc/caddy/Caddyfile"


def fetch_openai():
    req = urllib.request.Request(OPENAI_URL, headers={"User-Agent": "uhura-ranges"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    prefixes = []
    for row in data["prefixes"]:
        value = row.get("ipv4Prefix") or row.get("ipv6Prefix")
        if value:
            prefixes.append(str(ipaddress.ip_network(value)))
    if not prefixes:
        raise ValueError("zero OpenAI prefixes: refusing to replace the list")
    return prefixes


def main():
    openai = fetch_openai()
    for net in openai:
        if ipaddress.ip_network(net).prefixlen < 8:
            raise ValueError("implausibly wide OpenAI prefix: " + net)
    body = "@vendors remote_ip " + " ".join(sorted(set(ANTHROPIC + openai))) + "\n"
    old = open(TARGET, encoding="utf-8").read() if os.path.exists(TARGET) else None
    if body == old:
        print("unchanged", len(openai), "OpenAI prefixes")
        return
    backup = None
    if old is not None:
        backup = old
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(body)
    check = subprocess.run(["caddy", "validate", "--config", CADDYFILE, "--adapter", "caddyfile"], capture_output=True)
    if check.returncode:
        if backup is not None:
            with open(TARGET, "w", encoding="utf-8") as f:
                f.write(backup)
        raise RuntimeError("caddy rejected the new list; previous list restored")
    subprocess.run(["systemctl", "reload", "caddy"], check=True)
    print(time.strftime("%FT%TZ", time.gmtime()), "updated", len(openai), "OpenAI prefixes")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("uhura-ranges FAILED:", type(exc).__name__, str(exc)[:300], flush=True)
        sys.exit(1)
