"""Tolkien is the Fellowship's scheduler (analogous to Starfleet one level down); McCoy advises both levels.

1. Free-spot data: run-state push carries {"nodes", "free_nodes", "rigby_busy", "rigby_line"}; scheduler.fellowship_load
   uses it. weigh_rows must price a busy rigby's line as wait time, not just availability, to correctly select in-band
   overflow over queued external workers.
2. /mccoy/tolkien: uses the same mccoy._ask call as triage. Shadow mode: does not act until questions are measured."""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

import app
import config
import fellowship_turn
import mccoy
import scheduler


def _resp(answers):
    r = mock.Mock()
    r.json.return_value = {"answers": answers}
    return r


class TolkiensDoorToMcCoy(unittest.TestCase):
    GOOD = {"worker": {"choice": "tablet-A", "confidence": 0.71}, "tries": {"choice": "1", "confidence": 0.8},
            "escalate": {"choice": "stay", "confidence": 0.9}}

    def test_his_questions_go_on_the_same_placed_kept_loaded_tracked_call(self):
        with mock.patch("scheduler.place", return_value=[("softserve", 50.0, "home")]) as place, \
                mock.patch.object(mccoy.ollama_client, "post_supervised", return_value=_resp(self.GOOD)) as post, \
                mock.patch.object(mccoy.ollama_client, "track") as track, \
                mock.patch.object(mccoy.config, "MCCOY_RESIDENT_HOSTS", {"softserve"}):
            out = mccoy.advise_tolkien("a one-line chat turn")
        place.assert_called_once_with("advisor", lend=False, only=())
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["questions"], mccoy.TOLKIEN_QUESTIONS)
        self.assertEqual(body["keep_alive"], -1, "the same kept-loaded McCoy on Softserve")
        self.assertEqual(track.call_args.kwargs["purpose"], "mccoy advises tolkien")
        self.assertEqual(track.call_args.kwargs["kind"], "advisor")
        self.assertEqual(out["advice"]["worker"]["choice"], "tablet-A")
        self.assertEqual(out["mode"], "shadow")

    def test_triage_still_asks_its_own_measured_questions(self):
        ans = {"tier": {"choice": mccoy.TIERS[0], "confidence": 0.95}}
        with mock.patch("scheduler.place", return_value=[("softserve", 50.0, "")]), \
                mock.patch.object(mccoy.ollama_client, "post_supervised", return_value=_resp(ans)) as post, \
                mock.patch.object(mccoy.ollama_client, "track"):
            mccoy.triage("hello")
        self.assertEqual(post.call_args.kwargs["json"]["questions"], mccoy.QUESTIONS)

    def test_a_choice_outside_his_questions_is_an_error(self):
        bad = dict(self.GOOD, worker={"choice": "mothership", "confidence": 0.9})
        with mock.patch("scheduler.place", return_value=[("softserve", 50.0, "")]), \
                mock.patch.object(mccoy.ollama_client, "post_supervised", return_value=_resp(bad)), \
                mock.patch.object(mccoy.ollama_client, "track"):
            with self.assertRaises(ValueError):
                mccoy.advise_tolkien("x")

    def test_the_door_answers_deferred_or_error_and_never_raises(self):
        req = app.McCoyTolkienReq(text="x")
        with mock.patch.object(app, "_p20_auth"):
            with mock.patch.object(mccoy, "advise_tolkien", side_effect=mccoy.AdvisorUnplaced("no card")):
                self.assertEqual(app.mccoy_tolkien(req, None), {"deferred": "no card"})
            with mock.patch.object(mccoy, "advise_tolkien", side_effect=RuntimeError("boom")):
                self.assertIn("boom", app.mccoy_tolkien(req, None)["error"])
            with mock.patch.object(mccoy, "advise_tolkien", return_value={"advice": {}}):
                self.assertEqual(app.mccoy_tolkien(req, None), {"advice": {}})

    def test_every_question_has_choices(self):
        for k, q in mccoy.TOLKIEN_QUESTIONS.items():
            self.assertEqual(q["type"], "choice", k)
            self.assertGreaterEqual(len(q["criteria"]), 2, k)


