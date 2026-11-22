"""A person arriving behind a long job."""
import threading
import time
import unittest
from unittest import mock

import app as _app
import chat_window
import config
import ollama_client
import scheduler


class _Slot(unittest.TestCase):
    def setUp(self):
        self.slot = _app._LeadSlot()
        self.slot.POLL = 0.01

    def job(self, prio=False, run="run:J"):
        """A job holds the slot until `self.stop` is set, and hands over the way the chat loop does."""
        self.stop, self.back = threading.Event(), threading.Event()
        held = threading.Event()

        def body():
            ollama_client._CALL_CONTEXT.window_run = run
            self.slot.acquire(is_job=True, prio=prio)
            held.set()
            self.stop.wait(5)
            self.slot.yield_to_quick()
            self.back.set()
            self.slot.release()
        t = threading.Thread(target=body, daemon=True)
        t.start()
        self.assertTrue(held.wait(2))
        return t

    def person(self, is_job=False, prio=False):
        got = threading.Event()

        def body():
            self.slot.acquire(timeout=5, is_job=is_job, prio=prio)
            got.set()
            self.slot.release()
        t = threading.Thread(target=body, daemon=True)
        t.start()
        return t, got

    def wait_for(self, cond):
        for _ in range(300):
            if cond():
                return True
            time.sleep(0.01)
        return False


class APersonBehindAJob(_Slot):
    def test_its_turn_is_parked_and_the_person_goes_first_and_the_job_takes_the_card_back(self):
        parked = []
        with mock.patch.object(chat_window, "park_turn", side_effect=lambda run: parked.append(run) or "win"), \
                mock.patch.object(config, "CHAT_WINDOWS", True):
            j = self.job()
            p, got = self.person()
            self.assertTrue(self.wait_for(lambda: parked), "the person's arrival asked the job's turn to park")
            self.assertFalse(got.is_set(), "the card is still the job's until the cut lands")
            self.stop.set()
            self.assertTrue(got.wait(2))
            self.assertTrue(self.back.wait(2), "and the job goes on")
            for t in (j, p):
                t.join(2)
        self.assertEqual(parked, ["run:J"], "that run's turn, once")

    def test_it_keeps_asking_until_a_turn_is_in_flight(self):
        answers = iter([None, None, "win"])
        calls = []

        def park(run):
            calls.append(run)
            return next(answers, "win")
        with mock.patch.object(chat_window, "park_turn", side_effect=park), mock.patch.object(config, "CHAT_WINDOWS", True):
            j = self.job()
            p, _got = self.person()
            self.assertTrue(self.wait_for(lambda: self.slot._cut_asked))
            time.sleep(0.05)
            self.assertEqual(len(calls), 3, "asked until a turn was in flight, then not again")
            self.stop.set()
            for t in (j, p):
                t.join(2)

    def test_a_job_waiting_behind_a_job_parks_nothing(self):
        with mock.patch.object(chat_window, "park_turn") as park, mock.patch.object(config, "CHAT_WINDOWS", True):
            j = self.job()
            p, _got = self.person(is_job=True)
            self.assertTrue(self.wait_for(lambda: self.slot._queue))
            time.sleep(0.05)
            park.assert_not_called()
            self.stop.set()
            for t in (j, p):
                t.join(2)

    def test_a_priority_users_job_parks_only_for_a_priority_user(self):
        for prio, asked in ((False, False), (True, True)):
            self.slot = _app._LeadSlot()
            self.slot.POLL = 0.01
            with mock.patch.object(chat_window, "park_turn", return_value="win") as park, \
                    mock.patch.object(config, "CHAT_WINDOWS", True):
                j = self.job(prio=True)
                self.assertEqual(self.slot.can_park_for(prio), asked)
                p, _got = self.person(prio=prio)
                self.assertTrue(self.wait_for(lambda: self.slot._queue))
                time.sleep(0.05)
                self.assertEqual(park.called, asked)
                self.stop.set()
                for t in (j, p):
                    t.join(2)

    def test_a_person_holding_the_slot_is_never_parked(self):
        with mock.patch.object(chat_window, "park_turn") as park, mock.patch.object(config, "CHAT_WINDOWS", True):
            ollama_client._CALL_CONTEXT.window_run = "run:P"
            self.addCleanup(setattr, ollama_client._CALL_CONTEXT, "window_run", None)
            self.slot.acquire(is_job=False)
            self.assertFalse(self.slot.can_park_for(True))
            p, _got = self.person()
            self.assertTrue(self.wait_for(lambda: self.slot._queue))
            time.sleep(0.05)
            park.assert_not_called()
            self.slot.release()
            p.join(2)

    def test_the_notice_says_it_was_asked_and_the_eta_is_the_hand_over_not_the_turn(self):
        with mock.patch.object(chat_window, "park_turn", return_value="win"), mock.patch.object(config, "CHAT_WINDOWS", True), \
                mock.patch.object(_app, "_reroute_offer", return_value=""):
            j = self.job()
            before = self.slot.queue_line({"job": False}, 1)
            p, _got = self.person()
            self.assertTrue(self.wait_for(lambda: self.slot._cut_asked))
            line = self.slot.queue_line(self.slot._queue[0], 1)
            self.stop.set()
            for t in (j, p):
                t.join(2)
        self.assertIn("next step", before)
        self.assertIn("no estimate yet", before, "control: before the ask a slot that has seen nothing has no estimate")
        self.assertIn("asked to pause for you now", line)
        self.assertIn("any moment", line)


