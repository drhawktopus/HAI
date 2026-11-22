"""Tests must not run against the live order desk. tablet_foundry defaults to live state, so ensure TABLET_INBOX, TABLET_OUTBOX, and TABLET_FOUNDRY_ROOT are set to scratch directories before execution to prevent accidental writes to production data."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as tf

LIVE_ROOTS = ("/ssdshared", "/tabfoundry", "/mnt/ssdshared", "/opt/arrstack")


def is_live(path):
    """Same verdict on Linux and Windows: slashes normalised, a drive letter dropped (E:\\SSDShared = /ssdshared)."""
    p = re.sub(r"^[a-z]:", "", path.replace("\\", "/").lower())
    return any(p == r or p.startswith(r + "/") for r in LIVE_ROOTS)


class GateIsolation(unittest.TestCase):
    def test_the_matcher_sees_every_live_default(self):
        # known positives: the module's own defaults, verbatim (tablet_foundry: INBOX, OUTBOX, STATE_DIR, ROOT)
        for p in ("/ssdshared/tabfoundry-jobs/inbox", "/ssdshared/tabfoundry-jobs/outbox", "/tabfoundry/state",
                  "/tabfoundry"):
            self.assertTrue(is_live(p), p)
        for p in ("/tmp/gate-outbox", r"C:\Users\x\AppData\Local\Temp\tolkien-gate\outbox"):
            self.assertFalse(is_live(p), p)

    def test_no_store_is_live(self):
        for name in ("INBOX", "OUTBOX", "ROOT", "STATE_DIR"):
            self.assertFalse(is_live(getattr(tf, name)),
                             "tf.%s = %s is the LIVE store: set TABLET_INBOX / TABLET_OUTBOX / TABLET_FOUNDRY_ROOT"
                             % (name, getattr(tf, name)))


if __name__ == "__main__":
    unittest.main()
