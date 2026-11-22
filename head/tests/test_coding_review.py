import json
import unittest
from unittest.mock import patch

import coding_review


class CodingReviewTests(unittest.TestCase):
    def test_parse_review_accepts_fenced_json(self):
        content = """```json
{"verdict":"changes_requested","summary":"Bug found","findings":[
{"severity":"high","file":"calculator.py","line":2,
"explanation":"Subtraction is reversed","suggested_test":"Assert subtract(7, 4) == 3"}
]}
```"""
        result = coding_review.parse_review(content)
        self.assertEqual(result["findings"][0]["severity"], "high")

    def test_parse_review_normalizes_inconsistent_pass(self):
        content = json.dumps({
            "verdict": "pass",
            "summary": "Looks good",
            "findings": [{
                "severity": "low",
                "file": "x.py",
                "line": 1,
                "explanation": "Something",
                "suggested_test": "Test it",
            }],
        })
        result = coding_review.parse_review(content)
        self.assertEqual(result["verdict"], "changes_requested")

    def test_parse_review_accepts_common_equivalent_fields(self):
        content = json.dumps({
            "verdict": "fail",
            "findings": [{
                "severity": "HIGH",
                "file": "x.py",
                "line": 3,
                "description": "The implementation is reversed.",
                "recommendation": "Add a regression test.",
            }],
        })
        result = coding_review.parse_review(content)
        self.assertEqual(result["verdict"], "changes_requested")
        self.assertEqual(result["summary"], "The implementation is reversed.")
        self.assertEqual(result["findings"][0]["suggested_test"], "Add a regression test.")

    def test_parse_review_promotes_explicit_failed_reasoning(self):
        content = json.dumps({
            "verdict": "fail",
            "reasoning": "Zero divisors are not handled.",
            "recommendation": "Add a zero-divisor regression test.",
        })
        result = coding_review.parse_review(content)
        self.assertEqual(result["verdict"], "changes_requested")
        self.assertEqual(result["findings"][0]["severity"], "high")
        self.assertIn("Zero divisors", result["findings"][0]["explanation"])

    def test_parse_review_accepts_review_notes_as_findings(self):
        content = json.dumps({
            "review_notes": [{
                "severity": "critical",
                "file": "client.py",
                "description": "A secret is hardcoded.",
                "remediation": "Load it from an approved secret source.",
            }],
        })
        result = coding_review.parse_review(content)
        self.assertEqual(result["verdict"], "changes_requested")
        self.assertEqual(result["findings"][0]["file"], "client.py")

    def test_parse_review_normalizes_out_of_vocab_severity(self):
        # Previously an out-of-vocab severity (e.g. "info"/"nit") raised ReviewError and
        # discarded the entire review. It should now normalize to a canonical level instead.
        content = json.dumps({
            "verdict": "changes_requested",
            "summary": "Minor note",
            "findings": [
                {"severity": "info", "file": "x.py", "line": "n/a",
                 "explanation": "Style nit", "suggested_test": ""},
                {"severity": "blocker", "file": "y.py", "line": 4,
                 "explanation": "Real bug", "suggested_test": "Add a test"},
                "not-a-dict-finding",
            ],
        })
        result = coding_review.parse_review(content)
        self.assertEqual(result["verdict"], "changes_requested")
        # both dict findings survive; the non-dict one is skipped
        self.assertEqual(len(result["findings"]), 2)
        self.assertEqual(result["findings"][0]["severity"], "low")   # info -> low
        self.assertIsNone(result["findings"][0]["line"])             # bad line -> None
        self.assertEqual(result["findings"][1]["severity"], "critical")  # blocker -> critical

    def test_build_packet_omits_task_notes(self):
        packet = coding_review.build_packet(
            {"id": "sample", "write": True, "test_profiles": ["unit"]},
            {
                "id": "a" * 32,
                "objective": "Fix it",
                "acceptance_criteria": ["Tests pass"],
                "status": "active",
                "plan": [],
                "notes": [{"text": "lead reasoning must stay private"}],
            },
            {"stdout": "## main"},
            {"stdout": "diff", "untracked_included": [], "untracked_skipped": []},
            None,
        )
        self.assertNotIn("notes", packet["task"])
        self.assertNotIn("lead reasoning", json.dumps(packet))

    @patch("coding_review._coding_client")
    @patch("coding_review.review_model")
    def test_review_task_records_single_pass(self, review_model, client_factory):
        client = client_factory.return_value
        client.workspace.return_value = {"id": "sample", "test_profiles": ["unit"]}
        client.task.side_effect = [
            {
                "id": "a" * 32,
                "workspace": "sample",
                "objective": "Fix it",
                "acceptance_criteria": [],
                "status": "active",
                "plan": [],
                "notes": [],
            },
            {"ok": True},
        ]
        client.git.side_effect = [
            {"stdout": "## main\n M calculator.py"},
            {"stdout": "diff --git a/calculator.py b/calculator.py\n"},
        ]
        client.test.return_value = {"returncode": 0, "stdout": "ok", "stderr": ""}
        review_model.return_value = {
            "verdict": "pass",
            "summary": "No findings",
            "findings": [],
            "reviewer": {"host": "http://10.0.0.76:11434", "model": "qwen3:14b"},
        }

        result = coding_review.review_task("sample", "a" * 32, "unit")

        self.assertEqual(result["verdict"], "pass")
        client.task_diff.assert_not_called()
        update_call = client.task.call_args_list[-1]
        self.assertTrue(update_call.kwargs["note"].startswith(coding_review.REVIEW_MARKER))

    @patch("coding_review._coding_client")
    @patch("coding_review.review_model")
    def test_review_task_uses_task_branch_diff_for_clean_isolated_task(self, review_model, client_factory):
        client = client_factory.return_value
        client.workspace.return_value = {"id": "wt-task", "test_profiles": ["unit"]}
        client.task.side_effect = [
            {
                "id": "a" * 32,
                "workspace": "wt-task",
                "objective": "Fix it",
                "acceptance_criteria": [],
                "status": "active",
                "plan": [],
                "notes": [],
                "worktree": {"base": "sample", "branch": "task/" + "a" * 32},
            },
            {"ok": True},
        ]
        client.git.side_effect = [
            {"stdout": "## task/aaaa"},
            {"stdout": ""},
        ]
        client.task_diff.return_value = {
            "stdout": "diff --git a/app.py b/app.py\n+VALUE = 1\n",
            "diff_source": "task_branch",
            "untracked_included": [],
            "untracked_skipped": [],
        }
        client.test.return_value = {"returncode": 0, "stdout": "ok", "stderr": ""}
        review_model.return_value = {
            "verdict": "pass",
            "summary": "No findings",
            "findings": [],
            "reviewer": {"host": "http://10.0.0.76:11434", "model": "qwen3.6:27b"},
        }

        result = coding_review.review_task("wt-task", "a" * 32, "unit")

        self.assertEqual(result["verdict"], "pass")
        client.task_diff.assert_called_once_with("a" * 32)
        packet = review_model.call_args.args[0]
        self.assertEqual(packet["diff"], "diff --git a/app.py b/app.py\n+VALUE = 1\n")

    @patch("coding_review._coding_client")
    def test_review_task_refuses_second_pass(self, client_factory):
        client = client_factory.return_value
        client.workspace.return_value = {"id": "sample"}
        client.task.return_value = {
            "id": "a" * 32,
            "workspace": "sample",
            "notes": [{"text": coding_review.REVIEW_MARKER + " verdict=pass"}],
        }
        result = coding_review.review_task("sample", "a" * 32)
        self.assertIn("already used", result["error"])


if __name__ == "__main__":
    unittest.main()