class AParkedTurnHandsOver(unittest.TestCase):
    class _Parked(ollama_client.httpx.ReadError):
        parked = True

    def run_chat(self, hook):
        left = [1]
        self.events = []

        def once(host, model, messages, *a, **k):
            self.events.append("ask")
            if left[0]:
                left[0] -= 1
                raise self._Parked("window w parked this turn mid-answer (parked by its caller); its state is saved")
            return {"message": {"role": "assistant", "content": "the whole answer"}}
        with mock.patch.object(ollama_client, "_chat_once", once), \
                mock.patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                mock.patch.object(ollama_client, "YIELD_HOOK", hook):
            return ollama_client.chat("h", "m", [{"role": "user", "content": "go"}], [])

    def test_before_it_is_asked_again(self):
        answer = self.run_chat(lambda status=None: self.events.append("hand over"))
        self.assertEqual(answer[0], "the whole answer")
        self.assertEqual(self.events, ["hand over", "ask", "hand over", "ask"],
                         "the first is the step's own point; the second is the one a person waits on")

    def test_a_failed_hand_over_never_ends_the_jobs_turn(self):
        def broken(status=None):
            self.events.append("hand over")
            if self.events.count("hand over") > 1:
                raise RuntimeError("the slot broke")
        self.assertEqual(self.run_chat(broken)[0], "the whole answer")


