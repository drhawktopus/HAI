"""#37: Context compaction is required to prevent long Background jobs from exceeding the model's token ceiling."""
import unittest
from unittest import mock

import ollama_client

CTX = 10000
TOOLS = [{"type": "function", "function": {"name": "search_docs", "parameters": {}}}]


def _tool_turn(prompt_tokens, tag):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "search_docs", "arguments": {"q": tag}}}]},
        "done_reason": "stop", "prompt_eval_count": prompt_tokens, "eval_count": 50}


def _answer(text):
    return {"message": {"role": "assistant", "content": text}, "done_reason": "stop",
            "prompt_eval_count": 100, "eval_count": 20}


class _Loop(unittest.TestCase):
    def run_loop(self, script, summary="SUMMARY OF THE WORK", summary_raises=False, start=None):
        seq, self.calls, self.summaries = list(script), [], []

        def fake_once(host, model, messages, tools_list, *a, **k):
            if messages and messages[0].get("role") == "system" and \
                    messages[0].get("content", "").startswith("You are compacting"):
                self.summaries.append(messages)
                if summary_raises:
                    raise ValueError("summary call failed")
                return {"message": {"role": "assistant", "content": summary}}
            self.calls.append([dict(m) for m in messages])
            return seq.pop(0)

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                mock.patch.object(ollama_client.tools, "run_tool",
                                  side_effect=lambda fn, args, *a, **k: "result for %s" % args.get("q")), \
                mock.patch.object(ollama_client.tool_selection, "select",
                                  side_effect=lambda tools, *_a, **_k: (tools, None)), \
                mock.patch.object(ollama_client.time, "sleep", lambda *_: None):
            answer, _ = ollama_client.chat("http://h", "m",
                                           start or [{"role": "system", "content": "SYS"},
                                                     {"role": "user", "content": "THE BRIEF"}],
                                           list(TOOLS), num_ctx=CTX)
        self.assertFalse(seq, "unused model replies: the loop ended earlier than the script")
        return answer


class CompactsAtNinetyPercent(_Loop):
    def test_a_full_window_is_summarised_and_the_brief_and_latest_step_survive(self):
        answer = self.run_loop([_tool_turn(1000, "early"), _tool_turn(1000, "middle"),
                                _tool_turn(int(0.95 * CTX), "late"), _answer("done")])
        self.assertEqual(answer, "done")
        self.assertEqual(len(self.summaries), 1, "exactly one compaction")
        after = self.calls[-1]
        text = [m.get("content") or "" for m in after]
        self.assertEqual(after[0]["content"], "SYS")
        self.assertEqual(after[1]["content"], "THE BRIEF")
        self.assertTrue(any("CONTEXT COMPACTED" in t and "SUMMARY OF THE WORK" in t for t in text))
        self.assertTrue(any("result for late" in t for t in text), "the latest step's tool result is kept verbatim")
        self.assertFalse(any("result for early" in t for t in text), "older steps live only in the summary")
        rendered = self.summaries[0][1]["content"]
        self.assertIn("result for early", rendered, "the summariser saw what it summarised")

    def test_below_the_line_nothing_changes(self):
        self.run_loop([_tool_turn(1000, "a"), _tool_turn(2000, "b"), _answer("done")])
        self.assertEqual(self.summaries, [])

    def test_a_failed_summary_keeps_working_uncompacted(self):
        answer = self.run_loop([_tool_turn(1000, "early"), _tool_turn(int(0.95 * CTX), "late"), _answer("done")],
                               summary_raises=True)
        self.assertEqual(answer, "done")
        self.assertEqual(len(self.summaries), 1, "the compaction must have been ATTEMPTED - fixture is wrong if not")
        text = [m.get("content") or "" for m in self.calls[-1]]
        self.assertTrue(any("result for early" in t for t in text), "a failed summary leaves the conversation whole")


