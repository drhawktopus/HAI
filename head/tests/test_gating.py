"""Tests for per-user tool gating + the admin-managed profile roster.

`config` and `roster` are stdlib-only, so we exercise their behavior directly. `tools`,
`ollama_client`, and `app` pull in heavy deps that aren't in the runner env, so those are
verified with static source-wiring checks (same pattern as the other suites here)."""
import os
import tempfile
import unittest
from pathlib import Path

import config
import roster


class ConfigDefaultTests(unittest.TestCase):
    def test_admin_and_ha_control_defaults(self):
        self.assertIn("Alex", config.ADMIN_USERS)
        self.assertLessEqual(config.ADMIN_USERS, {"Alex", "ClaudeSupervisor"})
        self.assertEqual(config.HA_CONTROL_USERS, {"Alex", "Avery"})
        # The "boys" must be locked out of control + admin by default.
        for boy in ("Jordan", "Riley", "Officer Parker"):
            self.assertNotIn(boy, config.HA_CONTROL_USERS)
            self.assertNotIn(boy, config.ADMIN_USERS)
        # The machine identity gets coding/admin tools — it must NEVER control the house.
        self.assertNotIn("ClaudeSupervisor", config.HA_CONTROL_USERS)


class RosterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.PROFILES_FILE
        config.PROFILES_FILE = os.path.join(self._tmp, "profiles.json")

    def tearDown(self):
        config.PROFILES_FILE = self._orig

    def test_add_list_note_remove(self):
        self.assertEqual(roster.dynamic(), [])
        roster.add("Officer Parker", note="prefers terse answers")
        self.assertIn("Officer Parker", roster.dynamic())
        self.assertEqual(roster.note_for("Officer Parker"), "prefers terse answers")
        # union keeps the built-in env profiles too
        allp = roster.all_profiles()
        for builtin in config.PROFILES:
            self.assertIn(builtin, allp)
        self.assertIn("Officer Parker", allp)
        # removal
        self.assertTrue(roster.remove("Officer Parker"))
        self.assertNotIn("Officer Parker", roster.dynamic())
        self.assertFalse(roster.remove("Officer Parker"))

    def test_all_profiles_dedupes(self):
        roster.add(config.PROFILES[0])  # re-adding a built-in name must not duplicate
        self.assertEqual(len(roster.all_profiles()), len(set(roster.all_profiles())))


class SourceWiringTests(unittest.TestCase):
    TOOLS = Path("tools.py").read_text(encoding="utf-8")
    OLLAMA = Path("ollama_client.py").read_text(encoding="utf-8")
    APP = Path("app.py").read_text(encoding="utf-8")

    def test_tools_define_gating(self):
        self.assertIn("_ADMIN_TOOLS = {", self.TOOLS)
        self.assertIn("_HA_CONTROL_TOOLS = {", self.TOOLS)
        self.assertIn("def _allowed(name, user):", self.TOOLS)
        self.assertIn("def tools_for(tools_list, user):", self.TOOLS)
        self.assertIn("config.ADMIN_USERS", self.TOOLS)
        self.assertIn("config.HA_CONTROL_USERS", self.TOOLS)

    def test_run_tool_hard_gate_first(self):
        # The dispatch gate must run before any tool body.
        self.assertIn("def run_tool(name, args, user_text, user=None, persona=None):", self.TOOLS)
        gate = self.TOOLS.index("if not _allowed(name, user):")
        first_branch = self.TOOLS.index('if name == "web_search":')
        self.assertLess(gate, first_branch, "permission gate must precede tool dispatch")
        self.assertIn("Permission denied", self.TOOLS)

    def test_ha_read_not_gated_control_is(self):
        self.assertIn('"ha_control_device"', self.TOOLS)
        # ha_get_devices must NOT be in either gated set
        admin_block = self.TOOLS.split("_ADMIN_TOOLS = {")[1].split("}")[0]
        control_block = self.TOOLS.split("_HA_CONTROL_TOOLS = {")[1].split("}")[0]
        self.assertNotIn("ha_get_devices", admin_block)
        self.assertNotIn("ha_get_devices", control_block)

    def test_ollama_threads_user(self):
        self.assertIn("status_callback=None, user=None", self.OLLAMA)
        self.assertIn("tools.tools_for(tools_list, user)", self.OLLAMA)
        self.assertIn("tools.run_tool(fn, args, user_text, user=user, persona=persona)", self.OLLAMA)

    def test_app_passes_user_and_restricts(self):
        self.assertIn("roster.all_profiles()", self.APP)
        self.assertIn("user=q.user", self.APP)
        self.assertIn("authorized to control Home Assistant devices", self.APP)
        self.assertIn("roster.note_for(q.user)", self.APP)

    def test_get_credential_is_admin_gated(self):
        """Ensure get_credential is in _ADMIN_TOOLS so _allowed() gates it to admins only.

        Being excluded from the RAG index (rag.py) does not restrict direct tool access,
        so explicit admin gating is required to prevent unauthorized credential retrieval."""
        admin_block = self.TOOLS.split("_ADMIN_TOOLS = {")[1].split("}")[0]
        self.assertIn("get_credential", admin_block)


if __name__ == "__main__":
    unittest.main()
