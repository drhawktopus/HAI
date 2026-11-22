"""A stop slip ends the batch in flight on the tablet."""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_fulfil


class StopEndsTheBatchInFlight(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.killed = threading.Event()
        self.started = threading.Event()
        self.kills = []
        self.stop = threading.Event()
        for p in (mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp),
                  mock.patch.object(tablet_fulfil, "SPECULATIVE_MIN_S", 9999.0),
                  mock.patch.object(tablet_fulfil, "_ssh", self._ssh),
                  mock.patch.object(tablet_fulfil, "_scp", lambda *a, **k: (0, "")),
                  mock.patch.object(tablet_fulfil, "_scp_from", lambda *a, **k: (1, "no result"))):
            p.start()
            self.addCleanup(p.stop)

    def _ssh(self, host, cmd, timeout=None):
        if "readlink" in cmd:                      # the kill: only for the stage that is running
            self.kills.append((host, cmd))
            self.killed.set()
            return 0, "killed 2"
        if "python" in cmd and "items.txt" in cmd:  # the batch: runs until it is killed (a loop never ends itself)
            self.started.set()
            self.killed.wait(10)
            return 137, "Killed"
        return 0, ""

    def test_a_stop_kills_exactly_the_running_attempt_and_the_order_returns(self):
        spec = {"kind": "map", "name": "stop-in-flight", "batch": 1, "payload": "/x/map_echo.py", "items": ["a"],
                "control": {"item": "control", "expect": "echo:control"}}
        out = {}
        t = threading.Thread(target=lambda: out.setdefault(
            "r", tablet_fulfil.run_order(spec, ["frodo"], stop_event=self.stop)), daemon=True)
        t.start()
        self.assertTrue(self.started.wait(5), "the batch never started")
        t0 = time.time()
        self.stop.set()
        t.join(10)
        self.assertFalse(t.is_alive(), "a stopped order must not wait for its batch to end on its own")
        self.assertLess(time.time() - t0, 8)
        self.assertEqual(len(self.kills), 1, "one kill, for the one attempt in flight: %s" % self.kills)
        host, cmd = self.kills[0]
        self.assertEqual(host, "frodo")
        self.assertIn("tfoundry_stage/stop-in-flight", cmd)
        self.assertIn("readlink $p/cwd", cmd, "matched on the working directory, never the command line")

    def test_no_stop_no_kill(self):
        self.assertEqual(self.kills, [])
        self.assertIn('S="$HOME/tfoundry_stage/x"', tablet_fulfil.kill_attempt_cmd("tfoundry_stage/x"))


if __name__ == "__main__":
    unittest.main()
