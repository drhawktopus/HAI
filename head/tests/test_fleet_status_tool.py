import json
import unittest
from unittest import mock

import app
import tools


def _agg(stale_card=False, stale_run=False, health="watching"):
    # A stale card NEVER coexists with a "free" summary in the live aggregate — it rewrites the
    # summary with the asleep-vs-silent discrimination. The fixture mirrors that contract.
    summary = ("asleep (worker silent 21046s - designed; WoL wakes it on demand)"
               if stale_card else "free")
    return {"factories": {"mothership": {
        "summary": summary, "card": {"state": "free", "stale": stale_card, "age_s": 21046.0},
        "run": {"name": "old_run" if stale_run else "", "state": "", "stale": stale_run},
        "health": {"state": health, "detail": "no events today"},
    }}}


class FleetStatusTool(unittest.TestCase):
    def test_offered_in_both_lists_and_ungated(self):
        for lst, label in ((tools.BRAIN_TOOLS, "BRAIN_TOOLS"), (tools.HEAVY_TOOLS, "HEAVY_TOOLS")):
            self.assertIn("fleet_status", {t["function"]["name"] for t in lst}, label)
        self.assertTrue(tools._allowed("fleet_status", "Riley"),
                        "deck reads are free for every user - looking is free")

    def test_fleet_view_reports_problems_from_stale_sources(self):
        with mock.patch.object(app, "p20_factories", return_value=_agg(stale_card=True,
                                                                       stale_run=True)):
            out = json.loads(tools._fleet_status("fleet"))
        probs = " ".join(out["problems"])
        # The problems line INHERITS the aggregate's discrimination (one derivation) — a sleeping
        # host is stated as asleep-and-wakeable, never as a bare fault.
        self.assertIn("asleep", probs)
        self.assertIn("stale run record", probs)

    def test_fleet_view_clean_says_so_explicitly(self):
        """A clean fleet must say 'none' — silence and health are different claims."""
        with mock.patch.object(app, "p20_factories", return_value=_agg()):
            out = json.loads(tools._fleet_status(""))
        self.assertEqual(out["problems"], ["none - all sources fresh"])
        self.assertEqual(out["factories"]["mothership"]["health"], "watching")

    def test_host_view_serves_the_tile(self):
        tile = {"host": "mothership", "state": "training", "order": "run_x", "progress": "5/100",
                "pct": 5.0, "rate": "", "eta": "", "detail": "", "health": "watching",
                "health_detail": "", "stale": False, "age_s": 3.0}
        with mock.patch.object(app, "_factory_view", return_value=tile) as fv:
            out = json.loads(tools._fleet_status("mothership"))
        fv.assert_called_once_with("mothership")
        self.assertEqual(out["state"], "training")
        self.assertEqual(out["order"], "run_x")

    def test_order_id_form_serves_fate_never_the_work(self):
        fate = {"ok": True, "id": "f97805882e08", "status": "done", "gate": "operator window"}
        with mock.patch.object(app, "_order_status_view", return_value=fate) as ov:
            out = json.loads(tools._fleet_status("f97805882e08"))
        ov.assert_called_once_with("f97805882e08")
        self.assertEqual(out["status"], "done")
        self.assertNotIn("spec", out)


if __name__ == "__main__":
    unittest.main()