class AHotModelOnItsCard(unittest.TestCase):
    def lead(self, job, hot, **ctx):
        for k, v in ctx.items():
            setattr(ollama_client._CALL_CONTEXT, k, v)
            self.addCleanup(setattr, ollama_client._CALL_CONTEXT, k, None)
        with mock.patch.object(ollama_client, "_crown", return_value={"model": "flash", "box": "mothership"}), \
                mock.patch.object(ollama_client, "_crown_box_url", return_value=config.HEAVY_HOST), \
                mock.patch.object(config, "CHAT_WINDOWS", True), \
                mock.patch.object(chat_window, "has_window", return_value=hot) as has:
            return ollama_client.lead_model(config.HEAVY_HOST, config.HEAVY_MODEL, job), has

    def test_a_plain_chat_keeps_its_model_hot_or_not_the_weighing_decides(self):
        self.assertEqual(self.lead(False, True)[0], config.HEAVY_MODEL)
        self.assertEqual(self.lead(False, False)[0], config.HEAVY_MODEL)

    def test_a_job_leads_on_the_crown_either_way(self):
        self.assertEqual(self.lead(True, False)[0], "flash")

    def test_a_users_pin_keeps_its_model(self):
        self.assertEqual(self.lead(False, True, exact_model=True)[0], config.HEAVY_MODEL)

    def test_a_cold_card_is_charged_its_electricity_so_a_hot_row_beats_a_cheaper_cold_one(self):
        def row(box, model, j):
            return {"box": box, "model": model, "role": "home", "quality": 0.95, "quality_low": 0.9, "n": 100,
                    "s_per_solve": 2.0, "j_per_solve": j, "load_s": 30.0}
        rows = [row("bighonker", "hot", 100.0), row("softserve", "cheap", 80.0)]

        def order(cold):
            with mock.patch.object(scheduler, "policy_rows", return_value=rows), \
                    mock.patch.object(scheduler, "policy_data", return_value={}), \
                    mock.patch.object(ollama_client, "models_resident", return_value=[]), \
                    mock.patch.object(scheduler, "_price_of_the_hour",
                                      return_value={"usd_per_kwh": 0.1, "source": "test", "band": "normal"}), \
                    mock.patch.object(scheduler, "residency_cost",
                                      side_effect=lambda h, m, *a, **k: (30.0, "cold") if m in cold else (0.0, "resident")):
                band, _rejected = scheduler.weigh_rows("chat-c1", "8gb", interactive=False, cost_first=True)
            return [c["entry"]["model"] for c in band]
        self.assertEqual(order(()), ["cheap", "hot"], "control: both hot, the cheaper row first")
        self.assertEqual(order(("cheap",)), ["hot", "cheap"])


