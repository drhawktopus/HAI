"""A bulk replace goes through the same door as a patch, and its preview is not optional.

The rule: a model may replace a literal or a regex across many files, but only after reading
the dry run. The tool's description states that order, the dispatch passes it to the runner
unchanged, and the runner's own refusal keeps a half-applied change from landing. A replace
is a checked write: if a restart cuts it, the workspace is asked rather than replayed.
"""
import json
import unittest
from unittest import mock

import tools


def _names(pool):
    return [t["function"]["name"] for t in pool]


class TheTool(unittest.TestCase):
    def test_it_sits_beside_coding_patch_in_the_coding_pool(self):
        names = _names(tools.CODING_TOOLS)
        self.assertIn("coding_replace", names)
        self.assertEqual(names.index("coding_replace"), names.index("coding_patch") + 1,
                        "the pool keeps its order otherwise")

    def test_the_description_tells_the_model_to_dry_run_first(self):
        entry = next(t for t in tools.CODING_TOOLS if t["function"]["name"] == "coding_replace")
        description = entry["function"]["description"]
        self.assertIn("ALWAYS dry-run first", description)
        self.assertIn("Read the preview", description)
        self.assertIn("refused", description, "the caller must know an apply can be refused")
        self.assertIn("max_files", entry["function"]["parameters"]["required"])
        self.assertNotIn("files", entry["function"]["parameters"]["required"],
                        "the file list is only needed to apply, not to preview")

    def test_it_is_in_the_job_set_and_survives_selection(self):
        job_names = {t["function"]["name"] for t in tools.CODING_JOB_TOOLS}
        self.assertIn("coding_replace", job_names)
        survey_names = {t["function"]["name"] for t in tools.CODING_SURVEY_TOOLS}
        self.assertNotIn("coding_replace", survey_names, "a survey pass reads, it does not write")

    def test_a_replaced_call_can_be_resumed(self):
        self.assertTrue(tools.can_resume("coding_replace"))
        self.assertIn("coding_replace", tools.CHECKED_WRITES)


class TheDispatch(unittest.TestCase):
    def setUp(self):
        self.seen = []

        def fake_replace(workspace, glob, find, replace, regex=False, max_files=100,
                         dry_run=True, files=None, task_id=None):
            self.seen.append({"workspace": workspace, "glob": glob, "find": find,
                              "replace": replace, "regex": regex, "max_files": max_files,
                              "dry_run": dry_run, "files": files, "task_id": task_id})
            return {"dry_run": dry_run, "files": [{"path": "a.py", "sha256": "abc", "matches": 2}]}

        patch = mock.patch.object(tools.coding_client, "replace", fake_replace)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_arguments_reach_the_runner_unchanged(self):
        out = tools.run_tool("coding_replace", {
            "workspace": "orchestrator-real", "glob": "tests/**/*.py",
            "find": "10.0.0.1", "replace": "localhost", "regex": True, "max_files": 40,
        }, "replace the addresses", user="Alex")
        self.assertEqual(self.seen[0]["glob"], "tests/**/*.py")
        self.assertEqual(self.seen[0]["find"], "10.0.0.1")
        self.assertEqual(self.seen[0]["replace"], "localhost")
        self.assertTrue(self.seen[0]["regex"])
        self.assertEqual(self.seen[0]["max_files"], 40)
        self.assertTrue(self.seen[0]["dry_run"], "omitted dry_run means preview, not write")

    def test_an_apply_carries_the_preview_list(self):
        files = [{"path": "a.py", "sha256": "abc"}]
        tools.run_tool("coding_replace", {
            "workspace": "orchestrator-real", "glob": "tests/**/*.py", "find": "x", "replace": "y",
            "max_files": 40, "dry_run": False, "files": files, "task_id": "T1",
        }, "apply it", user="Alex")
        self.assertFalse(self.seen[0]["dry_run"])
        self.assertEqual(self.seen[0]["files"], files)
        self.assertEqual(self.seen[0]["task_id"], "T1")

    def test_a_cut_replace_that_found_nothing_left_is_reported_as_landed(self):
        """The pattern is gone from the workspace, so the change already applied."""
        def nothing_left(*args, **kw):
            return {"dry_run": True, "file_count": 0, "files": []}

        patch = mock.patch.object(tools.coding_client, "replace", nothing_left)
        patch.start()
        self.addCleanup(patch.stop)
        result = json.loads(tools.resume_tool("coding_replace", {
            "workspace": "orchestrator-real", "glob": "tests/**/*.py",
            "find": "10.0.0.1", "replace": "localhost", "max_files": 40,
        }, None))
        self.assertIn("resumed", result, "no match left means it already landed")
        self.assertNotIn("files", result, "nothing was written again")

    def test_a_cut_replace_that_still_matches_is_not_guessed(self):
        asked = []

        def still_matching(*args, **kw):
            asked.append(kw.get("dry_run"))
            return {"dry_run": True, "file_count": 3, "files": [{"path": "a.py"}]}

        patch = mock.patch.object(tools.coding_client, "replace", still_matching)
        patch.start()
        self.addCleanup(patch.stop)
        result = json.loads(tools.resume_tool("coding_replace", {
            "workspace": "orchestrator-real", "glob": "tests/**/*.py",
            "find": "10.0.0.1", "replace": "localhost", "max_files": 40,
        }, None))
        self.assertIn("still match", result["error"])
        self.assertEqual(asked, [True], "the check is a preview, never a second write")


if __name__ == "__main__":
    unittest.main()