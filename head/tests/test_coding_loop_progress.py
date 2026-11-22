"""The coding tool loop must terminate based on outcome (answer, idleness, deadline), not turn or search counts."""
import json
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import ollama_client  # noqa: E402
import run_ledger  # noqa: E402
import tempfile  # noqa: E402
import shutil  # noqa: E402
import datetime  # noqa: E402


class _Clock:
    def __init__(self, step=1.0):
        self.t, self.step = 1000.0, step

    def __call__(self):
        self.t += self.step
        return self.t


class AJobSeesItsClock(unittest.TestCase):

    def test_the_line_goes_on_the_newest_tool_result_once(self):
        msgs = [{"role": "tool", "content": "old"}, {"role": "assistant", "content": ""}, {"role": "tool", "content": "new"}]
        ollama_client._clock_note(msgs, 5160, 10800)
        self.assertEqual(msgs[2]["content"], "new\n[Clock: 94 min used of your 180 min budget; 86 min left.]")
        self.assertEqual(msgs[0]["content"], "old", "a message already sent never changes: the engine reuses an exact prompt start")
        before = msgs[2]["content"]
        ollama_client._clock_note(msgs, 5100, 10800)
        self.assertEqual(msgs[2]["content"], before, "a turn asked again adds no second line")

    def test_past_the_end_says_zero_and_no_tool_result_is_no_line(self):
        msgs = [{"role": "tool", "content": "x"}]
        ollama_client._clock_note(msgs, -30, 600)
        self.assertIn("10 min used of your 10 min budget; 0 min left", msgs[0]["content"])
        none = [{"role": "user", "content": "go"}]
        ollama_client._clock_note(none, 60, 600)
        self.assertEqual(none, [{"role": "user", "content": "go"}])


class AJobsBudgetIsWorkingTime(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.led = run_ledger.RunLedger.create("r1", "stream", {}, root=self.d)

    def test_what_was_noted_is_what_a_restart_reads(self):
        self.led.note_working(1234.6)
        self.assertEqual(run_ledger.RunLedger("r1", root=self.d).working_seconds(10800), 1235)

    def test_a_run_from_before_this_counts_from_when_it_was_accepted(self):
        self.assertLess(self.led.working_seconds(10800), 60)
        info = self.led.info()
        info["accepted_at"] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)).isoformat()
        run_ledger._write_atomic(self.led.dir / "run.json", info)
        self.assertAlmostEqual(self.led.working_seconds(10800), 1800, delta=5)

    def test_a_run_held_for_longer_than_its_budget_keeps_a_whole_budget(self):
        info = self.led.info()
        info["accepted_at"] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).isoformat()
        run_ledger._write_atomic(self.led.dir / "run.json", info)
        self.assertEqual(self.led.working_seconds(10800), 0)