class MachineUsersNeedNamingEvenUnderStar(unittest.TestCase):
    """MCCOY_ACTIVE_USERS must explicitly list machine users (e.g., ClaudeSupervisor) alongside '*'; the star wildcard alone does not activate machine users due to special_route rule 7."""

    class Q:
        user, routing, effort_mode, persona, mode = "ClaudeSupervisor", "auto", "normal", "", ""
        absurd_max_seconds = external_agent = external_use = None

    def test_star_alone_does_not_make_a_machine_user_active(self):
        with mock.patch.object(config, "MCCOY_ACTIVE_USERS", {"*"}), \
                mock.patch.object(config, "MACHINE_USERS", {"ClaudeSupervisor"}):
            self.assertEqual(mccoy.special_route("One line: name a colour.", self.Q()), "machine")

    def test_naming_it_beside_star_does(self):
        with mock.patch.object(config, "MCCOY_ACTIVE_USERS", {"*", "ClaudeSupervisor"}), \
                mock.patch.object(config, "MACHINE_USERS", {"ClaudeSupervisor"}):
            self.assertIsNone(mccoy.special_route("One line: name a colour.", self.Q()))


class TheFreeSpotReportIsStored(unittest.TestCase):
    def test_typed_fields_kept_wrong_types_dropped(self):
        self.assertEqual(app._clean_capacity({"nodes": 10, "free_nodes": 9, "rigby_busy": True, "rigby_line": 2,
                                              "junk": 1}),
                         {"nodes": 10, "free_nodes": 9, "rigby_busy": True, "rigby_line": 2})
        self.assertEqual(app._clean_capacity({"free_nodes": True, "rigby_busy": 1}), None,
                         "a bool is not a count and 1 is not a bool")
        self.assertIsNone(app._clean_capacity(None))

    def test_the_push_writes_it_on_the_fellowships_record(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "fleet-run-state.json")
        with mock.patch.object(app, "FLEET_RUN_STATE", path), mock.patch.object(scheduler, "claim"), \
                mock.patch.object(app.notes, "log"):
            app.fleet_run_state(app.RunStateReq(host="fellowship", name="", state="idle", ttl=180,
                                                capacity={"nodes": 10, "free_nodes": 10, "rigby_busy": False,
                                                          "rigby_line": 0}))
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)["fellowship"]
        self.assertEqual(rec["capacity"]["free_nodes"], 10)
        self.assertEqual(rec["ttl"], 180)


