#!/usr/bin/env python3
"""Disable TX offload (gso/sg/tso/txcsum) on a tablet's rndis0, via the legacy ethtool ioctl.

Runs ON the tablet (Termux python3, as root over adb). Android GSI ships no `ethtool` binary, so
the SIOCETHTOOL ioctl is issued directly.

WHY: the mt6765 RNDIS gadget (class A: gandalf, aragorn) comes up with gso/sg ON after every gadget
build, and with them on the frames the TABLET sends reach rigby with WRONG TCP checksums. Small frames
pass, so the node pings and completes a TCP handshake, then ssh stalls at the key exchange. Turning
gso/sg off fixes the wire. tso/txcsum SET usually returns EOPNOTSUPP; expected, only gso/sg matter.

    python3 usb_offload_off.py rndis0            set gso/sg off, print before/after
    python3 usb_offload_off.py rndis0 --check    read only: prints `offload: on|off`

Shipped in the Tolkien image (tablet_control pushes it on every status/restore) and used by
Lab\\fellowship\\usbnet_up.py; this file is the one copy.
"""
import ctypes, fcntl, socket, struct, sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
CHECK = "--check" in sys.argv[1:]
IFACE = (args[0] if args else "rndis0").encode()
SIOCETHTOOL = 0x8946
# legacy ethtool_value ioctls: (name, GET cmd, SET cmd)
FEATS = [("tso", 0x1e, 0x1f), ("gso", 0x23, 0x24), ("sg", 0x18, 0x19), ("txcsum", 0x16, 0x17)]
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def eth(cmd, data=0):
    buf = ctypes.create_string_buffer(struct.pack("II", cmd, data), 8)
    ifr = ctypes.create_string_buffer(struct.pack("16sQ", IFACE, ctypes.addressof(buf)), 24)
    fcntl.ioctl(s.fileno(), SIOCETHTOOL, ifr, True)
    return struct.unpack("II", ctypes.string_at(ctypes.addressof(buf), 8))[1]


def get(name, gcmd):
    try:
        return eth(gcmd)
    except Exception as e:
        return "ERR:%s" % (type(e).__name__)


if CHECK:
    now = {n: get(n, g) for (n, g, _s) in FEATS if n in ("gso", "sg")}
    if any(isinstance(v, str) for v in now.values()):
        print("offload: ? %s" % now)
    else:
        print("offload: %s %s" % ("on" if any(now.values()) else "off", now))
    sys.exit(0)

print("iface", IFACE.decode())
print("before:", {n: get(n, g) for (n, g, _s) in FEATS})
# dependency order: tso/gso/sg before txcsum
for (n, g, scmd) in FEATS:
    try:
        eth(scmd, 0)
        print("set %s off: ok" % n)
    except Exception as e:
        print("set %s off: ERR %s %s" % (n, type(e).__name__, e))
print("after:", {n: get(n, g) for (n, g, _s) in FEATS})
