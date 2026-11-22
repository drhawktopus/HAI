"""A busy lane raises LaneBusy and leaves the order queued; it never consumes the order."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_fulfil
import tablet_runner

HOSTS = ["h1", "h2", "h3"]


def _arm(name, n):
    return {"name": name, "payload": "/x/map_echo.py", "batch": 1,
            "distinct_workers": n, "items": ["%s-item-%d" % (name, i) for i in range(n)],
            "control": {"item": "%s-control" % name, "expect": "echo:%s-control" % name}}


def _window(n):
    return {"kind": "map.window", "name": "lane-busy-window", "require_arch": "aarch64", "arms": [_arm("a", n)]}


def _distinct(n):
    return {"kind": "map", "name": "lane-busy-map", "payload": "/x/map_echo.py", "batch": 1,
            "distinct_workers": n, "items": ["it-%d" % i for i in range(n)],
            "control": {"item": "control", "expect": "echo:control"}}


class BuildOrderLaneBusy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.release = threading.Event()
        self.straggler = threading.Thread(target=self.release.wait, daemon=True)
        self.straggler.start()
        tablet_fulfil._ORPHAN_THREADS.clear()
        health = {h: {"healthy": True, "alive": True, "why": "ok"} for h in HOSTS}
        self.patches = [
            mock.patch.object(tablet_runner, "fleet_health", return_value=health),
            mock.patch.object(tablet_runner, "hold_order"),
            mock.patch.object(tablet_runner, "build_window_order"),
            mock.patch.object(tablet_runner, "detect_capabilities", return_value=([], HOSTS)),
            mock.patch.object(tablet_runner, "log"),
        ]
        self.m = [p.start() for p in self.patches]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.release.set()
        self.straggler.join(2)
        tablet_fulfil._ORPHAN_THREADS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, spec):
        path = os.path.join(self.tmp, spec["name"] + ".tjob.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        return path

    def test_window_waits_when_a_healthy_host_is_still_finishing(self):
        tablet_fulfil._ORPHAN_THREADS["h2"] = self.straggler
        path = self._write(_window(3))
        before = pathlib.Path(path).read_bytes()
        with self.assertRaises(tablet_runner.LaneBusy) as cm:
            tablet_runner.build_order(path)
        self.assertIn("h2", str(cm.exception))
        self.m[1].assert_not_called()                      # never HELD
        self.m[2].assert_not_called()                      # never started short-handed
        self.assertEqual(pathlib.Path(path).read_bytes(), before)  # the order is untouched, still queued

    def test_window_still_holds_when_too_few_are_healthy(self):
        path = self._write(_window(4))
        tablet_runner.build_order(path)
        self.m[1].assert_called_once()
        self.m[2].assert_not_called()

    def _sick(self, *hosts):
        health = {h: {"healthy": h not in hosts, "alive": True, "why": "ok" if h not in hosts else "dozing"}
                  for h in HOSTS}
        self.m[0].return_value = health

    def test_window_waits_when_a_node_is_unhealthy_but_on_the_roster(self):
        self._sick("h3")
        path = self._write(_window(3))
        with self.assertRaises(tablet_runner.LaneBusy) as cm:
            tablet_runner.build_order(path)
        self.assertIn("2 of the 3", str(cm.exception))
        self.m[1].assert_not_called()
        self.m[2].assert_not_called()

    def test_whole_fleet_unhealthy_waits(self):
        self._sick(*HOSTS)
        with self.assertRaises(tablet_runner.LaneBusy):
            tablet_runner.build_order(self._write(_window(1)))
        self.m[1].assert_not_called()

    def test_distinct_map_short_of_healthy_waits(self):
        self._sick("h1")
        with self.assertRaises(tablet_runner.LaneBusy):
            tablet_runner.build_order(self._write(_distinct(3)))
        self.m[1].assert_not_called()

    def test_excluded_nodes_do_not_count_toward_the_roster(self):
        self._sick("h3")
        spec = dict(_window(3), exclude_hosts=["h1"])
        tablet_runner.build_order(self._write(spec))
        self.m[1].assert_called_once()                    # 3 needed, only h2+h3 can ever run it

    def test_window_runs_on_free_hosts_only(self):
        tablet_fulfil._ORPHAN_THREADS["h3"] = self.straggler
        path = self._write(_window(2))
        tablet_runner.build_order(path)
        self.m[1].assert_not_called()
        hosts = self.m[2].call_args[0][2]
        self.assertEqual(sorted(hosts), ["h1", "h2"])

    def test_distinct_map_waits_the_same_way(self):
        tablet_fulfil._ORPHAN_THREADS["h1"] = self.straggler
        path = self._write(_distinct(3))
        with self.assertRaises(tablet_runner.LaneBusy):
            tablet_runner.build_order(path)
        self.m[1].assert_not_called()


class NextRunnable(unittest.TestCase):
    def test_skips_a_waiting_order_and_takes_the_next(self):
        waiting = {"a": 100.0}
        self.assertEqual(tablet_runner.next_runnable(["a", "b", "c"], waiting, now_t=50.0), "b")
        self.assertIn("a", waiting)

    def test_an_expired_wait_is_retried_before_anything_behind_it(self):
        waiting = {"a": 100.0}
        self.assertEqual(tablet_runner.next_runnable(["a", "b"], waiting, now_t=100.0), "a")
        self.assertNotIn("a", waiting)

    def test_everything_waiting_returns_none(self):
        self.assertIsNone(tablet_runner.next_runnable(["a"], {"a": 100.0}, now_t=1.0))

    def test_a_vanished_order_drops_its_wait(self):
        waiting = {"gone": 100.0}
        self.assertEqual(tablet_runner.next_runnable(["b"], waiting, now_t=1.0), "b")
        self.assertEqual(waiting, {})


if __name__ == "__main__":
    unittest.main()
