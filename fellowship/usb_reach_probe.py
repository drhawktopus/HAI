#!/usr/bin/env python3
"""On the tablet head: report which tablets answer on the USB segment, as one line the caller parses:

    USB_REACH <octet> <octet> ...      (an empty list = none answer)

Tolkien's tablet watchdog runs this over ssh, as a forced command for its key, before it calls the fleet unreachable,
so a WiFi or DNS outage with the USB link healthy is not read as the tablets being gone. The segment is local to the
head, which is why this runs there.

It needs no roster. The segment comes from the head's own bridge address (FELLOWSHIP_USB_BRIDGE, default br0), or
FELLOWSHIP_USB_SEGMENT when the bridge has none, and every address on it but the head's own is tried at once on the
tablets' sshd port (FELLOWSHIP_USB_PORT, default 8022): an open sshd proves a usable node, an ICMP reply does not.
When it cannot tell what the segment is it prints USB_PROBE_ERROR and exits 2; that line deliberately does not start
with USB_REACH, so the caller reads it as inconclusive, never as "no tablet answered".

    python3 usb_reach_probe.py [timeout_seconds]
"""
import concurrent.futures
import os
import re
import socket
import subprocess
import sys

PORT = int(os.environ.get("FELLOWSHIP_USB_PORT", "8022"))
BRIDGE = os.environ.get("FELLOWSHIP_USB_BRIDGE", "br0")
TIMEOUT = float(sys.argv[1]) if len(sys.argv) > 1 and re.fullmatch(r"[0-9]+(\.[0-9]+)?", sys.argv[1]) else 2.0


def segment():
    """(first three octets, the head's own last octet) of the USB segment; (None, None) when it cannot be read."""
    named = os.environ.get("FELLOWSHIP_USB_SEGMENT", "").strip().rstrip(".")
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", BRIDGE], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    m = re.search(r"\binet (\d+\.\d+\.\d+)\.(\d+)/", out)
    if m and (not named or named == m.group(1)):
        return m.group(1), int(m.group(2))
    return (named or None), None


def answers(address):
    try:
        with socket.create_connection((address, PORT), timeout=TIMEOUT):
            return True
    except OSError:
        return False


def main():
    seg, own = segment()
    if not seg:
        print("USB_PROBE_ERROR no address on %s and FELLOWSHIP_USB_SEGMENT is not set" % BRIDGE)
        return 2
    octets = [o for o in range(1, 255) if o != own]
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        reached = list(pool.map(lambda o: answers("%s.%d" % (seg, o)), octets))
    print("USB_REACH " + " ".join(str(o) for o, ok in zip(octets, reached) if ok))
    return 0


if __name__ == "__main__":
    sys.exit(main())
