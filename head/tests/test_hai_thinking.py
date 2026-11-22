"""Tests for GET /hai/thinking tile.

Key constraints:
1. Tile must reuse the worker's progress string (not derive its own) to prevent drift between chat and tile views.
2. Liveness is determined by presence in `_EVENTS`, not `status == 'running'`. Status fields may lag after crash for up to GRACE_TTL_SEC; only active runs should be visible.
3. Positive controls ensure live runs are shown, distinguishing strict liveness checks from broken ones."""
import types
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
import cancellation


class _ThinkingCase(unittest.TestCase):
    """Isolates the run registry AND the cancel-handle table for every test."""

    def setUp(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation._clear_for_tests()
        self.client = TestClient(app_module.app)
        self.registry = cancellation.QueryRunRegistry()

    def tearDown(self):
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation._clear_for_tests()

    def _start(self, cid, user="Alex", stage=None):
        """Begin a genuinely in-flight run the way the request path does."""
        cancellation.begin(cid)
        self.registry.register_with_user(cid, user)
        if stage:
            self.registry.set_stage(cid, stage)

    def _tile(self):
        resp = self.client.get("/hai/thinking")
        self.assertEqual(resp.status_code, 200)
        return resp.json()


class TheWireExists(_ThinkingCase):
    """Built but unreachable endpoint fails identically to never-built."""

    def test_the_endpoint_is_registered_and_answers(self):
        body = self._tile()
        self.assertTrue(body["ok"])
        self.assertIn(body["state"], ("idle", "thinking"))

    def test_run_query_records_progress_emitted_by_the_worker(self):
        """End-to-end wire: a status message emitted INSIDE the query lands on the run.

        Behavioural, not source-text: the fake core emits a status exactly as the real workers do,
        and the assertion is that the registry saw it. This is what would fail if the wrapper were
        removed from `_run_query`, whatever the code around it looked like.
        """
        q = types.SimpleNamespace(text="hi", user="Alex", cancel_id="wire-1",
                                  effort_mode=None, routing=None)
        cancellation.begin("wire-1")
        seen = []

        def fake_core(_q, status=None, diag=None):
            status("Tool step 2/12: search_docs")
            return {}

        with mock.patch.object(app_module, "_run_query_core", fake_core):
            app_module._run_query(q, seen.append)

        entry = self.registry.get("wire-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.stage, "Tool step 2/12: search_docs")
        self.assertEqual(seen, ["Tool step 2/12: search_docs"],
                         "the original callback must still receive the message")

    def test_the_plain_post_path_records_even_with_no_stream_callback(self):
        """`status=None` (POST /query) must still populate the tile."""
        q = types.SimpleNamespace(text="hi", user="Alex", cancel_id="wire-2",
                                  effort_mode=None, routing=None)
        cancellation.begin("wire-2")

        def fake_core(_q, status=None, diag=None):
            status("Mothership is working on the request")
            return {}

        with mock.patch.object(app_module, "_run_query_core", fake_core):
            app_module._run_query(q, None)

        self.assertEqual(self.registry.get("wire-2").stage,
                         "Mothership is working on the request")


class LivenessIsTheCancelHandle(_ThinkingCase):

    def test_a_live_run_is_shown(self):
        """POSITIVE CONTROL -- without this, 'hides dead runs' is indistinguishable from
        'hides everything', and the tile would be silently useless."""
        self._start("live-1", user="Alex", stage="Tool step 1/12: search_docs")
        body = self._tile()
        self.assertEqual(body["state"], "thinking")
        self.assertEqual(body["active"], 1)
        self.assertEqual(body["who"], "Alex")

    def test_a_finished_run_disappears_immediately_even_though_status_still_says_running(self):
        self._start("dead-1", user="Alex", stage="Tool step 1/12: search_docs")
        cancellation.end("dead-1")          # the `finally` every exit path runs

        self.assertEqual(self.registry.get("dead-1").status, "running",
                         "precondition: the registry entry is still 'running', which is exactly "
                         "the stale signal this feature must not trust")
        body = self._tile()
        self.assertEqual(body["state"], "idle")
        self.assertEqual(body["active"], 0)

    def test_a_cancel_handle_with_no_registry_entry_reads_as_starting(self):
        """`begin()` runs on the request thread before the worker registers -- a real, brief state."""
        cancellation.begin("early-1")
        body = self._tile()
        self.assertEqual(body["state"], "thinking")
        self.assertEqual(body["stage"], "Starting…")


class TheTileIsReadableOnAPhone(_ThinkingCase):
    """Correct data is not automatically a usable row."""

    def test_it_shows_the_workers_own_message_verbatim(self):
        self._start("m-1", stage="Think Really Hard: refining (round 2)")
        self.assertEqual(self._tile()["stage"], "Think Really Hard: refining (round 2)")

    def test_a_long_message_is_capped_for_the_narrow_case(self):
        long = ("⚠ Mothership's GPU is mid-render/training — answering on BigHonker's 8B; "
                "this will be a shorter answer than usual")
        self._start("m-2", stage=long)
        stage = self._tile()["stage"]
        self.assertLessEqual(len(stage), app_module._THINKING_STAGE_MAX)
        self.assertTrue(stage.endswith("…"))

    def test_a_registered_run_with_no_message_yet_never_renders_blank(self):
        self._start("m-3", user="Alex")
        self.assertEqual(self._tile()["stage"], "Starting…")

    def test_idle_fills_every_cell_so_no_row_renders_empty(self):
        body = self._tile()
        self.assertEqual(body["state"], "idle")
        for field in ("who", "stage", "elapsed"):
            self.assertTrue(str(body[field]).strip(),
                            "%s renders as an empty row when idle" % field)

    def test_the_oldest_run_leads_and_the_rest_are_counted(self):
        self._start("old-1", user="Alex", stage="Tool step 9/12: get_document")
        self._start("new-1", user="Jordan", stage="Selecting the best available local model")
        self.registry.age_entry("old-1", 120)

        body = self._tile()
        self.assertEqual(body["active"], 2)
        self.assertEqual(body["stage"], "Tool step 9/12: get_document",
                         "the longest-running query is the one a human is waiting on")
        self.assertEqual(body["who"], "Alex +1 more")
        self.assertGreaterEqual(body["elapsed_s"], 120)

    def test_elapsed_reads_as_a_duration_not_a_raw_float(self):
        self.assertEqual(app_module._elapsed_h(42), "42s")
        self.assertEqual(app_module._elapsed_h(190), "3m 10s")
        self.assertEqual(app_module._elapsed_h(3900), "1h 05m")


class TelemetryNeverBreaksTheQuery(_ThinkingCase):
    """A status board must never take down the work it is watching."""

    def test_a_failing_recorder_still_forwards_the_message(self):
        boom = mock.Mock()
        boom.set_stage.side_effect = RuntimeError("registry exploded")
        seen = []
        emit = app_module._recording_status("x-1", boom, seen.append)
        emit("Tool step 1/12: search_docs")          # must not raise
        self.assertEqual(seen, ["Tool step 1/12: search_docs"])

    def test_set_stage_on_an_unknown_run_is_ignored_not_raised(self):
        self.assertFalse(self.registry.set_stage("nope", "anything"))

    def test_an_empty_message_does_not_overwrite_real_progress(self):
        self._start("keep-1", stage="Tool step 4/12: search_docs")
        self.assertFalse(self.registry.set_stage("keep-1", "   "))
        self.assertEqual(self.registry.get("keep-1").stage, "Tool step 4/12: search_docs")

    def test_the_snapshot_hands_out_copies_not_live_registry_entries(self):
        self._start("copy-1", user="Alex", stage="Tool step 1/12: search_docs")
        rows = self.registry.in_flight_snapshot()
        rows[0]["stage"] = "MUTATED"
        self.assertEqual(self.registry.get("copy-1").stage, "Tool step 1/12: search_docs")


if __name__ == "__main__":
    unittest.main()
