"""#37 work loop: on a JOB that changed code, the tests decide when it is done — the model's final answer is refused
until coding_test has passed on the code as it now stands (the same rule A1 measures, a1_harness FINISH_GATE).
A model that keeps answering without a passing run is doing no new work: after CODING_IDLE_TURNS refusals its answer
ships, labelled unverified. Plain (non-job) chat and jobs that changed nothing are never gated."""
import json
import unittest
from unittest import mock

import config
import ollama_client

CODING_TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}}
                for n in ("coding_patch", "coding_test", "coding_read")]


def _reply(content="", calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in calls]
    return {"message": msg, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 20}


def _test_result(rc):
    return json.dumps({"profile": "unit", "returncode": rc, "stdout": "Ran 3 tests", "stderr": ""})


class _Base(unittest.TestCase):
    def run_loop(self, script, tool_results, job=True):
        """script: model replies in order; tool_results: {tool name: [results in order]}."""
        seq, seen = list(script), []
        results = {k: list(v) for k, v in tool_results.items()}

        def fake_once(host, model, messages, *a, **k):
            seen.append([dict(m) for m in messages])
            return seq.pop(0)

        def fake_tool(name, args, *a, **k):
            return results[name].pop(0)

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                mock.patch.object(ollama_client.tools, "run_tool", side_effect=fake_tool), \
                mock.patch.object(ollama_client.tool_selection, "select",
                                  side_effect=lambda tools, *_a, **_k: (tools, None)), \
                mock.patch.object(ollama_client.time, "sleep", lambda *_: None), \
                ollama_client.call_context(job=job):
            answer, _esc = ollama_client.chat("http://h", "m", [{"role": "user", "content": "fix the bug"}],
                                              list(CODING_TOOLS), num_ctx=4096)
        self.assertFalse(seq, "the fake model had unused replies - the loop ended earlier than the script")
        return answer, seen


class FinishGate(_Base):
    def test_an_answer_after_an_edit_with_no_test_is_refused_until_the_tests_pass(self):
        answer, seen = self.run_loop(
            [_reply(calls=[("coding_patch", {"path": "a.py"}), ("coding_patch", {"path": "tests/test_a.py"})]),
             _reply("done, fixed it"),                                   # refused: changed, never tested
             _reply(calls=[("coding_test", {"profile": "unit"})]),
             _reply("done, tests pass")],
            {"coding_patch": ["patched", "test written"], "coding_test": [_test_result(0)]})
        self.assertEqual(answer, "done, tests pass")
        refusals = [m for m in seen[2] if m.get("role") == "user" and "FINISH REFUSED" in (m.get("content") or "")]
        self.assertEqual(len(refusals), 1, "the first answer must have been refused once")

    def test_a_failing_test_run_does_not_open_the_gate(self):
        answer, seen = self.run_loop(
            [_reply(calls=[("coding_patch", {})]),
             _reply(calls=[("coding_test", {})]),
             _reply("done"),                                             # refused: returncode 1
             _reply(calls=[("coding_patch", {})]),
             _reply(calls=[("coding_test", {})]),
             _reply("done now")],
            {"coding_patch": ["p1", "p2"], "coding_test": [_test_result(1), _test_result(0)]})
        self.assertEqual(answer, "done now")

    def test_an_edit_after_a_green_run_closes_the_gate_again(self):
        answer, _ = self.run_loop(
            [_reply(calls=[("coding_patch", {}), ("coding_test", {}), ("coding_patch", {})]),
             _reply("done"),                                             # refused: the last edit came after the pass
             _reply(calls=[("coding_test", {})]),
             _reply("done, re-tested")],
            {"coding_patch": ["p1", "p2"], "coding_test": [_test_result(0), _test_result(0)]})
        self.assertEqual(answer, "done, re-tested")

    def test_repeated_refusals_ship_the_answer_labelled_unverified(self):
        n = config.CODING_IDLE_TURNS
        answer, _ = self.run_loop(
            [_reply(calls=[("coding_patch", {})])] + [_reply("done %d" % i) for i in range(n + 1)],
            {"coding_patch": ["p"]})
        self.assertTrue(answer.startswith("done %d" % n), answer)
        self.assertIn("Not verified", answer)
        self.assertIn("refused %d times" % n, answer)

    def test_unverified_work_is_kept_on_the_runs_own_tasks_and_the_label_says_where(self):
        """Unverified work must be retained and labeled, not deleted. The gate passes only the tasks touched by this run."""
        n = config.CODING_IDLE_TURNS
        handed = []

        def hook(task_ids, why):
            handed.append((task_ids, why))
            return "Kept in checkpoint `c1`."

        with mock.patch.object(ollama_client, "UNVERIFIED_HOOK", hook):
            answer, _ = self.run_loop(
                [_reply(calls=[("coding_task", {"action": "start"}), ("coding_patch", {"task_id": "T2"})])]
                + [_reply("done %d" % i) for i in range(n + 1)],
                {"coding_task": [json.dumps({"id": "T1", "status": "active"})], "coding_patch": ["p"]})
        self.assertEqual(handed, [(["T1", "T2"], "the tests never passed on it")])
        self.assertIn("Not verified", answer)
        self.assertIn("Kept in checkpoint `c1`.", answer)

    def test_a_failing_keep_never_costs_the_answer(self):
        n = config.CODING_IDLE_TURNS

        def hook(task_ids, why):
            raise RuntimeError("runner down")

        with mock.patch.object(ollama_client, "UNVERIFIED_HOOK", hook):
            answer, _ = self.run_loop(
                [_reply(calls=[("coding_patch", {"task_id": "T1"})])] + [_reply("done %d" % i) for i in range(n + 1)],
                {"coding_patch": ["p"]})
        self.assertIn("Not verified", answer)

    def test_plain_chat_is_never_gated(self):
        answer, _ = self.run_loop(
            [_reply(calls=[("coding_patch", {})]), _reply("done")],
            {"coding_patch": ["p"]}, job=False)
        self.assertEqual(answer, "done")

    def test_a_job_that_changed_nothing_is_not_gated(self):
        answer, _ = self.run_loop(
            [_reply(calls=[("coding_read", {})]), _reply("here is what the file does")],
            {"coding_read": ["contents"]})
        self.assertEqual(answer, "here is what the file does")