class TheMatrixDecidesWhereAPersonGoes(unittest.TestCase):
    """Jobs with parking capability bypass the weighted matrix and are routed via the comparable-box rule."""

    def serve(self, can_park):
        q = _app.Q(text="explain this", user="Avery", routing="auto")
        verdict = {"lane": "lead", "tier": "lead", "decided": True, "kind": "chat", "confidence": {}}
        sent = []
        with mock.patch("mccoy.verdict", return_value=verdict), \
                mock.patch("comfy_client.render_busy", return_value=False), \
                mock.patch.object(scheduler, "occupied_for", return_value=None), \
                mock.patch.object(config, "CHAT_WINDOWS", True), \
                mock.patch.object(chat_window, "serving_others", return_value=True), \
                mock.patch.object(_app._LEAD_LOCK, "can_park_for", return_value=can_park):
            res, lane, _v = _app._mccoy_serve(q, False, None, lambda: sent.append(1) or {"route": "lead"},
                                              weigh=lambda v: None)
        return res, lane, bool(sent)

    def test_a_job_that_can_park_sends_the_turn_to_the_weighed_walk_with_mothership_in_it(self):
        self.assertEqual(self.serve(True), (None, "weighed", False))

    def test_the_overflow_check_asks_about_the_box_by_the_name_windows_are_keyed_by(self):
        w = chat_window.Window(scheduler.LEAD_BOX, config.HEAVY_MODEL, 1000, "w1", "/nonexistent-dir", "d1")
        w.users = {"run:job"}
        q = _app.Q(text="explain this", user="Avery", routing="auto")
        verdict = {"lane": "lead", "tier": "lead", "decided": True, "kind": "chat", "confidence": {}}
        with mock.patch.dict(chat_window._WINDOWS, {w.key: w}, clear=True), \
                mock.patch.object(chat_window.Window, "ended", lambda self: False), \
                mock.patch("mccoy.verdict", return_value=verdict), \
                mock.patch("comfy_client.render_busy", return_value=False), \
                mock.patch.object(scheduler, "occupied_for", return_value=None), \
                mock.patch.object(config, "CHAT_WINDOWS", True), \
                mock.patch.object(_app._LEAD_LOCK, "can_park_for", return_value=False):
            _res, lane, _v = _app._mccoy_serve(q, False, None, lambda: {"route": "lead"}, weigh=lambda v: None)
        self.assertEqual(lane, "lead", "held by another run's window: overflow, not the lead's dispatch")

    def test_a_job_on_another_model_than_the_27b_is_still_asked_to_park(self):
        w = chat_window.Window(scheduler.LEAD_BOX, "flash", 1000, "hai-win-mothership-flash-1", "/nonexistent-dir", "d1")
        w.users = {"run:job"}
        q = _app.Q(text="explain this", user="Avery", routing="auto")
        verdict = {"lane": "lead", "tier": "lead", "decided": True, "kind": "chat", "confidence": {}}

        def serve(holder):
            with mock.patch.dict(chat_window._WINDOWS, {w.key: w}, clear=True), \
                    mock.patch.object(chat_window.Window, "ended", lambda self: False), \
                    mock.patch("mccoy.verdict", return_value=verdict), \
                    mock.patch("comfy_client.render_busy", return_value=False), \
                    mock.patch.object(scheduler, "occupied_for", return_value={"holder": holder, "resource": "gpu"}), \
                    mock.patch.object(_app, "_await_orphan_window", side_effect=lambda held, *a, **k: held), \
                    mock.patch.object(config, "CHAT_WINDOWS", True), \
                    mock.patch.object(_app._LEAD_LOCK, "can_park_for", return_value=True):
                return _app._mccoy_serve(q, False, None, lambda: {"route": "lead"}, weigh=lambda v: None)[1]
        self.assertEqual(serve("hai-win-mothership-flash-1_mothership"), "weighed", "our own job's window: the walk decides")
        self.assertEqual(serve("p20-render-order_mothership"), "lead", "someone else's order: overflow as before")

    def test_a_job_that_cannot_park_overflows_as_before_with_mothership_out(self):
        self.assertEqual(self.serve(False), (None, "lead", False))

    def test_a_card_held_by_a_jobs_window_takes_only_that_windows_model(self):
        """The hard constraint the walk meets: one model per card, and the job is still holding the card's window."""
        url = scheduler.CARDS[scheduler.LEAD_BOX]["url"]
        held = {"holder": "hai-win-mothership-flash-x_mothership", "resource": "gpu"}

        def row(model):
            c = {"host": scheduler.LEAD_BOX, "entry": {"model": model, "think": "on"}, "eligible": True, "quality": 1.0,
                 "time_s": 1.0, "j": 1.0}
            with mock.patch.object(scheduler, "claim_for_url", side_effect=lambda u, resource="gpu": held if resource == "gpu" else None), \
                    mock.patch.object(scheduler, "own_window", return_value="flash"), \
                    mock.patch.object(scheduler, "idle_own_window", return_value=None), \
                    mock.patch.object(scheduler, "render_busy", return_value=(False, "")), \
                    mock.patch("comfy_client.render_busy", return_value=False), \
                    mock.patch.object(ollama_client, "reachable", return_value=True):
                return _app._servable_gpu_row(_app.Q(text="x", user="Avery"), c, 1, "chat-c1", "lead", True)
        self.assertIsNone(row(config.HEAVY_MODEL), "the 27B would need the card the job holds")
        self.assertEqual(row("flash")[:2], (url, "flash"), "the window's own model joins it, which parks the job")