class ATurnThatFillsItsWindowIsNotAnAnswer(_Loop):
    """A turn truncated by length is an incomplete output, not a finished answer; compaction must proceed to summarize the partial thought."""

    CUT = {"message": {"role": "assistant", "content": "", "thinking": "half a thought"}, "done_reason": "length",
           "prompt_eval_count": 6000, "eval_count": 4000}

    def test_its_thought_is_summarised_with_the_rest_and_the_turn_is_asked_again(self):
        with ollama_client.call_context(job=True):
            answer = self.run_loop([_tool_turn(1000, "early"), _tool_turn(1000, "late"), dict(self.CUT), _answer("done")])
        self.assertEqual(answer, "done")
        self.assertEqual(len(self.summaries), 1, "the window was compacted")
        rendered = self.summaries[0][1]["content"]
        self.assertIn("half a thought", rendered, "Alex: compact by summarizing, never throw the thought away")
        self.assertLess(rendered.index("result for early"), rendered.index("half a thought"), "the cut turn comes last")
        text = [m.get("content") or "" for m in self.calls[-1]]
        self.assertTrue(any("SUMMARY OF THE WORK" in t for t in text))
        self.assertFalse(any("half a thought" in str(m) for m in self.calls[-1]), "it comes back as the summary, not verbatim")
        self.assertTrue(any("result for late" in t for t in text), "the latest finished step is kept verbatim as always")

    def test_a_first_turn_with_only_the_brief_before_it_is_still_summarised(self):
        with ollama_client.call_context(job=True):
            answer = self.run_loop([dict(self.CUT, prompt_eval_count=6000), dict(_answer("done"))])
        self.assertEqual(answer, "done")
        self.assertIn("half a thought", self.summaries[0][1]["content"])
        self.assertEqual(self.calls[-1][1]["content"], "THE BRIEF")

    def script(self):
        return [dict(self.CUT) for _ in range(ollama_client.config.CODING_IDLE_TURNS + 1)]

    def test_a_job_that_keeps_filling_the_window_stops_with_a_handoff_and_never_reads_as_finished(self):
        whys = []
        with ollama_client.call_context(job=True, budget_label="8 hours"), \
                mock.patch.object(ollama_client, "_explain", side_effect=lambda *a, **k: whys.append(k.get("why")) or "handoff"):
            answer = self.run_loop(self.script())
        self.assertTrue(answer.startswith("handoff"), answer)
        self.assertIn("not a finished answer", answer)
        self.assertIn("filled its window", whys[0])

    def test_a_chat_that_keeps_filling_it_keeps_the_answer_it_got(self):
        self.assertEqual(self.run_loop(self.script()), "half a thought")

    def test_a_prompt_that_grew_after_compaction_is_not_a_failure(self):
        """An early turn has no history to summarise, so the compaction overhead can make the prompt larger than the original."""
        with ollama_client.call_context(job=True):
            answer = self.run_loop([dict(self.CUT), dict(self.CUT, prompt_eval_count=7000, eval_count=3000), _answer("done")])
        self.assertEqual(answer, "done")

    def test_a_tool_call_starts_the_count_again(self):
        n = ollama_client.config.CODING_IDLE_TURNS
        with ollama_client.call_context(job=True):
            answer = self.run_loop([dict(self.CUT) for _ in range(n)] + [_tool_turn(1000, "a")]
                                   + [dict(self.CUT) for _ in range(n)] + [_answer("done")])
        self.assertEqual(answer, "done")

    def test_the_turn_asked_again_is_told_to_act(self):
        self.run_loop([dict(self.CUT), _answer("done")])
        self.assertTrue(any("Act now" in str(m.get("content")) for m in self.calls[-1]))

    def test_a_length_stop_with_room_left_is_a_deliberate_cap_and_changes_nothing(self):
        small = dict(self.CUT, prompt_eval_count=100, eval_count=50)
        self.assertEqual(self.run_loop([small]), "half a thought")
        self.assertEqual(self.summaries, [])


