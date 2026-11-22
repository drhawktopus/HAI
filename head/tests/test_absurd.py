"""Phase 2 (UI redesign): Absurd async-run registry + effort-mode clamp + endpoint wiring."""
import tempfile
import unittest
from pathlib import Path

import absurd
import config
import deliberation

APP = Path("app.py").read_text(encoding="utf-8")


class SecondsForChoiceTests(unittest.TestCase):
    def test_known_and_unknown(self):
        self.assertEqual(absurd.seconds_for_choice(config.ABSURD_TIME_CHOICES, "5m"), 300)
        self.assertEqual(absurd.seconds_for_choice(config.ABSURD_TIME_CHOICES, "1h"), 3600)
        self.assertEqual(absurd.seconds_for_choice(config.ABSURD_TIME_CHOICES, "all_night"), 36000)
        self.assertIsNone(absurd.seconds_for_choice(config.ABSURD_TIME_CHOICES, "nope"))

    def test_run_id_unique_and_prefixed(self):
        a, b = absurd.new_run_id(), absurd.new_run_id()
        self.assertTrue(a.startswith("abr-"))
        self.assertNotEqual(a, b)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.reg = absurd.AbsurdRegistry(self.dir)

    def test_create_get_and_defaults(self):
        rec = self.reg.create("abr-1", "Alex", "do a thing", "4h", 14400)
        self.assertEqual(rec["status"], absurd.QUEUED)
        self.assertEqual(rec["max_seconds"], 14400)
        got = self.reg.get("abr-1")
        self.assertEqual(got["user"], "Alex")
        self.assertIsNone(got["answer"])

    def test_update_and_answer_clip(self):
        self.reg.create("abr-2", "Alex", "x", "1h", 3600)
        self.reg.update("abr-2", status=absurd.RUNNING, last_status="thinking")
        self.reg.update("abr-2", status=absurd.DONE, answer="A" * 100_000)
        rec = self.reg.get("abr-2")
        self.assertEqual(rec["status"], absurd.DONE)
        self.assertLessEqual(len(rec["answer"]), 60_000)

    def test_update_unknown_returns_none(self):
        self.assertIsNone(self.reg.update("nope", status=absurd.DONE))

    def test_list_for_is_user_scoped_newest_first(self):
        self.reg.create("abr-a", "Alex", "1", "1h", 3600)
        self.reg.create("abr-b", "Jordan", "2", "1h", 3600)
        self.reg.create("abr-c", "Alex", "3", "1h", 3600)
        mine = self.reg.list_for("Alex")
        self.assertEqual([r["run_id"] for r in mine], ["abr-c", "abr-a"])
        self.assertEqual(self.reg.list_for("Jordan")[0]["run_id"], "abr-b")

    def test_has_active(self):
        self.assertFalse(self.reg.has_active())
        self.reg.create("abr-run", "Alex", "x", "1h", 3600)  # queued == active
        self.assertTrue(self.reg.has_active())
        self.assertTrue(self.reg.has_active("Alex"))
        self.assertFalse(self.reg.has_active("Jordan"))
        self.reg.update("abr-run", status=absurd.DONE)
        self.assertFalse(self.reg.has_active())

    def test_load_recovers_and_marks_interrupted(self):
        self.reg.create("abr-live", "Alex", "x", "1h", 3600)
        self.reg.update("abr-live", status=absurd.RUNNING)
        self.reg.create("abr-done", "Alex", "y", "1h", 3600)
        self.reg.update("abr-done", status=absurd.DONE, answer="ok")
        # A fresh registry over the same dir = a process restart.
        reloaded = absurd.AbsurdRegistry(self.dir)
        self.assertEqual(reloaded.get("abr-live")["status"], absurd.ERROR)  # running -> interrupted
        self.assertIn("interrupted", reloaded.get("abr-live")["error"])
        self.assertEqual(reloaded.get("abr-done")["status"], absurd.DONE)   # terminal preserved


class EffortModeTests(unittest.TestCase):
    def test_absurd_mode_allows_big_budget(self):
        st = deliberation.EffortState("absurd", 28800, 80)
        self.assertEqual(st.budget_seconds, 28800)       # NOT clamped to 1800
        self.assertEqual(st.max_passes, 80)

    def test_absurd_mode_hard_ceiling(self):
        st = deliberation.EffortState("absurd", 10_000_000, 10_000)
        self.assertEqual(st.budget_seconds, deliberation.ABSURD_MAX_BUDGET_SECONDS)
        self.assertEqual(st.max_passes, deliberation.ABSURD_MAX_PASSES)

    def test_think_hard_still_clamped(self):
        st = deliberation.EffortState("think_hard", 28800, 80)
        self.assertEqual(st.budget_seconds, 1800)
        self.assertEqual(st.max_passes, 20)

    def test_absurd_is_a_known_effort_mode(self):
        self.assertIn("absurd", deliberation.EFFORT_MODES)


class EndpointWiringTests(unittest.TestCase):
    """Static checks — app pulls heavy deps absent in the bare runner, so assert on the source."""

    def test_endpoints_defined(self):
        self.assertIn('@app.post("/absurd/start")', APP)
        self.assertIn('@app.get("/absurd/status")', APP)
        self.assertIn("def _absurd_worker(run_id, q):", APP)

    def test_absurd_forces_27b_and_deliberates(self):
        self.assertIn("absurd_run = bool(getattr(q, \"absurd_max_seconds\", None))", APP)
        self.assertIn("or self_engineering or absurd_run", APP)
        self.assertIn('deliberate = q.effort_mode == "think_hard" or absurd_run', APP)

    def test_worker_query_is_ephemeral_and_forced(self):
        # Detached run must not race the user's live transcript, and must land on the 27B.
        self.assertIn('effort_mode="think_hard", routing="force_27b"', APP)
        self.assertIn("ephemeral=True, absurd_max_seconds=max_seconds", APP)

    def test_energy_gate_exempts_unbounded_users(self):
        self.assertIn("if req.user not in config.ABSURD_ENERGY_UNBOUNDED_USERS:", APP)
        self.assertIn("status=absurd.DEFERRED", APP)

    def test_run_think_hard_reads_override(self):
        self.assertIn('absurd_seconds = getattr(q, "absurd_max_seconds", None)', APP)
        self.assertIn('effort_label = "absurd"', APP)
        self.assertIn('"effort_mode": effort_label', APP)


class UIWiringTests(unittest.TestCase):
    def test_the_background_switch_replaces_the_absurd_option(self):
        """# /absurd/start remains for machine lanes; the UI uses the background switch instead."""
        self.assertNotIn('<option value="absurd">', APP)
        self.assertIn('id="bgRun"', APP)
        self.assertIn('id="absurdTime"', APP)                       # the time picker is the background budget now
        self.assertNotIn("startAbsurd(", APP)

    def test_background_send_and_the_absurd_tile_poller(self):
        self.assertIn("background:(bgChoice||undefined)", APP)       # sent on the ordinary /query/stream turn
        self.assertIn("function pollAbsurd(runId)", APP)            # Absurd runs started by machine lanes still show
        self.assertIn('@app.post("/absurd/start")', APP)

    def test_energy_reminder_chip(self):
        self.assertIn('id="energyChip"', APP)
        self.assertIn("async function refreshEnergyChip()", APP)
        self.assertIn("if (!isAdminUser()){ energyChip.style.display='none'; return; }", APP)


if __name__ == "__main__":
    unittest.main()
