"""Granting a repository to Doc is the operator's decision, never the model's.

The tool validates with the runner first (a dry run that writes nothing), then holds the
registration as a pending action. Only confirm_action executes it, and the executing call
is the one that writes. A workspace grant is admin-gated like every other boundary change.
"""
import unittest
from unittest import mock

import pending_actions
import tools


class Runner:
    """A runner that answers exactly the questions the real one answers."""

    def __init__(self, accept=True, reason="the path is not under an allowed share root"):
        self.accept = accept
        self.reason = reason
        self.calls = []
        self.writes = 0

    def register_workspace(self, path, id, description="", commands=None,
                          protected_paths=None, ignored_paths=None, shared=True, dry_run=False):
        self.calls.append((path, id, dry_run))
        if not self.accept:
            return {"error": self.reason}
        if not dry_run:
            self.writes += 1
        return {"registered": not dry_run,
                "workspace": {"id": id, "path": path,
                              "protected_paths": [".git", ".env", "secrets", "backups"] + list(protected_paths or [])},
                "test_profiles": sorted(commands or {}),
                "proposed_not_run": sorted(commands or {}),
                "note": "Each profile was checked against the allowed command templates, not executed."}


def call(user, text, **args):
    return tools._coding_workspace_add_tool(args, user)


class TheDoor(unittest.TestCase):
    def setUp(self):
        self.runner = Runner()
        patch = mock.patch.object(tools.coding_client, "register_workspace", self.runner.register_workspace)
        patch.start()
        self.addCleanup(patch.stop)
        pending_actions._PENDING.clear()

    def test_it_is_admin_only(self):
        self.assertIn("coding_workspace_add", tools._ADMIN_TOOLS)
        self.assertFalse(tools._allowed("coding_workspace_add", "Riley"))
        self.assertTrue(tools._allowed("coding_workspace_add", "Alex"))
        self.assertTrue(tools._allowed("coding_workspace_add", "ClaudeSupervisor"))
        out = tools.run_tool("coding_workspace_add", {"path": "/app/shares/foundry", "id": "foundry"},
                            "give me the foundry repo", user="Riley")
        self.assertIn("Permission denied", str(out))
        self.assertEqual(self.runner.calls, [], "the runner was not even asked")

    def test_it_asks_the_runner_before_holding_anything(self):
        out = call("Alex", "add the foundry repo at /app/shares/foundry",
                   path="/app/shares/foundry", id="foundry",
                   test_commands={"unit": ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"]})
        self.assertEqual(self.runner.calls, [("/app/shares/foundry", "foundry", True)])
        self.assertEqual(self.runner.writes, 0, "nothing is registered before approval")
        self.assertEqual(out["status"], "pending")
        self.assertEqual(out["risk"], "MED")
        self.assertIn("repository in the lab", out["risk_reasons"][0])

    def test_a_rejected_request_never_becomes_a_pending_card(self):
        self.runner.accept = False
        out = call("Alex", "add the foundry repo", path="/elsewhere/foundry", id="foundry")
        self.assertNotIn("action_token", out)
        self.assertIn("will not accept", str(out))
        self.assertEqual(pending_actions._PENDING, {})

    def test_the_runner_says_why_and_the_tool_reports_it_verbatim(self):
        """The tool does not decide what is allowed; it asks and passes the refusal through."""
        self.runner.accept = False
        self.runner.reason = "this repository has Git remotes (origin); workspaces must stay local-only"
        out = call("Alex", "add a repo", path="/app/shares/remote", id="remote")
        self.assertIn("local-only", str(out))

    def test_the_preview_says_in_plain_words_what_is_granted(self):
        out = call("Alex", "add the foundry repo", path="/app/shares/foundry", id="foundry",
                   test_commands={"unit": ["python", "-m", "unittest", "discover", "-s", "."]})
        preview = out["preview"]
        for phrase in ("/app/shares/foundry", "'foundry'", "read+write access",
                       "NOT executed", "Protected", "deploy door", "unit"):
            self.assertIn(phrase, preview, f"the preview must say {phrase!r}")

    def test_confirming_is_the_only_write(self):
        out = call("Alex", "add the foundry repo", path="/app/shares/foundry", id="foundry",
                   test_commands={"compile": ["python", "-m", "compileall", "-q", "."]})
        result = pending_actions.confirm(out["action_token"], "Alex")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.runner.writes, 1)
        self.assertIn("foundry", str(result["result"]))
        self.assertEqual(self.runner.calls[-1][2], False, "the real registration was not a dry run")

    def test_denying_writes_nothing(self):
        out = call("Alex", "add the foundry repo", path="/app/shares/foundry", id="foundry")
        pending_actions.deny(out["action_token"], "Alex")
        self.assertEqual(self.runner.writes, 0)

    def test_another_user_cannot_confirm_it(self):
        out = call("Alex", "add the foundry repo", path="/app/shares/foundry", id="foundry")
        result = pending_actions.confirm(out["action_token"], "Riley")
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.runner.writes, 0)

    def test_missing_path_or_id_is_answered_not_registered(self):
        self.assertEqual(pending_actions._PENDING, {})
        out = call("Alex", "add a repo", path="", id="foundry")
        self.assertIn("needs both", str(out))
        out = call("Alex", "add a repo", path="/app/shares/foundry", id="")
        self.assertIn("needs both", str(out))
        self.assertEqual(pending_actions._PENDING, {})

    def test_it_is_in_the_coding_toolset_and_the_pool_keeps_its_order(self):
        names = [t["function"]["name"] for t in tools.CODING_TOOLS]
        self.assertIn("coding_workspace_add", names)
        self.assertEqual(names[0], "coding_workspace", "the new tool sits beside the existing one")
        self.assertEqual(names[1], "coding_workspace_add")
        out, note = tools.tool_selection.select(list(tools.CODING_JOB_TOOLS), "Alex", "add a repo", 128000)
        self.assertIn("coding_workspace_add", [t["function"]["name"] for t in out])
        self.assertIsNone(note, "the job set is handed whole, not narrowed")


if __name__ == "__main__":
    unittest.main()
