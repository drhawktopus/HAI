import json
import os
import tempfile
import unittest

import config
import pending_actions


class PendingActionTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()

    def tearDown(self):
        pending_actions._clear_for_tests()

    def test_register_and_confirm_executes_once_for_owner(self):
        calls = []
        preview = pending_actions.register(
            "test.write",
            "Would do the thing",
            lambda: calls.append("done") or "ok",
            user="Alex",
        )
        self.assertEqual(preview["status"], "pending")
        self.assertIn("action_token", preview)
        out = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(out["result"], "ok")
        self.assertEqual(calls, ["done"])
        again = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(again["status"], "error")
        self.assertEqual(calls, ["done"])

    def test_wrong_user_cannot_confirm(self):
        calls = []
        preview = pending_actions.register("test.write", "Preview", lambda: calls.append("bad"), user="Alex")
        out = pending_actions.confirm(preview["action_token"], user="Jordan")
        self.assertEqual(out["status"], "error")
        self.assertEqual(calls, [])
        self.assertEqual(len(pending_actions.list_pending("Alex")), 1)

    def test_deny_removes_without_executing(self):
        calls = []
        preview = pending_actions.register("test.write", "Preview", lambda: calls.append("bad"), user="Alex")
        out = pending_actions.deny(preview["action_token"], user="Alex")
        self.assertEqual(out["status"], "denied")
        self.assertEqual(calls, [])
        self.assertEqual(pending_actions.list_pending("Alex"), [])

    def test_destructive_preview_is_marked(self):
        preview = pending_actions.register("delete.thing", "Delete it", lambda: "deleted", user="Alex", destructive=True)
        self.assertTrue(preview["destructive"])
        self.assertIn("DESTRUCTIVE ACTION", preview["preview"])


class RiskAssessmentTests(unittest.TestCase):
    def test_levels(self):
        risk = lambda k, d, m: pending_actions.assess_risk(k, d, m)[0]
        self.assertEqual(risk("files.add", False, {"action": "add"}), "LOW")
        self.assertEqual(risk("files.resume", False, {"action": "resume", "hashes": "a"}), "LOW")
        self.assertEqual(risk("files.remove", True, {"action": "remove", "hashes": "a"}), "MED")
        self.assertEqual(risk("files.remove", True, {"action": "remove", "delete_files": True, "hashes": "a"}), "HIGH")
        self.assertEqual(risk("library.import", False, {"count": 403, "import_mode": "move"}), "HIGH")  # bulk
        self.assertEqual(risk("library.import", False, {"count": 6, "import_mode": "copy"}), "MED")

    def test_register_surfaces_risk_banner_for_high(self):
        p = pending_actions.register("files.remove", "remove it", lambda: "x", user="Alex",
                                     destructive=True, metadata={"action": "remove", "delete_files": True, "hashes": "a"})
        self.assertEqual(p["risk"], "HIGH")
        self.assertIn("RISK HIGH", p["preview"])
        self.assertIn("deletes downloaded files", p["preview"])

    def test_low_risk_has_no_banner(self):
        p = pending_actions.register("files.add", "add it", lambda: "x", user="Alex", metadata={"action": "add"})
        self.assertEqual(p["risk"], "LOW")
        self.assertNotIn("RISK", p["preview"])


class AuditTrailTests(unittest.TestCase):
    def test_audit_records_preview_and_applied(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "audit", "ops.jsonl")
        orig = config.AUDIT_FILE
        config.AUDIT_FILE = path
        pending_actions._clear_for_tests()
        try:
            p = pending_actions.register("files.remove", "rm", lambda: "done", user="Alex",
                                         destructive=True, metadata={"action": "remove", "hashes": "a|b"})
            pending_actions.confirm(p["action_token"], user="Alex")
            lines = open(path, encoding="utf-8").read().strip().splitlines()
            events = [json.loads(l)["event"] for l in lines]
            self.assertIn("PREVIEW", events)
            self.assertIn("APPLIED", events)
            rec = json.loads(lines[0])
            self.assertEqual(rec["risk"], "MED")
            self.assertEqual(rec["user"], "Alex")
        finally:
            config.AUDIT_FILE = orig
            pending_actions._clear_for_tests()


class ConfirmActionToolWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.tools = Path("tools.py").read_text(encoding="utf-8")

    def test_schema_dispatch_and_admin_gate(self):
        self.assertIn('"name": "confirm_action"', self.tools)
        self.assertIn('if name == "confirm_action":', self.tools)
        admin_block = self.tools.split("_ADMIN_TOOLS = {")[1].split("}")[0]
        self.assertIn('"confirm_action"', admin_block)
        for prefix in ("BRAIN_TOOLS = [", "HEAVY_TOOLS = ["):
            line = next(l for l in self.tools.splitlines() if l.startswith(prefix))
            self.assertIn("_CONFIRM_ACTION", line)

    def test_dispatch_returns_json(self):
        import tools
        pending_actions._clear_for_tests()
        preview = pending_actions.register("test.write", "Preview", lambda: "ok", user="Alex")
        out = tools.run_tool("confirm_action", {"action": "confirm", "action_token": preview["action_token"]}, "", user="Alex")
        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "applied")
        self.assertEqual(parsed["result"], "ok")


class IdempotentRegisterTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()

    def tearDown(self):
        pending_actions._clear_for_tests()

    def test_identical_registration_reuses_card(self):
        meta = {"service": "deploy", "action": "start", "self_deploy": True}
        a = pending_actions.register("self.deploy", "Deploy preview", lambda: "x", user="Alex", destructive=True, metadata=meta)
        b = pending_actions.register("self.deploy", "Deploy preview", lambda: "y", user="Alex", destructive=True, metadata=meta)
        self.assertEqual(a["action_token"], b["action_token"])
        self.assertEqual(len(pending_actions.list_pending("Alex")), 1)

    def test_different_metadata_creates_distinct_cards(self):
        a = pending_actions.register("files.add", "add A", lambda: "x", user="Alex", metadata={"hashes": "a"})
        b = pending_actions.register("files.add", "add B", lambda: "y", user="Alex", metadata={"hashes": "b"})
        self.assertNotEqual(a["action_token"], b["action_token"])
        self.assertEqual(len(pending_actions.list_pending("Alex")), 2)

    def test_different_user_not_deduped(self):
        a = pending_actions.register("self.deploy", "Deploy", lambda: "x", user="Alex", metadata={"self_deploy": True})
        b = pending_actions.register("self.deploy", "Deploy", lambda: "y", user="Jordan", metadata={"self_deploy": True})
        self.assertNotEqual(a["action_token"], b["action_token"])

    def test_dedup_does_not_match_expired(self):
        import datetime as _d
        a = pending_actions.register("self.deploy", "Deploy", lambda: "x", user="Alex", metadata={"self_deploy": True})
        for rec in pending_actions._PENDING.values():
            rec["expires_at"] = pending_actions._now() - _d.timedelta(seconds=1)
        b = pending_actions.register("self.deploy", "Deploy", lambda: "y", user="Alex", metadata={"self_deploy": True})
        self.assertNotEqual(a["action_token"], b["action_token"])

if __name__ == "__main__":
    unittest.main()
