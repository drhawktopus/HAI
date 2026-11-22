"""Speculative winners release an order without waiting for the losing transport."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_fulfil


def _map_spec(name="spec-release"):
    return {
        "kind": "map",
        "name": name,
        "batch": 1,
        "payload": "/x/map_echo.py",
        "items": ["alpha", "beta"],
        "control": {"item": "control", "expect": "echo:control"},
    }


class SpeculativeReleaseTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.release_slow = threading.Event()
        self.slow_started = threading.Event()
        self.calls = []
        with tablet_fulfil._ORPHAN_LOCK:
            tablet_fulfil._ORPHAN_THREADS.clear()

        self.patchers = [
            mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp),
            mock.patch.object(tablet_fulfil, "SPECULATIVE_FACTOR", 0.0),
            mock.patch.object(tablet_fulfil, "SPECULATIVE_MIN_S", 0.0),
            mock.patch.object(tablet_fulfil, "SPECULATIVE_CLEANUP_GRACE_S", 0.01),
            mock.patch.object(tablet_fulfil, "_ssh", self._ssh),
            mock.patch.object(tablet_fulfil, "_scp", self._scp),
            mock.patch.object(tablet_fulfil, "_scp_from", self._scp_from),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        self.release_slow.set()
        deadline = time.time() + 2
        while time.time() < deadline:
            available, quarantined = tablet_fulfil._available_hosts(["slow", "fast"])
            if not quarantined:
                break
            time.sleep(0.01)
        for patcher in reversed(self.patchers):
            patcher.stop()
        with tablet_fulfil._ORPHAN_LOCK:
            tablet_fulfil._ORPHAN_THREADS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ssh(self, host, cmd, timeout=0):
        self.calls.append((host, cmd))
        if host == "slow" and " python " in (" " + cmd + " "):
            self.slow_started.set()
            self.release_slow.wait(2)
            return 124, "late timeout"
        return 0, ""

    @staticmethod
    def _scp(host, src, dst, timeout=0):
        return 0, ""

    @staticmethod
    def _scp_from(host, remote, local, timeout=0):
        wd = os.path.dirname(local)
        with open(os.path.join(wd, "items.txt"), encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip()]
        with open(local, "w", encoding="utf-8") as f:
            json.dump([{"item": name, "result": "echo:%s" % name} for name in names], f)
        return 0, ""

    def test_winner_releases_order_and_quarantines_loser(self):
        started = time.time()
        rows, errors, _wall, expected, unclaimed = tablet_fulfil.run_order(
            _map_spec(), ["slow", "fast"])
        elapsed = time.time() - started

        self.assertTrue(self.slow_started.is_set(), "the original slow attempt must really start")
        self.assertLess(elapsed, 1.0, "the obsolete transport must not hold the order open")
        self.assertEqual(expected, 3)
        self.assertEqual(unclaimed, [])
        self.assertEqual(errors, {})
        self.assertEqual({row["item"] for row in rows}, {"alpha", "beta", "control"})
        self.assertEqual(len(rows), 3, "the speculative result must be accepted exactly once")
        setup_commands = [cmd for _host, cmd in self.calls if cmd.startswith("rm -rf ")]
        self.assertTrue(setup_commands)
        self.assertFalse(any(cmd.startswith("rm -rf tfoundry_stage;")
                             for cmd in setup_commands),
                         "an attempt must never clear the shared staging root")

        available, quarantined = tablet_fulfil._available_hosts(["slow", "fast"])
        self.assertEqual(available, ["fast"])
        self.assertEqual(quarantined, ["slow"])

        # A following order proceeds on the clean host and never overlaps the unresolved loser.
        next_rows, next_errors, _wall, next_expected, next_unclaimed = tablet_fulfil.run_order(
            _map_spec("spec-next"), ["slow", "fast"])
        self.assertEqual(next_errors, {})
        self.assertEqual(next_unclaimed, [])
        self.assertEqual(len(next_rows), next_expected)
        self.assertEqual({row["_host"] for row in next_rows}, {"fast"})

        # When the loser finally exits, its timeout neither mutates errors nor duplicates rows.
        self.release_slow.set()
        deadline = time.time() + 2
        while time.time() < deadline:
            _available, still_quarantined = tablet_fulfil._available_hosts(["slow", "fast"])
            if not still_quarantined:
                break
            time.sleep(0.01)
        self.assertEqual(still_quarantined, [])
        self.assertEqual(errors, {})
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
