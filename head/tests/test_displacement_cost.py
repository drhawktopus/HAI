import unittest
from unittest import mock

import chat_window
import ollama_client
import scheduler

TABLE = {"families": {"chat-c1": {"tiers": {"lead": {"fastest": [
    {"box": "mothership", "model": "flash", "load_s": 40.0, "read_tok_s": 1000.0}], "least_energy": [], "best_quality": []}}}}}


def window(model, in_use=False, context_tokens=None):
    return {"model": model, "in_use": in_use, "context_tokens": context_tokens}


class WhatAPlacementDisplaces(unittest.TestCase):
    def cost(self, *held, table=TABLE, model="27b", solve=8.0):
        with mock.patch.object(chat_window, "card_windows", return_value=list(held)), \
                mock.patch.object(scheduler, "policy_data", return_value=table):
            return scheduler._displacement_cost("mothership", model, solve)

    def test_an_idle_window_of_another_model_pays_its_reload_and_its_re_read(self):
        s, notes, unknown = self.cost(window("flash", context_tokens=20000))
        self.assertAlmostEqual(s, 40.0 + 20.0)
        self.assertFalse(unknown)
        self.assertTrue(any("displaces flash" in n for n in notes) and any("re-read its 20000 tokens" in n for n in notes))

    def test_a_window_another_run_is_using_cannot_give_the_card_up_so_the_wait_is_unknown(self):
        s, notes, unknown = self.cost(window("flash", in_use=True))
        self.assertTrue(unknown, "never priced as a free card")
        self.assertTrue(any("serves another run" in n for n in notes))

    def test_joining_the_window_of_a_run_using_this_model_parks_that_run_for_this_turn(self):
        s, notes, unknown = self.cost(window("flash", in_use=True), model="flash", solve=9.5)
        self.assertEqual((s, unknown), (9.5, False))
        self.assertTrue(any("that run waits this turn" in n for n in notes))

    def test_a_reload_nobody_measured_is_said_and_never_charged(self):
        s, notes, _u = self.cost(window("other", context_tokens=500), table={"families": {}})
        self.assertEqual(s, 0.0)
        self.assertTrue(any("reload unmeasured" in n for n in notes) and any("re-read unmeasured" in n for n in notes))

    def test_card_windows_leave_out_the_asking_run_and_ended_windows(self):
        w = chat_window.Window("mothership", "flash", 1000, "w1", "/nonexistent-dir", "d1")
        w.users = {"run:me"}
        gone = chat_window.Window("mothership", "old", 1000, "w2", "/nonexistent-dir", "d2")
        with mock.patch.dict(chat_window._WINDOWS, {w.key: w, gone.key: gone}, clear=True), \
                mock.patch.object(chat_window.Window, "ended", lambda self: self.name == "w2"):
            self.assertEqual(chat_window.card_windows("mothership", exclude_run="run:me"),
                             [{"model": "flash", "in_use": False, "context_tokens": None}])
            self.assertTrue(chat_window.card_windows("mothership", exclude_run="run:other")[0]["in_use"])

    def test_the_weighing_charges_the_row_that_evicts_and_not_the_row_that_stays(self):
        def row(model, s):
            return {"box": "mothership", "model": model, "role": "home", "quality": 0.95, "quality_low": 0.9, "n": 100,
                    "s_per_solve": s, "j_per_solve": 100.0, "load_s": 10.0, "think": "on", "temperature": "0.6"}
        rows = [row("flash", 9.0), row("27b", 8.0)]
        with mock.patch.object(scheduler, "policy_rows", return_value=rows), \
                mock.patch.object(scheduler, "policy_data", return_value=TABLE), \
                mock.patch.object(ollama_client, "models_resident", return_value=["flash"]), \
                mock.patch.object(scheduler, "_price_of_the_hour",
                                  return_value={"usd_per_kwh": 0.1, "source": "test", "band": "normal"}), \
                mock.patch.object(chat_window, "has_window", side_effect=lambda h, m: m == "flash"), \
                mock.patch.object(chat_window, "first_output_s", return_value=(None, 0)), \
                mock.patch.object(chat_window, "card_windows", return_value=[window("flash", context_tokens=20000)]):
            band, _r = scheduler.weigh_rows("chat-c1", "lead", interactive=False, window_cost=True)
        t = {c["entry"]["model"]: c["time_s"] for c in band}
        self.assertAlmostEqual(t["flash"], 9.0, msg="it stays: nothing displaced, nothing loaded")
        self.assertAlmostEqual(t["27b"], 8.0 + 10.0 + 40.0 + 20.0, msg="its own load, then flash's reload and re-read")


class AStrataRowNeedsNoOllamaFitProfile(unittest.TestCase):
    def test_flash_next_is_weighed_while_an_unprofiled_ollama_model_is_set_aside(self):
        """Strata orders are exempt from the ollama fit-check requirement; unprofiled models (including Flash-Next) are set aside from weighing."""
        import config

        def row(model):
            return {"box": "mothership", "model": model, "role": "home", "quality": 0.95, "quality_low": 0.9, "n": 100,
                    "s_per_solve": 5.0, "j_per_solve": 100.0, "load_s": 10.0, "think": "on", "temperature": "0.6"}
        rows = [row("flash"), row("unprofiled"), row("profiled")]
        fits = {"hosts": {"mothership": {"profiled": {"points": []}}}}
        with mock.patch.object(scheduler, "policy_rows", return_value=rows), \
                mock.patch.object(scheduler, "policy_data", return_value={"model_fits": fits}), \
                mock.patch.object(ollama_client, "models_resident", return_value=[]), \
                mock.patch.object(scheduler, "_price_of_the_hour",
                                  return_value={"usd_per_kwh": 0.1, "source": "test", "band": "normal"}), \
                mock.patch.object(chat_window, "has_window", return_value=False), \
                mock.patch.object(chat_window, "first_output_s", return_value=(None, 0)), \
                mock.patch.object(chat_window, "card_windows", return_value=[]), \
                mock.patch.object(config, "swap_base", side_effect=lambda h, m: "http://swap" if m == "flash" else None):
            band, rejected = scheduler.weigh_rows("chat-c1", "lead", interactive=False, window_cost=True)
        self.assertEqual(sorted(c["entry"]["model"] for c in band), ["flash", "profiled"])
        self.assertEqual([c["entry"]["model"] for c in rejected], ["unprofiled"])


if __name__ == "__main__":
    unittest.main()
