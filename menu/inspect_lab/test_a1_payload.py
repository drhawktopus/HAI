"""rigby's A1 agentic payload end to end through real Inspect, the real harness and the real hidden-test grade; only
llama-server is replaced (a scripted `tolkien_llm.chat`). Positive control: writing the reference on the control
exercise passes 4/4. Negative: never writing is graded, not 0/0. Plus the stop slip, the context-cap count, the
order's sampling settings reaching the server, and the grader self-test the order runs before any model time."""
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["A1_DIR"] = os.path.join(os.path.dirname(HERE), "a1")
sys.path.insert(0, HERE)
import a1_payload as P  # noqa: E402
import lab_provider as LP  # noqa: E402

SPEC = {"name": "a1-test", "model": "m.gguf", "reasoning": "off", "a1_mode": "agentic"}
REF = open(os.path.join(P.CONTROL, ".meta", "example.py"), encoding="utf-8").read()


def reply(calls=(), content="", done="stop", prompt=11):
    return {"message": {"role": "assistant", "content": content, "thinking": "",
                        "tool_calls": [{"function": {"name": n, "arguments": a}} for n, a in calls]},
            "prompt_eval_count": prompt, "eval_count": 7, "done_reason": done}


def fake_server(script, seen):
    m = types.ModuleType("tolkien_llm")
    m.SERVER_PORT = 8091
    it = iter(script)

    def chat(messages, tools=None, options=None, stop_event=None, live=None):
        seen.append({"options": options, "stop_event": stop_event, "live": live, "n_tools": len(tools or []),
                     "first_user": next((m["content"] for m in messages if m.get("role") == "user"), ""),
                     "messages": [dict(m) for m in messages]})
        step = next(it)
        return step(stop_event) if callable(step) else step
    m.chat = chat
    return m


def run(script, stop_event=None, ceiling_s=None, task="control_add", ctx=32768, **spec):
    seen = []
    with mock.patch.object(LP, "_tolkien", return_value=fake_server(script, seen)), \
         mock.patch.object(LP, "_served_ctx", return_value=ctx):
        tdef = P.CONTROL_TASKS[task] if task in P.CONTROL_TASKS else P.TASKS[task]
        row = P.run_agentic(task, tdef, spec=dict(SPEC, **spec), ctx=ctx, temperature=0.6, seed=3,
                            stop_event=stop_event, live=None, ceiling_s=ceiling_s, log_dir=tempfile.mkdtemp())
    return row, seen


WRITE = reply([("write_file", {"path": "control_add.py", "content": REF})])
FINISH = reply([("finish", {"note": "done"})])


