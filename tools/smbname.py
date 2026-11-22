"""Resolve a Windows host's computer name via unauthenticated SMB negotiation.

Modern Windows disables NetBIOS-over-TCP, and local DNS may lack PTR records,
making standard discovery unreliable. This tool exploits the SMB2 protocol:
during the NTLM handshake, the server sends its TargetInfo (including machine
name) in the CHALLENGE message before any credentials are provided.

Usage:
    python smbname.py <ip>
    python smbname.py --control          # Verify functionality against known host

Note: A negative result is ambiguous. Always run --control first to confirm
the instrument works and the target responds, otherwise "no name" could mean
either a tool failure or a non-Windows/non-SMB host.

The control hosts are the fleet table (FLEET_HOSTS); with no fleet configured there is no
control to run, and the tool says so rather than assuming a host.
"""
import socket
import struct
import sys

import lab_settings as S

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

AV_PAIRS = {1: "NetBIOS computer name", 2: "NetBIOS domain name",
            3: "DNS computer name", 4: "DNS domain name", 5: "DNS tree name"}


def smb2_header(command, message_id):
    return (b"\xfeSMB" + struct.pack("<HH", 64, 0) + struct.pack("<I", 0)
            + struct.pack("<HH", command, 31) + struct.pack("<I", 0) + struct.pack("<I", 0)
            + struct.pack("<Q", message_id) + struct.pack("<I", 0) + struct.pack("<I", 0)
            + struct.pack("<Q", 0) + b"\x00" * 16)


def wrap(payload):
    """NetBIOS session service framing: 0x00 + 24-bit big-endian length."""
    return b"\x00" + len(payload).to_bytes(3, "big") + payload


def recv_msg(sock):
    head = b""
    while len(head) < 4:
        chunk = sock.recv(4 - len(head))
        if not chunk:
            raise ConnectionError("closed during NetBIOS header")
        head += chunk
    want = int.from_bytes(head[1:4], "big")
    body = b""
    while len(body) < want:
        chunk = sock.recv(want - len(body))
        if not chunk:
            raise ConnectionError("closed during body")
        body += chunk
    return body


def negotiate_request():
    body = (struct.pack("<HHHH", 36, 2, 1, 0) + struct.pack("<I", 0)
            + b"\x00" * 16 + struct.pack("<Q", 0)
            + struct.pack("<HH", 0x0202, 0x0210))
    return wrap(smb2_header(0x0000, 0) + body)


def ntlm_negotiate():
    # UNICODE | OEM | REQUEST_TARGET | NTLM | ALWAYS_SIGN | EXTENDED_SESSIONSECURITY
    flags = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000200 | 0x00008000 | 0x00080000
    return (b"NTLMSSP\x00" + struct.pack("<I", 1) + struct.pack("<I", flags)
            + struct.pack("<HHI", 0, 0, 32) + struct.pack("<HHI", 0, 0, 32))


def session_setup_request(token):
    body = (struct.pack("<HBB", 25, 0, 1) + struct.pack("<I", 0) + struct.pack("<I", 0)
            + struct.pack("<HH", 88, len(token)) + struct.pack("<Q", 0) + token)
    return wrap(smb2_header(0x0001, 1) + body)


def parse_challenge(blob):
    at = blob.find(b"NTLMSSP\x00")
    if at < 0:
        return None, "no NTLMSSP CHALLENGE in the response"
    msg = blob[at:]
    if struct.unpack("<I", msg[8:12])[0] != 2:
        return None, "NTLMSSP message was not a CHALLENGE"
    ti_len, _, ti_off = struct.unpack("<HHI", msg[40:48])
    info, out = msg[ti_off:ti_off + ti_len], {}
    i = 0
    while i + 4 <= len(info):
        av_id, av_len = struct.unpack("<HH", info[i:i + 4])
        if av_id == 0:
            break
        if av_id in AV_PAIRS:
            out[AV_PAIRS[av_id]] = info[i + 4:i + 4 + av_len].decode("utf-16-le", "replace")
        i += 4 + av_len
    return out, None


def smbname(ip, port=445, timeout=6):
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(negotiate_request())
            recv_msg(s)
            s.sendall(session_setup_request(ntlm_negotiate()))
            return parse_challenge(recv_msg(s))
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}"


def report(ip, label=""):
    names, err = smbname(ip)
    tag = f"{ip}{'  (' + label + ')' if label else ''}"
    if names:
        print(f"  {tag}")
        for k, v in names.items():
            print(f"      {k:<22} {v}")
        return True
    print(f"  {tag}\n      -- no name: {err}")
    return False


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fleet = S.fleet_hosts()
    if "--control" in sys.argv or not args:
        if not fleet:
            print("no fleet configured (FLEET_HOSTS empty): no control host to validate against.")
            sys.exit(2)
        print("POSITIVE CONTROL (hosts whose names we already know):")
        ok = sum(report(ip, lbl) for lbl, ip in fleet.items())
        print(f"\n  control: {ok}/{len(fleet)} answered with a name.")
        if not ok:
            print("  ⚠ INSTRUMENT IS BLIND -- a negative below would mean nothing. Stop here.")
        print()
    if args:
        print("TARGETS:")
        for ip in args:
            report(ip)
