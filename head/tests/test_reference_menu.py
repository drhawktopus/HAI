"""A device that names a reference box places from that box's measured menu rows until its own rows exist
(config.apply_reference): the rows, windows, memory profiles and offload entries come across under the device's name,
its own rows win model by model, the crown stays where it is, and a roster that names no reference reads the table
unchanged. The weighing sets aside a row whose box states its capabilities without the row's model
(scheduler.declares_model, the order's own rule), so a reference row for a model the device lacks never places."""
import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import config
import scheduler

HERE = os.path.dirname(os.path.abspath(__file__))


def _row(box, model, q, low, s, j, **kw):
    r = {"box": box, "model": model, "think": "off", "temperature": "0.0", "role": "home", "quality": q,
         "quality_low": low, "s_per_solve": s, "j_per_solve": j, "load_s": 30.0}
    r.update(kw)
    return r


_LEAD = [_row("mothership", "big-model", 1.0, 0.95, 2.4, 900),
         _row("bighonker", "small-model", 0.80, 0.68, 1.9, 300),
         _row("mothership", "coder-model", 0.78, 0.66, 1.5, 250),
         _row("homepc", "coder-model", 0.90, 0.80, 1.2, 200)]


def _tiers(rows):
    return {"fastest": list(rows), "least_energy": list(rows), "best_quality": list(rows)}


TABLE = {"families": {"chat-c1": {"interactive_line_s": 15.0, "tiers": {"lead": _tiers(_LEAD)}}},
         "contexts": {"mothership": {"big-model": 131072, "coder-model": 65536}, "homepc": {"coder-model": 32768}},
         "model_fits": {"rules": {}, "hosts": {"mothership": {"big-model": {"points": []}}}},
         "offload_ok": [["mothership", "big-model"]],
         "offload_ram": {"mothership": {"big-model": {"host_mib": 1}}},
         "tier_homes": {"lead": ["mothership"]},
         "roles": {"crown": {"box": "mothership", "model": "big-model"}}}


def _stranger(**extra):
    dev = {"name": "homepc", "label": "Home PC", "roles": ["brain", "failover", "head", "advisor"], "vram_gb": 24.0,
           "ram_gb": 64.0, "cpu": 16, "gpu": "a 24 GB card"}
    dev.update(extra)
    return config.number_devices(config.validate_roster({"devices": [dev], "tablets": []}), environ={})


class ApplyingAReference(unittest.TestCase):

    def test_a_roster_naming_no_reference_reads_the_table_unchanged(self):
        lab, _src = config.load_roster(os.path.join(HERE, "fixtures", "lab_roster.json"))
        self.assertEqual(config.reference_boxes(lab), {})
        self.assertIs(config.apply_reference(TABLE, lab), TABLE)

    def test_the_device_gets_the_reference_rows_after_each_original_and_keeps_its_own(self):
        before = json.dumps(TABLE, sort_keys=True)
        out = config.apply_reference(TABLE, _stranger(reference_box="mothership"))
        self.assertEqual(json.dumps(TABLE, sort_keys=True), before, "the table read from disk is not changed")
        for lens, rows in out["families"]["chat-c1"]["tiers"]["lead"].items():
            got = [(r["box"], r["model"], r.get("reference_box")) for r in rows]
            self.assertEqual(got, [("mothership", "big-model", None), ("homepc", "big-model", "mothership"),
                                   ("bighonker", "small-model", None), ("mothership", "coder-model", None),
                                   ("homepc", "coder-model", None)], lens)
        self.assertEqual(out["contexts"]["homepc"], {"big-model": 131072, "coder-model": 32768},
                         "the reference's windows, the device's own window winning")
        self.assertIn("homepc", out["model_fits"]["hosts"])
        self.assertIn(["homepc", "big-model"], out["offload_ok"])
        self.assertEqual(out["offload_ram"]["homepc"], {"big-model": {"host_mib": 1}})
        self.assertEqual(out["tier_homes"]["lead"], ["mothership", "homepc"])
        self.assertEqual(out["roles"]["crown"]["box"], "mothership", "the crown is a decision, not a measured row")
        self.assertEqual(out["reference"], {"homepc": "mothership"})

    def test_the_reference_can_be_a_setting(self):
        roster = _stranger()
        with mock.patch.dict(os.environ, {"PC1_REFERENCE_BOX": "BigHonker"}):
            self.assertEqual(config.reference_boxes(roster), {"homepc": "bighonker"})
        with mock.patch.dict(os.environ, {"PC1_REFERENCE_BOX": "homepc"}):
            self.assertEqual(config.reference_boxes(roster), {}, "a device naming itself names nothing")

    def test_an_unreadable_table_is_an_empty_document(self):
        self.assertEqual(config.load_policy(os.path.join(tempfile.mkdtemp(), "missing.json")), {})


