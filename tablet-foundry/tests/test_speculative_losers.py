"""Speculative losers must not idle the Fellowship.

A loser's batch is already won, so its answer is never read: on completion it is ENDED (the stop path's kill), not left running. A host withheld at the start of an order because an earlier order's loser was still alive JOINS that order once the loser exits."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_fulfil


class _FakeFleet:
    """Tablets whose batch command runs `slow[host]` seconds, or until killed when slow[host] is None."""

    def __init__(self, slow):
        self.slow, self.kills, self.killed = slow, [], threading.Event()

    def ssh(self, host, cmd, timeout=None):
        if "readlink" in cmd:
            self.kills.append((host, cmd))
            self.killed.set()
            return 0, "killed 2"
        if "python" in cmd and "items.txt" in cmd:
            s = self.slow.get(host, 0.0)
            if s is None:
                self.killed.wait(20)
                return 137, "Killed"
            time.sleep(s)
        return 0, ""

    @staticmethod
    def scp_from(host, remote, local, timeout=None):
        with open(os.path.join(os.path.dirname(local), "items.txt"), encoding="utf-8") as f:
            items = [l.strip() for l in f if l.strip()]
        with open(local, "w", encoding="utf-8") as f:
            json.dump([{"item": it, "ok": True} for it in items], f)
        return 0, ""


class SpeculativeLosers(unittest.TestCase):

    def _patch(self, fleet, **consts):
        tmp = tempfile.mkdtemp()
        for p in [mock.patch.object(tablet_fulfil, "STATE_DIR", tmp),
                  mock.patch.object(tablet_fulfil, "_ssh", fleet.ssh),
                  mock.patch.object(tablet_fulfil, "_scp", lambda *a, **k: (0, "")),
                  mock.patch.object(tablet_fulfil, "_scp_from", fleet.scp_from),
                  mock.patch.dict(tablet_fulfil._ORPHAN_THREADS, {}, clear=True)] + \
                [mock.patch.object(tablet_fulfil, k, v) for k, v in consts.items()]:
            p.start()
            self.addCleanup(p.stop)

    def test_a_loser_is_ended_when_its_batch_is_won_and_its_host_is_not_withheld(self):
        fleet = _FakeFleet({"frodo": None, "sam": 0.05})        # frodo's attempt never ends by itself
        self._patch(fleet, SPECULATIVE_MIN_S=0.0, SPECULATIVE_FACTOR=0.0)
        spec = {"kind": "map", "name": "loser-ended", "batch": 1, "payload": "/x/map_echo.py", "items": ["a", "b"]}
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", tablet_fulfil.run_order(spec, ["frodo", "sam"])),
                             daemon=True)
        t.start()
        t.join(15)
        self.assertFalse(t.is_alive(), "the order must return once every batch has a winner")
        self.assertEqual([h for h, _c in fleet.kills], ["frodo"], "exactly the losing attempt is ended")
        self.assertIn("tfoundry_stage/loser-ended", fleet.kills[0][1])
        deadline = time.time() + 5
        while "frodo" in tablet_fulfil._ORPHAN_THREADS and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(tablet_fulfil._available_hosts(["frodo", "sam"])[1], [],
                         "no host is withheld from the next order")
        self.assertEqual(sorted(r["item"] for r in out["r"][0]), ["a", "b"])

    def test_a_withheld_host_joins_the_order_once_its_old_loser_exits(self):
        fleet = _FakeFleet({"frodo": 0.15, "sam": 0.0})
        self._patch(fleet, REJOIN_CHECK_S=0.05)
        old_loser = threading.Thread(target=time.sleep, args=(0.3,), daemon=True)
        old_loser.start()
        tablet_fulfil._ORPHAN_THREADS["sam"] = old_loser
        spec = {"kind": "map", "name": "rejoin", "batch": 1, "payload": "/x/map_echo.py",
                "items": ["i%02d" % i for i in range(20)]}
        rows = tablet_fulfil.run_order(spec, ["frodo", "sam"])[0]
        self.assertEqual(len(rows), 20)
        self.assertIn("sam", {r["_host"] for r in rows}, "the withheld host must join once its old loser exits")
        self.assertIn("frodo", {r["_host"] for r in rows})

    def test_a_host_whose_loser_is_still_alive_stays_out(self):
        """The safety the withholding exists for: never overlap a possibly-live remote process."""
        fleet = _FakeFleet({"frodo": 0.0})
        self._patch(fleet, REJOIN_CHECK_S=0.05)
        hold = threading.Event()
        old_loser = threading.Thread(target=hold.wait, args=(10,), daemon=True)
        old_loser.start()
        self.addCleanup(hold.set)
        tablet_fulfil._ORPHAN_THREADS["sam"] = old_loser
        spec = {"kind": "map", "name": "still-out", "batch": 1, "payload": "/x/map_echo.py",
                "items": ["i%02d" % i for i in range(5)]}
        rows = tablet_fulfil.run_order(spec, ["frodo", "sam"])[0]
        self.assertEqual({r["_host"] for r in rows}, {"frodo"})


class _TooSmallNode(_FakeFleet):
    """`small` returns instant staging failures (the model does not fit) for every item it pulls."""

    def __init__(self, small):
        super().__init__({})
        self.small = small

    def scp_from(self, host, remote, local, timeout=None):
        with open(os.path.join(os.path.dirname(local), "items.txt"), encoding="utf-8") as f:
            items = [l.strip() for l in f if l.strip()]
        with open(local, "w", encoding="utf-8") as f:
            json.dump([{"item": it, "ok": False, "error": "staging_failed:model_too_big_for_node"} if host == self.small
                       else {"item": it, "ok": True} for it in items], f)
        return 0, ""


class NodeThatCannotStage(unittest.TestCase):
    """A node that cannot stage the model retires; its items requeue to a node that can hold the model."""

    def test_unstaged_items_requeue_to_a_node_that_can_hold_the_model(self):
        fleet = _TooSmallNode("merry")
        tmp = tempfile.mkdtemp()
        for p in [mock.patch.object(tablet_fulfil, "STATE_DIR", tmp),
                  mock.patch.object(tablet_fulfil, "_ssh", fleet.ssh),
                  mock.patch.object(tablet_fulfil, "_scp", lambda *a, **k: (0, "")),
                  mock.patch.object(tablet_fulfil, "_scp_from", fleet.scp_from),
                  mock.patch.dict(tablet_fulfil._ORPHAN_THREADS, {}, clear=True)]:
            p.start()
            self.addCleanup(p.stop)
        spec = {"kind": "map", "name": "too-small", "batch": 1, "payload": "/x/map_echo.py",
                "items": ["i%02d" % i for i in range(12)]}
        rows = tablet_fulfil.run_order(spec, ["merry", "sam"])[0]
        self.assertEqual(len(rows), 12, "every item answered")
        self.assertEqual({r["_host"] for r in rows}, {"sam"}, "no staging-failure row is kept as an answer")
        self.assertTrue(all(r["ok"] for r in rows))

    def test_the_rule_is_all_rows_unstaged(self):
        self.assertTrue(tablet_fulfil.staging_failure([{"error": "staging_failed:model_too_big_for_node"}]))
        self.assertIsNone(tablet_fulfil.staging_failure([{"error": "staging_failed:x"}, {"error": ""}]),
                          "one staged row means the node can do the work")
        self.assertIsNone(tablet_fulfil.staging_failure([]))


if __name__ == "__main__":
    unittest.main()
