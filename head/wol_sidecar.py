"""Tiny Wake-on-LAN sidecar (stdlib only).

Requires `network_mode: host` so broadcasts originate on the physical LAN
(bridge networks cannot reach LAN broadcast addresses). The orchestrator
triggers wake via GET /wake?mac=...."""
import os
import socket
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

PORT = 9119
# The sidecar does not import config, so it reads its own env with the same name and the same
# neutral default: the limited broadcast works on any LAN without naming a subnet.
BROADCASTS = [b.strip() for b in os.getenv("WOL_SIDECAR_BROADCASTS", "255.255.255.255").split(",") if b.strip()]
WOL_PORT = 9


def send_magic(mac):
    raw = mac.replace(":", "").replace("-", "").strip()
    if len(raw) != 12:
        raise ValueError("bad mac")
    pkt = b"\xff" * 6 + bytes.fromhex(raw) * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sent = []
    try:
        for b in BROADCASTS:
            try:
                s.sendto(pkt, (b, WOL_PORT)); sent.append(b)
            except Exception:
                pass
    finally:
        s.close()
    return sent


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            self._json(200, {"ok": True})
            return
        if u.path != "/wake":
            self._json(404, {"error": "not found"})
            return
        mac = parse_qs(u.query).get("mac", [""])[0]
        try:
            sent = send_magic(mac)
            self._json(200, {"sent_to": sent, "mac": mac})
        except Exception as e:
            self._json(400, {"error": str(e)})

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
