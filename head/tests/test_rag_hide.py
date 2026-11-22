"""Benchmark reruns start blind: passages about the job are hidden while the hide list is active, the underlying library remains untouched, and tests use temp folders."""
import json
import os
import tempfile
import unittest
from unittest import mock

import coding_client
import config
import rag


class TheHideList(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = mock.patch.object(config, "STATE_DIR", self.temp.name)
        p.start()
        self.addCleanup(p.stop)
        self.docs = os.path.join(self.temp.name, "docs")
        os.makedirs(self.docs)
        docs = {"a": "# Notes\n\nThe gadget cookie cutter job was graded a draw.\n", "b": "# Other\n\nThe gadget server runs on the blue box.\n"}
        for i in range(8):                 # BM25 scores a word by how rare it is: most of the corpus must not hold it
            docs["filler%d" % i] = "# Filler %d\n\nPlain words about the weather number %d.\n" % (i, i)
        for name, text in docs.items():
            with open(os.path.join(self.docs, name + ".md"), "w", encoding="utf-8") as f:
                f.write(text)
        rag.configure("hide-test", [self.docs])
        self.addCleanup(rag._NAMESPACES.pop, "hide-test", None)

    def _hide(self, words):
        with open(rag.hide_file(), "w", encoding="utf-8") as f:
            json.dump({"words": words}, f)

    def test_with_no_list_nothing_changes(self):
        self.assertTrue(any("draw" in h["text"] for h in rag.search("gadget", k=4, namespace="hide-test")))
        self.assertEqual(rag.without_hidden("a\n\nb"), "a\n\nb")

    def test_a_listed_word_keeps_its_passages_out_and_the_rest_comes_through(self):
        self._hide(["Cookie Cutter"])
        hits = rag.search("gadget", k=4, namespace="hide-test")
        self.assertFalse([h for h in hits if "draw" in h["text"]], hits)
        self.assertTrue(any("blue box" in h["text"] for h in hits), "a neighbour stays: the positive control")

    def test_a_full_document_read_loses_only_the_listed_paragraphs(self):
        self._hide(["cookie cutter"])
        got = rag.without_hidden("keep this\n\nthe cookie cutter job\n\nand this")
        self.assertEqual(got, "keep this\n\nand this")

    def test_catch_up_reads_the_journals_through_the_hide_list_too(self):
        """catch_up reads journals directly from disk, bypassing the in-memory hide state; this test ensures it still respects the hide list."""
        import tools
        self._hide(["cookie cutter"])
        with mock.patch.object(tools.catch_up_mod, "catch_up", lambda topic="", days=2: "keep this\n\nthe cookie cutter job\n\nand this"):
            got = tools._run_tool_impl("catch_up", {}, "")
        self.assertEqual(got, "keep this\n\nand this")

    def test_turning_it_off_is_removing_the_file_and_a_bad_file_hides_nothing(self):
        self._hide(["cookie cutter"])
        os.remove(rag.hide_file())
        self.assertEqual(rag.hidden_words(), [])
        with open(rag.hide_file(), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(rag.hidden_words(), [])


class HiddenTasks(unittest.TestCase):
    """Verifies that a new job is informed about unfinished tasks from a previous run."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = mock.patch.object(config, "STATE_DIR", self.temp.name)
        p.start()
        self.addCleanup(p.stop)
        self.calls = []
        def fake(method, path, **kw):
            self.calls.append((method, path))
            return {"tasks": [{"id": "old", "objective": "the earlier attempt"}, {"id": "new", "objective": "this job"}]}
        q = mock.patch.object(coding_client, "_request", fake)
        q.start()
        self.addCleanup(q.stop)

    def _hide(self, ids):
        with open(rag.hide_file(), "w", encoding="utf-8") as f:
            json.dump({"tasks": ids}, f)

    def test_with_no_list_every_task_shows(self):
        self.assertEqual([t["id"] for t in coding_client.task("list")["tasks"]], ["old", "new"])

    def test_a_hidden_task_is_not_listed_and_its_neighbour_is(self):
        self._hide(["old"])
        self.assertEqual([t["id"] for t in coding_client.task("list")["tasks"]], ["new"])

    def test_a_hidden_task_cannot_be_opened_and_the_runner_is_not_asked(self):
        self._hide(["old"])
        for action in ("resume", "get", "update"):
            self.assertEqual(coding_client.task(action, task_id="old"), {"error": "no such task"})
        self.assertEqual(self.calls, [])

    def test_the_note_a_new_job_gets_leaves_it_out(self):
        self._hide(["old"])
        import app
        messages = [{"role": "user", "content": "build a thing"}]
        app._inject_unfinished_tasks(messages, "build a thing", True)
        said = json.dumps(messages)
        self.assertIn("this job", said)
        self.assertNotIn("the earlier attempt", said)


if __name__ == "__main__":
    unittest.main()