class PlacingFromAReference(unittest.TestCase):

    def setUp(self):
        self.table = os.path.join(tempfile.mkdtemp(), "policy.json")
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(TABLE, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        self.caps = {}
        self.p = [mock.patch.object(config, "POLICY_TABLE", self.table),
                  mock.patch.object(config, "ROSTER", _stranger(reference_box="mothership")),
                  mock.patch.object(scheduler, "CARDS", {"homepc": {"url": None}}),
                  mock.patch.object(scheduler, "TOLKIEN_BOX", None),
                  mock.patch.object(scheduler, "factory_capabilities", side_effect=lambda h, path=None:
                                    set(self.caps.get(h, ()))),
                  mock.patch.object(scheduler, "residency_cost", return_value=(0.0, "resident")),
                  mock.patch("ollama_client.models_resident", return_value=[]),
                  mock.patch.object(scheduler, "_price_of_the_hour", return_value={
                      "usd_per_kwh": 0.11, "source": "placeholder", "hour": None, "curve": None, "band": "normal"})]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        scheduler._POLICY_CACHE.update(key=None, data=None)

    def weigh(self):
        band, rejected = scheduler.weigh_rows("chat-c1", "lead", True)
        return band, rejected

    def test_the_placement_table_as_read_carries_the_reference_rows(self):
        rows = [(r["box"], r["model"]) for r in scheduler.policy_rows("chat-c1", "lead")]
        self.assertIn(("homepc", "big-model"), rows)
        self.assertEqual(rows.count(("homepc", "coder-model")), 1)

    def test_a_reference_row_for_a_model_the_device_lacks_is_set_aside_and_says_why(self):
        self.caps = {"homepc": {"model.coder-model"}}
        band, rejected = self.weigh()
        homepc = [(c["box"], c["entry"]["model"]) for c in band if c["box"] == "homepc"]
        self.assertEqual(homepc, [("homepc", "coder-model")])
        aside = [c for c in rejected if c["box"] == "homepc" and c["entry"]["model"] == "big-model"]
        self.assertEqual(len(aside), 1)
        self.assertIn("homepc does not declare model.big-model", " ".join(aside[0]["notes"]))

    def test_a_reference_row_for_a_model_the_device_has_is_weighed(self):
        self.caps = {"homepc": {"model.coder-model", "model.big-model"}}
        band, _rej = self.weigh()
        self.assertIn(("homepc", "big-model"), [(c["box"], c["entry"]["model"]) for c in band])

    def test_a_box_that_stated_nothing_is_left_to_place(self):
        """No capability statement is place()'s case ("cannot-tell is not free"), not the weighing's."""
        band, rejected = self.weigh()
        self.assertFalse([c for c in rejected if "does not declare" in " ".join(c["notes"])])
        self.assertIn(("homepc", "big-model"), [(c["box"], c["entry"]["model"]) for c in band + rejected])

    def test_a_box_this_head_does_not_have_sets_no_need(self):
        """Without a reference, the table's best row is the mothership row (lower bound 0.95), on a box this head does
        not have: the device's own measured row (0.90) is the band, not a row below a need set elsewhere."""
        with mock.patch.object(config, "ROSTER", _stranger()):
            scheduler._POLICY_CACHE.update(key=None, data=None)
            band, rejected = self.weigh()
        self.assertEqual([(c["box"], c["entry"]["model"]) for c in band], [("homepc", "coder-model")])
        self.assertEqual(rejected, [])

    def test_model_names_compare_as_ollama_resolves_them(self):
        """The menu writes `nomic-embed-text`; a factory declares `model.nomic-embed-text:latest`."""
        caps = {"model.nomic-embed-text:latest", "model.hf.co/org/x-gguf:Q4_K_M", "model.plain"}
        self.assertTrue(scheduler.declares_model("h", "nomic-embed-text", caps))
        self.assertTrue(scheduler.declares_model("h", "plain:latest", caps))
        self.assertTrue(scheduler.declares_model("h", "hf.co/org/x-gguf:Q4_K_M", caps))
        self.assertFalse(scheduler.declares_model("h", "hf.co/org/x-gguf", caps))
        self.assertFalse(scheduler.declares_model("h", "nomic-embed-text:v2", caps))

    def test_the_order_check_and_the_weighing_use_one_rule(self):
        self.caps = {"homepc": {"model.coder-model"}}
        self.assertTrue(scheduler.declares_model("homepc", "coder-model"))
        self.assertFalse(scheduler.declares_model("homepc", "big-model"))
        entry = next(r for r in scheduler.policy_rows("chat-c1", "lead")
                     if r["box"] == "homepc" and r["model"] == "big-model")
        _spec, why = scheduler.resolve_policy_tokens({"m": "{POLICY_MODEL}"}, "homepc", "chat-c1", "lead", "fastest",
                                                     entry=copy.deepcopy(entry))
        self.assertEqual(why, "homepc does not declare model.big-model")


if __name__ == "__main__":
    unittest.main()
