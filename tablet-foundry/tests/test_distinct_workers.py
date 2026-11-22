"""Production-native distinct-worker cohorts for Phase 5 map orders."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry
import tablet_fulfil
import tablet_runner


def _spec(**overrides):
    spec = {
        "kind": "map",
        "name": "distinct-test",
        "payload": "/x/map_echo.py",
        "batch": 1,
        "distinct_workers": 3,
        "items": ["alpha", "beta", "gamma"],
        "control": {"item": "control", "expect": "echo:control"},
    }
    spec.update(overrides)
    return spec


class DistinctWorkerSpecTests(unittest.TestCase):

    def test_valid_contract(self):
        self.assertEqual(tablet_foundry.spec_defects(_spec()), [])

    def test_count_must_match_unique_inline_experiment_items(self):
        defects = tablet_foundry.spec_defects(_spec(items=["alpha", "beta"]))
        self.assertTrue(any("exactly 3" in defect for defect in defects), defects)
        defects = tablet_foundry.spec_defects(_spec(items=["alpha", "alpha", "gamma"]))
        self.assertTrue(any("unique" in defect for defect in defects), defects)
        defects = tablet_foundry.spec_defects(_spec(items=None, items_list="items.txt"))
        self.assertTrue(any("inline" in defect for defect in defects), defects)

    def test_contract_refuses_batching_and_sku_affinity(self):
        defects = tablet_foundry.spec_defects(_spec(batch=2, sku_affinity=True))
        self.assertTrue(any("batch=1" in defect for defect in defects), defects)
        self.assertTrue(any("cannot be combined" in defect for defect in defects), defects)

    def test_control_is_not_an_experiment_item(self):
        defects = tablet_foundry.spec_defects(
            _spec(items=["alpha", "beta", "control"]))
        self.assertTrue(any("control item must be distinct" in defect for defect in defects), defects)

    def test_runner_owns_deterministic_selection(self):
        eligible = ["a", "b", "c", "d", "e"]
        first = tablet_runner._select_distinct_hosts("run-1", eligible, 3)
        self.assertEqual(first, tablet_runner._select_distinct_hosts("run-1", reversed(eligible), 3))
        self.assertEqual(len(first), 3)
        self.assertEqual(len(set(first)), 3)
        self.assertLessEqual(set(first), set(eligible))


class _Recorder:

    def __init__(self):
        self.by_host = {}
        self.lock = threading.Lock()

    def ssh(self, host, cmd, timeout=0):
        return 0, ""

    @staticmethod
    def scp(host, src, dst, timeout=0):
        return 0, ""

    def scp_from(self, host, remote, local, timeout=0):
        wd = os.path.dirname(local)
        with open(os.path.join(wd, "items.txt"), encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip()]
        with self.lock:
            self.by_host.setdefault(host, []).extend(names)
        with open(local, "w", encoding="utf-8") as f:
            json.dump([{"item": name, "result": "echo:%s" % name} for name in names], f)
        return 0, ""


class DistinctWorkerExecutionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with tablet_fulfil._ORPHAN_LOCK:
            tablet_fulfil._ORPHAN_THREADS.clear()

    def tearDown(self):
        with tablet_fulfil._ORPHAN_LOCK:
            tablet_fulfil._ORPHAN_THREADS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_each_selected_host_gets_one_experiment_before_control_floats(self):
        recorder = _Recorder()
        hosts = ["h1", "h2", "h3"]
        with mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp), \
             mock.patch.object(tablet_fulfil, "_ssh", recorder.ssh), \
             mock.patch.object(tablet_fulfil, "_scp", recorder.scp), \
             mock.patch.object(tablet_fulfil, "_scp_from", recorder.scp_from):
            rows, errors, _wall, expected, unclaimed = tablet_fulfil.run_order(
                _spec(), hosts)

        self.assertEqual(errors, {})
        self.assertEqual(unclaimed, [])
        self.assertEqual(expected, 4)
        for host, item in zip(hosts, ["alpha", "beta", "gamma"]):
            experiments = [value for value in recorder.by_host.get(host, [])
                           if value != "control"]
            self.assertEqual(experiments, [item], "%s must own its pinned sample" % host)
        ok, why, actual = tablet_fulfil.validate_distinct_workers(_spec(), rows, hosts)
        self.assertTrue(ok, why)
        self.assertEqual(actual, hosts)

    def test_idle_host_never_steals_a_pinned_sample_from_a_live_straggler(self):
        """Stealing a pinned sample can only VOID the cohort, so a slow selected host keeps its item."""
        recorder = _Recorder()
        real_scp_from = recorder.scp_from

        def slow_h3(host, remote, local, timeout=0):
            if host == "h3":
                import time
                time.sleep(0.6)
            return real_scp_from(host, remote, local, timeout)

        hosts = ["h1", "h2", "h3"]
        with mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp), \
             mock.patch.object(tablet_fulfil, "SPECULATIVE_FACTOR", 0.0), \
             mock.patch.object(tablet_fulfil, "SPECULATIVE_MIN_S", 0.0), \
             mock.patch.object(tablet_fulfil, "_ssh", recorder.ssh), \
             mock.patch.object(tablet_fulfil, "_scp", recorder.scp), \
             mock.patch.object(tablet_fulfil, "_scp_from", slow_h3):
            rows, errors, _wall, _expected, unclaimed = tablet_fulfil.run_order(_spec(), hosts)

        self.assertEqual(unclaimed, [])
        stolen = [h for h in ("h1", "h2") if "gamma" in recorder.by_host.get(h, [])]
        self.assertEqual(stolen, [], "gamma is pinned to h3 and must not be re-run elsewhere")
        ok, why, _actual = tablet_fulfil.validate_distinct_workers(_spec(), rows, hosts)
        self.assertTrue(ok, why)

    def test_unpinned_work_is_still_stolen_from_a_straggler(self):
        """Positive control: without a cohort the same straggler IS reclaimed."""
        recorder = _Recorder()
        real_scp_from = recorder.scp_from
        slow_calls = []

        def slow_first_h3(host, remote, local, timeout=0):
            if host == "h3" and not slow_calls:
                slow_calls.append(1)
                import time
                time.sleep(0.6)
            return real_scp_from(host, remote, local, timeout)

        spec = _spec(items=["alpha", "beta", "gamma", "delta", "epsilon", "zeta"])
        del spec["distinct_workers"]
        with mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp), \
             mock.patch.object(tablet_fulfil, "SPECULATIVE_FACTOR", 0.0), \
             mock.patch.object(tablet_fulfil, "SPECULATIVE_MIN_S", 0.0), \
             mock.patch.object(tablet_fulfil, "_ssh", recorder.ssh), \
             mock.patch.object(tablet_fulfil, "_scp", recorder.scp), \
             mock.patch.object(tablet_fulfil, "_scp_from", slow_first_h3), \
             mock.patch.object(tablet_fulfil, "log") as log:
            tablet_fulfil.run_order(spec, ["h1", "h2", "h3"])
        self.assertTrue(any("SPECULATIVE" in str(c) for c in log.call_args_list),
                        "no steal happened, so the pinned-sample test above would prove nothing")

    def test_reassignment_is_visible_and_invalid_not_silently_distinct(self):
        rows = [
            {"item": "alpha", "_host": "h1"},
            {"item": "beta", "_host": "h2"},
            {"item": "gamma", "_host": "h1"},
            {"item": "control", "_host": "h2", "result": "echo:control"},
        ]
        ok, why, actual = tablet_fulfil.validate_distinct_workers(
            _spec(), rows, ["h1", "h2", "h3"])
        self.assertFalse(ok)
        self.assertIn("requested N=3 but observed 2 hosts", why)
        self.assertEqual(actual, ["h1", "h2"])


if __name__ == "__main__":
    unittest.main()
