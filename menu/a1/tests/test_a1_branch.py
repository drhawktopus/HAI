"""A1 branching (a1_branch): an arm starts from a saved parent run up to its own difference. Driven through the REAL
harness with a scripted model, so the workspace really is rebuilt by re-executing the parent's tool calls."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import a1_branch as B  # noqa: E402
import a1_harness  # noqa: E402
from test_a1_harness import make_exercise  # noqa: E402


def last_sha(messages):
    for m in reversed(messages):
        if m["role"] == "tool" and str(m["content"]).startswith("sha256: "):
            return m["content"].split()[1]
    return ""


SOLVE = [lambda m: {"name": "coding_read", "arguments": {"path": "adder.py"}},
         lambda m: {"name": "coding_patch", "arguments": {"path": "adder.py", "old": "    pass",
                                                          "new": "    return a + b", "expected_sha256": last_sha(m)}},
         lambda m: {"name": "coding_test", "arguments": {}},
         lambda m: {"name": "finish", "arguments": {}}]


def scripted(steps, log=None):
    it = iter(steps)
    box = {"messages": None, "calls": 0}

    def chat(messages, tools):
        box["messages"] = messages
        box["calls"] += 1
        if log is not None:
            log.append("live")
        return {"content": "", "tool_calls": [next(it)(messages)]}
    return chat, box


class Branch(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="a1_branch_")
        self.ex = make_exercise(self.base)
        self.addCleanup(shutil.rmtree, self.base, True)
        chat, box = scripted(SOLVE)
        r = a1_harness.run_task(self.ex, chat, mode="agentic-hai", finish_gate=False)
        self.assertTrue(r["passed"], r)
        self.parent = {"conversation": box["messages"], "turns": [{"wall_s": 30.0, "eval_count": 900}] * 4}

    def test_review_branches_at_the_finish_and_only_the_review_and_after_run_live(self):
        live_log, reviews = [], []
        live, _ = scripted([lambda m: {"name": "finish", "arguments": {}}], log=live_log)
        replay = B.ReplayChat(self.parent, "finish", live)
        r = a1_harness.run_task(self.ex, replay, mode="agentic-hai",
                                review_fn=lambda pkg: reviews.append(pkg) or "Add a docstring.")
        self.assertEqual(replay.rec["replayed_turns"], 4, "read, patch, test and the accepted finish are replayed")
        self.assertEqual(len(reviews), 1, "the reviewer acts on the replayed finish")
        self.assertEqual(live_log, ["live"], "the live model is asked only for the repair round")
        self.assertTrue(replay.valid(), replay.rec)
        self.assertTrue(r["passed"])
        self.assertEqual(replay.rec["prefix_wall_s"], 120.0)
        self.assertEqual(replay.rec["prefix_eval_tokens"], 3600)

    def test_first_test_branch_lets_the_injected_fault_land_on_the_replayed_test(self):
        live, _ = scripted([lambda m: {"name": "coding_test", "arguments": {}},
                            lambda m: {"name": "finish", "arguments": {}}])
        replay = B.ReplayChat(self.parent, "first_test", live)
        r = a1_harness.run_task(self.ex, replay, mode="agentic-hai", behaviour="recover")
        brec = r.get("behaviour") or {}
        self.assertEqual(replay.rec["replayed_turns"], 3)
        self.assertEqual(brec.get("recover_action"), "retry", brec)
        self.assertTrue(replay.valid(), replay.rec)

    def test_a_parent_whose_edit_result_differs_is_an_invalid_branch(self):
        tampered = {"conversation": [dict(m) for m in self.parent["conversation"]], "turns": self.parent["turns"]}
        for m in tampered["conversation"]:
            if m.get("role") == "tool" and str(m.get("content")).startswith("OK patched"):
                m["content"] = "OK patched adder.py (1 replacement(s)); new sha256: " + "0" * 64
        live, _ = scripted([lambda m: {"name": "finish", "arguments": {}}])
        replay = B.ReplayChat(tampered, "finish", live)
        a1_harness.run_task(self.ex, replay, mode="agentic-hai", review_fn=lambda pkg: "Add a docstring.")
        self.assertFalse(replay.valid())
        self.assertEqual(len(replay.rec["mismatches"]), 1)

    def test_a_parent_that_never_reached_the_point_is_replayed_whole(self):
        chat, box = scripted(SOLVE[:2] + [lambda m: {"name": "finish", "arguments": {}}])
        a1_harness.run_task(self.ex, chat, mode="agentic-hai")
        no_test = {"conversation": box["messages"], "turns": []}
        live_log = []
        live, _ = scripted([], log=live_log)
        replay = B.ReplayChat(no_test, "first_test", live)
        a1_harness.run_task(self.ex, replay, mode="agentic-hai", behaviour="correct")
        self.assertFalse(replay.rec["point_reached"])
        self.assertEqual(live_log, [], "the arm's event never fires on that history: no live call")

    def test_a_stopped_exercise_resumes_from_its_checkpoint_without_regenerating(self):
        snap = {}

        def stopped(messages, tools):                    # read + patch answered live, then the stop lands
            snap["conversation"] = [dict(m) for m in messages]
            if len([m for m in messages if m["role"] == "assistant"]) == 2:
                raise KeyboardInterrupt("stop slip")
            return {"content": "", "tool_calls": [SOLVE[len([m for m in messages if m["role"] == "assistant"])](messages)]}
        with self.assertRaises(KeyboardInterrupt):
            a1_harness.run_task(self.ex, stopped, mode="agentic-hai")
        ck = {"conversation": snap["conversation"], "turns": [{"wall_s": 30.0, "eval_count": 900}] * 2}
        live_log = []
        live, _ = scripted(SOLVE[2:], log=live_log)
        resumed = B.ReplayChat(ck, "end", live)
        r = a1_harness.run_task(self.ex, resumed, mode="agentic-hai")
        self.assertEqual(resumed.rec["replayed_turns"], 2, "read and patch come from the checkpoint")
        self.assertEqual(live_log, ["live", "live"], "only test and finish are generated")
        self.assertTrue(resumed.valid(), resumed.rec)
        self.assertTrue(r["passed"], r)
        self.assertEqual(resumed.rec["prefix_wall_s"], 60.0)

    def test_an_incompatible_parent_is_refused(self):
        plan = {"model": "qwen3.8:27b", "num_ctx": 122880, "think": "xhigh", "mode": "agentic-hai"}
        good = dict(plan, harness_sha256="abc")
        self.assertEqual(B.compatible(good, plan, "abc"), "")
        self.assertIn("think", B.compatible(dict(good, think=True), plan, "abc"))
        self.assertIn("harness", B.compatible(good, plan, "def"))
        self.assertIn("turn_cap", B.compatible(good, dict(plan, turn_cap=20480), "abc"))
        self.assertEqual(B.compatible(dict(good, turn_cap=None), dict(plan, turn_cap=0), "abc"), "")
        with self.assertRaises(ValueError):
            B.branch_index([], "middle")

    def test_a_guard_cut_replays_as_the_same_cut(self):
        """A checkpoint holding a runaway-guard cut resumes into the same harness path (one derivation: the replay reads
        the harness's own RUNAWAY_TEXT)."""
        import a1_harness as H
        conv = [{"role": "system", "content": "s"}, {"role": "user", "content": "brief"},
                {"role": "assistant", "content": ""},
                {"role": "user", "content": H.RUNAWAY_TEXT.format(cap=20480)},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "finish", "arguments": {}}}]}]
        turns = B.assistant_turns(conv)
        self.assertEqual([t[2] for t in turns], [20480, None])
        chat = B.ReplayChat({"conversation": conv}, "end", lambda m, t: self.fail("nothing goes live"))
        self.assertEqual(chat(conv[:2], []), {"content": "", "tool_calls": [], "runaway": 20480})


if __name__ == "__main__":
    unittest.main()
