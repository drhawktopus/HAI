import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_jobs as GJ

PAYLOAD = """
TASKS = {"add": {"checks": ["lambda: add(2, 3) == 5"]}}
CONTROL_TASKS = {"control_add": {"checks": ["lambda: add(2,3)==5", "lambda: add(-1,1)==0", "lambda: add(100,-50)==50"]}}
def extract_code(text):
    return text
def score(code, checks, work):
    ns = {}
    try:
        exec(code, ns)
        ok = all(eval(c, ns)() for c in checks)
    except Exception as e:
        return 0, len(checks), repr(e)
    return (len(checks) if ok else 0), len(checks), ""
"""
GOOD, BAD = "def add(a, b):\n    return a + b\n", "def add(a, b):\n    return a - b\n"


class _Fake:
    """A payload: extract_code marks that it was used; score runs the code and its checks."""
    @staticmethod
    def extract_code(text):
        return "EXTRACTED = True\n"

    @staticmethod
    def score(code, checks, work):
        ns = {}
        try:
            exec(code, ns)
            ok = all(eval(c, ns)() for c in checks)
        except Exception as e:
            return 0, len(checks), repr(e)
        return (len(checks) if ok else 0), len(checks), ""


class AnswerAsWritten(unittest.TestCase):
    """Lenient scoring: each case below is one an audit of real answers found."""
    OPS = {"checks": ["lambda: tuple(ANSWER) == ('D', 'A')"]}

    def grade(self, answer, tdef=None):
        p, n, _ = GJ.score_answer(_Fake, tdef or self.OPS, answer, ".")
        return p == n

    def test_an_illustrative_fence_before_the_answer_fence_is_context(self):
        a = "Rebuild it:\n\n```\ndocker compose build app\n```\n\nSo:\n\n```python\nANSWER = (\"D\", \"A\")\n```"
        self.assertTrue(self.grade(a), "the strict first-fence rule graded the shell command and failed a right answer")

    def test_a_bare_answer_line_is_the_answer(self):
        self.assertTrue(self.grade("The cause is D and the fix is A.\n\nANSWER = ('D', 'A')."))

    def test_not_stated_is_the_sentinel_word(self):
        self.assertTrue(self.grade("ANSWER = NOT_STATED", {"checks": ["lambda: ANSWER == 'NOT_STATED'"]}))

    def test_two_different_answers_is_a_hedge_and_never_passes(self):
        a = "```python\nANSWER = ('B', 'C')\n```\nor maybe\n```python\nANSWER = ('D', 'A')\n```"
        self.assertFalse(self.grade(a))
        self.assertFalse(self.grade("ANSWER = ('B', 'C')\nANSWER = ('D', 'A')"))
        self.assertIn("hedged", GJ.answer_code(_Fake, a))

    def test_the_same_answer_twice_is_not_a_hedge(self):
        self.assertTrue(self.grade("```python\nANSWER = ('D', 'A')\n```\nAgain:\n```python\nANSWER = ('D','A')\n```"))

    def test_a_wrong_answer_still_fails(self):
        self.assertFalse(self.grade("```python\nANSWER = ('B', 'B')\n```"))

    def test_code_tasks_keep_the_first_fence_and_the_extractor(self):
        self.assertEqual(GJ.answer_code(_Fake, "```python\ndef f():\n    pass\n```\n```\nf()\n```"), "def f():\n    pass\n")
        self.assertEqual(GJ.answer_code(_Fake, "no fence at all"), "EXTRACTED = True\n")

    def test_the_grader_change_re_keys_the_cache(self):
        self.assertIn("answer_code", __import__("inspect").getsource(GJ.fingerprint))


