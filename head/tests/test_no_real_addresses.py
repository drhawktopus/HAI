"""A head's code carries no real network address: every box, share and NIC is a setting.

The rule checked here is the publish check's: a literal is a real address when it is a LAN address in
192.168.x.x, a Tailscale address in the 100.64/10 range, or a MAC of six hex pairs joined by ':'
or '-' (excluding the reserved ff:ff..., 01:00:5e..., 33:33... and 00:00:00... groups). The publish
check also refuses the lab's own public hosts; it stays home with them. Only non-test .py files under
the orchestrator root are scanned; tests/ may name fixtures, and the mirrored sample copy under
coding-runner/ is a separate workspace.

There is no allow-list: any address in the head's code fails the gate. The matcher is proved against
known positives, built at run time so this file holds no address of its own, before it is trusted to
report nothing.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SKIP_DIRS = {"__pycache__", "backups", "workspaces", ".git", ".pytest_cache", "tests", "coding-runner"}

IP_PATTERNS = (
    ("lan", re.compile(r"192\.168\.\d{1,3}\.\d{1,3}")),
    ("tailscale", re.compile(r"100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}")),
)
MAC = re.compile(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}")
MAC_RESERVED = ("ff:ff", "01:00:5e", "33:33", "00:00:00")


def _addr(*parts):
    """A dotted address assembled at run time, so the controls below never write one into this file."""
    return ".".join(str(p) for p in parts)


def _mac(sep, *pairs):
    return sep.join(pairs)


def _mac_is_reserved(text):
    norm = text.replace("-", ":").lower()
    return any(norm.startswith(p) for p in MAC_RESERVED)


def find_addresses(text):
    """[(kind, literal) for every real address in `text`, in order."""
    out = []
    for kind, rx in IP_PATTERNS:
        for m in rx.finditer(text):
            out.append((kind, m.group(0)))
    for m in MAC.finditer(text):
        if not _mac_is_reserved(m.group(0)):
            out.append(("mac", m.group(0)))
    return out


def scan(root=ROOT):
    """[(relpath, line, literal) for every real address in the head's non-test Python."""
    hits = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(d, f)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                for _kind, literal in find_addresses(line):
                    hits.append((rel, line_no, literal))
    return hits


class NoRealAddresses(unittest.TestCase):
    def test_the_matcher_sees_a_known_positive(self):
        """Prove the detector can detect before trusting its silences."""
        lan = _addr(192, 168, 0, 1)
        hits = find_addresses("GET http://%s/api/4/gpu" % lan)
        self.assertEqual([h for h in hits if h[1] == lan], [("lan", lan)])

    def test_the_matcher_sees_the_ranges_it_must_see(self):
        want = {_addr(192, 168, 12, 9), _addr(100, 64, 0, 1), _addr(100, 127, 255, 255),
                _mac("-", "8C", "A6", "00", "00", "00", "01"), _mac(":", "aa", "bb", "cc", "dd", "ee", "ff")}
        lits = {lit for _k, lit in find_addresses("  ".join(sorted(want)))}
        self.assertEqual(lits, want)

    def test_reserved_mac_groups_are_not_devices(self):
        sample = " ".join((_mac(":", *["ff"] * 6), _mac(":", "01", "00", "5e", "00", "00", "00"),
                           _mac(":", "33", "33", "00", "00", "00", "00")))
        self.assertEqual(find_addresses(sample), [])

    def test_the_head_codes_carry_no_real_address(self):
        found = scan()
        self.assertEqual(found, [],
                         "real network addresses are written in the code (put them in config.py "
                         "settings read from the environment, with a neutral default):\n  "
                         + "\n  ".join("%s:%s  %s" % (rel, line, lit) for rel, line, lit in found))

    def test_the_tablet_roster_carries_no_address_or_serial(self):
        """The head places by speed and size; Tolkien holds each tablet's address and serial."""
        import sys
        sys.path.insert(0, ROOT)
        import scheduler
        for name, tab in scheduler.TABLETS.items():
            self.assertNotIn("ip", tab, name)
            self.assertNotIn("serial", tab, name)


if __name__ == "__main__":
    for rel, line, lit in scan():
        print("%s:%s  %s" % (rel, line, lit))
    unittest.main()
