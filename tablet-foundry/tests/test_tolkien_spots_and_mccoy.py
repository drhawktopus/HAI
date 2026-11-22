"""Generalizing DOWN: Tolkien is the Fellowship's scheduler - the same shape as Starfleet one
level down - and ONE McCoy advises both levels.

- Tolkien REPORTS what he has free on every run-state push (and re-pushes the moment it changes); Spock decides.
- Tolkien ASKS McCoy his own questions (worker, tries, stay or go up) through Kirk's door, in SHADOW: background,
  logged beside what Tolkien decided, never waited on, never acted on, and McCoy is never shown the decision."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as TF
import tablet_runner as TR


class TheFreeSpotReport(unittest.TestCase):
    def setUp(self):
        with TR.NODES_LK:
            TR.NODE_HOLDER.clear()
        self.addCleanup(TR.NODE_HOLDER.clear)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.queued = []
        for p in (mock.patch.object(TR, "_SCHED_TABLETS", {"h1": {}, "h2": {}, "h3": {}}),
                  mock.patch.object(TR, "queued", side_effect=lambda: list(self.queued)),
                  mock.patch.object(TR, "read_reservation", return_value=None),
                  mock.patch.object(TR, "log")):
            p.start()
            self.addCleanup(p.stop)

    def _rigby_piece(self, name):
        path = os.path.join(self.tmp, name + ".tjob.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"kind": "llm", "name": name, "arms": ["rigby"], "model": "r.gguf", "payload": "/p.py",
                       "tasks": ["t"]}, f)
        self.queued.append(path)
        return path

    def test_it_counts_free_tablets_rigby_and_rigbys_line(self):
        TR.take_nodes("other", ["h1"])
        running = self._rigby_piece("running")
        self._rigby_piece("waiting-1")
        self._rigby_piece("waiting-2")
        with mock.patch.object(TR, "RIGBY_RUNNING", [TR.order_stem(running)]), TR.RIGBY_LOCK:
            cap = TR.fleet_capacity()
        self.assertEqual(cap, {"nodes": 3, "free_nodes": 2, "rigby_busy": True, "rigby_line": 2})

    def test_an_idle_fellowship_reports_everything_free(self):
        self.assertEqual(TR.fleet_capacity(), {"nodes": 3, "free_nodes": 3, "rigby_busy": False, "rigby_line": 0})


class ThePushCarriesIt(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.cap = {"nodes": 3, "free_nodes": 3, "rigby_busy": False, "rigby_line": 0}
        for p in (mock.patch.dict(TF._PUB, {"fleet": None, "rigby": None}),
                  mock.patch.object(TF, "CAPACITY", [lambda: dict(self.cap)]),
                  mock.patch.object(TF, "_CAP_LAST", [None])):
            p.start()
            self.addCleanup(p.stop)

        def urlopen(req, timeout=None):
            self.sent.append(json.loads(req.data.decode("utf-8")))
            r = mock.MagicMock()
            r.__enter__.return_value.read.return_value = b'{"ok": true}'
            r.__enter__.return_value.status = 200
            return r
        p = mock.patch("urllib.request.urlopen", side_effect=urlopen)
        p.start()
        self.addCleanup(p.stop)

    def test_every_push_says_what_is_free(self):
        TF.publish(name="", state="idle")
        self.assertEqual(self.sent[-1]["capacity"], self.cap)

    def test_a_change_re_pushes_at_once_and_no_change_pushes_nothing(self):
        TF.publish(name="", state="idle")
        n = len(self.sent)
        self.assertFalse(TF.push_capacity_if_changed())
        self.assertEqual(len(self.sent), n)
        self.cap["rigby_busy"] = True
        self.assertTrue(TF.push_capacity_if_changed())
        self.assertTrue(self.sent[-1]["capacity"]["rigby_busy"])

    def test_cannot_say_sends_no_report_never_a_made_up_all_free(self):
        with mock.patch.object(TF, "CAPACITY", [None]):
            TF.publish(name="", state="idle")
        self.assertNotIn("capacity", self.sent[-1])


class McCoyAdvisesTolkienInShadow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.log = os.path.join(self.d, "mccoy_shadow.jsonl")
        for p in (mock.patch.object(TF, "MCCOY_SHADOW_LOG", self.log), mock.patch.object(TF, "P20_TOKEN", "tok"),
                  mock.patch.object(TF, "TOLKIEN_MCCOY", "shadow")):
            p.start()
            self.addCleanup(p.stop)

    def _rows(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_the_advice_is_logged_beside_tolkiens_decision(self):
        spec = {"kind": "llm", "name": "turn", "request": "what is 17 times 23?",
                "placement": {"family": "chat-turn", "tier": "fellowship", "lens": "fastest"}}
        decided = {"worker": ["rigby"], "hosts": ["rigby"], "tries": 1, "escalate": "stay"}
        advice = {"advice": {"worker": {"choice": "rigby", "confidence": 0.8}}, "mode": "shadow"}
        with mock.patch.object(TF, "_desk_post", return_value=advice) as post:
            TF.mccoy_shadow(spec, decided).join(5)
        path, payload = post.call_args[0]
        self.assertEqual(path, "/mccoy/tolkien")
        self.assertIn("what is 17 times 23?", payload["text"])
        self.assertNotIn("rigby", payload["text"], "McCoy is never shown Tolkien's decision")
        row = self._rows()[0]
        self.assertEqual((row["order"], row["decided"], row["advice"]["worker"]["choice"]),
                         ("turn", decided, "rigby"))

    def test_a_failure_is_a_logged_row_never_an_exception(self):
        with mock.patch.object(TF, "_desk_post", side_effect=OSError("kirk down")):
            TF.mccoy_shadow({"name": "x"}, {}).join(5)
        self.assertIn("kirk down", self._rows()[0]["error"])

    def test_off_asks_nothing(self):
        with mock.patch.object(TF, "TOLKIEN_MCCOY", "off"), mock.patch.object(TF, "_desk_post") as post:
            self.assertIsNone(TF.mccoy_shadow({"name": "x"}, {}))
        post.assert_not_called()

    def test_a_fleet_order_asks_once_it_holds_its_tablets(self):
        calls = []

        def fake_run_one(path, waiting):
            TR.take_nodes(TR.order_stem(path), ["h1"])
        with mock.patch.object(TR, "run_one", side_effect=fake_run_one), mock.patch.object(TR, "log"), \
                mock.patch.object(TR, "_spec_or_none", return_value={"name": "o", "replicates": 1}), \
                mock.patch.object(TR, "tablet_class", return_value="A"), \
                mock.patch.object(TR, "mccoy_shadow", side_effect=lambda s, d: calls.append(d)):
            TR.launch_fleet_order("/q/o.tjob.json", {})
        TR.give_back("o")
        self.assertEqual(calls, [{"worker": ["tablet-A"], "hosts": ["h1"], "tries": 1, "escalate": "stay"}])


if __name__ == "__main__":
    unittest.main()
