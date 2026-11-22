"""lab_errands: the look-only gate, the briefs parser, the errand loop's A1 rules, the report block."""
import asyncio
import json
import unittest

import lab_errands as E


def run(coro):
    return asyncio.run(coro)


class LookOnly(unittest.TestCase):
    def test_reads_pass_and_changes_are_refused(self):
        for ok in ("ls -la /app", "cat src/main.py | head -40", "grep -rn 'def main' /app", "find /app -name '*.py'",
                   "wc -l /app/data/INPUT.DAT", "od -c /app/src/INPUT.DAT"):
            self.assertTrue(E.is_look(ok), ok)
        for bad in ("rm -rf /app", "echo x > f", "cat a >> b", "ls; rm x", "ls && rm x", "find . -delete",
                    "find . -exec rm {} \\;", "sort -o out in", "python3 -c 'open(1)'", "sed -i s/a/b/ f",
                    "cat $(which x)", "ls `pwd`", "apt install p7zip", "tee f", "", "cat a | sh"):
            self.assertFalse(E.is_look(bad), bad)


class Briefs(unittest.TestCase):
    def test_briefs_come_from_the_terminus_answer(self):
        ans = json.dumps({"analysis": "a", "plan": "p", "commands": [],
                          "errands": [{"brief": "find the entry point"}, "count the .py files", {"brief": " "}]})
        self.assertEqual(E.briefs_of("text before " + ans), ["find the entry point", "count the .py files"])
        self.assertEqual(E.briefs_of('{"commands": []}'), [])
        self.assertEqual(E.briefs_of("not json"), [])


class ErrandLoop(unittest.TestCase):
    def errand(self, replies, outputs=None):
        replies = iter(replies)
        ran = []

        async def ask(msgs):
            return next(replies)

        async def look(cmd):
            ran.append(cmd)
            return (outputs or {}).get(cmd, "out of %s" % cmd)
        return run(E.run_errand("find main", ask, look)), ran

    def test_look_then_report(self):
        rec, ran = self.errand(['{"commands": ["ls /app"]}', '{"report": "main is in /app/cli.py"}'])
        self.assertEqual((rec["status"], rec["report"], rec["looks"]), ("answered", "main is in /app/cli.py", 1))
        self.assertEqual(ran, ["ls /app"])

    def test_a_report_before_any_look_is_nudged_once_then_marked_unlooked(self):
        rec, _ = self.errand(['{"report": "guess"}', '{"report": "guess again"}'])
        self.assertEqual((rec["status"], rec["turns"]), ("unlooked", 2))

    def test_refused_commands_never_run(self):
        rec, ran = self.errand(['{"commands": ["rm -rf /app", "ls /app"]}', '{"report": "r"}'])
        self.assertEqual(ran, ["ls /app"])
        self.assertEqual(rec["looks"], 1)

    def test_the_same_look_three_times_gets_the_answer_now_nudge_then_no_answer(self):
        rec, ran = self.errand(['{"commands": ["ls /app"]}'] * 4)
        self.assertEqual((rec["status"], len(ran)), ("no_answer", 3))

    def test_answer_after_the_nudge_is_kept(self):
        rec, _ = self.errand(['{"commands": ["ls /app"]}'] * 3 + ['{"report": "found it"}'])
        self.assertEqual((rec["status"], rec["report"]), ("answered", "found it"))