class CodingLoopProgressTests(unittest.TestCase):
    FINAL = "done: finished."

    def run_loop(self, script, results=None, finish_after=None, deadline=None, clock=None, tools_list=None):
        """script(turn) -> list of (tool, args) for that turn; the model answers FINAL after `finish_after`
        tool turns (None = never). results(tool, args) -> the tool's result text."""
        self.calls = 0
        self.seen = []
        tele = {}

        def fake_chat_once(host, model, messages, *a, **k):
            self.calls += 1
            if finish_after is not None and self.calls > finish_after:
                self.seen = [m.get("content") or "" for m in messages if m.get("role") == "tool"]
                return {"message": {"role": "assistant", "content": self.FINAL}}
            return {"message": {"role": "assistant", "tool_calls": [
                {"function": {"name": n, "arguments": a}} for n, a in script(self.calls)]}}

        def fake_explain(messages, *a, **k):
            self.seen = [m.get("content") or "" for m in messages if m.get("role") == "tool"]
            self.why = k.get("why")
            return "handoff"

        patches = [patch.object(ollama_client, "_chat_once", fake_chat_once),
                   patch.object(ollama_client, "_explain", fake_explain),
                   patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl),
                   patch.object(ollama_client.tools, "run_tool",
                                lambda fn, args, *a, **k: (results or (lambda f, x: "ok"))(fn, args))]
        if clock is not None:
            patches.append(patch.object(ollama_client.time, "monotonic", clock))
        for p in patches:
            p.start()
        try:
            with ollama_client.call_context(loop_deadline=deadline):
                answer, esc = ollama_client.chat(
                    "h", "m", [{"role": "user", "content": "x"}],
                    tools_list or [{"function": {"name": "coding_read"}}], telemetry=tele)
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertIsNone(esc)
        return answer, tele, "\n".join(self.seen)

    def test_repeating_the_same_call_ends_on_idle(self):
        answer, tele, seen = self.run_loop(lambda n: [("coding_read", {"path": "a.py"})])
        self.assertEqual(answer, "handoff")
        self.assertEqual(tele["ended"], "idle")
        # one new turn, then a trip that explains and restarts the count, then the trip that ends (A1's rule)
        n = config.CODING_IDLE_TURNS
        self.assertEqual(self.calls, 1 + n * (1 + ollama_client.IDLE_RESETS))
        self.assertEqual(tele.get("idle_resets"), ollama_client.IDLE_RESETS)
        # Every idle turn except the final one is flagged with the specific repeated action; a generic warning is insufficient to break the loop.
        self.assertEqual(seen.count("NO NEW WORK"), n * (1 + ollama_client.IDLE_RESETS) - 1)
        self.assertEqual(tele.get("nudges_idle"), n * (1 + ollama_client.IDLE_RESETS) - 1)
        self.assertIn("repeated coding_read", seen)
        self.assertEqual(seen.count("The count starts again now"), ollama_client.IDLE_RESETS)

    def test_new_work_after_the_reset_keeps_the_job_going(self):
        """The point of the reset: a model told why its repeats cannot work changes course and the job goes on."""
        n = config.CODING_IDLE_TURNS
        def stuck_then_unstuck(k):
            return [("coding_test", {"profile": "unit"})] if k <= 1 + n else [("coding_read", {"path": "f%d" % k})]
        answer, tele, _ = self.run_loop(stuck_then_unstuck, finish_after=1 + n + 5)
        self.assertEqual(answer, self.FINAL)
        self.assertNotIn("ended", tele)
        self.assertEqual(tele.get("idle_resets"), 1)

    def test_a_repeated_test_run_is_told_the_test_cannot_run_its_script(self):
        answer, tele, seen = self.run_loop(lambda n: [("coding_test", {"workspace": "w", "profile": "unit"})])
        self.assertEqual(tele["ended"], "idle")
        self.assertIn("repeated coding_test", seen)
        self.assertIn("call it from a test file (test_*.py)", seen)
        self.assertIn("1 more such turns in a row and the loop ends here", seen, "the last warning says it is the last")

    @staticmethod
    def _patch(k):
        return [("coding_patch", {"path": "a.py", "old": str(k)})]

    @staticmethod
    def _flip(f, a):
        """A patch result whose hash flips between two versions: the code goes back and forth."""
        return json.dumps({"path": "a.py", "new_sha256": "A" if int(a.get("old", 0)) % 2 else "B"})

    def test_rewriting_back_and_forth_trips_the_stall_rule_then_ends(self):
        """Distinct arguments on each flip prevent the repeat rule from triggering; the stall rule is required to detect this loop."""
        n = config.CODING_STALL_TURNS
        answer, tele, seen = self.run_loop(self._patch, results=self._flip)
        self.assertEqual(tele["ended"], "stall")
        self.assertEqual(self.calls, 2 + n * (1 + ollama_client.IDLE_RESETS), "two new states, then two trips")
        self.assertEqual(tele.get("idle_resets"), ollama_client.IDLE_RESETS)
        self.assertIn("NO PROGRESS: for %d turns" % n, seen)
        self.assertIn("returning to versions already tried", self.why)

    def test_real_progress_never_trips_it(self):
        answer, tele, _ = self.run_loop(self._patch, finish_after=40,
                                        results=lambda f, a: json.dumps({"new_sha256": "v" + a["old"]}))
        self.assertEqual(answer, self.FINAL)
        self.assertNotIn("ended", tele)
        self.assertNotIn("idle_resets", tele)

    def test_looking_at_something_new_is_progress_even_on_old_code(self):
        """Ornith's solves explore by reading (A1 rejected "new state only" for cutting them)."""
        def flip_and_read(k):
            return self._patch(k) + [("coding_read", {"path": "f%d" % k})]
        answer, tele, _ = self.run_loop(flip_and_read, finish_after=30, results=lambda f, a: (
            self._flip(f, a) if f == "coding_patch" else "text"))
        self.assertEqual(answer, self.FINAL)
        self.assertNotIn("ended", tele)

    def test_an_edit_with_no_hash_counts_as_a_new_state(self):
        p = ollama_client._LoopProgress()
        p.call("coding_fetch", {"url": "u"}, ok=True, result="saved")
        p.end_turn(True)
        p.call("coding_fetch", {"url": "u"}, ok=True, result="saved")
        p.end_turn(False)
        self.assertEqual(p.stall_turns, 0, "the generous reading: the rule may under-count, never over-count")

    def test_the_words_follow_the_state(self):
        nudge = ollama_client._idle_nudge
        self.assertNotIn("test_*.py", nudge(["coding_read"], 1, 3, 1))
        self.assertIn("test_*.py", nudge(["coding_read", "coding_test"], 1, 3, 1))
        self.assertIn("2 more such turns in a row and the loop stops you here to reset the count", nudge(["coding_read"], 1, 3, 1))
        self.assertIn("the loop ends here", nudge(["coding_read"], 2, 3, 0))
        self.assertIn("3 turns in a row repeated", nudge(["coding_read"], 3, 3, 1))
        self.assertIn("The count starts again now", nudge(["coding_read"], 3, 3, 1))

    def test_no_turn_cap_a_working_loop_runs_past_the_old_fifty(self):
        def edit_then_read(n):   # every successful patch makes the next read new
            return [("coding_patch", {"path": "a.py", "old": str(n)})] if n % 2 else [("coding_read", {"path": "a.py"})]
        # Each patch generates a unique hash (simulated below) to distinguish progress from a STALL; fixed hashes would trigger false stalls.
        answer, tele, _ = self.run_loop(edit_then_read, finish_after=120,
                                        results=lambda f, a: json.dumps({"path": "a.py", "new_sha256": "x" + str(a.get("old"))}))
        self.assertEqual(answer, self.FINAL)
        self.assertEqual(self.calls, 121)
        self.assertNotIn("ended", tele)

    def test_a_reread_after_a_successful_edit_is_new(self):
        p = ollama_client._LoopProgress()
        self.assertTrue(p.call("coding_read", {"path": "a"}, ok=True))
        self.assertFalse(p.call("coding_read", {"path": "a"}, ok=True))
        self.assertTrue(p.call("coding_patch", {"path": "a"}, ok=True))
        self.assertTrue(p.call("coding_read", {"path": "a"}, ok=True))

    def test_a_failed_edit_changes_nothing_so_retrying_it_is_idle(self):
        answer, tele, _ = self.run_loop(
            lambda n: [("coding_patch", {"path": "a.py", "old": "x"})],
            results=lambda f, a: json.dumps({"error": "old text is ambiguous (2 matches)"}))
        self.assertEqual(tele["ended"], "idle")

    def test_read_only_actions_of_a_mixed_tool_do_not_count_as_change(self):
        p = ollama_client._LoopProgress()
        p.call("coding_task", {"action": "list"}, ok=True)
        self.assertEqual(p.version, 0)
        p.call("coding_task", {"action": "start", "objective": "o"}, ok=True)
        self.assertEqual(p.version, 1)

    def test_a_passed_deadline_ends_before_any_model_call(self):
        answer, tele, _ = self.run_loop(lambda n: [("coding_read", {"path": "a"})],
                                        deadline=time.monotonic() - 1)
        self.assertEqual(self.calls, 0)
        self.assertEqual(tele["ended"], "deadline")

    def test_a_job_with_a_budget_sees_its_clock_and_one_without_does_not(self):
        with ollama_client.call_context(loop_budget=3600):
            _, _, seen = self.run_loop(lambda n: [("coding_read", {"path": "f%d" % n})], finish_after=2,
                                       deadline=time.monotonic() + 3000)
        self.assertEqual(seen.count("[Clock: 10 min used of your 60 min budget; 50 min left.]"), 2, seen)
        _, _, seen = self.run_loop(lambda n: [("coding_read", {"path": "f%d" % n})], finish_after=2,
                                   deadline=time.monotonic() + 3000)
        self.assertNotIn("[Clock:", seen, "a deadline with no budget of its own says nothing")

    def test_deadline_warns_once_then_ends(self):
        clock = _Clock()
        answer, tele, seen = self.run_loop(lambda n: [("coding_read", {"path": "f%d" % n})],
                                           deadline=clock.t + 60, clock=clock)
        self.assertEqual(tele["ended"], "deadline")
        self.assertEqual(seen.count("FINAL TURNS"), 1)
        self.assertGreater(self.calls, 3)

    def test_ordinary_tool_loop_keeps_its_count(self):
        answer, tele, _ = self.run_loop(lambda n: [("get_time", {})],
                                        tools_list=[{"function": {"name": "get_time"}}])
        self.assertEqual(self.calls, config.TOOL_LOOP_MAX)
        self.assertNotIn("ended", tele)

    def test_a_job_without_coding_tools_has_no_turn_cap(self):
        """A research JOB (no coding_* tools) runs past TOOL_LOOP_MAX while each turn is new work, and ends on idleness."""
        def search(n):
            return [("web_search", {"q": "q%d" % n})] if n <= 12 else [("web_search", {"q": "q12"})]
        with ollama_client.call_context(job=True):
            answer, tele, _ = self.run_loop(search, tools_list=[{"function": {"name": "web_search"}}])
        self.assertGreater(self.calls, config.TOOL_LOOP_MAX)
        self.assertEqual(tele["ended"], "idle")
        self.assertEqual(self.calls, 12 + config.CODING_IDLE_TURNS * (1 + ollama_client.IDLE_RESETS))

    def test_the_handoff_names_the_real_reason_the_loop_stopped(self):
        """Ensure the handoff message reflects the loop's actual stop reason, not a generic budget error."""
        self.run_loop(lambda n: [("coding_read", {"path": "a.py"})])
        self.assertIn("no new work", self.why)
        captured = []
        with patch.object(ollama_client, "_chat_once_raw",
                          lambda h, m, msgs, *a, **k: captured.append(msgs) or {"message": {"content": "ok"}}):
            ollama_client._explain([{"role": "user", "content": "x"}], "h", "m", 1000, why=self.why)
        prompt = captured[0][-1]["content"]
        self.assertIn("no new work", prompt)
        self.assertNotIn("budget", prompt)

    def test_a_job_without_coding_tools_ends_at_its_deadline(self):
        clock = _Clock()
        with ollama_client.call_context(job=True):
            answer, tele, _ = self.run_loop(lambda n: [("web_search", {"q": "q%d" % n})],
                                            deadline=clock.t + 60, clock=clock,
                                            tools_list=[{"function": {"name": "web_search"}}])
        self.assertEqual(tele["ended"], "deadline")
        self.assertGreater(self.calls, config.TOOL_LOOP_MAX)


