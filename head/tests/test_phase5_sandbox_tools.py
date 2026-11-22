import os
os.environ.setdefault("EMBED_ENABLED", "0")
import unittest
import tools


class SandboxToolsetTests(unittest.TestCase):
    def test_removes_deploy_write_tools_keeps_rest(self):
        fake = [
            {"function": {"name": "self_deploy"}},
            {"function": {"name": "deploy_promote"}},
            {"function": {"name": "deploy_status"}},
            {"function": {"name": "coding_read"}},
        ]
        names = {t["function"]["name"] for t in tools.sandbox_safe(fake)}
        self.assertNotIn("self_deploy", names)
        self.assertNotIn("deploy_promote", names)
        self.assertIn("deploy_status", names)
        self.assertIn("coding_read", names)

    def test_sandbox_safe_only_removes_blocked_from_real_tools(self):
        before = {t.get("function", {}).get("name") for t in tools.HEAVY_TOOLS if isinstance(t, dict)}
        after = {t.get("function", {}).get("name") for t in tools.sandbox_safe(tools.HEAVY_TOOLS) if isinstance(t, dict)}
        self.assertTrue(after.issubset(before))
        self.assertEqual(before - after, before & tools.SANDBOX_BLOCKED_TOOLS)