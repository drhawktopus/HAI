"""Verify that interrupted writing calls (create/patch/file) are recovered exactly once from workspace state,
never guessed, and reported accurately."""
import base64
import hashlib
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools  # noqa: E402


class AWritingCallARestartCutIsFinishedOnce(unittest.TestCase):
    def setUp(self):
        self.files, self.ran = {}, []

        def read_bytes(ws, path):
            if path not in self.files:
                return {"error": "path does not exist: %s" % path}
            data = self.files[path]
            return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "data_b64": base64.b64encode(data).decode()}
        for p in (mock.patch.object(tools.coding_client, "read_bytes", read_bytes),
                  mock.patch.object(tools, "_run_tool_impl", lambda name, args, *a, **k: self.ran.append(name) or "RAN")):
            p.start()
            self.addCleanup(p.stop)

    def test_a_create_already_written_is_not_written_again(self):
        self.files["a.py"] = b"x = 1\n"
        out = json.loads(tools.resume_tool("coding_create", {"workspace": "w", "path": "a.py", "content": "x = 1\n"}, None))
        self.assertIn("not written again", out["resumed"])
        self.assertEqual(self.ran, [])

    def test_a_create_that_never_landed_runs_once(self):
        self.assertEqual(tools.resume_tool("coding_create", {"workspace": "w", "path": "a.py", "content": "x"}, None), "RAN")
        self.assertEqual(self.ran, ["coding_create"])

    def test_a_patch_already_applied_is_not_applied_again(self):
        self.files["a.py"] = b"x = 2\n"
        out = json.loads(tools.resume_tool("coding_patch", {"workspace": "w", "path": "a.py", "old": "x = 1", "new": "x = 2"}, None))
        self.assertIn("not applied again", out["resumed"])
        self.assertEqual(self.ran, [])

    def test_a_patch_that_never_landed_runs_once(self):
        self.files["a.py"] = b"x = 1\n"
        sha = hashlib.sha256(b"x = 1\n").hexdigest()
        self.assertEqual(tools.resume_tool("coding_patch", {"workspace": "w", "path": "a.py", "old": "x = 1", "new": "x = 2",
                                                             "expected_sha256": sha}, None), "RAN")

    def test_a_patch_whose_new_text_contains_the_old_is_judged_by_the_hash_not_the_text(self):
        self.files["a.py"] = b"x = 1\n"
        sha = hashlib.sha256(b"x = 1\n").hexdigest()
        self.assertEqual(tools.resume_tool("coding_patch", {"workspace": "w", "path": "a.py", "old": "x = 1",
                                                             "new": "x = 1\ny = 2", "expected_sha256": sha}, None),
                         "RAN", "untouched since it was read, so it was never applied")

    def test_a_move_already_made_is_not_made_again(self):
        self.files["b.py"] = b"x"
        out = json.loads(tools.resume_tool("coding_file", {"workspace": "w", "action": "move", "path": "a.py",
                                                            "destination": "b.py"}, None))
        self.assertIn("not moved again", out["resumed"])
        self.assertEqual(self.ran, [])

    def test_a_runner_that_cannot_be_asked_is_said_never_guessed(self):
        with mock.patch.object(tools.coding_client, "read_bytes", lambda ws, p: {"error": "coding runner unavailable"}):
            out = json.loads(tools.resume_tool("coding_create", {"workspace": "w", "path": "a.py", "content": "x"}, None))
        self.assertIn("could not be told", out["error"])
        self.assertEqual(self.ran, [])


if __name__ == "__main__":
    unittest.main()
