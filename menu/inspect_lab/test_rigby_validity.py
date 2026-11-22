"""rigby's A1 path (Inspect + lab provider): a malformed tool call goes BACK TO THE MODEL as a parse error instead of
crashing the exercise (10-08: `ValidationError ... ToolCall` voided sgf/bowling/rest-api), and the row carries the
same void verdict as the GPU path (a1_validity, one derivation)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["A1_DIR"] = os.path.join(os.path.dirname(HERE), "a1")
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ["A1_DIR"])

import a1_payload  # noqa: E402
import a1_task  # noqa: E402
import a1_validity  # noqa: E402
import lab_provider  # noqa: E402


def reply(arguments):
    return {"message": {"content": "", "thinking": "", "tool_calls": [
        {"function": {"name": "coding_patch", "arguments": arguments}}]},
        "prompt_eval_count": 10, "eval_count": 5, "done_reason": "stop"}


class MalformedToolCall(unittest.TestCase):
    def test_unparseable_arguments_become_a_parse_error_not_an_exception(self):
        # tolkien_llm passes text it could not json.loads straight through - this exact shape crashed ToolCall
        out = lab_provider.to_model_output("m", reply('{"path": "x.py", "old": "a", "new": '))
        self.assertFalse(out.message.tool_calls)
        self.assertIn("coding_patch", out.metadata["invalid_tool_call"])
        mapped = a1_task.from_output(out)
        self.assertEqual(mapped["tool_calls"], [])
        self.assertIn("coding_patch", mapped["invalid_tool_call"], "the harness tells the model, as on the GPU path")

    def test_a_json_value_that_is_not_an_object_is_malformed_too(self):
        out = lab_provider.to_model_output("m", reply(["x.py"]))
        self.assertTrue(out.metadata["invalid_tool_call"])

    def test_good_arguments_are_untouched(self):
        out = lab_provider.to_model_output("m", reply({"path": "x.py"}))
        self.assertEqual(out.message.tool_calls[0].arguments, {"path": "x.py"})
        self.assertIsNone(out.metadata["invalid_tool_call"])
        self.assertNotIn("invalid_tool_call", a1_task.from_output(out))


class OneVerdict(unittest.TestCase):
    def test_the_payload_uses_the_shared_verdict(self):
        self.assertIs(a1_payload.verdict, a1_validity.verdict)
        self.assertIs(a1_payload.run_flags, a1_validity.run_flags)
        self.assertIs(a1_payload.lead_alone, a1_validity.lead_alone)

    def test_cap_hits_count_as_the_models_own_only_at_rigbys_tested_max(self):
        self.assertEqual(a1_payload.RIGBY_A1_TESTED_CTX, 65536, "GT 730 runbook: 65,536 loads, 98,304 does not")
        self.assertEqual(a1_payload.at_ceiling(2, 65536), 2)
        self.assertEqual(a1_payload.at_ceiling(2, 32768), 0, "the 32k orders were OUR limit (void, rerun)")

    def test_the_payload_uses_the_gpu_arms_early_stop(self):
        import a1_stop
        self.assertIs(a1_payload.early_stop, a1_stop.decide)
        # the GPU rule's own arithmetic, through rigby's handle: 0 of 3 done + 7 left < 10 - 2
        rule = {"baseline": "b", "margin": 2, "exclude": []}
        ex = ["e%d" % i for i in range(10)]
        rows = {e: {"passed": False} for e in ex[:3]}
        self.assertIsNotNone(a1_payload.early_stop(rows, ex, rule, base=10))
        self.assertIsNone(a1_payload.early_stop(rows, ex, rule, base=9))


if __name__ == "__main__":
    unittest.main()
