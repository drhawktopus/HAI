"""notify: "failures" on a Tolkien order (the GPU factories' policy): a SHIPPED order sends no DM, a VOID one still
does, and an order without the field DMs as before. A1-team pieces (one map order per delegated brief) use it so
one lead run is one DM, not one per piece."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_runner  # noqa: E402

HOSTS = ["h1", "h2"]


def _map(name, notify=None):
    spec = {"kind": "map", "name": name, "payload": "/x/map_brief.py", "batch": 1, "items": ["it-1"],
            "control": {"item": "control", "expect": "BRIEF_CONTROL_PASS"}}
    if notify is not None:
        spec["notify"] = notify
    return spec


class DmQuiet(unittest.TestCase):
    def test_policy_values(self):
        self.assertTrue(tablet_runner.dm_quiet({"notify": "failures"}))
        self.assertTrue(tablet_runner.dm_quiet({"notify": " Quiet "}))
        self.assertFalse(tablet_runner.dm_quiet({"notify": "all"}))
        self.assertFalse(tablet_runner.dm_quiet({}))
        self.assertFalse(tablet_runner.dm_quiet(None))


class MapOrderDm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dms = []
        health = {h: {"healthy": True, "alive": True, "why": "ok"} for h in HOSTS}
        self.ctl_ok = (True, "control PASS")
        self.patches = [
            mock.patch.object(tablet_runner, "fleet_health", return_value=health),
            mock.patch.object(tablet_runner, "detect_capabilities", return_value=([], HOSTS)),
            mock.patch.object(tablet_runner, "run_order",
                              return_value=([{"item": "it-1", "found": True}, {"item": "control"}], {}, 3.0, 2, [])),
            mock.patch.object(tablet_runner, "validate_control", side_effect=lambda spec, rows: self.ctl_ok),
            mock.patch.object(tablet_runner, "validate_complete", return_value=(True, "2/2")),
            mock.patch.object(tablet_runner, "retire_order", return_value=os.path.join(self.tmp, "r.results.json")),
            mock.patch.object(tablet_runner, "publish"), mock.patch.object(tablet_runner, "write_current"),
            mock.patch.object(tablet_runner, "beat"), mock.patch.object(tablet_runner, "log"),
            mock.patch.object(tablet_runner, "hold_order"),
            mock.patch.object(tablet_runner, "notify_desk", side_effect=self.dms.append),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build(self, spec):
        path = os.path.join(self.tmp, spec["name"] + ".tjob.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        tablet_runner.build_order(path)
        tablet_runner.hold_order.assert_not_called()

    def test_a_loud_shipped_order_dms_as_before(self):
        self._build(_map("loud"))
        self.assertEqual(len(self.dms), 1)
        self.assertIn("ORDER SHIPPED: loud", self.dms[0])

    def test_a_quiet_shipped_order_sends_no_dm(self):
        self._build(_map("quiet", notify="failures"))
        self.assertEqual(self.dms, [])

    def test_a_quiet_void_order_still_dms(self):
        self.ctl_ok = (False, "control: sentinel not found")
        self._build(_map("quiet-void", notify="failures"))
        self.assertEqual(len(self.dms), 1)
        self.assertIn("VOID", self.dms[0])


if __name__ == "__main__":
    unittest.main()