class AgenticPayload(unittest.TestCase):
    def test_writing_the_reference_passes_the_hidden_tests(self):
        row, seen = run([WRITE, FINISH])
        self.assertEqual((row["ok"], row["checks_passed"], row["checks_total"]), (True, 4, 4))
        self.assertEqual((row["finish_reason"], row["cap_hits"], row["error"]), ("stop", 0, None))
        self.assertTrue(os.path.exists(row["inspect_log"]))
        self.assertEqual(row["tool_counts"].get("write_file"), 1)
        self.assertEqual((row["prompt_tokens"], row["gen_tokens"]), (22, 14))
        self.assertEqual(row["task_set"], P.TASK_SET)

    def test_the_orders_sampling_settings_reach_the_server(self):
        _, seen = run([WRITE, FINISH])
        self.assertEqual(len(seen), 2)
        self.assertEqual({(s["options"].get("temperature"), s["options"].get("seed")) for s in seen}, {(0.6, 3)})
        self.assertNotIn("num_ctx", seen[0]["options"], "the context is the server's launch setting")
        self.assertGreater(seen[0]["n_tools"], 0)

    def test_never_writing_is_graded_not_zero_of_zero(self):
        row, _ = run([FINISH])
        self.assertFalse(row["ok"])
        self.assertEqual(row["checks_total"], 4)

    def test_a_stop_slip_cuts_the_item_and_no_further_call_is_made(self):
        stop = threading.Event()

        def cut(stop_event):
            stop.set()
            return reply(content="partial", done="stopped")
        row, seen = run([WRITE, cut, FINISH], stop_event=stop)
        self.assertEqual(row["finish_reason"], "stopped")
        self.assertFalse(row["ok"])
        self.assertEqual(len(seen), 2, "after the slip the provider refuses the next call")
        self.assertTrue(seen[0]["stop_event"] is not None)

    def test_a_turn_that_fills_the_context_is_counted(self):
        row, _ = run([reply(content="...", done="length"), WRITE, FINISH])
        self.assertEqual(row["cap_hits"], 1)
        self.assertEqual(row["finish_reason"], "length")

    def test_the_runaway_guard_cuts_at_the_turn_cap_and_the_item_carries_on(self):
        """`turn_cap` reaches llama-server as the per-call output cap; a reply cut there without a tool call
        is a runaway - the harness nudges, the model acts and passes, and the cut is no context hit (row stays valid)."""
        runaway = dict(reply(content="hmm " * 50, done="length"), eval_count=20480)
        row, seen = run([runaway, WRITE, FINISH], turn_cap=20480)
        self.assertEqual({s["options"].get("num_predict") for s in seen}, {20480}, "every call carries the cap")
        self.assertEqual((row["ok"], row["finish_reason"], row["cap_hits"]), (True, "stop", 0))
        self.assertEqual((row["runaway_cuts"], row["turn_cap"]), (1, 20480))
        self.assertIsNone(row.get("void"))

    def test_without_a_turn_cap_nothing_is_capped_and_a_length_cut_stays_a_cap_hit(self):
        row, seen = run([dict(reply(content="...", done="length"), eval_count=20480), WRITE, FINISH])
        self.assertNotIn("num_predict", seen[0]["options"])
        self.assertEqual((row["cap_hits"], row["runaway_cuts"]), (1, 0))

    SUMMARY = reply(content="<summary>The stub was replaced with the reference solution; tests not run yet.</summary>")

    @staticmethod
    def overflow(stop_event):
        raise RuntimeError('llama-server HTTP 400: {"error":{"code":400,"message":"request (65576 tokens) exceeds '
                           'the available context size (65536 tokens)","type":"exceed_context_size_error"}}')

    def test_a_conversation_past_the_threshold_is_compacted_and_the_agent_carries_on(self):
        """: Inspect's summary compaction.
        A call that reports a prompt past the threshold makes the NEXT call summarize first (a call with no tools),
        then continue on [system, brief, summary] - the brief is never lost."""
        row, seen = run([reply([("write_file", {"path": "control_add.py", "content": REF})], prompt=30000),
                         self.SUMMARY, FINISH], ctx=32768)          # threshold = 0.9 x 32,768 = 29,491
        self.assertEqual((row["ok"], row["ended"], row["error"]), (True, "finish", None))
        self.assertEqual([c["trigger"] for c in row["compactions"]], ["threshold"])
        self.assertEqual(seen[1]["n_tools"], 0, "the summary call")
        after = seen[2]["messages"]
        self.assertEqual(after[0]["role"], "system")
        self.assertIn("Brief:", after[1]["content"], "the brief is the prefix every compacted view keeps")
        self.assertIn("reference solution", after[-1]["content"] if isinstance(after[-1]["content"], str)
                      else str(after[-1]["content"]))
        self.assertLess(len(after), len(seen[0]["messages"]) + 2, "the history before the summary is not resent")
        self.assertIsNone(row["void"])

    def test_a_turn_cap_that_leaves_no_room_is_refused_before_any_call(self):
        with self.assertRaises(ValueError):
            run([WRITE, FINISH], ctx=32768, turn_cap=32768)

    def test_below_the_threshold_nothing_is_compacted(self):
        row, seen = run([WRITE, FINISH], ctx=32768)
        self.assertEqual(row["compactions"], [])
        self.assertEqual(len(seen), 2, "no summary call")

    def test_an_overflow_forces_a_compaction_and_the_call_is_retried(self):
        """Inspect's own overflow recovery: the server refuses the request -> compact unconditionally -> try again."""
        row, seen = run([WRITE, self.overflow, self.SUMMARY, FINISH], ctx=32768)
        self.assertEqual((row["ok"], row["ended"]), (True, "finish"))
        self.assertEqual([c["trigger"] for c in row["compactions"]], ["forced"])
        self.assertEqual((row["cap_hits"], row["void"]), (0, None), "recovered: no cap hit")

    def test_when_even_the_compacted_conversation_does_not_fit_it_ends_context_full(self):
        """The backstop: compaction could not make it fit. At rigby's tested max that is scored (10-09 rule); below
        it, the context was our limit (void)."""
        row, _ = run([WRITE, self.overflow, self.SUMMARY, self.overflow], ctx=P.RIGBY_A1_TESTED_CTX)
        self.assertEqual((row["ended"], row["ok"], row["checks_passed"]), ("context_full", True, 4))
        self.assertEqual((row["cap_hits"], row["length_at_ceiling"], row["error"]), (1, 1, None))
        self.assertIsNone(row["void"])
        row, _ = run([WRITE, self.overflow, self.SUMMARY, self.overflow], ctx=32768)
        self.assertEqual(row["ended"], "context_full")
        self.assertIsNotNone(row["void"], "below the tested max the context was our limit")

    def test_hooks_are_cleared_after_the_item(self):
        run([WRITE, FINISH], stop_event=threading.Event())
        self.assertEqual(LP.TOLKIEN_HOOKS, {"stop_event": None, "live": None})

    def test_reasoning_reaches_the_provider_and_a_context_mismatch_is_refused(self):
        seen = []
        with mock.patch.object(LP, "_tolkien", return_value=fake_server([WRITE, FINISH], seen)), \
             mock.patch.object(LP, "_served_ctx", return_value=8192):
            row = P.run_agentic("control_add", P.CONTROL_TASKS["control_add"], spec=dict(SPEC), ctx=32768,
                                temperature=0.6, seed=1, log_dir=tempfile.mkdtemp())
        self.assertEqual(seen, [], "no model call against a server serving a different context")
        self.assertFalse(row["ok"])
        self.assertIn("n_ctx", row["error"])

    def test_it_runs_from_a_worker_thread_as_the_rigby_lane_does(self):
        out = {}

        def lane():
            try:
                out["row"] = run([WRITE, FINISH])[0]
            except BaseException as e:                                      # noqa: BLE001
                out["error"] = repr(e)
        t = threading.Thread(target=lane, name="rigby-lane")
        t.start()
        t.join()
        self.assertNotIn("error", out)
        self.assertTrue(out["row"]["ok"])

    def test_each_load_runs_the_harness_now_on_disk_not_a_cached_one(self):
        """In its own process: re-importing the provider re-registers Inspect's "lab" model API, which would leave
        this suite's patched module object behind the registry (the module-identity trap)."""
        import subprocess
        code = ("import importlib.util, sys, types\n"
                "stale = types.ModuleType('a1_harness'); sys.modules['a1_harness'] = stale\n"
                "spec = importlib.util.spec_from_file_location('llm_payload', sys.argv[1])\n"
                "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
                "print('STALE' if mod.H is stale else 'FRESH', mod.H.__file__)\n")
        r = subprocess.run([sys.executable, "-c", code, P.__file__], capture_output=True, text=True,
                           env=dict(os.environ), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        verdict, path = r.stdout.split(None, 1)
        self.assertEqual(verdict, "FRESH", "a later order must not run an earlier order's harness")
        self.assertEqual(os.path.abspath(path.strip()), os.path.abspath(P.H.__file__))

    def test_a_second_order_in_the_same_process_gets_its_own_server_and_stop_slip(self):
        """The rigby lane runs order after order in one process. Order 2 (same model, same settings) must reach ITS
        server with ITS stop event - not a provider instance Inspect memoized for order 1."""
        import subprocess
        code = r'''
import importlib.util, os, sys, tempfile, threading, types
from unittest import mock
REF = open(os.path.join(os.path.dirname(sys.argv[1]), "control", "control-add", ".meta", "example.py")).read()
def reply(calls):
    return {"message": {"content": "", "thinking": "", "tool_calls": [{"function": {"name": n, "arguments": a}}
            for n, a in calls]}, "prompt_eval_count": 1, "eval_count": 1, "done_reason": "stop"}
def order(tag):
    spec = importlib.util.spec_from_file_location("llm_payload", sys.argv[1])
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    seen, stop = [], threading.Event()
    it = iter([reply([("write_file", {"path": "control_add.py", "content": REF})]), reply([("finish", {})])])
    srv = types.SimpleNamespace(SERVER_PORT=8091,
                                chat=lambda m, t=None, o=None, stop_event=None, live=None: seen.append(stop_event) or next(it))
    LP = importlib.import_module("lab_provider")     # the module run_agentic will import for this order
    with mock.patch.object(LP, "_tolkien", return_value=srv), mock.patch.object(LP, "_served_ctx", return_value=4096):
        row = mod.run_agentic("control_add", mod.CONTROL_TASKS["control_add"],
                              spec={"model": "m.gguf", "reasoning": "off", "a1_mode": "agentic"}, ctx=4096,
                              temperature=0.0, seed=1, stop_event=stop, log_dir=tempfile.mkdtemp())
    ok = len(seen) == 2 and all(s is not None and s.events and s.events[0] is stop for s in seen)
    print(tag, "calls", len(seen), "own-stop", ok, "passed", row["ok"])
order("ORDER1"); order("ORDER2")
'''
        r = subprocess.run([sys.executable, "-c", code, P.__file__], capture_output=True, text=True,
                           env=dict(os.environ), timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        lines = r.stdout.strip().splitlines()[-2:]
        self.assertEqual(lines, ["ORDER1 calls 2 own-stop True passed True", "ORDER2 calls 2 own-stop True passed True"],
                         r.stdout[-800:])

    def test_the_grader_self_test_is_sound_on_every_exercise(self):
        self.assertEqual(len(P.TASKS), 34)
        self.assertEqual(P.self_test(), "")


if __name__ == "__main__":
    unittest.main()
