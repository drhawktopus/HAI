"""Parallel non-overlapping Phase 5 map.window execution."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry
import tablet_runner


def _arm(name, n):
    return {"name": name, "payload": "/x/map_echo.py", "batch": 1,
            "distinct_workers": n, "items": ["%s-item-%d" % (name, i) for i in range(n)],
            "control": {"item": "%s-control" % name, "expect": "echo:%s-control" % name}}


def _window(*arms):
    return {"kind": "map.window", "name": "phase5-window", "require_arch": "aarch64",
            "arms": list(arms)}


class WindowSpecTests(unittest.TestCase):

    def test_valid_parallel_window(self):
        self.assertEqual(tablet_foundry.spec_defects(_window(_arm("n7", 7), _arm("n3", 3))), [])

    def test_window_refuses_more_than_ten_or_invalid_child(self):
        defects = tablet_foundry.spec_defects(_window(_arm("n8", 8), _arm("n3", 3)))
        self.assertTrue(any("maximum" in defect for defect in defects), defects)
        child = _arm("bad", 2)
        child["items"] = ["only-one"]
        defects = tablet_foundry.spec_defects(_window(child))
        self.assertTrue(any("arm bad" in defect and "exactly 2" in defect for defect in defects),
                        defects)


    def test_a_window_arm_without_a_cohort_is_refused_at_the_door(self):
        # distinct_workers is required for 'arm' types; ensure the validator rejects it early to avoid runtime KeyError
        arm = _arm("pull", 3)
        del arm["distinct_workers"]
        defects = tablet_foundry.spec_defects(_window(arm))
        self.assertTrue(any("arm pull" in d and "distinct_workers" in d and '"map"' in d for d in defects), defects)
        pull = dict(arm, kind="map", name="pull-order")
        self.assertEqual(tablet_foundry.spec_defects(pull), [], "the same work as a plain map order is valid")


class WindowExecutionTests(unittest.TestCase):

    @staticmethod
    def _fake_run_order(arm, hosts, on_rows=None, host_sku=None):
        time.sleep(0.15)
        rows = [{"item": item, "_host": host, "ok": True}
                for item, host in zip(arm["items"], hosts)]
        rows.append({"item": arm["control"]["item"], "_host": hosts[0],
                     "result": arm["control"]["expect"], "ok": True})
        if on_rows:
            on_rows(len(rows), len(rows))
        return rows, {}, 0.15, len(rows), []

    def test_arms_run_concurrently_on_disjoint_selected_cohorts(self):
        spec = _window(_arm("n7", 7), _arm("n3", 3))
        hosts = ["h%d" % i for i in range(10)]
        started = time.time()
        with mock.patch.object(tablet_runner, "run_order", self._fake_run_order):
            results, _wall = tablet_runner.execute_window(spec, hosts)
        elapsed = time.time() - started
        self.assertLess(elapsed, 0.27, "two 150ms arms should overlap, not take 300ms")
        self.assertTrue(all(result["usable"] for result in results), results)
        selected = [set(result["cohort"]["selected_hosts"]) for result in results]
        self.assertFalse(selected[0] & selected[1])
        self.assertEqual(len(selected[0]), 7)
        self.assertEqual(len(selected[1]), 3)
        self.assertTrue(all(result["cohort"]["passed"] for result in results))


if __name__ == "__main__":
    unittest.main()
