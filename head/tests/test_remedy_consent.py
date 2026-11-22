"""Tier B consent flow end-to-end inside Kirk (10d): the producer files beside the DM, every
surface converges on remedy_decide_dict, approval re-verifies against the DECK's own derivation,
and execution queues the desk control the factory will collect.

The gate env sets HAI_NOTES_DIR so the watch loop thread never starts here (the suite-DM scar);
every tick is a direct call, every DM lands in a list, and both stores are temp dirs.
"""
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import app
import p20_queue
import remedies
import tools

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


def _tile(state="training", order="run_x", health="watching", stale=False):
    return {"state": state, "order": order, "health": health, "health_detail": "", "stale": stale}


class _Harness(unittest.TestCase):
    def setUp(self):
        self.rdir = tempfile.mkdtemp(prefix="remedy-consent-")
        self.qdir = tempfile.mkdtemp(prefix="p20q-consent-")
        self._patches = [
            mock.patch.object(app, "REMEDIES", remedies.RemedyQueue(self.rdir)),
            mock.patch.object(p20_queue, "QUEUE_DIR", self.qdir),
            mock.patch.object(p20_queue, "CONTROL_DIR", os.path.join(self.qdir, "controls")),
        ]
        for p in self._patches:
            p.start()
        self.dms = []
        self._dm = app._overnight_dm_send
        app._overnight_dm_send = lambda m: self.dms.append(m)
        fd, self._wfile = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self._wfile)
        self._worig = app.FLEET_WATCH_FILE
        app.FLEET_WATCH_FILE = self._wfile
        app._AWAKE_CACHE.clear()

    def tearDown(self):
        app._overnight_dm_send = self._dm
        app.FLEET_WATCH_FILE = self._worig
        for p in self._patches:
            p.stop()
        for d in (self.rdir, self.qdir):
            shutil.rmtree(d, ignore_errors=True)
        if os.path.exists(self._wfile):
            os.unlink(self._wfile)


class Producer(_Harness):
    """_watch_tick files a stop proposal beside a wedge verdict — and ONLY there."""

    def _arm_problem(self, tile):
        with mock.patch.object(app, "_factory_view", return_value=tile):
            return app.fleet_watch_arm("mothership", "problem", 120, "night watch", "Alex")

    def test_wedge_verdict_files_a_stop_proposal_and_names_it_in_the_dm(self):
        out = self._arm_problem(_tile())
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="wedged", order="run_x", health="DOWN")):
            app._watch_tick()
        pending = app.REMEDIES.list()
        self.assertEqual(len(pending), 1)
        rec = pending[0]
        self.assertEqual((rec["kind"], rec["host"], rec["order"]),
                         ("stop", "mothership", "run_x"))
        self.assertEqual(rec["watch_id"], out["id"])
        self.assertIn(rec["id"], self.dms[0], "the DM must NAME the proposal id")
        self.assertIn("PROPOSED", self.dms[0])
        ttl_m = (rec["expires_epoch"] - time.time()) / 60
        self.assertAlmostEqual(ttl_m, 120, delta=2)

    def test_non_wedge_problem_files_nothing(self):
        """DOWN health without a wedge: a stop slip needs a LIVE consumer — proposing one for a
        dead runner would be a control that appears to exist and does not act."""
        self._arm_problem(_tile())
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="training", health="DOWN")), \
             mock.patch.object(app, "_host_awake", return_value=True):
            app._watch_tick()
        self.assertEqual(app.REMEDIES.list(), [])
        self.assertIn("FIRED", self.dms[0], "the verdict DM itself must still go out")

    def test_asleep_host_offers_wake_in_the_dm_but_files_nothing(self):
        """wake was CUT from the whitelist (already an ungated tool) — the DM offers it."""
        self._arm_problem(_tile())
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="training", health="DOWN")), \
             mock.patch.object(app, "_host_awake", return_value=False):
            app._watch_tick()
        self.assertEqual(app.REMEDIES.list(), [])
        self.assertIn("asleep", self.dms[0])

    def test_producer_failure_never_eats_the_verdict_dm(self):
        self._arm_problem(_tile())
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="wedged", order="run_x")), \
             mock.patch.object(app.REMEDIES, "submit", side_effect=RuntimeError("disk full")):
            app._watch_tick()
        self.assertEqual(len(self.dms), 1)
        self.assertIn("FIRED", self.dms[0])