class SimpleCommandsAndFetch(unittest.TestCase):
    """B8: a RUN touches only the errand's private copy (relative paths, one command, no installing); a FETCH never
    reaches a benchmark source."""

    def test_simple_commands_in_the_copy_pass(self):
        for ok in ("python3 check.py", "make test", "pytest -q visible_tests", "gcc -o a a.c", "bash run.sh",
                   "python3 check.py > out.txt", "python3 x.py 2>&1 | tail -5", "ls src", "sed -i s/a/b/ f.txt"):
            self.assertTrue(E.is_simple_command(ok), ok)

    def test_anything_reaching_outside_the_copy_or_changing_the_system_is_refused(self):
        for bad in ("python3 /app/x.py", "cat ../secret", "ls ~", "pip install x", "python3 -m pip install x",
                    "npm install", "apt-get install p7zip", "sudo ls", "make; rm -rf x", "make && ls", "make || ls",
                    "sleep 5 &", "bash -c 'rm x'", "bash", "cd /app", "python3 -c \"open('/app/x','w')\"",
                    "curl https://x", "cargo install ripgrep", "cat $(ls)", "ls `pwd`", ""):
            self.assertFalse(E.is_simple_command(bad), bad)

    def test_fetch_refuses_benchmark_sources_and_anything_but_a_plain_url(self):
        self.assertIsNone(E.fetch_refusal("https://docs.python.org/3/library/csv.html"))
        for bad in ("https://github.com/laude-institute/terminal-bench/tree/main/tasks",
                    "https://www.tbench.ai/registry", "https://huggingface.co/datasets/x/terminal-bench-2",
                    "ftp://x.org/a", "https://x.org/a b", "https://x.org/a';rm -rf /", "file:///etc/passwd"):
            self.assertIsNotNone(E.fetch_refusal(bad), bad)


class CommandsLoop(unittest.TestCase):
    def errand(self, replies, mode="commands"):
        replies, ran, fetched = iter(replies), [], []

        async def ask(msgs):
            return next(replies)

        async def look(cmd):
            return "looked %s" % cmd

        async def run(cmd):
            ran.append(cmd)
            return "ran %s" % cmd

        async def fetch(url):
            fetched.append(url)
            return "page of %s" % url
        return asyncio.run(E.run_errand("check the build", ask, look, mode=mode, run=run, fetch=fetch)), ran, fetched

    def test_run_and_fetch_are_executed_counted_and_the_fetch_text_recorded(self):
        rec, ran, fetched = self.errand(['{"run": ["make test"]}', '{"fetch": "https://docs.python.org/3/"}',
                                         '{"report": "2 tests fail in test_io"}'])
        self.assertEqual((rec["status"], rec["runs"], rec["looks"]), ("answered", 1, 2))
        self.assertEqual(ran, ["make test"])
        self.assertEqual(rec["fetches"], [{"url": "https://docs.python.org/3/", "chars": len("page of https://docs.python.org/3/"),
                                           "text": "page of https://docs.python.org/3/"}])

    def test_refused_runs_and_fetches_never_execute(self):
        rec, ran, fetched = self.errand(['{"run": ["python3 /app/x.py"], "fetch": "https://tbench.ai/x"}',
                                         '{"report": "r"}', '{"report": "r"}'])
        self.assertEqual((ran, fetched, rec["status"]), ([], [], "unlooked"))

    def test_the_same_run_three_times_gets_the_answer_now_nudge(self):
        rec, ran, _ = self.errand(['{"run": ["make test"]}'] * 4)
        self.assertEqual((rec["status"], len(ran)), ("no_answer", 3))

    def test_look_mode_never_runs_or_fetches_and_keeps_its_record_shape(self):
        rec, ran, fetched = self.errand(['{"run": ["make test"], "fetch": "https://docs.python.org/"}',
                                         '{"commands": ["ls"]}', '{"report": "r"}'], mode="look")
        self.assertEqual((ran, fetched), ([], []))
        self.assertEqual(set(rec), {"brief", "status", "report", "turns", "looks"}, "A4's errands.json shape")


class Reports(unittest.TestCase):
    def test_done_and_pending_both_show(self):
        block = E.reports_block([{"brief": "b1", "status": "answered", "report": "r1"},
                                 {"brief": "b2", "status": "unlooked", "report": "r2"}], ["b3"])
        self.assertIn("Errand reports:", block)
        self.assertIn("-> r1", block)
        self.assertIn("without looking", block)
        self.assertIn("b3\n  -> still running", block)
        self.assertEqual(E.reports_block([], []), "")


if __name__ == "__main__":
    unittest.main()
