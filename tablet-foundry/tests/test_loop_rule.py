"""Detects loops larger than the sliding window (e.g., 23k chars repeated 14x) that evade short-window ratio checks.
No server or external state is touched; `repeating` mirrors infer_ollama's logic."""
import io
import json
import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tolkien_llm as tv


def prose(n, seed):
    """`n` chars of text that does not repeat and does not compress like a loop."""
    rnd = random.Random(seed)
    out, size = [], 0
    while size < n:
        w = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randrange(2, 11)))
        out.append(w)
        size += len(w) + 1
    return " ".join(out)[:n]


class ALoopLongerThanTheWindows(unittest.TestCase):
    def test_one_long_block_written_over_and_over_is_a_loop(self):
        lead, block = prose(5000, 1), prose(23401, 2)
        self.assertEqual(tv.repeating(lead + block * 3), 23401)
        self.assertTrue(tv.looping(lead + block * 3))
        self.assertEqual(tv.repeating(lead + block * 2), 0, "a block written twice is a restatement")
        self.assertFalse(tv.looping(lead + block * 2))
        self.assertEqual(tv.repeating(lead + block * 3 + prose(600, 3)), 0, "it moved on: not a loop")

    def test_a_short_block_needs_as_much_text_as_the_ratio_rule_asks(self):
        lead, card = prose(12000, 4), prose(300, 5)
        self.assertEqual(tv.repeating(lead + card * 6), 0, "six identical cards in a page are content")
        self.assertEqual(tv.repeating(lead + card * 27), 300)
        self.assertEqual(tv.repeating(lead + prose(60, 6) * 400), 0, "under LOOP_MIN_PERIOD: the ratio's to judge")

    def test_rows_that_differ_only_in_their_numbers_are_not_the_long_rule_s(self):
        row = prose(400, 7)
        rows = "".join("%s %06d\n" % (row, i) for i in range(40))
        self.assertEqual(tv.repeating(prose(3000, 8) + rows), 0)

    def test_the_long_rule_leaves_the_real_genuine_answers_alone(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop_fixtures.json"), encoding="utf-8") as f:
            fx = json.load(f)
        self.assertEqual([tv.repeating(fx[k]["text"]) for k in ("legit_lowest", "correct_single_dip")], [0, 0])

    def test_a_long_loop_is_cut_in_a_chat_stream_because_the_whole_turn_is_kept(self):
        text = prose(5000, 9) + prose(23401, 10) * 5
        deltas = [{"reasoning_content": text[i:i + 50]} for i in range(0, len(text), 50)]
        body = b"".join(("data: " + json.dumps({"choices": [{"delta": d}]}) + "\n").encode() for d in deltas)
        with mock.patch.object(tv.urllib.request, "urlopen", return_value=io.BytesIO(body + b"data: [DONE]\n")):
            r = tv.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(r["done_reason"], "loop")
        self.assertLess(len(r["message"]["thinking"]), len(text) * 0.72, "ended on the third copy, not at the context")


class ATableOfNumbersIsNotALoop(unittest.TestCase):
    """Fixtures hold real production bytes to prevent the loop detector from misclassifying legitimate tabular data as a loop."""

    def fx(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop_fixtures.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_the_real_tables_pass_and_the_real_loops_do_not(self):
        fx = self.fx()
        self.assertFalse(tv.looping(fx["number_table_thinking"]["text"]))
        self.assertFalse(tv.looping(fx["number_table_in_code"]["text"]))
        self.assertTrue(tv.looping(fx["loop"]["text"]), "positive control")
        self.assertTrue(tv.looping(fx["counting_loop"]["text"]), "positive control")

    def test_numbers_that_never_end_are_still_a_loop(self):
        rnd = random.Random(11)
        rows = "".join("Row %d: %s\n" % (i, ",".join(str(rnd.randrange(12)) for _ in range(12))) for i in range(4000))
        self.assertFalse(tv.looping(rows[:tv.LOOP_WINDOW * 3]))
        self.assertTrue(tv.looping(rows[:tv.LOOP_WINDOW * tv.LOOP_DATA_WINDOWS]))


class RigbysChatWindowEndsALoop(unittest.TestCase):
    """rigby's live chat turn had no loop rule at all (the GPU boxes' windows read through infer_ollama's): a looping
    turn ran to the context. Now the turn ends and its row says done_reason "loop", the GPU boxes' word for it."""

    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp(prefix="loopwin_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_the_window_s_row_says_loop_and_carries_no_error(self):
        with open(os.path.join(self.d, "OWNER.json"), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": 10 ** 12}, f)
        with open(os.path.join(self.d, "t00001-a.item.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "t00001-a", "messages": [{"role": "user", "content": "hi"}]}, f)
        spec = {"kind": "chat.window", "name": "hai-win-rigby-q-1", "model": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
                "items_dir": self.d, "ctx": 8192, "idle_ttl": 300, "gpu": "gtx1060", "fit_target": 512, "build": "cuda"}
        def looped(base, messages, options, **kw):      # infer_ollama ends a looping stream and says "loop"
            for i in range(7):
                if kw.get("on_chunk"):
                    kw["on_chunk"]({"choices": [{"delta": {"content": "x"}}]})
            return {"message": {"content": ""}, "model": "m", "done_reason": "loop"}
        with open(os.path.join(self.d, "t00001-a.item.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "t00001-a", "messages": [{"role": "user", "content": "hi"}], "stream": True}, f)
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), mock.patch.object(tv, "stop_server"), \
                mock.patch.object(tv.HW.IO, "_chat_openai", side_effect=looped):
            _summary, err = tv.run_window(spec, lambda *a: None,
                                          sleep=lambda s: open(os.path.join(self.d, "CLOSE"), "w").close())
        self.assertIsNone(err)
        with open(os.path.join(self.d, "t00001-a.out.json"), encoding="utf-8") as f:
            row = json.load(f)
        self.assertEqual((row["done_reason"], row["error"], row["stream_chunks"]), ("loop", "", 7))


if __name__ == "__main__":
    unittest.main()