class ARunawayTurnOnRigbyIsStoppedAndAskedAgain(_Loop):

    RAN = {"message": {"role": "assistant", "content": "", "thinking": "a long plan that never acted"},
           "done_reason": ollama_client.RUNAWAY, "prompt_eval_count": None, "eval_count": 20480}

    def test_its_thinking_is_summarised_and_the_turn_is_asked_again_told_to_act(self):
        with ollama_client.call_context(job=True):
            answer = self.run_loop([_tool_turn(1000, "early"), _tool_turn(1000, "late"), dict(self.RAN), _answer("done")])
        self.assertEqual(answer, "done")
        self.assertEqual(len(self.summaries), 1, "the stopped thought goes through the summary, as a full window's does")
        self.assertIn("a long plan that never acted", self.summaries[0][1]["content"], "never thrown away")
        again = self.calls[-1]
        self.assertFalse(any("a long plan that never acted" in str(m) for m in again), "it comes back as the summary")
        self.assertTrue(any("Act now" in str(m.get("content")) for m in again))
        self.assertTrue(any("result for late" in str(m.get("content")) for m in again), "the latest finished step stays verbatim")

    def test_a_job_that_keeps_running_away_stops_with_a_handoff_and_says_so(self):
        script = [dict(self.RAN) for _ in range(ollama_client.config.CODING_IDLE_TURNS + 1)]
        whys = []
        with ollama_client.call_context(job=True, budget_label="8 hours"), \
                mock.patch.object(ollama_client, "_explain", side_effect=lambda *a, **k: whys.append(k.get("why")) or "handoff"):
            answer = self.run_loop(script)
        self.assertIn("thought past", whys[0])
        self.assertIn("not a finished answer", answer)

    def test_a_tool_call_starts_the_count_again(self):
        n = ollama_client.config.CODING_IDLE_TURNS
        with ollama_client.call_context(job=True):
            answer = self.run_loop([dict(self.RAN) for _ in range(n)] + [_tool_turn(1000, "a")]
                                   + [dict(self.RAN) for _ in range(n)] + [_answer("done")])
        self.assertEqual(answer, "done")

    def test_the_guard_is_for_a_job_on_rigby_that_holds_tools_and_for_nothing_else(self):
        cap, rigby = ollama_client._runaway_cap_chars, ollama_client.config.TOLKIEN_WINDOW_URL
        with ollama_client.call_context(job=True):
            self.assertEqual(cap(rigby, TOOLS), int(20480 * ollama_client.COMPACT_CHARS_PER_TOKEN))
            self.assertEqual(cap(rigby + "/", TOOLS), cap(rigby, TOOLS))
            self.assertEqual(cap("http://10.0.0.76:11434", TOOLS), 0,
                             "the GPU boxes have none on purpose: their real turns are as long as their runaways")
            self.assertEqual(cap(rigby, []), 0, "a turn with no tools is an answer being thought out")
        self.assertEqual(cap(rigby, TOOLS), 0, "a chat is not a job")


class _FakeStream:
    """A box's streamed turn: `lines` as the engine sends them, and how many of them were read before it was closed."""
    status_code = 200

    def __init__(self, lines):
        self.lines, self.read_n, self.closed = lines, 0, False

    def iter_lines(self):
        for line in self.lines:
            if self.closed:
                return
            self.read_n += 1
            yield line


