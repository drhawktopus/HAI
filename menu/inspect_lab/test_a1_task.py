"""A1-on-Inspect end to end with Inspect's mock model (no GPU): the harness's own loop and hidden-test grade run inside
an Inspect eval. Positive control: a scripted model writes the reference and finishes -> CORRECT. Negative control: a
model that never writes -> INCORRECT, graded (not 0/0). The score carries the harness row."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a1_task as AT  # noqa: E402
from inspect_ai import eval as inspect_eval  # noqa: E402
from inspect_ai.log import read_eval_log  # noqa: E402
from inspect_ai.model import ModelOutput, get_model  # noqa: E402

EX = "two-fer" if os.path.isdir(os.path.join(AT.EXERCISES, "two-fer")) else sorted(os.listdir(AT.EXERCISES))[0]


def run(outputs):
    model = get_model("mockllm/model", custom_outputs=outputs)
    log = inspect_eval(AT.a1(mode="agentic", exercises=EX), model=model, log_dir=tempfile.mkdtemp(), display="none")[0]
    return read_eval_log(log.location).samples[0], log


class A1OnInspect(unittest.TestCase):
    def test_writing_the_reference_scores_correct(self):
        ref = open(os.path.join(AT.EXERCISES, EX, ".meta", "example.py"), encoding="utf-8").read()
        s, log = run([ModelOutput.for_tool_call("mockllm/model", "write_file",
                                                {"path": EX.replace("-", "_") + ".py", "content": ref}),
                      ModelOutput.for_tool_call("mockllm/model", "finish", {"note": "done"})])
        self.assertEqual(log.status, "success")
        self.assertEqual(s.scores["a1_hidden_tests"].value, "C")
        row = s.metadata["a1_row"]
        self.assertTrue(row["passed"])
        self.assertGreater(row["tests_total"], 0)
        self.assertEqual(row["tool_counts"].get("write_file"), 1)

    def test_a_model_that_never_writes_scores_incorrect_and_is_graded(self):
        s, log = run([ModelOutput.for_tool_call("mockllm/model", "finish", {"note": "gave up"})])
        self.assertEqual(s.scores["a1_hidden_tests"].value, "I")
        self.assertGreater(s.metadata["a1_row"]["tests_total"], 0, "graded, never a 0/0")


class RigbyResumesFromItsCheckpoint(unittest.TestCase):
    """: rigby's A1 path checkpoints before every
    model call and a stopped exercise resumes by replaying it (a1_branch.ReplayChat "end"), exactly as the fleet's
    a1_order does - four deploy stops had cost rigby ~2.8 h of in-flight exercises because it did not."""

    def tearDown(self):
        AT.CHECKPOINT["path"] = None

    def test_a_checkpoint_is_written_and_a_resume_replays_it_then_goes_live(self):
        ref = open(os.path.join(AT.EXERCISES, EX, ".meta", "example.py"), encoding="utf-8").read()
        ck = os.path.join(tempfile.mkdtemp(), "checkpoints", EX + ".r1.partial.json")
        AT.CHECKPOINT["path"] = ck
        write = ModelOutput.for_tool_call("mockllm/model", "write_file",
                                          {"path": EX.replace("-", "_") + ".py", "content": ref})
        s, _ = run([write, ModelOutput.for_tool_call("mockllm/model", "finish", {"note": "done"})])
        self.assertTrue(s.metadata["a1_row"]["passed"])
        saved = json.load(open(ck, encoding="utf-8"))
        self.assertEqual(len(saved["turns"]), 1, "written before the 2nd call: the 1st turn and the conversation")
        self.assertTrue(any(m.get("role") == "tool" for m in saved["conversation"]))
        # resume: the saved write is REPLAYED (no model call), then one live call finishes - a stop cost one turn
        s2, log2 = run([ModelOutput.for_tool_call("mockllm/model", "finish", {"note": "done"})])
        self.assertEqual(log2.status, "success")
        self.assertEqual(s2.metadata.get("resumed_turns"), 1)
        self.assertTrue(s2.metadata["a1_row"]["passed"], "the replayed write is in the workspace that gets graded")


class ToolSchemaSeam(unittest.TestCase):
    """rigby's models got every A1 tool with NO parameters (the bridge read `properties` from a flat
    {name: description} map) and every call arrived `{}`. What reaches llama-server must equal what the GPU orders
    send - both tool sets, compared end to end through the provider."""

    def test_the_server_gets_the_gpu_orders_schema_for_both_tool_sets(self):
        import a1_ollama
        import lab_provider as LP
        H = AT.H
        for tools in (H._TOOLS, H._TOOLS_HAI):
            sent = LP.to_ollama_tools(AT.to_tool_infos(tools))
            gpu = a1_ollama.to_ollama_tools(tools)
            self.assertEqual(len(sent), len(gpu))
            for s, g in zip(sent, gpu):
                self.assertEqual(s["function"]["name"], g["function"]["name"])
                self.assertEqual(s["function"]["parameters"].get("properties"), g["function"]["parameters"]["properties"])
                self.assertEqual(sorted(s["function"]["parameters"].get("required") or []),
                                 sorted(g["function"]["parameters"].get("required") or []))
        writes = [t for t in AT.to_tool_infos(H._TOOLS_HAI) if t.name == "coding_create"]
        self.assertEqual(len(writes), 1)
        self.assertIn("path", writes[0].parameters.properties, "a tool that takes a path must declare it")


if __name__ == "__main__":
    unittest.main()
