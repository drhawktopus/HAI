"""Tests for overnight Discord-DM support.

1. Pure render_run_dm helper: happy path, empty state, None state.
2. Config defaults: OVERNIGHT_DM_URL, _TOKEN, _USER_ID exist with docs values.
3. Source wiring: app.py's _overnight_notify references DM keys and posts output.
"""
import unittest
from pathlib import Path

import config
import overnight


APP = Path("app.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Part 1: render_run_dm pure helper
# ---------------------------------------------------------------------------

class RenderRunDmTests(unittest.TestCase):
    """Pure-function tests for overnight.render_run_dm."""

    def test_none_state_yields_empty(self):
        self.assertEqual(overnight.render_run_dm(None), "")

    def test_empty_dict_yields_empty(self):
        self.assertEqual(overnight.render_run_dm({}), "")

    def test_populated_state_contains_run_id(self):
        state = {
            "run_id": "ovr-dm-1",
            "stop_reason": "exhausted",
            "done_count": 2,
            "bailed_count": 1,
            "elapsed_seconds": 45.0,
            "items": [
                {"id": "bl-0001", "outcome": "done"},
                {"id": "bl-0002", "outcome": "bailed"},
                {"id": "bl-0003", "outcome": "done"},
            ],
        }
        text = overnight.render_run_dm(state)
        self.assertIsInstance(text, str)
        self.assertIn("ovr-dm-1", text)

    def test_populated_state_shows_done_bailed_counts(self):
        state = {
            "run_id": "ovr-2",
            "stop_reason": "budget",
            "done_count": 3,
            "bailed_count": 4,
            "items": [],
        }
        text = overnight.render_run_dm(state)
        self.assertIn("3 done", text)
        self.assertIn("4 bailed", text)

    def test_populated_state_shows_each_item_id(self):
        state = {
            "run_id": "ovr-3",
            "stop_reason": "exhausted",
            "done_count": 2,
            "bailed_count": 0,
            "items": [
                {"id": "item-a", "outcome": "done"},
                {"id": "item-b", "outcome": "done"},
            ],
        }
        text = overnight.render_run_dm(state)
        self.assertIn("item-a", text)
        self.assertIn("item-b", text)

    def test_populated_state_shoes_outcomes(self):
        state = {
            "run_id": "ovr-4",
            "stop_reason": "exhausted",
            "done_count": 1,
            "bailed_count": 1,
            "items": [
                {"id": "x", "outcome": "done"},
                {"id": "y", "outcome": "bailed"},
            ],
        }
        text = overnight.render_run_dm(state)
        # Each item line contains id and outcome
        for line in text.split("\n"):
            if "  x:" in line:
                self.assertIn("done", line)
            if "  y:" in line:
                self.assertIn("bailed", line)

    def test_elapsed_seconds_present_when_set(self):
        state = {
            "run_id": "ovr-5",
            "stop_reason": "exhausted",
            "done_count": 1,
            "bailed_count": 0,
            "elapsed_seconds": 120,
            "items": [{"id": "a", "outcome": "done"}],
        }
        text = overnight.render_run_dm(state)
        self.assertIn("Elapsed: 120s", text)

    def test_capped_at_8_items(self):
        items = [{"id": f"cap-{i}", "outcome": "done"} for i in range(15)]
        state = {
            "run_id": "ovr-cap",
            "stop_reason": "budget",
            "done_count": 15,
            "bailed_count": 0,
            "items": items,
        }
        text = overnight.render_run_dm(state)
        # First 8 items appear, 9th does not
        self.assertIn("cap-7", text)   # index 7 == last shown
        self.assertNotIn("cap-8", text)  # index 8 == first hidden

    def test_empty_items_still_yields_header(self):
        state = {
            "run_id": "ovr-empty-items",
            "stop_reason": "exhausted",
            "done_count": 0,
            "bailed_count": 0,
            "items": [],
        }
        text = overnight.render_run_dm(state)
        self.assertIn("ovr-empty-items", text)
        self.assertIn("0 done", text)


# ---------------------------------------------------------------------------
# Part 2: Config defaults
# ---------------------------------------------------------------------------

class OvernightDmConfigTests(unittest.TestCase):
    """Verify config keys have defaults when their env vars are unset.

    Tests reload the module after unsetting relevant environment variables to ensure
    defaults are used, avoiding dependency on live orchestrator settings."""

    def test_documented_defaults_when_env_unset(self):
        import importlib
        import os
        names = ("OVERNIGHT_DM_URL", "OVERNIGHT_DM_TOKEN", "OVERNIGHT_DM_USER_ID")
        saved = {n: os.environ.pop(n, None) for n in names}
        try:
            importlib.reload(config)
            self.assertEqual(config.OVERNIGHT_DM_URL, "http://hawkadminbot:8477/dm")
            self.assertEqual(config.OVERNIGHT_DM_TOKEN, "")
            self.assertEqual(config.OVERNIGHT_DM_USER_ID, "")
        finally:
            for n, v in saved.items():
                if v is not None:
                    os.environ[n] = v
            importlib.reload(config)   # restore the live view for every later test

    def test_all_three_keys_defined(self):
        # Defensive: the names actually exist on the config module
        assert hasattr(config, "OVERNIGHT_DM_URL")
        assert hasattr(config, "OVERNIGHT_DM_TOKEN")
        assert hasattr(config, "OVERNIGHT_DM_USER_ID")


# ---------------------------------------------------------------------------
# Part 3: Source wiring in app.py
# ---------------------------------------------------------------------------

class OvernightDmWiringTests(unittest.TestCase):
    """Source-level assertions that _overnight_notify is wired to the DM path."""

    def test_overnight_notify_references_dm_token(self):
        self.assertIn("config.OVERNIGHT_DM_TOKEN", APP)

    def test_overnight_notify_references_dm_user_id(self):
        self.assertIn("config.OVERNIGHT_DM_USER_ID", APP)

    def test_overnight_notify_posts_to_dm_url(self):
        self.assertIn("config.OVERNIGHT_DM_URL", APP)

    def test_overnight_notify_calls_render_run_dm(self):
        # The DM body is produced by the pure helper
        self.assertIn("overnight.render_run_dm(state)", APP)

    def test_dm_post_sends_user_id_and_message_keys(self):
        # The JSON payload contains user_id and message fields
        self.assertIn('"user_id"', APP)
        self.assertIn('"message"', APP)

    def test_dm_post_includes_x_dm_token_header(self):
        self.assertIn('"X-DM-Token"', APP)



class RenderStartDmTests(unittest.TestCase):
    """Pure render_start_dm: the one-line 'the factory lit up' notice."""

    def test_contains_id_count_and_cap(self):
        out = overnight.render_start_dm("ovr-20260709T2200", 4, 14400)
        self.assertIn("ovr-20260709T2200", out)
        self.assertIn("4 eligible", out)
        self.assertIn("4.0h", out)

    def test_bad_cap_is_defensive(self):
        out = overnight.render_start_dm("ovr-x", 0, None)
        self.assertIn("ovr-x", out)
        self.assertIn("0 eligible", out)
        self.assertIn("0.0h", out)


class StartDmWiringTests(unittest.TestCase):
    """Source wiring: _overnight_start sends the start DM for REAL runs only, through the same
    fail-soft sender the finish path uses."""

    def test_shared_sender_exists(self):
        self.assertIn("def _overnight_dm_send(", APP)

    def test_notify_uses_shared_sender(self):
        notify_body = APP.split("def _overnight_notify(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_overnight_dm_send(overnight.render_run_dm(state))", notify_body)

    def test_start_path_dms_real_runs_only(self):
        """Behaviour, not spelling (it used to assert the literal `if not req.dry_run:`): a real run DMs its start;
        a dry run and a restart's quiet resume (#37) never do."""
        import app as _app
        from unittest import mock
        sent = []

        def start(req, **k):
            _app._overnight_start(req, **k)
            _app._OVERNIGHT["thread"].join(5)

        with mock.patch.object(_app, "_overnight_dm_send", lambda text: sent.append(text)), \
             mock.patch.object(_app, "_overnight_run_thread", lambda *a, **k: None), \
             mock.patch.object(_app.backlog, "eligible_items", lambda done: []), \
             mock.patch.object(_app, "_OVERNIGHT", {"thread": None, "state": None}), \
             mock.patch.dict(_app._SELF_ENG_MODE, {}):
            start(_app.OvernightStartReq(dry_run=False, energy_gated=False))
            self.assertEqual(len(sent), 1, "a real run DMs its start")
            start(_app.OvernightStartReq(dry_run=True, energy_gated=False))
            start(_app.OvernightStartReq(dry_run=False, energy_gated=False), quiet=True)
        self.assertEqual(len(sent), 1, "a dry run and a quiet resume send nothing")


# ---------------------------------------------------------------------------
# Part 4: Discord user-ID validation
# ---------------------------------------------------------------------------

class OvernightDmUserIdValidationTests(unittest.TestCase):
    """Assert that _overnight_notify guards against malformed user IDs."""

    def test_valid_numeric_17_digits(self):
        # A 17-digit snowflake ID is valid
        self.assertTrue(overnight._valid_discord_user_id("12345678901234567"))

    def test_valid_numeric_20_digits(self):
        # A 20-digit snowflake ID is valid
        self.assertTrue(overnight._valid_discord_user_id("12345678901234567890"))

    def test_valid_numeric_18_digits(self):
        self.assertTrue(overnight._valid_discord_user_id("123456789012345678"))

    def test_rejects_too_short(self):
        # Less than 17 digits is not a valid Discord snowflake
        self.assertFalse(overnight._valid_discord_user_id("123456789012345"))

    def test_rejects_too_long(self):
        # More than 20 digits is not a valid Discord snowflake
        self.assertFalse(overnight._valid_discord_user_id("123456789012345678901"))

    def test_rejects_non_numeric(self):
        # Contains letters → invalid
        self.assertFalse(overnight._valid_discord_user_id("notadiscordid123456"))

    def test_rejects_empty_string(self):
        self.assertFalse(overnight._valid_discord_user_id(""))

    def test_rejects_whitespace(self):
        self.assertFalse(overnight._valid_discord_user_id("   "))

    def test_app_uses_validation_helper(self):
        # app.py should reference the validation helper before using the ID
        self.assertIn("_valid_discord_user_id", APP)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