class Decision(_Harness):
    """remedy_decide_dict: the one path every surface calls."""

    def _file(self, kind="stop", order="run_x"):
        return app.REMEDIES.submit(kind, "mothership", order, "wedged at step 2088",
                                   "fleet-watch", ttl_seconds=600)

    def test_non_admin_cannot_decide(self):
        rec = self._file()
        out = app.remedy_decide_dict(rec["id"], "Riley", "approve")
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("gate"), "admin")
        self.assertEqual(app.REMEDIES.get(rec["id"])["status"], "pending")

    def test_approve_still_present_queues_the_desk_control(self):
        rec = self._file()
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="wedged", order="run_x")):
            out = app.remedy_decide_dict(rec["id"], "Alex", "approve")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["remedy"]["status"], "approved")
        ctls = p20_queue.pending_controls("mothership")
        self.assertEqual(len(ctls), 1)
        self.assertEqual((ctls[0]["action"], ctls[0]["order"]), ("stop", "run_x"))
        self.assertIn("remedy:%s" % rec["id"], ctls[0]["source"])

    def test_approve_after_self_resolution_fires_nothing(self):
        """The bedtime ruling's safety half: the 3 AM problem cleared itself; the 8 AM yes must
        decline politely instead of firing on a ghost."""
        rec = self._file()
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="idle", order="")):
            out = app.remedy_decide_dict(rec["id"], "Alex", "approve")
        self.assertTrue(out["ok"])
        self.assertEqual(out["remedy"]["status"], "resolved_self")
        self.assertEqual(p20_queue.pending_controls("mothership"), [])

    def test_approve_with_unreadable_deck_refuses_and_keeps_the_yes(self):
        rec = self._file()
        with mock.patch.object(app, "_factory_view", side_effect=RuntimeError("deck gone")):
            out = app.remedy_decide_dict(rec["id"], "Alex", "approve")
        self.assertFalse(out["ok"])
        self.assertEqual(app.REMEDIES.get(rec["id"])["status"], "pending")

    def test_requeue_verify_semantics_are_inverted(self):
        """For a requeue, 'still present' means the order is still ABSENT; an order already
        running again resolves self."""
        rec = self._file(kind="requeue")
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="training", order="run_x")):
            out = app.remedy_decide_dict(rec["id"], "Alex", "approve")
        self.assertEqual(out["remedy"]["status"], "resolved_self")
        rec2 = self._file(kind="requeue", order="run_z")
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="idle", order="")):
            out2 = app.remedy_decide_dict(rec2["id"], "Alex", "approve")
        self.assertEqual(out2["remedy"]["status"], "approved")
        ctl = p20_queue.pending_controls("mothership")[0]
        self.assertEqual(ctl["action"], "requeue")
        self.assertNotIn("force", ctl["payload"],
                         "an automated remedy must never carry force past a human cancel")


class Endpoints(_Harness):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(app.app)

    def test_admin_gate_on_all_three(self):
        self.assertEqual(self.client.get("/remedies?user=Riley").status_code, 403)
        self.assertEqual(self.client.post("/remedies/approve",
                                          json={"id": "x", "user": "Riley"}).status_code, 403)
        self.assertEqual(self.client.post("/remedies/reject",
                                          json={"id": "x", "user": "Riley"}).status_code, 403)

    def test_list_and_reject_roundtrip(self):
        rec = app.REMEDIES.submit("stop", "mothership", "run_x", "why", "fleet-watch")
        r = self.client.get("/remedies?user=Alex")
        self.assertEqual(r.status_code, 200)
        self.assertIn(rec["id"], [x["id"] for x in r.json()["remedies"]])
        r = self.client.post("/remedies/reject", json={"id": rec["id"], "user": "Alex"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["remedy"]["status"], "rejected")

    def test_gone_or_decided_maps_to_404_for_the_bot(self):
        r = self.client.post("/remedies/approve", json={"id": "rem-nope", "user": "Alex"})
        self.assertEqual(r.status_code, 404, "the bot renders 404 as 'already decided'")

    def test_unreadable_deck_maps_to_409_retryable(self):
        rec = app.REMEDIES.submit("stop", "mothership", "run_x", "why", "fleet-watch")
        with mock.patch.object(app, "_factory_view", side_effect=RuntimeError("deck gone")):
            r = self.client.post("/remedies/approve", json={"id": rec["id"], "user": "Alex"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(app.REMEDIES.get(rec["id"])["status"], "pending")


class ToolSurface(_Harness):
    def test_fleet_remedy_is_admin_only_and_registered(self):
        self.assertFalse(tools._allowed("fleet_remedy", "Riley"))
        self.assertTrue(tools._allowed("fleet_remedy", "Alex"))
        self.assertTrue(tools._allowed("fleet_remedy", "ClaudeSupervisor"))
        for lst in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            self.assertIn("fleet_remedy", {t["function"]["name"] for t in lst})

    def test_fleet_remedy_rides_the_core_floor(self):
        """'approve rem-1755...' is an opaque id with near-zero embedding signal — a consent
        surface that vanishes to narrowing is a consent surface that doesn't exist."""
        import tool_selection
        self.assertIn("fleet_remedy", tool_selection.CORE_TOOL_NAMES)

    def test_chat_approval_settles_the_same_store(self):
        rec = app.REMEDIES.submit("stop", "mothership", "run_x", "why", "fleet-watch")
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="wedged", order="run_x")):
            out = tools._fleet_remedy({"action": "approve", "id": rec["id"]}, "Alex")
        self.assertIn("Approved", out)
        self.assertEqual(app.REMEDIES.get(rec["id"])["status"], "approved",
                         "chat, web and Discord must all resolve the SAME record")

    def test_chat_propose_files_into_the_shared_store(self):
        out = tools._fleet_remedy({"action": "propose", "kind": "requeue",
                                   "host": "mothership", "order": "run_q",
                                   "why": "archived by a misfire"}, "Alex")
        self.assertIn("Filed", out)
        self.assertEqual(app.REMEDIES.list()[0]["order"], "run_q")


if __name__ == "__main__":
    unittest.main()
