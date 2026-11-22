"""Override re-answers (reconsideration) before persisting via `history.apply_override`; tests run in a temp HISTORY_DIR to isolate from live history."""
import pathlib
import shutil
import tempfile
import unittest

try:
    import history
    _OK = True
except Exception:
    _OK = False


@unittest.skipUnless(_OK, "history not importable in this environment")
class OverrideHistoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._saved = history.HISTORY_DIR
        history.HISTORY_DIR = pathlib.Path(self._tmp)

    def tearDown(self):
        history.HISTORY_DIR = self._saved
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _seed_refused(self, user):
        cid = history.resolve_chat(user)
        history.append(user, "a hard question",
                       "Withheld: refused on a critical finding.", chat=cid,
                       extras={"refused_draft": "The real drafted answer.",
                               "unresolved_findings": [{"severity": "critical", "text": "x"}]})
        return cid

    def test_apply_override_stores_the_answer_and_reason(self):
        user = "tester"
        cid = self._seed_refused(user)
        ok = history.apply_override(user, "the reconsidered answer",
                                    reason="verified the finding is wrong", chat=cid)
        self.assertTrue(ok)
        last = history.load(user, cid)[-1]
        self.assertEqual(last["content"], "the reconsidered answer")
        self.assertTrue(last["overridden"])
        self.assertEqual(last["override_reason"], "verified the finding is wrong")

    def test_apply_override_false_when_nothing_to_override(self):
        user = "tester2"
        cid = history.resolve_chat(user)
        history.append(user, "hi", "a normal answer", chat=cid)
        self.assertFalse(history.apply_override(user, "x", chat=cid))

    def test_no_double_override(self):
        user = "tester3"
        cid = self._seed_refused(user)
        self.assertTrue(history.apply_override(user, "first", chat=cid))
        self.assertFalse(history.apply_override(user, "second", chat=cid))

    def test_refused_context_returns_question_draft_findings(self):
        user = "tester4"
        cid = self._seed_refused(user)
        ctx = history.refused_context(user, cid)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["draft"], "The real drafted answer.")
        self.assertEqual(ctx["question"], "a hard question")
        self.assertTrue(ctx["findings"])

    def test_refused_context_none_when_no_refusal(self):
        user = "tester5"
        cid = history.resolve_chat(user)
        history.append(user, "hi", "normal", chat=cid)
        self.assertIsNone(history.refused_context(user, cid))

    def test_refused_context_none_after_override(self):
        user = "tester6"
        cid = self._seed_refused(user)
        history.apply_override(user, "done", chat=cid)
        self.assertIsNone(history.refused_context(user, cid))


if __name__ == "__main__":
    unittest.main()
