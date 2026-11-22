"""Escalation feed module tests: pending listing, STALE auto-retire, ack round-trips,
runner-unreachable gracefulness, HUMAN-LEGIBLE notify_new, and endpoint admin gating.

No live probes – the coding client is mocked throughout.
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import app
import escalation_feed
import config as _config
import coding_client

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


class _Harness(unittest.TestCase):
    def setUp(self):
        self.sdir = tempfile.mkdtemp(prefix="esc-feed-")
        self._p = mock.patch.object(_config, "ESCALATION_STATE_DIR", self.sdir)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.sdir, ignore_errors=True)


_LIVE_TASK = {"status": "blocked", "objective": "some objective"}


class Pending(_Harness):
    """pending() surfaces undecided LIVE packets newest first; omits acked + STALE ones."""

    def _fake_runner_list(self, packets):
        return {"escalations": packets}

    def test_surfaces_ready_packets_newest_first(self):
        pkts = [
            {"id": "old", "status": "ready", "task_id": "t1", "workspace": "w1",
             "reason": "capability_limit", "requested_help": "need bigger model",
             "created_at": "2026-01-01T00:00:00Z"},
            {"id": "new", "status": "ready", "task_id": "t2", "workspace": "w2",
             "reason": "repeated_failure", "requested_help": "tests failing",
             "created_at": "2026-08-15T00:00:00Z"},
        ]
        with mock.patch.object(coding_client, "escalation",
                               return_value=self._fake_runner_list(pkts)), \
             mock.patch.object(coding_client, "task", return_value=_LIVE_TASK):
            result = escalation_feed.pending()
        self.assertEqual(len(result["packets"]), 2)
        self.assertEqual(result["packets"][0]["id"], "new")
        self.assertEqual(result["packets"][1]["id"], "old")

    def test_omits_resolved_packets(self):
        pkts = [
            {"id": "resolved", "status": "returned", "task_id": "t1", "workspace": "w1",
             "reason": "capability_limit", "requested_help": "help",
             "created_at": "2026-08-15T00:00:00Z"},
            {"id": "ready", "status": "ready", "task_id": "t2", "workspace": "w2",
             "reason": "capability_limit", "requested_help": "help",
             "created_at": "2026-08-15T00:00:00Z"},
        ]
        with mock.patch.object(coding_client, "escalation",
                               return_value=self._fake_runner_list(pkts)), \
             mock.patch.object(coding_client, "task", return_value=_LIVE_TASK):
            result = escalation_feed.pending()
        self.assertEqual(len(result["packets"]), 1)
        self.assertEqual(result["packets"][0]["id"], "ready")

    def test_omits_locally_acked_packets(self):
        with open(os.path.join(self.sdir, "acked.json"), "w") as f:
            json.dump({"ackd": "Alex"}, f)
        pkts = [
            {"id": "ackd", "status": "ready", "task_id": "t1", "workspace": "w1",
             "reason": "capability_limit", "requested_help": "help",
             "created_at": "2026-08-15T00:00:00Z"},
            {"id": "not_ackd", "status": "ready", "task_id": "t2", "workspace": "w2",
             "reason": "capability_limit", "requested_help": "help",
             "created_at": "2026-08-15T00:00:00Z"},
        ]
        with mock.patch.object(coding_client, "escalation",
                               return_value=self._fake_runner_list(pkts)), \
             mock.patch.object(coding_client, "task", return_value=_LIVE_TASK):
            result = escalation_feed.pending()
        self.assertEqual(len(result["packets"]), 1)
        self.assertEqual(result["packets"][0]["id"], "not_ackd")

    def test_auto_retires_packet_whose_task_is_dead(self):
        """A packet whose underlying task is cancelled is auto-retired: not surfaced, and acked so
        it never re-appears (this is the fix for the stale-escalation re-DM-on-restart spam)."""
        pkts = [
            {"id": "dead", "status": "ready", "task_id": "tdead", "workspace": "w",
             "reason": "capability_limit", "requested_help": "h", "created_at": "2026-08-01T00:00:00Z"},
            {"id": "live", "status": "ready", "task_id": "tlive", "workspace": "w",
             "reason": "capability_limit", "requested_help": "h", "created_at": "2026-08-02T00:00:00Z"},
        ]

        def task_get(action, *, task_id=None, **k):
            return {"status": "cancelled" if task_id == "tdead" else "blocked", "objective": "x"}

        with mock.patch.object(coding_client, "escalation",
                               return_value=self._fake_runner_list(pkts)), \
             mock.patch.object(coding_client, "task", side_effect=task_get):
            result = escalation_feed.pending()
        self.assertEqual([p["id"] for p in result["packets"]], ["live"])
        with open(os.path.join(self.sdir, "acked.json")) as f:
            acked = json.load(f)
        self.assertIn("dead", acked)            # auto-retired
        self.assertNotIn("live", acked)

    def test_auto_retires_packet_whose_worktree_is_finished(self):
        """The common terminal path: a job finished via task_finish keeps status 'active' and only
        sets worktree_finished. It is still dead per the runner's own predicate, so a status-only
        check would wrongly show it — this locks the worktree_finished half of the derivation."""
        pkts = [{"id": "wt", "status": "ready", "task_id": "twt", "workspace": "w",
                 "reason": "capability_limit", "requested_help": "h", "created_at": "2026-08-01T00:00:00Z"}]

        def task_get(action, *, task_id=None, **k):
            return {"status": "active", "worktree_finished": {"action": "discard"}, "objective": "x"}

        with mock.patch.object(coding_client, "escalation",
                               return_value=self._fake_runner_list(pkts)), \
             mock.patch.object(coding_client, "task", side_effect=task_get):
            result = escalation_feed.pending()
        self.assertEqual(result["packets"], [])
        with open(os.path.join(self.sdir, "acked.json")) as f:
            self.assertIn("wt", json.load(f))

    def test_unknown_task_state_shows_packet_fail_open(self):
        """If the task state can't be determined (runner error on task lookup), the escalation is
        SHOWN, never hidden — hiding a live blocker is the dangerous failure."""
        pkts = [{"id": "u", "status": "ready", "task_id": "t", "workspace": "w",
                 "reason": "capability_limit", "requested_help": "h",
                 "created_at": "2026-08-01T00:00:00Z"}]
        with mock.patch.object(coding_client, "escalation",
                               return_value=self._fake_runner_list(pkts)), \
             mock.patch.object(coding_client, "task", side_effect=RuntimeError("task api down")):
            result = escalation_feed.pending()
        self.assertEqual([p["id"] for p in result["packets"]], ["u"])
        # NOT retired (we could not prove it dead)
        self.assertFalse(os.path.exists(os.path.join(self.sdir, "acked.json")))

    def test_runner_unreachable_returns_error_marker_no_raise(self):
        with mock.patch.object(coding_client, "escalation",
                               side_effect=RuntimeError("network gone")):
            result = escalation_feed.pending()
        self.assertEqual(result["packets"], [])
        self.assertIn("error", result)

    def test_runner_returns_error_dict(self):
        with mock.patch.object(coding_client, "escalation",
                               return_value={"error": "runner down"}):
            result = escalation_feed.pending()
        self.assertEqual(result["packets"], [])
        self.assertIn("runner down", result["error"])


class AckRoundTrip(_Harness):
    """ack() persists through JSON; double-ack idempotent; unknown id named error."""

    def test_ack_persists_to_json(self):
        with mock.patch.object(coding_client, "escalation",
                               return_value={"id": "pkt1", "status": "ready"}):
            out = escalation_feed.ack("pkt1", "Alex")
        self.assertTrue(out["ok"])
        acked_path = os.path.join(self.sdir, "acked.json")
        with open(acked_path) as f:
            data = json.load(f)
        self.assertEqual(data["pkt1"], "Alex")

    def test_double_ack_is_idempotent(self):
        pkt_data = {"id": "pkt2", "status": "ready"}
        for _ in range(2):
            with mock.patch.object(coding_client, "escalation", return_value=pkt_data):
                out = escalation_feed.ack("pkt2", "Alex")
            self.assertTrue(out["ok"])
        acked_path = os.path.join(self.sdir, "acked.json")
        with open(acked_path) as f:
            data = json.load(f)
        self.assertEqual(data["pkt2"], "Alex")

    def test_unknown_id_returns_named_error(self):
        with mock.patch.object(coding_client, "escalation",
                               return_value={"error": "not found"}):
            out = escalation_feed.ack("nope-id", "Alex")
        self.assertFalse(out.get("ok"))
        self.assertIn("unknown", out["error"])


class NotifyNew(_Harness):
    """notify_new returns HUMAN-LEGIBLE DMs, each packet ONCE across calls given the seen-set."""

    def test_dm_is_human_legible_not_raw_code(self):
        pkts = [{"id": "a", "reason": "capability_limit", "objective": "Add a /projects endpoint",
                 "created": "2026-08-29T00:00:00+00:00"}]
        seen = set()
        new, dms = escalation_feed.notify_new(pkts, seen)
        self.assertEqual(len(new), 1)
        dm = dms[0]
        self.assertNotIn("capability_limit", dm)            # no raw machine code
        self.assertIn("Add a /projects endpoint", dm)       # what it was doing (the objective)
        self.assertIn("tools can't do", dm)                 # mapped plain reason
        self.assertIn("approve", dm.lower())                # how to respond
        self.assertIn("dismiss", dm.lower())

    def test_unknown_reason_falls_back_gracefully(self):
        pkts = [{"id": "z", "reason": "weird_new_code", "objective": "x"}]
        _, dms = escalation_feed.notify_new(pkts, set())
        self.assertIn("can't resolve", dms[0])

    def test_second_call_returns_nothing_when_all_seen(self):
        pkts = [{"id": "a", "reason": "capability_limit", "objective": "x"}]
        seen = set()
        escalation_feed.notify_new(pkts, seen)
        new2, dms2 = escalation_feed.notify_new(pkts, seen)
        self.assertEqual(new2, [])
        self.assertEqual(dms2, [])

    def test_humanize_age_is_deterministic_with_injected_now(self):
        now = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(escalation_feed._humanize_age("2026-08-27T12:00:00+00:00", now=now), "2 days")
        self.assertEqual(escalation_feed._humanize_age("2026-08-29T09:00:00+00:00", now=now), "3 hours")


class EndpointGating(unittest.TestCase):
    """Non-admin GET /escalations -> 403; admin succeeds."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(app.app)

    def test_non_admin_get_escalations_returns_403(self):
        r = self.client.get("/escalations?user=Riley")
        self.assertEqual(r.status_code, 403)

    def test_non_admin_post_ack_returns_403(self):
        r = self.client.post("/escalations/ack", json={"id": "x", "user": "Riley"})
        self.assertEqual(r.status_code, 403)

    def test_non_admin_get_detail_returns_403(self):
        r = self.client.get("/escalations/some-id?user=Riley")
        self.assertEqual(r.status_code, 403)

    def test_admin_get_escalations_returns_200(self):
        with mock.patch.object(escalation_feed, "pending", return_value={"packets": []}):
            r = self.client.get("/escalations?user=Alex")
        self.assertEqual(r.status_code, 200)

    def test_admin_get_detail_returns_200(self):
        with mock.patch.object(escalation_feed, "get", return_value={"id": "x"}):
            r = self.client.get("/escalations/x?user=Alex")
        self.assertEqual(r.status_code, 200)

    def test_admin_post_ack_returns_200(self):
        with mock.patch.object(escalation_feed, "ack", return_value={"ok": True}):
            r = self.client.post("/escalations/ack", json={"id": "x", "user": "Alex"})
        self.assertEqual(r.status_code, 200)


class NoToolsImport(unittest.TestCase):
    """Module must never import 'tools' (cycle risk)."""

    def test_no_tools_in_imports(self):
        with open(escalation_feed.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("import tools", source)
        self.assertNotIn("from tools", source)


if __name__ == "__main__":
    unittest.main()