class AToolCallWrittenAsText(unittest.TestCase):

    def loop(self, replies, job):
        seen = []

        def once(host, model, messages, *a, **k):
            seen.append([dict(m) for m in messages])
            return replies.pop(0)
        with patch.object(ollama_client, "_chat_once", once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", lambda fn, args, *a, **k: "ok"):
            with ollama_client.call_context(job=job):
                answer, _ = ollama_client.chat("h", "m", [{"role": "user", "content": "x"}], [{"function": {"name": "coding_read"}}])
        return answer, seen

    TEXT = {"message": {"role": "assistant", "content": "<tool_call>\n<function=coding_create>"}, "done_reason": "stop"}

    def test_in_a_job_it_is_sent_back_to_make_the_call(self):
        answer, seen = self.loop([dict(self.TEXT), {"message": {"role": "assistant", "content": "done"}}], job=True)
        self.assertEqual(answer, "done")
        self.assertIn("as plain text", seen[-1][-1]["content"])

    def test_a_chat_keeps_it_as_its_answer(self):
        answer, _ = self.loop([dict(self.TEXT)], job=False)
        self.assertIn("<tool_call>", answer)

    def test_the_handover_may_save_with_one_real_call_then_explains(self):
        calls, ran = [], []

        def raw(host, model, msgs, tools_list, *a, **k):
            calls.append(list(tools_list or []))
            if len(calls) == 1:
                return {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "coding_create", "arguments": '{"path": "a.html", "content": "<p>"}'}}]}}
            return {"message": {"role": "assistant", "content": "I saved the page; it is unfinished."}}
        with patch.object(ollama_client, "_chat_once_raw", raw):
            out = ollama_client._explain([{"role": "user", "content": "x"}], "h", "m", 1000, why="the job's time budget ran out",
                                         run=lambda fn, args: ran.append((fn, args)) or "saved",
                                         tools_list=[{"function": {"name": "coding_create"}}])
        self.assertEqual(ran, [("coding_create", {"path": "a.html", "content": "<p>"})])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], [], "the explanation itself is asked with no tools")
        self.assertIn("I saved the page", out)
        self.assertIn("coding_create", out, "the answer says what was saved")

    def test_a_handover_call_that_is_not_a_save_does_not_run(self):
        ran = []

        def raw(host, model, msgs, tools_list, *a, **k):
            if tools_list:
                return {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "web_search", "arguments": {"q": "x"}}}]}}
            return {"message": {"role": "assistant", "content": "explained"}}
        with patch.object(ollama_client, "_chat_once_raw", raw):
            out = ollama_client._explain([{"role": "user", "content": "x"}], "h", "m", 1000,
                                         run=lambda fn, args: ran.append(fn) or "x",
                                         tools_list=[{"function": {"name": "coding_create"}}])
        self.assertEqual((ran, out), ([], "explained"))