class TheStreamItselfIsStopped(unittest.TestCase):
    """The guard cuts the turn where it is read, so the box stops writing: leaving the stream is what ends a generation."""

    def turn(self, chunks, host=None):
        import contextlib
        import json as _json
        fake = _FakeStream([_json.dumps(c) for c in chunks])

        @contextlib.contextmanager
        def opened(url, body, clock_s, watch=None):
            try:
                yield fake
            finally:
                fake.closed = True

        class Watch:
            longest, verdict, clock_s = 0.0, None, 1e9          # never silent long enough to be noted

            def __init__(self, *a, **k):
                pass

            def start(self):
                return self

            def beat(self):
                pass

            def stop(self):
                pass

        with mock.patch.object(ollama_client, "_open_chat_stream", opened), \
                mock.patch.object(ollama_client, "_SilenceWatch", Watch), \
                mock.patch.object(ollama_client, "_free_swap", lambda *_a, **_k: None), \
                mock.patch.object(ollama_client.config, "swap_base", lambda *_a, **_k: None), \
                mock.patch.object(ollama_client.config, "TOLKIEN_RUNAWAY_THINK", 10), \
                ollama_client.call_context(job=True):
            out = ollama_client._chat_once_raw(host or ollama_client.config.TOLKIEN_WINDOW_URL, "m", [], list(TOOLS), 49152, "5m", think=True)
        return out, fake

    THINK = [{"message": {"thinking": "abcde "}} for _ in range(40)]           # 240 characters; the cap is 10 tokens = 31
    DONE = [{"message": {"content": ""}, "done": True, "done_reason": "stop", "prompt_eval_count": 5, "eval_count": 41}]

    def test_thinking_past_the_guard_with_no_tool_call_ends_the_turn_there(self):
        out, fake = self.turn(self.THINK + self.DONE)
        self.assertEqual(out["done_reason"], ollama_client.RUNAWAY)
        self.assertLess(fake.read_n, len(self.THINK), "it stopped reading, which is what stops the box writing")
        self.assertTrue(fake.closed)
        self.assertTrue(out["message"]["thinking"].startswith("abcde abcde"), "the thought so far is handed back to be summarised")
        self.assertNotIn("tool_calls", out["message"])

    def test_a_turn_that_has_called_a_tool_is_never_cut(self):
        call = [{"message": {"tool_calls": [{"function": {"name": "search_docs", "arguments": {"q": "x"}}}]}}]
        out, fake = self.turn(call + self.THINK + self.DONE)
        self.assertEqual(out["done_reason"], "stop")
        self.assertEqual(fake.read_n, len(call) + len(self.THINK) + len(self.DONE))

    def test_a_turn_under_the_guard_is_untouched(self):
        out, _fake = self.turn(self.THINK[:3] + self.DONE)
        self.assertEqual(out["done_reason"], "stop")

    def test_another_box_thinks_as_long_as_it_likes(self):
        out, fake = self.turn(self.THINK + self.DONE, host="http://another-box")
        self.assertEqual(out["done_reason"], "stop")
        self.assertEqual(fake.read_n, len(self.THINK) + len(self.DONE))


class EveryBoxSummarisesNearTheEndOfItsWindow(_Loop):
    """Summarize triggers near the end of the window (90%) to avoid premature context loss."""

    def test_the_line(self):
        line, rigby = ollama_client._compact_line, ollama_client.config.TOLKIEN_WINDOW_URL
        for window in (32768, 49152, 65536):
            self.assertEqual(line(rigby, window), 0.9 * window, "rigby has no line of its own")
        self.assertEqual(line(rigby + "/", 49152), 0.9 * 49152)
        self.assertEqual(line("http://10.0.0.76:11434", 122880), 0.9 * 122880)

    def test_a_rigby_job_keeps_the_window_the_measurements_chose(self):
        self.assertEqual(ollama_client.config.TOLKIEN_JOB_CTX, 49152)

    def test_the_summariser_is_told_to_keep_the_running_record(self):
        # box2-p's tenth summary knew only the bug it had just fixed. What the summariser is handed is what is pinned.
        told = ollama_client._COMPACT_SYSTEM
        for heading in ("EARLIER SUMMARY", "REQUIREMENTS", "TRIED", "FILES", "LAST CHECK", "NEXT"):
            self.assertIn(heading, told)
        self.assertIn("never knows less about the earlier work", told)
        self.assertIn("Do not invent anything", told, "the old rules stay")

    def test_the_loop_compacts_on_that_line(self):
        with mock.patch.object(ollama_client, "_compact_line", return_value=2500):
            answer = self.run_loop([_tool_turn(1000, "early"), _tool_turn(2600, "late"), _answer("done")])
        self.assertEqual((answer, len(self.summaries)), ("done", 1))


