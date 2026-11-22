"""Test conversation branching: forks must continue from the selected answer,
leave the source thread untouched, and rely on message text to detect stale
counts."""
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import history  # noqa: E402

USER = "BranchUser"   # not a test* user: those are ephemeral and persist nothing


class BranchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._hist = history.HISTORY_DIR
        history.HISTORY_DIR = pathlib.Path(self.tmp) / "history"
        self.src = history.new_chat(USER)["id"]
        for n in range(3):
            history.append(USER, "question %d" % n, "answer %d" % n, chat=self.src)

    def tearDown(self):
        history.HISTORY_DIR = self._hist
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_branch_continues_from_that_answer_and_leaves_the_source_alone(self):
        entry = history.branch_chat(USER, self.src, 2)
        self.assertNotEqual(entry["id"], self.src)
        self.assertEqual([m["content"] for m in history.load(USER, entry["id"])],
                         ["question 0", "answer 0", "question 1", "answer 1"])
        self.assertEqual(len(history.load(USER, self.src)), 6, "the source keeps every message")
        self.assertEqual(history.list_chats(USER)["active"], entry["id"], "the branch is where the user lands")
        self.assertEqual(entry["branched_from"], {"chat": self.src, "exchanges": 2})
        self.assertTrue(entry["title"].startswith("⑂"))

    def test_a_stale_count_is_corrected_by_the_answers_own_text(self):
        entry = history.branch_chat(USER, self.src, 3, check="answer 1")
        self.assertEqual(history.load(USER, entry["id"])[-1]["content"], "answer 1")

    def test_no_such_answer_or_chat_makes_no_branch(self):
        self.assertIsNone(history.branch_chat(USER, self.src, 4))
        self.assertIsNone(history.branch_chat(USER, self.src, 0))
        self.assertIsNone(history.branch_chat(USER, "nochat", 1))
        self.assertIsNone(history.branch_chat(USER, self.src, 1, check="never said"))

    def test_the_endpoint_branches_and_returns_the_chat_list(self):
        from fastapi.testclient import TestClient
        r = TestClient(app.app).post("/chats/branch", params={"user": USER, "chat": self.src, "exchanges": 1,
                                                               "check": "answer 0"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["active"], d["chat"]["id"])
        self.assertEqual(len(history.load(USER, d["chat"]["id"])), 2)


if __name__ == "__main__":
    unittest.main()
