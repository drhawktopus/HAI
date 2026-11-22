"""Chat window sizing uses the largest measured window that fits the card.\n\nIf no row fits, the turn is handed to the rest of the weighing (including Fellowship rows) before falling back to the hand-coded recovery list."""
import unittest
from unittest import mock

import app
import config
import scheduler


def cand(host, model, eligible=True, order=None, box=None):
    entry = {"model": model, "think": False}
    if order:
        entry["order"] = order
    return {"host": host, "box": box or host, "entry": entry, "eligible": eligible, "quality": 0.9, "time_s": 1.0,
            "j": 10.0, "role": "home"}


class _Live:
    """The card checks every candidate passes through, all clear - each test changes only the one it is about."""

    def setUp(self):
        self._p = [mock.patch.object(scheduler, "occupied_for", return_value=None),
                   mock.patch.object(app, "brain_host_busy", return_value=False),
                   mock.patch("comfy_client.render_busy", return_value=False),
                   mock.patch("ollama_client.reachable", return_value=True),
                   mock.patch.object(config, "ctx_for", return_value=262144)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()


class SizingLivesAtTheWindowDoorOnly(_Live, unittest.TestCase):
    """Sizing is delegated exclusively to `chat_window.sized_ctx`; chat paths must never compute window size locally."""

    def test_a_weighed_row_asks_for_the_full_measured_window(self):
        with mock.patch.object(scheduler, "window_ctx", side_effect=AssertionError("a path must not size on its own")):
            row = app._servable_gpu_row(None, cand(scheduler.BRAIN, "nemotron-3-nano:4b"), 1, "chat-c1", "8gb", True)
        self.assertEqual(row[2], 262144)

    def test_only_the_door_calls_the_sizing(self):
        import ast
        import pathlib
        callers = []
        for p in pathlib.Path(".").glob("*.py"):
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8-sig"))):     # a BOM'd module is still code
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "window_ctx":
                    callers.append(p.name)
        self.assertEqual(sorted(set(callers)), ["chat_window.py"], "one place sizes a chat window")


class FallbackWalksTheMenu(_Live, unittest.TestCase):
    def walk(self, band, tried):
        with mock.patch.object(scheduler, "window_ctx", side_effect=lambda h, m, c: (c, "fits")):
            return [(k, h) for k, _a, h in app._band_fallbacks(None, ("chat-c1", "8gb", True, band), set(tried))]

    def test_the_rest_of_the_weighing_in_order_fellowship_included(self):
        band = [cand(scheduler.BRAIN, "nemotron-3-nano:4b"),
                cand(scheduler.FELLOWSHIP_HOST, "Qwen3.6-35B-A3B", box="rigby", order={"model": "x"}),
                cand(scheduler.LEAD_BOX, "qwen3.8:27b")]
        self.assertEqual(self.walk(band, {scheduler.BRAIN}),
                         [("fellowship", scheduler.FELLOWSHIP_HOST), ("gpu", scheduler.LEAD_BOX)])

    def test_a_fellowship_row_without_order_fields_and_an_ineligible_box_are_passed_over(self):
        band = [cand(scheduler.FELLOWSHIP_HOST, "bare", box="rigby"),
                cand(scheduler.FAILOVER, "qwen3.5:9b", eligible=False),
                cand(scheduler.LEAD_BOX, "qwen3.8:27b")]
        self.assertEqual(self.walk(band, set()), [("gpu", scheduler.LEAD_BOX)])

    def test_no_weighing_walks_nothing(self):
        self.assertEqual(list(app._band_fallbacks(None, None, set())), [])


class WindowCtxReadsTheDesksOwnReport(unittest.TestCase):
    """scheduler.window_ctx asks the same report and arithmetic the desk's check at open uses (one derivation)."""
    FITS = {"rules": {"full_margin_mib": 1024, "shared_min_mib": 512}, "hosts": {"bighonker": {
        "nemotron-3-nano:4b": {"engine_version": "0.40.5", "points": [
            {"num_ctx": 229376, "required_mib": 5759, "shared_mib": 598, "card_mib": 8192},
            {"num_ctx": 262144, "required_mib": 6191, "shared_mib": 630, "card_mib": 8192}]}}}}

    def ask(self, ambient, age=5.0, model="nemotron-3-nano:4b"):
        rec = {"vram_used_mb": ambient, "ollama_version": "0.40.5", "stale": False,
               "gpu_memory": {"counter_ok": True, "card_mb": 8192, "adapter_mb": None, "dedicated_mb": None,
                              "shared_mb": None}}
        with mock.patch.object(scheduler, "policy_data", return_value={"model_fits": self.FITS}), \
                mock.patch.object(scheduler, "gpu_load", return_value=rec), \
                mock.patch.object(scheduler, "_report_age_s", return_value=age):
            return scheduler.window_ctx("bighonker", model, 262144)

    def test_the_locked_desktop_of_2026_10_25(self):
        self.assertEqual(self.ask(1061)[0], 229376)
        self.assertEqual(self.ask(330)[0], 262144)

    def test_a_stale_report_or_an_unprofiled_model_keeps_the_full_window_for_the_desk(self):
        self.assertEqual(self.ask(1061, age=10 ** 6)[0], 262144)
        self.assertEqual(self.ask(1061, model="unprofiled")[0], 262144)


class FellowshipServableIsOneTest(unittest.TestCase):
    def test_wants_and_the_fallback_share_it(self):
        import fellowship_turn
        ok = cand(scheduler.FELLOWSHIP_HOST, "m", box="rigby", order={"model": "m"})
        self.assertTrue(fellowship_turn.servable(ok))
        self.assertIs(fellowship_turn.wants([ok]), ok)
        self.assertFalse(fellowship_turn.servable(cand(scheduler.FELLOWSHIP_HOST, "m", box="rigby")))
        self.assertFalse(fellowship_turn.servable(cand(scheduler.BRAIN, "m", order={"model": "m"})))


if __name__ == "__main__":
    unittest.main()
