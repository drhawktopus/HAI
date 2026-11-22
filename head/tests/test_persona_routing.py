"""A persona is the brief and the corpus, never a route, and history is compacted for the window that serves the chat."""
import os
import tempfile
import types
import unittest
from unittest import mock

import config
import history
import mccoy
import scheduler
import tools

import app


class _Q(types.SimpleNamespace):
    pass


def _nursing_q(**kw):
    base = dict(user="Riley", text="what reverses heparin", persona="nursing", mode="educator", routing="auto",
                effort_mode="normal", chat_id="c1", absurd_max_seconds=None, external_agent=None, external_use=None)
    base.update(kw)
    return _Q(**base)


class McCoyReadsNursingTurns(unittest.TestCase):
    def test_an_active_users_nursing_turn_is_triaged(self):
        with mock.patch.object(config, "MCCOY_MODE", "active"), \
                mock.patch.object(config, "MCCOY_ACTIVE_USERS", {"*"}), \
                mock.patch.object(mccoy, "_log"), \
                mock.patch.object(mccoy, "triage", return_value={"tier": "lead", "decided": True, "depth": "deep"}) as t:
            v = mccoy.verdict(_nursing_q())
        t.assert_called_once()
        self.assertEqual((v["lane"], v["depth"]), ("lead", "deep"))


class PersonaTurnsKeepTheirBrief(unittest.TestCase):
    def weigh(self, q, tier):
        seen = {}

        def placed(kind, family, tier_, **kw):
            seen.update(family=family, tier=tier_, exclude=kw.get("exclude"))
            return [], []
        with mock.patch.object(scheduler, "place_weighed", side_effect=placed):
            app._weigh_turn(q, {"tier": tier, "decided": True, "confidence": {}}, [{"role": "user", "content": "x"}])
        return seen

    def test_a_fellowship_sized_persona_turn_is_weighed_above_the_bare_rows(self):
        got = self.weigh(_nursing_q(), "fellowship")
        self.assertIn(scheduler.FELLOWSHIP_HOST, got["exclude"], "a Fellowship row drops the system prompt and tools")
        self.assertEqual(got["family"], scheduler.AUTO_TIER_FAMILY["8gb"])

    def test_a_lead_persona_turn_also_stays_off_the_bare_rows(self):
        self.assertIn(scheduler.FELLOWSHIP_HOST, self.weigh(_nursing_q(), "lead")["exclude"])

    def test_a_law_turn_too(self):
        self.assertIn(scheduler.FELLOWSHIP_HOST, self.weigh(_nursing_q(persona="law", mode=""), "lead")["exclude"])

    def test_a_plain_turn_keeps_the_fellowship(self):
        got = self.weigh(_nursing_q(persona="general", mode=""), "fellowship")
        self.assertNotIn(scheduler.FELLOWSHIP_HOST, got["exclude"])
        self.assertEqual(got["family"], scheduler.AUTO_TIER_FAMILY["fellowship"])

    def test_the_rows_a_nursing_turn_can_land_on_carry_its_evidence_tool(self):
        names = [t["function"]["name"] for t in tools.BRAIN_TOOLS]
        self.assertIn("search_evidence", names, "the 8 GB rows must be able to do what the nursing brief asks")


class EveryModelIsToldTheDate(unittest.TestCase):
    """Source-text check: the date is appended to the prompt in app.py, not injected via a mockable seam."""

    def setUp(self):
        with open("app.py", encoding="utf-8") as f:
            self.src = f.read()

    def test_it_rides_at_the_end_of_the_turn_so_the_prompt_still_extends_the_last_one(self):
        fold = self.src.index('user_content += "\\n\\n" + _ft_date.today_line().strip()')
        self.assertLess(self.src.index("if not ctx:\n        user_content = t"), fold, "appended, never the start of the turn")
        self.assertNotIn("today_line", self.src[self.src.index("base_system = ("):self.src.index("base_system = (") + 200])

    def test_the_critics_are_told_it_too(self):
        self.assertIn('_ft_date.today_line() + "Original task:', self.src)


