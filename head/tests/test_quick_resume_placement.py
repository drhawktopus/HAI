"""Placement must favor the box holding the chat's saved state (cold start optimization).
Kirk records the box where the chat last finished; the weigher prices cold rows on that box without
re-read overhead, assuming state restoration rather than disk I/O. Warm rows retain standard pricing."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import chat_window as CW
import config
import scheduler

FAM = {"families": {"chat-c1": {"interactive_line_s": 1000.0, "tiers": {"8gb": {
    "fastest": [], "least_energy": [],
    "best_quality": [{"box": "bighonker", "model": "m9", "think": "off", "role": "home", "quality": 0.9,
                      "quality_low": 0.8, "s_per_solve": 5.0, "j_per_solve": 100, "load_s": 10.0, "read_tok_s": 1000},
                     {"box": "softserve", "model": "m9", "think": "off", "role": "home", "quality": 0.9,
                      "quality_low": 0.8, "s_per_solve": 5.0, "j_per_solve": 90, "load_s": 10.0, "read_tok_s": 1000}]}}}}}


class _State(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        p = mock.patch.object(config, "STATE_DIR", self.d)
        p.start()
        self.addCleanup(p.stop)


class Homes(_State):
    def test_record_and_read_back(self):
        CW.record_home("Avery/abc", "bighonker", "m9", 19968)
        self.assertEqual(CW.conv_home("Avery/abc"), ("bighonker", "m9", 19968))
        CW.record_home("Avery/abc", "softserve", "m9", 8192)
        self.assertEqual(CW.conv_home("Avery/abc"), ("softserve", "m9", 8192), "the newest window wins")
        self.assertIsNone(CW.conv_home("Avery/never"))
        self.assertIsNone(CW.conv_home(None))

    def test_the_store_keeps_the_newest(self):
        with mock.patch.object(CW, "HOMES_KEPT", 3):
            for i in range(5):
                with mock.patch.object(CW.time, "time", return_value=float(i)):
                    CW.record_home("u/%d" % i, "bighonker", "m9", 1)
        self.assertEqual(sorted(CW._read_homes()), ["u/2", "u/3", "u/4"])


class ColdHomeRowIsNotReRead(_State):
    def setUp(self):
        super().setUp()
        table = os.path.join(self.d, "policy.json")
        with open(table, "w", encoding="utf-8") as f:
            json.dump(FAM, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        self.addCleanup(scheduler._POLICY_CACHE.update, key=None, data=None)
        self.warm = set()

        def residency(host, model, load_s, read_tok_s=None, context_tokens=0, loaded=None):
            if host in self.warm:
                return 0.0, "resident"
            reread = context_tokens / float(read_tok_s) if context_tokens else 0.0
            return float(load_s) + reread, "cold +%.0f s load%s" % (load_s, ", +%.0f s re-read" % reread if reread else "")
        for p in (mock.patch.object(config, "POLICY_TABLE", table),
                  mock.patch.object(scheduler, "residency_cost", side_effect=residency),
                  mock.patch.object(scheduler, "_window_open_cost", return_value=(0.0, None)),
                  mock.patch("ollama_client.models_resident", return_value=[]),
                  mock.patch.object(scheduler, "_price_of_the_hour", return_value={
                      "usd_per_kwh": 0.11, "source": "placeholder", "hour": None, "curve": None, "band": "normal"})):
            p.start()
            self.addCleanup(p.stop)

    def rows(self, conv):
        band, _ = scheduler.weigh_rows("chat-c1", "8gb", True, context_tokens=30000, window_cost=True, conv=conv)
        return {c["box"]: c for c in band}

    def test_the_home_box_is_restored_and_the_other_re_reads(self):
        CW.record_home("Avery/abc", "bighonker", "m9", 19968)
        r = self.rows("Avery/abc")
        self.assertAlmostEqual(r["bighonker"]["time_s"], 15.0, msg="5 s solve + 10 s load, no 30 s re-read")
        self.assertAlmostEqual(r["softserve"]["time_s"], 45.0)
        self.assertIn("this chat's state is saved here: restored, not re-read", r["bighonker"]["notes"])

    def test_a_chat_with_no_home_prices_as_before(self):
        r = self.rows("Avery/new")
        self.assertAlmostEqual(r["bighonker"]["time_s"], 45.0)
        self.assertAlmostEqual(r["softserve"]["time_s"], 45.0)

    def test_a_warm_row_keeps_todays_price(self):
        CW.record_home("Avery/abc", "softserve", "m9", 8192)
        self.warm.add("bighonker")
        r = self.rows("Avery/abc")
        self.assertAlmostEqual(r["bighonker"]["time_s"], 5.0)
        self.assertAlmostEqual(r["softserve"]["time_s"], 15.0)


class ReadRate(unittest.TestCase):
    """The window prices re-reading an older chat at the menu's measured read rate for its (box, model)."""

    def test_the_menus_measured_rate_for_the_pair(self):
        with mock.patch.object(scheduler, "policy_data", lambda: FAM):
            self.assertEqual(scheduler.read_rate("bighonker", "m9"), 1000.0)
            self.assertIsNone(scheduler.read_rate("bighonker", "other"), "an unmeasured pair has no price")
        with mock.patch.object(scheduler, "policy_data", lambda: {}):
            self.assertIsNone(scheduler.read_rate("bighonker", "m9"), "an unreadable table is no rate, never a raise")


if __name__ == "__main__":
    unittest.main()