class EveryModelsRowsOnABox(unittest.TestCase):
    """A box row retains every model's winning config, as a model's best config can vary by temperature."""

    def table(self, *rows):
        return {"families": {"chat-c1": {"tiers": {"lead": {"fastest": list(rows), "least_energy": [], "best_quality": []}}}}}

    def test_two_settings_of_one_model_both_reach_the_weighing(self):
        a = {"box": "mothership", "model": "m", "think": "on", "temperature": "0.0"}
        b = {"box": "mothership", "model": "m", "think": "on", "temperature": "0.6"}
        with mock.patch.object(scheduler, "policy_data", return_value=self.table(a, b)):
            self.assertEqual([r["temperature"] for r in scheduler.policy_rows("chat-c1", "lead")], ["0.0", "0.6"])

    def test_a_cold_row_with_no_measured_load_is_never_the_fast_one(self):
        """A model with no measured load must be treated as 'not loaded' (or ineligible for the fast path), not priced as if it had load."""
        def row(model, s, load):
            return {"box": "mothership", "model": model, "role": "home", "quality": 0.9, "quality_low": 0.85, "n": 100,
                    "s_per_solve": s, "j_per_solve": 100.0, "load_s": load, "think": "on", "temperature": "0.6"}
        rows = [row("measured", 5.0, 10.0), row("unmeasured", 1.0, None)]

        def first(resident):
            with mock.patch.object(scheduler, "policy_rows", return_value=rows), \
                    mock.patch.object(scheduler, "policy_data", return_value={}), \
                    mock.patch.object(ollama_client, "models_resident", return_value=list(resident)), \
                    mock.patch.object(scheduler, "_price_of_the_hour",
                                      return_value={"usd_per_kwh": 0.1, "source": "test", "band": "normal"}):
                band, _r = scheduler.weigh_rows("chat-c1", "lead", interactive=False, mode="quick")
            return band[0]["entry"]["model"]
        self.assertEqual(first(["unmeasured"]), "unmeasured", "control: loaded, its own speed wins")
        self.assertEqual(first([]), "measured")

    def test_softserve_is_chats_last_resort_in_the_weighing_as_in_place(self):
        """Verify that the weighing's cost-first sort does not demote a high-quality chat model (e.g. Softserve) below a cheaper, lower-quality model (e.g. BigHonker) without explicit justification."""
        def row(box, j):
            return {"box": box, "model": "m-" + box, "role": "home", "quality": 0.9, "quality_low": 0.85, "n": 100,
                    "s_per_solve": 5.0, "j_per_solve": j, "load_s": 5.0, "think": "on", "temperature": "0.6"}
        rows = [row("softserve", 100.0), row("bighonker", 900.0)]

        def order(window_cost):
            with mock.patch.object(scheduler, "policy_rows", return_value=rows), \
                    mock.patch.object(scheduler, "policy_data", return_value={}), \
                    mock.patch.object(ollama_client, "models_resident", return_value=[]), \
                    mock.patch.object(scheduler, "_price_of_the_hour",
                                      return_value={"usd_per_kwh": 0.1, "source": "test", "band": "normal"}), \
                    mock.patch.object(chat_window, "has_window", return_value=False), \
                    mock.patch.object(chat_window, "first_output_s", return_value=(None, 0)), \
                    mock.patch.object(chat_window, "card_windows", return_value=[]):
                band, _r = scheduler.weigh_rows("chat-c1", "8gb", interactive=False, window_cost=window_cost, cost_first=True)
            return [c["box"] for c in band]
        self.assertEqual(order(False), ["softserve", "bighonker"], "control: not a chat turn, the cheaper row first")
        self.assertEqual(order(True), ["bighonker", "softserve"])

    def test_the_policy_tokens_take_a_hosts_first_row_not_its_last(self):
        first = {"box": "mothership", "model": "first-model", "think": "on"}
        second = {"box": "mothership", "model": "second-model", "think": "on"}
        with mock.patch.object(scheduler, "policy_data", return_value=self.table(first, second)), \
                mock.patch.object(scheduler, "factory_capabilities", return_value=set()):
            _spec, why = scheduler.resolve_policy_tokens({"model": "{POLICY_MODEL}"}, "mothership", "chat-c1", "lead", "fastest")
        self.assertIn("model.first-model", why or "")


if __name__ == "__main__":
    unittest.main()
