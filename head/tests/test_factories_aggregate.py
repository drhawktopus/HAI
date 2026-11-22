"""
Verify aggregate and tile agree on run liveness to prevent phantom incidents."""
import datetime
import unittest
from unittest import mock

import app


def _iso(seconds_ago):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=seconds_ago)).isoformat()


def _record(seconds_ago, state="training"):
    return {"host": "mothership", "name": "concept_flux2_v1", "state": state,
            "step": 120, "total_steps": 3000, "phase": "training",
            "ts": _iso(seconds_ago)}


def _patched(rec):
    class _FakeQueue:
        @staticmethod
        def gpu_status_hosts():
            return {}

        @staticmethod
        def gpu_status(host=None):
            return {}
    import sys
    return (
        mock.patch.object(app, "_fleet_run_all", return_value={"mothership": rec}),
        mock.patch.object(app, "_rescue_all", return_value={"mothership": {"ts": _iso(30), "period": 60, "health": "watching"}}),
        mock.patch.dict(sys.modules, {"p20_queue": _FakeQueue}),
    )


class AggregateTileAgreement(unittest.TestCase):
    def _both(self, rec):
        p1, p2, p3 = _patched(rec)
        with p1, p2, p3:
            agg = app.p20_factories()["factories"]["mothership"]
            tile = app._factory_view("mothership")
        return agg, tile

    def test_stale_record_is_demoted_in_both_views(self):
        """The phantom: a `training` record hours old. The tile already demoted it; the aggregate
        must agree — stale flag set, blank state, summary NOT naming the dead run."""
        agg, tile = self._both(_record(seconds_ago=7200))
        self.assertTrue(agg["run"]["stale"], "aggregate must mark the record stale")
        self.assertEqual(agg["run"]["state"], "")
        self.assertNotIn("concept_flux2_v1", agg["summary"],
                         "a dead run may not headline the tile summary")
        self.assertEqual(tile["state"], "idle")
        self.assertTrue(tile["stale"])

    def test_fresh_record_is_live_in_both_views(self):
        """POSITIVE CONTROL: a record seconds old renders as a live run in BOTH views — the
        demotion must not eat real work (the inverted failure would be the dangerous one)."""
        agg, tile = self._both(_record(seconds_ago=20))
        self.assertFalse(agg["run"]["stale"])
        self.assertEqual(agg["run"]["state"], "training")
        self.assertIn("concept_flux2_v1", agg["summary"])
        self.assertEqual(tile["state"], "training")
        self.assertIn("concept_flux2_v1", tile.get("order") or "")

    def test_aggregate_age_is_real_not_null(self):
        """The updated-vs-ts key bug made age_s structurally ALWAYS null. It must be a number."""
        agg, _ = self._both(_record(seconds_ago=7200))
        self.assertIsNotNone(agg["run"]["age_s"], "age must parse from the 'ts' the writer writes")
        self.assertGreater(agg["run"]["age_s"], 3600)

    def test_stale_record_does_not_count_as_running_for_health(self):
        """The knock-on: the health verdict read bool(name) off the demoted phantom and asserted a
        rescue posture for a run that ended long ago. A stale record is not a running one."""
        agg, _ = self._both(_record(seconds_ago=7200))
        health = agg.get("health") or {}
        self.assertNotIn("UNPROTECTED", (health.get("detail") or ""),
                         "no scare verdict off a stale record")



class WorkerRenderRows(unittest.TestCase):
    def tile(self, card, run=None):
        with mock.patch.object(app, "_fleet_run_all", return_value={"mothership": run} if run else {}), \
             mock.patch.object(app, "_rescue_all", return_value={}), \
             mock.patch("p20_queue.gpu_status", return_value=card), \
             mock.patch("scheduler.gpu_accounting_line", return_value="accounted"):
            return app._factory_view("mothership")

    def test_sampling_worker_fields_reach_existing_homepage_rows(self):
        t = self.tile({"state":"rendering","job_id":"abc123","phase":"Sampling", "age_s":0,
                       "step":3,"steps_total":8,"s_per_step":2.7,"remaining_secs":29})
        self.assertEqual((t["order"], t["detail"], t["progress"], t["step"]),
                         ("abc123", "Sampling", "3 / 8", "3 of 8"))
        self.assertEqual(t["pct"], 37.5)
        self.assertEqual(t["rate"], "2.7 s/step")
        self.assertEqual(t["remaining"], "~29s left")

    def test_encoding_is_visible_without_fabricated_sampling_progress(self):
        t = self.tile({"state":"rendering","job_id":"abc123","phase":"Encoding reference", "age_s":0,
                       "job_started_at":app.time.time()-40, "remaining_secs":None})
        self.assertEqual((t["order"], t["detail"]), ("abc123", "Encoding reference"))
        self.assertIn("elapsed",t["progress"])
        self.assertIsNone(t["pct"])
        self.assertEqual((t["rate"],t["remaining"]), ("", ""))

    def test_heartbeat_readers_keep_a_complete_record_during_publication(self):
        import tempfile, os, json
        import p20_queue
        with tempfile.TemporaryDirectory() as root, mock.patch.object(p20_queue, "QUEUE_DIR", root):
            p20_queue.set_gpu_status({"host":"mothership", "state":"rendering", "step":1})
            replace = os.replace
            seen = []
            def publish(source, target):
                with open(target, encoding="utf-8") as f: seen.append(json.load(f)["step"])
                with open(source, encoding="utf-8") as f: self.assertEqual(json.load(f)["step"],2)
                replace(source,target)
            with mock.patch.object(p20_queue.os,"replace",side_effect=publish):
                p20_queue.set_gpu_status({"host":"mothership", "state":"rendering", "step":2})
            self.assertEqual(seen,[1])
            self.assertEqual(p20_queue.gpu_status(host="mothership")["step"],2)

    def test_worker_does_not_replace_a_live_factory_order(self):
        t = self.tile({"state":"rendering","job_id":"abc123","phase":"Sampling", "age_s":0,
                       "step":3,"steps_total":8}, _record(seconds_ago=1))
        self.assertEqual(t["order"], "concept_flux2_v1")
        self.assertEqual((t["step_n"],t["total"]), (120,3000))


if __name__ == "__main__":
    unittest.main()
