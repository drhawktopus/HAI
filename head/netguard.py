"""ONE rule for "is this host on the public internet", shared by every door HAI has to the web.

`lanfetch` (fetch_url, and the fetch-to-file tool) asks it before each hop; the coding runner's headless browser asks it
for EVERY request a rendered page makes (coding-runner/render.py), so a page a job wrote cannot reach the home network
or the tailnet through its own scripts. Pure standard library and no config import: the runner image copies this file
in beside stream_clip.py, so the two sides cannot drift.

A host is public only when every address it resolves to is a global internet address: no private, loopback, link-local
or CGNAT (tailnet) address, and no local name.
"""
import ipaddress
import socket

LAN_SUFFIXES = (".lan", ".local", ".localhost", ".internal", ".home.arpa", ".ts.net")


def not_public(ip):
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global


def public_refusal(host):
    """None when every address `host` resolves to is a public internet address, else why not."""
    h = (host or "").lower().rstrip(".")
    if not h:
        return "no host"
    try:
        literal = ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        return None if not not_public(literal) else f"{h} is not a public internet address"
    if "." not in h or h.endswith(LAN_SUFFIXES):
        return f"{h} is a local name"
    try:
        infos = socket.getaddrinfo(h, None)
    except Exception as exc:
        return f"{h} did not resolve ({exc})"
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return f"{h} did not resolve"
    private = sorted(a for a in addrs if not_public(ipaddress.ip_address(a.split("%")[0])))
    if private:
        return f"{h} resolves to a non-public address ({', '.join(private)})"
    return None
