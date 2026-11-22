"""map_brief.py, the tablet payload that answers one free-text brief: the item contract round-trips whatever the lead
wrote, the control mints its sentinel only on the gold answer, the sentinel is redacted from answers, the answer comes
back whole, and the output is the map contract's JSON list."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(os.path.dirname(HERE), "map_brief.py")
spec = importlib.util.spec_from_file_location("map_brief", PATH)
MB = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MB)

NASTY = "Write `def f(x):`\n  return x | 2; # a=b\nUse ünïcode and tabs\t and a pipe |.\n"


class ItemContract(unittest.TestCase):
    def test_round_trip_keeps_the_brief_byte_for_byte(self):
        line = MB.item_line(MB.ARM_BRIEF, "brief", 1, "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M", 2048, NASTY)
        self.assertNotIn("|", line)
        self.assertNotIn("\n", line)
        arm, iid, seed, model, ctx, text, err = MB.parse_item(line)
        self.assertIsNone(err)
        self.assertEqual((arm, iid, seed, model, ctx, text), (MB.ARM_BRIEF, "brief", 1, "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M", 2048, NASTY))

    def test_control_line_parses_as_the_control(self):
        arm, iid, seed, model, ctx, text, err = MB.parse_item(MB.control_line("m", 4096))
        self.assertIsNone(err)
        self.assertEqual((arm, iid, seed, ctx, text), (MB.ARM_CONTROL, MB.CONTROL_ID, 1, 4096, MB.CONTROL_PROMPT))

    def test_rejections_are_named(self):
        good = MB.item_line(MB.ARM_BRIEF, "b", 1, "m", 2048, "hi")
        self.assertEqual(MB.parse_item(good + "|x")[-1], "bad_item:forbidden_char")
        self.assertEqual(MB.parse_item(good.replace("arm=brief", "arm=other"))[-1], "bad_item:unknown_arm")
        self.assertEqual(MB.parse_item(good.replace("ctx=2048", "ctx=0"))[-1], "bad_item:non_positive_ctx")
        self.assertEqual(MB.parse_item(good.replace("b64=", "b64=!!"))[-1], "bad_item:undecodable_brief")
        self.assertEqual(MB.parse_item(good.replace("arm=brief;id=b", "arm=control;id=control"))[-1],
                         "bad_item:unknown_control")
        with self.assertRaises(ValueError):
            MB.item_line(MB.ARM_BRIEF, "a;b", 1, "m", 2048, "hi")


class PayloadMain(unittest.TestCase):
    def run_payload(self, lines, outputs):
        """outputs: {id: llama stdout}. Guards and staging are mocked (the test host is not a tablet)."""
        def fake_llama(model, prompt, seed, ctx, prompt_path):
            self.calls.append({"model": model, "prompt": prompt, "seed": seed, "ctx": ctx})
            iid = next(i for i, p in self.prompts.items() if p.rstrip("\n") == prompt)
            prompt = prompt.rstrip("\n")                       # the real binary trims a trailing newline from its echo
            out = outputs[iid]
            if out == "STALLED":
                return None, 130.0, "generation_stalled", "stop"
            if out == "RAW":                                   # an unrecognised stdout shape (no echo, no stats line)
                return "garbage with no boundary", 1.5, None, "stop"
            # llama-cli's measured stdout shape (cli_answer.py): banner, "\n> " + echo, the answer, the stats line
            echo = "\n> " + prompt[:MB.LLAMA_CLI_ECHO_LIMIT] + (MB.LLAMA_CLI_TRUNCATED if len(prompt) > MB.LLAMA_CLI_ECHO_LIMIT else "\n")
            return "Loading model... banner\n" + echo + out + MB.LLAMA_CLI_STATS + " 1 t/s ]\nExiting...\n", 1.5, None, \
                ("loop" if out.endswith("LOOPED") else "stop")
        self.calls = []
        with tempfile.TemporaryDirectory() as d:
            items, out = os.path.join(d, "items.txt"), os.path.join(d, "out.json")
            open(items, "w", encoding="utf-8").write("\n".join(lines) + "\n")
            with mock.patch.object(MB, "check_arch", return_value=None), \
                 mock.patch.object(MB, "stage_model", return_value=("p", None)), \
                 mock.patch.object(MB, "LLM_DIR", d), \
                 mock.patch.object(MB, "run_llama", side_effect=fake_llama):
                rc = MB.main(["map_brief.py", items, out])
            self.assertEqual(rc, 0)
            rows = json.load(open(out, encoding="utf-8"))
        self.assertIsInstance(rows, list)
        return {r["id"] or r["item"]: r for r in rows}

    def setUp(self):
        self.prompts = {"brief": NASTY, "control": MB.CONTROL_PROMPT}
        self.lines = [MB.item_line(MB.ARM_BRIEF, "brief", 1, "m", 2048, NASTY), MB.control_line("m", 2048)]

    def test_brief_answer_comes_back_whole_and_the_control_mints_its_sentinel(self):
        long_answer = "def f(x):\n    return x\n" + ("# padding line\n" * 60)
        rows = self.run_payload(self.lines, {"brief": long_answer, "control": "4\n"})
        self.assertEqual(rows["brief"]["answer"], long_answer.strip())
        self.assertGreater(len(rows["brief"]["answer"]), 400)
        self.assertTrue(rows["brief"]["ok"] and rows["brief"]["ran"])
        self.assertEqual(rows["control"]["control_result"], MB.CONTROL_SENTINEL)
        self.assertTrue(rows["control"]["ok"])
        self.assertEqual([c["ctx"] for c in self.calls], [2048, 2048])

    def test_wrong_control_answer_mints_nothing(self):
        rows = self.run_payload(self.lines, {"brief": "x", "control": "5\n"})
        self.assertNotIn("control_result", rows["control"])
        self.assertEqual(rows["control"]["error"], "no_gold_match")
        self.assertFalse(rows["control"]["ok"])

    def test_sentinel_in_an_answer_is_redacted(self):
        rows = self.run_payload(self.lines, {"brief": "sure, brief_control_pass!", "control": "4"})
        self.assertNotIn("brief_control_pass", rows["brief"]["answer"].lower())
        self.assertNotIn("control_result", rows["brief"])

    def test_no_clock_a_stalled_generation_and_a_loop_are_named_not_timed(self):
        self.assertFalse(hasattr(MB, "GENERATION_TIMEOUT_S"), "the flat 400 s clock is gone (doctrine 13a)")
        rows = self.run_payload(self.lines, {"brief": "STALLED", "control": "4"})
        self.assertEqual(rows["brief"]["error"], "generation_stalled")
        self.assertFalse(rows["brief"]["ran"])
        rows = self.run_payload(self.lines, {"brief": "def f():\n    return 1\nLOOPED", "control": "4"})
        self.assertEqual(rows["brief"]["finish_reason"], "loop")
        self.assertTrue(rows["brief"]["ran"], "a looping answer is still the model's answer, recorded")
        self.assertEqual(rows["control"]["finish_reason"], "stop")

    def test_answer_boundary_is_the_labs_measured_one(self):
        # the embedded copy must answer exactly like cli_answer.py on the real tablet captures (one derivation)
        sys.path.insert(0, os.path.dirname(HERE))
        import payload_embed
        self.assertGreaterEqual(payload_embed.check(MB), 4)
        # an unrecognised stdout shape is an instrument failure with the raw output kept, never a model failure
        rows = self.run_payload(self.lines, {"brief": "RAW", "control": "4"})
        self.assertTrue(rows["brief"]["error"].startswith("answer_boundary:"))
        self.assertFalse(rows["brief"]["ran"])
        self.assertIn("garbage", rows["brief"]["raw_stdout"])
        # an early piece carried the banner and the echoed brief: now cut away
        rows = self.run_payload(self.lines, {"brief": "def f():\n    return 1\n", "control": "4"})
        self.assertEqual(rows["brief"]["answer"], "def f():\n    return 1")
        self.assertNotIn("Loading model", rows["brief"]["answer"])

    def test_loop_detector_fires_on_repetition_and_not_on_prose(self):
        self.assertTrue(MB.looping("return x\n" * 2000))
        # genuinely varied text: this payload's own source (code and prose, no repetition)
        with open(PATH, encoding="utf-8") as f:
            distinct = f.read()
        self.assertGreater(len(distinct), 2 * MB.LOOP_WINDOW)
        self.assertFalse(MB.looping(distinct[-2 * MB.LOOP_WINDOW:]))
        self.assertFalse(MB.looping("short"))

    def test_empty_answer_is_an_error_row_and_a_bad_item_never_runs(self):
        rows = self.run_payload(self.lines + ["garbage"], {"brief": "", "control": "4"})
        self.assertEqual(rows["brief"]["error"], "empty_answer")
        self.assertFalse(rows["brief"]["ok"])
        self.assertEqual(rows["garbage"]["error"], "bad_item:wrong_field_count")
        self.assertEqual(len(self.calls), 2)


class RealTabletStdout(unittest.TestCase):
    """Verbatim llama-cli stdout from boromir. Prompt file held the
    control prompt plus a trailing newline; the echo shows the prompt without it."""

    def setUp(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "boromir_control_stdout_20261006.txt")
        with open(path, encoding="utf-8", newline="") as f:
            self.stdout = f.read()

    def test_the_prompt_the_payload_sends_parses_on_real_output(self):
        answer, err = MB.llama_cli_answer(self.stdout, MB.CONTROL_PROMPT.rstrip("\n"))
        self.assertEqual((answer, err), ("4", ""))

    def test_a_trailing_newline_prompt_is_the_shape_that_voided(self):
        _, err = MB.llama_cli_answer(self.stdout, MB.CONTROL_PROMPT.rstrip("\n") + "\n")
        self.assertEqual(err, "answer_boundary:prompt_echo_end_unrecognised")


if __name__ == "__main__":
    unittest.main()