class EveryStopOfAJobHandsOff(_Loop):
    """Every user-driven stop must terminate via a handoff (run marked stopped), never a bare failure or malformed response."""

    def stop(self, script):
        whys = []
        with ollama_client.call_context(job=True, budget_label="8 hours"), \
                mock.patch.object(ollama_client, "_explain", side_effect=lambda *a, **k: whys.append(k.get("why")) or "handoff"):
            answer = self.run_loop(script)
        return answer, whys

    def test_tool_calls_written_as_text_stop_instead_of_becoming_the_answer(self):
        text_call = _answer("<tool_call><function=coding_test><parameter=profile>unit</parameter></function></tool_call>")
        answer, whys = self.stop([dict(text_call) for _ in range(ollama_client.config.CODING_IDLE_TURNS + 1)])
        self.assertNotIn("<tool_call>", answer, "the broken call is not the job's answer")
        self.assertIn("tool calls as text", whys[0])
        self.assertIn("not a finished answer", answer)

    def test_an_empty_answer_stops_with_a_handoff(self):
        answer, whys = self.stop([_answer(" ")])
        self.assertIn("empty answer", whys[0])
        self.assertTrue(answer.startswith("handoff"))

    def test_a_chat_still_raises_on_an_empty_answer(self):
        with self.assertRaises(ollama_client.ChatError):
            self.run_loop([_answer(" ")])

    def test_every_stop_has_its_words(self):
        for k, say in ollama_client._STOP_WHY.items():
            self.assertTrue(say().strip(), k)


class RereadingAfterCompactionIsNewWork(_Loop):
    """Rereading content dropped by compaction counts as new work; the idle detector must not flag it."""

    def test_a_job_rereading_after_compaction_keeps_working(self):
        with ollama_client.call_context(job=True):
            answer = self.run_loop([_tool_turn(1000, "a"), _tool_turn(int(0.95 * CTX), "b"),
                                    _tool_turn(1000, "a"), _tool_turn(1000, "b"), _tool_turn(1000, "a"),
                                    _answer("done")])
        self.assertEqual(len(self.summaries), 1, "fixture: the window must have been compacted")
        self.assertEqual(answer, "done", "re-reading what the summary dropped is new work, not idleness")


class KeepsTheCurrentRequestInAReusedThread(_Loop):
    """Ensure the active request survives compaction while prior history is summarized."""

    THREAD = [{"role": "system", "content": "SYS"},
              {"role": "user", "content": "OLD BRIEF"},
              {"role": "assistant", "content": "old answer"},
              {"role": "user", "content": "THE NIGHT BRIEF"}]

    def test_the_current_request_survives_and_the_old_history_is_summarised(self):
        answer = self.run_loop([_tool_turn(1000, "early"), _tool_turn(int(0.95 * CTX), "late"), _answer("done")],
                               start=[dict(m) for m in self.THREAD])
        self.assertEqual(answer, "done")
        self.assertEqual(len(self.summaries), 1)
        after = [m.get("content") or "" for m in self.calls[-1]]
        self.assertEqual(after[0], "SYS")
        self.assertEqual(after[1], "THE NIGHT BRIEF", "the request this run answers is kept verbatim, right after SYS")
        self.assertNotIn("OLD BRIEF", after, "the thread's earlier request is history, not the brief")
        rendered = self.summaries[0][1]["content"]
        self.assertIn("OLD BRIEF", rendered, "the old history is summarised, not lost")
        self.assertNotIn("THE NIGHT BRIEF", rendered, "the current brief is kept, so it is not summarised too")

    def test_an_earlier_compaction_note_is_never_taken_for_the_brief(self):
        msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "REAL BRIEF"},
                {"role": "user", "content": "[CONTEXT COMPACTED - a summary ...]\nwork"},
                {"role": "assistant", "content": "x"}]
        self.assertEqual(ollama_client._current_brief(msgs)["content"], "REAL BRIEF")


if __name__ == "__main__":
    unittest.main()
