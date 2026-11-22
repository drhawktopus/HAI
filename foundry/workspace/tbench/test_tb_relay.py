"""tb_relay: OpenAI chat requests become model-window items; the answer file becomes the response (no clock)."""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request

import tb_relay as R


def fake_window(items_dir, answer, stop):
    """Answers every item like helper_window does (one row beside it)."""
    import time
    while not stop.is_set():
        for f in os.listdir(items_dir):
            if f.endswith(R.ITEM):
                iid = f[:-len(R.ITEM)]
                out = os.path.join(items_dir, iid + R.OUT)
                if not os.path.exists(out):
                    item = json.load(open(os.path.join(items_dir, f), encoding="utf-8"))
                    row = dict(answer(item), id=iid)
                    json.dump(row, open(out, "w", encoding="utf-8"))
        time.sleep(0.05)


class Relay(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.stop = threading.Event()
        self.seen = []

    def tearDown(self):
        self.stop.set()
        shutil.rmtree(self.d, ignore_errors=True)

    def serve(self, answer):
        threading.Thread(target=fake_window, args=(self.d, answer, self.stop), daemon=True).start()
        relay = R.Relay(self.d, os.path.join(self.d, "log.jsonl"), sleep=lambda s: __import__("time").sleep(0.02))
        srv = R.ThreadedServer(("127.0.0.1", 0), R.make_handler(relay))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d/v1" % srv.server_address[1]

    def post(self, base, body):
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_a_chat_request_round_trips_through_the_window(self):
        def answer(item):
            self.seen.append(item)
            return {"answer": "ls -la", "done_reason": "stop", "prompt_eval_count": 50, "eval_count": 7,
                    "wall_s": 1.5, "served_model": "qwen3.8:27b", "error": ""}
        base = self.serve(answer)
        st, resp = self.post(base, {"model": "openai/lab", "messages": [
            {"role": "system", "content": "you drive a terminal"},
            {"role": "user", "content": [{"type": "text", "text": "list files"}]}]})
        self.assertEqual(st, 200)
        self.assertEqual(resp["choices"][0]["message"]["content"], "ls -la")
        self.assertEqual(resp["choices"][0]["finish_reason"], "stop")
        self.assertEqual(resp["usage"], {"prompt_tokens": 50, "completion_tokens": 7, "total_tokens": 57})
        self.assertEqual(self.seen[0]["messages"][1], {"role": "user", "content": "list files"}, "parts become text")
        log = [json.loads(l) for l in open(os.path.join(self.d, "log.jsonl"), encoding="utf-8")]
        self.assertEqual((log[0]["done_reason"], log[0]["window_wall_s"]), ("stop", 1.5))

    def test_a_cut_answer_reports_length_and_tool_calls_come_back_openai_shaped(self):
        rows = iter([{"answer": "partial", "done_reason": "loop", "error": ""},
                     {"answer": "", "done_reason": "stop", "error": "",
                      "tool_calls": [{"function": {"name": "run", "arguments": {"cmd": "ls"}}}]}])
        base = self.serve(lambda item: next(rows))
        _st, cut = self.post(base, {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(cut["choices"][0]["finish_reason"], "length", "a loop cut is counted as a cut, never a stop")
        _st, tc = self.post(base, {"messages": [{"role": "user", "content": "y"}], "tools": [{"type": "function"}]})
        call = tc["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(json.loads(call["function"]["arguments"]), {"cmd": "ls"})
        self.assertEqual(tc["choices"][0]["finish_reason"], "tool_calls")

    def test_assistant_tool_call_arguments_go_to_the_window_as_objects(self):
        msgs = R.to_window_messages([{"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "run", "arguments": "{\"cmd\": \"ls\"}"}}]}])
        self.assertEqual(msgs[0]["tool_calls"][0]["function"]["arguments"], {"cmd": "ls"})
        self.assertEqual(msgs[0]["content"], "")

    def test_three_loop_cuts_end_the_trial_as_task_complete_twice_then_a_new_trial_starts_fresh(self):
        reasons = iter(["loop", "loop", "loop", "stop"])
        base = self.serve(lambda item: {"answer": "again", "done_reason": next(reasons), "error": ""})
        long_conv = [{"role": "system", "content": "s"}, {"role": "user", "content": R.TASK_PROMPT_HEAD + " in a Linux environment. Task Description: x"},
                     {"role": "assistant", "content": "a"}, {"role": "user", "content": "out"}]
        got = [self.post(base, {"messages": long_conv})[1] for _ in range(4)]
        self.assertEqual(got[0]["choices"][0]["message"]["content"], "again")
        done = json.loads(got[2]["choices"][0]["message"]["content"])
        self.assertTrue(done["task_complete"], "third loop in a row ends the trial")
        self.assertEqual(done["commands"], [])
        self.assertTrue(json.loads(got[3]["choices"][0]["message"]["content"])["task_complete"],
                        "the confirmation turn is answered too, without asking the model")
        self.assertEqual(len([f for f in os.listdir(self.d) if f.endswith(R.ITEM)]), 3, "no item for the confirmation")
        fresh = self.post(base, {"messages": long_conv[:2]})[1]
        self.assertEqual(fresh["choices"][0]["message"]["content"], "again", "a new trial starts over")
        log = [json.loads(l) for l in open(os.path.join(self.d, "log.jsonl"), encoding="utf-8")]
        self.assertEqual(sum(1 for r in log if r.get("error") == "ended_no_progress"), 2)

    def test_eight_turns_with_no_new_terminal_output_end_the_trial_and_new_output_resets_it(self):
        base = self.serve(lambda item: {"answer": "persist in waiting", "done_reason": "stop", "error": ""})
        head = [{"role": "system", "content": "s"}, {"role": "user", "content": R.TASK_PROMPT_HEAD + " in a Linux environment. Task Description: x"}]

        def turn(n, out):
            conv = list(head)
            for i in range(n):
                conv += [{"role": "assistant", "content": "a%d" % i}, {"role": "user", "content": out}]
            return self.post(base, {"messages": conv})[1]["choices"][0]["message"]["content"]
        self.assertEqual(turn(1, "screen A"), "persist in waiting")              # first sight of "screen A"
        replies = [turn(i + 2, "screen A") for i in range(R.STALL_END)]         # the same screen again and again
        self.assertTrue(all(r == "persist in waiting" for r in replies[:-1]))
        self.assertTrue(json.loads(replies[-1])["task_complete"], "the 8th repeat ends the trial")
        log = [json.loads(l) for l in open(os.path.join(self.d, "log.jsonl"), encoding="utf-8")]
        self.assertEqual([r.get("why") for r in log if r.get("error") == "ended_no_progress"], ["stall"])
        self.assertEqual(turn(0, "")[:7], "persist", "a fresh trial starts over")
        self.assertEqual(turn(1, "screen B"), "persist in waiting", "new output is progress")

    def test_the_same_keystrokes_three_turns_in_a_row_end_the_trial_though_the_screen_keeps_changing(self):
        # the 10-10 arm, verbatim keystrokes: typed into an open nano, so every screen differs and the stall rule is blind
        stuck = json.dumps({"analysis": "The Python script contains syntax errors", "plan": "fix it", "commands": [
            {"keystrokes": "nano /app/program.py\n", "duration": 1.5},
            {"keystrokes": "python3 /app/program.py\n", "duration": 0.5},
            {"keystrokes": "diff -u /app/data/OUTPUT.DAT /app/data/EXPECTED.DAT\n", "duration": 0.5}],
            "task_complete": False}, indent=2)
        wait = json.dumps({"analysis": "waiting", "plan": "", "commands": [{"keystrokes": "", "duration": 60}]})
        base = self.serve(lambda item: {"answer": stuck, "done_reason": "stop", "error": ""})
        head = [{"role": "system", "content": "s"}, {"role": "user", "content": R.TASK_PROMPT_HEAD + " in a Linux environment. Task Description: x"}]

        def turn(said):
            conv = list(head)
            for i, a in enumerate(said):
                conv += [{"role": "assistant", "content": a}, {"role": "user", "content": "nano buffer %d" % i}]
            return self.post(base, {"messages": conv})[1]["choices"][0]["message"]["content"]
        self.assertEqual(turn([stuck]), stuck)
        self.assertEqual(turn([stuck] * 2), stuck, "two in a row is not yet a repeat")
        self.assertTrue(json.loads(turn([stuck] * 3))["task_complete"], "the third identical action ends the trial")
        log = [json.loads(l) for l in open(os.path.join(self.d, "log.jsonl"), encoding="utf-8")]
        self.assertEqual([r.get("why") for r in log if r.get("error") == "ended_no_progress"], ["repeat"])
        self.assertEqual(turn([]), stuck, "a fresh trial starts over")
        self.assertEqual(turn([wait] * 3), stuck, "turns that only wait are the stall rule's, not a repeat")
        self.assertIsNone(R.keystrokes_of(wait))
        self.assertIsNone(R.keystrokes_of("not json"))
        self.assertEqual(R.keystrokes_of("text before " + stuck)[0], "nano /app/program.py\n")

    def test_rejected_turns_count_as_stall_turns_though_each_error_screen_differs(self):
        base = self.serve(lambda item: {"answer": "prose, no json", "done_reason": "stop", "error": ""})
        head = [{"role": "system", "content": "s"}, {"role": "user", "content": R.TASK_PROMPT_HEAD + " in a Linux environment. Task Description: x"}]

        def turn(n, obs_of):
            conv = list(head)
            for i in range(n):
                conv += [{"role": "assistant", "content": "a%d" % i}, {"role": "user", "content": obs_of(i)}]
            return self.post(base, {"messages": conv})[1]["choices"][0]["message"]["content"]
        # Terminus-2's own feedback text (terminus_2.py), varied per turn as the live screens were
        err = lambda i: ("Previous response had parsing errors:\nERROR: No valid JSON found in response\nWARNINGS: - "
                         "No valid JSON object found (%d)\n\nPlease fix these issues and provide a proper JSON "
                         "response." % i)
        replies = [turn(i + 1, err) for i in range(R.STALL_END)]
        self.assertTrue(all(r == "prose, no json" for r in replies[:-1]))
        self.assertTrue(json.loads(replies[-1])["task_complete"], "the 8th rejected turn in a row ends the trial")
        self.assertEqual(turn(0, err), "prose, no json", "a fresh trial starts over")
        warned = lambda i: "WARNINGS: - Extra text detected before JSON object\n\nNew Terminal Output:\nout %d" % i
        self.assertEqual([turn(i + 1, warned) for i in range(R.STALL_END)][-1], "prose, no json",
                         "warnings ran the commands: new output each turn is progress, not a stall")

    def test_a_summarization_handoff_keeps_the_trials_no_progress_state_and_its_end(self):
        base = self.serve(lambda item: {"answer": "persist in waiting", "done_reason": "stop", "error": ""})
        task = [{"role": "user", "content": R.TASK_PROMPT_HEAD + " in a Linux environment. Task Description: x"}]
        # Terminus-2's handoff (terminus_2.py question_prompt, as it arrived 10-11): a FRESH conversation, same trial
        handoff = [{"role": "user", "content": "You are picking up work from a previous AI agent on this task:\n\n"
                                               "**Original Task:** x\n\n**Summary from Previous Agent:**\n..."}]

        def turn(head, n, out="screen A"):
            conv = list(head)
            for i in range(n):
                conv += [{"role": "assistant", "content": "a%d" % i}, {"role": "user", "content": out}]
            return self.post(base, {"messages": conv})[1]["choices"][0]["message"]["content"]
        turn(task, 1)                                             # first sight of screen A
        for i in range(R.STALL_END // 2):
            turn(task, i + 2)                                     # 4 stall turns
        self.assertEqual(turn(handoff, 0), "persist in waiting", "the handoff is answered, not ended")
        replies = [turn(handoff, i + 1) for i in range(R.STALL_END // 2)]
        self.assertTrue(json.loads(replies[-1])["task_complete"], "the count carried across the handoff to 8")
        self.assertTrue(json.loads(turn(handoff, 0))["task_complete"], "a later handoff cannot undo the end")
        self.assertEqual(turn(task, 0), "persist in waiting", "only a new TASK prompt starts a new trial")

    def test_a_full_context_answers_like_a_provider_so_terminus_summarizes(self):
        base = self.serve(lambda item: {"answer": "cut mid", "done_reason": "length", "prompt_eval_count": 14200,
                                        "eval_count": 136, "error": ""})
        st, resp = self.post(base, {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(st, 400)
        msg = resp["error"]["message"].lower()
        # the phrases Harbor's lite_llm._is_context_length_error matches (installed source, 10-11)
        self.assertTrue("context_length_exceeded" in msg and "maximum context length" in msg, msg)
        log = [json.loads(l) for l in open(os.path.join(self.d, "log.jsonl"), encoding="utf-8")]
        self.assertEqual(log[0]["error"], "context_full")

    def test_an_answer_filed_under_thinking_reaches_the_agent_and_is_counted(self):
        # nemotron-3-nano think on, 10-11: the whole JSON answer under thinking, content empty
        filed = '{\n  "analysis": "compile it",\n  "commands": [{"keystrokes": "cobc -x prog.cbl\\n"}]\n}'
        base = self.serve(lambda item: {"answer": "", "thinking": filed, "done_reason": "stop", "error": ""})
        _st, resp = self.post(base, {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(resp["choices"][0]["message"]["content"], filed)
        log = [json.loads(l) for l in open(os.path.join(self.d, "log.jsonl"), encoding="utf-8")]
        self.assertTrue(log[0]["answer_from_thinking"])
        both = R.to_openai_response({"answer": "the answer", "thinking": "scratch", "done_reason": "stop"}, "m", "r1")
        self.assertEqual(both["choices"][0]["message"]["content"], "the answer", "a real answer is never replaced")

    def test_a_closed_window_answers_an_error_instead_of_waiting_forever(self):
        relay = R.Relay(self.d, None, sleep=lambda s: None)
        open(os.path.join(self.d, R.CLOSE), "w").close()
        st, resp = relay.ask({"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(st, 503)
        self.assertEqual(resp["error"]["type"], "window_closed")

    def test_a_window_error_is_an_error_not_an_empty_answer(self):
        base = self.serve(lambda item: {"answer": "", "error": "HTTP 500: boom"})
        st, resp = self.post(base, {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(st, 502)
        self.assertIn("boom", resp["error"]["message"])


if __name__ == "__main__":
    unittest.main()
