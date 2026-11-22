"""The Fellowship tablets: the one roster every fellowship tool reads, from the file the FELLOWSHIP_ROSTER setting names
(roster.example.json shows its shape). Each tablet has an address and an adb serial, plus the fields some tools use:
usb_octet (its address on the USB segment), class (A, B or C by capability) and battery_switch (the file that turns
charging on and off). The settings module holds the reader (lab_settings.fellowship_roster and tablet_hosts); this
module refuses when no roster is set, which is what a fellowship tool wants.

    python roster.py --sh-lan      the tablets reachable over the network, as a bash associative-array body
    python roster.py --sh-serials  every tablet's adb serial, the same way
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# The lab's tool folder beside this one: bin in the lab's own tree, tools in the published one.
LAB_BIN = next((os.path.normpath(c) for c in (os.path.join(_HERE, "..", "bin"), os.path.join(_HERE, "..", "tools"),
                                             os.path.join(_HERE, ".."))
                if os.path.exists(os.path.join(c, "lab_settings.py"))), "")
if LAB_BIN:
    sys.path.insert(0, LAB_BIN)
import lab_settings as S  # noqa: E402


def tablets():
    """{name: {address, serial, ...}} from the roster; refuses when no roster is set."""
    t = S.fellowship_roster()
    if not t:
        raise SystemExit("set FELLOWSHIP_ROSTER to the fellowship roster file (roster.example.json shows its shape)")
    return t


def hosts():
    """name -> network address (the roster minus the tablets only on the USB segment); refuses when no roster is set."""
    tablets()
    return S.tablet_hosts()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--sh-lan"]:
        tablets()
        print(" ".join("[%s]=%s" % (n, a) for n, a in sorted(S.tablet_hosts().items())))
    elif sys.argv[1:2] == ["--sh-serials"]:
        print(" ".join("[%s]=%s" % (n, t["serial"]) for n, t in sorted(tablets().items()) if t.get("serial")))
    else:
        print(__doc__)