class CompactionIsForTheServingWindow(unittest.TestCase):
    def setUp(self):
        self.user = "persona-routing-test"
        self.msgs = []
        self.state = {"summary": "", "covered_messages": 0}
        for target, value in (("load", lambda *a, **k: self.msgs), ("load_summary", lambda *a, **k: dict(self.state)),
                              ("resolve_chat", lambda *a, **k: "c1")):
            p = mock.patch.object(history, target, value)
            p.start()
            self.addCleanup(p.stop)

    def chat(self, n, size):
        self.msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * size} for i in range(n)]

    def test_a_count_of_small_messages_compacts_nothing(self):
        self.chat(30, 10)
        self.assertIsNone(history.compaction_plan(self.user))

    def test_the_serving_windows_budget_decides(self):
        self.chat(18, 5000)                                         # 90,000 chars
        self.assertIsNotNone(history.compaction_plan(self.user, budget=40000), "the small brain's share is passed")
        self.assertIsNone(history.compaction_plan(self.user, budget=2 * 125952), "a 126k window has room")

    def test_the_prompt_keeps_what_the_budget_allows(self):
        self.chat(18, 5000)
        _s, small = history.context_messages(self.user, budget=40000)
        _s, large = history.context_messages(self.user, budget=2 * 125952)
        self.assertLess(len(small), 18)
        self.assertEqual(len(large), 18, "a compaction skipped must not become a chat that forgets")

    def test_the_budget_is_the_same_share_as_the_brains(self):
        self.assertEqual(app._home_char_budget(("mothership", "qwen3.8:27b", 125952)), 2 * 125952)
        self.assertIsNone(app._home_char_budget(None), "no known window keeps the default")
        self.assertIsNone(app._home_char_budget(("mothership", "m", 0)))


class WhoWritesTheSummary(unittest.TestCase):
    HOME = ("mothership", "qwen3.8:27b", 125952)

    def worker(self, resident, occupied=()):
        url = scheduler.CARDS["mothership"]["url"]
        with mock.patch.object(app.ollama_client, "reachable", return_value=True), \
                mock.patch.object(app.ollama_client, "models_resident", return_value=resident), \
                mock.patch.object(app, "failover_available", return_value=(True, "")), \
                mock.patch.object(scheduler, "occupied_for",
                                  side_effect=lambda u, m=None, takes_window=False:
                                  {"holder": "x"} if (u, m) in occupied else None):
            return url, app._compaction_worker(self.HOME)

    def test_the_hot_model_the_chat_is_on(self):
        url, got = self.worker(["qwen3.8:27b"])
        self.assertEqual(got[:3], (url, "qwen3.8:27b", 125952))

    def test_a_cold_home_is_not_loaded_for_it(self):
        _url, got = self.worker(["something-else"])
        self.assertEqual(got[1], config.BRAIN_MODEL)

    def test_a_box_that_could_not_be_asked_is_not_hot(self):
        _url, got = self.worker(None)
        self.assertEqual(got[1], config.BRAIN_MODEL)

    def test_an_occupied_box_is_passed_over(self):
        url, _ = self.worker([])
        _url, got = self.worker(["qwen3.8:27b"], occupied={(url, "qwen3.8:27b"), (config.BRAIN_HOST, config.BRAIN_MODEL)})
        self.assertEqual(got[1], config.FAILOVER_MODEL)

    def test_nobody_free_means_the_summary_waits(self):
        url = scheduler.CARDS["mothership"]["url"]
        everyone = {(url, "qwen3.8:27b"), (config.BRAIN_HOST, config.BRAIN_MODEL),
                    (config.FAILOVER_HOST, config.FAILOVER_MODEL), (config.HEAVY_HOST, config.HEAVY_MODEL)}
        self.assertIsNone(self.worker(["qwen3.8:27b"], occupied=everyone)[1])


if __name__ == "__main__":
    unittest.main()
