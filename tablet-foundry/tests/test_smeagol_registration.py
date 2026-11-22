"""Pins Sméagol's normal membership and Tolkien's existing failure/redelegation contract."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import scheduler
import tablet_fulfil


class SmeagolSchedulerTests(unittest.TestCase):
    def test_roster_records_truthful_runtime(self):
        row = scheduler.TABLETS["smeagol"]
        self.assertEqual(row["arch"], "armv7l")
        self.assertEqual(row["runtime_bits"], 32)
        self.assertEqual(row["gflops"], 0.0)
        self.assertFalse(row["direct_cpu_eligible"])

    def test_spock_direct_cpu_path_excludes_smeagol(self):
        with mock.patch.object(scheduler, "tablet_alive", return_value=True):
            self.assertEqual(scheduler.place("cpu", only=["smeagol"]), [])


class TolkienRedelegationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp)
        self.state.start()

    def tearDown(self):
        self.state.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_armv7_refusal_requeues_to_another_normal_member(self):
        """A node-level payload refusal requeues the batch and retires only that node."""
        spec = {
            "kind": "map",
            "name": "smeagol-redelegation",
            "batch": 1,
            "payload": os.path.join(self.tmp, "payload.py"),
            "items": ["work"],
            "control": {"item": "control", "expect": "echo:control"},
        }
        with open(spec["payload"], "w", encoding="utf-8") as fh:
            fh.write("# mocked transport payload\n")

        def fake_ssh(host, command, timeout=0):
            if host == "smeagol" and " python " in command:
                return 1, "unsupported runtime"
            return 0, ""

        def fake_scp(host, src, dst, timeout=0):
            return 0, ""

        def fake_scp_from(host, remote, local, timeout=0):
            workdir = os.path.dirname(local)
            with open(os.path.join(workdir, "items.txt"), encoding="utf-8") as fh:
                items = [line.strip() for line in fh if line.strip()]
            with open(local, "w", encoding="utf-8") as fh:
                json.dump([{"item": item, "result": "echo:" + item} for item in items], fh)
            return 0, ""

        with mock.patch.object(tablet_fulfil, "_ssh", fake_ssh), \
             mock.patch.object(tablet_fulfil, "_scp", fake_scp), \
             mock.patch.object(tablet_fulfil, "_scp_from", fake_scp_from):
            rows, errors, _wall, expected, unclaimed = tablet_fulfil.run_order(
                spec, ["smeagol", "gandalf"]
            )

        self.assertIn("smeagol", errors)
        self.assertEqual(unclaimed, [])
        self.assertEqual(expected, 2)
        self.assertEqual({row["item"] for row in rows}, {"work", "control"})
        self.assertEqual({row["_host"] for row in rows}, {"gandalf"})


if __name__ == "__main__":
    unittest.main()
