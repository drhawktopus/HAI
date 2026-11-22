"""The rigby lane's live generation rate reaches the Fellowship tile as `<phase> · N tok/s`, the text every factory
publishes and the desk parses into the tile's `tok_s` field (orchestrator `_factory_view`)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_runner

DESK_TOK_S = re.compile(r"([\d.]+)\s*tok/s\b")


class TpsPhase(unittest.TestCase):
    def test_median_of_recent_answers_in_the_desk_format(self):
        p = tablet_runner.tps_phase("llm-rigby", [4.0, 12.0, 6.0, 9.0])
        self.assertEqual(p, "llm-rigby · 7.5 tok/s")
        self.assertEqual(DESK_TOK_S.search(p).group(1), "7.5")

    def test_no_rate_reported_leaves_the_phase_alone(self):
        self.assertEqual(tablet_runner.tps_phase("llm-rigby", []), "llm-rigby")
        self.assertIsNone(DESK_TOK_S.search(tablet_runner.tps_phase("llm-rigby", [])))


class LiveRate(unittest.TestCase):
    """Speed is LIVE while answers stream; the recent-answer median is only used between generations."""

    def test_live_rate_wins_over_the_median(self):
        streams = {("a", "t", 0): [(1000.0, 0), (1030.0, 300), (1060.0, 600)]}
        live = tablet_runner.live_rate(streams, 1065.0)
        self.assertAlmostEqual(live, 10.0)
        self.assertEqual(tablet_runner.tps_phase("llm-rigby", [2.0], live=live), "llm-rigby · 10.0 tok/s")

    def test_concurrent_answers_sum(self):
        streams = {("warm", "t", 0): [(1010.0, 0), (1060.0, 500)], ("warm", "t", 1): [(1010.0, 0), (1060.0, 250)]}
        self.assertAlmostEqual(tablet_runner.live_rate(streams, 1061.0), 15.0)

    def test_stale_ticks_fall_back_to_the_median(self):
        streams = {("a", "t", 0): [(1000.0, 0), (1030.0, 300)]}
        stale = 1030.0 + tablet_runner.LIVE_TOKS_FRESH_S + 1
        self.assertIsNone(tablet_runner.live_rate(streams, stale))
        self.assertEqual(tablet_runner.tps_phase("llm-rigby", [4.0, 12.0, 6.0], live=None), "llm-rigby · 6.0 tok/s")

    def test_a_clock_or_a_restart_cannot_bend_it(self):
        beats = [(1040.0, 1791650000), (1060.0, 0), (1070.0, 9999999999), (1090.0, 1500)]
        self.assertAlmostEqual(tablet_runner.live_toks(beats, 1092.0), 50.0)


if __name__ == "__main__":
    unittest.main()
