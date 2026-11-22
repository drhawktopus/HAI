"""Stage 2 head-side: choosing a fellow, dispatching a burst, recording the result, grading it.

The grading half is where a wrong answer would be most persuasive, so it is pinned hardest: Lucy
scores 0.58-0.68 in the dining room and the measured false-positive band reaches 0.570, so a verdict
without a stated floor is not a verdict at all.
"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tolkien_cameras as tc  # noqa: E402


class NodeChoiceNeedsAReadyDETECTOR(unittest.TestCase):

    def test_a_node_with_no_ready_detector_is_not_chosen(self):
        h = {"gandalf": {"alive": True, "detector": {"ready": False}}}
        host, why = tc.pick_burst_node(h)
        self.assertIsNone(host)
        self.assertIn("detector", why)

    def test_a_dead_node_is_not_chosen(self):
        h = {"gandalf": {"alive": False, "detector": {"ready": True}}}
        self.assertIsNone(tc.pick_burst_node(h)[0])

    def test_a_healthy_node_is_chosen(self):
        h = {"gandalf": {"alive": True, "detector": {"ready": True}}}
        self.assertEqual(tc.pick_burst_node(h)[0], "gandalf")

    def test_a_PREFERRED_node_that_is_unhealthy_is_skipped_not_forced(self):
        """Preference must not override health, or a 'preferred' node silently becomes a failure
        mode. Any healthy node can serve any camera since both channels went 2K30 — which is exactly
        what standardising the source bought."""
        h = {"gandalf": {"alive": True, "detector": {"ready": True}},
             "sam": {"alive": True, "detector": {"ready": False}}}
        self.assertEqual(tc.pick_burst_node(h, prefer=["sam"])[0], "gandalf")

    def test_a_PREFERRED_healthy_node_wins(self):
        h = {"gandalf": {"alive": True, "detector": {"ready": True}},
             "sam": {"alive": True, "detector": {"ready": True}}}
        self.assertEqual(tc.pick_burst_node(h, prefer=["sam"])[0], "sam")

    def test_an_empty_health_map_says_how_many_it_ASKED(self):
        """Zero candidates from zero nodes asked is a different fact from zero out of six, and the
        message has to carry which — otherwise 'no healthy node' hides a broken probe."""
        self.assertIn("asked 0", tc.pick_burst_node({})[1])


class TheINNERVerdictWins(unittest.TestCase):
    """The /work endpoint wraps handler results. The outer 'ok' indicates transport success;
    the inner 'ok' (inside 'result') is the operation's actual verdict.

    A zero-frame burst failure arrives with an outer ok=True envelope. If we only check the
    outer envelope, this critical failure is misreported as SUCCESS."""

    def test_a_zero_frame_burst_inside_a_SUCCESSFUL_envelope_is_a_FAILURE(self):
        def caller(host, path, body, timeout=None):
            return True, {"ok": True, "host": "gandalf", "task": "burst", "secs": 4.2,
                          "result": {"ok": False, "error": "decoder produced ZERO frames (rc=0)",
                                     "decoded": 0, "decoder_stderr": "cap 0"}}

        out = tc.burst_analyse(caller, "gandalf", 1, "s", "e", "u", "p")
        self.assertFalse(out["ok"], "the INNER verdict must win over a successful envelope")
        self.assertEqual(out["decoded"], 0)
        self.assertIn("ZERO frames", out["error"])
        self.assertTrue(out["transport_ok"], "the transport genuinely did succeed; keep that fact")

    def test_a_successful_burst_is_unwrapped_to_its_result(self):
        """Control: the unwrapping must also work in the normal direction, or the test above passes
        for the wrong reason."""
        def caller(host, path, body, timeout=None):
            return True, {"ok": True, "task": "burst", "secs": 9.1,
                          "result": {"ok": True, "decoded": 40, "analysed": 20,
                                     "frames_with_detections": 3}}

        out = tc.burst_analyse(caller, "gandalf", 1, "s", "e", "u", "p")
        self.assertTrue(out["ok"])
        self.assertEqual(out["decoded"], 40)
        self.assertEqual(out["secs"], 9.1)

    def test_a_rejected_task_has_no_result_and_is_a_transport_failure(self):
        """Unknown task / busy / bad token: the agent never ran a handler, so the envelope IS the
        whole story and must not be mistaken for a burst verdict."""
        def caller(host, path, body, timeout=None):
            return False, {"ok": False, "error": "unknown task 'burst'",
                           "tasks": ["detect", "payload", "ping"]}

        out = tc.burst_analyse(caller, "gandalf", 1, "s", "e", "u", "p")
        self.assertFalse(out["ok"])
        self.assertIn("unknown task", out["error"])

    def test_a_BUSY_node_is_a_failure_not_an_empty_burst(self):
        """503 busy must never look like 'analysed the clip and found nothing'."""
        def caller(host, path, body, timeout=None):
            return False, {"ok": False, "error": "busy", "host": "gandalf"}

        out = tc.burst_analyse(caller, "gandalf", 1, "s", "e", "u", "p")
        self.assertFalse(out["ok"])
        self.assertEqual(out.get("frames_with_detections"), None,
                         "a busy node must not report a detection count of any kind")


class DispatchPreservesFAILUREDetail(unittest.TestCase):
    def test_a_failed_burst_is_returned_as_is_not_flattened(self):
        """A burst that decoded zero frames and a burst that could not be reached are different
        conditions with different fixes. Collapsing them to None loses the distinction the fellow
        went to the trouble of reporting."""
        def caller(host, path, body, timeout=None):
            return False, {"ok": False, "error": "decoder produced ZERO frames (rc=0)",
                           "decoded": 0, "decoder_stderr": "cap 0"}

        out = tc.burst_analyse(caller, "gandalf", 1, "s", "e", "u", "p")
        self.assertFalse(out["ok"])
        self.assertEqual(out["decoded"], 0)
        self.assertIn("cap 0", out["decoder_stderr"])
        self.assertEqual(out["node"], "gandalf", "the result must name WHICH node produced it")

    def test_a_transport_failure_still_names_the_node(self):
        def caller(host, path, body, timeout=None):
            return False, {"ok": False, "error": "URLError: timed out"}

        out = tc.burst_analyse(caller, "sam", 1, "s", "e", "u", "p")
        self.assertEqual(out["node"], "sam")
        self.assertFalse(out["ok"])

    def test_a_nonsense_response_does_not_crash_the_head(self):
        out = tc.burst_analyse(lambda *a, **k: (True, "not a dict"), "sam", 1, "s", "e", "u", "p")
        self.assertFalse(out["ok"])
        self.assertIn("non-dict", out["error"])

    def test_it_posts_to_THE_AGENTS_REAL_ENDPOINT(self):
        seen = {}

        def caller(host, path, body, timeout=None):
            seen["path"] = path
            return True, {"ok": True, "decoded": 3}

        tc.burst_analyse(caller, "gandalf", 1, "s", "e", "u", "p")
        self.assertEqual(seen["path"], "/work",
                         "the fellow agent serves /work; /task returns 'no such endpoint'")

    def test_the_request_targets_the_playback_WINDOW_and_carries_credentials(self):
        seen = {}

        def caller(host, path, body, timeout=None):
            seen.update(body)
            return True, {"ok": True, "decoded": 3}

        tc.burst_analyse(caller, "gandalf", 2, "2026-08-09 11:34:00", "2026-08-09 11:35:00",
                         "admin", "pw")
        self.assertEqual(seen["task"], "burst")
        self.assertEqual(seen["channel"], 2)
        self.assertEqual(seen["start"], "2026-08-09 11:34:00")
        self.assertEqual((seen["user"], seen["pw"]), ("admin", "pw"))


class TheFloorIsTheVERDICT(unittest.TestCase):
    """Tests verify that the detection floor distinguishes true positives from overlapping false positives.

    Lucy's scores (0.58-0.68) fall below Frigate's global 0.7 threshold, causing missed detections.
    A naive low floor (e.g., 0.25) introduces false positives (max 0.57). The tests enforce a specific
    floor to ensure the positive control passes while negative controls fail, preventing threshold tuning
    biases."""

    LUCY = {"detections": [{"frame": 0, "predictions": [{"label": "cat", "confidence": 0.61}]},
                           {"frame": 1, "predictions": [{"label": "cat", "confidence": 0.66}]}]}
    FALSE_POS = {"detections": [{"frame": 0,
                                 "predictions": [{"label": "cat", "confidence": 0.57}]}]}

    def test_lucys_band_PASSES_at_a_floor_below_it(self):
        hits, best = tc.detections_at_floor(self.LUCY, 0.58)
        self.assertEqual(hits, 2)
        self.assertAlmostEqual(best, 0.66, places=3)

    def test_lucys_band_FAILS_at_frigates_global_threshold(self):
        hits, _ = tc.detections_at_floor(self.LUCY, 0.7)
        self.assertEqual(hits, 0)

    def test_the_highest_measured_FALSE_POSITIVE_is_excluded_at_0_58(self):
        """Verify FP band (≤0.570) is excluded at floor 0.58."""
        hits, _ = tc.detections_at_floor(self.FALSE_POS, 0.58)
        self.assertEqual(hits, 0)

    def test_it_reports_the_BEST_score_even_when_nothing_clears_the_floor(self):
        """A bare zero would hide 'we nearly saw it', which is the difference between 'wrong floor'
        and 'nothing there' — the two diagnoses that matter here."""
        hits, best = tc.detections_at_floor(self.LUCY, 0.9)
        self.assertEqual(hits, 0)
        self.assertAlmostEqual(best, 0.66, places=3)

    def test_DOG_counts_as_a_cat_sighting(self):
        """Sam's label flickers between cat and dog — measured 70% then 60% on the same table 20 s
        apart. A cat-only filter silently loses about half the sightings."""
        r = {"detections": [{"frame": 0, "predictions": [{"label": "dog", "confidence": 0.62}]}]}
        self.assertEqual(tc.detections_at_floor(r, 0.58)[0], 1)

    def test_a_PERSON_is_not_counted_as_a_cat(self):
        """The widening must be specific to the cat/dog confusion, not a free-for-all — several of
        the measured false positives WERE people."""
        r = {"detections": [{"frame": 0, "predictions": [{"label": "person", "confidence": 0.95}]}]}
        self.assertEqual(tc.detections_at_floor(r, 0.58)[0], 0)

    def test_an_empty_result_scores_zero_rather_than_erroring(self):
        self.assertEqual(tc.detections_at_floor({}, 0.58), (0, 0.0))


class AnalysesAreRECORDEDIncludingFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tolkburst-")
        self._saved = tc.BURST_STORE
        tc.BURST_STORE = os.path.join(self.tmp, "analyses")

    def tearDown(self):
        tc.BURST_STORE = self._saved

    def _rows(self):
        p = tc.analysis_path()
        with io.open(p, "r", encoding="utf-8") as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]

    def test_a_successful_analysis_is_recorded(self):
        tc.record_analysis({"ok": True, "node": "gandalf", "decoded": 40, "analysed": 40,
                            "frames_with_detections": 3, "detections": []})
        self.assertEqual(len(self._rows()), 1)

    def test_a_FAILED_analysis_is_recorded_too(self):
        """'Found nothing' and 'never ran' are different facts. A store that keeps only successes
        cannot answer "did we look at that event?" — which is the question it exists for."""
        tc.record_analysis({"ok": False, "node": "sam", "error": "ZERO frames", "decoded": 0})
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("ZERO frames", rows[0])

    def test_the_analysis_file_is_day_partitioned_in_CENTRAL(self):
        tc.record_analysis({"ok": True, "node": "gandalf"})
        self.assertTrue(tc.analysis_path().endswith("%s.jsonl" % tc.day_key(__import__("time").time())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