class SpockWeighsTolkiensFreeSpots(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "fleet-run-state.json")
        p = mock.patch.object(scheduler, "FELLOWSHIP_PENDING", [None])
        p.start()
        self.addCleanup(p.stop)

    def _push(self, cap, age_s=5):
        ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_s)).isoformat()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"fellowship": {"host": "fellowship", "name": "", "state": "idle", "ts": ts, "ttl": 180,
                                      "capacity": cap}}, f)

    def test_a_busy_rigby_and_its_line_are_turns_ahead(self):
        self._push({"nodes": 10, "free_nodes": 10, "rigby_busy": True, "rigby_line": 2})
        load = scheduler.fellowship_load(self.path)
        self.assertEqual(load["ahead"], 3)
        self.assertEqual(load["free_nodes"], 10)

    def test_a_stale_or_missing_report_says_so_and_counts_nothing(self):
        self._push({"nodes": 10, "free_nodes": 0, "rigby_busy": True, "rigby_line": 5}, age_s=900)
        load = scheduler.fellowship_load(self.path)
        self.assertEqual((load["ahead"], load["free_nodes"]), (0, None))
        self.assertIn("stale", load["why"])
        self.assertIn("not reported", scheduler.fellowship_load(os.path.join(self.d, "absent.json"))["why"])

    def test_kirks_unanswered_turns_count_before_tolkien_has_collected_them(self):
        self._push({"nodes": 10, "free_nodes": 10, "rigby_busy": False, "rigby_line": 0})
        scheduler.FELLOWSHIP_PENDING[0] = lambda: 2
        self.assertEqual(scheduler.fellowship_load(self.path)["ahead"], 2)

    def _rows(self):
        return [{"box": "rigby", "model": "r.gguf", "quality": 1.0, "quality_low": 0.9, "s_per_solve": 1.9,
                 "order_overhead_s": 8.6, "role": "home", "j_per_solve": 100.0, "caveats": []},
                {"box": "fellowship", "model": "t-0.5b", "quality": 1.0, "quality_low": 0.9, "s_per_solve": 3.4,
                 "order_overhead_s": 28.6, "role": "home", "j_per_solve": None, "caveats": []},
                {"box": "bighonker", "model": "qwen3:8b", "quality": 1.0, "quality_low": 0.94, "s_per_solve": 1.7,
                 "role": "overflow", "j_per_solve": 300.0, "caveats": []}]

    def _weigh(self, load):
        price = {"usd_per_kwh": 0.11, "source": "test", "band": "normal"}
        with mock.patch.object(scheduler, "policy_rows", return_value=self._rows()), \
                mock.patch.object(scheduler, "_interactive_line", return_value=16.9), \
                mock.patch.object(scheduler, "_price_of_the_hour", return_value=price), \
                mock.patch.object(scheduler, "residency_cost", return_value=(0.0, "")), \
                mock.patch.object(scheduler, "fellowship_load", return_value=load), \
                mock.patch("ollama_client.models_resident", return_value=None):
            return scheduler.weigh_rows("chat-turn", "fellowship", interactive=True)

    def test_free_rigby_keeps_the_turn_home(self):
        band, _ = self._weigh({"ahead": 0, "free_nodes": 10, "why": "free"})
        self.assertEqual(band[0]["box"], "rigby")

    def test_a_busy_rigby_sends_the_next_turn_to_the_in_band_overflow(self):
        band, _ = self._weigh({"ahead": 1, "free_nodes": 10, "why": "Tolkien: rigby busy"})
        self.assertEqual(band[0]["box"], "bighonker", "rigby's wait put it past the line; the overflow row answers")
        rig = next(c for c in band if c["box"] == "rigby")
        self.assertTrue(rig["over"])
        self.assertTrue(any("rigby busy: 1 ahead" in n for n in rig["notes"]))

    def test_no_free_tablet_is_an_unknown_wait_never_inside_the_line(self):
        band, _ = self._weigh({"ahead": 0, "free_nodes": 0, "why": "Tolkien: 0 free"})
        tab = next(c for c in band if c["box"] == "fellowship")
        self.assertTrue(tab["over"])
        self.assertTrue(any("no free tablet" in n for n in tab["notes"]))


class TheOrderCarriesTheRequestForMcCoy(unittest.TestCase):
    def test_the_fellowship_form_carries_the_bare_words(self):
        class Q:
            user, chat_id = "ClaudeSupervisor", "t"
        with mock.patch.object(fellowship_turn, "_paths",
                               return_value=(tempfile.mkdtemp(), "/ssdshared/x", "p.py", "r.json")):
            body, _, _ = fellowship_turn.order_bodies(Q(), "what is 17 times 23?", "chat-turn", "fellowship", True)
        self.assertEqual(body["fellowship"]["spec"]["request"], "what is 17 times 23?")


class KirkCountsHisFellowshipTurns(unittest.TestCase):
    def test_a_turn_counts_while_it_waits_and_never_after_by_any_route(self):
        seen = []

        def fake_wait(oid, result, status=None, desk=None):
            seen.append(fellowship_turn.in_flight())
            raise RuntimeError("the desk vanished")
        cand = {"box": "rigby", "entry": {"model": "r.gguf"}, "time_s": 10.0, "j": None}
        with mock.patch.object(fellowship_turn, "render", return_value="hi"), \
                mock.patch.object(fellowship_turn, "submit", return_value=("oid1", "/r.json", "n1")), \
                mock.patch.object(fellowship_turn, "wait", side_effect=fake_wait), \
                mock.patch.object(fellowship_turn.notes, "log"):
            with self.assertRaises(RuntimeError):
                fellowship_turn.answer_turn(object(), [], cand, "chat-turn", "fellowship", True)
        self.assertEqual(seen, [1])
        self.assertEqual(fellowship_turn.in_flight(), 0)
        self.assertIs(scheduler.FELLOWSHIP_PENDING[0], fellowship_turn.in_flight,
                      "the weighing reads it through the hook the door registers")


if __name__ == "__main__":
    unittest.main()
