"""fetch_audit (B8): a trial whose helpers fetched a page holding its task's own tests or reference solution LEAKED;
every trial with an errands.json is listed; a key with no distinctive line is an ERROR, never clean."""
import json
import os
import shutil
import tempfile
import unittest

import fetch_audit as A

TEST_LINE = "assert parse_duration('1h30m') == 5400, 'durations must add up across units'"
SOLVE_LINE = "python3 -c \"import sys; print(sum(int(x) for x in sys.stdin.read().split()))\" > /app/out.txt"


class FetchAudit(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.ds = os.path.join(self.d, "dataset")
        for task, sub, name, text in (("task-a", "tests", "test_outputs.py", "import x\n" + TEST_LINE + "\n"),
                                      ("task-b", "solution", "solve.sh", "#!/bin/sh\n" + SOLVE_LINE + "\n"),
                                      ("task-c", "tests", "test.sh", "set -e\nrun\n")):
            os.makedirs(os.path.join(self.ds, task, sub), exist_ok=True)
            with open(os.path.join(self.ds, task, sub, name), "w", encoding="utf-8") as f:
                f.write(text)
        self.run = os.path.join(self.d, "run")

    def trial(self, name, recs):
        p = os.path.join(self.run, "jobs", "2026-10-17__02-00-00", name, "agent")
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "errands.json"), "w", encoding="utf-8") as f:
            json.dump(recs, f)

    def test_a_fetched_test_or_solution_line_is_a_leak_and_every_trial_is_listed(self):
        self.trial("task-a__1", [{"fetches": [{"url": "https://mirror/x", "text": "page...\n" + TEST_LINE + "\n..."}]}])
        self.trial("task-b__2", [{"fetches": [{"url": "https://blog/y", "text": "see: " + SOLVE_LINE}]}])
        self.trial("task-a__3", [{"fetches": [{"url": "https://docs/z", "text": "unrelated documentation"}]}])
        self.trial("task-a__4", [{"status": "answered", "looks": 2}])                    # no fetch at all
        out = A.audit(self.run, self.ds)
        self.assertEqual(set(out), {"task-a__1", "task-b__2", "task-a__3", "task-a__4"})
        self.assertTrue(out["task-a__1"]["leak"])
        self.assertEqual(out["task-a__1"]["matched"], [TEST_LINE])
        self.assertTrue(out["task-b__2"]["leak"], "the reference solution is the key too")
        self.assertFalse(out["task-a__3"]["leak"])
        self.assertEqual((out["task-a__4"]["fetches"], out["task-a__4"]["leak"]), (0, False))

    def test_a_key_with_no_distinctive_line_cannot_be_audited_and_says_so(self):
        self.trial("task-c__1", [{"fetches": [{"url": "https://x", "text": "anything"}]}])
        out = A.audit(self.run, self.ds)
        self.assertIn("cannot be audited", out["task-c__1"]["error"])

    def test_main_writes_the_audit_beside_the_run(self):
        self.trial("task-a__1", [{"fetches": []}])
        A.main([self.run, self.ds])
        with open(os.path.join(self.run, "fetch_audit.json"), encoding="utf-8") as f:
            self.assertIn("task-a__1", json.load(f))


if __name__ == "__main__":
    unittest.main()
