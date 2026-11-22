"""exclude_hosts: a customer may name nodes an order must NOT run on.

Constraint: The scheduler seeds items to hosts in list order. For stages that enforce host
distinctness (e.g., `critic_host != solver_host`), you must specify `exclude_hosts` for the
second stage to ensure it lands on a different node than the first stage's deterministic seed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry
import tablet_runner


def _spec(**overrides):
    spec = {
        "kind": "map",
        "name": "exclude-test",
        "payload": "/x/map_echo.py",
        "batch": 1,
        "items": ["alpha", "beta"],
        "control": {"item": "control", "expect": "echo:control"},
    }
    spec.update(overrides)
    return spec


KNOWN = sorted(tablet_foundry.TABLET_NET)


class ExcludeHostsSpecTests(unittest.TestCase):

    def test_absent_is_fine_and_a_known_node_is_accepted(self):
        self.assertEqual(tablet_foundry.spec_defects(_spec()), [])
        self.assertEqual(tablet_foundry.spec_defects(_spec(exclude_hosts=[KNOWN[0]])), [])
        self.assertEqual(tablet_foundry.spec_defects(_spec(exclude_hosts=[])), [])

    def test_unknown_node_is_refused_at_intake(self):
        defects = tablet_foundry.spec_defects(_spec(exclude_hosts=[KNOWN[0], "not-a-tablet"]))
        self.assertTrue(any("unknown node" in d and "not-a-tablet" in d for d in defects), defects)

    def test_must_be_a_list_of_names(self):
        for bad in ("boromir", [1], [""], {"boromir": True}):
            defects = tablet_foundry.spec_defects(_spec(exclude_hosts=bad))
            self.assertTrue(any("list of node names" in d for d in defects), (bad, defects))

    def test_window_level_only_never_per_arm(self):
        arm = _spec(distinct_workers=2, exclude_hosts=[KNOWN[0]])
        arm.pop("kind")
        window = {"kind": "map.window", "name": "w", "user": "test", "require_arch": "aarch64",
                  "arms": [arm]}
        defects = tablet_foundry.spec_defects(window)
        self.assertTrue(any("belongs on the window" in d for d in defects), defects)
        # and on the window itself it is accepted
        arm.pop("exclude_hosts")
        window["exclude_hosts"] = [KNOWN[0]]
        self.assertEqual(tablet_foundry.spec_defects(window), [])


class ExcludeHostsRunnerTests(unittest.TestCase):

    def test_named_hosts_are_removed_and_reported(self):
        kept, excluded = tablet_runner._apply_exclude_hosts(
            "o", {"exclude_hosts": ["boromir"]}, ["aragorn", "boromir", "faramir"])
        self.assertEqual(kept, ["aragorn", "faramir"])
        self.assertEqual(excluded, ["boromir"])

    def test_no_field_is_a_no_op(self):
        hosts = ["aragorn", "boromir"]
        self.assertEqual(tablet_runner._apply_exclude_hosts("o", {}, hosts), (hosts, []))
        self.assertEqual(tablet_runner._apply_exclude_hosts("o", {"exclude_hosts": []}, hosts), (hosts, []))

    def test_excluding_an_absent_host_costs_nothing(self):
        kept, excluded = tablet_runner._apply_exclude_hosts(
            "o", {"exclude_hosts": ["gimli"]}, ["aragorn", "boromir"])
        self.assertEqual(kept, ["aragorn", "boromir"])
        self.assertEqual(excluded, [])

    def test_excluding_everything_leaves_an_empty_pool_for_the_caller_to_hold(self):
        kept, excluded = tablet_runner._apply_exclude_hosts(
            "o", {"exclude_hosts": ["aragorn", "boromir"]}, ["aragorn", "boromir"])
        self.assertEqual(kept, [])
        self.assertEqual(excluded, ["aragorn", "boromir"])


if __name__ == "__main__":
    unittest.main()
