"""The Fellowship tile's Progress / Rate / ETA: a window pushes step, total and an eta string the deck can read.

The two patterns below are copied from the orchestrator's /p20/factory builder (app.py, the "rate" and "~Nm left"
extraction) because the deck lives in another image; if the deck's spelling changes, change them together."""
import io
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as tf
import tablet_runner as tr

DECK_RATE = re.compile(r"([\d.]+)\s*s\s*/\s*(it|img|image|step|item)\b")
DECK_LEFT = re.compile(r"~\s*(\d+)\s*m\s*left")


class WindowEta(unittest.TestCase):
    def test_nothing_until_the_first_item(self):
        self.assertEqual(tr.window_eta(30.0, 0, 10), "")

    def test_rate_and_time_left_in_the_decks_spelling(self):
        s = tr.window_eta(120.0, 4, 10)            # 30 s/item, 6 left -> 3 min
        self.assertEqual(DECK_RATE.search(s).groups(), ("30.0", "item"))
        self.assertEqual(DECK_LEFT.search(s).group(1), "3")

    def test_last_item_reads_finishing(self):
        self.assertIn("finishing", tr.window_eta(50.0, 10, 10))

    def test_parallel_window_is_not_counted_as_serial(self):
        """Items on several tablets finish together: the estimate is parallel-aware, never the serial sum."""
        s = tr.window_eta(165.0, 2, 9, parallel=8, done_times=[138.0, 138.0])
        self.assertEqual(DECK_LEFT.search(s).group(1), "2")
        self.assertEqual(DECK_RATE.search(s).groups(), ("138.0", "item"))

    def test_second_round_completions_are_divided_by_their_round(self):
        # 4 items on 2 tablets, 60 s each: completions at 60, 60, 120 -> per item 60 s; end 120 s; at 125 s finishing
        self.assertEqual(DECK_RATE.search(tr.window_eta(121.0, 3, 4, 2, [60.0, 60.0, 120.0])).group(1), "60.0")
        self.assertIn("finishing", tr.window_eta(125.0, 3, 4, 2, [60.0, 60.0, 120.0]))


class ResumedLlmEta(unittest.TestCase):
    def test_answers_a_resume_kept_never_enter_the_rate(self):
        """Excluded resumed answers must not skew the per-item rate calculation; only answers from the current run are valid for estimating speed."""
        s = tr.llm_eta(1850.0, 10, 19, resumed=8)      # 2 fresh answers in 1850 s, 9 left
        self.assertEqual(DECK_RATE.search(s).groups(), ("925.0", "item"))
        self.assertEqual(DECK_LEFT.search(s).group(1), "139")
        self.assertEqual(tr.llm_eta(30.0, 8, 19, resumed=8), "", "only kept answers so far: no rate yet")

    def test_no_resume_is_unchanged(self):
        self.assertEqual(tr.llm_eta(120.0, 4, 10), tr.window_eta(120.0, 4, 10))


class MedianLlmEta(unittest.TestCase):
    def test_one_marathon_item_does_not_set_the_pace(self):
        """Uses a skewed distribution (one 180-min item) where mean (~51) diverges from median (20), proving median is the pace."""
        done_at = [m * 60.0 for m in (5, 185, 190, 236, 256)]
        s = tr.llm_eta(256 * 60.0, 5, 12, done_at=done_at)
        self.assertEqual(DECK_RATE.search(s).groups(), ("1200.0", "item"))
        self.assertEqual(DECK_LEFT.search(s).group(1), "140")          # 7 left x 20 min

    def test_kept_answers_stay_out_of_the_median(self):
        s = tr.llm_eta(1850.0, 10, 19, resumed=8, done_at=[900.0, 1850.0])
        self.assertEqual(DECK_RATE.search(s).groups(), ("925.0", "item"))

    def test_mismatched_times_fall_back_to_the_mean(self):
        self.assertEqual(tr.llm_eta(120.0, 4, 10, done_at=[30.0]), tr.window_eta(120.0, 4, 10))

    def test_the_control_answer_is_not_an_item_of_the_rate(self):
        """Controls count toward completion but are excluded from ETA rate calculations.
        Rate is derived solely from the median of real item durations."""
        self.assertEqual(tr.llm_eta(172.0, 1, 9, done_at=[], controls=1), "", "control only: no rate yet")
        s = tr.llm_eta(3000.0, 2, 9, done_at=[2800.0], controls=1)
        self.assertEqual(DECK_RATE.search(s).groups(), ("2800.0", "item"))
        self.assertEqual(DECK_LEFT.search(s).group(1), "327")            # 7 exercises left x 2800 s


class PublishCarriesEta(unittest.TestCase):
    def test_eta_and_counts_reach_the_run_state_body(self):
        sent = []

        class R(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            sent.append(json.loads(req.data.decode("utf-8")))
            return R(b'{"ok": true}')
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            tf.publish(name="w", state="running", phase="window", step=4, total=10, eta="30.0 s/item · ~3m left")
        self.assertEqual((sent[0]["step"], sent[0]["total_steps"], sent[0]["eta"]), (4, 10, "30.0 s/item · ~3m left"))

    def test_no_eta_key_when_there_is_none(self):
        sent = []

        class R(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch("urllib.request.urlopen", side_effect=lambda req, timeout=None: (sent.append(json.loads(req.data)) or R(b'{"ok": true}'))):
            tf.publish(name="", state="idle")
        self.assertNotIn("eta", sent[0])


if __name__ == "__main__":
    unittest.main()