class OwnTestsRule(_Base):
    """A1's rule (a1_harness FINISH_GATE: the lead's OWN tests) = the overnight verifier's hollow-pass check: a code
    change that adds or changes no test is not proven by a passing run of the old tests."""

    def test_a_code_change_with_no_test_change_is_refused_even_when_the_suite_passes(self):
        answer, seen = self.run_loop(
            [_reply(calls=[("coding_patch", {"path": "app.py"}), ("coding_test", {})]),
             _reply("done"),                                             # refused: green, but no test touched
             _reply(calls=[("coding_create", {"path": "tests/test_new.py"}), ("coding_test", {})]),
             _reply("done, with a test")],
            {"coding_patch": ["p"], "coding_create": ["created"], "coding_test": [_test_result(0), _test_result(0)]})
        self.assertEqual(answer, "done, with a test")
        hollow = [m for m in seen[2] if m.get("role") == "user" and "added or changed no test" in (m.get("content") or "")]
        self.assertEqual(len(hollow), 1)

    def test_a_non_code_change_needs_no_new_test(self):
        answer, _ = self.run_loop(
            [_reply(calls=[("coding_patch", {"path": "README.md"}), ("coding_test", {})]), _reply("done")],
            {"coding_patch": ["p"], "coding_test": [_test_result(0)]})
        self.assertEqual(answer, "done")

    def test_test_paths_are_recognised_both_ways(self):
        for p in ("tests/test_a.py", "orchestrator/tests/helper.py", "test_x.py", "pkg/test_y.py"):
            self.assertTrue(ollama_client._is_test_path(p), p)
        for p in ("app.py", "contest.py", "tests_data.json", "latest.py"):
            self.assertFalse(ollama_client._is_test_path(p), p)


class TestPassedReadsTheRunnerReply(unittest.TestCase):
    def test_returncode_zero_is_a_pass_and_anything_else_is_not(self):
        self.assertTrue(ollama_client._test_passed(_test_result(0)))
        self.assertFalse(ollama_client._test_passed(_test_result(1)))
        self.assertFalse(ollama_client._test_passed(json.dumps({"error": "no such profile"})))
        self.assertFalse(ollama_client._test_passed("not json and no returncode"))

    def test_a_returncode_inside_the_test_output_is_not_read_as_the_runs(self):
        reply = json.dumps({"returncode": 1, "stdout": 'printed {"returncode": 0} from a test'})
        self.assertFalse(ollama_client._test_passed(reply))


if __name__ == "__main__":
    unittest.main()