class GradeJobs(unittest.TestCase):
    def _run(self, fp=None, controls=None):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "pay.py"), "w", encoding="utf-8") as f:
            f.write(PAYLOAD)
        job = lambda k, a, **x: dict({"key": k, "payload": "pay.py", "task": "add", "answer": a}, **x)
        spec = {"fingerprint": fp or GJ.fingerprint(), "payload_dir": d,
                "controls": controls if controls is not None else [job("c1", GOOD, expect=True),
                                                                    job("c2", BAD, expect=False)],
                "jobs": [job("j1", GOOD), job("j2", BAD)]}
        jp, op = os.path.join(d, "jobs.json"), os.path.join(d, "out", "grades.jsonl")
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        out = io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", ["grade_jobs.py", jp, op, "--workers", "1"]), \
             mock.patch.object(sys, "stdout", out), mock.patch.object(out, "reconfigure", lambda **k: None, create=True):
            try:
                GJ.main()
            except SystemExit as e:
                code = e.code
        grades = [json.loads(ln) for ln in open(op, encoding="utf-8")] if os.path.exists(op) else []
        return code, out.getvalue(), grades

    def test_grades_every_job_and_prints_progress(self):
        code, out, grades = self._run()
        self.assertEqual(code, 0)
        self.assertEqual({k: g["ok"] for k, g in grades}, {"j1": True, "j2": False})
        self.assertIn("[grade] 2/2 ", out, "progress lines in the foundry's [tag] N/M shape")

    def test_resume_keeps_existing_grades_and_skips_them(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "pay.py"), "w", encoding="utf-8") as f:
            f.write(PAYLOAD)
        job = lambda k, a, **x: dict({"key": k, "payload": "pay.py", "task": "add", "answer": a}, **x)
        spec = {"fingerprint": GJ.fingerprint(), "payload_dir": d,
                "controls": [job("c1", GOOD, expect=True), job("c2", BAD, expect=False)],
                "jobs": [job("j1", GOOD), job("j2", BAD)]}
        jp, op = os.path.join(d, "jobs.json"), os.path.join(d, "grades.jsonl")
        json.dump(spec, open(jp, "w", encoding="utf-8"))
        with open(op, "w", encoding="utf-8") as f:
            f.write(json.dumps(["j1", {"ok": True, "checks_passed": 1, "checks_total": 1}]) + "\n")
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["grade_jobs.py", jp, op, "--workers", "1"]), \
             mock.patch.object(sys, "stdout", out), mock.patch.object(out, "reconfigure", lambda **k: None, create=True):
            GJ.main()
        keys = [json.loads(ln)[0] for ln in open(op, encoding="utf-8")]
        self.assertEqual(keys, ["j1", "j2"], "j1 kept from before, only j2 graded now")
        self.assertIn("resuming: 1 already graded", out.getvalue())

    def test_a_different_grader_refuses_with_exit_2(self):
        code, out, grades = self._run(fp="000000000000")
        self.assertEqual((code, grades), (2, []))
        self.assertIn("REFUSED", out)

    def test_a_box_that_grades_a_known_answer_differently_refuses(self):
        wrong = [{"key": "c1", "payload": "pay.py", "task": "add", "answer": GOOD, "expect": False}]
        code, out, grades = self._run(controls=wrong)
        self.assertEqual((code, grades), (2, []))
        self.assertIn("python3", out)


class MapMode(unittest.TestCase):
    """Tolkien's contract: python grade_jobs.py items.txt out.json, the scorer staged beside the payload."""

    def _run(self, jobs, payload=PAYLOAD):
        d = tempfile.mkdtemp()
        here = os.path.join(d, "stage")
        os.makedirs(here)
        with open(os.path.join(here, "pay.py"), "w", encoding="utf-8") as f:
            f.write(payload)
        items = os.path.join(here, "items.txt")
        with open(items, "w", encoding="utf-8") as f:
            f.write("\n".join(GJ.map_item(j) for j in jobs) + "\n")
        out = os.path.join(here, "out.json")
        with mock.patch.object(GJ.os.path, "abspath", lambda p: os.path.join(here, "grade_jobs.py")
                               if p == GJ.__file__ else os.path.normpath(os.path.join(os.getcwd(), p))), \
             mock.patch.object(sys, "stdout", io.StringIO()):
            GJ.map_main(items, out)
        return json.load(open(out, encoding="utf-8")), items

    def _job(self, key, ans, **x):
        return dict({"key": key, "payload": "pay.py", "task": "add", "answer": ans,
                     "fingerprint": GJ.fingerprint()}, **x)

    def test_one_row_per_item_echoing_it_with_the_grade(self):
        rows, items = self._run([self._job("k1", GOOD), self._job("k2", BAD)])
        lines = [ln.strip() for ln in open(items, encoding="utf-8") if ln.strip()]
        self.assertEqual([r["item"] for r in rows], lines, "every row echoes its item (Tolkien's coverage key)")
        self.assertEqual({r["key"]: r["grade"]["ok"] for r in rows}, {"k1": True, "k2": False})
        self.assertTrue(all("|" not in ln for ln in lines))

    def test_a_control_row_says_ok_only_when_it_grades_as_known(self):
        rows, _ = self._run([self._job("c", GOOD, expect=True), self._job("w", GOOD, expect=False)])
        self.assertEqual({r["key"]: r["control"] for r in rows}, {"c": "GRADE_CONTROL_OK", "w": "GRADE_CONTROL_WRONG"})

    def test_a_job_from_a_different_grader_is_an_error_row_not_a_grade(self):
        rows, _ = self._run([self._job("k1", GOOD, fingerprint="000000000000")])
        self.assertIsNone(rows[0]["grade"])
        self.assertIn("fingerprint", rows[0]["error"])

    def test_a_node_that_cannot_run_code_grades_nothing(self):
        broken = PAYLOAD.replace("ok = all(eval(c, ns)() for c in checks)", "ok = False")
        rows, _ = self._run([self._job("k1", GOOD), self._job("c", GOOD, expect=True)], payload=broken)
        self.assertTrue(all(r["grade"] is None and "SELFTEST_FAILED" in r["error"] for r in rows), rows)
        self.assertNotIn("GRADE_CONTROL_OK", json.dumps(rows), "a broken node can never pass the order's control")


if __name__ == "__main__":
    unittest.main()