class LoopDeadlineScopeTests(unittest.TestCase):

    def test_tighten_outside_a_scope_is_refused(self):
        self.assertIsNone(ollama_client.tighten_loop_deadline(5.0))
        self.assertIsNone(getattr(ollama_client._CALL_CONTEXT, "loop_deadline", None))

    def test_nested_deadlines_only_tighten_and_are_restored(self):
        with ollama_client.call_context(loop_deadline=50.0):
            with ollama_client.call_context(loop_deadline=100.0):
                self.assertEqual(ollama_client._CALL_CONTEXT.loop_deadline, 50.0)
            self.assertEqual(ollama_client.tighten_loop_deadline(20.0), 20.0)
        self.assertIsNone(getattr(ollama_client._CALL_CONTEXT, "loop_deadline", None))
        self.assertEqual(getattr(ollama_client._CALL_CONTEXT, "scope_depth", 0), 0)

    def test_the_old_count_knobs_are_gone(self):
        for knob in ("CODING_TOOL_LOOP_MAX", "CODING_SEARCH_SOFT", "CODING_SEARCH_HARD",
                     "CODING_TURN_SOFT", "CODING_TURN_HARD"):
            self.assertFalse(hasattr(config, knob), knob)
        self.assertGreaterEqual(config.CODING_IDLE_TURNS, 1)


if __name__ == "__main__":
    unittest.main()
